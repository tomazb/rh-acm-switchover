# ArgoCD Pause/Resume Correctness Residuals — Design

**Date:** 2026-07-29
**Branch:** `ansible` (spec branch `docs/thermos-safety-specs`)
**Status:** approved design, awaiting implementation plan
**Origin:** cross-validation of main-branch safety specs against `ansible` @ `0bf55db9`, independently revalidated by a second reviewer (Codex). Only issues confirmed open on `ansible` and untracked in `thermos-resolution-plan.md` are in scope.

## Problem

Four residual defects survive the earlier ArgoCD fixes on this branch (`automated: null`
pause patch, post-pause re-read, resume resourceVersion precondition, marker-mismatch skip):

1. **Bash pause is a silent no-op.** `scripts/argocd-manage.sh:341` builds the pause patch
   with `jq 'del(.automated)'` and sends it with `oc patch --type=merge` (`:345`). Under
   RFC 7396 merge-patch semantics, omitting a key leaves it unchanged — auto-sync stays
   enabled. The script then prints `Paused ...` (`:353-356`) and journals the Application as
   paused (`:364-366`). Python (`lib/argocd.py:678-684`, `automated = None`) and the
   collection (`roles/argocd_manage/tasks/pause.yml:49-53`, `combine({'automated': none})`)
   are correct; the Bash path is divergent and lies about safety-critical state.
2. **Auto-sync classification is incomplete and fails open on malformed values.**
   `automated.enabled: false` is classified as active everywhere: Python
   `lib/argocd.py:423-426` (any non-null `automated` object), Bash
   `scripts/argocd-manage.sh:326-330`, collection `pause.yml:58-61`. Argo CD ≥2.13 keeps the
   `automated` object with `enabled: false` when auto-sync is toggled off, so an
   already-disabled Application gets patched, journaled, and "restored" for no reason.
   The same truthiness/coercion paths also fail to distinguish arrays, scalars, invalid
   nested values, and structurally unreadable responses from valid absent, disabled, or
   active state. A malformed value can therefore be skipped, patched, or journaled as if
   it were trustworthy instead of blocking destructive work.
3. **Resume overwrites with stale values and never verifies.** All three implementations
   send the entire stored `syncPolicy` snapshot back (`lib/argocd.py:809-897`,
   `resume.yml:55-136`, `argocd-manage.sh:430-449`). A merge patch does not delete
   concurrently added sibling keys, but it does overwrite any pre-existing key an operator
   changed mid-switchover with the stale stored value. No implementation re-reads the
   Application after resume to verify `automated` is restored and the marker is gone. Bash
   additionally lacks the resourceVersion precondition Python and the collection have.
   (Collection OCC parity is separately tracked as `TR2D-02`; that row does not cover
   post-resume verification or the stale-overwrite shape.)
4. **No destructive-phase gates.** The pause step is checkpointed
   (`modules/primary_prep.py:71-81`, `state.step(STEP_PAUSE_ARGOCD_APPS, ...)`), so a run
   resumed at ACTIVATION never re-examines ArgoCD state (`acm_switchover.py:802-825`,
   `lib/operation_runners.py:139-165`). Integrated decommission
   (`modules/finalization.py:1125-1149`) performs no ArgoCD revalidation. Between crash and
   resume, an operator or controller can re-enable auto-sync and the tool proceeds into
   activation/teardown with GitOps actively fighting it.

## Goals

1. The Bash pause patch actually disables auto-sync, and the script fails loudly when it
   does not.
2. One exhaustive, fail-closed auto-sync classification rule across Python, collection,
   and Bash, including `enabled: false`, Argo's documented active-equivalent
   `enabled: null`, and every malformed top-level or nested shape.
3. Resume restores exactly what pause removed — nothing else — and proves it.
4. Destructive phases revalidate journaled pause state before proceeding.

## Non-goals

- Bash script lifecycle (deprecation/deletion, state identity, permissions, locks, JSON
  Patch, RV preconditions in Bash): owned by planned slice `SSA-05`. This spec makes the
  minimal correctness fix only, so the script tells the truth for as long as it exists.
- Global rediscovery gates for arbitrary Applications: planned `R3-10a` is narrowing
  exactly that blast radius. This spec stays journal-scoped after pause discovery, but
  every Application that enters the managed pause/classification path receives the
  exhaustive classification below; an `UNKNOWN` result is durably blocking evidence,
  never an unjournaled success or skip.
- Collection resume OCC outcome parity: tracked by `TR2D-02`.
- Foreign-marker override policy changes (current mismatch → skip behavior is kept).

## Design

### 1. Bash pause fix (minimal)

- `scripts/argocd-manage.sh` pause: build the patch with `jq '.automated = null'` (explicit
  null) instead of `del(.automated)`; keep `--type=merge`.
- After patching, re-read the Application (`oc get ... -o json`) and verify the
  **paused shape**: `spec.syncPolicy.automated` absent or JSON `null`. (RFC 7396 deletes
  the key when patched with `null`, so the persisted resource normally has the key absent
  — requiring a literal `null` would be unsatisfiable.) Merely "inactive" per the §2
  classification is not enough — another actor flipping the object to
  `{"enabled": false}` between patch and re-read must not be journaled as our successful
  pause: any object value fails. On failure: print it for that Application, do NOT
  journal it as paused, and exit non-zero after processing remaining Applications
  (consistent with the existing per-app error accumulation). The same paused-shape check
  applies to the Python and collection verifies.
