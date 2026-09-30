# R4-04 Migration Evidence — PR C Decisions Amendment

**Date:** 2026-09-30
**Status:** operator-directed amendment. After R4-04 PR B (#331) merged, the operator decided on
2026-09-30, in the coordinating session, two questions the governing documents left open:
legacy `passive_patch` hive/cluster credential requests use **branch-aware exclusion**, and an
activation-failure rescue under a migration journal uses **explicit Argo CD recovery**. It is
subject to the governed documentation workflow before any R4-04 PR C implementation relies on it.
**Amends:** `docs/plans/2026-09-30-r4-04-controller-child-evidence-amendment.md` ("child-evidence
amendment"), `docs/plans/2026-08-27-r4-04-current-base-design-amendment.md` ("August
amendment") and, through them, `docs/plans/2026-07-29-migration-evidence-design.md` ("July
design").

## Authority and scope

Where this document conflicts with the child-evidence amendment, the August amendment or the
July design, **this document wins**; where it is silent, those documents remain normative in
that order. It changes no runtime, test, RBAC, manifest, Helm, release-validation,
lab-controller, protected-file, or support behavior, and it does not authorize implementation by
itself. The R4-04 implementation plan remains the execution authority; its normative
specification is now the July design, the August amendment, the child-evidence amendment, and
this document. Both questions were recorded as PR C carry-forwards in #332.

`U<lane>/<file>:<lines>` cites the pinned cluster-backup-operator snapshots of the
child-evidence amendment (2.12 `74b54988`, 2.13 `7a7b240b`, 2.14 `8b489db4`, 2.15 `25b28b76`,
2.16 `9efe77ea`, 2.17 `c8578f94`).

## 1. Legacy `passive_patch` hive/cluster credential requests

### 1.1 Controller behavior

On ACM 2.12–2.16 the sync branch of a `passive_patch` Restore can request `CredentialsHive` and
`CredentialsCluster` Velero Restores in addition to the activation set, when a Credentials or
Resources Backup newer than the one last restored exists
(`U2.12–2.13/restore_controller.go:399-425`, `U2.14–2.16/restore_controller.go:502-528`;
request set `U2.12–2.13/restore.go:607-625`, `U2.14–2.16/restore.go:614-632`). The sync branch
requires valid sync options and ACM phase `Enabled`; creation is staged, so these requests can
appear on a later reconciliation after the PATCH. 2.17 has no hive/cluster request types
(`U2.17/restore.go:53-73`).

For the `latest` option the controller selects the newest eligible **Credentials** Backup and
then inspects that Backup's `spec.orLabelSelectors`: a non-empty list returns that same
Credentials Backup for the hive/cluster request; only an empty list proceeds to exact-name
correlation and the ±30 second fallback with the hive/cluster token
(`U2.12–2.13/restore.go:502-580`, `U2.14–2.16/restore.go:509-587`). A lookup failure for these
optional types is ignored (`U2.12–2.13/restore.go:700-742`, `U2.14–2.16/restore.go:707-749`).

The supported Backup schedules construct non-empty credentials OR selectors and define no
separate hive or cluster schedule (`U2.12/backup.go:138-144,238-280`,
`U2.16/backup.go:138-144,255-280`; the 2.13–2.15 producer sources are not pinned here, so their
producer behavior is inferred from these two and from their identical restore-side shortcut). On a supported installation the hive/cluster requests
therefore **reuse the frozen `activation_credentials` Backup**, and because a generated child
name depends only on the ACM Restore name and the Backup name (child-evidence amendment §4.2),
both requests share the unsuffixed child name of that Backup. A child created by a hive/cluster
request can exist even though no hive or cluster Backup exists.

Blocking every selected hive/cluster candidate would therefore refuse every supported legacy
`passive_patch`. The child-evidence amendment §4.1 hive/cluster block stays specific to
`full_restore`, whose concrete credentials option bypasses the shortcut.

### 1.2 Rule: branch-aware exclusion (new child-evidence amendment §4.3)

For `passive_patch` on the legacy lanes:

1. **Before PATCH**, predict both `CredentialsHive` and `CredentialsCluster` regardless of whether
   the observed controller state suggests the activation-only branch. Resolve their `latest`
   input from the same strictly complete Backup inventory used to freeze
   `activation_credentials`. If the selected Credentials Backup has a non-empty
   `spec.orLabelSelectors`, both requests select that same frozen Backup: permitted, and no
   Backup category is added. Otherwise apply the child-evidence amendment §2 independently with
   the hive and cluster tokens: a **selected distinct Backup** or a **blocking** prediction
   refuses the PATCH with zero ACM Restore mutation; only a proven "no candidate" result permits
   the correlation branch.
2. The decision is made from strict reads at the final pre-PATCH boundary, together with the
   other pre-mutation revalidations, and adds no journal field (`schema_version` stays `2`).
   After the PATCH, the prediction is not recomputed to expand frozen evidence; the completion
   and revalidation requirements in rules 3, 4 and 6 apply. A
   strict-read failure, malformed input, or ambiguous selection is never "no candidate".
3. **At completion** and at every later evidence revalidation, using the strictly complete
   exact-owner child inventory: any child whose `spec.backupName` contains
   `acm-credentials-hive-schedule` or `acm-credentials-cluster-schedule` blocks, **including a
   historical child** — an explicit, documented exception to child-evidence amendment §5.2 that
   restricts installations whose sync Restore history includes the legacy three-Backup format.
   The substring test follows the controller's own correlation predicate
   (`U2.12–2.13/restore.go:561-573`, `U2.14–2.16/restore.go:568-580`).
4. When present, the unsuffixed child generated from the frozen `activation_credentials` Backup
   must be exact-owner-bound, `Completed`, and bound to that Backup; it is recorded in
   `restore.velero_restores.activation_credentials` (duplicate observations collapse only when
   all five fields agree). It is not required: the controller may take the activation-only
   branch.
5. The hive and cluster shortcut requests share the unsuffixed output already represented by
   the legacy predictor's `Credentials` entry for `activation_credentials`. Represent this
   permitted shared output once for name-collision checking; do not add independent hive/cluster
   entries that collide with that existing entry. This does not make the optional unsuffixed
   child required or relax the required status-published `Credentials` child check. All other
   generated-name collisions remain blocking before mutation.
6. A later dedicated hive/cluster child or a changed consumed Backup blocks completion and
   finalization and never adds a category on resume. This detects divergence after the PATCH; it
   does not prevent a child Restore that already ran.

The August amendment's "Journal categories" claim that `passive_patch` freezes every
controller-selectable input is qualified accordingly: legacy hive/cluster requests are admitted
only as reuse of the frozen `activation_credentials` Backup or as a proven "no candidate".

## 2. Activation-failure rescue under a migration journal

### 2.1 Current behavior

With Argo CD management and resume-on-failure enabled, an activation failure resumes the paused
Argo CD Applications on the primary hub and then rewinds to primary preparation so that the
retry re-pauses them: Python clears the pause step and records the retry at `PRIMARY_PREP`
(`PREFLIGHT` for restore-only) in `lib/argocd_resume.py`; the collection issues `status: reset`
of `primary_prep` with `reset_from: primary_prep` in `playbooks/switchover.yml` with
`ignore_errors: true`. Once a journal exists, August §10 forbids that pre-freeze rewind, and the
PR B collection guard refuses it; with `ignore_errors` the refusal is hidden, the checkpoint
keeps `primary_prep` complete, and a retry would continue activation with Argo CD auto-sync live
on the primary hub — while Python would still re-pause. The two form factors diverge.

### 2.2 Rule: explicit Argo CD recovery

1. **Without a migration journal** (the failure happened before the Backup freeze), behavior is
   unchanged in both form factors.
2. **With a journal present** (valid or invalid), neither form factor rewinds to a pre-freeze
   phase. **Before attempting any Argo CD resume mutation**, each durably records a
   re-pause-required marker through its own state or checkpoint owner, **not** in the migration
   journal (the collection can carry it as operational data of the activation `fail`
   transition; `status: update` stays journal-only). If that persistence fails, issue no resume
   mutation. The marker survives a partial resume, a failed resume and process interruption. The
   retry stays positioned at activation and the journal is unchanged.
3. A retry that finds the marker re-pauses the Argo CD Applications with the same pause
   semantics and register as primary preparation **before any further ACM Restore mutation or
   evidence step**, and clears the marker only after the re-pause succeeds. It never replays the
   rest of primary preparation (in particular the BackupSchedule pause).
4. Every refusal and failure on this path is visible: a failed resume, a failed marker write, or
   a failed re-pause fails the run with an operator-actionable message. The collection must not
   hide it with `ignore_errors`, and the original activation failure is still reported.
5. Dry-run and check mode record nothing and report what they would do.

## 3. Implementation-plan changes

- The normative specification list adds this document.
- Task 7 (Python) and Task 8 (collection) implement §1.2 in `passive_patch` legacy activation
  and completion and §2.2 in the activation-failure path (Python `lib/argocd_resume.py`;
  collection `playbooks/switchover.yml` and the activation role).
- Task 12 documents both rules and the §1.2 rule 3 compatibility restriction.
- Carry-forwards (i), (i-b) and the hive/cluster design question of #332 are resolved by this
  document.

## 4. Acceptance-criteria changes

New criteria:

39. Legacy `passive_patch` predicts `CredentialsHive` and `CredentialsCluster` before PATCH:
    the `orLabelSelectors` shortcut reusing the frozen `activation_credentials` Backup is
    permitted; a selected distinct hive/cluster Backup or a blocking prediction issues zero
    PATCH.
40. At legacy `passive_patch` completion, any exact-owner child bound to a dedicated hive or
    cluster credential Backup blocks, including a historical child; the shared unsuffixed child
    of the frozen credentials Backup is bound to `activation_credentials` when present and is
    not required.
41. With a journal, an activation-failure rescue never rewinds to a pre-freeze phase; both form
    factors durably record an Argo CD re-pause marker outside the journal before any resume
    mutation, the retry re-pauses before
    any further activation step and clears the marker only on success, and every failure on the
    path is visible. Without a journal, behavior is unchanged.
42. Tests cover, per legacy lane and in both form factors: the shortcut with no dedicated
    Backups (permitted); an exact and a fallback hive candidate (block); an ambiguous or
    unreadable prediction (blocks); a later dedicated child after an absent decision (blocks
    completion); a historical dedicated child (blocks); shared-name deduplication; and, for the
    rescue, journal-free unchanged behavior, marker recording, re-pause on retry, and a failed
    re-pause blocking visibly. Also test both predictions returning no candidate; cluster-only
    exact and fallback candidates; the shortcut with dedicated Backups present but unselected; and
    a pre-PATCH retry using fresh reads without changing frozen evidence. Recovery tests apply to
    all supported lanes, including 2.17, and cover valid and invalid journals, partial and failed
    resume, marker persistence failure before resume (no resume mutation), interruption after
    resume begins, failed marker clearing, and dry-run/check-mode zero writes; they assert that the
    retry preserves the journal and does not replay BackupSchedule preparation.
