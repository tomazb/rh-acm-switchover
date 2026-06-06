# PR25 RBAC Preflight Scaling Design

## Goal

Reduce redundant Python RBAC preflight API calls and repeated namespace probes while preserving the full set of reported permission failures and keeping Python behavior aligned with the Ansible collection's existing RBAC contract.

## Problem

`F42` is not asking for looser RBAC validation. The Python path in `lib/rbac_validator.py` is already fail-closed, but it still pays for the same work more than once:

- `validate_rbac_permissions()` runs live RBAC validation for a hub.
- When that validation fails, `generate_permission_report()` re-runs validation on the same `RBACValidator` instance just to format the report.
- Namespace existence checks for fixed namespaces are re-issued across validator paths even though the answer cannot change within a single validation run.

The collection-side module `ansible_collections/tomazb/acm_switchover/plugins/modules/acm_rbac_validate.py` already works from an already-computed denial set instead of re-running live checks during reporting. PR25 should move Python closer to that "evaluate once, report many times" model without changing the operator-visible failure surface.

## Scope

- Python RBAC validation flow in `lib/rbac_validator.py`.
- The Python preflight caller in `modules/preflight_coordinator.py` only if a tiny caller adjustment is needed to support the cached validator flow.
- Python RBAC tests in `tests/test_rbac_validator.py`, `tests/test_rbac_integration.py`, and `tests/release/checks/test_rbac_certification.py`.
- Tracker updates in `thermos-resolution-plan.md` for the PR25 slice handoff.

## Non-Goals

- No change to the collection's RBAC validation algorithm.
- No change to the shared RBAC permission matrices unless a correctness bug is discovered.
- No batching rewrite around `SelfSubjectAccessReview` or replacement with a different Kubernetes authorization API.
- No operator-facing CLI flag, report schema change, or support-boundary change.
- No intentional drift in Python versus collection failure semantics.

## Parity Constraints

1. Python must continue reporting the same categories of failures (`cluster`, `namespaces`) and the same full denied-permission content for a given permission surface.
2. The collection remains the parity reference for permission-matrix content; PR25 changes evaluation strategy, not required permissions.
3. A missing permission must still produce a fail-closed result even if the check result comes from an in-memory cache rather than a second live API call.
4. The optimization must not reintroduce the old short-circuit behavior that hid later permission failures.

## Approaches Considered

### Approach 1: Per-call memoization only

Cache `check_permission()` results and namespace existence checks inside `RBACValidator`, but leave the higher-level validation/report flow unchanged.

Pros:

- Smallest implementation.
- Low behavioral risk.

Cons:

- Still keeps duplicated orchestration structure.
- Does not make it explicit that reporting is supposed to reuse the first validation result.

### Approach 2: One-pass validation summary with cached probes

Compute validation results once per `RBACValidator` instance and validation option set, then have reporting reuse that stored summary. Also cache per-permission SSAR outcomes and namespace existence probes inside the same validator instance.

Pros:

- Removes the biggest avoidable duplicate live-check cost.
- Preserves the current public API shape.
- Makes Python conceptually closer to the collection's "expand once, summarize many times" flow.

Cons:

- Slightly more internal structure than simple memoization.
- Requires careful cache keys so report generation never reuses the wrong validation surface.

### Approach 3: Broader RBAC API redesign

Replace many SSAR checks with a different cluster authorization snapshot or a much larger local comparison pass.

Pros:

- Potentially largest call-volume reduction.

Cons:

- Higher correctness risk.
- Larger parity and review burden.
- Unnecessary for the current Thermos slice.

## Recommendation

Use Approach 2.

That means:

- cache `check_permission()` outcomes per permission tuple and namespace for the lifetime of a single `RBACValidator` instance
- cache namespace existence results per namespace for the same instance
- cache the final validation summary for each option set used by `validate_all_permissions()` and `validate_decommission_permissions()`
- have `generate_permission_report()` reuse the cached summary instead of triggering a second live validation pass

