"""Tests for Argo CD resume-only and resume-on-failure CLI helpers."""

import logging
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from acm_switchover import _attempt_argocd_resume_on_failure, _run_argocd_resume_only
from lib import argocd as argocd_lib
from tests.main_test_helpers import make_resume_on_failure_args, make_resume_only_context_args


@pytest.mark.unit
class TestArgocdResumeOnly:
    def _make_identity_state(self, tmp_path):
        from lib.utils import StateManager

        state = StateManager(str(tmp_path / "resume-state.json"))
        state.ensure_contexts("hub-a", "hub-b")
        state.ensure_hub_identities(
            {
                "primary": {"context": "hub-a", "cluster_uid": "uid-primary"},
                "secondary": {"context": "hub-b", "cluster_uid": "uid-secondary"},
            }
        )
        state.set_config("argocd_run_id", "run-1")
        state.set_config(
            "argocd_paused_apps",
            [
                {
                    "hub": "secondary",
                    "namespace": "argocd",
                    "name": "app-2",
                    "original_sync_policy": {"automated": {}},
                }
            ],
        )
        return state

    def test_resume_only_rejects_stored_secondary_identity_mismatch(self, tmp_path, caplog):
        from acm_switchover import _run_argocd_resume_only

        state = self._make_identity_state(tmp_path)
        args = SimpleNamespace(primary_context="hub-a", secondary_context="hub-b", force=False)
        primary = Mock(name="primary-client")
        primary.get_cluster_identity.return_value = {"context": "hub-a", "cluster_uid": "uid-primary"}
        secondary = Mock(name="secondary-client")
        secondary.get_cluster_identity.return_value = {"context": "hub-b", "cluster_uid": "uid-secondary-new"}
        logger = logging.getLogger("test.resume_only_identity_mismatch")

        with patch("acm_switchover.argocd_lib.resume_recorded_applications") as resume_recorded:
            resume_recorded.return_value = argocd_lib.ResumeSummary(restored=1, already_resumed=0, failed=0)
            with caplog.at_level(logging.ERROR):
                result = _run_argocd_resume_only(args, state, primary, secondary, logger)

        assert result is False
        assert "hub identity" in caplog.text
        resume_recorded.assert_not_called()

    def test_resume_only_rejects_unreadable_live_secondary_identity(self, tmp_path, caplog):
        from acm_switchover import _run_argocd_resume_only

        state = self._make_identity_state(tmp_path)
        args = SimpleNamespace(primary_context="hub-a", secondary_context="hub-b", force=False)
        primary = Mock(name="primary-client")
        primary.get_cluster_identity.return_value = {"context": "hub-a", "cluster_uid": "uid-primary"}
        secondary = Mock(name="secondary-client")
        secondary.get_cluster_identity.side_effect = RuntimeError("kube-system UID unavailable")
        logger = logging.getLogger("test.resume_only_identity_missing")

        with patch("acm_switchover.argocd_lib.resume_recorded_applications") as resume_recorded:
            resume_recorded.return_value = argocd_lib.ResumeSummary(restored=1, already_resumed=0, failed=0)
            with caplog.at_level(logging.ERROR):
                result = _run_argocd_resume_only(args, state, primary, secondary, logger)

        assert result is False
        assert "hub identity" in caplog.text
        resume_recorded.assert_not_called()

    def test_resume_only_builds_primary_client_from_recorded_state_when_primary_context_omitted(
        self,
    ):
        from acm_switchover import _run_argocd_resume_only

        paused_apps = [
            {
                "hub": "primary",
                "namespace": "argocd",
                "name": "app-1",
                "original_sync_policy": {"automated": {}},
            }
        ]
        state = Mock()
        state.state = {
            "contexts": {
                "primary": "hub-a",
                "secondary": "hub-b",
            }
        }
        state.get_config.side_effect = lambda key, default=None: {
            "argocd_run_id": "run-1",
            "argocd_paused_apps": paused_apps,
        }.get(key, default)
        args = SimpleNamespace(primary_context=None, secondary_context="hub-b", dry_run=False)
        secondary = Mock(name="secondary-client")
        created_primary = Mock(name="primary-client")
        logger = logging.getLogger("test")

        with patch("acm_switchover.KubeClient", return_value=created_primary) as kube_client, patch(
            "acm_switchover.argocd_lib.resume_recorded_applications"
        ) as resume_recorded:
            resume_recorded.return_value = argocd_lib.ResumeSummary(restored=1, already_resumed=0, failed=0)

            assert _run_argocd_resume_only(args, state, None, secondary, logger) is True

        kube_client.assert_called_once_with("hub-a", dry_run=False)
        resume_recorded.assert_called_once_with(
            paused_apps,
            "run-1",
            created_primary,
            secondary,
            logger,
        )

    def test_resume_only_swaps_clients_when_contexts_are_reversed(self):
        from acm_switchover import _run_argocd_resume_only

        paused_apps = [
            {
                "hub": "primary",
                "namespace": "argocd",
                "name": "app-1",
                "original_sync_policy": {"automated": {}},
            },
            {
                "hub": "secondary",
                "namespace": "argocd",
                "name": "app-2",
                "original_sync_policy": {"automated": {"prune": True}},
            },
        ]
        state = Mock()
        state.state = {
            "contexts": {
                "primary": "hub-a",
                "secondary": "hub-b",
            }
        }
        state.get_config.side_effect = lambda key, default=None: {
            "argocd_run_id": "run-1",
            "argocd_paused_apps": paused_apps,
        }.get(key, default)
        args = SimpleNamespace(primary_context="hub-b", secondary_context="hub-a")
        primary = Mock(name="primary-client")
        secondary = Mock(name="secondary-client")
        logger = logging.getLogger("test")

        with patch("acm_switchover.argocd_lib.resume_recorded_applications") as resume_recorded:
            resume_recorded.return_value = argocd_lib.ResumeSummary(restored=2, already_resumed=0, failed=0)

            assert _run_argocd_resume_only(args, state, primary, secondary, logger) is True

        resume_recorded.assert_called_once_with(
            paused_apps,
            "run-1",
            secondary,
            primary,
            logger,
        )

    def test_resume_only_fails_when_state_missing(self):
        from acm_switchover import _run_argocd_resume_only

        state = Mock()
        state.get_config.side_effect = lambda key, default=None: {
            "argocd_run_id": None,
            "argocd_paused_apps": [],
        }.get(key, default)
        args = SimpleNamespace()
        primary = Mock()
        secondary = Mock()
        logger = logging.getLogger("test")

        assert _run_argocd_resume_only(args, state, primary, secondary, logger) is False

    def test_resume_only_rejects_dry_run_state(self):
        from acm_switchover import _run_argocd_resume_only

        state = Mock()
        state.get_config.side_effect = lambda key, default=None: {
            "argocd_pause_dry_run": True,
            "argocd_run_id": "run-1",
            "argocd_paused_apps": [
                {
                    "hub": "primary",
                    "namespace": "argocd",
                    "name": "app-1",
                    "original_sync_policy": {"automated": {}},
                }
            ],
        }.get(key, default)
        args = SimpleNamespace()
        primary = Mock()
        secondary = Mock()
        logger = logging.getLogger("test")

        with patch("acm_switchover.argocd_lib.resume_autosync") as resume_autosync:
            assert _run_argocd_resume_only(args, state, primary, secondary, logger) is False
            resume_autosync.assert_not_called()

    def test_resume_only_fails_when_restore_fails(self):
        from acm_switchover import _run_argocd_resume_only
        from lib import argocd as argocd_lib

        paused_apps = [
            {
                "hub": "primary",
                "namespace": "argocd",
                "name": "app-1",
                "original_sync_policy": {"automated": {}},
            },
            {
                "hub": "secondary",
                "namespace": "argocd",
                "name": "app-2",
                "original_sync_policy": {"automated": {"prune": True}},
            },
        ]
        state = Mock()
        state.get_config.side_effect = lambda key, default=None: {
            "argocd_run_id": "run-1",
            "argocd_paused_apps": paused_apps,
        }.get(key, default)
        args = SimpleNamespace()
        primary = Mock()
        secondary = Mock()
        logger = logging.getLogger("test")

        with patch("acm_switchover.argocd_lib.resume_autosync") as resume_autosync:
            resume_autosync.side_effect = [
                argocd_lib.ResumeResult(namespace="argocd", name="app-1", restored=True),
                argocd_lib.ResumeResult(
                    namespace="argocd",
                    name="app-2",
                    restored=False,
                    skip_reason="patch failed: 403 Forbidden",
                ),
            ]
            assert _run_argocd_resume_only(args, state, primary, secondary, logger) is False

    def test_resume_only_treats_marker_missing_as_already_resumed(self):
        from acm_switchover import _run_argocd_resume_only
        from lib import argocd as argocd_lib

        paused_apps = [
            {
                "hub": "secondary",
                "namespace": "argocd",
                "name": "app-2",
                "original_sync_policy": {"automated": {"prune": True}},
            },
        ]
        state = Mock()
        state.get_config.side_effect = lambda key, default=None: {
            "argocd_run_id": "run-1",
            "argocd_paused_apps": paused_apps,
        }.get(key, default)
        args = SimpleNamespace()
        primary = Mock()
        secondary = Mock()
        logger = logging.getLogger("test")

        with patch("acm_switchover.argocd_lib.resume_autosync") as resume_autosync:
            resume_autosync.return_value = argocd_lib.ResumeResult(
                namespace="argocd",
                name="app-2",
                restored=False,
                skip_reason=argocd_lib.RESUME_SKIP_REASON_MARKER_MISSING,
            )
            assert _run_argocd_resume_only(args, state, primary, secondary, logger) is True

    def test_resume_only_fails_on_marker_mismatch(self):
        from acm_switchover import _run_argocd_resume_only
        from lib import argocd as argocd_lib

        paused_apps = [
            {
                "hub": "secondary",
                "namespace": "argocd",
                "name": "app-2",
                "original_sync_policy": {"automated": {"prune": True}},
            },
        ]
        state = Mock()
        state.get_config.side_effect = lambda key, default=None: {
            "argocd_run_id": "run-1",
            "argocd_paused_apps": paused_apps,
        }.get(key, default)
        args = SimpleNamespace()
        primary = Mock()
        secondary = Mock()
        logger = logging.getLogger("test")

        with patch("acm_switchover.argocd_lib.resume_autosync") as resume_autosync:
            resume_autosync.return_value = argocd_lib.ResumeResult(
                namespace="argocd",
                name="app-2",
                restored=False,
                skip_reason=argocd_lib.RESUME_SKIP_REASON_MARKER_MISMATCH,
            )
            assert _run_argocd_resume_only(args, state, primary, secondary, logger) is False

    def test_resume_only_logs_malformed_state_entries(self, caplog):
        from acm_switchover import _run_argocd_resume_only

        paused_apps = [
            "bad-entry",
            {
                "hub": "secondary",
                "namespace": "argocd",
                "name": None,
                "original_sync_policy": {"automated": {}},
            },
        ]
        state = Mock()
        state.get_config.side_effect = lambda key, default=None: {
            "argocd_run_id": "run-1",
            "argocd_paused_apps": paused_apps,
        }.get(key, default)
        args = SimpleNamespace()
        primary = Mock()
        secondary = Mock()
        logger = logging.getLogger("test")

        with caplog.at_level(logging.WARNING):
            assert _run_argocd_resume_only(args, state, primary, secondary, logger) is False

        assert "unexpected format" in caplog.text
        assert "missing required fields" in caplog.text


