# ACM Switchover Ansible/Python Parity — Work To Do

**Branch baseline:** `ansible` branch of `tomazb/rh-acm-switchover`  
**Scope:** Align the Ansible Collection with the Python CLI safety and operational semantics.  
**Review basis:** Static code review of repository files. This plan has not been verified against a live ACM/OpenShift cluster.  
**Confidence:** 87% for the original static review; refreshed status below is based on repository inspection only.

## Current review status

**Last reviewed:** 2026-05-05
**Branch inspected:** `ansible`
**Working tree note:** `AGENTS.md` has unrelated local modifications and was not changed for this review.
**Verification scope:** Static inspection only. No live ACM/OpenShift cluster verification was performed.

The backlog is still valid overall, but several items are partially implemented and should be narrowed before execution:

- PR-2: `acm_restore_info` already returns `restore_phase` and `restore_ready`; activation still uses preflight Restore facts and still lacks an activation-time readiness assertion.
- PR-3: `post_activation` has phase-local execute-mode ManagedCluster discovery; `primary_prep` still depends on preflight MCH and BackupSchedule facts.
- PR-4: Argo CD resume is run-id aware, and resume-on-failure now resets the checkpoint from `primary_prep` so downstream phases are retried. The optional `resume_force` override was intentionally omitted to keep resume scoped to Applications paused by the current run ID.
- PR-8: runbook edits are protected by `AGENTS.md` and require explicit operator approval plus `.claude/skills` synchronization before any change.

Do not treat partially implemented items as complete until the acceptance criteria in the relevant PR section are satisfied. In particular, existing tests for Argo CD run IDs, Restore readiness helpers, or post-activation discovery do not close the checkpoint reset, activation live-read, or primary-prep resume gaps.

---

## 0. Goal

The target outcome is:

> The Ansible Collection should match the Python CLI’s safety model for checkpointing, validation, activation, resume behavior, artifact handling, and major operational gates.

The main issues to resolve are:

1. Checkpoints are not sufficiently bound to the hub pair or operation identity.
2. `validate` mode can persist checkpoint progress in Ansible, unlike Python validate-only behavior.
3. Ansible activation can use stale Restore facts gathered during preflight.
4. Ansible activation does not re-check passive Restore readiness immediately before mutation.
5. Later phases depend on transient Ansible facts from earlier phases, which breaks resume safety.
6. ArgoCD resume-on-failure resets too little checkpoint state.
7. Ansible checkpoint writes are direct and not atomic/locked.
8. Decommission summary writing bypasses the common artifact safe-path helper.
9. Python and Ansible validation rules are not fully aligned.
10. Klusterlet remediation is sequential in Ansible while Python uses bounded concurrency.

---

## 1. Work organization

Create a dedicated implementation branch:

```bash
git checkout ansible
git pull
git checkout -b fix/ansible-python-parity-state-activation
```

Recommended PR split and current status:

| PR | Theme | Priority | Risk | Merge order | Current status |
|---|---:|---:|---:|---:|---|
| PR-1 | Checkpoint/state safety | P0 | Critical | 1 | Pending |
| PR-2 | Activation live-read + passive readiness | P0 | Critical | 2 | Partially implemented; live-read and activation assertion still pending |
| PR-3 | Phase self-sufficiency / fact freshness | P1 | High | 3 | Partially pending; primary_prep remains the main gap |
| PR-4 | ArgoCD resume-on-failure checkpoint semantics | P1 | High | 4 | Implemented; `reset_from primary_prep` semantics verified |
| PR-5 | Decommission/report path safety | P2 | Medium | 5 | Pending |
| PR-6 | Python/Ansible validation parity | P2 | Medium | 6 | Pending |
| PR-7 | Klusterlet scalability | P3 | Medium | 7 | Pending |
| PR-8 | Docs, migration map, runbook updates | P2 | Medium | 8 | Pending; protected runbook gate applies |

Do **not** combine all of these into a single PR. The checkpoint and activation changes affect operator safety and should be reviewed independently.

---

# PR-1 — Rework Ansible checkpoint/state safety

## 1.1 Problem statement

The Ansible checkpoint model records phase completion, but it is not strongly bound to the operation identity: primary hub, secondary hub, method, restore-only mode, activation method, or old-hub action.

The current checkpoint helper stores a phase, completed phases, operational data, errors, report references, and timestamps.

The default Ansible checkpoint path is static:

```yaml
acm_switchover_execution:
  checkpoint:
    enabled: false
    backend: file
    path: .state/switchover.json
```

The Python CLI has a richer state manager with context checks, stale completed-state handling, locking, and atomic write semantics.

**Runtime risk [Inference]:** A checkpoint from one hub pair can be reused for another hub pair and cause Ansible to skip phases incorrectly.

**Current status as of 2026-05-05:** Pending. The collection checkpoint schema is still `1.0`, and the action plugin still treats only `execution.mode=dry_run` as non-mutating. It does not yet bind checkpoints to operation identity, reject unsafe schema `1.0` resumes, support `reset_from`, or write atomically.

## 1.2 Files to modify

Primary files:

```text
ansible_collections/tomazb/acm_switchover/plugins/module_utils/checkpoint.py
ansible_collections/tomazb/acm_switchover/plugins/action/checkpoint_phase.py
ansible_collections/tomazb/acm_switchover/playbooks/switchover.yml
ansible_collections/tomazb/acm_switchover/roles/*/tasks/main.yml
ansible_collections/tomazb/acm_switchover/roles/*/defaults/main.yml
```

Likely test files to add or modify:

```text
tests/unit/ansible/test_checkpoint_module_utils.py
tests/unit/ansible/test_checkpoint_phase_action.py
ansible_collections/tomazb/acm_switchover/tests/unit/plugins/module_utils/test_checkpoint.py
ansible_collections/tomazb/acm_switchover/tests/unit/plugins/action/test_checkpoint_phase.py
```

Use the actual collection test layout if it already exists.

## 1.3 Add checkpoint operation identity

Add this object to the checkpoint schema:

```json
{
  "schema_version": "2.0",
  "operation_identity": {
    "primary_context": "...",
    "secondary_context": "...",
    "primary_kubeconfig": "...",
    "secondary_kubeconfig": "...",
    "method": "passive",
    "activation_method": "patch",
    "restore_only": false,
    "old_hub_action": "secondary",
    "collection_version": "..."
  }
}
```

Do **not** store raw kubeconfig file content. Store path strings or sanitized fingerprints only.

Recommended implementation:

```python
def build_operation_identity(
    hubs: dict,
    operation: dict,
    collection_version: str | None = None,
) -> dict:
    primary = hubs.get("primary", {})
    secondary = hubs.get("secondary", {})

    return {
        "primary_context": primary.get("context", ""),
        "secondary_context": secondary.get("context", ""),
        "primary_kubeconfig": primary.get("kubeconfig", ""),
        "secondary_kubeconfig": secondary.get("kubeconfig", ""),
        "method": operation.get("method", "passive"),
        "activation_method": operation.get("activation_method", "patch"),
        "restore_only": bool(operation.get("restore_only", False)),
        "old_hub_action": operation.get("old_hub_action", "secondary"),
        "collection_version": collection_version or "",
    }
```

Add comparison helper:

