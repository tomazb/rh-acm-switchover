# R4-03 Decommission Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: use `superpowers:subagent-driven-development` for reviewed task-by-task work in one session, or `superpowers:executing-plans` in a separate session. Every builder, independent validator, and PR-comment resolver must also read current `AGENTS.md` and the directly relevant repository authorities before acting.

**Status:** authored against the operator-approved R4-03 amended design at exact head
`177554f6e598461571e3785173b52feabdfe4c52`; awaiting independent validation. **This document
does not authorize runtime implementation.**

**Goal:** Implement the accepted R4-03 decommission-completion design in both production form
factors so that every MCO, MCH, and ManagedCluster teardown is bound to the observed object
identity by a server-side UID precondition and proven complete before it reports success; a
refused destructive substep can never yield a successful decommission; inventory reads
distinguish empty from unverifiable; integrated decommission cannot silently end observability
continuity; and MCH drain exclusion is decided by a complete controller-owner chain to a durably
recorded operator Deployment UID rather than a name prefix.

**Architecture:** One shared strict Kubernetes read algebra owned by R4-03 and consumed by
R4-04 — `lib/strict_read.py` plus strict methods on `lib/kube_client.py` in Python, and the
existing `acm_k8s_read_outcome` module extended in the collection. Durable teardown state flows
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
6. An interactive refusal aborts the run and exits non-zero.
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
2. **Absence is positive or it is not absence.** `object_absent` requires a successful discovery
   followed by a 404 on the named object. Kind absence requires a positive determination from a
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
   identity, no phase, and no completion, and report `changed: false`.
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
        | PR F  Check-mode closure, docs, parity, gates |
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
| D and E to F | F asserts end-to-end check-mode and closes the documentation contract |

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
| F | Check-mode closure, documentation and parity | both | none | documentation only |

**Independent reviewability.** A is a read algebra with no destructive caller. B is a state and
result-vocabulary change with no new API call. C, D, and E each own exactly one resource family's
teardown. F changes no teardown algorithm.

**Every intermediate merged state is fail-closed.**

| After | State |
| --- | --- |
| A | Read semantics strengthen only: outcomes that were silently empty or silently partial become `error`. Both merged R3-02 consumers already fail closed on non-`ok`. |
| B | Refusal now aborts and exits non-zero; the collection artifact reports the real aggregated status. Teardown records exist but no teardown yet writes one, so no clean-skip decision can be laundered. |
| C | MCO teardown is guarded and proven; the destination gate blocks. ManagedCluster and MCH teardown remain exactly as today and claim nothing they cannot prove — R4-C1 stays open and is not represented as closed. |
| D | ManagedCluster inventory and deletion are strict and guarded. MCH remains as today. |
| E | MCH completion is proven; R4-C1 closes. |
| F | Preview paths are provably non-persisting end to end and the documentation contract matches behavior. |

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
obligation from the 2026-08-02 triage: a 404 proves absence only after successful discovery.

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

**Interfaces produced:** `KubeClient._discovery_serves(group, version, plural) -> StrictReadOutcome`
returning an `ITEMS` outcome with an empty list when the kind is served, `CRD_ABSENT` when the
successful discovery response positively lacks it, and `ERROR` otherwise.

**Intended behavior:** One `GET` of `/apis/{group}/{version}`, or `/api/v1` when `group` is empty,
issued through this instance's `ApiClient` with the instance request timeout. The response must
decode to a mapping carrying a list-valued `resources` key. The kind is served when some entry's
`name` equals `plural`.

