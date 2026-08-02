"""Unit tests for the CRD-discovery and reporting paths of pause_hubs().

Covers the ADR-0001 CRD-loss branches -- preserving a register of unresolved
resume obligations versus clearing a genuinely empty one -- and the
PauseSummary counters callers report from.
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
        # Assert the rendered counts, not bare digits: "ADR-0001" in the message
        # body makes a substring check for "1" pass regardless of the real count.
        assert "2 unresolved app(s)" in warning
        assert "(1 confirmed paused)" in warning

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
