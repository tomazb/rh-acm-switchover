# PR 44: Orchestrator Short-Circuit Finalize Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collapse `_run_release_certification`'s three triplicated not-applicable/finalize blocks into one module-level `_short_circuit_finalize(...)` helper (R2-H4), behavior-preserving.

**Architecture:** Per the approved design (`docs/superpowers/specs/2026-07-03-pr44-orchestrator-shortcircuit-design.md`): keyword-only helper writes the `runtime-parity.json`/`final-baseline.json` not-applicable pair and delegates to `_finalize_run`; the varying `mandatory_argocd` expression stays at each of the three call sites. Existing short-circuit characterization tests guard behavior; one new direct unit test covers the helper.

**Tech Stack:** Python 3, pytest, black/isort (line-length 120).

## Global Constraints

- Behavior-preserving: identical artifacts and `_finalize_run` arguments on all three paths.
- `black --line-length 120` / `isort --profile black --line-length 120` on touched files.
- Base branch: `ansible` @ `cdbc4468`; PR branch `refactor/thermos-44-release-orchestrator-shortcircuit`.

---

### Task 1: Red-first helper unit test

**Files:**
- Modify: `tests/release/test_orchestrator.py` (append)

- [ ] **Step 1: Append the failing test**

```python
def test_short_circuit_finalize_writes_not_applicable_artifacts_and_delegates(tmp_path: Path, monkeypatch) -> None:
    from tests.release import orchestrator as orch_module
    from tests.release.orchestrator import _short_circuit_finalize
    from tests.release.reporting.artifacts import ReleaseArtifacts

    artifacts = ReleaseArtifacts.create(root=tmp_path, run_id="short-circuit-test")
    captured: dict = {}

    def fake_finalize_run(**kwargs):
        captured.update(kwargs)
        return {"finalized": True}

    monkeypatch.setattr(orch_module, "_finalize_run", fake_finalize_run)

    result = _short_circuit_finalize(
        artifacts=artifacts,
        release_options=object(),
        matrix=object(),
        manifest={"status": "running"},
        certification_eligible=False,
        results=[{"scenario": "static-gates"}],
        recovery={"budget": 0},
        mandatory_argocd={"status": "not_applicable"},
        release_metadata={"tag": "test"},
        matrix_validation={"blocked": False},
    )

    assert result == {"finalized": True}
    runtime_parity = json.loads((artifacts.run_dir / "runtime-parity.json").read_text())
    assert runtime_parity["status"] == "not_applicable"
    final_baseline = json.loads((artifacts.run_dir / "final-baseline.json").read_text())
    assert final_baseline == {"schema_version": 1, "status": "not_applicable", "assertions": []}
    assert captured["runtime_parity"] == runtime_parity
    assert captured["final_baseline"] == {"status": "not_applicable", "assertions": []}
    assert captured["mandatory_argocd"] == {"status": "not_applicable"}
    assert captured["results"] == [{"scenario": "static-gates"}]
```

(`json` and `Path` are already imported at the top of the test file; verify and add if missing.)

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/release/test_orchestrator.py::test_short_circuit_finalize_writes_not_applicable_artifacts_and_delegates -q`
Expected: FAIL — `ImportError: cannot import name '_short_circuit_finalize'`.

- [ ] **Step 3: Commit**

```bash
git add tests/release/test_orchestrator.py
git commit -m "test: add red unit test for orchestrator short-circuit finalize helper"
```

### Task 2: Extract the helper and collapse the three blocks

**Files:**
- Modify: `tests/release/orchestrator.py:831-1039`

- [ ] **Step 1: Add the helper after `_not_applicable_artifact` (line ~833)**

```python
def _short_circuit_finalize(
    *,
    artifacts: ReleaseArtifacts,
    release_options: ReleaseOptions,
    matrix,
    manifest: dict,
    certification_eligible: bool,
    results: list[dict],
    recovery: dict,
    mandatory_argocd: dict,
    release_metadata: dict,
    matrix_validation: dict,
) -> dict:
    """Write not-applicable runtime-parity/final-baseline artifacts and finalize an aborted run."""
    runtime_parity = _not_applicable_artifact()
    artifacts.write_json("runtime-parity.json", runtime_parity)
    final_baseline = {"status": "not_applicable", "assertions": []}
    artifacts.write_json("final-baseline.json", {"schema_version": 1, **final_baseline})
    return _finalize_run(
        artifacts=artifacts,
        release_options=release_options,
        matrix=matrix,
        manifest=manifest,
        certification_eligible=certification_eligible,
        results=results,
        runtime_parity=runtime_parity,
        final_baseline=final_baseline,
        recovery=recovery,
        mandatory_argocd=mandatory_argocd,
        release_metadata=release_metadata,
        matrix_validation=matrix_validation,
    )
