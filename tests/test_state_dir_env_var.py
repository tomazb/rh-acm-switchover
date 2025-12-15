import argparse
import logging
import os

import pytest

from acm_switchover import _resolve_state_file, validate_args


def test_state_file_cli_overrides_env_state_dir(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ACM_SWITCHOVER_STATE_DIR", "/tmp/acm-switchover-state")

    resolved = _resolve_state_file(
        requested_path="custom/state.json",
        primary_ctx="primary-a",
        secondary_ctx="secondary-b",
    )

    assert resolved == "custom/state.json"


def test_env_state_dir_used_for_default_state_file(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ACM_SWITCHOVER_STATE_DIR", "/tmp/acm-switchover-state")

    resolved = _resolve_state_file(
        requested_path=None,
        primary_ctx="primary-a",
        secondary_ctx="secondary-b",
    )

    assert resolved == os.path.join("/tmp/acm-switchover-state", "switchover-primary-a__secondary-b.json")


def test_empty_env_state_dir_ignored(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ACM_SWITCHOVER_STATE_DIR", "   ")

    resolved = _resolve_state_file(
        requested_path=None,
        primary_ctx="primary-a",
        secondary_ctx="secondary-b",
    )

    assert resolved == os.path.join(".state", "switchover-primary-a__secondary-b.json")


def test_invalid_env_state_dir_rejected_when_state_file_not_set(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("ACM_SWITCHOVER_STATE_DIR", "../bad")

    args = argparse.Namespace(
        primary_context="primary-a",
        secondary_context="secondary-b",
        method="passive",
        old_hub_action="secondary",
        log_format="text",
        state_file=None,
        decommission=False,
        non_interactive=False,
    )

    logger = logging.getLogger("test")

    with pytest.raises(SystemExit):
        validate_args(args, logger)