- Patch-succeeded-but-verify-unreadable is not a discard: when the patch call returned
  success but the post-patch read fails, the Application may already be paused. All three
  implementations journal the entry as `verify_pending` (state written immediately, even
  though the overall run fails) so resume and the destructive-phase gates know a
  restoration obligation may exist; gates treat `verify_pending` as blocking until an
  operator or rerun re-reads and settles it.
- Concurrency boundary, stated exactly: each pause/resume mutation is one merge-patch
  request, atomic only at the single-request level. Bash has no resourceVersion
  precondition, JSON-Patch `test` operations, or other compare-and-swap mechanism, so
  its post-patch re-read **detects** a concurrent overwrite but cannot **prevent**
  every interleaving; Python and the collection narrow (not eliminate) the same window
  with their resourceVersion preconditions. Closing the remaining prevention gap in
  Bash stays `SSA-05` scope unless a later operator decision moves it; nothing in this
  design or `R4-01` claims Bash OCC parity.
- The only other Bash changes are the shared classification expression (§2) and the resume
  shape + verification (§3) — all correctness fixes to existing code paths. Everything else
  about the script is `SSA-05` scope.

### 1a. Mutation/journal durability boundary

`verify_pending` above covers a mutation whose *verification read* failed. It does not
cover a mutation whose *journal write* failed: a successful pause leaves auto-sync
disabled with no durable record at all, and a successful resume clears the marker while
the entry stays non-terminal — after which §4's paused-entry check reads the missing
marker as tampering and blocks every destructive gate permanently. Both pause and resume
therefore run under one explicit durable state machine, and the guarantee is stated
precisely: **no accepted mutation is untracked, and no failed evidence write is
unrecoverable.**

Each mutation carries an immutable `operation` record, minted once and never re-minted on
retry:

| field | meaning |
| --- | --- |
| `operation_id` | immutable unique id for this pause or resume attempt |
| `kind` | `pause` or `resume` |
| `namespace`, `name` | Application identity |
| `expected_uid` | the Application UID observed at read time; a different live UID is a replacement, never adopted |
| `expected_resource_version` | the resourceVersion the precondition was built from (Python and the collection; absent for Bash, which has none — §1) |
| `owned_marker` | the exact pause-marker annotation value **this operation** writes or clears, fixed at mint time |
| `owned_transition` | the intended `automated` transition: for `pause`, canonical `ACTIVE` payload → paused shape; for `resume`, paused shape → that same canonical payload |
| `state` | one of the states below |

`owned_marker` binds the operation to its own marker rather than to whatever the journal
entry happens to hold later. It is derived from the operation's intended marker at mint
time, is **immutable across retries**, and every reconciliation and settlement step
requires an **exact** match against it. A marker that differs — a foreign run's marker, or
a marker rewritten between attempts — is never accepted as ours, so reconciliation can
never settle an operation against another actor's pause. The journal entry's `run_marker`
(§2a) must equal the `owned_marker` of the operation that produced it; a disagreement is
`recovery_required`.

States, in order: `intent_recorded` → `mutation_accepted` → `verify_pending` →
`verified` → `settled`, with `recovery_required` reachable from any of them.

Rules:

- **No mutation before durable intent.** `intent_recorded` — the complete operation record
  including `owned_transition` — is written and forced durable before the patch request is
  submitted. This is what makes a crashed mutation reconcilable rather than invisible.
- **A failed intent persistence means no mutation.** The patch is not sent; the step fails
  closed with nothing changed on the cluster.
- **Mutation accepted, evidence write failed → recoverable, not lost.** The durable
  `intent_recorded` record already names the Application, the expected identity, and the
  intended transition, so a resume or rerun can reconcile. A failure to advance the record
  past `intent_recorded` never converts into a successful pause/resume result: the run
  returns fatal/non-zero.
- **Reconciliation re-reads live state and compares it to the recorded intent**, never the
  reverse. For a `pause` intent: paused shape plus our marker and the expected UID →
  advance to `verified` and write the §2a entry, whose `restore_payload` is the
  `owned_transition` payload recorded *before* the mutation (the live paused Application no
  longer carries it, which is exactly why intent-first ordering is required). For a
  `resume` intent: `automated` deep-equal to the recorded payload and marker absent →
  advance to `verified`, then `settled` when the journal entry is durably `resumed`.
- **Replacement identity is never adopted, and identity is checked on every live read.**
  `metadata.uid` is compared against `operation.expected_uid` at *every* point the
  Application is read — before resume mutates, in the post-resume verification read, in
  every destructive-gate read, and in every reconciliation read. A difference is
  `recovery_required` with no mutation and no success record, regardless of how well the
  observed value matches. Shape and marker checks alone are insufficient: a same-name
  replacement can carry a copied marker and the expected shape, and would otherwise pass.
- **Ambiguous transport or verification results fail closed** to `verify_pending` (read
  ambiguity) or `recovery_required` (state ambiguity). Neither is terminal and neither
  satisfies a gate.
- **Completion is recorded only after live verification.** `settled` — and the `resumed`
  journal state — is written only after the post-mutation re-read and the §3 deep-equality
  and marker checks have passed.
- **Partial or malformed operation state blocks all destructive gates**, exactly like the
  §2a `recovery_required` state it shares.
- **Resume-side marker absence is disambiguated by the record, not guessed.** An entry
  whose operation record is `mutation_accepted`/`verify_pending` for `kind: resume`
  explains a missing marker as our own accepted resume awaiting settlement; the gate
  reconciles it rather than reporting tampering. A missing marker with no such record
  remains the journal/cluster disagreement of §4.

Bash parity is required for the record contents, ordering, states, and reconciliation
decisions. Bash still has no resourceVersion precondition or compare-and-swap, so it
records `expected_resource_version` as absent and its intent-first ordering narrows but
does not eliminate the interleaving window described in §1. Nothing here claims Bash OCC
parity.

