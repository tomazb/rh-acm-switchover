# Critical Remaining Test Gaps Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add business-meaningful tests for the highest remaining risks in primary preparation, finalization, and preflight backup validation without adding synthetic coverage.

**Architecture:** Extend the existing pytest modules with scenario-driven tests that call real workflow methods and mock only Kubernetes and Argo CD boundaries. Keep each change small, commit after each scenario group, and only touch production code if a stronger test reveals a real defect.

**Tech Stack:** Python 3.14, pytest, unittest.mock/patch, StateManager, kubernetes `ApiException`

---

### Task 1: Cover `primary_prep` Thanos failure semantics

**Files:**
- Modify: `tests/test_primary_prep.py`
- Reference: `modules/primary_prep.py:374-415`

**Step 1: Write the failing tests**

```python
def test_scale_down_thanos_404_warns_and_does_not_raise(...):
    mock_primary.scale_statefulset.side_effect = ApiException(status=404, reason="Not Found")
    prep._scale_down_thanos_compactor()
    mock_primary.get_pods.assert_not_called()


def test_scale_down_thanos_non_404_api_exception_raises(...):
    mock_primary.scale_statefulset.side_effect = ApiException(status=500, reason="Boom")
    with pytest.raises(ApiException):
        prep._scale_down_thanos_compactor()


def test_scale_down_thanos_runtime_error_raises(...):
    mock_primary.scale_statefulset.side_effect = RuntimeError("scale failed")
    with pytest.raises(RuntimeError, match="scale failed"):
        prep._scale_down_thanos_compactor()
```

**Step 2: Run test to verify the scenarios are not yet covered**

Run:

```bash
source .venv/bin/activate && python -m pytest tests/test_primary_prep.py -k "scale_down_thanos and (404 or runtime or api_exception)" -v
```

Expected: New tests fail or are missing before they are written.

**Step 3: Write minimal implementation**

- Add only the tests if the production behavior already matches.
- If a test reveals a real bug, patch only `modules/primary_prep.py:_scale_down_thanos_compactor`.

**Step 4: Run the targeted tests**

Run:

```bash
source .venv/bin/activate && python -m pytest tests/test_primary_prep.py -k "scale_down_thanos and (404 or runtime or api_exception)" -v
```

Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_primary_prep.py modules/primary_prep.py
git commit -m "test: cover primary prep Thanos failure handling"
```

### Task 2: Cover `primary_prep` Argo CD failure cleanup and rerun idempotency

**Files:**
- Modify: `tests/test_primary_prep.py`
- Reference: `modules/primary_prep.py:164-259, 350-372`

**Step 1: Write the failing tests**

```python
def test_pause_argocd_failure_removes_stale_pause_entry(...):
    pause_result = argocd_lib.PauseResult(namespace="openshift-gitops", name="acm-app", error="patch failed")
    ...
    prep._pause_argocd_acm_apps()
    assert state.get_config("argocd_paused_apps") == []


def test_disable_auto_import_skips_already_annotated_clusters(...):
    mock_primary.list_custom_resources.return_value = [
        {"metadata": {"name": "cluster-a", "annotations": {"cluster.open-cluster-management.io/disable-auto-import": ""}}},
        {"metadata": {"name": "cluster-b", "annotations": {}}},
    ]
    prep._disable_auto_import()
    mock_primary.patch_managed_cluster.assert_called_once_with(
        name="cluster-b",
        patch={"metadata": {"annotations": {"cluster.open-cluster-management.io/disable-auto-import": ""}}},
    )
```

**Step 2: Run the targeted tests**

Run:

```bash
source .venv/bin/activate && python -m pytest tests/test_primary_prep.py -k "argocd or auto_import" -v
```

Expected: FAIL before the new tests/assertions are in place.

**Step 3: Write minimal implementation**

- Prefer test-only changes.
- If a real bug is exposed, fix only the affected branch in `modules/primary_prep.py`.

**Step 4: Re-run the targeted tests**

Run:

```bash
source .venv/bin/activate && python -m pytest tests/test_primary_prep.py -k "argocd or auto_import" -v
```

Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_primary_prep.py modules/primary_prep.py
git commit -m "test: cover primary prep Argo CD failure and rerun semantics"
```

