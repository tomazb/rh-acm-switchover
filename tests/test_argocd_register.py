"""Unit tests for lib/argocd_register.py.

Tests cover ArgocdPauseRegister: hub detection, pause execution,
entry recovery, clobber guard, dry-run, error handling, state persistence,
and the pause-register invariant (ADR-0001): entries == currently paused apps,
resume removes entries on success, dry-run records nothing.
"""

import copy
from unittest.mock import Mock, patch

import pytest

from lib import argocd as argocd_lib
from lib.argocd_register import ArgocdPauseRegister, RegisterStatus
from lib.utils import StateManager


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
                "lib.argocd_register.argocd_lib.detect_argocd_installation",
                return_value=_discovery_with_crd(),
            ),
            patch(
                "lib.argocd_register.argocd_lib.list_argocd_applications",
                return_value=[app],
            ),
            patch(
                "lib.argocd_register.argocd_lib.find_acm_touching_apps",
                return_value=[impact],
            ),
            patch("lib.argocd_register.argocd_lib.pause_autosync") as mock_pause,
        ):
            mock_pause.return_value = argocd_lib.PauseResult(
                namespace="argocd",
                name="app-1",
                original_sync_policy={"automated": {}},
                patched=True,
            )
            coordinator = ArgocdPauseRegister(state, dry_run=False)
            summary = coordinator.pause_hubs([(client, "secondary")])

        assert summary.failed == 0
        assert summary.newly_paused == 1
        entries = state._config["argocd_paused_apps"]
        assert len(entries) == 1
        assert entries[0]["hub"] == "secondary"
        assert entries[0]["name"] == "app-1"
        assert entries[0]["pause_applied"] is True

    def test_no_crd_preserves_legacy_entry_without_pause_applied(self):
        """Legacy entries (no pause_applied flag) count as applied; CRD loss must not clobber them (ADR-0001)."""
        state = _make_state_manager(
            {
                "argocd_run_id": "run-1",
                "argocd_paused_apps": [{"hub": "secondary", "name": "old"}],
            }
        )
        client = Mock()

        with patch(
            "lib.argocd_register.argocd_lib.detect_argocd_installation",
            return_value=_discovery_without_crd(),
        ):
            coordinator = ArgocdPauseRegister(state, dry_run=False)
            summary = coordinator.pause_hubs([(client, "secondary")])

        assert summary.newly_paused == 0
        assert summary.failed == 0
        assert summary.applications_crd_visible is False
        assert state._config["argocd_paused_apps"] == [{"hub": "secondary", "name": "old"}]
        assert state._config["argocd_run_id"] == "run-1"

    def test_skips_app_without_autosync(self):
        state = _make_state_manager({"argocd_run_id": None, "argocd_paused_apps": []})
        client = Mock()
        app = _make_app("argocd", "static-app", automated=False)
        impact = _make_impact(app)

        with (
            patch(
                "lib.argocd_register.argocd_lib.detect_argocd_installation",
                return_value=_discovery_with_crd(),
            ),
            patch(
                "lib.argocd_register.argocd_lib.list_argocd_applications",
                return_value=[app],
            ),
            patch(
                "lib.argocd_register.argocd_lib.find_acm_touching_apps",
                return_value=[impact],
            ),
            patch("lib.argocd_register.argocd_lib.pause_autosync") as mock_pause,
        ):
            coordinator = ArgocdPauseRegister(state, dry_run=False)
            summary = coordinator.pause_hubs([(client, "secondary")])

        mock_pause.assert_not_called()
        assert summary.newly_paused == 0
        assert summary.failed == 0
        assert state._config["argocd_paused_apps"] == []


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
                "lib.argocd_register.argocd_lib.detect_argocd_installation",
                side_effect=detect_side_effect,
            ),
            patch(
                "lib.argocd_register.argocd_lib.list_argocd_applications",
                side_effect=list_side_effect,
            ),
            patch(
                "lib.argocd_register.argocd_lib.find_acm_touching_apps",
                side_effect=filter_side_effect,
            ),
            patch(
                "lib.argocd_register.argocd_lib.pause_autosync",
                side_effect=pause_side_effect,
            ),
        ):
            coordinator = ArgocdPauseRegister(state, dry_run=False)
            summary = coordinator.pause_hubs(
                [
                    (primary_client, "primary"),
                    (secondary_client, "secondary"),
                ]
            )

        assert summary.failed == 0
        assert summary.newly_paused == 2
        entries = state._config["argocd_paused_apps"]
        assert len(entries) == 2
        assert {e["hub"] for e in entries} == {"primary", "secondary"}

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
                "lib.argocd_register.argocd_lib.detect_argocd_installation",
                side_effect=detect_side_effect,
            ),
            patch(
                "lib.argocd_register.argocd_lib.list_argocd_applications",
                return_value=[app],
            ),
            patch(
                "lib.argocd_register.argocd_lib.find_acm_touching_apps",
                return_value=[_make_impact(app)],
            ),
            patch("lib.argocd_register.argocd_lib.pause_autosync") as mock_pause,
        ):
            mock_pause.return_value = argocd_lib.PauseResult(
                namespace="argocd",
                name="app-1",
                original_sync_policy={"automated": {}},
                patched=True,
            )
            coordinator = ArgocdPauseRegister(state, dry_run=False)
            summary = coordinator.pause_hubs(
                [
                    (primary_client, "primary"),
                    (secondary_client, "secondary"),
                ]
            )

        assert summary.failed == 0
        assert summary.newly_paused == 1
        entries = state._config["argocd_paused_apps"]
        assert len(entries) == 1
        assert entries[0]["hub"] == "primary"


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
                "lib.argocd_register.argocd_lib.detect_argocd_installation",
                return_value=_discovery_with_crd(),
            ),
            patch(
                "lib.argocd_register.argocd_lib.list_argocd_applications",
                return_value=[app],
            ),
            patch(
                "lib.argocd_register.argocd_lib.find_acm_touching_apps",
                return_value=[_make_impact(app)],
            ),
            patch("lib.argocd_register.argocd_lib.pause_autosync") as mock_pause,
        ):
            coordinator = ArgocdPauseRegister(state, dry_run=False)
            summary = coordinator.pause_hubs([(client, "primary")])

        mock_pause.assert_not_called()
        assert summary.failed == 0
        assert summary.already_paused == 1
        assert summary.newly_paused == 0
        entries = state._config["argocd_paused_apps"]
        assert len(entries) == 1
        assert entries[0]["pause_applied"] is True

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
                "lib.argocd_register.argocd_lib.detect_argocd_installation",
                return_value=_discovery_with_crd(),
            ),
            patch(
                "lib.argocd_register.argocd_lib.list_argocd_applications",
                return_value=[app],
            ),
            patch(
                "lib.argocd_register.argocd_lib.find_acm_touching_apps",
                return_value=[_make_impact(app)],
            ),
            patch("lib.argocd_register.argocd_lib.pause_autosync") as mock_pause,
        ):
            coordinator = ArgocdPauseRegister(state, dry_run=False)
            summary = coordinator.pause_hubs([(client, "primary")])

        mock_pause.assert_not_called()
        assert summary.failed == 0
        assert summary.recovered == 1
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
                "lib.argocd_register.argocd_lib.detect_argocd_installation",
                return_value=_discovery_with_crd(),
            ),
            patch(
                "lib.argocd_register.argocd_lib.list_argocd_applications",
                return_value=[app],
            ),
            patch(
                "lib.argocd_register.argocd_lib.find_acm_touching_apps",
                return_value=[_make_impact(app)],
            ),
            patch("lib.argocd_register.argocd_lib.pause_autosync") as mock_pause,
        ):
            coordinator = ArgocdPauseRegister(state, dry_run=False)
            summary = coordinator.pause_hubs([(client, "primary")])

        mock_pause.assert_not_called()
        assert summary.failed == 0
        assert summary.recovered == 0
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
                "lib.argocd_register.argocd_lib.detect_argocd_installation",
                return_value=_discovery_with_crd(),
            ),
            patch(
                "lib.argocd_register.argocd_lib.list_argocd_applications",
                return_value=[app],
            ),
            patch(
                "lib.argocd_register.argocd_lib.find_acm_touching_apps",
                return_value=[_make_impact(app)],
            ),
            patch("lib.argocd_register.argocd_lib.pause_autosync") as mock_pause,
        ):
            coordinator = ArgocdPauseRegister(state, dry_run=False)
            summary = coordinator.pause_hubs([(client, "primary")])

        mock_pause.assert_not_called()
        assert summary.failed == 0
        assert summary.recovered == 1
        persisted = state._config["argocd_paused_apps"]
        assert persisted[0]["pause_applied"] is True
        assert "pause_state" not in persisted[0]


