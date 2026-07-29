# Decommission Completion Proof + Destination Readiness — Design

**Date:** 2026-07-29
**Branch:** `ansible` (spec branch `docs/thermos-safety-specs`)
**Status:** approved design, awaiting implementation plan
**Origin:** cross-validation of main-branch safety specs against `ansible` @ `0bf55db9`,
independently revalidated (Codex). Untracked in `thermos-resolution-plan.md` — `B1` was a
dead-field cleanup; `SSA-02` (planned) covers wrong-target UID and embedded RBAC recheck
only and is complementary, not overlapping.

## Problem

`modules/decommission.py` tears down safety-critical resources without proving completion:

1. **MCO deletion is never verified.** `:122-138` deletes the MultiClusterObservability by
   name (no UID capture), then `:149-160` polls **pods namespace-wide with no label
   selector** and never re-reads the CR. A finalizer-stuck MCO passes undetected (the
   branch already made the pod timeout fatal at `:163-166`; the CR check is still missing).
2. **MCH completion fails open.** `:420-448` only warns when non-operator ACM pods remain
   after the timeout, `:449-455` reports completion; the MCH CR itself is never re-checked.
3. **All interactive refusals return success.** Declining the MCO, ManagedCluster, or MCH
   prompt logs a skip and flows to `return True` (`:69-98`) — a refused destructive step
   reads as a completed decommission.
4. **404 → `[]` blinds inventory reads.** `lib/kube_client.py:724-748` maps API 404 to an
   empty list, so `_delete_managed_clusters` (`:172-176`) cannot distinguish "no
   ManagedClusters" from "discovery/CRD missing".
5. **No destination readiness.** Integrated decommission
   (`modules/finalization.py:1125-1143`) passes the preflight-derived source
   `primary_has_observability` boolean (namespace existence,
   `modules/preflight/namespace_validators.py:102-103`). When source observability exists
   but the destination never got it, source MCO deletion silently ends metrics continuity.
   (POST_ACTIVATION verifies the destination only when observability *was* detected there —
   the uncovered case is exactly destination-not-detected.)

## Goals

1. Every deleted CR (MCO, MCH, ManagedClusters) is proven gone — same-UID absence — before
   the step reports success.
2. A refused destructive substep can never produce a successful decommission.
3. Decommission inventory reads distinguish empty from missing-API.
4. Integrated decommission cannot silently end observability continuity.

## Non-goals

- Wrong-target/UID-expectation and RBAC recheck gates: planned `SSA-02`.
- Generalizing strict-list semantics across the whole codebase (scoped to decommission
  reads here; a broader migration can follow later).