```python
class CheckpointIdentityMismatch(Exception):
    """Raised when a checkpoint belongs to a different operation."""


def validate_operation_identity(
    checkpoint: dict,
    expected_identity: dict,
    *,
    allow_missing: bool = False,
) -> None:
    existing = checkpoint.get("operation_identity")

    if not existing:
        if allow_missing:
            return
        raise CheckpointIdentityMismatch(
            "Checkpoint does not contain operation identity. "
            "Reset the checkpoint or migrate it before resuming."
        )

    mismatches = {
        key: {
            "checkpoint": existing.get(key),
            "current": expected_identity.get(key),
        }
        for key in expected_identity
        if existing.get(key) != expected_identity.get(key)
    }

    if mismatches:
        raise CheckpointIdentityMismatch(
            f"Checkpoint operation identity does not match current invocation: {mismatches}"
        )
```

## 1.4 Add schema migration behavior

Support existing `schema_version: "1.0"` checkpoints safely.

Preferred production-safe behavior:

```text
If schema_version == 1.0 and checkpoint already has completed phases:
  fail with a clear message requiring explicit reset.
```

Example failure message:

```text
Existing checkpoint uses schema_version 1.0 and is missing operation identity.
Refusing to resume because hub-pair identity cannot be verified.
Set acm_switchover_execution.checkpoint.reset=true to start a new checkpoint.
```

Alternative migration behavior:

```text
If schema_version == 1.0 and completed_phases is empty:
  upgrade in place to 2.0 using the current operation identity.
If schema_version == 1.0 and completed_phases is non-empty:
  fail unless checkpoint.reset=true.
```

Recommended helper:

```python
def migrate_checkpoint_if_safe(checkpoint: dict, expected_identity: dict) -> tuple[dict, bool]:
    schema_version = checkpoint.get("schema_version", "1.0")

    if schema_version == "2.0":
        return checkpoint, False

    if schema_version != "1.0":
        raise ValueError(f"Unsupported checkpoint schema version: {schema_version}")

    completed = checkpoint.get("completed_phases", [])
    if completed:
        raise CheckpointIdentityMismatch(
            "Cannot safely migrate checkpoint schema 1.0 with completed phases."
        )

    checkpoint["schema_version"] = "2.0"
    checkpoint["operation_identity"] = expected_identity
    return checkpoint, True
```

## 1.5 Treat `validate` mode as non-mutating

The Ansible switchover playbook has a validate-only path that ends after preflight. The checkpoint action should treat `validate` like `dry_run`.

Change checkpoint persistence logic:

```python
def is_checkpoint_write_allowed(execution_mode: str, check_mode: bool) -> bool:
    if check_mode:
        return False
    if execution_mode in ("dry_run", "validate"):
        return False
    return True
```

Apply this to all mutating checkpoint actions:

```text
status=enter
status=pass
status=fail
status=reset
status=reset_from
```

For `validate`, return a result that says what would have happened:

```json
{
  "changed": false,
  "would_change": true,
  "checkpoint_write_skipped": true,
  "skip_reason": "execution mode validate is non-mutating"
}
```

## 1.6 Add atomic checkpoint writes

Port the Python state manager’s atomic-write pattern into Ansible module utils.

Add imports:

```python
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
```

Implement lock helper:

```python
@contextmanager
def checkpoint_lock(path: Path):
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    lock_file = open(lock_path, "a+", encoding="utf-8")
    try:
        try:
            import fcntl
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        except ImportError:
            pass
        yield
    finally:
        try:
            import fcntl
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        lock_file.close()
```

Add atomic write:

```python
def write_checkpoint_atomic(path: str, checkpoint: dict) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)

    with checkpoint_lock(destination):
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=str(destination.parent),
            text=True,
        )

        try:
            with os.fdopen(fd, "w", encoding="utf-8") as tmp:
                json.dump(checkpoint, tmp, indent=2, sort_keys=True)
                tmp.write("\n")
                tmp.flush()
                os.fsync(tmp.fileno())

            os.replace(tmp_name, destination)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
```

Add safe read:

```python
def read_checkpoint(path: str) -> dict | None:
    checkpoint_path = Path(path)
    if not checkpoint_path.exists():
        return None

    try:
        return json.loads(checkpoint_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        corrupt_path = checkpoint_path.with_suffix(
            checkpoint_path.suffix + ".corrupt"
        )
        checkpoint_path.replace(corrupt_path)
        raise ValueError(
            f"Checkpoint file is corrupt and was moved to {corrupt_path}: {exc}"
        ) from exc
```

## 1.7 Add reset-from-phase behavior

Current checkpoint semantics should support resetting a phase and all downstream phases.

Add:

```python
PHASE_ORDER = [
    "preflight",
    "primary_prep",
    "activation",
    "post_activation",
    "finalization",
]


def phases_from(phase: str) -> list[str]:
    if phase not in PHASE_ORDER:
        raise ValueError(f"Unknown phase: {phase}")
    index = PHASE_ORDER.index(phase)
    return PHASE_ORDER[index:]


def reset_from_phase(checkpoint: dict, phase: str) -> dict:
    remove = set(phases_from(phase))
    checkpoint["completed_phases"] = [
        item for item in checkpoint.get("completed_phases", [])
        if item not in remove
    ]
    checkpoint["phase"] = phase
    return checkpoint
```

Expose it through the action plugin:

```yaml
- name: Reset checkpoint from primary_prep after ArgoCD resume
  tomazb.acm_switchover.checkpoint_phase:
    phase: primary_prep
    status: reset_from
```

## 1.8 PR-1 acceptance criteria

PR-1 is complete when:

- [ ] A checkpoint created for hub pair A fails or resets when used with hub pair B.
- [ ] `execution.mode=validate` does not write or mutate the checkpoint file.
- [ ] `execution.mode=dry_run` does not write or mutate the checkpoint file.
- [ ] Interrupted writes do not leave partial JSON in the final checkpoint path.
- [ ] Corrupt checkpoint JSON is detected and moved aside.
- [ ] `reset_from primary_prep` removes `primary_prep`, `activation`, `post_activation`, and `finalization` from `completed_phases`.
- [ ] Existing schema `1.0` checkpoints with completed phases are rejected unless reset is explicit.
- [ ] Unit tests cover all of the above.

---

# PR-2 — Make activation use live Restore data and re-check readiness

## 2.1 Problem statement

Preflight reads secondary Restore resources. Activation discovery skips a live Restore read when preflight facts already exist. Activation then verifies passive sync mainly by checking that a Restore exists and looks like a passive Restore.

Python activation re-verifies passive sync readiness at activation time.

**Runtime risk [Inference]:** Ansible activation can use a stale Restore snapshot gathered during preflight.

**Current status as of 2026-05-05:** Partial. The Restore info module already exposes `restore_phase` and `restore_ready`, but activation still reads into `acm_secondary_restores_info` only when preflight facts are absent and `verify_passive_sync.yml` still falls back to `acm_secondary_restore_info`. Activation still does not assert `restore_ready` before mutation.

## 2.2 Files to modify

```text
ansible_collections/tomazb/acm_switchover/roles/activation/tasks/discover_resources.yml
ansible_collections/tomazb/acm_switchover/roles/activation/tasks/verify_passive_sync.yml
ansible_collections/tomazb/acm_switchover/roles/activation/tasks/activate_restore.yml
ansible_collections/tomazb/acm_switchover/plugins/modules/acm_restore_info.py
ansible_collections/tomazb/acm_switchover/plugins/module_utils/restore.py
```

The exact module-utils path may differ if Restore logic currently lives directly in `acm_restore_info.py`.

