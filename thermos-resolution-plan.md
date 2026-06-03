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

**Last Updated:** 2026-06-03

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
| F31 | confirmed hardening | PR 18 | Safe path validation and report artifact sanitization remain duplicated across Python and collection helpers. These use different path bases and have incomplete adversarial parity coverage. |
| F32 | confirmed hardening | PR 15 | `scripts/setup-rbac.sh` creates token-bearing kubeconfigs with the process umask and only narrows permissions afterward. |
| F33 | confirmed hardening | PR 16 | `container-bootstrap/Containerfile` uses mutable base image tags and downloads `jq`/OpenShift client binaries without checksum verification. |
| F34 | confirmed robustness | PR 17 | Python and collection klusterlet remediation delete `bootstrap-hub-kubeconfig` before recreating it. PR 17 preserves parity by switching both implementations and managed-cluster RBAC/docs from delete-then-create to patch/create. |
| F35 | confirmed | PR 19 | Operator decision: Helm `customValidatorRules` must preserve the validator `ClusterRole` read-only boundary by allowing only `get`, `list`, and `watch`; no escape hatch. |
| F36 | confirmed hardening | PR 15 | Service-account token generation defaults to `48h`; reduce the default or require explicit opt-in for longer-lived tokens. |
| F37 | confirmed | PR 20 | Standalone collection Argo CD resume reloads a checkpoint run ID without validating checkpoint operation identity against live hub UIDs before mutating Applications. |
| F38 | confirmed | PR 21 | Python klusterlet API/client failures are still conflated with non-fatal unreachable/skipped states and can fail open during post-activation verification. |

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
| 20 | in_progress | `thermos-20-argocd-resume-identity` | `.worktrees/thermos-20-argocd-resume-identity` | F37 | pending | Red/green checkpoint identity module tests passed locally; final verification pending. |
| 21 | planned | `thermos-21-python-klusterlet-fail-closed` | `.worktrees/thermos-21-python-klusterlet-fail-closed` | F38 | pending | Start from updated `ansible` after PR 20 merges. |

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
