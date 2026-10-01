"""Shipped-playbook runtime tests for the Argo CD failure rescue under a migration journal.

PR C decisions amendment section 2.2 against fake hubs: the activation failure below is
the fake API's missing Restore API, which fails the activation phase after its entry.
"""

from __future__ import annotations

import json
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
    return request["path"].startswith("/apis/cluster.open-cluster-management.io")


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
    # The re-pause precedes the activation phase's first Restore read.
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
