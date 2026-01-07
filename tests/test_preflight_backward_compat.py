"""Tests for backward compatibility layer.

These tests verify that the old preflight_validators module
still works as expected with deprecation warnings.
"""

from modules.preflight_validators import (
    BackupValidator,
    NamespaceValidator,
    ValidationReporter,
    VersionValidator,
)


def test_backward_compatibility_imports_with_warning():
    """Test that importing from preflight_validators shows deprecation warning."""
    import warnings
    import importlib
    import sys

    # Remove from cache to re-trigger the deprecation warning
    if "modules.preflight_validators" in sys.modules:
        del sys.modules["modules.preflight_validators"]

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        import modules.preflight_validators
        importlib.reload(modules.preflight_validators)

        # Verify deprecation warning was raised
        deprecation_warnings = [x for x in w if issubclass(x.category, DeprecationWarning)]
        assert len(deprecation_warnings) >= 1, "Expected at least one DeprecationWarning"
        assert "deprecated" in str(deprecation_warnings[0].message).lower()

    # Test that we can import the classes we need
    from modules.preflight_validators import ValidationReporter, BackupValidator

    # Basic verification that the classes are the same
    from modules.preflight import ValidationReporter as NewValidationReporter
    from modules.preflight.backup_validators import BackupValidator as NewBackupValidator

    assert ValidationReporter is NewValidationReporter
    assert BackupValidator is NewBackupValidator


def test_backward_compatibility_classes_work():
    """Test that classes imported via backward compatibility work correctly."""
    reporter = ValidationReporter()

    # Test that classes can be instantiated
    backup_validator = BackupValidator(reporter)
    assert backup_validator is not None
    assert hasattr(backup_validator, 'run')

    namespace_validator = NamespaceValidator(reporter)
    assert namespace_validator is not None
    assert hasattr(namespace_validator, 'run')

    version_validator = VersionValidator(reporter)
    assert version_validator is not None
    assert hasattr(version_validator, 'run')


def test_backward_compatibility_same_classes():
    """Test that classes from old and new imports are identical."""
    # Import from old location
    from modules.preflight_validators import ValidationReporter as OldValidationReporter
    from modules.preflight_validators import BackupValidator as OldBackupValidator

    # Import from new location
    from modules.preflight import ValidationReporter as NewValidationReporter
    from modules.preflight.backup_validators import BackupValidator as NewBackupValidator

    # They should be the same classes
    assert OldValidationReporter is NewValidationReporter
    assert OldBackupValidator is NewBackupValidator


def test_all_validators_importable():
    """Test that all validators can be imported from backward compat layer."""
    # Dynamically verify all expected symbols are re-exported
    from modules import preflight_validators

    expected_symbols = [
        'AutoImportStrategyValidator',
        'BackupScheduleValidator',
        'BackupValidator',
        'ClusterDeploymentValidator',
        'HubComponentValidator',
        'KubeconfigValidator',
        'ManagedClusterBackupValidator',
        'NamespaceValidator',
        'ObservabilityDetector',
        'ObservabilityPrereqValidator',
        'PassiveSyncValidator',
        'ToolingValidator',
        'ValidationReporter',
        'VersionValidator',
    ]

    for symbol in expected_symbols:
        assert hasattr(preflight_validators, symbol), f"{symbol} not exported from preflight_validators"
        cls = getattr(preflight_validators, symbol)
        assert cls is not None, f"{symbol} is None"
