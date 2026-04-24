"""Scenario tests verifying end-to-end switchover report contract."""


def test_core_switchover_fixture_emits_all_phase_reports(run_switchover_fixture):
    completed, report = run_switchover_fixture("finalization_backup_recovery.yml")
    assert completed.returncode == 0
    assert set(report["phases"]) >= {"primary_prep", "activation", "post_activation", "finalization"}
