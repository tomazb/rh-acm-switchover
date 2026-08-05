# Issue #207 Argo CD Register Convergence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Converge the Ansible collection's Argo CD pause handling and the bash script onto ADR-0001 semantics: fail closed on undischarged resume obligations, one register per form factor.

**Architecture:** The cluster annotation pair is the collection's register (see design doc `docs/plans/2026-08-05-issue-207-argocd-register-convergence-design.md`). Changes are task-YAML edits in `roles/argocd_manage`, run_id lifecycle hardening, deletion of the bash second register and dead helper code, and guardrail tests that read the shipped YAML.

**Tech Stack:** Ansible task YAML, pytest (repo `tests/` + collection `tests/unit/`), yaml contract-test helpers (`yaml_contract_helpers._flatten_tasks`, `_when_text`).

## Global Constraints

- Base branch: `origin/ansible` (never `main`).
- Black: `black --line-length 120` (repo has no black config; default 88 is wrong).
- No `Co-Authored-By` / AI attribution trailers in commits or PRs.
- Annotation keys (exact): `acm-switchover.argoproj.io/paused-by`, `acm-switchover.argoproj.io/original-sync-policy`.
- Python flag (exact): `--argocd-resume-only`.
- Full gates before PR: `./run_tests.sh`, collection unit tests, `git diff --check`.
- Preserve: marker ownership (resume only own run_id), resourceVersion-conditional patches, dry-run non-mutation, ApplicationSet protections.

---

### Task 1: Fail closed on missing/empty original-sync-policy (C1, #184) + orphaned-annotation warning

**Files:**
- Modify: `ansible_collections/tomazb/acm_switchover/roles/argocd_manage/tasks/resume.yml`
- Test: `ansible_collections/tomazb/acm_switchover/tests/unit/test_argocd_manage_role_contracts.py`

**Interfaces:**
- Produces: resume.yml contract relied on by Task 4's parity test — the patch task's `spec.syncPolicy` template contains no `default('{}')`; a fail task named `Fail on unrecoverable original-sync-policy annotations` exists.

- [ ] **Step 1: Write failing contract tests**

Append to `test_argocd_manage_role_contracts.py` (add `ARGOCD_RESUME = ROLES_DIR / "argocd_manage" / "tasks" / "resume.yml"` next to the other path constants):

```python
class TestArgoCDResumeFailClosed:
    """resume.yml must never patch spec.syncPolicy without a recoverable policy (ADR-0001, issue #184)."""

    def setup_method(self):
        self.text = ARGOCD_RESUME.read_text()
        self.tasks = _flatten_tasks(yaml.safe_load(self.text) or [])

    def _task(self, name_fragment):
        matches = [t for t in self.tasks if name_fragment in (t.get("name") or "")]
        assert matches, f"resume.yml must contain a task matching {name_fragment!r}"
        return matches[0]

    def test_restore_patch_never_defaults_policy_to_empty(self):
        """The rejected shape: default('{}') silently patches syncPolicy to {} for
        Python-paused apps (whose policy lives in the state file, not the annotation)."""
        assert "default('{}')" not in self.text, (
            "resume.yml must not default a missing original-sync-policy annotation to '{}' "
            "— that destroys the sync policy of Python-paused Applications (audit C1)"
        )

    def test_restore_patch_requires_policy_annotation(self):
        task = self._task("Restore original sync policy")
        when = _when_text(task)
        assert "original-sync-policy" in when, (
            "Restore patch must be gated on the original-sync-policy annotation being present"
        )
        assert "length" in when, "Restore patch must require a non-empty policy annotation"

    def test_unrecoverable_policy_fails_the_phase(self):
        task = self._task("Fail on unrecoverable original-sync-policy annotations")
        assert "ansible.builtin.fail" in task
        msg = str(task["ansible.builtin.fail"].get("msg", ""))
        assert "--argocd-resume-only" in msg, (
            "Failure message must route Python-paused Applications to the Python resume path"
        )
        when = _when_text(task)
        assert "paused-by" in when and "original-sync-policy" in when

    def test_orphaned_policy_annotation_is_reported(self):
        task = self._task("orphaned original-sync-policy")
        when = _when_text(task)
        assert "paused-by" in when and "original-sync-policy" in when
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `cd ansible_collections/tomazb/acm_switchover && python -m pytest tests/unit/test_argocd_manage_role_contracts.py -k FailClosed -v`
Expected: FAIL (`default('{}')` present; named tasks missing). If the collection tests need a different invocation, check `ansible_collections/tomazb/acm_switchover/tests/unit/` for a conftest/README and use that; record the working command for later tasks.

- [ ] **Step 3: Edit resume.yml**

(a) In the `Restore original sync policy and remove pause annotations` task, change the `syncPolicy` template (currently lines 71–76) to drop the default:

```yaml
            syncPolicy: >-
              {{
                item.metadata.annotations['acm-switchover.argoproj.io/original-sync-policy']
                | from_json
              }}
