"""Tests for the Ansible release stream adapter."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.release.adapters.ansible import AnsibleAdapter


def _make_adapter(tmp_path: Path) -> AnsibleAdapter:
    return AnsibleAdapter(
        repo_root=Path("/repo"),
        collection_root=Path("/repo/ansible_collections/tomazb/acm_switchover"),
        primary_context="primary",
        secondary_context="secondary",
        primary_kubeconfig="/kube/primary",
        secondary_kubeconfig="/kube/secondary",
        artifact_dir=tmp_path,
    )


# ---------------------------------------------------------------------------
# Task 1: command construction
# ---------------------------------------------------------------------------


def test_ansible_preflight_command_uses_collection_playbook_and_profile_vars(tmp_path: Path) -> None:
    adapter = AnsibleAdapter(
        repo_root=Path("/repo"),
        collection_root=Path("/repo/collection"),
        primary_context="primary",
        secondary_context="secondary",
        primary_kubeconfig="/kube/primary",
        secondary_kubeconfig="/kube/secondary",
        artifact_dir=tmp_path,
    )

    command = adapter.build_command("preflight")

    assert command[:2] == ["ansible-playbook", "playbooks/preflight.yml"]
    assert "-e" in command
    assert "primary" in " ".join(command)
    assert "secondary" in " ".join(command)


def test_ansible_restore_only_uses_checkpoint_path(tmp_path: Path) -> None:
    adapter = AnsibleAdapter(Path("/repo"), Path("/repo/collection"), "primary", "secondary", "/kube/primary", "/kube/secondary", tmp_path)

    command = adapter.build_command("ansible-restore-only")

    assert "playbooks/restore_only.yml" in command
    assert "checkpoint.json" in " ".join(command)


# ---------------------------------------------------------------------------
# Task 2: execution capture and report discovery
# ---------------------------------------------------------------------------


def test_ansible_adapter_execute_captures_output(monkeypatch, tmp_path: Path) -> None:
    def fake_run(command, cwd, text, capture_output, check):
        assert cwd == Path("/repo/collection")
        return subprocess.CompletedProcess(command, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = AnsibleAdapter(Path("/repo"), Path("/repo/collection"), "primary", "secondary", "/kube/primary", "/kube/secondary", tmp_path)

    result = adapter.execute("preflight")

    assert result.stream == "ansible"
    assert result.status == "passed"
    assert Path(result.stdout_path).read_text(encoding="utf-8") == "ok\n"


def test_ansible_adapter_discovers_preflight_report(tmp_path: Path) -> None:
    adapter = AnsibleAdapter(Path("/repo"), Path("/repo/collection"), "primary", "secondary", "/kube/primary", "/kube/secondary", tmp_path)
    scenario_dir = adapter.scenario_dir("preflight")
    scenario_dir.mkdir(parents=True)
    (scenario_dir / "preflight-report.json").write_text('{"schema_version": "1.0", "status": "passed"}', encoding="utf-8")

    reports = adapter.discover_reports("preflight")

    assert reports[0].schema_version == "1.0"
    assert reports[0].type == "preflight"


def test_ansible_adapter_failed_command_sets_failed_status(monkeypatch, tmp_path: Path) -> None:
    def fake_run(command, cwd, text, capture_output, check):
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="error output\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = _make_adapter(tmp_path)

    result = adapter.execute("ansible-passive-switchover")

    assert result.status == "failed"
    assert result.returncode == 1


def test_ansible_adapter_discover_reports_returns_empty_for_missing_file(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)

    reports = adapter.discover_reports("preflight")

    assert reports == []


def test_ansible_adapter_discover_reports_returns_empty_for_unknown_scenario(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)

    reports = adapter.discover_reports("unknown-scenario")

    assert reports == []


# ---------------------------------------------------------------------------
# Task 4: scenario wiring
# ---------------------------------------------------------------------------


def test_execute_ansible_scenarios_filters_ansible_ids() -> None:
    from tests.release.test_release_certification import execute_ansible_scenarios

    class FakeAnsibleAdapter:
        def execute(self, scenario_id: str):
            return {"scenario_id": scenario_id, "stream": "ansible", "status": "passed"}

    results = execute_ansible_scenarios(
        adapter=FakeAnsibleAdapter(),
        scenario_ids=("preflight", "python-passive-switchover", "ansible-passive-switchover"),
    )

    assert [item["scenario_id"] for item in results] == ["preflight", "ansible-passive-switchover"]
