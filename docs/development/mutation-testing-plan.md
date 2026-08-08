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
- `mutmut show <mutant-name-from-mutmut-results>` output for selected survivors
- HTML report for manual inspection if available in the chosen tool/version
- optional generated JSON summary if scheduled CI needs stable artifacts
- optional JUnit/XML only if the chosen tool or wrapper can generate it without fragile parsing

## Phase 2 Baseline Records

### `modules/post_activation.py` baseline (`mutation/post-activation-baseline` @ `968720b9b6dd8f58250b29a024b277efbd487da1`)

- Source target: `modules/post_activation.py`
- Base branch: `ansible`
- Focused Python test target: `tests/test_post_activation.py`
- Collection parity targets:
  - `ansible_collections/tomazb/acm_switchover/tests/unit/test_post_activation_regressions.py`
  - `ansible_collections/tomazb/acm_switchover/tests/unit/test_post_activation_observability.py`
  - `ansible_collections/tomazb/acm_switchover/tests/unit/test_klusterlet_remediation.py`
  - `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_managedcluster_status.py`
  - `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_klusterlet_modules.py`
  - `ansible_collections/tomazb/acm_switchover/tests/unit/test_shared_ansible_logic_contracts.py`
  - `ansible_collections/tomazb/acm_switchover/tests/integration/test_switchover_roles.py -k post_activation`

#### Unmutated baseline

- Python baseline command/result:
  - Command: `source .venv/bin/activate && python -m pytest tests/test_post_activation.py -q`
  - Result: PASS (`119 passed in 0.96s`)
- Collection unit/contracts lane command/result:
  - Command: `source .venv/bin/activate && PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/test_post_activation_regressions.py ansible_collections/tomazb/acm_switchover/tests/unit/test_post_activation_observability.py ansible_collections/tomazb/acm_switchover/tests/unit/test_klusterlet_remediation.py ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_managedcluster_status.py ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_klusterlet_modules.py ansible_collections/tomazb/acm_switchover/tests/unit/test_shared_ansible_logic_contracts.py -q`
  - Result: PASS (`87 passed in 0.37s`)
- Collection integration lane command/result:
  - Command: `source .venv/bin/activate && PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/integration/test_switchover_roles.py -k post_activation -q`
  - Result: PASS (`1 passed, 9 deselected in 20.86s`)

#### Mutation tool baseline

- Tool/version: `mutmut 3.6.0`
- Command: `source .venv/bin/activate && rm -rf mutants/ && mutmut run`
- Result source of truth: `mutants/modules/post_activation.py.meta`
- Counts:
  - Total: `1389`
  - Killed: `840`
  - Survived: `544`
  - Not checked: `0`
  - Other statuses: `{-24: 5}` (`mutmut show` reports these as runtime timeouts)
- Top survivor-heavy functions:
  - `78` — `_wait_for_observatorium_api_rollout`
  - `65` — `_verify_observability_pods`
  - `38` — `_non_local_managed_cluster_names`
  - `37` — `_verify_disable_auto_import_cleared`
  - `37` — `_load_kubeconfig_data`
  - `24` — `_patch_or_create_bootstrap_secret`
  - `24` — `_wait_for_secret_visibility`
  - `20` — `_restart_klusterlet`
  - `19` — `_check_klusterlet_connection`
  - `18` — `_restart_observatorium_api`

#### High-value survivor classification