@pytest.mark.unit
class TestDryRun:
    """Dry-run reports the would-pause list but records nothing (ADR-0001)."""

    def test_dry_run_reports_would_pause_without_recording(self):
        state = _make_state_manager({"argocd_run_id": None, "argocd_paused_apps": []})
        client = Mock()
        app = _make_app("argocd", "app-1")

        with (
            patch(
                "lib.argocd_register.argocd_lib.detect_argocd_installation",
                return_value=_discovery_with_crd(),
            ),
            patch(
                "lib.argocd_register.argocd_lib.list_argocd_applications",
                return_value=[app],
            ),
            patch(
                "lib.argocd_register.argocd_lib.find_acm_touching_apps",
                return_value=[_make_impact(app)],
            ),
            patch("lib.argocd_register.argocd_lib.pause_autosync") as mock_pause,
        ):
            mock_pause.return_value = argocd_lib.PauseResult(
                namespace="argocd",
                name="app-1",
                original_sync_policy={"automated": {}},
                patched=True,
            )
            coordinator = ArgocdPauseRegister(state, dry_run=True)
            summary = coordinator.pause_hubs([(client, "primary")])

        assert summary.failed == 0
        assert summary.newly_paused == 1
        state.set_config.assert_not_called()


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
                "lib.argocd_register.argocd_lib.detect_argocd_installation",
                return_value=_discovery_with_crd(),
            ),
            patch(
                "lib.argocd_register.argocd_lib.list_argocd_applications",
                return_value=[child_app, safe_app],
            ),
            patch(
                "lib.argocd_register.argocd_lib.find_argocd_pause_blockers",
                return_value=[blocker],
            ),
            patch("lib.argocd_register.argocd_lib.find_acm_touching_apps") as mock_find_acm,
            patch("lib.argocd_register.argocd_lib.pause_autosync") as mock_pause,
        ):
            coordinator = ArgocdPauseRegister(state, dry_run=False)
            summary = coordinator.pause_hubs([(client, "primary")])

        assert summary.blocked == 1
        assert summary.failed == 0
        assert summary.newly_paused == 0
        mock_find_acm.assert_not_called()
        mock_pause.assert_not_called()

    def test_patch_failure_increments_failure_count(self):
        state = _make_state_manager({"argocd_run_id": None, "argocd_paused_apps": []})
        client = Mock()
        app = _make_app("argocd", "app-1")

        with (
            patch(
                "lib.argocd_register.argocd_lib.detect_argocd_installation",
                return_value=_discovery_with_crd(),
            ),
            patch(
                "lib.argocd_register.argocd_lib.list_argocd_applications",
                return_value=[app],
            ),
            patch(
                "lib.argocd_register.argocd_lib.find_acm_touching_apps",
                return_value=[_make_impact(app)],
            ),
            patch("lib.argocd_register.argocd_lib.pause_autosync") as mock_pause,
        ):
            mock_pause.return_value = argocd_lib.PauseResult(
                namespace="argocd",
                name="app-1",
                original_sync_policy={"automated": {}},
                patched=False,
                error="403 Forbidden",
            )
            coordinator = ArgocdPauseRegister(state, dry_run=False)
            summary = coordinator.pause_hubs([(client, "primary")])

        assert summary.failed == 1
        assert summary.newly_paused == 0
        # Failed entry should be removed from the register
        assert state._config["argocd_paused_apps"] == []

    def test_verification_failure_preserves_pause_state_for_resume(self):
        state = _make_state_manager({"argocd_run_id": None, "argocd_paused_apps": []})
        client = Mock()
        app = _make_app("argocd", "app-1")

        with (
            patch(
                "lib.argocd_register.argocd_lib.detect_argocd_installation",
                return_value=_discovery_with_crd(),
            ),
            patch(
                "lib.argocd_register.argocd_lib.list_argocd_applications",
                return_value=[app],
            ),
            patch(
                "lib.argocd_register.argocd_lib.find_acm_touching_apps",
                return_value=[_make_impact(app)],
            ),
            patch("lib.argocd_register.argocd_lib.pause_autosync") as mock_pause,
        ):
            mock_pause.return_value = argocd_lib.PauseResult(
                namespace="argocd",
                name="app-1",
                original_sync_policy={"automated": {}},
                patched=False,
                patch_applied=True,
                error="pause verification failed: 500 Internal Server Error",
            )
            coordinator = ArgocdPauseRegister(state, dry_run=False)
            summary = coordinator.pause_hubs([(client, "primary")])

        assert summary.failed == 1
        assert state._config["argocd_paused_apps"] == [
            {
                "hub": "primary",
                "namespace": "argocd",
                "name": "app-1",
                "original_sync_policy": {"automated": {}},
                "pause_applied": True,
            }
        ]

    def test_unknown_patch_state_persists_unconfirmed_entry(self):
        state = _make_state_manager({"argocd_run_id": "run-1", "argocd_paused_apps": []})
        client = Mock()
        app = _make_app("argocd", "app-1")

        with (
            patch(
                "lib.argocd_register.argocd_lib.detect_argocd_installation",
                return_value=_discovery_with_crd(),
            ),
            patch(
                "lib.argocd_register.argocd_lib.list_argocd_applications",
                return_value=[app],
            ),
            patch(
                "lib.argocd_register.argocd_lib.find_acm_touching_apps",
                return_value=[_make_impact(app)],
            ),
            patch("lib.argocd_register.argocd_lib.pause_autosync") as mock_pause,
        ):
            mock_pause.return_value = argocd_lib.PauseResult(
                namespace="argocd",
                name="app-1",
                original_sync_policy={"automated": {}},
                patched=False,
                patch_applied=None,
                error="patch state unknown",
            )
            coordinator = ArgocdPauseRegister(state, dry_run=False)
            summary = coordinator.pause_hubs([(client, "primary")])

        assert summary.failed == 1
        assert state._config["argocd_paused_apps"] == [
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

    def test_detection_failure_propagates(self):
        """ArgoCD detection errors should propagate to the caller."""
        state = _make_state_manager({})
        client = Mock()

        with patch(
            "lib.argocd_register.argocd_lib.detect_argocd_installation",
            side_effect=RuntimeError("API unreachable"),
        ):
            coordinator = ArgocdPauseRegister(state, dry_run=False)
            with pytest.raises(RuntimeError, match="API unreachable"):
                coordinator.pause_hubs([(client, "secondary")])

    def test_list_failure_propagates(self):
        """Application listing errors should propagate to the caller."""
        state = _make_state_manager({"argocd_run_id": None, "argocd_paused_apps": []})
        client = Mock()

        with (
            patch(
                "lib.argocd_register.argocd_lib.detect_argocd_installation",
                return_value=_discovery_with_crd(),
            ),
            patch(
                "lib.argocd_register.argocd_lib.list_argocd_applications",
                side_effect=RuntimeError("list failed"),
            ),
        ):
            coordinator = ArgocdPauseRegister(state, dry_run=False)
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
                "lib.argocd_register.argocd_lib.detect_argocd_installation",
                return_value=_discovery_with_crd(),
            ),
            patch(
                "lib.argocd_register.argocd_lib.list_argocd_applications",
                return_value=[app],
            ),
            patch(
                "lib.argocd_register.argocd_lib.find_acm_touching_apps",
                return_value=[_make_impact(app)],
            ),
            patch("lib.argocd_register.argocd_lib.pause_autosync") as mock_pause,
        ):
            mock_pause.return_value = argocd_lib.PauseResult(
                namespace="argocd",
                name="app-1",
                original_sync_policy={"automated": {}},
                patched=False,
                error="patch failed",
            )
            coordinator = ArgocdPauseRegister(state, dry_run=False)
            summary = coordinator.pause_hubs([(client, "primary")])

        assert summary.failed == 1
        assert state._config["argocd_paused_apps"] == []


