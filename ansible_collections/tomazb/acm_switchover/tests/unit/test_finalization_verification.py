"""Tests for finalization role backup and MCH verification hardening."""

import pathlib

import yaml

ROLES_DIR = pathlib.Path(__file__).resolve().parents[2] / "roles"
FINALIZATION_TASKS = ROLES_DIR / "finalization" / "tasks"


def _load_yaml(name: str) -> list[dict]:
    return yaml.safe_load((FINALIZATION_TASKS / name).read_text())


def _main_block_tasks() -> list[dict]:
    main_tasks = _load_yaml("main.yml")
    for task in main_tasks:
        if "block" in task:
            return task["block"]
    raise AssertionError("finalization/main.yml must contain a block of phase tasks")


def test_cleanup_restores_file_exists():
    """finalization must define a dedicated restore cleanup task file."""
    assert (FINALIZATION_TASKS / "cleanup_restores.yml").exists()


def test_main_cleans_restores_before_enabling_backups():
    """finalization/main.yml must clean secondary restores before enabling backups."""
    includes = [task.get("ansible.builtin.include_tasks", "") for task in _main_block_tasks()]

    assert "cleanup_restores.yml" in includes, "main.yml must include cleanup_restores.yml"
    assert "enable_backups.yml" in includes, "main.yml must include enable_backups.yml"
    assert includes.index("cleanup_restores.yml") < includes.index(
        "enable_backups.yml"
    ), "cleanup_restores.yml must run before enable_backups.yml"


def test_main_restores_backup_baseline_from_checkpoint():
    """finalization/main.yml must reload persisted backup baseline on resume."""
    text = (FINALIZATION_TASKS / "main.yml").read_text()
    assert "operational_data" in text
    assert "backup_schedule_enabled_at" in text
    assert "_checkpoint_enter.checkpoint" in text
    assert "default(omit)" not in text, "main.yml must not persist nested omit placeholders into checkpoint data"


def test_main_restores_saved_backup_schedule_from_checkpoint():
    """finalization/main.yml must reload saved BackupSchedule state on resume."""
    text = (FINALIZATION_TASKS / "main.yml").read_text()
    assert (
        "saved_backup_schedule" in text
    ), "main.yml must rehydrate saved_backup_schedule from checkpoint operational_data"


def test_verify_backups_waits_for_clean_completed_acm_owned_velero_backup():
    """verify_backups.yml must wait for a clean completed ACM-owned Velero backup."""
    tasks = _load_yaml("verify_backups.yml")
    timeout_task = next(
        task
        for task in tasks
        if "_acm_secondary_backup_verify_timeout_seconds" in task.get("ansible.builtin.set_fact", {})
    )
    backup_wait_tasks = [task for task in tasks if task.get("kubernetes.core.k8s_info", {}).get("kind") == "Backup"]

    assert backup_wait_tasks, "verify_backups.yml must query Velero Backup resources"

    timeout_expr = str(timeout_task["ansible.builtin.set_fact"]["_acm_secondary_backup_verify_timeout_seconds"])
    assert "+ 600" in timeout_expr, "verify_backups.yml must add completion grace beyond the BackupSchedule cadence"

    wait_task = backup_wait_tasks[0]
    assert wait_task["kubernetes.core.k8s_info"]["api_version"] == "velero.io/v1"
    assert "retries" in wait_task, "verify_backups.yml must wait for a new backup"
    assert "delay" in wait_task, "verify_backups.yml must poll for a new backup"
    until = str(wait_task.get("until", ""))
    assert (
        "acm_switchover_backup_schedule_enabled_at" in until
    ), "verify_backups.yml must require a backup created after backups were enabled"
    assert (
        "cluster.open-cluster-management.io/backup-schedule-type" in until
    ), "verify_backups.yml must filter to ACM-owned backups"
    retries = str(wait_task.get("retries", ""))
    assert (
        "_acm_secondary_backup_verify_timeout_seconds" in retries
    ), "verify_backups.yml must derive wait retries from a computed timeout"
    assert (
        len(backup_wait_tasks) >= 2
    ), "verify_backups.yml must re-check the selected fresh backup until it reaches a terminal phase"
    terminal_until = str(backup_wait_tasks[1].get("until", ""))
    assert (
        "PartiallyFailed" in terminal_until and "FailedValidation" in terminal_until
    ), "verify_backups.yml must wait on failure terminal phases for the selected backup"
    text = (FINALIZATION_TASKS / "verify_backups.yml").read_text()
    assert "veleroSchedule" in text, "verify_backups.yml must derive timeout from BackupSchedule cadence"
    assert (
        "PartiallyFailed" in text and "FailedValidation" in text
    ), "verify_backups.yml must fail unhealthy terminal backup phases"
    assert "errors" in text, "verify_backups.yml must validate backup error count before passing"
    assert (
        "sort(attribute='metadata.creationTimestamp')" in text
    ), "verify_backups.yml must validate the latest fresh backup, not the first healthy backup"
    assert_tasks = [task for task in tasks if "ansible.builtin.assert" in task]
    assert assert_tasks, "verify_backups.yml must fail when a fresh backup reaches an unhealthy terminal state"


