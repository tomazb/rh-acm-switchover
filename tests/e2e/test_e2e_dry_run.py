"""
E2E Dry-Run Tests.

This module contains CI-friendly tests that validate the E2E infrastructure
without requiring real cluster access. These tests use dry-run mode to verify
orchestrator logic, phase handler invocation, and artifact generation.

Usage:
    # Run all dry-run tests (no cluster required)
    pytest -m e2e --e2e-dry-run tests/e2e/test_e2e_dry_run.py

    # Run in CI pipeline
    pytest -m e2e --e2e-dry-run -v tests/e2e/
"""

import json
import pytest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from tests.e2e.orchestrator import RunConfig, E2EOrchestrator
from tests.e2e.phase_handlers import CycleResult, PhaseResult


def make_datetime(iso_str: str) -> datetime:
    """Helper to create datetime objects from ISO strings."""
    return datetime.fromisoformat(iso_str.replace("Z", "+00:00"))


def make_phase_result(
    phase_name: str,
    success: bool = True,
    start: str = "2026-01-03T10:00:00",
    end: str = "2026-01-03T10:01:00",
    error: str = None,
) -> PhaseResult:
    """Helper to create PhaseResult objects."""
    return PhaseResult(
        phase_name=phase_name,
        success=success,
        start_time=make_datetime(start),
        end_time=make_datetime(end),
        error=error,
    )


def make_cycle_result(
    cycle_id: str = "cycle_001",
    cycle_num: int = 1,
    success: bool = True,
    start: str = "2026-01-03T10:00:00",
    end: str = "2026-01-03T10:05:00",
    primary_context: str = "test-primary",
    secondary_context: str = "test-secondary",
    phase_results: list = None,
    error: str = None,
) -> CycleResult:
    """Helper to create CycleResult objects."""
    return CycleResult(
        cycle_id=cycle_id,
        cycle_num=cycle_num,
        success=success,
        start_time=make_datetime(start),
        end_time=make_datetime(end),
        primary_context=primary_context,
        secondary_context=secondary_context,
        phase_results=phase_results or [],
        error=error,
    )


