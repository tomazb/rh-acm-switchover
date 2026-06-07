# PR29 Operation Runner Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract the switchover/restore-only runner logic and `_execute_operation()` dispatch from `acm_switchover.py` into `lib/operation_runners.py` while keeping the existing `acm_switchover.*` wrapper surface patchable and behavior-preserving.

**Architecture:** Add a new `lib/operation_runners.py` module that owns direct dispatch and runner implementations behind explicit frozen hook dataclasses. Keep `run_switchover()`, `run_restore_only()`, `_run_switchover_impl()`, `_run_restore_only_impl()`, and `_execute_operation()` in `acm_switchover.py` as thin compatibility wrappers that inject the current phase and failure handlers, with `_run_restore_only_argocd_pause()` intentionally left in `acm_switchover.py` and passed into the extracted restore-only runner.

**Tech Stack:** Python, `pytest`, `unittest.mock`, `lib.workflow`, `lib.constants`, Graphify, Thermos tracker docs.

---

## File Map

- Create: `docs/superpowers/plans/2026-06-07-pr29-operation-runner-extraction.md`
- Create: `lib/operation_runners.py`
- Create: `tests/test_operation_runners.py`
- Modify: `acm_switchover.py`
- Modify: `tests/test_main.py`
- Modify: `thermos-resolution-plan.md`
- Test: `tests/test_main_phase_flow.py`
- Test: `tests/test_documentation_guardrails.py`

### Planned Responsibilities

- `lib/operation_runners.py`: direct owner of dispatch, switchover runner orchestration, and restore-only runner orchestration behind injected hook dataclasses
- `acm_switchover.py`: compatibility wrappers plus hook-builder helpers that preserve the current patch/import surface
- `tests/test_operation_runners.py`: direct unit coverage for the extracted module
- `tests/test_main.py`: wrapper-level compatibility coverage proving `acm_switchover` still injects the right phase/failure/decommission hooks
- `thermos-resolution-plan.md`: PR29 design/plan references and final verification evidence for the implementation branch

## Task 1: Create The Dispatch Helper Module And Direct Dispatch Tests

**Files:**
- Create: `lib/operation_runners.py`
- Create: `tests/test_operation_runners.py`

- [ ] **Step 1: Write the failing direct dispatch tests for the new module**

```python
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from lib.operation_runners import OperationDispatchHooks, execute_operation


def test_execute_operation_routes_decommission_without_touching_secondary():
    args = SimpleNamespace(decommission=True, restore_only=False)
    state = Mock()
    primary = Mock()
    logger = Mock()
    hooks = OperationDispatchHooks(
        decommission_runner=Mock(return_value=True),
        restore_only_runner=Mock(return_value=False),
        switchover_runner=Mock(return_value=False),
    )

    result = execute_operation(args, state, primary, None, logger, hooks=hooks)

    assert result is True
    hooks.decommission_runner.assert_called_once_with(args, primary, state, logger)
    hooks.restore_only_runner.assert_not_called()
    hooks.switchover_runner.assert_not_called()


def test_execute_operation_requires_secondary_for_restore_only():
    args = SimpleNamespace(decommission=False, restore_only=True)
    hooks = OperationDispatchHooks(Mock(), Mock(), Mock())

    with pytest.raises(ValueError, match="Secondary context is required for restore-only"):
        execute_operation(args, Mock(), Mock(), None, Mock(), hooks=hooks)


def test_execute_operation_routes_restore_only_with_secondary():
    args = SimpleNamespace(decommission=False, restore_only=True)
    state = Mock()
    secondary = Mock()
    logger = Mock()
    hooks = OperationDispatchHooks(
        decommission_runner=Mock(return_value=False),
        restore_only_runner=Mock(return_value=True),
        switchover_runner=Mock(return_value=False),
    )

    result = execute_operation(args, state, None, secondary, logger, hooks=hooks)

    assert result is True
    hooks.restore_only_runner.assert_called_once_with(args, state, secondary, logger)


def test_execute_operation_requires_secondary_for_switchover():
    args = SimpleNamespace(decommission=False, restore_only=False)
    hooks = OperationDispatchHooks(Mock(), Mock(), Mock())

    with pytest.raises(ValueError, match="Secondary context is required for switchover"):
        execute_operation(args, Mock(), Mock(), None, Mock(), hooks=hooks)


def test_execute_operation_routes_switchover_when_secondary_exists():
    args = SimpleNamespace(decommission=False, restore_only=False)
    state = Mock()
    primary = Mock()
    secondary = Mock()
    logger = Mock()
    hooks = OperationDispatchHooks(
        decommission_runner=Mock(return_value=False),
        restore_only_runner=Mock(return_value=False),
        switchover_runner=Mock(return_value=True),
    )

    result = execute_operation(args, state, primary, secondary, logger, hooks=hooks)

    assert result is True
    hooks.switchover_runner.assert_called_once_with(args, state, primary, secondary, logger)
```

