"""Unit tests for lib/argocd_coordinator.py.

Tests cover ArgoCDPauseCoordinator: hub detection, pause execution,
entry recovery, clobber guard, dry-run, error handling, and state persistence.
"""

import copy
from unittest.mock import Mock, patch

import pytest

from lib import argocd as argocd_lib
from lib.argocd_coordinator import ArgoCDPauseCoordinator


def _make_state_manager(config=None):
    """Create a mock StateManager backed by a real dict for config tracking."""
    state_config = config or {}
    mock = Mock()
    mock.get_config.side_effect = lambda key, default=None: copy.deepcopy(state_config.get(key, default))
    mock.set_config.side_effect = lambda key, value: state_config.__setitem__(key, copy.deepcopy(value))
    mock._config = state_config
    return mock


def _make_app(namespace, name, *, automated=True, resources=None, annotations=None):
    """Build a minimal Argo CD Application dict."""
    sync_policy = {"automated": {}} if automated else {}
    if resources is None:
        resources = [{"kind": "BackupSchedule", "namespace": "open-cluster-management-backup"}]
    return {
        "metadata": {"namespace": namespace, "name": name, "annotations": annotations or {}},
        "spec": {"syncPolicy": sync_policy},
        "status": {"resources": resources},
    }


def _make_impact(app):
    meta = app["metadata"]
    return argocd_lib.AppImpact(
        namespace=meta["namespace"],
        name=meta["name"],
        resource_count=1,
        app=app,
    )


def _discovery_with_crd():
    return argocd_lib.ArgocdDiscoveryResult(
        has_applications_crd=True,
        has_argocds_crd=False,
        install_type="vanilla",
    )


def _discovery_without_crd():
    return argocd_lib.ArgocdDiscoveryResult(
        has_applications_crd=False,
        has_argocds_crd=False,
        install_type="none",
    )


@pytest.mark.unit
class TestPauseHubsSingleHub:
    """pause_hubs with a single hub (restore-only scenario)."""

    def test_pauses_acm_app_on_secondary(self):
        state = _make_state_manager({"argocd_run_id": None, "argocd_paused_apps": []})
        client = Mock()
        app = _make_app("argocd", "app-1")
        impact = _make_impact(app)

        with (
            patch(
                "lib.argocd_coordinator.argocd_lib.detect_argocd_installation",
                return_value=_discovery_with_crd(),
            ),
            patch(
                "lib.argocd_coordinator.argocd_lib.list_argocd_applications",
                return_value=[app],
            ),
            patch(
                "lib.argocd_coordinator.argocd_lib.find_acm_touching_apps",
                return_value=[impact],
            ),
            patch("lib.argocd_coordinator.argocd_lib.pause_autosync") as mock_pause,
        ):
            mock_pause.return_value = argocd_lib.PauseResult(
                namespace="argocd",
                name="app-1",
                original_sync_policy={"automated": {}},
                patched=True,
            )
            coordinator = ArgoCDPauseCoordinator(state, dry_run=False)
            paused_apps, failures = coordinator.pause_hubs([(client, "secondary")])

        assert failures == 0
        assert len(paused_apps) == 1
        assert paused_apps[0]["hub"] == "secondary"
        assert paused_apps[0]["name"] == "app-1"
        assert paused_apps[0]["pause_applied"] is True

    def test_no_crd_clears_state(self):
        state = _make_state_manager(
            {
                "argocd_run_id": "stale",
                "argocd_paused_apps": [{"hub": "secondary", "name": "old"}],
            }
        )
        client = Mock()

        with patch(
            "lib.argocd_coordinator.argocd_lib.detect_argocd_installation",
            return_value=_discovery_without_crd(),
        ):
            coordinator = ArgoCDPauseCoordinator(state, dry_run=False)
            paused_apps, failures = coordinator.pause_hubs([(client, "secondary")])

        assert paused_apps == []
        assert failures == 0
        assert state._config["argocd_paused_apps"] == []
        assert state._config["argocd_run_id"] is None
        assert state._config["argocd_pause_dry_run"] is False

    def test_skips_app_without_autosync(self):
        state = _make_state_manager({"argocd_run_id": None, "argocd_paused_apps": []})
        client = Mock()
        app = _make_app("argocd", "static-app", automated=False)
        impact = _make_impact(app)

        with (
            patch(
                "lib.argocd_coordinator.argocd_lib.detect_argocd_installation",
                return_value=_discovery_with_crd(),
            ),
            patch(
                "lib.argocd_coordinator.argocd_lib.list_argocd_applications",
                return_value=[app],
            ),
            patch(
                "lib.argocd_coordinator.argocd_lib.find_acm_touching_apps",
                return_value=[impact],
            ),
            patch("lib.argocd_coordinator.argocd_lib.pause_autosync") as mock_pause,
        ):
            coordinator = ArgoCDPauseCoordinator(state, dry_run=False)
            paused_apps, failures = coordinator.pause_hubs([(client, "secondary")])

        mock_pause.assert_not_called()
        assert paused_apps == []
        assert failures == 0


