"""Unit tests for ACM switchover bash scripts.

Tests argument parsing, help output, and basic error handling for:
- scripts/preflight-check.sh
- scripts/postflight-check.sh
- scripts/lib-common.sh

These tests run quickly without requiring cluster access.
"""

import os
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"


def strip_ansi(text: str) -> str:
    """Remove ANSI color codes from text."""
    ansi_pattern = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
    return ansi_pattern.sub("", text)


def run_script(script_name: str, *args: str, env=None):
    """Run a bash script with optional environment override."""
    script_path = SCRIPTS_DIR / script_name
    assert script_path.exists(), f"Script not found: {script_path}"
    cmd = ["bash", str(script_path), *args]

    use_env = os.environ.copy()
    if env:
        use_env.update(env)

    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=use_env,
        timeout=30,  # Increased timeout for CI runners
    )
    output = strip_ansi(proc.stdout)
    return proc.returncode, output


# ============================================================================
# Argument Validation Tests
# ============================================================================


@pytest.mark.parametrize(
    "script,expected_args",
    [
        (
            "preflight-check.sh",
            ["--primary-context", "--secondary-context", "--method"],
        ),
        ("postflight-check.sh", ["--new-hub-context", "--old-hub-context"]),
    ],
)
def test_help_output(script, expected_args):
    """Test --help output for both scripts."""
    code, out = run_script(script, "--help")
    assert code == 0, f"{script} help should exit 0"
    assert "Usage:" in out
    for arg in expected_args:
        assert arg in out, f"Missing {arg} in help text"


@pytest.mark.parametrize(
    "args",
    [
        ([]),  # No args
        (["--primary-context", "alpha"]),  # Only primary
        (["--secondary-context", "beta"]),  # Only secondary
    ],
)
def test_preflight_missing_args(args):
    """Test preflight error handling for missing required args."""
    code, out = run_script("preflight-check.sh", *args)
    assert code == 2, "Should exit with arg error (2)"
    assert "required" in out.lower(), "Should mention required args"


def test_postflight_missing_new_hub_context():
    """Test postflight error for missing required new-hub-context."""
    code, out = run_script("postflight-check.sh")
    assert code == 2
    assert "--new-hub-context" in out or "required" in out.lower()


@pytest.mark.parametrize(
    "script,bad_option",
    [
        ("preflight-check.sh", "--invalid-option"),
        ("postflight-check.sh", "--bad-flag"),
    ],
)
def test_unknown_option(script, bad_option):
    """Test that unknown options are rejected."""
    code, out = run_script(script, bad_option)
    assert code == 2, "Unknown options should exit with code 2"
    assert "Unknown option" in out or "help" in out.lower()


def test_preflight_invalid_method():
    """Test preflight with valid args runs checks (will fail without real cluster but args parse)."""
    code, out = run_script(
        "preflight-check.sh",
        "--primary-context",
        "fake-primary",
        "--secondary-context",
        "fake-secondary",
        "--method",
        "passive",
    )
    # Without oc available, it will fail to find the command (127)
    # With oc available, it will fail validation (1)
    # Either way, args were parsed (not exit 2)
    assert code != 2, f"Got arg error (exit 2) but expected validation attempt"
    # We just want to verify that args were recognized
    if code in (0, 1):
        assert "Primary Hub:" in out or "Secondary Hub:" in out


def test_postflight_with_optional_old_hub():
    """Test postflight with optional old-hub-context (will fail on cluster access but args parse)."""
    code, out = run_script(
        "postflight-check.sh",
        "--new-hub-context",
        "fake-new",
        "--old-hub-context",
        "fake-old",
    )
    # Args should parse, failure will be validation (exit 1) or command not found (exit 127)
    assert code in (0, 1, 127), f"Expected 0/1/127, got {code}"
    # If script started, should show header
    if code in (0, 1):
        assert "New Hub:" in out


def test_preflight_output_format():
    """Test that preflight produces well-formatted output with sections."""
    code, out = run_script(
        "preflight-check.sh",
        "--primary-context",
        "test-primary",
        "--secondary-context",
        "test-secondary",
        "--method",
        "passive",
    )
    # Should show formatted header even if validation fails
    assert "ACM Switchover Pre-flight Validation" in out
    assert "Primary Hub:" in out
    assert "Secondary Hub:" in out
    # Should have section headers (unicode box drawing or plain text)
    assert "1." in out or "Checking" in out


def test_postflight_output_format():
    """Test that postflight produces well-formatted output with sections."""
    code, out = run_script(
        "postflight-check.sh",
        "--new-hub-context",
        "test-new",
    )
    # Should show formatted header even if validation fails
    assert "ACM Switchover Post-flight Verification" in out or "New Hub:" in out
    # Should attempt to show sections
    assert "1." in out or "Checking" in out or "Restore" in out


# ============================================================================
# Integration tests with mocks - BLOCKED by set -e + ((COUNTER++)) bug
# ============================================================================
# NOTE: Full integration tests require fixing the scripts' arithmetic expressions
# to be compatible with `set -euo pipefail`. See tests/README-scripts-tests.md
# for details and recommended fixes.
#
# When scripts are fixed, uncomment and complete the tests below:
#
# def test_preflight_success_with_mocks(mock_binaries):
#     """Test preflight success path with mocked oc/jq."""
#     pass
#
# def test_preflight_version_mismatch(mock_binaries):
#     """Test failure when ACM versions don't match."""
#     pass
#
# def test_postflight_success_with_mocks(mock_binaries):
#     """Test postflight success with mocked oc/jq."""
#     pass


# ============================================================================
# lib-common.sh Tests
# ============================================================================


