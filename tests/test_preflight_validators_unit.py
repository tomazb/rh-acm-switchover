"""Unit tests for individual validator classes.

Tests cover core validator logic with success and failure cases for each validator.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import pytest

from modules.preflight.backup_validators import (
    BackupScheduleValidator,
    BackupStorageLocationValidator,
    BackupValidator,
    ManagedClusterBackupValidator,
    PassiveSyncValidator,
)
from modules.preflight.cluster_validators import ClusterDeploymentValidator
from modules.preflight.namespace_validators import (
    NamespaceValidator,
    ObservabilityDetector,
    ObservabilityPrereqValidator,
    ToolingValidator,
)
from modules.preflight.reporter import ValidationReporter
from modules.preflight.version_validators import (
    AutoImportStrategyValidator,
    HubComponentValidator,
    KubeconfigValidator,
    VersionValidator,
)


@pytest.fixture
def reporter():
    """Create a validation reporter for tests."""
    return ValidationReporter()


@pytest.fixture
def mock_kube_client():
    """Create a mock Kubernetes client."""
    client = Mock()
    return client


class TestBackupValidator:
    """Tests for BackupValidator."""

    def test_no_backups_found(self, reporter, mock_kube_client):
        """Test critical failure when no backups exist."""
        validator = BackupValidator(reporter)
        # Mock empty backup list
        mock_kube_client.list_custom_resources.return_value = []

        validator.run(mock_kube_client)

        # Should have critical failure result
        results = reporter.results
        assert len(results) == 1
        assert results[0]["check"] == "Backup status"
        assert results[0]["passed"] is False
        assert results[0]["critical"] is True
        assert "no backups found" in results[0]["message"]

    def test_backup_in_progress(self, reporter, mock_kube_client, mocker):
        """Test critical failure when backup is in progress."""
        # Mock time functions to simulate timeout immediately
        # First call returns 0, second call returns timeout+1 to exit loop immediately
        mocker.patch("modules.preflight.backup_validators.time.sleep")
        mocker.patch("modules.preflight.backup_validators.time.time", side_effect=[0, 601, 602, 603])

        validator = BackupValidator(reporter)
        # Mock backups with one in progress - stays in progress through all polls
        mock_kube_client.list_custom_resources.return_value = [
            {
                "metadata": {"name": "backup-in-progress", "creationTimestamp": "2025-12-31T10:00:00Z"},
                "status": {"phase": "InProgress"},
            },
            {
                "metadata": {"name": "backup-completed", "creationTimestamp": "2025-12-30T10:00:00Z"},
                "status": {"phase": "Completed", "completionTimestamp": "2025-12-30T10:05:00Z"},
            },
        ]

        validator.run(mock_kube_client)

        # Should have critical failure about in-progress backup
        results = reporter.results
        assert len(results) == 1
        assert results[0]["passed"] is False
        assert results[0]["critical"] is True
        assert "backup(s) in progress" in results[0]["message"]
        assert "backup-in-progress" in results[0]["message"]

    def test_backup_status_fails_when_backups_still_in_progress_after_wait(self, reporter, mock_kube_client):
        """Test critical failure when backups are still in progress after waiting."""
        validator = BackupValidator(reporter)
        validator._wait_for_backups_complete = Mock(return_value=["backup-a"])

        mock_kube_client.list_custom_resources.return_value = [
            {
                "metadata": {"name": "backup-a", "creationTimestamp": "2025-12-31T10:00:00Z"},
                "status": {"phase": "InProgress"},
            },
            {
                "metadata": {"name": "backup-b", "creationTimestamp": "2025-12-30T10:00:00Z"},
                "status": {"phase": "Completed", "completionTimestamp": "2025-12-30T10:05:00Z"},
            },
        ]

        validator.run(mock_kube_client)

        critical_failures = reporter.critical_failures()
        assert len(critical_failures) == 1
        assert critical_failures[0]["check"] == "Backup status"
        assert "backup(s) in progress after waiting" in critical_failures[0]["message"]
        assert "backup-a" in critical_failures[0]["message"]
        validator._wait_for_backups_complete.assert_called_once_with(mock_kube_client, ["backup-a"])

    def test_backup_status_fails_when_backups_disappear_after_wait(self, reporter, mock_kube_client):
        """Test critical failure when refresh finds no backups after waiting for completion."""
        validator = BackupValidator(reporter)
        validator._wait_for_backups_complete = Mock(return_value=[])

        mock_kube_client.list_custom_resources.side_effect = [
            [
                {
                    "metadata": {"name": "backup-a", "creationTimestamp": "2025-12-31T10:00:00Z"},
                    "status": {"phase": "InProgress"},
                }
            ],
            [],
        ]

        validator.run(mock_kube_client)

        critical_failures = reporter.critical_failures()
        assert len(critical_failures) == 1
        assert critical_failures[0]["check"] == "Backup status"
        assert "no backups found after waiting" in reporter.results[-1]["message"]
        validator._wait_for_backups_complete.assert_called_once_with(mock_kube_client, ["backup-a"])

    def test_latest_backup_failed(self, reporter, mock_kube_client):
        """Test critical failure when latest backup failed."""
        validator = BackupValidator(reporter)
        # Mock backups with failed latest backup
        mock_kube_client.list_custom_resources.return_value = [
            {
                "metadata": {"name": "backup-failed", "creationTimestamp": "2025-12-31T10:00:00Z"},
                "status": {"phase": "Failed"},
            },
            {
                "metadata": {"name": "backup-completed", "creationTimestamp": "2025-12-30T10:00:00Z"},
                "status": {"phase": "Completed", "completionTimestamp": "2025-12-30T10:05:00Z"},
            },
        ]

        validator.run(mock_kube_client)

        # Should have critical failure about failed backup
        results = reporter.results
        assert len(results) == 1
        assert results[0]["passed"] is False
        assert results[0]["critical"] is True
        assert "unexpected state: Failed" in results[0]["message"]
        assert "backup-failed" in results[0]["message"]

    def test_latest_backup_completed_fresh(self, reporter, mock_kube_client):
        """Test success with fresh completed backup."""
        validator = BackupValidator(reporter)
        # Mock fresh completed backup (very recent, less than 1 hour old)
        # Use current time to ensure it's detected as fresh
        now = datetime.now(timezone.utc)
        recent_time = (now - timedelta(minutes=5)).replace(second=0, microsecond=0).isoformat().replace("+00:00", "Z")

        mock_kube_client.list_custom_resources.return_value = [
            {
                "metadata": {"name": "backup-fresh", "creationTimestamp": recent_time},
                "status": {"phase": "Completed", "completionTimestamp": recent_time},
            }
        ]

        validator.run(mock_kube_client)

        # Should have success result with fresh indicator
        results = reporter.results
        assert len(results) == 1
        assert results[0]["check"] == "Backup status"
        assert results[0]["passed"] is True
        assert results[0]["critical"] is True
        assert "backup-fresh completed successfully" in results[0]["message"]
        assert "FRESH" in results[0]["message"]

    def test_latest_backup_completed_old(self, reporter, mock_kube_client):
        """Test success with old completed backup (shows age warning)."""
        validator = BackupValidator(reporter)
        # Mock old completed backup (more than 24 hours)
        mock_kube_client.list_custom_resources.return_value = [
            {
                "metadata": {"name": "backup-old", "creationTimestamp": "2025-12-28T10:00:00Z"},
                "status": {"phase": "Completed", "completionTimestamp": "2025-12-28T10:05:00Z"},
            }
        ]

        validator.run(mock_kube_client)

        # Should have success result but with age warning
        results = reporter.results
        assert len(results) == 1
        assert results[0]["check"] == "Backup status"
        assert results[0]["passed"] is True
        assert results[0]["critical"] is True
        assert "backup-old completed successfully" in results[0]["message"]
        assert "consider running a fresh backup" in results[0]["message"]

    def test_api_error_handling(self, reporter, mock_kube_client):
        """Test error handling when API call fails."""
        validator = BackupValidator(reporter)
        # Mock API exception
        mock_kube_client.list_custom_resources.side_effect = RuntimeError("API error")

        validator.run(mock_kube_client)

        # Should have critical failure result
        results = reporter.results
        assert len(results) == 1
        assert results[0]["check"] == "Backup status"
        assert results[0]["passed"] is False
        assert results[0]["critical"] is True
        assert "error checking backups: API error" in results[0]["message"]


class TestBackupScheduleValidator:
    """Tests for BackupScheduleValidator."""

    def test_no_backup_schedule_found(self, reporter, mock_kube_client):
        """Test critical failure when no BackupSchedule exists."""
        validator = BackupScheduleValidator(reporter)
        # Mock empty backup schedule list
        mock_kube_client.list_custom_resources.return_value = []

        validator.run(mock_kube_client)

        # Should have critical failure result
        results = reporter.results
        assert len(results) == 1
        assert results[0]["check"] == "BackupSchedule configuration"
        assert results[0]["passed"] is False
        assert results[0]["critical"] is True
        assert "no BackupSchedule found" in results[0]["message"]

    def test_use_managed_service_account_enabled(self, reporter, mock_kube_client):
        """Test success when useManagedServiceAccount is enabled."""
        validator = BackupScheduleValidator(reporter)
        # Mock BackupSchedule with useManagedServiceAccount=true
        mock_kube_client.list_custom_resources.return_value = [
            {"metadata": {"name": "acm-backup-schedule"}, "spec": {"useManagedServiceAccount": True}}
        ]

        validator.run(mock_kube_client)

        # Should have success result
        results = reporter.results
        assert len(results) == 1
        assert results[0]["check"] == "BackupSchedule configuration"
        assert results[0]["passed"] is True
        assert results[0]["critical"] is True
        assert "useManagedServiceAccount=true" in results[0]["message"]
        assert "managed clusters will auto-reconnect" in results[0]["message"]

    def test_use_managed_service_account_disabled(self, reporter, mock_kube_client):
        """Test critical failure when useManagedServiceAccount is disabled."""
        validator = BackupScheduleValidator(reporter)
        # Mock BackupSchedule with useManagedServiceAccount=false
        mock_kube_client.list_custom_resources.return_value = [
            {"metadata": {"name": "acm-backup-schedule"}, "spec": {"useManagedServiceAccount": False}}
        ]

        validator.run(mock_kube_client)

        # Should have critical failure result
        results = reporter.results
        assert len(results) == 1
        assert results[0]["check"] == "BackupSchedule configuration"
        assert results[0]["passed"] is False
        assert results[0]["critical"] is True
        assert "useManagedServiceAccount is not enabled" in results[0]["message"]
        assert "Managed clusters will NOT auto-reconnect" in results[0]["message"]

    def test_use_managed_service_account_missing(self, reporter, mock_kube_client):
        """Test critical failure when useManagedServiceAccount field is missing."""
        validator = BackupScheduleValidator(reporter)
        # Mock BackupSchedule without useManagedServiceAccount field
        mock_kube_client.list_custom_resources.return_value = [
            {"metadata": {"name": "acm-backup-schedule"}, "spec": {}}
        ]

        validator.run(mock_kube_client)

        # Should have critical failure result (defaults to False)
        results = reporter.results
        assert len(results) == 1
        assert results[0]["check"] == "BackupSchedule configuration"
        assert results[0]["passed"] is False
        assert results[0]["critical"] is True
        assert "useManagedServiceAccount is not enabled" in results[0]["message"]

    def test_api_error_handling(self, reporter, mock_kube_client):
        """Test error handling when API call fails."""
        validator = BackupScheduleValidator(reporter)
        # Mock API exception
        mock_kube_client.list_custom_resources.side_effect = RuntimeError("API error")

        validator.run(mock_kube_client)

        # Should have critical failure result
        results = reporter.results
        assert len(results) == 1
        assert results[0]["check"] == "BackupSchedule configuration"
        assert results[0]["passed"] is False
        assert results[0]["critical"] is True
        assert "error checking BackupSchedule: API error" in results[0]["message"]


class TestBackupStorageLocationValidator:
    """Tests for BackupStorageLocationValidator."""

    def test_no_bsl_found(self, reporter, mock_kube_client):
        """Test critical failure when no BSL exists."""
        validator = BackupStorageLocationValidator(reporter)
        mock_kube_client.list_custom_resources.return_value = []

        validator.run(mock_kube_client, "primary")

        results = reporter.results
        assert len(results) == 1
        assert results[0]["check"] == "BackupStorageLocation (primary)"
        assert results[0]["passed"] is False
        assert "no BackupStorageLocation found" in results[0]["message"]

    def test_bsl_available(self, reporter, mock_kube_client):
        """Test success when BSL is Available."""
        validator = BackupStorageLocationValidator(reporter)
        mock_kube_client.list_custom_resources.return_value = [
            {"metadata": {"name": "default"}, "status": {"phase": "Available"}}
        ]

        validator.run(mock_kube_client, "secondary")

        results = reporter.results
        assert len(results) == 1
        assert results[0]["check"] == "BackupStorageLocation (secondary)"
        assert results[0]["passed"] is True
        assert "Available" in results[0]["message"]

    def test_bsl_unavailable_with_conditions(self, reporter, mock_kube_client):
        """Test failure with condition details when BSL is not Available."""
        validator = BackupStorageLocationValidator(reporter)
        mock_kube_client.list_custom_resources.return_value = [
            {
                "metadata": {"name": "bsl-1"},
                "status": {
                    "phase": "Unavailable",
                    "conditions": [
                        {
                            "type": "Available",
                            "status": "False",
                            "reason": "Unavailable",
                            "message": "credentials invalid",
                        }
                    ],
                },
            }
        ]

        validator.run(mock_kube_client, "primary")

        results = reporter.results
        assert len(results) == 1
        assert results[0]["passed"] is False
        assert "bsl-1" in results[0]["message"]
        assert "Unavailable" in results[0]["message"]
        assert "credentials invalid" in results[0]["message"]


class TestClusterDeploymentValidator:
    """Tests for ClusterDeploymentValidator."""

    def test_no_cluster_deployments(self, reporter, mock_kube_client):
        """Test that no ClusterDeployments is reported as success."""
        validator = ClusterDeploymentValidator(reporter)
        mock_kube_client.list_custom_resources.return_value = []

        validator.run(mock_kube_client)

        results = reporter.results
        assert len(results) == 1
        assert results[0]["check"] == "ClusterDeployment preserveOnDelete"
        assert results[0]["passed"] is True
        assert "no ClusterDeployments found" in results[0]["message"]

    def test_all_cluster_deployments_have_preserve(self, reporter, mock_kube_client):
        """Test success when all ClusterDeployments have preserveOnDelete=true."""
        validator = ClusterDeploymentValidator(reporter)
        mock_kube_client.list_custom_resources.return_value = [
            {"metadata": {"name": "cluster1", "namespace": "ns1"}, "spec": {"preserveOnDelete": True}},
            {"metadata": {"name": "cluster2", "namespace": "ns2"}, "spec": {"preserveOnDelete": True}},
        ]

        validator.run(mock_kube_client)

        results = reporter.results
        assert len(results) == 1
        assert results[0]["passed"] is True
        assert "2 ClusterDeployments have preserveOnDelete=true" in results[0]["message"]

    def test_cluster_deployment_missing_preserve(self, reporter, mock_kube_client):
        """Test critical failure when ClusterDeployment lacks preserveOnDelete."""
        validator = ClusterDeploymentValidator(reporter)
        mock_kube_client.list_custom_resources.return_value = [
            {"metadata": {"name": "good-cluster", "namespace": "ns1"}, "spec": {"preserveOnDelete": True}},
            {"metadata": {"name": "bad-cluster", "namespace": "ns2"}, "spec": {"preserveOnDelete": False}},
        ]

        validator.run(mock_kube_client)

        results = reporter.results
        assert len(results) == 1
        assert results[0]["passed"] is False
        assert results[0]["critical"] is True
        assert "ns2/bad-cluster" in results[0]["message"]
        assert "DESTROY" in results[0]["message"]

    @patch("modules.preflight.cluster_validators.logger")
    def test_gitops_marker_record_failure_is_non_fatal(self, mock_logger, reporter, mock_kube_client):
        """Test marker recording exceptions are logged but do not fail validation."""
        validator = ClusterDeploymentValidator(reporter)
        mock_kube_client.list_custom_resources.return_value = [
            {"metadata": {"name": "cluster1", "namespace": "ns1"}, "spec": {"preserveOnDelete": True}},
        ]

        with patch(
            "lib.gitops_detector.record_gitops_markers",
            side_effect=RuntimeError("marker error"),
        ):
            validator.run(mock_kube_client)

        results = reporter.results
        assert len(results) == 1
        assert results[0]["passed"] is True
        mock_logger.warning.assert_called_once()
        assert mock_logger.warning.call_args[0][0] == "GitOps marker recording failed for %s %s: %s"
        assert mock_logger.warning.call_args[0][1] == "ClusterDeployment"


class TestKubeconfigValidator:
    """Tests for KubeconfigValidator."""

    def test_connectivity_success(self, reporter, mock_kube_client):
        """Test that API connectivity check reports success."""
        validator = KubeconfigValidator(reporter)
        mock_kube_client.list_namespaces.return_value = {"items": []}
        mock_kube_client.context = "test-context"

        # Call the internal method directly to test it
        validator._check_connectivity(mock_kube_client, "primary")

        results = reporter.results
        assert len(results) == 1
        assert results[0]["passed"] is True
        assert "Successfully connected" in results[0]["message"]

    def test_connectivity_failure(self, reporter, mock_kube_client):
        """Test that API connectivity failure is reported."""
        validator = KubeconfigValidator(reporter)
        mock_kube_client.list_namespaces.side_effect = RuntimeError("Connection refused")

        validator._check_connectivity(mock_kube_client, "primary")

        results = reporter.results
        assert len(results) == 1
        assert results[0]["passed"] is False
        assert results[0]["critical"] is True
        assert "Cannot connect" in results[0]["message"]

    def test_connectivity_failure_full_restore_adds_extra_message(self, reporter, mock_kube_client):
        """Test that full-restore method adds extra context on primary connectivity failure."""
        validator = KubeconfigValidator(reporter)
        validator.method = "full"
        mock_kube_client.list_namespaces.side_effect = RuntimeError("timeout")

        validator._check_connectivity(mock_kube_client, "primary")

        results = reporter.results
        assert results[0]["passed"] is False
        assert "Full-restore" in results[0]["message"]

    def test_connectivity_failure_secondary_no_extra_message(self, reporter, mock_kube_client):
        """Test that secondary hub connectivity failure has no full-restore extra message."""
        validator = KubeconfigValidator(reporter)
        validator.method = "full"
        mock_kube_client.list_namespaces.side_effect = RuntimeError("timeout")

        validator._check_connectivity(mock_kube_client, "secondary")

        results = reporter.results
        assert results[0]["passed"] is False
        assert "Full-restore" not in results[0]["message"]

    def test_run_calls_all_checks(self, reporter, mock_kube_client):
        """Test that run() invokes connectivity, duplicate users, and token checks."""
        validator = KubeconfigValidator(reporter)
        mock_kube_client.list_namespaces.return_value = {"items": []}
        mock_kube_client.context = "test-ctx"

        with patch.object(validator, "_check_duplicate_users"), patch.object(
            validator, "_check_token_expiration"
        ):
            validator.run(mock_kube_client, mock_kube_client, method="passive")

        assert validator.method == "passive"
        conn_results = [r for r in reporter.results if "Connectivity" in r["check"]]
        assert len(conn_results) == 2

    @patch("modules.preflight.version_validators.k8s_config", create=True)
    def test_check_duplicate_users_no_duplicates(self, mock_k8s_config, reporter, mock_kube_client):
        """Test duplicate user check when no duplicates exist."""
        validator = KubeconfigValidator(reporter)
        mock_kube_client.context = "hub1"

        with patch("kubernetes.config.list_kube_config_contexts") as mock_list:
            mock_list.return_value = (
                [
                    {"name": "hub1", "context": {"user": "user-a"}},
                    {"name": "hub2", "context": {"user": "user-b"}},
                ],
                {"name": "hub1"},
            )
            other_client = Mock()
            other_client.context = "hub2"
            validator._check_duplicate_users(mock_kube_client, other_client)

        dup_results = [r for r in reporter.results if "User Names" in r["check"]]
        assert dup_results[0]["passed"] is True

    @patch("modules.preflight.version_validators.k8s_config", create=True)
    def test_check_duplicate_users_with_collision(self, mock_k8s_config, reporter, mock_kube_client):
        """Test duplicate user check detects collision affecting our contexts."""
        validator = KubeconfigValidator(reporter)
        mock_kube_client.context = "hub1"

        with patch("kubernetes.config.list_kube_config_contexts") as mock_list:
            mock_list.return_value = (
                [
                    {"name": "hub1", "context": {"user": "same-user"}},
                    {"name": "hub2", "context": {"user": "same-user"}},
                ],
                {"name": "hub1"},
            )
            other_client = Mock()
            other_client.context = "hub2"
            validator._check_duplicate_users(mock_kube_client, other_client)

        dup_results = [r for r in reporter.results if "User Names" in r["check"]]
        assert dup_results[0]["passed"] is False
        assert "credential collision" in dup_results[0]["message"]

    @patch("modules.preflight.version_validators.k8s_config", create=True)
    def test_check_duplicate_users_exception(self, mock_k8s_config, reporter, mock_kube_client):
        """Test duplicate user check handles exceptions gracefully."""
        validator = KubeconfigValidator(reporter)
        mock_kube_client.context = "hub1"

        with patch("kubernetes.config.list_kube_config_contexts") as mock_list:
            mock_list.side_effect = Exception("kubeconfig error")
            other_client = Mock()
            other_client.context = "hub2"
            validator._check_duplicate_users(mock_kube_client, other_client)

        dup_results = [r for r in reporter.results if "User Names" in r["check"]]
        assert dup_results[0]["passed"] is False
        assert "error" in dup_results[0]["message"]

    def test_check_token_expiration_no_bearer(self, reporter, mock_kube_client):
        """Test token check when no Bearer token is present."""
        validator = KubeconfigValidator(reporter)
        mock_kube_client.context = "test-ctx"

        with patch("kubernetes.config.load_kube_config"), patch(
            "kubernetes.client.Configuration.get_default_copy"
        ) as mock_config:
            cfg = Mock()
            cfg.api_key = {"authorization": "Basic abc123"}
            mock_config.return_value = cfg

            validator._check_token_expiration(mock_kube_client, "primary")

        token_results = [r for r in reporter.results if "Token" in r["check"]]
        assert token_results[0]["passed"] is True
        assert "No Bearer token" in token_results[0]["message"]

    def test_check_token_expiration_valid_jwt(self, reporter, mock_kube_client):
        """Test token check with a valid JWT that has future expiry."""
        import base64
        import json
        import time

        validator = KubeconfigValidator(reporter)
        mock_kube_client.context = "test-ctx"

        # Create a JWT with exp claim 24 hours from now
        payload = {"exp": int(time.time()) + 86400}
        payload_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
        fake_jwt = f"header.{payload_b64}.signature"

        with patch("kubernetes.config.load_kube_config"), patch(
            "kubernetes.client.Configuration.get_default_copy"
        ) as mock_config:
            cfg = Mock()
            cfg.api_key = {"authorization": f"Bearer {fake_jwt}"}
            mock_config.return_value = cfg

            validator._check_token_expiration(mock_kube_client, "primary")

        token_results = [r for r in reporter.results if "Token" in r["check"]]
        assert token_results[0]["passed"] is True
        assert "valid for" in token_results[0]["message"]

    def test_check_token_expiration_expired_jwt(self, reporter, mock_kube_client):
        """Test token check with an expired JWT."""
        import base64
        import json
        import time

        validator = KubeconfigValidator(reporter)
        mock_kube_client.context = "test-ctx"

        payload = {"exp": int(time.time()) - 3600}  # Expired 1h ago
        payload_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
        fake_jwt = f"header.{payload_b64}.signature"

        with patch("kubernetes.config.load_kube_config"), patch(
            "kubernetes.client.Configuration.get_default_copy"
        ) as mock_config:
            cfg = Mock()
            cfg.api_key = {"authorization": f"Bearer {fake_jwt}"}
            mock_config.return_value = cfg

            validator._check_token_expiration(mock_kube_client, "primary")

        token_results = [r for r in reporter.results if "Token" in r["check"]]
        assert token_results[0]["passed"] is False
        assert "expired" in token_results[0]["message"].lower()

    def test_check_token_expiration_soon_expiry(self, reporter, mock_kube_client):
        """Test token check with a JWT expiring within warning threshold."""
        import base64
        import json
        import time

        validator = KubeconfigValidator(reporter)
        mock_kube_client.context = "test-ctx"

        # Expires in 2 hours (< TOKEN_EXPIRY_WARNING_HOURS=4)
        payload = {"exp": int(time.time()) + 7200}
        payload_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
        fake_jwt = f"header.{payload_b64}.signature"

        with patch("kubernetes.config.load_kube_config"), patch(
            "kubernetes.client.Configuration.get_default_copy"
        ) as mock_config:
            cfg = Mock()
            cfg.api_key = {"authorization": f"Bearer {fake_jwt}"}
            mock_config.return_value = cfg

            validator._check_token_expiration(mock_kube_client, "primary")

        token_results = [r for r in reporter.results if "Token" in r["check"]]
        assert token_results[0]["passed"] is True
        assert "soon" in token_results[0]["message"]

    def test_check_token_expiration_no_exp_claim(self, reporter, mock_kube_client):
        """Test token check with JWT that has no exp claim."""
        import base64
        import json

        validator = KubeconfigValidator(reporter)
        mock_kube_client.context = "test-ctx"

        payload = {"sub": "system:serviceaccount:test"}
        payload_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
        fake_jwt = f"header.{payload_b64}.signature"

        with patch("kubernetes.config.load_kube_config"), patch(
            "kubernetes.client.Configuration.get_default_copy"
        ) as mock_config:
            cfg = Mock()
            cfg.api_key = {"authorization": f"Bearer {fake_jwt}"}
            mock_config.return_value = cfg

            validator._check_token_expiration(mock_kube_client, "primary")

        token_results = [r for r in reporter.results if "Token" in r["check"]]
        assert token_results[0]["passed"] is True
        assert "no expiration" in token_results[0]["message"].lower()

    def test_check_token_expiration_invalid_jwt_format(self, reporter, mock_kube_client):
        """Test token check with malformed JWT."""
        validator = KubeconfigValidator(reporter)
        mock_kube_client.context = "test-ctx"

        with patch("kubernetes.config.load_kube_config"), patch(
            "kubernetes.client.Configuration.get_default_copy"
        ) as mock_config:
            cfg = Mock()
            cfg.api_key = {"authorization": "Bearer not.a-valid-jwt"}
            mock_config.return_value = cfg

            validator._check_token_expiration(mock_kube_client, "primary")

        token_results = [r for r in reporter.results if "Token" in r["check"]]
        assert token_results[0]["passed"] is True
        assert "Cannot decode" in token_results[0]["message"]

    def test_check_token_expiration_exception(self, reporter, mock_kube_client):
        """Test token check handles outer exceptions gracefully."""
        validator = KubeconfigValidator(reporter)
        mock_kube_client.context = "test-ctx"

        with patch("kubernetes.config.load_kube_config", side_effect=Exception("no config")):
            validator._check_token_expiration(mock_kube_client, "primary")

        token_results = [r for r in reporter.results if "Token" in r["check"]]
        assert token_results[0]["passed"] is False
        assert "error checking token" in token_results[0]["message"]


class TestHubComponentValidator:
    """Tests for HubComponentValidator."""

    def test_oadp_velero_pods_found(self, reporter, mock_kube_client):
        """Test success when Velero pods are found."""
        validator = HubComponentValidator(reporter)
        mock_kube_client.namespace_exists.return_value = True
        mock_kube_client.get_pods.return_value = [
            {"metadata": {"name": "velero-pod-1"}},
            {"metadata": {"name": "velero-pod-2"}},
        ]
        mock_kube_client.get_custom_resource.return_value = {"status": {"phase": "Ready"}}

        validator.run(mock_kube_client, "primary")

        oadp_results = [r for r in reporter.results if "OADP" in r["check"]]
        assert len(oadp_results) >= 1
        assert oadp_results[0]["passed"] is True
        assert "2 Velero pod(s)" in oadp_results[0]["message"]

    def test_oadp_namespace_missing(self, reporter, mock_kube_client):
        """Test critical failure when backup namespace doesn't exist."""
        validator = HubComponentValidator(reporter)
        mock_kube_client.namespace_exists.return_value = False

        validator.run(mock_kube_client, "primary")

        results = reporter.results
        oadp_results = [r for r in results if "OADP" in r["check"]]
        assert len(oadp_results) >= 1
        assert oadp_results[0]["passed"] is False