### 2. Shared auto-sync classification

Five-outcome rule, identical in `lib/argocd.py` (`is_autosync_enabled` → new
`classify_autosync`), `plugins/module_utils/argocd.py`, the collection pause pre-task, and
the Bash `jq` classifier:

| `spec.syncPolicy.automated` shape | classification | pause action | journal |
| --- | --- | --- | --- |
| key absent under a readable `spec` and absent/null/readable-object `syncPolicy` | `INACTIVE_ABSENT` | skip | not journaled (unchanged behavior) |
| `null` | `INACTIVE_NULL` | skip | not journaled (unchanged behavior) |
| schema-valid object with `enabled: false` | `INACTIVE_DISABLED` | skip | journaled as `skipped_disabled` with the observed object |
| valid/normalized-active object with `enabled` absent, `null`, or `true` (including `{}`) | `ACTIVE` | pause | journaled as paused with the exact original object |
| array; string, numeric, or boolean scalar; object with an invalid member; unreadable response; or structurally invalid parent data | `UNKNOWN` | error; no pause mutation | blocking `classification_unknown` record containing only sanitized identity and a stable reason code |

For an object to be schema-valid, its keys are a subset of `enabled`, `prune`,
`selfHeal`, and `allowEmpty`; `prune`, `selfHeal`, and `allowEmpty`, when present, are
booleans. The Application CRD declares `enabled` as a boolean without
`nullable: true`; Kubernetes prunes a null supplied for a non-nullable CRD field before
defaulting/persistence, making it equivalent to the member being absent. Argo CD also
documents `enabled: null` as enabled. Therefore a classifier that encounters
`enabled: null` in decoded/unstructured input treats it as normalized-active `ACTIVE`,
not malformed or disabled; any **non-null** non-boolean `enabled` value is `UNKNOWN`.
An object containing a string, number, array, object, or null where `prune`,
`selfHeal`, or `allowEmpty` requires a boolean is also `UNKNOWN`. Unknown keys or
malformed nested data are `UNKNOWN`; implementations do not rely on API-server pruning
to reinterpret a response already received by the tool.
An absent/null `syncPolicy` is the valid field-absent case, but a non-mapping `spec` or a
non-null non-mapping `syncPolicy` is structurally invalid and therefore `UNKNOWN`.

Canonicalization before journaling: because RFC 7396 merge patch deletes a member sent
as `null`, an `ACTIVE` object journaled verbatim as `{"enabled": null}` could never be
restored byte-identically — resume would send it and the persisted object would come
back as `{}`, failing §3's deep-equality check on a correct restore. Implementations
therefore journal the **canonical** `ACTIVE` object: the classification is performed on
the observed value, and a null-valued `enabled` member is dropped before the object is
stored (no other member is added, removed, reordered in meaning, or defaulted). The
canonical form is what §3 sends and what §3 compares against, so an `enabled: null`
input and an `enabled`-absent input journal and restore identically. This changes no
classification: `enabled: null` remains `ACTIVE`, and any non-null non-boolean `enabled`
remains `UNKNOWN` and is never canonicalized or journaled as active.

These semantics are grounded in the Argo CD
[automated-sync documentation][argocd-auto-sync] and the current
[Application CRD schema][argocd-application-crd], together with Kubernetes'
[CRD nullable/defaulting rules][kubernetes-crd-nullable]. In particular, the
documentation explicitly defines `enabled: null` as enabled and Kubernetes explains its
non-nullable normalization; the implementations must not infer that case from language
truthiness.

`skipped_disabled` entries are informational: resume must never patch them, and the
summary reports them distinctly. Rationale: auto-sync is already off; patching adds churn
and a restore obligation with no safety benefit. The stored observed object lets a future
operator audit what was seen. `classification_unknown` is never a successful
paused/skipped/resumed state: it is persisted before returning a fatal/non-zero result so
ACTIVATION, FINALIZATION, and every destructive-phase gate remain blocked across resume.
It contains the namespace/name when those fields are valid (otherwise a stable
non-sensitive discovery index), the reason code, and no raw malformed value, full
Application body, status payload, annotation value, or other potentially sensitive data.

[argocd-auto-sync]: https://argo-cd.readthedocs.io/en/stable/user-guide/auto_sync/
[argocd-application-crd]: https://github.com/argoproj/argo-cd/blob/stable/manifests/crds/application-crd.yaml
[kubernetes-crd-nullable]: https://kubernetes.io/docs/tasks/extend-kubernetes/custom-resources/custom-resource-definitions/#defaulting-and-nullable

### 2a. Versioned journal schema and strict validation

The pause journal is a versioned durable record, and **no gate, resume, settlement, or
mutation reads it without validating it first**. Validation is a whole-record schema
check, not a field lookup: a record that does not validate is never partially trusted.

`argocd_journal_schema_version: 2` is required on every record written by this design.
Version `1` is the legacy shape defined in §2b and is accepted only through the migration
contract there. Any other version — absent on a non-legacy record, unrecognized, or
non-integer — is unknown-version and blocks.

Per-entry required fields and shapes:

| field | shape |
| --- | --- |
| `argocd_journal_schema_version` | integer `2` |
| `namespace`, `name` | non-empty strings (or, when identity itself was malformed at discovery, the stable non-sensitive discovery index used by `classification_unknown`) |
| `state` | exactly one of `paused`, `verify_pending`, `skipped_disabled`, `resumed`, `classification_unknown`, `recovery_required` |
| `run_marker` | the exact pause-marker annotation value this run writes — the run identity gates compare against |
| `restore_payload` | the canonical `ACTIVE` object of §2 (see rules below) |
| `observed_value` | present only on `skipped_disabled` and `classification_unknown`; the sanitized observed object or stable reason code |
| `operation` | the §1a durable operation record |

