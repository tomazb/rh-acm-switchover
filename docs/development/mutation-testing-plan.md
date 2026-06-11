# Mutation Testing Design Handoff

> Status: deferred design handoff. This is not an implementation plan and it is
> not approval to add mutation testing to the normal developer or CI path.

This document captures why mutation testing is worth considering for
`rh-acm-switchover`, what an approved design/spec should decide, and how a later
implementation should keep mutation testing scoped, parity-aware, and useful.
Implementation remains deferred until the current Thermos PR sequence is complete
or explicitly paused.

When the start conditions at the end of this document are met, use this handoff
with a fresh Superpowers workflow:

1. Use `superpowers:brainstorming` to write and approve a design/spec.
2. Use `superpowers:writing-plans` to turn the approved design into an implementation plan.
3. Use `superpowers:executing-plans` or `superpowers:subagent-driven-development` only after the design and plan are approved.
4. Use `.claude/skills/mutation-testing/SKILL.md` as the repo-local skill for target selection, safe runs, survivor triage, and baseline reporting.

## Why Mutation Testing

Line coverage tells us which code ran during tests, not whether tests would catch
a behavior change. Mutation testing fills that gap: a tool introduces small
source changes, such as flipped comparisons, removed statements, changed
constants, or swapped booleans, and then re-runs relevant tests. A mutant that
survives marks behavior that the current tests do not actually assert.

For this repo, surviving mutants are useful when they point at missing negative
coverage in safety-sensitive paths. The highest-value examples match the project
review guidelines:

- wrong cluster, hub, namespace, Kubernetes context, or managed resource mutation
- RBAC denial and permission-scope failures
- checkpoint, resume, and hub identity binding failures
- destructive operation confirmation and dry-run/check-mode behavior
- Argo CD pause/resume behavior that might affect the wrong Application
- timeout, polling, or wait logic that can silently ignore failure
- Python CLI and Ansible collection behavior drift where parity is claimed

Mutation testing is not a substitute for existing unit, parity, release, or E2E
tests. It is a diagnostic tool for finding weak assertions.

## Non-Goals And Boundaries

Any future implementation should keep mutation testing off the normal developer
critical path unless a later design explicitly changes this after measured
runtime data.

- Do not add mutation testing to `./run_tests.sh`.
- Do not make it a required per-PR gate at the start.
- Do not run mutation testing against live-cluster E2E suites.
- Do not apply mutants to disk unless the checkout is clean and the operator
  explicitly asks to inspect a selected mutant locally.
- Run it one target module, function group, or behavior slice at a time.
- Treat first results as diagnostic baseline data, not immediate failure criteria.
- Preserve the dual-supported parity contract: survivors in shared behavior must
  be triaged against both the Python CLI and the Ansible collection.

## Candidate Tooling Inputs

`mutmut` remains the leading candidate because it fits the repo's pytest-based
stack and has a simpler local workflow than heavier distributed mutation tools.
The previously explored version range, `mutmut>=2.5,<3`, is now only a historical
input. Revalidate tooling before implementation.

As of 2026-06-08, the candidate split is:

- Prefer a current `mutmut` 3.x pin for the first approved implementation if the
  spike confirms that function-level mutation is enough for the initial targets.
- Keep `mutmut` 2.5.x as a fallback only if the approved design needs mutation of
  code outside functions; upstream documentation says `mutmut` 3+ has a different
  execution model and points users to `mutmut` 2 for code outside functions.
- Pin only in `requirements-dev.txt`; do not add mutation tooling to runtime dependencies.
- Keep mutation caches and reports out of tracked source files.
- Prefer a thin repo wrapper over requiring contributors to remember raw tool flags.
- If a future design chooses another tool, update this document or replace it with
  the approved design/spec.

Tool facts to re-check during the design/spec:

- current `mutmut` release and Python support on PyPI
- `source_paths`, `pytest_add_cli_args_test_selection`, `also_copy`,
  `only_mutate`, `do_not_mutate`, `mutate_only_covered_lines`, and
  `max_stack_depth` behavior in the installed version
- command behavior for focused module/function runs, `browse`, `show`, and any
  machine-readable output used by CI artifacts
- behavior on Linux runners and developer machines with fork support

## Candidate Repo Integration Shape

The first implementation PR should be small and reversible. It should add local
mutation-testing capability without changing normal verification behavior.

