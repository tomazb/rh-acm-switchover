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


def _load_yaml(path: pathlib.Path) -> list[dict]:
    return yaml.safe_load(path.read_text())


# ── Issue 1: Cluster polling checks both Available AND Joined ──


class TestVerifyManagedClustersPolling:
    """Structural tests for verify_managed_clusters.yml polling semantics."""

    @pytest.fixture(autouse=True)
    def _load_tasks(self):
        self.content = (POST_ACTIVATION_TASKS / "verify_managed_clusters.yml").read_text()
        self.expectation_content = (COMMON_TASKS / "resolve_managed_cluster_expectation.yml").read_text()

    def test_until_checks_available_condition(self):
        assert "ManagedClusterConditionAvailable" in self.content

    def test_until_checks_joined_condition(self):
        """Polling must check ManagedClusterJoined, not just Available."""
        assert "ManagedClusterJoined" in self.content

    def test_until_uses_effective_expected_count_and_totality(self):
        """Polling must wait for the effective expected count, then require all observed clusters ready."""
        tasks = yaml.safe_load(self.content)
        poll_task = None
        for task in tasks:
            if task.get("retries") and task.get("until"):
                poll_task = task
                break
        assert poll_task is not None, "Must have a polling task with retries + until"
        until_expr = poll_task["until"]
        assert "_acm_post_activation_min_managed_clusters" in until_expr
        assert ">= (_acm_post_activation_min_managed_clusters | int)" in until_expr
        assert "ManagedClusterConditionAvailable" in until_expr
        assert "ManagedClusterJoined" in until_expr

    def test_until_waits_for_expected_names_before_exiting(self):
        """Derived expected ManagedCluster names must keep polling until every expected name is visible."""
        tasks = yaml.safe_load(self.content)
        poll_task = next(task for task in tasks if task.get("retries") and task.get("until"))
        until_expr = poll_task["until"]

        assert "_acm_post_activation_expected_managed_cluster_names" in until_expr
        assert "difference(" in until_expr

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
        summary_task = next(task for task in tasks if "tomazb.acm_switchover.acm_managedcluster_status" in task)
        clusters_arg = str(summary_task["tomazb.acm_switchover.acm_managedcluster_status"]["clusters"])

        assert "local-cluster" in clusters_arg
        assert "selectattr('metadata.name', 'ne', 'local-cluster')" in clusters_arg

    def test_cluster_verify_uses_derived_expected_count_by_default(self):
        """Omitted min_managed_clusters should use the preflight-derived expected count."""
        tasks = yaml.safe_load(self.content)
        verify_task = next(task for task in tasks if "tomazb.acm_switchover.acm_cluster_verify" in task)
        module_args = verify_task["tomazb.acm_switchover.acm_cluster_verify"]

        assert "_acm_post_activation_min_managed_clusters" in str(module_args["min_managed_clusters"])
        assert "_acm_post_activation_expected_managed_cluster_names" in str(module_args["expected_names"])

    def test_verification_results_use_prefixed_facts_with_compatibility_aliases(self):
        """Public post_activation verification facts should use acm_switchover_ names."""
        tasks = yaml.safe_load(self.content)
        summary_task = next(task for task in tasks if "tomazb.acm_switchover.acm_managedcluster_status" in task)
        verify_task = next(task for task in tasks if "tomazb.acm_switchover.acm_cluster_verify" in task)
        alias_tasks = [task for task in tasks if "ansible.builtin.set_fact" in task]

        assert summary_task["register"] == "acm_switchover_cluster_status_result"
        assert verify_task["register"] == "acm_switchover_cluster_verify_result"
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
        verify_text = (POST_ACTIVATION_TASKS / "verify_managed_clusters.yml").read_text()
        assert "failed_when" in verify_text
        assert "acm_switchover_managed_cluster_wait_soft_fail" in verify_text

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
