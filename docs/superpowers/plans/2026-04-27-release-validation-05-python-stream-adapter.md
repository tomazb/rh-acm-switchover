# Release Validation Python Stream Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the Python CLI release stream adapter with isolated state files, stdout/stderr capture, report discovery, and scenario result records.

**Architecture:** Implement a stream-neutral result model in `tests/release/adapters/common.py` and a Python adapter in `tests/release/adapters/python_cli.py`. The adapter builds argv from profile/scenario data, runs `acm_switchover.py`, captures output through the artifact layer, and returns `StreamResult` records for later parity checks.

**Tech Stack:** Python dataclasses, subprocess, pathlib, pytest monkeypatch, release artifact helpers.

---

## File Map

- Create: `tests/release/adapters/__init__.py`
- Create: `tests/release/adapters/common.py`
- Create: `tests/release/adapters/python_cli.py`
- Create: `tests/release/adapters/test_common.py`
- Create: `tests/release/adapters/test_python_cli.py`
- Modify: `tests/release/scenarios/catalog.py`
- Modify: `tests/release/test_release_certification.py`
- Modify: `docs/superpowers/plans/2026-04-27-release-validation-progress.md`

## Task 1: Stream Result Model

**Files:**
- Create: `tests/release/adapters/common.py`
- Create: `tests/release/adapters/test_common.py`
- Create: `tests/release/adapters/__init__.py`

- [ ] **Step 1: Add result serialization tests**

```python
from tests.release.adapters.common import AssertionRecord, ReportArtifact, StreamResult


def test_stream_result_serializes_to_json_ready_dict() -> None:
    result = StreamResult(
        stream="python",
        scenario_id="preflight",
        status="passed",
        command=["python", "acm_switchover.py", "--validate-only"],
        returncode=0,
        stdout_path="scenarios/preflight/stdout.txt",
        stderr_path="scenarios/preflight/stderr.txt",
        reports=[ReportArtifact(type="preflight", path="preflight-report.json", schema_version="1", required=True)],
        assertions=[AssertionRecord(capability="preflight validation", name="exit-code", status="passed", expected="0", actual="0", evidence_path=None, message="command succeeded")],
        started_at="2026-04-27T00:00:00+00:00",
        ended_at="2026-04-27T00:00:01+00:00",
    )

    payload = result.to_dict()

    assert payload["stream"] == "python"
    assert payload["reports"][0]["type"] == "preflight"
    assert payload["assertions"][0]["capability"] == "preflight validation"
```

- [ ] **Step 2: Run common adapter tests and confirm they fail**

Run: `python -m pytest tests/release/adapters/test_common.py -q`

Expected: import failure.

- [ ] **Step 3: Implement stream result dataclasses**

```python
# tests/release/adapters/common.py
from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ReportArtifact:
    type: str
    path: str
    schema_version: str | int | None
    required: bool


@dataclass(frozen=True)
class AssertionRecord:
    capability: str
    name: str
    status: str
    expected: str
    actual: str
    evidence_path: str | None
    message: str


@dataclass(frozen=True)
class StreamResult:
    stream: str
    scenario_id: str
    status: str
    command: list[str]
    returncode: int | None
    stdout_path: str | None
    stderr_path: str | None
    reports: list[ReportArtifact]
    assertions: list[AssertionRecord]
    started_at: str
    ended_at: str

    def to_dict(self) -> dict:
        return asdict(self)
```

```python
# tests/release/adapters/__init__.py
"""Release stream adapters."""
```

- [ ] **Step 4: Run common adapter tests**

Run: `python -m pytest tests/release/adapters/test_common.py -q`

Expected: `1 passed`.

- [ ] **Step 5: Commit stream model**

```bash
git add tests/release/adapters/__init__.py tests/release/adapters/common.py tests/release/adapters/test_common.py
git commit -m "feat: define release stream result model"
```

## Task 2: Python Adapter Command Construction

**Files:**
- Create: `tests/release/adapters/python_cli.py`
- Create: `tests/release/adapters/test_python_cli.py`

- [ ] **Step 1: Add command construction tests**

```python
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
```

- [ ] **Step 2: Run command tests and confirm they fail**

Run: `python -m pytest tests/release/adapters/test_python_cli.py::test_python_preflight_command_uses_profile_contexts tests/release/adapters/test_python_cli.py::test_python_restore_only_command_uses_unique_state_file -q`

