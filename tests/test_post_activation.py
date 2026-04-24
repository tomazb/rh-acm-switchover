"""Unit tests for modules/post_activation.py.

Tests cover PostActivationVerification class for verifying switchover success.
"""

import base64
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest
import yaml
from kubernetes.client.rest import ApiException

# Add parent to path to import modules directly
sys.path.insert(0, str(Path(__file__).parent.parent))

import modules.post_activation as post_activation_module
from lib.constants import (
    CLUSTER_VERIFY_INTERVAL,
    DEFAULT_KUBECONFIG_SIZE,
    DISABLE_AUTO_IMPORT_ANNOTATION,
    LOCAL_CLUSTER_NAME,
    MANAGED_CLUSTER_AGENT_NAMESPACE,
    MAX_KUBECONFIG_SIZE,
    OBSERVABILITY_NAMESPACE,
)
from lib.exceptions import SwitchoverError
from lib.waiter import WaitConditionResult

PostActivationVerification = post_activation_module.PostActivationVerification


def create_mock_step_context(is_step_completed_func, mark_step_completed_func):
    """Create a mock step context manager that mimics StepContext behavior."""

    @contextmanager
    def mock_step(step_name, logger=None):
        if is_step_completed_func(step_name):
            if logger:
                logger.info("Step already completed: %s", step_name)
            yield False
        else:
            yield True
            mark_step_completed_func(step_name)

    return mock_step


@pytest.fixture
def mock_secondary_client():
    """Create a mock KubeClient for secondary hub."""
    return Mock()


@pytest.fixture
def mock_state_manager():
    """Create a mock StateManager with step() context manager support."""
    mock = Mock()
    mock.is_step_completed.return_value = False
    # Set up step() to return a proper context manager
    mock.step.side_effect = create_mock_step_context(
        mock.is_step_completed,
        mock.mark_step_completed,
    )
    return mock


@pytest.fixture
def post_verify_with_obs(mock_secondary_client, mock_state_manager):
    """Create PostActivationVerification instance with observability."""
    mock_secondary_client.get_route_host.return_value = "grafana.example.com"
    return PostActivationVerification(
        secondary_client=mock_secondary_client,
        state_manager=mock_state_manager,
        has_observability=True,
    )


@pytest.fixture
def post_verify_no_obs(mock_secondary_client, mock_state_manager):
    """Create PostActivationVerification instance without observability."""
    return PostActivationVerification(
        secondary_client=mock_secondary_client,
        state_manager=mock_state_manager,
        has_observability=False,
    )


