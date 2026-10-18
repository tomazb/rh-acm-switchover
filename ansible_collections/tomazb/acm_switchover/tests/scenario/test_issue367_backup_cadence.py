"""A long-cadence execute route completes without waiting when a healthy Backup exists."""

from ansible_collections.tomazb.acm_switchover.tests.integration.test_backup_cadence_runtime import (
    BACKUP_NAME,
    BACKUP_PATH,
    backup_api,
    run_backup_verification,
)


def test_issue367_weekly_cadence_reaches_terminal_backup_pass(tmp_path):
    with backup_api() as (server, requests):
        completed, facts = run_backup_verification(tmp_path, server, cron="5 2 * * 1")
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert int(facts["timeout"]) == 605400
    assert facts["result"]["status"] == "pass"
    assert int(facts["result"]["backup_count"]) == 1
    assert facts["result"]["enabled_at"] == "2026-10-17T12:00:00Z"
    assert BACKUP_PATH in requests
    assert f"{BACKUP_PATH}/{BACKUP_NAME}" in requests
