# Thermos Ansible Review Resolution Plan

> **For agentic workers:** REQUIRED SUB-SKILLS: Use `superpowers:using-git-worktrees` before starting each implementation PR. Before changing code for any planned Thermos slice, use `superpowers:brainstorming` to explore the current context, compare approaches, and write an approved design/spec for that slice; then use `superpowers:writing-plans` to turn the approved design into the implementation plan. Use `superpowers:executing-plans` or `superpowers:subagent-driven-development` only after the design/spec and implementation plan are written and approved. Update this file in every Thermos PR.

**Goal:** Resolve the validated findings captured from the operator-supplied external Thermos Ansible review through isolated, reviewable branches without parity drift between the Python CLI and the Ansible collection.

**Architecture:** Treat the external report as a hypothesis source, not an authority. The original report may exist locally as an untracked `thermos_ansible_review.md`, but it is not required in a fresh checkout; this tracker is the self-contained resolution source. Every finding must stay tied to source evidence, tests, and documentation changes. Each PR uses a dedicated worktree and branch, updates this tracker, and preserves the dual-supported parity contract unless explicit operator approval records an intentional divergence.

**Tech Stack:** Python CLI, Ansible collection roles/playbooks/modules, pytest, GitHub PRs, `.claude/worktrees/` git worktrees.

---

## State Tracking Rules

- Status values: `planned`, `in_progress`, `ready_for_review`, `merged`, `blocked`, `deferred`.
- Findings tracked as GitHub issues rather than PR rows additionally use
  `open/deferred`, `open/split`, and `open/design track`. These describe an
  issue's disposition, not a PR slice's progress, and are the correct values in
  the deferred-follow-up and `H3` tables.
- A PR branch may mark only its own row `in_progress` or `ready_for_review`.
- Mark a row `merged` only after the PR has merged into `ansible` and the next branch is created from the updated base.
- Keep one branch and one worktree per PR slice.
- Do not start implementation for a planned Thermos slice until a slice-specific design/spec exists, has been reviewed, and its implementation plan is written from that approved design.
- A PR row should move from `planned` to `in_progress` only after the spec/design gate above is complete.
- Update the `Last Updated` field whenever this tracker changes.
- Do not modify protected runbook files or `.claude/skills/**/*.skill.md` without explicit operator approval.
- Do not intentionally change Python/Ansible parity status without explicit operator approval and repo documentation updates.

## Spec And Design Gate

Before implementation begins for any remaining Thermos slice:

1. Use `superpowers:brainstorming` to explore the current codebase context, ask clarifying questions as needed, compare 2-3 viable approaches, and present the recommended design.
2. Save the approved design/spec for the slice so the acceptance criteria are explicit before code changes start.
3. Use `superpowers:writing-plans` to create the implementation plan from that approved design/spec.
4. Treat the approved design/spec as the verification source of truth: implementation is not complete until the verification evidence shows the delivered behavior matches the accepted design, not merely that tests pass.

This gate applies to the open `SSA-01`-`SSA-10`, `R3-*`, and `TR2D-*`
boundaries and to any new Thermos follow-up slice added later. It no longer
applies to the deep-scan queue: every implementation row in the PR Sequence
table (`PR 01`-`PR 47` and `H1`) is `merged`. `PR 48` is this
tracker-maintenance correction and is `ready_for_review`, which means only
that the builder and review-comment resolver passes are complete. GitHub
readiness is separate, and every branch-head change requires fresh exact-head
independent validation before a merge-readiness assessment.

**Last Updated:** 2026-07-29

## Post-Merge Revalidation (2026-06-03)

`ansible` HEAD at that time, `ac041f6`, included merged Thermos PRs 17-20 (`#89`, `#90`, `#91`, `#92`). A focused source revalidation confirmed:

- `F31` is resolved: path-safety now routes through canonical `path_safety` helpers plus adversarial parity coverage.
- `F34` is resolved: Python and collection klusterlet remediation now patch/create `bootstrap-hub-kubeconfig`; managed-cluster RBAC/docs were realigned to `patch`.
- `F35` is resolved: Helm rendering now rejects mutating `rbac.customValidatorRules` verbs before template output.
- `F37` is resolved: standalone collection `argocd_resume.yml` validates checkpoint hub UID identity against live hubs before resuming Applications.
- Historical note: `F38` was the first residual follow-up after this snapshot and
  was later resolved by `PR 21`. The current source of truth is the resolved
  validation matrix and PR sequence below.

## Deep-Scan Follow-Up Queue (2026-06-04)

Validated follow-up findings from the Graphify-assisted deep scan and paired Thermos review passes.
Status after merged follow-up PRs 22-31:

