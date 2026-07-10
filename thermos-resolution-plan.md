# Thermos Ansible Review Resolution Plan

> **For agentic workers:** REQUIRED SUB-SKILLS: Use `superpowers:using-git-worktrees` before starting each implementation PR. Before changing code for any planned Thermos slice, use `superpowers:brainstorming` to explore the current context, compare approaches, and write an approved design/spec for that slice; then use `superpowers:writing-plans` to turn the approved design into the implementation plan. Use `superpowers:executing-plans` or `superpowers:subagent-driven-development` only after the design/spec and implementation plan are written and approved. Update this file in every Thermos PR.

**Goal:** Resolve the validated findings captured from the operator-supplied external Thermos Ansible review through isolated, reviewable branches without parity drift between the Python CLI and the Ansible collection.

**Architecture:** Treat the external report as a hypothesis source, not an authority. The original report may exist locally as an untracked `thermos_ansible_review.md`, but it is not required in a fresh checkout; this tracker is the self-contained resolution source. Every finding must stay tied to source evidence, tests, and documentation changes. Each PR uses a dedicated worktree and branch, updates this tracker, and preserves the dual-supported parity contract unless explicit operator approval records an intentional divergence.

**Tech Stack:** Python CLI, Ansible collection roles/playbooks/modules, pytest, GitHub PRs, `.worktrees/` git worktrees.

---

## State Tracking Rules

- Status values: `planned`, `in_progress`, `ready_for_review`, `merged`, `blocked`, `deferred`.
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

This gate applies to the remaining deep-scan queue (`PR 24` onward) and to any new Thermos follow-up slice added later.

**Last Updated:** 2026-07-09

## Post-Merge Revalidation (2026-06-03)

Current `ansible` HEAD `ac041f6` includes merged Thermos PRs 17-20 (`#89`, `#90`, `#91`, `#92`). A focused source revalidation confirmed:

- `F31` is resolved: path-safety now routes through canonical `path_safety` helpers plus adversarial parity coverage.
- `F34` is resolved: Python and collection klusterlet remediation now patch/create `bootstrap-hub-kubeconfig`; managed-cluster RBAC/docs were realigned to `patch`.
- `F35` is resolved: Helm rendering now rejects mutating `rbac.customValidatorRules` verbs before template output.
- `F37` is resolved: standalone collection `argocd_resume.yml` validates checkpoint hub UID identity against live hubs before resuming Applications.
- Historical note: `F38` was the first residual follow-up after this snapshot and
  was later resolved by `PR 21`. The current source of truth is the resolved
  validation matrix and PR sequence below.

## Deep-Scan Follow-Up Queue (2026-06-04)

Validated follow-up findings from the Graphify-assisted deep scan and paired Thermos review passes.
Status after merged follow-up PRs 22-26:

- `F39` Resolved by `PR 22`: Python Argo CD resume-only now fails closed for legacy state files that recorded paused Applications before `hub_identities` existed.
- `F40` Resolved by `PR 23`: Python dry-run Argo CD management now performs discovery and blocker reporting instead of skipping directly to no-op behavior.
- `F41` Resolved by `PR 24`: Argo CD pause discovery now scopes `Application` listing to the relevant namespaces without weakening durable pause-state persistence.
- `F42` Resolved by `PR 25`: Python RBAC preflight no longer expands to avoidable serial SelfSubjectAccessReview probes for each repeated tuple.
- `F43` Resolved by `PR 26`: Release runtime parity now compares real resume, Argo CD, and RBAC/bootstrap outcomes instead of mostly artifact metadata.
- `F44` Active follow-up: `PR 27` completed the runtime/bootstrap extraction, and
  `PR 28` now records the remaining reviewable seams before the next
  implementation slices start.

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
runtime/bootstrap seam, and `PR 28` now records the remaining slice map for the
follow-up implementation PRs.

## Thermos Review #1 Revalidation (2026-06-13)

Current `ansible` HEAD `f52a19d4` was revalidated against the operator-supplied
Thermos review captured in
`docs/plans/2026-06-13-thermos-ansible-review-findings.md`.

- `B1`, `H1`, `H2`, `H3`, `M2`-`M5`, and `L2`-`L7` remain real.
- `B1` is a low-risk cleanup only: old-hub `MultiClusterObservability` deletion is
  already documented, parity-tested, and no longer gated by the deprecated
  `--disable-observability-on-secondary` flag.
- `M1` is lower value after `PR 29`-`PR 31` extracted the major orchestration
  seams.
- `L1` is already covered by path-safety tests; no immediate PR is planned.

Follow-up order after `PR 32`:

1. `H2` - add custom-resource access helpers and collapse repeated
   group/version/plural call-site boilerplate.
