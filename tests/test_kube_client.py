"""Unit tests for lib/kube_client.py.

Modernized pytest tests with fixtures, markers, and parameterization.
Tests cover KubeClient initialization, CRUD operations, and dry-run mode.
"""

import errno
import inspect
from itertools import chain, repeat
from unittest.mock import MagicMock, Mock, patch

import pytest
from kubernetes.client.rest import ApiException
from kubernetes.config.config_exception import ConfigException
from tenacity import wait_none

from lib.constants import (
    STRICT_READ_MAX_PAGES,
    STRICT_READ_MAX_RESTARTS,
    STRICT_READ_PAGE_LIMIT,
    STRICT_READ_REASON_DISCOVERY_UNVERIFIABLE,
    STRICT_READ_REASON_INVENTORY_INCOMPLETE,
    STRICT_READ_REASON_KIND_NOT_SERVED,
    STRICT_READ_REASON_MALFORMED_RESPONSE,
    STRICT_READ_REASON_NAMESPACE_NOT_FOUND,
    STRICT_READ_REASON_OBJECT_NOT_FOUND,
    STRICT_READ_REASON_READ_FAILED,
)
from lib.kube_client import KubeClient, api_call, is_retryable_error
from lib.strict_read import StrictReadOutcome, StrictReadStatus


