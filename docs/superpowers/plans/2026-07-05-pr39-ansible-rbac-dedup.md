# Thermos PR 39 — Ansible RBAC Hub-Loop Deduplication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the duplicated primary/secondary RBAC validation blocks in
`roles/preflight/tasks/validate_rbac.yml` with a hub-data table plus an
`include_tasks` loop over one shared `validate_rbac_hub.yml`, mirroring Python
H1's `hub_validations` table + `_validate_hub(...)` loop.

**Architecture:** Per the approved design
[`docs/superpowers/specs/2026-07-05-pr39-ansible-rbac-dedup-design.md`](../specs/2026-07-05-pr39-ansible-rbac-dedup-design.md):
the main file keeps mode derivation, primary-only old-hub-finalization
determination, the once-computed observability skip state, the managed-cluster
section, the merge task, and the completion marker; a new shared hub task file
holds the 10-task per-hub sequence once, registering generic `*_hub`
intermediates and re-publishing every existing per-hub fact name via templated
`set_fact` keys.

**Tech Stack:** Ansible task files (collection `tomazb.acm_switchover`), pytest
static-contract tests, fixture-driven `ansible-playbook` integration tests.

## Global Constraints

- No Python source changes; `lib/rbac_validator.py` untouched.
- No changes to `docs/ACM_SWITCHOVER_RUNBOOK.md` or `.claude/skills/**/*.skill.md`.
- Rendered operator-facing messages byte-identical to today.
- Registered-fact names preserved: `_rbac_argocd_app_crd_<hub>`,
  `_rbac_argocd_instance_crd_<hub>`, `_rbac_argocd_install_type_<hub>`,
  `_rbac_expanded_<hub>`, `_rbac_denied_permissions_<hub>`,
  `acm_<hub>_rbac_validation` for hub ∈ {primary, secondary}.
- The forbidden literal `argocd_install_type: unknown` must not appear in either
  task file (generic per-iteration fact is named `_rbac_argocd_install_type_hub`
  so the suffix breaks the banned substring).
- Python test files formatted with `black --line-length 120` and
  `isort --profile black --line-length 120`.
- Test runs use the repo `.venv` (`source .venv/bin/activate` from the repo root
  works inside the worktree if `.venv` exists there; otherwise create one or use
  the system interpreter that already passes the suite).

---

### Task 1: Red-first contract tests

**Files:**
- Modify: `ansible_collections/tomazb/acm_switchover/tests/unit/test_preflight_parity.py`
  (replace `test_preflight_rbac_skips_observability_permissions_when_observability_absent`,
  append four new tests)
- Modify: `ansible_collections/tomazb/acm_switchover/tests/unit/test_ansible_resilience_contracts.py`
  (rewrite `test_preflight_validate_rbac_detects_argocd_install_type`,
  `test_collection_rbac_validation_runs_ssars_in_dry_run`,
  `test_preflight_validate_rbac_fails_closed_on_argocd_401`)

**Interfaces:**
- Produces: the contract the Task 2 YAML must satisfy — file
  `roles/preflight/tasks/validate_rbac_hub.yml`, fact table
  `_rbac_hub_validations`, loop var `rbac_hub`, loop `when:
  rbac_hub.enabled | bool`, templated published fact keys listed in Global
  Constraints.

- [ ] **Step 1: Replace the observability parity test** in
  `test_preflight_parity.py` with:

```python
def test_preflight_rbac_skips_observability_permissions_when_observability_absent():
    """Collection RBAC checks must mirror Python's automatic Observability absence handling."""
    text = (PREFLIGHT_TASKS / "validate_rbac.yml").read_text()
    hub_text = (PREFLIGHT_TASKS / "validate_rbac_hub.yml").read_text()

    assert "_rbac_skip_observability" in text
    assert "not (acm_switchover_primary_has_observability | default(false) | bool)" in text
    assert "not (acm_switchover_secondary_has_observability | default(false) | bool)" in text
    assert 'skip_observability: "{{ _rbac_skip_observability }}"' in hub_text
```

- [ ] **Step 2: Append the four new contract tests** to
  `test_preflight_parity.py`:

```python
def test_preflight_rbac_validates_hubs_through_shared_hub_loop():
    """Both hubs must flow through one parameterized include, mirroring Python H1's hub table + loop."""
    tasks = _load_yaml("validate_rbac.yml")

    hub_includes = [t for t in tasks if t.get("ansible.builtin.include_tasks") == "validate_rbac_hub.yml"]
    assert len(hub_includes) == 1, "validate_rbac.yml must drive hub validation through exactly one shared include"
    loop_task = hub_includes[0]
    assert loop_task["loop"] == "{{ _rbac_hub_validations }}"
    assert loop_task["loop_control"]["loop_var"] == "rbac_hub"
    assert loop_task["when"] == "rbac_hub.enabled | bool"
    assert (PREFLIGHT_TASKS / "validate_rbac_hub.yml").is_file()

    table_task = next(t for t in tasks if "_rbac_hub_validations" in t.get("ansible.builtin.set_fact", {}))
    entries = table_task["ansible.builtin.set_fact"]["_rbac_hub_validations"]
    assert [entry["hub"] for entry in entries] == ["primary", "secondary"]
    primary, secondary = entries
    assert "restore_only" in str(primary["enabled"]), "primary hub entry must be disabled in restore-only mode"
    assert secondary["enabled"] is True, "secondary hub validation must be unconditional"


def test_preflight_rbac_hub_loop_preserves_registered_fact_names():
    """The shared hub file must re-publish every per-hub fact the pre-loop file registered."""
    hub_text = (PREFLIGHT_TASKS / "validate_rbac_hub.yml").read_text()
    main_text = (PREFLIGHT_TASKS / "validate_rbac.yml").read_text()

    for published in [
        '"_rbac_argocd_app_crd_{{ rbac_hub.hub }}"',
        '"_rbac_argocd_instance_crd_{{ rbac_hub.hub }}"',
        '"_rbac_argocd_install_type_{{ rbac_hub.hub }}"',
        '"_rbac_expanded_{{ rbac_hub.hub }}"',
        '"_rbac_denied_permissions_{{ rbac_hub.hub }}"',
        '"acm_{{ rbac_hub.hub }}_rbac_validation"',
    ]:
        assert published in hub_text, f"validate_rbac_hub.yml must re-publish {published}"

    assert "acm_primary_rbac_validation.results" in main_text
    assert "acm_secondary_rbac_validation.results" in main_text
    assert "acm_managed_cluster_rbac_validation.results" in main_text


def test_preflight_rbac_hub_loop_keeps_primary_only_and_secondary_only_behavior():
    """Asymmetries must live in the hub table as data, exactly like Python H1's hub_validations."""
    tasks = _load_yaml("validate_rbac.yml")
    table_task = next(t for t in tasks if "_rbac_hub_validations" in t.get("ansible.builtin.set_fact", {}))
    primary, secondary = table_task["ansible.builtin.set_fact"]["_rbac_hub_validations"]

    assert "'decommission'" in primary["include_decommission"]
    assert "_rbac_include_old_hub_finalization_primary" in primary["include_old_hub_finalization"]
    assert secondary["include_decommission"] is False
    assert secondary["include_old_hub_finalization"] is False

    old_hub_task = next(t for t in tasks if "old-hub finalization" in t["name"])
    assert "restore_only" in str(old_hub_task.get("when", "")), "old-hub finalization must stay primary-only input"


def test_preflight_rbac_managed_cluster_validation_stays_separate_from_hub_loop():
    """Managed-cluster RBAC validation keeps its own scope/loop, matching Python's separate scope."""
    tasks = _load_yaml("validate_rbac.yml")
    includes = [t.get("ansible.builtin.include_tasks") for t in tasks if t.get("ansible.builtin.include_tasks")]
    assert "validate_managed_cluster_rbac.yml" in includes

    hub_text = (PREFLIGHT_TASKS / "validate_rbac_hub.yml").read_text()
    assert "validate_managed_cluster_rbac" not in hub_text
    assert "managed_cluster" not in hub_text
```

- [ ] **Step 3: Rewrite the three resilience tests** in
  `test_ansible_resilience_contracts.py`:

```python
def test_preflight_validate_rbac_detects_argocd_install_type():
    """preflight RBAC validation must detect Argo CD install type instead of hardcoding unknown."""
    hub_tasks = _load_yaml(PREFLIGHT_TASKS / "validate_rbac_hub.yml")
    hub_text = (PREFLIGHT_TASKS / "validate_rbac_hub.yml").read_text()
    main_text = (PREFLIGHT_TASKS / "validate_rbac.yml").read_text()
    argocds_crd = ".".join(("argocds", "argoproj", "io"))
    applications_crd = ".".join(("applications", "argoproj", "io"))

    crd_queries = [
        task
        for task in hub_tasks
        if task.get("kubernetes.core.k8s_info", {}).get("kind") == "CustomResourceDefinition"
    ]
    assert crd_queries, "validate_rbac_hub.yml must query Argo CD CRDs to determine install type"
    assert argocds_crd in hub_text, "validate_rbac_hub.yml must detect operator installs via the argocds CRD"
    for text in (main_text, hub_text):
        assert (
            "argocd_install_type: unknown" not in text
        ), "RBAC validation must stop widening permissions with a hardcoded unknown install type"
    assert (
        applications_crd in hub_text
    ), "validate_rbac_hub.yml must probe the applications CRD to distinguish vanilla Argo CD from no install"
    assert "'check'" in main_text, "validate_rbac.yml must support the read-only Argo CD RBAC check mode"
    assert "skip_gitops_check" in main_text, "validate_rbac.yml must derive Argo CD RBAC mode from skip_gitops_check"
```

```python
def test_collection_rbac_validation_runs_ssars_in_dry_run():
    """Dry-run must still validate RBAC with non-mutating SSAR/SAR requests."""
    for path in [
        PREFLIGHT_TASKS / "validate_rbac.yml",
        PREFLIGHT_TASKS / "validate_rbac_hub.yml",
        DECOMMISSION_TASKS / "validate_rbac.yml",
    ]:
        text = path.read_text()
        assert "mode | default('dry_run') == 'dry_run'" not in text
        assert "mode | default('dry_run') != 'dry_run'" not in text

    for path in [
        PREFLIGHT_TASKS / "validate_rbac_hub.yml",
        DECOMMISSION_TASKS / "validate_rbac.yml",
    ]:
        assert "run_ssar" in path.read_text()
```

```python
def test_preflight_validate_rbac_fails_closed_on_argocd_401():
    """The shared hub RBAC task file must fail closed on HTTP 401 during Argo CD CRD discovery.

    401 indicates broken/expired credentials, not merely missing CRD read permission
    (which is 403). Silently deferring 401 to RBAC validation would hide auth problems
    and potentially produce misleading "missing permissions" results. This mirrors the
    Python CLI behavior in preflight_coordinator.py. Since the hub-loop refactor
    (Thermos PR 39 / R2-H3), both hubs run the same shared fail-closed tasks, driven
    by the _rbac_hub_validations table in validate_rbac.yml.
    """
    hub_tasks = _load_yaml(PREFLIGHT_TASKS / "validate_rbac_hub.yml")
    main_tasks = _load_yaml(PREFLIGHT_TASKS / "validate_rbac.yml")

    loop_task = next(t for t in main_tasks if t.get("ansible.builtin.include_tasks") == "validate_rbac_hub.yml")
    assert loop_task["loop"] == "{{ _rbac_hub_validations }}"
    table_task = next(t for t in main_tasks if "_rbac_hub_validations" in t.get("ansible.builtin.set_fact", {}))
    hubs = [entry["hub"] for entry in table_task["ansible.builtin.set_fact"]["_rbac_hub_validations"]]
    assert hubs == ["primary", "secondary"], "the 401 fail-closed path must cover both hubs via the hub table"

    fail_tasks = [t for t in hub_tasks if "ansible.builtin.fail" in t]
    auth_fail_tasks = [
        t
        for t in fail_tasks
        if "authorization denied" in t.get("ansible.builtin.fail", {}).get("msg", "").lower()
        or "401" in t.get("ansible.builtin.fail", {}).get("msg", "")
    ]
    assert auth_fail_tasks, "validate_rbac_hub.yml must have a fail-closed task for 401/unauthorized"
    for task in auth_fail_tasks:
        assert (
            "{{ rbac_hub.hub }}" in task["ansible.builtin.fail"]["msg"]
        ), "401 fail-closed message must name the hub being validated"

    unexpected_fail_tasks = [
        t for t in fail_tasks if "unable to inspect" in t.get("ansible.builtin.fail", {}).get("msg", "").lower()
    ]
    assert unexpected_fail_tasks, "validate_rbac_hub.yml must fail on unexpected CRD discovery errors"
    for task in unexpected_fail_tasks:
        assert "{{ rbac_hub.hub }}" in task["ansible.builtin.fail"]["msg"]
        when = _when_text(task)
        assert (
            "'401' not in" not in when
        ), "Unexpected-error fail task must not exclude 401 — the dedicated 401 task handles it"
        assert (
            "'unauthorized' not in" not in when
        ), "Unexpected-error fail task must not exclude unauthorized — the dedicated 401 task handles it"
        assert "'403' not in" in when, "Unexpected-error fail task must still exclude 403 (deferred to RBAC validation)"

    for task in auth_fail_tasks + unexpected_fail_tasks:
        when = _when_text(task)
        assert ".failed" not in when, (
            "Error detection must use .msg presence, not .failed — "
            "failed_when: false on k8s_info overrides .failed to False"
        )

    for task in unexpected_fail_tasks:
        when = _when_text(task)
        assert ".msg" in when, (
            "Unexpected-error fail task must gate on .msg presence " "since failed_when: false overrides .failed"
        )

    auth_indices = [i for i, t in enumerate(hub_tasks) if t in auth_fail_tasks]
    unexpected_indices = [i for i, t in enumerate(hub_tasks) if t in unexpected_fail_tasks]
    assert auth_indices and unexpected_indices
    assert auth_indices[0] < unexpected_indices[0], "401 fail task must precede the unexpected-error fail task"
```

