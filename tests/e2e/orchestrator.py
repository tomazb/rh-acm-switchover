"""
E2E Orchestrator for ACM Switchover Testing.

This module provides a Python-based orchestrator that runs complete
switchover cycles with automated context swapping, metrics collection,
and state management.
"""

import json
import logging
import os
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from lib import KubeClient, Phase, StateManager

from .phase_handlers import CycleResult, PhaseHandlers, PhaseResult


@dataclass
class RunConfig:
    """Configuration for an E2E test run."""

    primary_context: str
    secondary_context: str
    method: str = "passive"
    old_hub_action: str = "secondary"
    cycles: int = 5
    dry_run: bool = False
    output_dir: Path = field(default_factory=lambda: Path("./e2e-results"))
    state_dir: Path = field(default_factory=lambda: Path("./.state"))
    stop_on_failure: bool = False
    cooldown_seconds: int = 30
    skip_observability_checks: bool = False
    skip_rbac_validation: bool = False
    manage_auto_import_strategy: bool = False

    def __post_init__(self):
        """Convert string paths to Path objects if needed."""
        if isinstance(self.output_dir, str):
            self.output_dir = Path(self.output_dir)
        if isinstance(self.state_dir, str):
            self.state_dir = Path(self.state_dir)


@dataclass
class RunResult:
    """Result of a complete E2E test run."""

    run_id: str
    config: RunConfig
    cycles: List[CycleResult]
    start_time: datetime
    end_time: datetime
    success_count: int
    failure_count: int

    @property
    def success_rate(self) -> float:
        """Calculate success rate as a percentage."""
        total = len(self.cycles)
        return (self.success_count / total * 100) if total > 0 else 0.0

    @property
    def total_duration_seconds(self) -> float:
        """Calculate total run duration in seconds."""
        return (self.end_time - self.start_time).total_seconds()


