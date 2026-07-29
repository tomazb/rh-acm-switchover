# Auto-Import Strategy Transaction — Design

**Date:** 2026-07-29
**Branch:** `ansible` (spec branch `docs/thermos-safety-specs`)
**Status:** approved design, awaiting implementation plan
**Origin:** cross-validation of main-branch safety specs against `ansible` @ `0bf55db9`,
independently revalidated (Codex). Entire area is untracked in `thermos-resolution-plan.md`
(only `F20`, an unrelated version-gating refactor, touches auto-import).

## Problem

The switchover temporarily sets `autoImportStrategy: ImportAndSync` in the
`multicluster-engine/import-controller-config` ConfigMap on the destination hub and is
supposed to undo it during finalization. Four defects:

1. **Mutation before ownership.** `modules/activation.py:630-634` calls
   `create_or_patch_configmap`, then `:635` records
   `set_config("auto_import_strategy_set", True)`. A crash between the two leaves the
   destination hub on `ImportAndSync` with finalization believing nothing was changed
   (`modules/finalization.py:1569-1576` then only warns).
2. **`data: null` breaks the reader.** `modules/activation.py:615-616` uses
   `.get("data", {}).get(...)`; a ConfigMap with `data: null` (key present, value null —
   legal) raises `AttributeError`, caught at `:636-639` and converted to a misleading
   `SwitchoverError` (management enabled) or a silent skip (disabled). The collection
   shares the pattern in `roles/activation/tasks/manage_auto_import.yml:55-59` and
   `roles/finalization/tasks/reset_auto_import.yml:35-40`.
3. **Restore deletes the whole ConfigMap.** `modules/finalization.py:1545-1553` →
   `delete_configmap` (`lib/kube_client.py:492-513`). Apply only patched one key; restore
   destroys every operator-owned key, label, and annotation on a pre-existing ConfigMap.
   Collection: `reset_auto_import.yml:21-40` `state: absent`, gated on an in-memory
   `set_fact` (`manage_auto_import.yml:105-108`) that survives only via checkpoint
   `operational_data` — and checkpointing defaults to disabled
   (`roles/activation/defaults/main.yml:15-16`).
4. **Decommission is unaware.** Integrated decommission can tear down the old hub while
   the destination hub still runs the temporary `ImportAndSync`, with no check anywhere.

## Goals

1. Prior reality is captured durably before the ConfigMap is touched.
2. Restore inverts exactly what apply changed — never more.
3. `data: null` is normalized, never an exception, in all implementations.
4. Integrated decommission refuses to run while the transaction is unrestored.

## Non-goals

- Full transaction phase machinery (`apply_intent/apply_observed/applied` with UID/RV
  preconditions on every call) as designed in the main-branch spec series — the ansible
  branch stays with its existing state primitives (KISS).
- Changing `create_or_patch_configmap` semantics for other callers.
- MCE-operator reconciliation watching (single re-read on restore is sufficient here; the
  value is only load-bearing during activation).

## Design

### 1. Prior-state capture and durable intent (before mutation)

Python (`modules/activation.py`):

0. **Resume continuation**: if state already carries `auto_import_txn` in
   `intent`/`applied` for this run, the existing transaction is continued — its
   `auto_import_txn_id` and `auto_import_prior` are reused verbatim, never re-minted or
   re-captured. Re-capturing after the mutation would record our own temporary
   `ImportAndSync` as the "prior" and finalization would then `restored_noop` past it.
   Only a run with no open transaction starts a new one.
1. Read the ConfigMap; normalize `data: null` → `{}`.
2. Record to state — persisted immediately by `set_config` — before any mutation:
   - `auto_import_txn_id`: a fresh unique id (uuid4); recording it also clears any prior
     terminal restore evidence, so stale evidence from an earlier transaction can never
     satisfy this one's gate.
   - `auto_import_prior`: one of
     `{"state": "absent"}` |
     `{"state": "no_key", "uid": <cm-uid>}` |
     `{"state": "value", "value": "<X>", "uid": <cm-uid>}`
   - `auto_import_txn`: `"intent"`
