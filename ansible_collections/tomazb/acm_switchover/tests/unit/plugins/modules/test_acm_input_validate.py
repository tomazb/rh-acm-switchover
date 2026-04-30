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
            "features": {"argocd": {"manage": False}},
        }
    )
    assert any(item["id"] == "preflight-input-secondary-context" for item in results)
    assert any(item["status"] == "fail" for item in results)


def test_missing_required_kubeconfigs_fail_execute_mode():
    results = build_input_validation_results(
        {
            "hubs": {
                "primary": {"context": "primary-hub", "kubeconfig": ""},
                "secondary": {"context": "secondary-hub", "kubeconfig": ""},
            },
            "operation": {"method": "passive", "activation_method": "patch"},
            "execution": {"mode": "execute", "checkpoint": {"path": ".state/run.json"}},
            "features": {"argocd": {"manage": False}},
        }
    )

    primary_result = next(item for item in results if item["id"] == "preflight-input-primary-kubeconfig")
    secondary_result = next(item for item in results if item["id"] == "preflight-input-secondary-kubeconfig")

    assert primary_result["status"] == "fail"
    assert primary_result["message"] == "primary kubeconfig is required for collection preflight and switchover runs"
    assert secondary_result["status"] == "fail"
    assert (
        secondary_result["message"] == "secondary kubeconfig is required for collection preflight and switchover runs"
    )


