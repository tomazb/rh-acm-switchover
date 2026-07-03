"""Tests for BackupSchedule persistence across Ansible phase handoff."""

from pathlib import Path

import yaml

ROLES_DIR = Path(__file__).resolve().parents[2] / "roles"
PRIMARY_PREP_TASKS = ROLES_DIR / "primary_prep" / "tasks"


def _load_pause_backups_tasks() -> list[dict]:
    return yaml.safe_load((PRIMARY_PREP_TASKS / "pause_backups.yml").read_text())


def test_pause_backups_captures_saved_backup_schedule_body():
    """primary_prep must persist a reusable BackupSchedule body before pause/delete."""
    text = (PRIMARY_PREP_TASKS / "pause_backups.yml").read_text()
    assert (
        "acm_switchover_saved_backup_schedule" in text
    ), "pause_backups.yml must store the current BackupSchedule body for later recreation"


def test_backup_schedule_version_derivation_does_not_default_to_212():
    """Missing MCH versions must fail planning instead of assuming ACM 2.12 semantics."""
    primary_text = (PRIMARY_PREP_TASKS / "pause_backups.yml").read_text()
    finalization_text = (ROLES_DIR / "finalization" / "tasks" / "enable_backups.yml").read_text()

    assert "currentVersion', '2.12.0'" not in primary_text
    assert "currentVersion', '2.12.0'" not in finalization_text


def test_primary_prep_persists_saved_backup_schedule_in_checkpoint_operational_data():
    """checkpointed primary_prep runs must carry the saved BackupSchedule across resume."""
    text = (PRIMARY_PREP_TASKS / "main.yml").read_text()
    assert "operational_data:" in text
    assert (
        "saved_backup_schedule" in text
    ), "primary_prep/main.yml must write saved_backup_schedule into checkpoint operational_data"


def test_pause_backups_captures_backup_schedule_before_acm_211_delete():
    """ACM 2.11 delete pause must snapshot the BackupSchedule before destructive mutation."""
    text = (PRIMARY_PREP_TASKS / "pause_backups.yml").read_text()
    assert text.index("Persist reusable BackupSchedule body before pause/delete") < text.index(
        "Delete BackupSchedule for ACM 2.11 pause"
    )


def test_pause_backups_delete_branch_restores_saved_schedule_on_failure():
    """Failed ACM 2.11 delete flow must have an explicit restore path using the saved body."""
    tasks = _load_pause_backups_tasks()
    delete_block = next(task for task in tasks if task.get("name") == "Delete BackupSchedule for ACM 2.11 pause")
    rescue_text = str(delete_block.get("rescue", []))

    assert "Restore BackupSchedule after failed ACM 2.11 pause delete" in rescue_text
    assert "acm_switchover_saved_backup_schedule" in rescue_text
    assert "Fail after BackupSchedule pause delete failure" in rescue_text
    assert "ignore_errors" not in rescue_text


def test_pause_backups_patch_targets_primary_backup_schedule_resource():
    """ACM 2.12+ pause must patch the primary hub BackupSchedule with exact API targeting."""
    tasks = _load_pause_backups_tasks()
    patch_task = next(task for task in tasks if task.get("name") == "Patch BackupSchedule to paused state")
    k8s_args = patch_task["kubernetes.core.k8s"]

    assert k8s_args["kubeconfig"] == "{{ acm_switchover_hubs.primary.kubeconfig }}"
    assert k8s_args["context"] == "{{ acm_switchover_hubs.primary.context }}"
    assert k8s_args["state"] == "patched"
    assert k8s_args["api_version"] == "cluster.open-cluster-management.io/v1beta1"
    assert k8s_args["kind"] == "BackupSchedule"
    assert k8s_args["name"] == "{{ item.metadata.name }}"
    assert k8s_args["namespace"] == "{{ item.metadata.namespace | default('open-cluster-management-backup') }}"
    assert k8s_args["definition"] == "{{ acm_backup_schedule_operation.operation.patch }}"


def test_pause_backups_delete_targets_primary_backup_schedule_resource():
    """ACM 2.11 pause must delete the primary hub BackupSchedule with exact API targeting."""
    tasks = _load_pause_backups_tasks()
    delete_block = next(task for task in tasks if task.get("name") == "Delete BackupSchedule for ACM 2.11 pause")
    delete_task = next(
        task
        for task in delete_block["block"]
        if task.get("name") == "Delete BackupSchedule resource for ACM 2.11 pause"
    )
    k8s_args = delete_task["kubernetes.core.k8s"]

    assert k8s_args["kubeconfig"] == "{{ acm_switchover_hubs.primary.kubeconfig }}"
    assert k8s_args["context"] == "{{ acm_switchover_hubs.primary.context }}"
    assert k8s_args["state"] == "absent"
    assert k8s_args["api_version"] == "cluster.open-cluster-management.io/v1beta1"
    assert k8s_args["kind"] == "BackupSchedule"
    assert k8s_args["name"] == "{{ item.metadata.name }}"
    assert k8s_args["namespace"] == "{{ item.metadata.namespace | default('open-cluster-management-backup') }}"


def test_pause_backups_published_changed_covers_native_check_mode():
    """--check with mode: execute must surface a would-change verdict (Thermos R2-M1 part 2)."""
    text = (PRIMARY_PREP_TASKS / "pause_backups.yml").read_text()
    assert "ansible_check_mode" in text, "pause_backups.yml published changed must treat native check mode like dry_run"
