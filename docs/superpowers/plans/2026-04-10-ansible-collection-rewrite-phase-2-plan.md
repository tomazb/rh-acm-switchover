# Ansible Collection Rewrite Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port the core preflight and validation workflow into the Ansible Collection so supported validation scenarios gate mutations with the same critical safety conditions as the current Python tool.

**Architecture:** Phase 2 keeps orchestration in the `preflight` role and uses a small plugin surface for controller-side input validation, RBAC self-validation, and report writing. Kubernetes reads stay in role tasks through `kubernetes.core.k8s_info`, while normalization and artifact/report shaping live in collection plugins and existing `module_utils` from Phase 1.

**Tech Stack:** Ansible Collection (`roles`, `playbooks`, custom modules), Python 3.10+, `ansible-core >= 2.15`, `kubernetes.core >= 3.0.0`, pytest, `ansible-playbook`, `ansible-test sanity`

This plan assumes the Phase 0 and Phase 1 foundation work from `docs/superpowers/plans/2026-04-10-ansible-collection-rewrite-plan.md` is complete first. It does not cover core switchover mutation phases, optional checkpoint resume semantics, Argo CD mutation flows, or non-core helpers.

---

## File Structure

All paths below are relative to repo root.

```text
ansible_collections/tomazb/acm_switchover/
  plugins/
    modules/
      acm_input_validate.py                  - Controller-side validation of contexts, paths, modes, and cross-argument rules
      acm_rbac_validate.py                   - SelfSubjectAccessReview-based RBAC validation with collection result output
      acm_preflight_report.py                - Build and optionally persist structured preflight report artifacts
  roles/
    preflight/
      tasks/
        main.yml                             - Phase orchestration, result aggregation, and final fail/pass gating
        validate_inputs.yml                  - Run collection-native input contract validation
        discover_resources.yml               - Read ACM resources unless test fixtures already seeded them
        validate_kubeconfigs.yml             - Connectivity and kubeconfig safety checks
        validate_versions.yml                - ACM/MCE/MCH version compatibility checks
        validate_namespaces.yml              - Namespace, hub component, and observability presence checks
        validate_backups.yml                 - Backup, BackupSchedule, BackupStorageLocation, passive restore, and cluster backup checks
        validate_rbac.yml                    - Optional RBAC validation using the custom module
        write_report.yml                     - Emit machine-readable report artifact and phase summary facts
  tests/
    unit/
      plugins/
        modules/
          __init__.py
          test_acm_input_validate.py
          test_acm_rbac_validate.py
          test_acm_preflight_report.py
    integration/
      conftest.py                            - Shared ansible-playbook runner for fixture-driven integration tests
      fixtures/
        preflight/
          passive_success.yml
          input_failure.yml
          version_mismatch.yml
          backup_failure.yml
      test_preflight_role.py                 - End-to-end preflight role contract using seeded resource facts
  docs/
    artifact-schema.md                       - Update with preflight report fields and failure semantics
    cli-migration-map.md                     - Mark preflight-related flags as dual-supported when Phase 2 lands
    variable-reference.md                    - Document preflight result variables and report path contract
docs/ansible-collection/
  parity-matrix.md                           - Update preflight rows from planned to dual-supported where this phase delivers parity
  scenario-catalog.md                        - Promote supported preflight scenarios from planned to collection-covered
```

## Environment Setup

Before running tests for this phase:

```bash
cd /home/tomaz/sources/rh-acm-switchover
PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules -v
```

For fixture-driven role integration tests:

```bash
cd /home/tomaz/sources/rh-acm-switchover
PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/integration/test_preflight_role.py -v
```

For collection sanity after the Python plugin files exist:

```bash
cd /home/tomaz/sources/rh-acm-switchover/ansible_collections/tomazb/acm_switchover
ansible-test sanity --python 3.11 plugins/
```

---

## Phase 2: Preflight and Validation Migration

### Task 1: Input Validation Module (TDD)

**Files:**
- Create: `ansible_collections/tomazb/acm_switchover/plugins/modules/acm_input_validate.py`
- Create: `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/__init__.py`
- Create: `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_input_validate.py`

- [ ] **Step 1: Write the failing tests**

Create `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/__init__.py`:

```python
"""Unit tests for collection modules."""
```

Create `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_input_validate.py`:

```python
"""Tests for the acm_input_validate collection module."""

from ansible_collections.tomazb.acm_switchover.plugins.modules.acm_input_validate import (
    build_input_validation_results,
    summarize_input_validation,
)


def test_missing_secondary_context_fails_execute_mode():
    results = build_input_validation_results(
        {
            "hubs": {
                "primary": {"context": "primary-hub", "kubeconfig": "~/.kube/config"},
                "secondary": {"context": "", "kubeconfig": "~/.kube/config"},
            },
            "operation": {"method": "passive", "activation_method": "patch"},
            "execution": {"mode": "execute", "checkpoint": {"path": ".state/run.json"}},
            "features": {"argocd": {"manage": False, "resume_after_switchover": False}},
        }
    )
    assert any(item["id"] == "preflight-input-secondary-context" for item in results)
    assert any(item["status"] == "fail" for item in results)


def test_restore_requires_passive_method():
    results = build_input_validation_results(
        {
            "hubs": {
                "primary": {"context": "primary-hub", "kubeconfig": "~/.kube/config"},
                "secondary": {"context": "secondary-hub", "kubeconfig": "~/.kube/config"},
            },
            "operation": {"method": "full", "activation_method": "restore"},
            "execution": {"mode": "execute", "checkpoint": {"path": ".state/run.json"}},
            "features": {"argocd": {"manage": False, "resume_after_switchover": False}},
        }
    )
    assert any(item["id"] == "preflight-input-operation" for item in results)
    assert any("requires method=passive" in item["message"] for item in results)


def test_safe_paths_and_valid_contexts_pass():
    results = build_input_validation_results(
        {
            "hubs": {
                "primary": {"context": "admin/api.cluster.local:6443", "kubeconfig": "./kubeconfigs/primary"},
                "secondary": {"context": "secondary-hub", "kubeconfig": "./kubeconfigs/secondary"},
            },
            "operation": {"method": "passive", "activation_method": "patch"},
            "execution": {"mode": "dry_run", "checkpoint": {"path": ".state/run.json"}},
            "features": {"argocd": {"manage": True, "resume_after_switchover": True}},
        }
    )
    assert all(item["status"] == "pass" for item in results)


def test_summary_marks_critical_failures():
    summary = summarize_input_validation(
        [
            {
                "id": "preflight-input-secondary-context",
                "severity": "critical",
                "status": "fail",
                "message": "secondary context is required",
                "details": {},
                "recommended_action": "Set acm_switchover_hubs.secondary.context",
            }
        ]
    )
    assert summary["passed"] is False
    assert summary["critical_failures"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/tomaz/sources/rh-acm-switchover
PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_input_validate.py -v
```

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

Create `ansible_collections/tomazb/acm_switchover/plugins/modules/acm_input_validate.py`:

```python
"""Controller-side input validation module for preflight."""

from __future__ import annotations

from ansible.module_utils.basic import AnsibleModule

from ansible_collections.tomazb.acm_switchover.plugins.module_utils.result import ValidationResult
from ansible_collections.tomazb.acm_switchover.plugins.module_utils.validation import (
    ValidationError,
    validate_context_name,
    validate_operation_inputs,
    validate_safe_path,
)


def _pass_result(result_id: str, message: str, details: dict | None = None) -> dict:
    return ValidationResult(
        id=result_id,
        severity="info",
        status="pass",
        message=message,
        details=details or {},
    ).to_dict()


def _fail_result(result_id: str, message: str, recommended_action: str) -> dict:
    return ValidationResult(
        id=result_id,
        severity="critical",
        status="fail",
        message=message,
        recommended_action=recommended_action,
    ).to_dict()


def build_input_validation_results(params: dict) -> list[dict]:
    hubs = params.get("hubs", {})
    operation = params.get("operation", {})
    execution = params.get("execution", {})
    features = params.get("features", {})

    results: list[dict] = []

    primary_context = hubs.get("primary", {}).get("context", "")
    secondary_context = hubs.get("secondary", {}).get("context", "")
    primary_kubeconfig = hubs.get("primary", {}).get("kubeconfig", "")
    secondary_kubeconfig = hubs.get("secondary", {}).get("kubeconfig", "")
    checkpoint_path = execution.get("checkpoint", {}).get("path")
    mode = execution.get("mode", "execute")

    try:
        validate_context_name(primary_context)
        results.append(_pass_result("preflight-input-primary-context", "primary context is valid"))
    except ValidationError as exc:
        results.append(_fail_result("preflight-input-primary-context", str(exc), "Set a valid primary context"))

    if mode in {"execute", "validate", "dry_run"} and not secondary_context:
        results.append(
            _fail_result(
                "preflight-input-secondary-context",
                "secondary context is required for collection preflight and switchover runs",
                "Set acm_switchover_hubs.secondary.context",
            )
        )
    else:
        validate_context_name(secondary_context)
        results.append(_pass_result("preflight-input-secondary-context", "secondary context is valid"))

    for result_id, path_value in (
        ("preflight-input-primary-kubeconfig", primary_kubeconfig),
        ("preflight-input-secondary-kubeconfig", secondary_kubeconfig),
        ("preflight-input-checkpoint-path", checkpoint_path),
    ):
        if not path_value:
            continue
        try:
            validate_safe_path(path_value)
            results.append(_pass_result(result_id, f"{result_id} is safe", {"path": path_value}))
        except ValidationError as exc:
            results.append(_fail_result(result_id, str(exc), "Use a relative path without traversal or shell metacharacters"))

    try:
        normalized_operation = validate_operation_inputs(operation=operation, execution=execution, features=features)
        results.append(
            _pass_result(
                "preflight-input-operation",
                "operation inputs are internally consistent",
                normalized_operation,
            )
        )
    except ValidationError as exc:
        results.append(
            _fail_result(
                "preflight-input-operation",
                str(exc),
                "Adjust method, activation_method, execution mode, or Argo CD flags to a supported combination",
            )
        )

    return results


def summarize_input_validation(results: list[dict]) -> dict:
    critical_failures = [
        item for item in results if item["severity"] == "critical" and item["status"] in {"fail", "error"}
    ]
    return {
        "passed": len(critical_failures) == 0,
        "critical_failures": len(critical_failures),
        "results": results,
    }


def main() -> None:
    module = AnsibleModule(
        argument_spec={
            "hubs": {"type": "dict", "required": True},
            "operation": {"type": "dict", "required": True},
            "execution": {"type": "dict", "required": True},
            "features": {"type": "dict", "required": True},
        },
        supports_check_mode=True,
    )

    results = build_input_validation_results(module.params)
    summary = summarize_input_validation(results)
    module.exit_json(changed=False, **summary)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /home/tomaz/sources/rh-acm-switchover
PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_input_validate.py -v
```

Expected: All 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add ansible_collections/tomazb/acm_switchover/plugins/modules/acm_input_validate.py
git add ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/__init__.py
git add ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_input_validate.py
git commit -m "feat: add collection input validation module"
```

---

### Task 2: RBAC Validation Module (TDD)

**Files:**
- Create: `ansible_collections/tomazb/acm_switchover/plugins/modules/acm_rbac_validate.py`
- Create: `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_rbac_validate.py`

- [ ] **Step 1: Write the failing tests**

Create `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_rbac_validate.py`:

```python
"""Tests for the acm_rbac_validate collection module."""

from ansible_collections.tomazb.acm_switchover.plugins.modules.acm_rbac_validate import (
    expand_rbac_requirements,
    summarize_rbac_results,
)


def test_manage_mode_adds_application_patch_permission():
    permissions = expand_rbac_requirements(
        role="operator",
        include_decommission=False,
        skip_observability=False,
        argocd_mode="manage",
        argocd_install_type="operator",
    )
    assert ("argoproj.io", "applications", "patch", None) in permissions


def test_decommission_adds_delete_permissions():
    permissions = expand_rbac_requirements(
        role="operator",
        include_decommission=True,
        skip_observability=True,
        argocd_mode="none",
        argocd_install_type="unknown",
    )
    assert ("cluster.open-cluster-management.io", "managedclusters", "delete", None) in permissions


def test_summary_reports_failure_when_permission_missing():
    summary = summarize_rbac_results(
        hub="primary",
        denied_permissions=[
            {
                "permission": "patch argoproj.io/applications",
                "scope": "cluster",
                "reason": "Forbidden",
            }
        ],
    )
    assert summary["passed"] is False
    assert any(item["id"] == "preflight-rbac-primary" for item in summary["results"])


def test_summary_reports_pass_when_all_permissions_allowed():
    summary = summarize_rbac_results(hub="secondary", denied_permissions=[])
    assert summary["passed"] is True
    assert summary["results"][0]["status"] == "pass"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/tomaz/sources/rh-acm-switchover
PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_rbac_validate.py -v
```

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

Create `ansible_collections/tomazb/acm_switchover/plugins/modules/acm_rbac_validate.py`:

```python
"""RBAC self-validation module for collection preflight."""

from __future__ import annotations

from ansible.module_utils.basic import AnsibleModule

from ansible_collections.tomazb.acm_switchover.plugins.module_utils.result import ValidationResult

BASE_CLUSTER_PERMISSIONS = [
    ("", "namespaces", "get", None),
    ("cluster.open-cluster-management.io", "managedclusters", "get", None),
    ("cluster.open-cluster-management.io", "managedclusters", "list", None),
    ("operator.open-cluster-management.io", "multiclusterhubs", "get", None),
    ("operator.open-cluster-management.io", "multiclusterhubs", "list", None),
]

OBSERVABILITY_CLUSTER_PERMISSIONS = [
    ("observability.open-cluster-management.io", "multiclusterobservabilities", "get", None),
    ("observability.open-cluster-management.io", "multiclusterobservabilities", "list", None),
]

ARGOCD_READ_PERMISSIONS = [
    ("argoproj.io", "applications", "get", None),
    ("argoproj.io", "applications", "list", None),
    ("apiextensions.k8s.io", "customresourcedefinitions", "get", None),
]

ARGOCD_OPERATOR_PERMISSIONS = [
    ("argoproj.io", "argocds", "get", None),
    ("argoproj.io", "argocds", "list", None),
]

ARGOCD_MANAGE_EXTRA = [
    ("argoproj.io", "applications", "patch", None),
]

DECOMMISSION_EXTRA = [
    ("cluster.open-cluster-management.io", "managedclusters", "delete", None),
    ("operator.open-cluster-management.io", "multiclusterhubs", "delete", None),
]


def expand_rbac_requirements(
    role: str,
    include_decommission: bool,
    skip_observability: bool,
    argocd_mode: str,
    argocd_install_type: str,
) -> list[tuple[str, str, str, str | None]]:
    permissions = list(BASE_CLUSTER_PERMISSIONS)

    if role == "operator":
        permissions.append(("cluster.open-cluster-management.io", "managedclusters", "patch", None))

    if not skip_observability:
        permissions.extend(OBSERVABILITY_CLUSTER_PERMISSIONS)

    if argocd_mode in {"check", "manage"}:
        permissions.extend(ARGOCD_READ_PERMISSIONS)
        if argocd_install_type in {"operator", "unknown"}:
            permissions.extend(ARGOCD_OPERATOR_PERMISSIONS)

    if argocd_mode == "manage":
        permissions.extend(ARGOCD_MANAGE_EXTRA)

    if include_decommission:
        permissions.extend(DECOMMISSION_EXTRA)

    return permissions


def summarize_rbac_results(hub: str, denied_permissions: list[dict]) -> dict:
    if denied_permissions:
        result = ValidationResult(
            id=f"preflight-rbac-{hub}",
            severity="critical",
            status="fail",
            message=f"missing required RBAC permissions on {hub} hub",
            details={"denied_permissions": denied_permissions},
            recommended_action="Grant the documented collection RBAC role before running preflight again",
        ).to_dict()
        return {"passed": False, "results": [result]}

    result = ValidationResult(
        id=f"preflight-rbac-{hub}",
        severity="info",
        status="pass",
        message=f"all required RBAC permissions validated on {hub} hub",
    ).to_dict()
    return {"passed": True, "results": [result]}