Candidate file changes:

| Area | Candidate change | Notes |
| --- | --- | --- |
| Dependency | add pinned `mutmut` candidate to `requirements-dev.txt` | dev dependency only |
| Config | add a minimal `[mutmut]` section to `setup.cfg` only after a spike | keep source/test scope explicit |
| Wrapper | add `tools/run_mutation_tests.py` or `scripts/run_mutation_tests.sh` | wrapper should validate inputs and print the exact underlying command |
| Ignore rules | add mutation cache/report paths to `.gitignore` | expected candidates: `mutants/`, `.mutmut-cache/`, `mutation-reports/` |
| Docs | update `docs/development/testing.md` with an on-demand mutation section | keep separate from `./run_tests.sh` |
| CI | optional report-only workflow after local baseline | use `workflow_dispatch` first; scheduled later if useful |

Do not add thresholds, badges, or required checks in the first implementation PR.

## Candidate Safety Requirements

The future wrapper or workflow should fail early before it mutates the wrong
files or creates noisy repo state.

- Assert the expected mutation-tool version before running.
- Require explicit source and test targets for normal runs.
- Refuse to run when tracked source or test files involved in the target are dirty,
  unless an explicit local-only override such as `--allow-dirty` is provided.
- Default diff-only runs to the active base branch rather than hard-coding
  `origin/main`; prefer `GITHUB_BASE_REF`, then current upstream, then `origin/ansible`.
- Run an unmutated targeted pytest command first and stop if it fails.
- Refuse live E2E markers and commands that require real cluster contexts.
- Record the exact commit, tool version, source target, test target, and command in
  every baseline artifact.
- Keep scheduled CI report-only at first.

## Candidate Phase Targets

These targets are hypotheses for the future design/spec. Re-check them after the
Thermos queue finishes because source layout, test coverage, and parity mappings
may change.

| Phase | Candidate source targets | Candidate test focus |
| --- | --- | --- |
| 0 | one narrow spike target from Phase 1 | validate tool version, config, wrapper UX, runtime, output, and noise |
| 1 | `lib/validation.py` | `tests/test_validation.py`, `tests/test_validation_parity.py`, collection validation parity fixtures |
| 1 | `lib/rbac_validator.py` | `tests/test_rbac_validator.py`, `tests/test_rbac_collection_parity.py`, collection RBAC parity |
| 1 | `lib/utils.py` | `tests/test_utils.py`, checkpoint/resume tests, hub identity binding parity |
| 1 | `modules/decommission.py` | `tests/test_decommission.py`, destructive-operation safety, dry-run behavior, collection decommission contracts |
| 1 | `modules/activation.py` | `tests/test_activation.py`, passive/full activation waits, stale restore handling, collection activation parity |
| 2 | remaining `lib/` and `modules/` | preflight, finalization, primary prep, post-activation, Argo CD, waiter behavior |
| 3 | collection `plugins/module_utils/` and `plugins/modules/` | validation, checkpoint, GitOps, klusterlet, result, report, and module contracts |

For dual-supported behavior, a survivor should not be closed by adding Python-only
coverage if the same operator-facing behavior exists in the collection. Either add
or confirm matching collection/parity coverage, or record an approved parity
divergence through the existing parity process.

## Candidate Reporting And Baseline Model

The first useful output is a baseline of surviving mutants by target area. A later
implementation should define the exact artifact format rather than assuming one.

Candidate baseline fields:

- commit SHA and branch
- tool name and version
- source target and test target
- exact wrapper command and underlying mutation command
- unmutated pytest command and result
- mutant counts by status: killed, survived, timeout, suspicious, skipped, equivalent
- top surviving mutants by operational risk
- parity impact classification for each high-value survivor
- next action: add assertion, add parity coverage, mark equivalent with reason, or defer

Candidate outputs:

- text summary from the mutation tool
- `mutmut show <id>` output for selected survivors
- HTML report for manual inspection if available in the chosen tool/version
- optional generated JSON summary if scheduled CI needs stable artifacts
- optional JUnit/XML only if the chosen tool or wrapper can generate it without fragile parsing

Do not introduce per-module score thresholds until a module has a reviewed
baseline and high-value survivors have been triaged. Thresholds should be a
ratchet: start with Phase 1 targets only, then raise expectations as survivors are
killed or explicitly excluded.

