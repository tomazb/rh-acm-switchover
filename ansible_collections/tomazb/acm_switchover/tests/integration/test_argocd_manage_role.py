"""Integration tests for the argocd_manage role contract."""


def test_argocd_pause_and_resume_fixture(run_argocd_fixture):
    completed, summary = run_argocd_fixture("pause_and_resume.yml")
    assert completed.returncode == 0, completed.stderr
    assert summary["paused"] >= 1
    assert summary["restored"] >= 1