@pytest.mark.e2e
class TestE2EDryRun:
    """Dry-run tests that don't require real clusters."""

    def test_orchestrator_initialization(self, tmp_path):
        """Test that orchestrator initializes correctly in dry-run mode."""
        config = RunConfig(
            primary_context="test-primary",
            secondary_context="test-secondary",
            dry_run=True,
            cycles=1,
            output_dir=tmp_path,
        )

        orchestrator = E2EOrchestrator(config)

        assert orchestrator.config.dry_run is True
        assert orchestrator.run_id is not None
        assert len(orchestrator.run_id) > 0

    def test_run_output_directory_structure(self, tmp_path):
        """Test that run output directory has correct structure."""
        config = RunConfig(
            primary_context="test-primary",
            secondary_context="test-secondary",
            dry_run=True,
            cycles=1,
            output_dir=tmp_path,
        )

        orchestrator = E2EOrchestrator(config)

        # Verify subdirectories are created
        assert orchestrator.run_output_dir.exists()
        assert (orchestrator.run_output_dir / "logs").exists()
        assert (orchestrator.run_output_dir / "states").exists()
        assert (orchestrator.run_output_dir / "metrics").exists()
        assert (orchestrator.run_output_dir / "manifests").exists()

    def test_dry_run_cycle_execution(self, tmp_path):
        """Test that dry-run cycles execute without errors."""
        config = RunConfig(
            primary_context="test-primary",
            secondary_context="test-secondary",
            dry_run=True,
            cycles=2,
            output_dir=tmp_path,
            cooldown_seconds=0,
        )

        orchestrator = E2EOrchestrator(config)

        # Mock the _run_cycle method to avoid actual execution
        with patch.object(orchestrator, "_run_cycle") as mock_run_cycle:
            mock_run_cycle.return_value = make_cycle_result()

            result = orchestrator.run_all_cycles()

        assert result is not None
        assert mock_run_cycle.call_count == 2

    def test_context_swapping(self, tmp_path):
        """Test that contexts are swapped between cycles."""
        config = RunConfig(
            primary_context="hub-a",
            secondary_context="hub-b",
            dry_run=True,
            cycles=3,
            output_dir=tmp_path,
            cooldown_seconds=0,
        )

        orchestrator = E2EOrchestrator(config)

        captured_contexts = []

        def capture_contexts(cycle_num, primary, secondary):
            captured_contexts.append((primary, secondary))
            return make_cycle_result(
                cycle_id=f"cycle_{cycle_num:03d}",
                cycle_num=cycle_num,
                primary_context=primary,
                secondary_context=secondary,
            )

        with patch.object(orchestrator, "_run_cycle", side_effect=capture_contexts):
            orchestrator.run_all_cycles()

        # Verify context swapping pattern
        assert len(captured_contexts) == 3
        assert captured_contexts[0] == ("hub-a", "hub-b")
        assert captured_contexts[1] == ("hub-b", "hub-a")  # Swapped
        assert captured_contexts[2] == ("hub-a", "hub-b")  # Swapped back

    def test_stop_on_failure(self, tmp_path):
        """Test that stop_on_failure halts execution after first failure."""
        config = RunConfig(
            primary_context="test-primary",
            secondary_context="test-secondary",
            dry_run=True,
            cycles=5,
            output_dir=tmp_path,
            stop_on_failure=True,
            cooldown_seconds=0,
        )

        orchestrator = E2EOrchestrator(config)

        call_count = 0

        def fail_on_second(cycle_num, primary, secondary):
            nonlocal call_count
            call_count += 1
            success = call_count != 2  # Fail on second cycle
            return make_cycle_result(
                cycle_id=f"cycle_{cycle_num:03d}",
                cycle_num=cycle_num,
                success=success,
                primary_context=primary,
                secondary_context=secondary,
                error="Simulated failure" if not success else None,
            )

        with patch.object(orchestrator, "_run_cycle", side_effect=fail_on_second):
            result = orchestrator.run_all_cycles()

        assert result.success_rate < 100.0
        assert call_count == 2, "Should stop after second (failed) cycle"

    def test_continue_on_failure(self, tmp_path):
        """Test that cycles continue when stop_on_failure is False."""
        config = RunConfig(
            primary_context="test-primary",
            secondary_context="test-secondary",
            dry_run=True,
            cycles=5,
            output_dir=tmp_path,
            stop_on_failure=False,
            cooldown_seconds=0,
        )

        orchestrator = E2EOrchestrator(config)

        call_count = 0

        def fail_sometimes(cycle_num, primary, secondary):
            nonlocal call_count
            call_count += 1
            success = call_count != 2 and call_count != 4  # Fail on 2nd and 4th
            return make_cycle_result(
                cycle_id=f"cycle_{cycle_num:03d}",
                cycle_num=cycle_num,
                success=success,
                primary_context=primary,
                secondary_context=secondary,
                error="Simulated failure" if not success else None,
            )

        with patch.object(orchestrator, "_run_cycle", side_effect=fail_sometimes):
            result = orchestrator.run_all_cycles()

        assert result.failure_count == 2
        assert call_count == 5, "Should run all 5 cycles"

    def test_output_directory_creation(self, tmp_path):
        """Test that output directories are created correctly."""
        output_dir = tmp_path / "nested" / "output" / "dir"

        config = RunConfig(
            primary_context="test-primary",
            secondary_context="test-secondary",
            dry_run=True,
            cycles=1,
            output_dir=output_dir,
        )

        orchestrator = E2EOrchestrator(config)

        # Verify the run-specific output dir was created inside output_dir
        assert orchestrator.run_output_dir.exists()
        assert output_dir in orchestrator.run_output_dir.parents or orchestrator.run_output_dir.parent == output_dir


