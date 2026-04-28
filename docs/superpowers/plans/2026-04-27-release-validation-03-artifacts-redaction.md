# Release Validation Artifacts And Redaction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add release artifact directory management, schema validators, redaction scanning, sanitized persistence, and early failed artifact emission.

**Architecture:** Put artifact writing and redaction under `tests/release/reporting/` so every later adapter and scenario uses one persistence path. Required JSON files use schema version `1`; command output and reports are sanitized before paths are referenced.

**Tech Stack:** Python dataclasses, json, pathlib, re, pytest, tempfile.

---

## File Map

- Create: `tests/release/reporting/__init__.py`
- Create: `tests/release/reporting/artifacts.py`
- Create: `tests/release/reporting/redaction.py`
- Create: `tests/release/reporting/schema.py`
- Create: `tests/release/reporting/test_artifacts.py`
- Create: `tests/release/reporting/test_redaction.py`
- Create: `tests/release/reporting/test_schema.py`
- Modify: `tests/release/conftest.py`
- Modify: `docs/superpowers/plans/2026-04-27-release-validation-progress.md`

## Task 1: Run Artifact Directory And Required Empty Files

**Files:**
- Create: `tests/release/reporting/artifacts.py`
- Create: `tests/release/reporting/test_artifacts.py`
- Modify: `tests/release/reporting/__init__.py`

- [ ] **Step 1: Add artifact initialization tests**

```python
import json
from pathlib import Path

from tests.release.reporting.artifacts import ReleaseArtifacts


def test_release_artifacts_create_required_files(tmp_path: Path) -> None:
    artifacts = ReleaseArtifacts.create(root=tmp_path, run_id="run-1")

    assert artifacts.run_dir == tmp_path / "run-1"
    for filename in [
        "manifest.json",
        "scenario-results.json",
        "runtime-parity.json",
        "recovery.json",
        "redaction.json",
        "summary.json",
    ]:
        data = json.loads((artifacts.run_dir / filename).read_text(encoding="utf-8"))
        assert data["schema_version"] == 1
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `python -m pytest tests/release/reporting/test_artifacts.py::test_release_artifacts_create_required_files -q`

Expected: import failure for missing reporting package.

- [ ] **Step 3: Implement artifact initialization**

```python
# tests/release/reporting/artifacts.py
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REQUIRED_JSON_ARTIFACTS = {
    "manifest.json": {"schema_version": 1, "status": "created", "warnings": [], "failure_reasons": []},
    "scenario-results.json": {"schema_version": 1, "results": [], "scenario_statuses": []},
    "runtime-parity.json": {"schema_version": 1, "comparisons": [], "status": "not_applicable"},
    "recovery.json": {"schema_version": 1, "budget_minutes": 0, "budget_consumed_seconds": 0, "pre_run": [], "post_failure": [], "hard_stops": [], "status": "not_applicable"},
    "redaction.json": {"schema_version": 1, "status": "not_applicable", "scanned_artifacts": [], "redacted_counts_by_class": {}, "rejected_artifacts": [], "warnings": []},
    "summary.json": {"schema_version": 1, "status": "failed", "certification_eligible": False, "warnings": [], "failure_reasons": []},
}


@dataclass(frozen=True)
class ReleaseArtifacts:
    root: Path
    run_id: str
    run_dir: Path

    @classmethod
    def create(cls, *, root: Path, run_id: str) -> "ReleaseArtifacts":
        run_dir = root / run_id
        run_dir.mkdir(parents=True, exist_ok=False)
        artifacts = cls(root=root, run_id=run_id, run_dir=run_dir)
        for filename, payload in REQUIRED_JSON_ARTIFACTS.items():
            artifacts.write_json(filename, payload)
        (run_dir / "release-report.md").write_text("# Release Report\n\nStatus: created\n", encoding="utf-8")
        return artifacts

    def write_json(self, relative_path: str, payload: dict[str, Any]) -> Path:
        path = self.run_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return path
```

```python
# tests/release/reporting/__init__.py
"""Release reporting and artifact helpers."""
```

- [ ] **Step 4: Run artifact initialization test**

Run: `python -m pytest tests/release/reporting/test_artifacts.py::test_release_artifacts_create_required_files -q`

Expected: `1 passed`.

- [ ] **Step 5: Commit artifact initialization**

```bash
git add tests/release/reporting/__init__.py tests/release/reporting/artifacts.py tests/release/reporting/test_artifacts.py
git commit -m "feat: initialize release artifact files"
```

## Task 2: Redaction Scanner And Sanitized Writes

**Files:**
- Create: `tests/release/reporting/redaction.py`
- Modify: `tests/release/reporting/artifacts.py`
- Create: `tests/release/reporting/test_redaction.py`

- [ ] **Step 1: Add redaction tests**

```python
from pathlib import Path

