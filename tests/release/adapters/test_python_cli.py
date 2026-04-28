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

    assert command[:2] == ["python", "acm_switchover.py"]
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
    assert state_file.endswith("python-restore-only/state.json")