@pytest.mark.unit
class TestPostActivationVerification:
    """Tests for PostActivationVerification class."""

    def test_build_managed_cluster_clients_disables_kubeconfig_persistence(
        self, mock_secondary_client, mock_state_manager
    ):
        """Per-context clients should not persist refreshed credentials back to kubeconfig."""
        verify = PostActivationVerification(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            has_observability=False,
        )
        api_client = Mock(name="api_client")
        core_v1 = Mock(name="core_v1")
        apps_v1 = Mock(name="apps_v1")

        with patch(
            "modules.post_activation.config.new_client_from_config",
            return_value=api_client,
        ) as new_client:
            with patch("modules.post_activation.client.CoreV1Api", return_value=core_v1) as core_ctor:
                with patch("modules.post_activation.client.AppsV1Api", return_value=apps_v1) as apps_ctor:
                    result = verify._build_managed_cluster_clients("managed-context")

        new_client.assert_called_once_with(context="managed-context", persist_config=False)
        core_ctor.assert_called_once_with(api_client=api_client)
        apps_ctor.assert_called_once_with(api_client=api_client)
        assert result == (core_v1, apps_v1)

    def test_klusterlet_verification_bypasses_kubeconfig_size_limit(self, mock_secondary_client, mock_state_manager):
        """Klusterlet verification should bypass kubeconfig size limits."""
        verify = PostActivationVerification(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            has_observability=False,
        )

        with patch.object(verify, "_get_hub_api_server", return_value="https://new-hub"):
            with patch.object(verify, "_load_kubeconfig_data", return_value={}) as mock_load:
                verify._verify_klusterlet_connections()

        mock_load.assert_called_with(max_size=0)

    @patch("modules.post_activation.wait_for_condition")
    def test_verify_success_with_observability(
        self, mock_wait, post_verify_with_obs, mock_secondary_client, mock_state_manager
    ):
        """Test successful verification with observability."""
        mock_wait.return_value = True
        mock_secondary_client.wait_for_pods_ready.return_value = True

        # Mock clusters
        mock_secondary_client.list_custom_resources.return_value = [
            {
                "metadata": {"name": "cluster1"},
                "status": {
                    "conditions": [
                        {"type": "ManagedClusterConditionAvailable", "status": "True"},
                        {"type": "HubAcceptedManagedCluster", "status": "True"},
                    ]
                },
            }
        ]

        mock_secondary_client.get_pods.side_effect = [
            [
                {
                    "metadata": {"name": "obs-api"},
                    "status": {
                        "phase": "Running",
                        "startTime": "2024-01-01T00:00:00Z",
                        "conditions": [{"type": "Ready", "status": "True"}],
                    },
                }
            ],
            [
                {
                    "metadata": {"name": "pod1"},
                    "status": {
                        "phase": "Running",
                        "conditions": [{"type": "Ready", "status": "True"}],
                    },
                }
            ],
            [{"metadata": {"name": "metrics-pod"}}],
        ]

        mock_secondary_client.get_deployment.return_value = {
            "metadata": {"generation": 1},
            "spec": {"replicas": 2},
            "status": {
                "observedGeneration": 1,
                "replicas": 2,
                "updatedReplicas": 2,
                "availableReplicas": 2,
                "readyReplicas": 2,
                "unavailableReplicas": 0,
            },
        }
        mock_secondary_client.rollout_restart_deployment.return_value = {"status": "ok"}

        result = post_verify_with_obs.verify()

        assert result is True
        assert mock_state_manager.mark_step_completed.call_count >= 4

    @patch("modules.post_activation.wait_for_condition")
    def test_verify_success_without_observability(
        self, mock_wait, post_verify_no_obs, mock_secondary_client, mock_state_manager
    ):
        """Test successful verification without observability."""
        mock_wait.return_value = True

        mock_secondary_client.list_custom_resources.return_value = [
            {
                "metadata": {"name": "cluster1"},
                "status": {
                    "conditions": [
                        {"type": "ManagedClusterConditionAvailable", "status": "True"},
                        {"type": "HubAcceptedManagedCluster", "status": "True"},
                    ]
                },
            }
        ]

        result = post_verify_no_obs.verify()

        assert result is True
        # Should not verify observability
        mock_secondary_client.rollout_restart_deployment.assert_not_called()

    def test_verify_steps_already_completed(self, post_verify_with_obs, mock_state_manager):
        """Test skipping already completed steps."""
        mock_state_manager.is_step_completed.return_value = True

        result = post_verify_with_obs.verify()

        assert result is True

    @patch("modules.post_activation.wait_for_condition")
    def test_verify_managed_clusters_all_available(self, mock_wait, post_verify_with_obs, mock_secondary_client):
        """Test when all managed clusters are available."""
        mock_wait.return_value = True

        mock_secondary_client.list_custom_resources.return_value = [
            {
                "metadata": {"name": "cluster1"},
                "status": {
                    "conditions": [
                        {"type": "ManagedClusterConditionAvailable", "status": "True"},
                        {"type": "HubAcceptedManagedCluster", "status": "True"},
                    ]
                },
            },
            {
                "metadata": {"name": "cluster2"},
                "status": {
                    "conditions": [
                        {"type": "ManagedClusterConditionAvailable", "status": "True"},
                        {"type": "HubAcceptedManagedCluster", "status": "True"},
                    ]
                },
            },
        ]

        post_verify_with_obs._verify_managed_clusters_connected()

        # Should complete successfully
        assert mock_wait.called

    @patch("modules.post_activation.wait_for_condition")
    def test_verify_managed_clusters_timeout(self, mock_wait, post_verify_with_obs, mock_secondary_client):
        """Test timeout while waiting for clusters."""
        mock_wait.return_value = False  # Timeout

        mock_secondary_client.list_custom_resources.return_value = [
            {
                "metadata": {"name": "cluster1"},
                "status": {"conditions": [{"type": "ManagedClusterConditionAvailable", "status": "False"}]},
            }
        ]

        # Should raise exception
        with pytest.raises(Exception, match="timeout|Timeout"):
            post_verify_with_obs._verify_managed_clusters_connected()

    @patch("modules.post_activation.wait_for_condition")
    def test_verify_clusters_triggers_klusterlet_fix_on_timeout(
        self, mock_wait, post_verify_no_obs, mock_secondary_client, mock_state_manager
    ):
        """Test that klusterlet fix is triggered when initial cluster wait times out.

        This simulates a switchover where klusterlets are still connected to the old hub.
        The verify() method should:
        1. Wait briefly (120s) for clusters - timeout
        2. Trigger klusterlet verification/fix
        3. Wait again for clusters to reconnect
        """
        call_count = [0]

        def wait_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                # First call (brief wait) - timeout
                return False
            else:
                # Subsequent calls - success
                return True

        mock_wait.side_effect = wait_side_effect

        # Mock list_custom_resources for managed clusters
        mock_secondary_client.list_custom_resources.return_value = [
            {
                "metadata": {"name": "cluster1"},
                "spec": {"managedClusterClientConfigs": [{"url": "https://api.cluster1:6443"}]},
                "status": {
                    "conditions": [
                        {"type": "ManagedClusterConditionAvailable", "status": "True"},
                        {"type": "ManagedClusterJoined", "status": "True"},
                    ]
                },
            }
        ]

        # Mock state manager to allow all steps to run
        mock_state_manager.is_step_completed.return_value = False

        result = post_verify_no_obs.verify()

        assert result is True
        # wait_for_condition should be called at least twice
        # (initial brief wait + wait after klusterlet fix)
        assert mock_wait.call_count >= 2
        # verify_klusterlet_connections should be recorded exactly once even when the
        # fallback path runs it before the later optional verification block.
        calls = [call[0][0] for call in mock_state_manager.mark_step_completed.call_args_list]
        assert calls.count("verify_klusterlet_connections") == 1

    @patch("modules.post_activation.wait_for_condition")
    def test_verify_no_clusters(self, mock_wait, post_verify_with_obs, mock_secondary_client):
        """Test when no managed clusters exist - should timeout waiting for clusters."""
        mock_secondary_client.list_custom_resources.return_value = []

        def capture_wait(*args, **kwargs):
            condition_fn = kwargs.get("condition_fn", args[1])
            result = condition_fn()
            assert isinstance(result, WaitConditionResult)
            assert result.done is False
            assert result.public_detail == "no ManagedClusters found"
            return False

        mock_wait.side_effect = capture_wait

        # Should raise SwitchoverError with timeout message
        with pytest.raises(SwitchoverError, match="Timeout waiting for ManagedClusters"):
            post_verify_with_obs._verify_managed_clusters_connected(timeout=1)

        # Verify wait_for_condition was called with expected parameters
        mock_wait.assert_called_once()
        call_args = mock_wait.call_args
        assert call_args[0][0] == "ManagedCluster connections"  # description
        assert call_args[1]["timeout"] == 1
        assert call_args[1]["interval"] == CLUSTER_VERIFY_INTERVAL

    @patch("modules.post_activation.wait_for_condition")
    def test_wait_for_secret_visibility_returns_public_wait_result(
        self, mock_wait, post_verify_no_obs, mock_secondary_client
    ):
        """Secret visibility poller should return an explicit public wait result."""
        mock_v1 = Mock()
        mock_v1.read_namespaced_secret.side_effect = ApiException(status=404)

        def capture_wait(*_args, **kwargs):
            condition_fn = kwargs["condition_fn"]
            result = condition_fn()
            assert isinstance(result, WaitConditionResult)
            assert result.done is False
            assert result.public_detail == "secret not found"
            return False

        mock_wait.side_effect = capture_wait

        post_verify_no_obs._wait_for_secret_visibility(mock_v1, "cluster-a")

        mock_wait.assert_called_once()

    @patch("modules.post_activation.time.sleep")
    def test_restart_observatorium_api_waits_for_full_deployment_rollout(
        self, mock_sleep, post_verify_with_obs, mock_secondary_client
    ):
        """Restart gate must wait for the full Deployment replica target, not just one ready pod."""
        mock_secondary_client.get_deployment.side_effect = [
            {
                "metadata": {"generation": 2},
                "spec": {"replicas": 2},
                "status": {
                    "observedGeneration": 1,
                    "replicas": 2,
                    "updatedReplicas": 1,
                    "availableReplicas": 1,
                    "readyReplicas": 1,
                    "unavailableReplicas": 1,
                },
            },
            {
                "metadata": {"generation": 2},
                "spec": {"replicas": 2},
                "status": {
                    "observedGeneration": 2,
                    "replicas": 2,
                    "updatedReplicas": 0,
                    "availableReplicas": 2,
                    "readyReplicas": 2,
                    "unavailableReplicas": 0,
                },
            },
            {
                "metadata": {"generation": 2},
                "spec": {"replicas": 2},
                "status": {
                    "observedGeneration": 2,
                    "replicas": 2,
                    "updatedReplicas": 2,
                    "availableReplicas": 2,
                    "readyReplicas": 2,
                    "unavailableReplicas": 0,
                },
            },
        ]
        mock_secondary_client.get_pods.return_value = [
            {
                "metadata": {"name": "api"},
                "status": {"startTime": "2024-01-01T00:00:00Z"},
            }
        ]
        mock_secondary_client.rollout_restart_deployment.return_value = {"status": "ok"}

        post_verify_with_obs._restart_observatorium_api()

        mock_secondary_client.rollout_restart_deployment.assert_called_once_with(
            namespace=OBSERVABILITY_NAMESPACE,
            name=post_activation_module.OBSERVATORIUM_API_DEPLOYMENT,
        )
        assert mock_secondary_client.get_deployment.call_count == 3
        mock_secondary_client.wait_for_pods_ready.assert_not_called()
        mock_secondary_client.get_pods.assert_called()
        assert mock_sleep.call_count == 2

    def test_restart_observatorium_api_404_without_reason_is_graceful(
        self, post_verify_with_obs, mock_secondary_client
    ):
        """ApiException(status=404) without 'Not Found' in reason must be treated as not-found, not re-raised.

        Bug: current code checks 'not found' in str(e).lower() — ApiException(status=404) without
        an explicit reason contains 'Reason: None', so 'not found' is absent and the error is
        incorrectly re-raised. Fix: check e.status == 404 instead.
        """
        mock_secondary_client.rollout_restart_deployment.side_effect = ApiException(status=404)

        # Must not raise — a missing deployment should log a warning, not crash the step
        post_verify_with_obs._restart_observatorium_api()

    def test_restart_observatorium_api_non_404_with_not_found_text_reraises(
        self, post_verify_with_obs, mock_secondary_client
    ):
        """ApiException with non-404 status containing 'not found' in reason must re-raise.

        Bug: current code checks 'not found' in str(e).lower() — a 403 with 'namespace not found'
        in the reason is incorrectly swallowed. Fix: check e.status == 404 instead.
        """
        exc = ApiException(status=403, reason="namespace not found in service account token")
        mock_secondary_client.rollout_restart_deployment.side_effect = exc

        with pytest.raises(ApiException) as exc_info:
            post_verify_with_obs._restart_observatorium_api()

        assert exc_info.value.status == 403

    @patch("modules.post_activation.wait_for_condition")
    def test_verify_observability_pods_all_ready(self, mock_wait, post_verify_with_obs, mock_secondary_client):
        """Test when all observability pods are ready."""
        mock_secondary_client.get_pods.return_value = [
            {
                "metadata": {"name": "pod1"},
                "status": {
                    "phase": "Running",
                    "conditions": [{"type": "Ready", "status": "True"}],
                },
            }
        ]

        # Method should complete without error
        post_verify_with_obs._verify_observability_pods()

        # Verify get_pods was called
        mock_secondary_client.get_pods.assert_called_once()

    @patch("modules.post_activation.wait_for_condition")
    def test_verify_observability_pods_none_found(self, mock_wait, post_verify_with_obs, mock_secondary_client):
        """Test when no observability pods are found."""
        mock_wait.return_value = False
        mock_secondary_client.get_pods.return_value = []

        # Should handle gracefully with warning, not raise exception
        post_verify_with_obs._verify_observability_pods()

        # Verify get_pods was called
        mock_secondary_client.get_pods.assert_called_once()

    @patch("modules.post_activation.logger")
    def test_verify_observability_pods_detects_crashloop(
        self, mock_logger, post_verify_with_obs, mock_secondary_client
    ):
        """CrashLoopBackOff pods should be reported explicitly."""
        mock_secondary_client.get_pods.return_value = [
            {
                "metadata": {"name": "obs-pod"},
                "status": {
                    "phase": "Running",
                    "conditions": [{"type": "Ready", "status": "False"}],
                    "containerStatuses": [
                        {
                            "name": "collector",
                            "state": {
                                "waiting": {
                                    "reason": "CrashLoopBackOff",
                                    "message": "Back-off restarting failed container",
                                }
                            },
                        }
                    ],
                },
            }
        ]

        post_verify_with_obs._verify_observability_pods()

        # With lazy logging, format string is args[0] and values are args[1:]
        # Check all args for CrashLoopBackOff
        assert any(
            any("CrashLoopBackOff" in str(arg) for arg in call.args) for call in mock_logger.warning.call_args_list
        )

    @patch("modules.post_activation.wait_for_condition")
    def test_verify_metrics_collection(self, mock_wait, post_verify_with_obs, mock_secondary_client):
        """Test verifying metrics collection."""
        mock_wait.return_value = True

        # Mock get_pods to return a list
        mock_secondary_client.get_pods.return_value = [{"metadata": {"name": "metrics-pod"}}]

        # This method just logs information, no exception expected
        post_verify_with_obs._verify_metrics_collection()

        # Verify get_pods was called
        assert mock_secondary_client.get_pods.called

    @patch("modules.post_activation.logger")
    def test_log_grafana_route_found(self, mock_logger, post_verify_with_obs, mock_secondary_client):
        """Grafana route should be logged when host detected."""
        mock_secondary_client.get_route_host.return_value = "grafana.example.com"

        post_verify_with_obs._log_grafana_route()

        mock_logger.info.assert_any_call(
            "Grafana route detected: https://%s (namespace: %s)",
            "grafana.example.com",
            OBSERVABILITY_NAMESPACE,
        )

    @patch("modules.post_activation.logger")
    def test_log_grafana_route_missing(self, mock_logger, post_verify_with_obs, mock_secondary_client):
        """Missing Grafana route should emit warning."""
        mock_secondary_client.get_route_host.return_value = None

        post_verify_with_obs._log_grafana_route()

        mock_logger.warning.assert_any_call("Grafana route not found in Observability namespace")

    def test_verify_error_handling(self, post_verify_with_obs, mock_secondary_client, mock_state_manager):
        """Test error handling during verification."""
        mock_secondary_client.list_custom_resources.side_effect = Exception("API error")

        result = post_verify_with_obs.verify()

        assert result is False
        mock_state_manager.add_error.assert_called_once()

    @pytest.mark.parametrize("has_obs", [True, False])
    @patch("modules.post_activation.wait_for_condition")
    def test_observability_verification_conditional(
        self, mock_wait, mock_secondary_client, mock_state_manager, has_obs
    ):
        """Test that observability verification is conditional."""
        mock_wait.return_value = True

        verify = PostActivationVerification(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            has_observability=has_obs,
        )

        mock_secondary_client.list_custom_resources.return_value = [
            {
                "metadata": {"name": "cluster1"},
                "status": {
                    "conditions": [
                        {"type": "ManagedClusterConditionAvailable", "status": "True"},
                        {"type": "HubAcceptedManagedCluster", "status": "True"},
                    ]
                },
            }
        ]

        with patch.object(verify, "_restart_observatorium_api") as mock_restart:
            verify.verify()

            if has_obs:
                mock_restart.assert_called_once()
            else:
                mock_restart.assert_not_called()

    def test_verify_disable_auto_import_cleanup_success(self, post_verify_with_obs, mock_secondary_client):
        """disable-auto-import verification should pass when annotations removed."""
        mock_secondary_client.list_custom_resources.return_value = [
            {"metadata": {"name": "cluster1", "annotations": {}}},
            {"metadata": {"name": "local-cluster"}},
        ]

        post_verify_with_obs._verify_disable_auto_import_cleared()

    def test_verify_disable_auto_import_cleanup_failure(self, post_verify_with_obs, mock_secondary_client):
        """disable-auto-import verification should fail when annotation remains."""
        mock_secondary_client.list_custom_resources.return_value = [
            {
                "metadata": {
                    "name": "cluster1",
                    "annotations": {DISABLE_AUTO_IMPORT_ANNOTATION: ""},
                }
            }
        ]

        with pytest.raises(Exception):
            post_verify_with_obs._verify_disable_auto_import_cleared()

    def test_verify_disable_auto_import_cleanup_patches_with_supported_keyword(
        self, post_verify_with_obs, mock_secondary_client
    ):
        """disable-auto-import cleanup must call KubeClient.patch_custom_resource with patch=."""
        mock_secondary_client.list_custom_resources.side_effect = [
            [
                {
                    "metadata": {
                        "name": "cluster1",
                        "annotations": {DISABLE_AUTO_IMPORT_ANNOTATION: ""},
                    }
                },
                {"metadata": {"name": "local-cluster"}},
            ],
            [
                {"metadata": {"name": "cluster1", "annotations": {}}},
                {"metadata": {"name": "local-cluster"}},
            ],
        ]

        post_verify_with_obs._verify_disable_auto_import_cleared()

        mock_secondary_client.patch_custom_resource.assert_called_once()
        kwargs = mock_secondary_client.patch_custom_resource.call_args.kwargs
        assert kwargs["name"] == "cluster1"
        assert kwargs["patch"] == {"metadata": {"annotations": {DISABLE_AUTO_IMPORT_ANNOTATION: None}}}
        assert "body" not in kwargs


