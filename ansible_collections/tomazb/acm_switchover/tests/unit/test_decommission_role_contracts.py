"""YAML contract tests for the decommission role task files.

These tests verify the structural safety contracts of decommission role tasks:
- Confirmation gate blocks execution without explicit opt-in (bypassed only in dry-run).
- Primary hub is explicitly asserted before any destructive operations.
- RBAC is validated before destructive operations begin.
- Dry-run mode skips all live cluster reads and deletes.
- Delete loops, wait conditions, and NotFound handling are present.
- ClusterDeployment safety verification is in place before ManagedCluster deletion.
- Pod wait uses failed_when: false to warn (not fail) when pods linger.
"""

import pathlib

import yaml

ROLES_DIR = pathlib.Path(__file__).resolve().parents[2] / "roles"
DECOMMISSION_MAIN = ROLES_DIR / "decommission" / "tasks" / "main.yml"
DELETE_OBSERVABILITY = ROLES_DIR / "decommission" / "tasks" / "delete_observability.yml"
DELETE_MANAGED_CLUSTERS = ROLES_DIR / "decommission" / "tasks" / "delete_managed_clusters.yml"
DELETE_MCH = ROLES_DIR / "decommission" / "tasks" / "delete_multiclusterhub.yml"


def _when_text(task: dict) -> str:
    """Normalize a task's 'when' condition to a single string for assertion."""
    when = task.get("when", "")
    if isinstance(when, list):
        return " ".join(str(w) for w in when)
    return str(when)


def _include_file(task: dict) -> str:
    """Extract filename from ansible.builtin.include_tasks regardless of string vs dict form."""
    val = task.get("ansible.builtin.include_tasks", "")
    if isinstance(val, str):
        return val
    return val.get("file", "") if isinstance(val, dict) else ""