import pytest

from tests.release.reporting.artifacts import ReleaseArtifacts
from tests.release.reporting.redaction import RedactionError, sanitize_text


def test_sanitize_text_replaces_bearer_token() -> None:
    sanitized = sanitize_text("Authorization: Bearer abcdef1234567890")

    assert "abcdef1234567890" not in sanitized.text
    assert sanitized.redacted_counts_by_class["authorization-header"] == 1


def test_sanitized_write_rejects_secret_data(tmp_path: Path) -> None:
    artifacts = ReleaseArtifacts.create(root=tmp_path, run_id="run-1")

    with pytest.raises(RedactionError, match="kubernetes-secret-data"):
        artifacts.write_sanitized_text("logs/bad.txt", "data:\n  password: c2VjcmV0")
```

- [ ] **Step 2: Run redaction tests and confirm they fail**

Run: `python -m pytest tests/release/reporting/test_redaction.py -q`

Expected: import failure for missing redaction module or methods.

- [ ] **Step 3: Implement scanner and sanitized write**

```python
# tests/release/reporting/redaction.py
from __future__ import annotations

import re
from dataclasses import dataclass


class RedactionError(ValueError):
    """Raised when sensitive material must be rejected instead of redacted."""


@dataclass(frozen=True)
class SanitizedText:
    text: str
    redacted_counts_by_class: dict[str, int]