@pytest.mark.unit
class TestPauseHubsTwoHubs:
    """pause_hubs with two hubs (switchover scenario)."""

    def test_pauses_apps_on_both_hubs(self):
        state = _make_state_manager({"argocd_run_id": None, "argocd_paused_apps": []})
        primary_client = Mock()
        secondary_client = Mock()
        app_p = _make_app("argocd", "app-primary")
        app_s = _make_app("argocd", "app-secondary")

        def detect_side_effect(client):
            return _discovery_with_crd()

        def list_side_effect(client, namespaces=None):
            if client is primary_client:
                return [app_p]
            return [app_s]

        def filter_side_effect(apps):
            return [_make_impact(a) for a in apps]

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
                "lib.argocd_coordinator.argocd_lib.detect_argocd_installation",
                side_effect=detect_side_effect,
            ),
            patch(
                "lib.argocd_coordinator.argocd_lib.list_argocd_applications",
                side_effect=list_side_effect,
            ),
            patch(
                "lib.argocd_coordinator.argocd_lib.find_acm_touching_apps",
                side_effect=filter_side_effect,
            ),
            patch(
                "lib.argocd_coordinator.argocd_lib.pause_autosync",
                side_effect=pause_side_effect,
            ),
        ):
            coordinator = ArgoCDPauseCoordinator(state, dry_run=False)
            paused_apps, failures = coordinator.pause_hubs(
                [
                    (primary_client, "primary"),
                    (secondary_client, "secondary"),
                ]
            )

        assert failures == 0
        assert len(paused_apps) == 2
        hubs = {e["hub"] for e in paused_apps}
        assert hubs == {"primary", "secondary"}

    def test_skips_hub_without_crd(self):
        """When one hub lacks the CRD, only the other hub is processed."""
        state = _make_state_manager({"argocd_run_id": None, "argocd_paused_apps": []})
        primary_client = Mock()
        secondary_client = Mock()
        app = _make_app("argocd", "app-1")

        def detect_side_effect(client):
            if client is primary_client:
                return _discovery_with_crd()
            return _discovery_without_crd()

        with (
            patch(
                "lib.argocd_coordinator.argocd_lib.detect_argocd_installation",
                side_effect=detect_side_effect,
            ),
            patch(
                "lib.argocd_coordinator.argocd_lib.list_argocd_applications",
                return_value=[app],
            ),
            patch(
                "lib.argocd_coordinator.argocd_lib.find_acm_touching_apps",
                return_value=[_make_impact(app)],
            ),
            patch("lib.argocd_coordinator.argocd_lib.pause_autosync") as mock_pause,
        ):
            mock_pause.return_value = argocd_lib.PauseResult(
                namespace="argocd",
                name="app-1",
                original_sync_policy={"automated": {}},
                patched=True,
            )
            coordinator = ArgoCDPauseCoordinator(state, dry_run=False)
            paused_apps, failures = coordinator.pause_hubs(
                [
                    (primary_client, "primary"),
                    (secondary_client, "secondary"),
                ]
            )

        assert failures == 0
        assert len(paused_apps) == 1
        assert paused_apps[0]["hub"] == "primary"