@pytest.mark.unit
class TestStatePersistence:
    """Verify state keys (argocd_paused_apps, argocd_run_id) are persisted correctly."""

    def test_run_id_persisted(self):
        state = _make_state_manager({"argocd_run_id": None, "argocd_paused_apps": []})
        client = Mock()

        with (
            patch(
                "lib.argocd_register.argocd_lib.detect_argocd_installation",
                return_value=_discovery_with_crd(),
            ),
            patch(
                "lib.argocd_register.argocd_lib.list_argocd_applications",
                return_value=[],
            ),
            patch(
                "lib.argocd_register.argocd_lib.find_acm_touching_apps",
                return_value=[],
            ),
        ):
            coordinator = ArgocdPauseRegister(state, dry_run=False)
            coordinator.pause_hubs([(client, "primary")])

        run_id = state._config["argocd_run_id"]
        assert run_id is not None
        assert len(run_id) == 12

    def test_existing_run_id_preserved(self):
        state = _make_state_manager({"argocd_run_id": "existing-run", "argocd_paused_apps": []})
        client = Mock()

        with (
            patch(
                "lib.argocd_register.argocd_lib.detect_argocd_installation",
                return_value=_discovery_with_crd(),
            ),
            patch(
                "lib.argocd_register.argocd_lib.list_argocd_applications",
                return_value=[],
            ),
            patch(
                "lib.argocd_register.argocd_lib.find_acm_touching_apps",
                return_value=[],
            ),
        ):
            coordinator = ArgocdPauseRegister(state, dry_run=False)
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
                "lib.argocd_register.argocd_lib.detect_argocd_installation",
                return_value=_discovery_with_crd(),
            ),
            patch(
                "lib.argocd_register.argocd_lib.list_argocd_applications",
                return_value=[app1, app2],
            ),
            patch(
                "lib.argocd_register.argocd_lib.find_acm_touching_apps",
                return_value=[_make_impact(app1), _make_impact(app2)],
            ),
            patch(
                "lib.argocd_register.argocd_lib.pause_autosync",
                side_effect=pause_side_effect,
            ),
        ):
            coordinator = ArgocdPauseRegister(state, dry_run=False)
            summary = coordinator.pause_hubs([(client, "primary")])

        assert summary.failed == 0
        assert summary.newly_paused == 2

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
                "lib.argocd_register.argocd_lib.detect_argocd_installation",
                return_value=_discovery_with_crd(),
            ),
            patch(
                "lib.argocd_register.argocd_lib.list_argocd_applications",
                return_value=[],
            ) as mock_list,
            patch(
                "lib.argocd_register.argocd_lib.find_acm_touching_apps",
                return_value=[],
            ),
        ):
            coordinator = ArgocdPauseRegister(state, dry_run=False)
            coordinator.pause_hubs([(client, "primary")])

        mock_list.assert_called_once_with(client, namespaces=None)

    def test_first_pass_records_discovery_namespaces_before_pause(self):
        state = _make_state_manager({"argocd_run_id": None, "argocd_paused_apps": []})
        client = Mock()
        app_argocd = _make_app("argocd", "app-1")
        app_team = _make_app("team-gitops", "app-2")

        with (
            patch(
                "lib.argocd_register.argocd_lib.detect_argocd_installation",
                return_value=_discovery_with_crd(),
            ),
            patch(
                "lib.argocd_register.argocd_lib.list_argocd_applications",
                return_value=[app_argocd, app_team],
            ),
            patch(
                "lib.argocd_register.argocd_lib.find_acm_touching_apps",
                return_value=[],
            ),
        ):
            coordinator = ArgocdPauseRegister(state, dry_run=False)
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
                "lib.argocd_register.argocd_lib.detect_argocd_installation",
                return_value=_discovery_with_crd(),
            ),
            patch(
                "lib.argocd_register.argocd_lib.list_argocd_applications",
                return_value=[app],
            ) as mock_list,
            patch(
                "lib.argocd_register.argocd_lib.find_acm_touching_apps",
                return_value=[],
            ),
        ):
            coordinator = ArgocdPauseRegister(state, dry_run=False)
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
                "lib.argocd_register.argocd_lib.detect_argocd_installation",
                return_value=_discovery_with_crd(),
            ),
            patch(
                "lib.argocd_register.argocd_lib.list_argocd_applications",
                return_value=[],
            ) as mock_list,
            patch(
                "lib.argocd_register.argocd_lib.find_acm_touching_apps",
                return_value=[],
            ),
        ):
            coordinator = ArgocdPauseRegister(state, dry_run=False)
            coordinator.pause_hubs([(client, "primary")])

        mock_list.assert_called_once_with(client, namespaces=None)

    def test_fresh_run_ignores_stale_recorded_namespace_hints(self):
        state = _make_state_manager(
            {
                "argocd_run_id": None,
                "argocd_paused_apps": [],
                "argocd_discovery_namespaces": {"primary": ["stale-namespace"]},
            }
        )
        client = Mock()

        with (
            patch(
                "lib.argocd_register.argocd_lib.detect_argocd_installation",
                return_value=_discovery_with_crd(),
            ),
            patch(
                "lib.argocd_register.argocd_lib.list_argocd_applications",
                return_value=[],
            ) as mock_list,
            patch(
                "lib.argocd_register.argocd_lib.find_acm_touching_apps",
                return_value=[],
            ),
        ):
            coordinator = ArgocdPauseRegister(state, dry_run=False)
            coordinator.pause_hubs([(client, "primary")])

        mock_list.assert_called_once_with(client, namespaces=None)

    def test_no_crd_clears_discovery_namespaces(self):
        """With an empty register, CRD loss clears leftovers (ADR-0001: only non-empty registers are preserved)."""
        state = _make_state_manager(
            {
                "argocd_run_id": "stale",
                "argocd_paused_apps": [],
                "argocd_discovery_namespaces": {"secondary": ["argocd"]},
            }
        )
        client = Mock()

        with patch(
            "lib.argocd_register.argocd_lib.detect_argocd_installation",
            return_value=_discovery_without_crd(),
        ):
            coordinator = ArgocdPauseRegister(state, dry_run=False)
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
                "lib.argocd_register.argocd_lib.detect_argocd_installation",
                return_value=operator_discovery,
            ),
            patch(
                "lib.argocd_register.argocd_lib.list_argocd_applications",
                return_value=[watched_app],
            ) as mock_list,
            patch(
                "lib.argocd_register.argocd_lib.find_acm_touching_apps",
                return_value=[],
            ),
        ):
            coordinator = ArgocdPauseRegister(state, dry_run=False)
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
                "lib.argocd_register.argocd_lib.detect_argocd_installation",
                return_value=_discovery_with_crd(),
            ),
            patch(
                "lib.argocd_register.argocd_lib.list_argocd_applications",
                return_value=[],
            ) as mock_list,
            patch(
                "lib.argocd_register.argocd_lib.find_acm_touching_apps",
                return_value=[],
            ),
        ):
            coordinator = ArgocdPauseRegister(state, dry_run=False)
            coordinator.pause_hubs([(client, "primary")])

        mock_list.assert_called_once_with(client, namespaces=None)