| Mutant | Risk area | Class | Evidence / why it survived |
| --- | --- | --- | --- |
| `modules.post_activation.xǁPostActivationVerificationǁ_wait_for_observatorium_api_rollout__mutmut_7` | observability rollout readiness | missing assertion | Python tests prove replica-count gating, but the restart path only asserts `get_deployment()` call count, so `name=None` / `namespace=None` rollout fetches survive. Collection coverage already locks the shared rollout contract via `verify_observability.yml` and `test_post_activation_observability.py` assertions on `observedGeneration`, `updatedReplicas`, `availableReplicas`, `readyReplicas`, and `unavailableReplicas`. |
| `modules.post_activation.xǁPostActivationVerificationǁ_wait_for_observatorium_api_rollout__mutmut_6` | observability polling / timeout budget | tool/runtime issue | Mutating the rollout fetch to `deployment = None` does not produce a fast assertion failure; it burns the real sleep budget inside the polling loop and times out under mutmut. Record this as mutation-runner/runtime noise, not as evidence that the unmutated timeout contract is wrong. |
| `modules.post_activation.xǁPostActivationVerificationǁ_verify_observability_pods__mutmut_20` and `__mutmut_21` | observability unhealthy-pod fail-closed behavior | parity gap | Python covers `OOMKilled`, non-zero terminated exit codes, failed/unknown phases, and readiness thresholds, but it does not explicitly pin `terminated.reason == "Error"`. The collection `verify_observability.yml` and `test_post_activation_observability.py` currently inspect phase/Ready/waiting reasons only, so terminated-container fail-closed semantics are not aligned across both form factors yet. |
| `modules.post_activation.xǁPostActivationVerificationǁ_verify_disable_auto_import_cleared__mutmut_2` | auto-import cleanup correctness after activation | missing scenario | The survivor weakens the initial `force_refresh=True` read to `force_refresh=None`. Current Python tests cover clean/fail/patch flows, but they never pre-seed `_cached_managed_clusters` to prove the pre-patch read must bypass stale cache. The collection cleanup task always performs a live read before patching and a second live read before asserting success. |
| `modules.post_activation.xǁPostActivationVerificationǁ_patch_or_create_bootstrap_secret__mutmut_1` and `__mutmut_13` | klusterlet remediation secret patch/create path | missing assertion | Python exercises the helper only indirectly through `_force_klusterlet_reconnect()` and mostly asserts call counts, so wrong `name` / `namespace` / `body` values survive. The collection remediation module has stronger tuple-level assertions for patched/created bootstrap secrets in `test_acm_klusterlet_modules.py`, so this is primarily a Python assertion gap. |
| `modules.post_activation.xǁPostActivationVerificationǁ_wait_for_secret_visibility__mutmut_17` | klusterlet remediation wait/poll diagnostics | parity gap | Python exposes a distinct bootstrap-secret visibility wait with public pending details, but the collection remediation helper does not currently implement a matching visibility wait before restart. Treat this as a shared-behavior review item before spending time on Python-only mutant killing. |
| `modules.post_activation.xǁPostActivationVerificationǁ_restart_klusterlet__mutmut_2` | klusterlet restart patch semantics | parity gap | Mutating the restart patch body (`"spec"` → `"XXspecXX"`) survives because Python only checks that a restart call happened. Collection tests assert that a deployment restart call occurs, but they do not currently pin the patch body shape either, so the shared restart contract still lacks exact payload assertions. |
| `modules.post_activation.xǁPostActivationVerificationǁ_check_klusterlet_connection__mutmut_21` | wrong-hub detection / fallback secret lookup | missing assertion | Python verifies verified/wrong-hub/unreachable/failed result classes, but it does not assert the fallback bootstrap secret name/namespace passed to `read_namespaced_secret()`. The collection probe module already exercises verified/wrong-hub/skip outcomes, so this survivor is mainly a Python helper-call assertion gap rather than a collection-wide blind spot. |
| `modules.post_activation.xǁPostActivationVerificationǁ_load_kubeconfig_data__mutmut_34` | kubeconfig discovery for wrong-hub remediation | missing assertion | `tests/test_post_activation.py::test_load_kubeconfig_default_path` does exercise the fallback branch with `KUBECONFIG` unset, but it only asserts that the helper returns a `dict`. The mutant changes `os.path.expanduser("~/.kube/config")` to `os.path.expanduser(None)`, which is swallowed by the helper's broad exception handling and still returns `{}`, so the current test reaches the branch without asserting the intended fallback behavior. |
| `modules.post_activation.xǁPostActivationVerificationǁ_non_local_managed_cluster_names__mutmut_9` | helper-only cluster-name discovery | incidental/noisy | The surviving mutations are mostly argument-string/default tweaks on the helper’s `list_custom_resources()` call. They do not currently outrank the rollout, observability, or klusterlet survivors for mutation follow-up priority. |

#### Next action recommendation

1. Do **not** ratchet a module threshold on `modules/post_activation.py` yet; the shared-behavior survivors still mix Python-only assertion gaps with unresolved parity questions.
2. Prioritize future kill work on Python call-argument assertions for observatorium rollout fetches, bootstrap secret patch/create calls, and klusterlet fallback secret reads.
3. Resolve parity intent first for two shared-behavior concerns before writing more tests: terminated-container observability failures and bootstrap-secret visibility wait semantics.
4. Keep `_non_local_managed_cluster_names()` in a lower-priority helper-noise bucket, but treat `_load_kubeconfig_data()` as a real Python assertion follow-up once the higher-risk rollout and klusterlet gaps are addressed.

Do not introduce per-module score thresholds until a module has a reviewed
baseline and high-value survivors have been triaged. Thresholds should be a
ratchet: start with Phase 1 targets only, then raise expectations as survivors are
killed or explicitly excluded.

Equivalent or intentional mutants need an auditable reason. Use a tool-supported
exclusion such as `# pragma: no mutate` or config exclusion only when the mutant
is genuinely equivalent or not operationally meaningful. Prefer targeted
exclusions near the source over broad config suppression.

### Phase 2 baseline: `lib/argocd.py` (2026-06-09)

