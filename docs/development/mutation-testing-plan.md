# Mutation Testing Plan — rh-acm-switchover

> Status: proposed. 

## 1. Why

Line coverage tells you which code *ran* during tests, not whether the tests would
*catch a bug* if the behavior changed. Mutation testing fills that gap: a tool
introduces small changes ("mutants") into the source — flip a comparison, delete a
statement, change a constant, swap a boolean — and re-runs the suite. A mutant the
suite still passes on ("survived") marks a behavior the tests do not actually assert.

For this repo, surviving mutants in safety-critical paths are exactly the **missing
negative tests** the project's Code Review Guidelines already prioritize (wrong-context
mutation, RBAC denial, checkpoint/resume failure, destructive-op confirmation, etc.).

## 2. Tool choice — `mutmut` (2.5.x)

| | mutmut | cosmic-ray |
|---|---|---|
| Setup | Minimal (`setup.cfg` section) | Heavier (per-run TOML + session DB) |
| Runner | Native `pytest` | pytest via config |
| Parallelism | Built-in | Built-in (distributed) |
| Fit here | Strong — matches plain-pytest setup, KISS | Better only for very large distributed runs |

Recommendation: **mutmut**, pinned `>=2.5,<3`. The 2.5.x line supports the per-module
`--paths-to-mutate` workflow and `--use-patch-file` (diff-only) that this plan relies
on; the v3 rewrite changes the CLI/config in ways that don't fit the wrapper.

Verified working in-environment: `mutmut 2.5.1`, end-to-end smoke run on
`lib/exceptions.py` scoped to its test file.

## 3. Core constraint — scoped & manual, never a per-PR gate

A full-repo run over ~12k lines re-executes the suite hundreds of times. That is far
too slow for a required CI check. Therefore mutation testing is:

- **NOT** part of `./run_tests.sh`
- **NOT** a per-PR required gate
- Run **one module at a time**, on demand or on a schedule

## 4. Scope — phased target list (highest value first)

Respects the dual-supported parity contract: both form factors get coverage, in order.

- **Phase 1 (safety-critical Python CLI):**
  `lib/validation.py`, `lib/rbac_validator.py`, `lib/utils.py` (StateManager
  checkpoint/resume + `cluster_uid` binding), `modules/decommission.py`,
  `modules/activation.py`
- **Phase 2 (remaining Python CLI):** rest of `lib/` and `modules/`
  (`kube_client.py`, `waiter.py`, `argocd.py`/`argocd_coordinator.py`,
  `preflight*`, `finalization.py`, `primary_prep.py`, `post_activation.py`)
- **Phase 3 (collection):** `plugins/module_utils/` (validation, checkpoint, gitops,
  klusterlet, result) then `plugins/modules/`

## 5. PR 1 — tooling + docs (IMPLEMENTED, pending push)

Files changed (7):

| File | Change |
|---|---|
| `requirements-dev.txt` | Pin `mutmut>=2.5,<3` (preserves upstream `ansible-core`) |
| `setup.cfg` | `[mutmut]` block: `paths_to_mutate`, `tests_dir`, fast pytest `runner` (`-x -q`, excludes e2e/release) |
| `scripts/run-mutation.sh` | Wrapper: per-module run, `--diff` (diff-only via `--use-patch-file`), `--results`/`--html`; mirrors `run_tests.sh` venv detection |
| `docs/development/testing.md` | New "Mutation Testing" section (workflow, survivor triage, phased rollout); flipped the future-enhancements checkbox |
| `AGENTS.md` | Note: mutation runs are scoped/manual, not part of `run_tests.sh` |
| `.gitignore` | Ignore `.mutmut-cache`, `mutmut-results/` |
| `CHANGELOG.md` | `[Unreleased]` → Added entry |

`setup.cfg` `[mutmut]` block:

```ini
[mutmut]
paths_to_mutate = lib/,modules/
tests_dir = tests/
runner = python -m pytest -x -q -p no:cacheprovider -m "not e2e" --ignore=tests/release --ignore=tests/e2e
```

Invocation pattern:

```bash
# Phase 1 safety-critical modules
./scripts/run-mutation.sh

# Single module, scoped to its own tests (fastest)
./scripts/run-mutation.sh lib/validation.py tests/test_validation.py

# Diff-only (lines changed vs main)
git diff origin/main... > /tmp/changes.patch
./scripts/run-mutation.sh --diff /tmp/changes.patch

# Inspect last run
./scripts/run-mutation.sh --results      # or: mutmut results
mutmut show <id>                          # diff for a surviving mutant
./scripts/run-mutation.sh --html          # html/index.html
```

## 6. Baseline, triage, quality bar (PR 2+)

1. First runs are **diagnostic**: capture a survivor baseline per Phase-1 module.
2. For each survivor, `mutmut show <id>` reveals the un-asserted behavior; add a
   focused negative test that fails on the mutant, then re-run.
3. Genuinely equivalent / intentional mutants: exclude in config with a comment so
   the exclusion is auditable (same spirit as `# pragma: no cover`).
4. Once a module is clean, set a **per-module mutation-score threshold** enforced by a
   scheduled (not per-PR) job — an incremental ratchet, not a repo-wide gate.

## 7. CI integration — off the critical path

- Add a separate **manual + scheduled** workflow (`workflow_dispatch` + weekly `cron`,
  matching the existing weekly security cron). Runs one phase's module set, uploads the
  HTML/JSON survivor report as an artifact, **report-only** at first (does not fail).
- Later: optional **diff-only PR job** using `mutmut run --use-patch-file` on the PR's
  changed lines, so contributors get signal without a full sweep.
- Ratchet to per-module score thresholds (Phase 1 first) once survivors are addressed.

## 8. Suggested rollout sequence

1. **PR 1** — tooling: `mutmut` dep, `[mutmut]` config, `scripts/run-mutation.sh`,
   docs section. No CI gate. *(done locally — `81fd1836`)*
2. **PR 2** — Phase-1 survivor baseline + kill survivors with new negative tests; add
   the scheduled report-only workflow.
3. **PR 3** — enable per-module score thresholds for Phase-1 modules; start Phase 2.
4. **Later** — Phase 3 (collection) + optional diff-only PR job.

## 9. To land PR 1

The commit is ready and rebased on the live `ansible` tip. It needs either:

- **Push access** restored for the session → `git push -u origin claude/peaceful-euler-UkTjq`, or
- Manual: pull/cherry-pick `81fd1836` from this branch and push from a machine with
  write access.

(Optional: commit is currently unsigned because the session signing key is empty —
GitHub will label it "Unverified"; cosmetic, does not block push or CI.)