@pytest.mark.unit
class TestKlusterletReconnect:
    """Test cases for force klusterlet reconnect functionality."""

    def _make_verify(self, mock_secondary_client, mock_state_manager):
        return PostActivationVerification(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            has_observability=False,
        )

    def test_force_klusterlet_reconnect_success(self, mock_secondary_client, mock_state_manager):
        """Test successful klusterlet reconnect with import secret."""
        import base64

        verify = self._make_verify(mock_secondary_client, mock_state_manager)

        import_docs = """---
apiVersion: v1
kind: Secret
metadata:
  name: bootstrap-hub-kubeconfig
  namespace: open-cluster-management-agent
data:
  kubeconfig: dGVzdAo=
"""
        mock_secondary_client.get_secret.return_value = {
            "data": {"import.yaml": base64.b64encode(import_docs.encode()).decode()}
        }

        mock_v1 = Mock()
        mock_apps_v1 = Mock()
        mock_v1.delete_namespaced_secret.side_effect = ApiException(status=404)
        mock_v1.create_namespaced_secret.return_value = None
        mock_apps_v1.patch_namespaced_deployment.return_value = None

        verify._build_managed_cluster_clients = Mock(return_value=(mock_v1, mock_apps_v1))

        with patch("modules.post_activation.wait_for_condition", return_value=True):
            result = verify._force_klusterlet_reconnect("test-cluster", "test-context")

        assert result is True
        verify._build_managed_cluster_clients.assert_called_once_with("test-context")
        mock_secondary_client.get_secret.assert_called_once_with(namespace="test-cluster", name="test-cluster-import")

    def test_force_klusterlet_reconnect_no_secret(self, mock_secondary_client, mock_state_manager):
        """Test klusterlet reconnect when import secret not found."""
        verify = self._make_verify(mock_secondary_client, mock_state_manager)
        mock_secondary_client.get_secret.return_value = None

        result = verify._force_klusterlet_reconnect("test-cluster", "test-context")

        assert result is False

    def test_parallel_workers_use_isolated_clients(self, mock_secondary_client, mock_state_manager):
        """Each parallel worker must receive its own ApiClient; no global context mutation."""
        import threading

        verify = self._make_verify(mock_secondary_client, mock_state_manager)

        call_log = []
        call_lock = threading.Lock()

        def fake_build_clients(context_name):
            mock_v1 = Mock(name=f"v1-{context_name}")
            mock_apps_v1 = Mock(name=f"apps_v1-{context_name}")
            with call_lock:
                call_log.append((context_name, mock_v1))
            return mock_v1, mock_apps_v1

        verify._build_managed_cluster_clients = fake_build_clients

        import base64

        import_docs = """---
apiVersion: v1
kind: Secret
metadata:
  name: bootstrap-hub-kubeconfig
  namespace: open-cluster-management-agent
data:
  kubeconfig: dGVzdAo=
"""
        mock_secondary_client.get_secret.return_value = {
            "data": {"import.yaml": base64.b64encode(import_docs.encode()).decode()}
        }

        with patch("modules.post_activation.wait_for_condition", return_value=True):
            from concurrent.futures import ThreadPoolExecutor, as_completed

            contexts = ["ctx-cluster-a", "ctx-cluster-b"]
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [
                    executor.submit(verify._force_klusterlet_reconnect, f"cluster-{i}", ctx)
                    for i, ctx in enumerate(contexts)
                ]
                results = [f.result() for f in as_completed(futures)]
        assert all(results), f"Expected all workers to return True, got {results}"

        assert len(call_log) == 2
        ctx_names = {entry[0] for entry in call_log}
        assert ctx_names == set(contexts), "Each worker must use its own named context"
        first_client = next(client for ctx, client in call_log if ctx == "ctx-cluster-a")
        second_client = next(client for ctx, client in call_log if ctx == "ctx-cluster-b")
        assert first_client is not second_client, "Each worker must receive a distinct CoreV1Api instance"


