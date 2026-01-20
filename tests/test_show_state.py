"""Tests for the show_state.py helper CLI."""

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from show_state import (
    _default_state_dir,
    find_state_files,
    format_timestamp,
    load_state,
    main,
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

    def test_default_state_dir_falls_back_on_invalid_env(self, monkeypatch: pytest.MonkeyPatch):
        # Use a clearly unsafe path and force validator to raise
        monkeypatch.setenv("ACM_SWITCHOVER_STATE_DIR", "../bad")

        # Monkeypatch validator to raise so we exercise the fallback branch
        import show_state as ss

        original_validator = ss.InputValidator.validate_safe_filesystem_path

        def _raise_validation(path: str, field_name: str) -> None:  # pragma: no cover - trivial wrapper
            raise ss.ValidationError(f"bad path for {field_name}: {path}")

        monkeypatch.setattr(ss.InputValidator, "validate_safe_filesystem_path", staticmethod(_raise_validation))
        try:
            assert _default_state_dir() == ".state"
        finally:
            # Restore original to avoid side effects on other tests
            monkeypatch.setattr(
                ss.InputValidator,
                "validate_safe_filesystem_path",
                staticmethod(original_validator),
            )

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
    import show_state

    # Patch argparse to use our args list
    import sys

    original_argv = sys.argv
    sys.argv = ["show_state.py"] + list(args)
    try:
        return show_state.main()
    finally:
        sys.argv = original_argv

