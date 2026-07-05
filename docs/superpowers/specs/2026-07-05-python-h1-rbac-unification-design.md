# Python H1 — RBAC Validator Unification (Design/Spec)

- **Date:** 2026-07-05
- **Tracker item:** `H1` (Thermos Review #1, re-confirmed by Review #2) — "derive the
  Python validator RBAC table from the Python operator table while keeping
  cross-surface parity tests."
- **Branch:** `refactor/thermos-h1-python-rbac-unification`
- **Worktree:** `.worktrees/thermos-h1-python-rbac-unification`
- **Relationship to PR 39 / R2-H3:** This PR is the Python-side prerequisite. PR 39
  later mirrors the hub-role loop shape on the Ansible side
  (`roles/preflight/tasks/validate_rbac.yml`) while preserving registered-fact
  contracts. **This PR must not touch that task file or any Ansible RBAC validation
  logic.**

## Problem: exact current duplication in `lib/rbac_validator.py`

### Facet 1 — hand-maintained validator cluster table

`OPERATOR_CLUSTER_PERMISSIONS` (`lib/rbac_validator.py:52-77`) and
`VALIDATOR_CLUSTER_PERMISSIONS` (`lib/rbac_validator.py:80-97`) are near-identical
literal copies. Verified relationship in the current tree: stripping mutating verbs
from every operator entry reproduces the validator table **exactly**. The single
difference is `patch` on
`cluster.open-cluster-management.io/managedclusters` (operator:
`["get", "list", "patch"]`; validator: `["get", "list"]`). Every cluster-table
permission change is currently a two-place edit inside the same file, on top of the
deliberate cross-surface copies (collection module, manifests, docs) that the
parity tests police.

Out of scope: `OPERATOR_HUB_NAMESPACE_PERMISSIONS` vs
`VALIDATOR_HUB_NAMESPACE_PERMISSIONS` are **not** pure verb-stripped mirrors (e.g.
validator `apps/deployments` gains `list` that the operator entry lacks, and the
operator-only `statefulsets/scale` subresource is absent from the validator table).
Deriving those would change checked permissions and is explicitly not part of H1.
The managed-cluster namespace tables have the same property (validator drops
`create`/`patch` **and** `list` differences don't arise, but the tables stay
literal for the same reason). H1 scope is the cluster table only, matching the
tracker wording.

### Facet 2 — duplicated primary/secondary hub validation

`validate_rbac_permissions()` (`lib/rbac_validator.py:946-1004`) contains two
near-verbatim blocks: build validator → `validate_all_permissions(...)` → wrap
discovery/authorization `ValidationError` (fail closed) → on invalid, log
`generate_permission_report(...)` and raise. The asymmetries, verified against
source:

1. **Primary-only flags:** `include_decommission` and
   `include_old_hub_finalization` pass through for primary; secondary hardcodes
   both `False` (decommission / old-hub finalization apply to the old hub only).
2. **Secondary-only install-type override:** secondary uses
   `secondary_argocd_install_type or argocd_install_type`; primary uses
   `argocd_install_type` directly.
3. **Secondary-only error-count message:** secondary failure raises
   `"RBAC permission validation failed on secondary hub ({N} error(s)). See report
   above for details."`; primary failure raises
   `"RBAC permission validation failed on primary hub. See report above for
   details."` (no count).
4. **Missing-hub handling:** absent primary logs
   `"Primary hub not available; skipping primary RBAC validation"`; absent
   secondary is silently skipped.
5. **Presence gate:** primary is gated on `primary_client is not None`; secondary
   on truthiness (`if secondary_client:`). Preserved verbatim.
6. Shared preconditions (unchanged): both clients `None` → `ValueError`;
   `include_decommission` without primary → `ValueError`; `argocd_mode` validated
   up front.

## Chosen design

### Facet 1 — derivation helper with explicit exception data

Add a module-level constant and helper (module level so the class body can call it
while the class is being defined):

```python
MUTATING_VERBS = frozenset({"create", "update", "patch", "delete"})

# The one intentional operator-vs-validator cluster-table difference. The
# validator role is read-only, so the operator's managedclusters "patch" (used to
# activate managed clusters during switchover) must not be granted to it.
VALIDATOR_CLUSTER_VERB_EXCEPTIONS = {
    ("cluster.open-cluster-management.io", "managedclusters"): frozenset({"patch"}),
}

def _derive_read_only_permissions(operator_permissions, expected_removals):
    ...
```

The helper filters `MUTATING_VERBS` out of each `(api_group, resource, verbs)`
entry, preserving entry order and verb order, and **verifies** that the verbs
actually removed match `expected_removals` exactly, raising `ValueError` at import
time on any drift (a new mutating verb in the operator table, or a stale exception
entry). Entries whose verbs all get stripped would be dropped (none exist today;
the guard makes silent drops impossible anyway). Then:

```python
VALIDATOR_CLUSTER_PERMISSIONS = _derive_read_only_permissions(
    OPERATOR_CLUSTER_PERMISSIONS, VALIDATOR_CLUSTER_VERB_EXCEPTIONS
)
```

inside the class body (both names are class attributes today; that stays true, so
`RBACValidator.VALIDATOR_CLUSTER_PERMISSIONS` and the `_get_cluster_permissions()`
role dispatch are unchanged for all consumers, including
`tests/test_rbac_collection_parity.py`, `tests/release/checks/rbac_certification.py`,
and `check_rbac.py`).

`_is_write_verb()` currently duplicates the same verb tuple inline; it delegates to
`MUTATING_VERBS` so the mutating-verb definition has one source. No behavior
change (same four verbs).

**Rejected alternatives (Facet 1):**

- *Plain generic strip without exception verification.* Simpler, but a future
  mutating verb added to the operator table would silently narrow the validator
  table with no conscious decision point; the task requires the exception to be
  explicit data.
- *Keep two literals + a derivation-relationship test.* Leaves the two-place edit
  in place; only detects drift, doesn't remove the duplication (the actual H1
  finding).