@pytest.mark.integration
class TestPostActivationVerificationIntegration:
    """Integration tests for PostActivationVerification."""

    @patch("modules.post_activation.wait_for_condition")
    def test_full_verification_workflow(self, mock_wait, mock_secondary_client, tmp_path):
        """Test complete verification workflow with real StateManager."""
        from lib.utils import Phase, StateManager

        mock_wait.return_value = True

        state = StateManager(str(tmp_path / "state.json"))
        state.set_phase(Phase.POST_ACTIVATION)

        verify = PostActivationVerification(
            secondary_client=mock_secondary_client,
            state_manager=state,
            has_observability=True,
        )

        # Mock successful flow
        mock_secondary_client.list_custom_resources.return_value = [
            {
                "metadata": {"name": "cluster1"},
                "status": {
                    "conditions": [
                        {"type": "ManagedClusterConditionAvailable", "status": "True"},
                        {"type": "HubAcceptedManagedCluster", "status": "True"},
                    ]
                },
            }
        ]
        mock_secondary_client.get_pods.return_value = [{"metadata": {"name": "pod1"}, "status": {"phase": "Running"}}]
        mock_secondary_client.get_deployment.return_value = {
            "metadata": {"generation": 1},
            "spec": {"replicas": 1},
            "status": {
                "observedGeneration": 1,
                "replicas": 1,
                "updatedReplicas": 1,
                "availableReplicas": 1,
                "readyReplicas": 1,
                "unavailableReplicas": 0,
            },
        }
        mock_secondary_client.rollout_restart_deployment.return_value = {"status": "ok"}

        result = verify.verify()

        assert result is True
        assert state.is_step_completed("verify_clusters_connected")
        assert state.is_step_completed("verify_observability_pods")


@pytest.mark.unit
class TestLoadKubeconfigData:
    """Tests for _load_kubeconfig_data method."""

    def test_load_single_kubeconfig(self, mock_secondary_client, mock_state_manager, tmp_path):
        """Test loading a single kubeconfig file."""
        kubeconfig = tmp_path / "config"
        kubeconfig.write_text("""
apiVersion: v1
clusters:
- cluster:
    server: https://api.cluster1.example.com:6443
  name: cluster1
contexts:
- context:
    cluster: cluster1
    user: admin
  name: admin@cluster1
users:
- name: admin
  user:
    token: test-token
""")
        verify = PostActivationVerification(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            has_observability=False,
        )

        with patch.dict("os.environ", {"KUBECONFIG": str(kubeconfig)}):
            data = verify._load_kubeconfig_data()

        assert len(data["clusters"]) == 1
        assert len(data["contexts"]) == 1
        assert data["clusters"][0]["name"] == "cluster1"
        assert data["contexts"][0]["name"] == "admin@cluster1"

    def test_load_multiple_kubeconfigs(self, mock_secondary_client, mock_state_manager, tmp_path):
        """Test loading and merging multiple kubeconfig files."""
        kubeconfig1 = tmp_path / "config1"
        kubeconfig1.write_text("""
apiVersion: v1
clusters:
- cluster:
    server: https://api.cluster1.example.com:6443
  name: cluster1
contexts:
- context:
    cluster: cluster1
    user: admin1
  name: admin@cluster1
users:
- name: admin1
  user:
    token: token1
""")
        kubeconfig2 = tmp_path / "config2"
        kubeconfig2.write_text("""
apiVersion: v1
clusters:
- cluster:
    server: https://api.cluster2.example.com:6443
  name: cluster2
contexts:
- context:
    cluster: cluster2
    user: admin2
  name: admin@cluster2
users:
- name: admin2
  user:
    token: token2
""")
        verify = PostActivationVerification(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            has_observability=False,
        )

        # Test colon-separated KUBECONFIG paths
        with patch.dict("os.environ", {"KUBECONFIG": f"{kubeconfig1}:{kubeconfig2}"}):
            data = verify._load_kubeconfig_data()

        assert len(data["clusters"]) == 2
        assert len(data["contexts"]) == 2
        cluster_names = [c["name"] for c in data["clusters"]]
        assert "cluster1" in cluster_names
        assert "cluster2" in cluster_names

    def test_load_kubeconfig_missing_file(self, mock_secondary_client, mock_state_manager, tmp_path):
        """Test graceful handling of missing kubeconfig file."""
        existing = tmp_path / "exists"
        existing.write_text("""
apiVersion: v1
clusters:
- cluster:
    server: https://api.exists.example.com:6443
  name: exists
contexts: []
users: []
""")
        missing = tmp_path / "does_not_exist"

        verify = PostActivationVerification(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            has_observability=False,
        )

        # One existing, one missing - should still work
        with patch.dict("os.environ", {"KUBECONFIG": f"{existing}:{missing}"}):
            data = verify._load_kubeconfig_data()

        assert len(data["clusters"]) == 1
        assert data["clusters"][0]["name"] == "exists"

    def test_load_kubeconfig_default_path(self, mock_secondary_client, mock_state_manager, tmp_path):
        """Test falling back to default ~/.kube/config when KUBECONFIG not set."""
        verify = PostActivationVerification(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            has_observability=False,
        )

        # Clear KUBECONFIG to test default path behavior
        with patch.dict("os.environ", {}, clear=True):
            # This will try ~/.kube/config which may or may not exist
            # The method should not raise an exception
            data = verify._load_kubeconfig_data()
            assert isinstance(data, dict)


# ---------------------------------------------------------------------------
# Helper to build a PostActivationVerification quickly
# ---------------------------------------------------------------------------
def _make_pav(secondary, state, obs=False, dry_run=False):
    secondary.context = "secondary-ctx"
    return PostActivationVerification(
        secondary_client=secondary,
        state_manager=state,
        has_observability=obs,
        dry_run=dry_run,
    )


def _kubeconfig_yaml(clusters=None, contexts=None):
    """Build a minimal kubeconfig YAML string."""
    data = {
        "apiVersion": "v1",
        "clusters": clusters or [],
        "contexts": contexts or [],
        "users": [],
    }
    return yaml.dump(data)


# ========================================================================
# 1. Klusterlet parallel verification (_verify_klusterlet_connections)
# ========================================================================
@pytest.mark.unit
class TestKlusterletParallelVerification:
    """Tests for _verify_klusterlet_connections parallel execution."""

    def test_filters_local_cluster(self, mock_secondary_client, mock_state_manager):
        """local-cluster should be excluded from klusterlet verification."""
        pav = _make_pav(mock_secondary_client, mock_state_manager)

        mock_secondary_client.list_custom_resources.return_value = [
            {
                "metadata": {"name": LOCAL_CLUSTER_NAME},
                "spec": {"managedClusterClientConfigs": [{"url": "https://api.local:6443"}]},
            },
        ]

        with patch.object(pav, "_get_hub_api_server", return_value="https://hub:6443"):
            with patch.object(
                pav,
                "_load_kubeconfig_data",
                return_value={"contexts": [], "clusters": []},
            ):
                with patch.object(pav, "_check_klusterlet_connection") as mock_check:
                    pav._verify_klusterlet_connections()

        mock_check.assert_not_called()

    def test_categorizes_verified_wrong_hub_unreachable(self, mock_secondary_client, mock_state_manager):
        """Results should be categorized into verified / wrong_hub / unreachable."""
        pav = _make_pav(mock_secondary_client, mock_state_manager)

        mock_secondary_client.list_custom_resources.return_value = [
            {
                "metadata": {"name": "c1"},
                "spec": {"managedClusterClientConfigs": [{"url": "https://api.c1:6443"}]},
            },
            {
                "metadata": {"name": "c2"},
                "spec": {"managedClusterClientConfigs": [{"url": "https://api.c2:6443"}]},
            },
            {
                "metadata": {"name": "c3"},
                "spec": {"managedClusterClientConfigs": [{"url": "https://api.c3:6443"}]},
            },
        ]

        kube_data = {
            "contexts": [
                {"name": "ctx-c1", "context": {"cluster": "kc1"}},
                {"name": "ctx-c2", "context": {"cluster": "kc2"}},
                {"name": "ctx-c3", "context": {"cluster": "kc3"}},
            ],
            "clusters": [
                {"name": "kc1", "cluster": {"server": "https://api.c1:6443"}},
                {"name": "kc2", "cluster": {"server": "https://api.c2:6443"}},
                {"name": "kc3", "cluster": {"server": "https://api.c3:6443"}},
            ],
        }

        def fake_check(ctx, name, hub):
            return {"c1": "verified", "c2": "wrong_hub", "c3": "unreachable"}[name]

        with patch.object(pav, "_get_hub_api_server", return_value="https://hub:6443"):
            with patch.object(pav, "_load_kubeconfig_data", return_value=kube_data):
                with patch.object(pav, "_check_klusterlet_connection", side_effect=fake_check):
                    with patch.object(pav, "_force_klusterlet_reconnect", return_value=True) as mock_fix:
                        pav._verify_klusterlet_connections()

        # wrong_hub cluster c2 should be fixed
        mock_fix.assert_called_once()
        assert mock_fix.call_args[0][0] == "c2"

    def test_no_hub_api_server_skips(self, mock_secondary_client, mock_state_manager):
        """If hub API server can't be determined, skip verification."""
        pav = _make_pav(mock_secondary_client, mock_state_manager)

        with patch.object(pav, "_get_hub_api_server", return_value=""):
            with patch.object(pav, "_load_kubeconfig_data") as mock_load:
                pav._verify_klusterlet_connections()

        mock_load.assert_not_called()

    def test_no_kubeconfig_skips(self, mock_secondary_client, mock_state_manager):
        """If kubeconfig can't be loaded, skip verification."""
        pav = _make_pav(mock_secondary_client, mock_state_manager)

        with patch.object(pav, "_get_hub_api_server", return_value="https://hub:6443"):
            with patch.object(pav, "_load_kubeconfig_data", return_value={}):
                with patch.object(pav, "_check_klusterlet_connection") as mock_check:
                    pav._verify_klusterlet_connections()

        mock_check.assert_not_called()

    def test_cluster_without_api_url(self, mock_secondary_client, mock_state_manager):
        """Clusters without managedClusterClientConfigs get empty api_url."""
        pav = _make_pav(mock_secondary_client, mock_state_manager)

        mock_secondary_client.list_custom_resources.return_value = [
            {"metadata": {"name": "orphan"}, "spec": {}},
        ]

        kube_data = {"contexts": [], "clusters": []}

        with patch.object(pav, "_get_hub_api_server", return_value="https://hub:6443"):
            with patch.object(pav, "_load_kubeconfig_data", return_value=kube_data):
                with patch.object(pav, "_find_context_by_api_url", return_value="") as mock_find:
                    pav._verify_klusterlet_connections()

        # Called with empty api_url
        mock_find.assert_called_once_with(kube_data, "", "orphan")

    def test_check_cluster_exception_returns_unreachable(self, mock_secondary_client, mock_state_manager):
        """Exceptions in check_cluster inner function should yield 'unreachable'."""
        pav = _make_pav(mock_secondary_client, mock_state_manager)

        mock_secondary_client.list_custom_resources.return_value = [
            {
                "metadata": {"name": "c1"},
                "spec": {"managedClusterClientConfigs": [{"url": "https://api.c1:6443"}]},
            },
        ]

        kube_data = {
            "contexts": [{"name": "ctx-c1", "context": {"cluster": "kc1"}}],
            "clusters": [{"name": "kc1", "cluster": {"server": "https://api.c1:6443"}}],
        }

        with patch.object(pav, "_get_hub_api_server", return_value="https://hub:6443"):
            with patch.object(pav, "_load_kubeconfig_data", return_value=kube_data):
                with patch.object(pav, "_find_context_by_api_url", return_value="ctx-c1"):
                    with patch.object(
                        pav,
                        "_check_klusterlet_connection",
                        side_effect=RuntimeError("boom"),
                    ):
                        # Should not raise; c1 lands in unreachable bucket
                        pav._verify_klusterlet_connections()