@pytest.mark.e2e
class TestPhaseHandlersDryRun:
    """Tests for phase handlers in dry-run mode."""

    def test_phase_result_structure(self):
        """Test PhaseResult dataclass structure."""
        result = make_phase_result("preflight", success=True)

        assert result.phase_name == "preflight"
        assert result.success is True
        assert result.duration_seconds == 60.0  # 1 minute between start and end
        assert result.error is None

    def test_phase_result_with_error(self):
        """Test PhaseResult with error information."""
        result = make_phase_result(
            "activation",
            success=False,
            start="2026-01-03T10:00:00",
            end="2026-01-03T10:00:30",
            error="Connection timeout",
        )

        assert result.success is False
        assert result.error == "Connection timeout"
        assert result.duration_seconds == 30.0

    def test_phase_result_to_dict(self):
        """Test PhaseResult serialization to dict."""
        result = make_phase_result(
            "finalization",
            success=True,
            start="2026-01-03T10:00:00",
            end="2026-01-03T10:02:00",
        )

        result_dict = result.to_dict()

        assert isinstance(result_dict, dict)
        assert result_dict["phase_name"] == "finalization"
        assert result_dict["success"] is True
        assert result_dict["duration_seconds"] == 120.0
        assert "start_time" in result_dict
        assert "end_time" in result_dict

    def test_phase_result_duration_calculation(self):
        """Test that duration is calculated correctly from timestamps."""
        result = PhaseResult(
            phase_name="test",
            success=True,
            start_time=make_datetime("2026-01-03T10:00:00"),
            end_time=make_datetime("2026-01-03T10:05:30"),
        )

        assert result.duration_seconds == 330.0  # 5 minutes 30 seconds


@pytest.mark.e2e
class TestRunConfigValidation:
    """Tests for RunConfig validation."""

    def test_default_config_values(self):
        """Test RunConfig default values."""
        config = RunConfig(
            primary_context="primary",
            secondary_context="secondary",
        )

        assert config.method == "passive"
        assert config.old_hub_action == "secondary"
        assert config.cycles == 5  # Default is 5
        assert config.dry_run is False
        assert config.stop_on_failure is False
        assert config.cooldown_seconds == 30

    def test_config_override(self):
        """Test RunConfig value overrides."""
        config = RunConfig(
            primary_context="hub1",
            secondary_context="hub2",
            method="passive",
            old_hub_action="decommission",
            cycles=10,
            dry_run=True,
            stop_on_failure=True,
            cooldown_seconds=60,
        )

        assert config.old_hub_action == "decommission"
        assert config.cycles == 10
        assert config.dry_run is True
        assert config.stop_on_failure is True
        assert config.cooldown_seconds == 60

    def test_config_with_path_output_dir(self, tmp_path):
        """Test RunConfig with Path output directory."""
        config = RunConfig(
            primary_context="primary",
            secondary_context="secondary",
            output_dir=tmp_path / "results",
        )

        assert isinstance(config.output_dir, Path)

    def test_config_string_to_path_conversion(self, tmp_path):
        """Test that string paths are converted to Path objects."""
        config = RunConfig(
            primary_context="primary",
            secondary_context="secondary",
            output_dir=str(tmp_path / "results"),
            state_dir=str(tmp_path / "state"),
        )

        assert isinstance(config.output_dir, Path)
        assert isinstance(config.state_dir, Path)


