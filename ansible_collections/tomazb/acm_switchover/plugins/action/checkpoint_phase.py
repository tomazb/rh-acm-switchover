# SPDX-License-Identifier: MIT
"""Runtime helpers and ActionModule for the checkpoint_phase action plugin."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime, timezone

from ansible.plugins.action import ActionBase

from ansible_collections.tomazb.acm_switchover.plugins.module_utils.checkpoint import (
    CHECKPOINT_BACKEND_FILE,
    CHECKPOINT_DEFAULT_PATH,
    CHECKPOINT_REPORT_KIND_JSON,
    CHECKPOINT_VALID_STATUSES,
    KNOWN_PHASES,
    SCHEMA_VERSION,
    CheckpointIdentityMismatch,
    build_checkpoint_record,
    build_operation_identity,
    checkpoint_facts,
    is_unsafe_legacy_checkpoint,
    normalize_operation_identity,
    record_resume_start_phase,
    record_teardown_phase,
    reset_completed_phases_from,
    should_resume_phase,
    teardown_records,
    validate_operation_identity,
)
from ansible_collections.tomazb.acm_switchover.plugins.module_utils.validation import (
    ValidationError,
    validate_context_name,
    validate_report_artifact_path,
)


def build_phase_transition(checkpoint: dict, phase: str, status: str) -> dict:
    """Return a *partial* update dict reflecting a phase transition.

    Appends *phase* to ``completed_phases`` when *status* is ``"pass"`` and the
    phase has not already been recorded. Removes *phase* when *status* is
    ``"reset"`` or ``"fail"`` so a later retry can execute the phase again.

    .. warning::
        This returns only ``completed_phases`` and ``phase_status``. Callers are
        responsible for merging this into the full checkpoint record. Replacing
        the checkpoint wholesale will silently drop ``operational_data``,
        ``errors``, ``report_refs``, and timestamp fields.
    """
    completed = list(checkpoint.get("completed_phases", []))
    if status == "pass" and phase not in completed:
        completed.append(phase)
    elif status in {"fail", "reset"}:
        completed = [completed_phase for completed_phase in completed if completed_phase != phase]
    return {
        "completed_phases": completed,
        "phase_status": status,
    }


class ActionModule(ActionBase):
    """Ansible action plugin that reads, updates, and persists checkpoint state.

    Accepts:
        phase (str): switchover phase name
        checkpoint (dict): checkpoint config from ``acm_switchover_execution.checkpoint``
            (keys: ``enabled``, ``backend``, ``path``, ``reset``)
        status (str): one of ``enter``, ``pass``, ``fail``, ``reset``
        error (str, optional): error message to record on ``status: fail``
        report_ref (str, optional): artifact path to record on ``status: pass``
    """

    TRANSFERS_FILES = False
    INITIAL_PHASE = "preflight"

    def run(self, tmp=None, task_vars=None):
        super().run(tmp, task_vars)
        task_vars = task_vars or {}

        args = self._task.args
        if args.get("read_facts") is True or "teardown_record" in args:
            return self._run_teardown_record_data(task_vars=task_vars)

        phase = args.get("phase", "")
        status = args.get("status", "enter")
        identity_barrier = args.get("identity_barrier") is True
        standalone = args.get("standalone_decommission_identity") is True

        # Mutual exclusion is checked before either mode runs: the two establish
        # different identity shapes (two-hub vs primary-only), so a task asking for
        # both is ambiguous and fails closed rather than silently picking one.
        if identity_barrier and standalone:
            return {
                "failed": True,
                "msg": (
                    "identity_barrier and standalone_decommission_identity are mutually exclusive. "
                    "Use identity_barrier for the two-hub preflight barrier, or "
                    "standalone_decommission_identity for one-hub decommission."
                ),
            }

        if identity_barrier:
            if phase != self.INITIAL_PHASE or status != "enter":
                return {
                    "failed": True,
                    "msg": "identity_barrier requires phase=preflight and status=enter.",
                }
            return self._run_identity_barrier(tmp=tmp, task_vars=task_vars)

        if standalone:
            return self._run_standalone_decommission_transition(
                phase=phase,
                status=status,
                tmp=tmp,
                task_vars=task_vars,
            )

        execution = task_vars.get("acm_switchover_execution") or {}
        execution_mode = execution.get("mode", "dry_run") if isinstance(execution, Mapping) else "dry_run"
        return self._run_checkpoint_transition(
            phase=phase,
            checkpoint_config=self._task.args.get("checkpoint", {}),
            status=status,
            error=self._task.args.get("error"),
            report_ref=self._task.args.get("report_ref"),
            operational_data=self._task.args.get("operational_data") or {},
            execution_mode=execution_mode,
            is_check_mode=getattr(self._play_context, "check_mode", False) is True,
            task_vars=task_vars,
            expected_operation_identity=None,
            hub_identities=None,
        )

    def _run_teardown_record_data(self, *, task_vars: dict) -> dict:
        """Read checkpoint facts or persist one validated teardown record.

        This path owns no phase transition. It preserves the caller's phase,
        completion list, phase status, and operation identity byte for byte.
        """
        args = self._task.args
        read_facts = args.get("read_facts") is True
        has_record = "teardown_record" in args
        if read_facts and has_record:
            return {
                "failed": True,
                "msg": "read_facts and teardown_record are mutually exclusive.",
            }
        if any(name in args for name in ("phase", "status", "operational_data")):
            return {
                "failed": True,
                "msg": "Checkpoint fact and teardown-record operations do not accept phase, status, or operational_data.",
            }

        checkpoint_config = args.get("checkpoint")
        if not isinstance(checkpoint_config, Mapping):
            checkpoint_config = {}
        enabled = bool(checkpoint_config.get("enabled", False))
        execution = task_vars.get("acm_switchover_execution") or {}
        execution_mode = execution.get("mode", "dry_run") if isinstance(execution, Mapping) else "dry_run"
        is_check_mode = getattr(self._play_context, "check_mode", False) is True
        is_non_mutating = is_check_mode or execution_mode in {"dry_run", "validate"}

        if not enabled:
            if has_record and not is_non_mutating:
                return self._missing_operation_identity_failure()
            return {"changed": False, "checkpoint": {}, "facts": checkpoint_facts({})}

        backend = checkpoint_config.get("backend", CHECKPOINT_BACKEND_FILE)
        path = checkpoint_config.get("path", CHECKPOINT_DEFAULT_PATH)
        if backend != CHECKPOINT_BACKEND_FILE:
            return {
                "failed": True,
                "msg": f"Invalid checkpoint backend '{backend}'. Expected: file.",
            }
        try:
            validate_report_artifact_path(path)
        except ValidationError as exc:
            return {"failed": True, "msg": str(exc)}

        checkpoint_data = self._load_checkpoint(path, quarantine_corrupt=False)
        if checkpoint_data.get("failed"):
            return checkpoint_data
        try:
            # This data route supplies mutation authority. Keep the general facts
            # facade tolerant for reporting callers, but reject malformed persisted
            # operational_data before exposing an empty record map here.
            teardown_records(checkpoint_data)
            facts = checkpoint_facts(checkpoint_data)
        except ValueError as exc:
            return {"failed": True, "msg": str(exc)}
        if read_facts or is_non_mutating:
            return {"changed": False, "checkpoint": checkpoint_data, "facts": facts}

        if not checkpoint_data.get("operation_identity"):
            return self._missing_operation_identity_failure()
        record = args.get("teardown_record")
        if not isinstance(record, Mapping):
            return {"failed": True, "msg": "teardown_record must be a mapping."}
        allowed = {
            "key",
            "expected_uid",
            "phase",
            "observed_at",
            "resource_versions",
            "absence_proofs",
            "operator_deployment",
            "operator_identity_unavailable",
        }
        unknown = set(record) - allowed
        if unknown:
            return {
                "failed": True,
                "msg": f"teardown_record carries unknown fields {sorted(unknown)}.",
            }
        if any(name not in record for name in ("key", "expected_uid", "phase")):
            return {
                "failed": True,
                "msg": "teardown_record requires key, expected_uid, and phase.",
            }

        before_operational_data = deepcopy(checkpoint_data.get("operational_data"))
        try:
            record_teardown_phase(
                checkpoint_data,
                record["key"],
                record["expected_uid"],
                record["phase"],
                observed_at=record.get("observed_at"),
                resource_versions=record.get("resource_versions"),
                absence_proofs=record.get("absence_proofs"),
                operator_deployment=record.get("operator_deployment"),
                operator_identity_unavailable=record.get("operator_identity_unavailable"),
            )
        except ValueError as exc:
            return {"failed": True, "msg": str(exc)}

        changed = checkpoint_data.get("operational_data") != before_operational_data
        if changed:
            checkpoint_data["updated_at"] = datetime.now(timezone.utc).isoformat()
            save_result = self._save_checkpoint(path, checkpoint_data)
            if save_result is not None and save_result.get("failed"):
                return save_result
        return {
            "changed": changed,
            "checkpoint": checkpoint_data,
            "facts": checkpoint_facts(checkpoint_data),
        }

    def _run_identity_barrier(self, *, tmp, task_vars: dict) -> dict:
        args = self._task.args
        hubs = args.get("hubs")
        operation = args.get("operation")
        execution = args.get("execution")
        test_overrides = args.get("test_overrides")
        checkpoint_config = args.get("checkpoint")
        if not isinstance(hubs, Mapping):
            hubs = {}
        if not isinstance(operation, Mapping):
            operation = {}
        if not isinstance(execution, Mapping):
            execution = {}
        if not isinstance(test_overrides, Mapping):
            test_overrides = {}
        if not isinstance(checkpoint_config, Mapping):
            checkpoint_config = {}

        restore_only = operation.get("restore_only") is True
        required_roles = ("secondary",) if restore_only else ("primary", "secondary")
        validated_hubs = {}
        for role in required_roles:
            hub = hubs.get(role)
            if not isinstance(hub, Mapping):
                return self._identity_failure(role)
            context = hub.get("context")
            if not isinstance(context, str):
                return self._identity_failure(role)
            try:
                validate_context_name(context)
            except ValidationError:
                return self._identity_failure(role)
            validated_hubs[role] = {
                "context": context,
                "kubeconfig": hub.get("kubeconfig"),
            }

        if not restore_only and validated_hubs["primary"]["context"] == validated_hubs["secondary"]["context"]:
            return {
                "failed": True,
                "msg": "Primary and secondary Kubernetes context names must differ for a normal two-hub switchover.",
            }

        execution_mode = execution.get("mode", "execute")
        override_configured = execution_mode in {"validate", "dry_run"} and "non_live_hub_identities" in test_overrides
        override_identities = test_overrides.get("non_live_hub_identities") if override_configured else None
        trusted_uids = {}
        for role in required_roles:
            if override_configured:
                role_identity = override_identities.get(role) if isinstance(override_identities, Mapping) else None
                override_uid = role_identity.get("cluster_uid") if isinstance(role_identity, Mapping) else None
                try:
                    trusted_uids[role] = self._validated_namespace_uid(
                        role,
                        {"resources": [{"metadata": {"uid": override_uid}}]},
                    )
                except ValidationError:
                    return self._identity_failure(role)
            else:
                try:
                    trusted_uids[role] = self._read_live_namespace_uid(
                        role,
                        validated_hubs[role],
                        task_vars,
                        tmp,
                    )
                except ValidationError:
                    return self._identity_failure(role)

        if not restore_only and trusted_uids["primary"] == trusted_uids["secondary"]:
            return {
                "failed": True,
                "msg": (
                    "Primary and secondary hubs resolve to the same physical Kubernetes cluster. "
                    "Refusing the normal two-hub switchover."
                ),
            }

        expected_operation_identity = self._build_trusted_operation_identity(
            hubs=validated_hubs,
            operation=operation,
            collection_version=args.get("collection_version"),
            trusted_uids=trusted_uids,
        )
        identity_summary = {role: {"cluster_uid": trusted_uids[role]} for role in required_roles}
        if not bool(checkpoint_config.get("enabled", False)):
            return {
                "changed": False,
                "skipped_phase": False,
                "facts": {},
                "hub_identities": identity_summary,
            }

        return self._run_checkpoint_transition(
            phase=self.INITIAL_PHASE,
            checkpoint_config=checkpoint_config,
            status="enter",
            error=None,
            report_ref=None,
            operational_data={},
            execution_mode=execution_mode,
            is_check_mode=getattr(self._play_context, "check_mode", False) is True,
            task_vars=task_vars,
            expected_operation_identity=expected_operation_identity,
            hub_identities=identity_summary,
        )

    STANDALONE_PHASE = "decommission"

    #: The execution modes a standalone decommission transition understands. Anything
    #: else is refused rather than silently treated as a preview.
    STANDALONE_KNOWN_MODES = frozenset({"execute", "dry_run", "validate"})

    def _run_standalone_decommission_transition(self, *, phase: str, status: str, tmp, task_vars: dict) -> dict:
        """Establish and re-prove a PRIMARY-ONLY operation identity for standalone decommission.

        Standalone decommission is a one-hub workflow: there is no secondary hub to
        read, and the two-hub distinctness predicate does not apply. It is entered
        ONLY through the explicit ``standalone_decommission_identity: true`` argument
        and is never inferred from a missing secondary, from the caller, or from the
        shape of an existing checkpoint.

        Every one of the four real statuses carries the freshly built identity as an
        explicit ``expected_operation_identity``, so
        ``_canonical_established_operation_identity`` -- which admits only two-hub and
        secondary-only records -- is never consulted and needs no change.
        """
        args = self._task.args

        if phase != self.STANDALONE_PHASE:
            return {
                "failed": True,
                "msg": (f"standalone_decommission_identity requires phase={self.STANDALONE_PHASE}, got '{phase}'."),
            }

        if status not in CHECKPOINT_VALID_STATUSES:
            return {
                "failed": True,
                "msg": (f"Invalid checkpoint status '{status}'. Expected one of: enter, pass, fail, reset."),
            }

        checkpoint_config = args.get("checkpoint")
        if not isinstance(checkpoint_config, Mapping):
            checkpoint_config = {}

        # Refused BEFORE _run_checkpoint_transition, which is where reset is
        # processed. The reset configuration bypasses identity validation:
        # _build_reset_from_checkpoint overwrites operation_identity unconditionally
        # and returns before validation, and validate_operation_identity is skipped
        # whenever has_explicit_reset. Standalone is the first caller that both
        # supplies a primary-only identity and is legal off preflight/enter, so
        # without this a standalone transition could silently overwrite an
        # established TWO-HUB identity with the primary-only one, raising no
        # CheckpointIdentityMismatch.
        if bool(checkpoint_config.get("reset", False)) or bool(checkpoint_config.get("reset_from")):
            return {
                "failed": True,
                "msg": (
                    "Refusing a standalone decommission checkpoint transition while checkpoint.reset "
                    "or checkpoint.reset_from is set: the reset configuration bypasses operation "
                    "identity validation and could rebind an established identity. Clear both. To "
                    "start a standalone decommission from empty state, remove, rename or repoint "
                    "the checkpoint file at acm_switchover_execution.checkpoint.path -- which "
                    "discards the teardown obligations that record carried."
                ),
            }

        hubs = task_vars.get("acm_switchover_hubs")
        if not isinstance(hubs, Mapping):
            hubs = {}
        primary = hubs.get("primary")
        if not isinstance(primary, Mapping):
            primary = {}
        context = primary.get("context")
        kubeconfig = primary.get("kubeconfig")

        # Explicit routing only. A direct action call bypasses the playbook's assert,
        # so this is checked here too -- and BEFORE any Kubernetes client is built,
        # so an absent kubeconfig can never fall back to an ambient/default context.
        if not isinstance(context, str) or not context.strip():
            return self._standalone_routing_failure("context")
        if not isinstance(kubeconfig, str) or not kubeconfig.strip():
            return self._standalone_routing_failure("kubeconfig")
        try:
            validate_context_name(context)
        except ValidationError:
            return self._standalone_routing_failure("context")

        validated_primary = {"context": context, "kubeconfig": kubeconfig}

        execution = task_vars.get("acm_switchover_execution") or {}
        execution_mode = execution.get("mode", "dry_run") if isinstance(execution, Mapping) else "dry_run"
        is_check_mode = getattr(self._play_context, "check_mode", False) is True

        # An unrecognised mode is refused rather than treated as non-execute. Only
        # `execute` performs the identity read, but every mode other than `dry_run`
        # is treated as mutating downstream -- the role's delete guards fire on
        # `mode != 'dry_run'` -- so a typo such as `Execute` would otherwise skip the
        # identity read, skip the role's own gate, and still delete while persisting
        # an empty operation identity.
        if execution_mode not in self.STANDALONE_KNOWN_MODES:
            known = ", ".join(sorted(self.STANDALONE_KNOWN_MODES))
            return {
                "failed": True,
                "msg": (
                    f"Unknown execution mode '{execution_mode}' for a standalone decommission "
                    f"checkpoint transition. Expected one of: {known}."
                ),
            }

        # Non-mutating modes persist no authoritative identity and perform NO
        # identity read at all. Execute mode always reads live; the
        # non_live_hub_identities override stays gated to validate/dry_run on the
        # two-hub barrier and is deliberately not honoured here.
        if execution_mode != "execute" or is_check_mode:
            # Return WITHOUT descending into the transition, mirroring the two-hub
            # barrier's own non-mutating exit.
            #
            # This path must never call _run_checkpoint_transition with
            # expected_operation_identity=None. Doing so consults
            # _canonical_established_operation_identity, which admits only two-hub and
            # secondary-only records and therefore rejects the primary-only identity a
            # real standalone execute run persists -- so a preview of a partially
            # completed standalone decommission would hard-fail, advising the operator
            # to "run the preflight identity barrier first", which a one-hub workflow
            # has no way to run. Passing an empty identity instead is not a fix either:
            # validate_operation_identity would then compare {} against the stored
            # record and raise a mismatch.
            #
            # There is nothing to verify here in any case: verification means proving
            # physical identity, and section 10.3.8 forbids the live read outside
            # execute mode.
            return {
                "changed": False,
                "skipped_phase": False,
                "facts": {},
            }

        try:
            primary_uid = self._read_live_namespace_uid("primary", validated_primary, task_vars, tmp)
        except ValidationError:
            return self._standalone_identity_failure()
        expected_operation_identity = self._build_trusted_operation_identity(
            hubs={"primary": validated_primary},
            operation=task_vars.get("acm_switchover_operation") or {},
            collection_version=args.get("collection_version"),
            trusted_uids={"primary": primary_uid},
        )
        identity_summary = {"primary": {"cluster_uid": primary_uid}}

        return self._run_checkpoint_transition(
            phase=phase,
            checkpoint_config=checkpoint_config,
            status=status,
            error=args.get("error"),
            report_ref=args.get("report_ref"),
            operational_data=args.get("operational_data") or {},
            execution_mode=execution_mode,
            is_check_mode=is_check_mode,
            task_vars=task_vars,
            expected_operation_identity=expected_operation_identity,
            hub_identities=identity_summary,
        )

    @staticmethod
    def _standalone_identity_failure() -> dict:
        """One-hub wording. _identity_failure speaks of "the normal two-hub switchover",
        which is actively misleading for an operator decommissioning a single hub."""
        return {
            "failed": True,
            "msg": (
                "Unable to verify the primary hub physical identity from the live kube-system "
                "Namespace UID. Refusing the standalone decommission before any teardown."
            ),
        }

    @staticmethod
    def _standalone_routing_failure(field: str) -> dict:
        return {
            "failed": True,
            "msg": (
                f"Standalone decommission requires an explicit non-empty primary {field} in "
                "acm_switchover_hubs.primary. Refusing to use an implicit or default Kubernetes context."
            ),
        }

    _AUTO_PYTHON_INTERPRETERS = frozenset(
        {
            None,
            "",
            "auto",
            "auto_legacy",
            "auto_silent",
            "auto_legacy_silent",
        }
    )

    def _task_vars_for_controller_module(self, task_vars: dict) -> dict:
        """Ensure nested controller-side modules use a concrete Python interpreter.

        ansible-core 2.16 local-connection auto-discovery frequently selects
        ``/usr/bin/python3``, which commonly lacks the ``kubernetes`` package
        installed beside ansible-core. Identity-barrier live reads then fail
        before any API request. Prefer an explicit inventory/interpreter setting;
        otherwise pin to the playbook controller interpreter.
        """
        effective = dict(task_vars or {})
        configured = effective.get("ansible_python_interpreter")
        if configured in self._AUTO_PYTHON_INTERPRETERS:
            playbook_python = effective.get("ansible_playbook_python")
            if isinstance(playbook_python, str) and playbook_python.strip():
                effective["ansible_python_interpreter"] = playbook_python
            else:
                effective["ansible_python_interpreter"] = sys.executable
        return effective

    def _read_live_namespace_uid(self, role: str, hub: Mapping, task_vars: dict, tmp) -> str:
        try:
            result = self._execute_module(
                module_name="kubernetes.core.k8s_info",
                module_args={
                    "api_version": "v1",
                    "kind": "Namespace",
                    "name": "kube-system",
                    "kubeconfig": hub.get("kubeconfig"),
                    "context": hub.get("context"),
                },
                task_vars=self._task_vars_for_controller_module(task_vars),
                tmp=tmp,
            )
        except Exception:
            raise ValidationError(self._identity_failure(role)["msg"]) from None
        return self._validated_namespace_uid(role, result)

    def _validated_namespace_uid(self, role: str, result) -> str:
        message = self._identity_failure(role)["msg"]
        if not isinstance(result, Mapping) or result.get("failed") is True:
            raise ValidationError(message)
        resources = result.get("resources")
        if not isinstance(resources, list) or len(resources) != 1:
            raise ValidationError(message)
        resource = resources[0]
        if not isinstance(resource, Mapping):
            raise ValidationError(message)
        metadata = resource.get("metadata")
        if not isinstance(metadata, Mapping):
            raise ValidationError(message)
        uid = metadata.get("uid")
        if not isinstance(uid, str) or not uid.strip():
            raise ValidationError(message)
        return uid.strip()

    def _build_trusted_operation_identity(
        self,
        hubs: Mapping,
        operation: Mapping,
        collection_version,
        trusted_uids: Mapping,
    ) -> dict:
        sanitized_local_hubs = {
            role: {"context": hubs[role]["context"]} for role in ("primary", "secondary") if role in hubs
        }
        trusted_local_hub_identities = {
            role: {"cluster_uid": trusted_uids[role]} for role in ("primary", "secondary") if role in trusted_uids
        }
        return build_operation_identity(
            hubs=sanitized_local_hubs,
            operation=dict(operation),
            collection_version=collection_version,
            hub_identities=trusted_local_hub_identities,
        )

    @staticmethod
    def _identity_failure(role: str) -> dict:
        return {
            "failed": True,
            "msg": (
                f"Unable to verify the {role} hub physical identity from the live kube-system Namespace UID. "
                "Refusing the normal two-hub switchover."
            ),
        }

    def _run_checkpoint_transition(
        self,
        *,
        phase: str,
        checkpoint_config,
        status: str,
        error,
        report_ref,
        operational_data,
        execution_mode,
        is_check_mode: bool,
        task_vars: dict,
        expected_operation_identity,
        hub_identities,
    ) -> dict:
        if not isinstance(checkpoint_config, Mapping):
            checkpoint_config = {}
        is_non_mutating = is_check_mode or execution_mode in {"dry_run", "validate"}

        backend = checkpoint_config.get("backend", CHECKPOINT_BACKEND_FILE)
        path = checkpoint_config.get("path", CHECKPOINT_DEFAULT_PATH)
        reset_from = checkpoint_config.get("reset_from")

        if status not in CHECKPOINT_VALID_STATUSES:
            return {
                "failed": True,
                "msg": f"Invalid checkpoint status '{status}'. Expected one of: enter, pass, fail, reset.",
            }

        if not phase:
            return {
                "failed": True,
                "msg": "Missing required checkpoint phase.",
            }

        if phase not in KNOWN_PHASES:
            valid_phases = ", ".join(KNOWN_PHASES)
            return {
                "failed": True,
                "msg": f"Invalid checkpoint phase '{phase}'. Expected one of: {valid_phases}.",
            }

        if backend not in {CHECKPOINT_BACKEND_FILE}:
            return {
                "failed": True,
                "msg": f"Invalid checkpoint backend '{backend}'. Expected: file.",
            }

        try:
            validate_report_artifact_path(path)
        except ValidationError as exc:
            return {
                "failed": True,
                "msg": str(exc),
            }

        if reset_from and reset_from not in KNOWN_PHASES:
            valid_phases = ", ".join(KNOWN_PHASES)
            return {
                "failed": True,
                "msg": f"Invalid checkpoint reset_from '{reset_from}'. Expected one of: {valid_phases}.",
            }

        reset = bool(checkpoint_config.get("reset", False))
        has_explicit_reset = reset or bool(reset_from)
        should_reset = reset and status == "enter" and phase == self.INITIAL_PHASE
        if expected_operation_identity is None and has_explicit_reset:
            expected_operation_identity = build_operation_identity(
                hubs=task_vars.get("acm_switchover_hubs") or {},
                operation=task_vars.get("acm_switchover_operation") or {},
                collection_version=task_vars.get("acm_switchover_collection_version"),
                hub_identities=task_vars.get("acm_switchover_hub_identities") or {},
            )

        if should_reset and not is_check_mode:
            checkpoint_data = build_checkpoint_record(
                phase,
                {},
                operation_identity=expected_operation_identity,
            )
        else:
            checkpoint_data = self._load_checkpoint(path, quarantine_corrupt=not is_non_mutating)
        if checkpoint_data.get("failed"):
            return checkpoint_data

        if expected_operation_identity is None:
            established_identity = checkpoint_data.get("operation_identity")
            if is_unsafe_legacy_checkpoint(checkpoint_data):
                # Preserve the existing, more specific unsafe legacy refusal
                # in _normalize_checkpoint_data.
                expected_operation_identity = {}
            elif established_identity is None and (
                checkpoint_data.get("schema_version") == SCHEMA_VERSION and checkpoint_data.get("completed_phases")
            ):
                # Preserve the existing, more specific missing identity
                # refusal in _normalize_checkpoint_data.
                expected_operation_identity = {}
            elif established_identity is None:
                if bool(checkpoint_config.get("enabled", False)) and execution_mode == "execute" and not is_check_mode:
                    return self._missing_operation_identity_failure()
                expected_operation_identity = {}
            else:
                expected_operation_identity = self._canonical_established_operation_identity(established_identity)
                if expected_operation_identity is None:
                    return self._missing_operation_identity_failure()

        if is_check_mode:
            read_only_failure = self._validate_checkpoint_read_only(
                checkpoint_data=checkpoint_data,
                expected_operation_identity=expected_operation_identity,
                has_explicit_reset=has_explicit_reset,
            )
            if read_only_failure is not None:
                return read_only_failure
            if status == "enter":
                already_done = (
                    False if execution_mode == "validate" else not should_resume_phase(checkpoint_data, phase)
                )
                result = {
                    "changed": False,
                    "checkpoint": checkpoint_data,
                    "skipped_phase": already_done,
                    "facts": checkpoint_facts(checkpoint_data),
                }
                if hub_identities is not None:
                    result["hub_identities"] = hub_identities
                return result
            result = {
                "changed": False,
                "checkpoint": checkpoint_data,
                "check_mode": True,
            }
            if execution_mode in {"dry_run", "validate"}:
                result[execution_mode] = True
            return result

        checkpoint_data, operation_identity_changed = self._normalize_checkpoint_data(
            checkpoint_data=checkpoint_data,
            phase=phase,
            status=status,
            reset_from=reset_from,
            has_explicit_reset=has_explicit_reset,
            expected_operation_identity=expected_operation_identity,
        )
        if checkpoint_data.get("failed"):
            return checkpoint_data

        phase_changed = checkpoint_data.get("phase") != phase
        checkpoint_data["phase"] = phase

        if should_reset and backend == CHECKPOINT_BACKEND_FILE and not is_non_mutating:
            save_result = self._save_checkpoint(path, checkpoint_data)
            if save_result is not None and save_result.get("failed"):
                return save_result

        if status == "enter":
            resume_summary_changed = False
            already_done = False if execution_mode == "validate" else not should_resume_phase(checkpoint_data, phase)
            if (
                not is_non_mutating
                and checkpoint_data.get("completed_phases")
                and not already_done
                and task_vars.get("_acm_switchover_resume_recorded") != str(os.getpid())
            ):
                # First executing phase of this process on a resumed checkpoint:
                # replace resume_summary wholesale (parity with Python RunRecord —
                # last resume wins). Later enters in the same process are fenced
                # by the _acm_switchover_resume_recorded fact returned below. The
                # sentinel carries the controller PID so a stale value surviving
                # in a persistent fact cache cannot fence a later process (action
                # plugins run on the controller, so the PID identifies this
                # ansible-playbook invocation).
                record_resume_start_phase(checkpoint_data, phase)
                resume_summary_changed = True
            if (
                (operation_identity_changed or reset_from or resume_summary_changed)
                and backend == CHECKPOINT_BACKEND_FILE
                and not is_non_mutating
            ):
                if operation_identity_changed or resume_summary_changed:
                    checkpoint_data["updated_at"] = datetime.now(timezone.utc).isoformat()
                save_result = self._save_checkpoint(path, checkpoint_data)
                if save_result is not None and save_result.get("failed"):
                    return save_result
            result = {
                "changed": False,
                "checkpoint": checkpoint_data,
                "skipped_phase": already_done,
                "facts": checkpoint_facts(checkpoint_data),
            }
            if hub_identities is not None:
                result["hub_identities"] = hub_identities
            if resume_summary_changed:
                result["ansible_facts"] = {"_acm_switchover_resume_recorded": str(os.getpid())}
            return result

        if is_non_mutating:
            result = {
                "changed": False,
                "checkpoint": checkpoint_data,
            }
            if is_check_mode:
                result["check_mode"] = True
            if execution_mode in {"dry_run", "validate"}:
                result[execution_mode] = True
            return result

        changed = operation_identity_changed or phase_changed
        transition = build_phase_transition(checkpoint_data, phase, status)
        if checkpoint_data.get("completed_phases") != transition["completed_phases"]:
            checkpoint_data["completed_phases"] = transition["completed_phases"]
            changed = True
        if checkpoint_data.get("phase_status") != transition["phase_status"]:
            checkpoint_data["phase_status"] = transition["phase_status"]
            changed = True
        sanitized_operational_data = {key: value for key, value in operational_data.items() if value not in (None, "")}
        current_operational_data = checkpoint_data.setdefault("operational_data", {})
        for key, value in sanitized_operational_data.items():
            if current_operational_data.get(key) != value:
                current_operational_data[key] = value
                changed = True

        if error and status == "fail":
            checkpoint_data.setdefault("errors", []).append({"phase": phase, "error": error})
            changed = True
        if report_ref:
            report_refs = checkpoint_data.setdefault("report_refs", [])
            new_report_ref = {
                "phase": phase,
                "path": report_ref,
                "kind": CHECKPOINT_REPORT_KIND_JSON,
            }
            if new_report_ref not in report_refs:
                report_refs.append(new_report_ref)
                changed = True

        if changed:
            checkpoint_data["updated_at"] = datetime.now(timezone.utc).isoformat()

        if changed and backend == CHECKPOINT_BACKEND_FILE:
            save_result = self._save_checkpoint(path, checkpoint_data)
            if save_result is not None and save_result.get("failed"):
                return save_result

        return {"changed": changed, "checkpoint": checkpoint_data}

    @staticmethod
    def _missing_operation_identity_failure() -> dict:
        return {
            "failed": True,
            "msg": "Checkpoint has no established operation identity; run the preflight identity barrier first.",
        }

    @staticmethod
    def _canonical_established_operation_identity(identity) -> dict | None:
        if not isinstance(identity, Mapping):
            return None
        normalized_identity = normalize_operation_identity(dict(identity))
        canonical_fields = {
            "primary_context",
            "secondary_context",
            "primary_cluster_uid",
            "secondary_cluster_uid",
            "method",
            "activation_method",
            "restore_only",
            "old_hub_action",
            "collection_version",
        }
        if set(normalized_identity) != canonical_fields:
            return None
        if not isinstance(normalized_identity["restore_only"], bool):
            return None
        string_fields = canonical_fields - {"restore_only"}
        if any(not isinstance(normalized_identity[field], str) for field in string_fields):
            return None
        if any(
            not normalized_identity[field].strip()
            for field in (
                "secondary_context",
                "secondary_cluster_uid",
                "method",
                "activation_method",
                "old_hub_action",
            )
        ):
            return None
        if normalized_identity["restore_only"]:
            if normalized_identity["primary_context"] or normalized_identity["primary_cluster_uid"]:
                return None
        elif (
            not normalized_identity["primary_context"].strip() or not normalized_identity["primary_cluster_uid"].strip()
        ):
            return None
        return dict(normalized_identity)

    def _validate_checkpoint_read_only(
        self,
        *,
        checkpoint_data: dict,
        expected_operation_identity: dict,
        has_explicit_reset: bool,
    ) -> dict | None:
        if is_unsafe_legacy_checkpoint(checkpoint_data):
            if has_explicit_reset:
                return None
            return {
                "failed": True,
                "msg": (
                    "Checkpoint schema 1.0 with completed phases is unsafe to resume. "
                    "Use checkpoint.reset or checkpoint.reset_from to start from a safe point."
                ),
            }
        if checkpoint_data.get("schema_version") != SCHEMA_VERSION:
            return None
        operation_identity = checkpoint_data.get("operation_identity")
        if operation_identity is None:
            if checkpoint_data.get("completed_phases") and not has_explicit_reset:
                return {
                    "failed": True,
                    "msg": (
                        "Checkpoint has completed phases but no operation identity — cannot verify hub binding. "
                        "Use checkpoint.reset or checkpoint.reset_from to start a new execution safely."
                    ),
                }
            return None
        if not has_explicit_reset:
            try:
                validate_operation_identity(checkpoint_data, expected_operation_identity)
            except CheckpointIdentityMismatch as exc:
                return {
                    "failed": True,
                    "msg": f"{exc} Use checkpoint.reset or checkpoint.reset_from to start a new execution safely.",
                }
        return None

    def _normalize_checkpoint_data(
        self,
        *,
        checkpoint_data: dict,
        phase: str,
        status: str,
        reset_from: str | None,
        has_explicit_reset: bool,
        expected_operation_identity: dict,
    ) -> tuple[dict, bool]:
        if reset_from and status in {"enter", "reset"}:
            # Only prune when reset_from is still in completed_phases.
            # By invariant, if reset_from is absent, all downstream phases are too
            # (phases are appended in order), so pruning is already complete.
            if reset_from in checkpoint_data.get("completed_phases", []):
                return (
                    self._build_reset_from_checkpoint(checkpoint_data, reset_from, expected_operation_identity),
                    False,
                )

        if is_unsafe_legacy_checkpoint(checkpoint_data):
            if not has_explicit_reset:
                return (
                    {
                        "failed": True,
                        "msg": (
                            "Checkpoint schema 1.0 with completed phases is unsafe to "
                            "resume. Use checkpoint.reset or checkpoint.reset_from to "
                            "start from a safe point."
                        ),
                    },
                    False,
                )
            if status == "enter":
                return (
                    build_checkpoint_record(
                        phase,
                        {},
                        operation_identity=expected_operation_identity,
                    ),
                    False,
                )

        backfilled_operation_identity = False
        if (
            checkpoint_data.get("schema_version") == SCHEMA_VERSION
            and checkpoint_data.get("operation_identity") is None
        ):
            if checkpoint_data.get("completed_phases") and not has_explicit_reset:
                return (
                    {
                        "failed": True,
                        "msg": (
                            "Checkpoint has completed phases but no operation identity — "
                            "cannot verify hub binding. Use checkpoint.reset or "
                            "checkpoint.reset_from to start a new execution safely."
                        ),
                    },
                    False,
                )
            checkpoint_data["operation_identity"] = expected_operation_identity
            backfilled_operation_identity = True

        if checkpoint_data.get("schema_version") == SCHEMA_VERSION and not has_explicit_reset:
            try:
                validate_operation_identity(checkpoint_data, expected_operation_identity)
            except CheckpointIdentityMismatch as exc:
                return (
                    {
                        "failed": True,
                        "msg": (
                            f"{exc} Use checkpoint.reset or checkpoint.reset_from to " "start a new execution safely."
                        ),
                    },
                    False,
                )

        if (
            checkpoint_data.get("schema_version") == SCHEMA_VERSION
            and checkpoint_data.get("operation_identity") is not None
        ):
            normalized_identity = normalize_operation_identity(checkpoint_data["operation_identity"])
            if checkpoint_data["operation_identity"] != normalized_identity:
                checkpoint_data["operation_identity"] = normalized_identity
                backfilled_operation_identity = True

        return checkpoint_data, backfilled_operation_identity

    def _build_reset_from_checkpoint(
        self, checkpoint_data: dict, reset_from: str, expected_operation_identity: dict
    ) -> dict:
        pruned_completed_phases = reset_completed_phases_from(checkpoint_data.get("completed_phases", []), reset_from)

        if checkpoint_data.get("schema_version") != SCHEMA_VERSION:
            rebuilt_checkpoint = build_checkpoint_record(
                reset_from,
                checkpoint_data.get("operational_data") or {},
                operation_identity=expected_operation_identity,
            )
            rebuilt_checkpoint["errors"] = list(checkpoint_data.get("errors", []))
            rebuilt_checkpoint["report_refs"] = list(checkpoint_data.get("report_refs", []))
            rebuilt_checkpoint["completed_phases"] = pruned_completed_phases
            return rebuilt_checkpoint

        normalized_checkpoint = dict(checkpoint_data)
        normalized_checkpoint.setdefault("operational_data", {})
        normalized_checkpoint.setdefault("errors", [])
        normalized_checkpoint.setdefault("report_refs", [])
        normalized_checkpoint["schema_version"] = SCHEMA_VERSION
        normalized_checkpoint["operation_identity"] = expected_operation_identity
        normalized_checkpoint["completed_phases"] = pruned_completed_phases
        normalized_checkpoint["updated_at"] = datetime.now(timezone.utc).isoformat()
        return normalized_checkpoint

    def _load_checkpoint(self, path: str, *, quarantine_corrupt: bool = True) -> dict:
        if not os.path.exists(path):
            return build_checkpoint_record("", {})
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except json.JSONDecodeError as e:
            if quarantine_corrupt:
                quarantine_path = self._build_corrupt_checkpoint_path(path)
                try:
                    os.replace(path, quarantine_path)
                except OSError as rename_error:
                    return {
                        "failed": True,
                        "msg": (
                            f"Checkpoint file '{path}' is corrupted (invalid JSON): {e}. "
                            f"Unable to quarantine it at '{quarantine_path}': {rename_error}."
                        ),
                    }
                return {
                    "failed": True,
                    "msg": (
                        f"Checkpoint file '{path}' is corrupted (invalid JSON): {e}. "
                        f"It was quarantined to '{quarantine_path}'."
                    ),
                }
            return {
                "failed": True,
                "msg": f"Checkpoint file '{path}' is corrupted (invalid JSON): {e}. "
                f"Delete or repair the file to resume.",
            }
        except OSError as e:
            return {
                "failed": True,
                "msg": f"Cannot read checkpoint file '{path}': {e}.",
            }

    def _build_corrupt_checkpoint_path(self, path: str) -> str:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return f"{path}.corrupt-{timestamp}"

    def _save_checkpoint(self, path: str, data: dict) -> dict | None:
        dir_path = os.path.dirname(path)
        temp_path = self._build_temp_checkpoint_path(path)
        try:
            if dir_path:
                os.makedirs(dir_path, exist_ok=True)
            with open(temp_path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(temp_path, path)
            self._fsync_parent_directory(path)
        except OSError as e:
            if os.path.exists(temp_path):
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass
            return {
                "failed": True,
                "msg": f"Cannot write checkpoint file '{path}': {e}.",
            }
        return None

    def _fsync_parent_directory(self, path: str) -> None:
        dir_path = os.path.dirname(path) or "."
        dir_fd = None
        try:
            dir_fd = os.open(dir_path, os.O_RDONLY)
            os.fsync(dir_fd)
        except OSError:
            # Some platforms/filesystems do not support opening or fsyncing
            # directories. The checkpoint file itself was already fsynced before
            # replace; directory fsync is a best-effort durability improvement.
            return
        finally:
            if dir_fd is not None:
                try:
                    os.close(dir_fd)
                except OSError:
                    pass

    def _build_temp_checkpoint_path(self, path: str) -> str:
        dir_path = os.path.dirname(path) or "."
        file_name = os.path.basename(path)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        return os.path.join(dir_path, f".{file_name}.tmp-{timestamp}")