class TestAutoImportStrategyValidator:
    """Tests for AutoImportStrategyValidator."""

    def test_sync_strategy_with_old_acm_version(self, reporter, mock_kube_client):
        """Test that sync strategy is flagged on old ACM versions."""
        validator = AutoImportStrategyValidator(reporter)
        mock_kube_client.get_configmap.return_value = {"data": {"AUTO_IMPORT_STRATEGY": "Sync"}}

        # Test with version below 2.12 where sync wasn't supported
        validator.run(mock_kube_client, mock_kube_client, "2.11.0", "2.11.0")

        results = reporter.results
        # Should have some results about auto-import strategy
        strategy_results = [
            r for r in results if "auto-import" in r["check"].lower() or "strategy" in r["check"].lower()
        ]
        # At minimum, ensure we emitted at least one strategy-related result
        assert strategy_results


class TestNamespaceValidator:
    """Tests for NamespaceValidator."""

    def test_namespace_exists_on_both_hubs(self, reporter, mock_kube_client):
        """Test success when required namespaces exist on both hubs."""
        validator = NamespaceValidator(reporter)
        mock_kube_client.namespace_exists.return_value = True

        validator.run(mock_kube_client, mock_kube_client)

        # Should have 4 results (2 namespaces x 2 hubs)
        results = reporter.results
        assert len(results) == 4
        assert all(r["passed"] is True for r in results)

    def test_namespace_missing_on_primary(self, reporter, mock_kube_client):
        """Test critical failure when namespace missing on primary."""
        validator = NamespaceValidator(reporter)
        # First call (primary check) returns False, subsequent return True
        mock_kube_client.namespace_exists.side_effect = [False, True, True, True]

        validator.run(mock_kube_client, mock_kube_client)

        results = reporter.results
        failed = [r for r in results if not r["passed"]]
        assert len(failed) == 1
        assert failed[0]["critical"] is True
        assert "primary" in failed[0]["check"]


