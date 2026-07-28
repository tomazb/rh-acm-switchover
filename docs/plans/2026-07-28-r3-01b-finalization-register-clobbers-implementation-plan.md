# R3-01b Finalization Register/`set_fact` Clobbers + Collision Guardrail — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Plan identifier:** `R3-01b-PLAN-B2`
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
  - `docs/plans/2026-07-28-r3-01b-finalization-register-clobbers-implementation-plan.md`
  - `thermos-resolution-plan.md`
  - `AGENTS.md`
  - `CHANGELOG.md`
- **Strict separation:** do not touch `TR2D-02` code/tests, the merged Argo CD scoped-discovery work, or any unrelated `R3` slice.
- **No work on issue #202.** The preflight collision is only *allowlisted* (debt category), never fixed here. Issue #202 stays **separate and open**.
- **Tracker:** at implementation start, update **only** the `R3-01b` row (see Task 2). Do **not** claim `ready_for_review` and do **not** record merge credit at any point in this plan.
- **Builder execution requires separate explicit approval of `R3-01b-PLAN-B2`.** Do not create the branch, edit any tracked file, or open a PR until that approval is given.
- **Guardrail boundary (design §6.2):** `roles/**/tasks/**/*.{yml,yaml}`, `roles/**/handlers/**/*.{yml,yaml}`, `playbooks/**/*.{yml,yaml}`. **Playbook scanning must traverse every play-level task section — `pre_tasks`, `tasks`, `post_tasks`, `handlers` — before applying recursive `block`/`rescue`/`always` flattening.** Role `tasks`/`handlers` files are flat task lists, flattened directly.
- **Current-state facts:** playbooks have **zero** collisions today; no `roles/*/handlers/` files exist yet (boundary still includes them); the guardrail finds **14** collisions — Tasks 3–5 fix 2, leaving 2 intentional + 10 debt(#202).

All commands run from the worktree root with its venv active.

---

## Task 0: Hard pre-start gate + isolated worktree (execute only after `R3-01b-PLAN-B2` approval)

**Step 1: Re-baseline gate — fetch and require `origin/ansible == approved base`**

```bash
cd /home/tomaz/sources/rh-acm-switchover
git fetch origin ansible
BASE=0bf55db9eed76ae7d60844b806975c04cd0111e4
REMOTE=$(git rev-parse origin/ansible)
echo "approved_base=$BASE"; echo "origin/ansible=$REMOTE"
test "$REMOTE" = "$BASE" || { echo "STOP: origin/ansible advanced past approved base — explicit delta assessment / re-baselining required before implementation."; exit 1; }
```

**HARD GATE:** if `origin/ansible` has advanced, **stop**. Do not implement. Return to the operator for delta assessment and re-baselining of `R3-01b-PLAN-B2`.

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

**Step 1: Copy the exact approved artifacts**

```bash
# from the primary worktree paths (or session artifacts), copy verbatim:
cp <primary>/docs/plans/2026-07-28-r3-01b-finalization-register-clobbers-design.md docs/plans/
cp <primary>/docs/plans/2026-07-28-r3-01b-finalization-register-clobbers-implementation-plan.md docs/plans/
```

**Step 2: Record content hashes as evidence (design + plan)**

```bash
sha256sum docs/plans/2026-07-28-r3-01b-finalization-register-clobbers-design.md \
          docs/plans/2026-07-28-r3-01b-finalization-register-clobbers-implementation-plan.md
git hash-object docs/plans/2026-07-28-r3-01b-finalization-register-clobbers-design.md \
                docs/plans/2026-07-28-r3-01b-finalization-register-clobbers-implementation-plan.md
```
Capture both sets of hashes in the PR body / evidence log; they bind the delivered artifacts to `R3-01b-DESIGN-B2` / `R3-01b-PLAN-B2`.

**Step 3: Commit**

```bash
git add docs/plans/2026-07-28-r3-01b-finalization-register-clobbers-design.md \
        docs/plans/2026-07-28-r3-01b-finalization-register-clobbers-implementation-plan.md
git commit -m "docs(plan): seed approved R3-01b-DESIGN-B2 and R3-01b-PLAN-B2 into worktree"
```

---

## Task 2: Mark `R3-01b` in progress (tracker only)

**Files:** Modify `thermos-resolution-plan.md` — the `R3-01b` row only.

**Step 1: Update only the `R3-01b` tracker row**

- Change `R3-01b` status from `planned` to `in_progress`.
- Record the identifiers and branch in the row/notes: `R3-01b-DESIGN-B2`, `R3-01b-PLAN-B2`, branch `fix/r3-01b-finalization-register-clobbers`, base `0bf55db9`.
- Update the document's `Last Updated` date.
- Do **not** touch the `R3-A2`/`R3-A3` finding rows' resolution status, the #202 reference, or any other row. Do **not** write `ready_for_review` or merge credit.

**Step 2: Commit**

```bash
git add thermos-resolution-plan.md
git commit -m "docs(tracker): mark R3-01b in_progress with R3-01b-DESIGN-B2/PLAN-B2 identifiers"
```

---

## Task 3: Executable fake ACM backup API (shared test infrastructure)

**Files:**
- Create: `ansible_collections/tomazb/acm_switchover/tests/integration/finalization_fake_api.py`

**Step 1: Implement a fake, modeled on `argocd_fake_api.py`**

A `ThreadingHTTPServer` on `127.0.0.1:0` exposing `url`, `close()`, and serving:
- `GET /apis/cluster.open-cluster-management.io/v1beta1/namespaces/open-cluster-management-backup/restores` → a Restore list from constructor-supplied resources (secondary hub);
- `GET .../restores/restore-acm-passive-sync` → single Restore (primary/old hub);
- an optional `list_failures`/`get_failures` map that returns HTTP 500 for a route, so tests can assert a live query failure is **fatal**.

Reuse the existing `write_kubeconfig(path, context=..., server=hub.url)` helper from `argocd_fake_api.py` (import it) to point a hub's kubeconfig at the fake server.

**Step 2: Smoke test the fake resolves via `k8s_info` (temporary, then delete)**

Run a throwaway `ansible-playbook` that does a `k8s_info` Restore read against the fake and prints the count; confirm it returns the seeded resources. Delete the throwaway after confirming.

**Step 3: Commit**

```bash
git add ansible_collections/tomazb/acm_switchover/tests/integration/finalization_fake_api.py
git commit -m "test(collection): add executable fake ACM backup API for finalization runtime tests"
```

---

## Task 4: Shared staged fail-closed shape-validation include

**Files:**
- Create: `ansible_collections/tomazb/acm_switchover/roles/finalization/tasks/assert_restore_source_shape.yml`

**Design (satisfies design §6.1 + revision items 8 & 9):** staged assertions on a caller-supplied `_acm_restore_source`, structured so undefined / non-mapping inputs reach the controlled sanitized `fail_msg` at stage 1 (before any `.resources` access), and mapping-valued `resources` is explicitly rejected.

```yaml
---
# Fail-closed, staged validation of an authoritative Restore source.
# A deliberately-seeded {resources: []} is valid authoritative absence;
# undefined / non-mapping / skipped / non-list / mapping-valued resources /
# malformed entries must fail with a sanitized message and never degrade into
# an empty successful result. (R3-01b-DESIGN-B2 section 6.1; PLAN-B2 items 8-9)

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
      - _acm_restore_source.resources is iterable
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

**Note (item 9):** the caller (Tasks 5 & 7) must select the candidate so `_acm_restore_source` is always *defined* (default to a non-mapping sentinel such as `None` when the source variable is absent), guaranteeing `is mapping` evaluates to `False` and reaches the stage-1 `fail_msg` rather than raising.

**Step: Commit** (after Task 5 wiring — this file is committed with Task 5).

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
    ({"resources": "not-a-list"}, "non-list-string"),
    ({"resources": {}}, "empty-mapping-resources"),
    ({"resources": {"a": 1}}, "non-empty-mapping-resources"),
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

**Step 7: Add R3-A2 deterministic execute-mode runtime test (fake API — item 10)**

Using `finalization_fake_api.FakeAcmBackupHub` + `write_kubeconfig`, seed a **stale** `acm_finalization_restores_info` but run in execute mode with the secondary hub pointed at a fake serving **fresh** live Restores; assert the published count reflects the **fresh live** data (overrides stale pre-seed). Add a fatal case: the fake returns 500 for the Restore list → assert `returncode != 0`.

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
git add ansible_collections/tomazb/acm_switchover/roles/finalization/tasks/assert_restore_source_shape.yml \
        ansible_collections/tomazb/acm_switchover/roles/finalization/tasks/cleanup_restores.yml \
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

`finalization_old_hub_restore_discovery.yml` includes `finalization` `tasks_from: discover_resources`, then:

```yaml
    - name: Report old-hub restore discovery
      ansible.builtin.debug:
        msg: >-
          OLD_HUB_HAS_RESOURCES_KEY={{ 'resources' in _old_hub_existing_restore_info }}
          OLD_HUB_COUNT={{ _old_hub_existing_restore_info.resources | default([]) | length }}
```

`_old_hub_vars` pre-seeds the other finalization discovery facts (`acm_finalization_backup_schedules_info`, `acm_finalization_mch_info`, `acm_finalization_restores_info` = `{resources: []}`) so unrelated reads are skipped without a live cluster.

**Step 2: Write the failing tests**

```python
def test_dry_run_seed_survives(tmp_path):
    r = _run(tmp_path, _old_hub_vars(mode="dry_run"))
    assert r.returncode == 0 and "OLD_HUB_HAS_RESOURCES_KEY=True" in r.stdout and "OLD_HUB_COUNT=0" in r.stdout

def test_injected_fixture_preserved(tmp_path):
    v = _old_hub_vars(mode="dry_run")
    v["_old_hub_existing_restore_info"] = {"resources": [{"metadata": {"name": "restore-acm-passive-sync"}}]}
    r = _run(tmp_path, v)
    assert r.returncode == 0 and "OLD_HUB_COUNT=1" in r.stdout
```

**Step 3: Run — verify fail on the real defect**

Expected: FAIL (clobber → `OLD_HUB_HAS_RESOURCES_KEY=False`), not a missing-playbook error.

**Step 4: Fix the old-hub Restore block (candidate/validate/publish + distinct live name)**

```yaml
- name: Seed empty old-hub passive restore info in dry-run mode
  ansible.builtin.set_fact:
    _old_hub_existing_restore_info:
      resources: []
  when:
    - _old_hub_existing_restore_info is not defined
    - acm_switchover_execution.mode | default('dry_run') == 'dry_run'
    - (acm_switchover_operation.old_hub_action | default('secondary')) == 'secondary'
    - not (acm_switchover_operation.restore_only | default(false))

- name: Read existing passive sync restore on old hub (execute mode, not pre-seeded)
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

- name: Publish authoritative old-hub passive restore info from live read
  ansible.builtin.set_fact:
    _old_hub_existing_restore_info: "{{ _old_hub_existing_restore_live_info }}"
  when:
    - _old_hub_existing_restore_info is not defined
    - _old_hub_existing_restore_live_info is defined
    - not (_old_hub_existing_restore_live_info.skipped | default(false))

- name: Validate authoritative old-hub passive restore source (fail-closed, staged)
  ansible.builtin.include_tasks: assert_restore_source_shape.yml
  vars:
    _acm_restore_source: "{{ _old_hub_existing_restore_info | default(None) }}"
    _acm_restore_source_label: "old-hub passive restore"
  when:
    - (acm_switchover_operation.old_hub_action | default('secondary')) == 'secondary'
    - not (acm_switchover_operation.restore_only | default(false))
```

`_old_hub_existing_restore_info` is now `set_fact`-only; the live read registers to `_old_hub_existing_restore_live_info` → no same-name collision. Execute-mode refresh preserved; injected fixtures still win (`is not defined` guard).

**Step 5: Run — verify pass**

Expected: PASS (both tests).

**Step 6: Add execute-mode fake-API + fatal + negative tests (items 9, 10)**

- **A3 live published without fixture:** execute mode, no `_old_hub_existing_restore_info` pre-seed, primary hub pointed at `FakeAcmBackupHub` serving one passive-sync Restore → assert `OLD_HUB_COUNT=1` from live.
- **A3 fixture authoritative when injected:** execute mode, pre-seed `_old_hub_existing_restore_info` with a *different* count than the fake serves → assert the fixture count wins (live read skipped).
- **Live query failure fatal:** fake returns 500 → assert `returncode != 0`.
- **Negative matrix** for `_old_hub_existing_restore_info` mirroring Task 5 (undefined-under-secondary, non-list, empty/non-empty mapping resources, skipped, malformed) → fail closed with sanitized message.

**Step 7: Static contract**

```python
def test_finalization_old_hub_restore_has_no_name_collision():
    text = FINALIZATION_DISCOVER.read_text()
    assert "register: _old_hub_existing_restore_live_info" in text
    assert "register: _old_hub_existing_restore_info\n" not in text
    assert "assert_restore_source_shape.yml" in text
```

**Step 8: Run + commit**

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
    for pat in ("*/tasks/**/*.yml", "*/tasks/**/*.yaml", "*/handlers/**/*.yml", "*/handlers/**/*.yaml"):
        for p in roles.glob(pat):
            for var in scan_file(p, is_playbook=False):
                found.add((str(p.relative_to(collection_root)), var))
    for pat in ("**/*.yml", "**/*.yaml"):
        for p in (collection_root / "playbooks").glob(pat):
            for var in scan_file(p, is_playbook=True):
                found.add((str(p.relative_to(collection_root)), var))
    return found
```

**Step 2: Fixtures**

- `tasks_collision.yml`, `handlers_collision.yml`, `playbook_collision.yml` (collision in `post_tasks`), `clean.yml` (distinct names).
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
def test_clean_file_no_collision():     assert scan_file(FIX/"clean.yml", is_playbook=False) == []
def test_dynamic_register_not_flagged():assert scan_file(FIX/"dynamic_register.yml", is_playbook=False) == []
def test_dynamic_setfact_not_flagged(): assert scan_file(FIX/"dynamic_setfact.yml", is_playbook=False) == []
def test_parse_error_is_path_bearing():
    with pytest.raises(CollisionScanError) as e:
        scan_file(FIX/"broken.yml", is_playbook=False)
    assert "broken.yml" in str(e.value)
```

**Step 4: Run + commit**

Run: `python -m pytest .../test_register_setfact_collision_guardrail.py -q`
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
- Modify: `ansible_collections/tomazb/acm_switchover/tests/unit/test_register_setfact_collision_guardrail.py`

**Step 1: Allowlist loader/validator (item 12)**

Add a reusable `load_and_validate_allowlist(path)` that raises `AllowlistError` unless:
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
        ansible_collections/tomazb/acm_switchover/tests/unit/fixtures/allowlists/ \
        ansible_collections/tomazb/acm_switchover/tests/unit/test_register_setfact_collision_guardrail.py
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
git diff --check
```

**Step 7: Changed-file / protected-file / scope check**

```bash
git diff --name-only "$BASE"..HEAD | sort
```
Assert every changed path is under `ansible_collections/tomazb/acm_switchover/` **or** is one of the five authorized docs. Assert **no** path under `lib/`, `modules/`, `acm_switchover.py`, or root `tests/` appears. Assert no protected file (`docs/ACM_SWITCHOVER_RUNBOOK.md`, `.claude/skills/**/*.skill.md`) is modified. Assert `TR2D-02` files are untouched.

---

## Task 12: PR-preparation gate (requires separate approval before opening a PR)

Do **not** open a PR until separately authorized. When authorized:

- Run the `code-review` skill against the branch diff; address all critical/warning findings or record a concrete technical reason; re-run after changes.
- PR body includes: bound identifiers (`R3-01b-DESIGN-B2`, `R3-01b-PLAN-B2`), base `0bf55db9`, seeded-artifact hashes (Task 1), `ansible-playbook --version` for the 2.15.x gate (Task 9), and all Task 11 gate outputs.
- The PR resolves `R3-01b` only; it does **not** close issue #202, does **not** change tracker status beyond `in_progress`, and does **not** claim `ready_for_review` or merge credit within this plan's scope.

---

## Verification checklist (definition of done for the slice)

- [ ] Pre-start re-baseline gate passed (`origin/ansible == 0bf55db9`).
- [ ] Approved design + plan seeded and committed into the worktree with recorded hashes.
- [ ] Only the `R3-01b` tracker row set to `in_progress`; #202 remains separate/open; no `ready_for_review`/merge credit.
- [ ] Finalization dry-run preview reports the real `restore_count`/`restore_names`.
- [ ] Candidate → staged-validate → publish: malformed data never published; undefined/non-mapping/empty+non-empty mapping `resources`/skipped/malformed all reach the sanitized `fail_msg`; deliberate `{resources: []}` succeeds.
- [ ] Deterministic execute-mode fake-API tests: A2 fresh overrides stale pre-seed; A3 live published without fixture; A3 fixture authoritative when injected; live-query failure fatal.
- [ ] Scanner is literal-only (dynamic names excluded), detects collisions in `tasks`/`handlers`/playbook `post_tasks`, and raises a path-bearing error on parse failure.
- [ ] Allowlist: 2 intentional + 10 debt(#202); non-empty required values, unique `(path,variable)`, no cross-category duplicates, unknown categories rejected, exact #202 reference; malformed-allowlist fixtures rejected automatically.
- [ ] Skipped-register semantics confirmed on the default controller and on ansible-core 2.15.x (foundation container or Python 3.11), with `ansible-playbook --version` recorded.
- [ ] AGENTS.md documents the collision rule + category-specific allowlist policy.
- [ ] All Task 11 gates green; `git diff --check` clean; changed-file/protected-file/scope checks pass.
- [ ] No Python CLI production/test file modified; no `TR2D-02` change; no #202 fix; tracker only `in_progress`.
