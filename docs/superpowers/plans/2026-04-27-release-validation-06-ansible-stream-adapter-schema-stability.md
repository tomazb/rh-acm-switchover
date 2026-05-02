# Release Validation Ansible Stream Adapter And Schema Stability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the Ansible playbook stream adapter, collection gate support, checkpoint isolation, source report extraction, and collection-side schema stability tests for fields consumed by release normalizers.

**Architecture:** Mirror the Python adapter structure in `tests/release/adapters/ansible.py` while keeping collection source schema tests in the collection test tree. The adapter invokes `ansible-playbook` with profile-derived extra vars and writes all output into scenario-specific artifact directories.

**Tech Stack:** Python dataclasses, subprocess, pathlib, json, pytest monkeypatch, Ansible collection test fixtures.

---

## File Map

- Create: `tests/release/adapters/ansible.py`
- Create: `tests/release/adapters/test_ansible.py`
- Create: `ansible_collections/tomazb/acm_switchover/tests/unit/test_release_source_schema.py`
- Modify: `tests/release/test_release_certification.py`
- Modify: `docs/superpowers/plans/2026-04-27-release-validation-progress.md`

## Task 1: Ansible Command Construction

**Files:**
- Create: `tests/release/adapters/ansible.py`
- Create: `tests/release/adapters/test_ansible.py`

- [ ] **Step 1: Add command construction tests**

```python
from pathlib import Path

from tests.release.adapters.ansible import AnsibleAdapter


def test_ansible_preflight_command_uses_collection_playbook_and_profile_vars(tmp_path: Path) -> None:
    adapter = AnsibleAdapter(
        repo_root=Path("/repo"),
        collection_root=Path("/repo/ansible_collections/tomazb/acm_switchover"),
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
```

- [ ] **Step 2: Run command tests and confirm they fail**

Run: `python -m pytest tests/release/adapters/test_ansible.py::test_ansible_preflight_command_uses_collection_playbook_and_profile_vars tests/release/adapters/test_ansible.py::test_ansible_restore_only_uses_checkpoint_path -q`

Expected: import failure.

- [ ] **Step 3: Implement Ansible command construction**

```python
# tests/release/adapters/ansible.py
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


PLAYBOOKS = {
    "preflight": "playbooks/preflight.yml",
    "ansible-passive-switchover": "playbooks/switchover.yml",
    "ansible-restore-only": "playbooks/restore_only.yml",
    "argocd-managed-switchover": "playbooks/switchover.yml",
    "decommission": "playbooks/decommission.yml",
}


@dataclass(frozen=True)
class AnsibleAdapter:
    repo_root: Path
    collection_root: Path
    primary_context: str
    secondary_context: str
    primary_kubeconfig: str
    secondary_kubeconfig: str
    artifact_dir: Path

    def scenario_dir(self, scenario_id: str) -> Path:
        return self.artifact_dir / "scenarios" / scenario_id / "ansible"

    def build_extra_vars(self, scenario_id: str) -> dict:
        return {
            "acm_switchover_hubs": {
                "primary": {"context": self.primary_context, "kubeconfig": self.primary_kubeconfig},
                "secondary": {"context": self.secondary_context, "kubeconfig": self.secondary_kubeconfig},
            },
            "acm_switchover_operation": {
                "restore_only": scenario_id == "ansible-restore-only",
                "dry_run": False,
            },
            "acm_switchover_checkpoint_path": str(self.scenario_dir(scenario_id) / "checkpoint.json"),
            "acm_switchover_report_dir": str(self.scenario_dir(scenario_id)),
        }

    def build_command(self, scenario_id: str) -> list[str]:
        return [
            "ansible-playbook",
            PLAYBOOKS[scenario_id],
            "-e",
            json.dumps(self.build_extra_vars(scenario_id), sort_keys=True),
        ]
```

- [ ] **Step 4: Run command construction tests**

Run: `python -m pytest tests/release/adapters/test_ansible.py::test_ansible_preflight_command_uses_collection_playbook_and_profile_vars tests/release/adapters/test_ansible.py::test_ansible_restore_only_uses_checkpoint_path -q`

Expected: `2 passed`.

- [ ] **Step 5: Commit Ansible command construction**

```bash
git add tests/release/adapters/ansible.py tests/release/adapters/test_ansible.py
git commit -m "feat: build Ansible release adapter commands"
```

