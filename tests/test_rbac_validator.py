"""
Unit tests for RBAC validator module.
"""

import logging
from unittest.mock import MagicMock, call, patch

import pytest
from kubernetes.client.rest import ApiException

from lib import rbac_validator
from lib.constants import (
    ACM_NAMESPACE,
    BACKUP_NAMESPACE,
    MANAGED_CLUSTER_AGENT_NAMESPACE,
    MANAGED_CLUSTER_API_GROUP,
    MANAGED_CLUSTER_PLURAL,
    MCE_NAMESPACE,
    OBSERVABILITY_NAMESPACE,
)
from lib.exceptions import ValidationError
from lib.rbac_validator import RBACValidator, validate_decommission_permissions, validate_rbac_permissions


class TestRBACValidator:
    """Test cases for RBACValidator class."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock KubeClient."""
        client = MagicMock()
        client.context = "test-context"
        client.namespace_exists = MagicMock(return_value=True)
        return client

    @pytest.fixture
    def validator(self, mock_client):
        """Create an RBACValidator instance."""
        return RBACValidator(mock_client)

    @patch("kubernetes.client")
    def test_check_permission_allowed(self, mock_k8s_client, validator):
        """Test check_permission when permission is allowed."""
        # Mock SelfSubjectAccessReview response
        mock_response = MagicMock()
        mock_response.status.allowed = True
        mock_response.status.reason = None

        mock_api = MagicMock()
        mock_api.create_self_subject_access_review.return_value = mock_response
        mock_k8s_client.AuthorizationV1Api.return_value = mock_api

        has_perm, error = validator.check_permission("", "pods", "get", "default")

        assert has_perm is True
        assert error == ""

    @patch("kubernetes.client")
    def test_check_permission_denied(self, mock_k8s_client, validator):
        """Test check_permission when permission is denied."""
        # Mock SelfSubjectAccessReview response
        mock_response = MagicMock()
        mock_response.status.allowed = False
        mock_response.status.reason = "Forbidden"

        mock_api = MagicMock()
        mock_api.create_self_subject_access_review.return_value = mock_response
        mock_k8s_client.AuthorizationV1Api.return_value = mock_api

        has_perm, error = validator.check_permission("", "pods", "delete", "default")

        assert has_perm is False
        assert "Forbidden" in error

    @patch("kubernetes.client")
    def test_check_permission_splits_subresource_for_ssar(self, mock_k8s_client, validator):
        """Kubernetes SSAR resourceAttributes require resource and subresource to be separate fields."""
        mock_response = MagicMock()
        mock_response.status.allowed = True
        mock_response.status.reason = None

        mock_api = MagicMock()
        mock_api.create_self_subject_access_review.return_value = mock_response
        mock_k8s_client.AuthorizationV1Api.return_value = mock_api

        validator.check_permission("apps", "statefulsets/scale", "patch", OBSERVABILITY_NAMESPACE)

        mock_k8s_client.V1ResourceAttributes.assert_called_once_with(
            verb="patch",
            resource="statefulsets",
            subresource="scale",
            group="apps",
            namespace=OBSERVABILITY_NAMESPACE,
        )

    @patch("kubernetes.client")
    def test_check_permission_raises_validation_error_on_api_failure(self, mock_k8s_client, validator):
        """Infrastructure failures should not be reported as missing permissions."""
        mock_api = MagicMock()
        mock_api.create_self_subject_access_review.side_effect = ApiException(
            status=500, reason="Internal Server Error"
        )
        mock_k8s_client.AuthorizationV1Api.return_value = mock_api

        with pytest.raises(ValidationError, match="Unable to check permission get core/pods"):
            validator.check_permission("", "pods", "get", "default")

    @patch("kubernetes.client")
    def test_check_permission_cached_api_failure_reraises_fresh_validation_error(self, mock_k8s_client, validator):
        """Cached infrastructure failures should keep the message but not reuse the same exception instance."""
        mock_api = MagicMock()
        mock_api.create_self_subject_access_review.side_effect = ApiException(
            status=500, reason="Internal Server Error"
        )
        mock_k8s_client.AuthorizationV1Api.return_value = mock_api

        with pytest.raises(ValidationError, match="Unable to check permission get core/pods") as first_error:
            validator.check_permission("", "pods", "get", "default")

        with pytest.raises(ValidationError, match="Unable to check permission get core/pods") as second_error:
            validator.check_permission("", "pods", "get", "default")

        assert str(first_error.value) == str(second_error.value)
        assert first_error.value is not second_error.value
        assert mock_api.create_self_subject_access_review.call_count == 1

    @patch("kubernetes.client")
    def test_check_permission_reuses_cached_ssar_result(self, mock_k8s_client, validator):
        """Identical permission tuples should not trigger duplicate SSAR API calls."""
        mock_response = MagicMock()
        mock_response.status.allowed = True
        mock_response.status.reason = None

        mock_api = MagicMock()
        mock_api.create_self_subject_access_review.return_value = mock_response
        mock_k8s_client.AuthorizationV1Api.return_value = mock_api

        assert validator.check_permission("", "pods", "get", "default") == (True, "")
        assert validator.check_permission("", "pods", "get", "default") == (True, "")

        assert mock_api.create_self_subject_access_review.call_count == 1

    def test_validate_cluster_permissions_success(self, validator):
        """validate_cluster_permissions must check the exact OPERATOR_CLUSTER_PERMISSIONS set."""
        validator.check_permission = MagicMock(return_value=(True, ""))

        all_valid, errors = validator.validate_cluster_permissions()

        assert all_valid is True
        assert errors == []

        # expected is built dynamically from the class constant; mutmut 3.x does not mutate class-level
        # attributes, only function bodies — so this correctly targets loop/iteration mutations in
        # validate_cluster_permissions while still asserting exact call shape and coverage.
        expected = frozenset((ag, r, v) for ag, r, verbs in RBACValidator.OPERATOR_CLUSTER_PERMISSIONS for v in verbs)
        all_calls = validator.check_permission.call_args_list
        assert all(
            len(c.args) == 3 for c in all_calls
        ), f"Unexpected check_permission call shape: {[len(c.args) for c in all_calls if len(c.args) != 3]}"
        actual = frozenset((c.args[0], c.args[1], c.args[2]) for c in all_calls)
        assert actual == expected, (
            f"Permission set mismatch.\n" f"  Missing: {expected - actual}\n" f"  Unexpected: {actual - expected}"
        )

    @pytest.mark.parametrize(
        "permissions",
        [
            RBACValidator.OPERATOR_CLUSTER_PERMISSIONS,
            RBACValidator.VALIDATOR_CLUSTER_PERMISSIONS,
        ],
    )
    def test_cluster_permissions_require_namespace_list_for_preflight_discovery(self, permissions):
        """Preflight lists Namespace objects, so RBAC validation must require list."""
        namespace_rule = next(rule for rule in permissions if rule[0] == "" and rule[1] == "namespaces")

        assert "get" in namespace_rule[2]
        assert "list" in namespace_rule[2]

    def test_validate_cluster_permissions_failure(self, validator):
        """Test validate_cluster_permissions when some permissions missing."""

        # Mock check_permission to return False for specific permission
        def mock_check(api_group, resource, verb, namespace=None):
            if resource == "managedclusters" and verb == "patch":
                return (False, "Permission denied")
            return (True, "")

        validator.check_permission = MagicMock(side_effect=mock_check)

        all_valid, errors = validator.validate_cluster_permissions()

        assert all_valid is False
        assert len(errors) > 0
        assert any("managedclusters" in error for error in errors)

    def test_validate_cluster_permissions_argocd_none_does_not_check_argocd_permissions(self, validator):
        """Test that argocd_mode=none does not add Argo CD permissions."""
        validator.check_permission = MagicMock(return_value=(True, ""))

        all_valid, errors = validator.validate_cluster_permissions(argocd_mode="none")

        assert all_valid is True
        assert len(errors) == 0
        checked = {(call.args[0], call.args[1], call.args[2]) for call in validator.check_permission.call_args_list}
        assert ("argoproj.io", "applications", "get") not in checked
        assert ("argoproj.io", "applications", "patch") not in checked

    def test_validate_cluster_permissions_argocd_check_adds_read_permissions(self, validator):
        """Test that argocd_mode=check validates Argo CD read-only permissions.

        This covers the auto-detection scenario where preflight discovers ArgoCD
        CRDs on the cluster and automatically enables argocd_mode="check". In that
        mode only read-only permissions (get/list) are validated — never patch,
        which is reserved for the explicit "manage" mode.
        """
        validator.check_permission = MagicMock(return_value=(True, ""))

        all_valid, errors = validator.validate_cluster_permissions(argocd_mode="check")

        assert all_valid is True
        assert len(errors) == 0
        checked = {(call.args[0], call.args[1], call.args[2]) for call in validator.check_permission.call_args_list}
        assert ("argoproj.io", "applications", "get") in checked
        assert ("argoproj.io", "applications", "list") in checked
        assert ("argoproj.io", "argocds", "get") in checked
        assert ("argoproj.io", "argocds", "list") in checked
        assert ("apiextensions.k8s.io", "customresourcedefinitions", "get") in checked
        assert ("argoproj.io", "applications", "patch") not in checked

    def test_validate_cluster_permissions_argocd_manage_requires_patch_for_operator(self, validator):
        """Test that argocd_mode=manage validates Application patch permission for operator role."""

        def mock_check(api_group, resource, verb, namespace=None):
            if api_group == "argoproj.io" and resource == "applications" and verb == "patch":
                return (False, "Permission denied")
            return (True, "")

        validator.check_permission = MagicMock(side_effect=mock_check)

        all_valid, errors = validator.validate_cluster_permissions(argocd_mode="manage")

        assert all_valid is False
        assert any("Missing Argo CD permission: patch argoproj.io/applications" in error for error in errors)

    @pytest.mark.parametrize("argocd_install_type", ["none", "vanilla", "operator", "unknown"])
    def test_validate_cluster_permissions_argocd_manage_validator_role_raises(self, mock_client, argocd_install_type):
        """Validator role must reject argocd_mode=manage instead of silently downgrading it."""
        validator = RBACValidator(mock_client, role="validator")

        with pytest.raises(ValueError, match="validator.*manage"):
            validator.validate_cluster_permissions(
                argocd_mode="manage",
                argocd_install_type=argocd_install_type,
            )

    def test_validate_cluster_permissions_invalid_argocd_mode_raises(self, validator):
        """Test validate_cluster_permissions rejects invalid argocd_mode values."""
        with pytest.raises(ValueError):
            validator.validate_cluster_permissions(argocd_mode="invalid")

    def test_validate_cluster_permissions_argocd_check_skips_operator_crd_for_vanilla(self, validator):
        """Vanilla Argo CD installs must not require argocds.argoproj.io permissions."""
        validator.check_permission = MagicMock(return_value=(True, ""))

        all_valid, errors = validator.validate_cluster_permissions(
            argocd_mode="check",
            argocd_install_type="vanilla",
        )

        assert all_valid is True
        assert len(errors) == 0
        checked = {(call.args[0], call.args[1], call.args[2]) for call in validator.check_permission.call_args_list}
        assert ("argoproj.io", "applications", "get") in checked
        assert ("argoproj.io", "applications", "list") in checked
        assert ("apiextensions.k8s.io", "customresourcedefinitions", "get") in checked
        assert ("argoproj.io", "argocds", "get") not in checked
        assert ("argoproj.io", "argocds", "list") not in checked

    def test_validate_cluster_permissions_argocd_check_skips_all_checks_when_not_installed(self, validator):
        """Clusters without Argo CD must not validate any Argo CD permissions."""
        validator.check_permission = MagicMock(return_value=(True, ""))

        all_valid, errors = validator.validate_cluster_permissions(
            argocd_mode="check",
            argocd_install_type="none",
        )

        assert all_valid is True
        assert len(errors) == 0
        checked = {(call.args[0], call.args[1], call.args[2]) for call in validator.check_permission.call_args_list}
        assert ("argoproj.io", "applications", "get") not in checked
        assert ("argoproj.io", "applications", "list") not in checked
        assert ("argoproj.io", "argocds", "get") not in checked
        assert ("apiextensions.k8s.io", "customresourcedefinitions", "get") not in checked

    def test_validate_cluster_permissions_requires_mco_delete_for_old_hub_finalization(self, validator):
        """Normal old-hub finalization deletes MCO when observability was detected."""
        validator.check_permission = MagicMock(return_value=(True, ""))

        all_valid, errors = validator.validate_cluster_permissions(include_old_hub_finalization=True)

        assert all_valid is True
        assert errors == []
        assert (
            call(
                "observability.open-cluster-management.io",
                "multiclusterobservabilities",
                "delete",
            )
            in validator.check_permission.call_args_list
        )

    def test_validate_cluster_permissions_skips_mco_delete_when_observability_absent(self, validator):
        """Verified observability absence must avoid requiring MCO delete."""
        validator.check_permission = MagicMock(return_value=(True, ""))

        all_valid, errors = validator.validate_cluster_permissions(
            include_old_hub_finalization=True,
            skip_observability=True,
        )

        assert all_valid is True
        assert errors == []
        assert (
            call(
                "observability.open-cluster-management.io",
                "multiclusterobservabilities",
                "delete",
            )
            not in validator.check_permission.call_args_list
        )

    def test_validate_cluster_permissions_skips_decommission_mco_delete_when_observability_absent(self, validator):
        """Decommission checks should not require MCO delete after verified observability absence."""
        validator.check_permission = MagicMock(return_value=(True, ""))

        all_valid, errors = validator.validate_cluster_permissions(
            include_decommission=True,
            skip_observability=True,
        )

        assert all_valid is True
        assert errors == []
        assert (
            call(
                "observability.open-cluster-management.io",
                "multiclusterobservabilities",
                "delete",
            )
            not in validator.check_permission.call_args_list
        )

    def test_validate_cluster_permissions_skips_base_observability_permissions(self, validator):
        """skip_observability=True must filter out observability-tagged entries in the base cluster loop.

        The 'observability' string check guards the standard cluster_permissions loop (not just
        the finalization path). This test catches mutations that change that string.
        """
        validator.check_permission = MagicMock(return_value=(True, ""))

        all_valid, errors = validator.validate_cluster_permissions(skip_observability=True)

        assert all_valid is True
        assert errors == []
        checked = {(c.args[0], c.args[1], c.args[2]) for c in validator.check_permission.call_args_list}
        # Base cluster permissions include multiclusterobservabilities get/list — both must be skipped.
        assert ("observability.open-cluster-management.io", "multiclusterobservabilities", "get") not in checked
        assert ("observability.open-cluster-management.io", "multiclusterobservabilities", "list") not in checked

    def test_validate_cluster_permissions_deduplicates_mco_delete_when_both_paths_request_it(self, validator):
        """MCO delete should be checked once when decommission and finalization both require it."""
        validator.check_permission = MagicMock(return_value=(True, ""))

        all_valid, errors = validator.validate_cluster_permissions(
            include_decommission=True,
            include_old_hub_finalization=True,
        )

        assert all_valid is True
        assert errors == []
        assert (
            validator.check_permission.call_args_list.count(
                call(
                    "observability.open-cluster-management.io",
                    "multiclusterobservabilities",
                    "delete",
                )
            )
            == 1
        )

    def test_validate_namespace_permissions_success(self, validator):
        """validate_namespace_permissions must check the exact OPERATOR_HUB_NAMESPACE_PERMISSIONS set."""
        validator.client.namespace_exists.return_value = True
        validator.check_permission = MagicMock(return_value=(True, ""))

        all_valid, errors = validator.validate_namespace_permissions()

        assert all_valid is True
        assert errors == []

        # expected is built dynamically from the class constant; mutmut 3.x does not mutate class-level
        # attributes, only function bodies — so this correctly targets loop/iteration mutations.
        expected = frozenset(
            (ag, r, v, ns)
            for ns, rules in RBACValidator.OPERATOR_HUB_NAMESPACE_PERMISSIONS.items()
            for ag, r, verbs in rules
            for v in verbs
        )
        all_calls = validator.check_permission.call_args_list
        assert all(
            len(c.args) == 4 for c in all_calls
        ), f"Unexpected check_permission call shape: {[len(c.args) for c in all_calls if len(c.args) != 4]}"
        actual = frozenset((c.args[0], c.args[1], c.args[2], c.args[3]) for c in all_calls)
        assert actual == expected, (
            f"Namespace permission set mismatch.\n"
            f"  Missing: {expected - actual}\n"
            f"  Unexpected: {actual - expected}"
        )

    def test_validate_namespace_permissions_namespace_missing(self, validator):
        """Test validate_namespace_permissions when namespace doesn't exist."""
        # Mock namespace_exists to return False
        validator.client.namespace_exists.return_value = False
        validator.check_permission = MagicMock(return_value=(True, ""))

        all_valid, errors = validator.validate_namespace_permissions()

        # Missing namespaces should cause validation failure
        assert all_valid is False
        assert len(errors) > 0
        assert any("does not exist" in error for error in errors)

    def test_validate_namespace_permissions_skip_observability(self, validator):
        """Test validate_namespace_permissions with skip_observability=True."""
        validator.client.namespace_exists.return_value = True
        validator.check_permission = MagicMock(return_value=(True, ""))

        all_valid, errors = validator.validate_namespace_permissions(skip_observability=True)

        assert all_valid is True
        # Should not check observability namespace
        validator.client.namespace_exists.assert_any_call("open-cluster-management-backup")
        # This will not be called for observability namespace when skipped
        namespaces_checked = [call[0][0] for call in validator.client.namespace_exists.call_args_list]
        assert "open-cluster-management-observability" not in namespaces_checked

    def test_validate_namespace_permissions_reuses_cached_namespace_exists_results(self, validator):
        """Repeated namespace validation should not re-probe the same namespaces."""
        validator.client.namespace_exists.return_value = True
        validator.check_permission = MagicMock(return_value=(True, ""))

        assert validator.validate_namespace_permissions(skip_observability=True) == (True, [])
        assert validator.validate_namespace_permissions(skip_observability=True) == (True, [])

        assert validator.client.namespace_exists.call_args_list.count(call(BACKUP_NAMESPACE)) == 1
        assert validator.client.namespace_exists.call_args_list.count(call(ACM_NAMESPACE)) == 1
        assert validator.client.namespace_exists.call_args_list.count(call(MCE_NAMESPACE)) == 1

    def test_validator_role_gets_read_only_namespace_permissions(self, mock_client):
        """Validator role must receive read-only namespace permissions, not operator write permissions."""
        mock_client.context = "test-context"
        validator = RBACValidator(mock_client, role="validator")

        perms = validator._get_hub_namespace_permissions()

        assert perms is RBACValidator.VALIDATOR_HUB_NAMESPACE_PERMISSIONS
        assert perms is not RBACValidator.OPERATOR_HUB_NAMESPACE_PERMISSIONS

        backup_ns_perms = perms.get("open-cluster-management-backup", [])
        backup_schedule_rule = next(
            (
                rule
                for rule in backup_ns_perms
                if rule[0] == "cluster.open-cluster-management.io" and rule[1] == "backupschedules"
            ),
            None,
        )
        assert backup_schedule_rule is not None, "backupschedules rule missing from validator permissions"
        assert "create" not in backup_schedule_rule[2], "validator must not have create on backupschedules"
        assert "patch" not in backup_schedule_rule[2], "validator must not have patch on backupschedules"
        assert "delete" not in backup_schedule_rule[2], "validator must not have delete on backupschedules"

    def test_validator_role_gets_read_only_cluster_permissions(self, mock_client):
        """Validator role must receive read-only cluster permissions, not operator write permissions."""
        mock_client.context = "test-context"
        validator = RBACValidator(mock_client, role="validator")

        perms = validator._get_cluster_permissions()

        assert perms is RBACValidator.VALIDATOR_CLUSTER_PERMISSIONS
        assert perms is not RBACValidator.OPERATOR_CLUSTER_PERMISSIONS

        # Spot-check: validator must NOT have delete on managedclusters (an operator-only write permission)
        mc_rule = next(
            (r for r in perms if r[0] == "cluster.open-cluster-management.io" and r[1] == "managedclusters"),
            None,
        )
        assert mc_rule is not None, "managedclusters rule missing from validator cluster permissions"
        assert "delete" not in mc_rule[2], "validator must not have delete on managedclusters"
        assert "patch" not in mc_rule[2], "validator must not have patch on managedclusters"

    def test_validate_all_permissions_success(self, validator):
        """Test validate_all_permissions when all checks pass."""
        validator.check_permission = MagicMock(return_value=(True, ""))
        validator.client.namespace_exists.return_value = True

        all_valid, all_errors = validator.validate_all_permissions()

        assert all_valid is True
        assert len(all_errors) == 0

    def test_validate_all_permissions_failure(self, validator):
        """Test validate_all_permissions when checks fail."""

        # Some permissions fail
        def mock_check(api_group, resource, verb, namespace=None):
            if resource == "managedclusters":
                return (False, "Denied")
            return (True, "")

        validator.check_permission = MagicMock(side_effect=mock_check)
        validator.client.namespace_exists.return_value = True

        all_valid, all_errors = validator.validate_all_permissions()

        assert all_valid is False
        assert "cluster" in all_errors
        assert len(all_errors["cluster"]) > 0

    def test_generate_permission_report(self, validator):
        """Test generate_permission_report output."""
        validator.check_permission = MagicMock(return_value=(True, ""))
        validator.client.namespace_exists.return_value = True

        report = validator.generate_permission_report()

        assert "RBAC PERMISSION VALIDATION REPORT" in report
        assert "STATUS:" in report
        assert "=" * 80 in report

    def test_generate_permission_report_with_errors(self, validator):
        """Test generate_permission_report with validation errors."""

        def mock_check(api_group, resource, verb, namespace=None):
            return (False, "Permission denied")

        validator.check_permission = MagicMock(side_effect=mock_check)
        validator.client.namespace_exists.return_value = True

        report = validator.generate_permission_report()

        assert "PERMISSION VALIDATION FAILED" in report
        assert "REMEDIATION" in report
        assert "deploy/rbac/" in report

    def test_generate_permission_report_with_decommission_errors(self, validator):
        """Decommission reports should include the extension remediation guidance."""

        def mock_check(api_group, resource, verb, namespace=None):
            return (False, "Permission denied")

        validator.check_permission = MagicMock(side_effect=mock_check)
        validator.client.namespace_exists.return_value = True

        report = validator.generate_permission_report(include_decommission=True)

        assert "deploy/rbac/extensions/decommission/" in report
        assert "rbac.includeDecommissionClusterRole=true" in report

    def test_generate_permission_report_reuses_cached_validation_summary(self, validator):
        """Report generation should reuse the prior full validation result on the same validator."""
        validator.client.namespace_exists.return_value = True

        def mock_check(api_group, resource, verb, namespace=None):
            if resource == "managedclusters" and verb == "get" and namespace is None:
                return (False, "Denied")
            if resource == "pods" and verb == "get" and namespace == BACKUP_NAMESPACE:
                return (False, "Denied")
            return (True, "")

        validator.check_permission = MagicMock(side_effect=mock_check)

        all_valid, all_errors = validator.validate_all_permissions(skip_observability=True)
        first_call_count = len(validator.check_permission.call_args_list)
        all_errors["cluster"][0] = "mutated cluster error"
        all_errors["namespaces"][0] = "mutated namespace error"
        all_errors["extra"] = ["mutated extra error"]

        second_valid, second_errors = validator.validate_all_permissions(skip_observability=True)

        report = validator.generate_permission_report(skip_observability=True)

        assert all_valid is False
        assert second_valid is False
        assert "cluster" in second_errors
        assert "namespaces" in second_errors
        assert second_errors["cluster"] == [
            "Missing permission: get cluster.open-cluster-management.io/managedclusters - Denied"
        ]
        assert second_errors["namespaces"] == [f"Missing permission in {BACKUP_NAMESPACE}: get core/pods - Denied"]
        assert "extra" not in second_errors
        assert len(validator.check_permission.call_args_list) == first_call_count
        assert "Missing permission: get cluster.open-cluster-management.io/managedclusters - Denied" in report
        assert f"Missing permission in {BACKUP_NAMESPACE}: get core/pods - Denied" in report
        assert "mutated cluster error" not in report
        assert "mutated namespace error" not in report
        assert "mutated extra error" not in report

    def test_generate_permission_report_forwards_all_kwargs_to_validate_all_permissions(self, validator):
        """generate_permission_report must forward all keyword arguments unchanged to validate_all_permissions.

        Mutations that drop or replace an argument (e.g. include_decommission=None instead of
        include_decommission=include_decommission) change which permissions are validated without
        any error being raised.
        """
        validator.validate_all_permissions = MagicMock(return_value=(True, {}))

        validator.generate_permission_report(
            include_decommission=True,
            include_old_hub_finalization=True,
            skip_observability=True,
            argocd_mode="check",
            argocd_install_type="operator",
        )

        validator.validate_all_permissions.assert_called_once_with(
            include_decommission=True,
            include_old_hub_finalization=True,
            skip_observability=True,
            argocd_mode="check",
            argocd_install_type="operator",
        )

    def test_validate_decommission_permissions_reuses_cached_summary(self, validator):
        """Repeated decommission validation should reuse the first summary for the same options."""
        validator.client.namespace_exists.return_value = True
        validator.check_permission = MagicMock(return_value=(True, ""))

        assert validator.validate_decommission_permissions(skip_observability=True) == (True, {})
        first_call_count = len(validator.check_permission.call_args_list)
        cached_valid, cached_errors = validator.validate_decommission_permissions(skip_observability=True)
        cached_errors["cluster"] = ["mutated cluster error"]
        cached_errors["namespaces"] = ["mutated namespace error"]

        assert cached_valid is True
        assert validator.validate_decommission_permissions(skip_observability=True) == (True, {})
        assert len(validator.check_permission.call_args_list) == first_call_count
        assert validator.client.namespace_exists.call_args_list.count(call(ACM_NAMESPACE)) == 1

    def test_validate_decommission_permissions_checks_exact_permission_set(self, validator):
        """validate_decommission_permissions must check the complete DECOMMISSION permission sets.

        Mutations that skip loop iterations, change permission names/verbs, or alter namespace
        routing are caught because this test asserts the exact frozenset of check_permission calls.
        """
        validator.client.namespace_exists.return_value = True
        validator.check_permission = MagicMock(return_value=(True, ""))

        all_valid, errors = validator.validate_decommission_permissions(skip_observability=False)

        assert all_valid is True
        assert errors == {}

        expected = frozenset(
            (ag, r, v, None) for ag, r, vbs in RBACValidator.DECOMMISSION_CLUSTER_PERMISSIONS for v in vbs
        ) | frozenset(
            (ag, r, v, ns)
            for ns, perms in RBACValidator.DECOMMISSION_NAMESPACE_PERMISSIONS.items()
            for ag, r, vbs in perms
            for v in vbs
        )
        actual = frozenset(
            (c.args[0], c.args[1], c.args[2], c.args[3])
            for c in validator.check_permission.call_args_list
            if len(c.args) >= 4
        )
        assert actual == expected, (
            f"Decommission permission set mismatch.\n"
            f"  Missing: {expected - actual}\n"
            f"  Unexpected: {actual - expected}"
        )

    def test_validate_managed_cluster_permissions_success(self, validator):
        """Test validate_managed_cluster_permissions when all permissions exist."""
        validator.client.namespace_exists.return_value = True
        validator.check_permission = MagicMock(return_value=(True, ""))

        all_valid, errors = validator.validate_managed_cluster_permissions()

        assert all_valid is True
        assert len(errors) == 0
        # Verify it checked the agent namespace
        validator.client.namespace_exists.assert_called_with("open-cluster-management-agent")

    def test_validate_managed_cluster_permissions_checks_exact_permission_set(self, validator):
        """validate_managed_cluster_permissions must check the exact OPERATOR_MANAGED_CLUSTER_NAMESPACE_PERMISSIONS set."""
        validator.client.namespace_exists.return_value = True
        validator.check_permission = MagicMock(return_value=(True, ""))

        all_valid, errors = validator.validate_managed_cluster_permissions()

        assert all_valid is True
        assert errors == []

        expected = frozenset(
            (ag, r, v, ns)
            for ns, perms in RBACValidator.OPERATOR_MANAGED_CLUSTER_NAMESPACE_PERMISSIONS.items()
            for ag, r, vbs in perms
            for v in vbs
        )
        actual = frozenset(
            (c.args[0], c.args[1], c.args[2], c.args[3])
            for c in validator.check_permission.call_args_list
            if len(c.args) >= 4
        )
        assert actual == expected, (
            f"Managed cluster permission set mismatch.\n"
            f"  Missing: {expected - actual}\n"
            f"  Unexpected: {actual - expected}"
        )

    def test_validate_managed_cluster_permissions_namespace_missing(self, validator):
        """Test validate_managed_cluster_permissions when namespace doesn't exist."""
        validator.client.namespace_exists.return_value = False
        validator.check_permission = MagicMock(return_value=(True, ""))

        all_valid, errors = validator.validate_managed_cluster_permissions()

        assert all_valid is False
        assert len(errors) > 0
        assert any("does not exist" in error for error in errors)

    def test_validate_managed_cluster_permissions_failure(self, validator):
        """Test validate_managed_cluster_permissions when some permissions missing."""
        validator.client.namespace_exists.return_value = True

        def mock_check(api_group, resource, verb, namespace=None):
            if resource == "secrets" and verb == "patch":
                return (False, "Permission denied")
            return (True, "")

        validator.check_permission = MagicMock(side_effect=mock_check)

        all_valid, errors = validator.validate_managed_cluster_permissions()

        assert all_valid is False
        assert len(errors) > 0
        assert any("secrets" in error for error in errors)

    def test_operator_managed_cluster_secret_permissions_patch_without_delete(self):
        """Operator remediation should patch or create bootstrap secrets, not delete them."""
        perms = RBACValidator.OPERATOR_MANAGED_CLUSTER_NAMESPACE_PERMISSIONS[MANAGED_CLUSTER_AGENT_NAMESPACE]
        secrets_perm = next((p for p in perms if p[1] == "secrets"), None)

        assert secrets_perm is not None
        assert "get" in secrets_perm[2]
        assert "create" in secrets_perm[2]
        assert "patch" in secrets_perm[2]
        assert "delete" not in secrets_perm[2]

    def test_validate_managed_cluster_permissions_validator_role(self, mock_client):
        """Test validate_managed_cluster_permissions with validator role (read-only)."""
        validator = RBACValidator(mock_client, role="validator")
        validator.client.namespace_exists.return_value = True
        validator.check_permission = MagicMock(return_value=(True, ""))

        all_valid, errors = validator.validate_managed_cluster_permissions()

        assert all_valid is True
        # Validator should only check get verbs, not create/delete
        calls = validator.check_permission.call_args_list
        verbs_checked = [c.args[2] if len(c.args) > 2 else c.kwargs.get("verb") for c in calls]
        assert "create" not in verbs_checked
        assert "patch" not in verbs_checked
        assert "delete" not in verbs_checked
        assert "get" in verbs_checked


