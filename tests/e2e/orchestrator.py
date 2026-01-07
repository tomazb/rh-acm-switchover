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
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from lib import KubeClient, Phase, StateManager

from .phase_handlers import CycleResult, PhaseHandlers, PhaseResult
from .failure_injection import FailureInjector, InjectionResult

if TYPE_CHECKING:
    from .monitoring import MetricsLogger


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
    # Soak testing controls
    run_hours: Optional[float] = None
    max_failures: Optional[int] = None
    resume: bool = False
    # Failure injection controls (Phase 3 resilience testing)
    inject_failure: Optional[str] = None
    inject_at_phase: str = "activation"

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
        total = self.success_count + self.failure_count
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
        self._resume_state: Optional[dict] = None
        self._metrics_logger: Optional["MetricsLogger"] = None

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
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
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
                "run_hours": self.config.run_hours,
                "max_failures": self.config.max_failures,
                "resume": self.config.resume,
                "inject_failure": self.config.inject_failure,
                "inject_at_phase": self.config.inject_at_phase,
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

    def _get_resume_state_path(self) -> Path:
        """Get the path to the resume state file."""
        return self.config.output_dir / ".resume_state.json"

    def _load_resume_state(self) -> Optional[dict]:
        """
        Load the resume state from the last run.

        Returns:
            Resume state dict if available, None otherwise
        """
        resume_path = self._get_resume_state_path()
        if not resume_path.exists():
            self.logger.info("No resume state found at %s", resume_path)
            return None

        try:
            with open(resume_path, "r", encoding="utf-8") as f:
                state = json.load(f)
            self.logger.info(
                "Loaded resume state: last_completed_cycle=%d, contexts=(%s, %s)",
                state.get("last_completed_cycle", 0),
                state.get("current_primary", "?"),
                state.get("current_secondary", "?"),
            )
            return state
        except (json.JSONDecodeError, IOError) as e:
            self.logger.warning("Failed to load resume state: %s", e)
            return None

    def _save_resume_state(
        self,
        last_completed_cycle: int,
        current_primary: str,
        current_secondary: str,
        success_count: int,
        failure_count: int,
        start_time: datetime,
    ) -> None:
        """
        Save the resume state for potential continuation.

        Args:
            last_completed_cycle: Last successfully completed cycle number
            current_primary: Current primary context
            current_secondary: Current secondary context
            success_count: Number of successful cycles
            failure_count: Number of failed cycles
            start_time: Original run start time for time limit calculation
        """
        resume_state = {
            "run_id": self.run_id,
            "last_completed_cycle": last_completed_cycle,
            "current_primary": current_primary,
            "current_secondary": current_secondary,
            "success_count": success_count,
            "failure_count": failure_count,
            "start_time": start_time.isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

        resume_path = self._get_resume_state_path()
        resume_path.parent.mkdir(parents=True, exist_ok=True)
        with open(resume_path, "w", encoding="utf-8") as f:
            json.dump(resume_state, f, indent=2)

    def _clear_resume_state(self) -> None:
        """Remove the resume state file after successful completion."""
        resume_path = self._get_resume_state_path()
        if resume_path.exists():
            resume_path.unlink()
            self.logger.info("Cleared resume state")

    def _should_stop_for_time(self, start_time: datetime) -> bool:
        """
        Check if we should stop due to time limit.

        Args:
            start_time: When the run started

        Returns:
            True if run_hours limit has been exceeded
        """
        if self.config.run_hours is None:
            return False

        elapsed_hours = (datetime.now(timezone.utc) - start_time).total_seconds() / 3600
        return elapsed_hours >= self.config.run_hours

    def _should_stop_for_failures(self, failure_count: int) -> bool:
        """
        Check if we should stop due to max failures.

        Args:
            failure_count: Current failure count

        Returns:
            True if max_failures limit has been reached
        """
        if self.config.max_failures is None:
            return False

        return failure_count >= self.config.max_failures

    def _init_metrics_logger(self) -> "MetricsLogger":
        """
        Initialize the JSONL metrics logger.

        Returns:
            MetricsLogger instance
        """
        from .monitoring import MetricsLogger
        return MetricsLogger(self.run_output_dir / "metrics", self.logger)

    def _is_transient_error(self, error_msg: str) -> bool:
        """
        Check if an error message indicates a transient failure.

        Transient failures are temporary timing issues that typically
        resolve themselves on the next cycle (e.g., restore still processing).

        Args:
            error_msg: Error message to check

        Returns:
            True if error appears to be transient
        """
        transient_patterns = [
            "not found",
            "not ready",
            "still processing",
            "currently executing",
            "waiting for restore to complete",
            "Running -",
            "Started -",
        ]
        
        error_lower = error_msg.lower()
        return any(pattern.lower() in error_lower for pattern in transient_patterns)

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

        # Log cycle start to JSONL
        if self._metrics_logger:
            self._metrics_logger.log_cycle_start(
                cycle_id, cycle_num, primary_context, secondary_context
            )

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
        failure_injector = None
        
        # Set up failure injection if configured
        if self.config.inject_failure:
            failure_injector = FailureInjector(
                client=secondary_client,  # Inject failures on secondary hub
                scenario=self.config.inject_failure,
                inject_at_phase=self.config.inject_at_phase,
                dry_run=self.config.dry_run,
            )
            
            def phase_callback(phase_name: str, timing: str) -> None:
                """Callback to inject failure before specified phase."""
                if timing == "before" and failure_injector.should_inject_at(phase_name):
                    result = failure_injector.inject()
                    if result.success:
                        self.logger.warning(
                            "Failure injected at phase %s: %s",
                            phase_name, result.message
                        )
                        if self._metrics_logger:
                            self._metrics_logger.log_event(
                                "failure_injection",
                                {
                                    "scenario": result.scenario,
                                    "phase": result.phase,
                                    "success": result.success,
                                    "message": result.message,
                                    "details": result.details,
                                }
                            )
        else:
            phase_callback = None
        
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
                phase_callback=phase_callback,
            )

            # Check if all phases succeeded
            cycle_success = all(pr.success for pr in phase_results)
            
            # Log information about transient failures (for analysis)
            if not cycle_success:
                failed_phases = [pr for pr in phase_results if not pr.success]
                for failed_phase in failed_phases:
                    error_msg = failed_phase.error or ""
                    if self._is_transient_error(error_msg):
                        self.logger.warning(
                            "Phase %s failed with transient error (may succeed on next cycle): %s",
                            failed_phase.phase_name, error_msg
                        )

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
        finally:
            # Clean up any injected failures
            if failure_injector:
                cleanup_result = failure_injector.cleanup()
                if cleanup_result.success:
                    self.logger.info("Failure injection cleaned up: %s", cleanup_result.message)
                else:
                    self.logger.warning("Failure injection cleanup failed: %s", cleanup_result.message)

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

        # Log phase results and cycle end to JSONL
        if self._metrics_logger:
            for pr in phase_results:
                self._metrics_logger.log_phase_result(
                    cycle_id, pr.phase_name, pr.success, pr.duration_seconds, pr.error
                )
            self._metrics_logger.log_cycle_end(
                cycle_id, cycle_success, result.total_duration_seconds
            )

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
        - Optionally resumes from last completed cycle
        - Writes the initial manifest
        - Runs each cycle, swapping contexts between cycles
        - Respects time limits (run_hours) and failure limits (max_failures)
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
        if self.config.run_hours:
            self.logger.info("Time limit: %.1f hours", self.config.run_hours)
        if self.config.max_failures:
            self.logger.info("Max failures: %d", self.config.max_failures)
        if self.config.resume:
            self.logger.info("Resume mode: enabled")
        self.logger.info("")

        # Write manifest
        self._write_manifest()

        # Initialize JSONL metrics logger
        self._metrics_logger = self._init_metrics_logger()

        start_time = datetime.now(timezone.utc)
        cycles: List[CycleResult] = []
        self._cycle_results = []  # Reset for this run
        success_count = 0
        failure_count = 0
        start_cycle = 1

        # Track current contexts (swap after each cycle)
        current_primary = self.config.primary_context
        current_secondary = self.config.secondary_context

        # Handle resume from previous run
        if self.config.resume:
            resume_state = self._load_resume_state()
            if resume_state:
                start_cycle = resume_state.get("last_completed_cycle", 0) + 1
                current_primary = resume_state.get("current_primary", current_primary)
                current_secondary = resume_state.get("current_secondary", current_secondary)
                success_count = resume_state.get("success_count", 0)
                failure_count = resume_state.get("failure_count", 0)
                
                # Restore original start_time for accurate time limit enforcement
                if "start_time" in resume_state:
                    start_time = datetime.fromisoformat(resume_state["start_time"])
                    elapsed = (datetime.now(timezone.utc) - start_time).total_seconds() / 3600
                    self.logger.info(
                        "Resuming from cycle %d (previous: %d success, %d failures, elapsed: %.1fh)",
                        start_cycle, success_count, failure_count, elapsed
                    )
                else:
                    self.logger.info(
                        "Resuming from cycle %d (previous: %d success, %d failures)",
                        start_cycle, success_count, failure_count
                    )

        stop_reason = None
        for cycle_num in range(start_cycle, self.config.cycles + 1):
            # Check time limit before starting cycle
            if self._should_stop_for_time(start_time):
                stop_reason = "time_limit"
                self.logger.info(
                    "Stopping: time limit of %.1f hours reached",
                    self.config.run_hours,
                )
                break

            cycle_result = self._run_cycle(cycle_num, current_primary, current_secondary)
            cycles.append(cycle_result)
            self._cycle_results.append(cycle_result)  # Also store in property

            if cycle_result.success:
                success_count += 1
            else:
                failure_count += 1

            # Calculate next cycle's contexts (swap for bi-directional switchover)
            next_primary, next_secondary = current_secondary, current_primary

            # Save resume state after each cycle (before any early exit checks)
            # This ensures resume works correctly even when stopping early
            self._save_resume_state(
                last_completed_cycle=cycle_num,
                current_primary=next_primary,
                current_secondary=next_secondary,
                success_count=success_count,
                failure_count=failure_count,
                start_time=start_time,
            )

            # Check for early stop conditions
            if not cycle_result.success and self.config.stop_on_failure:
                stop_reason = "stop_on_failure"
                self.logger.warning(
                    "Stopping after cycle %d failure (stop_on_failure=True)",
                    cycle_num
                )
                break

            # Check max failures limit
            if self._should_stop_for_failures(failure_count):
                stop_reason = "max_failures"
                self.logger.warning(
                    "Stopping: max failures limit of %d reached",
                    self.config.max_failures,
                )
                break

            # Swap contexts for next cycle
            if cycle_num < self.config.cycles:
                current_primary, current_secondary = next_primary, next_secondary
                self.logger.info("Swapped contexts for next cycle: Primary=%s, Secondary=%s",
                               current_primary, current_secondary)

                # Cooldown between cycles
                if self.config.cooldown_seconds > 0:
                    self.logger.info("Cooldown for %d seconds before next cycle...",
                                   self.config.cooldown_seconds)
                    time.sleep(self.config.cooldown_seconds)

        end_time = datetime.now(timezone.utc)

        # Clear resume state only on successful completion of all cycles
        if stop_reason is None:
            self._clear_resume_state()

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
