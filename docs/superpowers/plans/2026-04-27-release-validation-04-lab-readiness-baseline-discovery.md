# Release Validation Lab Readiness And Baseline Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add non-mutating lab discovery, environment fingerprint generation, lab-readiness assertions, and baseline compliance checks using mocked Kubernetes responses.

**Architecture:** Put discovery and baseline logic under `tests/release/baseline/`, with readiness checks under `tests/release/checks/`. The harness reads profile data and Kubernetes facts, emits stable fingerprint fields, and blocks mutation when required evidence is missing.

**Tech Stack:** Python dataclasses, Kubernetes dynamic client call boundaries, pytest monkeypatch/mocks, json-ready dictionaries.

---

## File Map

- Create: `tests/release/baseline/__init__.py`
- Create: `tests/release/baseline/discovery.py`
- Create: `tests/release/baseline/fingerprint.py`
- Create: `tests/release/baseline/assertions.py`
- Create: `tests/release/checks/lab_readiness.py`
- Create: `tests/release/baseline/test_discovery.py`
- Create: `tests/release/baseline/test_fingerprint.py`
- Create: `tests/release/baseline/test_assertions.py`
- Create: `tests/release/checks/test_lab_readiness.py`
- Modify: `tests/release/conftest.py`
- Modify: `docs/superpowers/plans/2026-04-27-release-validation-progress.md`

## Task 1: Discovery Data Model And Kubernetes Boundary

**Files:**
- Create: `tests/release/baseline/discovery.py`
- Create: `tests/release/baseline/test_discovery.py`

- [ ] **Step 1: Add discovery tests using a fake client**

```python
from tests.release.baseline.discovery import HubDiscoveryClient, discover_hub_facts


class FakeHubDiscoveryClient(HubDiscoveryClient):
    def __init__(self) -> None:
        self.resources = {
            "multiclusterhubs": [{"metadata": {"name": "multiclusterhub"}, "status": {"currentVersion": "2.12.0"}}],
            "backupschedules": [{"metadata": {"name": "acm-backup"}, "spec": {"paused": False}}],
            "restores": [{"metadata": {"name": "restore-primary"}, "status": {"phase": "Finished"}, "spec": {"syncRestoreWithNewBackups": True}}],
            "managedclusters": [{"metadata": {"name": "cluster-a"}}, {"metadata": {"name": "cluster-b"}}],
            "applications.argoproj.io": [{"metadata": {"name": "acm-app", "namespace": "openshift-gitops"}}],
        }

    def list_resources(self, resource: str, namespace: str | None = None) -> list[dict]:
        return self.resources.get(resource, [])


def test_discover_hub_facts_normalizes_core_fields() -> None:
    facts = discover_hub_facts(
        client=FakeHubDiscoveryClient(),
        context="primary",
        acm_namespace="open-cluster-management",
        argocd_namespaces=("openshift-gitops",),
    )

    assert facts.context == "primary"
    assert facts.acm_version == "2.12.0"
    assert facts.backup_schedule["present"] is True
    assert facts.restore["sync_restore_enabled"] is True
    assert facts.managed_cluster_names == ("cluster-a", "cluster-b")
    assert facts.argocd["application_count"] == 1
```

- [ ] **Step 2: Run discovery test and confirm it fails**

Run: `python -m pytest tests/release/baseline/test_discovery.py -q`

Expected: import failure for missing discovery module.

- [ ] **Step 3: Implement discovery model and fake-friendly boundary**

```python
# tests/release/baseline/discovery.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class HubDiscoveryClient(Protocol):
    def list_resources(self, resource: str, namespace: str | None = None) -> list[dict]:
        ...


@dataclass(frozen=True)
class HubFacts:
    context: str
    acm_namespace: str
    acm_version: str
    hub_role: str
    backup_schedule: dict
    restore: dict
    managed_cluster_names: tuple[str, ...]
    observability: dict
    argocd: dict


def _first(items: list[dict]) -> dict | None:
    return items[0] if items else None


def discover_hub_facts(
    *, client: HubDiscoveryClient, context: str, acm_namespace: str, argocd_namespaces: tuple[str, ...]
) -> HubFacts:
    mch = _first(client.list_resources("multiclusterhubs", acm_namespace)) or {}
    backup = _first(client.list_resources("backupschedules", acm_namespace))
    restore = _first(client.list_resources("restores", acm_namespace))
    managed_clusters = client.list_resources("managedclusters")
    applications = []
    for namespace in argocd_namespaces:
        applications.extend(client.list_resources("applications.argoproj.io", namespace))
    backup_present = backup is not None
    restore_present = restore is not None
    hub_role = "primary" if backup_present else "secondary" if restore_present else "standby"
    return HubFacts(
        context=context,
        acm_namespace=acm_namespace,
        acm_version=str(mch.get("status", {}).get("currentVersion", "unknown")),
        hub_role=hub_role,
        backup_schedule={
            "present": backup_present,
            "name": backup.get("metadata", {}).get("name") if backup else None,
            "paused": backup.get("spec", {}).get("paused") if backup else None,
        },
        restore={
            "present": restore_present,
            "name": restore.get("metadata", {}).get("name") if restore else None,
            "phase": restore.get("status", {}).get("phase") if restore else None,
            "sync_restore_enabled": restore.get("spec", {}).get("syncRestoreWithNewBackups") if restore else None,
        },
        managed_cluster_names=tuple(sorted(item["metadata"]["name"] for item in managed_clusters)),
        observability={"present": bool(client.list_resources("multiclusterobservabilities", acm_namespace)), "status": "unknown"},
        argocd={"present": bool(applications), "namespaces": tuple(argocd_namespaces), "application_count": len(applications), "fixture_application_count": len(applications)},
    )
```

