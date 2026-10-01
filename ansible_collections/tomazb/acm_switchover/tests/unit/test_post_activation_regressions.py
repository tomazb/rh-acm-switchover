"""Tests for post_activation control-flow regression fixes.

Covers:
- Cluster polling waits for ALL clusters (Available + Joined)
- Klusterlet remediation triggers re-verification
- Negative min_managed_clusters is rejected
"""

import pathlib

import pytest
import yaml

from ansible_collections.tomazb.acm_switchover.plugins.module_utils.constants import (
    NO_MANAGED_CLUSTERS_PENDING_REASON,
)
from ansible_collections.tomazb.acm_switchover.plugins.module_utils.migration_post_activation import (
    managed_cluster_readiness,
)
from ansible_collections.tomazb.acm_switchover.plugins.module_utils.validation import (
    ValidationError,
    validate_operation_inputs,
)
from ansible_collections.tomazb.acm_switchover.plugins.modules.acm_cluster_verify import (
    summarize_cluster_group,
)

ROLES_DIR = pathlib.Path(__file__).resolve().parents[2] / "roles"
POST_ACTIVATION_TASKS = ROLES_DIR / "post_activation" / "tasks"
COMMON_TASKS = ROLES_DIR / "common" / "tasks"


_TRUE_AVAILABLE = {"type": "ManagedClusterConditionAvailable", "status": "True"}


def _load_yaml(path: pathlib.Path) -> list[dict]:
    return yaml.safe_load(path.read_text())


# ── Issue 1: Cluster polling checks both Available AND Joined ──


