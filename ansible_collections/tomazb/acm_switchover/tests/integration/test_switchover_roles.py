"""Integration tests for switchover execution roles."""


def test_execute_mode_primary_prep_reaches_backup_schedule_plan_without_live_cluster(
    run_role_fixture,
):
    completed = run_role_fixture(
        "primary_prep",
        "execute_primary_prep_mutation_plan.yml",
    )

    assert completed.returncode != 0
    assert "Plan BackupSchedule pause operation" in completed.stdout
    assert "Patch BackupSchedule to paused state" in completed.stdout


def test_primary_prep_and_activation_fixture_pass(run_switchover_fixture):
    completed, report = run_switchover_fixture("passive_activation_success.yml")
    assert completed.returncode == 0
    assert report.get("phases", {}).get("primary_prep", {}).get("status") == "pass"
    assert report.get("phases", {}).get("activation", {}).get("status") == "pass"


def test_post_activation_dry_run_fixture_reports_skip(run_switchover_fixture):
    completed, report = run_switchover_fixture("post_activation_dry_run_skip.yml")
    assert completed.returncode == 0
    assert report["phases"]["post_activation"]["status"] == "skipped"
    assert report["phases"]["post_activation"]["reason"] == "dry_run"


def test_restore_activation_fixture_reports_delete_and_create_plan(
    run_switchover_fixture,
):
    completed, report = run_switchover_fixture("restore_activation_success.yml")
    assert completed.returncode == 0
    assert report["phases"]["activation"]["status"] == "pass"
    assert report["phases"]["activation"]["operation"]["action"] == "delete_and_create"
    assert (
        report["phases"]["activation"]["wait_target"]["name"] == "restore-acm-activate"
    )


def test_stale_preflight_restore_facts_do_not_allow_failed_live_activation_restore(
    run_switchover_fixture,
):
    completed, report = run_switchover_fixture(
        "stale_preflight_live_activation_failure.yml"
    )
    assert completed.returncode != 0
    assert "Passive Restore phase Failed failed." in completed.stdout
    assert "Build activation plan" not in completed.stdout
    assert report.get("phases", {}).get("activation") is None


def test_full_activation_fixture_reports_full_restore_plan(run_switchover_fixture):
    completed, report = run_switchover_fixture("full_activation_success.yml")
    assert completed.returncode == 0
    assert report["phases"]["activation"]["status"] == "pass"
    assert report["phases"]["activation"]["operation"]["action"] == "create"
    assert report["phases"]["activation"]["wait_target"]["name"] == "restore-acm-full"


def test_finalization_fixture_reports_enable_backup_operation(run_switchover_fixture):
    completed, report = run_switchover_fixture("finalization_backup_recovery.yml")
    assert completed.returncode == 0
    assert report["phases"]["finalization"]["status"] == "pass"
    assert (
        report["phases"]["finalization"]["enable_backups"]["operation"]["action"]
        == "patch"
    )


def test_finalization_reports_collision_repair_plan_when_backup_enable_is_already_satisfied(
    run_switchover_fixture,
):
    completed, report = run_switchover_fixture("finalization_noop.yml")
    assert completed.returncode == 0
    assert report["phases"]["finalization"]["status"] == "pass"
    assert (
        report["phases"]["finalization"]["enable_backups"]["operation"]["action"]
        == "none"
    )
    assert (
        report["phases"]["finalization"]["backup_schedule_collision_repair"]["changed"]
        is True
    )
    assert report["phases"]["finalization"]["changed"] is True


def test_switchover_invalid_report_dir_fails_without_writing_report(
    run_switchover_fixture,
):
    completed, report = run_switchover_fixture("invalid_report_dir.yml")
    assert completed.returncode != 0
    assert report == {}
    assert (
        "Path traversal attempt" in completed.stdout
        or "Path traversal attempt" in completed.stderr
    )


def test_switchover_playbook_rejects_restore_only_mode(run_switchover_fixture):
    completed, report = run_switchover_fixture("restore_only_rejected.yml")
    assert completed.returncode != 0
    assert report == {}
    assert (
        "restore_only mode must use" in completed.stdout
        or "restore_only mode must use" in completed.stderr
    )
    assert "Run primary prep" not in completed.stdout