- [ ] **Step 2: Run the new dispatch tests to confirm the module does not exist yet**

Run: `python -m pytest tests/test_operation_runners.py -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'lib.operation_runners'`

- [ ] **Step 3: Create `lib/operation_runners.py` with the dispatch dataclass and function**

```python
from dataclasses import dataclass
from typing import Any, Callable, Optional


DecommissionRunner = Callable[[Any, Any, Any, Any], bool]
RestoreOnlyRunner = Callable[[Any, Any, Any, Any], bool]
SwitchoverRunner = Callable[[Any, Any, Any, Any, Any], bool]


@dataclass(frozen=True)
class OperationDispatchHooks:
    decommission_runner: DecommissionRunner
    restore_only_runner: RestoreOnlyRunner
    switchover_runner: SwitchoverRunner


def execute_operation(
    args: Any,
    state: Any,
    primary: Optional[Any],
    secondary: Optional[Any],
    logger: Any,
    *,
    hooks: OperationDispatchHooks,
) -> bool:
    if args.decommission:
        return hooks.decommission_runner(args, primary, state, logger)

    if getattr(args, "restore_only", False):
        if secondary is None:
            raise ValueError("Secondary context is required for restore-only")
        return hooks.restore_only_runner(args, state, secondary, logger)

    if secondary is None:
        raise ValueError("Secondary context is required for switchover")

    return hooks.switchover_runner(args, state, primary, secondary, logger)
```

- [ ] **Step 4: Re-run the dispatch tests and verify they pass**

Run: `python -m pytest tests/test_operation_runners.py -q`

Expected: PASS

- [ ] **Step 5: Commit the dispatch helper slice**

```bash
git add lib/operation_runners.py tests/test_operation_runners.py
git commit -m "refactor: add operation dispatch helpers"
```

## Task 2: Extract Switchover And Restore-Only Runner Implementations

**Files:**
- Modify: `lib/operation_runners.py`
- Modify: `tests/test_operation_runners.py`
- Test: `tests/test_main_phase_flow.py`

- [ ] **Step 1: Extend `tests/test_operation_runners.py` with failing runner-orchestration tests**