class TestValidatorClusterTableDerivation:
    """H1: VALIDATOR_CLUSTER_PERMISSIONS is derived from OPERATOR_CLUSTER_PERMISSIONS."""

    EXPECTED_VALIDATOR_TABLE = [
        ("", "namespaces", ["get", "list"]),
        ("", "nodes", ["get", "list"]),
        ("config.openshift.io", "clusteroperators", ["get", "list"]),
        ("config.openshift.io", "clusterversions", ["get", "list"]),
        ("cluster.open-cluster-management.io", "managedclusters", ["get", "list"]),
        ("hive.openshift.io", "clusterdeployments", ["get", "list"]),
        ("operator.open-cluster-management.io", "multiclusterhubs", ["get", "list"]),
        ("observability.open-cluster-management.io", "multiclusterobservabilities", ["get", "list"]),
    ]

    def test_validator_table_matches_pre_derivation_literal(self):
        """Regression pin: derived table equals the exact table shipped before H1."""
        assert RBACValidator.VALIDATOR_CLUSTER_PERMISSIONS == self.EXPECTED_VALIDATOR_TABLE

    def test_validator_table_is_derived_from_operator_table(self):
        derived = rbac_validator._derive_read_only_permissions(
            RBACValidator.OPERATOR_CLUSTER_PERMISSIONS,
            rbac_validator.VALIDATOR_CLUSTER_VERB_EXCEPTIONS,
        )
        assert derived == RBACValidator.VALIDATOR_CLUSTER_PERMISSIONS

    def test_managedclusters_patch_exception_is_explicit_data(self):
        assert rbac_validator.VALIDATOR_CLUSTER_VERB_EXCEPTIONS == {
            (MANAGED_CLUSTER_API_GROUP, MANAGED_CLUSTER_PLURAL): frozenset({"patch"})
        }

    def test_managedclusters_patch_stripped_for_validator(self):
        operator_rule = next(
            r
            for r in RBACValidator.OPERATOR_CLUSTER_PERMISSIONS
            if (r[0], r[1]) == (MANAGED_CLUSTER_API_GROUP, MANAGED_CLUSTER_PLURAL)
        )
        validator_rule = next(
            r
            for r in RBACValidator.VALIDATOR_CLUSTER_PERMISSIONS
            if (r[0], r[1]) == (MANAGED_CLUSTER_API_GROUP, MANAGED_CLUSTER_PLURAL)
        )
        assert "patch" in operator_rule[2]
        assert "patch" not in validator_rule[2]
        assert [v for v in operator_rule[2] if v != "patch"] == validator_rule[2]

    def test_unexpected_mutating_verb_fails_derivation(self):
        perms = [("group.example.io", "widgets", ["get", "delete"])]
        with pytest.raises(ValueError, match="drifted") as exc_info:
            rbac_validator._derive_read_only_permissions(perms, {})
        # The error must name the stripped verbs, not just the resource keys,
        # so an import-time drift failure is diagnosable from the message alone.
        assert "'delete'" in str(exc_info.value)

    def test_stale_exception_entry_fails_derivation(self):
        perms = [("group.example.io", "widgets", ["get", "list"])]
        expected = {("group.example.io", "widgets"): frozenset({"delete"})}
        with pytest.raises(ValueError, match="drifted") as exc_info:
            rbac_validator._derive_read_only_permissions(perms, expected)
        assert "'delete'" in str(exc_info.value)

    def test_mutating_verbs_match_write_verb_helper(self):
        client = MagicMock()
        client.context = "test-context"
        validator = RBACValidator(client)
        for verb in ("create", "update", "patch", "delete"):
            assert validator._is_write_verb(verb)
            assert verb in rbac_validator.MUTATING_VERBS
        for verb in ("get", "list", "watch"):
            assert not validator._is_write_verb(verb)
            assert verb not in rbac_validator.MUTATING_VERBS