def main() -> None:
    module = AnsibleModule(
        argument_spec={
            "hub": {"type": "str", "required": True},
            "role": {"type": "str", "default": "operator"},
            "include_decommission": {"type": "bool", "default": False},
            "skip_observability": {"type": "bool", "default": False},
            "argocd_mode": {"type": "str", "default": "none"},
            "argocd_install_type": {"type": "str", "default": "unknown"},
            "denied_permissions": {"type": "list", "elements": "dict", "default": []},
        },
        supports_check_mode=True,
    )

    permissions = expand_rbac_requirements(
        role=module.params["role"],
        include_decommission=module.params["include_decommission"],
        skip_observability=module.params["skip_observability"],
        argocd_mode=module.params["argocd_mode"],
        argocd_install_type=module.params["argocd_install_type"],
    )
    summary = summarize_rbac_results(module.params["hub"], module.params["denied_permissions"])
    module.exit_json(changed=False, permissions=permissions, **summary)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /home/tomaz/sources/rh-acm-switchover
PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_rbac_validate.py -v
```

Expected: All 4 tests PASS.

- [ ] **Step 5: Expand the module from the real Python permission matrix**

Update `ansible_collections/tomazb/acm_switchover/plugins/modules/acm_rbac_validate.py` by replacing the small bootstrap constants with the full role-aware permission sets from `lib/rbac_validator.py`. Preserve these behaviors explicitly:

```python
VALID_ROLES = ("operator", "validator")
VALID_ARGOCD_MODES = ("none", "check", "manage")

# Preserve the split between base Argo CD read permissions and operator-install-only argocd CRD checks.
ARGOCD_BASE_CLUSTER_PERMISSIONS = [
    ("argoproj.io", "applications", ["get", "list"]),
    ("apiextensions.k8s.io", "customresourcedefinitions", ["get"]),
]

ARGOCD_OPERATOR_CLUSTER_PERMISSIONS = [
    ("argoproj.io", "argocds", ["get", "list"]),
]

ARGOCD_MANAGE_EXTRA_CLUSTER_PERMISSIONS = [
    ("argoproj.io", "applications", ["patch"]),
]
```

The collection module must emit the same denied-permission categories the Python validator already reports:
- cluster-scoped failures
- hub namespace failures
- managed-cluster namespace failures
- decommission extras when enabled

- [ ] **Step 6: Re-run module unit tests**

```bash
cd /home/tomaz/sources/rh-acm-switchover
PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_rbac_validate.py -v
```

Expected: PASS after the real permission matrix replaces the bootstrap constants.

- [ ] **Step 7: Commit**

```bash
git add ansible_collections/tomazb/acm_switchover/plugins/modules/acm_rbac_validate.py
git add ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_rbac_validate.py
git commit -m "feat: add collection rbac validation module"
```

---

### Task 3: Structured Preflight Report Module (TDD)

**Files:**
- Create: `ansible_collections/tomazb/acm_switchover/plugins/modules/acm_preflight_report.py`
- Create: `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_preflight_report.py`

- [ ] **Step 1: Write the failing tests**

Create `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_preflight_report.py`:

```python
"""Tests for the acm_preflight_report collection module."""

from ansible_collections.tomazb.acm_switchover.plugins.modules.acm_preflight_report import (
    build_preflight_report,
    summarize_preflight_results,
)


def test_report_status_is_fail_when_critical_finding_fails():
    report = build_preflight_report(
        phase="preflight",
        results=[
            {
                "id": "preflight-version-compatibility",
                "severity": "critical",
                "status": "fail",
                "message": "versions are incompatible",
                "details": {},
                "recommended_action": "Upgrade the secondary hub",
            }
        ],
        hubs={"primary": {"context": "primary-hub"}, "secondary": {"context": "secondary-hub"}},
    )
    assert report["status"] == "fail"
    assert report["phase"] == "preflight"


def test_report_status_is_pass_when_only_warnings_exist():
    report = build_preflight_report(
        phase="preflight",
        results=[
            {
                "id": "preflight-kubeconfig-duplicate-users",
                "severity": "warning",
                "status": "fail",
                "message": "duplicate user names found",
                "details": {},
                "recommended_action": "Regenerate kubeconfigs",
            }
        ],
        hubs={"primary": {"context": "primary-hub"}, "secondary": {"context": "secondary-hub"}},
    )
    assert report["status"] == "pass"


def test_summary_counts_failures_by_severity():
    summary = summarize_preflight_results(
        [
            {"severity": "critical", "status": "fail"},
            {"severity": "warning", "status": "fail"},
            {"severity": "info", "status": "pass"},
        ]
    )
    assert summary["critical_failures"] == 1
    assert summary["warning_failures"] == 1
    assert summary["passed"] is False
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/tomaz/sources/rh-acm-switchover
PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_preflight_report.py -v
```

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

Create `ansible_collections/tomazb/acm_switchover/plugins/modules/acm_preflight_report.py`:

```python
"""Build and optionally persist preflight report artifacts."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ansible.module_utils.basic import AnsibleModule


def summarize_preflight_results(results: list[dict]) -> dict:
    critical_failures = [
        item for item in results if item.get("severity") == "critical" and item.get("status") in {"fail", "error"}
    ]
    warning_failures = [
        item for item in results if item.get("severity") == "warning" and item.get("status") in {"fail", "error"}
    ]
    return {
        "passed": len(critical_failures) == 0,
        "critical_failures": len(critical_failures),
        "warning_failures": len(warning_failures),
    }


def build_preflight_report(phase: str, results: list[dict], hubs: dict) -> dict:
    summary = summarize_preflight_results(results)
    return {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "tomazb.acm_switchover",
        "phase": phase,
        "status": "pass" if summary["passed"] else "fail",
        "summary": summary,
        "hubs": hubs,
        "results": results,
    }


def write_report(report: dict, destination: str) -> str:
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return str(path)


def main() -> None:
    module = AnsibleModule(
        argument_spec={
            "phase": {"type": "str", "required": True},
            "results": {"type": "list", "elements": "dict", "required": True},
            "hubs": {"type": "dict", "required": True},
            "path": {"type": "str", "required": False, "default": None},
        },
        supports_check_mode=True,
    )

    report = build_preflight_report(
        phase=module.params["phase"],
        results=module.params["results"],
        hubs=module.params["hubs"],
    )
    output_path = None
    if module.params["path"] and not module.check_mode:
        output_path = write_report(report, module.params["path"])

    module.exit_json(changed=bool(output_path), report=report, path=output_path)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /home/tomaz/sources/rh-acm-switchover
PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_preflight_report.py -v
```

Expected: All 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add ansible_collections/tomazb/acm_switchover/plugins/modules/acm_preflight_report.py
git add ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_preflight_report.py
git commit -m "feat: add structured preflight report module"
```

---

### Task 4: Preflight Role Orchestration and Fixture-Driven Integration Harness

**Files:**
- Modify: `ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/main.yml`
- Modify: `ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/validate_inputs.yml`
- Create: `ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/discover_resources.yml`
- Create: `ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/validate_rbac.yml`
- Create: `ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/write_report.yml`
- Create: `ansible_collections/tomazb/acm_switchover/tests/integration/conftest.py`
- Create: `ansible_collections/tomazb/acm_switchover/tests/integration/fixtures/preflight/input_failure.yml`
- Create: `ansible_collections/tomazb/acm_switchover/tests/integration/test_preflight_role.py`

- [ ] **Step 1: Write the failing integration test**

Create `ansible_collections/tomazb/acm_switchover/tests/integration/conftest.py`:

```python
"""Helpers for fixture-driven preflight integration tests."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml


@pytest.fixture
def run_preflight_fixture(tmp_path):
    def _run(fixture_name: str) -> tuple[subprocess.CompletedProcess[str], dict]:
        repo_root = Path.cwd()
        fixture_path = (
            repo_root
            / "ansible_collections/tomazb/acm_switchover/tests/integration/fixtures/preflight"
            / fixture_name
        )
        vars_payload = yaml.safe_load(fixture_path.read_text())
        vars_payload["acm_switchover_execution"]["report_dir"] = str(tmp_path / "artifacts")

        vars_file = tmp_path / "vars.yml"
        vars_file.write_text(yaml.safe_dump(vars_payload, sort_keys=False))

        completed = subprocess.run(
            [
                "ansible-playbook",
                "ansible_collections/tomazb/acm_switchover/playbooks/preflight.yml",
                "-i",
                "ansible_collections/tomazb/acm_switchover/examples/inventory.yml",
                "-e",
                f"@{vars_file}",
            ],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )

        report_path = tmp_path / "artifacts" / "preflight-report.json"
        report = json.loads(report_path.read_text()) if report_path.exists() else {}
        return completed, report

    return _run
```

Create `ansible_collections/tomazb/acm_switchover/tests/integration/fixtures/preflight/input_failure.yml`:

```yaml
---
acm_switchover_hubs:
  primary:
    context: primary-hub
    kubeconfig: ./kubeconfigs/primary
  secondary:
    context: ""
    kubeconfig: ./kubeconfigs/secondary

acm_switchover_operation:
  method: passive
  activation_method: patch

acm_switchover_features:
  skip_rbac_validation: true
  skip_observability_checks: true
  argocd:
    manage: false
    resume_after_switchover: false

acm_switchover_execution:
  mode: validate
  report_dir: ./artifacts
  checkpoint:
    path: .state/preflight.json
```

Create `ansible_collections/tomazb/acm_switchover/tests/integration/test_preflight_role.py`:

```python
"""Integration tests for the preflight role contract."""


def test_preflight_input_failure_writes_report_and_fails(run_preflight_fixture):
    completed, report = run_preflight_fixture("input_failure.yml")
    assert completed.returncode != 0
    assert report["phase"] == "preflight"
    assert report["status"] == "fail"
    assert any(item["id"] == "preflight-input-secondary-context" for item in report["results"])
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/tomaz/sources/rh-acm-switchover
PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/integration/test_preflight_role.py -v
```

Expected: FAIL because the role does not yet write a preflight report or fail on critical findings.

- [ ] **Step 3: Write minimal orchestration and report wiring**

Update `ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/main.yml`:

```yaml
---
- name: Initialize preflight result accumulator
  ansible.builtin.set_fact:
    acm_switchover_validation_results: []
    acm_switchover_preflight_summary:
      passed: true
      critical_failures: 0
      warning_failures: 0

- name: Validate controller-side inputs
  ansible.builtin.import_tasks: validate_inputs.yml

- name: Discover resources needed by later preflight checks
  ansible.builtin.import_tasks: discover_resources.yml
  when: acm_switchover_preflight_summary.passed

- name: Validate RBAC permissions when enabled
  ansible.builtin.import_tasks: validate_rbac.yml
  when:
    - acm_switchover_preflight_summary.passed
    - not (acm_switchover_features.skip_rbac_validation | default(false))

- name: Persist report and summary facts
  ansible.builtin.import_tasks: write_report.yml

- name: Stop on critical preflight failures
  ansible.builtin.fail:
    msg: >-
      Preflight failed with {{ acm_switchover_preflight_summary.critical_failures }} critical finding(s).
      See {{ acm_switchover_preflight_report.path }} for details.
  when: not acm_switchover_preflight_summary.passed
```

Update `ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/validate_inputs.yml`:

```yaml
---
- name: Run collection-native input validation
  tomazb.acm_switchover.acm_input_validate:
    hubs: "{{ acm_switchover_hubs }}"
    operation: "{{ acm_switchover_operation }}"
    execution: "{{ acm_switchover_execution }}"
    features: "{{ acm_switchover_features }}"
  register: acm_input_validation

- name: Merge input validation results into preflight accumulator
  ansible.builtin.set_fact:
    acm_switchover_validation_results: >-
      {{
        (acm_switchover_validation_results | default([]))
        + acm_input_validation.results
      }}
    acm_switchover_preflight_summary:
      passed: "{{ acm_input_validation.passed }}"
      critical_failures: "{{ acm_input_validation.critical_failures }}"
      warning_failures: 0
```

Create `ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/discover_resources.yml`:

```yaml
---
- name: Read primary MultiClusterHub when not pre-seeded by tests
  kubernetes.core.k8s_info:
    api_version: operator.open-cluster-management.io/v1
    kind: MultiClusterHub
    namespace: open-cluster-management
    kubeconfig: "{{ acm_switchover_hubs.primary.kubeconfig }}"
    context: "{{ acm_switchover_hubs.primary.context }}"
  register: acm_primary_mch_info
  when: acm_primary_mch_info is not defined

- name: Read secondary MultiClusterHub when not pre-seeded by tests
  kubernetes.core.k8s_info:
    api_version: operator.open-cluster-management.io/v1
    kind: MultiClusterHub
    namespace: open-cluster-management
    kubeconfig: "{{ acm_switchover_hubs.secondary.kubeconfig }}"
    context: "{{ acm_switchover_hubs.secondary.context }}"
  register: acm_secondary_mch_info
  when: acm_secondary_mch_info is not defined
```

Create `ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/validate_rbac.yml`:

```yaml
---
- name: Validate primary-hub RBAC permissions
  tomazb.acm_switchover.acm_rbac_validate:
    hub: primary
    role: "{{ acm_switchover_rbac.role | default('operator') }}"
    include_decommission: "{{ acm_switchover_operation.old_hub_action | default('secondary') == 'decommission' }}"
    skip_observability: "{{ acm_switchover_features.skip_observability_checks | default(false) }}"
    argocd_mode: >-
      {{
        'manage'
        if acm_switchover_features.argocd.manage | default(false)
        else 'none'
      }}
    argocd_install_type: unknown
  register: acm_primary_rbac_validation

- name: Merge RBAC results into preflight accumulator
  ansible.builtin.set_fact:
    acm_switchover_validation_results: >-
      {{
        (acm_switchover_validation_results | default([]))
        + acm_primary_rbac_validation.results
      }}
    acm_switchover_preflight_summary:
      passed: >-
        {{
          (acm_switchover_preflight_summary.passed | default(true))
          and acm_primary_rbac_validation.passed
        }}
      critical_failures: >-
        {{
          (acm_switchover_preflight_summary.critical_failures | default(0))
          + (0 if acm_primary_rbac_validation.passed else 1)
        }}
      warning_failures: "{{ acm_switchover_preflight_summary.warning_failures | default(0) }}"
```

Create `ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/write_report.yml`:

```yaml
---
- name: Write preflight report artifact
  tomazb.acm_switchover.acm_preflight_report:
    phase: preflight
    hubs: "{{ acm_switchover_hubs }}"
    results: "{{ acm_switchover_validation_results | default([]) }}"
    path: "{{ (acm_switchover_execution.report_dir | default('./artifacts')) ~ '/preflight-report.json' }}"
  register: acm_switchover_preflight_report

- name: Publish stable role result contract
  ansible.builtin.set_fact:
    acm_switchover_preflight_result:
      phase: preflight
      status: "{{ acm_switchover_preflight_report.report.status }}"
      changed: "{{ acm_switchover_preflight_report.changed }}"
      report: "{{ acm_switchover_preflight_report.report }}"
      path: "{{ acm_switchover_preflight_report.path }}"
