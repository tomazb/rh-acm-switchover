# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later
"""Runtime helpers for the checkpoint_phase action plugin."""

from __future__ import annotations


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