class E2EOrchestrator:
    """
    Orchestrator for running E2E switchover test cycles.

    This class manages:
    - Running multiple switchover cycles
    - Swapping primary/secondary contexts between cycles
    - Collecting metrics and timing data
    - Generating manifest and result files
    """

    def __init__(self, config: RunConfig, logger: Optional[logging.Logger] = None):
        """
        Initialize the E2E orchestrator.

        Args:
            config: Run configuration
            logger: Optional logger instance
        """
        self.config = config
        self.logger = logger or logging.getLogger("e2e_orchestrator")
        self.run_id = self._generate_run_id()
        self.run_output_dir = self._setup_output_dir()
        self.phase_handlers = PhaseHandlers(self.logger)
        self._cycle_results: List[CycleResult] = []

    @property
    def manifest_path(self) -> Path:
        """Get the path to the manifest file."""
        return self.run_output_dir / "manifests" / "manifest.json"

    @property
    def cycle_results(self) -> List[CycleResult]:
        """Get the list of cycle results from the last run."""
        return self._cycle_results

    def _generate_run_id(self) -> str:
        """Generate a unique run identifier."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        short_uuid = str(uuid.uuid4())[:8]
        return f"run_{timestamp}_{short_uuid}"

    def _setup_output_dir(self) -> Path:
        """Set up the output directory structure for this run."""
        run_dir = self.config.output_dir / self.run_id
        subdirs = ["logs", "states", "metrics", "manifests"]
        for subdir in subdirs:
            (run_dir / subdir).mkdir(parents=True, exist_ok=True)
        return run_dir

    def _get_git_sha(self) -> str:
        """Get the current git SHA, if available."""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            return result.stdout.strip() if result.returncode == 0 else "unknown"
        except (subprocess.SubprocessError, FileNotFoundError):
            return "unknown"

    def _get_tool_version(self) -> str:
        """Get the ACM switchover tool version."""
        try:
            from lib import __version__
            return __version__
        except ImportError:
            return "unknown"

    def _write_manifest(self) -> None:
        """Write the run manifest with configuration and environment info."""
        manifest = {
            "run_id": self.run_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "config": {
                "primary_context": self.config.primary_context,
                "secondary_context": self.config.secondary_context,
                "method": self.config.method,
                "old_hub_action": self.config.old_hub_action,
                "cycles": self.config.cycles,
                "dry_run": self.config.dry_run,
                "cooldown_seconds": self.config.cooldown_seconds,
                "stop_on_failure": self.config.stop_on_failure,
            },
            "environment": {
                "git_sha": self._get_git_sha(),
                "tool_version": self._get_tool_version(),
                "python_version": self._get_python_version(),
                "hostname": os.uname().nodename,
            },
        }

        manifest_file = self.run_output_dir / "manifests" / "manifest.json"
        with open(manifest_file, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)

        self.logger.info("Manifest written to: %s", manifest_file)

    def _get_python_version(self) -> str:
        """Get the Python version."""
        import sys
        return f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"

    def _create_clients(
        self, primary_context: str, secondary_context: str
    ) -> tuple[KubeClient, KubeClient]:
        """
        Create KubeClient instances for both hubs.

        Args:
            primary_context: Kubernetes context for primary hub
            secondary_context: Kubernetes context for secondary hub

        Returns:
            Tuple of (primary_client, secondary_client)
        """
        primary = KubeClient(
            context=primary_context,
            dry_run=self.config.dry_run,
        )
        secondary = KubeClient(
            context=secondary_context,
            dry_run=self.config.dry_run,
        )
        return primary, secondary

    def _create_state_manager(self, cycle_id: str, primary_context: str, secondary_context: str) -> StateManager:
        """
        Create a fresh StateManager for a cycle.

        Args:
            cycle_id: Unique cycle identifier
            primary_context: Primary hub context name
            secondary_context: Secondary hub context name

        Returns:
            Fresh StateManager instance
        """
        state_file = self.config.state_dir / f"e2e_{self.run_id}_{cycle_id}.json"
        # Remove existing state file to ensure fresh start
        if state_file.exists():
            state_file.unlink()
        return StateManager(str(state_file))

    def _write_cycle_metrics(self, cycle_result: CycleResult) -> None:
        """
        Write metrics for a completed cycle.

        Args:
            cycle_result: Result from the completed cycle
        """
        metrics = {
            "cycle_id": cycle_result.cycle_id,
            "run_id": self.run_id,
            "success": cycle_result.success,
            "start_time": cycle_result.start_time.isoformat(),
            "end_time": cycle_result.end_time.isoformat(),
            "total_duration_seconds": cycle_result.total_duration_seconds,
            "primary_context": cycle_result.primary_context,
            "secondary_context": cycle_result.secondary_context,
            "phases": [
                {
                    "name": pr.phase_name,
                    "success": pr.success,
                    "duration_seconds": pr.duration_seconds,
                    "error": pr.error,
                }
                for pr in cycle_result.phase_results
            ],
        }

        metrics_file = self.run_output_dir / "metrics" / f"metrics_{cycle_result.cycle_id}.json"
        with open(metrics_file, "w", encoding="utf-8") as f:
            json.dump(metrics, f, indent=2)

    def _write_final_summary(self, result: RunResult) -> None:
        """
        Write the final run summary.

        Args:
            result: Complete run result
        """
        summary = {
            "run_id": result.run_id,
            "start_time": result.start_time.isoformat(),
            "end_time": result.end_time.isoformat(),
            "total_duration_seconds": result.total_duration_seconds,
            "total_cycles": len(result.cycles),
            "success_count": result.success_count,
            "failure_count": result.failure_count,
            "success_rate_percent": result.success_rate,
            "cycles": [
                {
                    "cycle_id": c.cycle_id,
                    "success": c.success,
                    "duration_seconds": c.total_duration_seconds,
                    "primary_context": c.primary_context,
                    "secondary_context": c.secondary_context,
                }
                for c in result.cycles
            ],
        }

        summary_file = self.run_output_dir / "summary.json"
        with open(summary_file, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2)

        # Also write CSV for compatibility with bash analyzer
        csv_file = self.run_output_dir / "cycle_results.csv"
        with open(csv_file, "w", encoding="utf-8") as f:
            f.write("cycle,phase,status,start_time,end_time,duration_seconds,exit_code\n")
            for cycle in result.cycles:
                for phase in cycle.phase_results:
                    status = "0" if phase.success else "1"
                    # Approximate phase times from cycle times
                    f.write(
                        f"{cycle.cycle_id},{phase.phase_name},{status},"
                        f"{phase.start_time.isoformat()},{phase.end_time.isoformat()},"
                        f"{phase.duration_seconds},{status}\n"
                    )

        self.logger.info("Summary written to: %s", summary_file)

    def _run_cycle(
        self,
        cycle_num: int,
        primary_context: str,
        secondary_context: str,
    ) -> CycleResult:
        """
        Run a single switchover cycle.

        Args:
            cycle_num: Cycle number (1-based)
            primary_context: Current primary hub context
            secondary_context: Current secondary hub context

        Returns:
            CycleResult with timing and status information
        """
        cycle_id = f"cycle_{cycle_num:03d}"
        self.logger.info("")
        self.logger.info("=" * 60)
        self.logger.info("STARTING CYCLE %d/%d (ID: %s)", cycle_num, self.config.cycles, cycle_id)
        self.logger.info("Primary: %s, Secondary: %s", primary_context, secondary_context)
        self.logger.info("=" * 60)

        start_time = datetime.now(timezone.utc)
        phase_results: List[PhaseResult] = []

        # Create fresh clients and state for this cycle
        try:
            primary_client, secondary_client = self._create_clients(
                primary_context, secondary_context
            )
            state_manager = self._create_state_manager(
                cycle_id, primary_context, secondary_context
            )
        except Exception as e:
            self.logger.error("Failed to initialize cycle %d: %s", cycle_num, e)
            end_time = datetime.now(timezone.utc)
            return CycleResult(
                cycle_id=cycle_id,
                cycle_num=cycle_num,
                success=False,
                start_time=start_time,
                end_time=end_time,
                primary_context=primary_context,
                secondary_context=secondary_context,
                phase_results=[],
                error=str(e),
            )

        # Run all phases
        cycle_success = True
        try:
            phase_results = self.phase_handlers.run_all_phases(
                primary_client=primary_client,
                secondary_client=secondary_client,
                state_manager=state_manager,
                method=self.config.method,
                old_hub_action=self.config.old_hub_action,
                dry_run=self.config.dry_run,
                skip_observability_checks=self.config.skip_observability_checks,
                skip_rbac_validation=self.config.skip_rbac_validation,
                manage_auto_import_strategy=self.config.manage_auto_import_strategy,
            )

            # Check if all phases succeeded
            cycle_success = all(pr.success for pr in phase_results)

        except Exception as e:
            self.logger.error("Cycle %d failed with exception: %s", cycle_num, e)
            cycle_success = False
            phase_results.append(
                PhaseResult(
                    phase_name="exception",
                    success=False,
                    start_time=datetime.now(timezone.utc),
                    end_time=datetime.now(timezone.utc),
                    error=str(e),
                )
            )

        end_time = datetime.now(timezone.utc)

        # Save state file to output directory
        state_file = self.config.state_dir / f"e2e_{self.run_id}_{cycle_id}.json"
        if state_file.exists():
            dest_state = self.run_output_dir / "states" / f"{cycle_id}_state.json"
            import shutil
            shutil.copy(state_file, dest_state)

        result = CycleResult(
            cycle_id=cycle_id,
            cycle_num=cycle_num,
            success=cycle_success,
            start_time=start_time,
            end_time=end_time,
            primary_context=primary_context,
            secondary_context=secondary_context,
            phase_results=phase_results,
        )

        self._write_cycle_metrics(result)

        if cycle_success:
            self.logger.info("✅ Cycle %d completed successfully in %.1f seconds",
                           cycle_num, result.total_duration_seconds)
        else:
            self.logger.error("❌ Cycle %d failed after %.1f seconds",
                            cycle_num, result.total_duration_seconds)

        return result

    def run_all_cycles(self) -> RunResult:
        """
        Run all configured switchover cycles.

        This method:
        - Writes the initial manifest
        - Runs each cycle, swapping contexts between cycles
        - Collects metrics and results
        - Writes the final summary

        Returns:
            RunResult with all cycle results and aggregate statistics
        """
        self.logger.info("")
        self.logger.info("=" * 60)
        self.logger.info("ACM SWITCHOVER E2E TEST ORCHESTRATOR")
        self.logger.info("=" * 60)
        self.logger.info("Run ID: %s", self.run_id)
        self.logger.info("Output directory: %s", self.run_output_dir)
        self.logger.info("Cycles: %d", self.config.cycles)
        self.logger.info("Method: %s", self.config.method)
        self.logger.info("Dry run: %s", self.config.dry_run)
        self.logger.info("")

        # Write manifest
        self._write_manifest()

        start_time = datetime.now(timezone.utc)
        cycles: List[CycleResult] = []
        self._cycle_results = []  # Reset for this run
        success_count = 0
        failure_count = 0

        # Track current contexts (swap after each cycle)
        current_primary = self.config.primary_context
        current_secondary = self.config.secondary_context

        for cycle_num in range(1, self.config.cycles + 1):
            cycle_result = self._run_cycle(cycle_num, current_primary, current_secondary)
            cycles.append(cycle_result)
            self._cycle_results.append(cycle_result)  # Also store in property

            if cycle_result.success:
                success_count += 1
            else:
                failure_count += 1
                if self.config.stop_on_failure:
                    self.logger.warning(
                        "Stopping after cycle %d failure (stop_on_failure=True)",
                        cycle_num
                    )
                    break

            # Swap contexts for next cycle (simulating bi-directional switchover)
            if cycle_num < self.config.cycles:
                current_primary, current_secondary = current_secondary, current_primary
                self.logger.info("Swapped contexts for next cycle: Primary=%s, Secondary=%s",
                               current_primary, current_secondary)

                # Cooldown between cycles
                if self.config.cooldown_seconds > 0:
                    self.logger.info("Cooldown for %d seconds before next cycle...",
                                   self.config.cooldown_seconds)
                    time.sleep(self.config.cooldown_seconds)

        end_time = datetime.now(timezone.utc)

        result = RunResult(
            run_id=self.run_id,
            config=self.config,
            cycles=cycles,
            start_time=start_time,
            end_time=end_time,
            success_count=success_count,
            failure_count=failure_count,
        )

        self._write_final_summary(result)

        # Final summary log
        self.logger.info("")
        self.logger.info("=" * 60)
        self.logger.info("E2E TEST RUN COMPLETED")
        self.logger.info("=" * 60)
        self.logger.info("Total cycles: %d", len(cycles))
        self.logger.info("Successful: %d", success_count)
        self.logger.info("Failed: %d", failure_count)
        self.logger.info("Success rate: %.1f%%", result.success_rate)
        self.logger.info("Total duration: %.1f seconds", result.total_duration_seconds)
        self.logger.info("Results: %s", self.run_output_dir)
        self.logger.info("")

        if failure_count > 0:
            self.logger.warning("⚠️  Some cycles failed. Check logs for details.")
        else:
            self.logger.info("🎉 All cycles completed successfully!")

        return result
