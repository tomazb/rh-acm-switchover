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
