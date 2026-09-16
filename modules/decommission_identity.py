"""MCH operator identity capture and Pod classification (R4-03 PR E, tasks E2-E3).

This module owns two things: producing the read-side operator-identity value
described by plan §10.2.2 (`operator_deployment`) and §10.2.3
(`operator_identity_unavailable`), shaped for `lib.teardown_record.TeardownRecord`
(`capture_operator_identity`); and classifying an already-fetched Pod inventory
against that identity's controller chain, back to the exact recorded operator
Deployment UID (`classify_pods`, plan §11C.2/§11C.3). It performs no
persistence, no mutation, and no orchestration -- those stay in
`modules/decommission.py`. In particular this module does not own teardown
phases, RunRecord/StateManager access, checkpoints, DELETE, waits, namespace
reads, Pod-list orchestration, dry-run policy, or completion transitions.

The identity is discovered by finding the one `Succeeded` ClusterServiceVersion
that owns the MultiClusterHub CRD (`lib.teardown_record.MCH_OWNED_CRD`), then by
resolving its install-strategy Deployment. `classify_pods` consumes that
identity together with an already-fetched Pod inventory, resolving each Pod
through its controller ReplicaSet to the recorded operator Deployment UID; it
never lists Pods, reads Namespaces, or decides Pod-list-read policy -- that is
the caller's responsibility (task E4).
"""

from dataclasses import dataclass
from typing import Any, Dict, Mapping, NoReturn, Optional, Sequence, Tuple

from lib.constants import (
    ACM_NAMESPACE,
    CSV_API_GROUP,
    CSV_API_VERSION,
    CSV_INSTALL_STRATEGY_DEPLOYMENT,
    CSV_PHASE_SUCCEEDED,
    CSV_PLURAL,
    OPERATOR_IDENTITY_DISCOVERY_METHOD,
    OPERATOR_IDENTITY_UNAVAILABLE_REASONS,
    POD_CLASSIFICATION_DRAIN_BLOCKING,
    POD_CLASSIFICATION_IDENTITY_INCONSISTENT,
    POD_CLASSIFICATION_IDENTITY_UNAVAILABLE,
    POD_CLASSIFICATION_OPERATOR_OWNED,
)
from lib.exceptions import SwitchoverError, ValidationError
from lib.strict_read import StrictReadOutcome, StrictReadStatus
from lib.teardown_record import MCH_OWNED_CRD
from lib.validation import InputValidator

# Section 10.2.3 evidence_summary text: one static, sanitized sentence per closed
# reason. Never built from API-supplied strings or server-side counts.
EVIDENCE_SUMMARY_BY_REASON: Dict[str, str] = {
    "csv_absent": "No ClusterServiceVersion owning the MultiClusterHub CRD was found.",
    "csv_ambiguous": "More than one ClusterServiceVersion candidate identifies the MultiClusterHub operator.",
    "csv_not_succeeded": "The MultiClusterHub operator ClusterServiceVersion has not reached the Succeeded phase.",
    "csv_owned_crd_mismatch": "The candidate ClusterServiceVersion does not own the MultiClusterHub CRD.",
    "install_deployment_absent": (
        "The ClusterServiceVersion install strategy does not name a usable operator Deployment."
    ),
    "install_deployment_ambiguous": "The ClusterServiceVersion install strategy names more than one Deployment.",
    "deployment_read_failed": "The MultiClusterHub operator Deployment could not be read.",
    "deployment_identity_incomplete": "The MultiClusterHub operator Deployment did not carry a usable identity.",
}
assert set(EVIDENCE_SUMMARY_BY_REASON) == set(
    OPERATOR_IDENTITY_UNAVAILABLE_REASONS
), "EVIDENCE_SUMMARY_BY_REASON must cover exactly OPERATOR_IDENTITY_UNAVAILABLE_REASONS"

# Sentinel distinguishing ">1 install-strategy Deployment" from "no usable name".
_AMBIGUOUS_DEPLOYMENT = object()


@dataclass(frozen=True)
class OperatorIdentity:
    """Exactly one of the two §10.2.2/§10.2.3 record values, shaped for TeardownRecord."""

    operator_deployment: Optional[Dict[str, Any]] = None
    operator_identity_unavailable: Optional[Dict[str, Any]] = None

    def __post_init__(self) -> None:
        if (self.operator_deployment is None) == (self.operator_identity_unavailable is None):
            raise ValueError(
                "OperatorIdentity must carry exactly one of operator_deployment / operator_identity_unavailable"
            )

    @property
    def available(self) -> bool:
        return self.operator_deployment is not None


@dataclass(frozen=True)
class PodDecision:
    """One Pod's classification decision (task E3, plan §11C.2/§11C.3)."""

    name: str
    decision: str