## 2.3 Add activation-specific live Restore fact

Replace activation discovery with a dedicated live-read variable:

```yaml
- name: Read secondary Restore resources for activation
  kubernetes.core.k8s_info:
    api_version: cluster.open-cluster-management.io/v1beta1
    kind: Restore
    namespace: open-cluster-management-backup
    kubeconfig: "{{ acm_switchover_hubs.secondary.kubeconfig }}"
    context: "{{ acm_switchover_hubs.secondary.context }}"
  register: acm_activation_restores_info
```

Do not use this production cache guard:

```yaml
when:
  - acm_secondary_restores_info is not defined
  - acm_secondary_restore_info is not defined
```

For test pre-seeding, introduce an explicit test-only override:

```yaml
acm_switchover_test_overrides:
  activation_restores_info: null
```

Then:

```yaml
- name: Use test override for activation Restore resources
  ansible.builtin.set_fact:
    acm_activation_restores_info: "{{ acm_switchover_test_overrides.activation_restores_info }}"
  when:
    - acm_switchover_test_overrides is defined
    - acm_switchover_test_overrides.activation_restores_info is defined
```

This removes ambiguity between production caching and test fixtures.

## 2.4 Re-run Restore selection against live data

Update `verify_passive_sync.yml`:

```yaml
- name: Select passive sync restore from activation-time data
  tomazb.acm_switchover.acm_restore_info:
    activation_method: "{{ acm_switchover_operation.activation_method | default('patch') }}"
    restores: "{{ acm_activation_restores_info.resources | default([]) }}"
  register: acm_passive_restore_selection
```

Avoid falling back to preflight variables:

```yaml
# Remove this style:
restores: "{{ acm_secondary_restores_info.resources | default(acm_secondary_restore_info.resources | default([])) }}"
```

## 2.5 Add explicit readiness assertion

`acm_restore_info` already returns `restore_phase` and `restore_ready`. Add `restore_ready_reason` only if the implementation needs a human-readable explanation in assertion failures.

Expected output shape after this PR:

```json
{
  "restore": {},
  "restore_phase": "FinishedWithErrors",
  "restore_ready": true,
  "restore_ready_reason": "FinishedWithErrors contains only benign Velero backup collision messages"
}
```

Then update the activation assertion:

```yaml
- name: Require passive sync Restore to be activation-ready
  ansible.builtin.assert:
    that:
      - acm_passive_restore_selection.restore is not none
      - acm_passive_restore_selection.restore_ready | default(false) | bool
    fail_msg: >-
      Passive switchover requires an activation-ready secondary passive Restore.
      Selected restore={{ acm_passive_restore_selection.restore.metadata.name | default('none') }},
      phase={{ acm_passive_restore_selection.restore_phase | default('unknown') }},
      reason={{ acm_passive_restore_selection.restore_ready_reason | default('not reported') }}
```

Keep the identity check too:

```yaml
- name: Require passive sync restore identity
  ansible.builtin.assert:
    that:
      - >-
        (acm_passive_restore_selection.sync_enabled_count | default(0) | int) > 0
        or (acm_passive_restore_selection.reason | default('')) == 'conventional_name_fallback'
```

## 2.6 Guard activation mutation with resourceVersion

Optional but strongly recommended.

When patching an existing Restore, include a resourceVersion precondition when possible:

```yaml
definition:
  metadata:
    resourceVersion: "{{ acm_passive_restore_selection.restore.metadata.resourceVersion }}"
  spec:
    syncRestoreWithNewBackups: false
```

If `kubernetes.core.k8s` does not behave well with resourceVersion on patch, add a custom module for Restore activation so the logic can use Kubernetes API semantics directly.

## 2.7 PR-2 acceptance criteria

PR-2 is complete when:

- [ ] Activation always reads Restore resources live unless an explicit test override is used.
- [ ] Activation fails before mutation when the passive Restore is not activation-ready.
- [ ] Activation no longer consumes `acm_secondary_restore_info` from preflight.
- [x] Restore analysis exposes `restore_phase` and `restore_ready`.
- [ ] Restore analysis exposes `restore_ready_reason` if assertion output needs it.
- [ ] Unit tests cover:
  - [ ] passive Restore ready at preflight but failed at activation;
  - [ ] passive Restore missing at activation;
  - [ ] passive Restore present but `syncRestoreWithNewBackups=false`;
  - [ ] conventional-name fallback;
  - [ ] benign `FinishedWithErrors`;
  - [ ] hard failure phase.
- [ ] An integration-style mocked Ansible run proves preflight facts cannot bypass activation live-read.

---

# PR-3 — Make each phase self-contained and resume-safe

## 3.1 Problem statement

Primary prep currently depends on facts gathered by preflight. For example, `pause_backups.yml` uses `acm_primary_mch_info` and `acm_primary_backup_schedules_info`, while primary prep’s own discovery only reads the Thanos compactor StatefulSet.

**Runtime risk [Inference]:** If checkpointing skips preflight on resume, primary prep may lack required facts or use stale ones.

**Current status as of 2026-05-05:** Partially pending. `post_activation` already performs execute-mode local ManagedCluster discovery. `primary_prep` still only discovers the Thanos compactor and still uses preflight `acm_primary_mch_info` and `acm_primary_backup_schedules_info` in `pause_backups.yml`.

## 3.2 Files to modify

```text
ansible_collections/tomazb/acm_switchover/roles/primary_prep/tasks/discover_resources.yml
ansible_collections/tomazb/acm_switchover/roles/primary_prep/tasks/pause_backups.yml
ansible_collections/tomazb/acm_switchover/roles/activation/tasks/discover_resources.yml
ansible_collections/tomazb/acm_switchover/roles/post_activation/tasks/discover_resources.yml
ansible_collections/tomazb/acm_switchover/roles/finalization/tasks/main.yml
```

## 3.3 Define phase data ownership

Adopt this rule:

```text
A phase may consume durable checkpoint metadata from previous phases.
A phase must not require transient Ansible facts from previous phases.
```

Phase-owned reads:

| Phase | Must read live inside the phase |
|---|---|
| `preflight` | all validation inputs |
| `primary_prep` | primary MCH, BackupSchedule, compactor/observability resources, ArgoCD Applications if pausing |
| `activation` | secondary Restore resources, secondary MCH |
| `post_activation` | secondary ManagedClusters, optional managed-cluster kubeconfig map |
| `finalization` | secondary Restore resources, secondary BackupSchedule, ArgoCD resume targets if needed |

## 3.4 Add primary prep discovery

Update:

```text
roles/primary_prep/tasks/discover_resources.yml
```

Add:

```yaml
- name: Read primary MultiClusterHub for primary prep
  kubernetes.core.k8s_info:
    api_version: operator.open-cluster-management.io/v1
    kind: MultiClusterHub
    namespace: open-cluster-management
    kubeconfig: "{{ acm_switchover_hubs.primary.kubeconfig }}"
    context: "{{ acm_switchover_hubs.primary.context }}"
  register: acm_primary_prep_mch_info

- name: Read primary BackupSchedule resources for primary prep
  kubernetes.core.k8s_info:
    api_version: cluster.open-cluster-management.io/v1beta1
    kind: BackupSchedule
    namespace: open-cluster-management-backup
    kubeconfig: "{{ acm_switchover_hubs.primary.kubeconfig }}"
    context: "{{ acm_switchover_hubs.primary.context }}"
  register: acm_primary_prep_backup_schedules_info
```

