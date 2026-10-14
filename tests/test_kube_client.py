"""Unit tests for lib/kube_client.py.

Modernized pytest tests with fixtures, markers, and parameterization.
Tests cover KubeClient initialization, CRUD operations, and dry-run mode.
"""

import errno
import inspect
import json
from itertools import chain, repeat
from unittest.mock import MagicMock, Mock, patch

import pytest
from kubernetes.client.rest import ApiException
from kubernetes.config.config_exception import ConfigException
from tenacity import wait_none
from urllib3.exceptions import ProtocolError, ReadTimeoutError
from urllib3.exceptions import TimeoutError as Urllib3TimeoutError

from lib.constants import (
    MANAGED_CLUSTER_API_GROUP,
    MANAGED_CLUSTER_API_VERSION,
    MANAGED_CLUSTER_PLURAL,
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
from lib.exceptions import MalformedResponseError
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
        # The pre-37 client shape: no `param_serialize`, so requests take the legacy `call_api` path.
        api_client = MagicMock(name="api_client", spec=["call_api", "configuration"])
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
            "metadata": {
                "name": "import-controller-config",
                "namespace": "multicluster-engine",
            },
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
            side_effect=[
                ApiException(status=503, reason=private_reason),
                {"metadata": {"name": "crd"}},
            ],
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
class TestCustomObjectResponseShape:
    """Custom-object API methods return the decoded JSON body untyped; a body that is not a
    JSON object is a malformed response, raised as an error and never read as absence."""

    _CALLS = {
        "route-host": ("get_namespaced_custom_object", lambda c: c.get_route_host("ns", "r")),
        "get": ("get_namespaced_custom_object", lambda c: c.get_custom_resource("g", "v1", "widgets", "w", "ns")),
        "get-advisory": (
            "get_cluster_custom_object",
            lambda c: c.get_custom_resource_advisory("g", "v1", "widgets", "w"),
        ),
        "list": ("list_namespaced_custom_object", lambda c: c.list_custom_resources("g", "v1", "widgets", "ns")),
        "patch": (
            "patch_namespaced_custom_object",
            lambda c: c.patch_custom_resource("g", "v1", "widgets", "w", {"spec": {}}, namespace="ns"),
        ),
        "create": (
            "create_namespaced_custom_object",
            lambda c: c.create_custom_resource("g", "v1", "widgets", {"metadata": {"name": "w"}}, namespace="ns"),
        ),
    }

    @pytest.mark.parametrize("response", [None, [], "Widget", 7], ids=["null", "list", "string", "number"])
    @pytest.mark.parametrize("call", sorted(_CALLS))
    def test_a_non_object_response_raises(self, kube_client, mock_k8s_apis, call, response):
        method, invoke = self._CALLS[call]
        getattr(mock_k8s_apis["custom_api"], method).return_value = response

        with pytest.raises(MalformedResponseError, match="expected a JSON object"):
            invoke(kube_client)


@pytest.mark.unit
class TestUndecodableCustomObjectBodies:
    """A 2xx custom-object body the installed client cannot decode is a malformed response,
    whatever error the client raises for it (36.x and 37.x differ); an HTTP error stays an
    `ApiException` with its status even when its body cannot be decoded."""

    _CALLS = {
        "route-host": (200, lambda c: c.get_route_host("ns", "r")),
        "get": (200, lambda c: c.get_custom_resource("g", "v1", "widgets", "w", "ns")),
        "get-advisory": (200, lambda c: c.get_custom_resource_advisory("g", "v1", "widgets", "w", "ns")),
        "list": (200, lambda c: c.list_custom_resources("g", "v1", "widgets", "ns")),
        "patch": (200, lambda c: c.patch_custom_resource("g", "v1", "widgets", "w", {"spec": {}}, namespace="ns")),
        "create": (
            201,
            lambda c: c.create_custom_resource("g", "v1", "widgets", {"metadata": {"name": "w"}}, namespace="ns"),
        ),
    }

    @staticmethod
    def _client(pool_manager):
        from kubernetes import client as k8s

        kube = KubeClient.__new__(KubeClient)
        kube.request_timeout = 30
        kube.dry_run = False
        kube._api_client = _real_api_client(pool_manager)
        kube.custom_api = k8s.CustomObjectsApi(kube._api_client)
        return kube

    @pytest.mark.parametrize(
        ("body", "content_type"),
        [(b"{not json", "application/json"), (b"\xff\xfe", "application/json"), (b"null", "application/octet-stream")],
        ids=["invalid-json", "invalid-utf8", "unsupported-content-type"],
    )
    @pytest.mark.parametrize("call", sorted(_CALLS))
    def test_an_undecodable_2xx_body_is_malformed(self, call, body, content_type):
        status, invoke = self._CALLS[call]
        pool_manager = _RecordingPoolManager(status=status, body=body, content_type=content_type)

        with pytest.raises(MalformedResponseError):
            invoke(self._client(pool_manager))
        assert len(pool_manager.calls) == 1

    @pytest.mark.parametrize("body", [b"{not json", b"\xff\xfe"], ids=["invalid-json", "invalid-utf8"])
    def test_an_undecodable_read_inside_a_handled_http_error_is_malformed_not_that_error(self, body):
        """A read issued while an earlier HTTP error is being handled must not inherit it: a 200
        whose body cannot be decoded under a handled 404 is malformed, never absence."""
        pool_manager = _RecordingPoolManager(status=200, body=body)
        client = self._client(pool_manager)

        try:
            raise ApiException(status=404, reason="Not Found")
        except ApiException:
            with pytest.raises(MalformedResponseError):
                client.get_custom_resource_advisory("g", "v1", "widgets", "w", "ns")
        assert len(pool_manager.calls) == 1

    @staticmethod
    def _rest_layer_decodes_before_its_status_check():
        """Some clients (29.x) decode every body in the REST layer before checking the status,
        so an undecodable HTTP error body never becomes an `ApiException` at all."""
        pool_manager = _RecordingPoolManager(status=409, body=b"\xff\xfe")
        try:
            _real_api_client(pool_manager).rest_client.request("GET", "http://fake.invalid/x")
        except UnicodeDecodeError:
            return True
        except ApiException:
            return False
        return False

    @pytest.mark.parametrize("call", ["patch", "create"])
    def test_an_http_error_with_an_undecodable_body_stays_an_api_exception(self, call):
        pool_manager = _RecordingPoolManager(status=409, body=b"\xff\xfe")
        client = self._client(pool_manager)

        if self._rest_layer_decodes_before_its_status_check():
            # The status is lost inside the client, so the outcome is unknown: malformed.
            with pytest.raises(MalformedResponseError):
                self._CALLS[call][1](client)
            return
        with pytest.raises(ApiException) as excinfo:
            self._CALLS[call][1](client)
        assert excinfo.value.status == 409


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

    def test_create_or_patch_configmap_sends_a_typed_configmap_body(self, kube_client, mock_k8s_apis):
        """The create body is a V1ConfigMap whose wire form is the plain ConfigMap document."""
        from kubernetes.client import ApiClient, V1ConfigMap

        mock_k8s_apis["core_api"].create_namespaced_config_map.return_value = MagicMock()

        kube_client.create_or_patch_configmap("ns", "cm1", {"k": "v"})

        body = mock_k8s_apis["core_api"].create_namespaced_config_map.call_args.kwargs["body"]
        assert isinstance(body, V1ConfigMap)
        assert ApiClient().sanitize_for_serialization(body) == {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {"name": "cm1", "namespace": "ns"},
            "data": {"k": "v"},
        }

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

        for sentinel in (
            "raw-config-exception-sentinel",
            context,
            kubeconfig_path,
            token,
            credential,
        ):
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


class _UnreadableBody:
    """A response body whose read fails, as a timeout or reset after the headers would."""

    def __init__(self, exc):
        self._exc = exc

    def read(self, *_args, **_kwargs):
        raise self._exc

    def close(self):
        pass


class _RecordingPoolManager:
    """Stands in for the urllib3 pool manager under a real, installed `ApiClient`.

    Everything above the socket is the installed client's own code: request preparation,
    authentication, path encoding, the REST layer and its HTTP-error handling. Faking here,
    rather than at `call_api` or the REST layer, binds whichever `call_api`/`param_serialize`
    signature the resolved kubernetes client exposes (36.x and 37.x differ) and leaves
    non-2xx classification to that client.
    """

    def __init__(self, status=200, payload=None, body=None, content_type="application/json"):
        import urllib3

        self._urllib3 = urllib3
        self._status = status
        self._body = body if body is not None else json.dumps(payload).encode("utf-8")
        self._content_type = content_type
        self.calls = []

    def request(self, method, url, **kwargs):
        import io

        self.calls.append({"method": method, "url": url, **kwargs})
        stream = self._body if isinstance(self._body, _UnreadableBody) else io.BytesIO(self._body)
        return self._urllib3.HTTPResponse(
            body=stream,
            status=self._status,
            reason="Fake",
            headers={"Content-Type": self._content_type},
            preload_content=False,
        )


def _real_api_client(pool_manager):
    """A real `ApiClient` of the installed version, with a bearer token and a faked socket."""
    from kubernetes.client import ApiClient, Configuration

    configuration = Configuration()
    # The key `load_kube_config` populates from a kubeconfig token: `BearerToken` from 36.x,
    # `authorization` before it.
    configuration.api_key = {"BearerToken": "Bearer binding-test-token", "authorization": "Bearer binding-test-token"}
    api_client = ApiClient(configuration=configuration)
    api_client.rest_client.pool_manager = pool_manager
    return api_client


def _real_kube_client(pool_manager):
    """A KubeClient whose raw transport and generated custom-object API both run the installed
    client unmocked down to the faked socket."""
    from kubernetes.client import CustomObjectsApi

    client = KubeClient.__new__(KubeClient)
    client.request_timeout = 30
    client.dry_run = False
    client._api_client = _real_api_client(pool_manager)
    client.custom_api = CustomObjectsApi(client._api_client)
    return client


# A 2xx body no form factor may read as an answer: not JSON, not UTF-8, not an object, or of
# a content type kubernetes 37 does not deserialize.
_MALFORMED_2XX_BODIES = [
    pytest.param(b"null", "application/json", id="null"),
    pytest.param(b"[]", "application/json", id="list"),
    pytest.param(b'"Restore"', "application/json", id="string"),
    pytest.param(b"{not json", "application/json", id="invalid-json"),
    pytest.param(b"\xff\xfe", "application/json", id="invalid-utf8"),
    pytest.param(b"null", "application/octet-stream", id="unsupported-content-type"),
]


# The three non-standard constants Python's json module accepts by default. None is JSON, so a
# body carrying one is not a JSON object even when every other member is well formed.
_NON_JSON_CONSTANTS = ["NaN", "Infinity", "-Infinity"]


def _with_constant(payload, constant):
    """`payload` serialized as JSON with one extra member whose value is `constant`."""
    encoded = json.dumps(payload)
    return (encoded[:-1] + f', "extra": {constant}}}').encode("utf-8")


class TestDiscoveryProver:
    """R4-03: kind absence must come from a successful discovery response."""

    @staticmethod
    def _body(payload):
        """A raw client response: the prover decodes the body itself, so fixtures carry bytes.

        Returning a decoded mapping here would mock away the decode step and hide a client
        signature change, which is how the `response_type`/`response_types_map` rename slipped
        past this class once already.
        """
        data = payload if isinstance(payload, (bytes, str)) else json.dumps(payload)
        return Mock(data=data.encode("utf-8") if isinstance(data, str) else data)

    def _client(self, call_api):
        client = KubeClient.__new__(KubeClient)
        client.request_timeout = 30
        client.dry_run = False
        # The pre-37 client shape: no `param_serialize`, so requests take the legacy `call_api` path.
        client._api_client = Mock(spec=["call_api"])
        client._api_client.call_api = call_api
        return client

    def test_served_kind_returns_items(self):
        call = Mock(
            return_value=self._body(
                {
                    "kind": "APIResourceList",
                    "groupVersion": "operator.open-cluster-management.io/v1",
                    "resources": [{"name": "multiclusterhubs", "kind": "MultiClusterHub"}],
                }
            )
        )
        client = self._client(call)
        outcome = client._discovery_serves("operator.open-cluster-management.io", "v1", "multiclusterhubs")
        assert outcome.status is StrictReadStatus.ITEMS

    def test_absent_kind_in_a_successful_response_is_crd_absent(self):
        call = Mock(
            return_value=self._body(
                {
                    "kind": "APIResourceList",
                    "groupVersion": "g/v1",
                    "resources": [{"name": "somethingelse", "kind": "SomethingElse"}],
                }
            )
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
        call = Mock(return_value=self._body("<html>gateway error</html>"))
        assert self._client(call)._discovery_serves("g", "v1", "p").status is StrictReadStatus.ERROR

    def test_missing_resources_key_is_error_not_absence(self):
        call = Mock(return_value=self._body({"kind": "APIResourceList", "groupVersion": "g/v1"}))
        assert self._client(call)._discovery_serves("g", "v1", "p").status is StrictReadStatus.ERROR

    def test_discovery_404_is_error_not_absence(self):
        call = Mock(side_effect=ApiException(status=404, reason="Not Found"))
        outcome = self._client(call)._discovery_serves("g", "v1", "p")
        assert outcome.status is StrictReadStatus.ERROR

    def test_discovery_decode_failure_is_error_not_absence(self):
        call = Mock(side_effect=ValueError("invalid json"))
        assert self._client(call)._discovery_serves("g", "v1", "p").status is StrictReadStatus.ERROR

    def test_malformed_api_resource_list_is_error_not_absence(self):
        call = Mock(
            return_value=self._body({"kind": "APIResourceList", "groupVersion": "g/v1", "resources": [{"name": 7}]})
        )
        assert self._client(call)._discovery_serves("g", "v1", "p").status is StrictReadStatus.ERROR

    @pytest.mark.parametrize(
        "resources",
        [
            [{"name": "p", "kind": "P"}, {"name": 7}],
            [{"name": 7}, {"name": "p", "kind": "P"}],
        ],
        ids=["malformed_after_match", "malformed_before_match"],
    )
    def test_a_malformed_entry_anywhere_is_error_whatever_the_entry_order(self, resources):
        """The whole document is validated before any verdict is returned.

        Deciding on the first matching entry makes one response mean `ITEMS` or `ERROR`
        depending only on the order the server happened to serve its entries in, which no
        parity vector can pin down. Absence was never order-sensitive — it already requires
        the full list to validate — so only the served verdict needed closing.
        """
        call = Mock(
            return_value=self._body({"kind": "APIResourceList", "groupVersion": "g/v1", "resources": resources})
        )
        outcome = self._client(call)._discovery_serves("g", "v1", "p")
        assert outcome.status is StrictReadStatus.ERROR
        assert outcome.reason == STRICT_READ_REASON_DISCOVERY_UNVERIFIABLE

    def test_irregular_plural_is_matched_by_exact_resource_name(self):
        call = Mock(
            return_value=self._body(
                {
                    "kind": "APIResourceList",
                    "groupVersion": "observability.open-cluster-management.io/v1beta2",
                    "resources": [
                        {
                            "name": "multiclusterobservabilities",
                            "kind": "MultiClusterObservability",
                        },
                    ],
                }
            )
        )
        outcome = self._client(call)._discovery_serves(
            "observability.open-cluster-management.io",
            "v1beta2",
            "multiclusterobservabilities",
        )
        assert outcome.status is StrictReadStatus.ITEMS

    _SERVED_PODS = {"kind": "APIResourceList", "groupVersion": "v1", "resources": [{"name": "pods", "kind": "Pod"}]}

    @staticmethod
    def _real_client(pool_manager):
        client = KubeClient.__new__(KubeClient)
        client.request_timeout = 30
        client.dry_run = False
        client._api_client = _real_api_client(pool_manager)
        return client

    def test_discovery_call_binds_the_real_api_client_signature(self):
        """The prover must issue a call the installed client actually accepts.

        Every other test in this class replaces `call_api` with a `Mock`, which accepts any
        keyword, so none of them can prove the call is issuable. The transport API changed
        inside the supported dependency range: the deserialization keyword was renamed
        (`response_type` on 28.x/31.x, `response_types_map` on 36.x), and 37.x split request
        preparation (`param_serialize`) from a `call_api` that rejects the older keywords.
        Plan section 9.2 requires the discovery mechanism to be version-invariant. This test
        drives a real `ApiClient` with only its socket faked, so a call the resolved client
        rejects fails here instead of silently degrading every custom-resource strict read to
        `discovery_unverifiable` against a live cluster.
        """
        pool_manager = _RecordingPoolManager(payload=self._SERVED_PODS)

        outcome = self._real_client(pool_manager)._discovery_serves("", "v1", "pods")

        assert outcome.status is StrictReadStatus.ITEMS
        [call] = pool_manager.calls
        assert call["method"] == "GET"
        assert call["url"].endswith("/api/v1")
        assert {key.lower(): value for key, value in call["headers"].items()}["authorization"] == (
            "Bearer binding-test-token"
        )
        assert call["timeout"].total == 30

    @pytest.mark.parametrize("status", [401, 403, 404, 500, 503])
    def test_a_real_client_http_error_is_unverifiable_never_absence(self, status):
        pool_manager = _RecordingPoolManager(status=status, payload=self._SERVED_PODS)

        outcome = self._real_client(pool_manager)._discovery_serves("", "v1", "pods")

        assert outcome.status is StrictReadStatus.ERROR
        assert outcome.reason == STRICT_READ_REASON_DISCOVERY_UNVERIFIABLE
        assert len(pool_manager.calls) == 1

    @pytest.mark.parametrize("exc", [ReadTimeoutError(None, "/api/v1", "read timed out"), OSError("reset")])
    def test_a_real_client_body_read_failure_is_unverifiable(self, exc):
        pool_manager = _RecordingPoolManager(body=_UnreadableBody(exc))

        outcome = self._real_client(pool_manager)._discovery_serves("", "v1", "pods")

        assert outcome.status is StrictReadStatus.ERROR
        assert outcome.reason == STRICT_READ_REASON_DISCOVERY_UNVERIFIABLE
        assert len(pool_manager.calls) == 1

    def test_a_real_client_undecodable_body_is_unverifiable(self):
        pool_manager = _RecordingPoolManager(body=b"<html>")

        outcome = self._real_client(pool_manager)._discovery_serves("", "v1", "pods")

        assert outcome.status is StrictReadStatus.ERROR
        assert outcome.reason == STRICT_READ_REASON_DISCOVERY_UNVERIFIABLE
        assert len(pool_manager.calls) == 1

    def test_core_group_uses_the_core_discovery_path(self):
        call = Mock(
            return_value=self._body(
                {
                    "kind": "APIResourceList",
                    "groupVersion": "v1",
                    "resources": [{"name": "pods", "kind": "Pod"}],
                }
            )
        )
        self._client(call)._discovery_serves("", "v1", "pods")
        assert call.call_args[0][0] == "/api/v1"


class TestStrictCustomResourceReads:
    """R4-03 July §3 outcome algebra."""

    def _client(self, list_pages=None, discovery_served=True, get_result=None, get_error=None):
        client = KubeClient.__new__(KubeClient)
        client.request_timeout = 30
        client.dry_run = False
        client.custom_api = Mock()
        client._discovery_serves = Mock(
            return_value=(
                StrictReadOutcome.from_items([])
                if discovery_served
                else StrictReadOutcome.crd_absent(STRICT_READ_REASON_KIND_NOT_SERVED)
            )
        )
        if list_pages is not None:
            client.custom_api.list_cluster_custom_object = Mock(side_effect=list_pages)
        if get_result is not None or get_error is not None:
            client.custom_api.get_cluster_custom_object = Mock(return_value=get_result, side_effect=get_error)
        return client

    def test_true_empty_list_is_a_proven_complete_inventory(self):
        client = self._client(list_pages=[{"items": [], "metadata": {"resourceVersion": "100"}}])
        outcome = client.list_custom_resources_strict("g", "v1", "widgets")
        assert outcome.status is StrictReadStatus.ITEMS
        assert outcome.items == []

    def test_complete_multi_page_inventory_is_joined(self):
        pages = [
            {
                "items": [{"metadata": {"name": "a"}}],
                "metadata": {"continue": "tok", "resourceVersion": "100"},
            },
            {
                "items": [{"metadata": {"name": "b"}}],
                "metadata": {"resourceVersion": "100"},
            },
        ]
        outcome = self._client(list_pages=pages).list_custom_resources_strict("g", "v1", "widgets")
        assert [i["metadata"]["name"] for i in outcome.items] == ["a", "b"]

    def test_every_page_request_carries_the_fixed_page_limit(self):
        pages = [
            {
                "items": [{"metadata": {"name": "a"}}],
                "metadata": {"continue": "tok", "resourceVersion": "100"},
            },
            {
                "items": [{"metadata": {"name": "b"}}],
                "metadata": {"resourceVersion": "100"},
            },
        ]
        client = self._client(list_pages=pages)
        client.list_custom_resources_strict("g", "v1", "widgets")
        calls = client.custom_api.list_cluster_custom_object.call_args_list
        assert len(calls) == 2
        assert [call.kwargs["limit"] for call in calls] == [
            STRICT_READ_PAGE_LIMIT,
            STRICT_READ_PAGE_LIMIT,
        ]

    def test_every_page_request_is_bounded_and_follows_the_continuation(self):
        pages = [
            {"items": [], "metadata": {"continue": "tok", "resourceVersion": "100"}},
            {"items": [], "metadata": {"resourceVersion": "100"}},
        ]
        client = self._client(list_pages=pages)
        client.list_custom_resources_strict("g", "v1", "widgets")
        calls = client.custom_api.list_cluster_custom_object.call_args_list
        assert [call.kwargs["_continue"] for call in calls] == [None, "tok"]
        assert all(call.kwargs["_request_timeout"] == 30 for call in calls)

    def test_later_page_failure_fails_the_whole_read(self):
        pages = [
            {
                "items": [{"metadata": {"name": "a"}}],
                "metadata": {"continue": "tok", "resourceVersion": "100"},
            },
            ApiException(status=500, reason="Internal Server Error"),
        ]
        outcome = self._client(list_pages=pages).list_custom_resources_strict("g", "v1", "widgets")
        assert outcome.status is StrictReadStatus.ERROR
        assert outcome.items == []

    def test_outstanding_continuation_at_exit_is_incomplete(self):
        # A server that keeps returning a continue token must not be reported as complete.
        pages = [{"items": [], "metadata": {"continue": "tok", "resourceVersion": "100"}}] * (STRICT_READ_MAX_PAGES + 5)
        outcome = self._client(list_pages=pages).list_custom_resources_strict("g", "v1", "widgets")
        assert outcome.status is StrictReadStatus.ERROR
        assert outcome.reason == STRICT_READ_REASON_INVENTORY_INCOMPLETE

    def test_expired_continue_token_restarts_the_whole_read_once(self):
        pages = [
            {
                "items": [{"metadata": {"name": "a"}}],
                "metadata": {"continue": "tok", "resourceVersion": "100"},
            },
            ApiException(status=410, reason="Gone"),
            {
                "items": [{"metadata": {"name": "a"}}],
                "metadata": {"continue": "tok", "resourceVersion": "200"},
            },
            {
                "items": [{"metadata": {"name": "b"}}],
                "metadata": {"resourceVersion": "200"},
            },
        ]
        client = self._client(list_pages=pages)
        outcome = client.list_custom_resources_strict("g", "v1", "widgets")
        assert outcome.status is StrictReadStatus.ITEMS
        # The prefix collected before the 410 is discarded, not carried into the restart.
        assert [i["metadata"]["name"] for i in outcome.items] == ["a", "b"]
        restarted = client.custom_api.list_cluster_custom_object.call_args_list[2]
        assert restarted.kwargs["_continue"] is None

    def test_a_restarted_read_reports_the_restarted_snapshot_revision(self):
        """A3.0 rule 8: the expired read's revision is discarded with its prefix."""
        pages = [
            {
                "items": [{"metadata": {"name": "a"}}],
                "metadata": {"continue": "tok", "resourceVersion": "100"},
            },
            ApiException(status=410, reason="Gone"),
            {
                "items": [{"metadata": {"name": "a"}}],
                "metadata": {"continue": "tok", "resourceVersion": "200"},
            },
            {
                "items": [{"metadata": {"name": "b"}}],
                "metadata": {"resourceVersion": "200"},
            },
        ]
        outcome = self._client(list_pages=pages).list_custom_resources_strict("g", "v1", "widgets")
        assert outcome.resource_version == "200", "the restarted read's page 1 owns the snapshot"

    def test_a_coherent_multi_page_read_publishes_its_one_snapshot_revision(self):
        """A3.0 rule 8: every normal continuation page is served at page 1's revision."""
        pages = [
            {
                "items": [{"metadata": {"name": "a"}}],
                "metadata": {"continue": "tok", "resourceVersion": "100"},
            },
            {
                "items": [{"metadata": {"name": "b"}}],
                "metadata": {"resourceVersion": "100"},
            },
        ]
        outcome = self._client(list_pages=pages).list_custom_resources_strict("g", "v1", "widgets")
        assert outcome.status is StrictReadStatus.ITEMS
        assert outcome.resource_version == "100"

    def test_a_continuation_page_at_a_different_revision_is_malformed(self):
        """A3.0 rule 8, negative: mismatched pages are not one snapshot, so the read fails."""
        pages = [
            {
                "items": [{"metadata": {"name": "a"}}],
                "metadata": {"continue": "tok", "resourceVersion": "100"},
            },
            {
                "items": [{"metadata": {"name": "b"}}],
                "metadata": {"resourceVersion": "999"},
            },
        ]
        outcome = self._client(list_pages=pages).list_custom_resources_strict("g", "v1", "widgets")
        assert outcome.status is StrictReadStatus.ERROR
        assert outcome.reason == STRICT_READ_REASON_MALFORMED_RESPONSE
        assert outcome.items == []
        assert outcome.resource_version is None

    def test_a_restarted_read_with_an_inconsistent_continuation_is_malformed(self):
        """The restarted read establishes a new snapshot, and its pages must agree with it."""
        pages = [
            {
                "items": [{"metadata": {"name": "a"}}],
                "metadata": {"continue": "tok", "resourceVersion": "100"},
            },
            ApiException(status=410, reason="Gone"),
            {
                "items": [{"metadata": {"name": "a"}}],
                "metadata": {"continue": "tok", "resourceVersion": "200"},
            },
            {
                "items": [{"metadata": {"name": "b"}}],
                "metadata": {"resourceVersion": "100"},
            },
        ]
        outcome = self._client(list_pages=pages).list_custom_resources_strict("g", "v1", "widgets")
        assert outcome.status is StrictReadStatus.ERROR
        assert outcome.reason == STRICT_READ_REASON_MALFORMED_RESPONSE
        assert outcome.resource_version is None

    def test_a_page_without_a_readable_revision_is_malformed(self):
        pages = [{"items": [], "metadata": {}}]
        outcome = self._client(list_pages=pages).list_custom_resources_strict("g", "v1", "widgets")
        assert outcome.status is StrictReadStatus.ERROR
        assert outcome.resource_version is None

    def test_second_expired_continue_token_fails_closed(self):
        pages = [
            {
                "items": [{"metadata": {"name": "a"}}],
                "metadata": {"continue": "tok", "resourceVersion": "100"},
            },
            ApiException(status=410, reason="Gone"),
            {
                "items": [{"metadata": {"name": "a"}}],
                "metadata": {"continue": "tok", "resourceVersion": "200"},
            },
            ApiException(status=410, reason="Gone"),
        ]
        outcome = self._client(list_pages=pages).list_custom_resources_strict("g", "v1", "widgets")
        assert outcome.status is StrictReadStatus.ERROR
        assert outcome.items == []

    def test_malformed_items_is_error_not_empty(self):
        outcome = self._client(list_pages=[{"items": "nope", "metadata": {}}]).list_custom_resources_strict(
            "g", "v1", "widgets"
        )
        assert outcome.status is StrictReadStatus.ERROR
        assert outcome.reason == STRICT_READ_REASON_MALFORMED_RESPONSE

    def test_missing_items_key_is_error_not_empty(self):
        outcome = self._client(list_pages=[{"metadata": {}}]).list_custom_resources_strict("g", "v1", "widgets")
        assert outcome.status is StrictReadStatus.ERROR
        assert outcome.reason == STRICT_READ_REASON_MALFORMED_RESPONSE

    def test_null_items_is_error_not_empty(self):
        outcome = self._client(list_pages=[{"items": None, "metadata": {}}]).list_custom_resources_strict(
            "g", "v1", "widgets"
        )
        assert outcome.status is StrictReadStatus.ERROR

    def test_malformed_list_metadata_is_error(self):
        outcome = self._client(list_pages=[{"items": [], "metadata": "nope"}]).list_custom_resources_strict(
            "g", "v1", "widgets"
        )
        assert outcome.status is StrictReadStatus.ERROR
        assert outcome.reason == STRICT_READ_REASON_MALFORMED_RESPONSE

    def test_non_mapping_member_is_error_not_empty(self):
        outcome = self._client(list_pages=[{"items": ["x"], "metadata": {}}]).list_custom_resources_strict(
            "g", "v1", "widgets"
        )
        assert outcome.status is StrictReadStatus.ERROR

    def test_authorization_failure_is_error_not_absence(self):
        outcome = self._client(list_pages=[ApiException(status=403, reason="Forbidden")]).list_custom_resources_strict(
            "g", "v1", "widgets"
        )
        assert outcome.status is StrictReadStatus.ERROR
        assert outcome.proves_absence is False

    def test_list_404_on_a_served_kind_is_error_not_absence(self):
        outcome = self._client(list_pages=[ApiException(status=404, reason="Not Found")]).list_custom_resources_strict(
            "g", "v1", "widgets"
        )
        assert outcome.status is StrictReadStatus.ERROR

    def test_unserved_kind_short_circuits_to_crd_absent(self):
        client = self._client(list_pages=[], discovery_served=False)
        outcome = client.list_custom_resources_strict("g", "v1", "widgets")
        assert outcome.status is StrictReadStatus.CRD_ABSENT
        client.custom_api.list_cluster_custom_object.assert_not_called()

    def test_named_get_returns_the_resource_and_its_resource_version(self):
        resource = {"metadata": {"name": "mch", "uid": "u-1", "resourceVersion": "77"}}
        outcome = self._client(get_result=resource).get_custom_resource_strict("g", "v1", "widgets", "mch")
        assert outcome.status is StrictReadStatus.ITEMS
        assert outcome.resource is resource
        assert outcome.resource_version == "77"

    def test_named_get_takes_a_caller_deadline_as_its_request_timeout(self):
        """R4-04 Task 10: a poll's strict read is capped to the remaining poll budget."""
        resource = {"metadata": {"name": "mch", "uid": "u-1", "resourceVersion": "77"}}
        client = self._client(get_result=resource)
        client.get_custom_resource_strict("g", "v1", "widgets", "mch", request_timeout=7)
        assert client.custom_api.get_cluster_custom_object.call_args.kwargs["_request_timeout"] == 7
        client.get_custom_resource_strict("g", "v1", "widgets", "mch")
        assert (
            client.custom_api.get_cluster_custom_object.call_args.kwargs["_request_timeout"] == client.request_timeout
        )

    @pytest.mark.parametrize(
        "metadata",
        [
            {"name": "mch", "uid": "u-1"},  # revision missing
            {"name": "mch", "uid": "u-1", "resourceVersion": ""},  # revision empty
            {
                "name": "mch",
                "uid": "u-1",
                "resourceVersion": 77,
            },  # revision not a string
        ],
        ids=["missing", "empty", "non_string"],
    )
    def test_named_get_without_a_usable_revision_is_malformed(self, metadata):
        """A3.0 rule 9: a successful named GET may never publish a null revision."""
        outcome = self._client(get_result={"metadata": metadata}).get_custom_resource_strict(
            "g", "v1", "widgets", "mch"
        )
        assert outcome.status is StrictReadStatus.ERROR
        assert outcome.reason == STRICT_READ_REASON_MALFORMED_RESPONSE
        assert outcome.resource_version is None

    def test_named_get_is_bounded(self):
        client = self._client(get_result={"metadata": {"name": "mch", "resourceVersion": "77"}})
        client.get_custom_resource_strict("g", "v1", "widgets", "mch")
        assert client.custom_api.get_cluster_custom_object.call_args.kwargs["_request_timeout"] == 30

    def test_named_get_404_after_successful_discovery_is_object_absent(self):
        outcome = self._client(get_error=ApiException(status=404, reason="Not Found")).get_custom_resource_strict(
            "g", "v1", "widgets", "mch"
        )
        assert outcome.status is StrictReadStatus.OBJECT_ABSENT

    def test_named_get_404_without_successful_discovery_is_never_object_absent(self):
        client = self._client(
            get_error=ApiException(status=404, reason="Not Found"),
            discovery_served=False,
        )
        outcome = client.get_custom_resource_strict("g", "v1", "widgets", "mch")
        assert outcome.status is StrictReadStatus.CRD_ABSENT

    def test_strict_surface_offers_no_truncation(self):
        for method in (
            KubeClient.list_custom_resources_strict,
            KubeClient.list_pods_strict,
        ):
            assert "max_items" not in inspect.signature(method).parameters

    def test_legacy_readers_are_unchanged(self):
        # The strict surface is additive; existing callers keep the current behavior.
        assert KubeClient.list_custom_resources.__doc__ is not None
        assert "max_items" in inspect.signature(KubeClient.list_custom_resources).parameters


@pytest.mark.unit
class TestListManagedClustersStrict:
    """R4-03 PR D: plan-named ManagedCluster strict inventory wrapper."""

    def _client(self, list_pages=None, discovery_served=True):
        client = KubeClient.__new__(KubeClient)
        client.request_timeout = 30
        client.dry_run = False
        client.custom_api = Mock()
        client._discovery_serves = Mock(
            return_value=(
                StrictReadOutcome.from_items([])
                if discovery_served
                else StrictReadOutcome.crd_absent(STRICT_READ_REASON_KIND_NOT_SERVED)
            )
        )
        if list_pages is not None:
            client.custom_api.list_cluster_custom_object = Mock(side_effect=list_pages)
        return client

    def test_wrapper_targets_managedcluster_constants(self):
        client = self._client(list_pages=[{"items": [], "metadata": {"resourceVersion": "1"}}])
        client.list_managed_clusters_strict()
        client._discovery_serves.assert_called_once_with(
            MANAGED_CLUSTER_API_GROUP,
            MANAGED_CLUSTER_API_VERSION,
            MANAGED_CLUSTER_PLURAL,
        )
        client.custom_api.list_cluster_custom_object.assert_called_once()
        kwargs = client.custom_api.list_cluster_custom_object.call_args.kwargs
        assert kwargs["group"] == MANAGED_CLUSTER_API_GROUP
        assert kwargs["version"] == MANAGED_CLUSTER_API_VERSION
        assert kwargs["plural"] == MANAGED_CLUSTER_PLURAL

    def test_proven_empty_inventory_is_items_not_absence(self):
        outcome = self._client(
            list_pages=[{"items": [], "metadata": {"resourceVersion": "1"}}]
        ).list_managed_clusters_strict()
        assert outcome.status is StrictReadStatus.ITEMS
        assert outcome.items == []
        assert outcome.proves_absence is False

    def test_populated_inventory_returns_items(self):
        pages = [
            {
                "items": [{"metadata": {"name": "spoke-a"}}],
                "metadata": {"resourceVersion": "9"},
            }
        ]
        outcome = self._client(list_pages=pages).list_managed_clusters_strict()
        assert outcome.status is StrictReadStatus.ITEMS
        assert [item["metadata"]["name"] for item in outcome.items] == ["spoke-a"]

    def test_unserved_kind_is_crd_absent_never_empty_items(self):
        client = self._client(list_pages=[], discovery_served=False)
        outcome = client.list_managed_clusters_strict()
        assert outcome.status is StrictReadStatus.CRD_ABSENT
        assert outcome.proves_absence is True
        client.custom_api.list_cluster_custom_object.assert_not_called()

    def test_authorization_failure_is_error_not_empty(self):
        outcome = self._client(list_pages=[ApiException(status=403, reason="Forbidden")]).list_managed_clusters_strict()
        assert outcome.status is StrictReadStatus.ERROR
        assert outcome.proves_absence is False

    def test_later_page_failure_is_error_not_partial_or_empty(self):
        pages = [
            {
                "items": [{"metadata": {"name": "spoke-a"}}],
                "metadata": {"continue": "tok", "resourceVersion": "9"},
            },
            ApiException(status=500, reason="Internal Error"),
        ]
        outcome = self._client(list_pages=pages).list_managed_clusters_strict()
        assert outcome.status is StrictReadStatus.ERROR
        # ERROR outcomes carry the empty default list; callers must not treat it as inventory.
        assert outcome.items == []
        assert outcome.proves_absence is False

    def test_wrapper_offers_no_truncation_parameter(self):
        assert "max_items" not in inspect.signature(KubeClient.list_managed_clusters_strict).parameters


class TestStrictCoreReads:
    """R4-03: live namespace absence and complete Pod inventory."""

    def _client(self, namespace_result=None, namespace_error=None, pod_pages=None):
        client = KubeClient.__new__(KubeClient)
        client.request_timeout = 30
        client.dry_run = False
        client.core_v1 = Mock()
        if namespace_result is not None or namespace_error is not None:
            client.core_v1.read_namespace = Mock(return_value=namespace_result, side_effect=namespace_error)
        if pod_pages is not None:
            client.core_v1.list_namespaced_pod = Mock(side_effect=pod_pages)
        return client

    @staticmethod
    def _namespace(name, resource_version="12"):
        """Build a V1Namespace-shaped mock.

        `name` cannot be passed as a Mock keyword: `Mock(name=...)` names the
        mock rather than setting the attribute, so it is assigned explicitly.
        """
        namespace = Mock()
        namespace.metadata = Mock(resource_version=resource_version)
        namespace.metadata.name = name
        return namespace

    @staticmethod
    def _pod_page(names, continue_token=None, resource_version="12"):
        """Build a V1PodList-shaped page: attribute access, not dict access."""
        pods = []
        for pod_name in names:
            pod = Mock()
            pod.to_dict = Mock(return_value={"metadata": {"name": pod_name, "owner_references": []}})
            pods.append(pod)
        page = Mock()
        page.items = pods
        page.metadata = Mock(_continue=continue_token, resource_version=resource_version)
        return page

    # --- get_namespace_strict -------------------------------------------------
    def test_present_namespace_is_items(self):
        outcome = self._client(namespace_result=self._namespace("acm")).get_namespace_strict("acm")
        assert outcome.status is StrictReadStatus.ITEMS
        assert outcome.resource_version == "12"

    def test_namespace_get_404_is_namespace_absent(self):
        outcome = self._client(namespace_error=ApiException(status=404, reason="Not Found")).get_namespace_strict("acm")
        assert outcome.status is StrictReadStatus.NAMESPACE_ABSENT
        assert outcome.reason == STRICT_READ_REASON_NAMESPACE_NOT_FOUND

    @pytest.mark.parametrize("status", [401, 403, 500, 503])
    def test_namespace_get_failure_is_error_not_absence(self, status):
        outcome = self._client(namespace_error=ApiException(status=status, reason="failed")).get_namespace_strict("acm")
        assert outcome.status is StrictReadStatus.ERROR
        assert outcome.proves_absence is False

    def test_namespace_transport_failure_is_error_not_absence(self):
        outcome = self._client(namespace_error=TimeoutError("deadline")).get_namespace_strict("acm")
        assert outcome.status is StrictReadStatus.ERROR

    def test_namespace_with_a_different_returned_name_is_error(self):
        outcome = self._client(namespace_result=self._namespace("somewhere-else")).get_namespace_strict("acm")
        assert outcome.status is StrictReadStatus.ERROR

    def test_namespace_get_is_bounded(self):
        client = self._client(namespace_result=self._namespace("acm"))
        client.get_namespace_strict("acm")
        assert client.core_v1.read_namespace.call_args.kwargs["_request_timeout"] == 30

    @pytest.mark.parametrize("revision", [None, "", 12], ids=["missing", "empty", "non_string"])
    def test_namespace_get_without_a_usable_revision_is_malformed(self, revision):
        """A3.0 rule 9: `resource_versions["drain_namespace"]` has no null-revision source."""
        client = self._client(namespace_result=self._namespace("acm", resource_version=revision))
        outcome = client.get_namespace_strict("acm")
        assert outcome.status is StrictReadStatus.ERROR
        assert outcome.reason == STRICT_READ_REASON_MALFORMED_RESPONSE
        assert outcome.resource_version is None

    @pytest.mark.parametrize("invalid", ["not.a.namespace", "acm.open-cluster-management"])
    def test_a_dotted_namespace_is_rejected_before_the_read(self, invalid):
        """A namespace is a DNS-1123 *label*, so a dotted name is invalid input, not an absence.

        `get_namespace` and `list_pods_strict` both validate namespaces as labels. Validating
        this argument as a generic resource name instead accepts dots, sends the read, and lets
        a 404 on an impossible name be published as a positive `NAMESPACE_ABSENT` proof.
        """
        from lib.validation import ValidationError

        client = self._client(namespace_result=self._namespace("acm"))
        with pytest.raises(ValidationError):
            client.get_namespace_strict(invalid)
        client.core_v1.read_namespace.assert_not_called()

    # --- list_pods_strict -----------------------------------------------------
    def test_zero_pods_is_a_proven_empty_inventory(self):
        outcome = self._client(pod_pages=[self._pod_page([])]).list_pods_strict("acm")
        assert outcome.status is StrictReadStatus.ITEMS
        assert outcome.items == []
        assert outcome.resource_version == "12"

    def test_complete_multi_page_pod_inventory_is_joined_and_bounded(self):
        pages = [self._pod_page(["a"], continue_token="tok"), self._pod_page(["b"])]
        client = self._client(pod_pages=pages)
        outcome = client.list_pods_strict("acm")
        assert [p["metadata"]["name"] for p in outcome.items] == ["a", "b"]
        calls = client.core_v1.list_namespaced_pod.call_args_list
        assert [call.kwargs["limit"] for call in calls] == [STRICT_READ_PAGE_LIMIT] * 2
        assert [call.kwargs["_continue"] for call in calls] == [None, "tok"]
        assert all(call.kwargs["_request_timeout"] == 30 for call in calls)

    def test_pod_later_page_failure_exposes_no_partial_inventory(self):
        pages = [
            self._pod_page(["a"], continue_token="tok"),
            ApiException(status=500, reason="boom"),
        ]
        outcome = self._client(pod_pages=pages).list_pods_strict("acm")
        assert outcome.status is StrictReadStatus.ERROR
        assert outcome.items == []

    def test_pod_expired_continuation_restarts_the_whole_read_once(self):
        pages = [
            self._pod_page(["a"], continue_token="tok"),
            ApiException(status=410, reason="Gone"),
            self._pod_page(["a"], continue_token="tok"),
            self._pod_page(["b"]),
        ]
        client = self._client(pod_pages=pages)
        outcome = client.list_pods_strict("acm")
        assert [p["metadata"]["name"] for p in outcome.items] == ["a", "b"]
        assert client.core_v1.list_namespaced_pod.call_args_list[2].kwargs["_continue"] is None

    def test_pod_snapshot_revision_is_page_one_and_survives_no_restart(self):
        """A3.0 rule 8, for the read whose revision becomes `resource_versions["drain_pods"]`."""
        pages = [
            self._pod_page(["a"], continue_token="tok", resource_version="100"),
            self._pod_page(["b"], resource_version="100"),
        ]
        outcome = self._client(pod_pages=pages).list_pods_strict("acm")
        assert outcome.status is StrictReadStatus.ITEMS
        assert outcome.resource_version == "100"

    def test_a_pod_continuation_page_at_a_different_revision_is_malformed(self):
        """A3.0 rule 8, negative, on the drain read that produces final-proof evidence."""
        pages = [
            self._pod_page(["a"], continue_token="tok", resource_version="100"),
            self._pod_page(["b"], resource_version="999"),
        ]
        outcome = self._client(pod_pages=pages).list_pods_strict("acm")
        assert outcome.status is StrictReadStatus.ERROR
        assert outcome.reason == STRICT_READ_REASON_MALFORMED_RESPONSE
        assert outcome.items == []
        assert outcome.resource_version is None

    def test_pod_restarted_read_discards_the_expired_snapshot_revision(self):
        pages = [
            self._pod_page(["a"], continue_token="tok", resource_version="100"),
            ApiException(status=410, reason="Gone"),
            self._pod_page(["a"], continue_token="tok", resource_version="200"),
            self._pod_page(["b"], resource_version="200"),
        ]
        outcome = self._client(pod_pages=pages).list_pods_strict("acm")
        assert outcome.resource_version == "200"

    def test_a_failed_pod_read_carries_no_revision(self):
        pages = [
            self._pod_page(["a"], continue_token="tok"),
            ApiException(status=500, reason="boom"),
        ]
        outcome = self._client(pod_pages=pages).list_pods_strict("acm")
        assert outcome.status is StrictReadStatus.ERROR
        assert outcome.resource_version is None

    def test_pod_second_expiry_fails_closed(self):
        pages = [
            self._pod_page(["a"], continue_token="tok"),
            ApiException(status=410, reason="Gone"),
            self._pod_page(["a"], continue_token="tok"),
            ApiException(status=410, reason="Gone"),
        ]
        outcome = self._client(pod_pages=pages).list_pods_strict("acm")
        assert outcome.status is StrictReadStatus.ERROR

    def test_pod_outstanding_continuation_at_exit_is_incomplete(self):
        pages = [self._pod_page([], continue_token="tok")] * (STRICT_READ_MAX_PAGES + 5)
        outcome = self._client(pod_pages=pages).list_pods_strict("acm")
        assert outcome.status is StrictReadStatus.ERROR
        assert outcome.reason == STRICT_READ_REASON_INVENTORY_INCOMPLETE

    def test_pod_page_without_items_is_error_not_empty(self):
        page = Mock()
        page.items = None
        page.metadata = Mock(_continue=None, resource_version="12")
        outcome = self._client(pod_pages=[page]).list_pods_strict("acm")
        assert outcome.status is StrictReadStatus.ERROR
        assert outcome.reason == STRICT_READ_REASON_MALFORMED_RESPONSE

    def test_pod_page_with_non_list_items_is_error(self):
        page = Mock()
        page.items = "nope"
        page.metadata = Mock(_continue=None, resource_version="12")
        outcome = self._client(pod_pages=[page]).list_pods_strict("acm")
        assert outcome.status is StrictReadStatus.ERROR

    def test_malformed_pod_member_is_error(self):
        page = Mock()
        page.items = ["not-a-pod"]
        page.metadata = Mock(_continue=None, resource_version="12")
        outcome = self._client(pod_pages=[page]).list_pods_strict("acm")
        assert outcome.status is StrictReadStatus.ERROR

    def test_pod_page_without_list_metadata_is_error(self):
        page = Mock()
        page.items = []
        page.metadata = None
        outcome = self._client(pod_pages=[page]).list_pods_strict("acm")
        assert outcome.status is StrictReadStatus.ERROR

    def test_pod_list_404_with_a_present_namespace_is_error(self):
        client = self._client(
            pod_pages=[ApiException(status=404, reason="Not Found")],
            namespace_result=self._namespace("acm"),
        )
        outcome = client.list_pods_strict("acm")
        assert outcome.status is StrictReadStatus.ERROR

    def test_pod_list_404_with_a_fresh_namespace_404_is_namespace_absent(self):
        client = self._client(
            pod_pages=[ApiException(status=404, reason="Not Found")],
            namespace_error=ApiException(status=404, reason="Not Found"),
        )
        outcome = client.list_pods_strict("acm")
        assert outcome.status is StrictReadStatus.NAMESPACE_ABSENT
        client.core_v1.read_namespace.assert_called_once()

    def test_pod_list_404_with_an_unreadable_namespace_is_error(self):
        client = self._client(
            pod_pages=[ApiException(status=404, reason="Not Found")],
            namespace_error=ApiException(status=403, reason="Forbidden"),
        )
        assert client.list_pods_strict("acm").status is StrictReadStatus.ERROR

    @pytest.mark.parametrize("status", [401, 403, 500, 503])
    def test_pod_list_failure_is_error_not_empty(self, status):
        outcome = self._client(pod_pages=[ApiException(status=status, reason="failed")]).list_pods_strict("acm")
        assert outcome.status is StrictReadStatus.ERROR
        assert outcome.items == []


class TestStrictAppsReads:
    """R4-03: identity-bearing Apps reads for the owner chain."""

    def _client(self, result=None, error=None, kind="deployment"):
        client = KubeClient.__new__(KubeClient)
        client.request_timeout = 30
        client.dry_run = False
        client.apps_v1 = Mock()
        reader = Mock(return_value=result, side_effect=error)
        if kind == "deployment":
            client.apps_v1.read_namespaced_deployment = reader
        else:
            client.apps_v1.read_namespaced_replica_set = reader
        return client

    @staticmethod
    def _object(
        name="multiclusterhub-operator",
        namespace="open-cluster-management",
        uid="u-1",
        resource_version="7",
    ):
        """A V1Deployment/V1ReplicaSet-shaped mock.

        `to_dict()` produces the client's snake_case mapping, so the revision key is
        `resource_version`. It is explicit here because A3.0 rule 9 makes a successful
        Apps GET impossible without one.
        """
        model = Mock()
        model.to_dict = Mock(
            return_value={
                "metadata": {
                    "name": name,
                    "namespace": namespace,
                    "uid": uid,
                    "resource_version": resource_version,
                }
            }
        )
        return model

    @pytest.mark.parametrize(
        "kind, method",
        [
            ("deployment", "get_deployment_strict"),
            ("replicaset", "get_replicaset_strict"),
        ],
    )
    def test_present_object_carries_identity(self, kind, method):
        client = self._client(result=self._object(), kind=kind)
        outcome = getattr(client, method)("multiclusterhub-operator", "open-cluster-management")
        assert outcome.status is StrictReadStatus.ITEMS
        assert outcome.resource["metadata"]["uid"] == "u-1"
        assert outcome.resource_version == "7"

    @pytest.mark.parametrize(
        "kind, method",
        [
            ("deployment", "get_deployment_strict"),
            ("replicaset", "get_replicaset_strict"),
        ],
    )
    def test_explicit_404_is_object_absent(self, kind, method):
        client = self._client(error=ApiException(status=404, reason="Not Found"), kind=kind)
        outcome = getattr(client, method)("multiclusterhub-operator", "open-cluster-management")
        assert outcome.status is StrictReadStatus.OBJECT_ABSENT

    @pytest.mark.parametrize("status", [401, 403, 500, 503])
    def test_api_failure_is_error_not_absence(self, status):
        client = self._client(error=ApiException(status=status, reason="failed"))
        outcome = client.get_deployment_strict("d", "ns")
        assert outcome.status is StrictReadStatus.ERROR
        assert outcome.proves_absence is False

    def test_missing_uid_is_error(self):
        client = self._client(result=self._object(uid=""))
        assert (
            client.get_deployment_strict("multiclusterhub-operator", "open-cluster-management").status
            is StrictReadStatus.ERROR
        )

    def test_mismatched_identity_is_error(self):
        client = self._client(result=self._object(name="something-else"))
        assert (
            client.get_deployment_strict("multiclusterhub-operator", "open-cluster-management").status
            is StrictReadStatus.ERROR
        )

    def test_malformed_object_is_error(self):
        model = Mock()
        model.to_dict = Mock(return_value="nope")
        client = self._client(result=model)
        assert client.get_deployment_strict("d", "ns").status is StrictReadStatus.ERROR

    @pytest.mark.parametrize("revision", [None, "", 7], ids=["missing", "empty", "non_string"])
    def test_apps_get_without_a_usable_revision_is_malformed(self, revision):
        """A3.0 rule 9: `resource_versions["operator_deployment"]` has no null-revision source."""
        client = self._client(result=self._object(resource_version=revision))
        outcome = client.get_deployment_strict("multiclusterhub-operator", "open-cluster-management")
        assert outcome.status is StrictReadStatus.ERROR
        assert outcome.reason == STRICT_READ_REASON_MALFORMED_RESPONSE
        assert outcome.resource_version is None

    def test_apps_get_is_bounded(self):
        client = self._client(result=self._object())
        client.get_deployment_strict("multiclusterhub-operator", "open-cluster-management")
        assert client.apps_v1.read_namespaced_deployment.call_args.kwargs["_request_timeout"] == 30


@pytest.mark.unit
class TestPreconditionedDelete:
    """UID-preconditioned delete: the API server evaluates the UID atomically with the
    deletion, so the object read and the object deleted cannot diverge.

    This primitive deliberately carries NO ``@api_call`` decorator. The ordinary
    ``delete_custom_resource`` uses ``@api_call(not_found_value=True)``, which makes an
    accepted delete and a 404 indistinguishable to the caller -- the imprecision PR B
    documented and could not fix without this primitive. Here the 404 must be
    classified, so it must not be swallowed.
    """

    def test_uid_precondition_is_sent_in_the_delete_body(self, kube_client):
        kube_client.custom_api.delete_cluster_custom_object = Mock(return_value={})
        kube_client.delete_custom_resource_preconditioned(
            "observability.open-cluster-management.io",
            "v1beta2",
            "multiclusterobservabilities",
            "observability",
            uid="uid-1",
        )
        body = kube_client.custom_api.delete_cluster_custom_object.call_args.kwargs["body"]
        assert body.preconditions.uid == "uid-1"
        assert body.preconditions.resource_version is None

    def test_resource_version_is_optional_and_omitted_for_r4_03_callers(self, kube_client):
        kube_client.custom_api.delete_cluster_custom_object = Mock(return_value={})
        kube_client.delete_custom_resource_preconditioned("g", "v1", "widgets", "w", uid="uid-1", resource_version="77")
        body = kube_client.custom_api.delete_cluster_custom_object.call_args.kwargs["body"]
        assert body.preconditions.resource_version == "77"

    def test_namespaced_delete_routes_through_the_namespaced_api_with_the_same_body(self, kube_client):
        kube_client.custom_api.delete_namespaced_custom_object = Mock(return_value={})
        kube_client.custom_api.delete_cluster_custom_object = Mock()
        kube_client.delete_custom_resource_preconditioned("g", "v1", "widgets", "w", uid="uid-1", namespace="ns-1")
        kube_client.custom_api.delete_cluster_custom_object.assert_not_called()
        call = kube_client.custom_api.delete_namespaced_custom_object.call_args
        assert call.kwargs["namespace"] == "ns-1"
        assert call.kwargs["body"].preconditions.uid == "uid-1"

    @pytest.mark.parametrize("status", [409, 412])
    def test_precondition_conflict_is_fatal_and_never_retried_unconditionally(self, kube_client, status):
        """A conflict means the live object is not the one we proved. Retrying without
        the precondition would delete whatever is there now -- the exact failure the
        precondition exists to prevent."""
        from lib.exceptions import PreconditionConflict

        kube_client.custom_api.delete_cluster_custom_object = Mock(
            side_effect=ApiException(status=status, reason="Conflict")
        )
        with pytest.raises(PreconditionConflict):
            kube_client.delete_custom_resource_preconditioned("g", "v1", "w", "n", uid="uid-1")
        assert kube_client.custom_api.delete_cluster_custom_object.call_count == 1

    def test_404_at_delete_time_is_surfaced_not_swallowed(self, kube_client):
        """The caller must run its final live verification rather than assume success."""
        from lib.exceptions import TargetDisappeared

        kube_client.custom_api.delete_cluster_custom_object = Mock(
            side_effect=ApiException(status=404, reason="Not Found")
        )
        with pytest.raises(TargetDisappeared):
            kube_client.delete_custom_resource_preconditioned("g", "v1", "w", "n", uid="uid-1")

    def test_other_api_errors_propagate_unchanged(self, kube_client):
        kube_client.custom_api.delete_cluster_custom_object = Mock(
            side_effect=ApiException(status=500, reason="Server Error")
        )
        with pytest.raises(ApiException):
            kube_client.delete_custom_resource_preconditioned("g", "v1", "w", "n", uid="uid-1")

    @pytest.mark.parametrize("uid", ["", "   ", None])
    def test_empty_uid_is_rejected_before_any_request(self, kube_client, uid):
        from lib.validation import ValidationError

        kube_client.custom_api.delete_cluster_custom_object = Mock()
        with pytest.raises(ValidationError):
            kube_client.delete_custom_resource_preconditioned("g", "v1", "w", "n", uid=uid)
        kube_client.custom_api.delete_cluster_custom_object.assert_not_called()

    def test_dry_run_client_refuses_the_primitive(self, kube_client):
        """A preview must never reach a delete primitive at all. This raises rather
        than logging a would-delete line, unlike ``delete_custom_resource``: PR C's
        callers branch before this point, so arriving here in dry-run is a bug."""
        from lib.exceptions import FatalError

        kube_client.dry_run = True
        kube_client.custom_api.delete_cluster_custom_object = Mock()
        with pytest.raises(FatalError):
            kube_client.delete_custom_resource_preconditioned("g", "v1", "w", "n", uid="uid-1")
        kube_client.custom_api.delete_cluster_custom_object.assert_not_called()

    def test_the_primitive_is_undecorated_so_a_404_can_never_be_swallowed(self, kube_client):
        """Kill condition for the whole class.

        Wrapping this in ``@api_call(not_found_value=True)`` would turn the 404 into a
        ``True`` return, silently restoring the ambiguity PR B documented. Asserted on
        the actual decoration rather than on source text: an earlier version of this
        test grepped the source and failed against its own docstring, which mentions
        the decorator by name in explaining why it is absent.

        ``api_call`` wraps via ``functools.wraps`` and therefore sets ``__wrapped__``;
        the decorated neighbour is asserted alongside so this test fails if that
        detection stops working rather than passing for the wrong reason.
        """
        assert not hasattr(KubeClient.delete_custom_resource_preconditioned, "__wrapped__")
        assert hasattr(
            KubeClient.delete_custom_resource, "__wrapped__"
        ), "the decorated neighbour must be detectable, or this guard proves nothing"


# A sentinel for a discovery document with no `groupVersion` key at all (#321).
_MISSING_GROUP_VERSION = object()


class TestDiscoveryGroupVersionProof:
    """#321: a discovery document proves nothing unless it declares the requested group/version.

    The collection's prover has rejected a missing, empty, non-string or mismatched
    `APIResourceList.groupVersion` since #317; Python now does the same, so such a document
    can establish neither a served kind (and so no object absence after a 404) nor kind absence.
    """

    _GROUP = "operator.open-cluster-management.io"
    _PLURAL = "multiclusterhubs"

    @staticmethod
    def _document(group_version, *, listed):
        resources = (
            [{"name": "multiclusterhubs", "kind": "MultiClusterHub"}]
            if listed
            else [{"name": "somethingelse", "kind": "SomethingElse"}]
        )
        document = {"kind": "APIResourceList", "resources": resources}
        if group_version is not _MISSING_GROUP_VERSION:
            document["groupVersion"] = group_version
        return Mock(data=json.dumps(document).encode("utf-8"))

    def _client(self, document):
        client = KubeClient.__new__(KubeClient)
        client.request_timeout = 30
        client.dry_run = False
        # The pre-37 client shape: no `param_serialize`, so requests take the legacy `call_api` path.
        client._api_client = Mock(spec=["call_api"])
        client._api_client.call_api = Mock(return_value=document)
        client.custom_api = Mock()
        client.custom_api.get_cluster_custom_object = Mock(side_effect=ApiException(status=404))
        client.custom_api.list_cluster_custom_object = Mock(
            return_value={"items": [], "metadata": {"resourceVersion": "1"}}
        )
        return client

    @pytest.mark.parametrize("listed", [True, False], ids=["resource-listed", "resource-omitted"])
    @pytest.mark.parametrize(
        "group_version",
        [_MISSING_GROUP_VERSION, "", 7, None, "operator.open-cluster-management.io/v2", "other.io/v1"],
        ids=["missing", "empty", "non-string", "null", "other-version", "other-group"],
    )
    def test_a_document_not_declaring_the_requested_group_version_is_unverifiable(self, group_version, listed):
        outcome = self._client(self._document(group_version, listed=listed))._discovery_serves(
            self._GROUP, "v1", self._PLURAL
        )
        assert outcome.status is StrictReadStatus.ERROR
        assert outcome.reason == STRICT_READ_REASON_DISCOVERY_UNVERIFIABLE

    @pytest.mark.parametrize("listed", [True, False], ids=["resource-listed", "resource-omitted"])
    @pytest.mark.parametrize(
        "group_version", [_MISSING_GROUP_VERSION, "", 7, None, "operator.open-cluster-management.io/v2", "other.io/v1"]
    )
    def test_a_named_get_never_proves_absence_from_such_a_document(self, group_version, listed):
        client = self._client(self._document(group_version, listed=listed))
        outcome = client.get_custom_resource_strict(self._GROUP, "v1", self._PLURAL, "multiclusterhub")
        assert outcome.status is StrictReadStatus.ERROR
        assert outcome.proves_absence is False
        client.custom_api.get_cluster_custom_object.assert_not_called()

    @pytest.mark.parametrize("listed", [True, False], ids=["resource-listed", "resource-omitted"])
    @pytest.mark.parametrize(
        "group_version", [_MISSING_GROUP_VERSION, "", 7, None, "operator.open-cluster-management.io/v2", "other.io/v1"]
    )
    def test_a_list_never_proves_absence_or_inventory_from_such_a_document(self, group_version, listed):
        client = self._client(self._document(group_version, listed=listed))
        outcome = client.list_custom_resources_strict(self._GROUP, "v1", self._PLURAL)
        assert outcome.status is StrictReadStatus.ERROR
        assert outcome.proves_absence is False
        client.custom_api.list_cluster_custom_object.assert_not_called()

    def test_the_requested_group_version_keeps_every_positive_outcome(self):
        """Positive controls: a document for the requested group/version proves as before."""
        served = self._client(self._document("operator.open-cluster-management.io/v1", listed=True))
        assert served._discovery_serves(self._GROUP, "v1", self._PLURAL).status is StrictReadStatus.ITEMS
        absent_object = served.get_custom_resource_strict(self._GROUP, "v1", self._PLURAL, "multiclusterhub")
        assert absent_object.status is StrictReadStatus.OBJECT_ABSENT
        assert served.list_custom_resources_strict(self._GROUP, "v1", self._PLURAL).status is StrictReadStatus.ITEMS
        unserved = self._client(self._document("operator.open-cluster-management.io/v1", listed=False))
        assert unserved._discovery_serves(self._GROUP, "v1", self._PLURAL).status is StrictReadStatus.CRD_ABSENT
        assert unserved.list_custom_resources_strict(self._GROUP, "v1", self._PLURAL).status is (
            StrictReadStatus.CRD_ABSENT
        )
        assert unserved.get_custom_resource_strict(self._GROUP, "v1", self._PLURAL, "multiclusterhub").status is (
            StrictReadStatus.CRD_ABSENT
        )

    def test_the_core_group_is_declared_as_its_bare_version(self):
        document = Mock(
            data=json.dumps(
                {"kind": "APIResourceList", "groupVersion": "v1", "resources": [{"name": "pods", "kind": "Pod"}]}
            ).encode("utf-8")
        )
        assert self._client(document)._discovery_serves("", "v1", "pods").status is StrictReadStatus.ITEMS


# Unit expectations for the guarded Restore mutations (R4-04 Task 4). Equality with the
# collection's acm_restore_guarded_mutation is enforced by
# tests/test_restore_guarded_mutation_parity.py, which drives both form factors.
GUARDED_RESTORE_PATCH_VECTOR = {
    "uid": "3f0c2a4e-uid",
    "resource_version": "48213",
    "expected_managed_clusters_backup_name": " Skip ",
    "replacement_managed_clusters_backup_name": "latest",
    "content_type": "application/json-patch+json",
    "patch": [
        {"op": "test", "path": "/metadata/uid", "value": "3f0c2a4e-uid"},
        {"op": "test", "path": "/metadata/resourceVersion", "value": "48213"},
        {"op": "test", "path": "/spec/veleroManagedClustersBackupName", "value": " Skip "},
        {"op": "replace", "path": "/spec/veleroManagedClustersBackupName", "value": "latest"},
    ],
}
# HTTP status -> (conflict, reason) for the guarded PATCH. Every other ApiException status
# and every non-API failure (timeout, transport) is (False, "unverifiable").
GUARDED_RESTORE_PATCH_STATUS_VECTOR = {
    409: (True, "precondition_failed"),
    412: (True, "precondition_failed"),
    422: (True, "precondition_failed"),
    404: (False, "not_found"),
    403: (False, "unverifiable"),
    500: (False, "unverifiable"),
}


class TestGuardedRestoreJsonPatch:
    """The guarded Restore JSON Patch: an atomic PATCH the API server applies only while the
    object is still the proved UID, at the proved resourceVersion, holding the proved raw
    ManagedClusters backup value. A failed test is a no-mutation conflict, never retried and
    never replaced by a merge patch."""

    _SECRET = "R4-04-GUARDED-PATCH-SECRET-BODY token=abc123 /home/op/.kube/config"

    @staticmethod
    def _kwargs(**overrides):
        kwargs = {
            "namespace": "open-cluster-management-backup",
            "uid": GUARDED_RESTORE_PATCH_VECTOR["uid"],
            "resource_version": GUARDED_RESTORE_PATCH_VECTOR["resource_version"],
            "expected_managed_clusters_backup_name": GUARDED_RESTORE_PATCH_VECTOR[
                "expected_managed_clusters_backup_name"
            ],
            "replacement_managed_clusters_backup_name": "latest",
        }
        kwargs.update(overrides)
        return kwargs

    @staticmethod
    def _accepted(kube_client, metadata):
        body = {"apiVersion": "cluster.open-cluster-management.io/v1beta1", "kind": "Restore", "metadata": metadata}
        kube_client._api_client.call_api = Mock(return_value=Mock(data=json.dumps(body).encode("utf-8")))

    def _patch(self, kube_client, **overrides):
        return kube_client.json_patch_custom_resource_guarded("restore-acm-passive-sync", **self._kwargs(**overrides))

    def test_the_patch_document_is_the_shared_parity_vector(self):
        from lib.kube_client import build_guarded_restore_patch

        assert (
            build_guarded_restore_patch(
                uid=GUARDED_RESTORE_PATCH_VECTOR["uid"],
                resource_version=GUARDED_RESTORE_PATCH_VECTOR["resource_version"],
                expected_managed_clusters_backup_name=GUARDED_RESTORE_PATCH_VECTOR[
                    "expected_managed_clusters_backup_name"
                ],
                replacement_managed_clusters_backup_name="latest",
            )
            == GUARDED_RESTORE_PATCH_VECTOR["patch"]
        )

    def test_one_json_patch_request_carries_every_test_before_the_replace(self, kube_client):
        self._accepted(kube_client, {"uid": "3f0c2a4e-uid", "resourceVersion": "48300", "generation": 7})
        kube_client.custom_api.patch_namespaced_custom_object = Mock()

        self._patch(kube_client)

        kube_client.custom_api.patch_namespaced_custom_object.assert_not_called()
        assert kube_client._api_client.call_api.call_count == 1
        call = kube_client._api_client.call_api.call_args
        assert call.args[1] == "PATCH"
        assert call.args[0] == "/apis/{group}/{version}/namespaces/{namespace}/{plural}/{name}"
        assert call.kwargs["path_params"] == {
            "group": "cluster.open-cluster-management.io",
            "version": "v1beta1",
            "namespace": "open-cluster-management-backup",
            "plural": "restores",
            "name": "restore-acm-passive-sync",
        }
        assert call.kwargs["header_params"]["Content-Type"] == GUARDED_RESTORE_PATCH_VECTOR["content_type"]
        # The client library silently downgrades a non-list body to a strategic merge patch.
        assert isinstance(call.kwargs["body"], list)
        assert call.kwargs["body"] == GUARDED_RESTORE_PATCH_VECTOR["patch"]
        assert call.kwargs["auth_settings"] == ["BearerToken"]
        assert call.kwargs["_request_timeout"] == kube_client.request_timeout

    def test_an_explicit_timeout_bounds_the_request(self, kube_client):
        self._accepted(kube_client, {"uid": "3f0c2a4e-uid", "resourceVersion": "48300", "generation": 7})
        self._patch(kube_client, timeout_seconds=11)
        assert kube_client._api_client.call_api.call_args.kwargs["_request_timeout"] == 11

    def test_an_accepted_response_exposes_its_own_identity_and_generation(self, kube_client):
        self._accepted(kube_client, {"uid": "3f0c2a4e-uid", "resourceVersion": "48300", "generation": 7})
        assert self._patch(kube_client) == {
            "accepted": True,
            "http_accepted": True,
            "changed": True,
            "would_change": False,
            "conflict": False,
            "dry_run": False,
            "reason": "ok",
            "uid": "3f0c2a4e-uid",
            "resource_version": "48300",
            "generation": 7,
            "generation_reported": True,
        }

    def test_an_omitted_generation_is_reported_never_invented(self, kube_client):
        self._accepted(kube_client, {"uid": "3f0c2a4e-uid", "resourceVersion": "48300"})
        result = self._patch(kube_client)
        assert result["accepted"] is True
        assert result["generation"] is None
        assert result["generation_reported"] is False

    @pytest.mark.parametrize("generation", [None, "7", "2", True, False, 7.0, 0, -1, [7], {"value": 7}])
    def test_a_present_but_invalid_generation_is_a_malformed_response(self, kube_client, generation):
        """C section 5.1 recovers only an omitted generation; a present invalid one is malformed (J section 1b)."""
        self._accepted(kube_client, {"uid": "3f0c2a4e-uid", "resourceVersion": "48300", "generation": generation})
        assert self._patch(kube_client) == self._MALFORMED_IDENTITY

    _MALFORMED_IDENTITY = {
        "accepted": False,
        "http_accepted": True,
        "changed": False,
        "would_change": False,
        "conflict": False,
        "dry_run": False,
        "reason": "malformed_response",
        "uid": None,
        "resource_version": None,
        "generation": None,
        "generation_reported": False,
    }

    @pytest.mark.parametrize(
        "metadata",
        [
            {"resourceVersion": "48300", "generation": 7},
            {"uid": "", "resourceVersion": "48300", "generation": 7},
            {"uid": "3f0c2a4e-uid", "generation": 7},
            {"uid": "3f0c2a4e-uid", "resourceVersion": "", "generation": 7},
            {"uid": "3f0c2a4e-uid", "resourceVersion": 48300, "generation": 7},
            {"uid": "a-different-uid", "resourceVersion": "48300", "generation": 7},
            "not-a-mapping",
        ],
    )
    def test_a_malformed_response_identity_is_fail_closed_with_no_generation_evidence(self, kube_client, metadata):
        """July §1 step 3 recovers only a missing generation under a valid identity; a
        missing or different UID, or a missing resourceVersion, is never verified acceptance
        and never publishes the generation it came with."""
        self._accepted(kube_client, metadata)
        assert self._patch(kube_client) == self._MALFORMED_IDENTITY

    @pytest.mark.parametrize("data", [b"<html>", b"[]", b"null", b"\xff\xfe"])
    def test_an_undecodable_accepted_body_is_fail_closed(self, kube_client, data):
        kube_client._api_client.call_api = Mock(return_value=Mock(data=data))
        assert self._patch(kube_client) == self._MALFORMED_IDENTITY

    @pytest.mark.parametrize(
        "exc", [ReadTimeoutError(None, "/restores", "read timed out"), ProtocolError("reset"), OSError("reset")]
    )
    def test_a_deferred_body_read_failure_is_not_acceptance(self, kube_client, exc):
        """With _preload_content=False the body is read after call_api returns; a timeout
        there says nothing about whether the PATCH was applied."""

        class _DeferredBody:
            @property
            def data(self):
                raise exc

        kube_client._api_client.call_api = Mock(return_value=_DeferredBody())
        result = self._patch(kube_client)
        assert result == {
            "accepted": False,
            "http_accepted": False,
            "changed": False,
            "would_change": False,
            "conflict": False,
            "dry_run": False,
            "reason": "unverifiable",
            "uid": None,
            "resource_version": None,
            "generation": None,
            "generation_reported": False,
        }
        assert kube_client._api_client.call_api.call_count == 1

    def test_replacing_an_already_latest_value_is_accepted_but_unchanged(self, kube_client):
        self._accepted(kube_client, {"uid": "3f0c2a4e-uid", "resourceVersion": "48213", "generation": 6})
        result = self._patch(kube_client, expected_managed_clusters_backup_name="latest")
        assert (result["accepted"], result["changed"]) == (True, False)

    @pytest.mark.parametrize("status", sorted(GUARDED_RESTORE_PATCH_STATUS_VECTOR))
    def test_api_failures_follow_the_shared_classification_and_are_never_retried(self, kube_client, status):
        kube_client._api_client.call_api = Mock(side_effect=ApiException(status=status, reason="Rejected"))
        kube_client.custom_api.patch_namespaced_custom_object = Mock()

        result = self._patch(kube_client)

        conflict, reason = GUARDED_RESTORE_PATCH_STATUS_VECTOR[status]
        assert (result["conflict"], result["reason"]) == (conflict, reason)
        assert (result["accepted"], result["changed"]) == (False, False)
        assert result["uid"] is None and result["generation"] is None
        # One guarded attempt; no retry and no merge-patch fallback.
        assert kube_client._api_client.call_api.call_count == 1
        kube_client.custom_api.patch_namespaced_custom_object.assert_not_called()

    @pytest.mark.parametrize("exc", [Urllib3TimeoutError("read timed out"), OSError("reset"), ValueError("x")])
    def test_a_timeout_or_transport_failure_is_not_acceptance(self, kube_client, exc):
        kube_client._api_client.call_api = Mock(side_effect=exc)
        result = self._patch(kube_client)
        assert (result["accepted"], result["changed"], result["conflict"], result["reason"]) == (
            False,
            False,
            False,
            "unverifiable",
        )
        assert kube_client._api_client.call_api.call_count == 1

    def test_the_primitive_is_undecorated_so_nothing_retries_it(self):
        assert not hasattr(KubeClient.json_patch_custom_resource_guarded, "__wrapped__")
        assert hasattr(KubeClient.patch_custom_resource, "__wrapped__")

    @pytest.mark.parametrize("expected", ["skip", " Skip ", "latest"])
    def test_dry_run_makes_no_api_call_and_reports_the_prediction_separately(self, dry_run_client, expected):
        dry_run_client._api_client.call_api = Mock()
        dry_run_client.custom_api.patch_namespaced_custom_object = Mock()

        result = self._patch(dry_run_client, expected_managed_clusters_backup_name=expected)

        dry_run_client._api_client.call_api.assert_not_called()
        dry_run_client.custom_api.patch_namespaced_custom_object.assert_not_called()
        assert result == {
            "accepted": False,
            "http_accepted": False,
            "changed": False,
            "would_change": expected != "latest",
            "conflict": False,
            "dry_run": True,
            "reason": "predicted",
            "uid": None,
            "resource_version": None,
            "generation": None,
            "generation_reported": False,
        }

    @pytest.mark.parametrize(
        "overrides",
        [
            {"uid": ""},
            {"uid": None},
            {"resource_version": ""},
            {"resource_version": 48213},
            {"uid": " 3f0c2a4e-uid"},
            {"uid": "3f0c2a4e-uid\n"},
            {"resource_version": "48213 "},
            {"expected_managed_clusters_backup_name": ""},
            {"expected_managed_clusters_backup_name": None},
            {"replacement_managed_clusters_backup_name": "Latest"},
            {"replacement_managed_clusters_backup_name": "acm-managed-clusters-schedule-20260930101010"},
            {"replacement_managed_clusters_backup_name": "skip"},
            {"namespace": ""},
            {"namespace": None},
            {"namespace": "Bad_Namespace"},
        ],
    )
    def test_invalid_inputs_are_rejected_before_any_request_even_in_dry_run(self, kube_client, overrides):
        from lib.validation import ValidationError

        kube_client._api_client.call_api = Mock()
        for dry_run in (False, True):
            kube_client.dry_run = dry_run
            with pytest.raises(ValidationError):
                self._patch(kube_client, **overrides)
        kube_client._api_client.call_api.assert_not_called()

    @pytest.mark.parametrize("status", [409, 422, 500])
    def test_failures_never_expose_the_response_body_or_credentials(self, kube_client, caplog, status):
        kube_client._api_client.call_api = Mock(side_effect=ApiException(status=status, reason=self._SECRET))
        kube_client._api_client.call_api.side_effect.body = self._SECRET
        with caplog.at_level("DEBUG", logger="acm_switchover"):
            result = self._patch(kube_client)
        assert self._SECRET not in json.dumps(result)
        assert "abc123" not in caplog.text
        assert "R4-04-GUARDED-PATCH-SECRET-BODY" not in caplog.text

    def test_transport_failures_never_expose_their_message(self, kube_client, caplog):
        kube_client._api_client.call_api = Mock(side_effect=OSError(self._SECRET))
        with caplog.at_level("DEBUG", logger="acm_switchover"):
            result = self._patch(kube_client)
        assert self._SECRET not in json.dumps(result)
        assert "abc123" not in caplog.text

    _ACCEPTED_BODY = {
        "apiVersion": "cluster.open-cluster-management.io/v1beta1",
        "kind": "Restore",
        "metadata": {"uid": "3f0c2a4e-uid", "resourceVersion": "48300", "generation": 7},
    }

    @staticmethod
    def _real_client(pool_manager):
        client = KubeClient.__new__(KubeClient)
        client.request_timeout = 30
        client.dry_run = False
        client._api_client = _real_api_client(pool_manager)
        return client

    def test_the_guarded_patch_binds_the_real_api_client_signature(self):
        """Every other test here mocks `call_api`, which accepts any keyword. This drives a
        real, installed `ApiClient` with only its socket faked, so a transport call the
        resolved kubernetes client rejects (37.x's `call_api` takes none of the older
        preparation keywords) fails here instead of reporting every guarded PATCH as not
        accepted before any request is sent."""
        pool_manager = _RecordingPoolManager(payload=self._ACCEPTED_BODY)

        result = self._real_client(pool_manager).json_patch_custom_resource_guarded(
            "restore-acm-passive-sync", **self._kwargs(timeout_seconds=11)
        )

        assert (result["accepted"], result["reason"], result["generation"]) == (True, "ok", 7)
        [call] = pool_manager.calls
        assert call["method"] == "PATCH"
        assert call["url"].endswith(
            "/apis/cluster.open-cluster-management.io/v1beta1/namespaces/open-cluster-management-backup"
            "/restores/restore-acm-passive-sync"
        )
        # Still a JSON Patch on the wire: the client downgrades a non-list body to a strategic
        # merge patch, which would drop every `test` operation.
        assert call["headers"]["Content-Type"] == GUARDED_RESTORE_PATCH_VECTOR["content_type"]
        assert json.loads(call["body"]) == GUARDED_RESTORE_PATCH_VECTOR["patch"]
        assert {key.lower(): value for key, value in call["headers"].items()}["authorization"] == (
            "Bearer binding-test-token"
        )
        assert call["timeout"].total == 11

    @pytest.mark.parametrize("status", sorted(GUARDED_RESTORE_PATCH_STATUS_VECTOR))
    def test_a_real_client_http_error_follows_the_shared_classification(self, status):
        pool_manager = _RecordingPoolManager(status=status, payload={"kind": "Status", "code": status})

        result = self._real_client(pool_manager).json_patch_custom_resource_guarded(
            "restore-acm-passive-sync", **self._kwargs()
        )

        assert (result["conflict"], result["reason"]) == GUARDED_RESTORE_PATCH_STATUS_VECTOR[status]
        assert (result["accepted"], result["http_accepted"], result["changed"]) == (False, False, False)
        assert len(pool_manager.calls) == 1

    @pytest.mark.parametrize(
        "exc", [ReadTimeoutError(None, "/restores", "read timed out"), ProtocolError("reset"), OSError("reset")]
    )
    def test_a_real_client_body_read_failure_is_not_acceptance(self, exc):
        pool_manager = _RecordingPoolManager(body=_UnreadableBody(exc))

        result = self._real_client(pool_manager).json_patch_custom_resource_guarded(
            "restore-acm-passive-sync", **self._kwargs()
        )

        assert (result["accepted"], result["http_accepted"], result["reason"]) == (False, False, "unverifiable")
        assert len(pool_manager.calls) == 1

    @pytest.mark.parametrize("constant", _NON_JSON_CONSTANTS)
    def test_a_real_client_non_json_constant_is_malformed(self, constant):
        pool_manager = _RecordingPoolManager(body=_with_constant(self._ACCEPTED_BODY, constant))

        result = self._real_client(pool_manager).json_patch_custom_resource_guarded(
            "restore-acm-passive-sync", **self._kwargs()
        )

        assert result == self._MALFORMED_IDENTITY
        assert len(pool_manager.calls) == 1

    @pytest.mark.parametrize("revision", [" ", "48300 ", " 48300", "\t"])
    def test_a_real_client_padded_response_revision_is_malformed(self, revision):
        body = dict(self._ACCEPTED_BODY, metadata={"uid": "3f0c2a4e-uid", "resourceVersion": revision, "generation": 7})
        pool_manager = _RecordingPoolManager(payload=body)

        result = self._real_client(pool_manager).json_patch_custom_resource_guarded(
            "restore-acm-passive-sync", **self._kwargs()
        )

        assert result == self._MALFORMED_IDENTITY
        assert len(pool_manager.calls) == 1

    def test_a_real_client_undecodable_accepted_body_is_malformed(self):
        pool_manager = _RecordingPoolManager(body=b"<html>")

        result = self._real_client(pool_manager).json_patch_custom_resource_guarded(
            "restore-acm-passive-sync", **self._kwargs()
        )

        assert result == self._MALFORMED_IDENTITY
        assert len(pool_manager.calls) == 1

    def test_the_generic_merge_patch_is_unchanged(self, kube_client):
        kube_client.custom_api.patch_namespaced_custom_object = Mock(return_value={"metadata": {}})
        kube_client._api_client.call_api = Mock()
        kube_client.patch_custom_resource("g", "v1", "widgets", "w", {"spec": {"a": 1}}, namespace="ns")
        kube_client.custom_api.patch_namespaced_custom_object.assert_called_once()
        kube_client._api_client.call_api.assert_not_called()


class TestGuardedRestoreDelete:
    """The narrow Restore delete: one raw DELETE carrying both preconditions, mapped to the
    guarded-mutation result shape. In dry-run it predicts without a request."""

    _ARGS = {
        "namespace": "open-cluster-management-backup",
        "uid": "3f0c2a4e-uid",
        "resource_version": "48213",
    }

    def _delete(self, client, **overrides):
        return client.delete_restore_guarded("restore-acm-passive-sync", **{**self._ARGS, **overrides})

    @staticmethod
    def _shape(**updates):
        result = {
            "accepted": False,
            "http_accepted": False,
            "changed": False,
            "would_change": False,
            "conflict": False,
            "dry_run": False,
            "reason": "unverifiable",
            "uid": None,
            "resource_version": None,
        }
        result.update(updates)
        return result

    @staticmethod
    def _answer(client, response):
        """Answer the one raw DELETE with `response` as its JSON body."""
        client._api_client.call_api = Mock(return_value=Mock(data=json.dumps(response).encode("utf-8")))

    def test_an_accepted_delete_sends_both_preconditions_once(self, kube_client):
        self._answer(kube_client, {"kind": "Status", "status": "Success"})
        kube_client.custom_api.delete_namespaced_custom_object = Mock()
        result = self._delete(kube_client, timeout_seconds=9)
        assert result == self._shape(
            accepted=True,
            http_accepted=True,
            changed=True,
            reason="ok",
            uid="3f0c2a4e-uid",
            resource_version="48213",
        )
        kube_client.custom_api.delete_namespaced_custom_object.assert_not_called()
        assert kube_client._api_client.call_api.call_count == 1
        call = kube_client._api_client.call_api.call_args
        assert call.args == ("/apis/{group}/{version}/namespaces/{namespace}/{plural}/{name}", "DELETE")
        assert call.kwargs["path_params"] == {
            "group": "cluster.open-cluster-management.io",
            "version": "v1beta1",
            "namespace": "open-cluster-management-backup",
            "plural": "restores",
            "name": "restore-acm-passive-sync",
        }
        assert call.kwargs["_request_timeout"] == 9
        assert call.kwargs["body"]["preconditions"] == {"uid": "3f0c2a4e-uid", "resourceVersion": "48213"}

    @pytest.mark.parametrize(
        ("exc", "conflict", "reason"),
        [
            (ApiException(status=409), True, "precondition_failed"),
            (ApiException(status=412), True, "precondition_failed"),
            (ApiException(status=404), False, "not_found"),
            (ApiException(status=403), False, "unverifiable"),
            (ApiException(status=500), False, "unverifiable"),
            (ReadTimeoutError(None, "/restores", "read timed out"), False, "unverifiable"),
        ],
    )
    def test_failures_are_classified_once_and_never_retried(self, kube_client, exc, conflict, reason):
        kube_client._api_client.call_api = Mock(side_effect=exc)
        assert self._delete(kube_client) == self._shape(conflict=conflict, reason=reason)
        assert kube_client._api_client.call_api.call_count == 1

    def test_the_default_timeout_is_the_client_request_timeout(self, kube_client):
        self._answer(kube_client, {"kind": "Status", "status": "Success"})
        assert self._delete(kube_client)["accepted"] is True
        call = kube_client._api_client.call_api.call_args
        assert call.kwargs["_request_timeout"] == kube_client.request_timeout

    def test_dry_run_predicts_without_any_api_call(self, dry_run_client):
        dry_run_client.custom_api.delete_namespaced_custom_object = Mock()
        dry_run_client._api_client.call_api = Mock()
        assert self._delete(dry_run_client) == self._shape(dry_run=True, would_change=True, reason="predicted")
        dry_run_client.custom_api.delete_namespaced_custom_object.assert_not_called()
        dry_run_client._api_client.call_api.assert_not_called()

    @pytest.mark.parametrize(
        "overrides",
        [
            {"uid": ""},
            {"uid": None},
            {"resource_version": ""},
            {"resource_version": None},
            {"resource_version": 48213},
            {"uid": " 3f0c2a4e-uid"},
            {"uid": "3f0c2a4e-uid\t"},
            {"resource_version": " 48213"},
            {"resource_version": "48213\n"},
            {"namespace": ""},
            {"namespace": None},
            {"namespace": "Bad_Namespace"},
        ],
    )
    def test_invalid_inputs_are_rejected_before_any_request_even_in_dry_run(self, kube_client, overrides):
        from lib.validation import ValidationError

        kube_client._api_client.call_api = Mock()
        for dry_run in (False, True):
            kube_client.dry_run = dry_run
            with pytest.raises(ValidationError):
                self._delete(kube_client, **overrides)
        kube_client._api_client.call_api.assert_not_called()

    def test_failures_never_expose_exception_text(self, kube_client, caplog):
        secret = "R4-04-DELETE-SECRET token=abc123"
        exc = ApiException(status=500, reason=secret)
        exc.body = secret
        kube_client._api_client.call_api = Mock(side_effect=exc)
        with caplog.at_level("DEBUG", logger="acm_switchover"):
            result = self._delete(kube_client)
        assert "abc123" not in json.dumps(result)
        assert "abc123" not in caplog.text

    def test_the_guarded_delete_binds_the_real_api_client_signature(self):
        pool_manager = _RecordingPoolManager(payload={"kind": "Status", "status": "Success"})

        result = self._delete(_real_kube_client(pool_manager), timeout_seconds=9)

        assert result == self._shape(
            accepted=True, http_accepted=True, changed=True, reason="ok", uid="3f0c2a4e-uid", resource_version="48213"
        )
        [call] = pool_manager.calls
        assert call["method"] == "DELETE"
        assert call["url"].endswith(
            "/apis/cluster.open-cluster-management.io/v1beta1/namespaces/open-cluster-management-backup"
            "/restores/restore-acm-passive-sync"
        )
        assert json.loads(call["body"])["preconditions"] == {"uid": "3f0c2a4e-uid", "resourceVersion": "48213"}
        assert call["timeout"].total == 9

    @pytest.mark.parametrize(
        ("status", "conflict", "reason"),
        [(409, True, "precondition_failed"), (412, True, "precondition_failed"), (404, False, "not_found")]
        + [(status, False, "unverifiable") for status in (403, 500, 504)],
    )
    def test_a_real_client_http_error_is_classified_once(self, status, conflict, reason):
        pool_manager = _RecordingPoolManager(status=status, payload={"kind": "Status", "code": status})

        assert self._delete(_real_kube_client(pool_manager)) == self._shape(conflict=conflict, reason=reason)
        assert len(pool_manager.calls) == 1

    _DELETED_RESTORE = {
        "apiVersion": "cluster.open-cluster-management.io/v1beta1",
        "kind": "Restore",
        "metadata": {"name": "restore-acm-passive-sync", "uid": "3f0c2a4e-uid", "resourceVersion": "48214"},
    }

    @pytest.mark.parametrize("status", [200, 202])
    @pytest.mark.parametrize(
        "payload",
        [{"kind": "Status", "apiVersion": "v1", "status": "Success"}, _DELETED_RESTORE],
        ids=["status-object", "deleted-restore"],
    )
    def test_a_real_client_2xx_object_is_acceptance(self, status, payload):
        """A 202 is as much an accepted DELETE as a 200, whether the server answers with a Status
        or with the object whose deletion it started. The generated clients decode a DELETE body
        for 200 only, so the guarded delete reads the raw body itself."""
        pool_manager = _RecordingPoolManager(status=status, payload=payload)

        result = self._delete(_real_kube_client(pool_manager))

        assert result == self._shape(
            accepted=True, http_accepted=True, changed=True, reason="ok", uid="3f0c2a4e-uid", resource_version="48213"
        )
        assert len(pool_manager.calls) == 1

    @pytest.mark.parametrize("status", [200, 202])
    @pytest.mark.parametrize(("body", "content_type"), _MALFORMED_2XX_BODIES)
    def test_a_real_client_malformed_2xx_body_is_not_acceptance(self, body, content_type, status):
        """The server answered 2xx, so the Restore may be gone, but nothing in the answer can be
        read as a delete of the proved object: it is not accepted, on every client version."""
        pool_manager = _RecordingPoolManager(status=status, body=body, content_type=content_type)

        result = self._delete(_real_kube_client(pool_manager))

        assert result == self._shape(http_accepted=True, reason="malformed_response")
        assert len(pool_manager.calls) == 1

    @pytest.mark.parametrize("status", [200, 202])
    @pytest.mark.parametrize("constant", _NON_JSON_CONSTANTS)
    def test_a_real_client_non_json_constant_is_not_acceptance(self, constant, status):
        payload = {"kind": "Status", "apiVersion": "v1", "status": "Success"}
        pool_manager = _RecordingPoolManager(status=status, body=_with_constant(payload, constant))

        result = self._delete(_real_kube_client(pool_manager))

        assert result == self._shape(http_accepted=True, reason="malformed_response")
        assert len(pool_manager.calls) == 1

    def test_a_real_client_body_read_failure_is_not_acceptance(self):
        pool_manager = _RecordingPoolManager(body=_UnreadableBody(ReadTimeoutError(None, "/restores", "timed out")))

        assert self._delete(_real_kube_client(pool_manager)) == self._shape()
        assert len(pool_manager.calls) == 1


class TestGuardedRestoreCreate:
    """R4-04 PR C Task 7: one create request; acceptance only from a 2xx body that names the
    created object. A 409, a timeout or any other failure is never reconciled into success."""

    _BODY = {
        "apiVersion": "cluster.open-cluster-management.io/v1beta1",
        "kind": "Restore",
        "metadata": {"name": "restore-acm-full", "namespace": "open-cluster-management-backup"},
        "spec": {"veleroManagedClustersBackupName": "acm-managed-clusters-schedule-20240101120000"},
    }

    def _create(self, client, body=None):
        return client.create_restore_guarded(body or self._BODY, namespace="open-cluster-management-backup")

    @staticmethod
    def _answer(client, response):
        """Answer the one raw POST with `response` as its JSON body."""
        client._api_client.call_api = Mock(return_value=Mock(data=json.dumps(response).encode("utf-8")))

    @staticmethod
    def _created(**metadata):
        base = {"name": "restore-acm-full", "namespace": "open-cluster-management-backup"}
        base.update(metadata)
        return {"kind": "Restore", "metadata": base, "spec": {}}

    def test_an_accepted_create_reports_identity_and_generation(self, kube_client):
        self._answer(kube_client, self._created(uid="u-1", resourceVersion="10", generation=1))
        result = self._create(kube_client)
        assert result["accepted"] is True and result["changed"] is True and result["reason"] == "ok"
        assert (result["uid"], result["resource_version"], result["generation"]) == ("u-1", "10", 1)
        assert result["generation_reported"] is True
        kube_client._api_client.call_api.assert_called_once()

    def test_a_missing_generation_is_not_inferred(self, kube_client):
        self._answer(kube_client, self._created(uid="u-1", resourceVersion="10"))
        result = self._create(kube_client)
        assert result["accepted"] is True
        assert result["generation"] is None and result["generation_reported"] is False

    @pytest.mark.parametrize("generation", [None, "7", "2", True, False, 7.0, 0, -1, [7], {"value": 7}])
    def test_a_present_but_invalid_generation_is_a_malformed_response(self, kube_client, generation):
        self._answer(kube_client, self._created(uid="u-1", resourceVersion="10", generation=generation))
        result = self._create(kube_client)
        assert (result["accepted"], result["http_accepted"], result["changed"]) == (False, True, False)
        assert (result["reason"], result["uid"], result["resource_version"]) == ("malformed_response", None, None)
        assert (result["generation"], result["generation_reported"]) == (None, False)

    @pytest.mark.parametrize(
        "metadata",
        [
            {"resourceVersion": "10"},
            {"uid": "", "resourceVersion": "10"},
            {"uid": "u-1"},
            {"uid": "u-1", "resourceVersion": "10", "name": "other"},
        ],
    )
    def test_a_2xx_without_the_created_identity_is_not_accepted(self, kube_client, metadata):
        self._answer(kube_client, self._created(**metadata))
        result = self._create(kube_client)
        assert result["accepted"] is False and result["http_accepted"] is True
        assert result["reason"] == "malformed_response" and result["uid"] is None

    def test_a_conflict_is_never_reconciled(self, kube_client):
        kube_client._api_client.call_api = Mock(side_effect=ApiException(status=409))
        kube_client.custom_api.get_namespaced_custom_object = Mock()
        result = self._create(kube_client)
        assert result["accepted"] is False and result["conflict"] is True and result["reason"] == "already_exists"
        kube_client.custom_api.get_namespaced_custom_object.assert_not_called()
        kube_client._api_client.call_api.assert_called_once()

    @pytest.mark.parametrize(
        "error", [ApiException(status=504), TimeoutError("read timed out"), ApiException(status=500)]
    )
    def test_a_timeout_or_server_error_is_unverifiable_and_not_retried(self, kube_client, error):
        kube_client._api_client.call_api = Mock(side_effect=error)
        result = self._create(kube_client)
        assert result["accepted"] is False and result["reason"] == "unverifiable"
        kube_client._api_client.call_api.assert_called_once()

    def test_dry_run_predicts_without_a_request(self, dry_run_client):
        dry_run_client._api_client.call_api = Mock()
        result = self._create(dry_run_client)
        assert result["accepted"] is False and result["would_change"] is True and result["reason"] == "predicted"
        dry_run_client._api_client.call_api.assert_not_called()

    def test_failures_never_expose_exception_text(self, kube_client, caplog):
        secret = "R4-04-CREATE-SECRET token=abc123"
        exc = ApiException(status=500, reason=secret)
        exc.body = secret
        kube_client._api_client.call_api = Mock(side_effect=exc)
        with caplog.at_level("DEBUG", logger="acm_switchover"):
            result = self._create(kube_client)
        assert "abc123" not in json.dumps(result)
        assert "abc123" not in caplog.text

    _ACCEPTED = {
        "apiVersion": "cluster.open-cluster-management.io/v1beta1",
        "kind": "Restore",
        "metadata": {"name": "restore-acm-full", "uid": "u-1", "resourceVersion": "10", "generation": 1},
    }

    def test_the_guarded_create_binds_the_real_api_client_signature(self):
        """A single POST through the installed client, on every supported version: the request
        body is the Restore, the bearer token and the caller's timeout bound it."""
        pool_manager = _RecordingPoolManager(status=201, payload=self._ACCEPTED)

        result = _real_kube_client(pool_manager).create_restore_guarded(
            self._BODY, namespace="open-cluster-management-backup", timeout_seconds=11
        )

        assert (result["accepted"], result["reason"], result["uid"], result["generation"]) == (True, "ok", "u-1", 1)
        [call] = pool_manager.calls
        assert call["method"] == "POST"
        assert call["url"].endswith(
            "/apis/cluster.open-cluster-management.io/v1beta1/namespaces/open-cluster-management-backup/restores"
        )
        assert json.loads(call["body"]) == self._BODY
        assert {key.lower(): value for key, value in call["headers"].items()}["authorization"] == (
            "Bearer binding-test-token"
        )
        assert call["timeout"].total == 11

    @pytest.mark.parametrize(
        ("status", "body", "conflict", "reason"),
        [
            (409, b'{"kind": "Status", "code": 409}', True, "already_exists"),
            (403, b'{"kind": "Status", "code": 403}', False, "unverifiable"),
            (404, b'{"kind": "Status", "code": 404}', False, "unverifiable"),
            (500, b'{"kind": "Status", "code": 500}', False, "unverifiable"),
            (504, b"<html>", False, "unverifiable"),
        ],
    )
    def test_a_real_client_http_error_is_never_acceptance(self, status, body, conflict, reason):
        pool_manager = _RecordingPoolManager(status=status, body=body)

        result = self._create(_real_kube_client(pool_manager))

        assert (result["accepted"], result["http_accepted"], result["conflict"], result["reason"]) == (
            False,
            False,
            conflict,
            reason,
        )
        assert len(pool_manager.calls) == 1

    def test_a_real_client_undecodable_error_body_is_never_acceptance(self):
        """Before 37 the client decodes an error body while raising it, so a 409 whose body is
        not UTF-8 is unverifiable there and `already_exists` on 37: never accepted on either."""
        pool_manager = _RecordingPoolManager(status=409, body=b"\xff\xfe")

        result = self._create(_real_kube_client(pool_manager))

        assert (result["accepted"], result["http_accepted"], result["changed"]) == (False, False, False)
        assert result["reason"] in ("already_exists", "unverifiable")
        assert len(pool_manager.calls) == 1

    @pytest.mark.parametrize(("body", "content_type"), _MALFORMED_2XX_BODIES)
    def test_a_real_client_malformed_2xx_body_is_not_acceptance(self, body, content_type):
        """The POST may have landed, so this is never a failure to compensate for and never an
        accepted create: it is `malformed_response`, with no identity, on every client version."""
        pool_manager = _RecordingPoolManager(status=201, body=body, content_type=content_type)

        result = self._create(_real_kube_client(pool_manager))

        assert (result["accepted"], result["http_accepted"], result["changed"], result["reason"]) == (
            False,
            True,
            False,
            "malformed_response",
        )
        assert (result["uid"], result["resource_version"], result["generation_reported"]) == (None, None, False)
        assert len(pool_manager.calls) == 1

    @pytest.mark.parametrize("constant", _NON_JSON_CONSTANTS)
    def test_a_real_client_non_json_constant_is_not_acceptance(self, constant):
        pool_manager = _RecordingPoolManager(status=201, body=_with_constant(self._ACCEPTED, constant))

        result = self._create(_real_kube_client(pool_manager))

        assert (result["accepted"], result["http_accepted"], result["reason"]) == (False, True, "malformed_response")
        assert (result["uid"], result["resource_version"], result["generation_reported"]) == (None, None, False)
        assert len(pool_manager.calls) == 1

    @pytest.mark.parametrize(
        "identity",
        [
            {"uid": " ", "resourceVersion": "10"},
            {"uid": " u-1", "resourceVersion": "10"},
            {"uid": "u-1", "resourceVersion": " "},
            {"uid": "u-1", "resourceVersion": "10\n"},
        ],
    )
    def test_a_real_client_padded_response_identity_is_not_acceptance(self, identity):
        body = dict(self._ACCEPTED, metadata={"name": "restore-acm-full", "generation": 1, **identity})
        pool_manager = _RecordingPoolManager(status=201, payload=body)

        result = self._create(_real_kube_client(pool_manager))

        assert (result["accepted"], result["http_accepted"], result["reason"]) == (False, True, "malformed_response")
        assert (result["uid"], result["resource_version"], result["generation_reported"]) == (None, None, False)
        assert len(pool_manager.calls) == 1

    def test_a_real_client_body_read_failure_is_not_acceptance(self):
        pool_manager = _RecordingPoolManager(status=201, body=_UnreadableBody(ProtocolError("reset")))

        result = self._create(_real_kube_client(pool_manager))

        assert (result["accepted"], result["http_accepted"], result["reason"]) == (False, False, "unverifiable")
        assert len(pool_manager.calls) == 1


class TestApiResourceServed:
    """The public discovery check: the Restore status subresource is served, absent, or unverifiable."""

    def test_status_subresource_is_asserted_through_discovery(self):
        body = {
            "kind": "APIResourceList",
            "groupVersion": "cluster.open-cluster-management.io/v1beta1",
            "resources": [{"name": "restores", "kind": "Restore"}, {"name": "restores/status", "kind": "Restore"}],
        }
        client = TestDiscoveryProver()._client(Mock(return_value=TestDiscoveryProver._body(body)))
        served = client.api_resource_served("cluster.open-cluster-management.io", "v1beta1", "restores/status")
        assert served.status is StrictReadStatus.ITEMS
        body["resources"] = body["resources"][:1]
        client = TestDiscoveryProver()._client(Mock(return_value=TestDiscoveryProver._body(body)))
        absent = client.api_resource_served("cluster.open-cluster-management.io", "v1beta1", "restores/status")
        assert absent.status is StrictReadStatus.CRD_ABSENT
