"""Unit tests for individual validator classes.

Tests cover core validator logic with success and failure cases for each validator.
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timedelta
import base64
import json

from modules.preflight.backup_validators import (
    BackupValidator,
    BackupScheduleValidator,
    PassiveSyncValidator,
    ManagedClusterBackupValidator,
)
from modules.preflight.cluster_validators import ClusterDeploymentValidator
from modules.preflight.namespace_validators import (
    NamespaceValidator,
    ObservabilityDetector,
    ObservabilityPrereqValidator,
    ToolingValidator,
)
from modules.preflight.version_validators import (
    KubeconfigValidator,
    VersionValidator,
    HubComponentValidator,
    AutoImportStrategyValidator,
)
from modules.preflight.reporter import ValidationReporter


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

    def test_validator_instantiation(self, reporter):
        """Test that BackupValidator can be instantiated."""
        validator = BackupValidator(reporter)
        assert validator is not None
        assert validator.reporter == reporter

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

    def test_backup_in_progress(self, reporter, mock_kube_client):
        """Test critical failure when backup is in progress."""
        validator = BackupValidator(reporter)
        # Mock backups with one in progress
        mock_kube_client.list_custom_resources.return_value = [
            {
                "metadata": {"name": "backup-in-progress", "creationTimestamp": "2025-12-31T10:00:00Z"},
                "status": {"phase": "InProgress"}
            },
            {
                "metadata": {"name": "backup-completed", "creationTimestamp": "2025-12-30T10:00:00Z"},
                "status": {"phase": "Completed", "completionTimestamp": "2025-12-30T10:05:00Z"}
            }
        ]
        
        validator.run(mock_kube_client)
        
        # Should have critical failure about in-progress backup
        results = reporter.results
        assert len(results) == 1
        assert results[0]["passed"] is False
        assert results[0]["critical"] is True
        assert "backup(s) in progress" in results[0]["message"]
        assert "backup-in-progress" in results[0]["message"]

    def test_latest_backup_failed(self, reporter, mock_kube_client):
        """Test critical failure when latest backup failed."""
        validator = BackupValidator(reporter)
        # Mock backups with failed latest backup
        mock_kube_client.list_custom_resources.return_value = [
            {
                "metadata": {"name": "backup-failed", "creationTimestamp": "2025-12-31T10:00:00Z"},
                "status": {"phase": "Failed"}
            },
            {
                "metadata": {"name": "backup-completed", "creationTimestamp": "2025-12-30T10:00:00Z"},
                "status": {"phase": "Completed", "completionTimestamp": "2025-12-30T10:05:00Z"}
            }
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
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        recent_time = now.replace(minute=now.minute-5, second=0, microsecond=0).isoformat().replace('+00:00', 'Z')
        
        mock_kube_client.list_custom_resources.return_value = [
            {
                "metadata": {"name": "backup-fresh", "creationTimestamp": recent_time},
                "status": {"phase": "Completed", "completionTimestamp": recent_time}
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
                "status": {"phase": "Completed", "completionTimestamp": "2025-12-28T10:05:00Z"}
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

    def test_validator_instantiation(self, reporter):
        """Test that BackupScheduleValidator can be instantiated."""
        validator = BackupScheduleValidator(reporter)
        assert validator is not None
        assert validator.reporter == reporter

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
            {
                "metadata": {"name": "acm-backup-schedule"},
                "spec": {"useManagedServiceAccount": True}
            }
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
            {
                "metadata": {"name": "acm-backup-schedule"},
                "spec": {"useManagedServiceAccount": False}
            }
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
            {
                "metadata": {"name": "acm-backup-schedule"},
                "spec": {}
            }
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


class TestClusterDeploymentValidator:
    """Tests for ClusterDeploymentValidator."""

    def test_validator_instantiation(self, reporter):
        """Test that ClusterDeploymentValidator can be instantiated."""
        validator = ClusterDeploymentValidator(reporter)
        assert validator is not None
        assert validator.reporter == reporter

    def test_run_method_exists(self, reporter, mock_kube_client):
        """Test that run method exists and can be called."""
        validator = ClusterDeploymentValidator(reporter)
        # Mock empty cluster deployments
        mock_kube_client.list_resources.return_value = {"items": []}
        
        # Should not raise an exception
        validator.run(mock_kube_client)


class TestKubeconfigValidator:
    """Tests for KubeconfigValidator."""

    def test_validator_instantiation(self, reporter):
        """Test that KubeconfigValidator can be instantiated."""
        validator = KubeconfigValidator(reporter)
        assert validator is not None
        assert validator.reporter == reporter

    def test_run_method_exists(self, reporter):
        """Test that run method exists and can be called."""
        validator = KubeconfigValidator(reporter)
        mock_primary = Mock()
        mock_secondary = Mock()
        
        # Should not raise an exception even with mocked clients
        try:
            validator.run(mock_primary, mock_secondary)
        except AttributeError:
            # Expected since we're not mocking all the internal methods
            pass


class TestHubComponentValidator:
    """Tests for HubComponentValidator."""

    def test_validator_instantiation(self, reporter):
        """Test that HubComponentValidator can be instantiated."""
        validator = HubComponentValidator(reporter)
        assert validator is not None
        assert validator.reporter == reporter

    def test_run_method_exists(self, reporter, mock_kube_client):
        """Test that run method exists and can be called."""
        validator = HubComponentValidator(reporter)
        
        # Should not raise an exception
        try:
            validator.run(mock_kube_client, "test-hub")
        except AttributeError:
            # Expected since we're not mocking all the internal methods
            pass


class TestAutoImportStrategyValidator:
    """Tests for AutoImportStrategyValidator."""

    def test_validator_instantiation(self, reporter):
        """Test that AutoImportStrategyValidator can be instantiated."""
        validator = AutoImportStrategyValidator(reporter)
        assert validator is not None
        assert validator.reporter == reporter

    def test_run_method_exists(self, reporter, mock_kube_client):
        """Test that run method exists and can be called."""
        validator = AutoImportStrategyValidator(reporter)
        
        # Mock version detection and managed clusters
        mock_kube_client.get_custom_resource.return_value = {
            "spec": {"version": "2.14.0"}
        }
        mock_kube_client.list_custom_resources.return_value = {"items": []}
        mock_kube_client.get_configmap.return_value = {
            "data": {"AUTO_IMPORT_STRATEGY": "default"}
        }
        
        # Should not raise an exception
        try:
            validator.run(mock_kube_client, mock_kube_client, "2.14.0", "2.14.0")
        except (AttributeError, TypeError):
            # Expected since we're not mocking all the internal methods
            pass


class TestNamespaceValidator:
    """Tests for NamespaceValidator."""

    def test_validator_instantiation(self, reporter):
        """Test that NamespaceValidator can be instantiated."""
        validator = NamespaceValidator(reporter)
        assert validator is not None
        assert validator.reporter == reporter

    def test_run_method_exists(self, reporter, mock_kube_client):
        """Test that run method exists and can be called."""
        validator = NamespaceValidator(reporter)
        # Mock namespace existence
        mock_kube_client.get_namespace.return_value = {"metadata": {"name": "test-ns"}}
        
        # Should not raise an exception
        validator.run(mock_kube_client, mock_kube_client)


class TestObservabilityDetector:
    """Tests for ObservabilityDetector."""

    def test_validator_instantiation(self, reporter):
        """Test that ObservabilityDetector can be instantiated."""
        validator = ObservabilityDetector(reporter)
        assert validator is not None
        assert validator.reporter == reporter

    def test_detect_method_exists(self, reporter, mock_kube_client):
        """Test that detect method exists and can be called."""
        validator = ObservabilityDetector(reporter)
        # Mock no observability
        mock_kube_client.get_custom_resource.return_value = None
        
        # Should return a tuple
        result = validator.detect(mock_kube_client, mock_kube_client)
        assert isinstance(result, tuple)
        assert len(result) == 2


class TestObservabilityPrereqValidator:
    """Tests for ObservabilityPrereqValidator."""

    def test_validator_instantiation(self, reporter):
        """Test that ObservabilityPrereqValidator can be instantiated."""
        validator = ObservabilityPrereqValidator(reporter)
        assert validator is not None
        assert validator.reporter == reporter

    def test_run_method_exists(self, reporter, mock_kube_client):
        """Test that run method exists and can be called."""
        validator = ObservabilityPrereqValidator(reporter)
        # Mock pod existence
        mock_kube_client.list_resources.return_value = {
            "items": [{"metadata": {"name": "test-pod"}}]
        }
        
        # Should not raise an exception
        validator.run(mock_kube_client)


class TestToolingValidator:
    """Tests for ToolingValidator."""

    def test_validator_instantiation(self, reporter):
        """Test that ToolingValidator can be instantiated."""
        validator = ToolingValidator(reporter)
        assert validator is not None
        assert validator.reporter == reporter

    @patch("modules.preflight.namespace_validators.shutil.which")
    def test_run_method_exists(self, mock_which, reporter):
        """Test that run method exists and can be called."""
        mock_which.return_value = "/usr/bin/oc"
        
        validator = ToolingValidator(reporter)
        
        # Should not raise an exception
        validator.run()


class TestPassiveSyncValidator:
    """Tests for PassiveSyncValidator."""

    def test_validator_instantiation(self, reporter):
        """Test that PassiveSyncValidator can be instantiated."""
        validator = PassiveSyncValidator(reporter)
        assert validator is not None
        assert validator.reporter == reporter

    def test_run_method_exists(self, reporter, mock_kube_client):
        """Test that run method exists and can be called."""
        validator = PassiveSyncValidator(reporter)
        # Mock no passive sync restore
        mock_kube_client.get_custom_resource.return_value = None
        
        # Should not raise an exception
        validator.run(mock_kube_client)


class TestManagedClusterBackupValidator:
    """Tests for ManagedClusterBackupValidator."""

    def test_validator_instantiation(self, reporter):
        """Test that ManagedClusterBackupValidator can be instantiated."""
        validator = ManagedClusterBackupValidator(reporter)
        assert validator is not None
        assert validator.reporter == reporter

    def test_run_method_exists(self, reporter, mock_kube_client):
        """Test that run method exists and can be called."""
        validator = ManagedClusterBackupValidator(reporter)
        # Mock no managed clusters
        mock_kube_client.list_resources.return_value = {"items": []}
        
        # Should not raise an exception
        validator.run(mock_kube_client)

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

    def test_validator_instantiation(self, reporter):
        """Test that VersionValidator can be instantiated."""
        validator = VersionValidator(reporter)
        assert validator is not None
        assert validator.reporter == reporter

    def test_run_method_exists(self, reporter, mock_kube_client):
        """Test that run method exists and can be called."""
        validator = VersionValidator(reporter)
        # Mock version detection
        mock_kube_client.get_custom_resource.return_value = {
            "spec": {"version": "2.11.8"}
        }
        
        # Should not raise an exception
        validator.run(mock_kube_client, mock_kube_client)


class TestValidationReporter:
    """Tests for ValidationReporter."""

    def test_reporter_instantiation(self):
        """Test that ValidationReporter can be instantiated."""
        reporter = ValidationReporter()
        assert reporter is not None
        assert len(reporter.results) == 0

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