- **Source target:** `lib/argocd.py`
- **Baseline branch:** `mutation/argocd-baseline`
- **Baseline commit:** `4722efd227ea757d4de835720a882021ad1cdb57`
- **Python baseline command/result:** `python -m pytest tests/test_argocd.py tests/test_argocd_constants_parity.py -q` → `67 passed`
- **Collection unit lane command/result:** `PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_argocd_autosync.py ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_argocd_filter.py ansible_collections/tomazb/acm_switchover/tests/unit/test_argocd_discovery_safety.py ansible_collections/tomazb/acm_switchover/tests/unit/test_argocd_hub_parameterization.py ansible_collections/tomazb/acm_switchover/tests/unit/test_argocd_manage_role_contracts.py ansible_collections/tomazb/acm_switchover/tests/unit/test_argocd_resume_on_failure.py -q` → `114 passed`
- **Collection integration lane command/result:** `PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/integration/test_argocd_manage_role.py -q` → `5 passed`
- **Mutation tool/version:** `mutmut 3.6.0`
- **Mutation command:** `mutmut run` (using a temporary Argo CD-focused `[mutmut]` config in `setup.cfg`)
- **Counts:** `total 556 / killed 353 / survived 203 / not_checked 0`

Baseline note for reruns:

- `tests/test_argocd_constants_parity.py` imports collection Argo CD helpers from
  `ansible_collections/tomazb/acm_switchover/plugins/module_utils/`.
- The valid Argo CD baseline above was captured with a temporary Argo CD-focused
  `[mutmut]` config in `setup.cfg`; `setup.cfg` was restored afterward, so reruns
  must reconstruct or reuse that focused config instead of using final HEAD as-is
  (see Task 5 in `docs/plans/2026-06-09-argocd-mutation-baseline.md`).
- Valid `lib/argocd.py` baselines therefore require `[mutmut] also_copy` to copy
  both `lib/` and
  `ansible_collections/tomazb/acm_switchover/plugins/module_utils/` into the
  mutant workspace. Without that copy, parity-test imports drift from the
  mutant environment and the baseline is not comparable.

Direct pause-patch payload drift is already comparatively well-covered by
`tests/test_argocd_constants_parity.py` plus collection
`test_acm_argocd_autosync.py`; the top surviving Argo CD mutants cluster around
post-patch ground-truth verification, ApplicationSet safety boundaries,
discovery helper mocks, and resume-on-failure replay paths instead.

| Survivor group | Representative survivors | Class | Why it survived / parity read |
| --- | --- | --- | --- |
| Pause verification after patch errors | `lib.argocd.x__pause_ground_truth_applied__mutmut_2`, `lib.argocd.x__pause_ground_truth_applied__mutmut_24` | missing scenario | Python tests cover successful re-read recovery and explicit re-read failure, but not `None`/partial re-read shapes after a patch error. Collection contracts verify the live re-read/fail path in `pause.yml`, so this is not pure Argo CD parity drift, but the Python helper still needs a tighter negative-path scenario matrix. |
| ApplicationSet and child-Application safety boundary | `lib.argocd.x_find_argocd_pause_blockers__mutmut_4` | parity gap | The mutant would block any ACM-touching app, not only ApplicationSet-managed child Applications. Python tests cover positive blocker cases, and collection tests cover the same shared behavior, but neither side currently asserts the negative shared case that an ACM-touching app without an `ApplicationSet` owner must remain eligible for managed pause. |
| Stale `status.resources` boundary | `lib.argocd.x__status_resources_are_stale__mutmut_31` | parity gap | Python and collection both assert the stale `<` case, but neither side nails the equality boundary (`observedGeneration == generation`). Because `unknown-acm-impact` blocking is dual-supported, this survivor is shared-behavior debt rather than Python-only noise. |
| ApplicationSet owner-name fallback text | `lib.argocd.x__applicationset_owner_name__mutmut_27` | parity gap | Both implementations generate the same blocker message shape, and both test suites only exercise named parents such as `parent-set`. Missing-name fallback text remains under-specified on both sides, so this is a small but real shared contract gap. |
| Resume-on-failure pause-state replay | `lib.argocd.x_resume_recorded_applications__mutmut_22`, `lib.argocd.x_resume_recorded_applications__mutmut_41`, `lib.argocd.x_resume_recorded_applications__mutmut_74` | missing scenario | The Python suite has only a narrow invalid-entry test for `resume_recorded_applications`, leaving malformed keys, multi-entry replay, early-break, and wrong-argument mutants alive. Collection unit coverage proves the shared resume-on-failure workflow shape (guards, run_id reuse, trusted namespace reuse), but it does not map one-for-one to this Python helper, so the immediate gap is missing Python scenario coverage. |
| CRD/Application discovery helper call signatures | `lib.argocd.x__get_crd_presence__mutmut_10`, `lib.argocd.x__list_argocd_applications_once__mutmut_9`, `lib.argocd.x_detect_argocd_installation__mutmut_65` | tool/runtime issue | Several survivors delete or rename Kubernetes client kwargs or result keys that permissive `MagicMock`-based tests still accept. Collection discovery tests already exercise fail-closed rescue behavior at the role layer, so these survivors mostly reflect mock looseness and import/runtime isolation limitations rather than a shared operator-facing parity problem. |
| Helper default/fallback noise | `lib.argocd.x_has_applicationset_owner__mutmut_3`, `lib.argocd.x_detect_argocd_installation__mutmut_23` | equivalent | These representative mutants keep the same observable behavior (`or []` still normalizes missing owner references; `install_type_override = ""` behaves like `None` in the existing `or "vanilla"` flow). They should not drive follow-up work unless a later review wants a narrow `no mutate` exclusion. |

