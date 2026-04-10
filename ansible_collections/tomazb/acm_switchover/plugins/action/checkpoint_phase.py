# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later
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

    def run(self, tmp=None, task_vars=None):
        super().run(tmp, task_vars)

        phase = self._task.args.get("phase", "")
        checkpoint_config = self._task.args.get("checkpoint", {})
        status = self._task.args.get("status", "enter")
        error = self._task.args.get("error")
        report_ref = self._task.args.get("report_ref")

        backend = checkpoint_config.get("backend", "file")
        path = checkpoint_config.get("path", ".state/checkpoint.json")

        checkpoint_data = (
            self._load_checkpoint(path) if backend == "file" else build_checkpoint_record(phase, {})
        )

        if status == "enter":
            already_done = not should_resume_phase(checkpoint_data, phase)
            return {"changed": False, "checkpoint": checkpoint_data, "skipped_phase": already_done}

        transition = build_phase_transition(checkpoint_data, phase, status)
        checkpoint_data["completed_phases"] = transition["completed_phases"]
        checkpoint_data["updated_at"] = datetime.now(timezone.utc).isoformat()

        if error:
            checkpoint_data.setdefault("errors", []).append({"phase": phase, "error": error})
        if report_ref:
            checkpoint_data.setdefault("report_refs", []).append(
                {"phase": phase, "path": report_ref, "kind": "json-report"}
            )

        if backend == "file":
            self._save_checkpoint(path, checkpoint_data)

        return {"changed": True, "checkpoint": checkpoint_data}

    def _load_checkpoint(self, path: str) -> dict:
        if os.path.exists(path):
            with open(path) as fh:
                return json.load(fh)
        return build_checkpoint_record("", {})

    def _save_checkpoint(self, path: str, data: dict) -> None:
        dir_path = os.path.dirname(path)
        if dir_path:
            os.makedirs(dir_path, exist_ok=True)
        with open(path, "w") as fh:
            json.dump(data, fh, indent=2)
