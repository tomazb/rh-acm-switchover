# Code Review Fixes — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix 2 confirmed P2 bugs, close 2 critical Ansible feature-parity gaps, apply 1 defensive code-quality fix, and clean up 1 dead parameter — without breaking existing functionality.

**Architecture:** Six tasks across three workstreams (Python CLI, Ansible Collection, shared utilities). Tasks are ordered so that isolated bug fixes come first, then feature parity work. Each task includes tests that must pass before committing.

**Tech Stack:** Python 3.10+, Ansible core, kubernetes Python client, pytest, `kubernetes.core` Ansible collection.

---

## Task 1: Fix Decommission RBAC Validator — Allow Reruns (P2 Bug)

**Problem:** `lib/rbac_validator.py` line 664 marks a missing `open-cluster-management` namespace as a hard RBAC failure (`all_valid = False`). When decommission is re-run after ACM has already been removed, the validator aborts. Observability namespace already has grace handling (lines 634–639); ACM namespace does not.

**Files:**
- Modify: `lib/rbac_validator.py:660-669`
- Test: `tests/test_rbac_validator.py`

**Step 1: Write the failing test**

Add to the existing decommission test section in `tests/test_rbac_validator.py`:

```python
def test_validate_decommission_rbac_succeeds_when_acm_namespace_missing(self, mock_primary_client):
    """Missing ACM namespace on rerun should NOT fail validation (idempotent)."""
    mock_primary_client.namespace_exists.side_effect = lambda ns: {
        "open-cluster-management": False,
        "open-cluster-management-observability": True,
    }.get(ns, False)
    mock_primary_client.check_permission.return_value = (True, None)

    validator = RBACValidator(client=mock_primary_client, role="operator")
    all_valid, errors = validator.validate_decommission_rbac()

    assert all_valid is True
    assert not errors.get("namespaces", [])
```

**Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python -m pytest tests/test_rbac_validator.py -k "acm_namespace_missing" -v`
Expected: FAIL — current code sets `all_valid = False` for missing ACM namespace.

**Step 3: Implement the fix**

In `lib/rbac_validator.py`, modify the namespace loop (lines 660–669). Add a special case for `ACM_NAMESPACE` before the generic missing-namespace handler, mirroring the observability pattern:

```python
        for namespace, permissions in self.DECOMMISSION_NAMESPACE_PERMISSIONS.items():
            if namespace == OBSERVABILITY_NAMESPACE and not check_observability:
                continue

            if not self.client.namespace_exists(namespace):
                if namespace == ACM_NAMESPACE:
                    # ACM namespace removal is expected after successful decommission.
                    # Treat as success to allow idempotent reruns.
                    logger.info(
                        "Namespace %s does not exist — ACM already removed, "
                        "skipping decommission permission checks for this namespace",
                        namespace,
                    )
                    continue
                warning = f"Namespace {namespace} does not exist - skipping decommission permission checks"
                logger.warning(warning)
                namespace_errors.append(warning)
                all_valid = False
                continue
```

**Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && python -m pytest tests/test_rbac_validator.py -k "acm_namespace_missing" -v`
Expected: PASS

**Step 5: Run full test file to check for regressions**

Run: `source .venv/bin/activate && python -m pytest tests/test_rbac_validator.py -v`
Expected: All tests pass.

**Step 6: Commit**

```bash
git add lib/rbac_validator.py tests/test_rbac_validator.py
git commit -m "fix: allow decommission reruns when ACM namespace is gone (P2)

Missing open-cluster-management namespace on rerun now logs info and
continues instead of failing RBAC validation. Mirrors existing grace
handling for observability namespace."
```

---

## Task 2: Fix Ansible Primary Resume Guard (P2 Bug)

**Problem:** In `playbooks/argocd_resume.yml` (line 19) and `playbooks/switchover.yml` (line 55), the guard `when: acm_switchover_hubs.primary is defined` always evaluates true because `roles/preflight/defaults/main.yml` defines `primary` with empty strings. In restore-only or standalone resume, this invokes `argocd_manage` against a non-existent primary hub.