class TestObservabilityDetector:
    """Tests for ObservabilityDetector."""

    def test_observability_on_both_hubs(self, reporter, mock_kube_client):
        """Test detection when observability is on both hubs."""
        validator = ObservabilityDetector(reporter)
        mock_kube_client.namespace_exists.return_value = True

        result = validator.detect(mock_kube_client, mock_kube_client)

        assert result == (True, True)
        results = reporter.results
        assert len(results) == 1
        assert results[0]["passed"] is True
        assert "both hubs" in results[0]["message"]

    def test_observability_on_primary_only(self, reporter, mock_kube_client):
        """Test detection when observability is on primary only."""
        validator = ObservabilityDetector(reporter)
        mock_primary = Mock()
        mock_secondary = Mock()
        mock_primary.namespace_exists.return_value = True
        mock_secondary.namespace_exists.return_value = False

        result = validator.detect(mock_primary, mock_secondary)

        assert result == (True, False)
        results = reporter.results
        assert "primary hub only" in results[0]["message"]

    def test_no_observability(self, reporter, mock_kube_client):
        """Test detection when no observability is deployed."""
        validator = ObservabilityDetector(reporter)
        mock_kube_client.namespace_exists.return_value = False

        result = validator.detect(mock_kube_client, mock_kube_client)

        assert result == (False, False)
        results = reporter.results
        assert "not detected" in results[0]["message"]