### Task 3: Cover `finalization` old-hub observability outcomes

**Files:**
- Modify: `tests/test_finalization.py`
- Reference: `modules/finalization.py:1438-1528`

**Step 1: Write the failing tests**

```python
def test_old_hub_observability_reports_success_when_all_pods_gone(...):
    fin._report_observability_scale_down_status(
        compactor_pods=[{"metadata": {"name": "compact-0"}}],
        api_pods=[{"metadata": {"name": "api-0"}}],
        compactor_pods_after=[],
        api_pods_after=[],
    )
    logger.info.assert_any_call("All observability components scaled down on old hub")


def test_old_hub_observability_warns_when_pods_remain(...):
    fin._report_observability_scale_down_status(
        compactor_pods=[{"metadata": {"name": "compact-0"}}],
        api_pods=[],
        compactor_pods_after=[{"metadata": {"name": "compact-0"}}],
        api_pods_after=[],
    )
    logger.warning.assert_called()


def test_old_hub_observability_dry_run_only_reports_intent(...):
    fin.dry_run = True
    fin._report_observability_scale_down_status(...)
    primary.get_pods.assert_not_called()
```

**Step 2: Run the targeted tests**

Run:

```bash
source .venv/bin/activate && python -m pytest tests/test_finalization.py -k "observability and old_hub" -v
```

Expected: FAIL before the new scenarios exist.

**Step 3: Write minimal implementation**

- Add tests first.
- Only touch `modules/finalization.py` if a real mismatch is exposed.

**Step 4: Re-run the targeted tests**

Run:

```bash
source .venv/bin/activate && python -m pytest tests/test_finalization.py -k "observability and old_hub" -v
```

Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_finalization.py modules/finalization.py
git commit -m "test: cover finalization old-hub observability outcomes"
```

### Task 4: Cover `finalization` auto-import reset safety paths

**Files:**
- Modify: `tests/test_finalization.py`
- Reference: `modules/finalization.py:1552-1625`

**Step 1: Write the failing tests**

```python
def test_auto_import_reset_missing_configmap_clears_state(...):
    state.set_config("auto_import_strategy_set", True)
    secondary.get_configmap.return_value = None
    assert fin._ensure_auto_import_default() is True
    assert state.get_config("auto_import_strategy_set", False) is False


def test_auto_import_reset_non_sync_strategy_clears_state_without_delete(...):
    state.set_config("auto_import_strategy_set", True)
    secondary.get_configmap.return_value = {"data": {"autoImportStrategy": "ImportOnly"}}
    assert fin._ensure_auto_import_default() is True
    secondary.delete_configmap.assert_not_called()


def test_auto_import_reset_delete_404_is_treated_as_complete(...):
    state.set_config("auto_import_strategy_set", True)
    secondary.get_configmap.return_value = {"data": {"autoImportStrategy": "Sync"}}
    secondary.delete_configmap.side_effect = ApiException(status=404, reason="Not Found")
    assert fin._ensure_auto_import_default() is True
    assert state.get_config("auto_import_strategy_set", False) is False
```

**Step 2: Run the targeted tests**

Run:

```bash
source .venv/bin/activate && python -m pytest tests/test_finalization.py -k "auto_import and reset" -v
```

Expected: FAIL before the new scenarios are added.

**Step 3: Write minimal implementation**

- Prefer test-only changes.
- Only patch `modules/finalization.py` if the new tests expose a real safety bug.

**Step 4: Re-run the targeted tests**

Run:

```bash
source .venv/bin/activate && python -m pytest tests/test_finalization.py -k "auto_import and reset" -v
```

Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_finalization.py modules/finalization.py
git commit -m "test: cover finalization auto-import reset safety paths"
```

### Task 5: Cover preflight backup wait and disappearance decisions

**Files:**
- Modify: `tests/test_preflight_validators_unit.py`
- Reference: `modules/preflight/backup_validators.py:210-260`

**Step 1: Write the failing tests**

