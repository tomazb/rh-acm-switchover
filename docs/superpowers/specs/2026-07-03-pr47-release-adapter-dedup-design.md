# PR 47 Design: Shared Stream-Subprocess Execution for Release Adapters (R2-M6)

**Date:** 2026-07-03
**Finding:** `R2-M6` from
[`docs/plans/2026-07-02-thermos-ansible-review-2-findings.md`](../../plans/2026-07-02-thermos-ansible-review-2-findings.md)
**Tracker row:** PR 47 in [`thermos-resolution-plan.md`](../../../thermos-resolution-plan.md)
**Branch:** `refactor/thermos-47-release-adapter-dedup`

## Problem

Verified at `ansible` @ `947dd1ca`: the `execute()` methods of
`tests/release/adapters/ansible.py:178-306`, `bash.py:76-203`, and
`python_cli.py:167-295` re-implement the same ~120-line flow nearly
line-for-line: mkdir scenario dir → `subprocess.run` (text, capture,
no-check, 3600s default timeout) → `TimeoutExpired` branch writing partial
captures via `write_capture_artifact`, a timeout `exit-code` assertion and
a conditional `artifact-redaction` assertion → success branch writing
captures, an exit-code assertion, the same conditional redaction assertion
→ `StreamResult` assembly. The private `_now()`/`_decode()` helpers are
also copy-pasted into all three modules. `common.py` already defines the
right contract (`StreamResult`/`AssertionRecord`/`ReportArtifact`,
`StreamAdapter` Protocol) but carries no execution logic.

Actual per-adapter variance (byte-diffed):

| Aspect | ansible | bash | python_cli |
| --- | --- | --- | --- |
| `stream` | `"ansible"` | `"bash"` | `"python"` |
| assertion `capability` | `scenario_id` | `f"bash-{scenario_id}"` | `scenario_id` |
| timeout message | `Ansible command timed out after {t} seconds` | `Bash script timed out after {t} seconds` | `Python CLI timed out after {t} seconds` |
| success message | `Ansible command completed` | `Bash script completed` | `Python CLI exited with expected code` |
| failure message | `Ansible command returned a non-zero exit code` | `Bash script returned a non-zero exit code` | `Python CLI returned a non-zero exit code` |
| `reports` | `discover_reports(...)` | `[]` | `discover_reports(...)` |
| `env` kwarg | always `self._build_env(env)` | only set when extra env given (else inherit) | always `self._build_env(scenario_id, env)` |

## Approaches considered

1. **`run_stream_subprocess(...)` helper in `common.py` (chosen)** — owns
   the whole flow; adapters keep command/env construction and pass the
   variance as parameters (stream, capability, three message strings, a
   `reports` callable, optional env). Each `execute()` shrinks to a
   single call. Matches the finding's suggested fix and the existing
   contract file's role.
2. **Template-method base class** — inheritance where a function
   suffices; the adapters are frozen dataclasses and the Protocol is the
   deliberate abstraction. Rejected.
3. **Only extract `_now`/`_decode`** — leaves the 3× ~110-line flow.
   Rejected.

## Design

Add to `tests/release/adapters/common.py`:

```python
DEFAULT_STREAM_COMMAND_TIMEOUT_SECONDS = 3600
_REDACTION_REJECTED_MESSAGE = "Captured output was rejected by the sanitizer"


def _now() -> str: ...            # moved from the adapters
def _decode(data) -> str: ...     # moved from the adapters


def run_stream_subprocess(
    *,
    stream: str,
    scenario_id: str,
    command: list[str],
    cwd: Path,
    artifact_dir: Path,
    scenario_dir: Path,
    capability: str,
    timeout_message_template: str,   # must contain "{timeout}"
    success_message: str,
    failure_message: str,
    timeout_seconds: int | None = None,
    env: Mapping[str, str] | None = None,      # None -> inherit os.environ
    reports: Callable[[], list[ReportArtifact]] | None = None,
) -> StreamResult:
```

Behavior (all byte-identical to today):

- `scenario_dir.mkdir(parents=True, exist_ok=True)`;
  `stdout_path`/`stderr_path` under `scenario_dir`.
- `effective_timeout = timeout_seconds or DEFAULT_STREAM_COMMAND_TIMEOUT_SECONDS`;
  `subprocess.run(command, cwd=cwd, text=True, capture_output=True,
  check=False, timeout=effective_timeout, **({"env": dict(env)} if env is
  not None else {}))`.
- Timeout branch: write both captures from the exception's partial output,
  emit the failed `exit-code` assertion
  (`actual="timeout"`, message = template formatted with the effective
  timeout, `evidence_path=stderr`), append the redaction assertion when a
  capture was rejected, `returncode=-1`, `status="failed"`.
- Normal branch: write both captures, `status` from returncode, exit-code
  assertion (`evidence_path` = stdout on pass / stderr on fail, message =
  success/failure string), redaction assertion + forced `failed` when a
  capture was rejected.
- `reports() if reports else []` evaluated when building each
  `StreamResult` (both branches), preserving today's fresh
  `discover_reports` calls.

Adapter `execute()` bodies collapse to building `command` (+ env where
applicable) and calling the helper:

- ansible: `env=self._build_env(env)`, `capability=scenario_id`, ansible
  message strings, `reports=lambda: self.discover_reports(scenario_id)`.
- bash: `env=self._build_env(env) if env else None` (preserves the
  inherit-environment behavior), `capability=f"bash-{scenario_id}"`, bash
  message strings, `reports=None`.
- python_cli: `env=self._build_env(scenario_id, env)`,
  `capability=scenario_id`, python message strings,
  `reports=lambda: self.discover_reports(scenario_id)`.

Duplicated `_now`/`_decode` and the bash `_BASH_COMMAND_TIMEOUT_SECONDS`
constant are removed from the adapters (bash's 3600 equals the shared
default).

## Testing

Existing `test_ansible.py`/`test_bash.py`/`test_python_cli.py` assert on
`StreamResult` fields, not implementation — they characterize all three
adapters. Red-first addition in `test_common.py`: direct
`run_stream_subprocess` tests for (a) success path (echo command → passed,
exit-code assertion, captures written), (b) failure path (non-zero exit),
(c) timeout path (sleep with sub-second timeout → `returncode=-1`,
`actual="timeout"`, formatted message), (d) reports callable passthrough.

## Acceptance criteria

1. One subprocess/timeout/artifact flow in `adapters/`; each adapter's
   `execute()` is a single helper call; `_now`/`_decode` exist only in
   `common.py`.
2. New helper tests pass; the three adapter suites pass unchanged.
3. Touched-file `black`/`isort`/flake8, `git diff --check` clean; full
   `./run_tests.sh` passes.

## Assumptions (autonomous session)

Operator pre-approved via the tracker queue (rows 44-47 parallel-safe,
release-tooling scope). The bash env-inheritance quirk is preserved
verbatim rather than normalized — changing it would be a behavior change
outside this finding.
