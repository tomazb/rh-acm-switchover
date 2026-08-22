# SPDX-License-Identifier: MIT

from __future__ import annotations

DOCUMENTATION = r"""
---
module: acm_preflight_report
short_description: Build and write a preflight report artifact
description:
  - Aggregates preflight validation results into a structured JSON report and
    optionally writes it to disk. Supports check mode by validating the path and
    reporting whether the artifact would change without writing the file.
author:
  - ACM Switchover Contributors (@tomazb)
options:
  phase:
    description: Phase label for the report (e.g. C(preflight)).
    required: true
    type: str
  results:
    description: List of structured validation result dicts.
    required: true
    type: list
    elements: dict
  hubs:
    description: Hub connection info to embed in the report for traceability.
    required: true
    type: dict
  hub_identities:
    description: Live hub identity data to embed in the report for traceability.
    type: dict
    default: {}
  path:
    description: Destination path for the JSON report file. Skipped when not provided.
    type: str
    default: null
"""

EXAMPLES = r"""
- name: Write preflight report
  tomazb.acm_switchover.acm_preflight_report:
    phase: preflight
    results: "{{ acm_switchover_validation_results }}"
    hubs: "{{ acm_switchover_hubs }}"
    path: "{{ acm_switchover_execution.report_dir }}/preflight-report.json"
  register: report_result
"""

from datetime import datetime, timezone

from ansible.module_utils.basic import AnsibleModule

from ansible_collections.tomazb.acm_switchover.plugins.module_utils.artifacts import (
    ArtifactWriteError,
    write_json_artifact,
)
from ansible_collections.tomazb.acm_switchover.plugins.module_utils.validation import (
    ValidationError,
)

_DISTINCT_CONTEXT_RESULT_ID = "preflight-input-distinct-hub-contexts"


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


def sanitize_report_hubs(hubs: dict, hub_identities: dict | None = None) -> dict:
    """Return non-sensitive hub identity metadata for report artifacts."""
    hubs = hubs if isinstance(hubs, dict) else {}
    hub_identities = hub_identities if isinstance(hub_identities, dict) else {}
    ordered_roles = list(dict.fromkeys(("primary", "secondary", *hubs.keys(), *hub_identities.keys())))
    report_hubs = {}
    for role in ordered_roles:
        hub = hubs.get(role) or {}
        identity = hub_identities.get(role) or {}
        if not isinstance(hub, dict):
            hub = {}
        if not isinstance(identity, dict):
            identity = {}
        if not hub and not identity:
            continue
        report_hubs[role] = {
            "context": hub.get("context") or identity.get("context") or "",
            "cluster_uid": hub.get("cluster_uid") or identity.get("cluster_uid") or "",
        }
    return report_hubs


def _has_distinct_context_refusal(results: list[dict]) -> bool:
    return any(
        item.get("id") == _DISTINCT_CONTEXT_RESULT_ID and item.get("status") in {"fail", "error"} for item in results
    )


def _sanitize_distinct_context_refusal_results(results: list[dict]) -> list[dict]:
    """Retain only the stable SSA-01 refusal without other input diagnostics."""
    return [
        {**item, "details": {}}
        for item in results
        if item.get("id") == _DISTINCT_CONTEXT_RESULT_ID and item.get("status") in {"fail", "error"}
    ]


def build_preflight_report(
    phase: str,
    results: list[dict],
    hubs: dict,
    hub_identities: dict | None = None,
) -> dict:
    distinct_context_refusal = _has_distinct_context_refusal(results)
    report_results = _sanitize_distinct_context_refusal_results(results) if distinct_context_refusal else results
    summary = summarize_preflight_results(report_results)
    return {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "tomazb.acm_switchover",
        "phase": phase,
        "status": "pass" if summary["passed"] else "fail",
        "summary": summary,
        "hubs": {} if distinct_context_refusal else sanitize_report_hubs(hubs, hub_identities),
        "results": report_results,
    }


def write_report(report: dict, destination: str, check_mode: bool = False) -> tuple[str, bool, str | None]:
    """Write report to destination path.

    Returns:
        Tuple of (path, changed, error_message). error_message is None on success.
    """
    try:
        path, changed = write_json_artifact(report=report, destination=destination, check_mode=check_mode)
    except (ArtifactWriteError, ValidationError) as exc:
        return destination, False, str(exc)
    return path, changed, None


def main() -> None:
    module = AnsibleModule(
        argument_spec={
            "phase": {"type": "str", "required": True},
            "results": {"type": "list", "elements": "dict", "required": True},
            "hubs": {"type": "dict", "required": True},
            "hub_identities": {"type": "dict", "required": False, "default": {}},
            "path": {"type": "str", "required": False, "default": None},
        },
        supports_check_mode=True,
    )

    report = build_preflight_report(
        phase=module.params["phase"],
        results=module.params["results"],
        hubs=module.params["hubs"],
        hub_identities=module.params.get("hub_identities") or {},
    )
    output_path = None
    changed = False
    write_error = None
    if module.params["path"]:
        output_path, changed, write_error = write_report(report, module.params["path"], check_mode=module.check_mode)
        if write_error:
            module.fail_json(msg=write_error, report=report, path=output_path)
            return

    module.exit_json(changed=changed, report=report, path=output_path)


if __name__ == "__main__":
    main()
