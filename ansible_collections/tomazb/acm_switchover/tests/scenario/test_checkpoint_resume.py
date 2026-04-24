"""Scenario test verifying checkpoint resume skips already-completed phases."""

import re


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
    stdout = completed.stdout
    # Verify skipped phases did not execute — their checkpoint completion should be "skipping"
    for skipped_phase in ("preflight", "primary_prep", "activation"):
        pattern = rf"tomazb\.acm_switchover\.{skipped_phase} : Mark checkpoint phase completion.*\n.*skipping"
        assert re.search(
            pattern, stdout
        ), f"Phase '{skipped_phase}' should have been skipped but checkpoint completion was not skipping"
    # Verify remaining phases DID execute — their checkpoint completion should be "changed"
    for ran_phase in ("post_activation", "finalization"):
        pattern = rf"tomazb\.acm_switchover\.{ran_phase} : Mark checkpoint phase completion.*\n.*changed"
        assert re.search(
            pattern, stdout
        ), f"Phase '{ran_phase}' should have run but checkpoint completion was not changed"