@pytest.mark.unit
class TestClearArgocdPauseState:
    """Register-owned pause-state reset, with the dry-run guard inside it."""

    @staticmethod
    def _populated_state():
        return _make_state_manager(
            {
                "argocd_run_id": "run-1",
                "argocd_paused_apps": [{"hub": "primary", "namespace": "argocd", "name": "app-1"}],
                "argocd_discovery_namespaces": {"primary": ["argocd"]},
            }
        )

    def test_clear_resets_all_keys(self):
        state = self._populated_state()

        ArgocdPauseRegister(state, dry_run=False)._clear()

        assert state._config["argocd_paused_apps"] == []
        assert state._config["argocd_run_id"] is None
        assert state._config["argocd_discovery_namespaces"] == {}

    def test_clear_is_a_noop_in_dry_run(self):
        state = self._populated_state()

        ArgocdPauseRegister(state, dry_run=True)._clear()

        assert state.set_config.call_count == 0
        assert state._config["argocd_run_id"] == "run-1"


def _make_real_state(tmp_path):
    """Real StateManager backed by a temp state file."""
    return StateManager(str(tmp_path / "switchover-state.json"))


@pytest.mark.unit
class TestRegisterStatus:
    """status() / paused_hub_roles(): the register's read interface (ADR-0001)."""

    def test_empty_register_status(self, tmp_path):
        state = _make_real_state(tmp_path)
        register = ArgocdPauseRegister(state, dry_run=False)

        assert register.status() == RegisterStatus(confirmed_paused_count=0, run_id=None, entry_count=0)

    def test_status_counts_applied_entries_only(self, tmp_path):
        state = _make_real_state(tmp_path)
        state.set_config("argocd_run_id", "run-1")
        state.set_config(
            "argocd_paused_apps",
            [
                {
                    "hub": "primary",
                    "namespace": "argocd",
                    "name": "app-1",
                    "original_sync_policy": {"automated": {}},
                    "pause_applied": True,
                },
                {
                    "hub": "secondary",
                    "namespace": "argocd",
                    "name": "app-2",
                    "original_sync_policy": {"automated": {}},
                    "pause_applied": True,
                },
                {
                    "hub": "primary",
                    "namespace": "argocd",
                    "name": "app-3",
                    "original_sync_policy": {"automated": {}},
                    "pause_applied": False,
                },
                {
                    "hub": "primary",
                    "namespace": "argocd",
                    "name": "legacy-dry-run",
                    "original_sync_policy": {"automated": {}},
                    "pause_applied": False,
                    "dry_run": True,
                },
                "string garbage",
            ],
        )
        register = ArgocdPauseRegister(state, dry_run=False)

        status = register.status()

        assert status.confirmed_paused_count == 2
        assert status.run_id == "run-1"

    def test_status_drops_garbage_and_legacy_dry_run(self, tmp_path):
        state = _make_real_state(tmp_path)
        state.set_config(
            "argocd_paused_apps",
            [
                {"hub": "primary", "namespace": "argocd", "name": "app-1", "pause_applied": True},
                {"hub": "primary", "namespace": "argocd", "name": "old-dry", "dry_run": True},
                "garbage",
                42,
            ],
        )
        register = ArgocdPauseRegister(state, dry_run=False)

        status = register.status()

        assert status.entry_count == 1
        assert status.confirmed_paused_count == 1
        assert register.paused_hub_roles() == {"primary"}

    def test_register_reads_do_not_expose_mutable_state(self, tmp_path):
        state = _make_real_state(tmp_path)
        state.set_config(
            "argocd_paused_apps",
            [{"hub": "primary", "namespace": "argocd", "name": "app-1", "pause_applied": True}],
        )
        register = ArgocdPauseRegister(state, dry_run=False)

        entries = register._load_entries()
        entries[0]["name"] = "mutated"

        assert state.get_config("argocd_paused_apps")[0]["name"] == "app-1"


