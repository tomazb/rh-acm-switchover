# PR31 CLI Outcome/Report Orchestration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract the remaining CLI outcome/report orchestration seam from `acm_switchover.py` into `lib/cli_outcomes.py` while preserving current setup-mode behavior, non-setup exit semantics, state error-recording rules, report artifact shape, and GitOps summary emission order.

**Architecture:** Add `lib/cli_outcomes.py` as the direct owner of report target selection, phase summarization, Python report writing, setup-mode outcome handling, and the non-setup completion shell. Keep `acm_switchover.py` as the real entrypoint that still parses arguments, validates inputs, resolves the state file, prepares runtime state/clients, and exposes thin compatibility wrappers for `_report_target()`, `_phase_report_from_state()`, and `_write_python_report()`.

**Tech Stack:** Python, `pytest`, `unittest.mock`, `dataclasses`, `lib.report_artifacts`, `lib.operation_runners`, `lib.argocd_resume`, Thermos tracker docs.

---

## File Map

- Create: `lib/cli_outcomes.py`
- Create: `tests/test_cli_outcomes.py`
- Modify: `acm_switchover.py`
- Modify: `tests/test_main.py`
- Modify: `thermos-resolution-plan.md`
- Test: `tests/test_main_argocd_resume.py`
- Test: `tests/test_main_phase_flow.py`
- Test: `tests/test_operation_runners.py`
- Test: `tests/test_documentation_guardrails.py`

### Planned Responsibilities

- `lib/cli_outcomes.py`: direct owner of report target selection, phase summarization, report writing, setup-mode outcome handling, and the normal-operation completion shell
- `acm_switchover.py`: thin compatibility wrappers plus `main()` ordering for parse/validate/resolve/setup/runtime preparation
- `tests/test_cli_outcomes.py`: direct unit coverage for the extracted outcome/report module
- `tests/test_main.py`: wrapper and entrypoint delegation coverage proving `acm_switchover.py` still preserves the existing module surface and early branching
- `thermos-resolution-plan.md`: final PR31 scope summary, spec/plan references, and verification evidence

## Task 1: Create The Direct Report Helper Module And Tests

**Files:**
- Create: `lib/cli_outcomes.py`
- Create: `tests/test_cli_outcomes.py`

- [ ] **Step 1: Write failing direct tests for report target selection, phase summarization, and Python report writing**

```python
from types import SimpleNamespace
from unittest.mock import Mock, patch

from lib.utils import Phase

from lib.cli_outcomes import phase_report_from_state, report_target, write_python_report


def test_report_target_maps_cli_modes():
    assert report_target(SimpleNamespace(validate_only=True, decommission=False, restore_only=False)) == (
        "preflight",
        "preflight-report.json",
    )
    assert report_target(SimpleNamespace(validate_only=False, decommission=True, restore_only=False)) == (
        "decommission",
        "decommission-report.json",
    )
    assert report_target(SimpleNamespace(validate_only=False, decommission=False, restore_only=True)) == (
        "restore",
        "restore-only-report.json",
    )
    assert report_target(SimpleNamespace(validate_only=False, decommission=False, restore_only=False)) == (
        "switchover",
        "switchover-report.json",
    )


def test_phase_report_from_state_marks_completed_and_failed_phases():
    state_snapshot = {
        "completed_steps": [
            {"name": "preflight_cluster_access"},
            {"name": "pause_backup_schedule"},
            {"name": "verify_managed_clusters"},
        ],
        "current_phase": Phase.FAILED.value,
        "errors": [{"phase": Phase.ACTIVATION.value, "message": "boom"}],
    }

    phases = phase_report_from_state(state_snapshot)

    assert phases["preflight"]["status"] == "pass"
    assert phases["primary_prep"]["steps"] == ["pause_backup_schedule"]
    assert phases["post_activation"]["steps"] == ["verify_managed_clusters"]
    assert phases["activation"]["status"] == "fail"


def test_write_python_report_is_noop_without_report_dir_or_state():
    logger = Mock()

    write_python_report(SimpleNamespace(report_dir=None), Mock(), "pass", logger)
    write_python_report(SimpleNamespace(report_dir="/tmp/reports"), None, "pass", logger)

    logger.info.assert_not_called()
    logger.error.assert_not_called()


def test_write_python_report_builds_and_writes_artifact():
    args = SimpleNamespace(
        report_dir="/tmp/reports",
        validate_only=False,
        decommission=False,
        restore_only=False,
    )
    state = Mock()
    state.capture_state_snapshot.return_value = {
        "completed_steps": [{"name": "preflight_cluster_access"}],
        "current_phase": Phase.COMPLETED.value,
        "errors": [],
    }
    logger = Mock()

    with patch("lib.cli_outcomes.build_operation_report", return_value={"schema_version": "1.0"}) as build_report, patch(
        "lib.cli_outcomes.write_json_report_artifact",
        return_value="/tmp/reports/switchover-report.json",
    ) as write_artifact:
        write_python_report(args, state, "pass", logger)

    build_report.assert_called_once()
    write_artifact.assert_called_once_with({"schema_version": "1.0"}, "/tmp/reports/switchover-report.json")
    logger.info.assert_called_once_with("Wrote report artifact: %s", "/tmp/reports/switchover-report.json")
```

