from __future__ import annotations


def _lines_for_scenarios(items: list[dict]) -> list[str]:
    return [f"- `{item['scenario_id']}`: `{item.get('status', 'unknown')}`" for item in items]


def _lines_for_matrix_validation(matrix_validation: dict) -> list[str]:
    lines = [f"- Status: `{matrix_validation.get('status', 'unknown')}`"]
    issues = matrix_validation.get("issues") or []
    if issues:
        for issue in issues:
            stream = issue.get("stream") or "n/a"
            required = str(bool(issue.get("required", False))).lower()
            lines.append(
                f"- `{issue.get('scenario_id', 'unknown')}` / `{stream}`: "
                f"`{issue.get('status', 'unknown')}` "
                f"(`{issue.get('code', 'unknown')}`, required={required}) - "
                f"{issue.get('reason', 'no reason provided')}"
            )
        return lines
    lines.extend(f"- {reason}" for reason in matrix_validation.get("reasons", []))
    return lines


def render_release_report(summary: dict, manifest: dict) -> str:
    decision = "GO" if summary.get("status") == "passed" and summary.get("certification_eligible") else "NO-GO"
    matrix_validation = summary.get("matrix_validation", {})
    lines = [
        "# Release Validation Report",
        "",
        "## Run Identity",
        f"- Run ID: `{manifest.get('run_id', 'unknown')}`",
        f"- Profile: `{manifest.get('profile', {}).get('name', 'unknown')}`",
        f"- Mode: `{summary.get('release_mode', 'unknown')}`",
        "",
        "## Release Metadata Consistency",
        f"- Status: `{summary.get('release_metadata', {}).get('status', 'unknown')}`",
        "",
        "## Matrix Validation",
        *_lines_for_matrix_validation(matrix_validation),
        "",
        "## Required Scenario Results",
        *_lines_for_scenarios(summary.get("required_scenarios", [])),
        "",
        "## Optional Scenario Results",
        *_lines_for_scenarios(summary.get("optional_scenarios", [])),
        "",
        "## Mandatory Argo CD Certification",
        f"- Status: `{summary.get('mandatory_argocd', {}).get('status', 'unknown')}`",
        "",
        "## Runtime Parity Summary",
        f"- Status: `{summary.get('runtime_parity', {}).get('status', 'unknown')}`",
        "",
        "## Recovery Summary",
        f"- Status: `{summary.get('recovery', {}).get('status', 'unknown')}`",
        "",
        "## Artifact Redaction Summary",
        f"- Status: `{summary.get('artifact_redaction', {}).get('status', 'unknown')}`",
        "",
        "## Final Baseline Result",
        f"- Status: `{summary.get('final_baseline', {}).get('status', 'unknown')}`",
        "",
        "## Final Go/No-Go Decision",
        f"- Decision: **{decision}**",
    ]
    if summary.get("failure_reasons"):
        lines.extend(
            [
                "",
                "## Failure Reasons",
                *[f"- {reason}" for reason in summary["failure_reasons"]],
            ]
        )
    if summary.get("warnings"):
        lines.extend(["", "## Warnings", *[f"- {warning}" for warning in summary["warnings"]]])
    return "\n".join(lines) + "\n"