**Next action recommendation:** keep this as a recorded baseline only for now.
The next triage/apply slice should prioritize the shared-behavior survivors
first: ordinary ACM-touching apps vs `ApplicationSet` ownership, the
`observedGeneration == generation` boundary, and the owner-name fallback text.
After that, add a Python-only negative-path matrix for
`resume_recorded_applications` and `_pause_ground_truth_applied`. Treat the
discovery-helper signature survivors as tooling debt unless a later focused run
switches those mocks to stricter call-argument assertions.

## Phase 2 Baseline Record: `modules/primary_prep.py`

- Date: 2026-06-11
- Source target: `modules/primary_prep.py`
- Focused Python test target: `tests/test_primary_prep.py`
- Provenance:
  - base branch: `ansible`
  - baseline branch: `mutation/primary-prep-baseline`
  - baseline branch HEAD commit: `f1625c3876028aa9f3d99d475459650d6c3818eb`
  - worktree: `<repo-root>/.worktrees/mutation-primary-prep-baseline`

### Unmutated baseline lanes

- Python baseline: `source .venv/bin/activate && python -m pytest tests/test_primary_prep.py -q` — PASS (`39 passed`)
- Collection unit/contracts lane: `source .venv/bin/activate && PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/test_primary_prep_auto_import.py ansible_collections/tomazb/acm_switchover/tests/unit/test_backup_schedule_persistence.py -q` — PASS (`10 passed`)
- Collection integration/scenario lane: `source .venv/bin/activate && PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/integration/test_switchover_roles.py -k primary_prep -q` — PASS (`2 passed, 8 deselected`)

### Mutation baseline

- Tool/version: `mutmut 3.6.0`
- Config target: `setup.cfg` `[mutmut]` with `source_paths = modules/primary_prep.py` and `pytest_add_cli_args_test_selection = tests/test_primary_prep.py`
- Baseline command: `source .venv/bin/activate && rm -rf mutants/ && mutmut run`
- Inspection commands: `source .venv/bin/activate && mutmut results | head -40`; `source .venv/bin/activate && mutmut show <mutant-name-from-mutmut-results>`
- `mutmut show` in this mutmut version takes the full mutant name string copied from `mutmut results` (for example `modules.primary_prep.xǁPrimaryPreparationǁprepare__mutmut_2`); the earlier `FileNotFoundError` came from trying the short suffix form instead.
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
| `_pause_backup_schedule__mutmut_2`, `3`, `10`, `73`, `78`, `84` | BackupSchedule wrong-resource targeting (`group`, `version`, `namespace`) on list/patch calls | `ansible_collections/tomazb/acm_switchover/roles/primary_prep/tasks/pause_backups.yml`, `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_backup_schedule.py`, `ansible_collections/tomazb/acm_switchover/tests/integration/test_switchover_roles.py` | parity gap | Python tests assert patch payload and version branching, but they do not assert the exact `list_custom_resources()`, `patch_custom_resource()`, or delete-path targeting kwargs that the collection keeps explicit. |
| `_pause_backup_schedule__mutmut_50`, `51`, `52`, `53`, `64` | `saved_backup_schedule` persistence before pause/delete and on already-paused reruns | `ansible_collections/tomazb/acm_switchover/tests/unit/test_backup_schedule_persistence.py`, `ansible_collections/tomazb/acm_switchover/roles/primary_prep/tasks/main.yml` | parity gap | The collection treats saved schedule persistence as a checkpoint/finalization contract; Python tests do not currently assert the corresponding state writes on the already-paused or pre-mutation paths. |
| `_scale_down_thanos_compactor__mutmut_8`, `9`, `10`, `11`, `12`, `35`, `40` | Thanos premature-success / skipped-verification risk: unscoped pod reads can report success while compactor pods still exist, and the 404 branch must stay fail-closed | `ansible_collections/tomazb/acm_switchover/tests/unit/test_primary_prep_auto_import.py`, `ansible_collections/tomazb/acm_switchover/roles/primary_prep/tasks/scale_observability.yml` | parity gap | The collection contract explicitly checks the selector, namespace, retries/delay, and fail-on-remaining-pods flow. Python tests verify the scale call and timeout raise, but they do not yet pin the exact verification query or the fail-closed 404 mapping tightly enough to kill false-success mutants. |
| `_disable_auto_import__mutmut_4`, `8`, `10`, `23`, `25`, `42`, `43`, `44` | Disable-auto-import bookkeeping and malformed-metadata fallbacks | `ansible_collections/tomazb/acm_switchover/tests/unit/test_primary_prep_auto_import.py`, `ansible_collections/tomazb/acm_switchover/roles/primary_prep/tasks/manage_auto_import.yml` | incidental/noisy | No surviving mutant changed the real shared contract: annotation patching of non-local clusters and `local-cluster` exclusion remain covered. The survivors only affect log-count bookkeeping or unrealistic malformed `metadata` dict shapes. |
| `_pause_argocd_acm_apps__mutmut_10`, `11`, `12`, `13`, `14` | Argo CD success-path `run_id` resume-hint logging after pause | `ansible_collections/tomazb/acm_switchover/roles/primary_prep/tasks/main.yml`, `ansible_collections/tomazb/acm_switchover/tests/unit/test_argocd_resume_on_failure.py` | incidental/noisy | The collection already persists and rehydrates `argocd_run_id` for checkpoint/resume. These Python survivors only change whether the success log prints the run ID, not whether pause state is recorded. |
| `prepare__mutmut_2` | Execute-mode Argo CD pause step tracking can be recorded under the wrong step key, weakening resume/idempotence guarantees | `ansible_collections/tomazb/acm_switchover/roles/primary_prep/tasks/main.yml`, `ansible_collections/tomazb/acm_switchover/tests/unit/test_argocd_hub_parameterization.py` | missing scenario | Diff inspection with the full mutant name from `mutmut results` shows this mutant changes `self.state.step(STEP_PAUSE_ARGOCD_APPS, logger)` to `self.state.step(None, logger)`. Python tests only pin the dry-run branch for that step; they do not cover execute-mode `prepare()` with `argocd_manage=True` and assert the correct completed-step marker. |
| `prepare__mutmut_3`, `4`, `5`, `7`, `9`, `13`, `15`, `19`, `21` | Optional logger wiring on `state.step(...)` wrappers for Argo CD, BackupSchedule, auto-import, and Thanos orchestration | `lib/utils.py`, `tests/test_primary_prep.py` | incidental/noisy | Full-name diff inspection shows these mutants only remove or reshape the optional `logger` argument passed into `StateManager.step()`. `StepContext` still executes and records the same step names; the only behavioral loss is informational \"already completed\" logging, so these survivors are noisy rather than a missed safety assertion. |
| `__init____mutmut_9` | Secondary hub reference dropped during construction, which would skip secondary-hub Argo CD pause when dual-hub coordination is enabled | `ansible_collections/tomazb/acm_switchover/roles/primary_prep/tasks/main.yml`, `ansible_collections/tomazb/acm_switchover/tests/unit/test_argocd_hub_parameterization.py` | missing scenario | The collection explicitly pauses both hubs during `primary_prep`, but the Python tests do not construct `PrimaryPreparation(..., secondary_client=...)` and assert that the secondary hub participates in Argo CD coordination. |

