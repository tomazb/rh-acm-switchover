# Review Analysis Report

Date: 2026-04-20
Scope: source-level review of the five findings raised against the restore-only, Argo CD resume, and checkpoint changes in the Python CLI and Ansible collection.

## Executive Summary

All five review findings are substantiated by the current code. The main pattern is that the patch advertises new recovery and restore-only flows, but the runtime state needed to execute those flows is either not validated up front or not persisted for later reuse.

The highest-risk regressions are:

- restore-only Argo CD resume in the Python CLI cannot find its own state file by default
- restore-only preflight in both implementations can succeed without proving any usable backup exists
- the Ansible collection cannot reliably resume standalone Argo CD management or checkpointed passive activation after an interruption

Taken together, these are not cosmetic issues. They break documented operator paths and weaken the safety guarantees that `validate-only`, resume, and checkpoint support are supposed to provide.

## Finding 1: Restore-only Argo CD Resume Cannot Discover State

Severity: P1
Status: verified

Affected paths:

- `acm_switchover.py`
- `lib/validation.py`

### What the code does now

The CLI stores restore-only state under the restore-only filename pattern:

- `_build_default_state_file(None, <secondary>)` produces `.state/switchover-restore-only__<secondary>.json`

That part is correct.

The problem is in resume handling:

- `parse_args()` still declares `--primary-context` as required unless `--restore-only` is present
- `--argocd-resume-only` is a standalone mode, but it does not set `restore_only_requested`
- `_resolve_state_file(..., argocd_resume_only=True)` only probes:
  - the direct `<primary>__<secondary>` filename
  - the reversed `<secondary>__<primary>` filename
- it never probes the restore-only filename `restore-only__<secondary>`

That means the documented follow-up flow for a restore-only run cannot work by default. An operator must both:

- invent a dummy `--primary-context` to satisfy argument parsing
- pass `--state-file` explicitly because the resolver will otherwise search the wrong filenames

### Why this is a real regression

The code comments in `_resolve_state_file()` explicitly say restore-only needs no special handling, but that assumption only holds while performing the original restore-only run. It does not hold for the later `--argocd-resume-only` invocation, because that code path no longer knows it is resuming a restore-only operation.

The CLI contract is also internally inconsistent:

- parser behavior effectively requires a primary context for resume-only
- `InputValidator.validate_all_cli_args()` only documents `--secondary-context` as required for resume-only

So the user-facing behavior is both broken and misleading.

### Operator impact

- documented restore-only Argo CD resume path fails unless the operator manually reconstructs the right state reference
- failure happens before any useful recovery action begins
- a user can believe the tool supports explicit resume while the default path is actually unusable

### Recommended fix

Minimum safe fix:

- make `--primary-context` optional for standalone `--argocd-resume-only`
- teach `_resolve_state_file()` to probe the restore-only candidate when resume-only is requested
- keep the existing ambiguity protection when multiple candidate state files exist
- align CLI validation and help text with the actual supported resume inputs

### Test coverage to add

- resume-only resolves `.state/switchover-restore-only__<secondary>.json` when resuming a restore-only run
- resume-only does not require `--primary-context`
- ambiguity handling still fails safely when multiple matching state files exist

## Finding 2: Python Restore-only Preflight Does Not Validate Backup Presence

Severity: P2
Status: verified

Affected path:

- `modules/preflight_coordinator.py`

### What the code does now

In `PreflightValidator.run_all()`:

- restore-only mode skips all primary-side backup validators
- the skipped validators include:
  - `BackupValidator`
  - `BackupScheduleValidator`
  - primary `BackupStorageLocationValidator`
  - `ClusterDeploymentValidator`
  - `ManagedClusterBackupValidator`
- the only restore-related validation that still runs is secondary `BackupStorageLocationValidator`

That means restore-only validation proves only that the destination hub has a visible and available `BackupStorageLocation`. It does not prove that the bucket actually contains any usable backup artifacts.

### Why this is a real regression

Activation later relies on ACM restore resources using `latest` backup names. A fresh hub pointed at an empty bucket, a wrong bucket, or a bucket missing the relevant ACM artifacts can therefore pass `--restore-only --validate-only` and only fail later when activation or restore wait logic runs.

That defeats the purpose of a restore-only preflight. The preflight should be the point where the operator learns that recovery material is missing.

### Operator impact

- `--validate-only` can produce a false sense of safety for restore-only operations
- restore-only runs fail later, after the operator has already relied on the preflight result
- the failure mode moves from early validation into activation, which is harder to diagnose and operationally riskier

### Recommended fix

Add a restore-only specific backup validator on the secondary side that confirms at least one usable backup set is present for the restore target. At minimum, it should fail when no restorable backup artifacts are visible through the configured storage location.