@pytest.mark.unit
class TestNoCrdRegisterPreservation:
    """CRD-visibility loss must never clobber a non-empty register (ADR-0001)."""

    def test_no_crd_preserves_nonempty_register(self, tmp_path, caplog):
        state = _make_real_state(tmp_path)
        entry = {
            "hub": "secondary",
            "namespace": "argocd",
            "name": "app-1",
            "original_sync_policy": {"automated": {}},
            "pause_applied": True,
        }
        state.set_config("argocd_run_id", "run-1")
        state.set_config("argocd_paused_apps", [entry])
        client = Mock()

        with patch(
            "lib.argocd_register.argocd_lib.detect_argocd_installation",
            return_value=_discovery_without_crd(),
        ):
            register = ArgocdPauseRegister(state, dry_run=False)
            with caplog.at_level("WARNING", logger="acm_switchover"):
                summary = register.pause_hubs([(client, "secondary")])

        assert summary.failed == 0
        assert summary.newly_paused == 0
        assert summary.applications_crd_visible is False
        assert state.get_config("argocd_paused_apps") == [entry]
        assert state.get_config("argocd_run_id") == "run-1"
        assert any("keeping pause register" in record.message for record in caplog.records)

    def test_no_crd_preserves_provisional_only_register(self, tmp_path, caplog):
        """A provisional entry means the pause MAY have landed; it must survive CRD loss."""
        state = _make_real_state(tmp_path)
        entry = {
            "hub": "secondary",
            "namespace": "argocd",
            "name": "app-1",
            "original_sync_policy": {"automated": {}},
            "pause_applied": False,
        }
        state.set_config("argocd_run_id", "run-1")
        state.set_config("argocd_paused_apps", [entry])
        client = Mock()

        with patch(
            "lib.argocd_register.argocd_lib.detect_argocd_installation",
            return_value=_discovery_without_crd(),
        ):
            register = ArgocdPauseRegister(state, dry_run=False)
            with caplog.at_level("WARNING", logger="acm_switchover"):
                summary = register.pause_hubs([(client, "secondary")])

        assert summary.applications_crd_visible is False
        assert summary.run_id == "run-1"
        assert state.get_config("argocd_paused_apps") == [entry]
        assert state.get_config("argocd_run_id") == "run-1"
        assert any("keeping pause register" in record.message for record in caplog.records)

    def test_no_crd_preserves_unknown_only_register(self, tmp_path):
        """An unknown-outcome entry may correspond to a landed pause; never discard it."""
        state = _make_real_state(tmp_path)
        entry = {
            "hub": "secondary",
            "namespace": "argocd",
            "name": "app-1",
            "original_sync_policy": {"automated": {}},
            "pause_applied": False,
            "pause_state": "unknown",
            "pause_run_id": "run-1",
        }
        state.set_config("argocd_run_id", "run-1")
        state.set_config("argocd_paused_apps", [entry])
        state.set_config("argocd_discovery_namespaces", {"secondary": ["argocd"]})
        client = Mock()

        with patch(
            "lib.argocd_register.argocd_lib.detect_argocd_installation",
            return_value=_discovery_without_crd(),
        ):
            summary = ArgocdPauseRegister(state, dry_run=False).pause_hubs([(client, "secondary")])

        assert summary.applications_crd_visible is False
        assert summary.run_id == "run-1"
        assert state.get_config("argocd_paused_apps") == [entry]
        assert state.get_config("argocd_run_id") == "run-1"
        assert state.get_config("argocd_discovery_namespaces") == {"secondary": ["argocd"]}

    def test_no_crd_preserves_mixed_confirmed_and_unknown_register(self, tmp_path, caplog):
        state = _make_real_state(tmp_path)
        confirmed = {
            "hub": "primary",
            "namespace": "argocd",
            "name": "app-confirmed",
            "original_sync_policy": {"automated": {}},
            "pause_applied": True,
        }
        unknown = {
            "hub": "secondary",
            "namespace": "argocd",
            "name": "app-unknown",
            "original_sync_policy": {"automated": {}},
            "pause_applied": False,
            "pause_state": "unknown",
            "pause_run_id": "run-1",
        }
        state.set_config("argocd_run_id", "run-1")
        state.set_config("argocd_paused_apps", [confirmed, unknown])
        client = Mock()

        with patch(
            "lib.argocd_register.argocd_lib.detect_argocd_installation",
            return_value=_discovery_without_crd(),
        ):
            register = ArgocdPauseRegister(state, dry_run=False)
            with caplog.at_level("WARNING", logger="acm_switchover"):
                summary = register.pause_hubs([(client, "secondary")])

        assert summary.applications_crd_visible is False
        assert state.get_config("argocd_paused_apps") == [confirmed, unknown]
        assert state.get_config("argocd_run_id") == "run-1"
        warning = next(record.message for record in caplog.records if "keeping pause register" in record.message)
        assert "2" in warning and "1" in warning

    def test_no_crd_clears_empty_register(self, tmp_path):
        state = _make_real_state(tmp_path)
        state.set_config("argocd_run_id", "stale")
        state.set_config("argocd_paused_apps", [])
        state.set_config("argocd_discovery_namespaces", {"secondary": ["argocd"]})
        client = Mock()

        with patch(
            "lib.argocd_register.argocd_lib.detect_argocd_installation",
            return_value=_discovery_without_crd(),
        ):
            register = ArgocdPauseRegister(state, dry_run=False)
            summary = register.pause_hubs([(client, "secondary")])

        assert summary.newly_paused == 0
        assert summary.failed == 0
        assert summary.applications_crd_visible is False
        assert state.get_config("argocd_paused_apps") == []
        assert state.get_config("argocd_run_id") is None
        assert state.get_config("argocd_discovery_namespaces") == {}

    def test_cleared_register_reports_no_run_id_even_in_dry_run(self, tmp_path):
        """OO-017: a run that paused nothing must not report a stale persisted run id.

        Dry-run leaves the state file alone, so the stale id is still on disk --
        but the summary is the sole reporter (G1), and reporting it here would
        announce a pause that never happened.
        """
        state = _make_real_state(tmp_path)
        state.set_config("argocd_run_id", "stale-from-earlier-run")
        state.set_config("argocd_paused_apps", [])
        client = Mock()

        with patch(
            "lib.argocd_register.argocd_lib.detect_argocd_installation",
            return_value=_discovery_without_crd(),
        ):
            summary = ArgocdPauseRegister(state, dry_run=True).pause_hubs([(client, "secondary")])

        assert summary.run_id is None
        assert summary.newly_paused == 0
        assert summary.applications_crd_visible is False
        # dry-run still must not touch persisted state
        assert state.get_config("argocd_run_id") == "stale-from-earlier-run"


