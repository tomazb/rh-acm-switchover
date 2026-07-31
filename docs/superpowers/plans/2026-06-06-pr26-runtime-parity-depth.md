# PR26 Runtime Parity Depth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deepen release runtime parity so certification fails on Argo CD, checkpoint/resume, and RBAC/bootstrap behavior drift instead of passing on shallow artifact metadata alone.

**Architecture:** Keep most of the work in the release harness under `tests/release/`. Add only one tiny mirrored runtime metadata surface: a persisted `resume_summary.resume_start_phase` written into Python durable state and collection checkpoint `operational_data`, then let the harness derive richer parity fields from that plus existing reports, errors, identity binding, and cluster discovery.

**Tech Stack:** Python, pytest, release harness helpers in `tests/release/`, Python CLI durable state in `lib/`, collection checkpoint action plugin in `ansible_collections/tomazb/acm_switchover/plugins/action/`, Graphify.

---

## File Map

- Create: `docs/superpowers/plans/2026-06-06-pr26-runtime-parity-depth.md`
- Modify: `tests/release/scenarios/runtime_parity.py`
- Modify: `tests/release/scenarios/test_runtime_parity.py`
- Modify: `tests/release/orchestrator.py`
- Modify: `tests/release/test_orchestrator.py`
- Modify: `tests/release/test_release_certification.py`
- Modify: `lib/workflow.py`
- Modify: `tests/test_main_phase_flow.py`
- Modify: `ansible_collections/tomazb/acm_switchover/plugins/action/checkpoint_phase.py`
- Modify: `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/action/test_checkpoint_phase_runtime.py`
- Modify: `ansible_collections/tomazb/acm_switchover/docs/artifact-schema.md`
- Modify: `thermos-resolution-plan.md`

## Task 1: Expand Runtime Parity Normalizers

**Files:**
- Modify: `tests/release/scenarios/runtime_parity.py`
- Modify: `tests/release/scenarios/test_runtime_parity.py`

- [ ] **Step 1: Add failing tests for the richer capability fields and normalizers**

```python
from tests.release.scenarios.runtime_parity import (
    CAPABILITY_REQUIRED_FIELDS,
    normalize_argocd_management,
    normalize_checkpoint_artifact,
    normalize_rbac_bootstrap_artifact,
)


def test_runtime_parity_required_fields_cover_pr26_guardrails() -> None:
    assert CAPABILITY_REQUIRED_FIELDS["Argo CD management"] == (
        "run_id_present",
        "paused_application_names",
        "paused_application_count",
        "run_id_preserved_for_retry",
    )
    assert CAPABILITY_REQUIRED_FIELDS["checkpoints"] == (
        "resume_start_phase",
        "skipped_phases",
        "checkpoint_error_count",
        "identity_bound",
    )
    assert CAPABILITY_REQUIRED_FIELDS["RBAC/bootstrap artifacts"] == (
        "bootstrap_status",
        "manifest_assets",
        "include_decommission",
        "report_filename",
    )


def test_normalize_argocd_management_tracks_retry_preservation() -> None:
    normalized = normalize_argocd_management(
        {
            "run_id": "run-123",
            "paused_application_names": ["secondary:argocd/app-b", "secondary:argocd/app-a"],
            "run_id_preserved_for_retry": "preserved",
        }
    )

    assert normalized == {
        "run_id_present": True,
        "paused_application_names": ["secondary:argocd/app-a", "secondary:argocd/app-b"],
        "paused_application_count": 2,
        "run_id_preserved_for_retry": "preserved",
    }


def test_normalize_checkpoint_artifact_uses_resume_summary_and_existing_identity() -> None:
    normalized = normalize_checkpoint_artifact(
        {
            "config": {"resume_summary": {"resume_start_phase": "activation"}},
            "errors": [{"phase": "activation", "error": "boom"}],
            "hub_identities": {"primary": {"context": "hub-a", "cluster_uid": "uid-a"}},
        },
        scenario_id="python-passive-switchover",
    )

    assert normalized == {
        "resume_start_phase": "activation",
        "skipped_phases": ["preflight", "primary_prep"],
        "checkpoint_error_count": 1,
        "identity_bound": True,
    }


def test_normalize_rbac_bootstrap_artifact_tracks_exact_assets() -> None:
    normalized = normalize_rbac_bootstrap_artifact(
        {
            "status": "pass",
            "assets_applied": [
                "deploy/rbac/clusterrole.yaml",
                "deploy/rbac/decommission-clusterrole.yaml",
            ],
        },
        "rbac-bootstrap-report.json",
    )

    assert normalized == {
        "bootstrap_status": "pass",
        "manifest_assets": [
            "deploy/rbac/clusterrole.yaml",
            "deploy/rbac/decommission-clusterrole.yaml",
        ],
        "include_decommission": True,
        "report_filename": "rbac-bootstrap-report.json",
    }
```