- [ ] **Step 4: Run the touched tests to verify they fail** (missing
  `validate_rbac_hub.yml`):

Run: `python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/test_preflight_parity.py ansible_collections/tomazb/acm_switchover/tests/unit/test_ansible_resilience_contracts.py -q`
Expected: FAIL — the 5 parity tests and 3 resilience tests above error with
`FileNotFoundError: ... validate_rbac_hub.yml` (or assertion failures); all
other tests still pass.

- [ ] **Step 5: Format and commit**

```bash
black --line-length 120 ansible_collections/tomazb/acm_switchover/tests/unit/test_preflight_parity.py ansible_collections/tomazb/acm_switchover/tests/unit/test_ansible_resilience_contracts.py
isort --profile black --line-length 120 ansible_collections/tomazb/acm_switchover/tests/unit/test_preflight_parity.py ansible_collections/tomazb/acm_switchover/tests/unit/test_ansible_resilience_contracts.py
git add -A ansible_collections/tomazb/acm_switchover/tests/unit/
git commit -m "test: pin hub-loop contract for preflight RBAC validation (red)"
```

### Task 2: Create `validate_rbac_hub.yml` and rewrite `validate_rbac.yml`

**Files:**
- Create: `ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/validate_rbac_hub.yml`
- Modify: `ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/validate_rbac.yml`
  (replace lines 15–253 — the duplicated hub blocks plus the two retained
  single-computation tasks — with the retained tasks, the hub table, and the loop)

**Interfaces:**
- Consumes: contract pinned by Task 1.
- Produces: identical registered-fact surface as before the refactor (see
  Global Constraints); managed-cluster section, merge task, and completion
  marker unchanged.

- [ ] **Step 1: Create `validate_rbac_hub.yml`** with exactly:

```yaml
---
# Shared per-hub RBAC validation, driven by the _rbac_hub_validations table in
# validate_rbac.yml. Mirrors Python H1's _validate_hub(...) helper in
# lib/rbac_validator.py: one parameterized body, per-hub asymmetries supplied
# as data by the caller's hub table.
#
# Input (rbac_hub loop entry):
#   - rbac_hub.hub: 'primary' or 'secondary'
#   - rbac_hub.kubeconfig / rbac_hub.context: hub connection details
#   - rbac_hub.include_decommission: primary-only decommission permission checks
#   - rbac_hub.include_old_hub_finalization: primary-only old-hub finalization checks
# Output (per-hub facts, names preserved from the pre-loop implementation):
#   - _rbac_argocd_app_crd_<hub>, _rbac_argocd_instance_crd_<hub>
#   - _rbac_argocd_install_type_<hub>
#   - _rbac_expanded_<hub>, _rbac_denied_permissions_<hub>
#   - acm_<hub>_rbac_validation

- name: Default Argo CD install type for {{ rbac_hub.hub }} hub when checks are disabled
  ansible.builtin.set_fact:
    _rbac_argocd_install_type_hub: unknown
  when: _rbac_requested_argocd_mode == 'none'

- name: Detect Argo CD Applications CRD on {{ rbac_hub.hub }} hub
  kubernetes.core.k8s_info:
    api_version: apiextensions.k8s.io/v1
    kind: CustomResourceDefinition
    name: applications.argoproj.io
    kubeconfig: "{{ rbac_hub.kubeconfig }}"
    context: "{{ rbac_hub.context }}"
  register: _rbac_argocd_app_crd_hub
  failed_when: false
  when: _rbac_requested_argocd_mode != 'none'

- name: Detect Argo CD operator CRD on {{ rbac_hub.hub }} hub
  kubernetes.core.k8s_info:
    api_version: apiextensions.k8s.io/v1
    kind: CustomResourceDefinition
    name: argocds.argoproj.io
    kubeconfig: "{{ rbac_hub.kubeconfig }}"
    context: "{{ rbac_hub.context }}"
  register: _rbac_argocd_instance_crd_hub
  failed_when: false
  when:
    - _rbac_requested_argocd_mode != 'none'
    - (_rbac_argocd_app_crd_hub.msg | default('')) | length == 0
    - (_rbac_argocd_app_crd_hub.resources | default([]) | length) > 0

- name: Fail closed on authorization denied during Argo CD CRD discovery for {{ rbac_hub.hub }} hub
  ansible.builtin.fail:
    msg: >-
      Authorization denied while inspecting Argo CD CRDs on the {{ rbac_hub.hub }} hub
      (HTTP 401). This usually means the kubeconfig credentials are expired or
      invalid. Fix the credentials and retry.
      Detail: {{ _rbac_argocd_app_crd_hub.msg | default('no detail') }}
  when:
    - _rbac_requested_argocd_mode != 'none'
    - >-
      '401' in ((_rbac_argocd_app_crd_hub.msg | default('')) | lower)
      or 'unauthorized' in ((_rbac_argocd_app_crd_hub.msg | default('')) | lower)

- name: Fail on unexpected Argo CD Applications CRD discovery error for {{ rbac_hub.hub }} hub
  ansible.builtin.fail:
    msg: >-
      Unable to inspect applications.argoproj.io on the {{ rbac_hub.hub }} hub during RBAC preflight:
      {{ _rbac_argocd_app_crd_hub.msg | default(_rbac_argocd_app_crd_hub | string) }}
  when:
    - _rbac_requested_argocd_mode != 'none'
    - (_rbac_argocd_app_crd_hub.msg | default('')) | length > 0
    - "'403' not in ((_rbac_argocd_app_crd_hub.msg | default('')) | lower)"
    - "'forbidden' not in ((_rbac_argocd_app_crd_hub.msg | default('')) | lower)"

- name: Record Argo CD install type for {{ rbac_hub.hub }} hub
  ansible.builtin.set_fact:
    _rbac_argocd_install_type_hub: >-
      {%- set app_msg = (_rbac_argocd_app_crd_hub.msg | default('')) | lower -%}
      {%- if app_msg | length > 0 -%}
      unknown
      {%- elif (_rbac_argocd_app_crd_hub.resources | default([]) | length) == 0 -%}
      none
      {%- elif (_rbac_argocd_instance_crd_hub.msg | default('')) | length > 0 -%}
      unknown
      {%- elif (_rbac_argocd_instance_crd_hub.resources | default([]) | length) > 0 -%}
      operator
      {%- else -%}
      vanilla
      {%- endif -%}
  when: _rbac_requested_argocd_mode != 'none'

- name: Expand required RBAC permissions for {{ rbac_hub.hub }} hub
  tomazb.acm_switchover.acm_rbac_validate:
    hub: "{{ rbac_hub.hub }}"
    include_decommission: "{{ rbac_hub.include_decommission }}"
    include_old_hub_finalization: "{{ rbac_hub.include_old_hub_finalization }}"
    skip_observability: "{{ _rbac_skip_observability }}"
    argocd_mode: "{{ _rbac_requested_argocd_mode }}"
    argocd_install_type: "{{ _rbac_argocd_install_type_hub | default('unknown') }}"
  register: _rbac_expanded_hub

- name: Run SelfSubjectAccessReview checks for {{ rbac_hub.hub }} hub
  ansible.builtin.include_tasks: run_ssar.yml
  vars:
    acm_rbac_permissions: "{{ _rbac_expanded_hub.permissions }}"
    _ssar_target_kubeconfig: "{{ rbac_hub.kubeconfig }}"
    _ssar_target_context: "{{ rbac_hub.context }}"

- name: Collect denied permissions for {{ rbac_hub.hub }} hub
  ansible.builtin.set_fact:
    _rbac_denied_permissions_hub: "{{ _rbac_denied_permissions }}"

- name: Summarize RBAC validation results for {{ rbac_hub.hub }} hub
  tomazb.acm_switchover.acm_rbac_validate:
    hub: "{{ rbac_hub.hub }}"
    include_decommission: "{{ rbac_hub.include_decommission }}"
    include_old_hub_finalization: "{{ rbac_hub.include_old_hub_finalization }}"
    skip_observability: "{{ _rbac_skip_observability }}"
    argocd_mode: "{{ _rbac_requested_argocd_mode }}"
    argocd_install_type: "{{ _rbac_argocd_install_type_hub | default('unknown') }}"
    denied_permissions: "{{ _rbac_denied_permissions_hub | default([]) }}"
  register: _rbac_hub_validation_result

- name: Publish RBAC validation facts for {{ rbac_hub.hub }} hub
  ansible.builtin.set_fact:
    "_rbac_argocd_app_crd_{{ rbac_hub.hub }}": "{{ _rbac_argocd_app_crd_hub | default({}) }}"
    "_rbac_argocd_instance_crd_{{ rbac_hub.hub }}": "{{ _rbac_argocd_instance_crd_hub | default({}) }}"
    "_rbac_argocd_install_type_{{ rbac_hub.hub }}": "{{ _rbac_argocd_install_type_hub | default('unknown') }}"
    "_rbac_expanded_{{ rbac_hub.hub }}": "{{ _rbac_expanded_hub }}"
    "_rbac_denied_permissions_{{ rbac_hub.hub }}": "{{ _rbac_denied_permissions_hub }}"
    "acm_{{ rbac_hub.hub }}_rbac_validation": "{{ _rbac_hub_validation_result }}"
```

