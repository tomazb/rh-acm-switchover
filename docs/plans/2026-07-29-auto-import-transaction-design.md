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
   (`modules/finalization.py:1569-1576` then only warns). The helper itself is also
   unsuitable as creation-ownership evidence: `lib/kube_client.py:429-474` performs a
   create-first request but converts HTTP 409 into a patch and returns only the resulting
   object, without an outcome that proves whether this invocation created or patched it.
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

- A reusable cross-resource transaction framework. This design adds only the
  auto-import-specific intent/applied/ownership-conflict states and the minimum
  create-only/result boundaries needed to prove ConfigMap ownership.
- Changing `create_or_patch_configmap` semantics for other callers.
- MCE-operator reconciliation watching (single re-read on restore is sufficient here; the
  value is only load-bearing during activation).

## Design

### 1. Prior-state capture and durable intent (before mutation)

Python (`modules/activation.py`):

0. **Resume continuation**: if state already carries `auto_import_txn` in
   `intent`/`applied`/`ownership_conflict` for this run, the existing transaction is
   reused — its
   `auto_import_txn_id` and `auto_import_prior` are reused verbatim, never re-minted or
   re-captured. Re-capturing after the mutation would record our own temporary
   `ImportAndSync` as the "prior" and finalization would then `restored_noop` past it.
   An ownership conflict stays blocked and does not retry mutation. Only a run with no
   open transaction starts a new one.
1. Read the ConfigMap; normalize `data: null` → `{}`.
2. Record the complete transaction intent to state — persisted immediately by
   `set_config` and forced durable before any API mutation:
   - `auto_import_txn_id`: a fresh unique id (uuid4); recording it also clears any prior
     terminal restore evidence, so stale evidence from an earlier transaction can never
     satisfy this one's gate.
   - `auto_import_prior`: one of
     `{"state": "absent", "created_uid": null}` |
     `{"state": "no_key", "uid": <cm-uid>}` |
     `{"state": "value", "value": "<X>", "uid": <cm-uid>}`
   - `auto_import_txn`: `"intent"`
   - `auto_import_conflict`: `null`
   The allowed non-terminal transaction states are `intent` and
   `ownership_conflict`; `applied` is reachable only through the proof steps below.
3. Apply through an explicit outcome boundary, not
   `create_or_patch_configmap`:
   - For a captured `absent` prior, issue a **create-only POST**. Its typed result
     distinguishes `created` (and carries the exact create response), `already_exists`
     (HTTP 409), `patched_or_mutated` (an impossible/unsafe helper outcome), `conflict`,
     and `failure`. It never patches after 409.
   - For a captured `no_key`/`value` prior, use a UID-guarded key patch against the
     captured UID — server-enforced as one RFC 6902 JSON Patch whose first operation is
     a `test` against `/metadata/uid` (the same conditional mechanism as §2's restore
     patches). Its typed result must say `patched`; `created`, `already_exists`,
     replacement/conflict, ambiguous mutation, or failure is not accepted.
   Python adds a narrow create-only/result helper while leaving
   `create_or_patch_configmap` unchanged for its other callers. The collection uses an
   independent collection-owned module/helper with the same result algebra; native
   `kubernetes.core.k8s state=present` plus `changed` is not creation proof because it may
   patch an existing object.
4. On the absent-prior `created` result, read `metadata.uid` **only from that successful
   create response**. Require a non-empty string and durably persist it into
   `auto_import_prior.created_uid` before the transaction may become `applied`. A later
   name-based GET may verify equality with that already-persisted UID, but it may never
   establish, replace, or backfill `created_uid`.
5. Re-read for post-apply verification. For an absent prior, require the same UID as the
   create response and the expected key value. For a pre-existing prior, require the
   captured UID and expected key value. Only after that verification succeeds may
   `auto_import_txn` become `"applied"` in a durable update.

The absent-prior failure rules are exact:

- `already_exists`/409 is an ownership conflict, not an invitation to patch or adopt the
  live object.
- A result that reports patching or any mutation other than a new create is an unsafe
  protocol outcome; leave `created_uid` unset, record `ownership_conflict`, and fail.
- A create conflict or failure never establishes ownership. Leave `created_uid` unset,
  record `ownership_conflict` with the stable reason `create_conflict`,
  `create_failure`, or `create_outcome_ambiguous`, and fail. In particular, a transport
  failure after request submission may have hidden a successful create; a later
  name-based GET cannot convert that ambiguous outcome into ownership.
- A successful create response with a missing/empty/non-string UID leaves
  `created_uid` unset, records `ownership_conflict` with stable reason
  `create_response_missing_uid`, and fails. A later GET cannot repair that evidence.
