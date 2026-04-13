# SPDX-License-Identifier: MIT
"""Runtime helpers and ActionModule for the checkpoint_phase action plugin."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from ansible.plugins.action import ActionBase

from ansible_collections.tomazb.acm_switchover.plugins.module_utils.checkpoint import (
    build_checkpoint_record,
    should_resume_phase,
)


def build_phase_transition(checkpoint: dict, phase: str, status: str) -> dict:
    """Return a *partial* update dict reflecting a phase transition.

    Appends *phase* to ``completed_phases`` when *status* is ``"pass"`` and the
    phase has not already been recorded.

    .. warning::
        This returns only ``completed_phases`` and ``phase_status``. Callers are
        responsible for merging this into the full checkpoint record. Replacing
        the checkpoint wholesale will silently drop ``operational_data``,
        ``errors``, ``report_refs``, and timestamp fields.
    """
    completed = list(checkpoint.get("completed_phases", []))
    if status == "pass" and phase not in completed:
        completed.append(phase)
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
        status (str): one of ``enter``, ``pass``, ``fail``
        error (str, optional): error message to record on ``status: fail``
        report_ref (str, optional): artifact path to record on ``status: pass``
    """

    TRANSFERS_FILES = False
    INITIAL_PHASE = "preflight"

    def run(self, tmp=None, task_vars=None):
        super().run(tmp, task_vars)

        phase = self._task.args.get("phase", "")
        checkpoint_config = self._task.args.get("checkpoint", {})
        status = self._task.args.get("status", "enter")
        error = self._task.args.get("error")
        report_ref = self._task.args.get("report_ref")

        backend = checkpoint_config.get("backend", "file")
        path = checkpoint_config.get("path", ".state/checkpoint.json")

        if status not in {"enter", "pass", "fail"}:
            return {
                "failed": True,
                "msg": f"Invalid checkpoint status '{status}'. Expected one of: enter, pass, fail.",
            }

        if not phase:
            return {
                "failed": True,
                "msg": "Missing required checkpoint phase.",
            }

        if backend not in {"file"}:
            return {
                "failed": True,
                "msg": f"Invalid checkpoint backend '{backend}'. Expected: file.",
            }

        reset = bool(checkpoint_config.get("reset", False))
        should_reset = reset and status == "enter" and phase == self.INITIAL_PHASE
        checkpoint_data = build_checkpoint_record(phase, {}) if should_reset else self._load_checkpoint(path)
        if checkpoint_data.get("failed"):
            return checkpoint_data

        checkpoint_data["phase"] = phase

        if should_reset and backend == "file":
            save_result = self._save_checkpoint(path, checkpoint_data)
            if save_result is not None and save_result.get("failed"):
                return save_result

        if status == "enter":
            already_done = not should_resume_phase(checkpoint_data, phase)
            return {"changed": False, "checkpoint": checkpoint_data, "skipped_phase": already_done}

        transition = build_phase_transition(checkpoint_data, phase, status)
        checkpoint_data["completed_phases"] = transition["completed_phases"]
        checkpoint_data["phase_status"] = transition["phase_status"]
        checkpoint_data["updated_at"] = datetime.now(timezone.utc).isoformat()

        if error:
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

    def _load_checkpoint(self, path: str) -> dict:
        if not os.path.exists(path):
            return build_checkpoint_record("", {})
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except json.JSONDecodeError as e:
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

    def _save_checkpoint(self, path: str, data: dict) -> dict | None:
        dir_path = os.path.dirname(path)
        try:
            if dir_path:
                os.makedirs(dir_path, exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2)
        except OSError as e:
            return {
                "failed": True,
                "msg": f"Cannot write checkpoint file '{path}': {e}.",
            }
        return None
