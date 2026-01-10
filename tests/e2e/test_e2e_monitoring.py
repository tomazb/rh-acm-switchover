"""
Tests for E2E Monitoring Module.

This module contains tests for the resource monitoring and metrics logging
functionality in the E2E testing framework.
"""

import json
import pytest
import threading
import time
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from tests.e2e.monitoring import (
    Alert,
    AlertThresholds,
    MetricsLogger,
    ResourceMonitor,
    ResourceSnapshot,
    MonitoringContext,
)


def make_datetime(iso_str: str) -> datetime:
    """Helper to create datetime objects from ISO strings."""
    return datetime.fromisoformat(iso_str.replace("Z", "+00:00"))


@pytest.mark.e2e
class TestAlert:
    """Tests for Alert dataclass."""

    def test_alert_creation(self):
        """Test creating an Alert."""
        alert = Alert(
            alert_type="CLUSTER_UNAVAILABLE",
            hub_type="primary",
            resource="managed-cluster-1",
            message="Cluster unavailable for 300s",
        )

        assert alert.alert_type == "CLUSTER_UNAVAILABLE"
        assert alert.hub_type == "primary"
        assert alert.resource == "managed-cluster-1"
        assert alert.timestamp is not None

    def test_alert_to_dict(self):
        """Test Alert serialization."""
        alert = Alert(
            alert_type="BACKUP_FAILURE",
            hub_type="primary",
            resource="backup-schedule",
            message="Backup failing for 600s",
            phase="activation",
        )

        result = alert.to_dict()

        assert isinstance(result, dict)
        assert result["alert_type"] == "BACKUP_FAILURE"
        assert result["hub_type"] == "primary"
        assert result["phase"] == "activation"
        assert "timestamp" in result


@pytest.mark.e2e
class TestAlertThresholds:
    """Tests for AlertThresholds configuration."""

    def test_default_thresholds(self):
        """Test default threshold values."""
        thresholds = AlertThresholds()

        assert thresholds.cluster_unavailable_seconds == 300  # 5 min
        assert thresholds.backup_failure_seconds == 600  # 10 min
        assert thresholds.restore_stalled_seconds == 900  # 15 min

    def test_custom_thresholds(self):
        """Test custom threshold values."""
        thresholds = AlertThresholds(
            cluster_unavailable_seconds=60,
            backup_failure_seconds=120,
            restore_stalled_seconds=180,
        )

        assert thresholds.cluster_unavailable_seconds == 60
        assert thresholds.backup_failure_seconds == 120
        assert thresholds.restore_stalled_seconds == 180


@pytest.mark.e2e
class TestResourceSnapshot:
    """Tests for ResourceSnapshot dataclass."""

    def test_snapshot_creation(self):
        """Test creating a ResourceSnapshot."""
        timestamp = datetime.now(timezone.utc)
        snapshot = ResourceSnapshot(
            timestamp=timestamp,
            hub_type="primary",
            managed_clusters=[
                {"name": "cluster1", "available": "True"},
                {"name": "cluster2", "available": "False"},
            ],
        )

        assert snapshot.hub_type == "primary"
        assert len(snapshot.managed_clusters) == 2

    def test_snapshot_to_dict(self):
        """Test ResourceSnapshot serialization."""
        timestamp = datetime.now(timezone.utc)
        snapshot = ResourceSnapshot(
            timestamp=timestamp,
            hub_type="secondary",
            restores=[{"name": "restore-1", "phase": "InProgress"}],
        )

        result = snapshot.to_dict()

        assert isinstance(result, dict)
        assert result["hub_type"] == "secondary"
        assert len(result["restores"]) == 1