```python
from unittest.mock import Mock, patch

from lib.constants import PHASE_FLOW_NAME_RESTORE_ONLY, PHASE_FLOW_NAME_SWITCHOVER
from lib.operation_runners import (
    RestoreOnlyRunnerHooks,
    SwitchoverRunnerHooks,
    run_restore_only_impl,
    run_switchover_impl,
)
from tests.main_test_helpers import make_restore_only_args, make_switchover_args


def _bool_handler(name):
    handler = Mock(name=name)
    handler.return_value = True
    return handler


def test_run_switchover_impl_uses_validate_only_preflight_shortcut():
    args = make_switchover_args(validate_only=True)
    state = Mock()
    primary = Mock()
    secondary = Mock()
    logger = Mock()
    hooks = SwitchoverRunnerHooks(
        preflight_handler=_bool_handler("preflight"),
        primary_prep_handler=_bool_handler("primary_prep"),
        activation_handler=_bool_handler("activation"),
        post_activation_handler=_bool_handler("post_activation"),
        finalization_handler=_bool_handler("finalization"),
        fail_phase=Mock(return_value=False),
        fail_unexpected_phase_state=Mock(return_value=False),
        on_phase_failure=Mock(),
    )

    with patch("lib.operation_runners.handle_completed_state", return_value=False), patch(
        "lib.operation_runners.handle_failed_state"
    ), patch("lib.operation_runners.run_validate_only_preflight", return_value=True) as validate_only:
        result = run_switchover_impl(args, state, primary, secondary, logger, hooks=hooks)

    assert result is True
    validate_only.assert_called_once_with(args, state, primary, secondary, logger, hooks.preflight_handler)


def test_run_switchover_impl_passes_expected_phase_flow_to_run_phase_flow():
    args = make_switchover_args()
    state = Mock()
    primary = Mock()
    secondary = Mock()
    logger = Mock()
    hooks = SwitchoverRunnerHooks(
        preflight_handler=_bool_handler("preflight"),
        primary_prep_handler=_bool_handler("primary_prep"),
        activation_handler=_bool_handler("activation"),
        post_activation_handler=_bool_handler("post_activation"),
        finalization_handler=_bool_handler("finalization"),
        fail_phase=Mock(return_value=False),
        fail_unexpected_phase_state=Mock(return_value=False),
        on_phase_failure=Mock(),
    )

    with patch("lib.operation_runners.handle_completed_state", return_value=False), patch(
        "lib.operation_runners.handle_failed_state"
    ), patch("lib.operation_runners.run_phase_flow", return_value=True) as run_phase_flow:
        result = run_switchover_impl(args, state, primary, secondary, logger, hooks=hooks)

    assert result is True
    phase_flow = run_phase_flow.call_args.args[5]
    assert [entry[0] for entry in phase_flow] == [
        hooks.preflight_handler,
        hooks.primary_prep_handler,
        hooks.activation_handler,
        hooks.post_activation_handler,
        hooks.finalization_handler,
    ]
    assert run_phase_flow.call_args.args[6] == PHASE_FLOW_NAME_SWITCHOVER
    assert run_phase_flow.call_args.args[7] is hooks.fail_phase
    assert run_phase_flow.call_args.args[8] is hooks.fail_unexpected_phase_state
    assert run_phase_flow.call_args.args[9] is hooks.on_phase_failure


def test_run_restore_only_impl_uses_validate_only_preflight_shortcut():
    args = make_restore_only_args(validate_only=True)
    state = Mock()
    secondary = Mock()
    logger = Mock()
    hooks = RestoreOnlyRunnerHooks(
        preflight_handler=_bool_handler("preflight"),
        restore_only_pause_handler=_bool_handler("restore_only_pause"),
        activation_handler=_bool_handler("activation"),
        post_activation_handler=_bool_handler("post_activation"),
        finalization_handler=_bool_handler("finalization"),
        fail_phase=Mock(return_value=False),
        fail_unexpected_phase_state=Mock(return_value=False),
        on_phase_failure=Mock(),
    )

    with patch("lib.operation_runners.handle_completed_state", return_value=False), patch(
        "lib.operation_runners.handle_failed_state"
    ), patch("lib.operation_runners.run_validate_only_preflight", return_value=True) as validate_only:
        result = run_restore_only_impl(args, state, secondary, logger, hooks=hooks)

    assert result is True
    validate_only.assert_called_once_with(args, state, None, secondary, logger, hooks.preflight_handler)


def test_run_restore_only_impl_injects_pause_handler_and_restore_defaults_into_phase_flow():
    args = make_restore_only_args(method=None, old_hub_action=None)
    state = Mock()
    secondary = Mock()
    logger = Mock()
    hooks = RestoreOnlyRunnerHooks(
        preflight_handler=_bool_handler("preflight"),
        restore_only_pause_handler=_bool_handler("restore_only_pause"),
        activation_handler=_bool_handler("activation"),
        post_activation_handler=_bool_handler("post_activation"),
        finalization_handler=_bool_handler("finalization"),
        fail_phase=Mock(return_value=False),
        fail_unexpected_phase_state=Mock(return_value=False),
        on_phase_failure=Mock(),
    )

    with patch("lib.operation_runners.handle_completed_state", return_value=False), patch(
        "lib.operation_runners.handle_failed_state"
    ), patch("lib.operation_runners.run_phase_flow", return_value=True) as run_phase_flow:
        result = run_restore_only_impl(args, state, secondary, logger, hooks=hooks)

    assert result is True
    assert args.method == "full"
    assert args.old_hub_action == "none"
    phase_flow = run_phase_flow.call_args.args[5]
    assert [entry[0] for entry in phase_flow] == [
        hooks.preflight_handler,
        hooks.restore_only_pause_handler,
        hooks.activation_handler,
        hooks.post_activation_handler,
        hooks.finalization_handler,
    ]
    assert run_phase_flow.call_args.args[6] == PHASE_FLOW_NAME_RESTORE_ONLY
```

