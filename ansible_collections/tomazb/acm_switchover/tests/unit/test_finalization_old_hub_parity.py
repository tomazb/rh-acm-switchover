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


def test_disable_old_hub_observability_scales_real_workloads():
    """disable_old_hub_observability.yml must scale the real observability workloads."""
    text = (FINALIZATION_TASKS / "disable_old_hub_observability.yml").read_text()

    assert "kubernetes.core.k8s_scale" in text
    assert "observability-observatorium-api" in text
    assert "observability-thanos-compact" in text
    assert "replicas: 0" in text


def test_verify_old_hub_state_checks_clusters_and_backup_schedule():
    """verify_old_hub_state.yml must query ManagedClusters and BackupSchedule on the old hub."""
    text = (FINALIZATION_TASKS / "verify_old_hub_state.yml").read_text()

    assert "kind: ManagedCluster" in text
    assert "ManagedClusterConditionAvailable" in text
    assert "kind: BackupSchedule" in text
    assert "paused" in text
