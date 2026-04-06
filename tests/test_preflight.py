"""Unit tests for preflight validation helpers.

Modernized pytest tests with fixtures, markers, and comprehensive coverage
of validation reporters and validators.
"""

from unittest.mock import Mock, patch

import pytest

from lib.constants import ACM_NAMESPACE, BACKUP_NAMESPACE
from modules.preflight import (
    NamespaceValidator,
    ObservabilityDetector,
    ValidationReporter,
)


@pytest.fixture
def reporter():
    """Create a ValidationReporter instance."""
    return ValidationReporter()


@pytest.mark.unit
class TestValidationReporter:
    """Tests for the ValidationReporter helper."""

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

