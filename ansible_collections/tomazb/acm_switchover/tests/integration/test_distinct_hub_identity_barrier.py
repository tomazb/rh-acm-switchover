"""Adversarial shipped-playbook tests for the trusted hub identity barrier."""

from __future__ import annotations

import json

import pytest

from ansible_collections.tomazb.acm_switchover.tests.integration.argocd_fake_api import (
    application,
)

IDENTITY_PATH = "/api/v1/namespaces/kube-system"
WRITE_METHODS = {"POST", "PATCH", "PUT", "DELETE"}
EQUAL_UID_REFUSAL = (
    "Primary and secondary hubs resolve to the same physical Kubernetes cluster. "
    "Refusing the normal two-hub switchover."
)
ROLE_REFUSAL = (
    "Unable to verify the {role} hub physical identity from the live kube-system Namespace UID. "
    "Refusing the normal two-hub switchover."
)
SAME_CONTEXT_REFUSAL = "Primary and secondary Kubernetes context names must differ for a normal two-hub switchover."
CHECKPOINT_MISMATCH = "Checkpoint operation identity does not match the current execution."
LEAK_SENTINELS = (
    "ssa01-secret-kubeconfig-KP71",
    "ssa01-secret-token-TK72",
    "ssa01-secret-api-body-BD73",
    "ssa01-secret-raw-exception-EX74",
    "ssa01-secret-uid-UID75",
    "ssa01-secret-context-CTX76",
    "ssa01-secret-credential-CR77",
)


def _malicious_identity_variables(primary_uid: str, secondary_uid: str) -> dict:
    """Supply usable-looking identity through every caller-addressable name."""
    identities = {
        "primary": {"context": "spoof-primary", "cluster_uid": primary_uid},
        "secondary": {"context": "spoof-secondary", "cluster_uid": secondary_uid},
    }
    operational_facts = {
        "expected_managed_cluster_names": [],
        "expected_managed_cluster_count": 0,
        "primary_has_observability": False,
        "secondary_has_observability": False,
    }
    return {
        "_acm_primary_identity_namespace": {
            "resources": [{"metadata": {"uid": primary_uid}}],
        },
        "_acm_secondary_identity_namespace": {
            "resources": [{"metadata": {"uid": secondary_uid}}],
        },
        "acm_switchover_hub_identities": identities,
        "_acm_switchover_verified_hub_identities": identities,
        "acm_switchover_distinct_hubs_verified": True,
        "_checkpoint_enter": {
            "failed": False,
            "skipped_phase": True,
            "facts": operational_facts,
            "hub_identities": identities,
        },
        "acm_input_validation": {
            "failed": False,
            "passed": True,
            "critical_failures": 0,
            "warning_failures": 0,
            "results": [],
        },
        "_acm_identity_barrier_result": {
            "failed": False,
            "passed": True,
            "hub_identities": identities,
        },
        "hub_identities": identities,
        "trusted_uids": identities,
        "trusted_local_hub_identities": identities,
        "sanitized_local_hubs": {
            "primary": {"context": "spoof-primary"},
            "secondary": {"context": "spoof-secondary"},
        },
        "expected_operation_identity": {
            "primary_cluster_uid": primary_uid,
            "secondary_cluster_uid": secondary_uid,
        },
        "acm_switchover_preflight_result": {"status": "pass"},
        "acm_switchover_report": {"identity": identities, "status": "pass"},
        "acm_switchover_primary_prep_result": {"status": "pass"},
        "acm_switchover_hubs": {
            "primary": {"cluster_uid": primary_uid},
            "secondary": {"cluster_uid": secondary_uid},
        },
        "acm_switchover_test_overrides": {
            "non_live_hub_identities": identities,
        },
    }


def _identity_gets(requests: list[dict]) -> list[dict]:
    return [request for request in requests if request == {"method": "GET", "path": IDENTITY_PATH}]