class TestObservabilityPrereqValidator:
    """Tests for ObservabilityPrereqValidator."""

    def test_secret_present_on_secondary(self, reporter, mock_kube_client):
        """Test success when thanos object storage secret exists."""
        validator = ObservabilityPrereqValidator(reporter)
        mock_kube_client.namespace_exists.return_value = True
        mock_kube_client.secret_exists.return_value = True

        validator.run(mock_kube_client)

        results = reporter.results
        assert len(results) == 1
        assert results[0]["passed"] is True
        assert "present on secondary hub" in results[0]["message"]

    def test_secret_missing_on_secondary(self, reporter, mock_kube_client):
        """Test critical failure when thanos object storage secret missing."""
        validator = ObservabilityPrereqValidator(reporter)
        mock_kube_client.namespace_exists.return_value = True
        mock_kube_client.secret_exists.return_value = False

        validator.run(mock_kube_client)

        results = reporter.results
        assert len(results) == 1
        assert results[0]["passed"] is False
        assert results[0]["critical"] is True
        assert "missing on secondary hub" in results[0]["message"]

    def test_no_observability_namespace_skips_check(self, reporter, mock_kube_client):
        """Test that check is skipped when observability namespace doesn't exist."""
        validator = ObservabilityPrereqValidator(reporter)
        mock_kube_client.namespace_exists.return_value = False

        validator.run(mock_kube_client)

        # Should not add any results when namespace doesn't exist
        results = reporter.results
        assert len(results) == 0


