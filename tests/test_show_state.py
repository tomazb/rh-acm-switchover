"""Tests for the show_state.py helper CLI."""

import json
import os
from pathlib import Path

import pytest

from lib.runtime_bootstrap import get_default_state_dir
from show_state import (
    _default_state_dir,
    find_state_files,
    format_timestamp,
    load_state,
    print_state,
)


@pytest.mark.unit
class TestShowStateHelpers:
    def test_format_timestamp_handles_invalid_values(self):
        """Invalid or empty timestamps should be returned as-is or 'unknown'."""
        assert format_timestamp("") == "unknown"
        assert format_timestamp("not-a-timestamp") == "not-a-timestamp"

    def test_default_state_dir_uses_env_when_valid(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("ACM_SWITCHOVER_STATE_DIR", "/tmp/acm-state")
        assert _default_state_dir() == "/tmp/acm-state"

    def test_default_state_dir_matches_cli(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        # The viewer must look where the CLI writes, so state-dir resolution is
        # shared: no independent validation/fallback in show_state.
        monkeypatch.setenv("ACM_SWITCHOVER_STATE_DIR", str(tmp_path / "custom-state"))
        assert _default_state_dir() == get_default_state_dir()

    def test_default_state_dir_matches_cli_for_relative_env(self, monkeypatch: pytest.MonkeyPatch):
        # Previously rejected by InputValidator and silently replaced with
        # ".state"; now honoured exactly as the CLI honours it.
        monkeypatch.setenv("ACM_SWITCHOVER_STATE_DIR", "../bad")
        assert _default_state_dir() == get_default_state_dir() == "../bad"

    def test_default_state_dir_defaults_without_env(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("ACM_SWITCHOVER_STATE_DIR", raising=False)
        assert _default_state_dir() == get_default_state_dir() == ".state"

    def test_find_state_files_discovers_json_files(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        state_dir = tmp_path / ".state"
        state_dir.mkdir()
        f1 = state_dir / "switchover-a__b.json"
        f2 = state_dir / "switchover-x__y.json"
        f1.write_text("{}", encoding="utf-8")
        f2.write_text("{}", encoding="utf-8")

        monkeypatch.setenv("ACM_SWITCHOVER_STATE_DIR", str(state_dir))
        files = find_state_files()
        assert len(files) == 2
        assert str(f1) in files and str(f2) in files

    def test_load_state_success_and_errors(self, tmp_path: Path, capsys):
        good = tmp_path / "good.json"
        bad = tmp_path / "bad.json"
        missing = tmp_path / "missing.json"

        good.write_text(json.dumps({"foo": "bar"}), encoding="utf-8")
        bad.write_text("{ invalid json", encoding="utf-8")

        assert load_state(str(good)) == {"foo": "bar"}
        assert load_state(str(missing)) is None
        assert load_state(str(bad)) is None

        captured = capsys.readouterr().out
        assert "Error: State file not found" in captured
        assert "Error: Invalid JSON in state file" in captured


@pytest.mark.unit
class TestPrintState:
    """Golden output for the RunSummary-rendered lifecycle sections."""

    def test_print_state_renders_phase_steps_and_errors(self, capsys):
        state = {
            "current_phase": "activation",
            "completed_steps": [
                {"name": "pause_backup_schedule", "phase": "primary_preparation", "timestamp": "2026-01-01T00:00:00Z"},
                {"name": "custom_step", "phase": "activation", "timestamp": ""},
            ],
            "errors": [{"phase": "activation", "error": "restore stalled", "timestamp": "2026-01-01T01:00:00Z"}],
        }

        print_state(state, use_color=False)
        out = capsys.readouterr().out

        # Phase renders through PHASE_INFO, not the raw key.
        assert "  Activation: Activating secondary hub as new primary" in out
        # Known step renders its description; unknown step falls back to its name.
        assert "  ✓  1. Paused BackupSchedule on primary hub" in out
        assert "       2026-01-01 00:00:00 (" in out
        assert "  ✓  2. custom_step" in out
        # An empty timestamp keeps the historical "unknown" render.
        assert "       unknown" in out
        assert "━ Errors (1) ━" in out
        assert "  ✗ [activation] restore stalled" in out


@pytest.mark.unit
class TestShowStateMain:
    def test_main_lists_files_when_list_flag_set(self, monkeypatch: pytest.MonkeyPatch, capsys, tmp_path: Path):
        # Create a fake state file and ensure list_state_files path is exercised via main()
        state_dir = tmp_path / ".state"
        state_dir.mkdir()
        state_file = state_dir / "switchover-primary__secondary.json"
        state_file.write_text(
            json.dumps(
                {
                    "current_phase": "completed",
                    "last_updated": "2025-01-01T00:00:00Z",
                    "contexts": {"primary": "p1", "secondary": "p2"},
                }
            ),
            encoding="utf-8",
        )

        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("ACM_SWITCHOVER_STATE_DIR", str(state_dir))

        # Simulate CLI: show_state.py --list
        result = main_cli(args=["--list"])
        assert result == 0
        out = capsys.readouterr().out
        assert "Available State Files" in out
        assert "switchover-primary__secondary.json" in out

    def test_main_uses_most_recent_state_file_and_prints_json(self, monkeypatch: pytest.MonkeyPatch, capsys, tmp_path):
        state_dir = tmp_path / ".state"
        state_dir.mkdir()
        old_file = state_dir / "switchover-old.json"
        new_file = state_dir / "switchover-new.json"

        old_file.write_text(json.dumps({"current_phase": "init"}), encoding="utf-8")
        new_file.write_text(json.dumps({"current_phase": "completed"}), encoding="utf-8")

        # Make new_file the most recently modified
        os.utime(str(old_file), (1, 1))
        os.utime(str(new_file), None)

        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("ACM_SWITCHOVER_STATE_DIR", str(state_dir))

        # Exercise formatted output path
        monkeypatch.setattr("show_state.print_state", lambda state, use_color: None)
        result = main_cli(args=[])
        assert result == 0

        # Now exercise --json path
        result = main_cli(args=["--json"])
        assert result == 0
        out = capsys.readouterr().out
        # Should contain JSON representation from one of the state files
        # The JSON block starts after the "Using:" line
        lines = out.splitlines()
        try:
            start = next(i for i, line in enumerate(lines) if line.strip().startswith("{"))
        except StopIteration:
            pytest.fail("No JSON object found in show_state --json output\n" + out)
        json_str = "\n".join(lines[start:])
        loaded = json.loads(json_str)
        assert loaded.get("current_phase") in {"init", "completed"}

    def test_main_returns_error_when_no_state_files(self, monkeypatch: pytest.MonkeyPatch, capsys, tmp_path: Path):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("ACM_SWITCHOVER_STATE_DIR", str(tmp_path / ".state"))

        result = main_cli(args=[])
        assert result == 1
        out = capsys.readouterr().out
        assert "No state files found" in out


def main_cli(args):
    """Helper to call main() with custom argv-like list."""
    # Patch argparse to use our args list
    import sys

    import show_state

    original_argv = sys.argv
    sys.argv = ["show_state.py"] + list(args)
    try:
        return show_state.main()
    finally:
        sys.argv = original_argv
