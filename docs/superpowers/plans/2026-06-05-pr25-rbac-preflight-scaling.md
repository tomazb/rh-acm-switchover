# PR25 RBAC Preflight Scaling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce repeated Python RBAC preflight API calls in `RBACValidator` without changing the required permission surface, the error strings operators see, or the full failure set reported for cluster and namespace validation.

**Architecture:** Keep the optimization local to `RBACValidator`. Add three instance-local caches: one for individual `check_permission()` results, one for namespace existence lookups, and one for full validation summaries keyed by option set. The public entrypoints (`RBACValidator.validate_all_permissions()`, `RBACValidator.validate_decommission_permissions()`, `RBACValidator.generate_permission_report()`, `validate_rbac_permissions()`, and module-level `validate_decommission_permissions()`) keep their current signatures and behavior.

**Tech Stack:** Python 3, `pytest`, Kubernetes Python client (`SelfSubjectAccessReview`), `git`, `graphify`

---

## File Map

- `lib/rbac_validator.py`
  Responsibility: add instance-local permission, namespace, and validation-summary caches inside `RBACValidator` while preserving current return types, error messages, and ordering.
- `tests/test_rbac_validator.py`
  Responsibility: add red/green regression coverage for identical SSAR reuse, namespace existence reuse, cached report generation, and repeated decommission validation on the same validator instance.
- `thermos-resolution-plan.md`
  Responsibility: move PR 25 from `planned` to `in_progress` when implementation starts, then to `ready_for_review` after verification.
- Verification only:
  - `tests/test_rbac_collection_parity.py`
  - `tests/test_rbac_integration.py`
  - `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_rbac_validate.py`
  - `tests/release/checks/test_rbac_certification.py`

---

## Task 1: Start PR25 and cache identical SSAR permission probes

**Files:**
- Modify: `thermos-resolution-plan.md`
- Modify: `tests/test_rbac_validator.py`
- Modify: `lib/rbac_validator.py`

- [ ] **Step 1: Mark PR 25 `in_progress` in the tracker.**
```md
| 25 | in_progress | `perf/thermos-25-rbac-preflight-scaling` | `.worktrees/thermos-25-rbac-scaling` | F42 | not opened | Planned verification: `python -m pytest tests/test_rbac_validator.py tests/test_rbac_collection_parity.py tests/test_rbac_integration.py -q`; `python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_rbac_validate.py -q`; `python -m pytest tests/release/checks/test_rbac_certification.py -q`; `git diff --check`; final `./run_tests.sh`. |
```
Expected: `git diff -- thermos-resolution-plan.md` shows only the PR 25 row status change.

- [ ] **Step 2: Add a focused red test for repeated identical permission probes.**
```python
    @patch("kubernetes.client")
    def test_check_permission_reuses_cached_ssar_result(self, mock_k8s_client, validator):
        """Identical permission tuples should not trigger duplicate SSAR API calls."""
        mock_response = MagicMock()
        mock_response.status.allowed = True
        mock_response.status.reason = None

        mock_api = MagicMock()
        mock_api.create_self_subject_access_review.return_value = mock_response
        mock_k8s_client.AuthorizationV1Api.return_value = mock_api

        assert validator.check_permission("", "pods", "get", "default") == (True, "")
        assert validator.check_permission("", "pods", "get", "default") == (True, "")

        assert mock_api.create_self_subject_access_review.call_count == 1
```
Expected: the new test lives beside the existing `check_permission` unit tests near the top of `tests/test_rbac_validator.py`.

- [ ] **Step 3: Run the red test.**
```bash
python -m pytest tests/test_rbac_validator.py::TestRBACValidator::test_check_permission_reuses_cached_ssar_result -q
```
Expected: fail with an `AssertionError` because `create_self_subject_access_review` is called twice.

- [ ] **Step 4: Implement the minimal permission-result cache in `RBACValidator`.**
```python
import logging
from typing import Dict, List, Optional, Tuple, Union


class RBACValidator:
    def __init__(self, client: KubeClient, role: str = "operator"):
        ...
        self._permission_check_cache: Dict[
            Tuple[str, str, str, Optional[str]],
            Union[Tuple[bool, str], ValidationError],
        ] = {}

    def check_permission(
        self, api_group: str, resource: str, verb: str, namespace: Optional[str] = None
    ) -> Tuple[bool, str]:
        cache_key = (api_group, resource, verb, namespace)
        cached = self._permission_check_cache.get(cache_key)
        if isinstance(cached, ValidationError):
            raise cached
        if cached is not None:
            return cached
        ...
        if response.status.allowed:
            result = (True, "")
        else:
            reason = response.status.reason or "Permission denied"
            result = (False, reason)
        self._permission_check_cache[cache_key] = result
        return result
```
And in both exception branches, cache the constructed `ValidationError` before re-raising it:
```python
        except ApiException as e:
            error = ValidationError(
                f"Unable to check permission {verb} {group_name}/{resource} on {scope}: {e.status} {e.reason}"
            )
            self._permission_check_cache[cache_key] = error
            raise error from e
```
Expected: identical permission tuples reuse the first result, including fail-closed infrastructure errors.

