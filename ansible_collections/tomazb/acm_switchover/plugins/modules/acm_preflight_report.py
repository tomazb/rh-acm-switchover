# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

DOCUMENTATION = r"""
---
module: acm_preflight_report
short_description: Build and write a preflight report artifact
description:
  - Aggregates preflight validation results into a structured JSON report and
    optionally writes it to disk. Supports check mode (skips file write).
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
