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


# ---------------------------------------------------------------------------
# restore_only validation
# ---------------------------------------------------------------------------


def _restore_only_params(**overrides):
    """Build a minimal valid restore_only param set, with optional overrides."""
    params = {
        "hubs": {
            "primary": {"context": "", "kubeconfig": ""},
            "secondary": {"context": "new-hub", "kubeconfig": "~/.kube/config"},
        },
        "operation": {"restore_only": True, "activation_method": "patch"},
        "execution": {"mode": "execute", "checkpoint": {"path": ".state/run.json"}},
        "features": {"argocd": {"manage": False, "resume_after_switchover": False}},
    }
    for key, value in overrides.items():
        # support dotted paths like "hubs.primary.context"
        parts = key.split(".")
        target = params
        for part in parts[:-1]:
            target = target[part]
        target[parts[-1]] = value
    return params


def test_restore_only_valid_inputs_pass():
    """Minimal valid restore-only scenario: empty primary, valid secondary."""
    results = build_input_validation_results(_restore_only_params())
    assert all(item["status"] == "pass" for item in results), (
        f"Expected all pass, got: {[r for r in results if r['status'] != 'pass']}"
    )
    # Normalized operation values
    op_result = next(r for r in results if r["id"] == "preflight-input-operation")
    assert op_result["details"]["method"] == "full"
    assert op_result["details"]["old_hub_action"] == "none"
    assert op_result["details"]["restore_only"] is True


def test_restore_only_rejects_primary_context():
    """Primary context must be empty in restore-only mode."""
    results = build_input_validation_results(
        _restore_only_params(**{"hubs.primary.context": "old-hub"})
    )
    primary_result = next(r for r in results if r["id"] == "preflight-input-primary-context")
    assert primary_result["status"] == "fail"
    assert "restore_only" in primary_result["message"]


def test_restore_only_rejects_missing_secondary_context():
    """Secondary context is required in restore-only mode."""
    results = build_input_validation_results(
        _restore_only_params(**{"hubs.secondary.context": ""})
    )
    secondary_result = next(r for r in results if r["id"] == "preflight-input-secondary-context")
    assert secondary_result["status"] == "fail"


def test_restore_only_rejects_method_passive():
    """Passive method requires a live primary — incompatible with restore-only."""
    results = build_input_validation_results(
        _restore_only_params(**{"operation.method": "passive"})
    )
    op_result = next(r for r in results if r["id"] == "preflight-input-operation")
    assert op_result["status"] == "fail"
    assert "method" in op_result["message"]


def test_restore_only_accepts_method_full_explicit():
    """Explicitly setting method=full is fine in restore-only mode."""
    results = build_input_validation_results(
        _restore_only_params(**{"operation.method": "full"})
    )
    op_result = next(r for r in results if r["id"] == "preflight-input-operation")
    assert op_result["status"] == "pass"
    assert op_result["details"]["method"] == "full"


def test_restore_only_rejects_old_hub_action_secondary():
    """old_hub_action must be none — no old hub to manage."""
    results = build_input_validation_results(
        _restore_only_params(**{"operation.old_hub_action": "secondary"})
    )
    op_result = next(r for r in results if r["id"] == "preflight-input-operation")
    assert op_result["status"] == "fail"
    assert "old_hub_action" in op_result["message"]


def test_restore_only_rejects_old_hub_action_decommission():
    """old_hub_action=decommission is also rejected in restore-only."""
    results = build_input_validation_results(
        _restore_only_params(**{"operation.old_hub_action": "decommission"})
    )
    op_result = next(r for r in results if r["id"] == "preflight-input-operation")
    assert op_result["status"] == "fail"
    assert "old_hub_action" in op_result["message"]


def test_restore_only_accepts_old_hub_action_none_explicit():
    """Explicitly setting old_hub_action=none is fine."""
    results = build_input_validation_results(
        _restore_only_params(**{"operation.old_hub_action": "none"})
    )
    op_result = next(r for r in results if r["id"] == "preflight-input-operation")
    assert op_result["status"] == "pass"
    assert op_result["details"]["old_hub_action"] == "none"


def test_restore_only_allows_argocd_manage():
    """ArgoCD pause on secondary is allowed — protects against auto-sync during restore."""
    results = build_input_validation_results(
        _restore_only_params(**{"features.argocd.manage": True})
    )
    op_result = next(r for r in results if r["id"] == "preflight-input-operation")
    assert op_result["status"] == "pass"
    assert op_result["details"]["argocd_manage"] is True


def test_restore_only_rejects_argocd_resume_after_switchover():
    """ArgoCD resume would restore secondary-role state — must be rejected."""
    results = build_input_validation_results(
        _restore_only_params(
            **{
                "features.argocd.manage": True,
                "features.argocd.resume_after_switchover": True,
            }
        )
    )
    op_result = next(r for r in results if r["id"] == "preflight-input-operation")
    assert op_result["status"] == "fail"
    assert "resume_after_switchover" in op_result["message"]


def test_restore_only_skips_primary_kubeconfig_validation():
    """Primary kubeconfig is irrelevant in restore-only — should not produce results."""
    results = build_input_validation_results(
        _restore_only_params(**{"hubs.primary.kubeconfig": "~/.kube/config"})
    )
    result_ids = [r["id"] for r in results]
    assert "preflight-input-primary-kubeconfig" not in result_ids