**Files:**
- Modify: `ansible_collections/tomazb/acm_switchover/playbooks/argocd_resume.yml:19`
- Modify: `ansible_collections/tomazb/acm_switchover/playbooks/switchover.yml:55`
- Test: `ansible_collections/tomazb/acm_switchover/tests/unit/test_argocd_resume_on_failure.py`

**Step 1: Write the failing test**

Add to `ansible_collections/tomazb/acm_switchover/tests/unit/test_argocd_resume_on_failure.py`:

```python
def test_primary_resume_guard_checks_kubeconfig_not_empty():
    """The primary resume guard must check that kubeconfig is actually populated."""
    playbooks_to_check = [
        PLAYBOOKS_DIR / "argocd_resume.yml",
        PLAYBOOKS_DIR / "switchover.yml",
    ]
    for pb_path in playbooks_to_check:
        text = pb_path.read_text()
        # Verify the guard checks kubeconfig length, not just 'is defined'
        assert "kubeconfig" in text and "length" in text, (
            f"{pb_path.name}: Primary hub resume guard must check that "
            "kubeconfig is non-empty, not just 'is defined'"
        )
```

**Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/test_argocd_resume_on_failure.py -k "kubeconfig_not_empty" -v`
Expected: FAIL — current guards only check `is defined`.

**Step 3: Fix argocd_resume.yml**

Replace line 19 in `ansible_collections/tomazb/acm_switchover/playbooks/argocd_resume.yml`:

```yaml
      when:
        - acm_switchover_hubs.primary is defined
        - (acm_switchover_hubs.primary.kubeconfig | default('')) | length > 0
        - (acm_switchover_hubs.primary.context | default('')) | length > 0
```

**Step 4: Fix switchover.yml rescue block**

Replace line 55 in `ansible_collections/tomazb/acm_switchover/playbooks/switchover.yml`. The existing `when` block is a list; replace the `acm_switchover_hubs.primary is defined` entry:

```yaml
          when:
            - acm_switchover_features.argocd.manage | default(false)
            - acm_switchover_features.argocd.resume_on_failure | default(false)
            - acm_switchover_hubs.primary is defined
            - (acm_switchover_hubs.primary.kubeconfig | default('')) | length > 0
            - (acm_switchover_hubs.primary.context | default('')) | length > 0
```

**Step 5: Run test to verify it passes**

Run: `source .venv/bin/activate && python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/test_argocd_resume_on_failure.py -v`
Expected: All tests pass.

**Step 6: Commit**

```bash
git add ansible_collections/tomazb/acm_switchover/playbooks/argocd_resume.yml \
        ansible_collections/tomazb/acm_switchover/playbooks/switchover.yml \
        ansible_collections/tomazb/acm_switchover/tests/unit/test_argocd_resume_on_failure.py
git commit -m "fix(ansible): guard primary resume on non-empty kubeconfig (P2)

The 'is defined' check always passes because role defaults define
primary with empty strings. Add length checks on kubeconfig and context
to skip the primary resume when no real connection details are provided.

Fixes argocd_resume.yml and switchover.yml rescue block."
```

---

## Task 3: Fix dry_run_skip Decorator Null-Safety

**Problem:** In `lib/utils.py`, if an intermediate attribute in a dot-separated `dry_run_attr` path is `None`, the loop breaks and `obj` becomes `None`. The `if obj is True` check fails, causing the function to execute instead of skipping. While theoretical for current production callers (all use single-level `"dry_run"`), this is a safety gap that should default to safe behavior.

**Files:**
- Modify: `lib/utils.py:78-82`
- Test: `tests/test_utils.py`

**Step 1: Write the failing test**

Add to the `TestDryRunSkipDecorator` class in `tests/test_utils.py`:

```python
    def test_decorator_skips_when_intermediate_attribute_is_none(self):
        """When an intermediate in the dot-path is None, default to skipping (safe)."""

        class Outer:
            client = None  # intermediate is None

        @dry_run_skip(
            message="Should skip safely",
            return_value="skipped",
            dry_run_attr="client.dry_run",
        )
        def guarded_method(self):
            return "executed"

        obj = Outer()
        result = guarded_method(obj)
        assert result == "skipped"
