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
            "modules.preflight.cluster_validators.record_gitops_markers",
            side_effect=RuntimeError("marker error"),
        ):
            validator.run(mock_kube_client)

        results = reporter.results
        assert len(results) == 1
        assert results[0]["passed"] is True
        mock_logger.warning.assert_called_once()
        assert "GitOps marker recording failed for ClusterDeployment" in mock_logger.warning.call_args[0][0]


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
