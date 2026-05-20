# SPDX-License-Identifier: MIT
"""Runtime helpers and ActionModule for the checkpoint_phase action plugin."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from ansible.plugins.action import ActionBase

from ansible_collections.tomazb.acm_switchover.plugins.module_utils.checkpoint import (
    KNOWN_PHASES,
    SCHEMA_VERSION,
    CheckpointIdentityMismatch,
    build_checkpoint_record,
    build_operation_identity,
    is_unsafe_legacy_checkpoint,
    reset_completed_phases_from,
    should_resume_phase,
    validate_operation_identity,
)
from ansible_collections.tomazb.acm_switchover.plugins.module_utils.validation import (
    ValidationError,
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

        phase = self._task.args.get("phase", "")
        checkpoint_config = self._task.args.get("checkpoint", {})
        status = self._task.args.get("status", "enter")
        error = self._task.args.get("error")
        report_ref = self._task.args.get("report_ref")
        operational_data = self._task.args.get("operational_data") or {}
        execution = task_vars.get("acm_switchover_execution") or {}
        execution_mode = execution.get("mode", "dry_run")
        is_check_mode = (
            task_vars.get("ansible_check_mode") is True or getattr(self._play_context, "check_mode", False) is True
        )
        is_non_mutating = is_check_mode or execution_mode in {"dry_run", "validate"}

        backend = checkpoint_config.get("backend", "file")
        path = checkpoint_config.get("path", ".state/checkpoint.json")
        reset_from = checkpoint_config.get("reset_from")

        if status not in {"enter", "pass", "fail", "reset"}:
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

        if backend not in {"file"}:
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

        expected_operation_identity = build_operation_identity(
            hubs=task_vars.get("acm_switchover_hubs") or {},
            operation=task_vars.get("acm_switchover_operation") or {},
            collection_version=task_vars.get("acm_switchover_collection_version"),
            hub_identities=task_vars.get("acm_switchover_hub_identities") or {},
        )
        reset = bool(checkpoint_config.get("reset", False))
        has_explicit_reset = reset or bool(reset_from)
        should_reset = reset and status == "enter" and phase == self.INITIAL_PHASE
        checkpoint_data = (
            build_checkpoint_record(
                phase,
                {},
                operation_identity=expected_operation_identity,
            )
            if should_reset
            else self._load_checkpoint(path, quarantine_corrupt=not is_non_mutating)
        )
        if checkpoint_data.get("failed"):
            return checkpoint_data

        checkpoint_data, backfilled_operation_identity = self._normalize_checkpoint_data(
            checkpoint_data=checkpoint_data,
            phase=phase,
            status=status,
            reset_from=reset_from,
            has_explicit_reset=has_explicit_reset,
            expected_operation_identity=expected_operation_identity,
        )
        if checkpoint_data.get("failed"):
            return checkpoint_data

        checkpoint_data["phase"] = phase

        if should_reset and backend == "file" and not is_non_mutating:
            save_result = self._save_checkpoint(path, checkpoint_data)
            if save_result is not None and save_result.get("failed"):
                return save_result

        if status == "enter":
            if (backfilled_operation_identity or reset_from) and backend == "file" and not is_non_mutating:
                save_result = self._save_checkpoint(path, checkpoint_data)
                if save_result is not None and save_result.get("failed"):
                    return save_result
            already_done = False if execution_mode == "validate" else not should_resume_phase(checkpoint_data, phase)
            return {
                "changed": False,
                "checkpoint": checkpoint_data,
                "skipped_phase": already_done,
            }

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

        transition = build_phase_transition(checkpoint_data, phase, status)
        checkpoint_data["completed_phases"] = transition["completed_phases"]
        checkpoint_data["phase_status"] = transition["phase_status"]
        checkpoint_data["updated_at"] = datetime.now(timezone.utc).isoformat()
        sanitized_operational_data = {key: value for key, value in operational_data.items() if value not in (None, "")}
        checkpoint_data.setdefault("operational_data", {}).update(sanitized_operational_data)

        if error and status == "fail":
            checkpoint_data.setdefault("errors", []).append({"phase": phase, "error": error})
        if report_ref:
            checkpoint_data.setdefault("report_refs", []).append(
                {"phase": phase, "path": report_ref, "kind": "json-report"}
            )

        if backend == "file":
            save_result = self._save_checkpoint(path, checkpoint_data)
            if save_result is not None and save_result.get("failed"):
                return save_result

        return {"changed": True, "checkpoint": checkpoint_data}

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