- [ ] **Step 2: Run the new runtime parity unit tests and confirm they fail**

Run: `source "/home/tomaz/sources/rh-acm-switchover/.venv/bin/activate" && python -m pytest tests/release/scenarios/test_runtime_parity.py -k "pr26_guardrails or retry_preservation or resume_summary or exact_assets" -q`

Expected: failures for missing capability fields and mismatched normalizer outputs.

- [ ] **Step 3: Implement the richer field table and normalizers**

```python
# tests/release/scenarios/runtime_parity.py
CAPABILITY_REQUIRED_FIELDS = {
    "preflight validation": (
        "status",
        "critical_failure_count",
        "warning_failure_count",
        "check_ids",
        "failed_check_ids",
    ),
    "Argo CD management": (
        "run_id_present",
        "paused_application_names",
        "paused_application_count",
        "run_id_preserved_for_retry",
    ),
    "switchover artifacts": (
        "schema_version",
        "status",
        "phase_ids",
        "report_filename",
    ),
    "restore-only artifacts": (
        "schema_version",
        "status",
        "phase_ids",
        "report_filename",
    ),
    "decommission artifacts": (
        "status",
        "report_filename",
    ),
    "RBAC/bootstrap artifacts": (
        "bootstrap_status",
        "manifest_assets",
        "include_decommission",
        "report_filename",
    ),
    "checkpoints": (
        "resume_start_phase",
        "skipped_phases",
        "checkpoint_error_count",
        "identity_bound",
    ),
    "report artifacts": (
        "schema_version",
        "source_present",
        "safe_path_validated",
    ),
}

PHASE_ORDER_BY_SCENARIO = {
    "python-passive-switchover": ("preflight", "primary_prep", "activation", "post_activation", "finalization"),
    "ansible-passive-switchover": ("preflight", "primary_prep", "activation", "post_activation", "finalization"),
    "argocd-managed-switchover": ("preflight", "primary_prep", "activation", "post_activation", "finalization"),
    "python-restore-only": ("preflight", "activation", "post_activation", "finalization"),
    "ansible-restore-only": ("preflight", "activation", "post_activation", "finalization"),
    "checkpoint-resume": ("preflight", "primary_prep", "activation", "post_activation", "finalization"),
}


def normalize_argocd_management(source: dict[str, Any]) -> dict[str, Any]:
    names = _sorted_list(source.get("paused_application_names"))
    return {
        "run_id_present": bool(source.get("run_id")),
        "paused_application_names": names,
        "paused_application_count": len(names),
        "run_id_preserved_for_retry": str(source.get("run_id_preserved_for_retry", "not_applicable")),
    }


def _skipped_phases_for_resume(*, scenario_id: str, resume_start_phase: str | None) -> list[str]:
    if not resume_start_phase:
        return []
    phase_order = PHASE_ORDER_BY_SCENARIO.get(scenario_id, ())
    if resume_start_phase not in phase_order:
        return []
    return list(phase_order[: phase_order.index(resume_start_phase)])


def normalize_checkpoint_artifact(source: dict[str, Any], *, scenario_id: str) -> dict[str, Any]:
    config = source.get("config") if isinstance(source.get("config"), dict) else {}
    operational_data = source.get("operational_data") if isinstance(source.get("operational_data"), dict) else {}
    resume_summary = config.get("resume_summary") or operational_data.get("resume_summary") or {}
    errors = source.get("errors") if isinstance(source.get("errors"), list) else []
    resume_start_phase = resume_summary.get("resume_start_phase")
    return {
        "resume_start_phase": resume_start_phase,
        "skipped_phases": _skipped_phases_for_resume(
            scenario_id=scenario_id,
            resume_start_phase=resume_start_phase,
        ),
        "checkpoint_error_count": len(errors),
        "identity_bound": bool(source.get("hub_identities") or source.get("operation_identity")),
    }


def normalize_rbac_bootstrap_artifact(source: dict[str, Any], report_filename: str) -> dict[str, Any]:
    assets = _sorted_list(str(asset) for asset in source.get("assets_applied", []) if asset)
    return {
        "bootstrap_status": str(source.get("status", "unknown")),
        "manifest_assets": assets,
        "include_decommission": any("decommission" in asset for asset in assets),
        "report_filename": report_filename,
    }
```