- [ ] **Step 5: Re-run the focused test.**
```bash
python -m pytest tests/test_rbac_validator.py::TestRBACValidator::test_check_permission_reuses_cached_ssar_result -q
```
Expected: `1 passed`.

- [ ] **Step 6: Commit the permission-cache slice.**
```bash
git add thermos-resolution-plan.md tests/test_rbac_validator.py lib/rbac_validator.py
git commit -m "perf: cache repeated RBAC permission probes"
```
Expected: one commit containing only the tracker status bump plus the permission-cache red/green change.

---

## Task 2: Cache namespace existence probes across repeated validator calls

**Files:**
- Modify: `tests/test_rbac_validator.py`
- Modify: `lib/rbac_validator.py`

- [ ] **Step 1: Expand the test imports and add a red test for repeated namespace checks.**
```python
from lib.constants import (
    ACM_NAMESPACE,
    BACKUP_NAMESPACE,
    MANAGED_CLUSTER_AGENT_NAMESPACE,
    MCE_NAMESPACE,
    OBSERVABILITY_NAMESPACE,
)
```

```python
    def test_validate_namespace_permissions_reuses_cached_namespace_exists_results(self, validator):
        """Repeated namespace validation should not re-probe the same namespaces."""
        validator.client.namespace_exists.return_value = True
        validator.check_permission = MagicMock(return_value=(True, ""))

        assert validator.validate_namespace_permissions(skip_observability=True) == (True, [])
        assert validator.validate_namespace_permissions(skip_observability=True) == (True, [])

        assert validator.client.namespace_exists.call_args_list.count(call(BACKUP_NAMESPACE)) == 1
        assert validator.client.namespace_exists.call_args_list.count(call(ACM_NAMESPACE)) == 1
        assert validator.client.namespace_exists.call_args_list.count(call(MCE_NAMESPACE)) == 1
```
Expected: the new test sits near the existing `validate_namespace_permissions` coverage.

- [ ] **Step 2: Run the red test.**
```bash
python -m pytest tests/test_rbac_validator.py::TestRBACValidator::test_validate_namespace_permissions_reuses_cached_namespace_exists_results -q
```
Expected: fail because each namespace is checked twice.

- [ ] **Step 3: Add a cached namespace helper and route all namespace existence reads through it.**
```python
    def __init__(self, client: KubeClient, role: str = "operator"):
        ...
        self._namespace_exists_cache: Dict[str, bool] = {}

    def _namespace_exists_cached(self, namespace: str) -> bool:
        cached = self._namespace_exists_cache.get(namespace)
        if cached is not None:
            return cached
        exists = self.client.namespace_exists(namespace)
        self._namespace_exists_cache[namespace] = exists
        return exists
```

Replace the direct `self.client.namespace_exists(...)` calls in:
- `validate_namespace_permissions()`
- `validate_managed_cluster_permissions()`
- `validate_decommission_permissions()`

with:
```python
            if not self._namespace_exists_cached(namespace):
                warning = f"Namespace {namespace} does not exist - skipping permission checks"
                ...
```
and:
```python
        if check_observability and not self._namespace_exists_cached(OBSERVABILITY_NAMESPACE):
            logger.info(
                "Namespace %s does not exist - skipping observability decommission permission checks",
                OBSERVABILITY_NAMESPACE,
            )
            check_observability = False
```
Expected: repeated validation calls on the same validator instance reuse prior namespace existence answers without changing warning text.

- [ ] **Step 4: Re-run the namespace cache test.**
```bash
python -m pytest tests/test_rbac_validator.py::TestRBACValidator::test_validate_namespace_permissions_reuses_cached_namespace_exists_results -q
```
Expected: `1 passed`.

- [ ] **Step 5: Commit the namespace-cache slice.**
```bash
git add tests/test_rbac_validator.py lib/rbac_validator.py
git commit -m "perf: cache repeated RBAC namespace probes"
```
Expected: one commit containing only the namespace-cache red/green change.

