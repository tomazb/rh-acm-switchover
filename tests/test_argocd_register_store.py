"""Unit tests for the pause register's durable state (lib/argocd_register_store.py).

Covers the register's read/write interface: status and entry counting, legacy
sanitization, run-id and discovery-namespace persistence, the pause-state
reset, and the ADR-0001 rule that dry-run records nothing.
"""

from unittest.mock import Mock, patch

import pytest

from lib import argocd as argocd_lib
from lib.argocd_register import ArgocdPauseRegister, RegisterStatus
from tests.argocd_register_helpers import (
    _discovery_with_crd,
    _discovery_without_crd,
    _make_app,
    _make_impact,
    _make_real_state,
    _make_state_manager,
)


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


@pytest.mark.unit
class TestRegisterStatus:
    """status() / paused_hub_roles(): the register's read interface (ADR-0001)."""

    def test_empty_register_status(self, tmp_path):
        state = _make_real_state(tmp_path)
        register = ArgocdPauseRegister(state, dry_run=False)

        assert register.status() == RegisterStatus(confirmed_paused_count=0, run_id=None, entry_count=0)

    @pytest.mark.parametrize("malformed", [{"hub": "secondary"}, "corrupted", 7])
    def test_malformed_register_state_counts_as_discarded(self, tmp_path, malformed):
        """A non-list persisted value is corruption, not an empty register.

        It must report a discarded record so resume-only stays in its error path
        rather than treating the run id as leftover metadata and clearing the
        evidence needed to recover.
        """
        state = _make_real_state(tmp_path)
        state.set_config("argocd_run_id", "run-1")
        state.set_config("argocd_paused_apps", malformed)

        status = ArgocdPauseRegister(state, dry_run=False).status()

        assert status.entry_count == 0
        assert status.discarded_entry_count >= 1
        assert status.run_id == "run-1"

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