```

(b) Extend that task's `when:` with annotation-integrity conditions (after the existing run_id match condition):

```yaml
        - "'acm-switchover.argoproj.io/original-sync-policy' in item.metadata.annotations"
        - (item.metadata.annotations['acm-switchover.argoproj.io/original-sync-policy'] | trim | length) > 0
        - (item.metadata.annotations['acm-switchover.argoproj.io/original-sync-policy'] | trim) != '{}'
```

(c) After the `Record resume summary` task (end of the block), add two tasks:

```yaml
    - name: Warn about applications with orphaned original-sync-policy annotation
      ansible.builtin.debug:
        msg: >-
          {{ item.metadata.namespace }}/{{ item.metadata.name }} carries
          'acm-switchover.argoproj.io/original-sync-policy' but no
          'acm-switchover.argoproj.io/paused-by' marker. Ownership cannot be
          established; leaving the application untouched. Inspect and remove the
          orphaned annotation manually.
      loop: "{{ acm_switchover_argocd_all_apps | default([]) | selectattr('metadata.annotations', 'defined') | list }}"
      loop_control:
        label: "{{ item.metadata.namespace }}/{{ item.metadata.name }}"
      when:
        - acm_switchover_argocd_mock_apps is not defined
        - "'acm-switchover.argoproj.io/paused-by' not in item.metadata.annotations"
        - "'acm-switchover.argoproj.io/original-sync-policy' in item.metadata.annotations"

    - name: Fail on unrecoverable original-sync-policy annotations
      ansible.builtin.fail:
        msg: >-
          Refusing to resume {{ item.metadata.namespace }}/{{ item.metadata.name }}:
          the pause marker matches run_id '{{ _argocd_expected_run_id }}' but the
          'acm-switchover.argoproj.io/original-sync-policy' annotation is missing or
          empty, so the original sync policy cannot be restored. If this application
          was paused by the Python tool, resume it with:
          acm_switchover.py --argocd-resume-only. Never patching spec.syncPolicy
          without a recoverable policy (ADR-0001).
      loop: "{{ acm_switchover_argocd_all_apps | default([]) | selectattr('metadata.annotations', 'defined') | list }}"
      loop_control:
        label: "{{ item.metadata.namespace }}/{{ item.metadata.name }}"
      when:
        - acm_switchover_argocd_mock_apps is not defined
        - "'acm-switchover.argoproj.io/paused-by' in item.metadata.annotations"
        - item.metadata.annotations['acm-switchover.argoproj.io/paused-by'] == _argocd_expected_run_id
        - >-
          'acm-switchover.argoproj.io/original-sync-policy' not in item.metadata.annotations
          or (item.metadata.annotations['acm-switchover.argoproj.io/original-sync-policy'] | trim | length) == 0
          or (item.metadata.annotations['acm-switchover.argoproj.io/original-sync-policy'] | trim) == '{}'
