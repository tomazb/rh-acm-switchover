# Detailed Analysis

## Scope

This report reviews the current branch against `origin/main` and focuses on real executable issues:

- logic errors
- regressions
- unsafe operational behavior
- CI/build regressions
- meaningful code smells that affect diagnosis or correctness

The goal is to give a follow-up agent enough context to fix the issues without having to repeat the full investigation.

## Concrete Checklist

Use `docs/plans/2026-03-28-branch-fix-checklist.md` as the execution checklist.

- [x] Task 1: Fix completed-state timestamp mutation in validate-only mode (`F1`)
- [x] Task 2: Fix shell Argo CD detection coverage and error handling (`F2`, `F3`)
- [x] Task 3: Sanitize setup-generated kubeconfig filenames (`F4`)
- [x] Task 4: Restore consistent preflight and RBAC failure handling (`F6`, `F7`, `F8`)
- [x] Task 5: Replace unsupported container ignore handling with Docker-supported ignore files (`F5`)
- [x] Task 6: Make Argo CD RBAC requirements conditional for vanilla installs (`F9`)
- [x] Task 7: Run targeted tests for each task, then `./run_tests.sh`

## Priority Summary

| ID | Severity | Title |
|---|---|---|
| F1 | High | `--validate-only` refreshes completed state and defeats stale-state protection |
| F2 | High | Shell Argo CD detection misses watched-namespace Applications on operator installs |
| F3 | High | Shell Argo CD discovery hides auth/API failures as "CRD not found" |
| F4 | High | `--setup` writes kubeconfigs using unsanitized context names |
| F5 | Medium | Docker/Buildx builds ignore `.containerignore`, so build context regressed |
| F6 | Medium | Preflight RBAC/API failures can now escape as top-level exceptions |
| F7 | Medium | `check_rbac.py --role validator --include-decommission` can return a false green |
| F8 | Medium | Generic `_fail_phase()` errors overwrite more actionable root-cause messages |
| F9 | Medium | Argo CD RBAC validation over-requires `argocds` permissions on vanilla installs |

## F1 - `--validate-only` refreshes completed state and defeats stale-state protection

- Severity: High
- Primary locations:
  - `acm_switchover.py:340`
  - `acm_switchover.py:403`
  - `acm_switchover.py:501`
  - `lib/utils.py:367`
  - `lib/utils.py:393`

### What changed

The branch explicitly allows `--validate-only` to run preflight even when the saved state is already `COMPLETED`. It does this by skipping the normal completed-state return path, running `_run_phase_preflight(...)`, and then restoring the original phase in a `finally` block via `state.set_phase(saved_phase)`.

### Why this is a real bug

`StateManager.set_phase()` always flushes state and updates `last_updated`. That means a validate-only run against a previously completed switchover rewrites the state file and makes it look fresh, even though no switchover work actually ran.

This undermines the stale-completed-state protection at the top of `run_switchover()`. A user can validate an old completed state, then immediately run the real command and no longer get the stale-state warning/reset path they should have gotten.

### User-visible impact

- Old completed runs can be made to look recent by a harmless-looking validation command.
- The next actual switchover attempt can incorrectly no-op as "already completed" instead of forcing the operator to reset or use `--force`.
- This is especially risky for resume/rerun safety.

### Likely fix direction

- Preserve the original phase for validate-only without refreshing `last_updated`.
- Do not use `set_phase()` to restore the saved phase in validate-only mode.
- Possible approaches:
  - restore the phase in memory without flushing
  - restore both phase and the original `last_updated`
  - avoid mutating phase at all for validate-only preflight

### Tests to add or tighten

- Extend `tests/test_main.py` to assert that validate-only on a completed state does not update `last_updated`.
- Add a test proving that a stale completed state remains stale after validate-only.

## F2 - Shell Argo CD detection misses watched-namespace Applications on operator installs

- Severity: High
- Primary locations:
  - `scripts/lib-common.sh:1082`
  - `scripts/lib-common.sh:1098`
  - `scripts/lib-common.sh:1132`
  - `scripts/preflight-check.sh:711`
  - `scripts/postflight-check.sh:636`

### What changed

