"""Unit tests for pause execution in pause_hubs() (lib/argocd_register.py).

Covers pausing across one and two hubs, the clobber guard and entry recovery
on re-pause, pause blockers, and patch failure handling. The CRD-discovery
branches and PauseSummary reporting live in
tests/test_argocd_register_discovery.py.
"""

from unittest.mock import Mock, patch

import pytest

from lib import argocd as argocd_lib
from lib.argocd_register import ArgocdPauseRegister
from tests.argocd_register_helpers import (
    _discovery_with_crd,
    _discovery_without_crd,
    _make_app,
    _make_impact,
    _make_state_manager,
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

    def test_keeps_pending_entry_without_matching_marker_as_unresolved(self):
        """A missing marker does not prove the pause failed, so the obligation is kept (ADR-0001).

        A passive-sync restore can overwrite the marker from an older backup while
        the pause itself stands. Forgetting the entry here would discard the
        original_sync_policy that is the only way to undo it.
        """
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
        entries = state._config["argocd_paused_apps"]
        assert len(entries) == 1
        assert entries[0]["pause_state"] == "unknown"
        assert entries[0]["pause_applied"] is False
        assert entries[0]["original_sync_policy"] == {"automated": {"prune": True}}

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