class TestToolingValidator:
    """Tests for ToolingValidator."""

    @patch("modules.preflight.namespace_validators.shutil.which")
    def test_oc_found(self, mock_which, reporter):
        """Test success when oc is found in PATH."""
        mock_which.side_effect = lambda cmd: "/usr/bin/oc" if cmd == "oc" else None

        validator = ToolingValidator(reporter)
        validator.run()

        results = reporter.results
        cli_result = next(r for r in results if r["check"] == "Cluster CLI")
        assert cli_result["passed"] is True
        assert "oc found" in cli_result["message"]

    @patch("modules.preflight.namespace_validators.shutil.which")
    def test_kubectl_found_when_oc_missing(self, mock_which, reporter):
        """Test success when kubectl is found but oc is missing."""
        mock_which.side_effect = lambda cmd: "/usr/bin/kubectl" if cmd == "kubectl" else None

        validator = ToolingValidator(reporter)
        validator.run()

        results = reporter.results
        cli_result = next(r for r in results if r["check"] == "Cluster CLI")
        assert cli_result["passed"] is True
        assert "kubectl found" in cli_result["message"]

    @patch("modules.preflight.namespace_validators.shutil.which")
    def test_no_cli_tools(self, mock_which, reporter):
        """Test critical failure when neither oc nor kubectl is found."""
        mock_which.return_value = None

        validator = ToolingValidator(reporter)
        validator.run()

        results = reporter.results
        cli_result = next(r for r in results if r["check"] == "Cluster CLI")
        assert cli_result["passed"] is False
        assert cli_result["critical"] is True
        assert "Neither oc nor kubectl found" in cli_result["message"]