- [ ] **Step 4: Run the runtime parity unit suite**

Run: `source "/home/tomaz/sources/rh-acm-switchover/.venv/bin/activate" && python -m pytest tests/release/scenarios/test_runtime_parity.py -q`

Expected: all runtime parity unit tests pass.

- [ ] **Step 5: Commit the richer normalizers**

```bash
git add tests/release/scenarios/runtime_parity.py tests/release/scenarios/test_runtime_parity.py
git commit -m "test: deepen runtime parity normalizers"
```

## Task 2: Persist Python Resume Start Metadata

**Files:**
- Modify: `lib/workflow.py`
- Modify: `tests/test_main_phase_flow.py`

- [ ] **Step 1: Add a failing test that resumed Python flows persist `resume_summary.resume_start_phase`**

```python
def test_resume_from_activation_persists_resume_summary(self, tmp_path):
    from lib.utils import Phase, StateManager

    state_file = tmp_path / "state.json"
    state = StateManager(str(state_file))
    state.set_phase(Phase.ACTIVATION)

    call_order = []
    args = make_switchover_args(state_file=str(state_file))

    with patch(
        "acm_switchover._run_phase_preflight",
        side_effect=phase_stub("preflight", call_order),
    ), patch(
        "acm_switchover._run_phase_primary_prep",
        side_effect=phase_stub("primary_prep", call_order),
    ), patch(
        "acm_switchover._run_phase_activation",
        side_effect=phase_stub("activation", call_order),
    ), patch(
        "acm_switchover._run_phase_post_activation",
        side_effect=phase_stub("post_activation", call_order),
    ), patch(
        "acm_switchover._run_phase_finalization",
        side_effect=phase_stub("finalization", call_order),
    ):
        assert run_switchover(args, state, Mock(), Mock(), Mock()) is True

    reloaded = StateManager(str(state_file))
    assert reloaded.get_config("resume_summary") == {"resume_start_phase": Phase.ACTIVATION.value}
```

- [ ] **Step 2: Run the focused phase-flow test and confirm it fails**

Run: `source "/home/tomaz/sources/rh-acm-switchover/.venv/bin/activate" && python -m pytest tests/test_main_phase_flow.py -k "resume_summary" -q`

Expected: assertion failure because no `resume_summary` is persisted yet.

- [ ] **Step 3: Persist Python `resume_summary` when a phase flow starts from a mid-stream phase**

```python
# lib/workflow.py
current_phase = state.get_current_phase()
runnable_phases = {phase for _, phases, _ in phase_flow for phase in phases}
if current_phase not in runnable_phases:
    return fail_phase(
        state,
        WORKFLOW_NON_RUNNABLE_PHASE_MESSAGE % (current_phase.value, flow_name),
        logger,
    )

fresh_start_states = set(phase_flow[0][1])
if current_phase not in fresh_start_states:
    state.set_config(
        "resume_summary",
        {
            "resume_start_phase": current_phase.value,
        },
    )
```

