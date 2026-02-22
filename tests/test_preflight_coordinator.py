"""Unit tests for preflight coordinator wiring."""

from unittest.mock import Mock, patch

import pytest

from modules.preflight_coordinator import PreflightValidator


def _build_validator(argocd_check: bool, argocd_manage: bool) -> PreflightValidator:
    """Create PreflightValidator with internal validators stubbed for focused RBAC tests."""
    primary = Mock()
    secondary = Mock()
    primary.namespace_exists.return_value = True
    secondary.namespace_exists.return_value = True

    validator = PreflightValidator(
        primary_client=primary,
        secondary_client=secondary,
        method="passive",
        skip_rbac_validation=False,
        argocd_check=argocd_check,
        argocd_manage=argocd_manage,
    )

    # Stub downstream validators so this test only exercises RBAC wiring.
    validator.kubeconfig_validator.run = Mock()
    validator.tooling_validator.run = Mock()
    validator.namespace_validator.run = Mock()
    validator.version_validator.run = Mock(return_value=("2.14.0", "2.14.0"))
    validator.hub_component_validator.run = Mock()
    validator.backup_validator.run = Mock()
    validator.backup_schedule_validator.run = Mock()
    validator.backup_storage_location_validator.run = Mock()
    validator.cluster_deployment_validator.run = Mock()
    validator.managed_cluster_backup_validator.run = Mock()
    validator.passive_sync_validator.run = Mock()
    validator.observability_detector.detect = Mock(return_value=(False, False))
    validator.observability_prereq_validator.run = Mock()
    validator.reporter.print_summary = Mock()

    return validator


@pytest.mark.unit
@pytest.mark.parametrize(
    "argocd_check,argocd_manage,expected_mode",
    [
        (False, False, "none"),
        (True, False, "check"),
        (True, True, "manage"),
    ],
)
def test_validate_all_passes_expected_argocd_rbac_mode(argocd_check, argocd_manage, expected_mode):
    """Preflight should pass the correct Argo CD RBAC mode to RBAC validation."""
    validator = _build_validator(argocd_check=argocd_check, argocd_manage=argocd_manage)

    with patch("modules.preflight_coordinator.validate_rbac_permissions") as validate_rbac, patch(
        "modules.preflight_coordinator.AutoImportStrategyValidator"
    ) as auto_import_validator:
        auto_import_validator.return_value.run = Mock()
        passed, _config = validator.validate_all()

    assert passed is True
    validate_rbac.assert_called_once_with(
        primary_client=validator.primary,
        secondary_client=validator.secondary,
        include_decommission=False,
        skip_observability=False,
        argocd_mode=expected_mode,
    )