Equivalent or intentional mutants need an auditable reason. Use a tool-supported
exclusion such as `# pragma: no mutate` or config exclusion only when the mutant
is genuinely equivalent or not operationally meaningful. Prefer targeted
exclusions near the source over broad config suppression.

## Phase 2 Baseline Record: `modules/primary_prep.py`

- Date: 2026-06-11
- Source target: `modules/primary_prep.py`
- Focused Python test target: `tests/test_primary_prep.py`
- Provenance:
  - base branch: `ansible`
  - baseline branch: `mutation/primary-prep-baseline`
  - baseline commit: `f1625c3876028aa9f3d99d475459650d6c3818eb`
  - worktree: `/home/tomaz/sources/rh-acm-switchover/.worktrees/mutation-primary-prep-baseline`

### Unmutated baseline lanes

- Python baseline: `source .venv/bin/activate && python -m pytest tests/test_primary_prep.py -q` — PASS (`39 passed`)
- Collection unit/contracts lane: `source .venv/bin/activate && PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/test_primary_prep_auto_import.py ansible_collections/tomazb/acm_switchover/tests/unit/test_backup_schedule_persistence.py -q` — PASS (`10 passed`)
- Collection integration/scenario lane: `source .venv/bin/activate && PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/integration/test_switchover_roles.py -k primary_prep -q` — PASS (`2 passed, 8 deselected`)

### Mutation baseline

- Tool/version: `mutmut 3.6.0`
- Config target: `setup.cfg` `[mutmut]` with `source_paths = modules/primary_prep.py` and `pytest_add_cli_args_test_selection = tests/test_primary_prep.py`
- Baseline command: `source .venv/bin/activate && rm -rf mutants/ && mutmut run`
- Inspection commands: `source .venv/bin/activate && mutmut results | head -40`; `source .venv/bin/activate && mutmut show <id>`
- Counts: total `249`, killed `140`, survived `109`, not_checked `0`
- Additional status buckets: none (`{}`)
- Survivor-heavy functions:
  - `_pause_backup_schedule`: `70`
  - `_scale_down_thanos_compactor`: `15`
  - `prepare`: `10`
  - `_disable_auto_import`: `8`
  - `_pause_argocd_acm_apps`: `5`
  - `__init__`: `1`

### High-value survivor classification

| Survivor group | Risk area | Collection evidence | Class | Baseline note |
| --- | --- | --- | --- | --- |
| `_pause_backup_schedule__mutmut_2`, `3`, `10`, `73`, `78`, `84` | BackupSchedule wrong-resource targeting (`group`, `version`, `namespace`) on list/patch calls | `roles/primary_prep/tasks/pause_backups.yml`, `tests/unit/plugins/modules/test_acm_backup_schedule.py`, `tests/integration/test_switchover_roles.py` | parity gap | Python tests assert patch payload and version branching, but they do not assert the exact `list_custom_resources()`, `patch_custom_resource()`, or delete-path targeting kwargs that the collection keeps explicit. |
| `_pause_backup_schedule__mutmut_50`, `51`, `52`, `53`, `64` | `saved_backup_schedule` persistence before pause/delete and on already-paused reruns | `tests/unit/test_backup_schedule_persistence.py`, `roles/primary_prep/tasks/main.yml` | parity gap | The collection treats saved schedule persistence as a checkpoint/finalization contract; Python tests do not currently assert the corresponding state writes on the already-paused or pre-mutation paths. |
| `_scale_down_thanos_compactor__mutmut_8`, `9`, `10`, `11`, `12`, `35`, `40` | Thanos pod-query selector/namespace handling, bounded wait semantics, and missing-compactor fail-closed behavior | `tests/unit/test_primary_prep_auto_import.py`, `roles/primary_prep/tasks/scale_observability.yml` | parity gap | The collection contract explicitly checks the selector, namespace, retries/delay, and fail-on-remaining-pods flow. Python tests verify the scale call and timeout raise, but not the exact pod-query targeting or the 404 mapping in a way that kills these mutants. |
| `_disable_auto_import__mutmut_4`, `8`, `10`, `23`, `25`, `42`, `43`, `44` | Disable-auto-import bookkeeping and malformed-metadata fallbacks | `tests/unit/test_primary_prep_auto_import.py`, `roles/primary_prep/tasks/manage_auto_import.yml` | incidental/noisy | No surviving mutant changed the real shared contract: annotation patching of non-local clusters and `local-cluster` exclusion remain covered. The survivors only affect log-count bookkeeping or unrealistic malformed `metadata` dict shapes. |
| `_pause_argocd_acm_apps__mutmut_10`, `11`, `12`, `13`, `14` | Argo CD success-path `run_id` resume-hint logging after pause | `roles/primary_prep/tasks/main.yml`, `tests/unit/test_argocd_resume_on_failure.py` | incidental/noisy | The collection already persists and rehydrates `argocd_run_id` for checkpoint/resume. These Python survivors only change whether the success log prints the run ID, not whether pause state is recorded. |
| `prepare__mutmut_2`, `3`, `4`, `5`, `7`, `9`, `13`, `15`, `19`, `21` | Top-level phase orchestration triage blocked by tool lookup failure | `mutmut results`, `mutants/modules/primary_prep.py.meta` | tool/runtime issue | `mutmut results` reports these survivors, but `mutmut show <id>` raised `FileNotFoundError` for the `prepare` mutant names under `mutmut 3.6.0`, so this family could not be diff-inspected in the same way as the function-level survivors above. |

