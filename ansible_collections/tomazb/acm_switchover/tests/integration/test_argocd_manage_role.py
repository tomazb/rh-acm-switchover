"""Integration tests for the argocd_manage role contract."""

import pytest


def test_argocd_pause_and_resume_fixture(run_argocd_fixture):
    completed, summary = run_argocd_fixture("pause_and_resume.yml")
    assert completed.returncode == 0, completed.stderr
    assert summary["paused"] >= 1
    assert summary["restored"] >= 1


def test_argocd_restore_only_fixture(run_argocd_fixture):
    """Restore-only scenario: pause/resume with Policy kind app."""
    completed, summary = run_argocd_fixture("restore_only_pause.yml")
    assert completed.returncode == 0, completed.stderr
    assert summary["paused"] >= 1
    assert summary["restored"] >= 1


def test_argocd_re_pause_clobber_fixture(run_argocd_fixture):
    """Re-pause scenario: already-paused app should be skipped, fresh app paused."""
    completed, summary = run_argocd_fixture("re_pause_clobber.yml")
    assert completed.returncode == 0, completed.stderr
    # At least the fresh app should be paused; already-paused app should be skipped
    assert summary["paused"] >= 1
    assert summary["restored"] >= 1


def test_argocd_applicationset_child_blocks_managed_pause(run_argocd_fixture):
    completed, summary = run_argocd_fixture("applicationset_blocked.yml")

    assert completed.returncode != 0
    output = completed.stdout + completed.stderr
    assert "ApplicationSet parent-set" in output
    assert "pause/update the ApplicationSet" in output
    assert summary == {}


def test_argocd_resume_with_rehydrated_discovery_namespaces_fixture(run_argocd_fixture):
    """Resume must still succeed when trusted namespace hints are pre-seeded."""
    completed, summary = run_argocd_fixture("resume_with_discovery_namespaces.yml")
    assert completed.returncode == 0, completed.stderr
    assert summary["restored"] >= 1


# The fixtures above set no execution mode; the examples inventory they run with pins
# acm_switchover_execution.mode to execute (examples/group_vars/all.yml), so they count
# applied pauses and restores. A preview mode applies nothing (#284 decision 6): the
# applied counts stay zero and the planned counts carry what execute would change.
@pytest.mark.parametrize("mode", ["validate", "dry_run"])
@pytest.mark.parametrize(
    "fixture_name",
    ["pause_and_resume.yml", "restore_only_pause.yml", "re_pause_clobber.yml"],
)
def test_argocd_mock_fixture_previews_without_applying(run_argocd_fixture, fixture_name, mode):
    completed, summary = run_argocd_fixture(fixture_name, {"acm_switchover_execution": {"mode": mode}})
    assert completed.returncode == 0, completed.stderr
    assert summary["paused"] == 0
    assert summary["restored"] == 0
    assert summary["planned_pause"] >= 1
    assert summary["planned_resume"] >= 1


def test_argocd_mock_fixture_execute_summary_keeps_its_keys(run_argocd_fixture):
    completed, summary = run_argocd_fixture("pause_and_resume.yml")
    assert completed.returncode == 0, completed.stderr
    assert set(summary) == {"paused", "restored"}