def test_restore_requires_passive_method():
    results = build_input_validation_results(
        {
            "hubs": {
                "primary": {"context": "primary-hub", "kubeconfig": "~/.kube/config"},
                "secondary": {"context": "secondary-hub", "kubeconfig": "~/.kube/config"},
            },
            "operation": {"method": "full", "activation_method": "restore"},
            "execution": {"mode": "execute", "checkpoint": {"path": ".state/run.json"}},
            "features": {"argocd": {"manage": False}},
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
            "features": {"argocd": {"manage": True}},
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
            "features": {"argocd": {"manage": False}},
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
        "features": {"argocd": {"manage": False}},
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
    assert all(
        item["status"] == "pass" for item in results
    ), f"Expected all pass, got: {[r for r in results if r['status'] != 'pass']}"
    # Normalized operation values
    op_result = next(r for r in results if r["id"] == "preflight-input-operation")
    assert op_result["details"]["method"] == "full"
    assert op_result["details"]["old_hub_action"] == "none"
    assert op_result["details"]["restore_only"] is True


def test_restore_only_rejects_primary_context():
    """Primary context must be empty in restore-only mode."""
    results = build_input_validation_results(_restore_only_params(**{"hubs.primary.context": "old-hub"}))
    primary_result = next(r for r in results if r["id"] == "preflight-input-primary-context")
    assert primary_result["status"] == "fail"
    assert "restore_only" in primary_result["message"]


def test_restore_only_rejects_missing_secondary_context():
    """Secondary context is required in restore-only mode."""
    results = build_input_validation_results(_restore_only_params(**{"hubs.secondary.context": ""}))
    secondary_result = next(r for r in results if r["id"] == "preflight-input-secondary-context")
    assert secondary_result["status"] == "fail"


def test_restore_only_rejects_method_passive():
    """Passive method requires a live primary — incompatible with restore-only."""
    results = build_input_validation_results(_restore_only_params(**{"operation.method": "passive"}))
    op_result = next(r for r in results if r["id"] == "preflight-input-operation")
    assert op_result["status"] == "fail"
    assert "method" in op_result["message"]


def test_restore_only_accepts_method_full_explicit():
    """Explicitly setting method=full is fine in restore-only mode."""
    results = build_input_validation_results(_restore_only_params(**{"operation.method": "full"}))
    op_result = next(r for r in results if r["id"] == "preflight-input-operation")
    assert op_result["status"] == "pass"
    assert op_result["details"]["method"] == "full"


def test_restore_only_rejects_old_hub_action_secondary():
    """old_hub_action must be none — no old hub to manage."""
    results = build_input_validation_results(_restore_only_params(**{"operation.old_hub_action": "secondary"}))
    op_result = next(r for r in results if r["id"] == "preflight-input-operation")
    assert op_result["status"] == "fail"
    assert "old_hub_action" in op_result["message"]


def test_restore_only_rejects_old_hub_action_decommission():
    """old_hub_action=decommission is also rejected in restore-only."""
    results = build_input_validation_results(_restore_only_params(**{"operation.old_hub_action": "decommission"}))
    op_result = next(r for r in results if r["id"] == "preflight-input-operation")
    assert op_result["status"] == "fail"
    assert "old_hub_action" in op_result["message"]


def test_restore_only_accepts_old_hub_action_none_explicit():
    """Explicitly setting old_hub_action=none is fine."""
    results = build_input_validation_results(_restore_only_params(**{"operation.old_hub_action": "none"}))
    op_result = next(r for r in results if r["id"] == "preflight-input-operation")
    assert op_result["status"] == "pass"
    assert op_result["details"]["old_hub_action"] == "none"


def test_restore_only_allows_argocd_manage():
    """ArgoCD pause on secondary is allowed — protects against auto-sync during restore."""
    results = build_input_validation_results(_restore_only_params(**{"features.argocd.manage": True}))
    op_result = next(r for r in results if r["id"] == "preflight-input-operation")
    assert op_result["status"] == "pass"
    assert op_result["details"]["argocd_manage"] is True


def test_disable_observability_on_secondary_requires_secondary_old_hub_action():
    """Observability teardown on the old hub only makes sense when the old hub becomes secondary."""
    results = build_input_validation_results(
        {
            "hubs": {
                "primary": {"context": "primary-hub", "kubeconfig": "~/.kube/config"},
                "secondary": {"context": "secondary-hub", "kubeconfig": "~/.kube/config"},
            },
            "operation": {"method": "passive", "activation_method": "patch", "old_hub_action": "decommission"},
            "execution": {"mode": "execute", "report_dir": "./artifacts", "checkpoint": {"path": ".state/run.json"}},
            "features": {
                "disable_observability_on_secondary": True,
                "argocd": {"manage": False},
            },
        }
    )

    op_result = next(r for r in results if r["id"] == "preflight-input-operation")
    assert op_result["status"] == "fail"
    assert "disable_observability_on_secondary" in op_result["message"]


def test_report_dir_must_be_a_safe_path():
    """Unsafe report_dir values must be rejected before execution starts."""
    results = build_input_validation_results(
        {
            "hubs": {
                "primary": {"context": "primary-hub", "kubeconfig": "~/.kube/config"},
                "secondary": {"context": "secondary-hub", "kubeconfig": "~/.kube/config"},
            },
            "operation": {"method": "passive", "activation_method": "patch"},
            "execution": {
                "mode": "execute",
                "report_dir": "../artifacts",
                "checkpoint": {"enabled": True, "path": ".state/run.json"},
            },
            "features": {"argocd": {"manage": False}},
        }
    )

    report_dir_result = next(r for r in results if r["id"] == "preflight-input-report-dir")
    assert report_dir_result["status"] == "fail"
    assert "Path traversal attempt" in report_dir_result["message"]


def test_checkpoint_enabled_requires_a_checkpoint_path():
    """Enabled checkpointing without a path should fail early with an actionable error."""
    results = build_input_validation_results(
        {
            "hubs": {
                "primary": {"context": "primary-hub", "kubeconfig": "~/.kube/config"},
                "secondary": {"context": "secondary-hub", "kubeconfig": "~/.kube/config"},
            },
            "operation": {"method": "passive", "activation_method": "patch"},
            "execution": {"mode": "execute", "checkpoint": {"enabled": True, "path": ""}},
            "features": {"argocd": {"manage": False}},
        }
    )

    checkpoint_result = next(r for r in results if r["id"] == "preflight-input-checkpoint-path")
    assert checkpoint_result["status"] == "fail"
    assert "checkpoint.path is required" in checkpoint_result["message"]


# ---------------------------------------------------------------------------
# enum validation
# ---------------------------------------------------------------------------


def test_invalid_method_rejected():
    """A typo in method must be caught during validation, not in activation."""
    results = build_input_validation_results(
        {
            "hubs": {
                "primary": {"context": "primary-hub", "kubeconfig": "~/.kube/config"},
                "secondary": {"context": "secondary-hub", "kubeconfig": "~/.kube/config"},
            },
            "operation": {"method": "pasive", "activation_method": "patch"},
            "execution": {"mode": "execute", "checkpoint": {"path": ".state/run.json"}},
            "features": {"argocd": {"manage": False}},
        }
    )
    assert any(item["id"] == "preflight-input-operation" and item["status"] == "fail" for item in results)
    assert any("pasive" in item["message"] for item in results)


def test_invalid_old_hub_action_rejected():
    """A typo in old_hub_action must be caught during validation."""
    results = build_input_validation_results(
        {
            "hubs": {
                "primary": {"context": "primary-hub", "kubeconfig": "~/.kube/config"},
                "secondary": {"context": "secondary-hub", "kubeconfig": "~/.kube/config"},
            },
            "operation": {"method": "passive", "activation_method": "patch", "old_hub_action": "secodnary"},
            "execution": {"mode": "execute", "checkpoint": {"path": ".state/run.json"}},
            "features": {"argocd": {"manage": False}},
        }
    )
    assert any(item["id"] == "preflight-input-operation" and item["status"] == "fail" for item in results)
    assert any("secodnary" in item["message"] for item in results)


def test_invalid_activation_method_rejected():
    """A typo in activation_method must be caught during validation."""
    results = build_input_validation_results(
        {
            "hubs": {
                "primary": {"context": "primary-hub", "kubeconfig": "~/.kube/config"},
                "secondary": {"context": "secondary-hub", "kubeconfig": "~/.kube/config"},
            },
            "operation": {"method": "passive", "activation_method": "restor"},
            "execution": {"mode": "execute", "checkpoint": {"path": ".state/run.json"}},
            "features": {"argocd": {"manage": False}},
        }
    )
    assert any(item["id"] == "preflight-input-operation" and item["status"] == "fail" for item in results)
    assert any("restor" in item["message"] for item in results)
