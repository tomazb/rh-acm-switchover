"""Unit tests for modules/post_activation.py.

Tests cover PostActivationVerification class for verifying switchover success.
"""

import pytest
import sys
from pathlib import Path
from unittest.mock import Mock, patch

# Add parent to path to import modules directly
sys.path.insert(0, str(Path(__file__).parent.parent))

import modules.post_activation as post_activation_module
from lib.constants import OBSERVABILITY_NAMESPACE

PostActivationVerification = post_activation_module.PostActivationVerification


@pytest.fixture
def mock_secondary_client():
    """Create a mock KubeClient for secondary hub."""
    return Mock()


@pytest.fixture
def mock_state_manager():
    """Create a mock StateManager."""
    mock = Mock()
    mock.is_step_completed.return_value = False
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

    def test_verify_steps_already_completed(
        self, post_verify_with_obs, mock_state_manager
    ):
        """Test skipping already completed steps."""
        mock_state_manager.is_step_completed.return_value = True

        result = post_verify_with_obs.verify()

        assert result is True

    @patch("modules.post_activation.wait_for_condition")
    def test_verify_managed_clusters_all_available(
        self, mock_wait, post_verify_with_obs, mock_secondary_client
    ):
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
    def test_verify_managed_clusters_timeout(
        self, mock_wait, post_verify_with_obs, mock_secondary_client
    ):
        """Test timeout while waiting for clusters."""
        mock_wait.return_value = False  # Timeout

        mock_secondary_client.list_custom_resources.return_value = [
            {
                "metadata": {"name": "cluster1"},
                "status": {
                    "conditions": [
                        {"type": "ManagedClusterConditionAvailable", "status": "False"}
                    ]
                },
            }
        ]

        # Should raise exception
        with pytest.raises(Exception, match="timeout|Timeout"):
            post_verify_with_obs._verify_managed_clusters_connected()

    def test_verify_no_clusters(self, post_verify_with_obs, mock_secondary_client):
        """Test when no managed clusters exist."""
        mock_secondary_client.list_custom_resources.return_value = []

        # Should handle gracefully or raise appropriate error
        with pytest.raises(Exception):
            post_verify_with_obs._verify_managed_clusters_connected(timeout=1)

    def test_restart_observatorium_api(
        self, post_verify_with_obs, mock_secondary_client
    ):
        """Test restarting observatorium API deployment."""
        mock_secondary_client.wait_for_pods_ready.return_value = True
        mock_secondary_client.get_pods.return_value = [
            {"metadata": {"name": "api"}, "status": {"startTime": "2024-01-01T00:00:00Z"}}
        ]
        mock_secondary_client.rollout_restart_deployment.return_value = {"status": "ok"}

        post_verify_with_obs._restart_observatorium_api()

        mock_secondary_client.rollout_restart_deployment.assert_called_once_with(
            namespace=OBSERVABILITY_NAMESPACE, name="observability-observatorium-api"
        )
        mock_secondary_client.get_pods.assert_called()

    @patch("modules.post_activation.wait_for_condition")
    def test_verify_observability_pods_all_ready(
        self, mock_wait, post_verify_with_obs, mock_secondary_client
    ):
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
    def test_verify_observability_pods_none_found(
        self, mock_wait, post_verify_with_obs, mock_secondary_client
    ):
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

        assert any(
            "CrashLoopBackOff" in str(call.args[0])
            for call in mock_logger.warning.call_args_list
        )

    @patch("modules.post_activation.wait_for_condition")
    def test_verify_metrics_collection(
        self, mock_wait, post_verify_with_obs, mock_secondary_client
    ):
        """Test verifying metrics collection."""
        mock_wait.return_value = True

        # Mock get_pods to return a list
        mock_secondary_client.get_pods.return_value = [
            {"metadata": {"name": "metrics-pod"}}
        ]

        # This method just logs information, no exception expected
        post_verify_with_obs._verify_metrics_collection()

        # Verify get_pods was called
        assert mock_secondary_client.get_pods.called

    @patch("modules.post_activation.logger")
    def test_log_grafana_route_found(
        self, mock_logger, post_verify_with_obs, mock_secondary_client
    ):
        """Grafana route should be logged when host detected."""
        mock_secondary_client.get_route_host.return_value = "grafana.example.com"

        post_verify_with_obs._log_grafana_route()

        mock_logger.info.assert_any_call(
            "Grafana route detected: https://%s (namespace: %s)",
            "grafana.example.com",
            OBSERVABILITY_NAMESPACE,
        )

    @patch("modules.post_activation.logger")
    def test_log_grafana_route_missing(
        self, mock_logger, post_verify_with_obs, mock_secondary_client
    ):
        """Missing Grafana route should emit warning."""
        mock_secondary_client.get_route_host.return_value = None

        post_verify_with_obs._log_grafana_route()

        mock_logger.warning.assert_any_call(
            "Grafana route not found in Observability namespace"
        )

    def test_verify_error_handling(
        self, post_verify_with_obs, mock_secondary_client, mock_state_manager
    ):
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

    def test_verify_disable_auto_import_cleanup_success(
        self, post_verify_with_obs, mock_secondary_client
    ):
        """disable-auto-import verification should pass when annotations removed."""
        mock_secondary_client.list_custom_resources.return_value = [
            {"metadata": {"name": "cluster1", "annotations": {}}},
            {"metadata": {"name": "local-cluster"}},
        ]

        post_verify_with_obs._verify_disable_auto_import_cleared()

    def test_verify_disable_auto_import_cleanup_failure(
        self, post_verify_with_obs, mock_secondary_client
    ):
        """disable-auto-import verification should fail when annotation remains."""
        mock_secondary_client.list_custom_resources.return_value = [
            {
                "metadata": {
                    "name": "cluster1",
                    "annotations": {
                        "import.open-cluster-management.io/disable-auto-import": ""
                    },
                }
            }
        ]

        with pytest.raises(Exception):
            post_verify_with_obs._verify_disable_auto_import_cleared()


@pytest.mark.integration
class TestPostActivationVerificationIntegration:
    """Integration tests for PostActivationVerification."""

    @patch("modules.post_activation.wait_for_condition")
    def test_full_verification_workflow(
        self, mock_wait, mock_secondary_client, tmp_path
    ):
        """Test complete verification workflow with real StateManager."""
        from lib.utils import StateManager, Phase

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
        mock_secondary_client.get_pods.return_value = [
            {"metadata": {"name": "pod1"}, "status": {"phase": "Running"}}
        ]
        mock_secondary_client.rollout_restart_deployment.return_value = {"status": "ok"}

        result = verify.verify()

        assert result is True
        assert state.is_step_completed("verify_clusters_connected")
        assert state.is_step_completed("verify_observability_pods")
