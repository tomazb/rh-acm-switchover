# Release Validation Summary Reporting And Operator Docs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add final summary aggregation, human-readable release report rendering, operator documentation, and final verification for the release validation framework.

**Architecture:** Put aggregation in `tests/release/reporting/summary.py`, markdown rendering in `tests/release/reporting/render.py`, and operator-facing usage docs in `docs/ansible-collection/` or `docs/development/` without touching protected runbook/SKILL files. Aggregation is fail-closed and mirrors the source design's final rules.

**Tech Stack:** Python dataclasses/dicts, json, markdown rendering by string templates, pytest.

---

## File Map

- Create: `tests/release/reporting/summary.py`
- Create: `tests/release/reporting/render.py`
- Create: `tests/release/reporting/test_summary.py`
- Create: `tests/release/reporting/test_render.py`
- Create: `docs/development/release-validation-framework.md`
- Modify: `tests/release/test_release_certification.py`
- Modify: `CHANGELOG.md` only if the release framework is included in user-facing release notes for the current branch.
- Modify: `docs/superpowers/plans/2026-04-27-release-validation-progress.md`

## Task 1: Final Summary Aggregation

**Files:**
- Create: `tests/release/reporting/summary.py`
- Create: `tests/release/reporting/test_summary.py`

- [ ] **Step 1: Add fail-closed aggregation tests**

```python
from tests.release.reporting.summary import build_summary


def test_summary_passes_only_when_required_gates_pass() -> None:
    summary = build_summary(
        release_mode="certification",
        certification_eligible=True,
        required_scenarios=[{"scenario_id": "preflight", "status": "passed"}],
        optional_scenarios=[],
        runtime_parity={"status": "passed"},
        artifact_redaction={"status": "passed"},
        final_baseline={"status": "passed"},
        recovery={"status": "passed"},
        mandatory_argocd={"status": "passed"},
        release_metadata={"status": "passed"},
    )

    assert summary["status"] == "passed"
    assert summary["certification_eligible"] is True


def test_summary_fails_dirty_or_non_certification_run() -> None:
    summary = build_summary(
        release_mode="debug",
        certification_eligible=False,
        required_scenarios=[{"scenario_id": "preflight", "status": "passed"}],
        optional_scenarios=[],
        runtime_parity={"status": "passed"},
        artifact_redaction={"status": "passed"},
        final_baseline={"status": "passed"},
        recovery={"status": "passed"},
        mandatory_argocd={"status": "passed"},
        release_metadata={"status": "passed"},
    )

    assert summary["status"] == "failed"
    assert "release mode is not certification" in summary["failure_reasons"]
```

- [ ] **Step 2: Run summary tests and confirm they fail**

Run: `python -m pytest tests/release/reporting/test_summary.py -q`

Expected: import failure.

- [ ] **Step 3: Implement summary aggregation**

```python
# tests/release/reporting/summary.py
from __future__ import annotations


def _failed_required_scenarios(required_scenarios: list[dict]) -> list[str]:
    return [item["scenario_id"] for item in required_scenarios if item.get("status") not in {"passed", "not_applicable"}]


def build_summary(
    *,
    release_mode: str,
    certification_eligible: bool,
    required_scenarios: list[dict],
    optional_scenarios: list[dict],
    runtime_parity: dict,
    artifact_redaction: dict,
    final_baseline: dict,
    recovery: dict,
    mandatory_argocd: dict,
    release_metadata: dict,
) -> dict:
    failure_reasons: list[str] = []
    if release_mode != "certification":
        failure_reasons.append("release mode is not certification")
    if not certification_eligible:
        failure_reasons.append("run is not certification eligible")
    for scenario_id in _failed_required_scenarios(required_scenarios):
        failure_reasons.append(f"required scenario failed: {scenario_id}")
    for name, payload in {
        "runtime parity": runtime_parity,
        "artifact redaction": artifact_redaction,
        "final baseline": final_baseline,
        "mandatory Argo CD": mandatory_argocd,
        "release metadata": release_metadata,
    }.items():
        if payload.get("status") != "passed":
            failure_reasons.append(f"{name} failed")
    if recovery.get("hard_stops"):
        failure_reasons.append("recovery hard stop remains open")
    return {
        "schema_version": 1,
        "status": "passed" if not failure_reasons else "failed",
        "certification_eligible": certification_eligible and not failure_reasons,
        "release_mode": release_mode,
        "required_scenarios": required_scenarios,
        "optional_scenarios": optional_scenarios,
        "mandatory_argocd": mandatory_argocd,
        "release_metadata": release_metadata,
        "runtime_parity": runtime_parity,
        "artifact_redaction": artifact_redaction,
        "final_baseline": final_baseline,
        "recovery": recovery,
        "warnings": [],
        "failure_reasons": failure_reasons,
    }
```

- [ ] **Step 4: Run summary tests**

Run: `python -m pytest tests/release/reporting/test_summary.py -q`

Expected: `2 passed`.

- [ ] **Step 5: Commit summary aggregation**

```bash
git add tests/release/reporting/summary.py tests/release/reporting/test_summary.py
git commit -m "feat: aggregate release validation summary"
```

