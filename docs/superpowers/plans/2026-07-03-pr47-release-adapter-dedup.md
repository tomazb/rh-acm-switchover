# PR 47: Shared Adapter Execution Helper Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the triplicated subprocess/timeout/artifact execution flow from the three release adapters into `run_stream_subprocess(...)` in `adapters/common.py` (R2-M6), byte-identical `StreamResult`s.

**Architecture:** Per the approved design (`docs/superpowers/specs/2026-07-03-pr47-release-adapter-dedup-design.md`): the helper owns mkdir → `subprocess.run` → timeout/normal branches → `write_capture_artifact` pair → exit-code + redaction assertions → `StreamResult`. Adapters keep command/env construction and pass the variance (stream, capability, message strings, reports callable). `_now`/`_decode` move to `common.py`.

**Tech Stack:** Python 3, pytest, black/isort/flake8 (line-length 120).

## Global Constraints

- Byte-identical `StreamResult` output on success, failure, timeout, and redaction-rejected paths for all three adapters; bash's inherit-env-when-no-extra-env behavior preserved.
- `black --line-length 120` / `isort --profile black --line-length 120` on touched files.
- Base branch: `ansible` @ `947dd1ca`; PR branch `refactor/thermos-47-release-adapter-dedup`.

---

### Task 1: Red-first helper tests

**Files:**
- Modify: `tests/release/adapters/test_common.py` (append)

**Interfaces:**
- Consumes (from Task 2): `run_stream_subprocess(*, stream, scenario_id, command, cwd, artifact_dir, scenario_dir, capability, timeout_message_template, success_message, failure_message, timeout_seconds=None, env=None, reports=None) -> StreamResult`.

- [ ] **Step 1: Append the failing tests**

```python
def _run_helper(tmp_path, command, **overrides):
    from tests.release.adapters.common import run_stream_subprocess

    scenario_dir = tmp_path / "scenarios" / "demo" / "stream"
    kwargs = dict(
        stream="demo",
        scenario_id="demo-scenario",
        command=command,
        cwd=tmp_path,
        artifact_dir=tmp_path,
        scenario_dir=scenario_dir,
        capability="demo-capability",
        timeout_message_template="Demo timed out after {timeout} seconds",
        success_message="Demo completed",
        failure_message="Demo returned a non-zero exit code",
    )
    kwargs.update(overrides)
    return run_stream_subprocess(**kwargs), scenario_dir


def test_run_stream_subprocess_success_writes_captures_and_passes(tmp_path):
    result, scenario_dir = _run_helper(tmp_path, ["sh", "-c", "echo out; echo err >&2"])

    assert result.status == "passed"
    assert result.stream == "demo"
    assert result.returncode == 0
    assert result.reports == []
    assert (scenario_dir / "stdout.txt").read_text() == "out\n"
    assert (scenario_dir / "stderr.txt").read_text() == "err\n"
    (assertion,) = result.assertions
    assert assertion.capability == "demo-capability"
    assert assertion.name == "exit-code"
    assert assertion.status == "passed"
    assert assertion.actual == "0"
    assert assertion.message == "Demo completed"
    assert assertion.evidence_path == str(scenario_dir / "stdout.txt")


def test_run_stream_subprocess_failure_uses_stderr_evidence(tmp_path):
    result, scenario_dir = _run_helper(tmp_path, ["sh", "-c", "exit 3"])

    assert result.status == "failed"
    assert result.returncode == 3
    (assertion,) = result.assertions
    assert assertion.status == "failed"
    assert assertion.actual == "3"
    assert assertion.message == "Demo returned a non-zero exit code"
    assert assertion.evidence_path == str(scenario_dir / "stderr.txt")


def test_run_stream_subprocess_timeout_reports_formatted_message(tmp_path):
    result, scenario_dir = _run_helper(tmp_path, ["sleep", "5"], timeout_seconds=1)

    assert result.status == "failed"
    assert result.returncode == -1
    (assertion,) = result.assertions
    assert assertion.actual == "timeout"
    assert assertion.message == "Demo timed out after 1 seconds"
    assert assertion.evidence_path == str(scenario_dir / "stderr.txt")


def test_run_stream_subprocess_evaluates_reports_callable(tmp_path):
    report = ReportArtifact(type="demo", path="p.json", schema_version=1, required=True)
    result, _ = _run_helper(tmp_path, ["true"], reports=lambda: [report])

    assert result.reports == [report]
```

(`ReportArtifact` should already be imported in `test_common.py`; add it to the import if missing.)

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/release/adapters/test_common.py -q -k run_stream_subprocess`
Expected: FAIL — `ImportError: cannot import name 'run_stream_subprocess'`.

- [ ] **Step 3: Commit**

```bash
git add tests/release/adapters/test_common.py
git commit -m "test: add red tests for shared stream subprocess helper"
```

### Task 2: Implement the helper in common.py

**Files:**
- Modify: `tests/release/adapters/common.py`

- [ ] **Step 1: Add imports, constants, helpers**

```python
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from tests.release.reporting.artifacts import write_capture_artifact

