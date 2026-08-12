# Issue #246 — Contributor, Testing, and Architecture Documentation Realignment

**Date**: 2026-08-11
**Issue**: [#246](https://github.com/tomazb/rh-acm-switchover/issues/246)
**Base**: `origin/ansible` at `9cd55ac1` (the #245 `AGENTS.md` refresh)
**Status**: Design approved; ready for implementation planning

## Problem

`AGENTS.md` is now the durable policy authority (#245), but it delegates command
inventories and architecture detail to secondary documents. Three of those documents have
drifted from source, so correct top-level policy still routes contributors to obsolete
commands, a retired class name, and a pre-extraction architecture.

Every drift item below was verified against source at `9cd55ac1`, not inferred.

### Verified drift

| Document | Location | Drift | Ground truth |
| --- | --- | --- | --- |
| `CONTRIBUTING.md` | `:27` | "Maximum line length: 100 characters" | CI runs `black --line-length 120`; `setup.cfg` sets flake8 max 120 |
| `CONTRIBUTING.md` | `:70-110` | "Add method to `PreflightValidator` class" as the way to add a check | The class still exists (`modules/preflight_coordinator.py:53`, imported at `acm_switchover.py:69`), but checks now live in the `modules/preflight/` package — `backup_validators.py`, `cluster_validators.py`, `namespace_validators.py`, `version_validators.py`. The obsolete part is the recipe, not the name |
| `CONTRIBUTING.md` | `:223-233` | `--dry-run` / `--validate-only` examples omit required arguments | `--method` **and** `--old-hub-action` are both required unless `--setup` / `--restore-only` / `--argocd-resume-only` (`acm_switchover.py:85-89`); examples as written exit 2 on argument validation |
| `CONTRIBUTING.md` | `:166-188` | Teaches hand-rolled `if self.dry_run: return {}` in new call sites | Dry-run is centralised in `lib/kube_client.py` with state-snapshot and fail-closed behaviour |
| `CONTRIBUTING.md` | `:321-333` | Unmaintained feature-idea list, partly delivered | "Enhanced logging — structured JSON output" is delivered as `--log-format json`; the rest is untracked wishlist with no governing issue |
| `docs/development/testing.md` | `:236-254` | `black --line-length 120 .` and `isort … .` | `AGENTS.md:375-376` forbids repo-wide formatting that can walk `.venv/` |
| `docs/development/testing.md` | `:21` | `test_preflight.py  # Tests for modules/preflight.py` | `modules/preflight/` is a package |
| `docs/development/testing.md` | `:7-12` | "four distinct verification surfaces" | Nine maintained surfaces; `AGENTS.md:367-369` requires each be named separately |
| `docs/development/testing.md` | `:389`, `:401` | `python acm_switchover.py switchover …` | No subcommand exists; the CLI is flag-only |
| `docs/development/testing.md` | `:393`, `:405` | `--method passive-sync` | Valid values are `passive` and `full` |
| `docs/development/testing.md` | `:190-194` | 2026-01-28 lab observations presented inline as current | Must be labelled historical, not current support evidence |
| `docs/development/architecture.md` | `:3` | `**Version**: 1.6.3` | Reads as a product release; versioning governance forbids this |
| `docs/development/architecture.md` | `:174-179` | Credits `acm_switchover.py` with cross-mode branching and phase orchestration | Moved to `lib/operation_runners.py` (`execute_operation`, `run_switchover_impl`, `run_restore_only_impl`) and `lib/workflow.py` (`run_phase_flow`, `handle_completed_state`, `handle_failed_state`) |
| `docs/development/architecture.md` | `:192` | `StateManager` persists "config discovered during execution" | Contradicts `CONTEXT.md`, which lists `config keys` / `state config` under **_Avoid_**; the key vocabulary belongs to `RunRecord` (`lib/run_record.py`) |
| `docs/development/architecture.md` | — | No mention of `lab_controller` or release-validation (0 occurrences) | `AGENTS.md:498-530` defines the authority boundary |
| `docs/development/lab-role-controller-spec.md` | `:242` | Attributes cluster-UID binding to `AGENTS.md` | `docs/operations/usage.md:172` and `architecture.md` State Model |

`workflow.py`, `operation_runners`, `RunRecord`, and `run_record` each occur exactly once in
`architecture.md` — only inside the file-tree inventory. The inventory was refreshed; the
prose was not.

## Resolved scope decisions

### `docs/development/ci.md` conflict — surfaced, not silently fixed

`AGENTS.md:341-344` names `docs/development/ci.md` as an authoritative gate inventory.
`ci.md` is a Quay/GHCR registry-secrets and container-build guide containing zero `pytest`
references. Neither file is in #246's declared scope.

**Decision**: `docs/development/testing.md` becomes the sole gate inventory and says so
explicitly. The stale `AGENTS.md` → `ci.md` pointer is raised as a follow-up issue and noted
in the pull request. This honours the issue's instruction to surface conflicts rather than
invent resolutions, and its constraint that no other files change without explicit scope
justification.

### Deferred attribution fixes — split by verification surface

Three files still attribute to `AGENTS.md` content that #245 moved to owning authorities.
Two are inside the Ansible collection. Under `AGENTS.md:350`, any collection change requires
unit, integration, and scenario tests, a playbook syntax check, and a collection build —
disproportionate for a comment fix, and it would make a collection gate failure read as
noise against a large documentation change.

**Decision**: split into two pull requests.

| Pull request | Files | Gates |
| --- | --- | --- |
| Docs (this issue) | `CONTRIBUTING.md`, `docs/development/testing.md`, `docs/development/architecture.md`, `tests/test_documentation_guardrails.py`, `docs/development/lab-role-controller-spec.md` | Documentation gates plus the root Python lane and formatter gates |
| Collection attribution | `roles/preflight/tasks/check_auto_import_orphan.yml:16`, `tests/unit/test_preflight_auto_import_orphan.py:47` | Full collection gate set, paid once in isolation |

### Implementation order — guardrails first

Write the failing guardrail assertions first, confirm they fail against the current
documents, then correct each document to green. This matches the repository's
test-driven culture and keeps each subsequent commit's value provable. Writing the
documents first would produce assertions that describe what was written rather than what CI
requires.

Commit sequence on one branch:

1. `test(docs): guard contributor, testing, and architecture contracts`
2. `docs(contributing): route work to current owners and gates`
3. `docs(testing): define the nine verification surfaces`
4. `docs(architecture): reflect workflow, runner, and run-record ownership`

## Design

### 1. `CONTRIBUTING.md`

**Retained** (verified accurate): conventional-commit format, error-handling guidance,
logging levels, the idempotency pattern, and the documentation-update checklist.

**Corrected**:

- Line length becomes 120, stated as matching CI with a pointer to `setup.cfg` and the
  `black` invocation in `.github/workflows/ci-cd.yml`, so the reason travels with the value.
- The `PreflightValidator` recipe is replaced by an ownership routing table:

  | Change | Owner |
  | --- | --- |
  | CLI, input, and path validation | `lib/validation.py` |
  | Python preflight checks | `modules/preflight/` plus `modules/preflight_coordinator.py` and `modules/preflight/reporter.py` |
  | Python phase behaviour | The owning phase module, or `lib/workflow.py` / `lib/operation_runners.py` for flow behaviour |
  | Ansible behaviour | The owning role, module, `module_utils`, or action plugin |
  | Release checks | `tests/release/checks/` and the framework contracts |
  | Lab-controller safety | `tests/release/lab_controller/` |
  | Parity behaviour | Parity fixtures, parity tests, and the parity authority documents |

- CLI examples use the flag-only form with `--method` present. No subcommand appears.
- The dry-run section describes the public contract — `KubeClient` honours dry-run centrally,
  runs restore a `StateManager` snapshot, and unsafe paths fail closed — and points at
  `lib/kube_client.py` instead of teaching a local recipe that can bypass it.
- The feature-ideas list is removed. One entry is already delivered (`--log-format json`) and
  the remainder is an untracked wishlist with no governing issue, which conflicts with the
  requirement that work start from a governing issue or spec. Contributors are pointed at the
  issue tracker instead.

**Added**: `ansible` is the primary development branch; read current `AGENTS.md` and the
governing issue or spec before starting; use an isolated branch or worktree for
implementation and independent validation.

### 2. `docs/development/testing.md`

The four-surface framing is replaced by a nine-surface taxonomy. Each surface documents
purpose, the exact current command or authoritative reference, its nature, when it is
required, and what it does not prove.

| # | Surface | Command or authority | Nature |
| --- | --- | --- | --- |
| 1 | Root Python and Bash tests | `python -m pytest tests/ --ignore=tests/release -m "not e2e"` | Local, fake-backed |
| 2 | Release-framework helpers (non-live) | `python -m pytest tests/release -q` | Local, fake-backed |
| 3 | Collection unit | `PYTHONPATH=. pytest ansible_collections/tomazb/acm_switchover/tests/unit/ -q` | Local |
| 4 | Collection integration | `PYTHONPATH=. pytest ansible_collections/tomazb/acm_switchover/tests/integration/ -q` | Local, fake-backed |
| 5 | Collection scenario | `PYTHONPATH=. pytest ansible_collections/tomazb/acm_switchover/tests/scenario/ -q` | Local, fake-backed |
| 6 | Playbook syntax | `ansible-playbook <playbook> --syntax-check` | Local |
| 7 | Collection archive build | `ansible-galaxy collection build --output-path <dir>` | Local |
| 8 | On-demand E2E | `RUN_E2E=1 ./run_tests.sh`, or `pytest tests/e2e -m e2e` with contexts | Live, real hubs |
| 9 | Controller-gated live release evidence | `pytest tests/release/test_release_certification.py --release-profile <p> --release-mode certification` | Live, certification-eligible |

Commands for surfaces 3 through 7 are taken from
`.github/workflows/ansible-collection-foundation.yml`, which is ground truth. The
`PYTHONPATH=.` prefix is part of the command, not a footnote: without it the collection
imports fail before any test runs.

The *what it does not prove* field carries the weight. Surfaces 1 through 7 are entirely
fake-backed or local, so none of them is live evidence — consistent with the authority
boundary at `AGENTS.md:498-530`.

Further corrections:

- Formatter commands reproduce CI exactly instead of using `.`. The authoritative form is
  the `lint` job in `.github/workflows/ci-cd.yml:104,108`:

  ```bash
  black --check --line-length 120 --diff acm_switchover.py lib modules \
    ansible_collections/tomazb/acm_switchover/plugins \
    ansible_collections/tomazb/acm_switchover/tests tests
  isort --check-only --profile black --line-length 120 acm_switchover.py lib modules \
    ansible_collections/tomazb/acm_switchover/plugins \
    ansible_collections/tomazb/acm_switchover/tests tests
  ```

  This path list is copied from the workflow, not composed. Note that CI does not currently
  format `check_rbac.py` or `show_state.py`; the documentation records what CI does rather
  than an idealised superset, because a scoped command that merely looks plausible fails
  differently from CI and is worse than none.
- The test-structure tree names `modules/preflight/` as a package.
- Both obsolete subcommand examples become flag-only invocations, and `passive-sync` becomes
  `passive`.
- The 2026-01-28 lab observations move under an explicit **Historical observations** heading
  stating they are not current support evidence.
- Compatibility facts link to
  `ansible_collections/tomazb/acm_switchover/docs/compatibility.md` (#244's authority). No
  version numbers are restated.
- Three tiers are distinguished explicitly: the targeted development loop, the complete
  pre-terminal gate set, and exact-head hosted CI.
- `./run_tests.sh` gains an explicit non-completeness statement: it covers surfaces 1 and 2
  only and never runs collection gates.
- A short statement establishes this document as the gate inventory, giving the `ci.md`
  follow-up issue a concrete target.

### 3. `docs/development/architecture.md`

The file inventory, Runtime Branches, Core Design Principles, Phase Modules, Switchover
Interaction Model, GitOps, State Model, shell-companion, setup, and Ansible Collection
sections were verified current and are retained. Five targeted edits:

**3a. Metadata.** Remove `**Version**: 1.6.3`, which is indistinguishable from a product
release. Retain and refresh `Last Updated`.

**3b. Main Components reflects the extraction.** The `acm_switchover.py` entry becomes
argument parsing, logger setup, runtime bootstrap, and dispatch. Two subsections are added:

- `lib/operation_runners.py` — operation dispatch and the switchover and restore-only runner
  implementations, with `OperationDispatchHooks`, `SwitchoverRunnerHooks`, and
  `RestoreOnlyRunnerHooks` as the seam.
- `lib/workflow.py` — phase-flow execution, completed-state and failed-state handling, and
  validate-only preflight.

**3c. `RunRecord` gains prose and the `CONTEXT.md` conflict resolves in `CONTEXT.md`'s
favour.** "Config discovered during execution" is replaced by the facade framing: the
durable file belongs to `StateManager`, and the cross-phase key vocabulary belongs to
`RunRecord` (`lib/run_record.py`) alone. The State Model bullet names `RunRecord` as the
access path.

**3d. A Release Validation and Lab-Controller Boundary section, as links only.** It points
at the `AGENTS.md` boundary section, `docs/development/release-validation-framework.md`, and
`docs/development/lab-role-controller-spec.md`. It restates neither the invariants
(`AGENTS.md` owns them) nor Phase 9 status (`AGENTS.md:525` states the issue tracker owns
it, and that any status sentence written into a document is presumed stale).

**3e.** `docs/development/lab-role-controller-spec.md:242-243` currently reads "as described
in `AGENTS.md` and `docs/operations/usage.md`". It is a dual citation, so the edit removes
only the `AGENTS.md` half and adds this document's State Model beside the surviving
`usage.md` reference — which resolves to
`docs/operations/usage.md`'s "Hub identity binding on resume" section (`:172`).

### 4. Guardrails

Nine tests are added to `tests/test_documentation_guardrails.py`. They assert required
tokens and forbidden command shapes, not frozen prose.

| Test | Assertion |
| --- | --- |
| `test_contributing_line_length_matches_ci` | `120` present; `100 characters` absent |
| `test_contributing_routes_validation_to_modular_owners` | `modules/preflight/`, `lib/validation.py`, `preflight_coordinator` present; the obsolete "add a method to `PreflightValidator`" recipe absent (the class itself is still live, so the bare name must not be banned) |
| `test_active_docs_avoid_obsolete_cli_shapes` | `acm_switchover\.py\s+switchover` and `passive-sync` absent from all three documents |
| `test_testing_guide_covers_every_collection_surface` | `tests/unit/`, `tests/integration/`, `tests/scenario/`, `--syntax-check`, `collection build`, `tests/e2e`, `tests/release`, `certification` present |
| `test_formatter_guidance_avoids_repo_wide_traversal` | No `black` or `isort` line ending in a bare `.` target |
| `test_testing_guide_states_run_tests_is_not_complete` | The `run_tests.sh` non-completeness statement is present |
| `test_architecture_names_workflow_and_runner_extraction` | `run_phase_flow`, `execute_operation`, and `RunRecord` appear in prose |
| `test_architecture_links_authorities_without_restating_status` | Links to `release-validation-framework.md` and `lab-role-controller-spec.md` present; no `**Version**:` line; no Phase 9 status sentence |
| `test_docs_link_compatibility_authority` | `compatibility.md` link present; no pinned `ansible-core==` literal |

The formatter assertion is the one with false-positive risk. It anchors on a bare `.` as the
final path argument in multiline mode so that `./run_tests.sh` does not match.

The existing `test_contributing_matches_current_dev_workflow`
(`tests/test_documentation_guardrails.py:90`) is retargeted. It currently passes against the
stale file, which is how this drift survived.

## Verification

```bash
python -m pytest tests/test_documentation_guardrails.py -q
python -m pytest tests/test_ci_guardrails.py -q
python -m pytest tests/ --ignore=tests/release -q -m "not e2e"
black --check --line-length 120 tests/test_documentation_guardrails.py
isort --check-only --profile black --line-length 120 tests/test_documentation_guardrails.py
git diff --check
```

The changed surface is documentation and process plus one Python test file, so the root
lane and the formatter gates run. Collection gates do not run here; they belong to the
split-off collection pull request. Markdown link checking runs in the `documentation` job of
`.github/workflows/ci-cd.yml` and is advisory (`continue-on-error: true`). Exact-head hosted
CI remains mandatory for merge readiness.

Every executable example added by this work is checked against current `--help` output and
argument validation before it ships, contains no real credentials, kubeconfigs, cluster
identifiers, or private paths, and does not imply stronger safety evidence than the surface
provides.

## Deliverables

1. Documentation pull request — the four primary files plus
   `docs/development/lab-role-controller-spec.md`.
2. Collection attribution pull request — two comment-only fixes, carrying the full
   collection gate set.
3. Follow-up issue — the `AGENTS.md` → `ci.md` gate-inventory pointer.

## Out of scope

`AGENTS.md`, `docs/development/ci.md`, `README.md` beyond documentation links,
`docs/README.md` unless navigation consistency requires it, and any restatement of the
compatibility matrix or Phase 9 status.
