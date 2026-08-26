# R3-02 Fail-Closed Verification Implementation Plan

> **For agentic workers:** execute this plan only after explicit operator approval of this plan. Before implementation, read current `AGENTS.md`, the approved R3-02 design, issue #272, current compatibility/parity authorities, and the applicable Superpowers skills. Use `using-git-worktrees`, `test-driven-development`, `verification-before-completion`, and the repository Builder → Independent Validator → PR-comment Resolver workflow. Use Graphify only as a hypothesis generator and verify every inferred relationship against source/tests.

**Status:** Implementation-plan candidate; runtime/test implementation is not yet authorized.  
**Date:** 2026-08-26  
**Approved design:** `r3-02-fail-closed-verification-design-v2@7723260db038e2774513f115fcd00394312e2723`  
**Design base:** `ansible@3dc6778814c1e457b064e97654b6b66f03554119`  
**Governing issue:** #272  
**Findings:** `R3-A4`, `R3-A5`, `GLM-H12`

## Goal

Implement the approved R3-02 design so Collection compactor verification, Collection hub-connectivity reporting, and Collection/Python activation auto-import-strategy verification fail closed on unverified Kubernetes reads. Preserve existing retry budgets, report ownership, check-mode/dry-run behavior, hub/context/namespace targeting, checkpoint/phase semantics, accurate `changed` reporting, Python/Collection parity, and the existing RBAC surface.

## Architecture

Use a hybrid implementation exactly matching the approved design:

1. Add one narrow, read-only Collection support module, `acm_k8s_read_outcome`, for the two decisions where `kubernetes.core.k8s_info` loses information. It performs one read attempt, reuses `kubernetes.core` client/auth construction, and returns only `read_status: ok | not_found | error` plus sanitized resources. It owns no phase policy, reporting, retry policy, or mutation.
2. Keep connectivity on `kubernetes.core.k8s_info`, but make `pass` depend on the exact `default` Namespace result rather than `.failed`.
3. Keep compactor retries owned by the existing `30 x 10s` Ansible task loop. Retain `failed_when: false` (or an exactly equivalent continuation mechanism) so exhausted retries reach the role-owned stable sanitized failure task. Never use `.failed` as verification evidence; the independent validator observed divergent exhausted-register `.failed` values between ansible-core 2.16 and 2.21.
4. Make Collection auto-import defaulting reachable only from an explicit named ConfigMap 404 or a valid present ConfigMap. Any 400/403/discovery/transport/malformed result fails before ManagedCluster discovery or patch.
5. Add Python `get_configmap_advisory()` without changing `get_configmap()`, then make the activation-time immediate-import path raise the same stable `FatalError` on read failure or malformed ConfigMap evidence. This isolates R3-02 from the separate finalization and R4-owned callers of `get_configmap()`.

## Tech stack

- Python 3.10–3.12 for the Python CLI; repository-tested Collection lanes are ansible-core 2.16.* / Python 3.11 and ansible-core 2.21.* / Python 3.12.
- `kubernetes.core >=6.0.0,<7.0.0` and Python `kubernetes>=28.0.0`.
- Ansible roles/modules, `kubernetes.core` public module-utils (`AUTH_ARG_SPEC`, `get_api_client`), Kubernetes dynamic-client exceptions, pytest, fake HTTP Kubernetes APIs, PyYAML, and existing repository validation scripts.

## Non-negotiable implementation constraints

- No protected-file changes: `docs/ACM_SWITCHOVER_RUNBOOK.md` and `.claude/skills/**` remain untouched.
- No RBAC permission expansion. If implementation needs a new verb/resource/namespace permission, stop and return `BLOCKED_SCOPE_EXPANSION`.
- No Python/Collection runtime imports across form factors.
- No new production retry knob, timeout knob, checkpoint field, report-schema field, public CLI/Collection variable, release-profile field, or phase-ordering change.
- No generalized Kubernetes abstraction/framework. The new module has exactly the two R3-02 consumers unless current-base evidence requires otherwise and the operator approves scope expansion.
- No live-cluster mutation or ACM certification claim. Fake API and read-only dependency behavior are non-live/non-certification evidence.
- Newly introduced public failures must be stable and sanitized. Do not copy raw exception text, HTTP response bodies, kubeconfig paths/content, tokens, certificates, or authorization headers into task output/report fields.
- Apply TDD literally: add one failing behavior test, run it and confirm the expected failure, implement the minimum code, rerun green, then refactor while green. Production edits must not precede their failing regression test.

## Expected implementation envelope

### Runtime

- `ansible_collections/tomazb/acm_switchover/plugins/modules/acm_k8s_read_outcome.py` — new.
- `ansible_collections/tomazb/acm_switchover/roles/primary_prep/tasks/scale_observability.yml`.
- `ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/validate_kubeconfigs.yml`.
- `ansible_collections/tomazb/acm_switchover/roles/activation/tasks/apply_immediate_import.yml`.
- `lib/kube_client.py`.
- `modules/activation.py`.

### Tests/harness

- `ansible_collections/tomazb/acm_switchover/tests/unit/test_k8s_read_outcome.py` — new.
- `ansible_collections/tomazb/acm_switchover/tests/unit/test_primary_prep_auto_import.py` — repin defective compactor assertions.
- `ansible_collections/tomazb/acm_switchover/tests/unit/test_activation_auto_import.py` — repin defective auto-import assertions.
- `ansible_collections/tomazb/acm_switchover/tests/unit/test_preflight_connectivity_contract.py` — new focused task contract unless current-base tests already provide the same discriminating assertions.
- `ansible_collections/tomazb/acm_switchover/tests/integration/conftest.py` — test-only runner/kubeconfig support where needed.
- `ansible_collections/tomazb/acm_switchover/tests/integration/r3_02_fake_api.py` — new shared fake Kubernetes API for R3-02 runtime cases.
- `ansible_collections/tomazb/acm_switchover/tests/integration/test_k8s_read_outcome_runtime.py` — new.
- `ansible_collections/tomazb/acm_switchover/tests/integration/test_r3_02_compactor_runtime.py` — new.
- `ansible_collections/tomazb/acm_switchover/tests/integration/test_preflight_role.py` — extend existing real `k8s_info` fake-API coverage.
- `ansible_collections/tomazb/acm_switchover/tests/integration/test_r3_02_activation_runtime.py` — new.
- `ansible_collections/tomazb/acm_switchover/tests/integration/playbooks/run_k8s_read_outcome.yml` — new.
- `ansible_collections/tomazb/acm_switchover/tests/integration/playbooks/run_r3_02_primary_prep.yml` — new, using `include_role: tasks_from: scale_observability`.
- `ansible_collections/tomazb/acm_switchover/tests/integration/playbooks/run_r3_02_activation.yml` — new, using `include_role: tasks_from: apply_immediate_import`.
- `tests/test_kube_client.py`.
- `tests/test_activation.py`.
- `tests/test_r3_02_fail_closed_parity.py` — new minimal cross-form-factor guardrail if it adds discriminating value without importing Ansible runtime code.

