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
POST_ACTIVATION_DISCOVER = (
    ROLES_DIR / "post_activation" / "tasks" / "discover_resources.yml"
)


class TestActivationDiscoverResources:
    """Activation discover_resources.yml must use test_overrides for Restores and standard guards for MCH."""

    def setup_method(self):
        self.tasks = yaml.safe_load(ACTIVATION_DISCOVER.read_text()) or []

    def test_file_exists(self):
        assert (
            ACTIVATION_DISCOVER.exists()
        ), "activation/tasks/discover_resources.yml must exist"

    def test_restores_use_test_override_pattern(self):
        """Activation Restore discovery uses acm_switchover_test_overrides, not a bare is-not-defined guard.

        This allows tests to pre-seed Restore data via a dedicated override variable rather than
        the normal fact variable, which may already be set from a previous role run.
        """
        set_fact_tasks = [t for t in self.tasks if "ansible.builtin.set_fact" in t]
        override_tasks = [
            t for t in set_fact_tasks if "test_overrides" in _when_text(t)
        ]
        assert override_tasks, (
            "activation/discover_resources.yml must have a set_fact task guarded by "
            "acm_switchover_test_overrides.activation_restores_info to allow test pre-seeding"
        )

    def test_restores_live_read_skipped_when_test_override_supplied(self):
        """Live Restore read must be gated so it is skipped when a test override is supplied."""
        k8s_info_tasks = [t for t in self.tasks if "kubernetes.core.k8s_info" in t]
        restore_reads = [
            t
            for t in k8s_info_tasks
            if t.get("kubernetes.core.k8s_info", {}).get("kind") == "Restore"
        ]
        assert (
            restore_reads
        ), "activation/discover_resources.yml must read Restore resources"
        for task in restore_reads:
            when = _when_text(task)
            assert (
                "acm_switchover_test_overrides" in when and "is not defined" in when
            ), "Live Restore read must be gated on acm_switchover_test_overrides so tests can skip it by supplying an override"

    def test_restores_live_read_uses_secondary_hub(self):
        """Activation live Restore read must target the secondary hub."""
        k8s_info_tasks = [t for t in self.tasks if "kubernetes.core.k8s_info" in t]
        restore_reads = [
            t
            for t in k8s_info_tasks
            if t.get("kubernetes.core.k8s_info", {}).get("kind") == "Restore"
        ]
        assert restore_reads
        for task in restore_reads:
            params = task["kubernetes.core.k8s_info"]
            assert "secondary" in str(
                params.get("kubeconfig", "")
            ), "Activation Restore live read must use acm_switchover_hubs.secondary.kubeconfig"
            assert "secondary" in str(
                params.get("context", "")
            ), "Activation Restore live read must use acm_switchover_hubs.secondary.context"

    def test_mch_uses_standard_is_not_defined_guard(self):
        """Activation MCH discovery must use the standard 'is not defined' pre-seed guard."""
        k8s_info_tasks = [t for t in self.tasks if "kubernetes.core.k8s_info" in t]
        mch_reads = [
            t
            for t in k8s_info_tasks
            if t.get("kubernetes.core.k8s_info", {}).get("kind") == "MultiClusterHub"
        ]
        assert (
            mch_reads
        ), "activation/discover_resources.yml must read MultiClusterHub resources"
        for task in mch_reads:
            when = _when_text(task)
            assert "acm_activation_mch_info is not defined" in when, (
                "MCH read must use 'when: acm_activation_mch_info is not defined' guard "
                "to allow tests to pre-seed without triggering a live cluster read"
            )

    def test_mch_live_read_uses_secondary_hub(self):
        """Activation MCH read must target the secondary hub (new hub being activated)."""
        k8s_info_tasks = [t for t in self.tasks if "kubernetes.core.k8s_info" in t]
        mch_reads = [
            t
            for t in k8s_info_tasks
            if t.get("kubernetes.core.k8s_info", {}).get("kind") == "MultiClusterHub"
        ]
        assert mch_reads
        for task in mch_reads:
            params = task["kubernetes.core.k8s_info"]
            assert "secondary" in str(params.get("kubeconfig", "")), (
                "MCH live read must use acm_switchover_hubs.secondary.kubeconfig — "
                "MCH readiness is checked on the new (secondary) hub"
            )

    def test_publish_tasks_guard_against_skipped_registers(self):
        """Publish set_fact tasks must guard against skipped register values.

        If the live read was skipped (because test override was supplied), the register variable
        has skipped=True. Publishing it without the guard would set the fact to the skip result,
        breaking downstream templates that iterate over resources.
        """
        publish_tasks = [t for t in self.tasks if "Publish live" in t.get("name", "")]
        assert (
            publish_tasks
        ), "activation/discover_resources.yml must have 'Publish live...' set_fact tasks"
        for task in publish_tasks:
            when = _when_text(task)
            assert "skipped" in when, (
                f"Publish task '{task.get('name')}' must guard against skipped register "
                "with 'not (register.skipped | default(false))'"
            )