DEFAULT_STREAM_COMMAND_TIMEOUT_SECONDS = 3600
_REDACTION_REJECTED_MESSAGE = "Captured output was rejected by the sanitizer"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decode(data: str | bytes | None) -> str:
    """Decode partial subprocess capture, handling bytes or None from TimeoutExpired."""
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return data or ""
```

- [ ] **Step 2: Add `run_stream_subprocess`**

```python
def run_stream_subprocess(
    *,
    stream: str,
    scenario_id: str,
    command: list[str],
    cwd: Path,
    artifact_dir: Path,
    scenario_dir: Path,
    capability: str,
    timeout_message_template: str,
    success_message: str,
    failure_message: str,
    timeout_seconds: int | None = None,
    env: Mapping[str, str] | None = None,
    reports: Callable[[], list[ReportArtifact]] | None = None,
) -> StreamResult:
    """Run a stream command with shared timeout/capture/assertion handling.

    env=None inherits the current process environment (subprocess default);
    reports is re-evaluated when each StreamResult is built.
    """
    scenario_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = scenario_dir / "stdout.txt"
    stderr_path = scenario_dir / "stderr.txt"
    effective_timeout = timeout_seconds or DEFAULT_STREAM_COMMAND_TIMEOUT_SECONDS
    started_at = _now()
    run_kwargs: dict[str, Any] = {
        "cwd": cwd,
        "text": True,
        "capture_output": True,
        "check": False,
        "timeout": effective_timeout,
    }
    if env is not None:
        run_kwargs["env"] = dict(env)

    def _write_captures(stdout_content: str, stderr_content: str) -> bool:
        _, stdout_written = write_capture_artifact(
            run_dir=artifact_dir,
            relative_path=stdout_path.relative_to(artifact_dir),
            content=stdout_content,
            rejected_placeholder="",
        )
        _, stderr_written = write_capture_artifact(
            run_dir=artifact_dir,
            relative_path=stderr_path.relative_to(artifact_dir),
            content=stderr_content,
            rejected_placeholder=_REDACTION_REJECTED_MESSAGE + "\n",
        )
        return stdout_written and stderr_written

    def _redaction_assertion() -> AssertionRecord:
        return AssertionRecord(
            capability=capability,
            name="artifact-redaction",
            status="failed",
            expected="clean",
            actual="rejected",
            evidence_path="",
            message=_REDACTION_REJECTED_MESSAGE,
        )

    def _result(status: str, returncode: int | None, assertions: list[AssertionRecord]) -> StreamResult:
        return StreamResult(
            stream=stream,
            scenario_id=scenario_id,
            status=status,
            command=command,
            returncode=returncode,
            stdout_path=str(stdout_path),
            stderr_path=str(stderr_path),
            reports=reports() if reports else [],
            assertions=assertions,
            started_at=started_at,
            ended_at=_now(),
        )

    try:
        completed = subprocess.run(command, **run_kwargs)
    except subprocess.TimeoutExpired as exc:
        captures_clean = _write_captures(_decode(exc.stdout), _decode(exc.stderr))
        assertions = [
            AssertionRecord(
                capability=capability,
                name="exit-code",
                status="failed",
                expected="0",
                actual="timeout",
                evidence_path=str(stderr_path),
                message=timeout_message_template.format(timeout=effective_timeout),
            )
        ]
        if not captures_clean:
            assertions.append(_redaction_assertion())
        return _result("failed", -1, assertions)

    captures_clean = _write_captures(completed.stdout, completed.stderr)
    status = "passed" if completed.returncode == 0 else "failed"
    assertions = [
        AssertionRecord(
            capability=capability,
            name="exit-code",
            status=status,
            expected="0",
            actual=str(completed.returncode),
            evidence_path=(str(stdout_path) if status == "passed" else str(stderr_path)),
            message=(success_message if status == "passed" else failure_message),
        )
    ]
    if not captures_clean:
        status = "failed"
        assertions.append(_redaction_assertion())
    return _result(status, completed.returncode, assertions)
```

Note: the `ended_at` timestamp is taken inside `_result` (after captures are
written) whereas today it is taken before the capture writes. Both are
wall-clock ISO strings with no consumer asserting ordering relative to
capture writes; if any adapter test pins this, take `ended_at = _now()`
before `_write_captures` and pass it into `_result` instead.

- [ ] **Step 3: Run helper tests**

Run: `python -m pytest tests/release/adapters/test_common.py -q`
Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/release/adapters/common.py
git commit -m "feat: add run_stream_subprocess shared execution helper (R2-M6)"
```

