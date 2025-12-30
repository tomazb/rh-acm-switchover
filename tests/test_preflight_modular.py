"""Tests for individual validator modules in the new modular structure.

These tests verify that each validator module can be imported
and used independently, ensuring the modular decomposition works correctly.
"""

import pytest

# Test that each module can be imported
def test_import_backup_validators():
    """Test that backup_validators module can be imported."""
    from modules.preflight import backup_validators
    assert backup_validators.BackupValidator is not None
    assert backup_validators.BackupScheduleValidator is not None
    assert backup_validators.PassiveSyncValidator is not None
    assert backup_validators.ManagedClusterBackupValidator is not None


def test_import_cluster_validators():
    """Test that cluster_validators module can be imported."""
    from modules.preflight import cluster_validators
    assert cluster_validators.ClusterDeploymentValidator is not None


def test_import_namespace_validators():
    """Test that namespace_validators module can be imported."""
    from modules.preflight import namespace_validators
    assert namespace_validators.NamespaceValidator is not None
    assert namespace_validators.ObservabilityDetector is not None
    assert namespace_validators.ObservabilityPrereqValidator is not None
    assert namespace_validators.ToolingValidator is not None


def test_import_version_validators():
    """Test that version_validators module can be imported."""
    from modules.preflight import version_validators
    assert version_validators.KubeconfigValidator is not None
    assert version_validators.VersionValidator is not None
    assert version_validators.HubComponentValidator is not None
    assert version_validators.AutoImportStrategyValidator is not None


def test_import_base_validator():
    """Test that base_validator module can be imported."""
    from modules.preflight import base_validator
    assert base_validator.BaseValidator is not None


def test_import_reporter():
    """Test that reporter module can be imported."""
    from modules.preflight import reporter
    assert reporter.ValidationReporter is not None




def test_all_validator_classes_instantiate():
    """Test that all validator classes can be instantiated with ValidationReporter."""
    from modules.preflight import reporter
    from modules.preflight import backup_validators
    from modules.preflight import cluster_validators
    from modules.preflight import namespace_validators
    from modules.preflight import version_validators
    
    validation_reporter = reporter.ValidationReporter()
    
    # Test instantiation of backup validators
    backup_validator = backup_validators.BackupValidator(validation_reporter)
    assert backup_validator is not None
    assert backup_validator.reporter is validation_reporter
    
    # Test instantiation of cluster validators
    cluster_validator = cluster_validators.ClusterDeploymentValidator(validation_reporter)
    assert cluster_validator is not None
    assert cluster_validator.reporter is validation_reporter
    
    # Test instantiation of namespace validators
    namespace_validator = namespace_validators.NamespaceValidator(validation_reporter)
    assert namespace_validator is not None
    assert namespace_validator.reporter is validation_reporter
    
    # Test instantiation of version validators
    version_validator = version_validators.VersionValidator(validation_reporter)
    assert version_validator is not None
    assert version_validator.reporter is validation_reporter
