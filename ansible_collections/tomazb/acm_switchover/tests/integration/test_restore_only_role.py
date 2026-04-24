"""Integration tests for the restore-only playbook contract."""


def test_restore_only_invalid_report_dir_fails_without_writing_report(run_restore_only_fixture):
    completed, report = run_restore_only_fixture("invalid_report_dir.yml")
    assert completed.returncode != 0
    assert report == {}
    assert "Path traversal attempt" in completed.stdout or "Path traversal attempt" in completed.stderr