`restore_payload` rules — this is the object a resume would send back, so it carries the
whole restoration obligation:

- It is **required and must be a schema-valid canonical `ACTIVE` object** for `paused`,
  `verify_pending`, and `resumed` entries.
- It is the **tool-owned `automated` object only**. It is never a full `syncPolicy`, never
  a partial or reconstructed `spec`, and never contains sibling sync-policy keys. A
  payload carrying anything other than the canonical `automated` object is structurally
  impossible under this schema and is rejected.
- Its keys are a subset of `enabled`, `prune`, `selfHeal`, `allowEmpty` with the §2 value
  types, and a null-valued `enabled` has already been dropped at journaling time. An
  `enabled: null` member surviving in a stored payload is a canonicalization violation and
  is rejected, not re-canonicalized on read.
- It is **absent** on `skipped_disabled` and `classification_unknown` entries (those never
  incur a restoration obligation), and its presence there is malformed.

Validation points are exhaustive and identical in all three form factors: every
destructive-phase gate pass (§4), every resume eligibility check and every resume patch
(§3), every `verify_pending` settlement, and every journal state transition. Each of them
validates the complete record **before** evaluating live cluster state and **before** any
mutation.

A record that is missing, malformed, partial, of unknown version, in an unknown state, or
structurally impossible transitions to the durable blocking state `recovery_required` and
produces a fatal/non-zero result. `recovery_required` is never a successful
paused/skipped/resumed state, never satisfies a gate, and is never repaired by re-reading
live cluster state — in particular a live paused Application is never used to infer or
reconstruct a restore payload, because the live paused shape by definition no longer
contains the value that must be restored. It is cleared only by an operator or by a rerun
of the normal pause flow durably replacing the evidence. Its diagnostics carry only the
sanitized namespace/name (or discovery index), the schema version observed, and a stable
reason code — never the malformed payload, the Application body, or annotation values.

Parity: Python, the collection, and Bash each own an independent implementation of this
validator, and the schema version, field names, state names, stable reason codes,
accept/reject decisions, and sanitized output are parity fixtures.

### 2b. Legacy journal migration (schema version 1)

Two current pause paths store the **full** `spec.syncPolicy` rather than the canonical
`automated` object. The collection writes it into the
`acm-switchover.argoproj.io/original-sync-policy` annotation
(`ansible_collections/tomazb/acm_switchover/roles/argocd_manage/tasks/pause.yml:46-47`)
and rebuilds `syncPolicy` from that snapshot on resume (`resume.yml:71`); the Bash script
writes it into its state file as `original_sync_policy`
(`scripts/argocd-manage.sh:325-326,336-337,355-356`) and sends the whole stored object
back on resume (`:432-433`). An Application paused by **either** implementation before
this slice ships therefore carries a legacy restore value that the §2a schema rejects.
Upgrading must not strand it, and must not send it back blindly.

Legacy identification is explicit, never inferred from a parse failure: a record is
schema version `1` when it carries no `argocd_journal_schema_version` **and** its stored
restore value is a mapping whose keys are sync-policy keys rather than the canonical
`automated` members. Anything else lacking a recognized version is unknown-version per
§2a, not legacy.

Migration is a read-only conversion followed by one durable write, performed **before**
any resume mutation and before any gate accepts the entry:

1. Extract **only** the legacy `automated` member. Every sibling `syncPolicy` key
   (`syncOptions`, `retry`, and any other) is discarded: pause never removed them, so
   resume must never restore them. A legacy snapshot with no `automated` member, or with
   a non-mapping `syncPolicy`, is not migratable.
2. Classify the extracted member through the current strict §2 classifier. Only an
   `ACTIVE` result is migratable. `INACTIVE_ABSENT`, `INACTIVE_NULL`, `INACTIVE_DISABLED`,
   and `UNKNOWN` are not: an entry recorded as paused whose stored policy was never active
   is an impossible legacy record.
3. Canonicalize exactly as §2 requires — a null-valued `enabled` is dropped — so the
   migrated payload is byte-comparable with a natively written one.
4. Bind the migrated record to the existing Application identity and ownership marker:
   the live Application must still carry the legacy pause marker for that run. A missing,
   replaced, or foreign marker means the entry is no longer ours to restore.
5. Durably write the complete schema-version-2 record — including `restore_payload`,
   `run_marker`, and a §2b-minted `operation` record in state `verified` reflecting the
   already-completed legacy pause — **before** any resume patch is sent. The migrated
   record, not the legacy annotation, is what resume reads.
6. Clear the legacy `original-sync-policy` annotation only in the same resume patch that
   clears the pause marker, and only after a verified restore, so a failure between
   migration and resume leaves the legacy evidence intact for a retry.

Every non-migratable legacy record — malformed, ambiguous, missing `automated`,
non-`ACTIVE`, marker-missing, marker-replaced, or bound to a replacement Application
identity — becomes `recovery_required` per §2a. It is fatal, blocks every destructive
gate, and its message names the Application and the stable legacy reason code. In no case
is a restoration payload inferred from the live paused Application: the live value is the
paused shape, so inferring from it would silently "restore" auto-sync to absent and
permanently disable it.

