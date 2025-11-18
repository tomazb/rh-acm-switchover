"""Unit tests for modules/preflight.py."""

import unittest
from unittest.mock import MagicMock, patch
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from modules.preflight import PreflightValidator
from lib.utils import StateManager
import tempfile


class TestPreflightValidator(unittest.TestCase):
    """Test cases for PreflightValidator class."""

    def setUp(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.mkdtemp()
        self.state_file = os.path.join(self.temp_dir, "test-state.json")
        self.state = StateManager(self.state_file)
        
        self.mock_primary = MagicMock()
        self.mock_secondary = MagicMock()
        
        self.validator = PreflightValidator(
            self.mock_primary,
            self.mock_secondary,
            self.state,
            method="passive-sync"
        )

    def tearDown(self):
        """Clean up test fixtures."""
        if os.path.exists(self.state_file):
            os.remove(self.state_file)
        if os.path.exists(self.temp_dir):
            os.rmdir(self.temp_dir)

    def test_check_namespaces_exist_success(self):
        """Test namespace check with all namespaces present."""
        self.mock_primary.namespace_exists.return_value = True
        self.mock_secondary.namespace_exists.return_value = True
        
        result = self.validator._check_namespaces()
        
        self.assertTrue(result)
        self.assertTrue(self.state.is_step_completed("namespaces_checked"))

    def test_check_namespaces_missing_primary(self):
        """Test namespace check with missing primary namespace."""
        self.mock_primary.namespace_exists.side_effect = [True, False]
        self.mock_secondary.namespace_exists.return_value = True
        
        result = self.validator._check_namespaces()
        
        self.assertFalse(result)

    def test_detect_acm_version_success(self):
        """Test ACM version detection."""
        mock_mch = {
            "status": {
                "currentVersion": "2.12.0"
            }
        }
        
        self.mock_primary.get_custom_resource.return_value = mock_mch
        
        result = self.validator._detect_acm_version()
        
        self.assertTrue(result)
        self.assertEqual(self.state.get_config("acm_version"), "2.12.0")
        self.assertTrue(self.state.is_step_completed("acm_version_detected"))

    def test_detect_acm_version_missing(self):
        """Test ACM version detection when MultiClusterHub not found."""
        self.mock_primary.list_custom_resources.return_value = []
        
        result = self.validator._detect_acm_version()
        
        self.assertFalse(result)

    def test_detect_acm_version_mismatch(self):
        """Test ACM version detection with version mismatch."""
        mock_mch_primary = {"status": {"currentVersion": "2.12.0"}}
        mock_mch_secondary = {"status": {"currentVersion": "2.11.0"}}
        
        self.mock_primary.list_custom_resources.return_value = [mock_mch_primary]
        self.mock_secondary.list_custom_resources.return_value = [mock_mch_secondary]
        
        result = self.validator._detect_acm_version()
        
        self.assertFalse(result)

    def test_check_oadp_operator_present(self):
        """Test OADP operator check."""
        self.mock_primary.namespace_exists.return_value = True
        self.mock_secondary.namespace_exists.return_value = True
        
        mock_pod = MagicMock()
        mock_pod.status.phase = "Running"
        self.mock_primary.list_pods.return_value = [mock_pod]
        self.mock_secondary.list_pods.return_value = [mock_pod]
        
        result = self.validator._check_oadp_operator()
        
        self.assertTrue(result)
        self.assertTrue(self.state.is_step_completed("oadp_operator_checked"))

    def test_check_oadp_operator_missing(self):
        """Test OADP operator check when namespace missing."""
        self.mock_primary.namespace_exists.return_value = False
        
        result = self.validator._check_oadp_operator()
        
        self.assertFalse(result)

    def test_check_dpa_success(self):
        """Test DataProtectionApplication check."""
        mock_dpa = {
            "status": {
                "conditions": [
                    {"type": "Reconciled", "status": "True"}
                ]
            }
        }
        
        self.mock_primary.list_custom_resources.return_value = [mock_dpa]
        self.mock_secondary.list_custom_resources.return_value = [mock_dpa]
        
        result = self.validator._check_dpa()
        
        self.assertTrue(result)

    def test_check_backup_status_finished(self):
        """Test backup status check with finished backup."""
        mock_schedule = {
            "status": {
                "phase": "Enabled",
                "lastBackup": "backup-20251118"
            }
        }
        
        mock_backup = {
            "status": {
                "phase": "Finished"
            }
        }
        
        self.mock_primary.list_custom_resources.side_effect = [
            [mock_schedule],  # BackupSchedule
            [mock_backup]     # Backup
        ]
        
        result = self.validator._check_backup_status()
        
        self.assertTrue(result)

    def test_check_backup_status_in_progress(self):
        """Test backup status check with in-progress backup."""
        mock_schedule = {
            "status": {
                "phase": "Enabled",
                "lastBackup": "backup-20251118"
            }
        }
        
        mock_backup = {
            "status": {
                "phase": "InProgress"
            }
        }
        
        self.mock_primary.list_custom_resources.side_effect = [
            [mock_schedule],
            [mock_backup]
        ]
        
        result = self.validator._check_backup_status()
        
        self.assertFalse(result)

    def test_check_cluster_deployments_preserve_success(self):
        """Test ClusterDeployment preserveOnDelete check - all set."""
        mock_cd1 = {
            "metadata": {"name": "cluster1"},
            "spec": {"preserveOnDelete": True}
        }
        mock_cd2 = {
            "metadata": {"name": "cluster2"},
            "spec": {"preserveOnDelete": True}
        }
        
        self.mock_primary.list_custom_resources.return_value = [mock_cd1, mock_cd2]
        
        result = self.validator._check_cluster_deployments_preserve()
        
        self.assertTrue(result)

    def test_check_cluster_deployments_preserve_missing(self):
        """Test ClusterDeployment preserveOnDelete check - some missing."""
        mock_cd1 = {
            "metadata": {"name": "cluster1"},
            "spec": {"preserveOnDelete": True}
        }
        mock_cd2 = {
            "metadata": {"name": "cluster2"},
            "spec": {"preserveOnDelete": False}
        }
        
        self.mock_primary.list_custom_resources.return_value = [mock_cd1, mock_cd2]
        
        result = self.validator._check_cluster_deployments_preserve()
        
        self.assertFalse(result)

    def test_check_passive_sync_restore_enabled(self):
        """Test passive sync restore check - enabled."""
        mock_restore = {
            "status": {
                "phase": "Enabled",
                "lastSyncTimestamp": "2025-11-18T10:00:00Z"
            }
        }
        
        self.mock_secondary.get_custom_resource.return_value = mock_restore
        
        result = self.validator._check_passive_sync_restore()
        
        self.assertTrue(result)

    def test_check_passive_sync_restore_missing(self):
        """Test passive sync restore check - not found."""
        self.mock_secondary.get_custom_resource.return_value = None
        
        result = self.validator._check_passive_sync_restore()
        
        self.assertFalse(result)

    def test_detect_observability_present(self):
        """Test Observability detection when present."""
        self.mock_primary.namespace_exists.return_value = True
        
        result = self.validator._detect_observability()
        
        self.assertTrue(result)
        self.assertEqual(self.state.get_config("observability_enabled"), True)

    def test_detect_observability_absent(self):
        """Test Observability detection when absent."""
        self.mock_primary.namespace_exists.return_value = False
        
        result = self.validator._detect_observability()
        
        self.assertTrue(result)  # Still succeeds, just marks as disabled
        self.assertEqual(self.state.get_config("observability_enabled"), False)

    def test_validate_all_success(self):
        """Test full validation flow - all checks pass."""
        # Mock all namespace checks
        self.mock_primary.namespace_exists.return_value = True
        self.mock_secondary.namespace_exists.return_value = True
        
        # Mock ACM version
        mock_mch = {"status": {"currentVersion": "2.12.0"}}
        self.mock_primary.list_custom_resources.return_value = [mock_mch]
        self.mock_secondary.list_custom_resources.return_value = [mock_mch]
        
        # Mock OADP
        mock_pod = MagicMock()
        mock_pod.status.phase = "Running"
        self.mock_primary.list_pods.return_value = [mock_pod]
        self.mock_secondary.list_pods.return_value = [mock_pod]
        
        # Mock DPA
        mock_dpa = {
            "status": {
                "conditions": [{"type": "Reconciled", "status": "True"}]
            }
        }
        
        # Mock backup
        mock_schedule = {
            "status": {"phase": "Enabled", "lastBackup": "backup-20251118"}
        }
        mock_backup = {"status": {"phase": "Finished"}}
        
        # Mock ClusterDeployments
        mock_cd = {
            "metadata": {"name": "cluster1"},
            "spec": {"preserveOnDelete": True}
        }
        
        # Mock passive sync
        mock_restore = {
            "status": {"phase": "Enabled", "lastSyncTimestamp": "2025-11-18T10:00:00Z"}
        }
        
        self.mock_primary.get_custom_resource.return_value = mock_mch
        self.mock_secondary.get_custom_resource.return_value = mock_restore
        
        # Set up list_custom_resources to return appropriate values
        def list_side_effect(group, version, namespace, plural):
            if plural == "multiclusterhubs":
                return [mock_mch]
            elif plural == "dataprotectionapplications":
                return [mock_dpa]
            elif plural == "backupschedules":
                return [mock_schedule]
            elif plural == "backups":
                return [mock_backup]
            elif plural == "clusterdeployments":
                return [mock_cd]
            return []
        
        self.mock_primary.list_custom_resources.side_effect = list_side_effect
        self.mock_secondary.list_custom_resources.side_effect = list_side_effect
        
        result = self.validator.validate_all()
        
        self.assertTrue(result)


if __name__ == '__main__':
    unittest.main()