### Primary-prep survivor resolution: top three groups (2026-06-11)

Scope: test-only resolution for the top three primary-prep survivor groups. No
production behavior, CLI surface, report schema, parity status, or operator
workflow changed.

Commands run:

- Baseline before edits:
  - `python -m pytest tests/test_primary_prep.py -q` — PASS (`39 passed`)
  - `python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/test_primary_prep_auto_import.py ansible_collections/tomazb/acm_switchover/tests/unit/test_backup_schedule_persistence.py ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_backup_schedule.py -q` — PASS (`29 passed`)
  - `python -m pytest ansible_collections/tomazb/acm_switchover/tests/integration/test_switchover_roles.py -k primary_prep -q` — PASS (`2 passed, 8 deselected`)
- After test additions:
  - `python -m pytest tests/test_primary_prep.py -q` — PASS (`42 passed`)
  - `python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/test_primary_prep_auto_import.py ansible_collections/tomazb/acm_switchover/tests/unit/test_backup_schedule_persistence.py ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_backup_schedule.py -q` — PASS (`31 passed`)
  - `python -m pytest ansible_collections/tomazb/acm_switchover/tests/integration/test_switchover_roles.py -k primary_prep -q` — PASS (`2 passed, 8 deselected`)
- Tool checks:
  - `python -m pip show mutmut` — no global package on this shell path
  - `.venv/bin/python -m pip show mutmut` — `mutmut 3.6.0`
  - `.venv/bin/mutmut --help` — available commands include `run`, `results`, `show`
- Focused mutation confirmation:
  - temporary `[mutmut]`: `source_paths = modules/primary_prep.py`, `pytest_add_cli_args_test_selection = tests/test_primary_prep.py`, `also_copy = lib/` and `modules/`, exclusions limited to `raise .*Error\(` and `logger\..*\(`
  - `rm -rf mutants/`
  - `.venv/bin/mutmut run`
  - `.venv/bin/mutmut results | head -40`
  - restored the default `[mutmut]` target afterward

Final focused mutation counts:

- Total: `249`
- Killed: `213`
- Survived: `36`
- Not checked/timeouts/suspicious: `0`

