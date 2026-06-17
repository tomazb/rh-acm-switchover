from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.release import run_lab_role_controller as cli
from tests.release.lab_controller.models import CertificationDecision

REPO_ROOT = Path(__file__).resolve().parents[2]


def _run(args: list[str]) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    code = cli.main(args, stdout=stdout, stderr=stderr)
    return code, stdout.getvalue(), stderr.getvalue()


def _artifact(path: Path) -> dict:
    return json.loads((path / "lab-controller-run.json").read_text(encoding="utf-8"))


def test_fake_mode_ping_pong_cli_returns_pass_with_sanitized_artifact(tmp_path: Path) -> None:
    code, stdout, stderr = _run(["--plan", "ping-pong", "--mode", "fake", "--artifact-dir", str(tmp_path)])

    artifact = _artifact(tmp_path)
    artifact_text = json.dumps(artifact, sort_keys=True)

    assert code == 0
    assert stderr == ""
    assert artifact["final_decision"] == "PASS"
    assert artifact["cli_metadata"]["selected_plan"] == "ping-pong"
    assert artifact["cli_metadata"]["selected_mode"] == "fake"
    assert artifact["live_certification_evidence"] is False
    assert "/.kube/" not in artifact_text
    assert "live_certification_evidence=false" in stdout


def test_documented_script_invocation_works_without_pythonpath(tmp_path: Path) -> None:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)

    result = subprocess.run(
        [
            sys.executable,
            "scripts/release/run_lab_role_controller.py",
            "--plan",
            "ping-pong",
            "--mode",
            "fake",
            "--artifact-dir",
            str(tmp_path),
        ],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert _artifact(tmp_path)["final_decision"] == "PASS"
    assert "live_certification_evidence=false" in result.stdout


def test_release_framework_dry_run_writes_materialized_requests_without_execution(tmp_path: Path) -> None:
    code, stdout, _ = _run(
        ["--plan", "ping-pong", "--mode", "release-framework-dry-run", "--artifact-dir", str(tmp_path)]
    )

    artifact = _artifact(tmp_path)

    assert code == 0
    assert artifact["materialized_release_framework"]["materialized_segments"] == 5
    assert artifact["execution_harness_summary"]["executed_segments"] == 0
    assert artifact["real_execution_evidence"] is False
    assert artifact["live_certification_evidence"] is False
    assert "final_decision=PASS" in stdout


@pytest.mark.parametrize("mode", ["live", "release-framework-live"])
def test_unsupported_live_mode_fails_closed_without_artifact(tmp_path: Path, mode: str) -> None:
    code, _, stderr = _run(["--plan", "ping-pong", "--mode", mode, "--artifact-dir", str(tmp_path)])

    assert code == 2
    assert "live execution mode is unsupported" in stderr
    assert not list(tmp_path.iterdir())


def test_artifact_dir_is_required_unless_no_write() -> None:
    code, _, stderr = _run(["--plan", "ping-pong", "--mode", "fake"])

    assert code == 2
    assert "--artifact-dir is required unless --no-write is set" in stderr


def test_artifact_dir_traversal_is_rejected(tmp_path: Path) -> None:
    code, _, stderr = _run(["--plan", "ping-pong", "--artifact-dir", str(tmp_path / ".." / "out")])

    assert code == 2
    assert "path traversal" in stderr


def test_unsafe_absolute_artifact_facing_path_is_rejected() -> None:
    code, _, stderr = _run(["--plan", "ping-pong", "--artifact-dir", "/home/operator/.kube/output"])

    assert code == 2
    assert "unsafe artifact directory" in stderr


def test_no_write_prints_sanitized_summary_and_creates_no_files(tmp_path: Path) -> None:
    code, stdout, stderr = _run(["--plan", "ping-pong", "--mode", "fake", "--no-write"])

    assert code == 0
    assert stderr == ""
    assert "final_decision=PASS" in stdout
    assert "artifact_path=not_written" in stdout
    assert not list(tmp_path.iterdir())


def test_strict_returns_non_zero_for_non_pass_controller_decision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_runner = cli.run_controller

    def no_go_runner(*args, **kwargs):
        result = original_runner(*args, **kwargs)
        return cli.with_final_decision(result, CertificationDecision.NO_GO)

    monkeypatch.setattr(cli, "run_controller", no_go_runner)

    code, stdout, _ = _run(
        [
            "--plan",
            "ping-pong",
            "--mode",
            "fake",
            "--artifact-dir",
            str(tmp_path),
            "--strict",
        ]
    )

    assert code == 1
    assert _artifact(tmp_path)["final_decision"] == "NO_GO"
    assert "final_decision=NO_GO" in stdout


def test_non_strict_non_pass_decision_is_deterministic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_runner = cli.run_controller

    def no_go_runner(*args, **kwargs):
        result = original_runner(*args, **kwargs)
        return cli.with_final_decision(result, CertificationDecision.NO_GO)

    monkeypatch.setattr(cli, "run_controller", no_go_runner)

    code, _, _ = _run(
        [
            "--plan",
            "ping-pong",
            "--mode",
            "fake",
            "--artifact-dir",
            str(tmp_path),
        ]
    )

    assert code == 0
    assert _artifact(tmp_path)["final_decision"] == "NO_GO"


def test_stdout_summary_contains_decision_and_no_live_certification_evidence(tmp_path: Path) -> None:
    code, stdout, _ = _run(["--plan", "ping-pong", "--mode", "fake", "--artifact-dir", str(tmp_path)])

    assert code == 0
    assert "final_decision=PASS" in stdout
    assert "safe_to_continue=true" in stdout
    assert "retry_allowed=false" in stdout
    assert "manual_recovery_required=false" in stdout
    assert "live_certification_evidence=false" in stdout


def test_stdout_summary_includes_first_blocking_reason_for_non_pass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_runner = cli.run_controller

    def no_go_runner(*args, **kwargs):
        result = original_runner(*args, **kwargs)
        return cli.with_final_decision(result, CertificationDecision.NO_GO)

    monkeypatch.setattr(cli, "run_controller", no_go_runner)

    code, stdout, _ = _run(["--plan", "ping-pong", "--mode", "fake", "--artifact-dir", str(tmp_path)])

    assert code == 0
    assert "first_blocking_reason=simulated final decision" in stdout


def test_stdout_summary_does_not_leak_kubeconfig_like_paths(tmp_path: Path) -> None:
    code, stdout, _ = _run(["--plan", "ping-pong", "--mode", "fake", "--artifact-dir", str(tmp_path)])

    assert code == 0
    assert str(tmp_path) not in stdout
    assert "/.kube/" not in stdout
    assert "kubeconfig" not in stdout.lower()


def test_artifact_json_contains_expected_top_level_run_fields(tmp_path: Path) -> None:
    code, _, _ = _run(["--plan", "ping-pong", "--mode", "fake", "--artifact-dir", str(tmp_path)])

    artifact = _artifact(tmp_path)

    assert code == 0
    assert artifact["schema_version"] == 1
    assert artifact["controller_phase"] == "phase7a"
    assert artifact["plan_id"] == "phase7a-ping-pong"
    assert artifact["segment_decisions"]
    assert artifact["role_transition_graph"]


def test_artifact_json_includes_cli_metadata(tmp_path: Path) -> None:
    code, _, _ = _run(["--plan", "ping-pong", "--mode", "fake", "--artifact-dir", str(tmp_path), "--strict"])

    metadata = _artifact(tmp_path)["cli_metadata"]

    assert code == 0
    assert metadata["script_name"] == "scripts/release/run_lab_role_controller.py"
    assert metadata["strict"] is True
    assert metadata["write_mode"] == "write"
    assert metadata["no_live_execution_evidence"] is True
    assert metadata["no_live_certification_evidence"] is True


@pytest.mark.parametrize("mode", ["fake", "release-framework-dry-run"])
def test_fake_and_dry_run_artifacts_have_no_real_execution_evidence(tmp_path: Path, mode: str) -> None:
    code, _, _ = _run(["--plan", "ping-pong", "--mode", mode, "--artifact-dir", str(tmp_path)])

    assert code == 0
    assert _artifact(tmp_path)["real_execution_evidence"] is False


def test_dry_run_mode_does_not_execute_pytest_or_adapters(tmp_path: Path) -> None:
    code, _, _ = _run(["--plan", "ping-pong", "--mode", "release-framework-dry-run", "--artifact-dir", str(tmp_path)])

    artifact = _artifact(tmp_path)

    assert code == 0
    assert artifact["execution_harness_summary"]["executed_segments"] == 0
    assert all(
        segment["execution_summary"]["pytest_invoked"] is False
        for segment in artifact["segment_artifacts"]
        if segment["execution_mode"] == "release_framework_dry_run"
    )
    assert all(
        segment["execution_summary"]["adapters_invoked"] is False
        for segment in artifact["segment_artifacts"]
        if segment["execution_mode"] == "release_framework_dry_run"
    )


def test_local_mode_without_allow_local_execution_fails_closed(tmp_path: Path) -> None:
    code, _, stderr = _run(
        ["--plan", "ping-pong", "--mode", "release-framework-local", "--artifact-dir", str(tmp_path)]
    )

    assert code == 2
    assert "requires --allow-local-execution" in stderr
    assert not list(tmp_path.iterdir())


def test_local_mode_with_allow_local_execution_uses_fake_runner_only(tmp_path: Path) -> None:
    code, _, _ = _run(
        [
            "--plan",
            "ping-pong",
            "--mode",
            "release-framework-local",
            "--artifact-dir",
            str(tmp_path),
            "--allow-local-execution",
        ]
    )

    artifact = _artifact(tmp_path)

    assert code == 0
    assert artifact["execution_harness_summary"]["executed_segments"] == 5
    assert all(
        segment["command_runner_kind"] == "fake" for segment in artifact["segment_artifacts"] if segment["executed"]
    )
    assert artifact["live_certification_evidence"] is False


def test_invalid_plan_fails_closed(tmp_path: Path) -> None:
    code, _, stderr = _run(["--plan", "future-plan", "--artifact-dir", str(tmp_path)])

    assert code == 2
    assert "unsupported plan" in stderr
    assert not list(tmp_path.iterdir())


def test_invalid_mode_fails_closed(tmp_path: Path) -> None:
    code, _, stderr = _run(["--plan", "ping-pong", "--mode", "future-mode", "--artifact-dir", str(tmp_path)])

    assert code == 2
    assert "unsupported mode" in stderr
    assert not list(tmp_path.iterdir())


def test_help_text_lists_only_supported_non_live_modes() -> None:
    help_text = cli._parser().format_help()

    assert "ping-pong" in help_text
    assert "fake" in help_text
    assert "release-framework-dry-run" in help_text
    assert "release-framework-local" in help_text
    assert "live certification support" not in help_text.lower()


def test_redaction_failure_prevents_unsafe_artifact_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original_builder = cli.build_run_artifact

    def unsafe_builder(*args, **kwargs):
        payload = original_builder(*args, **kwargs)
        payload["unsafe"] = "token: abc123"
        return payload

    monkeypatch.setattr(cli, "build_run_artifact", unsafe_builder)

    code, _, stderr = _run(["--plan", "ping-pong", "--mode", "fake", "--artifact-dir", str(tmp_path)])

    assert code == 3
    assert "artifact payload contains unredacted sensitive metadata" in stderr
    assert not (tmp_path / "lab-controller-run.json").exists()


def test_cli_does_not_read_environment_for_kubeconfigs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KUBECONFIG", "/home/operator/.kube/config")
    monkeypatch.setenv("ACM_RELEASE_PROFILE", "/home/operator/private-profile.yaml")

    code, stdout, _ = _run(["--plan", "ping-pong", "--mode", "release-framework-dry-run", "--no-write"])

    assert code == 0
    assert "/home/operator" not in stdout
    assert "kubeconfig" not in stdout.lower()
    assert not (tmp_path / ".release").exists()


def test_no_release_directory_is_created(tmp_path: Path) -> None:
    code, _, _ = _run(["--plan", "ping-pong", "--mode", "fake", "--artifact-dir", str(tmp_path)])

    assert code == 0
    assert not (Path.cwd() / ".release").exists()


def test_output_format_json_prints_sanitized_json_summary_without_writing() -> None:
    code, stdout, _ = _run(["--plan", "ping-pong", "--mode", "fake", "--no-write", "--output-format", "json"])

    summary = json.loads(stdout)

    assert code == 0
    assert summary["final_decision"] == "PASS"
    assert summary["artifact_path"] == "not_written"
    assert summary["live_certification_evidence"] is False
    assert "kubeconfig" not in stdout.lower()


def test_run_artifact_rejects_live_certification_claim(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original_runner = cli.run_controller

    def unsafe_runner(*args, **kwargs):
        result = original_runner(*args, **kwargs)
        result.artifact_bundle.payload["live_certification_evidence"] = True
        return result

    monkeypatch.setattr(cli, "run_controller", unsafe_runner)

    code, _, stderr = _run(["--plan", "ping-pong", "--mode", "fake", "--artifact-dir", str(tmp_path)])

    assert code == 3
    assert "live certification evidence" in stderr
    assert not (tmp_path / "lab-controller-run.json").exists()


def test_final_decision_override_helper_uses_controller_decision_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_runner = cli.run_controller

    def recovery_runner(*args, **kwargs):
        result = original_runner(*args, **kwargs)
        return cli.with_final_decision(result, CertificationDecision.RECOVERY_REQUIRED)

    monkeypatch.setattr(cli, "run_controller", recovery_runner)

    code, _, _ = _run(["--plan", "ping-pong", "--mode", "fake", "--artifact-dir", str(tmp_path)])

    assert code == 0
    assert _artifact(tmp_path)["final_decision"] == CertificationDecision.RECOVERY_REQUIRED.value
