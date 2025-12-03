import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

# Allow direct imports from repo root
sys.path.insert(0, str(Path(__file__).parent.parent))

from modules.preflight_validators import AutoImportStrategyValidator, ValidationReporter
from modules.activation import SecondaryActivation
from modules.finalization import Finalization
from lib.utils import StateManager
from lib.constants import (
    MCE_NAMESPACE,
    IMPORT_CONTROLLER_CONFIGMAP,
    AUTO_IMPORT_STRATEGY_KEY,
    AUTO_IMPORT_STRATEGY_DEFAULT,
    AUTO_IMPORT_STRATEGY_SYNC,
)


@pytest.mark.unit
class TestAutoImportPreflight:
    def test_secondary_with_existing_clusters_and_default(self):
        reporter = ValidationReporter()
        validator = AutoImportStrategyValidator(reporter)
        primary = Mock()
        secondary = Mock()

        # Primary 2.14 default
        primary_cm = None
        primary.get_configmap.return_value = primary_cm
        # Secondary has one non-local cluster, default strategy
        secondary.get_configmap.return_value = None
        secondary.list_custom_resources.return_value = [
            {"metadata": {"name": "cluster-a"}},
            {"metadata": {"name": "local-cluster"}},
        ]

        validator.run(
            primary, secondary, primary_version="2.14.0", secondary_version="2.14.1"
        )

        # Expect a warning (non-critical) on secondary suggesting ImportAndSync
        msgs = [
            r
            for r in reporter.results
            if r["check"].startswith("Auto-Import Strategy (secondary)")
        ]
        assert msgs, "Expected secondary auto-import strategy message"
        assert msgs[0]["passed"] is False
        assert "ImportAndSync" in msgs[0]["message"]

    def test_primary_non_default_warns(self):
        reporter = ValidationReporter()
        validator = AutoImportStrategyValidator(reporter)
        primary = Mock()
        secondary = Mock()

        primary.get_configmap.return_value = {
            "data": {AUTO_IMPORT_STRATEGY_KEY: AUTO_IMPORT_STRATEGY_SYNC}
        }
        secondary.get_configmap.return_value = None
        secondary.list_custom_resources.return_value = []

        validator.run(
            primary, secondary, primary_version="2.14.0", secondary_version="2.14.0"
        )
        msgs = [
            r
            for r in reporter.results
            if r["check"].startswith("Auto-Import Strategy (primary)")
        ]
        assert msgs and msgs[0]["passed"] is False


@pytest.mark.unit
class TestActivationManageFlag:
    def test_sets_import_and_sync_when_flag_on(self, tmp_path):
        # Prepare state with ACM >= 2.14
        state = StateManager(str(tmp_path / "state.json"))
        state.set_config("secondary_version", "2.14.2")

        # Mock client with one non-local managed cluster and default strategy
        client = Mock()
        client.list_custom_resources.return_value = [{"metadata": {"name": "c1"}}]
        client.get_configmap.return_value = None

        act = SecondaryActivation(
            secondary_client=client,
            state_manager=state,
            method="passive",
            manage_auto_import_strategy=True,
        )

        # Call internal helper directly to keep the test focused
        act._maybe_set_auto_import_strategy()

        client.create_or_patch_configmap.assert_called_once()
        args, kwargs = client.create_or_patch_configmap.call_args
        assert kwargs["namespace"] == MCE_NAMESPACE
        assert kwargs["name"] == IMPORT_CONTROLLER_CONFIGMAP
        assert kwargs["data"][AUTO_IMPORT_STRATEGY_KEY] == AUTO_IMPORT_STRATEGY_SYNC
        assert state.get_config("auto_import_strategy_set") is True


@pytest.mark.unit
class TestFinalizationReset:
    def test_resets_import_strategy_when_flag_on(self, tmp_path):
        state = StateManager(str(tmp_path / "state.json"))
        fin = Finalization(
            secondary_client=Mock(),
            state_manager=state,
            acm_version="2.14.1",
            manage_auto_import_strategy=True,
        )
        # Pretend ImportAndSync is set
        fin.secondary.get_configmap.return_value = {
            "data": {AUTO_IMPORT_STRATEGY_KEY: AUTO_IMPORT_STRATEGY_SYNC}
        }

        # Access the private method via name mangling to avoid lint warnings
        fin._ensure_auto_import_default()

        fin.secondary.delete_configmap.assert_called_once_with(
            MCE_NAMESPACE, IMPORT_CONTROLLER_CONFIGMAP
        )
