# R3-01b Finalization Register/`set_fact` Clobbers + Collision Guardrail — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Plan identifier:** `R3-01b-PLAN-B9`
**Supersedes:** `R3-01b-PLAN-B8` (local artifact only; never published at a branch head)
**Provenance note:** `R3-01b-PLAN-B4` (SHA-256
`bf86f3abfb94b0939a61e42b1152ba8de5e13b46a08c0f61f367f5a888e652df`, Git blob
`c1d91f2956cef2dc2384987bd4bfdbef9d317914`) was also a local artifact that
never appeared at any branch head. The commit
`f7824a69fac33d2182decdf99dc3460da8205694` is the historical PR #203 head and
contains only the failed B2 publication; B4, B5, B6, B7, and B8 were never
associated with that head. B9 preserves every accepted B8 correction and
changes only identifier/provenance text, complete Task 6 normal/direct
scenario builders and defect-specific red evidence, the Restore discovery
delete verb, and their directly required consistency changes.
**Design of record:** `R3-01b-DESIGN-B2` (`docs/plans/2026-07-28-r3-01b-finalization-register-clobbers-design.md`, APPROVED)
**Bound approved base:** `0bf55db9eed76ae7d60844b806975c04cd0111e4` (PR #201 merge commit)

**Goal:** Make the collection finalization dry-run Restore preview truthful, preserve fixture/deliberate-seed semantics with fail-closed staged shape validation, and add a recursive **literal-only** `register`/`set_fact` collision guardrail — collection-only, without touching the Argo CD regression, `TR2D-02`, or issue #202.

**Architecture:** For each colliding name, register the live query to a distinct name, **select a temporary candidate, validate it with staged assertions, and publish the authoritative fact only after validation** (never publish malformed data). Add a static pytest guardrail that scans `roles/**/tasks`, `roles/**/handlers`, and `playbooks/**` for **literal-scalar** `register`/`set_fact` name collisions (Jinja/computed names excluded) and enforces a two-category (intentional / debt) allowlist with strict metadata validation.

**Tech Stack:** Ansible collection (`kubernetes.core`, `ansible.builtin.assert`), pytest static YAML-contract tests, pytest subprocess `ansible-playbook` integration harness with an executable fake Kubernetes API (`tests/integration/`).

---

## Binding preconditions and boundary (read before Task 0)

- **Approved base:** `0bf55db9eed76ae7d60844b806975c04cd0111e4`. Branch from exactly this SHA.
- **Scope (corrected):** *No Python CLI production files (`acm_switchover.py`, `lib/`, `modules/`) or root Python CLI tests under `tests/` are modified. Collection pytest files are in scope.* Modify only files under `ansible_collections/tomazb/acm_switchover/` plus the authorized documentation boundary below.
- **Authorized documentation boundary:** exactly these tracked docs may be edited:
  - `docs/plans/2026-07-28-r3-01b-finalization-register-clobbers-design.md`
  - `docs/plans/2026-07-28-r3-01b-finalization-register-clobbers-implementation-plan-b9.md`
  - `thermos-resolution-plan.md`
  - `AGENTS.md`
  - `CHANGELOG.md`
- **Strict separation:** do not touch `TR2D-02` code/tests, the merged Argo CD scoped-discovery work, or any unrelated `R3` slice.
- **No work on issue #202.** The preflight collision is only *allowlisted* (debt category), never fixed here. Issue #202 stays **separate and open**.
- **Tracker:** at implementation start, update **only** the `R3-01b` row (see Task 2). Do **not** claim `ready_for_review` and do **not** record merge credit at any point in this plan.
- **Builder execution requires separate explicit approval of `R3-01b-PLAN-B9`.** Do not create the branch, edit any tracked file, or open a PR until that approval is given.
- **Guardrail boundary (design §6.2):** `roles/**/tasks/**/*.{yml,yaml}`, `roles/**/handlers/**/*.{yml,yaml}`, `playbooks/**/*.{yml,yaml}`. **Playbook scanning must traverse every play-level task section — `pre_tasks`, `tasks`, `post_tasks`, `handlers` — before applying recursive `block`/`rescue`/`always` flattening.** Role `tasks`/`handlers` files are flat task lists, flattened directly.
- **Current-state facts:** playbooks have **zero** collisions today; no `roles/*/handlers/` files exist yet (boundary still includes them); the guardrail finds **14** collisions — Tasks 5–6 fix 2, leaving 2 intentional + 10 debt(#202).

All commands run from the worktree root with its venv active.

---

## Plan self-check (commit-coverage contract)

> **B7-C4 gate:** every created/new-test artifact in this plan must appear in
> exactly one `git add`/commit boundary below. Modified files are tracked
> separately. Task 11 Step 9 proves presence, uniqueness, and bidirectional
> consistency among this table, Task `Files` sections, `git add` commands, and
> the Task 11 scope allowlist. Do not open a PR if any set or task ownership
> differs.

The following table is the complete task-ownership contract:

| Path | Task | Kind |
| ---- | ---- | ---- |
| `docs/plans/2026-07-28-r3-01b-finalization-register-clobbers-design.md` | Task 1 | Create |
| `docs/plans/2026-07-28-r3-01b-finalization-register-clobbers-implementation-plan-b9.md` | Task 1 | Create |
| `thermos-resolution-plan.md` | Task 2 | Modify |
| `ansible_collections/tomazb/acm_switchover/tests/integration/finalization_fake_api.py` | Task 3 | Create |
| `ansible_collections/tomazb/acm_switchover/tests/integration/test_finalization_fake_api.py` | Task 3 | Create |
| `ansible_collections/tomazb/acm_switchover/roles/finalization/tasks/assert_restore_source_shape.yml` | Task 4 | Create |
| `ansible_collections/tomazb/acm_switchover/tests/integration/playbooks/finalization_assert_restore_source_shape.yml` | Task 4 | Create |
| `ansible_collections/tomazb/acm_switchover/tests/integration/test_restore_source_shape_contract.py` | Task 4 | Create |
| `ansible_collections/tomazb/acm_switchover/roles/finalization/tasks/cleanup_restores.yml` | Task 5 | Modify |
| `ansible_collections/tomazb/acm_switchover/tests/integration/playbooks/finalization_cleanup_restores.yml` | Task 5 | Create |
| `ansible_collections/tomazb/acm_switchover/tests/integration/test_finalization_cleanup_restores_runtime.py` | Task 5 | Create |
| `ansible_collections/tomazb/acm_switchover/tests/unit/test_finalization_verification.py` | Task 5 | Modify |
| `ansible_collections/tomazb/acm_switchover/roles/finalization/tasks/discover_resources.yml` | Task 6 | Modify |
| `ansible_collections/tomazb/acm_switchover/tests/integration/playbooks/finalization_old_hub_restore_discovery.yml` | Task 6 | Create |
| `ansible_collections/tomazb/acm_switchover/tests/integration/test_finalization_old_hub_restore_runtime.py` | Task 6 | Create |
| `ansible_collections/tomazb/acm_switchover/tests/unit/test_discover_resources_contracts.py` | Task 6 | Modify |
| `ansible_collections/tomazb/acm_switchover/tests/unit/register_collision_scan.py` | Task 7 | Create |
| `ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/collisions/` | Task 7 | Create |
| `ansible_collections/tomazb/acm_switchover/tests/unit/test_register_setfact_collision_guardrail.py` | Task 7 | Create |
| `ansible_collections/tomazb/acm_switchover/tests/unit/register_setfact_collision_allowlist.yml` | Task 8 | Create |
| `ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/allowlists/` | Task 8 | Create |
| `ansible_collections/tomazb/acm_switchover/tests/integration/playbooks/register_skip_semantics.yml` | Task 9 | Create |
| `ansible_collections/tomazb/acm_switchover/tests/integration/test_register_skip_semantics.py` | Task 9 | Create |
| `AGENTS.md` | Task 10 | Modify |
| `CHANGELOG.md` | Task 10 | Modify |

---

## Task 0: Hard pre-start gate + isolated worktree (execute only after `R3-01b-PLAN-B9` approval)

**Step 1: Re-baseline gate — fetch and require `origin/ansible == approved base`**

```bash
cd /home/tomaz/sources/rh-acm-switchover
git fetch origin ansible
BASE=0bf55db9eed76ae7d60844b806975c04cd0111e4
REMOTE=$(git rev-parse origin/ansible)
echo "approved_base=$BASE"; echo "origin/ansible=$REMOTE"
test "$REMOTE" = "$BASE" || { echo "STOP: origin/ansible advanced past approved base — explicit delta assessment / re-baselining required before implementation."; exit 1; }
```

**HARD GATE:** if `origin/ansible` has advanced, **stop**. Do not implement. Return to the operator for delta assessment and re-baselining of `R3-01b-PLAN-B9`.

**Step 2: Create the worktree/branch from the approved base**

```bash
git worktree add .claude/worktrees/r3-01b -b fix/r3-01b-finalization-register-clobbers "$BASE"
cd .claude/worktrees/r3-01b && python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements-dev.txt
```

**Step 3: Confirm base**

Run: `git rev-parse HEAD`
Expected: `0bf55db9eed76ae7d60844b806975c04cd0111e4`

---

## Task 1: Seed the approved design + plan into the worktree

Untracked files do **not** enter a worktree created from the base commit, so the approved artifacts must be copied in and committed first.

**Files:**
- Create: `docs/plans/2026-07-28-r3-01b-finalization-register-clobbers-design.md`
- Create: `docs/plans/2026-07-28-r3-01b-finalization-register-clobbers-implementation-plan-b9.md`

**Step 1: Copy the exact approved artifacts**

```bash
# from the primary worktree paths (or session artifacts), copy verbatim:
cp <primary>/docs/plans/2026-07-28-r3-01b-finalization-register-clobbers-design.md docs/plans/
cp <primary>/docs/plans/2026-07-28-r3-01b-finalization-register-clobbers-implementation-plan-b9.md docs/plans/
```

**Step 2: Record content hashes as evidence (design + plan)**

```bash
sha256sum docs/plans/2026-07-28-r3-01b-finalization-register-clobbers-design.md \
          docs/plans/2026-07-28-r3-01b-finalization-register-clobbers-implementation-plan-b9.md
git hash-object docs/plans/2026-07-28-r3-01b-finalization-register-clobbers-design.md \
                docs/plans/2026-07-28-r3-01b-finalization-register-clobbers-implementation-plan-b9.md
```
Capture both sets of hashes in the PR body / evidence log; they bind the delivered artifacts to `R3-01b-DESIGN-B2` / `R3-01b-PLAN-B9`.

**Step 3: Commit**

```bash
git add docs/plans/2026-07-28-r3-01b-finalization-register-clobbers-design.md \
        docs/plans/2026-07-28-r3-01b-finalization-register-clobbers-implementation-plan-b9.md
git commit -m "docs(plan): seed approved R3-01b-DESIGN-B2 and R3-01b-PLAN-B9 into worktree"
```

---

## Task 2: Mark `R3-01b` in progress (tracker only)

**Files:**
- Modify: `thermos-resolution-plan.md`

**Step 1: Update only the `R3-01b` tracker row**

- Change `R3-01b` status from `planned` to `in_progress`.
- Record the identifiers and branch in the row/notes: `R3-01b-DESIGN-B2`, `R3-01b-PLAN-B9`, branch `fix/r3-01b-finalization-register-clobbers`, base `0bf55db9`.
- Update the document's `Last Updated` date.
- Do **not** touch the `R3-A2`/`R3-A3` finding rows' resolution status, the #202 reference, or any other row. Do **not** write `ready_for_review` or merge credit.

**Step 2: Commit**

```bash
git add thermos-resolution-plan.md
git commit -m "docs(tracker): mark R3-01b in_progress with R3-01b-DESIGN-B2/PLAN-B9 identifiers"
```

---

## Task 3: Executable fake ACM backup API (shared test infrastructure)

**Files:**
- Create: `ansible_collections/tomazb/acm_switchover/tests/integration/finalization_fake_api.py`
- Test: `ansible_collections/tomazb/acm_switchover/tests/integration/test_finalization_fake_api.py`

**Step 1: Implement a fake, modeled on `argocd_fake_api.py`**

A `ThreadingHTTPServer` on `127.0.0.1:0` exposing `url`, `close()`, and context-manager support:

```python
def __enter__(self):
    return self

def __exit__(self, exc_type, exc, traceback):
    self.close()
    return False
```

All permanent tests use `with FakeAcmBackupHub(...) as hub:`; do not mix
context-manager ownership with unguarded manual cleanup.

Define and export these canonical fixture builders in
`finalization_fake_api.py`; the fake API tests and every execute-mode
finalization test import them rather than hand-writing valid Kubernetes
resources:

```python
import copy

RESTORE_API_VERSION = "cluster.open-cluster-management.io/v1beta1"
RESTORE_NAMESPACE = "open-cluster-management-backup"
MCH_API_VERSION = "operator.open-cluster-management.io/v1"
MCH_NAMESPACE = "open-cluster-management"


def restore_fixture(
    name,
    *,
    namespace=RESTORE_NAMESPACE,
    resource_version="1",
    spec=None,
    status=None,
):
    """Return a complete Kubernetes Restore object for fake/execute tests."""
    resource = {
        "apiVersion": RESTORE_API_VERSION,
        "kind": "Restore",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "resourceVersion": str(resource_version),
        },
    }
    if spec is not None:
        resource["spec"] = copy.deepcopy(spec)
    if status is not None:
        resource["status"] = copy.deepcopy(status)
    return resource


def multiclusterhub_fixture(
    name="multiclusterhub",
    *,
    namespace=MCH_NAMESPACE,
    resource_version="1",
    current_version="2.14.0",
):
    """Return a complete Kubernetes MultiClusterHub object."""
    return {
        "apiVersion": MCH_API_VERSION,
        "kind": "MultiClusterHub",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "resourceVersion": str(resource_version),
        },
        "spec": {},
        "status": {"currentVersion": current_version},
    }
```

`FakeAcmBackupHub` deep-copies `restore_fixture(...)` objects into mutable
state and deep-copies a supplied `multiclusterhubs` list; its default MCH list
is `[multiclusterhub_fixture()]`. Valid Restore/MCH constructor inputs that
lack the canonical identity fields are rejected with a clear test-infrastructure
error before the HTTP server is started rather than silently normalized.
Deliberately malformed mappings used
only to exercise the staged shape validator are exempt: they do not enter the
fake API or an execute-mode fixture.

This is a test-fixture invariant, not optional example style:

- every valid Restore passed to `FakeAcmBackupHub` is built with
  `restore_fixture`;
- every valid MultiClusterHub passed to the fake is built with
  `multiclusterhub_fixture`;
- every valid execute-mode pre-seed/injected Kubernetes Restore or
  MultiClusterHub object is built with the same helper;
- dry-run shape-only payloads may remain minimal because they deliberately
  test the validator contract and are not fake API/execute-mode Kubernetes
  fixtures.

The fake serves:
- `GET /version`
- `GET /apis`
- `GET /apis/operator.open-cluster-management.io/v1`
- `GET /apis/operator.open-cluster-management.io/v1/namespaces/open-cluster-management/multiclusterhubs`
- `GET /apis/cluster.open-cluster-management.io/v1beta1/namespaces/open-cluster-management-backup/restores` → a Restore list from mutable fake state (secondary hub);
- **Generic named Restore GET:**
  `GET /apis/cluster.open-cluster-management.io/v1beta1/namespaces/open-cluster-management-backup/restores/<name>`
  — returns the specific Restore resource for any `<name>` present in mutable state;
  — returns a Kubernetes 404 Status object for an absent name:
  ```json
  {"apiVersion":"v1","kind":"Status","metadata":{},"status":"Failure",
   "message":"restores.cluster.open-cluster-management.io \"<name>\" not found",
   "reason":"NotFound","code":404}
  ```
  The response body must be a valid Kubernetes object that the dynamic client can deserialize (correct `apiVersion`, `kind`, `metadata`, and `status` fields). A plain HTTP 404 with an empty body is **not** sufficient.
  > **Note (B6-C1):** `kubernetes.core.k8s state: absent` performs a named GET before issuing DELETE to check whether the resource exists. A fake that implements DELETE without a generic named GET will cause `k8s state: absent` to fail at the pre-DELETE probe, never reaching the DELETE call. Any execute-mode cleanup task using `k8s state: absent` therefore requires this generic GET route to be implemented correctly in the fake.
- `DELETE /apis/cluster.open-cluster-management.io/v1beta1/namespaces/open-cluster-management-backup/restores/<name>` for execute-mode cleanup:
  — returns 200 with the deleted resource body on success;
  — removes the Restore from mutable state so subsequent LIST and GET reflect its absence;
  — returns Kubernetes 404 Status for deleting an absent name.
- `GET /apis/cluster.open-cluster-management.io/v1beta1`
- an optional `list_failures` / `get_failures` / `delete_failures` map that returns HTTP 500 for a route, so tests can assert a live query failure is **fatal**. `delete_failures` follows the same dict-keyed-by-name pattern as the other failure maps.
- mutable namespaced Restore state so deletes persist in subsequent list and named-GET calls (execute-mode wait can observe candidate removal).
- valid Kubernetes discovery payloads for the operator and backup API groups, with:
  - namespaced `MultiClusterHub` resource in operator discovery (`multiclusterhubs`, verbs `get/list`);
  - namespaced `Restore` resource in backup discovery (`restores`, verbs
    `get/list/delete`), matching the fake's named DELETE route.
- request logs or route-specific hit counters proving route reachability and precedence:
  - `request_count` — aggregate count of every fake Kubernetes API request, used
    to prove direct candidate mode performs zero API requests; increment it at
    the start of every implemented HTTP method before route dispatch;
  - `secondary_mch_list_hits`
  - `secondary_restore_list_hits`
  - `old_hub_restore_named_get_hits` — dict keyed by Restore name (e.g., `{"restore-acm-passive-sync": 1, "restore-acm-full": 1}`); the aggregate scalar `old_hub_restore_get_hits` must equal the sum of all values for backwards compatibility with B5-era test assertions
  - `secondary_restore_delete_hits` (aggregate scalar)
  - `secondary_restore_named_delete_hits` — dict keyed by Restore name, proving per-name DELETE reachability

Required discovery-shape examples (authoritative contract for fake responses):

```json
{
  "kind": "APIResourceList",
  "groupVersion": "operator.open-cluster-management.io/v1",
  "resources": [
    {
      "name": "multiclusterhubs",
      "singularName": "multiclusterhub",
      "namespaced": true,
      "kind": "MultiClusterHub",
      "verbs": ["get", "list"]
    }
  ]
}
```

```json
{
  "kind": "APIResourceList",
  "groupVersion": "cluster.open-cluster-management.io/v1beta1",
  "resources": [
    {
      "name": "restores",
      "singularName": "restore",
      "namespaced": true,
      "kind": "Restore",
      "verbs": ["get", "list", "delete"]
    }
  ]
}
```

Reuse the existing `write_kubeconfig(path, context=..., server=hub.url)` helper from `argocd_fake_api.py` (import it) to point a hub's kubeconfig at the fake server.

**Step 2: Add a normally collected fake-infrastructure CRUD contract test**

Create `test_finalization_fake_api.py` with an ordinary pytest-collected test
named `test_fake_restore_crud_contract`. Do **not** rely on an uncollected
`_test_*` helper inside `finalization_fake_api.py`.

Use `urllib.request` (or an equivalent standard-library client) to exercise the
fake directly without `ansible-playbook`. A small test-local request helper may
decode successful JSON and catch `urllib.error.HTTPError` so the test can assert
both status and Kubernetes Status bodies. Import `FakeAcmBackupHub` and
`restore_fixture` from `finalization_fake_api`; the collected test must use the
fake as a context manager and prove this complete chain:

```python
def test_fake_restore_crud_contract():
    name = "restore-acm-passive-sync"
    other = "restore-acm-full"
    seeded = [
        restore_fixture(name, resource_version="10"),
        restore_fixture(other, resource_version="11"),
    ]
    with FakeAcmBackupHub(restores=seeded) as hub:
        named_url = f"{hub.restore_collection_url}/{name}"
        status, payload = _request("GET", named_url)
        assert status == 200 and payload["metadata"]["name"] == name
        assert hub.old_hub_restore_named_get_hits[name] == 1

        status, payload = _request("DELETE", named_url)
        assert status in {200, 204}
        assert hub.secondary_restore_named_delete_hits[name] == 1

        status, payload = _request("GET", hub.restore_collection_url)
        assert status == 200
        assert name not in {item["metadata"]["name"] for item in payload["items"]}

        status, payload = _request("GET", named_url)
        assert status == 404
        assert payload["kind"] == "Status" and payload["code"] == 404

        status, payload = _request("DELETE", named_url)
        assert status == 404
        assert payload["kind"] == "Status" and payload["code"] == 404

    with FakeAcmBackupHub(
        restores=[seeded[1]],
        delete_failures={other: True},
    ) as hub:
        status, payload = _request("DELETE", f"{hub.restore_collection_url}/{other}")
        assert status == 500
        assert payload["kind"] == "Status"
        assert payload["reason"] == "InternalError"
        assert hub.secondary_restore_named_delete_hits[other] == 1
```

The implementation may expose `restore_collection_url` as a convenience
property or construct the route in the test, but the assertions are mandatory:

1. GET existing named Restore succeeds.
2. The named GET counter increments.
3. DELETE existing Restore succeeds.
4. The named DELETE counter increments.
5. LIST omits the deleted Restore.
6. GET deleted Restore returns Kubernetes 404 Status.
7. DELETE deleted Restore returns Kubernetes 404 Status.
8. `delete_failures` returns the intended route-specific failure.

Add a normally collected discovery contract alongside the CRUD contract:

```python
def test_fake_restore_discovery_advertises_delete():
    with FakeAcmBackupHub(restores=[]) as hub:
        status, payload = _request(
            "GET",
            f"{hub.url}/apis/cluster.open-cluster-management.io/v1beta1",
        )
    restore_resource = next(
        item for item in payload["resources"] if item["name"] == "restores"
    )
    assert status == 200
    assert restore_resource["verbs"] == ["get", "list", "delete"]
```

This discovery assertion supplements rather than replaces the existing
named-GET, collection-LIST, named-DELETE, 404, route-failure, and counter
assertions in `test_fake_restore_crud_contract`.

Add another normally collected test,
`test_canonical_finalization_fixture_contract`, that calls both builders and
asserts the full identity:

```python
def test_canonical_finalization_fixture_contract():
    restore = restore_fixture("restore-acm-full", resource_version="70")
    assert restore["apiVersion"] == RESTORE_API_VERSION
    assert restore["kind"] == "Restore"
    assert restore["metadata"] == {
        "name": "restore-acm-full",
        "namespace": RESTORE_NAMESPACE,
        "resourceVersion": "70",
    }

    mch = multiclusterhub_fixture(resource_version="71")
    assert mch["apiVersion"] == MCH_API_VERSION
    assert mch["kind"] == "MultiClusterHub"
    assert mch["metadata"] == {
        "name": "multiclusterhub",
        "namespace": MCH_NAMESPACE,
        "resourceVersion": "71",
    }
    assert mch["status"]["currentVersion"] == "2.14.0"

    with pytest.raises(ValueError, match="complete Kubernetes Restore"):
        FakeAcmBackupHub(restores=[{"metadata": {"name": "partial"}}])
    with pytest.raises(ValueError, match="complete Kubernetes MultiClusterHub"):
        FakeAcmBackupHub(
            restores=[],
            multiclusterhubs=[{"metadata": {"name": "partial"}}],
        )
```

Import the four constants, `multiclusterhub_fixture`, and `pytest` for this
test. Constructor validation must run before server/thread startup so these
negative assertions cannot leak a test server.

Run:

```bash
python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/integration/test_finalization_fake_api.py -q
```

Expected: PASS with `test_fake_restore_crud_contract` collected normally.

**Step 3: Smoke test the fake resolves via `k8s_info` (temporary, then delete)**

Run a throwaway `ansible-playbook` that does a `k8s_info` Restore read against the fake and prints the count; confirm it returns the seeded resources. Delete the throwaway after confirming.

**Step 4: Commit**

```bash
git add ansible_collections/tomazb/acm_switchover/tests/integration/finalization_fake_api.py \
        ansible_collections/tomazb/acm_switchover/tests/integration/test_finalization_fake_api.py
git commit -m "test(collection): add executable fake ACM backup API for finalization runtime tests"
```

---

## Task 4: Shared staged fail-closed shape-validation include

**Files:**
- Create: `ansible_collections/tomazb/acm_switchover/roles/finalization/tasks/assert_restore_source_shape.yml`
- Create: `ansible_collections/tomazb/acm_switchover/tests/integration/playbooks/finalization_assert_restore_source_shape.yml`
- Test: `ansible_collections/tomazb/acm_switchover/tests/integration/test_restore_source_shape_contract.py`

**Design (satisfies design §6.1 + revision items 8 & 9):** staged assertions on a caller-supplied `_acm_restore_source`, structured so undefined / non-mapping inputs reach the controlled sanitized `fail_msg` at stage 1 (before any `.resources` access), and mapping-valued `resources` is explicitly rejected.

```yaml
---
# Fail-closed, staged validation of an authoritative Restore source.
# A deliberately-seeded {resources: []} is valid authoritative absence;
# undefined / non-mapping / skipped / non-list / mapping-valued resources /
# malformed entries must fail with a sanitized message and never degrade into
# an empty successful result. (R3-01b-DESIGN-B2 section 6.1; PLAN-B9 items 8-9)

- name: Stage 1 — Restore source must be a usable mapping ({{ _acm_restore_source_label }})
  ansible.builtin.assert:
    that:
      - _acm_restore_source is mapping
      - not (_acm_restore_source.skipped | default(false))
    fail_msg: >-
      {{ _acm_restore_source_label }} Restore source is undefined, skipped, or
      not a mapping; refusing to treat it as an empty successful result.
    quiet: true

# Reached only if stage 1 passed (assert halts on failure), so .resources access is safe.
- name: Stage 2 — Restore source 'resources' must be a list ({{ _acm_restore_source_label }})
  ansible.builtin.assert:
    that:
      - "'resources' in _acm_restore_source"
      - (_acm_restore_source.resources | type_debug) == 'list'
      - _acm_restore_source.resources is not string
      - _acm_restore_source.resources is not mapping
    fail_msg: >-
      {{ _acm_restore_source_label }} Restore source has a missing, non-list, or
      mapping-valued 'resources'; refusing to treat it as an empty successful result.
    quiet: true

- name: Stage 3 — every Restore entry must be a mapping ({{ _acm_restore_source_label }})
  ansible.builtin.assert:
    that:
      - (_acm_restore_source.resources | reject('mapping') | list | length) == 0
    fail_msg: >-
      {{ _acm_restore_source_label }} Restore source contains a non-mapping entry;
      refusing to classify malformed data.
    quiet: true
```

Stage 2's `type_debug == 'list'` check is the final acceptance contract: tuple-like, generator-like, mapping, string, numeric, null, or arbitrary iterable values must fail.

**Step 2: Add a shared undefined-source contract harness (C2)**

Create/extend a dedicated harness for `assert_restore_source_shape.yml` that invokes the include with `_acm_restore_source` genuinely undefined.

Require:
- non-zero playbook exit;
- stable sanitized failure marker/message;
- no uncontrolled Jinja undefined-variable traceback exposed as the asserted contract;
- no authoritative publication.

This shared harness proves the generic undefined-source fail-closed contract and replaces unreachable runtime undefined-path expectations in Task 6.

**Note (item 9):** the caller (Tasks 5 & 6) must select the candidate so `_acm_restore_source` is always *defined* (default to a non-mapping sentinel such as `None` when the source variable is absent), guaranteeing `is mapping` evaluates to `False` and reaches the stage-1 `fail_msg` rather than raising.

**Step 3: Run the shared validator harness test**

Run: `python -m pytest ansible_collections/tomazb/acm_switchover/tests/integration/test_restore_source_shape_contract.py -q`
Expected: PASS; the undefined-source case must fail closed with sanitized output and without authoritative publication.

**Step 4: Commit**

```bash
git add ansible_collections/tomazb/acm_switchover/roles/finalization/tasks/assert_restore_source_shape.yml \
        ansible_collections/tomazb/acm_switchover/tests/integration/playbooks/finalization_assert_restore_source_shape.yml \
        ansible_collections/tomazb/acm_switchover/tests/integration/test_restore_source_shape_contract.py
git commit -m "test(collection): Task 4 shared staged fail-closed shape validator with undefined-source harness"
```

---

## Task 5: R3-A2 — candidate → validate → publish (truthful dry-run preview)

**Files:**
- Create: `ansible_collections/tomazb/acm_switchover/tests/integration/playbooks/finalization_cleanup_restores.yml`
- Test: `ansible_collections/tomazb/acm_switchover/tests/integration/test_finalization_cleanup_restores_runtime.py`
- Modify: `ansible_collections/tomazb/acm_switchover/roles/finalization/tasks/cleanup_restores.yml` (current lines 1–14)
- Modify (unit): `ansible_collections/tomazb/acm_switchover/tests/unit/test_finalization_verification.py`

**Step 1: Create the harness playbook FIRST (so the first red run fails on the real defect, not a missing file — item 7)**

`tests/integration/playbooks/finalization_cleanup_restores.yml`:

```yaml
---
- name: Exercise finalization Restore-cleanup source selection
  hosts: localhost
  connection: local
  gather_facts: false
  tasks:
    - name: Run cleanup_restores source selection and classification
      ansible.builtin.include_role:
        name: tomazb.acm_switchover.finalization
        tasks_from: cleanup_restores
    - name: Report cleanup preview
      ansible.builtin.debug:
        msg: >-
          RESTORE_COUNT={{ acm_switchover_cleanup_restores_result.restore_count }}
          RESTORE_NAMES={{ acm_switchover_cleanup_restores_result.restore_names | join(',') }}
```

**Step 2: Write the failing dry-run behavior test**

Harness helper `_run` reads controller executable + interpreter from the environment (item 13):

```python
import os, subprocess
from pathlib import Path
import yaml, pytest

REPO_ROOT = Path(__file__).resolve().parents[5]
PLAYBOOK = ("ansible_collections/tomazb/acm_switchover/tests/integration/"
            "playbooks/finalization_cleanup_restores.yml")
ANSIBLE_BIN = os.environ.get("ACM_ANSIBLE_PLAYBOOK_BIN", "ansible-playbook")
ANSIBLE_PY = os.environ.get("ACM_ANSIBLE_PYTHON", None)  # None -> playbook default

def _run(tmp_path, variables):
    v = dict(variables)
    if ANSIBLE_PY:
        v["ansible_python_interpreter"] = ANSIBLE_PY
    vf = tmp_path / "vars.yml"; vf.write_text(yaml.safe_dump(v, sort_keys=False))
    return subprocess.run(
        [ANSIBLE_BIN, PLAYBOOK,
         "-i", "ansible_collections/tomazb/acm_switchover/examples/inventory.yml",
         "-e", f"@{vf}"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=False, timeout=300)

def _dry_run_vars(restores):
    return {
        "acm_switchover_execution": {"mode": "dry_run"},
        "acm_switchover_hubs": {
            "secondary": {"kubeconfig": "/dev/null", "context": "sec"},
            "primary": {"kubeconfig": "/dev/null", "context": "pri"}},
        "acm_switchover_operation": {"old_hub_action": "secondary", "restore_only": False},
        "acm_finalization_restores_info": {"resources": restores},
    }

def test_dry_run_preview_reports_true_restore_count(tmp_path):
    r = _run(tmp_path, _dry_run_vars([
        {"metadata": {"name": "restore-acm-full"}},
        {"metadata": {"name": "restore-acm-passive-sync"}}]))
    assert r.returncode == 0, r.stderr
    assert "RESTORE_COUNT=2" in r.stdout, r.stdout
```

**Step 3: Run — verify it fails on the real defect**

Run: `python -m pytest .../test_finalization_cleanup_restores_runtime.py::test_dry_run_preview_reports_true_restore_count -q`
Expected: FAIL with `RESTORE_COUNT=0` (skipped-register clobber), **not** a missing-playbook error.

**Step 4: Fix `cleanup_restores.yml` (candidate → staged validate → publish)**

```yaml
---
- name: Read Restore resources on the secondary hub (execute mode)
  kubernetes.core.k8s_info:
    api_version: cluster.open-cluster-management.io/v1beta1
    kind: Restore
    namespace: open-cluster-management-backup
    kubeconfig: "{{ acm_switchover_hubs.secondary.kubeconfig }}"
    context: "{{ acm_switchover_hubs.secondary.context }}"
  register: _acm_secondary_restores_live_info
  when: acm_switchover_execution.mode | default('dry_run') != 'dry_run'

- name: Select Restore cleanup candidate (defined, possibly non-mapping sentinel)
  ansible.builtin.set_fact:
    _acm_secondary_restores_candidate: >-
      {{
        _acm_secondary_restores_live_info
        if (acm_switchover_execution.mode | default('dry_run') != 'dry_run')
        else (acm_finalization_restores_info if (acm_finalization_restores_info is defined) else None)
      }}

- name: Validate Restore cleanup candidate (fail-closed, staged)
  ansible.builtin.include_tasks: assert_restore_source_shape.yml
  vars:
    _acm_restore_source: "{{ _acm_secondary_restores_candidate }}"
    _acm_restore_source_label: "finalization cleanup"

- name: Publish authoritative Restore cleanup source (only after validation)
  ansible.builtin.set_fact:
    _acm_secondary_restores_to_cleanup: "{{ _acm_secondary_restores_candidate }}"
```

The previous `| default({'resources': []})` silent fallback is removed. Malformed data is never published under the authoritative name — the assert halts first (item 8). The downstream classification/deletion tasks are unchanged.

**Step 5: Run — verify pass**

Run: same test → PASS (`RESTORE_COUNT=2`).

**Step 6: Add the R3-A2 negative + positive-absence matrix (items 9)**

```python
@pytest.mark.parametrize("payload,idv", [
    ("__undefined__", "undefined"),
    ({}, "missing-resources-key"),
    ({"resources": "text"}, "resources-string"),
    ({"resources": 1}, "resources-number"),
    ({"resources": None}, "resources-null"),
    ({"resources": {}}, "empty-mapping-resources"),
    ({"resources": {"unexpected": "value"}}, "mapping-resources-unexpected-value"),
    ({"changed": False, "skipped": True}, "skipped-result"),
    ({"resources": ["not-a-mapping"]}, "malformed-entry"),
])
def test_dry_run_fails_closed_on_bad_source(tmp_path, payload, idv):
    v = _dry_run_vars([])
    if payload == "__undefined__":
        del v["acm_finalization_restores_info"]
    else:
        v["acm_finalization_restores_info"] = payload
    r = _run(tmp_path, v)
    assert r.returncode != 0, r.stdout
    assert "refusing to treat it as an empty successful result" in (r.stdout + r.stderr) \
        or "refusing to classify malformed data" in (r.stdout + r.stderr)

def test_dry_run_accepts_deliberate_empty_absence(tmp_path):
    r = _run(tmp_path, _dry_run_vars([]))
    assert r.returncode == 0 and "RESTORE_COUNT=0" in r.stdout
```

**Step 7: Add R3-A2 deterministic execute-mode runtime tests (fake API — item 10)**

Define three separate execute-mode scenarios using `finalization_fake_api.FakeAcmBackupHub` + `write_kubeconfig`.

> **Rationale (B6-C2):** a single combined execute test cannot independently prove each safety invariant. Splitting into three scenarios ensures each failure mode is attributable to the intended route and that the unexpected-Restore safety gate is tested without conflating it with API-failure behavior.

**Scenario A — Candidate-only successful cleanup**

```python
def test_execute_mode_candidate_cleanup_success(tmp_path):
    """Prove the full cleanup path: fresh live list overrides stale pre-seed,
    every candidate is individually probed (GET) then deleted (DELETE),
    and the post-delete wait observes candidate removal."""
    with FakeAcmBackupHub(restores=[
        restore_fixture("restore-acm-full", resource_version="20", status={"phase": "Finished"}),
        restore_fixture(
            "restore-acm-passive-sync",
            resource_version="21",
            status={"phase": "Finished"},
        ),
    ]) as hub:
        # Seed a stale pre-seeded discovery that must be overridden by the live query
        stale_restores = [restore_fixture("restore-stale", resource_version="19")]
        r = _run(tmp_path, _execute_vars(hub, stale_seed=stale_restores))
    assert r.returncode == 0, r.stderr
    # Stale pre-seeded discovery is ignored; fresh live candidates are classified
    assert "restore-stale" not in r.stdout
    # Every candidate receives one named GET (for k8s state: absent probe) and one DELETE
    assert hub.old_hub_restore_named_get_hits.get("restore-acm-full", 0) == 1
    assert hub.old_hub_restore_named_get_hits.get("restore-acm-passive-sync", 0) == 1
    assert hub.secondary_restore_named_delete_hits.get("restore-acm-full", 0) == 1
    assert hub.secondary_restore_named_delete_hits.get("restore-acm-passive-sync", 0) == 1
    # Candidate count/names are correct
    assert "RESTORE_COUNT=2" in r.stdout
    # Subsequent list omits deleted candidates (wait logic observes removal)
    # (implicitly asserted by successful returncode after delete loop + wait)
```

Require:
- stale pre-seeded discovery is ignored and fresh live candidates are classified correctly;
- every candidate receives one named GET and one DELETE;
- candidate count/names match the seeded switchover candidates;
- subsequent list omits deleted candidates (wait loop terminates successfully);
- test passes with `returncode == 0`.

**Scenario B — Unexpected-Restore blocker**

```python
def test_execute_mode_unexpected_restore_blocks_cleanup(tmp_path):
    """Prove the unexpected-Restore safety gate: when at least one unrecognized
    Restore is present alongside valid candidates, the task must refuse to
    delete anything and fail with a sanitized refusal message."""
    unexpected = restore_fixture(
        "restore-acm-unexpected",
        resource_version="31",
        status={"phase": "Finished"},
    )
    candidate = restore_fixture(
        "restore-acm-full",
        resource_version="30",
        status={"phase": "Finished"},
    )
    with FakeAcmBackupHub(restores=[candidate, unexpected]) as hub:
        r = _run(tmp_path, _execute_vars(hub, stale_seed=[]))
    # Task must fail at the existing unexpected-Restore safety gate
    assert r.returncode != 0, r.stdout
    # Failure is the intended sanitized refusal, not a fake API or discovery failure
    assert ("unexpected" in (r.stdout + r.stderr).lower()
            or "unrecognized" in (r.stdout + r.stderr).lower()), r.stdout + r.stderr
    # Zero DELETE calls for candidates
    assert hub.secondary_restore_named_delete_hits.get("restore-acm-full", 0) == 0
    # Zero DELETE calls for the unexpected Restore
    assert hub.secondary_restore_named_delete_hits.get("restore-acm-unexpected", 0) == 0
    # Unexpected resource remains in fake state (list still returns it)
    assert hub.secondary_restore_list_hits >= 1
```

Require:
- task fails at the existing unexpected-Restore safety gate;
- zero DELETE calls occur for candidates;
- zero DELETE calls occur for the unexpected Restore;
- unexpected resource remains in fake state;
- failure is the intended sanitized refusal, not a fake API or discovery failure.

**Scenario C — API failures**

Define three separate tests, each attributable to exactly one failure route:

```python
def test_execute_mode_list_failure_is_fatal(tmp_path):
    """A 500 on the Restore LIST route must cause a fatal, non-zero exit."""
    with FakeAcmBackupHub(
        restores=[restore_fixture("restore-acm-full", resource_version="40")],
        list_failures={"restores": True},
    ) as hub:
        r = _run(tmp_path, _execute_vars(hub, stale_seed=[]))
    assert r.returncode != 0, r.stdout
    # Failure attributable to the list route, not a DELETE or GET route
    assert hub.secondary_restore_delete_hits == 0

def test_execute_mode_named_get_failure_is_fatal(tmp_path):
    """A 500 on the named Restore GET route (pre-DELETE probe) must cause a
    fatal exit; no DELETE calls must follow."""
    with FakeAcmBackupHub(
        restores=[restore_fixture("restore-acm-full", resource_version="41")],
        get_failures={"restore-acm-full": True},
    ) as hub:
        r = _run(tmp_path, _execute_vars(hub, stale_seed=[]))
    assert r.returncode != 0, r.stdout
    assert hub.secondary_restore_named_delete_hits.get("restore-acm-full", 0) == 0

def test_execute_mode_delete_failure_is_fatal(tmp_path):
    """A 500 on the Restore DELETE route must cause a fatal exit."""
    with FakeAcmBackupHub(
        restores=[restore_fixture("restore-acm-full", resource_version="42")],
        delete_failures={"restore-acm-full": True},
    ) as hub:
        r = _run(tmp_path, _execute_vars(hub, stale_seed=[]))
    assert r.returncode != 0, r.stdout
```

Each failure must be attributable to the intended route (list / named GET / DELETE).

**Step 8: Add static unit contract**

```python
def test_cleanup_restores_has_no_register_setfact_name_collision():
    text = (FINALIZATION_TASKS / "cleanup_restores.yml").read_text()
    assert "register: _acm_secondary_restores_live_info" in text
    assert "register: _acm_secondary_restores_to_cleanup\n" not in text
    assert "default({'resources': []})" not in text
    assert "assert_restore_source_shape.yml" in text
    # publish happens after the validation include
    assert text.index("assert_restore_source_shape.yml") < text.index("_acm_secondary_restores_to_cleanup:")
```

**Step 9: Run + commit**

Run: `python -m pytest ansible_collections/tomazb/acm_switchover/tests/integration/test_finalization_cleanup_restores_runtime.py ansible_collections/tomazb/acm_switchover/tests/unit/test_finalization_verification.py -q`
Expected: PASS

```bash
git add ansible_collections/tomazb/acm_switchover/roles/finalization/tasks/cleanup_restores.yml \
        ansible_collections/tomazb/acm_switchover/tests/integration/playbooks/finalization_cleanup_restores.yml \
        ansible_collections/tomazb/acm_switchover/tests/integration/test_finalization_cleanup_restores_runtime.py \
        ansible_collections/tomazb/acm_switchover/tests/unit/test_finalization_verification.py
git commit -m "fix(collection): R3-A2 truthful finalization dry-run Restore preview via candidate/validate/publish"
```

---

## Task 6: R3-A3 — old-hub restore distinct names, candidate/validate/publish, fixture + execute-refresh

**Files:**
- Create: `ansible_collections/tomazb/acm_switchover/tests/integration/playbooks/finalization_old_hub_restore_discovery.yml`
- Test: `ansible_collections/tomazb/acm_switchover/tests/integration/test_finalization_old_hub_restore_runtime.py`
- Modify: `ansible_collections/tomazb/acm_switchover/roles/finalization/tasks/discover_resources.yml` (current lines 54–78)
- Modify (unit): `ansible_collections/tomazb/acm_switchover/tests/unit/test_discover_resources_contracts.py`

**Step 1: Create the harness playbook FIRST (item 7)**

`finalization_old_hub_restore_discovery.yml` initializes stable Boolean mode
markers, then makes normal discovery and direct-candidate validation mutually
exclusive:

```yaml
    - name: Initialize harness mode markers
      ansible.builtin.set_fact:
        normal_mode_entered: false
        direct_mode_entered: false
        normal_backup_schedule_preseeded: false
        normal_mch_preseeded: false
        normal_secondary_restores_preseeded: false
        normal_role_completed: false
        old_hub_seed_register_behavior_reached: false
        old_hub_authoritative_defined_before_normal: false

    - name: Run normal finalization discovery
      when: acm_test_direct_old_hub_candidate is not defined
      block:
        - name: Mark normal discovery mode and capture pre-role fixture state
          ansible.builtin.set_fact:
            normal_mode_entered: true
            normal_backup_schedule_preseeded: >-
              {{ acm_finalization_backup_schedules_info is defined }}
            normal_mch_preseeded: "{{ acm_finalization_mch_info is defined }}"
            normal_secondary_restores_preseeded: >-
              {{ acm_finalization_restores_info is defined }}
            old_hub_authoritative_defined_before_normal: >-
              {{ _old_hub_existing_restore_info is defined }}
        - name: Execute discover_resources
          ansible.builtin.include_role:
            name: tomazb.acm_switchover.finalization
            tasks_from: discover_resources
        - name: Mark normal discovery role completed
          ansible.builtin.set_fact:
            normal_role_completed: true
            old_hub_seed_register_behavior_reached: true
      rescue:
        - name: Mark candidate-validation failure path
          ansible.builtin.set_fact:
            old_hub_discovery_failed: true
        - name: Re-raise discovery failure after marker capture
          ansible.builtin.fail:
            msg: "{{ ansible_failed_result.msg | default(ansible_failed_result | string) }}"
      always:
        - name: Report old-hub restore discovery outcome
          ansible.builtin.debug:
            msg: >-
              NORMAL_MODE_ENTERED={{ normal_mode_entered }}
              DIRECT_MODE_ENTERED={{ direct_mode_entered }}
              NORMAL_BACKUP_SCHEDULE_PRESEEDED={{ normal_backup_schedule_preseeded }}
              NORMAL_MCH_PRESEEDED={{ normal_mch_preseeded }}
              NORMAL_SECONDARY_RESTORES_PRESEEDED={{ normal_secondary_restores_preseeded }}
              NORMAL_ROLE_COMPLETED={{ normal_role_completed }}
              OLD_HUB_SEED_REGISTER_REACHED={{ old_hub_seed_register_behavior_reached }}
              OLD_HUB_AUTHORITATIVE_DEFINED_BEFORE_NORMAL={{ old_hub_authoritative_defined_before_normal }}
              OLD_HUB_RESULT_SKIPPED={{ (_old_hub_existing_restore_info | default({})).skipped | default(false) }}
              OLD_HUB_HAS_RESOURCES_KEY={{ (_old_hub_existing_restore_info is mapping) and ('resources' in _old_hub_existing_restore_info) }}
              OLD_HUB_COUNT={{ (_old_hub_existing_restore_info.resources | default([]) | length) if (_old_hub_existing_restore_info is mapping) else -1 }}
              OLD_HUB_AUTHORITATIVE_DEFINED_AFTER_FAILURE={{ (_old_hub_existing_restore_info is defined) if (old_hub_discovery_failed | default(false)) else 'N/A' }}
```

The harness supports two execution modes controlled by test variables:

- **Normal mode** (no `acm_test_direct_old_hub_candidate`): the explicitly
  guarded normal block sets `NORMAL_MODE_ENTERED=True`, runs
  `include_role tasks_from: discover_resources`, leaves
  `DIRECT_MODE_ENTERED=False`, and exercises the full live-discovery or fixture
  path through the role.
- **Direct candidate mode** (`acm_test_direct_old_hub_candidate` is defined):
  the normal block is skipped, so `NORMAL_MODE_ENTERED=False`; the direct block
  sets `DIRECT_MODE_ENTERED=True`, proves the authoritative fact is undefined
  before validation, and directly exercises candidate → staged validator →
  publication with a simulated module result. It never runs
  `tasks_from: discover_resources` and performs zero fake Kubernetes API
  requests. See Step 6 for the concrete implementation.

Use the following complete, scenario-specific builders in
`test_finalization_old_hub_restore_runtime.py`. Do not use one shared
“pre-seed everything” helper: each builder makes route suppression explicit.
`FakeAcmBackupHub` must **not** gain BackupSchedule discovery behavior.
BackupSchedule discovery is unrelated to R3-A3, so every normal-mode builder
deliberately defines `acm_finalization_backup_schedules_info`.

```python
_UNSET = object()


def _base_old_hub_vars(mode, primary_kubeconfig, secondary_kubeconfig):
    return {
        "acm_switchover_execution": {"mode": mode},
        "acm_switchover_operation": {
            "old_hub_action": "secondary",
            "restore_only": False,
        },
        "acm_switchover_hubs": {
            "primary": {
                "kubeconfig": str(primary_kubeconfig),
                "context": "primary",
            },
            "secondary": {
                "kubeconfig": str(secondary_kubeconfig),
                "context": "secondary",
            },
        },
    }


def _canonical_old_hub_fixture(resource_version="50"):
    return {
        "resources": [
            restore_fixture(
                "restore-acm-passive-sync",
                resource_version=resource_version,
                status={"phase": "Finished"},
            )
        ]
    }


def _dry_run_normal_vars(*, old_hub_fixture=_UNSET):
    variables = _base_old_hub_vars("dry_run", "/dev/null", "/dev/null")
    variables.update(
        {
            "acm_finalization_backup_schedules_info": {"resources": []},
            "acm_finalization_mch_info": {"resources": []},
            "acm_finalization_restores_info": {"resources": []},
        }
    )
    if old_hub_fixture is not _UNSET:
        variables["_old_hub_existing_restore_info"] = old_hub_fixture
    return variables


def _execute_normal_vars(
    tmp_path,
    hub,
    *,
    old_hub_fixture=_UNSET,
    secondary_restore_preseed=_UNSET,
    mch_preseed=_UNSET,
):
    primary_kubeconfig = tmp_path / "primary.kubeconfig"
    secondary_kubeconfig = tmp_path / "secondary.kubeconfig"
    write_kubeconfig(primary_kubeconfig, context="primary", server=hub.url)
    write_kubeconfig(secondary_kubeconfig, context="secondary", server=hub.url)
    variables = _base_old_hub_vars(
        "execute",
        primary_kubeconfig,
        secondary_kubeconfig,
    )
    variables["acm_finalization_backup_schedules_info"] = {"resources": []}
    if old_hub_fixture is not _UNSET:
        variables["_old_hub_existing_restore_info"] = old_hub_fixture
    if secondary_restore_preseed is not _UNSET:
        variables["acm_finalization_restores_info"] = {
            "resources": secondary_restore_preseed,
        }
    if mch_preseed is not _UNSET:
        variables["acm_finalization_mch_info"] = {
            "resources": mch_preseed,
        }
    return variables


def _direct_candidate_vars(tmp_path, hub, candidate):
    primary_kubeconfig = tmp_path / "primary-direct.kubeconfig"
    secondary_kubeconfig = tmp_path / "secondary-direct.kubeconfig"
    write_kubeconfig(primary_kubeconfig, context="primary", server=hub.url)
    write_kubeconfig(secondary_kubeconfig, context="secondary", server=hub.url)
    variables = _base_old_hub_vars(
        "dry_run",
        primary_kubeconfig,
        secondary_kubeconfig,
    )
    variables["acm_test_direct_old_hub_candidate"] = candidate
    return variables
```

The dry-run common pre-seeds are exactly:

```yaml
acm_finalization_backup_schedules_info:
  resources: []
acm_finalization_mch_info:
  resources: []
acm_finalization_restores_info:
  resources: []
```

The dry-run no-fixture builder leaves
`_old_hub_existing_restore_info` undefined. The fixture variant adds only
`_old_hub_existing_restore_info` to those common pre-seeds, using
`_canonical_old_hub_fixture()`. Thus the first live-relevant behavior reached
by the initial dry-run red test is the old-hub seed/skipped-register collision,
not BackupSchedule, MCH, or secondary Restore discovery.

The complete scenario matrix is:

| Scenario | BackupSchedule pre-seed | MCH pre-seed | Secondary Restore pre-seed | Old-hub fixture | Required fake routes |
| ---- | ---- | ---- | ---- | ---- | ---- |
| Dry-run normal, no fixture | Empty | Empty | Empty | Undefined | None |
| Dry-run normal, fixture | Empty | Empty | Empty | Canonical passive Restore | None |
| Execute normal, fixture; secondary list unrelated | Empty | Optional; never suppresses refresh | Empty | Canonical passive Restore | MCH LIST; no old-hub named GET |
| Execute normal, fixture; assert secondary list | Empty | Optional; never suppresses refresh | Undefined | Canonical passive Restore | MCH LIST + secondary Restore LIST; no old-hub named GET |
| Execute normal, no fixture | Empty | Optional; never suppresses refresh | Undefined | Undefined | MCH LIST + secondary Restore LIST + old-hub named GET |
| Direct candidate | Not required | Not required | Not required | Authoritative variable undefined | Zero aggregate requests |

For execute mode with an injected old-hub fixture:

- always define:

  ```yaml
  acm_finalization_backup_schedules_info:
    resources: []
  ```

- optionally supply `acm_finalization_mch_info`, but never describe it as
  suppressing execute-mode MCH refresh;
- pass `secondary_restore_preseed=[]` when the secondary Restore list is
  unrelated to the assertion;
- leave `secondary_restore_preseed` at `_UNSET` when asserting
  `secondary_restore_list_hits >= 1`;
- pass `old_hub_fixture=_canonical_old_hub_fixture()` and require
  `old_hub_restore_get_hits == 0`.

For execute mode without an old-hub fixture, call
`_execute_normal_vars(tmp_path, hub)` with neither
`old_hub_fixture` nor `secondary_restore_preseed`. This always pre-seeds only
the unrelated BackupSchedule source and leaves both
`acm_finalization_restores_info` and `_old_hub_existing_restore_info`
undefined. It must reach execute-mode MCH refresh, the secondary Restore list,
and the old-hub named Restore GET.

The absent variables for that scenario are exactly:

```text
acm_finalization_restores_info
_old_hub_existing_restore_info
```

Direct candidate mode uses `_direct_candidate_vars`, skips the normal block,
does not require a BackupSchedule pre-seed, begins with the authoritative
old-hub variable undefined, and retains the zero aggregate fake-request gate.

**Step 2: Write the scenario-builder contract and failing tests**

```python
def test_task6_scenario_builder_matrix(tmp_path):
    dry_no_fixture = _dry_run_normal_vars()
    assert dry_no_fixture["acm_finalization_backup_schedules_info"] == {
        "resources": []
    }
    assert dry_no_fixture["acm_finalization_mch_info"] == {"resources": []}
    assert dry_no_fixture["acm_finalization_restores_info"] == {"resources": []}
    assert "_old_hub_existing_restore_info" not in dry_no_fixture

    dry_fixture = _dry_run_normal_vars(
        old_hub_fixture=_canonical_old_hub_fixture("51"),
    )
    assert set(dry_fixture) - set(dry_no_fixture) == {
        "_old_hub_existing_restore_info"
    }

    with FakeAcmBackupHub(
        restores=[
            restore_fixture(
                "restore-acm-passive-sync",
                resource_version="52",
                status={"phase": "Finished"},
            )
        ],
        multiclusterhubs=[multiclusterhub_fixture(resource_version="53")],
    ) as hub:
        execute_fixture = _execute_normal_vars(
            tmp_path,
            hub,
            old_hub_fixture=_canonical_old_hub_fixture("54"),
            secondary_restore_preseed=[],
            mch_preseed=[multiclusterhub_fixture(resource_version="55")],
        )
        assert execute_fixture["acm_finalization_backup_schedules_info"] == {
            "resources": []
        }
        assert execute_fixture["acm_finalization_restores_info"] == {
            "resources": []
        }
        assert execute_fixture["acm_finalization_mch_info"]["resources"][0][
            "kind"
        ] == "MultiClusterHub"
        assert "_old_hub_existing_restore_info" in execute_fixture

        execute_live = _execute_normal_vars(tmp_path, hub)
        assert execute_live["acm_finalization_backup_schedules_info"] == {
            "resources": []
        }
        assert "acm_finalization_restores_info" not in execute_live
        assert "_old_hub_existing_restore_info" not in execute_live

        direct = _direct_candidate_vars(tmp_path, hub, {"resources": []})
        assert "acm_finalization_backup_schedules_info" not in direct
        assert "_old_hub_existing_restore_info" not in direct
        assert hub.request_count == 0


def _assert_normal_prerequisites_neutralized(
    result,
    *,
    authoritative_defined_before_normal,
):
    """Prove a red result reached R3-A3 rather than an unrelated prerequisite."""
    assert result.returncode == 0, result.stdout + result.stderr
    assert "NORMAL_MODE_ENTERED=True" in result.stdout, result.stdout
    assert "DIRECT_MODE_ENTERED=False" in result.stdout, result.stdout
    assert "NORMAL_BACKUP_SCHEDULE_PRESEEDED=True" in result.stdout, result.stdout
    assert "NORMAL_MCH_PRESEEDED=True" in result.stdout, result.stdout
    assert "NORMAL_SECONDARY_RESTORES_PRESEEDED=True" in result.stdout, result.stdout
    assert "NORMAL_ROLE_COMPLETED=True" in result.stdout, result.stdout
    assert "OLD_HUB_SEED_REGISTER_REACHED=True" in result.stdout, result.stdout
    expected = f"OLD_HUB_AUTHORITATIVE_DEFINED_BEFORE_NORMAL={authoritative_defined_before_normal}"
    assert expected in result.stdout, result.stdout


def test_dry_run_seed_survives(tmp_path):
    variables = _dry_run_normal_vars()
    assert "_old_hub_existing_restore_info" not in variables
    r = _run(tmp_path, variables)
    _assert_normal_prerequisites_neutralized(
        r,
        authoritative_defined_before_normal=False,
    )
    assert "OLD_HUB_RESULT_SKIPPED=False" in r.stdout, r.stdout
    assert "OLD_HUB_HAS_RESOURCES_KEY=True" in r.stdout, r.stdout
    assert "OLD_HUB_COUNT=0" in r.stdout, r.stdout

def test_injected_fixture_preserved(tmp_path):
    variables = _dry_run_normal_vars(
        old_hub_fixture=_canonical_old_hub_fixture(),
    )
    assert set(variables) - set(_dry_run_normal_vars()) == {
        "_old_hub_existing_restore_info"
    }
    r = _run(tmp_path, variables)
    _assert_normal_prerequisites_neutralized(
        r,
        authoritative_defined_before_normal=True,
    )
    assert "OLD_HUB_RESULT_SKIPPED=False" in r.stdout, r.stdout
    assert "OLD_HUB_HAS_RESOURCES_KEY=True" in r.stdout, r.stdout
    assert "OLD_HUB_COUNT=1" in r.stdout, r.stdout
```

**Step 3: Run — verify fail on the real defect**

Run both red tests with captured output visible:

```bash
python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/integration/test_finalization_old_hub_restore_runtime.py::test_dry_run_seed_survives \
  ansible_collections/tomazb/acm_switchover/tests/integration/test_finalization_old_hub_restore_runtime.py::test_injected_fixture_preserved \
  -vv
```

Expected: both tests fail only on the post-fix expectation
`OLD_HUB_RESULT_SKIPPED=False` or the immediately following authoritative-shape
assertion. The captured output must show all of:

```text
NORMAL_MODE_ENTERED=True
DIRECT_MODE_ENTERED=False
NORMAL_BACKUP_SCHEDULE_PRESEEDED=True
NORMAL_MCH_PRESEEDED=True
NORMAL_SECONDARY_RESTORES_PRESEEDED=True
NORMAL_ROLE_COMPLETED=True
OLD_HUB_SEED_REGISTER_REACHED=True
OLD_HUB_RESULT_SKIPPED=True
OLD_HUB_HAS_RESOURCES_KEY=False
```

The no-fixture case must additionally show
`OLD_HUB_AUTHORITATIVE_DEFINED_BEFORE_NORMAL=False`; the fixture case must show
`OLD_HUB_AUTHORITATIVE_DEFINED_BEFORE_NORMAL=True`. `returncode == 0` from the
harness plus `NORMAL_ROLE_COMPLETED=True` and
`OLD_HUB_SEED_REGISTER_REACHED=True` is mandatory defect-specific evidence
that kubeconfig/API discovery, BackupSchedule, MCH, and secondary Restore
prerequisites did not fail. Do not accept a red result caused by a missing
playbook, kubeconfig, API route, or any unrelated discovery task. The current
clobber must remain the first failed test expectation.

**Step 4: Fix the old-hub Restore block with explicit candidate ownership and validate-before-publish flow (C1)**

Ownership must be explicit and preserved in this order:

- fixture/input owner: `_old_hub_existing_restore_info` (only when injected before task execution)
- raw live-query owner: `_old_hub_existing_restore_live_info`
- temporary selected candidate: `_old_hub_existing_restore_candidate`
- authoritative downstream owner: `_old_hub_existing_restore_info` (published only after candidate validation)

Required mode-specific flow:

1. Dry-run + fixture supplied: copy fixture into candidate, skip live query, validate candidate, publish authoritative value from validated candidate.
2. Dry-run + no fixture: seed candidate with `{resources: []}`, skip live query, validate candidate, publish authoritative value.
3. Execute mode + fixture supplied: use fixture as candidate, skip live query, validate candidate, publish authoritative value.
4. Execute mode + no fixture: query live into `_old_hub_existing_restore_live_info`, fail on skipped/failed query shape, assign candidate from live result, validate candidate, publish authoritative value.

At no point may malformed, skipped, missing, or non-list data be assigned to `_old_hub_existing_restore_info` before validation.

```yaml
- name: Select old-hub passive restore candidate for dry-run without fixture
  ansible.builtin.set_fact:
    _old_hub_existing_restore_candidate:
      resources: []
  when:
    - acm_switchover_execution.mode | default('dry_run') == 'dry_run'
    - _old_hub_existing_restore_info is not defined
    - (acm_switchover_operation.old_hub_action | default('secondary')) == 'secondary'
    - not (acm_switchover_operation.restore_only | default(false))

- name: Select old-hub passive restore candidate from injected fixture
  ansible.builtin.set_fact:
    _old_hub_existing_restore_candidate: "{{ _old_hub_existing_restore_info }}"
  when:
    - _old_hub_existing_restore_info is defined
    - (acm_switchover_operation.old_hub_action | default('secondary')) == 'secondary'
    - not (acm_switchover_operation.restore_only | default(false))

- name: Read existing passive sync restore on old hub (execute mode, no fixture)
  kubernetes.core.k8s_info:
    api_version: cluster.open-cluster-management.io/v1beta1
    kind: Restore
    name: restore-acm-passive-sync
    namespace: open-cluster-management-backup
    kubeconfig: "{{ acm_switchover_hubs.primary.kubeconfig }}"
    context: "{{ acm_switchover_hubs.primary.context }}"
  register: _old_hub_existing_restore_live_info
  when:
    - _old_hub_existing_restore_info is not defined
    - acm_switchover_execution.mode | default('dry_run') != 'dry_run'
    - (acm_switchover_operation.old_hub_action | default('secondary')) == 'secondary'
    - not (acm_switchover_operation.restore_only | default(false))

- name: Select old-hub passive restore candidate from execute live query
  ansible.builtin.set_fact:
    _old_hub_existing_restore_candidate: "{{ _old_hub_existing_restore_live_info }}"
  when:
    - _old_hub_existing_restore_info is not defined
    - _old_hub_existing_restore_live_info is defined
    - not (_old_hub_existing_restore_live_info.skipped | default(false))

- name: Validate selected old-hub passive restore candidate (fail-closed, staged)
  ansible.builtin.include_tasks: assert_restore_source_shape.yml
  vars:
    _acm_restore_source: "{{ _old_hub_existing_restore_candidate | default(None) }}"
    _acm_restore_source_label: "old-hub passive restore"
  when:
    - (acm_switchover_operation.old_hub_action | default('secondary')) == 'secondary'
    - not (acm_switchover_operation.restore_only | default(false))

- name: Publish authoritative old-hub passive restore source (only after validation)
  ansible.builtin.set_fact:
    _old_hub_existing_restore_info: "{{ _old_hub_existing_restore_candidate }}"
  when:
    - _old_hub_existing_restore_candidate is defined
    - (acm_switchover_operation.old_hub_action | default('secondary')) == 'secondary'
    - not (acm_switchover_operation.restore_only | default(false))
```

`_old_hub_existing_restore_info` is now `set_fact`-only; the live read registers to `_old_hub_existing_restore_live_info` → no same-name collision. Execute-mode refresh preserved; injected fixtures still win (`is not defined` guard).

**Step 5: Run — verify pass**

Expected: PASS (the scenario-builder contract and both behavior tests).

**Step 6: Add execute-mode fake-API + fatal + negative tests (items 9, 10)**

- **Dry-run no fixture succeeds with deliberate absence:** use
  `_dry_run_normal_vars()`; assert all three unrelated pre-seed markers,
  `OLD_HUB_HAS_RESOURCES_KEY=True`, `OLD_HUB_COUNT=0`, and that
  `_old_hub_existing_restore_info` was absent from the input variables.
- **Dry-run with valid fixture preserves fixture:** use
  `_dry_run_normal_vars(old_hub_fixture=_canonical_old_hub_fixture())`; assert
  the old-hub fixture is the only addition to the common dry-run variables and
  its count is retained rather than replaced by a skipped result.
- **Execute mode with valid fixture preserves fixture and skips only the
  old-hub named GET:** use
  `_execute_normal_vars(tmp_path, hub,
  old_hub_fixture=_canonical_old_hub_fixture(),
  secondary_restore_preseed=[])` when the secondary list is unrelated. Require
  `secondary_mch_list_hits >= 1` despite any optional MCH pre-seed,
  `secondary_restore_list_hits == 0`, and
  `old_hub_restore_get_hits == 0`. In a separate test that intentionally
  asserts the secondary Restore-list route, omit
  `secondary_restore_preseed` and require
  `secondary_restore_list_hits >= 1` while still requiring
  `old_hub_restore_get_hits == 0`.
- **Execute mode without fixture publishes successful fake-live data:** call
  `_execute_normal_vars(tmp_path, hub)` without
  `acm_finalization_restores_info` or `_old_hub_existing_restore_info`.
  Require `OLD_HUB_COUNT=1`, `secondary_mch_list_hits >= 1`,
  `secondary_restore_list_hits >= 1`, and
  `old_hub_restore_get_hits == 1`.
- **Malformed fixture fails before authoritative publication:** malformed injected `_old_hub_existing_restore_info` returns non-zero with sanitized shape failure.
- **Skipped-shaped fixture fails:** injected `{'changed': false, 'skipped': true}` returns non-zero with sanitized shape failure.
- **Missing `resources` fails:** injected mapping without `resources` returns non-zero with sanitized shape failure.
- **Mapping-valued `resources` fails:** both `{resources: {}}` and `{resources: {unexpected: value}}` return non-zero with sanitized shape failure.
- **String/number/null `resources` fails:** `{resources: "text"}`, `{resources: 1}`, and `{resources: null}` each return non-zero with sanitized shape failure.
- **Malformed list entries fail:** list entries that are not mappings return non-zero with sanitized shape failure.
- **Live query failure fatal:** fake returns 500 → assert `returncode != 0`.
- **Builder use is mandatory:** every malformed/skipped/missing-resources
  dry-run normal test passes its payload as
  `old_hub_fixture` to `_dry_run_normal_vars`; every execute normal success or
  failure test uses `_execute_normal_vars`. No normal-mode test calls
  `_base_old_hub_vars` directly or manually assembles variables, so the
  BackupSchedule pre-seed cannot be omitted accidentally.
- **Normal-mode observability:** every normal-mode test asserts
  `NORMAL_MODE_ENTERED=True` and `DIRECT_MODE_ENTERED=False`; the execute
  no-fixture case additionally proves the role ran by asserting its expected
  fake API counters are non-zero. Every normal-mode variable mapping contains
  `acm_finalization_backup_schedules_info: {resources: []}` and every
  normal-mode result reports
  `NORMAL_BACKUP_SCHEDULE_PRESEEDED=True`.
- **Canonical execute fixtures:** import `restore_fixture` and
  `multiclusterhub_fixture` from `finalization_fake_api`. Build the valid
  injected old-hub fixture through `_canonical_old_hub_fixture()`. Build the
  no-preseed live response with `restore_fixture`, and run valid execute
  scenarios with
  `FakeAcmBackupHub(restores=[...],
  multiclusterhubs=[multiclusterhub_fixture(resource_version="60")])`.
  Do not hand-write partial valid Restore or MCH mappings in the execute-mode
  builders. Malformed fixture-injection cases remain deliberately raw because
  they test the validator before publication and never become fake API
  resources.
- **Direct candidate harness (B6-C4 — concrete implementation):**

  Implement the direct candidate test path inside the existing
  `finalization_old_hub_restore_discovery.yml` harness using the test variable
  `acm_test_direct_old_hub_candidate`. When this variable is defined, the
  harness must bypass `include_role tasks_from: discover_resources` entirely
  and instead follow the block below. The direct-mode variable builder must not
  pre-seed `_old_hub_existing_restore_info`; the before-direct marker must
  therefore be `False`.

  ```yaml
  # In finalization_old_hub_restore_discovery.yml, after the normal-mode block:
  - name: Direct candidate test mode (acm_test_direct_old_hub_candidate)
    when: acm_test_direct_old_hub_candidate is defined
    block:
      - name: Mark direct candidate mode and capture pre-validation publication state
        ansible.builtin.set_fact:
          direct_mode_entered: true
          old_hub_authoritative_defined_before_direct: "{{ _old_hub_existing_restore_info is defined }}"
      - name: Set candidate directly from test variable (simulated module result)
        ansible.builtin.set_fact:
          _old_hub_existing_restore_candidate: "{{ acm_test_direct_old_hub_candidate }}"
      - name: Validate directly-supplied candidate (fail-closed, staged)
        ansible.builtin.include_role:
          name: tomazb.acm_switchover.finalization
          tasks_from: assert_restore_source_shape
        vars:
          _acm_restore_source: "{{ _old_hub_existing_restore_candidate }}"
          _acm_restore_source_label: "direct test candidate"
      - name: Publish authoritative after validation
        ansible.builtin.set_fact:
          _old_hub_existing_restore_info: "{{ _old_hub_existing_restore_candidate }}"
    rescue:
      - name: Capture direct-candidate validation failure
        ansible.builtin.set_fact:
          old_hub_discovery_failed: true
      - name: Re-raise after failure marker
        ansible.builtin.fail:
          msg: "{{ ansible_failed_result.msg | default(ansible_failed_result | string) }}"
    always:
      - name: Emit authoritative-defined-after-failure marker
        ansible.builtin.debug:
          msg: >-
            DIRECT_MODE_ENTERED={{ direct_mode_entered }}
            NORMAL_MODE_ENTERED={{ normal_mode_entered }}
            OLD_HUB_AUTHORITATIVE_DEFINED_BEFORE_DIRECT={{ old_hub_authoritative_defined_before_direct }}
            OLD_HUB_AUTHORITATIVE_DEFINED_AFTER_FAILURE={{ (_old_hub_existing_restore_info is defined)
            if (old_hub_discovery_failed | default(false)) else 'N/A' }}
  ```

  > **Scope note (B6-C4, hardened by B7-C1/C2/C5):** this harness duplicates only the
  > candidate/validator/publication contract for negative-shape evidence. It
  > avoids all fake Kubernetes API calls (no live queries, no MCH discovery,
  > no secondary Restore list). The parsed-YAML structural test in
  > `test_discover_resources_contracts.py` (`test_old_hub_restore_validate_before_publish_ordering`)
  > binds the same candidate → validate → publish ordering to the production
  > task file. These two test surfaces are complementary, not redundant.

  Harness execution flow when `acm_test_direct_old_hub_candidate` is defined:

  1. Skip the normal discovery block because its explicit `when` is false.
  2. Set `DIRECT_MODE_ENTERED=True` and capture that
     `_old_hub_existing_restore_info` is undefined before validation.
  3. Set `_old_hub_existing_restore_candidate` from the supplied simulated module result.
  4. Run `assert_restore_source_shape` through a role-relative
     `ansible.builtin.include_role`.
  5. Publish `_old_hub_existing_restore_info` only after successful validation.
  6. Capture any validation failure in `rescue` and set `old_hub_discovery_failed: true`.
  7. Re-raise after the failure marker is captured.
  8. Emit the direct/normal mode, before-publication, and
     after-failure publication markers in `always`.

  Test assertions for this mode:

  ```python
  @pytest.mark.parametrize("malformed_candidate", [
      {},
      {"resources": "text"},
      {"resources": 1},
      {"resources": None},
      {"resources": {}},
      {"changed": False, "skipped": True},
      {"resources": ["not-a-mapping"]},
  ])
  def test_direct_candidate_malformed_fails_with_sanitized_message(tmp_path, malformed_candidate):
      with FakeAcmBackupHub(restores=[]) as hub:
          v = _direct_candidate_vars(tmp_path, hub, malformed_candidate)
          assert "acm_finalization_backup_schedules_info" not in v
          assert "_old_hub_existing_restore_info" not in v
          r = _run(tmp_path, v)
          assert hub.request_count == 0
      assert r.returncode != 0, r.stdout
      combined = r.stdout + r.stderr
      assert ("refusing to treat it as an empty successful result" in combined
              or "refusing to classify malformed data" in combined), combined
      assert "DIRECT_MODE_ENTERED=True" in r.stdout, r.stdout
      assert "NORMAL_MODE_ENTERED=False" in r.stdout, r.stdout
      assert "OLD_HUB_AUTHORITATIVE_DEFINED_BEFORE_DIRECT=False" in r.stdout, r.stdout
      assert "OLD_HUB_AUTHORITATIVE_DEFINED_AFTER_FAILURE=False" in r.stdout, r.stdout
  ```

  The aggregate `request_count == 0` assertion is mandatory. Per-route counters
  may also be asserted as zero, but they do not replace the aggregate proof.
  Together with the four stable markers, this prevents a false pass where
  normal discovery runs first and the direct validator fails later.

**Step 7: Add a static direct-harness contract (B7-C2)**

Parse `finalization_old_hub_restore_discovery.yml` in
`test_finalization_old_hub_restore_runtime.py` or
`test_discover_resources_contracts.py` and assert:

```python
def test_direct_harness_is_role_relative_and_validates_before_publication():
    text = OLD_HUB_HARNESS.read_text(encoding="utf-8")
    assert "COLLECTION_ROOT" not in text
    tasks = yaml.safe_load(text)
    flat = [task for task in _flatten_tasks(tasks[0]["tasks"]) if isinstance(task, dict)]

    direct = next(task for task in tasks[0]["tasks"] if task.get("name", "").startswith("Direct candidate test mode"))
    assert "acm_test_direct_old_hub_candidate is defined" in str(direct.get("when", ""))
    direct_flat = [task for task in _flatten_tasks(direct["block"]) if isinstance(task, dict)]
    validator_idx = next(
        index for index, task in enumerate(direct_flat)
        if (task.get("ansible.builtin.include_role") or {}).get("tasks_from")
        == "assert_restore_source_shape"
    )
    validator = direct_flat[validator_idx]["ansible.builtin.include_role"]
    assert validator["name"] == "tomazb.acm_switchover.finalization"
    publish_idx = next(
        index for index, task in enumerate(direct_flat)
        if "Publish authoritative after validation" in str(task.get("name", ""))
    )
    assert validator_idx < publish_idx
```

Also assert the normal block has
`when: acm_test_direct_old_hub_candidate is not defined`, contains the sole
`tasks_from: discover_resources` role include, and emits
`NORMAL_MODE_ENTERED=True` when exercised. This static contract and the runtime
zero-request/marker contract are both required.

**Preferred malformed-output coverage boundary (B6 split):**
- Live fake-HTTP cases are limited to valid success paths and explicit API failures (e.g., HTTP 500).
- Malformed source-shape coverage is proven by the shared shape-validator harness, malformed fixture-injection tests, and the direct candidate-validation/publication harness with simulated module-result mappings.
- Do **not** treat Kubernetes client/module rejection of malformed HTTP payloads as evidence for the R3-A3 validator boundary.
- A test that fails during discovery or MCH refresh does **not** prove R3-A3 candidate/validator behavior.

**Step 8: Static production contract**

```python
import pytest
import yaml

from yaml_contract_helpers import _flatten_tasks


def test_finalization_old_hub_restore_has_no_name_collision():
    text = FINALIZATION_DISCOVER.read_text()
    assert "register: _old_hub_existing_restore_live_info" in text
    assert "register: _old_hub_existing_restore_info\n" not in text

def _include_tasks_target(task):
    """Return an include_tasks target across FQCN/short and scalar/mapping forms."""
    for module_key in ("ansible.builtin.include_tasks", "include_tasks"):
        if module_key not in task:
            continue
        module_args = task[module_key]
        if isinstance(module_args, str):
            return module_args
        if isinstance(module_args, dict):
            file_arg = module_args.get("file")
            return file_arg if isinstance(file_arg, str) else ""
        return ""
    return ""


def _assert_old_hub_restore_validate_before_publish(tasks):
    flat = [t for t in _flatten_tasks(tasks) if isinstance(t, dict)]

    candidate_indices = [
        i for i, t in enumerate(flat)
        if "Select old-hub passive restore candidate" in str(t.get("name", ""))
    ]
    validate_indices = [
        i for i, t in enumerate(flat)
        if _include_tasks_target(t).endswith("assert_restore_source_shape.yml")
        and "old-hub passive restore" in str(t.get("vars", {}).get("_acm_restore_source_label", ""))
    ]
    publish_indices = [
        i for i, t in enumerate(flat)
        if "Publish authoritative old-hub passive restore source" in str(t.get("name", ""))
    ]

    assert candidate_indices, "expected candidate-selection tasks"
    assert len(validate_indices) == 1, "expected exactly one validation include"
    assert len(publish_indices) == 1, "expected exactly one authoritative publication task"
    validate_idx = validate_indices[0]
    publish_idx = publish_indices[0]
    assert max(candidate_indices) < validate_idx < publish_idx

    for idx in candidate_indices:
        sf = flat[idx].get("ansible.builtin.set_fact", {}) or {}
        assert "_old_hub_existing_restore_candidate" in sf
        assert "_old_hub_existing_restore_info" not in sf

    live_query_indices = [
        i for i, t in enumerate(flat)
        if "Read existing passive sync restore on old hub" in str(t.get("name", ""))
    ]
    assert live_query_indices, "expected live query task"
    for idx in live_query_indices:
        assert str(flat[idx].get("register", "")) == "_old_hub_existing_restore_live_info"
        assert str(flat[idx].get("register", "")) != "_old_hub_existing_restore_info"

    publish_task = flat[publish_idx]
    sf = publish_task.get("ansible.builtin.set_fact", {}) or {}
    published_value = str(sf.get("_old_hub_existing_restore_info", ""))
    assert "_old_hub_existing_restore_candidate" in published_value
    assert "_old_hub_existing_restore_live_info" not in published_value


def test_old_hub_restore_validate_before_publish_ordering():
    tasks = yaml.safe_load(FINALIZATION_DISCOVER.read_text())
    _assert_old_hub_restore_validate_before_publish(tasks)


@pytest.mark.parametrize(
    ("module_key", "module_args"),
    [
        ("ansible.builtin.include_tasks", "assert_restore_source_shape.yml"),
        ("include_tasks", "assert_restore_source_shape.yml"),
        (
            "ansible.builtin.include_tasks",
            {"file": "assert_restore_source_shape.yml"},
        ),
        ("include_tasks", {"file": "assert_restore_source_shape.yml"}),
    ],
)
def test_old_hub_ordering_helper_supports_include_forms_and_rejects_late_validation(
    module_key,
    module_args,
):
    """Synthetic regression for both module keys, both arg forms, and ordering."""
    candidate = {
        "name": "Select old-hub passive restore candidate (synthetic)",
        "ansible.builtin.set_fact": {
            "_old_hub_existing_restore_candidate": {"resources": []},
        },
    }
    live_query = {
        "name": "Read existing passive sync restore on old hub (synthetic)",
        "register": "_old_hub_existing_restore_live_info",
    }
    validator = {
        "name": "Validate old-hub passive restore candidate (synthetic)",
        module_key: module_args,
        "vars": {"_acm_restore_source_label": "old-hub passive restore"},
    }
    publication = {
        "name": "Publish authoritative old-hub passive restore source (synthetic)",
        "ansible.builtin.set_fact": {
            "_old_hub_existing_restore_info": (
                "{{ _old_hub_existing_restore_candidate }}"
            ),
        },
    }

    _assert_old_hub_restore_validate_before_publish(
        [candidate, live_query, validator, publication]
    )
    with pytest.raises(AssertionError):
        _assert_old_hub_restore_validate_before_publish(
            [candidate, live_query, publication, validator]
        )
```

Keep `_include_tasks_target` and
`_assert_old_hub_restore_validate_before_publish` at module scope beside the
production test, with the file's existing `pytest`, `yaml`, and
`_flatten_tasks` imports. The production assertion must see exactly one
old-hub validation include. The synthetic parameter matrix proves both module
keys and both supported module-argument shapes reach the same helper and that
moving the validator after authoritative publication still fails.

**Step 9: Run + commit**

Run: `python -m pytest .../test_finalization_old_hub_restore_runtime.py ansible_collections/tomazb/acm_switchover/tests/unit/test_discover_resources_contracts.py -q`
Expected: PASS

```bash
git add ansible_collections/tomazb/acm_switchover/roles/finalization/tasks/discover_resources.yml \
        ansible_collections/tomazb/acm_switchover/tests/integration/playbooks/finalization_old_hub_restore_discovery.yml \
        ansible_collections/tomazb/acm_switchover/tests/integration/test_finalization_old_hub_restore_runtime.py \
        ansible_collections/tomazb/acm_switchover/tests/unit/test_discover_resources_contracts.py
git commit -m "fix(collection): R3-A3 preserve old-hub restore fixture/seed with distinct live name and staged validation"
```

---

## Task 7: Literal-only collision scanner + red-on-regression + dynamic-name/parse-error tests

**Files:**
- Create: `ansible_collections/tomazb/acm_switchover/tests/unit/register_collision_scan.py`
- Create: `ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/collisions/` (fixtures)
- Test: `ansible_collections/tomazb/acm_switchover/tests/unit/test_register_setfact_collision_guardrail.py`

**Step 1: Scanner — literal scalars only, path-bearing parse errors (item 11)**

```python
"""Static scanner for literal-scalar register/set_fact name collisions."""
from __future__ import annotations
import pathlib, yaml
from yaml_contract_helpers import _flatten_tasks

SET_FACT_KEYS = ("ansible.builtin.set_fact", "set_fact")
PLAY_TASK_SECTIONS = ("pre_tasks", "tasks", "post_tasks", "handlers")

class CollisionScanError(Exception):
    pass

def _is_literal_scalar(value) -> bool:
    # literal only: a plain string with no Jinja/computed markers
    return isinstance(value, str) and "{{" not in value and "{%" not in value

def _load(path: pathlib.Path):
    try:
        return yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise CollisionScanError(f"{path}: YAML parse error: {exc}") from exc

def _task_lists(data, *, is_playbook):
    if is_playbook:
        if isinstance(data, list):
            for play in data:
                if isinstance(play, dict):
                    for section in PLAY_TASK_SECTIONS:
                        if play.get(section):
                            yield play[section]
    else:
        if isinstance(data, list):
            yield data

def _collisions(task_lists):
    registers, set_facts = set(), set()
    for tasks in task_lists:
        for task in _flatten_tasks(tasks):
            if not isinstance(task, dict):
                continue
            if _is_literal_scalar(task.get("register")):
                registers.add(task["register"])
            for key in SET_FACT_KEYS:
                mapping = task.get(key)
                if isinstance(mapping, dict):
                    set_facts.update(k for k in mapping if _is_literal_scalar(k))
    return sorted(registers & set_facts)

def scan_file(path, *, is_playbook):
    return _collisions(_task_lists(_load(path), is_playbook=is_playbook))

def scan_boundary(collection_root):
    found = set()
    roles = collection_root / "roles"
    role_task_handler_files = set()
    for pat in ("**/tasks/**/*.yml", "**/tasks/**/*.yaml", "**/handlers/**/*.yml", "**/handlers/**/*.yaml"):
        role_task_handler_files.update(roles.glob(pat))
    for p in sorted(role_task_handler_files):
        for var in scan_file(p, is_playbook=False):
            found.add((str(p.relative_to(collection_root)), var))

    playbook_files = set()
    for pat in ("**/*.yml", "**/*.yaml"):
        playbook_files.update((collection_root / "playbooks").glob(pat))
    for p in sorted(playbook_files):
        for var in scan_file(p, is_playbook=True):
            found.add((str(p.relative_to(collection_root)), var))
    return found
```

**Step 2: Fixtures**

- `tasks_collision.yml`, `handlers_collision.yml`, `playbook_collision.yml` (collision in `post_tasks`), `clean.yml` (distinct names).
- `playbook_pre_tasks_block_collision.yml`, `playbook_tasks_rescue_collision.yml`, `playbook_post_tasks_always_collision.yml`, `playbook_handlers_block_collision.yml` to prove recursive flattening across `block`/`rescue`/`always` in each play-level section.
- `dynamic_register.yml` — `register: "{{ dynamic_name }}"` with a `set_fact` literal of a matching-looking name → must **not** be flagged (literal-only).
- `dynamic_setfact.yml` — a `set_fact` whose key is templated → not flagged.
- `broken.yml` — invalid YAML → parse error.

**Step 3: Tests**

```python
import pathlib, pytest
from register_collision_scan import scan_file, scan_boundary, CollisionScanError
FIX = pathlib.Path(__file__).resolve().parent / "fixtures" / "collisions"

def test_detects_task_collision():      assert scan_file(FIX/"tasks_collision.yml", is_playbook=False) == ["x"]
def test_detects_handler_collision():   assert scan_file(FIX/"handlers_collision.yml", is_playbook=False) == ["x"]
def test_detects_playbook_post_tasks(): assert scan_file(FIX/"playbook_collision.yml", is_playbook=True) == ["x"]
def test_detects_pre_tasks_block_collision(): assert scan_file(FIX/"playbook_pre_tasks_block_collision.yml", is_playbook=True) == ["x"]
def test_detects_tasks_rescue_collision():    assert scan_file(FIX/"playbook_tasks_rescue_collision.yml", is_playbook=True) == ["x"]
def test_detects_post_tasks_always_collision(): assert scan_file(FIX/"playbook_post_tasks_always_collision.yml", is_playbook=True) == ["x"]
def test_detects_handlers_block_collision():  assert scan_file(FIX/"playbook_handlers_block_collision.yml", is_playbook=True) == ["x"]
def test_scan_boundary_includes_nested_role_tasks_and_handlers(): ...
def test_scan_boundary_includes_nested_playbooks(): ...
def test_scan_boundary_has_no_duplicate_file_scans(): ...
def test_scan_boundary_paths_are_collection_relative(): ...
def test_clean_file_no_collision():     assert scan_file(FIX/"clean.yml", is_playbook=False) == []
def test_dynamic_register_not_flagged():assert scan_file(FIX/"dynamic_register.yml", is_playbook=False) == []
def test_dynamic_setfact_not_flagged(): assert scan_file(FIX/"dynamic_setfact.yml", is_playbook=False) == []
def test_parse_error_is_path_bearing():
    with pytest.raises(CollisionScanError) as e:
        scan_file(FIX/"broken.yml", is_playbook=False)
    assert "broken.yml" in str(e.value)
```

Required assertions for the boundary tests above:
- nested role task directories are scanned;
- nested role handler directories are scanned;
- playbooks recursively below `playbooks/` are scanned;
- no duplicate file scan occurs;
- returned paths are normalized relative to the collection root.

To preserve the B7-C4 exactly-one-commit contract for this new test file, also
write the allowlist loader and enforcement/malformed-fixture tests into
`test_register_setfact_collision_guardrail.py` in this task. The reusable
`load_and_validate_allowlist(path)` must raise `AllowlistError` unless:

- every entry has a `category` in `{"intentional", "debt"}`;
- intentional entries contain non-empty string values for `path`, `variable`,
  `structural_rationale`, `category`, and `approval_reference`;
- debt entries additionally contain non-empty string values for
  `issue_reference` and `removal_condition`;
- `(path, variable)` pairs are unique across the whole file; and
- every debt `issue_reference` contains the exact URL
  `https://github.com/tomazb/rh-acm-switchover/issues/202`.

Add these enforcement tests in the same new file:

```python
def test_no_unallowlisted_collisions():
    allow = allowlist_pairs()
    extra = scan_boundary(COLLECTION_ROOT) - allow
    assert not extra, f"Unallowlisted collisions: {sorted(extra)}"

def test_no_stale_allowlist_entries():
    stale = allowlist_pairs() - scan_boundary(COLLECTION_ROOT)
    assert not stale, f"Stale allowlist entries: {sorted(stale)}"

def test_allowlist_metadata_valid():
    load_and_validate_allowlist(ALLOWLIST)

@pytest.mark.parametrize("name", [
    "missing_removal_condition", "empty_rationale", "unknown_category",
    "duplicate_pair", "cross_category_duplicate", "wrong_issue_ref"])
def test_malformed_allowlist_rejected(name):
    with pytest.raises(AllowlistError):
        load_and_validate_allowlist(ALLOWLIST_FIX / f"{name}.yml")
```

These tests refer to Task 8 data paths but are not executed until Task 8
creates the allowlist and malformed-fixture data. Task 8 must not modify or
re-stage this test file.

**Step 4: Run only the scanner tests + commit**

Run the scanner test node IDs explicitly:

```bash
python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_register_setfact_collision_guardrail.py::test_detects_task_collision \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_register_setfact_collision_guardrail.py::test_detects_handler_collision \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_register_setfact_collision_guardrail.py::test_detects_playbook_post_tasks \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_register_setfact_collision_guardrail.py::test_detects_pre_tasks_block_collision \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_register_setfact_collision_guardrail.py::test_detects_tasks_rescue_collision \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_register_setfact_collision_guardrail.py::test_detects_post_tasks_always_collision \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_register_setfact_collision_guardrail.py::test_detects_handlers_block_collision \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_register_setfact_collision_guardrail.py::test_scan_boundary_includes_nested_role_tasks_and_handlers \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_register_setfact_collision_guardrail.py::test_scan_boundary_includes_nested_playbooks \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_register_setfact_collision_guardrail.py::test_scan_boundary_has_no_duplicate_file_scans \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_register_setfact_collision_guardrail.py::test_scan_boundary_paths_are_collection_relative \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_register_setfact_collision_guardrail.py::test_clean_file_no_collision \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_register_setfact_collision_guardrail.py::test_dynamic_register_not_flagged \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_register_setfact_collision_guardrail.py::test_dynamic_setfact_not_flagged \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_register_setfact_collision_guardrail.py::test_parse_error_is_path_bearing \
  -q
```

Do not use `-k` substring selection and do not run the Task 8 tests before
their data files exist.

Expected: PASS

```bash
git add ansible_collections/tomazb/acm_switchover/tests/unit/register_collision_scan.py \
        ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/collisions/ \
        ansible_collections/tomazb/acm_switchover/tests/unit/test_register_setfact_collision_guardrail.py
git commit -m "test(collection): literal-only register/set_fact collision scanner with dynamic-name and parse-error coverage"
```

---

## Task 8: Allowlist enforcement + strict metadata validation (automated malformed fixtures)

**Files:**
- Create: `ansible_collections/tomazb/acm_switchover/tests/unit/register_setfact_collision_allowlist.yml`
- Create: `ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/allowlists/` (malformed fixtures)

**Step 1: Allowlist loader/validator (item 12)**

Use the `load_and_validate_allowlist(path)` and enforcement tests already
created and committed in Task 7. Do not modify or stage
`test_register_setfact_collision_guardrail.py` in this task. The loader raises
`AllowlistError` unless:
- every entry has a `category` in `{"intentional", "debt"}` (reject unknown categories);
- intentional entries have non-empty `path`, `variable`, `structural_rationale`, `category`, `approval_reference`;
- debt entries additionally have non-empty `issue_reference`, `removal_condition`;
- all required values are non-empty strings;
- `(path, variable)` pairs are unique across the whole file (no duplicates, no cross-category duplicates);
- every debt entry's `issue_reference` contains the exact `#202` reference (`https://github.com/tomazb/rh-acm-switchover/issues/202`).

**Step 2: Enforcement tests**

```python
def test_no_unallowlisted_collisions():
    allow = allowlist_pairs()
    extra = scan_boundary(COLLECTION_ROOT) - allow
    assert not extra, f"Unallowlisted collisions: {sorted(extra)}"

def test_no_stale_allowlist_entries():
    stale = allowlist_pairs() - scan_boundary(COLLECTION_ROOT)
    assert not stale, f"Stale allowlist entries: {sorted(stale)}"

def test_allowlist_metadata_valid():
    load_and_validate_allowlist(ALLOWLIST)  # raises on any violation
```

**Step 3: Automated malformed-allowlist fixtures (replaces manual edit/revert — item 12)**

Create fixtures under `fixtures/allowlists/`: `missing_removal_condition.yml`, `empty_rationale.yml`, `unknown_category.yml`, `duplicate_pair.yml`, `cross_category_duplicate.yml`, `wrong_issue_ref.yml`. Parametrized test:

```python
@pytest.mark.parametrize("name", [
    "missing_removal_condition", "empty_rationale", "unknown_category",
    "duplicate_pair", "cross_category_duplicate", "wrong_issue_ref"])
def test_malformed_allowlist_rejected(name):
    with pytest.raises(AllowlistError):
        load_and_validate_allowlist(ALLOWLIST_FIX / f"{name}.yml")
```

**Step 4: Write the real allowlist (2 intentional + 10 debt/#202)**

Intentional: `disable_old_hub_observability.yml::_acm_old_hub_mco_info` (block happy-path register + rescue set_fact fallback for absent CRD), `verify_managed_clusters.yml::acm_switchover_cluster_verify_result` (unconditional register + conditional in-place `combine`). Debt: the 10 `roles/preflight/tasks/discover_resources.yml::acm_primary_*` variables, each `category: debt`, `issue_reference: https://github.com/tomazb/rh-acm-switchover/issues/202`, `removal_condition: preflight primary facts seeded deterministically without a skippable same-name register (issue #202 resolved)`.

> **Builder gate:** re-confirm the two intentional `structural_rationale`s against current source before writing them as intentional. If either has a reachable defeat path, reclassify as `debt` with its own tracked issue.

**Step 5: Run + commit**

Run: `python -m pytest .../test_register_setfact_collision_guardrail.py -q`
Expected: PASS (0 unallowlisted; 0 stale; metadata valid; malformed fixtures rejected).

```bash
git add ansible_collections/tomazb/acm_switchover/tests/unit/register_setfact_collision_allowlist.yml \
        ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/allowlists/
git commit -m "test(collection): enforce collision guardrail with strict two-category allowlist validation (#202 debt)"
```

---

## Task 9: ansible-core 2.15.x foundation verification gate

**Files:**
- Create: `ansible_collections/tomazb/acm_switchover/tests/integration/playbooks/register_skip_semantics.yml`
- Test: `ansible_collections/tomazb/acm_switchover/tests/integration/test_register_skip_semantics.py`

**Step 1: Committed reproduction playbook**

Asserts: a skipped `register` defines the var as a skipped result; a later `set_fact ... when: <var> is not defined` does not fire; `<var>.resources | default([]) | length == 0`. Emits `SEED_FIRED=... HAS_RESOURCES_KEY=...`.

**Step 2: Test that runs it via the env-selected controller (item 13)**

Reuse the `_run` helper (reads `ACM_ANSIBLE_PLAYBOOK_BIN` / `ACM_ANSIBLE_PYTHON`). The test asserts `SEED_FIRED=False` and `HAS_RESOURCES_KEY=False`.

**Step 3: Run on the default CI controller**

Run: `python -m pytest .../test_register_skip_semantics.py -q` → PASS.

**Step 4: Mandatory 2.15.x run in the foundation environment (item 13)**

Run inside the **foundation CI container image** used by the `ansible-collection-foundation` workflow, **or** a local **Python 3.11** venv (ansible-core 2.15 controller supports Python 3.9–3.11):

```bash
python3.11 -m venv /tmp/ac215
/tmp/ac215/bin/pip install 'ansible-core>=2.15,<2.16' 'kubernetes>=28.0.0'
/tmp/ac215/bin/ansible-playbook --version   # RECORD THIS OUTPUT in the evidence log
export ACM_ANSIBLE_PLAYBOOK_BIN=/tmp/ac215/bin/ansible-playbook
export ACM_ANSIBLE_PYTHON=/tmp/ac215/bin/python
python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/integration/test_register_skip_semantics.py \
  ansible_collections/tomazb/acm_switchover/tests/integration/test_finalization_cleanup_restores_runtime.py \
  ansible_collections/tomazb/acm_switchover/tests/integration/test_finalization_old_hub_restore_runtime.py -q
```

**Controller-selection contract:** all R3-A2/R3-A3 runtime tests select the controller executable via `ACM_ANSIBLE_PLAYBOOK_BIN` (default `ansible-playbook`) and the module-side interpreter via `ACM_ANSIBLE_PYTHON`. The 2.15.x gate sets both to the 2.15.x venv, guaranteeing the fix and validation behave identically on the collection floor. Record `ansible-playbook --version` output in the PR evidence.

**Step 5: Commit**

```bash
git add ansible_collections/tomazb/acm_switchover/tests/integration/playbooks/register_skip_semantics.yml \
        ansible_collections/tomazb/acm_switchover/tests/integration/test_register_skip_semantics.py
git commit -m "test(collection): pin skipped-register semantics with a 2.15.x foundation verification gate"
```

---

## Task 10: AGENTS.md convention + CHANGELOG

**Files:**
- Modify: `AGENTS.md`
- Modify: `CHANGELOG.md` (`## [Unreleased]`)

**Step 1: AGENTS.md — add the collision rule and allowlist policy (item 15)**

Add a subsection (near the Ansible collection patterns / testing guidance) stating:

- In collection role `tasks`/`handlers` and playbooks, a literal-scalar `register` name must never collide with a literal-scalar `set_fact` key in the same file (skipped registers clobber same-named facts with `{skipped: true}`, silently defeating `| default([])`). Use distinct live-query and authoritative-fact names, then candidate → staged-validate → publish.
- Any remaining literal collision must be recorded in `tests/unit/register_setfact_collision_allowlist.yml` as either an **intentional** entry (path, variable, structural rationale, category, approval reference) or a **debt** entry (all intentional fields plus issue reference and removal condition). The guardrail fails on unallowlisted collisions, stale entries, or malformed metadata.

**Step 2: CHANGELOG — `[Unreleased]`**

```markdown
### Fixed
- Ansible collection: finalization dry-run now reports the true set of Restore
  resources execute mode would delete (previously always `restore_count: 0`)
  and preserves injected/deliberately-seeded old-hub restore data, using
  distinct live-query/authoritative names with fail-closed staged shape
  validation (R3-A2, R3-A3).

### Added
- Ansible collection: a repository guardrail that fails when a literal-scalar
  `register` target collides with a `set_fact` name across role
  `tasks`/`handlers` and playbooks, with a strict two-category
  (intentional/debt) allowlist. The preflight restore-only seed collision is
  tracked as debt in issue #202.
```

**Step 3: Commit**

```bash
git add AGENTS.md CHANGELOG.md
git commit -m "docs: record register/set_fact collision convention and R3-01b changelog"
```

---

## Task 11: Full strict verification gates (item 14)

Run all of the following; all must pass before the PR gate. Record outputs as evidence.

**Step 1: Relevant Python CLI finalization tests (read-only parity reference; not modified)**

```bash
python -m pytest tests/ -k "finaliz" -q
```

**Step 2: Collection unit + integration**

```bash
python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit ansible_collections/tomazb/acm_switchover/tests/integration -q
```

**Step 3: Combined collection/root lane**

```bash
python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit tests/ -q
```

**Step 4: `./run_tests.sh` (strict quality on by default)**

```bash
./run_tests.sh
```

**Step 5: Individual CI-equivalent gates (exact CI scope)**

```bash
black --check --line-length 120 acm_switchover.py lib modules ansible_collections/tomazb/acm_switchover/plugins ansible_collections/tomazb/acm_switchover/tests tests
isort --check-only --profile black --line-length 120 acm_switchover.py lib modules ansible_collections/tomazb/acm_switchover/plugins ansible_collections/tomazb/acm_switchover/tests tests
flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics
mypy --explicit-package-bases acm_switchover.py lib/ modules/ ansible_collections/tomazb/acm_switchover/plugins ansible_collections/tomazb/acm_switchover/tests --ignore-missing-imports --no-strict-optional
bandit --ini .bandit -f txt
```

**Step 6: Whitespace / diff hygiene**

```bash
git diff --check 0bf55db9eed76ae7d60844b806975c04cd0111e4..HEAD
git diff --check
```

**Step 7: Changed-file / protected-file / scope check**

```bash
python - <<'PY'
import subprocess, sys

def run(cmd):
    return subprocess.check_output(cmd, text=True).splitlines()

base = "0bf55db9eed76ae7d60844b806975c04cd0111e4"
required_paths = {
    "docs/plans/2026-07-28-r3-01b-finalization-register-clobbers-design.md",
    "docs/plans/2026-07-28-r3-01b-finalization-register-clobbers-implementation-plan-b9.md",
    "thermos-resolution-plan.md",
    "AGENTS.md",
    "CHANGELOG.md",
    "ansible_collections/tomazb/acm_switchover/roles/finalization/tasks/assert_restore_source_shape.yml",
    "ansible_collections/tomazb/acm_switchover/roles/finalization/tasks/cleanup_restores.yml",
    "ansible_collections/tomazb/acm_switchover/roles/finalization/tasks/discover_resources.yml",
    "ansible_collections/tomazb/acm_switchover/tests/integration/finalization_fake_api.py",
    "ansible_collections/tomazb/acm_switchover/tests/integration/test_finalization_fake_api.py",
    "ansible_collections/tomazb/acm_switchover/tests/integration/playbooks/finalization_assert_restore_source_shape.yml",
    "ansible_collections/tomazb/acm_switchover/tests/integration/test_restore_source_shape_contract.py",
    "ansible_collections/tomazb/acm_switchover/tests/integration/playbooks/finalization_cleanup_restores.yml",
    "ansible_collections/tomazb/acm_switchover/tests/integration/test_finalization_cleanup_restores_runtime.py",
    "ansible_collections/tomazb/acm_switchover/tests/unit/test_finalization_verification.py",
    "ansible_collections/tomazb/acm_switchover/tests/integration/playbooks/finalization_old_hub_restore_discovery.yml",
    "ansible_collections/tomazb/acm_switchover/tests/integration/test_finalization_old_hub_restore_runtime.py",
    "ansible_collections/tomazb/acm_switchover/tests/unit/test_discover_resources_contracts.py",
    "ansible_collections/tomazb/acm_switchover/tests/unit/register_collision_scan.py",
    "ansible_collections/tomazb/acm_switchover/tests/unit/test_register_setfact_collision_guardrail.py",
    "ansible_collections/tomazb/acm_switchover/tests/unit/register_setfact_collision_allowlist.yml",
    "ansible_collections/tomazb/acm_switchover/tests/integration/playbooks/register_skip_semantics.yml",
    "ansible_collections/tomazb/acm_switchover/tests/integration/test_register_skip_semantics.py",
}
optional_prefixes = {
    "ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/collisions/",
    "ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/allowlists/",
}
actual = set(run(["git", "diff", "--name-only", f"{base}..HEAD"]))
actual.update(run(["git", "diff", "--name-only", "--cached"]))
actual.update(run(["git", "diff", "--name-only"]))
actual.update(run(["git", "ls-files", "--others", "--exclude-standard"]))
actual = {p for p in actual if p.strip()}

violations = []
for p in sorted(actual):
    if p in required_paths:
        pass
    elif any(p.startswith(prefix) for prefix in optional_prefixes):
        pass
    else:
        violations.append(f"unexpected-path: {p}")
    if p == "docs/ACM_SWITCHOVER_RUNBOOK.md" or p.startswith(".claude/skills/") and p.endswith(".skill.md"):
        violations.append(f"protected-file-modified: {p}")
    if p.startswith("lib/") or p.startswith("modules/") or p == "acm_switchover.py" or p.startswith("tests/"):
        violations.append(f"forbidden-root-python-surface: {p}")

missing_required = sorted(required_paths - actual)
if missing_required:
    violations.extend(f"missing-required-path: {p}" for p in missing_required)

if violations:
    print("\n".join(violations))
    sys.exit(1)
print("scope-gate-pass-exact-allowlist")
PY
```
Assert:
- `unexpected = actual - expected` is empty (using exact allowlist plus the two allowed fixture prefixes).
- `missing_required = required - actual` is empty.
- no protected files are modified.
- no root Python production/test paths are modified.
- no preflight production changes from issue #202, no Argo CD production/test changes, no `TR2D-02` implementation/test changes, and no unrelated collection/plugins/RBAC/manifests/workflow/dependency changes can pass the gate because they are outside the exact allowlist.

**Step 8: Task-reference self-check (X1/X2 hardening)**

Programmatically audit all task cross-reference forms before execution/publication:

```bash
python - <<'PY'
import pathlib, re, sys
p = pathlib.Path("docs/plans/2026-07-28-r3-01b-finalization-register-clobbers-implementation-plan-b9.md")
text = p.read_text(encoding="utf-8")
lines = text.splitlines(keepends=True)
non_code = []
in_fence = False
for line in lines:
    if line.startswith("```"):
        in_fence = not in_fence
        continue
    if not in_fence:
        non_code.append(line)
scan_text = "".join(non_code)
semantic_expected = {
    "Tasks 5–6": "Current-state facts line describing which tasks fix the two finalization collisions",
    "Tasks 5 & 6": "Task 4 note naming the two runtime callers of the shared shape validator",
}
single_refs = []
valid_task_ids = {str(i) for i in range(0, 13)}
for m in re.finditer(r"Task (\d+)", scan_text):
    single_refs.append(m.group(1))
invalid_single = sorted({n for n in single_refs if n not in valid_task_ids})

matches = list(re.finditer(r"Tasks \d+–\d+|Tasks \d+ & \d+", scan_text))
detected_refs = sorted({m.group(0) for m in matches})
unexpected = sorted(set(detected_refs) - set(semantic_expected))
missing = sorted(set(semantic_expected) - set(detected_refs))
if unexpected or missing:
    print("unexpected-range-or-pair-refs:", unexpected)
    print("missing-range-or-pair-refs:", missing)
    sys.exit(1)
if invalid_single:
    print("invalid single-task refs:", invalid_single)
    sys.exit(1)

context_checks = {
    "Tasks 5–6": ["Current-state facts:"],
    "Tasks 5 & 6": ["**Note (item 9):**"],
}
for m in matches:
    ref = m.group(0)
    if ref not in context_checks:
        print(f"unexpected-range-or-pair-occurrence: {ref} at {m.start()}")
        sys.exit(1)
    win_start = max(0, m.start() - 500)
    win_end = min(len(scan_text), m.end() + 500)
    window = scan_text[win_start:win_end]
    if not any(anchor in window for anchor in context_checks[ref]):
        print(f"semantic-anchor-window-failed: {ref} at {m.start()}")
        sys.exit(1)

print("task-reference-gate-pass-semantic")
PY
```

Confirm each reference points to the intended task after final numbering is stable; fail the gate on stale or mismatched references.

**Step 9: Commit presence, uniqueness, and bidirectional scope audit (B7-C4)**

```bash
python - <<'PY'
"""Prove task ownership, commit presence/uniqueness, and scope consistency."""
import pathlib
import re
import shlex
import sys

plan = pathlib.Path("docs/plans/2026-07-28-r3-01b-finalization-register-clobbers-implementation-plan-b9.md")
text = plan.read_text(encoding="utf-8")

expected_created_paths = {
    "docs/plans/2026-07-28-r3-01b-finalization-register-clobbers-design.md",
    "docs/plans/2026-07-28-r3-01b-finalization-register-clobbers-implementation-plan-b9.md",
    "ansible_collections/tomazb/acm_switchover/tests/integration/finalization_fake_api.py",
    "ansible_collections/tomazb/acm_switchover/tests/integration/test_finalization_fake_api.py",
    "ansible_collections/tomazb/acm_switchover/roles/finalization/tasks/assert_restore_source_shape.yml",
    "ansible_collections/tomazb/acm_switchover/tests/integration/playbooks/finalization_assert_restore_source_shape.yml",
    "ansible_collections/tomazb/acm_switchover/tests/integration/test_restore_source_shape_contract.py",
    "ansible_collections/tomazb/acm_switchover/tests/integration/playbooks/finalization_cleanup_restores.yml",
    "ansible_collections/tomazb/acm_switchover/tests/integration/test_finalization_cleanup_restores_runtime.py",
    "ansible_collections/tomazb/acm_switchover/tests/integration/playbooks/finalization_old_hub_restore_discovery.yml",
    "ansible_collections/tomazb/acm_switchover/tests/integration/test_finalization_old_hub_restore_runtime.py",
    "ansible_collections/tomazb/acm_switchover/tests/unit/register_collision_scan.py",
    "ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/collisions/",
    "ansible_collections/tomazb/acm_switchover/tests/unit/test_register_setfact_collision_guardrail.py",
    "ansible_collections/tomazb/acm_switchover/tests/unit/register_setfact_collision_allowlist.yml",
    "ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/allowlists/",
    "ansible_collections/tomazb/acm_switchover/tests/integration/playbooks/register_skip_semantics.yml",
    "ansible_collections/tomazb/acm_switchover/tests/integration/test_register_skip_semantics.py",
}
permitted_modified_paths = {
    "thermos-resolution-plan.md",
    "AGENTS.md",
    "CHANGELOG.md",
    "ansible_collections/tomazb/acm_switchover/roles/finalization/tasks/cleanup_restores.yml",
    "ansible_collections/tomazb/acm_switchover/roles/finalization/tasks/discover_resources.yml",
    "ansible_collections/tomazb/acm_switchover/tests/unit/test_finalization_verification.py",
    "ansible_collections/tomazb/acm_switchover/tests/unit/test_discover_resources_contracts.py",
}
expected_fixture_boundaries = {
    "ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/collisions/",
    "ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/allowlists/",
}
expected_owned_paths = expected_created_paths | permitted_modified_paths

# Parse only literal git-add commands inside fenced command blocks.
current_task = "unknown"
in_fence = False
in_git_add = False
commits_by_path = {}

for line in text.splitlines():
    m_task = re.match(r"^## (Task \d+)", line)
    if m_task:
        current_task = m_task.group(1)

    if line.startswith("```"):
        in_fence = not in_fence
        if not in_fence:
            in_git_add = False
        continue

    if not in_fence:
        continue

    stripped = line.strip()

    if stripped.startswith("git add "):
        in_git_add = True
        fragment = stripped.removeprefix("git add ").rstrip(" \\").strip()
    elif in_git_add:
        fragment = stripped.rstrip(" \\").strip()
    else:
        continue

    for path in shlex.split(fragment):
        commits_by_path.setdefault(path, []).append(current_task)
    if not stripped.endswith("\\"):
        in_git_add = False

missing = expected_created_paths - set(commits_by_path)
unexpected = set(commits_by_path) - expected_created_paths - permitted_modified_paths
multiply_committed = {
    path: sorted(set(tasks))
    for path, tasks in commits_by_path.items()
    if path in expected_created_paths and len(set(tasks)) != 1
}
missing_modified = permitted_modified_paths - set(commits_by_path)

# Parse every Task Files section and retain declared task ownership.
files_by_path = {}
current_task = "unknown"
for line in text.splitlines():
    task_match = re.match(r"^## (Task \d+)", line)
    if task_match:
        current_task = task_match.group(1)
    file_match = re.match(
        r"^- (?:Create|Test|Modify(?: \([^)]*\))?): `([^`]+)`",
        line.strip(),
    )
    if file_match:
        path = file_match.group(1)
        files_by_path.setdefault(path, set()).add(current_task)

# Parse the top-level ownership table.
table_by_path = {}
table_kind_by_path = {}
for path, task, kind in re.findall(
    r"^\| `([^`]+)` \| (Task \d+) \| (Create|Modify) \|$",
    text,
    re.MULTILINE,
):
    if path in table_by_path:
        print(f"duplicate-coverage-table-path: {path}")
        sys.exit(1)
    table_by_path[path] = task
    table_kind_by_path[path] = kind

# Parse Task 11 Step 7's exact scope sets.
required_match = re.search(r"required_paths = \{\n(?P<body>.*?)\n\}", text, re.DOTALL)
prefix_match = re.search(r"optional_prefixes = \{\n(?P<body>.*?)\n\}", text, re.DOTALL)
if required_match is None or prefix_match is None:
    print("scope-set-parse-failed")
    sys.exit(1)
scope_required_paths = set(re.findall(r'"([^"]+)"', required_match.group("body")))
scope_fixture_prefixes = set(re.findall(r'"([^"]+)"', prefix_match.group("body")))

violations = []
for label, values in (
    ("missing-created", sorted(missing)),
    ("unexpected-git-add", sorted(unexpected)),
    ("multiply-committed", sorted(multiply_committed.items())),
    ("missing-modified", sorted(missing_modified)),
    ("coverage-table-missing", sorted(expected_owned_paths - set(table_by_path))),
    ("coverage-table-unexpected", sorted(set(table_by_path) - expected_owned_paths)),
    ("files-missing", sorted(expected_owned_paths - set(files_by_path))),
    ("files-unexpected", sorted(set(files_by_path) - expected_owned_paths)),
    (
        "scope-required-mismatch",
        sorted(scope_required_paths ^ (expected_owned_paths - expected_fixture_boundaries)),
    ),
    ("scope-fixture-prefix-mismatch", sorted(scope_fixture_prefixes ^ expected_fixture_boundaries)),
):
    if values:
        violations.append(f"{label}: {values}")

for path in sorted(expected_owned_paths):
    expected_task = table_by_path.get(path)
    file_tasks = files_by_path.get(path, set())
    commit_tasks = set(commits_by_path.get(path, []))
    if file_tasks != {expected_task}:
        violations.append(
            f"files-task-mismatch: {path}: table={expected_task} files={sorted(file_tasks)}"
        )
    if commit_tasks != {expected_task}:
        violations.append(
            f"git-add-task-mismatch: {path}: table={expected_task} commits={sorted(commit_tasks)}"
        )
    expected_kind = "Create" if path in expected_created_paths else "Modify"
    if table_kind_by_path.get(path) != expected_kind:
        violations.append(
            f"table-kind-mismatch: {path}: expected={expected_kind} "
            f"actual={table_kind_by_path.get(path)}"
        )

if violations:
    print("\n".join(violations))
    sys.exit(1)
print(
    "commit-presence-uniqueness-scope-gate-pass "
    f"created={len(expected_created_paths)} modified={len(permitted_modified_paths)}"
)
PY
```

Fail when any created/new-test artifact is absent, unexpected, or owned by more
than one task; when any modified path is absent or mixed into the created set;
when fixture-directory commit boundaries differ; or when the coverage table,
Task Files sections, `git add` commands, and Task 11 scope allowlist disagree in
either direction.

---

## Task 12: PR-preparation gate (requires separate approval before opening a PR)

Do **not** open a PR until separately authorized. When authorized:

- Run the `code-review` skill against the branch diff; address all critical/warning findings or record a concrete technical reason; re-run after changes.
- PR body includes: bound identifiers (`R3-01b-DESIGN-B2`, `R3-01b-PLAN-B9`), base `0bf55db9`, seeded-artifact hashes (Task 1), `ansible-playbook --version` for the 2.15.x gate (Task 9), and all Task 11 gate outputs.
- The PR resolves `R3-01b` only; it does **not** close issue #202, does **not** change tracker status beyond `in_progress`, and does **not** claim `ready_for_review` or merge credit within this plan's scope.

---

## Verification checklist (definition of done for the slice)

- [ ] Pre-start re-baseline gate passed (`origin/ansible == 0bf55db9`).
- [ ] Approved design + plan seeded and committed into the worktree with recorded hashes.
- [ ] Only the `R3-01b` tracker row set to `in_progress`; #202 remains separate/open; no `ready_for_review`/merge credit.
- [ ] Finalization dry-run preview reports the real `restore_count`/`restore_names`.
- [ ] Candidate → staged-validate → publish: malformed data never published; undefined/non-mapping/empty+non-empty mapping `resources`/skipped/malformed all reach the sanitized `fail_msg`; deliberate `{resources: []}` succeeds.
- [ ] Task 3 fake API: context-manager cleanup; generic named Restore GET returning valid Kubernetes objects; 404 Status for absent names; DELETE with state removal; `delete_failures` config; aggregate/per-name counters; Restore discovery advertises exactly `["get", "list", "delete"]`; normally collected discovery and CRUD tests retain GET→DELETE→LIST omission→GET 404→DELETE 404, route-specific delete failure, and counter coverage.
- [ ] Canonical fixture contract: `restore_fixture` always supplies `apiVersion`, `kind`, and metadata name/namespace/resourceVersion; `multiclusterhub_fixture` supplies the same Kubernetes identity plus MCH status; the collected fixture-contract test passes; every fake API and valid execute-mode Restore/MCH fixture uses those helpers, while deliberately malformed validator-only mappings remain explicit.
- [ ] R3-A2 execute tests: separate candidate-only cleanup scenario, unexpected-Restore blocker scenario (zero DELETEs), and three API-failure scenarios (list/GET/DELETE) each attributable to their intended route.
- [ ] R3-A3 harness modes are mutually exclusive: normal mode alone runs `tasks_from: discover_resources`; direct mode skips normal discovery, uses role-relative `include_role tasks_from: assert_restore_source_shape`, contains no `COLLECTION_ROOT` dependency, and preserves validate-before-publish ordering.
- [ ] Task 6 scenario-builder matrix passes: every normal-mode builder pre-seeds empty BackupSchedule discovery; dry-run normal mode also pre-seeds empty MCH and secondary Restore discovery; execute injected-fixture tests pre-seed or omit the secondary Restore source according to route intent; execute no-fixture mode omits both secondary Restore and authoritative old-hub inputs; direct mode requires neither BackupSchedule pre-seeding nor API requests.
- [ ] Initial R3-A3 red output proves normal mode entered, all three unrelated dry-run sources were pre-seeded, `OLD_HUB_SEED_REGISTER_REACHED=True`, the role completed, and the current old-hub register result is skipped; the red failure is the clobbered authoritative shape rather than kubeconfig, API discovery, BackupSchedule, MCH, or secondary Restore setup.
- [ ] Task 6 production ordering helper recognizes both `ansible.builtin.include_tasks` and `include_tasks`, in both scalar and `{file: ...}` forms; it requires exactly one old-hub validation include and retains candidate-before-validation-before-publication plus ownership assertions. The synthetic parameterized regression accepts all four supported forms and rejects publication before validation.
- [ ] Malformed direct candidates fail with sanitized output and emit `DIRECT_MODE_ENTERED=True`, `NORMAL_MODE_ENTERED=False`, `OLD_HUB_AUTHORITATIVE_DEFINED_BEFORE_DIRECT=False`, and `OLD_HUB_AUTHORITATIVE_DEFINED_AFTER_FAILURE=False`; aggregate fake `request_count` remains exactly zero.
- [ ] Deterministic execute-mode fake-API tests: A2 fresh overrides stale pre-seed, candidate names/count are correct, candidate deletes execute once per route, non-candidates remain, wait observes removal, and API failure is fatal; A3 live published without fixture; A3 fixture authoritative when injected; live-query failure fatal.
- [ ] Task 4 artifacts committed in their own Task 4 commit (not deferred to Task 5).
- [ ] Commit presence/uniqueness audit (Task 11 Step 9) passes: every created/new-test artifact and fixture-directory boundary is present in exactly one task; no created path is omitted or committed twice; modified files are separate; the coverage table, Task Files sections, `git add` commands, and scope allowlist agree bidirectionally.
- [ ] Scanner is literal-only (dynamic names excluded), detects collisions in `tasks`/`handlers`/playbook `post_tasks`, and raises a path-bearing error on parse failure.
- [ ] Allowlist: 2 intentional + 10 debt(#202); non-empty required values, unique `(path,variable)`, no cross-category duplicates, unknown categories rejected, exact #202 reference; malformed-allowlist fixtures rejected automatically.
- [ ] Skipped-register semantics confirmed on the default controller and on ansible-core 2.15.x (foundation container or Python 3.11), with `ansible-playbook --version` recorded.
- [ ] AGENTS.md documents the collision rule + category-specific allowlist policy.
- [ ] All Task 11 gates green; base-aware `git diff --check 0bf55db9..HEAD` clean; changed-file/protected-file/scope checks pass.
- [ ] No Python CLI production/test file modified; no `TR2D-02` change; no #202 fix; tracker only `in_progress`.
