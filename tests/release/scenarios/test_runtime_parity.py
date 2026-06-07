from __future__ import annotations

import json
from pathlib import Path

from tests.release.orchestrator import _normalized_runtime_sources
from tests.release.reporting.artifacts import ReleaseArtifacts
from tests.release.scenarios.runtime_parity import (
    CAPABILITY_REQUIRED_FIELDS,
    ComparisonRecord,
    compare_normalized_records,
    normalize_argocd_management,
    normalize_checkpoint_artifact,
    normalize_decommission_artifact,
    normalize_operation_artifact,
    normalize_preflight,
    normalize_rbac_bootstrap_artifact,
    normalize_report_artifact,
    runtime_parity_not_applicable,
    write_runtime_parity_artifact,
)
from tests.release.test_release_certification import execute_runtime_parity


def test_comparison_record_serializes_required_fields() -> None:
    record = ComparisonRecord(
        capability="preflight validation",
        scenario_id="preflight",
        streams=("python", "ansible"),
        status="passed",
        required_fields=("status", "check_ids"),
        differences=[],
        evidence_paths=("scenario-results.json",),
    )

    payload = record.to_dict()

    assert payload["capability"] == "preflight validation"
    assert payload["streams"] == ["python", "ansible"]
    assert payload["required_fields"] == ["status", "check_ids"]


def test_runtime_parity_required_fields_cover_release_1710_guardrails() -> None:
    assert {
        "preflight validation",
        "Argo CD management",
        "switchover artifacts",
        "restore-only artifacts",
        "decommission artifacts",
        "RBAC/bootstrap artifacts",
        "checkpoints",
        "report artifacts",
    }.issubset(CAPABILITY_REQUIRED_FIELDS)
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


def test_compare_normalized_records_passes_equal_required_fields() -> None:
    record = compare_normalized_records(
        capability="activation",
        scenario_id="python-passive-switchover",
        python={"status": "passed", "restore_name": "restore-acm", "duration": 10},
        ansible={"status": "passed", "restore_name": "restore-acm", "duration": 12},
        required_fields=("status", "restore_name"),
    )

    assert record.status == "passed"
    assert record.differences == []


def test_compare_normalized_records_fails_missing_source_field() -> None:
    record = compare_normalized_records(
        capability="activation",
        scenario_id="python-passive-switchover",
        python={"status": "passed"},
        ansible={"status": "passed", "restore_name": "restore-acm"},
        required_fields=("status", "restore_name"),
    )

    assert record.status == "failed"
    assert record.differences[0]["field"] == "restore_name"


def test_normalize_preflight_sorts_check_sets() -> None:
    normalized = normalize_preflight(
        {
            "status": "passed",
            "critical_failure_count": 0,
            "warning_failure_count": 1,
            "check_ids": ["z", "a"],
            "failed_check_ids": ["z"],
        }
    )

    assert normalized["check_ids"] == ["a", "z"]
    assert normalized["failed_check_ids"] == ["z"]


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


def test_normalize_release_artifact_guardrails_ignore_implementation_metadata(tmp_path: Path) -> None:
    operation = normalize_operation_artifact(
        {
            "schema_version": 1,
            "source": "python-cli",
            "status": "pass",
            "generated_at": "2026-05-12T00:00:00Z",
            "phases": {"activation": {"status": "pass"}, "finalization": {"status": "pass"}},
        },
        "switchover-report.json",
    )
    decommission = normalize_decommission_artifact({"status": "passed"}, "decommission-report.json")
    rbac = normalize_rbac_bootstrap_artifact(
        {
            "status": "pass",
            "assets_applied": [
                "deploy/rbac/clusterrole.yaml",
                "deploy/rbac/decommission-clusterrole.yaml",
            ],
        },
        "rbac-bootstrap-report.json",
    )
    checkpoint = normalize_checkpoint_artifact(
        {
            "config": {"resume_summary": {"resume_start_phase": "activation"}},
            "errors": [{"phase": "activation", "error": "boom"}],
            "hub_identities": {"primary": {"context": "hub-a", "cluster_uid": "uid-a"}},
        },
        scenario_id="python-passive-switchover",
    )
    report = normalize_report_artifact({"schema_version": "1.0", "source": "tomazb.acm_switchover"}, str(tmp_path))

    assert operation == {
        "schema_version": "1",
        "status": "pass",
        "phase_ids": ["activation", "finalization"],
        "report_filename": "switchover-report.json",
    }
    assert decommission == {"status": "passed", "report_filename": "decommission-report.json"}
    assert rbac == {
        "bootstrap_status": "pass",
        "manifest_assets": [
            "deploy/rbac/clusterrole.yaml",
            "deploy/rbac/decommission-clusterrole.yaml",
        ],
        "include_decommission": True,
        "report_filename": "rbac-bootstrap-report.json",
    }
    assert checkpoint == {
        "resume_start_phase": "activation",
        "skipped_phases": ["preflight", "primary_prep"],
        "checkpoint_error_count": 1,
        "identity_bound": True,
    }
    assert report == {"schema_version": "1.0", "source_present": True, "safe_path_validated": True}


