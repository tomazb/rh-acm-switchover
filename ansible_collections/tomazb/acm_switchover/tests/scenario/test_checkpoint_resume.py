"""Scenario test verifying dry-run checkpoint resume behavior."""

import re


def test_dry_run_resume_skips_completed_phases_and_runs_remaining_without_persisting(
    run_checkpoint_fixture,
):
    completed, checkpoint = run_checkpoint_fixture(
        "interrupted_after_activation.yml",
        pre_completed_phases=["preflight", "primary_prep", "activation"],
    )
    assert completed.returncode == 0, completed.stderr
    assert checkpoint["completed_phases"] == [
        "preflight",
        "primary_prep",
        "activation",
    ]
    stdout = completed.stdout
    # Verify skipped phases did not execute: their checkpoint completion should be "skipping".
    for skipped_phase in ("preflight", "primary_prep", "activation"):
        pattern = rf"tomazb\.acm_switchover\.{skipped_phase} : Mark checkpoint phase completion.*\n.*skipping"
        assert re.search(
            pattern, stdout
        ), f"Phase '{skipped_phase}' should have been skipped but checkpoint completion was not skipping"
    # Dry-run mode executes the remaining role logic but intentionally avoids
    # persisting pass/fail checkpoint transitions.
    for ran_phase in ("post_activation", "finalization"):
        changed_pattern = rf"tomazb\.acm_switchover\.{ran_phase} : Mark checkpoint phase completion.*\n.*changed"
        ok_pattern = rf"tomazb\.acm_switchover\.{ran_phase} : Mark checkpoint phase completion.*\n.*ok"
        assert not re.search(
            changed_pattern, stdout
        ), f"Phase '{ran_phase}' should not persist checkpoint completion during dry-run"
        assert re.search(ok_pattern, stdout), f"Phase '{ran_phase}' should have run during dry-run"