---

## Task 3: Reuse full validation summaries for reports and repeat decommission checks

**Files:**
- Modify: `tests/test_rbac_validator.py`
- Modify: `lib/rbac_validator.py`

- [ ] **Step 1: Add red tests for cached full-validation reuse.**
```python
    def test_generate_permission_report_reuses_cached_validation_summary(self, validator):
        """Report generation should reuse the prior full validation result on the same validator."""
        validator.client.namespace_exists.return_value = True

        def mock_check(api_group, resource, verb, namespace=None):
            if resource == "managedclusters" and verb == "get" and namespace is None:
                return (False, "Denied")
            if resource == "pods" and verb == "get" and namespace == BACKUP_NAMESPACE:
                return (False, "Denied")
            return (True, "")

        validator.check_permission = MagicMock(side_effect=mock_check)

        all_valid, all_errors = validator.validate_all_permissions(skip_observability=True)
        first_call_count = len(validator.check_permission.call_args_list)

        report = validator.generate_permission_report(skip_observability=True)

        assert all_valid is False
        assert "cluster" in all_errors
        assert "namespaces" in all_errors
        assert len(validator.check_permission.call_args_list) == first_call_count
        assert "Missing permission: get cluster.open-cluster-management.io/managedclusters - Denied" in report
        assert f"Missing permission in {BACKUP_NAMESPACE}: get core/pods - Denied" in report
```

```python
    def test_validate_decommission_permissions_reuses_cached_summary(self, validator):
        """Repeated decommission validation should reuse the first summary for the same options."""
        validator.client.namespace_exists.return_value = True
        validator.check_permission = MagicMock(return_value=(True, ""))

        assert validator.validate_decommission_permissions(skip_observability=True) == (True, {})
        first_call_count = len(validator.check_permission.call_args_list)

        assert validator.validate_decommission_permissions(skip_observability=True) == (True, {})
        assert len(validator.check_permission.call_args_list) == first_call_count
        assert validator.client.namespace_exists.call_args_list.count(call(ACM_NAMESPACE)) == 1
```
Expected: both tests live in `TestRBACValidator` with the other full-validation/report tests.

- [ ] **Step 2: Run the new red tests.**
```bash
python -m pytest \
  tests/test_rbac_validator.py::TestRBACValidator::test_generate_permission_report_reuses_cached_validation_summary \
  tests/test_rbac_validator.py::TestRBACValidator::test_validate_decommission_permissions_reuses_cached_summary \
  -q
```
Expected: both tests fail because the validator currently recomputes the same summaries.

- [ ] **Step 3: Add a validation-summary cache and wrap the full-summary entrypoints with it.**
```python
from copy import deepcopy
from typing import Any, Callable, Dict, List, Optional, Tuple, Union


class RBACValidator:
    def __init__(self, client: KubeClient, role: str = "operator"):
        ...
        self._validation_result_cache: Dict[Tuple[Any, ...], Tuple[bool, Any]] = {}

    def _cached_validation_result(
        self,
        cache_key: Tuple[Any, ...],
        builder: Callable[[], Tuple[bool, Any]],
    ) -> Tuple[bool, Any]:
        cached = self._validation_result_cache.get(cache_key)
        if cached is None:
            cached = builder()
            self._validation_result_cache[cache_key] = cached
        all_valid, errors = cached
        return all_valid, deepcopy(errors)
```

Refactor `validate_all_permissions()` to compute through the helper:
```python
    def validate_all_permissions(...):
        cache_key = (
            "all",
            include_decommission,
            include_old_hub_finalization,
            skip_observability,
            argocd_mode,
            argocd_install_type,
        )

        def _build() -> Tuple[bool, Dict[str, List[str]]]:
            all_errors: Dict[str, List[str]] = {}
            cluster_valid, cluster_errors = self.validate_cluster_permissions(
                include_decommission=include_decommission,
                include_old_hub_finalization=include_old_hub_finalization,
                skip_observability=skip_observability,
                argocd_mode=argocd_mode,
                argocd_install_type=argocd_install_type,
            )
            if cluster_errors:
                all_errors["cluster"] = cluster_errors

            namespace_valid, namespace_errors = self.validate_namespace_permissions(skip_observability)
            if namespace_errors:
                all_errors["namespaces"] = namespace_errors

            all_valid = cluster_valid and namespace_valid
            ...
            return all_valid, all_errors

        return self._cached_validation_result(cache_key, _build)
```