## Task 2: Markdown Report Rendering

**Files:**
- Create: `tests/release/reporting/render.py`
- Create: `tests/release/reporting/test_render.py`

- [ ] **Step 1: Add render tests**

```python
from tests.release.reporting.render import render_release_report


def test_render_release_report_contains_required_sections() -> None:
    report = render_release_report(
        {
            "status": "failed",
            "release_mode": "certification",
            "certification_eligible": False,
            "required_scenarios": [{"scenario_id": "preflight", "status": "failed"}],
            "optional_scenarios": [],
            "mandatory_argocd": {"status": "passed"},
            "runtime_parity": {"status": "failed"},
            "artifact_redaction": {"status": "passed"},
            "final_baseline": {"status": "passed"},
            "recovery": {"status": "passed"},
            "release_metadata": {"status": "passed"},
            "failure_reasons": ["required scenario failed: preflight"],
            "warnings": [],
        },
        manifest={"run_id": "run-1", "profile": {"name": "lab"}},
    )

    assert "## Run Identity" in report
    assert "## Runtime Parity Summary" in report
    assert "required scenario failed: preflight" in report
    assert "NO-GO" in report
```

- [ ] **Step 2: Run render test and confirm it fails**

Run: `python -m pytest tests/release/reporting/test_render.py -q`

Expected: import failure.

- [ ] **Step 3: Implement renderer**

```python
# tests/release/reporting/render.py
from __future__ import annotations


def _lines_for_scenarios(items: list[dict]) -> list[str]:
    return [f"- `{item['scenario_id']}`: `{item.get('status', 'unknown')}`" for item in items]


def render_release_report(summary: dict, manifest: dict) -> str:
    decision = "GO" if summary.get("status") == "passed" and summary.get("certification_eligible") else "NO-GO"
    lines = [
        "# Release Validation Report",
        "",
        "## Run Identity",
        f"- Run ID: `{manifest.get('run_id', 'unknown')}`",
        f"- Profile: `{manifest.get('profile', {}).get('name', 'unknown')}`",
        f"- Mode: `{summary.get('release_mode', 'unknown')}`",
        "",
        "## Release Metadata Consistency",
        f"- Status: `{summary.get('release_metadata', {}).get('status', 'unknown')}`",
        "",
        "## Required Scenario Results",
        *_lines_for_scenarios(summary.get("required_scenarios", [])),
        "",
        "## Optional Scenario Results",
        *_lines_for_scenarios(summary.get("optional_scenarios", [])),
        "",
        "## Mandatory Argo CD Certification",
        f"- Status: `{summary.get('mandatory_argocd', {}).get('status', 'unknown')}`",
        "",
        "## Runtime Parity Summary",
        f"- Status: `{summary.get('runtime_parity', {}).get('status', 'unknown')}`",
        "",
        "## Recovery Summary",
        f"- Status: `{summary.get('recovery', {}).get('status', 'unknown')}`",
        "",
        "## Artifact Redaction Summary",
        f"- Status: `{summary.get('artifact_redaction', {}).get('status', 'unknown')}`",
        "",
        "## Final Baseline Result",
        f"- Status: `{summary.get('final_baseline', {}).get('status', 'unknown')}`",
        "",
        "## Final Go/No-Go Decision",
        f"- Decision: **{decision}**",
    ]
    if summary.get("failure_reasons"):
        lines.extend(["", "## Failure Reasons", *[f"- {reason}" for reason in summary["failure_reasons"]]])
    if summary.get("warnings"):
        lines.extend(["", "## Warnings", *[f"- {warning}" for warning in summary["warnings"]]])
    return "\n".join(lines) + "\n"
```

- [ ] **Step 4: Run render tests**

Run: `python -m pytest tests/release/reporting/test_render.py -q`

Expected: `1 passed`.

- [ ] **Step 5: Commit markdown renderer**

```bash
git add tests/release/reporting/render.py tests/release/reporting/test_render.py
git commit -m "feat: render release validation report"
```

## Task 3: Lifecycle Summary Wiring

**Files:**
- Modify: `tests/release/test_release_certification.py`
- Modify: `tests/release/reporting/test_summary.py`

- [ ] **Step 1: Add lifecycle summary helper test**

```python
from tests.release.test_release_certification import finalize_release_artifacts


class FakeArtifacts:
    def __init__(self):
        self.writes = {}

    def write_json(self, relative_path, payload):
        self.writes[relative_path] = payload

    @property
    def run_dir(self):
        return Path("/tmp/run")


def test_finalize_release_artifacts_writes_summary_and_report() -> None:
    artifacts = FakeArtifacts()

    finalize_release_artifacts(
        artifacts=artifacts,
        manifest={"run_id": "run-1", "profile": {"name": "lab"}},
        summary_inputs={
            "release_mode": "certification",
            "certification_eligible": True,
            "required_scenarios": [{"scenario_id": "preflight", "status": "passed"}],
            "optional_scenarios": [],
            "runtime_parity": {"status": "passed"},
            "artifact_redaction": {"status": "passed"},
            "final_baseline": {"status": "passed"},
            "recovery": {"status": "passed"},
            "mandatory_argocd": {"status": "passed"},
            "release_metadata": {"status": "passed"},
        },
    )

    assert artifacts.writes["summary.json"]["status"] == "passed"
```