```

Placement rationale (keep in the file as no comment — this is for the implementer): the fail task comes after the patch loop and summary so recoverable applications are restored first; the phase still fails, leaving the unrecoverable obligations visible. The fail fires in dry-run too (surfacing the defect early is the point); only mock mode is excluded.

- [ ] **Step 4: Run contract tests, verify pass**

Run the Task 1 Step 2 command. Expected: PASS. Also run the whole file: `python -m pytest tests/unit/test_argocd_manage_role_contracts.py -v` — pre-existing tests must stay green.

- [ ] **Step 5: Commit**

```bash
git add ansible_collections/tomazb/acm_switchover/roles/argocd_manage/tasks/resume.yml ansible_collections/tomazb/acm_switchover/tests/unit/test_argocd_manage_role_contracts.py
git commit -m "fix(ansible): fail closed when Argo CD resume lacks original-sync-policy (ADR-0001, #184)"
```

---

### Task 2: Fail resume when Application CRD is absent but a run_id is known

**Files:**
- Modify: `ansible_collections/tomazb/acm_switchover/roles/argocd_manage/tasks/resume.yml`
- Test: `ansible_collections/tomazb/acm_switchover/tests/unit/test_argocd_manage_role_contracts.py`

**Interfaces:**
- Consumes: `acm_switchover_argocd_installed` / `acm_switchover_argocd_discovery_status` set by discover.yml (rescue path sets `installed: false` on CRD-absent and on advisory discovery error).
- Produces: a top-level task `Fail when resume cannot verify obligations` in resume.yml, gated on `acm_switchover_argocd.run_id` (the strong signal — NOT the `_argocd_expected_run_id` fallback chain, which includes `acm_switchover_execution.run_id` and is non-empty on every switchover even when Argo CD was never installed).

- [ ] **Step 1: Write failing contract tests**

Append to `TestArgoCDResumeFailClosed` (or a new class) in `test_argocd_manage_role_contracts.py`:

```python
class TestArgoCDResumeCrdAbsent:
    """CRD invisible at resume must not be a silent no-op when a pause run_id exists.

    discover.yml's rescue sets installed=false and blanks the app lists on a
    CRD-absent error; resume.yml's main block is gated on installed. Without this
    gate that combination is ADR-0001's explicitly rejected 'clear register when
    CRD absent' behaviour: restored: 0, exit 0.
    """

    def setup_method(self):
        raw = yaml.safe_load(ARGOCD_RESUME.read_text()) or []
        self.top_level = raw
        self.tasks = _flatten_tasks(raw)

    def test_crd_absent_gate_exists_and_fails(self):
        gates = [t for t in self.tasks if "cannot verify obligations" in (t.get("name") or "")]
        assert gates, "resume.yml must fail when CRD is absent but a run_id is known"
        gate = gates[0]
        assert "ansible.builtin.fail" in gate

    def test_gate_uses_strong_run_id_signal(self):
        gate = [t for t in self.tasks if "cannot verify obligations" in (t.get("name") or "")][0]
        when = _when_text(gate)
        assert "acm_switchover_argocd" in when and "run_id" in when
        assert "acm_switchover_execution" not in when, (
            "Gate must key on acm_switchover_argocd.run_id only; the execution.run_id "
            "fallback is non-empty on every run and would hard-fail switchovers on "
            "clusters that never had Argo CD installed"
        )
        assert "acm_switchover_argocd_installed" in when

    def test_gate_is_top_level_before_installed_block(self):
        names = [t.get("name") for t in self.top_level]
        gate_idx = next(i for i, n in enumerate(names) if n and "cannot verify obligations" in n)
        block_idx = next(i for i, n in enumerate(names) if n and "Resume auto-sync" in n)
        assert gate_idx < block_idx, "Gate must run before the installed-gated resume block"