- [ ] **Step 4: Run the focused phase-flow tests**

Run: `source "/home/tomaz/sources/rh-acm-switchover/.venv/bin/activate" && python -m pytest tests/test_main_phase_flow.py -k "resume_from_activation or resume_summary" -q`

Expected: the new resume-summary test passes and the existing resume-ordering tests stay green.

- [ ] **Step 5: Commit the Python resume metadata**

```bash
git add lib/workflow.py tests/test_main_phase_flow.py
git commit -m "fix: persist python resume start metadata"
```

## Task 3: Persist Collection Resume Start Metadata

**Files:**
- Modify: `ansible_collections/tomazb/acm_switchover/plugins/action/checkpoint_phase.py`
- Modify: `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/action/test_checkpoint_phase_runtime.py`
- Modify: `ansible_collections/tomazb/acm_switchover/docs/artifact-schema.md`

- [ ] **Step 1: Add a failing runtime test for `resume_summary.resume_start_phase`**

```python
def test_action_module_enter_persists_resume_start_phase_when_resuming(tmp_path):
    import json

    checkpoint_file = tmp_path / "checkpoint.json"
    checkpoint_file.write_text(
        json.dumps(
            {
                "schema_version": "2.0",
                "phase": "preflight",
                "completed_phases": ["preflight"],
                "operational_data": {},
                "operation_identity": build_operation_identity(hubs={}, operation={}),
                "errors": [],
                "report_refs": [],
                "updated_at": "2026-01-01T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    action = _make_checkpoint_action(
        {
            "phase": "primary_prep",
            "checkpoint": {
                "enabled": True,
                "backend": "file",
                "path": str(checkpoint_file),
            },
            "status": "enter",
        }
    )

    result = action.run(task_vars=_task_vars_for_mode("execute"))

    assert result["skipped_phase"] is False
    assert result["checkpoint"]["operational_data"]["resume_summary"] == {
        "resume_start_phase": "primary_prep",
    }
```

- [ ] **Step 2: Run the focused checkpoint action test and confirm it fails**

Run: `source "/home/tomaz/sources/rh-acm-switchover/.venv/bin/activate" && python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/plugins/action/test_checkpoint_phase_runtime.py -k "resume_start_phase" -q`

Expected: assertion failure because `status: enter` does not persist any resume summary yet.

- [ ] **Step 3: Persist `operational_data.resume_summary.resume_start_phase` on the first resumed enter**

```python
# ansible_collections/.../plugins/action/checkpoint_phase.py
if status == "enter":
    already_done = False if execution_mode == "validate" else not should_resume_phase(checkpoint_data, phase)
    current_operational_data = checkpoint_data.setdefault("operational_data", {})
    resume_summary = current_operational_data.setdefault("resume_summary", {})
    resumed_execution = bool(checkpoint_data.get("completed_phases")) and not already_done
    if resumed_execution and not resume_summary.get("resume_start_phase"):
        resume_summary["resume_start_phase"] = phase
        checkpoint_data["updated_at"] = datetime.now(timezone.utc).isoformat()
        save_result = self._save_checkpoint(path, checkpoint_data)
        if save_result is not None and save_result.get("failed"):
            return save_result
    return {
        "changed": False,
        "checkpoint": checkpoint_data,
        "skipped_phase": already_done,
    }
```

- [ ] **Step 4: Document the new checkpoint field and run focused tests**

```markdown
`operational_data.resume_summary.resume_start_phase` records the first phase executed after reusing a checkpoint so release parity can compare resume behavior without parsing logs.
```

Run: `source "/home/tomaz/sources/rh-acm-switchover/.venv/bin/activate" && python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/plugins/action/test_checkpoint_phase_runtime.py -q`