- *Derive the namespace tables too.* Rejected: they are not pure verb-strips (see
  scope note above); forcing derivation would either change checked permissions or
  require an exception table bigger than the data it derives.

### Facet 2 — `_validate_hub(...)` + loop over hub roles

Add a module-level helper:

```python
def _validate_hub(
    hub_role,            # HUB_ROLE_PRIMARY / HUB_ROLE_SECONDARY (lib.constants)
    client,
    *,
    include_decommission,
    include_old_hub_finalization,
    skip_observability,
    argocd_mode,
    argocd_install_type,
    include_error_count,  # secondary-only message behavior, explicit
) -> None
```

body = the exact shared block: info log
`"Validating RBAC permissions on %s hub..."`, `RBACValidator(client)`,
`validate_all_permissions(...)` with the passed flags, `ValidationError` wrap
`"RBAC permission validation could not be completed on {hub_role} hub: {exc}"`
(fail-closed discovery/authorization path preserved), and on invalid:
`generate_permission_report(...)` with the same flags, `logger.error("\n%s", report)`,
then raise with the hub-appropriate message (error count appended only when
`include_error_count=True`, computed as today:
`sum(len(errs) for errs in errors.values())`).

`validate_rbac_permissions()` keeps its signature, docstring contract, and
precondition `ValueError`s, then loops:

```python
hub_validations = (
    (HUB_ROLE_PRIMARY, primary_client, include_decommission,
     include_old_hub_finalization, argocd_install_type, False),
    (HUB_ROLE_SECONDARY, secondary_client, False, False,
     secondary_argocd_install_type or argocd_install_type, True),
)
for hub_role, client, decommission, old_hub_finalization, install_type, with_count in hub_validations:
    if hub_role == HUB_ROLE_PRIMARY and client is None:
        logger.info("Primary hub not available; skipping primary RBAC validation")
        continue
    if hub_role == HUB_ROLE_SECONDARY and not client:
        continue
    _validate_hub(hub_role, client, ...)
```