# ========================================================================
# 2. Kubeconfig loading / parsing (_load_kubeconfig_data)
# ========================================================================
@pytest.mark.unit
class TestKubeconfigLoading:
    """Tests for _load_kubeconfig_data caching, size limits, errors."""

    def test_cache_hit_returns_cached_data(self, mock_secondary_client, mock_state_manager, tmp_path):
        """Second call should return cached data when mtime unchanged."""
        cfg = tmp_path / "config"
        cfg.write_text(
            _kubeconfig_yaml(
                clusters=[{"name": "c1", "cluster": {"server": "https://c1:6443"}}],
                contexts=[{"name": "ctx-c1", "context": {"cluster": "c1"}}],
            )
        )

        pav = _make_pav(mock_secondary_client, mock_state_manager)

        with patch.dict("os.environ", {"KUBECONFIG": str(cfg)}):
            first = pav._load_kubeconfig_data()
            second = pav._load_kubeconfig_data()

        assert first is second  # same object = cache hit

    def test_cache_invalidated_on_mtime_change(self, mock_secondary_client, mock_state_manager, tmp_path):
        """Cache should be invalidated when file mtime changes."""
        cfg = tmp_path / "config"
        cfg.write_text(
            _kubeconfig_yaml(
                clusters=[{"name": "c1", "cluster": {"server": "https://c1:6443"}}],
            )
        )

        pav = _make_pav(mock_secondary_client, mock_state_manager)

        with patch.dict("os.environ", {"KUBECONFIG": str(cfg)}):
            first = pav._load_kubeconfig_data()
            # Simulate file modification
            time.sleep(0.05)
            cfg.write_text(
                _kubeconfig_yaml(
                    clusters=[
                        {"name": "c1", "cluster": {"server": "https://c1:6443"}},
                        {"name": "c2", "cluster": {"server": "https://c2:6443"}},
                    ],
                )
            )
            second = pav._load_kubeconfig_data()

        assert len(second["clusters"]) == 2
        assert first is not second

    def test_cache_invalidated_on_file_deletion(self, mock_secondary_client, mock_state_manager, tmp_path):
        """Cache should be invalidated when a kubeconfig file is deleted."""
        cfg = tmp_path / "config"
        cfg.write_text(
            _kubeconfig_yaml(
                clusters=[{"name": "c1", "cluster": {"server": "https://c1:6443"}}],
            )
        )

        pav = _make_pav(mock_secondary_client, mock_state_manager)

        with patch.dict("os.environ", {"KUBECONFIG": str(cfg)}):
            first = pav._load_kubeconfig_data()
            assert len(first["clusters"]) == 1

            cfg.unlink()
            second = pav._load_kubeconfig_data()

        assert len(second["clusters"]) == 0
        assert first is not second

    def test_force_reload_bypasses_cache(self, mock_secondary_client, mock_state_manager, tmp_path):
        """force_reload=True should bypass cache even when mtime unchanged."""
        cfg = tmp_path / "config"
        cfg.write_text(
            _kubeconfig_yaml(
                clusters=[{"name": "c1", "cluster": {"server": "https://c1:6443"}}],
            )
        )

        pav = _make_pav(mock_secondary_client, mock_state_manager)

        with patch.dict("os.environ", {"KUBECONFIG": str(cfg)}):
            first = pav._load_kubeconfig_data()
            second = pav._load_kubeconfig_data(force_reload=True)

        assert first is not second
        assert second["clusters"][0]["name"] == "c1"

    def test_size_limit_enforcement(self, mock_secondary_client, mock_state_manager, tmp_path):
        """Files exceeding max_size should be skipped."""
        cfg = tmp_path / "config"
        # Create a file larger than 100 bytes
        cfg.write_text("x" * 200)

        pav = _make_pav(mock_secondary_client, mock_state_manager)

        with patch.dict("os.environ", {"KUBECONFIG": str(cfg)}):
            data = pav._load_kubeconfig_data(max_size=100)

        assert data["clusters"] == []

    def test_size_bypass_with_zero(self, mock_secondary_client, mock_state_manager, tmp_path):
        """max_size=0 should bypass size check entirely."""
        cfg = tmp_path / "config"
        cfg.write_text(
            _kubeconfig_yaml(
                clusters=[{"name": "big", "cluster": {"server": "https://big:6443"}}],
            )
        )

        pav = _make_pav(mock_secondary_client, mock_state_manager)

        with patch.dict("os.environ", {"KUBECONFIG": str(cfg)}):
            data = pav._load_kubeconfig_data(max_size=0)

        assert len(data["clusters"]) == 1

    def test_yaml_parse_error_skips_file(self, mock_secondary_client, mock_state_manager, tmp_path):
        """Invalid YAML should be skipped gracefully."""
        bad = tmp_path / "bad.yaml"
        bad.write_text("{{{{invalid yaml")

        good = tmp_path / "good.yaml"
        good.write_text(
            _kubeconfig_yaml(
                clusters=[{"name": "ok", "cluster": {"server": "https://ok:6443"}}],
            )
        )

        pav = _make_pav(mock_secondary_client, mock_state_manager)

        with patch.dict("os.environ", {"KUBECONFIG": f"{bad}:{good}"}):
            data = pav._load_kubeconfig_data()

        assert len(data["clusters"]) == 1
        assert data["clusters"][0]["name"] == "ok"

    def test_oserror_skips_file(self, mock_secondary_client, mock_state_manager, tmp_path):
        """OSError during open should be handled gracefully."""
        cfg = tmp_path / "config"
        cfg.write_text(
            _kubeconfig_yaml(
                clusters=[{"name": "c1", "cluster": {"server": "https://c1:6443"}}],
            )
        )

        pav = _make_pav(mock_secondary_client, mock_state_manager)

        original_open = open

        def patched_open(path, *args, **kwargs):
            if str(path) == str(cfg):
                raise OSError("permission denied")
            return original_open(path, *args, **kwargs)

        with patch.dict("os.environ", {"KUBECONFIG": str(cfg)}):
            with patch("builtins.open", side_effect=patched_open):
                data = pav._load_kubeconfig_data()

        assert data["clusters"] == []

    def test_explicit_max_size_positive(self, mock_secondary_client, mock_state_manager, tmp_path):
        """Explicit positive max_size should set the limit."""
        cfg = tmp_path / "config"
        content = _kubeconfig_yaml(
            clusters=[{"name": "c1", "cluster": {"server": "https://c1:6443"}}],
        )
        cfg.write_text(content)

        pav = _make_pav(mock_secondary_client, mock_state_manager)

        # File should fit in large limit
        with patch.dict("os.environ", {"KUBECONFIG": str(cfg)}):
            data = pav._load_kubeconfig_data(max_size=100000)

        assert len(data["clusters"]) == 1


