from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.release.adapters.bash import BashAdapter
from tests.release.reporting.artifacts import ReleaseArtifacts
from tests.release.test_release_certification import execute_bash_scenarios


def test_bash_preflight_command_uses_profile_contexts(tmp_path: Path) -> None:
    adapter = BashAdapter(
        Path("/repo"),
        "primary",
        "secondary",
        "/kube/primary",
        "/kube/secondary",
        tmp_path,
    )

    command = adapter.build_command("preflight")

    assert command[0] == "scripts/preflight-check.sh"
    assert "primary" in command
    assert "secondary" in command


def test_bash_adapter_execute_returns_stream_result(monkeypatch, tmp_path: Path) -> None:
    def fake_run(command, cwd, text, capture_output, check, timeout):
        return subprocess.CompletedProcess(command, 0, stdout="Summary: 0 failed checks\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = BashAdapter(
        Path("/repo"),
        "primary",
        "secondary",
        "/kube/primary",
        "/kube/secondary",
        tmp_path,
    )

    result = adapter.execute("preflight")

    assert result.stream == "bash"
    assert result.status == "passed"
    assert result.assertions[0].capability == "bash-preflight"


def test_bash_adapter_execute_surfaces_redaction_rejection(monkeypatch, tmp_path: Path) -> None:
    from tests.release.reporting.redaction import RedactionError

    def fake_run(command, cwd, text, capture_output, check, timeout):
        return subprocess.CompletedProcess(command, 0, stdout="output", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(
        "tests.release.reporting.artifacts.sanitize_text",
        lambda _: (_ for _ in ()).throw(RedactionError("sensitive")),
    )
    artifacts = ReleaseArtifacts.create(root=tmp_path, run_id="run-1")
    adapter = BashAdapter(
        Path("/repo"),
        "primary",
        "secondary",
        "/kube/primary",
        "/kube/secondary",
        artifacts.run_dir,
    )

    result = adapter.execute("preflight")

    assert result.status == "failed"
    assert any(a.name == "artifact-redaction" for a in result.assertions)
    assert "scenarios/preflight/bash/stdout.txt" in (artifacts.run_dir / "redaction.json").read_text(encoding="utf-8")


def test_build_command_raises_for_unknown_scenario(tmp_path: Path) -> None:
    adapter = BashAdapter(
        Path("/repo"),
        "primary",
        "secondary",
        "/kube/primary",
        "/kube/secondary",
        tmp_path,
    )

    with pytest.raises(ValueError, match="Unknown scenario"):
        adapter.build_command("not-a-real-scenario")


def test_bash_adapter_execute_uses_profile_timeout_env_and_extra_args(monkeypatch, tmp_path: Path) -> None:
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["timeout"] = kwargs["timeout"]
        captured["env"] = kwargs["env"]

        class Completed:
            returncode = 0
            stdout = "ok"
            stderr = ""

        return Completed()

    monkeypatch.setattr("subprocess.run", fake_run)
    adapter = BashAdapter(
        Path("/repo"),
        "primary",
        "secondary",
        "/kube/primary",
        "/kube/secondary",
        tmp_path,
    )

    adapter.execute(
        "preflight",
        timeout_seconds=56,
        env={"ACM_BASH_RELEASE": "1"},
        extra_args=("--verbose",),
    )

    assert captured["timeout"] == 56
    assert captured["env"]["ACM_BASH_RELEASE"] == "1"
    assert captured["command"][-1] == "--verbose"


def test_bash_adapter_execute_handles_timeout(monkeypatch, tmp_path: Path) -> None:
    def fake_run(command, cwd, text, capture_output, check, timeout):
        raise subprocess.TimeoutExpired(
            cmd=command,
            timeout=timeout,
            output="partial-stdout",
            stderr="partial-stderr",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = BashAdapter(
        Path("/repo"),
        "primary",
        "secondary",
        "/kube/primary",
        "/kube/secondary",
        tmp_path,
    )

    result = adapter.execute("preflight")

    assert result.status == "failed"
    assert result.returncode == -1
    exit_code = next(a for a in result.assertions if a.name == "exit-code")
    assert exit_code.actual == "timeout"
    assert "timed out" in exit_code.message
    assert Path(result.stdout_path).read_text(encoding="utf-8") == "partial-stdout"
    assert Path(result.stderr_path).read_text(encoding="utf-8") == "partial-stderr"


class FakeBashAdapter:
    def execute(self, scenario_id: str):
        return {"scenario_id": scenario_id, "stream": "bash", "status": "passed"}


def test_execute_bash_scenarios_runs_only_bash_supported_ids() -> None:
    results = execute_bash_scenarios(
        adapter=FakeBashAdapter(),
        scenario_ids=("preflight", "python-passive-switchover"),
    )

    assert [item["scenario_id"] for item in results] == ["preflight"]