def run_bash_command(command: str, env=None):
    """Run a bash command and return (returncode, output)."""
    use_env = os.environ.copy()
    if env:
        use_env.update(env)

    proc = subprocess.run(
        ["bash", "-c", command],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=use_env,
        timeout=10,
    )
    return proc.returncode, strip_ansi(proc.stdout)


def test_lib_common_exists():
    """Test that lib-common.sh exists in scripts directory."""
    lib_common = SCRIPTS_DIR / "lib-common.sh"
    assert lib_common.exists(), "lib-common.sh should exist in scripts/"


def test_lib_common_sources_successfully():
    """Test that lib-common.sh can be sourced without errors."""
    constants = SCRIPTS_DIR / "constants.sh"
    lib_common = SCRIPTS_DIR / "lib-common.sh"
    code, out = run_bash_command(f"source '{constants}' && source '{lib_common}' && echo 'success'")
    assert code == 0, f"lib-common.sh should source successfully. Output: {out}"
    assert "success" in out


def test_lib_common_functions_defined():
    """Test that all expected functions are defined after sourcing lib-common.sh."""
    constants = SCRIPTS_DIR / "constants.sh"
    lib_common = SCRIPTS_DIR / "lib-common.sh"

    functions = [
        "check_pass",
        "check_fail",
        "check_warn",
        "section_header",
        "detect_cluster_cli",
        "print_summary",
    ]

    for func in functions:
        code, out = run_bash_command(
            f"source '{constants}' && source '{lib_common}' && type {func} >/dev/null 2>&1 && echo 'exists'"
        )
        assert "exists" in out, f"Function {func} should be defined in lib-common.sh"


def test_lib_common_counters_initialize():
    """Test that counters are initialized to zero."""
    constants = SCRIPTS_DIR / "constants.sh"
    lib_common = SCRIPTS_DIR / "lib-common.sh"
    code, out = run_bash_command(
        f"source '{constants}' && source '{lib_common}' && "
        "echo $TOTAL_CHECKS,$PASSED_CHECKS,$FAILED_CHECKS,$WARNING_CHECKS"
    )
    assert code == 0
    assert out.strip() == "0,0,0,0", f"Counters should initialize to 0,0,0,0. Got: {out.strip()}"


def test_lib_common_check_pass_increments():
    """Test that check_pass increments the right counters."""
    constants = SCRIPTS_DIR / "constants.sh"
    lib_common = SCRIPTS_DIR / "lib-common.sh"
    code, out = run_bash_command(
        f"source '{constants}' && source '{lib_common}' && "
        "check_pass 'test' >/dev/null && "
        "echo $TOTAL_CHECKS,$PASSED_CHECKS,$FAILED_CHECKS,$WARNING_CHECKS"
    )
    assert code == 0
    assert out.strip() == "1,1,0,0", f"After check_pass, counters should be 1,1,0,0. Got: {out.strip()}"


def test_lib_common_check_fail_increments():
    """Test that check_fail increments the right counters."""
    constants = SCRIPTS_DIR / "constants.sh"
    lib_common = SCRIPTS_DIR / "lib-common.sh"
    code, out = run_bash_command(
        f"source '{constants}' && source '{lib_common}' && "
        "check_fail 'test' >/dev/null && "
        "echo $TOTAL_CHECKS,$PASSED_CHECKS,$FAILED_CHECKS,$WARNING_CHECKS"
    )
    assert code == 0
    assert out.strip() == "1,0,1,0", f"After check_fail, counters should be 1,0,1,0. Got: {out.strip()}"


def test_lib_common_check_warn_increments():
    """Test that check_warn increments the right counters."""
    constants = SCRIPTS_DIR / "constants.sh"
    lib_common = SCRIPTS_DIR / "lib-common.sh"
    code, out = run_bash_command(
        f"source '{constants}' && source '{lib_common}' && "
        "check_warn 'test' >/dev/null && "
        "echo $TOTAL_CHECKS,$PASSED_CHECKS,$FAILED_CHECKS,$WARNING_CHECKS"
    )
    assert code == 0
    assert out.strip() == "1,0,0,1", f"After check_warn, counters should be 1,0,0,1. Got: {out.strip()}"


def test_lib_common_print_summary_preflight():
    """Test print_summary in preflight mode."""
    constants = SCRIPTS_DIR / "constants.sh"
    lib_common = SCRIPTS_DIR / "lib-common.sh"
    code, out = run_bash_command(
        f"source '{constants}' && source '{lib_common}' && " "check_pass 'test' >/dev/null && print_summary 'preflight'"
    )
    assert code == 0
    assert "Validation Summary" in out, "Preflight summary should say 'Validation Summary'"
    assert "ready to proceed" in out.lower(), "Preflight success should say 'ready to proceed'"


def test_lib_common_print_summary_postflight():
    """Test print_summary in postflight mode."""
    constants = SCRIPTS_DIR / "constants.sh"
    lib_common = SCRIPTS_DIR / "lib-common.sh"
    code, out = run_bash_command(
        f"source '{constants}' && source '{lib_common}' && "
        "check_pass 'test' >/dev/null && print_summary 'postflight'"
    )
    assert code == 0
    assert "Verification Summary" in out, "Postflight summary should say 'Verification Summary'"
    assert "completed successfully" in out.lower(), "Postflight success should mention completion"


def test_constants_exit_codes_defined():
    """Test that exit code constants are defined in constants.sh."""
    constants = SCRIPTS_DIR / "constants.sh"
    code, out = run_bash_command(f"source '{constants}' && echo $EXIT_SUCCESS,$EXIT_FAILURE,$EXIT_INVALID_ARGS")
    assert code == 0
    assert out.strip() == "0,1,2", f"Exit codes should be 0,1,2. Got: {out.strip()}"
