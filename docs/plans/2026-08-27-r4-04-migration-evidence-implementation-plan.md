# R4-04 ManagedCluster Migration Evidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: use `superpowers:subagent-driven-development` for reviewed task-by-task work in one session, or `superpowers:executing-plans` in a separate session. Every builder, independent validator, and PR-comment resolver must also read current `AGENTS.md` and the directly relevant repository instructions before acting.

**Goal:** Implement the accepted R4-04 migration-evidence transaction in both production form factors so activation freezes and proves the exact Backup/Restore inputs it consumes, expected ManagedCluster names cannot be weakened by a count floor, post-activation/finalization cannot proceed on incomplete evidence, and Restore cleanup is UID/resourceVersion-guarded and recoverable.

**Architecture:** Keep Python journal vocabulary behind `lib/run_record.py` and collection journal vocabulary behind `plugins/module_utils/checkpoint.py`; keep both stores physically independent but schema/transition-compatible. Reuse the R4-03 strict inventory authority rather than creating another list/read algebra. Implement only the narrow R4-05 durability prerequisite explicitly authorized by the R4-04 amendment. Keep the evidence model pure and mirrored, then integrate activation, post-activation, finalization, and integrated teardown in one behavior-changing parity PR so no merged intermediate state writes evidence without enforcing it or enforces evidence that activation does not produce.

**Tech stack:** Python 3.10–3.12 CLI, pytest, Kubernetes Python client, Ansible Collection, Ansible action/module plugins, `kubernetes.core`, YAML roles/playbooks.

**Normative specification:**

- `docs/plans/2026-07-29-migration-evidence-design.md`
- `docs/plans/2026-08-27-r4-04-current-base-design-amendment.md` at accepted exact head `f5f7505d55d7ef97b4642c87844e0c254b635018`

**Plan status:** reviewed and approved for publication. This document does **not** authorize runtime implementation by itself.

---

## Global constraints and delivery shape

1. Every implementation PR starts from the then-current `origin/ansible`, not from this documentation branch. If `origin/ansible` no longer contains the accepted specification and this plan, stop and resolve that governance state first.
2. Before the first edit in every PR, record repository identity, base SHA, head SHA, merge base, clean worktree, declared scope, and protected-file diff. Never modify `docs/ACM_SWITCHOVER_RUNBOOK.md` or `.claude/skills/**` without separate explicit operator approval.
3. Python CLI and Ansible Collection changes for parity-sensitive behavior land together unless the approved plan explicitly says a PR is a dormant prerequisite/foundation that changes no operator behavior.
4. R4-03 owns the shared complete strict Kubernetes inventory primitive. R4-04 may consume it but must not independently redesign its decommission consumers or create a competing read algebra.
5. R4-05 owns the general state-integrity residuals. R4-04 may implement only the file/directory durability prerequisite required before an R4-04 journal can authorize mutation.
6. The six immutable cluster-backup-operator contracts remain pinned exactly as accepted by the amendment: 2.12 `74b54988a5bd6712ea3fe3e9ceb770e06db91e8b`, 2.13 `7a7b240b3df71105da3f15620e4116498f9e2a23`, 2.14 `8b489db488739e7d9adca50cb3be0eae79293f22`, 2.15 `25b28b762355a14b4fb7f145efe173f73659e740`, 2.16 `9efe77eaec2139f106c957051e2297dafc84b482`, 2.17 `c8578f94df09deab561e1aa5a7e9fc9b57f7d113`.
7. Use TDD for every runtime task: add the smallest failing test first, run it and observe the expected failure, implement the minimum production change, rerun the targeted test, then run the relevant wider gate before committing.
8. Each implementation PR uses the project 3-prompt workflow: builder -> independent validator from a clean exact-head checkout -> PR-comment resolver/final validator. No merge/ready state with unresolved actionable threads.
9. If implementation is split as below, add one `thermos-resolution-plan.md` row with branch/worktree per PR when that PR is actually created. Do not pre-populate tracker rows from this plan branch.

### Recommended PR sequence

- **Prerequisite outside R4-04:** R4-03 shared strict-inventory implementation, if it is not already merged when execution starts.
- **R4-04 PR A — durability prerequisite:** narrow R4-05 file/directory durability contract only.
- **R4-04 PR B — dormant evidence foundation:** pure evidence model, persistence facades, checkpoint `status:update`, corruption/reset semantics, guarded mutation primitives. No activation/finalization caller switches yet.
- **R4-04 PR C — behavior integration:** both form factors together: operator inputs, Backup freeze, Restore mutation/completion evidence, post-activation markers, cleanup transaction, repair, BackupSchedule/finalization/integrated-teardown gates, docs and parity.

Suggested branch/worktree names when execution begins:

- `feature/r4-04-durability-prereq` / `.claude/worktrees/r4-04-durability-prereq`
- `feature/r4-04-foundation` / `.claude/worktrees/r4-04-foundation`
- `feature/r4-04-integration` / `.claude/worktrees/r4-04-integration`

---

## Task 0: Revalidate prerequisites before any implementation

**Files to read only:**

- `AGENTS.md`
- `thermos-resolution-plan.md`
- the two R4-04 normative plan files named above
- `docs/plans/2026-07-29-decommission-completion-design.md` (R4-03 authority)
- `docs/plans/2026-07-29-state-integrity-residuals-design.md` (R4-05 authority)
- `docs/development/testing.md`