@pytest.mark.unit
class TestPauseSummaryReporting:
    """F1: pause_hubs returns a PauseSummary whose counters mean one thing each."""

    def test_no_crd_preserve_path_reports_zero_newly_paused(self, tmp_path):
        """ADR-0001 preserve path paused nothing this run; it must not claim otherwise."""
        state = _make_real_state(tmp_path)
        entry = {
            "hub": "secondary",
            "namespace": "argocd",
            "name": "app-1",
            "original_sync_policy": {"automated": {}},
            "pause_applied": True,
        }
        state.set_config("argocd_run_id", "run-1")
        state.set_config("argocd_paused_apps", [entry])
        client = Mock()

        with patch(
            "lib.argocd_register.argocd_lib.detect_argocd_installation",
            return_value=_discovery_without_crd(),
        ):
            register = ArgocdPauseRegister(state, dry_run=False)
            summary = register.pause_hubs([(client, "secondary")])

        assert summary.newly_paused == 0
        assert summary.failed == 0
        assert summary.blocked == 0
        assert summary.applications_crd_visible is False
        assert summary.run_id == "run-1"

    def test_blockers_are_reported_as_blocked_not_failed(self):
        state = _make_state_manager({"argocd_run_id": None, "argocd_paused_apps": []})
        client = Mock()
        blocker = argocd_lib.ArgocdPauseBlocker(
            namespace="argocd",
            name="child-app",
            reason=argocd_lib.PAUSE_BLOCK_REASON_APPLICATIONSET_MANAGED,
            message="managed by ApplicationSet parent-set",
        )

        with (
            patch(
                "lib.argocd_register.argocd_lib.detect_argocd_installation",
                return_value=_discovery_with_crd(),
            ),
            patch("lib.argocd_register.argocd_lib.list_argocd_applications", return_value=[]),
            patch("lib.argocd_register.argocd_lib.find_argocd_pause_blockers", return_value=[blocker]),
        ):
            summary = ArgocdPauseRegister(state, dry_run=False).pause_hubs([(client, "primary")])

        assert summary.blocked == 1
        assert summary.failed == 0
        assert summary.newly_paused == 0

    def test_counts_newly_paused_already_paused_and_recovered(self):
        state = _make_state_manager(
            {
                "argocd_run_id": "run-1",
                "argocd_paused_apps": [
                    {
                        "hub": "primary",
                        "namespace": "argocd",
                        "name": "already",
                        "original_sync_policy": {"automated": {}},
                        "pause_applied": True,
                    },
                    {
                        "hub": "primary",
                        "namespace": "argocd",
                        "name": "pending",
                        "original_sync_policy": {"automated": {}},
                        "pause_applied": False,
                    },
                ],
            }
        )
        client = Mock()
        fresh = _make_app("argocd", "fresh")
        already = _make_app("argocd", "already", automated=False)
        pending = _make_app(
            "argocd",
            "pending",
            automated=False,
            annotations={argocd_lib.ARGOCD_PAUSED_BY_ANNOTATION: "run-1"},
        )
        apps = [fresh, already, pending]

        with (
            patch(
                "lib.argocd_register.argocd_lib.detect_argocd_installation",
                return_value=_discovery_with_crd(),
            ),
            patch("lib.argocd_register.argocd_lib.list_argocd_applications", return_value=apps),
            patch(
                "lib.argocd_register.argocd_lib.find_acm_touching_apps",
                return_value=[_make_impact(a) for a in apps],
            ),
            patch("lib.argocd_register.argocd_lib.pause_autosync") as mock_pause,
        ):
            mock_pause.return_value = argocd_lib.PauseResult(
                namespace="argocd",
                name="fresh",
                original_sync_policy={"automated": {}},
                patched=True,
            )
            summary = ArgocdPauseRegister(state, dry_run=False).pause_hubs([(client, "primary")])

        assert summary.newly_paused == 1
        assert summary.already_paused == 1
        assert summary.recovered == 1
        assert summary.failed == 0
        assert summary.blocked == 0
        assert summary.applications_crd_visible is True
        assert summary.run_id == "run-1"

    def test_summary_reports_the_mode_the_run_used(self):
        """G2: PauseSummary is self-describing about dry-run, like its ResumeSummary sibling."""
        client = Mock()
        app = _make_app("argocd", "app-1")

        def _run(dry_run):
            state = _make_state_manager({"argocd_run_id": None, "argocd_paused_apps": []})
            with (
                patch(
                    "lib.argocd_register.argocd_lib.detect_argocd_installation",
                    return_value=_discovery_with_crd(),
                ),
                patch("lib.argocd_register.argocd_lib.list_argocd_applications", return_value=[app]),
                patch(
                    "lib.argocd_register.argocd_lib.find_acm_touching_apps",
                    return_value=[_make_impact(app)],
                ),
                patch("lib.argocd_register.argocd_lib.pause_autosync") as mock_pause,
            ):
                mock_pause.return_value = argocd_lib.PauseResult(
                    namespace="argocd",
                    name="app-1",
                    original_sync_policy={"automated": {}},
                    patched=True,
                )
                return ArgocdPauseRegister(state, dry_run=dry_run).pause_hubs([(client, "primary")])

        dry_summary = _run(True)
        assert dry_summary.dry_run is True
        assert dry_summary.run_id
        assert _run(False).dry_run is False

    def test_no_crd_summary_reports_the_mode_the_run_used(self):
        """G2: the ADR-0001 short-circuit paths describe their mode too."""
        state = _make_state_manager({"argocd_run_id": None, "argocd_paused_apps": []})
        with patch(
            "lib.argocd_register.argocd_lib.detect_argocd_installation",
            return_value=_discovery_without_crd(),
        ):
            summary = ArgocdPauseRegister(state, dry_run=True).pause_hubs([(Mock(), "primary")])

        assert summary.dry_run is True
        assert summary.applications_crd_visible is False

    def test_patch_failure_counts_failed_only(self):
        state = _make_state_manager({"argocd_run_id": None, "argocd_paused_apps": []})
        client = Mock()
        app = _make_app("argocd", "app-1")

        with (
            patch(
                "lib.argocd_register.argocd_lib.detect_argocd_installation",
                return_value=_discovery_with_crd(),
            ),
            patch("lib.argocd_register.argocd_lib.list_argocd_applications", return_value=[app]),
            patch(
                "lib.argocd_register.argocd_lib.find_acm_touching_apps",
                return_value=[_make_impact(app)],
            ),
            patch("lib.argocd_register.argocd_lib.pause_autosync") as mock_pause,
        ):
            mock_pause.return_value = argocd_lib.PauseResult(
                namespace="argocd",
                name="app-1",
                original_sync_policy={"automated": {}},
                patched=False,
                error="403 Forbidden",
            )
            summary = ArgocdPauseRegister(state, dry_run=False).pause_hubs([(client, "primary")])

        assert summary.failed == 1
        assert summary.blocked == 0
        assert summary.newly_paused == 0


