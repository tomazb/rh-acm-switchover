"""Unit tests for modules/post_activation.py.

Tests cover PostActivationVerification class for verifying switchover success.
"""

import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from kubernetes.client.rest import ApiException

# Add parent to path to import modules directly
sys.path.insert(0, str(Path(__file__).parent.parent))

import modules.post_activation as post_activation_module
from lib.constants import (
    CLUSTER_VERIFY_INTERVAL,
    DISABLE_AUTO_IMPORT_ANNOTATION,
    OBSERVABILITY_NAMESPACE,
)
from lib.exceptions import SwitchoverError

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

    def test_initialization(self, mock_secondary_client, mock_state_manager):
        """Test PostActivationVerification initialization."""
        verify = PostActivationVerification(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            has_observability=True,
        )

        assert verify.secondary == mock_secondary_client
        assert verify.state == mock_state_manager
        assert verify.has_observability is True

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

        with patch("modules.post_activation.config.new_client_from_config", return_value=api_client) as new_client:
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
        # verify_klusterlet_connections step should be marked completed
        calls = [call[0][0] for call in mock_state_manager.mark_step_completed.call_args_list]
        assert "verify_klusterlet_connections" in calls

    @patch("modules.post_activation.wait_for_condition")
    def test_verify_no_clusters(self, mock_wait, post_verify_with_obs, mock_secondary_client):
        """Test when no managed clusters exist - should timeout waiting for clusters."""
        mock_secondary_client.list_custom_resources.return_value = []
        mock_wait.return_value = False  # Simulate timeout (no clusters found)

        # Should raise SwitchoverError with timeout message
        with pytest.raises(SwitchoverError, match="Timeout waiting for ManagedClusters"):
            post_verify_with_obs._verify_managed_clusters_connected(timeout=1)

        # Verify wait_for_condition was called with expected parameters
        mock_wait.assert_called_once()
        call_args = mock_wait.call_args
        assert call_args[0][0] == "ManagedCluster connections"  # description
        assert call_args[1]["timeout"] == 1
        assert call_args[1]["interval"] == CLUSTER_VERIFY_INTERVAL

    def test_restart_observatorium_api(self, post_verify_with_obs, mock_secondary_client):
        """Test restarting observatorium API deployment."""
        mock_secondary_client.wait_for_pods_ready.return_value = True
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
        mock_secondary_client.get_pods.assert_called()

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
                for f in as_completed(futures):
                    f.result()

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
        kubeconfig.write_text(
            """
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
"""
        )
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
        kubeconfig1.write_text(
            """
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
"""
        )
        kubeconfig2 = tmp_path / "config2"
        kubeconfig2.write_text(
            """
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
"""
        )
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
        existing.write_text(
            """
apiVersion: v1
clusters:
- cluster:
    server: https://api.exists.example.com:6443
  name: exists
contexts: []
users: []
"""
        )
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