```

- [ ] **Step 4: Run integration test to verify it passes**

```bash
cd /home/tomaz/sources/rh-acm-switchover
PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/integration/test_preflight_role.py -v
```

Expected: PASS. The role should write `preflight-report.json` and fail the play after the artifact exists.

- [ ] **Step 5: Commit**

```bash
git add ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/main.yml
git add ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/validate_inputs.yml
git add ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/discover_resources.yml
git add ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/validate_rbac.yml
git add ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/write_report.yml
git add ansible_collections/tomazb/acm_switchover/tests/integration/conftest.py
git add ansible_collections/tomazb/acm_switchover/tests/integration/fixtures/preflight/input_failure.yml
git add ansible_collections/tomazb/acm_switchover/tests/integration/test_preflight_role.py
git commit -m "feat: wire preflight role orchestration and reporting"
```

---

### Task 5: Version, Namespace, and Connectivity Validation Tasks

**Files:**
- Create: `ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/validate_kubeconfigs.yml`
- Create: `ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/validate_versions.yml`
- Create: `ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/validate_namespaces.yml`
- Modify: `ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/main.yml`
- Create: `ansible_collections/tomazb/acm_switchover/tests/integration/fixtures/preflight/passive_success.yml`
- Create: `ansible_collections/tomazb/acm_switchover/tests/integration/fixtures/preflight/version_mismatch.yml`
- Modify: `ansible_collections/tomazb/acm_switchover/tests/integration/test_preflight_role.py`

- [ ] **Step 1: Extend the integration tests with passing and failing version scenarios**

Create `ansible_collections/tomazb/acm_switchover/tests/integration/fixtures/preflight/passive_success.yml`:

```yaml
---
acm_switchover_hubs:
  primary:
    context: primary-hub
    kubeconfig: ./kubeconfigs/primary
  secondary:
    context: secondary-hub
    kubeconfig: ./kubeconfigs/secondary

acm_switchover_operation:
  method: passive
  activation_method: patch

acm_switchover_features:
  skip_rbac_validation: true
  skip_observability_checks: true
  argocd:
    manage: false
    resume_after_switchover: false

acm_switchover_execution:
  mode: validate
  report_dir: ./artifacts
  checkpoint:
    path: .state/preflight.json

acm_primary_mch_info:
  resources:
    - metadata:
        name: multiclusterhub
      status:
        currentVersion: 2.14.3

acm_secondary_mch_info:
  resources:
    - metadata:
        name: multiclusterhub
      status:
        currentVersion: 2.14.1

acm_primary_namespace_info:
  resources:
    - metadata:
        name: open-cluster-management
    - metadata:
        name: open-cluster-management-backup
    - metadata:
        name: open-cluster-management-observability

acm_secondary_namespace_info:
  resources:
    - metadata:
        name: open-cluster-management
    - metadata:
        name: open-cluster-management-backup
    - metadata:
        name: open-cluster-management-observability

acm_primary_cluster_deployments_info:
  resources:
    - metadata:
        name: cluster-a

acm_primary_managed_cluster_backups_info:
  resources:
    - metadata:
        name: cluster-a-backup
```

Create `ansible_collections/tomazb/acm_switchover/tests/integration/fixtures/preflight/version_mismatch.yml`:

```yaml
---
acm_switchover_hubs:
  primary:
    context: primary-hub
    kubeconfig: ./kubeconfigs/primary
  secondary:
    context: secondary-hub
    kubeconfig: ./kubeconfigs/secondary

acm_switchover_operation:
  method: passive
  activation_method: patch

acm_switchover_features:
  skip_rbac_validation: true
  skip_observability_checks: true
  argocd:
    manage: false
    resume_after_switchover: false

acm_switchover_execution:
  mode: validate
  report_dir: ./artifacts
  checkpoint:
    path: .state/preflight.json

acm_primary_mch_info:
  resources:
    - metadata:
        name: multiclusterhub
      status:
        currentVersion: 2.14.3

acm_secondary_mch_info:
  resources:
    - metadata:
        name: multiclusterhub
      status:
        currentVersion: 2.13.7

acm_primary_namespace_info:
  resources:
    - metadata:
        name: open-cluster-management
    - metadata:
        name: open-cluster-management-backup
    - metadata:
        name: open-cluster-management-observability

acm_secondary_namespace_info:
  resources:
    - metadata:
        name: open-cluster-management
    - metadata:
        name: open-cluster-management-backup
    - metadata:
        name: open-cluster-management-observability

acm_primary_cluster_deployments_info:
  resources:
    - metadata:
        name: cluster-a

acm_primary_managed_cluster_backups_info:
  resources:
    - metadata:
        name: cluster-a-backup
```

Update `ansible_collections/tomazb/acm_switchover/tests/integration/test_preflight_role.py`:

```python
def test_preflight_success_fixture_passes(run_preflight_fixture):
    completed, report = run_preflight_fixture("passive_success.yml")
    assert completed.returncode == 0
    assert report["status"] == "pass"
    assert any(item["id"] == "preflight-version-compatibility" for item in report["results"])


def test_preflight_version_mismatch_fails(run_preflight_fixture):
    completed, report = run_preflight_fixture("version_mismatch.yml")
    assert completed.returncode != 0
    assert report["status"] == "fail"
    assert any(item["id"] == "preflight-version-compatibility" and item["status"] == "fail" for item in report["results"])
```

- [ ] **Step 2: Run integration tests to verify they fail**

```bash
cd /home/tomaz/sources/rh-acm-switchover
PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/integration/test_preflight_role.py -v
```

Expected: FAIL because the role does not yet evaluate version, namespace, or connectivity findings.

- [ ] **Step 3: Implement the validation task files**

Create `ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/validate_kubeconfigs.yml`:

```yaml
---
- name: Record primary-hub connectivity result from discovered resources
  ansible.builtin.set_fact:
    acm_switchover_validation_results: >-
      {{
        acm_switchover_validation_results
        + [
          {
            "id": "preflight-kubeconfig-primary-connectivity",
            "severity": "critical",
            "status": "pass" if (acm_primary_mch_info.resources | default([]) | length) > 0 else "fail",
            "message": "primary hub API connectivity validated"
              if (acm_primary_mch_info.resources | default([]) | length) > 0
              else "primary hub API connectivity or MultiClusterHub lookup failed",
            "details": {"context": acm_switchover_hubs.primary.context},
            "recommended_action": None
          }
        ]
      }}

- name: Record secondary-hub connectivity result from discovered resources
  ansible.builtin.set_fact:
    acm_switchover_validation_results: >-
      {{
        acm_switchover_validation_results
        + [
          {
            "id": "preflight-kubeconfig-secondary-connectivity",
            "severity": "critical",
            "status": "pass" if (acm_secondary_mch_info.resources | default([]) | length) > 0 else "fail",
            "message": "secondary hub API connectivity validated"
              if (acm_secondary_mch_info.resources | default([]) | length) > 0
              else "secondary hub API connectivity or MultiClusterHub lookup failed",
            "details": {"context": acm_switchover_hubs.secondary.context},
            "recommended_action": None
          }
        ]
      }}
```

Create `ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/validate_versions.yml`:

```yaml
---
- name: Extract primary and secondary ACM versions
  ansible.builtin.set_fact:
    acm_primary_version: "{{ acm_primary_mch_info.resources[0].status.currentVersion | default('') }}"
    acm_secondary_version: "{{ acm_secondary_mch_info.resources[0].status.currentVersion | default('') }}"

- name: Record ACM version compatibility result
  ansible.builtin.set_fact:
    acm_switchover_validation_results: >-
      {{
        acm_switchover_validation_results
        + [
          {
            "id": "preflight-version-compatibility",
            "severity": "critical",
            "status": "pass"
              if (acm_primary_version.split('.')[0:2] == acm_secondary_version.split('.')[0:2])
              else "fail",
            "message": "primary and secondary hubs are on compatible ACM minor versions"
              if (acm_primary_version.split('.')[0:2] == acm_secondary_version.split('.')[0:2])
              else "primary and secondary hubs are on incompatible ACM minor versions",
            "details": {
              "primary_version": acm_primary_version,
              "secondary_version": acm_secondary_version
            },
            "recommended_action": "Upgrade or align the secondary hub before running switchover"
              if (acm_primary_version.split('.')[0:2] != acm_secondary_version.split('.')[0:2])
              else None
          }
        ]
      }}