- [ ] **Step 2: Run the direct runner tests and verify the new dataclasses/functions are still missing**

Run: `python -m pytest tests/test_operation_runners.py -q`

Expected: FAIL with `ImportError: cannot import name 'RestoreOnlyRunnerHooks' from 'lib.operation_runners'`

- [ ] **Step 3: Extend `lib/operation_runners.py` with hook dataclasses and extracted runner implementations**

```python
from datetime import datetime
from typing import Any, Callable, Optional, Tuple

from lib import Phase
from lib.constants import (
    DRY_RUN_RESTORE_ONLY_COMPLETION_MESSAGE,
    DRY_RUN_RESTORE_ONLY_NEXT_STEPS_MESSAGE,
    DRY_RUN_SWITCHOVER_COMPLETION_MESSAGE,
    DRY_RUN_SWITCHOVER_NEXT_STEPS_MESSAGE,
    OPERATION_LABEL_RESTORE,
    OPERATION_LABEL_SWITCHOVER,
    OPERATION_NOUN_RESTORE,
    OPERATION_NOUN_SWITCHOVER,
    PHASE_FLOW_NAME_RESTORE_ONLY,
    PHASE_FLOW_NAME_SWITCHOVER,
    RESTORE_ONLY_COMPLETED_SUCCESS_MESSAGE,
    SWITCHOVER_COMPLETED_SUCCESS_MESSAGE,
)
from lib.workflow import (
    CompletedStateConfig,
    FailedStateConfig,
    PhaseFlowEntry,
    handle_completed_state,
    handle_failed_state,
    run_phase_flow,
    run_validate_only_preflight,
)

PhaseHandler = Callable[[Any, Any, Any, Any, Any], bool]
FailPhaseHandler = Callable[[Any, str, Any], bool]
UnexpectedPhaseHandler = Callable[[Any, Phase, Any], bool]
FailureHook = Callable[[Any, Any, Any, Any, Any], None]


@dataclass(frozen=True)
class SwitchoverRunnerHooks:
    preflight_handler: PhaseHandler
    primary_prep_handler: PhaseHandler
    activation_handler: PhaseHandler
    post_activation_handler: PhaseHandler
    finalization_handler: PhaseHandler
    fail_phase: FailPhaseHandler
    fail_unexpected_phase_state: UnexpectedPhaseHandler
    on_phase_failure: FailureHook


@dataclass(frozen=True)
class RestoreOnlyRunnerHooks:
    preflight_handler: PhaseHandler
    restore_only_pause_handler: PhaseHandler
    activation_handler: PhaseHandler
    post_activation_handler: PhaseHandler
    finalization_handler: PhaseHandler
    fail_phase: FailPhaseHandler
    fail_unexpected_phase_state: UnexpectedPhaseHandler
    on_phase_failure: FailureHook


def run_switchover_impl(
    args: Any,
    state: Any,
    primary: Any,
    secondary: Any,
    logger: Any,
    *,
    hooks: SwitchoverRunnerHooks,
) -> bool:
    if secondary is None:
        raise ValueError("Secondary client is required for switchover")

    if handle_completed_state(
        args,
        state,
        logger,
        CompletedStateConfig(operation_label=OPERATION_LABEL_SWITCHOVER, operation_noun=OPERATION_NOUN_SWITCHOVER),
    ):
        return True
    handle_failed_state(
        args,
        state,
        logger,
        FailedStateConfig(
            resumable_phases=(
                Phase.PREFLIGHT,
                Phase.PRIMARY_PREP,
                Phase.SECONDARY_VERIFY,
                Phase.ACTIVATION,
                Phase.POST_ACTIVATION,
                Phase.FINALIZATION,
            ),
            operation_noun=OPERATION_NOUN_SWITCHOVER,
        ),
    )

    if args.validate_only:
        return run_validate_only_preflight(args, state, primary, secondary, logger, hooks.preflight_handler)

    phase_flow: Tuple[PhaseFlowEntry, ...] = (
        (hooks.preflight_handler, (Phase.INIT, Phase.PREFLIGHT), Phase.PREFLIGHT),
        (
            hooks.primary_prep_handler,
            (Phase.PREFLIGHT, Phase.PRIMARY_PREP),
            Phase.PRIMARY_PREP,
        ),
        (
            hooks.activation_handler,
            (
                Phase.PREFLIGHT,
                Phase.PRIMARY_PREP,
                Phase.SECONDARY_VERIFY,
                Phase.ACTIVATION,
            ),
            Phase.ACTIVATION,
        ),
        (
            hooks.post_activation_handler,
            (Phase.ACTIVATION, Phase.POST_ACTIVATION),
            Phase.POST_ACTIVATION,
        ),
        (
            hooks.finalization_handler,
            (Phase.POST_ACTIVATION, Phase.FINALIZATION),
            Phase.FINALIZATION,
        ),
    )

    if not run_phase_flow(
        args,
        state,
        primary,
        secondary,
        logger,
        phase_flow,
        PHASE_FLOW_NAME_SWITCHOVER,
        hooks.fail_phase,
        hooks.fail_unexpected_phase_state,
        hooks.on_phase_failure,
    ):
        return False

    if getattr(args, "dry_run", False):
        logger.info(DRY_RUN_SWITCHOVER_COMPLETION_MESSAGE)
        logger.info(DRY_RUN_SWITCHOVER_NEXT_STEPS_MESSAGE)
        return True

    state.set_phase(Phase.COMPLETED)
    logger.info("\n" + "=" * 60)
    logger.info(SWITCHOVER_COMPLETED_SUCCESS_MESSAGE)
    logger.info("=" * 60)
    logger.info("\nSwitchover completed at: %s", datetime.now().astimezone().isoformat())
    logger.info("State file: %s", args.state_file)
    logger.info("\nNext steps:")
    logger.info("  1. Inform stakeholders that switchover is complete")
    logger.info("  2. Provide new hub connection details")
    logger.info("  3. Verify applications are functioning correctly")
    logger.info("  4. Optionally decommission old hub with: --decommission")
    return True


def run_restore_only_impl(
    args: Any,
    state: Any,
    secondary: Any,
    logger: Any,
    *,
    hooks: RestoreOnlyRunnerHooks,
) -> bool:
    if secondary is None:
        raise ValueError("Secondary client is required for restore-only")

    if not getattr(args, "method", None):
        args.method = "full"
    if not getattr(args, "old_hub_action", None):
        args.old_hub_action = "none"

    if handle_completed_state(
        args,
        state,
        logger,
        CompletedStateConfig(operation_label=OPERATION_LABEL_RESTORE, operation_noun=OPERATION_NOUN_RESTORE),
    ):
        return True
    handle_failed_state(
        args,
        state,
        logger,
        FailedStateConfig(
            resumable_phases=(
                Phase.PREFLIGHT,
                Phase.ACTIVATION,
                Phase.POST_ACTIVATION,
                Phase.FINALIZATION,
            ),
            operation_noun=OPERATION_NOUN_RESTORE,
        ),
    )

    if args.validate_only:
        return run_validate_only_preflight(args, state, None, secondary, logger, hooks.preflight_handler)

    phase_flow: Tuple[PhaseFlowEntry, ...] = (
        (hooks.preflight_handler, (Phase.INIT, Phase.PREFLIGHT), Phase.PREFLIGHT),
        (hooks.restore_only_pause_handler, (Phase.PREFLIGHT,), Phase.PREFLIGHT),
        (hooks.activation_handler, (Phase.PREFLIGHT, Phase.ACTIVATION), Phase.ACTIVATION),
        (
            hooks.post_activation_handler,
            (Phase.ACTIVATION, Phase.POST_ACTIVATION),
            Phase.POST_ACTIVATION,
        ),
        (
            hooks.finalization_handler,
            (Phase.POST_ACTIVATION, Phase.FINALIZATION),
            Phase.FINALIZATION,
        ),
    )

    if not run_phase_flow(
        args,
        state,
        None,
        secondary,
        logger,
        phase_flow,
        PHASE_FLOW_NAME_RESTORE_ONLY,
        hooks.fail_phase,
        hooks.fail_unexpected_phase_state,
        hooks.on_phase_failure,
    ):
        return False

    if getattr(args, "dry_run", False):
        logger.info(DRY_RUN_RESTORE_ONLY_COMPLETION_MESSAGE)
        logger.info(DRY_RUN_RESTORE_ONLY_NEXT_STEPS_MESSAGE)
        return True

    state.set_phase(Phase.COMPLETED)
    logger.info("\n" + "=" * 60)
    logger.info(RESTORE_ONLY_COMPLETED_SUCCESS_MESSAGE)
    logger.info("=" * 60)
    logger.info("\nRestore completed at: %s", datetime.now().astimezone().isoformat())
    logger.info("State file: %s", args.state_file)
    logger.info("\nNext steps:")
    logger.info("  1. Verify managed clusters are connected and healthy")
    logger.info("  2. Inform stakeholders that restore is complete")
    logger.info("  3. Provide new hub connection details")
    return True
```