@dataclass(frozen=True)
class ClassificationPass:
    """The result of one `classify_pods` call over one Pod inventory snapshot."""

    decisions: Tuple[PodDecision, ...]
    identity_status: Optional[str]
    operator_deployment_resource_version: Optional[str]

    @property
    def blocking(self) -> Tuple[PodDecision, ...]:
        return tuple(d for d in self.decisions if d.decision == POD_CLASSIFICATION_DRAIN_BLOCKING)


def classify_pods(client: Any, pods: Sequence[Mapping[str, Any]], identity: OperatorIdentity) -> ClassificationPass:
    """Classify an already-fetched Pod inventory by controller chain (task E3).

    Does not list Pods, read Namespaces, or handle Pod-list-read failures --
    those are the caller's (task E4) policy.
    """
    if not identity.available:
        decisions = tuple(_blocking_decision(pod) for pod in pods)
        return ClassificationPass(
            decisions=decisions,
            identity_status=POD_CLASSIFICATION_IDENTITY_UNAVAILABLE,
            operator_deployment_resource_version=None,
        )

    recorded = identity.operator_deployment
    assert recorded is not None  # guaranteed by OperatorIdentity's exactly-one invariant
    recorded_name = recorded["name"]
    recorded_namespace = recorded["namespace"]
    recorded_uid = recorded["uid"]

    verified_resource_version: Optional[str] = None
    if _is_valid_name(recorded_name) and recorded_namespace == ACM_NAMESPACE:
        outcome = client.get_deployment_strict(name=recorded_name, namespace=recorded_namespace)
        if outcome.status is StrictReadStatus.ITEMS:
            resource_metadata = outcome.resource.get("metadata") if isinstance(outcome.resource, dict) else None
            live_uid = resource_metadata.get("uid") if isinstance(resource_metadata, dict) else None
            if isinstance(live_uid, str) and live_uid == recorded_uid:
                verified_resource_version = outcome.resource_version

    if verified_resource_version is None:
        decisions = tuple(_blocking_decision(pod) for pod in pods)
        return ClassificationPass(
            decisions=decisions,
            identity_status=POD_CLASSIFICATION_IDENTITY_INCONSISTENT,
            operator_deployment_resource_version=None,
        )

    memo: Dict[Any, StrictReadOutcome] = {}
    decisions = tuple(_classify_pod(client, pod, recorded, memo) for pod in pods)
    return ClassificationPass(
        decisions=decisions,
        identity_status=None,
        operator_deployment_resource_version=verified_resource_version,
    )


def _blocking_decision(pod: Mapping[str, Any]) -> PodDecision:
    return PodDecision(name=_pod_name(pod), decision=POD_CLASSIFICATION_DRAIN_BLOCKING)


def _pod_name(pod: Mapping[str, Any]) -> str:
    metadata = pod.get("metadata") if isinstance(pod, dict) else None
    if not isinstance(metadata, dict):
        return ""
    name = metadata.get("name")
    return name if isinstance(name, str) else ""


def _classify_pod(
    client: Any,
    pod: Mapping[str, Any],
    recorded: Dict[str, Any],
    memo: Dict[Any, StrictReadOutcome],
) -> PodDecision:
    name = _pod_name(pod)
    metadata = pod.get("metadata") if isinstance(pod, dict) else None
    if not isinstance(metadata, dict):
        return PodDecision(name=name, decision=POD_CLASSIFICATION_DRAIN_BLOCKING)

    owned = PodDecision(name=name, decision=POD_CLASSIFICATION_OPERATOR_OWNED)
    blocked = PodDecision(name=name, decision=POD_CLASSIFICATION_DRAIN_BLOCKING)

    if metadata.get("namespace") != recorded["namespace"]:
        return blocked

    controller_ref = _sole_controller_ref(metadata.get("owner_references"))
    if controller_ref is None:
        return blocked
    if controller_ref.get("api_version") != "apps/v1" or controller_ref.get("kind") != "ReplicaSet":
        return blocked

    ref_name = controller_ref.get("name")
    if not isinstance(ref_name, str) or not ref_name or not _is_valid_name(ref_name):
        return blocked
    ref_uid = controller_ref.get("uid")
    if not isinstance(ref_uid, str) or not ref_uid:
        return blocked

    namespace = metadata["namespace"]
    memo_key = (namespace, "ReplicaSet", ref_name, ref_uid)
    if memo_key not in memo:
        memo[memo_key] = client.get_replicaset_strict(name=ref_name, namespace=namespace)
    rs_outcome = memo[memo_key]

    if rs_outcome.status is not StrictReadStatus.ITEMS:
        return blocked
    rs_resource = rs_outcome.resource
    rs_metadata = rs_resource.get("metadata") if isinstance(rs_resource, dict) else None
    if not isinstance(rs_metadata, dict) or rs_metadata.get("uid") != ref_uid:
        return blocked

    rs_controller_ref = _sole_controller_ref(rs_metadata.get("owner_references"))
    if rs_controller_ref is None:
        return blocked
    if rs_controller_ref.get("api_version") != "apps/v1" or rs_controller_ref.get("kind") != "Deployment":
        return blocked
    if rs_controller_ref.get("name") != recorded["name"] or rs_controller_ref.get("uid") != recorded["uid"]:
        return blocked

    return owned


