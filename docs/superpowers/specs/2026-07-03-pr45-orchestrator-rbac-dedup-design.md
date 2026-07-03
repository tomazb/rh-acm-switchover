# PR 45 Design: Loop the Orchestrator Hub RBAC Certification (R2-M7)

**Date:** 2026-07-03
**Finding:** `R2-M7` from
[`docs/plans/2026-07-02-thermos-ansible-review-2-findings.md`](../../plans/2026-07-02-thermos-ansible-review-2-findings.md)
**Tracker row:** PR 45 in [`thermos-resolution-plan.md`](../../../thermos-resolution-plan.md)
**Branch:** `refactor/thermos-45-release-orchestrator-rbac-dedup`

## Problem

Verified at `ansible` @ `7ee06d1a`: inside
`_run_release_certification` (`tests/release/orchestrator.py:1057-1107`),
the primary-hub and secondary-hub RBAC certification blocks are identical
modulo the hub name: `_rbac_certification_scope(...)` →
`certify_rbac_permissions(...)` (9 kwargs) → a 7-field
assertion-renaming comprehension with an `f"{hub}:{name}"` prefix. Any
change to the certification call or assertion shape must be made twice,
inline in the god function.

## Approaches considered

1. **Module-level `_certify_hub_rbac(...)` helper + loop (chosen)** —
   helper takes `hub`, `hub_name`, `scenario_profiles`, `rbac_cert_dir`
   and returns `(CertificationResult, list[dict])`; the call site loops
   over `("primary", "secondary")`. Independently testable; shrinks the
   god function; matches the finding's suggested fix.
2. **Closure in the function** — not independently testable, keeps the
   lines inline. Rejected.
3. **Move the loop into `checks/rbac_certification.py`** — expands scope
   into another module owned by PR 46 (R2-M8). Rejected for this slice.

## Design

```python
def _certify_hub_rbac(
    *,
    hub,
    hub_name: str,
    scenario_profiles: Mapping[str, ScenarioProfile],
    rbac_cert_dir: Path,
) -> tuple[CertificationResult, list[dict]]:
    """Certify one hub's RBAC scope and return its result plus prefixed assertion dicts."""
    scope = _rbac_certification_scope(scenario_profiles, hub_name)
    result = certify_rbac_permissions(
        hub=hub,
        hub_name=hub_name,
        artifact_dir=rbac_cert_dir / hub_name,
        role=scope.role,
        namespace=scope.namespace,
        service_account=scope.service_account,
        include_decommission=scope.include_decommission,
        include_old_hub_finalization=scope.include_old_hub_finalization,
        include_forbidden_permissions=scope.include_forbidden_permissions,
    )
    assertions = [
        {
            "capability": a.capability,
            "name": f"{hub_name}:{a.name}",
            "status": a.status,
            "expected": a.expected,
            "actual": a.actual,
            "evidence_path": a.evidence_path,
            "message": a.message,
        }
        for a in result.assertions
    ]
    return result, assertions
```

Call site:

```python
    if "rbac-bootstrap-live" in scenarios_by_id:
        rbac_cert_dir = artifacts.run_dir / "scenarios" / "rbac-bootstrap-live"
        rbac_cert_dir.mkdir(parents=True, exist_ok=True)
        rbac_cert_assertions: list[dict] = []
        hub_statuses: list[str] = []
        for hub_name in ("primary", "secondary"):
            hub_result, hub_assertions = _certify_hub_rbac(
                hub=profile.hubs[hub_name],
                hub_name=hub_name,
                scenario_profiles=scenario_profiles,
                rbac_cert_dir=rbac_cert_dir,
            )
            hub_statuses.append(hub_result.status)
            rbac_cert_assertions.extend(hub_assertions)

        if all(status == "skipped" for status in hub_statuses):
            rbac_cert_status = "not_applicable"
        elif any(status == "failed" for status in hub_statuses):
            rbac_cert_status = "failed"
        else:
            rbac_cert_status = "passed"
```

The `all`/`any` aggregation is exactly equivalent to the previous
two-hub conditional. `ScenarioProfile` typing follows the existing
`_rbac_certification_scope` signature — reuse whatever type it declares.

## Testing

Existing characterization:
`test_orchestrator_uses_profile_live_rbac_certification_scope`,
`test_orchestrator_required_live_rbac_skip_fails_required_scenario`.
Red-first addition: a direct unit test for `_certify_hub_rbac` with a
monkeypatched `certify_rbac_permissions` asserting the per-hub artifact
dir, the scope-driven kwargs passthrough, and the `hub:name` assertion
prefixing — red while the helper doesn't exist.

## Acceptance criteria

1. `certify_rbac_permissions` is called from exactly one place in
   `orchestrator.py`; primary/secondary handling is a loop.
2. New helper test passes; existing release suites pass unchanged.
3. Touched-file `black`/`isort`, `git diff --check` clean; full
   `./run_tests.sh` passes.

## Assumptions (autonomous session)

Operator pre-approved via the tracker queue (rows 44-47 parallel-safe,
release-tooling scope). Adjacent-region overlap with open PR 44 (#132) is
expected to merge cleanly (disjoint hunks in the same function); whichever
merges second rebases trivially.
