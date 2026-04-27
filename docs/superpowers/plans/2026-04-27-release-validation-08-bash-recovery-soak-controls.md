# Release Validation Bash Adapter, Recovery, And Soak Controls Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Bash stream validation, bounded recovery vocabulary, recovery budget tracking, and soak/cycle aggregation.

**Architecture:** Add `tests/release/adapters/bash.py` for script execution, `tests/release/baseline/recovery.py` for bounded recovery records, and `tests/release/scenarios/soak.py` for fail-closed cycle aggregation. Recovery never performs cleanup unless the profile allows the resource class.

**Tech Stack:** Python dataclasses, subprocess, pathlib, datetime, pytest monkeypatch.

---

## File Map

- Create: `tests/release/adapters/bash.py`
- Create: `tests/release/adapters/test_bash.py`
- Create: `tests/release/baseline/recovery.py`
- Create: `tests/release/baseline/test_recovery.py`
- Create: `tests/release/scenarios/soak.py`
- Create: `tests/release/scenarios/test_soak.py`
- Modify: `tests/release/test_release_certification.py`
- Modify: `docs/superpowers/plans/2026-04-27-release-validation-progress.md`

## Task 1: Bash Adapter Command Construction And Execution

**Files:**
- Create: `tests/release/adapters/bash.py`
- Create: `tests/release/adapters/test_bash.py`

- [ ] **Step 1: Add Bash adapter tests**

```python
from pathlib import Path
import subprocess

from tests.release.adapters.bash import BashAdapter


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
```

- [ ] **Step 2: Run Bash adapter tests and confirm they fail**

Run: `python -m pytest tests/release/adapters/test_bash.py -q`

Expected: import failure.

- [ ] **Step 3: Implement Bash adapter**

```python
# tests/release/adapters/bash.py
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .common import AssertionRecord, StreamResult


SCRIPT_BY_SCENARIO = {
    "preflight": "scripts/preflight-check.sh",
    "bash-discovery": "scripts/discover-hub.sh",
    "bash-postflight": "scripts/postflight-check.sh",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class BashAdapter:
    repo_root: Path
    primary_context: str
    secondary_context: str
    primary_kubeconfig: str
    secondary_kubeconfig: str
    artifact_dir: Path

    def scenario_dir(self, scenario_id: str) -> Path:
        return self.artifact_dir / "scenarios" / scenario_id / "bash"

    def build_command(self, scenario_id: str) -> list[str]:
        script = SCRIPT_BY_SCENARIO.get(scenario_id, "scripts/preflight-check.sh")
        return [
            script,
            "--primary-context",
            self.primary_context,
            "--secondary-context",
            self.secondary_context,
            "--primary-kubeconfig",
            self.primary_kubeconfig,
            "--secondary-kubeconfig",
            self.secondary_kubeconfig,
        ]

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
            stream="bash",
            scenario_id=scenario_id,
            status=status,
            command=command,
            returncode=completed.returncode,
            stdout_path=str(stdout_path),
            stderr_path=str(stderr_path),
            reports=[],
            assertions=[AssertionRecord(f"bash-{scenario_id}", "exit-code", status, "0", str(completed.returncode), str(stdout_path), "Bash script completed")],
            started_at=started_at,
            ended_at=ended_at,
        )
```

- [ ] **Step 4: Run Bash adapter tests**

Run: `python -m pytest tests/release/adapters/test_bash.py -q`

Expected: `2 passed`.

- [ ] **Step 5: Commit Bash adapter**

```bash
git add tests/release/adapters/bash.py tests/release/adapters/test_bash.py
git commit -m "feat: add Bash release adapter"
```

## Task 2: Recovery Budget And Attempt Records

**Files:**
- Create: `tests/release/baseline/recovery.py`
- Create: `tests/release/baseline/test_recovery.py`

- [ ] **Step 1: Add recovery budget tests**

```python
from tests.release.baseline.recovery import RecoveryBudget, RecoveryPolicy


def test_recovery_budget_records_allowed_action() -> None:
    budget = RecoveryBudget(policy=RecoveryPolicy(total_budget_minutes=30, allowed_resources=("Restore",), rbac_actions=("revalidate",)))

    attempt = budget.record_attempt(action="cleanup", scope="secondary", resource="Restore", status="passed", evidence_paths=("recovery.json",))

    assert attempt.allowed_by_profile is True
    assert budget.to_artifact()["status"] == "passed"


def test_recovery_budget_rejects_disallowed_resource() -> None:
    budget = RecoveryBudget(policy=RecoveryPolicy(total_budget_minutes=30, allowed_resources=(), rbac_actions=("revalidate",)))

    attempt = budget.record_attempt(action="cleanup", scope="secondary", resource="BackupSchedule", status="skipped", evidence_paths=())

    assert attempt.allowed_by_profile is False
```