def _sole_controller_ref(owner_references: Any) -> Optional[Dict[str, Any]]:
    """The exactly-one `controller is True` entry, or None (absent/invalid/ambiguous)."""
    if not isinstance(owner_references, list):
        return None
    if not all(isinstance(ref, dict) for ref in owner_references):
        return None
    controllers = [ref for ref in owner_references if ref.get("controller") is True]
    if len(controllers) != 1:
        return None
    return controllers[0]


def capture_operator_identity(
    client: Any,
    *,
    mch_teardown_key: str,
    mch_expected_uid: str,
    captured_at: str,
) -> OperatorIdentity:
    """Capture the live MCH operator identity, or a sanitized unavailable reason.

    `client` is a KubeClient-like object exposing `list_custom_resources_strict`,
    `get_custom_resource_strict`, and `get_deployment_strict`. The three keyword
    values are caller-supplied and copied verbatim into the returned value; they
    are never generated here.

    Raises `lib.exceptions.SwitchoverError` for fatal outcomes (an unreadable or
    malformed CSV inventory), with a sanitized, stable stage message. Raises
    `ValueError` if any of the three keyword values is not a non-empty string.
    """
    _require_non_empty_str("mch_teardown_key", mch_teardown_key)
    _require_non_empty_str("mch_expected_uid", mch_expected_uid)
    _require_non_empty_str("captured_at", captured_at)

    csv_list = client.list_custom_resources_strict(
        group=CSV_API_GROUP, version=CSV_API_VERSION, plural=CSV_PLURAL, namespace=ACM_NAMESPACE
    )
    if csv_list.status is StrictReadStatus.CRD_ABSENT:
        return _unavailable("csv_absent", mch_teardown_key, mch_expected_uid, captured_at)
    if csv_list.status is not StrictReadStatus.ITEMS:
        _raise_fatal("ClusterServiceVersion inventory unreadable")

    if not csv_list.items:
        return _unavailable("csv_absent", mch_teardown_key, mch_expected_uid, captured_at)

    owning = [item for item in csv_list.items if _owns_mch_crd(item)]
    if not owning:
        return _unavailable("csv_owned_crd_mismatch", mch_teardown_key, mch_expected_uid, captured_at)

    candidates = [item for item in owning if _phase(item) == CSV_PHASE_SUCCEEDED]
    if not candidates:
        return _unavailable("csv_not_succeeded", mch_teardown_key, mch_expected_uid, captured_at)
    if len(candidates) > 1:
        return _unavailable("csv_ambiguous", mch_teardown_key, mch_expected_uid, captured_at)

    candidate = candidates[0]
    candidate_name = _metadata(candidate).get("name")
    if not isinstance(candidate_name, str) or not _is_valid_name(candidate_name):
        _raise_fatal("ClusterServiceVersion candidate name is invalid")
    candidate_uid = _metadata(candidate).get("uid")
    if not isinstance(candidate_uid, str) or not candidate_uid:
        _raise_fatal("ClusterServiceVersion candidate is missing identity")

    csv_get = client.get_custom_resource_strict(
        group=CSV_API_GROUP,
        version=CSV_API_VERSION,
        plural=CSV_PLURAL,
        name=candidate_name,
        namespace=ACM_NAMESPACE,
    )
    if csv_get.status is StrictReadStatus.ERROR:
        _raise_fatal("ClusterServiceVersion re-read failed")
    if csv_get.status in (StrictReadStatus.OBJECT_ABSENT, StrictReadStatus.CRD_ABSENT):
        return _unavailable("csv_absent", mch_teardown_key, mch_expected_uid, captured_at)
    if csv_get.status is not StrictReadStatus.ITEMS:
        _raise_fatal("ClusterServiceVersion re-read failed")

    body = csv_get.resource
    body_uid = _metadata(body).get("uid")
    if not isinstance(body_uid, str) or not body_uid:
        _raise_fatal("ClusterServiceVersion re-read is missing identity")
    if body_uid != candidate_uid:
        return _unavailable("csv_ambiguous", mch_teardown_key, mch_expected_uid, captured_at)
    if not _owns_mch_crd(body):
        return _unavailable("csv_owned_crd_mismatch", mch_teardown_key, mch_expected_uid, captured_at)
    if _phase(body) != CSV_PHASE_SUCCEEDED:
        return _unavailable("csv_not_succeeded", mch_teardown_key, mch_expected_uid, captured_at)

    deployment_name = _install_deployment_name(body)
    if deployment_name is None:
        return _unavailable("install_deployment_absent", mch_teardown_key, mch_expected_uid, captured_at)
    if deployment_name is _AMBIGUOUS_DEPLOYMENT:
        return _unavailable("install_deployment_ambiguous", mch_teardown_key, mch_expected_uid, captured_at)

    deployment_get = client.get_deployment_strict(name=deployment_name, namespace=ACM_NAMESPACE)
    if deployment_get.status is StrictReadStatus.OBJECT_ABSENT:
        return _unavailable("install_deployment_absent", mch_teardown_key, mch_expected_uid, captured_at)
    if deployment_get.status is not StrictReadStatus.ITEMS:
        return _unavailable("deployment_read_failed", mch_teardown_key, mch_expected_uid, captured_at)

    deployment_uid = _metadata(deployment_get.resource).get("uid")
    if not isinstance(deployment_uid, str) or not deployment_uid:
        return _unavailable("deployment_identity_incomplete", mch_teardown_key, mch_expected_uid, captured_at)

    operator_deployment = {
        "namespace": ACM_NAMESPACE,
        "name": deployment_name,
        "uid": deployment_uid,
        "discovery_method": OPERATOR_IDENTITY_DISCOVERY_METHOD,
        "captured_at": captured_at,
        "csv": {
            "namespace": ACM_NAMESPACE,
            "name": candidate_name,
            "uid": body_uid,
            "owned_crd": MCH_OWNED_CRD,
        },
        "mch_teardown_key": mch_teardown_key,
        "mch_expected_uid": mch_expected_uid,
    }
    return OperatorIdentity(operator_deployment=operator_deployment)