```

- [ ] **Step 2: Run, verify FAIL**

Same pytest command as Task 1. Expected: FAIL (gate task absent).

- [ ] **Step 3: Add the gate at the top of resume.yml**

Insert as the first top-level task (before the `Resume auto-sync on previously paused applications` block):

```yaml
- name: Fail when resume cannot verify obligations (Application CRD not visible)
  ansible.builtin.fail:
    msg: >-
      Cannot verify Argo CD resume obligations: run_id
      '{{ (acm_switchover_argocd | default({})).get('run_id', '') }}' indicates
      applications may have been paused, but Application discovery on this hub
      reported status
      '{{ (acm_switchover_argocd_discovery_status | default({})).get('status', 'unknown') }}'.
      Refusing to report resume success without proof (ADR-0001). Pause markers
      remain on the cluster in 'acm-switchover.argoproj.io/paused-by' annotations;
      restore API visibility and retry, or resume Python-paused applications with
      acm_switchover.py --argocd-resume-only.
  when:
    - not (acm_switchover_argocd_installed | default(false))
    - acm_switchover_argocd_mock_apps is not defined
    - ((acm_switchover_argocd | default({})).get('run_id', '') | length) > 0
```

Note the gate fires for both `status: absent` and the advisory `status: error` discovery outcomes — both blank the app list, and a resume that cannot see applications cannot discharge obligations.

- [ ] **Step 4: Run contract tests, verify PASS; run whole collection unit suite**

`python -m pytest tests/unit -q` from the collection root. Fix any pre-existing test that asserted the old silent-skip shape only if its assertion is exactly the behaviour this task removes (document in commit message if so).

- [ ] **Step 5: Commit**

```bash
git add ansible_collections/tomazb/acm_switchover/roles/argocd_manage/tasks/resume.yml ansible_collections/tomazb/acm_switchover/tests/unit/test_argocd_manage_role_contracts.py
git commit -m "fix(ansible): fail Argo CD resume when CRD invisible but run_id known (ADR-0001)"
```

---

### Task 3: run_id lifecycle — mint after discovery, persist without execution fallback, no 'unknown' marker

**Files:**
- Modify: `ansible_collections/tomazb/acm_switchover/roles/argocd_manage/tasks/discover.yml` (move `Generate run_id if not provided`, currently lines 59–69)
- Modify: `ansible_collections/tomazb/acm_switchover/roles/argocd_manage/tasks/pause.yml` (line 45 fallback; new fail task)
- Modify: `ansible_collections/tomazb/acm_switchover/roles/primary_prep/tasks/main.yml:109,125`, `ansible_collections/tomazb/acm_switchover/roles/activation/tasks/main.yml:57,84`, `ansible_collections/tomazb/acm_switchover/playbooks/switchover.yml:74`, `ansible_collections/tomazb/acm_switchover/playbooks/restore_only.yml:107`
- Test: `ansible_collections/tomazb/acm_switchover/tests/unit/test_argocd_manage_role_contracts.py`

**Interfaces:**
- Produces: invariant relied on by Task 2's gate — `acm_switchover_argocd.run_id` is non-empty **iff** a pause-mode discovery found Argo CD installed (in-play) or a checkpoint/operator supplied it (resume). Checkpoint `operational_data.argocd_run_id` no longer inherits `execution.run_id`.

Why: today run_id is minted before discovery and checkpoints persist `acm_switchover_argocd.run_id | default(acm_switchover_execution.run_id | default(''))`. Both pollute the "did a pause happen" signal, which would make Task 2's gate hard-fail switchovers on clusters without Argo CD.

- [ ] **Step 1: Write failing contract tests**

```python
class TestArgoCDRunIdLifecycle:
    """run_id must exist iff a pause may have landed (ADR-0001 obligation signal)."""

    def setup_method(self):
        self.discover_text = ARGOCD_DISCOVER.read_text()
        self.pause_text = ARGOCD_PAUSE.read_text()
        self.discover_tasks = _flatten_tasks(yaml.safe_load(self.discover_text) or [])
        self.pause_tasks = _flatten_tasks(yaml.safe_load(self.pause_text) or [])

    def test_run_id_minted_only_when_installed(self):
        mint = [t for t in self.discover_tasks if "Generate run_id" in (t.get("name") or "")]
        assert mint, "discover.yml must keep the run_id generation task"
        when = _when_text(mint[0])
        assert "acm_switchover_argocd_installed" in when, (
            "run_id must be minted only after discovery confirms Argo CD is installed; "
            "an unconditional mint makes run_id useless as an obligation signal"
        )

    def test_pause_marker_never_falls_back_to_unknown(self):
        assert "'unknown'" not in self.pause_text and '"unknown"' not in self.pause_text, (
            "pause.yml must not write paused-by: unknown — an 'unknown' marker can never "
            "be matched by any resume run_id and orphans the pause"
        )

    def test_pause_requires_run_id(self):
        req = [t for t in self.pause_tasks if "Require run_id" in (t.get("name") or "")]
        assert req and "ansible.builtin.fail" in req[0]

    def test_checkpoint_persist_has_no_execution_fallback(self):
        roles_dir = ROLES_DIR
        collection_root = roles_dir.parent
        offenders = []
        for path in [
            roles_dir / "primary_prep" / "tasks" / "main.yml",
            roles_dir / "activation" / "tasks" / "main.yml",
            collection_root / "playbooks" / "switchover.yml",
            collection_root / "playbooks" / "restore_only.yml",
        ]:
            for i, line in enumerate(path.read_text().splitlines(), 1):
                if "argocd_run_id:" in line and "acm_switchover_execution" in line:
                    offenders.append(f"{path.name}:{i}")
        assert not offenders, (
            f"argocd_run_id persisted with execution.run_id fallback at {offenders}; the "
            "checkpoint value must be non-empty only when an Argo CD pause run_id exists"
        )