@pytest.mark.unit
class TestIdempotentRepause:
    """Clobber guard and entry recovery via the coordinator."""

    def test_already_paused_and_recorded_is_skipped(self):
        """An app already paused and confirmed in state must not be re-paused."""
        state = _make_state_manager(
            {
                "argocd_run_id": "run-1",
                "argocd_paused_apps": [
                    {
                        "hub": "primary",
                        "namespace": "argocd",
                        "name": "app-1",
                        "original_sync_policy": {"automated": {"prune": True}},
                        "pause_applied": True,
                    }
                ],
            }
        )
        client = Mock()
        # App is already paused (no automated in syncPolicy)
        app = _make_app("argocd", "app-1", automated=False)

        with (
            patch(
                "lib.argocd_coordinator.argocd_lib.detect_argocd_installation",
                return_value=_discovery_with_crd(),
            ),
            patch(
                "lib.argocd_coordinator.argocd_lib.list_argocd_applications",
                return_value=[app],
            ),
            patch(
                "lib.argocd_coordinator.argocd_lib.find_acm_touching_apps",
                return_value=[_make_impact(app)],
            ),
            patch("lib.argocd_coordinator.argocd_lib.pause_autosync") as mock_pause,
        ):
            coordinator = ArgoCDPauseCoordinator(state, dry_run=False)
            paused_apps, failures = coordinator.pause_hubs([(client, "primary")])

        mock_pause.assert_not_called()
        assert failures == 0
        assert len(paused_apps) == 1
        assert paused_apps[0]["pause_applied"] is True

    def test_recovers_pending_entry_when_app_already_paused(self):
        """Entry with pause_applied=False should be confirmed when live app has this run's pause marker."""
        state = _make_state_manager(
            {
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
            }
        )
        client = Mock()
        app = _make_app(
            "argocd",
            "app-1",
            automated=False,
            annotations={argocd_lib.ARGOCD_PAUSED_BY_ANNOTATION: "run-1"},
        )

        with (
            patch(
                "lib.argocd_coordinator.argocd_lib.detect_argocd_installation",
                return_value=_discovery_with_crd(),
            ),
            patch(
                "lib.argocd_coordinator.argocd_lib.list_argocd_applications",
                return_value=[app],
            ),
            patch(
                "lib.argocd_coordinator.argocd_lib.find_acm_touching_apps",
                return_value=[_make_impact(app)],
            ),
            patch("lib.argocd_coordinator.argocd_lib.pause_autosync") as mock_pause,
        ):
            coordinator = ArgoCDPauseCoordinator(state, dry_run=False)
            paused_apps, failures = coordinator.pause_hubs([(client, "primary")])

        mock_pause.assert_not_called()
        assert failures == 0
        assert paused_apps[0]["pause_applied"] is True
        # Verify state was persisted with confirmed entry
        persisted = state._config["argocd_paused_apps"]
        assert persisted[0]["pause_applied"] is True

    def test_does_not_recover_pending_entry_without_matching_marker(self):
        """An unconfirmed entry must not be treated as ours when the live marker is absent."""
        state = _make_state_manager(
            {
                "argocd_run_id": "run-1",
                "argocd_paused_apps": [
                    {
                        "hub": "primary",
                        "namespace": "argocd",
                        "name": "app-1",
                        "original_sync_policy": {"automated": {"prune": True}},
                        "pause_applied": False,
                        "pause_state": "unknown",
                        "pause_run_id": "run-1",
                    }
                ],
            }
        )
        client = Mock()
        app = _make_app("argocd", "app-1", automated=False)

        with (
            patch(
                "lib.argocd_coordinator.argocd_lib.detect_argocd_installation",
                return_value=_discovery_with_crd(),
            ),
            patch(
                "lib.argocd_coordinator.argocd_lib.list_argocd_applications",
                return_value=[app],
            ),
            patch(
                "lib.argocd_coordinator.argocd_lib.find_acm_touching_apps",
                return_value=[_make_impact(app)],
            ),
            patch("lib.argocd_coordinator.argocd_lib.pause_autosync") as mock_pause,
        ):
            coordinator = ArgoCDPauseCoordinator(state, dry_run=False)
            paused_apps, failures = coordinator.pause_hubs([(client, "primary")])

        mock_pause.assert_not_called()
        assert failures == 0
        assert paused_apps == []
        assert state._config["argocd_paused_apps"] == []

    def test_recovers_unknown_entry_only_with_matching_marker(self):
        state = _make_state_manager(
            {
                "argocd_run_id": "run-1",
                "argocd_paused_apps": [
                    {
                        "hub": "primary",
                        "namespace": "argocd",
                        "name": "app-1",
                        "original_sync_policy": {"automated": {"prune": True}},
                        "pause_applied": False,
                        "pause_state": "unknown",
                        "pause_run_id": "run-1",
                    }
                ],
            }
        )
        client = Mock()
        app = _make_app(
            "argocd",
            "app-1",
            automated=False,
            annotations={argocd_lib.ARGOCD_PAUSED_BY_ANNOTATION: "run-1"},
        )

        with (
            patch(
                "lib.argocd_coordinator.argocd_lib.detect_argocd_installation",
                return_value=_discovery_with_crd(),
            ),
            patch(
                "lib.argocd_coordinator.argocd_lib.list_argocd_applications",
                return_value=[app],
            ),
            patch(
                "lib.argocd_coordinator.argocd_lib.find_acm_touching_apps",
                return_value=[_make_impact(app)],
            ),
            patch("lib.argocd_coordinator.argocd_lib.pause_autosync") as mock_pause,
        ):
            coordinator = ArgoCDPauseCoordinator(state, dry_run=False)
            paused_apps, failures = coordinator.pause_hubs([(client, "primary")])

        mock_pause.assert_not_called()
        assert failures == 0
        assert paused_apps[0]["pause_applied"] is True
        assert "pause_state" not in paused_apps[0]


