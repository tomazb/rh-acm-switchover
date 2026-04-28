import subprocess
import sys
from pathlib import Path

from tests.release.adapters.python_cli import PythonCliAdapter
from tests.release.adapters.common import ReportArtifact


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
    def fake_run(command, cwd, text, capture_output, check, timeout):
        assert cwd == Path("/repo")
        return subprocess.CompletedProcess(command, 0, stdout="done\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = PythonCliAdapter(Path("/repo"), "primary", "secondary", "/kube/primary", "/kube/secondary", tmp_path)

    result = adapter.execute("preflight")

    assert result.status == "passed"
    assert result.returncode == 0
    assert Path(result.stdout_path).read_text(encoding="utf-8") == "done\n"
    assert Path(result.stderr_path).read_text(encoding="utf-8") == ""
    assert result.assertions[0].name == "exit-code"


def test_python_adapter_execute_reports_failure_on_nonzero_returncode(monkeypatch, tmp_path: Path) -> None:
    def fake_run(command, cwd, text, capture_output, check, timeout):
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="fatal: cluster unreachable\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = PythonCliAdapter(Path("/repo"), "primary", "secondary", "/kube/primary", "/kube/secondary", tmp_path)

    result = adapter.execute("preflight")

    assert result.status == "failed"
    assert result.returncode == 1
    assert result.assertions[0].status == "failed"
    assert result.assertions[0].actual == "1"
    assert Path(result.stderr_path).read_text(encoding="utf-8") == "fatal: cluster unreachable\n"
    assert result.assertions[0].evidence_path == result.stderr_path


def test_discover_reports_returns_empty_when_file_absent(tmp_path: Path) -> None:
    adapter = PythonCliAdapter(Path("/repo"), "p", "s", "/k/p", "/k/s", tmp_path)
    assert adapter.discover_reports("preflight") == []


def test_python_adapter_discovers_required_reports(tmp_path: Path) -> None:
    adapter = PythonCliAdapter(Path("/repo"), "primary", "secondary", "/kube/primary", "/kube/secondary", tmp_path)
    scenario_dir = adapter.scenario_dir("preflight")
    scenario_dir.mkdir(parents=True)
    (scenario_dir / "preflight-report.json").write_text('{"schema_version": 1, "status": "passed"}', encoding="utf-8")

    reports = adapter.discover_reports("preflight")

    assert isinstance(reports[0], ReportArtifact)
    assert reports[0].type == "preflight"
    assert reports[0].required is True
    assert reports[0].schema_version == 1
