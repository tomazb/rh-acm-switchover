"""Integration tests for the argocd_manage role contract."""


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
