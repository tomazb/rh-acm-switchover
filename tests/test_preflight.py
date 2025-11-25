"""Unit tests for preflight validation helpers.

Modernized pytest tests with fixtures, markers, and comprehensive coverage
of validation reporters and validators.
"""

import pytest
from unittest.mock import Mock, patch

from modules.preflight_validators import (
    ValidationReporter,
    NamespaceValidator,
)
from lib.constants import ACM_NAMESPACE, BACKUP_NAMESPACE


@pytest.fixture
def reporter():
    """Create a ValidationReporter instance."""
    return ValidationReporter()


@pytest.fixture
def mock_kube_client():
    """Create a mock KubeClient."""
    return Mock()


@pytest.mark.unit
class TestValidationReporter:
    """Tests for the ValidationReporter helper."""

    def test_add_result_passed(self, reporter):
        """Test adding a passing validation result."""
        reporter.add_result("demo", True, "all good", critical=True)

        assert len(reporter.results) == 1
        result = reporter.results[0]
        assert result["check"] == "demo"
        assert result["passed"] is True
        assert result["message"] == "all good"
        assert result["critical"] is True

    def test_add_result_warning(self, reporter):
        """Test adding a warning (non-critical failure) result."""
        reporter.add_result("demo", False, "warn", critical=False)

        result = reporter.results[0]
        assert result["passed"] is False
        assert result["critical"] is False

    def test_critical_failures(self, reporter):
        """Test filtering critical failures."""
        reporter.add_result("ok", True, "fine", critical=True)
        reporter.add_result("bad", False, "nope", critical=True)
        reporter.add_result("warn", False, "heads up", critical=False)

        failures = reporter.critical_failures()
        assert len(failures) == 1
        assert failures[0]["check"] == "bad"

    def test_multiple_results(self, reporter):
        """Test adding multiple results."""
        reporter.add_result("check1", True, "pass")
        reporter.add_result("check2", False, "fail", critical=True)
        reporter.add_result("check3", True, "pass")

        assert len(reporter.results) == 3
        assert len(reporter.critical_failures()) == 1

    @patch("modules.preflight_validators.logger")
    def test_print_summary_all_passed(self, mock_logger, reporter):
        """Test summary when all checks pass."""
        reporter.add_result("check1", True, "ok", critical=True)
        reporter.add_result("check2", True, "ok", critical=True)

        reporter.print_summary()

        # Verify info log calls
        assert any(
            "2/2 checks passed" in str(call) for call in mock_logger.info.call_args_list
        )

    @patch("modules.preflight_validators.logger")
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

    def test_namespace_exists_on_both_hubs(self, reporter, mock_kube_client):
        """Test validation when namespaces exist on both hubs."""
        primary = Mock()
        secondary = Mock()
        primary.namespace_exists.return_value = True
        secondary.namespace_exists.return_value = True

        validator = NamespaceValidator(reporter)
        validator.run(primary, secondary)

        # Should have results for both namespaces on both hubs
        assert len(reporter.results) == 4  # 2 namespaces × 2 hubs
        assert all(r["passed"] for r in reporter.results)

    def test_namespace_missing_on_primary(self, reporter):
        """Test validation when namespace is missing on primary hub."""
        primary = Mock()
        secondary = Mock()
        primary.namespace_exists.return_value = False
        secondary.namespace_exists.return_value = True

        validator = NamespaceValidator(reporter)
        validator.run(primary, secondary)

        # Should have failures for primary hub
        failures = [r for r in reporter.results if not r["passed"]]
        assert len(failures) == 2  # 2 namespaces missing on primary
        assert all("primary" in r["check"] for r in failures)

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
