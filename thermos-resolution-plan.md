# Thermos Ansible Review Resolution Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:using-git-worktrees` before starting each implementation PR. Use `superpowers:executing-plans` or `superpowers:subagent-driven-development` to execute each PR slice task-by-task. Update this file in every Thermos PR.

**Goal:** Resolve the validated findings captured from the operator-supplied external Thermos Ansible review through isolated, reviewable branches without parity drift between the Python CLI and the Ansible collection.

**Architecture:** Treat the external report as a hypothesis source, not an authority. The original report may exist locally as an untracked `thermos_ansible_review.md`, but it is not required in a fresh checkout; this tracker is the self-contained resolution source. Every finding must stay tied to source evidence, tests, and documentation changes. Each PR uses a dedicated worktree and branch, updates this tracker, and preserves the dual-supported parity contract unless explicit operator approval records an intentional divergence.

**Tech Stack:** Python CLI, Ansible collection roles/playbooks/modules, pytest, GitHub PRs, `.worktrees/` git worktrees.

---

## State Tracking Rules

- Status values: `planned`, `in_progress`, `ready_for_review`, `merged`, `blocked`, `deferred`.
- A PR branch may mark only its own row `in_progress` or `ready_for_review`.
- Mark a row `merged` only after the PR has merged into `ansible` and the next branch is created from the updated base.
- Keep one branch and one worktree per PR slice.
- Update the `Last Updated` field whenever this tracker changes.
- Do not modify protected runbook files or `.claude/skills/**/*.skill.md` without explicit operator approval.
- Do not intentionally change Python/Ansible parity status without explicit operator approval and repo documentation updates.

**Last Updated:** 2026-05-30

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
| F26 | confirmed coverage gap | PR 08 | Root-to-collection bundled RBAC manifest parity has only narrow coverage today. |
| F27 | confirmed test naming issue | PR 06 | A post-activation integration test name claims pending-cluster failure while asserting dry-run skip. |
| F28 | confirmed | PR 03 | Collection klusterlet probe treats broad API exceptions as skipped instead of failed. |

## PR Sequence

| PR | Status | Branch | Worktree | Findings | PR URL | Verification |
| --- | --- | --- | --- | --- | --- | --- |
| 01 | merged | `docs/thermos-resolution-tracking` | `.worktrees/thermos-01-tracking` | tracker + agent instructions | https://github.com/tomazb/rh-acm-switchover/pull/72 | `python -m pytest tests/test_documentation_guardrails.py -q` passed; `git diff --check` passed; CI passed |
| 02 | ready_for_review | `fix/thermos-restore-only-guard` | `.worktrees/thermos-02-restore-only` | F1 | unopened | `python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/ -q` passed; `python -m pytest ansible_collections/tomazb/acm_switchover/tests/integration/test_switchover_roles.py -q` passed; `git diff --check` passed |
| 03 | planned | `fix/thermos-klusterlet-fail-closed` | `.worktrees/thermos-03-klusterlet` | F2, F3, F28 | unopened | Python post-activation tests + collection klusterlet module tests |
| 04 | planned | `fix/thermos-hub-identity-resume` | `.worktrees/thermos-04-hub-identity` | F5, F6 | unopened | `tests/test_main.py`, `tests/test_utils.py` |
| 05 | planned | `fix/thermos-report-checkpoint-identity` | `.worktrees/thermos-05-identity-hygiene` | F9, F16 | unopened | report artifact tests + checkpoint action tests |
| 06 | planned | `fix/thermos-finalization-refresh-tests` | `.worktrees/thermos-06-finalization-refresh` | F7, F10, F27 | unopened | finalization discovery tests + integration fixtures |
| 07 | planned | `docs/thermos-rbac-operator-guidance` | `.worktrees/thermos-07-rbac-docs` | F4, F11, F24, F25 | unopened | documentation guardrails + link checks |
| 08 | planned | `fix/thermos-rbac-managed-cluster-parity` | `.worktrees/thermos-08-rbac-parity` | F8, F26 | unopened | RBAC parity, validator, and manifest parity tests |
| 09 | planned | `fix/thermos-ansible-surface-cleanup` | `.worktrees/thermos-09-ansible-cleanup` | F17, F18, F21, F23 | unopened | module tests, stale-task contract tests, Helm/static tests |
| 10 | planned | `fix/thermos-preflight-complete-reporting` | `.worktrees/thermos-10-preflight-reporting` | F22 | unopened | preflight fixture with RBAC and backup failures |
| 11 | planned | `refactor/thermos-shared-ansible-logic` | `.worktrees/thermos-11-shared-logic` | F19, F20 | unopened | activation/post-activation role tests |
| 12 | deferred | `refactor/thermos-maintainability` | `.worktrees/thermos-12-maintainability` | F12, F13, F14, F15 | unopened | targeted tests first, then `./run_tests.sh` |

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

**Likely Work Items**
- Extract shared flow helpers from `acm_switchover.py`.
- Extract Python klusterlet verifier internals after PR 03.
- Split `validate_backups.yml` or move complex logic into tested module utilities.
- Split `tests/test_main.py` fixtures and classes.

**Acceptance Criteria**
- Refactors are behavior-preserving.
- Targeted tests pass after each sub-slice.
- Full strict suite runs before final merge.

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