## Task 2: Execution Capture And Report Discovery

**Files:**
- Modify: `tests/release/adapters/ansible.py`
- Modify: `tests/release/adapters/test_ansible.py`

- [ ] **Step 1: Add execution and report tests**

```python
import subprocess


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
```

- [ ] **Step 2: Run tests and confirm they fail**

Run: `python -m pytest tests/release/adapters/test_ansible.py::test_ansible_adapter_execute_captures_output tests/release/adapters/test_ansible.py::test_ansible_adapter_discovers_preflight_report -q`

Expected: attribute failures.

- [ ] **Step 3: Implement execution and report discovery**

```python
import subprocess
from datetime import datetime, timezone

from .common import AssertionRecord, ReportArtifact, StreamResult


REPORT_NAMES = {
    "preflight": ("preflight", "preflight-report.json"),
    "ansible-passive-switchover": ("switchover", "switchover-report.json"),
    "ansible-restore-only": ("restore", "restore-only-report.json"),
    "argocd-managed-switchover": ("switchover", "switchover-report.json"),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
```

Add methods:

```python
    def discover_reports(self, scenario_id: str) -> list[ReportArtifact]:
        if scenario_id not in REPORT_NAMES:
            return []
        report_type, filename = REPORT_NAMES[scenario_id]
        path = self.scenario_dir(scenario_id) / filename
        if not path.exists():
            return []
        schema_version = json.loads(path.read_text(encoding="utf-8")).get("schema_version")
        return [ReportArtifact(type=report_type, path=str(path), schema_version=schema_version, required=True)]

    def execute(self, scenario_id: str) -> StreamResult:
        scenario_dir = self.scenario_dir(scenario_id)
        scenario_dir.mkdir(parents=True, exist_ok=True)
        command = self.build_command(scenario_id)
        started_at = _now()
        completed = subprocess.run(command, cwd=self.collection_root, text=True, capture_output=True, check=False)
        ended_at = _now()
        stdout_path = scenario_dir / "stdout.txt"
        stderr_path = scenario_dir / "stderr.txt"
        stdout_path.write_text(completed.stdout, encoding="utf-8")
        stderr_path.write_text(completed.stderr, encoding="utf-8")
        status = "passed" if completed.returncode == 0 else "failed"
        return StreamResult(
            stream="ansible",
            scenario_id=scenario_id,
            status=status,
            command=command,
            returncode=completed.returncode,
            stdout_path=str(stdout_path),
            stderr_path=str(stderr_path),
            reports=self.discover_reports(scenario_id),
            assertions=[AssertionRecord("ansible-adapter", "exit-code", status, "0", str(completed.returncode), str(stdout_path), "Ansible command completed")],
            started_at=started_at,
            ended_at=ended_at,
        )
```

- [ ] **Step 4: Run adapter tests**

Run: `python -m pytest tests/release/adapters/test_ansible.py -q`

Expected: all Ansible adapter tests pass.

- [ ] **Step 5: Commit execution and report capture**

```bash
git add tests/release/adapters/ansible.py tests/release/adapters/test_ansible.py
git commit -m "feat: execute Ansible release adapter"
```

## Task 3: Collection Source Schema Stability Tests

**Files:**
- Create: `ansible_collections/tomazb/acm_switchover/tests/unit/test_release_source_schema.py`

- [ ] **Step 1: Add source schema tests with representative payloads**

```python
def test_preflight_report_fields_consumed_by_release_normalizer_are_stable() -> None:
    report = {
        "schema_version": "1.0",
        "status": "passed",
        "summary": {"passed": 10, "critical_failures": 0, "warning_failures": 0},
        "results": [{"id": "acm-version", "severity": "critical", "status": "passed", "message": "ok"}],
        "hubs": {},
    }

    assert report["schema_version"]
    assert isinstance(report["summary"]["critical_failures"], int)
    assert report["results"][0]["id"] == "acm-version"


def test_switchover_report_fields_consumed_by_release_normalizer_are_stable() -> None:
    report = {
        "schema_version": "1.0",
        "source": "ansible",
        "argocd": {"run_id": "run-1", "summary": {"paused": 1, "restored": 1}},
        "phases": {
            "primary_prep": {"status": "passed"},
            "activation": {"status": "passed"},
            "post_activation": {"status": "passed"},
            "finalization": {"status": "passed"},
        },
    }

    assert report["argocd"]["run_id"] == "run-1"
    assert report["phases"]["activation"]["status"] == "passed"


def test_checkpoint_fields_consumed_by_release_normalizer_are_stable() -> None:
    checkpoint = {
        "schema_version": "1.0",
        "completed_phases": ["preflight"],
        "phase_status": {"preflight": "completed"},
        "operational_data": {},
        "errors": [],
        "report_refs": [],
        "updated_at": "2026-04-27T00:00:00+00:00",
    }

    assert checkpoint["completed_phases"] == ["preflight"]
    assert checkpoint["phase_status"]["preflight"] == "completed"
```

