# State Execution Integrity Residuals — Design

**Date:** 2026-07-29
**Branch:** `ansible` (spec branch `docs/thermos-safety-specs`)
**Status:** approved design, awaiting implementation plan
**Origin:** cross-validation of main-branch safety specs against `ansible` @ `0bf55db9`,
independently revalidated (Codex). Excludes issues already tracked: `SSA-01`
(primary≠secondary UID distinctness), `R3-A6` (collection `reset_from` identity bypass),
`R3-P7`/`R3-10c` (dry-run report artifact), `R3-X1`/`R3-10g` (run-lock handle close on
normal completion — the handle is intentionally held for process lifetime and released
through an `atexit` hook, which is not itself a leak; `R3-X1` tracks the absence of an
explicit close on normal completion and for long-lived embedding/reuse, which is what
the suite's `ResourceWarning` surfaces).
This design deliberately builds on the branch's accepted snapshot-rollback approach —
memory-mode state (as designed for `main`) is explicitly rejected here.

## Problem

1. **Validate-only leaks state.** The runtime checkpoint captures only `current_phase`,
   `errors`, `last_updated` (`lib/utils.py:482-505`); validate-only preflight writes
   versions, observability flags, expected names, and other `config` keys
   (`acm_switchover.py:639-692`) that survive the restore. The state file is not
   byte-identical after a validate-only run.
2. **Crash mid-simulation poisons state.** Dry-run snapshot/restore happens only in
   `finally` (`acm_switchover.py:428-443`); every intermediate phase/step/config write is
   durable (`lib/utils.py:468-471,516-529,570-577`). SIGKILL mid-dry-run leaves PREFLIGHT→
   FINALIZATION residue a later real run will trust. (A fresh `COMPLETED` cannot be minted
   by dry-run — `lib/operation_runners.py:182-185` returns before completion — but
   everything short of it can.)
3. **No parent-directory fsync.** `_write_state` fsyncs the temp file and `os.replace`s it
   (`lib/utils.py:367-384`) but never fsyncs the directory; the rename itself can be lost
   on power failure.
4. **Locks are state-file-scoped.** `_run_lock_path = realpath(state_file) + ".run.lock"`
   (`lib/utils.py:156`); two operators running against the same physical hubs with
   different `--state-file` paths do not contend.
5. **Reset races and `--force` overreach.** `--reset-state` calls
   `os.remove(resolved_state_file)` before the StateManager (and its run lock) exists
   (`acm_switchover.py:1073-1083`) — it can delete an active run's state. Separately,
   `--force` resets progressed/completed state on the stale-COMPLETED and
   FAILED-undeterminable paths.
6. **No run contract.** Resume reads `method`, `old_hub_action`,
   `manage_auto_import_strategy`, ArgoCD flags, etc. from the current invocation
   (`acm_switchover.py:813-823,889-901`) with no comparison against what the recorded
   progress was executed under.

## Goals

1. Simulation modes (validate-only, dry-run) leave the state file byte-identical, and a
   crashed simulation is detected — never silently trusted.
2. State writes survive power failure (rename durability).
3. One physical hub pair, one running switchover — regardless of state-file paths, Unix
   user, container, execution node, or host.
4. State reset happens under the lock; `--force` cannot destroy progressed history.
5. Safety-critical options are bound to the state; silent behavior changes on resume are
   impossible.

## Non-goals

- Memory-mode state (`main`-spec design) — rejected; the snapshot approach stays.
- Hub distinctness (`SSA-01`), collection `reset_from` (`R3-A6`).
- Evidence/transaction schema beyond what Areas B and D define (the auto-import
  transaction and migration-evidence slices, `2026-07-29-auto-import-transaction-design.md`
  and `2026-07-29-migration-evidence-design.md`).
- A bespoke lock service or lock files on shared filesystems. §3 uses the coordination
  primitive the cluster already provides (`coordination.k8s.io/v1` Lease); a local flock
  survives only as a same-host optimization and carries no guarantee.

## Design

### 1. Full-fidelity simulation snapshot + crash marker

- `capture_state_snapshot()` / the validate-only checkpoint are unified into one
  full-fidelity snapshot: capture the original state-file **bytes** (or an explicit
  absent-file sentinel), not a field-level deep copy. Restore atomically writes the
  captured bytes back — deleting the file, and fsyncing the containing directory, when the
  original was absent (§2) — so both `--validate-only` and `--dry-run` end byte-identical
  by construction.
- Durably persist `simulation_in_progress: {"mode": "dry_run"|"validate_only",
  "started_at": ...}` **before the first simulation write** (it is the first write), keep
  it present on every intermediate write, and clear it as part of the byte restore.
- On load, a present `simulation_in_progress` marker means a simulation died without
  restoring: fail closed with a message naming the mode, start time, and remedies
  (`--reset-state`, or manual inspection). No auto-repair — the snapshot lived in the
  dead process.
- Preserves the existing property that dry-run cannot mint `COMPLETED`.

### 2. Parent-directory fsync

After `os.replace` in `_write_state`: open the containing directory read-only, fsync, close.
Only explicitly-unsupported errors (`ENOTSUP`/`EINVAL`-class, e.g. filesystems that reject
directory fsync) are suppressed and logged at debug; any other I/O failure propagates —
a state write must not report success when its durability step failed.

**The same requirement applies to the unlink path.** §1 restores an originally-absent
state file by deleting the generated one, and a rename is not the only directory-entry
change that can be lost across power failure — an unlink can be too, resurrecting the
state file and with it the `simulation_in_progress` marker, which both violates
byte-identical restoration and blocks the next real run on a simulation that in fact
completed. The absent-file restoration sequence is therefore, in order:

1. `unlink` the state file;
2. open the containing directory read-only;
3. `fsync` the directory descriptor;
4. close the descriptor.

Error handling is identical to the rename path: only the explicitly-unsupported
`ENOTSUP`/`EINVAL`-class errors are suppressed and logged at debug; **any other fsync
failure means the restoration did not durably succeed** and propagates, so a simulation
never reports a successful byte-identical restore on an undurable deletion. An unlink
that fails with `ENOENT` — the file already absent — is the desired end state and skips
to the directory fsync rather than failing.

### 3. Per-hub distributed locks (Kubernetes Lease)

A lock root namespaced by effective UID cannot enforce Goal 3. Two runs as different Unix
users, two AAP execution-node workers, two containers, or two hosts each get their own
`/tmp/acm-switchover-<euid>/` tree and never contend, while operating on the same physical
hub pair. A host-local `flock` is not the right authority for a guarantee stated in terms
of physical clusters, and this design does not retain the global claim while implementing
a per-euid local lock.

**The authoritative lock is a `coordination.k8s.io/v1` Lease on the hub itself** — the one
namespace every supported execution form factor (CLI on a workstation, container, AAP
execution node, collection run) already shares by definition, because it is the cluster
being switched over. Viability is established, not assumed: `lib/kube_client.py` already
exposes api-version/kind-driven `get_custom_resource`, `create_custom_resource`,
`patch_custom_resource`, `delete_custom_resource`, and `list_custom_resources`, and
`coordination.k8s.io/v1` is a built-in API group on every supported OpenShift/ACM version.

- **Lock identity.** One Lease per physical hub, named from the hub's cluster UID
  (`acm-switchover-<sha256(uid)>`, truncated to a valid object name and prefixed so it is
  unambiguously tool-owned). The UID, not the kubeconfig, state-file path, or hostname, is
  the identity — that is what makes the lock physical.
- **Topology: one Lease per hub, not one per pair.** A pair-keyed Lease would have to live
  on one of the two hubs, and that hub is exactly the one integrated decommission tears
  down; the surviving hub would then hold no record. Per-hub Leases also make
  single-context operations (standalone decommission) lock precisely the one hub they
  touch, and they compose: a switchover acquires both, an operation on one hub acquires
  one.
- **Coordination namespace.** The Lease is created in the tool's own operating namespace
  on each hub — `open-cluster-management-backup`, the namespace the switchover already
  requires and already holds a `Role` in (`deploy/rbac/role.yaml`) — not `kube-system` and
  not `kube-node-lease`. This keeps the required permission namespaced rather than
  cluster-scoped, and keeps the lock inside the blast radius the operator already granted.
- **Acquisition order.** Both hub UIDs are sorted and acquired in that fixed order, so two
  runs approaching the pair from opposite directions cannot deadlock. If the second
  acquisition fails, the first is released before failing.
- **Holder identity.** `spec.holderIdentity` is a structured, non-secret string identifying
  this run: tool run id, hostname, PID, and effective user. It is diagnostic, not
  authorization — a run never adopts a Lease because the identity "looks like" its own.
- **Acquisition timestamp and renewal.** `spec.acquireTime` is set on acquisition;
  `spec.renewTime` is refreshed on a fixed interval by the holding run for its whole
  lifetime, and `spec.leaseDurationSeconds` is a bounded value comfortably larger than the
  renewal interval.
- **Crash expiry.** A Lease whose `renewTime` is older than `leaseDurationSeconds` is
  expired and may be taken over — that is the only path by which another run acquires a
  Lease it did not create. Takeover is a conditional update bound to the observed
  `resourceVersion`, so two runs racing to claim the same expired Lease cannot both win.
- **Never adopt another holder's live Lease.** An unexpired Lease held by a different
  identity is contention: fatal, with the holder identity, `acquireTime`, `renewTime`, and
  the hub UID in the message.
- **Replacement-UID detection.** The Lease name is derived from the UID, so a hub deleted
  and recreated produces a different Lease. That is necessary but not sufficient — see the
  post-acquisition identity barrier below.
- **Release.** On normal completion the run deletes its Leases with a precondition on the
  `resourceVersion` it last wrote, so a Lease already taken over after expiry is never
  deleted out from under its new holder. On abnormal exit the Lease simply expires.
- **Check mode / dry-run.** A dry-run or check-mode run **does** acquire the Leases: a
  simulation reads live cluster state and must not run concurrently with a real switchover
  against the same hubs. It creates no other durable cluster object, and its Leases are
  released on completion like any other run's. When Lease permissions are unavailable in
  check mode the run fails closed with the same diagnostic as execute mode rather than
  silently downgrading.
- **Sanitized errors.** Lease diagnostics carry the hub UID, Lease name, namespace, holder
  identity, and timestamps — never kubeconfig data, tokens, or API response bodies.
- **Parity.** Python and the collection implement this independently against the same
  contract; lock names, acquisition order, expiry arithmetic, takeover decisions, and
  failure classes are parity fixtures.

**A local `flock` may remain as a same-host/same-process optimization** — it fails fast
and cheaply when two runs on one machine collide — but it is explicitly **not** the global
safety authority, and no guarantee in this document rests on it.

**Failure behavior when Lease RBAC is missing.** A `403` on Lease get/create/update is
fatal and fails closed with an explicit remediation message naming the required rule. It
is never downgraded to "proceed without the lock", because doing so would silently restore
exactly the unguarded concurrency this section exists to prevent.

#### Post-acquisition identity barrier

Discovery resolves both hub UIDs, and the Lease names are derived from them — but the
identities are read *before* the locks exist. If a hub is deleted and recreated inside
that window, a concurrent run resolves the replacement UID, derives a different Lease
name, acquires it cleanly, and both runs proceed against the same physical hub. Lock
acquisition alone therefore does not establish identity. The full ordering is:

1. **Strict UID discovery** for both hubs (read-only; a missing, unreadable, or malformed
   identity is fatal here, before any lock exists).
2. **Deterministic acquisition** of both Leases in sorted-UID order.
3. **Immediate strict re-read of every hub UID**, once **all** required locks are held.
4. **Exact equality** against the UIDs the Lease names were derived from.

Rules:

- **No state or cluster mutation before this barrier passes** — no simulation-marker
  write, no legacy contract adoption, no state mutation beyond identity recording, and no
  cluster mutation of any kind.
- **Any of replacement, unreadable identity, malformed identity, or mismatch releases all
  locks and fails fatally.** The design does not retry-and-continue in place: a hub that
  changed identity mid-acquisition is a different cluster, and the operator re-invokes
  after establishing which cluster they meant.
- The re-read is **strict**: it uses the same fail-closed identity read as step 1, so an
  API error at step 3 is a failure, never a silent pass.
- Python and the collection implement identical behaviour, including the ordering and the
  release-before-failure rule.

- Contention → fatal: "another switchover is running against this hub", naming the hub
  UID, the Lease, and the current holder.
- Existing state-file run-lock and write-lock stay unchanged. Single-context operations
  (e.g. standalone decommission) lock the one hub they have.

#### RBAC implications (documented here, implemented with the slice)

This is a design document and changes no RBAC file. The implementation slice must update
these surfaces **together**, and this design is not implementable until it does:

- `deploy/rbac/role.yaml` — add `apiGroups: ["coordination.k8s.io"]`,
  `resources: ["leases"]`, `verbs: ["get", "create", "update", "delete"]`, namespaced to
  the tool's operating namespace. `list` and `watch` are deliberately **not** requested:
  the tool addresses its Lease by name. `patch` is not requested either — takeover and
  renewal use conditional `update`.
- `deploy/helm/acm-switchover-rbac` — the same rule in the chart's rendered Role, kept in
  sync with the static manifest by the existing manifest-consistency checks.
- `deploy/acm-policies/` — the same rule wherever the RBAC is distributed as policy.
- Least privilege: namespaced `Role`, not a `ClusterRole`; one resource; four verbs; no
  wildcard.
- Bootstrap/deployment: an existing installation upgraded to this slice has no Lease
  permission until its Role is updated, so the slice's rollout notes must state that the
  RBAC update precedes the tool update. Until then the run fails closed with the
  remediation message above — which is the safe direction, but it is an operator-visible
  behavior change and must be released as one.
- Rollback and stale Leases: rolling the tool back to a pre-Lease version leaves Lease
  objects behind. They are inert (nothing reads them) and expire by `renewTime`; the
  rollback notes should mention them so an operator does not mistake them for live locks.

### 4. Reset under lock; `--force` scope

- `--reset-state` moves into StateManager as a construction mode that never parses or
  validates the existing payload: resolve the state path → acquire the run lock → then
  reinitialize/remove the file under the lock. A corrupt or schema-invalid state file must
  not make reset fail on load — reset exists precisely for that case. Normal (non-reset)
  construction keeps fail-closed payload validation. The pre-construction `os.remove` at
  `acm_switchover.py:1073-1083` is deleted. A concurrent run holding the lock makes reset
  fail fast instead of deleting live state.
- `--force` no longer triggers `state.reset()` on the stale-COMPLETED or
  FAILED-undeterminable paths; those paths now instruct the operator to use
  `--reset-state` explicitly. `--force` retains its other meanings (staleness override,
  legacy identity backfill).

### 5. Run contract

- On first run (state creation), persist `run_contract`. Canonical field table (exhaustive
  — a field not listed here is not contract-bound):

  | field | mismatch class | condition (phase boundary or journal state) and override eligibility |
  | --- | --- | --- |
  | `method` (passive/full) | A — immutable/destructive past PRIMARY_PREP | freely re-recorded through PRIMARY_PREP; past it no override is accepted |
  | `activation_method` (patch/restore) | A — immutable/destructive past PRIMARY_PREP | same boundary as `method` (the collection already binds it in checkpoint identity; Python must match) |
  | `old_hub_action` | A — immutable/destructive past PRIMARY_PREP | same boundary as `method` |
  | `manage_auto_import_strategy` | A — immutable/destructive past PRIMARY_PREP | same boundary as `method` |
  | `--argocd-manage` | A — immutable while the pause journal holds a gate obligation (any entry other than `resumed`, including `skipped_disabled`) | class B (overridable, any phase) only when the journal is absent or holds `resumed` entries exclusively; clearing it otherwise withdraws the FINALIZATION gate that must still re-read those entries |
  | `--argocd-resume-after-switchover` | A — immutable while the pause journal holds a restoration obligation (any `paused`, `verify_pending`, `classification_unknown`, or `recovery_required` entry) | class B (overridable, any phase) once no such entry remains — `resumed` and `skipped_disabled` entries impose no restoration obligation; changing it with outstanding entries can silently drop that obligation |
  | `--min-managed-clusters` (expectation floor) | B — overridable | `--accept-changed-options` accepted at any phase |
  | expectation waiver (the Area D expected-ManagedCluster-name waiver, `--skip-managed-cluster-expectations`, provisional name, in `2026-07-29-migration-evidence-design.md` §2) | B — overridable | `--accept-changed-options` accepted at any phase |
  | `--skip-observability-checks` | B — overridable | `--accept-changed-options` accepted at any phase |
  | `tool_version` | C — informational | mismatch warns at every phase; never blocks, never requires the flag |

- Every resume compares the live invocation against the contract **before any mutation**
  and **rejects every non-permitted contract difference**, listing each as
  `field: recorded → requested` together with its class and, for a rejection, the reason.
  All mismatches present are reported together, not just the first. A run carrying both a
  class A and a class B mismatch remains fatal even with the override flag.
- New flag `--accept-changed-options`: re-records the contract and journals the old→new
  diff into state (audit trail) for class B fields whose condition permits it, as the one
  atomic transition defined in the mismatch decision model below. `--force`
  does not substitute. The override is field-and-condition scoped per the canonical table
  above.
- Legacy state without a contract:
  - **Un-progressed** legacy state (at or before PREFLIGHT) records the contract silently
    from the current invocation — nothing has been executed yet, so there is nothing to
    protect — logged at info.
  - **Progressed** legacy state (beyond PREFLIGHT) is *not* adoptable by
    `--accept-changed-options`. That flag is the class B override, and a legacy record
    carries no baseline for the class A fields (`method`, `activation_method`,
    `old_hub_action`, `manage_auto_import_strategy`), so adopting the current invocation
    would let those destructive values be set from an unverified command line **after**
    the work they govern has already run — precisely the silent change class A exists to
    prevent, and it would be indistinguishable from an operator resuming with different
    destructive options. Two paths only: `--reset-state`, or an explicit audited legacy
    migration in which the operator states the class A values the recorded progress was
    executed under. That migration is accepted only when those values are supplied
    explicitly (never defaulted from the current invocation), is journaled with actor,
    timestamp, reason, and the supplied values, and is refused when the state's own
    evidence contradicts them. Class B and C fields are recorded from the current
    invocation in the same durable act. After migration the contract is ordinary and all
    three classes apply normally.

#### Mismatch decision model

Every contract-bound field belongs to exactly one of three classes, and the class
together with the field's current condition — its phase boundary, or for the two ArgoCD
flags its journal state — determines the outcome of a difference. There is no "any
difference is fatal" rule.

**Class A — immutable/destructive.** While the field is *within* its permitted window, a
difference is not a mismatch at all: the field is re-recorded normally (see the audit
rule below). Once the window has closed — the phase boundary passed, or the journal
obligation incurred — any difference is **always fatal before any mutation**, and neither
`--accept-changed-options` nor `--force` can override it. The error lists the field,
recorded value, requested value, and the reason and phase that make it immutable.
Recovery is `--reset-state`; additionally, for `--argocd-resume-after-switchover`,
settling every outstanding restoration obligation through an explicit resume/rerun
reopens the window. That second route does not exist for `--argocd-manage`, whose
obligation a resume cannot clear (below).

"Outstanding obligation" is **not the same obligation for the two ArgoCD flags**, because
the ArgoCD design gives `resumed` and `skipped_disabled` different roles: `resumed` is
its only terminal state and is skipped by gates, while `skipped_disabled` is
informational, never terminal, and is re-read on *every* gate pass so an app that was
disabled at pause time cannot be silently re-enabled mid-switchover.

- `--argocd-resume-after-switchover` binds a **restoration** obligation: one exists while
  any `paused`, `verify_pending`, `classification_unknown`, or `recovery_required` entry
  remains. Resume never
  patches `skipped_disabled` entries and `resumed` entries are complete, so a journal
  holding only those two kinds carries no restoration obligation and the flag is class B.
- `--argocd-manage` binds a **gate** obligation, which is broader: clearing it withdraws
  the FINALIZATION gate, which is the destructive-phase gate that must still re-read
  `skipped_disabled` entries before any finalization mutation. (The ACTIVATION gate runs
  on every pass and is unaffected, so the exposure is the finalization boundary.) An
  obligation therefore exists while any entry other than `resumed` remains — including
  `skipped_disabled`, which no resume can convert. Only an absent journal, or one
  consisting solely of `resumed` entries, makes this flag class B.

**Class B — overridable.** Fatal before any mutation **without**
`--accept-changed-options`. Accepted only when the canonical table permits the override
under the field's current condition — for the two ArgoCD flags, the journal-state
condition above; an override attempted while that condition forbids it is a class A
outcome. `--force` is never a substitute for the flag.

##### The transition is one atomic commit

Writing the audit record and then rewriting the contract is two durable writes with a
crash window between them: the audit says the change was accepted while the contract still
holds the old value, so the next resume either rejects a change it already accepted or
appends a duplicate audit entry. The authoritative contract is therefore that **an
accepted override is a single atomic authoritative state/checkpoint update** containing,
together:

- the audit event;
- the old contract;
- the new contract;
- the phase;
- the timestamp;
- the operator invocation/audit context;
- an immutable `transition_id`;
- `status: committed`.

Only that one durable commit changes the effective contract. Before it lands the old
contract is in force in full; after it lands the new one is, and there is no observable
state in between. The state file is written through the single atomic
temp-write + `os.replace` + directory-fsync path of §2, so this is achievable directly.

**Versioned pending/committed protocol (fallback).** Where an implementation primitive
genuinely cannot perform one atomic update — a checkpoint backend that must write the
audit journal separately from the contract — the transition uses an explicit two-phase
protocol instead, never the bare audit-then-rewrite sequence:

- A durable **pending** transition record is written first, carrying the immutable
  `transition_id`, the old value, the new value, the phase, the timestamp, and the
  operator context.
- Resume **reconciles** before evaluating any mismatch: a pending record whose
  `transition_id` has no commit marker is completed forward (the new value is applied and
  the commit marker written) or is reported as an incomplete transition — it is never
  silently rolled back, and never reapplied a second time once committed. `transition_id`
  is what makes the reconciliation idempotent: a duplicate audit entry for the same id is
  impossible.
- The **commit marker is written last**. It is the single point at which the new contract
  becomes effective.
- A **partial pending state blocks all mutation** until reconciliation completes; it is
  not a warning and is not skipped.
- A **malformed transition record** — missing `transition_id`, missing old or new value,
  unknown status, or a commit marker with no matching pending record — fails closed.

Either way, a failed durable write leaves the contract unchanged and fails the run. The
contract is never re-recorded on an unwritten or half-written transition. Python and the
collection apply the identical protocol, including the reconciliation decisions and the
`transition_id` semantics.

The same atomic transition — the same fields, `transition_id`, and commit semantics — is
written whenever a class A field is re-recorded inside its permitted window (for example
`method` changed during PRIMARY_PREP). That is a permitted contract update rather than a
mismatch rejection — it needs no flag — but it is committed identically, so no
contract-bound field ever changes without an audit trail and none changes non-atomically.

**Class C — informational.** `tool_version` is the current and only example. It warns,
never blocks, never requires `--accept-changed-options`, and never silently alters any
safety-critical behavior; it is recorded for diagnostics only.

### 6. Collection parity

- `checkpoint_phase`'s existing `validate_operation_identity` gains a contract comparison
  recorded at checkpoint creation. The compared set is the complete canonical field set
  above, enumerated explicitly rather than abbreviated — no summary phrase such as
  "activation-affecting flags" stands in for it:

  | contract field | collection representation | class and phase rule |
  | --- | --- | --- |
  | `method` | operation method variable | A — immutable past PRIMARY_PREP |
  | `activation_method` | activation-method variable (already bound in checkpoint identity) | A — immutable past PRIMARY_PREP |
  | `old_hub_action` | old-hub action variable | A — immutable past PRIMARY_PREP |
  | `manage_auto_import_strategy` | auto-import management variable | A — immutable past PRIMARY_PREP |
  | `--argocd-manage` | ArgoCD management variable | A while any entry other than `resumed` remains (gate obligation, `skipped_disabled` included); otherwise B, any phase |
  | `--argocd-resume-after-switchover` | ArgoCD resume-behavior variable | A while any `paused`, `verify_pending`, `classification_unknown`, or `recovery_required` entry remains (restoration obligation); otherwise B, any phase |
  | `--min-managed-clusters` | expectation-floor variable | B — overridable at any phase |
  | expectation waiver (Area D expected-name waiver, per `2026-07-29-migration-evidence-design.md` §2) | waiver variable | B — overridable at any phase |
  | `--skip-observability-checks` | observability-skip variable | B — overridable at any phase |
  | `tool_version` | recorded collection/tool version | C — warns, never blocks |

  An override variable mirrors `--accept-changed-options` with the same field-and-phase
  scoping and the same atomic-transition requirement (audit event, old contract, new
  contract, phase, timestamp, invocation context, `transition_id`, and committed status in
  one durable commit — or the versioned pending/committed protocol where the checkpoint
  backend cannot commit them together).
  Parity means the two implementations reach the identical class, phase decision,
  override outcome, and audit content for the same inputs — no field can change silently
  in one implementation but not the other — while remaining independent implementations
  that share no code.
- The simulation marker is N/A for the collection (per-task `dry_run` guards mean no
  durable writes in check mode today); noted in role docs.

## Testing

- Byte-equivalence: hash state file before/after `--validate-only` and `--dry-run`
  (marker lifecycle included) — equal.
- Simulated crash: capture marker written, process killed → next load fails closed naming
  the mode; `--reset-state` then recovers.
- `_write_state` calls dir-fsync (mock `os.fsync` counts); `EIO`-class dir-fsync failure
  propagates (write not reported successful); `ENOTSUP`/`EINVAL` suppressed at debug.
- Absent-file restoration durability: the restore path unlinks and **then** fsyncs the
  containing directory, asserted by call ordering (unlink precedes the directory fsync,
  and the descriptor fsynced is the directory's). An `EIO`-class directory-fsync failure
  after the unlink propagates and the restoration is **not** reported successful;
  `ENOTSUP`/`EINVAL` are suppressed at debug exactly as on the rename path; an `ENOENT`
  unlink still performs the directory fsync and succeeds.
- Distributed locking: two runs against the same hub UIDs **as different Unix users** →
  the second fails fast (the direct regression test for the per-euid namespace); same for
  two runs with different `--state-file` paths, and for a run in a container against a run
  on the host. Different UID pairs → no contention. Acquisition order stable under
  reversed context order. Lease behaviour: an unexpired Lease held by another identity is
  fatal contention naming holder and timestamps; a Lease expired past
  `leaseDurationSeconds` is taken over via a `resourceVersion`-conditional update, and two
  racing takeovers produce exactly one winner; renewal refreshes `renewTime` on schedule;
  release deletes only when the last-written `resourceVersion` still matches; a `403` on
  Lease get/create/update is fatal with the remediation message and **never** proceeds
  unlocked; check mode acquires and releases Leases and fails closed identically on
  missing permission. Python and the collection produce identical lock names, ordering,
  expiry decisions, and failure classes.
- Post-acquisition identity barrier: a hub UID that changes between discovery and the
  post-lock re-read releases all locks and fails fatally, with no state or cluster
  mutation performed — asserted for replacement UID, unreadable identity, malformed
  identity, and API error at re-read. A stable UID passes the barrier and proceeds. The
  re-read is asserted to occur **after** both locks are held, not between them.
- Reset: concurrent lock holder → reset fails fast, live state intact; reset under lock
  reinitializes.
- `--force` on progressed state no longer resets; message points to `--reset-state`.
- Contract, by mismatch class over the complete field set:
  - every **immutable-at-the-current-phase** field mismatch is fatal before mutation both
    without and with `--accept-changed-options`, naming field, recorded value, requested
    value, and the reason/phase;
  - every **overridable** field mismatch is fatal without the override flag;
  - every overridable field mismatch succeeds **only** in a phase where the canonical
    table permits the override, and only after the durable audit record is written;
  - an override attempted while its phase or journal-state condition forbids it fails
    closed, with the two ArgoCD flags asserted separately: a journal holding only
    `resumed` and `skipped_disabled` entries leaves `--argocd-resume-after-switchover`
    overridable while `--argocd-manage` stays class A (its gate must keep re-reading the
    `skipped_disabled` entries), and any `paused`, `verify_pending`, or
    `classification_unknown`, or `recovery_required` entry keeps both flags class A;
  - a class A field changed **at or before** its phase boundary (for example `method`
    during PRIMARY_PREP) is re-recorded normally rather than rejected, and that
    re-record is audited like any other contract update;
  - a durable write failure during an accepted class B override leaves the
    contract unchanged and fails the run — the contract is never re-recorded on an
    unwritten or half-written transition;
  - the accepted override is **one atomic commit**: a crash injected during the write
    leaves either the complete old contract or the complete new contract with its audit
    event, `transition_id`, and committed status — never an audit event with the old
    contract still in force;
  - under the pending/committed fallback: a pending record with no commit marker is
    reconciled forward on resume before any mismatch evaluation and is neither rolled back
    nor reapplied once committed; replaying the same `transition_id` produces no duplicate
    audit entry; a partial pending state blocks all mutation; a malformed transition
    (missing `transition_id`, missing old or new value, unknown status, or a commit marker
    with no pending record) fails closed;
  - a class A re-record inside its permitted window uses the identical atomic transition,
    asserted by the same crash-injection vector;
  - `tool_version` mismatch warns, does not block, and never requires the flag;
  - multiple simultaneous mismatches are all reported in one message, not just the
    first;
  - a mix of immutable and overridable mismatches remains fatal even with the flag;
  - `--force` alone authorizes no contract change in any class;
  - `--min-managed-clusters` and the expectation-waiver flag are explicitly covered,
    including their any-phase override eligibility;
  - progressed legacy state is refused by `--accept-changed-options` alone and requires
    either `--reset-state` or the audited legacy migration with explicitly supplied
    class A values, which is journaled and is refused when the state's own evidence
    contradicts the supplied values; un-progressed legacy state adopts silently.
- Collection: the same vectors over the same complete field set produce the same
  class, phase decision, override outcome, and audit-record content as Python.

## Release/process follow-up

Version management is not a test case. Per the repository's Version Management policy in
`AGENTS.md`, the implementation PR for this slice is ordinary development work:

- it records its changelog-worthy change under `CHANGELOG.md` `## [Unreleased]`;
- it does **not** change released version identifiers and does not create a release tag;
- the synchronized Python/Bash/container/Helm/README version updates happen only in a
  separately scoped release/version-bump PR that selects the next version from the
  accumulated `[Unreleased]` entries.

This design document performs no changelog or version update itself.

## Tracker updates (same PR)

| id | severity | summary |
| --- | --- | --- |
| new-E1 | High | Crashed dry-run/validate-only leaves durable intermediate state trusted by later runs |
| new-E2 | Medium | Validate-only checkpoint restores phase/errors only; config/steps leak |
| new-E3 | Medium | No run contract: resume silently accepts changed safety-critical options |
| new-E4 | Medium | Locks are state-file-scoped and host-local; same physical hubs don't contend across state files, Unix users, containers, execution nodes, or hosts |
| new-E5 | Medium | `--reset-state` deletes state before lock; `--force` resets progressed state |
| new-E6 | Low | `_write_state` lacks parent-directory fsync |

Plus one planned slice row referencing this design. `SSA-01`, `R3-A6`, `R3-P7`, `R3-X1`
cross-referenced as excluded-tracked.

## Acceptance criteria

1. A validate-only or dry-run leaves the state file byte-identical; a killed simulation is
   detected and blocks the next run until reset.
2. Two invocations against the same physical hubs cannot both run, regardless of state
   file paths, Unix user, container, execution node, or host — enforced by a
   `coordination.k8s.io/v1` Lease per hub, keyed on the hub's cluster UID, with bounded
   duration, renewal, crash expiry, and no adoption of an unexpired foreign holder. A
   missing Lease permission is fatal, never a silent unlocked run.
3. State reset cannot affect a running process's state file.
4. `--force` alone can never destroy progressed history.
5. Every resume rejects every non-permitted contract difference before any cluster
   mutation, per the three-class model: a class A immutable/destructive mismatch is fatal
   even with `--accept-changed-options`; a class B overridable mismatch is fatal without
   the flag and is accepted only in a phase the canonical table permits, leaving a
   durable audit record (field, old value, new value, phase, timestamp, invocation
   context, `transition_id`, committed status) applied as one atomic commit — or through
   the versioned pending/committed protocol where a backend cannot commit them together,
   with idempotent resume reconciliation; a class C informational mismatch
   (`tool_version`) warns without blocking. `--force` authorizes no contract change.
   Python and the collection apply the identical field-and-phase decisions over the same
   complete field set.
6. Restoring an originally-absent state file is durable: the unlink is followed by a
   containing-directory fsync, and an unexpected fsync failure means the restoration did
   not succeed rather than being reported as a byte-identical restore.
7. No state or cluster mutation occurs before every required hub lock is held **and**
   every hub UID has been strictly re-read under those locks and matched exactly against
   the UID its lock was derived from; any replacement, unreadable identity, malformed
   identity, or mismatch releases all locks and fails fatally.
