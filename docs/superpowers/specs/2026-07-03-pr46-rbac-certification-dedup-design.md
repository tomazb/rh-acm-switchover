# PR 46 Design: Polarity-Parameterized Permission Evaluation (R2-M8)

**Date:** 2026-07-03
**Finding:** `R2-M8` from
[`docs/plans/2026-07-02-thermos-ansible-review-2-findings.md`](../../plans/2026-07-02-thermos-ansible-review-2-findings.md)
**Tracker row:** PR 46 in [`thermos-resolution-plan.md`](../../../thermos-resolution-plan.md)
**Branch:** `refactor/thermos-46-rbac-certification-dedup`

## Problem

Verified at `ansible` @ `7ee06d1a`:
`tests/release/checks/rbac_certification.py:422-470` (required permissions)
and `:472-520` (forbidden permissions) run the same algorithm — per
permission: `_check_permission_via_sar` → three-way branch on
error/allowed → build a `CertificationAssertion` — duplicated in full,
differing only in which boolean counts as "passed" and the four
expected/actual/message strings that follow from that polarity.

## Approaches considered

1. **One `_evaluate_permissions(..., expect_allowed: bool)` helper
   (chosen)** — returns `(assertions, unexpected_count, error_count)`;
   expected/actual/message strings derive from the polarity so the emitted
   assertions are byte-identical to today's. Matches the finding's
   suggested fix.
2. **Two thin wrappers over a shared core** — extra indirection with no
   caller that needs it. Rejected.
3. **Leave as-is** — keeps the copy-paste risk (e.g. a message or
   evidence change applied to one loop only). Rejected.

## Design

```python
def _evaluate_permissions(
    *,
    permissions: Iterable[PermissionCheck],
    expect_allowed: bool,
    hub: HubProfile,
    service_account: str,
    artifact_dir: Path,
) -> tuple[list[CertificationAssertion], int, int]:
    """Run SAR checks for permissions; return (assertions, unexpected_count, error_count).

    expect_allowed=True evaluates required permissions (denied = failure);
    expect_allowed=False evaluates forbidden permissions (allowed = failure).
    """
    expected = "allowed" if expect_allowed else "denied"
    assertions: list[CertificationAssertion] = []
    unexpected_count = 0
    error_count = 0
    for permission in permissions:
        sar_result = _check_permission_via_sar(
            kubeconfig=hub.kubeconfig,
            context=hub.context,
            permission=permission,
            service_account=service_account,
            artifact_dir=artifact_dir,
        )
        perm_name = _permission_name(permission)
        if sar_result.error:
            error_count += 1
            assertions.append(
                CertificationAssertion(
                    capability="rbac-certification",
                    name=perm_name,
                    status="failed",
                    expected=expected,
                    actual="error",
                    evidence_path=sar_result.evidence_path,
                    message=f"SAR check failed for {service_account}: {sar_result.error}",
                )
            )
        elif sar_result.allowed != expect_allowed:
            unexpected_count += 1
            assertions.append(
                CertificationAssertion(
                    capability="rbac-certification",
                    name=perm_name,
                    status="failed",
                    expected=expected,
                    actual="allowed" if sar_result.allowed else "denied",
                    evidence_path=sar_result.evidence_path,
                    message=(
                        f"Permission denied for {service_account}"
                        if expect_allowed
                        else f"Forbidden permission allowed for {service_account}"
                    ),
                )
            )
        else:
            assertions.append(
                CertificationAssertion(
                    capability="rbac-certification",
                    name=perm_name,
                    status="passed",
                    expected=expected,
                    actual=expected,
                    evidence_path=sar_result.evidence_path,
                    message=(
                        f"Permission allowed for {service_account}"
                        if expect_allowed
                        else f"Forbidden permission denied for {service_account}"
                    ),
                )
            )
    return assertions, unexpected_count, error_count
```

Call site in `certify_rbac_permissions`:

```python
    assertions, denied_count, error_count = _evaluate_permissions(
        permissions=permissions,
        expect_allowed=True,
        hub=hub,
        service_account=sa_full_name,
        artifact_dir=artifact_dir,
    )
    forbidden_allowed_count = 0
    if include_forbidden_permissions:
        forbidden_assertions, forbidden_allowed_count, forbidden_errors = _evaluate_permissions(
            permissions=_get_forbidden_permissions(),
            expect_allowed=False,
            hub=hub,
            service_account=sa_full_name,
            artifact_dir=artifact_dir,
        )
        assertions.extend(forbidden_assertions)
        error_count += forbidden_errors
```

(The `hub` parameter's type annotation follows whatever
`certify_rbac_permissions` already declares for its `hub` argument.)
Downstream `failed_count`/summary logic unchanged. Emitted assertions are
byte-identical to today's on every branch.

## Testing

Existing `tests/release/checks/test_rbac_certification.py` (~27 tests)
characterizes required, forbidden, error, and mixed outcomes. Red-first
addition: a direct parametrized unit test for `_evaluate_permissions`
covering both polarities (allowed/denied/error → status, expected/actual,
message strings, counts) with a monkeypatched `_check_permission_via_sar`.

## Acceptance criteria

1. One evaluation loop in the file; `certify_rbac_permissions` calls the
   helper twice.
2. New helper tests pass; existing certification suite passes unchanged.
3. Touched-file `black`/`isort`, `git diff --check` clean; full
   `./run_tests.sh` passes.

## Assumptions (autonomous session)

Operator pre-approved via the tracker queue (rows 44-47 parallel-safe,
release-tooling scope).
