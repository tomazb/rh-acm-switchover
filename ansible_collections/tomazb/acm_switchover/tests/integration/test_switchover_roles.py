"""Integration tests for switchover execution roles."""


def test_primary_prep_and_activation_fixture_pass(run_switchover_fixture):
    completed, report = run_switchover_fixture("passive_activation_success.yml")
    assert completed.returncode == 0
    assert report.get("phases", {}).get("primary_prep", {}).get("status") == "pass"
    assert report.get("phases", {}).get("activation", {}).get("status") == "pass"


def test_post_activation_failure_reports_pending_clusters(run_switchover_fixture):
    completed, report = run_switchover_fixture("post_activation_cluster_failure.yml")
    assert completed.returncode == 0
    assert report["phases"]["post_activation"]["status"] == "skipped"
    assert report["phases"]["post_activation"]["reason"] == "dry_run"


def test_restore_activation_fixture_reports_delete_and_create_plan(run_switchover_fixture):
    completed, report = run_switchover_fixture("restore_activation_success.yml")
    assert completed.returncode == 0
    assert report["phases"]["activation"]["status"] == "pass"
    assert report["phases"]["activation"]["operation"]["action"] == "delete_and_create"
    assert report["phases"]["activation"]["wait_target"]["name"] == "restore-acm-activate"


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
    assert report["phases"]["finalization"]["enable_backups"]["operation"]["action"] == "patch"


def test_finalization_reports_no_change_when_backup_enable_is_already_satisfied(run_switchover_fixture):
    completed, report = run_switchover_fixture("finalization_noop.yml")
    assert completed.returncode == 0
    assert report["phases"]["finalization"]["status"] == "pass"
    assert report["phases"]["finalization"]["enable_backups"]["operation"]["action"] == "none"
    assert report["phases"]["finalization"]["changed"] is False


def test_switchover_invalid_report_dir_fails_without_writing_report(run_switchover_fixture):
    completed, report = run_switchover_fixture("invalid_report_dir.yml")
    assert completed.returncode != 0
    assert report == {}
    assert "Path traversal attempt" in completed.stdout or "Path traversal attempt" in completed.stderr