2. `H1` - derive the Python validator RBAC table from the Python operator table
   while keeping cross-surface parity tests. (In review via
   `refactor/thermos-h1-python-rbac-unification`; see the `H1` row in the PR
   Sequence table.)
3. `M4` - add `StateManager.get_completed_steps()` /
   `get_step_timestamp(name)` and remove direct `StateManager.state` reach-through.
4. `H3` - decompose large modules only through separate design-gated PRs.

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
- 12 new findings were validated with independent source verification (not
  just subagent claims): `R2-H1` (unbounded delete API calls on the
  PRIMARY_PREP critical path), `R2-H2` (a sharper, quantified framing of the
  existing `H2`: `MANAGED_CLUSTER_API_GROUP` is used in only 1 of ~48 relevant
  call sites), `R2-H3` (new ~140-line RBAC validation duplication on the
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

Follow-up order after `PR 33` (new queue, sequenced for risk-adjusted value —
mechanical/low-risk fixes first to build confidence, then safety fixes, then
larger structural work):

1. `PR 34` - `R2-H2`: route the 47 remaining hardcoded API-group/version/plural
   literals in `modules/` through `MANAGED_CLUSTER_API_GROUP` (and companion
   constants where they exist).
2. `PR 35` - `R2-M4`: deduplicate `lib/utils.py` `REPORT_PHASE_NAMES` and
   `lib/workflow.py` `_CANONICAL_RESUME_START_PHASES` into one shared mapping.
3. `PR 36` - `R2-H1`: add request timeouts to `delete_configmap`/`delete_pod`
   and thread an explicit timeout through `primary_prep.py`'s ACM ≤2.11
   BackupSchedule delete call.
4. `PR 37` - `R2-M1` (part 1): fix `acm_preflight_report.py`'s check-mode
   `changed` override to match its `acm_report_artifact.py` sibling.
5. `PR 38` - `R2-M1` (part 2): resolve or explicitly document the
   native-Ansible-check-mode `changed` gap in `pause_backups.yml` /
   `activate_restore.yml` against the `docs/variable-reference.md`
   "native check mode is non-mutating" contract.
6. `PR 39` - `R2-H3`: deduplicate the Ansible RBAC validation task file's
   primary/secondary blocks; sequence against the still-queued Python `H1`
   unification so both sides land on a consistent approach.
7. `PR 40` - `R2-M3` + existing `M2`: extract the near-duplicate
   `_wait_for_restore_deletion`/`_wait_for_primary_restore_deletion` methods
   into `lib/waiter.py` alongside the `M2` unification work.
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

Existing queued items `H1`, `H2`, `M4`, `H3` (Python-side, from the `PR 32`
follow-up order above) remain valid and should be sequenced alongside `PR 34`,
`PR 39`, and the Python `H3` decomposition track; this queue does not replace
that one, it extends it with Review #2's findings.

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
| F41 | resolved | PR 24 | Argo CD discovery now uses namespace scoping where available while preserving per-app durable pause persistence. |
| F42 | resolved | PR 25 | Python RBAC preflight now avoids repeated serial SelfSubjectAccessReview probes without losing reporting fidelity. |
| F43 | resolved | PR 26 | Release runtime parity now compares real resume, Argo CD, and RBAC/bootstrap outcomes instead of mostly artifact metadata. |
| F44 | planned deep-scan follow-up | PR 27+ | `PR 27` extracted the runtime/bootstrap seam after PR26 cleared the parity/runtime guardrail gate. `PR 28` records the remaining slice map before follow-up implementation slices tackle operation runners, Argo CD resume safety, and CLI outcome/report orchestration. |
| R2-H1 | confirmed | PR 36 | `delete_configmap`/`delete_pod` in `lib/kube_client.py` and the ACM ≤2.11 `delete_custom_resource` BackupSchedule call in `modules/primary_prep.py` have no request timeout; a hung API call can block PRIMARY_PREP indefinitely. |
| R2-H2 | confirmed, sharper framing of `H2` | PR 34 | `MANAGED_CLUSTER_API_GROUP` exists but is used in only 1 of ~48 relevant call sites across `modules/`; verified by grep. |
| R2-H3 | confirmed | PR 39 | Ansible RBAC validation task file duplicates ~140 lines between primary-hub and secondary-hub blocks, mirroring the still-open Python `H1`. |
| R2-M1 | confirmed, resolves subagent disagreement | PR 37 (part 1), PR 38 (part 2) | `acm_preflight_report.py` computes an accurate check-mode `changed` value then explicitly discards it (confirmed by reading `write_json_artifact`/`write_report` and comparing against sibling `acm_report_artifact.py`, which has no such override) — a real, self-contained bug. `acm_backup_schedule.py`/`acm_restore_info.py` force `changed=False` under native Ansible check mode; their owning roles do not surface this to the published role-level `changed` result unless the collection's own `mode: dry_run` variable is set (traced through `pause_backups.yml`/`activate_restore.yml`), which is misleading against the documented "native Ansible check mode is non-mutating even when `mode: execute`" contract in `docs/variable-reference.md` — a real but architecturally distinct issue from the `acm_preflight_report.py` bug. |
| R2-M2 | confirmed | PR 42 | Crash between restore-staleness verification and activation completion, followed by resume, can skip re-validating restore staleness before completing activation. |
| R2-M3 | confirmed | PR 40 | `_wait_for_restore_deletion` (`activation.py`) and `_wait_for_primary_restore_deletion` (`finalization.py`) are near-verbatim duplicates; bundle with existing `M2` waiter unification. |
| R2-M4 | confirmed | PR 35 | `lib/utils.py` `REPORT_PHASE_NAMES` and `lib/workflow.py` `_CANONICAL_RESUME_START_PHASES` are byte-identical duplicate dicts. |
| R2-M5 | confirmed | PR 41 | Ansible summary-path resolution logic is duplicated across 4 role/playbook locations. |
| R2-L1..L9 | confirmed, low priority | PR 43 | Mixed low-severity maintainability/robustness items; see findings doc for the full list and per-item effort. |
| R2-H4 | confirmed | PR 44 | `tests/release/orchestrator.py` is 1199 lines on its first commit; `_run_release_certification` (335 lines) triplicates a short-circuit finalize pattern at 3 call sites. |
| R2-M6 | confirmed | PR 47 | `tests/release/adapters/ansible.py`, `bash.py`, `python_cli.py` duplicate ~70% of `execute()` logic despite an existing shared contract in `adapters/common.py`. |
| R2-M7 | confirmed | PR 45 | `tests/release/orchestrator.py` duplicates primary/secondary RBAC certification handling inline (~75 lines) instead of looping over a shared helper. |
| R2-M8 | confirmed | PR 46 | `tests/release/checks/rbac_certification.py`'s required-vs-forbidden permission evaluation loops are the same algorithm with polarity flipped, duplicated in full. |

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
| 34 | merged | `fix/thermos-34-managed-cluster-constant` | `.claude/worktrees/thermos-34-managed-cluster-constant` | R2-H2 (sharper H2) | https://github.com/tomazb/rh-acm-switchover/pull/127 | Design spec `docs/superpowers/specs/2026-07-02-pr34-managed-cluster-constant-design.md`; implementation plan `docs/superpowers/plans/2026-07-02-pr34-managed-cluster-constant.md`. Routed all 56 hardcoded `cluster.open-cluster-management.io` literals in `modules/**/*.py` (the review's 49 top-level sites plus 7 more found in `modules/preflight/` during the red-test run) and the 2 behavioral `lib/kube_client.py` helpers through `MANAGED_CLUSTER_*` and new `CLUSTER_BACKUP_*` constants; `ACM_BACKUP_SCHEDULE_TYPE_LABEL` now derives from the shared group constant. Added red-first static guardrail `tests/test_api_literal_guardrails.py` (red at 56 violations, now green) and the `MANAGED_CLUSTER_API_GROUP` ↔ `CLUSTER_OPEN_CLUSTER_MANAGEMENT_IO` pair in `tests/test_constants_parity.py`. Verification: targeted `python -m pytest tests/test_api_literal_guardrails.py tests/test_constants_parity.py tests/test_activation.py tests/test_finalization.py tests/test_post_activation.py tests/test_primary_prep.py tests/test_backup_schedule.py tests/test_decommission.py tests/test_kube_client.py -q` passed (`456 passed`); preflight suites passed (`147 passed`); touched-file `black`/`isort` (line-length 120) applied; `git diff --check` passed; full `./run_tests.sh` passed (root lane `1557 passed, 105 deselected`; release lane `1021 passed, 3 skipped`; Black/isort/MyPy/Bandit/pip-audit all clean). PR #127 merged into `ansible` 2026-07-02. |
| 35 | merged | `refactor/thermos-35-phase-name-dedup` | `.claude/worktrees/thermos-35-phase-name-dedup` | R2-M4 | https://github.com/tomazb/rh-acm-switchover/pull/128 | Design spec `docs/superpowers/specs/2026-07-02-pr35-phase-name-dedup-design.md`; implementation plan `docs/superpowers/plans/2026-07-02-pr35-phase-name-dedup.md`. Collapsed the byte-identical `lib/utils.py` `REPORT_PHASE_NAMES` and `lib/workflow.py` `_CANONICAL_RESUME_START_PHASES` dicts into one `CANONICAL_PHASE_NAMES` mapping in `lib/utils.py` (next to the `Phase` enum), consumed by `StateManager.mark_step_completed`, `lib/workflow.py` resume-start summaries, and `lib/cli_outcomes.py`; no compatibility alias (zero external references, verified by grep). Red-first guardrails in `tests/test_phase_name_canonical.py` (exact executable-phase key set, SECONDARY_VERIFY fold, `is`-identity across consumers, old names gone). Verification: `python -m pytest tests/test_phase_name_canonical.py tests/test_utils.py tests/test_main_phase_flow.py tests/test_cli_outcomes.py tests/test_report_artifacts.py tests/test_main.py -q` passed (`298 passed`); touched-file `black`/`isort` (line-length 120) applied; `git diff --check` passed; full `./run_tests.sh` passed (root lane `1560 passed, 105 deselected`; release lane `1021 passed, 3 skipped`). PR #128 merged into `ansible` 2026-07-03 (includes read-only MappingProxyType hardening). |
| 36 | merged | `fix/thermos-36-delete-timeouts` | `.claude/worktrees/thermos-36-delete-timeouts` | R2-H1 | https://github.com/tomazb/rh-acm-switchover/pull/129 | Design spec `docs/superpowers/specs/2026-07-03-pr36-delete-timeouts-design.md`; implementation plan `docs/superpowers/plans/2026-07-03-pr36-delete-timeouts.md`. `delete_configmap`/`delete_pod` now pass the client-default request timeout via `_request_timeout_kwargs()` like every sibling core_v1 call, and `primary_prep.py`'s ACM ≤2.11 BackupSchedule delete passes `timeout_seconds=DELETE_REQUEST_TIMEOUT`, matching the five equivalent delete sites in activation/decommission; timeout expiry flows through the existing tenacity retry/error path. Red-first: tightened `assert_called_once_with` in `tests/test_kube_client.py` (both deletes) and `tests/test_primary_prep.py` (ACM 2.11 delete) to require the timeout kwargs (3 failed before the fix). Verification: `python -m pytest tests/test_kube_client.py tests/test_primary_prep.py -q` passed (`135 passed`); touched-file `black`/`isort` applied; `git diff --check` passed; full `./run_tests.sh` passed (root lane `1556 passed, 105 deselected`; release lane `1021 passed, 3 skipped`). Note: trivial adjacent-line overlap with PR 34 at the `primary_prep.py` call site (PR 34 rewrites the group/version/plural lines) — whichever merges second resolves a one-hunk conflict. |
| 37 | merged | `fix/thermos-37-preflight-report-checkmode` | `.claude/worktrees/thermos-37-preflight-report-checkmode` | R2-M1 (part 1) | https://github.com/tomazb/rh-acm-switchover/pull/130 | Design spec `docs/superpowers/specs/2026-07-03-pr37-preflight-report-checkmode-design.md`; implementation plan `docs/superpowers/plans/2026-07-03-pr37-preflight-report-checkmode.md`. Deleted `acm_preflight_report.py`'s unconditional `if module.check_mode: changed = False` override so the module reports `write_json_artifact`'s accurate diff-based `changed`, matching sibling `acm_report_artifact.py`; the no-write guarantee stays in `write_json_artifact`'s check-mode gate, and the preflight role's published `acm_switchover_preflight_result.changed` now carries the truthful value. Red-first: flipped the check-mode create test to assert `changed=True` (no write) and added a mode-independence test; also fixed the matching-artifact fixture, which lacked the `cluster_uid` field `sanitize_report_hubs` adds and only passed because the override masked the real diff. Verification: module suite `10 passed`; collection unit suite `807 passed`; full `./run_tests.sh` passed (root lane `1563 passed, 105 deselected`; release lane `1021 passed, 3 skipped`); touched-file `black`/`isort` applied; `git diff --check` passed. PR #130 merged into `ansible` 2026-07-03. |
| 38 | merged | `fix/thermos-38-checkmode-changed-surfacing` | `.claude/worktrees/thermos-38-checkmode-changed-surfacing` | R2-M1 (part 2) | https://github.com/tomazb/rh-acm-switchover/pull/131 | Design spec `docs/superpowers/specs/2026-07-03-pr38-checkmode-changed-surfacing-design.md` records the required decision: **(a) wiring chosen**, plus a one-sentence doc clarification. `acm_backup_schedule.py`/`acm_restore_info.py` plan modules now report mode-independent `changed` (they mutate nothing; the check-mode zeroing was the same discard-the-right-answer pattern PR 37 removed), and the `pause_backups.yml`/`activate_restore.yml` published-changed fallbacks treat `ansible_check_mode` like `mode: dry_run`, so `--check` with `mode: execute` publishes truthful would-change verdicts. `variable-reference.md` mode row documents the clarified contract. Red-first: flipped the two plugin tests pinning suppressed behavior + 2 new role-contract text asserts (4 red before the fix). Verification: collection unit suite `808 passed`; full `./run_tests.sh` passed (root lane `1563 passed, 105 deselected`; release lane `1021 passed, 3 skipped`); touched-file `black`/`isort` applied; `git diff --check` passed. Implementation plan `docs/superpowers/plans/2026-07-03-pr38-checkmode-changed-surfacing.md`. PR #131 merged into `ansible` 2026-07-03. |
| H1 | merged | `refactor/thermos-h1-python-rbac-unification` | `.worktrees/thermos-h1-python-rbac-unification` | H1 (Python RBAC validator unification) | https://github.com/tomazb/rh-acm-switchover/pull/148 | Design spec `docs/superpowers/specs/2026-07-05-python-h1-rbac-unification-design.md`; implementation plan `docs/superpowers/plans/2026-07-05-python-h1-rbac-unification.md`. `VALIDATOR_CLUSTER_PERMISSIONS` is now derived from `OPERATOR_CLUSTER_PERMISSIONS` via `_derive_read_only_permissions()` stripping `MUTATING_VERBS`, with the managedclusters `patch` exception recorded as explicit data (`VALIDATOR_CLUSTER_VERB_EXCEPTIONS`) and verified at import time (drift raises `ValueError`). `validate_rbac_permissions()` primary/secondary duplication collapsed into a hub-parameterized `_validate_hub()` helper plus a loop over an explicit per-hub table; asymmetries preserved as data (primary-only decommission/old-hub-finalization pass-through, secondary-only install-type override and error-count failure message, primary-only "not available" skip log). Behavior preserved byte-for-byte (message strings pinned by tests); hub namespace tables intentionally stay literal (not pure verb-strips). Red-first tests: derivation/exception/drift-guard class plus hub-loop asymmetry/message tests (8 failed before implementation across the two facets). Verification: `git diff --check` passed; `python -m pytest tests/test_rbac_validator.py tests/test_check_rbac.py tests/test_rbac_collection_parity.py tests/test_rbac_integration.py tests/release/checks/test_rbac_certification.py -q` passed; full-parity `python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/ tests/ -q` passed; `./run_tests.sh` passed. This PR establishes the hub-role-loop structural pattern that `PR 39` (R2-H3) must mirror on the Ansible side; no Ansible RBAC validation logic touched. Review-comment resolution pass (2026-07-05): Copilot inline comment (drift-guard `ValueError` printed only dict keys via `sorted(<dict>)`, hiding stripped verbs) fixed by formatting the full key→verbs mapping via `_format_verb_removals()` with red-first message assertions in both drift tests; CodeRabbit nitpick (secondary hub used `not client` vs primary `client is None`) fixed by unifying on `client is None`. Verification after fixes: targeted RBAC/parity suites passed (183), full-parity pytest passed (3515 passed, 29 skipped), `git diff --check` clean, `./run_tests.sh` passed. PR #148 merged into `ansible` 2026-07-05 at merge commit `0afeea52`; the `PR 39` branch was created from that updated base (status reconciled to merged per the tracker rules). |
| 39 | merged | `refactor/thermos-39-ansible-rbac-dedup` | `.claude/worktrees/thermos-39-ansible-rbac-dedup` (planned `.worktrees/` path unavailable in the implementing session; same isolation contract) | R2-H3 | https://github.com/tomazb/rh-acm-switchover/pull/149 | Design spec `docs/superpowers/specs/2026-07-05-pr39-ansible-rbac-dedup-design.md`; implementation plan `docs/superpowers/plans/2026-07-05-pr39-ansible-rbac-dedup.md`. Branch created from updated `origin/ansible` merge commit `0afeea52` (Python `H1` merged, PR #148). The ~140-line duplicated primary/secondary blocks in `roles/preflight/tasks/validate_rbac.yml` now run through a `_rbac_hub_validations` data table plus one `include_tasks` loop over the new shared `validate_rbac_hub.yml`, mirroring Python H1's `hub_validations` table + `_validate_hub()` loop; asymmetries are table data (primary-only restore-only skip via `enabled`, decommission expression, old-hub finalization; secondary `false` literals), `_rbac_skip_observability` stays computed once, the managed-cluster section stays separate, and every per-hub fact name (`_rbac_argocd_app_crd_*`, `_rbac_argocd_instance_crd_*`, `_rbac_argocd_install_type_*`, `_rbac_expanded_*`, `_rbac_denied_permissions_*`, `acm_<hub>_rbac_validation`) is re-published via templated `set_fact` keys; 401 fail-closed and non-403 unexpected-error CRD discovery tasks preserved with hub-templated, render-identical messages. Red-first: 8 contract tests (5 new/updated parity, 3 rewritten resilience) failed before the refactor; `test_rbac_bootstrap_contracts.py` old-hub-finalization contract updated to follow the new chain (deviation from plan's test list, recorded here). No Python changes; `lib/rbac_validator.py` untouched. Verification: `git diff --check` clean; targeted `test_preflight_parity.py` + `test_ansible_resilience_contracts.py` passed (54); collection unit suite passed (822); fixture-driven `ansible-playbook` integration `test_preflight_role.py` + `test_restore_only_role.py` passed (8, including `test_preflight_rbac_failure_still_reports_backup_findings` exercising both hub-loop iterations end-to-end); root RBAC/parity suites (`test_rbac_validator.py`, `test_check_rbac.py`, `test_rbac_collection_parity.py`, `test_rbac_integration.py`, `tests/release/checks/test_rbac_certification.py`) passed (183); full `./run_tests.sh` passed (exit 0; compile/unit/release-framework/quality/security lanes all green). PR #149 merged into `ansible` on 2026-07-08 at merge commit `79b1d92f516bfb45a5c18ff54d554044a6e80f15`; PR43 was created from that updated `origin/ansible` base. |
| 40 | merged | `refactor/thermos-40-restore-wait-dedup` | `.claude/worktrees/thermos-40-restore-wait-dedup` | R2-M3, existing M2 | https://github.com/tomazb/rh-acm-switchover/pull/145 | Design spec `docs/superpowers/specs/2026-07-03-pr40-restore-wait-dedup-design.md`; implementation plan `docs/superpowers/plans/2026-07-03-pr40-restore-wait-dedup.md`. `lib/waiter.py` now owns `wait_for_restore_deletion(client, restore_name, *, dry_run, timeout, where, logger)`; `activation._wait_for_restore_deletion` and `finalization._wait_for_primary_restore_deletion` are one-line delegates preserving their patch seams, historical dry-run flag sources, and the `" on primary"` message suffix byte-for-byte. Red-first: 4 waiter unit tests (absent, poll-until-absent, timeout FatalError with suffix, dry-run skip). Two existing tests that pinned the wait through `modules.<mod>.wait_for_condition` now patch `lib.waiter.wait_for_condition` for the moved poll (recorded in the spec seam note). Verification: `python -m pytest tests/test_waiter.py tests/test_activation.py tests/test_finalization.py -q` passed (15+59+87); full `./run_tests.sh` passed (root lane `1567 passed, 105 deselected`; release lane `1029 passed, 3 skipped`); touched-file `black`/`isort` applied; `git diff --check` passed. |
| 41 | merged | `refactor/thermos-41-summary-path-dedup` | `.claude/worktrees/thermos-41-summary-path-dedup` | R2-M5 | https://github.com/tomazb/rh-acm-switchover/pull/146 | Design spec `docs/superpowers/specs/2026-07-04-pr41-summary-path-dedup-design.md`; implementation plan `docs/superpowers/plans/2026-07-04-pr41-summary-path-dedup.md`. Added the collection's first filter plugin (`plugins/filter/paths.py`, `tomazb.acm_switchover.acm_abs_path(path, base_dir)`) and converted the four `Resolve summary path to absolute` sites (discovery/decommission/rbac_bootstrap roles, `argocd_manage_test.yml`) to it; PWD lookup, task names, and `when:` conditions untouched; resolution byte-identical including the no-normalization concatenation semantics (pinned by unit test). Red-first: 5 filter unit tests + a 4-site contract test banning the inline expression. Verification: collection unit suite `818 passed`; full `./run_tests.sh` passed (root lane `1563 passed, 105 deselected`; release lane `1034 passed, 3 skipped`); touched-file `black`/`isort` applied; `git diff --check` passed. |
| 42 | merged | `fix/thermos-42-activation-resume-staleness` | `.claude/worktrees/thermos-42-activation-resume-staleness` | R2-M2 | https://github.com/tomazb/rh-acm-switchover/pull/147 | Design spec `docs/superpowers/specs/2026-07-04-pr42-activation-resume-staleness-design.md` (records the approach decision: call-scoped re-validation chosen over step-semantics changes or verification timestamps); implementation plan `docs/superpowers/plans/2026-07-04-pr42-activation-resume-staleness.md`. Extracted `_assert_passive_restore_ready(restore, restore_name)` from `_verify_passive_sync` (log/error strings verbatim) and asserted it on entry of both activation paths: after the already-applied early return on the patch path (patched restores legitimately transition phases) and before snapshot/delete on the Option-B path (never destroy a passive restore that is not activation-ready). Crash-resume after the verify checkpoint now fails closed with `Passive sync restore not ready: ...` instead of activating against a degraded restore; read-only before any mutation; no checkpoint-semantics changes; collection parity gap ruled out in the spec (the activation role re-plans from a fresh `acm_restore_info` run on every invocation). Red-first: 3 new tests (patch path fail-closed, already-applied exemption, Option-B fail-closed with delete/create never called); 4 existing Option-B fixtures gained the ready phase the guard now demands. Verification: `python -m pytest tests/test_activation.py -q` passed (`62 passed`); full `./run_tests.sh` passed (root lane `1572 passed, 105 deselected`; release lane `1034 passed, 3 skipped`); touched-file `black`/`isort` applied; `git diff --check` passed. PR #147 merged into `ansible` 2026-07-05. |
| 43 | ready_for_review | `chore/thermos-43-low-severity-cleanup` | `.worktrees/thermos-43-low-severity-cleanup` | R2-L3, R2-L4, R2-L5, R2-L7 partial, R2-L9; deferred/split: R2-L1, R2-L6, R2-L7 observability/Helm/RBAC/bootstrap subitems, R2-L8; excluded: R2-L2 | https://github.com/tomazb/rh-acm-switchover/pull/151 | Base `origin/ansible` at PR #149 merge commit `79b1d92f516bfb45a5c18ff54d554044a6e80f15`. Design spec `docs/superpowers/specs/2026-07-08-pr43-low-severity-cleanup-design.md`; implementation plan `docs/superpowers/plans/2026-07-08-pr43-low-severity-cleanup.md`. Included only local behavior-preserving cleanup: bounded waiter/decommission log detail, post-parse CLI required-argument validation, documented/tested klusterlet structured failure contract, Argo CD resume checkpoint guard dedup, and release `StreamResult.to_dict()` cleanup. Validation polish restored bare Jinja truthiness for Argo CD resume `checkpoint.enabled`, added conditionally-required CLI help wording without parser behavior changes, strengthened the Argo CD resume guard test to require the exact checkpoint task-name set, and left V4 as non-actionable cosmetic. No protected files, RBAC permissions/manifests/Helm, live/lab release certification behavior, R2-L2, deferred/split low-severity items, Python H3 decomposition, report schema, or fail-closed/check-mode/idempotence changes. Verification: `python -m pytest tests/test_waiter.py tests/test_decommission.py -q` passed (`57 passed`); `python -m pytest tests/test_main.py tests/test_validation.py -q` passed (`199 passed`); `python -m pytest tests/test_post_activation.py ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_klusterlet_modules.py ansible_collections/tomazb/acm_switchover/tests/unit/test_klusterlet_remediation.py ansible_collections/tomazb/acm_switchover/tests/unit/test_post_activation_regressions.py -q` passed (`192 passed`); `python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/test_argocd_hub_parameterization.py ansible_collections/tomazb/acm_switchover/tests/unit/test_ansible_resilience_contracts.py ansible_collections/tomazb/acm_switchover/tests/unit/test_argocd_resume_on_failure.py -q` passed (`86 passed`); `python -m pytest tests/release/test_orchestrator.py tests/release/test_release_certification.py -q` passed (`32 passed, 1 skipped`); `python -m pytest tests/test_documentation_guardrails.py -q` passed (`60 passed`); `python -m pytest tests/test_waiter.py tests/test_kube_client.py -q` passed (`114 passed`); `git diff --check` passed; `./run_tests.sh` passed (`1593 passed, 105 deselected`; release lane `1035 passed, 3 skipped`; Black/isort/MyPy/Bandit/pip-audit/compile clean). CodeRabbit `coderabbit review --agent -t uncommitted --base origin/ansible` reported 0 findings. |
| 44 | merged | `refactor/thermos-44-release-orchestrator-shortcircuit` | `.claude/worktrees/thermos-44-release-orchestrator-shortcircuit` | R2-H4 | https://github.com/tomazb/rh-acm-switchover/pull/132 | Design spec `docs/superpowers/specs/2026-07-03-pr44-orchestrator-shortcircuit-design.md`; implementation plan `docs/superpowers/plans/2026-07-03-pr44-orchestrator-shortcircuit.md`. Extracted module-level `_short_circuit_finalize(...)` owning the `not_applicable` runtime-parity/final-baseline artifact pair and the `_finalize_run` delegation; the three abort paths in `_run_release_certification` (matrix-validation blocked, required static-gates failure, stop-before-mutation) collapse to single calls with only their `mandatory_argocd` expression varying. Behavior-preserving; guarded by the existing short-circuit characterization tests plus a new red-first direct helper unit test. Verification: `python -m pytest tests/release/test_orchestrator.py tests/release/test_release_certification.py -q` passed (`30 passed, 1 skipped`); full `./run_tests.sh` passed (root lane `1563 passed, 105 deselected`; release lane `1022 passed, 3 skipped`); touched-file `black`/`isort` applied; `git diff --check` passed. PR #132 merged into `ansible` 2026-07-03. |
| 45 | merged | `refactor/thermos-45-release-orchestrator-rbac-dedup` | `.claude/worktrees/thermos-45-release-orchestrator-rbac-dedup` | R2-M7 | https://github.com/tomazb/rh-acm-switchover/pull/133 | Design spec `docs/superpowers/specs/2026-07-03-pr45-orchestrator-rbac-dedup-design.md`; implementation plan `docs/superpowers/plans/2026-07-03-pr45-orchestrator-rbac-dedup.md`. Extracted `_certify_hub_rbac(...)` (scope lookup -> `certify_rbac_permissions` -> `hub:name`-prefixed assertion dicts) and replaced the duplicated primary/secondary blocks in `_run_release_certification` with a loop over `("primary", "secondary")` plus equivalent `all`/`any` status aggregation. Behavior-preserving; guarded by existing live-RBAC characterization tests plus a new red-first direct helper unit test. Verification: `python -m pytest tests/release/test_orchestrator.py tests/release/test_release_certification.py -q` passed; full `./run_tests.sh` passed (root lane `1563 passed, 105 deselected`; release lane `1022 passed, 3 skipped`; Flake8/Black/isort/MyPy/Bandit/pip-audit clean); `git diff --check` passed. Rebased onto PR #132's short-circuit helper after resolving the adjacent release orchestrator conflict. |
| 46 | merged | `refactor/thermos-46-rbac-certification-dedup` | `.claude/worktrees/thermos-46-rbac-certification-dedup` | R2-M8 | https://github.com/tomazb/rh-acm-switchover/pull/134 | Design spec `docs/superpowers/specs/2026-07-03-pr46-rbac-certification-dedup-design.md`; implementation plan `docs/superpowers/plans/2026-07-03-pr46-rbac-certification-dedup.md`. Extracted `_evaluate_permissions(..., expect_allowed: bool)` returning `(assertions, unexpected_count, error_count)`; `certify_rbac_permissions` now calls it once for required permissions and once for forbidden permissions, with expected/actual/message strings derived from the polarity so emitted `CertificationAssertion`s are byte-identical to before. Red-first 6-case polarity-matrix unit test (allowed/denied/error × both polarities); guarded by the existing certification suite. Verification: `python -m pytest tests/release/checks/test_rbac_certification.py tests/release/test_orchestrator.py -q` passed (`62 passed`); full `./run_tests.sh` passed (root lane `1563 passed, 105 deselected`; release lane `1027 passed, 3 skipped`; Flake8/Black/isort/MyPy/Bandit/pip-audit clean); `git diff --check` passed. |
| 47 | merged | `refactor/thermos-47-release-adapter-dedup` | `.claude/worktrees/thermos-47-release-adapter-dedup` | R2-M6 | https://github.com/tomazb/rh-acm-switchover/pull/135 | Design spec `docs/superpowers/specs/2026-07-03-pr47-release-adapter-dedup-design.md`; implementation plan `docs/superpowers/plans/2026-07-03-pr47-release-adapter-dedup.md`. Added `run_stream_subprocess(...)` to `adapters/common.py` owning the mkdir → `subprocess.run` → timeout/normal branches → `write_capture_artifact` pair → exit-code + redaction assertions → `StreamResult` flow; each adapter's `execute()` now builds its command/env and passes stream name, capability, message strings, and a reports callable. Duplicated `_now`/`_decode` moved to `common.py`; per-adapter variance (bash `bash-` capability prefix, bash inherit-env-when-no-extra-env, exact message strings) preserved byte-for-byte per the spec's variance table. Red-first: 4 direct helper tests (success/failure/timeout/reports) in `test_common.py`; the existing adapter suites (asserting on `StreamResult` fields) guard integrated behavior. Verification: `python -m pytest tests/release/adapters/ tests/release/test_orchestrator.py -q` passed (`90 passed`); full `./run_tests.sh` passed (root lane `1563 passed, 105 deselected`; release lane `1026 passed, 3 skipped`); touched-file `black`/`isort` applied, no new flake8 findings; `git diff --check` passed. |

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
- Keep `.worktrees/` ignored by flake8 so advisory style checks do not scan nested worktrees.

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
- Keep Thermos implementation changes isolated in `.worktrees/thermos-*` worktrees and their corresponding branches.
