"""
Unit tests for RBAC validator module.
"""

from unittest.mock import MagicMock, patch

import pytest

from lib.exceptions import ValidationError
from lib.rbac_validator import RBACValidator, validate_rbac_permissions


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

    def test_init(self, mock_client):
        """Test RBACValidator initialization."""
        validator = RBACValidator(mock_client)
        assert validator.client == mock_client

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

    def test_validate_cluster_permissions_success(self, validator):
        """Test validate_cluster_permissions when all permissions exist."""
        # Mock check_permission to always return True
        validator.check_permission = MagicMock(return_value=(True, ""))

        all_valid, errors = validator.validate_cluster_permissions()

        assert all_valid is True
        assert len(errors) == 0

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

    def test_validate_namespace_permissions_success(self, validator):
        """Test validate_namespace_permissions when all permissions exist."""
        # Mock namespace_exists and check_permission
        validator.client.namespace_exists.return_value = True
        validator.check_permission = MagicMock(return_value=(True, ""))

        all_valid, errors = validator.validate_namespace_permissions()

        assert all_valid is True
        assert len(errors) == 0

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
        def mock_validate(include_decommission=False, skip_observability=False):
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
        mock_validator.validate_all_permissions.assert_called_with(include_decommission=True, skip_observability=False)

    @patch("lib.rbac_validator.RBACValidator")
    def test_validate_skip_observability(self, mock_validator_class, mock_primary_client):
        """Test validate_rbac_permissions with skip_observability."""
        mock_validator = MagicMock()
        mock_validator.validate_all_permissions.return_value = (True, {})
        mock_validator_class.return_value = mock_validator

        validate_rbac_permissions(mock_primary_client, skip_observability=True)

        # Verify skip_observability was passed
        mock_validator.validate_all_permissions.assert_called_with(include_decommission=False, skip_observability=True)