### Documentation/state

- `thermos-resolution-plan.md` — correct the now-disproved R3-02 mechanism text and advance only the R3-02 row/state appropriate to the implementation branch.
- `CHANGELOG.md` `[Unreleased]` — add the operator-visible fail-closed correction.
- Approved design and this implementation plan must be present in the future implementation branch/PR, but do not amend the approved design except through a separately approved design revision.

Do not expand this file envelope for unrelated cleanup. If a directly affected existing test/harness has a different current owner at implementation time, use the existing owner rather than creating a duplicate test subsystem.

---

## Task 0 — Bind the future implementation to current `origin/ansible`

**Files:** no runtime/test edit yet. The implementation branch will carry the approved design and approved plan documents plus the tracker update described below.

### Step 0.1 — Re-run the mandatory start gate

From the repository root:

```bash
git fetch origin ansible r3-02-fail-closed-verification-design-v2 r3-02-fail-closed-verification-plan
printf 'repo=%s\n' "$(git remote get-url origin)"
printf 'base=%s\n' "$(git rev-parse origin/ansible)"
printf 'design=%s\n' "$(git rev-parse origin/r3-02-fail-closed-verification-design-v2)"
printf 'plan=%s\n' "$(git rev-parse origin/r3-02-fail-closed-verification-plan)"
```

Required design SHA remains:

```text
7723260db038e2774513f115fcd00394312e2723
```

The approved plan SHA is the exact plan head recorded by the operator after reviewing this document. Do not infer approval from a mutable branch name.

Read current:

```text
AGENTS.md
GitHub issue #272
approved R3-02 design
this approved implementation plan
thermos-resolution-plan.md
docs/ansible-collection/parity-matrix.md
docs/ansible-collection/behavior-map.md
ansible_collections/tomazb/acm_switchover/docs/coexistence.md
ansible_collections/tomazb/acm_switchover/docs/compatibility.md
```

Hard-fail if the repository identity, target branch, authorization, or required evidence is unavailable.

### Step 0.2 — Create the isolated implementation worktree from fresh base

```bash
git worktree add .claude/worktrees/r3-02-implementation \
  -b r3-02-fail-closed-verification-implementation \
  origin/ansible
cd .claude/worktrees/r3-02-implementation
git status --porcelain=v1
```

Expected status: empty.

Record before first edit:

```bash
BASE=$(git rev-parse origin/ansible)
HEAD=$(git rev-parse HEAD)
MERGE_BASE=$(git merge-base HEAD origin/ansible)
printf 'BASE=%s\nHEAD=%s\nMERGE_BASE=%s\n' "$BASE" "$HEAD" "$MERGE_BASE"
```

`BASE`, `HEAD`, and `MERGE_BASE` must initially match.

### Step 0.3 — Revalidate drift since the approved design base

If current `origin/ansible` is not `3dc6778814c1e457b064e97654b6b66f03554119`, compare all directly affected runtime/test/authority files against the design base before continuing. At minimum inspect:

```bash
git diff 3dc6778814c1e457b064e97654b6b66f03554119..origin/ansible -- \
  AGENTS.md \
  thermos-resolution-plan.md \
  modules/activation.py \
  lib/kube_client.py \
  ansible_collections/tomazb/acm_switchover/roles/primary_prep/tasks/scale_observability.yml \
  ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/validate_kubeconfigs.yml \
  ansible_collections/tomazb/acm_switchover/roles/activation/tasks/apply_immediate_import.yml \
  ansible_collections/tomazb/acm_switchover/docs/compatibility.md \
  docs/ansible-collection/parity-matrix.md
```

If relevant source/dependency/authority drift invalidates a design assumption, stop for design reconciliation. Do not silently adapt a safety contract inside implementation.

### Step 0.4 — Bring the approved governance artifacts into the implementation branch

After the operator has approved this plan and recorded the exact plan head:

```bash
git cherry-pick 7723260db038e2774513f115fcd00394312e2723
git cherry-pick <exact-operator-approved-plan-head>
```

The second command intentionally uses the operator-recorded exact plan SHA; do not replace it with a moving branch ref.

Verify the base-relative diff at this point contains only the two unprotected plan/design docs:

```bash
git diff --name-status origin/ansible...HEAD
git diff --name-only origin/ansible...HEAD | \
  grep -E '^(docs/ACM_SWITCHOVER_RUNBOOK\.md|\.claude/skills/)' && exit 1 || true
```

### Step 0.5 — Mark only R3-02 implementation `in_progress`

Update `thermos-resolution-plan.md` immediately after the design/plan cherry-picks:

- update `Last Updated`;
- move the owning R3-02 implementation row/state from `planned` to `in_progress` according to the current tracker structure;
- replace the outdated R3-02 Resolution claim that `resources is defined` can distinguish error from absence;
- record the approved mechanism: exact positive Namespace evidence for connectivity and `acm_k8s_read_outcome` for compactor/auto-import;
- record that `k8s_info` normalizes a valid empty list, named 404, and BadRequest/400 to the same `api_found: true`, `resources: []` shape across the governed 6.x dependency range;
- record that a normalized 400 in the current auto-import path can proceed to the default-ImportOnly mutation branch, not merely skip;
- do not claim implementation or test evidence yet.

Run:

```bash
git diff --check
git diff --name-only origin/ansible...HEAD | \
  grep -E '^(docs/ACM_SWITCHOVER_RUNBOOK\.md|\.claude/skills/)' && exit 1 || true
```

Commit:

```bash
git add thermos-resolution-plan.md
git commit -m "docs: start R3-02 fail-closed implementation"
```

---

## Task 1 — Build the Collection read-outcome primitive with TDD

**Files:**

- Create `ansible_collections/tomazb/acm_switchover/tests/unit/test_k8s_read_outcome.py`.
- Create `ansible_collections/tomazb/acm_switchover/plugins/modules/acm_k8s_read_outcome.py`.