The shell Argo CD detection path treats operator installs specially: when `argocds.argoproj.io` exists, it lists Argo CD instances and then scans `applications.argoproj.io` only within each Argo CD instance namespace.

### Why this is a real bug

Operator-based Argo CD can watch namespaces other than its own control-plane namespace. In that configuration, Applications may live outside the Argo CD namespace entirely.

The current shell logic never performs a cluster-wide Application scan when operator instances exist, so it misses those watched-namespace Applications.

The Python implementation does not have this limitation because it can list Applications cluster-wide.

### User-visible impact

- `scripts/preflight-check.sh` and `scripts/postflight-check.sh` can falsely claim that no ACM-touching Argo CD Applications exist.
- Operators may proceed without pausing/scoping GitOps, increasing the chance that Argo CD reverts switchover changes.

### Likely fix direction

- For shell detection, do not restrict operator installs to namespace-local Application scans.
- Either:
  - always scan Applications cluster-wide, or
  - scan cluster-wide whenever operator instances exist, or
  - detect watched namespaces explicitly and include them.
- Keep shell and Python detection behavior aligned.

### Tests to add or tighten

- Add an integration test for operator-installed Argo CD where:
  - `argocds.argoproj.io` exists
  - Applications exist outside the Argo CD namespace
  - ACM-touching resources are present in `status.resources`
- The expected result should be a warning, not a clean pass.

## F3 - Shell Argo CD discovery hides auth/API failures as "CRD not found"

- Severity: High
- Primary locations:
  - `scripts/lib-common.sh:1069`
  - `scripts/preflight-check.sh:711`
  - `scripts/postflight-check.sh:636`

### What changed

The shell logic uses `kubectl get crd applications.argoproj.io` and treats any non-zero exit status as `Argo CD Applications CRD not found (skipping ArgoCD GitOps check)`.

### Why this is a real bug

A non-zero exit here does not only mean `CRD absent`. It also includes:

- 401/403 authorization failures
- transient API failures
- cluster/API unavailability
- kubeconfig/context problems

The Python preflight path distinguishes authorization failures and surfaces them as RBAC-related issues. The shell path silently converts them into `not installed`.

### User-visible impact

- Users can get a false clean result when they actually lack permission to inspect Argo CD.
- Real GitOps drift risk is hidden behind a misleading informational message.

### Likely fix direction

- Differentiate:
  - CRD genuinely absent
  - RBAC denied
  - transient/API failure
- If needed, inspect stderr/exit conditions or perform a more explicit probe sequence.
- At minimum, do not report `CRD not found` for all failures.

### Tests to add or tighten

- Add shell/integration coverage for:
  - forbidden CRD access
  - transient kubectl failure
  - real CRD absence
- These must produce distinct outcomes.

## F4 - `--setup` writes kubeconfigs using unsanitized context names

- Severity: High
- Primary locations:
  - `lib/validation.py:51`
  - `lib/validation.py:164`
  - `lib/validation.py:311`
  - `scripts/setup-rbac.sh:415`
  - `scripts/setup-rbac.sh:455`

### What changed

Context validation was intentionally widened to allow realistic OpenShift/Kubernetes context names containing `/` and `:`. That part is correct.

However, `scripts/setup-rbac.sh` still uses raw `${CONTEXT}` directly in output filenames like:

- `${OUTPUT_DIR}/${CONTEXT}-operator.yaml`
- `${OUTPUT_DIR}/${CONTEXT}-validator.yaml`

### Why this is a real bug

A context such as `admin/api-ci-aws` is now valid input, but becomes a nested path when interpolated into a filename. Only the base output directory is created, not arbitrary subdirectories implied by the context string.

There is already a sanitizer helper available: `InputValidator.sanitize_context_identifier(...)`.

### User-visible impact

- `--setup` can fail unexpectedly for valid contexts.
- Kubeconfigs can be written to unintended nested locations.
- Validation and summary output may reference paths that were never created successfully.

### Likely fix direction

- Sanitize the context before using it in filenames in `scripts/setup-rbac.sh`.
- Use the same sanitized identifier consistently for:
  - generated kubeconfig paths
  - validation paths
  - summary output
  - `Next steps` examples

### Tests to add or tighten