def _write_requests(run) -> list[dict]:
    return [request for request in run.primary_requests + run.secondary_requests if request["method"] in WRITE_METHODS]


def _visible_output(run) -> str:
    return run.completed.stdout + run.completed.stderr + json.dumps(run.report, sort_keys=True)


def _schema_2_checkpoint(
    *,
    primary_uid: str,
    secondary_uid: str,
    completed_phases: list[str] | None = None,
    operational_data: dict | None = None,
) -> dict:
    return {
        "schema_version": "2.0",
        "phase": "preflight",
        "phase_status": "pass" if completed_phases else "enter",
        "completed_phases": list(completed_phases or []),
        "operational_data": dict(operational_data or {}),
        "operation_identity": {
            "primary_context": "primary-hub",
            "secondary_context": "secondary-hub",
            "primary_cluster_uid": primary_uid,
            "secondary_cluster_uid": secondary_uid,
            "method": "passive",
            "activation_method": "patch",
            "restore_only": False,
            "old_hub_action": "secondary",
            "collection_version": "",
        },
        "errors": [],
        "report_refs": [],
        "created_at": "2026-08-20T00:00:00+00:00",
        "updated_at": "2026-08-20T00:00:00+00:00",
    }


def test_same_live_cluster_rejects_spoofed_distinct_extra_vars(
    run_distinct_hub_playbook,
):
    run = run_distinct_hub_playbook(
        primary_uid="LIVE-SAME",
        secondary_uid="LIVE-SAME",
        variables=_malicious_identity_variables("FAKE-A", "FAKE-B"),
    )
    output = _visible_output(run)

    assert run.completed.returncode != 0
    assert EQUAL_UID_REFUSAL in output
    assert len(_identity_gets(run.primary_requests)) == 1
    assert len(_identity_gets(run.secondary_requests)) == 1
    assert run.checkpoint_after is None
    assert "Mark checkpoint phase completion" not in output
    assert "Run primary prep" not in output
    assert "Reset primary prep checkpoint after Argo CD resume on failure" not in output
    assert _write_requests(run) == []


def test_missing_execution_mode_uses_execute_identity_freshness(
    run_distinct_hub_playbook,
):
    run = run_distinct_hub_playbook(
        primary_uid="LIVE-SAME",
        secondary_uid="LIVE-SAME",
        omit_execution_mode=True,
        checkpoint_enabled=False,
        variables=_malicious_identity_variables("OVERRIDE-A", "OVERRIDE-B"),
    )
    output = _visible_output(run)

    assert run.completed.returncode != 0
    assert EQUAL_UID_REFUSAL in output
    assert len(_identity_gets(run.primary_requests)) == 1
    assert len(_identity_gets(run.secondary_requests)) == 1
    assert run.checkpoint_after is None
    assert _write_requests(run) == []


def test_same_context_refusal_report_artifact_omits_sensitive_inputs(
    run_distinct_hub_playbook,
):
    context_with_sentinels = "|".join(LEAK_SENTINELS)
    run = run_distinct_hub_playbook(
        primary_context=context_with_sentinels,
        secondary_context=context_with_sentinels,
        primary_kubeconfig_name="ssa01-secret-kubeconfig-KP71-primary",
        secondary_kubeconfig_name="ssa01-secret-kubeconfig-KP71-secondary",
        primary_uid="ssa01-secret-uid-UID75",
        secondary_uid="ssa01-secret-uid-UID75",
    )
    serialized = json.dumps(run.preflight_report, sort_keys=True)

    assert run.completed.returncode != 0
    assert run.preflight_report["status"] == "fail"
    assert SAME_CONTEXT_REFUSAL in serialized
    leaked = [sentinel for sentinel in LEAK_SENTINELS if sentinel in serialized]
    assert leaked == []
    assert run.primary_requests == []
    assert run.secondary_requests == []
    assert _write_requests(run) == []