**Step 1: Perform the mandatory start gate**

```bash
git fetch origin ansible
git status --short
git rev-parse origin/ansible
git merge-base HEAD origin/ansible
```

Expected: clean isolated worktree based on current `origin/ansible`; no protected-file changes.

**Step 2: Hard-gate on R4-03**

Verify the current branch contains a shared strict inventory primitive in both form factors with complete pagination/outcome tests, and that R4-04 callers can consume it without using the current fail-open/advisory list path.

Minimum evidence to require before continuing:

- Python has the R4-03 strict list/read interface and tests for true empty, 404/discovery failure, malformed `items`, transport/auth failure, and complete pagination.
- Collection has the corresponding complete list outcome, reusing/extending `acm_k8s_read_outcome` rather than creating another read abstraction.
- The R4-03 implementation is merged on `origin/ansible`, not merely present in another worktree.

If any prerequisite is absent: **STOP R4-04 implementation.** Execute the governed R4-03 workflow separately; do not copy its code into R4-04.

**Step 3: Record execution baseline**

Record base SHA, plan/spec SHAs, worktree path, declared PR scope, and protected boundary in the builder report/tracker row.

---

# R4-04 PR A — narrow durability prerequisite

## Task 1: Make authoritative state/checkpoint replacement durably acknowledged

**Files:**

- Modify: `lib/utils.py`
- Modify: `tests/test_utils.py`
- Modify: `ansible_collections/tomazb/acm_switchover/plugins/action/checkpoint_phase.py`
- Modify: `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/action/test_checkpoint_phase_runtime.py`

**Scope:** implement only the R4-05 parent-directory durability contract needed before R4-04 journal writes. Do not implement Lease/session ownership, simulation policy, or unrelated R4-05 residuals.

### Step 1: Write failing Python durability tests

Add tests around `StateManager._write_state()` proving:

1. temp-file contents are `fsync`ed before `os.replace`;
2. after successful `os.replace`, the parent directory is opened and `fsync`ed;
3. parent-directory open/fsync failure propagates as a failed critical write rather than being logged/suppressed;
4. no test treats an indeterminate directory durability result as successful persistence.

Run:

```bash
python -m pytest tests/test_utils.py -q -k "fsync or durability or write_state"
```

Expected before implementation: FAIL because the parent directory is not fsynced.

### Step 2: Implement minimal Python durability acknowledgement

In `StateManager._write_state()`:

- keep the existing temp write + file `fsync` + same-filesystem `os.replace` flow;
- after `os.replace`, open the containing directory read-only with the platform-supported directory flags and call `os.fsync()`;
- treat inability to obtain the required acknowledgement as a write failure for safety-authorizing state;
- preserve existing temp cleanup and lock behavior; do not broaden the public StateManager interface.

### Step 3: Write failing collection durability tests

Extend `test_checkpoint_phase_runtime.py` so `_save_checkpoint`/its write helper:

- requires directory fsync after replace;
- returns failure on directory fsync/open error instead of swallowing `OSError`;
- still does no authoritative write in dry-run/check/validate modes.

Run:

```bash
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/unit/plugins/action/test_checkpoint_phase_runtime.py \
  -q -k "fsync or durability or save_checkpoint"
```

Expected before implementation: FAIL because the current parent-directory fsync is best-effort.

### Step 4: Implement minimal collection durability acknowledgement

Remove the best-effort suppression only for the authoritative checkpoint replacement path. Keep the existing atomic temp-file/replace architecture and return a sanitized failure when durability cannot be established.

### Step 5: Verify PR A

```bash
python -m pytest tests/test_utils.py tests/test_run_record.py -q
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/unit/plugins/action/test_checkpoint_phase_runtime.py -q
python -m pytest tests/test_documentation_guardrails.py -q
```

Then run the full relevant root and collection unit gates before opening the PR.

**Commit:**

```bash
git commit -m "fix: require durable R4-04 state replacement"
```

**PR A safety condition:** this PR changes persistence acknowledgement only; it must not add or consume `migration_backups` yet.

---

# R4-04 PR B — dormant evidence foundation

## Task 2: Implement the pure migration-evidence model and parity vectors

**Files:**

- Create: `lib/migration_evidence.py`
- Create: `ansible_collections/tomazb/acm_switchover/plugins/module_utils/migration_evidence.py`
- Create: `tests/fixtures/r4_04_migration_evidence_vectors.json`
- Create: `tests/test_migration_evidence.py`
- Create: `tests/test_migration_evidence_parity.py`
- Create: `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/module_utils/test_migration_evidence.py`

Keep both runtime modules pure: no live Kubernetes calls, no Ansible module execution, no state-file IO.

These two pure modules own AC-27's **evidence-domain** reclassification. Given the method
and selected evidence category, they classify a generic-resource Backup as
`resources_generic` or `activation_resources_generic`; they do not change the repository's
coarse ACM ownership classifiers. Do not widen `ACM_BACKUP_NAME_RE`, add a global generic
member to `ACM_BACKUP_SCHEDULE_TYPES`, or change unrelated finalization/E2E classification
paths for this requirement.

### Step 1: Write failing vector-driven tests

The shared JSON fixture must cover at least:

- all six ACM minor -> controller-contract mappings;
- Backup counter normalization: omitted errors/warnings -> `0`; present non-negative integer retained; null/bool/string/float/negative rejected;
- direct `latest` selection ordering: resource-type prefix -> `{Completed, PartiallyFailed}` raw phase eligibility -> `startTimestamp` descending -> first, then R4 eligibility;
- a `PartiallyFailed` newest Backup blocks instead of selecting an older successful Backup;
- passive-patch auxiliary Backup categories `activation_credentials`, `activation_resources`, and `activation_resources_generic`: controller-selectable inputs are derived with the pinned lane's upstream-first selection algebra, a required auxiliary input that cannot be selected deterministically blocks before mutation, and resume keeps the frozen category identities rather than re-resolving later aliases;
- generic exact-name match;
- generic fallback timestamp parsed from the ordinary Backup **name**, raw `strings.Contains` semantics, non-prefix Contains candidate, ±30 second boundary, raw ambiguity (0/1/>1) before R4 eligibility;
- scoped generic reclassification: a generic name already coarsely recognized through the
  current `resources` ownership pattern becomes `resources_generic` or
  `activation_resources_generic` only inside migration evidence; ordinary resources stay
  `resources`, and tests guard against widening unrelated backup classification;
- exact five-field Velero child evidence validation;
- ownerRef group/kind/name/UID/controller checks without served-version pinning;
- child-to-Backup provenance mapping for legacy and 2.17 passive cohorts, including exact binding to `activation_credentials`, `activation_resources`, or `activation_resources_generic` when those journal categories are consumed;
- 2.17 all-status-empty -> complete owner list and non-empty status -> current/base-`-active` cohort;
- legacy full-owner cohort;
- `EnabledWithErrors` rejected for 2.17 and unknown values rejected for legacy;
- canonical restore projection/fingerprint determinism;
- malformed/partial journal, waiver, cleanup, recovery, and repair records fail closed.

Suggested public pure interfaces in **both** modules:

```python
class MigrationEvidenceError(ValueError): ...

def normalize_backup_evidence(raw: dict, namespace: str) -> dict: ...
def select_latest_backup(backups: list[dict], category: str) -> dict: ...
def select_correlated_backup(backups: list[dict], source_backup_name: str, target_category: str) -> dict: ...
def controller_contract_for_acm_minor(acm_minor: str) -> str: ...
def build_completion_cohort(controller_contract: str, owner_children: list[dict], status_names: dict) -> list[dict]: ...
def validate_velero_child(raw: dict, expected_owner: dict, expected_backup_name: str) -> dict: ...
def canonical_restore_projection(journal: dict) -> dict: ...
def restore_spec_fingerprint(journal: dict) -> str: ...
def validate_migration_journal(candidate: dict) -> dict: ...
def validate_cleanup_transition(previous: dict, candidate: dict) -> dict: ...
def validate_waiver(candidate: dict, expected_names: list[str], scope: str) -> dict: ...
def validate_repair(candidate: dict, journal: dict) -> dict: ...
```

Implement only interfaces that the final tests need; do not create a class hierarchy.

Run first:

```bash
python -m pytest tests/test_migration_evidence.py tests/test_migration_evidence_parity.py -q
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/unit/plugins/module_utils/test_migration_evidence.py -q
```

Expected before implementation: import/test failures.

### Step 2: Implement the minimum pure helpers

Mirror behavior deliberately; do not cross-import form factors. Make the parity test feed identical fixture input to both modules and compare normalized results, decisions, stable error codes, and fingerprints.

### Step 3: Verify and commit

```bash
python -m pytest tests/test_migration_evidence.py tests/test_migration_evidence_parity.py -q
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/unit/plugins/module_utils/test_migration_evidence.py -q
```

**Commit:** `feat: add migration evidence model`

---

## Task 3: Add strict journal persistence facades, `status:update`, corruption persistence, and rewind guards

**Files:**

- Modify: `lib/run_record.py`
- Modify: `tests/test_run_record.py`
- Modify: `tests/test_run_record_guardrails.py`
- Modify: `ansible_collections/tomazb/acm_switchover/plugins/module_utils/checkpoint.py`
- Modify: `ansible_collections/tomazb/acm_switchover/plugins/action/checkpoint_phase.py`
- Modify: `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/action/test_checkpoint_phase_runtime.py`
- Modify: `ansible_collections/tomazb/acm_switchover/tests/unit/test_checkpoint_vocabulary_guardrail.py`
- Create: `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/module_utils/test_migration_checkpoint.py`

### Step 1: Write failing Python facade tests

Add `RunRecord` methods with strict semantics:

```python
def migration_backups(self) -> dict | None: ...
def record_migration_backups(self, candidate: dict) -> None: ...
```

Requirements:

- `migration_backups()` distinguishes absent from invalid and delegates validation to `lib.migration_evidence`;
- `record_migration_backups()` validates the complete candidate first and persists one complete top-level value through the existing critical StateManager write path;
- no production module outside `RunRecord` may read/write the named config key;
- retry/resume never silently rewrites an immutable field.

Tests: absent, valid round trip/reload, invalid present key blocks, unknown schema blocks, critical write failure propagates, guardrail catches any raw production access.

### Step 2: Write failing collection checkpoint tests

Add to `plugins/module_utils/checkpoint.py`:

- the named `migration_backups` vocabulary/parser/validator;
- a reset/rewind helper enforcing: activation/post_activation/finalization rewinds retain the journal; preflight/primary_prep rewind with a journal fails; full reset may remove it;
- `CHECKPOINT_VALID_STATUSES` includes `update`.