@pytest.mark.unit
class TestDryRun:
    """Dry-run behavior records apps but marks pause_applied=False."""

    def test_dry_run_records_would_pause(self):
        state = _make_state_manager({"argocd_run_id": None, "argocd_paused_apps": []})
        client = Mock()
        app = _make_app("argocd", "app-1")

        with (
            patch(
                "lib.argocd_coordinator.argocd_lib.detect_argocd_installation",
                return_value=_discovery_with_crd(),
            ),
            patch(
                "lib.argocd_coordinator.argocd_lib.list_argocd_applications",
                return_value=[app],
            ),
            patch(
                "lib.argocd_coordinator.argocd_lib.find_acm_touching_apps",
                return_value=[_make_impact(app)],
            ),
            patch("lib.argocd_coordinator.argocd_lib.pause_autosync") as mock_pause,
        ):
            mock_pause.return_value = argocd_lib.PauseResult(
                namespace="argocd",
                name="app-1",
                original_sync_policy={"automated": {}},
                patched=True,
            )
            coordinator = ArgoCDPauseCoordinator(state, dry_run=True)
            paused_apps, failures = coordinator.pause_hubs([(client, "primary")])

        assert failures == 0
        assert len(paused_apps) == 1
        assert paused_apps[0]["pause_applied"] is False
        assert paused_apps[0]["dry_run"] is True
        assert state._config["argocd_pause_dry_run"] is True


