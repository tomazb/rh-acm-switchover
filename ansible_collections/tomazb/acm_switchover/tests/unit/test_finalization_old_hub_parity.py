"""Static parity tests for finalization old-hub behavior."""

import pathlib

import yaml

ROLES_DIR = pathlib.Path(__file__).resolve().parents[2] / "roles"
FINALIZATION_TASKS = ROLES_DIR / "finalization" / "tasks"


def _main_block_tasks() -> list[dict]:
    main_tasks = yaml.safe_load((FINALIZATION_TASKS / "main.yml").read_text())
    for task in main_tasks:
        if "block" in task:
            return task["block"]
    raise AssertionError("finalization/main.yml must contain a block of phase tasks")


def test_finalization_main_includes_old_hub_support_tasks():
    """finalization/main.yml must wire the old-hub parity task files."""
    includes = [task.get("ansible.builtin.include_tasks", "") for task in _main_block_tasks()]

    assert "disable_old_hub_observability.yml" in includes
    assert "verify_old_hub_state.yml" in includes
    assert includes.index("disable_old_hub_observability.yml") < includes.index("enable_backups.yml")
    assert includes.index("verify_old_hub_state.yml") > includes.index("handle_old_hub.yml")


def test_finalization_main_disables_old_hub_observability_only_when_observability_enabled():
    """Old-hub observability disablement must honor observability skip state."""
    disable_task = next(
        task
        for task in _main_block_tasks()
        if task.get("ansible.builtin.include_tasks") == "disable_old_hub_observability.yml"
    )
    when_text = "\n".join(disable_task.get("when", []))

    assert "disable_observability_on_secondary" not in when_text
    assert "skip_observability_checks" in when_text
    assert "(acm_switchover_operation.old_hub_action | default('secondary')) == 'secondary'" in when_text


def test_disable_old_hub_observability_deletes_mco_and_waits_for_termination():
    """disable_old_hub_observability.yml must delete MCO, not scale workloads to zero."""
    text = (FINALIZATION_TASKS / "disable_old_hub_observability.yml").read_text()

    assert "kind: MultiClusterObservability" in text
    assert "state: absent" in text
    assert "deleted_mcos" in text
    assert "kind: Pod" in text
    assert "kubernetes.core.k8s_scale" not in text


def test_verify_old_hub_state_checks_clusters_and_backup_schedule():
    """verify_old_hub_state.yml must query ManagedClusters and BackupSchedule on the old hub."""
    text = (FINALIZATION_TASKS / "verify_old_hub_state.yml").read_text()

    assert "kind: ManagedCluster" in text
    assert "ManagedClusterConditionAvailable" in text
    assert "kind: BackupSchedule" in text
    assert "paused" in text