Expected: the new runtime test and the existing checkpoint action tests pass.

- [ ] **Step 5: Commit the collection resume metadata**

```bash
git add ansible_collections/tomazb/acm_switchover/plugins/action/checkpoint_phase.py ansible_collections/tomazb/acm_switchover/tests/unit/plugins/action/test_checkpoint_phase_runtime.py ansible_collections/tomazb/acm_switchover/docs/artifact-schema.md
git commit -m "fix: persist collection resume start metadata"
```

## Task 4: Wire Orchestrator Runtime Evidence

**Files:**
- Modify: `tests/release/orchestrator.py`
- Modify: `tests/release/test_orchestrator.py`
- Modify: `tests/release/test_release_certification.py`
- Modify: `tests/release/scenarios/test_runtime_parity.py`

- [ ] **Step 1: Add failing orchestrator tests for Argo CD runtime evidence and live RBAC consistency**

```python
def test_normalized_runtime_sources_populates_argocd_management_from_reports_and_pause_markers(tmp_path: Path) -> None:
    python_dir = tmp_path / "python"
    ansible_dir = tmp_path / "ansible"
    python_dir.mkdir()
    ansible_dir.mkdir()
    (python_dir / "switchover-report.json").write_text(
        json.dumps({"schema_version": "1.0", "source": "acm_switchover.py", "argocd": {"run_id": "run-1"}}),
        encoding="utf-8",
    )
    (ansible_dir / "switchover-report.json").write_text(
        json.dumps({"schema_version": "1.0", "source": "tomazb.acm_switchover", "argocd": {"run_id": "run-1"}}),
        encoding="utf-8",
    )
    (python_dir / "state.json").write_text(
        json.dumps(
            {
                "config": {
                    "resume_summary": {"resume_start_phase": "activation"},
                    "argocd_discovery_namespaces": {"secondary": ["argocd"]},
                },
            }
        ),
        encoding="utf-8",
    )
    (ansible_dir / "checkpoint.json").write_text(
        json.dumps(
            {
                "operational_data": {
                    "resume_summary": {"resume_start_phase": "activation"},
                    "argocd_discovery_namespaces": {"secondary": ["argocd"]},
                }
            }
        ),
        encoding="utf-8",
    )

    discovery_clients = {
        "primary": FakeDiscoveryClient(primary=True),
        "secondary": FakeDiscoveryClient(
            primary=False,
            applications_by_namespace={
                "argocd": [
                    {
                        "metadata": {
                            "namespace": "argocd",
                            "name": "app-a",
                            "annotations": {"acm-switchover.argoproj.io/paused-by": "run-1"},
                        }
                    }
                ]
            },
        ),
    }

    results = [
        {
            "stream": "python",
            "scenario_id": "argocd-managed-switchover",
            "stdout_path": str(python_dir / "stdout.txt"),
            "reports": [{"type": "switchover", "path": str(python_dir / "switchover-report.json")}],
        },
        {
            "stream": "ansible",
            "scenario_id": "argocd-managed-switchover",
            "stdout_path": str(ansible_dir / "stdout.txt"),
            "reports": [{"type": "switchover", "path": str(ansible_dir / "switchover-report.json")}],
        },
    ]

    sources = _normalized_runtime_sources(results, discovery_clients=discovery_clients)

    assert sources["Argo CD management"]["python"] == sources["Argo CD management"]["ansible"]
    assert sources["Argo CD management"]["python"]["paused_application_names"] == ["secondary:argocd/app-a"]


def test_runtime_parity_records_rbac_live_consistency_failure(tmp_path: Path) -> None:
    artifacts = ReleaseArtifacts.create(root=tmp_path, run_id="run-1")
    results = [
        {
            "stream": "ansible",
            "scenario_id": "rbac-bootstrap",
            "status": "passed",
            "reports": [{"type": "rbac-bootstrap", "path": str(tmp_path / "rbac-bootstrap-report.json")}],
        },
        {
            "stream": "local",
            "scenario_id": "rbac-bootstrap-live",
            "status": "failed",
            "assertions": [{"status": "failed", "name": "core/pods:get@cluster"}],
        },
    ]

    (tmp_path / "rbac-bootstrap-report.json").write_text(
        json.dumps({"status": "pass", "assets_applied": ["deploy/rbac/clusterrole.yaml"]}),
        encoding="utf-8",
    )

    payload = _runtime_parity(artifacts, results, discovery_clients={})

    assert payload["status"] == "failed"
    assert any(item["capability"] == "RBAC live consistency" for item in payload["comparisons"])
```