```python
def test_backup_validator_fails_when_backups_still_in_progress_after_wait(...):
    validator._wait_for_backups_complete = Mock(return_value=["backup-a"])
    validator.run(...)
    assert reporter.critical_failures()[0]["check"] == "Backup status"


def test_backup_validator_fails_when_backups_disappear_after_wait(...):
    primary.list_custom_resources.side_effect = [[{"metadata": {"name": "backup-a"}, "status": {"phase": "InProgress"}}], []]
    validator._wait_for_backups_complete = Mock(return_value=[])
    validator.run(...)
    assert "no backups found after waiting" in reporter.results[-1]["message"]
```

**Step 2: Run the targeted tests**

Run:

```bash
source .venv/bin/activate && python -m pytest tests/test_preflight_validators_unit.py -k "backup status and wait" -v
```

Expected: FAIL before the new scenarios are added.

**Step 3: Write minimal implementation**

- Prefer test-only changes.
- Only patch `modules/preflight/backup_validators.py` if a stronger test exposes a real misclassification bug.

**Step 4: Re-run the targeted tests**

Run:

```bash
source .venv/bin/activate && python -m pytest tests/test_preflight_validators_unit.py -k "backup status and wait" -v
```

Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_preflight_validators_unit.py modules/preflight/backup_validators.py
git commit -m "test: cover preflight backup wait and disappearance failures"
```

### Task 6: Cover restore `FinishedWithErrors` classification in preflight

**Files:**
- Modify: `tests/test_preflight_validators_unit.py`
- Reference: `modules/preflight/backup_validators.py:480-520`

**Step 1: Write the failing tests**

```python
def test_passive_restore_finished_with_errors_already_available_is_benign(...):
    restore = {"status": {"phase": "FinishedWithErrors", "messages": ["cluster already available"]}}
    validator._check_passive_sync_restore(...)
    assert reporter.results[-1]["passed"] is True


def test_passive_restore_finished_with_errors_real_failure_is_critical(...):
    restore = {"status": {"phase": "FinishedWithErrors", "messages": ["PVC restore failed"]}}
    validator._check_passive_sync_restore(...)
    assert reporter.critical_failures()
```

**Step 2: Run the targeted tests**

Run:

```bash
source .venv/bin/activate && python -m pytest tests/test_preflight_validators_unit.py -k "passive sync restore and FinishedWithErrors" -v
```

Expected: FAIL before the new scenarios exist.

**Step 3: Write minimal implementation**

- Add tests first.
- Only patch classification logic if the stronger tests expose a real mismatch.

**Step 4: Re-run the targeted tests**

Run:

```bash
source .venv/bin/activate && python -m pytest tests/test_preflight_validators_unit.py -k "passive sync restore and FinishedWithErrors" -v
```

Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_preflight_validators_unit.py modules/preflight/backup_validators.py
git commit -m "test: cover preflight restore error classification"
```

### Task 7: Final verification and coverage check

**Files:**
- Modify: none expected
- Verify: `tests/test_primary_prep.py`
- Verify: `tests/test_finalization.py`
- Verify: `tests/test_preflight_validators_unit.py`

**Step 1: Run the focused suites**

Run:

```bash
source .venv/bin/activate && python -m pytest tests/test_primary_prep.py tests/test_finalization.py tests/test_preflight_validators_unit.py -q
```

Expected: PASS

**Step 2: Run the full suite**

Run:

```bash
source .venv/bin/activate && python -m pytest tests/ -x -q
```

Expected: PASS

**Step 3: Run targeted coverage**

Run:

```bash
source .venv/bin/activate && python -m pytest tests/ --cov=modules/primary_prep --cov=modules/finalization --cov=modules/preflight/backup_validators --cov-report=term-missing -q
```

Expected: Improved coverage in all three modules with no meaningful regression elsewhere.

**Step 4: Commit**

```bash
git add tests/test_primary_prep.py tests/test_finalization.py tests/test_preflight_validators_unit.py modules/primary_prep.py modules/finalization.py modules/preflight/backup_validators.py
git commit -m "test: close critical remaining business-significant gaps"
```