For `plugins/action/checkpoint_phase.py`, tests must prove `status:update`:

- requires an existing checkpoint/current phase and matching requested phase;
- accepts operational-data update only;
- rejects `error` and `report_ref`;
- leaves `phase`, `completed_phases`, `phase_status`, `errors`, and `report_refs` byte-for-byte unchanged;
- replaces the entire non-empty `migration_backups` top-level value;
- reports no authoritative state change in check/dry/validate mode.

Corrupt checkpoint behavior must change from **move** to **preserve original + forensic copy**. A subsequent invocation must remain blocked by the original corrupt file until explicit operator reset/removal.

### Step 3: Run targeted tests and implement

```bash
python -m pytest tests/test_run_record.py tests/test_run_record_guardrails.py -q
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/unit/plugins/action/test_checkpoint_phase_runtime.py \
  ansible_collections/tomazb/acm_switchover/tests/unit/plugins/module_utils/test_migration_checkpoint.py \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_checkpoint_vocabulary_guardrail.py -q
```

Expected first run: FAIL on missing facade/update/reset/corruption semantics. Implement only enough to satisfy the accepted schema and tests.

**Commit:** `feat: persist migration evidence through state facades`

---

## Task 4: Add dormant guarded Restore mutation primitives

**Files:**

- Modify: `lib/kube_client.py`
- Modify: `tests/test_kube_client.py`
- Create: `ansible_collections/tomazb/acm_switchover/plugins/modules/acm_restore_guarded_mutation.py`
- Create: `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_restore_guarded_mutation.py`

### Step 1: Write failing Python guarded-mutation tests

Add narrow helpers rather than changing generic merge-patch/delete semantics globally:

```python
def json_patch_custom_resource_guarded(..., patch_ops: list[dict]) -> dict: ...
def delete_custom_resource_preconditioned(..., uid: str, resource_version: str, timeout_seconds: int) -> dict: ...
```

Tests must prove:

- JSON Patch uses `application/json-patch+json` and sends `test` operations for exact UID/resourceVersion/raw ManagedClusters field before `replace`;
- 409/422/precondition conflict is returned as conflict/failure, never silently retried as an unguarded mutation;
- delete builds `V1DeleteOptions(preconditions=V1Preconditions(uid=..., resource_version=...))`;
- dry-run makes no API call and reports predicted change separately from actual mutation;
- exception/result sanitization does not expose kubeconfig/token/response bodies.

### Step 2: Write failing collection module tests

`acm_restore_guarded_mutation` supports exactly:

```yaml
action: patch | delete
api_version: cluster.open-cluster-management.io/v1beta1
kind: Restore
namespace: <ns>
name: <name>
expected_uid: <uid>
expected_resource_version: <resourceVersion>
expected_managed_clusters_backup_name: <raw string>   # patch only
replacement_managed_clusters_backup_name: latest     # patch only
```

The module performs one guarded patch/delete request, supports check mode, returns sanitized `changed`, `would_change`, `conflict`, and minimal identity metadata, and owns no polling or phase policy.

### Step 3: Implement and verify

```bash
python -m pytest tests/test_kube_client.py -q -k "json_patch or precondition"
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_restore_guarded_mutation.py -q
```

Run RBAC impact review: these helpers must use the Restore patch/delete permissions already required. If implementation introduces any new API group/resource/verb/namespace, **STOP** and execute the full RBAC cross-surface review required by `AGENTS.md` before proceeding.

**Commit:** `feat: add guarded Restore mutation primitives`

---

## Task 5: Verify PR B stays behavior-dormant

Before opening PR B, prove no activation/post-activation/finalization caller has been switched to the new foundation yet.

Run:

```bash
python -m pytest tests/test_migration_evidence.py tests/test_migration_evidence_parity.py \
  tests/test_run_record.py tests/test_run_record_guardrails.py tests/test_kube_client.py -q
PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/ -q
python -m pytest tests/test_documentation_guardrails.py -q
```

Inspect the diff: changes should be pure helpers/facades/plugins/tests only. No existing operator workflow should require a migration journal after this PR merges.

**Commit if needed for final foundation cleanup:** `test: lock R4-04 foundation contracts`

---

# R4-04 PR C — parity-preserving behavior integration

## Task 6: Add the exact operator-facing waiver and cleanup-repair inputs; make count/name expectations additive

**Files:**

- Modify: `acm_switchover.py`
- Modify: `lib/validation.py`
- Modify: `tests/test_main.py`
- Modify: `tests/test_validation.py`
- Modify: `ansible_collections/tomazb/acm_switchover/plugins/module_utils/validation.py`
- Modify: `ansible_collections/tomazb/acm_switchover/plugins/modules/acm_input_validate.py`
- Modify: `ansible_collections/tomazb/acm_switchover/roles/common/tasks/resolve_managed_cluster_expectation.yml`
- Modify: `ansible_collections/tomazb/acm_switchover/roles/activation/defaults/main.yml`
- Modify: corresponding post-activation/finalization defaults used by shipped playbooks
- Modify: `ansible_collections/tomazb/acm_switchover/examples/group_vars/all.yml`
- Modify/add collection validation and role-static tests as needed.

### Step 1: Lock the exact Python CLI surface with failing parser/validation tests

Use these names:

```text
--skip-managed-cluster-expectations
--managed-cluster-expectation-waiver-scope {activation,post_activation,both}
--managed-cluster-expectation-waiver-actor TEXT
--managed-cluster-expectation-waiver-reason TEXT
--managed-cluster-expectation-waiver-request-id TEXT   # optional

--repair-migration-cleanup
--cleanup-repair-actor TEXT
--cleanup-repair-reason TEXT
--cleanup-repair-evidence TEXT                         # repeatable, at least one
--cleanup-repair-run-id UUID
--cleanup-repair-operation-id UUID
```

Validation requirements:

- waiver flag requires scope + non-empty actor/reason; request ID optional;
- waiver is rejected if no expected-name predicate exists in the requested scope;
- waiver never changes the recorded expected names/count;
- cleanup repair arguments are accepted only as a complete set and are an acknowledgement request, not evidence of successful delete;
- explicit `--min-managed-clusters N` remains an additional floor; expected names remain present and enforced; `0` does not clear names.

### Step 2: Lock the collection surface

Use:

```yaml
acm_switchover_operation:
  skip_managed_cluster_expectations: false
  managed_cluster_expectation_waiver:
    scope: activation | post_activation | both
    actor: ""
    reason: ""
    request_id: null
  migration_cleanup_repair:
    actor: ""
    reason: ""
    inspected_evidence: []
    run_id: ""
    operation_id: ""
```

Presence/enabling rules and outcomes mirror Python. Update `resolve_managed_cluster_expectation.yml` so explicit minimum never clears effective expected names, and `allow_zero` is false whenever effective names are non-empty.

### Step 3: Run tests, implement, rerun

```bash
python -m pytest tests/test_main.py tests/test_validation.py -q
PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/ -q -k "input_validate or managed_cluster_expectation"
```

**Commit:** `feat: add migration evidence operator inputs`

---

## Task 7: Integrate Python activation with frozen Backup evidence and lane-specific child proof

**Files:**

- Modify: `modules/restore_discovery.py`
- Modify: `modules/activation.py`
- Modify: `acm_switchover.py` only for wiring already validated inputs/effective expectations
- Create: `tests/test_restore_discovery.py`
- Modify: `tests/test_activation.py`
- Modify: `tests/test_main.py`
- Reuse: `lib/migration_evidence.py`, `lib/run_record.py`, R4-03 strict KubeClient reads, guarded mutation helpers.

### Step 1: Write failing activation transaction tests

Cover all mutation kinds and resume:

1. strict passive Restore discovery failure/partial list blocks before create/patch/delete;
2. fresh journal absent -> strict complete Backup inventory -> upstream-first selection -> seven-field freeze -> durable `RunRecord.record_migration_backups()` **before mutation**;
3. each frozen Backup is strict-GET revalidated by namespace/name/UID and by the complete persisted status projection immediately before mutation, comparing `errors`/`warnings` only after the same omitted-counter normalization used during freeze;
4. `passive_patch` fresh precondition requires sync=true, normalized `skip/latest/latest`, exact empty MC status locator, and a strict live read of `spec.cleanupBeforeRestore` that normalizes to the exact `CleanupRestored` value and is bound into the journal before mutation; non-empty locator or cleanup mismatch causes **zero Backup freeze and zero PATCH**;
5. before `passive_patch`, freeze `activation_credentials`, `activation_resources`, and `activation_resources_generic` in addition to `managed_clusters`, using the pinned lane's exact upstream-first selection rules; if any controller-required auxiliary input cannot be selected deterministically, record no accepted mutation intent and issue zero PATCH; resume revalidates these frozen categories and never re-resolves them to later alias targets;
6. immediately before `passive_patch`, a strict live re-read tests that normalized `cleanupBeforeRestore` still equals the journaled value, then the guarded patch tests UID/resourceVersion/raw MC field and replaces only MC with canonical `latest`; cleanup mismatch fails before mutation;
7. `passive_restore` creates one-shot concrete MC with credential/resource skips and sends the journaled normalized `cleanupBeforeRestore` value;
8. `full_restore` uses concrete managed/credential/resource fields, freezes/validates correlated `resources_generic`, and sends the journaled normalized `cleanupBeforeRestore` value;
9. each create response plus mandatory strict post-create read, and each patch response plus strict post-patch read, binds ACM Restore namespace/name/UID/generation/mutation kind/cleanupBeforeRestore/fingerprint and rejects a cleanup mismatch;
10. resume loads/revalidates the journal, checks `cleanupBeforeRestore` at every governed post-read/evidence/revalidation boundary, and never refreezes any managed or passive auxiliary category to later `latest` targets;
11. same-name different Backup UID or status drift blocks;
12. lane-specific child sweep uses strict complete Velero Restore list + exact owner UID filtering, never `.metadata.controller` server selector;
13. legacy 2.12–2.16 requires the legacy owner cohort and ManagedClusters/Credentials/ResourcesGeneric provenance, with each consumed credential/resource/generic child bound to its corresponding frozen `activation_*` evidence;
14. 2.17 reproduces the all-status-empty/otherwise-current+`-active` cohort, requires all current children `Completed`, and binds any consumed credentials/resources/generic child to `activation_credentials`, `activation_resources`, or `activation_resources_generic` respectively;
15. child `spec.backupName` mismatch blocks, including post-PATCH alias race detection and auxiliary child mismatch;
16. ACM phase `Enabled` is accepted only with the full passive-patch conjunctive proof; `EnabledWithErrors` blocks on 2.17; one-shot/full require `Finished`;
17. `restore.completed_at` is written only after every required identity/provenance/completion/name predicate is complete, and written last.
18. cleanup-policy tests prove a pre-PATCH mismatch issues zero PATCH, one-shot and full create bodies send the normalized journal value, post-create/post-patch mismatch blocks, and resume drift blocks.