- [ ] **Step 4: Run the direct runner tests plus the existing phase-flow integration surface**

Run: `python -m pytest tests/test_operation_runners.py tests/test_main_phase_flow.py -q`

Expected: PASS

- [ ] **Step 5: Commit the extracted runner-implementation slice**

```bash
git add lib/operation_runners.py tests/test_operation_runners.py
git commit -m "refactor: extract operation runner implementations"
```

## Task 3: Wire `acm_switchover.py` Compatibility Wrappers

**Files:**
- Modify: `acm_switchover.py`
- Modify: `tests/test_main.py`
- Test: `tests/test_main_phase_flow.py`

- [ ] **Step 1: Add failing compatibility-wrapper tests to `tests/test_main.py`**

```python
from acm_switchover import (
    _attempt_argocd_resume_on_failure,
    _execute_operation,
    _fail_phase,
    _fail_unexpected_phase_state,
    _run_phase_activation,
    _run_phase_finalization,
    _run_phase_post_activation,
    _run_phase_preflight,
    _run_phase_primary_prep,
    _run_restore_only_argocd_pause,
    _run_restore_only_impl,
    _run_switchover_impl,
    run_decommission,
    run_restore_only,
    run_switchover,
)
from tests.main_test_helpers import make_restore_only_args, make_switchover_args


@pytest.mark.unit
class TestOperationRunnerDelegation:
    def test_run_switchover_impl_delegates_with_current_phase_hooks(self):
        args = make_switchover_args()
        state = Mock()
        primary = Mock()
        secondary = Mock()
        logger = Mock()

        with patch("acm_switchover.operation_runners.run_switchover_impl", return_value=True) as impl:
            result = _run_switchover_impl(args, state, primary, secondary, logger)

        assert result is True
        hooks = impl.call_args.kwargs["hooks"]
        assert hooks.preflight_handler is _run_phase_preflight
        assert hooks.primary_prep_handler is _run_phase_primary_prep
        assert hooks.activation_handler is _run_phase_activation
        assert hooks.post_activation_handler is _run_phase_post_activation
        assert hooks.finalization_handler is _run_phase_finalization
        assert hooks.fail_phase is _fail_phase
        assert hooks.fail_unexpected_phase_state is _fail_unexpected_phase_state
        assert hooks.on_phase_failure is _attempt_argocd_resume_on_failure

    def test_run_restore_only_impl_delegates_with_restore_only_pause_hook(self):
        args = make_restore_only_args()
        state = Mock()
        secondary = Mock()
        logger = Mock()

        with patch("acm_switchover.operation_runners.run_restore_only_impl", return_value=True) as impl:
            result = _run_restore_only_impl(args, state, secondary, logger)

        assert result is True
        hooks = impl.call_args.kwargs["hooks"]
        assert hooks.preflight_handler is _run_phase_preflight
        assert hooks.restore_only_pause_handler is _run_restore_only_argocd_pause
        assert hooks.activation_handler is _run_phase_activation
        assert hooks.post_activation_handler is _run_phase_post_activation
        assert hooks.finalization_handler is _run_phase_finalization
        assert hooks.fail_phase is _fail_phase
        assert hooks.fail_unexpected_phase_state is _fail_unexpected_phase_state
        assert hooks.on_phase_failure is _attempt_argocd_resume_on_failure

    def test_execute_operation_delegates_with_current_dispatch_hooks(self):
        args = make_switchover_args()
        state = Mock()
        primary = Mock()
        secondary = Mock()
        logger = Mock()

        with patch("acm_switchover.operation_runners.execute_operation", return_value=True) as dispatch:
            result = _execute_operation(args, state, primary, secondary, logger)

        assert result is True
        hooks = dispatch.call_args.kwargs["hooks"]
        assert hooks.decommission_runner is run_decommission
        assert hooks.restore_only_runner is run_restore_only
        assert hooks.switchover_runner is run_switchover
```

