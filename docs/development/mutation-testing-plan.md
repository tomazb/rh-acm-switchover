# Mutation Testing Notes and Future Design Stub

> Status: deferred concept note. This is not an implementation plan.

This document captures why mutation testing is worth considering for
`rh-acm-switchover` and what a later design/spec should decide. Implementation is
deferred until the current Thermos PR sequence is complete or explicitly paused.
When that happens, start a fresh Superpowers workflow:

1. Use `superpowers:brainstorming` to write and approve a design/spec.
2. Use `superpowers:writing-plans` to turn the approved design into an implementation plan.
3. Use `superpowers:executing-plans` or `superpowers:subagent-driven-development` only after the design and plan are approved.

## Why Mutation Testing

Line coverage tells us which code ran during tests, not whether tests would catch
a behavior change. Mutation testing fills that gap: a tool introduces small
source changes, such as flipped comparisons, removed statements, changed
constants, or swapped booleans, and then re-runs the relevant tests. A mutant
that survives marks behavior that the current tests do not actually assert.

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

## Boundaries

Any future implementation should keep mutation testing off the normal developer
critical path unless a later design explicitly changes this after measured
runtime data.

- Do not add mutation testing to `./run_tests.sh`.
- Do not make it a required per-PR gate at the start.
- Run it one target module or target behavior area at a time.
- Treat first results as diagnostic baseline data, not immediate failure criteria.
- Preserve the dual-supported parity contract: survivors in shared behavior must
  be triaged against both the Python CLI and the Ansible collection.

## Candidate Tooling Inputs

`mutmut` is the current candidate because it fits the repo's pytest-based test
stack and has a simpler local workflow than heavier distributed mutation tools.
The previously explored candidate version range was `mutmut>=2.5,<3`.

These are design inputs, not final decisions:

- Revalidate the installed `mutmut` CLI and config behavior before implementation.
- Pin only in `requirements-dev.txt`; do not add mutation tooling to runtime dependencies.
- Keep any cache or result output out of tracked source files.
- Prefer a thin repo wrapper over requiring contributors to remember raw tool flags.
- If a future design chooses another tool, update this document or replace it with
  the approved design/spec.

## Candidate Safety Requirements

The future wrapper or workflow should be designed to fail early before it mutates
the wrong files or creates noisy repo state.

- Assert the expected mutation-tool version before running.
- Require explicit source and test targets for normal runs.
- Refuse to run when tracked source or test files involved in the target are dirty,
  unless an explicit local-only override such as `--allow-dirty` is provided.
- Default diff-only runs to the active base branch rather than hard-coding
  `origin/main`; prefer `GITHUB_BASE_REF`, then current upstream, then `origin/ansible`.
- Ignore mutation caches and generated reports through `.gitignore`.
- Keep scheduled CI report-only at first.

## Candidate Phase Targets

These targets are hypotheses for the future design/spec. Re-check them after the
Thermos queue finishes because source layout, test coverage, and parity mappings
may change.

| Phase | Candidate source targets | Candidate test focus |
| --- | --- | --- |
| 1 | `lib/validation.py` | CLI validation, safe-path behavior, validation parity fixtures |
| 1 | `lib/rbac_validator.py` | Python RBAC tests, RBAC integration tests, collection RBAC parity |
| 1 | `lib/utils.py` | StateManager, checkpoint/resume, hub identity binding |
| 1 | `modules/decommission.py` | destructive-operation safety, dry-run behavior, collection decommission contracts |
| 1 | `modules/activation.py` | passive/full activation waits, stale restore handling, collection activation parity |
| 2 | remaining `lib/` and `modules/` | preflight, finalization, primary prep, post-activation, Argo CD, waiter behavior |
| 3 | collection `plugins/module_utils/` and `plugins/modules/` | validation, checkpoint, GitOps, klusterlet, result, report, and module contracts |

For dual-supported behavior, a survivor should not be closed by adding Python-only
coverage if the same operator-facing behavior exists in the collection. Either add
or confirm matching collection/parity coverage, or record an approved parity
divergence through the existing parity process.

## Candidate Reporting And Baseline Model

The first useful output is a baseline of surviving mutants by target area. A later
implementation should define the exact artifact format rather than assuming one.

Candidate outputs:

- text summary from the mutation tool
- `mutmut show <id>` output for selected survivors
- HTML report for manual inspection
- optional JUnit/XML or generated JSON summary if scheduled CI needs stable artifacts

Do not introduce per-module score thresholds until a module has a reviewed
baseline and high-value survivors have been triaged. Thresholds should be a
ratchet: start with Phase 1 targets only, then raise expectations as survivors are
killed or explicitly excluded.

Equivalent or intentional mutants need an auditable reason. Use a tool-supported
exclusion such as `# pragma: no mutate` or config exclusion only when the mutant
is genuinely equivalent or not operationally meaningful.

## Deferred Design Questions

The future Superpowers design/spec should answer these before implementation:

- Should the first implementation target Python-only safety modules, paired
  Python/collection parity slices, or collection-local modules first?
- Should the first implementation PR add local tooling only, or also add a
  scheduled report-only workflow?
- Which artifact format is required for CI: text/HTML only, JUnit/XML, generated
  JSON, or a combination?
- What is the first acceptable baseline: record survivors only, or kill selected
  Phase 1 survivors before adding any scheduled workflow?
- How should the wrapper map source files to focused tests while avoiding stale or
  under-scoped test selections?
- What exact policy should apply when a survivor exposes a dual-supported parity gap?

## Start Conditions For Future Work

Do not implement mutation testing from this concept note alone. Start the real
work only when all of these are true:

- The current Thermos PR sequence is complete or explicitly paused.
- The active branch is current with the target branch used for Thermos follow-up work.
- The source/test topology is rechecked against `docs/ansible-collection/behavior-map.md`,
  `docs/ansible-collection/parity-matrix.md`, and
  `docs/ansible-collection/test-migration-catalog.md`.
- A fresh Superpowers design/spec is written, reviewed, and approved.
- A Superpowers implementation plan is written from that approved design/spec.
