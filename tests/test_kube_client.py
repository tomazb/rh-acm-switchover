"""Unit tests for lib/kube_client.py."""

import unittest
from unittest.mock import MagicMock, patch, call
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from lib.kube_client import KubeClient


class TestKubeClient(unittest.TestCase):
    """Test cases for KubeClient class."""

    def setUp(self):
        """Set up test fixtures."""
        with patch('lib.kube_client.config'):
            self.client = KubeClient(context="test-context", dry_run=False)
            self.dry_run_client = KubeClient(context="test-context", dry_run=True)

    @patch('lib.kube_client.client.CustomObjectsApi')
    def test_get_custom_resource(self, mock_api):
        """Test getting a custom resource."""
        mock_instance = mock_api.return_value
        mock_instance.get_namespaced_custom_object.return_value = {
            "metadata": {"name": "test"}
        }
        
        result = self.client.get_custom_resource(
            "operator.open-cluster-management.io",
            "v1",
            "test-ns",
            "multiclusterhubs",
            "test-hub"
        )
        
        self.assertIsNotNone(result)
        mock_instance.get_namespaced_custom_object.assert_called_once()

    @patch('lib.kube_client.client.CustomObjectsApi')
    def test_get_custom_resource_not_found(self, mock_api):
        """Test getting a non-existent custom resource."""
        from kubernetes.client.rest import ApiException
        
        mock_instance = mock_api.return_value
        mock_instance.get_namespaced_custom_object.side_effect = ApiException(status=404)
        
        result = self.client.get_custom_resource(
            "operator.open-cluster-management.io",
            "v1",
            "test-ns",
            "multiclusterhubs",
            "test-hub"
        )
        
        self.assertIsNone(result)

    @patch('lib.kube_client.client.CustomObjectsApi')
    def test_list_custom_resources(self, mock_api):
        """Test listing custom resources."""
        mock_instance = mock_api.return_value
        mock_instance.list_namespaced_custom_object.return_value = {
            "items": [{"metadata": {"name": "cluster1"}}, {"metadata": {"name": "cluster2"}}]
        }
        
        result = self.client.list_custom_resources(
            "cluster.open-cluster-management.io",
            "v1",
            "test-ns",
            "managedclusters"
        )
        
        self.assertEqual(len(result), 2)
        mock_instance.list_namespaced_custom_object.assert_called_once()

    @patch('lib.kube_client.client.CustomObjectsApi')
    def test_patch_custom_resource_dry_run(self, mock_api):
        """Test patching a custom resource in dry-run mode."""
        result = self.dry_run_client.patch_custom_resource(
            "cluster.open-cluster-management.io",
            "v1",
            "test-ns",
            "managedclusters",
            "test-cluster",
            {"spec": {"paused": True}}
        )
        
        self.assertTrue(result)
        # Should not call API in dry-run mode
        mock_api.return_value.patch_namespaced_custom_object.assert_not_called()

    @patch('lib.kube_client.client.CustomObjectsApi')
    def test_patch_custom_resource_normal(self, mock_api):
        """Test patching a custom resource in normal mode."""
        mock_instance = mock_api.return_value
        
        result = self.client.patch_custom_resource(
            "cluster.open-cluster-management.io",
            "v1",
            "test-ns",
            "managedclusters",
            "test-cluster",
            {"spec": {"paused": True}}
        )
        
        self.assertTrue(result)
        mock_instance.patch_namespaced_custom_object.assert_called_once()

    @patch('lib.kube_client.client.CustomObjectsApi')
    def test_create_custom_resource_dry_run(self, mock_api):
        """Test creating a custom resource in dry-run mode."""
        resource_body = {
            "apiVersion": "cluster.open-cluster-management.io/v1beta1",
            "kind": "Restore",
            "metadata": {"name": "test-restore"}
        }
        
        result = self.dry_run_client.create_custom_resource(
            "cluster.open-cluster-management.io",
            "v1beta1",
            "test-ns",
            "restores",
            resource_body
        )
        
        self.assertTrue(result)
        mock_api.return_value.create_namespaced_custom_object.assert_not_called()

    @patch('lib.kube_client.client.CustomObjectsApi')
    def test_delete_custom_resource_dry_run(self, mock_api):
        """Test deleting a custom resource in dry-run mode."""
        result = self.dry_run_client.delete_custom_resource(
            "cluster.open-cluster-management.io",
            "v1",
            "test-ns",
            "managedclusters",
            "test-cluster"
        )
        
        self.assertTrue(result)
        mock_api.return_value.delete_namespaced_custom_object.assert_not_called()

    @patch('lib.kube_client.client.AppsV1Api')
    def test_scale_deployment_dry_run(self, mock_api):
        """Test scaling deployment in dry-run mode."""
        result = self.dry_run_client.scale_deployment("test-ns", "test-deploy", 3)
        
        self.assertTrue(result)
        mock_api.return_value.patch_namespaced_deployment.assert_not_called()

    @patch('lib.kube_client.client.AppsV1Api')
    def test_scale_deployment_normal(self, mock_api):
        """Test scaling deployment in normal mode."""
        mock_instance = mock_api.return_value
        
        result = self.client.scale_deployment("test-ns", "test-deploy", 3)
        
        self.assertTrue(result)
        mock_instance.patch_namespaced_deployment.assert_called_once()

    @patch('lib.kube_client.client.AppsV1Api')
    def test_scale_statefulset(self, mock_api):
        """Test scaling statefulset."""
        mock_instance = mock_api.return_value
        
        result = self.client.scale_statefulset("test-ns", "test-sts", 0)
        
        self.assertTrue(result)
        mock_instance.patch_namespaced_stateful_set.assert_called_once()

    @patch('lib.kube_client.client.CoreV1Api')
    def test_namespace_exists(self, mock_api):
        """Test checking if namespace exists."""
        mock_instance = mock_api.return_value
        mock_instance.read_namespace.return_value = MagicMock()
        
        result = self.client.namespace_exists("test-ns")
        
        self.assertTrue(result)
        mock_instance.read_namespace.assert_called_once_with("test-ns")

    @patch('lib.kube_client.client.CoreV1Api')
    def test_namespace_not_exists(self, mock_api):
        """Test checking non-existent namespace."""
        from kubernetes.client.rest import ApiException
        
        mock_instance = mock_api.return_value
        mock_instance.read_namespace.side_effect = ApiException(status=404)
        
        result = self.client.namespace_exists("test-ns")
        
        self.assertFalse(result)

    @patch('lib.kube_client.client.CoreV1Api')
    def test_list_pods(self, mock_api):
        """Test listing pods."""
        mock_instance = mock_api.return_value
        mock_pod1 = MagicMock()
        mock_pod1.metadata.name = "pod1"
        mock_pod2 = MagicMock()
        mock_pod2.metadata.name = "pod2"
        
        mock_instance.list_namespaced_pod.return_value.items = [mock_pod1, mock_pod2]
        
        result = self.client.list_pods("test-ns", label_selector="app=test")
        
        self.assertEqual(len(result), 2)
        mock_instance.list_namespaced_pod.assert_called_once()

    @patch('lib.kube_client.client.CoreV1Api')
    @patch('lib.kube_client.time.sleep')
    def test_wait_for_pods_ready(self, mock_sleep, mock_api):
        """Test waiting for pods to be ready."""
        mock_instance = mock_api.return_value
        
        # First call: not ready
        mock_pod1 = MagicMock()
        mock_pod1.metadata.name = "pod1"
        mock_pod1.status.conditions = [
            MagicMock(type="Ready", status="False")
        ]
        
        # Second call: ready
        mock_pod2 = MagicMock()
        mock_pod2.metadata.name = "pod1"
        mock_pod2.status.conditions = [
            MagicMock(type="Ready", status="True")
        ]
        
        mock_instance.list_namespaced_pod.return_value.items = [mock_pod1]
        
        # Mock to become ready after first check
        def side_effect(*args, **kwargs):
            if mock_instance.list_namespaced_pod.call_count == 1:
                return MagicMock(items=[mock_pod1])
            return MagicMock(items=[mock_pod2])
        
        mock_instance.list_namespaced_pod.side_effect = side_effect
        
        result = self.client.wait_for_pods_ready("test-ns", "app=test", timeout=60)
        
        self.assertTrue(result)

    @patch('lib.kube_client.client.AppsV1Api')
    def test_rollout_restart_deployment_dry_run(self, mock_api):
        """Test rollout restart in dry-run mode."""
        result = self.dry_run_client.rollout_restart_deployment("test-ns", "test-deploy")
        
        self.assertTrue(result)
        mock_api.return_value.patch_namespaced_deployment.assert_not_called()

    @patch('lib.kube_client.client.AppsV1Api')
    def test_rollout_restart_deployment_normal(self, mock_api):
        """Test rollout restart in normal mode."""
        mock_instance = mock_api.return_value
        
        result = self.client.rollout_restart_deployment("test-ns", "test-deploy")
        
        self.assertTrue(result)
        mock_instance.patch_namespaced_deployment.assert_called_once()


class TestKubeClientInitialization(unittest.TestCase):
    """Test cases for KubeClient initialization."""

    @patch('lib.kube_client.config.load_kube_config')
    def test_init_with_context(self, mock_load_config):
        """Test initialization with context."""
        client = KubeClient(context="test-context")
        
        mock_load_config.assert_called_once_with(context="test-context")

    @patch('lib.kube_client.config.load_kube_config')
    def test_init_without_context(self, mock_load_config):
        """Test initialization without context."""
        client = KubeClient()
        
        mock_load_config.assert_called_once_with(context=None)

    @patch('lib.kube_client.config.load_kube_config')
    def test_init_dry_run_flag(self, mock_load_config):
        """Test dry-run flag initialization."""
        client_normal = KubeClient(dry_run=False)
        client_dry = KubeClient(dry_run=True)
        
        self.assertFalse(client_normal.dry_run)
        self.assertTrue(client_dry.dry_run)


if __name__ == '__main__':
    unittest.main()