def test_normalize_checkpoint_artifact_ignores_non_mapping_resume_summary() -> None:
    checkpoint = normalize_checkpoint_artifact(
        {
            "config": {"resume_summary": "activation"},
            "operational_data": {"resume_summary": ["activation"]},
            "errors": [{"phase": "activation", "error": "boom"}],
        },
        scenario_id="checkpoint-resume",
    )

    assert checkpoint == {
        "resume_start_phase": None,
        "skipped_phases": [],
        "checkpoint_error_count": 1,
        "identity_bound": False,
    }


def test_normalized_runtime_sources_populates_release_artifact_guardrails(tmp_path: Path) -> None:
    python_dir = tmp_path / "python"
    ansible_dir = tmp_path / "ansible"
    rbac_dir = tmp_path / "rbac"
    python_dir.mkdir()
    ansible_dir.mkdir()
    rbac_dir.mkdir()
    (python_dir / "switchover-report.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "source": "acm_switchover.py",
                "status": "pass",
                "phases": {"activation": {"status": "pass"}},
            }
        ),
        encoding="utf-8",
    )
    (ansible_dir / "switchover-report.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "source": "tomazb.acm_switchover",
                "phases": {"activation": {"status": "pass"}},
            }
        ),
        encoding="utf-8",
    )
    (python_dir / "state.json").write_text(json.dumps({"completed_steps": ["activation"]}), encoding="utf-8")
    (ansible_dir / "checkpoint.json").write_text(json.dumps({"completed_phases": ["activation"]}), encoding="utf-8")
    (rbac_dir / "rbac-bootstrap-report.json").write_text(
        json.dumps(
            {
                "status": "pass",
                "assets_applied": [
                    "deploy/rbac/clusterrole.yaml",
                    "deploy/rbac/decommission-clusterrole.yaml",
                ],
            }
        ),
        encoding="utf-8",
    )

    sources = _normalized_runtime_sources(
        [
            {
                "stream": "python",
                "scenario_id": "python-passive-switchover",
                "stdout_path": str(python_dir / "stdout.txt"),
                "reports": [
                    {
                        "type": "switchover",
                        "path": str(python_dir / "switchover-report.json"),
                    }
                ],
            },
            {
                "stream": "ansible",
                "scenario_id": "ansible-passive-switchover",
                "stdout_path": str(ansible_dir / "stdout.txt"),
                "reports": [
                    {
                        "type": "switchover",
                        "path": str(ansible_dir / "switchover-report.json"),
                    }
                ],
            },
            {
                "stream": "ansible",
                "scenario_id": "rbac-bootstrap",
                "stdout_path": str(rbac_dir / "stdout.txt"),
                "reports": [
                    {
                        "type": "rbac-bootstrap",
                        "path": str(rbac_dir / "rbac-bootstrap-report.json"),
                    }
                ],
            },
        ]
    )

    assert sources["switchover artifacts"]["python"] == sources["switchover artifacts"]["ansible"]
    assert sources["checkpoints"]["python"] == sources["checkpoints"]["ansible"]
    assert sources["report artifacts"]["python"] == sources["report artifacts"]["ansible"]
    assert sources["RBAC/bootstrap artifacts"]["ansible"] == {
        "bootstrap_status": "pass",
        "manifest_assets": [
            "deploy/rbac/clusterrole.yaml",
            "deploy/rbac/decommission-clusterrole.yaml",
        ],
        "include_decommission": True,
        "report_filename": "rbac-bootstrap-report.json",
    }


def test_write_runtime_parity_artifact_sets_failed_status(tmp_path: Path) -> None:
    artifacts = ReleaseArtifacts.create(root=tmp_path, run_id="run-1")
    failed = ComparisonRecord(
        "activation",
        "python-passive-switchover",
        ("python", "ansible"),
        "failed",
        ("status",),
        [{"field": "status"}],
        (),
    )

    write_runtime_parity_artifact(artifacts=artifacts, comparisons=[failed])

    payload = json.loads((artifacts.run_dir / "runtime-parity.json").read_text(encoding="utf-8"))
    assert payload["status"] == "failed"
    assert payload["comparisons"][0]["capability"] == "activation"


def test_execute_runtime_parity_compares_matching_sources(tmp_path: Path) -> None:
    comparisons = execute_runtime_parity(
        normalized_sources={
            "preflight validation": {
                "python": {
                    "status": "passed",
                    "critical_failure_count": 0,
                    "warning_failure_count": 0,
                    "check_ids": ["a"],
                    "failed_check_ids": [],
                },
                "ansible": {
                    "status": "passed",
                    "critical_failure_count": 0,
                    "warning_failure_count": 0,
                    "check_ids": ["a"],
                    "failed_check_ids": [],
                },
            }
        }
    )

    assert comparisons[0].status == "passed"


def test_runtime_parity_writes_not_applicable_without_supported_reports(tmp_path: Path) -> None:
    artifacts = ReleaseArtifacts.create(root=tmp_path, run_id="run-1")

    write_runtime_parity_artifact(
        artifacts=artifacts,
        comparisons=[runtime_parity_not_applicable("preflight validation", "runtime-parity", "missing source reports")],
    )

    payload = json.loads((artifacts.run_dir / "runtime-parity.json").read_text(encoding="utf-8"))
    assert payload["status"] == "not_applicable"
    assert payload["comparisons"][0]["status"] == "not_applicable"