- [ ] **Step 2: Run the new direct tests and confirm the module does not exist yet**

Run: `python -m pytest tests/test_cli_outcomes.py -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'lib.cli_outcomes'`

- [ ] **Step 3: Create `lib/cli_outcomes.py` with the extracted report helpers**

```python
from __future__ import annotations

import logging
import os
from typing import Any, Optional

from lib.report_artifacts import PYTHON_REPORT_SOURCE, build_operation_report, write_json_report_artifact
from lib.utils import Phase, StateManager


def report_target(args: Any) -> tuple[str, str]:
    """Return report type and filename for the current Python CLI operation."""
    if getattr(args, "validate_only", False):
        return "preflight", "preflight-report.json"
    if getattr(args, "decommission", False):
        return "decommission", "decommission-report.json"
    if getattr(args, "restore_only", False):
        return "restore", "restore-only-report.json"
    return "switchover", "switchover-report.json"


def phase_report_from_state(state_snapshot: dict) -> dict[str, dict[str, Any]]:
    """Build a compact phase map from durable state."""
    phases: dict[str, dict[str, Any]] = {}
    phase_by_step_prefix = {
        "preflight": "preflight",
        "pause_argocd": "preflight",
        "pause_backup": "primary_prep",
        "disable_auto_import": "primary_prep",
        "scale_down": "primary_prep",
        "verify_passive_sync": "activation",
        "activate_managed_clusters": "activation",
        "create_full_restore": "activation",
        "wait_restore_completion": "activation",
        "apply_immediate_import": "activation",
        "verify_managed_clusters": "post_activation",
        "verify_klusterlet": "post_activation",
        "enable_backup_schedule": "finalization",
        "verify_backup_schedule": "finalization",
        "fix_backup_collision": "finalization",
        "verify_new_backups": "finalization",
        "verify_backup_integrity": "finalization",
        "verify_mch_health": "finalization",
        "handle_old_hub": "finalization",
    }

    for step in state_snapshot.get("completed_steps", []) or []:
        name = step.get("name", "")
        phase = next((value for prefix, value in phase_by_step_prefix.items() if name.startswith(prefix)), None)
        if not phase:
            continue
        phases.setdefault(phase, {"phase": phase, "status": "pass", "steps": []})["steps"].append(name)

    if state_snapshot.get("current_phase") == Phase.FAILED.value:
        errors = state_snapshot.get("errors", []) or []
        failed_phase_value = (errors[-1] or {}).get("phase") if errors else None
        failed_phase = {
            Phase.PREFLIGHT.value: "preflight",
            Phase.PRIMARY_PREP.value: "primary_prep",
            Phase.ACTIVATION.value: "activation",
            Phase.POST_ACTIVATION.value: "post_activation",
            Phase.FINALIZATION.value: "finalization",
        }.get(failed_phase_value)
        if failed_phase:
            phases.setdefault(failed_phase, {"phase": failed_phase, "status": "pass", "steps": []})["status"] = "fail"

    return phases


def write_python_report(args: Any, state: Optional[StateManager], status: str, logger: logging.Logger) -> None:
    """Write a Python CLI report artifact when --report-dir is set."""
    report_dir = getattr(args, "report_dir", None)
    if not report_dir or state is None:
        return

    try:
        report_type, filename = report_target(args)
        state_snapshot = state.capture_state_snapshot()
        report = build_operation_report(
            report_type=report_type,
            status=status,
            source=PYTHON_REPORT_SOURCE,
            args=args,
            state_snapshot=state_snapshot,
            phases=phase_report_from_state(state_snapshot),
        )
        destination = os.path.join(report_dir, filename)
        written_path = write_json_report_artifact(report, destination)
        logger.info("Wrote report artifact: %s", written_path)
    except Exception as exc:
        logger.error("Failed to write report artifact: %s", exc)
```