class TestValidateRBACPermissions:
    """Test cases for validate_rbac_permissions function."""

    @pytest.fixture
    def mock_primary_client(self):
        """Create a mock primary KubeClient."""
        client = MagicMock()
        client.context = "primary-hub"
        client.namespace_exists = MagicMock(return_value=True)
        return client

    @pytest.fixture
    def mock_secondary_client(self):
        """Create a mock secondary KubeClient."""
        client = MagicMock()
        client.context = "secondary-hub"
        client.namespace_exists = MagicMock(return_value=True)
        return client

    @patch("lib.rbac_validator.RBACValidator")
    def test_validate_primary_only_success(self, mock_validator_class, mock_primary_client):
        """Test validate_rbac_permissions with only primary hub."""
        mock_validator = MagicMock()
        mock_validator.validate_all_permissions.return_value = (True, {})
        mock_validator_class.return_value = mock_validator

        # Should not raise exception
        validate_rbac_permissions(mock_primary_client)

    @patch("lib.rbac_validator.RBACValidator")
    def test_validate_both_hubs_success(self, mock_validator_class, mock_primary_client, mock_secondary_client):
        """Test validate_rbac_permissions with both hubs."""
        mock_validator = MagicMock()
        mock_validator.validate_all_permissions.return_value = (True, {})
        mock_validator_class.return_value = mock_validator

        # Should not raise exception
        validate_rbac_permissions(mock_primary_client, mock_secondary_client)

    @patch("lib.rbac_validator.RBACValidator")
    def test_validate_primary_failure(self, mock_validator_class, mock_primary_client):
        """Test validate_rbac_permissions when primary validation fails."""
        mock_validator = MagicMock()
        mock_validator.validate_all_permissions.return_value = (
            False,
            {"cluster": ["Missing permission: get pods"]},
        )
        mock_validator.generate_permission_report.return_value = "Error report"
        mock_validator_class.return_value = mock_validator

        with pytest.raises(ValidationError) as exc_info:
            validate_rbac_permissions(mock_primary_client)

        assert "primary hub" in str(exc_info.value)

    @patch("lib.rbac_validator.RBACValidator")
    def test_validate_secondary_failure(self, mock_validator_class, mock_primary_client, mock_secondary_client):
        """Test validate_rbac_permissions when secondary validation fails."""

        # Primary succeeds, secondary fails
        def mock_validate(
            include_decommission=False,
            include_old_hub_finalization=False,
            skip_observability=False,
            argocd_mode="none",
            argocd_install_type="unknown",
        ):
            if mock_validator_class.call_count == 1:
                # Primary validation
                return (True, {})
            else:
                # Secondary validation
                return (False, {"cluster": ["Missing permission"]})

        mock_validator = MagicMock()
        mock_validator.validate_all_permissions.side_effect = mock_validate
        mock_validator.generate_permission_report.return_value = "Error report"
        mock_validator_class.return_value = mock_validator

        with pytest.raises(ValidationError) as exc_info:
            validate_rbac_permissions(mock_primary_client, mock_secondary_client)

        assert "secondary hub" in str(exc_info.value)

    @patch("lib.rbac_validator.RBACValidator")
    def test_validate_with_decommission(self, mock_validator_class, mock_primary_client):
        """Test validate_rbac_permissions with decommission permissions."""
        mock_validator = MagicMock()
        mock_validator.validate_all_permissions.return_value = (True, {})
        mock_validator_class.return_value = mock_validator

        validate_rbac_permissions(mock_primary_client, include_decommission=True)

        # Verify decommission was passed
        mock_validator.validate_all_permissions.assert_called_with(
            include_decommission=True,
            include_old_hub_finalization=False,
            skip_observability=False,
            argocd_mode="none",
            argocd_install_type="unknown",
        )

    @patch("lib.rbac_validator.RBACValidator")
    def test_validate_old_hub_finalization_only_applies_to_primary(
        self, mock_validator_class, mock_primary_client, mock_secondary_client
    ):
        """Only the old hub needs MCO delete during normal finalization."""
        primary_validator = MagicMock()
        primary_validator.validate_all_permissions.return_value = (True, {})
        secondary_validator = MagicMock()
        secondary_validator.validate_all_permissions.return_value = (True, {})
        mock_validator_class.side_effect = [primary_validator, secondary_validator]

        validate_rbac_permissions(
            mock_primary_client,
            mock_secondary_client,
            include_old_hub_finalization=True,
        )

        primary_validator.validate_all_permissions.assert_called_once_with(
            include_decommission=False,
            include_old_hub_finalization=True,
            skip_observability=False,
            argocd_mode="none",
            argocd_install_type="unknown",
        )
        secondary_validator.validate_all_permissions.assert_called_once_with(
            include_decommission=False,
            include_old_hub_finalization=False,
            skip_observability=False,
            argocd_mode="none",
            argocd_install_type="unknown",
        )

    @patch("lib.rbac_validator.RBACValidator")
    def test_validate_primary_checker_failure_raises_contextual_error(self, mock_validator_class, mock_primary_client):
        """Infrastructure failures should bubble with primary-hub context."""
        mock_validator = MagicMock()
        mock_validator.validate_all_permissions.side_effect = ValidationError("auth check failed")
        mock_validator_class.return_value = mock_validator

        with pytest.raises(ValidationError, match="could not be completed on primary hub"):
            validate_rbac_permissions(mock_primary_client)

    @patch("lib.rbac_validator.RBACValidator")
    def test_validate_secondary_checker_failure_raises_contextual_error(
        self, mock_validator_class, mock_primary_client, mock_secondary_client
    ):
        """Infrastructure failures should bubble with secondary-hub context."""
        primary_validator = MagicMock()
        primary_validator.validate_all_permissions.return_value = (True, {})
        secondary_validator = MagicMock()
        secondary_validator.validate_all_permissions.side_effect = ValidationError("auth check failed")
        mock_validator_class.side_effect = [primary_validator, secondary_validator]

        with pytest.raises(ValidationError, match="could not be completed on secondary hub"):
            validate_rbac_permissions(mock_primary_client, mock_secondary_client)

    @patch("lib.rbac_validator.RBACValidator")
    def test_validate_skip_observability(self, mock_validator_class, mock_primary_client):
        """Test validate_rbac_permissions with skip_observability."""
        mock_validator = MagicMock()
        mock_validator.validate_all_permissions.return_value = (True, {})
        mock_validator_class.return_value = mock_validator

        validate_rbac_permissions(mock_primary_client, skip_observability=True)

        # Verify skip_observability was passed
        mock_validator.validate_all_permissions.assert_called_with(
            include_decommission=False,
            include_old_hub_finalization=False,
            skip_observability=True,
            argocd_mode="none",
            argocd_install_type="unknown",
        )

    @patch("lib.rbac_validator.RBACValidator")
    def test_validate_argocd_mode_manage(self, mock_validator_class, mock_primary_client):
        """Test validate_rbac_permissions forwards argocd_mode to validators."""
        mock_validator = MagicMock()
        mock_validator.validate_all_permissions.return_value = (True, {})
        mock_validator_class.return_value = mock_validator

        validate_rbac_permissions(mock_primary_client, argocd_mode="manage")

        mock_validator.validate_all_permissions.assert_called_with(
            include_decommission=False,
            include_old_hub_finalization=False,
            skip_observability=False,
            argocd_mode="manage",
            argocd_install_type="unknown",
        )

    @patch("lib.rbac_validator.RBACValidator")
    def test_validate_both_hubs_use_separate_argocd_install_types(
        self, mock_validator_class, mock_primary_client, mock_secondary_client
    ):
        """Primary and secondary hubs may require different Argo CD RBAC surfaces."""
        primary_validator = MagicMock()
        primary_validator.validate_all_permissions.return_value = (True, {})
        secondary_validator = MagicMock()
        secondary_validator.validate_all_permissions.return_value = (True, {})
        mock_validator_class.side_effect = [primary_validator, secondary_validator]

        validate_rbac_permissions(
            mock_primary_client,
            mock_secondary_client,
            argocd_mode="check",
            argocd_install_type="operator",
            secondary_argocd_install_type="vanilla",
        )

        primary_validator.validate_all_permissions.assert_called_once_with(
            include_decommission=False,
            include_old_hub_finalization=False,
            skip_observability=False,
            argocd_mode="check",
            argocd_install_type="operator",
        )
        secondary_validator.validate_all_permissions.assert_called_once_with(
            include_decommission=False,
            include_old_hub_finalization=False,
            skip_observability=False,
            argocd_mode="check",
            argocd_install_type="vanilla",
        )

    @patch("lib.rbac_validator.RBACValidator")
    def test_validate_invalid_argocd_mode_raises(self, mock_validator_class, mock_primary_client):
        """Test validate_rbac_permissions rejects invalid argocd_mode values."""
        with pytest.raises(ValueError):
            validate_rbac_permissions(mock_primary_client, argocd_mode="invalid")
        mock_validator_class.assert_not_called()

    def test_validate_both_clients_none_raises(self):
        """Test validate_rbac_permissions raises ValueError when both clients are None."""
        with pytest.raises(ValueError, match="At least one of primary_client or secondary_client"):
            validate_rbac_permissions(None, None)

    def test_validate_decommission_without_primary_raises(self, mock_secondary_client):
        """Test validate_rbac_permissions raises ValueError for decommission without primary."""
        with pytest.raises(ValueError, match="include_decommission requires primary_client"):
            validate_rbac_permissions(None, mock_secondary_client, include_decommission=True)

    @patch("lib.rbac_validator.RBACValidator")
    def test_validate_secondary_only_success(self, mock_validator_class, mock_secondary_client):
        """Test validate_rbac_permissions with only secondary hub (restore-only mode)."""
        mock_validator = MagicMock()
        mock_validator.validate_all_permissions.return_value = (True, {})
        mock_validator_class.return_value = mock_validator

        validate_rbac_permissions(None, mock_secondary_client)

        # Should only create one validator (for secondary)
        mock_validator_class.assert_called_once_with(mock_secondary_client)

    @patch("lib.rbac_validator.RBACValidator")
    def test_validate_secondary_only_failure(self, mock_validator_class, mock_secondary_client):
        """Test validate_rbac_permissions fails when secondary-only RBAC check fails."""
        mock_validator = MagicMock()
        mock_validator.validate_all_permissions.return_value = (
            False,
            {"cluster": ["Missing permission: create restores"]},
        )
        mock_validator.generate_permission_report.return_value = "Error report"
        mock_validator_class.return_value = mock_validator

        with pytest.raises(ValidationError, match="secondary hub"):
            validate_rbac_permissions(None, mock_secondary_client)