- [ ] **Step 2: Run the orchestrator-focused tests and confirm they fail**

Run: `source "/home/tomaz/sources/rh-acm-switchover/.venv/bin/activate" && python -m pytest tests/release/test_orchestrator.py -k "argocd_management or rbac_live_consistency" -q`

Expected: failures for missing helper signatures and missing comparison records.

- [ ] **Step 3: Implement orchestrator source extraction and consistency records**

```python
# tests/release/orchestrator.py
def _report_by_type(result: dict, report_type: str) -> dict | None:
    for report in result.get("reports", []):
        if report.get("type") == report_type:
            return _load_report(report.get("path", ""))
    return None


def _state_or_checkpoint_payload(result: dict) -> dict | None:
    artifact_dir = _result_artifact_dir(result)
    if artifact_dir is None:
        return None
    for filename in ("state.json", "checkpoint.json"):
        payload = _load_report(str(artifact_dir / filename))
        if payload:
            return payload
    return None


def _argocd_pause_names(
    *,
    discovery_clients: Mapping[str, HubDiscoveryClient],
    run_id: str | None,
    namespaces_by_hub: dict[str, list[str]],
) -> list[str]:
    if not run_id:
        return []
    names: list[str] = []
    for hub_name, client in discovery_clients.items():
        namespaces = namespaces_by_hub.get(hub_name) or [None]
        for namespace in namespaces:
            for item in client.list_resources("applications.argoproj.io", namespace):
                metadata = item.get("metadata") or {}
                annotations = metadata.get("annotations") or {}
                if annotations.get("acm-switchover.argoproj.io/paused-by") == run_id:
                    names.append(f"{hub_name}:{metadata.get('namespace', namespace)}/{metadata['name']}")
    return sorted(set(names))


def _add_argocd_source(
    sources: dict[str, dict[str, dict]],
    result: dict,
    *,
    discovery_clients: Mapping[str, HubDiscoveryClient],
) -> None:
    if result.get("scenario_id") != "argocd-managed-switchover" or result.get("stream") not in {"python", "ansible"}:
        return
    report = _report_by_type(result, "switchover") or {}
    state_payload = _state_or_checkpoint_payload(result) or {}
    config = state_payload.get("config") if isinstance(state_payload.get("config"), dict) else {}
    operational_data = state_payload.get("operational_data") if isinstance(state_payload.get("operational_data"), dict) else {}
    report_run_id = ((report.get("argocd") or {}).get("run_id") if isinstance(report.get("argocd"), dict) else None) or None
    state_run_id = config.get("argocd_run_id") or operational_data.get("argocd_run_id")
    namespaces_by_hub = config.get("argocd_discovery_namespaces") or operational_data.get("argocd_discovery_namespaces") or {}
    run_id_preserved_for_retry = (
        "preserved"
        if report_run_id and state_run_id and report_run_id == state_run_id
        else "not_applicable"
    )
    sources.setdefault("Argo CD management", {})[result["stream"]] = normalize_argocd_management(
        {
            "run_id": report_run_id or state_run_id,
            "paused_application_names": _argocd_pause_names(
                discovery_clients=discovery_clients,
                run_id=report_run_id or state_run_id,
                namespaces_by_hub=namespaces_by_hub,
            ),
            "run_id_preserved_for_retry": run_id_preserved_for_retry,
        }
    )


def _rbac_live_consistency_record(results: list[dict]) -> ComparisonRecord | None:
    bootstrap = next((item for item in results if item.get("scenario_id") == "rbac-bootstrap" and item.get("stream") == "ansible"), None)
    live = next((item for item in results if item.get("scenario_id") == "rbac-bootstrap-live" and item.get("stream") == "local"), None)
    if bootstrap is None or live is None:
        return None
    bootstrap_status = bootstrap.get("status", "unknown")
    live_status = live.get("status", "unknown")
    differences = [] if (bootstrap_status == "passed" and live_status in {"passed", "skipped"}) else [
        {"field": "live_status", "ansible": bootstrap_status, "local": live_status}
    ]
    return ComparisonRecord(
        capability="RBAC live consistency",
        scenario_id="rbac-bootstrap-live",
        streams=("ansible", "local"),
        status="passed" if not differences else "failed",
        required_fields=("bootstrap_status", "live_status"),
        differences=differences,
        evidence_paths=(),
    )
```