### Step 1.1 — Read the test-writing guidance before editing tests

Read current Superpowers `test-driven-development` and `writing-good-tests` guidance, then identify the production behavior each test will force.

### Step 1.2 — RED: add the semantic contract tests first

Test pure/module helper behavior using mocked `kubernetes.core` client construction/dynamic resources, not source-text inspection. Cover one behavior per test:

1. successful list with no objects => `read_status == "ok"`, `resources == []`;
2. successful list with objects => `ok`, resources preserved as dictionaries;
3. named get present => `ok`, exactly one dictionary resource;
4. named get raises explicit `NotFoundError`/404 => `not_found`, `resources == []`;
5. list raises 404 => `error`, never `not_found`;
6. `BadRequestError`/400 => `error`;
7. `ForbiddenError`/403 => `error`;
8. resource discovery `ResourceNotFoundError` => `error`;
9. timeout/connection/transport exception => `error`;
10. client/auth construction `CoreException` => `error`;
11. malformed/unexpected list/get response => `error`;
12. sensitive sentinel embedded in exception/body does not appear in the public result;
13. every returned path is read-only and `changed` is false at the module boundary.

Run RED:

```bash
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_k8s_read_outcome.py -q
```

Expected: failures because the new module/contract does not yet exist. If tests pass or fail for import/setup reasons instead of the intended missing behavior, correct the tests and rerun RED.

### Step 1.3 — GREEN: implement only the narrow module contract

`acm_k8s_read_outcome.py` should follow the existing Collection module style and include `DOCUMENTATION`, `EXAMPLES`, and `RETURN`.

Use the public `kubernetes.core` surfaces already proven by independent validation:

```python
from copy import deepcopy

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.kubernetes.core.plugins.module_utils.args_common import AUTH_ARG_SPEC
from ansible_collections.kubernetes.core.plugins.module_utils.k8s.client import get_api_client
```

Use `deepcopy(AUTH_ARG_SPEC)` and add only:

```text
read_mode: get | list
api_version: string
kind: string
namespace: optional string
name: required for read_mode=get
label_selectors: list[string], list mode only
```

Use `supports_check_mode=True`. The module performs no mutation regardless of check mode.

Use `get_api_client(module)`, `api_client.resource(kind, api_version)`, and exactly one `api_client.get(...)` call per invocation. Do not call `K8sService.find()` and do not add retry logic.

The classifier must preserve these semantics:

```text
named get + explicit NotFound/404  -> not_found
successful get/list                -> ok
all other exceptions/unverifiable -> error
```

For `error`, catch and discard raw exception details. Never put `str(exc)`, response body, headers, or kubeconfig data into `exit_json` fields. A fixed internal reason code is unnecessary unless a test proves it adds discriminating value; `read_status` is the role contract.

Normalize successful resources to plain dictionaries. A malformed successful response becomes `error` rather than an exception leak.

Every normal return uses:

```python
module.exit_json(changed=False, read_status=read_status, resources=resources)
```

Do not use `module.fail_json()` for expected API/read failures: the caller needs a structured `error` result for the compactor retry loop and activation failure barrier. Unexpected programmer/setup failures should still be caught at this boundary and converted to sanitized `error` unless doing so would hide a contract violation detectable only by tests.

### Step 1.4 — Verify GREEN and refactor only while green

```bash
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_k8s_read_outcome.py -q
```

Then run directly affected collection module/unit sanity where available:

```bash
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_compatibility_contract.py \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_k8s_read_outcome.py -q
```

Commit:

```bash
git add \
  ansible_collections/tomazb/acm_switchover/plugins/modules/acm_k8s_read_outcome.py \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_k8s_read_outcome.py
git commit -m "feat(collection): add lossless Kubernetes read outcome"
```

---

## Task 2 — Prove the new module through the real Ansible execution path

**Files:**

- Create `ansible_collections/tomazb/acm_switchover/tests/integration/r3_02_fake_api.py`.
- Create `ansible_collections/tomazb/acm_switchover/tests/integration/playbooks/run_k8s_read_outcome.yml`.
- Create `ansible_collections/tomazb/acm_switchover/tests/integration/test_k8s_read_outcome_runtime.py`.
- Modify `ansible_collections/tomazb/acm_switchover/tests/integration/conftest.py` only for reusable test-runner/kubeconfig helpers; do not add production switches.

### Step 2.1 — RED: write real Ansible module execution tests

The fake server must implement only the Kubernetes discovery/read endpoints required by the test and expose selectable response scenarios. Reuse the existing `ThreadingHTTPServer` pattern in `test_preflight_role.py` rather than introducing an external test service.

Required runtime cases:

- list 200 with zero items => `ok`, count 0;
- named get 200 => `ok`, count 1;
- named 404 => `not_found`;
- list 404 => `error`;
- 400 => `error`;
- 403 with a sentinel response body => `error` and sentinel absent from callback output;
- connection/transport failure => `error`;
- unmappable API resource => `error`;
- `--check` => still performs the read, reports `changed=0`, no write request recorded.

`run_k8s_read_outcome.yml` must call the shipped FQCN `tomazb.acm_switchover.acm_k8s_read_outcome`; do not import a test copy of the module.

Run RED before adding/finalizing integration support:

```bash
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/integration/test_k8s_read_outcome_runtime.py -q
```

Confirm the initial failure is due to missing runtime wiring/behavior, not fixture syntax.

### Step 2.2 — GREEN: make only the integration harness changes required

Keep the fake API reusable for Tasks 3–5. It should maintain request counters so later tests can prove ordering and zero post-barrier mutation.

Do not embed credentials or secrets in fixture output. For sanitization tests, use unmistakable sentinels such as `R302-SENTINEL-HTTP-BODY` and assert they never appear in `stdout + stderr`.

Run GREEN:

```bash
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_k8s_read_outcome.py \
  ansible_collections/tomazb/acm_switchover/tests/integration/test_k8s_read_outcome_runtime.py -q
```

Commit:

```bash
git add \
  ansible_collections/tomazb/acm_switchover/tests/integration/conftest.py \
  ansible_collections/tomazb/acm_switchover/tests/integration/r3_02_fake_api.py \
  ansible_collections/tomazb/acm_switchover/tests/integration/playbooks/run_k8s_read_outcome.yml \
  ansible_collections/tomazb/acm_switchover/tests/integration/test_k8s_read_outcome_runtime.py
git commit -m "test(collection): exercise lossless read outcome runtime"
```