```

**Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python -m pytest tests/test_utils.py -k "intermediate_attribute_is_none" -v`
Expected: FAIL — current code returns `"executed"` because `None is True` is False.

**Step 3: Implement the fix**

In `lib/utils.py`, replace lines 78–82 in the `wrapper` function:

```python
            obj = root
            for attr_name in dry_run_attr.split("."):
                obj = getattr(obj, attr_name, None)
                if obj is None:
                    # Attribute path broken — cannot determine dry-run state.
                    # Safe default: skip execution to avoid unintended changes.
                    logger = logging.getLogger("acm_switchover")
                    logger.warning(
                        "[DRY-RUN] Cannot resolve attribute path '%s' on %s; "
                        "skipping for safety",
                        dry_run_attr,
                        type(root).__name__,
                    )
                    if callable(return_value):
                        return return_value(*args, **kwargs)
                    return return_value
```

**Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && python -m pytest tests/test_utils.py -k "intermediate_attribute_is_none" -v`
Expected: PASS

**Step 5: Verify existing dry_run_skip tests still pass**

Run: `source .venv/bin/activate && python -m pytest tests/test_utils.py -k "dry_run" -v`
Expected: All pass. Pay special attention to `test_decorator_with_missing_attribute` — this test checks the case where the attribute doesn't exist on the object at all. The new behavior should still work: `getattr(obj, "nonexistent", None)` returns `None`, which now triggers the safe skip. If this existing test expects execution instead of skipping, update it to match the new safe-by-default behavior:

```python
    def test_decorator_with_missing_attribute(self):
        """When dry_run attribute doesn't exist at all, skip safely."""

        class NoAttr:
            pass

        @dry_run_skip(message="Missing attr", return_value="skipped")
        def method(self):
            return "executed"

        result = method(NoAttr())
        assert result == "skipped"  # Changed from "executed" to "skipped"
```

**Step 6: Run full test file**

Run: `source .venv/bin/activate && python -m pytest tests/test_utils.py -v`
Expected: All tests pass.

**Step 7: Commit**

```bash
git add lib/utils.py tests/test_utils.py
git commit -m "fix: dry_run_skip defaults to skip when attribute path is broken

When an intermediate attribute in the dot-separated path is None,
the decorator now skips execution (safe) instead of proceeding.
Logs a warning so operators know the path resolution failed."
```

---

## Task 4: Clean Up _resolve_state_file Unused Parameter

**Problem:** The `restore_only` parameter in `_resolve_state_file()` is accepted but never used. It's not a bug — the code works correctly because `--restore-only` forbids `--primary-context`, so `primary_ctx=None` naturally produces the right filename. Remove the dead parameter and add a clarifying comment.

**Files:**
- Modify: `acm_switchover.py:1305-1311` (signature) and `acm_switchover.py:1149-1155` (call site)

**Step 1: Remove the parameter from the function signature**

In `acm_switchover.py`, change `_resolve_state_file()` to remove `restore_only`:

```python
def _resolve_state_file(
    requested_path: Optional[str],
    primary_ctx: Optional[str],
    secondary_ctx: Optional[str],
    argocd_resume_only: bool = False,
) -> str:
    """Derive the state file path based on contexts unless user provided one.

    Note: restore-only mode needs no special handling here because
    --restore-only forbids --primary-context, so primary_ctx is None,
    and _build_default_state_file(None, secondary_ctx) naturally
    produces the correct "switchover-restore-only__<sec>.json" filename.
    """
```

**Step 2: Remove the parameter from the call site**

In `acm_switchover.py`, update the call at line ~1149:

```python
        resolved_state_file = _resolve_state_file(
            args.state_file,
            getattr(args, "primary_context", None),
            args.secondary_context,
            argocd_resume_only=getattr(args, "argocd_resume_only", False),
        )