```

Create `ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/validate_namespaces.yml`:

```yaml
---
- name: Collect discovered namespace names
  ansible.builtin.set_fact:
    acm_primary_namespace_names: "{{ (acm_primary_namespace_info.resources | default([])) | map(attribute='metadata.name') | list }}"
    acm_secondary_namespace_names: "{{ (acm_secondary_namespace_info.resources | default([])) | map(attribute='metadata.name') | list }}"

- name: Record required namespace presence on both hubs
  ansible.builtin.set_fact:
    acm_switchover_validation_results: >-
      {{
        acm_switchover_validation_results
        + [
          {
            "id": "preflight-namespaces-primary",
            "severity": "critical",
            "status": "pass" if 'open-cluster-management-backup' in acm_primary_namespace_names else 'fail',
            "message": "primary hub backup namespace exists"
              if 'open-cluster-management-backup' in acm_primary_namespace_names
              else "primary hub backup namespace is missing",
            "details": {"namespaces": acm_primary_namespace_names},
            "recommended_action": "Install or restore ACM backup components on the primary hub"
              if 'open-cluster-management-backup' not in acm_primary_namespace_names
              else None
          },
          {
            "id": "preflight-namespaces-secondary",
            "severity": "critical",
            "status": "pass" if 'open-cluster-management-backup' in acm_secondary_namespace_names else 'fail',
            "message": "secondary hub backup namespace exists"
              if 'open-cluster-management-backup' in acm_secondary_namespace_names
              else "secondary hub backup namespace is missing",
            "details": {"namespaces": acm_secondary_namespace_names},
            "recommended_action": "Install or restore ACM backup components on the secondary hub"
              if 'open-cluster-management-backup' not in acm_secondary_namespace_names
              else None
          },
          {
            "id": "preflight-observability-primary",
            "severity": "warning",
            "status": "skip"
              if (acm_switchover_features.skip_observability_checks | default(false))
              else (
                "pass" if 'open-cluster-management-observability' in acm_primary_namespace_names else "fail"
              ),
            "message": "primary hub observability namespace exists"
              if 'open-cluster-management-observability' in acm_primary_namespace_names
              else (
                "observability checks skipped by operator request"
                if (acm_switchover_features.skip_observability_checks | default(false))
                else "primary hub observability namespace is not installed"
              ),
            "details": {"namespaces": acm_primary_namespace_names},
            "recommended_action": "Install observability before relying on Grafana or Thanos validation"
              if (
                not (acm_switchover_features.skip_observability_checks | default(false))
                and 'open-cluster-management-observability' not in acm_primary_namespace_names
              )
              else None
          }
        ]
      }}
```

Update `ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/main.yml` to run the new files in order:

```yaml
- name: Validate kubeconfig reachability
  ansible.builtin.import_tasks: validate_kubeconfigs.yml

- name: Validate ACM version compatibility
  ansible.builtin.import_tasks: validate_versions.yml

- name: Validate required namespaces and base hub components
  ansible.builtin.import_tasks: validate_namespaces.yml
```

- [ ] **Step 4: Recompute the preflight summary before report writing**

Add this task to `ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/main.yml` immediately before `write_report.yml`:

```yaml
- name: Recompute aggregate preflight summary from collected results
  ansible.builtin.set_fact:
    acm_switchover_preflight_summary:
      passed: >-
        {{
          (
            acm_switchover_validation_results
            | selectattr('severity', 'equalto', 'critical')
            | selectattr('status', 'in', ['fail', 'error'])
            | list
            | length
          ) == 0
        }}
      critical_failures: >-
        {{
          acm_switchover_validation_results
          | selectattr('severity', 'equalto', 'critical')
          | selectattr('status', 'in', ['fail', 'error'])
          | list
          | length
        }}
      warning_failures: >-
        {{
          acm_switchover_validation_results
          | selectattr('severity', 'equalto', 'warning')
          | selectattr('status', 'in', ['fail', 'error'])
          | list
          | length
        }}
```

- [ ] **Step 5: Run integration tests to verify they pass**

```bash
cd /home/tomaz/sources/rh-acm-switchover
PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/integration/test_preflight_role.py -v
```

Expected: `input_failure.yml` and `version_mismatch.yml` FAIL correctly, while `passive_success.yml` PASSes and writes a passing report.

- [ ] **Step 6: Commit**

```bash
git add ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/main.yml
git add ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/validate_kubeconfigs.yml
git add ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/validate_versions.yml
git add ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/validate_namespaces.yml
git add ansible_collections/tomazb/acm_switchover/tests/integration/fixtures/preflight/passive_success.yml
git add ansible_collections/tomazb/acm_switchover/tests/integration/fixtures/preflight/version_mismatch.yml
git add ansible_collections/tomazb/acm_switchover/tests/integration/test_preflight_role.py
git commit -m "feat: add collection version and namespace preflight checks"
```

---

### Task 6: Backup, Passive Restore, and Managed-Cluster Validation Tasks

**Files:**
- Create: `ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/validate_backups.yml`
- Modify: `ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/discover_resources.yml`
- Create: `ansible_collections/tomazb/acm_switchover/tests/integration/fixtures/preflight/backup_failure.yml`
- Modify: `ansible_collections/tomazb/acm_switchover/tests/integration/test_preflight_role.py`

- [ ] **Step 1: Extend the integration test with a backup failure scenario**

Create `ansible_collections/tomazb/acm_switchover/tests/integration/fixtures/preflight/backup_failure.yml`:

```yaml
---
acm_switchover_hubs:
  primary:
    context: primary-hub
    kubeconfig: ./kubeconfigs/primary
  secondary:
    context: secondary-hub
    kubeconfig: ./kubeconfigs/secondary

acm_switchover_operation:
  method: passive
  activation_method: patch

acm_switchover_features:
  skip_rbac_validation: true
  skip_observability_checks: true
  argocd:
    manage: false
    resume_after_switchover: false

acm_switchover_execution:
  mode: validate
  report_dir: ./artifacts
  checkpoint:
    path: .state/preflight.json

acm_primary_mch_info:
  resources:
    - status:
        currentVersion: 2.14.3

acm_secondary_mch_info:
  resources:
    - status:
        currentVersion: 2.14.2

acm_primary_namespace_info:
  resources:
    - metadata:
        name: open-cluster-management
    - metadata:
        name: open-cluster-management-backup
    - metadata:
        name: open-cluster-management-observability

acm_secondary_namespace_info:
  resources:
    - metadata:
        name: open-cluster-management
    - metadata:
        name: open-cluster-management-backup
    - metadata:
        name: open-cluster-management-observability

acm_primary_backups_info:
  resources: []

acm_primary_backup_schedules_info:
  resources: []

acm_primary_bsl_info:
  resources:
    - metadata:
        name: acm-bsl
      status:
        phase: Unavailable

acm_secondary_bsl_info:
  resources:
    - metadata:
        name: acm-bsl
      status:
        phase: Available

acm_secondary_restore_info:
  resources: []

acm_primary_cluster_deployments_info:
  resources: []

acm_primary_managed_cluster_backups_info:
  resources: []
```

Update `ansible_collections/tomazb/acm_switchover/tests/integration/test_preflight_role.py`:

```python
def test_preflight_backup_failure_is_reported(run_preflight_fixture):
    completed, report = run_preflight_fixture("backup_failure.yml")
    assert completed.returncode != 0
    assert report["status"] == "fail"
    result_ids = {item["id"] for item in report["results"]}
    assert "preflight-backup-latest" in result_ids
    assert "preflight-backup-schedule" in result_ids
    assert "preflight-backup-storage-location-primary" in result_ids
    assert "preflight-passive-restore-secondary" in result_ids
    assert "preflight-clusterdeployments" in result_ids
    assert "preflight-managed-cluster-backups" in result_ids