- [ ] **Step 2: Run recovery tests and confirm they fail**

Run: `python -m pytest tests/release/baseline/test_recovery.py -q`

Expected: import failure.

- [ ] **Step 3: Implement recovery records**

```python
# tests/release/baseline/recovery.py
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone


@dataclass(frozen=True)
class RecoveryPolicy:
    total_budget_minutes: int
    allowed_resources: tuple[str, ...]
    rbac_actions: tuple[str, ...]


@dataclass(frozen=True)
class RecoveryAttempt:
    action: str
    scope: str
    resource: str | None
    started_at: str
    ended_at: str
    status: str
    allowed_by_profile: bool
    evidence_paths: tuple[str, ...]

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["evidence_paths"] = list(self.evidence_paths)
        return payload


@dataclass
class RecoveryBudget:
    policy: RecoveryPolicy
    attempts: list[RecoveryAttempt] = field(default_factory=list)
    hard_stops: list[dict] = field(default_factory=list)

    def record_attempt(self, *, action: str, scope: str, resource: str | None, status: str, evidence_paths: tuple[str, ...]) -> RecoveryAttempt:
        now = datetime.now(timezone.utc).isoformat()
        allowed = resource is None or resource in self.policy.allowed_resources
        attempt = RecoveryAttempt(action, scope, resource, now, now, status, allowed, evidence_paths)
        self.attempts.append(attempt)
        return attempt

    def to_artifact(self) -> dict:
        failed = any(item.status == "failed" for item in self.attempts) or bool(self.hard_stops)
        return {
            "schema_version": 1,
            "budget_minutes": self.policy.total_budget_minutes,
            "budget_consumed_seconds": 0,
            "pre_run": [item.to_dict() for item in self.attempts],
            "post_failure": [],
            "hard_stops": self.hard_stops,
            "status": "failed" if failed else "passed",
        }
```

- [ ] **Step 4: Run recovery tests**

Run: `python -m pytest tests/release/baseline/test_recovery.py -q`

Expected: `2 passed`.

- [ ] **Step 5: Commit recovery records**

```bash
git add tests/release/baseline/recovery.py tests/release/baseline/test_recovery.py
git commit -m "feat: track release recovery budget"
```

## Task 3: Recovery Safety Actions

**Files:**
- Modify: `tests/release/baseline/recovery.py`
- Modify: `tests/release/baseline/test_recovery.py`

- [ ] **Step 1: Add action safety tests**

```python
from tests.release.baseline.recovery import plan_recovery_actions


def test_plan_recovery_actions_allows_only_profile_resources() -> None:
    actions = plan_recovery_actions(
        drift={"stale_restore": True, "stale_backup_schedule": True},
        policy=RecoveryPolicy(total_budget_minutes=30, allowed_resources=("Restore",), rbac_actions=("revalidate",)),
    )

    assert [item["resource"] for item in actions if item["allowed_by_profile"]] == ["Restore"]
    assert any(item["resource"] == "BackupSchedule" and not item["allowed_by_profile"] for item in actions)
```

- [ ] **Step 2: Run action safety test and confirm it fails**

Run: `python -m pytest tests/release/baseline/test_recovery.py::test_plan_recovery_actions_allows_only_profile_resources -q`

Expected: import failure for missing function.

- [ ] **Step 3: Implement recovery action planner**

```python
def plan_recovery_actions(*, drift: dict, policy: RecoveryPolicy) -> list[dict]:
    candidates = []
    if drift.get("stale_restore"):
        candidates.append({"action": "cleanup", "resource": "Restore"})
    if drift.get("stale_backup_schedule"):
        candidates.append({"action": "cleanup", "resource": "BackupSchedule"})
    planned = []
    for candidate in candidates:
        planned.append({**candidate, "allowed_by_profile": candidate["resource"] in policy.allowed_resources})
    return planned
```

- [ ] **Step 4: Run action safety test**

Run: `python -m pytest tests/release/baseline/test_recovery.py::test_plan_recovery_actions_allows_only_profile_resources -q`

Expected: `1 passed`.

- [ ] **Step 5: Commit recovery planner**

```bash
git add tests/release/baseline/recovery.py tests/release/baseline/test_recovery.py
git commit -m "feat: plan bounded release recovery actions"
```

