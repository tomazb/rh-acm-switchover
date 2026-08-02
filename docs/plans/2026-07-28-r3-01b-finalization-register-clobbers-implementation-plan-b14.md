# R3-01b Finalization Register/`set_fact` Clobbers + Collision Guardrail — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Plan identifier:** `R3-01b-PLAN-B14`
**Supersedes:** `R3-01b-PLAN-B13` (local artifact; SHA-256
`93d82978ae9668a3771606d531be2320b1371a11f44f1f7651031056151c064b`,
Git blob `c0a6a32d2cd4b638225e2bce5229a117c722238a`, 4,364 lines,
200,898 bytes)
**Provenance note:** `R3-01b-PLAN-B4` (SHA-256
`bf86f3abfb94b0939a61e42b1152ba8de5e13b46a08c0f61f367f5a888e652df`, Git blob
`c1d91f2956cef2dc2384987bd4bfdbef9d317914`) was also a local artifact that
never appeared at any branch head. The commit
`f7824a69fac33d2182decdf99dc3460da8205694` is the historical PR #203 head and
contains only the failed B2 publication; B4, B5, B6, B7, and B8 were never
associated with that head. B9 preserved every accepted B8 correction and was
published at `b11a23189fee55b775a20c0833e03d436b94befa`. B10 preserves every
accepted B9 correction and changes only identifier/provenance text, the Task 5
R3-A2 execute-variable helper, exact fixture/status/commit ownership gates,
positive fake-failure route attribution, isolated ansible-core 2.15 collection
dependency installation, and their directly required consistency checks. B11
preserves every accepted B10 correction and changes only
identifier/provenance/path text, the Task 6 subprocess harness, reconciliation
of the existing finalization discovery unit contracts, concrete dynamic-tree
scanner boundary tests, and their directly required audit/checklist checks.
B12 preserves every accepted B11 correction and changes only
identifier/provenance/path text, the complete default implementation bootstrap,
the Task 5/6 subprocess environment contract, the Task 3 smoke environment,
the preserved isolated ansible-core 2.15 lane, and their directly required
audit/checklist consistency checks.
B13 preserves every accepted B12 correction and changes only
identifier/provenance/path text, Task 6 neutral fixture input and runtime-fact
seeding, defect-specific variable-precedence red/green evidence, complete
normal fixture-scenario coverage, the Task 9 variable-precedence regression,
and their directly required audit/checklist consistency checks.
B14 preserves every accepted B13 correction and changes only
identifier/provenance/path text, concrete AST-enforced Task 6 fixture tests,
occurrence-exact `git add` ownership and literal commit-boundary validation,
two physically distinct fake hubs for Task 6 runtime routing, the complete
Task 9 subprocess harness, the six-finding review disposition, the
focused-review Task 3 CRUD assertion consistency correction, and their
directly required audit/checklist consistency checks.
**Design of record:** `R3-01b-DESIGN-B2` (`docs/plans/2026-07-28-r3-01b-finalization-register-clobbers-design.md`, APPROVED)
**Bound approved base:** `0bf55db9eed76ae7d60844b806975c04cd0111e4` (PR #201 merge commit)

**Goal:** Make the collection finalization dry-run Restore preview truthful, preserve fixture/deliberate-seed semantics with fail-closed staged shape validation, and add a recursive **literal-only** `register`/`set_fact` collision guardrail — collection-only, without touching the Argo CD regression, `TR2D-02`, or issue #202.

**Architecture:** For each colliding name, register the live query to a distinct name, **select a temporary candidate, validate it with staged assertions, and publish the authoritative fact only after validation** (never publish malformed data). Add a static pytest guardrail that scans `roles/**/tasks`, `roles/**/handlers`, and `playbooks/**` for **literal-scalar** `register`/`set_fact` name collisions (Jinja/computed names excluded) and enforces a two-category (intentional / debt) allowlist with strict metadata validation.

**Tech Stack:** Ansible collection (`kubernetes.core`, `ansible.builtin.assert`), pytest static YAML-contract tests, pytest subprocess `ansible-playbook` integration harness with an executable fake Kubernetes API (`tests/integration/`).

---

## B14 complete six-finding CodeRabbit disposition

The complete six-finding payload was recovered from the local CodeRabbit review
record for B13 and validated against the approved design, B13, current
finalization tasks, and current tests. Findings 1 and 2 are publication
blockers; finding 4 is accepted runtime-test hardening. The remaining findings
are rejected only where the governing design or already-concrete plan contract
supplies contrary evidence.

| # | Exact original finding | Severity | Disposition | Source validation | B14 change or rejection rationale | Verification evidence |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | **Assert neutral-input validation in every fixture test body.** The B13 self-check only searches for scenario-description strings and the helper definition; it does not inspect the malformed, skipped, missing-resource, or valid execute test bodies. Removing `_assert_runtime_fixture_input(variables)` from one of those tests would still pass the gate. Parse the test functions (or enumerate their names explicitly) and require the helper call in each required normal fixture scenario. Also applies to: 4226-4239. | major | **Accepted — blocking.** | B13 Step 13 structurally inspected only `test_injected_fixture_preserved`; the remaining fixture scenarios were prose-token checks. | B14-C1 supplies four concrete executable test bodies and an AST audit that discovers every fixture-bearing builder call, enforces the exact four-function manifest, mapping name, one helper call, `_run` argument, source-line ordering, and nine case IDs. | Task 11 must print `b14-fixture-test-ast-gate-pass functions=4 malformed_cases=9`. |
| 2 | **Validate actual commit boundaries in the ownership audit.** This script records only `git add` occurrences grouped by task. Duplicate `git add` commands within one task collapse through `set(tasks)`, and a missing corresponding `git commit` is never detected. That does not enforce the stated “exactly one `git add`/commit boundary” contract. Track each add occurrence, parse the commit commands, and require exactly one add boundary plus one commit boundary per owned artifact. | major | **Accepted — blocking.** | B13 Step 9 retained task names rather than occurrences and never parsed literal commit commands. A duplicate same-task addition and a missing commit could pass. | B14-C2 parses logical commands only in command fences, preserves add/commit occurrences with command index and source line, enforces exact task boundaries/messages, and rejects all duplicate or unexpected additions. It does not use a set conversion to decide multiplicity. | Task 11 must print `commit-presence-uniqueness-boundary-scope-gate-pass created=33 modified=7 tasks_with_commits=10`. |
| 3 | **Do not identify the deliberate seed by shape alone.** The stated predicate accepts any non-skipped mapping containing `resources: []`; it cannot distinguish the intended old-hub dry-run seed from arbitrary stale or injected empty data. Carry explicit source identity, or constrain validation to the exact seed-path condition, and add a negative test for an unrelated empty mapping. | major | **Rejected.** | Approved design §6.1 explicitly defines the distinction by validated value shape: a mapping with present list-valued `resources` and not a skipped result is authoritative; it explicitly requires deliberate `{resources: []}` to succeed. Design §5.1 also requires injected discovered data to remain authoritative. B13 Task 6 constrains production selection to mutually exclusive fixture, dry-run seed, and execute-live paths before validation. | Source identity is a selection/ownership concern, not an additional shape-validator field. Rejecting an injected validated empty list would contradict the approved fixture semantics. B14 retains the exact selection guards and does not broaden source acceptance. | Task 6 parsed production-order test proves candidate ownership and validation-before-publication; the concrete malformed matrix rejects absent, skipped, non-mapping, non-list, and malformed-entry inputs while the intentional empty list remains positive coverage. |
| 4 | **Make the fake API hub-aware.** Both primary and secondary kubeconfigs point to the same `hub.url`, and the fake dispatches only on HTTP paths. A regression that swaps the primary and secondary clients can therefore still return the expected resources and counters, so the Task 6 routing assertions do not prove hub identity. Use separate fake servers/instances or add an explicit hub identity to each kubeconfig request and assert it server-side. Also applies to: 361-388, 1391-1394. | major | **Accepted — runtime-test hardening.** | Current `discover_resources.yml` routes the named old-hub Restore GET through primary credentials and MCH/secondary Restore LISTs through secondary credentials, but B13's one-server test cannot prove that physical distinction at runtime. | B14-C3 requires two distinct `FakeAcmBackupHub` instances and URLs in every execute scenario, distinct kubeconfigs, and cross-hub positive/zero counter assertions. | Task 6 runtime assertions plus the B14 dual-hub AST/token audit prove identity assertions, distinct URLs, two context managers, and route ownership. |
| 5 | **Specify mutually exclusive source-selection guards.** The design requires exactly one authoritative source but does not define the precise guards or precedence when injected/dry-run data and a live result are both present—or when neither is valid. Define mutually exclusive conditions and add a conflict-case test so publication cannot depend on task ordering. | major | **Rejected.** | B13 Task 6 Step 4 already gives exact guards: dry-run seed requires authoritative undefined; fixture selection requires authoritative defined; live query and live-candidate selection require authoritative undefined; the validator fails when no candidate exists; publication alone follows validation. The runtime fixture and live no-fixture scenarios exercise both branches, and the parsed ordering test rejects publication before validation. | B14 retains those exact conditions. A fixture/live “conflict” cannot occur because the live task is guarded by authoritative undefined; adding a second production source or file would violate the constrained scope. | Static routing/ordering contracts plus the dual-hub runtime fixture and no-fixture cases prove mutually exclusive route selection and fail-closed absence. |
| 6 | **Mark these dispositions as planned, not completed.** The table says collisions #1–2 are “Fixed,” while this document also states that no production code has been edited yet (Lines 363–364). Rename these to “Planned fix” and clarify that they leave the allowlist only after implementation. | minor | **Rejected for the implementation plan.** | The comment targets approved design §6.5. In that design, the disposition column describes the post-implementation allowlist state (“Fixed by R3-A2/R3-A3 → no allowlist entry”), while design §§1, 9, and 11 explicitly state current scope and that no production edit has occurred. B13/B14 binding preconditions separately state the current 14-collision baseline and the future work in Task 5 and Task 6. | This B14 run cannot modify the approved design, and the plan does not claim either collision is already fixed. Rewording the approved design is outside the authorized B13→B14 delta. | Task 0 baseline requires 14 current collisions; Task 5 and Task 6 own the two fixes; Task 8 requires the post-fix allowlist to contain only 2 intentional + 10 debt entries. |

No finding is silently omitted. The two accepted audit defects block publication
unless their executable gates pass. Two focused full-diff reviews are required
after all B14 corrections; completion requires:

```text
unresolved_blocking_findings=0
unresolved_nonblocking_findings=0
```

Focused review 1 produced two additional minor findings. The fake CRUD
assertion finding was accepted because Task 3 already declares a success
contract of HTTP 200 plus the deleted resource body; the concrete test below
now proves that exact contract. The path-generalization finding was rejected:
this governed plan intentionally binds the primary checkout and worktree under
`/home/tomaz/sources/rh-acm-switchover`; replacing those operator-specified
locations with ambient-root discovery or an unbound source variable would
weaken, not strengthen, the pre-start identity gate. This disposition adds no
file or production scope.

---

## Binding preconditions and boundary (read before Task 0)

- **Approved base:** `0bf55db9eed76ae7d60844b806975c04cd0111e4`. Branch from exactly this SHA.
- **Scope (corrected):** *No Python CLI production files (`acm_switchover.py`, `lib/`, `modules/`) or root Python CLI tests under `tests/` are modified. Collection pytest files are in scope.* Modify only files under `ansible_collections/tomazb/acm_switchover/` plus the authorized documentation boundary below.
- **Authorized documentation boundary:** exactly these tracked docs may be edited:
  - `docs/plans/2026-07-28-r3-01b-finalization-register-clobbers-design.md`
  - `docs/plans/2026-07-28-r3-01b-finalization-register-clobbers-implementation-plan-b14.md`
  - `thermos-resolution-plan.md`
  - `AGENTS.md`
  - `CHANGELOG.md`
- **Strict separation:** do not touch `TR2D-02` code/tests, the merged Argo CD scoped-discovery work, or any unrelated `R3` slice.
- **No work on issue #202.** The preflight collision is only *allowlisted* (debt category), never fixed here. Issue #202 stays **separate and open**.
- **Tracker:** at implementation start, update **only** the `R3-01b` row (see Task 2). Do **not** claim `ready_for_review` and do **not** record merge credit at any point in this plan.
- **Builder execution requires separate explicit approval of `R3-01b-PLAN-B14`.** Do not create the branch, edit any tracked file, or open a PR until that approval is given.
- **Guardrail boundary (design §6.2):** `roles/**/tasks/**/*.{yml,yaml}`, `roles/**/handlers/**/*.{yml,yaml}`, `playbooks/**/*.{yml,yaml}`. **Playbook scanning must traverse every play-level task section — `pre_tasks`, `tasks`, `post_tasks`, `handlers` — before applying recursive `block`/`rescue`/`always` flattening.** Role `tasks`/`handlers` files are flat task lists, flattened directly.
- **Current-state facts:** playbooks have **zero** collisions today; no `roles/*/handlers/` files exist yet (boundary still includes them); the guardrail finds **14** collisions — Tasks 5–6 fix 2, leaving 2 intentional + 10 debt(#202).

All commands run from the worktree root with its venv active.

---

## Plan self-check (commit-coverage contract)

> **B7-C4 gate:** every created/new-test artifact in this plan must appear in
> exactly one `git add`/commit boundary below. Modified files are tracked
> separately. Task 11 Step 9 proves presence, uniqueness, and bidirectional
> consistency among this table, Task `Files` sections, `git add` commands, and
> the Task 11 exact status map. Do not open a PR if any set or task ownership
> differs.

The following table is the complete task-ownership contract:

| Path | Task | Kind |
| ---- | ---- | ---- |
| `docs/plans/2026-07-28-r3-01b-finalization-register-clobbers-design.md` | Task 1 | Create |
| `docs/plans/2026-07-28-r3-01b-finalization-register-clobbers-implementation-plan-b14.md` | Task 1 | Create |
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
| `ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/collisions/tasks_collision.yml` | Task 7 | Create |
| `ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/collisions/handlers_collision.yml` | Task 7 | Create |
| `ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/collisions/playbook_collision.yml` | Task 7 | Create |
| `ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/collisions/clean.yml` | Task 7 | Create |
| `ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/collisions/playbook_pre_tasks_block_collision.yml` | Task 7 | Create |
| `ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/collisions/playbook_tasks_rescue_collision.yml` | Task 7 | Create |
| `ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/collisions/playbook_post_tasks_always_collision.yml` | Task 7 | Create |
| `ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/collisions/playbook_handlers_block_collision.yml` | Task 7 | Create |
| `ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/collisions/dynamic_register.yml` | Task 7 | Create |
| `ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/collisions/dynamic_setfact.yml` | Task 7 | Create |
| `ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/collisions/broken.yml` | Task 7 | Create |
| `ansible_collections/tomazb/acm_switchover/tests/unit/test_register_setfact_collision_guardrail.py` | Task 7 | Create |
| `ansible_collections/tomazb/acm_switchover/tests/unit/register_setfact_collision_allowlist.yml` | Task 8 | Create |
| `ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/allowlists/missing_removal_condition.yml` | Task 8 | Create |
| `ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/allowlists/empty_rationale.yml` | Task 8 | Create |
| `ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/allowlists/unknown_category.yml` | Task 8 | Create |
| `ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/allowlists/duplicate_pair.yml` | Task 8 | Create |
| `ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/allowlists/cross_category_duplicate.yml` | Task 8 | Create |
| `ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/allowlists/wrong_issue_ref.yml` | Task 8 | Create |
| `ansible_collections/tomazb/acm_switchover/tests/integration/playbooks/register_skip_semantics.yml` | Task 9 | Create |
| `ansible_collections/tomazb/acm_switchover/tests/integration/test_register_skip_semantics.py` | Task 9 | Create |
| `AGENTS.md` | Task 10 | Modify |
| `CHANGELOG.md` | Task 10 | Modify |

---

## Task 0: Hard pre-start gate + isolated worktree (execute only after `R3-01b-PLAN-B14` approval)

**Step 1: Re-baseline gate — fetch and require `origin/ansible == approved base`**

```bash
cd /home/tomaz/sources/rh-acm-switchover
git fetch origin ansible
BASE=0bf55db9eed76ae7d60844b806975c04cd0111e4
REMOTE=$(git rev-parse origin/ansible)
echo "approved_base=$BASE"; echo "origin/ansible=$REMOTE"
test "$REMOTE" = "$BASE" || { echo "STOP: origin/ansible advanced past approved base — explicit delta assessment / re-baselining required before implementation."; exit 1; }
```

**HARD GATE:** if `origin/ansible` has advanced, **stop**. Do not implement. Return to the operator for delta assessment and re-baselining of `R3-01b-PLAN-B14`.

**Step 2: Create the worktree/branch and complete the fail-closed default dependency bootstrap**

```bash
git worktree add \
  .claude/worktrees/r3-01b \
  -b fix/r3-01b-finalization-register-clobbers \
  "$BASE"

cd .claude/worktrees/r3-01b

python3 -m venv .venv
source .venv/bin/activate

python -m pip install --upgrade pip
python -m pip install \
  -r requirements.txt \
  -r requirements-dev.txt

DEFAULT_COLLECTIONS_DIR="$PWD/.venv/collections"
mkdir -p "$DEFAULT_COLLECTIONS_DIR"

ansible-galaxy collection install \
  -p "$DEFAULT_COLLECTIONS_DIR" \
  -r ansible_collections/tomazb/acm_switchover/requirements.yml

export ANSIBLE_COLLECTIONS_PATH="$PWD:$DEFAULT_COLLECTIONS_DIR"
export ANSIBLE_COLLECTIONS_SCAN_SYS_PATH=false
```

Immediately verify the complete default bootstrap:

```bash
python -c 'import kubernetes; print(kubernetes.__version__)'

test -d \
  "$DEFAULT_COLLECTIONS_DIR/ansible_collections/kubernetes/core"

ansible-playbook --version

ansible-galaxy collection list kubernetes.core

ansible-doc \
  -t module \
  kubernetes.core.k8s_info \
  >/dev/null
```

Record the output of every installation and verification command as
implementation evidence. The bootstrap is fail-closed: no Task 3 smoke run
and no Task 3, Task 5, or Task 6 targeted integration test may run until every
verification above passes. The default implementation lane must resolve
collections only through the repository root and
`$PWD/.venv/collections`; do not use `~/.ansible/collections` as an implicit
fallback.

**Step 3: Confirm base**

Run: `git rev-parse HEAD`
Expected: `0bf55db9eed76ae7d60844b806975c04cd0111e4`

---

## Task 1: Seed the approved design + plan into the worktree

Untracked files do **not** enter a worktree created from the base commit, so the approved artifacts must be copied in and committed first.

**Files:**
- Create: `docs/plans/2026-07-28-r3-01b-finalization-register-clobbers-design.md`
- Create: `docs/plans/2026-07-28-r3-01b-finalization-register-clobbers-implementation-plan-b14.md`

**Step 1: Copy the exact approved artifacts**

```bash
# from the primary worktree paths (or session artifacts), copy verbatim:
cp <primary>/docs/plans/2026-07-28-r3-01b-finalization-register-clobbers-design.md docs/plans/
cp <primary>/docs/plans/2026-07-28-r3-01b-finalization-register-clobbers-implementation-plan-b14.md docs/plans/
```

**Step 2: Record content hashes as evidence (design + plan)**

```bash
sha256sum docs/plans/2026-07-28-r3-01b-finalization-register-clobbers-design.md \
          docs/plans/2026-07-28-r3-01b-finalization-register-clobbers-implementation-plan-b14.md
git hash-object docs/plans/2026-07-28-r3-01b-finalization-register-clobbers-design.md \
                docs/plans/2026-07-28-r3-01b-finalization-register-clobbers-implementation-plan-b14.md
```
Capture both sets of hashes in the PR body / evidence log; they bind the delivered artifacts to `R3-01b-DESIGN-B2` / `R3-01b-PLAN-B14`.

**Step 3: Commit**

```bash
git add docs/plans/2026-07-28-r3-01b-finalization-register-clobbers-design.md \
        docs/plans/2026-07-28-r3-01b-finalization-register-clobbers-implementation-plan-b14.md
git commit -m "docs(plan): seed approved R3-01b-DESIGN-B2 and R3-01b-PLAN-B14 into worktree"
```

---

## Task 2: Mark `R3-01b` in progress (tracker only)

**Files:**
- Modify: `thermos-resolution-plan.md`

**Step 1: Update only the `R3-01b` tracker row**

- Change `R3-01b` status from `planned` to `in_progress`.
- Record the identifiers and branch in the row/notes: `R3-01b-DESIGN-B2`, `R3-01b-PLAN-B14`, branch `fix/r3-01b-finalization-register-clobbers`, base `0bf55db9`.
- Update the document's `Last Updated` date.
- Do **not** touch the `R3-A2`/`R3-A3` finding rows' resolution status, the #202 reference, or any other row. Do **not** write `ready_for_review` or merge credit.

**Step 2: Commit**

```bash
git add thermos-resolution-plan.md
git commit -m "docs(tracker): mark R3-01b in_progress with R3-01b-DESIGN-B2/PLAN-B14 identifiers"
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
- mutable namespaced Restore state so deletes persist in subsequent list and named-GET calls (execute-mode wait can observe candidate removal), plus a thread-safe `restore_names` snapshot property so failure tests can prove an injected DELETE failure did not mutate state.
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

Route-specific counters prove attempted reachability, including injected HTTP
500 responses. Increment each counter immediately after matching its route and
**before** evaluating the corresponding failure configuration:

- increment `secondary_restore_list_hits` before checking LIST failure
  configuration;
- increment `old_hub_restore_named_get_hits[name]` and the aggregate
  `old_hub_restore_get_hits` before checking `get_failures`; and
- increment `secondary_restore_named_delete_hits[name]` and the aggregate
  `secondary_restore_delete_hits` before checking `delete_failures`.

Every injected fake HTTP 500 response must be a Kubernetes Status object with
`kind: Status`, `status: Failure`, `reason: InternalError`, and `code: 500`.
Its `message` must be the stable route-specific marker:

- LIST: `FAKE_RESTORE_LIST_FAILURE`
- named GET: `FAKE_RESTORE_GET_FAILURE:<name>`
- named DELETE: `FAKE_RESTORE_DELETE_FAILURE:<name>`

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
        assert status == 200
        assert payload["metadata"]["name"] == name
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
        assert hub.secondary_restore_delete_hits == 1
        assert payload["message"] == f"FAKE_RESTORE_DELETE_FAILURE:{other}"
        assert other in hub.restore_names
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
8. `delete_failures` returns the intended route-specific failure marker and
   increments its route counters before failure injection.
9. An injected DELETE failure leaves the Restore in mutable fake state.

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

Keep a `FakeAcmBackupHub` context open with two canonical
`restore_fixture(...)` resources while the smoke command runs. Generate the
throwaway playbook, variables file, and kubeconfig with names containing
`r3-01b-k8s-info-smoke` (the exact paths may be assigned to
`$SMOKE_PLAYBOOK`, `$SMOKE_VARS`, and `$SMOKE_KUBECONFIG`). The playbook must
use `kubernetes.core.k8s_info` to list Restore resources from the fake and must
assert and print a count of two.

Before invoking the playbook, fail closed on both dependency lookups:

```bash
python -c 'import kubernetes'

ANSIBLE_COLLECTIONS_PATH="$PWD:$PWD/.venv/collections" \
ANSIBLE_COLLECTIONS_SCAN_SYS_PATH=false \
ansible-doc \
  -t module \
  kubernetes.core.k8s_info \
  >/dev/null
```

Then run the temporary playbook in the same explicit environment:

```bash
ANSIBLE_COLLECTIONS_PATH="$PWD:$PWD/.venv/collections" \
ANSIBLE_COLLECTIONS_SCAN_SYS_PATH=false \
ansible-playbook "$SMOKE_PLAYBOOK" \
  -i localhost, \
  -c local \
  -e "@$SMOKE_VARS"
```

The smoke fails if the Python Kubernetes client cannot import, if
`kubernetes.core.k8s_info` cannot resolve, if `ansible-playbook` exits
non-zero, or if the asserted count is not two. Record the dependency lookup,
playbook output, and asserted count as implementation evidence.

Delete every temporary smoke artifact after the run, including the playbook,
variables file, and kubeconfig. Then confirm no smoke artifact remains:

```bash
git status --porcelain=v1 --untracked-files=all
```

The status output may contain only implementation-owned paths from the
task-ownership contract; any path containing `r3-01b-k8s-info-smoke` fails the
smoke cleanup gate.

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
# an empty successful result. (R3-01b-DESIGN-B2 section 6.1; PLAN-B14 items 8-9)

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
import copy
import os
import subprocess
from pathlib import Path

import pytest
import yaml

from ansible_collections.tomazb.acm_switchover.tests.conftest import (
    _ansible_env,
)
from argocd_fake_api import write_kubeconfig
from finalization_fake_api import FakeAcmBackupHub, restore_fixture

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
    env = _ansible_env(REPO_ROOT, tmp_path)
    env["ANSIBLE_COLLECTIONS_PATH"] = os.environ.get(
        "ANSIBLE_COLLECTIONS_PATH",
        ":".join(
            [
                str(REPO_ROOT),
                str(REPO_ROOT / ".venv" / "collections"),
            ]
        ),
    )
    env["ANSIBLE_COLLECTIONS_SCAN_SYS_PATH"] = "false"
    return subprocess.run(
        [ANSIBLE_BIN, PLAYBOOK,
         "-i", "ansible_collections/tomazb/acm_switchover/examples/inventory.yml",
         "-e", f"@{vf}"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=env,
        timeout=300,
    )

def _dry_run_vars(restores):
    return {
        "acm_switchover_execution": {"mode": "dry_run"},
        "acm_switchover_hubs": {
            "secondary": {"kubeconfig": "/dev/null", "context": "sec"},
            "primary": {"kubeconfig": "/dev/null", "context": "pri"}},
        "acm_switchover_operation": {"old_hub_action": "secondary", "restore_only": False},
        "acm_finalization_restores_info": {"resources": restores},
    }

def _execute_vars(tmp_path, hub, *, stale_seed=()):
    """Build the complete R3-A2 execute-mode variable mapping."""
    secondary_kubeconfig = tmp_path / "secondary.kubeconfig"
    write_kubeconfig(
        secondary_kubeconfig,
        context="secondary",
        server=hub.url,
    )
    return {
        "acm_switchover_execution": {"mode": "execute"},
        "acm_switchover_hubs": {
            "secondary": {
                "kubeconfig": str(secondary_kubeconfig),
                "context": "secondary",
            },
            "primary": {
                "kubeconfig": "/dev/null",
                "context": "primary",
            },
        },
        "acm_switchover_operation": {
            "old_hub_action": "secondary",
            "restore_only": False,
        },
        # This is deliberately stale input. Execute mode must ignore it and
        # use the fresh secondary-hub list.
        "acm_finalization_restores_info": {
            "resources": copy.deepcopy(list(stale_seed)),
        },
    }

def test_dry_run_preview_reports_true_restore_count(tmp_path):
    r = _run(tmp_path, _dry_run_vars([
        {"metadata": {"name": "restore-acm-full"}},
        {"metadata": {"name": "restore-acm-passive-sync"}}]))
    assert r.returncode == 0, r.stderr
    assert "RESTORE_COUNT=2" in r.stdout, r.stdout
```

Task 5 must use the repository's shared `_ansible_env` unchanged. The helper
keeps Ansible local/remote temporary directories isolated, while the explicit
override above resolves the repository collection plus Task 0's isolated
`.venv/collections` installation by default. If Task 9 exports
`ANSIBLE_COLLECTIONS_PATH`, `os.environ.get(...)` preserves that value
unchanged instead of replacing it with the default path.
`ACM_ANSIBLE_PLAYBOOK_BIN` and `ACM_ANSIBLE_PYTHON` remain authoritative, and
ambient `sys.path` collection scanning stays disabled. No Task 5
`subprocess.run` call may omit `env=env`.

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
        r = _run(tmp_path, _execute_vars(tmp_path, hub, stale_seed=stale_restores))
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
        r = _run(tmp_path, _execute_vars(tmp_path, hub, stale_seed=[]))
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
        r = _run(tmp_path, _execute_vars(tmp_path, hub, stale_seed=[]))
    assert r.returncode != 0, r.stdout
    # Failure attributable to the list route, not a DELETE or GET route
    assert hub.secondary_restore_list_hits >= 1
    assert sum(hub.old_hub_restore_named_get_hits.values()) == 0
    assert hub.secondary_restore_delete_hits == 0
    assert "FAKE_RESTORE_LIST_FAILURE" in (r.stdout + r.stderr)

def test_execute_mode_named_get_failure_is_fatal(tmp_path):
    """A 500 on the named Restore GET route (pre-DELETE probe) must cause a
    fatal exit; no DELETE calls must follow."""
    with FakeAcmBackupHub(
        restores=[restore_fixture("restore-acm-full", resource_version="41")],
        get_failures={"restore-acm-full": True},
    ) as hub:
        r = _run(tmp_path, _execute_vars(tmp_path, hub, stale_seed=[]))
    assert r.returncode != 0, r.stdout
    assert hub.secondary_restore_list_hits >= 1
    assert hub.old_hub_restore_named_get_hits.get("restore-acm-full", 0) >= 1
    assert hub.secondary_restore_named_delete_hits.get("restore-acm-full", 0) == 0
    assert "FAKE_RESTORE_GET_FAILURE:restore-acm-full" in (
        r.stdout + r.stderr
    )

def test_execute_mode_delete_failure_is_fatal(tmp_path):
    """A 500 on the Restore DELETE route must cause a fatal exit."""
    with FakeAcmBackupHub(
        restores=[restore_fixture("restore-acm-full", resource_version="42")],
        delete_failures={"restore-acm-full": True},
    ) as hub:
        r = _run(tmp_path, _execute_vars(tmp_path, hub, stale_seed=[]))
    assert r.returncode != 0, r.stdout
    assert hub.secondary_restore_list_hits >= 1
    assert hub.old_hub_restore_named_get_hits.get("restore-acm-full", 0) >= 1
    assert hub.secondary_restore_named_delete_hits.get("restore-acm-full", 0) >= 1
    assert "FAKE_RESTORE_DELETE_FAILURE:restore-acm-full" in (
        r.stdout + r.stderr
    )
    assert "restore-acm-full" in hub.restore_names
```

Each failure must be attributable to the intended route (list / named GET /
DELETE) through a positive failing-route counter, expected prerequisite/later
route counters, and the stable failure marker. A nonzero subprocess exit alone
is insufficient attribution. The injected DELETE failure must also leave the
resource in mutable fake state.

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

**Variable-precedence basis:** Ansible's
[variable precedence documentation](https://docs.ansible.com/projects/ansible/latest/playbook_guide/playbooks_variables.html#understanding-variable-precedence)
places registered variables and runtime `set_fact` values in the same tier,
where a later definition replaces the earlier one, while `-e` extra variables
always win. Its
[registered-variable condition documentation](https://docs.ansible.com/projects/ansible/latest/playbook_guide/playbooks_conditionals.html#conditions-based-on-registered-variables)
also states that a skipped task still registers a skipped result. Therefore
the production-defect test must transport its fixture under a neutral extra
variable and create the authoritative name with runtime `set_fact`; passing the
authoritative name itself through `-e` would mask the clobber.

**Task 6 test-module subprocess harness (B11-C1; define before all scenario
builders):**

Task 6 owns a complete subprocess helper in
`test_finalization_old_hub_restore_runtime.py`. It does not import or rely on
Task 5's test module. Adapt only the import form if collection test discovery
requires the repository-qualified convention; preserve the same imported
symbols and behavior:

```python
import os
import subprocess
from pathlib import Path

import pytest
import yaml

from ansible_collections.tomazb.acm_switchover.tests.conftest import (
    _ansible_env,
)
from argocd_fake_api import write_kubeconfig
from finalization_fake_api import (
    FakeAcmBackupHub,
    multiclusterhub_fixture,
    restore_fixture,
)

REPO_ROOT = Path(__file__).resolve().parents[5]
PLAYBOOK = (
    "ansible_collections/tomazb/acm_switchover/tests/integration/"
    "playbooks/finalization_old_hub_restore_discovery.yml"
)
ANSIBLE_BIN = os.environ.get(
    "ACM_ANSIBLE_PLAYBOOK_BIN",
    "ansible-playbook",
)
ANSIBLE_PY = os.environ.get("ACM_ANSIBLE_PYTHON")


def _run(tmp_path, variables):
    values = dict(variables)
    if ANSIBLE_PY:
        values["ansible_python_interpreter"] = ANSIBLE_PY

    vars_file = tmp_path / "old-hub-vars.yml"
    vars_file.write_text(
        yaml.safe_dump(values, sort_keys=False),
        encoding="utf-8",
    )

    env = _ansible_env(REPO_ROOT, tmp_path)
    env["ANSIBLE_COLLECTIONS_PATH"] = os.environ.get(
        "ANSIBLE_COLLECTIONS_PATH",
        ":".join(
            [
                str(REPO_ROOT),
                str(REPO_ROOT / ".venv" / "collections"),
            ]
        ),
    )
    env["ANSIBLE_COLLECTIONS_SCAN_SYS_PATH"] = "false"

    return subprocess.run(
        [
            ANSIBLE_BIN,
            PLAYBOOK,
            "-i",
            (
                "ansible_collections/tomazb/acm_switchover/"
                "examples/inventory.yml"
            ),
            "-e",
            f"@{vars_file}",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=env,
        timeout=300,
    )
```

There is exactly one Task 6 `_run` definition. Every Task 6 call passes
`tmp_path` and a variables mapping. The environment-selected
`ACM_ANSIBLE_PLAYBOOK_BIN` and `ACM_ANSIBLE_PYTHON` keep the isolated
ansible-core 2.15 lane authoritative.

Task 6 uses the same repository subprocess-environment contract as Task 5:
`_ansible_env` provides isolated Ansible local/remote temporary directories;
the explicit default collection path is the repository root plus Task 0's
`.venv/collections`; ambient `sys.path` collection scanning is disabled; and
`os.environ.get(...)` preserves Task 9's explicitly exported isolated
`ANSIBLE_COLLECTIONS_PATH` unchanged. `ACM_ANSIBLE_PLAYBOOK_BIN` and
`ACM_ANSIBLE_PYTHON` remain authoritative. No Task 6 `subprocess.run` call may
omit `env=env`.

**Step 1: Create the harness playbook FIRST (item 7)**

`finalization_old_hub_restore_discovery.yml` initializes stable Boolean mode
markers, then makes normal discovery and direct-candidate validation mutually
exclusive:

```yaml
    - name: Initialize harness mode markers
      ansible.builtin.set_fact:
        normal_mode_entered: false
        direct_mode_entered: false
        old_hub_fixture_seeded_by_harness: false
        normal_backup_schedule_preseeded: false
        normal_mch_preseeded: false
        normal_secondary_restores_preseeded: false
        normal_role_completed: false
        old_hub_seed_register_behavior_reached: false
        old_hub_authoritative_defined_before_normal: false

    - name: Seed old-hub fixture as a runtime fact
      ansible.builtin.set_fact:
        _old_hub_existing_restore_info: "{{ acm_test_old_hub_fixture }}"
        old_hub_fixture_seeded_by_harness: true
      when: acm_test_old_hub_fixture is defined

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
              OLD_HUB_FIXTURE_SEEDED_BY_HARNESS={{ old_hub_fixture_seeded_by_harness }}
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
        variables["acm_test_old_hub_fixture"] = old_hub_fixture

    assert "_old_hub_existing_restore_info" not in variables
    return variables


def _execute_normal_vars(
    tmp_path,
    primary_hub,
    secondary_hub,
    *,
    old_hub_fixture=_UNSET,
    secondary_restore_preseed=_UNSET,
    mch_preseed=_UNSET,
):
    assert primary_hub is not secondary_hub
    assert primary_hub.url != secondary_hub.url

    primary_kubeconfig = tmp_path / "primary.kubeconfig"
    secondary_kubeconfig = tmp_path / "secondary.kubeconfig"

    write_kubeconfig(
        primary_kubeconfig,
        context="primary",
        server=primary_hub.url,
    )
    write_kubeconfig(
        secondary_kubeconfig,
        context="secondary",
        server=secondary_hub.url,
    )

    variables = _base_old_hub_vars(
        "execute",
        primary_kubeconfig,
        secondary_kubeconfig,
    )
    variables["acm_finalization_backup_schedules_info"] = {"resources": []}
    if old_hub_fixture is not _UNSET:
        variables["acm_test_old_hub_fixture"] = old_hub_fixture
    if secondary_restore_preseed is not _UNSET:
        variables["acm_finalization_restores_info"] = {
            "resources": secondary_restore_preseed,
        }
    if mch_preseed is not _UNSET:
        variables["acm_finalization_mch_info"] = {
            "resources": mch_preseed,
        }

    assert "_old_hub_existing_restore_info" not in variables
    return variables


def _direct_candidate_vars(
    tmp_path,
    primary_hub,
    secondary_hub,
    candidate,
):
    assert primary_hub is not secondary_hub
    assert primary_hub.url != secondary_hub.url

    primary_kubeconfig = tmp_path / "primary-direct.kubeconfig"
    secondary_kubeconfig = tmp_path / "secondary-direct.kubeconfig"
    write_kubeconfig(
        primary_kubeconfig,
        context="primary",
        server=primary_hub.url,
    )
    write_kubeconfig(
        secondary_kubeconfig,
        context="secondary",
        server=secondary_hub.url,
    )

    variables = _base_old_hub_vars(
        "dry_run",
        primary_kubeconfig,
        secondary_kubeconfig,
    )
    variables["acm_test_direct_old_hub_candidate"] = candidate
    return variables


def _assert_runtime_fixture_input(variables):
    """Prove normal-mode fixtures enter through the neutral -e name only."""
    assert "_old_hub_existing_restore_info" not in variables
    assert "acm_test_old_hub_fixture" in variables
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

The dry-run no-fixture builder leaves both
`acm_test_old_hub_fixture` and `_old_hub_existing_restore_info` undefined. The
fixture variant adds only `acm_test_old_hub_fixture` to those common pre-seeds,
using `_canonical_old_hub_fixture()`. The harness copies that neutral input to
`_old_hub_existing_restore_info` with a normal runtime `set_fact` before the
normal role include. The runtime fact and the production file's later skipped
same-name `register` therefore occupy the intended same-precedence,
later-assignment defect path. No normal-mode scenario may pass
`_old_hub_existing_restore_info` through `-e`.

The complete scenario matrix is:

| Scenario | BackupSchedule pre-seed | MCH pre-seed | Secondary Restore pre-seed | Old-hub fixture | Required fake routes |
| ---- | ---- | ---- | ---- | ---- | ---- |
| Dry-run normal, no fixture | Empty | Empty | Empty | Undefined | None |
| Dry-run normal, runtime fixture | Empty | Empty | Empty | Neutral `acm_test_old_hub_fixture`; harness runtime `set_fact` | None |
| Execute normal, runtime fixture; secondary list unrelated | Empty | Optional; never suppresses refresh | Empty | Neutral `acm_test_old_hub_fixture`; harness runtime `set_fact` | MCH LIST; no old-hub named GET |
| Execute normal, runtime fixture; assert secondary list | Empty | Optional; never suppresses refresh | Undefined | Neutral `acm_test_old_hub_fixture`; harness runtime `set_fact` | MCH LIST + secondary Restore LIST; no old-hub named GET |
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
  `secondary_hub.secondary_restore_list_hits >= 1`;
- pass `old_hub_fixture=_canonical_old_hub_fixture()`, call
  `_assert_runtime_fixture_input(variables)`, and require
  both hubs' old-hub named-GET counters to remain zero;
- require MCH live refresh even if `mch_preseed` was supplied;
- require the runtime fixture to remain authoritative after candidate
  validation and publication.

For execute mode without an old-hub fixture, call
`_execute_normal_vars(tmp_path, primary_hub, secondary_hub)` with neither
`old_hub_fixture` nor `secondary_restore_preseed`. This always pre-seeds only
the unrelated BackupSchedule source and leaves both
`acm_finalization_restores_info` and `_old_hub_existing_restore_info`
undefined. It must reach execute-mode MCH refresh, the secondary Restore list,
and the old-hub named Restore GET.

The absent variables for that scenario are exactly:

```text
acm_finalization_restores_info
acm_test_old_hub_fixture
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

    with (
        FakeAcmBackupHub(
            restores=[
                restore_fixture(
                    "restore-acm-passive-sync",
                    resource_version="52",
                    status={"phase": "Finished"},
                )
            ],
            multiclusterhubs=[],
        ) as primary_hub,
        FakeAcmBackupHub(
            restores=[],
            multiclusterhubs=[
                multiclusterhub_fixture(resource_version="53")
            ],
        ) as secondary_hub,
    ):
        execute_live = _execute_normal_vars(
            tmp_path,
            primary_hub,
            secondary_hub,
        )
        assert execute_live["acm_finalization_backup_schedules_info"] == {
            "resources": []
        }
        assert "acm_finalization_restores_info" not in execute_live
        assert "acm_test_old_hub_fixture" not in execute_live
        assert "_old_hub_existing_restore_info" not in execute_live

        direct = _direct_candidate_vars(
            tmp_path,
            primary_hub,
            secondary_hub,
            {"resources": []},
        )
        assert "acm_finalization_backup_schedules_info" not in direct
        assert "_old_hub_existing_restore_info" not in direct
        assert primary_hub.request_count == 0
        assert secondary_hub.request_count == 0


def _assert_normal_prerequisites_neutralized(
    result,
    *,
    authoritative_defined_before_normal,
    fixture_seeded_by_harness,
):
    """Prove a red result reached R3-A3 rather than an unrelated prerequisite."""
    assert result.returncode == 0, result.stdout + result.stderr
    assert "NORMAL_MODE_ENTERED=True" in result.stdout, result.stdout
    assert "DIRECT_MODE_ENTERED=False" in result.stdout, result.stdout
    expected_fixture_marker = (
        "OLD_HUB_FIXTURE_SEEDED_BY_HARNESS="
        f"{fixture_seeded_by_harness}"
    )
    assert expected_fixture_marker in result.stdout, result.stdout
    assert "NORMAL_BACKUP_SCHEDULE_PRESEEDED=True" in result.stdout, result.stdout
    assert "NORMAL_MCH_PRESEEDED=True" in result.stdout, result.stdout
    assert "NORMAL_SECONDARY_RESTORES_PRESEEDED=True" in result.stdout, result.stdout
    assert "NORMAL_ROLE_COMPLETED=True" in result.stdout, result.stdout
    assert "OLD_HUB_SEED_REGISTER_REACHED=True" in result.stdout, result.stdout
    expected = f"OLD_HUB_AUTHORITATIVE_DEFINED_BEFORE_NORMAL={authoritative_defined_before_normal}"
    assert expected in result.stdout, result.stdout


def test_dry_run_seed_survives(tmp_path):
    variables = _dry_run_normal_vars()
    assert "acm_test_old_hub_fixture" not in variables
    assert "_old_hub_existing_restore_info" not in variables
    r = _run(tmp_path, variables)
    _assert_normal_prerequisites_neutralized(
        r,
        authoritative_defined_before_normal=False,
        fixture_seeded_by_harness=False,
    )
    assert "OLD_HUB_RESULT_SKIPPED=False" in r.stdout, r.stdout
    assert "OLD_HUB_HAS_RESOURCES_KEY=True" in r.stdout, r.stdout
    assert "OLD_HUB_COUNT=0" in r.stdout, r.stdout

def test_injected_fixture_preserved(tmp_path):
    variables = _dry_run_normal_vars(
        old_hub_fixture=_canonical_old_hub_fixture(),
    )
    assert "_old_hub_existing_restore_info" not in variables
    assert "acm_test_old_hub_fixture" in variables
    _assert_runtime_fixture_input(variables)
    r = _run(tmp_path, variables)
    _assert_normal_prerequisites_neutralized(
        r,
        authoritative_defined_before_normal=True,
        fixture_seeded_by_harness=True,
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

Expected: both tests fail on their first post-fix expectation,
`assert "OLD_HUB_RESULT_SKIPPED=False" in r.stdout`. The valid runtime-fixture
test first proves its `-e` mapping contains `acm_test_old_hub_fixture` and does
not contain `_old_hub_existing_restore_info`; its captured pre-fix output must
show all of:

```text
NORMAL_MODE_ENTERED=True
DIRECT_MODE_ENTERED=False
OLD_HUB_FIXTURE_SEEDED_BY_HARNESS=True
OLD_HUB_AUTHORITATIVE_DEFINED_BEFORE_NORMAL=True
OLD_HUB_SEED_REGISTER_REACHED=True
OLD_HUB_RESULT_SKIPPED=True
OLD_HUB_HAS_RESOURCES_KEY=False
```

Both cases must also retain the prerequisite markers
`NORMAL_BACKUP_SCHEDULE_PRESEEDED=True`, `NORMAL_MCH_PRESEEDED=True`,
`NORMAL_SECONDARY_RESTORES_PRESEEDED=True`, and
`NORMAL_ROLE_COMPLETED=True`. The no-fixture case remains otherwise unchanged
and must report `OLD_HUB_FIXTURE_SEEDED_BY_HARNESS=False`,
`OLD_HUB_AUTHORITATIVE_DEFINED_BEFORE_NORMAL=False`,
`OLD_HUB_RESULT_SKIPPED=True`, and `OLD_HUB_HAS_RESOURCES_KEY=False`.
`returncode == 0` from the harness plus `NORMAL_ROLE_COMPLETED=True` and
`OLD_HUB_SEED_REGISTER_REACHED=True` is mandatory defect-specific evidence
that kubeconfig/API discovery, BackupSchedule, MCH, and secondary Restore
prerequisites did not fail. Do not accept a red result caused by a missing
playbook, kubeconfig, API route, or any unrelated discovery task. The current
clobber must remain the first failed test expectation.

**Step 4: Fix the old-hub Restore block with explicit candidate ownership and validate-before-publish flow (C1)**

Ownership must be explicit and preserved in this order:

- test transport owner: `acm_test_old_hub_fixture` (extra-variable input to the
  harness only; never read by the production role)
- runtime fixture owner: `_old_hub_existing_restore_info` (set by the harness
  through normal runtime `set_fact` before role execution)
- raw live-query owner: `_old_hub_existing_restore_live_info`
- temporary selected candidate: `_old_hub_existing_restore_candidate`
- authoritative downstream owner: `_old_hub_existing_restore_info` (published only after candidate validation)

Required mode-specific flow:

1. Dry-run + fixture supplied: copy fixture into candidate, skip live query, validate candidate, publish authoritative value from validated candidate.
2. Dry-run + no fixture: seed candidate with `{resources: []}`, skip live query, validate candidate, publish authoritative value.
3. Execute mode + fixture supplied: use fixture as candidate, skip live query, validate candidate, publish authoritative value.
4. Execute mode + no fixture: query live into `_old_hub_existing_restore_live_info`, fail on skipped/failed query shape, assign candidate from live result, validate candidate, publish authoritative value.

The test harness intentionally assigns even malformed fixture payloads to
`_old_hub_existing_restore_info` before the production role so those cases
exercise the required runtime-fact precedence path. Outside that bounded
test-only seed, no production task may publish or reassign malformed, skipped,
missing, or non-list data to `_old_hub_existing_restore_info`; production
publication remains strictly after validation.

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

Expected: PASS (the scenario-builder contract and both behavior tests). The
valid runtime-fixture case must now report:

```text
OLD_HUB_RESULT_SKIPPED=False
OLD_HUB_HAS_RESOURCES_KEY=True
OLD_HUB_COUNT=1
```

The no-fixture case must report
`OLD_HUB_FIXTURE_SEEDED_BY_HARNESS=False`,
`OLD_HUB_RESULT_SKIPPED=False`, `OLD_HUB_HAS_RESOURCES_KEY=True`, and
`OLD_HUB_COUNT=0`.

**Step 6: Add execute-mode fake-API + fatal + negative tests (items 9, 10)**

B14 makes the required fixture and route cases concrete rather than relying on
prose names. Add these bodies to the same Task 6 test module. Together with
`test_injected_fixture_preserved` from Step 2, the only functions that pass an
`old_hub_fixture` keyword to a normal builder are the exact four-function
manifest enforced by Task 11's AST gate:

```python
def test_execute_mode_runtime_fixture_preserved_without_secondary_restore_list(
    tmp_path,
):
    with (
        FakeAcmBackupHub(
            restores=[
                restore_fixture(
                    "restore-acm-passive-sync",
                    resource_version="61",
                    status={"phase": "Finished"},
                )
            ],
            multiclusterhubs=[],
        ) as primary_hub,
        FakeAcmBackupHub(
            restores=[
                restore_fixture(
                    "restore-acm-full",
                    resource_version="62",
                )
            ],
            multiclusterhubs=[
                multiclusterhub_fixture(resource_version="63")
            ],
        ) as secondary_hub,
    ):
        variables = _execute_normal_vars(
            tmp_path,
            primary_hub,
            secondary_hub,
            old_hub_fixture=_canonical_old_hub_fixture("64"),
            secondary_restore_preseed=[],
        )
        _assert_runtime_fixture_input(variables)

        result = _run(tmp_path, variables)

        assert result.returncode == 0, result.stdout + result.stderr
        assert "OLD_HUB_RESULT_SKIPPED=False" in result.stdout
        assert "OLD_HUB_HAS_RESOURCES_KEY=True" in result.stdout
        assert "OLD_HUB_COUNT=1" in result.stdout
        assert primary_hub.old_hub_restore_named_get_hits == {}
        assert secondary_hub.old_hub_restore_named_get_hits == {}
        assert secondary_hub.secondary_mch_list_hits >= 1
        assert secondary_hub.secondary_restore_list_hits == 0
        assert primary_hub.secondary_mch_list_hits == 0
        assert primary_hub.secondary_restore_list_hits == 0


def test_execute_mode_runtime_fixture_preserved_with_secondary_restore_list(
    tmp_path,
):
    with (
        FakeAcmBackupHub(
            restores=[
                restore_fixture(
                    "restore-acm-passive-sync",
                    resource_version="65",
                    status={"phase": "Finished"},
                )
            ],
            multiclusterhubs=[],
        ) as primary_hub,
        FakeAcmBackupHub(
            restores=[
                restore_fixture(
                    "restore-acm-full",
                    resource_version="66",
                )
            ],
            multiclusterhubs=[
                multiclusterhub_fixture(resource_version="67")
            ],
        ) as secondary_hub,
    ):
        variables = _execute_normal_vars(
            tmp_path,
            primary_hub,
            secondary_hub,
            old_hub_fixture=_canonical_old_hub_fixture("68"),
        )
        _assert_runtime_fixture_input(variables)

        result = _run(tmp_path, variables)

        assert result.returncode == 0, result.stdout + result.stderr
        assert "OLD_HUB_RESULT_SKIPPED=False" in result.stdout
        assert "OLD_HUB_HAS_RESOURCES_KEY=True" in result.stdout
        assert "OLD_HUB_COUNT=1" in result.stdout
        assert primary_hub.old_hub_restore_named_get_hits == {}
        assert secondary_hub.old_hub_restore_named_get_hits == {}
        assert secondary_hub.secondary_mch_list_hits >= 1
        assert secondary_hub.secondary_restore_list_hits >= 1
        assert primary_hub.secondary_mch_list_hits == 0
        assert primary_hub.secondary_restore_list_hits == 0


@pytest.mark.parametrize(
    ("payload", "case_id"),
    [
        ("not-a-mapping", "top-level-non-mapping"),
        ({}, "missing-resources"),
        (
            {"changed": False, "skipped": True},
            "skipped-result",
        ),
        ({"resources": {}}, "empty-mapping-resources"),
        (
            {"resources": {"unexpected": "value"}},
            "nonempty-mapping-resources",
        ),
        ({"resources": "text"}, "string-resources"),
        ({"resources": 1}, "number-resources"),
        ({"resources": None}, "null-resources"),
        (
            {"resources": ["not-a-mapping"]},
            "malformed-list-entry",
        ),
    ],
)
def test_runtime_fixture_fails_closed_on_bad_shape(
    tmp_path,
    payload,
    case_id,
):
    variables = _dry_run_normal_vars(
        old_hub_fixture=payload,
    )
    _assert_runtime_fixture_input(variables)

    result = _run(tmp_path, variables)

    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert (
        "refusing to treat it as an empty successful result"
        in combined
        or "refusing to classify malformed data" in combined
    )
```

The non-fixture and failure route tests are equally concrete and use two
physical fake servers:

```python
def test_execute_mode_live_routes_to_physical_hubs(tmp_path):
    with (
        FakeAcmBackupHub(
            restores=[
                restore_fixture(
                    "restore-acm-passive-sync",
                    resource_version="69",
                    status={"phase": "Finished"},
                )
            ],
            multiclusterhubs=[],
        ) as primary_hub,
        FakeAcmBackupHub(
            restores=[
                restore_fixture(
                    "restore-acm-full",
                    resource_version="70",
                )
            ],
            multiclusterhubs=[
                multiclusterhub_fixture(resource_version="71")
            ],
        ) as secondary_hub,
    ):
        variables = _execute_normal_vars(
            tmp_path,
            primary_hub,
            secondary_hub,
        )
        result = _run(tmp_path, variables)

        assert result.returncode == 0, result.stdout + result.stderr
        assert "OLD_HUB_COUNT=1" in result.stdout
        assert (
            primary_hub.old_hub_restore_named_get_hits.get(
                "restore-acm-passive-sync",
                0,
            )
            == 1
        )
        assert primary_hub.secondary_restore_list_hits == 0
        assert primary_hub.secondary_mch_list_hits == 0
        assert secondary_hub.secondary_mch_list_hits >= 1
        assert secondary_hub.secondary_restore_list_hits >= 1
        assert (
            secondary_hub.old_hub_restore_named_get_hits.get(
                "restore-acm-passive-sync",
                0,
            )
            == 0
        )


def test_execute_mode_old_hub_get_failure_is_primary_only(tmp_path):
    with (
        FakeAcmBackupHub(
            restores=[
                restore_fixture(
                    "restore-acm-passive-sync",
                    resource_version="72",
                    status={"phase": "Finished"},
                )
            ],
            multiclusterhubs=[],
            get_failures={"restore-acm-passive-sync": True},
        ) as primary_hub,
        FakeAcmBackupHub(
            restores=[
                restore_fixture(
                    "restore-acm-full",
                    resource_version="73",
                )
            ],
            multiclusterhubs=[
                multiclusterhub_fixture(resource_version="74")
            ],
        ) as secondary_hub,
    ):
        variables = _execute_normal_vars(
            tmp_path,
            primary_hub,
            secondary_hub,
        )
        result = _run(tmp_path, variables)

        assert result.returncode != 0
        assert (
            primary_hub.old_hub_restore_named_get_hits.get(
                "restore-acm-passive-sync",
                0,
            )
            >= 1
        )
        assert (
            secondary_hub.old_hub_restore_named_get_hits.get(
                "restore-acm-passive-sync",
                0,
            )
            == 0
        )
        assert secondary_hub.secondary_mch_list_hits >= 1
        assert secondary_hub.secondary_restore_list_hits >= 1
        assert primary_hub.secondary_mch_list_hits == 0
        assert primary_hub.secondary_restore_list_hits == 0
        assert primary_hub.secondary_restore_delete_hits == 0
        assert secondary_hub.secondary_restore_delete_hits == 0
        assert (
            sum(primary_hub.secondary_restore_named_delete_hits.values())
            == 0
        )
        assert (
            sum(secondary_hub.secondary_restore_named_delete_hits.values())
            == 0
        )
        assert "FAKE_RESTORE_GET_FAILURE:restore-acm-passive-sync" in (
            result.stdout + result.stderr
        )
```

- **Dry-run no fixture succeeds with deliberate absence:** use
  `_dry_run_normal_vars()`; assert all three unrelated pre-seed markers,
  `OLD_HUB_FIXTURE_SEEDED_BY_HARNESS=False`,
  `OLD_HUB_HAS_RESOURCES_KEY=True`, `OLD_HUB_COUNT=0`, and that
  both `acm_test_old_hub_fixture` and `_old_hub_existing_restore_info` were
  absent from the input variables.
- **Dry-run with valid runtime fixture preserves fixture:** use
  `_dry_run_normal_vars(old_hub_fixture=_canonical_old_hub_fixture())`; assert
  `_old_hub_existing_restore_info` is absent from the input mapping,
  `acm_test_old_hub_fixture` is present, and
  `_assert_runtime_fixture_input(variables)` passes. Require
  `OLD_HUB_FIXTURE_SEEDED_BY_HARNESS=True`,
  `OLD_HUB_AUTHORITATIVE_DEFINED_BEFORE_NORMAL=True`,
  `OLD_HUB_RESULT_SKIPPED=False`, `OLD_HUB_HAS_RESOURCES_KEY=True`, and
  `OLD_HUB_COUNT=1`.
- **Execute mode with valid runtime fixture preserves fixture and skips only
  the old-hub named GET:** use
  `_execute_normal_vars(tmp_path, primary_hub, secondary_hub,
  old_hub_fixture=_canonical_old_hub_fixture(),
  secondary_restore_preseed=[])` when the secondary list is unrelated. Require
  `_old_hub_existing_restore_info` absent from the input mapping,
  `acm_test_old_hub_fixture` present, and
  `_assert_runtime_fixture_input(variables)` to pass. Require
  `secondary_hub.secondary_mch_list_hits >= 1` despite any optional MCH
  pre-seed, `secondary_hub.secondary_restore_list_hits == 0`, and zero
  old-hub named GETs on both hubs; after candidate validation and publication,
  require `OLD_HUB_RESULT_SKIPPED=False`,
  `OLD_HUB_HAS_RESOURCES_KEY=True`, and `OLD_HUB_COUNT=1`. In a separate test
  that intentionally asserts the secondary Restore-list route, omit
  `secondary_restore_preseed` and require
  `secondary_hub.secondary_restore_list_hits >= 1` while still requiring
  zero old-hub named GETs on both hubs; the same input-absence and authoritative
  publication assertions are mandatory.
- **Execute mode without fixture publishes successful fake-live data:** call
  `_execute_normal_vars(tmp_path, primary_hub, secondary_hub)` without
  `acm_finalization_restores_info`, `acm_test_old_hub_fixture`, or
  `_old_hub_existing_restore_info`.
  Require `OLD_HUB_COUNT=1`, positive MCH/Restore LIST counters only on
  `secondary_hub`, exactly one old-hub named GET only on `primary_hub`, and
  zero corresponding counters on the wrong hub.
- **Malformed fixture matrix:** the exact parameterized body above covers all
  nine required case IDs and calls the neutral-input helper before execution.
- **Live old-hub named-GET failure is fatal and positively attributed:**
  configure the GET failure only on `primary_hub`, run through the concrete
  two-server test above, and require its exact cross-hub assertions.

  The positive named-GET counter proves the intended failing route was
  reached; the prerequisite MCH/Restore list counters prove normal discovery
  reached the failure in order; zero DELETE counters prove no later mutation;
  and the stable marker proves the observed nonzero exit came from the
  injected route. A nonzero subprocess exit alone is insufficient.
- **Builder use is mandatory:** every malformed/skipped/missing-resources
  dry-run normal test passes its payload as
  `old_hub_fixture` to `_dry_run_normal_vars`; every execute normal success or
  failure test uses `_execute_normal_vars`. No normal-mode test calls
  `_base_old_hub_vars` directly or manually assembles variables, so the
  BackupSchedule pre-seed cannot be omitted accidentally. Every normal-mode
  test that supplies `old_hub_fixture` must call
  `_assert_runtime_fixture_input(variables)` before execution. This applies to
  the valid dry-run fixture, both valid execute fixture route variants,
  malformed mapping, skipped-shaped mapping, missing `resources`,
  mapping/string/number/null `resources`, and malformed list-entry scenarios.
  No normal-mode scenario may pass `_old_hub_existing_restore_info` through
  `-e`; both normal builders assert that the authoritative name is absent.
- **Normal-mode observability:** every normal-mode test asserts
  `NORMAL_MODE_ENTERED=True` and `DIRECT_MODE_ENTERED=False`; the execute
  no-fixture case additionally proves the role ran by asserting its expected
  fake API counters are non-zero. Every normal-mode variable mapping contains
  `acm_finalization_backup_schedules_info: {resources: []}` and every
  normal-mode result reports
  `NORMAL_BACKUP_SCHEDULE_PRESEEDED=True`. Every normal fixture result reports
  `OLD_HUB_FIXTURE_SEEDED_BY_HARNESS=True`; every normal no-fixture result
  reports `OLD_HUB_FIXTURE_SEEDED_BY_HARNESS=False`.
- **Canonical execute fixtures:** import `restore_fixture` and
  `multiclusterhub_fixture` from `finalization_fake_api`. Build the valid
  neutral old-hub fixture through `_canonical_old_hub_fixture()`. Build the
  no-preseed live response with `restore_fixture`, and run valid execute
  scenarios with
  `FakeAcmBackupHub(restores=[...],
  multiclusterhubs=[multiclusterhub_fixture(resource_version="60")])`.
  Do not hand-write partial valid Restore or MCH mappings in the execute-mode
  builders. Malformed fixture cases remain deliberately raw payloads under
  `acm_test_old_hub_fixture` because they test the validator before
  publication and never become fake API resources.
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
    with (
        FakeAcmBackupHub(restores=[], multiclusterhubs=[]) as primary_hub,
        FakeAcmBackupHub(restores=[], multiclusterhubs=[]) as secondary_hub,
    ):
        v = _direct_candidate_vars(
            tmp_path,
            primary_hub,
            secondary_hub,
            malformed_candidate,
        )
        assert "acm_finalization_backup_schedules_info" not in v
        assert "_old_hub_existing_restore_info" not in v
        r = _run(tmp_path, v)
        assert primary_hub.request_count == 0
        assert secondary_hub.request_count == 0
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

**Step 7: Add static runtime-seeding and direct-harness contracts (B7-C2)**

Parse `finalization_old_hub_restore_discovery.yml` in
`test_finalization_old_hub_restore_runtime.py` or
`test_discover_resources_contracts.py` and assert:

```python
def test_direct_harness_is_role_relative_and_validates_before_publication():
    text = OLD_HUB_HARNESS.read_text(encoding="utf-8")
    assert "COLLECTION_ROOT" not in text
    tasks = yaml.safe_load(text)
    top_tasks = tasks[0]["tasks"]
    flat = [task for task in _flatten_tasks(top_tasks) if isinstance(task, dict)]

    initialize = next(
        task for task in top_tasks
        if task.get("name") == "Initialize harness mode markers"
    )
    assert (
        initialize["ansible.builtin.set_fact"][
            "old_hub_fixture_seeded_by_harness"
        ]
        is False
    )

    seed_tasks = [
        task for task in top_tasks
        if (
            task.get("ansible.builtin.set_fact", {}).get(
                "_old_hub_existing_restore_info"
            )
            == "{{ acm_test_old_hub_fixture }}"
        )
    ]
    assert len(seed_tasks) == 1
    seed = seed_tasks[0]
    assert seed["ansible.builtin.set_fact"][
        "old_hub_fixture_seeded_by_harness"
    ] is True
    assert "acm_test_old_hub_fixture is defined" in str(seed.get("when", ""))

    normal = next(
        task for task in top_tasks
        if task.get("name") == "Run normal finalization discovery"
    )
    assert top_tasks.index(seed) < top_tasks.index(normal)
    normal_flat = [
        task for task in _flatten_tasks(normal["block"])
        if isinstance(task, dict)
    ]
    normal_role_idx = next(
        index for index, task in enumerate(normal_flat)
        if (task.get("ansible.builtin.include_role") or {}).get("tasks_from")
        == "discover_resources"
    )
    assert normal_role_idx >= 0
    assert "OLD_HUB_FIXTURE_SEEDED_BY_HARNESS" in text

    direct = next(task for task in top_tasks if task.get("name", "").startswith("Direct candidate test mode"))
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
`NORMAL_MODE_ENTERED=True` when exercised. The seed contract proves there is
exactly one normal runtime `set_fact` copying `acm_test_old_hub_fixture` to
`_old_hub_existing_restore_info`, it precedes the normal role include, and the
observability marker is initialized and emitted. This static contract and the
runtime zero-request/marker contract are both required.

**Preferred malformed-output coverage boundary (B6 split):**
- Live fake-HTTP cases are limited to valid success paths and explicit API failures (e.g., HTTP 500).
- Malformed source-shape coverage is proven by the shared shape-validator harness, malformed fixture-injection tests, and the direct candidate-validation/publication harness with simulated module-result mappings.
- Do **not** treat Kubernetes client/module rejection of malformed HTTP payloads as evidence for the R3-A3 validator boundary.
- A test that fails during discovery or MCH refresh does **not** prove R3-A3 candidate/validator behavior.

**Step 8: Static production contract**

The base test
`test_dry_run_seeds_empty_old_hub_restore` is stale because it requires the
dry-run seed to write the authoritative
`_old_hub_existing_restore_info` directly. Deliberately replace it; do not
leave it beside the new contract under its old name:

```python
import pytest
import yaml

from yaml_contract_helpers import _flatten_tasks, _when_text


def test_dry_run_seeds_empty_old_hub_restore_candidate():
    tasks = yaml.safe_load(FINALIZATION_DISCOVER.read_text()) or []

    seed = next(
        task
        for task in tasks
        if task.get("name")
        == "Select old-hub passive restore candidate for dry-run without fixture"
    )

    facts = seed["ansible.builtin.set_fact"]
    assert facts["_old_hub_existing_restore_candidate"] == {
        "resources": []
    }
    assert "_old_hub_existing_restore_info" not in facts

    when = _when_text(seed)
    assert "dry_run" in when
    assert "_old_hub_existing_restore_info is not defined" in when


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

The production ordering test is the authoritative-publication contract:
publication is a distinct task, reads
`_old_hub_existing_restore_candidate`, never reads the live register directly,
and occurs after the single validation include. The synthetic four-form matrix
keeps that ordering helper honest.

Keep `_include_tasks_target` and
`_assert_old_hub_restore_validate_before_publish` at module scope beside the
production test, with the file's existing `pytest`, `yaml`, and
`_flatten_tasks`/`_when_text` imports. The production assertion must see exactly one
old-hub validation include. The synthetic parameter matrix proves both module
keys and both supported module-argument shapes reach the same helper and that
moving the validator after authoritative publication still fails.

**Compatibility audit of every current collection-test reference (B11-C2):**

The pre-implementation search is:

```bash
rg -n \
  '_old_hub_existing_restore_info|_old_hub_existing_restore_candidate|_old_hub_existing_restore_live_info|cleanup_restores\.yml|discover_resources\.yml' \
  ansible_collections/tomazb/acm_switchover/tests
```

Record these source-validated dispositions:

| Existing test | Disposition |
| ---- | ---- |
| `tests/unit/test_discover_resources_contracts.py` | **Affected and modified in Task 6.** Replace the stale authoritative-seed test with `test_dry_run_seeds_empty_old_hub_restore_candidate`; retain hub-routing/execute-mode contracts; add the single-validation candidate-before-publication contract. |
| `tests/unit/test_finalization_verification.py` | **Affected and modified in Task 5.** Retain unexpected-resource/delete-order safety assertions and add the cleanup candidate/live/authoritative ownership contract specified by Task 5. |
| `tests/unit/test_ansible_resilience_contracts.py` | **Compatible; no edit.** Its finalization assertions require phase-local `acm_finalization_*` inputs and forbid those public facts as direct register targets; the distinct live/candidate names preserve both assumptions. |
| `tests/unit/test_preflight_parity.py` | **Out of scope; no edit.** Its `discover_resources.yml` references are preflight-only; issue #202 remains separate/open. |
| `tests/unit/test_restore_only_recovery_contracts.py` | **Out of scope; no edit.** Its discovery reference is preflight-only and remains governed by issue #202. |
| `tests/unit/test_primary_prep_auto_import.py` | **Unaffected; no edit.** It reads only primary-prep discovery. |
| `tests/unit/test_activation_auto_import.py` | **Unaffected; no edit.** It checks finalization include ordering, not old-hub Restore fact ownership. |

Re-run the search after implementation and fail review if a new affected
assumption is unclassified.

**Step 9: Run targeted unit reconciliation + Task 6 runtime, then commit**

Run the required existing-contract reconciliation:

```bash
python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_discover_resources_contracts.py \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_finalization_verification.py \
  -q
```

Then run:

```bash
python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/integration/test_finalization_old_hub_restore_runtime.py \
  -q
```

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
- Create: `ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/collisions/tasks_collision.yml`
- Create: `ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/collisions/handlers_collision.yml`
- Create: `ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/collisions/playbook_collision.yml`
- Create: `ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/collisions/clean.yml`
- Create: `ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/collisions/playbook_pre_tasks_block_collision.yml`
- Create: `ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/collisions/playbook_tasks_rescue_collision.yml`
- Create: `ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/collisions/playbook_post_tasks_always_collision.yml`
- Create: `ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/collisions/playbook_handlers_block_collision.yml`
- Create: `ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/collisions/dynamic_register.yml`
- Create: `ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/collisions/dynamic_setfact.yml`
- Create: `ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/collisions/broken.yml`
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
import pathlib
from collections import Counter

import pytest
import register_collision_scan
import yaml

from register_collision_scan import CollisionScanError, scan_boundary, scan_file

FIX = pathlib.Path(__file__).resolve().parent / "fixtures" / "collisions"


def _write_yaml(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(payload, sort_keys=False),
        encoding="utf-8",
    )


def _collision_tasks(variable):
    return [
        {
            "name": "Register value",
            "ansible.builtin.debug": {"msg": "fixture"},
            "register": variable,
        },
        {
            "name": "Publish value",
            "ansible.builtin.set_fact": {variable: "fixture"},
        },
    ]


def test_detects_task_collision():      assert scan_file(FIX/"tasks_collision.yml", is_playbook=False) == ["x"]
def test_detects_handler_collision():   assert scan_file(FIX/"handlers_collision.yml", is_playbook=False) == ["x"]
def test_detects_playbook_post_tasks(): assert scan_file(FIX/"playbook_collision.yml", is_playbook=True) == ["x"]
def test_detects_pre_tasks_block_collision(): assert scan_file(FIX/"playbook_pre_tasks_block_collision.yml", is_playbook=True) == ["x"]
def test_detects_tasks_rescue_collision():    assert scan_file(FIX/"playbook_tasks_rescue_collision.yml", is_playbook=True) == ["x"]
def test_detects_post_tasks_always_collision(): assert scan_file(FIX/"playbook_post_tasks_always_collision.yml", is_playbook=True) == ["x"]
def test_detects_handlers_block_collision():  assert scan_file(FIX/"playbook_handlers_block_collision.yml", is_playbook=True) == ["x"]


def test_scan_boundary_includes_nested_role_tasks_and_handlers(tmp_path):
    collection_root = tmp_path / "collection"
    task_path = collection_root / "roles/example/tasks/nested/main.yml"
    handler_path = collection_root / "roles/example/handlers/nested/main.yml"
    _write_yaml(task_path, _collision_tasks("nested_task_value"))
    _write_yaml(handler_path, _collision_tasks("nested_handler_value"))

    found = scan_boundary(collection_root)

    assert (
        "roles/example/tasks/nested/main.yml",
        "nested_task_value",
    ) in found
    assert (
        "roles/example/handlers/nested/main.yml",
        "nested_handler_value",
    ) in found


def test_scan_boundary_includes_nested_playbooks(tmp_path):
    collection_root = tmp_path / "collection"
    playbook_path = (
        collection_root / "playbooks/nested/deeper/example.yml"
    )
    _write_yaml(
        playbook_path,
        [
            {
                "name": "Nested play",
                "hosts": "localhost",
                "tasks": [
                    {
                        "name": "Nested collision block",
                        "block": _collision_tasks("nested_playbook_value"),
                    }
                ],
            }
        ],
    )

    assert scan_boundary(collection_root) == {
        (
            "playbooks/nested/deeper/example.yml",
            "nested_playbook_value",
        )
    }


def test_scan_boundary_has_no_duplicate_file_scans(tmp_path, monkeypatch):
    collection_root = tmp_path / "collection"
    expected = [
        collection_root / "roles/example/tasks/nested/main.yml",
        collection_root / "roles/example/handlers/nested/main.yml",
        collection_root / "playbooks/nested/deeper/example.yml",
    ]
    _write_yaml(expected[0], _collision_tasks("nested_task_value"))
    _write_yaml(expected[1], _collision_tasks("nested_handler_value"))
    _write_yaml(
        expected[2],
        [
            {
                "name": "Nested play",
                "hosts": "localhost",
                "tasks": _collision_tasks("nested_playbook_value"),
            }
        ],
    )

    calls = []
    real_scan_file = register_collision_scan.scan_file

    def recording_scan_file(path, *, is_playbook):
        calls.append(path)
        return real_scan_file(path, is_playbook=is_playbook)

    monkeypatch.setattr(
        register_collision_scan,
        "scan_file",
        recording_scan_file,
    )
    register_collision_scan.scan_boundary(collection_root)

    assert len(calls) == len(set(calls))
    counts = Counter(calls)
    for path in expected:
        assert counts[path] == 1


def test_scan_boundary_paths_are_collection_relative(tmp_path):
    collection_root = tmp_path / "collection"
    task_path = collection_root / "roles/example/tasks/nested/main.yml"
    handler_path = collection_root / "roles/example/handlers/nested/main.yml"
    playbook_path = (
        collection_root / "playbooks/nested/deeper/example.yml"
    )
    _write_yaml(task_path, _collision_tasks("nested_task_value"))
    _write_yaml(handler_path, _collision_tasks("nested_handler_value"))
    _write_yaml(
        playbook_path,
        [
            {
                "name": "Nested play",
                "hosts": "localhost",
                "tasks": _collision_tasks("nested_playbook_value"),
            }
        ],
    )

    found = scan_boundary(collection_root)
    assert found == {
        (
            "roles/example/tasks/nested/main.yml",
            "nested_task_value",
        ),
        (
            "roles/example/handlers/nested/main.yml",
            "nested_handler_value",
        ),
        (
            "playbooks/nested/deeper/example.yml",
            "nested_playbook_value",
        ),
    }
    for relative_path, _variable in found:
        assert not pathlib.Path(relative_path).is_absolute()
        assert str(collection_root) not in relative_path
        assert relative_path.startswith(("roles/", "playbooks/"))


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

The nested role-task, role-handler, and playbook boundary tests must create
their additional synthetic directory trees dynamically under pytest
`tmp_path`. The eleven repository fixtures listed in this task are exhaustive;
do not add any other repository fixture to exercise nested-boundary structure.

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
        ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/collisions/tasks_collision.yml \
        ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/collisions/handlers_collision.yml \
        ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/collisions/playbook_collision.yml \
        ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/collisions/clean.yml \
        ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/collisions/playbook_pre_tasks_block_collision.yml \
        ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/collisions/playbook_tasks_rescue_collision.yml \
        ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/collisions/playbook_post_tasks_always_collision.yml \
        ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/collisions/playbook_handlers_block_collision.yml \
        ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/collisions/dynamic_register.yml \
        ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/collisions/dynamic_setfact.yml \
        ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/collisions/broken.yml \
        ansible_collections/tomazb/acm_switchover/tests/unit/test_register_setfact_collision_guardrail.py
git commit -m "test(collection): literal-only register/set_fact collision scanner with dynamic-name and parse-error coverage"
```

---

## Task 8: Allowlist enforcement + strict metadata validation (automated malformed fixtures)

**Files:**
- Create: `ansible_collections/tomazb/acm_switchover/tests/unit/register_setfact_collision_allowlist.yml`
- Create: `ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/allowlists/missing_removal_condition.yml`
- Create: `ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/allowlists/empty_rationale.yml`
- Create: `ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/allowlists/unknown_category.yml`
- Create: `ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/allowlists/duplicate_pair.yml`
- Create: `ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/allowlists/cross_category_duplicate.yml`
- Create: `ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/allowlists/wrong_issue_ref.yml`

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
        ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/allowlists/missing_removal_condition.yml \
        ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/allowlists/empty_rationale.yml \
        ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/allowlists/unknown_category.yml \
        ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/allowlists/duplicate_pair.yml \
        ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/allowlists/cross_category_duplicate.yml \
        ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/allowlists/wrong_issue_ref.yml
git commit -m "test(collection): enforce collision guardrail with strict two-category allowlist validation (#202 debt)"
```

---

## Task 9: ansible-core 2.15.x skipped-register + variable-precedence verification gate

**Files:**
- Create: `ansible_collections/tomazb/acm_switchover/tests/integration/playbooks/register_skip_semantics.yml`
- Test: `ansible_collections/tomazb/acm_switchover/tests/integration/test_register_skip_semantics.py`

**Step 1: Committed reproduction playbook**

Keep the original skipped-register/defeated-seed proof and add two explicit
variable-precedence cases:

```yaml
---
- name: Verify skipped-register and variable-precedence semantics
  hosts: localhost
  connection: local
  gather_facts: false
  tasks:
    - name: Initialize original seed marker
      ansible.builtin.set_fact:
        seed_fired: false

    - name: Produce a skipped registered result for the original contract
      ansible.builtin.debug:
        msg: unreachable
      register: skipped_seed_target
      when: false

    - name: Attempt seed after skipped register
      ansible.builtin.set_fact:
        skipped_seed_target:
          resources: []
        seed_fired: true
      when: skipped_seed_target is not defined

    - name: Copy neutral extra-variable fixture to a runtime fact
      ansible.builtin.set_fact:
        runtime_fact_target: "{{ acm_test_runtime_fixture }}"

    - name: Clobber runtime fact with a later skipped same-name register
      ansible.builtin.debug:
        msg: unreachable
      register: runtime_fact_target
      when: false

    - name: Try to clobber an extra-variable target with a skipped register
      ansible.builtin.debug:
        msg: unreachable
      register: extra_var_target
      when: false

    - name: Report skipped-register and precedence outcomes
      ansible.builtin.debug:
        msg: >-
          SEED_FIRED={{ seed_fired }}
          HAS_RESOURCES_KEY={{ (skipped_seed_target is mapping) and ('resources' in skipped_seed_target) }}
          RUNTIME_FACT_CLOBBERED={{
            (runtime_fact_target is mapping)
            and (runtime_fact_target.skipped | default(false))
            and ('resources' not in runtime_fact_target)
          }}
          EXTRA_VAR_REMAINS_AUTHORITATIVE={{
            (extra_var_target is mapping)
            and not (extra_var_target.skipped | default(false))
            and ('resources' in extra_var_target)
            and (extra_var_target.resources | length == 1)
          }}
```

The runtime case receives only `acm_test_runtime_fixture` through `-e`, copies
it to `runtime_fact_target` with normal runtime `set_fact`, and then registers
a skipped task into that same target. The control case deliberately passes
`extra_var_target` itself through `-e`; because extra variables outrank
registered variables and `set_fact`, it must remain authoritative. This
control is never used by the production-defect test.

**Step 2: Test both the runtime-fact defect path and the extra-variable control**

Task 9 creates its test module from scratch, so the complete independent module
harness and test are in the same concrete Python fence:

```python
import os
import subprocess
from pathlib import Path

import yaml

from ansible_collections.tomazb.acm_switchover.tests.conftest import (
    _ansible_env,
)

REPO_ROOT = Path(__file__).resolve().parents[5]
PLAYBOOK = (
    "ansible_collections/tomazb/acm_switchover/tests/integration/"
    "playbooks/register_skip_semantics.yml"
)
ANSIBLE_BIN = os.environ.get(
    "ACM_ANSIBLE_PLAYBOOK_BIN",
    "ansible-playbook",
)
ANSIBLE_PY = os.environ.get("ACM_ANSIBLE_PYTHON")


def _run(tmp_path, variables):
    values = dict(variables)
    if ANSIBLE_PY:
        values["ansible_python_interpreter"] = ANSIBLE_PY

    vars_file = tmp_path / "register-semantics-vars.yml"
    vars_file.write_text(
        yaml.safe_dump(values, sort_keys=False),
        encoding="utf-8",
    )

    env = _ansible_env(REPO_ROOT, tmp_path)
    env["ANSIBLE_COLLECTIONS_PATH"] = os.environ.get(
        "ANSIBLE_COLLECTIONS_PATH",
        ":".join(
            [
                str(REPO_ROOT),
                str(REPO_ROOT / ".venv" / "collections"),
            ]
        ),
    )
    env["ANSIBLE_COLLECTIONS_SCAN_SYS_PATH"] = "false"

    return subprocess.run(
        [
            ANSIBLE_BIN,
            PLAYBOOK,
            "-i",
            (
                "ansible_collections/tomazb/acm_switchover/"
                "examples/inventory.yml"
            ),
            "-e",
            f"@{vars_file}",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=env,
        timeout=300,
    )


def test_skipped_register_and_variable_precedence_contract(tmp_path):
    variables = {
        "acm_test_runtime_fixture": {
            "resources": [{"metadata": {"name": "runtime-fact"}}],
        },
        "extra_var_target": {
            "resources": [{"metadata": {"name": "extra-var-control"}}],
        },
    }
    assert "runtime_fact_target" not in variables
    assert "acm_test_runtime_fixture" in variables
    assert "extra_var_target" in variables

    result = _run(tmp_path, variables)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "SEED_FIRED=False" in result.stdout
    assert "HAS_RESOURCES_KEY=False" in result.stdout
    assert "RUNTIME_FACT_CLOBBERED=True" in result.stdout
    assert "EXTRA_VAR_REMAINS_AUTHORITATIVE=True" in result.stdout
```

This is a committed regression for the exact precedence distinction that
governs Task 6. The Task 6 production-defect test must use only the
neutral-input → runtime-`set_fact` path, never the `extra_var_target` control
path. Task 11's B14 check parses this fence and requires exactly one `_run`
definition, exactly one `_run(tmp_path, variables)` call, the explicit
`_ansible_env` import/use, collection-path preservation, disabled sys-path
scanning, `env=env`, and the exact four output assertions.

**Step 3: Run on the default CI controller**

Run: `python -m pytest .../test_register_skip_semantics.py -q` → PASS with
both `RUNTIME_FACT_CLOBBERED=True` and
`EXTRA_VAR_REMAINS_AUTHORITATIVE=True`.

**Step 4: Mandatory 2.15.x run in the foundation environment (item 13)**

Run inside the **foundation CI container image** used by the `ansible-collection-foundation` workflow, **or** a local **Python 3.11** venv (ansible-core 2.15 controller supports Python 3.9–3.11):

```bash
AC215=/tmp/r3-01b-ac215
AC215_COLLECTIONS=/tmp/r3-01b-ac215-collections

rm -rf "$AC215" "$AC215_COLLECTIONS"

python3.11 -m venv "$AC215"

"$AC215/bin/pip" install \
  'ansible-core>=2.15,<2.16' \
  'kubernetes>=28.0.0'

mkdir -p "$AC215_COLLECTIONS"

export ANSIBLE_COLLECTIONS_PATH="$PWD:$AC215_COLLECTIONS"
export ANSIBLE_COLLECTIONS_SCAN_SYS_PATH=false

"$AC215/bin/ansible-galaxy" collection install \
  -p "$AC215_COLLECTIONS" \
  -r ansible_collections/tomazb/acm_switchover/requirements.yml

test -d \
  "$AC215_COLLECTIONS/ansible_collections/kubernetes/core"

"$AC215/bin/ansible-playbook" --version

"$AC215/bin/ansible-galaxy" collection list kubernetes.core

"$AC215/bin/ansible-doc" \
  -t module \
  kubernetes.core.k8s_info \
  >/dev/null

export ACM_ANSIBLE_PLAYBOOK_BIN="$AC215/bin/ansible-playbook"
export ACM_ANSIBLE_PYTHON="$AC215/bin/python"

PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/integration/test_register_skip_semantics.py \
  ansible_collections/tomazb/acm_switchover/tests/integration/test_finalization_cleanup_restores_runtime.py \
  ansible_collections/tomazb/acm_switchover/tests/integration/test_finalization_old_hub_restore_runtime.py \
  -q
```

**Controller-selection contract:** all R3-A2/R3-A3 runtime tests select the controller executable via `ACM_ANSIBLE_PLAYBOOK_BIN` (default `ansible-playbook`) and the module-side interpreter via `ACM_ANSIBLE_PYTHON`. The 2.15.x gate sets both to the 2.15.x venv, guaranteeing the fix and validation behave identically on the collection floor. Record the complete dependency and test evidence below in the PR.

The Task 5 and Task 6 `_run` helpers preserve this lane's explicitly exported
`ANSIBLE_COLLECTIONS_PATH="$PWD:$AC215_COLLECTIONS"` unchanged through
`os.environ.get(...)`; they must not replace it with the default
`$PWD/.venv/collections` path. They also pass
`ANSIBLE_COLLECTIONS_SCAN_SYS_PATH=false` to every subprocess, while
`_ansible_env` continues to isolate Ansible's local and remote temporary
directories.

Record all four results in the evidence log:

1. exact `ansible-playbook --version` output;
2. exact `ansible-galaxy collection list kubernetes.core` output;
3. successful `ansible-doc -t module kubernetes.core.k8s_info` lookup; and
4. exact three-file integration pytest result.

Do not fall back to an ambient user collection installation:
`ANSIBLE_COLLECTIONS_PATH` must remain exactly rooted in the repository and
the fresh isolated collection directory above. The foundation-container
alternative is allowed only when it performs the same Python dependency
installation, `ansible-galaxy` requirements installation, isolated collection
path setup, collection listing, `ansible-doc` lookup, and exact integration
test run as the repository workflow.

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

**Step 7: Exact changed-status / protected-file / scope check**

The final worktree and index must be clean before scope validation. The scope
gate validates only the committed branch diff:

```bash
test -z "$(git status --porcelain=v1 --untracked-files=all)"

python - <<'PY'
import subprocess
import sys

base = "0bf55db9eed76ae7d60844b806975c04cd0111e4"

expected_status = {
    "docs/plans/2026-07-28-r3-01b-finalization-register-clobbers-design.md": "A",
    "docs/plans/2026-07-28-r3-01b-finalization-register-clobbers-implementation-plan-b14.md": "A",
    "ansible_collections/tomazb/acm_switchover/tests/integration/finalization_fake_api.py": "A",
    "ansible_collections/tomazb/acm_switchover/tests/integration/test_finalization_fake_api.py": "A",
    "ansible_collections/tomazb/acm_switchover/roles/finalization/tasks/assert_restore_source_shape.yml": "A",
    "ansible_collections/tomazb/acm_switchover/tests/integration/playbooks/finalization_assert_restore_source_shape.yml": "A",
    "ansible_collections/tomazb/acm_switchover/tests/integration/test_restore_source_shape_contract.py": "A",
    "ansible_collections/tomazb/acm_switchover/tests/integration/playbooks/finalization_cleanup_restores.yml": "A",
    "ansible_collections/tomazb/acm_switchover/tests/integration/test_finalization_cleanup_restores_runtime.py": "A",
    "ansible_collections/tomazb/acm_switchover/tests/integration/playbooks/finalization_old_hub_restore_discovery.yml": "A",
    "ansible_collections/tomazb/acm_switchover/tests/integration/test_finalization_old_hub_restore_runtime.py": "A",
    "ansible_collections/tomazb/acm_switchover/tests/unit/register_collision_scan.py": "A",
    "ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/collisions/tasks_collision.yml": "A",
    "ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/collisions/handlers_collision.yml": "A",
    "ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/collisions/playbook_collision.yml": "A",
    "ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/collisions/clean.yml": "A",
    "ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/collisions/playbook_pre_tasks_block_collision.yml": "A",
    "ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/collisions/playbook_tasks_rescue_collision.yml": "A",
    "ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/collisions/playbook_post_tasks_always_collision.yml": "A",
    "ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/collisions/playbook_handlers_block_collision.yml": "A",
    "ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/collisions/dynamic_register.yml": "A",
    "ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/collisions/dynamic_setfact.yml": "A",
    "ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/collisions/broken.yml": "A",
    "ansible_collections/tomazb/acm_switchover/tests/unit/test_register_setfact_collision_guardrail.py": "A",
    "ansible_collections/tomazb/acm_switchover/tests/unit/register_setfact_collision_allowlist.yml": "A",
    "ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/allowlists/missing_removal_condition.yml": "A",
    "ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/allowlists/empty_rationale.yml": "A",
    "ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/allowlists/unknown_category.yml": "A",
    "ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/allowlists/duplicate_pair.yml": "A",
    "ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/allowlists/cross_category_duplicate.yml": "A",
    "ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/allowlists/wrong_issue_ref.yml": "A",
    "ansible_collections/tomazb/acm_switchover/tests/integration/playbooks/register_skip_semantics.yml": "A",
    "ansible_collections/tomazb/acm_switchover/tests/integration/test_register_skip_semantics.py": "A",
    "thermos-resolution-plan.md": "M",
    "AGENTS.md": "M",
    "CHANGELOG.md": "M",
    "ansible_collections/tomazb/acm_switchover/roles/finalization/tasks/cleanup_restores.yml": "M",
    "ansible_collections/tomazb/acm_switchover/roles/finalization/tasks/discover_resources.yml": "M",
    "ansible_collections/tomazb/acm_switchover/tests/unit/test_finalization_verification.py": "M",
    "ansible_collections/tomazb/acm_switchover/tests/unit/test_discover_resources_contracts.py": "M",
}

output = subprocess.check_output(
    ["git", "diff", "--name-status", "--find-renames", f"{base}..HEAD"],
    text=True,
)

actual_status = {}
violations = []

for raw_line in output.splitlines():
    fields = raw_line.split("\t")
    status = fields[0]

    if status.startswith(("R", "C")):
        violations.append(f"rename-or-copy-forbidden: {raw_line}")
        continue

    if len(fields) != 2:
        violations.append(f"unexpected-name-status-shape: {raw_line}")
        continue

    path = fields[1]

    if path in actual_status:
        violations.append(f"duplicate-status-path: {path}")

    actual_status[path] = status

missing = sorted(set(expected_status) - set(actual_status))
unexpected = sorted(set(actual_status) - set(expected_status))
wrong_status = sorted(
    (path, expected_status[path], actual_status[path])
    for path in set(expected_status) & set(actual_status)
    if actual_status[path] != expected_status[path]
)

violations.extend(f"missing-path: {path}" for path in missing)
violations.extend(f"unexpected-path: {path}" for path in unexpected)
violations.extend(
    f"wrong-status: {path}: expected={expected} actual={actual}"
    for path, expected, actual in wrong_status
)

if violations:
    print("\n".join(violations))
    sys.exit(1)

print(
    "scope-gate-pass-exact-status "
    "created=33 modified=7"
)
PY
```

The literal map is the complete boundary: every created path must be `A`,
every base-existing modified path must be `M`, and no expected path may be
absent. Deletion (`D`), type change (`T`), unmerged status, or any other status
fails as a wrong status; rename/copy (`R`/`C`) fails explicitly before path
comparison. No unexpected path or broad fixture-directory allowance exists.
Consequently protected files, root Python surfaces, preflight production fixes
for issue #202, Argo CD, `TR2D-02`, and unrelated collection/RBAC/workflow
changes cannot pass. In particular, deleting a required base-existing file
cannot masquerade as an allowed name.

**Step 8: Task-reference self-check (X1/X2 hardening)**

Programmatically audit all task cross-reference forms before execution/publication:

```bash
python - <<'PY'
import pathlib, re, sys
p = pathlib.Path("docs/plans/2026-07-28-r3-01b-finalization-register-clobbers-implementation-plan-b14.md")
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

**Step 9: Occurrence-exact commit presence, uniqueness, boundary, and bidirectional scope audit (B14-C2)**

```bash
python - <<'PY'
"""Prove occurrence-exact task ownership and literal commit boundaries."""
import ast
from collections import defaultdict
import pathlib
import re
import shlex
import sys

plan = pathlib.Path("docs/plans/2026-07-28-r3-01b-finalization-register-clobbers-implementation-plan-b14.md")
text = plan.read_text(encoding="utf-8")

expected_created_paths = {
    "docs/plans/2026-07-28-r3-01b-finalization-register-clobbers-design.md",
    "docs/plans/2026-07-28-r3-01b-finalization-register-clobbers-implementation-plan-b14.md",
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
    "ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/collisions/tasks_collision.yml",
    "ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/collisions/handlers_collision.yml",
    "ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/collisions/playbook_collision.yml",
    "ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/collisions/clean.yml",
    "ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/collisions/playbook_pre_tasks_block_collision.yml",
    "ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/collisions/playbook_tasks_rescue_collision.yml",
    "ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/collisions/playbook_post_tasks_always_collision.yml",
    "ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/collisions/playbook_handlers_block_collision.yml",
    "ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/collisions/dynamic_register.yml",
    "ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/collisions/dynamic_setfact.yml",
    "ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/collisions/broken.yml",
    "ansible_collections/tomazb/acm_switchover/tests/unit/test_register_setfact_collision_guardrail.py",
    "ansible_collections/tomazb/acm_switchover/tests/unit/register_setfact_collision_allowlist.yml",
    "ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/allowlists/missing_removal_condition.yml",
    "ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/allowlists/empty_rationale.yml",
    "ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/allowlists/unknown_category.yml",
    "ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/allowlists/duplicate_pair.yml",
    "ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/allowlists/cross_category_duplicate.yml",
    "ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/allowlists/wrong_issue_ref.yml",
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
expected_owned_paths = expected_created_paths | permitted_modified_paths

expected_commit_messages = {
    "Task 1": (
        "docs(plan): seed approved R3-01b-DESIGN-B2 "
        "and R3-01b-PLAN-B14 into worktree"
    ),
    "Task 2": (
        "docs(tracker): mark R3-01b in_progress with "
        "R3-01b-DESIGN-B2/PLAN-B14 identifiers"
    ),
    "Task 3": (
        "test(collection): add executable fake ACM backup API "
        "for finalization runtime tests"
    ),
    "Task 4": (
        "test(collection): Task 4 shared staged fail-closed "
        "shape validator with undefined-source harness"
    ),
    "Task 5": (
        "fix(collection): R3-A2 truthful finalization dry-run "
        "Restore preview via candidate/validate/publish"
    ),
    "Task 6": (
        "fix(collection): R3-A3 preserve old-hub restore "
        "fixture/seed with distinct live name and staged validation"
    ),
    "Task 7": (
        "test(collection): literal-only register/set_fact collision "
        "scanner with dynamic-name and parse-error coverage"
    ),
    "Task 8": (
        "test(collection): enforce collision guardrail with strict "
        "two-category allowlist validation (#202 debt)"
    ),
    "Task 9": (
        "test(collection): pin skipped-register semantics with "
        "a 2.15.x foundation verification gate"
    ),
    "Task 10": (
        "docs: record register/set_fact collision convention "
        "and R3-01b changelog"
    ),
}

# Parse logical commands only in fenced shell/command blocks. Backslash
# continuations become one command with the first physical source line.
shell_fences = {"bash", "sh", "shell", "command", "console"}
logical_commands = []
current_task = "outside"
fence_language = None
parts = []
command_line = None
command_task = None
heredoc_end = None

for line_number, line in enumerate(text.splitlines(), start=1):
    task_match = re.match(r"^## (Task \d+)", line)
    if task_match and fence_language is None:
        current_task = task_match.group(1)

    fence_match = re.match(r"^```([A-Za-z0-9_-]*)\s*$", line)
    if fence_match:
        if fence_language is None:
            fence_language = fence_match.group(1)
        else:
            if parts:
                print(f"unterminated-logical-command-before-fence:{command_line}")
                sys.exit(1)
            fence_language = None
        continue

    if fence_language not in shell_fences:
        continue
    if heredoc_end is not None:
        if line.strip() == heredoc_end:
            heredoc_end = None
        continue

    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        continue
    if not parts:
        command_line = line_number
        command_task = current_task
    continued = stripped.endswith("\\")
    parts.append(stripped[:-1].rstrip() if continued else stripped)
    if continued:
        continue

    command_text = " ".join(part for part in parts if part)
    logical_commands.append(
        {
            "task": command_task,
            "line": command_line,
            "text": command_text,
        }
    )
    heredoc_match = re.search(
        r"<<-?['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?\s*$",
        command_text,
    )
    if heredoc_match:
        heredoc_end = heredoc_match.group(1)
    parts = []
    command_line = None
    command_task = None

if parts or fence_language is not None or heredoc_end is not None:
    print("unterminated-command-or-fence")
    sys.exit(1)

git_add_occurrences = defaultdict(list)
git_commit_occurrences = defaultdict(list)
git_add_commands = defaultdict(list)
git_commit_commands = defaultdict(list)

for command_index, command in enumerate(logical_commands):
    try:
        tokens = shlex.split(command["text"])
    except ValueError as exc:
        print(
            f"shell-command-parse-failed line={command['line']}: {exc}"
        )
        sys.exit(1)

    if tokens[:2] == ["git", "add"]:
        if len(tokens) < 3:
            print(f"empty-git-add line={command['line']}")
            sys.exit(1)
        paths = tokens[2:]
        if any(path.startswith("-") for path in paths):
            print(
                f"git-add-option-forbidden line={command['line']}: {paths}"
            )
            sys.exit(1)
        entry = {
            "task": command["task"],
            "command_index": command_index,
            "line": command["line"],
            "paths": paths,
        }
        git_add_commands[command["task"]].append(entry)
        for path in paths:
            git_add_occurrences[path].append(
                {
                    "task": command["task"],
                    "command_index": command_index,
                    "line": command["line"],
                }
            )
        continue

    if tokens[:2] == ["git", "commit"]:
        if len(tokens) != 4 or tokens[2] != "-m":
            print(
                f"git-commit-form-forbidden line={command['line']}: "
                f"{tokens}"
            )
            sys.exit(1)
        entry = {
            "message": tokens[3],
            "command_index": command_index,
            "line": command["line"],
        }
        git_commit_occurrences[command["task"]].append(entry)
        git_commit_commands[command["task"]].append(entry)

# Parse every Task Files section and retain declared task ownership.
files_by_path = defaultdict(list)
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
        files_by_path[path].append(current_task)

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

# Parse Task 11 Step 7's literal exact-status map.
status_match = re.search(
    r"expected_status = (?P<body>\{\n.*?\n\})",
    text,
    re.DOTALL,
)
if status_match is None:
    print("scope-status-map-parse-failed")
    sys.exit(1)
scope_expected_status = ast.literal_eval(status_match.group("body"))
expected_scope_status = {
    **{path: "A" for path in expected_created_paths},
    **{path: "M" for path in permitted_modified_paths},
}

violations = []
for label, values in (
    (
        "git-add-missing",
        sorted(expected_owned_paths - set(git_add_occurrences)),
    ),
    (
        "git-add-unexpected",
        sorted(set(git_add_occurrences) - expected_owned_paths),
    ),
    ("coverage-table-missing", sorted(expected_owned_paths - set(table_by_path))),
    ("coverage-table-unexpected", sorted(set(table_by_path) - expected_owned_paths)),
    ("files-missing", sorted(expected_owned_paths - set(files_by_path))),
    ("files-unexpected", sorted(set(files_by_path) - expected_owned_paths)),
    (
        "scope-status-map-mismatch",
        sorted(
            (path, expected_scope_status.get(path), scope_expected_status.get(path))
            for path in set(expected_scope_status) | set(scope_expected_status)
            if expected_scope_status.get(path) != scope_expected_status.get(path)
        ),
    ),
    (
        "ownership-total-mismatch",
        []
        if len(expected_created_paths) == 33
        and len(permitted_modified_paths) == 7
        else [len(expected_created_paths), len(permitted_modified_paths)],
    ),
):
    if values:
        violations.append(f"{label}: {values}")

for task_number in range(1, 11):
    task = f"Task {task_number}"
    add_commands = git_add_commands.get(task, [])
    commit_commands = git_commit_commands.get(task, [])
    if len(add_commands) != 1:
        violations.append(
            f"git-add-command-count: {task}: {len(add_commands)}"
        )
        continue
    if len(commit_commands) != 1:
        violations.append(
            f"git-commit-command-count: {task}: {len(commit_commands)}"
        )
        continue

    add_command = add_commands[0]
    commit_command = commit_commands[0]
    expected_task_paths = {
        path
        for path, owner in table_by_path.items()
        if owner == task
    }
    if len(add_command["paths"]) != len(set(add_command["paths"])):
        violations.append(
            f"duplicate-path-in-sole-git-add: {task}: "
            f"{add_command['paths']}"
        )
    if set(add_command["paths"]) != expected_task_paths:
        violations.append(
            f"sole-git-add-scope-mismatch: {task}: "
            f"expected={sorted(expected_task_paths)} "
            f"actual={sorted(add_command['paths'])}"
        )
    if add_command["command_index"] >= commit_command["command_index"]:
        violations.append(
            f"git-add-not-before-commit: {task}: "
            f"add_line={add_command['line']} "
            f"commit_line={commit_command['line']}"
        )
    expected_message = expected_commit_messages[task]
    if commit_command["message"] != expected_message:
        violations.append(
            f"commit-message-mismatch: {task}: "
            f"expected={expected_message!r} "
            f"actual={commit_command['message']!r}"
        )

for task in ("Task 0", "Task 11", "Task 12"):
    if git_commit_occurrences.get(task):
        violations.append(
            f"forbidden-commit-command: {task}: "
            f"{git_commit_occurrences[task]}"
        )

for path in sorted(expected_owned_paths):
    expected_task = table_by_path.get(path)
    file_tasks = files_by_path.get(path, [])
    add_occurrences = git_add_occurrences.get(path, [])
    if len(file_tasks) != 1 or file_tasks[0] != expected_task:
        violations.append(
            f"files-task-mismatch: {path}: "
            f"table={expected_task} files={file_tasks}"
        )
    if len(add_occurrences) != 1:
        violations.append(
            f"git-add-occurrence-count: {path}: "
            f"expected=1 actual={len(add_occurrences)} "
            f"occurrences={add_occurrences}"
        )
    elif add_occurrences[0]["task"] != expected_task:
        violations.append(
            f"git-add-task-mismatch: {path}: "
            f"table={expected_task} "
            f"occurrence={add_occurrences[0]}"
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
    "commit-presence-uniqueness-boundary-scope-gate-pass "
    f"created={len(expected_created_paths)} "
    f"modified={len(permitted_modified_paths)} "
    f"tasks_with_commits={len(git_commit_occurrences)}"
)
PY
```

Fail on any missing, duplicate same-task, duplicate cross-task, or unexpected
addition; any extra add/commit command; any `git add -A`, option-bearing add,
`git commit -a`, `git commit --all`, or non-literal commit form; any boundary
or exact-message mismatch; or any disagreement among the ownership table,
Task Files sections, exact status map, and created/modified sets. Occurrence
lists remain lists through multiplicity validation; set conversion is used
only for scope equality after counts have been proven.

**Step 10: Executable preserved B10 bounded self-check**

```bash
python - <<'PY'
import ast
import pathlib
import re
import shlex
import sys

plan = pathlib.Path(
    "docs/plans/"
    "2026-07-28-r3-01b-finalization-register-clobbers-"
    "implementation-plan-b14.md"
)
text = plan.read_text(encoding="utf-8")

fixture_paths = {
    "ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/collisions/tasks_collision.yml",
    "ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/collisions/handlers_collision.yml",
    "ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/collisions/playbook_collision.yml",
    "ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/collisions/clean.yml",
    "ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/collisions/playbook_pre_tasks_block_collision.yml",
    "ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/collisions/playbook_tasks_rescue_collision.yml",
    "ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/collisions/playbook_post_tasks_always_collision.yml",
    "ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/collisions/playbook_handlers_block_collision.yml",
    "ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/collisions/dynamic_register.yml",
    "ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/collisions/dynamic_setfact.yml",
    "ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/collisions/broken.yml",
    "ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/allowlists/missing_removal_condition.yml",
    "ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/allowlists/empty_rationale.yml",
    "ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/allowlists/unknown_category.yml",
    "ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/allowlists/duplicate_pair.yml",
    "ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/allowlists/cross_category_duplicate.yml",
    "ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/allowlists/wrong_issue_ref.yml",
}

violations = []
task5 = text.split("## Task 5:", 1)[1].split("## Task 6:", 1)[0]
helper_definitions = re.findall(
    r"^def _execute_vars\(tmp_path, hub, \*, stale_seed=\(\)\):$",
    task5,
    re.MULTILINE,
)
helper_calls = [
    line.strip()
    for line in task5.splitlines()
    if "_execute_vars(" in line and not line.lstrip().startswith("def ")
]
if len(helper_definitions) != 1:
    violations.append(
        f"task5-execute-helper-definition-count: {len(helper_definitions)}"
    )
if len(helper_calls) != 5:
    violations.append(f"task5-execute-helper-call-count: {len(helper_calls)}")
for call in helper_calls:
    if "_execute_vars(tmp_path, hub," not in call:
        violations.append(f"task5-execute-helper-bad-call: {call}")
bad_execute_call = "_execute_vars(" + "hub,"
if bad_execute_call in task5:
    violations.append("task5-unresolved-execute-vars-hub-form")

table_entries = {
    path: kind
    for path, _task, kind in re.findall(
        r"^\| `([^`]+)` \| (Task \d+) \| (Create|Modify) \|$",
        text,
        re.MULTILINE,
    )
}
files_entries = set(
    re.findall(
        r"^- (?:Create|Test|Modify(?: \([^)]*\))?): `([^`]+)`",
        text,
        re.MULTILINE,
    )
)

git_add_paths = set()
in_fence = False
in_git_add = False
for line in text.splitlines():
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
    git_add_paths.update(shlex.split(fragment))
    if not stripped.endswith("\\"):
        in_git_add = False

status_match = re.search(
    r"expected_status = (?P<body>\{\n.*?\n\})",
    text,
    re.DOTALL,
)
if status_match is None:
    violations.append("exact-status-map-missing")
    status_map = {}
else:
    status_map = ast.literal_eval(status_match.group("body"))

step9 = text.split("**Step 9: Occurrence-exact", 1)[1].split(
    "**Step 10:", 1
)[0]
created_match = re.search(
    r"expected_created_paths = (?P<body>\{\n.*?\n\})",
    step9,
    re.DOTALL,
)
if created_match is None:
    violations.append("commit-audit-created-set-missing")
    commit_created = set()
else:
    commit_created = ast.literal_eval(created_match.group("body"))

for label, surface in (
    ("ownership-table", set(table_entries)),
    ("task-files", files_entries),
    ("git-add", git_add_paths),
    ("exact-status-map", set(status_map)),
    ("commit-audit", commit_created),
):
    surface_fixtures = {
        path
        for path in surface
        if path.startswith(
            "ansible_collections/tomazb/acm_switchover/"
            "tests/unit/fixtures/collisions/"
        )
        or path.startswith(
            "ansible_collections/tomazb/acm_switchover/"
            "tests/unit/fixtures/allowlists/"
        )
    }
    if surface_fixtures != fixture_paths:
        violations.append(
            f"{label}-fixture-inventory-mismatch: "
            f"{sorted(surface_fixtures ^ fixture_paths)}"
        )

created_count = sum(value == "Create" for value in table_entries.values())
modified_count = sum(value == "Modify" for value in table_entries.values())
if (created_count, modified_count) != (33, 7):
    violations.append(
        f"ownership-totals: created={created_count} modified={modified_count}"
    )
if (
    sum(value == "A" for value in status_map.values()),
    sum(value == "M" for value in status_map.values()),
) != (33, 7):
    violations.append("exact-status-map-totals-not-33-7")

banned_prefix_symbol = "optional_" + "prefixes"
banned_fixture_boundary_symbol = "expected_fixture_" + "boundaries"
if banned_prefix_symbol in text:
    violations.append("broad-fixture-prefix-symbol-remains")
if banned_fixture_boundary_symbol in text:
    violations.append("fixture-boundary-symbol-remains")
if '"git", "diff", "--name-status", "--find-renames"' not in text:
    violations.append("name-status-scope-command-missing")
for token in (
    "rename-or-copy-forbidden",
    "wrong-status:",
    "Deletion (`D`)",
    "missing-path:",
):
    if token not in text:
        violations.append(f"scope-rejection-token-missing: {token}")

for marker, counter in (
    ("FAKE_RESTORE_LIST_FAILURE", "secondary_restore_list_hits >= 1"),
    (
        "FAKE_RESTORE_GET_FAILURE:restore-acm-full",
        'old_hub_restore_named_get_hits.get("restore-acm-full", 0) >= 1',
    ),
    (
        "FAKE_RESTORE_DELETE_FAILURE:restore-acm-full",
        'secondary_restore_named_delete_hits.get("restore-acm-full", 0) >= 1',
    ),
):
    if marker not in task5:
        violations.append(f"task5-failure-marker-missing: {marker}")
    if counter not in task5:
        violations.append(f"task5-positive-counter-missing: {counter}")

task9 = text.split("## Task 9:", 1)[1].split("## Task 10:", 1)[0]
for token in (
    "ansible-galaxy\" collection install",
    "ansible_collections/tomazb/acm_switchover/requirements.yml",
    "ansible-galaxy\" collection list kubernetes.core",
    "ansible-doc\"",
    "kubernetes.core.k8s_info",
):
    if token not in task9:
        violations.append(f"task9-dependency-token-missing: {token}")

if violations:
    print("\n".join(violations))
    sys.exit(1)
print(
    "b10-preserved-bounded-self-check-pass "
    "created=33 modified=7 fixtures=17 execute_calls=5"
)
PY
```

**Step 11: Executable B11 bounded self-check**

```bash
python - <<'PY'
import ast
import pathlib
import re
import sys

plan = pathlib.Path(
    "docs/plans/"
    "2026-07-28-r3-01b-finalization-register-clobbers-"
    "implementation-plan-b14.md"
)
text = plan.read_text(encoding="utf-8")
violations = []

task3 = text.split("## Task 3:", 1)[1].split("## Task 4:", 1)[0]
task5 = text.split("## Task 5:", 1)[1].split("## Task 6:", 1)[0]
task6 = text.split("## Task 6:", 1)[1].split("## Task 7:", 1)[0]
task7 = text.split("## Task 7:", 1)[1].split("## Task 8:", 1)[0]
task9 = text.split("## Task 9:", 1)[1].split("## Task 10:", 1)[0]

run_definitions = re.findall(
    r"^def _run\(tmp_path, variables\):$",
    task6,
    re.MULTILINE,
)
run_calls = [
    line.strip()
    for line in task6.splitlines()
    if "_run(" in line and not line.lstrip().startswith("def ")
]
if len(run_definitions) != 1:
    violations.append(
        f"task6-run-definition-count: {len(run_definitions)}"
    )
if len(run_calls) != 8:
    violations.append(f"task6-run-call-count: {len(run_calls)}")
for call in run_calls:
    if not re.search(
        r"_run\(\s*tmp_path\s*,\s*(variables|v)\s*\)",
        call,
    ):
        violations.append(f"task6-run-call-missing-tmp-path-or-mapping: {call}")
if "test_finalization_cleanup_restores_runtime" in task6:
    violations.append("task6-relies-on-task5-test-module")
for token in (
    'os.environ.get(\n    "ACM_ANSIBLE_PLAYBOOK_BIN"',
    'os.environ.get("ACM_ANSIBLE_PYTHON")',
    "cwd=REPO_ROOT",
    "timeout=300",
):
    if token not in task6:
        violations.append(f"task6-run-harness-token-missing: {token}")

stale_definition = (
    "def test_dry_run_seeds_empty_old_hub_restore" + "():"
)
if stale_definition in task6:
    violations.append("stale-old-hub-authoritative-seed-test-remains")
if (
    "def test_dry_run_seeds_empty_old_hub_restore_candidate():"
    not in task6
):
    violations.append("replacement-old-hub-candidate-seed-test-missing")
for token in (
    '"_old_hub_existing_restore_candidate"',
    'assert "_old_hub_existing_restore_info" not in facts',
    '"_old_hub_existing_restore_info is not defined"',
    "python -m pytest \\",
    "tests/unit/test_discover_resources_contracts.py",
    "tests/unit/test_finalization_verification.py",
):
    if token not in task6:
        violations.append(f"task6-unit-reconciliation-token-missing: {token}")

guardrail_functions = {
    "test_scan_boundary_includes_nested_role_tasks_and_handlers",
    "test_scan_boundary_includes_nested_playbooks",
    "test_scan_boundary_has_no_duplicate_file_scans",
    "test_scan_boundary_paths_are_collection_relative",
}
python_fences = re.findall(r"```python\n(.*?)```", task7, re.DOTALL)
guardrail_fence = next(
    (
        fence
        for fence in python_fences
        if all(f"def {name}" in fence for name in guardrail_functions)
    ),
    None,
)
if guardrail_fence is None:
    violations.append("guardrail-concrete-test-fence-missing")
else:
    try:
        tree = ast.parse(guardrail_fence)
    except SyntaxError as exc:
        violations.append(f"guardrail-test-fence-syntax-error: {exc}")
    else:
        functions = {
            node.name: node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for name in sorted(guardrail_functions):
            function = functions.get(name)
            if function is None:
                violations.append(f"guardrail-test-function-missing: {name}")
                continue
            if not function.body:
                violations.append(f"guardrail-test-empty-body: {name}")
            for node in ast.walk(function):
                if isinstance(node, ast.Pass):
                    violations.append(f"guardrail-test-pass-body: {name}")
                if (
                    isinstance(node, ast.Constant)
                    and node.value is Ellipsis
                ):
                    violations.append(f"guardrail-test-ellipsis-body: {name}")

for token in (
    'roles/example/tasks/nested/main.yml',
    'roles/example/handlers/nested/main.yml',
    'playbooks/nested/deeper/example.yml',
    "len(calls) == len(set(calls))",
    "assert counts[path] == 1",
    "not pathlib.Path(relative_path).is_absolute()",
    "str(collection_root) not in relative_path",
):
    if token not in task7:
        violations.append(f"guardrail-concrete-boundary-token-missing: {token}")

table_entries = [
    (path, kind)
    for path, _task, kind in re.findall(
        r"^\| `([^`]+)` \| (Task \d+) \| (Create|Modify) \|$",
        text,
        re.MULTILINE,
    )
]
created_count = sum(kind == "Create" for _path, kind in table_entries)
modified_count = sum(kind == "Modify" for _path, kind in table_entries)
fixture_count = sum(
    "/tests/unit/fixtures/collisions/" in path
    or "/tests/unit/fixtures/allowlists/" in path
    for path, _kind in table_entries
)
if (created_count, modified_count, fixture_count) != (33, 7, 17):
    violations.append(
        "ownership-totals-changed: "
        f"created={created_count} modified={modified_count} "
        f"fixtures={fixture_count}"
    )

for token in (
    '"git", "diff", "--name-status", "--find-renames"',
    "rename-or-copy-forbidden",
    "wrong-status:",
    "Deletion (`D`)",
    "created=33 modified=7",
):
    if token not in text:
        violations.append(f"preserved-exact-status-token-missing: {token}")

for token in (
    "increment `secondary_restore_list_hits` before checking LIST failure",
    "before checking `get_failures`",
    "before checking `delete_failures`",
    "FAKE_RESTORE_LIST_FAILURE",
    "FAKE_RESTORE_GET_FAILURE:<name>",
    "FAKE_RESTORE_DELETE_FAILURE:<name>",
):
    if token not in task3:
        violations.append(f"preserved-fake-contract-token-missing: {token}")

for marker, counter in (
    ("FAKE_RESTORE_LIST_FAILURE", "secondary_restore_list_hits >= 1"),
    (
        "FAKE_RESTORE_GET_FAILURE:restore-acm-full",
        'old_hub_restore_named_get_hits.get("restore-acm-full", 0) >= 1',
    ),
    (
        "FAKE_RESTORE_DELETE_FAILURE:restore-acm-full",
        'secondary_restore_named_delete_hits.get("restore-acm-full", 0) >= 1',
    ),
):
    if marker not in task5:
        violations.append(f"preserved-task5-failure-marker-missing: {marker}")
    if counter not in task5:
        violations.append(f"preserved-task5-failing-counter-missing: {counter}")

for token in (
    "FAKE_RESTORE_GET_FAILURE:restore-acm-passive-sync",
    'old_hub_restore_named_get_hits.get(',
    "secondary_mch_list_hits >= 1",
    "secondary_restore_list_hits >= 1",
    "secondary_restore_delete_hits == 0",
):
    if token not in task6:
        violations.append(f"preserved-task6-failure-attribution-missing: {token}")

for token in (
    "ansible-galaxy\" collection install",
    "ansible_collections/tomazb/acm_switchover/requirements.yml",
    "ansible-galaxy\" collection list kubernetes.core",
    "ansible-doc\"",
    "kubernetes.core.k8s_info",
):
    if token not in task9:
        violations.append(f"preserved-task9-dependency-token-missing: {token}")

if violations:
    print("\n".join(violations))
    sys.exit(1)
print(
    "b11-bounded-self-check-pass "
    "created=33 modified=7 fixtures=17 task6_run_calls=8"
)
PY
```

**Step 12: Executable B12 bounded self-check**

Run this only after Steps 10 and 11 have both exited zero in the same evidence
run. It proves B12-C1 through B12-C4, exact ownership totals, and the presence
of both successful preserved-check gates:

```bash
python - <<'PY'
import pathlib
import re
import sys

plan = pathlib.Path(
    "docs/plans/"
    "2026-07-28-r3-01b-finalization-register-clobbers-"
    "implementation-plan-b14.md"
)
text = plan.read_text(encoding="utf-8")
violations = []

task0 = text.split("## Task 0:", 1)[1].split("## Task 1:", 1)[0]
task3 = text.split("## Task 3:", 1)[1].split("## Task 4:", 1)[0]
task5 = text.split("## Task 5:", 1)[1].split("## Task 6:", 1)[0]
task6 = text.split("## Task 6:", 1)[1].split("## Task 7:", 1)[0]
task9 = text.split("## Task 9:", 1)[1].split("## Task 10:", 1)[0]
before_b12_check = text.split(
    "**Step 12: Executable B12 bounded self-check**",
    1,
)[0]


def require_tokens(section, label, tokens):
    for token in tokens:
        if token not in section:
            violations.append(f"{label}-token-missing: {token}")


require_tokens(
    task0,
    "task0-bootstrap",
    (
        "python3 -m venv .venv",
        "source .venv/bin/activate",
        "python -m pip install --upgrade pip",
        "-r requirements.txt",
        "-r requirements-dev.txt",
        'DEFAULT_COLLECTIONS_DIR="$PWD/.venv/collections"',
        "ansible-galaxy collection install",
        "-r ansible_collections/tomazb/acm_switchover/requirements.yml",
        'export ANSIBLE_COLLECTIONS_PATH="$PWD:$DEFAULT_COLLECTIONS_DIR"',
        "export ANSIBLE_COLLECTIONS_SCAN_SYS_PATH=false",
        "python -c 'import kubernetes; print(kubernetes.__version__)'",
        '"$DEFAULT_COLLECTIONS_DIR/ansible_collections/kubernetes/core"',
        "ansible-playbook --version",
        "ansible-galaxy collection list kubernetes.core",
        "kubernetes.core.k8s_info",
        "no Task 3 smoke run",
        "`~/.ansible/collections`",
    ),
)

required_import = (
    "from ansible_collections.tomazb.acm_switchover.tests.conftest import (\n"
    "    _ansible_env,\n"
    ")"
)


def check_run_environment(section, label):
    python_fences = re.findall(r"```python\n(.*?)```", section, re.DOTALL)
    run_fences = [
        fence
        for fence in python_fences
        if "def _run(tmp_path, variables):" in fence
    ]
    if len(run_fences) != 1:
        violations.append(f"{label}-run-fence-count: {len(run_fences)}")
        return
    fence = run_fences[0]
    require_tokens(
        fence,
        f"{label}-run-environment",
        (
            required_import,
            "env = _ansible_env(REPO_ROOT, tmp_path)",
            'env["ANSIBLE_COLLECTIONS_PATH"] = os.environ.get(',
            '"ANSIBLE_COLLECTIONS_PATH",',
            "str(REPO_ROOT)",
            'str(REPO_ROOT / ".venv" / "collections")',
            'env["ANSIBLE_COLLECTIONS_SCAN_SYS_PATH"] = "false"',
            "env=env",
            "timeout=300",
        ),
    )
    subprocess_runs = fence.count("subprocess.run(")
    explicit_envs = fence.count("env=env")
    if subprocess_runs != 1 or explicit_envs != subprocess_runs:
        violations.append(
            f"{label}-subprocess-env-count: "
            f"runs={subprocess_runs} envs={explicit_envs}"
        )


check_run_environment(task5, "task5")
check_run_environment(task6, "task6")

require_tokens(
    task3,
    "task3-smoke",
    (
        "python -c 'import kubernetes'",
        'ANSIBLE_COLLECTIONS_PATH="$PWD:$PWD/.venv/collections"',
        "ANSIBLE_COLLECTIONS_SCAN_SYS_PATH=false",
        "ansible-doc",
        "kubernetes.core.k8s_info",
        'ansible-playbook "$SMOKE_PLAYBOOK"',
        "git status --porcelain=v1 --untracked-files=all",
        "r3-01b-k8s-info-smoke",
    ),
)

require_tokens(
    task9,
    "task9-isolated-lane",
    (
        "python3.11 -m venv",
        "'ansible-core>=2.15,<2.16'",
        "'kubernetes>=28.0.0'",
        'AC215_COLLECTIONS=/tmp/r3-01b-ac215-collections',
        'export ANSIBLE_COLLECTIONS_PATH="$PWD:$AC215_COLLECTIONS"',
        "export ANSIBLE_COLLECTIONS_SCAN_SYS_PATH=false",
        "ansible-galaxy\" collection install",
        "ansible_collections/tomazb/acm_switchover/requirements.yml",
        "ansible-galaxy\" collection list kubernetes.core",
        "ansible-doc\"",
        "kubernetes.core.k8s_info",
        "test_register_skip_semantics.py",
        "test_finalization_cleanup_restores_runtime.py",
        "test_finalization_old_hub_restore_runtime.py",
        "preserve this lane's explicitly exported",
        "must not replace it with the default",
    ),
)

table_entries = [
    (path, kind)
    for path, _task, kind in re.findall(
        r"^\| `([^`]+)` \| (Task \d+) \| (Create|Modify) \|$",
        text,
        re.MULTILINE,
    )
]
created_count = sum(kind == "Create" for _path, kind in table_entries)
modified_count = sum(kind == "Modify" for _path, kind in table_entries)
fixture_count = sum(
    "/tests/unit/fixtures/collisions/" in path
    or "/tests/unit/fixtures/allowlists/" in path
    for path, _kind in table_entries
)
if (created_count, modified_count, fixture_count) != (33, 7, 17):
    violations.append(
        "ownership-totals-changed: "
        f"created={created_count} modified={modified_count} "
        f"fixtures={fixture_count}"
    )

for label, step, marker in (
    (
        "b10",
        "**Step 10: Executable preserved B10 bounded self-check**",
        "b10-preserved-bounded-self-check-pass",
    ),
    (
        "b11",
        "**Step 11: Executable B11 bounded self-check**",
        "b11-bounded-self-check-pass",
    ),
):
    if step not in before_b12_check:
        violations.append(f"{label}-preserved-step-missing")
    if marker not in before_b12_check:
        violations.append(f"{label}-preserved-pass-marker-missing")

if violations:
    print("\n".join(violations))
    sys.exit(1)
print(
    "b12-bounded-self-check-pass "
    "created=33 modified=7 fixtures=17 "
    "task0_bootstrap=complete task5_env=complete task6_env=complete "
    "task3_smoke_env=complete task9_isolated_lane=preserved "
    "b10_check=preserved b11_check=preserved"
)
PY
```

**Step 13: Executable preserved B13 bounded self-check**

Run this only after Steps 10, 11, and 12 have each exited zero in the same
evidence run. It proves B13-C1 through B13-C5, unchanged ownership, and the
presence of all three successful preserved-check gates:

```bash
python - <<'PY'
import pathlib
import re
import sys

plan = pathlib.Path(
    "docs/plans/"
    "2026-07-28-r3-01b-finalization-register-clobbers-"
    "implementation-plan-b14.md"
)
text = plan.read_text(encoding="utf-8")
violations = []

task6 = text.split("## Task 6:", 1)[1].split("## Task 7:", 1)[0]
task9 = text.split("## Task 9:", 1)[1].split("## Task 10:", 1)[0]
before_b13_check = text.split(
    "**Step 13: Executable preserved B13 bounded self-check**",
    1,
)[0]

dry_builder = task6.split(
    "def _dry_run_normal_vars(*, old_hub_fixture=_UNSET):",
    1,
)[1].split("def _execute_normal_vars(", 1)[0]
execute_builder = task6.split("def _execute_normal_vars(", 1)[1].split(
    "def _direct_candidate_vars(",
    1,
)[0]

for label, builder in (
    ("dry-run", dry_builder),
    ("execute", execute_builder),
):
    forbidden_assignment = (
        'variables["_old_hub_existing_restore_info"]' + " ="
    )
    if forbidden_assignment in builder:
        violations.append(f"{label}-authoritative-extra-var-assignment")
    for token in (
        'variables["acm_test_old_hub_fixture"] = old_hub_fixture',
        'assert "_old_hub_existing_restore_info" not in variables',
    ):
        if token not in builder:
            violations.append(f"{label}-neutral-fixture-token-missing: {token}")

yaml_fences = re.findall(r"```yaml\n(.*?)```", task6, re.DOTALL)
seed_fences = [
    fence
    for fence in yaml_fences
    if "Seed old-hub fixture as a runtime fact" in fence
]
if len(seed_fences) != 1:
    violations.append(f"runtime-seed-fence-count: {len(seed_fences)}")
else:
    harness = seed_fences[0]
    seed_assignment = (
        '_old_hub_existing_restore_info: '
        '"{{ acm_test_old_hub_fixture }}"'
    )
    if harness.count(seed_assignment) != 1:
        violations.append(
            "runtime-seed-assignment-count: "
            f"{harness.count(seed_assignment)}"
        )
    for token in (
        "old_hub_fixture_seeded_by_harness: false",
        "old_hub_fixture_seeded_by_harness: true",
        "when: acm_test_old_hub_fixture is defined",
        "OLD_HUB_FIXTURE_SEEDED_BY_HARNESS",
        "tasks_from: discover_resources",
    ):
        if token not in harness:
            violations.append(f"runtime-harness-token-missing: {token}")
    if (
        seed_assignment in harness
        and "tasks_from: discover_resources" in harness
        and harness.index(seed_assignment)
        >= harness.index("tasks_from: discover_resources")
    ):
        violations.append("runtime-seed-not-before-normal-role-include")

valid_fixture_test = task6.split(
    "def test_injected_fixture_preserved(tmp_path):",
    1,
)[1].split("**Step 3:", 1)[0]
for token in (
    'assert "_old_hub_existing_restore_info" not in variables',
    'assert "acm_test_old_hub_fixture" in variables',
    "_assert_runtime_fixture_input(variables)",
    'assert "OLD_HUB_RESULT_SKIPPED=False" in r.stdout',
    'assert "OLD_HUB_COUNT=1" in r.stdout',
):
    if token not in valid_fixture_test:
        violations.append(f"valid-fixture-test-token-missing: {token}")

pref_fix_fixture_evidence = (
    "NORMAL_MODE_ENTERED=True",
    "DIRECT_MODE_ENTERED=False",
    "OLD_HUB_FIXTURE_SEEDED_BY_HARNESS=True",
    "OLD_HUB_AUTHORITATIVE_DEFINED_BEFORE_NORMAL=True",
    "OLD_HUB_SEED_REGISTER_REACHED=True",
    "OLD_HUB_RESULT_SKIPPED=True",
    "OLD_HUB_HAS_RESOURCES_KEY=False",
)
for token in pref_fix_fixture_evidence:
    if token not in task6:
        violations.append(f"pre-fix-evidence-token-missing: {token}")

for token in (
    "OLD_HUB_RESULT_SKIPPED=False",
    "OLD_HUB_HAS_RESOURCES_KEY=True",
    "OLD_HUB_COUNT=1",
):
    if token not in task6:
        violations.append(f"post-fix-evidence-token-missing: {token}")

fixture_assert_helper = task6.split(
    "def _assert_runtime_fixture_input(variables):",
    1,
)[1].split("```", 1)[0]
for token in (
    'assert "_old_hub_existing_restore_info" not in variables',
    'assert "acm_test_old_hub_fixture" in variables',
):
    if token not in fixture_assert_helper:
        violations.append(f"fixture-assert-helper-token-missing: {token}")

if "No normal-mode scenario may pass `_old_hub_existing_restore_info` through" not in task6:
    violations.append("normal-mode-authoritative-extra-var-ban-missing")

for token in (
    "Copy neutral extra-variable fixture to a runtime fact",
    'runtime_fact_target: "{{ acm_test_runtime_fixture }}"',
    "register: runtime_fact_target",
    "register: extra_var_target",
    "RUNTIME_FACT_CLOBBERED=True",
    "EXTRA_VAR_REMAINS_AUTHORITATIVE=True",
    "default CI controller",
    "Mandatory 2.15.x run",
):
    if token not in task9:
        violations.append(f"precedence-regression-token-missing: {token}")

for label, step, marker in (
    (
        "b10",
        "**Step 10: Executable preserved B10 bounded self-check**",
        "b10-preserved-bounded-self-check-pass",
    ),
    (
        "b11",
        "**Step 11: Executable B11 bounded self-check**",
        "b11-bounded-self-check-pass",
    ),
    (
        "b12",
        "**Step 12: Executable B12 bounded self-check**",
        "b12-bounded-self-check-pass",
    ),
):
    if step not in before_b13_check:
        violations.append(f"{label}-preserved-step-missing")
    if marker not in before_b13_check:
        violations.append(f"{label}-preserved-pass-marker-missing")

table_entries = [
    (path, kind)
    for path, _task, kind in re.findall(
        r"^\| `([^`]+)` \| (Task \d+) \| (Create|Modify) \|$",
        text,
        re.MULTILINE,
    )
]
created_count = sum(kind == "Create" for _path, kind in table_entries)
modified_count = sum(kind == "Modify" for _path, kind in table_entries)
fixture_count = sum(
    "/tests/unit/fixtures/collisions/" in path
    or "/tests/unit/fixtures/allowlists/" in path
    for path, _kind in table_entries
)
if (created_count, modified_count, fixture_count) != (33, 7, 17):
    violations.append(
        "ownership-totals-changed: "
        f"created={created_count} modified={modified_count} "
        f"fixtures={fixture_count}"
    )

if violations:
    print("\n".join(violations))
    sys.exit(1)
print(
    "b13-preserved-bounded-self-check-pass "
    "created=33 modified=7 fixtures=17 "
    "neutral_fixture_input=complete runtime_fact_seed=complete "
    "red_green_evidence=complete "
    "precedence_regression=complete "
    "b10_check=preserved b11_check=preserved b12_check=preserved"
)
PY
```

**Step 14: B14 fixture-test AST manifest and call-order gate**

This is the authoritative fixture-coverage proof. It parses every Task 6
Python fence; comments, strings, decorators, and prose cannot satisfy a
function-body call requirement.

```bash
python - <<'PY'
import ast
import pathlib
import re
import sys

plan = pathlib.Path(
    "docs/plans/"
    "2026-07-28-r3-01b-finalization-register-clobbers-"
    "implementation-plan-b14.md"
)
text = plan.read_text(encoding="utf-8")
task6 = text.split("## Task 6:", 1)[1].split("## Task 7:", 1)[0]

required_manifest = [
    "test_injected_fixture_preserved",
    (
        "test_execute_mode_runtime_fixture_preserved_"
        "without_secondary_restore_list"
    ),
    (
        "test_execute_mode_runtime_fixture_preserved_"
        "with_secondary_restore_list"
    ),
    "test_runtime_fixture_fails_closed_on_bad_shape",
]
builder_names = {
    "_dry_run_normal_vars",
    "_execute_normal_vars",
}
violations = []
functions = {}

for fence_match in re.finditer(r"```python\n(.*?)```", task6, re.DOTALL):
    code = fence_match.group(1)
    base_line = task6[: fence_match.start(1)].count("\n")
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        violations.append(
            f"task6-python-fence-syntax-error: "
            f"line={base_line + exc.lineno} {exc.msg}"
        )
        continue
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name in functions:
            violations.append(f"duplicate-function-definition: {node.name}")
        functions[node.name] = (node, base_line)


def body_nodes(function):
    for statement in function.body:
        yield from ast.walk(statement)


def call_name(node):
    if not isinstance(node, ast.Call):
        return None
    return node.func.id if isinstance(node.func, ast.Name) else None


discovered = []
for name, (function, _base_line) in functions.items():
    fixture_builder_calls = [
        node
        for node in body_nodes(function)
        if call_name(node) in builder_names
        and any(
            keyword.arg == "old_hub_fixture"
            for keyword in node.keywords
        )
    ]
    if fixture_builder_calls:
        discovered.append(name)

if set(discovered) != set(required_manifest) or len(discovered) != 4:
    violations.append(
        "fixture-function-manifest-mismatch: "
        f"expected={required_manifest} actual={discovered}"
    )

for name in required_manifest:
    function_record = functions.get(name)
    if function_record is None:
        violations.append(f"fixture-function-missing: {name}")
        continue
    function, base_line = function_record
    nodes = list(body_nodes(function))

    builder_assignments = []
    for node in nodes:
        if not isinstance(node, ast.Assign):
            continue
        if call_name(node.value) not in builder_names:
            continue
        if not any(
            keyword.arg == "old_hub_fixture"
            for keyword in node.value.keywords
        ):
            continue
        target_names = {
            target.id
            for target in node.targets
            if isinstance(target, ast.Name)
        }
        if target_names == {"variables"}:
            builder_assignments.append(node)
    if len(builder_assignments) != 1:
        violations.append(
            f"fixture-builder-assignment-count: {name}: "
            f"{len(builder_assignments)}"
        )

    helper_calls = [
        node
        for node in nodes
        if call_name(node) == "_assert_runtime_fixture_input"
        and len(node.args) == 1
        and isinstance(node.args[0], ast.Name)
        and node.args[0].id == "variables"
        and not node.keywords
    ]
    if len(helper_calls) != 1:
        violations.append(
            f"fixture-helper-call-count: {name}: {len(helper_calls)}"
        )

    all_run_calls = [
        node for node in nodes if call_name(node) == "_run"
    ]
    exact_run_calls = [
        node
        for node in all_run_calls
        if len(node.args) == 2
        and isinstance(node.args[0], ast.Name)
        and node.args[0].id == "tmp_path"
        and isinstance(node.args[1], ast.Name)
        and node.args[1].id == "variables"
        and not node.keywords
    ]
    if len(all_run_calls) != 1 or len(exact_run_calls) != 1:
        violations.append(
            f"fixture-run-call-contract: {name}: "
            f"all={len(all_run_calls)} exact={len(exact_run_calls)}"
        )
    if helper_calls and exact_run_calls:
        helper_line = base_line + helper_calls[0].lineno
        run_line = base_line + exact_run_calls[0].lineno
        if helper_line >= run_line:
            violations.append(
                f"fixture-helper-not-before-run: {name}: "
                f"helper_line={helper_line} run_line={run_line}"
            )

malformed_record = functions.get(
    "test_runtime_fixture_fails_closed_on_bad_shape"
)
case_ids = []
if malformed_record is not None:
    malformed_function = malformed_record[0]
    parametrizers = []
    for decorator in malformed_function.decorator_list:
        if not isinstance(decorator, ast.Call):
            continue
        source = ast.unparse(decorator.func)
        if source == "pytest.mark.parametrize":
            parametrizers.append(decorator)
    if len(parametrizers) != 1 or len(parametrizers[0].args) < 2:
        violations.append("malformed-parametrize-decorator-contract")
    else:
        try:
            cases = ast.literal_eval(parametrizers[0].args[1])
            case_ids = [case[1] for case in cases]
        except (ValueError, TypeError, IndexError) as exc:
            violations.append(f"malformed-case-list-parse-failed: {exc}")

expected_case_ids = [
    "top-level-non-mapping",
    "missing-resources",
    "skipped-result",
    "empty-mapping-resources",
    "nonempty-mapping-resources",
    "string-resources",
    "number-resources",
    "null-resources",
    "malformed-list-entry",
]
if case_ids != expected_case_ids:
    violations.append(
        f"malformed-case-ids-mismatch: "
        f"expected={expected_case_ids} actual={case_ids}"
    )

if violations:
    print("\n".join(violations))
    sys.exit(1)
print("fixture-function-manifest:", ",".join(required_manifest))
print("fixture-call-order: helper_before_first_run=true mapping=variables")
print("b14-fixture-test-ast-gate-pass functions=4 malformed_cases=9")
PY
```

**Step 15: Final B14 bounded self-check**

Run this only after Steps 9–14 have exited zero in the same evidence run. It
proves the two-hub AST/runtime assertion contract, Task 9's complete module,
unchanged ownership, and all preserved gate markers.

```bash
python - <<'PY'
import ast
import pathlib
import re
import sys

plan = pathlib.Path(
    "docs/plans/"
    "2026-07-28-r3-01b-finalization-register-clobbers-"
    "implementation-plan-b14.md"
)
text = plan.read_text(encoding="utf-8")
task6 = text.split("## Task 6:", 1)[1].split("## Task 7:", 1)[0]
task9 = text.split("## Task 9:", 1)[1].split("## Task 10:", 1)[0]
step9 = text.split("**Step 9: Occurrence-exact", 1)[1].split(
    "**Step 10:",
    1,
)[0]
before_b14_check = text.split(
    "**Step 15: Final B14 bounded self-check**",
    1,
)[0]
violations = []


def parse_python_fences(section, label):
    functions = {}
    trees = []
    for match in re.finditer(r"```python\n(.*?)```", section, re.DOTALL):
        try:
            tree = ast.parse(match.group(1))
        except SyntaxError as exc:
            violations.append(f"{label}-python-fence-syntax-error: {exc}")
            continue
        trees.append(tree)
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name in functions:
                    violations.append(
                        f"{label}-duplicate-function: {node.name}"
                    )
                functions[node.name] = node
    return functions, trees


task6_functions, task6_trees = parse_python_fences(task6, "task6")
task9_functions, task9_trees = parse_python_fences(task9, "task9")

execute_builder = task6_functions.get("_execute_normal_vars")
direct_builder = task6_functions.get("_direct_candidate_vars")
if execute_builder is None:
    violations.append("two-hub-execute-builder-missing")
else:
    positional = [arg.arg for arg in execute_builder.args.args]
    keyword_only = [arg.arg for arg in execute_builder.args.kwonlyargs]
    if positional != ["tmp_path", "primary_hub", "secondary_hub"]:
        violations.append(
            f"two-hub-execute-signature: {positional}"
        )
    if keyword_only != [
        "old_hub_fixture",
        "secondary_restore_preseed",
        "mch_preseed",
    ]:
        violations.append(
            f"two-hub-execute-keyword-signature: {keyword_only}"
        )
    source = ast.unparse(execute_builder)
    for token in (
        "assert primary_hub is not secondary_hub",
        "assert primary_hub.url != secondary_hub.url",
        "server=primary_hub.url",
        "server=secondary_hub.url",
    ):
        if token not in source:
            violations.append(
                f"two-hub-execute-builder-token-missing: {token}"
            )

if direct_builder is None:
    violations.append("two-hub-direct-builder-missing")
else:
    positional = [arg.arg for arg in direct_builder.args.args]
    if positional != [
        "tmp_path",
        "primary_hub",
        "secondary_hub",
        "candidate",
    ]:
        violations.append(f"two-hub-direct-signature: {positional}")
    source = ast.unparse(direct_builder)
    for token in (
        "assert primary_hub is not secondary_hub",
        "assert primary_hub.url != secondary_hub.url",
        "server=primary_hub.url",
        "server=secondary_hub.url",
    ):
        if token not in source:
            violations.append(
                f"two-hub-direct-builder-token-missing: {token}"
            )


def body_nodes(function):
    for statement in function.body:
        yield from ast.walk(statement)


def call_name(node):
    if not isinstance(node, ast.Call):
        return None
    return node.func.id if isinstance(node.func, ast.Name) else None


execute_tests = []
for name, function in task6_functions.items():
    if name.startswith("test_") and any(
        call_name(node) == "_execute_normal_vars"
        for node in body_nodes(function)
    ):
        execute_tests.append(name)
        fake_contexts = 0
        for node in body_nodes(function):
            if not isinstance(node, ast.With):
                continue
            fake_contexts = max(
                fake_contexts,
                sum(
                    1
                    for item in node.items
                    if call_name(item.context_expr) == "FakeAcmBackupHub"
                ),
            )
        if fake_contexts != 2:
            violations.append(
                f"execute-test-not-two-hub: {name}: {fake_contexts}"
            )

expected_execute_tests = {
    "test_task6_scenario_builder_matrix",
    (
        "test_execute_mode_runtime_fixture_preserved_"
        "without_secondary_restore_list"
    ),
    (
        "test_execute_mode_runtime_fixture_preserved_"
        "with_secondary_restore_list"
    ),
    "test_execute_mode_live_routes_to_physical_hubs",
    "test_execute_mode_old_hub_get_failure_is_primary_only",
}
if set(execute_tests) != expected_execute_tests:
    violations.append(
        f"execute-test-manifest: {sorted(execute_tests)}"
    )

for token in (
    "primary_hub.old_hub_restore_named_get_hits.get(",
    "secondary_hub.old_hub_restore_named_get_hits.get(",
    "primary_hub.old_hub_restore_named_get_hits == {}",
    "secondary_hub.old_hub_restore_named_get_hits == {}",
    "primary_hub.secondary_mch_list_hits == 0",
    "primary_hub.secondary_restore_list_hits == 0",
    "secondary_hub.secondary_mch_list_hits >= 1",
    "secondary_hub.secondary_restore_list_hits == 0",
    "secondary_hub.secondary_restore_list_hits >= 1",
    "primary_hub.secondary_restore_delete_hits == 0",
    "secondary_hub.secondary_restore_delete_hits == 0",
    "FAKE_RESTORE_GET_FAILURE:restore-acm-passive-sync",
):
    if token not in task6:
        violations.append(f"cross-hub-counter-token-missing: {token}")

run_definitions = [
    node
    for tree in task9_trees
    for node in tree.body
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    and node.name == "_run"
]
run_calls = [
    node
    for tree in task9_trees
    for node in ast.walk(tree)
    if call_name(node) == "_run"
]
exact_run_calls = [
    node
    for node in run_calls
    if len(node.args) == 2
    and isinstance(node.args[0], ast.Name)
    and node.args[0].id == "tmp_path"
    and isinstance(node.args[1], ast.Name)
    and node.args[1].id == "variables"
    and not node.keywords
]
if len(run_definitions) != 1:
    violations.append(
        f"task9-run-definition-count: {len(run_definitions)}"
    )
if len(run_calls) != 1 or len(exact_run_calls) != 1:
    violations.append(
        f"task9-run-call-contract: all={len(run_calls)} "
        f"exact={len(exact_run_calls)}"
    )
for token in (
    "from ansible_collections.tomazb.acm_switchover.tests.conftest import (",
    "_ansible_env,",
    "env = _ansible_env(REPO_ROOT, tmp_path)",
    'env["ANSIBLE_COLLECTIONS_PATH"] = os.environ.get(',
    'env["ANSIBLE_COLLECTIONS_SCAN_SYS_PATH"] = "false"',
    "env=env",
):
    if token not in task9:
        violations.append(f"task9-complete-harness-token-missing: {token}")

expected_output_assertions = {
    "SEED_FIRED=False",
    "HAS_RESOURCES_KEY=False",
    "RUNTIME_FACT_CLOBBERED=True",
    "EXTRA_VAR_REMAINS_AUTHORITATIVE=True",
}
output_assertions = {
    node.value
    for tree in task9_trees
    for assertion in ast.walk(tree)
    if isinstance(assertion, ast.Assert)
    for node in ast.walk(assertion.test)
    if isinstance(node, ast.Constant)
    and isinstance(node.value, str)
    and node.value in expected_output_assertions
}
if output_assertions != expected_output_assertions:
    violations.append(
        "task9-output-assertions: "
        f"expected={sorted(expected_output_assertions)} "
        f"actual={sorted(output_assertions)}"
    )

table_entries = [
    (path, kind)
    for path, _task, kind in re.findall(
        r"^\| `([^`]+)` \| (Task \d+) \| (Create|Modify) \|$",
        text,
        re.MULTILINE,
    )
]
created_count = sum(kind == "Create" for _path, kind in table_entries)
modified_count = sum(kind == "Modify" for _path, kind in table_entries)
fixture_count = sum(
    "/tests/unit/fixtures/collisions/" in path
    or "/tests/unit/fixtures/allowlists/" in path
    for path, _kind in table_entries
)
if (created_count, modified_count, fixture_count) != (33, 7, 17):
    violations.append(
        f"ownership-totals: created={created_count} "
        f"modified={modified_count} fixtures={fixture_count}"
    )

if "set(" + "tasks)" in step9:
    violations.append("occurrence-audit-collapses-task-multiplicity")

for marker in (
    "commit-presence-uniqueness-boundary-scope-gate-pass",
    "b10-preserved-bounded-self-check-pass",
    "b11-bounded-self-check-pass",
    "b12-bounded-self-check-pass",
    "b13-preserved-bounded-self-check-pass",
    "b14-fixture-test-ast-gate-pass",
):
    if marker not in before_b14_check:
        violations.append(f"required-prior-marker-missing: {marker}")

if violations:
    print("\n".join(violations))
    sys.exit(1)
print("dual-hub-execute-test-manifest:", ",".join(sorted(execute_tests)))
print("task9-complete-harness-gate-pass run_defs=1 run_calls=1 outputs=4")
print("b14-bounded-self-check-pass created=33 modified=7 fixtures=17")
PY
```

---

## Task 12: PR-preparation gate (requires separate approval before opening a PR)

Do **not** open a PR until separately authorized. When authorized:

- Run the `code-review` skill against the branch diff; address all critical/warning findings or record a concrete technical reason; re-run after changes.
- Run two focused full-diff reviews after all corrections. Record every finding,
  disposition, and follow-up edit from review 1; then run review 2 against the
  corrected exact head. Do not proceed unless the combined disposition is
  complete and both totals are exactly:

  ```text
  unresolved_blocking_findings=0
  unresolved_nonblocking_findings=0
  ```

- PR body includes: bound identifiers (`R3-01b-DESIGN-B2`, `R3-01b-PLAN-B14`), base `0bf55db9`, seeded-artifact hashes (Task 1), all Task 0 bootstrap evidence, all four isolated ansible-core 2.15 dependency/test evidence records (Task 9), and all Task 11 gate outputs.
- The PR resolves `R3-01b` only; it does **not** close issue #202, does **not** change tracker status beyond `in_progress`, and does **not** claim `ready_for_review` or merge credit within this plan's scope.

---

## Verification checklist (definition of done for the slice)

- [ ] Pre-start re-baseline gate passed (`origin/ansible == 0bf55db9`).
- [ ] Task 0 default bootstrap completed before any Task 3/5/6 smoke or targeted integration run: `.venv` installed both `requirements.txt` and `requirements-dev.txt`; `kubernetes.core` was installed from the collection `requirements.yml` into `.venv/collections`; `ANSIBLE_COLLECTIONS_PATH` is exactly repository root plus that isolated directory; sys-path scanning is disabled; Python `kubernetes` import, installed collection directory, `ansible-playbook --version`, `ansible-galaxy collection list kubernetes.core`, and `ansible-doc kubernetes.core.k8s_info` all passed with outputs recorded.
- [ ] Approved design + plan seeded and committed into the worktree with recorded hashes.
- [ ] Only the `R3-01b` tracker row set to `in_progress`; #202 remains separate/open; no `ready_for_review`/merge credit.
- [ ] Finalization dry-run preview reports the real `restore_count`/`restore_names`.
- [ ] Candidate → staged-validate → publish: malformed data never published; undefined/non-mapping/empty+non-empty mapping `resources`/skipped/malformed all reach the sanitized `fail_msg`; deliberate `{resources: []}` succeeds.
- [ ] Task 3 fake API: context-manager cleanup; generic named Restore GET returning valid Kubernetes objects; 404 Status for absent names; DELETE with state removal; thread-safe `restore_names`; `list_failures`/`get_failures`/`delete_failures`; aggregate/per-name counters increment immediately after route matching and before failure injection; stable LIST/GET/DELETE HTTP 500 Status markers; Restore discovery advertises exactly `["get", "list", "delete"]`; normally collected discovery and CRUD tests retain GET→DELETE→LIST omission→GET 404→DELETE 404, route-specific delete failure, retained state after injected DELETE failure, and counter coverage.
- [ ] Task 3 temporary `k8s_info` smoke required Python `kubernetes` and `ansible-doc` resolution first, ran `ansible-playbook` with `ANSIBLE_COLLECTIONS_PATH="$PWD:$PWD/.venv/collections"` and `ANSIBLE_COLLECTIONS_SCAN_SYS_PATH=false`, returned the two seeded resources, then deleted every smoke artifact and confirmed none remained in `git status --porcelain=v1 --untracked-files=all`.
- [ ] Canonical fixture contract: `restore_fixture` always supplies `apiVersion`, `kind`, and metadata name/namespace/resourceVersion; `multiclusterhub_fixture` supplies the same Kubernetes identity plus MCH status; the collected fixture-contract test passes; every fake API and valid execute-mode Restore/MCH fixture uses those helpers, while deliberately malformed validator-only mappings remain explicit.
- [ ] Task 5 defines `_execute_vars(tmp_path, hub, *, stale_seed=())` exactly once; all five R3-A2 execute-mode calls pass `tmp_path` and `hub`; no call omits `tmp_path`; Task 6 retains its separate `_execute_normal_vars` harness.
- [ ] Task 5 imports the repository `_ansible_env`, constructs `env` before its sole subprocess, preserves an explicitly exported `ANSIBLE_COLLECTIONS_PATH`, defaults to repository root plus `.venv/collections`, disables sys-path collection scanning, retains `ACM_ANSIBLE_PLAYBOOK_BIN`/`ACM_ANSIBLE_PYTHON`, and passes `env=env`.
- [ ] R3-A2 execute tests: separate candidate-only cleanup scenario, unexpected-Restore blocker scenario (zero DELETEs), and three API-failure scenarios (LIST/named GET/DELETE), each with a positive failing-route counter, expected prerequisite/later counters, and stable marker; injected DELETE failure retains the resource in fake state.
- [ ] Task 6 defines exactly one complete `_run(tmp_path, variables)` subprocess harness before its scenario builders; all eight Task 6 call sites pass `tmp_path` and a variables mapping; no Task 5 test-module dependency exists; environment-selected ansible-core 2.15 execution remains supported.
- [ ] Task 6 imports the repository `_ansible_env`, constructs `env` before its sole subprocess, preserves an explicitly exported `ANSIBLE_COLLECTIONS_PATH`, defaults to repository root plus `.venv/collections`, disables sys-path collection scanning, retains `ACM_ANSIBLE_PLAYBOOK_BIN`/`ACM_ANSIBLE_PYTHON`, and passes `env=env`.
- [ ] The stale `test_dry_run_seeds_empty_old_hub_restore` definition is replaced by `test_dry_run_seeds_empty_old_hub_restore_candidate`; the seed writes only `_old_hub_existing_restore_candidate: {resources: []}`, is dry-run/no-authoritative-input guarded, and authoritative publication remains a distinct post-validation task.
- [ ] The current-test compatibility search is recorded and re-run; both affected unit files pass the exact two-file reconciliation command, while every no-edit disposition remains source-valid.
- [ ] R3-A3 harness modes are mutually exclusive: normal mode alone runs `tasks_from: discover_resources`; direct mode skips normal discovery, uses role-relative `include_role tasks_from: assert_restore_source_shape`, contains no `COLLECTION_ROOT` dependency, and preserves validate-before-publish ordering. The harness initializes `old_hub_fixture_seeded_by_harness: false`, has exactly one pre-normal-role runtime `set_fact` that copies `acm_test_old_hub_fixture` to `_old_hub_existing_restore_info`, and emits `OLD_HUB_FIXTURE_SEEDED_BY_HARNESS`.
- [ ] Task 6 scenario-builder matrix passes: both normal builders assert `_old_hub_existing_restore_info` is absent from their `-e` mapping and use only `acm_test_old_hub_fixture` for fixture transport; `_execute_normal_vars` and `_direct_candidate_vars` require distinct `primary_hub`/`secondary_hub` objects and URLs and write separate kubeconfigs; every normal-mode builder pre-seeds empty BackupSchedule discovery; dry-run normal mode also pre-seeds empty MCH and secondary Restore discovery; execute runtime-fixture tests pre-seed or omit the secondary Restore source according to route intent; execute no-fixture mode omits secondary Restore, neutral fixture, and authoritative old-hub inputs; direct mode remains on `acm_test_direct_old_hub_candidate` and requires neither BackupSchedule pre-seeding nor API requests.
- [ ] The Task 6 fixture AST gate discovers exactly `test_injected_fixture_preserved`, `test_execute_mode_runtime_fixture_preserved_without_secondary_restore_list`, `test_execute_mode_runtime_fixture_preserved_with_secondary_restore_list`, and `test_runtime_fixture_fails_closed_on_bad_shape`; every builder result is assigned to `variables`, calls `_assert_runtime_fixture_input(variables)` exactly once before `_run(tmp_path, variables)`, and the malformed test carries all nine exact case IDs. No comment, string, decorator, prose token, or alternate mapping name can satisfy the gate.
- [ ] Initial valid-fixture R3-A3 red output proves `NORMAL_MODE_ENTERED=True`, `DIRECT_MODE_ENTERED=False`, `OLD_HUB_FIXTURE_SEEDED_BY_HARNESS=True`, `OLD_HUB_AUTHORITATIVE_DEFINED_BEFORE_NORMAL=True`, `OLD_HUB_SEED_REGISTER_REACHED=True`, `OLD_HUB_RESULT_SKIPPED=True`, and `OLD_HUB_HAS_RESOURCES_KEY=False`; it fails first on the post-fix `OLD_HUB_RESULT_SKIPPED=False` expectation. The no-fixture red test retains the same defect proof with `OLD_HUB_FIXTURE_SEEDED_BY_HARNESS=False`. Both prove all three unrelated sources were pre-seeded and the role completed, rather than failing on kubeconfig/API/prerequisite setup.
- [ ] Post-fix valid runtime-fixture evidence requires `OLD_HUB_RESULT_SKIPPED=False`, `OLD_HUB_HAS_RESOURCES_KEY=True`, and `OLD_HUB_COUNT=1`.
- [ ] Task 6 production ordering helper recognizes both `ansible.builtin.include_tasks` and `include_tasks`, in both scalar and `{file: ...}` forms; it requires exactly one old-hub validation include and retains candidate-before-validation-before-publication plus ownership assertions. The synthetic parameterized regression accepts all four supported forms and rejects publication before validation.
- [ ] Malformed direct candidates fail with sanitized output and emit `DIRECT_MODE_ENTERED=True`, `NORMAL_MODE_ENTERED=False`, `OLD_HUB_AUTHORITATIVE_DEFINED_BEFORE_DIRECT=False`, and `OLD_HUB_AUTHORITATIVE_DEFINED_AFTER_FAILURE=False`; aggregate `request_count` remains exactly zero on both physical fake hubs.
- [ ] Deterministic execute-mode fake-API tests: A2 fresh overrides stale pre-seed, candidate names/count are correct, candidate deletes execute once per route, non-candidates remain, wait observes removal, and each API failure is positively route-attributed. Every A3 execute scenario uses two physical fake hubs. Without a fixture, only primary receives exactly one named old-hub GET and only secondary receives MCH/Restore LISTs. With a fixture and suppressed secondary list, both hubs receive zero named old-hub GETs, only secondary receives MCH LIST, and neither receives Restore LIST on the wrong route. With the secondary list required, only secondary receives positive MCH/Restore LISTs. Primary-only named-GET failure has a positive primary counter, zero secondary named GET, prerequisites only on secondary, no list on primary, zero DELETEs on both, and the stable marker.
- [ ] Task 4 artifacts committed in their own Task 4 commit (not deferred to Task 5).
- [ ] Exact fixture inventory contains only the eleven collision fixtures and six malformed-allowlist fixtures listed in Task 7 and Task 8; nested role-task, role-handler, and playbook boundary structures are generated dynamically under pytest `tmp_path`.
- [ ] All four scanner boundary tests have concrete executable bodies: nested role tasks/handlers and nested playbooks produce exact collection-relative collision tuples; monkeypatched scan calls contain no duplicates and include every expected file exactly once; returned paths are non-absolute and exclude the temporary collection-root prefix. No ellipsis/pass-only body remains.
- [ ] The occurrence-exact commit-boundary audit (Task 11 Step 9) passes with exactly 33 created and seven modified paths: every owned path occurs in exactly one literal `git add`; every Task 1–10 has exactly one literal add and one later literal commit with the exact required message; Tasks 0, 11, and 12 have no literal commit; duplicate same-task/cross-task additions, unexpected paths, extra commands, `git add -A`, and commit-all forms are rejected; ownership table, Task Files, exact status map, created/modified sets, and command boundaries agree bidirectionally.
- [ ] Scanner is literal-only (dynamic names excluded), detects collisions in `tasks`/`handlers`/playbook `post_tasks`, and raises a path-bearing error on parse failure.
- [ ] Allowlist: 2 intentional + 10 debt(#202); non-empty required values, unique `(path,variable)`, no cross-category duplicates, unknown categories rejected, exact #202 reference; malformed-allowlist fixtures rejected automatically.
- [ ] Task 9 contains the complete independent module in one Python fence: imports, exactly one `_run` definition, exactly one `_run(tmp_path, variables)` call, `_ansible_env`, preserved explicit/default collection path, disabled sys-path scanning, `env=env`, and the exact four output assertions. Skipped-register and precedence semantics are confirmed on the default controller and isolated ansible-core 2.15.x: neutral `-e` fixture → runtime `set_fact` → later skipped same-name `register` emits `RUNTIME_FACT_CLOBBERED=True`, while the direct extra-variable control emits `EXTRA_VAR_REMAINS_AUTHORITATIVE=True`. `kubernetes.core` is installed from collection requirements into the isolated path; sys-path scanning stays disabled; Task 5/6 preserve Task 9's exported collection path; version, collection list, `ansible-doc`, and exact three-file results are recorded with no ambient fallback.
- [ ] AGENTS.md documents the collision rule + category-specific allowlist policy.
- [ ] Task 11 exact status gate runs only after a completely clean worktree/index, uses `git diff --name-status --find-renames`, requires 33 `A` plus seven `M`, and rejects missing/deleted/type-changed/unmerged/wrong-status paths, rename/copy, and every unexpected path.
- [ ] Task 11 executable preserved B10 bounded self-check passes: helper/calls, exact 17-fixture agreement across all five ownership surfaces, 33/7 totals, exact status rejection semantics, failure counters/markers, and isolated 2.15 dependency checks.
- [ ] Task 11 executable B11 bounded self-check passes: Task 6 `_run` definition/calls, stale-test replacement, concrete scanner bodies, unchanged 33/7/17 ownership, and every preserved B10 failure/status/dependency gate.
- [ ] Task 11 executable B12 bounded self-check passes after the preserved B10 and B11 checks: complete Task 0 bootstrap, Task 5/6 `_ansible_env` contract, explicit Task 3 smoke environment, preserved Task 9 isolated lane, and unchanged 33/7/17 ownership.
- [ ] Task 11 executable preserved B13 bounded self-check passes after the preserved B10, B11, and B12 checks: neutral fixture inputs, single pre-role runtime-fact seeding, defect-specific red/green evidence, the runtime-fact/extra-variable precedence regression, and unchanged 33/7/17 ownership.
- [ ] Task 11 B14 fixture AST gate prints `b14-fixture-test-ast-gate-pass functions=4 malformed_cases=9`.
- [ ] Task 11 occurrence-exact boundary gate prints `commit-presence-uniqueness-boundary-scope-gate-pass created=33 modified=7 tasks_with_commits=10`.
- [ ] Task 11 final B14 bounded check proves the dual-hub builder/test manifest, cross-hub counters, complete Task 9 harness, every prior marker, unchanged 33/7/17 ownership, and prints `b14-bounded-self-check-pass created=33 modified=7 fixtures=17`.
- [ ] The complete six-finding disposition remains present; both audit defects and fake-hub hardening are accepted; two focused full-diff reviews finish with `unresolved_blocking_findings=0` and `unresolved_nonblocking_findings=0`.
- [ ] All Task 11 gates green; base-aware `git diff --check 0bf55db9..HEAD` clean; exact changed-status/protected-file/scope checks pass.
- [ ] No Python CLI production/test file modified; no `TR2D-02` change; no #202 fix; tracker only `in_progress`.