### Task 3: Collapse the three adapters

**Files:**
- Modify: `tests/release/adapters/ansible.py:178-306` (+ delete its `_now`/`_decode`)
- Modify: `tests/release/adapters/bash.py:24-203` (+ delete `_now`/`_decode`/`_BASH_COMMAND_TIMEOUT_SECONDS`)
- Modify: `tests/release/adapters/python_cli.py:29-295` (+ delete `_now`/`_decode`)

- [ ] **Step 1: ansible.py execute()**

```python
    def execute(
        self,
        scenario_id: str,
        *,
        timeout_seconds: int | None = None,
        env: Mapping[str, str] | None = None,
        extra_args: tuple[str, ...] = (),
    ) -> StreamResult:
        return run_stream_subprocess(
            stream="ansible",
            scenario_id=scenario_id,
            command=self.build_command(scenario_id, extra_args=extra_args),
            cwd=self.repo_root,
            artifact_dir=self.artifact_dir,
            scenario_dir=self.scenario_dir(scenario_id),
            capability=scenario_id,
            timeout_message_template="Ansible command timed out after {timeout} seconds",
            success_message="Ansible command completed",
            failure_message="Ansible command returned a non-zero exit code",
            timeout_seconds=timeout_seconds,
            env=self._build_env(env),
            reports=lambda: self.discover_reports(scenario_id),
        )
```

Update its `.common` import to include `run_stream_subprocess`; drop now-unused `subprocess`/`datetime` imports and the local `_now`/`_decode` (keep whatever other code still uses).

- [ ] **Step 2: bash.py execute()**

```python
    def execute(
        self,
        scenario_id: str,
        *,
        timeout_seconds: int | None = None,
        env: Mapping[str, str] | None = None,
        extra_args: tuple[str, ...] = (),
    ) -> StreamResult:
        return run_stream_subprocess(
            stream="bash",
            scenario_id=scenario_id,
            command=self.build_command(scenario_id, extra_args=extra_args),
            cwd=self.repo_root,
            artifact_dir=self.artifact_dir,
            scenario_dir=self.scenario_dir(scenario_id),
            capability=f"bash-{scenario_id}",
            timeout_message_template="Bash script timed out after {timeout} seconds",
            success_message="Bash script completed",
            failure_message="Bash script returned a non-zero exit code",
            timeout_seconds=timeout_seconds,
            env=self._build_env(env) if env else None,
        )
```

- [ ] **Step 3: python_cli.py execute()**

```python
    def execute(
        self,
        scenario_id: str,
        *,
        timeout_seconds: int | None = None,
        env: Mapping[str, str] | None = None,
        extra_args: tuple[str, ...] = (),
    ) -> StreamResult:
        return run_stream_subprocess(
            stream="python",
            scenario_id=scenario_id,
            command=self.build_command(scenario_id, extra_args=extra_args),
            cwd=self.repo_root,
            artifact_dir=self.artifact_dir,
            scenario_dir=self.scenario_dir(scenario_id),
            capability=scenario_id,
            timeout_message_template="Python CLI timed out after {timeout} seconds",
            success_message="Python CLI exited with expected code",
            failure_message="Python CLI returned a non-zero exit code",
            timeout_seconds=timeout_seconds,
            env=self._build_env(scenario_id, env),
            reports=lambda: self.discover_reports(scenario_id),
        )
```

Note the python_cli success message differs from ansible's ("exited with expected code") — keep each adapter's exact strings.

- [ ] **Step 4: Run all adapter + orchestrator suites**

Run: `python -m pytest tests/release/adapters/ tests/release/test_orchestrator.py -q`
Expected: all PASS.

- [ ] **Step 5: Format, lint, commit**

```bash
black --line-length 120 tests/release/adapters/*.py
isort --profile black --line-length 120 tests/release/adapters/*.py
flake8 --max-line-length 120 tests/release/adapters/
git add -A
git commit -m "refactor: route adapter execution through run_stream_subprocess (R2-M6)"
```

### Task 4: Full verification, tracker update, draft PR

**Files:**
- Modify: `thermos-resolution-plan.md` (row PR 47)

- [ ] **Step 1: Full gate** — `./run_tests.sh`, expect PASS, record lane counts.

- [ ] **Step 2: Tracker + push + PR**

```bash
git add thermos-resolution-plan.md
git commit -m "docs: mark Thermos PR 47 ready for review in tracker"
git push -u origin refactor/thermos-47-release-adapter-dedup
gh pr create --draft --base ansible --title "Thermos PR 47: shared stream-subprocess execution for release adapters (R2-M6)" --body "<summary + verification evidence>"
```