Then update `pause_backups.yml`:

```yaml
_acm_primary_version: >-
  {{
    (
      (acm_primary_prep_mch_info.resources | default([]) | first | default({}))
      .get('status', {})
      .get('currentVersion', '')
    )
  }}
```

And:

```yaml
schedules: "{{ acm_primary_prep_backup_schedules_info.resources | default([]) }}"
```

Replace all uses of these inside primary prep:

```yaml
acm_primary_mch_info
acm_primary_backup_schedules_info
```

with phase-owned names.

## 3.5 Add phase-local variable naming convention

Use this naming pattern:

```text
acm_<phase>_<resource>_info
```

Examples:

```text
acm_primary_prep_mch_info
acm_primary_prep_backup_schedules_info
acm_activation_restores_info
acm_activation_mch_info
acm_post_activation_managed_clusters_info
acm_finalization_restores_info
```

Avoid reusing preflight variable names in later phases.

## 3.6 Add phase entry validation

At the top of each role, add an assert that required input variables exist.

Example for primary prep:

```yaml
- name: Validate primary prep required inputs
  ansible.builtin.assert:
    that:
      - acm_switchover_hubs.primary.kubeconfig | length > 0
      - acm_switchover_hubs.primary.context | length > 0
      - acm_switchover_hubs.secondary.kubeconfig | length > 0
      - acm_switchover_hubs.secondary.context | length > 0
    fail_msg: "Primary prep requires primary and secondary hub kubeconfig/context values."
```

Do not validate phase-discovered facts here. Read them inside the phase.

## 3.7 PR-3 acceptance criteria

PR-3 is complete when:

- [ ] `primary_prep` can run after preflight was skipped by checkpoint.
- [ ] `activation` can run without preflight Restore facts.
- [x] `post_activation` can run without preflight ManagedCluster facts in execute mode.
- [ ] No phase role requires transient facts created by a previous phase.
- [ ] Tests simulate:
  - [ ] checkpoint has `preflight` complete;
  - [ ] no preflight facts are injected;
  - [ ] primary prep still reads its own MCH and BackupSchedules.

---

# PR-4 — Align ArgoCD resume-on-failure with checkpoint semantics

## 4.1 Problem statement

Ansible rescue resumes ArgoCD and resets only `primary_prep`. The checkpoint plugin can skip phases listed in `completed_phases`.

**Runtime risk [Inference]:** After a late failure, Ansible may resume ArgoCD, reset only `primary_prep`, and leave downstream phases marked complete. A retry may skip activation or post-activation even though ArgoCD state has been changed.

**Current status as of 2026-05-06:** Implemented. Argo CD resume requires a run ID and only resumes Applications whose `acm-switchover.argoproj.io/paused-by` annotation matches that run ID. The switchover rescue calls `checkpoint_phase` with `status: reset` and `checkpoint.reset_from: primary_prep`, which removes `primary_prep` plus downstream phases from `completed_phases` while preserving `preflight`.

## 4.2 Files to modify

```text
ansible_collections/tomazb/acm_switchover/playbooks/switchover.yml
ansible_collections/tomazb/acm_switchover/plugins/action/checkpoint_phase.py
ansible_collections/tomazb/acm_switchover/plugins/module_utils/checkpoint.py
ansible_collections/tomazb/acm_switchover/roles/argocd_manage/tasks/main.yml
ansible_collections/tomazb/acm_switchover/roles/argocd_manage/tasks/resume.yml
ansible_collections/tomazb/acm_switchover/plugins/module_utils/argocd.py
ansible_collections/tomazb/acm_switchover/plugins/modules/acm_argocd_filter.py
```

## 4.3 Use checkpoint action `reset_from`

Implemented playbook usage:

```yaml
- name: Reset checkpoint from primary prep after ArgoCD resume-on-failure
  tomazb.acm_switchover.checkpoint_phase:
    phase: primary_prep
    checkpoint: "{{ acm_switchover_execution.checkpoint | combine({'reset_from': 'primary_prep'}) }}"
    status: reset
    operational_data:
      argocd_run_id: "{{ acm_switchover_argocd.run_id | default(acm_switchover_execution.run_id | default('')) }}"
  when:
    - acm_switchover_features.argocd.manage | default(false)
    - acm_switchover_features.argocd.resume_on_failure | default(false)
    - acm_switchover_execution.checkpoint.enabled | default(false) | bool
```

## 4.4 Store ArgoCD pause metadata in checkpoint operational data

The checkpoint already stores `argocd_run_id` in several phase transitions. This PR should either keep that minimal form and document it, or expand it to the structured metadata below if resume targeting needs persisted namespaced Application names.

When ArgoCD pause runs, store:

```json
{
  "argocd": {
    "paused": true,
    "run_id": "...",
    "primary": {
      "applications": [
        {"namespace": "...", "name": "..."}
      ]
    },
    "secondary": {
      "applications": [
        {"namespace": "...", "name": "..."}
      ]
    }
  }
}
```

Do **not** store full Application manifests unless needed. Namespaced names are enough for resume targeting.

## 4.5 Make resume idempotent and run-id-aware

The Ansible ArgoCD helper defines a pause annotation:

```python
ARGOCD_PAUSED_BY_ANNOTATION = "acm-switchover.argoproj.io/paused-by"
```

Resume removes the pause only for Applications whose annotation matches the current run ID. Issue #31 intentionally does not add `resume_force`; manual recovery remains a separate operator action instead of a collection variable that can resume Applications paused by a different run.

Current required resume logic:

```yaml
when:
  - app.metadata.annotations['acm-switchover.argoproj.io/paused-by'] == acm_switchover_run_id
```

**Runtime rationale [Inference]:** This avoids one switchover run accidentally resuming Applications paused by another run.

## 4.6 PR-4 acceptance criteria

PR-4 is complete when:

- [x] Resume-on-failure resets checkpoint from `primary_prep`, not only `primary_prep`.
- [x] Completed downstream phases are removed after ArgoCD resume-on-failure.
- [x] Resume only targets Applications paused by the current run ID.
- [x] Optional `resume_force=true` override is intentionally omitted; manual override remains out of scope.
- [x] Tests cover:
  - [x] failure after activation;
  - [x] completed phases include `primary_prep`, `activation`;
  - [x] ArgoCD resume runs;
  - [x] checkpoint result keeps `preflight` but removes `primary_prep`, `activation`, `post_activation`, `finalization`.

---

# PR-5 — Use the same safe-path policy for decommission summaries

## 5.1 Problem statement

Decommission writes `summary_path` using `ansible.builtin.copy`.

Other report artifacts use `acm_report_artifact`, which delegates to `write_json_artifact()` and `validate_safe_path()`.

**Runtime risk [Inference]:** Decommission summary output can bypass the collection’s report-artifact path validation policy.

**Current status as of 2026-05-05:** Pending. `roles/decommission/tasks/main.yml` still resolves `summary_path` manually and writes it with `ansible.builtin.copy`.

## 5.2 Files to modify

```text
ansible_collections/tomazb/acm_switchover/roles/decommission/tasks/main.yml
ansible_collections/tomazb/acm_switchover/plugins/modules/acm_report_artifact.py
ansible_collections/tomazb/acm_switchover/plugins/module_utils/artifacts.py
ansible_collections/tomazb/acm_switchover/plugins/module_utils/validation.py
```

## 5.3 Replace raw copy with artifact module

