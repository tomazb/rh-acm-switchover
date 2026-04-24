"""Tests for post_activation control-flow regression fixes.

Covers:
- Cluster polling waits for ALL clusters (Available + Joined)
- Klusterlet remediation triggers re-verification
- Negative min_managed_clusters is rejected
"""

import pathlib

import pytest
import yaml

from ansible_collections.tomazb.acm_switchover.plugins.module_utils.validation import (
    ValidationError,
    validate_operation_inputs,
)
from ansible_collections.tomazb.acm_switchover.plugins.modules.acm_cluster_verify import (
    summarize_cluster_group,
)

ROLES_DIR = pathlib.Path(__file__).resolve().parents[2] / "roles"
POST_ACTIVATION_TASKS = ROLES_DIR / "post_activation" / "tasks"


def _load_yaml(path: pathlib.Path) -> list[dict]:
    return yaml.safe_load(path.read_text())


# ── Issue 1: Cluster polling checks both Available AND Joined ──


class TestVerifyManagedClustersPolling:
    """Structural tests for verify_managed_clusters.yml polling semantics."""

    @pytest.fixture(autouse=True)
    def _load_tasks(self):
        self.content = (
            POST_ACTIVATION_TASKS / "verify_managed_clusters.yml"
        ).read_text()

    def test_until_checks_available_condition(self):
        assert "ManagedClusterConditionAvailable" in self.content

    def test_until_checks_joined_condition(self):
        """Polling must check ManagedClusterJoined, not just Available."""
        assert "ManagedClusterJoined" in self.content

    def test_until_uses_totality_not_threshold(self):
        """Polling must compare count == total (totality), not count >= threshold."""
        tasks = yaml.safe_load(self.content)
        poll_task = None
        for task in tasks:
            if task.get("retries") and task.get("until"):
                poll_task = task
                break
        assert poll_task is not None, "Must have a polling task with retries + until"
        until_expr = poll_task["until"]
        assert "min_managed_clusters" not in until_expr, (
            "Polling until clause should not use min_managed_clusters threshold — "
            "it should wait for ALL clusters like the Python CLI"
        )

    def test_until_requires_non_empty_resources_before_exiting(self):
        """Polling must NOT exit when resources list is empty.

        An empty resources list means no ManagedClusters found yet — the hub
        reconciler hasn't caught up.  Exiting early (0 == 0 totality check) would
        miss the actual wait, replicating the original short-circuit bug.
        Mirrors Python: `if not managed_clusters: return False, 'no ManagedClusters found'`.
        """
        tasks = yaml.safe_load(self.content)
        poll_task = None
        for task in tasks:
            if task.get("retries") and task.get("until"):
                poll_task = task
                break
        assert poll_task is not None, "Must have a polling task with retries + until"
        until_expr = poll_task["until"]
        assert "| length) > 0" in until_expr, (
            "until clause must guard resources list non-empty before any exit branch — "
            "prevents 0 == 0 totality from trivially passing on empty resource list"
        )

    def test_status_summary_receives_only_non_local_clusters(self):
        """local-cluster must not count toward managed cluster readiness verification."""
        tasks = yaml.safe_load(self.content)
        summary_task = next(
            task
            for task in tasks
            if "tomazb.acm_switchover.acm_managedcluster_status" in task
        )
        clusters_arg = str(
            summary_task["tomazb.acm_switchover.acm_managedcluster_status"]["clusters"]
        )

        assert "local-cluster" in clusters_arg
        assert "selectattr('metadata.name', 'ne', 'local-cluster')" in clusters_arg


# ── Issue 2: Re-verification after klusterlet remediation ──