Resolved groups:

- BackupSchedule wrong-resource targeting: resolved. The listed wrong-target
  mutants (`_pause_backup_schedule__mutmut_2`, `3`, `10`, `73`, `78`, `84`) now
  have exit code `1`. Python tests now pin list, patch, and delete calls to the
  BackupSchedule API group/version/plural, backup namespace, resource name, and
  patch payload. Collection tests now pin the matching `pause_backups.yml`
  patch/delete task API version, kind, namespace default, primary kubeconfig,
  primary context, and state.
- `saved_backup_schedule` persistence: resolved. The persistence mutants
  (`_pause_backup_schedule__mutmut_50`, `51`, `52`, `53`, `64`) now have exit
  code `1`. Python tests assert the saved schedule is written before patch/delete
  mutation and that already-paused reruns persist only when no saved schedule is
  present.
- Thanos compactor false-success/skipped-verification: resolved. The targeted
  Thanos mutants (`_scale_down_thanos_compactor__mutmut_8`, `9`, `10`, `11`,
  `12`, `35`, `40`) now have exit code `1`. Python tests now pin the pod
  verification namespace and selector in both the wait condition and timeout
  recheck, and directly assert 404 maps to `SwitchoverError`. Collection tests
  now pin the pod query namespace plus primary hub kubeconfig/context.

Remaining survivors from this focused run are outside the top-three resolution
scope. They remain classified according to the baseline table: Argo CD success
logging and optional `StateManager.step()` logger wiring are incidental/noisy;
disable-auto-import survivors are incidental/noisy malformed-fixture or
bookkeeping cases; execute-mode Argo CD step persistence and secondary-hub Argo
CD pause participation remain missing-scenario follow-ups. The residual
`_pause_backup_schedule` survivors are helper context-string/logging mutations
around multiple-schedule diagnostics rather than the resolved wrong-resource or
state-persistence contracts.

### Next action recommendation

1. The original top three shared-behavior survivor groups are resolved by the
   2026-06-11 test-only pass above.
2. Treat the disable-auto-import and Argo CD `run_id` logging survivors as
   baseline noise unless a later review shows operator-visible impact beyond
   logging or malformed fixture shapes.
3. Add focused coverage for execute-mode Argo CD step persistence and
   secondary-hub pause coordination in a separate parity-aware pass.
4. Keep residual `_pause_backup_schedule` helper context-string/logging
   survivors lower priority unless diagnostic wording becomes part of an
   operator-facing contract.

## Phase 2 Baseline Record: `modules/finalization.py`

- Date: 2026-06-11
- Source target: `modules/finalization.py`
- Focused Python test target: `tests/test_finalization.py tests/test_backup_schedule.py`
- Provenance:
  - base branch: `ansible`
  - baseline/fix branch: `mutation/finalization-baseline`
  - baseline branch HEAD commit: `5b85a666c26332073090cb9a2f26a060c7ef0664`
  - worktree: `<repo-root>/.worktrees/mutation-finalization-baseline`

### Unmutated baseline lanes

- Python baseline: `.venv/bin/python -m pytest tests/test_finalization.py tests/test_backup_schedule.py -q` - PASS (`102 passed`)
- Collection unit/contracts lane: `PYTHONPATH=. .venv/bin/python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/test_finalization_verification.py ansible_collections/tomazb/acm_switchover/tests/unit/test_finalization_old_hub_safety.py ansible_collections/tomazb/acm_switchover/tests/unit/test_finalization_old_hub_parity.py ansible_collections/tomazb/acm_switchover/tests/unit/test_restore_only_recovery_contracts.py ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_backup_schedule.py -q` - PASS (`78 passed`)
- Collection finalization integration lane: `PYTHONPATH=. .venv/bin/python -m pytest ansible_collections/tomazb/acm_switchover/tests/integration/test_switchover_roles.py -k finalization -q` - PASS (`2 passed, 8 deselected`)
- Collection restore-only integration lane: `PYTHONPATH=. .venv/bin/python -m pytest ansible_collections/tomazb/acm_switchover/tests/integration/test_restore_only_role.py -q` - PASS (`1 passed`)

### Mutation baseline

- Tool/version: `mutmut 3.6.0`
- Config target: temporary `setup.cfg` `[mutmut]` with `source_paths = modules/finalization.py`, Python test selection above, `also_copy = lib/` and `modules/`, and exclusions limited to `raise .*Error\(` plus `logger\.`
- Baseline command: `rm -rf mutants/ && .venv/bin/mutmut run`
- Result source of truth: `mutants/modules/finalization.py.meta`
- Counts: total `1170`, killed `440`, survived `688`, timeouts `42`, not_checked/suspicious `0`
- Survivor-heavy functions:
  - `_verify_old_hub_state`: `81`
  - `_disable_observability_on_old_hub`: `68`
  - `_wait_for_backup_schedule_deletion`: `60`
  - `_check_velero_logs_for_backup`: `53`
  - `_archive_restore_details`: `52`
  - `_cleanup_restore_resources`: `45`
  - `_backup_effective_timestamp`: `43`
  - `_wait_for_primary_restore_deletion`: `43`
  - `finalize`: `42`
  - `_parse_cron_interval_seconds`: `41`

