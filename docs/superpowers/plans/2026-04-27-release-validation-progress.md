# Release Validation Framework Progress Tracker

Date: 2026-04-27
Source spec: `docs/superpowers/specs/2026-04-23-release-validation-framework-design.md`
Purpose: Track planning and implementation state across the release validation framework plan set.

## Status Vocabulary

- `not-started`: No implementation work has begun.
- `ready`: The plan is detailed enough for implementation.
- `in-progress`: Implementation has started.
- `blocked`: Work is paused on a concrete blocker.
- `implemented`: Code/docs for the plan have been written.
- `verified`: The plan's verification commands have run successfully and evidence is recorded below.

## Plan Set

| Plan | File | Depends on | Status | Owner/session | Branch or commit | Verification command | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 01 | `docs/superpowers/plans/2026-04-27-release-validation-01-profile-contract-foundation.md` | Source spec | `verified` | session `800ac011-0e47-49a5-a2b8-4ece8eb426fd` | `release-validation-01-profile-contract-foundation` | `python -m pytest tests/release/contracts -q` | Implemented in `.worktrees/release-validation-01`; `tests/release -q` currently exercises contract tests only, so lifecycle collection gating remains for Plan 02. |
| 02 | `docs/superpowers/plans/2026-04-27-release-validation-02-release-selection-static-gates.md` | Plan 01 | `verified` | session `33c94cb1-91f8-43b0-a56d-0766108c05e4` | `ansible` (commits `17d84ff`–`1534f49`) | `python -m pytest tests/release -k "options or catalog or static_gates or metadata or artifact_reuse" -q` | 25 passed, 1 skipped (lifecycle test). Fixed skip guard to use `iter_markers("release")` instead of keyword path matching. |
| 03 | `docs/superpowers/plans/2026-04-27-release-validation-03-artifacts-redaction.md` | Plans 01-02 | `verified` | session `18ba9995-087e-4d47-b915-892c5e290a84` | `ansible` (commits `f7af279`–`7961692`) | `python -m pytest tests/release/reporting -q` | 17 passed. Fixed plan defect: `post_failure` added to `LIST_FIELDS`. Used typed `ArtifactsProfile.root` in conftest fixture. write_sanitized_text now auto-updates redaction.json audit log. |
| 04 | `docs/superpowers/plans/2026-04-27-release-validation-04-lab-readiness-baseline-discovery.md` | Plans 01-03 | `ready` |  |  | `python -m pytest tests/release/baseline tests/release/checks -q` | Adds mocked discovery, readiness, environment fingerprints, and baseline assertions. |
| 05 | `docs/superpowers/plans/2026-04-27-release-validation-05-python-stream-adapter.md` | Plans 01-04 | `ready` |  |  | `python -m pytest tests/release/adapters/test_python_cli.py -q` | Adds Python CLI stream adapter and scenario result capture. |
| 06 | `docs/superpowers/plans/2026-04-27-release-validation-06-ansible-stream-adapter-schema-stability.md` | Plans 01-05 | `ready` |  |  | `python -m pytest tests/release/adapters/test_ansible.py ansible_collections/tomazb/acm_switchover/tests/unit/test_release_source_schema.py -q` | Adds Ansible adapter and source schema stability tests. |
| 07 | `docs/superpowers/plans/2026-04-27-release-validation-07-runtime-parity-normalizers.md` | Plans 01-06 | `ready` |  |  | `python -m pytest tests/release/scenarios/test_runtime_parity.py -q` | Adds Python/Ansible runtime parity normalization and comparison artifacts. |
| 08 | `docs/superpowers/plans/2026-04-27-release-validation-08-bash-recovery-soak-controls.md` | Plans 01-07 | `ready` |  |  | `python -m pytest tests/release/adapters/test_bash.py tests/release/baseline/test_recovery.py tests/release/scenarios/test_soak.py -q` | Adds Bash adapter, bounded recovery, recovery budget, and soak aggregation. |
| 09 | `docs/superpowers/plans/2026-04-27-release-validation-09-summary-reporting-operator-docs.md` | Plans 01-08 | `ready` |  |  | `python -m pytest tests/release/reporting/test_summary.py tests/release/reporting/test_render.py -q` | Adds summary aggregation, report rendering, and operator documentation. |

## Cross-Plan Invariants

- Release certification tests must not run from a normal `pytest tests/` invocation unless `--release-profile` or `ACM_RELEASE_PROFILE` is set.
- No protected runbook or `.claude/skills/**/*.skill.md` files may be edited by these plans.
- No intentional parity divergence may be introduced. Any dual-supported capability touched by release logic must compare Python and Ansible behavior or record profile-backed `not_applicable`.
- Profiles, adapters, and tests must not hardcode lab context names, managed-cluster names, private namespaces, or artifact directories outside example profile data.
- Profile files must never embed kubeconfig contents, tokens, certificates, or private keys.
- Artifact persistence must pass through redaction before command output, source reports, rendered reports, or snapshots are referenced by required JSON artifacts.
- Bash may be certified as an operator surface, but V1 parity comparisons are Python/Ansible only unless a later plan adds a structured Bash comparison contract.
- Every plan should update this tracker after task groups, verification runs, blockers, and commits.

## Handoff Protocol

1. Before starting a plan, set its status to `in-progress`, record the branch or working session, and confirm its dependencies are at least `implemented`.
2. After each task group, add the commit SHA or local checkpoint and summarize any deviation in the `Notes` column.
3. After verification, paste the exact command and outcome in the plan file and set status to `verified`.
4. If a blocker is found, set status to `blocked`, record the failing command or missing decision, and do not continue dependent plans until the blocker is resolved.

## Verification Log

| Date | Plan | Command | Result | Notes |
| --- | --- | --- | --- | --- |
| 2026-04-27 | planning package | `python - <<'PY' ... PY` using runtime-built rejected phrases | Passed | No rejected planning phrases found. |
| 2026-04-27 | planning package | Structural header check for nine plan files and progress tracker | Passed | All nine plan files include required headers, file maps, and final verification sections. |
| 2026-04-27 | 01 | `python -m pytest tests/release/contracts -q` | Passed | `11 passed` after adding the profile contract package, loader/schema validation, defaults, and example profiles. |
| 2026-04-27 | 01 | `python -m pytest tests/release -q` | Passed | `11 passed`; release lifecycle guard behavior is still pending Plan 02 because only contract tests exist under `tests/release/`. |
| 2026-04-27 | 01 | `python -m pytest tests/ -q` | Passed | Full repository suite stayed green with `1215 passed, 26 skipped`. |
| 2026-04-27 | 02 | `python -m pytest tests/release -k "options or catalog or static_gates or metadata or artifact_reuse" -q` | Passed | `14 passed` for new tests; `25 passed, 1 skipped` for full `tests/release` run. |
| 2026-04-27 | 02 | `python -m pytest tests/ -m "not e2e and not release" -q` | Passed | `1150 passed, 106 deselected`; release lifecycle test does not appear in un-gated run. |
| 2026-04-27 | 03 | `python -m pytest tests/release/reporting -q` | Passed | `17 passed` (after review fixes). Auto-update of redaction.json, docstrings, line-length, return-type all addressed. |
| 2026-04-27 | 03 | `python -m pytest tests/ -q` | Passed | `1248 passed, 27 skipped` — no regressions in full suite. |