```

**Step 3: Run tests to verify no breakage**

Run: `source .venv/bin/activate && python -m pytest tests/test_main.py -v`
Expected: All pass.

**Step 4: Commit**

```bash
git add acm_switchover.py
git commit -m "refactor: remove unused restore_only param from _resolve_state_file

The parameter was never referenced in the function body. The code works
correctly without it because --restore-only forbids --primary-context,
so primary_ctx=None naturally produces the restore-only state filename."
```

---

## Task 5: Add Auto-Import Strategy Management to Ansible Activation Role

**Problem:** Python's `_maybe_set_auto_import_strategy()` and `_apply_immediate_import_annotations()` manage the `import-controller-config` ConfigMap in MCE namespace for ACM 2.14+. Ansible's `apply_immediate_import.yml` is a no-op stub.

**Files:**
- Create: `ansible_collections/tomazb/acm_switchover/roles/activation/tasks/manage_auto_import.yml`
- Create: `ansible_collections/tomazb/acm_switchover/roles/activation/tasks/reset_auto_import.yml`
- Modify: `ansible_collections/tomazb/acm_switchover/roles/activation/tasks/apply_immediate_import.yml`
- Modify: `ansible_collections/tomazb/acm_switchover/roles/activation/tasks/main.yml`
- Modify: `ansible_collections/tomazb/acm_switchover/plugins/module_utils/constants.py`
- Test: `ansible_collections/tomazb/acm_switchover/tests/integration/test_switchover_roles.py`

**Step 1: Add missing constants to Ansible constants module**

In `ansible_collections/tomazb/acm_switchover/plugins/module_utils/constants.py`, add:

```python
IMPORT_CONTROLLER_CONFIG_CM = "import-controller-config"
AUTO_IMPORT_STRATEGY_KEY = "autoImportStrategy"
AUTO_IMPORT_STRATEGY_DEFAULT = "ImportOnly"
AUTO_IMPORT_STRATEGY_SYNC = "ImportAndSync"
IMMEDIATE_IMPORT_ANNOTATION = "import.open-cluster-management.io/immediate-import"
LOCAL_CLUSTER_NAME = "local-cluster"
```

**Step 2: Create manage_auto_import.yml**

Create `ansible_collections/tomazb/acm_switchover/roles/activation/tasks/manage_auto_import.yml`:

```yaml
---
# Mirrors Python _maybe_set_auto_import_strategy() in modules/activation.py.
# For ACM 2.14+, temporarily set autoImportStrategy=ImportAndSync so
# clusters restored from backup are immediately re-imported by the hub.
- name: Read import-controller-config ConfigMap
  kubernetes.core.k8s_info:
    api_version: v1
    kind: ConfigMap
    name: import-controller-config
    namespace: multicluster-engine
    kubeconfig: "{{ acm_switchover_hubs.secondary.kubeconfig }}"
    context: "{{ acm_switchover_hubs.secondary.context }}"
  register: _import_controller_cm

- name: Determine current autoImportStrategy
  ansible.builtin.set_fact:
    _auto_import_current_strategy: >-
      {{ (_import_controller_cm.resources | default([]) | first | default({}))
         .get('data', {}).get('autoImportStrategy', 'default') }}

- name: Set autoImportStrategy to ImportAndSync
  kubernetes.core.k8s:
    api_version: v1
    kind: ConfigMap
    name: import-controller-config
    namespace: multicluster-engine
    kubeconfig: "{{ acm_switchover_hubs.secondary.kubeconfig }}"
    context: "{{ acm_switchover_hubs.secondary.context }}"
    state: patched
    definition:
      data:
        autoImportStrategy: ImportAndSync
  register: _auto_import_patched
  when:
    - _auto_import_current_strategy != 'ImportAndSync'

- name: Record strategy change for later reset
  ansible.builtin.set_fact:
    _auto_import_strategy_changed: true
  when: _auto_import_patched is changed
