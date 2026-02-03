"""Unit tests for lib/gitops_detector.py.

Tests cover GitOps marker detection, the GitOpsCollector singleton,
and the convenience function for recording markers.
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from lib.gitops_detector import (
    GitOpsCollector,
    detect_gitops_markers,
    record_gitops_markers,
)


# Path to test fixtures
FIXTURES_DIR = Path(__file__).parent / "fixtures" / "gitops_markers"


def load_fixture(name: str) -> dict:
    """Load a JSON fixture file."""
    with open(FIXTURES_DIR / name) as f:
        return json.load(f)


@pytest.fixture(autouse=True)
def reset_collector():
    """Reset the GitOpsCollector singleton before each test."""
    GitOpsCollector.reset()
    yield
    GitOpsCollector.reset()


@pytest.mark.unit
class TestDetectGitopsMarkers:
    """Test cases for detect_gitops_markers function."""

    def test_detects_argocd_labels(self):
        """Test detection of ArgoCD labels."""
        metadata = {
            "labels": {
                "app.kubernetes.io/managed-by": "argocd",
                "argocd.argoproj.io/instance": "my-app",
            }
        }
        markers = detect_gitops_markers(metadata)
        assert "label:app.kubernetes.io/managed-by" in markers
        assert "label:argocd.argoproj.io/instance" in markers
        assert len(markers) == 2

    def test_detects_instance_label_as_unreliable(self):
        """Test detection of generic instance label as unreliable."""
        metadata = {
            "labels": {
                "app.kubernetes.io/instance": "my-app",
            }
        }
        markers = detect_gitops_markers(metadata)
        assert "label:app.kubernetes.io/instance (UNRELIABLE)" in markers
        assert len(markers) == 1

    def test_detects_argocd_annotations(self):
        """Test detection of ArgoCD annotations."""
        metadata = {
            "annotations": {
                "argocd.argoproj.io/sync-wave": "5",
                "argocd.argoproj.io/compare-options": "IgnoreExtraneous",
            }
        }
        markers = detect_gitops_markers(metadata)
        assert "annotation:argocd.argoproj.io/sync-wave" in markers
        assert "annotation:argocd.argoproj.io/compare-options" in markers
        assert len(markers) == 2

    def test_detects_flux_labels(self):
        """Test detection of Flux labels."""
        metadata = {
            "labels": {
                "app.kubernetes.io/managed-by": "flux",
                "kustomize.toolkit.fluxcd.io/name": "my-app",
                "kustomize.toolkit.fluxcd.io/namespace": "flux-system",
            }
        }
        markers = detect_gitops_markers(metadata)
        assert "label:app.kubernetes.io/managed-by" in markers
        assert "label:kustomize.toolkit.fluxcd.io/name" in markers
        assert "label:kustomize.toolkit.fluxcd.io/namespace" in markers
        assert len(markers) == 3

    def test_detects_flux_annotations(self):
        """Test detection of Flux annotations."""
        metadata = {
            "annotations": {
                "fluxcd.io/automated": "true",
            }
        }
        markers = detect_gitops_markers(metadata)
        assert "annotation:fluxcd.io/automated" in markers
        assert len(markers) == 1

    def test_detects_fluxcd_as_managed_by(self):
        """Test detection of 'fluxcd' as managed-by value."""
        metadata = {
            "labels": {
                "app.kubernetes.io/managed-by": "fluxcd",
            }
        }
        markers = detect_gitops_markers(metadata)
        assert "label:app.kubernetes.io/managed-by" in markers
        assert len(markers) == 1

    def test_managed_by_substring_not_matched(self):
        """Test that managed-by substring values do not match."""
        metadata = {
            "labels": {
                "app.kubernetes.io/managed-by": "not-argocd",
            }
        }
        markers = detect_gitops_markers(metadata)
        assert markers == []

    def test_managed_by_with_argocd_label_still_detects_label(self):
        """Test that argocd labels are detected even if managed-by value is non-matching."""
        metadata = {
            "labels": {
                "app.kubernetes.io/managed-by": "not-argocd",
                "argocd.argoproj.io/instance": "my-app",
            }
        }
        markers = detect_gitops_markers(metadata)
        assert "label:argocd.argoproj.io/instance" in markers
        assert "label:app.kubernetes.io/managed-by" not in markers

    def test_returns_empty_for_unmarked_resource(self):
        """Test that unmarked resources return empty list."""
        metadata = {
            "labels": {
                "app.kubernetes.io/name": "my-app",
                "app.kubernetes.io/version": "1.0.0",
            },
            "annotations": {
                "description": "My application",
            }
        }
        markers = detect_gitops_markers(metadata)
        assert markers == []

    def test_returns_empty_for_empty_metadata(self):
        """Test with empty metadata."""
        markers = detect_gitops_markers({})
        assert markers == []

    def test_returns_empty_for_none_labels_annotations(self):
        """Test with None labels and annotations."""
        metadata = {
            "labels": None,
            "annotations": None,
        }
        markers = detect_gitops_markers(metadata)
        assert markers == []

    def test_case_insensitive_detection(self):
        """Test that detection is case-insensitive for values."""
        metadata = {
            "labels": {
                "app.kubernetes.io/managed-by": "ArgoCD",  # Capital letters
            },
            "annotations": {
                "ArgoCD.argoproj.io/sync-wave": "1",  # Capital in key
            }
        }
        markers = detect_gitops_markers(metadata)
        assert "label:app.kubernetes.io/managed-by" in markers
        # Key is preserved as-is but still detected
        assert "annotation:ArgoCD.argoproj.io/sync-wave" in markers

    def test_fixture_argocd_backupschedule(self):
        """Test with ArgoCD BackupSchedule fixture."""
        fixture = load_fixture("argocd_backupschedule.json")
        markers = detect_gitops_markers(fixture["metadata"])

        assert "label:app.kubernetes.io/managed-by" in markers
        assert "label:argocd.argoproj.io/instance" in markers
        assert "annotation:argocd.argoproj.io/sync-wave" in markers
        assert "annotation:argocd.argoproj.io/compare-options" in markers
        assert len(markers) == 4

    def test_fixture_flux_mco(self):
        """Test with Flux MCO fixture."""
        fixture = load_fixture("flux_mco.json")
        markers = detect_gitops_markers(fixture["metadata"])

        assert "label:app.kubernetes.io/managed-by" in markers
        assert "label:kustomize.toolkit.fluxcd.io/name" in markers
        assert "label:kustomize.toolkit.fluxcd.io/namespace" in markers
        assert "annotation:fluxcd.io/automated" in markers
        assert len(markers) == 4

    def test_fixture_unmarked_mch(self):
        """Test with unmarked MCH fixture."""
        fixture = load_fixture("unmarked_mch.json")
        markers = detect_gitops_markers(fixture["metadata"])

        assert markers == []


@pytest.mark.unit
class TestGitOpsCollector:
    """Test cases for GitOpsCollector singleton."""

    def test_singleton_instance(self):
        """Test that GitOpsCollector is a singleton."""
        collector1 = GitOpsCollector.get_instance()
        collector2 = GitOpsCollector.get_instance()
        assert collector1 is collector2

    def test_record_stores_markers(self):
        """Test that record() stores markers correctly."""
        collector = GitOpsCollector.get_instance()
        collector.record(
            context="primary",
            namespace="open-cluster-management-backup",
            kind="BackupSchedule",
            name="acm-backup-schedule",
            markers=["label:app.kubernetes.io/managed-by"],
        )

        assert collector.has_detections()
        assert collector.get_detection_count() == 1

        records = collector.get_records()
        assert "primary" in records
        key = ("open-cluster-management-backup", "BackupSchedule", "acm-backup-schedule")
        assert key in records["primary"]
        assert records["primary"][key] == ["label:app.kubernetes.io/managed-by"]

    def test_record_ignores_empty_markers(self):
        """Test that record() ignores empty marker lists."""
        collector = GitOpsCollector.get_instance()
        collector.record(
            context="primary",
            namespace="ns",
            kind="Kind",
            name="name",
            markers=[],
        )

        assert not collector.has_detections()
        assert collector.get_detection_count() == 0

    def test_record_multiple_contexts(self):
        """Test recording from multiple contexts."""
        collector = GitOpsCollector.get_instance()

        collector.record(
            context="primary",
            namespace="ns1",
            kind="Kind1",
            name="resource1",
            markers=["label:test1"],
        )
        collector.record(
            context="secondary",
            namespace="ns2",
            kind="Kind2",
            name="resource2",
            markers=["annotation:test2"],
        )

        assert collector.get_detection_count() == 2
        records = collector.get_records()
        assert "primary" in records
        assert "secondary" in records

    def test_set_enabled_disabled(self):
        """Test enabling/disabling the collector."""
        collector = GitOpsCollector.get_instance()

        # Disable
        collector.set_enabled(False)
        assert not collector.is_enabled()

        # Records should be ignored when disabled
        collector.record(
            context="primary",
            namespace="ns",
            kind="Kind",
            name="name",
            markers=["label:test"],
        )
        assert not collector.has_detections()

        # Re-enable
        collector.set_enabled(True)
        assert collector.is_enabled()

        # Now records should be stored
        collector.record(
            context="primary",
            namespace="ns",
            kind="Kind",
            name="name",
            markers=["label:test"],
        )
        assert collector.has_detections()

    def test_reset_clears_state(self):
        """Test that reset() clears all state."""
        collector = GitOpsCollector.get_instance()
        collector.set_enabled(False)
        collector.record(
            context="primary",
            namespace="ns",
            kind="Kind",
            name="name",
            markers=["label:test"],
        )

        GitOpsCollector.reset()
        collector = GitOpsCollector.get_instance()

        assert collector.is_enabled()
        assert not collector.has_detections()

    @patch("lib.gitops_detector.logger")
    def test_print_report_when_disabled(self, mock_logger):
        """Test that print_report() does nothing when disabled."""
        collector = GitOpsCollector.get_instance()
        collector.set_enabled(False)
        collector.print_report()

        mock_logger.warning.assert_not_called()

    @patch("lib.gitops_detector.logger")
    def test_print_report_when_no_detections(self, mock_logger):
        """Test that print_report() does nothing when no detections."""
        collector = GitOpsCollector.get_instance()
        collector.print_report()

        mock_logger.warning.assert_not_called()

    @patch("lib.gitops_detector.logger")
    def test_print_report_single_detection(self, mock_logger):
        """Test print_report() with a single detection."""
        collector = GitOpsCollector.get_instance()
        collector.record(
            context="primary",
            namespace="open-cluster-management-backup",
            kind="BackupSchedule",
            name="acm-backup-schedule",
            markers=["label:app.kubernetes.io/managed-by"],
        )

        collector.print_report()

        # Check that logger.warning was called
        assert mock_logger.warning.called
        # Reconstruct all logged messages from calls
        logged_messages = []
        for call in mock_logger.warning.call_args_list:
            args = call[0]
            if len(args) > 1:
                # Format string with arguments
                logged_messages.append(args[0] % args[1:])
            else:
                logged_messages.append(args[0])

        full_output = "\n".join(logged_messages)
        assert "GitOps-related markers detected (1 warning)" in full_output
        assert "[primary]" in full_output
        assert "BackupSchedule" in full_output

    @patch("lib.gitops_detector.logger")
    def test_print_report_multiple_detections(self, mock_logger):
        """Test print_report() with multiple detections."""
        collector = GitOpsCollector.get_instance()

        # Add multiple resources
        for i in range(3):
            collector.record(
                context="primary",
                namespace="ns",
                kind="Kind",
                name=f"resource{i}",
                markers=[f"label:marker{i}"],
            )

        collector.print_report()

        # Reconstruct all logged messages from calls
        logged_messages = []
        for call in mock_logger.warning.call_args_list:
            args = call[0]
            if len(args) > 1:
                logged_messages.append(args[0] % args[1:])
            else:
                logged_messages.append(args[0])

        full_output = "\n".join(logged_messages)
        assert "GitOps-related markers detected (3 warnings)" in full_output

    @patch("lib.gitops_detector.logger")
    def test_print_report_truncates_long_lists(self, mock_logger):
        """Test that print_report() truncates lists with more than MAX_DISPLAY_PER_KIND."""
        collector = GitOpsCollector.get_instance()

        # Add 15 ManagedClusters (more than MAX_DISPLAY_PER_KIND=10)
        for i in range(15):
            collector.record(
                context="secondary",
                namespace="",
                kind="ManagedCluster",
                name=f"cluster{i}",
                markers=["label:app.kubernetes.io/managed-by"],
            )

        collector.print_report()

        # Reconstruct all logged messages from calls
        logged_messages = []
        for call in mock_logger.warning.call_args_list:
            args = call[0]
            if len(args) > 1:
                logged_messages.append(args[0] % args[1:])
            else:
                logged_messages.append(args[0])

        full_output = "\n".join(logged_messages)
        # Should show "and 5 more ManagedCluster(s)"
        assert "and 5 more ManagedCluster(s)" in full_output

    @patch("lib.gitops_detector.logger")
    def test_print_report_cluster_scoped_resource(self, mock_logger):
        """Test print_report() formats cluster-scoped resources correctly."""
        collector = GitOpsCollector.get_instance()
        collector.record(
            context="primary",
            namespace="",  # Empty namespace for cluster-scoped
            kind="ManagedCluster",
            name="cluster1",
            markers=["label:app.kubernetes.io/managed-by"],
        )

        collector.print_report()

        calls = [str(call) for call in mock_logger.warning.call_args_list]
        # Should show "ManagedCluster/cluster1" without namespace prefix
        assert any("ManagedCluster/cluster1" in str(call) for call in calls)


@pytest.mark.unit
class TestRecordGitopsMarkers:
    """Test cases for record_gitops_markers convenience function."""

    def test_detects_and_records_markers(self):
        """Test that function detects and records markers."""
        metadata = {
            "labels": {
                "app.kubernetes.io/managed-by": "argocd",
            }
        }

        markers = record_gitops_markers(
            context="primary",
            namespace="ns",
            kind="Kind",
            name="resource",
            metadata=metadata,
        )

        assert markers == ["label:app.kubernetes.io/managed-by"]
        assert GitOpsCollector.get_instance().has_detections()

    def test_returns_empty_for_unmarked(self):
        """Test that function returns empty list for unmarked resources."""
        metadata = {
            "labels": {
                "app": "test",
            }
        }

        markers = record_gitops_markers(
            context="primary",
            namespace="ns",
            kind="Kind",
            name="resource",
            metadata=metadata,
        )

        assert markers == []
        assert not GitOpsCollector.get_instance().has_detections()

    def test_respects_collector_disabled(self):
        """Test that function returns empty when collector is disabled."""
        GitOpsCollector.get_instance().set_enabled(False)

        metadata = {
            "labels": {
                "app.kubernetes.io/managed-by": "argocd",
            }
        }

        markers = record_gitops_markers(
            context="primary",
            namespace="ns",
            kind="Kind",
            name="resource",
            metadata=metadata,
        )

        # When disabled, no markers should be returned (suppresses inline warnings)
        assert markers == []
        # And nothing should be recorded
        assert not GitOpsCollector.get_instance().has_detections()
