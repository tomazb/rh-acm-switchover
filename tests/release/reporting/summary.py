from __future__ import annotations


def _failed_required_scenarios(required_scenarios: list[dict]) -> list[str]:
    return [
        item["scenario_id"] for item in required_scenarios if item.get("status") not in {"passed", "not_applicable"}
    ]


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
) -> dict:
    failure_reasons: list[str] = []
    if release_mode != "certification":
        failure_reasons.append("release mode is not certification")
    if not certification_eligible:
        failure_reasons.append("run is not certification eligible")
    for scenario_id in _failed_required_scenarios(required_scenarios):
        failure_reasons.append(f"required scenario failed: {scenario_id}")
    for name, payload in {
        "runtime parity": runtime_parity,
        "artifact redaction": artifact_redaction,
        "final baseline": final_baseline,
        "mandatory Argo CD": mandatory_argocd,
        "release metadata": release_metadata,
    }.items():
        if payload.get("status") != "passed":
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
        "runtime_parity": runtime_parity,
        "artifact_redaction": artifact_redaction,
        "final_baseline": final_baseline,
        "recovery": recovery,
        "warnings": [],
        "failure_reasons": failure_reasons,
    }
