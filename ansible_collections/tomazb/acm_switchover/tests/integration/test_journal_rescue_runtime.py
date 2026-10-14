"""Shipped-playbook runtime tests for the Argo CD failure rescue under a migration journal.

PR C decisions amendment section 2.2 against fake hubs: the activation failure below is
the fake API's missing Restore API, which fails the activation phase after its entry.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from ansible_collections.tomazb.acm_switchover.tests.integration.argocd_fake_api import application

RUN_ID = "journal-rescue-run"
_VECTORS = Path(__file__).resolve().parents[5] / "tests" / "fixtures" / "r4_04_migration_evidence_vectors.json"
RESTORE_API_PREFIX = "/apis/cluster.open-cluster-management.io/v1beta1/namespaces/open-cluster-management-backup"
WRITE_METHODS = {"POST", "PATCH", "PUT", "DELETE"}


def _journal() -> dict:
    cases = json.loads(_VECTORS.read_text(encoding="utf-8"))["cases"]
    return next(case["input"] for case in cases if case["id"] == "transition-pre-to-completed")["previous"]


def _checkpoint(**operational_data) -> dict:
    data = {
        "argocd_run_id": RUN_ID,
        "argocd_discovery_namespaces": {"primary": ["argocd"], "secondary": ["argocd"]},
        "expected_managed_cluster_names": [],
        "expected_managed_cluster_count": 0,
        "primary_has_observability": False,
        "secondary_has_observability": False,
    }
    data.update(operational_data)
    return {
        "schema_version": "2.0",
        "phase": "primary_prep",
        "phase_status": "pass",
        "completed_phases": ["preflight", "primary_prep"],
        "operational_data": data,
        "operation_identity": {
            "primary_context": "primary-hub",
            "secondary_context": "secondary-hub",
            "primary_cluster_uid": "LIVE-PRIMARY",
            "secondary_cluster_uid": "LIVE-SECONDARY",
            "method": "passive",
            "activation_method": "patch",
            "restore_only": False,
            "old_hub_action": "secondary",
            "collection_version": "",
        },
        "errors": [],
        "report_refs": [],
        "created_at": "2026-10-01T00:00:00+00:00",
        "updated_at": "2026-10-01T00:00:00+00:00",
    }


def _variables(*, manage=True, resume_on_failure=True) -> dict:
    return {
        "acm_switchover_features": {"argocd": {"manage": manage, "resume_on_failure": resume_on_failure}},
        # Extra vars outrank set_fact, so the run_id the checkpoint records is supplied too.
        "acm_switchover_argocd": {"mode": "pause", "namespace": "argocd", "run_id": RUN_ID},
    }


def _paused(name):
    return application("argocd", name, automated=False, run_id=RUN_ID)


def _running(name):
    return application("argocd", name, automated=True)


def _marker(phase="activation"):
    return {"failed_phase": phase, "recorded_at": "2026-10-01T00:00:00+00:00"}


def _output(run) -> str:
    return run.completed.stdout + run.completed.stderr


def _writes(requests):
    return [request for request in requests if request["method"] in WRITE_METHODS]


def _first_index(requests, predicate):
    return next(index for index, request in enumerate(requests) if predicate(request))


def _restore_read(request):
    """A read of the activation phase: its MultiClusterHub or Restore discovery and reads."""
    return request["path"].startswith(
        ("/apis/cluster.open-cluster-management.io", "/apis/operator.open-cluster-management.io")
    )


def test_an_activation_failure_under_a_journal_records_the_marker_then_resumes(run_distinct_hub_playbook):
    journal = _journal()
    run = run_distinct_hub_playbook(
        primary_applications=[_paused("primary-owned")],
        secondary_applications=[_paused("secondary-owned")],
        checkpoint_record=_checkpoint(migration_backups=journal),
        variables=_variables(),
    )
    output = _output(run)

    assert run.completed.returncode != 0, output
    data = run.checkpoint["operational_data"]
    assert data["argocd_repause_required"]["failed_phase"] == "activation"
    assert data["migration_backups"] == journal
    # The retry stays at the failed phase: no rewind to primary_prep, no checkpoint reset.
    assert run.checkpoint["completed_phases"] == ["preflight", "primary_prep"]
    assert run.checkpoint["errors"][-1]["phase"] == "activation"
    # Both hubs resumed, and the original failure is reported together with the outcome.
    assert [patch["name"] for patch in run.secondary_patches] == ["secondary-owned"]
    assert [patch["name"] for patch in run.primary_patches] == ["primary-owned"]
    assert "Argo CD resume-on-failure resumed the paused Applications" in output
    assert "re-pauses them before activation continues" in output
    # Nothing on the journal path is attempted and hidden: no checkpoint reset is even tried.
    assert re.search(
        r"TASK \[Reset primary prep checkpoint after Argo CD resume on failure\] \*+\nskipping", output
    ), output
    assert "ignored=0" in output


def test_a_retry_repauses_both_hubs_before_any_phase_work_and_clears_the_marker(run_distinct_hub_playbook):
    journal = _journal()
    run = run_distinct_hub_playbook(
        primary_applications=[_running("primary-owned")],
        secondary_applications=[_running("secondary-owned")],
        checkpoint_record=_checkpoint(migration_backups=journal, argocd_repause_required=_marker()),
        # Regardless of argocd.manage now; resume_on_failure off, so the retry's own
        # failure leaves the re-paused Applications paused and records no new marker.
        variables=_variables(manage=False, resume_on_failure=False),
    )
    output = _output(run)

    assert run.completed.returncode != 0, output
    assert "argocd_repause_required" not in run.checkpoint["operational_data"]
    assert run.checkpoint["operational_data"]["migration_backups"] == journal
    for hub, requests, patches in (
        ("primary", run.primary_requests, run.primary_patches),
        ("secondary", run.secondary_requests, run.secondary_patches),
    ):
        assert [patch["name"] for patch in patches] == [f"{hub}-owned"]
        annotations = patches[0]["body"]["metadata"]["annotations"]
        assert annotations["acm-switchover.argoproj.io/paused-by"] == RUN_ID
        # The rest of primary preparation (the BackupSchedule pause) is never replayed.
        assert not [request for request in requests if "backupschedules" in request["path"]]
    # The re-pause precedes the activation phase's first read.
    requests = run.secondary_requests
    assert _first_index(requests, lambda r: r["method"] == "PATCH") < _first_index(requests, _restore_read)


def test_a_failed_repause_keeps_the_marker_and_runs_no_phase_work(run_distinct_hub_playbook):
    checkpoint = _checkpoint(migration_backups=_journal(), argocd_repause_required=_marker("post_activation"))
    run = run_distinct_hub_playbook(
        primary_applications=[_running("primary-owned")],
        secondary_applications=[_running("secondary-owned")],
        checkpoint_record=checkpoint,
        variables=_variables(),
        secondary_patch_failure_status=500,
    )
    output = _output(run)

    assert run.completed.returncode != 0, output
    assert "Argo CD re-pause before post_activation failed" in output
    assert "the retry stays at post_activation and re-pauses again" in output
    assert run.checkpoint["operational_data"]["argocd_repause_required"] == _marker("post_activation")
    assert run.checkpoint["completed_phases"] == ["preflight", "primary_prep"]
    # No phase work and no resume of what the failed re-pause left.
    assert not [request for request in run.secondary_requests if _restore_read(request)]
    assert "Resume Argo CD under the migration journal after failure" not in output


@pytest.mark.parametrize("native_check", [False, True])
def test_a_preview_reports_the_repause_and_writes_nothing(run_distinct_hub_playbook, native_check):
    checkpoint = _checkpoint(migration_backups=_journal(), argocd_repause_required=_marker())
    variables = _variables()
    # A dry run proves identity through the non-live override the checkpoint was bound to.
    variables["acm_switchover_test_overrides"] = {
        "non_live_hub_identities": {
            "primary": {"cluster_uid": "LIVE-PRIMARY"},
            "secondary": {"cluster_uid": "LIVE-SECONDARY"},
        }
    }
    run = run_distinct_hub_playbook(
        primary_applications=[_running("primary-owned")],
        secondary_applications=[_running("secondary-owned")],
        checkpoint_record=checkpoint,
        variables=variables,
        mode="execute" if native_check else "dry_run",
        native_check=native_check,
    )
    output = _output(run)

    assert "Would re-pause Argo CD auto-sync on the primary, secondary hub(s) before activation" in output
    assert run.checkpoint_after == run.checkpoint_before
    assert _writes(run.primary_requests) == [] and _writes(run.secondary_requests) == []


def test_without_a_durable_marker_no_application_is_resumed(run_distinct_hub_playbook):
    run = run_distinct_hub_playbook(
        primary_applications=[_paused("primary-owned")],
        secondary_applications=[_paused("secondary-owned")],
        checkpoint_record=_checkpoint(migration_backups=_journal()),
        variables=_variables(),
        checkpoint_dir_read_only=True,
    )
    output = _output(run)

    assert run.completed.returncode != 0, output
    assert "Argo CD resume-on-failure refused" in output
    assert "No Application was resumed" in output
    assert run.checkpoint_after == run.checkpoint_before
    assert run.primary_patches == [] and run.secondary_patches == []


def test_an_invalid_journal_still_takes_the_journal_rescue(run_distinct_hub_playbook):
    invalid = {"schema_version": 9}
    run = run_distinct_hub_playbook(
        primary_applications=[_paused("primary-owned")],
        secondary_applications=[_paused("secondary-owned")],
        checkpoint_record=_checkpoint(migration_backups=invalid),
        variables=_variables(),
    )

    assert run.completed.returncode != 0
    assert run.checkpoint["operational_data"]["argocd_repause_required"]["failed_phase"] == "activation"
    assert run.checkpoint["operational_data"]["migration_backups"] == invalid
    assert run.checkpoint["completed_phases"] == ["preflight", "primary_prep"]


def test_a_restore_only_identity_failure_never_resumes_applications(run_distinct_hub_playbook):
    """Review finding 2: the secondary context now resolves to another cluster. With a journal
    and a recorded marker, the identity refusal must not reach the resume-enabled rescue."""
    checkpoint = _checkpoint(migration_backups=_journal(), argocd_repause_required=_marker())
    checkpoint["operation_identity"].update(
        primary_context="",
        primary_cluster_uid="",
        secondary_cluster_uid="RECORDED-SECONDARY",
        method="full",
        restore_only=True,
        old_hub_action="none",
    )
    checkpoint["completed_phases"] = ["preflight"]
    run = run_distinct_hub_playbook(
        restore_only=True,
        secondary_applications=[_paused("secondary-owned")],
        checkpoint_record=checkpoint,
        variables=_variables(),
    )
    output = _output(run)

    assert run.completed.returncode != 0
    assert "Checkpoint operation identity does not match the current execution" in output
    assert run.secondary_patches == [] and _writes(run.secondary_requests) == []
    assert run.checkpoint_after == run.checkpoint_before


def test_a_visible_marker_whose_write_was_not_acknowledged_resumes_nothing(run_distinct_hub_playbook):
    """Review finding 1: the fail transition's replace lands, its directory fsync fails. The
    marker is visible on a reread but not proven durable, so no Application is resumed."""
    run = run_distinct_hub_playbook(
        primary_applications=[_paused("primary-owned")],
        secondary_applications=[_paused("secondary-owned")],
        checkpoint_record=_checkpoint(migration_backups=_journal()),
        variables=_variables(),
        fail_directory_fsync_after_marker=True,
    )
    output = _output(run)

    assert run.completed.returncode != 0
    assert "directory durability could not be acknowledged" in output
    # The marker is visible, yet the play rescue resumes nothing.
    assert run.checkpoint["operational_data"]["argocd_repause_required"]["failed_phase"] == "activation"
    assert run.primary_patches == [] and run.secondary_patches == []
    assert "Argo CD resume-on-failure refused" in output


def test_a_journal_free_restore_only_identity_failure_never_resumes_applications(run_distinct_hub_playbook):
    """Review finding 2 without a journal: only the playbook structure keeps the journal-free
    resume (and its ignore_errors) away from a hub whose identity failed."""
    checkpoint = _checkpoint()
    checkpoint["operation_identity"].update(
        primary_context="",
        primary_cluster_uid="",
        secondary_cluster_uid="RECORDED-SECONDARY",
        method="full",
        restore_only=True,
        old_hub_action="none",
    )
    checkpoint["completed_phases"] = ["preflight"]
    run = run_distinct_hub_playbook(
        restore_only=True,
        secondary_applications=[_paused("secondary-owned")],
        checkpoint_record=checkpoint,
        variables=_variables(),
    )
    output = _output(run)

    assert run.completed.returncode != 0
    assert "Checkpoint operation identity does not match the current execution" in output
    assert run.secondary_patches == [] and _writes(run.secondary_requests) == []


def test_a_finalization_cleanup_failure_under_a_journal_records_the_marker(run_distinct_hub_playbook):
    """R4-04 Task 10: the journaled Restore cleanup's own refusal takes the same journal rescue."""
    cases = json.loads(_VECTORS.read_text(encoding="utf-8"))["cases"]
    journal = next(case for case in cases if case["id"] == "journal-cleanup-recovery-required")["input"]["candidate"]
    checkpoint = _checkpoint(migration_backups=journal)
    checkpoint.update(
        phase="post_activation", completed_phases=["preflight", "primary_prep", "activation", "post_activation"]
    )

    run = run_distinct_hub_playbook(
        primary_applications=[_paused("primary-owned")],
        secondary_applications=[_paused("secondary-owned")],
        checkpoint_record=checkpoint,
        variables=_variables(),
    )
    output = _output(run)

    assert run.completed.returncode != 0, output
    assert "cleanup_recovery_required" in output
    data = run.checkpoint["operational_data"]
    assert data["argocd_repause_required"]["failed_phase"] == "finalization"
    assert data["migration_backups"] == journal
    assert run.checkpoint["errors"][-1]["phase"] == "finalization"
    assert [patch["name"] for patch in run.primary_patches] == ["primary-owned"]


