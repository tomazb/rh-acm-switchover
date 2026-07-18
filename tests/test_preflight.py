"""Unit tests for preflight validation helpers.

Modernized pytest tests with fixtures, markers, and comprehensive coverage
of validation reporters and validators.
"""

from unittest.mock import Mock, patch

import pytest
from kubernetes.client.rest import ApiException

from lib.constants import ACM_NAMESPACE, BACKUP_NAMESPACE
from modules.preflight import (
    NamespaceValidator,
    ObservabilityDetector,
    ValidationReporter,
)
from modules.preflight_coordinator import PreflightValidator

SENSITIVE_LOG_VALUES = (
    "Authorization: Bearer secret-token",
    "token: secret-token",
    "client-key-data: c2VjcmV0LWtleQ==",
    "/home/operator/.kube/production-config",
    "https://api.internal.example:6443",
    "ApiException: user=cluster-admin password=secret-password",
    "context=production-admin",
    "user=system:admin",
    "injected-log-entry",
    "\x1b[31m",
)
SENSITIVE_VALIDATION_MESSAGE = "\n".join(SENSITIVE_LOG_VALUES)


@pytest.fixture
def reporter():
    """Create a ValidationReporter instance."""
    return ValidationReporter()


@pytest.mark.unit
class TestValidationReporter:
    """Tests for the ValidationReporter helper."""

    @pytest.mark.parametrize(
        ("passed", "critical", "expected_status"),
        [
            (True, True, "passed"),
            (False, True, "failed"),
            (False, False, "warning"),
        ],
    )
    def test_result_logs_public_status_without_private_message(
        self,
        caplog,
        reporter,
        passed,
        critical,
        expected_status,
    ):
        """Passed, failed, and warning logs must omit private diagnostics."""
        with caplog.at_level("INFO", logger="acm_switchover"):
            reporter.add_result(
                "Backup status",
                passed,
                SENSITIVE_VALIDATION_MESSAGE,
                critical=critical,
            )

        log_text = "\n".join(record.getMessage() for record in caplog.records)
        assert "Backup status" in log_text
        assert expected_status in log_text
        assert all(value not in log_text for value in SENSITIVE_LOG_VALUES)

        assert reporter.results == [
            {
                "check": "Backup status",
                "passed": passed,
                "message": SENSITIVE_VALIDATION_MESSAGE,
                "critical": critical,
            }
        ]

    def test_summary_logs_public_category_without_private_message(self, caplog, reporter):
        """Summary logging must not re-publish stored failure diagnostics."""
        reporter.add_result(
            "RBAC Permissions",
            False,
            SENSITIVE_VALIDATION_MESSAGE,
            critical=True,
        )
        caplog.clear()

        with caplog.at_level("INFO", logger="acm_switchover"):
            reporter.print_summary()

        log_text = "\n".join(record.getMessage() for record in caplog.records)
        assert "RBAC permissions" in log_text
        assert "failed" in log_text
        assert all(value not in log_text for value in SENSITIVE_LOG_VALUES)

    @pytest.mark.parametrize(
        "untrusted_check",
        [
            "unknown context=production-admin\ninjected-log-entry\x1b[31m",
            "x" * 1024,
            "Backup status\nAuthorization: Bearer secret-token",
        ],
    )
    def test_untrusted_check_labels_use_opaque_fallback(self, caplog, reporter, untrusted_check):
        """Unapproved, oversized, or control-bearing labels cannot enter logs."""
        with caplog.at_level("INFO", logger="acm_switchover"):
            reporter.add_result(untrusted_check, False, "private detail", critical=True)

        log_text = "\n".join(record.getMessage() for record in caplog.records)
        assert "Preflight validation" in log_text
        assert untrusted_check not in log_text
        assert "injected-log-entry" not in log_text
        assert "secret-token" not in log_text
        assert "\x1b" not in log_text

    def test_result_order_and_critical_filtering_are_unchanged(self, reporter):
        """Logging hardening must preserve the decision-engine result contract."""
        reporter.add_result("Backup status", True, "success", critical=True)
        reporter.add_result("RBAC Permissions", False, "critical detail", critical=True)
        reporter.add_result("GitOps advisory", False, "warning detail", critical=False)

        assert [result["check"] for result in reporter.results] == [
            "Backup status",
            "RBAC Permissions",
            "GitOps advisory",
        ]
        assert reporter.critical_failures() == [reporter.results[1]]

    def test_critical_failures(self, reporter):
        """Test filtering critical failures."""
        reporter.add_result("ok", True, "fine", critical=True)
        reporter.add_result("bad", False, "nope", critical=True)
        reporter.add_result("warn", False, "heads up", critical=False)

        failures = reporter.critical_failures()
        assert len(failures) == 1
        assert failures[0]["check"] == "bad"

    @patch("modules.preflight.reporter.logger")
    def test_print_summary_all_passed(self, mock_logger, reporter):
        """Test summary when all checks pass."""
        reporter.add_result("check1", True, "ok", critical=True)
        reporter.add_result("check2", True, "ok", critical=True)

        reporter.print_summary()

        # Verify info log calls
        assert any("2/2 checks passed" in str(call) for call in mock_logger.info.call_args_list)

    @patch("modules.preflight.reporter.logger")
    def test_print_summary_with_failures(self, mock_logger, reporter):
        """Test summary when there are critical failures."""
        reporter.add_result("check1", True, "ok", critical=True)
        reporter.add_result("check2", False, "failed", critical=True)

        reporter.print_summary()

        # Verify error log calls for failures
        assert mock_logger.error.called