class TestPassiveSyncValidator:
    """Tests for PassiveSyncValidator."""

    def test_passive_sync_enabled(self, reporter, mock_kube_client):
        """Test success when passive sync is enabled."""
        validator = PassiveSyncValidator(reporter)
        mock_kube_client.list_custom_resources.return_value = []
        mock_kube_client.get_custom_resource.return_value = {
            "status": {"phase": "Enabled", "lastMessage": "Sync active"}
        }

        validator.run(mock_kube_client)

        results = reporter.results
        assert len(results) == 1
        assert results[0]["passed"] is True
        assert "Enabled" in results[0]["message"]

    def test_passive_sync_finished(self, reporter, mock_kube_client):
        """Test success when passive sync finished."""
        validator = PassiveSyncValidator(reporter)
        mock_kube_client.list_custom_resources.return_value = []
        mock_kube_client.get_custom_resource.return_value = {
            "status": {"phase": "Finished", "lastMessage": "Sync completed"}
        }

        validator.run(mock_kube_client)

        results = reporter.results
        assert len(results) == 1
        assert results[0]["passed"] is True

    def test_passive_sync_running(self, reporter, mock_kube_client):
        """Test success when passive sync is actively running (transient state during sync)."""
        validator = PassiveSyncValidator(reporter)
        mock_kube_client.list_custom_resources.return_value = []
        mock_kube_client.get_custom_resource.return_value = {
            "status": {"phase": "Running", "lastMessage": "Velero restore executing"}
        }

        validator.run(mock_kube_client)

        results = reporter.results
        assert len(results) == 1
        assert results[0]["passed"] is True
        assert "Running" in results[0]["message"]

    def test_passive_sync_unknown(self, reporter, mock_kube_client):
        """Test success when passive sync is in Unknown state (transient during Velero sync)."""
        validator = PassiveSyncValidator(reporter)
        mock_kube_client.list_custom_resources.return_value = []
        mock_kube_client.get_custom_resource.return_value = {
            "status": {"phase": "Unknown", "lastMessage": "Unknown status for Velero restore"}
        }

        validator.run(mock_kube_client)

        results = reporter.results
        assert len(results) == 1
        assert results[0]["passed"] is True
        assert "Unknown" in results[0]["message"]

    def test_passive_sync_not_found(self, reporter, mock_kube_client):
        """Test critical failure when passive sync restore not found."""
        validator = PassiveSyncValidator(reporter)
        mock_kube_client.context = "secondary"
        mock_kube_client.list_custom_resources.return_value = []
        mock_kube_client.get_custom_resource.return_value = None

        validator.run(mock_kube_client)

        results = reporter.results
        assert len(results) == 1
        assert results[0]["passed"] is False
        assert results[0]["critical"] is True
        assert "No passive sync restore found" in results[0]["message"]

    def test_passive_sync_unexpected_phase(self, reporter, mock_kube_client):
        """Test critical failure when passive sync in unexpected state."""
        validator = PassiveSyncValidator(reporter)
        mock_kube_client.list_custom_resources.return_value = []
        mock_kube_client.get_custom_resource.return_value = {
            "status": {"phase": "Failed", "lastMessage": "Sync failed"}
        }

        validator.run(mock_kube_client)

        results = reporter.results
        assert len(results) == 1
        assert results[0]["passed"] is False
        assert "unexpected state" in results[0]["message"]

    def test_passive_sync_surfaces_velero_validation_errors(self, reporter, mock_kube_client):
        """Test that Velero validation errors are surfaced when referenced by ACM restore message."""
        validator = PassiveSyncValidator(reporter)
        mock_kube_client.context = "secondary"
        mock_kube_client.list_custom_resources.return_value = []

        # First get_custom_resource call: ACM restore
        # Second get_custom_resource call: referenced Velero restore
        mock_kube_client.get_custom_resource.side_effect = [
            {
                "metadata": {"name": "restore-acm-passive-sync"},
                "status": {
                    "phase": "Error",
                    "lastMessage": "Velero restore restore-acm-passive-sync-acm-resources-schedule-123 has failed validation",
                },
            },
            {
                "status": {
                    "phase": "FailedValidation",
                    "validationErrors": ["the BSL acm-backup-dpa-1 is unavailable"],
                }
            },
        ]

        validator.run(mock_kube_client)

        results = reporter.results
        assert len(results) == 1
        assert results[0]["passed"] is False
        assert "validationErrors" in results[0]["message"]
        assert "acm-backup-dpa-1" in results[0]["message"]


class TestManagedClusterBackupValidator:
    """Tests for ManagedClusterBackupValidator."""

    def test_no_joined_clusters(self, reporter, mock_kube_client):
        """Test that no joined clusters is handled with info message."""
        validator = ManagedClusterBackupValidator(reporter)
        # Return local-cluster only, which is excluded
        mock_kube_client.list_custom_resources.return_value = [{"metadata": {"name": "local-cluster"}}]

        validator.run(mock_kube_client)

        results = reporter.results
        assert len(results) == 1
        assert results[0]["passed"] is True
        assert results[0]["critical"] is False
        assert "no joined ManagedClusters" in results[0]["message"]

    def test_warns_when_cluster_created_after_backup(self, reporter, mock_kube_client):
        """Test that validator warns about clusters created after the latest backup.

        This ensures clusters imported after the last backup are flagged, as they
        would be lost during switchover.
        """
        validator = ManagedClusterBackupValidator(reporter)

        # Mock joined managed clusters (one created before backup, one after)
        mock_kube_client.list_custom_resources.side_effect = [
            # First call: list managed clusters
            [
                {
                    "metadata": {"name": "cluster-before", "creationTimestamp": "2025-12-01T10:00:00Z"},
                    "status": {"conditions": [{"type": "ManagedClusterJoined", "status": "True"}]},
                },
                {
                    "metadata": {"name": "cluster-after", "creationTimestamp": "2025-12-15T10:00:00Z"},
                    "status": {"conditions": [{"type": "ManagedClusterJoined", "status": "True"}]},
                },
            ],
            # Second call: list backups (with required ACM label)
            [
                {
                    "metadata": {
                        "name": "acm-managed-clusters-schedule-20251210100000",
                        "creationTimestamp": "2025-12-10T10:00:00Z",
                        "labels": {"cluster.open-cluster-management.io/backup-schedule-type": "managedClusters"},
                    },
                    "status": {"phase": "Completed", "completionTimestamp": "2025-12-10T10:05:00Z"},
                },
            ],
        ]

        # Mock get_custom_resource for individual cluster lookups
        def get_cluster(group, version, plural, name):
            if name == "cluster-before":
                return {"metadata": {"name": "cluster-before", "creationTimestamp": "2025-12-01T10:00:00Z"}}
            elif name == "cluster-after":
                return {"metadata": {"name": "cluster-after", "creationTimestamp": "2025-12-15T10:00:00Z"}}
            return None

        mock_kube_client.get_custom_resource.side_effect = get_cluster

        validator.run(mock_kube_client)

        # Should have a critical failure result about cluster-after
        results = reporter.results
        warning_results = [r for r in results if "after backup" in r.get("check", "").lower()]
        assert len(warning_results) == 1
        assert warning_results[0]["passed"] is False
        assert warning_results[0]["critical"] is True  # Critical failure - clusters will be lost
        assert "cluster-after" in warning_results[0]["message"]

    def test_no_warning_when_all_clusters_before_backup(self, reporter, mock_kube_client):
        """Test that no warning is issued when all clusters existed before the backup."""
        validator = ManagedClusterBackupValidator(reporter)

        # Mock joined managed clusters (all created before backup)
        mock_kube_client.list_custom_resources.side_effect = [
            # First call: list managed clusters
            [
                {
                    "metadata": {"name": "cluster-1", "creationTimestamp": "2025-12-01T10:00:00Z"},
                    "status": {"conditions": [{"type": "ManagedClusterJoined", "status": "True"}]},
                },
                {
                    "metadata": {"name": "cluster-2", "creationTimestamp": "2025-12-05T10:00:00Z"},
                    "status": {"conditions": [{"type": "ManagedClusterJoined", "status": "True"}]},
                },
            ],
            # Second call: list backups (with required ACM label)
            [
                {
                    "metadata": {
                        "name": "acm-managed-clusters-schedule-20251210100000",
                        "creationTimestamp": "2025-12-10T10:00:00Z",
                        "labels": {"cluster.open-cluster-management.io/backup-schedule-type": "managedClusters"},
                    },
                    "status": {"phase": "Completed", "completionTimestamp": "2025-12-10T10:05:00Z"},
                },
            ],
        ]

        # Mock get_custom_resource for individual cluster lookups
        def get_cluster(group, version, plural, name):
            if name == "cluster-1":
                return {"metadata": {"name": "cluster-1", "creationTimestamp": "2025-12-01T10:00:00Z"}}
            elif name == "cluster-2":
                return {"metadata": {"name": "cluster-2", "creationTimestamp": "2025-12-05T10:00:00Z"}}
            return None

        mock_kube_client.get_custom_resource.side_effect = get_cluster

        validator.run(mock_kube_client)

        # Should NOT have any warning about clusters after backup
        results = reporter.results
        warning_results = [r for r in results if "after backup" in r.get("check", "").lower()]
        assert len(warning_results) == 0


