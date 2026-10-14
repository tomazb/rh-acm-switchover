"""YAML contract tests for discover_resources.yml across activation, finalization, and post_activation roles.

These tests verify that each role's resource discovery follows the expected contracts:
- Pre-seed guards allow unit tests to inject fixture data without live clusters.
- Hub routing is correct: secondary hub for new-hub reads, primary hub for old-hub reads.
- Publish tasks guard against skipped register variables.
- Dry-run mode seeds safe defaults rather than attempting live reads.
"""

import pathlib

import yaml
from yaml_contract_helpers import _when_text

ROLES_DIR = pathlib.Path(__file__).resolve().parents[2] / "roles"
ACTIVATION_DISCOVER = ROLES_DIR / "activation" / "tasks" / "discover_resources.yml"
FINALIZATION_DISCOVER = ROLES_DIR / "finalization" / "tasks" / "discover_resources.yml"
POST_ACTIVATION_DISCOVER = ROLES_DIR / "post_activation" / "tasks" / "discover_resources.yml"


class TestActivationDiscoverResources:
    """R4-04: activation strictly reads the destination hub; fixtures pre-seed only non-live runs."""

    def setup_method(self):
        self.tasks = yaml.safe_load(ACTIVATION_DISCOVER.read_text()) or []
        self.strict_read = yaml.safe_load((ACTIVATION_DISCOVER.parent / "strict_read.yml").read_text()) or []

    def test_file_exists(self):
        assert ACTIVATION_DISCOVER.exists(), "activation/tasks/discover_resources.yml must exist"

    def test_mch_is_read_strictly_through_the_shared_read(self):
        reads = [t for t in self.tasks if t.get("ansible.builtin.include_tasks") == "strict_read.yml"]
        assert [t["vars"]["_acm_read"]["kind"] for t in reads] == ["MultiClusterHub"]
        assert reads[0]["vars"]["_acm_read"]["read_mode"] == "list"
        assert not [t for t in self.tasks if "kubernetes.core.k8s_info" in t]

    def test_strict_reads_use_the_secondary_hub(self):
        (read,) = [t for t in self.strict_read if "tomazb.acm_switchover.acm_k8s_read_outcome" in t]
        params = read["tomazb.acm_switchover.acm_k8s_read_outcome"]
        assert "secondary" in params["kubeconfig"] and "secondary" in params["context"]

    def test_the_override_seam_is_non_live_only(self):
        """An executing run always reads the live hub; pre-seeded reads serve dry_run/validate fixtures."""
        resolve = self.strict_read[0]["ansible.builtin.set_fact"]["_acm_read_overridden"]
        assert "migration_reads" in resolve
        assert "in ['dry_run', 'validate']" in resolve
        (read,) = [t for t in self.strict_read if "tomazb.acm_switchover.acm_k8s_read_outcome" in t]
        assert _when_text(read) == "not (_acm_read_overridden | bool)"
        published = self.strict_read[-1]["ansible.builtin.set_fact"]["{{ _acm_read_fact }}"]
        assert "if (_acm_read_overridden | bool)" in published and "'read_status'" in published

    def test_an_invalid_journal_refuses_before_any_restore_read(self):
        names = [t.get("name") for t in self.tasks]
        assert names[-1] == "Refuse an invalid recorded migration journal"
        assert "migration_journal_outcome" in _when_text(self.tasks[-1])