@pytest.mark.parametrize(
    ("failed_role", "primary_gets", "secondary_gets"),
    [("primary", 1, 0), ("secondary", 1, 1)],
)
def test_unavailable_live_uid_rejects_spoofed_identity(
    run_distinct_hub_playbook,
    failed_role,
    primary_gets,
    secondary_gets,
):
    failure_body = {
        "apiVersion": "v1",
        "kind": "Status",
        "status": "Failure",
        "reason": "InternalError",
        "code": 503,
        "message": " ".join(LEAK_SENTINELS),
        "details": {
            "uid": "ssa01-secret-uid-UID75",
            "exception": "ssa01-secret-raw-exception-EX74",
        },
    }
    run = run_distinct_hub_playbook(
        variables=_malicious_identity_variables("FAKE-A", "FAKE-B"),
        primary_context=("ssa01-secret-context-CTX76" if failed_role == "primary" else "primary-hub"),
        secondary_context=("ssa01-secret-context-CTX76" if failed_role == "secondary" else "secondary-hub"),
        primary_kubeconfig_name=("ssa01-secret-kubeconfig-KP71" if failed_role == "primary" else "primary.kubeconfig"),
        secondary_kubeconfig_name=(
            "ssa01-secret-kubeconfig-KP71" if failed_role == "secondary" else "secondary.kubeconfig"
        ),
        primary_identity_status=(503 if failed_role == "primary" else 200),
        secondary_identity_status=(503 if failed_role == "secondary" else 200),
        primary_identity_body=(failure_body if failed_role == "primary" else None),
        secondary_identity_body=(failure_body if failed_role == "secondary" else None),
    )
    visible = _visible_output(run)

    assert run.completed.returncode != 0
    assert ROLE_REFUSAL.format(role=failed_role) in visible
    assert len(_identity_gets(run.primary_requests)) == primary_gets
    assert len(_identity_gets(run.secondary_requests)) == secondary_gets
    assert _write_requests(run) == []
    for sentinel in LEAK_SENTINELS:
        assert sentinel not in visible


@pytest.mark.parametrize("drift_role", ["primary", "secondary"])
def test_checkpoint_drift_rejects_spoofed_stored_identity(
    run_distinct_hub_playbook,
    drift_role,
):
    stored = {"primary": "STORED-A", "secondary": "STORED-B"}
    live = dict(stored)
    live[drift_role] = f"LIVE-DRIFT-{drift_role.upper()}"
    checkpoint = _schema_2_checkpoint(
        primary_uid=stored["primary"],
        secondary_uid=stored["secondary"],
        completed_phases=["preflight"],
    )
    run = run_distinct_hub_playbook(
        primary_uid=live["primary"],
        secondary_uid=live["secondary"],
        checkpoint_record=checkpoint,
        variables=_malicious_identity_variables(stored["primary"], stored["secondary"]),
    )
    output = _visible_output(run)

    assert run.completed.returncode != 0
    assert CHECKPOINT_MISMATCH in output
    assert len(_identity_gets(run.primary_requests)) == 1
    assert len(_identity_gets(run.secondary_requests)) == 1
    assert run.checkpoint_after == run.checkpoint_before
    assert run.checkpoint == checkpoint
    assert _write_requests(run) == []


