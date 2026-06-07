from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from lib.constants import PHASE_FLOW_NAME_RESTORE_ONLY, PHASE_FLOW_NAME_SWITCHOVER
from lib.operation_runners import (
    OperationDispatchHooks,
    RestoreOnlyRunnerHooks,
    SwitchoverRunnerHooks,
    execute_operation,
    run_restore_only_impl,
    run_switchover_impl,
)
from lib.utils import Phase
from tests.main_test_helpers import make_restore_only_args, make_switchover_args

EXPECTED_SWITCHOVER_PHASE_ROUTING = (
    ((Phase.INIT, Phase.PREFLIGHT), Phase.PREFLIGHT),
    ((Phase.PREFLIGHT, Phase.PRIMARY_PREP), Phase.PRIMARY_PREP),
    (
        (Phase.PREFLIGHT, Phase.PRIMARY_PREP, Phase.SECONDARY_VERIFY, Phase.ACTIVATION),
        Phase.ACTIVATION,
    ),
    ((Phase.ACTIVATION, Phase.POST_ACTIVATION), Phase.POST_ACTIVATION),
    ((Phase.POST_ACTIVATION, Phase.FINALIZATION), Phase.FINALIZATION),
)

EXPECTED_RESTORE_ONLY_PHASE_ROUTING = (
    ((Phase.INIT, Phase.PREFLIGHT), Phase.PREFLIGHT),
    ((Phase.PREFLIGHT,), Phase.PREFLIGHT),
    ((Phase.PREFLIGHT, Phase.ACTIVATION), Phase.ACTIVATION),
    ((Phase.ACTIVATION, Phase.POST_ACTIVATION), Phase.POST_ACTIVATION),
    ((Phase.POST_ACTIVATION, Phase.FINALIZATION), Phase.FINALIZATION),
)


def _bool_handler(name):
    handler = Mock(name=name)
    handler.return_value = True
    return handler


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
    assert [(entry[1], entry[2]) for entry in phase_flow] == list(EXPECTED_SWITCHOVER_PHASE_ROUTING)
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
    assert [(entry[1], entry[2]) for entry in phase_flow] == list(EXPECTED_RESTORE_ONLY_PHASE_ROUTING)
    assert run_phase_flow.call_args.args[6] == PHASE_FLOW_NAME_RESTORE_ONLY