- [ ] **Step 4: Re-run the direct report-helper tests and verify they pass**

Run: `python -m pytest tests/test_cli_outcomes.py -q`

Expected: PASS

- [ ] **Step 5: Commit the extracted report-helper slice**

```bash
git add lib/cli_outcomes.py tests/test_cli_outcomes.py
git commit -m "refactor: extract cli report helpers"
```

## Task 2: Add Setup-Mode And Runtime-Operation Outcome Helpers

**Files:**
- Modify: `lib/cli_outcomes.py`
- Modify: `tests/test_cli_outcomes.py`

- [ ] **Step 1: Extend the direct test file with failing setup and runtime outcome tests**

```python
from types import SimpleNamespace
from unittest.mock import Mock

from lib.cli_outcomes import CliOperationHooks, run_operation_mode, run_setup_mode
from lib.exceptions import SwitchoverError


def test_run_setup_mode_returns_success_exit_code_and_logs_success():
    logger = Mock()

    exit_code = run_setup_mode(
        SimpleNamespace(verbose=False),
        logger,
        run_setup=lambda args, logger: True,
        exit_success=0,
        exit_failure=1,
        exit_interrupt=130,
    )

    assert exit_code == 0
    logger.info.assert_any_call("\n✓ Setup completed successfully!")


def test_run_setup_mode_handles_keyboard_interrupt():
    logger = Mock()

    def _raise_interrupt(args, logger):
        raise KeyboardInterrupt

    exit_code = run_setup_mode(
        SimpleNamespace(verbose=False),
        logger,
        run_setup=_raise_interrupt,
        exit_success=0,
        exit_failure=1,
        exit_interrupt=130,
    )

    assert exit_code == 130
    logger.warning.assert_called_once_with("\n\nSetup interrupted by user")


def test_run_setup_mode_returns_failure_exit_code_when_runner_returns_false():
    logger = Mock()

    exit_code = run_setup_mode(
        SimpleNamespace(verbose=False),
        logger,
        run_setup=lambda args, logger: False,
        exit_success=0,
        exit_failure=1,
        exit_interrupt=130,
    )

    assert exit_code == 1
    logger.error.assert_called_once_with("\n✗ Setup failed!")


def test_run_setup_mode_logs_unexpected_exception_with_verbose_flag():
    logger = Mock()
    failure = RuntimeError("boom")

    def _raise_failure(args, logger):
        raise failure

    exit_code = run_setup_mode(
        SimpleNamespace(verbose=True),
        logger,
        run_setup=_raise_failure,
        exit_success=0,
        exit_failure=1,
        exit_interrupt=130,
    )

    assert exit_code == 1
    logger.error.assert_called_once_with("\n✗ Setup failed: %s", failure, exc_info=True)


def test_run_operation_mode_returns_success_and_writes_pass_report():
    args = SimpleNamespace(argocd_resume_only=False, verbose=False, state_file="state.json")
    state = Mock()
    logger = Mock()
    reporter = Mock()
    hooks = CliOperationHooks(
        bind_runtime_hub_identities=Mock(),
        run_argocd_resume_only=Mock(),
        execute_operation=Mock(return_value=True),
        write_python_report=Mock(),
        gitops_reporter_factory=lambda: reporter,
    )

    exit_code = run_operation_mode(
        args,
        state,
        Mock(),
        Mock(),
        logger,
        should_bind_state=True,
        should_record_state_errors=True,
        hooks=hooks,
        exit_success=0,
        exit_failure=1,
        exit_interrupt=130,
    )

    assert exit_code == 0
    logger.info.assert_any_call("\n✓ Operation completed successfully!")
    hooks.write_python_report.assert_called_once_with(args, state, "pass", logger)
    reporter.print_report.assert_called_once()


def test_run_operation_mode_uses_resume_only_failure_message():
    args = SimpleNamespace(argocd_resume_only=True, verbose=False, state_file="state.json")
    state = Mock()
    logger = Mock()
    reporter = Mock()
    hooks = CliOperationHooks(
        bind_runtime_hub_identities=Mock(),
        run_argocd_resume_only=Mock(return_value=False),
        execute_operation=Mock(),
        write_python_report=Mock(),
        gitops_reporter_factory=lambda: reporter,
    )

    exit_code = run_operation_mode(
        args,
        state,
        Mock(),
        Mock(),
        logger,
        should_bind_state=False,
        should_record_state_errors=True,
        hooks=hooks,
        exit_success=0,
        exit_failure=1,
        exit_interrupt=130,
    )

    assert exit_code == 1
    logger.error.assert_any_call("\n✗ Argo CD resume failed or had nothing to restore.")
    hooks.write_python_report.assert_called_once_with(args, state, "fail", logger)
    reporter.print_report.assert_called_once()


def test_run_operation_mode_handles_keyboard_interrupt_without_recording_state_error():
    args = SimpleNamespace(argocd_resume_only=False, verbose=False, state_file="state.json")
    state = Mock()
    logger = Mock()
    reporter = Mock()
    hooks = CliOperationHooks(
        bind_runtime_hub_identities=Mock(side_effect=KeyboardInterrupt),
        run_argocd_resume_only=Mock(),
        execute_operation=Mock(),
        write_python_report=Mock(),
        gitops_reporter_factory=lambda: reporter,
    )

    exit_code = run_operation_mode(
        args,
        state,
        Mock(),
        Mock(),
        logger,
        should_bind_state=True,
        should_record_state_errors=True,
        hooks=hooks,
        exit_success=0,
        exit_failure=1,
        exit_interrupt=130,
    )

    assert exit_code == 130
    state.add_error.assert_not_called()
    logger.warning.assert_called_once_with("\n\nOperation interrupted by user")
    hooks.write_python_report.assert_called_once_with(args, state, "fail", logger)
    reporter.print_report.assert_called_once()


def test_run_operation_mode_records_unexpected_error_and_finalizes_report():
    args = SimpleNamespace(argocd_resume_only=False, verbose=False, state_file="state.json")
    state = Mock()
    logger = Mock()
    report_order = []
    reporter = Mock()
    reporter.print_report.side_effect = lambda: report_order.append("gitops")
    hooks = CliOperationHooks(
        bind_runtime_hub_identities=Mock(side_effect=RuntimeError("boom")),
        run_argocd_resume_only=Mock(),
        execute_operation=Mock(),
        write_python_report=lambda *call_args: report_order.append("report"),
        gitops_reporter_factory=lambda: reporter,
    )

    exit_code = run_operation_mode(
        args,
        state,
        Mock(),
        Mock(),
        logger,
        should_bind_state=True,
        should_record_state_errors=True,
        hooks=hooks,
        exit_success=0,
        exit_failure=1,
        exit_interrupt=130,
    )

    assert exit_code == 1
    state.add_error.assert_called_once_with("boom")
    assert report_order == ["report", "gitops"]


def test_run_operation_mode_handles_switchover_error_without_exc_info():
    args = SimpleNamespace(argocd_resume_only=False, verbose=True, state_file="state.json")
    state = Mock()
    logger = Mock()
    reporter = Mock()
    failure = SwitchoverError("bad news")
    hooks = CliOperationHooks(
        bind_runtime_hub_identities=Mock(),
        run_argocd_resume_only=Mock(),
        execute_operation=Mock(side_effect=failure),
        write_python_report=Mock(),
        gitops_reporter_factory=lambda: reporter,
    )

    exit_code = run_operation_mode(
        args,
        state,
        Mock(),
        Mock(),
        logger,
        should_bind_state=False,
        should_record_state_errors=True,
        hooks=hooks,
        exit_success=0,
        exit_failure=1,
        exit_interrupt=130,
    )

    assert exit_code == 1
    logger.error.assert_called_once_with("\n✗ %s", failure)
    state.add_error.assert_called_once_with("bad news")
    hooks.write_python_report.assert_called_once_with(args, state, "fail", logger)
    reporter.print_report.assert_called_once()
```

