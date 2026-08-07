# Checkpoint Convergence and Default Posture (Issue #214)

**Date:** 2026-08-06
**Issue:** #214 — Run-record follow-up: converge collection checkpoint state on
named operations
**Audit findings addressed:** C3 (auto-import obligation lost at shipped
defaults), C4 (checkpoint off by default — decided as posture, not flipped)
from `docs/ansible-collection/parity-audit-2026-08-03.md`.

## Problem

1. **C3:** The collection's auto-import reset obligation
   (`auto_import_strategy_changed`) lives in an in-memory fact plus checkpoint
   `operational_data`, both gated on `checkpoint.enabled` — which defaults to
   `false` in all five role defaults. An interrupted run at shipped defaults
   leaves `autoImportStrategy=ImportAndSync` on the destination hub
   permanently; finalization and the next preflight both report `pass`.
2. **C4:** With `checkpoint.enabled: false`, the shipped collection has no
   resume, no persisted `argocd_run_id`, and no hub-identity binding.
3. **Vocabulary drift:** Collection roles read checkpoint state through raw
   Jinja chains (`.get('operational_data', {}).get('<key>')` across nine keys
   in five roles), while the Python CLI routes the same facts through the
   guardrail-locked `RunRecord` facade (`lib/run_record.py`).
4. **resume_summary divergence:** Python's
   `RunRecord.record_resume_start_phase` replaces the whole dict (last resume
   wins); the collection's `checkpoint_phase` fills `resume_start_phase` only
   when unset (first resume wins, forever).

## Decisions

1. **Cluster-as-register for the auto-import obligation.** The obligation
   marker rides the mutation itself, exactly as PR #223 did for Argo CD pause
   annotations. `checkpoint.enabled` stays `false` by default.
2. **resume_summary converges on replace** (Python semantics): each resumed
   process records its own start phase.
3. **Facade + named outputs:** `module_utils/checkpoint.py` owns the key
   vocabulary; `checkpoint_phase` returns flattened named facts; roles stop
   reading raw keys. No on-disk key renames.
4. **Orphan posture:** preflight warns (non-blocking); finalization is the
   discharge point.
5. **C4 residue is documented posture:** resume/identity-binding remain
   opt-in; recorded in `coexistence.md` and the role-defaults comments, not
   silently absent.

## Design

### 1. Auto-import obligation on the cluster (C3)

- `roles/activation/tasks/manage_auto_import.yml`, task "Set autoImportStrategy
  to ImportAndSync": add a marker annotation inside the same
  `kubernetes.core.k8s` `state: present` definition — atomic with the
  mutation. Constant lives in `module_utils/checkpoint.py` (or a small shared
  constants module):

  ```python
  AUTO_IMPORT_MARKER_ANNOTATION = "acm-switchover.open-cluster-management.io/import-strategy-set-by"
  AUTO_IMPORT_MARKER_VALUE = "acm-switchover"
  ```

  Re-apply is idempotent. The existing guard is unchanged: when the operator
  already had `ImportAndSync`, the collection does not patch and therefore
  writes no marker — no false obligation.

- `roles/finalization/tasks/reset_auto_import.yml` discharge gate becomes:
  **marker present on the ConfigMap** OR the legacy signal
  (`_auto_import_strategy_changed` in-memory fact / checkpoint
  `auto_import_strategy_changed` flag). The `ImportAndSync` value check stays.
  The legacy OR-branch keeps discharging obligations created by pre-marker
  collection versions; it can be retired in a later release. Discharge still
  deletes the ConfigMap (Python-parity behavior; the pre-existing-ConfigMap
  edge — where deletion also removes operator content — is existing behavior
  on both runtimes and out of scope here).

- **Preflight orphan check:** a new preflight task reads
  `import-controller-config` on **both hubs**; if the marker is present and
  the value is `ImportAndSync`, it records a warning entry in
  `acm_switchover_validation_results` (severity `warning`, following the
  GitOps-drift-warning precedent). Non-blocking: activation may legitimately
  want `ImportAndSync` again in this run; finalization discharges.

### 2. Facade + named outputs (vocabulary convergence)

