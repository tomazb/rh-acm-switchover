"""Integration tests for non-core operational roles: discovery, decommission, rbac_bootstrap."""


def test_decommission_dry_run_fixture(run_noncore_fixture):
    completed, summary = run_noncore_fixture("decommission_dry_run.yml", "decommission")
    assert completed.returncode == 0
    assert summary["phase"] == "decommission"
    assert summary["mode"] == "dry_run"
