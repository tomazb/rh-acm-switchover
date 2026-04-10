"""Integration tests for switchover execution roles."""


def test_primary_prep_and_activation_fixture_pass(run_switchover_fixture):
    completed, report = run_switchover_fixture("passive_activation_success.yml")
    assert completed.returncode == 0
    assert report.get("phases", {}).get("primary_prep", {}).get("status") == "pass"
    assert report.get("phases", {}).get("activation", {}).get("status") == "pass"
