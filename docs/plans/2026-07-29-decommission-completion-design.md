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
6. **MCH operator-pod identity is name-only and spoofable.** Python removes every Pod
   whose name starts with `ACM_OPERATOR_POD_PREFIX` from the drain-blocking set
   (`modules/decommission.py:420-434`); the collection applies the equivalent
   `^multiclusterhub-operator` regular expression
   (`roles/decommission/tasks/delete_multiclusterhub.yml:33-79`). Neither path validates
   labels or controller ownership. A bare, Job-owned, replacement-owned, or otherwise
   unrelated Pod can use that prefix, evade the drain proof, and help the MCH completion
   path report success. The current tests preserve this gap by representing operator Pods
   with names only (`tests/test_decommission.py:648-683`) and by requiring the collection
   prefix expression
   (`ansible_collections/tomazb/acm_switchover/tests/unit/test_ansible_resilience_contracts.py:476-483`).

## Goals

1. Every MCO, MCH, and ManagedCluster deletion is bound to the observed object identity by
   a server-side UID precondition, then proven complete before the step reports success.
2. A refused destructive substep can never produce a successful decommission.
3. Decommission inventory reads distinguish empty from missing-API.
4. Integrated decommission cannot silently end observability continuity.
5. A Pod is excluded from the MCH drain set only when a complete controller-owner chain
   binds it to the exact, durably recorded ACM operator Deployment UID. Names, prefixes,
   labels, service accounts, annotations, and container images never establish identity.

## Non-goals

- Wrong-target/UID-expectation and RBAC recheck gates: planned `SSA-02`.
- Generalizing strict-list semantics across the whole codebase (scoped to decommission
  reads here; a broader migration can follow later).