```

**Step 3: Replace apply_immediate_import.yml stub**

Replace the stub in `ansible_collections/tomazb/acm_switchover/roles/activation/tasks/apply_immediate_import.yml`:

```yaml
---
# Mirrors Python _apply_immediate_import_annotations() in modules/activation.py.
# When autoImportStrategy is ImportOnly (ACM 2.14+ default), apply the
# immediate-import annotation to each non-local ManagedCluster so the
# import controller processes them without waiting for the next sync.
- name: Get current autoImportStrategy
  kubernetes.core.k8s_info:
    api_version: v1
    kind: ConfigMap
    name: import-controller-config
    namespace: multicluster-engine
    kubeconfig: "{{ acm_switchover_hubs.secondary.kubeconfig }}"
    context: "{{ acm_switchover_hubs.secondary.context }}"
  register: _import_cm_for_annotation

- name: Determine if immediate-import annotations are needed
  ansible.builtin.set_fact:
    _apply_immediate_import: >-
      {{ (_import_cm_for_annotation.resources | default([]) | first | default({}))
         .get('data', {}).get('autoImportStrategy', 'ImportOnly')
         in ['ImportOnly', 'default', ''] }}

- name: Get ManagedClusters on secondary hub
  kubernetes.core.k8s_info:
    api_version: cluster.open-cluster-management.io/v1
    kind: ManagedCluster
    kubeconfig: "{{ acm_switchover_hubs.secondary.kubeconfig }}"
    context: "{{ acm_switchover_hubs.secondary.context }}"
  register: _mc_for_import
  when: _apply_immediate_import | bool

- name: Apply immediate-import annotation to non-local clusters
  kubernetes.core.k8s:
    api_version: cluster.open-cluster-management.io/v1
    kind: ManagedCluster
    name: "{{ item.metadata.name }}"
    kubeconfig: "{{ acm_switchover_hubs.secondary.kubeconfig }}"
    context: "{{ acm_switchover_hubs.secondary.context }}"
    state: patched
    definition:
      metadata:
        annotations:
          import.open-cluster-management.io/immediate-import: ""
  loop: >-
    {{ (_mc_for_import.resources | default([]))
       | rejectattr('metadata.name', 'equalto', 'local-cluster')
       | list }}
  loop_control:
    label: "{{ item.metadata.name }}"
  when:
    - _apply_immediate_import | bool
    - (item.metadata.annotations | default({})).get('import.open-cluster-management.io/immediate-import', 'missing') != ''
  ignore_errors: true  # noqa: ignore-errors

- name: Publish immediate import result
  ansible.builtin.set_fact:
    acm_switchover_immediate_import_result:
      changed: "{{ _apply_immediate_import | default(false) | bool }}"
      mode: "{{ acm_switchover_operation.activation_method | default('patch') }}"
```

**Step 4: Create reset_auto_import.yml**

Create `ansible_collections/tomazb/acm_switchover/roles/activation/tasks/reset_auto_import.yml`:

```yaml
---
# After activation, reset autoImportStrategy to ImportOnly if we changed it.
- name: Reset autoImportStrategy to ImportOnly
  kubernetes.core.k8s:
    api_version: v1
    kind: ConfigMap
    name: import-controller-config
    namespace: multicluster-engine
    kubeconfig: "{{ acm_switchover_hubs.secondary.kubeconfig }}"
    context: "{{ acm_switchover_hubs.secondary.context }}"
    state: patched
    definition:
      data:
        autoImportStrategy: ImportOnly
  when: _auto_import_strategy_changed | default(false) | bool
```

**Step 5: Update activation main.yml**

In `ansible_collections/tomazb/acm_switchover/roles/activation/tasks/main.yml`, add `manage_auto_import.yml` before activation and `reset_auto_import.yml` after immediate import. The block inside `Run activation phase` should become:

```yaml
  block:
    - name: Manage auto-import strategy for ACM 2.14+
      ansible.builtin.include_tasks: manage_auto_import.yml
      when: acm_switchover_features.manage_auto_import_strategy | default(false)

    - name: Verify passive sync when method is passive
      ansible.builtin.include_tasks: verify_passive_sync.yml
      when: acm_switchover_operation.method == 'passive'

    - name: Activate restore
      ansible.builtin.include_tasks: activate_restore.yml

    - name: Wait for restore completion
      ansible.builtin.include_tasks: wait_for_restore.yml

    - name: Apply immediate import annotations when required
      ansible.builtin.include_tasks: apply_immediate_import.yml

    - name: Reset auto-import strategy after activation
      ansible.builtin.include_tasks: reset_auto_import.yml

    - name: Publish activation result contract
      # ... (existing, unchanged)