# ========================================================================
# 3. Context lookup by API URL (_find_context_by_api_url)
# ========================================================================
@pytest.mark.unit
class TestFindContextByApiUrl:
    """Tests for _find_context_by_api_url hostname matching and fallback."""

    def test_hostname_match(self, mock_secondary_client, mock_state_manager):
        """Should match context by API URL hostname."""
        pav = _make_pav(mock_secondary_client, mock_state_manager)

        kube_data = {
            "clusters": [
                {
                    "name": "kc-prod",
                    "cluster": {"server": "https://api.prod.example.com:6443"},
                },
            ],
            "contexts": [
                {"name": "admin@prod", "context": {"cluster": "kc-prod"}},
            ],
        }

        result = pav._find_context_by_api_url(kube_data, "https://api.prod.example.com:6443", "prod")
        assert result == "admin@prod"

    def test_hostname_match_ignores_port(self, mock_secondary_client, mock_state_manager):
        """Port differences should not prevent a match."""
        pav = _make_pav(mock_secondary_client, mock_state_manager)

        kube_data = {
            "clusters": [
                {
                    "name": "kc-a",
                    "cluster": {"server": "https://api.a.example.com:6443"},
                },
            ],
            "contexts": [
                {"name": "ctx-a", "context": {"cluster": "kc-a"}},
            ],
        }

        result = pav._find_context_by_api_url(kube_data, "https://api.a.example.com:443", "cluster-a")
        assert result == "ctx-a"

    def test_no_match_returns_empty(self, mock_secondary_client, mock_state_manager):
        """No matching cluster should return empty string."""
        pav = _make_pav(mock_secondary_client, mock_state_manager)

        kube_data = {
            "clusters": [
                {
                    "name": "kc-x",
                    "cluster": {"server": "https://api.x.example.com:6443"},
                },
            ],
            "contexts": [
                {"name": "ctx-x", "context": {"cluster": "kc-x"}},
            ],
        }

        result = pav._find_context_by_api_url(kube_data, "https://api.unknown.example.com:6443", "unknown")
        assert result == ""

    def test_empty_api_url_name_fallback_success(self, mock_secondary_client, mock_state_manager):
        """Empty api_url should fall back to name-based context matching."""
        pav = _make_pav(mock_secondary_client, mock_state_manager)

        with patch("modules.post_activation.config.list_kube_config_contexts") as mock_list:
            mock_list.return_value = (
                [{"name": "prod-cluster"}],
                {"name": "prod-cluster"},
            )
            result = pav._find_context_by_api_url({}, "", "prod-cluster")

        assert result == "prod-cluster"

    def test_empty_api_url_name_fallback_no_match(self, mock_secondary_client, mock_state_manager):
        """Empty api_url with no matching context name should return empty."""
        pav = _make_pav(mock_secondary_client, mock_state_manager)

        with patch("modules.post_activation.config.list_kube_config_contexts") as mock_list:
            mock_list.return_value = ([{"name": "other-ctx"}], {"name": "other-ctx"})
            result = pav._find_context_by_api_url({}, "", "my-cluster")

        assert result == ""

    def test_empty_api_url_config_exception(self, mock_secondary_client, mock_state_manager):
        """ConfigException during name-based fallback should return empty."""
        pav = _make_pav(mock_secondary_client, mock_state_manager)

        from kubernetes import config as kube_config

        with patch("modules.post_activation.config.list_kube_config_contexts") as mock_list:
            mock_list.side_effect = kube_config.ConfigException("no config")
            result = pav._find_context_by_api_url({}, "", "my-cluster")

        assert result == ""

    def test_cluster_in_kubeconfig_but_no_context_using_it(self, mock_secondary_client, mock_state_manager):
        """Cluster matched by URL but no context references it should return empty."""
        pav = _make_pav(mock_secondary_client, mock_state_manager)

        kube_data = {
            "clusters": [
                {
                    "name": "orphan-cluster",
                    "cluster": {"server": "https://api.orphan:6443"},
                },
            ],
            "contexts": [
                {"name": "ctx-other", "context": {"cluster": "different-cluster"}},
            ],
        }

        result = pav._find_context_by_api_url(kube_data, "https://api.orphan:6443", "orphan")
        assert result == ""


# ========================================================================
# 4. Klusterlet connection check (_check_klusterlet_connection)
# ========================================================================
@pytest.mark.unit
class TestCheckKlusterletConnection:
    """Tests for _check_klusterlet_connection secret inspection logic."""

    def _setup(self, mock_secondary_client, mock_state_manager):
        pav = _make_pav(mock_secondary_client, mock_state_manager)
        mock_v1 = Mock()
        mock_apps = Mock()
        pav._build_managed_cluster_clients = Mock(return_value=(mock_v1, mock_apps))
        return pav, mock_v1

    def _make_secret(self, server_url):
        """Build a mock V1Secret with an embedded kubeconfig pointing to server_url."""
        inner_kube = yaml.dump(
            {
                "clusters": [{"name": "hub", "cluster": {"server": server_url}}],
            }
        )
        encoded = base64.b64encode(inner_kube.encode()).decode()
        secret = Mock()
        secret.data = {"kubeconfig": encoded}
        return secret

    def test_verified_when_hub_matches(self, mock_secondary_client, mock_state_manager):
        """Should return 'verified' when klusterlet points to expected hub."""
        pav, mock_v1 = self._setup(mock_secondary_client, mock_state_manager)
        mock_v1.read_namespaced_secret.return_value = self._make_secret("https://api.newhub.com:6443")

        result = pav._check_klusterlet_connection("ctx-c1", "c1", "https://api.newhub.com:6443")
        assert result == "verified"

    def test_wrong_hub_when_mismatch(self, mock_secondary_client, mock_state_manager):
        """Should return 'wrong_hub' when klusterlet points to different hub."""
        pav, mock_v1 = self._setup(mock_secondary_client, mock_state_manager)
        mock_v1.read_namespaced_secret.return_value = self._make_secret("https://api.oldhub.com:6443")

        result = pav._check_klusterlet_connection("ctx-c1", "c1", "https://api.newhub.com:6443")
        assert result == "wrong_hub"

    def test_fallback_to_bootstrap_secret(self, mock_secondary_client, mock_state_manager):
        """Should try bootstrap-hub-kubeconfig when hub-kubeconfig-secret is 404."""
        pav, mock_v1 = self._setup(mock_secondary_client, mock_state_manager)

        bootstrap_secret = self._make_secret("https://api.newhub.com:6443")

        def side_effect(name, namespace):
            if name == "hub-kubeconfig-secret":
                raise ApiException(status=404)
            return bootstrap_secret

        mock_v1.read_namespaced_secret.side_effect = side_effect

        result = pav._check_klusterlet_connection("ctx-c1", "c1", "https://api.newhub.com:6443")
        assert result == "verified"
        assert mock_v1.read_namespaced_secret.call_count == 2

    def test_unreachable_on_empty_kubeconfig(self, mock_secondary_client, mock_state_manager):
        """Should return 'unreachable' when secret has no kubeconfig data."""
        pav, mock_v1 = self._setup(mock_secondary_client, mock_state_manager)
        secret = Mock()
        secret.data = {"kubeconfig": ""}
        mock_v1.read_namespaced_secret.return_value = secret

        result = pav._check_klusterlet_connection("ctx-c1", "c1", "https://hub:6443")
        assert result == "unreachable"

    def test_unreachable_on_invalid_yaml_in_secret(self, mock_secondary_client, mock_state_manager):
        """Should return 'unreachable' when kubeconfig inside secret is invalid YAML."""
        pav, mock_v1 = self._setup(mock_secondary_client, mock_state_manager)
        secret = Mock()
        secret.data = {"kubeconfig": base64.b64encode(b"{{{{bad yaml").decode()}
        mock_v1.read_namespaced_secret.return_value = secret

        result = pav._check_klusterlet_connection("ctx-c1", "c1", "https://hub:6443")
        assert result == "unreachable"

    def test_unreachable_on_empty_clusters(self, mock_secondary_client, mock_state_manager):
        """Should return 'unreachable' when embedded kubeconfig has no clusters."""
        pav, mock_v1 = self._setup(mock_secondary_client, mock_state_manager)
        inner = yaml.dump({"clusters": []})
        secret = Mock()
        secret.data = {"kubeconfig": base64.b64encode(inner.encode()).decode()}
        mock_v1.read_namespaced_secret.return_value = secret

        result = pav._check_klusterlet_connection("ctx-c1", "c1", "https://hub:6443")
        assert result == "unreachable"

    def test_unreachable_on_non_dict_cluster_entry(self, mock_secondary_client, mock_state_manager):
        """Should return 'unreachable' when clusters[0] is not a dict."""
        pav, mock_v1 = self._setup(mock_secondary_client, mock_state_manager)
        inner = yaml.dump({"clusters": ["not-a-dict"]})
        secret = Mock()
        secret.data = {"kubeconfig": base64.b64encode(inner.encode()).decode()}
        mock_v1.read_namespaced_secret.return_value = secret

        result = pav._check_klusterlet_connection("ctx-c1", "c1", "https://hub:6443")
        assert result == "unreachable"

    def test_unreachable_on_config_exception(self, mock_secondary_client, mock_state_manager):
        """ConfigException (context doesn't exist) should return 'unreachable'."""
        from kubernetes import config as kube_config

        pav = _make_pav(mock_secondary_client, mock_state_manager)
        pav._build_managed_cluster_clients = Mock(side_effect=kube_config.ConfigException("no context"))

        result = pav._check_klusterlet_connection("bad-ctx", "c1", "https://hub:6443")
        assert result == "unreachable"

    def test_unreachable_on_generic_exception(self, mock_secondary_client, mock_state_manager):
        """Generic exceptions should return 'unreachable'."""
        pav = _make_pav(mock_secondary_client, mock_state_manager)
        pav._build_managed_cluster_clients = Mock(side_effect=RuntimeError("connection refused"))

        result = pav._check_klusterlet_connection("bad-ctx", "c1", "https://hub:6443")
        assert result == "unreachable"

    def test_unreachable_on_non_404_api_exception(self, mock_secondary_client, mock_state_manager):
        """Non-404 ApiException on hub-kubeconfig-secret should propagate to unreachable."""
        pav, mock_v1 = self._setup(mock_secondary_client, mock_state_manager)
        mock_v1.read_namespaced_secret.side_effect = ApiException(status=403)

        result = pav._check_klusterlet_connection("ctx-c1", "c1", "https://hub:6443")
        assert result == "unreachable"

    def test_unreachable_on_none_kubeconfig_data(self, mock_secondary_client, mock_state_manager):
        """Should return 'unreachable' when yaml.safe_load returns None."""
        pav, mock_v1 = self._setup(mock_secondary_client, mock_state_manager)
        # Base64 encode empty string -> yaml.safe_load returns None
        secret = Mock()
        secret.data = {"kubeconfig": base64.b64encode(b"").decode()}
        mock_v1.read_namespaced_secret.return_value = secret

        result = pav._check_klusterlet_connection("ctx-c1", "c1", "https://hub:6443")
        assert result == "unreachable"

    def test_unreachable_on_non_dict_cluster_info(self, mock_secondary_client, mock_state_manager):
        """Should return 'unreachable' when cluster_info is not a dict."""
        pav, mock_v1 = self._setup(mock_secondary_client, mock_state_manager)
        inner = yaml.dump({"clusters": [{"name": "hub", "cluster": "not-a-dict"}]})
        secret = Mock()
        secret.data = {"kubeconfig": base64.b64encode(inner.encode()).decode()}
        mock_v1.read_namespaced_secret.return_value = secret

        result = pav._check_klusterlet_connection("ctx-c1", "c1", "https://hub:6443")
        assert result == "unreachable"

    def test_unreachable_on_empty_server_url(self, mock_secondary_client, mock_state_manager):
        """Should return 'unreachable' when server URL in embedded kubeconfig is empty."""
        pav, mock_v1 = self._setup(mock_secondary_client, mock_state_manager)
        inner = yaml.dump({"clusters": [{"name": "hub", "cluster": {"server": ""}}]})
        secret = Mock()
        secret.data = {"kubeconfig": base64.b64encode(inner.encode()).decode()}
        mock_v1.read_namespaced_secret.return_value = secret

        result = pav._check_klusterlet_connection("ctx-c1", "c1", "https://hub:6443")
        assert result == "unreachable"