# -- Compositions: a rescue run, then its retry against the same checkpoint (D-42) ----------------


def _vector_journal(case_id):
    cases = json.loads(_VECTORS.read_text(encoding="utf-8"))["cases"]
    return next(case for case in cases if case["id"] == case_id)["input"]["candidate"]


def _restore_only_checkpoint(**operational_data):
    checkpoint = _checkpoint(**operational_data)
    checkpoint["operation_identity"].update(
        primary_context="",
        primary_cluster_uid="",
        method="full",
        restore_only=True,
        old_hub_action="none",
    )
    checkpoint.update(phase="preflight", completed_phases=["preflight"])
    return checkpoint


def test_a_restore_only_rescue_then_its_retry_repauses_the_secondary_hub_first(run_distinct_hub_playbook):
    journal = _journal()
    rescued = run_distinct_hub_playbook(
        restore_only=True,
        secondary_applications=[_paused("secondary-owned")],
        checkpoint_record=_restore_only_checkpoint(migration_backups=journal),
        variables=_variables(),
    )
    output = _output(rescued)

    assert rescued.completed.returncode != 0, output
    assert rescued.checkpoint["operational_data"]["argocd_repause_required"]["failed_phase"] == "activation"
    assert rescued.checkpoint["completed_phases"] == ["preflight"]
    assert [patch["name"] for patch in rescued.secondary_patches] == ["secondary-owned"]

    # The retry against the same checkpoint: the resumed Application is running again.
    retry = run_distinct_hub_playbook(
        restore_only=True,
        secondary_applications=[_running("secondary-owned")],
        checkpoint_record=None,
        variables=_variables(manage=False, resume_on_failure=False),
    )
    output = _output(retry)

    assert retry.completed.returncode != 0, output
    assert "argocd_repause_required" not in retry.checkpoint["operational_data"]
    assert retry.checkpoint["operational_data"]["migration_backups"] == journal
    assert [patch["name"] for patch in retry.secondary_patches] == ["secondary-owned"]
    assert retry.secondary_patches[0]["body"]["metadata"]["annotations"]["acm-switchover.argoproj.io/paused-by"] == (
        RUN_ID
    )
    assert retry.primary_requests == []
    requests = retry.secondary_requests
    assert _first_index(requests, lambda r: r["method"] == "PATCH") < _first_index(requests, _restore_read)


