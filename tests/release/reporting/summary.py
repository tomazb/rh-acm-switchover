from __future__ import annotations


def _failed_required_scenarios(required_scenarios: list[dict]) -> list[str]:
    return [item["scenario_id"] for item in required_scenarios if item.get("status") != "passed"]


def _matrix_validation_payload(matrix_validation: object | None) -> dict:
    if matrix_validation is None:
        return {"status": "passed", "reasons": []}
    if isinstance(matrix_validation, dict):
        return matrix_validation
    return {"status": "failed", "reasons": ["matrix validation payload is malformed"]}


def build_summary(
    *,
    release_mode: str,
    certification_eligible: bool,
    required_scenarios: list[dict],
    optional_scenarios: list[dict],
    runtime_parity: dict,
    artifact_redaction: dict,
    final_baseline: dict,
    recovery: dict,
    mandatory_argocd: dict,
    release_metadata: dict,
    matrix_validation: object | None = None,
) -> dict:
    matrix_validation = _matrix_validation_payload(matrix_validation)
    failure_reasons: list[str] = []
    if release_mode != "certification":
        failure_reasons.append("release mode is not certification")
    if not certification_eligible:
        failure_reasons.append("run is not certification eligible")
    if matrix_validation.get("status") != "passed":
        reasons = matrix_validation.get("reasons") or ["matrix validation failed"]
        for reason in reasons:
            failure_reasons.append(f"matrix validation failed: {reason}")
    for scenario_id in _failed_required_scenarios(required_scenarios):
        failure_reasons.append(f"required scenario failed: {scenario_id}")
    runtime_parity_expectations = {"passed"} if release_mode == "certification" else {"passed", "not_applicable"}
    status_expectations = {
        "runtime parity": runtime_parity_expectations,
        "artifact redaction": {"passed"},
        "final baseline": {"passed"},
        "mandatory Argo CD": {"passed"},
        "release metadata": {"passed"},
    }
    payloads = {
        "runtime parity": runtime_parity,
        "artifact redaction": artifact_redaction,
        "final baseline": final_baseline,
        "mandatory Argo CD": mandatory_argocd,
        "release metadata": release_metadata,
    }
    for name, payload in payloads.items():
        if payload.get("status") not in status_expectations[name]:
            failure_reasons.append(f"{name} failed")
    if recovery.get("hard_stops"):
        failure_reasons.append("recovery hard stop remains open")
    return {
        "schema_version": 1,
        "status": "passed" if not failure_reasons else "failed",
        "certification_eligible": certification_eligible and not failure_reasons,
        "release_mode": release_mode,
        "required_scenarios": required_scenarios,
        "optional_scenarios": optional_scenarios,
        "mandatory_argocd": mandatory_argocd,
        "release_metadata": release_metadata,
        "matrix_validation": matrix_validation,
        "runtime_parity": runtime_parity,
        "artifact_redaction": artifact_redaction,
        "final_baseline": final_baseline,
        "recovery": recovery,
        "warnings": [],
        "failure_reasons": failure_reasons,
    }