def test_pre_barrier_failure_ignores_spoofed_recovery_values(
    run_distinct_hub_playbook,
):
    variables = _malicious_identity_variables("FAKE-A", "FAKE-B")
    variables.update(
        {
            "acm_switchover_features": {
                "argocd": {"manage": True, "resume_on_failure": True},
            },
            "acm_switchover_argocd": {"run_id": "spoofed-recovery-run"},
            "acm_switchover_argocd_installed": True,
            "acm_switchover_argocd_discovery_status": {"status": "ok"},
            "acm_switchover_argocd_all_apps": [
                application("argocd", "spoofed", automated=False, run_id="spoofed-recovery-run")
            ],
            "acm_switchover_argocd_acm_apps": [
                application("argocd", "spoofed", automated=False, run_id="spoofed-recovery-run")
            ],
            "acm_switchover_argocd_summary": {"paused": 1, "restored": 1},
        }
    )
    run = run_distinct_hub_playbook(
        primary_identity_status=503,
        primary_identity_body={
            "kind": "Status",
            "status": "Failure",
            "reason": "InternalError",
            "code": 503,
            "message": "trusted identity is unavailable",
        },
        variables=variables,
    )
    output = _visible_output(run)

    assert run.completed.returncode != 0
    assert ROLE_REFUSAL.format(role="primary") in output
    assert "Attempt Argo CD resume on secondary hub after failure" not in output
    assert "Attempt Argo CD resume on primary hub after failure" not in output
    assert "Reset primary prep checkpoint after Argo CD resume on failure" not in output
    assert _write_requests(run) == []


def test_post_barrier_failure_retains_recovery(run_distinct_hub_playbook):
    run_id = "controlled-recovery-run"
    run = run_distinct_hub_playbook(
        primary_applications=[
            application("argocd", "primary-owned", automated=False, run_id=run_id),
        ],
        secondary_applications=[
            application("argocd", "secondary-owned", automated=False, run_id=run_id),
        ],
        variables={
            "_checkpoint_enter": {"skipped_phase": True, "facts": {}},
            "acm_switchover_features": {
                "argocd": {"manage": True, "resume_on_failure": True},
            },
            "acm_switchover_argocd": {
                "mode": "pause",
                "run_id": run_id,
                "namespace": "argocd",
            },
            "acm_switchover_argocd_discovery_namespaces": {
                "primary": ["argocd"],
                "secondary": ["argocd"],
            },
        },
    )
    output = _visible_output(run)

    assert run.completed.returncode != 0
    assert "Skipped preflight checkpoint is missing required operational metadata" in output
    assert "Attempt Argo CD resume on secondary hub after failure" in output
    assert "Attempt Argo CD resume on primary hub after failure" in output
    assert "Reset primary prep checkpoint after Argo CD resume on failure" in output
    assert len(_identity_gets(run.primary_requests)) == 1
    assert len(_identity_gets(run.secondary_requests)) == 1
    assert run.primary_patches == [
        {
            "namespace": "argocd",
            "name": "primary-owned",
            "changed": True,
            "body": {
                "apiVersion": "argoproj.io/v1alpha1",
                "kind": "Application",
                "metadata": {
                    "name": "primary-owned",
                    "namespace": "argocd",
                    "resourceVersion": "1",
                    "annotations": {
                        "acm-switchover.argoproj.io/paused-by": None,
                        "acm-switchover.argoproj.io/original-sync-policy": None,
                    },
                },
                "spec": {
                    "syncPolicy": {
                        "automated": {"prune": True, "selfHeal": True},
                    }
                },
            },
        }
    ]
    assert run.secondary_patches == [
        {
            "namespace": "argocd",
            "name": "secondary-owned",
            "changed": True,
            "body": {
                "apiVersion": "argoproj.io/v1alpha1",
                "kind": "Application",
                "metadata": {
                    "name": "secondary-owned",
                    "namespace": "argocd",
                    "resourceVersion": "1",
                    "annotations": {
                        "acm-switchover.argoproj.io/paused-by": None,
                        "acm-switchover.argoproj.io/original-sync-policy": None,
                    },
                },
                "spec": {
                    "syncPolicy": {
                        "automated": {"prune": True, "selfHeal": True},
                    }
                },
            },
        }
    ]
    assert run.primary_requests.index({"method": "GET", "path": IDENTITY_PATH}) < next(
        index for index, request in enumerate(run.primary_requests) if request["method"] == "PATCH"
    )
    assert run.secondary_requests.index({"method": "GET", "path": IDENTITY_PATH}) < next(
        index for index, request in enumerate(run.secondary_requests) if request["method"] == "PATCH"
    )
    assert run.checkpoint["phase"] == "primary_prep"
    assert run.checkpoint["phase_status"] == "reset"