def _unavailable(reason: str, mch_teardown_key: str, mch_expected_uid: str, captured_at: str) -> OperatorIdentity:
    return OperatorIdentity(
        operator_identity_unavailable={
            "reason": reason,
            "discovery_method": OPERATOR_IDENTITY_DISCOVERY_METHOD,
            "captured_at": captured_at,
            "evidence_summary": EVIDENCE_SUMMARY_BY_REASON[reason],
            "mch_teardown_key": mch_teardown_key,
            "mch_expected_uid": mch_expected_uid,
        }
    )


def _raise_fatal(stage_message: str) -> NoReturn:
    # A stable, sanitized stage message -- never the StrictReadOutcome.reason code
    # or any API-supplied string.
    raise SwitchoverError(f"Cannot capture the MultiClusterHub operator identity: {stage_message}")


def _require_non_empty_str(label: str, value: Any) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty string")


def _metadata(item: Any) -> Dict[str, Any]:
    metadata = item.get("metadata") if isinstance(item, dict) else None
    return metadata if isinstance(metadata, dict) else {}


def _owns_mch_crd(item: Dict[str, Any]) -> bool:
    """Tolerate non-list/non-mapping shapes as "not owning" (read flow step 3)."""
    spec = item.get("spec")
    if not isinstance(spec, dict):
        return False
    crds = spec.get("customresourcedefinitions")
    if not isinstance(crds, dict):
        return False
    owned = crds.get("owned")
    if not isinstance(owned, list):
        return False
    return any(isinstance(entry, dict) and entry.get("name") == MCH_OWNED_CRD for entry in owned)


def _phase(item: Dict[str, Any]) -> Any:
    status = item.get("status")
    return status.get("phase") if isinstance(status, dict) else None


def _is_valid_name(name: str) -> bool:
    try:
        InputValidator.validate_kubernetes_name(name)
    except ValidationError:
        return False
    return True


def _install_deployment_name(body: Dict[str, Any]) -> Any:
    """Read flow step 7. Returns a name, None (absent), or _AMBIGUOUS_DEPLOYMENT."""
    spec = body.get("spec")
    if not isinstance(spec, dict):
        return None
    install = spec.get("install")
    if not isinstance(install, dict):
        return None
    if install.get("strategy") != CSV_INSTALL_STRATEGY_DEPLOYMENT:
        return None
    install_spec = install.get("spec")
    if not isinstance(install_spec, dict):
        return None
    deployments = install_spec.get("deployments")
    if not isinstance(deployments, list) or not deployments:
        return None
    if len(deployments) > 1:
        return _AMBIGUOUS_DEPLOYMENT
    entry = deployments[0]
    if not isinstance(entry, dict):
        return None
    name = entry.get("name")
    if not isinstance(name, str) or not _is_valid_name(name):
        return None
    return name