- `F39` Resolved by `PR 22`: Python Argo CD resume-only now fails closed for legacy state files that recorded paused Applications before `hub_identities` existed.
- `F40` Resolved by `PR 23`: Python dry-run Argo CD management now performs discovery and blocker reporting instead of skipping directly to no-op behavior.
- `F41` **Resolved after regression correction** by `PR 24` and PR [#200](https://github.com/tomazb/rh-acm-switchover/pull/200): the Python scoped-discovery behavior remains correct, and PR #200 repaired the collection no-op with distinct query/publication ownership, complete positive validation, and non-mock retry/resume coverage. Exact head `0bc1a4b6701508f6c3d4cd898515d82b8a29b6a3` merged as `786f8325493c6086e136cb9694a9997557f12e02`.
- `F42` Resolved by `PR 25`: Python RBAC preflight no longer expands to avoidable serial SelfSubjectAccessReview probes for each repeated tuple.
- `F43` Resolved by `PR 26`: Release runtime parity now compares real resume, Argo CD, and RBAC/bootstrap outcomes instead of mostly artifact metadata.
- `F44` Resolved by `PR 27`-`PR 31`: `PR 27` extracted runtime/bootstrap,
  docs-only `PR 28` mapped the remaining seams, and `PR 29`-`PR 31` completed
  operation/phase-flow runners, Argo CD resume safety, and CLI outcome/report
  orchestration.

Execution order for the deep-scan queue:

1. `PR 22` - fail-closed Python Argo CD resume validation for legacy state and shared resume wiring
2. `PR 23` - Python dry-run Argo CD discovery and blocker parity
3. `PR 24` - namespace-scoped Argo CD discovery performance hardening
4. `PR 25` - RBAC preflight scaling without reporting regressions
5. `PR 26` - deeper runtime parity guardrails before any large refactor
6. `PR 27` - orchestrator-first runtime/bootstrap extraction
7. `PR 28` - tracker/spec map for the remaining `F44` seams
8. `PR 29` - operation dispatch and phase-flow runner extraction
9. `PR 30` - Argo CD resume safety extraction
10. `PR 31` - CLI outcome/report orchestration extraction

`F44` started only after `F39` through `F43` merged and revalidated. That gate
cleared on 2026-06-07 after `PR 26` merged; `PR 27` extracted the
runtime/bootstrap seam, docs-only `PR 28` recorded the remaining slice map, and
`PR 29`-`PR 31` completed the three mapped implementation seams.

## Thermos Review #1 Revalidation (2026-06-13)

`ansible` HEAD at that time, `f52a19d4`, was revalidated against the operator-supplied
Thermos review captured in
`docs/plans/2026-06-13-thermos-ansible-review-findings.md`.

- `B1`, `H1`, `H2`, `H3`, `M2`-`M5`, and `L2`-`L7` remain real.
- `B1` is a low-risk cleanup only: old-hub `MultiClusterObservability` deletion is
  already documented, parity-tested, and no longer gated by the deprecated
  `--disable-observability-on-secondary` flag.
- `M1` is lower value after `PR 29`-`PR 31` extracted the major orchestration
  seams.
- `L1` is already covered by path-safety tests; no immediate PR is planned.

Historical follow-up order recorded after `PR 32` (all completed except the
separate `H3` design track):

1. `H2` - add custom-resource access helpers and collapse repeated
   group/version/plural call-site boilerplate.
2. `H1` - derive the Python validator RBAC table from the Python operator table
   while keeping cross-surface parity tests.
3. `M4` - add `StateManager.get_completed_steps()` /
   `get_step_timestamp(name)` and remove direct `StateManager.state` reach-through.
4. `H3` - decompose large modules only through separate design-gated PRs.

Current disposition: `H1` merged through Python RBAC unification PR
[#148](https://github.com/tomazb/rh-acm-switchover/pull/148). `PR 34` / GitHub
PR [#127](https://github.com/tomazb/rh-acm-switchover/pull/127) merged and
resolved the `modules/**` sub-scope of `H2` / `R2-H2`; the overall finding
remains partial because the `lib/rbac_validator.py` residual remains, and the
guardrail blind spot is tracked by `R3-T12`. `M4` / `R2-M4` merged through
`PR 35` / GitHub PR
[#128](https://github.com/tomazb/rh-acm-switchover/pull/128). `H3` remains open
as the design-gated structural track in issue
[#158](https://github.com/tomazb/rh-acm-switchover/issues/158).

For tracker status, the accepted Review #2 scopes supersede the earlier proposed
implementation techniques: the delivered `modules/**` portion of `H2` /
`R2-H2` through `PR 34` records centralized API tuple constants, not creation
of typed custom-resource accessors; the remaining `lib/rbac_validator.py`
literal duplication keeps the overall finding partial. Closing `M4` / `R2-M4`
through `PR 35` records the canonical phase-name mapping, not addition of
`StateManager` completed-step accessors. Those earlier techniques remain
historical context and are not separate open findings in this reconciliation.

## Thermos Review #2 (2026-07-02)

Full-branch deep review (798 commits, 684 files, ~123.5k insertions, `ansible` vs
`main`) using 14 chunked, paired `thermo-nuclear-review-subagent` +
`thermo-nuclear-code-quality-review-subagent` passes (one quality pass for the
release validation framework stalled after ~50 minutes with zero progress and
was replaced by a narrower bounded retry rather than re-run as-is). Full findings,
evidence, and the disputed-finding resolution are in
[`docs/plans/2026-07-02-thermos-ansible-review-2-findings.md`](docs/plans/2026-07-02-thermos-ansible-review-2-findings.md).

Headline results:

- All previously tracked open items (`H1`, `H2`, `H3`, `M1`-`M5`, `L2`, `L4`,
  `L6`, `L7`) were re-confirmed still open and unchanged, with two
  (`M2`, `M5`) reprioritized upward after being shown to be net-new debt added
  within this branch rather than legacy carryover.
  **Revalidation note (2026-07-26):** this list silently dropped `L3` and `L5`,
  which Review #1 had confirmed real, with no rationale recorded here or in the
  Review #2 findings document. Both were re-verified and are still open. None of
  the items in this bullet was ever given a matrix row, so all of them were
  untracked between 2026-07-02 and 2026-07-26; they now have rows. `M2` in
  particular was later mis-credited as resolved by `PR 40` — see the `M2` row.
- 12 new medium/high findings were validated with independent source
  verification (not just subagent claims): `R2-H1` (unbounded delete API calls
  on the PRIMARY_PREP critical path), `R2-H2` (a sharper, quantified framing of
  the existing `H2`: `MANAGED_CLUSTER_API_GROUP` was used in only 1 of the 49
  call sites the review counted in `modules/`; the delivered `PR 34` scope grew
  to 56 after 7 more were found in `modules/preflight/`), `R2-H3` (new
  ~140-line RBAC validation duplication on the
  Ansible side, mirroring `H1`), `R2-M1` (resolves a genuine disagreement
  between the two chunk-5 subagents about check-mode `changed` reporting
  across three collection "plan" modules — confirmed `acm_preflight_report.py`
  has a distinct, self-contained bug, and the other two modules have a real
  but architecturally different role-aggregation gap versus the collection's
  documented native-check-mode contract), `R2-M2` through `R2-M5`,
  `R2-H4`/`R2-M6`-`R2-M8` (release validation framework: a 1199-line
  `orchestrator.py` with a triplicated short-circuit pattern in its very first
  commit, plus 70%-duplicated adapter execution logic despite an existing
  shared contract), and 9 `R2-L*` low-severity items.
- No new Blocker-severity findings; `R2-H1`, `R2-H3`, and `R2-M2` are the
  highest-value safety/RBAC-adjacent items in the queue below.

Historical follow-up order after `PR 33` (the `PR 34`-`PR 47` main queue is now
complete):

1. `PR 34` - `R2-H2`: route the remaining hardcoded API-group/version/plural
   literals in `modules/` through `MANAGED_CLUSTER_API_GROUP` (and companion
   constants where they exist). Delivered scope was 56 literals: the 49
   top-level sites the review counted plus 7 found in `modules/preflight/`
   during the red-test run.
2. `PR 35` - `R2-M4`: deduplicate `lib/utils.py` `REPORT_PHASE_NAMES` and
   `lib/workflow.py` `_CANONICAL_RESUME_START_PHASES` into one shared mapping.
3. `PR 36` - `R2-H1`: add request timeouts to `delete_configmap`/`delete_pod`
   and thread an explicit timeout through `primary_prep.py`'s ACM ≤2.11
   BackupSchedule delete call.
4. `PR 37` - `R2-M1` (part 1): fix `acm_preflight_report.py`'s check-mode
   `changed` override to match its `acm_report_artifact.py` sibling.
5. `PR 38` - `R2-M1` (part 2): resolve or explicitly document the
   native-Ansible-check-mode `changed` gap in `pause_backups.yml` /
   `activate_restore.yml` against the
   `ansible_collections/tomazb/acm_switchover/docs/variable-reference.md`
   "native check mode is non-mutating" contract.
6. `PR 39` - `R2-H3`: deduplicate the Ansible RBAC validation task file's
   primary/secondary blocks; sequence against the still-queued Python `H1`
   unification so both sides land on a consistent approach.
7. `PR 40` - `R2-M3`: extract the near-duplicate
   `_wait_for_restore_deletion`/`_wait_for_primary_restore_deletion` methods
   into `lib/waiter.py`. (This row originally also claimed Review #1 `M2`; that
   credit was wrong and was corrected on 2026-07-26 — see the `M2` matrix row.)
8. `PR 41` - `R2-M5`: factor the 4x-duplicated Ansible summary-path resolution
   logic into one shared `set_fact`/filter.
9. `PR 42` - `R2-M2`: add resume-path re-validation of Velero-restore
   staleness in `modules/activation.py` for the crash-mid-verification case.
10. `PR 43` - `R2-L*` batch: bundle the remaining low-severity items
    (`R2-L1`, `R2-L3`-`R2-L9`) into one cleanup PR, splitting into a second PR
    only if review feedback indicates the diff is too large for one review.
11. `PR 44` - `R2-H4`: extract `tests/release/orchestrator.py`'s triplicated
    short-circuit finalize blocks in `_run_release_certification` into one
    helper.
12. `PR 45` - `R2-M7`: extract the duplicated primary/secondary RBAC
    certification handling in `tests/release/orchestrator.py` into a loop over
    a single helper.
13. `PR 46` - `R2-M8`: deduplicate the required-vs-forbidden permission
    evaluation loops in `tests/release/checks/rbac_certification.py` into one
    polarity-parameterized helper.
14. `PR 47` - `R2-M6`: extract the ~70%-duplicated subprocess/timeout/artifact
    execution logic shared by `tests/release/adapters/ansible.py`, `bash.py`,
    and `python_cli.py` into a shared helper in `adapters/common.py`.

`PR 44`-`PR 47` are independent of `PR 34`-`PR 43` (different subsystem:
release validation tooling, not the live switchover code paths) and, being
release-tooling-scoped, carry no operator-facing safety risk; they can be
implemented in any order relative to the rest of this queue, including in
parallel by a different worker.

The Review #2 queue extended the earlier backlog rather than replacing it.
`H1` and `M4` are complete through GitHub PR #148 and tracker `PR 35` / GitHub
PR #128 respectively. `PR 34` / GitHub PR #127 completed the `modules/**`
sub-scope of `H2` / `R2-H2`; the overall finding remains partial because
`lib/rbac_validator.py` retains seven literals and the scan-root guardrail does
not cover them (`R3-T12`). `H3` remains the separate structural item tracked in
issue #158.

## Current Completion Summary (2026-07-25)

### Completed main queue

- The main Thermos Review #2 `PR 34`-`PR 47` queue is complete; every row is
  merged into `ansible`.
- Python `H1` RBAC unification is complete through GitHub PR #148, and
  `M4` / `R2-M4` is complete through GitHub PR #128. GitHub PR #127 completed
  only the `modules/**` sub-scope of `H2` / `R2-H2`; the overall finding
  remains partial because the `lib/rbac_validator.py` residual remains, with
  its guardrail blind spot tracked by `R3-T12`.
- `F44` is complete through `PR 27`-`PR 31`: GitHub PR #102 extracted
  runtime/bootstrap, GitHub PR #103 recorded the remaining slice map, GitHub PR
  #104 extracted operation and phase-flow runners, GitHub PR #106 extracted Argo
  CD resume safety, and GitHub PR #107 extracted CLI outcome/report orchestration.

### Newly planned security and stability follow-up

- The independently validated Security & Stability Audit contributes 17
  actionable findings grouped into 10 design-gated `SSA-*` resolution slices.
- `SSA-01` remains the highest-impact P1 invariant. No `SSA-*` slice has a PR
  number, branch, or worktree until its slice-specific design and plan are
  approved.
- A 2026-07-20 source revalidation confirmed that all ten SSA slices remain
  incomplete/planned; some individual acceptance criteria are already
  satisfied as recorded in the per-slice notes.

### Deferred low-severity follow-ups

`PR 43` deliberately resolved only `R2-L3`, `R2-L4`, `R2-L5`, the `R2-L7`
checkpoint-guard subitem, and `R2-L9`. The remaining low-priority items are
separate open issues so future PR numbers are assigned only after each
design/spec and implementation sequence is approved.

| Finding | Issue | Status | Safety classification |
| --- | --- | --- | --- |
| R2-L1 | [#152](https://github.com/tomazb/rh-acm-switchover/issues/152) | open/deferred | waiter behavior preservation |
| R2-L6 | [#153](https://github.com/tomazb/rh-acm-switchover/issues/153) | open/split | destructive decommission path |
| R2-L7a | [#154](https://github.com/tomazb/rh-acm-switchover/issues/154) | open/split | observability polling/targeting |
| R2-L7b | [#155](https://github.com/tomazb/rh-acm-switchover/issues/155) | open/split | RBAC/Helm safety-sensitive |
| R2-L7c | [#156](https://github.com/tomazb/rh-acm-switchover/issues/156) | open/split | RBAC bootstrap/parity-sensitive |
| R2-L8 | [#157](https://github.com/tomazb/rh-acm-switchover/issues/157) | open/deferred | shell/kubeconfig safety |

### Newly planned Thermos Review #3 follow-up

- The 2026-07-25 three-agent full-branch review originally claimed 40 findings.
  Revalidation added two raw claims, folded `R3-P6b` into `R3-P6`, and leaves
  41 unique IDs: 37 actionable, 1 optional hardening, 2 rejected/non-actionable,
  and 1 routed to the existing `H3` track. See
  **Thermos Review #3 (2026-07-25)**.
- The delivery sequence begins with the bounded `R3-01` / `TR2D-01` and
  `R3-02` regression corrections. `SSA-01` remains the highest-impact P1
  wrong-target safety invariant; distinguishing delivery order from impact
  prevents either statement from demoting the other.
- Two `R3-*` findings are regressions introduced by merged Thermos PRs
  (`R3-A1` from `F41`/`PR 24`, `R3-P1` from `F2`/`PR 03`). Their resolutions
  must not reopen the original findings.
- No `R3-*` slice has a PR number, branch, or worktree until its
  slice-specific design and plan are approved.

### Revalidation status (2026-07-26)

- A full-file revalidation against `ansible` HEAD `4fed598c` re-verified every
  source, status, and pointer claim in this tracker except the `R3-*` findings,
  which were validated one day earlier. See **Revalidation (2026-07-26)**.
- 19 of 21 re-checked resolved rows hold (20 of 22 counting `R2-M1`'s two parts
  separately). `F41` is now recorded as partially
  regressed and `R2-H2` as partial-as-delivered.
- Ten Review #1 matrix rows were added: nine orphaned findings (`M1`, `M3`,
  `M5`, `L2`-`L7`) plus `M2`, which was a separate mis-credit rather than an
  orphan. All were open but
  untracked since 2026-07-02 now have Finding Validation Matrix rows. All nine
  are still open. The claim that the main queue is complete refers to the
  `PR 34`-`PR 47` slice queue only, not to these findings.
- All ten `SSA-*` slices remain `planned`, re-confirmed by construction.

### Structural H3 design track

`H3` remains open and design-gated. It is not part of the completed low-severity
follow-up batch and must preserve safety-sensitive post-activation and
finalization behavior during decomposition. `R3-Q1` updates its file-size
figures rather than opening a parallel structural track.

| Finding | Issue | Status | Safety classification |
| --- | --- | --- | --- |
| H3 | [#158](https://github.com/tomazb/rh-acm-switchover/issues/158) | open/design track | large safety-sensitive decomposition |

## Post-Review #2 Delta Reconciliation (2026-07-26)

The still-valid post-Review #2 delta from GitHub PR
[#196](https://github.com/tomazb/rh-acm-switchover/pull/196) is absorbed here
after revalidation against source and the authoritative Phase 9A design. PR
`#196` and PR `#197` edit the same tracker from the same `ansible` base. PR `#197` is
therefore **intended to supersede PR #196 only after a different agent
independently validates PR #197's exact corrected head**. Supersession is not
complete in this builder pass; PR #196 remains open and unchanged.

### Delta disposition and taxonomy

| Claim | Validation | Tracker disposition |
| --- | --- | --- |
| `TR2D-M1` + `TR2D-L1` | confirmed with nuance | Fold into the same `R3-A1` / `R3-01` / `TR2D-01` Argo CD scoped-discovery correctness boundary. Require positive success for every namespace read before aggregation; do not duplicate implementation work under two IDs. PR #200 captures the failed-item-without-`msg` runtime shape in its executable mixed-result coverage. |
| `TR2D-M2` | confirmed | Preserve as `TR2D-02`: fresh Application re-read immediately before resume, current same-run marker validation, non-empty current `resourceVersion`, conditional patch, and Python/collection OCC outcome parity. |
| `TR2D-Q1` | confirmed maintainability/review risk | Preserve as `TR2D-03`, a characterization-first Phase 9B decomposition design input. It is a preferred predecessor or strong design input for later Phase 9 work, not a mandatory Phase 9C prerequisite unless the authoritative design is separately amended. |
| `TR2D-Q4` | confirmed maintainability | Preserve as deferred `TR2D-04`: remove GitOps advisory duplication only after explicitly recording primary/secondary and restore-only asymmetries. |
| `TR2D-Q2` | inventory signal only | No standalone slice. File size supports responsibility/coupling analysis but does not by itself authorize a refactor. |
| `TR2D-Q3` | confirmed low-value residual seam | Do **not** silently add it to `R2-L1` / issue #152. It remains unassigned until that issue's scope is explicitly amended and approved, or a separate design boundary is accepted. |
| `TR2D-Q6` | rejected/non-defect | Preserve the deliberate strict-versus-advisory error surface and the advisory path's no-raw-exception logging policy; deduplication is not a defect fix. |
| `TR2D-L2` | unverified | Excluded pending an exact path, expected report reference, and reproducible failing scenario. |

### Preserved delta boundaries

| Boundary | Status | Findings | Resolution boundary |
| --- | --- | --- | --- |
| `R3-01` / `TR2D-01` | merged | `R3-A1`, `TR2D-M1`, `TR2D-L1` | PR [#200](https://github.com/tomazb/rh-acm-switchover/pull/200) merged exact validated head `0bc1a4b6701508f6c3d4cd898515d82b8a29b6a3` into `ansible` as merge commit `786f8325493c6086e136cb9694a9997557f12e02`. The final validator returned `READY_TO_MERGE_EXACT_HEAD`, exact-head CI was green, and zero actionable feedback remained. Issue [#199](https://github.com/tomazb/rh-acm-switchover/issues/199) closed as completed. Merge credit covers only the distinct scoped/cluster/published ownership, positive all-namespace success, fail-closed malformed/failed/skipped/unreachable/mixed handling, exact-Boolean changed-count, primary-prep retry/re-pause, and standalone two-hub resume boundary. All evidence remains non-live. |
| `TR2D-02` | planned | `TR2D-M2` | Re-read each exact Application immediately before resume, revalidate current same-run ownership, require current resource version, patch conditionally, and align missing/foreign marker, missing-RV, conflict, success, and `changed` outcomes with Python. |
| `TR2D-03` | planned/design input | `TR2D-Q1` | Characterize and then decompose Phase 9B immutable contracts, enrollment/trust validation, typed read/pagination, identity fingerprinting, freshness/provenance, artifact/redaction, and orchestration without broadening live authority. |
| `TR2D-04` | deferred/design-gated | `TR2D-Q4` | Replace duplicated dual-hub GitOps advisory blocks only after preserving every intentional hub asymmetry, status fact, and message. |

The authoritative Phase 9A design keeps Phase 9C non-mutating: it expands the
read-only proof and produces non-executable authorization only. Preparation
mutation begins in Phase 9D, and the first live switchover mutation occurs in
Phase 9E. Accordingly, `TR2D-03` should be considered before Phase 9C expands
the read-only proof and non-executable authorization surface; Phase 9C remains
non-mutating. Nothing in this reconciliation grants mutation authority or
changes the controller trust boundary.

## Security & Stability Audit Follow-Up (2026-07-19)

This queue records the 17 actionable findings from the independently validated
Security & Stability Audit. It intentionally excludes audit findings `A3`, `A4`,
and `R3`, which validation determined were documented behavior rather than
defects. Audit IDs are prefixed with `SSA-` in this tracker to avoid collisions
with the earlier Thermos finding IDs.

The corrected severity is authoritative for sequencing:

- `SSA-A2` and `SSA-P2` are P1 because both supported implementations can accept
  primary and secondary contexts that resolve to the same physical cluster.
- Ten findings are P2 and five are P3 after removing overstated impact and
  accounting for existing confirmation, lifecycle, and controller gates.
  The post-Review #2 delta revalidation raises `SSA-PY5` to P2 because the
  reusable mutation helper directly logs bounded raw API response-body and
  rendered exception content.
- The grouped slices below are resolution boundaries, not assigned PR numbers.
  A slice enters the numbered PR sequence only after its own approved design/spec
  and implementation plan satisfy the tracker gate.

### Relationship To Existing Thermos Work

- `SSA-A2` / `SSA-P2` complement the identity-binding work in `F5`, `F37`, and
  `F39`; those fixes protect resume targets but do not prove that the two live
  hub roles are physically distinct before a new switchover.
- `SSA-P1` / `SSA-PY4` are new decommission safety work. Open issue
  [#153](https://github.com/tomazb/rh-acm-switchover/issues/153) (`R2-L6`) is a
  structural Ansible discovery-order refactor and does not resolve wrong-target
  protection or the Python embedded-decommission RBAC recheck.
- `SSA-PY3` is a narrower residual gap after `F31`: report artifacts received
  canonical containment checks, while relative general state paths still receive
  syntax-only validation.
- `SSA-S2` sharpens the residual after `F33`: the OpenShift client is checksum
  verified today, but release builds may still trust a checksum fetched from the
  same origin instead of an independently pinned digest.
- `SSA-S1` / `SSA-S3` overlap the deprecated-script neighborhood of open issue
  [#157](https://github.com/tomazb/rh-acm-switchover/issues/157) (`R2-L8`), but
  require a broader remove-versus-harden decision for the deprecated Argo CD
  script. Resolve them in one script-lifecycle slice rather than duplicating
  shell cleanup.
- `SSA-C1`, `SSA-C2`, and `SSA-C3` are not covered by the blocking Bandit and
  CodeQL controls already present. Their scope is the remaining advisory
  scanners, mutable workflow/tool references, collection Bandit coverage, and
  reproducible CI/release dependency inputs.

### Planned Resolution Slices

`SSA-01` is the highest-impact P1 product-runtime invariant. Its design may
proceed in parallel with the immediate bounded regression deliveries described
in the global delivery sequence below. Release/CI/docs slices `SSA-04`,
`SSA-06`, `SSA-07`, and `SSA-10` may also proceed independently in isolated
worktrees when their slice-specific designs establish no dependency conflict.

| Slice | Status | Findings | Proposed resolution boundary | Required review |
| --- | --- | --- | --- | --- |
| SSA-01 | planned | SSA-A2, SSA-P2 | Add a shared-behavior, fail-closed physical-hub distinction guard before any mutation. | Python/collection parity; wrong-context and same-UID safety |
| SSA-02 | planned | SSA-P1, SSA-PY4 | Strengthen standalone and embedded decommission target/RBAC checks without requiring prior switchover state. | destructive-operation, RBAC, parity, and dry-run review |
| SSA-03 | planned | SSA-PY2, SSA-A6 | Make klusterlet endpoint selection unambiguous and bound collection worker concurrency. Extended by `R4-06`: implement against `docs/plans/2026-07-29-kubeconfig-ambiguity-guard-design.md` (fail-closed merge, duplicate-name rule, snapshot-built client, mutation barrier). | post-activation parity, timeout, and scale review |
| SSA-04 | planned | SSA-R1, SSA-R2 | Require explicit release-profile authorization for live decommission and reject safety-critical adapter overrides. | lab-controller trust boundary and release evidence review |
| SSA-05 | planned | SSA-S1, SSA-S3 | Remove the deprecated Argo CD shell path if compatibility permits; otherwise make state identity, permissions, and context parsing fail closed. | operator migration, shell safety, and documentation review |
| SSA-06 | planned | SSA-C1, SSA-C2 | Establish required dependency/secret gates and pin third-party actions and security tools immutably. | CI availability, false-positive, and update-process review |
| SSA-07 | planned | SSA-C3, SSA-S2 | Extend blocking Bandit coverage and make CI/release dependency and OpenShift-client inputs reproducible. | supply-chain and multi-architecture build review |
| SSA-08 | planned | SSA-PY3 | Apply canonical containment and safe-write policy to relative state paths. | resume compatibility and filesystem-adversary review |
| SSA-09 | planned | SSA-PY5 | First remove raw API/exception logging as a bounded sub-slice; then separately bound or stream full-list aggregation without silently omitting safety-relevant resources. | secret handling, scale, and fail-closed review |
| SSA-10 | planned | SSA-A5 | Correct the collection `force` documentation contract; do not introduce an unaudited override. | migration-map and operator-expectation review |

### Resolution Requirements

#### SSA-01: Distinct Physical Hub Guard

**Resolution**
- Read the live `kube-system` namespace UID for both configured hubs during
  preflight/initialization and reject equal UIDs before a mutation-capable phase.
- Implement the same operator-facing decision in Python and the collection while
  respecting their independent import boundaries.
- Keep per-role identity binding for resume; distinct-hub validation is an
  additional invariant, not a replacement.

**Acceptance criteria**
- Identical context names fail input/preflight validation.
- Different context names and kubeconfigs that resolve to one cluster fail on
  equal live UIDs.
- Unreadable live identity fails closed in execute mode and reports which hub
  could not be verified without exposing credentials.
- Distinct live UIDs continue through validate, dry-run, and execute paths.
- Python, collection, and shared parity tests cover same-context, same-UID,
  unreadable-UID, and distinct-UID cases.

**Revalidation note (2026-07-26)**
- Still unmet in product code. `lib/validation.py:302-308` and
  `plugins/module_utils/validation.py:27-44` validate each context
  independently and never compare them;
  `roles/preflight/tasks/discover_hub_identities.yml:28-65` publishes both UIDs
  and asserts each is non-empty without comparing them; `lib/utils.py:712-760`
  and `acm_switchover.py:1120-1131` compare stored-vs-current **per role** only.
- The third acceptance bullet (unreadable identity fails closed, naming the hub)
  is **already satisfied** by pre-existing per-role binding
  (`lib/kube_client.py:328-334`, `lib/utils.py:731-736`,
  `discover_hub_identities.yml:50-65`). The slice's remaining work is the
  *comparison*, not the read. Note also that
  `discover_hub_identities.yml:10-14,24-26` skips the live read in non-execute
  mode when `acm_switchover_hub_identities` is already defined.
- Design donor: the release harness already implements this exact check —
  `tests/release/lab_controller/identity.py:98-110`
  (`duplicate physical hub identity fingerprint`),
  `live_discovery.py:849-854`, `profiles.py:37-43`, `live_config.py:696-702`.
  These are release-validation-only, never execute in the CLI or the collection,
  and do **not** close `SSA-A2`/`SSA-P2`.

#### SSA-02: Decommission Target And RBAC Revalidation

**Resolution**
- Add an optional expected physical-cluster UID to standalone decommission and
  verify it immediately before destructive work; preserve a clearly confirmed
  path for operators who have no prior state or expected UID.
- Re-run decommission RBAC validation immediately before Python finalization
  invokes embedded teardown so permission drift cannot begin a partial teardown
  unnoticed.
- Compare the collection's standalone and embedded paths and close equivalent
  gaps in the same slice; any intentional difference requires the parity
  approval process.

**Acceptance criteria**
- An expected-UID mismatch or unreadable UID stops before deleting a
  `ManagedCluster`, `MultiClusterHub`, namespace, or observability resource.
- Standalone decommission remains usable without switchover state, but the
  no-expected-UID path retains explicit confirmation and reports the verified
  context/cluster identity before mutation.
- Embedded decommission fails before teardown if the immediate RBAC recheck
  denies or errors.
- Negative tests cover wrong context, UID mismatch, RBAC drift, dry-run, and
  non-interactive execution in both supported form factors where applicable.

#### SSA-03: Klusterlet Targeting And Concurrency Bounds

**Resolution**
- Replace hostname-only spoke matching with normalized endpoint identities and
  fail closed when more than one context remains a valid match.
- Preserve the documented tolerance for equivalent default-port forms only when
  the normalized endpoint is unambiguous.
- Validate the collection worker setting against a documented upper bound before
  creating worker pools.

**Acceptance criteria**
- Two contexts that share a hostname but differ by port or endpoint cannot
  overwrite each other silently.
- Zero-match and multi-match results are explicit non-mutation failures.
- Default worker behavior remains unchanged; zero, negative, non-integer, and
  above-limit values fail validation with actionable messages.
- Scale tests prove that the configured upper bound constrains concurrent API
  work and existing per-call timeouts remain effective.

**Revalidation note (2026-07-26)**
- The worker-validation criterion is **half-satisfied already**:
  `plugins/module_utils/klusterlet.py:43-52` rejects non-integer and `< 1` with
  `"workers must be a positive integer"`. Only the documented **upper bound**
  and its scale test remain — `plugins/module_utils/constants.py:16` defines
  just `KLUSTERLET_DEFAULT_WORKERS = 10`, and any configured value flows
  through to `ThreadPoolExecutor(max_workers=workers)` at `klusterlet.py:197`.
- Endpoint targeting is unchanged: `modules/post_activation.py:1483-1494`
  still collapses to `cluster_servers[server_host]` (last write wins), with the
  same collapse at `plugins/module_utils/klusterlet.py:161-162`. Zero-match
  returns `""` with a debug log; multi-match is never detected.
- Sequence with `R3-03` (Python klusterlet batch-timeout budget): the two are
  adjacent but neither resolves the other.

#### SSA-04: Release Profile Mutation Controls

**Resolution**
- Add a dedicated, default-false release-profile authorization for live Python
  decommission and reject live decommission outside disposable-lab controller
  policy.
- Replace unrestricted safety-critical `extra_args` / environment overrides with
  an allowlist or explicit denylist validation before adapter command assembly.
- Keep matrix lifecycle and lab-controller GO/NO-GO checks authoritative; profile
  validation adds defense in depth and must not bypass controller decisions.

**Acceptance criteria**
- A focused Python decommission rerun is non-mutating unless the profile,
  disposable-lab policy, and controller authorization all permit it.
- Trailing Ansible `-e` values cannot override dry-run, confirmation, target
  identity, checkpoint, or controller-owned safety variables.
- Unsafe arguments fail during profile validation, before subprocess execution.
- Tests cover duplicate-option precedence, environment overrides, focused
  reruns, and artifact evidence showing whether live mutation was authorized.

**Revalidation note (2026-07-26)**
- Phase 8J/9B landed **none** of this slice. `tests/release/adapters/python_cli.py:99-112`
  still builds `--decommission --non-interactive` with no `--dry-run`, while the
  Ansible stream forces dry-run for the same scenario
  (`tests/release/adapters/ansible.py:131-138`). No live-authorization field
  exists on the profile model (`tests/release/contracts/models.py:37-42`), and
  `contracts/loader.py:112` only coerces `extra_args` to `str` — no allowlist.
  `adapters/ansible.py:141-149` still appends `extra_args` verbatim *after* the
  controller-owned `-e` JSON.
- The overlapping lab-controller work (`lab_controller/live_config.py:926-962`,
  `decisions.py:26`, `read_only_discovery.py:338`) is a different trust boundary
  and is already credited in the `SSA-R1`/`SSA-R2` rows. It satisfies no
  `SSA-04` criterion.

#### SSA-05: Deprecated Script Lifecycle

**Resolution**
- Prefer deleting `scripts/argocd-manage.sh` and its obsolete tests/docs after
  confirming supported Python and collection replacements cover every documented
  operator workflow.
- If removal is not yet compatible, reject legacy resume state that lacks bound
  context/UID identity, treat explicit context mismatch as fatal, create state
  files under `umask 077`, and use kubeconfig-aware context resolution that
  handles quoted names.
- Coordinate related deprecated-shell cleanup with issue #157 so the same
  compatibility surface is not changed twice.

**Acceptance criteria**
- No supported path can patch Argo CD Applications after only warning about a
  state/context mismatch.
- Any retained state file is owner-only from creation, not repaired only after
  writing.
- Shell tests cover legacy state, mismatched context, quoted context names, and
  secure file creation.
- Migration docs and `CHANGELOG.md` direct operators to supported replacements.
  Protected runbook/SKILL files remain unchanged unless separately approved.

#### SSA-06: Required Security Gates And Immutable CI References

**Resolution**
- Make dependency auditing and at least one maintained secret-scanning lane
  blocking, with reviewed suppressions/baselines instead of unconditional
  `continue-on-error` or `|| true`.
- Keep intentionally advisory scanners labeled as advisory so workflow status is
  not misleading.
- Pin third-party actions to full commit SHAs and install security tools at
  reviewed versions, with an explicit automated or scheduled update process.

**Acceptance criteria**
- A known vulnerable dependency fixture and a safe synthetic secret fixture make
  their required jobs fail.
- Reports upload with `if: always()` even when a required scanner fails.
- Workflow/static tests reject branch refs such as `@main` / `@master` and
  unversioned security-tool installation.
- Bandit and CodeQL remain blocking; the change does not weaken existing gates.

#### SSA-07: Reproducible Dependencies And Release Artifact Integrity

**Resolution**
- Extend the blocking Bandit target set to collection plugins while preserving
  reviewed exclusions only where technically justified.
- Add constraints or lock artifacts for CI and release certification without
  replacing the project's intentional minimum-version declarations for
  consumers.
- Require independently pinned OpenShift-client digests in release builds for
  every supported architecture; retain same-origin checksum discovery only for
  explicitly non-release development builds if still needed.

**Acceptance criteria**
- A Bandit finding in collection plugin code fails the blocking quality lane.
- Clean-environment CI/release installs resolve from reviewed reproducible inputs.
- Release container builds fail when an architecture digest is absent or does
  not match; amd64 and arm64 paths are tested.
- Dependency and digest update instructions identify ownership and verification
  commands.

#### SSA-08: Relative State Path Containment

**Resolution**
- Route relative state-file paths through the canonical path-safety policy used
  for protected artifacts, resolving from the documented base directory.
- Preserve valid ordinary relative state files while rejecting symlink escapes,
  prefix confusion, unsafe ancestors, and unsafe final-component replacement.

**Acceptance criteria**
- Existing normal relative and absolute state-path workflows remain compatible.
- Adversarial tests cover symlinked parents, symlinked final components,
  non-existent ancestors, traversal, and base-prefix collisions.
- State creation and replacement use the existing safe-write/no-follow strategy
  where applicable.
- Resume reports an actionable path error before reading or writing attacker-
  redirected state.

#### SSA-09: API Error Redaction And Resource Bounds

**Resolution**
- Treat API/exception redaction as the first bounded implementation sub-slice;
  resource-bound changes follow separately if their design confirms independent
  rollback and verification boundaries.
- Stop logging raw Kubernetes API response bodies from reusable helpers; log
  stable status/reason text and already-sanitized public details only.
- Inventory every full-list aggregation call and either stream/process pages or
  apply a documented maximum appropriate to that operation.
- Never treat a truncated set as complete for mutation, identity, RBAC, or
  safety decisions; reaching a required-completeness limit must fail closed.

**Acceptance criteria**
- Tests with token-, kubeconfig-, and Secret-like API bodies prove logs contain
  no body fragments.
- High-cardinality tests prove bounded memory/concurrency behavior.
- A configured limit reached on a safety-relevant list returns an explicit error,
  not partial success.
- Operator diagnostics retain status code, resource kind, namespace, and a
  sanitized reason sufficient for troubleshooting.

**Revalidation note (2026-07-26) — scope widened**
- The 2026-07-18 "safe logging" commits (`051ef43f`, `cf109d6a`, `37a1605b`)
  did **not** touch this slice's first bullet. They sanitized preflight detail
  strings and Argo CD discovery output — work now tracked separately as
  `R3-P2` / slice `R3-04`. The reusable-helper body log is unchanged:
  `lib/kube_client.py:863-869` still logs `body=%s` with `e.body[:500]`.
- **Widen the scope beyond `e.body`.** `logger.error(..., e)` on an
  `ApiException` leaks the response body through `str(e)`, which embeds
  `HTTP response body:`. Sites: `lib/kube_client.py:872,1149,1190,1226`,
  `modules/post_activation.py:490,1306`,
  `modules/finalization.py:357,360,1529`, `modules/primary_prep.py:295`. This
  is a wider leak class than the single `e.body[:500]` the finding names, and
  no test asserts that token-, kubeconfig-, or Secret-like bodies stay out of
  logs.
- The truncation bullet is confirmed unmet and safety-relevant:
  `lib/kube_client.py:714-768` truncates with only a `logger.debug` and returns
  a partial list to callers making safety decisions on `max_items=2`
  (`modules/finalization.py:905`, `modules/primary_prep.py:144`). ~25
  `list_custom_resources(...)` call sites pass no `max_items` at all.

#### SSA-10: Collection `force` Contract

**Resolution**
- Remove the false CLI migration claim that Python `--force` maps to active
  collection behavior, and consistently label the collection field as reserved
  or unsupported.
- Do not implement a collection force bypass in this slice. Any future override
  requires a separate safety design covering identity, checkpoint, and
  destructive-operation boundaries.

**Acceptance criteria**
- Migration and variable-reference docs agree that no active collection force
  behavior exists.
- Documentation guardrails fail if the unsupported one-to-one mapping returns.
- No runtime defaults, validation, checkpoint, or identity behavior changes.

## Thermos Review #3 (2026-07-25)

Full-branch deep review (923 commits, 745 files, +146,459/-11,180, `ansible` vs
`main` at merge base `aca2d296`) using three area-scoped
`thermo-nuclear-review-subagent` passes — Python core, Ansible collection, and
test suite — each also applying the
`thermo-nuclear-code-quality-review` rubric rather than running a separate
paired quality agent. The split was by area, not by rubric, because the three
file sets are near-disjoint at this branch size.

**Naming note:** Review #3 findings use the `R3-` prefix by analogy with `R2-`.
The bare `R3` identifier that appears in the Security & Stability Audit
exclusion list (documented behavior, not a defect) is unrelated to any `R3-*`
finding below.

Baseline established during the review:

- Full suite: **3079 passed, 29 skipped** in ~110s (`python -m pytest tests/ -q`),
  reproduced twice with no flakes. The 29 skips are e2e-needs-real-cluster plus
  three gated release/pilot tests.
- `black --check --line-length 120 tests/` clean across 184 files.
- 867 collection unit tests pass. All Ansible-semantics claims in `R3-A*` were
  confirmed empirically with `ansible-playbook` on the reviewing host, not
  inferred from reading.

### Headline: One Defect Class, Three Surfaces

The high-severity findings cluster around **safety verification that does not
verify**, reached by four different mechanisms. None is detectable by a linter;
all four are semantically valid code. Note the shapes differ: `R3-A1`, `R3-A4`,
and `R3-A5` report success on a failed path; `R3-P1` reports *failure* on a
healthy one, after activation; `R3-T1` is a coverage gap that lets the first
shape survive undetected. (Framing corrected 2026-07-26 — the original text
called all five "a failure path that reports success", which is wrong for
`R3-P1` and `R3-T1`.)

| Mechanism | Surface | Findings |
| --- | --- | --- |
| `register` fires on *skipped* tasks, overwriting a same-named `set_fact` with `{'skipped': True}`; `.resources` disappears and every `\| default([])` converts the loss into an empty-list success | Ansible | `R3-A1`, `R3-A2`, `R3-A3` |
| `failed_when: false` rewrites `result.failed = False`, making every later `when: result is failed` gate unreachable dead code | Ansible | `R3-A4`, `R3-A5` |
| One batch deadline consumed as if it were a per-item deadline | Python | `R3-P1` |
| Mutating dry-run guards exercised only through fully-mocked clients, so guard removal is invisible | Tests | `R3-T1` |

### Relationship To Existing Thermos Work

Two findings are **regressions introduced by previously merged Thermos PRs**,
which is why neither was caught by the review that motivated the original fix:

- `R3-A1` is a regression from `F41` / `PR 24` (namespace-scoped Argo CD
  discovery). The scoped-discovery branch added in that PR is the exact branch
  the register clobber disables, so Argo CD pause and resume both silently
  no-op on every path that uses it.
- `R3-P1` is a regression from `F2` / `PR 03` (make Python klusterlet worker
  timeout fail closed). Converting the timeout from a skip into a
  `SwitchoverError` is correct; it also converted a batch-wide wall clock into
  a fleet-size-dependent false failure that now aborts the run *after*
  activation.

Residuals of closed findings, tracked as new IDs rather than reopened rows:

- `R3-P6` is the residual of `R2-H1` / `PR 36`, which added request timeouts to
  `delete_configmap` and `delete_pod`. `delete_custom_resource` was not in that
  slice and is now the only mutating `KubeClient` method without a default
  request timeout.
- `R3-A11` is the residual of `F17` / `PR 09` and `R2-M1` / `PR 37`-`PR 38`,
  which addressed `changed` reporting under **Ansible check mode**. The
  plan-only `changed=true` reported during an ordinary (non-check-mode) run was
  outside all three slices.
- `R3-P2` is the operability cost of the API-response-redaction direction that
  `SSA-09` also pursues. `SSA-09` remains correct; `R3-P2` records that the
  preflight reporter applied redaction to the wrong axis and must be resolved
  without weakening `SSA-09`.

Adjacent but distinct, not duplicates:

- `SSA-03` bounds *collection* klusterlet worker concurrency. `R3-P1` is the
  *Python* batch timeout budget. Sequence them together if convenient; they do
  not resolve each other.
- `SSA-01`'s distinct-physical-hub guard does not address `R3-A6`, which
  disables identity validation through a persistent configuration key.

### Validated Findings

Review #3 originally claimed **40** findings: 11 `R3-A*`, 13 `R3-P*`,
11 `R3-T*`, 4 `R3-Q*`, and 1 `R3-X*`. Revalidation added two raw claims,
`R3-T12` and `R3-P6b`; `R3-P6b` is supporting evidence folded into `R3-P6`,
not a second unique implementation item. The canonical table therefore has
**41 unique IDs**: 11 `R3-A*`, 13 `R3-P*`, 12 `R3-T*`, 4 `R3-Q*`, and
1 `R3-X*`.

The mechanically exclusive disposition is **37 actionable + 1 optional
hardening + 2 rejected/non-actionable + 1 routed to the existing `H3` track =
41 unique IDs**. The raw-claim ledger is **40 original + 2 revalidation-added
- 1 folded duplicate = 41 unique IDs**.

| Finding | Severity | Surface | Summary |
| --- | --- | --- | --- |
| R3-A1 | High | Ansible | `roles/argocd_manage/tasks/discover.yml:154` sets `_argocd_app_list`; `:174` re-registers the same name on a `when:`-skipped task. Under scoped discovery the app list becomes `[]`, so pause and resume both patch nothing and report success. Reached by a `primary_prep` retry after checkpoint rehydration and by the documented standalone `playbooks/argocd_resume.yml`. |
| R3-A2 | Medium | Ansible | Same clobber at `roles/finalization/tasks/cleanup_restores.yml:3` → `:14`; the dry-run preview always reports `restore_count: 0`, hiding which Restore resources execute mode will delete. |
| R3-A3 | Medium | Ansible | Same clobber at `roles/finalization/tasks/discover_resources.yml:55-78`. Benign **only on the execute-mode live-query path**, where the real result is fetched anyway; injected dry-run and fixture data is already defeated, so the `when: … is not defined` guard the same file implements correctly three times above does not hold here. |
| R3-A4 | High | Ansible | `roles/primary_prep/tasks/scale_observability.yml:33-70` masks API errors with `failed_when: false`; the `until` loop's `resources \| default([]) \| length == 0` then succeeds on the first attempt and both branches of the follow-up gate are dead. A 403/timeout while verifying Thanos compactor termination reads as "drained". Python fails closed here — a parity divergence on a dual-supported capability. |
| R3-A5 | High | Ansible | `roles/preflight/tasks/validate_kubeconfigs.yml:32,82` derive the connectivity verdict from `.failed`, which `failed_when: false` pins to `False`. Both hub-connectivity entries are hard-coded `status: pass`; the `else "fail"` branch is unreachable. Expired tokens, wrong `server:` URLs, and DNS failures all render as validated in the go/no-go `preflight-report.json`. |
| R3-A6 | Medium | Ansible | `plugins/action/checkpoint_phase.py:141,318` treat `reset_from` as an explicit reset, skipping `validate_operation_identity` for **every** `checkpoint_phase` call in the run, not just the pruned phase. `reset_from` is a persistent key shipped in the defaults of all five checkpointed phase roles (e.g. `roles/activation/defaults/main.yml:15-20`), so a value left in group_vars disables hub-identity binding indefinitely; `_build_reset_from_checkpoint` then rewrites `operation_identity` to whatever is configured now. |
| R3-A7 | Medium | Ansible | `module_utils/klusterlet.py:331` places `"failed": bool(failed_clusters)` in the probe result and `acm_klusterlet_probe.py:106` returns it via `exit_json`, so Ansible fails the task itself. The role's own diagnostic messages in `verify_klusterlet.yml` never render, contradicting the module's documented contract. |
| R3-A8 | Medium | Ansible | `module_utils/klusterlet.py:39-40` use `str \| None` in *assignments*, which `from __future__ import annotations` does not defer, so import raises `TypeError` on Python 3.9. The collection declares only `requires_ansible: ">=2.15.0"` and no Python floor; the EE pins `ansible-runner:stable-2.15-latest`. |
| R3-A9 | Medium | Ansible | `acm_input_validate.py:196-210` re-adds kubeconfig/checkpoint/report paths into `results`, which flow into the `0644` `preflight-report.json`. Precisely: `sanitize_report_hubs` (`acm_preflight_report.py:76-113`) only promises to sanitize the `hubs` block, so this is not literally a contract violation — but the same paths the module strips from `hubs` reappear via `results`, defeating the intent. Paths, not credentials. |
| R3-A10 | Medium | Ansible | `roles/post_activation/tasks/verify_observability.yml:85-105` re-annotates observatorium-api on every run. Python guards the equivalent step with per-step state (`modules/post_activation.py:200`); the collection has only phase-level checkpointing, and `checkpoint.enabled` defaults to `false`. Each retry after a post-activation failure extends the metrics outage. |
| R3-A11 | Medium | Ansible | Residual of `F17`/`R2-M1`: `acm_restore_info.py:386,430` and `acm_backup_schedule.py:181` report `changed=true` for plan-only operations outside check mode, contradicting the `_info` naming convention and `AGENTS.md:547`. The `RETURN`-doc contradiction applies to `acm_backup_schedule` only; `acm_restore_info` has no `RETURN` block at all. |
| R3-P1 | High | Python | `modules/post_activation.py:782` calls `wait(futures, timeout=KLUSTERLET_WORKER_TIMEOUT)`, one deadline for the whole batch, while the constant and `KLUSTERLET_WORKER_TIMEOUT_MESSAGE` are sized per cluster. With `CLUSTER_VERIFY_MAX_WORKERS = 10` and up to two 30s reads per cluster, a 30-cluster fleet exactly consumes the 180s budget; healthy clusters queued behind slow ones land in `timed_out` and raise at `:1057`, failing the run after activation has already moved production. Scales the wrong way with fleet size. Shared by `_fix_wrong_hub_klusterlets` and `_remediated_klusterlet_state`. |
| R3-P2 | Medium | Python | `modules/preflight/reporter.py` discards the `message` argument at every log level, with no debug fallback. Diagnostics such as `_describe_bsl_issue()` reach nothing; `--validate-only` prints only `✗ <category>: failed` even under `-v`, Corrected 2026-07-26: the messages **are** always persisted to `state.config["preflight_results"]` (`acm_switchover.py:639-649`); only the artifact export is `--report-dir`-gated (`lib/cli_outcomes.py:110-129`). So the diagnostic is recoverable by hand-reading the state JSON — but it reaches no log at any verbosity, which is the actual defect. Redaction was applied to the bounded code-owned category instead of the unbounded actionable message. |
| R3-P3 | Medium | Python | `lib/argocd_resume.py:343-349` signals "re-run Argo CD pause on retry" by appending to `errors[]`, but `lib/cli_outcomes.py:96-105` and `lib/workflow.py:172` both read only `errors[-1]`. A post-activation failure yields a report artifact naming `primary_prep` and a resume banner showing the Argo CD housekeeping note instead of the real cause. |
| R3-P4 | Medium | Python | `lib/argocd.py:491-527`: the ApplicationSet blocker branch is correctly gated on `_count_acm_resources(app) > 0`; the stale-status branch is not. On a hub that also runs Argo CD for fleet workloads, one never-synced Application with `automated` set and empty `status.resources` — any kind, any namespace — hard-fails `PRIMARY_PREP`. The operator message also says pause "failed for N Application(s)" when zero pause attempts were made. |
| R3-P5 | Medium | Python | `lib/workflow.py:149,193` replaced `sys.exit` with `raise SwitchoverError`, so `lib/cli_outcomes.py:204-208` now records state-refusal messages via `add_error`. On the **unresumable-FAILED** refusal path (`lib/workflow.py:159-194`) the errors array grows on every rerun and `get_last_error_phase()` pins to `FAILED`, masking the real failure in the banner. Scope corrected 2026-07-26: this is the failed-state path specifically, not every refusal path. |
| R3-P6 | Low | Python | Residual of `R2-H1`: `lib/kube_client.py:1027-1044` still builds `kwargs` conditionally instead of using `_request_timeout_kwargs()`, leaving `delete_custom_resource` the only mutating method able to hang indefinitely. Revalidation claim `R3-P6b` is folded here as supporting evidence, not a separate finding: the docstring at `lib/kube_client.py:1017-1018` says a `None` timeout uses the client default even though no timeout keyword is sent, so the documentation also conceals the gap. |
| R3-P7 | Low | Python | `--dry-run --report-dir` writes an empty artifact: `acm_switchover.py:438-443` restores the pre-run snapshot in `run_switchover`'s `finally`, which precedes the report write in `lib/cli_outcomes.py:227-228`, so the report records `current_phase: "init"`, zero steps, and `status: "pass"`. Scope corrected 2026-07-26: this describes a fresh successful dry-run; a dry-run over pre-existing state, or a failed dry-run, reports differently. |
| R3-P8 | Low | Python | Corrected 2026-07-26: **two** of the four constants are unreferenced, not four — `OADP_NAMESPACE` and `BACKUP_STORAGE_LOCATION_RESOURCE` are used by `tests/release/baseline/discovery.py:6,56,60`. `HUB_KUBECONFIG_SECRET_NAME` and `BOOTSTRAP_HUB_KUBECONFIG_SECRET_NAME` have no runtime use while `modules/post_activation.py` inlines the same literals at six operational sites (`:1181,1212,1234,1254,1544,1554`), contrary to `AGENTS.md`. |
| R3-P9 | Low | Python | `lib/report_artifacts.py:131-142` writes mode `0o644` payloads embedding raw exception text from `state_snapshot["errors"]`. Its validate → `mkdir` → revalidate sequence is intentional security behavior, mirrored by the collection before `os.open`; it must be preserved and is not part of this finding. |
| R3-P10 | Rejected | Python | Rejected/non-actionable. The pinned cluster-backup-operator `RestorePhase` definition contains `Started`, `Running`, `Finished`, `FinishedWithErrors`, `Error`, `Unknown`, `EnabledWithErrors`, and `Enabled`; it does not contain `FailedWithErrors`. Release branches 2.12-2.17 were checked at `74b54988`, `7a7b240b`, `8b489db4`, `25b28b76`, `9efe77ea`, and `c8578f94` respectively, and none defines that phase. Authoritative pinned source: [`restore_types.go@c8578f94`](https://github.com/stolostron/cluster-backup-operator/blob/c8578f94df09deab561e1aa5a7e9fc9b57f7d113/api/v1beta1/restore_types.go). Do not add handling for an invented phase. |
| R3-P11 | Rejected | Python | Rejected/non-actionable as the current executable contract. `lib/waiter.py` uses normal polling when `fast_timeout <= 0`, and `tests/test_waiter.py::test_wait_fast_timeout_zero_disables_fast_interval` explicitly fixes `fast_timeout=0` as disabling fast polling and using the standard interval. Historical behavior on `main` does not override this tested contract. |
| R3-P12 | Low | Scripts | `scripts/generate-merged-kubeconfig.sh:355` relies on `(umask 077 && ...)`, which does not tighten an already-existing world-readable `merged-kubeconfig.yaml`. `scripts/setup-rbac.sh:468` gets this right with an explicit `chmod 600`. |
| R3-P13 | Optional | Scripts | Optional packaging/error-message hardening only. The supported invocation in `scripts/README.md:580-606` runs `generate-sa-kubeconfig.sh` with its internal sibling `constants.sh` present, so no supported "copy one script without companions" contract has been demonstrated. A future `-f` guard may provide a clearer packaging error, but this is not mandatory runtime-fix work. |
| R3-T1 | High | Tests | `scale_statefulset`, `delete_pod`, `delete_configmap`, and `create_or_patch_configmap` have `if self.dry_run:` guards with no dry-run test at any level. Deleting the guard from `scale_statefulset` leaves all 3079 tests passing while `--dry-run` scales the production Thanos compactor to 0. Workflow suites cannot catch it: `tests/test_primary_prep.py:52` returns a bare `Mock()`, so `assert_called_once_with` asserts against the mock and never reaches the guard. The correct pattern already exists at `tests/test_kube_client.py:225`. |
| R3-T2 | Medium | Tests | `lib/utils.py:98` deliberately uses `if obj is True:` to avoid truthy object references, but every test in `TestDryRunSkipDecorator` passes exact `True`/`False`. Relaxing it to `if obj:` keeps the suite green, and a non-bool truthy `dry_run` (e.g. `1`, a config-parsed `"true"`) silently *runs* the mutation — the coupling `lib/argocd.py:628` warns about is unpinned. |
| R3-T3 | Medium | Tests | `tests/test_argocd_constants_parity.py:52-87` claims to verify `build_pause_patch` against `pause.yml`'s Jinja but never loads the file; the oracle is hand-written Python. It is already wrong: `pause.yml` gates the whole task on `automated is not none` and issues no patch when absent, while the test asserts a patch body for exactly that case. |
| R3-T4 | Medium | Tests | `test_acm_namespaces_parity` and `test_ansible_argocd_filters_match_acm_sub_namespaces` assert only positive matches, so widening `ARGOCD_ACM_NS_REGEX` to `.*` passes — the dangerous direction, since that regex selects which Applications get paused. The filter test passes against a stub `return True` and consults neither Python nor Bash. |
| R3-T5 | Medium | Tests | `tests/test_ci_guardrails.py:31-38` is an exact-version denylist, not a floor (`actions/checkout@v3`, Node16 and EOL, passes), and its positive assertions match the concatenated corpus, so regressing one workflow passes while another still carries `@v6`. `upload-artifact`, `cache`, `download-artifact`, and `github-script` are unguarded. |
| R3-T6 | Medium | Tests | `tests/properties/conftest.py` registers `ci` and `deep` Hypothesis profiles, but `HYPOTHESIS_PROFILE` is set nowhere in the repo, so CI silently runs `dev` (50 examples) across the eight `test_*.py` property modules (count corrected 2026-07-26; the figure includes `test_scaffolding.py`). `tests/properties/test_scaffolding.py:17-19` cannot detect this — it reads the same env var with the same default and compares against a duplicated literal table. |
| R3-T7 | Medium | Tests | `run_tests.sh:97` and `.github/workflows/ci-cd.yml:46` measure and upload coverage with no `--cov-fail-under` and no Codecov threshold, so coverage can regress arbitrarily. This is the mechanism by which `R3-T1` stayed invisible. |
| R3-T8 | Medium | Tests | `tests/release/conftest.py:52-53` accepts `ACM_RELEASE_PROFILE` as equivalent to the explicit `--release-profile` flag, so a stale shell export turns a plain `pytest tests/` into a real-cluster run with no confirmation. `test_lab_controller_phase8j_live_opt_in.py` models the correct pattern (explicit allowlist plus a separate live-contact flag). |
| R3-T9 | Medium | Tests | `create_mock_step_context` is byte-identical in four workflow suites and its sibling `mock_state_manager` has already drifted in `test_finalization.py`. There is **no `tests/conftest.py`**, contradicting `AGENTS.md:337`. The helper is also an unpinned hand-rolled double of `lib/utils.py:808 StepContext`, which works against a real `StateManager` on `tmp_path`. |
| R3-T10 | Medium | Tests | `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/action/test_checkpoint_phase_runtime.py:661` seeds stale `operational_data`, `errors`, and `report_refs` but asserts none of them, despite `checkpoint_phase.py:44-45` naming that exact hazard. It also asserts only the in-memory result and never re-reads the checkpoint file. |
| R3-T12 | Medium | Tests | Found during the 2026-07-26 revalidation. `tests/test_api_literal_guardrails.py:11,15-18` walks only `MODULES_DIR`, so the `R2-H2` residual — 7 hardcoded `cluster.open-cluster-management.io` literals at `lib/rbac_validator.py:118,149,154,193,194,238,261` — is outside the guardrail's scan root and can grow without failing CI. A guardrail with a blind spot reads as coverage it does not provide. |
| R3-T11 | Low | Tests | Batch: `time.sleep(0.05)` mtime dependence (`test_post_activation.py:1846`); the 1279-line doc-substring module whose `_assert_no_real_live_config_literals` misses `sha256~` tokens, `client-certificate-data` blobs, and real FQDNs; the tautological `assert "tests" in text` (`test_ci_guardrails.py:48`); a needlessly `@_requires_opt_in`-skipped blocks-without-opt-in assertion; two vacuous `validate_rbac_permissions` tests; and the `OC_VERSION=4.21` pin that will rot. **Withdrawn 2026-07-26:** the import-time `sys.modules` stubbing at `tests/test_rbac_collection_parity.py:18-34` was called a dead fallback; it is not — it supports root-lane CI jobs running without `ansible-core`, which `AGENTS.md` requires. |
| R3-Q1 | Medium | Quality | Counts re-measured 2026-07-26, superseding the original "twelve files, seven crossing": **36** tracked Python files exceed 1000 lines (excluding vendored `container-bootstrap/get-pip.py`), and **21** crossed the threshold in this branch measured against merge base `aca2d296`. Largest new arrivals: `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/action/test_checkpoint_phase_runtime.py` 2111, `tests/properties/strategies.py` 1843, `tests/release/lab_controller/read_only_preflight_pilot.py` 1732. Growth in pre-existing files: `test_rbac_validator.py` 589→1312, `test_argocd.py` 448→1192, `lib/rbac_validator.py` 794→1131, `modules/post_activation.py` →1622, `test_post_activation.py` →2744. |
| R3-Q2 | Low | Quality | Counts verified 2026-07-26: 44 `WORKFLOW_*` / `DRY_RUN_*` / `OPERATION_*` / `*_MESSAGE` entries in `lib/constants.py`, of which **35 are referenced from exactly one external file** — none is unreferenced. They are log and banner text, not shared configuration (`WORKFLOW_BLANK_LINE = ""`, `WORKFLOW_BANNER = "=" * 60`), so the indirection costs a jump to a 381-line module to read a log line. |
| R3-Q3 | Low | Quality | Corrected 2026-07-26: the original "twelve" was an overcount. `acm_switchover.py:964-1302` holds 15 functions, of which a subset — including `:964-981`, `:1205-1218`, and `:1221-1302` — are same-signature one-line pass-throughs to `lib.*`; `run_setup`, `_prepare_runtime`, `main`, and hook construction are substantive and must stay. If the delegates exist only as test seams, patching the `lib` functions at their call sites is equally testable and shorter. |
| R3-Q4 | Low | Quality | `scripts/release/run_lab_role_controller.py:15-42` puts `REPO_ROOT` on `sys.path` and imports seven modules from `tests.release.lab_controller.*`, making the test tree a runtime dependency of a `scripts/` entrypoint. |
| R3-X1 | Low | Python | Both full suite runs end with `ResourceWarning: unclosed file … state.json.run.lock`; a `StateManager` run-lock handle is leaked. Surfaced by the tests, but the fix belongs in `lib/utils.py`. |

### Categories Verified Clean

Recorded so future reviews do not re-derive them:

- Collection RBAC manifests under `roles/rbac_bootstrap/files/deploy/rbac/**` are
  byte-identical to `deploy/rbac/**` across all eight files (seven plus the
  decommission extension); `argoproj.io/applications` carries `get`, `list`,
  and `patch` — no `create` and no `delete`, so no verb can create or remove an
  Application. No parity drift.
- The decommission `preserveOnDelete` classifier is a faithful reimplementation
  of `modules/decommission.py:_cluster_deployment_relationship`, including the
  three-way classification and both fail-closed raises.
- Credential handling: `no_log: true` on token minting and copy, `0700` output
  directories, `0600` files, `argv` list form, and legacy kubeconfig fields
  stripped from checkpoint identity records.
- `module_utils/path_safety.py` rejects traversal, shell metacharacters,
  out-of-root absolutes, and intermediate symlink escapes; `artifacts.py:64-70`
  uses `O_NOFOLLOW` with post-`mkdir` revalidation.
- SSAR handling in `run_ssar.yml` and `validate_permission_target.yml` treats
  failed/statusless results as denied — the one correct, fail-closed use of
  `failed_when: false` in the collection.
- Test suite: zero `assert True` / swallowed-assertion occurrences (verified).
  No module-level mutable fixtures and no observed order dependence, though
  absence of order dependence is not exhaustively provable.
  **Withdrawn 2026-07-26:** "no writes outside `tmp_path`" was **false**.
  `tests/release/reporting/test_summary.py:146-161` supplies a `FakeArtifacts`
  whose `run_dir` returns `Path("/tmp/run")`, and
  `tests/release/test_release_certification.py:62-68` `finalize_release_artifacts`
  does a real `mkdir` + `write_text` through it. Reproduced: `rm -rf /tmp/run &&
  python -m pytest tests/release/reporting/test_summary.py` recreates
  `/tmp/run/release-report.md` on disk. Tracked for cleanup under `R3-10`.
- Property-test oracles are genuinely independent (`test_path_safety_properties.py`
  models containment via `os.path.commonpath` and asserts *both* implementations
  reject; `test_validation_properties.py` writes standalone predicates). Only
  the `R3-T6` examples budget detracts.
- Dry-run state rollback at the orchestrator boundary is strong:
  `tests/test_main.py:980,1040` use a real `StateManager`, reload from disk, and
  assert phase, config, `completed_steps`, and `last_updated` are unchanged.
- The branch's consolidation work is sound and was explicitly not flagged:
  `lib/argocd_coordinator.py`, `lib/workflow.py` + `lib/operation_runners.py`,
  `wait_for_restore_deletion`, the `WaitConditionResult` typed contract, and the
  import-time `_derive_read_only_permissions` assertion.
- Per the standing parity contract, Python↔Ansible RBAC and constants
  duplication was excluded from all three passes and is not a finding.

### Planned Resolution Slices

Slices are implementation resolution boundaries, not assigned PR numbers or
automatic one-umbrella-per-PR batches. A boundary enters the numbered PR
sequence only after its own approved design/spec and implementation plan
satisfy the tracker gate in **Spec And Design Gate**.

The single global delivery sequence distinguishes delivery order from
impact/severity:

1. Immediate bounded regression delivery: combined `R3-01` / `TR2D-01`
   Argo CD scoped-discovery correctness.
2. Immediate bounded regression delivery: `R3-02` fail-closed verification
   gates.
3. Highest-impact P1 invariant: `SSA-01` distinct physical-hub guard. Its
   design may proceed in parallel with the first two deliveries.
4. `R3-06` checkpoint `reset_from` identity-bypass correction.
5. `TR2D-02` fresh-read Argo CD resume OCC parity.
6. `R3-03` Python fleet-scale klusterlet timeout correction.
7. The `SSA-09` API/exception-redaction sub-slice.
8. Lower-priority reporting, test infrastructure, structural, and quality
   work.

This is the delivery sequence. Placing two bounded regressions ahead of
`SSA-01` does not demote `SSA-01`'s P1 wrong-target safety impact.

| Slice | Status | Findings | Proposed resolution boundary | Required review |
| --- | --- | --- | --- | --- |
| R3-01 / TR2D-01 | merged | R3-A1, TR2D-M1, TR2D-L1 | PR [#200](https://github.com/tomazb/rh-acm-switchover/pull/200) merged exact head `0bc1a4b6701508f6c3d4cd898515d82b8a29b6a3` as `786f8325493c6086e136cb9694a9997557f12e02`, removing the skipped-task clobber, requiring positive success for every namespace read before aggregation, failing closed on malformed/failed/skipped/unreachable/mixed results, and adding executable non-mock retry and standalone-resume coverage. The aliases preserve provenance; they do not create duplicate implementation work. | Argo CD pause/resume safety; retry and standalone-resume paths; sanitized failure handling |
| R3-01b | planned | R3-A2, R3-A3 | Correct the two finalization register/set-fact clobbers and guard fixture/live-query semantics without coupling them to the Argo CD regression delivery. | finalization dry-run preview and fixture/live-read behavior |
| R3-02 | planned | R3-A4, R3-A5 | Make masked-error verification gates fail closed so an API error can never satisfy a drain or connectivity check. | Thanos/observability parity with Python; preflight go/no-go artifact integrity |
| R3-03 | planned | R3-P1 | Correct the timeout budget in place. The slice design must choose one explicit algorithm; it must not extract helpers or modules. Decomposition remains owned by `H3`. | post-activation failure semantics at fleet scale; parity with `SSA-03` |
| R3-04a | planned | R3-P2 | Recover Python preflight diagnostics only after sanitizing them before verbose logging; keep raw exception/API/credential material prohibited. | secret-handling and operator troubleshooting |
| R3-04b | planned | R3-A9 | Sanitize collection report/path data in both success and failure records. | report schema, filesystem-path exposure, success/failure parity |
| R3-05a | planned | R3-T1, R3-T2 | Add direct dry-run client/decorator contract tests, including controlled rejection of non-boolean `dry_run` values before mutation. | dry-run mutation safety |
| R3-05b | planned | R3-T3, R3-T4 | Make Argo CD Jinja/filter parity tests load their real artifacts and cover dangerous over-match/absent-automation cases. | mutation-resistance of parity guardrails |
| R3-06 | planned | R3-A6 | Scope the `reset_from` identity bypass to the pruned phase and revalidate identity after pruning instead of overwriting it. | checkpoint identity binding; interaction with `SSA-01` |
| R3-07 | planned | R3-P3, R3-P5 | Separate control signals and refusal messages from the durable `errors[]` log so the last error always names the real failure. | report-artifact accuracy; resume banner correctness |
| R3-08a | planned | R3-A7 | Correct klusterlet module/role failure ownership so the role can render diagnostics. | module failure contract and operator diagnostics |
| R3-08b | planned | R3-A8 | Declare and enforce collection minimum-Python compatibility. | EE portability and supported-version policy |
| R3-08c | planned | R3-A10 | Make observability restart retry-idempotent. | post-activation outage and checkpoint semantics |
| R3-08d | planned | R3-A11 | Correct plan/info `changed` semantics. | module contract and `_info` convention |
| R3-09a | planned under SSA-06 | R3-T5 | Establish immutable, approved CI action references through an action-to-release mapping or equivalent reviewed version policy. Full commit-SHA formatting alone is insufficient. | CI supply-chain policy and update process |
| R3-09b | planned | R3-T6, R3-T7 | Enforce the property-test profile and a reviewed coverage policy. | CI gate strength and ratchet behavior |
| R3-09c | planned | R3-T8 | Require explicit live-release opt-in independent of ambient environment. | accidental live-cluster execution |
| R3-09d | planned | R3-T9 | Consolidate the fixture once at most, or use real `StateManager` instances throughout. | test fidelity and fixture ownership |
| R3-09e | planned | R3-T10 | Verify checkpoint reset persistence, including stale-field removal after re-read. | checkpoint persistence safety |
| R3-09f | planned | R3-T12, R2-H2 residual | Expand the literal guardrail to the `lib/rbac_validator.py` residual and resolve the remaining overall H2 scope. | RBAC parity and guardrail scan-root completeness |
| R3-10a | planned | R3-P4 | Argo CD blocker blast-radius correction. | fail-closed scope and unrelated-workload impact |
| R3-10b | planned | R3-P6 | Request-timeout correction including the folded misleading-doc evidence formerly labelled `R3-P6b`. | API timeout and failure semantics |
| R3-10c | planned | R3-P7, R3-P9 | Report-artifact behavior and security. | dry-run truthfulness, file mode, exception redaction, safe path |
| R3-10d | planned + optional | R3-P12; optional R3-P13 | Kubeconfig file-mode correction; keep standalone-script packaging/error-message hardening explicitly optional. | local credential-file safety and supported packaging contract |
| R3-10e | planned | R3-T11 | Independently review and deliver the surviving test-cleanup sub-items without a catch-all runtime diff. | test validity and root-lane compatibility |
| R3-10f | planned | R3-P8, R3-Q2, R3-Q3, R3-Q4 | Constants and quality/layering items, delivered only through focused designs rather than a mixed cleanup batch. | constants policy, abstraction value, entrypoint ownership, Phase 9 boundary |
| R3-10g | planned | R3-X1 | State run-lock lifecycle correction. | cleanup on normal/error/signal exits |

`R3-P10` and `R3-P11` are rejected/non-actionable and appear in no planned
boundary. `R3-P13` appears only as optional hardening. `R3-Q1` is routed to the
existing `H3` design track ([#158](https://github.com/tomazb/rh-acm-switchover/issues/158)).
Every later implementation boundary above requires its own focused design,
rollback boundary, and verification plan.

### Resolution Requirements

#### R3-01 / TR2D-01: Scoped Argo CD Discovery Correctness

**Resolution**
- Register live-query results to names distinct from the published aggregate,
  then publish through a guarded `set_fact`.
- Require positive success for every namespace result before aggregating
  `resources`; missing, malformed, failed, or unreachable results fail closed
  with sanitized diagnostics.
- Cover the live scoped-discovery branch with a test that does **not** seed
  `acm_switchover_argocd_mock_apps`; the existing
  `resume_with_discovery_namespaces.yml` fixture takes the mock branch and
  cannot observe the defect.

**Acceptance criteria**
- Scoped discovery yields the aggregated Application list, and pause/resume
  patch the same Applications they would under cluster-wide discovery.
- One failed or malformed namespace read prevents aggregation and fails the
  operation; executable mixed-success coverage proves this.
- A `primary_prep` retry after checkpoint rehydration re-pauses every ACM
  Application paused in the first attempt.
- Standalone `playbooks/argocd_resume.yml` restores auto-sync on a real
  (non-mocked) Application set and reports a non-zero `restored` count.
- The implementation and tests cover `R3-A1`, `TR2D-M1`, and `TR2D-L1` once,
  under this shared boundary.

**Validation and review evidence**
- Approved design `R3-01-TR2D-01-DESIGN-A1` and implementation plan
  `R3-01-TR2D-01-PLAN-A2` are recorded under `docs/plans/`.
- Issue [#199](https://github.com/tomazb/rh-acm-switchover/issues/199) and PR
  [#200](https://github.com/tomazb/rh-acm-switchover/pull/200) own only this
  combined boundary. PR #200 merged into `ansible`, and issue #199 closed as
  completed.
- The original approved base is
  `17c9589d41767ce582fe46444f5e1feb07af0d30`; the rebased integration base is
  `ed7ec95ff8d20cc14b7ce0d8d733dcab247a44f6`; the independently validated
  pre-tracker-closeout head is
  `e3a313c2813cd1eea0872cca0c322d062ebda898`; and the final exact head is
  `0bc1a4b6701508f6c3d4cd898515d82b8a29b6a3`.
- A fresh independent final validator reviewed exact head
  `0bc1a4b6701508f6c3d4cd898515d82b8a29b6a3` from a clean detached worktree,
  independently confirmed the tracker-only delta from
  `e3a313c2813cd1eea0872cca0c322d062ebda898`, and returned
  `READY_TO_MERGE_EXACT_HEAD`.
- Exact-head GitHub Actions completed successfully: CI/CD Pipeline run `#847`
  and ansible-collection-foundation run `#609`. CodeRabbit status also
  completed successfully.
- Copilot reviewed 23 of 23 changed files and generated no comments.
- CodeRabbit's trivial predicate-deduplication nit was source-checked and
  rejected: the explicit identical predicates keep the safety boundary
  directly auditable, the executable negative matrix guards drift, and the
  proposed indirection would not correct behavior. Its generic 80% docstring
  warning was rejected because it is not a repository acceptance or CI gate.
- The PR-comment resolver posted those dispositions, made no code changes, and
  confirmed zero review threads and zero unresolved actionable feedback.
- `tests/integration/test_argocd_scoped_discovery_runtime.py` exercises the
  explicit present/absent predicates, negative shape matrix, sanitized failure
  boundary, non-mock primary-prep retry, and non-mock standalone two-hub
  resume.
- The targeted four-file lane produced `41 failed, 52 passed` when only its
  test artifacts were applied to approved base `17c9589d`, then `94 passed` on
  the corrected worktree. Collection unit tests passed `875`; combined
  collection/root tests passed `3955` with `29` expected skips; release helpers
  passed `1169` with `3` expected skips; and the strict `./run_tests.sh` root
  lane passed `1832` selected tests.
- Review-driven red evidence separately proved that an explicit empty
  namespace reached cluster-wide discovery and patching before the input guard;
  its regression now fails closed before discovery or mutation.
- The first draft-PR foundation run exposed a test-interpreter defect
  (`2 failed, 65 passed`) because implicit localhost selected
  `/usr/bin/python3`. Binding localhost to the pytest interpreter then exposed
  that the foundation job omitted the collection's declared Python Kubernetes
  runtime. The workflow now installs `kubernetes>=28.0.0`, an import-safe root
  guard requires it, and the exact local collection integration lane passes
  `67`.
- PR #200 merged exact head `0bc1a4b6701508f6c3d4cd898515d82b8a29b6a3`
  into `ansible` as merge commit
  `786f8325493c6086e136cb9694a9997557f12e02`. All evidence is non-live, and
  merge credit is limited to `R3-A1`, `TR2D-M1`, and `TR2D-L1`.

#### R3-01b: Finalization Register Clobbers

**Resolution**
- Correct `R3-A2` and `R3-A3` using distinct live-query and published-fact
  names, preserving execute-mode refresh and fixture-injection semantics.
- Add a repository guardrail that fails when a `register` target collides with a
  `set_fact` name in the same task file.

**Acceptance criteria**
- Finalization dry-run reports the Restore resources execute mode would delete.
- Injected fixture data is preserved on guarded discovery paths while required
  execute-mode refresh remains live.
- The guardrail fails red against either reintroduced collision.

#### R3-02: Fail-Closed Verification Gates

**Resolution**
- Key drain and connectivity verdicts on positive evidence
  (`resources is defined`, expected counts) rather than on `.failed`, or replace
  `failed_when: false` with `block`/`rescue` so genuine errors stay failures.
- Follow the shape already correct in
  `roles/post_activation/tasks/verify_observability.yml:146,222`.
- Preserve the documented Python behavior as the parity reference; record no
  intentional divergence.

**Acceptance criteria**
- A 403, timeout, or connection error during compactor verification fails the
  phase instead of reporting the compactor drained.
- The `until` retry loop cannot exit successfully on a result that carries no
  `resources` key.
- Preflight hub connectivity reports `fail` for unreachable hubs, expired
  tokens, and wrong `server:` URLs, and that verdict reaches
  `preflight-report.json`.
- Negative tests cover each masked-error case for both hubs.

#### R3-03: Klusterlet Verification Timeout Budget

**Resolution**
- Choose one explicit algorithm during the slice design, then correct the
  timeout budget in place so queued-but-healthy clusters are never classified
  as timed out.
- Keep the `F2` / `PR 03` fail-closed direction intact: a genuine per-cluster
  timeout must still raise.
- Do not extract helpers or modules in this correctness slice. Module
  decomposition belongs exclusively to the `H3` design track.

**Acceptance criteria**
- A fleet larger than `CLUSTER_VERIFY_MAX_WORKERS` with a slow subset reports
  only the genuinely slow clusters as timed out.
- The operator-facing message reports a duration that matches the deadline
  actually applied to that cluster.
- A real per-cluster timeout still raises `SwitchoverError`.
- Scale tests cover fleets that require multiple scheduling rounds.

#### R3-04a: Python Sanitized Diagnostic Recovery

**Resolution**
- Sanitize the validator message before any verbose/debug logging, while
  keeping the default public line bounded and stable.
- Preserve the existing durable state record so a failed preflight remains
  diagnosable whether or not `--report-dir` was passed.
- Make an unmapped check category a test failure rather than an anonymous
  fallback line.

**Acceptance criteria**
- `--validate-only -v` reports a sanitized reason for failure, including a
  useful BackupStorageLocation diagnostic.
- Sanitization occurs before verbose logging. Raw `ApiException` strings, API
  bodies, tokens, kubeconfig content, Secret content, and credential-bearing
  endpoints remain prohibited at every verbosity.
- Default output and state/report serialization retain their existing public
  contracts, and adding a validator without a category constant fails a
  guardrail test.

#### R3-04b: Collection Report And Path Sanitization

**Resolution**
- Stop re-adding kubeconfig, checkpoint, and report paths through
  `acm_input_validate.py` results after the `hubs` block is sanitized.
- Apply the policy to both successful and failed validation records, including
  failure details and fallback paths.

**Acceptance criteria**
- `preflight-report.json` contains no filesystem paths for hubs, checkpoint, or
  report directory in either success or failure records.
- Failure-path tests prove sanitization is not limited to successful detail
  records.

#### R3-05a: Direct Dry-Run And Decorator Contracts

**Resolution**
- Add a direct dry-run test for every mutating `KubeClient` method, following
  `tests/test_kube_client.py:225`, so guard removal fails at the client boundary
  rather than relying on fully-mocked workflow suites.
- Pin a fail-fast boolean contract for the decorator: exact `True` skips the
  mutation, exact `False` executes it, and every non-boolean value raises a
  controlled failure before mutation.

**Acceptance criteria**
- Removing any `if self.dry_run:` guard turns at least one test red.
- `dry_run = 1`, string values, and `MagicMock()` all fail before mutation;
  exact `True` and `False` preserve skip/execute behavior.

#### R3-05b: Argo CD Jinja And Filter Parity

**Resolution**
- Rewrite the Jinja parity test to load and evaluate `pause.yml`, and correct
  the expectation that a missing `automated` key yields a patch.
- Add negative assertions to the namespace and filter parity tests so
  over-matching fails.

**Acceptance criteria**
- Rewriting `pause.yml`'s Jinja to different semantics fails the parity test.
- Broadening `ARGOCD_ACM_NS_REGEX` toward `.*` fails; a stub
  `is_acm_touching_application` returning `True` fails.

#### R3-06: Scoped `reset_from` Identity Validation

**Resolution**
- Treat `reset_from` as a phase-scoped prune, not a run-wide identity bypass.
- Revalidate `operation_identity` after pruning instead of overwriting it with
  the currently configured expectation.
- Coordinate with `SSA-01` so distinct-hub validation and per-role identity
  binding remain complementary.

**Acceptance criteria**
- A `reset_from` value left in group_vars does not disable identity validation
  for unrelated phases or subsequent runs.
- A checkpoint whose recorded hub UIDs no longer match live hubs fails after
  pruning.
- Existing legitimate phase-reset workflows continue to work.
- Tests cover run-wide bypass, cross-run persistence, and post-prune identity
  mismatch.

#### R3-07: Error Channel Hygiene

**Resolution**
- Carry the "re-run Argo CD pause on retry" signal in configuration or explicit
  state rather than by appending to `errors[]`.
- Stop recording state-refusal messages as workflow errors; refusal should not
  mutate durable state.

**Acceptance criteria**
- A post-activation failure with `--argocd-resume-on-failure` produces a report
  artifact naming `post_activation`, and the resume banner shows the real error.
- Repeated refused reruns do not grow `errors[]` and do not change
  `get_last_error_phase()`.
- The retry still re-runs Argo CD pause.

#### R3-08a-d: Independent Collection Boundaries

These are four independent design, rollback, and verification boundaries:

- `R3-08a` / `R3-A7`: return probe outcomes without task-level `failed: true`,
  let the role own failure via `failed_when`, and prove role diagnostics render
  for failed, wrong-hub, and skipped clusters before abort.
- `R3-08b` / `R3-A8`: replace unsupported module-level union expressions,
  declare the collection's minimum Python version, and import-test that exact
  floor in CI.
- `R3-08c` / `R3-A10`: make observatorium-api restart retry-idempotent and prove
  a resumed post-activation does not re-annotate it.
- `R3-08d` / `R3-A11`: make `acm_restore_info` informational and report
  `acm_backup_schedule.changed` only when a resource is actually modified.

Do not combine these into one collection-contract PR merely because the
original umbrella was named `R3-08`.

#### R3-09a-f: Independent Test-Infrastructure Boundaries

- `R3-09a` / `R3-T5` is routed through `SSA-06`: use an approved
  action-to-release mapping or equivalent reviewed version policy, apply it per
  workflow/action, and cover checkout, upload/download artifact, cache, and
  github-script. A full commit SHA proves immutability but is insufficient by
  itself to prove that the approved action release is in use.
- `R3-09b` / `R3-T6` + `R3-T7`: load the CI Hypothesis profile observably and
  establish a reviewed coverage floor/ratchet.
- `R3-09c` / `R3-T8`: require explicit live-release authorization so an ambient
  `ACM_RELEASE_PROFILE` alone cannot contact a cluster.
- `R3-09d` / `R3-T9`: `create_mock_step_context` must exist **at most once**;
  zero is valid when all affected suites use real `StateManager` instances on
  `tmp_path`. In either design, a `StepContext` semantic change must be visible
  to the workflow suites.
- `R3-09e` / `R3-T10`: assert removal of seeded `operational_data`, `errors`,
  and `report_refs`, then re-read the checkpoint file and assert persistence.
- `R3-09f` / `R3-T12`: extend the scan root to the
  `lib/rbac_validator.py` residual and resolve the remaining overall
  `R2-H2` scope without weakening Python/collection parity checks.

Each boundary receives separate rollback and focused verification. A combined
CI-policy design may prove that `R3-09a` and an `SSA-06` delivery are identical;
until then they are cross-references, not duplicate PRs.

#### R3-10a-g: Residual Inventory Boundaries

`R3-10` is an inventory umbrella, not a future implementation PR:

- `R3-10a` / `R3-P4`: Argo CD blocker blast radius. Preserve intentional
  fail-closed behavior and review only the non-ACM-scoped breadth.
- `R3-10b` / `R3-P6`: bounded request timeout and corrected documentation,
  including folded `R3-P6b` evidence.
- `R3-10c` / `R3-P7` + `R3-P9`: truthful dry-run report state, secure report
  mode/content, sanitized exception data, and preservation of the existing
  validate → `mkdir` → revalidate path-safety sequence.
- `R3-10d` / `R3-P12` plus optional `R3-P13`: correct existing kubeconfig
  permissions. Treat the companion-script guard only as optional
  packaging/error-message hardening because supported documentation keeps
  `constants.sh` present.
- `R3-10e` / `R3-T11`: independently validate and deliver the surviving test
  cleanup sub-items; preserve the root-lane no-`ansible-core` import support.
- `R3-10f` / `R3-P8` + `R3-Q2` + `R3-Q3` + `R3-Q4`: constants and
  quality/layering changes only after a focused abstraction/ownership design;
  Phase 9 trust boundaries remain unchanged.
- `R3-10g` / `R3-X1`: close the run-lock handle on normal, error, and
  signal-driven lifecycle exits.

Every boundary requires a focused design, rollback boundary, and verification
plan. `R3-P10` and `R3-P11` are rejected and must not re-enter this inventory.
`R3-Q1` remains an input to `H3` / issue #158, not a parallel queue.

## Revalidation (2026-07-26)

Full-file revalidation of this tracker against `ansible` HEAD `4fed598c`, run as
three independent read-only passes: (1) re-verification of every finding this
file claims is resolved or merged, (2) per-criterion re-check of the ten `SSA-*`
slices, and (3) metadata and internal-consistency audit — real GitHub PR and
issue states, referenced-path existence, the Verification Command Reference,
worktree conventions, and cross-section contradictions.

**Why:** this tracker makes three kinds of claim that decay silently — *source*
claims ("`F31` is resolved"), *status* claims (slice, PR, and issue states), and
*pointer* claims (spec paths, "Likely Files", command references). A tracker that
is wrong about its own state is worse than no tracker, because work gets skipped
on the strength of a stale "resolved".

**Historical scope of the first 2026-07-26 pass:** the original 40 `R3-*`
claims were not all re-run in that first pass. This corrective pass then
re-read every directly relevant source/test named by the operator, reconciled
PR #196, and revalidated the disputed taxonomy and sequencing claims. In
particular, `R3-P10`, `R3-P11`, `R3-P13`, and folded `R3-P6b` now carry the
source-backed dispositions above.

### Verified accurate, no change required

- **All 48 pre-existing PR URLs** in the PR Sequence table (every row except the
  `48` row this slice added) are genuinely `MERGED`, with titles matching each
  row's description. No row claims `merged` for a PR that is open, draft, or
  closed-unmerged.
- **All 7 GitHub issues** (#152-#158) are genuinely `OPEN`, with titles matching
  the findings attached to them.
- **The Verification Command Reference** is fully accurate: all five commands
  resolve and collect (61, 867, 238, and 140 tests respectively);
  `run_tests.sh` is present and executable.
- **Review #3 baseline figures are exact**: 923 commits, 745 files,
  +146,459/-11,180, merge base `aca2d296`, 184 test files, 867 collection tests.
  The `R3-Q1` line counts are exact.
- **Counts add up**: `SSA` 17 findings / 10 slices / 2 P1 + 10 P2 + 5 P3.
  Review #3 has 40 original claims plus 2 revalidation-added raw claims; folding
  `R3-P6b` into `R3-P6` yields 41 unique IDs. Their exclusive dispositions are
  37 actionable, 1 optional hardening, 2 rejected/non-actionable, and 1 routed
  to `H3`.
- **Every referenced spec and findings document exists.** The one absent path,
  `docs/plans/2026-04-10-ansible-collection-rewrite-design.md`, is absent *by
  design* — its absence is finding `F25`.
- **19 of 21 re-verified resolved rows are CONFIRMED still resolved** — or 20 of
  22 if `R2-M1`'s two parts are counted separately. `F41` and `R2-H2` are the
  two partial rows. Evidence is
  current-source evidence, including `F31`, `F34`, `F35`, `F37`, `F39`, `F40`,
  `F42`, `R2-H1`, `R2-M1` (both parts), `R2-M2` through `R2-M5`, `H1`, `R2-H3`,
  and all four release-tooling dedups `R2-H4`/`R2-M6`/`R2-M7`/`R2-M8`. Nothing
  checked had its fix reverted.

### Deterministic Review #3 count check

Run from the repository root. This parses the canonical findings table rather
than trusting prose arithmetic:

```bash
python - <<'PY'
import re
from pathlib import Path

text = Path("thermos-resolution-plan.md").read_text()
findings = text.split("### Validated Findings", 1)[1].split("### Categories Verified Clean", 1)[0]
ids = set(re.findall(r"^\| (R3-(?:A|P|T|Q|X)\d+) \|", findings, re.MULTILINE))
matrix = text.rsplit("## Finding Validation Matrix", 1)[1].split("\n## PR Sequence", 1)[0]
matrix_ids = set(re.findall(r"^\| (R3-(?:A|P|T|Q|X)\d+) \|", matrix, re.MULTILINE))
expected = (
    {f"R3-A{i}" for i in range(1, 12)}
    | {f"R3-P{i}" for i in range(1, 14)}
    | {f"R3-T{i}" for i in range(1, 13)}
    | {f"R3-Q{i}" for i in range(1, 5)}
    | {"R3-X1"}
)
optional = {"R3-P13"}
rejected = {"R3-P10", "R3-P11"}
routed = {"R3-Q1"}
actionable = ids - optional - rejected - routed
assert ids == expected
assert matrix_ids == expected
assert len(actionable) == 37
assert "| R3-P6b |" not in findings
assert "| R3-P6b |" not in matrix

review3 = text.split("## Thermos Review #3", 1)[1].split("## Revalidation", 1)[0]
slice_table = review3.split("### Planned Resolution Slices", 1)[1].split(
    "### Resolution Requirements", 1
)[0]
planned_ids = set()
for row in slice_table.splitlines():
    if not row.startswith("| R3-"):
        continue
    columns = [column.strip() for column in row.strip("|").split("|")]
    planned_ids.update(re.findall(r"R3-(?:A|P|T|Q|X)\d+", columns[2]))
assert actionable <= planned_ids
assert not (rejected | routed) & planned_ids
print(
    "original=40 revalidation_added=2 folded=1 unique=41 "
    "actionable=37 optional=1 rejected=2 routed=1 "
    f"canonical_rows={len(ids)} matrix_rows={len(matrix_ids)}"
)
PY
```

### All ten SSA slices remain `planned`

All ten SSA slices remain incomplete/planned; some individual acceptance
criteria are already satisfied as recorded in the per-slice notes.
Specifically, `SSA-01`'s unreadable-identity bullet and `SSA-03`'s
integer/lower-bound worker validation are already satisfied by pre-existing
code.
`git show --stat 4fed598c` touches only
`tests/release/lab_controller/*`, `tests/release/test_lab_controller_*`,
`tests/release/README.md`, `CHANGELOG.md`, and `docs/`; `78126c05` is docs-only;
and the three safe-logging commits merged on 2026-07-18, *before* the 07-20
check. No product-runtime file under `lib/`, `modules/`, `acm_switchover.py`,
`scripts/`, `ansible_collections/`, `.github/workflows/`, or
`container-bootstrap/` changed in the interval. No status flips.

Four slices gained **Revalidation note** blocks recording scope corrections that
do not change status — see `SSA-01` (bullet 3 already satisfied; harness-side
design donor), `SSA-03` (worker validation half-satisfied), `SSA-04` (Phase
8J/9B landed none of it), and `SSA-09` (scope widened to the `str(ApiException)`
leak class, and the safe-logging commits credited to `R3-P2` where they belong).

`SSA-A2` / `SSA-P2`, the two P1 findings, remain **unguarded in product code**:
nothing in the CLI or the collection rejects a primary and secondary context
that resolve to the same physical cluster.

### Corrections applied to this file

| Area | Correction |
| --- | --- |
| `F41` row | Changed from `resolved` to **partially regressed**. The Python side is confirmed working, but the Ansible side is inert and Argo CD pause/resume silently no-op. Cross-linked to `R3-A1` / `R3-01`. The tracker had recorded the regression as a Review #3 finding while its own `F41` row still read "resolved" — the exact failure mode this pass exists to catch. |
| `R2-H2` row | Changed to **partial as delivered**. `modules/` is genuinely clean, but 7 literals remain in `lib/rbac_validator.py`, outside the guardrail's scan root. |
| Worktree convention | `.worktrees/` → `.claude/worktrees/` at the Tech Stack line, Current Branch Notes, and the `H1` and `43` rows. Confirmed current via `git worktree list` and `.git/info/exclude:5`. The historical `PR 12` flake8 item remains `.worktrees/`, matching `setup.cfg` and that implementation-era scope. |
| Path pointers | `docs/variable-reference.md` → `ansible_collections/tomazb/acm_switchover/docs/variable-reference.md` (2 sites); `R3-T10`'s checkpoint test path prefixed with `ansible_collections/tomazb/acm_switchover/`. |
| `PR 34` merge date | 2026-07-02 → 2026-07-03 (`gh` reports `2026-07-03T05:30:44Z`). Every other merge date matches `gh` exactly. |
| Spec And Design Gate | No longer points at "the remaining deep-scan queue (`PR 24` onward)" — every implementation row through `PR 47` and `H1` is merged. Now points at the open `SSA-*`, `R3-*`, and `TR2D-*` boundaries. |
| `R3-Q1` assignment | Removed from slice `R3-10`, which now covers `R3-Q2`-`R3-Q4`. `R3-Q1` belongs to the `H3` track (#158), as three other places in this file already said. |
| Review #2 arithmetic | "12 new findings" then enumerating 21 → "12 new medium/high findings ... plus 9 `R2-L*` low-severity items". |
| `R2-H2` call-site count | Three different figures (48 / 47 / 49+7) reconciled: the review counted 49 top-level sites in `modules/`; delivered `PR 34` scope was 56 after 7 more were found in `modules/preflight/`. |
| Section dates | "Current Completion Summary (2026-07-19)" → 2026-07-25, since it contains 07-20 and 07-25 content. "Status after merged follow-up PRs 22-26" → 22-31, matching its own bullets. |
| Historical HEADs | "Current `ansible` HEAD `ac041f6`" / `f52a19d4` → "HEAD at that time". Actual HEAD is `4fed598c`. |
| Status vocabulary | Extended to cover `open/deferred`, `open/split`, and `open/design track`, which the deferred-follow-up and `H3` tables already used. |

### Nine orphaned Review #1 findings, now tracked

`M1`, `M3`, `M5`, `L2`, `L4`, `L6`, and `L7` were declared "still open and
unchanged" in the Review #2 headline but appeared in **no** table in this file —
not the validation matrix, not the deferred-issues table, not any slice or PR
row — while the Completion Summary declared the queue complete. They had been
invisible since 2026-07-02. `L3` and `L5` were worse: listed as real by Review #1,
then silently dropped from Review #2's re-confirmation list **with no stated
rationale anywhere**, including in the Review #2 findings document itself.

All nine were re-verified against source. **All nine are still open.** None was
fixed in passing, superseded, or restructured away. Each now has a Finding
Validation Matrix row with current evidence. Two carry a resolution pointer
rather than standing alone:

- `L4`'s four duplicated `re.sub(r"https://([^:/]+).*", ...)` sites are exactly
  the sites `SSA-PY2` describes and `SSA-03` will rewrite. Extract the shared
  helper inside `SSA-03`; do not open a separate cleanup.
- `M2` (see below) is superseded by `R3-Q2`.

**A mis-credit was found while verifying this.** The `PR 40` row claimed it
resolved "`R2-M3` + existing `M2`". Review #1's `M2` is `lib/constants.py`
UI string-table sprawl — unrelated to the restore-deletion-wait dedup `PR 40`
actually delivered. `lib/constants.py` is still 381 lines with 44 top-level
`WORKFLOW_*`/`OPERATION_*`/`DRY_RUN_*`/`*_MESSAGE` constants. `M2` was never
resolved; it is now recorded as open and superseded by `R3-Q2`, and the `PR 40`
row and execution-order entry have been corrected to claim `R2-M3` only.

This is the most consequential class of error this pass found: a finding marked
resolved by a PR that did different work is indistinguishable from real
progress, and nothing in the file would have surfaced it.

### Revalidation-added claims

Two raw claims were found while verifying other claims. They use Review #3-era
labels because they belong to the same open queue, not to a new review round.

- **`R3-T12`** (Medium, slice `R3-09`) — `tests/test_api_literal_guardrails.py`
  walks only `MODULES_DIR`, so the `R2-H2` residual in `lib/rbac_validator.py`
  is unguarded and can grow without failing CI. A guardrail with a scan-root
  blind spot reads as coverage it does not provide.
- **`R3-P6b`** — supporting documentation evidence folded into `R3-P6`, not a
  second implementation finding. The `delete_custom_resource` docstring at
  `lib/kube_client.py:1017-1018` documents a client-default timeout the code
  never requests.

### Fixed outside this file

- `.gitignore` — added `.claude/worktrees/`. Only `.worktrees/` was committed,
  and the real path was ignored solely through the local, uncommitted
  `.git/info/exclude`, so a fresh clone would show worktree contents as
  untracked.
- `AGENTS.md` — corrected the worktree path in the Thermos Review Resolution
  rules to `.claude/worktrees/thermos-*` (also dropping the `NN`, since the
  `H1` row uses a non-numeric name).

### Recorded, not acted on

Three defects are recorded here for a later decision rather than changed in this
pass:

1. **`AGENTS.md:337` claims a `tests/conftest.py` that does not exist.** This is
   already tracked as `R3-T9`. Correcting the doc now would make it true by
   lowering the claim rather than by adding the missing shared fixtures, so the
   doc stays wrong until `R3-09` resolves it.
2. **`AGENTS.md:485-509` contains no design/spec gate**, while this tracker's
   State Tracking Rules and the whole Spec And Design Gate section require
   `superpowers:brainstorming` → approved design → `superpowers:writing-plans`
   before implementation. Either mirror the gate in `AGENTS.md` or stop
   describing it as a repo-wide rule.
3. **`AGENTS.md:511-537` requires the `code-review` skill before opening and
   before merging every PR**, but rows `34`-`42` and `44`-`47` record no review
   evidence while rows `04`-`11` and `19`-`33` do. Either add the gate to the
   State Tracking Rules and backfill the evidence, or explicitly waive it for
   those rows.

### Adversarial validation of this pass

The revalidation was itself re-checked by an independent Codex run against the
same worktree, instructed to treat every added claim as unproven and anchor each
verdict to a file, line, or SHA it had actually read. It returned a **negative**
overall verdict on the first draft. Its substantive hits were all
self-inflicted by the revalidation edits and are now fixed:

- The Spec And Design Gate said "every row in the PR Sequence table is
  `merged`" *after* this slice added a `ready_for_review` row `48`.
- The deep-scan queue still read "`F41` Resolved by `PR 24`" while the matrix
  row had been corrected to partially regressed — the same inconsistency this
  pass exists to catch, reintroduced one section away.
- The Current Branch Notes implied all `.worktrees/` rows were corrected; only
  `H1` and `43` were, by design.
- "Every `SSA-*` acceptance criterion remains unmet" contradicted the per-slice
  notes added in the same commit, which record two criteria as already satisfied.
- The Review #3 findings table was labelled 40 while carrying 42 raw-claim rows
  after `R3-T12` and `R3-P6b` were inserted. The corrected table has 41 unique
  rows because `R3-P6b` is folded into `R3-P6`.
- "19 of 22 resolved claims" conflated row counting with atomic-claim counting.
- Counts corrected against a fresh measurement: `lib/constants.py` has 44
  matching constants, not 43; the bundled RBAC set is 8 files, not 7, and
  `argoproj.io/applications` carries `get,list,patch` rather than being
  "patch-only"; 37 Python files exceed 1000 lines repo-wide, where `R3-Q1` had
  quoted a branch-scoped figure without saying so.

It also disproved several findings' details. Corrected in place, with the
verdict recorded on the affected row: `R3-P2` (diagnostics **are** always
persisted to state; only artifact export is `--report-dir`-gated), `R3-P8` (two
of four constants are referenced, six inline sites not seven), `R3-Q1` (36
files over 1000 lines and 21 crossing in-branch, not twelve and seven),
`R3-Q2` (44 constants, 35 with exactly one consumer), `R3-Q3` (the "twelve
delegates" figure was an overcount), `R3-T6` (eight property modules), and
scope narrowing on `R3-A3`, `R3-A6`, `R3-A9`, `R3-A11`, `R3-P5`, `R3-P7`, and
`R3-P13`. One `R3-T11` sub-item was **withdrawn**: the `sys.modules` stubbing
is not a dead fallback, it supports root-lane CI without `ansible-core`.

**A clean-category claim was withdrawn.** Codex reported that the suite writes
outside `tmp_path`. That was initially rejected here on the grounds that
`FakeArtifacts.write_json` only stores to a dict — which was a misread:
`finalize_release_artifacts` (`tests/release/test_release_certification.py:62-68`)
takes `artifacts.run_dir` and performs a real `mkdir` + `write_text`.
Reproduced by `rm -rf /tmp/run && python -m pytest
tests/release/reporting/test_summary.py`, which recreates
`/tmp/run/release-report.md`. Codex was right; the clean category is corrected
above and the cleanup is tracked under `R3-10`.

**Process claims in this file are not repo-verifiable and should not be read as
evidence.** The review-agent division of labour, the empirical
`ansible-playbook` confirmations, the two 3,079-pass suite runs, the
guard-removal mutation experiments behind `R3-T1`/`R3-T2`, and the GitHub issue
states all live outside git. They are recorded as provenance, not proof. A
reader re-deriving any finding should re-run the check rather than trust the
narration.

## Spec-Sourced Safety Review (2026-07-29)

Origin: seven safety design specs written against `main` (external hypothesis
source, not part of this branch) were cross-validated against `ansible` HEAD
`0bf55db9` by two independent read-only passes (Claude exploration agents, then a
full Codex revalidation: 20 confirmed, 7 partially amended, 0 refuted). Only
findings confirmed open on `ansible` and untracked above are recorded here, grouped
into the **six** new slice designs in `docs/plans/2026-07-29-*-design.md` (six, not
seven: tracked-elsewhere issues were excluded and the kubeconfig design folds into
existing `SSA-03`). Each slice follows the standard Spec And Design Gate (the
designs exist; implementation plans are still required).

### Validated findings

| Finding | Severity | Surface | Summary |
| --- | --- | --- | --- |
| R4-A1 | High | Bash | `scripts/argocd-manage.sh:341,345` pause builds `jq 'del(.automated)'` + `--type=merge` — RFC 7396 no-op; auto-sync stays enabled while the script prints `Paused` and journals success. Python/collection are fixed; Bash is divergent. |
| R4-A2 | Medium | All three | `automated.enabled: false` (Argo CD ≥2.13) classified as active auto-sync in Python (`lib/argocd.py:423-426`), Bash (`:326-330`), and collection (`pause.yml:58-61`). |
| R4-A3 | Medium | All three | Resume sends the whole stored `syncPolicy` (overwrites pre-existing keys with stale values; merge patch does not delete added siblings) and never verifies post-resume; Bash also lacks the RV precondition. `TR2D-02` covers collection OCC parity only. |
| R4-A4 | Medium | Python + collection | Pause step is checkpointed (`modules/primary_prep.py:71-81`); a run resumed at ACTIVATION, and integrated decommission, never revalidate journaled pause state. |
| R4-B1 | High | Python + collection | Auto-import restore deletes the entire `import-controller-config` ConfigMap (`modules/finalization.py:1545-1553`; collection `state: absent`) — operator-owned keys destroyed; unset ownership only warns and leaves `ImportAndSync` behind. |
| R4-B2 | Medium | Python + collection | ConfigMap mutated before ownership recorded (`modules/activation.py:630-635`); collection ownership is `set_fact`, durable only via optional checkpointing. |
| R4-B3 | Medium | Python + collection | `data: null` raises `AttributeError` → misleading `SwitchoverError` or silent skip (`modules/activation.py:615-616`; both collection roles share the pattern). |
| R4-B4 | Medium | Python + collection | Decommission ignores an unrestored auto-import transaction. |
| R4-C1 | High | Python | MCH completion fails open: lingering non-operator pods only warn, MCH CR absence never re-checked, decommission reports success (`modules/decommission.py:420-455`). |
| R4-C2 | High | Python | Interactive refusal of MCO/ManagedCluster/MCH prompts logs a skip and still flows to `return True` (`modules/decommission.py:69-98`). |
| R4-C3 | Medium | Python + collection | No CR-absence proof or UID verification for MCO/MCH/ManagedCluster deletion; pod waits are namespace-wide with no label selector. |
| R4-C4 | Medium | Python | 404→`[]` (`lib/kube_client.py:724-748`) makes missing discovery indistinguishable from an empty inventory in `_delete_managed_clusters` (`modules/decommission.py:172-176`). |
| R4-C5 | Medium | Python + collection | No destination-observability check before source MCO deletion when destination observability was never detected (metrics continuity ends silently). |
| R4-D1 | High | Python + collection | Restores bind to the moving `latest` alias (`modules/activation.py:339-350,453,793-804`); the consumed backup is never journaled; resume re-resolves. |
| R4-D2 | Medium | Python | Explicit `--min-managed-clusters` replaces name enforcement with count-only; explicit `0` disables enforcement (`acm_switchover.py:869-875`). |
| R4-D3 | Medium | Python | 404→`[]` yields empty baselines on activation/post-activation inventory reads. |
| R4-D4 | Medium | Python + collection | Integrated teardown consumes no migration evidence. |
| R4-E1 | High | Python | A killed dry-run/validate-only leaves durable intermediate state (snapshot restore only in `finally`) that later runs trust; no crash marker. |
| R4-E2 | Medium | Python | Validate-only checkpoint restores phase/errors/timestamp only; preflight `config` writes leak (`lib/utils.py:482-505`). |
| R4-E3 | Medium | Python + collection | No run contract: resume silently accepts changed safety-critical options (`old_hub_action`, method, ArgoCD/auto-import management). |
| R4-E4 | Medium | Python | Locks are state-file-scoped (`lib/utils.py:156`); same physical hubs don't contend across different `--state-file` paths. |
| R4-E5 | Medium | Python | `--reset-state` removes the state file before the run lock exists (`acm_switchover.py:1073-1083`); `--force` resets progressed state. |
| R4-E6 | Low | Python | `_write_state` lacks a parent-directory fsync after `os.replace` (`lib/utils.py:367-384`). |
| R4-F1 | Medium | Python | Klusterlet-repair kubeconfig merge fails open: unreadable/oversized/YAML-invalid files are debug-skips, and the repair call site passes `max_size=0` (`modules/post_activation.py:1311-1317,1377-1404,1438-1444`); mutations proceed on the partial view. |
| R4-F2 | Medium | Python | The mutation client is built by re-reading kubeconfig files (`new_client_from_config`, `:1118-1134`) after manual matching — TOCTOU and dual-resolver disagreement. |
| R4-F3 | Low | Python | Duplicate entry names and duplicate YAML mapping keys silently last-write-win in the manual merge. |

### Planned resolution slices

| Slice | Status | Findings | Design | Proposed resolution boundary |
| --- | --- | --- | --- | --- |
| R4-01 | planned | R4-A1, R4-A2, R4-A3, R4-A4 | `docs/plans/2026-07-29-argocd-pause-correctness-residuals-design.md` | Minimal Bash pause fix (lifecycle stays `SSA-05`), shared tri-state auto-sync classification, `automated`-only resume with post-resume verification, journal-scoped destructive-phase gates. |
| R4-02 | planned | R4-B1, R4-B2, R4-B3, R4-B4 | `docs/plans/2026-07-29-auto-import-transaction-design.md` | Prior-state capture with durable intent before mutation, key-level restore, `data: null` normalization, decommission gate. |
| R4-03 | planned | R4-C1, R4-C2, R4-C3, R4-C4, R4-C5 | `docs/plans/2026-07-29-decommission-completion-design.md` | CR-absence proof with UID verification, refusal-aborts semantics, scoped strict-404 list, destination-observability gate. |
| R4-04 | planned | R4-D1, R4-D2, R4-D3, R4-D4 | `docs/plans/2026-07-29-migration-evidence-design.md` | Freeze `latest` to journaled concrete backup names at activation entry, additive name+count expectations with explicit waiver, strict inventory reads, evidence gate before teardown. |
| R4-05 | planned | R4-E1, R4-E2, R4-E3, R4-E4, R4-E5, R4-E6 | `docs/plans/2026-07-29-state-integrity-residuals-design.md` | Full-fidelity simulation snapshot with crash marker, parent-dir fsync, per-hub UID locks, reset-under-lock with narrowed `--force`, run contract. |
| R4-06 | planned | R4-F1, R4-F2, R4-F3 (+ SSA-PY2, SSA-A6) | `docs/plans/2026-07-29-kubeconfig-ambiguity-guard-design.md` | Extends `SSA-03`: fail-closed merge, duplicate-name rule, full-URL endpoint normalization, snapshot-built client, mutation barrier. `SSA-03` implementation should use this design. |

Cross-references (adjacent, not superseded): `SSA-01` (hub distinctness — excluded,
already tracked), `SSA-02` (decommission target/RBAC — complementary to `R4-03`),
`SSA-05` (Bash script lifecycle — owns everything beyond the `R4-A1` correctness
fix), `TR2D-02` (collection resume OCC parity), `R3-10a` (discovery blast radius —
`R4-01` gates are journal-scoped to avoid conflict), `R3-T3` (parity-test oracle),
`F19`/`F20` (unrelated refactors), `R2-M2`, `R3-P7`, `R3-A6`, `R3-X1`.

## Finding Validation Matrix

| Finding | Validation | Resolution PR | Notes |
| --- | --- | --- | --- |
| F1 | confirmed | PR 02 | `switchover.yml` can enter `primary_prep` while `restore_only` is true; validation should reject primary hub data in restore-only mode. |
| F2 | confirmed | PR 03 | Python klusterlet worker timeout is classified as unreachable/skipped and can fail open. |
| F3 | confirmed with nuance | PR 03 | Check mode is unsafe; Ansible does fail tasks that return `failed: true`, so the original "silent success" wording is overstated. |
| F4 | confirmed | PR 07 | RBAC docs understate required read-only cluster-scope resources and namespace verbs. |
| F5 | confirmed | PR 04 | Python Argo CD resume-only validates context names but skips live cluster UID binding. |
| F6 | confirmed coverage gap | PR 04 | Main-level hub identity wiring tests are missing. |
| F7 | confirmed coverage gap | PR 06 | Collection integration fixtures mostly use dry-run/validate modes, leaving execute-mode paths thinly covered. |
| F8 | confirmed | PR 08 | Collection defines managed-cluster RBAC permission constants but does not validate them. |
| F9 | confirmed | PR 05 | Collection reports/checkpoint identity include kubeconfig paths; Python reports do not. |
| F10 | confirmed | PR 06 | Finalization MCH discovery can reuse stale pre-seeded facts in execute mode. |
| F11 | confirmed | PR 07 | RBAC deployment guide recommends deprecated script instead of collection bootstrap playbook. |
| F12 | confirmed maintainability | PR 12 | Python orchestrator is large; refactor only after safety work. |
| F13 | confirmed maintainability | PR 12 | Python klusterlet verification method is complex; refactor after behavior fix. |
| F14 | confirmed maintainability | PR 12 | `validate_backups.yml` is large; refactor after safety work. |
| F15 | confirmed with nuance | PR 12 | `tests/test_main.py` lacks fixtures and is large; other cited files do use fixtures. |
| F16 | confirmed | PR 05 | Checkpoint `changed` and `report_refs` behavior is not idempotent enough. |
| F17 | confirmed | PR 09 | `acm_restore_info` can report `changed=true` in check mode. |
| F18 | confirmed cleanup | PR 09 | Unsafe kubeconfig fact handling exists in stale single-cluster task files; active path appears module-based. |
| F19 | confirmed | PR 11 | Managed-cluster expectation logic is duplicated between activation and post-activation. |
| F20 | confirmed with nuance | PR 11 | Activation auto-import version gating is duplicated; finalization version derivation is related but should stay separate if it needs fresh MCH data. |
| F21 | confirmed cleanup | PR 09 | Some public role facts drift from the `acm_switchover_` prefix. |
| F22 | confirmed | PR 10 | Preflight can short-circuit after RBAC failure and omit later findings. |
| F23 | confirmed cleanup | PR 09 | Helm `rbac.customNamespaces` is documented in values/README but unused in templates. |
| F24 | confirmed docs drift | PR 07 | Collection README still calls itself a foundation collection. |
| F25 | confirmed docs drift | PR 07 | Docs link to a missing 2026-04-10 Ansible design spec. |
| F26 | confirmed coverage gap | PR 08 + PR 12 | PR 08 aligned managed-cluster RBAC behavior; PR 12 adds full root-to-collection bundled RBAC manifest parity coverage. |
| F27 | confirmed test naming issue | PR 06 | A post-activation integration test name claims pending-cluster failure while asserting dry-run skip. |
| F28 | confirmed | PR 03 | Collection klusterlet probe treats broad API exceptions as skipped instead of failed. |
| F29 | confirmed | PR 14 | Python ACM version parsing treats suffixed modern versions as older than 2.12 and can route `BackupSchedule` handling to delete. Collection fails loudly on the same input, so behavior is not in parity. |
| F30 | resolved | PR 13 | Current CI-scope `black --check --line-length 120` passes on `ansible` at `4fbc352`. Keep strict formatting verification in every Round 6 PR. |
| F31 | resolved | PR 18 | Path safety now funnels through canonical `path_safety` helpers with adversarial parity coverage; the remaining Python/collection dual copy is an intentional import-boundary mirror guarded by tests. |
| F32 | resolved | PR 15 | `setup-rbac.sh` and merged kubeconfig generation now write under `umask 077` and create output directories with owner-only permissions. |
| F33 | resolved | PR 16 | Container base images are digest-pinned and `jq` / OpenShift client downloads are checksum-verified before installation or extraction. |
| F34 | resolved | PR 17 | Python and collection klusterlet remediation now patch/create `bootstrap-hub-kubeconfig`; managed-cluster RBAC/docs were realigned from `delete` to `patch`. |
| F35 | resolved | PR 19 | Helm render now rejects mutating `rbac.customValidatorRules` verbs through `validateValidatorCustomRules`, with static tests covering allowed and rejected cases. |
| F36 | resolved | PR 15 | Service-account token generation defaults were reduced to `24h`; longer lifetimes remain explicit operator opt-in. |
| F37 | resolved | PR 20 | Standalone collection `argocd_resume.yml` validates checkpoint hub UID identity against the live hubs before resuming Applications. |
| F38 | resolved | PR 21 | Python klusterlet verification now fails closed for broad API/client inspection failures instead of downgrading them to informational `unreachable`. |
| F39 | resolved | PR 22 | Python `--argocd-resume-only` now fails closed for legacy state without hub identity binding when `argocd_paused_apps` exist but `hub_identities` are absent. |
| F40 | resolved | PR 23 | Python dry-run Argo CD management now performs discovery and blocker reporting in parity with the collection dry-run path. |
| F41 | merged (2026-07-27) | PR 24; issue #199; PR #200 | Python scoped discovery remains correct. The collection correction on PR #200 gives scoped and cluster-wide queries distinct register ownership, validates every scoped item before publication, and adds non-mock retry/resume coverage. Exact validated head `0bc1a4b6701508f6c3d4cd898515d82b8a29b6a3` merged as `786f8325493c6086e136cb9694a9997557f12e02` after the final validator returned `READY_TO_MERGE_EXACT_HEAD`; issue #199 is closed as completed. |
| F42 | resolved | PR 25 | Python RBAC preflight now avoids repeated serial SelfSubjectAccessReview probes without losing reporting fidelity. |
| F43 | resolved | PR 26 | Release runtime parity now compares real resume, Argo CD, and RBAC/bootstrap outcomes instead of mostly artifact metadata. |
| F44 | resolved | PR 27-PR 31 | `PR 27` extracted runtime/bootstrap; docs-only `PR 28` recorded the remaining slice map; `PR 29`, `PR 30`, and `PR 31` completed operation/phase-flow runners, Argo CD resume safety, and CLI outcome/report orchestration respectively. GitHub PRs #102, #103, #104, #106, and #107 are merged, and the extracted `lib/` modules remain wired through `acm_switchover.py` with dedicated tests. |
| R2-H1 | confirmed | PR 36 | `delete_configmap`/`delete_pod` in `lib/kube_client.py` and the ACM ≤2.11 `delete_custom_resource` BackupSchedule call in `modules/primary_prep.py` have no request timeout; a hung API call can block PRIMARY_PREP indefinitely. |
| R2-H2 | confirmed, sharper framing of `H2`; partial as delivered (2026-07-26) | PR 34 | Original: `MANAGED_CLUSTER_API_GROUP` existed but was used in only 1 of the 49 call sites the review counted across `modules/`. `modules/` is now clean — 0 hardcoded `cluster.open-cluster-management.io`, 13 sites importing the constant. **Residual:** `lib/rbac_validator.py:118,149,154,193,194,238,261` still hardcode the literal, and the guardrail `tests/test_api_literal_guardrails.py:11,15-18` only walks `MODULES_DIR`, so the residual can grow silently. Guardrail blind spot tracked as `R3-T12` / slice `R3-09`. |
| R2-H3 | confirmed | PR 39 | Ansible RBAC validation task file duplicated ~140 lines between primary-hub and secondary-hub blocks (mirroring the Python `H1` pattern later closed by GitHub PR #148); closed by tracker `PR 39` / GitHub PR #149. |
| R2-M1 | confirmed, resolves subagent disagreement | PR 37 (part 1), PR 38 (part 2) | `acm_preflight_report.py` computes an accurate check-mode `changed` value then explicitly discards it (confirmed by reading `write_json_artifact`/`write_report` and comparing against sibling `acm_report_artifact.py`, which has no such override) — a real, self-contained bug. `acm_backup_schedule.py`/`acm_restore_info.py` force `changed=False` under native Ansible check mode; their owning roles do not surface this to the published role-level `changed` result unless the collection's own `mode: dry_run` variable is set (traced through `pause_backups.yml`/`activate_restore.yml`), which is misleading against the documented "native Ansible check mode is non-mutating even when `mode: execute`" contract in `ansible_collections/tomazb/acm_switchover/docs/variable-reference.md` — a real but architecturally distinct issue from the `acm_preflight_report.py` bug. |
| R2-M2 | confirmed | PR 42 | Crash between restore-staleness verification and activation completion, followed by resume, can skip re-validating restore staleness before completing activation. |
| R2-M3 | confirmed | PR 40 | `PR 40` delivered only restore-deletion waiter work: `lib/waiter.py` owns `wait_for_restore_deletion`, and the activation/finalization wrappers delegate to it. Review #1 `M2` is unrelated constants/message-table sprawl and receives no credit here. |
| R2-M4 | confirmed | PR 35 | `lib/utils.py` `REPORT_PHASE_NAMES` and `lib/workflow.py` `_CANONICAL_RESUME_START_PHASES` are byte-identical duplicate dicts. |
| R2-M5 | confirmed | PR 41 | Ansible summary-path resolution logic is duplicated across 4 role/playbook locations. |
| R2-L1..L9 | partially resolved; follow-ups open | PR 43 + issues #152-#157 | `PR 43` resolved R2-L3, R2-L4, R2-L5, the R2-L7 checkpoint-guard subitem, and R2-L9. R2-L1, R2-L6, R2-L7a-c, and R2-L8 remain separately tracked by issues #152-#157; R2-L2 remains explicitly excluded from this queue. |
| R2-H4 | confirmed | PR 44 | `tests/release/orchestrator.py` is 1199 lines on its first commit; `_run_release_certification` (335 lines) triplicates a short-circuit finalize pattern at 3 call sites. |
| R2-M6 | confirmed | PR 47 | `tests/release/adapters/ansible.py`, `bash.py`, `python_cli.py` duplicate ~70% of `execute()` logic despite an existing shared contract in `adapters/common.py`. |
| R2-M7 | confirmed | PR 45 | `tests/release/orchestrator.py` duplicates primary/secondary RBAC certification handling inline (~75 lines) instead of looping over a shared helper. |
| R2-M8 | confirmed | PR 46 | `tests/release/checks/rbac_certification.py`'s required-vs-forbidden permission evaluation loops are the same algorithm with polarity flipped, duplicated in full. |
| M1 | confirmed still open (Review #1; re-confirmed Review #2 and 2026-07-26) | none - deprioritized after PR 29-PR 31 | `lib/operation_runners.py:100-197` (`run_switchover_impl`) and `:200-286` (`run_restore_only_impl`) still share the completed-state/failed-state/validate-only/phase-flow/dry-run/completion skeleton, differing only in the `PhaseFlowEntry` tuples (`:139-165` vs `:238-252`) and message constants. Lower value after the orchestration seams were extracted; collapse into one descriptor-driven runner only if a future change must be made twice again. |
| M2 | confirmed still open; the `M2` credited to `PR 40` was the waiter unification, not this finding | superseded by `R3-Q2` (slice `R3-10`) | Review #1 `M2` is `lib/constants.py` UI string-table sprawl, not restore-wait dedup. Still present: `lib/constants.py` is 381 lines with 44 top-level `WORKFLOW_*`/`OPERATION_*`/`DRY_RUN_*`/`*_MESSAGE` constants (count verified 2026-07-26), and `lib/workflow.py:9` still opens a long `from lib.constants import (` block. `R3-Q2` is the sharper current framing. |
| M3 | confirmed still open (Review #1; re-confirmed Review #2 and 2026-07-26) | none - not covered by PR 40 | Two dry-run idioms still coexist: `@dry_run_skip` (`modules/finalization.py:415,724,918,941,1124,1151,1236,1514`) and inline `if self.dry_run:` (`modules/post_activation.py:465,693`; `modules/finalization.py:1046,1065`). `PR 40` moved only the restore-deletion wait into `lib/waiter.py` and touched no dry-run guard. Distinct from `R3-T1`/`R3-T2` (slice `R3-05`), which cover testing the guards rather than unifying the idiom. |
| M5 | confirmed still open, reprioritized upward by Review #2, re-confirmed 2026-07-26 | none - planned | `lib/argocd_coordinator.py:120-283`: `pause_hubs` is still a 164-line method whose tri-state result-handling block (`:237-282`, `result.patched` / `result.error` / `patch_applied is True/False/None`) was never extracted into `_reconcile_pause_result` (`git log -S` finds that name only in tracker docs). Correctness-critical durable-pause path; `M4`/`PR 35` had no overlap. |
| L2 | confirmed still open (Review #1; re-confirmed Review #2 and 2026-07-26) | none - opportunistic cleanup | `modules/post_activation.py:1178-1182` derives `bootstrap_namespace` from the import manifest's `bootstrap-hub-kubeconfig` Secret and `:1109` passes it to `_restart_klusterlet` (`:1281`) to patch the `klusterlet` Deployment; the ACM co-residence assumption is still undocumented in the code. |
| L3 | confirmed still open; silently dropped from Review #2's re-confirmation list without rationale | none - opportunistic cleanup | `deploy/helm/acm-switchover-rbac/templates/namespace.yaml:7-15` inlines its labels and is the only template not using `include "acm-switchover-rbac.labels"` (`templates/_helpers.tpl:35-44`), so the Namespace receives neither `.Values.commonLabels` nor any `managed-by` label. Second residue: `values.yaml:73` sets lowercase `app.kubernetes.io/managed-by: helm`, which will not match a conventional `managed-by=Helm` selector. |
| L4 | confirmed still open; call sites overlap `SSA-PY2` | resolve inside SSA-03 (planned) | `re.sub(r"https://([^:/]+).*", r"\1", url)` still duplicated 4x at `modules/post_activation.py:1483,1490,1608,1609` with silent passthrough on non-match; no `lib.utils.host_from_url` exists. `SSA-PY2` (hostname-key collapse at `:1490-1491`) targets these exact sites and `SSA-03` replaces hostname-only matching with normalized endpoint identities — extract the shared helper there rather than as a standalone cleanup. |
| L5 | confirmed still open; silently dropped from Review #2's re-confirmation list without rationale | none - opportunistic cleanup | `lib/operation_runners.py:78-80,101-105,201-204` still type `args`/`state`/`primary`/`secondary`/`logger` as `Any` despite `argparse.Namespace`, `StateManager`, and `KubeClient` being the real types, used precisely in `lib/workflow.py`. |
| L6 | confirmed still open (Review #1; re-confirmed Review #2 and 2026-07-26) | none - opportunistic cleanup | `modules/post_activation.py:1289` still does `import time as time_module` inside `_restart_klusterlet` while the module imports `time` at `:12`. |
| L7 | confirmed still open, wider than originally cited | none - opportunistic cleanup | `%`-style formatting inside `raise SwitchoverError(...)` at `modules/finalization.py:1299-1302,1367-1369,1375-1378,1381-1384,1447-1451` — 5 sites, up from the 2 cited on 2026-06-13 — while the rest of the file uses f-strings. `:1423` uses the same idiom in a `WaitConditionResult.pending` call. |
| SSA-A2 | confirmed, corrected P1 | SSA-01 (planned) | Collection contexts and live UIDs are validated independently but never compared; identical contexts or different contexts targeting one cluster can enter self-switchover. |
| SSA-P1 | confirmed with lower impact, corrected P2 | SSA-02 (planned) | Python standalone decommission intentionally lacks prior state binding; wrong-context risk remains, so add optional expected-UID verification without making switchover state mandatory. |
| SSA-P2 | confirmed, corrected P1 | SSA-01 (planned) | Python binds each role identity for resume but does not require primary and secondary live UIDs to differ before a new switchover. |
| SSA-S1 | confirmed, corrected P2 | SSA-05 (planned) | Deprecated `argocd-manage.sh` accepts legacy state and only warns on explicit context mismatch before patching the CLI-selected hub. |
| SSA-R1 | confirmed with lifecycle mitigation, corrected P2 | SSA-04 (planned) | Release Python decommission can build a live non-interactive command while the Ansible stream is forced dry-run; lifecycle gates reduce but do not remove focused-rerun risk. |
| SSA-R2 | confirmed with controller mitigation, corrected P2 | SSA-04 (planned) | Trusted profile arguments are appended verbatim and a trailing Ansible `-e` can override adapter dry-run defaults, although matrix/controller gates still apply. |
| SSA-C1 | confirmed with narrower scope, corrected P2 | SSA-06 (planned) | Dependency, secret, Semgrep, and Trivy lanes are advisory; Bandit and CodeQL were incorrectly included in the original claim and are already blocking. |
| SSA-A5 | confirmed documentation defect, corrected P3 | SSA-10 (planned) | The migration map claims `--force` maps to `acm_switchover_execution.force`, but collection runtime never reads it and the variable reference describes reserved compatibility. |
| SSA-PY2 | confirmed, corrected P2 | SSA-03 (planned) | Python klusterlet context selection collapses server URLs to hostname keys, so distinct endpoints sharing a host can overwrite each other and mis-target remediation. |
| SSA-PY3 | confirmed with local-filesystem precondition, corrected P3 | SSA-08 (planned) | Relative general state paths receive syntax-only validation and do not inherit the symlink-containment policy already used for report artifacts. |
| SSA-PY4 | confirmed, corrected P2 | SSA-02 (planned) | Python preflight checks decommission RBAC, but finalization invokes embedded `Decommission` without an immediate permission recheck before teardown. |
| SSA-S2 | confirmed as residual hardening, corrected P3 | SSA-07 (planned) | OpenShift-client downloads are checksum verified, but empty pinned-digest defaults permit same-origin checksum trust instead of independently pinned release digests. |
| SSA-C2 | confirmed with narrower scope, corrected P2 | SSA-06 (planned) | Trivy and TruffleHog use branch refs and Semgrep is installed unversioned; other floating major action tags also retain supply-chain movement. |
| SSA-C3 | confirmed, corrected P2 | SSA-07 (planned) | Blocking Bandit omits collection plugins, and CI/release dependency resolution has minimum floors without reviewed constraints or lock artifacts. |
| SSA-PY5 | confirmed with direct reusable-helper exposure, corrected P2 | SSA-09 (planned) | `KubeClient.patch_custom_resource()` logs status, reason, bounded raw API response body, and the rendered exception; full-list aggregation remains a separate lower-urgency subproblem within the same design gate. |
| SSA-A6 | confirmed with narrower scope, corrected P3 | SSA-03 (planned) | Collection worker configuration has no upper cap; defaults and API timeouts mitigate impact, and the original check-mode concern was not substantiated. |
| SSA-S3 | confirmed with lower composite impact, corrected P3 | SSA-05 (planned) | Deprecated Argo CD state may be created mode `0644`, and shell jsonpath context lookup can break on quoted context names; token stdout is documented and its wrapper already writes mode `0600`. |
| R3-A1 | merged, High | R3-01 / TR2D-01; issue #199; PR #200 | The correction assigns distinct scoped, cluster-wide, validation, and published variables and guards publication behind complete positive validation. Non-mock primary-prep retry and standalone resume prove the former no-op paths; exact validated head `0bc1a4b6701508f6c3d4cd898515d82b8a29b6a3` merged as `786f8325493c6086e136cb9694a9997557f12e02`, and issue #199 is closed as completed. |
| R3-A2 | confirmed empirically, Medium | R3-01b (planned) | Same clobber pattern makes the finalization dry-run preview always report `restore_count: 0`. |
| R3-A3 | confirmed empirically, Medium | R3-01b (planned) | Same clobber pattern defeats the file's own fixture-injection guard; currently benign. |
| R3-A4 | confirmed empirically, High | R3-02 (planned) | `failed_when: false` makes Thanos compactor drain verification fail open; the `until` loop exits on the first attempt and the follow-up gate is dead code. Python fails closed — parity divergence. |
| R3-A5 | confirmed empirically, High | R3-02 (planned) | Preflight hub connectivity is hard-coded `status: pass`; the `fail` branch is unreachable and the fabricated verdict reaches the go/no-go report. |
| R3-A6 | confirmed, Medium | R3-06 (planned) | `reset_from` disables checkpoint identity validation run-wide, not just for the pruned phase, and rewrites `operation_identity`. Persistent config key shipped in role defaults. |
| R3-A7 | confirmed, Medium | R3-08a (planned) | Probe returns `failed: true`, failing the task and suppressing the role diagnostics its own documentation promises. |
| R3-A8 | confirmed, Medium | R3-08b (planned) | `str \| None` in a module-level assignment is not deferred by `from __future__ import annotations`; import raises `TypeError` on Python 3.9 and the collection declares no Python floor. |
| R3-A9 | confirmed, Medium | R3-04b (planned) | `acm_input_validate` re-adds paths stripped from `hubs`; correction must sanitize both success and failure report records. Paths, not credentials. |
| R3-A10 | confirmed, Medium | R3-08c (planned) | Observatorium-api restart is not idempotent; Python guards the equivalent step with per-step state and the collection has only phase-level checkpointing, disabled by default. |
| R3-A11 | confirmed, Medium | R3-08d (planned) | Residual of `F17`/`R2-M1`: plan-only `changed=true` outside check mode, contrary to the `_info` convention and documented module behavior. |
| R3-P1 | confirmed, High | R3-03 (planned) | Regression from `F2`/`PR 03`: `wait()` batch deadline consumed as a per-cluster budget, so healthy queued clusters false-fail the run after activation. Worsens with fleet size. |
| R3-P2 | confirmed, Medium | R3-04a (planned) | Preflight reporter discards the validator message at every log level, though the detail is always persisted in state. Recovery must sanitize before verbose logging; raw exception/API/credential material remains prohibited. |
| R3-P3 | confirmed, Medium | R3-07 (planned) | Retry-phase signal appended to `errors[]`, but two consumers read only `errors[-1]`, so the report artifact and resume banner name the wrong phase. |
| R3-P4 | confirmed with scope nuance, Medium | R3-10a (planned) | Argo CD stale-status blocker is not ACM-scoped, unlike the adjacent ApplicationSet branch; one unrelated Application can hard-fail `PRIMARY_PREP`. Fail-closed direction is intentional; breadth is the finding. |
| R3-P5 | confirmed, Medium | R3-07 (planned) | State-refusal messages are now recorded via `add_error`, growing `errors[]` across reruns and pinning `get_last_error_phase()` to `FAILED`. |
| R3-P6 | confirmed, Low | R3-10b (planned) | Residual of `R2-H1`/`PR 36`: `delete_custom_resource` is the only mutating `KubeClient` method without a default request timeout. Former `R3-P6b` is folded supporting evidence that its docstring also misstates the behavior. |
| R3-P7 | confirmed, Low | R3-10c (planned) | `--dry-run --report-dir` writes an empty artifact because the state snapshot is restored before the report is written. |
| R3-P8 | confirmed with corrected count, Low | R3-10f (planned) | Two constants, not four, are unreferenced; six operational sites inline the corresponding Secret names. Route with the constants/quality design boundary. |
| R3-P9 | confirmed, Low | R3-10c (planned) | Report artifacts are `0644` and embed raw exception text. The existing validate → `mkdir` → revalidate sequence intentionally checks the created parent before the no-follow open and must remain unchanged. |
| R3-P10 | rejected/non-actionable | none | Pinned upstream RestorePhase definitions for supported 2.12-2.17 branches do not define `FailedWithErrors`; do not add handling for an invented phase. |
| R3-P11 | rejected/non-actionable | none | `tests/test_waiter.py::test_wait_fast_timeout_zero_disables_fast_interval` explicitly defines zero as disabling fast polling and using the standard interval. This executable current contract supersedes historical behavior. |
| R3-P12 | confirmed, Low | R3-10d (planned) | `(umask 077 && ...)` does not tighten an existing world-readable merged kubeconfig; `setup-rbac.sh` uses an explicit `chmod 600`. |
| R3-P13 | optional hardening | R3-10d (optional only) | Supported documentation invokes the script with sibling `constants.sh` present. No supported copy-one-script contract is shown; a guard may improve packaging/error messages but is not mandatory runtime work. |
| R3-T1 | confirmed, High | R3-05a (planned) | Four mutating dry-run guards have no dry-run test; deleting the `scale_statefulset` guard leaves all 3079 tests green while `--dry-run` scales the production Thanos compactor to 0. |
| R3-T2 | confirmed, Medium | R3-05a (planned) | Preferred fail-fast contract: exact `True` skips, exact `False` executes, and any non-boolean value fails in a controlled way before mutation. |
| R3-T3 | confirmed and already wrong, Medium | R3-05b (planned) | The Jinja parity test never loads `pause.yml`; its hand-written oracle also contradicts the task's `automated is not none` gate. |
| R3-T4 | confirmed, Medium | R3-05b (planned) | Namespace and filter parity tests assert only positive matches, so over-matching — the dangerous direction for pause selection — passes. |
| R3-T5 | confirmed, Medium | R3-09a / SSA-06 (planned) | Require a reviewed action-to-release mapping or equivalent version policy per workflow/action. Full commit-SHA formatting alone is insufficient. |
| R3-T6 | confirmed, Medium | R3-09b (planned) | `HYPOTHESIS_PROFILE` is set nowhere, so the `ci` profile never loads and CI explores half the intended state space; the scaffolding test cannot detect this. |
| R3-T7 | confirmed, Medium | R3-09b (planned) | Coverage is measured and uploaded with no `--cov-fail-under` and no Codecov threshold — the mechanism by which `R3-T1` stayed invisible. |
| R3-T8 | confirmed, Medium | R3-09c (planned) | `ACM_RELEASE_PROFILE` alone un-gates real-cluster certification; the opt-in pilot tests model the correct explicit pattern. |
| R3-T9 | confirmed, Medium | R3-09d (planned) | `create_mock_step_context` is duplicated in four suites. It must exist at most once; zero is valid if the suites use real `StateManager` instances. |
| R3-T10 | confirmed, Medium | R3-09e (planned) | Checkpoint reset test seeds three kinds of stale state and asserts none of them, despite the source naming that exact hazard; also never re-reads the persisted file. |
| R3-T12 | confirmed 2026-07-26, Medium | R3-09f (planned) | Guardrail scan-root blind spot leaves the seven-literal `R2-H2` residual in `lib/rbac_validator.py` unguarded and free to grow. |
| R3-T11 | confirmed after one withdrawal, Low | R3-10e (planned) | Surviving test-cleanup inventory excludes the import-time `sys.modules` stubbing, which is required by root-lane jobs without `ansible-core`. |
| R3-Q1 | confirmed maintainability, Medium | H3 track (#158) | Corrected inventory: 36 tracked Python files exceed 1000 lines and 21 crossed in this branch. This updates the existing `H3` design track, not a parallel effort. |
| R3-Q2 | confirmed maintainability, Low | R3-10f (planned) | 44 matching constants exist; 35 have exactly one external consumer. Evaluate them through the focused constants/quality design. |
| R3-Q3 | confirmed maintainability with corrected scope, Low | R3-10f (planned) | The original twelve-wrapper count was overstated; only the verified pass-through subset is in scope. |
| R3-Q4 | confirmed layering issue, Low | R3-10f (planned) | A `scripts/` entrypoint imports seven modules from `tests.release.lab_controller.*`, making the test tree a runtime dependency. |
| R3-X1 | confirmed, Low | R3-10g (planned) | `StateManager` run-lock file handle leaked; surfaced by the suite as a `ResourceWarning`, fix belongs in `lib/utils.py`. |
| TR2D-M1 / TR2D-L1 | merged | R3-01 / TR2D-01; issue #199; PR #200 | Folded with `R3-A1` into one boundary. The implementation requires complete positive all-namespace success, rejects malformed and mixed shapes, and preserves sanitized no-mutation advisory behavior; exact validated head `0bc1a4b6701508f6c3d4cd898515d82b8a29b6a3` merged as `786f8325493c6086e136cb9694a9997557f12e02`, and issue #199 is closed as completed. |
| TR2D-M2 | confirmed | TR2D-02 (planned) | Collection resume uses discovery-time Application data; align fresh re-read, marker ownership, current resource version, OCC refusal/conflict, and changed semantics with Python. |
| TR2D-Q1 | confirmed maintainability/review risk | TR2D-03 (planned/design input) | Phase 9B decomposition is a strong design input or preferred predecessor, not a mandatory Phase 9C prerequisite absent an authoritative design amendment. Phase 9C remains non-mutating. |
| TR2D-Q4 | confirmed maintainability | TR2D-04 (deferred/design-gated) | Deduplicate GitOps advisories only after preserving explicit hub and restore-only asymmetries. |
| TR2D-Q2 | inventory signal only | none | File size alone does not authorize refactoring. |
| TR2D-Q3 | confirmed low-value residual seam | unassigned | Do not add to issue #152 without explicit scope amendment and approval. |
| TR2D-Q6 | rejected/non-defect | none | Strict/advisory clients deliberately preserve different error/logging surfaces. |
| TR2D-L2 | unverified | none | Requires a reproducible path, expected report reference, and failing scenario. |

## PR Sequence

| PR | Status | Branch | Worktree | Findings | PR URL | Verification |
| --- | --- | --- | --- | --- | --- | --- |
| 01 | merged | `docs/thermos-resolution-tracking` | `.worktrees/thermos-01-tracking` | tracker + agent instructions | https://github.com/tomazb/rh-acm-switchover/pull/72 | `python -m pytest tests/test_documentation_guardrails.py -q` passed; `git diff --check` passed; CI passed |
| 02 | merged | `fix/thermos-restore-only-guard` | `.worktrees/thermos-02-restore-only` | F1 | https://github.com/tomazb/rh-acm-switchover/pull/73 | `python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/ -q` passed; `python -m pytest ansible_collections/tomazb/acm_switchover/tests/integration/test_switchover_roles.py -q` passed; `python -m pytest tests/release/adapters/test_ansible.py -q` passed; CI passed |
| 03 | merged | `fix/thermos-klusterlet-fail-closed` | `.worktrees/thermos-03-klusterlet` | F2, F3, F28 | https://github.com/tomazb/rh-acm-switchover/pull/74 | `python -m pytest tests/test_post_activation.py -q` passed; `python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_klusterlet_modules.py -q` passed; `python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/ -q` passed; CI passed |
| 04 | merged | `fix/thermos-hub-identity-resume` | `.worktrees/thermos-04-hub-identity` | F5, F6 | https://github.com/tomazb/rh-acm-switchover/pull/75 | CodeRabbit CLI pre-merge review `findings=0`; `python -m pytest tests/test_main.py -q` passed; `python -m pytest tests/test_utils.py -k hub_identities -q` passed; `git diff --check` passed; CI passed |
| 05 | merged | `fix/thermos-report-checkpoint-identity` | `.worktrees/thermos-05-identity-hygiene` | F9, F16 | https://github.com/tomazb/rh-acm-switchover/pull/76 | CodeRabbit CLI pre-merge review `findings=0`; focused checkpoint/report tests passed; collection unit tests passed; documentation guardrails passed; `git diff --check` passed; CI passed |
| 06 | merged | `fix/thermos-finalization-refresh-tests` | `.worktrees/thermos-06-finalization-refresh` | F7, F10, F27 | https://github.com/tomazb/rh-acm-switchover/pull/77 | CodeRabbit CLI pre-merge review `findings=0`; Gemini review thread addressed and resolved; `python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/test_discover_resources_contracts.py -q` passed; `python -m pytest ansible_collections/tomazb/acm_switchover/tests/integration/test_switchover_roles.py -q` passed; `python -m pytest ansible_collections/tomazb/acm_switchover/tests/scenario/test_checkpoint_resume.py -q` passed; `python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/ -q` passed; `python -m pytest tests/test_documentation_guardrails.py -q` passed; `ruff check` on touched Python tests passed; `black --check` on touched Python tests passed; `git diff --check` passed; CI passed |
| 07 | merged | `docs/thermos-rbac-operator-guidance` | `.worktrees/thermos-07-rbac-docs` | F4, F11, F24, F25 | https://github.com/tomazb/rh-acm-switchover/pull/78 | CodeRabbit CLI pre-merge review `findings=0`; Codex review thread addressed and resolved; `python -m pytest tests/test_documentation_guardrails.py -q` passed; `ruff check tests/test_documentation_guardrails.py` passed; `black --check --line-length 120 tests/test_documentation_guardrails.py` passed; stale design-spec/status grep returned no matches; `git diff --check` passed; CI passed |
| 08 | merged | `fix/thermos-rbac-managed-cluster-parity` | `.worktrees/thermos-08-rbac-parity` | F8, F26 | https://github.com/tomazb/rh-acm-switchover/pull/79 | CodeRabbit CLI pre-merge review `findings=0`; all review threads resolved; `python -m pytest tests/test_rbac_collection_parity.py tests/test_rbac_validator.py tests/test_check_rbac.py tests/test_documentation_guardrails.py -q` passed; `python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/ -q` passed; `ruff check` on touched Python tests passed; `black --check` on touched Python tests passed; `git diff --check` passed; CI passed |
| 09 | merged | `fix/thermos-ansible-surface-cleanup` | `.worktrees/thermos-09-ansible-cleanup` | F17, F18, F21, F23 | https://github.com/tomazb/rh-acm-switchover/pull/80 | CodeRabbit CLI pre-merge review `findings=0`; Gemini review threads addressed, resolved, and re-fetched clean; `python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/ -q` passed; `python -m pytest tests/test_rbac_integration.py tests/test_documentation_guardrails.py -q` passed; `ruff check` on touched Python files passed; `black --check --line-length 120` on touched Python files passed; `git diff --check` passed; CI passed |
| 10 | merged | `fix/thermos-preflight-complete-reporting` | `.worktrees/thermos-10-preflight-reporting` | F22 | https://github.com/tomazb/rh-acm-switchover/pull/81 | CodeRabbit CLI pre-merge review `findings=0`; all review threads addressed, resolved, and re-fetched clean; red/green RBAC plus backup preflight fixture passed; `python -m pytest ansible_collections/tomazb/acm_switchover/tests/integration/test_preflight_role.py -q` passed; `python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/test_preflight_checkpoint_validation_order.py -q` passed; `python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/ -q` passed; `python -m pytest tests/test_documentation_guardrails.py -q` passed; `ruff check` on touched Python files passed; touched-file `black --check --line-length 120` and `isort --check-only --profile black --line-length 120` passed; `git diff --check` passed; CI passed |
| 11 | merged | `refactor/thermos-shared-ansible-logic` | `.worktrees/thermos-11-shared-logic` | F19, F20 | https://github.com/tomazb/rh-acm-switchover/pull/82 | CodeRabbit CLI pre-PR review `findings=0`. CodeRabbit CLI pre-merge review rerun `findings=0`. CodeRabbit minor count-validation finding addressed. 3 Gemini review threads addressed, resolved, and re-fetched clean. Shared logic contract tests passed. `python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/ -q` passed. `python -m pytest ansible_collections/tomazb/acm_switchover/tests/integration/test_switchover_roles.py -q` passed. `python -m pytest ansible_collections/tomazb/acm_switchover/tests/scenario/test_checkpoint_resume.py -q` passed. `python -m pytest tests/test_documentation_guardrails.py -q` passed. `ruff check` on touched Python tests passed. Touched-file `black --check --line-length 120` and `isort --check-only --profile black --line-length 120` passed. `git diff --check` passed. CI passed |
| 12 | merged | `refactor/thermos-maintainability` | `.worktrees/thermos-12-maintainability` | F12, F13, F14, F15, residual F26 coverage | https://github.com/tomazb/rh-acm-switchover/pull/84 | `python -m pytest tests/test_main.py tests/test_main_phase_flow.py tests/test_main_argocd_resume.py tests/test_utils.py tests/test_post_activation.py -q` passed; `python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/test_rbac_bootstrap_contracts.py tests/test_rbac_collection_parity.py tests/test_rbac_validator.py -q` passed; `python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/test_preflight_parity.py ansible_collections/tomazb/acm_switchover/tests/unit/test_preflight_passive_restore_alignment.py ansible_collections/tomazb/acm_switchover/tests/unit/test_restore_only_recovery_contracts.py ansible_collections/tomazb/acm_switchover/tests/integration/test_preflight_role.py -q` passed; `python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/ -q` passed; CodeRabbit CLI review `findings=2` minor RBAC parity test hardening findings addressed; review-comment pass addressed PR #84 inline threads for workflow exits/constants, flow typing/import cleanup, klusterlet worker fallback, and managed-cluster key assertions; `python -m pytest tests/test_main.py tests/test_main_phase_flow.py tests/test_main_argocd_resume.py tests/test_post_activation.py -q` passed; final `./run_tests.sh` passed; merged in local history at `4fbc352` |
| 13 | merged | `docs/thermos-round6-tracking` | `.worktrees/thermos-13-round6-tracking` | Round 6 tracker + PR 12 status drift + F30 verification | https://github.com/tomazb/rh-acm-switchover/pull/85 | `python -m pytest tests/test_documentation_guardrails.py -q` passed; `git diff --check` passed; CI-scope `black --check --line-length 120 --diff acm_switchover.py lib modules ansible_collections/tomazb/acm_switchover/plugins ansible_collections/tomazb/acm_switchover/tests tests` passed during validation; Gemini and CodeRabbit review threads addressed and resolved; merged 2026-06-01 |
| 14 | merged | `fix/thermos-version-parsing-parity` | `.worktrees/thermos-14-version-parity` | F29 | https://github.com/tomazb/rh-acm-switchover/pull/86 | Red/green version parsing tests passed; review threads addressed and resolved; `python -m pytest tests/test_utils.py tests/test_primary_prep.py tests/test_backup_schedule.py ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_backup_schedule.py -q` passed; `python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/ -q` passed; `python -m pytest tests/test_documentation_guardrails.py -q` passed; touched-file `black --check --line-length 120` and `isort --check-only --profile black --line-length 120` passed; `git diff --check` passed; final `./run_tests.sh` passed; merged 2026-06-01 |
| 15 | merged | `fix/thermos-kubeconfig-token-hardening` | `.worktrees/thermos-15-token-hardening` | F32, F36 | https://github.com/tomazb/rh-acm-switchover/pull/87 | Red/green token default and kubeconfig write hardening tests passed; targeted Python/script, collection, and docs guardrail suites passed; `git diff --check` and `bash -n` passed; final `./run_tests.sh` passed; merged 2026-06-02. |
| 16 | merged | `fix/thermos-container-supply-chain` | `.worktrees/thermos-16-container-supply-chain` | F33 | https://github.com/tomazb/rh-acm-switchover/pull/88 | Red/green container supply-chain guardrails passed. Targeted pytest, touched-file `black --check`, and `git diff --check` passed. Podman build passed and runtime check reported `oc` 4.21.16, `jq` 1.7.1, and Python 3.12.13. Final `./run_tests.sh` passed. Merged 2026-06-02. |
| 17 | merged | `fix/thermos-klusterlet-secret-ordering` | `.worktrees/thermos-17-klusterlet-secret-ordering` | F34 | https://github.com/tomazb/rh-acm-switchover/pull/89 | Red/green klusterlet secret ordering tests passed; targeted Python, collection, RBAC parity/static, docs guardrail suites passed; `git diff --check` passed; final `./run_tests.sh` passed; merged 2026-06-02. |
| 18 | merged | `fix/thermos-safe-path-consolidation` | `.worktrees/thermos-18-safe-path-consolidation` | F31 | https://github.com/tomazb/rh-acm-switchover/pull/90 | Red/green safe-path consolidation tests passed; targeted Python/collection safe-path and report-artifact suites passed; collection unit suite passed; documentation guardrails passed; `git diff --check` passed; final `./run_tests.sh` passed; merged 2026-06-02. |
| 19 | merged | `fix/thermos-helm-validator-guardrail` | `.worktrees/thermos-19-helm-validator-guardrail` | F35 | https://github.com/tomazb/rh-acm-switchover/pull/91 | Red/green Helm validator guardrail tests passed; explicit positive and negative `helm template` checks passed; `python -m pytest tests/test_rbac_integration.py tests/test_documentation_guardrails.py -q` passed; `python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/test_collection_metadata.py -q` passed; touched-file Black/isort and `git diff --check` passed; final `./run_tests.sh` passed; CodeRabbit CLI review `findings=0`; Gemini review thread for malformed `verbs` values addressed; CodeRabbit review thread for non-mapping custom rule entries addressed; merged 2026-06-03. |
| 20 | merged | `thermos-20-argocd-resume-identity` | `.worktrees/thermos-20-argocd-resume-identity` | F37 | https://github.com/tomazb/rh-acm-switchover/pull/92 | Red/green checkpoint identity module and playbook contract tests passed; `python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/ -q` passed; `python -m pytest tests/test_post_activation.py tests/test_documentation_guardrails.py -q` passed; touched-file Black/isort and `git diff --check` passed; final `./run_tests.sh` passed; merged in local history at `ac041f6`. |
| 21 | merged | `thermos-21-python-klusterlet-fail-closed` | `.worktrees/thermos-21-python-klusterlet-fail-closed` | F38 | https://github.com/tomazb/rh-acm-switchover/pull/93 | Red/green Python klusterlet fail-closed tests passed; `python -m pytest tests/test_post_activation.py -q` passed; `python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_klusterlet_modules.py -q` passed; `python -m pytest tests/test_documentation_guardrails.py -q` passed; touched-file Black and `git diff --check` passed; final `./run_tests.sh` passed; CodeRabbit CLI review `findings=0`. PR #93 merged 2026-06-03. |
| 22 | merged | `fix/thermos-22-python-resume-fail-closed` | `.worktrees/thermos-22-python-resume` | F39 | https://github.com/tomazb/rh-acm-switchover/pull/97 | `python -m pytest tests/test_main_argocd_resume.py tests/test_main.py tests/test_main_phase_flow.py tests/test_utils.py tests/test_documentation_guardrails.py -q` passed; `git diff --check` passed; CodeRabbit CLI review `findings=0`. PR #97 merged 2026-06-04. |
| 23 | merged | `fix/thermos-23-argocd-dry-run-parity` | `.worktrees/thermos-23-argocd-dry-run` | F40 | https://github.com/tomazb/rh-acm-switchover/pull/98 | `python -m pytest tests/test_primary_prep.py tests/test_main.py tests/test_main_phase_flow.py tests/test_argocd_coordinator.py tests/test_documentation_guardrails.py -q` passed; `python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/test_argocd_manage_role_contracts.py -q` passed; `python -m pytest ansible_collections/tomazb/acm_switchover/tests/integration/test_argocd_manage_role.py -q` passed; `git diff --check` passed; `graphify update .` passed; CodeRabbit CLI review `findings=0`. PR #98 merged 2026-06-04. |
| 24 | merged | `perf/thermos-24-argocd-discovery-scope` | `.worktrees/thermos-24-argocd-scope` | F41 | https://github.com/tomazb/rh-acm-switchover/pull/99 | Restarted 2026-06-05 from a fresh worktree after the new spec/design gate landed. Baseline `python -m pytest tests/test_argocd.py tests/test_argocd_coordinator.py -q` passed before implementation. Added the PR24 design spec at `docs/superpowers/specs/2026-06-05-pr24-argocd-discovery-scope-design.md`. Verification: `python -m pytest tests/test_argocd.py tests/test_argocd_coordinator.py tests/test_primary_prep.py tests/test_main_argocd_resume.py -q` passed (`161 passed`); `python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/test_argocd_discovery_safety.py ansible_collections/tomazb/acm_switchover/tests/unit/test_argocd_manage_role_contracts.py ansible_collections/tomazb/acm_switchover/tests/unit/test_argocd_hub_parameterization.py ansible_collections/tomazb/acm_switchover/tests/unit/test_restore_only_recovery_contracts.py ansible_collections/tomazb/acm_switchover/tests/unit/test_argocd_resume_on_failure.py ansible_collections/tomazb/acm_switchover/tests/integration/test_argocd_manage_role.py -q` passed (`114 passed`); `git diff --check` passed; `graphify update .` passed; CodeRabbit CLI `coderabbit review --plain -t committed` reported `No findings`; `./run_tests.sh` still fails on pre-existing Black drift in four unrelated files (`ansible_collections/tomazb/acm_switchover/tests/unit/test_finalization_verification.py`, `tests/release/adapters/test_ansible.py`, `tests/release/checks/test_static_gates.py`, `tests/release/test_orchestrator.py`), reproduced on clean `ansible`. PR #99 merged 2026-06-05; PR 25 branch created from merged `ansible` base. |
| 25 | merged | `perf/thermos-25-rbac-preflight-scaling` | `.worktrees/thermos-25-rbac-scaling` | F42 | https://github.com/tomazb/rh-acm-switchover/pull/100 | `python -m pytest tests/test_rbac_validator.py tests/test_rbac_collection_parity.py tests/test_rbac_integration.py -q` passed (`117 passed, 6 skipped`); `python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_rbac_validate.py -q` passed (`32 passed`); `python -m pytest tests/release/checks/test_rbac_certification.py -q` passed (`27 passed`); `graphify update .` passed; `git diff --check` passed; `./run_tests.sh` reproduced the pre-existing Black drift recorded under PR 24 in `ansible_collections/tomazb/acm_switchover/tests/unit/test_finalization_verification.py`, `tests/release/adapters/test_ansible.py`, `tests/release/checks/test_static_gates.py`, and `tests/release/test_orchestrator.py`. PR #100 merged 2026-06-06. |
| 26 | merged | `test/thermos-26-runtime-parity-depth` | `.worktrees/thermos-26-parity-depth` | F43, F44 gate | https://github.com/tomazb/rh-acm-switchover/pull/101 | Added design spec `docs/superpowers/specs/2026-06-06-pr26-runtime-parity-depth-design.md` and implementation plan `docs/superpowers/plans/2026-06-06-pr26-runtime-parity-depth.md`. Runtime parity now compares Argo CD pause evidence, persisted resume-start metadata, richer RBAC/bootstrap artifact identity, and RBAC live-consistency outcomes. Verification: `python -m pytest tests/release/scenarios/test_runtime_parity.py tests/release/test_orchestrator.py tests/release/test_release_certification.py tests/test_main_phase_flow.py ansible_collections/tomazb/acm_switchover/tests/unit/plugins/action/test_checkpoint_phase_runtime.py -q` passed (`115 passed, 1 skipped` after the final malformed-Application regression); `graphify update .` passed; `git diff --check` passed; CodeRabbit rerun `coderabbit review --plain -t all --base ansible` reported `No findings`; repo-wide `black --check --line-length 120 --diff acm_switchover.py lib modules ansible_collections/tomazb/acm_switchover/plugins ansible_collections/tomazb/acm_switchover/tests tests` passed; final `./run_tests.sh` passed, and `pip-audit` now reports `No known vulnerabilities found` after raising the `aiohttp` floor to `3.14.0`. PR #101 merged 2026-06-07. |
| 27 | merged | `refactor/thermos-27-safety-file-decomposition` | `.worktrees/thermos-27-file-decomposition` | F44 orchestrator runtime/bootstrap extraction | https://github.com/tomazb/rh-acm-switchover/pull/102 | Added design spec `docs/superpowers/specs/2026-06-07-pr27-orchestrator-decomposition-design.md` and implementation plan `docs/superpowers/plans/2026-06-07-pr27-runtime-bootstrap-extraction.md`. Runtime/bootstrap helpers now live under `lib/runtime_bootstrap.py`, `main()` delegates state/bootstrap setup through `_prepare_runtime()`, the new state-file literals are centralized in `lib/constants.py`, and `requirements-dev.txt` now pins `ansible-core>=2.18.1` to avoid the vulnerable `2.15.13` line reported by `pip-audit`. Verification: `python -m pytest tests/test_runtime_bootstrap.py tests/test_main.py tests/test_main_phase_flow.py tests/test_main_argocd_resume.py tests/test_state_dir_env_var.py tests/test_dependency_security_constraints.py tests/release/scenarios/test_runtime_parity.py tests/test_documentation_guardrails.py -q` passed (`210 passed`); `pip-audit` passed with `No known vulnerabilities found`; `graphify update .` passed; `git diff --check` passed; CodeRabbit rerun `coderabbit review --plain -t all --base ansible` reported `No findings`. PR #102 merged 2026-06-07. |
| 28 | merged | `refactor/thermos-28-next-slice` | `.worktrees/thermos-28-next-slice` | F44 remaining slice map + tracker scope | https://github.com/tomazb/rh-acm-switchover/pull/103 | Added design spec `docs/superpowers/specs/2026-06-07-pr28-f44-remaining-slice-map-design.md`, plus the `PR29` design spec `docs/superpowers/specs/2026-06-07-pr29-operation-runner-design.md` and implementation plan `docs/superpowers/plans/2026-06-07-pr29-operation-runner-extraction.md`. This docs-only slice records the remaining `F44` backlog after `PR 27` and sequences the follow-up seams as `PR 29` operation/phase-flow runners, `PR 30` Argo CD resume safety, and `PR 31` CLI outcome/report orchestration. Verification: `python -m pytest tests/test_documentation_guardrails.py -q` passed; `git diff --check` passed; CodeRabbit uncommitted and committed review passes reported `No findings`. PR #103 merged 2026-06-07. |
| 29 | merged | `refactor/thermos-29-operation-runners` | `.worktrees/thermos-29-operation-runners` | F44 operation/phase-flow runner extraction | https://github.com/tomazb/rh-acm-switchover/pull/104 | Added design spec `docs/superpowers/specs/2026-06-07-pr29-operation-runner-design.md` and implementation plan `docs/superpowers/plans/2026-06-07-pr29-operation-runner-extraction.md`. `lib/operation_runners.py` now owns dispatch plus switchover/restore-only runner orchestration. `acm_switchover.py` keeps thin compatibility wrappers and `_run_restore_only_argocd_pause()` remains in place for PR30. Verification: `python -m pytest tests/test_operation_runners.py tests/test_main.py tests/test_main_phase_flow.py tests/test_documentation_guardrails.py -q` passed (`169 passed`); `graphify update .` passed; `git diff --check` passed; final `./run_tests.sh` passed after touched-file import sorting (`1435 passed, 6 skipped` root unit lane; `212 passed, 1 skipped` release lane; `black --check`, `isort --check-only`, `mypy`, `bandit`, `pip-audit`, and compile checks all passed). PR #104 merged 2026-06-07. |
| 30 | merged | `refactor/thermos-30-argocd-resume-safety` | `.worktrees/thermos-30-argocd-resume` | F44 Argo CD resume safety extraction | https://github.com/tomazb/rh-acm-switchover/pull/106 | Added design spec `docs/superpowers/specs/2026-06-08-pr30-argocd-resume-safety-design.md` and implementation plan `docs/superpowers/plans/2026-06-08-pr30-argocd-resume-safety.md`. `lib/argocd_resume.py` now owns `_prepare_argocd_resume_clients()`, `_run_argocd_resume_only()`, and `_attempt_argocd_resume_on_failure()`; `acm_switchover.py` keeps compatibility wrappers, and `_run_restore_only_argocd_pause()` remains in place and out of scope for this slice. Verification: `python -m pytest tests/test_argocd_resume_helpers.py tests/test_main_argocd_resume.py tests/test_main.py tests/test_main_phase_flow.py tests/test_operation_runners.py tests/test_documentation_guardrails.py -q` passed (`213 passed`); repo mypy scope passed; `graphify update .` passed; `git diff --check` passed; CodeRabbit re-review reported `No findings`; earlier branch validation also completed a full strict `./run_tests.sh` pass before the final constants/import cleanup, followed by fresh post-review focused checks. PR #106 merged 2026-06-08. |
| 31 | merged | `refactor/thermos-31-cli-report-orchestration` | `.worktrees/thermos-31-cli-reporting` | F44 CLI outcome/report orchestration extraction | https://github.com/tomazb/rh-acm-switchover/pull/107 | Added design spec `docs/superpowers/specs/2026-06-08-pr31-cli-outcome-report-design.md` and implementation plan `docs/superpowers/plans/2026-06-08-pr31-cli-outcome-report-orchestration.md`. `lib/cli_outcomes.py` now owns report target selection, phase summarization, Python report writing, setup-mode outcome handling, and the non-setup completion shell; `acm_switchover.py` keeps thin compatibility wrappers plus entrypoint ordering and delegates setup/runtime outcome handling through the extracted helper module. Verification: `python -m pytest tests/test_cli_outcomes.py tests/test_main.py tests/test_report_artifacts.py tests/test_main_phase_flow.py -q` passed (`167 passed`); `python -m pytest tests/test_documentation_guardrails.py -q` passed (`24 passed`); `graphify update .` passed; `git diff --check` passed; final `./run_tests.sh` passed after formatting the new direct test file with Black during verification. PR #107 merged 2026-06-08 at `fb90d62`. |
| 32 | merged | `docs/thermos-32-review-validation` | `.worktrees/thermos-32-review-validation` | Thermos Review #1 validation + B1 cleanup | https://github.com/tomazb/rh-acm-switchover/pull/120 | Added the validated 2026-06-13 Thermos review findings document and resolved `B1` by removing the dead `Finalization.disable_observability_on_secondary` instance field while preserving constructor/CLI compatibility and old-hub observability cleanup behavior. `python -m pytest tests/test_finalization.py ansible_collections/tomazb/acm_switchover/tests/unit/test_finalization_old_hub_parity.py tests/test_documentation_guardrails.py -q` passed (`120 passed`); `black --check --line-length 120 modules/finalization.py tests/test_finalization.py` passed; `git diff --check` passed; `coderabbit review --plain -t all --base ansible` reported `No findings` on rerun; final `./run_tests.sh` passed (root lane `1516 passed, 105 deselected`; release lane `212 passed, 1 skipped`; Black/MyPy/Bandit/pip-audit all clean). PR #120 merged 2026-06-15 at `ddbe46e`. |
| 33 | merged | `codex/thermos-maintainability-followups` | (no dedicated worktree recorded) | Argo CD resume/CLI-entrypoint/logging maintainability cleanup (not H1/H2/M4) | https://github.com/tomazb/rh-acm-switchover/pull/109 | Addressed a maintainability backlog adjacent to, but distinct from, the `H1`/`H2`/`M4` items queued after `PR 32`: decomposed the Argo CD resume client preparation hotspot into focused, independently testable helpers with unit coverage for resume identity checks; removed dead compatibility wrappers and stale complexity suppression from the CLI entrypoint; routed shared report filenames/types and Argo CD pause/hub role strings through constants; hardened the Option-B resume contract test; extracted duplicated operation-completion logging into `lib/workflow.py`; documented the dry-run rollback boundary. Verification: targeted `pytest` across `test_argocd_resume_helpers.py`, `test_operation_runners.py`, `test_cli_outcomes.py`, `test_primary_prep.py`, collection restore-only contracts, release adapters, and targeted `test_main.py` classes passed; `python -m compileall`, `black --check --line-length 120`, `isort --check-only --profile black --line-length 120`, `git diff --check`, and `./run_tests.sh` all passed. PR #109 merged 2026-06-08 at `7010dc2`. This PR only updated the `PR 31` row in the tracker at merge time and had no row of its own until this reconciliation pass. **Correction (2026-07-02, Thermos Review #2):** the original reconciliation-pass label for this row overstated scope by tagging it `H1, H2, M4 partial`; `lib/rbac_validator.py`'s primary/secondary duplication (`H1`), the modules-wide API-group/version/plural literal duplication (`H2`), and the `finalization.py:570` state accessor gap (`M4`) were independently re-verified as still fully unresolved by Review #2 (see `R2-H2`/`H1`/`H2`/`M4` in [`docs/plans/2026-07-02-thermos-ansible-review-2-findings.md`](docs/plans/2026-07-02-thermos-ansible-review-2-findings.md)); this PR's actual changes never touched those files. |
| 34 | merged | `fix/thermos-34-managed-cluster-constant` | `.claude/worktrees/thermos-34-managed-cluster-constant` | R2-H2 (sharper H2) | https://github.com/tomazb/rh-acm-switchover/pull/127 | Design spec `docs/superpowers/specs/2026-07-02-pr34-managed-cluster-constant-design.md`; implementation plan `docs/superpowers/plans/2026-07-02-pr34-managed-cluster-constant.md`. Routed all 56 hardcoded `cluster.open-cluster-management.io` literals in `modules/**/*.py` (the review's 49 top-level sites plus 7 more found in `modules/preflight/` during the red-test run) and the 2 behavioral `lib/kube_client.py` helpers through `MANAGED_CLUSTER_*` and new `CLUSTER_BACKUP_*` constants; `ACM_BACKUP_SCHEDULE_TYPE_LABEL` now derives from the shared group constant. Added red-first static guardrail `tests/test_api_literal_guardrails.py` (red at 56 violations, now green) and the `MANAGED_CLUSTER_API_GROUP` ↔ `CLUSTER_OPEN_CLUSTER_MANAGEMENT_IO` pair in `tests/test_constants_parity.py`. Verification: targeted `python -m pytest tests/test_api_literal_guardrails.py tests/test_constants_parity.py tests/test_activation.py tests/test_finalization.py tests/test_post_activation.py tests/test_primary_prep.py tests/test_backup_schedule.py tests/test_decommission.py tests/test_kube_client.py -q` passed (`456 passed`); preflight suites passed (`147 passed`); touched-file `black`/`isort` (line-length 120) applied; `git diff --check` passed; full `./run_tests.sh` passed (root lane `1557 passed, 105 deselected`; release lane `1021 passed, 3 skipped`; Black/isort/MyPy/Bandit/pip-audit all clean). PR #127 merged into `ansible` 2026-07-03. |
| 35 | merged | `refactor/thermos-35-phase-name-dedup` | `.claude/worktrees/thermos-35-phase-name-dedup` | R2-M4 | https://github.com/tomazb/rh-acm-switchover/pull/128 | Design spec `docs/superpowers/specs/2026-07-02-pr35-phase-name-dedup-design.md`; implementation plan `docs/superpowers/plans/2026-07-02-pr35-phase-name-dedup.md`. Collapsed the byte-identical `lib/utils.py` `REPORT_PHASE_NAMES` and `lib/workflow.py` `_CANONICAL_RESUME_START_PHASES` dicts into one `CANONICAL_PHASE_NAMES` mapping in `lib/utils.py` (next to the `Phase` enum), consumed by `StateManager.mark_step_completed`, `lib/workflow.py` resume-start summaries, and `lib/cli_outcomes.py`; no compatibility alias (zero external references, verified by grep). Red-first guardrails in `tests/test_phase_name_canonical.py` (exact executable-phase key set, SECONDARY_VERIFY fold, `is`-identity across consumers, old names gone). Verification: `python -m pytest tests/test_phase_name_canonical.py tests/test_utils.py tests/test_main_phase_flow.py tests/test_cli_outcomes.py tests/test_report_artifacts.py tests/test_main.py -q` passed (`298 passed`); touched-file `black`/`isort` (line-length 120) applied; `git diff --check` passed; full `./run_tests.sh` passed (root lane `1560 passed, 105 deselected`; release lane `1021 passed, 3 skipped`). PR #128 merged into `ansible` 2026-07-03 (includes read-only MappingProxyType hardening). |
| 36 | merged | `fix/thermos-36-delete-timeouts` | `.claude/worktrees/thermos-36-delete-timeouts` | R2-H1 | https://github.com/tomazb/rh-acm-switchover/pull/129 | Design spec `docs/superpowers/specs/2026-07-03-pr36-delete-timeouts-design.md`; implementation plan `docs/superpowers/plans/2026-07-03-pr36-delete-timeouts.md`. `delete_configmap`/`delete_pod` now pass the client-default request timeout via `_request_timeout_kwargs()` like every sibling core_v1 call, and `primary_prep.py`'s ACM ≤2.11 BackupSchedule delete passes `timeout_seconds=DELETE_REQUEST_TIMEOUT`, matching the five equivalent delete sites in activation/decommission; timeout expiry flows through the existing tenacity retry/error path. Red-first: tightened `assert_called_once_with` in `tests/test_kube_client.py` (both deletes) and `tests/test_primary_prep.py` (ACM 2.11 delete) to require the timeout kwargs (3 failed before the fix). Verification: `python -m pytest tests/test_kube_client.py tests/test_primary_prep.py -q` passed (`135 passed`); touched-file `black`/`isort` applied; `git diff --check` passed; full `./run_tests.sh` passed (root lane `1556 passed, 105 deselected`; release lane `1021 passed, 3 skipped`). Note: trivial adjacent-line overlap with PR 34 at the `primary_prep.py` call site (PR 34 rewrites the group/version/plural lines) — whichever merges second resolves a one-hunk conflict. |
| 37 | merged | `fix/thermos-37-preflight-report-checkmode` | `.claude/worktrees/thermos-37-preflight-report-checkmode` | R2-M1 (part 1) | https://github.com/tomazb/rh-acm-switchover/pull/130 | Design spec `docs/superpowers/specs/2026-07-03-pr37-preflight-report-checkmode-design.md`; implementation plan `docs/superpowers/plans/2026-07-03-pr37-preflight-report-checkmode.md`. Deleted `acm_preflight_report.py`'s unconditional `if module.check_mode: changed = False` override so the module reports `write_json_artifact`'s accurate diff-based `changed`, matching sibling `acm_report_artifact.py`; the no-write guarantee stays in `write_json_artifact`'s check-mode gate, and the preflight role's published `acm_switchover_preflight_result.changed` now carries the truthful value. Red-first: flipped the check-mode create test to assert `changed=True` (no write) and added a mode-independence test; also fixed the matching-artifact fixture, which lacked the `cluster_uid` field `sanitize_report_hubs` adds and only passed because the override masked the real diff. Verification: module suite `10 passed`; collection unit suite `807 passed`; full `./run_tests.sh` passed (root lane `1563 passed, 105 deselected`; release lane `1021 passed, 3 skipped`); touched-file `black`/`isort` applied; `git diff --check` passed. PR #130 merged into `ansible` 2026-07-03. |
| 38 | merged | `fix/thermos-38-checkmode-changed-surfacing` | `.claude/worktrees/thermos-38-checkmode-changed-surfacing` | R2-M1 (part 2) | https://github.com/tomazb/rh-acm-switchover/pull/131 | Design spec `docs/superpowers/specs/2026-07-03-pr38-checkmode-changed-surfacing-design.md` records the required decision: **(a) wiring chosen**, plus a one-sentence doc clarification. `acm_backup_schedule.py`/`acm_restore_info.py` plan modules now report mode-independent `changed` (they mutate nothing; the check-mode zeroing was the same discard-the-right-answer pattern PR 37 removed), and the `pause_backups.yml`/`activate_restore.yml` published-changed fallbacks treat `ansible_check_mode` like `mode: dry_run`, so `--check` with `mode: execute` publishes truthful would-change verdicts. `variable-reference.md` mode row documents the clarified contract. Red-first: flipped the two plugin tests pinning suppressed behavior + 2 new role-contract text asserts (4 red before the fix). Verification: collection unit suite `808 passed`; full `./run_tests.sh` passed (root lane `1563 passed, 105 deselected`; release lane `1021 passed, 3 skipped`); touched-file `black`/`isort` applied; `git diff --check` passed. Implementation plan `docs/superpowers/plans/2026-07-03-pr38-checkmode-changed-surfacing.md`. PR #131 merged into `ansible` 2026-07-03. |
| H1 | merged | `refactor/thermos-h1-python-rbac-unification` | `.claude/worktrees/thermos-h1-python-rbac-unification` | H1 (Python RBAC validator unification) | https://github.com/tomazb/rh-acm-switchover/pull/148 | Design spec `docs/superpowers/specs/2026-07-05-python-h1-rbac-unification-design.md`; implementation plan `docs/superpowers/plans/2026-07-05-python-h1-rbac-unification.md`. `VALIDATOR_CLUSTER_PERMISSIONS` is now derived from `OPERATOR_CLUSTER_PERMISSIONS` via `_derive_read_only_permissions()` stripping `MUTATING_VERBS`, with the managedclusters `patch` exception recorded as explicit data (`VALIDATOR_CLUSTER_VERB_EXCEPTIONS`) and verified at import time (drift raises `ValueError`). `validate_rbac_permissions()` primary/secondary duplication collapsed into a hub-parameterized `_validate_hub()` helper plus a loop over an explicit per-hub table; asymmetries preserved as data (primary-only decommission/old-hub-finalization pass-through, secondary-only install-type override and error-count failure message, primary-only "not available" skip log). Behavior preserved byte-for-byte (message strings pinned by tests); hub namespace tables intentionally stay literal (not pure verb-strips). Red-first tests: derivation/exception/drift-guard class plus hub-loop asymmetry/message tests (8 failed before implementation across the two facets). Verification: `git diff --check` passed; `python -m pytest tests/test_rbac_validator.py tests/test_check_rbac.py tests/test_rbac_collection_parity.py tests/test_rbac_integration.py tests/release/checks/test_rbac_certification.py -q` passed; full-parity `python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/ tests/ -q` passed; `./run_tests.sh` passed. This PR establishes the hub-role-loop structural pattern that `PR 39` (R2-H3) must mirror on the Ansible side; no Ansible RBAC validation logic touched. Review-comment resolution pass (2026-07-05): Copilot inline comment (drift-guard `ValueError` printed only dict keys via `sorted(<dict>)`, hiding stripped verbs) fixed by formatting the full key→verbs mapping via `_format_verb_removals()` with red-first message assertions in both drift tests; CodeRabbit nitpick (secondary hub used `not client` vs primary `client is None`) fixed by unifying on `client is None`. Verification after fixes: targeted RBAC/parity suites passed (183), full-parity pytest passed (3515 passed, 29 skipped), `git diff --check` clean, `./run_tests.sh` passed. PR #148 merged into `ansible` 2026-07-05 at merge commit `0afeea52`; the `PR 39` branch was created from that updated base (status reconciled to merged per the tracker rules). |
| 39 | merged | `refactor/thermos-39-ansible-rbac-dedup` | `.claude/worktrees/thermos-39-ansible-rbac-dedup` (planned `.worktrees/` path unavailable in the implementing session; same isolation contract) | R2-H3 | https://github.com/tomazb/rh-acm-switchover/pull/149 | Design spec `docs/superpowers/specs/2026-07-05-pr39-ansible-rbac-dedup-design.md`; implementation plan `docs/superpowers/plans/2026-07-05-pr39-ansible-rbac-dedup.md`. Branch created from updated `origin/ansible` merge commit `0afeea52` (Python `H1` merged, PR #148). The ~140-line duplicated primary/secondary blocks in `roles/preflight/tasks/validate_rbac.yml` now run through a `_rbac_hub_validations` data table plus one `include_tasks` loop over the new shared `validate_rbac_hub.yml`, mirroring Python H1's `hub_validations` table + `_validate_hub()` loop; asymmetries are table data (primary-only restore-only skip via `enabled`, decommission expression, old-hub finalization; secondary `false` literals), `_rbac_skip_observability` stays computed once, the managed-cluster section stays separate, and every per-hub fact name (`_rbac_argocd_app_crd_*`, `_rbac_argocd_instance_crd_*`, `_rbac_argocd_install_type_*`, `_rbac_expanded_*`, `_rbac_denied_permissions_*`, `acm_<hub>_rbac_validation`) is re-published via templated `set_fact` keys; 401 fail-closed and non-403 unexpected-error CRD discovery tasks preserved with hub-templated, render-identical messages. Red-first: 8 contract tests (5 new/updated parity, 3 rewritten resilience) failed before the refactor; `test_rbac_bootstrap_contracts.py` old-hub-finalization contract updated to follow the new chain (deviation from plan's test list, recorded here). No Python changes; `lib/rbac_validator.py` untouched. Verification: `git diff --check` clean; targeted `test_preflight_parity.py` + `test_ansible_resilience_contracts.py` passed (54); collection unit suite passed (822); fixture-driven `ansible-playbook` integration `test_preflight_role.py` + `test_restore_only_role.py` passed (8, including `test_preflight_rbac_failure_still_reports_backup_findings` exercising both hub-loop iterations end-to-end); root RBAC/parity suites (`test_rbac_validator.py`, `test_check_rbac.py`, `test_rbac_collection_parity.py`, `test_rbac_integration.py`, `tests/release/checks/test_rbac_certification.py`) passed (183); full `./run_tests.sh` passed (exit 0; compile/unit/release-framework/quality/security lanes all green). PR #149 merged into `ansible` on 2026-07-08 at merge commit `79b1d92f516bfb45a5c18ff54d554044a6e80f15`; PR43 was created from that updated `origin/ansible` base. |
| 40 | merged | `refactor/thermos-40-restore-wait-dedup` | `.claude/worktrees/thermos-40-restore-wait-dedup` | R2-M3 (the "existing M2" credit was a mis-attribution corrected 2026-07-26: Review #1 `M2` is `lib/constants.py` string-table sprawl, unrelated to restore-wait dedup, and remains open) | https://github.com/tomazb/rh-acm-switchover/pull/145 | Design spec `docs/superpowers/specs/2026-07-03-pr40-restore-wait-dedup-design.md`; implementation plan `docs/superpowers/plans/2026-07-03-pr40-restore-wait-dedup.md`. `lib/waiter.py` now owns `wait_for_restore_deletion(client, restore_name, *, dry_run, timeout, where, logger)`; `activation._wait_for_restore_deletion` and `finalization._wait_for_primary_restore_deletion` are one-line delegates preserving their patch seams, historical dry-run flag sources, and the `" on primary"` message suffix byte-for-byte. Red-first: 4 waiter unit tests (absent, poll-until-absent, timeout FatalError with suffix, dry-run skip). Two existing tests that pinned the wait through `modules.<mod>.wait_for_condition` now patch `lib.waiter.wait_for_condition` for the moved poll (recorded in the spec seam note). Verification: `python -m pytest tests/test_waiter.py tests/test_activation.py tests/test_finalization.py -q` passed (15+59+87); full `./run_tests.sh` passed (root lane `1567 passed, 105 deselected`; release lane `1029 passed, 3 skipped`); touched-file `black`/`isort` applied; `git diff --check` passed. |
| 41 | merged | `refactor/thermos-41-summary-path-dedup` | `.claude/worktrees/thermos-41-summary-path-dedup` | R2-M5 | https://github.com/tomazb/rh-acm-switchover/pull/146 | Design spec `docs/superpowers/specs/2026-07-04-pr41-summary-path-dedup-design.md`; implementation plan `docs/superpowers/plans/2026-07-04-pr41-summary-path-dedup.md`. Added the collection's first filter plugin (`plugins/filter/paths.py`, `tomazb.acm_switchover.acm_abs_path(path, base_dir)`) and converted the four `Resolve summary path to absolute` sites (discovery/decommission/rbac_bootstrap roles, `argocd_manage_test.yml`) to it; PWD lookup, task names, and `when:` conditions untouched; resolution byte-identical including the no-normalization concatenation semantics (pinned by unit test). Red-first: 5 filter unit tests + a 4-site contract test banning the inline expression. Verification: collection unit suite `818 passed`; full `./run_tests.sh` passed (root lane `1563 passed, 105 deselected`; release lane `1034 passed, 3 skipped`); touched-file `black`/`isort` applied; `git diff --check` passed. |
| 42 | merged | `fix/thermos-42-activation-resume-staleness` | `.claude/worktrees/thermos-42-activation-resume-staleness` | R2-M2 | https://github.com/tomazb/rh-acm-switchover/pull/147 | Design spec `docs/superpowers/specs/2026-07-04-pr42-activation-resume-staleness-design.md` (records the approach decision: call-scoped re-validation chosen over step-semantics changes or verification timestamps); implementation plan `docs/superpowers/plans/2026-07-04-pr42-activation-resume-staleness.md`. Extracted `_assert_passive_restore_ready(restore, restore_name)` from `_verify_passive_sync` (log/error strings verbatim) and asserted it on entry of both activation paths: after the already-applied early return on the patch path (patched restores legitimately transition phases) and before snapshot/delete on the Option-B path (never destroy a passive restore that is not activation-ready). Crash-resume after the verify checkpoint now fails closed with `Passive sync restore not ready: ...` instead of activating against a degraded restore; read-only before any mutation; no checkpoint-semantics changes; collection parity gap ruled out in the spec (the activation role re-plans from a fresh `acm_restore_info` run on every invocation). Red-first: 3 new tests (patch path fail-closed, already-applied exemption, Option-B fail-closed with delete/create never called); 4 existing Option-B fixtures gained the ready phase the guard now demands. Verification: `python -m pytest tests/test_activation.py -q` passed (`62 passed`); full `./run_tests.sh` passed (root lane `1572 passed, 105 deselected`; release lane `1034 passed, 3 skipped`); touched-file `black`/`isort` applied; `git diff --check` passed. PR #147 merged into `ansible` 2026-07-05. |
| 43 | merged | `chore/thermos-43-low-severity-cleanup` | `.claude/worktrees/thermos-43-low-severity-cleanup` | R2-L3, R2-L4, R2-L5, R2-L7 checkpoint-guard subitem, R2-L9; deferred/split: R2-L1 ([#152](https://github.com/tomazb/rh-acm-switchover/issues/152)), R2-L6 ([#153](https://github.com/tomazb/rh-acm-switchover/issues/153)), R2-L7a ([#154](https://github.com/tomazb/rh-acm-switchover/issues/154)), R2-L7b ([#155](https://github.com/tomazb/rh-acm-switchover/issues/155)), R2-L7c ([#156](https://github.com/tomazb/rh-acm-switchover/issues/156)), R2-L8 ([#157](https://github.com/tomazb/rh-acm-switchover/issues/157)); excluded: R2-L2 | https://github.com/tomazb/rh-acm-switchover/pull/151 | Merged 2026-07-10 via GitHub PR [#151](https://github.com/tomazb/rh-acm-switchover/pull/151), final head `917f8d7ca82b143b20e15a5779402b01fbbce432`, merge commit `3985b42cc91dd87d420c550baeade3a3cb774868`; branch was based on PR #149 merge commit `79b1d92f516bfb45a5c18ff54d554044a6e80f15`. Design spec `docs/superpowers/specs/2026-07-08-pr43-low-severity-cleanup-design.md`; implementation plan `docs/superpowers/plans/2026-07-08-pr43-low-severity-cleanup.md`. Included only local behavior-preserving cleanup: bounded waiter/decommission log detail, post-parse CLI required-argument validation, documented/tested klusterlet structured failure contract, Argo CD resume checkpoint guard dedup, and release `StreamResult.to_dict()` cleanup. Validation polish restored bare Jinja truthiness for Argo CD resume `checkpoint.enabled`, added conditionally-required CLI help wording without parser behavior changes, strengthened the Argo CD resume guard test to require the exact checkpoint task-name set, and left V4 as non-actionable cosmetic. No protected files, RBAC permissions/manifests/Helm, live/lab release certification behavior, R2-L2, deferred/split low-severity items, Python H3 decomposition, report schema, or fail-closed/check-mode/idempotence changes. Verification: `python -m pytest tests/test_waiter.py tests/test_decommission.py -q` passed (`57 passed`); `python -m pytest tests/test_main.py tests/test_validation.py -q` passed (`199 passed`); `python -m pytest tests/test_post_activation.py ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_klusterlet_modules.py ansible_collections/tomazb/acm_switchover/tests/unit/test_klusterlet_remediation.py ansible_collections/tomazb/acm_switchover/tests/unit/test_post_activation_regressions.py -q` passed (`192 passed`); `python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/test_argocd_hub_parameterization.py ansible_collections/tomazb/acm_switchover/tests/unit/test_ansible_resilience_contracts.py ansible_collections/tomazb/acm_switchover/tests/unit/test_argocd_resume_on_failure.py -q` passed (`86 passed`); `python -m pytest tests/release/test_orchestrator.py tests/release/test_release_certification.py -q` passed (`32 passed, 1 skipped`); `python -m pytest tests/test_documentation_guardrails.py -q` passed (`60 passed`); `python -m pytest tests/test_waiter.py tests/test_kube_client.py -q` passed (`114 passed`); `git diff --check` passed; `./run_tests.sh` passed (`1593 passed, 105 deselected`; release lane `1035 passed, 3 skipped`; Black/isort/MyPy/Bandit/pip-audit/compile clean). CodeRabbit `coderabbit review --agent -t uncommitted --base origin/ansible` reported 0 findings. |
| 44 | merged | `refactor/thermos-44-release-orchestrator-shortcircuit` | `.claude/worktrees/thermos-44-release-orchestrator-shortcircuit` | R2-H4 | https://github.com/tomazb/rh-acm-switchover/pull/132 | Design spec `docs/superpowers/specs/2026-07-03-pr44-orchestrator-shortcircuit-design.md`; implementation plan `docs/superpowers/plans/2026-07-03-pr44-orchestrator-shortcircuit.md`. Extracted module-level `_short_circuit_finalize(...)` owning the `not_applicable` runtime-parity/final-baseline artifact pair and the `_finalize_run` delegation; the three abort paths in `_run_release_certification` (matrix-validation blocked, required static-gates failure, stop-before-mutation) collapse to single calls with only their `mandatory_argocd` expression varying. Behavior-preserving; guarded by the existing short-circuit characterization tests plus a new red-first direct helper unit test. Verification: `python -m pytest tests/release/test_orchestrator.py tests/release/test_release_certification.py -q` passed (`30 passed, 1 skipped`); full `./run_tests.sh` passed (root lane `1563 passed, 105 deselected`; release lane `1022 passed, 3 skipped`); touched-file `black`/`isort` applied; `git diff --check` passed. PR #132 merged into `ansible` 2026-07-03. |
| 45 | merged | `refactor/thermos-45-release-orchestrator-rbac-dedup` | `.claude/worktrees/thermos-45-release-orchestrator-rbac-dedup` | R2-M7 | https://github.com/tomazb/rh-acm-switchover/pull/133 | Design spec `docs/superpowers/specs/2026-07-03-pr45-orchestrator-rbac-dedup-design.md`; implementation plan `docs/superpowers/plans/2026-07-03-pr45-orchestrator-rbac-dedup.md`. Extracted `_certify_hub_rbac(...)` (scope lookup -> `certify_rbac_permissions` -> `hub:name`-prefixed assertion dicts) and replaced the duplicated primary/secondary blocks in `_run_release_certification` with a loop over `("primary", "secondary")` plus equivalent `all`/`any` status aggregation. Behavior-preserving; guarded by existing live-RBAC characterization tests plus a new red-first direct helper unit test. Verification: `python -m pytest tests/release/test_orchestrator.py tests/release/test_release_certification.py -q` passed; full `./run_tests.sh` passed (root lane `1563 passed, 105 deselected`; release lane `1022 passed, 3 skipped`; Flake8/Black/isort/MyPy/Bandit/pip-audit clean); `git diff --check` passed. Rebased onto PR #132's short-circuit helper after resolving the adjacent release orchestrator conflict. |
| 46 | merged | `refactor/thermos-46-rbac-certification-dedup` | `.claude/worktrees/thermos-46-rbac-certification-dedup` | R2-M8 | https://github.com/tomazb/rh-acm-switchover/pull/134 | Design spec `docs/superpowers/specs/2026-07-03-pr46-rbac-certification-dedup-design.md`; implementation plan `docs/superpowers/plans/2026-07-03-pr46-rbac-certification-dedup.md`. Extracted `_evaluate_permissions(..., expect_allowed: bool)` returning `(assertions, unexpected_count, error_count)`; `certify_rbac_permissions` now calls it once for required permissions and once for forbidden permissions, with expected/actual/message strings derived from the polarity so emitted `CertificationAssertion`s are byte-identical to before. Red-first 6-case polarity-matrix unit test (allowed/denied/error × both polarities); guarded by the existing certification suite. Verification: `python -m pytest tests/release/checks/test_rbac_certification.py tests/release/test_orchestrator.py -q` passed (`62 passed`); full `./run_tests.sh` passed (root lane `1563 passed, 105 deselected`; release lane `1027 passed, 3 skipped`; Flake8/Black/isort/MyPy/Bandit/pip-audit clean); `git diff --check` passed. |
| 47 | merged | `refactor/thermos-47-release-adapter-dedup` | `.claude/worktrees/thermos-47-release-adapter-dedup` | R2-M6 | https://github.com/tomazb/rh-acm-switchover/pull/135 | Design spec `docs/superpowers/specs/2026-07-03-pr47-release-adapter-dedup-design.md`; implementation plan `docs/superpowers/plans/2026-07-03-pr47-release-adapter-dedup.md`. Added `run_stream_subprocess(...)` to `adapters/common.py` owning the mkdir → `subprocess.run` → timeout/normal branches → `write_capture_artifact` pair → exit-code + redaction assertions → `StreamResult` flow; each adapter's `execute()` now builds its command/env and passes stream name, capability, message strings, and a reports callable. Duplicated `_now`/`_decode` moved to `common.py`; per-adapter variance (bash `bash-` capability prefix, bash inherit-env-when-no-extra-env, exact message strings) preserved byte-for-byte per the spec's variance table. Red-first: 4 direct helper tests (success/failure/timeout/reports) in `test_common.py`; the existing adapter suites (asserting on `StreamResult` fields) guard integrated behavior. Verification: `python -m pytest tests/release/adapters/ tests/release/test_orchestrator.py -q` passed (`90 passed`); full `./run_tests.sh` passed (root lane `1563 passed, 105 deselected`; release lane `1026 passed, 3 skipped`); touched-file `black`/`isort` applied, no new flake8 findings; `git diff --check` passed. |
| 48 | ready_for_review | `docs/thermos-48-review3-tracker` | `.claude/worktrees/thermos-48-review3-tracker` | Non-runtime tracker/repository-maintenance correction: 40 original Review #3 claims + 2 revalidation-added raw claims - 1 folded duplicate = 41 unique IDs (37 actionable, 1 optional, 2 rejected, 1 routed); PR #196/TR2D reconciliation; corrected H2, SSA, priority, and delivery boundaries | https://github.com/tomazb/rh-acm-switchover/pull/197 | Builder and review-comment resolver passes complete only; this status does not imply independent validation or merge readiness. GitHub PR is non-draft, while every branch-head change still requires fresh exact-head independent validation. Targeted documentation/CI/waiter suite: 89 passed. Strict `./run_tests.sh`: root lane 1831 passed, 105 deselected; release lane 1169 passed, 3 skipped; Black/isort/MyPy/Bandit/compile gates completed, with pip-audit advisory findings reported under its CI exit-zero policy. Count parser: 41 canonical rows and 41 matrix rows, 37/1/2/1 disposition. `git diff --check` and worktree-ignore check passed. Changed files remain exactly `.gitignore`, `AGENTS.md`, and `thermos-resolution-plan.md`. |


**PR 48 note:** this row covers non-runtime tracker/repository-maintenance:
the Review #3 record, repository worktree-ignore/instruction maintenance, the
2026-07-26 full-file revalidation, and this corrective resolver pass. No
design/spec gate applies: like `PR 01`, `PR 13`, `PR 28`, and `PR 32`, it is
tracker maintenance, not an implementation slice. `ready_for_review` in this
row means the builder and review-comment resolver passes are complete; GitHub
readiness is separate, each changed head requires fresh exact-head independent
validation, and merge readiness remains a further separate determination.

**PR39-001 blocker fix evidence (2026-07-05):** The PR39 restore-only regression was reproduced with only `acm_switchover_hubs.secondary`: the new integration test failed red at `Build hub RBAC validation table` with `'dict object' has no attribute 'primary'`. The fix preserves the PR39 `_rbac_hub_validations` table/include design and makes only the skipped primary row's `kubeconfig`/`context` expressions restore-only-safe; normal switchover mode still dereferences the required primary hub. Added `restore_only_rbac_secondary_only.yml` and `test_restore_only_rbac_with_secondary_only_hub_reports_secondary_validation`, proving `preflight-rbac-primary` is absent while `preflight-rbac-secondary` is still reported. No operator-CRD 401 hardening is included in this PR39-001 fix. Verification after the fix: focused repro passed; targeted preflight/RBAC suite passed (`63 passed`); collection unit suite passed (`822 passed`); root RBAC/parity suite passed (`183 passed`); `git diff --check` passed; full `./run_tests.sh` passed (root lane `1584 passed, 105 deselected`; release lane `1034 passed, 3 skipped`; Black/isort/MyPy/Bandit/pip-audit/compile checks clean).

**PR39 comment-resolution evidence (2026-07-06):** Copilot flagged that the restore-only-safe primary row placeholders used `| bool` while the row's `enabled` gate preserved the original `not (acm_switchover_operation.restore_only | default(false))` expression. Fixed the mismatch by using the same restore-only expression for primary `enabled`, `kubeconfig`, and `context`, preserving the PR39 hub-table design and avoiding placeholder dereferences under the exact row-skip rule. Added `test_preflight_rbac_primary_restore_only_skip_expression_is_consistent` (red before the fix) and reran the focused restore-only sparse-primary integration proof. CodeRabbit then flagged stale PR39 design/plan snippets, which now mirror the same restore-only-safe primary row expression; no Python, Helm, manifest, release-tooling, PR43, protected-file, registered-fact, or fail-closed behavior changes.

**PR43 comment-resolution evidence (2026-07-09):** Three unresolved Gemini review threads were source-validated and fixed in scope. `_missing_parse_required_args()` now uses `getattr()` for helper-level robustness with partial `SimpleNamespace` callers, guarded by a focused parser test. `argocd_resume.yml` now uses defensive `.get()` access for the explicit Argo CD run ID and checkpoint-enabled predicate while preserving the PR43 V1 invariant that `checkpoint.enabled` keeps bare Jinja truthiness and is not coerced through `| bool`; the static Argo CD resume tests now assert that defensive contract. The already-resolved CodeRabbit plan-example thread remained outdated/resolved. Verification after the resolver fixes: `python -m pytest tests/test_main.py::TestArgParsing -q` passed (`16 passed`); `python -m pytest tests/test_main.py tests/test_validation.py -q` passed (`200 passed`); `python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/test_argocd_hub_parameterization.py ansible_collections/tomazb/acm_switchover/tests/unit/test_ansible_resilience_contracts.py ansible_collections/tomazb/acm_switchover/tests/unit/test_argocd_resume_on_failure.py -q` passed (`86 passed`); `python -m pytest tests/test_documentation_guardrails.py -q` passed (`60 passed`); `git diff --check` passed; full `./run_tests.sh` passed (`1594 passed, 105 deselected`; release lane `1035 passed, 3 skipped`; Black/isort/MyPy/Bandit/pip-audit/compile clean); CodeRabbit local review `coderabbit review --agent -t uncommitted --base origin/ansible` reported `findings=0`. No protected files, RBAC permissions/manifests/Helm, live/lab certification behavior, R2-L2, Python H3, StateManager structural refactor, report schema, fail-closed/check-mode/idempotence semantics, or deferred PR43 findings were changed.

## Per-PR Implementation Details

### PR 01: Resolution Tracking And Agent Instructions

**Scope**
- Create this tracker.
- Add Thermos-specific instructions to `AGENTS.md`.
- No product behavior changes.

**Files**
- Create: `thermos-resolution-plan.md`
- Modify: `AGENTS.md`

**Acceptance Criteria**
- The tracker records all 28 findings, validation status, PR sequence, branch/worktree names, and verification commands.
- `AGENTS.md` directs future agents to use this tracker and keep parity rules active.
- Documentation guardrail tests still pass.

### PR 02: Restore-Only Fail-Closed Guard

**Scope**
- Prevent the full switchover playbook from running restore-only mode.
- Reject primary hub kubeconfig/context input when restore-only is selected.

**Likely Files**
- `ansible_collections/tomazb/acm_switchover/playbooks/switchover.yml`
- `ansible_collections/tomazb/acm_switchover/plugins/modules/acm_input_validate.py`
- `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_input_validate.py`
- Collection integration or role contract tests for switchover entrypoint behavior.

**Acceptance Criteria**
- `restore_only: true` through `switchover.yml` fails before `primary_prep`.
- `restore_only: true` plus non-empty primary hub kubeconfig fails input validation.
- `restore_only.yml` remains the supported restore-only entrypoint.

### PR 03: Klusterlet Fail-Closed Verification

**Scope**
- Make Python post-activation klusterlet worker timeout fail closed.
- Make collection klusterlet probe check-mode safe.
- Treat broad API/client failures as failed probe results, not skipped results.

**Likely Files**
- `modules/post_activation.py`
- `tests/test_post_activation.py`
- `ansible_collections/tomazb/acm_switchover/plugins/module_utils/klusterlet.py`
- `ansible_collections/tomazb/acm_switchover/plugins/modules/acm_klusterlet_probe.py`
- `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_klusterlet_modules.py`

**Acceptance Criteria**
- Initial klusterlet worker timeout raises a switchover failure in Python.
- Collection probe in check mode performs no live client construction and returns `changed=false`.
- API/client exceptions become failed probe results and fail the module task.
- Missing optional managed-cluster kubeconfig/import secret remains explicitly skipped only where current workflow expects it.

### PR 04: Hub Identity Resume Wiring

**Scope**
- Extend live UID validation to Argo CD resume-only when stored hub identities exist.
- Add main-level tests for hub identity wiring.

**Likely Files**
- `acm_switchover.py`
- `tests/test_main.py`
- `tests/test_utils.py`

**Acceptance Criteria**
- Resume-only fails on stored UID mismatch or unreadable live UID.
- Existing `--force` legacy-state behavior remains explicit and tested.
- Normal switchover, validate-only, dry-run, forced legacy state, and resume-only identity paths are covered.

### PR 05: Report And Checkpoint Identity Hygiene

**Scope**
- Stop writing kubeconfig paths into Ansible reports/checkpoint identity.
- Normalize legacy checkpoint identities that already contain kubeconfig fields.
- Improve checkpoint idempotence for changed state and report refs.

**Likely Files**
- `ansible_collections/tomazb/acm_switchover/plugins/modules/acm_preflight_report.py`
- `ansible_collections/tomazb/acm_switchover/plugins/module_utils/checkpoint.py`
- `ansible_collections/tomazb/acm_switchover/plugins/action/checkpoint_phase.py`
- Checkpoint/report tests under `ansible_collections/tomazb/acm_switchover/tests/unit/`

**Acceptance Criteria**
- Reports include context and cluster UID, not kubeconfig paths.
- New checkpoint operation identity excludes kubeconfig paths.
- Legacy checkpoints with kubeconfig identity fields still resume when context/UID match.
- Repeating a no-op checkpoint action does not duplicate report refs or report `changed=true`.

### PR 06: Finalization Freshness And Execute-Mode Coverage

**Scope**
- Refresh finalization MCH discovery in execute mode even when facts are pre-seeded.
- Add meaningful execute-mode integration coverage.
- Rename or rewrite misleading post-activation fixture test.

**Likely Files**
- `ansible_collections/tomazb/acm_switchover/roles/finalization/tasks/discover_resources.yml`
- `ansible_collections/tomazb/acm_switchover/tests/unit/test_discover_resources_contracts.py`
- `ansible_collections/tomazb/acm_switchover/tests/integration/fixtures/`
- `ansible_collections/tomazb/acm_switchover/tests/integration/test_switchover_roles.py`

**Acceptance Criteria**
- Execute-mode finalization ignores stale pre-seeded MCH data.
- At least one execute-mode integration fixture reaches a mutation-planning path without live clusters.
- Post-activation test names match asserted behavior.

### PR 07: RBAC And Operator Documentation

**Scope**
- Correct RBAC requirements.
- Make collection RBAC bootstrap the recommended path.
- Align collection README status.
- Replace broken design-spec links.

**Likely Files**
- `docs/deployment/rbac-requirements.md`
- `docs/deployment/rbac-deployment.md`
- `ansible_collections/tomazb/acm_switchover/README.md`
- `docs/project/summary.md`
- `docs/project/prd.md`
- Documentation guardrail tests.

**Acceptance Criteria**
- RBAC docs mention namespace `get/list` and read-only cluster health resources actually required by manifests/validators.
- Deprecated scripts are no longer recommended over `playbooks/rbac_bootstrap.yml`.
- No docs link to the missing `2026-04-10-ansible-collection-rewrite-design.md`.

### PR 08: RBAC Managed-Cluster Parity

**Scope**
- Add collection validation coverage for managed-cluster namespace permissions.
- Add full root-vs-collection-bundled RBAC manifest parity coverage.

**Likely Files**
- `ansible_collections/tomazb/acm_switchover/plugins/modules/acm_rbac_validate.py`
- `ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/validate_rbac.yml`
- `tests/test_rbac_collection_parity.py`
- `ansible_collections/tomazb/acm_switchover/tests/unit/test_rbac_bootstrap_contracts.py`
- Collection RBAC module tests.

**Acceptance Criteria**
- Collection managed-cluster permission expansion aligns with Python `RBACValidator`.
- Tests compare all bundled RBAC manifests against root manifests.
- Any support-boundary change is documented only if explicit operator approval exists.

### PR 09: Ansible Surface Cleanup

**Scope**
- Fix `acm_restore_info` check-mode changed reporting.
- Remove obsolete single-cluster klusterlet task files or protect secrets with `no_log`.
- Normalize public fact names where feasible.
- Implement or remove Helm `rbac.customNamespaces`.

**Likely Files**
- `ansible_collections/tomazb/acm_switchover/plugins/modules/acm_restore_info.py`
- `ansible_collections/tomazb/acm_switchover/roles/post_activation/tasks/`
- Post-activation role contract tests.
- `deploy/helm/acm-switchover-rbac/values.yaml`
- `deploy/helm/acm-switchover-rbac/templates/role.yaml`
- `deploy/helm/acm-switchover-rbac/templates/rolebinding.yaml`

**Acceptance Criteria**
- `acm_restore_info` reports `changed=false` in check mode.
- No active or stale task logs decoded kubeconfig/import material.
- Public facts use `acm_switchover_` prefix or have compatibility aliases with tests.
- Helm custom namespace documentation matches rendered templates.

### PR 10: Preflight Complete Reporting

**Scope**
- Keep collecting later preflight validation findings after RBAC validation records critical failures.
- Continue fail-fast behavior for unrecoverable controller/input exceptions.

**Likely Files**
- `ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/main.yml`
- `ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/validate_rbac.yml`
- Preflight integration fixtures/tests.

**Acceptance Criteria**
- A fixture with RBAC denial and backup failure reports both findings.
- Final preflight failure still occurs once critical findings exist.
- Fatal task exceptions still fail immediately.

### PR 11: Shared Ansible Logic Refactors

**Scope**
- Extract shared managed-cluster expectation derivation.
- Centralize activation auto-import version gating.

**Likely Files**
- `ansible_collections/tomazb/acm_switchover/roles/activation/tasks/`
- `ansible_collections/tomazb/acm_switchover/roles/post_activation/tasks/`
- Activation/post-activation role tests.

**Acceptance Criteria**
- Activation and post-activation use one shared expectation derivation.
- Activation computes auto-import support once.
- Finalization keeps separate MCH/version derivation when it needs fresh post-activation state.

### PR 12: Maintainability Refactor Backlog

**Scope**
- Split large Python and Ansible files only after safety fixes merge.
- Break this into smaller PRs if any single refactor touches multiple behavior surfaces.
- Close the residual F26 tracker gap by adding broad root-to-collection bundled RBAC manifest parity coverage.
- Restore strict local runner hygiene for the touched source/test scope.

**Likely Work Items**
- Extract shared flow helpers from `acm_switchover.py`.
- Extract Python klusterlet verifier internals after PR 03.
- Split `validate_backups.yml` or move complex logic into tested module utilities.
- Split `tests/test_main.py` fixtures and classes.
- Add full `deploy/rbac/` to collection-bundled `deploy/rbac/` file-set and content parity tests.
- Keep `.worktrees/` ignored by flake8 so advisory style checks do not scan the nested worktrees used by this slice.

**Acceptance Criteria**
- Refactors are behavior-preserving.
- Targeted tests pass after each sub-slice.
- Root and collection-bundled RBAC manifests are compared by file set and exact content.
- Full strict suite runs before final merge.

### PR 13: Round 6 Tracking Reconciliation

**Scope**
- Add Round 6 findings F29-F36 to this tracker.
- Mark PR 12 merged now that PR #84 is present in local `ansible` history.
- Record F30 as resolved by current CI-scope Black verification.
- No product behavior changes.

**Files**
- Modify: `thermos-resolution-plan.md`

**Acceptance Criteria**
- The validation matrix records F29-F36 with planned resolution PRs.
- The PR sequence includes one planned Round 6 row per follow-up slice.
- PR 12 status is `merged`.
- Documentation guardrail tests pass.

### PR 14: ACM Version Parsing Parity

**Scope**
- Fix F29 by aligning Python and collection BackupSchedule version decisions.
- Accept ACM versions with pre-release/build suffixes when the leading numeric `major.minor` decision is unambiguous, such as `2.14.3-rc1` and `2.14.3+build`.
- Fail closed on truly unparsable versions instead of silently routing Python to the ACM 2.11 delete path.

**Likely Files**
- `lib/utils.py`
- `modules/primary_prep.py`
- `modules/backup_schedule.py`
- `ansible_collections/tomazb/acm_switchover/plugins/modules/acm_backup_schedule.py`
- `tests/test_utils.py`
- `tests/test_backup_schedule.py`
- `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_backup_schedule.py`
- Parity tests if a shared fixture is added.

**Acceptance Criteria**
- Python and collection both classify `2.14.3-rc1` and `2.14.3+build` as pause-capable.
- Python does not delete a BackupSchedule when the ACM version cannot be parsed.
- Existing ACM 2.11 delete behavior remains intact for clean 2.11 versions.
- Targeted Python and collection BackupSchedule tests pass.

### PR 15: Kubeconfig Token Hardening

**Scope**
- Fix F32 and F36 in one credential-handling slice.
- Create generated kubeconfig output directories with owner-only permissions.
- Write token-bearing kubeconfigs under `umask 077` so there is no readable window before `chmod`.
- Reduce the default TokenRequest duration from `48h` to a shorter operator-safe default, or require explicit opt-in for longer durations.

**Likely Files**
- `scripts/setup-rbac.sh`
- `scripts/generate-sa-kubeconfig.sh`
- `scripts/generate-merged-kubeconfig.sh`
- `tests/test_scripts_integration.py`
- RBAC deployment docs if defaults or examples change.

**Acceptance Criteria**
- `setup-rbac.sh` uses a tightened umask for kubeconfig writes and creates output directories with mode `700`.
- Token duration defaults are consistent across setup, single-service-account generation, and merged kubeconfig generation.
- CLI validation and docs reflect the new default and explicit longer-duration path.
- Script integration/static tests cover the secure write pattern and duration default.

### PR 16: Container Supply Chain Hardening

**Scope**
- Fix F33.
- Pin container base images by digest.
- Verify `jq` and OpenShift client downloads with checksums before installation.
- Avoid `curl | tar` extraction.

**Likely Files**
- `container-bootstrap/Containerfile`
- Container build docs if image pinning or version update process needs operator guidance.
- Tests or static guardrails that inspect the Containerfile.

**Acceptance Criteria**
- Base images use digest-pinned references.
- Downloaded `jq` and OpenShift client artifacts are verified before use.
- `oc`/`kubectl` extraction happens only after checksum verification.
- Static tests fail if mutable tags or unchecked `curl | tar` patterns return.

### PR 17: Klusterlet Bootstrap Secret Ordering

**Scope**
- Fix F34.
- Remove the delete-then-create failure window for `bootstrap-hub-kubeconfig` during Python and collection klusterlet remediation.
- Preserve current idempotent behavior and failure reporting.
- Preserve Python/collection parity by changing both implementations and managed-cluster RBAC/docs from `delete` to `patch`.

**Likely Files**
- `modules/post_activation.py`
- `ansible_collections/tomazb/acm_switchover/plugins/module_utils/klusterlet.py`
- RBAC validators, managed-cluster RBAC manifests/policies, and RBAC docs.
- Python post-activation, collection klusterlet, RBAC parity, and documentation guardrail tests.
- `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_klusterlet_modules.py`

**Acceptance Criteria**
- A failed replacement attempt does not leave a previously existing bootstrap secret deleted.
- Existing 404/409 handling remains intentional and tested.
- Remediation still reports accurate `changed`, `failed_clusters`, and per-step status.
- Managed-cluster RBAC requires `secrets` `patch`, not `delete`, with Python and collection validators aligned.
- Collection klusterlet module tests pass.

### PR 18: Safe Path Consolidation

**Scope**
- Fix F31.
- Collapse duplicated safe-path/report-artifact validation logic into one canonical Python implementation.
- Add a thin collection copy where import boundaries require it.
- Add adversarial parity coverage for traversal, symlink escapes, `/tmp` prefix confusion, home/cwd roots, and non-existent ancestors.

**Likely Files**
- `lib/validation.py`
- `lib/report_artifacts.py`
- `ansible_collections/tomazb/acm_switchover/plugins/module_utils/validation.py`
- `tests/fixtures/validation_parity_cases.yml`
- `tests/test_validation.py`
- `tests/test_report_artifacts.py`
- `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/module_utils/test_validation_security.py`
- `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_safe_path_validate.py`

**Acceptance Criteria**
- Python CLI, Python report artifact, and collection report artifact checks make the same accept/reject decisions for shared cases.
- Symlink escape and prefix-confusion cases are explicitly covered.
- Existing safe artifact write protections, including no-follow behavior, remain intact.
- Python and collection validation parity tests pass.

### PR 19: Helm Validator Rule Guardrail

**Scope**
- Resolve F35 after explicit operator decision.
- Reject mutating verbs in `rbac.customValidatorRules` so the validator ClusterRole remains read-only.
- Do not add an escape hatch or intentional parity divergence.

**Likely Files**
- `deploy/helm/acm-switchover-rbac/templates/clusterrole.yaml`
- `deploy/helm/acm-switchover-rbac/values.yaml`
- `deploy/helm/acm-switchover-rbac/README.md`
- Helm/static tests for rendered RBAC behavior.
- Parity/support docs if the read-only validator contract intentionally changes.

**Acceptance Criteria**
- The validator read-only invariant is enforced by template guardrails.
- Mutating validator custom rules cannot be added silently.
- Helm rendering/static tests cover allowed read-only custom rules and rejected mutating custom rules.
- No intentional parity/support boundary change is introduced.

### PR 20: Standalone Argo CD Resume Identity Validation

**Scope**
- Resolve F37 by validating collection checkpoint operation identity before standalone Argo CD resume mutates Applications.
- Preserve explicit `run_id` behavior when operators supply `acm_switchover_argocd.run_id` or `acm_switchover_execution.run_id`.
- Accept two-hub swapped primary/secondary contexts only when stored context and live UID pairs match the checkpoint.

**Likely Files**
- `ansible_collections/tomazb/acm_switchover/playbooks/argocd_resume.yml`
- `ansible_collections/tomazb/acm_switchover/plugins/modules/acm_checkpoint_identity_validate.py`
- `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_checkpoint_identity_validate.py`
- `ansible_collections/tomazb/acm_switchover/tests/unit/test_argocd_hub_parameterization.py`
- Operator-facing collection docs and changelog.

**Acceptance Criteria**
- Checkpoint-backed standalone resume reads live `kube-system` namespace UIDs before including `argocd_manage`.
- Missing checkpoint identity, missing live UID, unreadable live identity, and UID/context mismatch fail before resume mutation.
- Normal and swapped two-hub mappings are covered by unit tests.
- Explicit `run_id` input remains usable without checkpoint loading.

### PR 21: Python Klusterlet Fail-Closed Recheck

**Scope**
- Resolve F38 by separating expected non-fatal klusterlet skips from real Python inspection failures.
- Leave collection klusterlet code unchanged unless parity tests expose drift.

**Likely Files**
- `modules/post_activation.py`
- `tests/test_post_activation.py`
- `CHANGELOG.md`
- Operator docs that describe klusterlet probing behavior.

**Acceptance Criteria**
- No kubeconfig context, missing klusterlet hub secret, malformed/uninspectable secret data, and no hub server in the secret remain non-fatal skips.
- Client construction failures, non-404 API errors, transport errors, and unexpected probe exceptions are classified as failed statuses.
- `_verify_klusterlet_connections` raises `SwitchoverError` when those failed probe results are present.
- Regression tests cover 403/500 secret reads, bootstrap fallback 500, client construction failure, and full verification failure.

### PR 27: Orchestrator Runtime Bootstrap Extraction

**Scope**
- Start `F44` with an orchestrator-first runtime/bootstrap extraction scoped to `acm_switchover.py`.
- Preserve the current `acm_switchover.*` helper patch/import surface while moving leaf runtime logic into `lib/runtime_bootstrap.py`.
- Record the design/plan artifacts plus green focused verification for this first implementation slice.

**Likely Files**
- `thermos-resolution-plan.md`
- `docs/superpowers/specs/2026-06-07-pr27-orchestrator-decomposition-design.md`
- `docs/superpowers/plans/2026-06-07-pr27-runtime-bootstrap-extraction.md`
- `lib/runtime_bootstrap.py`
- `acm_switchover.py`
- `tests/test_runtime_bootstrap.py`
- `tests/test_main.py`

**Acceptance Criteria**
- `PR 26` is recorded as merged and the F39-F43 gate is reflected as resolved in the tracker.
- `PR 27` is recorded as the runtime/bootstrap extraction slice with both the spec and implementation plan paths.
- `lib/runtime_bootstrap.py` owns the extracted leaf state/bootstrap helpers and direct unit coverage.
- `main()` delegates state/bootstrap setup through `_prepare_runtime()` without breaking existing `acm_switchover` helper patch/import surfaces.
- Focused runtime/bootstrap regression tests and release-parity/documentation guardrails pass.

### PR 28: Remaining F44 Slice Map

**Scope**
- Record the remaining `F44` seams after `PR 27` without changing product code.
- Add a docs-only design spec that fixes the intended follow-up slice order.
- Update the tracker so later `F44` implementation PRs start from an explicit map.
- Stage the slice-specific `PR29` design/spec and implementation plan so the next
  extraction can start from reviewed docs in a separate worktree.

**Likely Files**
- `thermos-resolution-plan.md`
- `docs/superpowers/specs/2026-06-07-pr28-f44-remaining-slice-map-design.md`
- `docs/superpowers/specs/2026-06-07-pr29-operation-runner-design.md`
- `docs/superpowers/plans/2026-06-07-pr29-operation-runner-extraction.md`

**Acceptance Criteria**
- `PR 28` is recorded as a docs-only scope pass in the tracker.
- The remaining `F44` backlog is mapped into distinct follow-up seams for
  operation runners, Argo CD resume safety, and CLI outcome/report
  orchestration.
- The tracker adds tentative `PR 29` through `PR 31` rows that remain gated on
  slice-specific design/spec and implementation-plan artifacts before
  implementation starts.
- The branch records the `PR29` design/spec and implementation plan paths so the
  next worktree can begin from approved docs instead of branch-local chat
  context.
- Documentation guardrail verification passes.

### PR 32: Thermos Review Validation And B1 Cleanup

**Scope**
- Add the validated Thermos Review #1 findings document.
- Update this tracker with the 2026-06-13 validation summary and follow-up order.
- Resolve `B1` only by removing the dead `Finalization` instance field for the
  deprecated observability flag.
- Preserve constructor compatibility for `disable_observability_on_secondary` and
  keep old-hub observability deletion automatic when observability is detected.

**Files**
- Create: `docs/plans/2026-06-13-thermos-ansible-review-findings.md`
- Modify: `thermos-resolution-plan.md`
- Modify: `modules/finalization.py`
- Modify: `tests/test_finalization.py`

**Acceptance Criteria**
- The deprecated CLI flag remains accepted and documented as deprecated.
- `Finalization(..., disable_observability_on_secondary=False)` still deletes
  old-hub `MultiClusterObservability` when the old hub is kept as a secondary and
  primary observability was detected.
- Tests that directly exercise old-hub observability deletion no longer pass
  misleading deprecated-flag arguments.
- Targeted Python, collection parity, documentation guardrail, formatting, and
  diff checks pass.

## Verification Command Reference

Use the narrowest meaningful command before each PR, then broaden before push when behavior changes cross subsystems.

```bash
python -m pytest tests/test_documentation_guardrails.py -q
python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/ -q
python -m pytest tests/test_main.py tests/test_post_activation.py -q
python -m pytest tests/test_rbac_collection_parity.py tests/test_rbac_validator.py ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_rbac_validate.py -q
./run_tests.sh
```

## Current Branch Notes

- Base branch: `ansible`
- Root worktree had pre-existing untracked Graphify review artifacts and `thermos_ansible_review.md` when this tracker was created.
- Keep Thermos implementation changes isolated in `.claude/worktrees/thermos-*` worktrees and their corresponding branches. Rows `01`-`33` record the historical `.worktrees/` path and are left as accurate history. Only the `H1` and `43` rows were corrected on 2026-07-26, because those two slices ran after the convention changed and had recorded the old path in error.