```

- [ ] **Step 2: Run, verify FAIL** (same pytest command)

- [ ] **Step 3: Implement**

(a) discover.yml: move the `Generate run_id if not provided` task from lines 59–69 to immediately after the task that sets `acm_switchover_argocd_installed: true` on successful discovery (the fact-setting task around lines 230–250 inside the same block). New `when:`:

```yaml
      when:
        - acm_switchover_argocd_installed | default(false)
        - ((acm_switchover_argocd | default({})).get('run_id', '')) == ''
        - (acm_switchover_argocd_mode_override | default((acm_switchover_argocd | default({})).get('mode', 'pause'))) == 'pause'
```

Verify nothing between the old and new position consumes `acm_switchover_argocd.run_id` (scoped discovery and namespace validation do not; grep `run_id` in discover.yml to confirm).

(b) pause.yml line 45: replace the fallback chain with the strict value:

```yaml
              acm-switchover.argoproj.io/paused-by: "{{ acm_switchover_argocd.run_id }}"
```

and add, immediately before the `Remove automated sync policy and annotate with run-id` patch task:

```yaml
    - name: Require run_id before pausing
      ansible.builtin.fail:
        msg: >-
          Cannot pause Argo CD applications without a run_id; discovery mints one in
          pause mode when Argo CD is installed. Refusing to write an unmatchable
          pause marker.
      when:
        - acm_switchover_argocd_mock_apps is not defined
        - acm_switchover_execution.mode | default('dry_run') != 'dry_run'
        - ((acm_switchover_argocd | default({})).get('run_id', '') | length) == 0
```

(c) The four persist sites: change

```yaml
argocd_run_id: "{{ acm_switchover_argocd.run_id | default(acm_switchover_execution.run_id | default('')) }}"
```

to

```yaml
argocd_run_id: "{{ (acm_switchover_argocd | default({})).get('run_id', '') }}"
```

Backwards compatibility note for the commit message: checkpoints written by older versions may carry `argocd_run_id == execution.run_id`; resume still honours them because `_argocd_expected_run_id` keeps its fallback chain for marker *matching* — only the *obligation signal* is strict now.

- [ ] **Step 4: Run collection unit suite + integration**

`python -m pytest tests/unit -q` and `python -m pytest tests/integration/test_argocd_scoped_discovery_runtime.py -q` from the collection root (the latter builds checkpoints with `operational_data.argocd_run_id` — fix fixtures only if they assert the removed fallback). Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ansible_collections/tomazb/acm_switchover/roles/argocd_manage/tasks/discover.yml ansible_collections/tomazb/acm_switchover/roles/argocd_manage/tasks/pause.yml ansible_collections/tomazb/acm_switchover/roles/primary_prep/tasks/main.yml ansible_collections/tomazb/acm_switchover/roles/activation/tasks/main.yml ansible_collections/tomazb/acm_switchover/playbooks/switchover.yml ansible_collections/tomazb/acm_switchover/playbooks/restore_only.yml ansible_collections/tomazb/acm_switchover/tests/unit/test_argocd_manage_role_contracts.py
git commit -m "fix(ansible): make Argo CD run_id a true obligation signal (mint after discovery, strict persist)"
```