3. Apply the create/patch (unchanged call).
4. For the `absent` prior: immediately re-read and persist the created ConfigMap's UID
   into `auto_import_prior` (`created_uid`). A crash between create and this record leaves
   an `absent` prior without `created_uid`; restore then falls back to key-removal only —
   it never deletes a ConfigMap whose creation it cannot prove (no unconditional delete).
5. Set `auto_import_txn`: `"applied"`.

Legacy migration: a state with the old `auto_import_strategy_set: true` boolean and no
`auto_import_prior` is treated as `applied` with unknown prior → restore degrades to
remove-only-our-key plus an explicit warning that the original value could not be known.
At migration time a transaction id is minted and persisted for the legacy record, and the
restore's terminal result carries it — so legacy runs neither stay blocked at the
decommission gate (no id to match) nor pass it without matching evidence.
`auto_import_strategy_set` stops being written.

Collection: the same record is written into checkpoint `operational_data` **before** the
`kubernetes.core.k8s` task. Auto-import management **requires checkpointing**: the
activation role asserts at entry that checkpointing is enabled when
auto-import management is requested and fails otherwise — the in-memory `set_fact`-only
mode is removed for this feature. This closes the checkpoint-disabled crash window
instead of documenting it; the decommission gate (§4) additionally fails closed whenever
required transaction evidence is absent.

### 2. Key-level restore (finalization)

Restore reads `auto_import_txn` + `auto_import_prior` and inverts exactly:

Before inverting, restore reads the live `autoImportStrategy` value and compares it with
the temporary value this run wrote (`ImportAndSync`). If it differs — an operator or
another actor changed it mid-switchover — restore preserves the live value, performs no
mutation, and journals `restore_conflict` with both values (terminal result; surfaced as a
warning-level completion note, not silently).

| captured prior | restore action (live value == our temporary value) | journal result |
| --- | --- | --- |
| `absent` with `created_uid` | if live CM still matches the tool-owned shape (`data` is exactly our one key; no operator-added keys/labels/annotations beyond creation defaults) → delete CM with server-side UID precondition; otherwise patch removing only our key (operator content appeared post-creation — preserve it) | `restored_deleted` / `restored_key_removed` |
| `absent` without `created_uid` | patch removing only the `autoImportStrategy` key (creation unprovable — never unconditional delete) | `restored_key_removed` |
| `no_key` | patch removing only the `autoImportStrategy` key | `restored_key_removed` |
| `value X` | patch the key back to `X` | `restored_value` |
| any, live value already matches prior | no-op | `restored_noop` |
| any, live value ≠ our temporary value and ≠ prior | preserve live value, no mutation | `restore_conflict` |

The `no_key` and `value` patch paths are UID-guarded too: restore compares the live
ConfigMap UID with the captured `uid`; a mismatch (deleted and recreated since capture)
or a missing object with prior `value X` → `restore_conflict`, no mutation. A missing
object with prior `no_key` → `restored_noop` (nothing of ours remains).

- `intent` (crash before/during apply): re-read the live ConfigMap and apply the same
  inversion against the captured prior — if the live value never changed, journal
  `restored_noop`.
- The delete uses a server-side UID precondition (`V1DeleteOptions.preconditions.uid` with
  the captured `created_uid`); `delete_configmap` gains an optional precondition
  parameter. Precondition mismatch (409/412) → leave the replacement ConfigMap intact,
  refuse the delete, journal `restore_conflict`. No read-before-delete race remains.
- Whole-ConfigMap deletion for a pre-existing ConfigMap is removed entirely.
- Restore failure raises `SwitchoverError` (Python) / `ansible.builtin.fail` (collection)
  — no warning-only path. The "ownership unset" branch survives only for genuinely
  never-applied runs (`auto_import_txn` absent and legacy boolean absent).
- Terminal journal result is recorded in state before the phase completes, and carries
  `auto_import_txn_id`; the decommission gate (§4) accepts terminal evidence only when its
  id matches the current transaction's id.