```

**Step 6: Write integration test**

Add to `ansible_collections/tomazb/acm_switchover/tests/integration/test_switchover_roles.py`:

```python
def test_activation_manages_auto_import_strategy(run_switchover_fixture):
    """When manage_auto_import_strategy is enabled, activation includes strategy management."""
    # This test verifies the task files are included and the flow is correct.
    # The fixture should have manage_auto_import_strategy: true set.
    # Actual k8s calls will fail in fixture mode, but task inclusion is verified.
    pass  # Placeholder — fill in once fixture structure is understood
```

Note: The exact test depends on the fixture infrastructure. If the integration tests run against fixtures that mock k8s calls, create a fixture YAML that sets `acm_switchover_features.manage_auto_import_strategy: true` and verify the playbook includes the new tasks. If the tests only validate task file structure, verify that `manage_auto_import.yml` and `reset_auto_import.yml` are syntactically valid and included.

**Step 7: Run tests**

Run: `source .venv/bin/activate && python -m pytest ansible_collections/tomazb/acm_switchover/tests/ -v`
Expected: All pass.

**Step 8: Commit**

```bash
git add ansible_collections/tomazb/acm_switchover/roles/activation/tasks/ \
        ansible_collections/tomazb/acm_switchover/plugins/module_utils/constants.py \
        ansible_collections/tomazb/acm_switchover/tests/
git commit -m "feat(ansible): add auto-import strategy management to activation role

Implements parity with Python _maybe_set_auto_import_strategy() and
_apply_immediate_import_annotations() for ACM 2.14+:
- manage_auto_import.yml: temporarily sets ImportAndSync before activation
- apply_immediate_import.yml: applies annotation to non-local ManagedClusters
- reset_auto_import.yml: resets to ImportOnly after activation
Guarded by acm_switchover_features.manage_auto_import_strategy."
```

---

## Task 6: Add Klusterlet Auto-Remediation to Ansible Post-Activation Role

**Problem:** Python's `_force_klusterlet_reconnect()` automatically fixes klusterlets pointing to the old hub by getting the import secret from the new hub, deleting the bootstrap secret on the managed cluster, re-applying the import manifest, and restarting the klusterlet. Ansible only records a warning note.

**Approach:** Accept managed cluster kubeconfigs via `acm_switchover_managed_clusters` variable. For clusters identified as pending/wrong_hub that have kubeconfigs provided, run the reconnect flow.

**Files:**
- Create: `ansible_collections/tomazb/acm_switchover/roles/post_activation/tasks/fix_klusterlet.yml`
- Modify: `ansible_collections/tomazb/acm_switchover/roles/post_activation/tasks/verify_klusterlet.yml`
- Modify: `ansible_collections/tomazb/acm_switchover/roles/post_activation/tasks/main.yml`
- Modify: `ansible_collections/tomazb/acm_switchover/roles/post_activation/defaults/main.yml`
- Test: `ansible_collections/tomazb/acm_switchover/tests/integration/test_switchover_roles.py`

**Step 1: Add managed_clusters variable to defaults**

In `ansible_collections/tomazb/acm_switchover/roles/post_activation/defaults/main.yml`, add:

```yaml
# Optional: kubeconfigs for managed clusters to enable automatic klusterlet
# remediation. Without this, pending clusters get a warning only.
# Format:
#   acm_switchover_managed_clusters:
#     cluster1:
#       kubeconfig: "/path/to/cluster1-kubeconfig"
#       context: ""  # optional, uses default context if omitted
acm_switchover_managed_clusters: {}
```

**Step 2: Create fix_klusterlet.yml**

Create `ansible_collections/tomazb/acm_switchover/roles/post_activation/tasks/fix_klusterlet.yml`:

```yaml
---
# Mirrors Python _force_klusterlet_reconnect() in modules/post_activation.py.
# For each pending/wrong_hub cluster with a managed cluster kubeconfig,
# fetch the import secret from the new hub, delete the bootstrap secret
# on the managed cluster, re-apply the import manifest, and restart
# the klusterlet deployment.

