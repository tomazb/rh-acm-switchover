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

1. Every MCO, MCH, and ManagedCluster deletion is bound to the observed object identity by
   a server-side UID precondition, then proven complete before the step reports success.
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

1. Read the CR. Absent → clean no-op **only when no teardown for this resource was
   previously started**. When present, durably record `metadata.uid` as the resource's
   immutable `expected_uid` together with its teardown phase **before DELETE**
   (`delete_started` → `cr_absent` → `drained`). The state shape is keyed by API
   version/kind/namespace/name and contains both `expected_uid` and `phase`;
   ManagedClusters therefore retain one record per name. A rerun must reuse the recorded
   UID and must never replace it with a fresh same-name observation. If the live name now
   has another UID, fail before DELETE and leave the replacement intact. A rerun that
   finds the CR absent but the recorded phase is `delete_started`/`cr_absent` must still
   run the pod-drain wait (step 4) before marking `drained` — a prior run's drain timeout
   cannot be laundered into success by the CR having disappeared in between.
2. Delete with a server-side identity precondition bound to the recorded `expected_uid`
   (`V1DeleteOptions.preconditions.uid`) — a name-only delete has a TOCTOU gap where a
   replacement created between read and delete gets deleted and the absence poll then
   reads as success. Precondition mismatch (409/412) → fatal, naming the resource.
   Applies to MCO, MCH, and each ManagedCluster in both form factors. Python passes
   `V1DeleteOptions(preconditions=V1Preconditions(uid=expected_uid))`. The collection uses
   the collection-owned guarded-delete boundary defined below.
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

#### Collection-owned UID-preconditioned deletion boundary

The collection implementation adds a thin custom module, provisionally
`tomazb.acm_switchover.acm_uid_guarded_delete`, backed by a collection
`plugins/module_utils/uid_guarded_delete.py` helper. This boundary remains collection-owned:
it may use the Kubernetes Python client already required by the collection, but it must not
import Python CLI production code.

The module accepts these explicit inputs:

| Input | Contract |
| --- | --- |
| `kubeconfig`, `context` | Required explicit hub routing; never fall back to ambient/default kubeconfig or context. |
| `api_version`, `kind` | Required discovery identity. |
| `namespace` | Optional only for cluster-scoped resources; validate it against the discovered resource scope. |
| `name`, `expected_uid` | Required non-empty object identity. |
| `request_timeout`, `wait_timeout`, `wait_sleep` | Positive, bounded request and completion budgets. |

The helper follows the collection's existing client-factory pattern:
`config.new_client_from_config(..., persist_config=False, config_file=kubeconfig,
context=context)`, per-request timeouts, and dynamic API discovery for the supplied API
version and kind. The module supports check mode and owns the complete read → conditional
DELETE → wait → final verification state machine:

1. Perform a live GET through the explicitly selected client. Only an API 404 means
   already absent. Discovery, authorization, TLS, timeout, transport, decode, and every
   other read failure mean **unverifiable** and fail closed.
2. Compare the live `metadata.uid` to `expected_uid`. Missing UID or a different UID is
   fatal; do not issue DELETE. This re-read narrows stale-input errors but is supplementary
   defense, not the race-closing primitive.
3. In check mode, stop after the live read and UID validation. Do not issue a DELETE or
   poll for a mutation. Return `changed: false` plus an explicit `would_change: true` for a
   matching present object; an already-absent object returns `changed: false`,
   `would_change: false`.
4. In execute mode, issue DELETE for the supplied name with a body equivalent to
   `V1DeleteOptions(preconditions=V1Preconditions(uid=expected_uid))`. The UID is evaluated
   by the API server atomically with deletion. HTTP 409 and 412 are fatal precondition
   conflicts; never retry them as an unconditional or name-only delete.
5. If the object disappears after the live read but before DELETE and DELETE reports 404,
   perform a final live verification. Confirmed absence is an idempotent `changed: false`
   result. A replacement or an unverifiable read is fatal.