class TestFinalizationDiscoverResources:
    """Finalization discover_resources.yml hub routing: secondary for new-hub reads, primary for old-hub reads."""

    def setup_method(self):
        self.file_text = FINALIZATION_DISCOVER.read_text()
        self.tasks = yaml.safe_load(self.file_text) or []

    def test_file_exists(self):
        assert FINALIZATION_DISCOVER.exists(), "finalization/tasks/discover_resources.yml must exist"

    def test_backup_schedules_guard_and_secondary_hub(self):
        """BackupSchedule discovery must be pre-seedable and target the secondary hub."""
        k8s_info_tasks = [t for t in self.tasks if "kubernetes.core.k8s_info" in t]
        bs_reads = [t for t in k8s_info_tasks if t.get("kubernetes.core.k8s_info", {}).get("kind") == "BackupSchedule"]
        assert bs_reads, "finalization/discover_resources.yml must read BackupSchedule resources"
        for task in bs_reads:
            when = _when_text(task)
            assert (
                "acm_finalization_backup_schedules_info is not defined" in when
            ), "BackupSchedule read must use pre-seed guard"
            params = task["kubernetes.core.k8s_info"]
            assert "secondary" in str(
                params.get("kubeconfig", "")
            ), "BackupSchedule reads must target secondary hub (where backups are being enabled)"

    def test_mch_refreshes_in_execute_mode_and_uses_secondary_hub(self):
        """MCH discovery must ignore pre-seeded facts in execute mode and target the secondary hub."""
        k8s_info_tasks = [t for t in self.tasks if "kubernetes.core.k8s_info" in t]
        mch_reads = [
            t for t in k8s_info_tasks if t.get("kubernetes.core.k8s_info", {}).get("kind") == "MultiClusterHub"
        ]
        assert mch_reads, "finalization/discover_resources.yml must read MCH resources"
        for task in mch_reads:
            when = _when_text(task)
            assert "acm_switchover_execution.mode | default('dry_run') == 'execute'" in when, (
                "Finalization MCH read must refresh live data in execute mode even when "
                "tests or previous phases pre-seeded acm_finalization_mch_info"
            )
            assert (
                "acm_finalization_mch_info is not defined" in when
            ), "Finalization MCH read must remain pre-seedable outside execute mode"
            params = task["kubernetes.core.k8s_info"]
            assert "secondary" in str(params.get("kubeconfig", "")), "MCH reads must target secondary hub"

    def test_restores_secondary_guard_and_secondary_hub(self):
        """Secondary Restore discovery must be pre-seedable and target the secondary hub."""
        k8s_info_tasks = [t for t in self.tasks if "kubernetes.core.k8s_info" in t]
        # Secondary restores use secondary hub; distinguish from old-hub primary restore
        secondary_restore_reads = [
            t
            for t in k8s_info_tasks
            if t.get("kubernetes.core.k8s_info", {}).get("kind") == "Restore"
            and "secondary" in str(t.get("kubernetes.core.k8s_info", {}).get("kubeconfig", ""))
        ]
        assert (
            secondary_restore_reads
        ), "finalization/discover_resources.yml must read Restore resources on the secondary hub"
        for task in secondary_restore_reads:
            when = _when_text(task)
            assert "acm_finalization_restores_info is not defined" in when

    def test_old_hub_restore_reads_from_primary_hub(self):
        """Old hub passive sync Restore must be read from the PRIMARY hub — not from the secondary.

        Reading old-hub restore status from the wrong hub would silently report missing data,
        causing finalization to skip the passive sync setup on the old hub.
        """
        k8s_info_tasks = [t for t in self.tasks if "kubernetes.core.k8s_info" in t]
        primary_restore_reads = [
            t
            for t in k8s_info_tasks
            if t.get("kubernetes.core.k8s_info", {}).get("kind") == "Restore"
            and "primary" in str(t.get("kubernetes.core.k8s_info", {}).get("kubeconfig", ""))
        ]
        assert primary_restore_reads, (
            "finalization/discover_resources.yml must read the old hub's passive sync Restore "
            "from the PRIMARY hub kubeconfig. Reading from the wrong hub silently returns no restore."
        )
        for task in primary_restore_reads:
            params = task["kubernetes.core.k8s_info"]
            assert "primary" in str(params.get("context", "")), "Old hub Restore read must also use primary hub context"

    def test_old_hub_restore_live_read_requires_execute_mode(self):
        """Old hub Restore live read must be guarded so it does not run in dry-run mode."""
        k8s_info_tasks = [t for t in self.tasks if "kubernetes.core.k8s_info" in t]
        primary_restore_reads = [
            t
            for t in k8s_info_tasks
            if t.get("kubernetes.core.k8s_info", {}).get("kind") == "Restore"
            and "primary" in str(t.get("kubernetes.core.k8s_info", {}).get("kubeconfig", ""))
        ]
        assert (
            primary_restore_reads
        ), "Old hub Restore live read task must exist (see test_old_hub_restore_reads_from_primary_hub)"
        for task in primary_restore_reads:
            when = _when_text(task)
            assert "!= 'dry_run'" in when, (
                "Old hub Restore live read must be guarded by '!= dry_run' to prevent "
                "attempting a real cluster read during dry-run operations"
            )

    def test_dry_run_seeds_empty_old_hub_restore(self):
        """In dry-run mode, _old_hub_existing_restore_info must be seeded as empty.

        Without this, dry-run tasks that reference old-hub restore state would fail with
        an 'undefined variable' error since the live read is skipped.
        """
        set_fact_tasks = [t for t in self.tasks if "ansible.builtin.set_fact" in t]
        dry_run_seed_tasks = [
            t
            for t in set_fact_tasks
            if "_old_hub_existing_restore_info" in str(t.get("ansible.builtin.set_fact", {}))
            and "dry_run" in _when_text(t)
        ]
        assert dry_run_seed_tasks, (
            "finalization/discover_resources.yml must seed _old_hub_existing_restore_info "
            "as empty in dry-run mode to prevent undefined variable errors"
        )
        for task in dry_run_seed_tasks:
            fact = task["ansible.builtin.set_fact"]
            assert "_old_hub_existing_restore_info" in fact
            assert (
                fact["_old_hub_existing_restore_info"].get("resources") == []
            ), "Dry-run seed must initialize resources: [] to allow safe iteration"


class TestPostActivationDiscoverResources:
    """post_activation discover_resources.yml must guard ManagedCluster reads and target the secondary hub."""

    def setup_method(self):
        self.tasks = yaml.safe_load(POST_ACTIVATION_DISCOVER.read_text()) or []

    def test_file_exists(self):
        assert POST_ACTIVATION_DISCOVER.exists(), "post_activation/tasks/discover_resources.yml must exist"

    def test_managed_clusters_are_read_strictly_without_a_pre_seed_guard(self):
        """R4-04 Task 9: every execution reads the live inventory strictly; a pre-seed never stands in."""
        includes = [t.get("ansible.builtin.include_tasks") for t in self.tasks]
        assert includes == ["read_managed_clusters.yml"]
        assert self.tasks[0]["vars"] == {"_acm_mc_fact": "acm_secondary_managed_clusters_info"}
        assert "is not defined" not in POST_ACTIVATION_DISCOVER.read_text()

    def test_managed_clusters_uses_secondary_hub(self):
        """ManagedCluster reads must target the secondary hub (new hub after switchover)."""
        seam = yaml.safe_load(
            (POST_ACTIVATION_DISCOVER.parents[2] / "activation" / "tasks" / "strict_read.yml").read_text()
        )
        (read,) = [t for t in seam if "tomazb.acm_switchover.acm_k8s_read_outcome" in t]
        params = read["tomazb.acm_switchover.acm_k8s_read_outcome"]
        assert params["kubeconfig"] == "{{ acm_switchover_hubs.secondary.kubeconfig }}"
        assert params["context"] == "{{ acm_switchover_hubs.secondary.context }}"