---

## Task 3 — Make compactor drain verification fail closed

**Files:**

- Modify `ansible_collections/tomazb/acm_switchover/tests/unit/test_primary_prep_auto_import.py`.
- Create `ansible_collections/tomazb/acm_switchover/tests/integration/playbooks/run_r3_02_primary_prep.yml`.
- Create `ansible_collections/tomazb/acm_switchover/tests/integration/test_r3_02_compactor_runtime.py`.
- Modify `ansible_collections/tomazb/acm_switchover/roles/primary_prep/tasks/scale_observability.yml` only after RED is observed.

### Step 3.1 — RED: invert the existing static contract

Replace the current assertions that intentionally pin the defective shape. The new static guardrail must assert:

- Pod verification action is `tomazb.acm_switchover.acm_k8s_read_outcome`;
- `read_mode: list`, `api_version: v1`, `kind: Pod`, existing namespace/selector/primary kubeconfig/context;
- `failed_when: false` remains present — this is load-bearing so exhausted retries reach the role-owned sanitized fail task;
- `retries: 30`, `delay: 10` remain unchanged;
- the `until` predicate requires `read_status == "ok"`, a real list-valued `resources`, and zero length;
- neither `.failed`, `is failed`, nor `resources | default([])` is used as drain proof;
- the read task has `no_log: true`;
- the downstream failure has a stable verification-unavailable message and a separate non-empty Pod-count branch.

Run RED:

```bash
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_primary_prep_auto_import.py -q
```

Expected: the compactor contract test fails against the current `k8s_info` task.

### Step 3.2 — RED: add executable shipped-task-path cases

`run_r3_02_primary_prep.yml` should use:

```yaml
- ansible.builtin.include_role:
    name: tomazb.acm_switchover.primary_prep
    tasks_from: scale_observability
```

Seed only the minimum existing role facts needed to enter the scale/verify path. The fake API must successfully serve the StatefulSet scale interaction and then control the Pod-list outcome.

Required runtime cases:

1. `ok + []` => role succeeds;
2. `ok + non-empty Pods` for the whole wait => bounded exhaustion, existing count-oriented failure;
3. 400 => never satisfies `until`, bounded exhaustion, stable sanitized verification-unavailable failure;
4. 403 with sentinel body => same failure, sentinel absent;
5. connection/transport-style read failure after successful scale => same failure;
6. malformed helper result => fail closed;
7. after any verification refusal, no additional Kubernetes mutation occurs after the refusal barrier.

The production `30 x 10s` literals must remain untouched. To keep tests bounded, use a **test-only controller sleep shim**, not a production variable: have the integration runner add a temporary `sitecustomize.py` directory to the ansible-playbook subprocess `PYTHONPATH` that caps `time.sleep()` to a near-zero value for this focused subprocess. Assert separately in the unit contract that production remains `30` retries and `10` seconds. Do not materialize a modified copy of the production task file.

Run RED:

```bash
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/integration/test_r3_02_compactor_runtime.py -q
```

### Step 3.3 — GREEN: replace only the ambiguous read/predicates

Change the Pod read to the new module in list mode. Keep the existing primary hub, context, namespace, label selector, scale target, retry count, delay, execute/dry-run conditions, and success result publication.

The `until` predicate must be structurally equivalent to:

```text
read_status == "ok"
AND resources is a real list
AND len(resources) == 0
```

Use an Ansible/Jinja list-type check that behaves consistently on both supported lanes; `type_debug == 'list'` is preferred over a broad `sequence` test unless an executable two-lane test proves another predicate equally strict.

Keep:

```yaml
failed_when: false
no_log: true
```

The downstream fail task must branch only on `read_status`/validated resource shape/count. Never interpolate the module's raw `.msg` or use task `.failed`.

Run GREEN:

```bash
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_primary_prep_auto_import.py \
  ansible_collections/tomazb/acm_switchover/tests/integration/test_r3_02_compactor_runtime.py -q
```

Then rerun the read-outcome module tests to catch shared regression:

```bash
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_k8s_read_outcome.py \
  ansible_collections/tomazb/acm_switchover/tests/integration/test_k8s_read_outcome_runtime.py -q
```

Commit:

```bash
git add \
  ansible_collections/tomazb/acm_switchover/roles/primary_prep/tasks/scale_observability.yml \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_primary_prep_auto_import.py \
  ansible_collections/tomazb/acm_switchover/tests/integration/playbooks/run_r3_02_primary_prep.yml \
  ansible_collections/tomazb/acm_switchover/tests/integration/test_r3_02_compactor_runtime.py \
  ansible_collections/tomazb/acm_switchover/tests/integration/conftest.py \
  ansible_collections/tomazb/acm_switchover/tests/integration/r3_02_fake_api.py
git commit -m "fix(collection): fail closed on compactor verification"
```

---

## Task 4 — Make connectivity `pass` require exact Namespace evidence

**Files:**

- Create `ansible_collections/tomazb/acm_switchover/tests/unit/test_preflight_connectivity_contract.py` unless current-base unit ownership already has equivalent discriminating coverage.
- Modify `ansible_collections/tomazb/acm_switchover/tests/integration/test_preflight_role.py`.
- Modify test-only kubeconfig/server override support in `ansible_collections/tomazb/acm_switchover/tests/integration/conftest.py` only if needed to drive primary and secondary independently.
- Modify `ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/validate_kubeconfigs.yml` only after RED.

### Step 4.1 — RED: pin the positive-evidence predicate

The static/unit contract must reject the current `.failed | default(false)` logic and require each hub's pass predicate to prove all of:

```text
registered result is a mapping
api_found == true
resources is a real list
len(resources) == 1
resource is a mapping
resource.kind == "Namespace"
resource.metadata.name == "default"
```

Both probe tasks must use `no_log: true`. Result IDs, severity, context-only details, restore-only primary skip, and report schema remain unchanged.

Run RED:

```bash
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_preflight_connectivity_contract.py -q
```

### Step 4.2 — RED: extend the existing preflight runtime harness

Reuse `run_preflight_fixture` and the `ThreadingHTTPServer` pattern already in `test_preflight_role.py`. If one shared fixture server cannot independently drive primary and secondary outcomes, extend **test-only** override handling to accept separate primary/secondary server URLs while preserving the existing `fixture_kubeconfig_server` fallback for old tests.

