# Release Validation Runtime Parity Normalizers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add runtime parity normalizers and comparison writers for dual-supported Python and Ansible release capabilities.

**Architecture:** Put comparison code under `tests/release/scenarios/runtime_parity.py`. Normalizers consume `StreamResult` records, source reports, and harness discovery facts, then emit stable comparison records into `runtime-parity.json`.

**Tech Stack:** Python dataclasses, json, pathlib, pytest, existing adapter result models.

---

## File Map

- Create: `tests/release/scenarios/runtime_parity.py`
- Create: `tests/release/scenarios/test_runtime_parity.py`
- Modify: `tests/release/reporting/artifacts.py`
- Modify: `tests/release/test_release_certification.py`
- Modify: `docs/superpowers/plans/2026-04-27-release-validation-progress.md`

## Task 1: Comparison Record Model

**Files:**
- Create: `tests/release/scenarios/runtime_parity.py`
- Create: `tests/release/scenarios/test_runtime_parity.py`

- [ ] **Step 1: Add comparison serialization test**

```python
from tests.release.scenarios.runtime_parity import ComparisonRecord


def test_comparison_record_serializes_required_fields() -> None:
    record = ComparisonRecord(
        capability="preflight validation",
        scenario_id="preflight",
        streams=("python", "ansible"),
        status="passed",
        required_fields=("status", "check_ids"),
        differences=[],
        evidence_paths=("scenario-results.json",),
    )

    payload = record.to_dict()

    assert payload["capability"] == "preflight validation"
    assert payload["streams"] == ["python", "ansible"]
    assert payload["required_fields"] == ["status", "check_ids"]
```

- [ ] **Step 2: Run comparison model test and confirm it fails**

Run: `python -m pytest tests/release/scenarios/test_runtime_parity.py::test_comparison_record_serializes_required_fields -q`

Expected: import failure.

- [ ] **Step 3: Implement comparison model**

```python
# tests/release/scenarios/runtime_parity.py
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class ComparisonRecord:
    capability: str
    scenario_id: str
    streams: tuple[str, ...]
    status: str
    required_fields: tuple[str, ...]
    differences: list[dict[str, Any]]
    evidence_paths: tuple[str, ...]

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["streams"] = list(self.streams)
        payload["required_fields"] = list(self.required_fields)
        payload["evidence_paths"] = list(self.evidence_paths)
        return payload
```

- [ ] **Step 4: Run comparison model test**

Run: `python -m pytest tests/release/scenarios/test_runtime_parity.py::test_comparison_record_serializes_required_fields -q`

Expected: `1 passed`.

- [ ] **Step 5: Commit comparison model**

```bash
git add tests/release/scenarios/runtime_parity.py tests/release/scenarios/test_runtime_parity.py
git commit -m "feat: model release runtime parity comparisons"
```

## Task 2: Strict Normalized Dict Comparison

**Files:**
- Modify: `tests/release/scenarios/runtime_parity.py`
- Modify: `tests/release/scenarios/test_runtime_parity.py`

- [ ] **Step 1: Add strict comparison tests**

```python
from tests.release.scenarios.runtime_parity import compare_normalized_records


def test_compare_normalized_records_passes_equal_required_fields() -> None:
    record = compare_normalized_records(
        capability="activation",
        scenario_id="python-passive-switchover",
        python={"status": "passed", "restore_name": "restore-acm", "duration": 10},
        ansible={"status": "passed", "restore_name": "restore-acm", "duration": 12},
        required_fields=("status", "restore_name"),
    )

    assert record.status == "passed"
    assert record.differences == []


def test_compare_normalized_records_fails_missing_source_field() -> None:
    record = compare_normalized_records(
        capability="activation",
        scenario_id="python-passive-switchover",
        python={"status": "passed"},
        ansible={"status": "passed", "restore_name": "restore-acm"},
        required_fields=("status", "restore_name"),
    )

    assert record.status == "failed"
    assert record.differences[0]["field"] == "restore_name"
```

- [ ] **Step 2: Run strict comparison tests and confirm they fail**

Run: `python -m pytest tests/release/scenarios/test_runtime_parity.py::test_compare_normalized_records_passes_equal_required_fields tests/release/scenarios/test_runtime_parity.py::test_compare_normalized_records_fails_missing_source_field -q`

Expected: import failure for missing function.

- [ ] **Step 3: Implement strict comparison**