- [ ] **Step 2: Rewrite `validate_rbac.yml`.** Keep lines 1–13 (mode
  derivation) verbatim, keep the managed-cluster section, merge task, and
  completion marker (current lines 255–317) verbatim, and replace everything in
  between (current lines 15–253) with:

```yaml
- name: Determine primary old-hub finalization RBAC requirement
  ansible.builtin.set_fact:
    _rbac_include_old_hub_finalization_primary: >-
      {{
        (acm_switchover_operation.old_hub_action | default('secondary')) == 'secondary'
        and not (acm_switchover_features.skip_observability_checks | default(false) | bool)
        and 'open-cluster-management-observability' in (
          acm_primary_namespace_names
          | default((acm_primary_namespace_info.resources | default([])) | map(attribute='metadata.name') | list)
        )
      }}
  when: not (acm_switchover_operation.restore_only | default(false))

- name: Determine Observability RBAC skip state
  ansible.builtin.set_fact:
    _rbac_skip_observability: >-
      {{
        (acm_switchover_features.skip_observability_checks | default(false) | bool)
        or (
          not (acm_switchover_primary_has_observability | default(false) | bool)
          and not (acm_switchover_secondary_has_observability | default(false) | bool)
        )
      }}

- name: Build hub RBAC validation table
  ansible.builtin.set_fact:
    _rbac_hub_validations:
      # Asymmetries are explicit data, mirroring Python H1's hub_validations
      # table in lib/rbac_validator.py:
      # - the primary hub is skipped entirely in restore-only mode;
      # - decommission/old-hub-finalization checks apply to the primary hub only.
      - hub: primary
        enabled: "{{ not (acm_switchover_operation.restore_only | default(false)) }}"
        kubeconfig: "{{ acm_switchover_hubs.primary.kubeconfig }}"
        context: "{{ acm_switchover_hubs.primary.context }}"
        include_decommission: "{{ acm_switchover_operation.old_hub_action | default('secondary') == 'decommission' }}"
        include_old_hub_finalization: "{{ _rbac_include_old_hub_finalization_primary | default(false) }}"
      - hub: secondary
        enabled: true
        kubeconfig: "{{ acm_switchover_hubs.secondary.kubeconfig }}"
        context: "{{ acm_switchover_hubs.secondary.context }}"
        include_decommission: false  # secondary hub never runs decommission
        include_old_hub_finalization: false

- name: Validate RBAC permissions per hub
  ansible.builtin.include_tasks: validate_rbac_hub.yml
  loop: "{{ _rbac_hub_validations }}"
  loop_control:
    loop_var: rbac_hub
    label: "{{ rbac_hub.hub }}"
  when: rbac_hub.enabled | bool
```