class TestVersionValidator:
    """Tests for VersionValidator."""

    def test_version_detection_success(self, reporter, mock_kube_client):
        """Test successful ACM version detection."""
        validator = VersionValidator(reporter)
        mock_kube_client.get_custom_resource.return_value = {"status": {"currentVersion": "2.12.0"}}
        mock_kube_client.list_custom_resources.return_value = []

        version = validator._detect_version(mock_kube_client, "primary")

        assert version == "2.12.0"
        results = reporter.results
        assert len(results) == 1
        assert results[0]["passed"] is True
        assert "2.12.0" in results[0]["message"]

    def test_multiclusterhub_not_found(self, reporter, mock_kube_client):
        """Test critical failure when MultiClusterHub not found."""
        validator = VersionValidator(reporter)
        mock_kube_client.get_custom_resource.return_value = None
        mock_kube_client.list_custom_resources.return_value = []

        version = validator._detect_version(mock_kube_client, "primary")

        assert version == "unknown"
        results = reporter.results
        assert len(results) == 1
        assert results[0]["passed"] is False
        assert "MultiClusterHub not found" in results[0]["message"]

    def test_version_match_validation(self, reporter, mock_kube_client):
        """Test version matching between hubs."""
        validator = VersionValidator(reporter)

        validator._validate_match("2.12.0", "2.12.0")

        results = reporter.results
        assert len(results) == 1
        assert results[0]["passed"] is True
        assert "both hubs running 2.12.0" in results[0]["message"]

    def test_version_mismatch_validation(self, reporter, mock_kube_client):
        """Test critical failure when versions don't match."""
        validator = VersionValidator(reporter)

        validator._validate_match("2.12.0", "2.11.0")

        results = reporter.results
        assert len(results) == 1
        assert results[0]["passed"] is False
        assert "version mismatch" in results[0]["message"]

    def test_validate_match_unknown_primary(self, reporter):
        """Test that unknown primary version reports verification failure."""
        validator = VersionValidator(reporter)
        validator._validate_match("unknown", "2.12.0")

        results = reporter.results
        assert len(results) == 1
        assert results[0]["passed"] is False
        assert "cannot verify" in results[0]["message"]

    def test_validate_match_unknown_secondary(self, reporter):
        """Test that unknown secondary version reports verification failure."""
        validator = VersionValidator(reporter)
        validator._validate_match("2.12.0", "unknown")

        results = reporter.results
        assert len(results) == 1
        assert results[0]["passed"] is False
        assert "cannot verify" in results[0]["message"]

    def test_full_run_both_versions_detected_and_matched(self, reporter, mock_kube_client):
        """Test full run() with matching versions on both hubs."""
        validator = VersionValidator(reporter)
        mock_kube_client.get_custom_resource.return_value = {
            "metadata": {"name": "multiclusterhub"},
            "status": {"currentVersion": "2.12.0"},
        }

        primary_ver, secondary_ver = validator.run(mock_kube_client, mock_kube_client)

        assert primary_ver == "2.12.0"
        assert secondary_ver == "2.12.0"
        match_results = [r for r in reporter.results if "matching" in r["check"]]
        assert match_results[0]["passed"] is True

    def test_full_run_with_version_mismatch(self, reporter):
        """Test full run() with mismatched versions."""
        validator = VersionValidator(reporter)
        primary_client = Mock()
        secondary_client = Mock()
        primary_client.get_custom_resource.return_value = {
            "metadata": {"name": "multiclusterhub"},
            "status": {"currentVersion": "2.12.0"},
        }
        secondary_client.get_custom_resource.return_value = {
            "metadata": {"name": "multiclusterhub"},
            "status": {"currentVersion": "2.11.0"},
        }

        primary_ver, secondary_ver = validator.run(primary_client, secondary_client)

        assert primary_ver == "2.12.0"
        assert secondary_ver == "2.11.0"
        match_results = [r for r in reporter.results if "matching" in r["check"]]
        assert match_results[0]["passed"] is False

    def test_detect_version_via_list_fallback(self, reporter, mock_kube_client):
        """Test version detection via list_custom_resources when get returns None."""
        validator = VersionValidator(reporter)
        mock_kube_client.get_custom_resource.return_value = None
        mock_kube_client.list_custom_resources.return_value = [
            {
                "metadata": {"name": "multiclusterhub"},
                "status": {"currentVersion": "2.13.0"},
            }
        ]

        version = validator._detect_version(mock_kube_client, "secondary")

        assert version == "2.13.0"
        assert reporter.results[0]["passed"] is True


class TestHubComponentValidatorExtended:
    """Extended tests for HubComponentValidator."""

    def test_oadp_velero_no_pods(self, reporter, mock_kube_client):
        """Test failure when OADP namespace exists but no Velero pods."""
        validator = HubComponentValidator(reporter)
        mock_kube_client.namespace_exists.return_value = True
        mock_kube_client.get_pods.return_value = []

        validator._check_oadp_operator(mock_kube_client, "primary")

        oadp_results = [r for r in reporter.results if "OADP" in r["check"]]
        assert len(oadp_results) == 1
        assert oadp_results[0]["passed"] is False
        assert "no Velero pods" in oadp_results[0]["message"]

    def test_oadp_exception_handling(self, reporter, mock_kube_client):
        """Test error handling when OADP check raises an exception."""
        validator = HubComponentValidator(reporter)
        mock_kube_client.namespace_exists.side_effect = Exception("API timeout")

        validator._check_oadp_operator(mock_kube_client, "primary")

        oadp_results = [r for r in reporter.results if "OADP" in r["check"]]
        assert oadp_results[0]["passed"] is False
        assert "error checking OADP" in oadp_results[0]["message"]

    def test_dpa_reconciled(self, reporter, mock_kube_client):
        """Test successful DPA check when reconciled."""
        validator = HubComponentValidator(reporter)
        mock_kube_client.list_custom_resources.return_value = [
            {
                "metadata": {"name": "test-dpa"},
                "status": {
                    "conditions": [
                        {"type": "Reconciled", "status": "True"},
                    ]
                },
            }
        ]

        validator._check_dpa(mock_kube_client, "primary")

        dpa_results = [r for r in reporter.results if "DataProtection" in r["check"]]
        assert dpa_results[0]["passed"] is True
        assert "reconciled" in dpa_results[0]["message"]

    def test_dpa_not_reconciled(self, reporter, mock_kube_client):
        """Test failure when DPA exists but is not reconciled."""
        validator = HubComponentValidator(reporter)
        mock_kube_client.list_custom_resources.return_value = [
            {
                "metadata": {"name": "test-dpa"},
                "status": {
                    "conditions": [
                        {"type": "Reconciled", "status": "False"},
                    ]
                },
            }
        ]

        validator._check_dpa(mock_kube_client, "primary")

        dpa_results = [r for r in reporter.results if "DataProtection" in r["check"]]
        assert dpa_results[0]["passed"] is False
        assert "not reconciled" in dpa_results[0]["message"]

    def test_dpa_not_found(self, reporter, mock_kube_client):
        """Test failure when no DPA found."""
        validator = HubComponentValidator(reporter)
        mock_kube_client.list_custom_resources.return_value = []

        validator._check_dpa(mock_kube_client, "primary")

        dpa_results = [r for r in reporter.results if "DataProtection" in r["check"]]
        assert dpa_results[0]["passed"] is False
        assert "no DataProtectionApplication found" in dpa_results[0]["message"]

    def test_full_run(self, reporter, mock_kube_client):
        """Test full run() checking both OADP and DPA."""
        validator = HubComponentValidator(reporter)
        mock_kube_client.namespace_exists.return_value = True
        mock_kube_client.get_pods.return_value = [{"metadata": {"name": "velero-pod-1"}}]
        mock_kube_client.list_custom_resources.return_value = [
            {
                "metadata": {"name": "test-dpa"},
                "status": {"conditions": [{"type": "Reconciled", "status": "True"}]},
            }
        ]

        validator.run(mock_kube_client, "primary")

        assert len(reporter.results) == 2
        assert all(r["passed"] for r in reporter.results)


