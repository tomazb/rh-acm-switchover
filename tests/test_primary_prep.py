"""Unit tests for modules/primary_prep.py.

Tests cover PrimaryPreparation class for preparing the primary hub.
"""

import copy
import logging
import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

# Add parent to path to import modules directly
sys.path.insert(0, str(Path(__file__).parent.parent))

import modules.primary_prep as primary_prep_module
from lib import argocd as argocd_lib
from lib.constants import (
    BACKUP_NAMESPACE,
    DELETE_REQUEST_TIMEOUT,
    DISABLE_AUTO_IMPORT_ANNOTATION,
    OBSERVABILITY_NAMESPACE,
    OBSERVABILITY_TERMINATE_INTERVAL,
    OBSERVABILITY_TERMINATE_TIMEOUT,
    THANOS_COMPACTOR_LABEL_SELECTOR,
)
from lib.exceptions import SwitchoverError
from lib.run_record import RunRecord
from lib.waiter import WaitConditionResult

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
    # Back the config accessors with a real dict so tests can seed and read
    # cross-phase facts through RunRecord instead of raw key literals.
    config: dict = {}
    mock.config = config
    mock.set_config.side_effect = config.__setitem__
    mock.get_config.side_effect = lambda key, default=None: config.get(key, default)
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

    def test_pause_backup_schedule_fails_when_multiple_schedules_exist(self, primary_prep_no_obs, mock_primary_client):
        """Multiple BackupSchedules are ambiguous and must not be silently first-selected."""
        mock_primary_client.list_custom_resources.return_value = [
            {"metadata": {"name": "schedule-a"}, "spec": {"paused": False}},
            {"metadata": {"name": "schedule-b"}, "spec": {"paused": False}},
        ]

        with pytest.raises(SwitchoverError, match="Multiple BackupSchedules"):
            primary_prep_no_obs._pause_backup_schedule()

        mock_primary_client.patch_custom_resource.assert_not_called()
        mock_primary_client.delete_custom_resource.assert_not_called()

    def test_prepare_steps_already_completed(self, primary_prep_with_obs, mock_state_manager):
        """Test skipping already completed steps."""
        mock_state_manager.is_step_completed.return_value = True

        result = primary_prep_with_obs.prepare()

        assert result is True

    def test_prepare_dry_run_does_not_mark_argocd_pause_step_completed(self, mock_primary_client, mock_state_manager):
        """Dry-run should not record the Argo CD pause step as completed."""
        prep = PrimaryPreparation(
            primary_client=mock_primary_client,
            state_manager=mock_state_manager,
            acm_version="2.12.0",
            has_observability=False,
            dry_run=True,
            argocd_manage=True,
        )
        mock_primary_client.list_custom_resources.return_value = [
            {"metadata": {"name": "schedule-rhacm"}, "spec": {"paused": False}}
        ]
        mock_primary_client.list_managed_clusters.return_value = [{"metadata": {"name": "cluster1", "labels": {}}}]
        mock_primary_client.patch_custom_resource.return_value = True

        with patch.object(prep, "_pause_argocd_acm_apps"):
            result = prep.prepare()

        assert result is True
        assert not any(
            call.args == ("pause_argocd_apps",) for call in mock_state_manager.mark_step_completed.call_args_list
        )

    def test_prepare_dry_run_still_calls_argocd_pause_logic(self, mock_primary_client, mock_state_manager):
        """Dry-run must still discover Argo CD apps instead of skipping the coordinator path."""
        prep = PrimaryPreparation(
            primary_client=mock_primary_client,
            state_manager=mock_state_manager,
            acm_version="2.12.0",
            has_observability=False,
            dry_run=True,
            argocd_manage=True,
        )
        mock_primary_client.list_custom_resources.return_value = [
            {"metadata": {"name": "schedule-rhacm"}, "spec": {"paused": False}}
        ]
        mock_primary_client.list_managed_clusters.return_value = [{"metadata": {"name": "cluster1", "labels": {}}}]
        mock_primary_client.patch_custom_resource.return_value = True

        with patch.object(prep, "_pause_argocd_acm_apps") as pause_argocd:
            result = prep.prepare()

        assert result is True
        pause_argocd.assert_called_once_with()
        assert not any(
            call.args == ("pause_argocd_apps",) for call in mock_state_manager.mark_step_completed.call_args_list
        )

    def test_prepare_dry_run_surfaces_argocd_blockers(self, mock_primary_client, mock_state_manager):
        """Dry-run should still fail when Argo CD discovery finds pause blockers."""
        prep = PrimaryPreparation(
            primary_client=mock_primary_client,
            state_manager=mock_state_manager,
            acm_version="2.12.0",
            has_observability=False,
            dry_run=True,
            argocd_manage=True,
        )
        mock_primary_client.list_custom_resources.return_value = [
            {"metadata": {"name": "schedule-rhacm"}, "spec": {"paused": False}}
        ]
        mock_primary_client.list_managed_clusters.return_value = [{"metadata": {"name": "cluster1", "labels": {}}}]
        mock_primary_client.patch_custom_resource.return_value = True

        with patch.object(
            prep,
            "_pause_argocd_acm_apps",
            side_effect=SwitchoverError("Argo CD auto-sync pause failed for 1 Application(s)"),
        ) as pause_argocd:
            result = prep.prepare()

        assert result is False
        pause_argocd.assert_called_once_with()
        mock_state_manager.add_error.assert_called_once_with(
            "Argo CD auto-sync pause failed for 1 Application(s)",
            primary_prep_module.Phase.PRIMARY_PREP.value,
        )

    def test_prepare_dry_run_skips_argocd_pause_when_step_already_completed(
        self, mock_primary_client, mock_state_manager
    ):
        """Dry-run should mirror resumed step gating when Argo CD pause already completed."""
        prep = PrimaryPreparation(
            primary_client=mock_primary_client,
            state_manager=mock_state_manager,
            acm_version="2.12.0",
            has_observability=False,
            dry_run=True,
            argocd_manage=True,
        )
        mock_state_manager.is_step_completed.side_effect = lambda step_name: step_name == "pause_argocd_apps"
        mock_primary_client.list_custom_resources.return_value = [
            {"metadata": {"name": "schedule-rhacm"}, "spec": {"paused": False}}
        ]
        mock_primary_client.list_managed_clusters.return_value = [{"metadata": {"name": "cluster1", "labels": {}}}]
        mock_primary_client.patch_custom_resource.return_value = True

        with patch.object(prep, "_pause_argocd_acm_apps") as pause_argocd:
            result = prep.prepare()

        assert result is True
        pause_argocd.assert_not_called()
        assert not any(
            call.args == ("pause_argocd_apps",) for call in mock_state_manager.mark_step_completed.call_args_list
        )

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
            "status": {"resources": [{"kind": "BackupSchedule", "namespace": "open-cluster-management-backup"}]},
        }
        impacts = [argocd_lib.AppImpact(namespace="argocd", name="app-1", resource_count=1, app=app)]

        with (
            patch("lib.argocd_register.argocd_lib.detect_argocd_installation", return_value=discovery),
            patch("lib.argocd_register.argocd_lib.list_argocd_applications", return_value=[app]),
            patch("lib.argocd_register.argocd_lib.find_acm_touching_apps", return_value=impacts),
            patch("lib.argocd_register.argocd_lib.pause_autosync") as pause_autosync,
        ):
            pause_autosync.return_value = argocd_lib.PauseResult(
                namespace="argocd",
                name="app-1",
                original_sync_policy={"automated": {}},
                patched=True,
            )

            prep._pause_argocd_acm_apps()

        pause_autosync.assert_called_once()

        paused_call = [
            call for call in mock_state_manager.set_config.call_args_list if call.args[0] == "argocd_paused_apps"
        ][-1]
        paused_apps = paused_call.args[1]
        assert len(paused_apps) == 1
        assert paused_apps[0]["namespace"] == "argocd"
        assert paused_apps[0]["name"] == "app-1"
        assert paused_apps[0]["pause_applied"] is True

    def test_pause_argocd_acm_apps_dry_run_writes_no_state(self, mock_primary_client, mock_state_manager):
        """ADR-0001: dry-run reports would-pause apps but writes no durable state."""
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
            patch("lib.argocd_register.argocd_lib.detect_argocd_installation", return_value=discovery),
            patch("lib.argocd_register.argocd_lib.list_argocd_applications", return_value=[app]),
            patch("lib.argocd_register.argocd_lib.find_acm_touching_apps", return_value=impacts),
            patch("lib.argocd_register.argocd_lib.pause_autosync") as pause_autosync,
        ):
            pause_autosync.return_value = argocd_lib.PauseResult(
                namespace="argocd",
                name="app-2",
                original_sync_policy={"automated": {"prune": True}},
                patched=True,
            )

            prep._pause_argocd_acm_apps()

        # ADR-0001: dry-run records nothing durable - no state writes at all.
        mock_state_manager.set_config.assert_not_called()

    def test_pause_argocd_acm_apps_dry_run_reports_generated_run_id(
        self, mock_primary_client, mock_state_manager, caplog
    ):
        """G1: dry-run against a clean register still reports the pause it would perform."""
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
            patch("lib.argocd_register.argocd_lib.detect_argocd_installation", return_value=discovery),
            patch("lib.argocd_register.argocd_lib.list_argocd_applications", return_value=[app]),
            patch("lib.argocd_register.argocd_lib.find_acm_touching_apps", return_value=impacts),
            patch("lib.argocd_register.argocd_lib.pause_autosync") as pause_autosync,
            caplog.at_level(logging.INFO, logger="acm_switchover"),
        ):
            pause_autosync.return_value = argocd_lib.PauseResult(
                namespace="argocd",
                name="app-2",
                original_sync_policy={"automated": {"prune": True}},
                patched=True,
            )

            prep._pause_argocd_acm_apps()

        run_id = pause_autosync.call_args.args[2]
        assert run_id
        summary_lines = [
            record.getMessage()
            for record in caplog.records
            if record.getMessage().startswith("Argo CD: ") and "would be paused" in record.getMessage()
        ]
        assert len(summary_lines) == 1
        assert f"run_id={run_id}" in summary_lines[0]
        assert "1 Application(s) would be paused" in summary_lines[0]

    def test_pause_argocd_acm_apps_clears_empty_state_when_no_crd(self, mock_primary_client, mock_state_manager):
        """No Applications CRD clears leftover run_id when the register is empty (ADR-0001)."""
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
            "argocd_paused_apps": [],
        }.get(key, default)

        discovery = argocd_lib.ArgocdDiscoveryResult(
            has_applications_crd=False,
            has_argocds_crd=False,
            install_type="vanilla",
        )

        with patch(
            "lib.argocd_register.argocd_lib.detect_argocd_installation",
            return_value=discovery,
        ):
            prep._pause_argocd_acm_apps()

        # ADR-0001: only an empty register is cleared on CRD-visibility loss.
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
            "status": {"resources": [{"kind": "BackupSchedule", "namespace": "open-cluster-management-backup"}]},
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
            patch("lib.argocd_register.argocd_lib.detect_argocd_installation", return_value=discovery),
            patch("lib.argocd_register.argocd_lib.list_argocd_applications", return_value=[app1, app2]),
            patch("lib.argocd_register.argocd_lib.find_acm_touching_apps", return_value=impacts),
            patch("lib.argocd_register.argocd_lib.pause_autosync", side_effect=pause_side_effect),
        ):
            prep._pause_argocd_acm_apps()

        paused_calls = [
            call for call in mock_state_manager.set_config.call_args_list if call.args[0] == "argocd_paused_apps"
        ]
        assert len(paused_calls) == 4, "set_config must persist provisional and confirmed state for each app"

        first_list = paused_calls[0].args[1]
        second_list = paused_calls[1].args[1]
        third_list = paused_calls[2].args[1]
        fourth_list = paused_calls[3].args[1]

        assert first_list is not second_list
        assert second_list is not third_list
        assert third_list is not fourth_list
        assert first_list[0]["pause_applied"] is False
        assert second_list[0]["pause_applied"] is True
        assert len(third_list) == 2
        assert third_list[1]["pause_applied"] is False
        assert fourth_list[1]["pause_applied"] is True

    def test_pause_argocd_acm_apps_raises_on_patch_failure(self, mock_primary_client, mock_state_manager):
        """Patch failures must fail the Argo CD pause step instead of being treated as no-op."""
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
            "status": {"resources": [{"kind": "Restore", "namespace": "open-cluster-management-backup"}]},
        }
        impacts = [argocd_lib.AppImpact(namespace="argocd", name="app-1", resource_count=1, app=app)]

        with (
            patch("lib.argocd_register.argocd_lib.detect_argocd_installation", return_value=discovery),
            patch("lib.argocd_register.argocd_lib.list_argocd_applications", return_value=[app]),
            patch("lib.argocd_register.argocd_lib.find_acm_touching_apps", return_value=impacts),
            patch(
                "lib.argocd_register.argocd_lib.pause_autosync",
                return_value=argocd_lib.PauseResult(
                    namespace="argocd",
                    name="app-1",
                    original_sync_policy={"automated": {}},
                    patched=False,
                    error="403 Forbidden",
                ),
            ),
        ):
            with pytest.raises(SwitchoverError, match="pause failed for 1"):
                prep._pause_argocd_acm_apps()

        paused_calls = [
            call for call in mock_state_manager.set_config.call_args_list if call.args[0] == "argocd_paused_apps"
        ]
        assert paused_calls[-1].args == ("argocd_paused_apps", [])

    def test_pause_argocd_failure_removes_stale_pause_entry(self, mock_primary_client, mock_state_manager):
        """A failed retry must clear any stale resumable pause entry for that app."""
        prep = PrimaryPreparation(
            primary_client=mock_primary_client,
            state_manager=mock_state_manager,
            acm_version="2.12.0",
            has_observability=False,
            dry_run=False,
            argocd_manage=True,
        )
        state_config = {
            "argocd_run_id": "run-1",
            "argocd_paused_apps": [
                {
                    "hub": "primary",
                    "namespace": "openshift-gitops",
                    "name": "acm-app",
                    "original_sync_policy": {"automated": {}},
                    "pause_applied": False,
                }
            ],
        }
        mock_state_manager.get_config.side_effect = lambda key, default=None: copy.deepcopy(
            state_config.get(key, default)
        )
        mock_state_manager.set_config.side_effect = lambda key, value: state_config.__setitem__(
            key, copy.deepcopy(value)
        )

        discovery = argocd_lib.ArgocdDiscoveryResult(
            has_applications_crd=True,
            has_argocds_crd=False,
            install_type="vanilla",
        )
        app = {
            "metadata": {"namespace": "openshift-gitops", "name": "acm-app"},
            "spec": {"syncPolicy": {"automated": {}}},
            "status": {"resources": [{"kind": "Restore", "namespace": "open-cluster-management-backup"}]},
        }
        impacts = [argocd_lib.AppImpact(namespace="openshift-gitops", name="acm-app", resource_count=1, app=app)]

        with (
            patch("lib.argocd_register.argocd_lib.detect_argocd_installation", return_value=discovery),
            patch("lib.argocd_register.argocd_lib.list_argocd_applications", return_value=[app]),
            patch("lib.argocd_register.argocd_lib.find_acm_touching_apps", return_value=impacts),
            patch(
                "lib.argocd_register.argocd_lib.pause_autosync",
                return_value=argocd_lib.PauseResult(
                    namespace="openshift-gitops",
                    name="acm-app",
                    original_sync_policy={"automated": {}},
                    patched=False,
                    error="patch failed",
                ),
            ),
        ):
            with pytest.raises(SwitchoverError, match="pause failed for 1"):
                prep._pause_argocd_acm_apps()

        assert mock_state_manager.get_config("argocd_paused_apps") == []

    def test_pause_argocd_acm_apps_recovers_pending_entry_when_app_already_paused(
        self, mock_primary_client, mock_state_manager
    ):
        """Retry should confirm a previously recorded pause when the live app is already missing automated sync."""
        prep = PrimaryPreparation(
            primary_client=mock_primary_client,
            state_manager=mock_state_manager,
            acm_version="2.12.0",
            has_observability=False,
            dry_run=False,
            argocd_manage=True,
        )
        mock_state_manager.get_config.side_effect = lambda key, default=None: {
            "argocd_run_id": "run-1",
            "argocd_paused_apps": [
                {
                    "hub": "primary",
                    "namespace": "argocd",
                    "name": "app-1",
                    "original_sync_policy": {"automated": {"prune": True}},
                    "pause_applied": False,
                }
            ],
        }.get(key, default)

        discovery = argocd_lib.ArgocdDiscoveryResult(
            has_applications_crd=True,
            has_argocds_crd=False,
            install_type="vanilla",
        )
        app = {
            "metadata": {
                "namespace": "argocd",
                "name": "app-1",
                "annotations": {argocd_lib.ARGOCD_PAUSED_BY_ANNOTATION: "run-1"},
            },
            "spec": {"syncPolicy": {"syncOptions": ["CreateNamespace=true"]}},
            "status": {"resources": [{"kind": "Restore", "namespace": "open-cluster-management-backup"}]},
        }
        impacts = [argocd_lib.AppImpact(namespace="argocd", name="app-1", resource_count=1, app=app)]

        with (
            patch("lib.argocd_register.argocd_lib.detect_argocd_installation", return_value=discovery),
            patch("lib.argocd_register.argocd_lib.list_argocd_applications", return_value=[app]),
            patch("lib.argocd_register.argocd_lib.find_acm_touching_apps", return_value=impacts),
            patch("lib.argocd_register.argocd_lib.pause_autosync") as pause_autosync,
        ):
            prep._pause_argocd_acm_apps()

        pause_autosync.assert_not_called()
        paused_call = [
            call for call in mock_state_manager.set_config.call_args_list if call.args[0] == "argocd_paused_apps"
        ][-1]
        paused_apps = paused_call.args[1]
        assert paused_apps[0]["pause_applied"] is True
        assert paused_apps[0]["original_sync_policy"] == {"automated": {"prune": True}}

    def test_pause_argocd_acm_apps_keeps_recorded_entry_when_app_already_paused(
        self, mock_primary_client, mock_state_manager
    ):
        """Steady-state reruns should keep an already recorded pause entry unchanged."""
        prep = PrimaryPreparation(
            primary_client=mock_primary_client,
            state_manager=mock_state_manager,
            acm_version="2.12.0",
            has_observability=False,
            dry_run=False,
            argocd_manage=True,
        )
        recorded_entry = {
            "hub": "primary",
            "namespace": "argocd",
            "name": "app-1",
            "original_sync_policy": {"automated": {"prune": True}},
            "pause_applied": True,
        }
        state_config = {
            "argocd_run_id": "run-1",
            "argocd_paused_apps": [recorded_entry],
        }
        mock_state_manager.get_config.side_effect = lambda key, default=None: copy.deepcopy(
            state_config.get(key, default)
        )
        mock_state_manager.set_config.side_effect = lambda key, value: state_config.__setitem__(
            key, copy.deepcopy(value)
        )

        discovery = argocd_lib.ArgocdDiscoveryResult(
            has_applications_crd=True,
            has_argocds_crd=False,
            install_type="vanilla",
        )
        app = {
            "metadata": {"namespace": "argocd", "name": "app-1"},
            "spec": {"syncPolicy": {"syncOptions": ["CreateNamespace=true"]}},
            "status": {"resources": [{"kind": "Restore", "namespace": "open-cluster-management-backup"}]},
        }
        impacts = [argocd_lib.AppImpact(namespace="argocd", name="app-1", resource_count=1, app=app)]

        with (
            patch("lib.argocd_register.argocd_lib.detect_argocd_installation", return_value=discovery),
            patch("lib.argocd_register.argocd_lib.list_argocd_applications", return_value=[app]),
            patch("lib.argocd_register.argocd_lib.find_acm_touching_apps", return_value=impacts),
            patch("lib.argocd_register.argocd_lib.pause_autosync") as pause_autosync,
        ):
            prep._pause_argocd_acm_apps()

        pause_autosync.assert_not_called()
        assert not any(call.args[0] == "argocd_paused_apps" for call in mock_state_manager.set_config.call_args_list)
        assert state_config["argocd_paused_apps"] == [recorded_entry]

    def test_pause_backup_schedule_acm_212(self, primary_prep_with_obs, mock_primary_client, mock_state_manager):
        """Test pausing backup schedule for ACM 2.12+."""
        backup_schedule = {
            "metadata": {"name": "schedule-rhacm", "resourceVersion": "12345"},
            "spec": {"paused": False},
        }
        mock_primary_client.list_custom_resources.return_value = [backup_schedule]

        def patch_side_effect(**_kwargs):
            # The snapshot must already be persisted before the pause patch is issued.
            assert RunRecord(mock_state_manager).saved_backup_schedule() == backup_schedule
            return True

        mock_primary_client.patch_custom_resource.side_effect = patch_side_effect

        primary_prep_with_obs._pause_backup_schedule()

        mock_primary_client.list_custom_resources.assert_called_once_with(
            group="cluster.open-cluster-management.io",
            version="v1beta1",
            plural="backupschedules",
            namespace=BACKUP_NAMESPACE,
            max_items=2,
        )
        mock_primary_client.patch_custom_resource.assert_called_once_with(
            group="cluster.open-cluster-management.io",
            version="v1beta1",
            plural="backupschedules",
            name="schedule-rhacm",
            patch={"spec": {"paused": True}},
            namespace=BACKUP_NAMESPACE,
        )
        mock_primary_client.delete_custom_resource.assert_not_called()

    def test_pause_backup_schedule_acm_211_delete_targets_backup_schedule(
        self, mock_primary_client, mock_state_manager
    ):
        """ACM 2.11 pause must snapshot then delete the BackupSchedule by exact resource target."""
        backup_schedule = {"metadata": {"name": "schedule-rhacm"}, "spec": {"paused": False}}
        prep = PrimaryPreparation(
            primary_client=mock_primary_client,
            state_manager=mock_state_manager,
            acm_version="2.11.6",
            has_observability=False,
        )
        mock_primary_client.list_custom_resources.return_value = [backup_schedule]

        def delete_side_effect(**_kwargs):
            # The snapshot must already be persisted before the BackupSchedule is deleted.
            assert RunRecord(mock_state_manager).saved_backup_schedule() == backup_schedule
            return True

        mock_primary_client.delete_custom_resource.side_effect = delete_side_effect

        prep._pause_backup_schedule()

        mock_primary_client.list_custom_resources.assert_called_once_with(
            group="cluster.open-cluster-management.io",
            version="v1beta1",
            plural="backupschedules",
            namespace=BACKUP_NAMESPACE,
            max_items=2,
        )
        mock_primary_client.delete_custom_resource.assert_called_once_with(
            group="cluster.open-cluster-management.io",
            version="v1beta1",
            plural="backupschedules",
            name="schedule-rhacm",
            namespace=BACKUP_NAMESPACE,
            timeout_seconds=DELETE_REQUEST_TIMEOUT,
        )
        mock_primary_client.patch_custom_resource.assert_not_called()

    def test_pause_backup_schedule_already_paused_persists_when_no_saved_schedule_exists(
        self, primary_prep_with_obs, mock_primary_client, mock_state_manager
    ):
        """Already-paused reruns must persist the BackupSchedule when no saved schedule exists."""
        backup_schedule = {"metadata": {"name": "schedule-rhacm"}, "spec": {"paused": True}}
        mock_primary_client.list_custom_resources.return_value = [backup_schedule]

        primary_prep_with_obs._pause_backup_schedule()

        assert RunRecord(mock_state_manager).saved_backup_schedule() == backup_schedule
        mock_primary_client.patch_custom_resource.assert_not_called()
        mock_primary_client.delete_custom_resource.assert_not_called()

    def test_pause_backup_schedule_already_paused_keeps_existing_saved_schedule(
        self, primary_prep_with_obs, mock_primary_client, mock_state_manager
    ):
        """Already-paused reruns must not overwrite a previously saved BackupSchedule."""
        mock_primary_client.list_custom_resources.return_value = [
            {"metadata": {"name": "schedule-rhacm"}, "spec": {"paused": True}}
        ]
        previous_schedule = {"metadata": {"name": "previous-schedule"}}
        RunRecord(mock_state_manager).record_saved_backup_schedule(previous_schedule)
        mock_state_manager.set_config.reset_mock()

        primary_prep_with_obs._pause_backup_schedule()

        assert RunRecord(mock_state_manager).saved_backup_schedule() == previous_schedule
        mock_state_manager.set_config.assert_not_called()
        mock_primary_client.patch_custom_resource.assert_not_called()
        mock_primary_client.delete_custom_resource.assert_not_called()

    def test_pause_backup_schedule_not_found(self, primary_prep_with_obs, mock_primary_client):
        """Test when no backup schedule exists."""
        mock_primary_client.list_custom_resources.return_value = []

        # Should handle gracefully
        primary_prep_with_obs._pause_backup_schedule()

        mock_primary_client.patch_custom_resource.assert_not_called()

    def test_pause_backup_schedule_nameless_object_raises(self, primary_prep_with_obs, mock_primary_client):
        """BackupSchedule with no name in metadata must raise SwitchoverError, not silently succeed."""
        mock_primary_client.list_custom_resources.return_value = [{"metadata": {}, "spec": {"paused": False}}]

        with pytest.raises(SwitchoverError, match="no name in metadata"):
            primary_prep_with_obs._pause_backup_schedule()

    @pytest.mark.parametrize(
        "acm_version,should_patch",
        [
            ("2.12.0", True),
            ("2.13.0", True),
            ("2.14.3-rc1", True),
            ("2.14.3+build", True),
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

    @pytest.mark.parametrize("acm_version", ["not-a-version", "2"])
    def test_pause_backup_schedule_fails_closed_for_unparseable_version(
        self, mock_primary_client, mock_state_manager, acm_version
    ):
        """Unparseable ACM versions must not fall through to the delete path."""
        prep = PrimaryPreparation(
            primary_client=mock_primary_client,
            state_manager=mock_state_manager,
            acm_version=acm_version,
            has_observability=False,
        )
        mock_primary_client.list_custom_resources.return_value = [
            {"metadata": {"name": "schedule-rhacm"}, "spec": {"paused": False}}
        ]

        with pytest.raises(SwitchoverError, match="Invalid ACM version"):
            prep._pause_backup_schedule()

        mock_primary_client.patch_custom_resource.assert_not_called()
        mock_primary_client.delete_custom_resource.assert_not_called()

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

    def test_disable_auto_import_skips_already_annotated_clusters(self, primary_prep_with_obs, mock_primary_client):
        """Already-annotated clusters should be skipped so reruns only patch remaining clusters."""
        managed_clusters = [
            {"metadata": {"name": "cluster-a", "annotations": {DISABLE_AUTO_IMPORT_ANNOTATION: ""}}},
            {"metadata": {"name": "cluster-b", "annotations": {}}},
        ]
        mock_primary_client.list_custom_resources.return_value = managed_clusters
        mock_primary_client.list_managed_clusters.return_value = managed_clusters

        primary_prep_with_obs._disable_auto_import()

        mock_primary_client.patch_managed_cluster.assert_called_once_with(
            name="cluster-b",
            patch={"metadata": {"annotations": {DISABLE_AUTO_IMPORT_ANNOTATION: ""}}},
        )

    def test_disable_auto_import_no_clusters(self, primary_prep_with_obs, mock_primary_client):
        """Test when no managed clusters exist."""
        mock_primary_client.list_custom_resources.return_value = []
        mock_primary_client.list_managed_clusters.return_value = []

        primary_prep_with_obs._disable_auto_import()

        mock_primary_client.patch_managed_cluster.assert_not_called()

    @patch("modules.primary_prep.wait_for_condition")
    def test_scale_down_thanos(self, mock_wait, primary_prep_with_obs, mock_primary_client):
        """Test scaling down Thanos compactor."""
        mock_primary_client.scale_statefulset.return_value = {"status": "scaled"}
        mock_primary_client.get_pods.return_value = []  # No pods after scaling down

        def wait_side_effect(_description, condition_fn, **_kwargs):
            assert condition_fn() == WaitConditionResult.complete("all Thanos compactor pods terminated")
            return True

        mock_wait.side_effect = wait_side_effect

        primary_prep_with_obs._scale_down_thanos_compactor()

        mock_primary_client.scale_statefulset.assert_called_once_with(
            namespace=OBSERVABILITY_NAMESPACE,
            name="observability-thanos-compact",
            replicas=0,
        )
        mock_wait.assert_called_once()
        _, condition_fn = mock_wait.call_args.args[:2]
        assert mock_wait.call_args.kwargs["timeout"] == OBSERVABILITY_TERMINATE_TIMEOUT
        assert mock_wait.call_args.kwargs["interval"] == OBSERVABILITY_TERMINATE_INTERVAL
        mock_primary_client.get_pods.assert_called_once_with(
            namespace=OBSERVABILITY_NAMESPACE,
            label_selector=THANOS_COMPACTOR_LABEL_SELECTOR,
        )

    def test_prepare_with_thanos_404_blocks(
        self, primary_prep_with_obs, mock_primary_client, mock_state_manager, caplog
    ):
        """Missing Thanos compactor should block when Observability checks are enabled."""
        mock_primary_client.list_custom_resources.return_value = [
            {"metadata": {"name": "schedule-rhacm"}, "spec": {"paused": False}}
        ]
        mock_primary_client.list_managed_clusters.return_value = []
        mock_primary_client.scale_statefulset.side_effect = primary_prep_module.ApiException(
            status=404,
            reason="Not Found",
        )

        with caplog.at_level(logging.ERROR, logger="acm_switchover"):
            result = primary_prep_with_obs.prepare()

        assert result is False
        mock_state_manager.add_error.assert_called_once()
        assert not any(
            call.args == ("scale_down_thanos",) for call in mock_state_manager.mark_step_completed.call_args_list
        )
        assert "Failed to scale down Thanos compactor" in caplog.text
        mock_primary_client.get_pods.assert_not_called()

    def test_scale_down_thanos_404_maps_to_switchover_error(self, primary_prep_with_obs, mock_primary_client, caplog):
        """A missing Thanos StatefulSet must fail closed with the domain error expected by prepare()."""
        mock_primary_client.scale_statefulset.side_effect = primary_prep_module.ApiException(
            status=404,
            reason="Not Found",
        )

        with (
            caplog.at_level(logging.ERROR, logger="acm_switchover"),
            pytest.raises(SwitchoverError, match="Thanos compactor StatefulSet not found"),
        ):
            primary_prep_with_obs._scale_down_thanos_compactor()

        assert "Failed to scale down Thanos compactor: StatefulSet not found" in caplog.text
        mock_primary_client.get_pods.assert_not_called()

    @patch("modules.primary_prep.wait_for_condition")
    def test_scale_down_thanos_pods_remaining_blocks(self, mock_wait, primary_prep_with_obs, mock_primary_client):
        """Thanos pods still running after scale-down should block primary prep."""
        mock_primary_client.scale_statefulset.return_value = {"status": "scaled"}
        mock_primary_client.get_pods.return_value = [{"metadata": {"name": "thanos-compact-0"}}]
        mock_wait.return_value = False

        with pytest.raises(SwitchoverError) as exc_info:
            primary_prep_with_obs._scale_down_thanos_compactor()

        assert "Thanos compactor still has 1 pod(s) running after scale-down timeout" in str(exc_info.value)
        mock_wait.assert_called_once()
        mock_primary_client.get_pods.assert_called_once_with(
            namespace=OBSERVABILITY_NAMESPACE,
            label_selector=THANOS_COMPACTOR_LABEL_SELECTOR,
        )

    def test_prepare_with_thanos_api_exception_fails_as_real_error(
        self, primary_prep_with_obs, mock_primary_client, mock_state_manager, caplog
    ):
        """Non-404 Thanos API errors should fail primary prep and be recorded."""
        mock_primary_client.list_custom_resources.return_value = [
            {"metadata": {"name": "schedule-rhacm"}, "spec": {"paused": False}}
        ]
        mock_primary_client.list_managed_clusters.return_value = []
        mock_primary_client.scale_statefulset.side_effect = primary_prep_module.ApiException(
            status=500,
            reason="Internal Server Error",
        )

        with caplog.at_level(logging.ERROR, logger="acm_switchover"):
            result = primary_prep_with_obs.prepare()

        assert result is False
        mock_state_manager.add_error.assert_called_once()
        error_message, phase = mock_state_manager.add_error.call_args.args
        assert phase == "primary_preparation"
        assert error_message.startswith("Unexpected:")
        assert "500" in error_message
        assert "Failed to scale down Thanos compactor" in caplog.text
        assert not any(
            call.args == ("scale_down_thanos",) for call in mock_state_manager.mark_step_completed.call_args_list
        )
        mock_primary_client.get_pods.assert_not_called()

    @pytest.mark.parametrize(
        "error",
        [
            RuntimeError("compactor shutdown timed out"),
            ValueError("invalid replica count"),
        ],
        ids=["runtime_error", "value_error"],
    )
    def test_scale_down_thanos_runtime_and_value_errors_propagate(
        self, primary_prep_with_obs, mock_primary_client, caplog, error
    ):
        """Runtime and value failures should bubble up unchanged for callers."""
        mock_primary_client.scale_statefulset.side_effect = error

        with (
            caplog.at_level(logging.ERROR, logger="acm_switchover"),
            pytest.raises(type(error)) as exc_info,
        ):
            primary_prep_with_obs._scale_down_thanos_compactor()

        assert exc_info.value is error
        assert f"Failed to scale down Thanos compactor: {error}" in caplog.text
        mock_primary_client.get_pods.assert_not_called()

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