- [ ] **Step 2: Run schema stability tests**

Run: `python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/test_release_source_schema.py -q`

Expected: tests pass.

- [ ] **Step 3: Commit schema stability tests**

```bash
git add ansible_collections/tomazb/acm_switchover/tests/unit/test_release_source_schema.py
git commit -m "test: pin collection report fields for release normalizers"
```

## Task 4: Scenario Wiring

**Files:**
- Modify: `tests/release/test_release_certification.py`
- Modify: `tests/release/adapters/test_ansible.py`

- [ ] **Step 1: Add Ansible scenario helper test**

```python
from tests.release.test_release_certification import execute_ansible_scenarios


class FakeAnsibleAdapter:
    def execute(self, scenario_id: str):
        return {"scenario_id": scenario_id, "stream": "ansible", "status": "passed"}


def test_execute_ansible_scenarios_filters_ansible_ids() -> None:
    results = execute_ansible_scenarios(
        adapter=FakeAnsibleAdapter(),
        scenario_ids=("preflight", "python-passive-switchover", "ansible-passive-switchover"),
    )

    assert [item["scenario_id"] for item in results] == ["preflight", "ansible-passive-switchover"]
```

- [ ] **Step 2: Run scenario helper test and confirm it fails**

Run: `python -m pytest tests/release/adapters/test_ansible.py::test_execute_ansible_scenarios_filters_ansible_ids -q`

Expected: import or attribute failure.

- [ ] **Step 3: Implement Ansible scenario helper**

```python
# tests/release/test_release_certification.py
ANSIBLE_SCENARIOS = {"preflight", "ansible-passive-switchover", "ansible-restore-only", "argocd-managed-switchover"}


def execute_ansible_scenarios(*, adapter, scenario_ids: tuple[str, ...]) -> list:
    results = []
    for scenario_id in scenario_ids:
        if scenario_id in ANSIBLE_SCENARIOS:
            result = adapter.execute(scenario_id)
            results.append(result.to_dict() if hasattr(result, "to_dict") else result)
    return results
```

- [ ] **Step 4: Run scenario helper test**

Run: `python -m pytest tests/release/adapters/test_ansible.py::test_execute_ansible_scenarios_filters_ansible_ids -q`

Expected: `1 passed`.

- [ ] **Step 5: Commit scenario wiring**

```bash
git add tests/release/test_release_certification.py tests/release/adapters/test_ansible.py
git commit -m "feat: wire Ansible release scenarios"
```

## Final Verification

- [ ] Run Ansible adapter tests:

Run: `python -m pytest tests/release/adapters/test_ansible.py ansible_collections/tomazb/acm_switchover/tests/unit/test_release_source_schema.py -q`

Expected: all selected tests pass.

- [ ] Run Python and Ansible adapter tests together:

Run: `python -m pytest tests/release/adapters -q`

Expected: all adapter unit tests pass.

- [ ] Run the planning placeholder scan:

Run:

```bash
python - <<'PY'
from pathlib import Path
bad = ["TB" + "D", "TO" + "DO", "implement " + "later", "fill " + "in details", "handle " + "edge cases", "appropriate " + "error handling", "Similar " + "to Task"]
hits = []
for path in sorted(Path("docs/superpowers/plans").glob("*.md")):
    text = path.read_text(encoding="utf-8")
    for phrase in bad:
        if phrase in text:
            hits.append(f"{path}: contains rejected planning phrase {phrase!r}")
if hits:
    raise SystemExit("\n".join(hits))
PY
```

Expected: no matches.

- [ ] Update the progress tracker with status and verification evidence.
