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


def _make_app(namespace, name, *, automated=True, resources=None):
    """Build a minimal Argo CD Application dict."""
    sync_policy = {"automated": {}} if automated else {}
    if resources is None:
        resources = [{"kind": "BackupSchedule", "namespace": "open-cluster-management-backup"}]
    return {
        "metadata": {"namespace": namespace, "name": name},
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
            patch("lib.argocd_coordinator.argocd_lib.detect_argocd_installation", return_value=_discovery_with_crd()),
            patch("lib.argocd_coordinator.argocd_lib.list_argocd_applications", return_value=[app]),
            patch("lib.argocd_coordinator.argocd_lib.find_acm_touching_apps", return_value=[impact]),
            patch("lib.argocd_coordinator.argocd_lib.pause_autosync") as mock_pause,
        ):
            mock_pause.return_value = argocd_lib.PauseResult(
                namespace="argocd", name="app-1", original_sync_policy={"automated": {}}, patched=True
            )
            coordinator = ArgoCDPauseCoordinator(state, dry_run=False)
            paused_apps, failures = coordinator.pause_hubs([(client, "secondary")])

        assert failures == 0
        assert len(paused_apps) == 1
        assert paused_apps[0]["hub"] == "secondary"
        assert paused_apps[0]["name"] == "app-1"
        assert paused_apps[0]["pause_applied"] is True

    def test_no_crd_clears_state(self):
        state = _make_state_manager({
            "argocd_run_id": "stale",
            "argocd_paused_apps": [{"hub": "secondary", "name": "old"}],
        })
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
            patch("lib.argocd_coordinator.argocd_lib.detect_argocd_installation", return_value=_discovery_with_crd()),
            patch("lib.argocd_coordinator.argocd_lib.list_argocd_applications", return_value=[app]),
            patch("lib.argocd_coordinator.argocd_lib.find_acm_touching_apps", return_value=[impact]),
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
                namespace="argocd", name=name, original_sync_policy={"automated": {}}, patched=True
            )

        with (
            patch("lib.argocd_coordinator.argocd_lib.detect_argocd_installation", side_effect=detect_side_effect),
            patch("lib.argocd_coordinator.argocd_lib.list_argocd_applications", side_effect=list_side_effect),
            patch("lib.argocd_coordinator.argocd_lib.find_acm_touching_apps", side_effect=filter_side_effect),
            patch("lib.argocd_coordinator.argocd_lib.pause_autosync", side_effect=pause_side_effect),
        ):
            coordinator = ArgoCDPauseCoordinator(state, dry_run=False)
            paused_apps, failures = coordinator.pause_hubs([
                (primary_client, "primary"),
                (secondary_client, "secondary"),
            ])

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
            patch("lib.argocd_coordinator.argocd_lib.detect_argocd_installation", side_effect=detect_side_effect),
            patch("lib.argocd_coordinator.argocd_lib.list_argocd_applications", return_value=[app]),
            patch("lib.argocd_coordinator.argocd_lib.find_acm_touching_apps", return_value=[_make_impact(app)]),
            patch("lib.argocd_coordinator.argocd_lib.pause_autosync") as mock_pause,
        ):
            mock_pause.return_value = argocd_lib.PauseResult(
                namespace="argocd", name="app-1", original_sync_policy={"automated": {}}, patched=True
            )
            coordinator = ArgoCDPauseCoordinator(state, dry_run=False)
            paused_apps, failures = coordinator.pause_hubs([
                (primary_client, "primary"),
                (secondary_client, "secondary"),
            ])

        assert failures == 0
        assert len(paused_apps) == 1
        assert paused_apps[0]["hub"] == "primary"