### Finalization survivor resolution: top three groups (2026-06-11)

Scope: test-only resolution for the first three high-risk survivor groups
selected by the required priority order. No production behavior, CLI surface,
report schema, parity status, or operator workflow changed.

Selected groups:

- `_disable_observability_on_old_hub`: old-hub MCO delete target, dry-run
  safety, and termination wait target. Python tests now pin the list/delete API
  group, version, plural, resource name, delete timeout, pod namespace, wait
  description, wait timeout, and wait interval.
- `_cleanup_restore_resources`: restore cleanup list/delete target safety.
  Python tests now pin the Restore API group, version, plural, backup namespace,
  restore name, and delete timeout.
- `_wait_for_backup_schedule_deletion`: BackupSchedule repair/continuity polling
  and fail-closed UID safety. Python tests now execute the wait callback, pin the
  BackupSchedule API group/version/plural/name/namespace, assert pending detail,
  assert cache invalidation on final re-read, and assert a changed UID raises.

Post-fix verification:

- `.venv/bin/python -m pytest tests/test_finalization.py tests/test_backup_schedule.py -q` - PASS (`104 passed`)
- `PYTHONPATH=. .venv/bin/python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/test_finalization_verification.py ansible_collections/tomazb/acm_switchover/tests/unit/test_finalization_old_hub_safety.py ansible_collections/tomazb/acm_switchover/tests/unit/test_finalization_old_hub_parity.py ansible_collections/tomazb/acm_switchover/tests/unit/test_restore_only_recovery_contracts.py ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_backup_schedule.py -q` - PASS (`78 passed`)
- `PYTHONPATH=. .venv/bin/python -m pytest ansible_collections/tomazb/acm_switchover/tests/integration/test_switchover_roles.py -k finalization -q` - PASS (`2 passed, 8 deselected`)
- `PYTHONPATH=. .venv/bin/python -m pytest ansible_collections/tomazb/acm_switchover/tests/integration/test_restore_only_role.py -q` - PASS (`1 passed`)
- `rm -rf mutants/ && .venv/bin/mutmut run` - total `1170`, killed `551`, survived `577`, timeouts `42`

Resolved-group count movement:

- `_disable_observability_on_old_hub`: survived `68` -> `31`
- `_cleanup_restore_resources`: survived `45` -> `13`
- `_wait_for_backup_schedule_deletion`: survived `60` -> `18`

Remaining survivors from this focused run are outside the top-three resolution
scope. The largest remaining groups are `_verify_old_hub_state`,
`_check_velero_logs_for_backup`, `_archive_restore_details`,
`_backup_effective_timestamp`, and `_wait_for_primary_restore_deletion`.

### Next action recommendation

1. Treat the selected finalization groups as improved, not fully exhausted; their
   remaining survivors are lower-risk helper/logging/default-shape mutations
   compared with the now-pinned API targets.
2. Prioritize the next pass on `_verify_old_hub_state` and
   `_wait_for_primary_restore_deletion` because they sit in old-hub secondary
   safety.
3. Review `_check_velero_logs_for_backup` and `_archive_restore_details` as
   diagnostic/helper survivors before spending more mutation budget on them.

## Phase 1 Baseline Record: `lib/rbac_validator.py`

- **Source target:** `lib/rbac_validator.py`
- **Baseline commit (before triage):** `98a22821` (`ansible` branch)
- **Python baseline command/result:** `python -m pytest tests/test_rbac_validator.py tests/test_rbac_collection_parity.py -q` → `105 passed`
- **Mutation tool/version:** `mutmut 3.6.0`
- **Mutation command:** `mutmut run` (using a temporary `[mutmut]` config: `source_paths = lib/rbac_validator.py`, `pytest_add_cli_args_test_selection = tests/test_rbac_validator.py`, `also_copy = lib/`, `do_not_mutate_patterns = raise .*Error\(` and `logger\.(info|debug|warning|error|exception)\(`; `tests/test_rbac_collection_parity.py` is excluded from the mutant workspace because it fails to collect there with `ModuleNotFoundError: ansible_collections`, so it only runs in the plain Python baseline above, not under mutation)
- **Counts before triage:** `total 657 / killed 453 / survived 204 / not_checked 0`
- **Counts after triage (this record):** `total 657 / killed 476 / survived 181 / not_checked 0`
- `setup.cfg` was restored to its `lib/validation.py` scope after this baseline; reruns must reconstruct the config above.

### Triaged and killed (23 mutants, 8 new tests in `tests/test_rbac_validator.py`)