Asymmetries 1-5 above are all encoded as explicit per-hub data or explicit
branches, not re-derived inside the helper. The loop-over-hub-roles shape is the
structural pattern PR 39 must later mirror in Ansible (loop over a hub-role list
feeding one parameterized include/task block, preserving registered-fact
contracts).

**Rejected alternatives (Facet 2):**

- *Two explicit helper calls, no loop.* Marginally simpler here, but the tracker
  sequences PR 39 to "mirror the hub-loop shape"; establishing the loop on the
  Python side is the point of doing H1 first.
- *Fold hub iteration into `RBACValidator` (classmethod).* Restructures the public
  API and the validator-per-hub lifecycle (per-client caches) for no gain; out of
  H1 scope.
- *Normalize the primary/secondary message shapes while deduplicating.* Rejected:
  operator-facing messages are externally meaningful and test-asserted; behavior
  preservation wins. Any message unification is a separate, explicitly documented
  change.

## Behavior-preservation contract

- Same effective permission checks: derived `VALIDATOR_CLUSTER_PERMISSIONS` is
  list/tuple-shape-compatible and **element-for-element equal** to the current
  literal (asserted by a new test against the previous literal value).
- Same required-vs-forbidden semantics: role dispatch, decommission/old-hub
  finalization gating (`ValueError` for validator role), Argo CD mode/install-type
  matrix — untouched.
- Same fail-closed behavior: `ValidationError` from discovery/authorization
  failures still aborts with the same wrap messages per hub.
- Same public result/report shapes: `validate_all_permissions` /
  `generate_permission_report` / `check_rbac.py` output paths untouched; exception
  message strings byte-identical (primary without error count, secondary with).
- Same logging: per-hub info logs, primary skip log, report `logger.error` calls.
- No Ansible-side changes: `roles/preflight/tasks/validate_rbac.yml` and
  `acm_rbac_validate.py` untouched; cross-surface parity tests keep policing the
  deliberate dual-support copies and are not retired, weakened, or adapted away.

## Testing

In `tests/test_rbac_validator.py` (plus existing suites kept green):

1. Derivation: `VALIDATOR_CLUSTER_PERMISSIONS` equals the operator table with
   mutating verbs removed; explicit equality against the expected literal table
   (regression pin of today's exact value).
2. Managedclusters `patch` exception: present in operator entry, absent in
   validator entry; `VALIDATOR_CLUSTER_VERB_EXCEPTIONS` contains exactly this one
   entry.
3. Drift guard: `_derive_read_only_permissions` raises `ValueError` when a
   mutating verb is removed that is not in the expected-exception data, and when
   an expected exception doesn't materialize.
4. Hub-loop equivalence: primary and secondary validation still issue
   `validate_all_permissions` with the same kwargs as before (primary passes
   flags through; secondary hardcodes `False`/`False` and applies the install-type
   override); failure paths raise the exact current message shapes
   (secondary includes `(N error(s))`, primary does not).
5. Asymmetry coverage: primary-only decommission/old-hub-finalization pass-through;
   secondary-only install-type override; primary-absent skip log with
   secondary-only validation still running (restore-only mode).
6. Regression guard against re-forking the hub blocks: assert
   `lib.rbac_validator._validate_hub` exists and both hub validations route
   through it (via call recording), so reintroducing independent primary/secondary
   blocks fails the test without brittle text matching.
7. Existing cross-surface parity tests (`test_rbac_collection_parity.py`,
   `test_rbac_integration.py`, release RBAC certification tests) run unmodified.

## Verification plan

- `git diff --check`
- `python -m pytest tests/test_rbac_validator.py tests/test_check_rbac.py tests/test_rbac_collection_parity.py -q`
- `python -m pytest tests/test_rbac_integration.py tests/release/checks/test_rbac_certification.py -q`
- `python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/ tests/ -q`
- `./run_tests.sh` (strict quality: black/isort/mypy/bandit)