def test_a_post_activation_rescue_then_its_retry_repauses_before_post_activation(run_distinct_hub_playbook):
    journal = _vector_journal("journal-completed-passive-patch-legacy")
    checkpoint = _checkpoint(migration_backups=journal)
    checkpoint.update(phase="activation", completed_phases=["preflight", "primary_prep", "activation"])
    rescued = run_distinct_hub_playbook(
        primary_applications=[_paused("primary-owned")],
        secondary_applications=[_paused("secondary-owned")],
        checkpoint_record=checkpoint,
        variables=_variables(),
    )
    output = _output(rescued)

    assert rescued.completed.returncode != 0, output
    data = rescued.checkpoint["operational_data"]
    assert data["argocd_repause_required"]["failed_phase"] == "post_activation"
    assert data["migration_backups"] == journal
    assert rescued.checkpoint["completed_phases"] == ["preflight", "primary_prep", "activation"]
    assert rescued.checkpoint["errors"][-1]["phase"] == "post_activation"
    assert [patch["name"] for patch in rescued.primary_patches] == ["primary-owned"]
    assert [patch["name"] for patch in rescued.secondary_patches] == ["secondary-owned"]

    retry = run_distinct_hub_playbook(
        primary_applications=[_running("primary-owned")],
        secondary_applications=[_running("secondary-owned")],
        checkpoint_record=None,
        variables=_variables(manage=False, resume_on_failure=False),
    )
    output = _output(retry)

    assert retry.completed.returncode != 0, output
    assert "argocd_repause_required" not in retry.checkpoint["operational_data"]
    assert retry.checkpoint["operational_data"]["migration_backups"] == journal
    assert retry.checkpoint["completed_phases"] == ["preflight", "primary_prep", "activation"]
    for patches, name in ((retry.primary_patches, "primary-owned"), (retry.secondary_patches, "secondary-owned")):
        assert [patch["name"] for patch in patches] == [name]
    # The re-pause precedes the post-activation phase's first hub read.
    requests = retry.secondary_requests
    assert _first_index(requests, lambda r: r["method"] == "PATCH") < _first_index(requests, _restore_read)


