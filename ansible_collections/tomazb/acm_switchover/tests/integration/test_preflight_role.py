"""Integration tests for the preflight role contract."""


def test_preflight_input_failure_writes_report_and_fails(run_preflight_fixture):
    completed, report = run_preflight_fixture("input_failure.yml")
    assert completed.returncode != 0
    assert report["phase"] == "preflight"
    assert report["status"] == "fail"
    assert any(item["id"] == "preflight-input-secondary-context" for item in report["results"])


def test_preflight_success_fixture_passes(run_preflight_fixture):
    completed, report = run_preflight_fixture("passive_success.yml")
    assert completed.returncode == 0
    assert report["status"] == "pass"
    assert any(item["id"] == "preflight-version-compatibility" for item in report["results"])


def test_preflight_version_mismatch_fails(run_preflight_fixture):
    completed, report = run_preflight_fixture("version_mismatch.yml")
    assert completed.returncode != 0
    assert report["status"] == "fail"
    assert any(
        item["id"] == "preflight-version-compatibility" and item["status"] == "fail" for item in report["results"]
    )


def test_preflight_backup_failure_is_reported(run_preflight_fixture):
    completed, report = run_preflight_fixture("backup_failure.yml")
    assert completed.returncode != 0
    assert report["status"] == "fail"
    result_ids = {item["id"] for item in report["results"]}
    assert "preflight-backup-latest" in result_ids
    assert "preflight-backup-schedule" in result_ids
    assert "preflight-backup-storage-location-primary" in result_ids
    assert "preflight-backup-storage-location-secondary" in result_ids
    assert "preflight-passive-restore-secondary" in result_ids
    assert "preflight-clusterdeployments" in result_ids
    assert "preflight-managed-cluster-backups" in result_ids


def test_preflight_fixture_without_execution_block_uses_defaults(run_preflight_fixture):
    completed, report = run_preflight_fixture("missing_execution_block.yml")
    assert completed.returncode == 0
    assert report["status"] == "pass"


def test_preflight_invalid_report_dir_fails_without_writing_report(run_preflight_fixture):
    completed, report = run_preflight_fixture("invalid_report_dir.yml")
    assert completed.returncode != 0
    assert report == {}
    assert "Path traversal attempt" in completed.stdout or "Path traversal attempt" in completed.stderr
