import subprocess
import sys
from pathlib import Path

from tests.release.adapters.python_cli import PythonCliAdapter


def test_python_preflight_command_uses_profile_contexts(tmp_path: Path) -> None:
    adapter = PythonCliAdapter(
        repo_root=Path("/repo"),
        primary_context="primary",
        secondary_context="secondary",
        primary_kubeconfig="/kube/primary",
        secondary_kubeconfig="/kube/secondary",
        artifact_dir=tmp_path,
    )

    command = adapter.build_command("preflight")

    assert command[:2] == [sys.executable, "acm_switchover.py"]
    assert "--validate-only" in command
    assert "--primary-context" in command
    assert "primary" in command
    assert "--secondary-context" in command
    assert "secondary" in command


def test_python_restore_only_command_uses_unique_state_file(tmp_path: Path) -> None:
    adapter = PythonCliAdapter(Path("/repo"), "primary", "secondary", "/kube/primary", "/kube/secondary", tmp_path)

    command = adapter.build_command("python-restore-only")

    assert "--restore-only" in command
    assert "--state-file" in command
    state_file = command[command.index("--state-file") + 1]
    assert state_file.endswith("python-restore-only/python/state.json")


def test_python_passive_switchover_command_includes_required_flags(tmp_path: Path) -> None:
    adapter = PythonCliAdapter(Path("/repo"), "primary", "secondary", "/kube/primary", "/kube/secondary", tmp_path)

    command = adapter.build_command("python-passive-switchover")

    assert sys.executable in command
    assert "--method" in command
    assert "--old-hub-action" in command
    assert "--validate-only" not in command
    assert "--restore-only" not in command


def test_python_adapter_execute_captures_stdout_and_stderr(monkeypatch, tmp_path: Path) -> None:
    def fake_run(command, cwd, text, capture_output, check):
        assert cwd == Path("/repo")
        return subprocess.CompletedProcess(command, 0, stdout="done\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = PythonCliAdapter(Path("/repo"), "primary", "secondary", "/kube/primary", "/kube/secondary", tmp_path)

    result = adapter.execute("preflight")

    assert result.status == "passed"
    assert result.returncode == 0
    assert Path(result.stdout_path).read_text(encoding="utf-8") == "done\n"
    assert result.assertions[0].name == "exit-code"