- [ ] **Step 2: Run the expanded direct tests and confirm the new helpers are still missing**

Run: `python -m pytest tests/test_cli_outcomes.py -q`

Expected: FAIL with `ImportError` or `AttributeError` for `CliOperationHooks`, `run_setup_mode`, or `run_operation_mode`

- [ ] **Step 3: Extend `lib/cli_outcomes.py` with the outcome orchestration helpers**

```python
from dataclasses import dataclass
from typing import Callable

from lib.exceptions import SwitchoverError


@dataclass(frozen=True)
class CliOperationHooks:
    bind_runtime_hub_identities: Callable[..., None]
    run_argocd_resume_only: Callable[..., bool]
    execute_operation: Callable[..., bool]
    write_python_report: Callable[..., None]
    gitops_reporter_factory: Callable[[], Any]


def run_setup_mode(
    args: Any,
    logger: logging.Logger,
    *,
    run_setup: Callable[[Any, logging.Logger], bool],
    exit_success: int,
    exit_failure: int,
    exit_interrupt: int,
) -> int:
    """Run setup mode and return the final process exit code."""
    try:
        success = run_setup(args, logger)
    except KeyboardInterrupt:
        logger.warning("\n\nSetup interrupted by user")
        return exit_interrupt
    except Exception as exc:
        logger.error("\n✗ Setup failed: %s", exc, exc_info=getattr(args, "verbose", False))
        return exit_failure

    if success:
        logger.info("\n✓ Setup completed successfully!")
        return exit_success

    logger.error("\n✗ Setup failed!")
    return exit_failure


def run_operation_mode(
    args: Any,
    state: StateManager,
    primary: Any,
    secondary: Any,
    logger: logging.Logger,
    *,
    should_bind_state: bool,
    should_record_state_errors: bool,
    hooks: CliOperationHooks,
    exit_success: int,
    exit_failure: int,
    exit_interrupt: int,
) -> int:
    """Run the prepared non-setup operation path and return the final exit code."""
    exit_code = exit_failure
    try:
        if should_bind_state:
            hooks.bind_runtime_hub_identities(args, state, primary, secondary)
        if getattr(args, "argocd_resume_only", False):
            success = hooks.run_argocd_resume_only(args, state, primary, secondary, logger)
        else:
            success = hooks.execute_operation(args, state, primary, secondary, logger)
    except KeyboardInterrupt:
        logger.warning("\n\nOperation interrupted by user")
        logger.info("State saved to: %s", args.state_file)
        logger.info("Re-run the same command to resume from last successful step")
        exit_code = exit_interrupt
    except SwitchoverError as exc:
        logger.error("\n✗ %s", exc)
        if should_record_state_errors:
            state.add_error(str(exc))
        exit_code = exit_failure
    except Exception as exc:
        logger.error("\n✗ Unexpected error: %s", exc, exc_info=getattr(args, "verbose", False))
        if should_record_state_errors:
            state.add_error(str(exc))
        exit_code = exit_failure
    else:
        if success:
            if getattr(args, "argocd_resume_only", False):
                logger.info("\n✓ Argo CD resume completed successfully!")
            else:
                logger.info("\n✓ Operation completed successfully!")
            exit_code = exit_success
        else:
            if getattr(args, "argocd_resume_only", False):
                logger.error("\n✗ Argo CD resume failed or had nothing to restore.")
            else:
                logger.error("\n✗ Operation failed!")
            exit_code = exit_failure
    finally:
        hooks.write_python_report(args, state, "pass" if exit_code == exit_success else "fail", logger)
        hooks.gitops_reporter_factory().print_report()

    return exit_code
```

