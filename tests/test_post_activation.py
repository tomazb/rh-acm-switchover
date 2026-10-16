"""Unit tests for modules/post_activation.py.

Tests cover PostActivationVerification class for verifying switchover success.
"""

import base64
import copy
import json
import logging
import os
import sys
import threading
import time
from concurrent.futures import Future
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest
import yaml
from kubernetes.client.rest import ApiException

# Add parent to path to import modules directly
sys.path.insert(0, str(Path(__file__).parent.parent))

import kubernetes.config.kube_config as kube_config_module

import modules.post_activation as post_activation_module
from lib.constants import (
    CLUSTER_VERIFY_INTERVAL,
    DEFAULT_KUBECONFIG_SIZE,
    DISABLE_AUTO_IMPORT_ANNOTATION,
    LOCAL_CLUSTER_NAME,
    MANAGED_CLUSTER_AGENT_NAMESPACE,
    MAX_KUBECONFIG_SIZE,
    OBSERVABILITY_NAMESPACE,
    OBSERVABILITY_POD_LABEL_SELECTOR,
)
from lib.exceptions import SwitchoverError
from lib.kubeconfig_snapshot import SnapshotError, build_snapshot
from lib.strict_read import StrictReadOutcome
from lib.utils import StateManager
from lib.waiter import WaitConditionResult

PostActivationVerification = post_activation_module.PostActivationVerification


def _completed_journal():
    """The completed full_restore journal of the shared R4-04 vectors."""
    path = Path(__file__).parent / "fixtures" / "r4_04_migration_evidence_vectors.json"
    cases = json.loads(path.read_text(encoding="utf-8"))["cases"]
    return next(case for case in cases if case["id"] == "post-activation-journal-completed")["input"]["journal"]


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


HERMETIC_HUB_SERVER = "https://api.new-hub.example:6443"


def _fake_api_client(host, token="t"):
    """An ApiClient double that satisfies every snapshot assertion except, possibly, the host."""
    configuration = SimpleNamespace(
        host=host,
        verify_ssl=True,
        ssl_ca_cert=None,
        cert_file=None,
        key_file=None,
        auth_settings=lambda: {"BearerToken": {"value": f"Bearer {token}"}},
    )
    return SimpleNamespace(configuration=configuration)


SPOKE_URL = "https://api.test-cluster:6443"


@pytest.fixture(autouse=True)
def hermetic_kubeconfig(tmp_path, monkeypatch):
    """Every test here reads a known kubeconfig, never the developer's ~/.kube/config.

    The klusterlet gate fails closed on an unloadable kubeconfig or an undeterminable
    new-hub endpoint (GLM-H3, #349), so a test may not depend on the machine it runs on.
    The hub context matches the mock secondary client's ``secondary-ctx``; it holds no
    spoke contexts, so spokes are the documented non-fatal "no context" skip.
    """
    path = tmp_path / "hermetic-kubeconfig.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "apiVersion": "v1",
                "clusters": [{"name": "new-hub", "cluster": {"server": HERMETIC_HUB_SERVER}}],
                "contexts": [{"name": "secondary-ctx", "context": {"cluster": "new-hub", "user": "admin"}}],
                "users": [{"name": "admin", "user": {"token": "fixture-token"}}],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("KUBECONFIG", str(path))
    return path


@pytest.fixture
def mock_secondary_client():
    """Create a mock KubeClient for secondary hub.

    Post-activation reads ManagedClusters only through the strict complete read. These
    tests state an inventory as ``list_custom_resources`` items; the strict read returns
    exactly those items as a positively complete inventory.
    """
    client = Mock()
    client.context = "secondary-ctx"
    client.list_managed_clusters_strict.side_effect = lambda: StrictReadOutcome.from_items(
        client.list_custom_resources(group="cluster.open-cluster-management.io", version="v1", plural="managedclusters")
    )
    return client