This keeps the public behavior content-stable while removing the duplicated work that currently makes failing RBAC runs more expensive than they need to be.

## Design

### Validator-local permission cache

Add a private cache on `RBACValidator` keyed by:

```python
(api_group, resource, verb, namespace)
```

The cached value should represent the completed result of a single permission self-check:

- allowed/denied outcome plus denial reason for normal results
- fail-closed exception detail for `ValidationError` cases

The cache is strictly instance-local. It must not be shared between hubs, between runs, or across different `KubeClient` instances.

### Validator-local namespace existence cache

Add a private helper for namespace existence checks, for example:

```python
_namespace_exists_cached(namespace: str) -> bool
```

This should cache the boolean result per namespace on the current validator instance and re-raise failures normally. The validator methods that currently call `self.client.namespace_exists(...)` directly should route through this helper.

This preserves current missing-namespace semantics while avoiding duplicate probes for the same namespace inside one validation/report flow.

### Validation-summary cache

Add a private cache keyed by the effective validation surface:

- full validation:
  - `include_decommission`
  - `include_old_hub_finalization`
  - `skip_observability`
  - `argocd_mode`
  - `argocd_install_type`
- standalone decommission validation:
  - `skip_observability`

The cached summary should store the fully computed return shape of the public validation method:

```python
(all_valid, all_errors)
```

`generate_permission_report()` should call the public validation entrypoint as it does today, but that entrypoint must reuse the cached summary when the same validator instance has already evaluated the same surface.

### Ordering and reporting behavior

Preserve the current iteration order through permission lists and namespace maps so the failure output remains as stable as possible. PR25 is allowed to change the evaluation strategy, but it should not intentionally reorder permission failures or collapse multiple denied permissions into a single summary line.

The cache must not suppress distinct logical failures. If two different permission tuples are denied today, both must still appear after the optimization.

### Caller behavior

Keep `modules/preflight_coordinator.py` behavior unchanged unless a tiny adjustment is needed to avoid constructing a throwaway validator before report generation. The preferred design is to keep the public `validate_rbac_permissions()` interface unchanged and let the optimization live inside the validator implementation.

## Test Design

### Python unit coverage

Add targeted tests in `tests/test_rbac_validator.py` for:

- failed `validate_all_permissions()` followed by `generate_permission_report()` on the same validator does not cause a second wave of `check_permission()` calls
- repeated validation/report access preserves the full failure set for multi-error cases
- repeated namespace checks on the same validator instance stay bounded to one live probe per namespace
- existing decommission deduplication behavior remains intact alongside the new caches

### Python integration coverage

Keep `tests/test_rbac_integration.py` focused on permission-surface parity and manifest coverage. Add a targeted assertion only if the implementation needs a higher-level regression proving the optimized path does not change the permission surface or required verb set.

### Release coverage

Run `tests/release/checks/test_rbac_certification.py` to ensure the certification helper still sees the same effective permission contract after the Python-side optimization.

## Documentation Impact

- Update `thermos-resolution-plan.md` in the PR25 branch to record the PR24 merge handoff and keep the PR25 slice state current.
- No operator-facing docs should change unless the implementation ends up altering observable output or supported workflow semantics.

## Acceptance Criteria

1. Python RBAC validation still fails closed and still reports the full denied-permission set for a given hub and validation surface.
2. A failing validation followed by report generation on the same `RBACValidator` instance does not repeat live SSAR calls solely to rebuild the same summary.
3. Repeated namespace existence checks within the same validator flow are satisfied from an instance-local cache.
4. The Python permission surface remains in parity with the collection-side RBAC contract.
5. Targeted RBAC tests continue to pass:
   - `python -m pytest tests/test_rbac_validator.py tests/test_rbac_collection_parity.py tests/test_rbac_integration.py -q`
   - `python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_rbac_validate.py -q`
   - `python -m pytest tests/release/checks/test_rbac_certification.py -q`