```python
def compare_normalized_records(
    *,
    capability: str,
    scenario_id: str,
    python: dict[str, Any],
    ansible: dict[str, Any],
    required_fields: tuple[str, ...],
    evidence_paths: tuple[str, ...] = (),
) -> ComparisonRecord:
    differences: list[dict[str, Any]] = []
    for field in required_fields:
        if field not in python or field not in ansible:
            differences.append({"field": field, "python": python.get(field, "<missing>"), "ansible": ansible.get(field, "<missing>")})
            continue
        if python[field] != ansible[field]:
            differences.append({"field": field, "python": python[field], "ansible": ansible[field]})
    return ComparisonRecord(
        capability=capability,
        scenario_id=scenario_id,
        streams=("python", "ansible"),
        status="passed" if not differences else "failed",
        required_fields=required_fields,
        differences=differences,
        evidence_paths=evidence_paths,
    )
```

- [ ] **Step 4: Run strict comparison tests**

Run: `python -m pytest tests/release/scenarios/test_runtime_parity.py::test_compare_normalized_records_passes_equal_required_fields tests/release/scenarios/test_runtime_parity.py::test_compare_normalized_records_fails_missing_source_field -q`

Expected: `2 passed`.

- [ ] **Step 5: Commit strict comparison**

```bash
git add tests/release/scenarios/runtime_parity.py tests/release/scenarios/test_runtime_parity.py
git commit -m "feat: compare normalized runtime parity records"
```

## Task 3: Capability Normalizers

**Files:**
- Modify: `tests/release/scenarios/runtime_parity.py`
- Modify: `tests/release/scenarios/test_runtime_parity.py`

- [ ] **Step 1: Add preflight and Argo CD normalizer tests**

```python
from tests.release.scenarios.runtime_parity import normalize_argocd_management, normalize_preflight


def test_normalize_preflight_sorts_check_sets() -> None:
    normalized = normalize_preflight(
        {
            "status": "passed",
            "critical_failure_count": 0,
            "warning_failure_count": 1,
            "check_ids": ["z", "a"],
            "failed_check_ids": ["z"],
        }
    )

    assert normalized["check_ids"] == ["a", "z"]
    assert normalized["failed_check_ids"] == ["z"]


def test_normalize_argocd_management_uses_discovered_application_sets() -> None:
    normalized = normalize_argocd_management(
        {
            "selected_applications": ["app-b", "app-a"],
            "paused_applications": ["app-a"],
            "resumed_applications": ["app-b"],
            "resume_failures": [],
            "conflict_allowlist_used": False,
        }
    )

    assert normalized["selected_applications"] == ["app-a", "app-b"]
```

- [ ] **Step 2: Run normalizer tests and confirm they fail**

Run: `python -m pytest tests/release/scenarios/test_runtime_parity.py::test_normalize_preflight_sorts_check_sets tests/release/scenarios/test_runtime_parity.py::test_normalize_argocd_management_uses_discovered_application_sets -q`

Expected: import failure for missing functions.

- [ ] **Step 3: Implement first normalizers and a field table**

```python
CAPABILITY_REQUIRED_FIELDS = {
    "preflight validation": ("status", "critical_failure_count", "warning_failure_count", "check_ids", "failed_check_ids"),
    "Argo CD management": ("selected_applications", "paused_applications", "resumed_applications", "resume_failures", "conflict_allowlist_used"),
    "activation": ("restore_name", "restore_phase_category", "sync_restore_enabled", "managed_cluster_activation_requested"),
    "finalization": ("backup_schedule_present", "backup_schedule_paused", "post_enable_backup_observed", "old_hub_action_result"),
}


def _sorted_list(value: Any) -> list:
    return sorted(value or [])


def normalize_preflight(source: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": source["status"],
        "critical_failure_count": int(source["critical_failure_count"]),
        "warning_failure_count": int(source["warning_failure_count"]),
        "check_ids": _sorted_list(source["check_ids"]),
        "failed_check_ids": _sorted_list(source["failed_check_ids"]),
    }


def normalize_argocd_management(source: dict[str, Any]) -> dict[str, Any]:
    return {
        "selected_applications": _sorted_list(source["selected_applications"]),
        "paused_applications": _sorted_list(source["paused_applications"]),
        "resumed_applications": _sorted_list(source["resumed_applications"]),
        "resume_failures": _sorted_list(source["resume_failures"]),
        "conflict_allowlist_used": bool(source["conflict_allowlist_used"]),
    }
```

- [ ] **Step 4: Run normalizer tests**

Run: `python -m pytest tests/release/scenarios/test_runtime_parity.py::test_normalize_preflight_sorts_check_sets tests/release/scenarios/test_runtime_parity.py::test_normalize_argocd_management_uses_discovered_application_sets -q`

Expected: `2 passed`.

- [ ] **Step 5: Commit normalizers**

```bash
git add tests/release/scenarios/runtime_parity.py tests/release/scenarios/test_runtime_parity.py
git commit -m "feat: normalize runtime parity capabilities"
```

## Task 4: Runtime Parity Artifact Writing