| Survivor | Class | Fix |
| --- | --- | --- |
| `check_permission__mutmut_1` (`cache_key = (api_group, resource, verb, namespace)` → `None`) | missing assertion | `test_check_permission_cache_is_keyed_by_full_permission_tuple` — two distinct permission checks on one validator instance must each hit the API and return their own result; a collapsed cache key would leak the first result onto the second. |
| `check_permission__mutmut_13`/`_38` (`resource_name = resource` → `None`; `resource_attributes=resource_attrs` → `None`) | missing assertion | `test_check_permission_builds_resource_attributes_for_plain_resource` — asserts the exact `V1ResourceAttributes`/`V1SelfSubjectAccessReviewSpec` call args for the common non-subresource path (only the subresource-splitting path had this assertion before). |
| `validate_cluster_permissions__mutmut_60` (`skip_observability and "observability" in api_group` → `or`) | missing scenario | `test_validate_cluster_permissions_decommission_checks_mco_delete_when_observability_present` — isolates `include_decommission=True` from `include_old_hub_finalization`, which previously masked this mutant by checking the same permission through a different code path. |
| `validate_cluster_permissions__mutmut_78`/`_77`/`_103`/`_102` (`all_valid = False` → `True`/`None` in the decommission and old-hub-finalization blocks) | missing assertion | `test_validate_cluster_permissions_decommission_failure_sets_all_valid_false` and the old-hub-finalization counterpart — each denies exactly one permission in its own block only, so the base cluster-permission loop's own `all_valid = False` can't mask an inversion deeper in the function. |
| `validate_cluster_permissions__mutmut_92`/`_67` (dedupe `continue` → `break`) and `_93` (`checked_extra_permissions.add(permission_key)` → `add(None)`) | missing scenario | `test_validate_cluster_permissions_decommission_dedupe_does_not_skip_later_verbs` and the old-hub-finalization counterpart — monkeypatch `DECOMMISSION_PERMISSIONS`/`OLD_HUB_FINALIZATION_PERMISSIONS` with an artificial duplicate verb followed by a distinct verb, since the real permission tables have no in-list duplicates today. `break` would silently stop checking remaining verbs after a duplicate; `add(None)` would break the dedupe match on a real repeat. |
| `x_validate_decommission_permissions__mutmut_3` (`RBACValidator(primary_client)` → `RBACValidator(None)`) | missing assertion | Added `mock_validator_class.assert_called_once_with(mock_primary_client)` to `test_validate_decommission_permissions_uses_dedicated_validation_path` — the constructor arg was never asserted even though the test already patches `RBACValidator`. |

### Residual survivors (181), sampled by cluster

| Survivor group | Representative survivors | Class | Why it survived |
| --- | --- | --- | --- |
| `generate_permission_report` banner/section text (49) | string-literal case/spacing/`"="`-count mutants | equivalent/incidental | Report text is a human-readable log/CLI artifact, not a pinned operator-facing contract (no schema in `docs/ansible-collection/parity-matrix.md` requires exact wording). Sampled several representative mutants directly; all were cosmetic. |
| `validate_decommission_permissions` (both the `RBACValidator` method, 38, and the module-level wrapper, ~35) report/banner text | equivalent/incidental | Same pattern as above — banner strings, blank-line separators, section headers. Sampled the module-level wrapper's mutants directly (ids 1, 9, 10, 12, 13, 16, 17, 19, 21, 22, 24, 26–30); all confirmed cosmetic. |
| `validate_decommission_permissions` default `skip_observability: bool = False` → `True` (mutmut_1) | equivalent | Every real caller (`acm_switchover.py:944`) and every test passes `skip_observability` explicitly; the default is unreachable in practice. |
| `validate_all_permissions` (13), `check_permission` remaining (11, mostly `split`/`rsplit`/maxsplit and default-message-string variants) | equivalent/incidental | `resource.split("/", 1)` vs `rsplit`/no-maxsplit/`maxsplit=2` only diverge on resources with 2+ slashes, which never occurs (Kubernetes subresources are always exactly one slash). Default-string mutants (`"Permission denied"` casing, `scope` fallback text) only affect text inside an already-matched exception message prefix. |
| `_validate_hub` (10) | not sampled this pass | deferred — smaller cluster, lower apparent risk from the `generate_permission_report`/`validate_decommission_permissions` sampling pattern; revisit before closing this target out entirely. |

### Next action recommendation

1. Treat this baseline as triaged for its highest-value survivors, not exhausted.
   The killed set above covers every RBAC-safety-relevant mutant found during
   review (cache collisions, silently-None resource attributes, an `and`/`or`
   skip-logic inversion, `all_valid` sign flips, and two dedupe-guard
   `continue`/`break` swaps).
2. `_validate_hub` (10 survivors) was not sampled — do that before treating
   `lib/rbac_validator.py` as fully triaged.
3. Do not chase the `generate_permission_report`/`validate_decommission_permissions`
   banner-text clusters (≈120 combined survivors) without an explicit decision to
   pin exact report wording as an operator-facing contract; today it is not one.

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