Run before implementation:

```bash
python -m pytest tests/test_restore_discovery.py tests/test_activation.py tests/test_main.py -q
```

Expected: focused new tests FAIL.

### Step 2: Implement the Python transaction in small internal helpers

Keep live IO in `SecondaryActivation`/KubeClient and pure decisions in `lib.migration_evidence`. Do not create a parallel state layer.

Recommended internal flow names (private, not public API):

```python
_load_or_freeze_migration_journal()
_revalidate_frozen_backups()
_apply_passive_patch_guarded()
_create_passive_restore_from_journal()
_create_full_restore_from_journal()
_collect_and_validate_velero_children()
_record_restore_completion_evidence()
```

### Step 3: Verify and commit

```bash
python -m pytest tests/test_restore_discovery.py tests/test_activation.py tests/test_main.py \
  tests/test_migration_evidence.py tests/test_run_record.py -q
```

**Commit:** `feat: bind Python activation to migration evidence`

---

## Task 8: Integrate collection activation with the same evidence transaction

**Files:**

- Modify: `ansible_collections/tomazb/acm_switchover/plugins/modules/acm_restore_info.py`
- Create: `ansible_collections/tomazb/acm_switchover/plugins/modules/acm_migration_evidence.py`
- Modify: `ansible_collections/tomazb/acm_switchover/roles/activation/tasks/discover_resources.yml`
- Modify: `ansible_collections/tomazb/acm_switchover/roles/activation/tasks/activate_restore.yml`
- Modify: `ansible_collections/tomazb/acm_switchover/roles/activation/tasks/wait_for_restore.yml`
- Modify: `ansible_collections/tomazb/acm_switchover/roles/activation/tasks/main.yml`
- Modify/add unit, integration, and scenario tests for activation.

`acm_migration_evidence.py` is a thin adapter over `plugins/module_utils/migration_evidence.py`; it validates/builds evidence but performs no cluster mutation. Role YAML must consume validated module/checkpoint facts and must not walk raw `operational_data`.

### Step 1: Write failing collection tests

Mirror every Python decision listed in Task 7, plus:

- explicitly freeze `activation_credentials`, `activation_resources`, and `activation_resources_generic` before a passive PATCH through the same pinned-lane upstream-first selection contract; if a required auxiliary input is indeterminate, persist no accepted mutation transition and issue no PATCH; resume reuses/revalidates those frozen categories rather than re-resolving `latest`;
- bind every consumed legacy/2.17 credential/resource/generic Velero child `spec.backupName` to the matching frozen `activation_*` category before completion evidence is accepted;
- strict pre-mutation Backup GET/revalidation compares the seven-field evidence after the same omitted-counter normalization used at freeze time;
- mirror the complete Task 7 `cleanupBeforeRestore` contract: pre-PATCH strict equality with
  zero mutation on mismatch, one-shot/full create bodies carrying the journaled normalized
  value, post-create/post-patch binding, and drift rejection at every resume/revalidation
  boundary;
- `check_mode`/dry-run reports prediction but neither mutates Restore nor persists authoritative journal transitions;
- checkpoint `status:update` receives one complete `migration_backups` mapping per transition;
- passive patch uses `acm_restore_guarded_mutation`, not `kubernetes.core.k8s state: patched`;
- one-shot/full create paths use concrete journaled fields;
- all strict LISTs use the R4-03 complete-outcome module/seam;
- no role/playbook raw checkpoint vocabulary bypass.

### Step 2: Implement and verify

```bash
PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/ -q -k "restore or migration or activation"
PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/integration/ -q -k "activation or restore"
```

### Step 3: Run parity vectors after both form factors are integrated

```bash
python -m pytest tests/test_migration_evidence_parity.py -q
```

**Commit:** `feat: bind collection activation to migration evidence`

---

## Task 9: Make post-activation completion evidence strict and last

**Files:**

- Modify: `modules/post_activation.py`
- Modify: `tests/test_post_activation.py`
- Modify: `ansible_collections/tomazb/acm_switchover/roles/post_activation/tasks/verify_managed_clusters.yml`
- Modify: `ansible_collections/tomazb/acm_switchover/roles/post_activation/tasks/main.yml`
- Modify/add post-activation collection tests/scenarios.

### Step 1: Write failing tests

Prove in both form factors:

- ManagedCluster inventory uses the R4-03 strict complete read; 404/discovery/auth/malformed/partial inventory is failure, not zero clusters;
- effective expected names and explicit minimum are both enforced;
- a valid scoped waiver can satisfy **only** the expected-name predicate and records `outcome: waived`; it never synthesizes `names_verified_at`;
- successful name predicate writes `post_activation.names_verified_at`;
- `post_activation.completed_at` is written only after every later required post-activation operation has succeeded and is the last post-activation evidence transition;
- a later observability/klusterlet/cleanup failure leaves `post_activation.completed_at` absent;
- resume validates existing markers rather than rewriting contradictory evidence.

### Step 2: Implement and verify

