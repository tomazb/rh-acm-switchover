"""Unit tests for modules/primary_prep.py.

Tests cover PrimaryPreparation class for preparing the primary hub.
"""

import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

# Add parent to path to import modules directly
sys.path.insert(0, str(Path(__file__).parent.parent))

import modules.primary_prep as primary_prep_module
from lib import argocd as argocd_lib
from lib.constants import OBSERVABILITY_NAMESPACE, THANOS_SCALE_DOWN_WAIT

PrimaryPreparation = primary_prep_module.PrimaryPreparation


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
def mock_primary_client():
    """Create a mock KubeClient for primary hub."""
    client = Mock()
    client.list_managed_clusters = Mock(return_value=[])
    client.patch_managed_cluster = Mock()
    return client


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
def primary_prep_with_obs(mock_primary_client, mock_state_manager):
    """Create PrimaryPreparation instance with observability."""
    return PrimaryPreparation(
        primary_client=mock_primary_client,
        state_manager=mock_state_manager,
        acm_version="2.12.0",
        has_observability=True,
    )


@pytest.fixture
def primary_prep_no_obs(mock_primary_client, mock_state_manager):
    """Create PrimaryPreparation instance without observability."""
    return PrimaryPreparation(
        primary_client=mock_primary_client,
        state_manager=mock_state_manager,
        acm_version="2.12.0",
        has_observability=False,
    )