- [ ] **Step 4: Run the release helper suite**

Run: `source "/home/tomaz/sources/rh-acm-switchover/.venv/bin/activate" && python -m pytest tests/release/scenarios/test_runtime_parity.py tests/release/test_orchestrator.py tests/release/test_release_certification.py -q`

Expected: the release helper suite passes with new Argo CD and RBAC consistency coverage.

- [ ] **Step 5: Commit the runtime evidence wiring**

```bash
git add tests/release/orchestrator.py tests/release/test_orchestrator.py tests/release/test_release_certification.py tests/release/scenarios/test_runtime_parity.py
git commit -m "test: harden runtime parity orchestration"
```

## Task 5: Tracker Update And Verification

**Files:**
- Modify: `thermos-resolution-plan.md`

- [ ] **Step 1: Record the spec and implementation verification in the tracker**

```markdown
| 26 | ready_for_review | `test/thermos-26-runtime-parity-depth` | `.worktrees/thermos-26-parity-depth` | F43, F44 gate | not opened | Added spec `docs/superpowers/specs/2026-06-06-pr26-runtime-parity-depth-design.md`; release runtime parity now covers Argo CD runtime evidence, resume-start metadata, and RBAC live consistency. Verification: `python -m pytest tests/release/scenarios/test_runtime_parity.py tests/release/test_orchestrator.py tests/release/test_release_certification.py tests/test_main_phase_flow.py ansible_collections/tomazb/acm_switchover/tests/unit/plugins/action/test_checkpoint_phase_runtime.py -q`; `graphify update .`; `git diff --check`; final `./run_tests.sh` (record any unchanged pre-existing formatter drift if still present). |
```

- [ ] **Step 2: Run focused release and resume verification**

Run: `source "/home/tomaz/sources/rh-acm-switchover/.venv/bin/activate" && python -m pytest tests/release/scenarios/test_runtime_parity.py tests/release/test_orchestrator.py tests/release/test_release_certification.py tests/test_main_phase_flow.py ansible_collections/tomazb/acm_switchover/tests/unit/plugins/action/test_checkpoint_phase_runtime.py -q`

Expected: all targeted release, Python resume, and collection checkpoint tests pass.

- [ ] **Step 3: Refresh Graphify after code changes**

Run: `graphify update .`

Expected: Graphify completes successfully from the PR26 worktree.

- [ ] **Step 4: Run diff hygiene and broad verification**

Run: `git diff --check`

Expected: no whitespace or patch-format errors.

Run: `source "/home/tomaz/sources/rh-acm-switchover/.venv/bin/activate" && ./run_tests.sh`

Expected: either full pass, or the same pre-existing unrelated formatter drift already documented in `thermos-resolution-plan.md`; if the known unrelated drift still reproduces, record that explicitly in the tracker instead of treating it as a PR26 regression.

- [ ] **Step 5: Commit tracker and verification updates**

```bash
git add thermos-resolution-plan.md
git commit -m "docs: record PR26 runtime parity verification"
```