@pytest.mark.e2e
class TestCycleResultStructure:
    """Tests for CycleResult structure."""

    def test_cycle_result_success(self):
        """Test successful CycleResult."""
        phase_results = [
            make_phase_result("preflight"),
            make_phase_result("primary_prep"),
            make_phase_result("activation"),
        ]

        result = make_cycle_result(
            cycle_id="cycle_001",
            cycle_num=1,
            success=True,
            phase_results=phase_results,
        )

        assert result.success is True
        assert result.error is None
        assert len(result.phase_results) == 3
        assert result.phase_results[0].phase_name == "preflight"

    def test_cycle_result_failure(self):
        """Test failed CycleResult."""
        result = make_cycle_result(
            cycle_id="cycle_002",
            cycle_num=2,
            success=False,
            error="Activation phase failed: restore timeout",
        )

        assert result.success is False
        assert "restore timeout" in result.error

    def test_cycle_result_duration_calculation(self):
        """Test that total duration is calculated correctly."""
        result = CycleResult(
            cycle_id="cycle_001",
            cycle_num=1,
            success=True,
            start_time=make_datetime("2026-01-03T10:00:00"),
            end_time=make_datetime("2026-01-03T10:10:00"),
            primary_context="hub1",
            secondary_context="hub2",
        )

        assert result.total_duration_seconds == 600.0  # 10 minutes

    def test_cycle_result_with_phase_results(self):
        """Test CycleResult with populated phase results."""
        phases = [
            make_phase_result("preflight", start="2026-01-03T10:00:00", end="2026-01-03T10:01:00"),
            make_phase_result("primary_prep", start="2026-01-03T10:01:00", end="2026-01-03T10:02:00"),
            make_phase_result("activation", start="2026-01-03T10:02:00", end="2026-01-03T10:05:00"),
            make_phase_result("post_activation", start="2026-01-03T10:05:00", end="2026-01-03T10:08:00"),
            make_phase_result("finalization", start="2026-01-03T10:08:00", end="2026-01-03T10:10:00"),
        ]

        result = CycleResult(
            cycle_id="cycle_001",
            cycle_num=1,
            success=True,
            start_time=make_datetime("2026-01-03T10:00:00"),
            end_time=make_datetime("2026-01-03T10:10:00"),
            primary_context="hub1",
            secondary_context="hub2",
            phase_results=phases,
        )

        # Verify all phases are present
        assert len(result.phase_results) == 5
        phase_names = [p.phase_name for p in result.phase_results]
        assert "preflight" in phase_names
        assert "activation" in phase_names
        assert "finalization" in phase_names


@pytest.mark.e2e
class TestRunResultStructure:
    """Tests for RunResult structure."""

    def test_run_result_success_rate_calculation(self):
        """Test that success rate is calculated correctly."""
        from tests.e2e.orchestrator import RunResult

        cycles = [
            make_cycle_result(cycle_num=1, success=True),
            make_cycle_result(cycle_num=2, success=True),
            make_cycle_result(cycle_num=3, success=False),
            make_cycle_result(cycle_num=4, success=True),
            make_cycle_result(cycle_num=5, success=False),
        ]

        config = RunConfig(
            primary_context="hub1",
            secondary_context="hub2",
        )

        result = RunResult(
            run_id="run_test",
            config=config,
            cycles=cycles,
            start_time=make_datetime("2026-01-03T10:00:00"),
            end_time=make_datetime("2026-01-03T11:00:00"),
            success_count=3,
            failure_count=2,
        )

        assert result.success_rate == 60.0  # 3 out of 5 = 60%

    def test_run_result_total_duration(self):
        """Test that total duration is calculated correctly."""
        from tests.e2e.orchestrator import RunResult

        config = RunConfig(
            primary_context="hub1",
            secondary_context="hub2",
        )

        result = RunResult(
            run_id="run_test",
            config=config,
            cycles=[],
            start_time=make_datetime("2026-01-03T10:00:00"),
            end_time=make_datetime("2026-01-03T12:30:00"),
            success_count=0,
            failure_count=0,
        )

        assert result.total_duration_seconds == 9000.0  # 2.5 hours