Current pattern:

```yaml
- name: Write summary when requested
  ansible.builtin.copy:
    content: "{{ acm_switchover_decommission_result | to_json }}"
    dest: "{{ _acm_summary_path_abs }}"
    mode: "0644"
```

Replace with:

```yaml
- name: Write decommission summary when requested
  tomazb.acm_switchover.acm_report_artifact:
    path: "{{ _acm_summary_path_abs }}"
    report: "{{ acm_switchover_decommission_result }}"
  when: _acm_decommission_summary_path | default('') | length > 0
```

If `acm_report_artifact` cannot set file mode today, add optional module argument:

```python
"mode": {"type": "str", "default": "0644"}
```

Then pass mode through to `write_json_artifact()`.

## 5.4 PR-5 acceptance criteria

PR-5 is complete when:

- [ ] Decommission summary paths go through the same safe-path validator as other report artifacts.
- [ ] Unsafe summary paths are rejected.
- [ ] Existing valid relative and absolute report paths still work.
- [ ] Unit tests cover:
  - [ ] valid relative path;
  - [ ] valid absolute path under allowed directory;
  - [ ] traversal path;
  - [ ] empty path means no artifact written.

---

# PR-6 — Align Python and Ansible validation behavior

## 6.1 Problem statement

Python validates ArgoCD option combinations more strictly than the Ansible validation utility. The Ansible validation module validates the main features object but does not appear to enforce every Python CLI combination, especially around `argocd.resume_on_failure`.

Path validation also differs: the Python validator rejects `~` paths, while Ansible validation accepts some `~/...` style paths.

**Current status as of 2026-05-05:** Pending. Python still rejects `~` as an unsafe path character, while collection validation explicitly permits leading `~/`. Collection validation also still returns only `argocd_manage` and does not enforce `resume_on_failure` rules.

## 6.2 Files to modify

```text
lib/validation.py
ansible_collections/tomazb/acm_switchover/plugins/module_utils/validation.py
ansible_collections/tomazb/acm_switchover/plugins/modules/acm_input_validate.py
tests/test_validation.py
ansible_collections/tomazb/acm_switchover/tests/unit/plugins/module_utils/test_validation.py
```

## 6.3 Create a validation parity matrix

Create a shared test data file:

```text
tests/fixtures/validation_parity_cases.yml
```

Example cases:

```yaml
- name: valid passive patch
  input:
    operation:
      restore_only: false
      method: passive
      activation_method: patch
      old_hub_action: secondary
    execution:
      mode: execute
    features:
      argocd:
        manage: false
  expected:
    passed: true

- name: resume_on_failure requires argocd manage
  input:
    operation:
      restore_only: false
      method: passive
    execution:
      mode: execute
    features:
      argocd:
        manage: false
        resume_on_failure: true
  expected:
    passed: false
    contains: "resume_on_failure requires argocd.manage"

- name: resume_on_failure invalid in validate mode
  input:
    operation:
      restore_only: false
      method: passive
    execution:
      mode: validate
    features:
      argocd:
        manage: true
        resume_on_failure: true
  expected:
    passed: false
    contains: "resume_on_failure is not valid in validate mode"
```

Run this fixture through both Python and Ansible validation code.

## 6.4 Add Ansible validation rules

In the Ansible validation helper:

```python
def validate_argocd_options(features: dict, execution: dict) -> list[ValidationResult]:
    argocd = features.get("argocd", {})
    manage = bool(argocd.get("manage", False))
    resume_on_failure = bool(argocd.get("resume_on_failure", False))
    mode = execution.get("mode", "dry_run")

    results = []

    if resume_on_failure and not manage:
        results.append(error(
            "argocd.resume_on_failure requires argocd.manage=true"
        ))

    if resume_on_failure and mode == "validate":
        results.append(error(
            "argocd.resume_on_failure is not valid with execution.mode=validate"
        ))

    return results
```

Also verify whether `resume_only` exists in Ansible variables. If yes, align with Python’s conflicts.

## 6.5 Decide and document path policy

Pick one.

### Option A — strict parity with Python

Reject `~` everywhere.

```text
Allowed:
  ./artifacts/report.json
  /tmp/acm-switchover/report.json

Rejected:
  ~/report.json
  ../../report.json
```

### Option B — allow `~` everywhere after expansion

Python and Ansible both expand `~` and validate the resolved absolute path.

Recommended: **Option A** because it is simpler and reduces environment-dependent behavior.

## 6.6 PR-6 acceptance criteria

PR-6 is complete when:

- [ ] Python and Ansible validation pass/fail the same parity cases.
- [ ] ArgoCD `resume_on_failure` rules match in both paths.
- [ ] Path policy is identical in Python and Ansible.
- [ ] Docs describe the accepted path forms.
- [ ] CI runs the parity fixture against both implementations.

---

# PR-7 — Improve klusterlet remediation scalability

## 7.1 Problem statement

Python post-activation uses bounded concurrency for cluster checks/remediation.

Ansible currently loops through cluster probes and remediation sequentially using `include_tasks`.

**Runtime risk [Inference]:** Large managed-cluster fleets can take significantly longer under Ansible than under Python.

**Current status as of 2026-05-05:** Pending. `verify_klusterlet_connections.yml` and `fix_klusterlet.yml` still use sequential `include_tasks` loops, and no `acm_klusterlet_probe.py` or `acm_klusterlet_remediate.py` module exists.

## 7.2 Files to modify

```text
ansible_collections/tomazb/acm_switchover/roles/post_activation/tasks/verify_klusterlet_connections.yml
ansible_collections/tomazb/acm_switchover/roles/post_activation/tasks/fix_klusterlet.yml
ansible_collections/tomazb/acm_switchover/roles/post_activation/tasks/fix_klusterlet_single.yml
ansible_collections/tomazb/acm_switchover/plugins/modules/acm_klusterlet_remediate.py
ansible_collections/tomazb/acm_switchover/plugins/modules/acm_klusterlet_probe.py
```

## 7.3 Recommended design

Move cluster-level probe/remediation into custom modules with bounded concurrency.

Variables:

```yaml
acm_switchover_execution:
  concurrency:
    klusterlet_probe_workers: 10
    klusterlet_remediation_workers: 10
```

Module input:

```yaml
tomazb.acm_switchover.acm_klusterlet_remediate:
  secondary_hub:
    kubeconfig: "{{ acm_switchover_hubs.secondary.kubeconfig }}"
    context: "{{ acm_switchover_hubs.secondary.context }}"
  managed_clusters: "{{ acm_switchover_managed_clusters }}"
  pending_clusters: "{{ acm_cluster_verify_result.pending | default([]) }}"
  workers: "{{ acm_switchover_execution.concurrency.klusterlet_remediation_workers | default(10) }}"
  mode: "{{ acm_switchover_execution.mode | default('dry_run') }}"
```

Module output:

```json
{
  "changed": true,
  "results": [
    {
      "cluster": "cluster-a",
      "status": "remediated",
      "steps": {
        "import_secret_read": "ok",
        "bootstrap_secret_deleted": "ok",
        "bootstrap_secret_applied": "ok",
        "klusterlet_restarted": "ok"
      }
    }
  ],
  "failed_clusters": [],
  "skipped_clusters": []
}
```

## 7.4 Preserve best-effort behavior

Current Ansible remediation uses `ignore_errors: true` for several best-effort steps. The module should not fail the whole phase for one cluster unless a strict variable is set:

```yaml
acm_switchover_features:
  klusterlet:
    strict_remediation: false
```

Behavior:

```text
strict_remediation=false:
  return failed_clusters but do not fail module

strict_remediation=true:
  fail module if any cluster remediation fails
```

## 7.5 PR-7 acceptance criteria

PR-7 is complete when:

- [ ] Klusterlet probe/remediation supports bounded concurrency.
- [ ] Default concurrency matches Python’s effective behavior: 10 workers.
- [ ] Sequential behavior can be forced with workers=1.
- [ ] Module returns per-cluster structured results.
- [ ] Existing best-effort behavior remains available.
- [ ] Tests cover:
  - [ ] no pending clusters;
  - [ ] pending cluster without kubeconfig;
  - [ ] import secret missing;
  - [ ] successful remediation;
  - [ ] partial remediation failure;
  - [ ] strict and non-strict modes.

---

# PR-8 — Documentation and migration updates

## 8.1 Problem statement

The CLI migration map appears to reference a kubeconfig generation option that does not match the Python CLI polarity.

The docs should also explain checkpoint identity, validate-mode behavior, activation live-read behavior, and safe resume.

**Current status as of 2026-05-05:** Pending, with a protected-doc constraint. `docs/ACM_SWITCHOVER_RUNBOOK.md` and `.claude/skills/**/*.skill.md` are protected by `AGENTS.md` and must not be edited without explicit operator approval, diff review, justification, and runbook/skills synchronization.

## 8.2 Files to modify

```text
README.md
CHANGELOG.md
ansible_collections/tomazb/acm_switchover/docs/cli-migration-map.md
ansible_collections/tomazb/acm_switchover/docs/variable-reference.md
docs/operations/usage.md
docs/operations/quickref.md
docs/ACM_SWITCHOVER_RUNBOOK.md
docs/ACM_SWITCHOVER_RUNBOOK_TLDR.md
```

Protected-file handling:

```text
Do not edit docs/ACM_SWITCHOVER_RUNBOOK.md or .claude/skills/**/*.skill.md
as part of a normal docs PR. If runbook wording must change, request explicit
operator approval first and present the runbook/skills diff separately.
```

## 8.3 Required doc updates

### Checkpoint section

Document:

```text
- Checkpoints are operation-identity-bound.
- A checkpoint created for one hub pair cannot be reused for another hub pair.
- validate and dry_run do not persist checkpoint progress.
- checkpoint.reset=true starts a new checkpoint.
- reset_from is used internally after ArgoCD resume-on-failure.
```

Example:

```yaml
acm_switchover_execution:
  mode: execute
  checkpoint:
    enabled: true
    path: ".state/acm-switchover-prod-a-to-prod-b.json"
    reset: false
```

### Activation section

Document:

```text
Activation always re-reads secondary Restore resources.
Preflight Restore state is advisory only.
Activation fails before mutation if the passive Restore is not activation-ready.
```

### ArgoCD section

Document:

```text
resume_on_failure=true requires argocd.manage=true.
Resume targets Applications paused by the current run ID.
Use resume_force=true only for manual recovery.
```

### Decommission section

Document:

```text
summary_path uses the same safe-path policy as report artifacts.
```

## 8.4 PR-8 acceptance criteria

PR-8 is complete when:

- [ ] Migration map no longer references stale Python option names.
- [ ] Variable reference documents all new variables.
- [ ] Non-protected docs explain validate/dry-run checkpoint behavior.
- [ ] Non-protected docs explain safe checkpoint reset.
- [ ] Runbook and `.claude/skills` changes are either explicitly approved and synchronized, or intentionally deferred with the reason documented.
- [ ] CHANGELOG has an operator-facing compatibility note.

---

# 9. Test plan

## 9.1 Unit tests

### Checkpoint unit tests

Required cases:

```text
test_build_operation_identity_contains_hub_and_operation_fields
test_identity_match_passes
test_identity_mismatch_fails
test_schema_1_completed_checkpoint_requires_reset
test_schema_1_empty_checkpoint_can_migrate
test_validate_mode_does_not_write
test_dry_run_mode_does_not_write
test_execute_mode_writes_atomically
test_corrupt_checkpoint_is_moved_aside
test_reset_from_primary_prep_removes_downstream_phases
```

### Restore activation tests

Required cases:

```text
test_activation_reads_activation_restores_not_preflight_restores
test_passive_restore_ready_finished
test_passive_restore_ready_completed
test_passive_restore_ready_benign_finished_with_errors
test_passive_restore_not_ready_failed
test_passive_restore_not_ready_running
test_passive_restore_missing
test_full_restore_existing_with_passive_present_still_cleans_passive
```

### Validation parity tests

Required cases:

```text
test_resume_on_failure_requires_argocd_manage_python
test_resume_on_failure_requires_argocd_manage_ansible
test_resume_on_failure_rejected_in_validate_python
test_resume_on_failure_rejected_in_validate_ansible
test_path_policy_rejects_tilde_python
test_path_policy_rejects_tilde_ansible
```

## 9.2 Molecule or integration-style Ansible tests

Use mocked Kubernetes responses where possible.

### Scenario A — validate mode checkpoint

```text
Given checkpoint.enabled=true
And execution.mode=validate
When preflight passes
Then checkpoint file is absent or unchanged
```

### Scenario B — hub identity mismatch

```text
Given checkpoint was created for primary=A secondary=B
When playbook runs with primary=C secondary=D
Then playbook fails before skipping any phase
```

### Scenario C — stale preflight Restore

```text
Given preflight sees Restore phase=Finished
And activation live-read sees Restore phase=Failed
When activation starts
Then activation fails before patch/create/delete
```

### Scenario D — resume after late failure

```text
Given checkpoint completed_phases=[preflight, primary_prep, activation]
And failure occurs in post_activation
And argocd.resume_on_failure=true
When rescue runs
Then checkpoint completed_phases=[preflight]
```

### Scenario E — primary prep resume

```text
Given checkpoint completed_phases=[preflight]
And no preflight facts are present
When primary_prep runs
Then primary_prep reads MCH and BackupSchedule itself
```

## 9.3 Live-cluster verification plan

Run only after unit and mocked tests pass.

### Environment

Use a non-production ACM pair:

```text
primary hub: test-primary
secondary hub: test-secondary
managed clusters: 2 minimum
backup namespace: open-cluster-management-backup
observability: enabled if available
ArgoCD: enabled if available
```

### Verification sequence

1. Run Ansible `mode=validate`, checkpoint enabled.
   - Expected: no checkpoint phase completion persisted.
2. Run Ansible `mode=dry_run`, checkpoint enabled.
   - Expected: no checkpoint phase completion persisted.
3. Run Ansible `mode=execute` through preflight only, then interrupt before primary prep.
   - Expected: checkpoint contains `preflight` only and operation identity.
4. Resume execute.
   - Expected: primary prep reads its own MCH and BackupSchedules.
5. Mutate passive Restore to non-ready before activation.
   - Expected: activation fails before mutation.
6. Restore passive Restore to ready.
   - Expected: activation proceeds.
7. Force a post-activation failure with ArgoCD resume-on-failure enabled.
   - Expected: ArgoCD resumes and checkpoint resets from `primary_prep`.
8. Retry.
   - Expected: primary prep, activation, post-activation, and finalization re-run as needed.
9. Run decommission dry-run with unsafe summary path.
   - Expected: path rejected.
