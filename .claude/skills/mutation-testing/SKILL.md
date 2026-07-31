---
name: mutation-testing
description: Plan, run, and triage targeted mutation testing for rh-acm-switchover. Usage - /mutation-testing --review lib/validation.py --tests tests/test_validation.py or /mutation-testing --baseline modules/activation.py --tests tests/test_activation.py
---

# Mutation Testing

Use this skill when the user asks to assess mutation testing, design a mutation
workflow, run a local mutation spike, interpret surviving mutants, or improve
tests based on mutation output.

The goal is not a score. The goal is to find weak assertions in safety-sensitive
and parity-sensitive behavior while keeping mutation testing out of the normal
local and CI critical path until an approved implementation plan says otherwise.

## Operating Modes

### Review-Only Mode

Use when the user asks for assessment, plan review, or target selection without
asking for edits or tool execution.

Output:

- recommended source target
- focused test command to run before mutation
- likely parity surface
- expected operational risk if a survivor appears
- open design questions

Do not modify files in this mode.

### Spike Mode

Use when the user asks to validate mutation tooling on one narrow target.

A spike may validate tool version, dependency placement, config behavior, target
selection, runtime, and report output. Keep the spike to one module or function
family. Do not add thresholds, required CI checks, or broad repo-wide mutation
configuration during a spike.

### Baseline Mode

Use when the user asks for a first mutation baseline on an approved target.

A baseline records what survived and why it matters. It does not automatically
mean the test suite is failing. Treat the result as triage data until a reviewed
ratchet threshold exists for that target.

### Triage/Apply Mode

Use when the user asks to kill selected survivors or improve tests.

Apply the smallest useful test change first. For dual-supported behavior, review
Python and collection coverage together before closing the survivor.

## Required Context

Read these files before planning or changing mutation testing behavior:

1. `AGENTS.md`
2. `docs/development/mutation-testing-plan.md`
3. `docs/development/testing.md`
4. `docs/ansible-collection/behavior-map.md`
5. `docs/ansible-collection/parity-matrix.md`
6. `docs/ansible-collection/test-migration-catalog.md`
7. `setup.cfg`
8. `requirements-dev.txt`

Also inspect the target source file and its focused tests before proposing a
mutation command.

## Safety Rules

- Do not add mutation testing to `./run_tests.sh`.
- Do not make mutation testing a required per-PR check in the first implementation.
- Do not run mutation testing against live-cluster E2E tests.
- Do not run commands that require real kubeconfig contexts unless the user
  explicitly requests a local experiment and provides the needed context.
- Run the unmutated focused pytest command before a mutation run.
- Require an explicit source target and explicit test target for normal runs.
- Keep mutation dependencies in `requirements-dev.txt` only.
- Keep mutation caches and reports out of tracked source files.
- Do not apply a mutant to disk unless the checkout is clean and the user asks to
  inspect that mutant locally.
- Do not use broad exclusions to hide survivors; prefer narrow source-level
  exclusions only for reviewed equivalent mutants.

## Target Selection

Favor safety-sensitive, assertion-heavy targets before broad codebase sweeps.

| Source target | Focused tests | Parity review |
| --- | --- | --- |
| `lib/validation.py` | `tests/test_validation.py`, `tests/test_validation_parity.py` | collection validation fixture and safe-path policy |
| `lib/rbac_validator.py` | `tests/test_rbac_validator.py`, `tests/test_rbac_collection_parity.py` | collection RBAC validation and RBAC manifest parity |
| `lib/utils.py` | `tests/test_utils.py`, checkpoint/resume tests | collection checkpoint action/plugin behavior |
| `modules/activation.py` | `tests/test_activation.py` | collection activation role and activation wait contracts |
| `modules/decommission.py` | `tests/test_decommission.py` | collection decommission role contracts |
| `lib/argocd.py` | `tests/test_argocd.py`, Argo CD parity tests | collection Argo CD module_utils, roles, and resume behavior |

When the requested target is not in this table, map it through
`docs/ansible-collection/behavior-map.md` and `docs/ansible-collection/test-migration-catalog.md`
before selecting tests.

## Preflight Checklist

Before running mutation tooling:

```bash
git status --short
python -m pytest <focused-test-target> -q
```