Collection `reset_auto_import.yml` mirrors the same table using the checkpoint record.

### 3. `data: null` normalization

Every reader normalizes `data` to `{}` when null or absent:
`modules/activation.py:615-616`, `modules/finalization.py` restore read,
`manage_auto_import.yml:55-59`, `reset_auto_import.yml:35-40`. With management enabled,
real API failures still raise `SwitchoverError` — now with the true cause instead of a
wrapped `AttributeError`.

### 4. Decommission gate

Integrated decommission (`modules/finalization.py::_decommission_old_hub` entry) fails
closed unless the run's own state shows a **successful** terminal restore
(`restored_deleted` / `restored_key_removed` / `restored_value` / `restored_noop`) whose
`auto_import_txn_id` matches the current transaction. `intent`/`applied` without a
terminal entry blocks (destination hub still running the temporary `ImportAndSync`), and
`restore_conflict` blocks too — a conflict means the captured prior was *not* restored;
proceeding past it requires an explicit operator acknowledgement flag (naming both
values), not the conflict record itself. Pure state read — no new cluster calls.
Standalone decommission (`--decommission`, typically a foreign/absent state file) is
unaffected. Collection: same check in the decommission role against checkpoint data when
present.

## Testing

- Table test: 3 prior states × apply → restore, asserting exact patch/delete bodies and
  journal results; sibling keys asserted untouched.
- Crash-window: state has `intent` + prior, ConfigMap unmutated → restore is `restored_noop`;
  ConfigMap mutated → correct inversion.
- Legacy boolean migration path (remove-only-our-key + warning).
- `data: null` for every reader (Python + both collection roles' Jinja).
- Delete precondition mismatch on `absent` prior → replacement intact, `restore_conflict`.
- `absent` prior without `created_uid` (crash before UID record) → key removal, never delete.
- Live value changed by operator mid-run → `restore_conflict`, live value preserved.
- Stale terminal evidence from an earlier `auto_import_txn_id` → gate still blocks.
- Decommission gate: blocks on `applied`-without-restore, blocks on `restore_conflict`
  (passes only with the acknowledgement flag), passes on successful terminal journal,
  passes on no-transaction state.
- Resume continuation: crash after mutation with `intent` open → rerun reuses the same
  txn id and prior (no re-capture of `ImportAndSync` as prior); legacy-migrated record
  passes the gate via its minted id.
- UID-guarded patch paths: recreated ConfigMap (new UID) with prior `no_key`/`value` →
  `restore_conflict`, untouched; tool-created CM with operator-added keys → key removal
  instead of delete.
- Collection parity tests for capture record shape and restore table.
- Version bump per repo policy (Python + collection, synced).

## Tracker updates (same PR)

| id | severity | summary |
| --- | --- | --- |
| new-B1 | High | Restore deletes entire pre-existing import-controller-config ConfigMap (operator data loss) |
| new-B2 | Medium | ConfigMap mutated before ownership recorded; crash orphans ImportAndSync |
| new-B3 | Medium | `data: null` raises AttributeError → misleading error / silent skip (Python + collection) |
| new-B4 | Medium | Decommission ignores unrestored auto-import transaction |

Plus one planned slice row referencing this design. `F20` cross-referenced as unrelated.

## Acceptance criteria

1. Kill -9 between intent record and mutation, then resume: finalization restores (or
   no-ops) correctly; destination hub never left on `ImportAndSync` silently.
2. A pre-existing ConfigMap with unrelated keys survives a full switchover with only the
   `autoImportStrategy` key transiently changed and exactly restored.
3. A ConfigMap created by the tool is deleted on restore only when its UID matches.
4. `data: null` never surfaces as `AttributeError` in any implementation.
5. Integrated decommission refuses to run with an unrestored transaction, naming the fix
   (run finalization / restore manually).
6. Legacy state with `auto_import_strategy_set: true` restores via key-removal with an
   explicit unknown-prior warning.