If the implementation can distinguish ACM backup families, the check should verify the artifact set needed by the full restore path rather than only checking for any Velero `Backup` object.

### Test coverage to add

- restore-only validate-only fails when secondary BSL is available but no backups are present
- restore-only validate-only fails when backup artifacts are incomplete or unusable
- restore-only validate-only still passes when valid backup artifacts are present

## Finding 3: Collection Restore-only Preflight Also Skips Backup Existence Checks

Severity: P2
Status: verified

Affected paths:

- `ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/discover_resources.yml`
- `ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/validate_backups.yml`
- `ansible_collections/tomazb/acm_switchover/playbooks/restore_only.yml`

### What the code does now

In the collection:

- `discover_resources.yml` only reads `acm_primary_backups_info` when `restore_only` is false
- in restore-only mode, it seeds empty primary backup facts instead of discovering backup artifacts on the secondary side
- there is no secondary backup discovery fact analogous to `acm_secondary_backups_info`
- the first critical backup existence check in `validate_backups.yml` is gated by:
  - `when: not (acm_switchover_operation.restore_only | default(false))`

So the collection has the same blind spot as the Python implementation, but more concretely: it does not even gather the data needed to validate restore-only backup presence.

### Why this is a real regression

`restore_only.yml` runs preflight, allows the workflow to continue, and later activation builds a restore plan using `backup_name: latest`. If the target bucket is empty or wrong, the preflight phase can still report success and the operator only learns the truth during activation.

This is exactly the kind of problem preflight is supposed to catch.

### Operator impact

- `ansible-playbook tomazb.acm_switchover.restore_only` can report clean preflight status against an unusable bucket
- automation users and AAP operators get a false-positive readiness signal
- later failure happens in activation, not in the dedicated validation phase

### Recommended fix

Minimum safe fix:

- add restore-only discovery of backup artifacts on the secondary side
- add a critical validation result that fails when no usable backup artifacts are visible for restore-only
- keep the existing primary-hub checks skipped in restore-only mode, but replace them with a secondary restore-artifact check rather than dropping backup validation entirely

### Test coverage to add

- restore-only preflight report contains a failing result when the bucket is empty
- restore-only preflight report passes when secondary-side backup artifacts exist
- validate-only in restore-only mode exits non-zero on missing backup artifacts

## Finding 4: Collection Passive Activation Resume Is Blocked After Delete-and-Create Activation

Severity: P2
Status: verified

Affected paths:

- `ansible_collections/tomazb/acm_switchover/roles/activation/tasks/verify_passive_sync.yml`
- `ansible_collections/tomazb/acm_switchover/roles/activation/tasks/main.yml`
- `ansible_collections/tomazb/acm_switchover/plugins/modules/acm_restore_info.py`

### What the code does now

For passive switchover, `roles/activation/tasks/main.yml` always includes `verify_passive_sync.yml` before activation.

`verify_passive_sync.yml`:

- selects a restore with `syncRestoreWithNewBackups=true`
- asserts that such a restore exists
- fails immediately if it does not

At the same time, `acm_restore_info.build_restore_activation_plan()` already supports the valid resume state that appears after `activation_method: restore` deletes the passive restore and creates `restore-acm-activate`:

- for passive `activation_method == "restore"`, it sets the wait target to `restore-acm-activate`
- it prefers `activation_restore` if present
- if `restore-acm-activate` already exists, it can continue without recreating it

So the lower-level planning logic already understands this resume scenario. The top-level activation role blocks it before the planner can use that information.

### Why this is a real regression

An interrupted run can legitimately leave the cluster in this state:

- passive sync restore deleted
- activation restore already created
- wait phase not yet complete

On rerun, the current activation role re-enters at `verify_passive_sync.yml`, sees no sync-enabled restore, and aborts before it can resume waiting on the activation restore that already exists.

This breaks checkpoint/retry behavior for a supported activation mode.

### Operator impact

- interrupted passive activation with `activation_method: restore` is not safely resumable
- checkpoint support is weaker than advertised
- users can be forced into manual cluster-state reasoning during recovery

### Recommended fix

Minimum safe fix:

- stop requiring a sync-enabled passive restore when an activation restore already exists
- either:
  - fold the selection logic into `acm_restore_info` entirely, or
  - extend `verify_passive_sync.yml` to accept either a valid passive restore or an existing activation restore for resume

The important rule is that reruns must tolerate the valid intermediate state created by delete-and-create activation.

### Test coverage to add

- rerun during passive activation with existing `restore-acm-activate` resumes successfully
- rerun still fails when neither passive restore nor activation restore exists
- `activation_method: patch` behavior remains unchanged

## Finding 5: Collection Does Not Persist Generated Argo CD run_id For Later Resume

