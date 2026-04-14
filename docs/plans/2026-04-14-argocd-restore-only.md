# ArgoCD Management in Restore-Only Mode: Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Allow `--argocd-manage` and `--argocd-resume-after-switchover` with `--restore-only` by adding a secondary-only ArgoCD pause step between PREFLIGHT and ACTIVATION in the restore-only flow.

**Architecture:** A new `_pause_argocd_for_restore()` helper in `acm_switchover.py` mirrors the pause logic from `PrimaryPrep._pause_argocd_acm_apps()` but targets only the secondary (target) hub. It writes state using the same schema (`argocd_paused_apps`, `argocd_run_id`, `argocd_pause_dry_run`) so that `finalization._resume_argocd_apps()` works without modification. Two validation rejections are removed; the advisory message is simplified.

**Tech Stack:** Python 3.10+, existing `lib.argocd` module, `StateManager`, `KubeClient`

---

## Background / Key Facts

- **Normal flow**: `PrimaryPrep._pause_argocd_acm_apps()` pauses ArgoCD on primary+secondary during `PRIMARY_PREP`. `Finalization._resume_argocd_apps()` resumes from state.
- **Restore-only flow**: Skips `PRIMARY_PREP` entirely (no primary hub). Currently rejects `--argocd-manage`.
- **Why it matters**: ArgoCD auto-sync on the target hub can fight Velero during restore, overwriting restored objects.
- **Resume side**: `resume_recorded_applications()` routes `hub="secondary"` entries to `secondary` client and handles `primary=None` — no changes needed in finalization.
- **State schema** (same as normal flow):
  - `argocd_paused_apps`: list of `{hub, namespace, name, original_sync_policy, pause_applied}`
  - `argocd_run_id`: UUID string
  - `argocd_pause_dry_run`: bool
  - step `"pause_argocd_apps"`: completion flag (idempotency)
- **`PauseResult` fields**: `namespace`, `name`, `original_sync_policy`, `patched` (bool), `skip_reason`, `error`
- **Run tests**: `source .venv/bin/activate && python -m pytest tests/ -x -q`
- **Focused tests**: `python -m pytest tests/test_validation.py tests/test_main.py -x -q`

---

## Task 1: Update validation tests to reflect new rules

**Files:**
- Modify: `tests/test_validation.py` (lines ~683–720, the three `test_restore_only_forbids_argocd_*` tests)

### Step 1: Find and replace the two forbidden-becoming-allowed tests

Replace `test_restore_only_forbids_argocd_manage` with an allowed test, and `test_restore_only_forbids_argocd_resume_after_switchover` with an allowed test. Keep `test_restore_only_forbids_argocd_resume_only` unchanged.

Find this block (lines ~683–711):
```python
    def test_restore_only_forbids_argocd_manage(self):
        """Test --restore-only forbids --argocd-manage."""
        args = MockArgs(
            primary_context=None,
            secondary_context="new-hub",
            method="full",
            old_hub_action=None,
            decommission=False,
            restore_only=True,
            argocd_manage=True,
        )
        with pytest.raises(ValidationError, match="--argocd-manage"):
            InputValidator.validate_all_cli_args(args)

    def test_restore_only_forbids_argocd_resume_after_switchover(self):
        """Test --restore-only forbids --argocd-resume-after-switchover."""
        args = MockArgs(
            primary_context=None,
            secondary_context="new-hub",
            method="full",
            old_hub_action=None,
            decommission=False,
            restore_only=True,
            argocd_resume_after_switchover=True,
        )
        with pytest.raises(ValidationError, match="--argocd-resume-after-switchover"):
            InputValidator.validate_all_cli_args(args)
```

