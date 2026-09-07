"""Integration tests for non-core operational roles: discovery, decommission, rbac_bootstrap."""


def test_decommission_dry_run_fixture(run_noncore_fixture):
    completed, summary = run_noncore_fixture("decommission_dry_run.yml", "decommission")
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert summary["phase"] == "decommission"
    assert summary["mode"] == "dry_run"
    assert summary["status"] == "pass"
    assert summary["changed"] is False
    assert summary["would_change"] is True
    assert summary["substeps"] == {}


def test_discovery_bridge_fixture(run_noncore_fixture):
    completed, summary = run_noncore_fixture("discovery_bridge.yml", "discovery")
    assert completed.returncode == 0
    assert summary["playbook"] == "discovery"


def test_rbac_bootstrap_dry_run_fixture(run_noncore_fixture):
    completed, summary = run_noncore_fixture("rbac_bootstrap_dry_run.yml", "rbac_bootstrap")
    assert completed.returncode == 0
    assert summary["mode"] == "dry_run"