- [ ] **Step 4: Re-run the direct outcome tests and verify they pass**

Run: `python -m pytest tests/test_cli_outcomes.py -q`

Expected: PASS

- [ ] **Step 5: Commit the outcome-orchestration helper slice**

```bash
git add lib/cli_outcomes.py tests/test_cli_outcomes.py
git commit -m "refactor: extract cli outcome orchestration"
```

## Task 3: Wire Compatibility Wrappers And `main()` Delegation

**Files:**
- Modify: `acm_switchover.py`
- Modify: `tests/test_main.py`
- Test: `tests/test_main_argocd_resume.py`
- Test: `tests/test_main_phase_flow.py`
- Test: `tests/test_operation_runners.py`

- [ ] **Step 1: Add failing wrapper and entrypoint delegation tests to `tests/test_main.py`**

```python
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from acm_switchover import (
    EXIT_FAILURE,
    EXIT_INTERRUPT,
    EXIT_SUCCESS,
    _phase_report_from_state,
    _report_target,
    _write_python_report,
    main,
    run_setup,
)


def test_report_target_delegates_to_cli_outcomes_module():
    args = SimpleNamespace(validate_only=False, decommission=False, restore_only=True)

    with patch("acm_switchover.cli_outcomes.report_target", return_value=("restore", "restore-only-report.json")) as report_target:
        assert _report_target(args) == ("restore", "restore-only-report.json")

    report_target.assert_called_once_with(args)


def test_write_python_report_delegates_to_cli_outcomes_module():
    args = SimpleNamespace(report_dir="/tmp/reports")
    state = Mock()
    logger = Mock()

    with patch("acm_switchover.cli_outcomes.write_python_report", return_value=None) as write_report:
        _write_python_report(args, state, "pass", logger)

    write_report.assert_called_once_with(args, state, "pass", logger)


def test_phase_report_from_state_delegates_to_cli_outcomes_module():
    snapshot = {"completed_steps": [], "current_phase": "INIT", "errors": []}

    with patch("acm_switchover.cli_outcomes.phase_report_from_state", return_value={"preflight": {"status": "pass"}}) as phase_report:
        assert _phase_report_from_state(snapshot) == {"preflight": {"status": "pass"}}

    phase_report.assert_called_once_with(snapshot)


def test_main_setup_branch_delegates_to_cli_outcomes_setup_helper():
    args = SimpleNamespace(
        verbose=False,
        log_format="text",
        state_file="state.json",
        primary_context="primary",
        secondary_context="secondary",
        skip_gitops_check=False,
        validate_only=False,
        argocd_manage=False,
        setup=True,
        reset_state=False,
        argocd_resume_only=False,
    )
    logger = Mock()

    with patch("acm_switchover.parse_args", return_value=args), patch(
        "acm_switchover.setup_logging",
        return_value=logger,
    ), patch("acm_switchover.validate_args"), patch(
        "acm_switchover._resolve_state_file",
        return_value="state.json",
    ), patch(
        "acm_switchover.cli_outcomes.run_setup_mode",
        return_value=EXIT_SUCCESS,
    ) as run_setup_mode:
        with pytest.raises(SystemExit) as exc_info:
            main()

    assert exc_info.value.code == EXIT_SUCCESS
    run_setup_mode.assert_called_once_with(
        args,
        logger,
        run_setup=run_setup,
        exit_success=EXIT_SUCCESS,
        exit_failure=EXIT_FAILURE,
        exit_interrupt=EXIT_INTERRUPT,
    )


def test_main_non_setup_branch_delegates_to_cli_outcomes_operation_helper():
    args = SimpleNamespace(
        verbose=False,
        log_format="text",
        state_file="state.json",
        primary_context="primary",
        secondary_context="secondary",
        skip_gitops_check=False,
        validate_only=False,
        argocd_manage=False,
        setup=False,
        reset_state=False,
        argocd_resume_only=False,
    )
    logger = Mock()
    state = Mock()
    primary = Mock()
    secondary = Mock()
    runtime = SimpleNamespace(
        state=state,
        primary=primary,
        secondary=secondary,
        should_bind_state=True,
        should_record_state_errors=True,
    )

    with patch("acm_switchover.parse_args", return_value=args), patch(
        "acm_switchover.setup_logging",
        return_value=logger,
    ), patch("acm_switchover.validate_args"), patch(
        "acm_switchover._resolve_state_file",
        return_value="state.json",
    ), patch(
        "acm_switchover.os.path.exists",
        return_value=True,
    ), patch(
        "acm_switchover._prepare_runtime",
        return_value=runtime,
    ), patch(
        "acm_switchover._build_cli_operation_hooks",
        return_value="hooks",
    ), patch(
        "acm_switchover.cli_outcomes.run_operation_mode",
        return_value=EXIT_SUCCESS,
    ) as run_operation_mode:
        with pytest.raises(SystemExit) as exc_info:
            main()

    assert exc_info.value.code == EXIT_SUCCESS
    run_operation_mode.assert_called_once_with(
        args,
        state,
        primary,
        secondary,
        logger,
        should_bind_state=True,
        should_record_state_errors=True,
        hooks="hooks",
        exit_success=EXIT_SUCCESS,
        exit_failure=EXIT_FAILURE,
        exit_interrupt=EXIT_INTERRUPT,
    )
```