- [ ] **Step 4: Run discovery test**

Run: `python -m pytest tests/release/baseline/test_discovery.py -q`

Expected: `1 passed`.

- [ ] **Step 5: Commit discovery model**

```bash
git add tests/release/baseline/discovery.py tests/release/baseline/test_discovery.py
git commit -m "feat: discover release lab hub facts"
```

## Task 2: Environment Fingerprint

**Files:**
- Create: `tests/release/baseline/fingerprint.py`
- Create: `tests/release/baseline/test_fingerprint.py`

- [ ] **Step 1: Add fingerprint tests**

```python
from tests.release.baseline.discovery import HubFacts
from tests.release.baseline.fingerprint import build_environment_fingerprint


def hub(context: str, role: str) -> HubFacts:
    return HubFacts(
        context=context,
        acm_namespace="open-cluster-management",
        acm_version="2.12.0",
        hub_role=role,
        backup_schedule={"present": role == "primary", "name": "acm-backup", "paused": False},
        restore={"present": role != "primary", "name": "restore", "phase": "Finished", "sync_restore_enabled": True},
        managed_cluster_names=("cluster-a", "cluster-b"),
        observability={"present": True, "status": "Ready"},
        argocd={"present": True, "namespaces": ("openshift-gitops",), "application_count": 1, "fixture_application_count": 1},
    )


def test_fingerprint_contains_stable_lab_contract_fields() -> None:
    fingerprint = build_environment_fingerprint(
        primary=hub("primary", "primary"),
        secondary=hub("secondary", "secondary"),
        expected_names=("cluster-a", "cluster-b"),
        expected_count=None,
        lab_readiness_status="passed",
    )

    assert fingerprint["hubs"]["primary"]["context"] == "primary"
    assert fingerprint["managed_clusters"]["expectation_type"] == "names"
    assert fingerprint["managed_clusters"]["observed_active_names"] == ["cluster-a", "cluster-b"]
    assert fingerprint["lab_readiness"]["status"] == "passed"
```

- [ ] **Step 2: Run fingerprint test and confirm it fails**

Run: `python -m pytest tests/release/baseline/test_fingerprint.py -q`

Expected: import failure.

- [ ] **Step 3: Implement fingerprint builder**

```python
# tests/release/baseline/fingerprint.py
from __future__ import annotations

from datetime import datetime, timezone

from .discovery import HubFacts


def _hub_payload(facts: HubFacts) -> dict:
    return {
        "context": facts.context,
        "acm_namespace": facts.acm_namespace,
        "acm_version": facts.acm_version,
        "platform_version": "unknown",
        "kubernetes_version": "unknown",
        "hub_role": facts.hub_role,
        "backup_schedule": facts.backup_schedule,
        "backup_storage_location": {"present": False, "health": "unknown"},
        "oadp": {"present": False, "status": "unknown"},
        "restore": facts.restore,
        "observability": facts.observability,
        "argocd": facts.argocd,
    }


def build_environment_fingerprint(
    *, primary: HubFacts, secondary: HubFacts, expected_names: tuple[str, ...], expected_count: int | None, lab_readiness_status: str
) -> dict:
    active = primary if primary.hub_role == "primary" else secondary
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "hubs": {"primary": _hub_payload(primary), "secondary": _hub_payload(secondary)},
        "managed_clusters": {
            "expectation_type": "names" if expected_names else "count",
            "expected_names": list(expected_names),
            "expected_count": expected_count,
            "observed_active_names": list(active.managed_cluster_names) if expected_names else [],
            "observed_active_count": len(active.managed_cluster_names),
            "contexts_available": [],
        },
        "lab_readiness": {"status": lab_readiness_status, "required_crds_present": [], "evidence_paths": []},
        "capabilities": {"observability": True, "argocd": True, "rbac_validation": True, "rbac_bootstrap": True, "decommission": True},
    }
```