Expected: import failure for missing Python adapter.

- [ ] **Step 3: Implement command construction**

```python
# tests/release/adapters/python_cli.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PythonCliAdapter:
    repo_root: Path
    primary_context: str
    secondary_context: str
    primary_kubeconfig: str
    secondary_kubeconfig: str
    artifact_dir: Path

    def scenario_dir(self, scenario_id: str) -> Path:
        return self.artifact_dir / "scenarios" / scenario_id / "python"

    def build_command(self, scenario_id: str) -> list[str]:
        scenario_dir = self.scenario_dir(scenario_id)
        state_file = scenario_dir / "state.json"
        base = [
            "python",
            "acm_switchover.py",
            "--primary-context",
            self.primary_context,
            "--secondary-context",
            self.secondary_context,
            "--primary-kubeconfig",
            self.primary_kubeconfig,
            "--secondary-kubeconfig",
            self.secondary_kubeconfig,
            "--state-file",
            str(state_file),
        ]
        if scenario_id == "preflight":
            return base + ["--validate-only"]
        if scenario_id == "python-restore-only":
            return base + ["--restore-only"]
        if scenario_id == "argocd-managed-switchover":
            return base + ["--argocd-manage"]
        return base
```

- [ ] **Step 4: Run command construction tests**

Run: `python -m pytest tests/release/adapters/test_python_cli.py::test_python_preflight_command_uses_profile_contexts tests/release/adapters/test_python_cli.py::test_python_restore_only_command_uses_unique_state_file -q`

Expected: `2 passed`.

- [ ] **Step 5: Commit command construction**

```bash
git add tests/release/adapters/python_cli.py tests/release/adapters/test_python_cli.py
git commit -m "feat: build Python release adapter commands"
```

## Task 3: Python Adapter Execution And Capture

**Files:**
- Modify: `tests/release/adapters/python_cli.py`
- Modify: `tests/release/adapters/test_python_cli.py`

- [ ] **Step 1: Add execution test with monkeypatched subprocess**

```python
import subprocess


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
```

- [ ] **Step 2: Run execution test and confirm it fails**

Run: `python -m pytest tests/release/adapters/test_python_cli.py::test_python_adapter_execute_captures_stdout_and_stderr -q`

Expected: attribute error for missing `execute()`.

- [ ] **Step 3: Implement execution**

```python
import subprocess
from datetime import datetime, timezone

from .common import AssertionRecord, StreamResult


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
```

Add method:

```python
    def execute(self, scenario_id: str) -> StreamResult:
        scenario_dir = self.scenario_dir(scenario_id)
        scenario_dir.mkdir(parents=True, exist_ok=True)
        command = self.build_command(scenario_id)
        started_at = _now()
        completed = subprocess.run(command, cwd=self.repo_root, text=True, capture_output=True, check=False)
        ended_at = _now()
        stdout_path = scenario_dir / "stdout.txt"
        stderr_path = scenario_dir / "stderr.txt"
        stdout_path.write_text(completed.stdout, encoding="utf-8")
        stderr_path.write_text(completed.stderr, encoding="utf-8")
        status = "passed" if completed.returncode == 0 else "failed"
        return StreamResult(
            stream="python",
            scenario_id=scenario_id,
            status=status,
            command=command,
            returncode=completed.returncode,
            stdout_path=str(stdout_path),
            stderr_path=str(stderr_path),
            reports=[],
            assertions=[
                AssertionRecord(
                    capability=scenario_id,
                    name="exit-code",
                    status=status,
                    expected="0",
                    actual=str(completed.returncode),
                    evidence_path=str(stdout_path),
                    message="Python CLI exited with expected code" if status == "passed" else "Python CLI returned a non-zero exit code",
                )
            ],
            started_at=started_at,
            ended_at=ended_at,
        )
```

- [ ] **Step 4: Run execution test**

Run: `python -m pytest tests/release/adapters/test_python_cli.py::test_python_adapter_execute_captures_stdout_and_stderr -q`

Expected: `1 passed`.

- [ ] **Step 5: Commit execution capture**

```bash
git add tests/release/adapters/python_cli.py tests/release/adapters/test_python_cli.py
git commit -m "feat: execute Python release adapter"
```

## Task 4: Report Discovery