**Two implementations wrote the legacy shape, not one.** Besides the collection, the Bash
script also stores the full `spec.syncPolicy` — `scripts/argocd-manage.sh:325-326` captures
`.spec.syncPolicy` into `original_sync_policy`, `:336-337` and `:355-356` journal it into
the state file, and resume at `:432-433` sends that whole stored object back. Bash
therefore needs the **same migration or recovery path** as the collection, not a
foreign-record rejection.

Form-factor ownership of §2b is consequently:

| implementation | legacy records it wrote | §2b role |
| --- | --- | --- |
| collection | full `spec.syncPolicy` in the `original-sync-policy` annotation (`pause.yml:46-47`) | owns conversion of its own legacy records |
| Bash | full `spec.syncPolicy` in the state file's `original_sync_policy` (`argocd-manage.sh:325-326,336-337,355-356`) | owns conversion of its own legacy records, by the identical rules |
| Python | none — never wrote a full-`syncPolicy` restore value | validator only: recognizes a legacy record and rejects it as foreign |

Bash's conversion follows steps 1-6 above unchanged, reading `original_sync_policy` from
its state file rather than an annotation, and writing the migrated schema-version-2 record
back to that state file before any resume patch. A Bash legacy record that is not
migratable is `recovery_required` exactly as elsewhere. Because Bash has no
resourceVersion precondition (§1), its migrated `operation` record carries no
`expected_resource_version`; the `expected_uid` and `owned_marker` bindings still apply.

All three implementations share one accept/reject decision table, and the Bash resume
suite covers the legacy state explicitly — a stored full `syncPolicy` with sibling
`syncOptions`/`retry` keys must migrate to the canonical `automated`-only payload and
produce a resume patch containing no sibling key, and every non-migratable legacy shape
must fail closed with no patch sent.

### 3. Resume shape and verification

- Patch body becomes `{"spec": {"syncPolicy": {"automated": <stored original>}}}` plus a
  merge-patch entry that nulls only this run's marker-annotation key — neither a
  reconstructed `metadata.annotations` map nor sibling annotation keys are sent. Only the
  sync-policy key pause removed is restored, and sibling `syncPolicy` keys are never sent.
  Applies to Python (`lib/argocd.py`), collection (`resume.yml`), and Bash.
- The "stored original" throughout this section is the canonical `ACTIVE` object defined
  in §2 (null-valued `enabled` already dropped at journaling time), so both the patch
  body and the deep-equality comparison use that one canonical representation.
- Before resume mutates, it validates the complete journal record against the §2a schema
  (a legacy record must already have been migrated per §2b), that the stored original is
  an exact schema-valid canonical `ACTIVE` object carrying only the tool-owned `automated`
  members, that the live field has the exact paused shape (absent or `null`), and
  that the live pause marker exactly equals the current journaled run identity immediately
  before the patch. A malformed stored value, an `UNKNOWN` live value, any live object —
  including valid `{"enabled": false}` — or a missing/replaced marker is a fail-closed
  journal/live disagreement. No resume patch is sent and no successful `resumed` state is
  recorded.
- Keep the existing resourceVersion precondition in Python and the collection (Bash stays
  without one — SSA-05 scope).
- After a successful patch, re-read the Application and verify: (a) `automated` is
  deep-equal to the stored original — including when the original was `{}`; a different
  active object (e.g. `{"enabled": true}` or a prune-only object) is a verification
  failure, with no normalization — (b) the restored value still classifies as
  schema-valid `ACTIVE`, never `UNKNOWN` — and (c) the pause marker annotation is absent.
  On mismatch: per-app failure in the summary, overall
  non-zero result (`SwitchoverError` / `ansible.builtin.fail` / exit ≠ 0). One re-read, no
  polling loop — controller drift after a verified restore is the operator's normal GitOps
  state, not a switchover concern.

### 4. Journal-scoped destructive-phase gates

New helper `revalidate_argocd_pause_journal(...)` in `lib/argocd_coordinator.py` (Python)
and an equivalent pre-task include in the collection:

- Input: the persisted pause journal (state key `argocd_paused_apps` / collection
  checkpoint equivalent). If the journal is empty or absent, the gate passes trivially.
- **Stored-record validation precedes every live read.** Before a gate evaluates any
  entry's cluster state, it validates that entry against the §2a schema — including that
  a `paused`, `verify_pending`, or `resumed` entry carries a present, schema-valid
  canonical `ACTIVE` `restore_payload` limited to the tool-owned `automated` object, and
  that a legacy (schema version 1) record has been migrated per §2b. A missing, malformed,
  partial, unknown-version, non-`ACTIVE`, or structurally impossible stored payload is
  `recovery_required`: the gate fails closed there, before ACTIVATION or FINALIZATION
  proceeds. This closes the window in which a malformed journal entry passed the
  destructive gates and was only rejected later by §3's resume-time validation, stranding
  the Application paused with auto-sync disabled and no path forward.
- Validation is per entry and does not short-circuit: every entry is validated and every
  failure is reported together, so one bad record does not hide another.
- Journal entry states and gate treatment: `paused` and `verify_pending` are non-terminal;
  `resumed` is the only terminal state and is skipped by gates. `skipped_disabled` is
  informational, never terminal, and is always re-read (below).
  `recovery_required` (§2a/§2b/§1a) is a blocking error state and is never terminal.
  `classification_unknown` is a blocking error state: every gate performs a fresh,
  schema-aware read for a sanitized diagnosis, but the gate is read-only: it never
  promotes the entry directly to paused/skipped/resumed. Even when the object has since
  become valid, the entry remains blocking until the operator or a rerun re-enters the
  normal pause flow and durably replaces the error evidence. No destructive mutation is
  permitted while the entry remains unresolved.