class TestDecommissionMain:
    """decommission/tasks/main.yml structural contract tests."""

    def setup_method(self):
        self.tasks = yaml.safe_load(DECOMMISSION_MAIN.read_text())

    def test_file_exists(self):
        assert DECOMMISSION_MAIN.exists(), "decommission/tasks/main.yml must exist"

    def test_confirmation_gate_exists(self):
        """A fail task must exist to block unconfirmed decommission operations.

        Without a confirmation gate, an operator who accidentally runs the decommission
        playbook without setting confirmed=true would destroy the cluster immediately.
        """
        fail_tasks = [t for t in self.tasks if "ansible.builtin.fail" in t]
        confirmation_gate = [
            t
            for t in fail_tasks
            if "confirmed" in str(t.get("ansible.builtin.fail", {}).get("msg", "")) or "confirmed" in _when_text(t)
        ]
        assert confirmation_gate, (
            "decommission/tasks/main.yml must have a confirmation gate fail task that references "
            "acm_switchover_decommission.confirmed"
        )

    def test_confirmation_gate_bypassed_only_in_dry_run(self):
        """Confirmation gate must be skipped in dry-run mode but enforced for all live operations."""
        fail_tasks = [t for t in self.tasks if "ansible.builtin.fail" in t]
        confirmation_gate = [
            t
            for t in fail_tasks
            if "confirmed" in str(t.get("ansible.builtin.fail", {}).get("msg", "")) or "confirmed" in _when_text(t)
        ]
        assert confirmation_gate, "Confirmation gate must exist (see test_confirmation_gate_exists)"
        for task in confirmation_gate:
            when = _when_text(task)
            assert "dry_run" in when and "!=" in when, (
                "Confirmation gate must allow dry-run to bypass with '!= dry_run' so dry-run "
                "can be used to preview decommission without a confirmed=true flag"
            )

    def test_primary_hub_assertion_exists(self):
        """An assert task must verify primary hub kubeconfig and context are non-empty.

        Without this safety gate, decommission could run against an implicit default kubeconfig,
        potentially destroying the wrong cluster.
        """
        assert_tasks = [t for t in self.tasks if "ansible.builtin.assert" in t]
        hub_asserts = [t for t in assert_tasks if "primary" in str(t.get("ansible.builtin.assert", {}).get("that", ""))]
        assert hub_asserts, (
            "decommission/tasks/main.yml must have an assert task verifying "
            "acm_switchover_hubs.primary kubeconfig and context are non-empty"
        )

    def test_primary_hub_assertion_checks_kubeconfig_and_context(self):
        """Primary hub assert must verify both kubeconfig AND context are non-empty strings."""
        assert_tasks = [t for t in self.tasks if "ansible.builtin.assert" in t]
        hub_asserts = [t for t in assert_tasks if "primary" in str(t.get("ansible.builtin.assert", {}).get("that", ""))]
        assert hub_asserts
        for task in hub_asserts:
            conditions = task["ansible.builtin.assert"]["that"]
            assert isinstance(conditions, list), "Assert 'that' must be a list of conditions"
            conditions_text = " ".join(str(c) for c in conditions)
            assert "kubeconfig" in conditions_text, "Primary hub assert must check kubeconfig is non-empty"
            assert "context" in conditions_text, "Primary hub assert must check context is non-empty"

    def test_rbac_validated_before_destructive_operations(self):
        """RBAC validation must run before any delete operations begin.

        If RBAC validation ran after deletes started, partial deletions could have already
        occurred before discovering the operator lacks sufficient permissions.
        """
        include_tasks = [t for t in self.tasks if "ansible.builtin.include_tasks" in t]
        filenames = [_include_file(t) for t in include_tasks]
        assert "validate_rbac.yml" in filenames, "decommission/tasks/main.yml must include validate_rbac.yml"
        rbac_idx = filenames.index("validate_rbac.yml")
        for delete_file in ("delete_observability.yml", "delete_managed_clusters.yml", "delete_multiclusterhub.yml"):
            assert delete_file in filenames, f"Expected {delete_file} to be included"
            delete_idx = filenames.index(delete_file)
            assert rbac_idx < delete_idx, (
                f"validate_rbac.yml (position {rbac_idx}) must be included before "
                f"{delete_file} (position {delete_idx})"
            )

    def test_observability_deletion_is_conditional(self):
        """delete_observability.yml must be conditionally included, not always run.

        Not all ACM installations have MultiClusterObservability. Running the delete
        unconditionally would fail when the namespace does not exist.
        """
        include_tasks = [t for t in self.tasks if "ansible.builtin.include_tasks" in t]
        obs_includes = [t for t in include_tasks if _include_file(t) == "delete_observability.yml"]
        assert obs_includes, "decommission/tasks/main.yml must include delete_observability.yml"
        for task in obs_includes:
            assert "when" in task, (
                "delete_observability.yml include must have a 'when' condition — "
                "it should only run when observability is present"
            )
            assert "has_observability" in _when_text(
                task
            ), "delete_observability.yml include must be gated on the effective has_observability flag"

    def test_managed_clusters_and_mch_are_unconditional_includes(self):
        """delete_managed_clusters.yml and delete_multiclusterhub.yml must always be included."""
        include_tasks = [t for t in self.tasks if "ansible.builtin.include_tasks" in t]
        for required_file in ("delete_managed_clusters.yml", "delete_multiclusterhub.yml"):
            matching = [t for t in include_tasks if _include_file(t) == required_file]
            assert matching, f"decommission/tasks/main.yml must include {required_file}"
            for task in matching:
                assert "when" not in task, (
                    f"{required_file} must be included unconditionally — "
                    "internal tasks already guard on dry-run and resource presence"
                )