6. After a 200/202 acceptance of the preconditioned DELETE, poll with a monotonic,
   bounded deadline. The same UID still present means deletion is pending; 404 means
   absent; the same name with a different UID is immediately fatal and must survive.
   Discovery/read errors are fatal, not absence. Timeout while the same UID remains is
   fatal.
7. Before successful return, perform one final live GET. Only confirmed 404 completes the
   contract. Any object at that name, including a different-UID replacement created after
   an earlier 404, is fatal.

Successful execute-mode reporting is exact:

- already absent, or confirmed disappearance before DELETE acceptance:
  `changed: false`;
- `changed: true` only after the intended UID's DELETE request was accepted and the
  bounded completion plus final-absence contract succeeded;
- mismatch, precondition conflict, timeout, replacement, ambiguous response, or
  unverifiable read: a failed result, never a successful changed result.

Every error and result is sanitized at the module boundary. Public output is limited to a
stable operation stage, API status/reason classification, and the non-secret resource
identity. It must never serialize or interpolate raw exception bodies, response headers,
client configuration, kubeconfig contents, bearer tokens, client certificates, private
keys, Secret data, or other credential material. The role task invoking the module also
uses `no_log: true` so callback failure rendering cannot expose module arguments; a
subsequent fixed-text fail task may publish only the sanitized result.

This module replaces the name-based `kubernetes.core.k8s` deletion tasks for MCO, MCH, and
ManagedClusters. The role passes the exact primary-hub kubeconfig/context and the durably
recorded `expected_uid` into each invocation; the module's live read validates that
recorded identity rather than adopting a newly observed UID. Python state and collection
checkpoint `operational_data` use the same per-resource identity/phase shape. Collection
execute-mode decommission requires checkpointing so the target UID map is durable before
the first DELETE; check mode remains non-mutating. Role-level re-reads, pod-drain waits,
and final checks remain useful supplementary defenses, but **a name-based
`kubernetes.core.k8s state=absent` call plus reads before and after it does not close the
read/DELETE race and is not an acceptable substitute for the server-side UID
precondition**.

Primary API basis:

- Kubernetes defines delete [preconditions][kubernetes-preconditions] as conditions that
  must be fulfilled before an operation and defines `uid` as the target UID.
- The official Kubernetes Python client
  [models `V1DeleteOptions.preconditions`][kubernetes-python-delete-options] as
  `V1Preconditions`, matching the Python-side request body used here.
- Current `kubernetes.core.k8s` documentation exposes
  [`delete_options.preconditions.uid`][kubernetes-core-delete] (added in
  `kubernetes.core` 1.2.0), explicit `kubeconfig`/`context`, check mode, and bounded
  deletion waiting. This corrects the earlier claim that the module did not expose delete
  preconditions. It does not make an invocation that omits `delete_options.preconditions`
  safe, and it does not replace the collection-owned state machine and redaction contract
  above.

[kubernetes-preconditions]: https://kubernetes.io/docs/reference/kubernetes-api/definitions/preconditions-v1-meta/
[kubernetes-python-delete-options]: https://github.com/kubernetes-client/python/blob/master/kubernetes/client/models/v1_delete_options.py
[kubernetes-core-delete]: https://docs.ansible.com/projects/ansible/latest/collections/kubernetes/core/k8s_module.html

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

- `roles/decommission/tasks/delete_observability.yml`,
  `delete_multiclusterhub.yml`, and `delete_managed_clusters.yml` route MCO, MCH, and each
  ManagedCluster through `acm_uid_guarded_delete`; no name-only `state: absent` task remains
  for those resources.
- Every invocation passes explicit primary-hub kubeconfig/context, API version, kind,
  namespace where applicable, name, observed UID, and bounded wait values.
- MCO and MCH pod waits gain the scoped selectors, and post-drain live reads preserve the
  through-completion replacement guard from §1.
- Roles are non-interactive; refusal semantics don't apply, but failed/partial status
  parity with Python must hold (no `failed_when: false` on these paths).

