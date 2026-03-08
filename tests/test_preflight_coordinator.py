"""Unit tests for preflight coordinator wiring."""

from unittest.mock import Mock, patch

import pytest

from lib import argocd as argocd_lib
from lib.exceptions import ValidationError
from modules.preflight_coordinator import PreflightValidator


def _build_validator(argocd_check: bool, argocd_manage: bool, include_decommission: bool = False) -> PreflightValidator:
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
        include_decommission=include_decommission,
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

    discovery = argocd_lib.ArgocdDiscoveryResult(
        has_applications_crd=True,
        has_argocds_crd=False,
        install_type="vanilla",
    )

    with patch("modules.preflight_coordinator.validate_rbac_permissions") as validate_rbac, patch(
        "modules.preflight_coordinator.AutoImportStrategyValidator"
    ) as auto_import_validator, patch(
        "modules.preflight_coordinator.argocd_lib.detect_argocd_installation",
        return_value=discovery,
    ):
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


@pytest.mark.unit
def test_validate_all_skips_argocd_rbac_when_applications_crd_missing():
    """Argo CD RBAC expansion should be skipped when Argo CD is not installed."""
    validator = _build_validator(argocd_check=True, argocd_manage=False)

    discovery = argocd_lib.ArgocdDiscoveryResult(
        has_applications_crd=False,
        has_argocds_crd=False,
        install_type="none",
    )

    with patch("modules.preflight_coordinator.validate_rbac_permissions") as validate_rbac, patch(
        "modules.preflight_coordinator.AutoImportStrategyValidator"
    ) as auto_import_validator, patch(
        "modules.preflight_coordinator.argocd_lib.detect_argocd_installation",
        return_value=discovery,
    ):
        auto_import_validator.return_value.run = Mock()
        passed, _config = validator.validate_all()

    assert passed is True
    validate_rbac.assert_called_once_with(
        primary_client=validator.primary,
        secondary_client=validator.secondary,
        include_decommission=False,
        skip_observability=False,
        argocd_mode="none",
    )


@pytest.mark.unit
def test_validate_all_includes_decommission_permissions_when_requested():
    """Preflight should expand primary-hub RBAC validation for decommission switchovers."""
    validator = _build_validator(argocd_check=False, argocd_manage=False, include_decommission=True)

    with patch("modules.preflight_coordinator.validate_rbac_permissions") as validate_rbac, patch(
        "modules.preflight_coordinator.AutoImportStrategyValidator"
    ) as auto_import_validator:
        auto_import_validator.return_value.run = Mock()
        passed, _config = validator.validate_all()

    assert passed is True
    validate_rbac.assert_called_once_with(
        primary_client=validator.primary,
        secondary_client=validator.secondary,
        include_decommission=True,
        skip_observability=False,
        argocd_mode="none",
    )


@pytest.mark.unit
def test_validate_all_records_validation_error_as_rbac_failure():
    """Expected RBAC validation failures should be reported, not raised."""
    validator = _build_validator(argocd_check=False, argocd_manage=False)

    with patch(
        "modules.preflight_coordinator.validate_rbac_permissions",
        side_effect=ValidationError("missing permissions"),
    ), patch("modules.preflight_coordinator.AutoImportStrategyValidator") as auto_import_validator:
        auto_import_validator.return_value.run = Mock()
        passed, _config = validator.validate_all()

    assert passed is False
    rbac_results = [result for result in validator.reporter.results if result["check"] == "RBAC Permissions"]
    assert len(rbac_results) == 1
    assert rbac_results[0]["passed"] is False
    assert "missing permissions" in rbac_results[0]["message"]


@pytest.mark.unit
def test_validate_all_does_not_swallow_unexpected_rbac_errors():
    """Unexpected errors during RBAC validation should propagate."""
    validator = _build_validator(argocd_check=False, argocd_manage=False)

    with patch(
        "modules.preflight_coordinator.validate_rbac_permissions",
        side_effect=RuntimeError("bug"),
    ), patch("modules.preflight_coordinator.AutoImportStrategyValidator") as auto_import_validator:
        auto_import_validator.return_value.run = Mock()
        with pytest.raises(RuntimeError, match="bug"):
            validator.validate_all()
