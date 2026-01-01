# Plan: Test and Validate ACM Switchover in Test Environment (Complete)

Comprehensive testing strategy covering all Bash scripts, Python CLI tools, forward/reverse switchover, decommission workflow, and state resume capability. Testing against mgmt1/mgmt2 hubs and prod1-prod3 managed clusters. KVM snapshots enable environment reset between test phases. Each bug fix increments patch version within 1.4.x (never 1.5.0+).

## Steps

1. **Verify and lock versions at 1.4.x** - Confirm `scripts/constants.sh` `SCRIPT_VERSION` and `lib/__init__.py` `__version__` are `1.4.0`; ensure `README.md` badge matches; block any 1.5.0 bumps during entire testing cycle.

2. **Run unit/integration test suite** - Activate `.venv`, execute `./run_tests.sh`; fix test failures, increment to 1.4.1 if needed; update `CHANGELOG.md` `[Unreleased]` section with fixes.

3. **Execute hub discovery and preflight scripts** - Run `./scripts/discover-hub.sh --contexts mgmt1,mgmt2,prod1,prod2,prod3 --verbose` and `./scripts/preflight-check.sh --primary-context mgmt1 --secondary-context mgmt2 --method passive`; fix script issues, batch into patch version.

4. **Validate Python CLI tools (non-destructive)** - Run `python check_rbac.py --primary-context mgmt1 --secondary-context mgmt2`, then `python acm_switchover.py --validate-only` and `--dry-run` with `--method passive --old-hub-action secondary`; fix CLI/validation errors.

5. **Execute forward switchover mgmt1→mgmt2** - Run full switchover `python acm_switchover.py --primary-context mgmt1 --secondary-context mgmt2 --method passive --old-hub-action secondary --verbose`; verify all phases complete; fix runtime errors.

6. **Run postflight with managed cluster verification** - Execute `./scripts/postflight-check.sh --new-hub-context mgmt2 --old-hub-context mgmt1`; verify prod1, prod2, prod3 show `ManagedClusterConditionAvailable=True` on mgmt2; fix connection/klusterlet issues.

7. **Execute reverse switchover mgmt2→mgmt1** - Run preflight with swapped contexts, then `python acm_switchover.py --primary-context mgmt2 --secondary-context mgmt1 --method passive --old-hub-action secondary --verbose`; verify bidirectional capability works; fix any asymmetric issues.

8. **Run postflight after reverse switchover** - Execute `./scripts/postflight-check.sh --new-hub-context mgmt1 --old-hub-context mgmt2`; verify prod1-prod3 reconnected to mgmt1; confirm state files track both directions correctly.

9. **🔄 Revert to Snapshot: "mgmt1 hub with prod1-prod3 attached"** - Reset environment to clean state before decommission testing.

10. **Execute forward switchover for decommission test** - Run `python acm_switchover.py --primary-context mgmt1 --secondary-context mgmt2 --method passive --old-hub-action secondary --verbose`; wait for completion.

11. **Test decommission workflow** - Run `python acm_switchover.py --primary-context mgmt1 --decommission --non-interactive --verbose`; verify ACM components removed from mgmt1; fix decommission-specific issues.

12. **🔄 Revert to Snapshot: "mgmt1 hub with prod1-prod3 attached"** - Reset environment for state resume testing.

13. **Test state resume capability** - Start switchover, interrupt mid-phase (e.g., Ctrl+C after PRIMARY_PREP begins); run same command again to verify `StateManager` resumes from saved checkpoint; test `--reset-state` flag to verify fresh start; verify `./show_state.py` shows correct phase.

14. **Test auxiliary tools** - Run `./show_state.py --list` and inspect state files; test `./scripts/generate-sa-kubeconfig.sh`; verify `./scripts/install-completions.sh test-completion`.

15. **Final regression and version finalization** - Re-run `./run_tests.sh`; confirm final patch version (e.g., 1.4.3) consistent across `scripts/constants.sh`, `lib/__init__.py`, `README.md`; update `CHANGELOG.md`; create and push git tag `v1.4.x`.

## KVM Snapshot Usage

| Snapshot Name | State | Use Before |
|---------------|-------|------------|
| **"mgmt1 hub with prod1-prod3"** | mgmt1=primary hub, prod1-prod3 attached | Steps 9, 12, or any restart |
| **"mgmt1 hub without clusters"** | mgmt1=hub, no managed clusters | Alternative testing scenarios |

Snapshot revert will be requested before steps 9 and 12, or whenever a test corrupts the environment unexpectedly.

## Testing Phases Summary

| Phase | Primary Hub | Secondary Hub | Action | Snapshot After |
|-------|-------------|---------------|--------|----------------|
| Forward | mgmt1 | mgmt2 | Switchover + postflight | - |
| Reverse | mgmt2 | mgmt1 | Switchover + postflight | Revert |
| Decommission | mgmt1 (old) | - | Remove ACM | Revert |
| State Resume | mgmt1 | mgmt2 | Interrupt + resume | - |

## Further Considerations

1. **Snapshot timing** - Creating an additional snapshot after successful forward switchover (mgmt2 as hub) could speed up reverse/decommission testing iterations.
2. **Parallel managed cluster checks** - Consider scripting `kubectl get managedclusters` across all contexts to quickly verify cluster attachment states between tests.
3. **Log collection** - Capture verbose output and state files from each test phase for debugging and documentation purposes.