```bash
python -m pytest tests/test_post_activation.py tests/test_migration_evidence.py -q
PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/ -q -k "post_activation or managed_cluster"
PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/scenario/ -q -k "post_activation or resume"
```

**Commit:** `feat: gate post-activation on migration evidence`

---

## Task 10: Implement live teardown revalidation and the durable Restore-cleanup transaction

**Files:**

- Modify: `modules/finalization.py`
- Modify: `tests/test_finalization.py`
- Reuse/modify: `lib/kube_client.py`, `lib/migration_evidence.py`, `lib/run_record.py` only as required by finalization tests
- Modify: `ansible_collections/tomazb/acm_switchover/roles/finalization/tasks/cleanup_restores.yml`
- Modify: `ansible_collections/tomazb/acm_switchover/roles/finalization/tasks/enable_backups.yml`
- Modify: `ansible_collections/tomazb/acm_switchover/roles/finalization/tasks/main.yml`
- Modify/add finalization collection unit/integration/scenario tests.

### Step 1: Write failing cleanup state-machine tests

Use the accepted July cleanup states exactly:

```text
not_started -> intent_persisted -> delete_accepted -> completed
                           \-> recovery_required -> repaired
```

Also allow the documented `delete_accepted -> delete_accepted` bounded retry update of only the final resourceVersion/delete timestamp.

Tests must prove:

1. finalization loads/validates the strict journal before any Restore cleanup or BackupSchedule enablement;
2. a fresh live evidence barrier revalidates the journaled ACM Restore UID/generation/mutation kind/cleanupBeforeRestore/fingerprint/backup fields and child completion, then writes `restore.teardown_revalidated_at`;
3. the journaled Restore locator is reserved from generic cleanup classification;
4. cleanup `intent_persisted` is durably written before DELETE;
5. final strict GET immediately before delete validates UID/generation/resourceVersion/spec projection;
6. DELETE supplies both UID and resourceVersion server preconditions;
7. a status-only resourceVersion conflict may cause a bounded **full validation** retry; retry exhaustion blocks; never retry an unguarded delete;
8. an accepted delete records `delete_accepted`, then bounded polling follows the exact UID;
9. first absence is followed by one final strict GET; only confirmed absence writes `absence_verified_at` + `completed_at` together;
10. replacement UID before/during polling/final GET becomes `recovery_required`, replacement is never deleted/adopted;
11. absent-on-resume without completed evidence becomes `recovery_required`, never success;
12. corrupt/unreadable state or malformed cleanup transition blocks;
13. other switchover-owned Restores at different names retain existing generic cleanup behavior only after the journaled locator is safely terminal;
14. BackupSchedule enablement refuses until cleanup is `completed` or validly `repaired` and a fresh strict locator GET proves absence.

### Step 2: Implement the Python and collection state machines

Keep cleanup transitions in the pure evidence model and persistence facades; keep live GET/DELETE/poll in finalization/Kubernetes modules. Replace collection's current generic `kubernetes.core.k8s state: absent` path for the journaled locator with the guarded mutation module. Do not use a name-only delete.

### Step 3: Implement explicit operator repair

Repair is accepted only when:

- current cleanup state is `recovery_required`;
- exact journal `run_id` and `cleanup.operation_id` match the operator request;
- actor/reason are non-empty and at least one inspected-evidence reference is supplied;
- a fresh strict destination read proves the journaled locator absent;
- one atomic durable journal update records the complete repair object;
- repair never deletes/adopts a replacement and never synthesizes delete-accepted/absence timestamps.

### Step 4: Verify and commit

```bash
python -m pytest tests/test_finalization.py tests/test_kube_client.py \
  tests/test_migration_evidence.py tests/test_run_record.py -q
PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/ -q -k "finalization or cleanup or migration"
PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/integration/ -q -k "finalization or cleanup"
PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/scenario/ -q -k "cleanup or recovery or resume"
```

**Commit:** `feat: make Restore cleanup evidence-bound and recoverable`

---

## Task 11: Gate integrated old-hub handling/decommission on the complete evidence bundle

**Files:**

- Modify: `modules/finalization.py`
- Modify: `tests/test_finalization.py`
- Modify: `ansible_collections/tomazb/acm_switchover/roles/finalization/tasks/handle_old_hub.yml`
- Modify: `ansible_collections/tomazb/acm_switchover/roles/finalization/tasks/main.yml`
- Modify/add finalization scenario/static tests.

### Step 1: Write failing gate tests

Before integrated old-hub mutation/decommission, require:

- complete validated Restore identity/provenance/completion bundle;
- `post_activation.completed_at`;
- `restore.teardown_revalidated_at` from this finalization pass;
- cleanup `completed` or validly `repaired`, bound to the same run/operation/Restore/spec/backup tuple;
- final fresh strict locator absence;
- activation and post-activation expected-name predicates passed, or each covered by the explicit scoped waiver.

Verify the waiver bypasses **none** of identity, provenance, completion, post-activation completion, cleanup/recovery/repair, final locator absence, or teardown revalidation.

Standalone decommission remains outside R4-04's transaction and keeps its own target-safety ownership.

### Step 2: Implement the shared gate decision through the migration-evidence helper

Do not duplicate a long YAML/Python boolean expression. Both form factors should call their mirrored pure helper and produce parity-stable missing-evidence reason codes.

### Step 3: Verify and commit

```bash
python -m pytest tests/test_finalization.py tests/test_migration_evidence_parity.py -q
PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/ -q -k "finalization or teardown or migration"
```