## Testing

- Finalizer-stuck MCO (CR persists past timeout) → `SwitchoverError`, decommission halts.
- Rerun after drain timeout with CR now absent → pod-drain wait still runs before
  `drained`; teardown-phase record drives it.
- Crash/rerun after `delete_started` with a same-name replacement → the recorded UID is
  reused, mismatch fails before DELETE, and the replacement survives; tests prove neither
  form factor re-captures the replacement UID.
- Python: expected UID deletion succeeds; server-side 409/412 precondition conflict is
  fatal; replacement UID mid-poll is fatal and survives.
- MCH: lingering CR → fatal; lingering *unrelated* pods (wrong labels) → success.
- Refusal at each of the three prompts → abort, correct partial summary, non-zero result;
  rerun completes idempotently.
- Strict list: discovery 404 → fatal; empty list → proceeds; CRD-absent+namespace-present
  → fatal; both absent → skip.
- Destination gate: destination missing obs → blocked; with ack flag → proceeds; destination
  has obs → passes without flag; flag with passing gate → rejected.
- Collection module/helper tests:
  - expected UID is deleted successfully and returns `changed: true` only after bounded
    completion and final confirmed absence;
  - a replacement created before DELETE produces a server-side precondition failure and
    survives;
  - disappearance before DELETE returns `changed: false` only after confirmed absence;
  - the same name with a different UID during polling fails immediately and survives;
  - HTTP 409 and 412 are fatal and never fall back to a name-only delete;
  - check mode performs the live read/UID validation without DELETE, reports
    `changed: false`, and reports `would_change` accurately;
  - already absent reports `changed: false`, while successful accepted-and-completed
    deletion reports `changed: true`;
  - GET 404 is distinguished from discovery, authorization, transport, timeout, and decode
    failures;
  - explicit kubeconfig and context reach client construction and no ambient routing is
    used;
  - request, poll, and total wait budgets are bounded; same-UID timeout fails;
  - injected API errors containing kubeconfig text, bearer tokens, client certificates,
    private keys, response headers/bodies, and Secret material are redacted from module
    results, failure messages, and callback-visible output.
- Collection role/static-contract tests prove MCO, MCH, and ManagedCluster coverage,
  durable per-resource UID/phase checkpointing before DELETE, execute-mode checkpoint
  enforcement, selector-scoped pod waits, post-drain re-reads, sanitized failure tasks,
  and the destination-observability gate.
- Version bump per repo policy (Python + collection, synced).

## Tracker updates (same PR)

| id | severity | summary |
| --- | --- | --- |
| new-C1 | High | MCH completion fails open: warns on lingering pods, never re-checks CR, reports success |
| new-C2 | High | Interactive refusals of destructive substeps still return overall success |
| new-C3 | Medium | No server-side UID-preconditioned DELETE or CR-absence proof for MCO/MCH/ManagedCluster deletion; pod waits unscoped |
| new-C4 | Medium | 404→[] makes missing discovery indistinguishable from empty inventory in decommission |
| new-C5 | Medium | No destination-observability check before source MCO deletion (metrics continuity) |

Plus one planned slice row referencing this design. `SSA-02` cross-referenced as
complementary (target identity/RBAC vs. completion/readiness).

## Acceptance criteria

1. Every MCO, MCH, and ManagedCluster deletion durably records its immutable target UID
   before DELETE and is server-side UID-preconditioned to that recorded value in both
   form factors. Reruns never rebind to a same-name replacement. Decommission cannot
   report success while any CR of a targeted name still exists — same-UID survivors and
   different-UID replacements are both fatal through the completion boundary.
2. A refused substep yields a non-zero result and an accurate summary.
3. Missing API discovery aborts before any deletion decision that depends on the list.
4. Integrated decommission with source observability and a destination without it fails
   closed absent the acknowledgement flag.
5. Existing Hive `preserveOnDelete` behavior unchanged.