class TestVerifyManagedClustersPolling:
    """Structural tests for verify_managed_clusters.yml polling semantics."""

    @pytest.fixture(autouse=True)
    def _load_tasks(self):
        self.content = (POST_ACTIVATION_TASKS / "verify_managed_clusters.yml").read_text()
        self.expectation_content = (COMMON_TASKS / "resolve_managed_cluster_expectation.yml").read_text()

    def _poll(self):
        tasks = yaml.safe_load((POST_ACTIVATION_TASKS / "poll_managed_cluster_readiness.yml").read_text())
        return next(task for task in tasks if "tomazb.acm_switchover.acm_migration_evidence" in task)

    def test_readiness_is_the_mirrored_available_and_joined_decision(self):
        """R4-04 Task 9: each poll decides through the module shared with the Python CLI by parity vectors."""
        poll = self._poll()["tomazb.acm_switchover.acm_migration_evidence"]
        assert poll["operation"] == "managed_cluster_readiness"
        ready = managed_cluster_readiness(
            items=[{"metadata": {"name": "a"}, "status": {"conditions": [_TRUE_AVAILABLE]}}],
            expected_names=[],
            min_managed_clusters=0,
            names_waived=False,
        )
        assert ready["code"] == "clusters_not_ready" and ready["pending"] == ["a"]

    def test_poll_uses_effective_expected_count_and_names(self):
        """Polling must wait for the effective expected count and every expected name."""
        inputs = self._poll()["tomazb.acm_switchover.acm_migration_evidence"]["inputs"]
        assert "_acm_post_activation_min_managed_clusters" in inputs["min_managed_clusters"]
        assert "_acm_post_activation_expected_managed_cluster_names" in inputs["expected_names"]
        tasks = yaml.safe_load(self.content)
        loop = next(
            task for task in tasks if task.get("ansible.builtin.include_tasks") == "poll_managed_cluster_readiness.yml"
        )
        assert loop["when"] == "_acm_mc_readiness_state != 'ready'"

    def test_an_empty_inventory_never_exits_the_wait(self):
        """An empty inventory means the hub reconciler has not caught up; it is never ready.

        Mirrors Python: `if not managed_clusters: return False, 'no ManagedClusters found'`.
        """
        decision = managed_cluster_readiness(items=[], expected_names=[], min_managed_clusters=0, names_waived=False)
        assert decision["ready"] is False and decision["code"] == "inventory_empty"

    def test_status_summary_receives_only_non_local_clusters(self):
        """local-cluster must not count toward managed cluster readiness verification."""
        tasks = yaml.safe_load(self.content)
        summary_task = next(task for task in tasks if "tomazb.acm_switchover.acm_managedcluster_status" in task)
        clusters_arg = str(summary_task["tomazb.acm_switchover.acm_managedcluster_status"]["clusters"])

        assert "local-cluster" in clusters_arg
        assert "selectattr('metadata.name', 'ne', 'local-cluster')" in clusters_arg

    def test_cluster_verify_result_is_the_readiness_decision(self):
        """The published result keeps its contract and is derived from the last readiness decision."""
        tasks = yaml.safe_load(self.content)
        publish = next(
            task
            for task in tasks
            if "acm_switchover_cluster_verify_result" in (task.get("ansible.builtin.set_fact") or {})
        )
        expression = publish["ansible.builtin.set_fact"]["acm_switchover_cluster_verify_result"]
        for key in ("'passed': _acm_mc_readiness.ready", "'total'", "'missing'", "'pending'"):
            assert key in expression

    def test_verification_results_use_prefixed_facts_with_compatibility_aliases(self):
        """Public post_activation verification facts should use acm_switchover_ names."""
        tasks = yaml.safe_load(self.content)
        summary_task = next(task for task in tasks if "tomazb.acm_switchover.acm_managedcluster_status" in task)
        alias_tasks = [task for task in tasks if "ansible.builtin.set_fact" in task]

        assert summary_task["register"] == "acm_switchover_cluster_status_result"
        assert any("acm_switchover_cluster_verify_result" in task["ansible.builtin.set_fact"] for task in alias_tasks)
        assert any(
            task["ansible.builtin.set_fact"].get("cluster_status_result")
            == "{{ acm_switchover_cluster_status_result }}"
            for task in alias_tasks
        )
        assert any(
            task["ansible.builtin.set_fact"].get("acm_cluster_verify_result")
            == "{{ acm_switchover_cluster_verify_result }}"
            for task in alias_tasks
        )

    def test_preflight_zero_cluster_compatibility_excludes_restore_only(self):
        """A preflight-derived zero count may allow non-restore switchovers, but not restore-only."""
        assert "not (acm_switchover_operation.restore_only | default(false) | bool)" in self.expectation_content
        assert "acm_switchover_expected_managed_cluster_count is defined" in self.expectation_content

    def test_explicit_min_zero_allows_empty_hub_like_python_cli(self):
        """Role-level min_managed_clusters=0 must be the CLI-equivalent empty-target opt-in."""
        assert "acm_switchover_operation.min_managed_clusters is defined" in self.expectation_content
        assert "(acm_switchover_operation.min_managed_clusters | int) == 0" in self.expectation_content


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
        assert "remediation_attempted" in when_str, "Re-verify 'when' must check a remediation-attempted flag"

    def test_initial_managed_cluster_wait_is_soft_failed(self):
        """The first cluster wait must allow klusterlet remediation to run before final hard failure."""
        verify_mc_indices = self._find_task_indices("verify_managed_clusters.yml")
        initial_task = self.block_tasks[verify_mc_indices[0]]
        reverify_task = self.block_tasks[verify_mc_indices[-1]]

        assert "acm_switchover_managed_cluster_wait_soft_fail" in str(initial_task)
        assert "acm_switchover_managed_cluster_wait_soft_fail" not in str(reverify_task)
        verify_tasks = _load_yaml(POST_ACTIVATION_TASKS / "verify_managed_clusters.yml")
        (hard_fail,) = [task for task in verify_tasks if "ansible.builtin.fail" in task]
        assert "acm_switchover_managed_cluster_wait_soft_fail" in str(hard_fail["when"])

    def test_verify_klusterlet_sets_remediation_flag(self):
        """verify_klusterlet.yml must set a remediation-attempted flag."""
        content = (POST_ACTIVATION_TASKS / "verify_klusterlet.yml").read_text()
        assert (
            "_klusterlet_remediation_attempted" in content
        ), "verify_klusterlet.yml must set _klusterlet_remediation_attempted flag"

    def test_main_consumes_prefixed_post_activation_result_facts(self):
        """The role result contract must consume namespaced public fact names."""
        content = (POST_ACTIVATION_TASKS / "main.yml").read_text()

        assert "acm_switchover_cluster_verify_result" in content
        assert "acm_switchover_observability_check_result" in content
        assert "status: \"{{ 'pass' if acm_cluster_verify_result.passed else 'fail' }}\"" not in content


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

    def test_cluster_verify_zero_with_no_clusters_fails_without_explicit_allow(self):
        """min_managed_clusters=0 alone must not silently allow an empty restore target."""
        result = summarize_cluster_group([], min_managed_clusters=0)
        assert result["passed"] is False
        assert NO_MANAGED_CLUSTERS_PENDING_REASON in result["pending"]

    def test_cluster_verify_zero_with_pending_clusters_fails(self):
        """min_managed_clusters=0 with pending clusters should fail."""
        result = summarize_cluster_group(
            [{"name": "c1", "joined": False, "available": False}],
            min_managed_clusters=0,
        )
        assert result["passed"] is False
        assert "c1" in result["pending"]

    def test_cluster_verify_enforces_expected_names(self):
        """Derived expected names must be missing-aware, not only count-aware."""
        result = summarize_cluster_group(
            [{"name": "cluster-a", "joined": True, "available": True}],
            min_managed_clusters=2,
            expected_names=["cluster-a", "cluster-b"],
        )
        assert result["passed"] is False
        assert "cluster-b" in result["missing"]

    def test_cluster_verify_zero_can_disable_expected_names_when_explicitly_allowed(self):
        """allow_zero_managed_clusters is the explicit opt-in for empty restore targets."""
        result = summarize_cluster_group(
            [],
            min_managed_clusters=0,
            expected_names=[],
            allow_zero_managed_clusters=True,
        )
        assert result["passed"] is True