- [ ] **Step 2: Run the focused entrypoint tests and confirm they fail before wiring**

Run: `python -m pytest tests/test_main.py -q`

Expected: FAIL because `acm_switchover.py` still owns the report helpers and `main()` does not yet delegate to `lib.cli_outcomes`

- [ ] **Step 3: Modify only the wrapper definitions and the setup/runtime branches in `main()`, while preserving the existing `_resolve_state_file` error handling, startup logging, GitOps enable/disable block, and early state-file guard**

```python
from lib import argocd_resume, cli_outcomes, operation_runners, runtime_bootstrap


def _report_target(args: argparse.Namespace) -> tuple[str, str]:
    """Return report type and filename for the current Python CLI operation."""
    return cli_outcomes.report_target(args)


def _phase_report_from_state(state_snapshot: dict) -> dict:
    """Build a compact phase map from durable state."""
    return cli_outcomes.phase_report_from_state(state_snapshot)


def _write_python_report(
    args: argparse.Namespace,
    state: Optional[StateManager],
    status: str,
    logger: logging.Logger,
) -> None:
    """Write a Python CLI report artifact when --report-dir is set."""
    cli_outcomes.write_python_report(args, state, status, logger)


def _build_cli_operation_hooks() -> cli_outcomes.CliOperationHooks:
    return cli_outcomes.CliOperationHooks(
        bind_runtime_hub_identities=_bind_runtime_hub_identities,
        run_argocd_resume_only=_run_argocd_resume_only,
        execute_operation=_execute_operation,
        write_python_report=_write_python_report,
        gitops_reporter_factory=GitOpsCollector.get_instance,
    )


def main():  # noqa: C901
    args = parse_args()
    state: Optional[StateManager] = None
    logger = setup_logging(args.verbose, args.log_format)

    validate_args(args, logger)
    try:
        resolved_state_file = _resolve_state_file(
            args.state_file,
            getattr(args, "primary_context", None),
            args.secondary_context,
            argocd_resume_only=getattr(args, "argocd_resume_only", False),
        )
    except ValueError as exc:
        logger.error("%s", exc)
        sys.exit(EXIT_FAILURE)
    args.state_file = resolved_state_file

    logger.info("ACM Hub Switchover Automation v%s (%s)", __version__, __version_date__)
    logger.info("Started at: %s", datetime.now(timezone.utc).isoformat())
    logger.info("Using state file: %s", resolved_state_file)

    if args.skip_gitops_check:
        GitOpsCollector.get_instance().set_enabled(False)
        logger.debug("GitOps marker detection disabled")

    if args.setup:
        sys.exit(
            cli_outcomes.run_setup_mode(
                args,
                logger,
                run_setup=run_setup,
                exit_success=EXIT_SUCCESS,
                exit_failure=EXIT_FAILURE,
                exit_interrupt=EXIT_INTERRUPT,
            )
        )

    if getattr(args, "argocd_resume_only", False) and not os.path.exists(resolved_state_file):
        logger.error(
            "State file not found for --argocd-resume-only: %s. "
            "Run a switchover with Argo CD management first or pass --state-file explicitly.",
            resolved_state_file,
        )
        sys.exit(EXIT_FAILURE)

    runtime = _prepare_runtime(args, logger, resolved_state_file)
    state = runtime.state

    operation_exit_code = cli_outcomes.run_operation_mode(
        args,
        state,
        runtime.primary,
        runtime.secondary,
        logger,
        should_bind_state=runtime.should_bind_state,
        should_record_state_errors=runtime.should_record_state_errors,
        hooks=_build_cli_operation_hooks(),
        exit_success=EXIT_SUCCESS,
        exit_failure=EXIT_FAILURE,
        exit_interrupt=EXIT_INTERRUPT,
    )
    sys.exit(operation_exit_code)
```