Required cases:

- primary exact default Namespace => primary `pass`;
- secondary exact default Namespace => secondary `pass`;
- primary 400 => primary `fail`, result appears in `preflight-report.json`;
- secondary 400 => secondary `fail`, report contains it;
- primary/secondary 403 => `fail`, no sensitive sentinel in callback/report;
- 404/empty result => `fail`;
- `api_found: false`/unmappable resource => `fail`;
- wrong Namespace name/kind/cardinality => `fail`;
- restore-only continues to omit primary connectivity and validate secondary only;
- the report is written before the existing aggregate critical-failure stop.

Run RED against the current task logic:

```bash
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/integration/test_preflight_role.py \
  -k 'connectivity or restore_only' -q
```

The new negative cases must expose the current false `pass` before production YAML is edited.

### Step 4.3 — GREEN: compute explicit per-hub positive-evidence booleans

Keep `kubernetes.core.k8s_info`; add `no_log: true` to both probes. Prefer one local boolean fact per hub so the long predicate is defined once and reused by status/message rather than duplicated.

Do not turn the probe task into an early fatal task. The report owner must still aggregate both findings, write `preflight-report.json`, recompute critical failures, and only then stop.

Run GREEN:

```bash
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_preflight_connectivity_contract.py \
  ansible_collections/tomazb/acm_switchover/tests/integration/test_preflight_role.py -q
```

Commit:

```bash
git add \
  ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/validate_kubeconfigs.yml \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_preflight_connectivity_contract.py \
  ansible_collections/tomazb/acm_switchover/tests/integration/test_preflight_role.py \
  ansible_collections/tomazb/acm_switchover/tests/integration/conftest.py
git commit -m "fix(collection): require positive hub connectivity evidence"
```

---

## Task 5 — Make Collection auto-import strategy verification fail closed

**Files:**

- Modify `ansible_collections/tomazb/acm_switchover/tests/unit/test_activation_auto_import.py`.
- Create `ansible_collections/tomazb/acm_switchover/tests/integration/playbooks/run_r3_02_activation.yml`.
- Create `ansible_collections/tomazb/acm_switchover/tests/integration/test_r3_02_activation_runtime.py`.
- Reuse/extend `ansible_collections/tomazb/acm_switchover/tests/integration/r3_02_fake_api.py`.
- Modify `ansible_collections/tomazb/acm_switchover/roles/activation/tasks/apply_immediate_import.yml` only after RED.

### Step 5.1 — RED: invert the current defective static assertions

Replace the current test assertions that require:

```text
kubernetes.core.k8s_info
failed_when: false
resources is not defined
autoImportStrategy_unavailable
```

with assertions that require:

- `tomazb.acm_switchover.acm_k8s_read_outcome` in named-get mode for `v1/ConfigMap`, `multicluster-engine/import-controller-config`, existing secondary kubeconfig/context;
- `no_log: true` on the capture;
- only `read_status == not_found` enters ConfigMap-absent/default behavior;
- `read_status == error`, missing/unknown status, or malformed `ok` evidence reaches an explicit stable `ansible.builtin.fail` before any ManagedCluster task;
- a valid `ok` response requires exactly one `v1 ConfigMap` with expected name/namespace and `data` absent/null/mapping;
- the current `reason: autoImportStrategy_unavailable` path is absent for execute-mode read failures;
- dry-run and unsupported-ACM-version skips remain.

Run RED:

```bash
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_activation_auto_import.py -q
```

### Step 5.2 — RED: prove failure-before-discovery/mutation through shipped tasks

`run_r3_02_activation.yml` should use:

```yaml
- ansible.builtin.include_role:
    name: tomazb.acm_switchover.activation
    tasks_from: apply_immediate_import
```

Seed `_acm_secondary_supports_auto_import: true`, a supported ACM version, execute mode, and the minimum existing variables. Use the fake API request recorder to distinguish ConfigMap GET, ManagedCluster LIST, and ManagedCluster PATCH.

Required cases:

1. explicit ConfigMap 404 => default `ImportOnly` behavior remains reachable;
2. present valid ConfigMap with absent/null/empty/default/`ImportOnly` data => annotation path remains reachable as today;
3. present `ImportAndSync` => no immediate-import mutation;
4. real-style 400 => stable activation failure **before** ManagedCluster list/patch; this explicitly covers validator NB-2, where the current code can default to ImportOnly and mutate;
5. 403 with sentinel => stable failure, zero ManagedCluster list/patch, no sentinel output;
6. transport/connection failure => same barrier;
7. wrong ConfigMap name/namespace/kind/API version => fail before ManagedCluster work;
8. non-mapping `data` => fail before ManagedCluster work;
9. native `--check` with valid ImportOnly evidence => reads may occur but no ManagedCluster PATCH request is sent.

Run RED:

```bash
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/integration/test_r3_02_activation_runtime.py -q
```

### Step 5.3 — GREEN: add the explicit read/error/evidence barrier

Replace only the ConfigMap read/classification and downstream conditions. Do not redesign the broader activation transaction.

Use the exact public failure message from the approved design:

```text
Unable to verify autoImportStrategy on the destination hub; verify API access and retry.
```

A safe task sequence is:

1. read ConfigMap with `acm_k8s_read_outcome`, `no_log: true`;
2. fail with the exact stable message if status is neither `not_found` nor a valid `ok` object;
3. derive `_apply_immediate_import` from `not_found` or the validated object's strategy;
4. only then list ManagedClusters and clear/apply annotations;
5. publish the existing success result shape.

Do not interpolate raw module error data. Do not add retries here.

Run GREEN:

```bash
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_activation_auto_import.py \
  ansible_collections/tomazb/acm_switchover/tests/integration/test_r3_02_activation_runtime.py -q
```

Commit:

```bash
git add \
  ansible_collections/tomazb/acm_switchover/roles/activation/tasks/apply_immediate_import.yml \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_activation_auto_import.py \
  ansible_collections/tomazb/acm_switchover/tests/integration/playbooks/run_r3_02_activation.yml \
  ansible_collections/tomazb/acm_switchover/tests/integration/test_r3_02_activation_runtime.py \
  ansible_collections/tomazb/acm_switchover/tests/integration/r3_02_fake_api.py
git commit -m "fix(collection): fail closed on auto-import strategy reads"
```

---

## Task 6 — Add Python advisory ConfigMap reads without changing existing callers

**Files:**

- Modify `tests/test_kube_client.py` first.
- Modify `lib/kube_client.py` second.