@pytest.fixture
def mock_k8s_apis():
    """Mock Kubernetes API clients."""
    with patch("lib.kube_client.config.new_client_from_config") as mock_new_client, patch(
        "lib.kube_client.config.load_kube_config"
    ) as mock_load_config, patch("lib.kube_client.client.CustomObjectsApi") as mock_custom_cls, patch(
        "lib.kube_client.client.CoreV1Api"
    ) as mock_core_cls, patch(
        "lib.kube_client.client.AppsV1Api"
    ) as mock_apps_cls:
        api_client = MagicMock(name="api_client")
        api_client.configuration = MagicMock(name="api_client_configuration")
        mock_new_client.return_value = api_client

        yield {
            "new_client": mock_new_client,
            "load_config": mock_load_config,
            "api_client": api_client,
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
class TestConfigMapAdvisoryReads:
    """ConfigMap advisory reads preserve absence, failure, and retry outcomes."""

    def test_configmap_advisory_returns_present_resource(self, kube_client, mock_k8s_apis):
        configmap = MagicMock()
        configmap.to_dict.return_value = {
            "metadata": {"name": "import-controller-config", "namespace": "multicluster-engine"},
            "data": {"autoImportStrategy": "ImportOnly"},
        }
        mock_k8s_apis["core_api"].read_namespaced_config_map.return_value = configmap

        result = kube_client.get_configmap_advisory("multicluster-engine", "import-controller-config")

        assert result == configmap.to_dict.return_value
        mock_k8s_apis["core_api"].read_namespaced_config_map.assert_called_once_with(
            name="import-controller-config",
            namespace="multicluster-engine",
            _request_timeout=30,
        )

    def test_configmap_advisory_returns_none_for_true_404(self, kube_client, mock_k8s_apis):
        mock_k8s_apis["core_api"].read_namespaced_config_map.side_effect = ApiException(status=404)

        result = kube_client.get_configmap_advisory("multicluster-engine", "import-controller-config")

        assert result is None
        assert mock_k8s_apis["core_api"].read_namespaced_config_map.call_count == 1

    def test_configmap_advisory_propagates_403_without_logging_detail(self, kube_client, mock_k8s_apis, caplog):
        failure = ApiException(status=403, reason="R302-CONFIGMAP-ADVISORY-SENTINEL")
        mock_k8s_apis["core_api"].read_namespaced_config_map.side_effect = failure

        with caplog.at_level("DEBUG", logger="acm_switchover"):
            with pytest.raises(ApiException) as exc_info:
                kube_client.get_configmap_advisory("multicluster-engine", "import-controller-config")

        assert exc_info.value is failure
        assert mock_k8s_apis["core_api"].read_namespaced_config_map.call_count == 1
        assert "R302-CONFIGMAP-ADVISORY-SENTINEL" not in caplog.text

    def test_configmap_advisory_retries_retryable_failure(self, kube_client, mock_k8s_apis):
        configmap = MagicMock()
        configmap.to_dict.return_value = {"metadata": {"name": "import-controller-config"}}
        mock_k8s_apis["core_api"].read_namespaced_config_map.side_effect = [
            ApiException(status=503),
            configmap,
        ]

        with patch.object(KubeClient.get_configmap_advisory.retry, "wait", return_value=0):
            result = kube_client.get_configmap_advisory("multicluster-engine", "import-controller-config")

        assert result == configmap.to_dict.return_value
        assert mock_k8s_apis["core_api"].read_namespaced_config_map.call_count == 2

    def test_configmap_advisory_bounds_retry_and_never_logs_exception_detail(self, kube_client, mock_k8s_apis, caplog):
        failure = ApiException(status=503, reason="R302-CONFIGMAP-RETRY-SENTINEL")
        mock_k8s_apis["core_api"].read_namespaced_config_map.side_effect = failure

        with patch.object(KubeClient.get_configmap_advisory.retry, "wait", return_value=0):
            with caplog.at_level("DEBUG", logger="acm_switchover"):
                with pytest.raises(ApiException) as exc_info:
                    kube_client.get_configmap_advisory("multicluster-engine", "import-controller-config")

        assert exc_info.value is failure
        assert mock_k8s_apis["core_api"].read_namespaced_config_map.call_count == 5
        assert "R302-CONFIGMAP-RETRY-SENTINEL" not in caplog.text

    @pytest.mark.parametrize(
        ("namespace", "name"),
        [
            ("", "import-controller-config"),
            ("multicluster-engine", ""),
        ],
    )
    def test_configmap_advisory_preserves_input_validation(self, kube_client, namespace, name):
        from lib.validation import ValidationError

        with pytest.raises(ValidationError):
            kube_client.get_configmap_advisory(namespace, name)


@pytest.mark.unit
class TestKubeClient:
    """Test cases for KubeClient class."""

    def test_get_custom_resource(self, kube_client, mock_k8s_apis):
        """Test getting a custom resource successfully."""
        mock_k8s_apis["custom_api"].get_namespaced_custom_object.return_value = {"metadata": {"name": "test"}}

        result = kube_client.get_custom_resource(
            "operator.open-cluster-management.io",
            "v1",
            "multiclusterhubs",
            "test-hub",
            namespace="test-ns",
        )

        assert result == {"metadata": {"name": "test"}}
        assert result["metadata"]["name"] == "test"
        mock_k8s_apis["custom_api"].get_namespaced_custom_object.assert_called_once_with(
            group="operator.open-cluster-management.io",
            version="v1",
            namespace="test-ns",
            plural="multiclusterhubs",
            name="test-hub",
            _request_timeout=30,
        )

    def test_get_custom_resource_not_found(self, kube_client, mock_k8s_apis):
        """Test getting a non-existent custom resource returns None."""
        mock_k8s_apis["custom_api"].get_namespaced_custom_object.side_effect = ApiException(status=404)

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
        assert result[0]["metadata"]["name"] == "cluster1"
        assert result[1]["metadata"]["name"] == "cluster2"
        mock_k8s_apis["custom_api"].list_namespaced_custom_object.assert_called_once()

    def test_advisory_list_retries_without_logging_exception_detail(self, kube_client, caplog):
        """Advisory retries must preserve resilience without stringifying external failures."""
        private_reason = "Authorization: Bearer advisory-retry-token"
        with patch.object(
            kube_client,
            "_list_custom_resources_raw",
            side_effect=[ApiException(status=500, reason=private_reason), []],
        ) as raw_list, patch.object(
            KubeClient.list_custom_resources_advisory.retry,
            "wait",
            return_value=0,
        ):
            with caplog.at_level("DEBUG", logger="acm_switchover"):
                result = kube_client.list_custom_resources_advisory(
                    "argoproj.io",
                    "v1alpha1",
                    "applications",
                )

        assert result == []
        assert raw_list.call_count == 2
        assert "advisory-retry-token" not in caplog.text

    def test_advisory_get_retries_without_logging_exception_detail(self, kube_client, caplog):
        """Advisory CRD reads must use the same silent retry contract."""
        private_reason = "context=advisory-admin user=system:advisory"
        with patch.object(
            kube_client,
            "_get_custom_resource_raw",
            side_effect=[ApiException(status=503, reason=private_reason), {"metadata": {"name": "crd"}}],
        ) as raw_get, patch.object(
            KubeClient.get_custom_resource_advisory.retry,
            "wait",
            return_value=0,
        ):
            with caplog.at_level("DEBUG", logger="acm_switchover"):
                result = kube_client.get_custom_resource_advisory(
                    "apiextensions.k8s.io",
                    "v1",
                    "customresourcedefinitions",
                    "applications.argoproj.io",
                )

        assert result == {"metadata": {"name": "crd"}}
        assert raw_get.call_count == 2
        assert "advisory-admin" not in caplog.text
        assert "system:advisory" not in caplog.text

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
        mock_k8s_apis["custom_api"].patch_namespaced_custom_object.return_value = {"result": True}

        result = kube_client.patch_custom_resource(
            "cluster.open-cluster-management.io",
            "v1",
            "managedclusters",
            name="test-cluster",
            patch={"spec": {"paused": True}},
            namespace="test-ns",
        )

        assert result == {"result": True}
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
        mock_k8s_apis["apps_api"].patch_namespaced_deployment_scale.return_value = response

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
        mock_k8s_apis["apps_api"].patch_namespaced_stateful_set_scale.return_value = response

        result = kube_client.scale_statefulset(
            namespace="test-ns",
            name="test-sts",
            replicas=0,
        )

        assert result == {"status": "scaled"}
        mock_k8s_apis["apps_api"].patch_namespaced_stateful_set_scale.assert_called_once()

    def test_namespace_exists(self, kube_client, mock_k8s_apis):
        """Test checking if namespace exists returns True for existing namespace."""
        mock_k8s_apis["core_api"].read_namespace.return_value = MagicMock()

        assert kube_client.namespace_exists("test-ns") is True
        assert kube_client.namespace_exists("test-ns") is not None
        mock_k8s_apis["core_api"].read_namespace.assert_called_with("test-ns", _request_timeout=30)

    def test_namespace_not_exists(self, kube_client, mock_k8s_apis):
        """Test checking if namespace doesn't exist returns False (not raises)."""
        mock_k8s_apis["core_api"].read_namespace.side_effect = ApiException(status=404)

        result = kube_client.namespace_exists("test-ns")

        assert result is False
        assert result is not None

    def test_get_cluster_identity_reads_kube_system_uid(self, kube_client, mock_k8s_apis):
        """Cluster identity must come from live kube-system UID, not context name alone."""
        namespace = MagicMock()
        namespace.to_dict.return_value = {"metadata": {"uid": "cluster-uid-123"}}
        mock_k8s_apis["core_api"].read_namespace.return_value = namespace

        result = kube_client.get_cluster_identity()

        assert result == {"context": "test-context", "cluster_uid": "cluster-uid-123"}
        mock_k8s_apis["core_api"].read_namespace.assert_called_with("kube-system", _request_timeout=30)

    def test_cluster_identity_non_retryable_failure_does_not_log_sentinels(
        self, kube_client, mock_k8s_apis, monkeypatch, caplog
    ):
        """A refusal must not emit an API response carrying identity-sensitive values."""
        failure = ApiException(status=403, reason="ssa01-secret-raw-exception-EX74")
        failure.body = (
            "ssa01-secret-api-body-BD73 /api/v1/namespaces/kube-system ssa01-secret-token-TK72 "
            "ssa01-secret-uid-UID75 ssa01-secret-credential-CR77"
        )
        mock_k8s_apis["core_api"].read_namespace.side_effect = failure

        monkeypatch.setattr(KubeClient._read_cluster_identity_namespace.retry, "wait", wait_none())
        with caplog.at_level("DEBUG", logger="acm_switchover"):
            with pytest.raises(ApiException):
                kube_client.get_cluster_identity()

        assert mock_k8s_apis["core_api"].read_namespace.call_count == 1
        for sentinel in (
            "ssa01-secret-raw-exception-EX74",
            "ssa01-secret-api-body-BD73",
            "/api/v1/namespaces/kube-system",
            "ssa01-secret-token-TK72",
            "ssa01-secret-uid-UID75",
            "ssa01-secret-credential-CR77",
        ):
            assert sentinel not in caplog.text

    def test_cluster_identity_retry_failure_does_not_log_sentinels(
        self, kube_client, mock_k8s_apis, monkeypatch, caplog
    ):
        """Retryable identity reads retain bounded retries without raw diagnostics."""
        failure = ApiException(status=503, reason="ssa01-secret-raw-exception-EX74")
        failure.body = (
            "ssa01-secret-api-body-BD73 /api/v1/namespaces/kube-system ssa01-secret-token-TK72 "
            "ssa01-secret-uid-UID75 ssa01-secret-credential-CR77"
        )
        mock_k8s_apis["core_api"].read_namespace.side_effect = failure

        monkeypatch.setattr(KubeClient._read_cluster_identity_namespace.retry, "wait", wait_none())
        with caplog.at_level("DEBUG", logger="acm_switchover"):
            with pytest.raises(ApiException):
                kube_client.get_cluster_identity()

        assert mock_k8s_apis["core_api"].read_namespace.call_count == 5
        for sentinel in (
            "ssa01-secret-raw-exception-EX74",
            "ssa01-secret-api-body-BD73",
            "/api/v1/namespaces/kube-system",
            "ssa01-secret-token-TK72",
            "ssa01-secret-uid-UID75",
            "ssa01-secret-credential-CR77",
        ):
            assert sentinel not in caplog.text

    def test_get_secret(self, kube_client, mock_k8s_apis):
        """Test getting a secret successfully."""
        mock_secret = MagicMock()
        mock_secret.to_dict.return_value = {
            "metadata": {"name": "test-secret", "namespace": "test-ns"},
            "data": {"key": "dmFsdWU="},
        }
        mock_k8s_apis["core_api"].read_namespaced_secret.return_value = mock_secret

        result = kube_client.get_secret("test-ns", "test-secret")

        assert result is not None
        assert result["metadata"]["name"] == "test-secret"
        assert result["data"]["key"] == "dmFsdWU="
        mock_k8s_apis["core_api"].read_namespaced_secret.assert_called_once_with(
            name="test-secret", namespace="test-ns", _request_timeout=30
        )

    def test_get_secret_not_found(self, kube_client, mock_k8s_apis):
        """Test getting a non-existent secret returns None."""
        mock_k8s_apis["core_api"].read_namespaced_secret.side_effect = ApiException(status=404)

        result = kube_client.get_secret("test-ns", "nonexistent")

        assert result is None

    def test_secret_exists(self, kube_client, mock_k8s_apis):
        """Test checking if secret exists."""
        mock_k8s_apis["core_api"].read_namespaced_secret.return_value = MagicMock()
        assert kube_client.secret_exists("ns", "secret") is True
        mock_k8s_apis["core_api"].read_namespaced_secret.assert_called_once_with(
            name="secret", namespace="ns", _request_timeout=30
        )

    def test_secret_not_exists(self, kube_client, mock_k8s_apis):
        """Test checking if secret does not exist."""
        mock_k8s_apis["core_api"].read_namespaced_secret.side_effect = ApiException(status=404)
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
        mock_k8s_apis["custom_api"].get_namespaced_custom_object.side_effect = ApiException(status=404)
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
        assert result[0] == {"metadata": {"name": "pod1"}}
        assert result[1] == {"metadata": {"name": "pod2"}}
        mock_k8s_apis["core_api"].list_namespaced_pod.assert_called_once_with(
            namespace="test-ns",
            label_selector="app=test",
            _request_timeout=30,
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
            assert result[0] == {"metadata": {"name": "pod1"}}
            mock_k8s_apis["core_api"].list_namespaced_pod.assert_called_once_with(
                namespace="test-ns",
                label_selector=selector,
                _request_timeout=30,
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
        first_call_kwargs = mock_k8s_apis["core_api"].list_namespaced_pod.call_args_list[0].kwargs
        assert 1 <= first_call_kwargs["_request_timeout"] <= 10

    @patch("lib.kube_client.time.sleep")
    def test_wait_for_pods_ready_retries_transient_poll_error(self, mock_sleep, kube_client, mock_k8s_apis):
        """A transient poll error should consume one poll cycle, not nested retries."""
        pod_ready = MagicMock()
        pod_ready.to_dict.return_value = {
            "metadata": {"name": "pod1"},
            "status": {"conditions": [{"type": "Ready", "status": "True"}]},
        }
        mock_k8s_apis["core_api"].list_namespaced_pod.side_effect = [
            ApiException(status=500),
            MagicMock(items=[pod_ready]),
        ]

        result = kube_client.wait_for_pods_ready("test-ns", "app=test", timeout=10)

        assert result is True
        assert mock_k8s_apis["core_api"].list_namespaced_pod.call_count == 2
        mock_sleep.assert_called_once_with(5)

    @patch("lib.kube_client.time.sleep")
    def test_wait_for_pods_ready_allows_extra_pods(self, mock_sleep, kube_client, mock_k8s_apis):
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

        mock_k8s_apis["core_api"].list_namespaced_pod.return_value = MagicMock(items=[pod_ready, pod_extra])

        result = kube_client.wait_for_pods_ready("test-ns", "app=test", expected_count=1, timeout=5)

        assert result is True
        mock_sleep.assert_not_called()

    @patch("lib.kube_client.time.sleep")
    @patch("lib.kube_client.time.time")
    def test_wait_for_pods_ready_does_not_succeed_when_no_pods_exist_and_count_unspecified(
        self, mock_time, mock_sleep, kube_client, mock_k8s_apis
    ):
        """Empty pod lists must not be treated as ready unless zero pods are explicitly expected."""
        mock_k8s_apis["core_api"].list_namespaced_pod.return_value = MagicMock(items=[])
        mock_time.side_effect = chain([100.0, 100.0, 100.0, 104.9, 105.1], repeat(105.1))

        result = kube_client.wait_for_pods_ready("test-ns", "app=test", timeout=5)

        assert result is False
        mock_k8s_apis["core_api"].list_namespaced_pod.assert_called_once()

    @patch("lib.kube_client.time.sleep")
    def test_wait_for_pods_ready_succeeds_when_zero_pods_explicitly_expected(
        self, mock_sleep, kube_client, mock_k8s_apis
    ):
        """expected_count=0 is the explicit opt-in for zero-pod readiness."""
        mock_k8s_apis["core_api"].list_namespaced_pod.return_value = MagicMock(items=[])

        result = kube_client.wait_for_pods_ready("test-ns", "app=test", expected_count=0, timeout=5)

        assert result is True
        mock_sleep.assert_not_called()

    @patch("lib.kube_client.time.sleep")
    @patch("lib.kube_client.time.time")
    def test_wait_for_pods_ready_with_expected_count_waits_for_enough_pods(
        self, mock_time, mock_sleep, kube_client, mock_k8s_apis
    ):
        """expected_count > 0 must not pass when fewer pods are present."""
        pod_ready = MagicMock()
        pod_ready.to_dict.return_value = {
            "metadata": {"name": "pod1"},
            "status": {"conditions": [{"type": "Ready", "status": "True"}]},
        }
        mock_k8s_apis["core_api"].list_namespaced_pod.return_value = MagicMock(items=[pod_ready])
        mock_time.side_effect = chain([100.0, 100.0, 100.0, 104.9, 105.1], repeat(105.1))

        result = kube_client.wait_for_pods_ready("test-ns", "app=test", expected_count=2, timeout=5)

        assert result is False
        mock_k8s_apis["core_api"].list_namespaced_pod.assert_called_once()

    @patch("lib.kube_client.time.sleep")
    @patch("lib.kube_client.time.time")
    def test_wait_for_pods_ready_uses_remaining_budget(self, mock_time, mock_sleep, kube_client, mock_k8s_apis):
        """Each polling API call should use the remaining wall-clock timeout budget."""
        pod_not_ready = MagicMock()
        pod_not_ready.to_dict.return_value = {
            "metadata": {"name": "pod1"},
            "status": {"conditions": [{"type": "Ready", "status": "False"}]},
        }
        mock_k8s_apis["core_api"].list_namespaced_pod.return_value = MagicMock(items=[pod_not_ready])

        # start_time=100, loop check=100, remaining-budget check=108 -> 2s left,
        # sleep budget check=109.5 -> 0.5s sleep, next loop check=110.1 -> timeout
        mock_time.side_effect = chain([100.0, 100.0, 108.0, 109.5, 110.1], repeat(110.1))

        result = kube_client.wait_for_pods_ready("test-ns", "app=test", timeout=10)

        assert result is False
        mock_k8s_apis["core_api"].list_namespaced_pod.assert_called_once()
        call_kwargs = mock_k8s_apis["core_api"].list_namespaced_pod.call_args.kwargs
        assert call_kwargs["_request_timeout"] == 2
        mock_sleep.assert_called_once_with(0.5)

    @patch("lib.kube_client.time.sleep")
    @patch("lib.kube_client.time.time")
    def test_wait_for_pods_ready_times_out_on_repeated_transient_errors(
        self, mock_time, mock_sleep, kube_client, mock_k8s_apis
    ):
        """Repeated transient poll failures must respect the wall-clock timeout."""
        mock_k8s_apis["core_api"].list_namespaced_pod.side_effect = ApiException(status=500)
        mock_time.side_effect = chain([100.0, 100.0, 100.0, 108.0, 110.1], repeat(110.1))

        result = kube_client.wait_for_pods_ready("test-ns", "app=test", timeout=10)

        assert result is False
        mock_k8s_apis["core_api"].list_namespaced_pod.assert_called_once()
        mock_sleep.assert_called_once_with(2.0)

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
class TestKubeClientRequestTimeouts:
    """Kubernetes API calls should pass explicit per-request timeout bounds."""

    def test_read_calls_include_request_timeout(self, kube_client, mock_k8s_apis):
        """Read helpers must not rely only on client configuration timeout."""
        namespace = MagicMock()
        namespace.to_dict.return_value = {"metadata": {"name": "test-ns"}}
        secret = MagicMock()
        secret.to_dict.return_value = {"metadata": {"name": "test-secret"}}
        mock_k8s_apis["core_api"].read_namespace.return_value = namespace
        mock_k8s_apis["core_api"].read_namespaced_secret.return_value = secret

        kube_client.get_namespace("test-ns")
        kube_client.get_secret("test-ns", "test-secret")

        assert mock_k8s_apis["core_api"].read_namespace.call_args.kwargs["_request_timeout"] == 30
        assert mock_k8s_apis["core_api"].read_namespaced_secret.call_args.kwargs["_request_timeout"] == 30

    def test_custom_resource_calls_include_request_timeout(self, kube_client, mock_k8s_apis):
        """Custom resource read/list/create/patch calls should be individually bounded."""
        mock_k8s_apis["custom_api"].get_namespaced_custom_object.return_value = {"metadata": {"name": "restore"}}
        mock_k8s_apis["custom_api"].list_namespaced_custom_object.return_value = {
            "items": [],
            "metadata": {},
        }
        mock_k8s_apis["custom_api"].patch_namespaced_custom_object.return_value = {"metadata": {"name": "restore"}}
        mock_k8s_apis["custom_api"].create_namespaced_custom_object.return_value = {"metadata": {"name": "restore"}}

        body = {"metadata": {"name": "restore"}}

        kube_client.get_custom_resource("cluster.open-cluster-management.io", "v1beta1", "restores", "restore", "ns")
        kube_client.list_custom_resources("cluster.open-cluster-management.io", "v1beta1", "restores", "ns")
        kube_client.patch_custom_resource(
            "cluster.open-cluster-management.io",
            "v1beta1",
            "restores",
            "restore",
            {"spec": {"paused": True}},
            "ns",
        )
        kube_client.create_custom_resource(
            "cluster.open-cluster-management.io",
            "v1beta1",
            "restores",
            body,
            "ns",
        )

        assert mock_k8s_apis["custom_api"].get_namespaced_custom_object.call_args.kwargs["_request_timeout"] == 30
        assert mock_k8s_apis["custom_api"].list_namespaced_custom_object.call_args.kwargs["_request_timeout"] == 30
        assert mock_k8s_apis["custom_api"].patch_namespaced_custom_object.call_args.kwargs["_request_timeout"] == 30
        assert mock_k8s_apis["custom_api"].create_namespaced_custom_object.call_args.kwargs["_request_timeout"] == 30

    def test_scale_and_log_calls_include_request_timeout(self, kube_client, mock_k8s_apis):
        """Scale and log calls should also have explicit request bounds."""
        scale_response = MagicMock()
        scale_response.to_dict.return_value = {"status": "scaled"}
        mock_k8s_apis["apps_api"].patch_namespaced_deployment_scale.return_value = scale_response
        mock_k8s_apis["core_api"].read_namespaced_pod_log.return_value = "log output"

        kube_client.scale_deployment("ns", "deploy", 2)
        kube_client.get_pod_logs("pod", "ns", container="main", tail_lines=10)

        assert mock_k8s_apis["apps_api"].patch_namespaced_deployment_scale.call_args.kwargs["_request_timeout"] == 30
        assert mock_k8s_apis["core_api"].read_namespaced_pod_log.call_args.kwargs["_request_timeout"] == 30


@pytest.mark.unit
class TestMutatorIdempotency:
    """Tests for 409-reconciliation and retry safety in mutating helpers."""

    def test_create_custom_resource_409_reconciles_when_resource_exists(self, kube_client, mock_k8s_apis):
        """When create returns 409 and reread object matches requested body, treat as success."""
        body = {
            "apiVersion": "cluster.open-cluster-management.io/v1beta1",
            "kind": "Restore",
            "metadata": {"name": "test-restore", "namespace": "test-ns"},
            "spec": {"syncRestoreWithNewBackups": True},
        }
        existing = {
            "apiVersion": "cluster.open-cluster-management.io/v1beta1",
            "kind": "Restore",
            "metadata": {
                "name": "test-restore",
                "namespace": "test-ns",
                "resourceVersion": "1",
                "uid": "abc123",
                "creationTimestamp": "2026-03-06T12:00:00Z",
            },
            "spec": {"syncRestoreWithNewBackups": True},
            "status": {"phase": "Running"},
        }
        mock_k8s_apis["custom_api"].create_namespaced_custom_object.side_effect = ApiException(status=409)
        mock_k8s_apis["custom_api"].get_namespaced_custom_object.return_value = existing

        result = kube_client.create_custom_resource(
            group="cluster.open-cluster-management.io",
            version="v1beta1",
            plural="restores",
            body=body,
            namespace="test-ns",
        )

        assert result == existing
        mock_k8s_apis["custom_api"].create_namespaced_custom_object.assert_called_once()
        mock_k8s_apis["custom_api"].get_namespaced_custom_object.assert_called_once()

    def test_create_custom_resource_409_uses_raw_reread_not_retry_wrapped_get(self, kube_client, mock_k8s_apis):
        """409 reconciliation should not recurse through retry-wrapped get_custom_resource."""
        body = {
            "apiVersion": "cluster.open-cluster-management.io/v1beta1",
            "kind": "Restore",
            "metadata": {"name": "test-restore", "namespace": "test-ns"},
            "spec": {"syncRestoreWithNewBackups": True},
        }
        existing = {
            "apiVersion": "cluster.open-cluster-management.io/v1beta1",
            "kind": "Restore",
            "metadata": {
                "name": "test-restore",
                "namespace": "test-ns",
                "resourceVersion": "1",
            },
            "spec": {"syncRestoreWithNewBackups": True},
        }
        mock_k8s_apis["custom_api"].create_namespaced_custom_object.side_effect = ApiException(status=409)
        mock_k8s_apis["custom_api"].get_namespaced_custom_object.return_value = existing

        with patch.object(
            kube_client,
            "get_custom_resource",
            side_effect=AssertionError("unexpected wrapper call"),
        ):
            result = kube_client.create_custom_resource(
                group="cluster.open-cluster-management.io",
                version="v1beta1",
                plural="restores",
                body=body,
                namespace="test-ns",
            )

        assert result == existing

    def test_create_custom_resource_409_reraises_when_resource_absent(self, kube_client, mock_k8s_apis):
        """When create returns 409 but resource is not found on re-read, re-raise the 409."""
        mock_k8s_apis["custom_api"].create_namespaced_custom_object.side_effect = ApiException(status=409)
        mock_k8s_apis["custom_api"].get_namespaced_custom_object.side_effect = ApiException(status=404)

        with pytest.raises(ApiException) as exc_info:
            kube_client.create_custom_resource(
                group="cluster.open-cluster-management.io",
                version="v1beta1",
                plural="restores",
                body={"metadata": {"name": "test-restore"}},
                namespace="test-ns",
            )

        assert exc_info.value.status == 409

    def test_create_custom_resource_409_reraises_when_existing_resource_differs(self, kube_client, mock_k8s_apis):
        """When create returns 409 and the reread object differs from the requested body, re-raise."""
        body = {
            "apiVersion": "cluster.open-cluster-management.io/v1beta1",
            "kind": "Restore",
            "metadata": {"name": "test-restore", "namespace": "test-ns"},
            "spec": {"syncRestoreWithNewBackups": True},
        }
        existing = {
            "apiVersion": "cluster.open-cluster-management.io/v1beta1",
            "kind": "Restore",
            "metadata": {
                "name": "test-restore",
                "namespace": "test-ns",
                "resourceVersion": "1",
            },
            "spec": {"syncRestoreWithNewBackups": False},
        }
        mock_k8s_apis["custom_api"].create_namespaced_custom_object.side_effect = ApiException(status=409)
        mock_k8s_apis["custom_api"].get_namespaced_custom_object.return_value = existing

        with pytest.raises(ApiException) as exc_info:
            kube_client.create_custom_resource(
                group="cluster.open-cluster-management.io",
                version="v1beta1",
                plural="restores",
                body=body,
                namespace="test-ns",
            )

        assert exc_info.value.status == 409

    @patch("lib.kube_client.time.sleep")
    def test_create_custom_resource_retries_named_retryable_create_and_reconciles(
        self, mock_sleep, kube_client, mock_k8s_apis
    ):
        """Named resources may retry retryable create errors and reconcile a later 409."""
        body = {
            "apiVersion": "cluster.open-cluster-management.io/v1beta1",
            "kind": "Restore",
            "metadata": {"name": "test-restore", "namespace": "test-ns"},
            "spec": {"veleroManagedClustersBackupName": "latest"},
        }
        existing = {
            "apiVersion": "cluster.open-cluster-management.io/v1beta1",
            "kind": "Restore",
            "metadata": {
                "name": "test-restore",
                "namespace": "test-ns",
                "resourceVersion": "1",
            },
            "spec": {"veleroManagedClustersBackupName": "latest"},
        }
        mock_k8s_apis["custom_api"].create_namespaced_custom_object.side_effect = [
            ApiException(status=500),
            ApiException(status=409),
        ]
        mock_k8s_apis["custom_api"].get_namespaced_custom_object.return_value = existing

        result = kube_client.create_custom_resource(
            group="cluster.open-cluster-management.io",
            version="v1beta1",
            plural="restores",
            body=body,
            namespace="test-ns",
        )

        assert result == existing
        assert mock_k8s_apis["custom_api"].create_namespaced_custom_object.call_count == 2
        mock_k8s_apis["custom_api"].get_namespaced_custom_object.assert_called_once()

    def test_create_custom_resource_does_not_retry_unnamed_retryable_create(self, kube_client, mock_k8s_apis):
        """Generated-name creates must fail after the first retryable create error to avoid duplicates."""
        body = {
            "apiVersion": "cluster.open-cluster-management.io/v1beta1",
            "kind": "Restore",
            "metadata": {"generateName": "restore-"},
            "spec": {"veleroManagedClustersBackupName": "latest"},
        }
        mock_k8s_apis["custom_api"].create_namespaced_custom_object.side_effect = ApiException(status=500)

        with pytest.raises(ApiException) as exc_info:
            kube_client.create_custom_resource(
                group="cluster.open-cluster-management.io",
                version="v1beta1",
                plural="restores",
                body=body,
                namespace="test-ns",
            )

        assert exc_info.value.status == 500
        mock_k8s_apis["custom_api"].create_namespaced_custom_object.assert_called_once()
        mock_k8s_apis["custom_api"].get_namespaced_custom_object.assert_not_called()

    def test_create_or_patch_configmap_creates_when_absent(self, kube_client, mock_k8s_apis):
        """ConfigMap upsert creates when the resource does not yet exist."""
        created = MagicMock()
        created.to_dict.return_value = {"metadata": {"name": "cm1"}, "data": {"k": "v"}}
        mock_k8s_apis["core_api"].create_namespaced_config_map.return_value = created

        result = kube_client.create_or_patch_configmap("ns", "cm1", {"k": "v"})

        assert result == {"metadata": {"name": "cm1"}, "data": {"k": "v"}}
        mock_k8s_apis["core_api"].create_namespaced_config_map.assert_called_once()
        mock_k8s_apis["core_api"].patch_namespaced_config_map.assert_not_called()

    def test_create_or_patch_configmap_patches_on_409(self, kube_client, mock_k8s_apis):
        """ConfigMap upsert patches when create returns 409 (concurrent create or timeout-after-create)."""
        mock_k8s_apis["core_api"].create_namespaced_config_map.side_effect = ApiException(status=409)
        patched = MagicMock()
        patched.to_dict.return_value = {"metadata": {"name": "cm1"}, "data": {"k": "v"}}
        mock_k8s_apis["core_api"].patch_namespaced_config_map.return_value = patched

        result = kube_client.create_or_patch_configmap("ns", "cm1", {"k": "v"})

        assert result == {"metadata": {"name": "cm1"}, "data": {"k": "v"}}
        mock_k8s_apis["core_api"].create_namespaced_config_map.assert_called_once()
        mock_k8s_apis["core_api"].patch_namespaced_config_map.assert_called_once()

    def test_create_or_patch_configmap_no_nested_retry_on_read(self, kube_client, mock_k8s_apis):
        """ConfigMap upsert no longer calls get_configmap; no nested retry amplification."""
        created = MagicMock()
        created.to_dict.return_value = {"metadata": {"name": "cm1"}}
        mock_k8s_apis["core_api"].create_namespaced_config_map.return_value = created

        kube_client.create_or_patch_configmap("ns", "cm1", {"k": "v"})

        # read_namespaced_config_map must NOT be called (old read-then-create path is gone)
        mock_k8s_apis["core_api"].read_namespaced_config_map.assert_not_called()


@pytest.mark.unit
class TestKubeClientInitialization:
    """Test cases for KubeClient initialization."""

    def test_init_logs_configuration_exception_by_default(self, caplog):
        """Unrelated callers retain the established configuration diagnostic."""
        context = "unrelated-context-sentinel"
        raw_error = "raw-config-exception-sentinel"

        with patch(
            "lib.kube_client.config.new_client_from_config",
            side_effect=ConfigException(raw_error),
        ), caplog.at_level("ERROR", logger="acm_switchover"):
            with pytest.raises(ConfigException):
                KubeClient(context=context)

        assert "Failed to load kubeconfig for context unrelated-context-sentinel" in caplog.text
        assert raw_error in caplog.text

    def test_init_can_suppress_configuration_exception_diagnostics_for_identity_checks(self, caplog):
        """The SSA-01 constructor path must not emit raw configuration details."""
        context = "identity-context-sentinel"
        kubeconfig_path = "/private/identity-kubeconfig-sentinel"
        token = "identity-token-sentinel"
        credential = "identity-credential-sentinel"
        raw_error = f"raw-config-exception-sentinel {kubeconfig_path} {token} {credential}"

        with patch(
            "lib.kube_client.config.new_client_from_config",
            side_effect=ConfigException(raw_error),
        ), caplog.at_level("ERROR", logger="acm_switchover"):
            with pytest.raises(ConfigException):
                KubeClient(context=context, log_config_errors=False)

        for sentinel in ("raw-config-exception-sentinel", context, kubeconfig_path, token, credential):
            assert sentinel not in caplog.text

    @patch("lib.kube_client.config.load_kube_config")
    @patch("lib.kube_client.config.new_client_from_config")
    def test_init_with_context(self, mock_new_client, mock_load_config):
        """Test initializing with a specific context."""
        api_client = MagicMock()
        api_client.configuration = MagicMock()
        mock_new_client.return_value = api_client

        kc = KubeClient(context="test-context")

        assert kc.context == "test-context"
        assert kc.dry_run is False
        mock_new_client.assert_called_once_with(context="test-context", persist_config=False)
        mock_load_config.assert_not_called()

    @patch("lib.kube_client.config.load_kube_config")
    @patch("lib.kube_client.config.new_client_from_config")
    def test_init_without_context(self, mock_new_client, mock_load_config):
        """Test initializing without a context."""
        api_client = MagicMock()
        api_client.configuration = MagicMock()
        mock_new_client.return_value = api_client

        kc = KubeClient()

        assert kc.context is None
        assert kc.dry_run is False
        mock_new_client.assert_called_once_with(context=None, persist_config=False)
        mock_load_config.assert_not_called()

    @patch("lib.kube_client.client.CustomObjectsApi")
    @patch("lib.kube_client.client.AppsV1Api")
    @patch("lib.kube_client.client.CoreV1Api")
    @patch("lib.kube_client.config.new_client_from_config")
    def test_init_uses_isolated_api_client_configuration(
        self, mock_new_client, mock_core_cls, mock_apps_cls, mock_custom_cls
    ):
        """KubeClient should configure the isolated ApiClient returned for the context."""
        api_client = MagicMock()
        api_client.configuration = MagicMock()
        api_client.configuration.assert_hostname = True
        mock_new_client.return_value = api_client

        KubeClient(context="ctx-a", request_timeout=45, disable_hostname_verification=True)

        assert api_client.configuration.retries == 0
        assert api_client.configuration.timeout == 45
        assert api_client.configuration.assert_hostname is False
        mock_core_cls.assert_called_once_with(api_client)
        mock_apps_cls.assert_called_once_with(api_client)
        mock_custom_cls.assert_called_once_with(api_client)


@pytest.mark.unit
class TestApiCallDecorator:
    """Tests for the @api_call decorator."""

    def test_returns_not_found_value_on_404(self):
        """Decorator returns not_found_value when ApiException with 404."""

        @api_call(not_found_value="default_value")
        def mock_api_method():
            raise ApiException(status=404)

        result = mock_api_method()
        assert result == "default_value"

    def test_returns_none_on_404_by_default(self):
        """Decorator returns None on 404 when not_found_value not specified."""

        @api_call()
        def mock_api_method():
            raise ApiException(status=404)

        result = mock_api_method()
        assert result is None

    def test_reraises_retryable_errors(self):
        """Decorator re-raises 5xx errors for tenacity to handle."""

        @api_call(not_found_value=None)
        def mock_api_method():
            raise ApiException(status=503)

        # Should re-raise ApiException for tenacity retry
        with pytest.raises(ApiException) as exc_info:
            mock_api_method()
        assert exc_info.value.status == 503

    def test_logs_and_reraises_non_retryable_errors(self):
        """Decorator logs non-retryable errors before re-raising."""

        @api_call(not_found_value=None, log_on_error=True)
        def mock_api_method():
            raise ApiException(status=403, reason="Forbidden")

        with pytest.raises(ApiException) as exc_info:
            mock_api_method()
        assert exc_info.value.status == 403

    def test_no_logging_when_log_on_error_false(self):
        """Decorator suppresses logging for non-retryable errors when disabled."""

        @api_call(not_found_value=None, log_on_error=False)
        def mock_api_method():
            raise ApiException(status=403, reason="Forbidden")

        with patch("lib.kube_client.logger.error") as mock_error:
            with pytest.raises(ApiException) as exc_info:
                mock_api_method()

        assert exc_info.value.status == 403
        mock_error.assert_not_called()

    def test_uses_method_name_as_default_resource_desc(self):
        """Decorator logs the method name when no custom resource_desc is provided."""

        @api_call(not_found_value=None)
        def get_some_resource():
            raise ApiException(status=403, reason="Forbidden")

        with patch("lib.kube_client.logger.error") as mock_error:
            with pytest.raises(ApiException) as exc_info:
                get_some_resource()

        assert exc_info.value.status == 403
        mock_error.assert_called_once()
        log_message = mock_error.call_args.args[0] % mock_error.call_args.args[1:]
        assert "Failed to get some resource:" in log_message
        assert "Forbidden" in log_message

    def test_uses_custom_resource_desc(self):
        """Decorator logs the provided resource_desc instead of the method name."""

        @api_call(not_found_value=None, resource_desc="fetch widget")
        def my_method():
            raise ApiException(status=403, reason="Forbidden")

        with patch("lib.kube_client.logger.error") as mock_error:
            with pytest.raises(ApiException) as exc_info:
                my_method()

        assert exc_info.value.status == 403
        mock_error.assert_called_once()
        log_message = mock_error.call_args.args[0] % mock_error.call_args.args[1:]
        assert "Failed to fetch widget:" in log_message
        assert "my method" not in log_message

    def test_returns_successful_result(self):
        """Decorator returns the function result on success."""

        @api_call(not_found_value=None)
        def mock_api_method():
            return {"name": "test", "value": 42}

        result = mock_api_method()
        assert result == {"name": "test", "value": 42}

    def test_preserves_function_name(self):
        """Decorator preserves the wrapped function name."""

        @api_call(not_found_value=None)
        def my_descriptive_function():
            return "success"

        assert my_descriptive_function.__name__ == "my_descriptive_function"


@pytest.mark.unit
class TestIsRetryableError:
    """Tests for the is_retryable_error function."""

    def test_500_is_retryable(self):
        """500 Internal Server Error is retryable."""
        assert is_retryable_error(ApiException(status=500)) is True

    def test_502_is_retryable(self):
        """502 Bad Gateway is retryable."""
        assert is_retryable_error(ApiException(status=502)) is True

    def test_503_is_retryable(self):
        """503 Service Unavailable is retryable."""
        assert is_retryable_error(ApiException(status=503)) is True

    def test_429_is_retryable(self):
        """429 Too Many Requests is retryable."""
        assert is_retryable_error(ApiException(status=429)) is True

    def test_404_is_not_retryable(self):
        """404 Not Found is not retryable."""
        assert is_retryable_error(ApiException(status=404)) is False

    def test_403_is_not_retryable(self):
        """403 Forbidden is not retryable."""
        assert is_retryable_error(ApiException(status=403)) is False

    def test_400_is_not_retryable(self):
        """400 Bad Request is not retryable."""
        assert is_retryable_error(ApiException(status=400)) is False

    def test_network_oserror_is_retryable(self):
        """Network-related OSError with specific errno values are retryable."""
        # Core errno values that exist on all platforms
        assert is_retryable_error(OSError(errno.ECONNREFUSED, "Connection refused")) is True
        assert is_retryable_error(OSError(errno.ECONNRESET, "Connection reset")) is True
        assert is_retryable_error(OSError(errno.ETIMEDOUT, "Connection timed out")) is True
        assert is_retryable_error(OSError(errno.ENETUNREACH, "Network unreachable")) is True
        assert is_retryable_error(OSError(errno.EAGAIN, "Resource temporarily unavailable")) is True

        # Platform-specific errno values (use getattr to handle cross-platform)
        econnaborted = getattr(errno, "ECONNABORTED", None)
        if econnaborted is not None:
            assert is_retryable_error(OSError(econnaborted, "Connection aborted")) is True

        ehostunreach = getattr(errno, "EHOSTUNREACH", None)
        if ehostunreach is not None:
            assert is_retryable_error(OSError(ehostunreach, "No route to host")) is True

        ewouldblock = getattr(errno, "EWOULDBLOCK", None)
        if ewouldblock is not None:
            assert is_retryable_error(OSError(ewouldblock, "Operation would block")) is True

    def test_file_oserror_is_not_retryable(self):
        """File-related OSError (not network) should not be retryable."""
        assert is_retryable_error(OSError(errno.ENOENT, "No such file")) is False
        assert is_retryable_error(OSError(errno.EACCES, "Permission denied")) is False
        assert is_retryable_error(OSError(errno.EEXIST, "File exists")) is False


@pytest.mark.unit
class TestDeleteOperationsNormalMode:
    """Tests for delete operations in normal (non-dry-run) mode."""

    def test_delete_custom_resource_namespaced_success(self, kube_client, mock_k8s_apis):
        """Test successful deletion of a namespaced custom resource."""
        mock_k8s_apis["custom_api"].delete_namespaced_custom_object.return_value = {}

        result = kube_client.delete_custom_resource(
            "cluster.open-cluster-management.io",
            "v1",
            "managedclusters",
            name="test-cluster",
            namespace="test-ns",
        )

        assert result is True
        mock_k8s_apis["custom_api"].delete_namespaced_custom_object.assert_called_once_with(
            group="cluster.open-cluster-management.io",
            version="v1",
            namespace="test-ns",
            plural="managedclusters",
            name="test-cluster",
        )

    def test_delete_custom_resource_cluster_scoped_success(self, kube_client, mock_k8s_apis):
        """Test successful deletion of a cluster-scoped custom resource."""
        mock_k8s_apis["custom_api"].delete_cluster_custom_object.return_value = {}

        result = kube_client.delete_custom_resource(
            "cluster.open-cluster-management.io",
            "v1",
            "managedclusters",
            name="test-cluster",
        )

        assert result is True
        mock_k8s_apis["custom_api"].delete_cluster_custom_object.assert_called_once_with(
            group="cluster.open-cluster-management.io",
            version="v1",
            plural="managedclusters",
            name="test-cluster",
        )

    def test_delete_custom_resource_with_timeout(self, kube_client, mock_k8s_apis):
        """Test deletion passes timeout_seconds as _request_timeout."""
        mock_k8s_apis["custom_api"].delete_namespaced_custom_object.return_value = {}

        result = kube_client.delete_custom_resource(
            "cluster.open-cluster-management.io",
            "v1",
            "managedclusters",
            name="test-cluster",
            namespace="test-ns",
            timeout_seconds=60,
        )

        assert result is True
        mock_k8s_apis["custom_api"].delete_namespaced_custom_object.assert_called_once_with(
            group="cluster.open-cluster-management.io",
            version="v1",
            namespace="test-ns",
            plural="managedclusters",
            name="test-cluster",
            _request_timeout=60,
        )

    def test_delete_custom_resource_404_returns_true(self, kube_client, mock_k8s_apis):
        """Test 404 on delete returns True (already absent, idempotent)."""
        mock_k8s_apis["custom_api"].delete_namespaced_custom_object.side_effect = ApiException(status=404)

        result = kube_client.delete_custom_resource(
            "cluster.open-cluster-management.io",
            "v1",
            "managedclusters",
            name="test-cluster",
            namespace="test-ns",
        )

        assert result is True

    def test_delete_custom_resource_other_error_reraises(self, kube_client, mock_k8s_apis):
        """Test non-404 ApiException is re-raised."""
        mock_k8s_apis["custom_api"].delete_namespaced_custom_object.side_effect = ApiException(status=403)

        with pytest.raises(ApiException) as exc_info:
            kube_client.delete_custom_resource(
                "cluster.open-cluster-management.io",
                "v1",
                "managedclusters",
                name="test-cluster",
                namespace="test-ns",
            )

        assert exc_info.value.status == 403

    def test_delete_pod_success(self, kube_client, mock_k8s_apis):
        """Test successful pod deletion."""
        mock_k8s_apis["core_api"].delete_namespaced_pod.return_value = {}

        result = kube_client.delete_pod("test-ns", "test-pod")

        assert result is True
        mock_k8s_apis["core_api"].delete_namespaced_pod.assert_called_once_with(
            name="test-pod", namespace="test-ns", _request_timeout=30
        )

    def test_delete_pod_404_returns_true(self, kube_client, mock_k8s_apis):
        """Test 404 on pod delete returns True (already absent)."""
        mock_k8s_apis["core_api"].delete_namespaced_pod.side_effect = ApiException(status=404)

        result = kube_client.delete_pod("test-ns", "test-pod")

        assert result is True

    def test_delete_pod_other_error_reraises(self, kube_client, mock_k8s_apis):
        """Test non-404 ApiException on pod delete is re-raised."""
        mock_k8s_apis["core_api"].delete_namespaced_pod.side_effect = ApiException(status=403)

        with pytest.raises(ApiException) as exc_info:
            kube_client.delete_pod("test-ns", "test-pod")

        assert exc_info.value.status == 403

    def test_delete_configmap_success(self, kube_client, mock_k8s_apis):
        """Test successful configmap deletion."""
        mock_k8s_apis["core_api"].delete_namespaced_config_map.return_value = {}

        result = kube_client.delete_configmap("test-ns", "test-cm")

        assert result is True
        mock_k8s_apis["core_api"].delete_namespaced_config_map.assert_called_once_with(
            name="test-cm", namespace="test-ns", _request_timeout=30
        )

    def test_delete_configmap_404_returns_true(self, kube_client, mock_k8s_apis):
        """Test 404 on configmap delete returns True (already absent)."""
        mock_k8s_apis["core_api"].delete_namespaced_config_map.side_effect = ApiException(status=404)

        result = kube_client.delete_configmap("test-ns", "test-cm")

        assert result is True

    def test_delete_configmap_other_error_reraises(self, kube_client, mock_k8s_apis):
        """Test non-404 ApiException on configmap delete is re-raised."""
        mock_k8s_apis["core_api"].delete_namespaced_config_map.side_effect = ApiException(status=403)

        with pytest.raises(ApiException) as exc_info:
            kube_client.delete_configmap("test-ns", "test-cm")

        assert exc_info.value.status == 403


@pytest.mark.unit
class TestGetDeployment:
    """Tests for get_deployment method."""

    def test_get_deployment_success(self, kube_client, mock_k8s_apis):
        """Test successful deployment retrieval."""
        mock_deployment = MagicMock()
        mock_deployment.to_dict.return_value = {
            "metadata": {"name": "test-deploy", "namespace": "test-ns"},
            "spec": {"replicas": 3},
        }
        mock_k8s_apis["apps_api"].read_namespaced_deployment.return_value = mock_deployment

        result = kube_client.get_deployment("test-deploy", "test-ns")

        assert result is not None
        assert result["metadata"]["name"] == "test-deploy"
        assert result["spec"]["replicas"] == 3
        mock_k8s_apis["apps_api"].read_namespaced_deployment.assert_called_once_with(
            name="test-deploy", namespace="test-ns", _request_timeout=30
        )

    def test_get_deployment_not_found(self, kube_client, mock_k8s_apis):
        """Test 404 returns None for missing deployment."""
        mock_k8s_apis["apps_api"].read_namespaced_deployment.side_effect = ApiException(status=404)

        result = kube_client.get_deployment("nonexistent", "test-ns")

        assert result is None

    def test_get_deployment_other_error_reraises(self, kube_client, mock_k8s_apis):
        """Test non-404 ApiException is re-raised."""
        mock_k8s_apis["apps_api"].read_namespaced_deployment.side_effect = ApiException(status=403)

        with pytest.raises(ApiException) as exc_info:
            kube_client.get_deployment("test-deploy", "test-ns")

        assert exc_info.value.status == 403


@pytest.mark.unit
class TestGetStatefulSet:
    """Tests for get_statefulset method."""

    def test_get_statefulset_success(self, kube_client, mock_k8s_apis):
        """Test successful statefulset retrieval."""
        mock_sts = MagicMock()
        mock_sts.to_dict.return_value = {
            "metadata": {"name": "test-sts", "namespace": "test-ns"},
            "spec": {"replicas": 1},
        }
        mock_k8s_apis["apps_api"].read_namespaced_stateful_set.return_value = mock_sts

        result = kube_client.get_statefulset("test-sts", "test-ns")

        assert result is not None
        assert result["metadata"]["name"] == "test-sts"
        assert result["spec"]["replicas"] == 1
        mock_k8s_apis["apps_api"].read_namespaced_stateful_set.assert_called_once_with(
            name="test-sts", namespace="test-ns", _request_timeout=30
        )

    def test_get_statefulset_not_found(self, kube_client, mock_k8s_apis):
        """Test 404 returns None for missing statefulset."""
        mock_k8s_apis["apps_api"].read_namespaced_stateful_set.side_effect = ApiException(status=404)

        result = kube_client.get_statefulset("nonexistent", "test-ns")

        assert result is None

    def test_get_statefulset_other_error_reraises(self, kube_client, mock_k8s_apis):
        """Test non-404 ApiException is re-raised."""
        mock_k8s_apis["apps_api"].read_namespaced_stateful_set.side_effect = ApiException(status=403)

        with pytest.raises(ApiException) as exc_info:
            kube_client.get_statefulset("test-sts", "test-ns")

        assert exc_info.value.status == 403


@pytest.mark.unit
class TestGetPodLogs:
    """Tests for get_pod_logs method."""

    def test_get_pod_logs_success(self, kube_client, mock_k8s_apis):
        """Test successful log retrieval."""
        mock_k8s_apis["core_api"].read_namespaced_pod_log.return_value = "line1\nline2\nline3"

        result = kube_client.get_pod_logs("test-pod", "test-ns")

        assert result == "line1\nline2\nline3"
        mock_k8s_apis["core_api"].read_namespaced_pod_log.assert_called_once_with(
            name="test-pod", namespace="test-ns", _request_timeout=30
        )

    def test_get_pod_logs_with_container(self, kube_client, mock_k8s_apis):
        """Test log retrieval with specific container."""
        mock_k8s_apis["core_api"].read_namespaced_pod_log.return_value = "container logs"

        result = kube_client.get_pod_logs("test-pod", "test-ns", container="sidecar")

        assert result == "container logs"
        mock_k8s_apis["core_api"].read_namespaced_pod_log.assert_called_once_with(
            name="test-pod",
            namespace="test-ns",
            container="sidecar",
            _request_timeout=30,
        )

    def test_get_pod_logs_with_tail_lines(self, kube_client, mock_k8s_apis):
        """Test log retrieval with tail_lines parameter."""
        mock_k8s_apis["core_api"].read_namespaced_pod_log.return_value = "last line"

        result = kube_client.get_pod_logs("test-pod", "test-ns", tail_lines=10)

        assert result == "last line"
        mock_k8s_apis["core_api"].read_namespaced_pod_log.assert_called_once_with(
            name="test-pod", namespace="test-ns", tail_lines=10, _request_timeout=30
        )

    def test_get_pod_logs_with_container_and_tail_lines(self, kube_client, mock_k8s_apis):
        """Test log retrieval with both container and tail_lines."""
        mock_k8s_apis["core_api"].read_namespaced_pod_log.return_value = "filtered logs"

        result = kube_client.get_pod_logs("test-pod", "test-ns", container="app", tail_lines=50)

        assert result == "filtered logs"
        mock_k8s_apis["core_api"].read_namespaced_pod_log.assert_called_once_with(
            name="test-pod",
            namespace="test-ns",
            container="app",
            tail_lines=50,
            _request_timeout=30,
        )

    def test_get_pod_logs_404_returns_empty_string(self, kube_client, mock_k8s_apis):
        """Test 404 returns empty string (pod not found)."""
        mock_k8s_apis["core_api"].read_namespaced_pod_log.side_effect = ApiException(status=404)

        result = kube_client.get_pod_logs("nonexistent", "test-ns")

        assert result == ""

    def test_get_pod_logs_api_returns_none_coerced_to_empty(self, kube_client, mock_k8s_apis):
        """Test that None return from API is coerced to empty string."""
        mock_k8s_apis["core_api"].read_namespaced_pod_log.return_value = None

        result = kube_client.get_pod_logs("test-pod", "test-ns")

        assert result == ""

    def test_get_pod_logs_invalid_name_raises(self, kube_client, mock_k8s_apis):
        """Test that empty pod name raises ValidationError."""
        from lib.validation import ValidationError

        with pytest.raises(ValidationError):
            kube_client.get_pod_logs("", "test-ns")

    def test_get_pod_logs_invalid_namespace_raises(self, kube_client, mock_k8s_apis):
        """Test that empty namespace raises ValidationError."""
        from lib.validation import ValidationError

        with pytest.raises(ValidationError):
            kube_client.get_pod_logs("test-pod", "")

    def test_get_pod_logs_negative_tail_lines_raises(self, kube_client, mock_k8s_apis):
        """Test that negative tail_lines raises ValidationError."""
        from lib.validation import ValidationError

        with pytest.raises(ValidationError):
            kube_client.get_pod_logs("test-pod", "test-ns", tail_lines=-1)

    def test_get_pod_logs_dry_run_returns_empty(self, dry_run_client, mock_k8s_apis):
        """Test dry-run mode returns empty string without API call."""
        result = dry_run_client.get_pod_logs("test-pod", "test-ns")

        assert result == ""
        mock_k8s_apis["core_api"].read_namespaced_pod_log.assert_not_called()


class TestDiscoveryProver:
    """R4-03: kind absence must come from a successful discovery response."""

    def _client(self, call_api):
        client = KubeClient.__new__(KubeClient)
        client.request_timeout = 30
        client.dry_run = False
        client._api_client = Mock()
        client._api_client.call_api = call_api
        return client

    def test_served_kind_returns_items(self):
        call = Mock(
            return_value={
                "kind": "APIResourceList",
                "resources": [{"name": "multiclusterhubs", "kind": "MultiClusterHub"}],
            }
        )
        client = self._client(call)
        outcome = client._discovery_serves("operator.open-cluster-management.io", "v1", "multiclusterhubs")
        assert outcome.status is StrictReadStatus.ITEMS

    def test_absent_kind_in_a_successful_response_is_crd_absent(self):
        call = Mock(
            return_value={
                "kind": "APIResourceList",
                "resources": [{"name": "somethingelse", "kind": "SomethingElse"}],
            }
        )
        outcome = self._client(call)._discovery_serves("g", "v1", "multiclusterhubs")
        assert outcome.status is StrictReadStatus.CRD_ABSENT

    def test_discovery_service_unavailable_is_error_not_absence(self):
        call = Mock(side_effect=ApiException(status=503, reason="Service Unavailable"))
        outcome = self._client(call)._discovery_serves("g", "v1", "multiclusterhubs")
        assert outcome.status is StrictReadStatus.ERROR
        assert outcome.reason == STRICT_READ_REASON_DISCOVERY_UNVERIFIABLE

    def test_discovery_forbidden_is_error_not_absence(self):
        call = Mock(side_effect=ApiException(status=403, reason="Forbidden"))
        assert self._client(call)._discovery_serves("g", "v1", "p").status is StrictReadStatus.ERROR

    def test_discovery_timeout_is_error_not_absence(self):
        call = Mock(side_effect=TimeoutError("deadline exceeded"))
        assert self._client(call)._discovery_serves("g", "v1", "p").status is StrictReadStatus.ERROR

    def test_undecodable_discovery_body_is_error_not_absence(self):
        call = Mock(return_value="<html>gateway error</html>")
        assert self._client(call)._discovery_serves("g", "v1", "p").status is StrictReadStatus.ERROR

    def test_missing_resources_key_is_error_not_absence(self):
        call = Mock(return_value={"kind": "APIResourceList"})
        assert self._client(call)._discovery_serves("g", "v1", "p").status is StrictReadStatus.ERROR

    def test_discovery_404_is_error_not_absence(self):
        call = Mock(side_effect=ApiException(status=404, reason="Not Found"))
        outcome = self._client(call)._discovery_serves("g", "v1", "p")
        assert outcome.status is StrictReadStatus.ERROR

    def test_discovery_decode_failure_is_error_not_absence(self):
        call = Mock(side_effect=ValueError("invalid json"))
        assert self._client(call)._discovery_serves("g", "v1", "p").status is StrictReadStatus.ERROR

    def test_malformed_api_resource_list_is_error_not_absence(self):
        call = Mock(return_value={"kind": "APIResourceList", "resources": [{"name": 7}]})
        assert self._client(call)._discovery_serves("g", "v1", "p").status is StrictReadStatus.ERROR

    def test_irregular_plural_is_matched_by_exact_resource_name(self):
        call = Mock(
            return_value={
                "kind": "APIResourceList",
                "resources": [
                    {"name": "multiclusterobservabilities", "kind": "MultiClusterObservability"},
                ],
            }
        )
        outcome = self._client(call)._discovery_serves(
            "observability.open-cluster-management.io", "v1beta2", "multiclusterobservabilities"
        )
        assert outcome.status is StrictReadStatus.ITEMS

    def test_core_group_uses_the_core_discovery_path(self):
        call = Mock(return_value={"kind": "APIResourceList", "resources": [{"name": "pods", "kind": "Pod"}]})
        self._client(call)._discovery_serves("", "v1", "pods")
        assert call.call_args[0][0] == "/api/v1"