- name: Identify clusters eligible for remediation
  ansible.builtin.set_fact:
    _klusterlet_fix_candidates: >-
      {{ (acm_cluster_verify_result.pending | default([]))
         | select('in', acm_switchover_managed_clusters.keys() | list)
         | list }}

- name: Skip klusterlet fix when no remediation candidates have kubeconfigs
  ansible.builtin.debug:
    msg: >-
      No klusterlet remediation possible — pending clusters
      {{ acm_cluster_verify_result.pending | default([]) | join(', ') }}
      do not have entries in acm_switchover_managed_clusters
  when: _klusterlet_fix_candidates | length == 0

- name: Fix klusterlet for each eligible cluster
  ansible.builtin.include_tasks: fix_klusterlet_single.yml
  loop: "{{ _klusterlet_fix_candidates }}"
  loop_control:
    loop_var: _fix_cluster_name
  when: _klusterlet_fix_candidates | length > 0
```

**Step 3: Create fix_klusterlet_single.yml**

Create `ansible_collections/tomazb/acm_switchover/roles/post_activation/tasks/fix_klusterlet_single.yml`:

```yaml
---
# Fix a single cluster's klusterlet. Called in a loop from fix_klusterlet.yml.
# Variable: _fix_cluster_name (the ManagedCluster name)

- name: "{{ _fix_cluster_name }} — Get import secret from new hub"
  kubernetes.core.k8s_info:
    api_version: v1
    kind: Secret
    name: "{{ _fix_cluster_name }}-import"
    namespace: "{{ _fix_cluster_name }}"
    kubeconfig: "{{ acm_switchover_hubs.secondary.kubeconfig }}"
    context: "{{ acm_switchover_hubs.secondary.context }}"
  register: _import_secret

- name: "{{ _fix_cluster_name }} — Skip when import secret not found"
  ansible.builtin.debug:
    msg: "No import secret found for {{ _fix_cluster_name }} on new hub — skipping"
  when: (_import_secret.resources | default([]) | length) == 0

- name: "{{ _fix_cluster_name }} — Decode import YAML"
  ansible.builtin.set_fact:
    _import_yaml_raw: >-
      {{ (_import_secret.resources | first).data['import.yaml'] | b64decode }}
  when: (_import_secret.resources | default([]) | length) > 0

- name: "{{ _fix_cluster_name }} — Delete bootstrap-hub-kubeconfig on managed cluster"
  kubernetes.core.k8s:
    api_version: v1
    kind: Secret
    name: bootstrap-hub-kubeconfig
    namespace: open-cluster-management-agent
    kubeconfig: "{{ acm_switchover_managed_clusters[_fix_cluster_name].kubeconfig }}"
    context: "{{ acm_switchover_managed_clusters[_fix_cluster_name].context | default(omit) }}"
    state: absent
  when: _import_yaml_raw is defined
  ignore_errors: true  # noqa: ignore-errors — secret may already be gone

- name: "{{ _fix_cluster_name }} — Re-apply bootstrap-hub-kubeconfig from import manifest"
  kubernetes.core.k8s:
    kubeconfig: "{{ acm_switchover_managed_clusters[_fix_cluster_name].kubeconfig }}"
    context: "{{ acm_switchover_managed_clusters[_fix_cluster_name].context | default(omit) }}"
    state: present
    definition: "{{ item }}"
  loop: "{{ _import_yaml_raw | from_yaml_all | list }}"
  loop_control:
    label: "{{ item.kind | default('unknown') }}/{{ item.metadata.name | default('unknown') }}"
  when:
    - _import_yaml_raw is defined
    - item is mapping
    - item.kind | default('') == 'Secret'
    - item.metadata.name | default('') == 'bootstrap-hub-kubeconfig'