class TestDeleteObservability:
    """decommission/tasks/delete_observability.yml contract tests."""

    def setup_method(self):
        self.tasks = yaml.safe_load(DELETE_OBSERVABILITY.read_text())

    def test_file_exists(self):
        assert DELETE_OBSERVABILITY.exists(), "decommission/tasks/delete_observability.yml must exist"

    def test_dry_run_announces_without_acting(self):
        """In dry-run mode, a debug message must announce what would be deleted — no live ops."""
        debug_tasks = [t for t in self.tasks if "ansible.builtin.debug" in t]
        dry_run_announce = [t for t in debug_tasks if "dry_run" in _when_text(t) and "==" in _when_text(t)]
        assert dry_run_announce, (
            "delete_observability.yml must have a dry-run debug task that announces the deletion "
            "that would occur without actually running it"
        )

    def test_list_task_skipped_in_dry_run(self):
        """MultiClusterObservability list (k8s_info) must be guarded by execute mode."""
        k8s_info_tasks = [t for t in self.tasks if "kubernetes.core.k8s_info" in t]
        mco_list_tasks = [
            t
            for t in k8s_info_tasks
            if t.get("kubernetes.core.k8s_info", {}).get("kind") == "MultiClusterObservability"
        ]
        assert mco_list_tasks, "delete_observability.yml must list MultiClusterObservability resources"
        for task in mco_list_tasks:
            when = _when_text(task)
            assert (
                "!= 'dry_run'" in when
            ), "MCO list task must be guarded by execute-mode check to skip live reads in dry-run"

    def test_delete_task_guarded_by_execute_mode(self):
        """k8s state:absent delete task must not run in dry-run mode."""
        k8s_tasks = [t for t in self.tasks if "kubernetes.core.k8s" in t and "k8s_info" not in str(t)]
        delete_tasks = [t for t in k8s_tasks if t.get("kubernetes.core.k8s", {}).get("state") == "absent"]
        assert delete_tasks, "delete_observability.yml must have a delete task (state: absent)"
        for task in delete_tasks:
            when = _when_text(task)
            assert "!= 'dry_run'" in when, (
                "Delete task must be guarded by execute-mode check — "
                "accidentally running in dry-run would destroy observability"
            )

    def test_delete_task_uses_loop(self):
        """Delete task must use a loop to delete all discovered resources."""
        k8s_tasks = [t for t in self.tasks if "kubernetes.core.k8s" in t and "k8s_info" not in str(t)]
        delete_tasks = [t for t in k8s_tasks if t.get("kubernetes.core.k8s", {}).get("state") == "absent"]
        assert delete_tasks
        for task in delete_tasks:
            assert "loop" in task, "Delete task must use 'loop' to handle all discovered MCO instances"

    def test_wait_task_has_retries_delay_until(self):
        """Pod wait task must use polling with retries and delay."""
        k8s_info_tasks = [t for t in self.tasks if "kubernetes.core.k8s_info" in t]
        wait_tasks = [t for t in k8s_info_tasks if t.get("kubernetes.core.k8s_info", {}).get("kind") == "Pod"]
        assert wait_tasks, "delete_observability.yml must have a Pod wait task"
        for task in wait_tasks:
            assert "retries" in task, "Pod wait must have retries"
            assert "delay" in task, "Pod wait must have delay"
            assert "until" in task, "Pod wait must have until condition"

    def test_wait_task_handles_notfound_gracefully(self):
        """Pod wait task's failed_when must handle NotFound so pod termination is idempotent.

        Pods may be gone before the wait poll runs. A bare failed=true check would cause the
        wait to fail even when pods are already cleanly terminated (404 = success, not error).
        """
        k8s_info_tasks = [t for t in self.tasks if "kubernetes.core.k8s_info" in t]
        wait_tasks = [t for t in k8s_info_tasks if t.get("kubernetes.core.k8s_info", {}).get("kind") == "Pod"]
        assert wait_tasks
        for task in wait_tasks:
            failed_when = task.get("failed_when", "")
            assert failed_when, "Pod wait task must have a failed_when condition"
            failed_when_text = (
                " ".join(str(c) for c in failed_when) if isinstance(failed_when, list) else str(failed_when)
            )
            assert "NotFound" in failed_when_text or "not found" in failed_when_text.lower(), (
                "Pod wait failed_when must allow NotFound responses to be treated as success "
                "(pods already gone = namespace/resource already cleaned up)"
            )


class TestDeleteManagedClusters:
    """decommission/tasks/delete_managed_clusters.yml contract tests."""

    def setup_method(self):
        self.tasks = yaml.safe_load(DELETE_MANAGED_CLUSTERS.read_text())

    def test_file_exists(self):
        assert DELETE_MANAGED_CLUSTERS.exists(), "decommission/tasks/delete_managed_clusters.yml must exist"

    def test_list_task_skipped_in_dry_run(self):
        """ManagedCluster list task must be guarded by execute mode."""
        k8s_info_tasks = [t for t in self.tasks if "kubernetes.core.k8s_info" in t]
        mc_list_tasks = [
            t for t in k8s_info_tasks if t.get("kubernetes.core.k8s_info", {}).get("kind") == "ManagedCluster"
        ]
        assert mc_list_tasks, "delete_managed_clusters.yml must list ManagedCluster resources"
        list_task = mc_list_tasks[0]
        assert "!= 'dry_run'" in _when_text(list_task), "ManagedCluster list must be guarded by execute-mode check"

    def test_local_cluster_excluded_from_deletion_targets(self):
        """local-cluster must always be excluded from the deletion targets.

        Deleting the local-cluster ManagedCluster would decommission the hub cluster's own
        ACM management, which is always the wrong behavior during decommission of spoke clusters.
        """
        set_fact_tasks = [t for t in self.tasks if "ansible.builtin.set_fact" in t]
        target_selection = [
            t for t in set_fact_tasks if "_managed_cluster_delete_targets" in str(t.get("ansible.builtin.set_fact", {}))
        ]
        assert (
            target_selection
        ), "delete_managed_clusters.yml must have a set_fact task that builds the deletion targets list"
        for task in target_selection:
            task_text = str(task)
            assert "local-cluster" in task_text, "Deletion target selection must explicitly exclude 'local-cluster'"
            assert (
                "rejectattr" in task_text
            ), "Deletion target selection must use rejectattr to filter out local-cluster"

    def test_clusterdeployment_safety_verified_before_deletion(self):
        """ClusterDeployment preserveOnDelete safety must be verified before deleting ManagedClusters.

        Deleting a ManagedCluster whose matching Hive ClusterDeployment lacks preserveOnDelete=true
        will deprovision the underlying cluster infrastructure. This is non-recoverable.
        """
        file_text = DELETE_MANAGED_CLUSTERS.read_text()
        assert (
            "ClusterDeployment" in file_text
        ), "delete_managed_clusters.yml must verify ClusterDeployment safety before deleting ManagedClusters"
        assert (
            "preserveOnDelete" in file_text or "preserve_on_delete" in file_text.lower()
        ), "ClusterDeployment safety check must verify preserveOnDelete=true"


