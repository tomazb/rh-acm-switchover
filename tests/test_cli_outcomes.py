from types import SimpleNamespace
from unittest.mock import Mock, patch

from lib.cli_outcomes import (
    CliOperationHooks,
    phase_report_from_state,
    report_target,
    run_operation_mode,
    run_setup_mode,
    write_python_report,
)
from lib.constants import (
    REPORT_FILENAME_DECOMMISSION,
    REPORT_FILENAME_PREFLIGHT,
    REPORT_FILENAME_RESTORE_ONLY,
    REPORT_FILENAME_SWITCHOVER,
    REPORT_TYPE_DECOMMISSION,
    REPORT_TYPE_PREFLIGHT,
    REPORT_TYPE_RESTORE,
    REPORT_TYPE_SWITCHOVER,
)
from lib.exceptions import SwitchoverError
from lib.utils import Phase


def test_report_target_maps_cli_modes():
    assert report_target(SimpleNamespace(validate_only=True, decommission=False, restore_only=False)) == (
        REPORT_TYPE_PREFLIGHT,
        REPORT_FILENAME_PREFLIGHT,
    )
    assert report_target(SimpleNamespace(validate_only=False, decommission=True, restore_only=False)) == (
        REPORT_TYPE_DECOMMISSION,
        REPORT_FILENAME_DECOMMISSION,
    )
    assert report_target(SimpleNamespace(validate_only=False, decommission=False, restore_only=True)) == (
        REPORT_TYPE_RESTORE,
        REPORT_FILENAME_RESTORE_ONLY,
    )
    assert report_target(SimpleNamespace(validate_only=False, decommission=False, restore_only=False)) == (
        REPORT_TYPE_SWITCHOVER,
        REPORT_FILENAME_SWITCHOVER,
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


def test_phase_report_from_state_ignores_malformed_snapshot_entries():
    state_snapshot = {
        "completed_steps": [
            "not-a-step-mapping",
            {"name": "pause_backup_schedule"},
            None,
        ],
        "current_phase": Phase.FAILED.value,
        "errors": [
            "not-an-error-mapping",
            {"phase": Phase.ACTIVATION.value, "message": "boom"},
        ],
    }

    phases = phase_report_from_state(state_snapshot)

    assert phases["primary_prep"]["steps"] == ["pause_backup_schedule"]
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

    with patch(
        "lib.cli_outcomes.build_operation_report", return_value={"schema_version": "1.0"}
    ) as build_report, patch(
        "lib.cli_outcomes.write_json_report_artifact",
        return_value="/tmp/reports/switchover-report.json",
    ) as write_artifact:
        write_python_report(args, state, "pass", logger)

    build_report.assert_called_once()
    write_artifact.assert_called_once_with({"schema_version": "1.0"}, "/tmp/reports/switchover-report.json")
    logger.info.assert_called_once_with("Wrote report artifact: %s", "/tmp/reports/switchover-report.json")


def test_write_python_report_logs_error_without_raising():
    args = SimpleNamespace(
        report_dir="/tmp/reports",
        validate_only=False,
        decommission=False,
        restore_only=False,
    )
    state = Mock()
    failure = RuntimeError("snapshot failed")
    state.capture_state_snapshot.side_effect = failure
    logger = Mock()

    write_python_report(args, state, "pass", logger)

    logger.error.assert_called_once_with("Failed to write report artifact: %s", failure)


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
    logger.info.assert_called_once_with("\n✓ Setup completed successfully!")
    logger.warning.assert_not_called()
    logger.error.assert_not_called()


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
    logger.info.assert_not_called()
    logger.error.assert_not_called()


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
    logger.info.assert_not_called()
    logger.warning.assert_not_called()


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
    logger.info.assert_not_called()
    logger.warning.assert_not_called()


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


def test_run_operation_mode_uses_generic_failure_message():
    args = SimpleNamespace(argocd_resume_only=False, verbose=False, state_file="state.json")
    state = Mock()
    logger = Mock()
    reporter = Mock()
    hooks = CliOperationHooks(
        bind_runtime_hub_identities=Mock(),
        run_argocd_resume_only=Mock(),
        execute_operation=Mock(return_value=False),
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

    assert exit_code == 1
    logger.error.assert_any_call("\n✗ Operation failed!")
    hooks.write_python_report.assert_called_once_with(args, state, "fail", logger)
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
    logger.info.assert_any_call("State saved to: %s", "state.json")
    logger.info.assert_any_call("Re-run the same command to resume from last successful step")
    hooks.write_python_report.assert_called_once_with(args, state, "fail", logger)
    reporter.print_report.assert_called_once()


def test_run_operation_mode_handles_keyboard_interrupt_without_state_file_attribute():
    args = SimpleNamespace(argocd_resume_only=False, verbose=False)
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
    logger.info.assert_any_call("State saved to: %s", None)
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


def test_run_operation_mode_skips_state_error_when_recording_disabled():
    args = SimpleNamespace(argocd_resume_only=False, verbose=False, state_file="state.json")
    state = Mock()
    logger = Mock()
    reporter = Mock()
    hooks = CliOperationHooks(
        bind_runtime_hub_identities=Mock(side_effect=RuntimeError("boom")),
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
        should_record_state_errors=False,
        hooks=hooks,
        exit_success=0,
        exit_failure=1,
        exit_interrupt=130,
    )

    assert exit_code == 1
    state.add_error.assert_not_called()
    hooks.write_python_report.assert_called_once_with(args, state, "fail", logger)