- [ ] **Step 4: Run fingerprint test**

Run: `python -m pytest tests/release/baseline/test_fingerprint.py -q`

Expected: `1 passed`.

- [ ] **Step 5: Commit fingerprint generation**

```bash
git add tests/release/baseline/fingerprint.py tests/release/baseline/test_fingerprint.py
git commit -m "feat: build release environment fingerprint"
```

## Task 3: Lab Readiness Assertions

**Files:**
- Create: `tests/release/checks/lab_readiness.py`
- Create: `tests/release/checks/test_lab_readiness.py`

- [ ] **Step 1: Add readiness assertion tests**

```python
from tests.release.checks.lab_readiness import assert_lab_readiness


def test_lab_readiness_passes_when_required_facts_exist() -> None:
    result = assert_lab_readiness(
        fingerprint={
            "hubs": {
                "primary": {"acm_version": "2.12.0", "argocd": {"present": True}, "backup_storage_location": {"present": True, "health": "Available"}},
                "secondary": {"acm_version": "2.12.0", "argocd": {"present": True}, "backup_storage_location": {"present": True, "health": "Available"}},
            },
            "managed_clusters": {"observed_active_count": 2},
        },
        require_argocd=True,
        require_backup_storage=True,
    )

    assert result.status == "passed"
    assert result.assertions[0]["status"] == "passed"


def test_lab_readiness_fails_missing_argocd() -> None:
    result = assert_lab_readiness(
        fingerprint={
            "hubs": {
                "primary": {"acm_version": "2.12.0", "argocd": {"present": False}, "backup_storage_location": {"present": True, "health": "Available"}},
                "secondary": {"acm_version": "2.12.0", "argocd": {"present": True}, "backup_storage_location": {"present": True, "health": "Available"}},
            },
            "managed_clusters": {"observed_active_count": 2},
        },
        require_argocd=True,
        require_backup_storage=True,
    )

    assert result.status == "failed"
    assert any(item["name"] == "argocd-present" for item in result.assertions)
```

- [ ] **Step 2: Run readiness tests and confirm they fail**

Run: `python -m pytest tests/release/checks/test_lab_readiness.py -q`

Expected: import failure.

- [ ] **Step 3: Implement readiness assertion result**

```python
# tests/release/checks/lab_readiness.py
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReadinessResult:
    status: str
    assertions: list[dict]


def _assertion(name: str, passed: bool, message: str) -> dict:
    return {"capability": "lab-readiness", "name": name, "status": "passed" if passed else "failed", "message": message}


def assert_lab_readiness(*, fingerprint: dict, require_argocd: bool, require_backup_storage: bool) -> ReadinessResult:
    assertions: list[dict] = []
    for role in ("primary", "secondary"):
        hub = fingerprint["hubs"][role]
        assertions.append(_assertion(f"{role}-acm-version", hub.get("acm_version") not in (None, "unknown"), "ACM version discovered"))
        if require_argocd:
            assertions.append(_assertion("argocd-present", bool(hub.get("argocd", {}).get("present")), f"Argo CD present on {role}"))
        if require_backup_storage:
            bsl = hub.get("backup_storage_location", {})
            assertions.append(_assertion(f"{role}-backup-storage", bool(bsl.get("present")) and bsl.get("health") in {"Available", "Ready"}, "Backup storage is acceptable"))
    assertions.append(_assertion("managed-clusters-present", fingerprint["managed_clusters"]["observed_active_count"] > 0, "Managed clusters observed on active hub"))
    status = "passed" if all(item["status"] == "passed" for item in assertions) else "failed"
    return ReadinessResult(status=status, assertions=assertions)
```

- [ ] **Step 4: Run readiness tests**

Run: `python -m pytest tests/release/checks/test_lab_readiness.py -q`

Expected: `2 passed`.

- [ ] **Step 5: Commit readiness checks**

```bash
git add tests/release/checks/lab_readiness.py tests/release/checks/test_lab_readiness.py
git commit -m "feat: assert release lab readiness"
```

## Task 4: Baseline Assertions And Fixture Wiring

**Files:**
- Create: `tests/release/baseline/assertions.py`
- Create: `tests/release/baseline/test_assertions.py`
- Modify: `tests/release/conftest.py`