@pytest.mark.e2e
class TestSoakControls:
    """Tests for soak testing controls (run-hours, max-failures, resume)."""

    def test_run_hours_config(self):
        """Test that run_hours config is properly set."""
        config = RunConfig(
            primary_context="hub1",
            secondary_context="hub2",
            run_hours=2.5,
        )
        assert config.run_hours == 2.5

    def test_max_failures_config(self):
        """Test that max_failures config is properly set."""
        config = RunConfig(
            primary_context="hub1",
            secondary_context="hub2",
            max_failures=3,
        )
        assert config.max_failures == 3

    def test_resume_config(self):
        """Test that resume config is properly set."""
        config = RunConfig(
            primary_context="hub1",
            secondary_context="hub2",
            resume=True,
        )
        assert config.resume is True

    def test_max_failures_stops_execution(self, tmp_path):
        """Test that max_failures stops execution after N failures."""
        config = RunConfig(
            primary_context="test-primary",
            secondary_context="test-secondary",
            dry_run=True,
            cycles=10,
            output_dir=tmp_path,
            max_failures=2,
            cooldown_seconds=0,
        )

        orchestrator = E2EOrchestrator(config)

        call_count = 0

        def always_fail(cycle_num, primary, secondary):
            nonlocal call_count
            call_count += 1
            return make_cycle_result(
                cycle_id=f"cycle_{cycle_num:03d}",
                cycle_num=cycle_num,
                success=False,
                primary_context=primary,
                secondary_context=secondary,
                error="Simulated failure",
            )

        with patch.object(orchestrator, "_run_cycle", side_effect=always_fail):
            result = orchestrator.run_all_cycles()

        assert result.failure_count == 2
        assert call_count == 2, "Should stop after 2 failures (max_failures=2)"

    def test_resume_state_saving(self, tmp_path):
        """Test that resume state is saved after each cycle."""
        config = RunConfig(
            primary_context="hub-a",
            secondary_context="hub-b",
            dry_run=True,
            cycles=3,
            output_dir=tmp_path,
            cooldown_seconds=0,
        )

        orchestrator = E2EOrchestrator(config)

        with patch.object(orchestrator, "_run_cycle") as mock_run_cycle:
            mock_run_cycle.return_value = make_cycle_result()
            orchestrator.run_all_cycles()

        # Resume state should be cleared after successful completion
        resume_path = config.output_dir / ".resume_state.json"
        assert not resume_path.exists(), "Resume state should be cleared after success"

    def test_resume_state_preserved_on_failure(self, tmp_path):
        """Test that resume state is preserved when stopped early due to failure."""
        config = RunConfig(
            primary_context="hub-a",
            secondary_context="hub-b",
            dry_run=True,
            cycles=5,
            output_dir=tmp_path,
            stop_on_failure=True,
            cooldown_seconds=0,
        )

        orchestrator = E2EOrchestrator(config)

        call_count = 0

        def fail_on_third(cycle_num, primary, secondary):
            nonlocal call_count
            call_count += 1
            success = call_count != 3
            return make_cycle_result(
                cycle_id=f"cycle_{cycle_num:03d}",
                cycle_num=cycle_num,
                success=success,
                primary_context=primary,
                secondary_context=secondary,
            )

        with patch.object(orchestrator, "_run_cycle", side_effect=fail_on_third):
            orchestrator.run_all_cycles()

        # Resume state should exist since run ended early
        resume_path = config.output_dir / ".resume_state.json"
        assert resume_path.exists(), "Resume state should be saved when stopping early"

        # Verify resume state contents
        with open(resume_path) as f:
            resume_state = json.load(f)

        assert resume_state["last_completed_cycle"] == 3, "Should record cycle 3 as last completed"
        assert resume_state["success_count"] == 2, "Should have 2 successful cycles"
        assert resume_state["failure_count"] == 1, "Should have 1 failed cycle"

    def test_resume_state_saved_on_max_failures(self, tmp_path):
        """Test that resume state is saved when max_failures limit is reached."""
        config = RunConfig(
            primary_context="hub-a",
            secondary_context="hub-b",
            dry_run=True,
            cycles=10,
            output_dir=tmp_path,
            max_failures=2,
            cooldown_seconds=0,
        )

        orchestrator = E2EOrchestrator(config)

        call_count = 0

        def fail_every_other(cycle_num, primary, secondary):
            nonlocal call_count
            call_count += 1
            # Fail on cycles 2 and 4
            success = call_count not in (2, 4)
            return make_cycle_result(
                cycle_id=f"cycle_{cycle_num:03d}",
                cycle_num=cycle_num,
                success=success,
                primary_context=primary,
                secondary_context=secondary,
            )

        with patch.object(orchestrator, "_run_cycle", side_effect=fail_every_other):
            result = orchestrator.run_all_cycles()

        # Should have stopped after 4 cycles (2 failures reached)
        assert result.failure_count == 2
        assert call_count == 4

        # Resume state should exist
        resume_path = config.output_dir / ".resume_state.json"
        assert resume_path.exists(), "Resume state should be saved when max_failures reached"

        with open(resume_path) as f:
            resume_state = json.load(f)

        assert resume_state["last_completed_cycle"] == 4
        assert resume_state["failure_count"] == 2
        assert "start_time" in resume_state, "Resume state should include start_time"
    
    def test_resume_preserves_start_time(self, tmp_path):
        """Test that resume preserves the original start_time for time limit enforcement."""
        # Manually save a resume state with specific start_time
        from datetime import datetime, timezone
        original_start = datetime(2026, 1, 4, 10, 0, 0, tzinfo=timezone.utc)
        resume_state = {
            "run_id": "test_run",
            "last_completed_cycle": 2,
            "current_primary": "hub-b",
            "current_secondary": "hub-a",
            "success_count": 2,
            "failure_count": 0,
            "start_time": original_start.isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        
        resume_path = tmp_path / ".resume_state.json"
        with open(resume_path, "w") as f:
            json.dump(resume_state, f)

        # Resume and run with max_failures so state is preserved
        config = RunConfig(
            primary_context="hub-a",
            secondary_context="hub-b",
            dry_run=True,
            cycles=10,
            output_dir=tmp_path,
            cooldown_seconds=0,
            max_failures=1,  # Stop after 1 failure to preserve state
            resume=True,
        )

        orchestrator = E2EOrchestrator(config)
        
        call_count = 0
        def fail_on_second(cycle_num, primary, secondary):
            nonlocal call_count
            call_count += 1
            # First call is cycle 3 (resuming from 2), fail on cycle 4
            success = call_count != 2
            return make_cycle_result(
                cycle_id=f"cycle_{cycle_num:03d}",
                cycle_num=cycle_num,
                success=success,
                primary_context=primary,
                secondary_context=secondary,
            )
        
        with patch.object(orchestrator, "_run_cycle", side_effect=fail_on_second):
            orchestrator.run_all_cycles()

        # Verify start_time was preserved in new resume state
        assert resume_path.exists(), "Resume state should still exist"
        with open(resume_path) as f:
            new_state = json.load(f)
        
        # The start_time should still be the original
        assert new_state["start_time"] == original_start.isoformat()

    def test_soak_config_in_manifest(self, tmp_path):
        """Test that soak controls appear in manifest."""
        config = RunConfig(
            primary_context="test-primary",
            secondary_context="test-secondary",
            dry_run=True,
            cycles=1,
            output_dir=tmp_path,
            run_hours=4.0,
            max_failures=5,
            resume=True,
        )

        orchestrator = E2EOrchestrator(config)

        with patch.object(orchestrator, "_run_cycle") as mock_run_cycle:
            mock_run_cycle.return_value = make_cycle_result()
            orchestrator.run_all_cycles()

        # Check manifest contains soak controls
        manifest_path = orchestrator.run_output_dir / "manifests" / "manifest.json"
        with open(manifest_path) as f:
            manifest = json.load(f)

        assert manifest["config"]["run_hours"] == 4.0
        assert manifest["config"]["max_failures"] == 5
        assert manifest["config"]["resume"] is True


@pytest.mark.e2e
class TestMetricsLogging:
    """Tests for JSONL metrics logging integration with orchestrator."""

    def test_metrics_logger_initialization(self, tmp_path):
        """Test that metrics logger is initialized by orchestrator."""
        config = RunConfig(
            primary_context="test-primary",
            secondary_context="test-secondary",
            dry_run=True,
            cycles=1,
            output_dir=tmp_path,
            cooldown_seconds=0,
        )

        orchestrator = E2EOrchestrator(config)

        # Before run_all_cycles, _metrics_logger is None
        assert orchestrator._metrics_logger is None

        # After run_all_cycles starts, metrics logger is initialized
        with patch.object(orchestrator, "_run_cycle") as mock_run_cycle:
            mock_run_cycle.return_value = make_cycle_result()
            orchestrator.run_all_cycles()

        # Metrics logger should be initialized
        assert orchestrator._metrics_logger is not None

    def test_metrics_directory_created(self, tmp_path):
        """Test that metrics directory is created."""
        config = RunConfig(
            primary_context="test-primary",
            secondary_context="test-secondary",
            dry_run=True,
            cycles=1,
            output_dir=tmp_path,
            cooldown_seconds=0,
        )

        orchestrator = E2EOrchestrator(config)

        # Metrics directory should exist after initialization
        metrics_dir = orchestrator.run_output_dir / "metrics"
        assert metrics_dir.exists(), "Metrics directory should exist"

    def test_metrics_logger_direct_logging(self, tmp_path):
        """Test MetricsLogger writes to JSONL file correctly."""
        from tests.e2e.monitoring import MetricsLogger

        metrics_dir = tmp_path / "metrics"
        logger = MetricsLogger(metrics_dir)

        # Log some events
        logger.log_cycle_start("cycle_001", 1, "primary", "secondary")
        logger.log_phase_result("cycle_001", "preflight", True, 15.0)
        logger.log_phase_result("cycle_001", "primary_prep", True, 30.0)
        logger.log_cycle_end("cycle_001", True, 45.0)

        # Check metrics file exists and has content
        metrics_file = metrics_dir / "metrics.jsonl"
        assert metrics_file.exists()

        with open(metrics_file) as f:
            lines = f.readlines()

        assert len(lines) == 4
        for line in lines:
            data = json.loads(line)
            assert "timestamp" in data
            assert "metric_type" in data

    def test_cycle_events_structure(self, tmp_path):
        """Test that cycle events have correct structure."""
        from tests.e2e.monitoring import MetricsLogger

        metrics_dir = tmp_path / "metrics"
        logger = MetricsLogger(metrics_dir)

        logger.log_cycle_start("cycle_001", 1, "ctx-a", "ctx-b")
        logger.log_cycle_end("cycle_001", True, 120.5)

        metrics_file = metrics_dir / "metrics.jsonl"
        with open(metrics_file) as f:
            events = [json.loads(line) for line in f.readlines()]

        # Check cycle_start event
        start_event = next(e for e in events if e["metric_type"] == "cycle_start")
        assert start_event["cycle_id"] == "cycle_001"
        assert start_event["cycle_num"] == 1
        assert start_event["primary_context"] == "ctx-a"
        assert start_event["secondary_context"] == "ctx-b"

        # Check cycle_end event
        end_event = next(e for e in events if e["metric_type"] == "cycle_end")
        assert end_event["cycle_id"] == "cycle_001"
        assert end_event["success"] is True
        assert end_event["duration_seconds"] == 120.5

    def test_phase_results_structure(self, tmp_path):
        """Test that phase results have correct structure."""
        from tests.e2e.monitoring import MetricsLogger

        metrics_dir = tmp_path / "metrics"
        logger = MetricsLogger(metrics_dir)

        logger.log_phase_result("cycle_001", "preflight", True, 15.3)
        logger.log_phase_result("cycle_001", "activation", False, 30.0, error="Timeout")

        metrics_file = metrics_dir / "metrics.jsonl"
        with open(metrics_file) as f:
            events = [json.loads(line) for line in f.readlines()]

        assert len(events) == 2

        # Check successful phase
        success_event = next(e for e in events if e["phase_name"] == "preflight")
        assert success_event["success"] is True
        assert success_event["duration_seconds"] == 15.3
        assert success_event["error"] is None

        # Check failed phase
        failed_event = next(e for e in events if e["phase_name"] == "activation")
        assert failed_event["success"] is False
        assert failed_event["error"] == "Timeout"