- `verify_pending` entries block every gate until settled, and **reconciliation is
  selected by `operation.kind`, because pause and resume expect opposite live states**.
  In both cases the live UID must equal `operation.expected_uid` and the observed marker
  must match `operation.owned_marker` exactly where a marker is expected at all:
  - `kind: pause` (pause patch succeeded but the post-patch read failed, §1): the gate
    re-reads the Application; paused shape (`automated` absent/null) **with** our marker →
    promote to `paused` (durably journaled) and apply the normal `paused` checks; any
    other observed state → fail closed; read error → fail closed, entry stays
    `verify_pending`.
  - `kind: resume` (resume patch accepted but the journal update or verification read
    failed, §1a): the expected state is the inverse — `automated` deep-equal to the
    operation's `owned_transition` payload **and the marker absent**. That combination →
    settle the operation to `verified`/`settled` and the journal entry to `resumed`
    (durably written); a still-paused shape with our marker → the resume did not take
    effect and the entry reverts to `paused` for a retry; any other state, a foreign or
    unexpected marker, a UID mismatch, or a read error → fail closed, entry stays
    `verify_pending`.
  A resume-side entry is therefore never misread as marker tampering merely because the
  marker is gone, which is exactly the state a successful resume produces.
- For each journaled *paused* entry: GET the Application. Failure modes, each fail-closed:
  - read error (incl. 404) → `SwitchoverError` naming the app and error;
  - marker annotation missing or not this run's identity → fail (journal/cluster
    disagreement — someone else touched it);
  - malformed `automated`, `spec`, or `syncPolicy` data → `UNKNOWN`, fatal, no journal
    success transition;
  - `automated` is any object value — the paused-shape check, not the §2 classification:
    an operator changing the tool-written absent/null to `{"enabled": false}` while
    leaving the marker intact must fail too, because resume would overwrite that operator
    change with the stored active policy and re-enable auto-sync.
- `skipped_disabled` entries are always re-read and re-classified, on every gate pass and
  regardless of any terminal filtering; if now active → fail with a message that auto-sync
  was enabled mid-switchover on a previously disabled app. Read errors (incl. 404) and
  `UNKNOWN`/malformed responses on these re-reads fail closed exactly like `paused`
  entries — an unreadable previously-disabled app is never treated as still-inactive.
- Failure message includes per-app `oc get application.argoproj.io -n <ns> <name> -o
  jsonpath=...` inspection commands and the choice: re-pause (re-run primary-prep) or
  investigate/override.
- Call sites (both un-checkpointed, executed on every pass):
  1. ACTIVATION entry, on every pass — including fresh runs that paused in-process. A
     controller or operator can re-enable auto-sync between the pause and activation within
     the same run; the gate is cheap (one GET per journaled Application) and closes that
     window too;
  2. FINALIZATION phase entry, before the phase's first mutation, when `--argocd-manage`
     was active for the run — not merely before `_decommission_old_hub()`; earlier
     finalization steps already mutate (backup enablement, old-hub handling), and the gate
     must precede all of them.
- Gate/resume ordering: gates skip only `resumed`-terminal entries (per the state table
  above — `skipped_disabled` is still re-read). When `--argocd-resume-after-switchover`
  resumes an Application, its journal entry is
  marked terminal (`resumed`) **only after** the resume patch, the deep-equality
  restoration check, and the marker-absence check have all passed and the journal update
  is durably written; a failed or unverified restore keeps the entry non-terminal, so
  subsequent gates still block on it. With that, the marker is legitimately gone for
  terminal entries, and gate passes skip them instead of reading the missing marker as
  tampering. The
  finalization gate therefore runs before any resume step in the same phase.
- No gate/resume deadlock exists, because "non-terminal" means re-checked, not
  automatically blocking. A healthy `paused` entry — our marker intact and the live
  `automated` in the paused shape — passes the gate; only the enumerated disagreements
  above block. A `verify_pending` entry is settled **by the gate's own re-read**, not by
  the resume step: a confirmed paused shape with our marker promotes it to `paused`
  in a durable journal update and it then passes the normal `paused` checks. So a run
  that paused successfully reaches `--argocd-resume-after-switchover` normally, and
  resume is never a prerequisite for passing the gate that precedes it. The states that
  do block — an unreadable or disagreeing entry, and `classification_unknown` — are
  states resume could not settle either, since resume itself requires a successful read
  and a valid stored `ACTIVE` value; blocking there is the intended fail-closed
  outcome and is cleared by operator action or a rerun of the normal pause flow.
- Dry-run: gates execute read-only and log what would block (consistent with the `F40`
  resolution that dry-run performs real discovery).

### 5. Cross-form-factor parity matrix

| Contract | Python | Collection | Bash |
| --- | --- | --- | --- |
| Exhaustive §2 classifier, including `enabled: null` and all `UNKNOWN` shapes | typed shared helper | collection-owned parity helper used before Jinja mutation tasks | `jq -e` classifier with explicit type checks |
| `UNKNOWN` outcome | fatal, sanitized blocking journal entry, no patch | failed task, sanitized blocking checkpoint entry, no patch | non-zero, sanitized blocking state entry, no patch |
| §2a schema validation before every gate, resume, settlement, and mutation | typed record validator | collection-owned record validator | `jq -e` record validator |
| §2b legacy (version 1) record handling | recognize and reject as foreign (never wrote the shape) | recognize, convert from the annotation, durably rewrite before resume | recognize, convert from the state file's `original_sync_policy`, durably rewrite before resume |
| §1a durable operation record and reconciliation | full state machine with RV precondition | full state machine with RV precondition | full state machine, `expected_resource_version` absent (no OCC — §1) |
| `recovery_required` outcome | fatal, blocks all destructive gates, sanitized | failed task, blocks all destructive gates, sanitized | non-zero, blocks all destructive gates, sanitized |
| Pause success | exact absent/null verification before `paused` | same | same |
| Resume eligibility and success | valid stored `ACTIVE`; live paused shape; exact post-read | same | same |
| Destructive gates | ACTIVATION and FINALIZATION plus integrated teardown | same phase entries | any destructive workflow consuming Bash state; malformed state never reports success |