class TestDeleteMultiClusterHub:
    """decommission/tasks/delete_multiclusterhub.yml contract tests."""

    def setup_method(self):
        self.tasks = yaml.safe_load(DELETE_MCH.read_text())

    def test_file_exists(self):
        assert DELETE_MCH.exists(), "decommission/tasks/delete_multiclusterhub.yml must exist"

    def test_list_and_delete_guarded_by_execute_mode(self):
        """MCH list and delete operations must not run in dry-run mode."""
        # list task
        k8s_info_tasks = [t for t in self.tasks if "kubernetes.core.k8s_info" in t]
        mch_list_tasks = [
            t for t in k8s_info_tasks if t.get("kubernetes.core.k8s_info", {}).get("kind") == "MultiClusterHub"
        ]
        assert mch_list_tasks, "delete_multiclusterhub.yml must list MultiClusterHub resources"
        for task in mch_list_tasks:
            assert "!= 'dry_run'" in _when_text(task), "MCH list must be guarded by execute-mode check"

        # delete task
        k8s_tasks = [t for t in self.tasks if "kubernetes.core.k8s" in t and "k8s_info" not in str(t)]
        mch_delete_tasks = [t for t in k8s_tasks if t.get("kubernetes.core.k8s", {}).get("kind") == "MultiClusterHub"]
        assert mch_delete_tasks, "delete_multiclusterhub.yml must have a MultiClusterHub delete task"
        for task in mch_delete_tasks:
            assert "!= 'dry_run'" in _when_text(task), "MCH delete must be guarded by execute-mode check"

    def test_mch_operations_use_primary_hub(self):
        """All MCH operations must target the primary hub (old hub being decommissioned)."""
        for task in self.tasks:
            for module in ("kubernetes.core.k8s_info", "kubernetes.core.k8s"):
                if module in task:
                    params = task[module]
                    if isinstance(params, dict) and params.get("kind") in ("MultiClusterHub", "Pod"):
                        kubeconfig = str(params.get("kubeconfig", ""))
                        assert (
                            "primary" in kubeconfig
                        ), f"Task '{task.get('name')}' must use primary hub kubeconfig for MCH operations"

    def test_pod_wait_uses_failed_when_false(self):
        """ACM pod wait must use failed_when: false to warn rather than fail when pods linger.

        The pod watch may time out when some ACM components take unexpectedly long to
        terminate. Failing hard here is unhelpful — the MCH is already deleted; the operator
        should be warned and can verify manually.
        """
        k8s_info_tasks = [t for t in self.tasks if "kubernetes.core.k8s_info" in t]
        pod_wait_tasks = [t for t in k8s_info_tasks if t.get("kubernetes.core.k8s_info", {}).get("kind") == "Pod"]
        assert pod_wait_tasks, "delete_multiclusterhub.yml must have a Pod wait task"
        for task in pod_wait_tasks:
            assert task.get("failed_when") is False, (
                "ACM pod wait must use 'failed_when: false' — lingering pods should warn, "
                "not abort the decommission. The MCH is already deleted at this point."
            )

    def test_pod_wait_has_retries_delay_until(self):
        """MCH pod wait must use polling with retries and delay."""
        k8s_info_tasks = [t for t in self.tasks if "kubernetes.core.k8s_info" in t]
        pod_wait_tasks = [t for t in k8s_info_tasks if t.get("kubernetes.core.k8s_info", {}).get("kind") == "Pod"]
        assert pod_wait_tasks
        for task in pod_wait_tasks:
            assert "retries" in task, "Pod wait must specify retries"
            assert "delay" in task, "Pod wait must specify delay"
            assert "until" in task, "Pod wait must specify until condition"