---

### Task 4: Remove dead build_pause_patch; guard the shipped YAML instead

**Files:**
- Modify: `ansible_collections/tomazb/acm_switchover/plugins/module_utils/argocd.py` (delete `build_pause_patch`, lines ~151–159)
- Modify: `ansible_collections/tomazb/acm_switchover/plugins/module_utils/constants.py` (add `ARGOCD_ORIGINAL_SYNC_POLICY_ANNOTATION = "acm-switchover.argoproj.io/original-sync-policy"` next to `ARGOCD_PAUSED_BY_ANNOTATION` at line 55)
- Delete: `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_argocd_autosync.py`
- Modify: `tests/test_argocd_constants_parity.py` (delete `test_build_pause_patch_matches_jinja_logic`, lines 54–88; add shipped-YAML guardrails)

**Interfaces:**
- Consumes: resume.yml/pause.yml shapes from Tasks 1–3.
- Produces: `ARGOCD_ORIGINAL_SYNC_POLICY_ANNOTATION` in `module_utils/constants.py` for any future consumer.

- [ ] **Step 1: Verify build_pause_patch is dead**

Run: `grep -rn "build_pause_patch" --include="*.py" --include="*.yml" . | grep -v test_`
Expected: only the definition in `module_utils/argocd.py`. If a production caller appears, STOP and re-plan.