@pytest.mark.unit
class TestErrorHandling:
    """API failures increment failure_count instead of raising."""

    def test_applicationset_child_blocks_without_patching_any_apps(self):
        state = _make_state_manager({"argocd_run_id": None, "argocd_paused_apps": []})
        client = Mock()
        child_app = _make_app("argocd", "child-app")
        safe_app = _make_app("argocd", "safe-app")
        blocker = argocd_lib.ArgocdPauseBlocker(
            namespace="argocd",
            name="child-app",
            reason=argocd_lib.PAUSE_BLOCK_REASON_APPLICATIONSET_MANAGED,
            message="Application argocd/child-app is managed by ApplicationSet parent-set; pause/update the ApplicationSet.",
        )

        with (
            patch(
                "lib.argocd_coordinator.argocd_lib.detect_argocd_installation",
                return_value=_discovery_with_crd(),
            ),
            patch(
                "lib.argocd_coordinator.argocd_lib.list_argocd_applications",
                return_value=[child_app, safe_app],
            ),
            patch(
                "lib.argocd_coordinator.argocd_lib.find_argocd_pause_blockers",
                return_value=[blocker],
            ),
            patch("lib.argocd_coordinator.argocd_lib.find_acm_touching_apps") as mock_find_acm,
            patch("lib.argocd_coordinator.argocd_lib.pause_autosync") as mock_pause,
        ):
            coordinator = ArgoCDPauseCoordinator(state, dry_run=False)
            paused_apps, failures = coordinator.pause_hubs([(client, "primary")])

        assert failures == 1
        assert paused_apps == []
        mock_find_acm.assert_not_called()
        mock_pause.assert_not_called()

    def test_patch_failure_increments_failure_count(self):
        state = _make_state_manager({"argocd_run_id": None, "argocd_paused_apps": []})
        client = Mock()
        app = _make_app("argocd", "app-1")

        with (
            patch(
                "lib.argocd_coordinator.argocd_lib.detect_argocd_installation",
                return_value=_discovery_with_crd(),
            ),
            patch(
                "lib.argocd_coordinator.argocd_lib.list_argocd_applications",
                return_value=[app],
            ),
            patch(
                "lib.argocd_coordinator.argocd_lib.find_acm_touching_apps",
                return_value=[_make_impact(app)],
            ),
            patch("lib.argocd_coordinator.argocd_lib.pause_autosync") as mock_pause,
        ):
            mock_pause.return_value = argocd_lib.PauseResult(
                namespace="argocd",
                name="app-1",
                original_sync_policy={"automated": {}},
                patched=False,
                error="403 Forbidden",
            )
            coordinator = ArgoCDPauseCoordinator(state, dry_run=False)
            paused_apps, failures = coordinator.pause_hubs([(client, "primary")])

        assert failures == 1
        # Failed entry should be removed from paused_apps
        assert paused_apps == []

    def test_verification_failure_preserves_pause_state_for_resume(self):
        state = _make_state_manager({"argocd_run_id": None, "argocd_paused_apps": []})
        client = Mock()
        app = _make_app("argocd", "app-1")

        with (
            patch(
                "lib.argocd_coordinator.argocd_lib.detect_argocd_installation",
                return_value=_discovery_with_crd(),
            ),
            patch(
                "lib.argocd_coordinator.argocd_lib.list_argocd_applications",
                return_value=[app],
            ),
            patch(
                "lib.argocd_coordinator.argocd_lib.find_acm_touching_apps",
                return_value=[_make_impact(app)],
            ),
            patch("lib.argocd_coordinator.argocd_lib.pause_autosync") as mock_pause,
        ):
            mock_pause.return_value = argocd_lib.PauseResult(
                namespace="argocd",
                name="app-1",
                original_sync_policy={"automated": {}},
                patched=False,
                patch_applied=True,
                error="pause verification failed: 500 Internal Server Error",
            )
            coordinator = ArgoCDPauseCoordinator(state, dry_run=False)
            paused_apps, failures = coordinator.pause_hubs([(client, "primary")])

        assert failures == 1
        assert paused_apps == [
            {
                "hub": "primary",
                "namespace": "argocd",
                "name": "app-1",
                "original_sync_policy": {"automated": {}},
                "pause_applied": True,
            }
        ]
        assert state._config["argocd_paused_apps"] == paused_apps

    def test_unknown_patch_state_persists_unconfirmed_entry(self):
        state = _make_state_manager({"argocd_run_id": "run-1", "argocd_paused_apps": []})
        client = Mock()
        app = _make_app("argocd", "app-1")

        with (
            patch(
                "lib.argocd_coordinator.argocd_lib.detect_argocd_installation",
                return_value=_discovery_with_crd(),
            ),
            patch(
                "lib.argocd_coordinator.argocd_lib.list_argocd_applications",
                return_value=[app],
            ),
            patch(
                "lib.argocd_coordinator.argocd_lib.find_acm_touching_apps",
                return_value=[_make_impact(app)],
            ),
            patch("lib.argocd_coordinator.argocd_lib.pause_autosync") as mock_pause,
        ):
            mock_pause.return_value = argocd_lib.PauseResult(
                namespace="argocd",
                name="app-1",
                original_sync_policy={"automated": {}},
                patched=False,
                patch_applied=None,
                error="patch state unknown",
            )
            coordinator = ArgoCDPauseCoordinator(state, dry_run=False)
            paused_apps, failures = coordinator.pause_hubs([(client, "primary")])

        assert failures == 1
        assert paused_apps == [
            {
                "hub": "primary",
                "namespace": "argocd",
                "name": "app-1",
                "original_sync_policy": {"automated": {}},
                "pause_applied": False,
                "pause_state": "unknown",
                "pause_run_id": "run-1",
            }
        ]
        assert state._config["argocd_paused_apps"] == paused_apps

    def test_detection_failure_propagates(self):
        """ArgoCD detection errors should propagate to the caller."""
        state = _make_state_manager({})
        client = Mock()

        with patch(
            "lib.argocd_coordinator.argocd_lib.detect_argocd_installation",
            side_effect=RuntimeError("API unreachable"),
        ):
            coordinator = ArgoCDPauseCoordinator(state, dry_run=False)
            with pytest.raises(RuntimeError, match="API unreachable"):
                coordinator.pause_hubs([(client, "secondary")])

    def test_list_failure_propagates(self):
        """Application listing errors should propagate to the caller."""
        state = _make_state_manager({"argocd_run_id": None, "argocd_paused_apps": []})
        client = Mock()

        with (
            patch(
                "lib.argocd_coordinator.argocd_lib.detect_argocd_installation",
                return_value=_discovery_with_crd(),
            ),
            patch(
                "lib.argocd_coordinator.argocd_lib.list_argocd_applications",
                side_effect=RuntimeError("list failed"),
            ),
        ):
            coordinator = ArgoCDPauseCoordinator(state, dry_run=False)
            with pytest.raises(RuntimeError, match="list failed"):
                coordinator.pause_hubs([(client, "primary")])

    def test_failed_retry_removes_stale_entry(self):
        """A failed pause attempt must clean up the provisional state entry."""
        state = _make_state_manager(
            {
                "argocd_run_id": "run-1",
                "argocd_paused_apps": [
                    {
                        "hub": "primary",
                        "namespace": "argocd",
                        "name": "app-1",
                        "original_sync_policy": {"automated": {}},
                        "pause_applied": False,
                    }
                ],
            }
        )
        client = Mock()
        # App still has automated (the previous pause didn't actually apply)
        app = _make_app("argocd", "app-1", automated=True)

        with (
            patch(
                "lib.argocd_coordinator.argocd_lib.detect_argocd_installation",
                return_value=_discovery_with_crd(),
            ),
            patch(
                "lib.argocd_coordinator.argocd_lib.list_argocd_applications",
                return_value=[app],
            ),
            patch(
                "lib.argocd_coordinator.argocd_lib.find_acm_touching_apps",
                return_value=[_make_impact(app)],
            ),
            patch("lib.argocd_coordinator.argocd_lib.pause_autosync") as mock_pause,
        ):
            mock_pause.return_value = argocd_lib.PauseResult(
                namespace="argocd",
                name="app-1",
                original_sync_policy={"automated": {}},
                patched=False,
                error="patch failed",
            )
            coordinator = ArgoCDPauseCoordinator(state, dry_run=False)
            paused_apps, failures = coordinator.pause_hubs([(client, "primary")])

        assert failures == 1
        assert paused_apps == []
        assert state._config["argocd_paused_apps"] == []