**Files:**
- Modify: `tests/release/reporting/artifacts.py`
- Modify: `tests/release/scenarios/runtime_parity.py`
- Modify: `tests/release/scenarios/test_runtime_parity.py`

- [ ] **Step 1: Add artifact writing test**

```python
import json

from tests.release.reporting.artifacts import ReleaseArtifacts
from tests.release.scenarios.runtime_parity import write_runtime_parity_artifact


def test_write_runtime_parity_artifact_sets_failed_status(tmp_path: Path) -> None:
    artifacts = ReleaseArtifacts.create(root=tmp_path, run_id="run-1")
    failed = ComparisonRecord("activation", "python-passive-switchover", ("python", "ansible"), "failed", ("status",), [{"field": "status"}], ())

    write_runtime_parity_artifact(artifacts=artifacts, comparisons=[failed])

    payload = json.loads((artifacts.run_dir / "runtime-parity.json").read_text(encoding="utf-8"))
    assert payload["status"] == "failed"
    assert payload["comparisons"][0]["capability"] == "activation"
```

- [ ] **Step 2: Run artifact writing test and confirm it fails**

Run: `python -m pytest tests/release/scenarios/test_runtime_parity.py::test_write_runtime_parity_artifact_sets_failed_status -q`

Expected: import failure for missing function.

- [ ] **Step 3: Implement artifact writer**

```python
def write_runtime_parity_artifact(*, artifacts, comparisons: list[ComparisonRecord]) -> None:
    status = "passed" if comparisons and all(item.status in {"passed", "not_applicable"} for item in comparisons) else "failed"
    if not comparisons:
        status = "not_applicable"
    artifacts.write_json(
        "runtime-parity.json",
        {
            "schema_version": 1,
            "comparisons": [item.to_dict() for item in comparisons],
            "status": status,
        },
    )
```

- [ ] **Step 4: Run runtime parity tests**

Run: `python -m pytest tests/release/scenarios/test_runtime_parity.py -q`

Expected: all runtime parity tests pass.

- [ ] **Step 5: Commit runtime parity artifact writing**

```bash
git add tests/release/scenarios/runtime_parity.py tests/release/scenarios/test_runtime_parity.py
git commit -m "feat: write runtime parity artifacts"
```

## Task 5: Lifecycle Wiring

**Files:**
- Modify: `tests/release/test_release_certification.py`
- Modify: `tests/release/scenarios/test_runtime_parity.py`

- [ ] **Step 1: Add lifecycle parity helper test**

```python
from tests.release.test_release_certification import execute_runtime_parity


def test_execute_runtime_parity_compares_matching_sources(tmp_path: Path) -> None:
    comparisons = execute_runtime_parity(
        normalized_sources={
            "preflight validation": {
                "python": {"status": "passed", "critical_failure_count": 0, "warning_failure_count": 0, "check_ids": ["a"], "failed_check_ids": []},
                "ansible": {"status": "passed", "critical_failure_count": 0, "warning_failure_count": 0, "check_ids": ["a"], "failed_check_ids": []},
            }
        }
    )

    assert comparisons[0].status == "passed"
```

- [ ] **Step 2: Run lifecycle helper test and confirm it fails**

Run: `python -m pytest tests/release/scenarios/test_runtime_parity.py::test_execute_runtime_parity_compares_matching_sources -q`

Expected: import failure for missing helper.

- [ ] **Step 3: Implement lifecycle helper**

```python
# tests/release/test_release_certification.py
from tests.release.scenarios.runtime_parity import CAPABILITY_REQUIRED_FIELDS, compare_normalized_records


def execute_runtime_parity(*, normalized_sources: dict) -> list:
    comparisons = []
    for capability, by_stream in normalized_sources.items():
        comparisons.append(
            compare_normalized_records(
                capability=capability,
                scenario_id="runtime-parity",
                python=by_stream["python"],
                ansible=by_stream["ansible"],
                required_fields=CAPABILITY_REQUIRED_FIELDS[capability],
            )
        )
    return comparisons
```

- [ ] **Step 4: Run lifecycle helper test**

Run: `python -m pytest tests/release/scenarios/test_runtime_parity.py::test_execute_runtime_parity_compares_matching_sources -q`

Expected: `1 passed`.

- [ ] **Step 5: Commit lifecycle parity helper**

```bash
git add tests/release/test_release_certification.py tests/release/scenarios/test_runtime_parity.py
git commit -m "feat: wire runtime parity execution"
```

## Final Verification

- [ ] Run runtime parity suite:

Run: `python -m pytest tests/release/scenarios/test_runtime_parity.py -q`

Expected: all runtime parity tests pass.

- [ ] Run adapter and parity tests together:

Run: `python -m pytest tests/release/adapters tests/release/scenarios/test_runtime_parity.py -q`

Expected: all selected tests pass.

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
