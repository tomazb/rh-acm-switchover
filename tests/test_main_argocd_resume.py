"""Tests for Argo CD resume-only and resume-on-failure CLI helpers."""

import logging
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from acm_switchover import _attempt_argocd_resume_on_failure, _run_argocd_resume_only
from lib import argocd as argocd_lib
from lib.argocd_register import ArgocdPauseRegister
from tests.main_test_helpers import make_resume_on_failure_args, make_resume_only_context_args


@pytest.mark.unit
class TestArgocdResumeOnly:
    @staticmethod
    def _identity_clients(
        *,
        primary_context="hub-a",
        secondary_context="hub-b",
        primary_uid="uid-primary",
        secondary_uid="uid-secondary",
    ):
        primary = Mock(name="primary-client")
        primary.context = primary_context
        primary.get_cluster_identity.return_value = {"context": primary_context, "cluster_uid": primary_uid}
        secondary = Mock(name="secondary-client")
        secondary.context = secondary_context
        secondary.get_cluster_identity.return_value = {"context": secondary_context, "cluster_uid": secondary_uid}
        return primary, secondary

    def _mock_resume_state(self, paused_apps, *, primary_ctx=None, secondary_ctx=None):
        state = Mock()
        state_data = {}
        if primary_ctx is not None or secondary_ctx is not None:
            state_data["contexts"] = {"primary": primary_ctx, "secondary": secondary_ctx}
        identities = {}
        if any(isinstance(item, dict) and item.get("hub") == "primary" for item in paused_apps):
            identities["primary"] = {"context": primary_ctx or "hub-a", "cluster_uid": "uid-primary"}
        if any(isinstance(item, dict) and item.get("hub") == "secondary" for item in paused_apps):
            identities["secondary"] = {"context": secondary_ctx or "hub-b", "cluster_uid": "uid-secondary"}
        if identities:
            state_data["hub_identities"] = identities
        state.state = state_data
        state.get_config.side_effect = lambda key, default=None: {
            "argocd_run_id": "run-1",
            "argocd_paused_apps": paused_apps,
        }.get(key, default)
        return state

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

        with patch.object(ArgocdPauseRegister, "resume") as resume_recorded:
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

        with patch.object(ArgocdPauseRegister, "resume") as resume_recorded:
            resume_recorded.return_value = argocd_lib.ResumeSummary(restored=1, already_resumed=0, failed=0)
            with caplog.at_level(logging.ERROR):
                result = _run_argocd_resume_only(args, state, primary, secondary, logger)

        assert result is False
        assert "hub identity" in caplog.text
        resume_recorded.assert_not_called()

    def test_resume_only_rejects_legacy_state_without_hub_identities(self, tmp_path, caplog):
        from lib.utils import StateManager

        state = StateManager(str(tmp_path / "legacy-resume-state.json"))
        state.ensure_contexts("hub-a", "hub-b")
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
        args = make_resume_only_context_args("hub-a", "hub-b")
        logger = logging.getLogger("test.resume_only_legacy_identity_missing")

        with patch.object(ArgocdPauseRegister, "resume") as resume_recorded:
            with caplog.at_level(logging.ERROR):
                result = _run_argocd_resume_only(args, state, Mock(), Mock(), logger)

        assert result is False
        assert "missing hub identity data" in caplog.text
        resume_recorded.assert_not_called()

    def test_resume_only_force_allows_legacy_state_without_hub_identities(self, tmp_path):
        from lib.utils import StateManager

        state = StateManager(str(tmp_path / "legacy-resume-force-state.json"))
        state.ensure_contexts("hub-a", "hub-b")
        state.set_config("argocd_run_id", "run-1")
        paused_apps = [
            {
                "hub": "secondary",
                "namespace": "argocd",
                "name": "app-2",
                "original_sync_policy": {"automated": {}},
            }
        ]
        state.set_config("argocd_paused_apps", paused_apps)
        args = make_resume_only_context_args("hub-a", "hub-b", force=True)
        primary = Mock(name="primary-client")
        primary.get_cluster_identity.return_value = {"context": "hub-a", "cluster_uid": "uid-primary"}
        secondary = Mock(name="secondary-client")
        secondary.get_cluster_identity.return_value = {"context": "hub-b", "cluster_uid": "uid-secondary"}
        logger = logging.getLogger("test.resume_only_legacy_identity_force")

        with patch.object(ArgocdPauseRegister, "resume") as resume_recorded:
            resume_recorded.return_value = argocd_lib.ResumeSummary(restored=1, already_resumed=0, failed=0)

            assert _run_argocd_resume_only(args, state, primary, secondary, logger) is True

        resume_recorded.assert_called_once_with(primary, secondary)

    def test_resume_only_full_success_empties_register_and_clears_run_id(self, tmp_path):
        """Integration: after a fully successful resume-only, the register is empty and run_id cleared (ADR-0001)."""
        from acm_switchover import _run_argocd_resume_only

        state = self._make_identity_state(tmp_path)
        args = SimpleNamespace(primary_context="hub-a", secondary_context="hub-b", dry_run=False, force=False)
        primary, secondary = self._identity_clients()
        secondary.dry_run = False
        secondary.get_custom_resource.return_value = {
            "metadata": {
                "namespace": "argocd",
                "name": "app-2",
                "annotations": {argocd_lib.ARGOCD_PAUSED_BY_ANNOTATION: "run-1"},
                "resourceVersion": "7",
            },
            "spec": {"syncPolicy": {}},
        }
        secondary.patch_custom_resource.return_value = None
        logger = logging.getLogger("test.resume_only_integration")

        assert _run_argocd_resume_only(args, state, primary, secondary, logger) is True

        assert state.get_config("argocd_paused_apps") == []
        assert state.get_config("argocd_run_id") is None
        assert state.get_config("argocd_discovery_namespaces") == {}
        secondary.patch_custom_resource.assert_called_once()

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
        state = self._mock_resume_state(paused_apps, primary_ctx="hub-a", secondary_ctx="hub-b")
        args = SimpleNamespace(primary_context=None, secondary_context="hub-b", dry_run=False)
        _, secondary = self._identity_clients()
        created_primary = Mock(name="primary-client")
        logger = logging.getLogger("test")

        with patch("acm_switchover.KubeClient", return_value=created_primary) as kube_client, patch.object(
            ArgocdPauseRegister, "resume"
        ) as resume_recorded:
            resume_recorded.return_value = argocd_lib.ResumeSummary(restored=1, already_resumed=0, failed=0)

            assert _run_argocd_resume_only(args, state, None, secondary, logger) is True

        kube_client.assert_called_once_with("hub-a", dry_run=False)
        resume_recorded.assert_called_once_with(created_primary, secondary)

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
        state = self._mock_resume_state(paused_apps, primary_ctx="hub-a", secondary_ctx="hub-b")
        args = SimpleNamespace(primary_context="hub-b", secondary_context="hub-a")
        primary, secondary = self._identity_clients()
        logger = logging.getLogger("test")

        with patch.object(ArgocdPauseRegister, "resume") as resume_recorded:
            resume_recorded.return_value = argocd_lib.ResumeSummary(restored=2, already_resumed=0, failed=0)

            assert _run_argocd_resume_only(args, state, primary, secondary, logger) is True

        resume_recorded.assert_called_once_with(secondary, primary)

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

    def test_resume_only_rejects_legacy_dry_run_entries(self):
        """Legacy dry_run entries are dropped on load (ADR-0001: dry-run records nothing), leaving nothing to resume."""
        from acm_switchover import _run_argocd_resume_only

        state = Mock()
        state.get_config.side_effect = lambda key, default=None: {
            "argocd_run_id": "run-1",
            "argocd_paused_apps": [
                {
                    "hub": "primary",
                    "namespace": "argocd",
                    "name": "app-1",
                    "original_sync_policy": {"automated": {}},
                    "dry_run": True,
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
        state = self._mock_resume_state(paused_apps)
        args = SimpleNamespace()
        primary, secondary = self._identity_clients()
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
        state = self._mock_resume_state(paused_apps)
        args = SimpleNamespace()
        primary, secondary = self._identity_clients()
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
        state = self._mock_resume_state(paused_apps)
        args = SimpleNamespace()
        primary, secondary = self._identity_clients()
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
        state = self._mock_resume_state(paused_apps)
        args = SimpleNamespace()
        primary, secondary = self._identity_clients()
        logger = logging.getLogger("test")

        with caplog.at_level(logging.WARNING):
            assert _run_argocd_resume_only(args, state, primary, secondary, logger) is False

        assert "unusable record" in caplog.text

    def test_resume_only_ignores_malformed_context_mapping(self):
        paused_apps = [
            {
                "hub": "secondary",
                "namespace": "argocd",
                "name": "app-2",
                "original_sync_policy": {"automated": {}},
            }
        ]
        state = self._mock_resume_state(paused_apps)
        state.state["contexts"] = "not-a-dict"
        args = SimpleNamespace()
        primary, secondary = self._identity_clients()
        logger = logging.getLogger("test.resume_only_malformed_contexts")

        with patch.object(ArgocdPauseRegister, "resume") as resume_recorded:
            resume_recorded.return_value = argocd_lib.ResumeSummary(restored=1, already_resumed=0, failed=0)

            assert _run_argocd_resume_only(args, state, primary, secondary, logger) is True

        resume_recorded.assert_called_once_with(primary, secondary)

    def test_resume_only_leaves_unknown_hub_entries_to_resume_summary(self, caplog):
        from acm_switchover import _run_argocd_resume_only

        paused_apps = [
            {
                "hub": "secondary",
                "namespace": "argocd",
                "name": "app-2",
                "original_sync_policy": {"automated": {}},
            },
            {
                "hub": "foo",
                "namespace": "argocd",
                "name": "app-3",
                "original_sync_policy": {"automated": {}},
            },
        ]
        state = self._mock_resume_state(paused_apps)
        args = SimpleNamespace()
        primary, secondary = self._identity_clients()
        logger = logging.getLogger("test.resume_only_unknown_hub")

        with patch("acm_switchover.argocd_lib.resume_autosync") as resume_autosync:
            resume_autosync.return_value = argocd_lib.ResumeResult(
                namespace="argocd",
                name="app-2",
                restored=True,
            )
            with caplog.at_level(logging.WARNING):
                assert _run_argocd_resume_only(args, state, primary, secondary, logger) is False

        assert "unusable record or no client" in caplog.text
        assert "missing live client for recorded foo hub identity" not in caplog.text


@pytest.mark.unit
class TestAttemptArgoCDResumeOnFailure:
    """Tests for _attempt_argocd_resume_on_failure best-effort cleanup."""

    @staticmethod
    def _identity_clients(
        *,
        primary_context="hub-a",
        secondary_context="hub-b",
        primary_uid="uid-primary",
        secondary_uid="uid-secondary",
    ):
        primary = Mock(name="primary-client")
        primary.context = primary_context
        primary.get_cluster_identity.return_value = {"context": primary_context, "cluster_uid": primary_uid}
        secondary = Mock(name="secondary-client")
        secondary.context = secondary_context
        secondary.get_cluster_identity.return_value = {"context": secondary_context, "cluster_uid": secondary_uid}
        return primary, secondary

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
        state.state = {
            "contexts": {"primary": "hub-a", "secondary": "hub-b"},
            "hub_identities": {
                role: {
                    "context": "hub-a" if role == "primary" else "hub-b",
                    "cluster_uid": "uid-primary" if role == "primary" else "uid-secondary",
                }
                for role in {
                    item.get("hub")
                    for item in paused_apps
                    if isinstance(item, dict) and item.get("hub") in {"primary", "secondary"}
                }
            },
        }
        state.get_config.side_effect = lambda key, *a: {
            "argocd_run_id": run_id,
            "argocd_paused_apps": paused_apps,
        }.get(key, a[0] if a else None)
        return state

    @staticmethod
    def _bind_real_state(state, *, primary=True, secondary=True):
        primary_context = "hub-a" if primary else None
        secondary_context = "hub-b" if secondary else None
        state.ensure_contexts(primary_context, secondary_context)
        identities = {}
        if primary:
            identities["primary"] = {"context": "hub-a", "cluster_uid": "uid-primary"}
        if secondary:
            identities["secondary"] = {"context": "hub-b", "cluster_uid": "uid-secondary"}
        if identities:
            state.ensure_hub_identities(identities)

    def test_resume_called_when_flag_set_and_apps_paused(self):
        """Resume is attempted when flag is set and paused apps exist in state."""
        args = make_resume_on_failure_args()
        state = self._make_state()
        logger = logging.getLogger("test")

        with patch.object(ArgocdPauseRegister, "resume") as mock_resume:
            mock_resume.return_value = argocd_lib.ResumeSummary(restored=1, already_resumed=0, failed=0)
            _attempt_argocd_resume_on_failure(args, state, Mock(), Mock(), logger)

        mock_resume.assert_called_once()

    def test_resume_success_clears_durable_pause_state(self, tmp_path):
        """Successful resume-on-failure must clear Argo CD pause state for retry."""
        from lib.utils import StateManager

        paused_apps = [
            {
                "hub": "primary",
                "namespace": "argocd",
                "name": "app1",
                "original_sync_policy": {"automated": {}},
                "pause_applied": True,
            },
            {
                "hub": "secondary",
                "namespace": "argocd",
                "name": "app2",
                "original_sync_policy": {"automated": {}},
                "pause_applied": True,
            },
        ]
        state_path = tmp_path / "state.json"
        state = StateManager(str(state_path))
        self._bind_real_state(state)
        state.set_config("argocd_run_id", "run-1")
        state.set_config("argocd_paused_apps", paused_apps)
        state.set_config(
            "argocd_discovery_namespaces",
            {"primary": ["argocd"], "secondary": ["openshift-gitops"]},
        )
        state.mark_step_completed("pause_argocd_apps")
        args = make_resume_on_failure_args()
        logger = logging.getLogger("test")
        primary, secondary = self._identity_clients()
        for client, app_name in ((primary, "app1"), (secondary, "app2")):
            client.dry_run = False
            client.get_custom_resource.return_value = {
                "metadata": {
                    "namespace": "argocd",
                    "name": app_name,
                    "annotations": {argocd_lib.ARGOCD_PAUSED_BY_ANNOTATION: "run-1"},
                    "resourceVersion": "5",
                },
                "spec": {"syncPolicy": {}},
            }
            client.patch_custom_resource.return_value = None

        _attempt_argocd_resume_on_failure(args, state, primary, secondary, logger)

        reloaded = StateManager(str(state_path))
        assert reloaded.get_config("argocd_paused_apps") == []
        assert reloaded.get_config("argocd_run_id") is None
        assert reloaded.get_config("argocd_discovery_namespaces") == {}
        assert reloaded.is_step_completed("pause_argocd_apps") is False

    def test_resume_success_rewinds_switchover_retry_to_primary_prep(self, tmp_path):
        """After successful resume-on-failure, the next FAILED retry must re-run primary_prep."""
        from lib.utils import Phase, StateManager

        paused_apps = [{"hub": "primary", "namespace": "argocd", "name": "app1"}]
        state_path = tmp_path / "state.json"
        state = StateManager(str(state_path))
        self._bind_real_state(state)
        state.set_phase(Phase.POST_ACTIVATION)
        state.add_error("post activation failed", Phase.POST_ACTIVATION.value)
        state.set_phase(Phase.FAILED)
        state.set_config("argocd_run_id", "run-1")
        state.set_config("argocd_paused_apps", paused_apps)
        state.mark_step_completed("pause_argocd_apps")
        args = make_resume_on_failure_args()
        logger = logging.getLogger("test")
        primary, secondary = self._identity_clients()

        with patch.object(ArgocdPauseRegister, "resume") as mock_resume:
            mock_resume.return_value = argocd_lib.ResumeSummary(restored=1, already_resumed=0, failed=0)
            _attempt_argocd_resume_on_failure(args, state, primary, secondary, logger)

        reloaded = StateManager(str(state_path))
        assert reloaded.get_current_phase() == Phase.FAILED
        assert reloaded.get_last_error_phase() == Phase.PRIMARY_PREP

    def test_resume_success_rewinds_restore_only_retry_to_preflight(self, tmp_path):
        """Restore-only retry must re-run the preflight-slot Argo CD pause after resume-on-failure."""
        from lib.utils import Phase, StateManager

        paused_apps = [{"hub": "secondary", "namespace": "argocd", "name": "app1"}]
        state_path = tmp_path / "state.json"
        state = StateManager(str(state_path))
        self._bind_real_state(state, primary=False, secondary=True)
        state.set_phase(Phase.POST_ACTIVATION)
        state.add_error("post activation failed", Phase.POST_ACTIVATION.value)
        state.set_phase(Phase.FAILED)
        state.set_config("argocd_run_id", "run-1")
        state.set_config("argocd_paused_apps", paused_apps)
        state.mark_step_completed("pause_argocd_apps")
        args = make_resume_on_failure_args(restore_only=True)
        logger = logging.getLogger("test")
        _, secondary = self._identity_clients()

        with patch.object(ArgocdPauseRegister, "resume") as mock_resume:
            mock_resume.return_value = argocd_lib.ResumeSummary(restored=1, already_resumed=0, failed=0)
            _attempt_argocd_resume_on_failure(args, state, None, secondary, logger)

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
        self._bind_real_state(state)
        state.set_config("argocd_run_id", "run-1")
        state.set_config("argocd_paused_apps", paused_apps)
        state.mark_step_completed("pause_argocd_apps")
        args = make_resume_on_failure_args()
        logger = logging.getLogger("test")
        primary, secondary = self._identity_clients()

        with patch.object(ArgocdPauseRegister, "resume") as mock_resume:
            mock_resume.return_value = argocd_lib.ResumeSummary(
                restored=1, already_resumed=0, failed=0, remaining_in_register=1
            )
            _attempt_argocd_resume_on_failure(args, state, primary, secondary, logger)

        reloaded = StateManager(str(state_path))
        assert reloaded.get_config("argocd_paused_apps") == paused_apps
        assert reloaded.get_config("argocd_run_id") == "run-1"
        assert reloaded.is_step_completed("pause_argocd_apps") is True

    def test_no_resume_when_flag_not_set(self):
        """Resume is NOT attempted when flag is not set."""
        args = make_resume_on_failure_args(argocd_resume_on_failure=False)
        state = self._make_state()
        logger = logging.getLogger("test")

        with patch.object(ArgocdPauseRegister, "resume") as mock_resume:
            _attempt_argocd_resume_on_failure(args, state, Mock(), Mock(), logger)

        mock_resume.assert_not_called()

    def test_no_resume_when_no_paused_apps(self):
        """Resume is skipped gracefully when no paused apps in state."""
        args = make_resume_on_failure_args()
        state = self._make_state(paused_apps=[])
        logger = logging.getLogger("test")

        with patch.object(ArgocdPauseRegister, "resume") as mock_resume:
            _attempt_argocd_resume_on_failure(args, state, Mock(), Mock(), logger)

        mock_resume.assert_not_called()

    def test_no_resume_when_no_run_id(self):
        """Resume is skipped gracefully when run_id is missing."""
        args = make_resume_on_failure_args()
        state = self._make_state(run_id=None)
        logger = logging.getLogger("test")

        with patch.object(ArgocdPauseRegister, "resume") as mock_resume:
            _attempt_argocd_resume_on_failure(args, state, Mock(), Mock(), logger)

        mock_resume.assert_not_called()

    def test_resume_failure_does_not_raise(self):
        """If resume itself fails, the exception is caught and logged (best-effort)."""
        args = make_resume_on_failure_args()
        state = self._make_state()
        logger = logging.getLogger("test")

        with patch.object(ArgocdPauseRegister, "resume") as mock_resume:
            mock_resume.side_effect = RuntimeError("API unreachable")
            # Must not raise
            _attempt_argocd_resume_on_failure(args, state, Mock(), Mock(), logger)

    def test_resume_partial_failure_logs_warning(self, caplog):
        """Partial resume failure is logged but doesn't raise."""
        args = make_resume_on_failure_args()
        state = self._make_state()
        logger = logging.getLogger("test")

        with patch.object(ArgocdPauseRegister, "resume") as mock_resume:
            mock_resume.return_value = argocd_lib.ResumeSummary(failed=1, remaining_in_register=1)
            with caplog.at_level(logging.WARNING):
                _attempt_argocd_resume_on_failure(args, state, Mock(), Mock(), logger)

        assert "in the pause register" in caplog.text
        state.set_config.assert_not_called()
        state.clear_step_completed.assert_not_called()

    def test_resume_on_failure_uses_context_neutral_context_mismatch_message(self, caplog):
        args = make_resume_on_failure_args()
        state = self._make_state()
        logger = logging.getLogger("test.resume_on_failure_context_message")
        primary, secondary = self._identity_clients(primary_context="hub-x")

        with patch.object(ArgocdPauseRegister, "resume") as mock_resume:
            with caplog.at_level(logging.WARNING):
                _attempt_argocd_resume_on_failure(args, state, primary, secondary, logger)

        mock_resume.assert_not_called()
        assert "Argo CD resume contexts" in caplog.text
        assert "Resume-only contexts" not in caplog.text

    def test_resume_on_failure_uses_context_neutral_missing_client_message(self, caplog):
        args = make_resume_on_failure_args()
        state = self._make_state()
        logger = logging.getLogger("test.resume_on_failure_missing_client_message")
        _, secondary = self._identity_clients()

        with patch.object(ArgocdPauseRegister, "resume") as mock_resume:
            with caplog.at_level(logging.WARNING):
                _attempt_argocd_resume_on_failure(args, state, None, secondary, logger)

        mock_resume.assert_not_called()
        assert "Argo CD resume hub identity validation failed" in caplog.text
        assert "Resume-only hub identity validation failed" not in caplog.text

    def test_resume_on_failure_skips_legacy_state_without_hub_identities(self, tmp_path, caplog):
        from lib.utils import Phase, StateManager

        state = StateManager(str(tmp_path / "resume-on-failure-legacy.json"))
        state.ensure_contexts("hub-a", "hub-b")
        state.set_phase(Phase.POST_ACTIVATION)
        state.set_config("argocd_run_id", "run-1")
        state.set_config(
            "argocd_paused_apps",
            [{"hub": "primary", "namespace": "argocd", "name": "app1", "original_sync_policy": {"automated": {}}}],
        )
        args = make_resume_on_failure_args()
        primary = Mock(name="primary-client")
        primary.get_cluster_identity.return_value = {"context": "hub-a", "cluster_uid": "uid-primary"}
        secondary = Mock(name="secondary-client")
        secondary.get_cluster_identity.return_value = {"context": "hub-b", "cluster_uid": "uid-secondary"}
        logger = logging.getLogger("test.resume_on_failure_legacy_identity_missing")

        with patch.object(ArgocdPauseRegister, "resume") as mock_resume:
            with caplog.at_level(logging.WARNING):
                _attempt_argocd_resume_on_failure(args, state, primary, secondary, logger)

        mock_resume.assert_not_called()
        assert "missing hub identity data" in caplog.text

    def test_resume_on_failure_force_allows_legacy_state_without_hub_identities(self, tmp_path):
        from lib.utils import Phase, StateManager

        state = StateManager(str(tmp_path / "resume-on-failure-legacy-force.json"))
        state.ensure_contexts("hub-a", "hub-b")
        state.set_phase(Phase.POST_ACTIVATION)
        state.set_config("argocd_run_id", "run-1")
        state.set_config(
            "argocd_paused_apps",
            [{"hub": "primary", "namespace": "argocd", "name": "app1", "original_sync_policy": {"automated": {}}}],
        )
        args = make_resume_on_failure_args(force=True)
        primary = Mock(name="primary-client")
        primary.get_cluster_identity.return_value = {"context": "hub-a", "cluster_uid": "uid-primary"}
        secondary = Mock(name="secondary-client")
        secondary.get_cluster_identity.return_value = {"context": "hub-b", "cluster_uid": "uid-secondary"}
        logger = logging.getLogger("test.resume_on_failure_legacy_identity_force")

        with patch.object(ArgocdPauseRegister, "resume") as mock_resume:
            mock_resume.return_value = argocd_lib.ResumeSummary(restored=1, already_resumed=0, failed=0)
            _attempt_argocd_resume_on_failure(args, state, primary, secondary, logger)

        mock_resume.assert_called_once()


class TestArgocdResumeOnlyContextMismatch:
    """_run_argocd_resume_only must fail closed when contexts differ from state."""

    def _make_state(self, primary_ctx, secondary_ctx):
        state = Mock()
        state.get_config.side_effect = lambda key, default=None: {
            "argocd_run_id": "run-123",
            "argocd_paused_apps": [{"hub": "secondary", "namespace": "argocd", "name": "app1"}],
        }.get(key, default)
        state.state = {
            "contexts": {"primary": primary_ctx, "secondary": secondary_ctx},
            "hub_identities": {
                "secondary": {"context": secondary_ctx or "hub-b", "cluster_uid": "uid-secondary"},
            },
        }
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

        with patch.object(ArgocdPauseRegister, "resume") as mock_resume:
            mock_resume.return_value = argocd_lib.ResumeSummary(restored=1, already_resumed=0, failed=0)
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

        with patch.object(ArgocdPauseRegister, "resume") as mock_resume:
            mock_resume.return_value = argocd_lib.ResumeSummary(restored=1, already_resumed=0, failed=0)
            result = _run_argocd_resume_only(args, state, Mock(), Mock(), logger)

        assert result is True
        assert "--force used" in caplog.text


@pytest.mark.unit
def test_resume_only_dry_run_does_not_report_work_as_done(tmp_path, caplog):
    """F6: dry-run resume simulates; it must not log restored work."""
    from lib.argocd_resume import run_argocd_resume_only
    from lib.utils import StateManager

    state = StateManager(str(tmp_path / "resume-state.json"))
    state.ensure_contexts("hub-a", "hub-b")
    state.ensure_hub_identities(
        {"secondary": {"context": "hub-b", "cluster_uid": "uid-secondary"}},
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
                "pause_applied": True,
            }
        ],
    )
    args = SimpleNamespace(primary_context=None, secondary_context="hub-b", dry_run=True, force=False)
    secondary = Mock(name="secondary-client")
    secondary.context = "hub-b"
    secondary.dry_run = True
    secondary.get_cluster_identity.return_value = {"context": "hub-b", "cluster_uid": "uid-secondary"}
    logger = logging.getLogger("acm_switchover")

    with caplog.at_level("INFO", logger="acm_switchover"):
        assert run_argocd_resume_only(args, state, None, secondary, logger) is True

    messages = [record.getMessage() for record in caplog.records]
    assert any("Would restore 1" in message for message in messages)
    assert not any(message.startswith("Restored ") for message in messages)
    assert state.get_config("argocd_paused_apps") != []
    # G2: the projection must not contradict itself - restoring every entry leaves none behind.
    assert any("0 would remain" in message for message in messages)
    assert not any("1 would remain" in message for message in messages)
