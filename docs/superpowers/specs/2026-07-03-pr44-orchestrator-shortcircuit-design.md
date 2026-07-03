# PR 44 Design: Extract the Orchestrator Short-Circuit Finalize Helper (R2-H4)

**Date:** 2026-07-03
**Finding:** `R2-H4` from
[`docs/plans/2026-07-02-thermos-ansible-review-2-findings.md`](../../plans/2026-07-02-thermos-ansible-review-2-findings.md)
**Tracker row:** PR 44 in [`thermos-resolution-plan.md`](../../../thermos-resolution-plan.md)
**Branch:** `refactor/thermos-44-release-orchestrator-shortcircuit`

## Problem

Verified at `ansible` @ `cdbc4468`: `tests/release/orchestrator.py`'s
`_run_release_certification` contains three near-identical short-circuit
blocks (lines 899-921 matrix-validation blocked, 956-974 required
static-gates failure, 1017-1039 stop-before-mutation). Each builds the same
`not_applicable` runtime-parity/final-baseline artifact pair, writes both
JSON files, and calls `_finalize_run` with the same 12-argument list —
differing only in the `mandatory_argocd` expression and how far `results`
has accumulated. A change to the artifact shape or `_finalize_run`
signature must be applied identically three times.

## Approaches considered

1. **Module-level `_short_circuit_finalize(...)` helper (chosen)** —
   keyword-only, writes the two `not_applicable` artifacts and delegates to
   `_finalize_run`; the varying `mandatory_argocd` stays at each call site.
   Directly unit-testable, and shrinks the 335-line function (the actual
   R2-H4 complaint).
2. **Closure inside `_run_release_certification`** — shorter signature via
   captured scope, but keeps all the lines inside the god function and is
   not independently testable. Rejected.
3. **Leave as-is with a comment** — does not fix the triplication. Rejected.

## Design

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

The three blocks collapse to single `return _short_circuit_finalize(...)`
calls with their existing `mandatory_argocd` expressions. No behavior
change.

## Testing

Behavior is characterized by existing suites covering all three paths
(`test_orchestrator_blocks_required_unsupported_pair_before_adapter_execution`,
`test_orchestrator_blocks_unsafe_mutating_sequence_...`,
`test_orchestrator_static_gate_secret_failure_reaches_summary`,
`test_orchestrator_stops_before_mutation_when_lab_readiness_fails`,
`test_orchestrator_stops_before_mutation_when_baseline_check_fails`).
Red-first addition: a direct unit test for `_short_circuit_finalize`
(stub artifacts dir) asserting both artifacts are written with
`not_applicable` status and the `_finalize_run` result is returned —
red while the helper doesn't exist.

## Acceptance criteria

1. Exactly one place in `orchestrator.py` builds the not-applicable
   runtime-parity/final-baseline pair (the count of `_not_applicable_artifact()` calls
   inside `_run_release_certification` drops to 0).
2. New helper unit test passes; `tests/release/test_orchestrator.py` and
   `tests/release/test_release_certification.py` pass unchanged.
3. Touched-file `black`/`isort`, `git diff --check` clean; full
   `./run_tests.sh` passes.

## Assumptions (autonomous session)

Operator pre-approved via the tracker queue (rows 44-47 are explicitly
parallel-safe, release-tooling scope). R2-M7 (primary/secondary RBAC
certification duplication in the same function) is PR 45, not this slice.