- [ ] **Step 2: Run the wrapper tests and confirm delegation has not been wired yet**

Run: `python -m pytest tests/test_main.py::TestOperationRunnerDelegation -q`

Expected: FAIL because `acm_switchover` does not yet import `operation_runners` or delegate through it

- [ ] **Step 3: Import `operation_runners`, add hook-builder helpers, and delegate the wrappers in `acm_switchover.py`**

```python
from lib import operation_runners


def _build_switchover_runner_hooks() -> operation_runners.SwitchoverRunnerHooks:
    return operation_runners.SwitchoverRunnerHooks(
        preflight_handler=_run_phase_preflight,
        primary_prep_handler=_run_phase_primary_prep,
        activation_handler=_run_phase_activation,
        post_activation_handler=_run_phase_post_activation,
        finalization_handler=_run_phase_finalization,
        fail_phase=_fail_phase,
        fail_unexpected_phase_state=_fail_unexpected_phase_state,
        on_phase_failure=_attempt_argocd_resume_on_failure,
    )


def _build_restore_only_runner_hooks() -> operation_runners.RestoreOnlyRunnerHooks:
    return operation_runners.RestoreOnlyRunnerHooks(
        preflight_handler=_run_phase_preflight,
        restore_only_pause_handler=_run_restore_only_argocd_pause,
        activation_handler=_run_phase_activation,
        post_activation_handler=_run_phase_post_activation,
        finalization_handler=_run_phase_finalization,
        fail_phase=_fail_phase,
        fail_unexpected_phase_state=_fail_unexpected_phase_state,
        on_phase_failure=_attempt_argocd_resume_on_failure,
    )


def _build_operation_dispatch_hooks() -> operation_runners.OperationDispatchHooks:
    return operation_runners.OperationDispatchHooks(
        decommission_runner=run_decommission,
        restore_only_runner=run_restore_only,
        switchover_runner=run_switchover,
    )


def _run_switchover_impl(
    args: argparse.Namespace,
    state: StateManager,
    primary: KubeClient,
    secondary: KubeClient,
    logger: logging.Logger,
):
    return operation_runners.run_switchover_impl(
        args,
        state,
        primary,
        secondary,
        logger,
        hooks=_build_switchover_runner_hooks(),
    )


def _run_restore_only_impl(
    args: argparse.Namespace,
    state: StateManager,
    secondary: KubeClient,
    logger: logging.Logger,
) -> bool:
    return operation_runners.run_restore_only_impl(
        args,
        state,
        secondary,
        logger,
        hooks=_build_restore_only_runner_hooks(),
    )


def _execute_operation(
    args: argparse.Namespace,
    state: StateManager,
    primary: Optional[KubeClient],
    secondary: Optional[KubeClient],
    logger: logging.Logger,
) -> bool:
    return operation_runners.execute_operation(
        args,
        state,
        primary,
        secondary,
        logger,
        hooks=_build_operation_dispatch_hooks(),
    )
```