class TestValidateHubLoop:
    """H1: primary/secondary validation routes through one hub-parameterized helper."""

    @pytest.fixture
    def mock_primary_client(self):
        client = MagicMock()
        client.context = "primary-hub"
        return client

    @pytest.fixture
    def mock_secondary_client(self):
        client = MagicMock()
        client.context = "secondary-hub"
        return client

    def test_both_hubs_route_through_validate_hub_with_explicit_asymmetries(
        self, mock_primary_client, mock_secondary_client, monkeypatch
    ):
        calls = []
        monkeypatch.setattr(
            rbac_validator,
            "_validate_hub",
            lambda hub_role, client, **kwargs: calls.append((hub_role, client, kwargs)),
        )
        validate_rbac_permissions(
            mock_primary_client,
            mock_secondary_client,
            include_decommission=True,
            include_old_hub_finalization=True,
            skip_observability=True,
            argocd_mode="check",
            argocd_install_type="operator",
            secondary_argocd_install_type="vanilla",
        )
        assert [(hub_role, client) for hub_role, client, _ in calls] == [
            ("primary", mock_primary_client),
            ("secondary", mock_secondary_client),
        ]
        primary_kwargs = calls[0][2]
        secondary_kwargs = calls[1][2]
        # Primary-only asymmetries: decommission/old-hub-finalization pass through.
        assert primary_kwargs["include_decommission"] is True
        assert primary_kwargs["include_old_hub_finalization"] is True
        assert primary_kwargs["argocd_install_type"] == "operator"
        assert primary_kwargs["include_error_count"] is False
        # Secondary-only asymmetries: flags forced off, install-type override, error count.
        assert secondary_kwargs["include_decommission"] is False
        assert secondary_kwargs["include_old_hub_finalization"] is False
        assert secondary_kwargs["argocd_install_type"] == "vanilla"
        assert secondary_kwargs["include_error_count"] is True
        # Shared flags reach both hubs unchanged.
        for kwargs in (primary_kwargs, secondary_kwargs):
            assert kwargs["skip_observability"] is True
            assert kwargs["argocd_mode"] == "check"

    def test_secondary_install_type_falls_back_to_primary_install_type(self, mock_secondary_client, monkeypatch):
        calls = []
        monkeypatch.setattr(
            rbac_validator,
            "_validate_hub",
            lambda hub_role, client, **kwargs: calls.append((hub_role, kwargs)),
        )
        validate_rbac_permissions(None, mock_secondary_client, argocd_install_type="operator")
        assert calls[0][0] == "secondary"
        assert calls[0][1]["argocd_install_type"] == "operator"

    def test_primary_absent_logs_skip_and_still_validates_secondary(self, mock_secondary_client, monkeypatch, caplog):
        calls = []
        monkeypatch.setattr(
            rbac_validator,
            "_validate_hub",
            lambda hub_role, client, **kwargs: calls.append(hub_role),
        )
        with caplog.at_level(logging.INFO, logger="acm_switchover"):
            validate_rbac_permissions(None, mock_secondary_client)
        assert calls == ["secondary"]
        assert "Primary hub not available; skipping primary RBAC validation" in caplog.text

    @patch("lib.rbac_validator.RBACValidator")
    def test_secondary_failure_message_includes_error_count(self, mock_validator_class, mock_secondary_client):
        mock_validator = MagicMock()
        mock_validator.validate_all_permissions.return_value = (
            False,
            {"cluster": ["err one", "err two"], "namespaces": ["err three"]},
        )
        mock_validator.generate_permission_report.return_value = "Error report"
        mock_validator_class.return_value = mock_validator
        with pytest.raises(ValidationError) as exc_info:
            validate_rbac_permissions(None, mock_secondary_client)
        assert (
            str(exc_info.value)
            == "RBAC permission validation failed on secondary hub (3 error(s)). See report above for details."
        )

    @patch("lib.rbac_validator.RBACValidator")
    def test_primary_failure_message_has_no_error_count(self, mock_validator_class, mock_primary_client):
        mock_validator = MagicMock()
        mock_validator.validate_all_permissions.return_value = (False, {"cluster": ["err one"]})
        mock_validator.generate_permission_report.return_value = "Error report"
        mock_validator_class.return_value = mock_validator
        with pytest.raises(ValidationError) as exc_info:
            validate_rbac_permissions(mock_primary_client)
        assert str(exc_info.value) == "RBAC permission validation failed on primary hub. See report above for details."