def test_a_partial_resume_keeps_the_marker_and_its_retry_repauses(run_distinct_hub_playbook):
    journal = _journal()
    rescued = run_distinct_hub_playbook(
        primary_applications=[_paused("primary-owned")],
        secondary_applications=[_paused("secondary-owned")],
        checkpoint_record=_checkpoint(migration_backups=journal),
        variables=_variables(),
        primary_patch_failure_status=500,
    )
    output = _output(rescued)

    # The secondary hub resumed, the primary did not: visible, with the obligation kept.
    assert rescued.completed.returncode != 0, output
    assert [patch["name"] for patch in rescued.secondary_patches] == ["secondary-owned"]
    assert rescued.primary_patches == []
    assert "Argo CD resume-on-failure failed" in output
    data = rescued.checkpoint["operational_data"]
    assert data["argocd_repause_required"]["failed_phase"] == "activation"
    assert data["migration_backups"] == journal
    assert rescued.checkpoint["completed_phases"] == ["preflight", "primary_prep"]
    assert "ignored=0" in output

    # The retry finds one hub running and one still paused; it re-pauses before phase work.
    retry = run_distinct_hub_playbook(
        primary_applications=[_paused("primary-owned")],
        secondary_applications=[_running("secondary-owned")],
        checkpoint_record=None,
        variables=_variables(manage=False, resume_on_failure=False),
    )
    output = _output(retry)

    assert retry.completed.returncode != 0, output
    assert "argocd_repause_required" not in retry.checkpoint["operational_data"]
    assert retry.checkpoint["operational_data"]["migration_backups"] == journal
    assert [patch["name"] for patch in retry.secondary_patches] == ["secondary-owned"]
    requests = retry.secondary_requests
    assert _first_index(requests, lambda r: r["method"] == "PATCH") < _first_index(requests, _restore_read)


