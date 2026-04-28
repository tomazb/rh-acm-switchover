import os
import subprocess
import sys
from pathlib import Path

from tests.release.adapters.python_cli import PythonCliAdapter
from tests.release.adapters.common import ReportArtifact
from tests.release.test_release_certification import execute_python_scenarios


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
    def fake_run(command, cwd, text, capture_output, check, timeout, env):
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
    def fake_run(command, cwd, text, capture_output, check, timeout, env):
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


class FakePythonAdapter:
    def execute(self, scenario_id: str) -> dict:
        return {"scenario_id": scenario_id, "stream": "python", "status": "passed"}


class FakeStreamResult:
    def __init__(self, scenario_id: str) -> None:
        self._d = {"scenario_id": scenario_id, "stream": "python", "status": "passed"}

    def to_dict(self) -> dict:
        return self._d


class FakePythonAdapterWithToDict:
    def execute(self, scenario_id: str) -> FakeStreamResult:
        return FakeStreamResult(scenario_id)


def test_execute_python_scenarios_filters_python_ids() -> None:
    results = execute_python_scenarios(
        adapter=FakePythonAdapter(),
        scenario_ids=("preflight", "python-passive-switchover", "ansible-passive-switchover"),
    )

    assert [item["scenario_id"] for item in results] == ["preflight", "python-passive-switchover"]


def test_execute_python_scenarios_calls_to_dict_when_available() -> None:
    results = execute_python_scenarios(
        adapter=FakePythonAdapterWithToDict(),
        scenario_ids=("preflight",),
    )

    assert results[0]["scenario_id"] == "preflight"


def test_python_adapter_execute_sets_kubeconfig_env(monkeypatch, tmp_path: Path) -> None:
    captured_env: dict[str, str] = {}

    def fake_run(command, cwd, text, capture_output, check, timeout, env):
        captured_env.update(env)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.delenv("KUBECONFIG", raising=False)
    adapter = PythonCliAdapter(Path("/repo"), "primary", "secondary", "/kube/primary", "/kube/secondary", tmp_path)

    adapter.execute("preflight")

    assert captured_env.get("KUBECONFIG") == os.pathsep.join(["/kube/primary", "/kube/secondary"])


def test_python_adapter_restore_only_uses_secondary_kubeconfig_only(monkeypatch, tmp_path: Path) -> None:
    captured_env: dict[str, str] = {}

    def fake_run(command, cwd, text, capture_output, check, timeout, env):
        captured_env.update(env)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.delenv("KUBECONFIG", raising=False)
    adapter = PythonCliAdapter(Path("/repo"), "primary", "secondary", "/kube/primary", "/kube/secondary", tmp_path)

    adapter.execute("python-restore-only")

    assert captured_env.get("KUBECONFIG") == "/kube/secondary"


def test_python_adapter_execute_overrides_inherited_kubeconfig(monkeypatch, tmp_path: Path) -> None:
    captured_env: dict[str, str] = {}

    def fake_run(command, cwd, text, capture_output, check, timeout, env):
        captured_env.update(env)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setenv("KUBECONFIG", "/inherited/kubeconfig")
    adapter = PythonCliAdapter(Path("/repo"), "primary", "secondary", "/kube/primary", "/kube/secondary", tmp_path)

    adapter.execute("preflight")

    assert captured_env.get("KUBECONFIG") != "/inherited/kubeconfig"
    assert "/kube/primary" in captured_env.get("KUBECONFIG", "")


def test_python_adapter_execute_handles_timeout_with_bytes_capture(monkeypatch, tmp_path: Path) -> None:
    def fake_run(command, cwd, text, capture_output, check, timeout, env):
        exc = subprocess.TimeoutExpired(command, 3600)
        exc.stdout = b"partial output bytes"
        exc.stderr = b"partial error bytes"
        raise exc

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = PythonCliAdapter(Path("/repo"), "primary", "secondary", "/kube/primary", "/kube/secondary", tmp_path)

    result = adapter.execute("preflight")

    assert result.status == "failed"
    assert result.returncode == -1
    assert result.assertions[0].actual == "timeout"
    assert Path(result.stdout_path).read_text(encoding="utf-8") == "partial output bytes"
    assert Path(result.stderr_path).read_text(encoding="utf-8") == "partial error bytes"


def test_discover_reports_handles_malformed_json(tmp_path: Path) -> None:
    adapter = PythonCliAdapter(Path("/repo"), "p", "s", "/k/p", "/k/s", tmp_path)
    scenario_dir = adapter.scenario_dir("preflight")
    scenario_dir.mkdir(parents=True)
    (scenario_dir / "preflight-report.json").write_text("{invalid json}", encoding="utf-8")

    reports = adapter.discover_reports("preflight")

    assert len(reports) == 1
    assert reports[0].schema_version is None
