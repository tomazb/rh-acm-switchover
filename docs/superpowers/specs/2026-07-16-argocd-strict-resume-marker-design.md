# Argo CD Strict Resume Marker Design

Status: approved prerequisite for PBT-08. Tracks issue #173.

## Problem

Argo CD resume has two competing contracts.

The current safety contract requires exact marker ownership: only an
Application whose `acm-switchover.argoproj.io/paused-by` annotation equals the
current run ID may have its recorded sync policy restored. Missing and foreign
markers must not authorize a mutation. A result is a no-op only when no
cluster-visible mutation was attempted or applied.

Commit `73dd6c33d5bd7fec72d50deddd4d3f0cf979358b` deliberately introduced a
different recovery contract. Passive restore can replace a current pause
marker with one from an older backup. When that happened on an Application
whose auto-sync was already enabled, Python removed the foreign marker and
returned the marker-missing result, which `is_resume_noop()` classifies as a
no-op. That preserved an operational cleanup path, but it also mutated unowned
metadata and hid the patch attempt behind no-op semantics.

The Ansible collection already enforces the stable-snapshot portion of the
strict contract: its resume patch task runs only when the discovered marker
exactly equals the expected run ID and merely warns for discovered foreign
markers. The final resilience review additionally identified that both form
factors need a mutation-time precondition to close the discovery-to-patch race.

## Options considered

### 1. Remove implicit foreign-marker cleanup

Strict resume returns marker mismatch for every non-empty foreign marker,
regardless of current auto-sync state, without patching.

Advantages:

- exact ownership remains the sole mutation authority;
- Python realigns with the collection;
- no-op results once again prove that no mutation occurred;
- no new CLI, RBAC, checkpoint, or reporting surface is introduced.

Trade-off: a marker restored from an older backup remains until an operator
inspects and explicitly removes it. This is acceptable because an Application
with auto-sync already enabled is functional, and strict resume cannot prove
that the foreign marker is stale or safe to delete.

### 2. Add an explicit stale-marker cleanup mode

A new operator-selected Python flag and collection variable/playbook path would
remove a foreign marker without restoring sync policy and would return a new,
non-noop result.

This preserves automation but expands both operator interfaces, validation,
RBAC review, report/checkpoint contracts, documentation, and parity testing.
There is no current evidence that this additional automated mutation path is
needed, so it violates YAGNI for this prerequisite fix.

### 3. Keep implicit cleanup but enrich `ResumeResult`

Python could retain automatic cleanup while adding fields such as
`patch_attempted` and `marker_cleaned`, preventing `is_resume_noop()` from
misclassifying the result.

Although result semantics would improve, strict resume would still mutate a
foreign marker without explicit operator intent and would remain inconsistent
with the collection. This does not satisfy the shared resume safety contract.

## Decision

Use option 1. Remove implicit foreign-marker cleanup and document why the
historical recovery behavior is intentionally retired.

## Behavior contract

| Marker state | Resume mutation | Result | `is_resume_noop()` |
| --- | --- | --- | --- |
| Equals current run ID and observed resource version remains current | Restore recorded sync policy and remove marker | `restored=True` on success; patch failure on error | `False` |
| Missing or empty | None | `RESUME_SKIP_REASON_MARKER_MISSING` | `True` |
| Non-empty foreign value | None | `RESUME_SKIP_REASON_MARKER_MISMATCH` | `False` |
| Same-run snapshot becomes stale before patch | None; Kubernetes rejects the conditional patch | actionable patch failure (normally `409 Conflict`) | `False` |

The same-run patch must target the exact Application namespace and name supplied
to `resume_autosync()`. Fetch and patch failures retain their existing
fail-closed result semantics. No result following a patch attempt is classified
as a no-op. The same-run check and patch are joined by an optimistic-concurrency
precondition: both implementations send the `resourceVersion` observed with the
matching marker, so a backup restore or concurrent run cannot replace ownership
between discovery and mutation without causing the patch to fail.

## Components

### Python implementation

`lib/argocd.py` removes the auto-sync-dependent foreign-marker cleanup branch
and includes the live Application `resourceVersion` in the same-run merge
patch. A malformed live response without `resourceVersion` fails closed before
the patch call.

### Collection parity

The collection task in `roles/argocd_manage/tasks/resume.yml` already requires
exact marker equality before patching and warns without mutation when the marker
differs. It now also copies the discovered Application `resourceVersion` into
the patch definition, giving the same conditional mutation boundary as Python.

### Tests

Deterministic Python unit tests cover:

- exact namespace/name on the same-run read and patch;
- same-run sync-policy restoration and marker removal;
- missing marker with zero patches and true no-op classification;
- foreign marker with auto-sync disabled and enabled, both with zero patches,
  mismatch results, and false no-op classification;
- same-run patch failure remaining false for `is_resume_noop()`;
- stale-resource `409 Conflict` remaining actionable and non-noop;
- missing `resourceVersion` failing before any Python patch; and
- collection patch definitions carrying the discovered `resourceVersion`.

Mocks remain at the Kubernetes client boundary and include realistic
`resourceVersion` metadata. No live cluster or Argo CD API is used.

### Operator documentation

`docs/operations/usage.md` and `docs/operations/quickref.md` describe strict
marker ownership and explain that confirmed stale foreign markers must be
inspected and removed explicitly. `CHANGELOG.md` records both the safety fix and
the intentional retirement of implicit stale-marker cleanup.

Protected runbook and skill files remain untouched.

## Rollback and operational impact

The change is code-only and has no persisted schema migration. Rolling back
restores the previous implicit cleanup behavior. During normal operation, a
foreign marker may remain visible on an already-enabled Application, but no
sync behavior changes. Resume returns `RESUME_SKIP_REASON_MARKER_MISMATCH`, and
the caller treats the Application as not restored/actionable, which may require
operator inspection and explicit marker removal.

The safety gain is fail-closed ownership: backup-restored metadata cannot cause
strict resume to mutate an Application unless the live marker proves the same
run ID.