- If the post-create GET is absent, unreadable, malformed, or returns a different UID,
  keep the response-derived `created_uid`, record the corresponding fail-closed
  ownership/recovery conflict, and do not mark `applied`. The different-UID object is a
  replacement and is never patched or deleted as tool-created.
- A crash after the API server successfully creates the object but before
  `created_uid` is durably persisted leaves `intent` plus an absent prior and null
  `created_uid`. On resume, a still-absent name may retry the create-only POST; any
  live same-name object becomes `ownership_conflict`. It is never adopted merely because
  it contains `ImportAndSync`, matches the intended data, or carries a familiar name.
- A crash after `created_uid` is durable but before `applied` reuses that exact UID. A
  later GET may verify it and finish the transition; a mismatch is a conflict and never
  rebinds the transaction.

`auto_import_conflict`, when non-null, is a complete sanitized object containing the
transaction id, one stable reason code (`already_exists`, `unexpected_patch`,
`create_conflict`, `create_failure`, `create_outcome_ambiguous`,
`create_response_missing_uid`, `post_create_absent`, `post_create_unreadable`,
`post_create_malformed`, or `replacement_uid`), and only the public ConfigMap
namespace/name. It never contains a newly observed UID as ownership, response bodies,
kubeconfig/client data, or ConfigMap content. `ownership_conflict` is non-terminal for
successful restoration and blocks the decommission gate.

Legacy migration: a state with the old `auto_import_strategy_set: true` boolean and no
`auto_import_prior` is migrated to `applied` with the explicit prior shape
`{"state": "unknown_legacy"}` (no `uid`, no `created_uid`). A legacy record carries
neither a captured `uid` nor a `created_uid`, so object identity is unprovable — the
same ownership rule as the `absent`-without-`created_uid` row applies: restore must not
mutate a live same-name ConfigMap it cannot prove it touched. A live ConfigMap present →
`restore_conflict` (fail closed, live object preserved, explicit message that the
original value could not be known and manual recovery — inspect and remove the temporary
`autoImportStrategy` by hand, or use the acknowledgement path — is required); live
ConfigMap absent → `restored_noop`. Unconditional remove-only-our-key is not performed.
At migration time a transaction id is minted and persisted for the legacy record, and the
restore's terminal result carries it — so legacy runs neither stay blocked at the
decommission gate (no id to match) nor pass it without matching evidence.
`auto_import_strategy_set` stops being written.

Collection: the same record is written into checkpoint `operational_data` **before** the
collection-owned create-only or UID-guarded patch task. Auto-import management
**requires checkpointing**: the
activation role asserts at entry that checkpointing is enabled when
auto-import management is requested and fails otherwise — the in-memory `set_fact`-only
mode is removed for this feature. This closes the checkpoint-disabled crash window
instead of documenting it; the decommission gate (§4) additionally fails closed whenever
required transaction evidence is absent.

#### Transaction record schema and validation

The persisted transaction record is versioned and validated as a whole before any
auto-import read, mutation, restore, or gate decision:

- `auto_import_schema_version: 1` is required on every new record. The only inputs
  accepted without it are the legacy `auto_import_strategy_set` boolean (migrated per
  the rules above) and the complete absence of all auto-import keys — auto-import
  management genuinely not requested, which is the only state the §4 gate passes as
  "no transaction".
- Required fields and shapes: `auto_import_txn_id` (non-empty string);
  `auto_import_txn` (exactly one of `intent`, `applied`, `ownership_conflict`);
  `auto_import_prior` (exactly one of the three §1 shapes — non-empty `uid` for
  `no_key`/`value`, `created_uid` null or a non-empty string for `absent` — or the
  legacy-migrated `{"state": "unknown_legacy"}` shape produced only by the migration
  rules above);
  `auto_import_conflict` (null or the complete sanitized object). When terminal
  restore evidence exists it must be one §2 table result carrying the matching
  `auto_import_txn_id`; when the §4 acknowledgement exists it must be the complete
  audited record (non-empty actor, timestamp, reason, both compared values) bound to
  the same id. Both fields are optional-but-shaped: absent is valid, present-and-
  incomplete is malformed, and present-with-a-different-`auto_import_txn_id` is the
  id-inconsistency case below.
- Minting a new `auto_import_txn_id` (§1 step 2) clears **both** carried-over terminal
  restore evidence and any carried-over §4 acknowledgement in the same durable update,
  so neither can be inherited by a later transaction. Independently of that reset, the
  §4 gate matches evidence by id, so a stale record surviving any path is an id
  mismatch that blocks rather than satisfies the gate.