- [ ] **Step 2: Run helper test and confirm it fails**

Run: `python -m pytest tests/release/reporting/test_summary.py::test_finalize_release_artifacts_writes_summary_and_report -q`

Expected: import failure for missing helper.

- [ ] **Step 3: Implement finalization helper**

```python
# tests/release/test_release_certification.py
from tests.release.reporting.render import render_release_report
from tests.release.reporting.summary import build_summary


def finalize_release_artifacts(*, artifacts, manifest: dict, summary_inputs: dict) -> dict:
    summary = build_summary(**summary_inputs)
    artifacts.write_json("summary.json", summary)
    report = render_release_report(summary, manifest)
    report_path = artifacts.run_dir / "release-report.md"
    report_path.write_text(report, encoding="utf-8")
    return summary
```

- [ ] **Step 4: Run helper test**

Run: `python -m pytest tests/release/reporting/test_summary.py::test_finalize_release_artifacts_writes_summary_and_report -q`

Expected: `1 passed`.

- [ ] **Step 5: Commit finalization wiring**

```bash
git add tests/release/test_release_certification.py tests/release/reporting/test_summary.py
git commit -m "feat: finalize release validation artifacts"
```

## Task 4: Operator Documentation

**Files:**
- Create: `docs/development/release-validation-framework.md`
- Modify: `tests/release/reporting/test_render.py`

- [ ] **Step 1: Add documentation existence test**

```python
def test_release_validation_operator_doc_mentions_profile_and_modes() -> None:
    doc = Path("docs/development/release-validation-framework.md").read_text(encoding="utf-8")

    assert "--release-profile" in doc
    assert "certification" in doc
    assert "focused-rerun" in doc
    assert "debug" in doc
    assert "release-report.md" in doc
```

- [ ] **Step 2: Run doc test and confirm it fails**

Run: `python -m pytest tests/release/reporting/test_render.py::test_release_validation_operator_doc_mentions_profile_and_modes -q`

Expected: file not found.

- [ ] **Step 3: Write operator documentation**

Create `docs/development/release-validation-framework.md` with these sections:

- Overview of the release validation framework and why it is separate from ordinary E2E.
- Profile requirements and the checked-in example profile locations.
- Invocation examples for `certification`, `focused-rerun`, and `debug`.
- Artifact outputs: `manifest.json`, `scenario-results.json`, `runtime-parity.json`, `recovery.json`, `redaction.json`, `summary.json`, and `release-report.md`.
- Safety notes: clean checkout requirement, explicit profile requirement, no protected runbook edits, and redaction fail-closed behavior.

- [ ] **Step 4: Run doc test**

Run: `python -m pytest tests/release/reporting/test_render.py::test_release_validation_operator_doc_mentions_profile_and_modes -q`

Expected: `1 passed`.

- [ ] **Step 5: Commit docs**

```bash
git add docs/development/release-validation-framework.md tests/release/reporting/test_render.py
git commit -m "docs: describe release validation workflow"
```

## Task 5: Changelog And Final Cross-Plan Verification

**Files:**
- Modify: `CHANGELOG.md` only when this framework is part of the branch's operator-facing changes.
- Modify: `docs/superpowers/plans/2026-04-27-release-validation-progress.md`

- [ ] **Step 1: Decide whether a changelog entry is required**

Read `CHANGELOG.md`. If the release framework is now operator-facing in this branch, add an `[Unreleased]` entry under `### Added`:

```markdown
- Added a pytest-native release validation framework for profile-driven ACM switchover certification across Python, Ansible, and Bash surfaces.
```

If the framework remains internal-only and hidden behind explicit developer invocation, record that decision in the progress tracker and leave `CHANGELOG.md` unchanged.

- [ ] **Step 2: Run reporting tests**

Run: `python -m pytest tests/release/reporting/test_summary.py tests/release/reporting/test_render.py -q`

Expected: all reporting tests pass.

- [ ] **Step 3: Run full release helper test suite without live profile**

Run: `python -m pytest tests/release -m "not release" -q`

Expected: all non-lifecycle release helper tests pass.

- [ ] **Step 4: Run normal test guard**

Run: `python -m pytest tests/ -m "not e2e and not release" -q`

Expected: root non-E2E tests pass or any failures are recorded as unrelated pre-existing issues with full command output.

- [ ] **Step 5: Run the planning placeholder scan**

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

- [ ] **Step 6: Update progress tracker**

Set all completed plan rows to `verified`, add the verification commands and outcomes, and record the final branch/commit identifiers.

## Final Verification

- [ ] Run summary and render tests:

Run: `python -m pytest tests/release/reporting/test_summary.py tests/release/reporting/test_render.py -q`

Expected: all selected tests pass.

- [ ] Run aggregate release helper tests:

Run: `python -m pytest tests/release -m "not release" -q`

Expected: all release helper tests pass without a live profile.

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
