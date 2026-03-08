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
| Medium | Security/Policy | Operator ClusterRole included broad delete permissions by default | `deploy/rbac/clusterrole.yaml` delete verbs for managedclusters/mch/mco | Fixed |
| Low | Documentation | Security docs mentioned hostname verification CLI flag not exposed in parser | `SECURITY.md` vs CLI parser mismatch | Fixed |
| Medium | Quality | Focused regression coverage was missing for newly hardened control-flow and finalization paths | `tests/test_main.py`, `tests/test_finalization.py`, `tests/test_rbac_integration.py`, `tests/test_scripts_integration.py` | Fixed |
| Low | Security/Docs | Direct stdout kubeconfig generation examples lacked secure redirection guidance | `scripts/generate-sa-kubeconfig.sh`, `scripts/README.md`, `docs/deployment/rbac-deployment.md` | Fixed |
| Low | Performance | Immediate config/step persistence may cause extra fsync overhead | `StateManager.set_config/mark_step_completed` immediate persist pattern | Open |

## Implemented Remediations

### 1) Switchover state-machine hardening

Updated `acm_switchover.py`:

- `--validate-only` now executes preflight directly and exits from that path only.
- Completed-state handling now returns success for recent completed runs instead of flowing through phase dispatch.
- Failed-state resume now rejects unknown phases but still supports legacy `SECONDARY_VERIFY` resume by routing it through the activation path.
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

### 6) Least-privilege RBAC split

Updated static and Helm RBAC manifests:

- `deploy/rbac/clusterrole.yaml` and the Helm operator ClusterRole template now keep baseline operator access non-destructive by default.
- Added opt-in decommission-only cluster-scoped delete permissions in:
  - `deploy/rbac/clusterrole-decommission.yaml`
  - `deploy/rbac/clusterrolebinding-decommission.yaml`
- Added Helm support for the same split via `rbac.includeDecommissionClusterRole`.
- Updated RBAC documentation to distinguish baseline switchover access from explicit decommission escalation.

### 7) Security documentation alignment

Updated `SECURITY.md` and related release notes:

- Removed the incorrect implication that a public `--disable-hostname-verification` CLI flag exists.
- Clarified that normal CLI usage keeps hostname verification enabled.
- Documented the insecure bypass as an internal `KubeClient` capability rather than a supported operator-facing switch.

### 8) Focused regression coverage and credential-output guidance

Updated tests and docs:

- Added targeted regression tests for:
  - validate-only behavior from a non-`INIT` phase
  - transient backup-list failures during finalization polling
  - `Succeeded` ACM pod handling during MultiClusterHub health verification
  - RBAC least-privilege and decommission-extension manifest structure
  - shell argument-array hardening in setup scripts
- Updated direct kubeconfig-generation help and documentation to use secure `umask 077` redirection guidance for stdout-generated kubeconfigs.

## Validation Results

Executed:

- `pytest tests/test_rbac_integration.py -q`
- `pytest tests/test_main.py tests/test_finalization.py tests/test_scripts_integration.py -q`
- `pytest tests/test_scripts_integration.py tests/test_rbac_integration.py -q`

Result:

- All focused verification passed.
- Verified counts:
  - `tests/test_rbac_integration.py`: 39 passed
  - `tests/test_main.py` + `tests/test_finalization.py` + `tests/test_scripts_integration.py`: 106 passed
  - final script/RBAC recheck: 49 passed
- No linter diagnostics were reported for the touched files during the final pass.
- Full-suite execution via `./run_tests.sh` was not re-run as part of this follow-up.

## Remaining Recommendations

1. Consider write batching for non-critical state updates in `lib/utils.py` high-churn paths while preserving current crash-safety guarantees and existing persistence semantics.
2. If broader confidence is needed before release, run the full suite (`./run_tests.sh`) to validate there are no unrelated regressions outside the focused areas above.