- A non-null `created_uid` is valid only under the §1 create-response provenance
  rules; a non-legacy record whose `created_uid` state cannot have arisen under those
  rules and the §1 crash-window rules is malformed. The `unknown_legacy` prior never
  carries `uid` or `created_uid`; the `absent` prior may carry a null `created_uid`
  only in the documented crash-before-persistence window, where it keeps restore
  fail-closed.

A record that is malformed, partial, of unknown version, in an unknown state, or
internally inconsistent (id mismatch between intent, terminal result, or
acknowledgement) fails closed **before any mutation**: activation refuses to start or
continue the transaction, restore refuses to report any successful terminal result,
and the §4 decommission gate blocks. A malformed record is never treated as
"no transaction", never repaired by re-reading live cluster state, and never
re-captures the temporary live value as a new prior; recovery requires operator
inspection or the audited acknowledgement path.

### 2. Key-level restore (finalization)

Restore reads `auto_import_txn` + `auto_import_prior` and inverts exactly:

Before inverting, restore reads the live `autoImportStrategy` value and compares it with
the temporary value this run wrote (`ImportAndSync`). If it differs — an operator or
another actor changed it mid-switchover — restore preserves the live value and performs
no mutation; when the live value already equals the captured prior, the desired end
state already holds and the terminal result is `restored_noop` (table row "live value
already matches prior"), otherwise restore journals `restore_conflict` with both values.
The table rows are the authoritative contract; this pre-check narrative introduces them.

`restore_conflict` semantics are exact: it is a durable journaled outcome, terminal
only for this run's automatic restore attempt, and it is never a successful
restoration. After the conflict is journaled, the phase fails — Python raises
`SwitchoverError` and the collection fails the play (non-zero result). The failure
output explains the conflict (both values, stable reason), but no warning-level or
informational rendering converts the outcome into success. The conflict keeps
integrated decommission blocked (§4) and is superseded only by a later successful
terminal restoration or by the matching durable, audited operator acknowledgement
defined in §4; nothing else clears it.
For an `absent` prior, "already matches prior" means the ConfigMap itself is absent; a
present same-name ConfigMap never matches an absent prior merely because its key is
missing or has a familiar value.

| captured prior + live state | restore action | journal result |
| --- | --- | --- |
| `absent` with response-derived, durably persisted `created_uid`, live UID == `created_uid` | if live CM still matches the tool-owned shape (`data` is exactly our one key; no operator-added keys/labels/annotations beyond creation defaults) → delete CM with server-side UID precondition; otherwise patch removing only our key (operator content appeared post-creation — preserve it) | `restored_deleted` / `restored_key_removed` |
| `absent` with `created_uid`, live UID ≠ `created_uid` | no patch, no delete — replacement object, ownership not ours, live object preserved unchanged | `restore_conflict` |
| `absent` with `created_uid`, live CM absent | no-op — the tool-created object no longer exists (deleted by another actor or by an earlier interrupted restore); the captured prior already holds and nothing of ours can remain. No create, patch, delete, or retry | `restored_noop` |
| `absent` without `created_uid`, live CM absent | no-op (nothing exists; nothing of ours can remain) | `restored_noop` |
| `absent` without `created_uid`, live CM present | no patch, no delete — creation ownership unprovable, live object preserved unchanged | `restore_conflict` |
| `no_key` | patch removing only the `autoImportStrategy` key | `restored_key_removed` |
| `value X` | patch the key back to `X` | `restored_value` |
| any, live value already matches prior | no-op (this row takes precedence over every mutating row, including when the captured prior itself equals the temporary value) | `restored_noop` |
| any, live value ≠ our temporary value and ≠ prior | preserve live value, no mutation | `restore_conflict` |

Row precedence is explicit: the `restored_noop` row takes precedence over every
mutating row (as noted in the table), and the final `restore_conflict` row takes
precedence over the mutating rows — the `absent`/`no_key`/`value` inversion rows apply
only when the pre-check found the live key value still equal to the temporary value
this run wrote. An operator-edited value on a tool-created ConfigMap is therefore
preserved and journaled `restore_conflict` even though UID identity is proven.

Every patch path is UID-guarded, not only the delete: for `no_key` and `value` priors,
restore validates the live ConfigMap UID against the captured `uid`; for the `absent`
prior with `created_uid`, both the delete branch and the key-removal fallback first
require live UID == `created_uid` — the shape check selects between delete and key
removal only after UID identity is proven. The guard is server-enforced, not
read-then-patch: every apply/restore key patch is submitted as one RFC 6902 JSON Patch
whose first operation is a `test` against `/metadata/uid` with the captured UID, using
the same conditional-PATCH mechanism, rationale, and client basis as the migration
evidence design (§1b there); a failed `test` fails the whole atomic PATCH with no
mutation and journals `restore_conflict`. A client-side UID comparison alone detects,
but cannot prevent, a GET→PATCH replacement race, and is never sufficient. A UID
mismatch (deleted and recreated since capture) or a
missing object with prior `value X` → `restore_conflict`, no mutation. A missing object
with prior `no_key` → `restored_noop` (nothing of ours remains).

Ownership rules for the `absent`-without-`created_uid` conflict row (and every other
unproven-identity path): absence of `created_uid` means creation ownership is
unprovable; key removal is not safe merely because the live key currently equals
`ImportAndSync` — a replacement ConfigMap created by another actor can legitimately
carry that value; a same-name object must never be adopted as the transaction target;
the `restore_conflict` journaled here is a fail-closed ownership conflict that keeps
integrated decommission blocked (§4) and requires operator investigation, or an
explicit, separately designed acknowledgement path, before teardown.

- `intent` (crash before/during apply): re-read the live ConfigMap and apply the same
  inversion against the captured prior — if the live value never changed, journal
  `restored_noop`. For an absent prior with null `created_uid`, a live same-name object is
  the unproven-ownership conflict row even when its value is `ImportAndSync`; the read
  cannot establish ownership. If a response-derived `created_uid` is already durable,
  the read may only verify that exact UID.
- `ownership_conflict` never authorizes patch or delete. Finalization reports the stable
  recovery reason and remains fail-closed until the operator uses the separately designed
  acknowledgement path; acknowledgement may permit teardown but never retroactively
  establishes `created_uid` or authorizes mutation of the live same-name object.
- The delete uses a server-side UID precondition (`V1DeleteOptions.preconditions.uid` with
  the captured `created_uid`); `delete_configmap` gains an optional precondition
  parameter. Precondition mismatch (409/412) → leave the replacement ConfigMap intact,
  refuse the delete, journal `restore_conflict`. No read-before-delete race remains.
- Whole-ConfigMap deletion for a pre-existing ConfigMap is removed entirely.
- Restore failure raises `SwitchoverError` (Python) / `ansible.builtin.fail` (collection)
  — no warning-only path. When both `auto_import_txn` and the legacy boolean are absent,
  there is no transaction to restore. Any existing transaction with unproven ownership
  follows the explicit conflict rows above; it is never downgraded to a warning.
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
`ownership_conflict` and `restore_conflict` block too — a conflict means ownership or the
captured prior was *not* proved/restored;
proceeding past it requires the explicit operator acknowledgement path: the
flag/variable invocation is accepted only when it produces one durable audited journal
entry — non-empty actor, timestamp, reason, and the exact `auto_import_txn_id` it
acknowledges, naming both values — and the gate is satisfied by that recorded entry,
never by the invocation alone or by the conflict record itself. Pure state read — no
new cluster calls.
Standalone decommission (`--decommission`, typically a foreign/absent state file) is
unaffected. Collection: same check in the decommission role against checkpoint data when
present.

### 5. Dry-run / check mode

Consistent with the family-wide rule in the decommission and migration designs: Python
dry-run and native Ansible check mode perform the reads, normalization, and validation
above, predict the apply/restore outcome, and mutate nothing — no ConfigMap create,
patch, or delete, and no authoritative `intent`/`applied`/`ownership_conflict`/terminal
journal transition is persisted. A dry-run preview therefore never creates an open
transaction that a later real run or restore would treat as its own. Reporting is
exact: `changed: false` with prediction published separately (`would_change` in the
collection module result; the Python preview's planned-change summary). Execute mode
reports `changed: true` only after this invocation's create/patch/delete was accepted
and verified; a conflict or failure never reports a successful change.

## Testing

- Table test: 3 prior states × apply → restore, asserting exact patch/delete bodies and
  journal results; sibling keys asserted untouched.
- Crash-window: state has `intent` + prior and the captured prior still holds →
  `restored_noop`; a pre-existing, UID-proven target with our temporary value is inverted
  exactly, while an absent-prior live object without a durable response UID becomes the
  no-adoption ownership conflict.
- Absent-prior successful create: the typed result is `created`, its response carries a
  non-empty UID, that exact response UID is durably persisted before `applied`, and the
  later GET only verifies equality.
- Absent-prior create returns 409/`already_exists` → no patch, no ownership adoption,
  `created_uid` remains null, and `ownership_conflict` blocks restore/decommission.
- A helper/module result reporting `patched_or_mutated` instead of `created` on the
  absent path → fail closed with no ownership claim.
- A create conflict, unambiguous failure, or ambiguous request outcome → the exact stable
  conflict/failure reason, null `created_uid`, no later-GET ownership adoption, and no
  patch/delete authorization.
- Create response missing/empty/malformed UID → `created_uid` remains null; a later GET
  returning a UID cannot establish it; no patch/delete is authorized.
- Replacement appears after a successful create response: the response UID remains the
  immutable `created_uid`, later GET mismatch records `replacement_uid`, replacement is
  unchanged, and `applied` is not written.
- Crash after successful create but before UID persistence: resume sees null
  `created_uid`; a live same-name object (including exact `ImportAndSync`) is not adopted,
  is not patched/deleted, and produces `ownership_conflict`.
- Crash after response UID persistence but before `applied`: same UID may verify and
  continue; a different UID fails closed without rebinding.
- Legacy boolean migration path: live ConfigMap present → `restore_conflict`, object
  untouched, unknown-prior manual-recovery message; live ConfigMap absent →
  `restored_noop`; minted txn id carried either way.
- `data: null` for every reader (Python + both collection roles' Jinja).
- Delete precondition mismatch on `absent` prior → replacement intact, `restore_conflict`.
- `absent` prior with a durable `created_uid` and the tool-created ConfigMap externally
  deleted before finalization → `restored_noop` in both form factors, no create/patch/
  delete request issued, no retry loop, and the decommission gate passes on that
  terminal evidence.
- `absent` prior without `created_uid` (crash before UID record): live ConfigMap absent →
  `restored_noop`; live ConfigMap present with `autoImportStrategy: ImportAndSync` →
  `restore_conflict`, no patch, no delete; live ConfigMap present with any other value →
  `restore_conflict`, no patch, no delete; in every present case the live object is
  byte-identical afterwards and the decommission gate stays blocked without the
  durable audited acknowledgement record.
- Live value changed by operator mid-run → `restore_conflict`, live value preserved.
- Stale terminal evidence from an earlier `auto_import_txn_id` → gate still blocks.
- Decommission gate: blocks on `applied`-without-restore, `ownership_conflict`, and
  `restore_conflict`
  (passes only with the durable audited acknowledgement record bound to the same
  transaction id), passes on successful terminal journal,
  passes on no-transaction state; malformed, partial, unknown-version, or
  id-inconsistent transaction records block and are never read as no-transaction.
- Resume continuation: crash after mutation with `intent` open → rerun reuses the same
  txn id and prior (no re-capture of `ImportAndSync` as prior); legacy-migrated record
  passes the gate via its minted id.
- UID-guarded patch paths: recreated ConfigMap (new UID) with prior `no_key`/`value` →
  `restore_conflict`, untouched; `absent` prior with `created_uid` but live UID ≠
  `created_uid` (replacement, with or without operator-added keys) → `restore_conflict`,
  no patch, no delete; tool-created CM (live UID == `created_uid`) with operator-added
  keys → key removal instead of delete, UID identity asserted in the test.
- Collection parity tests for capture record shape and restore table.
- Cross-form-factor parity fixtures assert identical result categories, state transitions,
  no-adoption decisions, and reason codes while Python and the collection use independent
  create-only implementations.
- Changelog entry under `CHANGELOG.md` `## [Unreleased]` per the repository's Version
  Management policy. The implementation slice is ordinary development work and does not
  change released version identifiers or create a release tag; the synchronized
  Python/collection bump belongs to a later explicitly scoped release PR.

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
3. A ConfigMap created by the tool is deleted — or key-patched via the fallback — on
   restore only when its live UID matches a non-empty `created_uid` captured exclusively
   from the successful create response and durably persisted before `applied`.
4. `data: null` never surfaces as `AttributeError` in any implementation.
5. Integrated decommission refuses to run with an unrestored transaction, naming the fix
   (run finalization / restore manually).
6. Legacy state with `auto_import_strategy_set: true` never mutates a live same-name
   ConfigMap it cannot prove it touched: present → fail-closed `restore_conflict` with an
   explicit unknown-prior manual-recovery message; absent → `restored_noop`.
7. A 409/already-exists result, an unexpected patch result, a create response without a
   UID, a crash before UID persistence, or a later different-UID GET never establishes
   creation ownership. The same-name object is not adopted, patched, or deleted, and
   integrated decommission remains blocked by explicit ownership/recovery conflict
   evidence.
8. Python and the collection independently implement the same create-only outcome,
   durable-state, resume, restore, and decommission-gate contract; neither uses
   `create_or_patch_configmap`/generic `state=present` as proof of creation.
