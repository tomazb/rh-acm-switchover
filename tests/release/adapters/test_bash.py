from __future__ import annotations

from pathlib import Path
import subprocess

from tests.release.adapters.bash import BashAdapter
from tests.release.test_release_certification import execute_bash_scenarios


def test_bash_preflight_command_uses_profile_contexts(tmp_path: Path) -> None:
    adapter = BashAdapter(Path("/repo"), "primary", "secondary", "/kube/primary", "/kube/secondary", tmp_path)

    command = adapter.build_command("preflight")

    assert command[0] == "scripts/preflight-check.sh"
    assert "primary" in command
    assert "secondary" in command


def test_bash_adapter_execute_returns_stream_result(monkeypatch, tmp_path: Path) -> None:
    def fake_run(command, cwd, text, capture_output, check):
        return subprocess.CompletedProcess(command, 0, stdout="Summary: 0 failed checks\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = BashAdapter(Path("/repo"), "primary", "secondary", "/kube/primary", "/kube/secondary", tmp_path)

    result = adapter.execute("preflight")

    assert result.stream == "bash"
    assert result.status == "passed"
    assert result.assertions[0].capability == "bash-preflight"


def test_bash_adapter_execute_surfaces_redaction_rejection(monkeypatch, tmp_path: Path) -> None:
    from tests.release.reporting.redaction import RedactionError

    def fake_run(command, cwd, text, capture_output, check):
        return subprocess.CompletedProcess(command, 0, stdout="output", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("tests.release.adapters.bash.sanitize_text", lambda _: (_ for _ in ()).throw(RedactionError("sensitive")))
    adapter = BashAdapter(Path("/repo"), "primary", "secondary", "/kube/primary", "/kube/secondary", tmp_path)

    result = adapter.execute("preflight")

    assert result.status == "failed"
    assert any(a.name == "artifact-redaction" for a in result.assertions)


class FakeBashAdapter:
    def execute(self, scenario_id: str):
        return {"scenario_id": scenario_id, "stream": "bash", "status": "passed"}


def test_execute_bash_scenarios_runs_only_bash_supported_ids() -> None:
    results = execute_bash_scenarios(
        adapter=FakeBashAdapter(),
        scenario_ids=("preflight", "python-passive-switchover"),
    )

    assert [item["scenario_id"] for item in results] == ["preflight"]