class TestValidateDecommissionPermissions:
    """Tests for standalone decommission RBAC validation."""

    @pytest.fixture
    def mock_primary_client(self):
        client = MagicMock()
        client.context = "primary-hub"
        client.namespace_exists = MagicMock(return_value=True)
        return client

    @patch("lib.rbac_validator.RBACValidator")
    def test_validate_decommission_permissions_uses_dedicated_validation_path(
        self, mock_validator_class, mock_primary_client
    ):
        """Standalone decommission should use its dedicated RBAC surface."""
        validator = RBACValidator(mock_primary_client)
        validator.validate_decommission_permissions = MagicMock(return_value=(True, {}))
        mock_validator_class.return_value = validator

        validate_decommission_permissions(mock_primary_client, skip_observability=True)

        validator.validate_decommission_permissions.assert_called_once_with(
            skip_observability=True,
        )

    def test_validate_decommission_permissions_fails_when_teardown_namespace_permission_missing(
        self, mock_primary_client
    ):
        validator = RBACValidator(mock_primary_client)

        def check_permission(api_group, resource, verb, namespace=None):
            if namespace == ACM_NAMESPACE and api_group == "" and resource == "pods" and verb == "get":
                return (False, "Permission denied")
            return (True, "")

        validator.check_permission = MagicMock(side_effect=check_permission)

        with patch("lib.rbac_validator.RBACValidator", return_value=validator):
            with pytest.raises(ValidationError, match="Decommission RBAC permission validation failed"):
                validate_decommission_permissions(mock_primary_client, skip_observability=True)

    def test_validate_decommission_permissions_checks_only_teardown_surface(self, mock_primary_client):
        validator = RBACValidator(mock_primary_client)
        validator.check_permission = MagicMock(return_value=(True, ""))

        def namespace_exists(namespace):
            if namespace in {"open-cluster-management-backup", "multicluster-engine"}:
                raise AssertionError(f"unexpected namespace probe: {namespace}")
            return True

        mock_primary_client.namespace_exists.side_effect = namespace_exists

        with patch("lib.rbac_validator.RBACValidator", return_value=validator):
            validate_decommission_permissions(mock_primary_client, skip_observability=False)

        assert (
            call("cluster.open-cluster-management.io", "managedclusters", "delete", None)
            in validator.check_permission.call_args_list
        )
        assert (
            call("operator.open-cluster-management.io", "multiclusterhubs", "list", None)
            in validator.check_permission.call_args_list
        )
        assert (
            call("hive.openshift.io", "clusterdeployments", "list", None) in validator.check_permission.call_args_list
        )
        assert (
            call("hive.openshift.io", "clusterdeployments", "get", None)
            not in validator.check_permission.call_args_list
        )
        assert call("", "pods", "get", OBSERVABILITY_NAMESPACE) in validator.check_permission.call_args_list

    def test_validate_decommission_rbac_succeeds_when_acm_namespace_missing(self, mock_primary_client):
        """Missing ACM namespace on rerun should NOT fail validation (idempotent)."""
        mock_primary_client.namespace_exists.side_effect = lambda ns: {
            "open-cluster-management": False,
            "open-cluster-management-observability": True,
        }.get(ns, False)
        mock_primary_client.check_permission.return_value = (True, None)

        validator = RBACValidator(client=mock_primary_client, role="operator")
        all_valid, errors = validator.validate_decommission_permissions()

        assert all_valid is True
        assert not errors.get("namespaces", [])