- Add script tests covering contexts with:
  - `/`
  - `:`
  - spaces or other characters that sanitize
- Assert that output filenames are safe and stable.

## F5 - Docker/Buildx builds ignore `.containerignore`, so build context regressed

- Severity: Medium
- Primary locations:
  - `.containerignore`
  - `.github/workflows/ci-cd.yml:305`
  - `.github/workflows/security.yml:149`

### What changed

CI switched to Docker Buildx using:

- `context: .`
- `file: ./container-bootstrap/Containerfile`

A repo-root `.containerignore` was added, but there is no `.dockerignore` and no `Containerfile.dockerignore`.

### Why this is a real bug/regression

Docker and Buildx do not honor `.containerignore`. They honor `.dockerignore` or a file-specific ignore file such as `Containerfile.dockerignore`.

So the full repository, including local untracked files and temp artifacts, is now sent to the build context.

### User-visible impact

- Slower builds and larger CI context uploads.
- Higher chance of accidentally including temp/local files in remote build context.
- Mismatch between intended and actual container build behavior.

### Likely fix direction

- Replace `.containerignore` with a Docker-supported ignore file.
- Validate behavior for both local `docker build` and GitHub Actions Buildx usage.

### Tests to add or tighten

- CI sanity step asserting the expected ignore file exists.
- Optional smoke check that excluded paths are not present in build context.

## F6 - Preflight RBAC/API failures can now escape as top-level exceptions

- Severity: Medium
- Primary locations:
  - `modules/preflight_coordinator.py:89`
  - `modules/preflight_coordinator.py:130`
  - `modules/preflight_coordinator.py:138`
  - `acm_switchover.py:501`
  - `lib/kube_client.py:295`

### What changed

The branch narrowed preflight RBAC exception handling to `ValidationError`, but some calls that can fail now happen outside that protection:

- `namespace_exists(...)` checks used to decide whether observability RBAC can be skipped
- Argo CD discovery in `_get_effective_argocd_rbac_mode()`

### Why this is a real bug/regression

`namespace_exists()` ultimately calls Kubernetes APIs and can raise on non-404 failures. `_get_effective_argocd_rbac_mode()` also re-raises non-401/403 discovery failures.

Because those calls are outside or before the narrowed `except ValidationError`, some routine API/auth failures no longer get summarized as normal preflight validation failures.

### User-visible impact

- Instead of a normal preflight summary, operators can get an unexpected top-level exception path.
- Failure-state handling becomes less consistent because the flow may exit outside the normal preflight failure reporting path.

### Likely fix direction

- Re-wrap the entire RBAC preflight block so routine API failures are converted into structured validation results.
- Keep the distinction between:
  - missing permissions
  - temporary API failure
  - missing CRDs/resources
  - coding/programming errors

### Tests to add or tighten

- Add coordinator tests where:
  - `namespace_exists()` raises `ApiException(500)`
  - Argo CD detection raises non-403 `ApiException`
  - the result is a failed validation summary rather than an uncaught exception

## F7 - `check_rbac.py --role validator --include-decommission` can return a false green

- Severity: Medium
- Primary locations:
  - `check_rbac.py:204`
  - `lib/rbac_validator.py:376`
  - `lib/validation.py:442`

### What changed

The main CLI/setup validation correctly rejects `--include-decommission` with validator role. The standalone checker does not.

Inside `RBACValidator`, validator mode silently skips decommission checks instead of rejecting the invalid combination.

### Why this is a real bug

A user can run:

- `check_rbac.py --role validator --include-decommission`

and receive a successful run that looks authoritative, even though decommission permissions were never actually validated.

### User-visible impact

- False operator confidence.
- Confusing mismatch between setup-mode validation and standalone checker behavior.

### Likely fix direction

Choose one consistent behavior and apply it everywhere:

- either reject the combination at argument parsing in `check_rbac.py`
- or make `RBACValidator` raise for validator plus include-decommission

Do not silently downgrade.

### Tests to add or tighten

- Add standalone checker tests asserting that validator plus include-decommission fails explicitly.

## F8 - Generic `_fail_phase()` errors overwrite more actionable root-cause messages