- [ ] **Step 4: Run the direct plus compatibility regression surface**

Run: `python -m pytest tests/test_operation_runners.py tests/test_main.py tests/test_main_phase_flow.py -q`

Expected: PASS

- [ ] **Step 5: Commit the wrapper-compatibility slice**

```bash
git add acm_switchover.py tests/test_main.py
git commit -m "refactor: delegate operation runners from main"
```

## Task 4: Update The Thermos Tracker And Run Final Verification

**Files:**
- Modify: `thermos-resolution-plan.md`
- Test: `tests/test_documentation_guardrails.py`

- [ ] **Step 1: Update the PR29 tracker row with the spec, plan, and implementation result**

```markdown
| 29 | ready_for_review | `refactor/thermos-29-operation-runners` | `.worktrees/thermos-29-operation-runners` | F44 operation/phase-flow runner extraction | — | Added design spec `docs/superpowers/specs/2026-06-07-pr29-operation-runner-design.md` and implementation plan `docs/superpowers/plans/2026-06-07-pr29-operation-runner-extraction.md`. `lib/operation_runners.py` now owns dispatch plus switchover/restore-only runner orchestration, while `acm_switchover.py` keeps thin compatibility wrappers and leaves `_run_restore_only_argocd_pause()` in place for PR30. Verification: `python -m pytest tests/test_operation_runners.py tests/test_main.py tests/test_main_phase_flow.py tests/test_documentation_guardrails.py -q` passed; `graphify update .` passed; `git diff --check` passed; final `./run_tests.sh` passed. |
```

- [ ] **Step 2: Run the focused regression suite plus documentation guardrails**

Run: `python -m pytest tests/test_operation_runners.py tests/test_main.py tests/test_main_phase_flow.py tests/test_documentation_guardrails.py -q`

Expected: PASS

- [ ] **Step 3: Refresh the graph after the Python code changes**

Run: `graphify update .`

Expected: exit code 0

- [ ] **Step 4: Run the whitespace sanity check**

Run: `git diff --check`

Expected: no output, exit code 0

- [ ] **Step 5: Run the full strict verification lane**

Run: `./run_tests.sh`

Expected: PASS

- [ ] **Step 6: Commit the tracker update and final verification state**

```bash
git add thermos-resolution-plan.md
git commit -m "docs: record PR29 runner extraction verification"
```