@pytest.mark.unit
class TestStatePersistence:
    """Verify state keys (argocd_paused_apps, argocd_run_id) are persisted correctly."""

    def test_run_id_persisted(self):
        state = _make_state_manager({"argocd_run_id": None, "argocd_paused_apps": []})
        client = Mock()

        with (
            patch(
                "lib.argocd_coordinator.argocd_lib.detect_argocd_installation",
                return_value=_discovery_with_crd(),
            ),
            patch(
                "lib.argocd_coordinator.argocd_lib.list_argocd_applications",
                return_value=[],
            ),
            patch(
                "lib.argocd_coordinator.argocd_lib.find_acm_touching_apps",
                return_value=[],
            ),
        ):
            coordinator = ArgoCDPauseCoordinator(state, dry_run=False)
            coordinator.pause_hubs([(client, "primary")])

        run_id = state._config["argocd_run_id"]
        assert run_id is not None
        assert len(run_id) == 12

    def test_existing_run_id_preserved(self):
        state = _make_state_manager({"argocd_run_id": "existing-run", "argocd_paused_apps": []})
        client = Mock()

        with (
            patch(
                "lib.argocd_coordinator.argocd_lib.detect_argocd_installation",
                return_value=_discovery_with_crd(),
            ),
            patch(
                "lib.argocd_coordinator.argocd_lib.list_argocd_applications",
                return_value=[],
            ),
            patch(
                "lib.argocd_coordinator.argocd_lib.find_acm_touching_apps",
                return_value=[],
            ),
        ):
            coordinator = ArgoCDPauseCoordinator(state, dry_run=False)
            coordinator.pause_hubs([(client, "primary")])

        assert state._config["argocd_run_id"] == "existing-run"

    def test_incremental_persist_per_app(self):
        """Each app must be individually persisted to survive mid-loop crashes."""
        state = _make_state_manager({"argocd_run_id": None, "argocd_paused_apps": []})
        client = Mock()
        app1 = _make_app("argocd", "app-1")
        app2 = _make_app("argocd", "app-2")

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
                "lib.argocd_coordinator.argocd_lib.detect_argocd_installation",
                return_value=_discovery_with_crd(),
            ),
            patch(
                "lib.argocd_coordinator.argocd_lib.list_argocd_applications",
                return_value=[app1, app2],
            ),
            patch(
                "lib.argocd_coordinator.argocd_lib.find_acm_touching_apps",
                return_value=[_make_impact(app1), _make_impact(app2)],
            ),
            patch(
                "lib.argocd_coordinator.argocd_lib.pause_autosync",
                side_effect=pause_side_effect,
            ),
        ):
            coordinator = ArgoCDPauseCoordinator(state, dry_run=False)
            paused_apps, failures = coordinator.pause_hubs([(client, "primary")])

        assert failures == 0
        assert len(paused_apps) == 2

        # Verify set_config was called multiple times (provisional + confirmed for each app)
        paused_calls = [call for call in state.set_config.call_args_list if call.args[0] == "argocd_paused_apps"]
        # 2 apps × 2 persists each (provisional + confirmed) = 4
        assert len(paused_calls) == 4

        # Verify each call got a distinct copy (not the same mutable reference)
        refs = [id(call.args[1]) for call in paused_calls]
        assert len(set(refs)) == len(refs)


