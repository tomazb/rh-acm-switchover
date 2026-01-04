"""
E2E Resource Monitoring for ACM Switchover.

This module provides real-time monitoring of critical Kubernetes resources
during E2E switchover testing. It replaces the deprecated phase_monitor.sh
with a Python-based solution that integrates with the E2E orchestrator.

Features:
- Real-time polling of ManagedClusters, BackupSchedules, Restores, Observability
- Structured alert emission (JSON)
- JSONL metrics time-series
- Configurable thresholds and intervals
"""

import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from lib.constants import (
    BACKUP_NAMESPACE,
    LOCAL_CLUSTER_NAME,
    OBSERVABILITY_NAMESPACE,
)
from lib.kube_client import KubeClient


@dataclass
class AlertThresholds:
    """Configurable thresholds for alerting."""

    cluster_unavailable_seconds: int = 300  # 5 minutes
    backup_failure_seconds: int = 600  # 10 minutes
    restore_stalled_seconds: int = 900  # 15 minutes


@dataclass
class Alert:
    """Structured alert for monitoring events."""

    alert_type: str
    hub_type: str
    resource: str
    message: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    phase: str = "monitoring"

    def to_dict(self) -> dict:
        """Serialize alert to dictionary."""
        return {
            "alert_type": self.alert_type,
            "hub_type": self.hub_type,
            "resource": self.resource,
            "message": self.message,
            "timestamp": self.timestamp.isoformat(),
            "phase": self.phase,
        }