- [ ] **Step 2: Write the new parity tests (failing where constants don't exist yet)**

Replace lines 54–88 of `tests/test_argocd_constants_parity.py` with:

```python
import pathlib

_ROLE_TASKS = pathlib.Path("ansible_collections/tomazb/acm_switchover/roles/argocd_manage/tasks")


def test_paused_by_annotation_identical_across_all_definitions():
    """Triple-defined constant (audit M1): drift silently orphans every paused Application."""
    import lib.argocd
    import lib.constants
    from ansible_collections.tomazb.acm_switchover.plugins.module_utils import constants as ans_constants

    assert (
        lib.argocd.ARGOCD_PAUSED_BY_ANNOTATION
        == lib.constants.ARGOCD_PAUSED_BY_ANNOTATION
        == ans_constants.ARGOCD_PAUSED_BY_ANNOTATION
    )


def test_shipped_task_yaml_uses_the_shared_annotation_keys():
    """Guard the artifact that ships (audit M3): the inline YAML patch, not a helper
    nothing calls. Every annotation literal in pause.yml/resume.yml must be one of
    the shared constants."""
    from ansible_collections.tomazb.acm_switchover.plugins.module_utils import constants as ans_constants

    pause_text = (_ROLE_TASKS / "pause.yml").read_text()
    resume_text = (_ROLE_TASKS / "resume.yml").read_text()

    for text, name in ((pause_text, "pause.yml"), (resume_text, "resume.yml")):
        assert ans_constants.ARGOCD_PAUSED_BY_ANNOTATION in text, f"{name} must use the paused-by key"
        assert ans_constants.ARGOCD_ORIGINAL_SYNC_POLICY_ANNOTATION in text, f"{name} must use the policy key"

    # No stray acm-switchover annotation keys beyond the two shared ones.
    import re

    for text, name in ((pause_text, "pause.yml"), (resume_text, "resume.yml")):
        keys = set(re.findall(r"acm-switchover\.argoproj\.io/[a-z-]+", text))
        assert keys <= {
            ans_constants.ARGOCD_PAUSED_BY_ANNOTATION,
            ans_constants.ARGOCD_ORIGINAL_SYNC_POLICY_ANNOTATION,
        }, f"{name} uses unexpected annotation keys: {keys}"


def test_pause_patch_shape_matches_python():
    """Python pause sets syncPolicy.automated=None so merge-patch deletes the key
    (lib/argocd.py). The shipped pause.yml must do the same."""
    pause_text = (_ROLE_TASKS / "pause.yml").read_text()
    assert "combine({'automated': none})" in pause_text


def test_resume_never_defaults_missing_policy():
    """Cross-runtime data-loss guard (audit C1 / issue #184)."""
    resume_text = (_ROLE_TASKS / "resume.yml").read_text()
    assert "default('{}')" not in resume_text
```

- [ ] **Step 3: Run, verify FAIL** — `python -m pytest tests/test_argocd_constants_parity.py -v` from repo root. Expected: `ARGOCD_ORIGINAL_SYNC_POLICY_ANNOTATION` AttributeError.

- [ ] **Step 4: Implement**

(a) Add to `module_utils/constants.py` beside line 55:

```python
ARGOCD_ORIGINAL_SYNC_POLICY_ANNOTATION = "acm-switchover.argoproj.io/original-sync-policy"
```

(b) Delete `build_pause_patch` from `module_utils/argocd.py` (and any now-unused imports).
(c) `git rm ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_argocd_autosync.py` (remove the directory if now empty and nothing else needs it).

- [ ] **Step 5: Run, verify PASS** — repo-root parity file + collection unit suite. Run black: `black --line-length 120 tests/test_argocd_constants_parity.py ansible_collections/tomazb/acm_switchover/plugins/module_utils/constants.py ansible_collections/tomazb/acm_switchover/plugins/module_utils/argocd.py`

- [ ] **Step 6: Commit**

```bash
git add -A tests/test_argocd_constants_parity.py ansible_collections/tomazb/acm_switchover/plugins/module_utils/ ansible_collections/tomazb/acm_switchover/tests/unit/plugins/
git commit -m "test: guard shipped Argo CD task YAML, drop dead build_pause_patch (audit M1/M3)"
```

---

### Task 5: Remove scripts/argocd-manage.sh (second register)

**Files:**
- Delete: `scripts/argocd-manage.sh`, `tests/test_argocd_manage_script.py`
- Modify: `scripts/README.md` (lines ~17, 47, 54, 912–942), `docs/development/code-walkthrough.md` (~549, 569, 666, 735), `docs/development/e2e-test-plan.md` (~66–67), `AGENTS.md` (~453), `CHANGELOG.md`

- [ ] **Step 1: Enumerate every reference**

Run: `grep -rn "argocd-manage" --include="*.md" --include="*.py" --include="*.sh" --include="*.yml" . | grep -v CHANGELOG | grep -v ".git/"`
Every hit outside `CHANGELOG.md` and `docs/plans/` must be handled in Step 2.

- [ ] **Step 2: Delete and update**

```bash
git rm scripts/argocd-manage.sh tests/test_argocd_manage_script.py
```

For each doc reference: remove the section or replace with a pointer to the two surviving form factors (`acm_switchover.py --argocd-resume-only` / the `argocd_manage` collection role). Add under the CHANGELOG unreleased/next section:

```markdown
- Removed deprecated `scripts/argocd-manage.sh` and its `.state/argocd-pause-state.json`
  register. Use `acm_switchover.py --argocd-resume-only` or the `argocd_manage`
  collection role; each form factor keeps exactly one pause register (ADR-0001, #207).
```

- [ ] **Step 3: Verify no dangling references**

Re-run the Step 1 grep — only CHANGELOG (history + new entry) and plan/design docs may remain. Run `python -m pytest tests/ -q -k "argocd" --co` to confirm no collection errors from the deleted test module.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "refactor: remove deprecated argocd-manage.sh second pause register (#207)"
```

---

### Task 6: Record the decision in coexistence.md

**Files:**
- Modify: `ansible_collections/tomazb/acm_switchover/docs/coexistence.md` (extend the `## Pause register invariant` section, lines 117–134)

- [ ] **Step 1: Extend the section**

Replace the closing paragraph (current lines 131–134, the one claiming checkpoint/cluster-as-truth is the equivalent register) with the recorded decision:

```markdown
### Collection register decision (issue #207)

The collection's register **is** the cluster: the annotation pair
(`acm-switchover.argoproj.io/paused-by`,
`acm-switchover.argoproj.io/original-sync-policy`) written in the same patch
that pauses the Application. The collection deliberately does not duplicate the
Python state-file register or its confirmed/provisional/unknown states:

- Python needs three resolution states because its pause is two steps — persist
  the register entry, then patch. The collection's record rides inside the pause
  patch itself, so record and mutation are one atomic API call and there is no
  provisional window to describe. A failed or ambiguous patch is a failed task;
  the operator retries with the same run_id and the patch is idempotent.
- ADR-0001's load-bearing invariant — an obligation is discharged only when
  resume is proven complete; fail closed on ambiguity — is enforced directly:
  - resume fails when the Application CRD is not visible but
    `acm_switchover_argocd.run_id` is set (the rejected "clear register when CRD
    absent" shape is unreachable);
  - resume never patches `spec.syncPolicy` without a recoverable
    `original-sync-policy` annotation; a matching marker with a missing/empty
    policy fails the phase and routes Python-paused Applications to
    `acm_switchover.py --argocd-resume-only`;
  - an orphaned `original-sync-policy` annotation (marker absent) is reported
    and left untouched — ownership cannot be established.
- `acm_switchover_argocd.run_id` is the obligation signal: minted only after
  pause-mode discovery confirms Argo CD is installed, persisted to checkpoints
  without the `execution.run_id` fallback. Non-empty run_id ⇒ a pause may have
  landed ⇒ resume must prove discharge or fail.

Accepted residual divergences:

- The run_id reaches the checkpoint only at phase end. A crash between the
  first pause patch and checkpoint persistence leaves pauses whose run_id is in
  no checkpoint — but it is durable on the cluster in every `paused-by`
  annotation, and the standalone resume playbook accepts an explicit run_id.
- Simultaneous loss of both annotations (external strip, backup restore) is
  unrecoverable by the collection alone — the same class of loss as deleting
  the Python state file.
```

- [ ] **Step 2: Verify docs coherence**

Read the surrounding section after editing — the marker-ownership rules (lines 103–115) must not contradict the new text (they don't: matching remains run_id-exact; only the missing-policy and CRD-absent behaviours changed).

- [ ] **Step 3: Commit**

```bash
git add ansible_collections/tomazb/acm_switchover/docs/coexistence.md
git commit -m "docs: record collection pause-register decision (cluster-as-register, ADR-0001, #207)"
```

---

### Task 7: Full gates, push, PR

- [ ] **Step 1: Repo-wide gates**

```bash
black --line-length 120 --check tests/ ansible_collections/tomazb/acm_switchover/plugins/ ansible_collections/tomazb/acm_switchover/tests/
./run_tests.sh
(cd ansible_collections/tomazb/acm_switchover && python -m pytest tests/unit tests/integration -q)
git diff --check
```

All green. Fix regressions before proceeding.

- [ ] **Step 2: Push branch + draft PR**

```bash
git push -u origin worktree-issue-207-argocd-register-convergence
gh pr create --draft --base ansible --title "Converge collection Argo CD pause handling on ADR-0001 (#207)" --body "..."
```

PR body: summary of the decision + the seven changes, `Closes #207`, `Closes #184`, audit C1/C2/M1/M3 references, test evidence. End with the standard generated-with footer per harness rules — but NO Co-Authored-By trailer.
