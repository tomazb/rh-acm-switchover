"""Scenario test verifying checkpoint resume skips already-completed phases."""


def test_resume_skips_completed_phases_and_runs_remaining(run_checkpoint_fixture):
    completed, checkpoint = run_checkpoint_fixture(
        "interrupted_after_activation.yml",
        pre_completed_phases=["preflight", "primary_prep", "activation"],
    )
    assert completed.returncode == 0, completed.stderr
    assert checkpoint["completed_phases"] == [
        "preflight",
        "primary_prep",
        "activation",
        "post_activation",
        "finalization",
    ]