Replace it with:
```python
    def test_restore_only_allows_argocd_manage(self):
        """Test --restore-only allows --argocd-manage (targets secondary hub only)."""
        args = MockArgs(
            primary_context=None,
            secondary_context="new-hub",
            method="full",
            old_hub_action=None,
            decommission=False,
            restore_only=True,
            argocd_manage=True,
        )
        # Should not raise
        InputValidator.validate_all_cli_args(args)

    def test_restore_only_allows_argocd_resume_after_switchover(self):
        """Test --restore-only allows --argocd-resume-after-switchover."""
        args = MockArgs(
            primary_context=None,
            secondary_context="new-hub",
            method="full",
            old_hub_action=None,
            decommission=False,
            restore_only=True,
            argocd_manage=True,
            argocd_resume_after_switchover=True,
        )
        # Should not raise
        InputValidator.validate_all_cli_args(args)
```

### Step 2: Run the new tests to confirm they fail (validation not yet changed)

```bash
source .venv/bin/activate
python -m pytest tests/test_validation.py::TestRestoreOnlyValidation::test_restore_only_allows_argocd_manage tests/test_validation.py::TestRestoreOnlyValidation::test_restore_only_allows_argocd_resume_after_switchover -v
```

Expected: FAIL (`ValidationError` is raised but shouldn't be)

---

## Task 2: Fix `lib/validation.py` to allow the flags

**Files:**
- Modify: `lib/validation.py` (lines ~414–424)

### Step 1: Remove the two rejection blocks

Find and remove (the two `if has_argocd_manage:` and `if has_argocd_resume_after:` blocks inside `if is_restore_only:`):

```python
            if has_argocd_manage:
                raise ValidationError(
                    "--restore-only cannot be used with --argocd-manage "
                    "(restore-only skips PRIMARY_PREP where Argo CD pause occurs; "
                    "pause Argo CD Applications manually before running restore-only)"
                )
            if has_argocd_resume_after:
                raise ValidationError(
                    "--restore-only cannot be used with --argocd-resume-after-switchover "
                    "(restore-only does not pause Argo CD Applications)"
                )
```

Replace with a single comment:
```python
            # --argocd-manage and --argocd-resume-after-switchover are allowed:
            # restore-only pauses ArgoCD on the target hub only (no primary hub).
```

### Step 2: Run the validation tests to confirm they pass

```bash
python -m pytest tests/test_validation.py -x -q
```

Expected: all pass

### Step 3: Commit

```bash
git add lib/validation.py tests/test_validation.py
git commit -m "fix: allow --argocd-manage and --argocd-resume-after-switchover with --restore-only"
```

---

## Task 3: Write failing tests for `_pause_argocd_for_restore`

**Files:**
- Modify: `tests/test_main.py` (add a new test class near `TestRestoreOnlyFlow`)

### Step 1: Add the test class

Add this class **after** `TestRestoreOnlyFlow` (search for `class TestRestoreOnlyFlow` to find the location, then add after the class ends):

```python
class TestPauseArgocdForRestore:
    """Tests for _pause_argocd_for_restore() helper."""

    def _make_state(self, completed_steps=None):
        """Create a mock StateManager."""
        from lib.utils import StateManager
        state = Mock(spec=StateManager)
        completed = set(completed_steps or [])
        state.is_step_completed.side_effect = lambda s: s in completed
        state.get_config.return_value = None
        state.set_config.return_value = None
        state.mark_step_completed.return_value = None
        state.add_error.return_value = None
        return state

    def test_no_crd_is_noop(self):
        """When ArgoCD CRD is absent, pause is a no-op and step is marked complete."""
        from acm_switchover import _pause_argocd_for_restore

        secondary = Mock()
        state = self._make_state()
        logger = Mock()

        with patch("acm_switchover.argocd_lib.detect_argocd_installation") as detect:
            detect.return_value = Mock(has_applications_crd=False)
            result = _pause_argocd_for_restore(secondary, state, dry_run=False, logger=logger)

        assert result is True
        state.set_config.assert_any_call("argocd_paused_apps", [])
        state.set_config.assert_any_call("argocd_run_id", None)
        state.set_config.assert_any_call("argocd_pause_dry_run", False)
        state.mark_step_completed.assert_called_once_with("pause_argocd_apps")

    def test_step_already_completed_returns_immediately(self):
        """When step is already completed, no ArgoCD calls are made."""
        from acm_switchover import _pause_argocd_for_restore

        secondary = Mock()
        state = self._make_state(completed_steps=["pause_argocd_apps"])
        logger = Mock()

        with patch("acm_switchover.argocd_lib.detect_argocd_installation") as detect:
            result = _pause_argocd_for_restore(secondary, state, dry_run=False, logger=logger)
            detect.assert_not_called()

        assert result is True

    def test_auto_sync_apps_are_paused(self):
        """Apps with auto-sync are paused and recorded in state with hub='secondary'."""
        from acm_switchover import _pause_argocd_for_restore
        import copy

        secondary = Mock()
        state = self._make_state()
        logger = Mock()

        app = {
            "metadata": {"namespace": "argocd", "name": "my-app"},
            "spec": {"syncPolicy": {"automated": {}}},
        }
        impact = Mock()
        impact.app = app

        pause_result = Mock()
        pause_result.patched = True
        pause_result.namespace = "argocd"
        pause_result.name = "my-app"
        pause_result.original_sync_policy = {"automated": {}}
        pause_result.error = None

        with patch("acm_switchover.argocd_lib.detect_argocd_installation") as detect, \
             patch("acm_switchover.argocd_lib.list_argocd_applications") as list_apps, \
             patch("acm_switchover.argocd_lib.find_acm_touching_apps") as find_acm, \
             patch("acm_switchover.argocd_lib.pause_autosync") as pause, \
             patch("acm_switchover.argocd_lib.run_id_or_new", return_value="run-123"):
            detect.return_value = Mock(has_applications_crd=True)
            list_apps.return_value = [app]
            find_acm.return_value = [impact]
            pause.return_value = pause_result

            result = _pause_argocd_for_restore(secondary, state, dry_run=False, logger=logger)

        assert result is True
        pause.assert_called_once()
        state.mark_step_completed.assert_called_once_with("pause_argocd_apps")
        # Verify state recorded the paused app with hub="secondary"
        set_config_calls = {call[0][0]: call[0][1] for call in state.set_config.call_args_list
                           if call[0][0] == "argocd_paused_apps"}
        last_paused = list(set_config_calls.values())[-1]
        assert len(last_paused) == 1
        assert last_paused[0]["hub"] == "secondary"
        assert last_paused[0]["namespace"] == "argocd"
        assert last_paused[0]["name"] == "my-app"
        assert last_paused[0]["pause_applied"] is True

    def test_dry_run_does_not_mark_pause_applied(self):
        """In dry-run mode, pause_applied=False and argocd_pause_dry_run=True."""
        from acm_switchover import _pause_argocd_for_restore

        secondary = Mock()
        state = self._make_state()
        logger = Mock()

        app = {
            "metadata": {"namespace": "argocd", "name": "my-app"},
            "spec": {"syncPolicy": {"automated": {}}},
        }
        impact = Mock()
        impact.app = app

        pause_result = Mock()
        pause_result.patched = True
        pause_result.namespace = "argocd"
        pause_result.name = "my-app"
        pause_result.original_sync_policy = {"automated": {}}
        pause_result.error = None

        with patch("acm_switchover.argocd_lib.detect_argocd_installation") as detect, \
             patch("acm_switchover.argocd_lib.list_argocd_applications", return_value=[app]), \
             patch("acm_switchover.argocd_lib.find_acm_touching_apps", return_value=[impact]), \
             patch("acm_switchover.argocd_lib.pause_autosync", return_value=pause_result), \
             patch("acm_switchover.argocd_lib.run_id_or_new", return_value="run-123"):
            detect.return_value = Mock(has_applications_crd=True)
            result = _pause_argocd_for_restore(secondary, state, dry_run=True, logger=logger)

        assert result is True
        state.set_config.assert_any_call("argocd_pause_dry_run", True)
        # Verify pause_applied=False for dry-run
        set_config_calls = [call[0][1] for call in state.set_config.call_args_list
                           if call[0][0] == "argocd_paused_apps" and isinstance(call[0][1], list) and call[0][1]]
        if set_config_calls:
            assert set_config_calls[-1][0]["pause_applied"] is False

    def test_detection_failure_returns_false(self):
        """When ArgoCD detection raises, function returns False and records error."""
        from acm_switchover import _pause_argocd_for_restore

        secondary = Mock()
        state = self._make_state()
        logger = Mock()

        with patch("acm_switchover.argocd_lib.detect_argocd_installation", side_effect=Exception("timeout")):
            result = _pause_argocd_for_restore(secondary, state, dry_run=False, logger=logger)

        assert result is False
        state.add_error.assert_called_once()
        state.mark_step_completed.assert_not_called()

    def test_no_acm_apps_skips_pause(self):
        """When no ACM-touching apps are found, step is marked complete with empty list."""
        from acm_switchover import _pause_argocd_for_restore

        secondary = Mock()
        state = self._make_state()
        logger = Mock()

        with patch("acm_switchover.argocd_lib.detect_argocd_installation") as detect, \
             patch("acm_switchover.argocd_lib.list_argocd_applications", return_value=[]), \
             patch("acm_switchover.argocd_lib.find_acm_touching_apps", return_value=[]), \
             patch("acm_switchover.argocd_lib.run_id_or_new", return_value="run-123"):
            detect.return_value = Mock(has_applications_crd=True)
            result = _pause_argocd_for_restore(secondary, state, dry_run=False, logger=logger)

        assert result is True
        state.mark_step_completed.assert_called_once_with("pause_argocd_apps")
```

### Step 2: Run the new tests to confirm they fail

```bash
python -m pytest tests/test_main.py::TestPauseArgocdForRestore -v
```

Expected: FAIL (`ImportError` or `AttributeError` — `_pause_argocd_for_restore` does not exist yet)

---

## Task 4: Implement `_pause_argocd_for_restore` in `acm_switchover.py`

**Files:**
- Modify: `acm_switchover.py`

### Step 1: Add `import copy` to the imports at the top of the file

The file starts with stdlib imports. Add `import copy` after `import argparse`:

```python
import copy
```

### Step 2: Add the helper function before `_report_argocd_acm_impact`

Insert this function just before the `def _report_argocd_acm_impact(` line (around line 722):

```python
def _pause_argocd_for_restore(
    secondary: KubeClient,
    state: StateManager,
    dry_run: bool,
    logger: logging.Logger,
) -> bool:
    """Pause Argo CD auto-sync on the restore target hub before running restore.

    In restore-only mode there is no primary hub, so ArgoCD management targets
    the secondary (target) hub only.  Writes state with the same schema as
    PrimaryPrep._pause_argocd_acm_apps so finalization._resume_argocd_apps()
    works without modification.
    """
    if state.is_step_completed("pause_argocd_apps"):
        logger.info("Argo CD pause already completed (restore-only); skipping")
        return True

    try:
        discovery = argocd_lib.detect_argocd_installation(secondary)
    except Exception as exc:
        logger.error("Failed to detect Argo CD installation on target hub: %s", exc)
        state.add_error(f"ArgoCD detection failed: {exc}", "argocd_pause_restore")
        return False

    if not discovery.has_applications_crd:
        logger.info("Argo CD Applications CRD not found on target hub; skipping Argo CD pause")
        state.set_config("argocd_paused_apps", [])
        state.set_config("argocd_run_id", None)
        state.set_config("argocd_pause_dry_run", False)
        state.mark_step_completed("pause_argocd_apps")
        return True

    run_id = argocd_lib.run_id_or_new(state.get_config("argocd_run_id"))
    state.set_config("argocd_run_id", run_id)
    state.set_config("argocd_pause_dry_run", dry_run)

    try:
        apps = argocd_lib.list_argocd_applications(secondary, namespaces=None)
    except Exception as exc:
        logger.error("Failed to list Argo CD Applications on target hub: %s", exc)
        state.add_error(f"ArgoCD list failed: {exc}", "argocd_pause_restore")
        return False

    acm_apps = argocd_lib.find_acm_touching_apps(apps)
    paused_apps = copy.deepcopy(state.get_config("argocd_paused_apps") or [])
    pause_failures = 0

    for impact in acm_apps:
        meta = impact.app.get("metadata", {}) or {}
        namespace = meta.get("namespace", "")
        name = meta.get("name", "")
        sync_policy = dict((impact.app.get("spec", {}) or {}).get("syncPolicy") or {})
        if "automated" not in sync_policy:
            logger.debug("  Skip %s/%s (no auto-sync)", namespace, name)
            continue

        # Idempotent retry: skip apps already recorded in state.
        if any(
            e.get("hub") == "secondary" and e.get("namespace") == namespace and e.get("name") == name
            for e in paused_apps
        ):
            logger.debug("  Skip %s/%s (already recorded)", namespace, name)
            continue

        entry: dict = {
            "hub": "secondary",
            "namespace": namespace,
            "name": name,
            "original_sync_policy": sync_policy,
            "pause_applied": False,
        }
        paused_apps.append(entry)
        state.set_config("argocd_paused_apps", copy.deepcopy(paused_apps))

        result = argocd_lib.pause_autosync(secondary, impact.app, run_id)
        if result.patched:
            entry["original_sync_policy"] = result.original_sync_policy
            entry["pause_applied"] = not dry_run
            if dry_run:
                logger.info("  [DRY-RUN] Would pause %s/%s on target hub", result.namespace, result.name)
            else:
                logger.info("  Paused %s/%s on target hub", result.namespace, result.name)
            state.set_config("argocd_paused_apps", copy.deepcopy(paused_apps))
        elif result.error:
            paused_apps.remove(entry)
            state.set_config("argocd_paused_apps", copy.deepcopy(paused_apps))
            logger.warning("  Failed to pause %s/%s on target hub: %s", namespace, name, result.error)
            pause_failures += 1
        else:
            paused_apps.remove(entry)
            state.set_config("argocd_paused_apps", copy.deepcopy(paused_apps))
            logger.debug("  Skip %s/%s (no auto-sync after recheck)", result.namespace, result.name)

    if pause_failures:
        state.add_error(
            f"Argo CD auto-sync pause failed for {pause_failures} Application(s)",
            "argocd_pause_restore",
        )
        return False

    logger.info(
        "Argo CD: %d Application(s) paused on target hub (run_id=%s). "
        "Use --argocd-resume-after-switchover or --argocd-resume-only after restore completes.",
        len(paused_apps),
        run_id,
    )
    state.mark_step_completed("pause_argocd_apps")
    return True

```

### Step 3: Run the new tests to confirm they pass

```bash
python -m pytest tests/test_main.py::TestPauseArgocdForRestore -v
```

Expected: all 6 tests PASS

### Step 4: Commit

```bash
git add acm_switchover.py
git commit -m "feat: add _pause_argocd_for_restore helper for restore-only ArgoCD management"
```

---

## Task 5: Write failing tests for the call site in `run_restore_only`

**Files:**
- Modify: `tests/test_main.py` (add tests to `TestRestoreOnlyFlow`)

### Step 1: Add two tests to `TestRestoreOnlyFlow`

Add these methods to the `TestRestoreOnlyFlow` class:

```python
    def test_argocd_pause_called_before_activation_when_argocd_manage(self):
        """When --argocd-manage is set, _pause_argocd_for_restore is called before ACTIVATION."""
        from lib.utils import Phase, StateManager

        args = self._make_restore_only_args(argocd_manage=True)
        state = Mock(spec=StateManager)
        state.get_current_phase.return_value = Phase.INIT
        state.get_state_age.return_value = None
        state.is_step_completed.return_value = False
        secondary = Mock()

        pause_order = []

        def track_pause(sec, st, dry_run, log):
            pause_order.append("pause")
            return True

        def track_activation(a, s, primary, sec, log):
            pause_order.append("activation")
            return True

        with patch("acm_switchover._run_phase_preflight", return_value=True), \
             patch("acm_switchover._pause_argocd_for_restore", side_effect=track_pause) as pause_fn, \
             patch("acm_switchover._run_phase_activation", side_effect=track_activation), \
             patch("acm_switchover._run_phase_post_activation", return_value=True), \
             patch("acm_switchover._run_phase_finalization", return_value=True):
            result = run_restore_only(args, state, secondary, Mock())

        assert result is True
        pause_fn.assert_called_once_with(secondary, state, False, ANY)
        assert pause_order == ["pause", "activation"], "pause must run before activation"

    def test_argocd_pause_not_called_without_argocd_manage(self):
        """When --argocd-manage is not set, _pause_argocd_for_restore is NOT called."""
        from lib.utils import Phase, StateManager

        args = self._make_restore_only_args(argocd_manage=False)
        state = Mock(spec=StateManager)
        state.get_current_phase.return_value = Phase.INIT
        state.get_state_age.return_value = None
        secondary = Mock()

        with patch("acm_switchover._run_phase_preflight", return_value=True), \
             patch("acm_switchover._pause_argocd_for_restore") as pause_fn, \
             patch("acm_switchover._run_phase_activation", return_value=True), \
             patch("acm_switchover._run_phase_post_activation", return_value=True), \
             patch("acm_switchover._run_phase_finalization", return_value=True):
            result = run_restore_only(args, state, secondary, Mock())

        assert result is True
        pause_fn.assert_not_called()

    def test_argocd_pause_failure_aborts_restore(self):
        """When _pause_argocd_for_restore returns False, run_restore_only returns False."""
        from lib.utils import Phase, StateManager

        args = self._make_restore_only_args(argocd_manage=True)
        state = Mock(spec=StateManager)
        state.get_current_phase.return_value = Phase.INIT
        state.get_state_age.return_value = None
        state.is_step_completed.return_value = False
        secondary = Mock()

        with patch("acm_switchover._run_phase_preflight", return_value=True), \
             patch("acm_switchover._pause_argocd_for_restore", return_value=False), \
             patch("acm_switchover._run_phase_activation") as activation:
            result = run_restore_only(args, state, secondary, Mock())

        assert result is False
        activation.assert_not_called()
```

Note: you need `from unittest.mock import ANY` in the test file imports. Check if it's already imported; if not, add it.

### Step 2: Run the new tests to confirm they fail

```bash
python -m pytest tests/test_main.py::TestRestoreOnlyFlow::test_argocd_pause_called_before_activation_when_argocd_manage tests/test_main.py::TestRestoreOnlyFlow::test_argocd_pause_not_called_without_argocd_manage tests/test_main.py::TestRestoreOnlyFlow::test_argocd_pause_failure_aborts_restore -v
```

Expected: FAIL (`_pause_argocd_for_restore` not called / `pause_fn.assert_called_once` fails)

---

## Task 6: Wire up the call site in `run_restore_only`

**Files:**
- Modify: `acm_switchover.py` (the `run_restore_only` function, the phase loop)

### Step 1: Inject the pause call before ACTIVATION

Find this block in `run_restore_only` (around line 594):

```python
    ran_phase = False
    for handler, allowed_states in phase_flow:
        if state.get_current_phase() in allowed_states:
            ran_phase = True
            result = handler(args, state, None, secondary, logger)
            if not result:
                return False
```

Replace it with:

```python
    ran_phase = False
    for handler, allowed_states in phase_flow:
        if state.get_current_phase() in allowed_states:
            ran_phase = True
            # In restore-only mode, pause ArgoCD on the target hub before ACTIVATION.
            if handler is _run_phase_activation and getattr(args, "argocd_manage", False):
                if not state.is_step_completed("pause_argocd_apps"):
                    if not _pause_argocd_for_restore(secondary, state, args.dry_run, logger):
                        return False
            result = handler(args, state, None, secondary, logger)
            if not result:
                return False
```

### Step 2: Run the call-site tests

```bash
python -m pytest tests/test_main.py::TestRestoreOnlyFlow::test_argocd_pause_called_before_activation_when_argocd_manage tests/test_main.py::TestRestoreOnlyFlow::test_argocd_pause_not_called_without_argocd_manage tests/test_main.py::TestRestoreOnlyFlow::test_argocd_pause_failure_aborts_restore -v
```

Expected: all 3 PASS

### Step 3: Run the full test suite to check for regressions

```bash
python -m pytest tests/test_main.py tests/test_validation.py -x -q
```

Expected: all pass

### Step 4: Commit

```bash
git add acm_switchover.py
git commit -m "feat: pause ArgoCD on target hub before restore-only ACTIVATION when --argocd-manage"
```

---

## Task 7: Update the advisory message in `_report_argocd_acm_impact`

**Files:**
- Modify: `acm_switchover.py` (the `_report_argocd_acm_impact` function, around line 780)

### Step 1: Find and check if existing advisory tests need updating

```bash
grep -n "not supported in restore-only\|argocd-manage is not supported\|Pause Argo CD auto-sync manually" tests/test_main.py
```

Note any test names found — you'll need to update them.

### Step 2: Replace the restore-only-specific advisory branch

Find this block (inside `if not argocd_manage and all_acm_apps:` → `if autosync_count:`):

```python
            if primary is None:
                logger.warning(
                    "\n⚠ ArgoCD advisory: %d ACM-touching Application(s) with auto-sync detected.\n"
                    "  --argocd-manage is not supported in restore-only mode.\n"
                    "  Pause Argo CD auto-sync manually before proceeding to avoid drift.\n"
                    "  To suppress: --skip-gitops-check",
                    autosync_count,
                )
            else:
                logger.warning(
                    "\n⚠ ArgoCD advisory: %d ACM-touching Application(s) with auto-sync detected.\n"
                    "  Consider --argocd-manage to pause auto-sync during switchover.\n"
                    "  Without pausing, ArgoCD may revert switchover changes.\n"
                    "  To suppress: --skip-gitops-check",
                    autosync_count,
                )
```

Replace with a single unified warning:

```python
            logger.warning(
                "\n⚠ ArgoCD advisory: %d ACM-touching Application(s) with auto-sync detected.\n"
                "  Consider --argocd-manage to pause auto-sync during switchover.\n"
                "  Without pausing, ArgoCD may revert switchover changes.\n"
                "  To suppress: --skip-gitops-check",
                autosync_count,
            )
```

### Step 3: Update any tests that asserted the old restore-only advisory text

Search for tests checking the old "not supported in restore-only mode" advisory text and update them to expect the new unified message (or remove the restore-only-specific branch checks if the tests were testing for the old behaviour).

### Step 4: Run the advisory tests

```bash
python -m pytest tests/test_main.py -k "advisory" -v
```

Expected: all pass

### Step 5: Commit

```bash
git add acm_switchover.py tests/test_main.py
git commit -m "fix: unify ArgoCD advisory message for restore-only mode (--argocd-manage now supported)"
```

---

## Task 8: Update CHANGELOG and run full suite

**Files:**
- Modify: `CHANGELOG.md`

### Step 1: Add entry under `[Unreleased]`

Add under `## [Unreleased]` → `### Fixed`:

```markdown
### Fixed

- **ArgoCD management in restore-only mode**: `--argocd-manage` and `--argocd-resume-after-switchover`
  are now supported with `--restore-only`. A new pre-ACTIVATION step pauses ACM-touching ArgoCD
  Applications with auto-sync on the target hub before Velero runs the restore, preventing
  auto-sync from fighting restored objects. Resume works via existing `--argocd-resume-after-switchover`
  or `--argocd-resume-only` mechanisms. The advisory message when ArgoCD is detected without
  `--argocd-manage` now correctly suggests using the flag in restore-only mode.
```

### Step 2: Run the full test suite

```bash
source .venv/bin/activate
python -m pytest tests/ -x -q
```

Expected: all pass (same number as baseline or more due to new tests)

### Step 3: Final commit

```bash
git add CHANGELOG.md
git commit -m "docs: update CHANGELOG for ArgoCD restore-only support"
```