- [ ] **Step 1: Add baseline assertion tests**

```python
from tests.release.baseline.assertions import assert_baseline


def test_baseline_passes_when_initial_primary_matches() -> None:
    result = assert_baseline(
        fingerprint={
            "hubs": {
                "primary": {"hub_role": "primary", "backup_schedule": {"present": True}},
                "secondary": {"hub_role": "secondary", "restore": {"present": True}},
            },
            "managed_clusters": {"expectation_type": "count", "expected_count": 2, "observed_active_count": 2},
        },
        initial_primary="primary",
    )

    assert result.status == "passed"


def test_baseline_fails_wrong_primary_role() -> None:
    result = assert_baseline(
        fingerprint={
            "hubs": {
                "primary": {"hub_role": "secondary", "backup_schedule": {"present": False}},
                "secondary": {"hub_role": "primary", "restore": {"present": False}},
            },
            "managed_clusters": {"expectation_type": "count", "expected_count": 2, "observed_active_count": 2},
        },
        initial_primary="primary",
    )

    assert result.status == "failed"
    assert any(item["name"] == "initial-primary-role" for item in result.assertions)
```

- [ ] **Step 2: Run baseline assertion tests and confirm they fail**

Run: `python -m pytest tests/release/baseline/test_assertions.py -q`

Expected: import failure.

- [ ] **Step 3: Implement baseline assertions**

```python
# tests/release/baseline/assertions.py
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BaselineResult:
    status: str
    assertions: list[dict]


def _record(name: str, passed: bool, message: str) -> dict:
    return {"capability": "baseline", "name": name, "status": "passed" if passed else "failed", "message": message}


def assert_baseline(*, fingerprint: dict, initial_primary: str) -> BaselineResult:
    assertions = []
    expected_secondary = "secondary" if initial_primary == "primary" else "primary"
    hubs = fingerprint["hubs"]
    assertions.append(_record("initial-primary-role", hubs[initial_primary]["hub_role"] == "primary", f"{initial_primary} is primary"))
    assertions.append(_record("secondary-role", hubs[expected_secondary]["hub_role"] in {"secondary", "standby"}, f"{expected_secondary} is passive"))
    managed = fingerprint["managed_clusters"]
    if managed["expectation_type"] == "count":
        assertions.append(_record("managed-cluster-count", managed["observed_active_count"] == managed["expected_count"], "Managed cluster count matches profile"))
    else:
        assertions.append(_record("managed-cluster-names", managed["observed_active_names"] == managed["expected_names"], "Managed cluster names match profile"))
    return BaselineResult(status="passed" if all(item["status"] == "passed" for item in assertions) else "failed", assertions=assertions)
```

- [ ] **Step 4: Wire fixture guard without live cluster mutation**

In `tests/release/conftest.py`, add a `baseline_manager` fixture only after a discovery client factory exists. Until live client creation is added, keep the fixture as an explicit skip for release lifecycle runs:

```python
@pytest.fixture(scope="session")
def baseline_manager():
    pytest.skip("release baseline manager requires a live Kubernetes discovery client")
```

This keeps normal unit tests import-safe and prevents accidental mutation.

- [ ] **Step 5: Run baseline tests**

Run: `python -m pytest tests/release/baseline/test_assertions.py -q`

Expected: `2 passed`.

- [ ] **Step 6: Commit baseline assertions**

```bash
git add tests/release/baseline/assertions.py tests/release/baseline/test_assertions.py tests/release/conftest.py
git commit -m "feat: assert release lab baseline"
```

## Final Verification

- [ ] Run baseline and readiness tests:

Run: `python -m pytest tests/release/baseline tests/release/checks/test_lab_readiness.py -q`

Expected: all selected tests pass.

- [ ] Run lifecycle guard:

Run: `python -m pytest tests/release/test_release_certification.py -q`

Expected: skipped without explicit profile or live baseline manager.

- [ ] Run the planning placeholder scan:

Run:

```bash
python - <<'PY'
from pathlib import Path
bad = ["TB" + "D", "TO" + "DO", "implement " + "later", "fill " + "in details", "handle " + "edge cases", "appropriate " + "error handling", "Similar " + "to Task"]
hits = []
for path in sorted(Path("docs/superpowers/plans").glob("*.md")):
    text = path.read_text(encoding="utf-8")
    for phrase in bad:
        if phrase in text:
            hits.append(f"{path}: contains rejected planning phrase {phrase!r}")
if hits:
    raise SystemExit("\n".join(hits))
PY
```

Expected: no matches.

- [ ] Update the progress tracker with status and verification evidence.