@pytest.mark.unit
class TestPrimaryPreparation:
    """Tests for PrimaryPreparation class."""

    def test_initialization(self, mock_primary_client, mock_state_manager):
        """Test PrimaryPreparation initialization."""
        prep = PrimaryPreparation(
            primary_client=mock_primary_client,
            state_manager=mock_state_manager,
            acm_version="2.12.0",
            has_observability=True,
        )

        assert prep.primary == mock_primary_client
        assert prep.state == mock_state_manager
        assert prep.acm_version == "2.12.0"
        assert prep.has_observability is True

    @patch("time.sleep")
    def test_prepare_success_with_observability(
        self, mock_sleep, primary_prep_with_obs, mock_primary_client, mock_state_manager
    ):
        """Test successful preparation with observability."""

        # Mock all list_custom_resources calls
        def list_side_effect(*args, **kwargs):
            plural = kwargs.get("plural", "")
            if plural == "backupschedules":
                return [{"metadata": {"name": "schedule-rhacm"}, "spec": {"paused": False}}]
            elif plural == "managedclusters":
                return [
                    {"metadata": {"name": "cluster1", "labels": {}}},
                    {"metadata": {"name": "cluster2", "labels": {}}},
                ]
            return []

        mock_primary_client.list_custom_resources.side_effect = list_side_effect
        mock_primary_client.list_managed_clusters.return_value = [
            {"metadata": {"name": "cluster1"}},
            {"metadata": {"name": "cluster2"}},
        ]
        mock_primary_client.patch_custom_resource.return_value = True
        mock_primary_client.scale_statefulset.return_value = {"status": "scaled"}
        mock_primary_client.get_pods.return_value = []

        result = primary_prep_with_obs.prepare()

        assert result is True
        assert mock_state_manager.mark_step_completed.call_count >= 3

    def test_prepare_success_without_observability(self, primary_prep_no_obs, mock_primary_client, mock_state_manager):
        """Test successful preparation without observability."""
        mock_primary_client.list_custom_resources.return_value = [
            {"metadata": {"name": "schedule-rhacm"}, "spec": {"paused": False}}
        ]
        mock_primary_client.list_managed_clusters.return_value = [{"metadata": {"name": "cluster1"}}]
        mock_primary_client.patch_custom_resource.return_value = True

        result = primary_prep_no_obs.prepare()

        assert result is True
        # Should not scale Thanos since no observability
        mock_primary_client.scale_statefulset.assert_not_called()

    def test_prepare_steps_already_completed(self, primary_prep_with_obs, mock_state_manager):
        """Test skipping already completed steps."""
        mock_state_manager.is_step_completed.return_value = True

        result = primary_prep_with_obs.prepare()

        assert result is True

    def test_pause_argocd_acm_apps_records_paused(self, mock_primary_client, mock_state_manager):
        """Pause Argo CD auto-sync should record paused apps in state."""
        prep = PrimaryPreparation(
            primary_client=mock_primary_client,
            state_manager=mock_state_manager,
            acm_version="2.12.0",
            has_observability=False,
            dry_run=False,
            argocd_manage=True,
        )
        mock_state_manager.get_config.side_effect = lambda key, default=None: {
            "argocd_run_id": None,
            "argocd_paused_apps": [],
        }.get(key, default)

        discovery = argocd_lib.ArgocdDiscoveryResult(
            has_applications_crd=True,
            has_argocds_crd=False,
            install_type="vanilla",
        )
        app = {
            "metadata": {"namespace": "argocd", "name": "app-1"},
            "spec": {"syncPolicy": {"automated": {}}},
            "status": {
                "resources": [
                    {
                        "kind": "BackupSchedule",
                        "namespace": "open-cluster-management-backup",
                    }
                ]
            },
        }
        impacts = [argocd_lib.AppImpact(namespace="argocd", name="app-1", resource_count=1, app=app)]

        with (
            patch(
                "modules.primary_prep.argocd_lib.detect_argocd_installation",
                return_value=discovery,
            ),
            patch(
                "modules.primary_prep.argocd_lib.list_argocd_applications",
                return_value=[app],
            ),
            patch(
                "modules.primary_prep.argocd_lib.find_acm_touching_apps",
                return_value=impacts,
            ),
            patch("modules.primary_prep.argocd_lib.pause_autosync") as pause_autosync,
        ):
            pause_autosync.return_value = argocd_lib.PauseResult(
                namespace="argocd",
                name="app-1",
                original_sync_policy={"automated": {}},
                patched=True,
            )

            prep._pause_argocd_acm_apps()

        pause_autosync.assert_called_once()

        paused_call = next(
            call for call in mock_state_manager.set_config.call_args_list if call.args[0] == "argocd_paused_apps"
        )
        paused_apps = paused_call.args[1]
        assert len(paused_apps) == 1
        assert paused_apps[0]["namespace"] == "argocd"
        assert paused_apps[0]["name"] == "app-1"

    def test_pause_argocd_acm_apps_dry_run_records_apps(self, mock_primary_client, mock_state_manager):
        """Dry-run should still report and record ACM-touching apps as would-paused."""
        prep = PrimaryPreparation(
            primary_client=mock_primary_client,
            state_manager=mock_state_manager,
            acm_version="2.12.0",
            has_observability=False,
            dry_run=True,
            argocd_manage=True,
        )
        mock_state_manager.get_config.side_effect = lambda key, default=None: {
            "argocd_run_id": None,
            "argocd_paused_apps": [],
        }.get(key, default)

        discovery = argocd_lib.ArgocdDiscoveryResult(
            has_applications_crd=True,
            has_argocds_crd=False,
            install_type="vanilla",
        )
        app = {
            "metadata": {"namespace": "argocd", "name": "app-2"},
            "spec": {"syncPolicy": {"automated": {"prune": True}}},
            "status": {"resources": [{"kind": "Restore", "namespace": "open-cluster-management-backup"}]},
        }
        impacts = [argocd_lib.AppImpact(namespace="argocd", name="app-2", resource_count=1, app=app)]

        with (
            patch(
                "modules.primary_prep.argocd_lib.detect_argocd_installation",
                return_value=discovery,
            ),
            patch(
                "modules.primary_prep.argocd_lib.list_argocd_applications",
                return_value=[app],
            ),
            patch(
                "modules.primary_prep.argocd_lib.find_acm_touching_apps",
                return_value=impacts,
            ),
            patch("modules.primary_prep.argocd_lib.pause_autosync") as pause_autosync,
        ):
            pause_autosync.return_value = argocd_lib.PauseResult(
                namespace="argocd",
                name="app-2",
                original_sync_policy={"automated": {"prune": True}},
                patched=True,
            )

            prep._pause_argocd_acm_apps()

        paused_call = next(
            call for call in mock_state_manager.set_config.call_args_list if call.args[0] == "argocd_paused_apps"
        )
        paused_apps = paused_call.args[1]
        assert paused_apps[0]["dry_run"] is True

        dry_run_call = next(
            call for call in mock_state_manager.set_config.call_args_list if call.args[0] == "argocd_pause_dry_run"
        )
        assert dry_run_call.args[1] is True

    def test_pause_argocd_acm_apps_clears_state_when_no_crd(self, mock_primary_client, mock_state_manager):
        """No Applications CRD should clear stale Argo CD pause state before returning."""
        prep = PrimaryPreparation(
            primary_client=mock_primary_client,
            state_manager=mock_state_manager,
            acm_version="2.12.0",
            has_observability=False,
            dry_run=False,
            argocd_manage=True,
        )
        mock_state_manager.get_config.side_effect = lambda key, default=None: {
            "argocd_run_id": "stale-run",
            "argocd_paused_apps": [{"hub": "primary", "namespace": "argocd", "name": "stale-app"}],
        }.get(key, default)

        discovery = argocd_lib.ArgocdDiscoveryResult(
            has_applications_crd=False,
            has_argocds_crd=False,
            install_type="vanilla",
        )

        with patch(
            "modules.primary_prep.argocd_lib.detect_argocd_installation",
            return_value=discovery,
        ):
            prep._pause_argocd_acm_apps()

        assert any(call.args == ("argocd_paused_apps", []) for call in mock_state_manager.set_config.call_args_list)
        assert any(call.args == ("argocd_run_id", None) for call in mock_state_manager.set_config.call_args_list)

    def test_pause_argocd_acm_apps_persists_each_app_incrementally(self, mock_primary_client, mock_state_manager):
        """Each paused app must be saved to state independently so a crash preserves prior pauses.

        Verifies that set_config receives a fresh list copy on every iteration (not the same
        mutable reference), so the equality guard in StateManager correctly detects changes.
        """
        prep = PrimaryPreparation(
            primary_client=mock_primary_client,
            state_manager=mock_state_manager,
            acm_version="2.12.0",
            has_observability=False,
            dry_run=False,
            argocd_manage=True,
        )
        mock_state_manager.get_config.side_effect = lambda key, default=None: {
            "argocd_run_id": None,
            "argocd_paused_apps": [],
        }.get(key, default)

        discovery = argocd_lib.ArgocdDiscoveryResult(
            has_applications_crd=True,
            has_argocds_crd=False,
            install_type="vanilla",
        )
        app1 = {
            "metadata": {"namespace": "argocd", "name": "app-1"},
            "spec": {"syncPolicy": {"automated": {}}},
            "status": {
                "resources": [
                    {
                        "kind": "BackupSchedule",
                        "namespace": "open-cluster-management-backup",
                    }
                ]
            },
        }
        app2 = {
            "metadata": {"namespace": "argocd", "name": "app-2"},
            "spec": {"syncPolicy": {"automated": {}}},
            "status": {"resources": [{"kind": "Restore", "namespace": "open-cluster-management-backup"}]},
        }
        impacts = [
            argocd_lib.AppImpact(namespace="argocd", name="app-1", resource_count=1, app=app1),
            argocd_lib.AppImpact(namespace="argocd", name="app-2", resource_count=1, app=app2),
        ]

        def pause_side_effect(client, app, run_id):
            name = app["metadata"]["name"]
            return argocd_lib.PauseResult(
                namespace="argocd",
                name=name,
                original_sync_policy={"automated": {}},
                patched=True,
            )

        with (
            patch(
                "modules.primary_prep.argocd_lib.detect_argocd_installation",
                return_value=discovery,
            ),
            patch(
                "modules.primary_prep.argocd_lib.list_argocd_applications",
                return_value=[app1, app2],
            ),
            patch(
                "modules.primary_prep.argocd_lib.find_acm_touching_apps",
                return_value=impacts,
            ),
            patch(
                "modules.primary_prep.argocd_lib.pause_autosync",
                side_effect=pause_side_effect,
            ),
        ):
            prep._pause_argocd_acm_apps()

        paused_calls = [
            call for call in mock_state_manager.set_config.call_args_list if call.args[0] == "argocd_paused_apps"
        ]
        assert len(paused_calls) == 2, "set_config must be called once per paused app"

        first_list = paused_calls[0].args[1]
        second_list = paused_calls[1].args[1]

        # Each call must carry a distinct list object (copies, not the same reference).
        assert first_list is not second_list, "set_config must receive a copy each iteration, not the same list"
        assert len(first_list) == 1
        assert len(second_list) == 2
        assert first_list[0]["name"] == "app-1"
        assert second_list[1]["name"] == "app-2"

    def test_pause_backup_schedule_acm_212(self, primary_prep_with_obs, mock_primary_client):
        """Test pausing backup schedule for ACM 2.12+."""
        mock_primary_client.list_custom_resources.return_value = [
            {"metadata": {"name": "schedule-rhacm"}, "spec": {"paused": False}}
        ]

        primary_prep_with_obs._pause_backup_schedule()

        mock_primary_client.patch_custom_resource.assert_called_once()
        call_kwargs = mock_primary_client.patch_custom_resource.call_args[1]
        assert call_kwargs["patch"] == {"spec": {"paused": True}}

    def test_pause_backup_schedule_already_paused(self, primary_prep_with_obs, mock_primary_client):
        """Test when backup schedule is already paused."""
        mock_primary_client.list_custom_resources.return_value = [
            {"metadata": {"name": "schedule-rhacm"}, "spec": {"paused": True}}
        ]

        primary_prep_with_obs._pause_backup_schedule()

        # Should not patch if already paused
        mock_primary_client.patch_custom_resource.assert_not_called()

    def test_pause_backup_schedule_not_found(self, primary_prep_with_obs, mock_primary_client):
        """Test when no backup schedule exists."""
        mock_primary_client.list_custom_resources.return_value = []

        # Should handle gracefully
        primary_prep_with_obs._pause_backup_schedule()

        mock_primary_client.patch_custom_resource.assert_not_called()

    @pytest.mark.parametrize(
        "acm_version,should_patch",
        [
            ("2.12.0", True),
            ("2.13.0", True),
            ("2.11.5", False),
            ("2.10.0", False),
        ],
    )
    def test_pause_version_handling(self, mock_primary_client, mock_state_manager, acm_version, should_patch):
        """Test version-specific pause behavior."""
        prep = PrimaryPreparation(
            primary_client=mock_primary_client,
            state_manager=mock_state_manager,
            acm_version=acm_version,
            has_observability=False,
        )

        mock_primary_client.list_custom_resources.return_value = [
            {"metadata": {"name": "schedule-rhacm"}, "spec": {"paused": False}}
        ]

        prep._pause_backup_schedule()

        if should_patch:
            mock_primary_client.patch_custom_resource.assert_called_once()
        else:
            # For ACM < 2.12, use delete instead
            mock_primary_client.delete_custom_resource.assert_called_once()

    def test_disable_auto_import_with_clusters(self, primary_prep_with_obs, mock_primary_client):
        """Test disabling auto-import on managed clusters."""
        mock_primary_client.list_custom_resources.return_value = [
            {"metadata": {"name": "cluster1", "labels": {}}},
            {"metadata": {"name": "local-cluster", "labels": {}}},
            {"metadata": {"name": "cluster2", "labels": {}}},
        ]
        mock_primary_client.list_managed_clusters.return_value = [
            {"metadata": {"name": "cluster1"}},
            {"metadata": {"name": "local-cluster"}},
            {"metadata": {"name": "cluster2"}},
        ]

        primary_prep_with_obs._disable_auto_import()

        # Should patch all clusters except local-cluster
        assert mock_primary_client.patch_managed_cluster.call_count == 2

    def test_disable_auto_import_no_clusters(self, primary_prep_with_obs, mock_primary_client):
        """Test when no managed clusters exist."""
        mock_primary_client.list_custom_resources.return_value = []
        mock_primary_client.list_managed_clusters.return_value = []

        primary_prep_with_obs._disable_auto_import()

        mock_primary_client.patch_managed_cluster.assert_not_called()

    @patch("time.sleep")
    def test_scale_down_thanos(self, mock_sleep, primary_prep_with_obs, mock_primary_client):
        """Test scaling down Thanos compactor."""
        mock_primary_client.scale_statefulset.return_value = {"status": "scaled"}
        mock_primary_client.get_pods.return_value = []  # No pods after scaling down

        primary_prep_with_obs._scale_down_thanos_compactor()

        mock_primary_client.scale_statefulset.assert_called_once_with(
            namespace=OBSERVABILITY_NAMESPACE,
            name="observability-thanos-compact",
            replicas=0,
        )
        mock_sleep.assert_called_once_with(THANOS_SCALE_DOWN_WAIT)

    def test_prepare_error_handling(self, primary_prep_with_obs, mock_primary_client, mock_state_manager):
        """Test error handling during preparation."""
        mock_primary_client.list_custom_resources.side_effect = Exception("API error")

        result = primary_prep_with_obs.prepare()

        assert result is False
        mock_state_manager.add_error.assert_called_once()