REDACT_PATTERNS = [
    ("authorization-header", re.compile(r"Authorization:\s*Bearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE), "Authorization: Bearer [REDACTED]"),
    ("api-token", re.compile(r"(?i)(api[_-]?token=)[A-Za-z0-9._~+/=-]+"), r"\1[REDACTED]"),
    ("pem-block", re.compile(r"-----BEGIN [^-]+-----.*?-----END [^-]+-----", re.DOTALL), "[REDACTED PEM BLOCK]"),
]
REJECT_PATTERNS = [
    ("kubeconfig-client-key", re.compile(r"client-key-data\s*:")),
    ("kubeconfig-token", re.compile(r"\btoken\s*:")),
    ("kubernetes-secret-data", re.compile(r"(?m)^\s*(data|stringData)\s*:")),
]


def sanitize_text(text: str) -> SanitizedText:
    counts: dict[str, int] = {}
    sanitized = text
    for klass, pattern in REJECT_PATTERNS:
        if pattern.search(sanitized):
            raise RedactionError(klass)
    for klass, pattern, replacement in REDACT_PATTERNS:
        sanitized, count = pattern.subn(replacement, sanitized)
        if count:
            counts[klass] = count
    return SanitizedText(text=sanitized, redacted_counts_by_class=counts)
```

Add to `ReleaseArtifacts`:

```python
from .redaction import sanitize_text

    def write_sanitized_text(self, relative_path: str, content: str) -> Path:
        sanitized = sanitize_text(content)
        path = self.run_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(sanitized.text, encoding="utf-8")
        return path
```

- [ ] **Step 4: Run redaction tests**

Run: `python -m pytest tests/release/reporting/test_redaction.py -q`

Expected: `2 passed`.

- [ ] **Step 5: Commit redaction**

```bash
git add tests/release/reporting/redaction.py tests/release/reporting/artifacts.py tests/release/reporting/test_redaction.py
git commit -m "feat: sanitize release artifacts"
```

## Task 3: JSON Schema Validators

**Files:**
- Create: `tests/release/reporting/schema.py`
- Create: `tests/release/reporting/test_schema.py`

- [ ] **Step 1: Add schema validation tests**

```python
import pytest

from tests.release.reporting.schema import validate_required_artifact


def test_manifest_requires_schema_version_one() -> None:
    with pytest.raises(ValueError, match="schema_version"):
        validate_required_artifact("manifest.json", {"schema_version": 2})


def test_scenario_results_requires_lists() -> None:
    with pytest.raises(ValueError, match="results"):
        validate_required_artifact("scenario-results.json", {"schema_version": 1, "results": {}, "scenario_statuses": []})
```

- [ ] **Step 2: Run schema tests and confirm they fail**

Run: `python -m pytest tests/release/reporting/test_schema.py -q`

Expected: import failure.

- [ ] **Step 3: Implement minimal required artifact validation**

```python
# tests/release/reporting/schema.py
from __future__ import annotations

from typing import Any


REQUIRED_FIELDS = {
    "manifest.json": ("schema_version", "status", "warnings", "failure_reasons"),
    "scenario-results.json": ("schema_version", "results", "scenario_statuses"),
    "runtime-parity.json": ("schema_version", "comparisons", "status"),
    "recovery.json": ("schema_version", "budget_minutes", "budget_consumed_seconds", "pre_run", "post_failure", "hard_stops", "status"),
    "redaction.json": ("schema_version", "status", "scanned_artifacts", "redacted_counts_by_class", "rejected_artifacts", "warnings"),
    "summary.json": ("schema_version", "status", "certification_eligible", "warnings", "failure_reasons"),
}
LIST_FIELDS = {"results", "scenario_statuses", "comparisons", "pre_run", "post_failure", "hard_stops", "scanned_artifacts", "rejected_artifacts", "warnings", "failure_reasons"}


def validate_required_artifact(filename: str, payload: dict[str, Any]) -> None:
    if filename not in REQUIRED_FIELDS:
        raise ValueError(f"{filename}: not a recognised required artifact")
    if payload.get("schema_version") != 1:
        raise ValueError(f"{filename}: schema_version must be 1")
    for field in REQUIRED_FIELDS[filename]:
        if field == "schema_version":
            continue
        if field not in payload:
            raise ValueError(f"{filename}: missing required field {field}")
        if field in LIST_FIELDS and not isinstance(payload[field], list):
            raise ValueError(f"{filename}: field {field} must be a list")
```

- [ ] **Step 4: Run schema tests**

Run: `python -m pytest tests/release/reporting/test_schema.py -q`

Expected: `2 passed`.

- [ ] **Step 5: Commit schema validators**

```bash
git add tests/release/reporting/schema.py tests/release/reporting/test_schema.py
git commit -m "feat: validate release artifact schemas"
```

## Task 4: Early Failed Manifest Emission

**Files:**
- Modify: `tests/release/reporting/artifacts.py`
- Modify: `tests/release/reporting/test_artifacts.py`
- Modify: `tests/release/conftest.py`

- [ ] **Step 1: Add failed manifest test**

```python
import json


def test_write_failed_manifest_records_reason(tmp_path: Path) -> None:
    artifacts = ReleaseArtifacts.create(root=tmp_path, run_id="run-1")
    artifacts.write_failed_manifest(reason="dirty checkout", command=["pytest", "-m", "release"])

    manifest = json.loads((artifacts.run_dir / "manifest.json").read_text(encoding="utf-8"))
    summary = json.loads((artifacts.run_dir / "summary.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "failed"
    assert "dirty checkout" in manifest["failure_reasons"]
    assert summary["status"] == "failed"
```

- [ ] **Step 2: Run failed manifest test and confirm it fails**

Run: `python -m pytest tests/release/reporting/test_artifacts.py::test_write_failed_manifest_records_reason -q`

Expected: attribute error for missing method.

- [ ] **Step 3: Implement failed manifest writer**

```python
    def write_failed_manifest(self, *, reason: str, command: list[str]) -> None:
        self.write_json(
            "manifest.json",
            {
                "schema_version": 1,
                "run_id": self.run_id,
                "status": "failed",
                "command": command,
                "certification_eligible": False,
                "warnings": [],
                "failure_reasons": [reason],
            },
        )
        self.write_json(
            "summary.json",
            {
                "schema_version": 1,
                "status": "failed",
                "certification_eligible": False,
                "warnings": [],
                "failure_reasons": [reason],
            },
        )
```

Wire `release_artifacts` fixture in `tests/release/conftest.py` after Plan 02 fixtures:

```python
from datetime import datetime, timezone
from tests.release.reporting.artifacts import ReleaseArtifacts


@pytest.fixture(scope="session")
def release_artifacts(release_profile, release_options: ReleaseOptions):
    root = release_options.artifact_dir or Path(release_profile.profile.raw.get("artifacts", {}).get("root", "artifacts/release"))
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return ReleaseArtifacts.create(root=root, run_id=run_id)
```

- [ ] **Step 4: Run artifact tests**

Run: `python -m pytest tests/release/reporting/test_artifacts.py -q`

Expected: all artifact tests pass.

- [ ] **Step 5: Commit early failure artifacts**

```bash
git add tests/release/reporting/artifacts.py tests/release/reporting/test_artifacts.py tests/release/conftest.py
git commit -m "feat: emit failed release manifests"
```

## Final Verification

- [ ] Run focused artifact and redaction tests:

Run: `python -m pytest tests/release/reporting -q`

Expected: all tests pass.

- [ ] Run the no-profile release guard:

Run: `python -m pytest tests/release/test_release_certification.py -q`

Expected: lifecycle test is skipped with the explicit profile requirement.

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
