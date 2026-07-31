# PR 42: Resume-Path Staleness Re-Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Both activation paths re-validate passive-restore readiness on entry (crash-resume included), fail-closed, per R2-M2.

**Architecture:** Per the approved design (`docs/superpowers/specs/2026-07-04-pr42-activation-resume-staleness-design.md`): extract `_assert_passive_restore_ready(restore, restore_name)` from `_verify_passive_sync`'s phase logic and call it (a) in `_activate_via_passive_sync` after the already-applied early return, using the already-fetched `restore_before`; (b) in `_activate_via_restore_resource` on the discovered passive restore before snapshot/delete. Read-only before any mutation; no checkpoint-semantics changes.

**Tech Stack:** Python 3, pytest, black/isort (line-length 120).

## Global Constraints

- Log/error strings from `_verify_passive_sync` preserved verbatim in the extracted method.
- Already-applied patch path and missing-passive-restore Option-B path keep their existing early returns with no new validation.
- `black --line-length 120` / `isort --profile black --line-length 120` on touched files.
- Base branch: `ansible` @ `87a9c070`; PR branch `fix/thermos-42-activation-resume-staleness`.

---

### Task 1: Red-first activation re-validation tests

**Files:**
- Modify: `tests/test_activation.py` (append to `TestSecondaryActivation`-level module scope as standalone functions or an appended class, matching file style)

**Interfaces:**
- Consumes (from Task 2): `SecondaryActivation._assert_passive_restore_ready(restore, restore_name) -> None` raising `lib.exceptions.FatalError`.

- [ ] **Step 1: Append the failing tests**

```python
class TestActivationResumeStalenessGuard:
    """Thermos R2-M2: activation paths must re-validate passive-restore readiness on entry."""

    def _activation(self, mock_secondary_client, mock_state_manager, activation_method):
        return SecondaryActivation(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            method="passive",
            activation_method=activation_method,
        )

    def test_patch_activation_fails_closed_on_degraded_restore(self, mock_secondary_client, mock_state_manager):
        """Resume after crash-mid-verification must not patch a degraded passive restore."""
        activation = self._activation(mock_secondary_client, mock_state_manager, "patch")
        degraded = {
            "metadata": {"name": RESTORE_PASSIVE_SYNC_NAME},
            "spec": {},
            "status": {"phase": "Error", "lastMessage": "sync broke"},
        }
        mock_secondary_client.get_custom_resource.return_value = degraded

        with pytest.raises(FatalError, match="Passive sync restore not ready: Error"):
            activation._activate_via_passive_sync()

        mock_secondary_client.patch_custom_resource.assert_not_called()
        mock_state_manager.set_config.assert_not_called()

    def test_patch_activation_already_applied_skips_revalidation(self, mock_secondary_client, mock_state_manager):
        """A patched restore legitimately transitions phases; already-applied must stay an early return."""
        activation = self._activation(mock_secondary_client, mock_state_manager, "patch")
        applied = {
            "metadata": {"name": RESTORE_PASSIVE_SYNC_NAME},
            "spec": {SPEC_VELERO_MANAGED_CLUSTERS_BACKUP_NAME: VELERO_BACKUP_LATEST},
            "status": {"phase": "FinishedWithErrors", "lastMessage": "post-patch churn"},
        }
        mock_secondary_client.get_custom_resource.return_value = applied

        activation._activate_via_passive_sync()

        mock_secondary_client.patch_custom_resource.assert_not_called()

    def test_restore_activation_fails_closed_on_degraded_passive_restore(
        self, mock_secondary_client, mock_state_manager
    ):
        """Option B must never delete a passive restore that is not activation-ready."""
        activation = self._activation(mock_secondary_client, mock_state_manager, "restore")
        mock_secondary_client.list_custom_resources.return_value = [
            {
                "metadata": {"name": RESTORE_PASSIVE_SYNC_NAME},
                "spec": {SPEC_SYNC_RESTORE_WITH_NEW_BACKUPS: True},
                "status": {"phase": "Error", "lastMessage": "sync broke"},
            }
        ]

        with pytest.raises(FatalError, match="Passive sync restore not ready: Error"):
            activation._activate_via_restore_resource()

        mock_secondary_client.delete_custom_resource.assert_not_called()
        mock_secondary_client.create_custom_resource.assert_not_called()
```

Check the top of `tests/test_activation.py` for the fixture names and imports (`FatalError`, `RESTORE_PASSIVE_SYNC_NAME`, `SPEC_SYNC_RESTORE_WITH_NEW_BACKUPS`, `SPEC_VELERO_MANAGED_CLUSTERS_BACKUP_NAME`, `VELERO_BACKUP_LATEST`) — add any missing ones. Verify `_activation_already_applied`'s actual applied-marker (read `_build_activation_patch`/`_activation_already_applied`) and adjust the `applied` fixture so it genuinely reads as applied.

- [ ] **Step 2: Run to verify the two fail-closed tests fail**

Run: `python -m pytest tests/test_activation.py::TestActivationResumeStalenessGuard -q`
Expected: tests 1 and 3 FAIL (no FatalError raised; patch/delete called); test 2 PASSES (existing early return).

- [ ] **Step 3: Commit**

```bash
git add tests/test_activation.py
git commit -m "test: require activation-entry staleness re-validation (red, R2-M2)"
```

### Task 2: Extract the guard and wire both paths

**Files:**
- Modify: `modules/activation.py:179-228` (extraction), `:230-247` (patch path), `:280-298` (Option-B path)

- [ ] **Step 1: Extract `_assert_passive_restore_ready`**

Replace the phase-logic block of `_verify_passive_sync` (from `status = restore.get("status", {})` through the final `raise FatalError(...)`) with a call `self._assert_passive_restore_ready(restore, restore_name)`, and add the new method (exact code in the design spec's Design section) directly after `_verify_passive_sync`.

- [ ] **Step 2: Wire the patch path**

In `_activate_via_passive_sync`, immediately after the already-applied early-return block:

```python
        # Thermos R2-M2: re-validate freshness on entry so a crash-resume
        # cannot activate against a degraded restore.
        self._assert_passive_restore_ready(restore_before, restore_name)
```

- [ ] **Step 3: Wire the Option-B path**

In `_activate_via_restore_resource`, inside `if restore_name:` before `passive_restore_snapshot = self._build_restore_snapshot(restore)`:

```python
            # Thermos R2-M2: never delete a passive restore that is not
            # activation-ready (crash-resume re-validation).
            self._assert_passive_restore_ready(restore, restore_name)
```

- [ ] **Step 4: Run activation suite**

Run: `python -m pytest tests/test_activation.py -q`
Expected: all PASS, including the three new tests.

- [ ] **Step 5: Format and commit**

```bash
black --line-length 120 modules/activation.py tests/test_activation.py
isort --profile black --line-length 120 modules/activation.py tests/test_activation.py
git add -A
git commit -m "fix: re-validate passive-restore freshness on activation entry (R2-M2)"
```

### Task 3: Full verification, tracker update, draft PR

**Files:**
- Modify: `thermos-resolution-plan.md` (row PR 42)

- [ ] **Step 1: Full gate** — `./run_tests.sh`, expect PASS, record lane counts.

- [ ] **Step 2: Tracker + push + PR**

```bash
git add thermos-resolution-plan.md
git commit -m "docs: mark Thermos PR 42 ready for review in tracker"
git push -u origin fix/thermos-42-activation-resume-staleness
gh pr create --draft --base ansible --title "Thermos PR 42: re-validate restore freshness on activation resume (R2-M2)" --body "<summary + verification evidence>"
```