class TestKlusterletReverification:
    """Structural tests for post_activation main.yml klusterlet re-verify flow."""

    @pytest.fixture(autouse=True)
    def _load_tasks(self):
        self.tasks = _load_yaml(POST_ACTIVATION_TASKS / "main.yml")
        # Flatten: the block tasks are nested under "block:" in the second item
        self.block_tasks = []
        for item in self.tasks:
            block = item.get("block")
            if block:
                self.block_tasks = block
                break

    def _find_task_indices(self, substring: str) -> list[int]:
        """Find indices of block tasks whose include_tasks matches substring."""
        return [
            i
            for i, task in enumerate(self.block_tasks)
            if substring in str(task.get("ansible.builtin.include_tasks", ""))
        ]

    def test_verify_managed_clusters_runs_after_klusterlet(self):
        """main.yml must re-include verify_managed_clusters.yml after verify_klusterlet.yml."""
        verify_mc_indices = self._find_task_indices("verify_managed_clusters.yml")
        klusterlet_indices = self._find_task_indices("verify_klusterlet.yml")

        assert len(verify_mc_indices) >= 2, (
            "verify_managed_clusters.yml must be included at least twice — "
            "once for initial check and once for post-remediation re-verify"
        )
        assert klusterlet_indices, "verify_klusterlet.yml must be included"
        assert verify_mc_indices[-1] > klusterlet_indices[-1], (
            "The last verify_managed_clusters.yml inclusion must come AFTER "
            "verify_klusterlet.yml for post-remediation re-verification"
        )

    def test_reverify_is_conditional_on_remediation_flag(self):
        """Re-verification must be gated on a remediation-attempted flag."""
        verify_mc_indices = self._find_task_indices("verify_managed_clusters.yml")
        assert len(verify_mc_indices) >= 2
        # The second (re-verify) inclusion should have a 'when' condition
        reverify_task = self.block_tasks[verify_mc_indices[-1]]
        when = reverify_task.get("when")
        assert when is not None, "Re-verify task must have a 'when' guard"
        when_str = str(when)
        assert (
            "remediation_attempted" in when_str
        ), "Re-verify 'when' must check a remediation-attempted flag"

    def test_verify_klusterlet_sets_remediation_flag(self):
        """verify_klusterlet.yml must set a remediation-attempted flag."""
        content = (POST_ACTIVATION_TASKS / "verify_klusterlet.yml").read_text()
        assert (
            "_klusterlet_remediation_attempted" in content
        ), "verify_klusterlet.yml must set _klusterlet_remediation_attempted flag"


# ── Issue 4: Negative min_managed_clusters rejection ──


class TestNegativeMinManagedClusters:
    """Tests for rejecting negative min_managed_clusters values."""

    def test_validation_rejects_negative(self):
        """validate_operation_inputs must reject negative min_managed_clusters."""
        with pytest.raises(ValidationError, match="non-negative"):
            validate_operation_inputs(
                operation={"min_managed_clusters": -1, "method": "passive"},
                features={},
            )

    def test_validation_rejects_negative_string(self):
        """validate_operation_inputs must reject negative value even as string."""
        with pytest.raises(ValidationError, match="non-negative"):
            validate_operation_inputs(
                operation={"min_managed_clusters": "-3", "method": "passive"},
                features={},
            )

    def test_validation_accepts_zero(self):
        """validate_operation_inputs must accept zero."""
        result = validate_operation_inputs(
            operation={"min_managed_clusters": 0, "method": "passive"},
            features={},
        )
        assert result["method"] == "passive"

    def test_validation_accepts_positive(self):
        """validate_operation_inputs must accept positive integers."""
        result = validate_operation_inputs(
            operation={"min_managed_clusters": 5, "method": "passive"},
            features={},
        )
        assert result["method"] == "passive"

    def test_cluster_verify_rejects_negative(self):
        """summarize_cluster_group must reject negative min_managed_clusters."""
        with pytest.raises(ValueError, match="non-negative"):
            summarize_cluster_group(
                [{"name": "c1", "joined": True, "available": True}],
                min_managed_clusters=-1,
            )

    def test_cluster_verify_zero_with_no_clusters_passes(self):
        """min_managed_clusters=0 with empty list should pass (no pending)."""
        result = summarize_cluster_group([], min_managed_clusters=0)
        assert result["passed"] is True

    def test_cluster_verify_zero_with_pending_clusters_fails(self):
        """min_managed_clusters=0 with pending clusters should fail."""
        result = summarize_cluster_group(
            [{"name": "c1", "joined": False, "available": False}],
            min_managed_clusters=0,
        )
        assert result["passed"] is False
        assert "c1" in result["pending"]