- Preconditioned deletes beyond `V1DeleteOptions.preconditions.uid` (no resourceVersion
  preconditions; UID identity binding is the contract, consistent with the auto-import
  design's preconditioned ConfigMap delete).
- Changes to the Hive `ClusterDeployment preserveOnDelete` safety check (kept as is).

## Design

### 1. Per-resource completion proof

Shared pattern for MCO and MCH (and per-cluster for ManagedClusters):

1. Read the CR; record `metadata.uid`. Absent → clean no-op **only when no teardown for
   this resource was previously started**: each resource records a teardown phase in state
   (`delete_started` → `cr_absent` → `drained`). A rerun that finds the CR absent but the
   phase at `delete_started`/`cr_absent` must still run the pod-drain wait (step 4) before
   marking `drained` — a prior run's drain timeout cannot be laundered into success by the
   CR having disappeared in between.
2. Delete with a server-side identity precondition bound to the observed UID
   (`V1DeleteOptions.preconditions.uid`) — a name-only delete has a TOCTOU gap where a
   replacement created between read and delete gets deleted and the absence poll then
   reads as success. Precondition mismatch (409/412) → fatal, naming the resource.
   Applies to MCO, MCH, and each ManagedCluster. Collection: `kubernetes.core.k8s` does
   not expose delete preconditions, so the role re-reads immediately before delete and
   fails on UID change, then re-verifies UID after deletion — documented as the
   compensating control until upstream supports preconditions.
3. Poll for **CR absence**: GET until 404/absent, bounded by the existing timeout
   constants. A CR that reappears with a *different* UID (replacement) → fatal — someone
   recreated it mid-teardown.
4. Only then run the pod-drain wait, scoped by label selector:
   - MCO: `observability.open-cluster-management.io/name=observability` (already used at
     `modules/post_activation.py` on this branch — promote to a shared constant).
   - MCH: existing ACM operator pod prefix constants; non-matching pods are ignored.
5. After the pod-drain wait completes, re-read the CR one final time before recording
   `drained`: a CR recreated during the drain (any UID) → fatal — replacements stay fatal
   through the completion boundary, matching the collection's post-wait re-list.
6. Timeout at any stage, or a still-present same-UID CR → `SwitchoverError`. The MCH
   warn-and-return-success path (`:449-455`) is removed.

ManagedClusters: per-cluster read → UID → delete → confirm absent; aggregate failures into
one `SwitchoverError` listing the survivors. Hive safety check runs before deletion as
today.

### 2. Refusal aborts

Any interactive "no" at the MCO, ManagedCluster, or MCH prompt:

- stops all remaining teardown substeps,
- prints a summary of what was completed vs. refused/not attempted,
- makes `decommission()` return failure (CLI exit non-zero).

Idempotent rerun resumes cleanly: already-deleted resources are absent and no-op. The
non-interactive path (`interactive=False`, integrated decommission) is unaffected — it
never prompts.

### 3. Scoped strict list

Add `list_custom_resources_strict(...)` (or a `strict=True` flag on the existing method)
in `lib/kube_client.py` that raises a typed error on API-group/resource 404 instead of
returning `[]`. Used by decommission only:

- `_delete_managed_clusters` inventory read: missing `cluster.open-cluster-management.io`
  discovery → fatal ("cannot verify inventory"), genuine empty list → clean "nothing to
  delete".
- MCO lookup: CRD absent **and** observability namespace absent → clean skip; CRD absent
  but namespace present → fatal (inconsistent cluster — refuse to guess).
- MCH lookup: same rule with the ACM namespace.

### 4. Destination observability gate

Immediately before the source MCO deletion substep (not merely at
`_decommission_old_hub` entry — no intervening mutations between check and delete), when
source observability is recorded true:

1. Fresh destination-hub check via the secondary client — MCO CR exists (strict list) and
   observability namespace present. The preflight boolean is not trusted, and the source
   lookup's clean-skip rule (§3) is **not** reused here: on the destination, missing
   discovery, missing CRD, missing CR, or missing namespace all block equally.
2. Missing/unverifiable → fail closed: "source observability will be deleted but
   destination hub has no observability — metrics continuity ends here."
3. Proceed only with a new explicit flag `--acknowledge-observability-not-migrated`
   (final name at implementation; validated like other acknowledgement flags). The flag is
   rejected when the gate would pass anyway (no stale acknowledgements).

Standalone decommission (`--decommission`) is unaffected — it has no destination client.
Collection: the decommission role performs the same gate when a destination
kubeconfig/context is provided; a boolean ack variable mirrors the flag.

### 5. Collection parity

- `roles/decommission/tasks/delete_observability.yml`: pod wait gains the label selector;
  after the wait, re-list MCO and fail if present (the `state: absent` + `wait: true`
  delete already blocks, this adds the replacement-UID/reappearance guard).
- MCH task: same completion proof (CR absence poll + selector-scoped pod wait, fail on
  timeout).
- Roles are non-interactive; refusal semantics don't apply, but failed/partial status
  parity with Python must hold (no `failed_when: false` on these paths).

## Testing

- Finalizer-stuck MCO (CR persists past timeout) → `SwitchoverError`, decommission halts.
- Rerun after drain timeout with CR now absent → pod-drain wait still runs before
  `drained`; teardown-phase record drives it.
- Replacement UID mid-poll → fatal.
- MCH: lingering CR → fatal; lingering *unrelated* pods (wrong labels) → success.
- Refusal at each of the three prompts → abort, correct partial summary, non-zero result;
  rerun completes idempotently.
- Strict list: discovery 404 → fatal; empty list → proceeds; CRD-absent+namespace-present
  → fatal; both absent → skip.
- Destination gate: destination missing obs → blocked; with ack flag → proceeds; destination
  has obs → passes without flag; flag with passing gate → rejected.
- Collection: selector-scoped waits, CR re-list failure path, gate parity.
- Version bump per repo policy (Python + collection, synced).

## Tracker updates (same PR)

| id | severity | summary |
| --- | --- | --- |
| new-C1 | High | MCH completion fails open: warns on lingering pods, never re-checks CR, reports success |
| new-C2 | High | Interactive refusals of destructive substeps still return overall success |
| new-C3 | Medium | No CR-absence proof or UID verification for MCO/MCH/ManagedCluster deletion; pod waits unscoped |
| new-C4 | Medium | 404→[] makes missing discovery indistinguishable from empty inventory in decommission |
| new-C5 | Medium | No destination-observability check before source MCO deletion (metrics continuity) |

Plus one planned slice row referencing this design. `SSA-02` cross-referenced as
complementary (target identity/RBAC vs. completion/readiness).

## Acceptance criteria

1. Decommission cannot report success while any CR of a targeted name still exists —
   same-UID survivors and different-UID replacements are both fatal through the completion
   boundary.
2. A refused substep yields a non-zero result and an accurate summary.
3. Missing API discovery aborts before any deletion decision that depends on the list.
4. Integrated decommission with source observability and a destination without it fails
   closed absent the acknowledgement flag.
5. Existing Hive `preserveOnDelete` behavior unchanged.
