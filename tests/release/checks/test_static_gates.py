import subprocess
from pathlib import Path

from tests.release.checks.static_gates import GateCommand, build_default_gate_commands, run_gate_command


def test_run_gate_command_records_returncode_and_output(tmp_path: Path) -> None:
    result = run_gate_command(
        GateCommand(gate_id="sample", label="python-version", command=["python", "-c", "print('ok')"], cwd=Path.cwd()),
        artifact_dir=tmp_path,
    )

    assert result.gate_id == "sample"
    assert result.status == "passed"
    assert result.returncode == 0
    assert Path(result.stdout_path).read_text(encoding="utf-8").strip() == "ok"


def test_run_gate_command_fails_on_nonzero_return(tmp_path: Path) -> None:
    result = run_gate_command(
        GateCommand(gate_id="sample", label="bad", command=["python", "-c", "import sys; sys.exit(7)"], cwd=Path.cwd()),
        artifact_dir=tmp_path,
    )

    assert result.status == "failed"
    assert result.returncode == 7


def test_run_gate_command_records_timeout_as_failed_result(tmp_path: Path, monkeypatch) -> None:
    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd=["python", "-c", "print('slow')"],
            timeout=300,
            output="partial stdout\n",
            stderr="partial stderr\n",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = run_gate_command(
        GateCommand(gate_id="sample", label="timeout", command=["python", "-c", "print('slow')"], cwd=Path.cwd()),
        artifact_dir=tmp_path,
    )

    assert result.status == "failed"
    assert result.returncode == -1
    assert Path(result.stdout_path).read_text(encoding="utf-8") == "partial stdout\n"
    stderr_text = Path(result.stderr_path).read_text(encoding="utf-8")
    assert "timed out after 300 seconds" in stderr_text
    assert "partial stderr" in stderr_text


def test_python_and_ansible_streams_enable_expected_gate_ids() -> None:
    gates = build_default_gate_commands(enabled_streams=("python", "ansible"), repo_root=Path("/repo"))
    gate_ids = {gate.gate_id for gate in gates}

    assert "root-non-e2e-tests" in gate_ids
    assert "static-parity-tests" in gate_ids
    assert "python-cli-smoke" in gate_ids
    assert "collection-build-install" in gate_ids
    assert "collection-playbook-syntax" in gate_ids
    assert "collection-integration-tests" in gate_ids
    assert "collection-scenario-tests" in gate_ids


def test_ansible_stream_includes_restore_only_syntax_gate() -> None:
    gates = build_default_gate_commands(enabled_streams=("ansible",), repo_root=Path("/repo"))
    restore_only_gates = [
        gate
        for gate in gates
        if gate.gate_id == "collection-playbook-syntax" and any("restore_only.yml" in part for part in gate.command)
    ]

    assert restore_only_gates


def test_bash_only_profile_still_runs_local_root_gate() -> None:
    gates = build_default_gate_commands(enabled_streams=("bash",), repo_root=Path("/repo"))
    assert [gate.gate_id for gate in gates] == ["root-non-e2e-tests"]
