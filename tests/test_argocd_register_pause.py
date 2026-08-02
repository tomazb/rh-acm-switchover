"""Unit tests for pause_hubs() in lib/argocd_register.py.

Covers hub detection and Argo CD discovery, the clobber guard and entry
recovery, pause blockers, patch failure handling, the PauseSummary counters,
and the ADR-0001 CRD-loss paths.
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
    _make_real_state,
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
