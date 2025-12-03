"""Unit tests for lib/kube_client.py.

Modernized pytest tests with fixtures, markers, and parameterization.
Tests cover KubeClient initialization, CRUD operations, and dry-run mode.
"""

from unittest.mock import MagicMock, patch

import pytest
from kubernetes.client.rest import ApiException

from lib.kube_client import KubeClient


@pytest.fixture
def mock_k8s_apis():
    """Mock Kubernetes API clients."""
    with patch("lib.kube_client.config.load_kube_config") as mock_config, patch(
        "lib.kube_client.client.CustomObjectsApi"
    ) as mock_custom_cls, patch(
        "lib.kube_client.client.CoreV1Api"
    ) as mock_core_cls, patch(
        "lib.kube_client.client.AppsV1Api"
    ) as mock_apps_cls:

        yield {
            "config": mock_config,
            "custom_api": mock_custom_cls.return_value,
            "core_api": mock_core_cls.return_value,
            "apps_api": mock_apps_cls.return_value,
        }


@pytest.fixture
def kube_client(mock_k8s_apis):
    """Create a normal KubeClient instance with mocked APIs."""
    return KubeClient(context="test-context", dry_run=False)


@pytest.fixture
def dry_run_client(mock_k8s_apis):
    """Create a dry-run KubeClient instance with mocked APIs."""
    return KubeClient(context="test-context", dry_run=True)