@pytest.mark.parametrize(
    ("secondary_uid", "expect_refusal"),
    [("LIVE-SECONDARY", False), ("LIVE-PRIMARY", True)],
)
def test_execute_check_mode_uses_fresh_uids_without_mutation(
    run_distinct_hub_playbook,
    secondary_uid,
    expect_refusal,
):
    run = run_distinct_hub_playbook(
        primary_uid="LIVE-PRIMARY",
        secondary_uid=secondary_uid,
        native_check=True,
        variables=_malicious_identity_variables("STALE-A", "STALE-B"),
    )
    output = _visible_output(run)

    assert len(_identity_gets(run.primary_requests)) == 1
    assert len(_identity_gets(run.secondary_requests)) == 1
    assert (EQUAL_UID_REFUSAL in output) is expect_refusal
    assert ("Run remaining preflight validation" in output) is not expect_refusal
    if expect_refusal:
        assert run.completed.returncode != 0
    assert run.checkpoint_after is None
    assert _write_requests(run) == []


def test_checkpoint_disabled_still_rejects_live_uid_equality(
    run_distinct_hub_playbook,
):
    run = run_distinct_hub_playbook(
        primary_uid="LIVE-SAME",
        secondary_uid="LIVE-SAME",
        checkpoint_enabled=False,
        variables=_malicious_identity_variables("FAKE-A", "FAKE-B"),
    )

    assert run.completed.returncode != 0
    assert EQUAL_UID_REFUSAL in _visible_output(run)
    assert len(_identity_gets(run.primary_requests)) == 1
    assert len(_identity_gets(run.secondary_requests)) == 1
    assert run.checkpoint_after is None
    assert _write_requests(run) == []


def test_dry_run_uses_explicit_override_without_api_writes(
    run_distinct_hub_playbook,
):
    variables = _malicious_identity_variables("OVERRIDE-A", "OVERRIDE-B")
    variables["_checkpoint_enter"] = {
        "skipped_phase": True,
        "facts": {
            "expected_managed_cluster_names": [],
            "expected_managed_cluster_count": 0,
            "primary_has_observability": False,
            "secondary_has_observability": False,
        },
    }
    run = run_distinct_hub_playbook(
        primary_uid="LIVE-SAME",
        secondary_uid="LIVE-SAME",
        mode="dry_run",
        checkpoint_enabled=False,
        variables=variables,
    )
    output = _visible_output(run)

    assert run.completed.returncode == 0, output
    assert EQUAL_UID_REFUSAL not in output
    assert _identity_gets(run.primary_requests) == []
    assert _identity_gets(run.secondary_requests) == []
    assert _write_requests(run) == []


def test_completed_preflight_rereads_before_using_skipped_phase(
    run_distinct_hub_playbook,
):
    checkpoint = _schema_2_checkpoint(
        primary_uid="LIVE-PRIMARY",
        secondary_uid="LIVE-SECONDARY",
        completed_phases=["preflight"],
        operational_data={
            "expected_managed_cluster_names": [],
            "expected_managed_cluster_count": 0,
            "primary_has_observability": False,
            "secondary_has_observability": False,
        },
    )
    run = run_distinct_hub_playbook(
        checkpoint_record=checkpoint,
        native_check=True,
    )
    output = _visible_output(run)

    assert len(_identity_gets(run.primary_requests)) == 1
    assert len(_identity_gets(run.secondary_requests)) == 1
    barrier_task = "Enter checkpointed phase"
    skipped_validation_task = "Validate required checkpoint data when preflight is skipped"
    skipped_restore_task = "Restore operational facts from checkpoint when preflight is skipped"
    assert output.index(barrier_task) < output.index(skipped_validation_task) < output.index(skipped_restore_task)
    assert run.checkpoint_after == run.checkpoint_before
    assert _write_requests(run) == []