- Severity: Medium
- Primary locations:
  - `acm_switchover.py:372`
  - `acm_switchover.py:474`
  - `modules/primary_prep.py:155`
  - `modules/activation.py:195`
  - `modules/post_activation.py:107`
  - `modules/finalization.py:247`
  - `lib/utils.py:462`

### What changed

The phase modules already add detailed error entries to state, for example the specific `SwitchoverError` message. After returning `False`, `run_switchover()` now calls `_fail_phase()` with a generic wrapper message like:

- `Primary hub preparation failed!`
- `Secondary hub activation failed!`
- `Finalization failed!`

### Why this is a real bug/smell

Resume logic reads the last error entry from state. Because `_fail_phase()` appends a new generic message after the specific one, the most recent error shown to the operator is often less actionable than the real root cause.

### User-visible impact

- Resume banners and troubleshooting output become less useful.
- Important failure detail is still in the error list, but no longer the default or visible one.

### Likely fix direction

- Avoid appending a generic wrapper error when the phase module already recorded a specific one.
- Or preserve the specific error as the `last error` and only change phase state.
- Or teach resume output to prefer the most recent non-generic phase-specific message.

### Tests to add or tighten

- Add tests asserting that resume output shows the underlying root-cause message after a phase failure.

## F9 - Argo CD RBAC validation over-requires `argocds` permissions on vanilla installs

- Severity: Medium
- Primary locations:
  - `lib/rbac_validator.py:193`
  - `lib/rbac_validator.py:244`
  - `modules/preflight_coordinator.py:89`
  - `lib/argocd.py:192`
  - `tests/test_rbac_validator.py:131`

### What changed

The feature explicitly supports both:

- operator installs with `argocds.argoproj.io`
- vanilla installs with only `applications.argoproj.io`

But `ARGOCD_CHECK_CLUSTER_PERMISSIONS` always includes:

- `argoproj.io/argocds get`
- `argoproj.io/argocds list`

### Why this is a real bug/regression

Preflight enables Argo CD RBAC validation when Applications CRD exists on at least one hub. It does not require the `argocds` CRD to exist. On a vanilla Argo CD install, the feature should work without any `argocds` resource permissions.

Requiring those permissions contradicts the vanilla support model and forces broader-than-needed RBAC.

### User-visible impact

- `--argocd-check` or `--argocd-manage` can fail RBAC validation on legitimate vanilla Argo CD installs.
- Users may have to grant unnecessary access just to pass validation.

### Likely fix direction

- Make Argo CD RBAC requirements conditional on actual install type.
- If only Applications CRD exists, require only Application and CRD inspection permissions.
- Require `argocds` permissions only when operator-installed Argo CD is actually present.

### Tests to add or tighten

- Add tests for a vanilla Argo CD install where:
  - Applications CRD exists
  - `argocds` CRD does not
  - Argo CD RBAC validation still passes without `argocds` permissions

## Suggested Fix Order

1. Fix `F1` first because it affects state safety and rerun behavior.
2. Fix `F2` and `F3` next because they can hide GitOps drift risk in real operations.
3. Fix `F4` next because it breaks valid setup inputs.
4. Fix `F6`, `F7`, and `F8` together because they all affect operational error handling and diagnosability.
5. Fix `F5` and `F9` after that; both are important but less immediately safety-critical than the items above.

## Suggested Verification Plan

After fixes, run targeted checks first:

```bash
pytest tests/test_main.py -v
pytest tests/test_preflight_coordinator.py -v
pytest tests/test_rbac_validator.py -v
pytest tests/test_validation.py -v
pytest tests/test_argocd.py -v
pytest tests/test_argocd_manage_script.py -v
pytest tests/test_scripts.py -v
pytest tests/test_scripts_integration.py -v
```

Then run the default suite:

```bash
./run_tests.sh
```

## Notes / Non-findings

These looked suspicious at first but I did not classify them as bugs:

- The post-activation client handling changes in `modules/post_activation.py` look like a concurrency and safety improvement, not a regression.
- The stricter Python-side GitOps marker matching in `lib/gitops_detector.py` looks intentional and better than the shell-side implementation.
- The shell and Python behavior mismatch around GitOps detection is itself worth fixing, but the Python side appears to be the more correct reference implementation.