@pytest.mark.unit
class TestKubeClient:
    """Test cases for KubeClient class."""

    def test_get_custom_resource(self, kube_client, mock_k8s_apis):
        """Test getting a custom resource successfully."""
        mock_k8s_apis["custom_api"].get_namespaced_custom_object.return_value = {
            "metadata": {"name": "test"}
        }

        result = kube_client.get_custom_resource(
            "operator.open-cluster-management.io",
            "v1",
            "multiclusterhubs",
            "test-hub",
            namespace="test-ns",
        )

        assert result is not None
        mock_k8s_apis[
            "custom_api"
        ].get_namespaced_custom_object.assert_called_once_with(
            group="operator.open-cluster-management.io",
            version="v1",
            namespace="test-ns",
            plural="multiclusterhubs",
            name="test-hub",
        )

    def test_get_custom_resource_not_found(self, kube_client, mock_k8s_apis):
        """Test getting a non-existent custom resource returns None."""
        mock_k8s_apis["custom_api"].get_namespaced_custom_object.side_effect = (
            ApiException(status=404)
        )

        result = kube_client.get_custom_resource(
            "operator.open-cluster-management.io",
            "v1",
            "multiclusterhubs",
            "test-hub",
            namespace="test-ns",
        )

        assert result is None

    def test_list_custom_resources(self, kube_client, mock_k8s_apis):
        """Test listing custom resources."""
        mock_k8s_apis["custom_api"].list_namespaced_custom_object.return_value = {
            "items": [
                {"metadata": {"name": "cluster1"}},
                {"metadata": {"name": "cluster2"}},
            ]
        }

        result = kube_client.list_custom_resources(
            "cluster.open-cluster-management.io",
            "v1",
            "managedclusters",
            namespace="test-ns",
        )

        assert len(result) == 2
        mock_k8s_apis["custom_api"].list_namespaced_custom_object.assert_called_once()

    def test_patch_custom_resource_dry_run(self, dry_run_client, mock_k8s_apis):
        """Test dry-run mode doesn't make actual API calls."""
        result = dry_run_client.patch_custom_resource(
            "cluster.open-cluster-management.io",
            "v1",
            "managedclusters",
            name="test-cluster",
            patch={"spec": {"paused": True}},
            namespace="test-ns",
        )

        assert result == {}
        mock_k8s_apis["custom_api"].patch_namespaced_custom_object.assert_not_called()

    def test_patch_custom_resource_normal(self, kube_client, mock_k8s_apis):
        """Test patching a custom resource in normal mode."""
        mock_k8s_apis["custom_api"].patch_namespaced_custom_object.return_value = {
            "result": True
        }

        result = kube_client.patch_custom_resource(
            "cluster.open-cluster-management.io",
            "v1",
            "managedclusters",
            name="test-cluster",
            patch={"spec": {"paused": True}},
            namespace="test-ns",
        )

        assert result
        mock_k8s_apis["custom_api"].patch_namespaced_custom_object.assert_called_once()

    def test_create_custom_resource_dry_run(self, dry_run_client, mock_k8s_apis):
        """Test creating a custom resource in dry-run mode."""
        resource_body = {
            "apiVersion": "cluster.open-cluster-management.io/v1beta1",
            "kind": "Restore",
            "metadata": {"name": "test-restore"},
        }

        result = dry_run_client.create_custom_resource(
            "cluster.open-cluster-management.io",
            "v1beta1",
            "restores",
            body=resource_body,
            namespace="test-ns",
        )

        assert result == resource_body
        mock_k8s_apis["custom_api"].create_namespaced_custom_object.assert_not_called()

    def test_delete_custom_resource_dry_run(self, dry_run_client, mock_k8s_apis):
        """Test deleting a custom resource in dry-run mode."""
        result = dry_run_client.delete_custom_resource(
            "cluster.open-cluster-management.io",
            "v1",
            "managedclusters",
            name="test-cluster",
            namespace="test-ns",
        )

        assert result is True
        mock_k8s_apis["custom_api"].delete_namespaced_custom_object.assert_not_called()

    def test_scale_deployment_dry_run(self, dry_run_client, mock_k8s_apis):
        """Test scaling deployment in dry-run mode."""
        result = dry_run_client.scale_deployment(
            namespace="test-ns",
            name="test-deploy",
            replicas=3,
        )

        assert result == {}
        mock_k8s_apis["apps_api"].patch_namespaced_deployment_scale.assert_not_called()

    def test_scale_deployment_normal(self, kube_client, mock_k8s_apis):
        """Test scaling deployment in normal mode."""
        response = MagicMock()
        response.to_dict.return_value = {"status": "scaled"}
        mock_k8s_apis["apps_api"].patch_namespaced_deployment_scale.return_value = (
            response
        )

        result = kube_client.scale_deployment(
            namespace="test-ns",
            name="test-deploy",
            replicas=3,
        )

        assert result == {"status": "scaled"}
        mock_k8s_apis["apps_api"].patch_namespaced_deployment_scale.assert_called_once()

    def test_list_custom_resources_pagination(self, kube_client, mock_k8s_apis):
        """Ensure list_custom_resources follows continue tokens."""
        mock_k8s_apis["custom_api"].list_cluster_custom_object.side_effect = [
            {
                "items": [{"metadata": {"name": "item1"}}],
                "metadata": {"continue": "token"},
            },
            {
                "items": [{"metadata": {"name": "item2"}}],
                "metadata": {},
            },
        ]

        results = kube_client.list_custom_resources(
            "cluster.open-cluster-management.io",
            "v1",
            "managedclusters",
        )

        assert [item["metadata"]["name"] for item in results] == ["item1", "item2"]
        assert mock_k8s_apis["custom_api"].list_cluster_custom_object.call_count == 2

    def test_scale_statefulset(self, kube_client, mock_k8s_apis):
        """Test scaling statefulset."""
        response = MagicMock()
        response.to_dict.return_value = {"status": "scaled"}
        mock_k8s_apis["apps_api"].patch_namespaced_stateful_set_scale.return_value = (
            response
        )

        result = kube_client.scale_statefulset(
            namespace="test-ns",
            name="test-sts",
            replicas=0,
        )

        assert result == {"status": "scaled"}
        mock_k8s_apis[
            "apps_api"
        ].patch_namespaced_stateful_set_scale.assert_called_once()

    def test_namespace_exists(self, kube_client, mock_k8s_apis):
        """Test checking if namespace exists."""
        mock_k8s_apis["core_api"].read_namespace.return_value = MagicMock()

        result = kube_client.namespace_exists("test-ns")

        assert result is True
        mock_k8s_apis["core_api"].read_namespace.assert_called_once_with("test-ns")

    def test_namespace_not_exists(self, kube_client, mock_k8s_apis):
        """Test checking if namespace doesn't exist."""
        mock_k8s_apis["core_api"].read_namespace.side_effect = ApiException(status=404)

        result = kube_client.namespace_exists("test-ns")

        assert result is False

    def test_secret_exists(self, kube_client, mock_k8s_apis):
        """Test checking if secret exists."""
        mock_k8s_apis["core_api"].read_namespaced_secret.return_value = MagicMock()
        assert kube_client.secret_exists("ns", "secret") is True
        mock_k8s_apis["core_api"].read_namespaced_secret.assert_called_once_with(
            name="secret", namespace="ns"
        )

    def test_secret_not_exists(self, kube_client, mock_k8s_apis):
        """Test checking if secret does not exist."""
        mock_k8s_apis["core_api"].read_namespaced_secret.side_effect = ApiException(
            status=404
        )
        assert kube_client.secret_exists("ns", "secret") is False

    def test_get_route_host(self, kube_client, mock_k8s_apis):
        """Test retrieving a route host."""
        mock_k8s_apis["custom_api"].get_namespaced_custom_object.return_value = {
            "spec": {"host": "grafana.example.com"}
        }
        host = kube_client.get_route_host("ns", "grafana")
        assert host == "grafana.example.com"

    def test_get_route_host_not_found(self, kube_client, mock_k8s_apis):
        """Test route host returns None when route missing."""
        mock_k8s_apis["custom_api"].get_namespaced_custom_object.side_effect = (
            ApiException(status=404)
        )
        assert kube_client.get_route_host("ns", "grafana") is None

    def test_get_pods(self, kube_client, mock_k8s_apis):
        """Test getting pods with label selector."""
        pod1 = MagicMock()
        pod1.to_dict.return_value = {"metadata": {"name": "pod1"}}
        pod2 = MagicMock()
        pod2.to_dict.return_value = {"metadata": {"name": "pod2"}}
        mock_k8s_apis["core_api"].list_namespaced_pod.return_value.items = [pod1, pod2]

        result = kube_client.get_pods("test-ns", label_selector="app=test")

        assert len(result) == 2
        mock_k8s_apis["core_api"].list_namespaced_pod.assert_called_once_with(
            namespace="test-ns",
            label_selector="app=test",
        )

    def test_get_pods_with_complex_label_selectors(self, kube_client, mock_k8s_apis):
        """Test getting pods with complex label selectors including slashes and operators."""
        pod1 = MagicMock()
        pod1.to_dict.return_value = {"metadata": {"name": "pod1"}}
        mock_k8s_apis["core_api"].list_namespaced_pod.return_value.items = [pod1]

        # Test various complex label selectors that should pass through to K8s API
        complex_selectors = [
            "app.kubernetes.io/name=velero",
            "app.kubernetes.io/component=server",
            "component!=api",
            "tier notin (dev,test)",
            "environment in (production,staging)",
            "pod-template-hash",
            "!excluded-label",
            "app.kubernetes.io/name=velero,component=server",
            "app.kubernetes.io/managed-by=helm,app.kubernetes.io/instance=myapp",
        ]

        for selector in complex_selectors:
            mock_k8s_apis["core_api"].list_namespaced_pod.reset_mock()
            result = kube_client.get_pods("test-ns", label_selector=selector)

            assert len(result) == 1
            mock_k8s_apis["core_api"].list_namespaced_pod.assert_called_once_with(
                namespace="test-ns",
                label_selector=selector,
            )

    def test_get_pods_with_empty_label_selector_raises(self, kube_client, mock_k8s_apis):
        """Test that empty or whitespace-only label selectors raise ValidationError."""
        from lib.validation import ValidationError

        with pytest.raises(ValidationError):
            kube_client.get_pods("test-ns", label_selector="")

        with pytest.raises(ValidationError):
            kube_client.get_pods("test-ns", label_selector="   ")

    @patch("lib.kube_client.time.sleep")
    def test_wait_for_pods_ready(self, mock_sleep, kube_client, mock_k8s_apis):
        """Test waiting for pods to become ready."""
        pod_not_ready = MagicMock()
        pod_not_ready.to_dict.return_value = {
            "metadata": {"name": "pod1"},
            "status": {"conditions": [{"type": "Ready", "status": "False"}]},
        }
        pod_ready = MagicMock()
        pod_ready.to_dict.return_value = {
            "metadata": {"name": "pod1"},
            "status": {"conditions": [{"type": "Ready", "status": "True"}]},
        }

        mock_k8s_apis["core_api"].list_namespaced_pod.side_effect = [
            MagicMock(items=[pod_not_ready]),
            MagicMock(items=[pod_ready]),
        ]

        result = kube_client.wait_for_pods_ready("test-ns", "app=test", timeout=10)

        assert result is True
        assert mock_k8s_apis["core_api"].list_namespaced_pod.call_count >= 2

    @patch("lib.kube_client.time.sleep")
    def test_wait_for_pods_ready_allows_extra_pods(
        self, mock_sleep, kube_client, mock_k8s_apis
    ):
        """When more pods than expected exist, success should still be reported."""
        pod_ready = MagicMock()
        pod_ready.to_dict.return_value = {
            "metadata": {"name": "pod-ready"},
            "status": {"conditions": [{"type": "Ready", "status": "True"}]},
        }
        pod_extra = MagicMock()
        pod_extra.to_dict.return_value = {
            "metadata": {"name": "pod-extra"},
            "status": {"conditions": [{"type": "Ready", "status": "False"}]},
        }

        mock_k8s_apis["core_api"].list_namespaced_pod.return_value = MagicMock(
            items=[pod_ready, pod_extra]
        )

        result = kube_client.wait_for_pods_ready(
            "test-ns", "app=test", expected_count=1, timeout=5
        )

        assert result is True
        mock_sleep.assert_not_called()

    def test_rollout_restart_deployment_dry_run(self, dry_run_client, mock_k8s_apis):
        """Test rollout restart deployment in dry-run mode."""
        result = dry_run_client.rollout_restart_deployment(
            namespace="test-ns",
            name="test-deploy",
        )

        assert result == {}
        mock_k8s_apis["apps_api"].patch_namespaced_deployment.assert_not_called()

    def test_rollout_restart_deployment_normal(self, kube_client, mock_k8s_apis):
        """Test rollout restart deployment in normal mode."""
        response = MagicMock()
        response.to_dict.return_value = {"status": "restarted"}
        mock_k8s_apis["apps_api"].patch_namespaced_deployment.return_value = response

        result = kube_client.rollout_restart_deployment(
            namespace="test-ns",
            name="test-deploy",
        )

        assert result == {"status": "restarted"}
        mock_k8s_apis["apps_api"].patch_namespaced_deployment.assert_called_once()


@pytest.mark.unit
class TestKubeClientInitialization:
    """Test cases for KubeClient initialization."""

    @patch("lib.kube_client.config.load_kube_config")
    def test_init_with_context(self, mock_load_config):
        """Test initializing with a specific context."""
        KubeClient(context="test-context")
        mock_load_config.assert_called_once_with(context="test-context")

    @patch("lib.kube_client.config.load_kube_config")
    def test_init_without_context(self, mock_load_config):
        """Test initializing without a context."""
        KubeClient()
        mock_load_config.assert_called_once_with(context=None)

    @patch("lib.kube_client.config.load_kube_config")
    def test_init_dry_run_flag(self, mock_load_config):
        """Test dry-run flag initialization."""
        client_normal = KubeClient(dry_run=False)
        client_dry = KubeClient(dry_run=True)

        assert client_normal.dry_run is False
        assert client_dry.dry_run is True