# ========================================================================
# 5. Scale up observability (_scale_up_observability_components)
# ========================================================================
@pytest.mark.unit
class TestScaleUpObservability:
    """Tests for _scale_up_observability_components."""

    def test_both_at_zero_scales_up(self, mock_secondary_client, mock_state_manager):
        """Both components at 0 replicas should be scaled up."""
        pav = _make_pav(mock_secondary_client, mock_state_manager, obs=True)

        mock_secondary_client.get_deployment.return_value = {"spec": {"replicas": 0}}
        mock_secondary_client.get_statefulset.return_value = {"spec": {"replicas": 0}}
        mock_secondary_client.wait_for_pods_ready.return_value = True

        pav._scale_up_observability_components()

        mock_secondary_client.scale_deployment.assert_called_once()
        mock_secondary_client.scale_statefulset.assert_called_once()
        assert mock_secondary_client.wait_for_pods_ready.call_count == 2

    def test_already_running_no_scale(self, mock_secondary_client, mock_state_manager):
        """Already-running components should not be scaled."""
        pav = _make_pav(mock_secondary_client, mock_state_manager, obs=True)

        mock_secondary_client.get_deployment.return_value = {"spec": {"replicas": 2}}
        mock_secondary_client.get_statefulset.return_value = {"spec": {"replicas": 1}}

        pav._scale_up_observability_components()

        mock_secondary_client.scale_deployment.assert_not_called()
        mock_secondary_client.scale_statefulset.assert_not_called()
        mock_secondary_client.wait_for_pods_ready.assert_not_called()

    def test_deployment_not_found(self, mock_secondary_client, mock_state_manager):
        """Missing deployment should be skipped, statefulset still checked."""
        pav = _make_pav(mock_secondary_client, mock_state_manager, obs=True)

        mock_secondary_client.get_deployment.return_value = None
        mock_secondary_client.get_statefulset.return_value = {"spec": {"replicas": 0}}
        mock_secondary_client.wait_for_pods_ready.return_value = True

        pav._scale_up_observability_components()

        mock_secondary_client.scale_deployment.assert_not_called()
        mock_secondary_client.scale_statefulset.assert_called_once()

    def test_statefulset_not_found(self, mock_secondary_client, mock_state_manager):
        """Missing statefulset should be skipped, deployment still checked."""
        pav = _make_pav(mock_secondary_client, mock_state_manager, obs=True)

        mock_secondary_client.get_deployment.return_value = {"spec": {"replicas": 0}}
        mock_secondary_client.get_statefulset.return_value = None
        mock_secondary_client.wait_for_pods_ready.return_value = True

        pav._scale_up_observability_components()

        mock_secondary_client.scale_deployment.assert_called_once()
        mock_secondary_client.scale_statefulset.assert_not_called()

    def test_deployment_exception_continues(self, mock_secondary_client, mock_state_manager):
        """ApiException on deployment should not prevent statefulset check."""
        pav = _make_pav(mock_secondary_client, mock_state_manager, obs=True)

        mock_secondary_client.get_deployment.side_effect = ApiException(status=500)
        mock_secondary_client.get_statefulset.return_value = {"spec": {"replicas": 0}}
        mock_secondary_client.wait_for_pods_ready.return_value = True

        pav._scale_up_observability_components()

        mock_secondary_client.scale_statefulset.assert_called_once()

    def test_statefulset_exception_continues(self, mock_secondary_client, mock_state_manager):
        """ApiException on statefulset should not prevent wait for deployment."""
        pav = _make_pav(mock_secondary_client, mock_state_manager, obs=True)

        mock_secondary_client.get_deployment.return_value = {"spec": {"replicas": 0}}
        mock_secondary_client.get_statefulset.side_effect = ApiException(status=500)
        mock_secondary_client.wait_for_pods_ready.return_value = True

        pav._scale_up_observability_components()

        mock_secondary_client.scale_deployment.assert_called_once()
        # Only deployment should be waited on
        assert mock_secondary_client.wait_for_pods_ready.call_count == 1

    def test_pods_not_ready_warns(self, mock_secondary_client, mock_state_manager):
        """Pods failing to become ready should not raise, just warn."""
        pav = _make_pav(mock_secondary_client, mock_state_manager, obs=True)

        mock_secondary_client.get_deployment.return_value = {"spec": {"replicas": 0}}
        mock_secondary_client.get_statefulset.return_value = {"spec": {"replicas": 0}}
        mock_secondary_client.wait_for_pods_ready.return_value = False

        # Should not raise
        pav._scale_up_observability_components()


