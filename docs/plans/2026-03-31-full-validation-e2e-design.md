# Full Validation E2E Test Suite — Design

**Date**: 2026-03-31
**Goal**: Deep end-to-end testing of ACM switchover tool against real clusters (mgmt1, mgmt2), with and without Argo CD management, validating recent branch changes and building broader release confidence.

## Scope

- Validate recent state handling and restore discovery changes
- Exercise the full CLI path via subprocess + pexpect
- Test three Argo CD variants: none, pause-only, pause+resume
- Cross-validate with shell scripts (discover-hub, preflight, postflight, argocd-manage)
- Cross-validate with Python utilities (check_rbac.py, show_state.py)
- 4-hour soak test with rotating Argo CD modes

## Approach

Extend the existing pytest E2E framework (`tests/e2e/`) with a new test file. Use subprocess invocation of `acm_switchover.py` (not the in-process orchestrator) to test the full CLI path including Argo CD flags. Use pexpect to handle interactive confirmation prompts.

## Test File

`tests/e2e/test_e2e_full_validation.py`

## Execution Phases

Phases run sequentially within a single test class. Later phases skip if prerequisites failed.

| Phase | Test Method | Direction | ArgoCD Mode | Description |
|-------|------------|-----------|-------------|-------------|
| 0 | `test_phase0_baseline_cleanup` | — | — | Fix BackupCollision on mgmt1, ensure known-good baseline |
| 1 | `test_phase1_shell_validation` | — | — | discover-hub, preflight, argocd-check, check_rbac, show_state |
| 2 | `test_phase2_validate_only` | both | — | `--validate-only` for mgmt2→mgmt1 and mgmt1→mgmt2 |
| 3 | `test_phase3_dry_run` | both | all 3 | `--dry-run` for both directions × 3 ArgoCD modes |
| 4 | `test_phase4_switchover_no_argocd` | mgmt2→mgmt1 | none | Real switchover without Argo CD management |
| 5 | `test_phase5_post_switchover_validation` | — | — | postflight, discover-hub, show_state confirm mgmt1 is primary |
| 6 | `test_phase6_switchover_argocd_pause` | mgmt1→mgmt2 | pause-only | `--argocd-manage`, verify apps stay paused |
| 7 | `test_phase7_argocd_resume_only` | — | resume | `--argocd-resume-only`, verify apps resumed |
| 8 | `test_phase8_switchover_argocd_full` | mgmt2→mgmt1 | pause+resume | `--argocd-manage --argocd-resume-after-switchover` |
| 9 | `test_phase9_restore_original_state` | mgmt1→mgmt2 | none | Reverse switchover to restore mgmt2 as primary |
| 10 | `test_phase10_soak` | alternating | rotating | 4-hour soak with ArgoCD mode rotation |
| 11 | `test_phase11_final_validation` | — | — | Final discover-hub, postflight, state check |

## CLI Invocation Pattern

Tests invoke `acm_switchover.py` via pexpect to handle interactive prompts:

```python
import pexpect

def run_switchover(primary, secondary, extra_args=None, timeout=600):
    cmd = f"{sys.executable} acm_switchover.py --primary-context {primary} --secondary-context {secondary}"
    if extra_args:
        cmd += " " + " ".join(extra_args)
    child = pexpect.spawn(cmd, timeout=timeout, encoding="utf-8")
    child.expect(r"(proceed|continue|confirm)", timeout=30)
    child.sendline("yes")
    child.expect(pexpect.EOF)
    child.close()
    return child.exitstatus, child.before
```

## Argo CD Test Matrix

| Phase | Direction | Flags | Verification |
|-------|-----------|-------|-------------|
| 3 | dry-run | `--argocd-check` | Reports ACM-touching apps |
| 4 | mgmt2→mgmt1 | (none) | Switchover works without ArgoCD management |
| 6 | mgmt1→mgmt2 | `--argocd-manage` | Apps paused, annotation present, state file records them |
| 7 | standalone | `--argocd-resume-only --secondary-context mgmt2` | Paused apps restored from state file |
| 8 | mgmt2→mgmt1 | `--argocd-manage --argocd-resume-after-switchover` | Apps paused then auto-resumed during finalization |

After each ArgoCD-managed phase, validate:
- `argocd-manage.sh --context <hub> --mode status` shows correct states
- Annotation `acm-switchover.argoproj.io/paused-by` present/absent as expected
- State file JSON tracks paused applications

## Shell Script Cross-Validation

| Script | When | What it validates |
|--------|------|-------------------|
| `discover-hub.sh --contexts mgmt1,mgmt2 --verbose` | Phases 1, 5, 11 | Hub roles match expected state |
| `preflight-check.sh` | Phase 1 | Both hubs pass preflight |
| `postflight-check.sh` | Phases 5, 9, 11 | New primary is healthy |
| `argocd-manage.sh --mode status` | After phases 6, 7, 8 | ArgoCD app states correct |
| `check_rbac.py` | Phases 1, 11 | RBAC permissions valid |
| `show_state.py` | After each real switchover | State file reflects current phase |

## Soak Configuration (Phase 10)

- **Duration**: 4 hours
- **Cooldown**: 60 seconds between cycles
- **ArgoCD rotation**: none → pause-only → pause+resume → repeat
- **Stop-on-failure**: false (continue accumulating data)
- **Max consecutive failures**: 3 (stop early if fundamentally broken)
- **Cross-validation**: postflight-check.sh every 5th cycle
- **Metrics**: JSONL time-series in output directory

## Dependencies

- **New**: `pexpect` in `requirements-dev.txt`
- **New markers**: `e2e_full_validation`, `e2e_soak` in `setup.cfg`
- **New conftest options**: `--e2e-argocd-mode` (none/pause/pause-resume/rotate)
- **Existing**: all E2E fixtures, RunConfig, E2EOrchestrator

## Class-Level State Tracking

```python
class TestFullValidation:
    _phase_results: ClassVar[dict] = {}

    @classmethod
    def _require_phase(cls, phase_name):
        if cls._phase_results.get(phase_name) is not True:
            pytest.skip(f"Prerequisite phase '{phase_name}' did not pass")
```

## Running the Tests

```bash
# Full validation suite (phases 0-11)
pytest -m e2e_full_validation --primary-context=mgmt2 --secondary-context=mgmt1 \
    tests/e2e/test_e2e_full_validation.py -v

# Skip soak (phases 0-9, 11 only)
pytest -m "e2e_full_validation and not e2e_soak" --primary-context=mgmt2 --secondary-context=mgmt1 \
    tests/e2e/test_e2e_full_validation.py -v

# Soak only (assumes phases 0-9 already ran)
pytest -m e2e_soak --primary-context=mgmt2 --secondary-context=mgmt1 \
    tests/e2e/test_e2e_full_validation.py::TestFullValidation::test_phase10_soak -v
```