@pytest.mark.unit
class TestDiscoveryNamespaceScope:
    """Scoped Argo CD discovery reuse within the same pause run."""

    def test_first_pass_uses_cluster_wide_listing(self):
        state = _make_state_manager({"argocd_run_id": None, "argocd_paused_apps": []})
        client = Mock()

        with (
            patch(
                "lib.argocd_coordinator.argocd_lib.detect_argocd_installation",
                return_value=_discovery_with_crd(),
            ),
            patch(
                "lib.argocd_coordinator.argocd_lib.list_argocd_applications",
                return_value=[],
            ) as mock_list,
            patch(
                "lib.argocd_coordinator.argocd_lib.find_acm_touching_apps",
                return_value=[],
            ),
        ):
            coordinator = ArgoCDPauseCoordinator(state, dry_run=False)
            coordinator.pause_hubs([(client, "primary")])

        mock_list.assert_called_once_with(client, namespaces=None)

    def test_first_pass_records_discovery_namespaces_before_pause(self):
        state = _make_state_manager({"argocd_run_id": None, "argocd_paused_apps": []})
        client = Mock()
        app_argocd = _make_app("argocd", "app-1")
        app_team = _make_app("team-gitops", "app-2")

        with (
            patch(
                "lib.argocd_coordinator.argocd_lib.detect_argocd_installation",
                return_value=_discovery_with_crd(),
            ),
            patch(
                "lib.argocd_coordinator.argocd_lib.list_argocd_applications",
                return_value=[app_argocd, app_team],
            ),
            patch(
                "lib.argocd_coordinator.argocd_lib.find_acm_touching_apps",
                return_value=[],
            ),
        ):
            coordinator = ArgoCDPauseCoordinator(state, dry_run=False)
            coordinator.pause_hubs([(client, "primary")])

        assert state._config["argocd_discovery_namespaces"] == {
            "primary": ["argocd", "team-gitops"],
        }

    def test_retry_pass_uses_recorded_namespace_set(self):
        state = _make_state_manager(
            {
                "argocd_run_id": "run-1",
                "argocd_paused_apps": [],
                "argocd_discovery_namespaces": {"primary": ["argocd", "team-gitops"]},
            }
        )
        client = Mock()
        app = _make_app("argocd", "app-1")

        with (
            patch(
                "lib.argocd_coordinator.argocd_lib.detect_argocd_installation",
                return_value=_discovery_with_crd(),
            ),
            patch(
                "lib.argocd_coordinator.argocd_lib.list_argocd_applications",
                return_value=[app],
            ) as mock_list,
            patch(
                "lib.argocd_coordinator.argocd_lib.find_acm_touching_apps",
                return_value=[],
            ),
        ):
            coordinator = ArgoCDPauseCoordinator(state, dry_run=False)
            coordinator.pause_hubs([(client, "primary")])

        mock_list.assert_called_once_with(client, namespaces=["argocd", "team-gitops"])

    def test_empty_recorded_namespace_list_falls_back_to_cluster_wide(self):
        state = _make_state_manager(
            {
                "argocd_run_id": "run-1",
                "argocd_paused_apps": [],
                "argocd_discovery_namespaces": {"primary": []},
            }
        )
        client = Mock()

        with (
            patch(
                "lib.argocd_coordinator.argocd_lib.detect_argocd_installation",
                return_value=_discovery_with_crd(),
            ),
            patch(
                "lib.argocd_coordinator.argocd_lib.list_argocd_applications",
                return_value=[],
            ) as mock_list,
            patch(
                "lib.argocd_coordinator.argocd_lib.find_acm_touching_apps",
                return_value=[],
            ),
        ):
            coordinator = ArgoCDPauseCoordinator(state, dry_run=False)
            coordinator.pause_hubs([(client, "primary")])

        mock_list.assert_called_once_with(client, namespaces=None)

    def test_no_crd_clears_discovery_namespaces(self):
        state = _make_state_manager(
            {
                "argocd_run_id": "stale",
                "argocd_paused_apps": [{"hub": "secondary", "name": "old"}],
                "argocd_discovery_namespaces": {"secondary": ["argocd"]},
            }
        )
        client = Mock()

        with patch(
            "lib.argocd_coordinator.argocd_lib.detect_argocd_installation",
            return_value=_discovery_without_crd(),
        ):
            coordinator = ArgoCDPauseCoordinator(state, dry_run=False)
            coordinator.pause_hubs([(client, "secondary")])

        assert state._config["argocd_discovery_namespaces"] == {}

    def test_operator_instance_namespaces_are_not_used_as_scope_hints(self):
        """Watched Application namespaces must come from discovery, not operator CRDs."""
        state = _make_state_manager({"argocd_run_id": None, "argocd_paused_apps": []})
        client = Mock()
        operator_discovery = argocd_lib.ArgocdDiscoveryResult(
            has_applications_crd=True,
            has_argocds_crd=True,
            install_type="operator",
            argocd_instances=[{"namespace": "openshift-gitops", "name": "openshift-gitops"}],
        )
        watched_app = _make_app("team-gitops", "watched-app")

        with (
            patch(
                "lib.argocd_coordinator.argocd_lib.detect_argocd_installation",
                return_value=operator_discovery,
            ),
            patch(
                "lib.argocd_coordinator.argocd_lib.list_argocd_applications",
                return_value=[watched_app],
            ) as mock_list,
            patch(
                "lib.argocd_coordinator.argocd_lib.find_acm_touching_apps",
                return_value=[],
            ),
        ):
            coordinator = ArgoCDPauseCoordinator(state, dry_run=False)
            coordinator.pause_hubs([(client, "primary")])

        mock_list.assert_called_once_with(client, namespaces=None)
        assert state._config["argocd_discovery_namespaces"] == {"primary": ["team-gitops"]}

    def test_malformed_recorded_namespace_value_falls_back_to_cluster_wide(self):
        state = _make_state_manager(
            {
                "argocd_run_id": "run-1",
                "argocd_paused_apps": [],
                "argocd_discovery_namespaces": {"primary": "argocd"},
            }
        )
        client = Mock()

        with (
            patch(
                "lib.argocd_coordinator.argocd_lib.detect_argocd_installation",
                return_value=_discovery_with_crd(),
            ),
            patch(
                "lib.argocd_coordinator.argocd_lib.list_argocd_applications",
                return_value=[],
            ) as mock_list,
            patch(
                "lib.argocd_coordinator.argocd_lib.find_acm_touching_apps",
                return_value=[],
            ),
        ):
            coordinator = ArgoCDPauseCoordinator(state, dry_run=False)
            coordinator.pause_hubs([(client, "primary")])

        mock_list.assert_called_once_with(client, namespaces=None)


@pytest.mark.unit
class TestClearArgocdPauseState:
    """Shared Argo CD pause-state reset helper."""

    def test_clear_argocd_pause_state_clears_all_keys(self):
        from lib.argocd_coordinator import clear_argocd_pause_state

        state = _make_state_manager(
            {
                "argocd_run_id": "run-1",
                "argocd_paused_apps": [{"hub": "primary", "namespace": "argocd", "name": "app-1"}],
                "argocd_pause_dry_run": True,
                "argocd_discovery_namespaces": {"primary": ["argocd"]},
            }
        )

        clear_argocd_pause_state(state)

        assert state._config["argocd_paused_apps"] == []
        assert state._config["argocd_run_id"] is None
        assert state._config["argocd_pause_dry_run"] is False
        assert state._config["argocd_discovery_namespaces"] == {}