### Step 6.1 — RED: add `get_configmap_advisory()` contract tests

Add a focused test class following existing KubeClient retry/advisory patterns. Required behavior:

- present ConfigMap => returned dictionary;
- explicit 404 => `None`;
- non-retryable 403 => exception propagates;
- retryable 5xx/transport exception uses the existing advisory retry predicate and bounded attempt count;
- advisory retries/final path do not log exception strings containing a sensitive sentinel;
- input validation remains the same as `get_configmap()`;
- `get_configmap()` itself is unchanged and its existing tests remain green.

For retry-bound tests, disable Tenacity sleeping through the decorated function's test hook/`retry` object rather than changing production wait constants.

Run RED:

```bash
python -m pytest tests/test_kube_client.py -k 'configmap and advisory' -q
```

### Step 6.2 — GREEN: mirror the existing custom-resource advisory structure

In `lib/kube_client.py`, add a raw one-attempt helper adjacent to ConfigMap helpers:

```text
_get_configmap_raw(namespace, name)
```

It validates inputs, calls `core_v1.read_namespaced_config_map`, maps only `ApiException(status=404)` to `None`, and re-raises everything else without logging rendered exception detail.

Add:

```text
@retry_api_call_advisory
get_configmap_advisory(namespace, name)
```

which delegates to `_get_configmap_raw`.

Do **not** change the existing `get_configmap()` decorator/body. That isolation preserves current preflight/finalization/R4-owned behavior outside R3-02.

Run GREEN:

```bash
python -m pytest tests/test_kube_client.py -k 'configmap or advisory' -q
python -m pytest tests/test_kube_client.py -q
```

Commit:

```bash
git add lib/kube_client.py tests/test_kube_client.py
git commit -m "feat(python): add advisory ConfigMap read"
```

---

## Task 7 — Make Python immediate-import verification match Collection decisions

**Files:**

- Modify `tests/test_activation.py` first.
- Modify `modules/activation.py` second.

### Step 7.1 — RED: repin immediate-import behavior

Update existing immediate-import fixtures to call/mock `get_configmap_advisory()` for this boundary. Present ConfigMap fixtures should include expected identity:

```yaml
metadata:
  name: import-controller-config
  namespace: multicluster-engine
data: ...
```

Add regression tests for:

1. advisory returns `None` => default strategy and existing annotation behavior;
2. valid present default/ImportOnly => existing annotation behavior;
3. valid ImportAndSync => no ManagedCluster discovery/patch for immediate-import;
4. advisory raises an exception containing a sentinel => exact stable `FatalError`, sentinel absent from public log/message, `list_custom_resources` not called, `patch_managed_cluster` not called;
5. present object is not a mapping => exact stable `FatalError`, zero ManagedCluster work;
6. wrong metadata name or namespace => same fail-closed barrier;
7. `data` is non-null and non-mapping => same barrier;
8. dry-run remains non-mutating under the existing KubeClient mutation guards.

The exact public failure is:

```text
Unable to verify autoImportStrategy on the destination hub; verify API access and retry.
```

Run RED:

```bash
python -m pytest tests/test_activation.py -k 'immediate_import or auto_import' -q
```

### Step 7.2 — GREEN: remove exception-to-`"error"` laundering

In `SecondaryActivation._get_auto_import_strategy()`:

- call `self.secondary.get_configmap_advisory(MCE_NAMESPACE, IMPORT_CONTROLLER_CONFIG_CM)`;
- on read exception, raise `FatalError` with the exact stable message and retain the original exception only as `__cause__`;
- `None` => `"default"`;
- validate present object type, metadata name/namespace, and `data` absent/null/mapping;
- malformed evidence => the same stable `FatalError`;
- valid present data => preserve current strategy normalization/default behavior.

In `_apply_immediate_import_annotations()`:

- remove the `strategy == "error"` warning/skip branch;
- preserve the supported-version gate and existing strategy decision;
- ensure a failed strategy read occurs before `list_custom_resources(... managedclusters ...)` and `patch_managed_cluster()`.

Do not redesign `_maybe_set_auto_import_strategy()`; it remains R4-owned and continues to call existing `get_configmap()`.

Run GREEN:

```bash
python -m pytest tests/test_activation.py -k 'immediate_import or auto_import' -q
python -m pytest tests/test_activation.py tests/test_kube_client.py -q
```

Commit:

```bash
git add modules/activation.py tests/test_activation.py
git commit -m "fix(python): fail closed on auto-import strategy reads"
```

---

## Task 8 — Add minimal parity/static guardrails

**Files:**

- Create `tests/test_r3_02_fail_closed_parity.py` only if it adds discriminating value beyond the per-form-factor runtime tests.
- Reuse the approved design/parity authorities; do not add a cross-runtime adapter.

### Step 8.1 — RED: pin only shared operator decisions that could drift silently

A useful root test should remain import-safe without requiring `ansible-core`. Parse Collection YAML as data/text and import Python runtime only through the repository's normal root test environment.

Pin at least:

- the exact stable auto-import verification failure string is the same in Python and Collection;
- Collection error path precedes ManagedCluster tasks;
- Python read failure raises before ManagedCluster listing/patch (already behavior-tested; do not duplicate mock-only assertions if the runtime test is stronger);
- compactor/connection changes do not alter documented dual-supported capability statuses.

If these assertions merely duplicate existing tests without catching cross-form-factor drift, skip the new file and document that decision in the PR description. Do not create a parity test for its own sake.

Run the chosen RED test if a new guardrail is added, then GREEN it with the minimum implementation/document adjustment.

### Step 8.2 — Run existing parity-sensitive contracts

```bash
PYTHONPATH=. python -m pytest \
  tests/test_constants_parity.py \
  tests/test_rbac_collection_parity.py \
  tests/test_validation_parity.py -q
```

No RBAC parity changes are expected; any required RBAC change is a scope blocker, not a test to update away.

Commit only if a new/changed parity guardrail is warranted:

```bash
git add tests/test_r3_02_fail_closed_parity.py
git commit -m "test: pin R3-02 fail-closed parity"
```

---

## Task 9 — Update operator/developer documentation and tracker evidence

**Files:**

- Modify `CHANGELOG.md`.
- Modify `thermos-resolution-plan.md`.
- Do not modify protected docs.

### Step 9.1 — Update `[Unreleased]`

Add a concise `Fixed` entry stating that:

- Collection compactor drain no longer accepts failed/unverified Pod reads as empty/drained;
- hub connectivity passes only from an exact Namespace read and still reaches preflight reports on failure;
- activation distinguishes explicit ConfigMap absence from 400/403/transport/malformed reads and fails before immediate-import mutation;
- Python mirrors the activation fail/continue decision.

Do not claim a release version, AAP certification, live ACM certification, or support change.

### Step 9.2 — Correct and advance R3-02 tracker state

Update the R3-02 Resolution text in `thermos-resolution-plan.md`; do not leave the now-false statement that `resources is not defined` occurs only on module error. Record:

- `k8s_info` normalized-empty ambiguity, including 400;
- the selected lossless reader for compactor/auto-import;
- connectivity exact-object evidence;
- Python activation parity correction;
- no intentional divergence and no RBAC expansion;
- actual implementation/test evidence from this branch, using exact commands/counts observed at execution time;
- status `ready_for_review` only after all required gates in Task 10 pass on the same exact head.

Do not mark `merged`; that happens only after the exact validated implementation head merges into `ansible` and a post-merge reconciliation updates tracker state.

### Step 9.3 — Check for other inaccurate unprotected docs without expanding scope

Search:

```bash
rg -n "autoImportStrategy_unavailable|resources is not defined|resources is defined|compactor.*drain|connectivity.*pass" \
  docs ansible_collections/tomazb/acm_switchover/docs thermos-resolution-plan.md \
  --glob '!docs/ACM_SWITCHOVER_RUNBOOK.md'
```

Edit another unprotected operator/developer doc only if it directly describes one of the corrected R3-02 behaviors inaccurately. Otherwise state in the PR description that no additional operator/developer doc change was necessary.

Run:

```bash
git diff --check
git diff --name-only origin/ansible...HEAD | \
  grep -E '^(docs/ACM_SWITCHOVER_RUNBOOK\.md|\.claude/skills/)' && exit 1 || true
```

Commit:

```bash
git add CHANGELOG.md thermos-resolution-plan.md
git commit -m "docs: record R3-02 fail-closed behavior"
```

---

## Task 10 — Run targeted verification, full gates, and the Pre-PR Simplification Gate

No new behavior should be added in this task. This is the evidence/convergence phase.

### Step 10.1 — Run targeted R3-02 tests first

```bash
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_k8s_read_outcome.py \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_primary_prep_auto_import.py \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_preflight_connectivity_contract.py \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_activation_auto_import.py -q

PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/integration/test_k8s_read_outcome_runtime.py \
  ansible_collections/tomazb/acm_switchover/tests/integration/test_r3_02_compactor_runtime.py \
  ansible_collections/tomazb/acm_switchover/tests/integration/test_preflight_role.py \
  ansible_collections/tomazb/acm_switchover/tests/integration/test_r3_02_activation_runtime.py -q

python -m pytest tests/test_kube_client.py tests/test_activation.py -q
```

If Task 8 added a parity guardrail:

```bash
python -m pytest tests/test_r3_02_fail_closed_parity.py -q
```

Record exact pass/fail/skip counts and exit codes. Do not paraphrase a failing lane as passing.

### Step 10.2 — Run the full affected Collection surfaces

Resolve dependencies exactly as current compatibility policy requires:

```bash
ansible-galaxy collection install \
  -r ansible_collections/tomazb/acm_switchover/requirements.yml
export ANSIBLE_COLLECTIONS_PATH="$PWD:$HOME/.ansible/collections"
```

Then:

```bash
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_compatibility_contract.py -q

PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/unit/ -q

PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/integration/ \
  ansible_collections/tomazb/acm_switchover/tests/scenario/ -q
```

Run the current playbook syntax/build/sanity/lint commands exactly as specified by the implementation-base `AGENTS.md`, `docs/development/testing.md`, and compatibility authority. At minimum, do not skip `ansible-test sanity`/`ansible-lint` when the new custom module invalidates those surfaces.

### Step 10.3 — Run combined Python/Collection parity-sensitive tests

```bash
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/unit/ \
  tests/ -q
```

This is required because activation, primary prep, and preflight are dual-supported.

### Step 10.4 — Run the authoritative root gate

```bash
./run_tests.sh
```

Keep strict quality enabled. Do not set `STRICT_QUALITY=0` to make a failing branch look green.

`./run_tests.sh` is expected to exercise current root quality gates and non-live release helpers according to current repository policy. If current `AGENTS.md` requires an additional explicit release-helper run, also run:

```bash
python -m pytest tests/release -q
```

Do not supply a live release profile; R3-02 does not authorize live certification.

### Step 10.5 — Exercise both repository-tested Collection endpoint lanes

The new module imports `kubernetes.core` client/auth helpers, so hosted CI for both endpoint lanes is a required merge-readiness gate:

```text
ansible-core 2.16.* / Python 3.11
ansible-core 2.21.* / Python 3.12
```

If both interpreters/lanes are locally available, rerun the focused new module + integration suite under both. Otherwise record the local limitation and require both hosted collection-foundation jobs to pass on the frozen PR head before terminal validation. Never infer one lane from the other.

### Step 10.6 — Recheck RBAC/protected/scope boundaries

```bash
git diff --name-only origin/ansible...HEAD | sort

git diff --name-only origin/ansible...HEAD | \
  grep -E '^(docs/ACM_SWITCHOVER_RUNBOOK\.md|\.claude/skills/)' && exit 1 || true

git diff --name-only origin/ansible...HEAD | \
  grep -E '^(deploy/rbac/|deploy/helm/acm-switchover-rbac/|ansible_collections/tomazb/acm_switchover/roles/rbac_bootstrap/)' \
  && { echo 'Unexpected RBAC-surface diff'; exit 1; } || true
```

If any new Kubernetes permission became necessary, stop for operator approval; do not silently update RBAC.

### Step 10.7 — Apply the Pre-PR Simplification Gate

Review all changed code and its immediate collaborators, especially:

- `acm_k8s_read_outcome.py` — reject framework growth, duplicated auth parsing, retry helpers, policy/report code, or future-facing options;
- three Collection task files — remove duplicated predicates only when a local fact/helper makes the safety condition clearer, not more indirect;
- `lib/kube_client.py` — keep advisory ConfigMap logic parallel to existing custom-resource advisory logic without changing unrelated callers;
- `modules/activation.py` — keep validation local to the immediate-import boundary; do not refactor the broader activation transaction.

