# PR 36: Delete Request Timeouts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give `delete_configmap`/`delete_pod` the client-default request timeout and pass `timeout_seconds=DELETE_REQUEST_TIMEOUT` at `primary_prep.py`'s ACM ≤2.11 BackupSchedule delete, so no PRIMARY_PREP/finalization delete can hang indefinitely (R2-H1).

**Architecture:** File-idiom fix per the approved design (`docs/superpowers/specs/2026-07-03-pr36-delete-timeouts-design.md`): the two core_v1 delete calls adopt `**self._request_timeout_kwargs()` like every sibling call; the custom-resource delete site adopts the same `timeout_seconds=DELETE_REQUEST_TIMEOUT` pattern as the five equivalent sites in activation/decommission. Timeout expiry flows through the existing retry/error path — no new handling.

**Tech Stack:** Python 3, pytest (mock-based kube tests), black/isort (line-length 120).

## Global Constraints

- `black --line-length 120` and `isort --profile black --line-length 120` on touched files.
- No new parameters on public methods (YAGNI — client-level `request_timeout` already configurable).
- Base branch: `ansible`; PR branch `fix/thermos-36-delete-timeouts`.

---

### Task 1: Red-first — assert timeouts in existing tests

**Files:**
- Modify: `tests/test_kube_client.py:1174-1182` (`test_delete_pod_success`), `~1201` (`test_delete_configmap_success`)
- Modify: `tests/test_primary_prep.py:770-776` (ACM 2.11 delete assertion)

**Interfaces:**
- Consumes: `DELETE_REQUEST_TIMEOUT` from `lib.constants` (=30).

- [ ] **Step 1: Tighten the kube_client delete assertions**

In `test_delete_pod_success` change the assertion to:

```python
        mock_k8s_apis["core_api"].delete_namespaced_pod.assert_called_once_with(
            name="test-pod", namespace="test-ns", _request_timeout=30
        )
```

In `test_delete_configmap_success` change its assertion analogously:

```python
        mock_k8s_apis["core_api"].delete_namespaced_config_map.assert_called_once_with(
            name="test-cm", namespace="test-ns", _request_timeout=30
        )
```

(Keep the existing name/namespace values used by that test if they differ; only add `_request_timeout=30`.)

- [ ] **Step 2: Tighten the primary_prep ACM 2.11 delete assertion**

In `test_pause_backup_schedule_acm_211_delete_targets_backup_schedule`, add the import at the top of the file (`from lib.constants import DELETE_REQUEST_TIMEOUT` — merge into the existing `lib.constants` import if present) and extend:

```python
        mock_primary_client.delete_custom_resource.assert_called_once_with(
            group="cluster.open-cluster-management.io",
            version="v1beta1",
            plural="backupschedules",
            name="schedule-rhacm",
            namespace=BACKUP_NAMESPACE,
            timeout_seconds=DELETE_REQUEST_TIMEOUT,
        )
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/test_kube_client.py::TestDeleteOperations -q tests/test_primary_prep.py -q -k "delete_pod_success or delete_configmap_success or acm_211_delete_targets"`
Expected: 3 FAIL (missing `_request_timeout` / `timeout_seconds` kwargs).

- [ ] **Step 4: Commit**

```bash
git add tests/test_kube_client.py tests/test_primary_prep.py
git commit -m "test: require request timeouts on delete calls (red, R2-H1)"
```

### Task 2: Implement timeouts

**Files:**
- Modify: `lib/kube_client.py:502,525` (the two core_v1 delete calls)
- Modify: `modules/primary_prep.py:189-195` + its `lib.constants` import block

- [ ] **Step 1: kube_client deletes**

```python
        self.core_v1.delete_namespaced_config_map(name=name, namespace=namespace, **self._request_timeout_kwargs())
```

```python
        self.core_v1.delete_namespaced_pod(name=name, namespace=namespace, **self._request_timeout_kwargs())
```

- [ ] **Step 2: primary_prep delete**

Add `DELETE_REQUEST_TIMEOUT` to the `from lib.constants import (...)` block, and:

```python
            self.primary.delete_custom_resource(
                group="cluster.open-cluster-management.io",
                version="v1beta1",
                plural="backupschedules",
                name=bs_name,
                namespace=BACKUP_NAMESPACE,
                timeout_seconds=DELETE_REQUEST_TIMEOUT,
            )
```

- [ ] **Step 3: Run tests to verify they pass**

Run: `python -m pytest tests/test_kube_client.py tests/test_primary_prep.py -q`
Expected: all PASS.

- [ ] **Step 4: Format and commit**

```bash
black --line-length 120 lib/kube_client.py modules/primary_prep.py tests/test_kube_client.py tests/test_primary_prep.py
isort --profile black --line-length 120 lib/kube_client.py modules/primary_prep.py tests/test_kube_client.py tests/test_primary_prep.py
git add -A
git commit -m "fix: add request timeouts to delete calls on the PRIMARY_PREP path (R2-H1)"
```

### Task 3: Full verification, tracker update, draft PR

**Files:**
- Modify: `thermos-resolution-plan.md` (row PR 36)

- [ ] **Step 1: Full gate**

Run: `./run_tests.sh`
Expected: PASS (record lane counts).

- [ ] **Step 2: Update tracker row 36 and push**

Set status `ready_for_review` with evidence; then:

```bash
git add thermos-resolution-plan.md
git commit -m "docs: mark Thermos PR 36 ready for review in tracker"
git push -u origin fix/thermos-36-delete-timeouts
gh pr create --draft --base ansible --title "Thermos PR 36: request timeouts for PRIMARY_PREP delete calls (R2-H1)" --body "<summary + verification evidence>"
```