def test_a_finalization_retry_repauses_then_refuses_an_incomplete_journal(run_distinct_hub_playbook):
    """D-41 composed, as the Python CLI: re-pause first, then refuse the journal lacking post-activation."""
    journal = _vector_journal("journal-completed-passive-patch-legacy")
    checkpoint = _checkpoint(migration_backups=journal, argocd_repause_required=_marker("finalization"))
    checkpoint.update(
        phase="post_activation", completed_phases=["preflight", "primary_prep", "activation", "post_activation"]
    )
    run = run_distinct_hub_playbook(
        primary_applications=[_running("primary-owned")],
        secondary_applications=[_running("secondary-owned")],
        checkpoint_record=checkpoint,
        variables=_variables(manage=False, resume_on_failure=False),
    )
    output = _output(run)

    assert run.completed.returncode != 0, output
    assert "post_activation_not_completed" in output
    # The marker clear after a successful re-pause precedes the completed-marker refusal.
    assert output.index("Clear the Argo CD re-pause marker after the re-pause") < output.index(
        "Require the evidence a completed post_activation marker claims"
    )
    for patches, name in ((run.primary_patches, "primary-owned"), (run.secondary_patches, "secondary-owned")):
        assert [patch["name"] for patch in patches] == [name]
        assert patches[0]["body"]["metadata"]["annotations"]["acm-switchover.argoproj.io/paused-by"] == RUN_ID
    assert "argocd_repause_required" not in run.checkpoint["operational_data"]
    assert run.checkpoint["operational_data"]["migration_backups"] == journal
    # No Restore mutation and no phase read after the refusal.
    assert not [
        request for request in _writes(run.secondary_requests) if request["path"].startswith(RESTORE_API_PREFIX)
    ]
    assert not [request for request in run.secondary_requests if _restore_read(request)]