10. Run decommission dry-run with safe summary path.
   - Expected: summary written.

---

# 10. Implementation sequence by work block

## Block 1 — Checkpoint schema and write safety

- [ ] Add schema version `2.0`.
- [ ] Add `operation_identity`.
- [ ] Add identity comparison.
- [ ] Add schema migration/rejection.
- [ ] Add atomic write and read handling.
- [ ] Add lock file.
- [ ] Add validate/dry-run non-mutating behavior.
- [ ] Add `reset_from`.
- [ ] Add tests.
- [ ] Update checkpoint docs.

Do not start activation changes until this passes locally.

## Block 2 — Activation live-read

- [ ] Introduce `acm_activation_restores_info`.
- [ ] Remove fallback to preflight Restore facts.
- [x] Confirm readiness output exists in Restore info module (`restore_phase`, `restore_ready`).
- [ ] Add `restore_ready_reason` to Restore info module if assertion messages need it.
- [ ] Add activation-time readiness assert.
- [ ] Add tests for stale preflight Restore data.
- [ ] Update non-protected docs.
- [ ] Request explicit operator approval before any runbook update.

## Block 3 — Phase self-sufficiency

- [ ] Add primary prep MCH live-read.
- [ ] Add primary prep BackupSchedule live-read.
- [ ] Rename phase-owned facts.
- [ ] Remove primary prep dependency on preflight facts.
- [x] Confirm post_activation has execute-mode local ManagedCluster discovery.
- [ ] Add phase entry asserts.
- [ ] Add resume tests.

## Block 4 — ArgoCD resume

- [x] Confirm run ID is recorded in ArgoCD pause annotation.
- [ ] Persist pause metadata in checkpoint operational data.
- [x] Confirm resume is run-id aware.
- [x] Decide whether to add `resume_force` override; document if intentionally omitted.
- [x] Use `reset_from primary_prep` after resume-on-failure.
- [x] Add failure/resume tests.

## Block 5 — Decommission path safety

- [ ] Replace raw `copy` summary write with `acm_report_artifact`.
- [ ] Add optional mode support if required.
- [ ] Add unsafe path tests.
- [ ] Update docs.

## Block 6 — Validation parity

- [ ] Create shared validation parity fixture.
- [ ] Add Python test runner for fixture.
- [ ] Add Ansible validation test runner for fixture.
- [ ] Add missing ArgoCD rules to Ansible validation.
- [ ] Normalize path policy.
- [ ] Update variable reference.

## Block 7 — Klusterlet scalability

- [ ] Decide module-based or async-based implementation.
- [ ] Prefer module-based bounded concurrency.
- [ ] Add worker variables.
- [ ] Return structured per-cluster results.
- [ ] Preserve non-strict best-effort mode.
- [ ] Add tests.

---

# 11. Detailed backlog tickets

## Ticket ACM-ANS-001 — Add checkpoint operation identity

**Type:** bug/safety  
**Priority:** P0  
**Files:**

```text
plugins/module_utils/checkpoint.py
plugins/action/checkpoint_phase.py
roles/*/tasks/main.yml
```

**Tasks:**

- [ ] Add `build_operation_identity()`.
- [ ] Add `validate_operation_identity()`.
- [ ] Add identity fields to checkpoint record.
- [ ] Pass `hubs`, `operation`, and collection version into checkpoint action.
- [ ] Fail on mismatch unless reset is explicit.

**Acceptance criteria:**

- [ ] Checkpoint from hub pair A cannot be used for hub pair B.
- [ ] Error message names mismatched fields.
- [ ] Tests pass.

## Ticket ACM-ANS-002 — Make validate mode checkpoint read-only

**Type:** bug/safety  
**Priority:** P0  
**Files:**

```text
plugins/action/checkpoint_phase.py
playbooks/switchover.yml
```

**Tasks:**

- [ ] Treat `validate` like `dry_run` for checkpoint persistence.
- [ ] Return `would_change=true` for validate mode.
- [ ] Add unit test.

**Acceptance criteria:**

- [ ] Validate-only run does not mark `preflight` complete.

## Ticket ACM-ANS-003 — Atomic checkpoint persistence

**Type:** reliability  
**Priority:** P0  
**Files:**

```text
plugins/module_utils/checkpoint.py
plugins/action/checkpoint_phase.py
```

**Tasks:**

- [ ] Add lock helper.
- [ ] Add atomic write helper.
- [ ] Add corrupt JSON handling.
- [ ] Replace direct JSON writes.

**Acceptance criteria:**

- [ ] Simulated interrupted write does not corrupt final checkpoint.
- [ ] Corrupt checkpoint is moved aside and reported.

## Ticket ACM-ANS-004 — Activation must live-read Restore resources

**Type:** bug/safety  
**Priority:** P0  
**Files:**

```text
roles/activation/tasks/discover_resources.yml
roles/activation/tasks/verify_passive_sync.yml
```

**Tasks:**

- [ ] Add `acm_activation_restores_info`.
- [ ] Remove fallback to preflight Restore facts.
- [ ] Add explicit test override variable.

**Acceptance criteria:**

- [ ] Activation does not consume `acm_secondary_restore_info` from preflight.

## Ticket ACM-ANS-005 — Activation must require passive Restore readiness

**Type:** bug/safety  
**Priority:** P0  
**Status:** Partial. `restore_phase` and `restore_ready` already exist; activation-time enforcement is still missing.
**Files:**

```text
roles/activation/tasks/verify_passive_sync.yml
plugins/modules/acm_restore_info.py
```

**Tasks:**

- [x] Expose `restore_ready` and `restore_phase`.
- [ ] Expose `restore_ready_reason` if needed for actionable assertion output.
- [ ] Fail activation if selected Restore is not ready.
- [ ] Keep sync-enabled/conventional-name identity check.

**Acceptance criteria:**

- [ ] Failed/Running/non-ready Restore blocks activation before mutation.

## Ticket ACM-ANS-006 — Primary prep must read its own MCH and BackupSchedules

**Type:** bug/resume  
**Priority:** P1  
**Files:**

```text
roles/primary_prep/tasks/discover_resources.yml
roles/primary_prep/tasks/pause_backups.yml
```

**Tasks:**

- [ ] Add primary prep MCH read.
- [ ] Add primary prep BackupSchedule read.
- [ ] Replace use of preflight facts.

**Acceptance criteria:**

- [ ] Primary prep works when preflight facts are absent but checkpoint says preflight completed.

## Ticket ACM-ANS-007 — Reset checkpoint from primary_prep after ArgoCD resume-on-failure

**Type:** bug/resume  
**Priority:** P1  
**Status:** Implemented. Current rescue uses `status: reset` with `checkpoint.reset_from: primary_prep`, which removes `primary_prep` and downstream phases.
**Files:**

```text
playbooks/switchover.yml
plugins/module_utils/checkpoint.py
plugins/action/checkpoint_phase.py
```

**Tasks:**

- [x] Add `reset_from` checkpoint configuration.
- [x] Use it after ArgoCD resume-on-failure.
- [x] Remove downstream completed phases.

**Acceptance criteria:**

- [x] After late failure + ArgoCD resume, retry starts from primary prep boundary.

## Ticket ACM-ANS-008 — Make ArgoCD resume run-id aware

**Type:** safety  
**Priority:** P1  
**Status:** Implemented for issue #31. Exact run-id matching is implemented; the optional force override is intentionally omitted so resume remains scoped to Applications paused by the current run.
**Files:**

