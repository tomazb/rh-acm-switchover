"""Tests for the acm_input_validate collection module."""

from ansible_collections.tomazb.acm_switchover.plugins.modules.acm_input_validate import (
    build_input_validation_results,
    summarize_input_validation,
)


def test_missing_secondary_context_fails_execute_mode():
    results = build_input_validation_results(
        {
            "hubs": {
                "primary": {"context": "primary-hub", "kubeconfig": "~/.kube/config"},
                "secondary": {"context": "", "kubeconfig": "~/.kube/config"},
            },
            "operation": {"method": "passive", "activation_method": "patch"},
            "execution": {"mode": "execute", "checkpoint": {"path": ".state/run.json"}},
            "features": {"argocd": {"manage": False, "resume_after_switchover": False}},
        }
    )
    assert any(item["id"] == "preflight-input-secondary-context" for item in results)
    assert any(item["status"] == "fail" for item in results)


def test_restore_requires_passive_method():
    results = build_input_validation_results(
        {
            "hubs": {
                "primary": {"context": "primary-hub", "kubeconfig": "~/.kube/config"},
                "secondary": {"context": "secondary-hub", "kubeconfig": "~/.kube/config"},
            },
            "operation": {"method": "full", "activation_method": "restore"},
            "execution": {"mode": "execute", "checkpoint": {"path": ".state/run.json"}},
            "features": {"argocd": {"manage": False, "resume_after_switchover": False}},
        }
    )
    assert any(item["id"] == "preflight-input-operation" for item in results)
    assert any("requires method=passive" in item["message"] for item in results)


def test_safe_paths_and_valid_contexts_pass():
    results = build_input_validation_results(
        {
            "hubs": {
                "primary": {"context": "admin/api.cluster.local:6443", "kubeconfig": "./kubeconfigs/primary"},
                "secondary": {"context": "secondary-hub", "kubeconfig": "./kubeconfigs/secondary"},
            },
            "operation": {"method": "passive", "activation_method": "patch"},
            "execution": {"mode": "dry_run", "checkpoint": {"path": ".state/run.json"}},
            "features": {"argocd": {"manage": True, "resume_after_switchover": True}},
        }
    )
    assert all(item["status"] == "pass" for item in results)


def test_summary_marks_critical_failures():
    summary = summarize_input_validation(
        [
            {
                "id": "preflight-input-secondary-context",
                "severity": "critical",
                "status": "fail",
                "message": "secondary context is required",
                "details": {},
                "recommended_action": "Set acm_switchover_hubs.secondary.context",
            }
        ]
    )
    assert summary["passed"] is False
    assert summary["critical_failures"] == 1


def test_missing_secondary_context_uses_actionable_message_for_nonstandard_modes():
    results = build_input_validation_results(
        {
            "hubs": {
                "primary": {"context": "primary-hub", "kubeconfig": "~/.kube/config"},
                "secondary": {"context": "", "kubeconfig": "~/.kube/config"},
            },
            "operation": {"method": "passive", "activation_method": "patch"},
            "execution": {"mode": "report_only", "checkpoint": {"path": ".state/run.json"}},
            "features": {"argocd": {"manage": False, "resume_after_switchover": False}},
        }
    )

    secondary_result = next(item for item in results if item["id"] == "preflight-input-secondary-context")
    assert secondary_result["status"] == "fail"
    assert secondary_result["message"] == "secondary context is required for collection preflight and switchover runs"
    assert secondary_result["recommended_action"] == "Set acm_switchover_hubs.secondary.context"