@pytest.mark.unit
class TestAttemptArgoCDResumeOnFailure:
    """Tests for _attempt_argocd_resume_on_failure best-effort cleanup."""

    def _make_state(self, *, run_id="abc123", paused_apps=None):
        state = Mock()
        if paused_apps is None:
            paused_apps = [
                {
                    "hub": "primary",
                    "namespace": "argocd",
                    "name": "app1",
                    "original_sync_policy": {"automated": {}},
                    "pause_applied": True,
                }
            ]
        state.get_config.side_effect = lambda key, *a: {
            "argocd_run_id": run_id,
            "argocd_paused_apps": paused_apps,
        }.get(key, a[0] if a else None)
        return state

    def test_resume_called_when_flag_set_and_apps_paused(self):
        """Resume is attempted when flag is set and paused apps exist in state."""
        args = make_resume_on_failure_args()
        state = self._make_state()
        logger = logging.getLogger("test")

        with patch("acm_switchover.argocd_lib.resume_recorded_applications") as mock_resume:
            mock_resume.return_value = Mock(restored=1, already_resumed=0, failed=0)
            _attempt_argocd_resume_on_failure(args, state, Mock(), Mock(), logger)

        mock_resume.assert_called_once()

    def test_resume_success_clears_durable_pause_state(self, tmp_path):
        """Successful resume-on-failure must clear Argo CD pause state for retry."""
        from lib.utils import StateManager

        paused_apps = [
            {"hub": "primary", "namespace": "argocd", "name": "app1"},
            {"hub": "secondary", "namespace": "argocd", "name": "app2"},
        ]
        state_path = tmp_path / "state.json"
        state = StateManager(str(state_path))
        state.set_config("argocd_run_id", "run-1")
        state.set_config("argocd_paused_apps", paused_apps)
        state.set_config("argocd_pause_dry_run", False)
        state.mark_step_completed("pause_argocd_apps")
        args = make_resume_on_failure_args()
        logger = logging.getLogger("test")

        with patch("acm_switchover.argocd_lib.resume_recorded_applications") as mock_resume:
            mock_resume.return_value = SimpleNamespace(restored=1, already_resumed=1, failed=0)
            _attempt_argocd_resume_on_failure(args, state, Mock(), Mock(), logger)

        reloaded = StateManager(str(state_path))
        assert reloaded.get_config("argocd_paused_apps") == []
        assert reloaded.get_config("argocd_run_id") is None
        assert reloaded.get_config("argocd_pause_dry_run") is False
        assert reloaded.is_step_completed("pause_argocd_apps") is False

    def test_resume_success_rewinds_switchover_retry_to_primary_prep(self, tmp_path):
        """After successful resume-on-failure, the next FAILED retry must re-run primary_prep."""
        from lib.utils import Phase, StateManager

        paused_apps = [{"hub": "primary", "namespace": "argocd", "name": "app1"}]
        state_path = tmp_path / "state.json"
        state = StateManager(str(state_path))
        state.set_phase(Phase.POST_ACTIVATION)
        state.add_error("post activation failed", Phase.POST_ACTIVATION.value)
        state.set_phase(Phase.FAILED)
        state.set_config("argocd_run_id", "run-1")
        state.set_config("argocd_paused_apps", paused_apps)
        state.mark_step_completed("pause_argocd_apps")
        args = make_resume_on_failure_args()
        logger = logging.getLogger("test")

        with patch("acm_switchover.argocd_lib.resume_recorded_applications") as mock_resume:
            mock_resume.return_value = SimpleNamespace(restored=1, already_resumed=0, failed=0)
            _attempt_argocd_resume_on_failure(args, state, Mock(), Mock(), logger)

        reloaded = StateManager(str(state_path))
        assert reloaded.get_current_phase() == Phase.FAILED
        assert reloaded.get_last_error_phase() == Phase.PRIMARY_PREP

    def test_resume_success_rewinds_restore_only_retry_to_preflight(self, tmp_path):
        """Restore-only retry must re-run the preflight-slot Argo CD pause after resume-on-failure."""
        from lib.utils import Phase, StateManager

        paused_apps = [{"hub": "secondary", "namespace": "argocd", "name": "app1"}]
        state_path = tmp_path / "state.json"
        state = StateManager(str(state_path))
        state.set_phase(Phase.POST_ACTIVATION)
        state.add_error("post activation failed", Phase.POST_ACTIVATION.value)
        state.set_phase(Phase.FAILED)
        state.set_config("argocd_run_id", "run-1")
        state.set_config("argocd_paused_apps", paused_apps)
        state.mark_step_completed("pause_argocd_apps")
        args = make_resume_on_failure_args(restore_only=True)
        logger = logging.getLogger("test")

        with patch("acm_switchover.argocd_lib.resume_recorded_applications") as mock_resume:
            mock_resume.return_value = SimpleNamespace(restored=1, already_resumed=0, failed=0)
            _attempt_argocd_resume_on_failure(args, state, None, Mock(), logger)

        reloaded = StateManager(str(state_path))
        assert reloaded.get_current_phase() == Phase.FAILED
        assert reloaded.get_last_error_phase() == Phase.PREFLIGHT

    def test_resume_success_keeps_state_when_not_all_apps_accounted_for(self, tmp_path):
        """Resume-on-failure must preserve pause state if the summary misses recorded apps."""
        from lib.utils import StateManager

        paused_apps = [
            {"hub": "primary", "namespace": "argocd", "name": "app1"},
            {"hub": "secondary", "namespace": "argocd", "name": "app2"},
        ]
        state_path = tmp_path / "state.json"
        state = StateManager(str(state_path))
        state.set_config("argocd_run_id", "run-1")
        state.set_config("argocd_paused_apps", paused_apps)
        state.set_config("argocd_pause_dry_run", False)
        state.mark_step_completed("pause_argocd_apps")
        args = make_resume_on_failure_args()
        logger = logging.getLogger("test")

        with patch("acm_switchover.argocd_lib.resume_recorded_applications") as mock_resume:
            mock_resume.return_value = SimpleNamespace(restored=1, already_resumed=0, failed=0)
            _attempt_argocd_resume_on_failure(args, state, Mock(), Mock(), logger)

        reloaded = StateManager(str(state_path))
        assert reloaded.get_config("argocd_paused_apps") == paused_apps
        assert reloaded.get_config("argocd_run_id") == "run-1"
        assert reloaded.is_step_completed("pause_argocd_apps") is True

    def test_no_resume_when_flag_not_set(self):
        """Resume is NOT attempted when flag is not set."""
        args = make_resume_on_failure_args(argocd_resume_on_failure=False)
        state = self._make_state()
        logger = logging.getLogger("test")

        with patch("acm_switchover.argocd_lib.resume_recorded_applications") as mock_resume:
            _attempt_argocd_resume_on_failure(args, state, Mock(), Mock(), logger)

        mock_resume.assert_not_called()

    def test_no_resume_when_no_paused_apps(self):
        """Resume is skipped gracefully when no paused apps in state."""
        args = make_resume_on_failure_args()
        state = self._make_state(paused_apps=[])
        logger = logging.getLogger("test")

        with patch("acm_switchover.argocd_lib.resume_recorded_applications") as mock_resume:
            _attempt_argocd_resume_on_failure(args, state, Mock(), Mock(), logger)

        mock_resume.assert_not_called()

    def test_no_resume_when_no_run_id(self):
        """Resume is skipped gracefully when run_id is missing."""
        args = make_resume_on_failure_args()
        state = self._make_state(run_id=None)
        logger = logging.getLogger("test")

        with patch("acm_switchover.argocd_lib.resume_recorded_applications") as mock_resume:
            _attempt_argocd_resume_on_failure(args, state, Mock(), Mock(), logger)

        mock_resume.assert_not_called()

    def test_resume_failure_does_not_raise(self):
        """If resume itself fails, the exception is caught and logged (best-effort)."""
        args = make_resume_on_failure_args()
        state = self._make_state()
        logger = logging.getLogger("test")

        with patch("acm_switchover.argocd_lib.resume_recorded_applications") as mock_resume:
            mock_resume.side_effect = RuntimeError("API unreachable")
            # Must not raise
            _attempt_argocd_resume_on_failure(args, state, Mock(), Mock(), logger)

    def test_resume_partial_failure_logs_warning(self, caplog):
        """Partial resume failure is logged but doesn't raise."""
        args = make_resume_on_failure_args()
        state = self._make_state()
        logger = logging.getLogger("test")

        with patch("acm_switchover.argocd_lib.resume_recorded_applications") as mock_resume:
            mock_resume.return_value = Mock(restored=0, already_resumed=0, failed=1)
            with caplog.at_level(logging.WARNING):
                _attempt_argocd_resume_on_failure(args, state, Mock(), Mock(), logger)

        assert "could not be resumed" in caplog.text
        state.set_config.assert_not_called()
        state.clear_step_completed.assert_not_called()