**Failure behavior:** Non-2xx, timeout, transport failure, undecodable body, missing or non-list
`resources`, or non-mapping entries all return `ERROR` with
`STRICT_READ_REASON_DISCOVERY_UNVERIFIABLE`. A 404 on the discovery path is `CRD_ABSENT` only
because a 404 there is the API server positively answering that the group/version is not served;
every other status is `ERROR`.

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
        call = Mock(return_value={"resources": [{"name": "multiclusterhubs", "kind": "MultiClusterHub"}]})
        client = self._client(call)
        outcome = client._discovery_serves("operator.open-cluster-management.io", "v1", "multiclusterhubs")
        assert outcome.status is StrictReadStatus.ITEMS

    def test_absent_kind_in_a_successful_response_is_crd_absent(self):
        call = Mock(return_value={"resources": [{"name": "somethingelse"}]})
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

    def test_discovery_404_is_positive_kind_absence(self):
        call = Mock(side_effect=ApiException(status=404, reason="Not Found"))
        outcome = self._client(call)._discovery_serves("g", "v1", "p")
        assert outcome.status is StrictReadStatus.CRD_ABSENT

    def test_core_group_uses_the_core_discovery_path(self):
        call = Mock(return_value={"resources": [{"name": "pods"}]})
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
    def _discovery_serves(self, group: str, version: str, plural: str) -> StrictReadOutcome:
        """Positively determine whether one kind is served, or fail closed.

        Kind absence is proven only by a successful, decodable discovery
        response that does not list `plural`. A discovery call that fails,
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
        except ApiException as exc:
            if exc.status == 404:
                return StrictReadOutcome.crd_absent(STRICT_READ_REASON_KIND_NOT_SERVED)
            return StrictReadOutcome.error(STRICT_READ_REASON_DISCOVERY_UNVERIFIABLE)
        except Exception:
            return StrictReadOutcome.error(STRICT_READ_REASON_DISCOVERY_UNVERIFIABLE)

        if not isinstance(response, dict):
            return StrictReadOutcome.error(STRICT_READ_REASON_DISCOVERY_UNVERIFIABLE)
        resources = response.get("resources")
        if not isinstance(resources, list):
            return StrictReadOutcome.error(STRICT_READ_REASON_DISCOVERY_UNVERIFIABLE)
        for entry in resources:
            if not isinstance(entry, dict):
                return StrictReadOutcome.error(STRICT_READ_REASON_DISCOVERY_UNVERIFIABLE)
            if entry.get("name") == plural:
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

**Purpose:** Deliver the two strict operations the July §3 contract defines.

**Files:** Modify `lib/kube_client.py`; modify `tests/test_kube_client.py`.

**Interfaces consumed:** `StrictReadOutcome`, `_discovery_serves`.
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
```

**Intended behavior.** Both prove the kind is served first. The list then pages through
`continue` tokens to exhaustion and returns `ITEMS` only when the final response carried no
outstanding continuation. The named GET returns a single-resource `ITEMS` outcome carrying
`metadata.resourceVersion`, or `OBJECT_ABSENT` on a 404 that followed a successful discovery.

**Failure behavior.** Any page failure fails the whole read as `ERROR` and never returns the
partial prefix. An expired continue token restarts the whole read once and then fails as `ERROR`
rather than truncating. A response whose `items` is missing or not a list, or whose members are
not mappings, is `ERROR` with `STRICT_READ_REASON_MALFORMED_RESPONSE`. Authorization, transport,
TLS, timeout, and decode failures are `ERROR`.

**State implications:** none. **Dry-run implications:** read-only; identical in dry-run.
**Parity implications:** held equal to the collection by Task A5's shared vectors.
**RBAC implications:** none new — `list` and `get` on the target kinds are added by the consuming
PRs C, D, and E where each read first becomes live.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_kube_client.py`:

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
        pages = [{"items": [], "metadata": {"continue": "tok"}}] * 200
        outcome = self._client(list_pages=pages).list_custom_resources_strict("g", "v1", "widgets")
        assert outcome.status is StrictReadStatus.ERROR
        assert outcome.reason == STRICT_READ_REASON_INVENTORY_INCOMPLETE

    def test_expired_continue_token_restarts_once_then_fails_closed(self):
        pages = [
            {"items": [{"metadata": {"name": "a"}}], "metadata": {"continue": "tok"}},
            ApiException(status=410, reason="Gone"),
            {"items": [{"metadata": {"name": "a"}}], "metadata": {"continue": "tok"}},
            ApiException(status=410, reason="Gone"),
        ]
        outcome = self._client(list_pages=pages).list_custom_resources_strict("g", "v1", "widgets")
        assert outcome.status is StrictReadStatus.ERROR

    def test_malformed_items_is_error_not_empty(self):
        outcome = self._client(list_pages=[{"items": "nope", "metadata": {}}]).list_custom_resources_strict(
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
        import inspect

        assert "max_items" not in inspect.signature(KubeClient.list_custom_resources_strict).parameters

    def test_legacy_readers_are_unchanged(self):
        # The strict surface is additive; existing callers keep the current behavior.
        assert KubeClient.list_custom_resources.__doc__ is not None
        assert "max_items" in inspect.signature(KubeClient.list_custom_resources).parameters
```

- [ ] **Step 2: Run and observe the expected failure**

```bash
python -m pytest tests/test_kube_client.py -q -k StrictCustomResourceReads
```

Expected: FAIL — `AttributeError: ... has no attribute 'list_custom_resources_strict'`.

- [ ] **Step 3: Implement**

Add both methods to `lib/kube_client.py`. Neither carries `@api_call` or `@retry_api_call`: the
strict surface owns its own classification and must never have a 404 rewritten underneath it, and
the caller owns the bounded retry budget (July §3, "the primitive itself never polls").

```python
    _STRICT_LIST_MAX_PAGES = 100
    _STRICT_LIST_MAX_RESTARTS = 1

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

        for _ in range(self._STRICT_LIST_MAX_RESTARTS + 1):
            outcome = self._drain_strict_list(group, version, plural, namespace, label_selector)
            if outcome is not _RESTART_READ:
                return outcome
        return StrictReadOutcome.error(STRICT_READ_REASON_INVENTORY_INCOMPLETE)

    def _drain_strict_list(self, group, version, plural, namespace, label_selector):
        items: List[Dict[str, Any]] = []
        continue_token: Optional[str] = None

        for _ in range(self._STRICT_LIST_MAX_PAGES):
            try:
                if namespace:
                    page = self.custom_api.list_namespaced_custom_object(
                        group=group, version=version, namespace=namespace, plural=plural,
                        label_selector=label_selector, _continue=continue_token,
                        **self._request_timeout_kwargs(),
                    )
                else:
                    page = self.custom_api.list_cluster_custom_object(
                        group=group, version=version, plural=plural,
                        label_selector=label_selector, _continue=continue_token,
                        **self._request_timeout_kwargs(),
                    )
            except ApiException as exc:
                # 410 Gone means the continuation expired: restart the whole
                # read rather than returning the prefix already collected.
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

Define the restart sentinel near the module's other private helpers:

```python
_RESTART_READ = object()
```

- [ ] **Step 4: Run and observe the tests pass**

```bash
python -m pytest tests/test_kube_client.py -q -k StrictCustomResourceReads
```

- [ ] **Step 5: Refactor**

`_drain_strict_list` performs exactly one responsibility. If the malformed-response checks read as
repetition, extract one local `_valid_page(page)` predicate inside `lib/kube_client.py`; do not
introduce a new module for it.

- [ ] **Step 6: Broader gate and commit**

```bash
python -m pytest tests/test_kube_client.py tests/test_strict_read.py -q
git add lib/kube_client.py tests/test_kube_client.py
git commit -m "feat: add strict custom-resource list and named get"
```

## Task A4: Extend the collection read-outcome seam

**Purpose:** Give the collection the same algebra by extending the merged R3-02 module, with no
second read abstraction.

**Files:** Modify `plugins/modules/acm_k8s_read_outcome.py`; modify
`tests/unit/test_k8s_read_outcome.py`; modify `tests/integration/test_k8s_read_outcome_runtime.py`.

**Interfaces produced:** `read_status` gains `kind_not_served`; list mode becomes
completeness-proving.

**Intended behavior.**

1. **Complete pagination.** List mode passes `_continue` to `api_client.get(...)` and repeats
   until the response carries no continuation. `ok` therefore asserts a positively complete
   inventory.
2. **Positive kind absence.** When `api_client.resource(kind, api_version)` raises, the module
   does not classify from the exception. It issues its own discovery request for the exact
   group/version through the same client and returns `kind_not_served` only when that request
   succeeds, decodes, and positively lacks the kind. Anything else is `error` (§9.2).
3. **Namespace absence stays composed.** The module gains no namespace-probing mode. A caller
   proves namespace absence with `read_mode: get`, `api_version: v1`, `kind: Namespace`.

**Failure behavior.** Unchanged for every existing failure: sanitized `error`, no raw bodies.

**Check-mode implications.** The module remains read-only and continues to perform its read in
check mode — existing tested behavior, preserved deliberately.

**Parity implications.** `kind_not_served` maps to Python `crd_absent`; complete `resources` maps
to `items`. Held equal by Task A5.

**RBAC implications:** none new.

**Deliberate reclassification.** The existing unit and runtime expectations for the positive
discovery miss currently assert `error`; they are inverted to `kind_not_served`. This is a change
to merged R3-02 code, so both runtime consumer lanes are rerun in Task A6.

- [ ] **Step 1: Write the failing tests**

Add to `ansible_collections/tomazb/acm_switchover/tests/unit/test_k8s_read_outcome.py`:

```python
def test_list_mode_follows_continue_tokens_to_exhaustion(monkeypatch):
    pages = [
        {"kind": "PodList", "items": [{"metadata": {"name": "a"}}], "metadata": {"continue": "tok"}},
        {"kind": "PodList", "items": [{"metadata": {"name": "b"}}], "metadata": {}},
    ]
    calls = []

    def fake_get(resource, **params):
        calls.append(params.get("_continue"))
        return pages[len(calls) - 1]

    result = run_module_with(fake_get=fake_get, read_mode="list", api_version="v1", kind="Pod")
    assert result["read_status"] == "ok"
    assert [r["metadata"]["name"] for r in result["resources"]] == ["a", "b"]
    assert calls == [None, "tok"]


def test_list_mode_page_failure_is_error_and_returns_no_partial_inventory():
    pages = [
        {"kind": "PodList", "items": [{"metadata": {"name": "a"}}], "metadata": {"continue": "tok"}},
        ApiError("boom"),
    ]
    result = run_module_with(fake_get=sequence(pages), read_mode="list", api_version="v1", kind="Pod")
    assert result["read_status"] == "error"
    assert result["resources"] == []


def test_list_mode_outstanding_continuation_at_exit_is_error():
    endless = {"kind": "PodList", "items": [], "metadata": {"continue": "tok"}}
    result = run_module_with(fake_get=lambda *a, **k: endless, read_mode="list", api_version="v1", kind="Pod")
    assert result["read_status"] == "error"


def test_positive_discovery_miss_is_kind_not_served():
    result = run_module_with(
        fake_resource=raises(ResourceNotFoundError("no matches")),
        fake_discovery={"resources": [{"name": "pods"}]},
        read_mode="list",
        api_version="operator.open-cluster-management.io/v1",
        kind="MultiClusterHub",
    )
    assert result["read_status"] == "kind_not_served"


def test_unverifiable_discovery_is_error_not_kind_not_served():
    result = run_module_with(
        fake_resource=raises(ResourceNotFoundError("no matches")),
        fake_discovery=raises(ApiError("503 Service Unavailable")),
        read_mode="list",
        api_version="operator.open-cluster-management.io/v1",
        kind="MultiClusterHub",
    )
    assert result["read_status"] == "error"


def test_undecodable_discovery_is_error_not_kind_not_served():
    result = run_module_with(
        fake_resource=raises(ResourceNotFoundError("no matches")),
        fake_discovery="<html>gateway</html>",
        read_mode="list",
        api_version="g/v1",
        kind="Widget",
    )
    assert result["read_status"] == "error"


def test_return_documentation_lists_every_status():
    import yaml

    from ansible_collections.tomazb.acm_switchover.plugins.modules import acm_k8s_read_outcome

    documented = yaml.safe_load(acm_k8s_read_outcome.RETURN)["read_status"]["choices"]
    assert sorted(documented) == ["error", "kind_not_served", "not_found", "ok"]


def test_module_still_reports_no_namespace_probing_mode():
    from ansible_collections.tomazb.acm_switchover.plugins.modules import acm_k8s_read_outcome

    spec = acm_k8s_read_outcome._argument_spec()
    assert spec["read_mode"]["choices"] == ["get", "list"]
```

Invert the existing positive-discovery-miss expectations in the same file and in
`tests/integration/test_k8s_read_outcome_runtime.py` from `error` to `kind_not_served`. Add the
helpers (`run_module_with`, `sequence`, `raises`) alongside the file's existing fixtures rather
than in a new support module, matching how that file already drives the module.

- [ ] **Step 2: Run and observe the expected failures**

```bash
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_k8s_read_outcome.py -q
```

Expected: FAIL — pagination assertions fail because only one request is made; the
`kind_not_served` assertions fail because the module returns `error`; the RETURN assertion fails
because `choices` lists three statuses.

- [ ] **Step 3: Implement**

In `plugins/modules/acm_k8s_read_outcome.py`:

Update the `RETURN` block's `read_status` description and `choices` to
`[ok, not_found, kind_not_served, error]`, describing `kind_not_served` as "the API group/version
was read successfully and positively does not serve this kind".

Replace the single `api_client.get(resource, **params)` call for list mode with a bounded drain,
and replace the bare `except Exception` around `api_client.resource(...)` with the proof:

```python
_MAX_LIST_PAGES = 100


def _discovery_serves(api_client, api_version: str, kind: str) -> bool | None:
    """True if served, False if positively absent, None if unverifiable.

    The dynamic client's discovery cache substitutes an empty resource list
    for some discovery-fetch failures, and the substituted set differs across
    the supported client range, so a lookup miss alone never proves absence.
    """
    path = f"/apis/{api_version}" if "/" in api_version else f"/api/{api_version}"
    try:
        response = api_client.client.request("GET", path, serialize=False)
        body = json.loads(response.data.decode("utf8"))
    except Exception:
        return None
    if not isinstance(body, dict):
        return None
    resources = body.get("resources")
    if not isinstance(resources, list):
        return None
    for entry in resources:
        if not isinstance(entry, dict):
            return None
        if entry.get("kind") == kind or entry.get("name") == kind.lower() + "s":
            return True
    return False


def _drain_list(api_client, resource, params) -> tuple[list[dict] | None, str]:
    """Return (resources, status). resources is None when status != 'ok'."""
    collected: list[dict] = []
    continue_token = None
    for _ in range(_MAX_LIST_PAGES):
        page_params = dict(params)
        if continue_token:
            page_params["_continue"] = continue_token
        try:
            raw = api_client.get(resource, **page_params)
        except Exception:
            return None, "error"
        mapping = _to_mapping(raw)
        if mapping is None:
            return None, "error"
        page = _normalize_resources("list", mapping)
        if page is None:
            return None, "error"
        collected.extend(page)
        metadata = mapping.get("metadata")
        if not isinstance(metadata, dict):
            return None, "error"
        continue_token = metadata.get("continue") or None
        if not continue_token:
            return collected, "ok"
    return None, "error"
```

Wire them into `run_module`: replace the `resource` lookup's `except Exception: _exit_outcome(module, "error")` with

```python
    try:
        resource = api_client.resource(module.params["kind"], module.params["api_version"])
    except Exception:
        served = _discovery_serves(api_client, module.params["api_version"], module.params["kind"])
        _exit_outcome(module, "kind_not_served" if served is False else "error")
        return
```

and replace the list branch's single `get` with `_drain_list`. The `get` branch is unchanged.
Add `import json` to the module's imports.

- [ ] **Step 4: Run and observe the tests pass**

```bash
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_k8s_read_outcome.py -q
export ANSIBLE_COLLECTIONS_PATH="$(pwd):${HOME}/.ansible/collections"
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/integration/test_k8s_read_outcome_runtime.py -q
```

- [ ] **Step 5: Refactor**

`_drain_list` and `_discovery_serves` must not log or return any part of a response body. Confirm
the module still exits only through `_exit_outcome`.

- [ ] **Step 6: Module documentation gate**

```bash
export ANSIBLE_COLLECTIONS_PATH="$(pwd):${HOME}/.ansible/collections"
ansible-test sanity --test validate-modules \
  --python 3.12 plugins/modules/acm_k8s_read_outcome.py
```

Run this from `ansible_collections/tomazb/acm_switchover`. Expected: PASS, proving `RETURN` matches
the module's real status vocabulary. **This repository has no `tests/sanity/` directory and no
sanity CI lane** — this is a design-required builder-run gate (amendment §16 item 2), not a CI
gate, and it must not be described as one.

- [ ] **Step 7: Commit**

```bash
git add ansible_collections/tomazb/acm_switchover/plugins/modules/acm_k8s_read_outcome.py \
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
    ("malformed_items", "malformed response", StrictReadStatus.ERROR, "error"),
    ("later_page_failure", "truncation / incomplete", StrictReadStatus.ERROR, "error"),
    ("outstanding_continuation", "truncation / incomplete", StrictReadStatus.ERROR, "error"),
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

`run_python_vector` and `run_collection_vector` are defined in this file and build their fakes
from the same vector id, so a vector that is added on one side and forgotten on the other fails.
Root `tests/` must import the collection module lazily inside `run_collection_vector` so the file
stays import-safe without `ansible-core` (`AGENTS.md` standing CI constraint).

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
```

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

Expected: PASS with no expectation edits. Both consumers read always-served core kinds (`Pod`,
`ConfigMap`), so `kind_not_served` is unreachable for them; `scale_observability.yml` already
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
target. Run:

```bash
python -m pytest tests/test_documentation_guardrails.py tests/test_ci_guardrails.py -q
```

- [ ] **Step 7: Simplification gate, then open PR A**

Review every changed file and its immediate collaborators for avoidable complexity introduced by
this change. Record what was simplified, or that no safe in-scope simplification was identified,
in the builder report and the PR description. Rerun the targeted gates after any simplification.

## 9.6 R4-04 Task 0 Step 2 gate mapping

PR A alone must satisfy this checklist verbatim. Each row names the evidence.

| R4-04 Task 0 Step 2 requirement | Satisfied by |
| --- | --- |
| Python strict list/read interface | `KubeClient.list_custom_resources_strict`, `KubeClient.get_custom_resource_strict` (Task A3) |
| Test: true empty | `test_true_empty_list_is_a_proven_complete_inventory`; parity vector `true_empty` |
| Test: 404/discovery failure | `test_unserved_kind_short_circuits_to_crd_absent`, `test_discovery_service_unavailable_is_error_not_absence`, `test_list_404_on_a_served_kind_is_error_not_absence`; vectors `kind_not_served`, `discovery_unverifiable` |
| Test: malformed `items` | `test_malformed_items_is_error_not_empty`; vector `malformed_items` |
| Test: transport/auth failure | `test_authorization_failure_is_error_not_absence`; vectors `authorization_failure`, `transport_failure` |
| Test: complete pagination | `test_complete_multi_page_inventory_is_joined`, `test_later_page_failure_fails_the_whole_read`, `test_outstanding_continuation_at_exit_is_incomplete`; vectors `complete_pagination`, `later_page_failure`, `outstanding_continuation` |
| Collection has the corresponding complete list outcome, extending `acm_k8s_read_outcome` rather than adding another abstraction | Task A4; `test_module_still_reports_no_namespace_probing_mode` proves no mode was added |
| Merged on `origin/ansible` | PR A merges before R4-04 execution begins; R4-04 re-runs its own Task 0 |
| No competing read algebra | Task A4 extends the single module; Task A5 holds both surfaces to one vector set |

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
"observability.open-cluster-management.io/v1beta2/MultiClusterObservability//observability":
  expected_uid: "8f0a...-uid"          # immutable once written; never rebound
  phase: "drain_pending"               # delete_started|cr_absent|drain_pending|drained|completed|recovery_required
  observed_at: "2026-09-04T10:11:12Z"  # required only at phase == completed
  resource_versions:                   # required only at phase == completed
    cr: "88214"
    pods: "88219"
"operator.open-cluster-management.io/v1/MultiClusterHub/open-cluster-management/multiclusterhub":
  expected_uid: "2b71...-uid"
  phase: "delete_started"
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

The `operator_identity_unavailable` alternative carries `reason`, `discovery_method`,
`captured_at`, `mch_teardown_key`, `mch_expected_uid`, and `evidence_summary`, per July §1a.

Validation rules, enforced by `RunRecord` on read and by `checkpoint.py` on read:

| Condition | Result |
| --- | --- |
| Missing or empty key component, missing or empty `expected_uid`, or unknown `phase` | malformed — fail closed before any mutation or clean-skip decision |
| `phase == completed` without `observed_at` or without `resource_versions` | malformed — fail closed |
| Both `operator_deployment` and `operator_identity_unavailable`, or neither, on an MCH record past identity capture | malformed — fail closed |
| `mch_teardown_key` or `mch_expected_uid` not equal to the enclosing record | malformed — fail closed |
| A rerun observing a different live UID for a recorded name | fatal before DELETE; the replacement is left intact |

`operator_deployment` and `operator_identity_unavailable` are **written by PR E**, which owns MCH
identity. PR B introduces their validation so no later PR can write a shape the reader does not
check.

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

def teardown_key(api_version: str, kind: str, namespace: str | None, name: str) -> str: ...

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

**Intended behavior.** `record_teardown_phase` writes through `_set` and then forces the state
file to disk before returning, so "forced durable before DELETE" is a property of the API rather
than of each call site. `teardown_record` raises `MalformedTeardownRecord` rather than returning
a degraded value: a teardown record is mutation authority, so the tolerant-degradation model used
by `RunSummary.from_snapshot` for reporting facts is wrong here.

**Failure behavior.** A failed durable write propagates; the caller must not proceed to DELETE.
**Dry-run implications.** `RunRecord` performs no dry-run branching; PR C's callers do not call
these writers in dry-run (Task C4). **Parity:** mirrored by Task B2.
**RBAC:** none.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_teardown_record.py`:

```python
"""Durable teardown records (R4-03 PR B)."""

import pytest

from lib.exceptions import FatalError
from lib.run_record import RunRecord
from lib.teardown_record import (
    MalformedTeardownRecord,
    TeardownPhase,
    TeardownRecord,
    teardown_key,
)

MCO_KEY = teardown_key("observability.open-cluster-management.io/v1beta2",
                       "MultiClusterObservability", None, "observability")


def test_key_is_stable_and_includes_an_empty_namespace_segment_when_cluster_scoped():
    assert MCO_KEY == (
        "observability.open-cluster-management.io/v1beta2/MultiClusterObservability//observability"
    )


def test_record_round_trips_through_the_facade(state_manager):
    record = RunRecord(state_manager)
    record.record_teardown_phase(
        TeardownRecord(key=MCO_KEY, expected_uid="uid-1", phase=TeardownPhase.DELETE_STARTED)
    )
    loaded = record.teardown_record(MCO_KEY)
    assert loaded.expected_uid == "uid-1"
    assert loaded.phase is TeardownPhase.DELETE_STARTED


def test_absent_record_reads_as_none(state_manager):
    assert RunRecord(state_manager).teardown_record(MCO_KEY) is None


def test_phase_write_is_forced_durable_before_returning(state_manager, monkeypatch):
    flushed = []
    monkeypatch.setattr(state_manager, "flush_state", lambda: flushed.append(True))
    RunRecord(state_manager).record_teardown_phase(
        TeardownRecord(key=MCO_KEY, expected_uid="uid-1", phase=TeardownPhase.DELETE_STARTED)
    )
    assert flushed, "teardown phase must be forced durable, not left to lazy save"


def test_a_failed_durable_write_propagates(state_manager, monkeypatch):
    monkeypatch.setattr(state_manager, "flush_state", _raise(OSError("disk full")))
    with pytest.raises(OSError):
        RunRecord(state_manager).record_teardown_phase(
            TeardownRecord(key=MCO_KEY, expected_uid="uid-1", phase=TeardownPhase.DELETE_STARTED)
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


def test_completed_requires_observed_at_and_resource_versions(state_manager):
    record = RunRecord(state_manager)
    with pytest.raises(MalformedTeardownRecord):
        record.record_teardown_phase(
            TeardownRecord(key=MCO_KEY, expected_uid="u", phase=TeardownPhase.COMPLETED)
        )
    record.record_teardown_phase(
        TeardownRecord(
            key=MCO_KEY, expected_uid="u", phase=TeardownPhase.COMPLETED,
            observed_at="2026-09-04T00:00:00Z", resource_versions={"cr": "9"},
        )
    )
    assert record.teardown_record(MCO_KEY).phase is TeardownPhase.COMPLETED


def test_expected_uid_is_never_rebound_by_a_later_write(state_manager):
    record = RunRecord(state_manager)
    record.record_teardown_phase(
        TeardownRecord(key=MCO_KEY, expected_uid="uid-1", phase=TeardownPhase.DELETE_STARTED)
    )
    with pytest.raises(MalformedTeardownRecord):
        record.record_teardown_phase(
            TeardownRecord(key=MCO_KEY, expected_uid="uid-2", phase=TeardownPhase.CR_ABSENT)
        )
    assert record.teardown_record(MCO_KEY).expected_uid == "uid-1"


def test_mch_identity_outcome_must_be_exactly_one(state_manager):
    both = TeardownRecord(
        key=MCO_KEY, expected_uid="u", phase=TeardownPhase.DELETE_STARTED,
        operator_deployment={"uid": "d"}, operator_identity_unavailable={"reason": "csv_ambiguous"},
    )
    with pytest.raises(MalformedTeardownRecord):
        RunRecord(state_manager).record_teardown_phase(both)
```

- [ ] **Step 2: Run and observe the expected failure**

```bash
python -m pytest tests/test_teardown_record.py -q
```

Expected: collection error — `No module named 'lib.teardown_record'`.

- [ ] **Step 3: Implement `lib/teardown_record.py` and the three `RunRecord` accessors**

Keep every raw-key touch inside `lib/run_record.py`; `lib/teardown_record.py` holds only the value
type, `teardown_key`, and validation. This is what keeps
`tests/test_run_record_guardrails.py::test_raw_config_keys_only_read_by_allowed_modules` passing
without widening its allow-list.

- [ ] **Step 4: Run and observe the tests pass**

```bash
python -m pytest tests/test_teardown_record.py tests/test_run_record.py \
  tests/test_run_record_guardrails.py -q
```

- [ ] **Step 5: Refactor**

Validation lives in exactly one function. If `record_teardown_phase` and `teardown_record` both
validate, extract one `_validated(record)` and call it from both.

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

**Intended behavior.** Identical algebra and identical malformed-record rules to Task B1. Roles
never touch the raw key; they call these functions through the checkpoint action plugin's
flattened facts, exactly as the merged vocabulary requires.

**Reset behavior.** A full `checkpoint.reset` rebuilds `operational_data` empty and therefore
destroys these records. `reset_from` prunes completed phases while **retaining**
`operational_data`; it must therefore retain and revalidate these records rather than launder
them. Task B4 tests both.

**Check-mode implications.** No `operational_data` transition is written in check mode; Task F1
asserts this end to end.

- [ ] **Step 1: Write the failing tests**

Create `ansible_collections/tomazb/acm_switchover/tests/unit/test_teardown_records.py` mirroring
every case in `tests/test_teardown_record.py`, expressed against the checkpoint functions:
round-trip, absent-reads-as-none, each malformed shape fails closed, `completed` requires
`observed_at` plus `resource_versions`, `expected_uid` is never rebound, and exactly one MCH
identity outcome is permitted.

Add to `tests/test_checkpoint_state_parity.py` an assertion that the Python config key and the
collection `operational_data` key are the same string, and that the two `teardown_key` builders
produce identical keys for the same inputs.

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
modify `tests/test_decommission.py`; modify `tests/test_finalization.py`.

**Purpose:** Replace the four states currently collapsed into `return True` with the amendment §7
table, and make refusal abort the run. Closes R4-C2 and criterion A5 on the Python side.

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
class DecommissionResult:
    substeps: dict[str, SubstepOutcome]   # "observability" | "managed_clusters" | "multiclusterhub"
    not_attempted: tuple[str, ...]
    @property
    def succeeded(self) -> bool: ...      # no REFUSED and no FAILED
    def summary_lines(self) -> list[str]: ...
```

**Intended behavior.** `Decommission.decommission(interactive=True)` runs the substeps in the
existing order. A refusal records `REFUSED`, stops immediately, records every remaining substep in
`not_attempted`, and returns a result whose `succeeded` is `False`. A substep disabled by
configuration records `NOT_REQUESTED`. A substep whose preconditions prove no mutation is needed
records `PRECONDITION_NOOP`. Non-interactive and integrated paths never prompt.

**Failure behavior.** `FAILED` on any `SwitchoverError`; remaining substeps are not attempted.

**State implications.** None: refusal is output, not state (amendment §13).
**Dry-run implications.** Dry-run previews requested substeps and records no outcome at all — the
result carries the requested set and the run reports a preview, never `completed` or `refused`.
**Parity implications.** Mirrored by Task B4. **RBAC:** none.

**Caller mapping**, preserving today's observable difference:

| Caller | Mapping |
| --- | --- |
| `acm_switchover.py::run_decommission` | returns `result.succeeded`; logs `result.summary_lines()`; the existing CLI exit path turns `False` into a non-zero exit |
| `modules/finalization.py::_decommission_old_hub` | raises `SwitchoverError` with its existing message context when `not result.succeeded`, appending the summary |

- [ ] **Step 1: Write the failing tests**

Replace `tests/test_decommission.py:143-159` — it currently asserts `result is True` after
declining every destructive step, and is named for an "extra MCH confirmation" that does not exist
in source. Delete that test and add:

```python
class TestDecommissionOutcomes:
    """R4-C2: a refused substep can never produce a successful decommission."""

    @patch("modules.decommission.confirm_action")
    def test_refusing_the_first_substep_aborts_and_fails(self, confirm, decommission_with_obs):
        confirm.side_effect = [True, False]  # proceed, then decline observability
        result = decommission_with_obs.decommission(interactive=True)
        assert result.succeeded is False
        assert result.substeps["observability"] is SubstepOutcome.REFUSED
        assert result.not_attempted == ("managed_clusters", "multiclusterhub")

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

    def test_summary_names_completed_refused_and_not_attempted(self, decommission_with_obs):
        result = DecommissionResult(
            substeps={"observability": SubstepOutcome.COMPLETED,
                      "managed_clusters": SubstepOutcome.REFUSED},
            not_attempted=("multiclusterhub",),
        )
        text = "\n".join(result.summary_lines())
        assert "observability" in text and "completed" in text
        assert "managed_clusters" in text and "refused" in text
        assert "multiclusterhub" in text and "not attempted" in text

    def test_dry_run_records_no_outcome_at_all(self, decommission_dry_run):
        result = decommission_dry_run.decommission(interactive=False)
        assert all(o is SubstepOutcome.NOT_REQUESTED or o is None for o in result.substeps.values()) or \
            result.is_preview is True
```

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
```

Add to `tests/test_main.py`, which already covers `run_decommission`:

```python
def test_run_decommission_returns_false_on_refusal(monkeypatch, args, primary, state, logger):
    monkeypatch.setattr(
        decommission_module.Decommission, "decommission",
        lambda self, interactive=True: DecommissionResult(
            substeps={"observability": SubstepOutcome.REFUSED}, not_attempted=("multiclusterhub",)
        ),
    )
    assert run_decommission(args, primary, state, logger) is False
```

- [ ] **Step 2: Run and observe the expected failures**

```bash
python -m pytest tests/test_decommission.py -q -k Outcomes
```

Expected: FAIL — `decommission()` returns `True`, so `result.succeeded` raises `AttributeError`
on a `bool`.

- [ ] **Step 3: Implement**

Add `lib/decommission_outcome.py`, then rewrite `Decommission.decommission` as an explicit loop
over three declared substeps rather than three copied `if/else` blocks:

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
            return DecommissionResult(substeps={}, not_attempted=self._SUBSTEPS)

        outcomes: dict[str, SubstepOutcome] = {}
        for index, substep in enumerate(self._SUBSTEPS):
            if not self._substep_requested(substep):
                outcomes[substep] = SubstepOutcome.NOT_REQUESTED
                continue
            if interactive and not confirm_action(self._PROMPTS[substep], default=False):
                outcomes[substep] = SubstepOutcome.REFUSED
                return DecommissionResult(substeps=outcomes, not_attempted=self._SUBSTEPS[index + 1 :])
            try:
                outcomes[substep] = self._run_substep(substep)
            except SwitchoverError as exc:
                logger.error("Decommission substep %s failed: %s", substep, exc)
                outcomes[substep] = SubstepOutcome.FAILED
                return DecommissionResult(substeps=outcomes, not_attempted=self._SUBSTEPS[index + 1 :])

        return DecommissionResult(substeps=outcomes, not_attempted=())
```

`_run_substep` dispatches to the three existing private methods, which still return
`SubstepOutcome.COMPLETED` in this PR; PRs C, D, and E replace their bodies with the guarded phase
machine and start returning `PRECONDITION_NOOP` where the July §3 rules prove no mutation is
needed. Update both callers per the mapping table above.

- [ ] **Step 4: Run and observe the tests pass**

```bash
python -m pytest tests/test_decommission.py tests/test_finalization.py -q
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
when no substep is `failed`, and computes `changed` from real mutation results. Refusal is not
applicable: the role stays non-interactive behind its confirmed-gate.

**Failure behavior.** A `failed` substep fails the play. Today's silent degradation to a warning
is removed in the substeps PRs C, D, and E own; this task removes the hard-coded `pass` that would
otherwise mask them.

**Check-mode implications.** In check mode the aggregated `changed` stays `false` and the
prediction is published as `would_change`; Task F1 asserts it end to end.

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_decommission_role_contracts.py`:

```python
def test_summary_status_is_not_hardcoded(decommission_main_tasks):
    publish = task_named(decommission_main_tasks, "Publish decommission result")
    status = publish["ansible.builtin.set_fact"]["acm_switchover_decommission_result"]["status"]
    assert status != "pass", "status must be derived from the real substep outcomes"
    assert "acm_switchover_decommission_outcomes" in str(status)


def test_every_substep_publishes_an_outcome(decommission_task_files):
    for substep in ("observability", "managed_clusters", "multiclusterhub"):
        assert any(
            f"acm_switchover_decommission_outcomes" in str(task)
            for task in decommission_task_files[substep]
        ), f"{substep} must publish an outcome"


def test_a_failed_substep_produces_a_failed_status(run_decommission_role):
    result = run_decommission_role(observability_outcome="failed")
    assert result["acm_switchover_decommission_result"]["status"] == "fail"


def test_outcome_values_come_from_the_collection_constants(run_decommission_role):
    from ansible_collections.tomazb.acm_switchover.plugins.module_utils import constants

    result = run_decommission_role(observability_outcome="precondition_noop")
    assert (
        result["acm_switchover_decommission_result"]["substeps"]["observability"]
        in constants.DECOMMISSION_SUBSTEP_OUTCOMES
    )
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
`modules/finalization.py`; modify `roles/decommission/tasks/main.yml`; modify tests.

**Purpose:** `Decommission` cannot own durable records it has no access to. `run_decommission`
already receives `state` and never uses it; the collection role has no checkpoint wiring at all.
Both are net-new wiring the amendment §13 names explicitly.

**Intended behavior.** `Decommission.__init__` accepts `run_record: RunRecord`.
`run_decommission` constructs `RunRecord(state)` and passes it. `Finalization` passes the
`RunRecord` it already holds. The collection role enters and exits a `decommission` checkpoint
phase through the existing `checkpoint_phase` action plugin, so `operational_data` is durable
before the first DELETE.

**Failure behavior.** Execute-mode collection decommission without checkpointing available fails
closed — the July deletion boundary requires the identity map to be durable before the first
DELETE. Dry-run and check mode do not require it and write nothing.

- [ ] **Step 1: Write the failing tests**

```python
def test_run_decommission_passes_a_run_record(monkeypatch, args, primary, state, logger):
    captured = {}
    monkeypatch.setattr(
        decommission_module, "Decommission",
        lambda *a, **kw: captured.setdefault("run_record", kw.get("run_record")) or _stub(),
    )
    run_decommission(args, primary, state, logger)
    assert captured["run_record"] is not None


def test_finalization_passes_its_run_record(finalization, monkeypatch):
    ...  # asserts the same object Finalization already holds reaches Decommission
```

Collection: assert `roles/decommission/tasks/main.yml` enters a checkpoint phase before the first
teardown include and that execute mode fails closed when checkpointing is unavailable.

- [ ] **Step 2 through 5:** run, observe failure, implement, rerun, commit.

```bash
python -m pytest tests/test_decommission.py tests/test_finalization.py tests/test_main.py -q
PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/ -q
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

`CHANGELOG.md` `## [Unreleased]` under `### Fixed`: a refused decommission substep now fails the
run and exits non-zero; the collection decommission artifact reports its real status. Update
[`docs/operations/usage.md`](../operations/usage.md)'s "Decommission Old Hub" section to state the
refusal behavior and the exit status.

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
| Strict Pod read | `KubeClient.list_pods_strict(namespace, label_selector=None)` added here, where its first consumer lands | YAGNI — PR A ships only what R4-04's gate names |
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
`lib/constants.py`; modify `lib/kube_client.py` (`list_pods_strict`); modify
`tests/test_decommission.py`; modify `tests/test_finalization.py`.

**Purpose:** Implement the July §1 phase machine once, in `Decommission`, and delete both current
MCO teardown copies. Closes GLM-H6 and, for MCO, R4-C3 and R4-C4.

**Interfaces produced:**

```python
class Decommission:
    def teardown_observability(self, *, record_gitops_markers: bool = False) -> SubstepOutcome: ...
    def _teardown_resource(self, spec: TeardownSpec, *, record_gitops_markers: bool) -> SubstepOutcome: ...
```

`TeardownSpec` declares the api group/version/plural, the kind, the optional namespace, the drain
namespace, the drain label selector, and the classifier callable. PRs D and E supply their own
specs to the same `_teardown_resource`; that is what keeps one algorithm.

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
5. Record `drain_pending`, then drain: `list_pods_strict(OBSERVABILITY_NAMESPACE,
   label_selector=OBSERVABILITY_POD_LABEL_SELECTOR)`. A positively absent namespace counts as
   verified-empty under the July §3 fixed-namespace scope rule; an unreadable or ambiguous
   namespace records `recovery_required`.
6. Record `drained` only after the bounded check proves empty; then re-run the CR-absence and
   pod-empty predicates and write `completed` carrying `observed_at` and the per-resource
   `resource_versions`.

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

**Dry-run implications.** In dry-run the machine performs the strict reads, reports the predicted
blocker set, issues no DELETE, and writes **no** record and no phase.

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
  proving no premature terminal write.
- [ ] **Step 2:** run; expected FAIL — `teardown_observability` does not exist and the current
  `_delete_observability` neither records nor re-reads.
- [ ] **Step 3:** implement `_teardown_resource`, `teardown_observability`, and
  `list_pods_strict`; delete `modules/finalization.py:1003-1088` and replace it with a call to
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
    monkeypatch.setattr(Decommission, "_teardown_resource",
                        lambda self, spec, **kw: calls.append((spec.kind, kw)) or SubstepOutcome.COMPLETED)
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

- [ ] **Step 1:** write the failing role-contract tests asserting the guarded module is used, the
  selector is present, no `state: absent` MCO task remains, and no failure-absorbing construct
  appears on those paths.
- [ ] **Step 2 through 4:** run, implement, rerun.
- [ ] **Step 5:** commit `feat: route collection MCO teardown through the guarded delete`.

### Task C5: Destination-observability gate

**Files:** Modify `modules/decommission.py`; modify `modules/finalization.py`; modify
`acm_switchover.py`; modify `lib/validation.py`; modify
`roles/decommission/tasks/delete_observability.yml`; modify
`roles/decommission/defaults/main.yml`; modify tests on both sides plus
`tests/test_validation.py` and `tests/test_validation_parity.py`.

**Purpose:** Close R4-C5 and July criterion 4.

**Intended behavior.** Immediately before the source MCO deletion substep — not at
`_decommission_old_hub` entry, and with no intervening mutation:

1. **Fresh source read.** Strict MCO CR read plus observability-namespace read on the source hub.
   Positively absent both → the gate is not applicable and the substep is `PRECONDITION_NOOP`.
   Positively present → continue. Any `error`, or a mixed state such as an absent CRD with a
   present namespace → **block**.
2. **Fresh destination read** through the secondary client: MCO CR strict list plus
   observability-namespace presence. The source clean-skip rule is **not** reused here; on the
   destination, missing discovery, missing CRD, missing CR, or missing namespace all block.
3. **Two distinguished blocking reasons.** Destination positively absent, versus destination
   unverifiable. They are never conflated in the message or in the outcome.
4. `--acknowledge-observability-not-migrated` proceeds **only** against a positively verified
   absent destination. It never overrides an unverifiable destination, and it is rejected when the
   gate would pass anyway.

**Never persisted.** The gate result is recomputed fresh on every run, including every resume.

**Standalone decommission** has no destination client and is unaffected.

**RBAC implications.** Destination reads: `multiclusterobservabilities get`/`list` and
`namespaces get` through the secondary client. Verified already granted by the baseline operator
`ClusterRole` (`deploy/rbac/clusterrole.yaml`: `namespaces get,list`;
`multiclusterobservabilities get,list,delete`), which the secondary hub already carries for
preflight. Recorded in §14 with that evidence; no new grant is required for the destination side.

- [ ] **Step 1: Write the failing tests**

```python
class TestDestinationObservabilityGate:
    def test_destination_present_passes_without_the_flag(self, integrated): ...
    def test_destination_positively_absent_blocks_without_the_flag(self, integrated): ...
    def test_destination_positively_absent_proceeds_with_the_flag(self, integrated): ...
    def test_destination_unverifiable_blocks_even_with_the_flag(self, integrated): ...
    def test_the_two_blocking_reasons_are_distinguishable(self, integrated): ...
    def test_flag_is_rejected_when_the_gate_would_pass_anyway(self, integrated): ...
    def test_source_is_re_read_fresh_and_the_preflight_boolean_is_not_consulted(self, integrated):
        # RunRecord records primary_has_observability; the gate must not read it.
        ...
    def test_mixed_source_state_absent_crd_present_namespace_blocks(self, integrated): ...
    def test_source_error_never_reads_as_nothing_to_delete(self, integrated): ...
    def test_gate_result_is_not_persisted(self, integrated, state_manager):
        integrated.run_gate()
        assert "observability_gate" not in json.dumps(state_manager.get_snapshot())

    def test_resume_reruns_the_gate(self, integrated): ...
    def test_standalone_decommission_has_no_destination_gate(self, standalone): ...
```

- [ ] **Step 2 through 4:** run, implement, rerun.
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
- [ ] **Step 4:** update [`docs/deployment/rbac-requirements.md`](../deployment/rbac-requirements.md),
  [`docs/deployment/rbac-deployment.md`](../deployment/rbac-deployment.md), and
  [`docs/development/rbac-implementation.md`](../development/rbac-implementation.md).
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
- [ ] Update the [parity matrix](../ansible-collection/parity-matrix.md) decommission row and the
  [behavior map](../ansible-collection/behavior-map.md) `modules/decommission.py` row for the
  guarded-delete and durable-phase boundaries.
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

**Failure behavior.** A same-name different-UID ManagedCluster is fatal and is left intact. Any
`error` outcome on inventory, per-cluster GET, or the absence poll is fatal and never absence.

**State/checkpoint implications.** One `TeardownRecord` per ManagedCluster name; the same phase
table; the same forced-durable ordering.

**Dry-run and check mode.** Preview lists the targets and writes no record.

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
`local-cluster` is never deleted; a denied `managedclusters get` blocks before any DELETE.

**Expected failing state before implementation:** the current base fixture
(`tests/test_decommission.py:25-31`) returns `[]` from the list mocks, so the discovery-failure and
later-page-failure tests pass vacuously today and fail once they assert a strict outcome; the
UID-recording tests fail with `AttributeError` on the missing record API usage.

**Steps:** the same red-green-refactor cycle as Task C3 — write the failing tests, run them and
observe the named failure, implement, rerun the targeted tests, simplify, rerun, then run the PR
gate set (Task A6 command set plus the full RBAC gate from Task C6 step 6).

**Commit boundary:** two commits — `feat: make ManagedCluster teardown strict and UID-guarded`,
and `feat: grant the ManagedCluster read decommission completion requires`.

**Documentation in this PR:** `CHANGELOG.md` `## [Unreleased]`; the RBAC requirements and
deployment documents; the parity matrix row if the ManagedCluster wording changes.

## 11C. PR E — MCH operator identity and completion

**Branch:** `feature/r4-03-mch-identity`

**Purpose.** Close R4-C1 and R4-C6: bind MCH drain exclusion to a complete controller-owner chain
ending at a durably recorded operator Deployment UID, and prove MCH completion. This is the
largest and highest-risk surface in the slice, which is why it is its own PR.

**Prerequisites:** PR A, PR B, PR C merged. Independent of PR D.

**Files:** Create `modules/decommission_identity.py`; modify `modules/decommission.py`; modify
`lib/kube_client.py` (`get_deployment_strict`, `get_replicaset_strict`); modify
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
| Classifier placement, Python | `modules/decommission_identity.py`, imported by `modules/decommission.py` | Amendment §10.3 — the helper lives with the teardown owner, and `lib/kube_client.py` stays a transport layer |
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
| `tests/unit/test_decommission_role_contracts.py:336-350` `failed_when: false` | inverted, as above |

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
`deploy/rbac/extensions/decommission/clusterrole.yaml` and its bundled copy; the RBAC
requirements, deployment, and implementation documents; Python and collection RBAC tests; the
parity, static-contract, and manifest/chart consistency tests; and negative authorization tests
that independently deny Pod `list`, CSV `list` and `get`, ReplicaSet `get`, Deployment `get`, and
Namespace `get`, each proving the denial blocks before DELETE or before completion with sanitized
output.

### 11C.6 Steps

- [ ] **Step 1:** write `tests/test_decommission_identity.py` containing matrix rows 1 through 20
  plus the provenance cases, driven from one declared fixture list; write the mirrored collection
  unit tests from the same fixture data; write the parity test comparing the two decision sets.
- [ ] **Step 2:** run all three and observe the expected failure — `modules/decommission_identity`
  does not exist, and the collection classification module does not exist. Also run
  `tests/test_decommission.py -k operator_pods_excluded` and observe that the existing
  names-only test now fails, confirming the defect it pinned is being removed rather than
  worked around.
- [ ] **Step 3:** implement `get_deployment_strict` and `get_replicaset_strict`, then
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
- [ ] **Step 8:** the full PR gate set (Task A6 command set), then documentation:
  `CHANGELOG.md` `## [Unreleased]`; the parity matrix decommission row must **replace** its current
  "warns if ACM workload pods remain" text with the fail-closed completion contract; the behavior
  map row gains the classification boundary; `docs/operations/usage.md` gains the operator-facing
  consequence of an unresolvable operator identity.
- [ ] **Step 9:** commits — `feat: bind MCH drain exclusion to the recorded operator Deployment`,
  `feat: prove MultiClusterHub teardown completion`, and
  `feat: grant the operator-identity reads MCH completion requires`.

---

# 12. PR F — check-mode closure, documentation and parity

**Branch:** `feature/r4-03-closure`

**Purpose.** Prove end to end that no preview path can leave trusted state or claim a change, and
close the documentation and parity contract. Closes criterion A9. Changes no teardown algorithm.

**Prerequisites:** PRs A, B, C, D, E merged.

## Task F1: Native check mode and dry-run end to end

**Files:** Modify `roles/decommission/tasks/*.yml`; modify `modules/decommission.py` where a
preview branch is missing; create
`tests/unit/test_decommission_check_mode.py`; create
`tests/test_decommission_dry_run.py`; modify `tests/scenario/`.

**Intended behavior.**

*Python dry-run.* Performs the strict provenance, inventory, and owner-chain reads read-only;
reports the predicted blocker set; issues no DELETE; persists no record, no phase, and no operator
identity; claims no change. A later live run trusts nothing from a dry run.

*Ansible, two layers.* The role's `acm_switchover_execution.mode` dry-run gate remains the primary
operator-facing preview and stays read-only — the existing live reads before those gates
(`has_observability: auto` reading the observability Namespace, and RBAC validation performing
live SelfSubjectAccessReviews unless explicitly skipped) keep their read-only character and are
reviewed against the new strict-read paths. Separately, **native** check mode is added wherever a
task would otherwise mutate or persist: `acm_uid_guarded_delete` stops after the live read and UID
validation and returns `changed: false` with explicit `would_change`;
`acm_k8s_read_outcome` continues to read (read-only by contract, existing tested behavior); the
classification module is read-only; no checkpoint `operational_data` transition is written; no
task reports `changed: true`; and `acm_switchover_decommission_result.changed` is `false`.

- [ ] **Step 1: Write the failing tests**

```python
# Collection
def test_check_mode_writes_no_checkpoint_transition(run_role_check_mode):
    result = run_role_check_mode()
    assert result["checkpoint"]["operational_data"].get("decommission_teardown_records") is None


def test_check_mode_reports_no_change_and_predicts_separately(run_role_check_mode):
    result = run_role_check_mode(mch_present=True)
    assert result["acm_switchover_decommission_result"]["changed"] is False
    assert result["acm_switchover_decommission_result"]["would_change"] is True


def test_no_task_reports_changed_true_in_check_mode(run_role_check_mode):
    assert not [t for t in run_role_check_mode()["tasks"] if t["changed"]]


def test_every_mutating_task_declares_check_mode_handling(decommission_task_files):
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
    assert "operator_deployment" not in json.dumps(state_manager.get_snapshot())


def test_a_live_run_after_a_dry_run_starts_from_no_record(decommission_dry_run, decommission_live,
                                                          state_manager):
    decommission_dry_run.decommission(interactive=False)
    assert RunRecord(state_manager).teardown_record(MCH_KEY) is None


def test_dry_run_reports_the_predicted_blocker_set(decommission_dry_run, caplog):
    decommission_dry_run.decommission(interactive=False)
    assert "predicted drain-blocking" in caplog.text
```

- [ ] **Steps 2 through 4:** run, observe the expected failures, implement the missing
  `ansible_check_mode` handling and the missing Python preview branches, rerun.
- [ ] **Step 5:** commit `fix: make decommission preview paths leave no trusted state`.

## Task F2: Documentation and parity closure

**Files to review and update only where behavior actually changed:**

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
- `docs/deployment/rbac-requirements.md`, `rbac-deployment.md`, `docs/development/rbac-implementation.md`
- the affected scenario and test-migration catalogs

**Must be documented by the end of this slice:**

1. The decommission outcome vocabulary and the non-zero exit on refusal or failure.
2. `--acknowledge-observability-not-migrated` and the mirrored collection variable, including that
   it is accepted only against a positively verified absent destination.
3. The teardown phase model, what `completed` does and does not assert, and that integrated
   teardown re-proves live.
4. **The full-reset consequence, operator-facing:** Python `--reset-state` and the collection's
   full `checkpoint.reset` destroy teardown records, so a post-reset rerun that finds a CR absent
   is indistinguishable from never-attempted and takes the clean-skip path. Resetting state between
   a failed drain and its rerun forfeits the drain obligation's memory. `reset_from` is different
   and retains the records.
5. That an unresolvable operator identity means no Pod is excluded, and only a strictly verified
   empty Pod list satisfies the drain.
6. That R4-03 binds **resource** identity and does not provide wrong-target hub protection (§19).

**Do not modify** `docs/ACM_SWITCHOVER_RUNBOOK.md` or `.claude/skills/**`. If implementation
reveals a genuinely required protected-file change, stop and request separate operator approval
with the proposed line-by-line diff; that future task is marked `OPERATOR_APPROVAL_REQUIRED` and
is not pre-authorized by this plan. §18 records the current assessment: no protected-file change
is anticipated, because the runbook documents the manual procedure and this slice changes the
tool's proof obligations rather than the operator's manual steps.

- [ ] Run `python -m pytest tests/test_documentation_guardrails.py tests/test_ci_guardrails.py -q`
- [ ] Commit `docs: document the R4-03 decommission completion contract`

## Task F3: Final slice verification

- [ ] The complete gate set in §21, at the frozen candidate head.
- [ ] Simplification gate across the whole slice's changed surface.
- [ ] Commit, open PR F, and run the §22 workflow.

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
| `resourceVersions` | required at `completed`, per resource proven | not applicable | not applicable |
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
10. Python RBAC tests — `tests/test_rbac_validator.py`, `tests/test_rbac_integration.py`
11. Collection RBAC tests
12. Parity and static-contract tests — `tests/test_rbac_collection_parity.py`,
    `tests/properties/test_rbac_properties.py`, and the manifest/chart consistency checks
13. Negative authorization tests — one per newly required permission, each proving the denial
    blocks before DELETE or before a completion claim, with sanitized output

**Timing rule.** Permissions land no later than the PR introducing the corresponding API call. No
PR in this plan makes a call whose permission arrives in a later PR.

---

# 15. External API and version pins

The July design's citation-provenance limitation is discharged here. Every reference below is
pinned to an immutable tag or a versioned upstream document, and was retrieved and verified on
2026-09-01 against the exact versions the repository's dependency and compatibility authorities
permit.

## 15.1 Supported version ranges

| Dependency | Declared range | Authority |
| --- | --- | --- |
| Python `kubernetes` client | `kubernetes>=28.0.0` | `requirements.txt`; restated by the compatibility authority |
| `kubernetes.core` | `>=6.0.0,<7.0.0`, resolved bounded not pinned | `galaxy.yml`, `requirements.yml`, compatibility authority |
| `ansible-core` | `>=2.16.0,<2.22`; repository-tested lanes 2.16 and 2.21 | `meta/runtime.yml`, compatibility authority |
| Python CLI | 3.10 – 3.12 | `setup.cfg` |

## 15.2 Pinned references

| Claim | Pinned source |
| --- | --- |
| Kubernetes defines delete preconditions, with `uid` as the target UID | `https://kubernetes.io/docs/reference/kubernetes-api/definitions/preconditions-v1-meta/` (versioned upstream API definition; unaffected by the July limitation) |
| Object UIDs are cluster-lifetime identity | `https://kubernetes.io/docs/concepts/overview/working-with-objects/names/#uids` |
| Controller owner references carry both name and UID | `https://kubernetes.io/docs/concepts/overview/working-with-objects/owners-dependents/` |
| Deployment to ReplicaSet to Pod controller relationship | `https://kubernetes.io/docs/concepts/workloads/controllers/deployment/` and `.../replicaset/` |
| OLM CSV install-strategy model | `https://olm.operatorframework.io/docs/concepts/crds/clusterserviceversion/` |
| `V1Preconditions` exposes `uid` and `resource_version` | `https://github.com/kubernetes-client/python/blob/v28.1.0/kubernetes/client/models/v1_preconditions.py` and `.../blob/v36.0.1/...` — `openapi_types` is `{'resource_version': 'str', 'uid': 'str'}` and `attribute_map` maps them to `resourceVersion` and `uid` at **both** pins |
| `V1DeleteOptions.preconditions` is a `V1Preconditions` | `https://github.com/kubernetes-client/python/blob/v28.1.0/kubernetes/client/models/v1_delete_options.py` and the v36.0.1 path |
| `CustomObjectsApi.delete_namespaced_custom_object` and `delete_cluster_custom_object` accept a `V1DeleteOptions` `body` | `https://github.com/kubernetes-client/python/blob/v36.0.1/kubernetes/client/api/custom_objects_api.py` — `:param body:` typed `V1DeleteOptions`, forwarded as `body_params` |
| Dynamic client `delete` accepts a `body`, and `get` accepts `_continue` and `limit` | `https://github.com/kubernetes-client/python/blob/v28.1.0/kubernetes/base/dynamic/client.py` and the v36.0.1 path — both signatures and both query-parameter mappings are present at **both** pins |
| Discovery swallows some fetch failures into an empty resource list | `https://github.com/kubernetes-client/python/blob/v28.1.0/kubernetes/base/dynamic/discovery.py` and the v36.0.1 path — see §15.3 |
| `kubernetes.core` exposes `get_api_client`, `AUTH_ARG_SPEC`, and `K8SClient.resource` / `.get` / `.delete` | `https://github.com/ansible-collections/kubernetes.core/blob/6.0.0/plugins/module_utils/k8s/client.py` and `.../blob/6.3.0/...`, plus `.../plugins/module_utils/args_common.py` at both tags — all five interfaces are present, and `_find_resource_with_prefix` is unchanged between the two |

`v28.1.0` is the earliest released tag satisfying the `kubernetes>=28.0.0` floor; `v36.0.1` is the
newest release verified during planning. `6.0.0` is the `kubernetes.core` floor and `6.3.0` is the
release the local lane resolved. No reference above targets `master`, `latest`, a search-result
snippet, a blog, or a generated summary.

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
type. Both prove it from their own discovery request that must succeed and decode (§9.2, Tasks A2
and A4). That rule is identical at every version in the range, so the implementation needs no
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
| 1. Strict-read parity vectors, both form factors, pagination completeness and later-page failure | `tests/test_strict_read_parity.py` (12 vectors, three assertions each); `TestStrictCustomResourceReads`; the collection pagination tests in `test_k8s_read_outcome.py` | A |
| 2. Read-outcome extension regression | `test_k8s_read_outcome.py` and `test_k8s_read_outcome_runtime.py` with inverted discovery-miss expectations, new pagination and incomplete-list vectors, and the RETURN assertion; `ansible-test sanity --test validate-modules`; runtime consumer lanes `test_r3_02_compactor_runtime.py` and `test_r3_02_activation_runtime.py` | A |
| 3. Fail-open inversions | collection: `test_decommission_role_contracts.py:336-350` and `test_ansible_resilience_contracts.py:485` inverted; Python: lingering-pod warning becomes fatal; MCO and MCH absence re-checks asserted against the real client seam | C, E |
| 4. Refusal matrix | `TestDecommissionOutcomes` — each prompt refused aborts with an accurate summary and a non-zero result; rerun completes idempotently; non-interactive and integrated paths never prompt | B |
| 5. Guarded-delete matrix | `TestPreconditionedDelete`; the collection `test_uid_guarded_delete.py` set — UID success, 409 and 412 fatal, pre-DELETE disappearance, mid-poll replacement, bounded timeout, check-mode `would_change`, redaction injection | C |
| 6. Identity and TOCTOU matrix | `tests/test_decommission_identity.py` rows 1 through 20 plus the provenance cases; same-name new-UID replacement between discovery and DELETE; unrelated prefixed Pod; invalid, missing, and multiple owner chains; Deployment and ReplicaSet replacement mid-drain | E |
| 7. Destination gate matrix | `TestDestinationObservabilityGate` — destination positively absent, present, `error`, and ambiguous mixed state; the flag accepted only against positive absence; the flag rejected when the gate would pass; resume re-runs the gate; the result is not persisted | C |
| 8. State, resume, reset | the phase-table resume matrix in `tests/test_decommission.py`; the full-reset clean-skip limitation asserted as **current** behavior so the R4-05 coordination stays visible; the collection `reset_from` case retaining and revalidating the records; malformed-record fail-closed cases in `tests/test_teardown_record.py` and `tests/unit/test_teardown_records.py` | B, C |
| 9. Consolidation regression, GLM-H6 | `test_finalization_and_direct_decommission_share_one_teardown_path`; `test_no_second_mco_teardown_implementation_remains`; GitOps markers recorded for the finalization caller only; caller preconditions preserved; collection artifact status honesty | B, C |
| 10. Wrong-target boundary | a negative test asserting R4-03 binds **resource** identity and that no wrong-context or wrong-hub target check exists here, so the SSA-02 boundary is tested rather than silently assumed | F |
| 11. Constants parity | `CONSTANT_PAIRS` gains the strict-read reason codes, `OBSERVABILITY_POD_LABEL_SELECTOR`, `ACM_OPERATOR_POD_PREFIX`, and the four classification reason codes | A, C, E |

## 16.3 Negative safety coverage required by `AGENTS.md`

Each of these is a defect if missing on a path this slice touches, and each has a named test
above: wrong-context behavior (§16.2 item 10), check-mode behavior (Task F1), idempotence (item 4
rerun case), RBAC denial (§14 surface 13), checkpoint and resume failure (item 8), stale Argo CD
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
| R4-C2 refusal returns success | B | B3, B4 | `TestDecommissionOutcomes`; finalization and CLI mapping tests | root suite |
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
| 2. A refused substep yields a non-zero result and an accurate summary | B | `TestDecommissionOutcomes` |
| 3. Missing API discovery aborts before any deletion decision depending on the list | A, D | `test_unserved_kind_short_circuits_to_crd_absent`; the ManagedCluster discovery-failure test |
| 4. Destination gate fails closed; source re-read fresh; ack only against positive absence | C | `TestDestinationObservabilityGate` |
| 5. Hive `preserveOnDelete` behavior unchanged | D | the retained `preserveOnDelete` and ambiguous-relationship tests |
| 6. Clean skip only with no record or prior obligation; every recorded phase resumes | C, D, E | the phase-table resume matrix |
| 7. Positive namespace absence counts as empty only under the fixed-namespace scope proof; `drained` and `completed` only after their full checks | C, E | namespace-absence and boundary-injection tests |
| 8. `completed` asserts proof at its final-read instant, carries `observed_at` and `resourceVersion`, and integrated teardown re-proves live | B, C | `test_completed_requires_observed_at_and_resource_versions`; the integrated fresh-gate test |
| 9. Operator provenance durably bound before MCH DELETE, or explicitly unavailable | E | the provenance test set; `test_mch_identity_outcome_must_be_exactly_one` |
| 10. Exclusion only after the complete owner chain; every broken link blocks; rolling-update ReplicaSets accepted only to the same UID | E | matrix rows 1, 8, 9, 10, 11, 12 |
| 11. Prefix spoofing cannot change classification; unavailable identity means only a verified empty list satisfies the drain; a replaced Deployment fails closed | E | matrix rows 2–7, 14, 15, 16, 17 |
| 12. Both form factors keep semantics, durable fields, bounds, preview mode, changed reporting, reason codes, redaction, RBAC, and negative tests in parity without cross-imports | A–F | parity vectors; constants parity; RBAC parity; matrix rows 18, 19, 20 |

## 17.3 Amendment criteria A1 through A9

| Criterion | PR | Tests | Gate |
| --- | --- | --- | --- |
| A1 shared strict-read contract in both form factors; R4-04 Task 0 Step 2 satisfiable verbatim | A | §9.6 mapping table | parity vectors + collection surfaces |
| A2 no `failed_when: false` on the collection MCH provenance, ownership, wait, or final-verification paths; unverifiable read fails the play; pins inverted | E | inverted `test_decommission_role_contracts.py:336-350` and `test_ansible_resilience_contracts.py:485` | collection unit + scenario |
| A3 collection summary artifact reports the real aggregated outcome | B | `test_summary_status_is_not_hardcoded`; `test_a_failed_substep_produces_a_failed_status` | collection unit |
| A4 both merged read-outcome consumers pass their runtime lanes unchanged | A | `test_r3_02_compactor_runtime.py`, `test_r3_02_activation_runtime.py` | collection integration |
| A5 the §7 outcome table is observable in both form factors | B | `TestDecommissionOutcomes`; `test_outcome_vocabulary_matches_python` | root + collection |
| A6 operator-prefix drift closed; every shared constant enforced by the parity test | E | `tests/test_constants_parity.py` with the prefix and reason-code pairs | parity |
| A7 Python teardown exists exactly once; both callers drive it with their semantics preserved | C | consolidation regression tests | root |
| A8 the durable-field table is exhaustive; full-reset loss documented operator-facing; `reset_from` preserves and revalidates | B, F | the reset and `reset_from` scenario cases; documentation guardrails | collection scenario + docs |
| A9 native check mode safe end to end; role-level `changed` false; prediction reported as `would_change` | F | `tests/unit/test_decommission_check_mode.py`; `tests/test_decommission_dry_run.py` | collection unit + scenario |

---

# 18. Documentation update map

Documentation lands in the PR whose behavior it describes. Nothing is deferred to a documentation
sweep.

| Document | PR A | PR B | PR C | PR D | PR E | PR F |
| --- | --- | --- | --- | --- | --- | --- |
| `CHANGELOG.md` `## [Unreleased]` | yes | yes | yes | yes | yes | yes |
| `README.md` and its Mermaid diagrams | no | no | no | no | no | review; update if the operator-facing decommission flow description changes |
| `docs/operations/usage.md` | no | refusal and exit status | the ack flag and gate behavior | inventory failure behavior | operator-identity consequence | reset consequence and the SSA-02 boundary |
| `docs/operations/quickref.md` | no | no | the ack flag | no | no | review |
| `docs/reference/validation-rules.md` | no | no | the ack flag's cross-argument rules | no | no | review |
| `docs/development/architecture.md` and diagrams | strict-read seam | durable teardown records | the teardown owner and phase machine | no | the classification boundary | review |
| `docs/ansible-collection/parity-matrix.md` | strict-read row | outcome row | decommission row | decommission row | **replace** the "warns if ACM workload pods remain" text with the fail-closed completion contract | final review |
| `docs/ansible-collection/behavior-map.md` | replace the generic `lib/kube_client.py` target with the strict-read mapping | state row | guarded-delete and durable-phase boundaries | no | classification boundary | final review |
| `ansible_collections/.../docs/coexistence.md` | no | outcome parity | gate parity | no | classification parity | final review |
| `ansible_collections/.../docs/variable-reference.md` | no | no | the ack variable | no | no | review |
| `ansible_collections/.../docs/cli-migration-map.md` | no | no | the ack flag mapping | no | no | review |
| `ansible_collections/.../examples/group_vars/all.yml` | no | no | the ack variable | no | no | review |
| `ansible_collections/.../README.md` | no | no | no | no | no | review |
| `docs/deployment/rbac-requirements.md` | no | no | yes | yes | yes | review |
| `docs/deployment/rbac-deployment.md` | no | no | yes | yes | yes | review |
| `docs/development/rbac-implementation.md` | no | no | yes | yes | yes | review |
| scenario and test-migration catalogs | no | if mapped behavior changes | if mapped behavior changes | if mapped behavior changes | if mapped behavior changes | final review |

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

The reset limitation is **documented and tested as current behavior**: a full reset destroys
teardown records, so a post-reset rerun that finds a CR absent is indistinguishable from
never-attempted and takes the clean-skip path. Task B4 asserts exactly that, deliberately, so the
R4-05 coordination remains visible rather than being silently mitigated inside R4-03. Task F2
states the operator-facing consequence.

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
| Full reset between a failed drain and its rerun | The drain obligation's memory is forfeited and the rerun clean-skips — documented, tested as current behavior, R4-05-owned | B4, F2 |
| `reset_from` | Retains `operational_data`, therefore retains the records, and revalidates rather than laundering them | B4 |
| Resume of an integrated run | The destination gate re-runs its fresh reads unconditionally; no stored gate result exists to reuse | C5 |
| Refusal | Ends the run; never persisted; the summary is output | B3 |
| Dry-run or check mode followed by a live run | The live run finds no record, no identity, and no phase | F1 |

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
| F | dual-supported, documentation | same as A |

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
§14 surface 13.

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
| Reset laundering | Accepted, documented, tested as current behavior, R4-05-owned (§19.3) |
| OLM or CSV contract drift beyond ACM 2.17 | Fail-closed by design; the audited range is the widest any repository authority claims (§15.4) |
| Two guarded-mutation modules in the collection | Accepted as different ownership boundaries with intra-collection `module_utils` reuse; revisit only if implementation shows the boundaries collapsing (§19.1) |
| PR E's size | Mitigated by its own PR, a declared fixture-driven matrix, and a classifier with one code path and no prefixed-Pod special case |
| A stale tracker sentence routes R4-04's Restore cleanup through `acm_uid_guarded_delete` | Does not affect R4-03 correctness; needs a tracker-reconciliation edit in a slice authorized to touch the tracker (amendment §19). No task here edits the tracker except to add its own PR row |

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