```

- [ ] **Step 2: Run integration tests to verify they fail**

```bash
cd /home/tomaz/sources/rh-acm-switchover
PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/integration/test_preflight_role.py -v
```

Expected: FAIL because the role does not yet evaluate backup, BSL, or passive restore findings.

- [ ] **Step 3: Expand resource discovery for backup-related resources**

Update `ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/discover_resources.yml`:

```yaml
- name: Read primary hub backups when not pre-seeded by tests
  kubernetes.core.k8s_info:
    api_version: velero.io/v1
    kind: Backup
    namespace: open-cluster-management-backup
    kubeconfig: "{{ acm_switchover_hubs.primary.kubeconfig }}"
    context: "{{ acm_switchover_hubs.primary.context }}"
  register: acm_primary_backups_info
  when: acm_primary_backups_info is not defined

- name: Read primary hub BackupSchedules when not pre-seeded by tests
  kubernetes.core.k8s_info:
    api_version: cluster.open-cluster-management.io/v1beta1
    kind: BackupSchedule
    namespace: open-cluster-management-backup
    kubeconfig: "{{ acm_switchover_hubs.primary.kubeconfig }}"
    context: "{{ acm_switchover_hubs.primary.context }}"
  register: acm_primary_backup_schedules_info
  when: acm_primary_backup_schedules_info is not defined

- name: Read primary hub BackupStorageLocations when not pre-seeded by tests
  kubernetes.core.k8s_info:
    api_version: velero.io/v1
    kind: BackupStorageLocation
    namespace: open-cluster-management-backup
    kubeconfig: "{{ acm_switchover_hubs.primary.kubeconfig }}"
    context: "{{ acm_switchover_hubs.primary.context }}"
  register: acm_primary_bsl_info
  when: acm_primary_bsl_info is not defined

- name: Read secondary hub BackupStorageLocations when not pre-seeded by tests
  kubernetes.core.k8s_info:
    api_version: velero.io/v1
    kind: BackupStorageLocation
    namespace: open-cluster-management-backup
    kubeconfig: "{{ acm_switchover_hubs.secondary.kubeconfig }}"
    context: "{{ acm_switchover_hubs.secondary.context }}"
  register: acm_secondary_bsl_info
  when: acm_secondary_bsl_info is not defined

- name: Read secondary passive restore resource when not pre-seeded by tests
  kubernetes.core.k8s_info:
    api_version: cluster.open-cluster-management.io/v1beta1
    kind: Restore
    namespace: open-cluster-management-backup
    kubeconfig: "{{ acm_switchover_hubs.secondary.kubeconfig }}"
    context: "{{ acm_switchover_hubs.secondary.context }}"
  register: acm_secondary_restore_info
  when: acm_secondary_restore_info is not defined

- name: Read primary hub ClusterDeployments when not pre-seeded by tests
  kubernetes.core.k8s_info:
    api_version: hive.openshift.io/v1
    kind: ClusterDeployment
    kubeconfig: "{{ acm_switchover_hubs.primary.kubeconfig }}"
    context: "{{ acm_switchover_hubs.primary.context }}"
  register: acm_primary_cluster_deployments_info
  when: acm_primary_cluster_deployments_info is not defined

- name: Read primary hub ManagedCluster backups when not pre-seeded by tests
  kubernetes.core.k8s_info:
    api_version: cluster.open-cluster-management.io/v1beta1
    kind: ManagedClusterBackup
    namespace: open-cluster-management-backup
    kubeconfig: "{{ acm_switchover_hubs.primary.kubeconfig }}"
    context: "{{ acm_switchover_hubs.primary.context }}"
  register: acm_primary_managed_cluster_backups_info
  when: acm_primary_managed_cluster_backups_info is not defined
```

- [ ] **Step 4: Implement backup and passive restore checks**

Create `ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/validate_backups.yml`:

```yaml
---
- name: Record latest backup existence
  ansible.builtin.set_fact:
    acm_switchover_validation_results: >-
      {{
        acm_switchover_validation_results
        + [
          {
            "id": "preflight-backup-latest",
            "severity": "critical",
            "status": "pass" if (acm_primary_backups_info.resources | default([]) | length) > 0 else "fail",
            "message": "primary hub has at least one backup artifact"
              if (acm_primary_backups_info.resources | default([]) | length) > 0
              else "primary hub has no Velero backup artifacts",
            "details": {
              "backup_count": acm_primary_backups_info.resources | default([]) | length
            },
            "recommended_action": "Run or repair ACM backup jobs on the primary hub"
              if (acm_primary_backups_info.resources | default([]) | length) == 0
              else None
          }
        ]
      }}

- name: Record BackupSchedule presence
  ansible.builtin.set_fact:
    acm_switchover_validation_results: >-
      {{
        acm_switchover_validation_results
        + [
          {
            "id": "preflight-backup-schedule",
            "severity": "critical",
            "status": "pass" if (acm_primary_backup_schedules_info.resources | default([]) | length) > 0 else "fail",
            "message": "primary hub BackupSchedule exists"
              if (acm_primary_backup_schedules_info.resources | default([]) | length) > 0
              else "primary hub BackupSchedule is missing",
            "details": {
              "schedule_count": acm_primary_backup_schedules_info.resources | default([]) | length
            },
            "recommended_action": "Recreate the ACM BackupSchedule before switchover"
              if (acm_primary_backup_schedules_info.resources | default([]) | length) == 0
              else None
          }
        ]
      }}

- name: Record primary hub BackupStorageLocation health
  ansible.builtin.set_fact:
    acm_switchover_validation_results: >-
      {{
        acm_switchover_validation_results
        + [
          {
            "id": "preflight-backup-storage-location-primary",
            "severity": "critical",
            "status": "pass"
              if (
                (acm_primary_bsl_info.resources | default([]) | length) > 0
                and ((acm_primary_bsl_info.resources | map(attribute='status.phase') | list) | difference(['Available']) | length) == 0
              )
              else "fail",
            "message": "primary hub BackupStorageLocations are available"
              if (
                (acm_primary_bsl_info.resources | default([]) | length) > 0
                and ((acm_primary_bsl_info.resources | map(attribute='status.phase') | list) | difference(['Available']) | length) == 0
              )
              else "primary hub BackupStorageLocation is missing or unavailable",
            "details": {
              "phases": acm_primary_bsl_info.resources | default([]) | map(attribute='status.phase') | list
            },
            "recommended_action": "Repair object storage access before switchover"
              if (
                (acm_primary_bsl_info.resources | default([]) | length) == 0
                or ((acm_primary_bsl_info.resources | map(attribute='status.phase') | list) | difference(['Available']) | length) > 0
              )
              else None
          }
        ]
      }}

- name: Record secondary hub BackupStorageLocation health
  ansible.builtin.set_fact:
    acm_switchover_validation_results: >-
      {{
        acm_switchover_validation_results
        + [
          {
            "id": "preflight-backup-storage-location-secondary",
            "severity": "critical",
            "status": "pass"
              if (
                (acm_secondary_bsl_info.resources | default([]) | length) > 0
                and ((acm_secondary_bsl_info.resources | map(attribute='status.phase') | list) | difference(['Available']) | length) == 0
              )
              else "fail",
            "message": "secondary hub BackupStorageLocations are available"
              if (
                (acm_secondary_bsl_info.resources | default([]) | length) > 0
                and ((acm_secondary_bsl_info.resources | map(attribute='status.phase') | list) | difference(['Available']) | length) == 0
              )
              else "secondary hub BackupStorageLocation is missing or unavailable",
            "details": {
              "phases": acm_secondary_bsl_info.resources | default([]) | map(attribute='status.phase') | list
            },
            "recommended_action": "Repair object storage access on the secondary hub before switchover"
              if (
                (acm_secondary_bsl_info.resources | default([]) | length) == 0
                or ((acm_secondary_bsl_info.resources | map(attribute='status.phase') | list) | difference(['Available']) | length) > 0
              )
              else None
          }
        ]
      }}