- Preconditioned deletes beyond `V1DeleteOptions.preconditions.uid` (no resourceVersion
  preconditions; UID identity binding is the contract, consistent with the auto-import
  design's preconditioned ConfigMap delete).
- Changes to the Hive `ClusterDeployment preserveOnDelete` safety check (kept as is).
- Treating a hardcoded Deployment name, Pod prefix, label selector, service account,
  annotation, image, or current-lab observation as a stable ACM-version contract.
- Implementing the owner-chain classifier or changing RBAC, tests, role defaults,
  manifests, Helm, collection packaging, or production code in this docs-only PR.

## Design

### 1. Per-resource completion proof

Shared pattern for MCO and MCH (and per-cluster for ManagedClusters):

1. Read the CR and the durable teardown record together. Absent → clean no-op **only when
   no teardown record exists for this exact API-version/kind/namespace/name key, no prior
   mutation was attempted, and therefore no delete, CR-absence, drain, or completion
   obligation exists**. When present, durably record `metadata.uid` as the resource's
   immutable `expected_uid` together with its teardown phase **before DELETE**
   (`delete_started` → `cr_absent` → `drain_pending` → `drained` → `completed`). The state shape is keyed by API
   version/kind/namespace/name and contains both `expected_uid` and `phase`;
   ManagedClusters therefore retain one record per name. A rerun must reuse the recorded
   UID and must never replace it with a fresh same-name observation. If the live name now
   has another UID, fail before DELETE and leave the replacement intact. A rerun that
   finds the CR, CRD, or namespace absent but has any non-terminal record must continue
   from that record's phase; absence never resets the state machine or creates a clean
   skip. In particular, `delete_started`, `cr_absent`, and `drain_pending` must still
   complete the remaining pod-drain and final-verification obligations — a prior run's
   drain timeout cannot be laundered into success by the CRD, CR, or namespace
   disappearing in between.
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
4. Durably record `drain_pending`, then run the pod-drain wait, scoped by label selector:
   - MCO: `observability.open-cluster-management.io/name=observability` (already used at
     `modules/post_activation.py` on this branch — promote to a shared constant).
   - MCH: strictly list every Pod in the fixed ACM namespace. Exclude only Pods whose
     complete controller ownership resolves as
     `Pod → ReplicaSet → exact recorded operator Deployment UID` under the §1a contract.
     A Pod name or prefix is never an exclusion rule. Every unproven Pod remains a drain
     obligation, including a prefixed Pod.
5. Record `drained` only after the bounded selector/identity-scoped pod check proves
   empty.
   Then perform the final completion verification: re-read the CR/CRD and perform one
   final identity-aware pod classification before recording `completed`. A CR recreated
   during or after the drain (any UID), a newly visible drain-blocking Pod, a replacement
   operator Deployment, or an unverifiable final read is fatal — replacements and drain
   regressions stay fatal through the completion boundary, matching the collection's
   post-wait re-list.

   **What `completed` actually asserts.** The final reads and the durable `completed`
   write are separate operations, and no compare-and-swap exists across a CR, a Pod list, a
   Deployment, and a namespace — a reservation barrier spanning four unrelated resources is
   not available in the Kubernetes API and this design does not invent one. So the guarantee
   is stated at the strength it actually has rather than overclaimed: **`completed` records
   that the teardown was proven complete at the instant of the final read**, together with
   the `observed_at` timestamp and the resourceVersions the proof was taken from. It does
   not assert that nothing was recreated afterwards, and nothing downstream may treat it as
   though it does.

   Two consequences follow, and both are binding:

   - The `completed` write carries the final read's `observed_at` and per-resource
     `resourceVersion` values, so a later consumer can see exactly what was proven and
     when.
   - **Integrated teardown requires a fresh live gate**, not the stored `completed` record.
     Before any subsequent destructive step relies on this teardown being complete, it
     re-runs the CR-absence and identity-aware Pod checks against live state. A `completed`
     record is necessary but never sufficient for a later destructive decision, and a
     replacement appearing after the completion write is caught by that gate rather than
     being masked by the stored proof.
6. Timeout at any stage, a still-present same-UID CR, or inability to obtain any remaining
   phase proof → `SwitchoverError` and, where the response is ambiguous, durable
   `recovery_required`. The MCH
   warn-and-return-success path (`:449-455`) is removed.

The phase contract is exact for MCO and MCH:

| durable phase | evidence already owned | required rerun work before the next phase |
| --- | --- | --- |
| no record | none | apply the §3 clean-skip rules or capture the live UID before mutation |
| `delete_started` | immutable resource key + `expected_uid`; delete may or may not have been accepted | reuse the key/UID, classify the live CR/CRD strictly, retry only the UID-preconditioned delete when the same UID remains, and prove CR absence |
| `cr_absent` | target CR absence positively proved | preserve the record and enter `drain_pending`; never return through initial inventory skip |
| `drain_pending` | CR absence proof plus outstanding drain obligation | continue the bounded relevant-pod check, including explicit namespace classification |
| `drained` | bounded drain check proved empty | repeat the final CR/CRD absence and relevant-pod-empty checks; only then write `completed` |
| `completed` | complete CR-absence + drain + final-verification proof | revalidate the final absence/empty predicates idempotently; a replacement, new pod, or unreadable API blocks rather than being ignored |
| `recovery_required` | a remaining proof was ambiguous or unobtainable | no mutation or success transition until a later strict rerun obtains the missing proof or an explicit repair workflow is designed |

Phase changes are forced durable. A phase is never inferred from the current name-based
inventory alone, and no rerun overwrites the recorded resource key or `expected_uid`.
A record with a missing/empty resource-key component, missing/empty `expected_uid`, or
unknown/invalid phase is malformed and fails closed before any mutation or clean-skip
decision.

#### 1a. MCH operator Deployment provenance and Pod identity

The operator Deployment identity is captured **before the MCH DELETE** and bound to the
same teardown record as the target MCH UID. Runtime discovery, rather than a Pod-name
heuristic, establishes provenance:

1. Through the explicit source/primary-hub client, strictly list
   `operators.coreos.com/v1alpha1` `ClusterServiceVersion` objects in the exact ACM
   namespace. Only a `Succeeded` CSV whose
   `spec.customresourcedefinitions.owned[].name` contains exactly
   `multiclusterhubs.operator.open-cluster-management.io` is a candidate.
2. Require exactly one candidate and a structurally valid
   `spec.install.spec.deployments` containing exactly one declared operator Deployment.
   Missing CSV discovery, zero/multiple candidates, zero/multiple install Deployments,
   missing fields, or malformed data means operator identity is unavailable; no
   Deployment or Pod is guessed from a name, label, selector, service account, image,
   annotation, or namespace.
3. Strictly GET that exact `apps/v1` Deployment in the CSV/MCH namespace and require a
   non-empty `metadata.uid`. Persist the following in the MCH teardown record with one
   forced-durable write before DELETE:

   ```yaml
   operator_deployment:
     namespace: "<exact namespace>"
     name: "<CSV install-strategy deployment name>"
     uid: "<live Deployment metadata.uid>"
     discovery_method: "olm_csv_owned_mch_crd_install_deployment_v1"
     captured_at: "<iso8601>"
     csv:
       namespace: "<exact namespace>"
       name: "<live CSV name>"
       uid: "<live CSV metadata.uid>"
       owned_crd: "multiclusterhubs.operator.open-cluster-management.io"
     mch_teardown_key: "<apiVersion/kind/namespace/name>"
     mch_expected_uid: "<recorded MCH metadata.uid>"
   ```

   Every string is required and non-empty; the timestamp must parse as ISO 8601; the
   teardown key/UID must exactly equal the enclosing MCH record. The Deployment name is a
   locator, while its UID is the immutable identity. A later same-name Deployment with
   another UID is never adopted.
4. If identity cannot be established before DELETE, durably persist — with the same
   forced-durable write contract as step 3 — a complete
   `operator_identity_unavailable` outcome in the MCH teardown record before DELETE:

   ```yaml
   operator_identity_unavailable:
     reason: "<stable reason code>"  # e.g. csv_discovery_failed, csv_ambiguous,
                                     # install_deployment_ambiguous, deployment_read_failed
     discovery_method: "olm_csv_owned_mch_crd_install_deployment_v1"
     captured_at: "<iso8601>"
     mch_teardown_key: "<apiVersion/kind/namespace/name>"
     mch_expected_uid: "<recorded MCH metadata.uid>"
     evidence_summary: "<sanitized one-line summary; no raw API bodies or secrets>"
   ```

   Exactly one of the two outcomes — the step-3 `operator_deployment` identity or this
   `operator_identity_unavailable` record — is durably persisted before the MCH
   DELETE; a record containing both, neither, or a partial shape is malformed and
   fails closed. If the durable write of either outcome fails, the MCH DELETE is not
   issued. (Execute mode only: dry-run/check mode performs the same discovery
   read-only, sends no DELETE, and persists nothing, per §5/§6.) Reruns reuse the
   persisted outcome immutably: an `unavailable` outcome is never silently upgraded by
   later rediscovery within the same teardown record, and a recorded Deployment
   identity is never overwritten (§5). Under the `unavailable` outcome no Pod may be
   excluded as operator-owned. A strict, verified empty Pod
   list may still satisfy the drain predicate; any remaining Pod blocks. Discovery,
   authorization, TLS, timeout, transport, decode, and malformed-response failures are
   errors, never empty evidence.

This discovery contract follows OLM's authoritative
[CSV install-strategy model][olm-csv]: a CSV owns CRDs and declares the Deployments OLM
installs. It does **not** claim that the
current Deployment name is stable. As supplementary supported-range evidence, the
official `stolostron/multiclusterhub-operator` CSV bundles were inspected at exact
release-branch commits for ACM 2.11 through 2.17:
[`2.11`][mch-211], [`2.12`][mch-212], [`2.13`][mch-213],
[`2.14`][mch-214], [`2.15`][mch-215], [`2.16`][mch-216], and
[`2.17`][mch-217]. Each inspected CSV owns the exact MCH CRD and declares one install
Deployment. That audit justifies the strict runtime provenance method for the currently
represented ACM range; it does not turn the observed name, labels, or CSV layout into a
future-version promise. An implementation must fail closed when a live supported
installation does not satisfy the runtime contract rather than silently fall back to
the historical name.

During **every** MCH drain pass, including the final post-drain verification:

1. Strictly list all `v1` Pods in the ACM namespace under the bounded overall drain
   deadline.
2. For each Pod proposed for exclusion, require exactly one
   `metadata.ownerReferences[]` entry with `controller: true`, `apiVersion: apps/v1`,
   `kind: ReplicaSet`, and non-empty `name` and `uid`. Missing, multiple, malformed, or
   other-kind controller references make the Pod drain-blocking.
3. Strictly GET that `apps/v1` ReplicaSet in the Pod namespace. Require its live
   `metadata.uid` to equal the Pod owner-reference UID, then require exactly one
   controller owner reference with `apiVersion: apps/v1`, `kind: Deployment`, and
   non-empty `name` and `uid`.
4. Strictly GET that Deployment. Require its live UID to equal the ReplicaSet
   owner-reference UID **and** its namespace/name/UID to equal the durably recorded
   operator Deployment identity. The recorded Deployment is also re-read even when no
   Pod is proposed for exclusion; 404, UID replacement, or an unverifiable read after
   identity capture is a recovery-required inconsistency, not an empty-drain proof.
   One entailed exception: when the fixed ACM namespace itself is **positively
   absent** under the §3 namespace rules, the recorded namespaced Deployment cannot
   exist either — its absence is entailed by the proven namespace absence, is not a
   recovery-required inconsistency, and the namespace-absence proof stands in for
   both the pod-empty predicate and this re-read. An unreadable or ambiguous
   namespace state never triggers this exception (§3 records `recovery_required`).
5. Only after every check succeeds is that Pod excluded. All other Pods remain
   drain-blocking regardless of name. A bare/unowned, Job-owned, StatefulSet-owned,
   unrelated-ReplicaSet-owned, replacement-Deployment-owned, or malformed-reference Pod
   whose name begins with `multiclusterhub-operator` therefore blocks completion.
6. Multiple ReplicaSets during a rolling update are accepted only when each complete
   owner chain resolves independently to the exact same recorded Deployment UID. A
   non-prefixed Pod with that exact chain is operator-owned; classification is by
   identity, not appearance.

Reads may be memoized within one pass by exact `(namespace, kind, name, uid)` key, but
never across passes without a new live verification. Every API call has an explicit
request timeout, the poll uses a monotonic bounded deadline and bounded interval, and a
read/list failure does not consume a retry as an empty or non-operator result. Public
errors contain only a stable stage/reason code and sanitized resource identity; they
must not include raw API bodies, response headers, kubeconfig/client configuration,
tokens, certificates, keys, Secret data, or credential-bearing exception strings.

Kubernetes defines an [object UID][kubernetes-object-uids] as cluster-lifetime identity,
defines [controller owner references][kubernetes-owner-references] with both name and
UID, and documents the
[Deployment][kubernetes-deployments] → [ReplicaSet][kubernetes-replicasets] → Pod
controller relationship. The owner chain above uses those API identities rather than
workload naming conventions.

[kubernetes-object-uids]: https://kubernetes.io/docs/concepts/overview/working-with-objects/names/#uids
[kubernetes-owner-references]: https://kubernetes.io/docs/concepts/overview/working-with-objects/owners-dependents/
[kubernetes-deployments]: https://kubernetes.io/docs/concepts/workloads/controllers/deployment/
[kubernetes-replicasets]: https://kubernetes.io/docs/concepts/workloads/controllers/replicaset/
[olm-csv]: https://olm.operatorframework.io/docs/concepts/crds/clusterserviceversion/
[mch-211]: https://github.com/stolostron/multiclusterhub-operator/tree/02653efdc47ad4e5fcb54f97755642509508b059
[mch-212]: https://github.com/stolostron/multiclusterhub-operator/tree/8d49d669fb95dae23c7adadb388fd7fce81c1b14
[mch-213]: https://github.com/stolostron/multiclusterhub-operator/tree/eda1d638f7efce547e89065458c18063d139cb90
[mch-214]: https://github.com/stolostron/multiclusterhub-operator/tree/87aac4828fd1ae292132ed3ec55eb231d56b482e
[mch-215]: https://github.com/stolostron/multiclusterhub-operator/tree/a343ce463a6ed69933b32a101b355db9deae63b0
[mch-216]: https://github.com/stolostron/multiclusterhub-operator/tree/41c1a913da8635da72b5a0c455f0a359393c97b4
[mch-217]: https://github.com/stolostron/multiclusterhub-operator/tree/9882318a207332076ee52f49bb38562e0b1ef1c5

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

### 3. Shared strict inventory primitive

Add `list_custom_resources_strict(...)` (or a `strict=True` flag on the existing method)
in `lib/kube_client.py` that separates an object-list 404, positive CRD absence, positive
namespace absence, a genuine empty list, and an API/discovery/read failure instead of
collapsing them to `[]`.

This is **one shared primitive, not a decommission-private helper.** The migration
evidence design (`2026-07-29-migration-evidence-design.md` §3) consumes the same contract
for ManagedCluster inventory and Velero backup reads, and both designs must not diverge
into two subtly different strict-list behaviours. The contract below is authoritative for
every consumer; §3 of the migration design references it rather than restating it.

#### Shared strict-list contract

- **Supported operations.** Exactly two: a namespaced or cluster-scoped **list** of one
  custom-resource kind (optionally selector-scoped), and a **GET by name** of one such
  resource. Nothing else — no watch, no aggregation across kinds, no implicit fallback to
  a different API group or version.
- **Explicit kubeconfig and context.** Every call is made through an explicitly supplied
  client whose kubeconfig and context are named by the caller. There is no ambient or
  default-context resolution: a decommission read against the old hub and a migration read
  against the destination hub are distinguishable at the call site and in the result.
- **Pagination is mandatory and complete.** Lists are read through the API server's
  `continue`/`limit` paging until the server reports no further continuation. A response
  carrying a `continue` token that the caller does not follow is an incomplete read, not a
  short list. An expired continue token restarts the read; it never truncates it.
- **Complete-response validation.** The decoded response must carry the expected list
  shape (`items` present and a list, `metadata` a mapping). A structurally invalid or
  undecodable response is a read failure, never an empty inventory.
- **Outcome algebra.** Every call resolves to exactly one of: `items` (possibly a genuine
  empty list, positively proven complete), `crd_absent` (positive discovery-level absence
  of the API group/resource), `namespace_absent` (positive absence of the named
  namespace), `object_absent` (a named resource proven absent), or `error`.
  `crd_absent`, `namespace_absent`, and `object_absent` are positive *absence proofs*;
  `error` is not, and no consumer may read an `error` as absence or as an empty list.
- **A 404 is not self-describing, and the two 404s must not be conflated.** A 404 returned
  while *resolving* the API group/version/resource means the kind is not served; a 404
  returned by a successful request *for a named object* of a served kind means that object
  does not exist. They are distinguished by where they occur, never by the status code
  alone:
  - `object_absent` is returned **only** when discovery first succeeded — the
    group/version/resource resolved and the kind is served — **and** the subsequent object
    GET, issued through the explicitly selected client, returned 404.
  - A 404 (or any failure) during discovery itself yields `crd_absent` only when it is a
    *positive* determination that the resource is not served — the API server answered and
    the group/resource is genuinely absent from its resource list. A discovery call that
    times out, is unauthorized, is unreachable, or returns an unparseable resource list is
    **`error`**, not `crd_absent`: an unserved kind and an unreachable API server are
    indistinguishable by status code and must not be treated alike.
  - A 404 from a *list* on a served kind in an existing namespace is not absence of the
    kind; it is `error` unless the namespace itself is positively proven absent, in which
    case `namespace_absent` applies.
  Implementations therefore record which phase produced the 404 and never infer the
  outcome from the status code in isolation.
- **Authorization, discovery, timeout, transport, and decode failures are `error`** — the
  403/401 case explicitly included, so a missing RBAC rule can never present as "nothing
  is there".
- **No silent partial aggregation.** The Collection implementation must not use
  `failed_when: false`, `ignore_errors`, or a `default([])` filter to absorb a failed
  read, and must not merge a partially-paged result into an inventory. A failed page fails
  the whole read.
- **Bounded calls.** Every call has a bounded retry/timeout budget stated by the caller;
  exhausting it is `error`, never absence. The primitive itself never polls — bounded
  polling loops live in the consumers, which repeat whole strict reads.
- **Sanitized errors.** Error results carry the api group/version/kind, namespace, name or
  selector, and a stable reason code — never response bodies, tokens, kubeconfig data, or
  resource contents.
- **No mutation.** The primitive issues read-only requests only.
- **Ownership.** Python owns `lib/kube_client.py::list_custom_resources_strict`; the
  Collection owns an independent module/helper with the identical outcome algebra. They
  share no code and are held equal by parity fixtures.
- **Tests are shared across consumers.** One vector set covers the outcome algebra,
  pagination completeness, and the error-is-not-absence rule, and is exercised by both the
  decommission and migration-evidence suites in both form factors — not duplicated with
  divergent expectations.

Decommission's own uses of the shared primitive:

- `_delete_managed_clusters` inventory read: missing `cluster.open-cluster-management.io`
  discovery → fatal ("cannot verify inventory"), genuine empty list → clean "nothing to
  delete".
- MCO initial lookup with **no teardown record**: CRD absent **and** observability
  namespace positively absent → clean skip; CRD absent but namespace present → fatal
  (inconsistent cluster — refuse to guess). A present CRD with a genuine empty list is a
  clean no-op only when there is no record. Authorization, discovery, timeout,
  transport, decode, or other list/namespace errors are fatal, never absence.
- MCH initial lookup with **no teardown record**: the identical rule using the MCH CRD
  and ACM namespace.
- MCO/MCH lookup with **any teardown record**, including `delete_started`, `cr_absent`,
  `drain_pending`, `drained`, `completed`, or `recovery_required`: the clean-skip branch
  is unavailable. Reuse the recorded resource key and `expected_uid`, preserve the
  durable phase, and execute the remaining phase-table work. A positively absent CRD is
  evidence that its CR cannot currently be served, but it does not satisfy the separate
  drain/final-verification obligations. If the CRD is present, a strict GET/list must
  prove the named CR absent or the same UID; a different UID is a replacement and
  blocks. An API error is never CRD/CR absence.

Namespace handling during a recorded drain is equally strict. First GET the fixed drain
namespace by name:

- a readable present namespace requires the bounded MCO selector-scoped list or the MCH
  strict all-Pod list plus §1a owner-chain classification;
- a positive namespace 404 may count as an explicit verified-empty pod result for these
  two resources only because this design defines the complete relevant drain sets as
  MCO pods in `open-cluster-management-observability` and all MCH Pods in
  `open-cluster-management` except those proven through §1a to belong to the exact
  recorded operator Deployment; namespaced pods cannot exist under a namespace the API
  positively proves absent;
- if an implementation or supported topology cannot establish that the relevant pods
  are confined to that fixed namespace, namespace absence instead records
  `recovery_required` and blocks completion;
- authorization, discovery, timeout, transport, malformed response, and all other
  namespace/pod-list failures record no empty proof and fail closed.

The final completion check repeats the applicable strict CRD/CR-absence predicate and
the namespace-present/empty-list or positively-absent-namespace predicate. Only their
joint success permits `completed`.

### 4. Destination observability gate

Immediately before the source MCO deletion substep (not merely at
`_decommission_old_hub` entry — no intervening mutations between check and delete):

1. **Fresh source-hub read first.** Whether source observability exists is re-established
   by a strict read at this point, never carried from the preflight
   `primary_has_observability` boolean — preflight ran before every other teardown
   substep, and observability can have been removed or added since. The source read covers
   the MCO CR and the observability namespace through the shared §3 primitive.
   - Source observability positively **absent** (both the CR and the namespace proven
     absent) → there is nothing to delete and nothing whose continuity could end; the gate
     is not applicable and the substep is a clean no-op.
   - Source observability positively **present** → continue to step 2.
   - Source discovery, CRD, CR, or namespace **missing or unverifiable** — any `error`
     outcome, or a mixed state such as an absent CRD with a present namespace → **block**.
     An unverifiable source is never read as "nothing to delete".
2. Fresh destination-hub check via the secondary client — MCO CR exists (strict list) and
   observability namespace present. The preflight boolean is not trusted, and the source
   lookup's clean-skip rule (§3) is **not** reused here: on the destination, missing
   discovery, missing CRD, missing CR, or missing namespace all block equally.
3. Fail closed, distinguishing the two reasons because they demand different operator
   responses:
   - destination observability **positively proven absent** → "source observability will
     be deleted but destination hub has no observability — metrics continuity ends here";
   - destination observability **unverifiable** (timeout, authorization failure, wrong-hub
     read, missing discovery, unparseable response, or any other `error`) → "cannot verify
     destination observability". This is never reported as, or treated as, the destination
     having no observability.
4. Proceed only with a new explicit flag `--acknowledge-observability-not-migrated`
   (final name at implementation; validated like other acknowledgement flags). The
   acknowledgement is accepted **only after the destination has been positively verified
   to have no observability** — that is, only against the first failure reason above. It
   can never override an unverifiable destination: an operator acknowledging "not
   migrated" is asserting a known fact about the destination, and when the tool could not
   read the destination at all there is no such fact to acknowledge, so the run stays
   blocked regardless of the flag. The flag is also rejected when the gate would pass
   anyway (no stale acknowledgements).

Standalone decommission (`--decommission`) is unaffected — it has no destination client.
Collection: the decommission role performs the same gate when a destination
kubeconfig/context is provided; a boolean ack variable mirrors the flag.

### 5. Python behavior

- `modules/decommission.py` uses the explicitly selected primary/source `KubeClient` for
  CSV, Deployment, ReplicaSet, Pod, namespace, and MCH reads. No ambient/default client
  or destination client is accepted for source teardown evidence.
- Before MCH DELETE, the Python teardown record receives the forced-durable §1a operator
  identity or the explicit unavailable reason. Reruns reuse it; they never re-discover
  and overwrite a recorded Deployment UID.
- A narrowly scoped owner-chain helper performs strict typed Deployment, ReplicaSet, and
  Pod reads with per-request timeouts, per-pass exact-identity caching, and the existing
  bounded monotonic wait budget. Prefix-only filtering is removed; the prefix constant
  may remain only as a supplementary diagnostic consistency check.
- Any missing, malformed, ambiguous, unauthorized, timed-out, TLS/transport-failed, or
  decode-failed read is a failed proof. A recorded Deployment 404 or UID replacement
  enters the existing `recovery_required` completion path; no later Pod or Deployment is
  adopted.
- The final `drained` → `completed` transition performs a fresh identity-aware pass.
  Logging/reporting distinguishes `operator_owned`, `drain_blocking`,
  `operator_identity_unavailable`, and `operator_identity_inconsistent` without claiming
  completion from a warning.
- Python dry-run performs the strict provenance and owner-chain reads and reports the
  predicted blocker set, but sends no DELETE, persists no authoritative teardown
  transition, and does not claim actual change. Stable public output is sanitized as in
  §1a.

### 6. Collection parity

- `roles/decommission/tasks/delete_observability.yml`,
  `delete_multiclusterhub.yml`, and `delete_managed_clusters.yml` route MCO, MCH, and each
  ManagedCluster through `acm_uid_guarded_delete`; no name-only `state: absent` task remains
  for those resources.
- Every invocation passes explicit primary-hub kubeconfig/context, API version, kind,
  namespace where applicable, name, observed UID, and bounded wait values.
- MCO waits gain the scoped selector. MCH tasks strictly read
  `operators.coreos.com/v1alpha1` CSVs, `apps/v1` Deployments and ReplicaSets, and `v1`
  Pods through the exact primary-hub kubeconfig/context, persist the §1a identity in
  checkpoint `operational_data`, and classify the complete owner chain identically to
  Python. No prefix-only bypass or ambient client fallback remains.
- A collection-owned module/module_utils boundary is preferred for owner-chain
  classification so Jinja name filtering cannot become the safety decision. It accepts
  explicit kubeconfig/context and the recorded Deployment namespace/name/UID, applies
  bounded request/wait budgets, and returns sanitized per-Pod reason codes.
- MCO and MCH tasks load the durable record before initial inventory classification and
  implement the same phase table and no-record-only clean skip as Python. A 404 or empty
  result cannot bypass an existing `delete_started`/`cr_absent`/drain obligation, and a
  same-name Deployment replacement cannot be adopted.
- Roles are non-interactive; refusal semantics don't apply, but failed/partial status
  parity with Python must hold. No `failed_when: false` is allowed on provenance,
  ownership, wait, or final-verification paths.
- Native check mode performs the same read-only identity validation, sends no DELETE,
  writes no authoritative checkpoint transition, reports `changed: false`, and publishes
  prediction separately as `would_change`. Execute-mode `changed: true` is possible only
  after the intended mutation and full completion proof; a read/classification failure
  never reports a successful change.
- Module arguments and callback-sensitive tasks use `no_log: true`; a following
  fixed-text task exposes only sanitized stage/reason/identity fields. The collection
  remains independent and must not import Python CLI code.

### 7. RBAC implementation implications

Current decommission RBAC is **not sufficient** for the §1a implementation. Both
`lib/rbac_validator.py` and
`plugins/modules/acm_rbac_validate.py` currently require namespace `get` plus Pod
`get/list`; the root/bundled/Helm ACM-namespace Roles likewise grant Pod `get/list` and
MCH `list`. They do not grant ACM-namespace reads for Deployments, ReplicaSets, or CSVs.

The least-privilege future permission set is:

| API/resource in the ACM namespace | required verbs | reason |
| --- | --- | --- |
| core `pods` | `list` (`get` may remain because it is already granted) | strict complete drain inventory and owner references |
| apps `replicasets` | `get` | resolve the exact Pod controller reference by name and validate its UID |
| apps `deployments` | `get` | capture and revalidate the CSV-declared Deployment and its UID |
| operators.coreos.com `clusterserviceversions` | `get`, `list` | discover the unique owning CSV, then revalidate its exact provenance |
| core `namespaces` | `get` | existing fixed-namespace presence/absence proof |

`list` is not required for Deployments or ReplicaSets when the implementation follows the
exact CSV/owner-reference locators, and `watch` is not required for any of these
resources because bounded polling repeats strict list/GET operations. Do not add those
verbs speculatively. If implementation chooses a batch-list optimization, its separate
least-privilege and completeness case must be reviewed before adding `list`.

The future implementation PR must update all RBAC surfaces together:
`lib/rbac_validator.py`,
`ansible_collections/tomazb/acm_switchover/plugins/modules/acm_rbac_validate.py`,
decommission/preflight/bootstrap task wiring, `deploy/rbac/`, the collection-bundled
`roles/rbac_bootstrap/files/deploy/rbac/` copies,
`deploy/helm/acm-switchover-rbac/`, the RBAC deployment/requirements/implementation docs,
and Python/collection parity, manifest, Helm, and negative authorization tests. Negative
tests must independently deny Pod list, CSV list/GET, ReplicaSet GET, Deployment GET, and
namespace GET and prove each denial blocks before DELETE/completion with sanitized
output. No RBAC file is changed by this design-only PR.

## Testing

- Finalizer-stuck MCO (CR persists past timeout) → `SwitchoverError`, decommission halts.
- Rerun after drain timeout with CR now absent → pod-drain wait still runs before
  `drained`; teardown-phase record drives it.
- Crash/rerun after `delete_started` with a same-name replacement → the recorded UID is
  reused, mismatch fails before DELETE, and the replacement survives; tests prove neither
  form factor re-captures the replacement UID.
- Python: expected UID deletion succeeds; server-side 409/412 precondition conflict is
  fatal; replacement UID mid-poll is fatal and survives.
- MCH: lingering CR → fatal; every Pod in the fixed ACM namespace remains
  drain-blocking unless the complete §1a identity proof excludes it.
- Refusal at each of the three prompts → abort, correct partial summary, non-zero result;
  rerun completes idempotently.
- Strict list: discovery 404 → fatal; empty list → proceeds; CRD-absent+namespace-present
  → fatal; CRD and namespace positively absent with no prior record → clean skip.
- Phase-aware strict-list matrix for both MCO and MCH: no prior record + CRD/namespace
  absent; `delete_started` + CRD absent; `cr_absent` + namespace absent;
  `drain_pending` + pods still present; prior record + CRD/namespace/pod-list API
  failure; and `drained` + final verification failure. Every prior-record case preserves
  the resource key/expected UID and phase, and none enters the clean-skip result.
- Namespace absence is accepted as verified empty only after a positive namespace 404
  and proof that the relevant MCO/MCH pod set is confined to the fixed namespace;
  unreadable or ambiguous namespace state becomes `recovery_required`.
- Completed-proof ordering: `drained` is written only after the bounded pod check
  succeeds, and `completed` only after the final CRD/CR and pod/namespace verification;
  injected failure at either boundary proves no premature terminal write.
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
  enforcement, MCO selector scoping, MCH identity-bound owner-chain classification,
  post-drain re-reads, sanitized failure tasks, and the destination-observability gate.
- Changelog entry under `CHANGELOG.md` `## [Unreleased]` per the repository's Version
  Management policy. The implementation slice is ordinary development work and does not
  change released version identifiers or create a release tag; the synchronized
  Python/collection bump belongs to a later explicitly scoped release PR.

### MCH operator-identity matrix

The same fixtures and expected reason codes run against Python and the collection; parity
tests compare the classification and mutation/no-mutation result directly:

1. A real operator Pod owned through
   `Pod → ReplicaSet → recorded Deployment UID` is excluded from the drain-blocking set.
2. A bare Pod whose name has the `multiclusterhub-operator` prefix blocks.
3. A Job-owned prefixed Pod blocks.
4. A StatefulSet-owned prefixed Pod blocks.
5. A prefixed Pod owned by an unrelated ReplicaSet blocks.
6. A Pod owned by a ReplicaSet whose Deployment has the expected name but a different UID
   blocks and the replacement is not adopted.
7. A non-prefixed Pod owned by the exact recorded operator Deployment is classified by
   ownership and excluded.
8. Multiple ReplicaSets from a rolling update are accepted only when every accepted
   chain resolves to the exact recorded Deployment UID.
9. A missing controller owner reference blocks.
10. A malformed or ambiguous controller owner reference blocks.
11. ReplicaSet GET 404, authorization failure, or other API failure blocks.
12. Deployment GET 404, authorization failure, replacement UID, or other API failure
    blocks after identity capture.
13. Pod-list discovery, authorization, TLS, timeout, transport, or decode failure blocks
    and is never converted to an empty list.
14. Operator Deployment unavailable before DELETE plus a strictly verified zero-Pod list
    is a verified empty drain and records the unavailable reason.
15. Operator Deployment unavailable before DELETE plus any Pod blocks; no Pod is
    excluded.
16. Deployment replacement during the drain blocks and enters
    `recovery_required`; no later Deployment is adopted.
17. Prefix-only mutation test: changing only a non-operator Pod's name to the operator
    prefix does not change its blocking classification.
18. Python and the collection consume identical fixtures and produce identical
    `operator_owned`/`drain_blocking` decisions and stable reason codes.
19. Python dry-run and collection check mode perform read-only CSV/Deployment/
    ReplicaSet/Pod validation, issue no DELETE, persist no authoritative transition,
    report `changed: false`, and report prediction separately.
20. Success and every failure output omit kubeconfig paths/content, contexts when
    sensitive, tokens, authorization headers, certificates, keys, client configuration,
    raw API bodies/headers, Secret content, and credential-bearing exception strings.

Additional provenance tests cover zero/multiple owning CSVs, a non-`Succeeded` CSV,
zero/multiple install Deployments, wrong owned CRD, missing CSV/Deployment UID, malformed
install strategy, mismatched MCH teardown key/UID, durable capture before DELETE, and
rerun reuse without re-binding. Request, poll, and total wait bounds are asserted in both
form factors. Final-verification tests inject a new unproven Pod or ownership/read failure
after `drained` and prove `completed` is not written.

## Tracker updates (same PR)

| id | severity | summary |
| --- | --- | --- |
| new-C1 | High | MCH completion fails open: warns on lingering pods, never re-checks CR, reports success |
| new-C2 | High | Interactive refusals of destructive substeps still return overall success |
| new-C3 | Medium | No server-side UID-preconditioned DELETE or CR-absence proof for MCO/MCH/ManagedCluster deletion; pod waits unscoped |
| new-C4 | Medium | 404→[] makes missing discovery indistinguishable from empty inventory in decommission |
| new-C5 | Medium | No destination-observability check before source MCO deletion (metrics continuity) |
| new-C6 | Medium | Python and collection exclude MCH Pods by name prefix; an unrelated prefixed Pod can evade drain proof |

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
   closed absent the acknowledgement flag; source observability is re-read fresh
   immediately before deletion rather than taken from preflight, and the acknowledgement is
   accepted only against a **positively verified absent** destination — never against an
   unverifiable one.
5. Existing Hive `preserveOnDelete` behavior unchanged.
6. The MCO/MCH clean skip for absent CRD plus absent namespace is available only when no
   teardown record or prior mutation/drain obligation exists. Every recorded
   `delete_started`, `cr_absent`, `drain_pending`, `drained`, or recovery state resumes
   from its durable resource key/UID and completes the remaining bounded drain and final
   verification contract.
7. A positively absent fixed namespace counts as empty only under the explicit
   fixed-namespace pod-scope proof; API/list errors never count as absence. `drained` and
   `completed` are written only after their full required checks succeed, identically for
   MCO and MCH in Python and the collection.
8. `completed` asserts completeness **at the instant of its final read**, not for all time:
   it carries that read's `observed_at` and per-resource `resourceVersion` values, and no
   consumer treats it as proof of current state. Integrated teardown re-runs the CR-absence
   and identity-aware Pod checks against live state before relying on this teardown being
   complete, so a replacement appearing after the completion write is caught by that gate
   rather than masked by the stored proof.
9. Before MCH DELETE, operator provenance is either durably bound to the enclosing MCH
   teardown record as exact CSV evidence plus Deployment namespace/name/UID, or explicitly
   unavailable. A name, prefix, label, service account, annotation, image, or current-lab
   observation alone never supplies that identity.
10. During every MCH drain and final-verification pass, a Pod is excluded only after
    `Pod owner UID → live ReplicaSet UID → Deployment owner UID → exact recorded
    Deployment UID` succeeds. Every missing, malformed, ambiguous, unauthorized,
    unreadable, timed-out, replaced, or inconsistent link blocks. Rolling-update
    ReplicaSets are accepted only when each resolves to the same recorded Deployment UID.
11. Prefix spoofing cannot change a Pod's classification. If pre-delete operator identity
    is unavailable, only a strictly verified empty Pod list satisfies the drain; any Pod
    blocks. If an already recorded Deployment disappears or is replaced, the completion
    path fails closed or records `recovery_required`.
12. Python and collection semantics, durable fields, bounded reads/waits, dry-run/check
    mode, changed reporting, stable reason codes, redaction, RBAC requirements, and
    negative tests remain in parity without cross-importing implementation code.