def _make_resume_client(apps_by_key, *, patch_error=None):
    """Fake KubeClient for resume: serves live Applications and records patches."""
    client = Mock()
    client.dry_run = False

    def get_custom_resource(group, version, plural, name, namespace=None):
        return copy.deepcopy(apps_by_key.get((namespace, name)))

    client.get_custom_resource.side_effect = get_custom_resource
    if patch_error is not None:
        client.patch_custom_resource.side_effect = patch_error
    return client


def _live_paused_app(namespace, name, run_id):
    return {
        "metadata": {
            "namespace": namespace,
            "name": name,
            "annotations": {argocd_lib.ARGOCD_PAUSED_BY_ANNOTATION: run_id},
            "resourceVersion": "10",
        },
        "spec": {"syncPolicy": {}},
    }


def _register_entry(hub, name, *, pause_applied=True):
    return {
        "hub": hub,
        "namespace": "argocd",
        "name": name,
        "original_sync_policy": {"automated": {"prune": True}},
        "pause_applied": pause_applied,
    }


@pytest.mark.unit
class TestRegisterResume:
    """resume(): ADR-0001 invariant - successful entries leave the register immediately."""

    def test_resume_removes_entries_on_success(self, tmp_path):
        state = _make_real_state(tmp_path)
        state.set_config("argocd_run_id", "run-1")
        state.set_config(
            "argocd_paused_apps", [_register_entry("secondary", "app-1"), _register_entry("secondary", "app-2")]
        )
        state.set_config("argocd_discovery_namespaces", {"secondary": ["argocd"]})
        client = _make_resume_client(
            {
                ("argocd", "app-1"): _live_paused_app("argocd", "app-1", "run-1"),
                ("argocd", "app-2"): _live_paused_app("argocd", "app-2", "run-1"),
            }
        )

        register = ArgocdPauseRegister(state, dry_run=False)
        summary = register.resume(None, client)

        assert summary.dry_run is False
        assert summary.restored == 2
        assert summary.failed == 0
        assert summary.remaining_in_register == 0
        assert state.get_config("argocd_paused_apps") == []
        assert state.get_config("argocd_run_id") is None
        assert state.get_config("argocd_discovery_namespaces") == {}

    def test_resume_forgets_marker_missing_entry_when_autosync_enabled(self, tmp_path):
        """Marker gone AND auto-sync back on: genuinely resumed elsewhere, so forget the entry."""
        state = _make_real_state(tmp_path)
        state.set_config("argocd_run_id", "run-1")
        state.set_config("argocd_paused_apps", [_register_entry("secondary", "app-1")])
        live = _live_paused_app("argocd", "app-1", "run-1")
        live["metadata"]["annotations"] = {}
        live["spec"]["syncPolicy"] = {"automated": {"prune": True}}
        client = _make_resume_client({("argocd", "app-1"): live})

        register = ArgocdPauseRegister(state, dry_run=False)
        summary = register.resume(None, client)

        assert summary.already_resumed == 1
        assert summary.failed == 0
        assert summary.remaining_in_register == 0
        assert state.get_config("argocd_paused_apps") == []
        assert state.get_config("argocd_run_id") is None

    def test_resume_keeps_marker_missing_entry_when_autosync_disabled(self, tmp_path, caplog):
        """Marker gone but auto-sync still off: the app is still paused, keep the obligation."""
        state = _make_real_state(tmp_path)
        state.set_config("argocd_run_id", "run-1")
        state.set_config("argocd_paused_apps", [_register_entry("secondary", "app-1")])
        live = _live_paused_app("argocd", "app-1", "run-1")
        live["metadata"]["annotations"] = {}
        live["spec"]["syncPolicy"] = {}
        client = _make_resume_client({("argocd", "app-1"): live})

        register = ArgocdPauseRegister(state, dry_run=False)
        with caplog.at_level("WARNING", logger="acm_switchover"):
            summary = register.resume(None, client)

        assert summary.failed == 1
        assert summary.already_resumed == 0
        assert summary.remaining_in_register == 1
        assert state.get_config("argocd_paused_apps") == [_register_entry("secondary", "app-1")]
        assert state.get_config("argocd_paused_apps")[0]["original_sync_policy"] == {"automated": {"prune": True}}
        assert state.get_config("argocd_run_id") == "run-1"
        assert any("auto-sync is still disabled" in record.message for record in caplog.records)

    def test_resume_keeps_marker_missing_entry_when_autosync_unobserved(self, tmp_path):
        """autosync_enabled=None is absence of evidence, not proof of restoration: keep."""
        state = _make_real_state(tmp_path)
        state.set_config("argocd_run_id", "run-1")
        state.set_config("argocd_paused_apps", [_register_entry("secondary", "app-1")])
        client = Mock()
        client.dry_run = False

        with patch(
            "lib.argocd_register.argocd_lib.resume_autosync",
            return_value=argocd_lib.ResumeResult(
                namespace="argocd",
                name="app-1",
                restored=False,
                skip_reason=argocd_lib.RESUME_SKIP_REASON_MARKER_MISSING,
                autosync_enabled=None,
            ),
        ):
            summary = ArgocdPauseRegister(state, dry_run=False).resume(None, client)

        assert summary.failed == 1
        assert summary.already_resumed == 0
        assert state.get_config("argocd_paused_apps") == [_register_entry("secondary", "app-1")]
        assert state.get_config("argocd_run_id") == "run-1"

    def test_resume_failure_keeps_entry(self, tmp_path):
        state = _make_real_state(tmp_path)
        state.set_config("argocd_run_id", "run-1")
        state.set_config("argocd_paused_apps", [_register_entry("secondary", "app-1")])
        client = _make_resume_client(
            {("argocd", "app-1"): _live_paused_app("argocd", "app-1", "run-1")},
            patch_error=RuntimeError("patch failed"),
        )

        register = ArgocdPauseRegister(state, dry_run=False)
        summary = register.resume(None, client)

        assert summary.failed == 1
        assert summary.remaining_in_register == 1
        assert state.get_config("argocd_paused_apps") == [_register_entry("secondary", "app-1")]
        assert state.get_config("argocd_run_id") == "run-1"

    def test_resume_attempts_unconfirmed_entry(self, tmp_path):
        """pause_applied=False entries are attempted; the marker check makes it safe (coexistence.md)."""
        state = _make_real_state(tmp_path)
        state.set_config("argocd_run_id", "run-1")
        state.set_config("argocd_paused_apps", [_register_entry("secondary", "app-1", pause_applied=False)])
        client = _make_resume_client({("argocd", "app-1"): _live_paused_app("argocd", "app-1", "run-1")})

        register = ArgocdPauseRegister(state, dry_run=False)
        summary = register.resume(None, client)

        assert summary.restored == 1
        client.patch_custom_resource.assert_called_once()
        assert state.get_config("argocd_paused_apps") == []

    def test_dry_run_resume_mutates_nothing(self, tmp_path):
        state = _make_real_state(tmp_path)
        state.set_config("argocd_run_id", "run-1")
        state.set_config("argocd_paused_apps", [_register_entry("secondary", "app-1")])
        client = _make_resume_client({("argocd", "app-1"): _live_paused_app("argocd", "app-1", "run-1")})

        register = ArgocdPauseRegister(state, dry_run=True)
        summary = register.resume(None, client)

        assert summary.restored == 1
        assert summary.dry_run is True
        client.get_custom_resource.assert_not_called()
        client.patch_custom_resource.assert_not_called()
        assert state.get_config("argocd_paused_apps") == [_register_entry("secondary", "app-1")]
        assert state.get_config("argocd_run_id") == "run-1"

    def test_unknown_hub_or_missing_client_counts_failed_and_stays(self, tmp_path):
        state = _make_real_state(tmp_path)
        state.set_config("argocd_run_id", "run-1")
        state.set_config(
            "argocd_paused_apps",
            [_register_entry("tertiary", "app-1"), _register_entry("primary", "app-2")],
        )

        register = ArgocdPauseRegister(state, dry_run=False)
        summary = register.resume(None, _make_resume_client({}))

        assert summary.failed == 2
        assert summary.remaining_in_register == 2
        assert len(state.get_config("argocd_paused_apps")) == 2
        assert state.get_config("argocd_run_id") == "run-1"

    def test_resume_with_empty_register_is_noop(self, tmp_path):
        state = _make_real_state(tmp_path)

        register = ArgocdPauseRegister(state, dry_run=False)
        summary = register.resume(None, _make_resume_client({}))

        assert (summary.restored, summary.already_resumed, summary.failed, summary.remaining_in_register) == (
            0,
            0,
            0,
            0,
        )