- name: Record passive restore availability on secondary hub
  ansible.builtin.set_fact:
    acm_switchover_validation_results: >-
      {{
        acm_switchover_validation_results
        + [
          {
            "id": "preflight-passive-restore-secondary",
            "severity": "critical",
            "status": "pass"
              if (
                acm_switchover_operation.method == 'full'
                or (acm_secondary_restore_info.resources | default([]) | length) > 0
              )
              else "fail",
            "message": "secondary hub passive restore is present or not required"
              if (
                acm_switchover_operation.method == 'full'
                or (acm_secondary_restore_info.resources | default([]) | length) > 0
              )
              else "secondary hub passive restore is missing",
            "details": {
              "method": acm_switchover_operation.method,
              "restore_count": acm_secondary_restore_info.resources | default([]) | length
            },
            "recommended_action": "Seed or repair passive sync restore on the secondary hub"
              if (
                acm_switchover_operation.method != 'full'
                and (acm_secondary_restore_info.resources | default([]) | length) == 0
              )
              else None
          },
          {
            "id": "preflight-clusterdeployments",
            "severity": "critical",
            "status": "pass"
              if (acm_primary_cluster_deployments_info.resources | default([]) | length) > 0
              else "fail",
            "message": "primary hub ClusterDeployments were discovered"
              if (acm_primary_cluster_deployments_info.resources | default([]) | length) > 0
              else "primary hub ClusterDeployments are missing",
            "details": {
              "clusterdeployment_count": acm_primary_cluster_deployments_info.resources | default([]) | length
            },
            "recommended_action": "Investigate missing Hive ClusterDeployment resources before switchover"
              if (acm_primary_cluster_deployments_info.resources | default([]) | length) == 0
              else None
          },
          {
            "id": "preflight-managed-cluster-backups",
            "severity": "critical",
            "status": "pass"
              if (acm_primary_managed_cluster_backups_info.resources | default([]) | length) > 0
              else "fail",
            "message": "primary hub ManagedClusterBackup resources were discovered"
              if (acm_primary_managed_cluster_backups_info.resources | default([]) | length) > 0
              else "primary hub ManagedClusterBackup resources are missing",
            "details": {
              "managed_cluster_backup_count": acm_primary_managed_cluster_backups_info.resources | default([]) | length
            },
            "recommended_action": "Repair managed cluster backup generation before switchover"
              if (acm_primary_managed_cluster_backups_info.resources | default([]) | length) == 0
              else None
          }
        ]
      }}
```

Update `ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/main.yml` to import the new file:

```yaml
- name: Validate backup, restore, and cluster backup prerequisites
  ansible.builtin.import_tasks: validate_backups.yml
```

- [ ] **Step 5: Run integration tests to verify they pass**

```bash
cd /home/tomaz/sources/rh-acm-switchover
PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/integration/test_preflight_role.py -v
```

Expected: `backup_failure.yml` FAILs with the expected result IDs, while previously passing scenarios still PASS.

- [ ] **Step 6: Commit**

```bash
git add ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/discover_resources.yml
git add ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/validate_backups.yml
git add ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/main.yml
git add ansible_collections/tomazb/acm_switchover/tests/integration/fixtures/preflight/backup_failure.yml
git add ansible_collections/tomazb/acm_switchover/tests/integration/test_preflight_role.py
git commit -m "feat: add collection backup and passive restore preflight checks"
```

---

### Task 7: Documentation, Parity Tracking, and Full Verification

**Files:**
- Modify: `ansible_collections/tomazb/acm_switchover/docs/artifact-schema.md`
- Modify: `ansible_collections/tomazb/acm_switchover/docs/cli-migration-map.md`
- Modify: `ansible_collections/tomazb/acm_switchover/docs/variable-reference.md`
- Modify: `docs/ansible-collection/parity-matrix.md`
- Modify: `docs/ansible-collection/scenario-catalog.md`

- [ ] **Step 1: Update collection docs for the Phase 2 preflight contract**

Update `ansible_collections/tomazb/acm_switchover/docs/artifact-schema.md` so the preflight report section explicitly documents:

```markdown
## Preflight Report Contract

- Path: `{{ acm_switchover_execution.report_dir }}/preflight-report.json`
- Written before the role fails on critical findings
- `status=pass` means no critical findings failed
- Warning-only failures remain visible in `results` but do not fail the role
- Each result entry uses the stable schema:
  - `id`
  - `severity`
  - `status`
  - `message`
  - `details`
  - `recommended_action`
```

Update `ansible_collections/tomazb/acm_switchover/docs/variable-reference.md`:

```markdown
## Preflight Result Facts

| Variable | Type | Description |
|----------|------|-------------|
| `acm_switchover_validation_results` | list[dict] | Accumulated preflight findings |
| `acm_switchover_preflight_summary.passed` | bool | False when any critical finding fails |
| `acm_switchover_preflight_result.report` | dict | Structured preflight report payload |
| `acm_switchover_preflight_result.path` | string | Path to the written JSON report |
```

Update `ansible_collections/tomazb/acm_switchover/docs/cli-migration-map.md`:

```markdown
| Python / CLI Capability | Collection Phase 2 Status | Notes |
|-------------------------|---------------------------|-------|
| Input validation | dual-supported | `acm_input_validate` |
| RBAC validation | dual-supported | `acm_rbac_validate` |
| Version validation | dual-supported | `roles/preflight/tasks/validate_versions.yml` |
| Backup / BSL validation | dual-supported | `roles/preflight/tasks/validate_backups.yml` |
| Passive restore validation | dual-supported | `roles/preflight/tasks/validate_backups.yml` |
```

- [ ] **Step 2: Update parity and scenario tracking documents**

Update `docs/ansible-collection/parity-matrix.md` by changing these rows from `planned` to `dual-supported` once the code and tests from Tasks 1-6 are green:

```markdown
| Kubeconfig validation | implemented | dual-supported | 2 | Connectivity and safe-path coverage landed in Phase 2 |
| ACM version validation | implemented | dual-supported | 2 | Collection preflight enforces compatible ACM minor versions |
| Namespace validation | implemented | dual-supported | 2 | Backup namespaces validated on both hubs |
| Observability detection | implemented | dual-supported | 2 | Collection preflight records observability presence or skip state |
| Backup validation | implemented | dual-supported | 2 | Backup, BackupSchedule, and BSL checks landed |
| ManagedCluster backup validation | implemented | dual-supported | 2 | Collection preflight requires managed-cluster backup artifacts |
| ClusterDeployment validation | implemented | dual-supported | 2 | Collection preflight requires Hive ClusterDeployment resources |
| Passive sync validation | implemented | dual-supported | 2 | Secondary passive restore required for passive method |
| RBAC self-validation (SelfSubjectAccessReview) | implemented | dual-supported | 2 | Collection module mirrors Python RBAC gate |
| Structured validation results | implemented | dual-supported | 2 | Report artifact written before role failure |
```

Update `docs/ansible-collection/scenario-catalog.md` so these scenarios are explicitly marked as collection-covered:

```markdown
| Scenario ID | Python | Collection | Notes |
|-------------|--------|------------|-------|
| `preflight-passive-success` | yes | yes | Matching report contract required |
| `preflight-input-failure` | yes | yes | Missing secondary context blocks execution |
| `preflight-version-mismatch` | yes | yes | Minor version mismatch fails preflight |
| `preflight-backup-failure` | yes | yes | Missing backup artifacts or BSL health fails preflight |
```

- [ ] **Step 3: Run the full Phase 2 verification suite**

```bash
cd /home/tomaz/sources/rh-acm-switchover
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules \
  ansible_collections/tomazb/acm_switchover/tests/integration/test_preflight_role.py -v
```

Expected: PASS across module unit tests and preflight role integration tests.

- [ ] **Step 4: Run collection sanity**

```bash
cd /home/tomaz/sources/rh-acm-switchover/ansible_collections/tomazb/acm_switchover
ansible-test sanity --python 3.11 plugins/
```

Expected: PASS with no sanity failures in `plugins/modules/`.

- [ ] **Step 5: Commit**

```bash
git add ansible_collections/tomazb/acm_switchover/docs/artifact-schema.md
git add ansible_collections/tomazb/acm_switchover/docs/cli-migration-map.md
git add ansible_collections/tomazb/acm_switchover/docs/variable-reference.md
git add docs/ansible-collection/parity-matrix.md
git add docs/ansible-collection/scenario-catalog.md
git commit -m "docs: mark phase 2 preflight parity and artifact contracts"
```