```text
roles/argocd_manage/tasks/resume.yml
plugins/module_utils/argocd.py
```

**Tasks:**

- [x] Resume only apps annotated with current run ID.
- [x] Omit `resume_force`; manual recovery remains out of scope.
- [x] Add tests.

**Acceptance criteria:**

- [x] Apps paused by another run are not resumed by default.
- [ ] Apps paused by another run are resumed only when force override is explicitly enabled, if that override is added.

## Ticket ACM-ANS-009 — Decommission summary must use artifact path validation

**Type:** security/hardening  
**Priority:** P2  
**Status:** Pending. Decommission still writes with `ansible.builtin.copy`.
**Files:**

```text
roles/decommission/tasks/main.yml
plugins/modules/acm_report_artifact.py
```

**Tasks:**

- [ ] Replace raw copy with report artifact module.
- [ ] Add optional file mode support if needed.
- [ ] Test unsafe paths.

**Acceptance criteria:**

- [ ] Traversal path is rejected.

## Ticket ACM-ANS-010 — Validation parity fixture

**Type:** quality  
**Priority:** P2  
**Status:** Pending. Python and collection path policy still diverge on leading `~/`, and collection Argo CD option validation is still incomplete.
**Files:**

```text
lib/validation.py
plugins/module_utils/validation.py
tests/fixtures/validation_parity_cases.yml
```

**Tasks:**

- [ ] Create shared cases.
- [ ] Run against Python and Ansible validation.
- [ ] Add missing ArgoCD rules.
- [ ] Normalize path policy.

**Acceptance criteria:**

- [ ] Same pass/fail results across both implementations.

## Ticket ACM-ANS-011 — Klusterlet bounded concurrency

**Type:** performance  
**Priority:** P3  
**Status:** Pending. Klusterlet probe/remediation still uses sequential task loops.
**Files:**

```text
roles/post_activation/tasks/*
plugins/modules/acm_klusterlet_probe.py
plugins/modules/acm_klusterlet_remediate.py
```

**Tasks:**

- [ ] Add worker variables.
- [ ] Add custom modules or async fan-out.
- [ ] Return structured results.
- [ ] Preserve best-effort mode.

**Acceptance criteria:**

- [ ] 50-cluster remediation no longer runs strictly one cluster at a time unless workers=1.

---

# 12. Maintainer review checklist

Before merging each PR, reviewers should check:

- [ ] Does this change preserve `dry_run` behavior?
- [ ] Does this change preserve `validate` behavior?
- [ ] Does this change mutate the cluster only in `execute` mode?
- [ ] Does this change avoid relying on stale preflight facts?
- [ ] Does this change behave correctly after checkpoint resume?
- [ ] Does this change behave correctly after partial failure?
- [ ] Does this change have Python/Ansible parity tests where applicable?
- [ ] Are new variables documented?
- [ ] Are unsafe paths rejected consistently?
- [ ] Are operator-facing failure messages actionable?

---

# 13. Release notes draft

Use something like this in `CHANGELOG.md`:

```markdown
## Unreleased

### Safety

- Added operation identity binding to Ansible switchover checkpoints.
  Checkpoints created for one hub pair are no longer resumable against a different hub pair without explicit reset.

- Changed Ansible validate mode checkpoint behavior.
  `execution.mode=validate` no longer persists phase completion.

- Activation now re-reads secondary Restore resources immediately before mutation and requires the selected passive Restore to be activation-ready.

- ArgoCD resume-on-failure now resets checkpoint progress from `primary_prep`, causing downstream phases to be re-run on retry.

### Reliability

- Checkpoint writes now use atomic replace semantics and lock files.
- Phase roles no longer depend on transient facts from earlier phases.

### Hardening

- Decommission summary output now uses the same safe-path policy as report artifacts.

### Compatibility notes

- Existing schema `1.0` checkpoint files with completed phases must be reset before resume.
- Operators should use unique checkpoint paths per hub pair.
```

---

# 14. Operator migration guidance

Before upgrading:

```bash
cp .state/switchover.json .state/switchover.json.backup
```

For active in-progress operations using old checkpoints:

```yaml
acm_switchover_execution:
  checkpoint:
    enabled: true
    path: .state/switchover.json
    reset: true
```

Recommended new path pattern:

```yaml
acm_switchover_execution:
  checkpoint:
    enabled: true
    path: ".state/{{ primary_name }}-to-{{ secondary_name }}-switchover.json"
```

Do not reuse one checkpoint path across environments.

---

# 15. Highest-priority minimal patch set

If time is limited, implement these first:

1. `validate` mode does not write checkpoint.
2. Checkpoint operation identity mismatch fails.
3. Atomic checkpoint writes.
4. Activation live-reads Restore resources.
5. Activation requires passive Restore readiness.
6. ArgoCD resume-on-failure resets from `primary_prep`.

These six changes address the most serious correctness and safety risks found in the static review.

---

# 16. Source files reviewed or referenced

The plan was derived from static review of the following repository areas:

```text
acm_switchover.py
lib/validation.py
lib/utils.py
lib/constants.py
lib/argocd.py
lib/argocd_coordinator.py
lib/rbac_validator.py
modules/activation.py
modules/primary_prep.py
modules/post_activation.py
modules/finalization.py
modules/decommission.py
ansible_collections/tomazb/acm_switchover/playbooks/switchover.yml
ansible_collections/tomazb/acm_switchover/playbooks/preflight.yml
ansible_collections/tomazb/acm_switchover/playbooks/restore_only.yml
ansible_collections/tomazb/acm_switchover/playbooks/decommission.yml
ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/*.yml
ansible_collections/tomazb/acm_switchover/roles/primary_prep/tasks/*.yml
ansible_collections/tomazb/acm_switchover/roles/activation/tasks/*.yml
ansible_collections/tomazb/acm_switchover/roles/post_activation/tasks/*.yml
ansible_collections/tomazb/acm_switchover/roles/finalization/tasks/*.yml
ansible_collections/tomazb/acm_switchover/roles/decommission/tasks/*.yml
ansible_collections/tomazb/acm_switchover/roles/argocd_manage/tasks/*.yml
ansible_collections/tomazb/acm_switchover/plugins/action/checkpoint_phase.py
ansible_collections/tomazb/acm_switchover/plugins/modules/acm_checkpoint.py
ansible_collections/tomazb/acm_switchover/plugins/modules/acm_restore_info.py
ansible_collections/tomazb/acm_switchover/plugins/modules/acm_input_validate.py
ansible_collections/tomazb/acm_switchover/plugins/modules/acm_report_artifact.py
ansible_collections/tomazb/acm_switchover/plugins/modules/acm_cluster_verify.py
ansible_collections/tomazb/acm_switchover/plugins/modules/acm_argocd_filter.py
ansible_collections/tomazb/acm_switchover/plugins/module_utils/checkpoint.py
ansible_collections/tomazb/acm_switchover/plugins/module_utils/validation.py
ansible_collections/tomazb/acm_switchover/plugins/module_utils/artifacts.py
ansible_collections/tomazb/acm_switchover/plugins/module_utils/constants.py
ansible_collections/tomazb/acm_switchover/plugins/module_utils/argocd.py
ansible_collections/tomazb/acm_switchover/docs/cli-migration-map.md
ansible_collections/tomazb/acm_switchover/docs/variable-reference.md
README.md
CHANGELOG.md
```
