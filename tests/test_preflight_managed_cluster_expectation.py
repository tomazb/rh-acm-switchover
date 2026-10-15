"""Preflight derives the expected ManagedCluster set only from a strict primary inventory (#340 E1).

The derivation consumes KubeClient.list_managed_clusters_strict; an unreadable, absent-API,
incomplete, malformed, nameless or duplicate inventory is a critical preflight failure and
never a zero expectation. The ordinary (fail-open) LIST is never used for it.
"""

from unittest.mock import Mock, patch

import pytest

from lib import argocd as argocd_lib
from lib.strict_read import StrictReadOutcome
from tests.test_preflight_coordinator import _build_validator


def _run_validate_all(validator):
    discovery = argocd_lib.ArgocdDiscoveryResult(
        has_applications_crd=False,
        has_argocds_crd=False,
        install_type="none",
    )
    with patch("modules.preflight_coordinator.validate_rbac_permissions"), patch(
        "modules.preflight_coordinator.AutoImportStrategyValidator"
    ) as auto_import_validator, patch(
        "modules.preflight_coordinator.argocd_lib.detect_argocd_installation",
        return_value=discovery,
    ):
        auto_import_validator.return_value.run = Mock()
        return validator.validate_all()


def _inventory_results(validator):
    return [r for r in validator.reporter.results if r["check"] == "ManagedCluster inventory"]


@pytest.mark.unit
@pytest.mark.parametrize(
    "outcome",
    [
        pytest.param(StrictReadOutcome.crd_absent("kind_not_served"), id="api-not-served"),
        pytest.param(StrictReadOutcome.error("read_failed"), id="read-failed"),
        pytest.param(StrictReadOutcome.error("inventory_incomplete"), id="incomplete"),
        pytest.param(StrictReadOutcome.from_items([{"metadata": {}}]), id="nameless-member"),
        pytest.param(
            StrictReadOutcome.from_items([{"metadata": {"name": "a"}}, {"metadata": {"name": "a"}}]),
            id="duplicate-name",
        ),
    ],
)
def test_an_unprovable_primary_inventory_fails_preflight_and_derives_nothing(outcome):
    validator = _build_validator()
    validator.primary.list_managed_clusters_strict.return_value = outcome

    passed, config = _run_validate_all(validator)

    assert passed is False
    [result] = _inventory_results(validator)
    assert result["passed"] is False and result["critical"] is True
    assert config["expected_managed_cluster_names"] == []
    assert config["expected_managed_cluster_count"] == 0
    validator.primary.list_custom_resources.assert_not_called()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("items", "names"),
    [
        pytest.param([], [], id="positively-empty"),
        pytest.param([{"metadata": {"name": "local-cluster"}}], [], id="local-cluster-only"),
        pytest.param(
            [{"metadata": {"name": "b"}}, {"metadata": {"name": "local-cluster"}}, {"metadata": {"name": "a"}}],
            ["a", "b"],
            id="non-local-sorted",
        ),
    ],
)
def test_a_positively_complete_primary_inventory_derives_the_expectation(items, names):
    validator = _build_validator()
    validator.primary.list_managed_clusters_strict.return_value = StrictReadOutcome.from_items(items)

    passed, config = _run_validate_all(validator)

    assert passed is True
    [result] = _inventory_results(validator)
    assert result["passed"] is True and result["critical"] is True
    assert config["expected_managed_cluster_names"] == names
    assert config["expected_managed_cluster_count"] == len(names)
    validator.primary.list_managed_clusters_strict.assert_called_once_with()
    validator.primary.list_custom_resources.assert_not_called()


@pytest.mark.unit
def test_restore_only_derives_no_expectation_and_reads_no_primary_inventory():
    validator = _build_validator(restore_only=True)

    passed, config = _run_validate_all(validator)

    assert passed is True
    assert config["expected_managed_cluster_names"] == []
    validator.primary.list_managed_clusters_strict.assert_not_called()