class TestAutoImportStrategyValidatorExtended:
    """Extended tests for AutoImportStrategyValidator."""

    def test_primary_acm_214_default_strategy(self, reporter, mock_kube_client):
        """Test primary hub on ACM 2.14+ with default strategy."""
        validator = AutoImportStrategyValidator(reporter)
        mock_kube_client.get_configmap.return_value = None

        validator.run(mock_kube_client, mock_kube_client, "2.14.0", "2.11.0")

        primary_results = [r for r in reporter.results if "primary" in r["check"]]
        assert primary_results[0]["passed"] is True
        assert "default" in primary_results[0]["message"]

    def test_primary_acm_214_non_default_strategy(self, reporter, mock_kube_client):
        """Test primary hub on ACM 2.14+ with non-default strategy."""
        validator = AutoImportStrategyValidator(reporter)
        mock_kube_client.get_configmap.return_value = {
            "data": {"autoImportStrategy": "CustomStrategy"}
        }

        validator.run(mock_kube_client, mock_kube_client, "2.14.0", "2.11.0")

        primary_results = [r for r in reporter.results if "primary" in r["check"]]
        assert primary_results[0]["passed"] is False
        assert "non-default" in primary_results[0]["message"]

    def test_primary_acm_214_error_strategy(self, reporter, mock_kube_client):
        """Test primary hub on ACM 2.14+ when configmap read fails."""
        validator = AutoImportStrategyValidator(reporter)
        mock_kube_client.get_configmap.side_effect = Exception("API error")

        validator.run(mock_kube_client, mock_kube_client, "2.14.0", "2.11.0")

        primary_results = [r for r in reporter.results if "primary" in r["check"]]
        assert primary_results[0]["passed"] is False
        assert "error" in primary_results[0]["message"].lower()

    def test_secondary_acm_214_sync_strategy(self, reporter):
        """Test secondary hub on ACM 2.14+ with Sync strategy already set."""
        from lib.constants import AUTO_IMPORT_STRATEGY_KEY, AUTO_IMPORT_STRATEGY_SYNC

        validator = AutoImportStrategyValidator(reporter)
        primary_client = Mock()
        secondary_client = Mock()
        primary_client.get_configmap.return_value = None
        secondary_client.get_configmap.return_value = {
            "data": {AUTO_IMPORT_STRATEGY_KEY: AUTO_IMPORT_STRATEGY_SYNC}
        }
        secondary_client.list_custom_resources.return_value = [
            {"metadata": {"name": "cluster1"}},
        ]

        validator.run(primary_client, secondary_client, "2.14.0", "2.14.0")

        secondary_results = [r for r in reporter.results if "secondary" in r["check"]]
        assert secondary_results[0]["passed"] is True
        assert "Sync" in secondary_results[0]["message"]

    def test_secondary_acm_214_default_with_existing_clusters(self, reporter):
        """Test secondary hub on 2.14+ default strategy with existing managed clusters warns."""
        validator = AutoImportStrategyValidator(reporter)
        primary_client = Mock()
        secondary_client = Mock()
        primary_client.get_configmap.return_value = None
        secondary_client.get_configmap.return_value = None
        secondary_client.list_custom_resources.return_value = [
            {"metadata": {"name": "cluster1"}},
            {"metadata": {"name": "cluster2"}},
        ]

        validator.run(primary_client, secondary_client, "2.14.0", "2.14.0")

        secondary_results = [r for r in reporter.results if "secondary" in r["check"]]
        assert secondary_results[0]["passed"] is False
        assert "existing managed cluster" in secondary_results[0]["message"]

    def test_secondary_acm_214_default_no_clusters(self, reporter):
        """Test secondary hub on 2.14+ default strategy with no clusters is fine."""
        validator = AutoImportStrategyValidator(reporter)
        primary_client = Mock()
        secondary_client = Mock()
        primary_client.get_configmap.return_value = None
        secondary_client.get_configmap.return_value = None
        secondary_client.list_custom_resources.return_value = []

        validator.run(primary_client, secondary_client, "2.14.0", "2.14.0")

        secondary_results = [r for r in reporter.results if "secondary" in r["check"]]
        assert secondary_results[0]["passed"] is True
        assert "default" in secondary_results[0]["message"]

    def test_secondary_acm_214_error_reading_configmap(self, reporter):
        """Test secondary hub on 2.14+ when configmap read fails."""
        validator = AutoImportStrategyValidator(reporter)
        primary_client = Mock()
        secondary_client = Mock()
        primary_client.get_configmap.return_value = None
        secondary_client.get_configmap.side_effect = Exception("connection error")
        secondary_client.list_custom_resources.return_value = []

        validator.run(primary_client, secondary_client, "2.14.0", "2.14.0")

        secondary_results = [r for r in reporter.results if "secondary" in r["check"]]
        assert secondary_results[0]["passed"] is False
        assert "error" in secondary_results[0]["message"].lower()


class TestValidationReporter:
    """Tests for ValidationReporter."""

    def test_add_result(self):
        """Test that add_result works correctly."""
        reporter = ValidationReporter()
        reporter.add_result("test", True, "success message")

        assert len(reporter.results) == 1
        assert reporter.results[0]["check"] == "test"
        assert reporter.results[0]["passed"] is True
        assert reporter.results[0]["message"] == "success message"

    def test_add_critical_result(self):
        """Test that critical flag is preserved."""
        reporter = ValidationReporter()
        reporter.add_result("test", False, "failure message", critical=True)

        assert len(reporter.results) == 1
        assert reporter.results[0]["critical"] is True

    def test_add_non_critical_result(self):
        """Test that non-critical flag is preserved."""
        reporter = ValidationReporter()
        reporter.add_result("test", False, "warning message", critical=False)

        assert len(reporter.results) == 1
        assert reporter.results[0]["critical"] is False