- [ ] **Step 4: Run the focused regression suite and verify the wrappers plus entrypoint still behave correctly**

Run: `python -m pytest tests/test_cli_outcomes.py tests/test_main.py tests/test_main_phase_flow.py tests/test_main_argocd_resume.py tests/test_operation_runners.py -q`

Expected: PASS

- [ ] **Step 5: Commit the compatibility-wiring slice**

```bash
git add acm_switchover.py tests/test_main.py lib/cli_outcomes.py tests/test_cli_outcomes.py
git commit -m "refactor: delegate cli outcome handling"
```

## Task 4: Update The Tracker And Run Final Verification

**Files:**
- Modify: `thermos-resolution-plan.md`
- Test: `tests/test_documentation_guardrails.py`
- Test: `tests/test_cli_outcomes.py`
- Test: `tests/test_main.py`
- Test: `tests/test_main_phase_flow.py`
- Test: `tests/test_main_argocd_resume.py`
- Test: `tests/test_operation_runners.py`

- [ ] **Step 1: Update the PR31 tracker row with the final scope summary and verification evidence**

```md
| 31 | ready_for_review | `refactor/thermos-31-cli-report-orchestration` | `.worktrees/thermos-31-cli-reporting` | F44 CLI outcome/report orchestration extraction | — | Added design spec `docs/superpowers/specs/2026-06-08-pr31-cli-outcome-report-design.md` and implementation plan `docs/superpowers/plans/2026-06-08-pr31-cli-outcome-report-orchestration.md`. `lib/cli_outcomes.py` now owns report target selection, phase summarization, Python report writing, setup-mode outcome handling, and the non-setup completion shell; `acm_switchover.py` keeps thin compatibility wrappers plus entrypoint ordering. Verification: `python -m pytest tests/test_cli_outcomes.py tests/test_main.py tests/test_main_phase_flow.py tests/test_main_argocd_resume.py tests/test_operation_runners.py tests/test_documentation_guardrails.py -q` passed; `graphify update .` passed; `git diff --check` passed; final `./run_tests.sh` passed. |
```

- [ ] **Step 2: Run the focused verification suite, including the documentation guardrail**

Run: `python -m pytest tests/test_cli_outcomes.py tests/test_main.py tests/test_main_phase_flow.py tests/test_main_argocd_resume.py tests/test_operation_runners.py tests/test_documentation_guardrails.py -q`

Expected: PASS

- [ ] **Step 3: Refresh the code graph and confirm the working tree is whitespace-clean**

Run: `graphify update . && git diff --check`

Expected: graph update completes successfully and `git diff --check` prints no output

- [ ] **Step 4: Run the full strict repository verification lane**

Run: `./run_tests.sh`

Expected: PASS, including formatting, type checking, bandit, root/release pytest lanes, and compile checks

- [ ] **Step 5: Commit the tracker update and final verification state**

```bash
git add thermos-resolution-plan.md
git commit -m "docs: update pr31 tracker"
```