# ========================================================================
# 6. Observability pod verification (_verify_observability_pods)
# ========================================================================
@pytest.mark.unit
class TestVerifyObservabilityPods:
    """Tests for _verify_observability_pods error detection."""

    def test_oomkilled_detected(self, mock_secondary_client, mock_state_manager):
        """OOMKilled container should be reported as error."""
        pav = _make_pav(mock_secondary_client, mock_state_manager, obs=True)

        mock_secondary_client.get_pods.return_value = [
            {
                "metadata": {"name": "obs-pod"},
                "status": {
                    "phase": "Running",
                    "conditions": [{"type": "Ready", "status": "False"}],
                    "containerStatuses": [
                        {
                            "name": "collector",
                            "state": {"terminated": {"reason": "OOMKilled", "exitCode": 137}},
                        }
                    ],
                },
            }
        ]

        with patch("modules.post_activation.logger") as mock_logger:
            pav._verify_observability_pods()
            assert any(any("OOMKilled" in str(arg) for arg in call.args) for call in mock_logger.warning.call_args_list)

    def test_failed_phase_detected(self, mock_secondary_client, mock_state_manager):
        """Pod in Failed phase should be flagged."""
        pav = _make_pav(mock_secondary_client, mock_state_manager, obs=True)

        mock_secondary_client.get_pods.return_value = [
            {
                "metadata": {"name": "bad-pod"},
                "status": {
                    "phase": "Failed",
                    "conditions": [],
                    "containerStatuses": [],
                },
            }
        ]

        with patch("modules.post_activation.logger") as mock_logger:
            pav._verify_observability_pods()
            assert any(any("Failed" in str(arg) for arg in call.args) for call in mock_logger.warning.call_args_list)

    def test_unknown_phase_detected(self, mock_secondary_client, mock_state_manager):
        """Pod in Unknown phase should be flagged."""
        pav = _make_pav(mock_secondary_client, mock_state_manager, obs=True)

        mock_secondary_client.get_pods.return_value = [
            {
                "metadata": {"name": "unknown-pod"},
                "status": {
                    "phase": "Unknown",
                    "conditions": [],
                    "containerStatuses": [],
                },
            }
        ]

        with patch("modules.post_activation.logger") as mock_logger:
            pav._verify_observability_pods()
            assert any(any("Unknown" in str(arg) for arg in call.args) for call in mock_logger.warning.call_args_list)

    def test_nonzero_exit_code_detected(self, mock_secondary_client, mock_state_manager):
        """Non-zero exit code with unknown reason should be flagged."""
        pav = _make_pav(mock_secondary_client, mock_state_manager, obs=True)

        mock_secondary_client.get_pods.return_value = [
            {
                "metadata": {"name": "exit-pod"},
                "status": {
                    "phase": "Running",
                    "conditions": [{"type": "Ready", "status": "False"}],
                    "containerStatuses": [
                        {
                            "name": "app",
                            "state": {"terminated": {"reason": "Completed", "exitCode": 1}},
                        }
                    ],
                },
            }
        ]

        with patch("modules.post_activation.logger") as mock_logger:
            pav._verify_observability_pods()
            assert any(any("exit=1" in str(arg) for arg in call.args) for call in mock_logger.warning.call_args_list)

    def test_image_pull_backoff_detected(self, mock_secondary_client, mock_state_manager):
        """ImagePullBackOff should be reported."""
        pav = _make_pav(mock_secondary_client, mock_state_manager, obs=True)

        mock_secondary_client.get_pods.return_value = [
            {
                "metadata": {"name": "img-pod"},
                "status": {
                    "phase": "Pending",
                    "conditions": [],
                    "containerStatuses": [
                        {
                            "name": "main",
                            "state": {"waiting": {"reason": "ImagePullBackOff"}},
                        }
                    ],
                },
            }
        ]

        with patch("modules.post_activation.logger") as mock_logger:
            pav._verify_observability_pods()
            assert any(
                any("ImagePullBackOff" in str(arg) for arg in call.args) for call in mock_logger.warning.call_args_list
            )

    def test_low_readiness_warns(self, mock_secondary_client, mock_state_manager):
        """Below POD_READINESS_TOLERANCE should trigger warning about readiness."""
        pav = _make_pav(mock_secondary_client, mock_state_manager, obs=True)

        # 5 pods, only 1 ready (20%) < 80% tolerance
        pods = []
        for i in range(5):
            ready = "True" if i == 0 else "False"
            pods.append(
                {
                    "metadata": {"name": f"pod-{i}"},
                    "status": {
                        "phase": "Running",
                        "conditions": [{"type": "Ready", "status": ready}],
                        "containerStatuses": [],
                    },
                }
            )

        mock_secondary_client.get_pods.return_value = pods

        with patch("modules.post_activation.logger") as mock_logger:
            pav._verify_observability_pods()
            assert any(
                any("ready" in str(arg).lower() for arg in call.args) for call in mock_logger.warning.call_args_list
            )


# ========================================================================
# 7. Error handling in verify()
# ========================================================================
@pytest.mark.unit
class TestVerifyErrorHandling:
    """Tests for verify() exception paths."""

    def test_switchover_error_returns_false(self, mock_secondary_client, mock_state_manager):
        """SwitchoverError should be caught and return False."""
        pav = _make_pav(mock_secondary_client, mock_state_manager)

        with patch.object(pav, "_verify_cluster_connections", side_effect=SwitchoverError("test fail")):
            result = pav.verify()

        assert result is False
        mock_state_manager.add_error.assert_called_once()
        assert "test fail" in mock_state_manager.add_error.call_args[0][0]

    def test_generic_exception_returns_false(self, mock_secondary_client, mock_state_manager):
        """Generic Exception should be caught and return False with 'Unexpected' prefix."""
        pav = _make_pav(mock_secondary_client, mock_state_manager)

        with patch.object(pav, "_verify_cluster_connections", side_effect=RuntimeError("kaboom")):
            result = pav.verify()

        assert result is False
        mock_state_manager.add_error.assert_called_once()
        assert "Unexpected" in mock_state_manager.add_error.call_args[0][0]


# ========================================================================
# 8. Force klusterlet reconnect exception handling
# ========================================================================
@pytest.mark.unit
class TestForceKlusterletReconnectException:
    """Tests for _force_klusterlet_reconnect exception path."""

    def test_api_exception_returns_false(self, mock_secondary_client, mock_state_manager):
        """ApiException during reconnect should return False."""
        pav = _make_pav(mock_secondary_client, mock_state_manager)

        mock_secondary_client.get_secret.side_effect = ApiException(status=500)

        result = pav._force_klusterlet_reconnect("c1", "ctx-c1")
        assert result is False

    def test_generic_exception_returns_false(self, mock_secondary_client, mock_state_manager):
        """Generic exception during reconnect should return False."""
        pav = _make_pav(mock_secondary_client, mock_state_manager)

        mock_secondary_client.get_secret.side_effect = RuntimeError("connection reset")

        result = pav._force_klusterlet_reconnect("c1", "ctx-c1")
        assert result is False

    def test_missing_import_yaml_data_returns_false(self, mock_secondary_client, mock_state_manager):
        """Import secret without import.yaml data should return False."""
        pav = _make_pav(mock_secondary_client, mock_state_manager)

        mock_secondary_client.get_secret.return_value = {"data": {"import.yaml": ""}}

        result = pav._force_klusterlet_reconnect("c1", "ctx-c1")
        assert result is False


# ========================================================================
# 9. Hub API server discovery (_get_hub_api_server)
# ========================================================================
@pytest.mark.unit
class TestGetHubApiServer:
    """Tests for _get_hub_api_server."""

    def test_successful_extraction(self, mock_secondary_client, mock_state_manager):
        """Should extract API server URL from kubeconfig matching secondary context."""
        pav = _make_pav(mock_secondary_client, mock_state_manager)

        kube_data = {
            "contexts": [
                {"name": "secondary-ctx", "context": {"cluster": "hub-cluster"}},
            ],
            "clusters": [
                {
                    "name": "hub-cluster",
                    "cluster": {"server": "https://api.hub.example.com:6443"},
                },
            ],
        }

        with patch.object(pav, "_load_kubeconfig_data", return_value=kube_data):
            result = pav._get_hub_api_server()

        assert result == "https://api.hub.example.com:6443"

    def test_empty_kubeconfig(self, mock_secondary_client, mock_state_manager):
        """Empty kubeconfig should return empty string."""
        pav = _make_pav(mock_secondary_client, mock_state_manager)

        with patch.object(pav, "_load_kubeconfig_data", return_value={}):
            result = pav._get_hub_api_server()

        assert result == ""

    def test_missing_context(self, mock_secondary_client, mock_state_manager):
        """No matching context should return empty string."""
        pav = _make_pav(mock_secondary_client, mock_state_manager)

        kube_data = {
            "contexts": [{"name": "other-ctx", "context": {"cluster": "other-cluster"}}],
            "clusters": [{"name": "other-cluster", "cluster": {"server": "https://other:6443"}}],
        }

        with patch.object(pav, "_load_kubeconfig_data", return_value=kube_data):
            result = pav._get_hub_api_server()

        assert result == ""

    def test_exception_returns_empty(self, mock_secondary_client, mock_state_manager):
        """Exception during hub API server lookup should return empty string."""
        pav = _make_pav(mock_secondary_client, mock_state_manager)

        with patch.object(pav, "_load_kubeconfig_data", side_effect=RuntimeError("disk error")):
            result = pav._get_hub_api_server()

        assert result == ""

    def test_context_found_but_cluster_missing(self, mock_secondary_client, mock_state_manager):
        """Context references a cluster not in clusters list should return empty."""
        pav = _make_pav(mock_secondary_client, mock_state_manager)

        kube_data = {
            "contexts": [{"name": "secondary-ctx", "context": {"cluster": "ghost-cluster"}}],
            "clusters": [{"name": "different-cluster", "cluster": {"server": "https://x:6443"}}],
        }

        with patch.object(pav, "_load_kubeconfig_data", return_value=kube_data):
            result = pav._get_hub_api_server()

        assert result == ""