@pytest.mark.integration
class TestPrimaryPreparationIntegration:
    """Integration tests for PrimaryPreparation."""

    @patch("time.sleep")
    def test_full_workflow_with_state(self, mock_sleep, mock_primary_client, tmp_path):
        """Test complete workflow with real StateManager."""
        from lib.utils import Phase, StateManager

        state = StateManager(str(tmp_path / "state.json"))
        state.set_phase(Phase.PRIMARY_PREP)

        prep = PrimaryPreparation(
            primary_client=mock_primary_client,
            state_manager=state,
            acm_version="2.12.0",
            has_observability=True,
        )

        # Mock successful flow
        def list_side_effect(*args, **kwargs):
            plural = kwargs.get("plural", "")
            if plural == "backupschedules":
                return [{"metadata": {"name": "schedule-rhacm"}, "spec": {"paused": False}}]
            elif plural == "managedclusters":
                return [{"metadata": {"name": "cluster1", "labels": {}}}]
            return []

        mock_primary_client.list_custom_resources.side_effect = list_side_effect
        mock_primary_client.list_managed_clusters.return_value = [{"metadata": {"name": "cluster1"}}]
        mock_primary_client.patch_custom_resource.return_value = True
        mock_primary_client.scale_statefulset.return_value = {"status": "scaled"}
        mock_primary_client.get_pods.return_value = []

        result = prep.prepare()

        assert result is True
        assert state.is_step_completed("pause_backup_schedule")
        assert state.is_step_completed("disable_auto_import")
        assert state.is_step_completed("scale_down_thanos")