@pytest.fixture
def mock_state_manager():
    """Create a mock StateManager with step() context manager support.

    Its config bag holds a migration journal whose activation completed, which every
    executing post-activation run requires (R4-04 plan Task 9).
    """
    mock = Mock()
    config = {"migration_backups": copy.deepcopy(_completed_journal())}
    mock.state = {"config": config}
    mock._get_config.side_effect = lambda key, default=None: config.get(key, default)
    mock._set_config.side_effect = config.__setitem__
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

    def test_build_managed_cluster_clients_builds_from_the_snapshot_alone(
        self, mock_secondary_client, mock_state_manager, tmp_path
    ):
        """R4-06 PR 3: the client comes from the snapshot dict, the context named, never persisted."""
        verify = PostActivationVerification(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            has_observability=False,
        )
        verify._spoke_material_dir = str(tmp_path)
        snapshot = build_snapshot(
            "managed-context",
            {"server": "https://api.spoke:6443"},
            str(tmp_path),
            {"token": "t"},
            str(tmp_path),
            "u",
            4096,
        )
        core_v1 = Mock(name="core_v1")
        apps_v1 = Mock(name="apps_v1")

        with patch(
            "lib.kubeconfig_snapshot.config.new_client_from_config_dict",
            wraps=kube_config_module.new_client_from_config_dict,
        ) as new_client:
            with patch("modules.post_activation.client.CoreV1Api", return_value=core_v1) as core_ctor:
                with patch("modules.post_activation.client.AppsV1Api", return_value=apps_v1) as apps_ctor:
                    result = verify._build_managed_cluster_clients(snapshot, "https://API.spoke:6443/")

        assert new_client.call_args.kwargs == {"context": "managed-context", "persist_config": False}
        assert "current-context" not in new_client.call_args.args[0]
        api_client = core_ctor.call_args.kwargs["api_client"]
        assert api_client.configuration.host == "https://api.spoke:6443"
        apps_ctor.assert_called_once_with(api_client=api_client)
        assert result == (core_v1, apps_v1)

    @pytest.mark.parametrize(
        "host", ["https://api.spoke:443", "http://api.spoke:6443", "https://api.other:6443", None, "api.spoke:6443"]
    )
    def test_a_client_bound_to_another_endpoint_is_never_returned(
        self, mock_secondary_client, mock_state_manager, host, tmp_path
    ):
        """Amendment §2.7 stays a defense-in-depth assertion on snapshot-built clients."""
        verify = PostActivationVerification(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            has_observability=False,
        )
        verify._spoke_material_dir = str(tmp_path)
        snapshot = build_snapshot(
            "managed-context",
            {"server": "https://api.spoke:6443"},
            str(tmp_path),
            {"token": "t"},
            str(tmp_path),
            "u",
            4096,
        )

        with patch("lib.kubeconfig_snapshot.config.new_client_from_config_dict", return_value=_fake_api_client(host)):
            with patch("modules.post_activation.client.CoreV1Api") as core_ctor:
                with patch("modules.post_activation.client.AppsV1Api") as apps_ctor:
                    with pytest.raises(SnapshotError) as excinfo:
                        verify._build_managed_cluster_clients(snapshot, "https://api.spoke:6443")

        assert excinfo.value.reason == "client_endpoint_mismatch"
        core_ctor.assert_not_called()
        apps_ctor.assert_not_called()

    def test_no_client_is_built_outside_a_verification_run(self, mock_secondary_client, mock_state_manager, tmp_path):
        verify = PostActivationVerification(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            has_observability=False,
        )
        snapshot = build_snapshot(
            "c", {"server": "https://h:6443"}, str(tmp_path), {"token": "t"}, str(tmp_path), "u", 64
        )
        with pytest.raises(SnapshotError):
            verify._build_managed_cluster_clients(snapshot, "https://h:6443")

    def test_klusterlet_verification_never_bypasses_the_kubeconfig_size_limit(
        self, mock_secondary_client, mock_state_manager, hermetic_kubeconfig
    ):
        """The klusterlet gate applies the standard size limit; there is no bypass (#349)."""
        verify = PostActivationVerification(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            has_observability=False,
        )

        with patch("modules.post_activation.MAX_KUBECONFIG_SIZE", 16):
            with patch.object(verify, "_check_klusterlet_targets") as check:
                with pytest.raises(SwitchoverError, match=r"kubeconfig_load_failed \(oversized\)"):
                    verify._verify_klusterlet_connections()

        check.assert_not_called()

    @patch("modules.post_activation.wait_for_condition")
    def test_verify_success_with_observability(
        self, mock_wait, post_verify_with_obs, mock_secondary_client, mock_state_manager
    ):
        """Test successful verification with observability."""
        mock_wait.side_effect = _poll_until_done
        mock_secondary_client.wait_for_pods_ready.return_value = True

        # Mock clusters
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
        mock_secondary_client.get_statefulset.return_value = {
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

        result = post_verify_with_obs.verify()

        assert result is True
        assert mock_state_manager.mark_step_completed.call_count >= 4

    @patch("modules.post_activation.wait_for_condition")
    def test_verify_success_without_observability(
        self, mock_wait, post_verify_no_obs, mock_secondary_client, mock_state_manager
    ):
        """Test successful verification without observability."""
        mock_wait.side_effect = _poll_until_done

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

        result = post_verify_no_obs.verify()

        assert result is True
        # Should not verify observability
        mock_secondary_client.rollout_restart_deployment.assert_not_called()

    def test_verify_steps_already_completed(self, post_verify_with_obs, mock_secondary_client, mock_state_manager):
        """Completed steps are skipped; the readiness evidence is still proven afresh (R4-04 Task 9)."""
        mock_state_manager.is_step_completed.return_value = True
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

        result = post_verify_with_obs.verify()

        assert result is True
        mock_secondary_client.list_managed_clusters_strict.assert_called_once_with()

    @patch("modules.post_activation.wait_for_condition")
    def test_verify_managed_clusters_all_available(self, mock_wait, post_verify_with_obs, mock_secondary_client):
        """Test when all managed clusters are available."""
        mock_wait.return_value = True

        mock_secondary_client.list_custom_resources.return_value = [
            {
                "metadata": {"name": "cluster1"},
                "spec": {"managedClusterClientConfigs": [{"url": "https://api.cluster1:6443"}]},
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
    def test_verify_managed_clusters_requires_derived_expected_names(
        self, mock_wait, mock_secondary_client, mock_state_manager
    ):
        """Post-activation must not pass when a preflight-observed cluster is missing."""
        verifier = PostActivationVerification(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            has_observability=False,
            expected_managed_cluster_names=["cluster-a", "cluster-b"],
            min_managed_clusters=2,
            enforce_expected_managed_cluster_names=True,
        )
        mock_secondary_client.list_custom_resources.return_value = [
            {
                "metadata": {"name": "cluster-a"},
                "status": {
                    "conditions": [
                        {"type": "ManagedClusterConditionAvailable", "status": "True"},
                        {"type": "ManagedClusterJoined", "status": "True"},
                    ]
                },
            },
            {"metadata": {"name": LOCAL_CLUSTER_NAME}},
        ]

        def capture_wait(*args, **kwargs):
            condition_fn = kwargs.get("condition_fn", args[1])
            result = condition_fn()
            assert isinstance(result, WaitConditionResult)
            assert result.done is False
            assert "missing expected ManagedCluster(s): cluster-b" in result.public_detail
            return False

        mock_wait.side_effect = capture_wait

        with pytest.raises(SwitchoverError, match="Missing expected ManagedCluster"):
            verifier._verify_managed_clusters_connected(timeout=1)

    @patch("modules.post_activation.wait_for_condition")
    def test_verify_managed_clusters_explicit_zero_allows_empty_hub(
        self, mock_wait, mock_secondary_client, mock_state_manager
    ):
        """Explicit --min-managed-clusters 0 preserves the empty-hub opt-out."""
        verifier = PostActivationVerification(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            has_observability=False,
            expected_managed_cluster_names=["cluster-a"],
            min_managed_clusters=0,
            enforce_expected_managed_cluster_names=False,
        )
        mock_secondary_client.list_custom_resources.return_value = [
            {"metadata": {"name": LOCAL_CLUSTER_NAME}},
        ]

        def capture_wait(*args, **kwargs):
            condition_fn = kwargs.get("condition_fn", args[1])
            result = condition_fn()
            assert isinstance(result, WaitConditionResult)
            assert result.done is True
            return True

        mock_wait.side_effect = capture_wait

        verifier._verify_managed_clusters_connected(timeout=1)

    @patch("modules.post_activation.wait_for_condition")
    def test_verify_managed_clusters_timeout(self, mock_wait, post_verify_with_obs, mock_secondary_client):
        """Test timeout while waiting for clusters."""
        mock_wait.return_value = False  # Timeout

        mock_secondary_client.list_custom_resources.return_value = [
            {
                "metadata": {"name": "cluster1"},
                "spec": {"managedClusterClientConfigs": [{"url": "https://api.cluster1:6443"}]},
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
                # Subsequent calls - the clusters reconnected
                return args[1]().done

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
        """Test when no managed clusters exist under a positive floor - should timeout waiting for clusters."""
        post_verify_with_obs.min_managed_clusters = 1
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

        with pytest.raises(SwitchoverError, match="bootstrap-hub-kubeconfig secret not visible"):
            post_verify_no_obs._wait_for_secret_visibility(mock_v1, "cluster-a", MANAGED_CLUSTER_AGENT_NAMESPACE)

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

    def test_restart_observatorium_api_404_blocks_when_observability_enabled(
        self, post_verify_with_obs, mock_secondary_client
    ):
        """Missing observatorium-api should block when Observability checks are enabled."""
        mock_secondary_client.rollout_restart_deployment.side_effect = ApiException(status=404)

        with pytest.raises(SwitchoverError) as exc_info:
            post_verify_with_obs._restart_observatorium_api()

        assert "observatorium-api deployment not found" in str(exc_info.value)

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

    def test_restart_observatorium_api_wraps_unexpected_errors(self, post_verify_with_obs, mock_secondary_client):
        """Unexpected restart errors should be classified as SwitchoverError."""
        mock_secondary_client.rollout_restart_deployment.side_effect = RuntimeError("rollout API failed")

        with pytest.raises(SwitchoverError) as exc_info:
            post_verify_with_obs._restart_observatorium_api()

        assert "Failed to restart observatorium-api" in str(exc_info.value)
        assert "rollout API failed" in str(exc_info.value)

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

        # The drain/health scope is the shared constant, not a second copy of the
        # selector string: a copy here and a constant in the teardown machine would
        # drift silently and check two different sets of pods.
        mock_secondary_client.get_pods.assert_called_once_with(
            namespace=OBSERVABILITY_NAMESPACE,
            label_selector=OBSERVABILITY_POD_LABEL_SELECTOR,
        )

    @patch("modules.post_activation.wait_for_condition")
    def test_verify_observability_pods_none_found(self, mock_wait, post_verify_with_obs, mock_secondary_client):
        """Test when no observability pods are found."""
        mock_wait.return_value = False
        mock_secondary_client.get_pods.return_value = []

        with pytest.raises(SwitchoverError) as exc_info:
            post_verify_with_obs._verify_observability_pods()

        assert "No Observability pods found" in str(exc_info.value)
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

        with pytest.raises(SwitchoverError) as exc_info:
            post_verify_with_obs._verify_observability_pods()

        assert "CrashLoopBackOff" in str(exc_info.value)

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

    def test_verify_error_handling(self, post_verify_with_obs, mock_state_manager):
        """SwitchoverError during verification should fail the phase cleanly."""
        with patch.object(
            post_verify_with_obs,
            "_verify_cluster_connections",
            side_effect=SwitchoverError("cluster verification failed"),
        ):
            result = post_verify_with_obs.verify()

        assert result is False
        mock_state_manager.add_error.assert_called_once_with(
            "cluster verification failed", "post_activation_verification"
        )

    def test_verify_reraises_programming_errors(self, post_verify_with_obs, mock_state_manager):
        """Programming errors should not be converted into phase failures."""
        with patch.object(
            post_verify_with_obs,
            "_verify_cluster_connections",
            side_effect=TypeError("bad call"),
        ):
            with pytest.raises(TypeError, match="bad call"):
                post_verify_with_obs.verify()

        mock_state_manager.add_error.assert_not_called()

    def test_verify_records_runtime_errors(self, post_verify_with_obs, mock_state_manager):
        """Runtime errors should remain recorded unexpected phase failures."""
        with patch.object(
            post_verify_with_obs,
            "_verify_cluster_connections",
            side_effect=RuntimeError("temporary runtime issue"),
        ):
            result = post_verify_with_obs.verify()

        assert result is False
        mock_state_manager.add_error.assert_called_once_with(
            "Unexpected: temporary runtime issue", "post_activation_verification"
        )

    @pytest.mark.parametrize("has_obs", [True, False])
    @patch("modules.post_activation.wait_for_condition")
    def test_observability_verification_conditional(
        self, mock_wait, mock_secondary_client, mock_state_manager, has_obs
    ):
        """Test that observability verification is conditional."""
        mock_wait.side_effect = _poll_until_done

        verify = PostActivationVerification(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            has_observability=has_obs,
        )

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
        mock_secondary_client.get_statefulset.return_value = {
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
        mock_secondary_client.get_pods.return_value = [
            {
                "metadata": {"name": "observability-pod"},
                "status": {
                    "phase": "Running",
                    "conditions": [{"type": "Ready", "status": "True"}],
                    "containerStatuses": [{"name": "collector", "state": {"running": {}}}],
                },
            }
        ]
        mock_secondary_client.get_route_host.return_value = "grafana.example.com"

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
  kubeconfig: Y2x1c3RlcnM6Ci0gY2x1c3Rlcjoge3NlcnZlcjogJ2h0dHBzOi8vYXBpLm5ldy1odWIuZXhhbXBsZTo2NDQzJ30K
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
            result = verify._force_klusterlet_reconnect("test-cluster", "test-context", SPOKE_URL, HERMETIC_HUB_SERVER)

        assert result is True
        verify._build_managed_cluster_clients.assert_called_once_with("test-context", SPOKE_URL)
        mock_secondary_client.get_secret.assert_called_once_with(namespace="test-cluster", name="test-cluster-import")
        mock_v1.delete_namespaced_secret.assert_not_called()
        mock_v1.patch_namespaced_secret.assert_called_once()

    def test_force_klusterlet_reconnect_patches_existing_bootstrap_secret_without_delete(
        self, mock_secondary_client, mock_state_manager
    ):
        """Existing bootstrap secret updates must not delete the old secret first."""
        verify = self._make_verify(mock_secondary_client, mock_state_manager)

        import_docs = """---
apiVersion: v1
kind: Secret
metadata:
  name: bootstrap-hub-kubeconfig
  namespace: open-cluster-management-agent
data:
  kubeconfig: Y2x1c3RlcnM6Ci0gY2x1c3Rlcjoge3NlcnZlcjogJ2h0dHBzOi8vYXBpLm5ldy1odWIuZXhhbXBsZTo2NDQzJ30K
"""
        mock_secondary_client.get_secret.return_value = {
            "data": {"import.yaml": base64.b64encode(import_docs.encode()).decode()}
        }

        mock_v1 = Mock()
        mock_apps_v1 = Mock()
        verify._build_managed_cluster_clients = Mock(return_value=(mock_v1, mock_apps_v1))

        with patch("modules.post_activation.wait_for_condition", return_value=True):
            result = verify._force_klusterlet_reconnect("test-cluster", "test-context", SPOKE_URL, HERMETIC_HUB_SERVER)

        assert result is True
        mock_v1.delete_namespaced_secret.assert_not_called()
        mock_v1.create_namespaced_secret.assert_not_called()
        mock_v1.patch_namespaced_secret.assert_called_once()
        mock_apps_v1.patch_namespaced_deployment.assert_called_once()

    def test_force_klusterlet_reconnect_failed_patch_keeps_secret_and_skips_restart(
        self, mock_secondary_client, mock_state_manager
    ):
        """Failed patch must not delete an existing bootstrap secret or restart klusterlet."""
        verify = self._make_verify(mock_secondary_client, mock_state_manager)

        import_docs = """---
apiVersion: v1
kind: Secret
metadata:
  name: bootstrap-hub-kubeconfig
  namespace: open-cluster-management-agent
data:
  kubeconfig: Y2x1c3RlcnM6Ci0gY2x1c3Rlcjoge3NlcnZlcjogJ2h0dHBzOi8vYXBpLm5ldy1odWIuZXhhbXBsZTo2NDQzJ30K
"""
        mock_secondary_client.get_secret.return_value = {
            "data": {"import.yaml": base64.b64encode(import_docs.encode()).decode()}
        }

        mock_v1 = Mock()
        mock_apps_v1 = Mock()
        mock_v1.patch_namespaced_secret.side_effect = ApiException(status=403, reason="Forbidden")
        verify._build_managed_cluster_clients = Mock(return_value=(mock_v1, mock_apps_v1))

        result = verify._force_klusterlet_reconnect("test-cluster", "test-context", SPOKE_URL, HERMETIC_HUB_SERVER)

        assert result is False
        mock_v1.delete_namespaced_secret.assert_not_called()
        mock_v1.create_namespaced_secret.assert_not_called()
        mock_apps_v1.patch_namespaced_deployment.assert_not_called()

    def test_force_klusterlet_reconnect_creates_absent_bootstrap_secret_without_delete(
        self, mock_secondary_client, mock_state_manager
    ):
        """Absent bootstrap secret should be created without a delete attempt."""
        verify = self._make_verify(mock_secondary_client, mock_state_manager)

        import_docs = """---
apiVersion: v1
kind: Secret
metadata:
  name: bootstrap-hub-kubeconfig
  namespace: open-cluster-management-agent
data:
  kubeconfig: Y2x1c3RlcnM6Ci0gY2x1c3Rlcjoge3NlcnZlcjogJ2h0dHBzOi8vYXBpLm5ldy1odWIuZXhhbXBsZTo2NDQzJ30K
"""
        mock_secondary_client.get_secret.return_value = {
            "data": {"import.yaml": base64.b64encode(import_docs.encode()).decode()}
        }

        mock_v1 = Mock()
        mock_apps_v1 = Mock()
        mock_v1.patch_namespaced_secret.side_effect = ApiException(status=404, reason="Not Found")
        verify._build_managed_cluster_clients = Mock(return_value=(mock_v1, mock_apps_v1))

        with patch("modules.post_activation.wait_for_condition", return_value=True):
            result = verify._force_klusterlet_reconnect("test-cluster", "test-context", SPOKE_URL, HERMETIC_HUB_SERVER)

        assert result is True
        mock_v1.delete_namespaced_secret.assert_not_called()
        mock_v1.create_namespaced_secret.assert_called_once()
        mock_apps_v1.patch_namespaced_deployment.assert_called_once()

    def test_force_klusterlet_reconnect_creates_the_manifest_secret_as_a_typed_body(
        self, mock_secondary_client, mock_state_manager
    ):
        """The create body is a V1Secret whose wire form is the manifest's Secret document."""
        from kubernetes.client import ApiClient, V1Secret

        verify = self._make_verify(mock_secondary_client, mock_state_manager)

        import_docs = """---
apiVersion: v1
kind: Secret
metadata:
  name: bootstrap-hub-kubeconfig
  namespace: open-cluster-management-agent
  labels:
    app: klusterlet
type: Opaque
data:
  kubeconfig: Y2x1c3RlcnM6Ci0gY2x1c3Rlcjoge3NlcnZlcjogJ2h0dHBzOi8vYXBpLm5ldy1odWIuZXhhbXBsZTo2NDQzJ30K
"""
        mock_secondary_client.get_secret.return_value = {
            "data": {"import.yaml": base64.b64encode(import_docs.encode()).decode()}
        }

        mock_v1 = Mock()
        mock_apps_v1 = Mock()
        mock_v1.patch_namespaced_secret.side_effect = ApiException(status=404, reason="Not Found")
        verify._build_managed_cluster_clients = Mock(return_value=(mock_v1, mock_apps_v1))

        with patch("modules.post_activation.wait_for_condition", return_value=True):
            assert (
                verify._force_klusterlet_reconnect("test-cluster", "test-context", SPOKE_URL, HERMETIC_HUB_SERVER)
                is True
            )

        create = mock_v1.create_namespaced_secret.call_args
        assert create.kwargs["namespace"] == "open-cluster-management-agent"
        assert isinstance(create.kwargs["body"], V1Secret)
        assert ApiClient().sanitize_for_serialization(create.kwargs["body"]) == yaml.safe_load(import_docs)

    def test_force_klusterlet_reconnect_create_body_accepts_an_unquoted_yaml_timestamp(
        self, mock_secondary_client, mock_state_manager
    ):
        """YAML loads an unquoted timestamp as a datetime; the typed create body must still be
        built, and its wire form is the one the client would send for the document itself."""
        from kubernetes.client import ApiClient

        verify = self._make_verify(mock_secondary_client, mock_state_manager)

        import_docs = """---
apiVersion: v1
kind: Secret
metadata:
  name: bootstrap-hub-kubeconfig
  namespace: open-cluster-management-agent
  creationTimestamp: 2025-01-01T00:00:00Z
type: Opaque
data:
  kubeconfig: Y2x1c3RlcnM6Ci0gY2x1c3Rlcjoge3NlcnZlcjogJ2h0dHBzOi8vYXBpLm5ldy1odWIuZXhhbXBsZTo2NDQzJ30K
"""
        mock_secondary_client.get_secret.return_value = {
            "data": {"import.yaml": base64.b64encode(import_docs.encode()).decode()}
        }

        mock_v1 = Mock()
        mock_apps_v1 = Mock()
        mock_v1.patch_namespaced_secret.side_effect = ApiException(status=404, reason="Not Found")
        verify._build_managed_cluster_clients = Mock(return_value=(mock_v1, mock_apps_v1))

        with patch("modules.post_activation.wait_for_condition", return_value=True):
            assert (
                verify._force_klusterlet_reconnect("test-cluster", "test-context", SPOKE_URL, HERMETIC_HUB_SERVER)
                is True
            )

        body = mock_v1.create_namespaced_secret.call_args.kwargs["body"]
        api = ApiClient()
        assert api.sanitize_for_serialization(body) == api.sanitize_for_serialization(yaml.safe_load(import_docs))

    def test_force_klusterlet_reconnect_create_body_keeps_every_manifest_field(
        self, mock_secondary_client, mock_state_manager
    ):
        """No manifest field is dropped on the way to the typed create body: owner references,
        finalizers and the rest of the metadata reach the wire unchanged."""
        from kubernetes.client import ApiClient

        verify = self._make_verify(mock_secondary_client, mock_state_manager)

        import_docs = """---
apiVersion: v1
kind: Secret
metadata:
  name: bootstrap-hub-kubeconfig
  namespace: open-cluster-management-agent
  labels:
    app: klusterlet
  annotations:
    note: bootstrap
  finalizers:
  - example.com/keep
  ownerReferences:
  - apiVersion: v1
    kind: Namespace
    name: open-cluster-management-agent
    uid: 0a1b2c3d-owner-uid
    controller: false
    blockOwnerDeletion: true
type: Opaque
immutable: false
data:
  kubeconfig: Y2x1c3RlcnM6Ci0gY2x1c3Rlcjoge3NlcnZlcjogJ2h0dHBzOi8vYXBpLm5ldy1odWIuZXhhbXBsZTo2NDQzJ30K
stringData:
  extra: value
"""
        mock_secondary_client.get_secret.return_value = {
            "data": {"import.yaml": base64.b64encode(import_docs.encode()).decode()}
        }

        mock_v1 = Mock()
        mock_apps_v1 = Mock()
        mock_v1.patch_namespaced_secret.side_effect = ApiException(status=404, reason="Not Found")
        verify._build_managed_cluster_clients = Mock(return_value=(mock_v1, mock_apps_v1))

        with patch("modules.post_activation.wait_for_condition", return_value=True):
            assert (
                verify._force_klusterlet_reconnect("test-cluster", "test-context", SPOKE_URL, HERMETIC_HUB_SERVER)
                is True
            )

        body = mock_v1.create_namespaced_secret.call_args.kwargs["body"]
        assert ApiClient().sanitize_for_serialization(body) == yaml.safe_load(import_docs)

    def test_force_klusterlet_reconnect_retries_patch_after_create_conflict(
        self, mock_secondary_client, mock_state_manager
    ):
        """A create 409 race should retry patching the now-existing bootstrap secret."""
        verify = self._make_verify(mock_secondary_client, mock_state_manager)

        import_docs = """---
apiVersion: v1
kind: Secret
metadata:
  name: bootstrap-hub-kubeconfig
  namespace: open-cluster-management-agent
data:
  kubeconfig: Y2x1c3RlcnM6Ci0gY2x1c3Rlcjoge3NlcnZlcjogJ2h0dHBzOi8vYXBpLm5ldy1odWIuZXhhbXBsZTo2NDQzJ30K
"""
        mock_secondary_client.get_secret.return_value = {
            "data": {"import.yaml": base64.b64encode(import_docs.encode()).decode()}
        }

        mock_v1 = Mock()
        mock_apps_v1 = Mock()
        mock_v1.patch_namespaced_secret.side_effect = [
            ApiException(status=404, reason="Not Found"),
            None,
        ]
        mock_v1.create_namespaced_secret.side_effect = ApiException(status=409, reason="Already Exists")
        verify._build_managed_cluster_clients = Mock(return_value=(mock_v1, mock_apps_v1))

        with patch("modules.post_activation.wait_for_condition", return_value=True):
            result = verify._force_klusterlet_reconnect("test-cluster", "test-context", SPOKE_URL, HERMETIC_HUB_SERVER)

        assert result is True
        mock_v1.delete_namespaced_secret.assert_not_called()
        assert mock_v1.patch_namespaced_secret.call_count == 2
        mock_v1.create_namespaced_secret.assert_called_once()
        mock_apps_v1.patch_namespaced_deployment.assert_called_once()

    def test_force_klusterlet_reconnect_uses_manifest_namespace_for_wait_and_restart(
        self, mock_secondary_client, mock_state_manager
    ):
        """Secret visibility and restart should use the bootstrap secret manifest namespace."""
        verify = self._make_verify(mock_secondary_client, mock_state_manager)

        import_docs = """---
apiVersion: v1
kind: Secret
metadata:
  name: bootstrap-hub-kubeconfig
  namespace: custom-agent
data:
  kubeconfig: Y2x1c3RlcnM6Ci0gY2x1c3Rlcjoge3NlcnZlcjogJ2h0dHBzOi8vYXBpLm5ldy1odWIuZXhhbXBsZTo2NDQzJ30K
"""
        mock_secondary_client.get_secret.return_value = {
            "data": {"import.yaml": base64.b64encode(import_docs.encode()).decode()}
        }

        mock_v1 = Mock()
        mock_apps_v1 = Mock()
        verify._build_managed_cluster_clients = Mock(return_value=(mock_v1, mock_apps_v1))

        result = verify._force_klusterlet_reconnect("test-cluster", "test-context", SPOKE_URL, HERMETIC_HUB_SERVER)

        assert result is True
        mock_v1.patch_namespaced_secret.assert_called_once()
        assert mock_v1.patch_namespaced_secret.call_args.kwargs["namespace"] == "custom-agent"
        mock_v1.read_namespaced_secret.assert_called_once()
        assert mock_v1.read_namespaced_secret.call_args.kwargs["namespace"] == "custom-agent"
        mock_apps_v1.patch_namespaced_deployment.assert_called_once()
        assert mock_apps_v1.patch_namespaced_deployment.call_args.kwargs["namespace"] == "custom-agent"

    def test_force_klusterlet_reconnect_fails_when_import_manifest_missing_bootstrap_secret(
        self, mock_secondary_client, mock_state_manager
    ):
        """Reconnect must fail if import.yaml has no bootstrap-hub-kubeconfig Secret."""
        verify = self._make_verify(mock_secondary_client, mock_state_manager)

        import_docs = """---
apiVersion: v1
kind: ConfigMap
metadata:
  name: unrelated
  namespace: open-cluster-management-agent
data:
  key: value
"""
        mock_secondary_client.get_secret.return_value = {
            "data": {"import.yaml": base64.b64encode(import_docs.encode()).decode()}
        }

        mock_v1 = Mock()
        mock_apps_v1 = Mock()
        verify._build_managed_cluster_clients = Mock(return_value=(mock_v1, mock_apps_v1))

        with patch("modules.post_activation.wait_for_condition", return_value=True):
            result = verify._force_klusterlet_reconnect("test-cluster", "test-context", SPOKE_URL, HERMETIC_HUB_SERVER)

        assert result is False
        mock_v1.patch_namespaced_secret.assert_not_called()
        mock_v1.create_namespaced_secret.assert_not_called()
        mock_apps_v1.patch_namespaced_deployment.assert_not_called()

    def test_force_klusterlet_reconnect_no_secret(self, mock_secondary_client, mock_state_manager):
        """Test klusterlet reconnect when import secret not found."""
        verify = self._make_verify(mock_secondary_client, mock_state_manager)
        mock_secondary_client.get_secret.return_value = None

        result = verify._force_klusterlet_reconnect("test-cluster", "test-context", SPOKE_URL, HERMETIC_HUB_SERVER)

        assert result is False

    def test_force_klusterlet_reconnect_fails_on_bootstrap_secret_create_error(
        self, mock_secondary_client, mock_state_manager
    ):
        """Non-conflict bootstrap secret creation failures must fail remediation clearly."""
        verify = self._make_verify(mock_secondary_client, mock_state_manager)

        import_docs = """---
apiVersion: v1
kind: Secret
metadata:
  name: bootstrap-hub-kubeconfig
  namespace: open-cluster-management-agent
data:
  kubeconfig: Y2x1c3RlcnM6Ci0gY2x1c3Rlcjoge3NlcnZlcjogJ2h0dHBzOi8vYXBpLm5ldy1odWIuZXhhbXBsZTo2NDQzJ30K
"""
        mock_secondary_client.get_secret.return_value = {
            "data": {"import.yaml": base64.b64encode(import_docs.encode()).decode()}
        }

        mock_v1 = Mock()
        mock_apps_v1 = Mock()
        mock_v1.patch_namespaced_secret.side_effect = ApiException(status=404, reason="Not Found")
        mock_v1.create_namespaced_secret.side_effect = ApiException(status=403, reason="Forbidden")
        verify._build_managed_cluster_clients = Mock(return_value=(mock_v1, mock_apps_v1))

        result = verify._force_klusterlet_reconnect("test-cluster", "test-context", SPOKE_URL, HERMETIC_HUB_SERVER)

        assert result is False
        mock_apps_v1.patch_namespaced_deployment.assert_not_called()

    def test_force_klusterlet_reconnect_fails_when_bootstrap_secret_never_visible(
        self, mock_secondary_client, mock_state_manager
    ):
        """A secret visibility timeout must fail remediation instead of continuing to restart."""
        verify = self._make_verify(mock_secondary_client, mock_state_manager)

        import_docs = """---
apiVersion: v1
kind: Secret
metadata:
  name: bootstrap-hub-kubeconfig
  namespace: open-cluster-management-agent
data:
  kubeconfig: Y2x1c3RlcnM6Ci0gY2x1c3Rlcjoge3NlcnZlcjogJ2h0dHBzOi8vYXBpLm5ldy1odWIuZXhhbXBsZTo2NDQzJ30K
"""
        mock_secondary_client.get_secret.return_value = {
            "data": {"import.yaml": base64.b64encode(import_docs.encode()).decode()}
        }

        mock_v1 = Mock()
        mock_apps_v1 = Mock()
        verify._build_managed_cluster_clients = Mock(return_value=(mock_v1, mock_apps_v1))

        with patch("modules.post_activation.wait_for_condition", return_value=False):
            result = verify._force_klusterlet_reconnect("test-cluster", "test-context", SPOKE_URL, HERMETIC_HUB_SERVER)

        assert result is False
        mock_apps_v1.patch_namespaced_deployment.assert_not_called()

    def test_parallel_workers_use_isolated_clients(self, mock_secondary_client, mock_state_manager):
        """Each parallel worker must receive its own ApiClient; no global context mutation."""
        import threading

        verify = self._make_verify(mock_secondary_client, mock_state_manager)

        call_log = []
        call_lock = threading.Lock()

        def fake_build_clients(context_name, api_url):
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
  kubeconfig: Y2x1c3RlcnM6Ci0gY2x1c3Rlcjoge3NlcnZlcjogJ2h0dHBzOi8vYXBpLm5ldy1odWIuZXhhbXBsZTo2NDQzJ30K
"""
        mock_secondary_client.get_secret.return_value = {
            "data": {"import.yaml": base64.b64encode(import_docs.encode()).decode()}
        }

        with patch("modules.post_activation.wait_for_condition", return_value=True):
            from concurrent.futures import ThreadPoolExecutor, as_completed

            contexts = ["ctx-cluster-a", "ctx-cluster-b"]
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [
                    executor.submit(
                        verify._force_klusterlet_reconnect,
                        f"cluster-{i}",
                        ctx,
                        f"https://api.cluster-{i}:6443",
                        HERMETIC_HUB_SERVER,
                    )
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

        mock_wait.side_effect = _poll_until_done

        state = StateManager(str(tmp_path / "state.json"))
        state.set_phase(Phase.POST_ACTIVATION)
        state.state["config"]["migration_backups"] = copy.deepcopy(_completed_journal())

        verify = PostActivationVerification(
            secondary_client=mock_secondary_client,
            state_manager=state,
            has_observability=True,
        )

        # Mock successful flow
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
        mock_secondary_client.get_pods.return_value = [
            {
                "metadata": {"name": "pod1"},
                "status": {
                    "phase": "Running",
                    "conditions": [{"type": "Ready", "status": "True"}],
                },
            }
        ]
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
        mock_secondary_client.get_statefulset.return_value = {
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
        """A missing KUBECONFIG entry aborts the whole load (fail-closed, design §1, #349)."""
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

        # One existing, one missing: no partial view is ever returned
        with patch.dict("os.environ", {"KUBECONFIG": f"{existing}:{missing}"}):
            with pytest.raises(SwitchoverError, match=r"kubeconfig_load_failed \(missing\).*does_not_exist"):
                verify._load_kubeconfig_data()

    def test_load_kubeconfig_default_path(self, mock_secondary_client, mock_state_manager, tmp_path):
        """With KUBECONFIG unset, ~/.kube/config is the one file read."""
        (tmp_path / ".kube").mkdir()
        (tmp_path / ".kube" / "config").write_text(_kubeconfig_yaml(clusters=[{"name": "dflt", "cluster": {}}]))
        verify = PostActivationVerification(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            has_observability=False,
        )

        with patch.dict("os.environ", {"HOME": str(tmp_path)}):
            os.environ.pop("KUBECONFIG", None)
            data = verify._load_kubeconfig_data()
        assert [c["name"] for c in data["clusters"]] == ["dflt"]

    def test_load_kubeconfig_missing_default_path_aborts(self, mock_secondary_client, mock_state_manager, tmp_path):
        verify = PostActivationVerification(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            has_observability=False,
        )

        with patch.dict("os.environ", {"HOME": str(tmp_path)}):
            os.environ.pop("KUBECONFIG", None)
            with pytest.raises(SwitchoverError, match=r"kubeconfig_load_failed \(missing\).*\.kube/config"):
                verify._load_kubeconfig_data()


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

    def test_worker_exception_uses_fallback_result(self, mock_secondary_client, mock_state_manager, caplog):
        """Worker exceptions should be logged and converted to their fallback result."""
        pav = _make_pav(mock_secondary_client, mock_state_manager)
        failed_future = Future()
        failed_future.set_exception(RuntimeError("worker exploded"))
        successful_future = Future()
        successful_future.set_result(("cluster-ok", "verified", "ctx-ok"))

        caplog.set_level(logging.ERROR, logger="acm_switchover")

        results = pav._collect_klusterlet_future_results(
            {
                failed_future: ("cluster-failed", "worker_timeout", None),
                successful_future: ("cluster-ok", "worker_timeout", None),
            },
            "checking",
        )

        assert ("cluster-failed", "worker_timeout", None) in results
        assert ("cluster-ok", "verified", "ctx-ok") in results
        assert "Worker thread for cluster-failed failed while checking klusterlet" in caplog.text
        assert "worker exploded" in caplog.text

    def test_filters_local_cluster(self, mock_secondary_client, mock_state_manager):
        """local-cluster should be excluded from klusterlet verification."""
        pav = _make_pav(mock_secondary_client, mock_state_manager)

        mock_secondary_client.list_custom_resources.return_value = [
            {
                "metadata": {"name": LOCAL_CLUSTER_NAME},
                "spec": {"managedClusterClientConfigs": [{"url": "https://api.local:6443"}]},
            },
        ]

        with patch.object(pav, "_expected_hub_endpoint", return_value=("https://hub:6443", None)):
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
                "metadata": {"name": "c1", "uid": "c1-uid"},
                "spec": {"managedClusterClientConfigs": [{"url": "https://api.c1:6443"}]},
            },
            {
                "metadata": {"name": "c2", "uid": "c2-uid"},
                "spec": {"managedClusterClientConfigs": [{"url": "https://api.c2:6443"}]},
            },
            {
                "metadata": {"name": "c3", "uid": "c3-uid"},
                "spec": {"managedClusterClientConfigs": [{"url": "https://api.c3:6443"}]},
            },
        ]

        kube_data = {
            "users": [{"name": "u", "user": {"token": "t"}}],
            "contexts": [
                {"name": "ctx-c1", "context": {"cluster": "kc1", "user": "u"}},
                {"name": "ctx-c2", "context": {"cluster": "kc2", "user": "u"}},
                {"name": "ctx-c3", "context": {"cluster": "kc3", "user": "u"}},
            ],
            "clusters": [
                {"name": "kc1", "cluster": {"server": "https://api.c1:6443"}},
                {"name": "kc2", "cluster": {"server": "https://api.c2:6443"}},
                {"name": "kc3", "cluster": {"server": "https://api.c3:6443"}},
            ],
        }

        c2_results = iter(["wrong_hub", "verified"])

        def fake_check(ctx, name, hub, api_url):
            if name == "c2":
                return next(c2_results), None
            return {"c1": "verified", "c3": "unreachable"}[name], None

        with patch.object(pav, "_expected_hub_endpoint", return_value=("https://hub:6443", None)):
            with patch.object(pav, "_load_kubeconfig_data", return_value=kube_data):
                with patch.object(pav, "_check_klusterlet_connection", side_effect=fake_check):
                    with patch.object(pav, "_force_klusterlet_reconnect", return_value=True) as mock_fix:
                        pav._verify_klusterlet_connections()

        # wrong_hub cluster c2 should be fixed
        mock_fix.assert_called_once()
        assert mock_fix.call_args[0][0] == "c2"

    def test_wrong_hub_remaining_after_remediation_is_fatal(self, mock_secondary_client, mock_state_manager):
        """Wrong-hub klusterlet state must fail when remediation does not move it."""
        pav = _make_pav(mock_secondary_client, mock_state_manager)

        mock_secondary_client.list_custom_resources.return_value = [
            {
                "metadata": {"name": "c1", "uid": "c1-uid"},
                "spec": {"managedClusterClientConfigs": [{"url": "https://api.c1:6443"}]},
            },
        ]
        kube_data = {
            "users": [{"name": "u", "user": {"token": "t"}}],
            "contexts": [{"name": "ctx-c1", "context": {"cluster": "kc1", "user": "u"}}],
            "clusters": [{"name": "kc1", "cluster": {"server": "https://api.c1:6443"}}],
        }

        with patch.object(pav, "_expected_hub_endpoint", return_value=("https://hub:6443", None)):
            with patch.object(pav, "_load_kubeconfig_data", return_value=kube_data):
                with patch.object(pav, "_force_klusterlet_reconnect", return_value=True):
                    with patch.object(
                        pav,
                        "_check_klusterlet_connection",
                        side_effect=[("wrong_hub", None), ("wrong_hub", None)],
                    ):
                        with patch(
                            "modules.post_activation.wait_for_condition",
                            side_effect=lambda description, condition_fn, **kwargs: condition_fn().done,
                        ):
                            with pytest.raises(
                                SwitchoverError,
                                match="still connected to the wrong hub",
                            ):
                                pav._verify_klusterlet_connections()

    def test_wrong_hub_after_remediation_waits_for_secret_convergence(self, mock_secondary_client, mock_state_manager):
        """Post-remediation recheck must poll through stale hub-kubeconfig-secret state."""
        pav = _make_pav(mock_secondary_client, mock_state_manager)

        mock_secondary_client.list_custom_resources.return_value = [
            {
                "metadata": {"name": "c1", "uid": "c1-uid"},
                "spec": {"managedClusterClientConfigs": [{"url": "https://api.c1:6443"}]},
            },
        ]
        kube_data = {
            "users": [{"name": "u", "user": {"token": "t"}}],
            "contexts": [{"name": "ctx-c1", "context": {"cluster": "kc1", "user": "u"}}],
            "clusters": [{"name": "kc1", "cluster": {"server": "https://api.c1:6443"}}],
        }

        def fake_wait_for_condition(description, condition_fn, **kwargs):
            assert "post-remediation klusterlet" in description
            for _ in range(3):
                result = condition_fn()
                if result.done:
                    return True
            return False

        with patch.object(pav, "_expected_hub_endpoint", return_value=("https://hub:6443", None)):
            with patch.object(pav, "_load_kubeconfig_data", return_value=kube_data):
                with patch.object(pav, "_force_klusterlet_reconnect", return_value=True):
                    with patch.object(
                        pav,
                        "_check_klusterlet_connection",
                        side_effect=[("wrong_hub", None), ("wrong_hub", None), ("verified", None)],
                    ):
                        with patch(
                            "modules.post_activation.wait_for_condition",
                            side_effect=fake_wait_for_condition,
                        ):
                            pav._verify_klusterlet_connections()

    def test_failed_remediation_is_fatal_after_recheck(self, mock_secondary_client, mock_state_manager):
        """A failed klusterlet remediation attempt must not be logged as success."""
        pav = _make_pav(mock_secondary_client, mock_state_manager)

        mock_secondary_client.list_custom_resources.return_value = [
            {
                "metadata": {"name": "c1", "uid": "c1-uid"},
                "spec": {"managedClusterClientConfigs": [{"url": "https://api.c1:6443"}]},
            },
        ]
        kube_data = {
            "users": [{"name": "u", "user": {"token": "t"}}],
            "contexts": [{"name": "ctx-c1", "context": {"cluster": "kc1", "user": "u"}}],
            "clusters": [{"name": "kc1", "cluster": {"server": "https://api.c1:6443"}}],
        }

        with patch.object(pav, "_expected_hub_endpoint", return_value=("https://hub:6443", None)):
            with patch.object(pav, "_load_kubeconfig_data", return_value=kube_data):
                with patch.object(pav, "_force_klusterlet_reconnect", return_value=False):
                    with patch.object(
                        pav,
                        "_check_klusterlet_connection",
                        side_effect=[("wrong_hub", None), ("wrong_hub", None)],
                    ):
                        with patch(
                            "modules.post_activation.wait_for_condition",
                            side_effect=lambda description, condition_fn, **kwargs: condition_fn().done,
                        ):
                            with pytest.raises(SwitchoverError, match="Klusterlet remediation failed"):
                                pav._verify_klusterlet_connections()

    @pytest.mark.parametrize("reason", ["expected_endpoint_unavailable", "expected_endpoint_malformed"])
    def test_an_unusable_expected_hub_endpoint_fails_without_inspecting(
        self, mock_secondary_client, mock_state_manager, reason
    ):
        """Amendment §2.4: no usable import-secret endpoint is `unverified`; no spoke client is built."""
        pav = _make_pav(mock_secondary_client, mock_state_manager)
        mock_secondary_client.list_custom_resources.return_value = [
            {
                "metadata": {"name": "c1"},
                "spec": {"managedClusterClientConfigs": [{"url": "https://api.c1:6443"}]},
            },
        ]
        kube_data = {
            "users": [{"name": "u", "user": {"token": "t"}}],
            "contexts": [{"name": "ctx-c1", "context": {"cluster": "kc1", "user": "u"}}],
            "clusters": [{"name": "kc1", "cluster": {"server": "https://api.c1:6443"}}],
        }

        with patch.object(pav, "_expected_hub_endpoint", return_value=(None, reason)):
            with patch.object(pav, "_load_kubeconfig_data", return_value=kube_data):
                with patch.object(pav, "_build_managed_cluster_clients") as build:
                    with pytest.raises(SwitchoverError, match=rf"c1 \({reason}\)"):
                        pav._verify_klusterlet_connections()

        build.assert_not_called()

    def test_unloadable_kubeconfig_fails_instead_of_skipping(self, mock_secondary_client, mock_state_manager):
        """GLM-H3 (#349): a load failure propagates; no cluster is checked."""
        pav = _make_pav(mock_secondary_client, mock_state_manager)

        with patch.object(
            pav, "_load_kubeconfig_data", side_effect=SwitchoverError("kubeconfig_load_failed (missing): x")
        ):
            with patch.object(pav, "_check_klusterlet_connection") as mock_check:
                with pytest.raises(SwitchoverError, match="kubeconfig_load_failed"):
                    pav._verify_klusterlet_connections()

        mock_check.assert_not_called()

    def test_cluster_without_api_url_fails_without_any_context_lookup(self, mock_secondary_client, mock_state_manager):
        """Decision 3: no usable API URL is `endpoint_unavailable` - never a name-only context guess."""
        pav = _make_pav(mock_secondary_client, mock_state_manager)

        mock_secondary_client.list_custom_resources.return_value = [
            {"metadata": {"name": "orphan"}, "spec": {}},
        ]
        # A context named exactly like the cluster must not be selected.
        kube_data = {
            "users": [{"name": "u", "user": {"token": "t"}}],
            "contexts": [{"name": "orphan", "context": {"cluster": "korphan"}}],
            "clusters": [{"name": "korphan", "cluster": {"server": "https://api.orphan:6443"}}],
        }

        with patch.object(pav, "_load_kubeconfig_data", return_value=kube_data):
            with patch("modules.post_activation.select_spoke_context") as select:
                with patch.object(pav, "_expected_hub_endpoint") as expected:
                    with patch.object(pav, "_build_managed_cluster_clients") as build:
                        with pytest.raises(SwitchoverError, match=r"orphan \(endpoint_unavailable\)"):
                            pav._verify_klusterlet_connections()

        select.assert_not_called()
        expected.assert_not_called()
        build.assert_not_called()

    def test_check_cluster_exception_fails_verification(self, mock_secondary_client, mock_state_manager):
        """Unexpected per-cluster probe exceptions should fail post-activation."""
        pav = _make_pav(mock_secondary_client, mock_state_manager)

        mock_secondary_client.list_custom_resources.return_value = [
            {
                "metadata": {"name": "c1"},
                "spec": {"managedClusterClientConfigs": [{"url": "https://api.c1:6443"}]},
            },
        ]

        kube_data = {
            "users": [{"name": "u", "user": {"token": "t"}}],
            "contexts": [{"name": "ctx-c1", "context": {"cluster": "kc1", "user": "u"}}],
            "clusters": [{"name": "kc1", "cluster": {"server": "https://api.c1:6443"}}],
        }

        with patch.object(pav, "_expected_hub_endpoint", return_value=("https://hub:6443", None)):
            with patch.object(pav, "_load_kubeconfig_data", return_value=kube_data):
                with patch.object(
                    pav,
                    "_check_klusterlet_connection",
                    side_effect=RuntimeError("boom"),
                ):
                    with pytest.raises(SwitchoverError, match="Klusterlet verification failed"):
                        pav._verify_klusterlet_connections()

    def test_initial_check_timeout_fails_closed(self, mock_secondary_client, mock_state_manager, caplog):
        """Timed-out initial klusterlet checks must fail post-activation."""
        pav = _make_pav(mock_secondary_client, mock_state_manager)
        mock_secondary_client.list_custom_resources.return_value = [
            {
                "metadata": {"name": "c1"},
                "spec": {"managedClusterClientConfigs": [{"url": "https://api.c1:6443"}]},
            },
        ]
        kube_data = {
            "users": [{"name": "u", "user": {"token": "t"}}],
            "contexts": [{"name": "ctx-c1", "context": {"cluster": "kc1", "user": "u"}}],
            "clusters": [{"name": "kc1", "cluster": {"server": "https://api.c1:6443"}}],
        }
        caplog.set_level(logging.INFO, logger="acm_switchover")

        with patch.object(pav, "_expected_hub_endpoint", return_value=("https://hub:6443", None)):
            with patch.object(pav, "_load_kubeconfig_data", return_value=kube_data):
                with patch.object(pav, "_check_klusterlet_connection", return_value=("verified", None)):
                    with patch(
                        "modules.post_activation.wait",
                        side_effect=lambda futures, timeout: (set(), set(futures)),
                    ):
                        with pytest.raises(SwitchoverError, match="Klusterlet verification timed out"):
                            pav._verify_klusterlet_connections()

        assert "Timed out checking klusterlet for c1" in caplog.text
        assert "c1" in caplog.text

    def test_no_context_remains_non_fatal_skip(self, mock_secondary_client, mock_state_manager, caplog):
        """Missing kubeconfig contexts should remain non-fatal klusterlet skips."""
        pav = _make_pav(mock_secondary_client, mock_state_manager)
        caplog.set_level(logging.INFO, logger="acm_switchover")

        mock_secondary_client.list_custom_resources.return_value = [
            {
                "metadata": {"name": "c1"},
                "spec": {"managedClusterClientConfigs": [{"url": "https://api.c1:6443"}]},
            },
        ]

        kube_data = {"contexts": [], "clusters": []}

        with patch.object(pav, "_expected_hub_endpoint", return_value=("https://hub:6443", None)):
            with patch.object(pav, "_load_kubeconfig_data", return_value=kube_data):
                with patch.object(pav, "_check_klusterlet_connection") as mock_check:
                    pav._verify_klusterlet_connections()

        mock_check.assert_not_called()
        assert "unreachable or uninspectable" in caplog.text
        assert "no context available" not in caplog.text

    def test_remediation_does_not_use_unbounded_as_completed(self, mock_secondary_client, mock_state_manager):
        """Timed-out remediation workers should be recorded as failed remediation."""
        pav = _make_pav(mock_secondary_client, mock_state_manager)
        mock_secondary_client.list_custom_resources.return_value = [
            {
                "metadata": {"name": "c1", "uid": "c1-uid"},
                "spec": {"managedClusterClientConfigs": [{"url": "https://api.c1:6443"}]},
            },
        ]
        kube_data = {
            "users": [{"name": "u", "user": {"token": "t"}}],
            "contexts": [{"name": "ctx-c1", "context": {"cluster": "kc1", "user": "u"}}],
            "clusters": [{"name": "kc1", "cluster": {"server": "https://api.c1:6443"}}],
        }
        wait_calls = 0
        remediation_exited = threading.Event()

        def fake_wait(futures, timeout):
            nonlocal wait_calls
            wait_calls += 1
            futures = set(futures)
            if wait_calls == 2:
                return set(), futures
            return futures, set()

        def slow_reconnect(_cluster_name, _context_name, _api_url, _expected_hub):
            time.sleep(0.05)
            remediation_exited.set()
            return True

        with patch.object(pav, "_expected_hub_endpoint", return_value=("https://hub:6443", None)):
            with patch.object(pav, "_load_kubeconfig_data", return_value=kube_data):
                with patch.object(
                    pav,
                    "_check_klusterlet_connection",
                    side_effect=[("wrong_hub", None), ("verified", None)],
                ):
                    with patch.object(pav, "_force_klusterlet_reconnect", side_effect=slow_reconnect):
                        with patch("modules.post_activation.wait", side_effect=fake_wait):
                            with patch(
                                "modules.post_activation.wait_for_condition",
                                side_effect=lambda description, condition_fn, **kwargs: condition_fn().done,
                            ):
                                with pytest.raises(
                                    SwitchoverError,
                                    match="Klusterlet remediation failed",
                                ):
                                    pav._verify_klusterlet_connections()
        assert remediation_exited.is_set()

    def test_recheck_does_not_use_unbounded_as_completed(self, mock_secondary_client, mock_state_manager):
        """Timed-out post-remediation rechecks should be treated as unverified."""
        pav = _make_pav(mock_secondary_client, mock_state_manager)
        mock_secondary_client.list_custom_resources.return_value = [
            {
                "metadata": {"name": "c1", "uid": "c1-uid"},
                "spec": {"managedClusterClientConfigs": [{"url": "https://api.c1:6443"}]},
            },
        ]
        kube_data = {
            "users": [{"name": "u", "user": {"token": "t"}}],
            "contexts": [{"name": "ctx-c1", "context": {"cluster": "kc1", "user": "u"}}],
            "clusters": [{"name": "kc1", "cluster": {"server": "https://api.c1:6443"}}],
        }
        wait_calls = 0

        def fake_wait(futures, timeout):
            nonlocal wait_calls
            wait_calls += 1
            futures = set(futures)
            if wait_calls == 3:
                return set(), futures
            return futures, set()

        with patch.object(pav, "_expected_hub_endpoint", return_value=("https://hub:6443", None)):
            with patch.object(pav, "_load_kubeconfig_data", return_value=kube_data):
                with patch.object(
                    pav,
                    "_check_klusterlet_connection",
                    side_effect=[("wrong_hub", None), ("verified", None)],
                ):
                    with patch.object(pav, "_force_klusterlet_reconnect", return_value=True):
                        with patch("modules.post_activation.wait", side_effect=fake_wait):
                            with patch(
                                "modules.post_activation.wait_for_condition",
                                side_effect=lambda description, condition_fn, **kwargs: condition_fn().done,
                            ):
                                with pytest.raises(SwitchoverError, match="could not be verified"):
                                    pav._verify_klusterlet_connections()


# ========================================================================
# 2. Kubeconfig loading / parsing (_load_kubeconfig_data)
# ========================================================================
@pytest.mark.unit
class TestKubeconfigLoading:
    """The klusterlet kubeconfig load is fail-closed and uncached (design §1, #349).

    The detailed contract lives in tests/test_kubeconfig_merge.py; these tests pin the
    PostActivationVerification seam: every call re-reads the files (no stale cache), the
    module size limit applies with no bypass, and a bad file raises instead of being skipped.
    """

    def test_every_load_rereads_the_files(self, mock_secondary_client, mock_state_manager, tmp_path):
        cfg = tmp_path / "config"
        cfg.write_text(_kubeconfig_yaml(clusters=[{"name": "c1", "cluster": {"server": "https://c1:6443"}}]))
        pav = _make_pav(mock_secondary_client, mock_state_manager)

        with patch.dict("os.environ", {"KUBECONFIG": str(cfg)}):
            first = pav._load_kubeconfig_data()
            cfg.write_text(
                _kubeconfig_yaml(
                    clusters=[
                        {"name": "c1", "cluster": {"server": "https://c1:6443"}},
                        {"name": "c2", "cluster": {"server": "https://c2:6443"}},
                    ]
                )
            )
            second = pav._load_kubeconfig_data()

        assert [c["name"] for c in first["clusters"]] == ["c1"]
        assert [c["name"] for c in second["clusters"]] == ["c1", "c2"]

    def test_a_deleted_file_is_never_served_from_a_cache(self, mock_secondary_client, mock_state_manager, tmp_path):
        cfg = tmp_path / "config"
        cfg.write_text(_kubeconfig_yaml(clusters=[{"name": "c1", "cluster": {"server": "https://c1:6443"}}]))
        pav = _make_pav(mock_secondary_client, mock_state_manager)

        with patch.dict("os.environ", {"KUBECONFIG": str(cfg)}):
            assert len(pav._load_kubeconfig_data()["clusters"]) == 1
            cfg.unlink()
            with pytest.raises(SwitchoverError, match=r"kubeconfig_load_failed \(missing\)"):
                pav._load_kubeconfig_data()

    def test_the_size_limit_applies_with_no_bypass(self, mock_secondary_client, mock_state_manager, tmp_path):
        cfg = tmp_path / "config"
        cfg.write_text("x" * 200)
        pav = _make_pav(mock_secondary_client, mock_state_manager)

        with patch.dict("os.environ", {"KUBECONFIG": str(cfg)}):
            with patch("modules.post_activation.MAX_KUBECONFIG_SIZE", 100):
                with pytest.raises(SwitchoverError, match=r"kubeconfig_load_failed \(oversized\)"):
                    pav._load_kubeconfig_data()

    def test_invalid_yaml_aborts_instead_of_being_skipped(self, mock_secondary_client, mock_state_manager, tmp_path):
        bad = tmp_path / "bad.yaml"
        bad.write_text("{{{{invalid yaml")
        good = tmp_path / "good.yaml"
        good.write_text(_kubeconfig_yaml(clusters=[{"name": "ok", "cluster": {"server": "https://ok:6443"}}]))
        pav = _make_pav(mock_secondary_client, mock_state_manager)

        with patch.dict("os.environ", {"KUBECONFIG": f"{bad}:{good}"}):
            with pytest.raises(SwitchoverError, match=r"kubeconfig_load_failed \(yaml_invalid\).*bad\.yaml"):
                pav._load_kubeconfig_data()

    def test_an_os_error_aborts_instead_of_being_skipped(self, mock_secondary_client, mock_state_manager, tmp_path):
        cfg = tmp_path / "config"
        cfg.write_text(_kubeconfig_yaml(clusters=[{"name": "c1", "cluster": {"server": "https://c1:6443"}}]))
        pav = _make_pav(mock_secondary_client, mock_state_manager)
        original_open = open

        def patched_open(path, *args, **kwargs):
            if str(path) == str(cfg):
                raise PermissionError("permission denied")
            return original_open(path, *args, **kwargs)

        with patch.dict("os.environ", {"KUBECONFIG": str(cfg)}):
            with patch("builtins.open", side_effect=patched_open):
                with pytest.raises(SwitchoverError, match=r"kubeconfig_load_failed \(unreadable\)"):
                    pav._load_kubeconfig_data()


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

        result, reason = pav._check_klusterlet_connection("ctx-c1", "c1", "https://api.newhub.com:6443", SPOKE_URL)
        assert result == "verified"

    def test_wrong_hub_when_mismatch(self, mock_secondary_client, mock_state_manager):
        """Should return 'wrong_hub' when klusterlet points to different hub."""
        pav, mock_v1 = self._setup(mock_secondary_client, mock_state_manager)
        mock_v1.read_namespaced_secret.return_value = self._make_secret("https://api.oldhub.com:6443")

        result, reason = pav._check_klusterlet_connection("ctx-c1", "c1", "https://api.newhub.com:6443", SPOKE_URL)
        assert result == "wrong_hub"

    def test_fallback_to_bootstrap_secret(self, mock_secondary_client, mock_state_manager):
        """Should try bootstrap-hub-kubeconfig when hub-kubeconfig-secret is 404."""
        pav, mock_v1 = self._setup(mock_secondary_client, mock_state_manager)

        bootstrap_secret = self._make_secret("https://api.newhub.com:6443")

        def side_effect(name, namespace, **kwargs):
            if name == "hub-kubeconfig-secret":
                raise ApiException(status=404)
            return bootstrap_secret

        mock_v1.read_namespaced_secret.side_effect = side_effect

        result, reason = pav._check_klusterlet_connection("ctx-c1", "c1", "https://api.newhub.com:6443", SPOKE_URL)
        assert result == "verified"
        assert mock_v1.read_namespaced_secret.call_count == 2
        assert all("_request_timeout" in call.kwargs for call in mock_v1.read_namespaced_secret.call_args_list)

    def test_both_hub_and_bootstrap_secret_404_remains_unreachable(self, mock_secondary_client, mock_state_manager):
        """Missing klusterlet hub secrets should remain a non-fatal unreachable result."""
        pav, mock_v1 = self._setup(mock_secondary_client, mock_state_manager)
        mock_v1.read_namespaced_secret.side_effect = ApiException(status=404)

        result, reason = pav._check_klusterlet_connection("ctx-c1", "c1", "https://hub:6443", SPOKE_URL)

        assert result == "unreachable"
        assert mock_v1.read_namespaced_secret.call_count == 2

    @pytest.mark.parametrize("status", [403, 500])
    def test_non_404_hub_secret_read_fails(self, mock_secondary_client, mock_state_manager, status):
        """Non-404 hub secret API errors should fail closed."""
        pav, mock_v1 = self._setup(mock_secondary_client, mock_state_manager)
        mock_v1.read_namespaced_secret.side_effect = ApiException(status=status)

        result, reason = pav._check_klusterlet_connection("ctx-c1", "c1", "https://hub:6443", SPOKE_URL)

        assert result == "failed"

    def test_bootstrap_secret_non_404_after_hub_404_fails(self, mock_secondary_client, mock_state_manager):
        """Hub-secret 404 plus bootstrap-secret API failure should fail closed."""
        pav, mock_v1 = self._setup(mock_secondary_client, mock_state_manager)

        def side_effect(name, namespace, **kwargs):
            if name == "hub-kubeconfig-secret":
                raise ApiException(status=404)
            raise ApiException(status=500)

        mock_v1.read_namespaced_secret.side_effect = side_effect

        result, reason = pav._check_klusterlet_connection("ctx-c1", "c1", "https://hub:6443", SPOKE_URL)

        assert result == "failed"

    def test_unreachable_on_empty_kubeconfig(self, mock_secondary_client, mock_state_manager):
        """Should return 'unreachable' when secret has no kubeconfig data."""
        pav, mock_v1 = self._setup(mock_secondary_client, mock_state_manager)
        secret = Mock()
        secret.data = {"kubeconfig": ""}
        mock_v1.read_namespaced_secret.return_value = secret

        result, reason = pav._check_klusterlet_connection("ctx-c1", "c1", "https://hub:6443", SPOKE_URL)
        assert result == "unreachable"

    def test_unreachable_on_invalid_yaml_in_secret(self, mock_secondary_client, mock_state_manager):
        """Should return 'unreachable' when kubeconfig inside secret is invalid YAML."""
        pav, mock_v1 = self._setup(mock_secondary_client, mock_state_manager)
        secret = Mock()
        secret.data = {"kubeconfig": base64.b64encode(b"{{{{bad yaml").decode()}
        mock_v1.read_namespaced_secret.return_value = secret

        result, reason = pav._check_klusterlet_connection("ctx-c1", "c1", "https://hub:6443", SPOKE_URL)
        assert result == "unreachable"

    def test_unreachable_on_invalid_base64_in_secret(self, mock_secondary_client, mock_state_manager):
        """Should return 'unreachable' when the embedded kubeconfig is not valid base64."""
        pav, mock_v1 = self._setup(mock_secondary_client, mock_state_manager)
        secret = Mock()
        secret.data = {"kubeconfig": "abc"}
        mock_v1.read_namespaced_secret.return_value = secret

        result, reason = pav._check_klusterlet_connection("ctx-c1", "c1", "https://hub:6443", SPOKE_URL)
        assert result == "unreachable"

    def test_unreachable_on_empty_clusters(self, mock_secondary_client, mock_state_manager):
        """Should return 'unreachable' when embedded kubeconfig has no clusters."""
        pav, mock_v1 = self._setup(mock_secondary_client, mock_state_manager)
        inner = yaml.dump({"clusters": []})
        secret = Mock()
        secret.data = {"kubeconfig": base64.b64encode(inner.encode()).decode()}
        mock_v1.read_namespaced_secret.return_value = secret

        result, reason = pav._check_klusterlet_connection("ctx-c1", "c1", "https://hub:6443", SPOKE_URL)
        assert result == "unreachable"

    def test_unreachable_on_clusters_mapping(self, mock_secondary_client, mock_state_manager):
        """Should return 'unreachable' when clusters is not a list."""
        pav, mock_v1 = self._setup(mock_secondary_client, mock_state_manager)
        inner = yaml.dump({"clusters": {"name": "hub", "cluster": {"server": "https://hub:6443"}}})
        secret = Mock()
        secret.data = {"kubeconfig": base64.b64encode(inner.encode()).decode()}
        mock_v1.read_namespaced_secret.return_value = secret

        result, reason = pav._check_klusterlet_connection("ctx-c1", "c1", "https://hub:6443", SPOKE_URL)
        assert result == "unreachable"

    def test_unreachable_on_non_dict_cluster_entry(self, mock_secondary_client, mock_state_manager):
        """Should return 'unreachable' when clusters[0] is not a dict."""
        pav, mock_v1 = self._setup(mock_secondary_client, mock_state_manager)
        inner = yaml.dump({"clusters": ["not-a-dict"]})
        secret = Mock()
        secret.data = {"kubeconfig": base64.b64encode(inner.encode()).decode()}
        mock_v1.read_namespaced_secret.return_value = secret

        result, reason = pav._check_klusterlet_connection("ctx-c1", "c1", "https://hub:6443", SPOKE_URL)
        assert result == "unreachable"

    def test_non_string_server_url_is_malformed_not_skipped(self, mock_secondary_client, mock_state_manager):
        """A present server that is not a string is `unverified` (`endpoint_malformed`), never a skip."""
        pav, mock_v1 = self._setup(mock_secondary_client, mock_state_manager)
        inner = yaml.dump({"clusters": [{"name": "hub", "cluster": {"server": ["https://hub:6443"]}}]})
        secret = Mock()
        secret.data = {"kubeconfig": base64.b64encode(inner.encode()).decode()}
        mock_v1.read_namespaced_secret.return_value = secret

        result, reason = pav._check_klusterlet_connection("ctx-c1", "c1", "https://hub:6443", SPOKE_URL)
        assert (result, reason) == ("unverified", "endpoint_malformed")

    def test_failed_on_config_exception(self, mock_secondary_client, mock_state_manager):
        """Client construction failures should return a failed probe result."""
        from kubernetes import config as kube_config

        pav = _make_pav(mock_secondary_client, mock_state_manager)
        pav._build_managed_cluster_clients = Mock(side_effect=kube_config.ConfigException("no context"))

        result, reason = pav._check_klusterlet_connection("bad-ctx", "c1", "https://hub:6443", SPOKE_URL)
        assert result == "failed"

    def test_failed_on_generic_client_exception(self, mock_secondary_client, mock_state_manager):
        """Generic client construction exceptions should return a failed probe result."""
        pav = _make_pav(mock_secondary_client, mock_state_manager)
        pav._build_managed_cluster_clients = Mock(side_effect=RuntimeError("connection refused"))

        result, reason = pav._check_klusterlet_connection("bad-ctx", "c1", "https://hub:6443", SPOKE_URL)
        assert result == "failed"

    def test_failed_on_non_404_api_exception(self, mock_secondary_client, mock_state_manager):
        """Non-404 ApiException on hub-kubeconfig-secret should fail closed."""
        pav, mock_v1 = self._setup(mock_secondary_client, mock_state_manager)
        mock_v1.read_namespaced_secret.side_effect = ApiException(status=403)

        result, reason = pav._check_klusterlet_connection("ctx-c1", "c1", "https://hub:6443", SPOKE_URL)
        assert result == "failed"

    def test_unreachable_on_none_kubeconfig_data(self, mock_secondary_client, mock_state_manager):
        """Should return 'unreachable' when yaml.safe_load returns None."""
        pav, mock_v1 = self._setup(mock_secondary_client, mock_state_manager)
        # Base64 encode empty string -> yaml.safe_load returns None
        secret = Mock()
        secret.data = {"kubeconfig": base64.b64encode(b"").decode()}
        mock_v1.read_namespaced_secret.return_value = secret

        result, reason = pav._check_klusterlet_connection("ctx-c1", "c1", "https://hub:6443", SPOKE_URL)
        assert result == "unreachable"

    def test_unreachable_on_non_dict_cluster_info(self, mock_secondary_client, mock_state_manager):
        """Should return 'unreachable' when cluster_info is not a dict."""
        pav, mock_v1 = self._setup(mock_secondary_client, mock_state_manager)
        inner = yaml.dump({"clusters": [{"name": "hub", "cluster": "not-a-dict"}]})
        secret = Mock()
        secret.data = {"kubeconfig": base64.b64encode(inner.encode()).decode()}
        mock_v1.read_namespaced_secret.return_value = secret

        result, reason = pav._check_klusterlet_connection("ctx-c1", "c1", "https://hub:6443", SPOKE_URL)
        assert result == "unreachable"

    def test_empty_server_url_is_malformed_not_skipped(self, mock_secondary_client, mock_state_manager):
        """A present empty server is `unverified` (`endpoint_malformed`), never a skip (amendment §2.3)."""
        pav, mock_v1 = self._setup(mock_secondary_client, mock_state_manager)
        inner = yaml.dump({"clusters": [{"name": "hub", "cluster": {"server": ""}}]})
        secret = Mock()
        secret.data = {"kubeconfig": base64.b64encode(inner.encode()).decode()}
        mock_v1.read_namespaced_secret.return_value = secret

        result, reason = pav._check_klusterlet_connection("ctx-c1", "c1", "https://hub:6443", SPOKE_URL)
        assert (result, reason) == ("unverified", "endpoint_malformed")

    def test_missing_server_key_remains_unreachable(self, mock_secondary_client, mock_state_manager):
        """An absent server is the documented skip (amendment §2.3), distinct from a malformed one."""
        pav, mock_v1 = self._setup(mock_secondary_client, mock_state_manager)
        inner = yaml.dump({"clusters": [{"name": "hub", "cluster": {"certificate-authority-data": "x"}}]})
        secret = Mock()
        secret.data = {"kubeconfig": base64.b64encode(inner.encode()).decode()}
        mock_v1.read_namespaced_secret.return_value = secret

        result, reason = pav._check_klusterlet_connection("ctx-c1", "c1", "https://hub:6443", SPOKE_URL)
        assert (result, reason) == ("unreachable", None)

    @pytest.mark.parametrize(
        ("observed", "expected"),
        [
            ("https://api.newhub.com:443", "https://api.newhub.com:6443"),
            ("https://api.newhub.com", "https://api.newhub.com:6443"),
            ("http://api.newhub.com:6443", "https://api.newhub.com:6443"),
            ("https://api.newhub.com:6443/proxy", "https://api.newhub.com:6443"),
            ("https://[2001:db8::2]:6443", "https://[2001:db8::1]:6443"),
        ],
    )
    def test_a_same_host_or_legacy_equal_mismatch_is_unverified_not_wrong_hub(
        self, mock_secondary_client, mock_state_manager, observed, expected
    ):
        """Decision 2: a pair the old hostname compare called equal is operator review, never remediation."""
        pav, mock_v1 = self._setup(mock_secondary_client, mock_state_manager)
        mock_v1.read_namespaced_secret.return_value = self._make_secret(observed)

        result, reason = pav._check_klusterlet_connection("ctx-c1", "c1", expected, SPOKE_URL)
        assert (result, reason) == ("unverified", "endpoint_mismatch_unverified")

    def test_default_port_and_case_differences_verify(self, mock_secondary_client, mock_state_manager):
        pav, mock_v1 = self._setup(mock_secondary_client, mock_state_manager)
        mock_v1.read_namespaced_secret.return_value = self._make_secret("https://API.NewHub.com/")

        result, reason = pav._check_klusterlet_connection("ctx-c1", "c1", "https://api.newhub.com:443", SPOKE_URL)
        assert (result, reason) == ("verified", None)

    def test_a_client_bound_elsewhere_is_unverified_with_zero_reads(self, mock_secondary_client, mock_state_manager):
        """Amendment §2.7: `client_endpoint_mismatch` happens after construction and before any read."""
        pav = _make_pav(mock_secondary_client, mock_state_manager)
        pav._build_managed_cluster_clients = Mock(side_effect=SnapshotError("client_endpoint_mismatch"))

        result, reason = pav._check_klusterlet_connection("ctx-c1", "c1", "https://hub:6443", SPOKE_URL)
        assert (result, reason) == ("unverified", "client_endpoint_mismatch")
        pav._build_managed_cluster_clients.assert_called_once_with("ctx-c1", SPOKE_URL)


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

    def test_deployment_not_found_blocks(self, mock_secondary_client, mock_state_manager):
        """Missing observatorium-api should block Observability scale-up."""
        pav = _make_pav(mock_secondary_client, mock_state_manager, obs=True)

        mock_secondary_client.get_deployment.return_value = None
        mock_secondary_client.get_statefulset.return_value = {"spec": {"replicas": 0}}
        mock_secondary_client.wait_for_pods_ready.return_value = True

        with pytest.raises(SwitchoverError) as exc_info:
            pav._scale_up_observability_components()

        assert "observatorium-api deployment not found" in str(exc_info.value)
        mock_secondary_client.scale_deployment.assert_not_called()
        mock_secondary_client.scale_statefulset.assert_not_called()

    def test_statefulset_not_found_blocks(self, mock_secondary_client, mock_state_manager):
        """Missing thanos-compact should block Observability scale-up."""
        pav = _make_pav(mock_secondary_client, mock_state_manager, obs=True)

        mock_secondary_client.get_deployment.return_value = {"spec": {"replicas": 0}}
        mock_secondary_client.get_statefulset.return_value = None
        mock_secondary_client.wait_for_pods_ready.return_value = True

        with pytest.raises(SwitchoverError) as exc_info:
            pav._scale_up_observability_components()

        mock_secondary_client.scale_deployment.assert_called_once()
        assert "thanos-compact StatefulSet not found" in str(exc_info.value)
        mock_secondary_client.scale_statefulset.assert_not_called()

    def test_deployment_exception_blocks(self, mock_secondary_client, mock_state_manager):
        """ApiException on deployment should block Observability scale-up."""
        pav = _make_pav(mock_secondary_client, mock_state_manager, obs=True)

        mock_secondary_client.get_deployment.side_effect = ApiException(status=500)
        mock_secondary_client.get_statefulset.return_value = {"spec": {"replicas": 0}}
        mock_secondary_client.wait_for_pods_ready.return_value = True

        with pytest.raises(SwitchoverError) as exc_info:
            pav._scale_up_observability_components()

        assert "Failed to check/scale observatorium-api" in str(exc_info.value)
        mock_secondary_client.scale_statefulset.assert_not_called()

    def test_statefulset_exception_blocks(self, mock_secondary_client, mock_state_manager):
        """ApiException on statefulset should block Observability scale-up."""
        pav = _make_pav(mock_secondary_client, mock_state_manager, obs=True)

        mock_secondary_client.get_deployment.return_value = {"spec": {"replicas": 0}}
        mock_secondary_client.get_statefulset.side_effect = ApiException(status=500)
        mock_secondary_client.wait_for_pods_ready.return_value = True

        with pytest.raises(SwitchoverError) as exc_info:
            pav._scale_up_observability_components()

        mock_secondary_client.scale_deployment.assert_called_once()
        assert "Failed to check/scale thanos-compact" in str(exc_info.value)
        mock_secondary_client.wait_for_pods_ready.assert_not_called()

    def test_pods_not_ready_blocks(self, mock_secondary_client, mock_state_manager):
        """Pods failing to become ready should block Observability scale-up."""
        pav = _make_pav(mock_secondary_client, mock_state_manager, obs=True)

        mock_secondary_client.get_deployment.return_value = {"spec": {"replicas": 0}}
        mock_secondary_client.get_statefulset.return_value = {"spec": {"replicas": 0}}
        mock_secondary_client.wait_for_pods_ready.return_value = False

        with pytest.raises(SwitchoverError) as exc_info:
            pav._scale_up_observability_components()

        assert "observatorium-api pods did not become ready in time" in str(exc_info.value)


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

        with pytest.raises(SwitchoverError) as exc_info:
            pav._verify_observability_pods()

        assert "OOMKilled" in str(exc_info.value)

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

        with pytest.raises(SwitchoverError) as exc_info:
            pav._verify_observability_pods()

        assert "Failed" in str(exc_info.value)

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

        with pytest.raises(SwitchoverError) as exc_info:
            pav._verify_observability_pods()

        assert "Unknown" in str(exc_info.value)

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

        with pytest.raises(SwitchoverError) as exc_info:
            pav._verify_observability_pods()

        assert "exit=1" in str(exc_info.value)

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

        with pytest.raises(SwitchoverError) as exc_info:
            pav._verify_observability_pods()

        assert "ImagePullBackOff" in str(exc_info.value)

    def test_low_readiness_blocks(self, mock_secondary_client, mock_state_manager):
        """Below POD_READINESS_TOLERANCE should block Observability verification."""
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

        with pytest.raises(SwitchoverError) as exc_info:
            pav._verify_observability_pods()

        assert "Only 1/5 Observability pods ready" in str(exc_info.value)


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

        result = pav._force_klusterlet_reconnect("c1", "ctx-c1", "https://api.c1:6443", HERMETIC_HUB_SERVER)
        assert result is False

    def test_generic_exception_returns_false(self, mock_secondary_client, mock_state_manager):
        """Generic exception during reconnect should return False."""
        pav = _make_pav(mock_secondary_client, mock_state_manager)

        mock_secondary_client.get_secret.side_effect = RuntimeError("connection reset")

        result = pav._force_klusterlet_reconnect("c1", "ctx-c1", "https://api.c1:6443", HERMETIC_HUB_SERVER)
        assert result is False

    def test_missing_import_yaml_data_returns_false(self, mock_secondary_client, mock_state_manager):
        """Import secret without import.yaml data should return False."""
        pav = _make_pav(mock_secondary_client, mock_state_manager)

        mock_secondary_client.get_secret.return_value = {"data": {"import.yaml": ""}}

        result = pav._force_klusterlet_reconnect("c1", "ctx-c1", "https://api.c1:6443", HERMETIC_HUB_SERVER)
        assert result is False


# -- R4-04 Task 9: strict post-activation completion evidence ----------------------------------

_VECTOR_CASES = {
    case["id"]: case
    for case in json.loads(
        (Path(__file__).parent / "fixtures" / "r4_04_migration_evidence_vectors.json").read_text(encoding="utf-8")
    )["cases"]
}
COMPLETED_JOURNAL = _VECTOR_CASES["post-activation-journal-completed"]["input"]["journal"]
PRE_COMPLETION_JOURNAL = _VECTOR_CASES["post-activation-journal-pre-completion-blocks"]["input"]["journal"]
BOTH_WAIVER_JOURNAL = _VECTOR_CASES["post-activation-waiver-journaled-both-reused"]["input"]["journal"]
EVIDENCE_NAMES = ["cluster-a", "cluster-b"]
POST_WAIVER_REQUEST = {
    "scope": "post_activation",
    "actor": "operator@example.com",
    "reason": "the expected list is stale",
    "request_id": None,
}


def _ready_mc(name, available="True"):
    return {
        "metadata": {"name": name},
        "status": {
            "conditions": [
                {"type": "ManagedClusterConditionAvailable", "status": available},
                {"type": "ManagedClusterJoined", "status": "True"},
            ]
        },
    }


def _strict_secondary(*names, outcome=None):
    secondary = Mock(name="secondary")
    items = [_ready_mc(LOCAL_CLUSTER_NAME)] + [_ready_mc(name) for name in names]
    secondary.list_managed_clusters_strict.return_value = outcome or StrictReadOutcome.from_items(items)
    return secondary


def _evidence_state(tmp_path, journal=COMPLETED_JOURNAL, steps=()):
    state = StateManager(str(tmp_path / "state.json"))
    if journal is not None:
        state.state["config"]["migration_backups"] = copy.deepcopy(journal)
    for step in steps:
        state.mark_step_completed(step)
    state.flush_state()
    return state


def _stored_journal(tmp_path):
    return json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))["config"].get("migration_backups")


def _evidence_verifier(state, secondary, **overrides):
    kwargs = {
        "has_observability": False,
        "min_managed_clusters": 2,
        "expected_managed_cluster_names": EVIDENCE_NAMES,
        "enforce_expected_managed_cluster_names": True,
    }
    kwargs.update(overrides)
    return PostActivationVerification(secondary_client=secondary, state_manager=state, **kwargs)


def _poll_once(_description, condition, **_kwargs):
    """wait_for_condition that polls exactly once: a pending poll is a timeout."""
    return condition().done


@pytest.mark.unit
class TestPostActivationMigrationEvidence:
    """Plan Task 9: post-activation evidence is strict, reconciled on resume, and completed_at is last."""

    @pytest.mark.parametrize(
        ("journal", "code"),
        [(None, "journal_required"), (PRE_COMPLETION_JOURNAL, "restore_not_completed")],
    )
    def test_an_executing_run_without_a_completed_journal_refuses_before_any_work(self, tmp_path, journal, code):
        state = _evidence_state(tmp_path, journal)
        secondary = _strict_secondary(*EVIDENCE_NAMES)
        verifier = _evidence_verifier(state, secondary)

        assert verifier.verify() is False

        secondary.list_managed_clusters_strict.assert_not_called()
        assert code in state.state["errors"][-1]["error"]
        assert _stored_journal(tmp_path) == journal

    @pytest.mark.parametrize("dry_run", [False, True], ids=["execute", "dry-run"])
    def test_an_invalid_journal_refuses_before_any_work(self, tmp_path, dry_run):
        state = _evidence_state(tmp_path, dict(COMPLETED_JOURNAL, waiver=False))
        secondary = _strict_secondary(*EVIDENCE_NAMES)

        assert _evidence_verifier(state, secondary, dry_run=dry_run).verify() is False

        secondary.list_managed_clusters_strict.assert_not_called()
        assert "invalid" in state.state["errors"][-1]["error"]

    def test_a_dry_run_without_a_journal_predicts_and_writes_nothing(self, tmp_path):
        state = _evidence_state(tmp_path, None)

        assert _evidence_verifier(state, _strict_secondary(*EVIDENCE_NAMES), dry_run=True).verify() is True

        assert _stored_journal(tmp_path) is None

    def test_a_dry_run_over_a_journal_writes_no_evidence(self, tmp_path):
        state = _evidence_state(tmp_path)

        assert _evidence_verifier(state, _strict_secondary(*EVIDENCE_NAMES), dry_run=True).verify() is True

        assert _stored_journal(tmp_path) == COMPLETED_JOURNAL

    def test_names_evidence_then_completion_strictly_last(self, tmp_path):
        state = _evidence_state(tmp_path)
        verifier = _evidence_verifier(state, _strict_secondary(*EVIDENCE_NAMES), has_observability=True)
        seen = {}

        def observability():
            seen["post_activation"] = copy.deepcopy(_stored_journal(tmp_path)["post_activation"])

        with patch.object(verifier, "_verify_klusterlet_connections"), patch.object(
            verifier, "_verify_observability_full", side_effect=observability
        ):
            assert verifier.verify() is True

        assert seen["post_activation"]["names_verified_at"] is not None
        assert seen["post_activation"]["completed_at"] is None
        post = _stored_journal(tmp_path)["post_activation"]
        assert post["names_verified_at"] == seen["post_activation"]["names_verified_at"]
        assert post["completed_at"] is not None

    @pytest.mark.parametrize("failing", ["_verify_observability_full", "_verify_disable_auto_import_cleared"])
    def test_a_later_failure_leaves_completion_absent(self, tmp_path, failing):
        state = _evidence_state(tmp_path)
        verifier = _evidence_verifier(state, _strict_secondary(*EVIDENCE_NAMES), has_observability=True)

        with patch.object(verifier, "_verify_klusterlet_connections"), patch.object(
            verifier, "_verify_observability_full"
        ), patch.object(verifier, failing, side_effect=SwitchoverError("later work failed")):
            assert verifier.verify() is False

        post = _stored_journal(tmp_path)["post_activation"]
        assert post["names_verified_at"] is not None
        assert post["completed_at"] is None

    def test_a_klusterlet_failure_records_no_evidence(self, tmp_path):
        state = _evidence_state(tmp_path)
        verifier = _evidence_verifier(state, _strict_secondary(*EVIDENCE_NAMES))

        with patch.object(verifier, "_verify_klusterlet_connections", side_effect=SwitchoverError("wrong hub")):
            assert verifier.verify() is False

        assert _stored_journal(tmp_path) == COMPLETED_JOURNAL

    @pytest.mark.parametrize(
        "outcome",
        [
            StrictReadOutcome.error("Forbidden"),
            StrictReadOutcome.crd_absent("ManagedCluster CRD not served"),
        ],
        ids=["error", "crd-absent"],
    )
    def test_a_failed_strict_inventory_is_failure_never_zero_clusters(self, tmp_path, outcome):
        state = _evidence_state(tmp_path)
        secondary = _strict_secondary(outcome=outcome)
        verifier = _evidence_verifier(state, secondary, min_managed_clusters=0, expected_managed_cluster_names=[])

        with patch.object(verifier, "_verify_klusterlet_connections") as klusterlet:
            assert verifier.verify() is False

        klusterlet.assert_not_called()
        assert "ManagedCluster inventory read failed" in state.state["errors"][-1]["error"]
        assert _stored_journal(tmp_path) == COMPLETED_JOURNAL
        secondary.list_custom_resources.assert_not_called()

    def test_a_missing_expected_name_records_no_evidence(self, tmp_path):
        state = _evidence_state(tmp_path)
        verifier = _evidence_verifier(state, _strict_secondary("cluster-a", "cluster-c"))

        with patch("modules.post_activation.wait_for_condition", side_effect=_poll_once), patch.object(
            verifier, "_verify_klusterlet_connections"
        ):
            assert verifier.verify() is False

        assert "cluster-b" in state.state["errors"][-1]["error"]
        assert _stored_journal(tmp_path) == COMPLETED_JOURNAL

    def test_a_post_activation_waiver_substitutes_only_for_the_name_check(self, tmp_path):
        state = _evidence_state(tmp_path)
        verifier = _evidence_verifier(
            state,
            _strict_secondary("cluster-a", "cluster-c"),
            managed_cluster_expectation_waiver=POST_WAIVER_REQUEST,
        )

        with patch.object(verifier, "_verify_klusterlet_connections"):
            assert verifier.verify() is True

        journal = _stored_journal(tmp_path)
        assert {key: journal["waiver"][key] for key in POST_WAIVER_REQUEST} == POST_WAIVER_REQUEST
        assert journal["waiver"]["outcome"] == "waived"
        assert journal["waiver"]["flag"] == "--skip-managed-cluster-expectations"
        assert journal["post_activation"]["names_verified_at"] is None
        assert journal["post_activation"]["completed_at"] is not None

    def test_a_waiver_never_lifts_the_count_floor(self, tmp_path):
        state = _evidence_state(tmp_path)
        verifier = _evidence_verifier(
            state, _strict_secondary("cluster-a"), managed_cluster_expectation_waiver=POST_WAIVER_REQUEST
        )

        with patch("modules.post_activation.wait_for_condition", side_effect=_poll_once), patch.object(
            verifier, "_verify_klusterlet_connections"
        ):
            assert verifier.verify() is False

        assert _stored_journal(tmp_path) == COMPLETED_JOURNAL

    def test_a_request_differing_from_the_journaled_waiver_refuses_before_any_work(self, tmp_path):
        state = _evidence_state(tmp_path, BOTH_WAIVER_JOURNAL)
        secondary = _strict_secondary(*EVIDENCE_NAMES)
        verifier = _evidence_verifier(state, secondary, managed_cluster_expectation_waiver=POST_WAIVER_REQUEST)

        assert verifier.verify() is False

        secondary.list_managed_clusters_strict.assert_not_called()
        assert "waiver_request_mismatch" in state.state["errors"][-1]["error"]
        assert _stored_journal(tmp_path) == BOTH_WAIVER_JOURNAL

    def test_resume_reconciles_evidence_despite_completed_step_markers(self, tmp_path):
        named = _VECTOR_CASES["names-evidence-recorded"]["expect"]["result"]["candidate"]
        steps = ("verify_clusters_connected", "verify_klusterlet_connections", "verify_auto_import_cleanup")
        state = _evidence_state(tmp_path, named, steps)
        secondary = _strict_secondary(*EVIDENCE_NAMES)

        assert _evidence_verifier(state, secondary).verify() is True

        secondary.list_managed_clusters_strict.assert_called()
        post = _stored_journal(tmp_path)["post_activation"]
        assert post["names_verified_at"] == named["post_activation"]["names_verified_at"]
        assert post["completed_at"] is not None

    def test_a_failed_revalidation_keeps_the_recorded_evidence(self, tmp_path):
        recorded = _VECTOR_CASES["completion-kept-on-resume"]["input"]["journal"]
        steps = ("verify_clusters_connected", "verify_klusterlet_connections", "verify_auto_import_cleanup")
        state = _evidence_state(tmp_path, recorded, steps)

        with patch("modules.post_activation.wait_for_condition", side_effect=_poll_once):
            assert _evidence_verifier(state, _strict_secondary("cluster-a", "cluster-c")).verify() is False

        error = state.state["errors"][-1]["error"]
        assert error.startswith("Missing expected ManagedCluster(s) after activation (expected_names_missing)")
        assert _stored_journal(tmp_path) == recorded

    def test_a_journal_write_failure_fails_the_phase(self, tmp_path):
        state = _evidence_state(tmp_path)
        verifier = _evidence_verifier(state, _strict_secondary(*EVIDENCE_NAMES))

        with patch.object(verifier, "_verify_klusterlet_connections"), patch(
            "lib.run_record.RunRecord.record_migration_backups", side_effect=OSError("disk full")
        ):
            assert verifier.verify() is False

        assert "could not be recorded durably" in state.state["errors"][-1]["error"]
        assert _stored_journal(tmp_path) == COMPLETED_JOURNAL


# The Codex T12 review (B1) recovery guidance, verbatim.
MISSING_EVIDENCE_GUIDANCE = (
    "The migration journal is unchanged. For missing post-activation completion, set `current_phase` to "
    "`post_activation_verification`. For missing Restore completion, set it to `activation` and remove only the "
    "`completed_steps` entry named `wait_restore_completion`, if present. Preserve the migration journal, mutation "
    "markers, and Argo CD re-pause marker. Re-run with the same arguments; the tool must establish the missing "
    "evidence from fresh reads. Do not synthesize completion timestamps or replace frozen Backup identities. "
    "Missing accepted-mutation evidence requires operator reconciliation. `--reset-state` deliberately discards "
    "the transaction."
)

_POST_ACTIVATION_STEPS = (
    "verify_clusters_connected",
    "verify_klusterlet_connections",
    "verify_auto_import_cleanup",
    "scale_up_observability_components",
    "restart_observatorium_api",
    "verify_observability_pods",
    "verify_metrics_collection",
)


def _poll_until_done(_description, condition, **_kwargs):
    """A bounded wait that polls until done, at most three times."""
    return any(condition().done for _ in range(3))


@pytest.mark.unit
class TestPostActivationEvidenceReviewFindings:
    """Task 9 review findings B1, B2 and W2."""

    def test_b1_a_dry_run_records_no_step_marker_so_execute_still_does_the_work(self, tmp_path):
        state = _evidence_state(tmp_path)
        dry = _evidence_verifier(state, _strict_secondary(*EVIDENCE_NAMES), dry_run=True)
        assert dry.verify() is True
        assert not [step for step in state.state["completed_steps"] if step["name"] in _POST_ACTIVATION_STEPS]

        live = _evidence_verifier(state, _strict_secondary(*EVIDENCE_NAMES))
        with patch.object(live, "_verify_klusterlet_connections") as klusterlet, patch.object(
            live, "_verify_disable_auto_import_cleared"
        ) as cleanup:
            assert live.verify() is True
        klusterlet.assert_called_once_with()
        cleanup.assert_called_once_with()

    @pytest.mark.parametrize("dry_run", [False, True], ids=["execute", "dry-run"])
    def test_b2_a_resume_past_post_activation_needs_its_completion_evidence(self, tmp_path, dry_run):
        from lib.utils import Phase
        from lib.workflow import run_phase_flow

        state = _evidence_state(tmp_path)  # restore completed, post-activation evidence null
        state.set_phase(Phase.FINALIZATION)
        finalization = Mock(return_value=True)
        failed = []

        def fail_phase(_state, message, _logger):
            failed.append(message)
            return False

        result = run_phase_flow(
            SimpleNamespace(dry_run=dry_run),
            state,
            None,
            Mock(),
            logging.getLogger("test"),
            ((finalization, (Phase.POST_ACTIVATION, Phase.FINALIZATION), Phase.FINALIZATION),),
            "switchover",
            fail_phase,
            Mock(),
            Mock(),
        )

        if dry_run:
            assert result is True and finalization.called
        else:
            assert result is False
            finalization.assert_not_called()
            assert "post_activation_not_completed" in failed[-1]

    def test_b2_a_dry_run_resume_past_post_activation_refuses_an_invalid_journal(self, tmp_path, caplog):
        """Only missing evidence is a dry-run prediction; an invalid journal refuses in every mode.

        The preview refusal is logged without the failure hook, which would record the error.
        """
        from lib.utils import Phase
        from lib.workflow import run_phase_flow

        journal = _VECTOR_CASES["journal-run-id-not-uuid"]["input"]["candidate"]
        state = _evidence_state(tmp_path, journal)
        state.set_phase(Phase.FINALIZATION)
        finalization = Mock(return_value=True)
        failed = []

        def fail_phase(_state, message, _logger):
            failed.append(message)
            return False

        result = run_phase_flow(
            SimpleNamespace(dry_run=True),
            state,
            None,
            Mock(),
            logging.getLogger("test"),
            ((finalization, (Phase.POST_ACTIVATION, Phase.FINALIZATION), Phase.FINALIZATION),),
            "switchover",
            fail_phase,
            Mock(),
            Mock(),
        )

        assert result is False
        finalization.assert_not_called()
        assert failed == []
        assert "The recorded migration journal is invalid" in caplog.text
        assert _stored_journal(tmp_path) == journal

    def test_b2_a_resume_past_recorded_post_activation_evidence_continues(self, tmp_path):
        from lib.utils import Phase
        from lib.workflow import run_phase_flow

        state = _evidence_state(tmp_path, _VECTOR_CASES["completion-kept-on-resume"]["input"]["journal"])
        state.set_phase(Phase.FINALIZATION)
        finalization = Mock(return_value=True)
        assert run_phase_flow(
            SimpleNamespace(dry_run=False),
            state,
            None,
            Mock(),
            logging.getLogger("test"),
            ((finalization, (Phase.POST_ACTIVATION, Phase.FINALIZATION), Phase.FINALIZATION),),
            "switchover",
            Mock(return_value=False),
            Mock(),
            Mock(),
        )
        finalization.assert_called_once()

    @staticmethod
    def _finalization_resume_refusal(state):
        from lib.utils import Phase
        from lib.workflow import run_phase_flow

        state.set_phase(Phase.FINALIZATION)
        finalization = Mock(return_value=True)
        failed = []

        def fail_phase(_state, message, _logger):
            failed.append(message)
            return False

        result = run_phase_flow(
            SimpleNamespace(dry_run=False),
            state,
            None,
            Mock(),
            logging.getLogger("test"),
            ((finalization, (Phase.POST_ACTIVATION, Phase.FINALIZATION), Phase.FINALIZATION),),
            "switchover",
            fail_phase,
            Mock(),
            Mock(),
        )
        return result, finalization, failed

    @pytest.mark.parametrize(
        ("journal", "code", "phase"),
        [
            (COMPLETED_JOURNAL, "post_activation_not_completed", "post_activation_verification"),
            (PRE_COMPLETION_JOURNAL, "restore_not_completed", "activation"),
        ],
        ids=["post-activation", "restore"],
    )
    def test_b2_the_refusal_names_the_rerun_that_keeps_the_journal(self, tmp_path, journal, code, phase):
        state = _evidence_state(tmp_path, journal)

        result, finalization, failed = self._finalization_resume_refusal(state)

        assert result is False
        finalization.assert_not_called()
        message = failed[-1]
        assert message.startswith(f"Migration evidence refused ({code}:")
        assert MISSING_EVIDENCE_GUIDANCE in message
        assert f"set it to `{phase}`" in message or f"set `current_phase` to `{phase}`" in message
        assert f"State file: {state.state_file}." in message
        assert _stored_journal(tmp_path) == journal

    def test_b2_an_invalid_journal_refusal_names_repair_or_an_explicit_reset(self, tmp_path):
        journal = _VECTOR_CASES["journal-run-id-not-uuid"]["input"]["candidate"]
        state = _evidence_state(tmp_path, journal)

        result, _finalization, failed = self._finalization_resume_refusal(state)

        assert result is False
        assert "current_phase" not in failed[-1]
        assert f"repair it in the state file {state.state_file}" in failed[-1]
        assert "Do not synthesize completion timestamps or replace frozen Backup identities." in failed[-1]
        assert "`--reset-state` deliberately discards the transaction." in failed[-1]

    def test_b2_following_the_refusal_guidance_records_the_evidence_and_resumes(self, tmp_path):
        """The named rerun re-proves post-activation over the kept journal, its step markers notwithstanding."""
        from lib.utils import Phase

        state = _evidence_state(tmp_path, steps=_POST_ACTIVATION_STEPS[:3])
        assert self._finalization_resume_refusal(state)[0] is False

        state_file = tmp_path / "state.json"
        raw = json.loads(state_file.read_text(encoding="utf-8"))
        raw["current_phase"] = Phase.POST_ACTIVATION.value
        state_file.write_text(json.dumps(raw), encoding="utf-8")
        edited = StateManager(str(state_file))
        with patch("modules.post_activation.wait_for_condition", side_effect=_poll_until_done):
            assert _evidence_verifier(edited, _strict_secondary(*EVIDENCE_NAMES)).verify() is True

        assert _stored_journal(tmp_path)["post_activation"]["completed_at"] is not None
        result, finalization, failed = self._finalization_resume_refusal(edited)
        assert result is True and not failed
        finalization.assert_called_once()

    def test_w2_resume_reconciles_readiness_with_the_bounded_wait(self, tmp_path):
        steps = ("verify_clusters_connected", "verify_klusterlet_connections", "verify_auto_import_cleanup")
        state = _evidence_state(tmp_path, steps=steps)
        secondary = Mock(name="secondary")
        not_ready = [_ready_mc(LOCAL_CLUSTER_NAME), _ready_mc("cluster-a", "False"), _ready_mc("cluster-b")]
        ready = [_ready_mc(LOCAL_CLUSTER_NAME), _ready_mc("cluster-a"), _ready_mc("cluster-b")]
        secondary.list_managed_clusters_strict.side_effect = [
            StrictReadOutcome.from_items(not_ready),
            StrictReadOutcome.from_items(ready),
        ]

        with patch("modules.post_activation.wait_for_condition", side_effect=_poll_until_done):
            assert _evidence_verifier(state, secondary).verify() is True

        assert _stored_journal(tmp_path)["post_activation"]["completed_at"] is not None
