"""Tests for the modular preflight validator architecture.

These tests verify the module contract - that all validators properly
inherit from BaseValidator and implement the expected interface.
"""

import inspect
import pytest

from modules.preflight.base_validator import BaseValidator
from modules.preflight.reporter import ValidationReporter


# All validator classes that should inherit from BaseValidator
VALIDATOR_CLASSES = [
    ("modules.preflight.backup_validators", "BackupValidator"),
    ("modules.preflight.backup_validators", "BackupScheduleValidator"),
    ("modules.preflight.backup_validators", "PassiveSyncValidator"),
    ("modules.preflight.backup_validators", "ManagedClusterBackupValidator"),
    ("modules.preflight.cluster_validators", "ClusterDeploymentValidator"),
    ("modules.preflight.namespace_validators", "NamespaceValidator"),
    ("modules.preflight.namespace_validators", "ObservabilityDetector"),
    ("modules.preflight.namespace_validators", "ObservabilityPrereqValidator"),
    ("modules.preflight.namespace_validators", "ToolingValidator"),
    ("modules.preflight.version_validators", "KubeconfigValidator"),
    ("modules.preflight.version_validators", "VersionValidator"),
    ("modules.preflight.version_validators", "HubComponentValidator"),
    ("modules.preflight.version_validators", "AutoImportStrategyValidator"),
]


@pytest.mark.parametrize("module_path,class_name", VALIDATOR_CLASSES)
def test_validator_inherits_from_base(module_path, class_name):
    """Verify that all validators properly inherit from BaseValidator.

    This ensures the modular architecture maintains the expected inheritance
    hierarchy, which is critical for consistent result reporting.
    """
    import importlib
    module = importlib.import_module(module_path)
    validator_class = getattr(module, class_name)

    assert issubclass(validator_class, BaseValidator), (
        f"{class_name} must inherit from BaseValidator"
    )


@pytest.mark.parametrize("module_path,class_name", VALIDATOR_CLASSES)
def test_validator_accepts_reporter(module_path, class_name):
    """Verify that all validators can be instantiated with a ValidationReporter.

    This ensures the dependency injection pattern is consistent across validators.
    """
    import importlib
    module = importlib.import_module(module_path)
    validator_class = getattr(module, class_name)

    reporter = ValidationReporter()
    validator = validator_class(reporter)

    assert validator.reporter is reporter, (
        f"{class_name} must store the reporter reference"
    )


def test_base_validator_add_result_propagates_to_reporter():
    """Verify that add_result on BaseValidator correctly updates the reporter.

    This is the core contract that all validators depend on for result collection.
    """
    reporter = ValidationReporter()

    # Create a concrete subclass since BaseValidator is abstract in practice
    class TestValidator(BaseValidator):
        pass

    validator = TestValidator(reporter)

    # Add results with different parameters
    validator.add_result("Critical Check", True, "passed", critical=True)
    validator.add_result("Warning Check", False, "warning", critical=False)

    assert len(reporter.results) == 2

    # Verify first result
    assert reporter.results[0]["check"] == "Critical Check"
    assert reporter.results[0]["passed"] is True
    assert reporter.results[0]["critical"] is True

    # Verify second result
    assert reporter.results[1]["check"] == "Warning Check"
    assert reporter.results[1]["passed"] is False
    assert reporter.results[1]["critical"] is False


def test_validation_reporter_critical_failures_filter():
    """Verify that ValidationReporter correctly filters critical failures.

    This is essential for the switchover to determine if it should proceed.
    """
    reporter = ValidationReporter()

    reporter.add_result("Pass", True, "ok", critical=True)
    reporter.add_result("Critical Fail", False, "error", critical=True)
    reporter.add_result("Warning", False, "warn", critical=False)
    reporter.add_result("Another Pass", True, "ok", critical=True)

    critical_failures = reporter.critical_failures()

    assert len(critical_failures) == 1
    assert critical_failures[0]["check"] == "Critical Fail"