```

Note: the helper must call `_finalize_run` via the module global (plain name) so the unit test's monkeypatch takes effect — which is the default; do not capture it in a default argument.

- [ ] **Step 2: Collapse the three call sites**

Matrix-blocked block (was lines 899-921):

```python
    if matrix_validation_result.blocked:
        certification_eligible = False
        manifest = build_manifest(certification_eligible)
        artifacts.write_json("manifest.json", manifest)
        results = matrix_validation_results(matrix_validation_result)
        return _short_circuit_finalize(
            artifacts=artifacts,
            release_options=release_options,
            matrix=matrix,
            manifest=manifest,
            certification_eligible=certification_eligible,
            results=results,
            recovery=recovery,
            mandatory_argocd=({"status": "not_applicable"} if profile.argocd.mandatory else {"status": "passed"}),
            release_metadata=release_metadata,
            matrix_validation=matrix_validation,
        )
```

Static-gates block (was 956-974): replace the artifact-pair lines and `_finalize_run` call with:

```python
        if status == "failed" and scenarios_by_id["static-gates"].required:
            return _short_circuit_finalize(
                artifacts=artifacts,
                release_options=release_options,
                matrix=matrix,
                manifest=manifest,
                certification_eligible=certification_eligible,
                results=results,
                recovery=recovery,
                mandatory_argocd=({"status": "not_applicable"} if profile.argocd.mandatory else {"status": "passed"}),
                release_metadata=release_metadata,
                matrix_validation=matrix_validation,
            )
```

Stop-before-mutation block (was 1017-1039):

```python
    if _stop_before_mutation(
        scenarios_by_id=scenarios_by_id,
        lab_readiness_status=lab_readiness.status,
        initial_baseline_status=initial_baseline.status,
    ):
        return _short_circuit_finalize(
            artifacts=artifacts,
            release_options=release_options,
            matrix=matrix,
            manifest=manifest,
            certification_eligible=certification_eligible,
            results=results,
            recovery=recovery,
            mandatory_argocd={"status": "passed" if not profile.argocd.mandatory else lab_readiness.status},
            release_metadata=release_metadata,
            matrix_validation=matrix_validation,
        )
```

- [ ] **Step 3: Run the release suites**

Run: `python -m pytest tests/release/test_orchestrator.py tests/release/test_release_certification.py -q`
Expected: all PASS including the new helper test.

- [ ] **Step 4: Format and commit**

```bash
black --line-length 120 tests/release/orchestrator.py tests/release/test_orchestrator.py
isort --profile black --line-length 120 tests/release/orchestrator.py tests/release/test_orchestrator.py
git add -A
git commit -m "refactor: extract _short_circuit_finalize in release orchestrator (R2-H4)"
```

### Task 3: Full verification, tracker update, draft PR

**Files:**
- Modify: `thermos-resolution-plan.md` (row PR 44)

- [ ] **Step 1: Full gate** — `./run_tests.sh`, expect PASS, record lane counts.

- [ ] **Step 2: Tracker + push + PR**

```bash
git add thermos-resolution-plan.md
git commit -m "docs: mark Thermos PR 44 ready for review in tracker"
git push -u origin refactor/thermos-44-release-orchestrator-shortcircuit
gh pr create --draft --base ansible --title "Thermos PR 44: extract orchestrator short-circuit finalize helper (R2-H4)" --body "<summary + verification evidence>"
```