**Files:**
- Modify: `tests/release/adapters/python_cli.py`
- Modify: `tests/release/adapters/test_python_cli.py`

- [ ] **Step 1: Add report discovery test**

```python
def test_python_adapter_discovers_required_reports(tmp_path: Path) -> None:
    adapter = PythonCliAdapter(Path("/repo"), "primary", "secondary", "/kube/primary", "/kube/secondary", tmp_path)
    scenario_dir = adapter.scenario_dir("preflight")
    scenario_dir.mkdir(parents=True)
    (scenario_dir / "preflight-report.json").write_text('{"schema_version": 1, "status": "passed"}', encoding="utf-8")

    reports = adapter.discover_reports("preflight")

    assert reports[0].type == "preflight"
    assert reports[0].required is True
```

- [ ] **Step 2: Run report discovery test and confirm it fails**

Run: `python -m pytest tests/release/adapters/test_python_cli.py::test_python_adapter_discovers_required_reports -q`

Expected: attribute error for missing method.

- [ ] **Step 3: Implement report discovery and attach reports to execution**

```python
import json
from .common import ReportArtifact


REPORT_NAMES = {
    "preflight": ("preflight", "preflight-report.json"),
    "python-passive-switchover": ("switchover", "switchover-report.json"),
    "python-restore-only": ("restore", "restore-only-report.json"),
    "argocd-managed-switchover": ("switchover", "switchover-report.json"),
}
```

Add method:

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
```

In `execute()`, set `reports=self.discover_reports(scenario_id)`.

- [ ] **Step 4: Run Python adapter tests**

Run: `python -m pytest tests/release/adapters/test_python_cli.py -q`

Expected: all Python adapter tests pass.

- [ ] **Step 5: Commit report discovery**

```bash
git add tests/release/adapters/python_cli.py tests/release/adapters/test_python_cli.py
git commit -m "feat: collect Python adapter reports"
```

## Task 5: Scenario Wiring

**Files:**
- Modify: `tests/release/test_release_certification.py`
- Modify: `tests/release/adapters/test_python_cli.py`

- [ ] **Step 1: Add orchestration unit test**

```python
from tests.release.test_release_certification import execute_python_scenarios


class FakePythonAdapter:
    def execute(self, scenario_id: str):
        return {"scenario_id": scenario_id, "stream": "python", "status": "passed"}


def test_execute_python_scenarios_filters_python_ids() -> None:
    results = execute_python_scenarios(
        adapter=FakePythonAdapter(),
        scenario_ids=("preflight", "python-passive-switchover", "ansible-passive-switchover"),
    )

    assert [item["scenario_id"] for item in results] == ["preflight", "python-passive-switchover"]
```

- [ ] **Step 2: Run orchestration test and confirm it fails**

Run: `python -m pytest tests/release/adapters/test_python_cli.py::test_execute_python_scenarios_filters_python_ids -q`

Expected: import or attribute failure.

- [ ] **Step 3: Implement Python scenario helper**

```python
# tests/release/test_release_certification.py
PYTHON_SCENARIOS = {"preflight", "python-passive-switchover", "python-restore-only", "argocd-managed-switchover"}


def execute_python_scenarios(*, adapter, scenario_ids: tuple[str, ...]) -> list:
    results = []
    for scenario_id in scenario_ids:
        if scenario_id in PYTHON_SCENARIOS:
            result = adapter.execute(scenario_id)
            results.append(result.to_dict() if hasattr(result, "to_dict") else result)
    return results
```

Keep `test_release_certification()` guarded from live execution until all adapters and baseline fixtures are available.

- [ ] **Step 4: Run orchestration test**

Run: `python -m pytest tests/release/adapters/test_python_cli.py::test_execute_python_scenarios_filters_python_ids -q`

Expected: `1 passed`.

- [ ] **Step 5: Commit Python scenario wiring**

```bash
git add tests/release/test_release_certification.py tests/release/adapters/test_python_cli.py
git commit -m "feat: wire Python release scenarios"
```

## Final Verification

- [ ] Run Python adapter tests:

Run: `python -m pytest tests/release/adapters/test_common.py tests/release/adapters/test_python_cli.py -q`

Expected: all selected tests pass.

- [ ] Run lifecycle guard:

Run: `python -m pytest tests/release/test_release_certification.py -q`

Expected: lifecycle test does not mutate a lab without an explicit profile and live manager.

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
