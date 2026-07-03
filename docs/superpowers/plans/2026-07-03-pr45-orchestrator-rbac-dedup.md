# PR 45: Orchestrator Hub RBAC Certification Dedup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the duplicated primary/secondary RBAC certification blocks in `_run_release_certification` with a loop over one `_certify_hub_rbac(...)` helper (R2-M7), behavior-preserving.

**Architecture:** Per the approved design (`docs/superpowers/specs/2026-07-03-pr45-orchestrator-rbac-dedup-design.md`): module-level helper resolves the hub scope, calls `certify_rbac_permissions`, and returns `(CertificationResult, prefixed assertion dicts)`; the call site loops `("primary", "secondary")` and aggregates status with equivalent `all`/`any` logic. Guarded by existing live-RBAC characterization tests plus a red-first helper unit test.

**Tech Stack:** Python 3, pytest, black/isort (line-length 120).

## Global Constraints

- Behavior-preserving: identical `certify_rbac_permissions` kwargs, assertion dicts, and scenario status on both hubs.
- `black --line-length 120` / `isort --profile black --line-length 120` on touched files.
- Base branch: `ansible` @ `7ee06d1a`; PR branch `refactor/thermos-45-release-orchestrator-rbac-dedup`.

---

### Task 1: Red-first helper unit test

**Files:**
- Modify: `tests/release/test_orchestrator.py` (append)

- [ ] **Step 1: Append the failing test**

```python
def test_certify_hub_rbac_prefixes_assertions_and_scopes_artifact_dir(tmp_path: Path, monkeypatch) -> None:
    from tests.release import orchestrator as orch_module
    from tests.release.orchestrator import _certify_hub_rbac

    captured: dict = {}

    def fake_certify(**kwargs):
        captured.update(kwargs)
        return CertificationResult(
            status="passed",
            assertions=[
                AssertionRecord(
                    capability="rbac",
                    name="read-backups",
                    status="passed",
                    expected="allowed",
                    actual="allowed",
                    evidence_path="evidence.json",
                    message="ok",
                )
            ],
        )

    monkeypatch.setattr(orch_module, "certify_rbac_permissions", fake_certify)

    result, assertions = _certify_hub_rbac(
        hub={"context": "primary-hub"},
        hub_name="primary",
        scenario_profiles={},
        rbac_cert_dir=tmp_path,
    )

    assert result.status == "passed"
    assert captured["hub_name"] == "primary"
    assert captured["artifact_dir"] == tmp_path / "primary"
    assert assertions == [
        {
            "capability": "rbac",
            "name": "primary:read-backups",
            "status": "passed",
            "expected": "allowed",
            "actual": "allowed",
            "evidence_path": "evidence.json",
            "message": "ok",
        }
    ]
```

Note: `CertificationResult` and `AssertionRecord` are already imported at the top of the test file (from `tests.release.checks.rbac_certification` / `tests.release.adapters.common`). Verify the `AssertionRecord` field set matches its dataclass; if the certification module uses its own assertion type, construct that type instead — copy the shape used by `test_orchestrator_uses_profile_live_rbac_certification_scope`.

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/release/test_orchestrator.py::test_certify_hub_rbac_prefixes_assertions_and_scopes_artifact_dir -q`
Expected: FAIL — `ImportError: cannot import name '_certify_hub_rbac'`.

- [ ] **Step 3: Commit**

```bash
git add tests/release/test_orchestrator.py
git commit -m "test: add red unit test for hub RBAC certification helper"
```

### Task 2: Extract helper, loop the call site

**Files:**
- Modify: `tests/release/orchestrator.py:211` region (add helper after `_rbac_certification_scope`) and `:1051-1115` (loop)

- [ ] **Step 1: Add the helper directly after `_rbac_certification_scope`**

Exactly the code from the design spec's Design section (helper returning `tuple[CertificationResult, list[dict]]`).

- [ ] **Step 2: Replace the duplicated blocks with the loop**

Exactly the call-site code from the design spec (loop over `("primary", "secondary")`, `hub_statuses` aggregation with `all(... == "skipped")` → `not_applicable`, `any(... == "failed")` → `failed`, else `passed`).

- [ ] **Step 3: Run the release suites**

Run: `python -m pytest tests/release/test_orchestrator.py tests/release/test_release_certification.py -q`
Expected: all PASS.

- [ ] **Step 4: Format and commit**

```bash
black --line-length 120 tests/release/orchestrator.py tests/release/test_orchestrator.py
isort --profile black --line-length 120 tests/release/orchestrator.py tests/release/test_orchestrator.py
git add -A
git commit -m "refactor: loop hub RBAC certification in release orchestrator (R2-M7)"
```

### Task 3: Full verification, tracker update, draft PR

**Files:**
- Modify: `thermos-resolution-plan.md` (row PR 45)

- [ ] **Step 1: Full gate** — `./run_tests.sh`, expect PASS, record lane counts.

- [ ] **Step 2: Tracker + push + PR**

```bash
git add thermos-resolution-plan.md
git commit -m "docs: mark Thermos PR 45 ready for review in tracker"
git push -u origin refactor/thermos-45-release-orchestrator-rbac-dedup
gh pr create --draft --base ansible --title "Thermos PR 45: loop hub RBAC certification in release orchestrator (R2-M7)" --body "<summary + verification evidence>"
```
