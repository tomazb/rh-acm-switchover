"""Unit tests for resume() in lib/argocd_register.py.

Covers the ADR-0001 resume obligations: entries leave the register only when
resume is proven complete, unresolved outcomes are retained, and the final
cleanup clears the run metadata once the register empties.
"""

from unittest.mock import Mock, patch

import pytest

from lib import argocd as argocd_lib
from lib.argocd_register import ArgocdPauseRegister
from tests.argocd_register_helpers import (
    _live_paused_app,
    _make_real_state,
    _make_resume_client,
    _register_entry,
)


@pytest.mark.unit
class TestRegisterResume:
    """resume(): ADR-0001 invariant - successful entries leave the register immediately."""

    def test_resume_removes_entries_on_success(self, tmp_path):
        state = _make_real_state(tmp_path)
        state._set_config("argocd_run_id", "run-1")
        state._set_config(
            "argocd_paused_apps", [_register_entry("secondary", "app-1"), _register_entry("secondary", "app-2")]
        )
        state._set_config("argocd_discovery_namespaces", {"secondary": ["argocd"]})
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
        assert state._get_config("argocd_paused_apps") == []
        assert state._get_config("argocd_run_id") is None
        assert state._get_config("argocd_discovery_namespaces") == {}

    def test_resume_forgets_marker_missing_entry_when_autosync_enabled(self, tmp_path):
        """Marker gone AND auto-sync back on: genuinely resumed elsewhere, so forget the entry."""
        state = _make_real_state(tmp_path)
        state._set_config("argocd_run_id", "run-1")
        state._set_config("argocd_paused_apps", [_register_entry("secondary", "app-1")])
        live = _live_paused_app("argocd", "app-1", "run-1")
        live["metadata"]["annotations"] = {}
        live["spec"]["syncPolicy"] = {"automated": {"prune": True}}
        client = _make_resume_client({("argocd", "app-1"): live})

        register = ArgocdPauseRegister(state, dry_run=False)
        summary = register.resume(None, client)

        assert summary.already_resumed == 1
        assert summary.failed == 0
        assert summary.remaining_in_register == 0
        assert state._get_config("argocd_paused_apps") == []
        assert state._get_config("argocd_run_id") is None

    def test_resume_keeps_marker_missing_entry_when_autosync_disabled(self, tmp_path, caplog):
        """Marker gone but auto-sync still off: the app is still paused, keep the obligation."""
        state = _make_real_state(tmp_path)
        state._set_config("argocd_run_id", "run-1")
        state._set_config("argocd_paused_apps", [_register_entry("secondary", "app-1")])
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
        assert state._get_config("argocd_paused_apps") == [_register_entry("secondary", "app-1")]
        assert state._get_config("argocd_paused_apps")[0]["original_sync_policy"] == {"automated": {"prune": True}}
        assert state._get_config("argocd_run_id") == "run-1"
        assert any("auto-sync is still disabled" in record.message for record in caplog.records)

    def test_resume_keeps_marker_missing_entry_when_autosync_unobserved(self, tmp_path):
        """autosync_enabled=None is absence of evidence, not proof of restoration: keep."""
        state = _make_real_state(tmp_path)
        state._set_config("argocd_run_id", "run-1")
        state._set_config("argocd_paused_apps", [_register_entry("secondary", "app-1")])
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
        assert state._get_config("argocd_paused_apps") == [_register_entry("secondary", "app-1")]
        assert state._get_config("argocd_run_id") == "run-1"

    def test_resume_failure_keeps_entry(self, tmp_path):
        state = _make_real_state(tmp_path)
        state._set_config("argocd_run_id", "run-1")
        state._set_config("argocd_paused_apps", [_register_entry("secondary", "app-1")])
        client = _make_resume_client(
            {("argocd", "app-1"): _live_paused_app("argocd", "app-1", "run-1")},
            patch_error=RuntimeError("patch failed"),
        )

        register = ArgocdPauseRegister(state, dry_run=False)
        summary = register.resume(None, client)

        assert summary.failed == 1
        assert summary.remaining_in_register == 1
        assert state._get_config("argocd_paused_apps") == [_register_entry("secondary", "app-1")]
        assert state._get_config("argocd_run_id") == "run-1"

    def test_resume_attempts_unconfirmed_entry(self, tmp_path):
        """pause_applied=False entries are attempted; the marker check makes it safe (coexistence.md)."""
        state = _make_real_state(tmp_path)
        state._set_config("argocd_run_id", "run-1")
        state._set_config("argocd_paused_apps", [_register_entry("secondary", "app-1", pause_applied=False)])
        client = _make_resume_client({("argocd", "app-1"): _live_paused_app("argocd", "app-1", "run-1")})

        register = ArgocdPauseRegister(state, dry_run=False)
        summary = register.resume(None, client)

        assert summary.restored == 1
        client.patch_custom_resource.assert_called_once()
        assert state._get_config("argocd_paused_apps") == []

    def test_dry_run_resume_mutates_nothing(self, tmp_path):
        state = _make_real_state(tmp_path)
        state._set_config("argocd_run_id", "run-1")
        state._set_config("argocd_paused_apps", [_register_entry("secondary", "app-1")])
        client = _make_resume_client({("argocd", "app-1"): _live_paused_app("argocd", "app-1", "run-1")})

        register = ArgocdPauseRegister(state, dry_run=True)
        summary = register.resume(None, client)

        assert summary.restored == 1
        assert summary.dry_run is True
        client.get_custom_resource.assert_not_called()
        client.patch_custom_resource.assert_not_called()
        assert state._get_config("argocd_paused_apps") == [_register_entry("secondary", "app-1")]
        assert state._get_config("argocd_run_id") == "run-1"

    def test_unknown_hub_or_missing_client_counts_failed_and_stays(self, tmp_path):
        state = _make_real_state(tmp_path)
        state._set_config("argocd_run_id", "run-1")
        state._set_config(
            "argocd_paused_apps",
            [_register_entry("tertiary", "app-1"), _register_entry("primary", "app-2")],
        )

        register = ArgocdPauseRegister(state, dry_run=False)
        summary = register.resume(None, _make_resume_client({}))

        assert summary.failed == 2
        assert summary.remaining_in_register == 2
        assert len(state._get_config("argocd_paused_apps")) == 2
        assert state._get_config("argocd_run_id") == "run-1"

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
