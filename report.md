# Deep Findings Report

## Scope

This report covers a deep analysis of:

- Code smells and maintainability
- Performance and reliability risks
- Security issues
- Logic and state-machine correctness

Target areas included:

- `acm_switchover.py`
- `modules/finalization.py`
- `modules/post_activation.py`
- `lib/utils.py`
- `lib/kube_client.py`
- `scripts/setup-rbac.sh`
- `scripts/generate-sa-kubeconfig.sh`
- `deploy/rbac/clusterrole.yaml`

## Method

- End-to-end control-flow review for switchover phases and resume logic
- Focused threat review of shell argument handling and credential output paths
- Poll/retry path review for expensive or brittle loops
- State durability and error-recording behavior review
- Validation via repository tests (`./run_tests.sh`)

## Findings Summary

| Severity | Category | Finding | Evidence | Status |
|---|---|---|---|---|
| High | Logic | `--validate-only` could execute mutating phases depending on stored state | `acm_switchover.py` phase loop selected handlers before validate-only return | Fixed |
| High | Logic | Unhandled state phases could fall through to `COMPLETED` without running workflow | `Phase.SECONDARY_VERIFY` existed but was not runnable in main switchover flow | Fixed |
| High | Logic | Resume from `FAILED` with non-runnable phase could incorrectly complete | Failed-state resume accepted phase without runnable-phase guard | Fixed |
| Medium | Reliability | Duplicate phase failure recording caused noisy state errors and extra flushes | Module-level `state.add_error(...)` plus orchestrator `_fail_phase(...)` | Fixed |
| High | Security | Shell argument-splitting risk in kubectl invocations | Unquoted string expansion (`kubectl $KUBECTL_ARGS`) in setup scripts | Fixed |
| Medium | Security | Generated kubeconfigs lacked explicit file mode hardening | Kubeconfig files written without strict permission set | Fixed |
| Medium | Reliability | Restore cleanup used brittle string matching for not-found errors | `"not found" in str(e).lower()` in finalization restore cleanup | Fixed |
| Medium | Logic | MCH pod health check treated all non-Running pods as unhealthy | Succeeded job pods could trigger false unhealthy state | Fixed |
| Medium | Reliability | Backup polling loop aborted on transient API errors | No tolerant handling around backup list calls | Fixed |
| Medium | Security/Policy | Operator ClusterRole includes broad delete permissions by default | `deploy/rbac/clusterrole.yaml` delete verbs for managedclusters/mch/mco | Open |
| Low | Documentation | Security docs mention hostname verification CLI flag not exposed in parser | `SECURITY.md` vs CLI parser mismatch | Open |
| Low | Performance | Immediate config/step persistence may cause extra fsync overhead | `StateManager.set_config/mark_step_completed` immediate persist pattern | Open |

## Implemented Remediations

### 1) Switchover state-machine hardening

Updated `acm_switchover.py`:

- `--validate-only` now executes preflight directly and exits from that path only.
- Completed-state handling now returns success for recent completed runs instead of flowing through phase dispatch.
- Failed-state resume now only accepts runnable retry phases (`PREFLIGHT`, `PRIMARY_PREP`, `ACTIVATION`, `POST_ACTIVATION`, `FINALIZATION`).
- Added runnable-phase guard before dispatch; unrunnable phases now fail fast instead of silently completing.
- Added explicit no-phase-ran guard.

### 2) Duplicate failure recording reduction

Updated `_fail_phase(...)` in `acm_switchover.py`:

- Avoids adding another error entry when the last recorded error is already for the same phase.
- Keeps phase transition to `FAILED` intact.

### 3) Script argument hardening

Updated shell scripts to use argument arrays:

- `scripts/setup-rbac.sh`
  - Replaced string-based `KUBECTL_ARGS` with array-based args.
  - Updated all kubectl invocations to `kubectl "${KUBECTL_ARGS[@]}" ...`.
- `scripts/generate-sa-kubeconfig.sh`
  - Replaced `KUBECTL_CONTEXT_ARGS` string with array-based args.
  - Updated kubectl usage accordingly.

### 4) Kubeconfig file permission hardening

Updated `scripts/setup-rbac.sh`:

- After kubeconfig generation, applies `chmod 600` to generated kubeconfig files.

### 5) Finalization robustness improvements

Updated `modules/finalization.py`:

- `_cleanup_restore_resources(...)` now handles `ApiException(status=404)` explicitly instead of string matching.
- `_verify_new_backups(...)` now tolerates transient `ApiException` from backup listing and continues polling.
- MultiClusterHub pod health check now allows pod phase `Succeeded` to avoid false unhealthy results.

## Validation Results

Executed:

- `./run_tests.sh`

Result:

- Majority of tests passed.
- 3 existing failures were observed in script integration tests unrelated to the remediated switchover logic and script argument hardening paths:
  - `tests/test_scripts_integration.py::test_preflight_success_passive_method`
  - `tests/test_scripts_integration.py::test_preflight_success_full_method`
  - One additional script integration preflight case in the same suite

## Remaining Recommendations

1. Split operator RBAC into baseline and decommission-extended variants to enforce least privilege by default.
2. Align `SECURITY.md` with current CLI behavior for hostname verification controls.
3. Consider write batching for non-critical state updates in high-churn paths while preserving crash safety.
4. Add targeted tests for:
   - validate-only behavior with non-INIT state
   - unrunnable phase rejection behavior
   - shell argument handling with spaces/special characters in context or kubeconfig paths