class TestArgocdResumeOnlyContextMismatch:
    """_run_argocd_resume_only must fail closed when contexts differ from state."""

    def _make_state(self, primary_ctx, secondary_ctx):
        state = Mock()
        state.get_config.side_effect = lambda key, default=None: {
            "argocd_pause_dry_run": False,
            "argocd_run_id": "run-123",
            "argocd_paused_apps": [{"hub": "secondary", "namespace": "argocd", "name": "app1"}],
        }.get(key, default)
        state.state = {"contexts": {"primary": primary_ctx, "secondary": secondary_ctx}}
        return state

    def test_context_mismatch_without_force_fails(self, caplog):
        state = self._make_state("hub-a", "hub-b")
        args = make_resume_only_context_args("hub-x", "hub-b")
        logger = logging.getLogger("test.argocd_resume_mismatch")

        result = _run_argocd_resume_only(args, state, Mock(), Mock(), logger)

        assert result is False
        assert "differ from recorded state" in caplog.text

    def test_context_mismatch_with_force_proceeds(self, caplog):
        state = self._make_state("hub-a", "hub-b")
        args = make_resume_only_context_args("hub-x", "hub-b", force=True)
        logger = logging.getLogger("test.argocd_resume_force")

        with patch("acm_switchover.argocd_lib.resume_recorded_applications") as mock_resume:
            mock_resume.return_value = Mock(restored=1, already_resumed=0, failed=0)
            result = _run_argocd_resume_only(args, state, Mock(), Mock(), logger)

        assert result is True
        assert "--force used" in caplog.text

    def test_secondary_mismatch_without_primary_fails(self, caplog):
        state = self._make_state("hub-a", "hub-b")
        args = make_resume_only_context_args(None, "hub-y")
        logger = logging.getLogger("test.argocd_resume_secondary_mismatch")

        result = _run_argocd_resume_only(args, state, Mock(), Mock(), logger)

        assert result is False
        assert "differ from recorded state" in caplog.text

    def test_secondary_mismatch_without_primary_force_proceeds(self, caplog):
        state = self._make_state("hub-a", "hub-b")
        args = make_resume_only_context_args(None, "hub-y", force=True)
        logger = logging.getLogger("test.argocd_resume_secondary_force")

        with patch("acm_switchover.argocd_lib.resume_recorded_applications") as mock_resume:
            mock_resume.return_value = Mock(restored=1, already_resumed=0, failed=0)
            result = _run_argocd_resume_only(args, state, Mock(), Mock(), logger)

        assert result is True
        assert "--force used" in caplog.text