- `module_utils/checkpoint.py` gains named accessors over checkpoint dicts,
  mirroring RunRecord verbs. All read-side key literals become private to this
  module:
  - `argocd_run_id(checkpoint)`
  - `argocd_discovery_namespaces(checkpoint)`
  - `auto_import_override_pending(checkpoint)` — reads the existing
    `auto_import_strategy_changed` key; the RunRecord-aligned *name* converges,
    the on-disk key does not change
  - `expected_managed_clusters(checkpoint)` → `(names, count)`
  - `hub_observability(checkpoint)` → primary/secondary flags
  - `saved_backup_schedule(checkpoint)`
  - `resume_start_phase(checkpoint)` / `record_resume_start_phase(checkpoint,
    phase)` — replace semantics
  - Malformed or missing shapes degrade to defaults (same tolerance model as
    `RunSummary.from_snapshot`).
- `checkpoint_phase` `enter` result adds a **`facts`** key: a flattened named
  dict built via the facade (`argocd_run_id`,
  `argocd_discovery_namespaces`, `auto_import_strategy_changed`,
  `expected_managed_cluster_names`, `expected_managed_cluster_count`,
  `primary_has_observability`, `secondary_has_observability`,
  `saved_backup_schedule`, `resume_start_phase`). Roles replace their Jinja
  read chains with `_checkpoint_enter.facts.<name>` (plus defaults where the
  checkpoint task was skipped).
- Write side is unchanged: roles keep passing `operational_data:` to the
  plugin — writes are already named at the plugin boundary.
- **Guardrail test** (mirror of `tests/test_run_record_guardrails.py`):
  asserts read-side `operational_data` access chains do not appear in role
  YAML, and checkpoint key literals appear only in `module_utils`.

### 3. resume_summary replace with process scoping

Collection `enter` fires once per phase, so a naive replace would overwrite
`resume_start_phase` with every phase entered in the same run (this is why
fill-if-unset exists today). Convergence uses process scoping:

- On the first resume record in a process, the plugin returns
  `ansible_facts: {_acm_switchover_resume_recorded: true}` alongside its
  result.
- Later `enter` calls in the same playbook process see that fact in
  `task_vars` and skip the overwrite.
- A new playbook process (a fresh resume) does not have the fact, so it
  replaces `resume_summary` wholesale — matching Python's last-resume-wins,
  scoped per run.

### 4. Parity fixture (shared key names)

A cross-runtime test (following the `__file__`-anchored Argo CD parity test
pattern) pins the shared key names between `lib/constants.py` / RunRecord
literals and the collection's `module_utils` constants:

- Shared and pinned equal: `resume_summary`, `resume_start_phase`,
  `expected_managed_cluster_names`, `expected_managed_cluster_count`,
  `primary_has_observability`, `secondary_has_observability`,
  `saved_backup_schedule`.
- Intentionally different, pinned via an explicit mapping table in the test:
  Python `auto_import_strategy_set` ↔ collection
  `auto_import_strategy_changed`.

The fixture fails when either side renames a key without updating the
contract.

### 5. Error handling

- Finalization marker read failure: task failure — fail closed; no silent
  skip of the discharge.
- Preflight orphan-check read failure: warning finding, non-blocking
  (diagnostic context).
- Dry-run / check mode / validate: reads only — no marker writes, no
  discharge, no checkpoint saves (existing `mode != 'dry_run'` and
  `is_non_mutating` gates).

### 6. Testing

- Facade accessor units, including malformed-shape degradation.
- Replace semantics with fact gating (first record replaces; same-process
  later enters do not; new process replaces again).
- Marker annotation present in the activation patch definition.
- Discharge gating matrix: marker × legacy flag × ConfigMap value.
- Preflight warning recorded for orphans on either hub; read-failure path.
- Guardrail test and parity fixture per above.
- Full collection unit suite + Python suite; `black --line-length 120`.

### 7. Documentation

- `ansible_collections/tomazb/acm_switchover/docs/coexistence.md`: record the
  auto-import cluster-as-register decision with the equivalence argument
  (obligation rides the mutation; discharge only on proven reset — same
  ADR-0001 invariant as the Argo CD register), the resume_summary
  convergence, and the opt-in resume/identity-binding posture (C4).
- Role-defaults comment on `checkpoint.enabled` pointing at the posture note.

## Out of scope

- Enabling `checkpoint.enabled` by default.
- Python-side adoption of the ConfigMap marker (Python's always-on state file
  already preserves its obligation).
- On-disk checkpoint key renames.
- Report-artifact contract (#217) and audit doc corrections (#219).