class TestFinalizationDiscoverResources:
    """Finalization discover_resources.yml hub routing: secondary for new-hub reads, primary for old-hub reads."""

    def setup_method(self):
        self.file_text = FINALIZATION_DISCOVER.read_text()
        self.tasks = yaml.safe_load(self.file_text) or []

    def test_file_exists(self):
        assert (
            FINALIZATION_DISCOVER.exists()
        ), "finalization/tasks/discover_resources.yml must exist"

    def test_backup_schedules_guard_and_secondary_hub(self):
        """BackupSchedule discovery must be pre-seedable and target the secondary hub."""
        k8s_info_tasks = [t for t in self.tasks if "kubernetes.core.k8s_info" in t]
        bs_reads = [
            t
            for t in k8s_info_tasks
            if t.get("kubernetes.core.k8s_info", {}).get("kind") == "BackupSchedule"
        ]
        assert (
            bs_reads
        ), "finalization/discover_resources.yml must read BackupSchedule resources"
        for task in bs_reads:
            when = _when_text(task)
            assert (
                "acm_finalization_backup_schedules_info is not defined" in when
            ), "BackupSchedule read must use pre-seed guard"
            params = task["kubernetes.core.k8s_info"]
            assert "secondary" in str(
                params.get("kubeconfig", "")
            ), "BackupSchedule reads must target secondary hub (where backups are being enabled)"

    def test_mch_guard_and_secondary_hub(self):
        """MCH discovery must be pre-seedable and target the secondary hub."""
        k8s_info_tasks = [t for t in self.tasks if "kubernetes.core.k8s_info" in t]
        mch_reads = [
            t
            for t in k8s_info_tasks
            if t.get("kubernetes.core.k8s_info", {}).get("kind") == "MultiClusterHub"
        ]
        assert mch_reads, "finalization/discover_resources.yml must read MCH resources"
        for task in mch_reads:
            when = _when_text(task)
            assert (
                "acm_finalization_mch_info is not defined" in when
            ), "MCH read must use pre-seed guard"
            params = task["kubernetes.core.k8s_info"]
            assert "secondary" in str(
                params.get("kubeconfig", "")
            ), "MCH reads must target secondary hub"

    def test_restores_secondary_guard_and_secondary_hub(self):
        """Secondary Restore discovery must be pre-seedable and target the secondary hub."""
        k8s_info_tasks = [t for t in self.tasks if "kubernetes.core.k8s_info" in t]
        # Secondary restores use secondary hub; distinguish from old-hub primary restore
        secondary_restore_reads = [
            t
            for t in k8s_info_tasks
            if t.get("kubernetes.core.k8s_info", {}).get("kind") == "Restore"
            and "secondary"
            in str(t.get("kubernetes.core.k8s_info", {}).get("kubeconfig", ""))
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
            and "primary"
            in str(t.get("kubernetes.core.k8s_info", {}).get("kubeconfig", ""))
        ]
        assert primary_restore_reads, (
            "finalization/discover_resources.yml must read the old hub's passive sync Restore "
            "from the PRIMARY hub kubeconfig. Reading from the wrong hub silently returns no restore."
        )
        for task in primary_restore_reads:
            params = task["kubernetes.core.k8s_info"]
            assert "primary" in str(
                params.get("context", "")
            ), "Old hub Restore read must also use primary hub context"

    def test_old_hub_restore_live_read_requires_execute_mode(self):
        """Old hub Restore live read must be guarded so it does not run in dry-run mode."""
        k8s_info_tasks = [t for t in self.tasks if "kubernetes.core.k8s_info" in t]
        primary_restore_reads = [
            t
            for t in k8s_info_tasks
            if t.get("kubernetes.core.k8s_info", {}).get("kind") == "Restore"
            and "primary"
            in str(t.get("kubernetes.core.k8s_info", {}).get("kubeconfig", ""))
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
            if "_old_hub_existing_restore_info"
            in str(t.get("ansible.builtin.set_fact", {}))
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
        assert (
            POST_ACTIVATION_DISCOVER.exists()
        ), "post_activation/tasks/discover_resources.yml must exist"

    def test_managed_clusters_uses_is_not_defined_guard(self):
        """ManagedCluster discovery must use 'is not defined' guard to allow test pre-seeding."""
        k8s_info_tasks = [t for t in self.tasks if "kubernetes.core.k8s_info" in t]
        mc_reads = [
            t
            for t in k8s_info_tasks
            if t.get("kubernetes.core.k8s_info", {}).get("kind") == "ManagedCluster"
        ]
        assert (
            mc_reads
        ), "post_activation/discover_resources.yml must read ManagedCluster resources"
        for task in mc_reads:
            when = _when_text(task)
            assert "acm_secondary_managed_clusters_info is not defined" in when, (
                "ManagedCluster read must use 'when: acm_secondary_managed_clusters_info is not defined' "
                "so unit tests can inject fixture data without needing a live cluster"
            )

    def test_managed_clusters_uses_secondary_hub(self):
        """ManagedCluster reads must target the secondary hub (new hub after switchover)."""
        k8s_info_tasks = [t for t in self.tasks if "kubernetes.core.k8s_info" in t]
        mc_reads = [
            t
            for t in k8s_info_tasks
            if t.get("kubernetes.core.k8s_info", {}).get("kind") == "ManagedCluster"
        ]
        assert mc_reads
        for task in mc_reads:
            params = task["kubernetes.core.k8s_info"]
            assert "secondary" in str(params.get("kubeconfig", "")), (
                "ManagedCluster reads must use secondary hub kubeconfig — "
                "post_activation checks that clusters connected to the new hub"
            )
            assert "secondary" in str(
                params.get("context", "")
            ), "ManagedCluster reads must use secondary hub context"