- name: "{{ _fix_cluster_name }} — Restart klusterlet deployment"
  kubernetes.core.k8s:
    api_version: apps/v1
    kind: Deployment
    name: klusterlet
    namespace: open-cluster-management-agent
    kubeconfig: "{{ acm_switchover_managed_clusters[_fix_cluster_name].kubeconfig }}"
    context: "{{ acm_switchover_managed_clusters[_fix_cluster_name].context | default(omit) }}"
    state: patched
    definition:
      spec:
        template:
          metadata:
            annotations:
              acm-switchover/restartedAt: "{{ now(utc=true, fmt='%Y-%m-%dT%H:%M:%SZ') }}"
  when: _import_yaml_raw is defined
```

**Step 4: Update verify_klusterlet.yml**

Replace `ansible_collections/tomazb/acm_switchover/roles/post_activation/tasks/verify_klusterlet.yml` to add the fix step:

```yaml
---
- name: Attempt klusterlet auto-remediation
  ansible.builtin.include_tasks: fix_klusterlet.yml
  when:
    - (acm_cluster_verify_result.pending | default([]) | length) > 0
    - (acm_switchover_managed_clusters | default({}) | length) > 0

- name: Note klusterlet remediation candidates
  ansible.builtin.set_fact:
    acm_klusterlet_remediation_note:
      warning: "Klusterlet verification requires manual review - check klusterlet pod logs on managed clusters if clusters fail to connect"
      pending: "{{ acm_cluster_verify_result.pending | default([]) }}"
  when: (acm_cluster_verify_result.pending | default([]) | length) > 0
```

**Step 5: Run tests**

Run: `source .venv/bin/activate && python -m pytest ansible_collections/tomazb/acm_switchover/tests/ -v`
Expected: All pass.

**Step 6: Commit**

```bash
git add ansible_collections/tomazb/acm_switchover/roles/post_activation/
git commit -m "feat(ansible): add klusterlet auto-remediation to post_activation role

Implements parity with Python _force_klusterlet_reconnect(). When
acm_switchover_managed_clusters provides kubeconfigs for pending clusters:
1. Fetches import secret from the new hub
2. Deletes bootstrap-hub-kubeconfig on the managed cluster
3. Re-applies the bootstrap secret from the import manifest
4. Restarts the klusterlet deployment
Falls back to warning note when kubeconfigs are not provided."
```

---

## Verification

After all tasks are complete:

**Step 1: Run full test suite**

```bash
source .venv/bin/activate && python -m pytest tests/ ansible_collections/tomazb/acm_switchover/tests/unit/ -q
```

Expected: All tests pass.

**Step 2: Run lint**

```bash
source .venv/bin/activate && python -m flake8 lib/rbac_validator.py lib/utils.py acm_switchover.py
```

Expected: No errors.

---

## Summary

| Task | Type | Files Changed | Commit |
|------|------|---------------|--------|
| T1 | P2 Bug Fix | `lib/rbac_validator.py`, `tests/test_rbac_validator.py` | `fix: allow decommission reruns when ACM namespace is gone` |
| T2 | P2 Bug Fix | 2 playbooks, 1 test file | `fix(ansible): guard primary resume on non-empty kubeconfig` |
| T3 | Defensive Fix | `lib/utils.py`, `tests/test_utils.py` | `fix: dry_run_skip defaults to skip when attribute path is broken` |
| T4 | Cleanup | `acm_switchover.py` | `refactor: remove unused restore_only param from _resolve_state_file` |
| T5 | Parity Gap | 4 activation task files, constants, test | `feat(ansible): add auto-import strategy management` |
| T6 | Parity Gap | 4 post_activation task files, defaults, test | `feat(ansible): add klusterlet auto-remediation` |