@dataclass
class ResourceSnapshot:
    """Snapshot of resource state at a point in time."""

    timestamp: datetime
    hub_type: str
    managed_clusters: List[dict] = field(default_factory=list)
    backup_schedules: List[dict] = field(default_factory=list)
    restores: List[dict] = field(default_factory=list)
    observability_status: Optional[dict] = None

    def to_dict(self) -> dict:
        """Serialize snapshot to dictionary."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "hub_type": self.hub_type,
            "managed_clusters": self.managed_clusters,
            "backup_schedules": self.backup_schedules,
            "restores": self.restores,
            "observability_status": self.observability_status,
        }


class MetricsLogger:
    """
    JSONL metrics logger for time-series data.

    Writes one JSON object per line to a metrics file for efficient
    streaming and analysis.
    """

    def __init__(self, output_dir: Path, logger: Optional[logging.Logger] = None):
        """
        Initialize the metrics logger.

        Args:
            output_dir: Directory for metrics files
            logger: Optional logger instance
        """
        self.output_dir = output_dir
        self.logger = logger or logging.getLogger("metrics_logger")
        self.metrics_file = output_dir / "metrics.jsonl"
        self._lock = threading.Lock()

        # Ensure directory exists
        output_dir.mkdir(parents=True, exist_ok=True)

    def log_metric(self, metric: dict) -> None:
        """
        Log a metric to the JSONL file.

        Args:
            metric: Dictionary containing metric data
        """
        metric_with_ts = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **metric,
        }

        with self._lock:
            with open(self.metrics_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(metric_with_ts) + "\n")

    def log_snapshot(self, primary_snapshot: ResourceSnapshot, secondary_snapshot: ResourceSnapshot) -> None:
        """
        Log resource snapshots from both hubs.

        Args:
            primary_snapshot: Snapshot from primary hub
            secondary_snapshot: Snapshot from secondary hub
        """
        metric = {
            "metric_type": "resource_snapshot",
            "primary": primary_snapshot.to_dict(),
            "secondary": secondary_snapshot.to_dict(),
        }
        self.log_metric(metric)

    def log_alert(self, alert: Alert) -> None:
        """
        Log an alert to the JSONL file.

        Args:
            alert: Alert to log
        """
        metric = {
            "metric_type": "alert",
            "alert": alert.to_dict(),
        }
        self.log_metric(metric)

    def log_cycle_start(self, cycle_id: str, cycle_num: int, primary_context: str, secondary_context: str) -> None:
        """Log the start of a cycle."""
        self.log_metric({
            "metric_type": "cycle_start",
            "cycle_id": cycle_id,
            "cycle_num": cycle_num,
            "primary_context": primary_context,
            "secondary_context": secondary_context,
        })

    def log_cycle_end(self, cycle_id: str, success: bool, duration_seconds: float) -> None:
        """Log the end of a cycle."""
        self.log_metric({
            "metric_type": "cycle_end",
            "cycle_id": cycle_id,
            "success": success,
            "duration_seconds": duration_seconds,
        })

    def log_phase_result(
        self,
        cycle_id: str,
        phase_name: str,
        success: bool,
        duration_seconds: float,
        error: Optional[str] = None,
    ) -> None:
        """Log a phase result."""
        self.log_metric({
            "metric_type": "phase_result",
            "cycle_id": cycle_id,
            "phase_name": phase_name,
            "success": success,
            "duration_seconds": duration_seconds,
            "error": error,
        })


class ResourceMonitor:
    """
    Real-time resource monitor for ACM switchover.

    Polls Kubernetes resources and emits alerts when thresholds are exceeded.
    """

    def __init__(
        self,
        primary_client: KubeClient,
        secondary_client: KubeClient,
        output_dir: Path,
        interval_seconds: int = 30,
        thresholds: Optional[AlertThresholds] = None,
        logger: Optional[logging.Logger] = None,
    ):
        """
        Initialize the resource monitor.

        Args:
            primary_client: KubeClient for primary hub
            secondary_client: KubeClient for secondary hub
            output_dir: Directory for output files
            interval_seconds: Polling interval in seconds
            thresholds: Alert thresholds configuration
            logger: Optional logger instance
        """
        self.primary_client = primary_client
        self.secondary_client = secondary_client
        self.output_dir = output_dir
        self.interval_seconds = interval_seconds
        self.thresholds = thresholds or AlertThresholds()
        self.logger = logger or logging.getLogger("resource_monitor")

        self.metrics_logger = MetricsLogger(output_dir / "metrics", self.logger)
        self.alerts_dir = output_dir / "alerts"
        self.alerts_dir.mkdir(parents=True, exist_ok=True)

        self._stop_event = threading.Event()
        self._monitor_thread: Optional[threading.Thread] = None
        self._start_time: Optional[datetime] = None
        self._alert_counts: Dict[str, int] = {}
        self._last_seen_states: Dict[str, datetime] = {}
        self._current_phase = "idle"

    def set_phase(self, phase: str) -> None:
        """Set the current monitoring phase."""
        self._current_phase = phase
        self.logger.debug("Monitoring phase set to: %s", phase)

    def start(self) -> None:
        """Start the monitoring loop in a background thread."""
        if self._monitor_thread is not None and self._monitor_thread.is_alive():
            self.logger.warning("Monitor already running")
            return

        self._stop_event.clear()
        self._start_time = datetime.now(timezone.utc)
        self._monitor_thread = threading.Thread(target=self._monitoring_loop, daemon=True)
        self._monitor_thread.start()
        self.logger.info(
            "Resource monitor started (interval=%ds, phase=%s)",
            self.interval_seconds,
            self._current_phase,
        )

    def stop(self) -> None:
        """Stop the monitoring loop."""
        if self._monitor_thread is None:
            return

        self._stop_event.set()
        self._monitor_thread.join(timeout=self.interval_seconds + 5)
        self._monitor_thread = None
        self.logger.info("Resource monitor stopped")

    def is_running(self) -> bool:
        """Check if the monitor is running."""
        return self._monitor_thread is not None and self._monitor_thread.is_alive()

    def _monitoring_loop(self) -> None:
        """Main monitoring loop."""
        self.logger.debug("Monitoring loop started")
        while not self._stop_event.is_set():
            try:
                self._poll_resources()
            except Exception as e:
                self.logger.error("Error in monitoring loop: %s", e)

            self._stop_event.wait(self.interval_seconds)

        self.logger.debug("Monitoring loop exited")

    def _poll_resources(self) -> None:
        """Poll all monitored resources from both hubs."""
        timestamp = datetime.now(timezone.utc)

        # Collect snapshots from both hubs
        primary_snapshot = self._collect_hub_snapshot("primary", self.primary_client, timestamp)
        secondary_snapshot = self._collect_hub_snapshot("secondary", self.secondary_client, timestamp)

        # Log snapshots to metrics file
        self.metrics_logger.log_snapshot(primary_snapshot, secondary_snapshot)

        # Check for alerts
        self._check_cluster_alerts(primary_snapshot)
        self._check_cluster_alerts(secondary_snapshot)
        self._check_backup_alerts(primary_snapshot)
        self._check_restore_alerts(secondary_snapshot)

    def _collect_hub_snapshot(self, hub_type: str, client: KubeClient, timestamp: datetime) -> ResourceSnapshot:
        """
        Collect a snapshot of resources from a hub.

        Args:
            hub_type: Type of hub (primary/secondary)
            client: KubeClient for the hub
            timestamp: Snapshot timestamp

        Returns:
            ResourceSnapshot with current state
        """
        snapshot = ResourceSnapshot(timestamp=timestamp, hub_type=hub_type)

        # Collect managed clusters
        try:
            clusters = client.list_custom_resources(
                group="cluster.open-cluster-management.io",
                version="v1",
                plural="managedclusters",
            )
            for cluster in clusters:
                name = cluster.get("metadata", {}).get("name", "")
                if name == LOCAL_CLUSTER_NAME:
                    continue

                conditions = cluster.get("status", {}).get("conditions", [])
                available = self._get_condition_status(conditions, "ManagedClusterConditionAvailable")
                joined = self._get_condition_status(conditions, "ManagedClusterJoined")
                accepted = self._get_condition_status(conditions, "HubAcceptedManagedCluster")

                snapshot.managed_clusters.append({
                    "name": name,
                    "available": available,
                    "joined": joined,
                    "accepted": accepted,
                })
        except Exception as e:
            self.logger.debug("Failed to list managed clusters on %s: %s", hub_type, e)

        # Collect backup schedules (primary hub)
        if hub_type == "primary":
            try:
                schedules = client.list_custom_resources(
                    group="cluster.open-cluster-management.io",
                    version="v1beta1",
                    plural="backupschedules",
                    namespace=BACKUP_NAMESPACE,
                )
                for schedule in schedules:
                    name = schedule.get("metadata", {}).get("name", "")
                    phase = schedule.get("status", {}).get("phase", "Unknown")
                    paused = schedule.get("spec", {}).get("paused", False)
                    last_backup = schedule.get("status", {}).get("lastBackupTime", "Never")

                    snapshot.backup_schedules.append({
                        "name": name,
                        "phase": phase,
                        "paused": paused,
                        "last_backup": last_backup,
                    })
            except Exception as e:
                self.logger.debug("Failed to list backup schedules on %s: %s", hub_type, e)

        # Collect restores (secondary hub)
        if hub_type == "secondary":
            try:
                restores = client.list_custom_resources(
                    group="cluster.open-cluster-management.io",
                    version="v1beta1",
                    plural="restores",
                    namespace=BACKUP_NAMESPACE,
                )
                for restore in restores:
                    name = restore.get("metadata", {}).get("name", "")
                    phase = restore.get("status", {}).get("phase", "Unknown")
                    started = restore.get("status", {}).get("startTimestamp", "Unknown")
                    completed = restore.get("status", {}).get("completionTimestamp", "Running")

                    snapshot.restores.append({
                        "name": name,
                        "phase": phase,
                        "started": started,
                        "completed": completed,
                    })
            except Exception as e:
                self.logger.debug("Failed to list restores on %s: %s", hub_type, e)

        # Collect observability status
        try:
            if client.namespace_exists(OBSERVABILITY_NAMESPACE):
                obs_status = {"deployments": 0, "statefulsets": 0, "ready": True}

                # Check deployments
                try:
                    deployments = client.apps_v1.list_namespaced_deployment(
                        namespace=OBSERVABILITY_NAMESPACE
                    )
                    obs_status["deployments"] = len(deployments.items)
                    for dep in deployments.items:
                        ready = dep.status.ready_replicas or 0
                        desired = dep.spec.replicas or 0
                        if ready != desired:
                            obs_status["ready"] = False
                except Exception:
                    pass

                # Check statefulsets
                try:
                    statefulsets = client.apps_v1.list_namespaced_stateful_set(
                        namespace=OBSERVABILITY_NAMESPACE
                    )
                    obs_status["statefulsets"] = len(statefulsets.items)
                    for sts in statefulsets.items:
                        ready = sts.status.ready_replicas or 0
                        desired = sts.spec.replicas or 0
                        if ready != desired:
                            obs_status["ready"] = False
                except Exception:
                    pass

                snapshot.observability_status = obs_status
        except Exception as e:
            self.logger.debug("Failed to check observability on %s: %s", hub_type, e)

        return snapshot

    def _get_condition_status(self, conditions: List[dict], condition_type: str) -> str:
        """Extract condition status from conditions list."""
        for cond in conditions:
            if cond.get("type") == condition_type:
                return cond.get("status", "Unknown")
        return "Unknown"

    def _check_cluster_alerts(self, snapshot: ResourceSnapshot) -> None:
        """Check for cluster availability alerts."""
        timestamp = snapshot.timestamp

        for cluster in snapshot.managed_clusters:
            name = cluster["name"]
            available = cluster["available"]
            state_key = f"{snapshot.hub_type}_{name}"

            if available != "True":
                if state_key not in self._last_seen_states:
                    self._last_seen_states[state_key] = timestamp
                else:
                    duration = (timestamp - self._last_seen_states[state_key]).total_seconds()
                    if duration > self.thresholds.cluster_unavailable_seconds:
                        alert_key = f"{state_key}_unavailable"
                        if alert_key not in self._alert_counts:
                            self._alert_counts[alert_key] = 0
                            self._emit_alert(Alert(
                                alert_type="CLUSTER_UNAVAILABLE",
                                hub_type=snapshot.hub_type,
                                resource=name,
                                message=f"Cluster unavailable for {int(duration)}s",
                                phase=self._current_phase,
                            ))
                        self._alert_counts[alert_key] += 1
            else:
                # Reset state when cluster becomes available
                if state_key in self._last_seen_states:
                    del self._last_seen_states[state_key]
                alert_key = f"{state_key}_unavailable"
                if alert_key in self._alert_counts:
                    del self._alert_counts[alert_key]

    def _check_backup_alerts(self, snapshot: ResourceSnapshot) -> None:
        """Check for backup failure alerts."""
        if snapshot.hub_type != "primary":
            return

        for schedule in snapshot.backup_schedules:
            phase = schedule["phase"]
            if phase in ("Failed", "PartiallyFailed"):
                state_key = f"{snapshot.hub_type}_backup_failure"

                if state_key not in self._last_seen_states:
                    self._last_seen_states[state_key] = snapshot.timestamp
                else:
                    duration = (snapshot.timestamp - self._last_seen_states[state_key]).total_seconds()
                    if duration >= self.thresholds.backup_failure_seconds:
                        alert_key = f"{state_key}_exceeded"
                        if alert_key not in self._alert_counts:
                            self._alert_counts[alert_key] = 0
                            self._emit_alert(Alert(
                                alert_type="BACKUP_FAILURE",
                                hub_type=snapshot.hub_type,
                                resource=schedule["name"],
                                message=f"Backup failing for {int(duration)}s (phase: {phase})",
                                phase=self._current_phase,
                            ))
                        self._alert_counts[alert_key] += 1
            else:
                # Reset failure state
                state_key = f"{snapshot.hub_type}_backup_failure"
                if state_key in self._last_seen_states:
                    del self._last_seen_states[state_key]
                alert_key = f"{state_key}_exceeded"
                if alert_key in self._alert_counts:
                    del self._alert_counts[alert_key]

    def _check_restore_alerts(self, snapshot: ResourceSnapshot) -> None:
        """Check for restore stalled alerts."""
        if snapshot.hub_type != "secondary":
            return

        for restore in snapshot.restores:
            phase = restore["phase"]
            started = restore["started"]

            if phase not in ("Completed", "Failed") and started != "Unknown":
                try:
                    start_time = datetime.fromisoformat(started.replace("Z", "+00:00"))
                    duration = (snapshot.timestamp - start_time).total_seconds()

                    if duration > self.thresholds.restore_stalled_seconds:
                        state_key = f"{snapshot.hub_type}_{restore['name']}_stalled"
                        if state_key not in self._alert_counts:
                            self._alert_counts[state_key] = 0
                            self._emit_alert(Alert(
                                alert_type="RESTORE_STALLED",
                                hub_type=snapshot.hub_type,
                                resource=restore["name"],
                                message=f"Restore stalled in phase '{phase}' for {int(duration)}s",
                                phase=self._current_phase,
                            ))
                        self._alert_counts[state_key] += 1
                except (ValueError, TypeError):
                    pass

    def _emit_alert(self, alert: Alert) -> None:
        """
        Emit an alert to file and log.

        Args:
            alert: Alert to emit
        """
        # Log to metrics file
        self.metrics_logger.log_alert(alert)

        # Write individual alert file
        safe_resource = alert.resource.replace("/", "_")
        alert_file = self.alerts_dir / f"{alert.alert_type}_{alert.hub_type}_{safe_resource}.json"
        with open(alert_file, "w", encoding="utf-8") as f:
            json.dump(alert.to_dict(), f, indent=2)

        # Log to console
        self.logger.warning(
            "ALERT: %s on %s - %s: %s",
            alert.alert_type,
            alert.hub_type,
            alert.resource,
            alert.message,
        )

    def get_summary(self) -> dict:
        """
        Get a summary of monitoring activity.

        Returns:
            Dictionary with monitoring summary
        """
        end_time = datetime.now(timezone.utc)
        duration = (end_time - self._start_time).total_seconds() if self._start_time else 0

        return {
            "start_time": self._start_time.isoformat() if self._start_time else None,
            "end_time": end_time.isoformat(),
            "duration_seconds": duration,
            "total_alerts": sum(self._alert_counts.values()),
            "alert_types": list(self._alert_counts.keys()),
            "current_phase": self._current_phase,
        }


class MonitoringContext:
    """Context manager for starting/stopping monitoring during E2E runs."""

    def __init__(
        self,
        primary_client: KubeClient,
        secondary_client: KubeClient,
        output_dir: Path,
        interval_seconds: int = 30,
        enabled: bool = True,
        logger: Optional[logging.Logger] = None,
    ):
        """
        Initialize the monitoring context.

        Args:
            primary_client: KubeClient for primary hub
            secondary_client: KubeClient for secondary hub
            output_dir: Directory for output files
            interval_seconds: Polling interval in seconds
            enabled: Whether monitoring is enabled
            logger: Optional logger instance
        """
        self.enabled = enabled
        self.logger = logger or logging.getLogger("monitoring_context")
        self._monitor: Optional[ResourceMonitor] = None

        if enabled:
            self._monitor = ResourceMonitor(
                primary_client=primary_client,
                secondary_client=secondary_client,
                output_dir=output_dir,
                interval_seconds=interval_seconds,
                logger=logger,
            )

    def __enter__(self) -> Optional[ResourceMonitor]:
        """Start monitoring."""
        if self._monitor:
            self._monitor.start()
        return self._monitor

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Stop monitoring."""
        if self._monitor:
            self._monitor.stop()
            summary = self._monitor.get_summary()
            self.logger.info(
                "Monitoring summary: duration=%.0fs, alerts=%d",
                summary["duration_seconds"],
                summary["total_alerts"],
            )