The three implementations are independent and do not import one another, but the
classification vectors, stable reason codes, journal state names, mutation/no-mutation
decisions, fatal status, and sanitized public identity are parity fixtures.

## Error handling summary

Every new failure path is fail-closed and identifies the affected Applications using only
sanitized namespace/name (or a stable discovery index when identity itself is malformed):
Bash exit ≠ 0 with per-app lines; Python `SwitchoverError` aggregating failed apps;
collection `ansible.builtin.fail` with the same list. The malformed value and full
resource are never echoed. Classification failure performs no pause/resume mutation,
writes no successful paused/skipped/resumed state, blocks activation/finalization/all
destructive gates, and returns fatal/non-zero. No new warning-only paths.

## Testing

- **Python** (`tests/test_argocd.py`, coordinator tests): patch-body assertions
  (`automated` key present and null on pause; resume body contains only `automated` +
  this run's marker removal, and preserves a concurrently added sibling annotation);
  classification table tests over every valid shape (absent, top-level
  null, object with `enabled` absent/null/true/false); canonicalization tests proving an
  `enabled: null` input and an `enabled`-absent input journal the identical canonical
  object, that resume of that canonical object passes the deep-equality check against
  the merge-patch result, and that no `UNKNOWN` value is ever canonicalized and every malformed category
  (array; string, numeric, and boolean scalar; invalid `enabled`; invalid
  `prune`/`selfHeal`/`allowEmpty`; unknown/malformed nested member; non-mapping parent;
  unreadable/structurally invalid response); resume verification
  success/mismatch; gate tests — marker stolen, auto-sync re-enabled, read error, 404,
  empty journal, `skipped_disabled` re-enabled, `classification_unknown`, clean pass;
  every malformed case asserts fatal/non-zero, no pause/resume mutation, no successful
  paused/skipped/resumed journal state, and a sanitized affected-Application identity;
  `verify_pending` promotion to
  `paused` on confirmed paused shape, blocking on any other state or read error;
  paused-entry gate failure on `{"enabled": false}` with intact marker; ACTIVATION
  call-site tests:
  restored-from-state resume AND same-run re-enable after an in-process pause both blocked.
- **Collection**: parity tests extended with the same complete valid/malformed
  classification table and resume
  shape; new-assertion oracles must load the real `pause.yml`/`resume.yml` values (do not
  extend the hand-written oracle pattern flagged by `R3-T3`).
- **Bash** (`tests/test_argocd_manage_script.py`): mock `oc` upgraded to capture and parse
  the `-p` payload; assert `"automated":null` present on pause. This closes the
  accept-any-patch blind spot that let defect 1 ship. Read-failure coverage is split to
  match §1: a pre-patch or patch failure exits non-zero and journals nothing; a successful
  patch followed by a failed post-patch read exits non-zero, does NOT journal the
  Application as paused, and immediately writes a durable `verify_pending` entry that
  preserves the original `automated` value and the run/application identity needed for
  recovery. Further asserts: destructive-phase gates treat `verify_pending` as blocking; a
  later successful re-read showing the paused shape plus this run's marker promotes the
  entry to `paused`; a non-paused shape, marker mismatch, 404, malformed response, or
  repeated read failure stays fail-closed (`verify_pending` retained); resume keeps the
  restoration obligation until the entry is verifiably settled. The same
  `verify_pending` test expectations apply to the Python and collection suites wherever
  the shared §1/§4 behavior is implemented. Bash also runs the same valid/malformed
  classification vectors and asserts non-zero/no mutation/no successful state for every
  `UNKNOWN` category. Every form factor also tests that a missing/replaced run marker
  immediately before resume produces no patch and no `resumed` state.
- **Journal schema validation (§2a)**, in all three form factors: for every validation
  point (each destructive gate pass, resume eligibility, resume patch, `verify_pending`
  settlement, every state transition), a record that is absent, malformed, partial, of
  unknown or non-integer version, in an unknown state, or structurally impossible produces
  `recovery_required`, a fatal/non-zero result, no mutation, and a sanitized diagnostic
  carrying no payload or Application body. Payload-specific vectors: `restore_payload`
  missing on a `paused`/`verify_pending`/`resumed` entry; present on a
  `skipped_disabled`/`classification_unknown` entry; carrying a full `syncPolicy` or any
  sibling sync-policy key; carrying an unknown member or a non-boolean
  `prune`/`selfHeal`/`allowEmpty`; carrying a surviving `enabled: null` (canonicalization
  violation — rejected, never re-canonicalized). Each asserts the gate fails **at the
  gate**, before ACTIVATION or FINALIZATION proceeds, rather than at resume time. A
  multi-entry journal with two distinct invalid records reports both.
- **Legacy migration (§2b)**: a collection-written legacy record (no schema version, full
  `spec.syncPolicy` with sibling `syncOptions`/`retry`) whose `automated` member is
  `ACTIVE` migrates to a schema-version-2 record whose `restore_payload` is the canonical
  `automated` object only, is durably written **before** any resume patch, and produces a
  resume patch byte-identical to the natively journaled case — asserting the sibling keys
  are never sent. Rejected legacy vectors, each `recovery_required` with no mutation: no
  `automated` member; non-mapping `syncPolicy`; `automated` classifying `INACTIVE_ABSENT`,
  `INACTIVE_NULL`, `INACTIVE_DISABLED`, or `UNKNOWN`; marker missing; marker replaced by a
  foreign run; live Application UID differing from the bound identity. One test asserts
  explicitly that no restore payload is ever derived from the live paused Application.
  The collection asserts conversion from its `original-sync-policy` annotation and Bash
  asserts the identical conversion from its state file's `original_sync_policy` — both
  including a legacy snapshot carrying sibling `syncOptions`/`retry` keys, whose migrated
  resume patch must contain no sibling key. Python asserts it recognizes and rejects a
  foreign legacy record rather than converting one, since it never wrote the shape. A
  migration that succeeds but whose resume then fails leaves the legacy evidence — the
  annotation for the collection, the state-file entry for Bash — intact for retry.
- **Mutation/journal durability boundary (§1a)**: intent-persistence failure sends no
  patch and leaves the cluster unchanged; a patch accepted with the subsequent evidence
  write failing leaves a durable `intent_recorded` operation record, returns non-zero, and
  is reconciled on rerun from the recorded `owned_transition` — asserting for `pause` that
  the restore payload comes from the pre-mutation record and never from the live paused
  Application; a resume whose journal write fails after an accepted patch is reconciled as
  our own accepted resume rather than reported as marker tampering, closing the
  permanent-block path; a live UID differing from `expected_uid` is `recovery_required` in
  every state even when the observed value matches exactly; ambiguous transport outcomes
  land in `verify_pending`/`recovery_required` and never in `settled`; `settled` is
  asserted unreachable without a passing post-mutation re-read. Bash runs the same vectors
  with `expected_resource_version` absent, and the suite asserts no test claims Bash OCC.
- Changelog entry under `CHANGELOG.md` `## [Unreleased]` per the repository's Version
  Management policy. The implementation slice is ordinary development work, so it does
  not change released version identifiers or create a release tag; the accumulated
  change is PATCH-level input to a later explicitly scoped release PR, which is where
  the synchronized Python/Bash/collection version bump belongs.

## Tracker updates (same PR as this spec)

New finding rows in `thermos-resolution-plan.md` (Round-4 / spec-sourced section):

| id | severity | summary |
| --- | --- | --- |
| new-A1 | High | Bash pause merge patch omits `automated` → silent no-op journaled as paused |
| new-A2 | Medium | `automated.enabled: false` classified active in all three implementations |
| new-A3 | Medium | Resume overwrites stale `syncPolicy` keys; no post-resume verification |
| new-A4 | Medium | No journal revalidation before ACTIVATION-on-resume or integrated decommission |

Plus one planned slice row referencing this design doc. Cross-references recorded as
adjacent-not-superseded: `SSA-05` (script lifecycle), `TR2D-02` (collection resume OCC),
`R3-10a` (discovery blast radius), `R3-T3` (parity-test oracle).

## Acceptance criteria

1. A cluster with an auto-sync Application paused via the Bash script has
   `spec.syncPolicy.automated` absent (or null) afterwards, or the script exits non-zero.
2. An Application with `automated.enabled: false` is never patched by pause and never
   patched by resume, in all three implementations.
3. Resume changes only `spec.syncPolicy.automated` and the marker annotation; a sibling
   `syncPolicy.retry` edit made mid-switchover survives resume.
4. A run resumed at ACTIVATION after its paused Application was re-enabled (or its marker
   replaced) fails closed before any activation mutation, naming the Application.
5. A finalization phase (including integrated decommission) with `--argocd-manage` fails
   closed under the same conditions before any finalization mutation.
6. Every valid or documented normalized-equivalent `automated` shape has the exact §2 outcome, including
   `enabled: null` → active and `enabled: false` → disabled. Every malformed scalar,
   array, nested object/member, parent container, unreadable response, or structurally
   invalid response produces `UNKNOWN`, no pause/resume mutation, no successful journal
   state, fatal/non-zero status, and a block at activation, finalization, and every
   destructive-phase gate in Python, the collection, and Bash.
7. Failure output identifies the affected Application without serializing the malformed
   value or full resource.
8. All existing green tests still pass except those that asserted the defective patch
   shapes, which are inverted in the same commit.
9. A pause patch that was accepted while the post-patch verification read failed is
   never journaled as paused success: a durable `verify_pending` entry is written, the
   run returns non-zero, and every destructive-phase gate blocks until a later re-read
   settles the entry. The guarantee is that no mutation is untracked or falsely
   reported successful — not that no mutation occurred.
10. Every destructive-phase gate fails closed on a journal entry whose stored
    `restore_payload` is missing, malformed, non-canonical, non-`ACTIVE`, or carries
    anything beyond the tool-owned `automated` object — at the gate, before ACTIVATION or
    FINALIZATION performs any mutation, and not merely at resume time.
11. An Application paused by the pre-slice collection implementation (full `spec.syncPolicy`
    snapshot) is either migrated to a schema-version-2 record whose restore payload is the
    canonical `automated` object alone — durably written before resume, and producing a
    resume patch containing no sibling `syncPolicy` key — or fails closed as
    `recovery_required`. No restore payload is ever inferred from the live paused
    Application.
12. No pause or resume mutation is submitted without a durable `intent_recorded` operation
    record, and a mutation accepted while its evidence write failed is recoverable rather
    than untracked: rerun reconciles it from the recorded intent, a replacement UID is
    never adopted, and a resume whose journal write failed never presents as marker
    tampering that blocks the gates permanently.
