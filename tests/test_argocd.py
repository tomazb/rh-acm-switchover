"""Unit tests for lib/argocd.py.

Tests cover Argo CD discovery, ACM-touching app detection,
pause/resume autosync logic, and run_id handling.
"""

from unittest.mock import MagicMock

import pytest
from kubernetes.client.rest import ApiException

from lib import argocd as argocd_lib


@pytest.mark.unit
class TestRunIdOrNew:
    """Test run_id_or_new."""

    def test_returns_existing_when_provided(self):
        assert argocd_lib.run_id_or_new("existing-id") == "existing-id"

    def test_returns_new_when_none(self):
        out = argocd_lib.run_id_or_new(None)
        assert out is not None
        assert len(out) == 12
        assert out.isalnum()

    def test_returns_new_when_empty_string(self):
        out = argocd_lib.run_id_or_new("")
        assert out is not None
        assert len(out) == 12


@pytest.mark.unit
class TestFindAcmTouchingApps:
    """Test find_acm_touching_apps filtering."""

    def test_includes_app_with_acm_kind_in_status_resources(self):
        apps = [
            {
                "metadata": {"namespace": "openshift-gitops", "name": "acm-app"},
                "status": {
                    "resources": [
                        {"kind": "BackupSchedule", "namespace": "open-cluster-management-backup", "name": "x"},
                    ]
                },
            }
        ]
        result = argocd_lib.find_acm_touching_apps(apps)
        assert len(result) == 1
        assert result[0].namespace == "openshift-gitops"
        assert result[0].name == "acm-app"
        assert result[0].resource_count == 1

    def test_includes_app_with_acm_namespace_in_status_resources(self):
        apps = [
            {
                "metadata": {"namespace": "argocd", "name": "acm-ns"},
                "status": {
                    "resources": [
                        {"kind": "ConfigMap", "namespace": "open-cluster-management", "name": "y"},
                    ]
                },
            }
        ]
        result = argocd_lib.find_acm_touching_apps(apps)
        assert len(result) == 1
        assert result[0].resource_count == 1

    def test_excludes_app_with_no_acm_resources(self):
        apps = [
            {
                "metadata": {"namespace": "argocd", "name": "other"},
                "status": {
                    "resources": [
                        {"kind": "ConfigMap", "namespace": "default", "name": "z"},
                    ]
                },
            }
        ]
        result = argocd_lib.find_acm_touching_apps(apps)
        assert len(result) == 0

    def test_excludes_app_with_no_status_resources(self):
        apps = [{"metadata": {"namespace": "argocd", "name": "no-status"}, "status": {}}]
        result = argocd_lib.find_acm_touching_apps(apps)
        assert len(result) == 0

    def test_excludes_app_with_missing_status(self):
        apps = [{"metadata": {"namespace": "argocd", "name": "x"}}]
        result = argocd_lib.find_acm_touching_apps(apps)
        assert len(result) == 0

    def test_cluster_scoped_acm_kind_matches(self):
        apps = [
            {
                "metadata": {"namespace": "argocd", "name": "mco"},
                "status": {
                    "resources": [
                        {"kind": "MultiClusterObservability", "name": "observability"},
                    ]
                },
            }
        ]
        result = argocd_lib.find_acm_touching_apps(apps)
        assert len(result) == 1
        assert result[0].resource_count == 1

    def test_includes_app_with_acm_sub_namespace(self):
        """Regression: open-cluster-management-* sub-namespaces must match (mirrors lib-common.sh)."""
        for sub_ns in ("open-cluster-management-hub", "open-cluster-management-addon"):
            apps = [
                {
                    "metadata": {"namespace": "argocd", "name": "hub-app"},
                    "status": {
                        "resources": [
                            {"kind": "ConfigMap", "namespace": sub_ns, "name": "z"},
                        ]
                    },
                }
            ]
            result = argocd_lib.find_acm_touching_apps(apps)
            assert len(result) == 1, f"Expected match for sub-namespace {sub_ns!r}"


@pytest.mark.unit
class TestPauseAutosync:
    """Test pause_autosync behavior."""

    def test_returns_patched_false_when_no_automated(self):
        client = MagicMock()
        app = {
            "metadata": {"namespace": "argocd", "name": "app"},
            "spec": {"syncPolicy": {"allowEmpty": True}},
        }
        result = argocd_lib.pause_autosync(client, app, "run-1")
        assert result.patched is False
        assert result.namespace == "argocd"
        assert result.name == "app"
        client.patch_custom_resource.assert_not_called()

    def test_patches_and_returns_patched_true_when_has_automated(self):
        client = MagicMock()
        client.patch_custom_resource.return_value = {"metadata": {"resourceVersion": "1001"}}
        app = {
            "metadata": {"namespace": "argocd", "name": "app", "resourceVersion": "1000"},
            "spec": {"syncPolicy": {"automated": {"prune": True}, "syncOptions": []}},
        }
        result = argocd_lib.pause_autosync(client, app, "run-1")
        assert result.patched is True
        assert result.original_sync_policy == {"automated": {"prune": True}, "syncOptions": []}
        client.patch_custom_resource.assert_called_once()
        call_kw = client.patch_custom_resource.call_args[1]
        assert call_kw["namespace"] == "argocd"
        assert call_kw["name"] == "app"
        patch = call_kw["patch"]
        assert patch["metadata"]["annotations"][argocd_lib.ARGOCD_PAUSED_BY_ANNOTATION] == "run-1"
        assert "automated" not in patch["spec"]["syncPolicy"]
        assert patch["spec"]["syncPolicy"].get("syncOptions") == []

    def test_api_exception_on_patch_returns_patched_false(self):
        """ApiException during patch (e.g. 403 Forbidden) must return patched=False and preserve original policy."""
        client = MagicMock()
        client.patch_custom_resource.side_effect = ApiException(status=403, reason="Forbidden")
        app = {
            "metadata": {"namespace": "argocd", "name": "app"},
            "spec": {"syncPolicy": {"automated": {"prune": True}}},
        }
        result = argocd_lib.pause_autosync(client, app, "run-1")
        assert result.patched is False
        assert result.original_sync_policy == {"automated": {"prune": True}}

    def test_patches_when_automated_is_empty_map(self):
        client = MagicMock()
        client.patch_custom_resource.return_value = {"metadata": {"resourceVersion": "1001"}}
        app = {
            "metadata": {"namespace": "argocd", "name": "app", "resourceVersion": "1000"},
            "spec": {"syncPolicy": {"automated": {}}},
        }
        result = argocd_lib.pause_autosync(client, app, "run-1")
        assert result.patched is True
        client.patch_custom_resource.assert_called_once()
        patch = client.patch_custom_resource.call_args[1]["patch"]
        assert "automated" not in patch["spec"]["syncPolicy"]


