"""Guards from the 2026-08-03 parity audit (findings H1 and H10).

H1: a resumed run whose state carries no managed-cluster expectation must
fail closed instead of silently disabling all enforcement.
H10: --dry-run must never destroy a real in-progress state file, even when
the invocation's contexts differ from the file's (ensure_contexts reset).
"""

import argparse

import pytest

from acm_switchover import _resolve_managed_cluster_expectation
from lib.exceptions import SwitchoverError
from lib.run_record import RunRecord
from lib.utils import StateManager

pytestmark = pytest.mark.unit


def _args(**overrides):
    defaults = {
        "min_managed_clusters": None,
        "dry_run": False,
        "argocd_resume_only": False,
        "decommission": False,
        "primary_context": "hub-a",
        "secondary_context": "hub-b",
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


class TestExpectationFailsClosed:
    def test_missing_expectation_record_raises(self, tmp_path):
        state = StateManager(str(tmp_path / "switchover-x.json"))

        with pytest.raises(SwitchoverError) as exc_info:
            _resolve_managed_cluster_expectation(_args(), state)

        message = str(exc_info.value)
        assert "--min-managed-clusters" in message
        assert "preflight" in message

    def test_recorded_expectation_still_resolves(self, tmp_path):
        state = StateManager(str(tmp_path / "switchover-x.json"))
        RunRecord(state).record_managed_cluster_expectation(names=["c1", "c2"], count=2, mode="derived_from_preflight")

        assert _resolve_managed_cluster_expectation(_args(), state) == (2, ["c1", "c2"], True)

    def test_restore_only_mode_still_resolves(self, tmp_path):
        state = StateManager(str(tmp_path / "switchover-x.json"))
        RunRecord(state).record_managed_cluster_expectation(names=[], count=0, mode="restore_only")

        assert _resolve_managed_cluster_expectation(_args(), state) == (1, [], False)

    def test_explicit_min_overrides_missing_record(self, tmp_path):
        state = StateManager(str(tmp_path / "switchover-x.json"))

        assert _resolve_managed_cluster_expectation(_args(min_managed_clusters=3), state) == (3, [], False)
        assert _resolve_managed_cluster_expectation(_args(min_managed_clusters=0), state) == (0, [], False)