If the focused tests fail before mutation, stop and report the failing baseline.
Do not interpret mutation results on top of a red unmutated test target.

Verify the installed mutation tool and its config behavior at execution time:

```bash
python -m pip show mutmut
mutmut --help
```

If `mutmut` is not installed, report the missing dependency and point back to the
approved implementation plan before editing dependency files.

## Candidate Mutmut Flow

Prefer config-driven execution over ad hoc flags. After an approved implementation
adds a minimal `[mutmut]` config and wrapper, the expected local flow is:

```bash
python -m pytest <focused-test-target> -q
mutmut run "<module-or-function-pattern>*"
mutmut browse
```

Use `mutmut show <id>` or the browse UI to inspect selected survivors. Only use
`mutmut apply <id>` on a clean checkout when the user wants to inspect a mutant on
disk.

Useful config concepts to re-check in the installed version before relying on
them:

- `source_paths`
- `pytest_add_cli_args_test_selection`
- `also_copy`
- `only_mutate`
- `do_not_mutate`
- `mutate_only_covered_lines`
- `max_stack_depth`
- `# pragma: no mutate`

## Survivor Triage

Classify each meaningful survivor before changing tests:

| Classification | Meaning | Action |
| --- | --- | --- |
| Missing assertion | Test reaches behavior but does not assert the mutated outcome | strengthen the smallest relevant test |
| Missing scenario | No focused test covers the behavior | add a targeted unit, integration, or parity test |
| Parity gap | Survivor touches dual-supported behavior | add or confirm coverage on both Python and collection surfaces, or document approved divergence |
| Equivalent | Mutant does not change observable behavior | record the reason; use a narrow exclusion after review |
| Incidental/noisy | Survivor is caused by broad incidental test reachability | tighten target selection or stack depth before judging coverage |
| Tool/runtime issue | Timeout, import isolation issue, unsupported construct, or runner issue | record as tooling debt, not as test weakness |

Prioritize survivors that affect wrong-cluster safety, RBAC denials,
checkpoint/resume identity binding, destructive-operation confirmation, dry-run
behavior, Argo CD application selection, and activation/wait timeouts.

## Parity-Sensitive Survivors

When a survivor touches a dual-supported capability:

1. Check `docs/ansible-collection/parity-matrix.md` for the current status.
2. Check `docs/ansible-collection/behavior-map.md` for the collection target.
3. Check `docs/ansible-collection/test-migration-catalog.md` for the matching test layer.
4. Add or confirm coverage on both relevant surfaces.
5. If behavior should diverge, ask for operator approval and record the divergence
   in the parity docs before treating the survivor as closed.

Do not close a dual-supported survivor with Python-only coverage unless the
collection surface is already covered or an approved divergence exists.

## Reporting Template

Use this structure for baseline or triage summaries:

```markdown
## Mutation Target
- Source: `<source>`
- Tests: `<tests>`
- Tool/version: `<tool version>`
- Commit: `<sha>`
- Command: `<command>`

## Unmutated Baseline
- Command: `<pytest command>`
- Result: pass/fail

## Results
- Killed: N
- Survived: N
- Timeout/suspicious/skipped: N

## High-Value Survivors
| Mutant | Risk | Classification | Proposed action | Parity impact |
| --- | --- | --- | --- | --- |
| `<id>` | wrong-cluster/RBAC/etc. | missing assertion/parity gap/etc. | add test / record equivalent / defer | Python + collection |

## Next Actions
1. ...
```

## When To Stop

Stop and report instead of continuing when:

- the focused unmutated tests fail
- the checkout has unexpected dirty changes
- the requested target needs live cluster contexts
- the selected test target is too broad to be useful
- the survivor appears equivalent but the equivalence argument is unclear
- a parity-sensitive survivor would require an intentional divergence decision

## Implementation Guardrails

A future implementation PR should stay small:

- add a dev-only mutation dependency
- add a minimal config only after a spike proves the selected tool behavior
- add a thin wrapper with explicit source/test target validation
- update `docs/development/testing.md` with on-demand usage
- add ignore rules for mutation caches/reports
- keep CI report-only and manually triggered at first, if CI is added at all

Do not add score thresholds until the target has a reviewed baseline and the
highest-value survivors have been triaged.