Refactor `validate_decommission_permissions()` the same way:
```python
    def validate_decommission_permissions(self, skip_observability: bool = False) -> Tuple[bool, Dict[str, List[str]]]:
        cache_key = ("decommission", skip_observability)

        def _build() -> Tuple[bool, Dict[str, List[str]]]:
            all_valid = True
            all_errors: Dict[str, List[str]] = {}
            cluster_errors: List[str] = []
            namespace_errors: List[str] = []
            ...
            return all_valid, all_errors

        return self._cached_validation_result(cache_key, _build)
```
Expected: `generate_permission_report()` reuses the already-cached `validate_all_permissions()` result on the same validator instance, and repeated standalone decommission validation does not re-run the same permission and namespace probes.

- [ ] **Step 4: Re-run the focused summary-cache tests.**
```bash
python -m pytest \
  tests/test_rbac_validator.py::TestRBACValidator::test_generate_permission_report_reuses_cached_validation_summary \
  tests/test_rbac_validator.py::TestRBACValidator::test_validate_decommission_permissions_reuses_cached_summary \
  -q
```
Expected: `2 passed`.

- [ ] **Step 5: Run the full RBAC validator unit file before moving on.**
```bash
python -m pytest tests/test_rbac_validator.py -q
```
Expected: the whole file passes.

- [ ] **Step 6: Commit the summary-cache slice.**
```bash
git add tests/test_rbac_validator.py lib/rbac_validator.py
git commit -m "perf: reuse RBAC validation summaries"
```
Expected: one commit containing only the full-summary cache red/green change.

---

## Task 4: Verify parity, update the tracker, and leave the branch review-ready

**Files:**
- Modify: `thermos-resolution-plan.md`

- [ ] **Step 1: Run the focused Python RBAC verification set.**
```bash
python -m pytest tests/test_rbac_validator.py tests/test_rbac_collection_parity.py tests/test_rbac_integration.py -q
```
Expected: all targeted Python RBAC tests pass.

- [ ] **Step 2: Run the collection and release verification lanes.**
```bash
python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_rbac_validate.py -q
python -m pytest tests/release/checks/test_rbac_certification.py -q
```
Expected: both commands pass; no RBAC parity or release-contract regressions.

- [ ] **Step 3: Refresh the graph and check the diff hygiene.**
```bash
graphify update .
git diff --check
```
Expected: `graphify update .` exits successfully and `git diff --check` prints no output.

- [ ] **Step 4: Run the repo-level verification command recorded for PR 25.**
```bash
./run_tests.sh
```
Expected: if the current `ansible` base is still carrying the known unrelated Black drift noted in PR 24 (`ansible_collections/tomazb/acm_switchover/tests/unit/test_finalization_verification.py`, `tests/release/adapters/test_ansible.py`, `tests/release/checks/test_static_gates.py`, `tests/release/test_orchestrator.py`), reproduce it exactly and record that unchanged base-state failure in the tracker instead of broadening PR 25 to fix unrelated files. Otherwise, expect a clean pass.

- [ ] **Step 5: Move PR 25 to `ready_for_review` and record the exact verification outcome.**
```md
| 25 | ready_for_review | `perf/thermos-25-rbac-preflight-scaling` | `.worktrees/thermos-25-rbac-scaling` | F42 | not opened | `python -m pytest tests/test_rbac_validator.py tests/test_rbac_collection_parity.py tests/test_rbac_integration.py -q` passed; `python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_rbac_validate.py -q` passed; `python -m pytest tests/release/checks/test_rbac_certification.py -q` passed; `graphify update .` passed; `git diff --check` passed; `./run_tests.sh` passed. |
```
If `./run_tests.sh` reproduces the known clean-base Black drift instead of passing, replace only the final clause with:
```md
`./run_tests.sh` reproduced the pre-existing Black drift recorded under PR 24 in `ansible_collections/tomazb/acm_switchover/tests/unit/test_finalization_verification.py`, `tests/release/adapters/test_ansible.py`, `tests/release/checks/test_static_gates.py`, and `tests/release/test_orchestrator.py`.
```
Expected: the tracker reflects the exact state of the finished PR 25 worktree.

- [ ] **Step 6: Commit the final tracker update and branch state.**
```bash
git add thermos-resolution-plan.md lib/rbac_validator.py tests/test_rbac_validator.py
git commit -m "perf: reduce repeated RBAC preflight checks"
```
Expected: the branch is ready for code review and PR creation without any unrelated file changes.