Severity: P1
Status: verified

Affected paths:

- `ansible_collections/tomazb/acm_switchover/roles/argocd_manage/tasks/discover.yml`
- `ansible_collections/tomazb/acm_switchover/roles/argocd_manage/tasks/resume.yml`
- `ansible_collections/tomazb/acm_switchover/playbooks/switchover.yml`
- `ansible_collections/tomazb/acm_switchover/playbooks/restore_only.yml`
- `ansible_collections/tomazb/acm_switchover/plugins/action/checkpoint_phase.py`
- `ansible_collections/tomazb/acm_switchover/docs/artifact-schema.md`

### What the code does now

During Argo CD pause:

- `discover.yml` generates a random `run_id` with `set_fact` if one was not provided
- `pause.yml` uses that `run_id` to annotate Applications with `acm-switchover.argoproj.io/paused-by`

During later resume:

- `resume.yml` requires `acm_switchover_argocd.run_id` or `acm_switchover_execution.run_id`
- if neither is present, it fails with:
  - `Cannot resume Argo CD applications without a run_id`

The persistence gap is that the generated `run_id` is not written anywhere durable:

- the switchover and restore-only reports only aggregate phase result payloads
- search of the playbooks and roles shows no persistence of `acm_switchover_argocd.run_id`
- checkpoint writes only phase status, errors, report refs, and a few selected operational fields from other phases
- current checkpoint writes do not store Argo CD pause metadata

This is especially notable because `docs/artifact-schema.md` explicitly describes `operational_data` as a place for future runtime state such as Argo CD pause metadata, but the implementation does not use it for that purpose.

### Why this is a real regression

The collection recommends explicit follow-up resume with:

- `ansible-playbook tomazb.acm_switchover.argocd_resume`

But if the operator did not manually supply a `run_id` during the original pause, the generated value disappears when that play exits. The later standalone resume run has no way to know which applications it is allowed to unpause.

The error message in `resume.yml` even suggests using the switchover report to find the `run_id`, but the report does not currently contain it.

### Operator impact

- explicit standalone Argo CD resume is not usable unless the operator already injected a `run_id`
- resume instructions in the playbooks are incomplete in practice
- report and checkpoint contracts do not preserve the data needed for safe resume

### Recommended fix

Minimum safe fix:

- persist Argo CD pause metadata to a durable artifact
- include at least:
  - `run_id`
  - paused application identifiers
  - which hub(s) were paused
- write that data into either:
  - switchover/restore-only report payloads, or
  - checkpoint `operational_data`, or
  - both

If checkpointing is meant to support real recovery, both report and checkpoint persistence are justified:

- report for operator visibility
- checkpoint for machine-readable resume

### Test coverage to add

- generated `run_id` is present in the report artifact after pause
- checkpoint resume can recover the persisted `run_id`
- standalone `argocd_resume.yml` succeeds after a prior pause without the operator manually supplying `run_id`

## Cross-Cutting Themes

These findings point to three broader issues in the patch:

1. New modes were added without carrying their state contracts through resume paths.
2. Validation logic was reduced for restore-only without adding an equivalent replacement check.
3. The collection and Python implementations are drifting in recovery behavior.

The Python activation code already tolerates the "passive restore deleted, activation restore exists" state. The collection currently does not. That kind of divergence is high risk because operators will assume both implementations support the same recovery semantics.

## Recommended Fix Order

1. Fix restore-only backup validation in both implementations.
2. Fix Python CLI restore-only Argo CD resume state discovery.
3. Fix collection activation resume for `activation_method: restore`.
4. Persist Argo CD pause metadata in collection artifacts and checkpoints.
5. Align docs, CLI help, and artifact schema with the final behavior.

This order restores safety first, then restores operability of the broken resume paths.

## Suggested Validation Plan After Fixes

- Python CLI:
  - run validation tests for `--argocd-resume-only` and `--restore-only`
  - add a restore-only resume state-file resolution test
  - add restore-only preflight tests for missing backup artifacts

- Ansible collection:
  - add unit tests for restore-only preflight with empty and populated backup sets
  - add activation resume tests covering an existing `restore-acm-activate`
  - add artifact/checkpoint persistence tests for generated Argo CD `run_id`

- End-to-end confidence:
  - simulate an interrupted passive activation after passive restore deletion and verify rerun recovery
  - simulate restore-only preflight against an empty bucket and verify failure occurs before activation
  - simulate Argo CD pause in one run and standalone resume in a later run without operator-supplied `run_id`

## Bottom Line

The review comments are materially correct. The patch should not be treated as safe or complete until the missing restore-only validation and missing resume-state persistence are fixed. The affected paths are not edge cases; they are exactly the flows the new feature set claims to support.