### Next action recommendation

1. Prioritize the parity-gap survivors first: add Python assertions that mirror the already-green collection contracts for BackupSchedule targeting, saved-schedule persistence, and Thanos selector/wait behavior.
2. Treat the disable-auto-import and Argo CD `run_id` logging survivors as baseline noise unless a later review shows operator-visible impact beyond logging or malformed fixture shapes.
3. Resolve the `mutmut show` lookup failure for `prepare` survivors before planning follow-up work on the phase-orchestration family.

## Survivor Triage Policy

Classify each surviving mutant before changing tests:

| Classification | Meaning | Expected action |
| --- | --- | --- |
| Missing assertion | Existing test reaches behavior but does not assert the mutated outcome | strengthen the smallest relevant test |
| Missing scenario | No focused test covers the behavior | add a targeted unit, integration, or parity test |
| Parity gap | Survivor exposes behavior shared by Python and collection | add/confirm both sides, or document approved divergence |
| Equivalent | Mutant does not change observable behavior | record reason; use narrow exclusion only after review |
| Incidental/noisy | Mutant is reached only through broad incidental tests | refine test selection, stack depth, or target scope |
| Tool/runtime issue | Timeout, import isolation issue, or unsupported construct | record as tooling debt; do not treat as weak coverage |

A survivor in a safety-sensitive path should be prioritized over score improvement.
Killing one wrong-cluster, RBAC, dry-run, checkpoint, or activation-wait mutant is
more valuable than improving aggregate score on low-risk boilerplate.

## Deferred Design Questions

The future Superpowers design/spec should answer these before implementation:

- Should the first implementation target Python-only safety modules, paired
  Python/collection parity slices, or collection-local modules first?
- Should the first implementation PR add local tooling only, or also add a
  report-only `workflow_dispatch` CI job?
- Which `mutmut` major version should be pinned after the spike, and why?
- What artifact format is required for CI: text/HTML only, generated JSON, JUnit/XML,
  or a combination?
- What is the first acceptable baseline: record survivors only, or kill selected
  Phase 1 survivors before adding any scheduled workflow?
- How should the wrapper map source files to focused tests while avoiding stale or
  under-scoped test selections?
- What exact policy should apply when a survivor exposes a dual-supported parity gap?
- How should equivalent mutants be reviewed so exclusions stay narrow and auditable?

## Start Conditions For Future Work

Do not implement mutation testing from this design handoff alone. Start the real
work only when all of these are true:

- The current Thermos PR sequence is complete or explicitly paused.
- The active branch is current with the target branch used for Thermos follow-up work.
- The source/test topology is rechecked against `docs/ansible-collection/behavior-map.md`,
  `docs/ansible-collection/parity-matrix.md`, and
  `docs/ansible-collection/test-migration-catalog.md`.
- A fresh Superpowers design/spec is written, reviewed, and approved.
- A Superpowers implementation plan is written from that approved design/spec.
- The repo-local `.claude/skills/mutation-testing/SKILL.md` skill is used to keep
  the implementation and triage workflow consistent with this handoff.