@pytest.mark.e2e
class TestMetricsLogger:
    """Tests for MetricsLogger class."""

    def test_metrics_file_creation(self, tmp_path):
        """Test that metrics file is created."""
        logger = MetricsLogger(tmp_path)
        logger.log_metric({"test": "value"})

        metrics_file = tmp_path / "metrics.jsonl"
        assert metrics_file.exists()

    def test_log_metric(self, tmp_path):
        """Test logging a metric."""
        logger = MetricsLogger(tmp_path)
        logger.log_metric({"key": "value", "number": 42})

        metrics_file = tmp_path / "metrics.jsonl"
        with open(metrics_file) as f:
            line = f.readline()
            data = json.loads(line)

        assert data["key"] == "value"
        assert data["number"] == 42
        assert "timestamp" in data

    def test_log_multiple_metrics(self, tmp_path):
        """Test logging multiple metrics."""
        logger = MetricsLogger(tmp_path)

        for i in range(5):
            logger.log_metric({"index": i})

        metrics_file = tmp_path / "metrics.jsonl"
        with open(metrics_file) as f:
            lines = f.readlines()

        assert len(lines) == 5
        for i, line in enumerate(lines):
            data = json.loads(line)
            assert data["index"] == i

    def test_log_cycle_start(self, tmp_path):
        """Test logging cycle start event."""
        logger = MetricsLogger(tmp_path)
        logger.log_cycle_start("cycle_001", 1, "primary-ctx", "secondary-ctx")

        metrics_file = tmp_path / "metrics.jsonl"
        with open(metrics_file) as f:
            data = json.loads(f.readline())

        assert data["metric_type"] == "cycle_start"
        assert data["cycle_id"] == "cycle_001"
        assert data["cycle_num"] == 1

    def test_log_cycle_end(self, tmp_path):
        """Test logging cycle end event."""
        logger = MetricsLogger(tmp_path)
        logger.log_cycle_end("cycle_001", True, 120.5)

        metrics_file = tmp_path / "metrics.jsonl"
        with open(metrics_file) as f:
            data = json.loads(f.readline())

        assert data["metric_type"] == "cycle_end"
        assert data["success"] is True
        assert data["duration_seconds"] == 120.5

    def test_log_phase_result(self, tmp_path):
        """Test logging phase result event."""
        logger = MetricsLogger(tmp_path)
        logger.log_phase_result("cycle_001", "preflight", True, 15.3)

        metrics_file = tmp_path / "metrics.jsonl"
        with open(metrics_file) as f:
            data = json.loads(f.readline())

        assert data["metric_type"] == "phase_result"
        assert data["phase_name"] == "preflight"
        assert data["success"] is True

    def test_log_phase_result_with_error(self, tmp_path):
        """Test logging failed phase result."""
        logger = MetricsLogger(tmp_path)
        logger.log_phase_result(
            "cycle_001", "activation", False, 30.0, error="Connection timeout"
        )

        metrics_file = tmp_path / "metrics.jsonl"
        with open(metrics_file) as f:
            data = json.loads(f.readline())

        assert data["success"] is False
        assert data["error"] == "Connection timeout"

    def test_log_alert(self, tmp_path):
        """Test logging an alert."""
        logger = MetricsLogger(tmp_path)
        alert = Alert(
            alert_type="CLUSTER_UNAVAILABLE",
            hub_type="primary",
            resource="cluster-1",
            message="Cluster unavailable",
        )
        logger.log_alert(alert)

        metrics_file = tmp_path / "metrics.jsonl"
        with open(metrics_file) as f:
            data = json.loads(f.readline())

        assert data["metric_type"] == "alert"
        assert data["alert"]["alert_type"] == "CLUSTER_UNAVAILABLE"

    def test_log_snapshot(self, tmp_path):
        """Test logging resource snapshots."""
        logger = MetricsLogger(tmp_path)
        timestamp = datetime.now(timezone.utc)

        primary = ResourceSnapshot(
            timestamp=timestamp,
            hub_type="primary",
            managed_clusters=[{"name": "c1", "available": "True"}],
        )
        secondary = ResourceSnapshot(
            timestamp=timestamp,
            hub_type="secondary",
            restores=[{"name": "r1", "phase": "Completed"}],
        )

        logger.log_snapshot(primary, secondary)

        metrics_file = tmp_path / "metrics.jsonl"
        with open(metrics_file) as f:
            data = json.loads(f.readline())

        assert data["metric_type"] == "resource_snapshot"
        assert "primary" in data
        assert "secondary" in data

    def test_thread_safety(self, tmp_path):
        """Test that metrics logging is thread-safe."""
        logger = MetricsLogger(tmp_path)
        num_threads = 10
        metrics_per_thread = 50

        def log_metrics(thread_id):
            for i in range(metrics_per_thread):
                logger.log_metric({"thread": thread_id, "index": i})

        threads = []
        for i in range(num_threads):
            t = threading.Thread(target=log_metrics, args=(i,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        metrics_file = tmp_path / "metrics.jsonl"
        with open(metrics_file) as f:
            lines = f.readlines()

        assert len(lines) == num_threads * metrics_per_thread
        # Verify each line is valid JSON
        for line in lines:
            json.loads(line)


@pytest.mark.e2e
class TestResourceMonitorUnit:
    """Unit tests for ResourceMonitor (without real K8s clients)."""

    def test_monitor_initialization(self, tmp_path):
        """Test ResourceMonitor initialization."""
        mock_primary = MagicMock()
        mock_secondary = MagicMock()

        monitor = ResourceMonitor(
            primary_client=mock_primary,
            secondary_client=mock_secondary,
            output_dir=tmp_path,
            interval_seconds=10,
        )

        assert monitor.interval_seconds == 10
        assert not monitor.is_running()

    def test_set_phase(self, tmp_path):
        """Test setting monitoring phase."""
        mock_primary = MagicMock()
        mock_secondary = MagicMock()

        monitor = ResourceMonitor(
            primary_client=mock_primary,
            secondary_client=mock_secondary,
            output_dir=tmp_path,
        )

        monitor.set_phase("activation")
        assert monitor._current_phase == "activation"

    def test_get_summary(self, tmp_path):
        """Test getting monitoring summary."""
        mock_primary = MagicMock()
        mock_secondary = MagicMock()

        monitor = ResourceMonitor(
            primary_client=mock_primary,
            secondary_client=mock_secondary,
            output_dir=tmp_path,
        )
        monitor._start_time = datetime.now(timezone.utc)

        summary = monitor.get_summary()

        assert "start_time" in summary
        assert "duration_seconds" in summary
        assert "total_alerts" in summary
        assert "current_phase" in summary


@pytest.mark.e2e
class TestMonitoringContext:
    """Tests for MonitoringContext context manager."""

    def test_context_disabled(self, tmp_path):
        """Test MonitoringContext when disabled."""
        mock_primary = MagicMock()
        mock_secondary = MagicMock()

        ctx = MonitoringContext(
            primary_client=mock_primary,
            secondary_client=mock_secondary,
            output_dir=tmp_path,
            enabled=False,
        )

        with ctx as monitor:
            assert monitor is None

    def test_context_enabled(self, tmp_path):
        """Test MonitoringContext when enabled (mocked)."""
        mock_primary = MagicMock()
        mock_secondary = MagicMock()

        ctx = MonitoringContext(
            primary_client=mock_primary,
            secondary_client=mock_secondary,
            output_dir=tmp_path,
            enabled=True,
            interval_seconds=1,
        )

        # Patch the _poll_resources to avoid actual K8s calls
        with patch.object(ResourceMonitor, "_poll_resources"):
            with ctx as monitor:
                assert monitor is not None
                assert isinstance(monitor, ResourceMonitor)
                # Give the thread time to start
                time.sleep(0.1)

        # Monitor should be stopped after context exits
        assert not monitor.is_running()