@pytest.mark.unit
class TestNamespaceValidator:
    """Tests for the NamespaceValidator."""

    def test_namespace_missing_on_secondary(self, reporter):
        """Test validation when namespace is missing on secondary hub."""
        primary = Mock()
        secondary = Mock()
        primary.namespace_exists.return_value = True
        secondary.namespace_exists.return_value = False

        validator = NamespaceValidator(reporter)
        validator.run(primary, secondary)

        # Should have failures for secondary hub
        failures = [r for r in reporter.results if not r["passed"]]
        assert len(failures) == 2  # 2 namespaces missing on secondary
        assert all("secondary" in r["check"] for r in failures)

    def test_required_namespaces_checked(self, reporter):
        """Test that all required namespaces are checked."""
        primary = Mock()
        secondary = Mock()
        primary.namespace_exists.return_value = True
        secondary.namespace_exists.return_value = True

        validator = NamespaceValidator(reporter)
        validator.run(primary, secondary)

        # Verify ACM and BACKUP namespaces are checked
        check_names = [r["check"] for r in reporter.results]
        assert any(ACM_NAMESPACE in check for check in check_names)
        assert any(BACKUP_NAMESPACE in check for check in check_names)


@pytest.mark.unit
class TestObservabilityDetector:
    """Tests for the ObservabilityDetector."""

    @pytest.mark.parametrize(
        "primary_has,secondary_has,expected_message",
        [
            (True, True, "detected on both hubs"),
            (True, False, "detected on primary hub only"),
            (False, True, "detected on secondary hub only"),
            (False, False, "not detected (optional component)"),
        ],
    )
    def test_detect_reports_per_hub_presence(self, reporter, primary_has, secondary_has, expected_message):
        primary = Mock()
        secondary = Mock()
        primary.namespace_exists.return_value = primary_has
        secondary.namespace_exists.return_value = secondary_has

        detector = ObservabilityDetector(reporter)
        result = detector.detect(primary, secondary)

        assert result == (primary_has, secondary_has)
        assert reporter.results[-1]["message"] == expected_message


@pytest.mark.unit
def test_preflight_fails_closed_when_argocd_crd_discovery_is_unauthorized(caplog):
    """Unauthorized Argo CD CRD discovery must fail preflight instead of being skipped."""
    primary = Mock()
    secondary = Mock()
    primary.namespace_exists.return_value = True
    secondary.namespace_exists.return_value = True
    primary.list_custom_resources.return_value = []

    validator = PreflightValidator(
        primary_client=primary,
        secondary_client=secondary,
        method="passive",
        skip_rbac_validation=False,
    )

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

    private_api_reason = "Authorization: Bearer secret-token\ncontext=production-admin"
    with caplog.at_level("INFO", logger="acm_switchover"):
        with patch("modules.preflight_coordinator.validate_rbac_permissions") as validate_rbac, patch(
            "modules.preflight_coordinator.AutoImportStrategyValidator"
        ) as auto_import_validator, patch(
            "modules.preflight_coordinator.argocd_lib.detect_argocd_installation",
            side_effect=ApiException(status=401, reason=private_api_reason),
        ):
            auto_import_validator.return_value.run = Mock()
            passed, _config = validator.validate_all()

    assert passed is False
    validate_rbac.assert_not_called()
    rbac_results = [result for result in validator.reporter.results if result["check"] == "RBAC Permissions"]
    assert len(rbac_results) == 1
    assert rbac_results[0]["passed"] is False
    assert private_api_reason in rbac_results[0]["message"]
    log_text = "\n".join(record.getMessage() for record in caplog.records)
    for sentinel in ("secret-token", "production-admin"):
        assert sentinel not in log_text
