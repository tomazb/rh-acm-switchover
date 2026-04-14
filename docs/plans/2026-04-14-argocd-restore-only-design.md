# Design: ArgoCD Management Support in Restore-Only Mode

**Date**: 2026-04-14  
**Status**: Approved  
**Branch**: single-hub

## Problem

`--restore-only` mode restores ACM managed clusters from S3 backups onto a fresh hub when the original
hub is permanently unavailable. If ArgoCD Applications with auto-sync are present on the target hub,
they can conflict with Velero during restore — overwriting restored objects or reverting cluster
import state before ACM can process it.

Currently, `--argocd-manage` and `--argocd-resume-after-switchover` are rejected when `--restore-only`
is set, with only an advisory warning suggesting manual intervention. This is insufficient: operators
need automated pause/resume to safely run restore-only against hubs with live ArgoCD.

## Approach: Inline orchestrator helper (Option A)

Add a `_pause_argocd_for_restore()` helper in `acm_switchover.py` that is called between PREFLIGHT
and ACTIVATION in the `run_restore_only()` flow when `--argocd-manage` is set. Resume is unchanged —
`finalization._resume_argocd_apps()` already reads from state and supports `hub="secondary"` entries
with `primary=None`.

## Design

### 1. Validation (`lib/validation.py`)

Remove the two rejections inside the `if is_restore_only:` block:

- ~~`--argocd-manage` rejected with restore-only~~ → **now allowed**
- ~~`--argocd-resume-after-switchover` rejected with restore-only~~ → **now allowed**

Keep: `--argocd-resume-only` remains rejected (it is a standalone mode incompatible with restore-only).

### 2. New helper: `_pause_argocd_for_restore` (`acm_switchover.py`)

```
_pause_argocd_for_restore(
    secondary: KubeClient,
    state: StateManager,
    dry_run: bool,
    logger: logging.Logger,
) -> bool
```

Behaviour:

1. Check `state.is_step_completed("pause_argocd_apps")` → return `True` immediately if already done
   (idempotent resume support).
2. Call `argocd_lib.detect_argocd_installation(secondary)`.
   - If no Applications CRD: initialize empty state (`argocd_paused_apps=[]`, `argocd_run_id=None`,
     `argocd_pause_dry_run=False`) and mark step completed. Return `True`.
3. Generate/reuse run ID via `argocd_lib.run_id_or_new(state.get_config("argocd_run_id"))`.
4. Write `argocd_run_id` and `argocd_pause_dry_run` (= `dry_run`) to state.
5. List all Applications on secondary; find ACM-touching ones via `argocd_lib.find_acm_touching_apps`.
6. For each auto-sync app: call `argocd_lib.pause_autosync(secondary, app, run_id)`;
   append entry to `argocd_paused_apps` with `hub="secondary"` (same schema as `PrimaryPrep`).
7. Persist `argocd_paused_apps` to state after each pause (same durability pattern as `PrimaryPrep`).
8. On `SwitchoverError`: log error, call `state.add_error(...)`, return `False`.
9. Mark step completed via `state.mark_step_completed("pause_argocd_apps")`. Return `True`.

### 3. Call site in `run_restore_only` (`acm_switchover.py`)

Inject before `_run_phase_activation` inside the phase loop:

```python
if handler is _run_phase_activation and getattr(args, "argocd_manage", False):
    if not state.is_step_completed("pause_argocd_apps"):
        if not _pause_argocd_for_restore(secondary, state, args.dry_run, logger):
            return False
```

### 4. Advisory update (`_report_argocd_acm_impact`)

Remove the restore-only-specific "not supported" branch. When `primary is None` and `argocd_manage`
is `False`, show the same standard advisory as normal mode ("consider `--argocd-manage`").

### 5. Resume — no changes (`modules/finalization.py`)

`_resume_argocd_apps()` calls `resume_recorded_applications(paused_apps, run_id, primary=None, secondary=target)`.
`resume_recorded_applications()` routes `hub="secondary"` entries to the secondary client — correct
for restore-only. No changes needed.

## State schema (unchanged from normal flow)

| Key | Value |
|---|---|
| `argocd_paused_apps` | `[{hub, namespace, name, original_sync_policy, pause_applied, ...}]` |
| `argocd_run_id` | UUID string |
| `argocd_pause_dry_run` | bool |
| step `pause_argocd_apps` | completed flag (idempotency) |

## Error handling

- Fatal errors during pause → `_pause_argocd_for_restore` returns `False` → `run_restore_only`
  returns `False` → phase set to `FAILED`. Operator can fix and resume; the step guard prevents
  re-pausing already-paused apps.
- ArgoCD CRD absent → treated as no-op success (not all hubs run ArgoCD).
- Partial pause failures → same behaviour as normal mode (logged per-app, collected in state).

## Tests

- `tests/test_validation.py`:
  - Remove: assertions that `--argocd-manage` + `--restore-only` raises `ValidationError`
  - Remove: assertions that `--argocd-resume-after-switchover` + `--restore-only` raises `ValidationError`
  - Add: assertions that both combinations are now **accepted**
- `tests/test_main.py` (or new dedicated file):
  - ArgoCD CRD absent → step completed, empty paused_apps, returns `True`
  - Apps found and paused → state written correctly with `hub="secondary"`
  - Dry-run → apps not patched, `argocd_pause_dry_run=True`
  - Step already completed → function returns `True` immediately without calling argocd_lib
  - Fatal ArgoCD error → returns `False`, error added to state
- Check `tests/test_restore_only.py` for advisory text assertions that need updating

## Files changed

| File | Change |
|---|---|
| `lib/validation.py` | Remove two `--restore-only` rejections |
| `acm_switchover.py` | Add `_pause_argocd_for_restore()` helper; inject call in `run_restore_only`; update advisory |
| `tests/test_validation.py` | Update tests |
| `tests/test_main.py` | Add tests for new helper |
| `CHANGELOG.md` | Add entry under `[Unreleased]` |