**Commit:** `feat: require migration evidence before old-hub actions`

---

## Task 12: Update non-protected operator/developer documentation and parity authorities

**Files to review and update only where behavior actually changed:**

- `README.md`
- `CHANGELOG.md`
- `docs/operations/usage.md`
- `docs/development/architecture.md` and relevant Mermaid diagrams
- `docs/ansible-collection/parity-matrix.md`
- `docs/ansible-collection/behavior-map.md`
- `ansible_collections/tomazb/acm_switchover/README.md`
- `ansible_collections/tomazb/acm_switchover/docs/cli-migration-map.md`
- affected scenario/test-migration catalogs if their mapped behavior changes
- `ansible_collections/tomazb/acm_switchover/examples/group_vars/all.yml`

Document:

- exact new waiver/repair interfaces;
- `latest` permitted only for the passive-patch upstream trigger while accepted provenance remains concrete;
- strict resume/no-refreeze behavior;
- cleanup recovery/repair states and operator action;
- count/name additivity;
- check/dry-run behavior;
- Python/collection parity and independent stores;
- no live-certification claim from fake/unit/scenario tests.

Do **not** modify `docs/ACM_SWITCHOVER_RUNBOOK.md` or `.claude/skills/**` under this plan. If implementation reveals a required protected-file change, stop and request separate operator approval with the proposed line-by-line diff.

Run:

```bash
python -m pytest tests/test_documentation_guardrails.py -q
```

Expected: PASS; the accepted spec-head baseline was 89 passed, but use the current exact-head count rather than hard-coding 89 as a future requirement.

**Commit:** `docs: document R4-04 migration evidence workflow`

---

## Task 13: Full verification, simplification gate, and exact-head convergence

### Step 1: Targeted parity gate

```bash
python -m pytest \
  tests/test_migration_evidence.py \
  tests/test_migration_evidence_parity.py \
  tests/test_activation.py \
  tests/test_post_activation.py \
  tests/test_finalization.py \
  tests/test_run_record.py \
  tests/test_run_record_guardrails.py \
  tests/test_kube_client.py -q

PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/ -q
```

### Step 2: Complete root surface

```bash
python -m pytest tests/ --ignore=tests/release -v -m "not e2e"
```

Run the repository's strict quality/security checks exactly as current `AGENTS.md` / `docs/development/testing.md` require. Do not format `.venv`, generated, artifact, or retained-evidence directories.

### Step 3: Complete collection surfaces 3–7

Run in both repository-tested compatibility lanes (`ansible-core` 2.16.* / Python 3.11 and 2.21.* / Python 3.12), or rely on exact-head hosted CI only after local targeted development passes:

```bash
export ANSIBLE_COLLECTIONS_PATH="$(pwd):${HOME}/.ansible/collections"
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_compatibility_contract.py -q

PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/ -q
PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/integration/ -q
PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/scenario/ -q
```

Then run the exact syntax-check loop from `docs/development/testing.md`, including `set -o pipefail` and the `does not support Ansible version` backstop, and build the collection archive.

### Step 4: Release-helper surface only when relevant and explicitly non-live

If R4-04 changes release-framework helpers or a governing gate requires them, first verify neither `ACM_RELEASE_PROFILE` nor `PYTEST_ADDOPTS` supplies a profile, then run:

```bash
python -m pytest tests/release -q
```

Never cite fake/helper/scenario output as live ACM certification evidence.

### Step 5: RBAC and protected-file checks

```bash
git diff --name-only origin/ansible...HEAD
```

- Protected paths must be absent unless separately approved.
- If any implementation change adds Kubernetes API group/resource/verb/namespace usage beyond already-required permissions, perform the full RBAC cross-surface code/manifests/Helm/docs/tests review before PR creation. Do not assume an existing role is sufficient.

### Step 6: Pre-PR simplification gate

Review every changed file and its immediate collaborators for safe, behavior-preserving simplification. Remove local duplication/unclear indirection without expanding into unrelated cleanup. Rerun every affected targeted test after simplification and record what was simplified or why no safe simplification was available.

### Step 7: Tracker and governed PR workflow

For each actual PR:

1. update `thermos-resolution-plan.md` with its exact row/branch/worktree/status;
2. builder publishes exact verification evidence;
3. independent validator uses a clean checkout/worktree and hard-fails missing prerequisites;
4. PR-comment resolver fetches all comments/reviews/threads, validates each against code, addresses or rejects with evidence, reruns invalidated gates, replies, and resolves only after the fix/reply is pushed;
5. terminal exact-head validation rechecks base/head/merge-base, changed-file scope, protected boundary, required CI, and review threads.

Do not mark ready or merge while any actionable thread remains. A PASS does not itself authorize merge.

---

## Implementation authorization gate

This approved plan deliberately ends before runtime implementation. Its approval does not
bypass Task 0 or the merged R4-03 prerequisite.

Runtime implementation may begin only in a new governed builder session that:

1. re-fetch current `origin/ansible` and repeat Task 0;
2. if R4-03 strict inventory is not merged, stop R4-04 and execute R4-03 first;
3. otherwise create the PR A isolated branch/worktree and its tracker row;
4. use `superpowers:subagent-driven-development` or `superpowers:executing-plans` as required, with TDD and verification checkpoints above;
5. do not carry stale base SHAs, test results, or implementation assumptions from this documentation branch into the builder session.
