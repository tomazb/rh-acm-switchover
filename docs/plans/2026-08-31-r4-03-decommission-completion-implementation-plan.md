# R4-03 Decommission Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: use `superpowers:subagent-driven-development` for reviewed task-by-task work in one session, or `superpowers:executing-plans` in a separate session. Every builder, independent validator, and PR-comment resolver must also read current `AGENTS.md` and the directly relevant repository authorities before acting.

**Status:** authored against the operator-approved R4-03 amended design at exact head
`177554f6e598461571e3785173b52feabdfe4c52`; awaiting independent validation. **This document
does not authorize runtime implementation.**

**Goal:** Implement the accepted R4-03 decommission-completion design in both production form
factors so that every MCO, MCH, and ManagedCluster teardown is bound to the observed object
identity by a server-side UID precondition and proven complete before it reports success; a
top-level cancellation or refused destructive substep can never yield a successful decommission; inventory reads
distinguish empty from unverifiable; integrated decommission cannot silently end observability
continuity; and MCH drain exclusion is decided by a complete controller-owner chain to a durably
recorded operator Deployment UID rather than a name prefix.

**Architecture:** One shared strict Kubernetes read algebra owned by R4-03 and consumed by
R4-04 — `lib/strict_read.py` plus strict methods on `lib/kube_client.py` in Python, and the
existing `acm_k8s_read_outcome` module extended with an exact canonical APIResource-name input in
the collection. The Python surface includes live Namespace absence, paginated Pod inventory, and
strict Deployment/ReplicaSet identity producers rather than leaving them to later callers.
Durable teardown state flows
through the typed `lib/run_record.py` facade in Python and the `plugins/module_utils/checkpoint.py`
named-operation vocabulary in the collection, with physically independent stores held equal by
parity vectors. `modules/decommission.py` becomes the single Python owner of the MCO teardown
algorithm that `modules/finalization.py` currently duplicates. The two form factors share no
runtime code; parity is maintained deliberately through mirrored constants, shared vectors, and
parity tests.

**Tech stack:** Python 3.10–3.12 CLI, pytest, Kubernetes Python client; Ansible Collection
`tomazb.acm_switchover` on `ansible-core` 2.16–2.21 with `kubernetes.core` 6.x, custom modules
and `module_utils`, YAML roles and playbooks.

**Normative specification:**

- [`docs/plans/2026-07-29-decommission-completion-design.md`](2026-07-29-decommission-completion-design.md) (the "July design")
- [`docs/plans/2026-08-31-r4-03-current-base-design-amendment.md`](2026-08-31-r4-03-current-base-design-amendment.md) at operator-approved exact head `177554f6e598461571e3785173b52feabdfe4c52`

Where the amendment and the July design disagree, the amendment is authoritative; everywhere
else the July design stands. This plan restates neither: it references them and adds only the
executable decomposition.

---

## 1. Title and status

| Field | Value |
| --- | --- |
| Slice | R4-03 — decommission completion proof and destination readiness |
| Findings | R4-C1, R4-C2, R4-C3, R4-C4, R4-C5, R4-C6, GLM-H6 |
| Tracker row | [`thermos-resolution-plan.md`](../../thermos-resolution-plan.md) R4-03 |
| Design base | `origin/ansible` @ `74268192` |
| Approved design head | `177554f6e598461571e3785173b52feabdfe4c52` |
| Plan status | authored, awaiting independent validation |
| Runtime authorization | **not granted** — see §24 |

## 2. Goal

Close R4-C1 through R4-C6 and GLM-H6 in the Python CLI and the Ansible Collection together, so
that after this slice:

1. No decommission substep can report success without a positive completion proof taken from
   live state.
2. No API failure, discovery failure, authorization failure, malformed response, or truncated
   page can present as absence or as an empty inventory in either form factor.
3. No destructive DELETE is issued without a durably recorded, immutable target UID, and every
   DELETE carries a server-side precondition bound to that UID.
4. No Pod is excluded from the MCH drain set on the basis of its name.
5. Integrated decommission blocks before deleting source observability unless the destination
   hub is positively proven to have observability, or the operator acknowledges a positively
   proven absence.
6. Top-level cancellation and an interactive substep refusal remain distinct but both make the
   result unsuccessful and the CLI exit non-zero; callers test `.succeeded` explicitly.
7. The MCO teardown algorithm exists exactly once in Python.

## 3. Authority and approved-design binding

Governing authorities, in `AGENTS.md` hierarchy order:

1. [`AGENTS.md`](../../AGENTS.md) at the then-current `origin/ansible`.
2. The R4-03 tracker row and Area C acceptance requirements in
   [`thermos-resolution-plan.md`](../../thermos-resolution-plan.md), the R4-C1..R4-C6 findings
   table, the GLM-H6 row, and the R4-03 converted implementation-slice obligation from the
   2026-08-02 triage.
3. The July design and the approved amendment named above; the R4-04 amendment and R4-04
   implementation plan as the consumer contract for the shared strict-read primitive; the
   [parity matrix](../ansible-collection/parity-matrix.md),
   [behavior map](../ansible-collection/behavior-map.md),
   [coexistence policy](../../ansible_collections/tomazb/acm_switchover/docs/coexistence.md),
   [compatibility authority](../../ansible_collections/tomazb/acm_switchover/docs/compatibility.md),
   [architecture](../development/architecture.md), [usage](../operations/usage.md),
   [testing](../development/testing.md), and the RBAC authorities.
4. Current source, tests, and exact-head CI.

Every relationship asserted in this plan was verified directly against source and tests at
`74268192`. Generated-graph analysis was considered as a hypothesis source and rejected as
unusable here: the retained `graphify-out/analysis.json` predates this base (2026-08-16), indexes
non-source scratch trees, and surfaces no R4-03 dependency relationship. Under the `AGENTS.md`
evidence rule that generated analysis is a hypothesis generator and never an authority, direct
source tracing is the stronger form of the same obligation and is what this plan is built on.

## 4. Non-authorization statement

Authoring and approving this plan does not authorize runtime implementation. See §24.

## 5. Current architecture snapshot

Facts established at `74268192` that every task below builds against. Each was read directly.

**Python**

| Surface | Current state |
| --- | --- |
| `lib/kube_client.py:691-727` `list_custom_resources` | Delegates to `_list_custom_resources_raw`; `@retry_api_call` |
| `lib/kube_client.py:729-798` `_list_custom_resources_raw` | Drains `continue` tokens to exhaustion; maps list 404 to `[]` at `:776-778`; supports `max_items` truncation |
| `lib/kube_client.py:594-638` `get_custom_resource` | `@api_call(not_found_value=None)` — 404 becomes `None` |
| `lib/kube_client.py:1024-1075` `delete_custom_resource` | `@api_call(not_found_value=True)`; passes no body; honours `self.dry_run` |
| `lib/kube_client.py:157-200` `api_call` | Converts 404 to `not_found_value` before the caller sees it |
| `lib/kube_client.py:1288-1308` `get_pods` | `@api_call(not_found_value=[])`; single `list_namespaced_pod` call, no pagination |
| `lib/kube_client.py:1095-1117` `get_deployment` | `@api_call(not_found_value=None)` |
| `lib/kube_client.py:206-271` `KubeClient.__init__` | Builds `core_v1`, `apps_v1`, `custom_api` from one per-context `ApiClient`; no discovery or dynamic client exists |
| `modules/decommission.py` (455 lines) | Stateless; no `StateManager` reference; three substeps; every refusal falls through to `return True` at `:97-98` |
| `modules/decommission.py:139-143` | Caller-side 404 arm made unreachable by `@api_call(not_found_value=True)` |
| `modules/decommission.py:149-161` | MCO pod wait is namespace-wide with no label selector; MCO CR never re-read after DELETE |
| `modules/decommission.py:174-176` | Empty ManagedCluster list returns "nothing to delete" |
| `modules/decommission.py:419-448` | Non-operator pods remaining only warn; MCH CR never re-read |
| `modules/decommission.py:427` | Prefix exclusion via `startswith(ACM_OPERATOR_POD_PREFIX)` |
| `modules/finalization.py:1003-1088` | Second MCO teardown copy; records GitOps markers at `:1030-1043`; different failure text at `:1084-1088` |
| `modules/finalization.py:211` | `state.step("disable_observability_on_secondary")` caller-side wrapper |
| `modules/finalization.py:1138-1141` | Instantiates `Decommission` and invokes with `interactive=False` |
| `acm_switchover.py:933-976` `run_decommission` | Receives `state` and never uses it |
| `lib/run_record.py:160-178` `RunRecord` | Typed named operations over `StateManager` config keys; `_set`/`_get` are the only raw accessors |
| `lib/constants.py:97` | `ACM_OPERATOR_POD_PREFIX = "multiclusterhub-operator"` |
| `modules/post_activation.py:569` | Observability selector literal `observability.open-cluster-management.io/name=observability` |
| `lib/rbac_validator.py:258-276` | `DECOMMISSION_CLUSTER_PERMISSIONS` and `DECOMMISSION_NAMESPACE_PERMISSIONS` |

**Collection**

| Surface | Current state |
| --- | --- |
| `plugins/modules/acm_k8s_read_outcome.py` | R3-02 seam; one unpaginated read; statuses `ok` / `not_found` / `error`; `supports_check_mode=True`; client via `get_api_client(**module.params)` |
| `roles/decommission/tasks/main.yml:55-76` | Publishes `acm_switchover_decommission_result` with hard-coded `status: pass` |
| `roles/decommission/tasks/main.yml` | No checkpoint or `operational_data` usage anywhere in the role |
| `roles/decommission/tasks/delete_observability.yml:16-29` | Name-only `kubernetes.core.k8s state: absent` |
| `roles/decommission/tasks/delete_multiclusterhub.yml:17-31` | Name-only `state: absent` |
| `roles/decommission/tasks/delete_multiclusterhub.yml:46-53` | `until` applies `default([])` to `resources`; `failed_when: false` |
| `roles/decommission/tasks/delete_multiclusterhub.yml:48,65,76` | `rejectattr('metadata.name', 'match', '^multiclusterhub-operator')` three times |
| `roles/decommission/tasks/delete_managed_clusters.yml:149-161` | Name-only `state: absent` |
| `plugins/module_utils/checkpoint.py:108-125` | `KEY_*` vocabulary owned here; roles read flattened `facts`, never raw keys |
| `plugins/module_utils/klusterlet.py:82,100` | Existing `module_utils` client-factory precedent: `config.new_client_from_config(**kwargs)` |
| `plugins/module_utils/constants.py` | Holds collection constants; has no operator-prefix mirror |
| `plugins/modules/acm_rbac_validate.py:251-268` | Mirrors the Python decommission RBAC tables |

**Manifests and guardrails**

| Surface | Current state |
| --- | --- |
| `deploy/rbac/role.yaml` ACM-namespace operator Role | `pods get,list`; `multiclusterhubs list`. No Deployments, ReplicaSets, or CSVs |
| `deploy/rbac/clusterrole.yaml` operator | `namespaces get,list`; `managedclusters get,list,patch`; `multiclusterhubs get,list`; `multiclusterobservabilities get,list,delete` |
| `deploy/rbac/extensions/decommission/clusterrole.yaml` | `clusterdeployments list`; `delete` on the three CRs only |
| `roles/rbac_bootstrap/files/deploy/rbac/**` | Collection-bundled copies of the above |
| `deploy/helm/acm-switchover-rbac/templates/{role,clusterrole}.yaml` | Helm equivalents |
| `tests/test_constants_parity.py` `CONSTANT_PAIRS` | Explicit Python-name to collection-name contract map |
| `tests/test_run_record_guardrails.py` | Locks raw config-key access to `lib/run_record.py` |
| `tests/unit/test_checkpoint_vocabulary_guardrail.py` | Locks raw `operational_data` key access to `module_utils/checkpoint.py` |

**Tests that currently pin the defects** (each is displaced by a task below):

| Test | Pins |
| --- | --- |
| `tests/test_decommission.py:143-159` | Refusal path asserting `result is True` |
| `tests/test_decommission.py:190-204` | 404 behavior asserted through a decorator-bypassing mock |
| `tests/test_decommission.py:645-702` | Operator Pods represented by name only |
| `tests/test_decommission.py:25-31` | Base fixture returning `[]` from list mocks |
| `tests/unit/test_decommission_role_contracts.py:336-350` | `failed_when: false` on the MCH wait |
| `tests/unit/test_ansible_resilience_contracts.py:479` | Bare operator-prefix substring assertion |
| `tests/unit/test_ansible_resilience_contracts.py:485` | `failed_when: false` on the MCH wait |

## 6. Global safety invariants

Every task inherits these. A change that violates one is wrong even if its own tests pass.

1. **Error is never absence.** Authorization, discovery, transport, TLS, timeout, decode, and
   malformed-response outcomes are `error`. No consumer reads `error` as absence or as an empty
   list.
2. **Absence is positive or it is not absence.** Custom-resource `object_absent` requires a
   successful discovery followed by a 404 on the named object; PR A's typed built-in Apps GETs
   classify their own explicit named-GET 404 as `object_absent`. Kind absence requires a positive determination from a
   successful discovery response. Namespace absence requires a positive 404 on a named Namespace
   GET.
3. **Partial inventory is never complete inventory.** A page failure fails the whole read. An
   outstanding continuation at exit is `error`. Truncation is not offered on a strict read.
4. **No DELETE before durable identity.** The target UID is captured and forced durable before
   the DELETE request is issued. If the durable write fails, the DELETE is not issued.
5. **Every teardown DELETE is UID-preconditioned.** 409 and 412 are fatal and are never retried
   as a name-only delete.
6. **Names never establish identity.** No prefix, label, service account, annotation, or image
   decides whether a Pod is operator-owned.
7. **Recorded phases resume obligations, never conclusions.** A stored `completed` is proof at
   the instant of its final read only. Every later destructive decision re-proves live.
8. **Preview never becomes evidence.** Python dry-run and Ansible check mode persist no
   identity, no phase, and no completion, call no DELETE primitive, and report actual
   `changed: false`. Any prediction is carried only in the separately named `would_change`
   field. The PR that first introduces a writer, mutation, or result aggregation also introduces
   and tests this guarantee; no intermediate merge waits for PR F to become safe.
9. **Permissions land no later than the API call that needs them.** Every PR that introduces a
   new API operation carries its full RBAC cross-surface change.
10. **Parity-sensitive observable behavior changes in both form factors in the same PR.** No
    intentional divergence is introduced without prior operator approval.
11. **No cross-form-factor imports.** Python CLI and collection runtime code never import each
    other.

## 7. Dependency graph

```
                    +---------------------------------------+
                    | PR A  Shared strict-read contract     |
                    | lib/strict_read.py + KubeClient strict|
                    | acm_k8s_read_outcome extension        |
                    +------------------+--------------------+
                                       |
              +------------------------+------------------------+
              |                                                 |
              v                                                 v
+-----------------------------+                    +-------------------------------+
| PR B  Durable state +       |                    | R4-04 Task 0 Step 2 gate       |
| outcome algebra + refusal   |                    | (satisfied by PR A merge)      |
| RunRecord + checkpoint      |                    +-------------------------------+
+--------------+--------------+
               |
               v
+---------------------------------------------------+
| PR C  Guarded delete primitive + MCO teardown +   |
| GLM-H6 consolidation + destination gate + RBAC    |
+----------------------+----------------------------+
                       |
        +--------------+--------------+
        |                             |
        v                             v
+---------------------+   +------------------------------------+
| PR D  ManagedCluster|   | PR E  MCH identity and completion  |
| teardown + RBAC     |   | CSV/Deployment/ReplicaSet + RBAC   |
+----------+----------+   +------------------+-----------------+
           |                                 |
           +----------------+----------------+
                            v
        +-----------------------------------------------+
        | PR F  Integrated proof and consistency gates  |
        | (no first safety behavior)                    |
        +-----------------------------------------------+
```

Edges are hard prerequisites:

| Edge | Reason |
| --- | --- |
| A to B | B's clean-skip and record-present rules are expressed in strict outcomes |
| A to R4-04 | R4-04 Task 0 Step 2 requires the merged strict primitive in both form factors |
| B to C | C writes teardown phases through the durable APIs B introduces |
| C to D | D reuses the single guarded-delete primitive C introduces |
| C to E | E reuses the same primitive and the same phase machine |
| D and E to F | F adds integrated proof that the already-safe B/C/D/E behavior composes; it introduces no safety fix |

D and E are independent of one another and may be built in parallel; either may merge first.

## 8. Runtime PR sequence overview

Six PRs. The count is chosen for reviewability, not symmetry, and each criterion the governing
task requires is discharged below.

| PR | Title | Form factors | New API calls | RBAC change |
| --- | --- | --- | --- | --- |
| A | Shared strict-read contract | both | discovery probe (no new verb) | none |
| B | Durable teardown state, outcome algebra, refusal abort | both | none | none |
| C | Guarded deletion, MCO teardown, GLM-H6, destination gate | both | MCO CR `get`, destination MCO/Namespace reads | yes |
| D | ManagedCluster teardown | both | `managedclusters get` | yes |
| E | MCH identity and completion | both | `multiclusterhubs get`, Deployments `get`, ReplicaSets `get`, CSVs `get`/`list` | yes |
| F | Integrated proof and consistency closure | both | none | none |

**Independent reviewability.** A is a read algebra with no destructive caller. B is a state and
result-vocabulary change with no new API call. C, D, and E each own exactly one resource family's
teardown. F changes no teardown algorithm.

**Every intermediate merged state is fail-closed.**

| After | State |
| --- | --- |
| A | A closed strict-read algebra exists in both form factors: exact-resource discovery, custom list/GET, live Namespace absence, complete Pod LIST, and strict Deployment/ReplicaSet GET producers. Outcomes that were silently empty or partial become `error`; both merged R3-02 consumers already fail closed on non-`ok`. |
| B | Refusal now aborts and exits non-zero; top-level cancellation remains unsuccessful; the collection artifact reports actual `changed: false` in check mode and an independent B-stage `would_change: false`. Python dry-run writes no result or state authority. Teardown records exist but no teardown yet writes one. |
| C | MCO teardown is guarded and proven. Every C writer and DELETE is check-mode/dry-run-safe, prediction is separate, and the reset-laundering limitation is already operator-visible. The destination gate blocks. ManagedCluster and MCH teardown remain as today — R4-C1 stays open and is not represented as closed. |
| D | ManagedCluster inventory and deletion are strict, guarded, and independently preview-safe: no DELETE, record write, or actual change in check mode/dry-run. MCH remains as today. |
| E | MCH identity and completion are proven and independently preview-safe; R4-C1 closes. |
| F | Integrated scenarios and cross-PR assertions prove the safety already delivered by B/C/D/E. F adds no missing check-mode branch, Python dry-run branch, writer guard, delete guard, result semantic, or first-publication documentation. |

**No API call lands before its RBAC requirement.** C, D, and E each carry the complete RBAC
cross-surface change for the calls they introduce, in the same PR (§14).

**No stale evidence becomes mutation authority.** The destination gate result is never persisted
(C). Teardown records carry obligations, and every destructive decision re-proves live (B, C, D, E).

**Parity-sensitive behavior changes in both form factors together.** Every PR above lists both
form factors. No PR introduces an intentional divergence; the three divergences that exist are
pre-existing and contract-backed (amendment §12) and are restated, not created.

**No PR needs hidden code from a later unmerged PR.** Verified edge by edge in §7.

**R4-04's prerequisite is satisfied by the first merge.** PR A alone satisfies the R4-04 Task 0
Step 2 checklist; §9.6 maps the checklist item by item.

**Rejected alternatives** are recorded in §23. No feature flag or compatibility mode is
introduced to make splitting easier.

---

# 9. PR A — shared strict-read prerequisite

**Branch:** `feature/r4-03-strict-read` · **Worktree:** `.claude/worktrees/r4-03-strict-read`

**Purpose.** Deliver the one shared strict Kubernetes read algebra whose contract R4-03 owns and
R4-04 consumes, in both form factors, with no competing read algebra left behind. This PR must be
independently mergeable and must satisfy the R4-04 Task 0 Step 2 gate on its own.

**Scope boundary.** No decommission caller migrates in this PR. `list_custom_resources`,
`get_custom_resource`, and `get_pods` keep their current behavior for every existing caller
(July non-goal: no repository-wide strict-read migration). The only behavior change to merged
code is the deliberate reclassification inside `acm_k8s_read_outcome` described in Task A4.

## 9.1 Decision record for PR A

Resolved here because the approved design delegates them to the implementation plan.

| Decision | Value | Basis |
| --- | --- | --- |
| Python outcome vocabulary owner | new module `lib/strict_read.py` | Keeps `lib/kube_client.py` a transport layer (amendment §10.3); lets R4-04 import the vocabulary without importing the client; gives the mirrored reason codes one owner for `CONSTANT_PAIRS` |
| Python strict list name | `KubeClient.list_custom_resources_strict` | July §3 names it |
| Python strict named GET name | `KubeClient.get_custom_resource_strict` | Matches the July "GET by name of one such resource" operation |
| Outcome type | frozen dataclass `StrictReadOutcome` with `status`, `items`, `resource`, `reason`, `resource_version` | Explicit over implicit; carries the `resourceVersion` that `completed` records need (July §1 step 5) |
| Status vocabulary | `ITEMS`, `CRD_ABSENT`, `NAMESPACE_ABSENT`, `OBJECT_ABSENT`, `ERROR` | July §3 outcome algebra verbatim |
| Kind-absence proof mechanism | independent discovery fetch that must succeed | §9.2 — required because exception type is not a reliable signal at either end of the supported range |
| Collection extension | extend `acm_k8s_read_outcome`; no second module | Amendment §6.2; R4-04 amendment criterion 27 |
| Collection namespace absence | composed at the call site by a `v1` `Namespace` named GET | Amendment §6.2 item 3 |
| `max_items` on the strict surface | not offered | Amendment §6.1 — truncation is incompatible with a strict inventory read |
| Strict page size | `STRICT_READ_PAGE_LIMIT = 500`, sent as `limit` on every strict LIST request in both form factors | Amendment §6.2 item 1 records that the collection module "supplies no `limit`, so truncation is latent rather than reachable"; making paging load-bearing requires a fixed positive page size, and a fixed size is what forces the multi-page path to be exercised rather than latent. `500` is the Kubernetes list-chunking size used by `kubectl` and is well inside the API server's page bounds |
| Strict page budget | `STRICT_READ_MAX_PAGES = 100` | With `limit=500` this bounds one whole-read attempt at 50 000 objects; a server that keeps returning a continuation past that is `error`, never a truncated success |
| Whole-read restarts | `STRICT_READ_MAX_RESTARTS = 1` | July §3 / amendment §6.1 — "an expired `continue` token restarts the whole read"; exactly one restart, then `error` |
| Per-call timeout | Python: the existing per-instance `KubeClient.request_timeout` (default `30`) via `_request_timeout_kwargs()`. Collection: `STRICT_READ_REQUEST_TIMEOUT = 30`, passed as `_request_timeout` | Bounded calls are a July §3 requirement. Python already owns the bound; adding a second Python constant would be unused configuration |

## 9.2 Why kind-absence needs its own proof

This is the single most important implementation constraint in PR A, and it is a mechanism
decision the amendment explicitly delegates: `kind_not_served` is "returned only on a *positive*
discovery determination … the implementation must prove the positive-determination property, not
infer it from exception type alone."

Verified in the pinned upstream sources (§15): the dynamic client's
`Discoverer.get_resources_for_api_version` catches discovery-fetch failures and substitutes an
empty resource list. A group/version whose discovery request failed therefore produces zero
matches, and `Discoverer.get` / `LazyDiscoverer.search` then raise `ResourceNotFoundError` —
indistinguishable from a genuine miss.

The swallowed set is **not stable across the supported dependency range**:

| Pinned source | Swallowed by `get_resources_for_api_version` |
| --- | --- |
| `kubernetes` v28.1.0 (the `requirements.txt` floor series) | `ServiceUnavailableError` |
| `kubernetes` v36.0.1 (resolved locally) | `ServiceUnavailableError` and `JSONDecodeError` |

Because the swallowed set differs materially inside the supported range, **no classification may
depend on it.** Both form factors therefore prove kind absence independently:

1. Issue a discovery request for the exact group/version — `GET /apis/{group}/{version}`, or
   `GET /api/v1` for the core group — through the same explicitly selected client.
2. Require a successful, decodable `APIResourceList` response. Any non-success, timeout,
   authorization failure, transport failure, or decode failure is `error`.
3. Return kind absence only when the requested resource is positively not present in that
   successful response's `resources` list.

This rule is version-invariant, so it satisfies both ends of the supported range without a
version branch. It is also the mechanism that discharges the R4-03 converted implementation-slice
obligation from the 2026-08-02 triage: for custom resources, only a named-object 404 after
successful discovery proves object absence; a discovery-path 404 is itself `error`.

## 9.3 Files

- Create: `lib/strict_read.py`
- Modify: `lib/kube_client.py` (add strict methods and the discovery prover; existing methods unchanged)
- Modify: `lib/constants.py` (strict-read reason codes)
- Create: `tests/test_strict_read.py`
- Create: `tests/test_strict_read_parity.py`
- Modify: `tests/test_kube_client.py`
- Modify: `tests/test_constants_parity.py`
- Modify: `ansible_collections/tomazb/acm_switchover/plugins/modules/acm_k8s_read_outcome.py`
- Modify: `ansible_collections/tomazb/acm_switchover/plugins/module_utils/constants.py`
- Modify: `ansible_collections/tomazb/acm_switchover/tests/unit/test_k8s_read_outcome.py`
- Modify: `ansible_collections/tomazb/acm_switchover/tests/integration/test_k8s_read_outcome_runtime.py`
- Modify: the existing R3-02 `acm_k8s_read_outcome` Pod and ConfigMap role call sites and their
  contract fixtures (add exact `resource_name` only; no behavior change)

## Task A1: Python strict outcome vocabulary

**Purpose:** Give both form factors one named outcome algebra with stable, mirrored reason codes.

**Interfaces produced:** `StrictReadStatus`, `StrictReadOutcome`, and the reason-code constants
consumed by every later task and by R4-04.

**Intended behavior:** A pure value type. It performs no I/O and makes no policy decision.

**Failure behavior:** Constructing an outcome whose `status` is not `ITEMS` with a non-empty
`items` list is a programming error and raises `ValueError`, so a caller cannot fabricate an
inventory on an error outcome.

**State/checkpoint implications:** none. **Dry-run implications:** none — read-only value type.
**Parity implications:** the reason codes are mirrored constants entering `CONSTANT_PAIRS`
(Task A5). **RBAC implications:** none.

- [ ] **Step 1: Write the failing test**

Create `tests/test_strict_read.py`:

```python
"""Strict read outcome algebra (R4-03 PR A)."""

import pytest

from lib.strict_read import StrictReadOutcome, StrictReadStatus


def test_items_outcome_carries_a_complete_inventory():
    outcome = StrictReadOutcome.from_items([{"metadata": {"name": "a"}}], resource_version="42")
    assert outcome.status is StrictReadStatus.ITEMS
    assert outcome.is_success is True
    assert outcome.proves_absence is False
    assert outcome.items == [{"metadata": {"name": "a"}}]
    assert outcome.resource_version == "42"


def test_error_outcome_is_never_absence_and_never_an_inventory():
    outcome = StrictReadOutcome.error("read_transport_failed")
    assert outcome.status is StrictReadStatus.ERROR
    assert outcome.is_success is False
    assert outcome.proves_absence is False
    assert outcome.items == []


@pytest.mark.parametrize(
    "factory, status",
    [
        (StrictReadOutcome.crd_absent, StrictReadStatus.CRD_ABSENT),
        (StrictReadOutcome.namespace_absent, StrictReadStatus.NAMESPACE_ABSENT),
        (StrictReadOutcome.object_absent, StrictReadStatus.OBJECT_ABSENT),
    ],
)
def test_positive_absence_outcomes_prove_absence(factory, status):
    outcome = factory("positively_absent")
    assert outcome.status is status
    assert outcome.proves_absence is True
    assert outcome.items == []


def test_non_items_outcome_cannot_carry_items():
    with pytest.raises(ValueError):
        StrictReadOutcome(status=StrictReadStatus.ERROR, items=[{"metadata": {}}])
```

- [ ] **Step 2: Run the test and observe the expected failure**

```bash
python -m pytest tests/test_strict_read.py -q
```

Expected before implementation: collection error — `ModuleNotFoundError: No module named 'lib.strict_read'`.

- [ ] **Step 3: Write the minimal implementation**

Create `lib/strict_read.py`:

```python
"""Shared strict Kubernetes read outcome algebra (R4-03).

Owned by R4-03 and consumed by R4-04. The vocabulary is deliberately small:
exactly one of five outcomes, three of which are positive absence proofs.
`error` is never absence and never an inventory.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class StrictReadStatus(Enum):
    ITEMS = "items"
    CRD_ABSENT = "crd_absent"
    NAMESPACE_ABSENT = "namespace_absent"
    OBJECT_ABSENT = "object_absent"
    ERROR = "error"


_ABSENCE_STATUSES = frozenset(
    {StrictReadStatus.CRD_ABSENT, StrictReadStatus.NAMESPACE_ABSENT, StrictReadStatus.OBJECT_ABSENT}
)


@dataclass(frozen=True)
class StrictReadOutcome:
    """One completed strict read. Exactly one status; items only on ITEMS."""

    status: StrictReadStatus
    items: List[Dict[str, Any]] = field(default_factory=list)
    resource: Optional[Dict[str, Any]] = None
    reason: Optional[str] = None
    resource_version: Optional[str] = None

    def __post_init__(self) -> None:
        if self.status is not StrictReadStatus.ITEMS and self.items:
            raise ValueError(f"{self.status.value} outcome must not carry items")
        if self.status is not StrictReadStatus.ITEMS and self.resource is not None:
            raise ValueError(f"{self.status.value} outcome must not carry a resource")

    @property
    def is_success(self) -> bool:
        return self.status is StrictReadStatus.ITEMS

    @property
    def proves_absence(self) -> bool:
        return self.status in _ABSENCE_STATUSES

    # Named constructors keep call sites explicit about which proof they hold.
    # They cannot be called `items`/`resource`: those names are fields.
    @classmethod
    def from_items(cls, items, resource_version=None) -> "StrictReadOutcome":
        return cls(status=StrictReadStatus.ITEMS, items=list(items), resource_version=resource_version)

    @classmethod
    def from_resource(cls, resource, resource_version=None) -> "StrictReadOutcome":
        return cls(
            status=StrictReadStatus.ITEMS,
            items=[resource],
            resource=resource,
            resource_version=resource_version,
        )

    @classmethod
    def crd_absent(cls, reason: str) -> "StrictReadOutcome":
        return cls(status=StrictReadStatus.CRD_ABSENT, reason=reason)

    @classmethod
    def namespace_absent(cls, reason: str) -> "StrictReadOutcome":
        return cls(status=StrictReadStatus.NAMESPACE_ABSENT, reason=reason)

    @classmethod
    def object_absent(cls, reason: str) -> "StrictReadOutcome":
        return cls(status=StrictReadStatus.OBJECT_ABSENT, reason=reason)

    @classmethod
    def error(cls, reason: str) -> "StrictReadOutcome":
        return cls(status=StrictReadStatus.ERROR, reason=reason)
```

- [ ] **Step 4: Run the test and observe it pass**

```bash
python -m pytest tests/test_strict_read.py -q
```

Expected: PASS.

- [ ] **Step 5: Add the mirrored reason codes**

Add to `lib/constants.py`, grouped under a `# R4-03 strict-read reason codes` comment:

```python
STRICT_READ_REASON_KIND_NOT_SERVED = "kind_not_served"
STRICT_READ_REASON_NAMESPACE_NOT_FOUND = "namespace_not_found"
STRICT_READ_REASON_OBJECT_NOT_FOUND = "object_not_found"
STRICT_READ_REASON_DISCOVERY_UNVERIFIABLE = "discovery_unverifiable"
STRICT_READ_REASON_INVENTORY_INCOMPLETE = "inventory_incomplete"
STRICT_READ_REASON_MALFORMED_RESPONSE = "malformed_response"
STRICT_READ_REASON_READ_FAILED = "read_failed"
```

Add the identical names and values to
`ansible_collections/tomazb/acm_switchover/plugins/module_utils/constants.py`.

- [ ] **Step 5b: Add the strict-read bound constants**

The bounds are decided here, once, rather than at each call site. Add to `lib/constants.py` under a
`# R4-03 strict-read bounds` comment, and add the identical names and values to the collection's
`module_utils/constants.py`:

```python
STRICT_READ_PAGE_LIMIT = 500
STRICT_READ_MAX_PAGES = 100
STRICT_READ_MAX_RESTARTS = 1
```

Add one collection-only constant to `module_utils/constants.py`, because the collection module has
no client instance carrying a timeout:

```python
STRICT_READ_REQUEST_TIMEOUT = 30
```

Python does **not** gain a mirrored timeout constant: `KubeClient` already owns a per-instance
`request_timeout` (default `30`, `lib/kube_client.py:210`) applied through the existing
`_request_timeout_kwargs()` helper, and a second unused Python constant would be dead configuration
(YAGNI). The collection constant's value equals that Python default, and Task A5 asserts the
equality directly rather than leaving it to prose.

- [ ] **Step 6: Refactor and simplify**

Read `lib/strict_read.py` once more. It must contain no branch that is not exercised by
`tests/test_strict_read.py`. Delete anything unexercised rather than adding a test for it.

- [ ] **Step 7: Targeted rerun**

```bash
python -m pytest tests/test_strict_read.py -q
```

- [ ] **Step 8: Commit**

```bash
git add lib/strict_read.py lib/constants.py tests/test_strict_read.py \
  ansible_collections/tomazb/acm_switchover/plugins/module_utils/constants.py
git commit -m "feat: add shared strict read outcome algebra"
```

## Task A2: Python discovery prover

**Purpose:** Establish positive kind-absence independently of the client's exception behavior (§9.2).

**Files:** Modify `lib/kube_client.py`; modify `tests/test_kube_client.py`.

**Interfaces produced:** `KubeClient._discovery_serves(group, version, resource_name) -> StrictReadOutcome`
returning an `ITEMS` outcome with an empty list when the kind is served, `CRD_ABSENT` when the
successful discovery response positively lacks it, and `ERROR` otherwise.

**Intended behavior:** One `GET` of `/apis/{group}/{version}`, or `/api/v1` when `group` is empty,
issued through this instance's `ApiClient` with the instance request timeout. `resource_name` is
the caller-supplied canonical Kubernetes APIResource name/plural (for example,
`multiclusterobservabilities`), never a value synthesized from `kind`. The response must decode
to a mapping whose `kind` is `APIResourceList` and whose `resources` is a list. Every resource
entry must be a mapping with non-empty string `name` and `kind`; malformed data makes the whole
determination unverifiable. The kind is served only when an entry's exact `name` equals
`resource_name`.

**Failure behavior:** Non-2xx, timeout, transport failure, undecodable body, missing or non-list
`resources`, malformed entries, or an unexpected APIResourceList shape all return `ERROR` with
`STRICT_READ_REASON_DISCOVERY_UNVERIFIABLE`. Discovery HTTP 404, 401/403, 5xx, timeout, TLS,
transport, and JSON/decode failure are all `ERROR`. Only a successfully fetched, decoded, and
structurally valid discovery document that lacks the exact canonical `resource_name` proves
`CRD_ABSENT`; neither `ResourceNotFoundError` nor any swallowed discovery-client failure proves
absence.

**State implications:** none. **Dry-run implications:** read-only; runs identically in dry-run.
**Parity implications:** the collection performs the equivalent proof in Task A4.
**RBAC implications:** none — discovery endpoints are unauthenticated-readable in the supported
topology and require no verb (§14).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_kube_client.py`:

```python
class TestDiscoveryProver:
    """R4-03: kind absence must come from a successful discovery response."""

    def _client(self, call_api):
        client = KubeClient.__new__(KubeClient)
        client.request_timeout = 30
        client.dry_run = False
        client._api_client = Mock()
        client._api_client.call_api = call_api
        return client

    def test_served_kind_returns_items(self):
        call = Mock(return_value={"kind": "APIResourceList", "resources": [{"name": "multiclusterhubs", "kind": "MultiClusterHub"}]})
        client = self._client(call)
        outcome = client._discovery_serves("operator.open-cluster-management.io", "v1", "multiclusterhubs")
        assert outcome.status is StrictReadStatus.ITEMS

    def test_absent_kind_in_a_successful_response_is_crd_absent(self):
        call = Mock(return_value={"kind": "APIResourceList", "resources": [{"name": "somethingelse", "kind": "SomethingElse"}]})
        outcome = self._client(call)._discovery_serves("g", "v1", "multiclusterhubs")
        assert outcome.status is StrictReadStatus.CRD_ABSENT

    def test_discovery_service_unavailable_is_error_not_absence(self):
        call = Mock(side_effect=ApiException(status=503, reason="Service Unavailable"))
        outcome = self._client(call)._discovery_serves("g", "v1", "multiclusterhubs")
        assert outcome.status is StrictReadStatus.ERROR
        assert outcome.reason == STRICT_READ_REASON_DISCOVERY_UNVERIFIABLE

    def test_discovery_forbidden_is_error_not_absence(self):
        call = Mock(side_effect=ApiException(status=403, reason="Forbidden"))
        assert self._client(call)._discovery_serves("g", "v1", "p").status is StrictReadStatus.ERROR

    def test_discovery_timeout_is_error_not_absence(self):
        call = Mock(side_effect=TimeoutError("deadline exceeded"))
        assert self._client(call)._discovery_serves("g", "v1", "p").status is StrictReadStatus.ERROR

    def test_undecodable_discovery_body_is_error_not_absence(self):
        call = Mock(return_value="<html>gateway error</html>")
        assert self._client(call)._discovery_serves("g", "v1", "p").status is StrictReadStatus.ERROR

    def test_missing_resources_key_is_error_not_absence(self):
        call = Mock(return_value={"kind": "APIResourceList"})
        assert self._client(call)._discovery_serves("g", "v1", "p").status is StrictReadStatus.ERROR

    def test_discovery_404_is_error_not_absence(self):
        call = Mock(side_effect=ApiException(status=404, reason="Not Found"))
        outcome = self._client(call)._discovery_serves("g", "v1", "p")
        assert outcome.status is StrictReadStatus.ERROR

    def test_discovery_decode_failure_is_error_not_absence(self):
        call = Mock(side_effect=ValueError("invalid json"))
        assert self._client(call)._discovery_serves("g", "v1", "p").status is StrictReadStatus.ERROR

    def test_malformed_api_resource_list_is_error_not_absence(self):
        call = Mock(return_value={"kind": "APIResourceList", "resources": [{"name": 7}]})
        assert self._client(call)._discovery_serves("g", "v1", "p").status is StrictReadStatus.ERROR

    def test_irregular_plural_is_matched_by_exact_resource_name(self):
        call = Mock(return_value={
            "kind": "APIResourceList",
            "resources": [{"name": "multiclusterobservabilities", "kind": "MultiClusterObservability"}],
        })
        outcome = self._client(call)._discovery_serves(
            "observability.open-cluster-management.io", "v1beta2", "multiclusterobservabilities"
        )
        assert outcome.status is StrictReadStatus.ITEMS

    def test_core_group_uses_the_core_discovery_path(self):
        call = Mock(return_value={"kind": "APIResourceList", "resources": [{"name": "pods", "kind": "Pod"}]})
        self._client(call)._discovery_serves("", "v1", "pods")
        assert call.call_args[0][0] == "/api/v1"
```

- [ ] **Step 2: Run the tests and observe the expected failure**

```bash
python -m pytest tests/test_kube_client.py -q -k DiscoveryProver
```

Expected: FAIL — `AttributeError: 'KubeClient' object has no attribute '_discovery_serves'`.

- [ ] **Step 3: Implement**

In `KubeClient.__init__`, retain the constructed client so discovery can reuse it. Immediately
after the three API clients are created, add:

```python
        # Retained so the strict-read discovery prover reuses this exact,
        # explicitly-selected client rather than resolving a second one.
        self._api_client = api_client
```

Then add the prover next to the other read helpers:

```python
    def _discovery_serves(self, group: str, version: str, resource_name: str) -> StrictReadOutcome:
        """Positively determine whether one kind is served, or fail closed.

        Kind absence is proven only by a successful, decodable discovery
        response that does not list the exact canonical `resource_name`. A discovery call that fails,
        times out, is unauthorized, or returns an unparseable body is an
        error: an unserved kind and an unreachable API server are not
        distinguishable by exception type, and the client library's own
        discovery cache swallows some failures into an empty resource list.
        """
        path = f"/apis/{group}/{version}" if group else f"/api/{version}"
        try:
            response = self._api_client.call_api(
                path,
                "GET",
                response_type="object",
                auth_settings=["BearerToken"],
                _return_http_data_only=True,
                _request_timeout=self.request_timeout,
            )
        except ApiException:
            return StrictReadOutcome.error(STRICT_READ_REASON_DISCOVERY_UNVERIFIABLE)
        except Exception:
            return StrictReadOutcome.error(STRICT_READ_REASON_DISCOVERY_UNVERIFIABLE)

        if not isinstance(response, dict):
            return StrictReadOutcome.error(STRICT_READ_REASON_DISCOVERY_UNVERIFIABLE)
        if response.get("kind") != "APIResourceList":
            return StrictReadOutcome.error(STRICT_READ_REASON_DISCOVERY_UNVERIFIABLE)
        resources = response.get("resources")
        if not isinstance(resources, list):
            return StrictReadOutcome.error(STRICT_READ_REASON_DISCOVERY_UNVERIFIABLE)
        for entry in resources:
            if (
                not isinstance(entry, dict)
                or not isinstance(entry.get("name"), str)
                or not entry["name"]
                or not isinstance(entry.get("kind"), str)
                or not entry["kind"]
            ):
                return StrictReadOutcome.error(STRICT_READ_REASON_DISCOVERY_UNVERIFIABLE)
            if entry["name"] == resource_name:
                return StrictReadOutcome.from_items([])
        return StrictReadOutcome.crd_absent(STRICT_READ_REASON_KIND_NOT_SERVED)
```

- [ ] **Step 4: Run the tests and observe them pass**

```bash
python -m pytest tests/test_kube_client.py -q -k DiscoveryProver
```

- [ ] **Step 5: Refactor**

The prover must contain exactly one success return and must not log the response body. Confirm
no `logger` call in this method receives `response` or `exc`.

- [ ] **Step 6: Targeted rerun and commit**

```bash
python -m pytest tests/test_kube_client.py -q
git add lib/kube_client.py tests/test_kube_client.py
git commit -m "feat: prove kind absence from a successful discovery response"
```

## Task A3: Python strict list and strict named GET

**Purpose:** Deliver the closed strict-read surface needed by R4-04 and by later R4-03 slices.

**Files:** Modify `lib/kube_client.py`; modify `tests/test_kube_client.py`.

**Interfaces consumed:** `StrictReadOutcome`, `StrictReadStatus`, `_discovery_serves`, and the
Task A1 bound constants `STRICT_READ_PAGE_LIMIT`, `STRICT_READ_MAX_PAGES`,
`STRICT_READ_MAX_RESTARTS`.

**Interfaces produced:**

```python
def list_custom_resources_strict(
    self, group: str, version: str, plural: str,
    namespace: Optional[str] = None, label_selector: Optional[str] = None,
) -> StrictReadOutcome: ...

def get_custom_resource_strict(
    self, group: str, version: str, plural: str, name: str,
    namespace: Optional[str] = None,
) -> StrictReadOutcome: ...

def get_namespace_strict(self, name: str) -> StrictReadOutcome: ...

def list_pods_strict(
    self, namespace: str, label_selector: Optional[str] = None,
) -> StrictReadOutcome: ...

def get_deployment_strict(self, name: str, namespace: str) -> StrictReadOutcome: ...

def get_replicaset_strict(self, name: str, namespace: str) -> StrictReadOutcome: ...
```

### A3.0 One bounded-read policy, applied by every strict method

| Bound | Where the value lives | Value | Applied to |
| --- | --- | --- | --- |
| Page size | `lib/constants.STRICT_READ_PAGE_LIMIT` | `500` | `limit=` on the **first page and every continuation page** of every strict LIST |
| Page budget | `lib/constants.STRICT_READ_MAX_PAGES` | `100` | maximum pages drained in one whole-read attempt |
| Whole-read restarts | `lib/constants.STRICT_READ_MAX_RESTARTS` | `1` | number of times an expired continuation may discard the prefix and restart from page 1 |
| Per-call timeout | instance `self.request_timeout` (default `30`) via the existing `_request_timeout_kwargs()` | `30` | `_request_timeout` on every discovery, list, and named-GET request |

Rules that hold for every method below, and that the tests in this task assert directly rather
than by inspection of prose:

1. **Every request is bounded.** No strict call is issued without `_request_timeout`.
2. **Every list request carries a fixed positive `limit`.** Paging is load-bearing, not latent.
3. **A continuation is followed with `_continue` until the server returns none.** `ITEMS` is
   returned only when the final page carried no outstanding continuation.
4. **No partial prefix ever escapes.** Accumulation is internal to one whole-read attempt; any
   failure returns `StrictReadOutcome.error(...)` with `items == []`.
5. **One expired continuation (HTTP 410) discards everything accumulated and restarts the whole
   read from page 1, exactly once.** A second expiry is `error`.
6. **Malformed is never empty.** A non-mapping page, missing `items`, non-list `items`, a
   non-mapping member, or missing/non-mapping list metadata is
   `error` with `STRICT_READ_REASON_MALFORMED_RESPONSE`.
7. **`max_items` is not offered** on any strict method.

**Strict core/typed-read contract.** These six methods are implemented in PR A rather than
invented at their later call sites. None uses `@api_call(not_found_value=...)`, `@retry_api_call`,
or another decorator that rewrites 404; each passes the instance's bounded request timeout and
returns only sanitized reason codes.

- `list_custom_resources_strict(...)` proves the kind is served first, then drains
  `CustomObjectsApi` pages under the A3.0 policy. The custom-objects API returns plain
  dictionaries, so page/member/metadata validation is dictionary-shaped.
- `get_custom_resource_strict(...)` proves the kind is served first, then performs one bounded
  named GET. It returns a single-resource `ITEMS` outcome carrying `metadata.resourceVersion`, or
  `OBJECT_ABSENT` on a 404 that followed a successful discovery determination.
- `get_namespace_strict(name)` performs one live CoreV1 named Namespace GET. A well-formed
  Namespace whose `metadata.name` equals `name` returns `ITEMS`; its explicit GET 404 returns
  `NAMESPACE_ABSENT`. Authorization, server, timeout, TLS, transport, decode, malformed metadata,
  or an unexpected returned name is `ERROR`. No cached or preflight namespace fact can satisfy
  this producer.
- `list_pods_strict(namespace, label_selector=None)` drains CoreV1 Pod LIST pages under the same
  A3.0 policy. **CoreV1 returns typed models, not dictionaries**: the page is a `V1PodList`, its
  members are `V1Pod`, and its list metadata is `V1ListMeta` whose continuation attribute is
  `_continue` and whose revision attribute is `resource_version` (verified at both pinned client
  tags, §15.2). The helper therefore validates attribute-shaped pages and converts members with
  `pod.to_dict()`, matching the existing `_list_pods_once` convention at
  `lib/kube_client.py:1259-1286`. **Members are therefore client-model mappings with snake_case
  keys** — `metadata["owner_references"]`, each with `api_version`, `kind`, `name`, `uid`,
  `controller` — and PR E's owner-chain classifier consumes exactly that shape. A successful final
  page with zero Pods is `ITEMS([])` carrying the list `resource_version`. A Pod LIST 404 does not
  mean zero Pods: the helper immediately performs a fresh `get_namespace_strict(namespace)`; only
  `NAMESPACE_ABSENT` is propagated, while namespace present or namespace-read `ERROR` makes the
  Pod result `ERROR`.
- `get_deployment_strict(name, namespace)` and `get_replicaset_strict(name, namespace)` perform
  bounded named AppsV1 GETs. A well-formed object must have the requested name and namespace and
  a non-empty `metadata.uid`, because every R4-03 consumer needs identity. Explicit 404 returns
  `OBJECT_ABSENT`; authorization, server, timeout, TLS, transport, decode, malformed metadata,
  missing UID, or mismatched identity returns `ERROR`. The helpers classify transport only; the
  operator-owner-chain consumer decides whether `OBJECT_ABSENT` means `recovery_required` or a
  different blocker.

**State implications:** none. **Dry-run implications:** read-only; identical in dry-run.
**Parity implications:** held equal to the collection by Task A5's shared vectors and bound
assertions. **RBAC implications:** none new — `list` and `get` on the target kinds are added by the
consuming PRs C, D, and E where each read first becomes live.

This task is executed as three red/green cycles — custom resources (Steps 1–4), core reads
(Steps 5–8), and Apps reads (Steps 9–12) — followed by one refactor and one wider gate. No method
is delegated to a "similarly" instruction.

### Cycle 1 — custom-resource strict list and named GET

- [ ] **Step 1: Write the failing tests**

Add these imports to the top of `tests/test_kube_client.py` if they are not already present —
`inspect` is imported **at module scope**, because more than one test below uses it:

```python
import inspect

from lib.constants import (
    STRICT_READ_MAX_PAGES,
    STRICT_READ_MAX_RESTARTS,
    STRICT_READ_PAGE_LIMIT,
    STRICT_READ_REASON_DISCOVERY_UNVERIFIABLE,
    STRICT_READ_REASON_INVENTORY_INCOMPLETE,
    STRICT_READ_REASON_KIND_NOT_SERVED,
    STRICT_READ_REASON_MALFORMED_RESPONSE,
    STRICT_READ_REASON_NAMESPACE_NOT_FOUND,
    STRICT_READ_REASON_OBJECT_NOT_FOUND,
    STRICT_READ_REASON_READ_FAILED,
)
from lib.strict_read import StrictReadOutcome, StrictReadStatus
```

The same names are imported into `lib/kube_client.py` alongside its existing `lib.constants`
imports; the strict methods below reference the module-level constants directly and define no
class-level bound attributes, so the bounds have exactly one spelling and one owner.

Then add:

```python
class TestStrictCustomResourceReads:
    """R4-03 July §3 outcome algebra."""

    def _client(self, list_pages=None, discovery_served=True, get_result=None, get_error=None):
        client = KubeClient.__new__(KubeClient)
        client.request_timeout = 30
        client.dry_run = False
        client.custom_api = Mock()
        client._discovery_serves = Mock(
            return_value=StrictReadOutcome.from_items([])
            if discovery_served
            else StrictReadOutcome.crd_absent(STRICT_READ_REASON_KIND_NOT_SERVED)
        )
        if list_pages is not None:
            client.custom_api.list_cluster_custom_object = Mock(side_effect=list_pages)
        if get_result is not None or get_error is not None:
            client.custom_api.get_cluster_custom_object = Mock(
                return_value=get_result, side_effect=get_error
            )
        return client

    def test_true_empty_list_is_a_proven_complete_inventory(self):
        client = self._client(list_pages=[{"items": [], "metadata": {}}])
        outcome = client.list_custom_resources_strict("g", "v1", "widgets")
        assert outcome.status is StrictReadStatus.ITEMS
        assert outcome.items == []

    def test_complete_multi_page_inventory_is_joined(self):
        pages = [
            {"items": [{"metadata": {"name": "a"}}], "metadata": {"continue": "tok"}},
            {"items": [{"metadata": {"name": "b"}}], "metadata": {}},
        ]
        outcome = self._client(list_pages=pages).list_custom_resources_strict("g", "v1", "widgets")
        assert [i["metadata"]["name"] for i in outcome.items] == ["a", "b"]

    def test_every_page_request_carries_the_fixed_page_limit(self):
        pages = [
            {"items": [{"metadata": {"name": "a"}}], "metadata": {"continue": "tok"}},
            {"items": [{"metadata": {"name": "b"}}], "metadata": {}},
        ]
        client = self._client(list_pages=pages)
        client.list_custom_resources_strict("g", "v1", "widgets")
        calls = client.custom_api.list_cluster_custom_object.call_args_list
        assert len(calls) == 2
        assert [call.kwargs["limit"] for call in calls] == [
            STRICT_READ_PAGE_LIMIT,
            STRICT_READ_PAGE_LIMIT,
        ]

    def test_every_page_request_is_bounded_and_follows_the_continuation(self):
        pages = [
            {"items": [], "metadata": {"continue": "tok"}},
            {"items": [], "metadata": {}},
        ]
        client = self._client(list_pages=pages)
        client.list_custom_resources_strict("g", "v1", "widgets")
        calls = client.custom_api.list_cluster_custom_object.call_args_list
        assert [call.kwargs["_continue"] for call in calls] == [None, "tok"]
        assert all(call.kwargs["_request_timeout"] == 30 for call in calls)

    def test_later_page_failure_fails_the_whole_read(self):
        pages = [
            {"items": [{"metadata": {"name": "a"}}], "metadata": {"continue": "tok"}},
            ApiException(status=500, reason="Internal Server Error"),
        ]
        outcome = self._client(list_pages=pages).list_custom_resources_strict("g", "v1", "widgets")
        assert outcome.status is StrictReadStatus.ERROR
        assert outcome.items == []

    def test_outstanding_continuation_at_exit_is_incomplete(self):
        # A server that keeps returning a continue token must not be reported as complete.
        pages = [{"items": [], "metadata": {"continue": "tok"}}] * (STRICT_READ_MAX_PAGES + 5)
        outcome = self._client(list_pages=pages).list_custom_resources_strict("g", "v1", "widgets")
        assert outcome.status is StrictReadStatus.ERROR
        assert outcome.reason == STRICT_READ_REASON_INVENTORY_INCOMPLETE

    def test_expired_continue_token_restarts_the_whole_read_once(self):
        pages = [
            {"items": [{"metadata": {"name": "a"}}], "metadata": {"continue": "tok"}},
            ApiException(status=410, reason="Gone"),
            {"items": [{"metadata": {"name": "a"}}], "metadata": {"continue": "tok"}},
            {"items": [{"metadata": {"name": "b"}}], "metadata": {}},
        ]
        client = self._client(list_pages=pages)
        outcome = client.list_custom_resources_strict("g", "v1", "widgets")
        assert outcome.status is StrictReadStatus.ITEMS
        # The prefix collected before the 410 is discarded, not carried into the restart.
        assert [i["metadata"]["name"] for i in outcome.items] == ["a", "b"]
        restarted = client.custom_api.list_cluster_custom_object.call_args_list[2]
        assert restarted.kwargs["_continue"] is None

    def test_second_expired_continue_token_fails_closed(self):
        pages = [
            {"items": [{"metadata": {"name": "a"}}], "metadata": {"continue": "tok"}},
            ApiException(status=410, reason="Gone"),
            {"items": [{"metadata": {"name": "a"}}], "metadata": {"continue": "tok"}},
            ApiException(status=410, reason="Gone"),
        ]
        outcome = self._client(list_pages=pages).list_custom_resources_strict("g", "v1", "widgets")
        assert outcome.status is StrictReadStatus.ERROR
        assert outcome.items == []

    def test_malformed_items_is_error_not_empty(self):
        outcome = self._client(list_pages=[{"items": "nope", "metadata": {}}]).list_custom_resources_strict(
            "g", "v1", "widgets"
        )
        assert outcome.status is StrictReadStatus.ERROR
        assert outcome.reason == STRICT_READ_REASON_MALFORMED_RESPONSE

    def test_missing_items_key_is_error_not_empty(self):
        outcome = self._client(list_pages=[{"metadata": {}}]).list_custom_resources_strict(
            "g", "v1", "widgets"
        )
        assert outcome.status is StrictReadStatus.ERROR
        assert outcome.reason == STRICT_READ_REASON_MALFORMED_RESPONSE

    def test_null_items_is_error_not_empty(self):
        outcome = self._client(list_pages=[{"items": None, "metadata": {}}]).list_custom_resources_strict(
            "g", "v1", "widgets"
        )
        assert outcome.status is StrictReadStatus.ERROR

    def test_malformed_list_metadata_is_error(self):
        outcome = self._client(list_pages=[{"items": [], "metadata": "nope"}]).list_custom_resources_strict(
            "g", "v1", "widgets"
        )
        assert outcome.status is StrictReadStatus.ERROR
        assert outcome.reason == STRICT_READ_REASON_MALFORMED_RESPONSE

    def test_non_mapping_member_is_error_not_empty(self):
        outcome = self._client(list_pages=[{"items": ["x"], "metadata": {}}]).list_custom_resources_strict(
            "g", "v1", "widgets"
        )
        assert outcome.status is StrictReadStatus.ERROR

    def test_authorization_failure_is_error_not_absence(self):
        outcome = self._client(
            list_pages=[ApiException(status=403, reason="Forbidden")]
        ).list_custom_resources_strict("g", "v1", "widgets")
        assert outcome.status is StrictReadStatus.ERROR
        assert outcome.proves_absence is False

    def test_list_404_on_a_served_kind_is_error_not_absence(self):
        outcome = self._client(
            list_pages=[ApiException(status=404, reason="Not Found")]
        ).list_custom_resources_strict("g", "v1", "widgets")
        assert outcome.status is StrictReadStatus.ERROR

    def test_unserved_kind_short_circuits_to_crd_absent(self):
        client = self._client(list_pages=[], discovery_served=False)
        outcome = client.list_custom_resources_strict("g", "v1", "widgets")
        assert outcome.status is StrictReadStatus.CRD_ABSENT
        client.custom_api.list_cluster_custom_object.assert_not_called()

    def test_named_get_returns_the_resource_and_its_resource_version(self):
        resource = {"metadata": {"name": "mch", "uid": "u-1", "resourceVersion": "77"}}
        outcome = self._client(get_result=resource).get_custom_resource_strict("g", "v1", "widgets", "mch")
        assert outcome.status is StrictReadStatus.ITEMS
        assert outcome.resource is resource
        assert outcome.resource_version == "77"

    def test_named_get_is_bounded(self):
        client = self._client(get_result={"metadata": {"name": "mch", "resourceVersion": "77"}})
        client.get_custom_resource_strict("g", "v1", "widgets", "mch")
        assert client.custom_api.get_cluster_custom_object.call_args.kwargs["_request_timeout"] == 30

    def test_named_get_404_after_successful_discovery_is_object_absent(self):
        outcome = self._client(
            get_error=ApiException(status=404, reason="Not Found")
        ).get_custom_resource_strict("g", "v1", "widgets", "mch")
        assert outcome.status is StrictReadStatus.OBJECT_ABSENT

    def test_named_get_404_without_successful_discovery_is_never_object_absent(self):
        client = self._client(get_error=ApiException(status=404, reason="Not Found"), discovery_served=False)
        outcome = client.get_custom_resource_strict("g", "v1", "widgets", "mch")
        assert outcome.status is StrictReadStatus.CRD_ABSENT

    def test_strict_surface_offers_no_truncation(self):
        for method in (
            KubeClient.list_custom_resources_strict,
            KubeClient.list_pods_strict,
        ):
            assert "max_items" not in inspect.signature(method).parameters

    def test_legacy_readers_are_unchanged(self):
        # The strict surface is additive; existing callers keep the current behavior.
        assert KubeClient.list_custom_resources.__doc__ is not None
        assert "max_items" in inspect.signature(KubeClient.list_custom_resources).parameters
```

- [ ] **Step 2: Run and observe the expected failure**

```bash
python -m pytest tests/test_kube_client.py -q -k StrictCustomResourceReads
```

Expected: collection/attribute failure —
`AttributeError: type object 'KubeClient' has no attribute 'list_custom_resources_strict'`, raised
first by `test_strict_surface_offers_no_truncation` at import/collection of the attribute and by
every behavioral test at call time. No test may pass at this point.

- [ ] **Step 3: Implement the custom-resource strict methods**

Add to `lib/kube_client.py`. Neither method carries `@api_call` or `@retry_api_call`: the strict
surface owns its own classification and must never have a 404 rewritten underneath it, and the
caller owns the bounded retry budget (July §3, "the primitive itself never polls").

Define the restart sentinel near the module's other private helpers:

```python
_RESTART_READ = object()
```

```python
    def list_custom_resources_strict(
        self,
        group: str,
        version: str,
        plural: str,
        namespace: Optional[str] = None,
        label_selector: Optional[str] = None,
    ) -> StrictReadOutcome:
        """Strictly list one custom-resource kind, or fail closed.

        Returns ITEMS only for a positively complete inventory. No partial
        prefix is ever returned, and no failure is ever reported as empty.
        """
        self._validate_resource_inputs(namespace=namespace)

        served = self._discovery_serves(group, version, plural)
        if served.status is not StrictReadStatus.ITEMS:
            return served

        for _ in range(STRICT_READ_MAX_RESTARTS + 1):
            outcome = self._drain_strict_list(group, version, plural, namespace, label_selector)
            if outcome is not _RESTART_READ:
                return outcome
        # The restart budget is spent and the continuation is still expiring.
        return StrictReadOutcome.error(STRICT_READ_REASON_INVENTORY_INCOMPLETE)

    def _drain_strict_list(self, group, version, plural, namespace, label_selector):
        """One whole-read attempt. Items accumulate here and escape only on success."""
        items: List[Dict[str, Any]] = []
        continue_token: Optional[str] = None

        for _ in range(STRICT_READ_MAX_PAGES):
            try:
                if namespace:
                    page = self.custom_api.list_namespaced_custom_object(
                        group=group, version=version, namespace=namespace, plural=plural,
                        label_selector=label_selector, _continue=continue_token,
                        limit=STRICT_READ_PAGE_LIMIT,
                        **self._request_timeout_kwargs(),
                    )
                else:
                    page = self.custom_api.list_cluster_custom_object(
                        group=group, version=version, plural=plural,
                        label_selector=label_selector, _continue=continue_token,
                        limit=STRICT_READ_PAGE_LIMIT,
                        **self._request_timeout_kwargs(),
                    )
            except ApiException as exc:
                # 410 Gone means the continuation expired: discard the prefix and
                # restart the whole read rather than returning what was collected.
                if exc.status == 410 and continue_token is not None:
                    return _RESTART_READ
                return StrictReadOutcome.error(STRICT_READ_REASON_READ_FAILED)
            except Exception:
                return StrictReadOutcome.error(STRICT_READ_REASON_READ_FAILED)

            if not isinstance(page, dict):
                return StrictReadOutcome.error(STRICT_READ_REASON_MALFORMED_RESPONSE)
            page_items = page.get("items")
            if not isinstance(page_items, list):
                return StrictReadOutcome.error(STRICT_READ_REASON_MALFORMED_RESPONSE)
            if any(not isinstance(item, dict) for item in page_items):
                return StrictReadOutcome.error(STRICT_READ_REASON_MALFORMED_RESPONSE)
            items.extend(page_items)

            metadata = page.get("metadata")
            if not isinstance(metadata, dict):
                return StrictReadOutcome.error(STRICT_READ_REASON_MALFORMED_RESPONSE)
            continue_token = metadata.get("continue") or None
            if continue_token is None:
                return StrictReadOutcome.from_items(items, resource_version=metadata.get("resourceVersion"))

        # Page budget exhausted with a continuation still outstanding.
        return StrictReadOutcome.error(STRICT_READ_REASON_INVENTORY_INCOMPLETE)

    def get_custom_resource_strict(
        self,
        group: str,
        version: str,
        plural: str,
        name: str,
        namespace: Optional[str] = None,
    ) -> StrictReadOutcome:
        """Strictly GET one named custom resource, or fail closed.

        OBJECT_ABSENT is returned only for a 404 that followed a successful
        discovery determination that the kind is served.
        """
        self._validate_resource_inputs(namespace, name, "custom resource")

        served = self._discovery_serves(group, version, plural)
        if served.status is not StrictReadStatus.ITEMS:
            return served

        try:
            if namespace:
                resource = self.custom_api.get_namespaced_custom_object(
                    group=group, version=version, namespace=namespace, plural=plural,
                    name=name, **self._request_timeout_kwargs(),
                )
            else:
                resource = self.custom_api.get_cluster_custom_object(
                    group=group, version=version, plural=plural, name=name,
                    **self._request_timeout_kwargs(),
                )
        except ApiException as exc:
            if exc.status == 404:
                return StrictReadOutcome.object_absent(STRICT_READ_REASON_OBJECT_NOT_FOUND)
            return StrictReadOutcome.error(STRICT_READ_REASON_READ_FAILED)
        except Exception:
            return StrictReadOutcome.error(STRICT_READ_REASON_READ_FAILED)

        if not isinstance(resource, dict):
            return StrictReadOutcome.error(STRICT_READ_REASON_MALFORMED_RESPONSE)
        metadata = resource.get("metadata")
        if not isinstance(metadata, dict):
            return StrictReadOutcome.error(STRICT_READ_REASON_MALFORMED_RESPONSE)
        return StrictReadOutcome.from_resource(resource, resource_version=metadata.get("resourceVersion"))
```

- [ ] **Step 4: Run and observe the tests pass**

```bash
python -m pytest tests/test_kube_client.py -q -k StrictCustomResourceReads
```

Expected: PASS.

### Cycle 2 — strict Namespace GET and strict Pod LIST

- [ ] **Step 5: Write the failing tests**

Add to `tests/test_kube_client.py`:

```python
class TestStrictCoreReads:
    """R4-03: live namespace absence and complete Pod inventory."""

    def _client(self, namespace_result=None, namespace_error=None, pod_pages=None):
        client = KubeClient.__new__(KubeClient)
        client.request_timeout = 30
        client.dry_run = False
        client.core_v1 = Mock()
        if namespace_result is not None or namespace_error is not None:
            client.core_v1.read_namespace = Mock(return_value=namespace_result, side_effect=namespace_error)
        if pod_pages is not None:
            client.core_v1.list_namespaced_pod = Mock(side_effect=pod_pages)
        return client

    @staticmethod
    def _namespace(name, resource_version="12"):
        """Build a V1Namespace-shaped mock.

        `name` cannot be passed as a Mock keyword: `Mock(name=...)` names the
        mock rather than setting the attribute, so it is assigned explicitly.
        """
        namespace = Mock()
        namespace.metadata = Mock(resource_version=resource_version)
        namespace.metadata.name = name
        return namespace

    @staticmethod
    def _pod_page(names, continue_token=None, resource_version="12"):
        """Build a V1PodList-shaped page: attribute access, not dict access."""
        pods = []
        for pod_name in names:
            pod = Mock()
            pod.to_dict = Mock(return_value={"metadata": {"name": pod_name, "owner_references": []}})
            pods.append(pod)
        page = Mock()
        page.items = pods
        page.metadata = Mock(_continue=continue_token, resource_version=resource_version)
        return page

    # --- get_namespace_strict -------------------------------------------------
    def test_present_namespace_is_items(self):
        outcome = self._client(namespace_result=self._namespace("acm")).get_namespace_strict("acm")
        assert outcome.status is StrictReadStatus.ITEMS

    def test_namespace_get_404_is_namespace_absent(self):
        outcome = self._client(
            namespace_error=ApiException(status=404, reason="Not Found")
        ).get_namespace_strict("acm")
        assert outcome.status is StrictReadStatus.NAMESPACE_ABSENT
        assert outcome.reason == STRICT_READ_REASON_NAMESPACE_NOT_FOUND

    @pytest.mark.parametrize("status", [401, 403, 500, 503])
    def test_namespace_get_failure_is_error_not_absence(self, status):
        outcome = self._client(
            namespace_error=ApiException(status=status, reason="failed")
        ).get_namespace_strict("acm")
        assert outcome.status is StrictReadStatus.ERROR
        assert outcome.proves_absence is False

    def test_namespace_transport_failure_is_error_not_absence(self):
        outcome = self._client(namespace_error=TimeoutError("deadline")).get_namespace_strict("acm")
        assert outcome.status is StrictReadStatus.ERROR

    def test_namespace_with_a_different_returned_name_is_error(self):
        outcome = self._client(
            namespace_result=self._namespace("somewhere-else")
        ).get_namespace_strict("acm")
        assert outcome.status is StrictReadStatus.ERROR

    def test_namespace_get_is_bounded(self):
        client = self._client(namespace_result=self._namespace("acm"))
        client.get_namespace_strict("acm")
        assert client.core_v1.read_namespace.call_args.kwargs["_request_timeout"] == 30

    # --- list_pods_strict -----------------------------------------------------
    def test_zero_pods_is_a_proven_empty_inventory(self):
        outcome = self._client(pod_pages=[self._pod_page([])]).list_pods_strict("acm")
        assert outcome.status is StrictReadStatus.ITEMS
        assert outcome.items == []
        assert outcome.resource_version == "12"

    def test_complete_multi_page_pod_inventory_is_joined_and_bounded(self):
        pages = [self._pod_page(["a"], continue_token="tok"), self._pod_page(["b"])]
        client = self._client(pod_pages=pages)
        outcome = client.list_pods_strict("acm")
        assert [p["metadata"]["name"] for p in outcome.items] == ["a", "b"]
        calls = client.core_v1.list_namespaced_pod.call_args_list
        assert [call.kwargs["limit"] for call in calls] == [STRICT_READ_PAGE_LIMIT] * 2
        assert [call.kwargs["_continue"] for call in calls] == [None, "tok"]
        assert all(call.kwargs["_request_timeout"] == 30 for call in calls)

    def test_pod_later_page_failure_exposes_no_partial_inventory(self):
        pages = [self._pod_page(["a"], continue_token="tok"), ApiException(status=500, reason="boom")]
        outcome = self._client(pod_pages=pages).list_pods_strict("acm")
        assert outcome.status is StrictReadStatus.ERROR
        assert outcome.items == []

    def test_pod_expired_continuation_restarts_the_whole_read_once(self):
        pages = [
            self._pod_page(["a"], continue_token="tok"),
            ApiException(status=410, reason="Gone"),
            self._pod_page(["a"], continue_token="tok"),
            self._pod_page(["b"]),
        ]
        client = self._client(pod_pages=pages)
        outcome = client.list_pods_strict("acm")
        assert [p["metadata"]["name"] for p in outcome.items] == ["a", "b"]
        assert client.core_v1.list_namespaced_pod.call_args_list[2].kwargs["_continue"] is None

    def test_pod_second_expiry_fails_closed(self):
        pages = [
            self._pod_page(["a"], continue_token="tok"),
            ApiException(status=410, reason="Gone"),
            self._pod_page(["a"], continue_token="tok"),
            ApiException(status=410, reason="Gone"),
        ]
        outcome = self._client(pod_pages=pages).list_pods_strict("acm")
        assert outcome.status is StrictReadStatus.ERROR

    def test_pod_outstanding_continuation_at_exit_is_incomplete(self):
        pages = [self._pod_page([], continue_token="tok")] * (STRICT_READ_MAX_PAGES + 5)
        outcome = self._client(pod_pages=pages).list_pods_strict("acm")
        assert outcome.status is StrictReadStatus.ERROR
        assert outcome.reason == STRICT_READ_REASON_INVENTORY_INCOMPLETE

    def test_pod_page_without_items_is_error_not_empty(self):
        page = Mock()
        page.items = None
        page.metadata = Mock(_continue=None, resource_version="12")
        outcome = self._client(pod_pages=[page]).list_pods_strict("acm")
        assert outcome.status is StrictReadStatus.ERROR
        assert outcome.reason == STRICT_READ_REASON_MALFORMED_RESPONSE

    def test_pod_page_with_non_list_items_is_error(self):
        page = Mock()
        page.items = "nope"
        page.metadata = Mock(_continue=None, resource_version="12")
        outcome = self._client(pod_pages=[page]).list_pods_strict("acm")
        assert outcome.status is StrictReadStatus.ERROR

    def test_malformed_pod_member_is_error(self):
        page = Mock()
        page.items = ["not-a-pod"]
        page.metadata = Mock(_continue=None, resource_version="12")
        outcome = self._client(pod_pages=[page]).list_pods_strict("acm")
        assert outcome.status is StrictReadStatus.ERROR

    def test_pod_page_without_list_metadata_is_error(self):
        page = Mock()
        page.items = []
        page.metadata = None
        outcome = self._client(pod_pages=[page]).list_pods_strict("acm")
        assert outcome.status is StrictReadStatus.ERROR

    def test_pod_list_404_with_a_present_namespace_is_error(self):
        client = self._client(
            pod_pages=[ApiException(status=404, reason="Not Found")],
            namespace_result=self._namespace("acm"),
        )
        outcome = client.list_pods_strict("acm")
        assert outcome.status is StrictReadStatus.ERROR

    def test_pod_list_404_with_a_fresh_namespace_404_is_namespace_absent(self):
        client = self._client(
            pod_pages=[ApiException(status=404, reason="Not Found")],
            namespace_error=ApiException(status=404, reason="Not Found"),
        )
        outcome = client.list_pods_strict("acm")
        assert outcome.status is StrictReadStatus.NAMESPACE_ABSENT
        client.core_v1.read_namespace.assert_called_once()

    def test_pod_list_404_with_an_unreadable_namespace_is_error(self):
        client = self._client(
            pod_pages=[ApiException(status=404, reason="Not Found")],
            namespace_error=ApiException(status=403, reason="Forbidden"),
        )
        assert client.list_pods_strict("acm").status is StrictReadStatus.ERROR

    @pytest.mark.parametrize("status", [401, 403, 500, 503])
    def test_pod_list_failure_is_error_not_empty(self, status):
        outcome = self._client(
            pod_pages=[ApiException(status=status, reason="failed")]
        ).list_pods_strict("acm")
        assert outcome.status is StrictReadStatus.ERROR
        assert outcome.items == []
```

- [ ] **Step 6: Run and observe the expected failure**

```bash
python -m pytest tests/test_kube_client.py -q -k StrictCoreReads
```

Expected: FAIL — `AttributeError: 'KubeClient' object has no attribute 'get_namespace_strict'`,
then the same for `list_pods_strict`.

- [ ] **Step 7: Implement the two core strict reads**

```python
    def get_namespace_strict(self, name: str) -> StrictReadOutcome:
        """Prove one Namespace present or positively absent, or fail closed."""
        self._validate_resource_inputs(name=name, resource_type="namespace")
        try:
            namespace = self.core_v1.read_namespace(name, **self._request_timeout_kwargs())
        except ApiException as exc:
            if exc.status == 404:
                return StrictReadOutcome.namespace_absent(STRICT_READ_REASON_NAMESPACE_NOT_FOUND)
            return StrictReadOutcome.error(STRICT_READ_REASON_READ_FAILED)
        except Exception:
            return StrictReadOutcome.error(STRICT_READ_REASON_READ_FAILED)

        metadata = getattr(namespace, "metadata", None)
        returned_name = getattr(metadata, "name", None)
        if not isinstance(returned_name, str) or returned_name != name:
            return StrictReadOutcome.error(STRICT_READ_REASON_MALFORMED_RESPONSE)
        return StrictReadOutcome.from_resource(
            {"metadata": {"name": returned_name}},
            resource_version=getattr(metadata, "resource_version", None),
        )

    def list_pods_strict(
        self, namespace: str, label_selector: Optional[str] = None
    ) -> StrictReadOutcome:
        """Strictly list Pods in one namespace, or fail closed.

        A Pod LIST 404 is not zero Pods: it is resolved by a fresh named
        Namespace GET, and only a positive namespace absence is propagated.
        """
        self._validate_resource_inputs(namespace=namespace)

        for _ in range(STRICT_READ_MAX_RESTARTS + 1):
            outcome = self._drain_strict_pod_list(namespace, label_selector)
            if outcome is not _RESTART_READ:
                return outcome
        return StrictReadOutcome.error(STRICT_READ_REASON_INVENTORY_INCOMPLETE)

    def _drain_strict_pod_list(self, namespace, label_selector):
        items: List[Dict[str, Any]] = []
        continue_token: Optional[str] = None

        for _ in range(STRICT_READ_MAX_PAGES):
            try:
                page = self.core_v1.list_namespaced_pod(
                    namespace=namespace,
                    label_selector=label_selector,
                    _continue=continue_token,
                    limit=STRICT_READ_PAGE_LIMIT,
                    **self._request_timeout_kwargs(),
                )
            except ApiException as exc:
                if exc.status == 410 and continue_token is not None:
                    return _RESTART_READ
                if exc.status == 404:
                    # Absence of the Pod collection is only meaningful if the
                    # namespace itself is positively absent; prove that live.
                    namespace_outcome = self.get_namespace_strict(namespace)
                    if namespace_outcome.status is StrictReadStatus.NAMESPACE_ABSENT:
                        return namespace_outcome
                    return StrictReadOutcome.error(STRICT_READ_REASON_READ_FAILED)
                return StrictReadOutcome.error(STRICT_READ_REASON_READ_FAILED)
            except Exception:
                return StrictReadOutcome.error(STRICT_READ_REASON_READ_FAILED)

            page_items = getattr(page, "items", None)
            if not isinstance(page_items, list):
                return StrictReadOutcome.error(STRICT_READ_REASON_MALFORMED_RESPONSE)
            for pod in page_items:
                converted = self._model_to_mapping(pod)
                if converted is None:
                    return StrictReadOutcome.error(STRICT_READ_REASON_MALFORMED_RESPONSE)
                items.append(converted)

            metadata = getattr(page, "metadata", None)
            if metadata is None:
                return StrictReadOutcome.error(STRICT_READ_REASON_MALFORMED_RESPONSE)
            continue_token = getattr(metadata, "_continue", None) or None
            if continue_token is None:
                return StrictReadOutcome.from_items(
                    items, resource_version=getattr(metadata, "resource_version", None)
                )

        return StrictReadOutcome.error(STRICT_READ_REASON_INVENTORY_INCOMPLETE)

    @staticmethod
    def _model_to_mapping(model) -> Optional[Dict[str, Any]]:
        """Convert one client model to a mapping, or None when it is not one."""
        to_dict = getattr(model, "to_dict", None)
        if not callable(to_dict):
            return None
        converted = to_dict()
        return converted if isinstance(converted, dict) else None
```

- [ ] **Step 8: Run and observe the tests pass**

```bash
python -m pytest tests/test_kube_client.py -q -k StrictCoreReads
```

Expected: PASS.

### Cycle 3 — strict Deployment and ReplicaSet named GET

- [ ] **Step 9: Write the failing tests**

```python
class TestStrictAppsReads:
    """R4-03: identity-bearing Apps reads for the owner chain."""

    def _client(self, result=None, error=None, kind="deployment"):
        client = KubeClient.__new__(KubeClient)
        client.request_timeout = 30
        client.dry_run = False
        client.apps_v1 = Mock()
        reader = Mock(return_value=result, side_effect=error)
        if kind == "deployment":
            client.apps_v1.read_namespaced_deployment = reader
        else:
            client.apps_v1.read_namespaced_replica_set = reader
        return client

    @staticmethod
    def _object(name="multiclusterhub-operator", namespace="open-cluster-management", uid="u-1"):
        model = Mock()
        model.to_dict = Mock(
            return_value={"metadata": {"name": name, "namespace": namespace, "uid": uid}}
        )
        return model

    @pytest.mark.parametrize("kind, method", [("deployment", "get_deployment_strict"),
                                              ("replicaset", "get_replicaset_strict")])
    def test_present_object_carries_identity(self, kind, method):
        client = self._client(result=self._object(), kind=kind)
        outcome = getattr(client, method)("multiclusterhub-operator", "open-cluster-management")
        assert outcome.status is StrictReadStatus.ITEMS
        assert outcome.resource["metadata"]["uid"] == "u-1"

    @pytest.mark.parametrize("kind, method", [("deployment", "get_deployment_strict"),
                                              ("replicaset", "get_replicaset_strict")])
    def test_explicit_404_is_object_absent(self, kind, method):
        client = self._client(error=ApiException(status=404, reason="Not Found"), kind=kind)
        outcome = getattr(client, method)("multiclusterhub-operator", "open-cluster-management")
        assert outcome.status is StrictReadStatus.OBJECT_ABSENT

    @pytest.mark.parametrize("status", [401, 403, 500, 503])
    def test_api_failure_is_error_not_absence(self, status):
        client = self._client(error=ApiException(status=status, reason="failed"))
        outcome = client.get_deployment_strict("d", "ns")
        assert outcome.status is StrictReadStatus.ERROR
        assert outcome.proves_absence is False

    def test_missing_uid_is_error(self):
        client = self._client(result=self._object(uid=""))
        assert client.get_deployment_strict("multiclusterhub-operator",
                                            "open-cluster-management").status is StrictReadStatus.ERROR

    def test_mismatched_identity_is_error(self):
        client = self._client(result=self._object(name="something-else"))
        assert client.get_deployment_strict("multiclusterhub-operator",
                                            "open-cluster-management").status is StrictReadStatus.ERROR

    def test_malformed_object_is_error(self):
        model = Mock()
        model.to_dict = Mock(return_value="nope")
        client = self._client(result=model)
        assert client.get_deployment_strict("d", "ns").status is StrictReadStatus.ERROR

    def test_apps_get_is_bounded(self):
        client = self._client(result=self._object())
        client.get_deployment_strict("multiclusterhub-operator", "open-cluster-management")
        assert client.apps_v1.read_namespaced_deployment.call_args.kwargs["_request_timeout"] == 30
```

- [ ] **Step 10: Run and observe the expected failure**

```bash
python -m pytest tests/test_kube_client.py -q -k StrictAppsReads
```

Expected: FAIL — `AttributeError: 'KubeClient' object has no attribute 'get_deployment_strict'`.

- [ ] **Step 11: Implement the two Apps strict reads**

```python
    def get_deployment_strict(self, name: str, namespace: str) -> StrictReadOutcome:
        """Strictly GET one Deployment with its identity, or fail closed."""
        return self._get_apps_object_strict(
            self.apps_v1.read_namespaced_deployment, name, namespace
        )

    def get_replicaset_strict(self, name: str, namespace: str) -> StrictReadOutcome:
        """Strictly GET one ReplicaSet with its identity, or fail closed."""
        return self._get_apps_object_strict(
            self.apps_v1.read_namespaced_replica_set, name, namespace
        )

    def _get_apps_object_strict(self, reader, name: str, namespace: str) -> StrictReadOutcome:
        """One bounded named Apps GET whose success always carries identity."""
        self._validate_resource_inputs(namespace, name, "apps object")
        try:
            model = reader(name=name, namespace=namespace, **self._request_timeout_kwargs())
        except ApiException as exc:
            if exc.status == 404:
                return StrictReadOutcome.object_absent(STRICT_READ_REASON_OBJECT_NOT_FOUND)
            return StrictReadOutcome.error(STRICT_READ_REASON_READ_FAILED)
        except Exception:
            return StrictReadOutcome.error(STRICT_READ_REASON_READ_FAILED)

        resource = self._model_to_mapping(model)
        if resource is None:
            return StrictReadOutcome.error(STRICT_READ_REASON_MALFORMED_RESPONSE)
        metadata = resource.get("metadata")
        if not isinstance(metadata, dict):
            return StrictReadOutcome.error(STRICT_READ_REASON_MALFORMED_RESPONSE)
        if metadata.get("name") != name or metadata.get("namespace") != namespace:
            return StrictReadOutcome.error(STRICT_READ_REASON_MALFORMED_RESPONSE)
        uid = metadata.get("uid")
        if not isinstance(uid, str) or not uid:
            return StrictReadOutcome.error(STRICT_READ_REASON_MALFORMED_RESPONSE)
        return StrictReadOutcome.from_resource(
            resource, resource_version=metadata.get("resource_version")
        )
```

- [ ] **Step 12: Run and observe the tests pass**

```bash
python -m pytest tests/test_kube_client.py -q -k StrictAppsReads
```

Expected: PASS.

- [ ] **Step 13: Refactor**

`_drain_strict_list` and `_drain_strict_pod_list` each perform exactly one responsibility and
differ only in transport and in dictionary-versus-model page shape; do not merge them behind a
conditional, and do not introduce a new module for them. If the malformed-response returns repeat,
extract one local `_malformed()` helper inside `lib/kube_client.py`. The three named core/Apps GET
helpers share only `_model_to_mapping`; they do not own operator-owner-chain policy. Confirm no
strict method logs a response body or an exception object.

- [ ] **Step 14: Broader gate and commit**

```bash
python -m pytest tests/test_kube_client.py tests/test_strict_read.py -q
git add lib/kube_client.py tests/test_kube_client.py
git commit -m "feat: add closed strict Kubernetes read surface"
```

## Task A4: Extend the collection read-outcome seam

**Purpose:** Give the collection the same algebra by extending the merged R3-02 module, with no
second read abstraction.

**Files:** Modify `plugins/modules/acm_k8s_read_outcome.py`; modify
`plugins/module_utils/constants.py`; modify `tests/unit/test_k8s_read_outcome.py`; modify
`tests/integration/test_k8s_read_outcome_runtime.py`.
Modify the two existing R3-02 role call sites and their fixtures/contracts to pass canonical
`resource_name: pods` and `resource_name: configmaps`.

**Interfaces produced:** `read_status` gains `kind_not_served`; list mode becomes
completeness-proving under the same bounds as Python; the module gains required `resource_name`,
the exact canonical APIResource name/plural for `kind`.

**Intended behavior.**

1. **Complete, bounded pagination.** List mode sends a fixed positive `limit`
   (`STRICT_READ_PAGE_LIMIT`), a bounded `_request_timeout`
   (`STRICT_READ_REQUEST_TIMEOUT`), and the server-provided `_continue` token on every page, and
   repeats until the response carries no continuation. `ok` therefore asserts a positively complete
   inventory. A later-page failure, a page budget exhausted with an outstanding continuation, or a
   malformed page is `error` and publishes **no** partial prefix.
2. **One whole-read restart on an expired continuation.** An HTTP 410 on a continuation page
   discards every accumulated member and restarts the read from page 1 exactly
   `STRICT_READ_MAX_RESTARTS` times; a second expiry is `error`. 410 is detected as
   `getattr(exc, "status", None) == 410`, matching the module's existing `_is_named_not_found`
   pattern; the dynamic client maps 410 to `GoneError`, a `DynamicApiError` subclass that carries
   `status`, identically at both pinned client tags (§15.2).
3. **Positive kind absence.** When `api_client.resource(kind, api_version)` raises, the module
   does not classify from the exception. It issues its own bounded discovery request for the exact
   group/version through the same client and returns `kind_not_served` only when that request
   succeeds, decodes to a structurally valid APIResourceList, and positively lacks the caller's
   exact `resource_name`. Anything else is `error` (§9.2). The module never derives a plural from
   `kind`. A resource that discovery positively serves but for which no usable resource handle
   could be obtained is `error`, never `ok` and never absence.
4. **Canonical resource-name flow.** `resource_name` is a required non-empty Kubernetes resource
   name, validated before any client work. PR A updates every existing call site: the compactor
   passes `pods`, immediate-import passes `configmaps`, and their fixtures/direct module
   invocations do the same. C passes `multiclusterobservabilities`; D passes `managedclusters` and
   `clusterdeployments`; E passes `multiclusterhubs`, `clusterserviceversions`, and every exact
   core/API resource name it probes. This is the sole flow; there is no `kind.lower()`, suffixing,
   inflector, or fallback.
5. **Namespace absence stays composed.** The module gains no namespace-probing mode. A caller
   proves namespace absence with `read_mode: get`, `api_version: v1`, `kind: Namespace`,
   `resource_name: namespaces`.

**Exact discovery transport.** `get_api_client(...)` returns `kubernetes.core`'s `K8SClient`, whose
`.client` attribute is the `kubernetes.dynamic.DynamicClient`. Discovery therefore issues:

```python
response = api_client.client.request(
    "GET", path, serialize=False, _request_timeout=STRICT_READ_REQUEST_TIMEOUT
)
body = json.loads(response.data.decode("utf8"))
```

`path` is `/apis/{api_version}` when `api_version` contains a group separator and `/api/{api_version}`
otherwise. `serialize=False` makes `DynamicClient.request` return the raw response object rather
than a `ResourceInstance`, and `_request_timeout` is forwarded to `ApiClient.call_api`; both are
verified at the pinned client tags (§15.2). Any exception, non-2xx, undecodable body, non-mapping
body, `kind != "APIResourceList"`, missing or non-list `resources`, or a malformed entry makes the
determination unverifiable, which is `error` — never absence.

**Failure behavior.** Unchanged for every existing failure: sanitized `error`, no raw bodies.

**Check-mode implications.** The module remains read-only and continues to perform its read in
check mode — existing tested behavior, preserved deliberately.

**Parity implications.** `kind_not_served` maps to Python `crd_absent`; complete `resources` maps
to `items`; the three numeric bounds are the mirrored constants from Task A1. Held equal by Task A5.

**RBAC implications:** none new. `limit` and `continue` are query parameters on the same `list`
verb, and discovery endpoints require no verb (§14 row 1).

**Deliberate reclassification.** The existing unit and runtime expectations for the positive
discovery miss currently assert `error`; they are inverted to `kind_not_served`. This is a change
to merged R3-02 code, so both runtime consumer lanes are rerun in Task A6.

- [ ] **Step 1: Extend the existing unit-test harness**

`tests/unit/test_k8s_read_outcome.py` already drives the module through `_run_module(monkeypatch,
params=..., client=...)` with `_FakeClient(resource=, resource_error=, get_result=, get_error=)`,
`_DictResult`, and `_api_error(exc_type, status)`. Extend that harness in place — do not add a new
support module and do not invent a second driver:

```python
import json


class _RawResponse:
    """`serialize=False` makes DynamicClient.request return the raw response object."""

    def __init__(self, body):
        if isinstance(body, bytes):
            self.data = body
        elif isinstance(body, str):
            self.data = body.encode("utf-8")
        else:
            self.data = json.dumps(body).encode("utf-8")


class _FakeDynamicClient:
    """Stands in for the DynamicClient that K8SClient exposes as `.client`."""

    def __init__(self, discovery=None, discovery_error=None):
        self._discovery = discovery
        self._discovery_error = discovery_error
        self.request_calls: list[dict] = []

    def request(self, method, path, **params):
        self.request_calls.append({"method": method, "path": path, **params})
        if self._discovery_error is not None:
            raise self._discovery_error
        return _RawResponse(self._discovery)
```

and extend `_FakeClient` with a page sequence, a recorded parameter log, and the dynamic client:

```python
class _FakeClient:
    def __init__(self, *, resource=None, resource_error=None, get_result=None, get_error=None,
                 pages=None, dynamic=None):
        self._resource = resource
        self._resource_error = resource_error
        self._get_result = get_result
        self._get_error = get_error
        self._pages = pages
        self.client = dynamic          # what the module reads for discovery
        self.get_calls = 0
        self.resource_calls = 0
        self.get_params: list[dict] = []

    def resource(self, kind, api_version):
        self.resource_calls += 1
        if self._resource_error is not None:
            raise self._resource_error
        return self._resource

    def get(self, resource, **params):
        self.get_calls += 1
        self.get_params.append(params)
        if self._pages is not None:
            page = self._pages[self.get_calls - 1]
            if isinstance(page, BaseException):
                raise page
            return page
        if self._get_error is not None:
            raise self._get_error
        return self._get_result
```

Every existing list-mode fixture in this file gains explicit list metadata
(`"metadata": {"resourceVersion": "1"}`), because the strict list path now requires readable
continuation metadata. The merged runtime consumer fixture at
`tests/integration/test_r3_02_compactor_runtime.py:152-153` already carries it, so no consumer
expectation changes.

- [ ] **Step 2: Write the failing tests**

Add to `tests/unit/test_k8s_read_outcome.py`. Every direct invocation supplies the exact canonical
`resource_name`; none is defaulted or synthesized.

```python
LIST_PARAMS = {
    "read_mode": "list",
    "api_version": "v1",
    "kind": "Pod",
    "namespace": "ns",
    "resource_name": "pods",
}


def _page(items, continue_token=None):
    metadata = {"resourceVersion": "1"}
    if continue_token:
        metadata["continue"] = continue_token
    return _DictResult({"kind": "PodList", "items": items, "metadata": metadata})


def test_list_mode_follows_continue_tokens_to_exhaustion(monkeypatch):
    client = _FakeClient(
        resource=object(),
        pages=[_page([{"metadata": {"name": "a"}}], "tok"), _page([{"metadata": {"name": "b"}}])],
    )
    result = _run_module(monkeypatch, params=LIST_PARAMS, client=client)
    assert result["read_status"] == "ok"
    assert [r["metadata"]["name"] for r in result["resources"]] == ["a", "b"]
    assert [p.get("_continue") for p in client.get_params] == [None, "tok"]


def test_every_list_page_carries_the_fixed_limit_and_a_bounded_timeout(monkeypatch):
    client = _FakeClient(resource=object(), pages=[_page([], "tok"), _page([])])
    _run_module(monkeypatch, params=LIST_PARAMS, client=client)
    assert [p["limit"] for p in client.get_params] == [
        constants.STRICT_READ_PAGE_LIMIT,
        constants.STRICT_READ_PAGE_LIMIT,
    ]
    assert all(p["_request_timeout"] == constants.STRICT_READ_REQUEST_TIMEOUT
               for p in client.get_params)


def test_list_mode_page_failure_is_error_and_returns_no_partial_inventory(monkeypatch):
    client = _FakeClient(
        resource=object(),
        pages=[_page([{"metadata": {"name": "a"}}], "tok"), _api_error(InternalServerError, 500)],
    )
    result = _run_module(monkeypatch, params=LIST_PARAMS, client=client)
    assert result["read_status"] == "error"
    assert result["resources"] == []


def test_list_mode_outstanding_continuation_at_exit_is_error(monkeypatch):
    client = _FakeClient(
        resource=object(),
        pages=[_page([], "tok")] * (constants.STRICT_READ_MAX_PAGES + 5),
    )
    result = _run_module(monkeypatch, params=LIST_PARAMS, client=client)
    assert result["read_status"] == "error"
    assert result["resources"] == []


def test_expired_continuation_restarts_the_whole_read_once(monkeypatch):
    client = _FakeClient(
        resource=object(),
        pages=[
            _page([{"metadata": {"name": "a"}}], "tok"),
            _api_error(GoneError, 410),
            _page([{"metadata": {"name": "a"}}], "tok"),
            _page([{"metadata": {"name": "b"}}]),
        ],
    )
    result = _run_module(monkeypatch, params=LIST_PARAMS, client=client)
    assert result["read_status"] == "ok"
    # The pre-410 prefix is discarded, not carried into the restart.
    assert [r["metadata"]["name"] for r in result["resources"]] == ["a", "b"]
    assert client.get_params[2].get("_continue") is None


def test_second_expired_continuation_is_error_with_no_partial_output(monkeypatch):
    client = _FakeClient(
        resource=object(),
        pages=[
            _page([{"metadata": {"name": "a"}}], "tok"),
            _api_error(GoneError, 410),
            _page([{"metadata": {"name": "a"}}], "tok"),
            _api_error(GoneError, 410),
        ],
    )
    result = _run_module(monkeypatch, params=LIST_PARAMS, client=client)
    assert result["read_status"] == "error"
    assert result["resources"] == []


@pytest.mark.parametrize(
    "page",
    [
        _DictResult({"kind": "PodList", "metadata": {"resourceVersion": "1"}}),   # items missing
        _DictResult({"kind": "PodList", "items": None, "metadata": {"resourceVersion": "1"}}),
        _DictResult({"kind": "PodList", "items": "nope", "metadata": {"resourceVersion": "1"}}),
        _DictResult({"kind": "PodList", "items": ["nope"], "metadata": {"resourceVersion": "1"}}),
        _DictResult({"kind": "PodList", "items": [], "metadata": "nope"}),
        _DictResult({"kind": "PodList", "items": []}),                            # metadata missing
    ],
)
def test_malformed_list_pages_are_error_never_empty_success(monkeypatch, page):
    client = _FakeClient(resource=object(), pages=[page])
    result = _run_module(monkeypatch, params=LIST_PARAMS, client=client)
    assert result["read_status"] == "error"
    assert result["resources"] == []


def test_positive_discovery_miss_is_kind_not_served(monkeypatch):
    client = _FakeClient(
        resource_error=ResourceNotFoundError("no matches"),
        dynamic=_FakeDynamicClient(
            discovery={"kind": "APIResourceList", "resources": [{"name": "pods", "kind": "Pod"}]}
        ),
    )
    result = _run_module(
        monkeypatch,
        params={
            "read_mode": "list",
            "api_version": "operator.open-cluster-management.io/v1",
            "kind": "MultiClusterHub",
            "resource_name": "multiclusterhubs",
        },
        client=client,
    )
    assert result["read_status"] == "kind_not_served"


def test_discovery_request_is_bounded_and_targets_the_exact_group_version(monkeypatch):
    dynamic = _FakeDynamicClient(
        discovery={"kind": "APIResourceList", "resources": [{"name": "pods", "kind": "Pod"}]}
    )
    client = _FakeClient(resource_error=ResourceNotFoundError("no matches"), dynamic=dynamic)
    _run_module(
        monkeypatch,
        params={
            "read_mode": "list",
            "api_version": "operator.open-cluster-management.io/v1",
            "kind": "MultiClusterHub",
            "resource_name": "multiclusterhubs",
        },
        client=client,
    )
    call = dynamic.request_calls[0]
    assert call["path"] == "/apis/operator.open-cluster-management.io/v1"
    assert call["_request_timeout"] == constants.STRICT_READ_REQUEST_TIMEOUT
    assert call["serialize"] is False


def test_core_group_discovery_uses_the_core_path(monkeypatch):
    dynamic = _FakeDynamicClient(
        discovery={"kind": "APIResourceList", "resources": [{"name": "pods", "kind": "Pod"}]}
    )
    client = _FakeClient(resource_error=ResourceNotFoundError("no matches"), dynamic=dynamic)
    _run_module(monkeypatch, params=LIST_PARAMS, client=client)
    assert dynamic.request_calls[0]["path"] == "/api/v1"


@pytest.mark.parametrize(
    "dynamic",
    [
        _FakeDynamicClient(discovery_error=_api_error(ServiceUnavailableError, 503)),
        _FakeDynamicClient(discovery_error=_api_error(ForbiddenError, 403)),
        _FakeDynamicClient(discovery_error=_api_error(NotFoundError, 404)),
        _FakeDynamicClient(discovery_error=TimeoutError("deadline exceeded")),
        _FakeDynamicClient(discovery="<html>gateway</html>"),
        _FakeDynamicClient(discovery={"kind": "Status"}),
        _FakeDynamicClient(discovery={"kind": "APIResourceList"}),
        _FakeDynamicClient(discovery={"kind": "APIResourceList", "resources": "nope"}),
        _FakeDynamicClient(discovery={"kind": "APIResourceList", "resources": [{"name": 7}]}),
    ],
)
def test_unverifiable_discovery_is_error_not_kind_not_served(monkeypatch, dynamic):
    client = _FakeClient(resource_error=ResourceNotFoundError("no matches"), dynamic=dynamic)
    result = _run_module(
        monkeypatch,
        params={"read_mode": "list", "api_version": "g/v1", "kind": "Widget",
                "resource_name": "widgets"},
        client=client,
    )
    assert result["read_status"] == "error"


def test_irregular_plural_resource_lookup_success_reads_ok(monkeypatch):
    """The canonical plural is supplied by the caller and the read completes normally."""
    client = _FakeClient(
        resource=object(),
        pages=[_DictResult({"kind": "MultiClusterObservabilityList", "items": [],
                            "metadata": {"resourceVersion": "1"}})],
    )
    result = _run_module(
        monkeypatch,
        params={
            "read_mode": "list",
            "api_version": "observability.open-cluster-management.io/v1beta2",
            "kind": "MultiClusterObservability",
            "resource_name": "multiclusterobservabilities",
        },
        client=client,
    )
    assert result["read_status"] == "ok"
    assert result["resources"] == []


def test_irregular_plural_matches_the_exact_name_and_never_becomes_absence(monkeypatch):
    """Discovery positively serves the irregular plural, but no resource handle exists.

    A synthesized plural would miss the discovery entry and wrongly yield
    `kind_not_served`; the exact canonical name matches, so the outcome is the
    fail-closed `error` for a served kind that could not be read.
    """
    client = _FakeClient(
        resource_error=ResourceNotFoundError("no matches"),
        dynamic=_FakeDynamicClient(
            discovery={
                "kind": "APIResourceList",
                "resources": [{"name": "multiclusterobservabilities",
                               "kind": "MultiClusterObservability"}],
            }
        ),
    )
    result = _run_module(
        monkeypatch,
        params={
            "read_mode": "list",
            "api_version": "observability.open-cluster-management.io/v1beta2",
            "kind": "MultiClusterObservability",
            "resource_name": "multiclusterobservabilities",
        },
        client=client,
    )
    assert result["read_status"] == "error"


@pytest.mark.parametrize("resource_name", [None, "", "   "])
def test_missing_resource_name_is_rejected_before_any_client_work(monkeypatch, resource_name):
    client = _FakeClient(resource=object(), pages=[_page([])])
    result = _run_module(
        monkeypatch,
        params={**LIST_PARAMS, "resource_name": resource_name},
        client=client,
    )
    assert result["read_status"] == "error"
    assert client.resource_calls == 0


def test_return_documentation_lists_every_status():
    import yaml

    documented = yaml.safe_load(acm_k8s_read_outcome.RETURN)["read_status"]["choices"]
    assert sorted(documented) == ["error", "kind_not_served", "not_found", "ok"]


def test_resource_name_is_required_and_module_reports_no_namespace_probing_mode():
    spec = acm_k8s_read_outcome._argument_spec()
    assert spec["read_mode"]["choices"] == ["get", "list"]
    assert spec["resource_name"]["required"] is True
```

Import `GoneError`, `InternalServerError`, and `ServiceUnavailableError` alongside the file's
existing `kubernetes.dynamic.exceptions` imports, and import the collection constants module as
`constants`. Invert the existing positive-discovery-miss expectations in this file and in
`tests/integration/test_k8s_read_outcome_runtime.py` from `error` to `kind_not_served`, and add
`resource_name` to every invocation in both files.

- [ ] **Step 3: Run and observe the expected failures**

```bash
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_k8s_read_outcome.py -q
```

Expected, by test: the pagination and `limit`/timeout assertions fail because only one unbounded
request is made (`client.get_params == [{...no limit...}]`); the `kind_not_served` assertions fail
because the module returns `error`; the 410 restart tests fail because the exception propagates to
`error` on the first expiry; the malformed-page parametrization fails for the missing-`items` and
missing-`metadata` cases because `_normalize_resources` maps a missing `items` to `[]` and reports
`ok`; the `resource_name` tests fail with `KeyError: 'resource_name'` from `_argument_spec()`; and
the RETURN assertion fails because `choices` lists three statuses.

- [ ] **Step 4: Implement**

In `plugins/modules/acm_k8s_read_outcome.py`:

Add `import json` and import `STRICT_READ_MAX_PAGES`, `STRICT_READ_MAX_RESTARTS`,
`STRICT_READ_PAGE_LIMIT`, and `STRICT_READ_REQUEST_TIMEOUT` from the collection's
`module_utils/constants.py`.

Add the required `resource_name` option to `DOCUMENTATION` and `_argument_spec()`
(`{"type": "str", "required": True}`), described as "the exact canonical Kubernetes APIResource
name (plural) for `kind`; never synthesized from `kind`".

Update the `RETURN` block's `read_status` description and `choices` to
`[ok, not_found, kind_not_served, error]`, describing `kind_not_served` as "the API group/version
was read successfully and positively does not serve this kind".

Add the strict list-page normalizer. It is a **new** function: `_normalize_resources` keeps its
current behavior for `read_mode: get`, and is not reused for list pages, because its
`mapping.get("items", [])` maps a missing `items` field to an empty list.

```python
def _strict_list_page(raw) -> tuple[list[dict] | None, str | None]:
    """Return (members, continue_token) for one list page, or (None, None) if malformed."""
    mapping = _to_mapping(raw)
    if mapping is None:
        return None, None
    if "items" not in mapping:
        return None, None
    items = mapping.get("items")
    if not isinstance(items, list):
        return None, None
    members: list[dict] = []
    for item in items:
        item_mapping = _to_mapping(item)
        if item_mapping is None:
            return None, None
        members.append(item_mapping)
    metadata = mapping.get("metadata")
    if not isinstance(metadata, dict):
        return None, None
    token = metadata.get("continue") or None
    return members, token


def _discovery_serves(api_client, api_version: str, resource_name: str) -> bool | None:
    """True if served, False if positively absent, None if unverifiable.

    The dynamic client's discovery cache substitutes an empty resource list
    for some discovery-fetch failures, and the substituted set differs across
    the supported client range, so a lookup miss alone never proves absence.
    """
    path = f"/apis/{api_version}" if "/" in api_version else f"/api/{api_version}"
    try:
        response = api_client.client.request(
            "GET", path, serialize=False, _request_timeout=STRICT_READ_REQUEST_TIMEOUT
        )
        body = json.loads(response.data.decode("utf8"))
    except Exception:
        return None
    if not isinstance(body, dict) or body.get("kind") != "APIResourceList":
        return None
    resources = body.get("resources")
    if not isinstance(resources, list):
        return None
    for entry in resources:
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("name"), str)
            or not entry["name"]
            or not isinstance(entry.get("kind"), str)
            or not entry["kind"]
        ):
            return None
        if entry["name"] == resource_name:
            return True
    return False


def _drain_list(api_client, resource, params) -> tuple[list[dict] | None, str]:
    """Drain every page of one list, or fail closed. Never returns a partial prefix."""
    for _ in range(STRICT_READ_MAX_RESTARTS + 1):
        collected, status = _drain_list_once(api_client, resource, params)
        if status != "restart":
            return collected, status
    return None, "error"


def _drain_list_once(api_client, resource, params) -> tuple[list[dict] | None, str]:
    collected: list[dict] = []
    continue_token = None
    for _ in range(STRICT_READ_MAX_PAGES):
        page_params = dict(params)
        page_params["limit"] = STRICT_READ_PAGE_LIMIT
        page_params["_request_timeout"] = STRICT_READ_REQUEST_TIMEOUT
        if continue_token:
            page_params["_continue"] = continue_token
        else:
            page_params["_continue"] = None
        try:
            raw = api_client.get(resource, **page_params)
        except Exception as exc:
            if getattr(exc, "status", None) == 410 and continue_token:
                # Expired continuation: discard everything and restart the read.
                return None, "restart"
            return None, "error"
        members, token = _strict_list_page(raw)
        if members is None:
            return None, "error"
        collected.extend(members)
        continue_token = token
        if not continue_token:
            return collected, "ok"
    return None, "error"
```

Wire them into `run_module`. Validate `resource_name` immediately after the existing `name`
validation, before `get_api_client`:

```python
    resource_name = module.params.get("resource_name")
    if not isinstance(resource_name, str) or not resource_name.strip():
        _exit_outcome(module, "error")
        return
```

Replace the `resource` lookup's `except Exception: _exit_outcome(module, "error")` with:

```python
    try:
        resource = api_client.resource(module.params["kind"], module.params["api_version"])
    except Exception:
        served = _discovery_serves(api_client, module.params["api_version"], resource_name)
        _exit_outcome(module, "kind_not_served" if served is False else "error")
        return
```

and replace the list branch's single `get` with `_drain_list`:

```python
    if read_mode == "list":
        resources, status = _drain_list(api_client, resource, params)
        if status != "ok":
            _exit_outcome(module, "error")
            return
        _exit_outcome(module, "ok", resources)
        return
```

The `get` branch otherwise remains unchanged and still classifies its own explicit 404 as
`not_found`. Update every existing role/task/test call site in this PR to pass canonical
`resource_name` as listed above.

- [ ] **Step 5: Run and observe the tests pass**

```bash
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_k8s_read_outcome.py -q
export ANSIBLE_COLLECTIONS_PATH="$(pwd):${HOME}/.ansible/collections"
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/integration/test_k8s_read_outcome_runtime.py -q
```

- [ ] **Step 6: Refactor**

`_drain_list`, `_drain_list_once`, `_strict_list_page`, and `_discovery_serves` must not log or
return any part of a response body, and must not return a partial `collected` list on any path.
Confirm the module still exits only through `_exit_outcome`, and that `_normalize_resources` is now
reached only from the `get` branch.

- [ ] **Step 7: Module documentation gate**

```bash
export ANSIBLE_COLLECTIONS_PATH="$(pwd):${HOME}/.ansible/collections"
ansible-test sanity --test validate-modules \
  --python 3.12 plugins/modules/acm_k8s_read_outcome.py
```

Run this from `ansible_collections/tomazb/acm_switchover`. Expected: PASS, proving `RETURN` matches
the module's real status vocabulary and that `resource_name` is documented. **This repository has no
`tests/sanity/` directory and no sanity CI lane** — this is a design-required builder-run gate
(amendment §16 item 2), not a CI gate, and it must not be described as one.

- [ ] **Step 8: Commit**

```bash
git add ansible_collections/tomazb/acm_switchover/plugins/modules/acm_k8s_read_outcome.py \
  ansible_collections/tomazb/acm_switchover/plugins/module_utils/constants.py \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_k8s_read_outcome.py \
  ansible_collections/tomazb/acm_switchover/tests/integration/test_k8s_read_outcome_runtime.py
git commit -m "feat: make collection reads complete and prove kind absence"
```

## Task A5: Shared parity vectors

**Purpose:** One vector set exercising the amendment §6.3 mapping table in both form factors, so
the two independent implementations cannot drift.

**Files:** Create `tests/test_strict_read_parity.py`; modify `tests/test_constants_parity.py`.

**Interfaces consumed:** the Python strict surface and the collection module's classifier
functions, both driven from the same declared vector list.

- [ ] **Step 1: Write the failing parity test**

Create `tests/test_strict_read_parity.py`:

```python
"""Parity contract: the strict read algebra must agree across form factors.

Vectors are declared once and asserted against both implementations. The two
runtimes share no code; this file is what keeps them equal.
"""

import pytest

from lib.strict_read import StrictReadStatus

# (vector id, normative outcome, python status, collection read_status)
VECTORS = [
    ("true_empty", "success, complete inventory", StrictReadStatus.ITEMS, "ok"),
    ("complete_pagination", "success, complete inventory", StrictReadStatus.ITEMS, "ok"),
    ("object_absent", "named-object absence", StrictReadStatus.OBJECT_ABSENT, "not_found"),
    ("kind_not_served", "positive kind-not-served", StrictReadStatus.CRD_ABSENT, "kind_not_served"),
    ("namespace_absent", "positive namespace absence", StrictReadStatus.NAMESPACE_ABSENT, "not_found"),
    ("authorization_failure", "api failure", StrictReadStatus.ERROR, "error"),
    ("transport_failure", "api failure", StrictReadStatus.ERROR, "error"),
    ("discovery_unverifiable", "api failure", StrictReadStatus.ERROR, "error"),
    ("discovery_http_404", "api failure", StrictReadStatus.ERROR, "error"),
    ("malformed_discovery", "malformed response", StrictReadStatus.ERROR, "error"),
    ("malformed_items", "malformed response", StrictReadStatus.ERROR, "error"),
    ("missing_items_key", "malformed response", StrictReadStatus.ERROR, "error"),
    ("later_page_failure", "truncation / incomplete", StrictReadStatus.ERROR, "error"),
    ("outstanding_continuation", "truncation / incomplete", StrictReadStatus.ERROR, "error"),
    ("expired_continuation_restart", "success, complete inventory", StrictReadStatus.ITEMS, "ok"),
    ("second_expired_continuation", "truncation / incomplete", StrictReadStatus.ERROR, "error"),
    ("timeout_exhausted", "timeout / retry exhaustion", StrictReadStatus.ERROR, "error"),
]


@pytest.mark.parametrize("vector_id, _normative, expected_status, _collection", VECTORS)
def test_python_strict_surface_matches_the_vector(vector_id, _normative, expected_status, _collection):
    outcome = run_python_vector(vector_id)
    assert outcome.status is expected_status


@pytest.mark.parametrize("vector_id, _normative, _python, expected_read_status", VECTORS)
def test_collection_module_matches_the_vector(vector_id, _normative, _python, expected_read_status):
    result = run_collection_vector(vector_id)
    assert result["read_status"] == expected_read_status


@pytest.mark.parametrize("vector_id, _normative, python_status, collection_status", VECTORS)
def test_error_is_never_absence_in_either_form_factor(vector_id, _normative, python_status, collection_status):
    if python_status is not StrictReadStatus.ERROR:
        return
    assert run_python_vector(vector_id).proves_absence is False
    assert run_collection_vector(vector_id)["resources"] == []
```

Add one bounds-parity test in the same file, so the two paging implementations cannot drift
numerically:

```python
def test_strict_read_bounds_are_mirrored():
    import inspect

    import ansible_collections.tomazb.acm_switchover.plugins.module_utils.constants as ans_constants

    from lib.kube_client import KubeClient

    for name in ("STRICT_READ_PAGE_LIMIT", "STRICT_READ_MAX_PAGES", "STRICT_READ_MAX_RESTARTS"):
        assert getattr(py_constants, name) == getattr(ans_constants, name), name
    # Python bounds each call with the per-instance request timeout; the collection
    # module has no instance, so its constant must equal that default.
    default_timeout = inspect.signature(KubeClient.__init__).parameters["request_timeout"].default
    assert ans_constants.STRICT_READ_REQUEST_TIMEOUT == default_timeout
```

`run_python_vector` and `run_collection_vector` are defined in this file and build their fakes
from the same vector id, so a vector that is added on one side and forgotten on the other fails.
Each runner asserts the bounds its vector exercises: every list request it observes carries
`limit == STRICT_READ_PAGE_LIMIT` and a bounded request timeout, the `expired_continuation_restart`
runner asserts the restart re-issued page 1 with no continuation token and published no pre-410
prefix, and the `second_expired_continuation` runner asserts an empty result on both sides.
Root `tests/` must import the collection module lazily inside `run_collection_vector` so the file
stays import-safe without `ansible-core` (`AGENTS.md` standing CI constraint). The
`namespace_absent` Python runner must call `KubeClient.get_namespace_strict`; it may not construct
`StrictReadOutcome.namespace_absent(...)` directly. The discovery runners pass the exact
canonical `resource_name` and include the irregular
`MultiClusterObservability`/`multiclusterobservabilities` case.

- [ ] **Step 2: Run and observe the expected failure**

```bash
python -m pytest tests/test_strict_read_parity.py -q
```

Expected: FAIL — the vector runners are not yet implemented.

- [ ] **Step 3: Implement the runners, then rerun**

```bash
python -m pytest tests/test_strict_read_parity.py -q
```

- [ ] **Step 4: Add the reason codes to the constants parity contract**

Add to `CONSTANT_PAIRS` in `tests/test_constants_parity.py`:

```python
    # R4-03 strict-read reason codes
    "STRICT_READ_REASON_KIND_NOT_SERVED": "STRICT_READ_REASON_KIND_NOT_SERVED",
    "STRICT_READ_REASON_NAMESPACE_NOT_FOUND": "STRICT_READ_REASON_NAMESPACE_NOT_FOUND",
    "STRICT_READ_REASON_OBJECT_NOT_FOUND": "STRICT_READ_REASON_OBJECT_NOT_FOUND",
    "STRICT_READ_REASON_DISCOVERY_UNVERIFIABLE": "STRICT_READ_REASON_DISCOVERY_UNVERIFIABLE",
    "STRICT_READ_REASON_INVENTORY_INCOMPLETE": "STRICT_READ_REASON_INVENTORY_INCOMPLETE",
    "STRICT_READ_REASON_MALFORMED_RESPONSE": "STRICT_READ_REASON_MALFORMED_RESPONSE",
    "STRICT_READ_REASON_READ_FAILED": "STRICT_READ_REASON_READ_FAILED",
    # R4-03 strict-read bounds
    "STRICT_READ_PAGE_LIMIT": "STRICT_READ_PAGE_LIMIT",
    "STRICT_READ_MAX_PAGES": "STRICT_READ_MAX_PAGES",
    "STRICT_READ_MAX_RESTARTS": "STRICT_READ_MAX_RESTARTS",
```

`STRICT_READ_REQUEST_TIMEOUT` is deliberately **not** in `CONSTANT_PAIRS`: it is collection-only,
and its equality with the Python per-instance default is asserted by
`test_strict_read_bounds_are_mirrored` above.

```bash
python -m pytest tests/test_constants_parity.py -q
```

- [ ] **Step 5: Commit**

```bash
git add tests/test_strict_read_parity.py tests/test_constants_parity.py
git commit -m "test: hold the strict read algebra equal across form factors"
```

## Task A6: PR A verification and consumer regression

**Purpose:** Prove the extension did not change behavior for the two merged R3-02 consumers, and
close the PR's gate set.

- [ ] **Step 1: Consumer regression lanes**

These two lanes are the only ones that can falsify the "no consumer regression" claim. The static
role-contract tests supplement them and do not replace them.

```bash
export ANSIBLE_COLLECTIONS_PATH="$(pwd):${HOME}/.ansible/collections"
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/integration/test_r3_02_compactor_runtime.py \
  ansible_collections/tomazb/acm_switchover/tests/integration/test_r3_02_activation_runtime.py -q
```

Expected: PASS with no behavioral expectation edits. PR A mechanically adds `resource_name: pods`
and `resource_name: configmaps` to these existing call sites and fixtures. Both consumers read
always-served core kinds (`Pod`, `ConfigMap`), so `kind_not_served` is unreachable for them;
`scale_observability.yml` already
fails closed on any non-`ok` status and `apply_immediate_import.yml` handles `not_found`
distinctly. If either lane needs an expectation edit, stop: that is a real consumer regression,
not a test that needs updating.

- [ ] **Step 2: Root and parity gates**

```bash
python -m pytest tests/test_strict_read.py tests/test_strict_read_parity.py \
  tests/test_kube_client.py tests/test_constants_parity.py \
  tests/test_r3_02_fail_closed_parity.py -q
python -m pytest tests/ --ignore=tests/release -v -m "not e2e"
```

- [ ] **Step 3: Collection surfaces 3 through 7**

Run every command in the "Commands by surface" block of
[`docs/development/testing.md`](../development/testing.md) for surfaces 3, 4, 5, 6, and 7, in both
repository-tested lanes defined by the
[compatibility authority](../../ansible_collections/tomazb/acm_switchover/docs/compatibility.md)
(`ansible-core` 2.16 on Python 3.11, and 2.21 on Python 3.12), including the resolved-dependency
compatibility step that precedes surface 3.

- [ ] **Step 4: Quality and security gates**

```bash
black --check --line-length 120 --diff acm_switchover.py lib modules \
  ansible_collections/tomazb/acm_switchover/plugins \
  ansible_collections/tomazb/acm_switchover/tests tests
isort --check-only --profile black --line-length 120 acm_switchover.py lib modules \
  ansible_collections/tomazb/acm_switchover/plugins \
  ansible_collections/tomazb/acm_switchover/tests tests
flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics
mypy --explicit-package-bases acm_switchover.py lib/ modules/ \
  ansible_collections/tomazb/acm_switchover/plugins \
  ansible_collections/tomazb/acm_switchover/tests \
  --ignore-missing-imports --no-strict-optional
bandit --ini .bandit -f txt
```

Never substitute `.` for the black and isort path lists.

- [ ] **Step 5: Scope and protected-file check**

```bash
git diff --name-only origin/ansible...HEAD
git diff --name-only origin/ansible...HEAD -- docs/ACM_SWITCHOVER_RUNBOOK.md '.claude/skills/**'
```

The second command must print nothing.

- [ ] **Step 6: Documentation for PR A**

Add a `CHANGELOG.md` `## [Unreleased]` entry under `### Added` naming the shared strict read
contract, and under `### Changed` naming the `acm_k8s_read_outcome` reclassification of a positive
discovery miss from `error` to `kind_not_served`. Add the strict-read seam row to
[`docs/ansible-collection/behavior-map.md`](../ansible-collection/behavior-map.md) mapping the
Python strict surface to the collection module, replacing the generic `lib/kube_client.py` row's
target. Update [`docs/development/architecture.md`](../development/architecture.md) with the
closed strict-read surface and exact canonical-resource-name flow. Run:

```bash
python -m pytest tests/test_documentation_guardrails.py tests/test_ci_guardrails.py -q
```

- [ ] **Step 7: Simplification gate, then open PR A**

Review every changed file and its immediate collaborators for avoidable complexity introduced by
this change. Record what was simplified, or that no safe in-scope simplification was identified,
in the builder report and the PR description. Rerun the targeted gates after any simplification.

## 9.6 R4-04 Task 0 Step 2 gate mapping

PR A alone must satisfy this checklist verbatim. Each row names the evidence.

For R4-04, "kind absent" therefore means only a successful, decoded, structurally valid
APIResourceList that lacks the exact canonical `resource_name`. Discovery HTTP 404/403/5xx,
timeout/TLS/transport/decode failure, malformed discovery, `ResourceNotFoundError`, and swallowed
client discovery failure are all `error`; R4-04 may not reinterpret them as an empty inventory.

| R4-04 Task 0 Step 2 requirement | Satisfied by |
| --- | --- |
| Python strict list/read interface | `KubeClient.list_custom_resources_strict`, `get_custom_resource_strict`, `get_namespace_strict`, `list_pods_strict`, `get_deployment_strict`, and `get_replicaset_strict` (Task A3) |
| Test: true empty | `test_true_empty_list_is_a_proven_complete_inventory`; parity vector `true_empty` |
| Test: 404/discovery failure | `test_unserved_kind_short_circuits_to_crd_absent`, `test_discovery_404_is_error_not_absence`, `test_discovery_service_unavailable_is_error_not_absence`, `test_list_404_on_a_served_kind_is_error_not_absence`; vectors `kind_not_served`, `discovery_http_404`, `discovery_unverifiable` |
| Test: malformed `items` | `test_malformed_items_is_error_not_empty`; vector `malformed_items` |
| Test: transport/auth failure | `test_authorization_failure_is_error_not_absence`; vectors `authorization_failure`, `transport_failure` |
| Test: complete pagination | `test_complete_multi_page_inventory_is_joined`, `test_later_page_failure_fails_the_whole_read`, `test_outstanding_continuation_at_exit_is_incomplete`; collection `test_list_mode_follows_continue_tokens_to_exhaustion`, `test_list_mode_page_failure_is_error_and_returns_no_partial_inventory`, `test_list_mode_outstanding_continuation_at_exit_is_error`; vectors `complete_pagination`, `later_page_failure`, `outstanding_continuation` |
| Test: mandatory `limit` and bounded calls on every page | Python `test_every_page_request_carries_the_fixed_page_limit`, `test_every_page_request_is_bounded_and_follows_the_continuation`, `test_complete_multi_page_pod_inventory_is_joined_and_bounded`, `test_named_get_is_bounded`, `test_namespace_get_is_bounded`, `test_apps_get_is_bounded`; collection `test_every_list_page_carries_the_fixed_limit_and_a_bounded_timeout`, `test_discovery_request_is_bounded_and_targets_the_exact_group_version`; parity `test_strict_read_bounds_are_mirrored` |
| Test: expired continuation restarts the whole read once, then fails closed | Python `test_expired_continue_token_restarts_the_whole_read_once`, `test_second_expired_continue_token_fails_closed`, `test_pod_expired_continuation_restarts_the_whole_read_once`, `test_pod_second_expiry_fails_closed`; collection `test_expired_continuation_restarts_the_whole_read_once`, `test_second_expired_continuation_is_error_with_no_partial_output`; vectors `expired_continuation_restart`, `second_expired_continuation` |
| Test: missing/`null`/non-list `items` and malformed members or list metadata are never an empty success | Python `test_missing_items_key_is_error_not_empty`, `test_null_items_is_error_not_empty`, `test_malformed_list_metadata_is_error`, `test_non_mapping_member_is_error_not_empty`, `test_pod_page_without_items_is_error_not_empty`, `test_pod_page_with_non_list_items_is_error`, `test_malformed_pod_member_is_error`, `test_pod_page_without_list_metadata_is_error`; collection `test_malformed_list_pages_are_error_never_empty_success` (six cases, including missing `items` and missing metadata); vector `missing_items_key` |
| Test: canonical resource name is exact, never synthesized | collection `test_irregular_plural_resource_lookup_success_reads_ok`, `test_irregular_plural_matches_the_exact_name_and_never_becomes_absence`, `test_missing_resource_name_is_rejected_before_any_client_work`, `test_resource_name_is_required_and_module_reports_no_namespace_probing_mode`; Python `test_irregular_plural_is_matched_by_exact_resource_name` |
| Collection has the corresponding complete list outcome, extending `acm_k8s_read_outcome` rather than adding another abstraction | Task A4; `test_resource_name_is_required_and_module_reports_no_namespace_probing_mode` proves no mode was added |
| Merged on `origin/ansible` | PR A merges before R4-04 execution begins; R4-04 re-runs its own Task 0 |
| No competing read algebra | Task A4 extends the single module with explicit canonical `resource_name`; Task A5 holds both surfaces to one vector set; Task A3 supplies the positive Python namespace-absence producer and strict core reads needed by later R4-03 consumers |

---

# 10. PR B — durable teardown state, outcome algebra, refusal abort

**Branch:** `feature/r4-03-state-outcomes` · **Worktree:** `.claude/worktrees/r4-03-state-outcomes`

**Purpose.** Introduce the durable teardown schema and the decommission outcome vocabulary in both
form factors, and make an interactive refusal abort the run. This PR adds **no new Kubernetes API
call**, so it carries no RBAC change; that is exactly why it is separated from the teardown
mechanics in PR C. Closes R4-C2 and amendment criteria A3 and A5.

**Prerequisite:** PR A merged.

## 10.1 Decision record for PR B

| Decision | Value | Basis |
| --- | --- | --- |
| Python durable owner | `lib/run_record.py::RunRecord` typed accessors | Amendment §5 fact 1; `tests/test_run_record_guardrails.py` forbids raw config keys elsewhere |
| Python config key | `decommission_teardown_records` (single key, one owner) | Amendment §13 — no other durable key is added |
| Collection durable owner | `plugins/module_utils/checkpoint.py` `KEY_DECOMMISSION_TEARDOWN_RECORDS` | Mirrors the merged named-operation vocabulary; `test_checkpoint_vocabulary_guardrail.py` forbids raw keys in roles |
| Record key format | `"{apiVersion}/{kind}/{namespace}/{name}"`, empty namespace segment for cluster-scoped | July §1 "keyed by API version/kind/namespace/name" |
| Outcome vocabulary owner | `lib/constants.py` and `module_utils/constants.py`, mirrored | Amendment §12 — stable reason codes are mirrored constants |
| `decommission()` return | `DecommissionResult` value object, not `bool` | July §2 requires an accurate summary plus a non-zero result; a bool cannot carry it |
| Caller mapping | CLI maps to exit status; `Finalization` maps to `SwitchoverError` with its own message context | Amendment §11 item 2 — caller-distinct failure text is caller-supplied context |
| Substep enablement | `not_requested` when configuration disables the substep | Amendment §7 table |
| Refusal persistence | never persisted | Amendment §13 "deliberately not persisted" |

## 10.2 Durable schema

Exactly three durable field families, all under the one Python key and the one collection key.

```yaml
# Python: RunRecord key "decommission_teardown_records"
# Collection: checkpoint operational_data key "decommission_teardown_records"

# MCO record, completed with a successful Pod-drain proof
"observability.open-cluster-management.io/v1beta2/MultiClusterObservability//observability":
  expected_uid: "8f0a...-uid"          # immutable once written; never rebound
  phase: "completed"                   # delete_started|cr_absent|drain_pending|drained|completed|recovery_required
  observed_at: "2026-09-04T10:11:12Z"  # required only at phase == completed
  resource_versions:                   # required only at phase == completed; see the proof-key rule
    cr: "88214"
    pods: "88219"

# MCO record, completed where positive namespace absence entailed the pod-empty predicate
"observability.open-cluster-management.io/v1beta2/MultiClusterObservability//observability":
  expected_uid: "8f0a...-uid"
  phase: "completed"
  observed_at: "2026-09-04T10:11:12Z"
  resource_versions:
    cr: "88214"
    namespace_absent: "open-cluster-management-observability"

# MCH record, mid-teardown, carrying the captured operator identity
"operator.open-cluster-management.io/v1/MultiClusterHub/open-cluster-management/multiclusterhub":
  expected_uid: "2b71...-uid"
  phase: "delete_started"
  resource_versions:
    cr: "77310"
  operator_deployment:                 # exactly one of operator_deployment / operator_identity_unavailable
    namespace: "open-cluster-management"
    name: "multiclusterhub-operator"
    uid: "aa10...-uid"
    discovery_method: "olm_csv_owned_mch_crd_install_deployment_v1"
    captured_at: "2026-09-04T10:09:00Z"
    csv:
      namespace: "open-cluster-management"
      name: "advanced-cluster-management.v2.13.0"
      uid: "cc42...-uid"
      owned_crd: "multiclusterhubs.operator.open-cluster-management.io"
    mch_teardown_key: "operator.open-cluster-management.io/v1/MultiClusterHub/open-cluster-management/multiclusterhub"
    mch_expected_uid: "2b71...-uid"
```

### 10.2.1 Completion proof keys — the exact `resource_versions` contract

`resource_versions` is a mapping from a **proof key** to a **non-empty string**. Each key names one
predicate the record proves, and its value is the strongest identifier the read that proved it can
carry. This is the explicit omission rule for proofs that carry no server revision; nothing is left
to the implementer.

| Proof key | Predicate proven | Value | Source read |
| --- | --- | --- | --- |
| `cr` | the last observed revision of the object this record deleted | `metadata.resourceVersion` | the strict named GET that bound `expected_uid`; written in the **same** forced-durable write as `delete_started` and carried forward unchanged at every later transition |
| `pods` | the drain scope held zero matching Pods | the list response's `metadata.resourceVersion` | the successful `list_pods_strict(...)` whose final page proved the drain empty |
| `namespace_absent` | the drain namespace is **positively absent**, which entails the pod-empty predicate (July §3 fixed-namespace scope rule) | the exact namespace name proven absent | the fresh `get_namespace_strict(namespace)` that returned `NAMESPACE_ABSENT` |

**Why two of the three proofs are not revisions.** A named-GET 404 carries no `resourceVersion`, and
the approved design proves both CR absence and namespace absence with named GETs (amendment §8
"final live GET before success"; §6.2 item 3). This plan does **not** substitute a LIST to
manufacture a revision: that would change the recorded reads and the §14 verb rows. The `cr` key
therefore records the revision of the object as last observed **before** its deletion — the value
`StrictReadOutcome.resource_version` already carries for that read (§9.1) — and the namespace proof
records the proven-absent identity. `pods` is a genuine list revision because the drain proof, when
it happens, is a successful LIST.

**Required key sets at `phase == completed`**, by resource family and proof mode:

| Family | Drain proof mode | Required keys | Forbidden keys |
| --- | --- | --- | --- |
| MCO, MCH | drain proven by a successful Pod LIST | `cr`, `pods` | `namespace_absent` |
| MCO, MCH | pod-empty predicate entailed by positive namespace absence | `cr`, `namespace_absent` | `pods` |
| ManagedCluster (no drain scope) | n/a | `cr` | `pods`, `namespace_absent` |

Validation rules for the mapping, enforced identically on both sides:

- any key outside `{cr, pods, namespace_absent}` → malformed;
- any non-string or empty value → malformed;
- `cr` missing at `completed` → malformed;
- for a family with a drain scope, neither or both of `pods` / `namespace_absent` → malformed;
- for a family without a drain scope, either of `pods` / `namespace_absent` present → malformed;
- `cr` changed by a later write → malformed, exactly like `expected_uid`.

A `completed` record is therefore never accepted merely because `resource_versions` is non-empty:
the key set must match the family and the recorded proof mode exactly.

### 10.2.2 `operator_deployment` — exact nested schema

Written by PR E; **validated by PR B**, so no later PR can write a shape the reader does not check.

| Field | Type | Required | Constraint |
| --- | --- | --- | --- |
| `namespace` | string | yes | non-empty; the exact ACM namespace the capture read |
| `name` | string | yes | non-empty |
| `uid` | string | yes | non-empty; the live Deployment UID at capture |
| `discovery_method` | string | yes | exactly `olm_csv_owned_mch_crd_install_deployment_v1` |
| `captured_at` | string | yes | non-empty RFC 3339 UTC timestamp |
| `csv` | mapping | yes | present and a mapping |
| `csv.namespace` | string | yes | non-empty; equal to `operator_deployment.namespace` |
| `csv.name` | string | yes | non-empty |
| `csv.uid` | string | yes | non-empty |
| `csv.owned_crd` | string | yes | exactly `multiclusterhubs.operator.open-cluster-management.io` |
| `mch_teardown_key` | string | yes | equal to the enclosing record's key |
| `mch_expected_uid` | string | yes | equal to the enclosing record's `expected_uid` |

Any other key, any wrong type, any empty value, and any violated equality is malformed → fail closed.

### 10.2.3 `operator_identity_unavailable` — exact nested schema

| Field | Type | Required | Constraint |
| --- | --- | --- | --- |
| `reason` | string | yes | one of `OPERATOR_IDENTITY_UNAVAILABLE_REASONS` |
| `discovery_method` | string | yes | exactly `olm_csv_owned_mch_crd_install_deployment_v1` |
| `captured_at` | string | yes | non-empty RFC 3339 UTC timestamp |
| `evidence_summary` | string | yes | non-empty, sanitized; no bodies, headers, tokens, or client configuration |
| `mch_teardown_key` | string | yes | equal to the enclosing record's key |
| `mch_expected_uid` | string | yes | equal to the enclosing record's `expected_uid` |

`OPERATOR_IDENTITY_UNAVAILABLE_REASONS` is a mirrored constant tuple added by **PR B** (which owns
the validation) to `lib/constants.py` and `module_utils/constants.py`, and to `CONSTANT_PAIRS`:

```python
OPERATOR_IDENTITY_UNAVAILABLE_REASONS = (
    "csv_absent",
    "csv_ambiguous",
    "csv_not_succeeded",
    "csv_owned_crd_mismatch",
    "install_deployment_absent",
    "install_deployment_ambiguous",
    "deployment_read_failed",
    "deployment_identity_incomplete",
)
```

A `reason` outside that tuple is malformed → fail closed. PR E writes only these values.

### 10.2.4 Record-level validation rules

Enforced by `RunRecord` on read and write, and by `checkpoint.py` on read and write:

| Condition | Result |
| --- | --- |
| Missing or empty key component, missing or empty `expected_uid`, or unknown `phase` | malformed — fail closed before any mutation or clean-skip decision |
| `phase == completed` without `observed_at`, or with a `resource_versions` mapping that violates §10.2.1 | malformed — fail closed |
| An MCH-family record carrying both `operator_deployment` and `operator_identity_unavailable`, or neither | malformed — fail closed. Every MCH record is born after identity capture, so "exactly one" holds for every phase |
| A non-MCH-family record carrying either identity field | malformed — fail closed |
| A nested identity shape violating §10.2.2 or §10.2.3 | malformed — fail closed |
| `mch_teardown_key` or `mch_expected_uid` not equal to the enclosing record | malformed — fail closed |
| A later write that changes `expected_uid` or `resource_versions.cr` | malformed — fail closed; the first value stands |
| A rerun observing a different live UID for a recorded name | fatal before DELETE; the replacement is left intact |

`operator_deployment` and `operator_identity_unavailable` are **written by PR E**, which owns MCH
identity. PR B introduces their validation so no later PR can write a shape the reader does not
check, and Task B1/B2 test every malformed nested case listed above on both sides.

## Task B1: Python durable teardown record API

**Files:** Create `lib/teardown_record.py`; modify `lib/run_record.py`; modify `lib/constants.py`;
create `tests/test_teardown_record.py`; modify `tests/test_run_record.py`.

**Purpose:** Own the record value type, its validation, and the typed durable accessors.

**Interfaces produced:**

```python
# lib/teardown_record.py
class TeardownPhase(Enum):
    DELETE_STARTED = "delete_started"
    CR_ABSENT = "cr_absent"
    DRAIN_PENDING = "drain_pending"
    DRAINED = "drained"
    COMPLETED = "completed"
    RECOVERY_REQUIRED = "recovery_required"

# Kinds whose teardown has a drain scope, and kinds that carry operator identity.
DRAIN_SCOPED_KINDS = frozenset({"MultiClusterObservability", "MultiClusterHub"})
IDENTITY_BEARING_KINDS = frozenset({"MultiClusterHub"})

def teardown_key(api_version: str, kind: str, namespace: str | None, name: str) -> str: ...
def teardown_kind(key: str) -> str: ...          # the kind segment of a record key

@dataclass(frozen=True)
class TeardownRecord:
    key: str
    expected_uid: str
    phase: TeardownPhase
    observed_at: str | None = None
    resource_versions: dict[str, str] = field(default_factory=dict)
    operator_deployment: dict | None = None
    operator_identity_unavailable: dict | None = None

class MalformedTeardownRecord(FatalError): ...

# lib/run_record.py additions
def record_teardown_phase(self, record: TeardownRecord) -> None: ...
def teardown_record(self, key: str) -> TeardownRecord | None: ...
def all_teardown_records(self) -> dict[str, TeardownRecord]: ...
```

**Intended behavior.** `record_teardown_phase` validates the record against §10.2, writes through
`_set`, and then forces the state file to disk before returning, so "forced durable before DELETE"
is a property of the API rather than of each call site. `teardown_record` validates on read and
raises `MalformedTeardownRecord` rather than returning a degraded value: a teardown record is
mutation authority, so the tolerant-degradation model used by `RunSummary.from_snapshot` for
reporting facts is wrong here. Validation of the proof keys (§10.2.1), the nested identity shapes
(§10.2.2, §10.2.3), and the record-level rules (§10.2.4) lives in exactly one function used by both
the reader and the writer.

**Failure behavior.** A failed durable write propagates; the caller must not proceed to DELETE.
**Dry-run implications.** `RunRecord` performs no dry-run branching; PR C's callers do not call
these writers in dry-run (Task C3/C4). **Parity:** mirrored by Task B2.
**RBAC:** none.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_teardown_record.py`. The repository has **no** `tests/conftest.py`, and the
`state_manager` fixture in `tests/test_utils.py:42` is module-local, so this file defines its own
fixtures and helper in the style `tests/test_run_record.py` already uses:

```python
"""Durable teardown records (R4-03 PR B)."""

import pytest

from lib.constants import OPERATOR_IDENTITY_UNAVAILABLE_REASONS
from lib.exceptions import FatalError
from lib.run_record import RunRecord
from lib.teardown_record import (
    MalformedTeardownRecord,
    TeardownPhase,
    TeardownRecord,
    teardown_key,
)
from lib.utils import StateManager

MCO_KEY = teardown_key("observability.open-cluster-management.io/v1beta2",
                       "MultiClusterObservability", None, "observability")
MCH_KEY = teardown_key("operator.open-cluster-management.io/v1",
                       "MultiClusterHub", "open-cluster-management", "multiclusterhub")
CLUSTER_KEY = teardown_key("cluster.open-cluster-management.io/v1",
                           "ManagedCluster", None, "spoke-1")


@pytest.fixture
def state_manager(tmp_path):
    return StateManager(str(tmp_path / "switchover-test.json"))


def _raise(exc):
    """Return a zero-argument callable that raises `exc` (monkeypatch stand-in)."""

    def _raiser(*args, **kwargs):
        raise exc

    return _raiser


def _identity(**overrides):
    identity = {
        "namespace": "open-cluster-management",
        "name": "multiclusterhub-operator",
        "uid": "dep-uid-1",
        "discovery_method": "olm_csv_owned_mch_crd_install_deployment_v1",
        "captured_at": "2026-09-04T10:09:00Z",
        "csv": {
            "namespace": "open-cluster-management",
            "name": "advanced-cluster-management.v2.13.0",
            "uid": "csv-uid-1",
            "owned_crd": "multiclusterhubs.operator.open-cluster-management.io",
        },
        "mch_teardown_key": MCH_KEY,
        "mch_expected_uid": "uid-mch",
    }
    identity.update(overrides)
    return identity


def _mch_record(**overrides):
    fields = {
        "key": MCH_KEY,
        "expected_uid": "uid-mch",
        "phase": TeardownPhase.DELETE_STARTED,
        "resource_versions": {"cr": "77310"},
        "operator_deployment": _identity(),
    }
    fields.update(overrides)
    return TeardownRecord(**fields)


def test_key_is_stable_and_includes_an_empty_namespace_segment_when_cluster_scoped():
    assert MCO_KEY == (
        "observability.open-cluster-management.io/v1beta2/MultiClusterObservability//observability"
    )


def test_record_round_trips_through_the_facade(state_manager):
    record = RunRecord(state_manager)
    record.record_teardown_phase(
        TeardownRecord(key=MCO_KEY, expected_uid="uid-1", phase=TeardownPhase.DELETE_STARTED,
                       resource_versions={"cr": "88214"})
    )
    loaded = record.teardown_record(MCO_KEY)
    assert loaded.expected_uid == "uid-1"
    assert loaded.phase is TeardownPhase.DELETE_STARTED
    assert loaded.resource_versions == {"cr": "88214"}


def test_absent_record_reads_as_none(state_manager):
    assert RunRecord(state_manager).teardown_record(MCO_KEY) is None


def test_phase_write_is_forced_durable_before_returning(state_manager, monkeypatch):
    flushed = []
    monkeypatch.setattr(state_manager, "flush_state", lambda: flushed.append(True))
    RunRecord(state_manager).record_teardown_phase(
        TeardownRecord(key=MCO_KEY, expected_uid="uid-1", phase=TeardownPhase.DELETE_STARTED,
                       resource_versions={"cr": "88214"})
    )
    assert flushed, "teardown phase must be forced durable, not left to lazy save"


def test_a_failed_durable_write_propagates(state_manager, monkeypatch):
    monkeypatch.setattr(state_manager, "flush_state", _raise(OSError("disk full")))
    with pytest.raises(OSError):
        RunRecord(state_manager).record_teardown_phase(
            TeardownRecord(key=MCO_KEY, expected_uid="uid-1", phase=TeardownPhase.DELETE_STARTED,
                           resource_versions={"cr": "88214"})
        )


@pytest.mark.parametrize(
    "stored",
    [
        {"phase": "delete_started"},                       # missing expected_uid
        {"expected_uid": "", "phase": "delete_started"},   # empty expected_uid
        {"expected_uid": "u", "phase": "banana"},          # unknown phase
        {"expected_uid": "u"},                             # missing phase
        {"expected_uid": "u", "phase": "completed"},       # completed without proof metadata
        {"expected_uid": "u", "phase": "completed", "observed_at": "2026-09-04T00:00:00Z"},
        "not-a-mapping",
    ],
)
def test_malformed_records_fail_closed(state_manager, stored):
    state_manager._set_config("decommission_teardown_records", {MCO_KEY: stored})
    with pytest.raises(MalformedTeardownRecord):
        RunRecord(state_manager).teardown_record(MCO_KEY)


def test_malformed_teardown_record_is_fatal():
    assert issubclass(MalformedTeardownRecord, FatalError)


# --- §10.2.1 completion proof keys ------------------------------------------------
@pytest.mark.parametrize(
    "resource_versions",
    [
        {},                                                  # empty
        {"pods": "9"},                                       # missing cr
        {"cr": "9"},                                         # drain-scoped kind with no drain proof
        {"cr": "9", "pods": "10", "namespace_absent": "ns"},  # both drain proofs
        {"cr": "9", "pods": ""},                             # empty value
        {"cr": "9", "pods": "10", "extra": "x"},             # unknown key
        {"cr": "9", "pods": 10},                             # non-string value
    ],
)
def test_completed_mco_record_requires_an_exact_proof_key_set(state_manager, resource_versions):
    with pytest.raises(MalformedTeardownRecord):
        RunRecord(state_manager).record_teardown_phase(
            TeardownRecord(key=MCO_KEY, expected_uid="u", phase=TeardownPhase.COMPLETED,
                           observed_at="2026-09-04T00:00:00Z",
                           resource_versions=resource_versions)
        )


@pytest.mark.parametrize(
    "resource_versions",
    [
        {"cr": "88214", "pods": "88219"},
        {"cr": "88214", "namespace_absent": "open-cluster-management-observability"},
    ],
)
def test_completed_mco_record_accepts_either_approved_drain_proof(state_manager, resource_versions):
    record = RunRecord(state_manager)
    record.record_teardown_phase(
        TeardownRecord(key=MCO_KEY, expected_uid="u", phase=TeardownPhase.COMPLETED,
                       observed_at="2026-09-04T00:00:00Z", resource_versions=resource_versions)
    )
    assert record.teardown_record(MCO_KEY).resource_versions == resource_versions


def test_completed_managed_cluster_record_carries_no_drain_proof(state_manager):
    record = RunRecord(state_manager)
    record.record_teardown_phase(
        TeardownRecord(key=CLUSTER_KEY, expected_uid="u", phase=TeardownPhase.COMPLETED,
                       observed_at="2026-09-04T00:00:00Z", resource_versions={"cr": "5"})
    )
    assert record.teardown_record(CLUSTER_KEY).phase is TeardownPhase.COMPLETED
    with pytest.raises(MalformedTeardownRecord):
        record.record_teardown_phase(
            TeardownRecord(key=CLUSTER_KEY, expected_uid="u", phase=TeardownPhase.COMPLETED,
                           observed_at="2026-09-04T00:00:00Z",
                           resource_versions={"cr": "5", "pods": "6"})
        )


def test_expected_uid_is_never_rebound_by_a_later_write(state_manager):
    record = RunRecord(state_manager)
    record.record_teardown_phase(
        TeardownRecord(key=MCO_KEY, expected_uid="uid-1", phase=TeardownPhase.DELETE_STARTED,
                       resource_versions={"cr": "88214"})
    )
    with pytest.raises(MalformedTeardownRecord):
        record.record_teardown_phase(
            TeardownRecord(key=MCO_KEY, expected_uid="uid-2", phase=TeardownPhase.CR_ABSENT,
                           resource_versions={"cr": "88214"})
        )
    assert record.teardown_record(MCO_KEY).expected_uid == "uid-1"


def test_cr_proof_revision_is_never_rebound_by_a_later_write(state_manager):
    record = RunRecord(state_manager)
    record.record_teardown_phase(
        TeardownRecord(key=MCO_KEY, expected_uid="uid-1", phase=TeardownPhase.DELETE_STARTED,
                       resource_versions={"cr": "88214"})
    )
    with pytest.raises(MalformedTeardownRecord):
        record.record_teardown_phase(
            TeardownRecord(key=MCO_KEY, expected_uid="uid-1", phase=TeardownPhase.CR_ABSENT,
                           resource_versions={"cr": "99999"})
        )


# --- §10.2.2 / §10.2.3 nested identity -------------------------------------------
def test_mch_identity_outcome_must_be_exactly_one(state_manager):
    record = RunRecord(state_manager)
    with pytest.raises(MalformedTeardownRecord):
        record.record_teardown_phase(
            _mch_record(operator_identity_unavailable={"reason": "csv_ambiguous"})
        )
    with pytest.raises(MalformedTeardownRecord):
        record.record_teardown_phase(
            _mch_record(operator_deployment=None, operator_identity_unavailable=None)
        )


def test_a_non_mch_record_may_not_carry_operator_identity(state_manager):
    with pytest.raises(MalformedTeardownRecord):
        RunRecord(state_manager).record_teardown_phase(
            TeardownRecord(key=MCO_KEY, expected_uid="u", phase=TeardownPhase.DELETE_STARTED,
                           resource_versions={"cr": "1"}, operator_deployment=_identity())
        )


@pytest.mark.parametrize(
    "identity",
    [
        _identity(namespace=None),
        _identity(namespace=""),
        _identity(name=None),
        _identity(uid=None),
        _identity(uid=""),
        _identity(captured_at=None),
        _identity(discovery_method=None),
        _identity(discovery_method="guessed_by_name_prefix"),
        _identity(csv=None),
        _identity(csv={"namespace": "open-cluster-management", "uid": "csv-uid-1",
                       "owned_crd": "multiclusterhubs.operator.open-cluster-management.io"}),
        _identity(csv={"namespace": "open-cluster-management", "name": "acm.v2.13.0",
                       "owned_crd": "multiclusterhubs.operator.open-cluster-management.io"}),
        _identity(csv={"namespace": "elsewhere", "name": "acm.v2.13.0", "uid": "csv-uid-1",
                       "owned_crd": "multiclusterhubs.operator.open-cluster-management.io"}),
        _identity(csv={"namespace": "open-cluster-management", "name": "acm.v2.13.0",
                       "uid": "csv-uid-1", "owned_crd": "somethingelse.example.com"}),
        _identity(mch_teardown_key="operator.open-cluster-management.io/v1/MultiClusterHub//other"),
        _identity(mch_expected_uid="uid-other"),
        _identity(uid=7),
        {"uid": "dep-uid-1"},                                   # partial shape
    ],
)
def test_malformed_operator_deployment_fails_closed(state_manager, identity):
    with pytest.raises(MalformedTeardownRecord):
        RunRecord(state_manager).record_teardown_phase(_mch_record(operator_deployment=identity))


@pytest.mark.parametrize(
    "unavailable",
    [
        {"reason": "not_a_known_reason", "discovery_method": "olm_csv_owned_mch_crd_install_deployment_v1",
         "captured_at": "2026-09-04T10:09:00Z", "evidence_summary": "no candidate CSV",
         "mch_teardown_key": MCH_KEY, "mch_expected_uid": "uid-mch"},
        {"reason": "csv_absent", "captured_at": "2026-09-04T10:09:00Z",
         "evidence_summary": "no candidate CSV", "mch_teardown_key": MCH_KEY,
         "mch_expected_uid": "uid-mch"},                                   # missing discovery_method
        {"reason": "csv_absent", "discovery_method": "olm_csv_owned_mch_crd_install_deployment_v1",
         "evidence_summary": "no candidate CSV", "mch_teardown_key": MCH_KEY,
         "mch_expected_uid": "uid-mch"},                                   # missing captured_at
        {"reason": "csv_absent", "discovery_method": "olm_csv_owned_mch_crd_install_deployment_v1",
         "captured_at": "2026-09-04T10:09:00Z", "mch_teardown_key": MCH_KEY,
         "mch_expected_uid": "uid-mch"},                                   # missing evidence_summary
        {"reason": "csv_absent", "discovery_method": "olm_csv_owned_mch_crd_install_deployment_v1",
         "captured_at": "2026-09-04T10:09:00Z", "evidence_summary": "x",
         "mch_expected_uid": "uid-mch"},                                   # missing teardown key
        {"reason": "csv_absent", "discovery_method": "olm_csv_owned_mch_crd_install_deployment_v1",
         "captured_at": "2026-09-04T10:09:00Z", "evidence_summary": "x",
         "mch_teardown_key": MCH_KEY, "mch_expected_uid": "uid-other"},    # mismatched UID
    ],
)
def test_malformed_operator_identity_unavailable_fails_closed(state_manager, unavailable):
    with pytest.raises(MalformedTeardownRecord):
        RunRecord(state_manager).record_teardown_phase(
            _mch_record(operator_deployment=None, operator_identity_unavailable=unavailable)
        )


@pytest.mark.parametrize("reason", OPERATOR_IDENTITY_UNAVAILABLE_REASONS)
def test_every_enumerated_unavailable_reason_is_accepted(state_manager, reason):
    record = RunRecord(state_manager)
    record.record_teardown_phase(
        _mch_record(
            operator_deployment=None,
            operator_identity_unavailable={
                "reason": reason,
                "discovery_method": "olm_csv_owned_mch_crd_install_deployment_v1",
                "captured_at": "2026-09-04T10:09:00Z",
                "evidence_summary": "sanitized summary",
                "mch_teardown_key": MCH_KEY,
                "mch_expected_uid": "uid-mch",
            },
        )
    )
    assert record.teardown_record(MCH_KEY).operator_identity_unavailable["reason"] == reason


def test_malformed_nested_identity_is_rejected_on_reload_too(state_manager):
    """The reader validates independently of the writer: a hand-edited state file fails closed."""
    state_manager._set_config(
        "decommission_teardown_records",
        {MCH_KEY: {"expected_uid": "uid-mch", "phase": "delete_started",
                   "resource_versions": {"cr": "77310"},
                   "operator_deployment": {"uid": "dep-uid-1"}}},
    )
    with pytest.raises(MalformedTeardownRecord):
        RunRecord(state_manager).teardown_record(MCH_KEY)


def test_all_teardown_records_fails_closed_on_any_malformed_member(state_manager):
    state_manager._set_config(
        "decommission_teardown_records",
        {MCO_KEY: {"expected_uid": "u", "phase": "delete_started",
                   "resource_versions": {"cr": "1"}},
         MCH_KEY: {"expected_uid": "u", "phase": "banana"}},
    )
    with pytest.raises(MalformedTeardownRecord):
        RunRecord(state_manager).all_teardown_records()
```

- [ ] **Step 2: Run and observe the expected failure**

```bash
python -m pytest tests/test_teardown_record.py -q
```

Expected: collection error — `ModuleNotFoundError: No module named 'lib.teardown_record'`, and
`ImportError` for `OPERATOR_IDENTITY_UNAVAILABLE_REASONS`.

- [ ] **Step 3: Implement `lib/teardown_record.py` and the three `RunRecord` accessors**

Add `OPERATOR_IDENTITY_UNAVAILABLE_REASONS` (§10.2.3) and the
`olm_csv_owned_mch_crd_install_deployment_v1` discovery-method constant to `lib/constants.py`, and
implement `lib/teardown_record.py` with `TeardownPhase`, `DRAIN_SCOPED_KINDS`,
`IDENTITY_BEARING_KINDS`, `teardown_key`, `teardown_kind`, `TeardownRecord`,
`MalformedTeardownRecord`, and one `validate(record, previous=None)` function implementing §10.2.1
through §10.2.4. `previous` carries the currently stored record so the immutability rules for
`expected_uid` and `resource_versions["cr"]` are checked in the same place as everything else.

Keep every raw-key touch inside `lib/run_record.py`; `lib/teardown_record.py` holds only the value
type, the key helpers, and validation. This is what keeps
`tests/test_run_record_guardrails.py::test_raw_config_keys_only_read_by_allowed_modules` passing
without widening its allow-list.

- [ ] **Step 4: Run and observe the tests pass**

```bash
python -m pytest tests/test_teardown_record.py tests/test_run_record.py \
  tests/test_run_record_guardrails.py -q
```

- [ ] **Step 5: Refactor**

Validation lives in exactly one function. If `record_teardown_phase` and `teardown_record` both
validate, they both call `validate(...)`; there is no second copy of any rule, and no rule is
expressed only in a docstring.

- [ ] **Step 6: Commit**

```bash
git add lib/teardown_record.py lib/run_record.py lib/constants.py tests/test_teardown_record.py
git commit -m "feat: add durable decommission teardown records"
```

## Task B2: Collection checkpoint teardown vocabulary

**Files:** Modify `plugins/module_utils/checkpoint.py`; modify
`plugins/module_utils/constants.py`; modify `tests/unit/test_checkpoint_vocabulary_guardrail.py`;
create `tests/unit/test_teardown_records.py`; modify `tests/test_checkpoint_state_parity.py`.

**Purpose:** Mirror Task B1 in the collection's own store, with the same schema and the same
fail-closed validation, sharing no code.

**Interfaces produced:**

```python
KEY_DECOMMISSION_TEARDOWN_RECORDS = "decommission_teardown_records"

def teardown_key(api_version, kind, namespace, name) -> str: ...
def teardown_record(checkpoint, key) -> dict | None: ...   # raises on malformed
def record_teardown_phase(checkpoint, key, expected_uid, phase, **proof) -> None: ...
def teardown_records(checkpoint) -> dict: ...
```

**Intended behavior.** Identical algebra and identical malformed-record rules to Task B1 —
§10.2.1 proof keys, §10.2.2 and §10.2.3 nested identity schemas, and §10.2.4 record rules — with
validation in exactly one collection-side function used by both the reader and the writer. The
mirrored constants `OPERATOR_IDENTITY_UNAVAILABLE_REASONS`, `DRAIN_SCOPED_KINDS`, and
`IDENTITY_BEARING_KINDS` are added to `module_utils/constants.py` and to `CONSTANT_PAIRS` in this
task. Roles never touch the raw key; they call these functions through the checkpoint action
plugin's flattened facts, exactly as the merged vocabulary requires.

**Reset behavior.** A full `checkpoint.reset` rebuilds `operational_data` empty and therefore
destroys these records. `reset_from` prunes completed phases while **retaining**
`operational_data`; it must therefore retain and revalidate these records rather than launder
them. Task B4 tests both.

**Check-mode implications.** PR B itself guards every checkpoint enter/exit and teardown-record
action with `when: not ansible_check_mode`; the action plugin's existing native short-circuit is
defense in depth. PR B unit/scenario tests prove no `operational_data` transition and
`changed: false` before B may merge. PR F only repeats this in an integrated scenario.

- [ ] **Step 1: Write the failing tests**

Create `ansible_collections/tomazb/acm_switchover/tests/unit/test_teardown_records.py` mirroring
**every** case in `tests/test_teardown_record.py`, expressed against the checkpoint functions, with
the same fixture data and the same expected outcome:

1. round-trip and absent-reads-as-none;
2. each malformed record shape from §10.2.4 fails closed;
3. the §10.2.1 proof-key matrix — `completed` requires `observed_at` and an exact proof-key set;
   both approved MCO drain proofs are accepted; a ManagedCluster record carrying a drain proof is
   malformed; `resource_versions["cr"]` is never rebound;
4. the §10.2.2 malformed `operator_deployment` parametrization, member for member;
5. the §10.2.3 malformed `operator_identity_unavailable` parametrization, member for member, plus
   acceptance of every enumerated reason;
6. exactly one MCH identity outcome; a non-MCH record carrying either identity field is malformed;
7. reload validation: a hand-written `operational_data` payload with a partial nested identity is
   rejected on read, not only on write.

Add to `tests/test_checkpoint_state_parity.py`:

- an assertion that the Python config key and the collection `operational_data` key are the same
  string, and that the two `teardown_key` builders produce identical keys for the same inputs;
- a **shared malformed-fixture parity test**: one declared list of malformed record payloads
  (the union of items 2 through 6 above, as plain JSON-compatible dictionaries) asserted to be
  rejected by **both** `lib/teardown_record.validate` and the collection `checkpoint` reader. A
  payload that only one side rejects fails this test, so mutation authority can never be tolerated
  on one side and refused on the other. The collection module is imported lazily inside the test
  body, keeping root `tests/` import-safe without `ansible-core`.

- [ ] **Step 2: Run and observe the expected failure**

```bash
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_teardown_records.py -q
python -m pytest tests/test_checkpoint_state_parity.py -q
```

- [ ] **Step 3: Implement, then rerun both**

- [ ] **Step 4: Keep the vocabulary guardrail honest**

Add the new key to the guardrail's expected vocabulary so a role that reads
`operational_data['decommission_teardown_records']` directly fails:

```bash
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_checkpoint_vocabulary_guardrail.py -q
```

- [ ] **Step 5: Commit**

```bash
git add ansible_collections/tomazb/acm_switchover/plugins/module_utils/checkpoint.py \
  ansible_collections/tomazb/acm_switchover/plugins/module_utils/constants.py \
  ansible_collections/tomazb/acm_switchover/tests/unit/ tests/test_checkpoint_state_parity.py
git commit -m "feat: mirror teardown record vocabulary in the collection checkpoint"
```

## Task B3: Decommission outcome algebra and refusal abort

**Files:** Modify `lib/constants.py`; create `lib/decommission_outcome.py`; modify
`modules/decommission.py`; modify `modules/finalization.py`; modify `acm_switchover.py`;
modify `tests/test_decommission.py`; modify `tests/test_finalization.py`; modify
`tests/test_main.py`.

**Purpose:** Replace the four states currently collapsed into `return True` with the amendment §7
table, make refusal abort the run, and establish the **one** substep execution-result interface that
PRs C, D, and E reuse unchanged. Closes R4-C2 and criterion A5 on the Python side.

**Interfaces produced:**

```python
# lib/decommission_outcome.py
class SubstepOutcome(Enum):
    NOT_REQUESTED = "not_requested"
    PRECONDITION_NOOP = "precondition_noop"
    COMPLETED = "completed"
    REFUSED = "refused"
    FAILED = "failed"

@dataclass(frozen=True)
class SubstepExecution:
    """The result of executing one substep in this invocation."""
    outcome: SubstepOutcome
    changed: bool = False

@dataclass(frozen=True)
class DecommissionResult:
    substeps: dict[str, SubstepOutcome]   # "observability" | "managed_clusters" | "multiclusterhub"
    not_attempted: tuple[str, ...]
    cancelled: bool = False              # top-level banner cancellation only
    changed: bool = False                # actual live mutation, never prediction
    would_change: bool = False            # fresh read-only prediction, never authority
    @property
    def succeeded(self) -> bool: ...      # not cancelled and no REFUSED/FAILED
    def summary_lines(self) -> list[str]: ...
```

### B3.1 The one execution-result interface

`SubstepExecution` is the single value every substep executor returns, in **every** PR of this
slice. There is no tuple form, no side channel, and no second shape:

| Producer | Signature | PR |
| --- | --- | --- |
| substep dispatch | `Decommission._run_substep(self, substep: str) -> SubstepExecution` | B |
| MCO teardown | `Decommission.teardown_observability(self, *, record_gitops_markers: bool = False) -> SubstepExecution` | C |
| shared phase machine | `Decommission._teardown_resource(self, spec: TeardownSpec, *, record_gitops_markers: bool) -> SubstepExecution` | C |
| ManagedCluster teardown | `Decommission.teardown_managed_clusters(self) -> SubstepExecution` | D |
| MCH teardown | `Decommission.teardown_multiclusterhub(self) -> SubstepExecution` | E |

`_run_substep` dispatches to the family method for the named substep and returns its
`SubstepExecution` unchanged. `Decommission.decommission` aggregates:

- `outcomes[substep] = execution.outcome`;
- `changed = changed or execution.changed`, monotonically — once true in an invocation it stays
  true, including on the paths that return early for refusal or failure.

Callers never see `SubstepExecution`: `decommission()` returns `DecommissionResult`, and
`Finalization` consumes `teardown_observability(...).outcome` plus `.changed` for its own logging,
mapping a non-`COMPLETED`/`PRECONDITION_NOOP` outcome to its existing `SwitchoverError` context.

### B3.2 What `changed` means, exactly

`changed` is **actual accepted mutation performed during this invocation**. It is never requested
work, predicted work, a resumed obligation, a precondition noop, an already-absent resource, check
mode, or dry run.

| Case | `outcome` | `changed` |
| --- | --- | --- |
| Substep disabled by configuration | `not_requested` | `false` |
| Requested resource already positively absent, no teardown record | `precondition_noop` | `false` |
| Resumed record, no new DELETE issued during this invocation (for example the record is already `drained` and only the final proof runs) | the phase machine's outcome, `completed` when the proof succeeds | `false` |
| UID-bound DELETE accepted in this invocation and the completion proof succeeds | `completed` | `true` |
| UID-bound DELETE accepted in this invocation, later proof fails | `failed` | `true` — the mutation happened |
| Check mode would delete | no authoritative completion outcome is recorded | `false`; prediction goes to `would_change` |
| Dry run would delete | no authoritative completion outcome is recorded | `false`; prediction goes to `would_change` |
| Refusal, cancellation, or failure | `refused` / `failed` / cancelled result | `false`, unless an earlier substep in this same invocation actually mutated |

At the B stage the three existing private methods are adapted to return `SubstepExecution`, with
`changed` true only when this invocation issued a delete request that the API accepted for an
object the method had just observed present. C, D, and E each replace their family's derivation
together with the phase machine, where "accepted DELETE plus completion proof" makes it exact. The
rule above never loosens.

`.succeeded` is exactly `not cancelled and no substep value is REFUSED or FAILED`. A successful
read-only dry-run may therefore report that the preview command succeeded while `changed` remains
false and all requested substeps remain `not_attempted`; it never claims teardown completion.
`summary_lines()` labels `cancelled`, `not attempted`, actual change, and predicted change
separately. The dataclass defines no `__bool__`; callers must use `.succeeded`. A partially mutated
run therefore reports `changed=True` **and** `.succeeded == False` without contradiction.

**Intended behavior.** `Decommission.decommission(interactive=True)` runs the substeps in the
existing order. Top-level banner cancellation returns `cancelled=True`, empty `substeps`, every
requested substep in `not_attempted`, `changed=False`, `would_change=False`, and
`.succeeded == False`; it invokes no destructive substep and persists no cancellation/refusal
state. A later substep refusal remains distinct: it records `REFUSED`, stops immediately, records
every remaining requested substep in `not_attempted`, carries forward any `changed` already earned,
and returns `.succeeded == False`. A substep disabled by configuration records `NOT_REQUESTED`. A
substep whose preconditions prove no mutation is needed records `PRECONDITION_NOOP`.
Non-interactive and integrated paths never prompt.

**Failure behavior.** `FAILED` on any `SwitchoverError`; remaining substeps are not attempted; any
change already made in this invocation is still reported.

**State implications.** None: refusal is output, not state (amendment §13).
**Dry-run implications.** Before dispatch, Python branches to a read-only prediction path. It
records no substep outcome, teardown record, operator identity, phase, or other result/state
authority; calls no delete primitive; returns `changed=False`; places every requested substep in
`not_attempted`; and sets only `would_change` from fresh strict reads. No later live run consumes
that value. The result carries no separate preview marker: callers already know the requested
execution mode, and the contract is expressed entirely through `changed`, `would_change`, and the
absence of any persisted outcome. At B merge, prediction covers the existing three resource-family
mutations; C/D/E replace each predictor alongside the stricter family algorithm so it remains
accurate at every merge.
**Parity implications.** Mirrored by Task B4. **RBAC:** none.

**Caller mapping**, preserving today's observable difference:

| Caller | Mapping |
| --- | --- |
| `acm_switchover.py::run_decommission` | returns `result.succeeded`; logs `result.summary_lines()`; the existing CLI exit path turns `False` into a non-zero exit |
| `modules/finalization.py::_decommission_old_hub` | raises `SwitchoverError` with its existing message context when `not result.succeeded`, appending the summary |

Both callers compare `.succeeded` explicitly. `if result:` and any other dataclass truthiness are
forbidden and covered by tests. The standalone CLI therefore preserves today's false/non-zero
mapping for top-level cancellation. Finalization's integrated non-interactive call cannot produce
`cancelled=True`.

- [ ] **Step 1: Write the failing tests**

Replace `tests/test_decommission.py:143-159` — it currently asserts `result is True` after
declining every destructive step, and is named for an "extra MCH confirmation" that does not exist
in source. Delete that test and add:

```python
class TestDecommissionOutcomes:
    """R4-C2: a refused substep can never produce a successful decommission."""

    @patch("modules.decommission.confirm_action")
    def test_top_level_cancel_returns_an_unsuccessful_result(self, confirm, decommission_with_obs):
        confirm.return_value = False
        result = decommission_with_obs.decommission(interactive=True)
        assert result.cancelled is True
        assert result.succeeded is False
        assert result.substeps == {}
        assert result.not_attempted == ("observability", "managed_clusters", "multiclusterhub")
        assert result.changed is False and result.would_change is False

    @patch("modules.decommission.confirm_action")
    def test_top_level_cancel_invokes_no_substep(self, confirm, decommission_with_obs, monkeypatch):
        confirm.return_value = False
        invoked = Mock()
        monkeypatch.setattr(decommission_with_obs, "_run_substep", invoked)
        decommission_with_obs.decommission(interactive=True)
        invoked.assert_not_called()

    @patch("modules.decommission.confirm_action")
    def test_refusing_the_first_substep_aborts_and_fails(self, confirm, decommission_with_obs):
        confirm.side_effect = [True, False]  # proceed, then decline observability
        result = decommission_with_obs.decommission(interactive=True)
        assert result.succeeded is False
        assert result.substeps["observability"] is SubstepOutcome.REFUSED
        assert result.not_attempted == ("managed_clusters", "multiclusterhub")
        assert result.changed is False

    @patch("modules.decommission.confirm_action")
    def test_refusal_stops_remaining_substeps(self, confirm, decommission_with_obs, mock_primary_client):
        confirm.side_effect = [True, False]
        decommission_with_obs.decommission(interactive=True)
        mock_primary_client.delete_custom_resource.assert_not_called()

    @patch("modules.decommission.confirm_action")
    def test_refusing_a_later_substep_still_fails_overall(self, confirm, decommission_no_obs):
        confirm.side_effect = [True, True, False]
        result = decommission_no_obs.decommission(interactive=True)
        assert result.succeeded is False
        assert result.substeps["multiclusterhub"] is SubstepOutcome.REFUSED

    def test_disabled_observability_is_not_requested_not_a_failure(self, decommission_no_obs):
        result = decommission_no_obs.decommission(interactive=False)
        assert result.substeps["observability"] is SubstepOutcome.NOT_REQUESTED
        assert result.succeeded is True

    @patch("modules.decommission.confirm_action")
    def test_non_interactive_never_prompts(self, confirm, decommission_with_obs):
        decommission_with_obs.decommission(interactive=False)
        confirm.assert_not_called()

    def test_summary_names_completed_refused_and_not_attempted(self):
        result = DecommissionResult(
            substeps={"observability": SubstepOutcome.COMPLETED,
                      "managed_clusters": SubstepOutcome.REFUSED},
            not_attempted=("multiclusterhub",),
        )
        text = "\n".join(result.summary_lines())
        assert "observability" in text and "completed" in text
        assert "managed_clusters" in text and "refused" in text
        assert "multiclusterhub" in text and "not attempted" in text

    def test_result_has_no_boolean_shortcut(self):
        assert "__bool__" not in vars(DecommissionResult)


class TestActualChangeTruth:
    """`changed` is accepted mutation in this invocation, never intent or prediction."""

    def _executing(self, decommission, executions):
        """Drive the substep loop with declared per-substep executions."""
        calls = []

        def fake_run_substep(substep):
            calls.append(substep)
            execution = executions[substep]
            if isinstance(execution, Exception):
                raise execution
            return execution

        decommission._run_substep = fake_run_substep
        return calls

    def test_a_noop_substep_reports_no_change(self, decommission_with_obs):
        self._executing(decommission_with_obs, {
            step: SubstepExecution(SubstepOutcome.PRECONDITION_NOOP, changed=False)
            for step in ("observability", "managed_clusters", "multiclusterhub")
        })
        result = decommission_with_obs.decommission(interactive=False)
        assert result.succeeded is True
        assert result.changed is False

    def test_a_resumed_substep_without_a_new_mutation_reports_no_change(self, decommission_with_obs):
        self._executing(decommission_with_obs, {
            "observability": SubstepExecution(SubstepOutcome.COMPLETED, changed=False),
            "managed_clusters": SubstepExecution(SubstepOutcome.NOT_REQUESTED, changed=False),
            "multiclusterhub": SubstepExecution(SubstepOutcome.PRECONDITION_NOOP, changed=False),
        })
        result = decommission_with_obs.decommission(interactive=False)
        assert result.changed is False, "a completed record proved live is not a new mutation"

    def test_an_accepted_delete_with_a_completion_proof_reports_change(self, decommission_with_obs):
        self._executing(decommission_with_obs, {
            "observability": SubstepExecution(SubstepOutcome.COMPLETED, changed=True),
            "managed_clusters": SubstepExecution(SubstepOutcome.PRECONDITION_NOOP, changed=False),
            "multiclusterhub": SubstepExecution(SubstepOutcome.PRECONDITION_NOOP, changed=False),
        })
        result = decommission_with_obs.decommission(interactive=False)
        assert result.changed is True
        assert result.succeeded is True

    def test_a_later_failure_does_not_erase_an_earlier_actual_change(self, decommission_with_obs):
        self._executing(decommission_with_obs, {
            "observability": SubstepExecution(SubstepOutcome.COMPLETED, changed=True),
            "managed_clusters": SwitchoverError("inventory unverifiable"),
            "multiclusterhub": SubstepExecution(SubstepOutcome.COMPLETED, changed=True),
        })
        result = decommission_with_obs.decommission(interactive=False)
        assert result.changed is True
        assert result.succeeded is False
        assert result.substeps["managed_clusters"] is SubstepOutcome.FAILED
        assert result.not_attempted == ("multiclusterhub",)

    @patch("modules.decommission.confirm_action")
    def test_a_later_refusal_does_not_erase_an_earlier_actual_change(self, confirm, decommission_with_obs):
        confirm.side_effect = [True, True, False]  # proceed, run observability, decline the next
        self._executing(decommission_with_obs, {
            "observability": SubstepExecution(SubstepOutcome.COMPLETED, changed=True),
            "managed_clusters": SubstepExecution(SubstepOutcome.COMPLETED, changed=True),
            "multiclusterhub": SubstepExecution(SubstepOutcome.COMPLETED, changed=True),
        })
        result = decommission_with_obs.decommission(interactive=True)
        assert result.changed is True
        assert result.succeeded is False

    def test_dry_run_records_no_outcome_and_never_reports_actual_change(self, decommission_dry_run):
        result = decommission_dry_run.decommission(interactive=False)
        assert result.substeps == {}
        assert result.not_attempted == ("observability", "managed_clusters", "multiclusterhub")
        assert result.changed is False
        assert isinstance(result.would_change, bool)
        assert decommission_dry_run.run_record.all_teardown_records() == {}
        decommission_dry_run.primary_client.delete_custom_resource.assert_not_called()

    def test_dry_run_prediction_is_separate_from_actual_change(self, decommission_dry_run, monkeypatch):
        monkeypatch.setattr(decommission_dry_run, "_preview_substep", lambda substep: True)
        result = decommission_dry_run.decommission(interactive=False)
        assert result.would_change is True
        assert result.changed is False

    def test_every_substep_executor_returns_the_one_execution_type(self):
        # Producer/consumer agreement is asserted, not assumed: C, D, and E extend this list.
        import inspect

        for name in ("_run_substep",):
            annotation = inspect.signature(getattr(Decommission, name)).return_annotation
            assert annotation in (SubstepExecution, "SubstepExecution")
```

`tests/test_decommission.py` already defines `mock_primary_client` (`:26`),
`decommission_with_obs` (`:35`), and `decommission_no_obs` (`:41`). This step **extends** those
three to construct `Decommission` with a real `RunRecord` over a `tmp_path` `StateManager` — the
constructor argument Task B5 makes required — and **adds** two more in the same place:
`state_manager`, the `StateManager` those fixtures share, and `decommission_dry_run`, identical to
`decommission_with_obs` except `dry_run=True`. No other test file gains a fixture, and no
`tests/conftest.py` is created: the repository deliberately has none.

Add to `tests/test_finalization.py`:

```python
def test_integrated_decommission_failure_keeps_its_own_message_context(finalization, monkeypatch):
    monkeypatch.setattr(
        decommission_module.Decommission, "decommission",
        lambda self, interactive=True: DecommissionResult(
            substeps={"observability": SubstepOutcome.FAILED}, not_attempted=("managed_clusters",)
        ),
    )
    with pytest.raises(SwitchoverError) as excinfo:
        finalization._decommission_old_hub()
    assert "Manual cleanup is required" in str(excinfo.value)
    assert "observability" in str(excinfo.value)


def test_finalization_maps_succeeded_explicitly_not_object_truthiness():
    source = inspect.getsource(Finalization._decommission_old_hub)
    assert ".succeeded" in source
    assert "if result:" not in source
```

`inspect` is imported at module scope in `tests/test_finalization.py`.

Add to `tests/test_main.py`, inside the existing class that covers `run_decommission`
(`tests/test_main.py:2551`). That file defines no fixtures: it builds `args`, `primary`, `state`,
and `logger` inline and patches `acm_switchover.Decommission`, and these tests do the same:

```python
    def test_run_decommission_returns_false_on_refusal(self):
        from acm_switchover import run_decommission

        args = SimpleNamespace(dry_run=False, non_interactive=True, skip_rbac_validation=True)
        primary = Mock()
        primary.namespace_exists.return_value = True
        state = Mock()
        logger = Mock()

        with patch("acm_switchover.Decommission") as Decom:
            Decom.return_value.decommission.return_value = DecommissionResult(
                substeps={"observability": SubstepOutcome.REFUSED},
                not_attempted=("multiclusterhub",),
            )
            assert run_decommission(args, primary, state, logger) is False

    def test_run_decommission_preserves_false_cli_result_on_banner_cancel(self):
        from acm_switchover import run_decommission

        args = SimpleNamespace(dry_run=False, non_interactive=False, skip_rbac_validation=True)
        primary = Mock()
        primary.namespace_exists.return_value = True
        state = Mock()
        logger = Mock()

        with patch("acm_switchover.Decommission") as Decom:
            Decom.return_value.decommission.return_value = DecommissionResult(
                substeps={},
                not_attempted=("observability", "managed_clusters", "multiclusterhub"),
                cancelled=True,
            )
            assert run_decommission(args, primary, state, logger) is False
```

- [ ] **Step 2: Run and observe the expected failures**

```bash
python -m pytest tests/test_decommission.py -q -k "Outcomes or ActualChangeTruth"
```

Expected: FAIL — `decommission()` returns `True`, so `result.succeeded` raises `AttributeError` on
a `bool`; and `ImportError` for `SubstepExecution`, which does not exist yet.

- [ ] **Step 3: Implement**

Add `lib/decommission_outcome.py` with `SubstepOutcome`, `SubstepExecution`, and
`DecommissionResult`, then rewrite `Decommission.decommission` as an explicit loop over three
declared substeps rather than three copied `if/else` blocks:

```python
    _SUBSTEPS = ("observability", "managed_clusters", "multiclusterhub")

    _PROMPTS = {
        "observability": "\nDelete MultiClusterObservability resource?",
        "managed_clusters": "\nDelete ManagedCluster resources (excluding local-cluster)?",
        "multiclusterhub": "\nDelete MultiClusterHub resource? (This will remove all ACM components)",
    }

    def decommission(self, interactive: bool = True) -> DecommissionResult:
        """Run the requested teardown substeps.

        A refusal aborts the remaining substeps and yields an unsuccessful
        result; it is never persisted (it ends the run, and the summary is
        output rather than state).
        """
        self._log_decommission_banner()

        if interactive and not confirm_action(
            "\nAre you sure you want to proceed with decommissioning the old hub?", default=False
        ):
            logger.info("Decommission cancelled by user")
            requested = tuple(step for step in self._SUBSTEPS if self._substep_requested(step))
            return DecommissionResult(
                substeps={}, not_attempted=requested, cancelled=True,
                changed=False, would_change=False,
            )

        if self.dry_run:
            requested = tuple(step for step in self._SUBSTEPS if self._substep_requested(step))
            predicted = [self._preview_substep(step) for step in requested]
            return DecommissionResult(
                substeps={}, not_attempted=requested, changed=False,
                would_change=any(predicted),
            )

        outcomes: dict[str, SubstepOutcome] = {}
        changed = False
        for index, substep in enumerate(self._SUBSTEPS):
            if not self._substep_requested(substep):
                outcomes[substep] = SubstepOutcome.NOT_REQUESTED
                continue
            if interactive and not confirm_action(self._PROMPTS[substep], default=False):
                outcomes[substep] = SubstepOutcome.REFUSED
                return DecommissionResult(
                    substeps=outcomes, not_attempted=self._remaining_after(index), changed=changed
                )
            try:
                execution = self._run_substep(substep)
            except SwitchoverError as exc:
                logger.error("Decommission substep %s failed: %s", substep, exc)
                outcomes[substep] = SubstepOutcome.FAILED
                return DecommissionResult(
                    substeps=outcomes, not_attempted=self._remaining_after(index), changed=changed
                )
            outcomes[substep] = execution.outcome
            changed = changed or execution.changed

        return DecommissionResult(substeps=outcomes, not_attempted=(), changed=changed)

    def _remaining_after(self, index: int) -> tuple[str, ...]:
        return tuple(step for step in self._SUBSTEPS[index + 1:] if self._substep_requested(step))
```

`_run_substep(substep) -> SubstepExecution` dispatches to the three family methods and returns their
`SubstepExecution` unchanged. A substep that raises `SwitchoverError` after having already issued an
accepted DELETE must report that mutation, so the family methods catch their own mid-flight failure,
record the phase, and re-raise only after the aggregation above has no further work to do; where a
family cannot report both, it raises and the earlier substeps' `changed` still stands. `_preview_substep`
performs only fresh reads and returns a bool; it is never called by a live run and never writes
`RunRecord`. PRs C, D, and E replace their family dispatch and predictor together with the guarded
phase machine and return `PRECONDITION_NOOP` where July §3 proves no mutation is needed. Update both
callers per the mapping table and add a source guardrail forbidding bare truthiness of
`DecommissionResult`.

- [ ] **Step 4: Run and observe the tests pass**

```bash
python -m pytest tests/test_decommission.py tests/test_finalization.py tests/test_main.py -q
```

- [ ] **Step 5: Refactor**

The loop above replaces three near-identical prompt/skip blocks. Confirm no `return True` remains
in `modules/decommission.py` and that the broad `except Exception` at the old `:99-102` either
disappears or is narrowed to a logged `FAILED` outcome rather than a silent success.

- [ ] **Step 6: Commit**

```bash
git add lib/decommission_outcome.py lib/constants.py modules/decommission.py \
  modules/finalization.py acm_switchover.py tests/
git commit -m "fix: make a refused decommission substep fail the run"
```

## Task B4: Collection outcome parity and artifact honesty

**Files:** Modify `roles/decommission/tasks/main.yml`; modify each of the three
`delete_*.yml` task files to register a per-substep outcome; modify
`tests/unit/test_decommission_role_contracts.py`; modify `tests/scenario/` decommission fixtures;
modify `tests/test_constants_parity.py`.

**Purpose:** Close criterion A3 — the summary artifact must report the real aggregated outcome
instead of hard-coded `pass` — and give the collection outcome parity with Task B3.

**Intended behavior.** Each substep sets one fact from the mirrored vocabulary:
`acm_switchover_decommission_outcomes.observability`, `.managed_clusters`, `.multiclusterhub`.
`main.yml` aggregates them into `acm_switchover_decommission_result.status`, which is `pass` only
when no substep is `failed`, and computes actual `changed` from real mutation results. Refusal is not
applicable: the role stays non-interactive behind its confirmed-gate.

**Failure behavior.** A `failed` substep fails the play. Today's silent degradation to a warning
is removed in the substeps PRs C, D, and E own; this task removes the hard-coded `pass` that would
otherwise mask them.

**Check-mode implications.** This safety ships in B. `main.yml` computes `changed: false` whenever
`ansible_check_mode` is true and never derives actual change from a module's prospective
`changed`. It publishes prediction only as `would_change`. At the B stage `would_change` is
explicitly `false`: B has not yet introduced a fresh strict per-family prediction result, and
debug announcements or a check-mode module's prospective `changed` are not evidence. C/D/E add
their accurate family predictions in the same PR as each mutation. Check mode skips every
checkpoint writer and records no substep outcome as completed/refused/failed. B tests this before
merge; F repeats it only as integrated proof.

### B4.1 The one collection role-result contract

Every collection test in B4, B5, C4, C5, and F1 drives the role through **one** helper with **one**
returned shape. It is defined here, in
`ansible_collections/tomazb/acm_switchover/tests/unit/test_decommission_role_contracts.py`, and
imported by the scenario lane; no other result shape is introduced later.

```python
def run_decommission_role(**options) -> dict:
    """Run the decommission role against declared fakes and return one canonical result."""
```

Returned mapping, exactly:

```python
{
    "tasks": [
        # one entry per executed task, in order
        {"name": str, "module": str, "changed": bool, "skipped": bool, "failed": bool, "result": dict},
    ],
    "facts": {...},                              # role-set facts after the run
    "acm_switchover_decommission_result": {      # the published summary artifact
        "status": "pass" | "fail",
        "substeps": {"observability": str, "managed_clusters": str, "multiclusterhub": str},
        "changed": bool,
        "would_change": bool,
    },
    "checkpoint": {
        "operational_data": dict,                # after the run
        "before_operational_data": dict,         # captured before the run, for byte comparison
        "phases": [...],                         # phase enter/exit calls, in order
    },
    "delete_calls": [...],                       # every acm_uid_guarded_delete invocation, in order
    "gate": {...} | None,                        # destination-gate result, when the run reached it (Task C5)
}
```

Accepted options: `check_mode: bool = False`; `execution_mode: str = "execute"`;
per-substep outcome overrides such as `observability_outcome="failed"`; resource-presence fakes
`mco_present`, `mch_present`, `managed_clusters`, `destination_mco`, `destination_namespace`;
`checkpoint_available: bool = True`; and
`acknowledge_observability_not_migrated: bool = False`.

`operational_data` lives at `result["checkpoint"]["operational_data"]` and the pre-run copy at
`result["checkpoint"]["before_operational_data"]`. There is **no** top-level
`result["operational_data"]`.

The role-**file** contract tests in B4, B5, C4, and F1 use one further set of module-level helpers,
all added to the same file next to its existing `DECOMMISSION_MAIN` / `DELETE_*` path constants and
`_include_file` helper. Nothing in a later task introduces another parsing helper:

| Helper | Kind | Contract |
| --- | --- | --- |
| `decommission_task_files` | module-level mapping | `{"main": [...], "observability": [...], "managed_clusters": [...], "multiclusterhub": [...]}`, each value the `yaml.safe_load` of the corresponding role task file |
| `task_named(tasks, name)` | function | the single task whose `name` equals `name`; raises `AssertionError` when absent or ambiguous |
| `index_of_task_using(tasks, action)` | function | the index of the first task invoking `action` (module or action-plugin name); `-1` when absent |
| `index_of_first_include(tasks, prefix)` | function | the index of the first `include_tasks` whose file name starts with `prefix`; `-1` when absent, reusing the existing `_include_file` |
| `read_outcome_tasks(tasks)` | function | every task invoking `tomazb.acm_switchover.acm_k8s_read_outcome` |
| `checkpoint_writer_tasks(task_files)` | function | every task in the given mapping that enters, exits, or writes checkpoint `operational_data` |
| `mutating_tasks(task_files)` | function | every parsed task whose module can mutate the cluster |
| `CHECK_MODE_NATIVE_MODULES` | frozen set | the collection modules implementing native check mode: `tomazb.acm_switchover.acm_uid_guarded_delete`, `tomazb.acm_switchover.acm_k8s_read_outcome`, and `tomazb.acm_switchover.acm_pod_owner_classify` once PR E adds it |

These are plain module-level names, not pytest fixtures, so every snippet below calls them
directly rather than declaring them as test parameters.

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_decommission_role_contracts.py`:

```python
def test_summary_status_is_not_hardcoded():
    publish = task_named(decommission_task_files["main"], "Publish decommission result")
    status = publish["ansible.builtin.set_fact"]["acm_switchover_decommission_result"]["status"]
    assert status != "pass", "status must be derived from the real substep outcomes"
    assert "acm_switchover_decommission_outcomes" in str(status)


def test_every_substep_publishes_an_outcome():
    for substep in ("observability", "managed_clusters", "multiclusterhub"):
        assert any(
            f"acm_switchover_decommission_outcomes" in str(task)
            for task in decommission_task_files[substep]
        ), f"{substep} must publish an outcome"


def test_a_failed_substep_produces_a_failed_status():
    result = run_decommission_role(observability_outcome="failed")
    assert result["acm_switchover_decommission_result"]["status"] == "fail"


def test_outcome_values_come_from_the_collection_constants():
    from ansible_collections.tomazb.acm_switchover.plugins.module_utils import constants

    result = run_decommission_role(observability_outcome="precondition_noop")
    assert (
        result["acm_switchover_decommission_result"]["substeps"]["observability"]
        in constants.DECOMMISSION_SUBSTEP_OUTCOMES
    )


def test_b_stage_check_mode_reports_no_actual_or_speculative_change():
    result = run_decommission_role(check_mode=True)
    summary = result["acm_switchover_decommission_result"]
    assert summary["changed"] is False
    assert summary["would_change"] is False
    assert not [task for task in result["tasks"] if task["changed"]]


def test_b_stage_check_mode_writes_no_checkpoint_or_outcome():
    result = run_decommission_role(check_mode=True)
    checkpoint = result["checkpoint"]
    assert checkpoint["operational_data"] == checkpoint["before_operational_data"]
    assert checkpoint["phases"] == []
    assert result["acm_switchover_decommission_result"]["substeps"] == {}
```

The cross-form-factor comparison belongs in the root parity surface, not here: collection tests do
not import Python CLI code, and no such import exists in the repository today. Add it to
`tests/test_constants_parity.py` instead, where root tests already read collection constants:

```python
def test_decommission_outcome_vocabulary_parity():
    from lib.decommission_outcome import SubstepOutcome

    assert {o.value for o in SubstepOutcome} == set(ans_constants.DECOMMISSION_SUBSTEP_OUTCOMES)
```

- [ ] **Step 2: Run and observe the expected failure**

```bash
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_decommission_role_contracts.py -q
```

Expected: FAIL — the published status is the literal `pass`.

- [ ] **Step 3: Implement, rerun, and add the reset tests**

Add to `tests/scenario/` a decommission checkpoint case proving:

1. a full `checkpoint.reset` removes `decommission_teardown_records`, and a post-reset rerun that
   finds the CR absent takes the clean-skip path — **asserted as current, documented behavior**,
   so the R4-05 coordination stays visible rather than silently mitigated;
2. `reset_from` retains `operational_data` and therefore retains the teardown records, and the
   rerun revalidates them instead of laundering them.

```bash
export ANSIBLE_COLLECTIONS_PATH="$(pwd):${HOME}/.ansible/collections"
PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/scenario/ -q
```

- [ ] **Step 4: Commit**

```bash
git add ansible_collections/tomazb/acm_switchover/roles/decommission/ \
  ansible_collections/tomazb/acm_switchover/tests/
git commit -m "fix: report the real decommission outcome in the collection artifact"
```

## Task B5: Wire durable state into both decommission entry points

**Files:** Modify `acm_switchover.py`; modify `modules/decommission.py`; modify
`modules/finalization.py`; modify `roles/decommission/tasks/main.yml`; modify
`tests/test_main.py`, `tests/test_finalization.py`, `tests/test_decommission.py`, and
`tests/unit/test_decommission_role_contracts.py`.

**Purpose:** `Decommission` cannot own durable records it has no access to. `run_decommission`
already receives `state` and never uses it (`acm_switchover.py:933-976`); the collection role has no
checkpoint wiring at all. Both are net-new wiring the amendment §13 names explicitly.

**Interfaces produced:**

```python
class Decommission:
    def __init__(self, primary_client, has_observability, *, run_record: RunRecord,
                 dry_run: bool = False) -> None: ...
```

`run_record` is keyword-only and **required**: a decommission without durable state cannot satisfy
the July deletion boundary, and a default would let a caller silently opt out.

**Intended behavior.** `run_decommission` constructs `RunRecord(state)` and passes it.
`Finalization` passes the `RunRecord` it already holds at `modules/finalization.py:107`. The
collection role enters and exits a `decommission` checkpoint phase through the existing
`checkpoint_phase` action plugin, so `operational_data` is durable before the first DELETE.

**Failure behavior.** Execute-mode collection decommission without checkpointing available fails
closed — the July deletion boundary requires the identity map to be durable before the first
DELETE. Dry-run and check mode do not require it and write nothing.

**B-merge safety.** Python branches on `self.dry_run` before every `RunRecord` call and does not
pass preview data into any state API. Collection checkpoint enter/exit and all future
`record_teardown_phase` calls carry `when: not ansible_check_mode`; role result aggregation is the
B4 check-mode-safe expression. A subsequent live run begins from unchanged authoritative state and
never reads `would_change` as authority.

- [ ] **Step 1: Write the failing Python tests**

Add to `tests/test_main.py`, in the same class and the same inline style as Task B3, importing
`json`, `RunRecord`, `StateManager`, `DecommissionResult`, and
`TeardownPhase`/`TeardownRecord`/`teardown_key` at module scope. The patched
`acm_switchover.Decommission` mock is itself the stand-in — `run_decommission` only calls
`.decommission()` on what the constructor returned — so no hand-written stub class is needed:

```python
    def test_run_decommission_passes_a_run_record(self):
        from acm_switchover import run_decommission

        args = SimpleNamespace(dry_run=False, non_interactive=True, skip_rbac_validation=True)
        primary = Mock()
        primary.namespace_exists.return_value = True
        state = Mock()
        logger = Mock()

        with patch("acm_switchover.Decommission") as Decom:
            Decom.return_value.decommission.return_value = DecommissionResult(
                substeps={}, not_attempted=()
            )
            run_decommission(args, primary, state, logger)

        assert isinstance(Decom.call_args.kwargs["run_record"], RunRecord)
        Decom.return_value.decommission.assert_called_once()

    def test_run_decommission_run_record_is_backed_by_the_cli_state(self, tmp_path):
        from acm_switchover import run_decommission

        args = SimpleNamespace(dry_run=False, non_interactive=True, skip_rbac_validation=True)
        primary = Mock()
        primary.namespace_exists.return_value = True
        state = StateManager(str(tmp_path / "switchover-test.json"))
        logger = Mock()

        with patch("acm_switchover.Decommission") as Decom:
            Decom.return_value.decommission.return_value = DecommissionResult(
                substeps={}, not_attempted=()
            )
            run_decommission(args, primary, state, logger)

        run_record = Decom.call_args.kwargs["run_record"]
        run_record.record_teardown_phase(
            TeardownRecord(
                key=teardown_key("observability.open-cluster-management.io/v1beta2",
                                 "MultiClusterObservability", None, "observability"),
                expected_uid="u", phase=TeardownPhase.DELETE_STARTED,
                resource_versions={"cr": "1"},
            )
        )
        assert "decommission_teardown_records" in json.dumps(state.capture_state_snapshot())
```

Add to `tests/test_finalization.py`, patching the name `modules/finalization.py` actually imported
(`modules.finalization.Decommission`, the target its existing decommission test at `:217` already
uses):

```python
def test_finalization_passes_its_own_run_record(finalization):
    with patch("modules.finalization.Decommission") as decommission_class:
        decommission_class.return_value.decommission.return_value = DecommissionResult(
            substeps={"observability": SubstepOutcome.COMPLETED}, not_attempted=()
        )
        finalization._decommission_old_hub()

    assert decommission_class.call_args.kwargs["run_record"] is finalization.run_record
```

Add to `tests/test_decommission.py`:

```python
def test_decommission_requires_a_run_record(mock_primary_client):
    with pytest.raises(TypeError):
        Decommission(mock_primary_client, True)


def test_dry_run_writes_nothing_to_state(decommission_dry_run, state_manager):
    before = json.dumps(state_manager.capture_state_snapshot(), sort_keys=True)
    decommission_dry_run.decommission(interactive=False)
    assert json.dumps(state_manager.capture_state_snapshot(), sort_keys=True) == before


def test_a_live_run_after_a_dry_run_reads_fresh_and_trusts_nothing(
    decommission_dry_run, decommission_with_obs, state_manager, mock_primary_client
):
    decommission_dry_run.decommission(interactive=False)
    mock_primary_client.reset_mock()
    decommission_with_obs.decommission(interactive=False)
    assert mock_primary_client.method_calls, "the live run must perform its own reads"
```

- [ ] **Step 2: Run the Python tests and observe the expected failure**

```bash
python -m pytest tests/test_main.py tests/test_finalization.py tests/test_decommission.py \
  -q -k "run_record or dry_run_writes_nothing or trusts_nothing"
```

Expected: FAIL — `KeyError: 'run_record'` from `Decom.call_args.kwargs`, because
`run_decommission` constructs `Decommission(primary, has_observability, dry_run=args.dry_run)` with
no `run_record` (`acm_switchover.py:965-969`); the same `KeyError` in the finalization test; and
`test_decommission_requires_a_run_record` fails because the current `__init__` accepts the two
positional arguments happily instead of raising `TypeError`.

- [ ] **Step 3: Implement the Python wiring**

1. `Decommission.__init__` gains the keyword-only required `run_record: RunRecord` parameter and
   stores it as `self.run_record`.
2. `acm_switchover.py::run_decommission` builds `RunRecord(state)` from the `state` it already
   receives and passes `run_record=` to the constructor. Nothing else in that function changes.
3. `modules/finalization.py:1138-1141` passes `run_record=self.run_record` when it instantiates
   `Decommission`, keeping `interactive=False`.
4. Every `RunRecord` writer call inside `Decommission` is preceded by an explicit `if self.dry_run`
   branch that returns the read-only prediction instead; no preview value is ever passed into a
   state API.

- [ ] **Step 4: Run the Python tests and observe them pass**

```bash
python -m pytest tests/test_main.py tests/test_finalization.py tests/test_decommission.py -q
```

- [ ] **Step 5: Write the failing collection tests**

Add to `tests/unit/test_decommission_role_contracts.py`, using the B4.1 contract:

```python
def test_role_enters_a_checkpoint_phase_before_the_first_teardown_include():
    tasks = decommission_task_files["main"]
    phase_index = index_of_task_using(tasks, "checkpoint_phase")
    first_teardown_index = index_of_first_include(tasks, "delete_")
    assert phase_index < first_teardown_index


def test_execute_mode_fails_closed_when_checkpointing_is_unavailable():
    result = run_decommission_role(checkpoint_available=False, execution_mode="execute")
    assert result["acm_switchover_decommission_result"]["status"] == "fail"
    assert result["delete_calls"] == []


def test_check_mode_does_not_require_checkpointing_and_writes_nothing():
    result = run_decommission_role(checkpoint_available=False, check_mode=True)
    checkpoint = result["checkpoint"]
    assert checkpoint["operational_data"] == checkpoint["before_operational_data"]
    assert checkpoint["phases"] == []
    assert result["delete_calls"] == []
    assert result["acm_switchover_decommission_result"]["changed"] is False


def test_dry_run_execution_mode_does_not_require_checkpointing():
    result = run_decommission_role(checkpoint_available=False, execution_mode="dry-run")
    assert result["acm_switchover_decommission_result"]["status"] != "fail"
    assert result["delete_calls"] == []


def test_every_checkpoint_writer_task_is_guarded_by_check_mode():
    for task in checkpoint_writer_tasks(decommission_task_files):
        assert "not ansible_check_mode" in str(task.get("when", ""))
```

`index_of_task_using`, `index_of_first_include`, and `checkpoint_writer_tasks` are small parsing
helpers added next to `decommission_task_files` in the same file (B4.1).

- [ ] **Step 6: Run the collection tests and observe the expected failure**

```bash
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_decommission_role_contracts.py -q
```

Expected: FAIL — `roles/decommission/tasks/main.yml` contains no `checkpoint_phase` task at all
(amendment §4: "the decommission role never touches checkpoint or `operational_data`"), so
`index_of_task_using` returns no index and the execute-mode fail-closed test finds `status == "pass"`.

- [ ] **Step 7: Implement the collection wiring**

Add to `roles/decommission/tasks/main.yml`, after the confirmed-gate and before the first
`delete_*.yml` include:

1. a `checkpoint_phase` **enter** task for the `decommission` phase, carrying
   `when: not ansible_check_mode`;
2. an assertion, evaluated only when `execution_mode` is `execute` and not in check mode, that the
   checkpoint is available, failing the play with an explicit message when it is not;
3. a matching `checkpoint_phase` **exit** task after the last include, with the same guard.

No teardown-record writes are added here: PR B only establishes the phase and the availability
requirement, and C, D, and E write records inside it.

- [ ] **Step 8: Run the collection tests and observe them pass, then run the scenario lane**

```bash
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/unit/ -q
export ANSIBLE_COLLECTIONS_PATH="$(pwd):${HOME}/.ansible/collections"
PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/scenario/ -q
```

- [ ] **Step 9: Simplify, rerun, and commit**

The wiring must add no second construction path for `Decommission` and no conditional
`RunRecord`. Rerun the two commands in Steps 4 and 8, then:

```bash
git add acm_switchover.py modules/decommission.py modules/finalization.py \
  ansible_collections/tomazb/acm_switchover/roles/decommission/tasks/main.yml \
  tests/ ansible_collections/tomazb/acm_switchover/tests/
git commit -m "feat: give both decommission entry points durable state"
```

## Task B6: PR B verification

- [ ] **Step 1: Targeted**

```bash
python -m pytest tests/test_teardown_record.py tests/test_decommission.py \
  tests/test_finalization.py tests/test_run_record.py tests/test_run_record_guardrails.py \
  tests/test_checkpoint_state_parity.py tests/test_constants_parity.py -q
```

- [ ] **Step 2: Root surface, collection surfaces 3 through 7 in both lanes, quality and security
gates, scope and protected-file check** — the same command set as Task A6 steps 2 through 5.

- [ ] **Step 3: Documentation**

`CHANGELOG.md` `## [Unreleased]` under `### Fixed`: a top-level cancellation or refused
decommission substep now fails the run and exits non-zero; the collection decommission artifact
reports its real status. Update [`docs/operations/usage.md`](../operations/usage.md)'s
"Decommission Old Hub" section to state cancellation/refusal behavior, the exit status, and the
separate actual `changed` versus `would_change` semantics. Update
[`docs/development/architecture.md`](../development/architecture.md),
[`docs/ansible-collection/parity-matrix.md`](../ansible-collection/parity-matrix.md), and
[`docs/ansible-collection/behavior-map.md`](../ansible-collection/behavior-map.md) in B because the
result and durable-state interfaces become true here; do not defer those descriptions to F.

```bash
python -m pytest tests/test_documentation_guardrails.py -q
```

- [ ] **Step 4: Simplification gate, then open PR B.**

---

# 11. Remaining implementation PRs

## 11A. PR C — guarded deletion, MCO teardown, GLM-H6 consolidation, destination gate

**Branch:** `feature/r4-03-mco-teardown` · **Worktree:** `.claude/worktrees/r4-03-mco-teardown`

**Purpose.** Deliver the single preconditioned-delete primitive in each form factor, convert the
MCO substep to the July §1 phase machine, consolidate the duplicated Python MCO teardown
(GLM-H6), and add the destination-observability gate. This PR introduces the first new API calls
of the slice and therefore carries the first RBAC change.

**Prerequisites:** PR A and PR B merged.

### 11A.1 Decision record for PR C

| Decision | Value | Basis |
| --- | --- | --- |
| Python delete primitive | `KubeClient.delete_custom_resource_preconditioned(group, version, plural, name, *, uid, resource_version=None, namespace=None, timeout_seconds=None)` | Amendment §8 item 2 — one primitive with `uid` required and `resource_version` optional, so R4-04 Task 4 consumes the same one; R4-03 callers pass UID only |
| Decorators on it | none | A `@api_call(not_found_value=True)` would swallow the 404 the primitive must classify itself |
| Collection module | `tomazb.acm_switchover.acm_uid_guarded_delete` backed by `plugins/module_utils/uid_guarded_delete.py` | July deletion boundary, verbatim |
| Collection client factory | `config.new_client_from_config(persist_config=False, config_file=kubeconfig, context=context)` | July deletion boundary names it; the existing `module_utils` precedent is `plugins/module_utils/klusterlet.py:82,100` |
| Teardown owner | `Decommission` owns the one Python MCO algorithm | Amendment §11 item 1 |
| GitOps markers | boolean parameter `record_gitops_markers: bool = False` on the shared teardown | Amendment §11 item 2 permits a flag or a callback; a flag is the smaller model and `safe_record_gitops_markers` is already a free function |
| MCO drain selector | new mirrored constant `OBSERVABILITY_POD_LABEL_SELECTOR = "observability.open-cluster-management.io/name=observability"` | July §1 step 4 promotes the `modules/post_activation.py:569` literal to a shared constant |
| Strict Pod and namespace reads | consume PR A's `KubeClient.list_pods_strict(...)` and `get_namespace_strict(...)` | The closed transport algebra lands in A; C owns only MCO policy and the first live RBAC use |
| Ack flag | `--acknowledge-observability-not-migrated` | July §4 step 4 names it; no existing flag collides |
| Collection ack variable | `acm_switchover_decommission.acknowledge_observability_not_migrated` (default `false`) | Collection variables carry the `acm_switchover_` prefix |
| Destination gate result | never persisted | Amendment §9 and §13 |

### Task C1: Python preconditioned delete primitive

**Files:** Modify `lib/kube_client.py`; modify `tests/test_kube_client.py`.

**Interfaces produced:** `delete_custom_resource_preconditioned(...) -> None`, raising
`PreconditionConflict` (new, subclass of `FatalError`) on 409/412.

**Intended behavior.** Builds
`V1DeleteOptions(preconditions=V1Preconditions(uid=uid))`, adding `resource_version` only when the
caller supplies one, and passes it as the `body` of
`delete_namespaced_custom_object` / `delete_cluster_custom_object`. The UID is evaluated by the API
server atomically with the deletion.

**Failure behavior.** 409 and 412 raise `PreconditionConflict` and are **never** retried as an
unconditional or name-only delete. 404 raises `TargetDisappeared` so the caller can run the final
live verification rather than assuming success. Every other `ApiException` propagates.

**Dry-run implications.** The primitive refuses to run when `self.dry_run` is set — it raises
rather than logging a would-delete line, because a preview must never reach a delete primitive at
all; PR C's callers branch before this point.

**RBAC implications:** none. `delete` is already granted on all three CRs, and preconditions are
request-body content, not authorization (amendment §15).

- [ ] **Step 1: Write the failing tests**

```python
class TestPreconditionedDelete:
    def test_uid_precondition_is_sent_in_the_delete_body(self, kube_client):
        kube_client.custom_api.delete_cluster_custom_object = Mock(return_value={})
        kube_client.delete_custom_resource_preconditioned(
            "observability.open-cluster-management.io", "v1beta2",
            "multiclusterobservabilities", "observability", uid="uid-1",
        )
        body = kube_client.custom_api.delete_cluster_custom_object.call_args.kwargs["body"]
        assert body.preconditions.uid == "uid-1"
        assert body.preconditions.resource_version is None

    def test_resource_version_is_optional_and_omitted_for_r4_03_callers(self, kube_client):
        kube_client.custom_api.delete_cluster_custom_object = Mock(return_value={})
        kube_client.delete_custom_resource_preconditioned(
            "g", "v1", "widgets", "w", uid="uid-1", resource_version="77",
        )
        body = kube_client.custom_api.delete_cluster_custom_object.call_args.kwargs["body"]
        assert body.preconditions.resource_version == "77"

    @pytest.mark.parametrize("status", [409, 412])
    def test_precondition_conflict_is_fatal_and_never_retried_unconditionally(self, kube_client, status):
        kube_client.custom_api.delete_cluster_custom_object = Mock(
            side_effect=ApiException(status=status, reason="Conflict")
        )
        with pytest.raises(PreconditionConflict):
            kube_client.delete_custom_resource_preconditioned("g", "v1", "w", "n", uid="uid-1")
        assert kube_client.custom_api.delete_cluster_custom_object.call_count == 1

    def test_404_at_delete_time_is_surfaced_not_swallowed(self, kube_client):
        kube_client.custom_api.delete_cluster_custom_object = Mock(
            side_effect=ApiException(status=404, reason="Not Found")
        )
        with pytest.raises(TargetDisappeared):
            kube_client.delete_custom_resource_preconditioned("g", "v1", "w", "n", uid="uid-1")

    def test_empty_uid_is_rejected_before_any_request(self, kube_client):
        kube_client.custom_api.delete_cluster_custom_object = Mock()
        with pytest.raises(ValidationError):
            kube_client.delete_custom_resource_preconditioned("g", "v1", "w", "n", uid="")
        kube_client.custom_api.delete_cluster_custom_object.assert_not_called()

    def test_dry_run_client_refuses_the_primitive(self, kube_client):
        kube_client.dry_run = True
        with pytest.raises(FatalError):
            kube_client.delete_custom_resource_preconditioned("g", "v1", "w", "n", uid="uid-1")
```

- [ ] **Step 2:** run and observe `AttributeError`.
- [ ] **Step 3:** implement; add `PreconditionConflict` and `TargetDisappeared` to
  `lib/exceptions.py` under `FatalError`.
- [ ] **Step 4:** rerun; expected PASS.
- [ ] **Step 5:** refactor — one body builder, no duplicated namespaced/cluster branches beyond the
  API call itself.
- [ ] **Step 6:** `python -m pytest tests/test_kube_client.py -q` then commit
  `feat: add UID-preconditioned custom resource delete`.

### Task C2: Collection guarded-delete module

**Files:** Create `plugins/modules/acm_uid_guarded_delete.py`; create
`plugins/module_utils/uid_guarded_delete.py`; create
`tests/unit/test_uid_guarded_delete.py`; create
`tests/integration/test_uid_guarded_delete_runtime.py`; modify `meta/runtime.yml` if the
collection's action groups enumerate modules.

**Interfaces produced:** module options `kubeconfig`, `context`, `api_version`, `kind`,
`namespace`, `name`, `expected_uid`, `request_timeout`, `wait_timeout`, `wait_sleep`; results
`changed`, `would_change`, `stage`, `reason`, `resource_version`.

**Intended behavior.** The complete July deletion-boundary state machine: live GET through the
explicitly selected client, UID comparison, check-mode stop, preconditioned DELETE, bounded
monotonic absence poll, final live GET. `changed: true` only after the intended UID's DELETE was
accepted and the bounded completion plus final-absence contract succeeded.

**Failure behavior.** Only an API 404 means absent. Discovery, authorization, TLS, timeout,
transport, and decode failures are unverifiable and fail closed. HTTP 409 and 412 are fatal.
A same-name different-UID object at any point is fatal and is left intact.

**Check-mode implications.** Stops after the live read and UID validation; returns
`changed: false` plus explicit `would_change: true` for a matching present object, and
`changed: false`, `would_change: false` for an already-absent one.

**Redaction.** `no_log: true` on every invoking task; module output carries only a stable stage,
an API status classification, and the non-secret resource identity.

**RBAC implications:** none new (see C1).

- [ ] **Step 1: Write the failing tests** covering, one test each: expected-UID success reports
  `changed: true` only after bounded completion and confirmed final absence; a replacement created
  before DELETE produces a server-side precondition failure and survives; disappearance before
  DELETE returns `changed: false` only after confirmed absence; the same name with a different UID
  during polling fails immediately and survives; 409 and 412 are fatal and never fall back to a
  name-only delete; check mode performs the read and UID validation without DELETE and reports
  `would_change` accurately; already-absent reports `changed: false`; a GET 404 is distinguished
  from discovery, authorization, transport, timeout, and decode failures; explicit kubeconfig and
  context reach client construction and no ambient routing is used; request, poll, and total wait
  budgets are bounded and a same-UID timeout fails; and injected API errors containing kubeconfig
  text, bearer tokens, client certificates, private keys, response headers and bodies, and Secret
  material are absent from module results, failure messages, and callback-visible output.
- [ ] **Step 2:** run; expected FAIL — the module does not exist.
- [ ] **Step 3:** implement the `module_utils` state machine and the thin module wrapper.
  `kubeconfig` and `context` are `required=True`: an ambient fallback is the failure this boundary
  exists to prevent.
- [ ] **Step 4:** rerun unit and runtime lanes; expected PASS.
- [ ] **Step 5:** refactor — the state machine is one function per stage, and no stage returns a
  value that another stage must re-derive.
- [ ] **Step 6:** `ansible-test sanity --test validate-modules --python 3.12
  plugins/modules/acm_uid_guarded_delete.py`; then commit
  `feat: add UID-guarded delete module`.

### Task C3: Shared Python teardown phase machine and MCO consolidation

**Files:** Modify `modules/decommission.py`; modify `modules/finalization.py`; modify
`lib/constants.py`; consume PR A's strict core reads; modify
`tests/test_decommission.py`; modify `tests/test_finalization.py`.

**Purpose:** Implement the July §1 phase machine once, in `Decommission`, and delete both current
MCO teardown copies. Closes GLM-H6 and, for MCO, R4-C3 and R4-C4.

**Interfaces produced:**

```python
class Decommission:
    def teardown_observability(self, *, record_gitops_markers: bool = False) -> SubstepExecution: ...
    def _teardown_resource(self, spec: TeardownSpec, *, record_gitops_markers: bool) -> SubstepExecution: ...
```

Both return the **one** execution-result type defined in Task B3.1 — `SubstepExecution(outcome,
changed)` — so `_run_substep` returns what the family method produced, unchanged, and
`decommission()` aggregates `changed` exactly as B3.1 specifies. There is no tuple form and no
side channel anywhere in C, D, or E.

`changed` is derived here, per B3.2: it is `True` only when **this invocation** issued a
UID-preconditioned DELETE that the API server accepted for the recorded `expected_uid`. A resumed
record whose DELETE was accepted in an earlier invocation contributes `changed=False`, even when
this invocation completes the drain and writes `completed`. A `PRECONDITION_NOOP`, a clean skip, a
dry run, and every refusal or pre-DELETE failure are `changed=False`.

`TeardownSpec` declares the api group/version/plural, the canonical `resource_name`, the kind, the
optional namespace, the drain namespace, the drain label selector, and the classifier callable. PRs
D and E supply their own specs to the same `_teardown_resource`; that is what keeps one algorithm.

**Intended behavior — the phase machine, per resource:**

1. Load the durable record (Task B1). Strictly read the CR.
   - **No record** and CRD positively absent **and** the observability namespace positively absent
     → `PRECONDITION_NOOP`. CRD absent but the namespace present → fatal. A present CRD with a
     proven empty list and no record → `PRECONDITION_NOOP`. Every `error` outcome is fatal.
   - **Any record** — including `completed` — the clean-skip branch is unavailable. Reuse the
     recorded key and `expected_uid` and execute the remaining phase-table work.
2. Record `expected_uid` and `delete_started`, forced durable, **before** the DELETE.
3. `delete_custom_resource_preconditioned(..., uid=expected_uid)`.
4. Poll strictly for CR absence; record `cr_absent`. A same-name different-UID CR is fatal and is
   left intact.
5. Record `drain_pending`, then drain: PR A's `list_pods_strict(OBSERVABILITY_NAMESPACE,
   label_selector=OBSERVABILITY_POD_LABEL_SELECTOR)`. A positively absent namespace counts as
   verified-empty under the July §3 fixed-namespace scope rule; an unreadable or ambiguous
   namespace records `recovery_required`.
6. Record `drained` only after the bounded check proves empty; then re-run the CR-absence and
   pod-empty predicates and write `completed` carrying `observed_at` and the exact §10.2.1 proof
   keys for the proof mode this run actually used:
   - `cr` is the revision already recorded at `delete_started` from the strict named GET that bound
     `expected_uid`, carried forward unchanged;
   - `pods` is the `resource_version` of the successful `list_pods_strict(...)` whose final page
     proved the drain empty, when the drain was proven that way;
   - `namespace_absent` is the exact namespace name, when `get_namespace_strict(...)` returned
     `NAMESPACE_ABSENT` and the July §3 fixed-namespace scope rule entailed the pod-empty predicate.

   Exactly one of `pods` / `namespace_absent` is written for MCO and MCH. Writing both, neither, or
   a key from the other proof mode is malformed and is rejected by Task B1's validator before it
   reaches the state file.

**Failure behavior.** Timeout at any stage, a still-present same-UID CR, or an unobtainable proof
raises `SwitchoverError`; where the response is ambiguous the record moves to `recovery_required`.
The dead caller-side 404 arm at `modules/decommission.py:139-143` is deleted with the call site it
guarded, and the behavior it purported to cover is re-asserted against the real seam rather than
through the decorator-bypassing mocks at `tests/test_decommission.py:190-204`.

**Caller-specific semantics preserved** (amendment §11 item 2):

| Concern | Where it stays |
| --- | --- |
| `self.primary` present, `old_hub_action == "secondary"`, `primary_has_observability` gating | `Finalization.finalize` (`modules/finalization.py:211`), unchanged |
| `state.step("disable_observability_on_secondary")` | `Finalization`, unchanged |
| GitOps marker recording | `record_gitops_markers=True` from `Finalization` only |
| Finalization-specific failure text | `Finalization` catches and re-raises with its own message |
| `interactive=False` for the integrated path | unchanged (`modules/finalization.py:1141`) |
| Dry-run preview | single shared behavior |

**Dry-run/check-mode implications.** This contract is complete in C before C merges. Python
branches before every `record_teardown_phase`/identity writer and before
`delete_custom_resource_preconditioned`; it performs strict reads, reports the predicted blocker
set and MCO `would_change`, issues no DELETE, records no outcome as completed/refused/failed, and
writes no record or phase. Collection reads may run, but every guarded delete, operational-data
writer, and phase enter/exit is explicitly skipped under `ansible_check_mode`; the role and every
task report actual `changed: false`, with accurate MCO prediction only in `would_change`. A
check-mode run followed by live execution proves the latter performs fresh reads and trusts no
preview data.

**RBAC implications.** New: MCO CR named `get` (the strict named GET and the final live absence
proof). Carried in Task C6.

- [ ] **Step 1: Write the failing tests** — the phase-aware matrix from the July Testing section:
  no prior record with CRD and namespace both positively absent (clean skip); no record with CRD
  absent and namespace present (fatal); `delete_started` plus CRD absent (resumes, does not clean
  skip); `cr_absent` plus namespace absent; `drain_pending` with pods still present; any prior
  record plus a CRD, namespace, or pod-list API failure (fatal, never absence); `drained` plus a
  final-verification failure (no `completed` write); a finalizer-stuck MCO whose CR persists past
  the timeout raising `SwitchoverError`; a rerun after a drain timeout with the CR now absent
  still running the pod drain before `drained`; a crash-rerun after `delete_started` with a
  same-name replacement reusing the recorded UID, failing before DELETE, and leaving the
  replacement intact; and injected failure at each of the `drained` and `completed` boundaries
  proving no premature terminal write. Add the actual-change cases required by B3.2:
  a clean skip returns `SubstepExecution(PRECONDITION_NOOP, changed=False)`; a live accepted
  UID-preconditioned DELETE plus a successful completion proof returns
  `SubstepExecution(COMPLETED, changed=True)`; a resumed `drained` record whose final proof
  succeeds without any DELETE in this invocation returns `SubstepExecution(COMPLETED,
  changed=False)`; and a run that accepted the DELETE but then failed the final proof reports
  `changed=True` while raising. Add the completed-record proof-key cases: a drain proven by a
  successful Pod LIST writes `{cr, pods}`; a drain entailed by a positive namespace absence writes
  `{cr, namespace_absent}`; and no path writes both.
- [ ] **Step 2:** run; expected FAIL — `teardown_observability` does not exist and the current
  `_delete_observability` neither records nor re-reads.
- [ ] **Step 3:** implement `_teardown_resource` and `teardown_observability`, consuming PR A's
  `list_pods_strict` and `get_namespace_strict`; delete `modules/finalization.py:1003-1088` and replace it with a call to
  `decommission.teardown_observability(record_gitops_markers=True)`; add
  `OBSERVABILITY_POD_LABEL_SELECTOR` to both constants modules and to `CONSTANT_PAIRS`; replace
  the `modules/post_activation.py:569` literal with the constant.
- [ ] **Step 4:** rerun the targeted tests.
- [ ] **Step 5: Consolidation regression** — assert that both callers drive the one path, that
  GitOps markers are recorded for the finalization caller and not for direct decommission, and
  that finalization's preconditions and `state.step` wrapper still gate it:

```python
def test_finalization_and_direct_decommission_share_one_teardown_path(monkeypatch):
    calls = []

    def fake_teardown(self, spec, **kw):
        calls.append((spec.kind, kw))
        return SubstepExecution(SubstepOutcome.COMPLETED, changed=True)

    monkeypatch.setattr(Decommission, "_teardown_resource", fake_teardown)
    Finalization(...)._disable_observability_on_old_hub()
    Decommission(...).teardown_observability()
    assert [k for k, _ in calls] == ["MultiClusterObservability", "MultiClusterObservability"]
    assert calls[0][1]["record_gitops_markers"] is True
    assert calls[1][1]["record_gitops_markers"] is False


def test_no_second_mco_teardown_implementation_remains():
    source = Path("modules/finalization.py").read_text(encoding="utf-8")
    assert "multiclusterobservabilities" not in source, "finalization must not re-implement MCO teardown"
```

- [ ] **Step 6:** `python -m pytest tests/test_decommission.py tests/test_finalization.py
  tests/test_post_activation.py tests/test_constants_parity.py -q`; commit
  `refactor: give Decommission the single MCO teardown algorithm`.

### Task C4: Collection MCO teardown parity

**Files:** Modify `roles/decommission/tasks/delete_observability.yml`; modify
`tests/unit/test_decommission_role_contracts.py`; modify `tests/scenario/`.

**Intended behavior.** Route the MCO delete through `acm_uid_guarded_delete` with the explicit
primary kubeconfig and context, the recorded `expected_uid`, and bounded wait values. Load the
durable record before the initial inventory classification and implement the same phase table and
the same no-record-only clean skip as Python. Read inventory through `acm_k8s_read_outcome` and
fail closed on any non-`ok`. Add the scoped selector to the MCO pod wait. No name-only
`kubernetes.core.k8s state: absent` task for MCO remains.

**Failure behavior.** No `failed_when: false`, no `ignore_errors`, and no `default([])` on any
provenance, ownership, wait, or final-verification path.

**Native check-mode safety.** The role passes canonical
`resource_name: multiclusterobservabilities` into every discovery-backed read. In check mode it
runs only reads/prediction, never invokes a phase or operational-data writer, and the guarded
delete module stops before DELETE. The per-substep result is not `completed`, `refused`, or
`failed`; actual `changed` is false and MCO `would_change` reflects fresh source/gate state.

**Exact task structure.** `delete_observability.yml` becomes this sequence, and the contract tests
below assert each element of it:

| # | Task | Module / action | Guard | Publishes |
| --- | --- | --- | --- | --- |
| 1 | Load the durable MCO teardown record | `checkpoint` fact read through the action plugin's flattened facts | none (read-only) | `acm_switchover_mco_record` |
| 2 | Read the source MCO inventory | `acm_k8s_read_outcome` with `read_mode: list`, `resource_name: multiclusterobservabilities` | none (read-only) | `acm_switchover_mco_read` |
| 3 | Fail closed on a non-`ok` read | `ansible.builtin.fail` | `when: acm_switchover_mco_read.read_status not in ["ok", "kind_not_served"]` | — |
| 4 | Destination gate (Task C5) | gate task file include | `when: acm_switchover_hubs.secondary is defined` | `acm_switchover_observability_gate` |
| 5 | Record `delete_started` with `expected_uid` and the `cr` revision | `checkpoint` operational-data writer | `when: not ansible_check_mode and acm_switchover_execution.mode == "execute"` | — |
| 6 | Guarded delete | `tomazb.acm_switchover.acm_uid_guarded_delete` with `expected_uid`, explicit `kubeconfig`/`context`, bounded `request_timeout`/`wait_timeout`/`wait_sleep`, `no_log: true` | none — the module implements native check mode itself and stops before DELETE | `acm_switchover_mco_delete` |
| 7 | Record `cr_absent`, then `drain_pending` | `checkpoint` writers | same guard as 5 | — |
| 8 | Selector-scoped bounded drain wait | `acm_k8s_read_outcome` list of Pods with `resource_name: pods` and the observability label selector, inside a bounded `until` with **no** `failed_when: false` and **no** `default([])` | none (read-only) | `acm_switchover_mco_pods` |
| 9 | Record `drained`, re-prove, then `completed` with the §10.2.1 proof keys | `checkpoint` writers | same guard as 5 | — |
| 10 | Publish the substep outcome and its actual/predicted change | `ansible.builtin.set_fact` | none | `acm_switchover_decommission_outcomes.observability`, and the substep's `changed`/`would_change` inputs to the B4 aggregation |

Task 6's `changed` is the module's own `changed`, which is true only after an accepted
intended-UID DELETE plus the bounded completion and final-absence contract (Task C2). In check mode
the module returns `changed: false` with `would_change` set, so task 10 publishes prediction only.

- [ ] **Step 1: Write the failing role-contract tests**

```python
def test_mco_delete_goes_through_the_guarded_module():
    tasks = decommission_task_files["observability"]
    assert any(t.get("module") == "tomazb.acm_switchover.acm_uid_guarded_delete" for t in tasks)
    assert not [t for t in tasks if t.get("module") == "kubernetes.core.k8s"
                and t.get("args", {}).get("state") == "absent"]


def test_mco_reads_supply_the_canonical_resource_name():
    for task in read_outcome_tasks(decommission_task_files["observability"]):
        assert task["args"]["resource_name"] in {"multiclusterobservabilities", "pods", "namespaces"}


def test_mco_drain_wait_is_selector_scoped_and_absorbs_no_failure():
    wait = task_named(decommission_task_files["observability"], "Wait for observability pods to terminate")
    assert "observability.open-cluster-management.io/name=observability" in str(wait)
    assert "failed_when" not in wait or wait["failed_when"] is not False
    assert "ignore_errors" not in wait
    assert "default([])" not in str(wait.get("until", ""))


def test_every_mco_checkpoint_writer_is_check_mode_guarded():
    for task in checkpoint_writer_tasks({"observability": decommission_task_files["observability"]}):
        assert "not ansible_check_mode" in str(task.get("when", ""))


def test_check_mode_issues_no_delete_and_writes_nothing():
    result = run_decommission_role(check_mode=True, mco_present=True)
    assert [c for c in result["delete_calls"] if c["changed"]] == []
    checkpoint = result["checkpoint"]
    assert checkpoint["operational_data"] == checkpoint["before_operational_data"]
    assert result["acm_switchover_decommission_result"]["changed"] is False
    assert not [t for t in result["tasks"] if t["changed"]]
    assert result["acm_switchover_decommission_result"]["substeps"].get("observability") not in {
        "completed", "refused", "failed"
    }


@pytest.mark.parametrize(
    "mco_present, expected_would_change",
    [(True, True), (False, False)],
)
def test_check_mode_prediction_is_accurate_and_separate(mco_present, expected_would_change):
    result = run_decommission_role(check_mode=True, mco_present=mco_present)
    summary = result["acm_switchover_decommission_result"]
    assert summary["changed"] is False
    assert summary["would_change"] is expected_would_change


def test_check_mode_with_an_unverifiable_mco_fails_rather_than_predicting():
    result = run_decommission_role(check_mode=True, mco_present="unverifiable")
    assert result["acm_switchover_decommission_result"]["status"] == "fail"
    assert result["delete_calls"] == []


def test_execute_mode_delete_carries_the_recorded_expected_uid():
    result = run_decommission_role(mco_present=True)
    assert result["delete_calls"][0]["expected_uid"] == "mco-uid-1"
    assert result["delete_calls"][0]["kubeconfig"] and result["delete_calls"][0]["context"]


def test_completed_record_carries_the_exact_proof_keys():
    result = run_decommission_role(mco_present=True)
    records = result["checkpoint"]["operational_data"]["decommission_teardown_records"]
    record = next(iter(records.values()))
    assert record["phase"] == "completed"
    assert set(record["resource_versions"]) == {"cr", "pods"}
```

- [ ] **Step 2: Run and observe the expected failure**

```bash
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_decommission_role_contracts.py -q
```

Expected: FAIL — `delete_observability.yml:16-29` is still a name-only
`kubernetes.core.k8s state: absent` task, so the guarded-module assertion fails; there is no
`resource_name` on any read; the drain wait is namespace-wide; and no checkpoint writer exists.

- [ ] **Step 3: Implement the task file**

Rewrite `roles/decommission/tasks/delete_observability.yml` as the ten-task sequence above,
deleting the name-only `state: absent` task. Add the acknowledgement variable default in
`roles/decommission/defaults/main.yml` (Task C5) and the mirrored
`OBSERVABILITY_POD_LABEL_SELECTOR` constant usage from `module_utils/constants.py`.

- [ ] **Step 4: Rerun the unit and scenario lanes**

```bash
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_decommission_role_contracts.py -q
export ANSIBLE_COLLECTIONS_PATH="$(pwd):${HOME}/.ansible/collections"
PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/scenario/ -q
```

- [ ] **Step 5: Simplify, rerun, and commit**

The file must contain exactly one guarded-delete task and one drain wait; no branch may duplicate
the phase writers. Rerun Step 4, then commit
`feat: route collection MCO teardown through the guarded delete`.

### Task C5: Destination-observability gate

**Files:** Modify `modules/decommission.py`; modify `modules/finalization.py`; modify
`acm_switchover.py`; modify `lib/validation.py`; modify `lib/constants.py` and
`plugins/module_utils/constants.py`; modify
`roles/decommission/tasks/delete_observability.yml`; create
`roles/decommission/tasks/destination_observability_gate.yml`; modify
`roles/decommission/defaults/main.yml`; modify tests on both sides plus
`tests/test_validation.py` and `tests/test_validation_parity.py`.

**Purpose:** Close R4-C5 and July criterion 4.

**Interfaces produced:**

```python
# lib/decommission_outcome.py
class ObservabilityGateDecision(Enum):
    PROCEED = "proceed"                 # destination positively present, or ack against proven absence
    NOT_APPLICABLE = "not_applicable"   # source positively absent: there is nothing to delete
    BLOCKED = "blocked"

@dataclass(frozen=True)
class ObservabilityGateResult:
    decision: ObservabilityGateDecision
    reason: str | None = None           # a stable mirrored reason code; required when BLOCKED

# modules/decommission.py
class Decommission:
    def destination_observability_gate(self) -> ObservabilityGateResult: ...
```

The gate is a named method on `Decommission`, called by `_teardown_resource` immediately before the
MCO deletion substep and by nothing else. It takes no arguments and reads nothing from state: every
input is a fresh live read. Mirrored reason codes, added to both constants modules and to
`CONSTANT_PAIRS` in this task:

```python
GATE_REASON_DESTINATION_ABSENT = "destination_observability_absent"
GATE_REASON_DESTINATION_UNVERIFIABLE = "destination_observability_unverifiable"
GATE_REASON_SOURCE_UNVERIFIABLE = "source_observability_unverifiable"
GATE_REASON_SOURCE_AMBIGUOUS = "source_observability_ambiguous"
GATE_REASON_ACK_NOT_APPLICABLE = "acknowledgement_not_applicable"
```

**Intended behavior.** Immediately before the source MCO deletion substep — not at
`_decommission_old_hub` entry, and with no intervening mutation:

1. **Fresh source read.** Strict MCO CR read plus observability-namespace read on the source hub.
   Positively absent both → `NOT_APPLICABLE`, and the substep is `PRECONDITION_NOOP`.
   Positively present → continue. Any `error` → `BLOCKED` with `GATE_REASON_SOURCE_UNVERIFIABLE`; a
   mixed state such as an absent CRD with a present namespace → `BLOCKED` with
   `GATE_REASON_SOURCE_AMBIGUOUS`.
2. **Fresh destination read** through the secondary client: MCO CR strict list plus
   observability-namespace presence. The source clean-skip rule is **not** reused here; on the
   destination, missing discovery, missing CRD, missing CR, or missing namespace all block.
3. **Two distinguished blocking reasons.** `GATE_REASON_DESTINATION_ABSENT` (positively absent)
   versus `GATE_REASON_DESTINATION_UNVERIFIABLE`. They are never conflated in the message or in the
   result.
4. `--acknowledge-observability-not-migrated` turns a `GATE_REASON_DESTINATION_ABSENT` block into
   `PROCEED` and nothing else. It never overrides `GATE_REASON_DESTINATION_UNVERIFIABLE`, and when
   the gate would pass anyway the flag is rejected with `GATE_REASON_ACK_NOT_APPLICABLE`.

**Never persisted.** The gate result is recomputed fresh on every run, including every resume.

Python namespace reads in both steps use PR A's live `get_namespace_strict`; no cached preflight
fact proves absence. Collection uses a fresh named Namespace GET with `resource_name: namespaces`.
Every MCO discovery-backed call passes the exact canonical `resource_name:
multiclusterobservabilities`.

**Standalone decommission** has no destination client: `destination_observability_gate` is not
called, and the substep proceeds as today.

**RBAC implications.** Destination reads: `multiclusterobservabilities get`/`list` and
`namespaces get` through the secondary client. Verified already granted by the baseline operator
`ClusterRole` (`deploy/rbac/clusterrole.yaml`: `namespaces get,list`;
`multiclusterobservabilities get,list,delete`), which the secondary hub already carries for
preflight. Recorded in §14 with that evidence; no new grant is required for the destination side.

- [ ] **Step 1: Write the failing Python tests**

The `integrated` fixture is defined in this step in `tests/test_decommission.py`: a `Decommission`
constructed with a mocked primary client, a mocked **secondary** client, a real `RunRecord` over a
`tmp_path` `StateManager`, and `acknowledge_observability_not_migrated=False`. It exposes
`integrated.source(...)` and `integrated.destination(...)` helpers that program the two clients'
strict-read return values from `StrictReadOutcome` factories. `standalone` is the same fixture
without a secondary client.

```python
class TestDestinationObservabilityGate:
    def test_destination_present_passes_without_the_flag(self, integrated):
        integrated.source(mco=StrictReadOutcome.from_items([{"metadata": {"uid": "u"}}]),
                          namespace=StrictReadOutcome.from_resource({"metadata": {"name": "ns"}}))
        integrated.destination(mco=StrictReadOutcome.from_items([{"metadata": {"uid": "d"}}]),
                               namespace=StrictReadOutcome.from_resource({"metadata": {"name": "ns"}}))
        assert integrated.destination_observability_gate().decision is ObservabilityGateDecision.PROCEED

    def test_destination_positively_absent_blocks_without_the_flag(self, integrated):
        integrated.source(mco=StrictReadOutcome.from_items([{"metadata": {"uid": "u"}}]),
                          namespace=StrictReadOutcome.from_resource({"metadata": {"name": "ns"}}))
        integrated.destination(mco=StrictReadOutcome.crd_absent("kind_not_served"),
                               namespace=StrictReadOutcome.namespace_absent("namespace_not_found"))
        result = integrated.destination_observability_gate()
        assert result.decision is ObservabilityGateDecision.BLOCKED
        assert result.reason == GATE_REASON_DESTINATION_ABSENT

    def test_destination_positively_absent_proceeds_with_the_flag(self, integrated):
        integrated.acknowledge_observability_not_migrated = True
        integrated.source(mco=StrictReadOutcome.from_items([{"metadata": {"uid": "u"}}]),
                          namespace=StrictReadOutcome.from_resource({"metadata": {"name": "ns"}}))
        integrated.destination(mco=StrictReadOutcome.crd_absent("kind_not_served"),
                               namespace=StrictReadOutcome.namespace_absent("namespace_not_found"))
        assert integrated.destination_observability_gate().decision is ObservabilityGateDecision.PROCEED

    def test_destination_unverifiable_blocks_even_with_the_flag(self, integrated):
        integrated.acknowledge_observability_not_migrated = True
        integrated.source(mco=StrictReadOutcome.from_items([{"metadata": {"uid": "u"}}]),
                          namespace=StrictReadOutcome.from_resource({"metadata": {"name": "ns"}}))
        integrated.destination(mco=StrictReadOutcome.error("read_failed"),
                               namespace=StrictReadOutcome.error("read_failed"))
        result = integrated.destination_observability_gate()
        assert result.decision is ObservabilityGateDecision.BLOCKED
        assert result.reason == GATE_REASON_DESTINATION_UNVERIFIABLE

    def test_the_two_blocking_reasons_are_distinguishable(self, integrated):
        integrated.source(mco=StrictReadOutcome.from_items([{"metadata": {"uid": "u"}}]),
                          namespace=StrictReadOutcome.from_resource({"metadata": {"name": "ns"}}))
        integrated.destination(mco=StrictReadOutcome.crd_absent("kind_not_served"),
                               namespace=StrictReadOutcome.namespace_absent("namespace_not_found"))
        absent = integrated.destination_observability_gate().reason
        integrated.destination(mco=StrictReadOutcome.error("read_failed"),
                               namespace=StrictReadOutcome.error("read_failed"))
        unverifiable = integrated.destination_observability_gate().reason
        assert absent != unverifiable

    def test_flag_is_rejected_when_the_gate_would_pass_anyway(self, integrated):
        integrated.acknowledge_observability_not_migrated = True
        integrated.source(mco=StrictReadOutcome.from_items([{"metadata": {"uid": "u"}}]),
                          namespace=StrictReadOutcome.from_resource({"metadata": {"name": "ns"}}))
        integrated.destination(mco=StrictReadOutcome.from_items([{"metadata": {"uid": "d"}}]),
                               namespace=StrictReadOutcome.from_resource({"metadata": {"name": "ns"}}))
        result = integrated.destination_observability_gate()
        assert result.decision is ObservabilityGateDecision.BLOCKED
        assert result.reason == GATE_REASON_ACK_NOT_APPLICABLE

    def test_source_is_re_read_fresh_and_the_preflight_boolean_is_not_consulted(self, integrated):
        integrated.run_record.record_hub_facts(HubFacts(primary_has_observability=False))
        integrated.source(mco=StrictReadOutcome.from_items([{"metadata": {"uid": "u"}}]),
                          namespace=StrictReadOutcome.from_resource({"metadata": {"name": "ns"}}))
        integrated.destination(mco=StrictReadOutcome.from_items([{"metadata": {"uid": "d"}}]),
                               namespace=StrictReadOutcome.from_resource({"metadata": {"name": "ns"}}))
        assert integrated.destination_observability_gate().decision is ObservabilityGateDecision.PROCEED
        assert integrated.primary_client.get_namespace_strict.called

    def test_mixed_source_state_absent_crd_present_namespace_blocks(self, integrated):
        integrated.source(mco=StrictReadOutcome.crd_absent("kind_not_served"),
                          namespace=StrictReadOutcome.from_resource({"metadata": {"name": "ns"}}))
        result = integrated.destination_observability_gate()
        assert result.decision is ObservabilityGateDecision.BLOCKED
        assert result.reason == GATE_REASON_SOURCE_AMBIGUOUS

    def test_source_error_never_reads_as_nothing_to_delete(self, integrated):
        integrated.source(mco=StrictReadOutcome.error("read_failed"),
                          namespace=StrictReadOutcome.error("read_failed"))
        result = integrated.destination_observability_gate()
        assert result.decision is ObservabilityGateDecision.BLOCKED
        assert result.reason == GATE_REASON_SOURCE_UNVERIFIABLE

    def test_source_positively_absent_is_not_applicable(self, integrated):
        integrated.source(mco=StrictReadOutcome.crd_absent("kind_not_served"),
                          namespace=StrictReadOutcome.namespace_absent("namespace_not_found"))
        assert (integrated.destination_observability_gate().decision
                is ObservabilityGateDecision.NOT_APPLICABLE)

    def test_gate_result_is_not_persisted(self, integrated, state_manager):
        integrated.source(mco=StrictReadOutcome.from_items([{"metadata": {"uid": "u"}}]),
                          namespace=StrictReadOutcome.from_resource({"metadata": {"name": "ns"}}))
        integrated.destination(mco=StrictReadOutcome.from_items([{"metadata": {"uid": "d"}}]),
                               namespace=StrictReadOutcome.from_resource({"metadata": {"name": "ns"}}))
        integrated.destination_observability_gate()
        snapshot = json.dumps(state_manager.capture_state_snapshot())
        assert "observability_gate" not in snapshot
        assert "destination_observability" not in snapshot

    def test_resume_reruns_the_gate_against_fresh_reads(self, integrated):
        integrated.source(mco=StrictReadOutcome.from_items([{"metadata": {"uid": "u"}}]),
                          namespace=StrictReadOutcome.from_resource({"metadata": {"name": "ns"}}))
        integrated.destination(mco=StrictReadOutcome.from_items([{"metadata": {"uid": "d"}}]),
                               namespace=StrictReadOutcome.from_resource({"metadata": {"name": "ns"}}))
        integrated.destination_observability_gate()
        calls_after_first = integrated.secondary_client.list_custom_resources_strict.call_count
        integrated.destination_observability_gate()
        assert integrated.secondary_client.list_custom_resources_strict.call_count > calls_after_first

    def test_standalone_decommission_has_no_destination_gate(self, standalone):
        standalone.source(mco=StrictReadOutcome.from_items([{"metadata": {"uid": "u"}}]),
                          namespace=StrictReadOutcome.from_resource({"metadata": {"name": "ns"}}))
        standalone.teardown_observability()
        assert standalone.secondary_client is None
```

Add the mirrored collection tests through the B4.1 contract, one per case above, driven by
`run_decommission_role(destination_mco=..., destination_namespace=...,
acknowledge_observability_not_migrated=...)` and asserting `result["gate"]["decision"]`,
`result["gate"]["reason"]`, and — for every blocked case — `result["delete_calls"] == []`.

- [ ] **Step 2: Run and observe the expected failure**

```bash
python -m pytest tests/test_decommission.py -q -k DestinationObservabilityGate
```

Expected: FAIL — `AttributeError: 'Decommission' object has no attribute
'destination_observability_gate'`, and `ImportError` for `ObservabilityGateDecision`.

- [ ] **Step 3: Implement**

Add `ObservabilityGateDecision`, `ObservabilityGateResult`, and the five reason-code constants;
implement `destination_observability_gate()` as the four-step algorithm above; call it from
`_teardown_resource` for the MCO spec only, immediately before the `delete_started` write, and map
`NOT_APPLICABLE` to `SubstepExecution(PRECONDITION_NOOP, changed=False)` and `BLOCKED` to a raised
`SwitchoverError` naming the reason code. Add the mirrored collection implementation in
`roles/decommission/tasks/destination_observability_gate.yml`, included by
`delete_observability.yml` at position 4 of the Task C4 table.

- [ ] **Step 4: Rerun both sides**

```bash
python -m pytest tests/test_decommission.py tests/test_finalization.py -q
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/unit/ -q
```

- [ ] **Step 5: CLI validation surface.** Per the repository's CLI validation guidance, adding a
  flag means updating `InputValidator` and its tests together with three documents. Add to
  `InputValidator.validate_all_cli_args`: `--acknowledge-observability-not-migrated` is valid only
  for an integrated switchover with `--old-hub-action decommission`, and is rejected with
  `--decommission` (standalone has no destination) and with `--validate-only`. Add the mirrored
  collection variable validation. Then update
  [`docs/reference/validation-rules.md`](../reference/validation-rules.md),
  [`docs/operations/usage.md`](../operations/usage.md), and
  [`docs/operations/quickref.md`](../operations/quickref.md).

```bash
python -m pytest tests/test_validation.py tests/test_validation_parity.py \
  tests/test_documentation_guardrails.py -q
```

- [ ] **Step 6:** commit `feat: gate source observability deletion on destination readiness`.

### Task C6: PR C RBAC cross-surface change

**Purpose:** Land the permissions for the calls PR C makes live, in PR C.

**New API operations introduced by PR C**

| Form factor | Caller | Hub | Namespace | Group | Resource | Verb | Already granted |
| --- | --- | --- | --- | --- | --- | --- | --- |
| both | MCO teardown strict named GET and final absence proof | source | cluster-scoped | `observability.open-cluster-management.io` | `multiclusterobservabilities` | `get` | operator `ClusterRole` yes; **decommission extension no** |
| both | MCO drain strict Pod list | source | `open-cluster-management-observability` | core | `pods` | `list` | yes |
| both | fixed-namespace absence proof | source | cluster-scoped | core | `namespaces` | `get` | yes |
| both | destination gate MCO read | destination | cluster-scoped | `observability.open-cluster-management.io` | `multiclusterobservabilities` | `get`, `list` | yes (baseline operator `ClusterRole`) |
| both | destination gate namespace read | destination | cluster-scoped | core | `namespaces` | `get` | yes (baseline operator `ClusterRole`) |

The one genuinely missing grant is `multiclusterobservabilities get` on the **standalone
decommission** surface: `deploy/rbac/extensions/decommission/clusterrole.yaml` grants `delete`
only, and a standalone run does not carry the baseline operator `ClusterRole`. A scoped `list` is
not substituted for the named-GET contract.

- [ ] **Step 1:** add `get` to `multiclusterobservabilities` in
  `lib/rbac_validator.py::DECOMMISSION_CLUSTER_PERMISSIONS` and in
  `plugins/modules/acm_rbac_validate.py::DECOMMISSION_CLUSTER_PERMISSIONS`.
- [ ] **Step 2:** add `get` to the `multiclusterobservabilities` rule in
  `deploy/rbac/extensions/decommission/clusterrole.yaml` and in the collection-bundled copy
  `roles/rbac_bootstrap/files/deploy/rbac/extensions/decommission/clusterrole.yaml`.
- [ ] **Step 3:** mirror it in `deploy/helm/acm-switchover-rbac/templates/clusterrole.yaml`.
- [ ] **Step 4:** update all four current RBAC authorities:
  [`docs/deployment/rbac-requirements.md`](../deployment/rbac-requirements.md),
  [`docs/deployment/rbac-deployment.md`](../deployment/rbac-deployment.md),
  [`docs/development/rbac-implementation.md`](../development/rbac-implementation.md), and
  [`docs/deployment/rbac-live-certification.md`](../deployment/rbac-live-certification.md).
  The live-certification document's required SelfSubjectAccessReview/read/delete inventory must
  include the new named GET, but editing that inventory is not evidence that a live certification
  run occurred; no live run is authorized by this plan.
- [ ] **Step 5:** add a negative authorization test that denies `multiclusterobservabilities get`
  and proves the MCO teardown blocks **before** any DELETE, with sanitized output.
- [ ] **Step 6:** run the complete RBAC gate:

```bash
python -m pytest tests/test_rbac_validator.py tests/test_rbac_collection_parity.py \
  tests/test_rbac_integration.py tests/test_documentation_guardrails.py -q
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/unit/ -q -k rbac
```

- [ ] **Step 7:** commit `feat: grant the MCO reads decommission completion requires`.

### Task C7: PR C verification

- [ ] Targeted: `python -m pytest tests/test_decommission.py tests/test_finalization.py
  tests/test_kube_client.py tests/test_teardown_record.py tests/test_validation.py
  tests/test_rbac_validator.py tests/test_rbac_collection_parity.py
  tests/test_constants_parity.py -q`
- [ ] Root surface, collection surfaces 3 through 7 in both lanes, quality and security gates,
  scope and protected-file check — the Task A6 command set.
- [ ] `CHANGELOG.md` `## [Unreleased]`: UID-preconditioned MCO deletion with a completion proof;
  the destination-observability gate and its flag; the consolidated MCO teardown.
- [ ] Publish the full-reset warning now, in C: update
  [`docs/operations/usage.md`](../operations/usage.md) and
  [`docs/development/architecture.md`](../development/architecture.md) to state that Python
  `--reset-state` and collection full `checkpoint.reset` destroy teardown records, so resetting
  after destructive work can erase the remembered drain obligation and allow a later clean skip;
  `reset_from` retains and revalidates the records. C is the first PR that can leave a destructive
  partial record, so this warning may not wait for F.
- [ ] Update the root and collection READMEs, CLI quick reference/validation rules, relevant
  Mermaid teardown flow, coexistence/CLI migration and variable-reference surfaces for the new
  acknowledgement flag, check-mode semantics, destination gate, and MCO phase machine in this PR.
  Update the [parity matrix](../ansible-collection/parity-matrix.md) decommission row and the
  [behavior map](../ansible-collection/behavior-map.md) `modules/decommission.py` row for the
  guarded-delete and durable-phase boundaries. Update all four RBAC documents in Task C6. No
  document made false by C is deferred to F.
- [ ] Simplification gate, then open PR C.

## 11B. PR D — ManagedCluster teardown

**Branch:** `feature/r4-03-managedcluster-teardown`

**Purpose.** Apply the strict inventory and the guarded phase machine to ManagedClusters. Closes
R4-C4's `_delete_managed_clusters` blindness and R4-C3 for this resource family.

**Prerequisites:** PR A, PR B, PR C merged.

**Files:** Modify `modules/decommission.py`, `lib/kube_client.py` (`list_managed_clusters_strict`),
`roles/decommission/tasks/delete_managed_clusters.yml`, `lib/rbac_validator.py`,
`plugins/modules/acm_rbac_validate.py`, `deploy/rbac/extensions/decommission/clusterrole.yaml`
and its bundled copy, `deploy/helm/acm-switchover-rbac/templates/clusterrole.yaml`, the RBAC
docs, `tests/test_decommission.py`, and the collection role-contract and scenario tests.

**Intended behavior.**

1. Inventory through the strict list. A missing `cluster.open-cluster-management.io` discovery is
   fatal — "cannot verify inventory" — and a genuine proven-empty list is a clean
   `PRECONDITION_NOOP`.
2. The Hive `preserveOnDelete` check runs before deletion exactly as today. Its behavior is
   **retained, not broadened**: this PR does not redesign the separate authorization TOCTOU, which
   is an explicit non-goal. The one change is that the ClusterDeployment inventory read also goes
   through the strict list, so a missing Hive CRD no longer reads as "no ClusterDeployments".
3. Per cluster: strict named GET → record `expected_uid` forced durable → preconditioned DELETE →
   bounded strict absence poll → final live absence proof → `completed`. One record per name, so
   ManagedClusters retain per-name records.
4. Aggregate failures into one `SwitchoverError` listing the survivors.
5. `local-cluster` continues to be skipped.
6. `Decommission.teardown_managed_clusters(self) -> SubstepExecution` returns the one execution
   type from Task B3.1. Its `changed` is `True` when **any** ManagedCluster in this invocation had a
   UID-preconditioned DELETE accepted, and `False` for a proven-empty inventory, for a resume in
   which every remaining cluster only needed its final absence proof, and for every dry-run or
   check-mode run. Each cluster's `completed` record carries the §10.2.1 key set for a family
   without a drain scope: `{cr}` only.

Collection callers pass canonical `resource_name: managedclusters` and
`resource_name: clusterdeployments`; neither caller nor the read module synthesizes a plural from
the Kind.

**Failure behavior.** A same-name different-UID ManagedCluster is fatal and is left intact. Any
`error` outcome on inventory, per-cluster GET, or the absence poll is fatal and never absence.

**State/checkpoint implications.** One `TeardownRecord` per ManagedCluster name; the same phase
table; the same forced-durable ordering.

**Dry-run and check mode.** D ships its complete safety contract. Python branches before every
per-cluster teardown-record write and guarded-delete call; collection skips every phase and
operational-data writer and the guarded-delete module stops before DELETE. Both perform fresh
strict ManagedCluster and ClusterDeployment inventories, report no substep as
completed/refused/failed, and report actual `changed: false`; accurate per-target prediction is
aggregated only into `would_change`. Present targets predict true, proven empty predicts false,
and unverifiable inventory fails rather than predicts. A live run after preview uses no preview
data and repeats every authoritative read.

**Parity implications.** The collection routes each delete through `acm_uid_guarded_delete` and
reads inventory through `acm_k8s_read_outcome`; no name-only `state: absent` remains.

**RBAC implications.**

| Form factor | Hub | Group | Resource | Verb | Already granted |
| --- | --- | --- | --- | --- | --- |
| both | source | `cluster.open-cluster-management.io` | `managedclusters` | `get` | operator `ClusterRole` yes; **decommission extension no** |
| both | source | `hive.openshift.io` | `clusterdeployments` | `list` | yes |

**Tests to write first:** strict inventory discovery failure is fatal; a proven-empty inventory is
a clean skip; a later-page failure during ManagedCluster listing is fatal; a missing Hive CRD is
fatal rather than "no ClusterDeployments"; `preserveOnDelete=false` still blocks with the existing
message; an ambiguous ClusterDeployment relationship still blocks with the existing message; each
delete carries the recorded UID; a same-name replacement between record and DELETE is fatal and
survives; the survivor list names every ManagedCluster that did not reach `completed`;
`local-cluster` is never deleted; a denied `managedclusters get` blocks before any DELETE; a
proven-empty inventory returns `SubstepExecution(PRECONDITION_NOOP, changed=False)`; a run that
accepted at least one DELETE and proved completion returns `changed=True`; a resume whose clusters
are already absent and only need their final proof returns `changed=False`; and each `completed`
record carries exactly `{cr}` in `resource_versions`. Add
Python dry-run and Collection native-check-mode cases for present, empty, and unverifiable
inventories; assert no DELETE primitive, no teardown-record/operational-data/phase write, no
completed/refused/failed outcome, no task or summary actual change, accurate separate
`would_change`, and a subsequent live run's fresh reads.

**Expected failing state before implementation:** the current base fixture
(`tests/test_decommission.py:25-31`) returns `[]` from the list mocks, so the discovery-failure and
later-page-failure tests pass vacuously today and fail once they assert a strict outcome; the
UID-recording tests fail with `AttributeError` on the missing record API usage.

**Steps:** the same red-green-refactor cycle as Task C3 — write the failing tests, run them and
observe the named failure, implement, rerun the targeted tests, simplify, rerun, then run the PR
gate set (Task A6 command set plus the full RBAC gate from Task C6 step 6).

**Commit boundary:** two commits — `feat: make ManagedCluster teardown strict and UID-guarded`,
and `feat: grant the ManagedCluster read decommission completion requires`.

**Documentation in this PR:** `CHANGELOG.md` `## [Unreleased]`; root and Collection README and
`docs/operations/usage.md` wherever their decommission inventory/check-mode descriptions become
false; architecture/Mermaid, parity matrix, behavior map, and coexistence/CLI migration surfaces
where ManagedCluster behavior is described; and all four RBAC authorities:
`docs/deployment/rbac-requirements.md`, `docs/deployment/rbac-deployment.md`,
`docs/development/rbac-implementation.md`, and
`docs/deployment/rbac-live-certification.md`. Update the latter's required
SelfSubjectAccessReview/read inventory without claiming a live certification run. No D behavior
documentation is deferred to F.

## 11C. PR E — MCH operator identity and completion

**Branch:** `feature/r4-03-mch-identity`

**Purpose.** Close R4-C1 and R4-C6: bind MCH drain exclusion to a complete controller-owner chain
ending at a durably recorded operator Deployment UID, and prove MCH completion. This is the
largest and highest-risk surface in the slice, which is why it is its own PR.

**Prerequisites:** PR A, PR B, PR C merged. Independent of PR D.

**Files:** Create `modules/decommission_identity.py`; modify `modules/decommission.py`; consume
PR A's `get_namespace_strict`, `list_pods_strict`, `get_deployment_strict`, and
`get_replicaset_strict`; modify
`lib/constants.py` and `plugins/module_utils/constants.py`; create
`plugins/modules/acm_pod_owner_classify.py` and
`plugins/module_utils/pod_owner_classify.py`; modify
`roles/decommission/tasks/delete_multiclusterhub.yml`; modify `lib/rbac_validator.py`,
`plugins/modules/acm_rbac_validate.py`, `deploy/rbac/role.yaml` and its bundled copy,
`deploy/helm/acm-switchover-rbac/templates/role.yaml`,
`deploy/rbac/extensions/decommission/clusterrole.yaml` and its bundled copy, the RBAC docs;
create `tests/test_decommission_identity.py`; modify `tests/test_decommission.py`,
`tests/test_constants_parity.py`,
`tests/unit/test_ansible_resilience_contracts.py`, and
`tests/unit/test_decommission_role_contracts.py`.

### 11C.1 Decision record for PR E

| Decision | Value | Basis |
| --- | --- | --- |
| Classifier placement, Python | `modules/decommission_identity.py`, imported by `modules/decommission.py` | Amendment §10 item 3 — the helper lives with the teardown owner, and `lib/kube_client.py` stays a transport layer |
| Classifier placement, collection | a collection-owned module plus `module_utils` | July §6 — Jinja name filtering must never be the safety decision |
| Strict typed reads | `KubeClient.get_deployment_strict`, `get_replicaset_strict` | Transport belongs to the read layer; classification does not |
| Prefix constant | retained as a supplementary diagnostic only, mirrored and parity-tested | Amendment §10 item 1 and criterion A6 |
| Reason codes | `operator_owned`, `drain_blocking`, `operator_identity_unavailable`, `operator_identity_inconsistent`, mirrored constants | July §5; amendment §12 |
| Memoization | per pass, keyed by exact `(namespace, kind, name, uid)`; never across passes | July §1a |

### 11C.2 Behavior

**Provenance capture, before the MCH DELETE.** Through the explicit source client: strictly list
`operators.coreos.com/v1alpha1` `ClusterServiceVersion` in the exact ACM namespace; a candidate is
only a `Succeeded` CSV whose `spec.customresourcedefinitions.owned[].name` contains exactly
`multiclusterhubs.operator.open-cluster-management.io`; require exactly one candidate and exactly
one declared install Deployment; strictly GET that `apps/v1` Deployment and require a non-empty
`metadata.uid`. Persist the July §1a `operator_deployment` shape in the MCH teardown record with
one forced-durable write **before** DELETE, or persist a complete
`operator_identity_unavailable` record with a stable reason code. Exactly one of the two exists;
both, neither, or a partial shape is malformed and fails closed. If the durable write fails, the
DELETE is not issued.

**Per-pass classification, on every drain pass including the final verification.** Strictly list
all `v1` Pods in the ACM namespace under the bounded drain deadline. A Pod is excluded only when
it has exactly one controller owner reference with `apiVersion: apps/v1`, `kind: ReplicaSet`, and
non-empty name and UID; the live ReplicaSet's UID equals that reference; the ReplicaSet has
exactly one controller owner reference to an `apps/v1` Deployment; and that Deployment's live UID
equals both the ReplicaSet's reference and the durably recorded operator Deployment UID. The
recorded Deployment is re-read even when no Pod is proposed for exclusion. Rolling-update
ReplicaSets are accepted only when every chain resolves to the same recorded Deployment UID.

**Namespace-absence entailment, exactly as approved.** When the fixed ACM namespace is
**positively** absent, the recorded namespaced Deployment cannot exist; that absence is entailed,
is not a recovery-required inconsistency, and the namespace-absence proof stands in for both the
pod-empty predicate and the Deployment re-read. An unreadable or ambiguous namespace state never
triggers this exception and records `recovery_required`.

That positive proof must come from PR A's fresh `get_namespace_strict(ACM_NAMESPACE)` producer.
A Pod LIST 404 is not sufficient by itself; `list_pods_strict` performs the fresh Namespace GET
before it can return `NAMESPACE_ABSENT`. Collection composes the same named Namespace GET. Every
collection discovery-backed read supplies its canonical `resource_name` explicitly, including
`multiclusterhubs`, `clusterserviceversions`, and `pods`.

**Exact written shapes.** The `operator_deployment` and `operator_identity_unavailable` payloads
PR E writes are exactly the schemas PR B already validates (§10.2.2 and §10.2.3), field for field,
including `discovery_method: olm_csv_owned_mch_crd_install_deployment_v1`, the
`csv.owned_crd` equality, and the `mch_teardown_key` / `mch_expected_uid` equalities with the
enclosing record. `reason` is drawn only from `OPERATOR_IDENTITY_UNAVAILABLE_REASONS`, and PR E adds
one test per enumerated reason proving the writer emits a payload the PR B validator accepts, plus
the reverse: every malformed payload from the §10.2.2/§10.2.3 matrices is rejected before any
DELETE.

**Execution interface.** `Decommission.teardown_multiclusterhub(self) -> SubstepExecution`, the one
type from Task B3.1. `changed` is `True` only when this invocation's UID-preconditioned MCH DELETE
was accepted. The `completed` record carries `{cr, pods}` when the drain was proven by a successful
Pod LIST and `{cr, namespace_absent}` when positive ACM-namespace absence entailed the pod-empty
predicate (§10.2.1).

**Pod member shape.** `list_pods_strict` yields `pod.to_dict()` mappings from the CoreV1 client
models, so the classifier reads `metadata["owner_references"]`, each entry carrying `api_version`,
`kind`, `name`, `uid`, and `controller` (Task A3). The collection classifier consumes the
`resources` members that `acm_k8s_read_outcome` normalizes, which preserve the server's camelCase
`ownerReferences`. Each side's fixtures use its own real shape; the parity test compares the
**decisions and reason codes**, never the raw member spelling.

**Under `operator_identity_unavailable`,** no Pod is excluded. A strictly verified empty Pod list
still satisfies the drain; any remaining Pod blocks.

**Failure behavior.** Every missing, malformed, ambiguous, unauthorized, timed-out, TLS-failed,
transport-failed, or decode-failed read is a failed proof. A recorded Deployment 404 or UID
replacement enters `recovery_required`; no later Deployment is adopted. The warn-and-return-success
path at `modules/decommission.py:449-455` and the collection's `failed_when: false` MCH wait are
both removed; an unverifiable pod read fails the play. The MCH CR gets a final live absence proof.

**No prefix or name is ever the safety decision.** The three Jinja
`rejectattr('metadata.name', 'match', '^multiclusterhub-operator')` filters at
`delete_multiclusterhub.yml:48,65,76` collapse into the collection-owned classification boundary.

**Sanitization.** Public errors carry a stable stage and reason code plus sanitized resource
identity, never raw bodies, headers, client configuration, tokens, certificates, keys, or Secret
data.

**Dry-run/check-mode safety at E merge.** Python and Collection may execute the strict CSV,
Namespace, Pod, Deployment, and ReplicaSet reads and owner classification. Python branches before
the durable identity/phase writer and guarded delete; Collection skips all phase and
operational-data writers and the guarded-delete module stops before DELETE. Neither records an
identity or completed/refused/failed outcome, neither invokes DELETE, and every task/result reports
actual `changed: false`; only fresh, complete classification can set the separate MCH
`would_change` prediction. Unverifiable identity is a blocker, not predicted success. A later live
run repeats the reads and trusts no preview observation.

### 11C.3 The 20-case operator identity matrix

The same fixtures and expected reason codes run against both form factors; parity tests compare
the classification and the mutation/no-mutation result directly. Each row is one test.

| # | Case | Expected |
| --- | --- | --- |
| 1 | Pod owned through ReplicaSet to the recorded Deployment UID | `operator_owned`, excluded |
| 2 | Bare prefixed Pod, no owner reference | `drain_blocking` |
| 3 | Job-owned prefixed Pod | `drain_blocking` |
| 4 | StatefulSet-owned prefixed Pod | `drain_blocking` |
| 5 | Prefixed Pod owned by an unrelated ReplicaSet | `drain_blocking` |
| 6 | Deployment with the expected name but a different UID | `drain_blocking`; replacement not adopted |
| 7 | Non-prefixed Pod owned by the exact recorded Deployment | `operator_owned`, excluded |
| 8 | Rolling update, multiple ReplicaSets, all resolving to the recorded UID | all `operator_owned` |
| 9 | Missing controller owner reference | `drain_blocking` |
| 10 | Malformed or ambiguous controller owner reference | `drain_blocking` |
| 11 | ReplicaSet GET 404, authorization failure, or other API failure | `drain_blocking` |
| 12 | Deployment GET 404, authorization failure, replacement UID, or other API failure after capture | `drain_blocking`, `recovery_required` |
| 13 | Pod-list discovery, authorization, TLS, timeout, transport, or decode failure | blocks; never an empty list |
| 14 | Identity unavailable before DELETE plus a strictly verified zero-Pod list | verified empty drain; unavailable reason recorded |
| 15 | Identity unavailable before DELETE plus any Pod | blocks; no Pod excluded |
| 16 | Deployment replaced during the drain | blocks; `recovery_required`; no later Deployment adopted |
| 17 | Prefix-only mutation: rename a non-operator Pod to the operator prefix | classification unchanged |
| 18 | Both form factors consume identical fixtures | identical decisions and reason codes |
| 19 | Python dry-run and collection check mode | read-only validation, no DELETE, no durable transition, `changed: false`, prediction reported separately |
| 20 | Success and every failure output | no kubeconfig paths or content, tokens, authorization headers, certificates, keys, client configuration, raw bodies or headers, Secret content, or credential-bearing exception strings |

**Additional provenance tests:** zero owning CSVs; multiple owning CSVs; a non-`Succeeded` CSV;
zero install Deployments; multiple install Deployments; a wrong owned CRD; a missing CSV or
Deployment UID; a malformed install strategy; a mismatched MCH teardown key or UID; durable
capture proven to occur before DELETE; and rerun reuse without re-binding. Request, poll, and
total wait bounds are asserted in both form factors. Final-verification tests inject a new
unproven Pod, and separately an ownership or read failure, after `drained`, and prove `completed`
is not written.

### 11C.4 Test displacement

| Displaced | Replaced by |
| --- | --- |
| `tests/test_decommission.py:645-702` names-only operator Pod fixtures | matrix rows 1 through 20 with full owner chains |
| `tests/unit/test_ansible_resilience_contracts.py:479` bare prefix substring | a contract test asserting no name-match filter decides exclusion |
| `tests/unit/test_ansible_resilience_contracts.py:485` `failed_when: false` | inverted: the MCH wait must carry no failure-absorbing construct |
| `tests/unit/test_decommission_role_contracts.py:336-350` `failed_when: false` | inverted: the MCH wait must carry no failure-absorbing construct |

### 11C.5 RBAC for PR E

| Form factor | Hub | Namespace | Group | Resource | Verb | Already granted |
| --- | --- | --- | --- | --- | --- | --- |
| both | source | `open-cluster-management` | `apps` | `deployments` | `get` | **no** |
| both | source | `open-cluster-management` | `apps` | `replicasets` | `get` | **no** |
| both | source | `open-cluster-management` | `operators.coreos.com` | `clusterserviceversions` | `get`, `list` | **no** |
| both | source | `open-cluster-management` | core | `pods` | `list` | yes |
| both | source | cluster-scoped | `operator.open-cluster-management.io` | `multiclusterhubs` | `get` | operator `ClusterRole` yes; **decommission extension no** |
| both | source | cluster-scoped | core | `namespaces` | `get` | yes |

No `list` is added for Deployments or ReplicaSets, and no `watch` is added for anything: the
implementation follows the exact CSV and owner-reference locators, and bounded polling repeats
strict list and GET operations. Do not add those verbs speculatively.

Surfaces to change together, all in PR E: `lib/rbac_validator.py`
(`DECOMMISSION_NAMESPACE_PERMISSIONS` gains the three ACM-namespace entries;
`DECOMMISSION_CLUSTER_PERMISSIONS` gains `multiclusterhubs get`);
`plugins/modules/acm_rbac_validate.py` mirrors both; the collection decommission task wiring;
`deploy/rbac/role.yaml` ACM-namespace operator Role; its bundled copy under
`roles/rbac_bootstrap/files/deploy/rbac/`; `deploy/helm/acm-switchover-rbac/templates/role.yaml`;
`deploy/rbac/extensions/decommission/clusterrole.yaml` and its bundled copy; all four RBAC
documents (`docs/deployment/rbac-requirements.md`, `docs/deployment/rbac-deployment.md`,
`docs/development/rbac-implementation.md`, and
`docs/deployment/rbac-live-certification.md`); Python and collection RBAC tests; the
parity, static-contract, and manifest/chart consistency tests; and negative authorization tests
that independently deny Pod `list`, CSV `list` and `get`, ReplicaSet `get`, Deployment `get`, and
Namespace `get`, each proving the denial blocks before DELETE or before completion with sanitized
output. The live-certification document is updated to keep its required
SelfSubjectAccessReview/read/delete inventory semantically accurate; that documentation update is
not a live certification result, and this plan authorizes no live certification run.

### 11C.6 Steps

- [ ] **Step 1:** write `tests/test_decommission_identity.py` containing matrix rows 1 through 20
  plus the provenance cases, driven from one declared fixture list; write the mirrored collection
  unit tests from the same fixture data; write the parity test comparing the two decision sets.
- [ ] **Step 2:** run all three and observe the expected failure — `modules/decommission_identity`
  does not exist, and the collection classification module does not exist. Also run
  `tests/test_decommission.py -k operator_pods_excluded` and observe that the existing
  names-only test now fails, confirming the defect it pinned is being removed rather than
  worked around.
- [ ] **Step 3:** consume PR A's strict core/Apps helpers and implement
  `modules/decommission_identity.py`, then the MCH `TeardownSpec` that supplies its classifier to
  the Task C3 phase machine; implement the collection module and `module_utils`, then rewrite
  `delete_multiclusterhub.yml` to use them and the guarded delete.
- [ ] **Step 4:** rerun the targeted suites; expected PASS.
- [ ] **Step 5:** add `ACM_OPERATOR_POD_PREFIX` and the four reason codes to
  `module_utils/constants.py` and to `CONSTANT_PAIRS`, closing criterion A6, and delete the three
  Jinja literals.
- [ ] **Step 6:** land the §11C.5 RBAC change across every surface listed, then run the complete
  RBAC gate from Task C6 step 6.
- [ ] **Step 7:** simplification review — the classifier is one pass over Pods with one memo per
  pass; it must contain no second code path for prefixed Pods.
- [ ] **Step 8:** the full PR gate set (Task A6 command set), then documentation in E:
  `CHANGELOG.md` `## [Unreleased]`; root and Collection READMEs and relevant Mermaid flows;
  the parity matrix decommission row must **replace** its current
  "warns if ACM workload pods remain" text with the fail-closed completion contract; the behavior
  map row gains the classification boundary; architecture and coexistence/CLI migration surfaces
  describe the identity/completion rule; `docs/operations/usage.md` gains the operator-facing
  consequence of an unresolvable operator identity; all four RBAC documents are updated in
  §11C.5. No E behavior documentation is deferred to F.
- [ ] **Step 9:** commits — `feat: bind MCH drain exclusion to the recorded operator Deployment`,
  `feat: prove MultiClusterHub teardown completion`, and
  `feat: grant the operator-identity reads MCH completion requires`.

---

# 12. PR F — integrated proof and consistency closure

**Branch:** `feature/r4-03-closure`

**Purpose.** Add end-to-end proof that the check-mode/dry-run, result, state, mutation, parity,
and documentation contracts already delivered by B/C/D/E compose. PR F introduces no production
safety behavior and repairs no unsafe intermediate merge. A9 is implemented across B/C/D/E; F
supplies its final integrated proof only.

**Prerequisites:** PRs A, B, C, D, E merged.

## Task F1: Prove native check mode and dry-run end to end

**Files:** Create `tests/unit/test_decommission_check_mode.py`; create
`tests/test_decommission_dry_run.py`; modify `tests/scenario/` only to add integrated cases. Test
cleanup may consolidate duplicate fixtures while preserving behavior. No production Python,
role/task, module/plugin, state, result, or manifest file is changed in F.

**Fixture contract.** Every collection assertion in F reads the **one** role-result shape defined in
Task B4.1 and produced by `run_decommission_role(**options)`; `run_role_check_mode(**options)` is a
thin alias for `run_decommission_role(check_mode=True, **options)` defined in
`tests/unit/test_decommission_check_mode.py`. `operational_data` is therefore always read at
`result["checkpoint"]["operational_data"]`. F introduces no new result shape, and any divergence
found during F is a defect in the owning earlier PR, not something F redefines.

**Intended behavior.**

*Python dry-run proof.* Re-exercise all three family contracts already implemented in B/C/D/E:
strict provenance, inventory, and owner-chain reads remain read-only; prediction uses the defined
`would_change` field; no DELETE primitive is called; no record, phase, operator identity, or
completed/refused/failed substep outcome is persisted; `changed` is false; and a later live run
repeats fresh reads and trusts nothing from the dry run.

*Ansible integrated proof, two layers.* The role's `acm_switchover_execution.mode` dry-run gate remains the primary
operator-facing preview and stays read-only — the existing live reads before those gates
(`has_observability: auto` reading the observability Namespace, and RBAC validation performing
live SelfSubjectAccessReviews unless explicitly skipped) keep their read-only character and are
reviewed against the new strict-read paths. Separately, **native** check mode behavior already
implemented with each mutation is exercised together: `acm_uid_guarded_delete` stops after the
live read and UID validation and returns `changed: false` with explicit `would_change`;
`acm_k8s_read_outcome` continues to read (read-only by contract, existing tested behavior); the
classification module is read-only; no checkpoint `operational_data` transition is written; no
task reports `changed: true`; and `acm_switchover_decommission_result.changed` is `false`.

- [ ] **Step 1: Write the failing tests**

```python
# Collection — every helper below is the B4.1 contract, imported from
# tests/unit/test_decommission_role_contracts.py
def test_check_mode_writes_no_checkpoint_transition():
    result = run_role_check_mode()
    assert result["checkpoint"]["operational_data"].get("decommission_teardown_records") is None


def test_check_mode_reports_no_change_and_predicts_separately():
    result = run_role_check_mode(mch_present=True)
    assert result["acm_switchover_decommission_result"]["changed"] is False
    assert result["acm_switchover_decommission_result"]["would_change"] is True


def test_no_task_reports_changed_true_in_check_mode():
    assert not [t for t in run_role_check_mode()["tasks"] if t["changed"]]


def test_every_mutating_task_declares_check_mode_handling():
    for task in mutating_tasks(decommission_task_files):
        assert "check_mode" in str(task) or task["module"] in CHECK_MODE_NATIVE_MODULES
```

```python
# Python
def test_dry_run_persists_no_teardown_record(decommission_dry_run, state_manager):
    decommission_dry_run.decommission(interactive=False)
    assert RunRecord(state_manager).all_teardown_records() == {}


def test_dry_run_persists_no_operator_identity(decommission_dry_run, state_manager):
    decommission_dry_run.decommission(interactive=False)
    assert "operator_deployment" not in json.dumps(state_manager.capture_state_snapshot())


def test_a_live_run_after_a_dry_run_starts_from_no_record(decommission_dry_run, decommission_live,
                                                          state_manager):
    decommission_dry_run.decommission(interactive=False)
    assert RunRecord(state_manager).teardown_record(MCH_KEY) is None


def test_dry_run_reports_the_predicted_blocker_set(decommission_dry_run, caplog):
    decommission_dry_run.decommission(interactive=False)
    assert "predicted drain-blocking" in caplog.text
```

- [ ] Add assertions that dry-run records no substep outcome as completed/refused/failed, calls no
  guarded-delete primitive, reports `changed is False`, and uses only the already-defined
  `would_change` prediction. The preview contract is expressed entirely through `changed`,
  `would_change`, the absence of any persisted outcome, identity, or phase, and the absence of any
  DELETE call: no additional result field marks a run as a preview, and no test asserts one.
- [ ] Run these tests first. Any failure is a regression in B/C/D/E and blocks PR F: repair the
  owning earlier PR before it merges, or, if already merged, open a separately governed safety
  fix. Do not add a first safety branch in F.
- [ ] Commit only integrated proof: `test: prove decommission preview safety end to end`.

## Task F2: Documentation and parity consistency audit

**Files to review:**

- `README.md` and its Mermaid diagrams
- `CHANGELOG.md`
- `docs/operations/usage.md`
- `docs/operations/quickref.md`
- `docs/reference/validation-rules.md`
- `docs/development/architecture.md` and its Mermaid diagrams
- `docs/ansible-collection/parity-matrix.md`
- `docs/ansible-collection/behavior-map.md`
- `ansible_collections/tomazb/acm_switchover/README.md`
- `ansible_collections/tomazb/acm_switchover/docs/coexistence.md`
- `ansible_collections/tomazb/acm_switchover/docs/variable-reference.md`
- `ansible_collections/tomazb/acm_switchover/docs/cli-migration-map.md`
- `ansible_collections/tomazb/acm_switchover/examples/group_vars/all.yml`
- all four RBAC authorities: `docs/deployment/rbac-requirements.md`,
  `docs/deployment/rbac-deployment.md`, `docs/development/rbac-implementation.md`, and
  `docs/deployment/rbac-live-certification.md`
- the affected scenario and test-migration catalogs

**Must already be documented by the owning PR before F starts:**

1. The decommission outcome vocabulary and the non-zero exit on refusal or failure.
2. `--acknowledge-observability-not-migrated` and the mirrored collection variable, including that
   it is accepted only against a positively verified absent destination.
3. The teardown phase model, what `completed` does and does not assert, and that integrated
   teardown re-proves live.
4. **The full-reset consequence, operator-facing (first published in C):** Python `--reset-state` and the collection's
   full `checkpoint.reset` destroy teardown records, so a post-reset rerun that finds a CR absent
   is indistinguishable from never-attempted and takes the clean-skip path. Resetting state between
   a failed drain and its rerun forfeits the drain obligation's memory. `reset_from` is different
   and retains the records.
5. That an unresolvable operator identity means no Pod is excluded, and only a strictly verified
   empty Pod list satisfies the drain.
6. That R4-03 binds **resource** identity and does not provide wrong-target hub protection (§19).

F runs guardrails and compares the documents against the merged behavior. It may fix a typo,
broken link, or duplicated test fixture that does not change behavior, but it may not be the first
PR to document behavior introduced by B/C/D/E. If an already-false operator document is found,
stop and repair the owning PR before it merges rather than using F as a documentation buffer.

**Do not modify** `docs/ACM_SWITCHOVER_RUNBOOK.md` or `.claude/skills/**`. If implementation
reveals a genuinely required protected-file change, stop and request separate operator approval
with the proposed line-by-line diff; that future task is marked `OPERATOR_APPROVAL_REQUIRED` and
is not pre-authorized by this plan. §18 records the current assessment: no protected-file change
is anticipated, because the runbook documents the manual procedure and this slice changes the
tool's proof obligations rather than the operator's manual steps.

- [ ] Run `python -m pytest tests/test_documentation_guardrails.py tests/test_ci_guardrails.py -q`
- [ ] Add a documentation-timing guardrail or audit assertion where useful; commit only a
  consistency-only change, if any. Do not republish the contracts already delivered in B/C/D/E.

## Task F3: Final slice verification

- [ ] The complete gate set in §21, at the frozen candidate head.
- [ ] Simplification gate across the whole slice's changed surface.
- [ ] Confirm the F diff contains no production behavior or first-publication operator/RBAC
  documentation, commit/open PR F, and run the §22 workflow.

---

# 13. Cross-PR state and schema contract

Every durable field R4-03 introduces, with the properties the implementation must make true. No
durable key outside this table is added by this slice.

| Property | Teardown record (per resource) | `operator_deployment` | `operator_identity_unavailable` |
| --- | --- | --- | --- |
| Python API | `RunRecord.record_teardown_phase` / `.teardown_record` / `.all_teardown_records` (Task B1) | same record API; written by the MCH spec (PR E) | same |
| Python config key | `decommission_teardown_records` | nested inside the MCH record | nested inside the MCH record |
| Collection owner | `module_utils/checkpoint.py` `KEY_DECOMMISSION_TEARDOWN_RECORDS` (Task B2) | nested in the same record | nested |
| Serialization shape | §10.2 | July §1a schema verbatim | July §1a schema verbatim |
| Validation path | `lib/teardown_record.py` and `checkpoint.py`, on both read and write | same | same |
| Write timing | before DELETE, and at every phase transition | one write before the MCH DELETE | one write before the MCH DELETE |
| Forced durable | yes — `record_teardown_phase` flushes before returning | yes | yes |
| Read timing | before the initial inventory classification, and at every resume | every drain and final-verification pass | every pass |
| UID binding | the exact CR UID; immutable | the exact Deployment UID; bound to the enclosing MCH key and UID | bound to the enclosing MCH key and UID |
| `observed_at` | required at `completed` | `captured_at` required | `captured_at` required |
| `resource_versions` | required at `completed` with the exact §10.2.1 proof-key set for the family and the proof mode used: `cr` always; `pods` when a successful Pod LIST proved the drain; `namespace_absent` when positive namespace absence entailed it; a family without a drain scope carries `cr` only | not applicable | not applicable |
| Serialized nested schema | not applicable | §10.2.2, field by field, validated on read and write in both form factors | §10.2.3, field by field, with `reason` drawn from `OPERATOR_IDENTITY_UNAVAILABLE_REASONS` |
| Malformed or missing | fail closed before any mutation or clean-skip decision | fail closed; DELETE not issued | fail closed; DELETE not issued |
| Resume behavior | resume from the recorded phase; absence never resets the machine or creates a clean skip | never re-discovered or overwritten | never silently upgraded by rediscovery |
| Reset behavior | Python `--reset-state` and collection full `checkpoint.reset` destroy it; `reset_from` retains and revalidates it | same | same |
| Check mode and dry-run | never written | never written | never written |

**Deliberately not persisted:** the destination-gate result (re-proven fresh every run, including
every resume); refusal events (they end the run; the summary is output, not state); and any
dry-run or check-mode observation.

**Recorded phases resume obligations, never conclusions.** A `completed` record asserts
completeness at the instant of its final read only. Integrated teardown re-runs the CR-absence and
identity-aware Pod checks against live state before relying on a teardown being complete, so a
replacement appearing after the completion write is caught by that gate rather than masked by the
stored proof.

**Fresh mutation predicates stay freshly discovered.** The destination gate, the source
observability determination, and the final Pod classification always run against live state. The
recorded `primary_has_observability` and `secondary_has_observability` facts in
`lib/run_record.py` are informational only and are never gate inputs.

---

# 14. RBAC change matrix

Every new API operation this slice introduces, by form factor, caller, cluster, namespace, group,
resource, verb, whether the permission exists today, and the PR that introduces it.

| # | Form factor | Caller | Hub | Namespace | Group | Resource | Verb | Exists today | PR |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | both | strict read discovery probe | source and destination | n/a | n/a | discovery endpoints | none | n/a — no verb required | A |
| 2 | both | MCO strict named GET, final absence proof | source | cluster | `observability.open-cluster-management.io` | `multiclusterobservabilities` | `get` | operator `ClusterRole` yes; decommission extension **no** | C |
| 3 | both | MCO drain strict Pod list | source | `open-cluster-management-observability` | core | `pods` | `list` | **yes** | C |
| 4 | both | fixed-namespace absence proof | source | cluster | core | `namespaces` | `get` | **yes** | C |
| 5 | both | destination gate MCO read | destination | cluster | `observability.open-cluster-management.io` | `multiclusterobservabilities` | `get`, `list` | **yes** — `deploy/rbac/clusterrole.yaml` grants `get, list, delete` | C |
| 6 | both | destination gate namespace read | destination | cluster | core | `namespaces` | `get` | **yes** — `deploy/rbac/clusterrole.yaml` grants `get, list` | C |
| 7 | both | ManagedCluster strict named GET, absence proof | source | cluster | `cluster.open-cluster-management.io` | `managedclusters` | `get` | operator `ClusterRole` yes; decommission extension **no** | D |
| 8 | both | Hive safety inventory | source | cluster | `hive.openshift.io` | `clusterdeployments` | `list` | **yes** | D |
| 9 | both | MCH strict named GET, final absence proof | source | cluster | `operator.open-cluster-management.io` | `multiclusterhubs` | `get` | operator `ClusterRole` yes; decommission extension **no** | E |
| 10 | both | CSV provenance discovery and revalidation | source | `open-cluster-management` | `operators.coreos.com` | `clusterserviceversions` | `get`, `list` | **no** | E |
| 11 | both | operator Deployment capture and revalidation | source | `open-cluster-management` | `apps` | `deployments` | `get` | **no** | E |
| 12 | both | Pod controller reference resolution | source | `open-cluster-management` | `apps` | `replicasets` | `get` | **no** | E |
| 13 | both | MCH drain strict Pod list | source | `open-cluster-management` | core | `pods` | `list` | **yes** | E |

**Explicitly resolved, as the governing task requires:**

- `managedclusters get` — row 7; **added** to the decommission extension in PR D.
- `multiclusterhubs get` — row 9; **added** to the decommission extension in PR E.
- `multiclusterobservabilities get` — row 2; **added** to the decommission extension in PR C.
- ACM-namespace Deployments GET — row 11; **added** in PR E.
- ACM-namespace ReplicaSets GET — row 12; **added** in PR E.
- CSV GET and LIST as actually required — row 10; both verbs, because provenance requires
  discovering the unique owning CSV (`list`) and then revalidating that exact CSV (`get`).
- Namespace GET — rows 4 and 6; already granted, no change.
- Source observability reads — rows 2 and 3; `get` added, `list` already granted.
- Destination observability reads — rows 5 and 6; **verified already granted** by the baseline
  operator `ClusterRole`, which the secondary hub already carries for preflight. No new grant.
- Delete verbs — already present on all three CRs; unchanged.
- **No new verb for UID preconditions.** Preconditions are request-body content, not
  authorization.
- **No new verb for pagination.** `continue` is a query parameter on the same `list` verb.
- **No speculative `list` on Deployments or ReplicaSets, and no `watch` anywhere.** The
  implementation follows exact CSV and owner-reference locators, and bounded polling repeats
  strict `list` and `get` operations. If a future implementation wants a batch-list optimization,
  it must make its own least-privilege and completeness case first.

**Surfaces every RBAC change must touch together**, per the `AGENTS.md` RBAC cross-surface
contract. Each of PR C, PR D, and PR E carries the full set for its own rows:

1. `lib/rbac_validator.py`
2. `ansible_collections/tomazb/acm_switchover/plugins/modules/acm_rbac_validate.py`
3. Collection task wiring that consumes the RBAC matrix (`roles/decommission/tasks/validate_rbac.yml`, and `preflight` / `rbac_bootstrap` where affected)
4. `deploy/rbac/` — `role.yaml`, `clusterrole.yaml`, `extensions/decommission/clusterrole.yaml`
5. `ansible_collections/tomazb/acm_switchover/roles/rbac_bootstrap/files/deploy/rbac/**` bundled copies
6. `deploy/helm/acm-switchover-rbac/templates/{role,clusterrole}.yaml`
7. `docs/deployment/rbac-requirements.md`
8. `docs/deployment/rbac-deployment.md`
9. `docs/development/rbac-implementation.md`
10. `docs/deployment/rbac-live-certification.md` — update the required live
    SelfSubjectAccessReview/read/delete inventory; never represent the edit as a completed live run
11. Python RBAC tests — `tests/test_rbac_validator.py`, `tests/test_rbac_integration.py`
12. Collection RBAC tests
13. Parity and static-contract tests — `tests/test_rbac_collection_parity.py`,
    `tests/properties/test_rbac_properties.py`, and the manifest/chart consistency checks
14. Negative authorization tests — one per newly required permission, each proving the denial
    blocks before DELETE or before a completion claim, with sanitized output

**Timing rule.** Permissions land no later than the PR introducing the corresponding API call. No
PR in this plan makes a call whose permission arrives in a later PR.

---

# 15. External API and version pins

The July design's citation-provenance limitation is discharged here. Every reference that carries a
safety conclusion is pinned to an immutable upstream tag or commit with a file path and the exact
declaration, and was retrieved and verified on 2026-09-01 against the exact versions the
repository's dependency and compatibility authorities permit. Unversioned documentation pages
appear only in §15.2.4, explicitly labelled background / non-normative, and no rule in this plan
rests on them.

## 15.1 Supported version ranges

| Dependency | Declared range | Authority |
| --- | --- | --- |
| Python `kubernetes` client | `kubernetes>=28.0.0` | `requirements.txt`; restated by the compatibility authority |
| `kubernetes.core` | `>=6.0.0,<7.0.0`, resolved bounded not pinned | `galaxy.yml`, `requirements.yml`, compatibility authority |
| `ansible-core` | `>=2.16.0,<2.22`; repository-tested lanes 2.16 and 2.21 | `meta/runtime.yml`, compatibility authority |
| Python CLI | 3.10 – 3.12 | `setup.cfg` |

## 15.2 Pinned references

Every claim that carries a **safety conclusion** in this plan is supported below by an immutable
upstream tag or commit, a file path, and the exact declaration that supports it. No `latest`, no
`master`, no branch tip, and no unversioned documentation page carries a safety conclusion.

### 15.2.1 Kubernetes API contract — `kubernetes/kubernetes`

Checked at two immutable tags, `v1.26.0` and `v1.31.0`, spanning the API surface this slice
depends on. Path is
`staging/src/k8s.io/apimachinery/pkg/apis/meta/v1/types.go` unless stated otherwise.

| Claim | Declaration | v1.26.0 | v1.31.0 |
| --- | --- | --- | --- |
| Delete preconditions carry the target UID, and optionally a resourceVersion | `type Preconditions struct { UID *types.UID \`json:"uid,omitempty"\`; ResourceVersion *string \`json:"resourceVersion,omitempty"\` }` | `:699-706` | `:734-741` |
| `DeleteOptions` carries those preconditions | `Preconditions *Preconditions \`json:"preconditions,omitempty"\`` | `:482` | `:521` |
| An object UID is unique in time and space, server-generated, and may not change | `ObjectMeta.UID` — "UID is the unique in time and space value for this object … not allowed to change on PUT operations. Populated by the system. Read-only." | `:151-159` | `:151-159` |
| A controller owner reference carries both the referent's kind/apiVersion and its UID, and marks the controller | `OwnerReference{ APIVersion, Kind, Name, UID types.UID, Controller *bool }` | `:290-305` | `:294-309` |
| A list response carries its own revision and its continuation token | `ListMeta{ ResourceVersion string \`json:"resourceVersion,omitempty"\`; Continue string \`json:"continue,omitempty"\` }` | `:73`, `:82` | `:73`, `:82` |
| Discovery returns `APIResourceList.resources`, whose `name` is the **plural** resource name that strict classification matches exactly | `APIResource{ Name string // "name is the plural name of the resource" }`, `APIResourceList{ APIResources []APIResource \`json:"resources"\` }` | `:1081-1083`, `:1131-1137` | `:1141-1143`, `:1193-1200` |

| Claim | Immutable source | v1.26.0 | v1.31.0 |
| --- | --- | --- | --- |
| A Deployment owns its ReplicaSets through a controller owner reference it sets at creation | `pkg/controller/deployment/sync.go` — `OwnerReferences: []metav1.OwnerReference{*metav1.NewControllerRef(d, controllerKind)}` | `:200` | `:201` |
| A ReplicaSet owns its Pods through a controller owner reference it sets at creation | `pkg/controller/replicaset/replica_set.go` — `rsc.podControl.CreatePods(..., metav1.NewControllerRef(rs, rsc.GroupVersionKind))` | `:581` | `:598` |
| A controller reference is resolved by **UID**, not by name: a same-name object with a different UID is not the owner | `pkg/controller/replicaset/replica_set.go::resolveControllerRef` — `if rs.UID != controllerRef.UID { … return nil }` | `:258-268` | `:274-284` |

This is the exact chain PR E requires: `Pod → controller ReplicaSet → controller Deployment`, with
UID equality at every link, and with the recorded operator Deployment UID as the terminal anchor.

### 15.2.2 OLM ClusterServiceVersion contract — `operator-framework/api`

Checked at two immutable tags, `v0.17.6` and `v0.27.0`. Path is
`pkg/operators/v1alpha1/clusterserviceversion_types.go`; the declarations are byte-identical at
both tags, including their line numbers.

| Claim | Declaration | Both tags |
| --- | --- | --- |
| A CSV declares the CRDs it owns, and each owned entry carries the CRD's exact `name` | `CustomResourceDefinitions{ Owned []CRDDescription }`; `CRDDescription{ Name string \`json:"name"\` }` | `:260-263`, `:119-125` |
| A CSV's install strategy declares its Deployments by name | `NamedInstallStrategy{ StrategySpec StrategyDetailsDeployment }`; `StrategyDetailsDeployment{ DeploymentSpecs []StrategyDeploymentSpec }`; `StrategyDeploymentSpec{ Name string \`json:"name"\` }` | `:54-57`, `:77-80`, `:68-72` |
| `Succeeded` is the phase asserting the CSV's resources were created successfully | `CSVPhaseSucceeded ClusterServiceVersionPhase = "Succeeded"`, with the comment "means that the resources in the CSV were created successfully" | `:395-396` |

That is exactly the locator PR E uses: the unique `Succeeded` CSV whose `owned` list contains
`multiclusterhubs.operator.open-cluster-management.io`, and its single declared install Deployment
name, which is then strictly GET-ed for its live UID.

**Support-range statement.** These are the two immutable endpoints this plan checked, and the
declarations are identical at both. This plan makes **no** OCP or OLM support claim beyond what
repository authorities already state; the ACM range remains §15.4's audited 2.11–2.17, and outside
any supported range the runtime contract fails closed rather than falling back.

### 15.2.3 Python client and `kubernetes.core` interfaces

| Claim | Pinned source |
| --- | --- |
| `V1Preconditions` exposes `uid` and `resource_version` | `https://github.com/kubernetes-client/python/blob/v28.1.0/kubernetes/client/models/v1_preconditions.py` and `https://github.com/kubernetes-client/python/blob/v36.0.1/kubernetes/client/models/v1_preconditions.py` — `openapi_types` includes `resource_version` and `uid`, mapped to `resourceVersion` and `uid`, at **both** pins |
| `V1DeleteOptions.preconditions` is a `V1Preconditions` | `https://github.com/kubernetes-client/python/blob/v28.1.0/kubernetes/client/models/v1_delete_options.py` and `https://github.com/kubernetes-client/python/blob/v36.0.1/kubernetes/client/models/v1_delete_options.py` |
| `CustomObjectsApi.delete_namespaced_custom_object` and `delete_cluster_custom_object` accept a `V1DeleteOptions` `body` | `https://github.com/kubernetes-client/python/blob/v28.1.0/kubernetes/client/api/custom_objects_api.py` and `https://github.com/kubernetes-client/python/blob/v36.0.1/kubernetes/client/api/custom_objects_api.py` — the optional `body` is typed `V1DeleteOptions` and forwarded as `body_params` at **both** pins |
| CoreV1 `list_namespaced_pod` supports bounded pagination via `_continue` and `limit`, and returns a `V1PodList` whose `V1ListMeta` exposes `_continue` and `resource_version` | `https://github.com/kubernetes-client/python/blob/v28.1.0/kubernetes/client/api/core_v1_api.py` and `https://github.com/kubernetes-client/python/blob/v36.0.1/kubernetes/client/api/core_v1_api.py`, plus `kubernetes/client/models/v1_list_meta.py` at both pins — `attribute_map` maps `_continue` to `continue` and `resource_version` to `resourceVersion` |
| Dynamic client `delete` accepts a `body`; `get` accepts `_continue` and `limit`; `request` accepts `serialize=False` and forwards `_request_timeout` to `ApiClient.call_api` | `https://github.com/kubernetes-client/python/blob/v28.1.0/kubernetes/base/dynamic/client.py` and `https://github.com/kubernetes-client/python/blob/v36.0.1/kubernetes/base/dynamic/client.py` — `meta_request` pops `serialize` (default `True`) and returns the raw response when it is `False`; `request` maps `limit`, `_continue`, and `_request_timeout` at **both** pins |
| HTTP 410 maps to `GoneError`, a `DynamicApiError` subclass carrying `status` | `https://github.com/kubernetes-client/python/blob/v28.1.0/kubernetes/base/dynamic/exceptions.py` and `https://github.com/kubernetes-client/python/blob/v36.0.1/kubernetes/base/dynamic/exceptions.py` — `410: GoneError` in the `api_exception` map, and `DynamicApiError.__init__` sets `self.status = e.status`, at **both** pins |
| Discovery swallows some fetch/decode failures into an empty resource list | `https://github.com/kubernetes-client/python/blob/v28.1.0/kubernetes/base/dynamic/discovery.py` and `https://github.com/kubernetes-client/python/blob/v36.0.1/kubernetes/base/dynamic/discovery.py` — see §15.3 |
| `kubernetes.core` exposes the required client factory and resource lookup interfaces, and `K8SClient.client` is the dynamic client the discovery prover uses | `https://github.com/ansible-collections/kubernetes.core/blob/6.0.0/plugins/module_utils/k8s/client.py`, `https://github.com/ansible-collections/kubernetes.core/blob/6.5.0/plugins/module_utils/k8s/client.py`, `https://github.com/ansible-collections/kubernetes.core/blob/6.0.0/plugins/module_utils/args_common.py`, and `https://github.com/ansible-collections/kubernetes.core/blob/6.5.0/plugins/module_utils/args_common.py` — `K8SClient.__init__` stores `self.client`, `get`/`resource`/`delete` proxy to it, and `_find_resource_with_prefix` is unchanged between the supported floor and the current resolved lane |

`v28.1.0` is the earliest released tag satisfying the `kubernetes>=28.0.0` floor; `v36.0.1` is the
newest compatibility pin verified during planning; the repository's current resolved one-off lane
uses Python client 36.0.3 without changing these load-bearing interfaces. `6.0.0` is the
`kubernetes.core` floor and `6.5.0` is the current resolved lane checked here.

### 15.2.4 Background reading — non-normative

The following unversioned documentation pages are **background / non-normative; not load-bearing
evidence**. No rule, classification, or fail-closed decision in this plan rests on them, and each
corresponding safety conclusion is carried by an immutable source in §15.2.1 through §15.2.3:

- `https://kubernetes.io/docs/concepts/overview/working-with-objects/names/#uids`
- `https://kubernetes.io/docs/concepts/overview/working-with-objects/owners-dependents/`
- `https://kubernetes.io/docs/concepts/workloads/controllers/deployment/`
- `https://kubernetes.io/docs/concepts/workloads/controllers/replicaset/`
- `https://olm.operatorframework.io/docs/concepts/crds/clusterserviceversion/`

### 15.2.5 Evidence audit — safety claim to immutable source to implementation task

| Safety claim | Immutable repository | Tag / commit | Path | Checked range | Implementation task |
| --- | --- | --- | --- | --- | --- |
| A DELETE can be bound to a target UID server-side | `kubernetes/kubernetes` | `v1.26.0`, `v1.31.0` | `staging/src/k8s.io/apimachinery/pkg/apis/meta/v1/types.go` | both endpoints, identical | C1, C2 |
| A UID identifies one object for its lifetime and is never rebound | `kubernetes/kubernetes` | `v1.26.0`, `v1.31.0` | same | both endpoints, identical | B1, B2, C1, C3, D, E |
| A list response's `resourceVersion` is the revision it was served at | `kubernetes/kubernetes` | `v1.26.0`, `v1.31.0` | same | both endpoints, identical | A3, C3 (`resource_versions.pods`) |
| Pagination continues through `metadata.continue` | `kubernetes/kubernetes` | `v1.26.0`, `v1.31.0` | same | both endpoints, identical | A3, A4 |
| Discovery's `resources[].name` is the exact plural to match | `kubernetes/kubernetes` | `v1.26.0`, `v1.31.0` | same | both endpoints, identical | A2, A4 |
| Controller ownership is UID-resolved, Deployment to ReplicaSet to Pod | `kubernetes/kubernetes` | `v1.26.0`, `v1.31.0` | `pkg/controller/deployment/sync.go`, `pkg/controller/replicaset/replica_set.go` | both endpoints, identical semantics | E (11C.2, matrix rows 1, 5–12, 16) |
| A CSV declares its owned CRDs and its install Deployment names, and `Succeeded` is the ready phase | `operator-framework/api` | `v0.17.6`, `v0.27.0` | `pkg/operators/v1alpha1/clusterserviceversion_types.go` | both endpoints, byte-identical | E (11C.2 provenance capture) |
| Preconditioned delete, bounded paging, `serialize=False` discovery, and 410 classification exist in the supported Python client range | `kubernetes-client/python` | `v28.1.0`, `v36.0.1` | `kubernetes/client/models/`, `kubernetes/client/api/`, `kubernetes/base/dynamic/` | both endpoints | A2, A3, A4, C1 |
| The collection's client factory exposes the dynamic client and resource lookup used by the strict seam | `ansible-collections/kubernetes.core` | `6.0.0`, `6.5.0` | `plugins/module_utils/k8s/client.py`, `plugins/module_utils/args_common.py` | floor and current resolved lane | A4, C2 |

## 15.3 A behavior that differs materially inside the supported range

`Discoverer.get_resources_for_api_version` substitutes an empty resource list when the discovery
request for a group/version fails, and the substituted failure set is **not stable**:

| Pin | Exception set swallowed |
| --- | --- |
| `v28.1.0` | `ServiceUnavailableError` |
| `v36.0.1` | `ServiceUnavailableError` and `JSONDecodeError` |

Because a swallowed failure yields zero matches, `Discoverer.get` and `LazyDiscoverer.search` then
raise `ResourceNotFoundError`, which is therefore **not** a proof of kind absence at either pin,
and is differently unreliable between them.

**How this plan accounts for both.** Neither form factor classifies kind absence from an exception
type, and neither uses discovery HTTP 404 as positive absence. Both prove absence only from their
own direct discovery request that returns HTTP success, decodes to a structurally valid
APIResourceList, and lacks the exact canonical resource name (§9.2, Tasks A2 and A4). That rule is
identical at every version in the range, so the implementation needs no
version branch and no narrowed supported subset. The behavior difference is therefore accounted
for rather than fenced off, and the fail-closed direction is preserved at both ends: an
unverifiable discovery is `error` regardless of which exception the client raised.

## 15.4 ACM version range

The July CSV audit covering ACM 2.11 through 2.17 stands, pinned at the seven exact
`stolostron/multiclusterhub-operator` release-branch commits listed in the July design. That range
remains the widest any repository authority claims. Outside it the runtime contract fails closed:
a live installation that does not satisfy the strict provenance contract blocks rather than
falling back to a historical name.

---

# 16. Test strategy and TDD map

## 16.1 Discipline

Every runtime task above follows red-green-refactor without exception:

1. Add the smallest failing test that states the required behavior.
2. Run it and observe the **named** expected failure. A test that passes before implementation is
   not evidence; investigate why rather than proceeding.
3. Write the minimum production change.
4. Rerun the targeted command.
5. Simplify the change and its directly affected collaborators.
6. Rerun the targeted command.
7. Run the wider gate the edit invalidates before committing.

Safety tests assert behavior, not implementation detail. The decorator-bypassing mock pattern at
`tests/test_decommission.py:190-204` is not carried forward: behavior that the real seam owns is
asserted against the real seam.

## 16.2 Amendment §16 matrix mapped to executable tests

| §16 item | Tests | PR |
| --- | --- | --- |
| 1. Strict-read parity vectors, both form factors, pagination completeness and later-page failure | `tests/test_strict_read_parity.py` (17 vectors, three assertions each, plus `test_strict_read_bounds_are_mirrored`); `TestStrictCustomResourceReads`; `TestStrictCoreReads` for Namespace and paginated Pods; `TestStrictAppsReads` for Deployment and ReplicaSet; the collection pagination, mandatory-`limit`, bounded-timeout, 410-restart, malformed-page, exact irregular-plural, discovery-404, and malformed-discovery tests in `test_k8s_read_outcome.py` | A |
| 2. Read-outcome extension regression | `test_k8s_read_outcome.py` and `test_k8s_read_outcome_runtime.py` with inverted discovery-miss expectations, canonical `resource_name` on every invocation, new pagination/bounds/restart/malformed-page vectors, and the RETURN plus required-option assertions; `ansible-test sanity --test validate-modules`; runtime consumer lanes `test_r3_02_compactor_runtime.py` and `test_r3_02_activation_runtime.py` | A |
| 3. Fail-open inversions | collection: `test_decommission_role_contracts.py:336-350` and `test_ansible_resilience_contracts.py:485` inverted; Python: lingering-pod warning becomes fatal; MCO and MCH absence re-checks asserted against the real client seam | C, E |
| 4. Refusal matrix | `TestDecommissionOutcomes` — top-level banner cancellation is explicitly unsuccessful with all requested work not attempted and no substep invoked; each later prompt refusal aborts with an accurate summary and non-zero result; current false CLI mapping is preserved; callers use `.succeeded`, never object truthiness; rerun completes idempotently; non-interactive and integrated paths never prompt | B |
| 5. Guarded-delete matrix | `TestPreconditionedDelete`; the collection `test_uid_guarded_delete.py` set — UID success, 409 and 412 fatal, pre-DELETE disappearance, mid-poll replacement, bounded timeout, check-mode `would_change`, redaction injection | C |
| 6. Identity and TOCTOU matrix | `tests/test_decommission_identity.py` rows 1 through 20 plus the provenance cases; same-name new-UID replacement between discovery and DELETE; unrelated prefixed Pod; invalid, missing, and multiple owner chains; Deployment and ReplicaSet replacement mid-drain | E |
| 7. Destination gate matrix | `TestDestinationObservabilityGate` — destination positively absent, present, `error`, and ambiguous mixed state; the flag accepted only against positive absence; the flag rejected when the gate would pass; resume re-runs the gate; the result is not persisted | C |
| 8. State, resume, reset | the phase-table resume matrix in `tests/test_decommission.py`; the full-reset clean-skip limitation asserted as **current** behavior and published operator-facing in C; the collection `reset_from` case retaining and revalidating the records; the §10.2.1 completion proof-key matrix and the §10.2.2/§10.2.3 malformed nested-identity matrices in `tests/test_teardown_record.py` and `tests/unit/test_teardown_records.py`, held equal by the shared malformed-fixture parity test in `tests/test_checkpoint_state_parity.py`; reload validation on both sides | B, C |
| 9. Consolidation regression, GLM-H6 | `test_finalization_and_direct_decommission_share_one_teardown_path`; `test_no_second_mco_teardown_implementation_remains`; GitOps markers recorded for the finalization caller only; caller preconditions preserved; collection artifact status honesty | B, C |
| 10. Wrong-target boundary | a negative test asserting R4-03 binds **resource** identity and that no wrong-context or wrong-hub target check exists here, so the SSA-02 boundary is tested rather than silently assumed | F |
| 11. Constants parity | `CONSTANT_PAIRS` gains the strict-read reason codes and the three strict-read bounds (A), `OPERATOR_IDENTITY_UNAVAILABLE_REASONS`, `DRAIN_SCOPED_KINDS`, and `IDENTITY_BEARING_KINDS` (B), `OBSERVABILITY_POD_LABEL_SELECTOR` and the five destination-gate reason codes (C), and `ACM_OPERATOR_POD_PREFIX` plus the four classification reason codes (E); `test_strict_read_bounds_are_mirrored` additionally ties the collection request-timeout constant to the Python client default | A, B, C, E |

## 16.3 Negative safety coverage required by `AGENTS.md`

Each of these is a defect if missing on a path this slice touches, and each has a named test
above: wrong-context behavior (§16.2 item 10), check-mode behavior implemented and tested in
B/C/D/E plus F's integrated proof, idempotence (item 4 rerun case), RBAC denial (§14 surface 14), checkpoint and resume failure (item 8), stale Argo CD
status (not touched by this slice), timeout failure (items 5 and 6), and destructive-operation
confirmation (item 4).

---

# 17. Design-criterion traceability matrix

No criterion is unmapped. "Gate" names the verification that must pass for the criterion to be
considered discharged.

## 17.1 Tracker findings

| Criterion | PR | Task | Tests | Gate |
| --- | --- | --- | --- | --- |
| R4-C1 MCH completion fails open | E | 11C.6 | matrix rows 1–20; final-verification injection tests; inverted `failed_when: false` pins | root suite + collection surfaces 3–7 |
| R4-C2 cancellation/refusal returns success | B | B3, B4 | `TestDecommissionOutcomes`; top-level cancellation, no-substep, `.succeeded`, finalization truthiness guard, and CLI mapping tests | root suite |
| R4-C3 no UID-preconditioned DELETE or absence proof; unscoped pod waits | C, D, E | C1, C2, C3, C4, 11B, 11C.6 | `TestPreconditionedDelete`; `test_uid_guarded_delete.py`; the phase-table matrix; selector-scoping contract test | both form factors + parity |
| R4-C4 404 to `[]` inventory blindness | A, C, D | A1–A5, C3, 11B | strict outcome and parity vectors; the ManagedCluster discovery-failure test; the Hive missing-CRD test | parity vectors |
| R4-C5 no destination-observability gate | C | C5 | `TestDestinationObservabilityGate` | root suite + collection |
| R4-C6 prefix-only operator identity | E | 11C.6 | matrix rows 2–7, 17; constants parity | parity + collection |
| GLM-H6 duplicated MCO teardown | C | C3 | consolidation regression tests | root suite |
| Tracker converted obligation: strict 404 algebra on the deletion boundary's named GETs and final-verification reads | A, C, D, E | A2, A3, C3, 11B, 11C.6 | `test_named_get_404_without_successful_discovery_is_never_object_absent`; discovery-prover suite; every final-absence proof test | parity vectors |

## 17.2 July design acceptance criteria

| July criterion | PR | Tests |
| --- | --- | --- |
| 1. Every deletion records an immutable UID before DELETE and is server-side preconditioned; reruns never rebind; no success while a targeted CR exists | C, D, E | `test_expected_uid_is_never_rebound_by_a_later_write`; `TestPreconditionedDelete`; same-UID survivor and different-UID replacement cases |
| 2. Top-level cancellation and a refused substep each yield an unsuccessful/non-zero result and accurate summary, without conflating their states | B | `TestDecommissionOutcomes`; CLI and Finalization explicit-`.succeeded` tests |
| 3. Missing API discovery aborts before any deletion decision depending on the list | A, D | `test_unserved_kind_short_circuits_to_crd_absent`; the ManagedCluster discovery-failure test |
| 4. Destination gate fails closed; source re-read fresh; ack only against positive absence | C | `TestDestinationObservabilityGate` |
| 5. Hive `preserveOnDelete` behavior unchanged | D | the retained `preserveOnDelete` and ambiguous-relationship tests |
| 6. Clean skip only with no record or prior obligation; every recorded phase resumes | C, D, E | the phase-table resume matrix |
| 7. Positive namespace absence counts as empty only under the fixed-namespace scope proof; `drained` and `completed` only after their full checks | C, E | namespace-absence and boundary-injection tests |
| 8. `completed` asserts proof at its final-read instant, carries `observed_at` and `resourceVersion`, and integrated teardown re-proves live | B, C | the §10.2.1 proof-key matrix — `test_completed_mco_record_requires_an_exact_proof_key_set`, `test_completed_mco_record_accepts_either_approved_drain_proof`, `test_completed_managed_cluster_record_carries_no_drain_proof`, `test_cr_proof_revision_is_never_rebound_by_a_later_write` — mirrored in `tests/unit/test_teardown_records.py`; C3's completed-record proof-key cases; the integrated fresh-gate test |
| 9. Operator provenance durably bound before MCH DELETE, or explicitly unavailable | E | the provenance test set; `test_mch_identity_outcome_must_be_exactly_one` |
| 10. Exclusion only after the complete owner chain; every broken link blocks; rolling-update ReplicaSets accepted only to the same UID | E | matrix rows 1, 8, 9, 10, 11, 12 |
| 11. Prefix spoofing cannot change classification; unavailable identity means only a verified empty list satisfies the drain; a replaced Deployment fails closed | E | matrix rows 2–7, 14, 15, 16, 17 |
| 12. Both form factors keep semantics, durable fields, bounds, preview mode, changed reporting, reason codes, redaction, RBAC, and negative tests in parity without cross-imports | A–E; F integrated proof | parity vectors plus `test_strict_read_bounds_are_mirrored`; the one execution-result interface of B3.1 with the B3.2 change-truth matrix (`TestActualChangeTruth`) on the Python side and the equivalent B4/C4/D/E collection cases through the B4.1 role-result contract — actual `changed` only after an accepted DELETE, `false` for noop, resume-without-mutation, check mode, and dry run, with prediction only in `would_change`; the shared malformed-fixture parity test; constants parity; RBAC parity; matrix rows 18, 19, 20; F scenarios |

## 17.3 Amendment criteria A1 through A9

| Criterion | PR | Tests | Gate |
| --- | --- | --- | --- |
| A1 shared strict-read contract in both form factors; R4-04 Task 0 Step 2 satisfiable verbatim | A | §9.6 mapping table, including its mandatory-`limit`/bounded-call, 410-restart, malformed-`items`, and canonical-resource-name rows. A1 closes only when every §9.6 row has a passing named test on **both** sides | parity vectors + collection surfaces |
| A2 no `failed_when: false` on the collection MCH provenance, ownership, wait, or final-verification paths; unverifiable read fails the play; pins inverted | E | inverted `test_decommission_role_contracts.py:336-350` and `test_ansible_resilience_contracts.py:485` | collection unit + scenario |
| A3 collection summary artifact reports the real aggregated outcome | B | `test_summary_status_is_not_hardcoded`; `test_a_failed_substep_produces_a_failed_status` | collection unit |
| A4 both merged read-outcome consumers pass their runtime lanes unchanged | A | `test_r3_02_compactor_runtime.py`, `test_r3_02_activation_runtime.py` | collection integration |
| A5 the §7 outcome table is observable in both form factors, with banner cancellation distinct and dry-run recording no outcome | B | `TestDecommissionOutcomes`; cancellation/CLI/truthiness tests; `test_decommission_outcome_vocabulary_parity`; dry-run state/mutation assertions | root + collection |
| A6 operator-prefix drift closed; every shared constant enforced by the parity test | E | `tests/test_constants_parity.py` with the prefix and reason-code pairs | parity |
| A7 Python teardown exists exactly once; both callers drive it with their semantics preserved | C | consolidation regression tests | root |
| A8 the durable-field table is exhaustive; full-reset loss documented operator-facing before destructive records become reachable; `reset_from` preserves and revalidates | B, C | the reset and `reset_from` scenario cases; C documentation guardrails | collection scenario + docs |
| A9 native check mode safe end to end; role-level `changed` false; prediction reported as `would_change` | B, C, D, E; F integrated proof | the B3.1 execution interface and the B3.2 change-truth table are the mechanism: `SubstepExecution(outcome, changed)` is the only executor result in B/C/D/E, `DecommissionResult.changed` aggregates it monotonically, and prediction stays in `would_change`. Tests: `TestActualChangeTruth`; B4's check-mode aggregation and checkpoint tests through the B4.1 contract; each C/D/E family's mutation/writer/prediction tests; F `test_decommission_check_mode.py`, `test_decommission_dry_run.py`, and scenarios | per-PR unit/scenario + F composition proof |

---

# 18. Documentation update map

Documentation lands in the PR whose behavior it describes. Nothing is deferred to a documentation
sweep.

| Document | PR A | PR B | PR C | PR D | PR E | PR F |
| --- | --- | --- | --- | --- | --- | --- |
| `CHANGELOG.md` `## [Unreleased]` | yes | yes | yes | yes | yes | consistency audit only |
| `README.md` and its Mermaid diagrams | no | no | MCO guarded flow/gate where represented | ManagedCluster flow where represented | MCH completion/identity flow | consistency audit only |
| `docs/operations/usage.md` | no | cancellation/refusal, exit status, `changed`/`would_change` | ack/gate/check mode and the full-reset warning | strict inventory/check mode | identity/completion/check mode | consistency audit only; SSA-02 boundary if the document already hosts it |
| `docs/operations/quickref.md` | no | no | the ack flag | no | no | consistency audit only |
| `docs/reference/validation-rules.md` | no | no | the ack flag's cross-argument rules | no | no | consistency audit only |
| `docs/development/architecture.md` and diagrams | strict-read seam | durable teardown records and result model | teardown owner, phase machine, check-mode boundary, reset warning | ManagedCluster strict/guarded flow where represented | classification/completion boundary | consistency audit only |
| `docs/ansible-collection/parity-matrix.md` | strict-read row | outcome/check-mode row | MCO decommission row | ManagedCluster decommission row | **replace** the "warns if ACM workload pods remain" text with fail-closed completion | consistency audit only |
| `docs/ansible-collection/behavior-map.md` | strict-read and canonical resource-name mapping | state/result row | guarded-delete and durable-phase boundaries | ManagedCluster strict/guarded mapping | classification boundary | consistency audit only |
| `ansible_collections/.../docs/coexistence.md` | no | outcome parity where described | gate/check-mode parity | ManagedCluster parity where described | classification parity | consistency audit only |
| `ansible_collections/.../docs/variable-reference.md` | no | no | the ack variable | no | no | consistency audit only |
| `ansible_collections/.../docs/cli-migration-map.md` | no | no | the ack flag mapping | no | no | consistency audit only |
| `ansible_collections/.../examples/group_vars/all.yml` | no | no | the ack variable | no | no | consistency audit only |
| `ansible_collections/.../README.md` | no | outcome semantics where described | MCO gate/guarded flow | ManagedCluster strict/guarded flow | MCH identity/completion | consistency audit only |
| `docs/deployment/rbac-requirements.md` | no | no | yes | yes | yes | consistency audit only |
| `docs/deployment/rbac-deployment.md` | no | no | yes | yes | yes | consistency audit only |
| `docs/development/rbac-implementation.md` | no | no | yes | yes | yes | consistency audit only |
| `docs/deployment/rbac-live-certification.md` | no | no | required SAR/read/delete inventory only; no run claim | required inventory only; no run claim | required inventory only; no run claim | consistency audit only |
| scenario and test-migration catalogs | no | with B cases | with C cases | with D cases | with E cases | integrated proof entries only |

The owner PR must update any additional current document made false by its behavior even if the
row says "where represented"; that qualifier means the document may not discuss the behavior, not
that an inaccurate statement may remain. F is a cross-check, never a first-publication escape
hatch.

**Protected files.** `docs/ACM_SWITCHOVER_RUNBOOK.md` and `.claude/skills/**` are **not** planned
for modification by any task. Current assessment: no change is anticipated, because the runbook
documents the manual switchover procedure while this slice changes the tool's internal proof
obligations. If implementation demonstrates that a protected change is genuinely required, that
task is `OPERATOR_APPROVAL_REQUIRED`: stop, present the line-by-line diff, and obtain explicit
operator approval. Nothing in this plan pre-authorizes such an edit, and the runbook-to-SKILLS
sync obligation would apply to any approved change.

**Release governance.** Every PR here is ordinary development work. None changes a released
version identifier and none creates a release tag; the synchronized version bump belongs to a
later, explicitly scoped release PR.

---

# 19. Cross-slice boundaries

## 19.1 R4-04 — consumer, not co-implemented

R4-04 consumes the shared strict-read contract that R4-03 owns. **PR A must merge before R4-04's
Task 0 Step 2 can pass**, and §9.6 maps that checklist item by item.

Explicitly out of scope here: no R4-04 Restore behavior, no migration-evidence journal, no
Backup freeze, no `acm_restore_guarded_mutation`. R4-04's Restore-scoped guarded mutation uses UID
**and** resourceVersion with patch and delete semantics; R4-03's teardown deletes use UID only.
The two collection modules have different ownership boundaries and remain separate (amendment §8
item 1); intra-collection `module_utils` reuse is permitted where natural.

**One Python primitive, not two.** `delete_custom_resource_preconditioned` is written with `uid`
required and `resource_version` optional precisely so that whichever slice merges first
implements it and the other consumes it. If R4-04 somehow merges first, PR C consumes its
primitive instead of adding a second one. Two parallel preconditioned-delete primitives in one
form factor would violate the DRY ownership rule.

**No second read algebra.** R4-04 must not create one, and PR A must not leave one behind. Task A4
extends the single collection module; Task A5 holds both surfaces to one vector set.

## 19.2 SSA-02 — complementary, not absorbed

**R4-03 binds resource identity. SSA-02 binds target-hub identity. Neither replaces the other.**

Not absorbed here: wrong-target and hub-identity checks, expected-hub-UID confirmation, and the
embedded RBAC recheck. SSA-02 remains `planned` with no design document.

**This plan does not claim R4-03 makes standalone or non-interactive decommission safe for live
certification.** The tracker holds SSA-P1 at "P1 (conditional: before next standalone or
non-interactive decommission)", and that gate stands regardless of R4-03's completion proofs:
`--skip-rbac-validation` plus `--non-interactive` still removes every remaining gate on a
standalone run, and nothing in this slice adds a wrong-target check. Live standalone and
non-interactive decommission use stays gated on SSA-02.

The boundary is **tested, not assumed**: §16.2 item 10 requires a negative test asserting that no
wrong-context or wrong-hub target protection exists in the R4-03 surface, so a future reader
cannot mistake resource-identity binding for target-hub binding. Task F2 documents the same
boundary operator-facing.

## 19.3 R4-05 — reset and locking hardening stays out

Not implemented here: `--reset-state` under lock, narrowed `--force`, Lease-based per-hub locking,
crash markers, or any other general state-integrity residual.

The reset limitation is **documented and tested as current behavior in PR C, before the first
destructive partial record can be left**: a full reset destroys
teardown records, so a post-reset rerun that finds a CR absent is indistinguishable from
never-attempted and takes the clean-skip path. Task B4 asserts exactly that, deliberately, so the
R4-05 coordination remains visible rather than being silently mitigated inside R4-03. Task C7
states the operator-facing consequence; F only checks consistency.

## 19.4 R4-02 — the unrestored auto-import gate stays out

The decommission gate for unrestored auto-import transactions (R4-B4) composes in front of
integrated decommission and remains R4-02-owned. No task here implements it.

## 19.5 Hive `preserveOnDelete`

The separate authorization TOCTOU around the `preserveOnDelete` check is an explicit non-goal and
is not redesigned. PR D retains today's behavior and its messages; the only change is that the
ClusterDeployment inventory read becomes strict, so a missing Hive CRD no longer presents as "no
ClusterDeployments".

## 19.6 Release validation and lab controller

No task adds or modifies anything under `tests/release/`, and no task touches the lab controller,
unless the operator separately expands scope. **No fake, dry-run, static-fixture, or local-harness
result produced by any task in this plan is live certification evidence**, and no verification
step in §21 may be described as one.

---

# 20. Failure, recovery, and resume considerations

| Situation | Required behavior | Where |
| --- | --- | --- |
| Durable write fails before DELETE | DELETE is not issued; the run fails with the write error | B1, B2, E |
| Crash after `delete_started`, resource now absent | Resume from the record; the drain and final verification still run; absence never creates a clean skip | C3, D, E |
| Crash after `delete_started`, same name with a new UID | Fatal before DELETE; the recorded UID is reused, never rebound; the replacement is left intact | B1, C3 |
| Drain timeout, then the CRD, CR, or namespace disappears | The prior drain obligation is not laundered; the record's phase drives the rerun | C3 |
| Ambiguous or unobtainable proof | Record `recovery_required`; no mutation and no success transition until a later strict rerun obtains the missing proof | C3, E |
| Recorded operator Deployment 404 or replaced | `recovery_required`; no later Deployment is adopted | E |
| Any strict read returns `error` | Fatal; never treated as absence or as an empty inventory | A, C, D, E |
| Malformed durable record | Fail closed before any mutation or clean-skip decision | B1, B2 |
| Full reset between a failed drain and its rerun | The drain obligation's memory is forfeited and the rerun clean-skips — documented in C when first reachable, tested as current behavior, R4-05-owned | B4, C7 |
| `reset_from` | Retains `operational_data`, therefore retains the records, and revalidates rather than laundering them | B4 |
| Resume of an integrated run | The destination gate re-runs its fresh reads unconditionally; no stored gate result exists to reuse | C5 |
| Top-level cancellation | Explicit `cancelled=True`, `.succeeded == False`, all requested work not attempted, no substep invoked, never persisted | B3 |
| Substep refusal | Distinct `REFUSED`; ends the run; never persisted; the summary is output | B3 |
| Dry-run or check mode followed by a live run | Each owning PR B/C/D/E leaves no record, identity, or phase and forces the live run to re-read; F proves composition | B3–B5, C3–C4, D, E, F1 |

---

# 21. Full verification and release-readiness gates

Every PR runs the gates its own edit invalidates — not a habitual subset and not a habitual
superset. The authoritative gate inventory is [`docs/development/testing.md`](../development/testing.md)
and the workflow files under `.github/workflows/` are ground truth.

## 21.1 Per-PR gate selection

| PR | Changed surface | Gates |
| --- | --- | --- |
| A | dual-supported, parity-sensitive | root suite; collection surfaces 3–7 in both lanes; parity and static-contract tests; quality and security gates; documentation guardrails |
| B | dual-supported, parity-sensitive | same as A |
| C | dual-supported, parity-sensitive, **RBAC** | same as A, plus the complete RBAC cross-surface gate |
| D | dual-supported, parity-sensitive, **RBAC** | same as C |
| E | dual-supported, parity-sensitive, **RBAC** | same as C |
| F | integrated tests and consistency audit only | focused integrated root/collection scenario tests; parity/static contracts; documentation/scope guardrails; no production safety repair |

No PR in this plan changes the release-validation framework, so `tests/release` is **not** part of
any PR's required gate set. If a future review shows a release helper is affected, first verify
that neither `ACM_RELEASE_PROFILE` nor `PYTEST_ADDOPTS` resolves a profile, then run
`python -m pytest tests/release -q`, and never cite its output as live certification evidence.

## 21.2 Root surface

```bash
python -m pytest tests/ --ignore=tests/release -v -m "not e2e"
```

Root `tests/` must stay import-safe without `ansible-core`: any collection import lives inside a
function, never at module import time.

## 21.3 Collection surfaces 3 through 7

Run every command in the "Commands by surface" block of `docs/development/testing.md` for surfaces
3, 4, 5, 6, and 7, preceded by the resolved-dependency compatibility step, in **both**
repository-tested lanes defined by the compatibility authority: `ansible-core` 2.16 on Python 3.11
and `ansible-core` 2.21 on Python 3.12. Surface 6's `set -o pipefail` and its
"does not support Ansible version" backstop are load-bearing and are not optional.

A partial run is never described as a full suite: collection integration, scenario, syntax, and
build are separate surfaces and must be named as such.

## 21.4 Parity and static-contract gates

```bash
python -m pytest tests/test_constants_parity.py tests/test_checkpoint_state_parity.py \
  tests/test_rbac_collection_parity.py tests/test_validation_parity.py \
  tests/test_strict_read_parity.py tests/test_r3_02_fail_closed_parity.py \
  tests/test_api_literal_guardrails.py -q
```

## 21.5 Quality and security gates

Reproduce CI exactly; never substitute `.` for the black and isort path lists, and never run
repo-wide formatting that can walk `.venv/`, `.claude/worktrees/`, `graphify-out/`, `htmlcov/`,
`review/`, or `completions/`.

```bash
black --check --line-length 120 --diff acm_switchover.py lib modules \
  ansible_collections/tomazb/acm_switchover/plugins \
  ansible_collections/tomazb/acm_switchover/tests tests
isort --check-only --profile black --line-length 120 acm_switchover.py lib modules \
  ansible_collections/tomazb/acm_switchover/plugins \
  ansible_collections/tomazb/acm_switchover/tests tests
flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics
mypy --explicit-package-bases acm_switchover.py lib/ modules/ \
  ansible_collections/tomazb/acm_switchover/plugins \
  ansible_collections/tomazb/acm_switchover/tests \
  --ignore-missing-imports --no-strict-optional
bandit --ini .bandit -f txt
```

## 21.6 Module documentation gate

For every PR that adds or changes a collection module's `DOCUMENTATION` or `RETURN`, run from
`ansible_collections/tomazb/acm_switchover`:

```bash
ansible-test sanity --test validate-modules --python 3.12 plugins/modules/<module>.py
```

**This repository has no `tests/sanity/` directory and no sanity lane in
`.github/workflows/ansible-collection-foundation.yml`.** This is a design-required builder-run
gate (amendment §16 item 2), not a CI gate, and no report may present it as one.

## 21.7 Documentation, scope, and protected-file gates

```bash
python -m pytest tests/test_documentation_guardrails.py tests/test_ci_guardrails.py \
  tests/test_ci_hardening_guardrails.py -q
git diff --check origin/ansible...HEAD
git diff --name-only origin/ansible...HEAD
git diff --name-only origin/ansible...HEAD -- docs/ACM_SWITCHOVER_RUNBOOK.md '.claude/skills/**'
```

The protected-file command must print nothing. Resolve every changed relative link in every
changed document.

## 21.8 RBAC cross-surface gate, for PRs C, D, and E

```bash
python -m pytest tests/test_rbac_validator.py tests/test_rbac_integration.py \
  tests/test_rbac_collection_parity.py tests/properties/test_rbac_properties.py \
  tests/test_documentation_guardrails.py -q
PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/ -q -k rbac
```

Plus the manifest and chart consistency checks, and the negative authorization tests required by
§14 surface 14.

## 21.9 Exact-head convergence

Freeze the candidate head, record that SHA, and run every required validator and reviewer against
that same head. Confirm exact-head CI is green immediately before any merge-readiness claim.

---

# 22. Builder, validator, and resolver workflow for each runtime PR

Each of PRs A through F runs the full governed three-role workflow.

**Builder**

1. Perform the mandatory start gate: fetch current `origin/ansible`; confirm repository identity
   and target branch; record base SHA, head SHA, merge base, declared scope, worktree
   cleanliness, and the protected-file boundary before the first edit.
2. Work in one isolated `.claude/worktrees/<slice>` worktree on one branch per PR, based on the
   then-current `origin/ansible` — never on this documentation branch.
3. Add one `thermos-resolution-plan.md` row with the branch and worktree **when that PR is
   actually created**. Do not pre-populate tracker rows from this plan.
4. Follow TDD for every task; run the gates the edit invalidates.
5. Run the Builder Simplification Gate before freezing the head or opening the PR, and record what
   was simplified or that no safe in-scope simplification was found — in both the builder report
   and the PR description.
6. Run the `code-review` skill against the completed branch changes before creating the PR;
   address critical and warning findings or record a concrete technical reason; re-run after
   review-driven changes.

**Independent validator**

7. Validate the frozen head from a **clean checkout it did not build**, against the governing
   acceptance criteria only. Hard-fail on missing prerequisites rather than proceeding on
   assumption. Repeat the start gate and the protected-file diff independently. Re-fetch and
   confirm the head and the base relationship are unchanged before publishing. Publish the
   terminal result as a new top-level PR comment recording the verdict, base, head, and merge-base
   SHAs, changed-file scope, protected-file result, applicable validation results, CI status,
   review-thread status, merge-readiness assessment, and confidence. Its only permitted PR
   mutation is that comment.

**PR-comment resolver and final validator**

8. Fetch every top-level comment, review, and review thread; validate each actionable comment
   against the codebase before changing code; address it with code, docs, or tests, or reply with
   a concrete technical reason; rerun every invalidated gate; resolve a thread only after the fix
   or reply is pushed; re-fetch after addressing feedback.
9. Apply the Reviewer Complexity Firewall: a remediation that would add or materially expand
   persisted state, a state-transition or checkpoint contract, an approval action, configuration
   surface, compatibility behavior, a recovery protocol, an abstraction layer, an execution mode,
   a dependency, or a security mechanism returns through this plan's design authority rather than
   entering an ordinary review fix loop. Routing a remediation back does not downgrade the
   underlying finding.
10. Confirm terminal PASS on the frozen head, all threads resolved, and required CI green. **PASS
    does not authorize merge**; merge remains an operator decision.

Findings are dispositioned against the governing acceptance gate: blocking in scope, valid but
deferred with a tracker reference, non-blocking observation with a reply only, or invalid with a
concrete technical reason. A deferral is complete only when filed in the receiving tracker.

---

# 23. Risks and rejected sequencing alternatives

## 23.1 Rejected sequencing alternatives

| Alternative | Why rejected |
| --- | --- |
| Four PRs, folding state and outcomes into the first teardown PR | PR C would then carry the delete primitive, the phase machine, the consolidation, the destination gate, the outcome vocabulary, **and** the first RBAC change. That is not independently reviewable, and a reviewer could not reject the outcome vocabulary while approving the teardown |
| Three PRs — strict read, everything else, docs | The "everything else" PR would introduce three RBAC changes at once and three resource families' teardown in one diff |
| One PR per resource family with no shared phase machine | Produces three teardown algorithms, reintroducing the GLM-H6 duplication this slice exists to remove |
| Merging PR D and PR E as one "remaining teardown" PR | MCH identity is the largest and highest-risk surface in the slice and carries a 20-case matrix; bundling it with ManagedClusters makes the riskiest change harder to review, for no safety gain |
| Deferring RBAC to a single trailing RBAC PR | Would put live API calls in front of their permissions — an explicit violation of the timing rule |
| Deferring the outcome vocabulary to the final PR | PRs C, D, and E each need to report a substep outcome; deferring it would force a temporary parallel result shape |
| Deferring check-mode/dry-run guards, actual-change honesty, or prediction semantics to F | Would make B/C/D/E unsafe on the day they merge. Each writer, mutation, result field, and predictor lands with its own guard and tests; F is integrated proof only |
| Deferring reset or behavior documentation to F | Would leave operator guidance false while destructive partial records and changed behavior are already reachable; C publishes the reset warning and every behavior PR updates its invalidated surfaces |
| A feature flag to let teardown changes land ahead of their permissions or their parity counterpart | Prohibited: no feature flag or compatibility mode is introduced merely to make splitting easier |

## 23.2 Design alternatives already rejected by the approved design

Restated so no implementation task reopens them: a second collection read helper; a
decommission-private strict list; gating on the recorded `secondary_has_observability`; name-based
delete with before-and-after reads; UID **plus** resourceVersion preconditions for decommission
deletes; absorbing SSA-02; cross-form-factor teardown sharing for GLM-H6; adding
`namespace_absent` as a module status; and persisting the destination-gate result.

## 23.3 Risks carried into implementation

| Risk | Mitigation |
| --- | --- |
| Read-outcome extension regresses a merged R3-02 consumer | Explicit inversion of the positive discovery-miss expectations; strengthening-only list semantics; mandatory reruns of both runtime consumer lanes (Task A6 step 1), with an instruction to stop rather than edit expectations if either fails |
| The discovery prover's own request behaves differently across clusters | The prover requires an explicit success and decode; anything else is `error`. Version-invariant by construction (§15.3) |
| Reset laundering | Accepted, tested as current behavior, R4-05-owned, and documented operator-facing in PR C before the first destructive partial record is reachable (§19.3) |
| OLM or CSV contract drift beyond ACM 2.17 | Fail-closed by design; the audited range is the widest any repository authority claims (§15.4) |
| Two guarded-mutation modules in the collection | Accepted as different ownership boundaries with intra-collection `module_utils` reuse; revisit only if implementation shows the boundaries collapsing (§19.1) |
| PR E's size | Mitigated by its own PR, a declared fixture-driven matrix, and a classifier with one code path and no prefixed-Pod special case |
| A stale tracker sentence routes R4-04's Restore cleanup through `acm_uid_guarded_delete` | Does not affect R4-03 correctness; needs a tracker-reconciliation edit in a slice authorized to touch the tracker (amendment §19). No task here edits the tracker except to add its own PR row |

**Post-repair simplification check.** The strict-read value/helper split remains one value type,
one Python transport owner, and the existing single Collection module. Canonical plural flow adds
one required `resource_name` input rather than an inflector or registry. The bounded-read policy is
three mirrored integers plus the client timeout that `KubeClient` already owns — no new
configuration surface and no second timeout mechanism. `DecommissionResult` adds only the three
observable facts consumers need (`cancelled`, actual `changed`, and `would_change`) and no preview
marker; `SubstepExecution` is one two-field frozen value object replacing an ad-hoc tuple, and it is
the only executor result across B, C, D, and E. The completion proof stays inside the approved
`resource_versions` field as an explicit, enumerated key rule rather than a new durable field.
`TeardownSpec` remains limited to resource, drain, and classifier inputs; transport helpers classify
reads while decommission owns policy. PR F is test/audit-only. No feature flag, compatibility mode,
recovery protocol, dependency, or second abstraction was introduced by this repair.

---

# 24. Implementation authorization gate

**This implementation plan does not authorize runtime implementation. Runtime implementation
requires separate explicit operator approval after this plan passes independent validation and
PR-comment resolution.**

Approval of this plan does not bypass any of the following. Runtime implementation may begin only
in a new governed builder session that:

1. re-fetches current `origin/ansible` and repeats the mandatory start gate from
   [`AGENTS.md`](../../AGENTS.md);
2. confirms that `origin/ansible` still contains the approved amendment and this plan, and that no
   intervening change has materially affected the R4-03 authorities, the decommission source or
   tests, the strict-read plumbing, RunRecord or checkpoint state, RBAC, parity, R4-04's
   dependency, compatibility, or `AGENTS.md`; if any has, it stops and returns to the operator
   rather than rebasing the change away;
3. creates the PR A isolated branch and worktree from that exact base, and adds its tracker row
   only when the PR is actually created;
4. uses `superpowers:subagent-driven-development` or `superpowers:executing-plans` with the TDD
   discipline and verification checkpoints in §16 and §21;
5. carries no stale base SHA, test result, or implementation assumption from this documentation
   branch into the builder session;
6. runs the builder, independent validator, and PR-comment-resolver workflow in §22 for every PR,
   terminating under the `AGENTS.md` Terminal Validation and Review Convergence rules.

A terminal PASS on a frozen head does not itself authorize merge; merge remains an operator
decision under the Pull Request Merge Gate.