- [ ] **Step 3: Run the Task 1 tests to verify they pass**

Run: `python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/test_preflight_parity.py ansible_collections/tomazb/acm_switchover/tests/unit/test_ansible_resilience_contracts.py -q`
Expected: PASS (all tests).

- [ ] **Step 4: Run the fixture-driven integration tests** (execute the real
  loop, templated `set_fact` keys, and merge task under `ansible-playbook`):

Run: `python -m pytest ansible_collections/tomazb/acm_switchover/tests/integration/test_preflight_role.py ansible_collections/tomazb/acm_switchover/tests/integration/test_restore_only_role.py -q`
Expected: PASS, notably
`test_preflight_rbac_failure_still_reports_backup_findings` (asserts
`preflight-rbac-primary` and `preflight-rbac-secondary` both fail) and the
restore-only run (primary loop iteration skipped).

- [ ] **Step 5: Commit**

```bash
git add ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/validate_rbac.yml ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/validate_rbac_hub.yml
git commit -m "refactor: dedupe preflight RBAC hub validation via hub table + shared include (R2-H3)"
```

### Task 3: Full verification gate

**Files:** none (verification only)

- [ ] **Step 1:** `git diff --check` — Expected: no output.
- [ ] **Step 2:** Collection unit suite:
  `python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/ -q` — Expected: PASS.
- [ ] **Step 3:** Collection integration suite:
  `python -m pytest ansible_collections/tomazb/acm_switchover/tests/integration/ -q` — Expected: PASS.
- [ ] **Step 4:** Root RBAC/parity suites:
  `python -m pytest tests/test_rbac_validator.py tests/test_check_rbac.py tests/test_rbac_collection_parity.py tests/test_rbac_integration.py tests/release/checks/test_rbac_certification.py -q` — Expected: PASS.
- [ ] **Step 5:** Full gate: `./run_tests.sh` — Expected: PASS (root lane,
  release lane, Black/isort/MyPy/Bandit clean).

### Task 4: Tracker, changelog

**Files:**
- Modify: `thermos-resolution-plan.md` (PR 39 row: `planned` →
  `ready_for_review`, worktree path correction to
  `.claude/worktrees/thermos-39-ansible-rbac-dedup`, spec/plan paths,
  verification evidence, PR URL after opening; H1 row: `ready_for_review` →
  `merged` reconciliation since PR #148 merged at `0afeea52` and this branch is
  created from that base; `Last Updated` → 2026-07-05)
- Modify: `CHANGELOG.md` (`[Unreleased]` → `### Changed` entry)

- [ ] **Step 1:** Update the tracker rows and `Last Updated`.
- [ ] **Step 2:** Add changelog entry under `## [Unreleased]` / `### Changed`:

```markdown
- Deduplicated the Ansible preflight RBAC validation task file: primary/secondary
  hub blocks now run through one hub-parameterized shared task file driven by an
  explicit per-hub table, mirroring the Python H1 hub-role loop (Thermos R2-H3);
  registered facts, fail-closed CRD discovery behavior, and operator-facing
  messages are unchanged.
```

- [ ] **Step 3:** Commit:

```bash
git add thermos-resolution-plan.md CHANGELOG.md
git commit -m "docs: record Thermos PR 39 Ansible RBAC hub-loop dedup in tracker and changelog"
```

### Task 5: Code review gate and PR

- [ ] **Step 1:** Run the `code-review` skill against the branch diff; address
  critical/warning findings (re-run after changes).
- [ ] **Step 2:** Push and open the draft PR:

```bash
git push -u origin refactor/thermos-39-ansible-rbac-dedup
gh pr create --draft --base ansible --title "Thermos PR 39: mirror Python H1 hub-loop in Ansible RBAC validation (R2-H3)" --body-file <prepared body>
```

PR body must include: tracker slice/finding ID, H1-merged confirmation, files
changed, spec/plan paths, registered-fact preservation summary, fail-closed
preservation summary, asymmetry table, parity impact, verification commands +
results, protected-file confirmation, remaining follow-ups (PR 43).

- [ ] **Step 3:** Record the PR URL in the tracker row and push the tracker
  update.