@pytest.mark.unit
class TestResumeAutosync:
    """Test resume_autosync behavior."""

    def test_skip_when_marker_mismatch(self):
        client = MagicMock()
        client.get_custom_resource.return_value = {
            "metadata": {
                "resourceVersion": "500",
                "annotations": {argocd_lib.ARGOCD_PAUSED_BY_ANNOTATION: "other-run"},
            },
        }
        result = argocd_lib.resume_autosync(client, "argocd", "app", {"automated": {}}, "run-1")
        assert result.restored is False
        assert "mismatch" in (result.skip_reason or "").lower()
        client.patch_custom_resource.assert_not_called()

    def test_restores_when_marker_matches(self):
        client = MagicMock()
        client.get_custom_resource.return_value = {
            "metadata": {"resourceVersion": "500", "annotations": {argocd_lib.ARGOCD_PAUSED_BY_ANNOTATION: "run-1"}},
        }
        client.patch_custom_resource.return_value = {"metadata": {"resourceVersion": "501"}}
        result = argocd_lib.resume_autosync(client, "argocd", "app", {"automated": {"prune": True}}, "run-1")
        assert result.restored is True
        client.patch_custom_resource.assert_called_once()
        call_kw = client.patch_custom_resource.call_args[1]
        assert call_kw["patch"]["spec"]["syncPolicy"] == {"automated": {"prune": True}}
        assert call_kw["patch"]["metadata"]["annotations"][argocd_lib.ARGOCD_PAUSED_BY_ANNOTATION] is None

    def test_skip_when_app_not_found(self):
        client = MagicMock()
        client.get_custom_resource.return_value = None
        result = argocd_lib.resume_autosync(client, "argocd", "missing", {"automated": {}}, "run-1")
        assert result.restored is False
        client.patch_custom_resource.assert_not_called()

    def test_patch_exception_returns_skip_reason(self):
        client = MagicMock()
        client.get_custom_resource.return_value = {
            "metadata": {"resourceVersion": "500", "annotations": {argocd_lib.ARGOCD_PAUSED_BY_ANNOTATION: "run-1"}},
        }
        client.patch_custom_resource.side_effect = RuntimeError("boom")
        result = argocd_lib.resume_autosync(client, "argocd", "app", {"automated": {"prune": True}}, "run-1")
        assert result.restored is False
        assert "patch failed" in (result.skip_reason or "").lower()

    def test_is_resume_noop_true_for_marker_mismatch(self):
        result = argocd_lib.ResumeResult(
            namespace="argocd",
            name="app",
            restored=False,
            skip_reason=argocd_lib.RESUME_SKIP_REASON_MARKER_MISMATCH,
        )
        assert argocd_lib.is_resume_noop(result) is True

    def test_is_resume_noop_false_for_patch_failure(self):
        result = argocd_lib.ResumeResult(
            namespace="argocd",
            name="app",
            restored=False,
            skip_reason="patch failed: 403 Forbidden",
        )
        assert argocd_lib.is_resume_noop(result) is False


@pytest.mark.unit
class TestDetectArgocdInstallation:
    """Test detect_argocd_installation."""

    def test_none_when_app_crd_missing(self):
        client = MagicMock()
        client.get_custom_resource.side_effect = [None, None]
        result = argocd_lib.detect_argocd_installation(client)
        assert result.has_applications_crd is False
        assert result.install_type == "none"

    def test_vanilla_when_app_crd_only(self):
        client = MagicMock()
        client.get_custom_resource.side_effect = [{"metadata": {"name": "applications.argoproj.io"}}, None]
        result = argocd_lib.detect_argocd_installation(client)
        assert result.has_applications_crd is True
        assert result.has_argocds_crd is False
        assert result.install_type == "vanilla"

    def test_operator_when_both_crds_and_instances(self):
        client = MagicMock()
        client.get_custom_resource.side_effect = [
            {"metadata": {"name": "applications.argoproj.io"}},
            {"metadata": {"name": "argocds.argoproj.io"}},
        ]
        client.list_custom_resources.return_value = [
            {"metadata": {"namespace": "openshift-gitops", "name": "openshift-gitops"}},
        ]
        result = argocd_lib.detect_argocd_installation(client)
        assert result.has_applications_crd is True
        assert result.has_argocds_crd is True
        assert result.install_type == "operator"
        assert len(result.argocd_instances) == 1
        assert result.argocd_instances[0]["namespace"] == "openshift-gitops"