def test_enable_backups_only_records_baseline_for_real_runs():
    """enable_backups.yml must not record a checkpoint baseline during dry-run."""
    text = (FINALIZATION_TASKS / "enable_backups.yml").read_text()
    assert "acm_switchover_backup_schedule_enabled_at" in text
    assert (
        "default('dry_run') != 'dry_run'" in text
    ), "enable_backups.yml must guard baseline timestamp recording for real execution only"


def test_enable_backups_can_recreate_saved_schedule():
    """enable_backups.yml must support recreating a saved BackupSchedule when none exists."""
    text = (FINALIZATION_TASKS / "enable_backups.yml").read_text()
    assert "saved_schedule" in text, "enable_backups.yml must pass saved_schedule into acm_backup_schedule"
    assert (
        "operation.action == 'create'" in text
    ), "enable_backups.yml must create a BackupSchedule when planning returns create"
    assert (
        "acm_secondary_backup_schedules_info" in text
    ), "enable_backups.yml must refresh schedule facts after create/patch so later verification sees current state"


def test_verify_backups_skips_restore_only_when_no_backup_schedule_exists():
    """restore-only finalization must not fail because BackupSchedule is intentionally absent."""
    text = (FINALIZATION_TASKS / "verify_backups.yml").read_text()
    assert "restore_only" in text, "verify_backups.yml must branch explicitly for restore-only mode"
    assert (
        "status: skipped" in text
    ), "verify_backups.yml must publish a skipped result when restore-only has no BackupSchedule to verify"


def test_verify_mch_requires_running_phase_and_healthy_pods():
    """verify_mch.yml must require a Running MCH and healthy ACM pods."""
    tasks = _load_yaml("verify_mch.yml")

    mch_tasks = [task for task in tasks if task.get("kubernetes.core.k8s_info", {}).get("kind") == "MultiClusterHub"]
    assert mch_tasks, "verify_mch.yml must query MultiClusterHub resources"
    mch_wait_task = mch_tasks[0]
    assert "retries" in mch_wait_task, "verify_mch.yml must wait for MCH readiness"
    assert "delay" in mch_wait_task, "verify_mch.yml must poll for MCH readiness"
    assert "Running" in str(
        mch_wait_task.get("until", "")
    ), "verify_mch.yml must wait for MultiClusterHub phase Running"

    pod_tasks = [task for task in tasks if task.get("kubernetes.core.k8s_info", {}).get("kind") == "Pod"]
    assert pod_tasks, "verify_mch.yml must verify ACM pod health"
    pod_wait_task = pod_tasks[0]
    assert pod_wait_task["kubernetes.core.k8s_info"]["namespace"] == "open-cluster-management"
    assert "retries" in pod_wait_task, "verify_mch.yml must wait for ACM pods to recover"
    assert "delay" in pod_wait_task, "verify_mch.yml must poll ACM pod health"
    until = str(pod_wait_task.get("until", ""))
    assert "Running" in until and "Succeeded" in until, "verify_mch.yml must only accept Running/Succeeded ACM pods"
    text = (FINALIZATION_TASKS / "verify_mch.yml").read_text()
    assert "status: skipped" in text, "verify_mch.yml must skip verification in dry-run"
    assert "Ready" in text, "verify_mch.yml must require pod readiness, not only pod phase"
    assert "CrashLoopBackOff" in text, "verify_mch.yml must reject crash-looping ACM pods"