## Task 4: Soak Aggregation

**Files:**
- Create: `tests/release/scenarios/soak.py`
- Create: `tests/release/scenarios/test_soak.py`

- [ ] **Step 1: Add soak aggregation tests**

```python
from tests.release.scenarios.soak import aggregate_soak_results


def test_soak_aggregation_fails_when_any_required_cycle_fails() -> None:
    result = aggregate_soak_results(
        [
            {"scenario_id": "soak/cycle-1/python", "status": "passed", "required": True},
            {"scenario_id": "soak/cycle-2/python", "status": "failed", "required": True},
        ]
    )

    assert result["status"] == "failed"
    assert result["failed_cycles"] == ["soak/cycle-2/python"]


def test_soak_aggregation_passes_all_required_cycles() -> None:
    result = aggregate_soak_results(
        [
            {"scenario_id": "soak/cycle-1/python", "status": "passed", "required": True},
            {"scenario_id": "soak/cycle-1/ansible", "status": "passed", "required": True},
        ]
    )

    assert result["status"] == "passed"
```

- [ ] **Step 2: Run soak tests and confirm they fail**

Run: `python -m pytest tests/release/scenarios/test_soak.py -q`

Expected: import failure.

- [ ] **Step 3: Implement soak aggregation**

```python
# tests/release/scenarios/soak.py
from __future__ import annotations


def aggregate_soak_results(cycle_results: list[dict]) -> dict:
    failed_cycles = [
        item["scenario_id"]
        for item in cycle_results
        if item.get("required", True) and item.get("status") not in {"passed", "not_applicable"}
    ]
    return {
        "scenario_id": "soak",
        "status": "failed" if failed_cycles else "passed",
        "failed_cycles": failed_cycles,
        "cycle_count": len(cycle_results),
    }
```

- [ ] **Step 4: Run soak tests**

Run: `python -m pytest tests/release/scenarios/test_soak.py -q`

Expected: `2 passed`.

- [ ] **Step 5: Commit soak aggregation**

```bash
git add tests/release/scenarios/soak.py tests/release/scenarios/test_soak.py
git commit -m "feat: aggregate release soak cycles"
```

## Task 5: Lifecycle Wiring

**Files:**
- Modify: `tests/release/test_release_certification.py`
- Modify: `tests/release/adapters/test_bash.py`

- [ ] **Step 1: Add Bash scenario helper test**

```python
from tests.release.test_release_certification import execute_bash_scenarios


class FakeBashAdapter:
    def execute(self, scenario_id: str):
        return {"scenario_id": scenario_id, "stream": "bash", "status": "passed"}


def test_execute_bash_scenarios_runs_only_bash_supported_ids() -> None:
    results = execute_bash_scenarios(adapter=FakeBashAdapter(), scenario_ids=("preflight", "python-passive-switchover"))

    assert [item["scenario_id"] for item in results] == ["preflight"]
```

- [ ] **Step 2: Run helper test and confirm it fails**

Run: `python -m pytest tests/release/adapters/test_bash.py::test_execute_bash_scenarios_runs_only_bash_supported_ids -q`

Expected: import failure for missing helper.

- [ ] **Step 3: Implement Bash helper**

```python
# tests/release/test_release_certification.py
BASH_SCENARIOS = {"preflight", "bash-discovery", "bash-postflight"}


def execute_bash_scenarios(*, adapter, scenario_ids: tuple[str, ...]) -> list:
    results = []
    for scenario_id in scenario_ids:
        if scenario_id in BASH_SCENARIOS:
            result = adapter.execute(scenario_id)
            results.append(result.to_dict() if hasattr(result, "to_dict") else result)
    return results
```

- [ ] **Step 4: Run helper test**

Run: `python -m pytest tests/release/adapters/test_bash.py::test_execute_bash_scenarios_runs_only_bash_supported_ids -q`

Expected: `1 passed`.

- [ ] **Step 5: Commit lifecycle wiring**

```bash
git add tests/release/test_release_certification.py tests/release/adapters/test_bash.py
git commit -m "feat: wire Bash release scenarios"
```

## Final Verification

- [ ] Run Bash, recovery, and soak tests:

Run: `python -m pytest tests/release/adapters/test_bash.py tests/release/baseline/test_recovery.py tests/release/scenarios/test_soak.py -q`

Expected: all selected tests pass.

- [ ] Run all release unit tests that do not need a live profile:

Run: `python -m pytest tests/release -m "not release" -q`

Expected: all non-lifecycle release helper tests pass.

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
