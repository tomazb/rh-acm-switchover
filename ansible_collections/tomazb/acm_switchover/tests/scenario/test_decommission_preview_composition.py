"""Integrated finalization check mode: the composed path reaches decommission and persists nothing.

Unit tests cover the role and the standalone playbook. This scenario drives the
real finalization -> handle_old_hub -> decommission include under native check mode.
"""

from __future__ import annotations

import pathlib
import sys

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[5]
_UNIT_TESTS_DIR = _REPO_ROOT / "ansible_collections/tomazb/acm_switchover/tests/unit"
if str(_UNIT_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_UNIT_TESTS_DIR))

from test_decommission_check_mode import (  # noqa: E402
    _GUARDED_DELETE_TASKS,
    _assert_guarded_deletes_ran_without_deleting,
    _assert_prediction_is_separate,
    _assert_preview_persists_nothing,
    _ran,
)
from test_decommission_role_contracts import _HANDLE_OLD_HUB_TASK_NAMES, run_decommission_role  # noqa: E402


def test_integrated_finalization_check_mode_reaches_decommission_and_persists_nothing():
    result = run_decommission_role(integrated_finalization=True, check_mode=True)
    ran = {task["name"] for task in result["tasks"] if not task["skipped"]}
    for name in _HANDLE_OLD_HUB_TASK_NAMES:
        assert name in ran, f"{name} did not run; the integrated path was not exercised"
    for name in _GUARDED_DELETE_TASKS:
        assert _ran(result, name), f"{name} did not run inside the integrated include"
    _assert_guarded_deletes_ran_without_deleting(result)
    _assert_preview_persists_nothing(result)
    _assert_prediction_is_separate(result, mode="execute")