Only behavior-preserving, in-scope simplifications are authorized. Do not expand into unrelated cleanup.

After any simplification, rerun every targeted test invalidated by the refactor and then the relevant broader gate. If no safe simplification exists, record that explicitly in the future PR description with the reason.

### Step 10.8 — Update final tracker evidence on the same candidate head

After all required local gates pass, update `thermos-resolution-plan.md` from `in_progress` to `ready_for_review` and record actual evidence. Do not pre-fill invented counts. Run documentation/static checks again and commit:

```bash
git add thermos-resolution-plan.md
git commit -m "docs: record R3-02 implementation evidence"
```

Then rerun any documentation guardrail affected by that commit and `git diff --check`.

### Step 10.9 — Freeze and verify before claiming completion

Invoke `verification-before-completion`. Run fresh:

```bash
git fetch origin ansible
BASE=$(git rev-parse origin/ansible)
HEAD=$(git rev-parse HEAD)
MERGE_BASE=$(git merge-base HEAD origin/ansible)
printf 'BASE=%s\nHEAD=%s\nMERGE_BASE=%s\n' "$BASE" "$HEAD" "$MERGE_BASE"
git status --porcelain=v1
git diff --check origin/ansible...HEAD
git diff --name-status origin/ansible...HEAD
```

If `origin/ansible` advanced, determine whether the branch is now stale under current `AGENTS.md`; rebase/revalidate or stop rather than hiding a base race.

Do not claim tests/quality/merge readiness without fresh command evidence from this exact head.

---

## Task 11 — PR and independent-validation handoff, only when separately authorized

Issue #272's original authorization did not itself authorize a PR. If the operator's implementation authorization does not explicitly include PR creation, stop after Task 10 and return the clean frozen implementation branch/evidence.

When PR creation is explicitly authorized:

1. Push the implementation branch.
2. Open a **draft** PR with `--base ansible`.
3. PR description must include:
   - issue #272 and findings R3-A4/R3-A5/GLM-H12;
   - approved design exact head and approved plan exact head;
   - changed-file scope and protected-file result;
   - Python/Collection parity statement;
   - RBAC no-expansion statement;
   - dry-run/check-mode/no-mutation evidence;
   - sanitized-error evidence;
   - exact targeted/full test commands and results;
   - both collection endpoint-lane CI requirement;
   - Pre-PR Simplification Gate result;
   - explicit statement that fake API/read-only dependency evidence is not ACM certification.
4. Freeze the candidate head before terminal review.
5. Run the **Independent Validator** from a fresh clean checkout/worktree against the exact frozen head. It must independently verify masked-error cases, 400/403/transport behavior, no post-barrier mutation, report inclusion, parity, both Ansible lanes, retry semantics, protected/RBAC scope, and exact-head CI. It may publish only its terminal top-level PR comment.
6. If actionable review feedback exists, use the **PR-comment Resolver/final validator** workflow: fetch every comment/review/thread, validate each against code, accept/reject with evidence, apply only in-scope corrections, rerun invalidated gates, push/reply, re-fetch, resolve only afterward.
7. Do not mark ready or merge with unresolved actionable threads or stale exact-head evidence.
8. Merge remains an operator decision even after terminal PASS.

---

## Required failure/decision matrix at implementation completion

The implementation is not complete unless executable tests prove this matrix:

| Boundary | Scenario | Required decision |
| --- | --- | --- |
| Collection compactor | successful empty Pod list | continue |
| Collection compactor | successful non-empty Pods | bounded retry, then count failure if still present |
| Collection compactor | 400 / 403 / timeout / transport / discovery / malformed | never drained; bounded retry; sanitized failure on exhaustion |
| Collection connectivity | exact `default` Namespace | `pass` |
| Collection connectivity | empty / 404 / 400 / 403 / discovery / wrong object/cardinality | critical `fail` in `preflight-report.json` |
| Collection auto-import | explicit named ConfigMap 404 | default `ImportOnly` behavior |
| Collection auto-import | valid present default/ImportOnly | existing immediate-import behavior |
| Collection auto-import | valid `ImportAndSync` | no immediate-import annotation mutation |
| Collection auto-import | 400 / 403 / timeout / transport / discovery / malformed | fail before ManagedCluster list/patch |
| Python auto-import | ConfigMap absent | default strategy |
| Python auto-import | valid present ConfigMap | configured strategy |
| Python auto-import | read failure / malformed object | exact stable `FatalError` before ManagedCluster list/patch |
| Both form factors | dry-run/check mode | no relevant mutation |

## Planned commit sequence

Use small logical commits; do not squash away useful red/green ownership during development unless repository merge policy later requires it:

1. `docs: start R3-02 fail-closed implementation`
2. `feat(collection): add lossless Kubernetes read outcome`
3. `test(collection): exercise lossless read outcome runtime`
4. `fix(collection): fail closed on compactor verification`
5. `fix(collection): require positive hub connectivity evidence`
6. `fix(collection): fail closed on auto-import strategy reads`
7. `feat(python): add advisory ConfigMap read`
8. `fix(python): fail closed on auto-import strategy reads`
9. optional `test: pin R3-02 fail-closed parity` only if it adds discriminating value
10. `docs: record R3-02 fail-closed behavior`
11. `docs: record R3-02 implementation evidence`

Each behavior commit must include its regression test(s), with RED observed before the production edit and GREEN observed after it.

## Stop conditions

Stop and return to the operator rather than improvising if any of these occur:

- current `origin/ansible` materially invalidates the approved design;
- a new Kubernetes permission is required;
- a protected file appears necessary;
- Python/Collection parity cannot be preserved as designed;
- the new Collection module cannot reuse the supported `kubernetes.core` client/auth surface on either repository-tested lane;
- preserving the compactor `30 x 10s` production contract would require a new operator-facing retry variable;
- tests can prove fail-closed behavior only by modifying/copying production task logic instead of executing the shipped task path;
- raw exception/API response content cannot be reliably kept out of public output without broader SSA-09 work;
- the implementation grows into a generalized verification/Kubernetes subsystem;
- hosted endpoint-lane CI disagrees with local behavior;
- base/head/merge-base or worktree-cleanliness prerequisites are not satisfied.

## Plan acceptance gate

This plan does **not** authorize implementation by itself. Runtime/test edits begin only after the operator explicitly approves the exact commit containing this plan. The operator approval record must name that exact plan head. After approval, Task 0 is mandatory before any implementation action.