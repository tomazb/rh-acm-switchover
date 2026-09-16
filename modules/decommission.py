"""
Decommission module for old primary hub.
"""

# Runbook: Step 14 (decommission) and Rollback references where applicable

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, NamedTuple, Optional, Sequence

from kubernetes.client.exceptions import ApiException
from urllib3.exceptions import HTTPError, MaxRetryError, NewConnectionError
from urllib3.exceptions import TimeoutError as Urllib3TimeoutError

from lib.constants import (
    ACM_NAMESPACE,
    DECOMMISSION_POD_INTERVAL,
    DECOMMISSION_POD_TIMEOUT,
    DELETE_REQUEST_TIMEOUT,
    GATE_REASON_ACK_NOT_APPLICABLE,
    GATE_REASON_DESTINATION_ABSENT,
    GATE_REASON_DESTINATION_UNVERIFIABLE,
    GATE_REASON_SOURCE_AMBIGUOUS,
    GATE_REASON_SOURCE_UNVERIFIABLE,
    HIVE_CLUSTERDEPLOYMENT_API_GROUP,
    HIVE_CLUSTERDEPLOYMENT_API_VERSION,
    HIVE_CLUSTERDEPLOYMENT_PLURAL,
    LOCAL_CLUSTER_NAME,
    MANAGED_CLUSTER_API_GROUP,
    MANAGED_CLUSTER_API_VERSION,
    MANAGED_CLUSTER_DELETE_INTERVAL,
    MANAGED_CLUSTER_DELETE_TIMEOUT,
    MANAGED_CLUSTER_PLURAL,
    OBSERVABILITY_NAMESPACE,
    OBSERVABILITY_POD_LABEL_SELECTOR,
    OBSERVABILITY_TERMINATE_INTERVAL,
    OBSERVABILITY_TERMINATE_TIMEOUT,
    POD_CLASSIFICATION_IDENTITY_INCONSISTENT,
)
from lib.decommission_outcome import (
    UNSUCCESSFUL_OUTCOMES,
    DecommissionResult,
    ObservabilityGateDecision,
    ObservabilityGateResult,
    SubstepExecution,
    SubstepOutcome,
)
from lib.exceptions import SwitchoverError, TargetDisappeared
from lib.gitops_detector import safe_record_gitops_markers
from lib.kube_client import KubeClient
from lib.run_record import RunRecord
from lib.strict_read import StrictReadOutcome, StrictReadStatus
from lib.teardown_record import (
    IDENTITY_BEARING_KINDS,
    AbsenceProof,
    TeardownPhase,
    TeardownRecord,
    split_resource_key,
    teardown_key,
    teardown_kind,
)
from lib.utils import confirm_action
from lib.validation import InputValidator, ValidationError
from lib.waiter import WaitConditionResult, format_public_list, wait_for_condition

from .decommission_identity import (
    ClassificationPass,
    OperatorIdentity,
    capture_operator_identity,
    classify_pods,
)

logger = logging.getLogger("acm_switchover")

#: One pass over a strict Pod inventory against the recorded operator identity.
DrainClassifier = Callable[[KubeClient, Sequence[Mapping[str, Any]], OperatorIdentity], ClassificationPass]


@dataclass(frozen=True)
class TeardownSpec:
    """Everything the shared phase machine needs about one resource family.

    PRs D and E supply their own specs to the same ``_teardown_resource``; that is
    what keeps one algorithm rather than three that drift.

    Exactly three drain shapes are valid, and every other combination is rejected
    at construction:

    * no drain -- ``drain_namespace``, ``drain_label_selector`` and ``classifier``
      all ``None`` (ManagedCluster);
    * selector drain -- a namespace and a non-empty selector, no classifier (MCO);
    * classified drain -- a namespace, no selector, and a pass-level ``classifier``
      deciding which of ALL the namespace's Pods block. Only an identity-bearing
      family (MultiClusterHub) may classify, because classification needs the
      recorded operator identity.

    A selector-drain spec that loses its selector therefore fails here rather than
    silently widening into an all-Pod scan.
    """

    group: str
    version: str
    plural: str
    resource_name: str
    kind: str
    namespace: Optional[str]
    name: str
    drain_namespace: Optional[str]
    drain_label_selector: Optional[str]
    classifier: Optional[DrainClassifier] = None
    # None means "use the module OBSERVABILITY_TERMINATE_* constants at call time"
    # so existing MCO tests that monkeypatch those names keep working. ManagedCluster
    # and MultiClusterHub supply explicit values.
    cr_absent_timeout: Optional[int] = None
    cr_absent_interval: Optional[int] = None
    drain_timeout: Optional[int] = None
    drain_interval: Optional[int] = None

    def __post_init__(self) -> None:
        drain_ns = self.drain_namespace
        drain_sel = self.drain_label_selector
        if drain_ns is None:
            if drain_sel is not None or self.classifier is not None:
                raise ValueError(f"{self.kind} teardown has no drain namespace, so it takes no selector or classifier")
            return
        if not drain_ns.strip():
            raise ValueError("drain_namespace must be non-empty when drain is enabled")
        if self.classifier is None:
            if drain_sel is None or not drain_sel.strip():
                raise ValueError(f"{self.kind} teardown needs a non-empty drain_label_selector or a classifier")
            return
        if drain_sel is not None:
            raise ValueError(f"{self.kind} teardown takes a drain_label_selector or a classifier, not both")
        if self.kind not in IDENTITY_BEARING_KINDS:
            raise ValueError(f"{self.kind} teardown carries no operator identity, so it cannot classify Pods")

    @property
    def api_version(self) -> str:
        return f"{self.group}/{self.version}"

    @property
    def has_drain(self) -> bool:
        return self.drain_namespace is not None

    def require_drain(self) -> tuple[str, Optional[str]]:
        """Return ``(drain_namespace, drain_label_selector)`` when drain is enabled.

        Narrows the Optional namespace for type checkers. Call only on drain-scoped
        specs (or inside ``if spec.has_drain``); no-drain families must not enter
        drain I/O. A ``None`` selector is deliberate and means "all Pods": it exists
        only on a classified drain, whose classifier decides what blocks.
        """
        if self.drain_namespace is None:
            raise ValueError(f"{self.kind} teardown has no drain scope")
        return self.drain_namespace, self.drain_label_selector

    def absence_wait_timeout(self) -> int:
        return self.cr_absent_timeout if self.cr_absent_timeout is not None else OBSERVABILITY_TERMINATE_TIMEOUT

    def absence_wait_interval(self) -> int:
        return self.cr_absent_interval if self.cr_absent_interval is not None else OBSERVABILITY_TERMINATE_INTERVAL

    def drain_wait_timeout(self) -> int:
        return self.drain_timeout if self.drain_timeout is not None else OBSERVABILITY_TERMINATE_TIMEOUT

    def drain_wait_interval(self) -> int:
        return self.drain_interval if self.drain_interval is not None else OBSERVABILITY_TERMINATE_INTERVAL


_MCH_GROUP = "operator.open-cluster-management.io"
_MCH_VERSION = "v1"
_MCH_PLURAL = "multiclusterhubs"
_MCH_KIND = "MultiClusterHub"