@pytest.mark.unit
class TestDryRunRecordsNothing:
    """ADR-0001: dry-run pause writes zero state; discovery/blocker checks still run."""

    def test_dry_run_pause_writes_no_state(self, tmp_path):
        state = _make_real_state(tmp_path)
        client = Mock()
        client.dry_run = True
        app = _make_app("argocd", "app-1")

        with (
            patch(
                "lib.argocd_register.argocd_lib.detect_argocd_installation",
                return_value=_discovery_with_crd(),
            ),
            patch(
                "lib.argocd_register.argocd_lib.list_argocd_applications",
                return_value=[app],
            ),
        ):
            register = ArgocdPauseRegister(state, dry_run=True)
            summary = register.pause_hubs([(client, "primary")])

        assert summary.failed == 0
        assert summary.newly_paused == 1
        client.patch_custom_resource.assert_not_called()
        for key in ("argocd_paused_apps", "argocd_run_id", "argocd_discovery_namespaces"):
            assert not state.get_config(key)

    def test_dry_run_pause_preserves_existing_real_state(self, tmp_path):
        state = _make_real_state(tmp_path)
        real_entry = {
            "hub": "secondary",
            "namespace": "argocd",
            "name": "app-9",
            "original_sync_policy": {"automated": {}},
            "pause_applied": True,
        }
        state.set_config("argocd_run_id", "run-1")
        state.set_config("argocd_paused_apps", [real_entry])
        state.set_config("argocd_discovery_namespaces", {"secondary": ["argocd"]})
        client = Mock()
        client.dry_run = True
        app = _make_app("argocd", "app-1")

        with (
            patch(
                "lib.argocd_register.argocd_lib.detect_argocd_installation",
                return_value=_discovery_with_crd(),
            ),
            patch(
                "lib.argocd_register.argocd_lib.list_argocd_applications",
                return_value=[app],
            ),
        ):
            register = ArgocdPauseRegister(state, dry_run=True)
            register.pause_hubs([(client, "primary")])

        assert state.get_config("argocd_paused_apps") == [real_entry]
        assert state.get_config("argocd_run_id") == "run-1"
        assert state.get_config("argocd_discovery_namespaces") == {"secondary": ["argocd"]}

    def test_dry_run_no_crd_does_not_clear_state(self, tmp_path):
        state = _make_real_state(tmp_path)
        state.set_config("argocd_discovery_namespaces", {"secondary": ["argocd"]})
        client = Mock()
        client.dry_run = True

        with patch(
            "lib.argocd_register.argocd_lib.detect_argocd_installation",
            return_value=_discovery_without_crd(),
        ):
            register = ArgocdPauseRegister(state, dry_run=True)
            summary = register.pause_hubs([(client, "secondary")])

        assert (summary.newly_paused, summary.failed) == (0, 0)
        assert summary.applications_crd_visible is False
        assert state.get_config("argocd_discovery_namespaces") == {"secondary": ["argocd"]}