@pytest.mark.unit
class TestIdempotentRepause:
    """Clobber guard and entry recovery via the coordinator."""

    def test_already_paused_and_recorded_is_skipped(self):
        """An app already paused and confirmed in state must not be re-paused."""
        state = _make_state_manager({
            "argocd_run_id": "run-1",
            "argocd_paused_apps": [{
                "hub": "primary",
                "namespace": "argocd",
                "name": "app-1",
                "original_sync_policy": {"automated": {"prune": True}},
                "pause_applied": True,
            }],
        })
        client = Mock()
        # App is already paused (no automated in syncPolicy)
        app = _make_app("argocd", "app-1", automated=False)

        with (
            patch("lib.argocd_coordinator.argocd_lib.detect_argocd_installation", return_value=_discovery_with_crd()),
            patch("lib.argocd_coordinator.argocd_lib.list_argocd_applications", return_value=[app]),
            patch("lib.argocd_coordinator.argocd_lib.find_acm_touching_apps", return_value=[_make_impact(app)]),
            patch("lib.argocd_coordinator.argocd_lib.pause_autosync") as mock_pause,
        ):
            coordinator = ArgoCDPauseCoordinator(state, dry_run=False)
            paused_apps, failures = coordinator.pause_hubs([(client, "primary")])

        mock_pause.assert_not_called()
        assert failures == 0
        assert len(paused_apps) == 1
        assert paused_apps[0]["pause_applied"] is True

    def test_recovers_pending_entry_when_app_already_paused(self):
        """Entry with pause_applied=False should be confirmed when live app lacks automated sync."""
        state = _make_state_manager({
            "argocd_run_id": "run-1",
            "argocd_paused_apps": [{
                "hub": "primary",
                "namespace": "argocd",
                "name": "app-1",
                "original_sync_policy": {"automated": {"prune": True}},
                "pause_applied": False,
            }],
        })
        client = Mock()
        app = _make_app("argocd", "app-1", automated=False)

        with (
            patch("lib.argocd_coordinator.argocd_lib.detect_argocd_installation", return_value=_discovery_with_crd()),
            patch("lib.argocd_coordinator.argocd_lib.list_argocd_applications", return_value=[app]),
            patch("lib.argocd_coordinator.argocd_lib.find_acm_touching_apps", return_value=[_make_impact(app)]),
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


@pytest.mark.unit
class TestDryRun:
    """Dry-run behavior records apps but marks pause_applied=False."""

    def test_dry_run_records_would_pause(self):
        state = _make_state_manager({"argocd_run_id": None, "argocd_paused_apps": []})
        client = Mock()
        app = _make_app("argocd", "app-1")

        with (
            patch("lib.argocd_coordinator.argocd_lib.detect_argocd_installation", return_value=_discovery_with_crd()),
            patch("lib.argocd_coordinator.argocd_lib.list_argocd_applications", return_value=[app]),
            patch("lib.argocd_coordinator.argocd_lib.find_acm_touching_apps", return_value=[_make_impact(app)]),
            patch("lib.argocd_coordinator.argocd_lib.pause_autosync") as mock_pause,
        ):
            mock_pause.return_value = argocd_lib.PauseResult(
                namespace="argocd", name="app-1", original_sync_policy={"automated": {}}, patched=True
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

    def test_patch_failure_increments_failure_count(self):
        state = _make_state_manager({"argocd_run_id": None, "argocd_paused_apps": []})
        client = Mock()
        app = _make_app("argocd", "app-1")

        with (
            patch("lib.argocd_coordinator.argocd_lib.detect_argocd_installation", return_value=_discovery_with_crd()),
            patch("lib.argocd_coordinator.argocd_lib.list_argocd_applications", return_value=[app]),
            patch("lib.argocd_coordinator.argocd_lib.find_acm_touching_apps", return_value=[_make_impact(app)]),
            patch("lib.argocd_coordinator.argocd_lib.pause_autosync") as mock_pause,
        ):
            mock_pause.return_value = argocd_lib.PauseResult(
                namespace="argocd", name="app-1", original_sync_policy={"automated": {}},
                patched=False, error="403 Forbidden",
            )
            coordinator = ArgoCDPauseCoordinator(state, dry_run=False)
            paused_apps, failures = coordinator.pause_hubs([(client, "primary")])

        assert failures == 1
        # Failed entry should be removed from paused_apps
        assert paused_apps == []

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
            patch("lib.argocd_coordinator.argocd_lib.detect_argocd_installation", return_value=_discovery_with_crd()),
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
        state = _make_state_manager({
            "argocd_run_id": "run-1",
            "argocd_paused_apps": [{
                "hub": "primary",
                "namespace": "argocd",
                "name": "app-1",
                "original_sync_policy": {"automated": {}},
                "pause_applied": False,
            }],
        })
        client = Mock()
        # App still has automated (the previous pause didn't actually apply)
        app = _make_app("argocd", "app-1", automated=True)

        with (
            patch("lib.argocd_coordinator.argocd_lib.detect_argocd_installation", return_value=_discovery_with_crd()),
            patch("lib.argocd_coordinator.argocd_lib.list_argocd_applications", return_value=[app]),
            patch("lib.argocd_coordinator.argocd_lib.find_acm_touching_apps", return_value=[_make_impact(app)]),
            patch("lib.argocd_coordinator.argocd_lib.pause_autosync") as mock_pause,
        ):
            mock_pause.return_value = argocd_lib.PauseResult(
                namespace="argocd", name="app-1", original_sync_policy={"automated": {}},
                patched=False, error="patch failed",
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
            patch("lib.argocd_coordinator.argocd_lib.detect_argocd_installation", return_value=_discovery_with_crd()),
            patch("lib.argocd_coordinator.argocd_lib.list_argocd_applications", return_value=[]),
            patch("lib.argocd_coordinator.argocd_lib.find_acm_touching_apps", return_value=[]),
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
            patch("lib.argocd_coordinator.argocd_lib.detect_argocd_installation", return_value=_discovery_with_crd()),
            patch("lib.argocd_coordinator.argocd_lib.list_argocd_applications", return_value=[]),
            patch("lib.argocd_coordinator.argocd_lib.find_acm_touching_apps", return_value=[]),
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
                namespace="argocd", name=name, original_sync_policy={"automated": {}}, patched=True
            )

        with (
            patch("lib.argocd_coordinator.argocd_lib.detect_argocd_installation", return_value=_discovery_with_crd()),
            patch("lib.argocd_coordinator.argocd_lib.list_argocd_applications", return_value=[app1, app2]),
            patch(
                "lib.argocd_coordinator.argocd_lib.find_acm_touching_apps",
                return_value=[_make_impact(app1), _make_impact(app2)],
            ),
            patch("lib.argocd_coordinator.argocd_lib.pause_autosync", side_effect=pause_side_effect),
        ):
            coordinator = ArgoCDPauseCoordinator(state, dry_run=False)
            paused_apps, failures = coordinator.pause_hubs([(client, "primary")])

        assert failures == 0
        assert len(paused_apps) == 2

        # Verify set_config was called multiple times (provisional + confirmed for each app)
        paused_calls = [
            call for call in state.set_config.call_args_list if call.args[0] == "argocd_paused_apps"
        ]
        # 2 apps × 2 persists each (provisional + confirmed) = 4
        assert len(paused_calls) == 4

        # Verify each call got a distinct copy (not the same mutable reference)
        refs = [id(call.args[1]) for call in paused_calls]
        assert len(set(refs)) == len(refs)