def _multiclusterhub_teardown_spec(name: str) -> TeardownSpec:
    """The classified-drain TeardownSpec for one MultiClusterHub name.

    Built per invocation, so the ``DECOMMISSION_POD_*`` bounds are read at call time.
    The CR-absence wait and the drain wait each use them: the design names no
    separate MCH CR-absence constant, so both sequential waits share this bound.
    """
    return TeardownSpec(
        group=_MCH_GROUP,
        version=_MCH_VERSION,
        plural=_MCH_PLURAL,
        resource_name=_MCH_PLURAL,
        kind=_MCH_KIND,
        namespace=ACM_NAMESPACE,
        name=name,
        drain_namespace=ACM_NAMESPACE,
        drain_label_selector=None,
        classifier=classify_pods,
        cr_absent_timeout=DECOMMISSION_POD_TIMEOUT,
        cr_absent_interval=DECOMMISSION_POD_INTERVAL,
        drain_timeout=DECOMMISSION_POD_TIMEOUT,
        drain_interval=DECOMMISSION_POD_INTERVAL,
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


_IDENTITY_INCONSISTENT_MESSAGE = (
    "The recorded MultiClusterHub operator Deployment is absent, replaced, or unreadable; recovery is required"
)


class _DrainPass(NamedTuple):
    """What one drain pass proved, before any caller decides what to write."""

    blocking: int
    identity_inconsistent: bool
    operator_deployment_revision: Optional[str]


OBSERVABILITY_TEARDOWN = TeardownSpec(
    group="observability.open-cluster-management.io",
    version="v1beta2",
    plural="multiclusterobservabilities",
    resource_name="multiclusterobservabilities",
    kind="MultiClusterObservability",
    namespace=None,
    name="observability",
    drain_namespace=OBSERVABILITY_NAMESPACE,
    drain_label_selector=OBSERVABILITY_POD_LABEL_SELECTOR,
)


class Decommission:
    """Handles decommissioning of old primary hub."""

    #: The decommission substeps, in execution order. Each name appears exactly
    #: once here and is the key used in prompts, dispatch, and the result.
    _SUBSTEPS = ("observability", "managed_clusters", "multiclusterhub")

    _PROMPTS = {
        "observability": "\nDelete MultiClusterObservability resource?",
        "managed_clusters": "\nDelete ManagedCluster resources (excluding local-cluster)?",
        "multiclusterhub": "\nDelete MultiClusterHub resource? (This will remove all ACM components)",
    }

    def __init__(
        self,
        primary_client: KubeClient,
        has_observability: bool,
        *,
        run_record: RunRecord,
        dry_run: bool = False,
        secondary_client: Optional[KubeClient] = None,
        acknowledge_observability_not_migrated: bool = False,
    ) -> None:
        self.primary = primary_client
        self.has_observability = has_observability
        # The destination hub, present only for an integrated switchover. Standalone
        # decommission has no destination, so the July section 4 gate is not called at
        # all rather than called and defaulted.
        self.secondary = secondary_client
        self.acknowledge_observability_not_migrated = acknowledge_observability_not_migrated
        # Keyword-only and required on purpose: a default would let a caller
        # silently opt out of the durable channel. This task opens the channel;
        # it writes no teardown record through it yet.
        self.run_record = run_record
        self.dry_run = dry_run

    def decommission(self, interactive: bool = True) -> DecommissionResult:
        """Run the requested teardown substeps.

        A refusal aborts the remaining substeps and yields an unsuccessful
        result; it is never persisted (it ends the run, and the summary is
        output rather than state).

        This aggregator deliberately handles nothing. An expected operational
        failure arrives as SubstepExecution(FAILED, changed=...) on the one
        execution-result channel, so the mutation a failing substep actually
        performed is aggregated before the early return. A programming error
        propagates instead of being laundered into a handled outcome
        (IV-R403-01).
        """
        self._log_decommission_banner()

        if interactive and not confirm_action(
            "\nAre you sure you want to proceed with decommissioning the old hub?",
            default=False,
        ):
            logger.info("Decommission cancelled by user")
            return DecommissionResult(
                substeps={},
                not_attempted=self._requested_substeps(),
                cancelled=True,
                changed=False,
                would_change=False,
            )

        if self.dry_run:
            # Read-only prediction. It records no outcome, no teardown record and
            # no phase, calls no delete primitive, and no later live run consumes
            # the answer.
            requested = self._requested_substeps()
            predicted = [self._preview_substep(substep) for substep in requested]
            logger.info("[DRY-RUN] Decommission preview complete (no changes made)")
            return DecommissionResult(
                substeps={},
                not_attempted=requested,
                changed=False,
                would_change=any(predicted),
            )

        outcomes: dict[str, SubstepOutcome] = {}
        changed = False
        for index, substep in enumerate(self._SUBSTEPS):
            if not self._substep_requested(substep):
                outcomes[substep] = SubstepOutcome.NOT_REQUESTED
                continue

            if interactive and not confirm_action(self._PROMPTS[substep], default=False):
                logger.error("Refused by operator: %s. Aborting decommission.", substep)
                outcomes[substep] = SubstepOutcome.REFUSED
                return DecommissionResult(
                    substeps=outcomes,
                    not_attempted=self._remaining_after(index),
                    changed=changed,
                )

            # The ONE execution-result channel.
            execution = self._run_substep(substep)

            outcomes[substep] = execution.outcome
            # Monotonic: once true in this invocation it stays true, on every
            # exit path including the early returns below.
            changed = changed or execution.changed

            if execution.outcome in UNSUCCESSFUL_OUTCOMES:
                return DecommissionResult(
                    substeps=outcomes,
                    not_attempted=self._remaining_after(index),
                    changed=changed,
                )

        logger.info("Decommission completed")
        return DecommissionResult(substeps=outcomes, not_attempted=(), changed=changed)

    @staticmethod
    def _log_decommission_banner() -> None:
        """Warn loudly before anything destructive is considered."""
        logger.warning("=" * 60)
        logger.warning("DECOMMISSION MODE - This will remove ACM from the old hub!")
        logger.warning("=" * 60)

    def _substep_requested(self, substep: str) -> bool:
        """Whether configuration or a durable record asks for this substep at all.

        A persisted teardown record is a request in its own right. Standalone
        ``--decommission`` re-detects ``has_observability`` live on every run, and the
        DELETE this machine issues is exactly what makes that detection answer false,
        so a resume of an interrupted teardown would otherwise skip the drain and the
        final absence proof it still owes. The collection's ``_acm_mco_requested``
        carries the same clause (``roles/decommission/tasks/delete_observability.yml``).
        A completed record counts too: it is re-proved read-only rather than trusted,
        which is what the collection does for any mapping record.
        """
        if substep == "observability":
            return self.has_observability or self._has_teardown_record(OBSERVABILITY_TEARDOWN)
        return True

    @staticmethod
    def _teardown_key(spec: TeardownSpec) -> str:
        """The canonical record key for ``spec``'s fixed identity, derived once.

        The request question and the phase machine both go through this, so they
        cannot disagree about which record they mean.
        """
        return teardown_key(spec.api_version, spec.kind, spec.namespace, spec.name)

    def _has_teardown_record(self, spec: TeardownSpec) -> bool:
        """Whether a durable teardown record already exists for ``spec``.

        Read-only: nothing here writes, defaults or repairs a record.
        """
        return self.run_record.teardown_record(self._teardown_key(spec)) is not None

    def _requested_substeps(self) -> tuple[str, ...]:
        return tuple(substep for substep in self._SUBSTEPS if self._substep_requested(substep))

    def _remaining_after(self, index: int) -> tuple[str, ...]:
        """The requested substeps after ``index``, which an abort leaves unattempted."""
        return tuple(substep for substep in self._SUBSTEPS[index + 1 :] if self._substep_requested(substep))

    def _run_substep(self, substep: str) -> SubstepExecution:
        """Dispatch one substep to its family method and return its execution unchanged.

        No exception handling of its own: each family method converts its own
        expected operational failures into a FAILED execution, at the point where
        it knows whether a delete was accepted.
        """
        dispatch = {
            "observability": self.teardown_observability,
            "managed_clusters": self.teardown_managed_clusters,
            "multiclusterhub": self.teardown_multiclusterhub,
        }
        return dispatch[substep]()

    def _preview_substep(self, substep: str) -> bool:
        """Fresh read-only prediction: would this substep delete anything?

        Reads only. It issues no delete, waits for nothing and writes no
        RunRecord, so a dry run leaves behind no result or state authority.
        """
        if substep == "observability":
            # Strict and named, exactly as the live teardown reads it: through the
            # non-strict list an API failure answered `None`, which became an empty
            # name list and a confident "nothing to delete". A preview that cannot
            # read must refuse, not predict.
            spec = OBSERVABILITY_TEARDOWN
            cr = self.primary.get_custom_resource_strict(
                group=spec.group,
                version=spec.version,
                plural=spec.plural,
                name=spec.name,
                namespace=spec.namespace,
            )
            if cr.status is StrictReadStatus.ERROR:
                raise SwitchoverError(f"Cannot verify {spec.kind} {spec.name} for the dry-run preview")
            if cr.status is StrictReadStatus.ITEMS:
                logger.info("[DRY-RUN] Would delete %s %s", spec.kind, spec.name)
                return True
            return False

        if substep == "managed_clusters":
            return self._preview_managed_clusters()

        if substep == "multiclusterhub":
            # The live teardown's own resolver, then its own guarded-target checks. It
            # predicts whether a DELETE is still owed, not whether the drain can finish,
            # so Pods are not classified here.
            name = self._resolve_multiclusterhub_target()
            if name is None or not self._preview_target_would_change(_multiclusterhub_teardown_spec(name)):
                return False
            logger.info("[DRY-RUN] Would delete MultiClusterHub %s", name)
            return True

        raise KeyError(substep)

    def teardown_multiclusterhub(self) -> SubstepExecution:
        """Tear down the MultiClusterHub through the shared phase machine.

        Resolves the one target strictly, then hands it to ``_teardown_resource``,
        which captures the operator identity for a new teardown before its first
        durable write and reuses the recorded identity on every resume.
        """
        logger.info("Deleting MultiClusterHub resource...")
        try:
            name = self._resolve_multiclusterhub_target()
        except ValidationError:
            # A caller or programming defect, never an operational outcome.
            raise
        except SwitchoverError as exc:
            logger.error("MultiClusterHub teardown failed: %s", exc)
            return SubstepExecution(SubstepOutcome.FAILED, changed=False)
        if name is None:
            logger.info("No MultiClusterHub: nothing to tear down")
            return SubstepExecution(SubstepOutcome.PRECONDITION_NOOP, changed=False)
        logger.info(
            "Tearing down or re-verifying MultiClusterHub %s; each wait is bounded by %ss",
            name,
            DECOMMISSION_POD_TIMEOUT,
        )
        return self._teardown_resource(_multiclusterhub_teardown_spec(name), record_gitops_markers=False)

    def _resolve_multiclusterhub_target(self) -> Optional[str]:
        """The one MultiClusterHub this teardown owns, or ``None`` when there is none.

        Shared by the live teardown and the dry-run preview so the two cannot disagree.
        A durable record binds the name for good: discovery never replaces it, and a
        different live MultiClusterHub is left intact rather than adopted. Without a
        record, strict discovery must find zero (a clean skip) or exactly one; more
        than one is ambiguous and nothing is chosen by name.
        """
        mch_api_version = f"{_MCH_GROUP}/{_MCH_VERSION}"
        recorded = []
        for key in self.run_record.all_teardown_records():
            parts = split_resource_key(key)
            if parts is not None and parts[:3] == (mch_api_version, _MCH_KIND, ACM_NAMESPACE):
                recorded.append(parts[3])
        if len(recorded) > 1:
            raise SwitchoverError("More than one MultiClusterHub teardown is recorded; refusing to choose one")
        if recorded and not self._is_valid_multiclusterhub_name(recorded[0]):
            raise SwitchoverError("The recorded MultiClusterHub name is invalid; refusing to resume its teardown")

        inventory = self.primary.list_custom_resources_strict(
            group=_MCH_GROUP,
            version=_MCH_VERSION,
            plural=_MCH_PLURAL,
            namespace=ACM_NAMESPACE,
        )
        if inventory.status is StrictReadStatus.CRD_ABSENT:
            live_names: list[str] = []
        elif inventory.status is StrictReadStatus.ITEMS:
            live_names = []
            for item in inventory.items:
                metadata = item.get("metadata") if isinstance(item, dict) else None
                live_name = metadata.get("name") if isinstance(metadata, dict) else None
                # A malformed member is a failed proof and never reaches a named read.
                if not self._is_valid_multiclusterhub_name(live_name):
                    raise SwitchoverError("Cannot verify MultiClusterHub inventory")
                live_names.append(live_name)
        else:
            raise SwitchoverError("Cannot verify MultiClusterHub inventory")

        if recorded:
            if any(live_name != recorded[0] for live_name in live_names):
                raise SwitchoverError(
                    f"A MultiClusterHub other than the recorded {recorded[0]} exists; "
                    "it was left intact and not adopted"
                )
            return recorded[0]
        if len(live_names) > 1:
            raise SwitchoverError("More than one MultiClusterHub exists; refusing to choose one")
        return live_names[0] if live_names else None

    @staticmethod
    def _is_valid_multiclusterhub_name(name: Any) -> bool:
        """Whether ``name`` may reach a named read without raising ``ValidationError``."""
        if not isinstance(name, str):
            return False
        try:
            InputValidator.validate_kubernetes_name(name, _MCH_KIND)
        except ValidationError:
            return False
        return True

    def teardown_observability(self, *, record_gitops_markers: bool = False) -> SubstepExecution:
        """Tear down MultiClusterObservability through the shared phase machine.

        The one MCO algorithm. ``Finalization`` reaches it through this same method
        with ``record_gitops_markers=True``; it owns no MCO deletion logic of its own
        (GLM-H6).
        """
        return self._teardown_resource(OBSERVABILITY_TEARDOWN, record_gitops_markers=record_gitops_markers)

    def destination_observability_gate(self) -> ObservabilityGateResult:
        """July section 4: may source observability be deleted at all right now?

        Takes no arguments and reads nothing from state. Every input is a fresh
        live read on both hubs, because the preflight ``primary_has_observability``
        boolean and any earlier gate answer describe a cluster as it was, and this
        decision authorizes a deletion happening now. The result is returned, never
        persisted and never cached, so a resume re-proves it (section 13).

        Called only for the MCO spec, only when a destination client exists, and only
        when the phase machine's own fresh read found the target present -- that is,
        only when this invocation has a DELETE to authorize.
        """
        if self.secondary is None:
            # Refused before any read: without a destination hub there is no fact to
            # gate on, and continuing would fail on None inside the destination step
            # after the source reads had already been issued.
            raise SwitchoverError(
                "destination_observability_gate requires a secondary client; "
                "standalone decommission has no destination"
            )

        spec = OBSERVABILITY_TEARDOWN
        source_cr = self.primary.get_custom_resource_strict(
            group=spec.group,
            version=spec.version,
            plural=spec.plural,
            name=spec.name,
            namespace=spec.namespace,
        )
        drain_ns, _drain_sel = spec.require_drain()
        source_namespace = self.primary.get_namespace_strict(drain_ns)

        if StrictReadStatus.ERROR in (source_cr.status, source_namespace.status):
            # An unverifiable source is never read as "nothing to delete".
            return self._blocked(GATE_REASON_SOURCE_UNVERIFIABLE, "the source hub's observability state")

        if source_cr.proves_absence and source_namespace.status is StrictReadStatus.NAMESPACE_ABSENT:
            logger.info(
                "No %s and no %s namespace on the source hub: the destination gate does not apply",
                spec.kind,
                drain_ns,
            )
            return ObservabilityGateResult(decision=ObservabilityGateDecision.NOT_APPLICABLE)

        if not (source_cr.status is StrictReadStatus.ITEMS and source_namespace.status is StrictReadStatus.ITEMS):
            # Half-removed: an absent CRD with a live namespace, or the reverse.
            return self._blocked(GATE_REASON_SOURCE_AMBIGUOUS, "the source hub's observability state")

        destination_cr = self.secondary.list_custom_resources_strict(
            group=spec.group,
            version=spec.version,
            plural=spec.plural,
        )
        destination_namespace = self.secondary.get_namespace_strict(drain_ns)

        if StrictReadStatus.ERROR in (
            destination_cr.status,
            destination_namespace.status,
        ):
            return self._blocked(
                GATE_REASON_DESTINATION_UNVERIFIABLE,
                "the destination hub could not be read",
            )

        namespace_present = destination_namespace.status is StrictReadStatus.ITEMS
        namespace_absent = destination_namespace.status is StrictReadStatus.NAMESPACE_ABSENT
        # A complete inventory with no items is a positive absence proof; the source's
        # clean-skip rule is deliberately NOT reused, so nothing here treats a missing
        # CRD or namespace as harmless.
        cr_present = destination_cr.status is StrictReadStatus.ITEMS and bool(destination_cr.items)
        cr_absent = destination_cr.proves_absence or (
            destination_cr.status is StrictReadStatus.ITEMS and not destination_cr.items
        )

        if cr_present and namespace_present:
            if self.acknowledge_observability_not_migrated:
                return self._blocked(
                    GATE_REASON_ACK_NOT_APPLICABLE,
                    "the destination hub already has observability, so there is nothing to acknowledge",
                )
            return ObservabilityGateResult(decision=ObservabilityGateDecision.PROCEED)

        if cr_absent and namespace_absent:
            if self.acknowledge_observability_not_migrated:
                logger.warning(
                    "Destination observability is proven absent and the operator acknowledged it: "
                    "metrics continuity ends with this deletion"
                )
                return ObservabilityGateResult(decision=ObservabilityGateDecision.PROCEED)
            return self._blocked(
                GATE_REASON_DESTINATION_ABSENT,
                "the destination hub has no observability, so metrics continuity ends here",
            )

        # Readable but mixed: this proves neither coherent presence nor complete
        # absence, so there is no fact to acknowledge. Reported as unverifiable
        # because nothing about the destination has been established -- the message
        # says inconsistent state, never a transport failure.
        return self._blocked(
            GATE_REASON_DESTINATION_UNVERIFIABLE,
            "the destination hub's observability is in an inconsistent, partially present state",
        )

    @staticmethod
    def _blocked(reason: str, detail: str) -> ObservabilityGateResult:
        """One blocked result. The code is the contract; the detail is for the log."""
        logger.error(
            "Destination observability gate blocked the teardown (%s): %s",
            reason,
            detail,
        )
        return ObservabilityGateResult(decision=ObservabilityGateDecision.BLOCKED, reason=reason)

    def _teardown_resource(  # noqa: C901 - one linear phase table; splitting it hides the order
        self, spec: TeardownSpec, *, record_gitops_markers: bool
    ) -> SubstepExecution:
        """The July section 1 phase machine, once, for any resource family.

        Owns the conversion of expected operational failures into a FAILED execution,
        because this is the single place that knows whether the API accepted the
        UID-preconditioned DELETE **for this invocation**. That local flag is what
        ``changed`` is derived from: it is set the moment the DELETE is accepted and
        never cleared, so a resumed record whose delete landed earlier contributes
        ``changed=False`` even when this invocation writes ``completed``.

        A ``ValidationError`` from the delete primitive is deliberately NOT caught: it
        means the caller failed to supply a proved identity, which is a bug here, and
        converting it into a FAILED result would hide a defect behind an
        operational-looking outcome. The primitive's own dry-run refusal is a
        ``FatalError`` and therefore a ``SwitchoverError``, so the general arm below
        would report it as FAILED rather than let it propagate. Nothing relies on that:
        this machine never calls the primitive in a dry run, because the dry-run branch
        returns before the DELETE.
        """
        key = self._teardown_key(spec)
        record = self.run_record.teardown_record(key)
        # A recorded operator identity is reused verbatim for the record's lifetime:
        # never rediscovered, never upgraded, never rebound.
        identity = self._recorded_identity(record)
        changed = False

        try:
            cr = self.primary.get_custom_resource_strict(
                group=spec.group,
                version=spec.version,
                plural=spec.plural,
                name=spec.name,
                namespace=spec.namespace,
            )
            if cr.status is StrictReadStatus.ERROR:
                raise SwitchoverError(f"Cannot verify {spec.kind} {spec.name}: inventory unreadable")

            if record is None:
                noop = self._precondition_noop(spec, cr)
                if noop is not None:
                    return noop

            if record is not None and record.phase is TeardownPhase.COMPLETED:
                return self._reprove_completed(spec, cr, record, identity)

            # July section 4, at the ruled position: after the clean-skip check and
            # after the completed dispatch (a completed record has no DELETE to
            # authorize), and before `expected_uid` -- therefore before any durable
            # write and before the DELETE, with no mutation in between. Selected by
            # the spec object, so PRs D and E reuse this machine ungated.
            #
            # `cr.status is ITEMS` is the whole rule: section 9 requires the gate to
            # re-run its fresh reads before the deletion substep, and this invocation
            # has a deletion substep only when its own fresh read found the target. A
            # record whose target is already gone owes a drain and a final proof, and
            # gating that would block the resume on a source the gate itself reads as
            # half-removed.
            if spec is OBSERVABILITY_TEARDOWN and self.secondary is not None and cr.status is StrictReadStatus.ITEMS:
                gate = self.destination_observability_gate()
                if gate.decision is ObservabilityGateDecision.BLOCKED:
                    # No write has happened yet, in a live run or a preview, so
                    # `changed` is necessarily false. A predicted blocker is a real
                    # preview result, which is why dry run takes this path too.
                    return SubstepExecution(SubstepOutcome.FAILED, changed=False)
                if gate.decision is ObservabilityGateDecision.NOT_APPLICABLE and record is None:
                    # Reachable only because the object disappeared between this
                    # machine's read and the gate's own: the machine saw ITEMS or the
                    # gate would not have run. With a record the same race falls
                    # through to the UID-preconditioned DELETE, whose TargetDisappeared
                    # arm hands the absence proof to the poll.
                    return SubstepExecution(SubstepOutcome.PRECONDITION_NOOP, changed=False)

            expected_uid = record.expected_uid if record is not None else self._live_uid(spec, cr)

            if cr.status is StrictReadStatus.ITEMS:
                live_uid = (cr.resource or {}).get("metadata", {}).get("uid")
                if live_uid != expected_uid:
                    raise SwitchoverError(
                        f"{spec.kind} {spec.name} is not the object recorded for teardown; " "it was left intact"
                    )

                if record_gitops_markers:
                    markers = safe_record_gitops_markers(
                        logger=logger,
                        context="primary",
                        namespace=spec.namespace or "",
                        kind=spec.kind,
                        name=spec.name,
                        metadata=(cr.resource or {}).get("metadata", {}),
                    )
                    if markers:
                        logger.warning(
                            "%s %s appears GitOps-managed (%s). Coordinate deletion to avoid drift.",
                            spec.kind,
                            spec.name,
                            ", ".join(markers),
                        )

                if self.dry_run:
                    logger.info("[DRY-RUN] Would delete %s %s", spec.kind, spec.name)
                    return SubstepExecution(SubstepOutcome.COMPLETED, changed=False)

                if record is None and spec.kind in IDENTITY_BEARING_KINDS:
                    # Plan 11C.2: after the live UID is proved, so the identity is bound
                    # to exactly this object, and before the first durable write, so it
                    # is durable before the DELETE. A fatal capture read raises here,
                    # with nothing written and nothing deleted.
                    identity = capture_operator_identity(
                        self.primary,
                        mch_teardown_key=key,
                        mch_expected_uid=expected_uid,
                        captured_at=_utc_now(),
                    )
                self._record(spec, key, expected_uid, TeardownPhase.DELETE_STARTED, identity=identity)
                try:
                    self.primary.delete_custom_resource_preconditioned(
                        spec.group,
                        spec.version,
                        spec.plural,
                        spec.name,
                        uid=expected_uid,
                        namespace=spec.namespace,
                        timeout_seconds=DELETE_REQUEST_TIMEOUT,
                    )
                except TargetDisappeared:
                    # The object went away between the proved read and the DELETE.
                    # July step 3's absence poll is "GET until 404/absent", so this is
                    # where the proof obligation starts, not a failure: fall through and
                    # let the poll, the drain and the final pass decide. Nothing was
                    # accepted for this invocation, so `changed` stays False.
                    logger.info(
                        "%s %s disappeared between the proved read and the delete; verifying absence live",
                        spec.kind,
                        spec.name,
                    )
                except ApiException as exc:
                    # Convert at the raise site so the failure travels the one
                    # execution-result channel and this invocation's aggregated
                    # `changed` reaches the caller. Status and reason only: the raw
                    # HTTP body and headers must never reach a log or the state file.
                    raise SwitchoverError(
                        f"Failed to delete {spec.kind} {spec.name}: API error {exc.status} {exc.reason}"
                    ) from exc
                except (
                    HTTPError,
                    MaxRetryError,
                    NewConnectionError,
                    Urllib3TimeoutError,
                ) as exc:
                    # The transport failures lib/kube_client.py classifies. Their str()
                    # can carry the request URL, so name only the exception type.
                    raise SwitchoverError(
                        f"Failed to delete {spec.kind} {spec.name}: transport error {type(exc).__name__}"
                    ) from exc
                else:
                    changed = True
            elif self.dry_run:
                return SubstepExecution(SubstepOutcome.COMPLETED, changed=False)

            def cr_removed() -> WaitConditionResult:
                observed = self.primary.get_custom_resource_strict(
                    group=spec.group,
                    version=spec.version,
                    plural=spec.plural,
                    name=spec.name,
                    namespace=spec.namespace,
                )
                if observed.status in (
                    StrictReadStatus.OBJECT_ABSENT,
                    StrictReadStatus.CRD_ABSENT,
                ):
                    return WaitConditionResult.complete("resource absent")
                if observed.status is StrictReadStatus.ITEMS:
                    if self._live_uid(spec, observed) != expected_uid:
                        raise SwitchoverError(f"{spec.kind} {spec.name} was replaced; it was left intact")
                    return WaitConditionResult.pending("resource still present")
                raise SwitchoverError(f"Cannot verify {spec.kind} {spec.name} absence")

            if not wait_for_condition(
                f"{spec.kind} removal",
                cr_removed,
                timeout=spec.absence_wait_timeout(),
                interval=spec.absence_wait_interval(),
                allow_success_after_timeout=True,
                logger=logger,
            ):
                raise SwitchoverError(f"Timeout waiting for {spec.kind} {spec.name} removal")
            self._record(spec, key, expected_uid, TeardownPhase.CR_ABSENT, identity=identity)

            if spec.has_drain:
                self._record(spec, key, expected_uid, TeardownPhase.DRAIN_PENDING, identity=identity)
                drain_ns, drain_sel = spec.require_drain()

                def pods_removed() -> WaitConditionResult:
                    namespace = self.primary.get_namespace_strict(drain_ns)
                    if namespace.status is StrictReadStatus.NAMESPACE_ABSENT:
                        return WaitConditionResult.complete("namespace absent")
                    if namespace.status is not StrictReadStatus.ITEMS:
                        self._record(spec, key, expected_uid, TeardownPhase.RECOVERY_REQUIRED, identity=identity)
                        raise SwitchoverError(f"The {drain_ns} namespace state is ambiguous")
                    pods = self.primary.list_pods_strict(drain_ns, label_selector=drain_sel)
                    if pods.status is not StrictReadStatus.ITEMS:
                        raise SwitchoverError(f"Cannot verify the {drain_ns} drain")
                    drain = self._classify_drain(spec, pods, identity)
                    if drain.identity_inconsistent:
                        self._record(spec, key, expected_uid, TeardownPhase.RECOVERY_REQUIRED, identity=identity)
                        raise SwitchoverError(_IDENTITY_INCONSISTENT_MESSAGE)
                    if drain.blocking:
                        return WaitConditionResult.pending(f"{drain.blocking} pod(s) still running")
                    return WaitConditionResult.complete("no pods remaining")

                if not wait_for_condition(
                    f"{spec.kind} pod termination",
                    pods_removed,
                    timeout=spec.drain_wait_timeout(),
                    interval=spec.drain_wait_interval(),
                    allow_success_after_timeout=True,
                    logger=logger,
                ):
                    raise SwitchoverError(f"Timeout: pods still running in {drain_ns}")
                self._record(spec, key, expected_uid, TeardownPhase.DRAINED, identity=identity)

            # Final verification pass. Every field of the completion evidence comes
            # from THESE reads and nothing earlier: evidence copied from a pre-DELETE
            # read or a previous invocation would certify what this run never proved.
            final_cr = self.primary.get_custom_resource_strict(
                group=spec.group,
                version=spec.version,
                plural=spec.plural,
                name=spec.name,
                namespace=spec.namespace,
            )
            if final_cr.status is StrictReadStatus.ITEMS:
                raise SwitchoverError(f"{spec.kind} {spec.name} is still present after its delete")
            if final_cr.status is StrictReadStatus.ERROR:
                raise SwitchoverError(f"Cannot re-prove {spec.kind} {spec.name} absence")

            resource_versions: dict = {}
            absence_proofs = {
                "target_cr": AbsenceProof(
                    proof_type=("crd_absent" if final_cr.status is StrictReadStatus.CRD_ABSENT else "object_absent"),
                    resource_key=key,
                )
            }

            if spec.has_drain:
                drain_ns, drain_sel = spec.require_drain()
                namespace_read = self.primary.get_namespace_strict(drain_ns)
                if namespace_read.status is StrictReadStatus.NAMESPACE_ABSENT:
                    # July section 3 fixed-namespace scope rule: a positively absent
                    # namespace is verified-empty.
                    absence_proofs["drain_namespace"] = AbsenceProof(
                        proof_type="namespace_absent",
                        resource_key=f"v1/Namespace//{drain_ns}",
                    )
                elif namespace_read.status is StrictReadStatus.ITEMS:
                    pods = self.primary.list_pods_strict(drain_ns, label_selector=drain_sel)
                    if pods.status is not StrictReadStatus.ITEMS:
                        raise SwitchoverError(f"Cannot verify the {drain_ns} drain")
                    drain = self._classify_drain(spec, pods, identity)
                    if drain.identity_inconsistent:
                        self._record(spec, key, expected_uid, TeardownPhase.RECOVERY_REQUIRED, identity=identity)
                        raise SwitchoverError(_IDENTITY_INCONSISTENT_MESSAGE)
                    if drain.blocking:
                        raise SwitchoverError(f"{drain.blocking} pod(s) still running in {drain_ns}")
                    resource_versions["drain_namespace"] = namespace_read.resource_version
                    resource_versions["drain_pods"] = pods.resource_version
                    if drain.operator_deployment_revision is not None:
                        resource_versions["operator_deployment"] = drain.operator_deployment_revision
                else:
                    self._record(spec, key, expected_uid, TeardownPhase.RECOVERY_REQUIRED, identity=identity)
                    raise SwitchoverError(f"The {drain_ns} namespace state is ambiguous")

            self._record(
                spec,
                key,
                expected_uid,
                TeardownPhase.COMPLETED,
                identity=identity,
                observed_at=_utc_now(),
                resource_versions=resource_versions,
                absence_proofs=absence_proofs,
            )

            return SubstepExecution(SubstepOutcome.COMPLETED, changed=changed)

        except ValidationError:
            # Not an operational outcome: the delete primitive raises it when the
            # caller supplied no proved identity, which is a bug in this method, and
            # converting it into a FAILED result would hide that behind an
            # operational-looking outcome.
            raise
        except SwitchoverError as exc:
            # Every expected operational failure, PreconditionConflict included:
            # sanitized stage and reason only, never a raw body, header, token or
            # client configuration.
            logger.error("%s teardown failed: %s", spec.kind, exc)
            return SubstepExecution(SubstepOutcome.FAILED, changed=changed)

    def _reprove_completed(
        self, spec: TeardownSpec, cr, record: TeardownRecord, identity: Optional[OperatorIdentity]
    ) -> SubstepExecution:
        """Revalidate a completed record without rewriting its immutable evidence.

        A failed reproof fails the run and writes nothing: ``completed`` is never
        rewritten or moved back to ``recovery_required``.
        """
        if cr.status is StrictReadStatus.ITEMS:
            if self._live_uid(spec, cr) != record.expected_uid:
                raise SwitchoverError(f"{spec.kind} {spec.name} was replaced; it was left intact")
            raise SwitchoverError(f"{spec.kind} {spec.name} is still present after its completed teardown")

        if not spec.has_drain:
            return SubstepExecution(SubstepOutcome.COMPLETED, changed=False)

        drain_ns, drain_sel = spec.require_drain()
        namespace = self.primary.get_namespace_strict(drain_ns)
        if namespace.status is StrictReadStatus.NAMESPACE_ABSENT:
            return SubstepExecution(SubstepOutcome.COMPLETED, changed=False)
        if namespace.status is not StrictReadStatus.ITEMS:
            raise SwitchoverError(f"The {drain_ns} namespace state is ambiguous")

        pods = self.primary.list_pods_strict(drain_ns, label_selector=drain_sel)
        if pods.status is not StrictReadStatus.ITEMS:
            raise SwitchoverError(f"Cannot verify the {drain_ns} drain")
        drain = self._classify_drain(spec, pods, identity)
        if drain.identity_inconsistent:
            raise SwitchoverError(_IDENTITY_INCONSISTENT_MESSAGE)
        if drain.blocking:
            raise SwitchoverError(f"{drain.blocking} pod(s) still running in {drain_ns}")
        return SubstepExecution(SubstepOutcome.COMPLETED, changed=False)

    def _classify_drain(
        self, spec: TeardownSpec, pods: StrictReadOutcome, identity: Optional[OperatorIdentity]
    ) -> _DrainPass:
        """One drain pass over a strict ``ITEMS`` Pod inventory. Writes nothing.

        A selector drain counts every listed Pod. A classified drain runs the spec's
        classifier against the recorded identity; callers must test
        ``identity_inconsistent`` BEFORE ``blocking``, because zero Pods leave nothing
        blocking even when the recorded operator Deployment is gone or replaced. A
        captured identity whose pass surfaced no Deployment revision is inconsistent
        too: there is no fresh proof that the recorded Deployment still exists.
        """
        if spec.classifier is None:
            return _DrainPass(blocking=len(pods.items), identity_inconsistent=False, operator_deployment_revision=None)
        if identity is None:
            raise ValueError(f"{spec.kind} drain classification requires the recorded operator identity")
        result = spec.classifier(self.primary, pods.items, identity)
        revision = result.operator_deployment_resource_version
        inconsistent = result.identity_status == POD_CLASSIFICATION_IDENTITY_INCONSISTENT or (
            identity.available and revision is None
        )
        return _DrainPass(
            blocking=len(result.blocking),
            identity_inconsistent=inconsistent,
            operator_deployment_revision=None if inconsistent else revision,
        )

    def _precondition_noop(self, spec: TeardownSpec, cr) -> Optional[SubstepExecution]:
        """A clean skip, available only when there is no record at all.

        For MultiClusterObservability, a CRD that is positively absent while the drain
        namespace is still present is NOT a clean skip: something is half-removed, and
        reporting a no-op would hide it. No-drain families skip on positive CR
        absence alone — they have no namespace half-removed predicate. Neither does
        MultiClusterHub: its drain namespace is the ACM namespace, which outlives the
        hub by design, and with no record no teardown started, so there is no identity
        or drain obligation to prove. That skip reads no namespace.
        """
        if cr.status is StrictReadStatus.ITEMS and cr.resource is not None:
            return None
        if not spec.has_drain or spec.kind == _MCH_KIND:
            if cr.status in (
                StrictReadStatus.OBJECT_ABSENT,
                StrictReadStatus.CRD_ABSENT,
            ):
                logger.info("No %s: nothing to tear down", spec.kind)
                return SubstepExecution(SubstepOutcome.PRECONDITION_NOOP, changed=False)
            raise SwitchoverError(f"Cannot verify {spec.kind} {spec.name}")
        drain_ns, _drain_sel = spec.require_drain()
        namespace_read = self.primary.get_namespace_strict(drain_ns)
        if namespace_read.status is StrictReadStatus.NAMESPACE_ABSENT:
            logger.info(
                "No %s and no %s namespace: nothing to tear down",
                spec.kind,
                drain_ns,
            )
            return SubstepExecution(SubstepOutcome.PRECONDITION_NOOP, changed=False)
        if namespace_read.status is StrictReadStatus.ITEMS:
            raise SwitchoverError(f"{spec.kind} is absent but the {drain_ns} namespace is still present")
        raise SwitchoverError(f"Cannot verify the {drain_ns} namespace")

    def _live_uid(self, spec: TeardownSpec, cr) -> str:
        uid = (cr.resource or {}).get("metadata", {}).get("uid") if cr.resource else None
        if not isinstance(uid, str) or not uid.strip():
            raise SwitchoverError(f"Cannot establish the identity of {spec.kind} {spec.name}")
        return uid

    def _record(
        self,
        spec: TeardownSpec,
        key: str,
        expected_uid: str,
        phase: TeardownPhase,
        *,
        identity: Optional[OperatorIdentity],
        observed_at: Optional[str] = None,
        resource_versions: Optional[dict] = None,
        absence_proofs: Optional[dict] = None,
    ) -> None:
        """The one durable teardown writer, for every phase including ``completed``.

        ``identity`` is required and explicit. An identity-bearing record must carry
        its captured outcome at every phase, and any other family must carry none; a
        call site that gets this wrong is a bug, so it raises ``ValueError`` before a
        ``TeardownRecord`` exists. Leaving it to the validator would surface
        ``MalformedTeardownRecord``, a ``SwitchoverError``, as an ordinary FAILED.
        Dry run never reaches a write.
        """
        if (identity is not None) != (teardown_kind(key) in IDENTITY_BEARING_KINDS):
            raise ValueError(f"{spec.kind} teardown write carries the wrong operator identity shape")
        if self.dry_run:
            return
        self.run_record.record_teardown_phase(
            TeardownRecord(
                key=key,
                expected_uid=expected_uid,
                phase=phase,
                observed_at=observed_at,
                resource_versions=resource_versions,
                absence_proofs=absence_proofs,
                operator_deployment=identity.operator_deployment if identity is not None else None,
                operator_identity_unavailable=(
                    identity.operator_identity_unavailable if identity is not None else None
                ),
            )
        )

    @staticmethod
    def _recorded_identity(record: Optional[TeardownRecord]) -> Optional[OperatorIdentity]:
        """The identity a durable record already carries, or ``None`` for a family with none."""
        if record is None or (record.operator_deployment is None and record.operator_identity_unavailable is None):
            return None
        return OperatorIdentity(
            operator_deployment=record.operator_deployment,
            operator_identity_unavailable=record.operator_identity_unavailable,
        )

    def _managed_cluster_teardown_spec(self, name: str) -> TeardownSpec:
        """No-drain TeardownSpec for one ManagedCluster name."""
        return TeardownSpec(
            group=MANAGED_CLUSTER_API_GROUP,
            version=MANAGED_CLUSTER_API_VERSION,
            plural=MANAGED_CLUSTER_PLURAL,
            resource_name=MANAGED_CLUSTER_PLURAL,
            kind="ManagedCluster",
            namespace=None,
            name=name,
            drain_namespace=None,
            drain_label_selector=None,
            cr_absent_timeout=MANAGED_CLUSTER_DELETE_TIMEOUT,
            cr_absent_interval=MANAGED_CLUSTER_DELETE_INTERVAL,
        )

    def _managed_cluster_record_names(self) -> set[str]:
        """Durable ManagedCluster teardown-record names, excluding local-cluster."""
        names: set[str] = set()
        for key in self.run_record.all_teardown_records():
            parts = key.rsplit("/", 3)
            if len(parts) != 4:
                continue
            api_version, kind, _namespace, name = parts
            if kind != "ManagedCluster":
                continue
            if api_version != f"{MANAGED_CLUSTER_API_GROUP}/{MANAGED_CLUSTER_API_VERSION}":
                continue
            if name and name != LOCAL_CLUSTER_NAME:
                names.add(name)
        return names

    def _managed_cluster_live_names_from_inventory(self, inventory: StrictReadOutcome) -> list[str]:
        """Non-local ManagedCluster names from a strict ITEMS inventory.

        Every returned item must be a mapping with mapping ``metadata`` and a
        non-empty string ``metadata.name``. Any malformed item makes the whole
        inventory unverifiable — nothing is dropped silently.
        """
        live_names: list[str] = []
        for mc in inventory.items or []:
            if not isinstance(mc, dict):
                raise SwitchoverError("Cannot verify ManagedCluster inventory")
            metadata = mc.get("metadata")
            if not isinstance(metadata, dict):
                raise SwitchoverError("Cannot verify ManagedCluster inventory")
            mc_name = metadata.get("name")
            if not isinstance(mc_name, str) or not mc_name.strip():
                raise SwitchoverError("Cannot verify ManagedCluster inventory")
            if mc_name == LOCAL_CLUSTER_NAME:
                logger.info("Skipping local-cluster")
                continue
            live_names.append(mc_name)
        return live_names

    def _resolve_managed_cluster_work_set(self, *, preview: bool = False) -> tuple[list[str], list[str]]:
        """Resolve live names and the effective work set, or raise SwitchoverError.

        Shared by live teardown and dry-run preview so inventory / durable-record
        union / CRD_ABSENT rules cannot drift.
        """
        inventory_error = (
            "Cannot verify ManagedCluster inventory for the dry-run preview"
            if preview
            else "Cannot verify ManagedCluster inventory"
        )
        inventory = self.primary.list_managed_clusters_strict()
        if inventory.status is StrictReadStatus.ERROR:
            raise SwitchoverError(inventory_error)
        if inventory.status is StrictReadStatus.CRD_ABSENT:
            live_names: list[str] = []
        elif inventory.status is StrictReadStatus.ITEMS:
            live_names = self._managed_cluster_live_names_from_inventory(inventory)
        else:
            raise SwitchoverError(inventory_error)

        work_set = sorted(set(live_names) | self._managed_cluster_record_names())
        if inventory.status is StrictReadStatus.CRD_ABSENT and not work_set:
            raise SwitchoverError(inventory_error)
        return live_names, work_set

    def _preview_target_would_change(self, spec: TeardownSpec) -> bool:
        """Read-only: would this named target still require a DELETE?

        Shared by the ManagedCluster and MultiClusterHub previews. For a new
        identity-bearing target it also runs the read-only identity capture, so a fatal
        identity read fails the prediction exactly as it would fail the live run before
        its DELETE. A determinate unavailable identity still predicts the DELETE.
        Nothing is persisted.
        """
        key = self._teardown_key(spec)
        record = self.run_record.teardown_record(key)
        cr = self.primary.get_custom_resource_strict(
            group=spec.group,
            version=spec.version,
            plural=spec.plural,
            name=spec.name,
            namespace=spec.namespace,
        )
        if cr.status is StrictReadStatus.ERROR:
            raise SwitchoverError(f"Cannot verify {spec.kind} {spec.name} for the dry-run preview")
        if cr.status is StrictReadStatus.ITEMS:
            live_uid = self._live_uid(spec, cr)
            if record is not None and live_uid != record.expected_uid:
                raise SwitchoverError(
                    f"{spec.kind} {spec.name} is not the object recorded for teardown; " "it was left intact"
                )
            if record is not None and record.phase is TeardownPhase.COMPLETED:
                raise SwitchoverError(f"{spec.kind} {spec.name} is still present after its completed teardown")
            if record is None and spec.kind in IDENTITY_BEARING_KINDS:
                capture_operator_identity(
                    self.primary,
                    mch_teardown_key=key,
                    mch_expected_uid=live_uid,
                    captured_at=_utc_now(),
                )
            return True
        if cr.status in (
            StrictReadStatus.OBJECT_ABSENT,
            StrictReadStatus.CRD_ABSENT,
        ):
            return False
        raise SwitchoverError(f"Cannot verify {spec.kind} {spec.name} for the dry-run preview")

    def _preview_managed_clusters(self) -> bool:
        """Dry-run ManagedCluster prediction using the live pre-mutation safety path."""
        live_names, work_set = self._resolve_managed_cluster_work_set(preview=True)
        if not work_set:
            return False
        if live_names:
            self._verify_managed_cluster_delete_safety(live_names)
        would_change_names = [
            name for name in work_set if self._preview_target_would_change(self._managed_cluster_teardown_spec(name))
        ]
        if would_change_names:
            logger.info(
                "[DRY-RUN] Would delete %s ManagedCluster(s): %s",
                len(would_change_names),
                format_public_list(would_change_names),
            )
        return bool(would_change_names)

    def teardown_managed_clusters(self) -> SubstepExecution:
        """Tear down ManagedClusters through the shared no-drain phase machine.

        Strict inventory, Hive preserveOnDelete safety, per-name UID-guarded
        teardown, and survivor aggregation on the one execution-result channel.
        """
        logger.info("Deleting ManagedCluster resources...")
        changed = False

        try:
            live_names, work_set = self._resolve_managed_cluster_work_set(preview=False)

            if not work_set:
                logger.info("No ManagedClusters found")
                return SubstepExecution(SubstepOutcome.PRECONDITION_NOOP, changed=False)

            # Hive safety runs before any per-cluster mutation when live targets remain.
            if live_names:
                self._verify_managed_cluster_delete_safety(live_names)
        except SwitchoverError as exc:
            # Global inventory / Hive failures abort before mutation. Per-target
            # expected failures stay on the returned channel from `_teardown_resource`;
            # ValidationError and other programmer errors must not be caught here.
            logger.error("ManagedCluster teardown failed: %s", exc)
            return SubstepExecution(SubstepOutcome.FAILED, changed=changed)

        survivors: list[str] = []
        for mc_name in work_set:
            logger.info("Deleting ManagedCluster: %s", mc_name)
            execution = self._teardown_resource(
                self._managed_cluster_teardown_spec(mc_name),
                record_gitops_markers=False,
            )
            changed = changed or execution.changed
            if execution.outcome is SubstepOutcome.FAILED:
                survivors.append(mc_name)
            elif execution.outcome not in (
                SubstepOutcome.COMPLETED,
                SubstepOutcome.PRECONDITION_NOOP,
            ):
                survivors.append(mc_name)

        if survivors:
            logger.error(
                "ManagedCluster teardown incomplete; survivors: %s",
                format_public_list(survivors),
            )
            return SubstepExecution(SubstepOutcome.FAILED, changed=changed)

        return SubstepExecution(SubstepOutcome.COMPLETED, changed=changed)

    def _verify_managed_cluster_delete_safety(self, managed_cluster_names: list[str]) -> None:
        """Verify matching Hive ClusterDeployments are safe before deleting ManagedClusters."""
        outcome = self.primary.list_custom_resources_strict(
            group=HIVE_CLUSTERDEPLOYMENT_API_GROUP,
            version=HIVE_CLUSTERDEPLOYMENT_API_VERSION,
            plural=HIVE_CLUSTERDEPLOYMENT_PLURAL,
        )
        if outcome.status is StrictReadStatus.ERROR:
            reason = outcome.reason or "unreadable"
            raise SwitchoverError(
                "Unable to verify ClusterDeployment preserveOnDelete safety before deleting ManagedClusters: "
                f"{reason}"
            )
        if outcome.status is StrictReadStatus.CRD_ABSENT:
            raise SwitchoverError(
                "Unable to verify ClusterDeployment preserveOnDelete safety before deleting ManagedClusters: "
                "Hive ClusterDeployment API is not served"
            )
        if outcome.status is not StrictReadStatus.ITEMS:
            raise SwitchoverError(
                "Unable to verify ClusterDeployment preserveOnDelete safety before deleting ManagedClusters: "
                "inventory unreadable"
            )
        cluster_deployments = outcome.items or []

        managed_cluster_name_set = set(managed_cluster_names)
        unsafe_matches = set()
        unverified_relationships = set()
        for cluster_deployment in cluster_deployments:
            matching_cluster_name, unverified_reason = self._cluster_deployment_relationship(
                cluster_deployment,
                managed_cluster_name_set,
            )
            if unverified_reason:
                metadata = cluster_deployment.get("metadata") or {}
                namespace = metadata.get("namespace", "unknown")
                name = metadata.get("name", "unknown")
                unverified_relationships.add(f"{namespace}/{name}: {unverified_reason}")
                continue
            if not matching_cluster_name:
                continue

            metadata = cluster_deployment.get("metadata") or {}
            spec = cluster_deployment.get("spec") or {}
            preserve_on_delete = spec.get("preserveOnDelete", False)
            if not preserve_on_delete:
                namespace = metadata.get("namespace", "unknown")
                name = metadata.get("name", "unknown")
                unsafe_matches.add(f"{matching_cluster_name} ({namespace}/{name})")

        if unverified_relationships:
            raise SwitchoverError(
                "Cannot verify ManagedCluster relationship for Hive ClusterDeployments before deleting "
                "ManagedClusters: "
                f"{', '.join(sorted(unverified_relationships))}. "
                "Review the ClusterDeployment ownership and set explicit clusterName metadata before decommission."
            )

        if unsafe_matches:
            raise SwitchoverError(
                "Cannot delete ManagedClusters because matching Hive ClusterDeployments "
                "do not have spec.preserveOnDelete=true: "
                f"{', '.join(sorted(unsafe_matches))}. Set preserveOnDelete=true before decommission."
            )

        logger.info(
            "Verified ClusterDeployment preserveOnDelete safety for ManagedCluster(s): %s",
            format_public_list(managed_cluster_names),
        )

    @staticmethod
    def _cluster_deployment_relationship(
        cluster_deployment: dict,
        managed_cluster_names: set[str],
    ) -> tuple[str | None, str | None]:
        """Classify a ClusterDeployment relationship to a ManagedCluster.

        Returns (matching_cluster_name, unverified_reason). A non-empty unverified_reason
        means the resource has a plausible relationship to a delete target but cannot be
        classified safely enough to proceed.
        """
        metadata = cluster_deployment.get("metadata") or {}
        spec = cluster_deployment.get("spec") or {}
        cluster_metadata = spec.get("clusterMetadata") or {}
        if not isinstance(cluster_metadata, dict):
            cluster_metadata = {}
        cluster_install_ref = spec.get("clusterInstallRef") or {}
        if not isinstance(cluster_install_ref, dict):
            cluster_install_ref = {}

        confirmed_candidates = []
        for source, candidate in (
            ("metadata.name", metadata.get("name")),
            ("spec.clusterName", spec.get("clusterName")),
            ("spec.clusterMetadata.clusterName", cluster_metadata.get("clusterName")),
        ):
            if candidate in managed_cluster_names:
                confirmed_candidates.append((source, candidate))

        namespace = metadata.get("namespace")
        install_ref_name = cluster_install_ref.get("name")
        if namespace in managed_cluster_names and install_ref_name == namespace:
            confirmed_candidates.append(("metadata.namespace/spec.clusterInstallRef.name", namespace))

        confirmed_names = sorted({candidate for _, candidate in confirmed_candidates})
        if len(confirmed_names) == 1:
            confirmed_name = confirmed_names[0]
            conflicting_plausible = []
            if namespace in managed_cluster_names and namespace != confirmed_name:
                conflicting_plausible.append(f"metadata.namespace={namespace}")
            if install_ref_name in managed_cluster_names and install_ref_name != confirmed_name:
                conflicting_plausible.append(f"spec.clusterInstallRef.name={install_ref_name}")
            if conflicting_plausible:
                sources = ", ".join(f"{source}={candidate}" for source, candidate in confirmed_candidates)
                return (
                    None,
                    "conflicting ManagedCluster identifiers " f"({sources}; {', '.join(conflicting_plausible)})",
                )
            return confirmed_names[0], None
        if len(confirmed_names) > 1:
            sources = ", ".join(f"{source}={candidate}" for source, candidate in confirmed_candidates)
            return None, f"conflicting ManagedCluster identifiers ({sources})"

        plausible_sources = []
        if namespace in managed_cluster_names:
            plausible_sources.append(f"metadata.namespace={namespace}")
        if install_ref_name in managed_cluster_names:
            plausible_sources.append(f"spec.clusterInstallRef.name={install_ref_name}")
        if plausible_sources:
            return (
                None,
                f"plausible but unverified identifier(s) ({', '.join(plausible_sources)})",
            )

        return None, None
