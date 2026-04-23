# SPDX-License-Identifier: MIT

from __future__ import annotations

DOCUMENTATION = r"""
---
module: acm_report_artifact
short_description: Validate and write a controller-side JSON report artifact
description:
  - Applies the collection safe-path policy to a report artifact path before touching the controller filesystem.
  - Writes structured JSON reports with stable pretty-print formatting.
  - Supports check mode by validating the path without creating directories or files.
author:
  - ACM Switchover Contributors (@tomazb)
options:
  path:
    description: Destination path for the JSON report artifact.
    required: true
    type: str
  report:
    description: Structured JSON-compatible report payload to write.
    required: true
    type: dict
"""

EXAMPLES = r"""
- name: Write switchover report artifact
  tomazb.acm_switchover.acm_report_artifact:
    path: "{{ (acm_switchover_execution.report_dir | default('./artifacts')) ~ '/switchover-report.json' }}"
    report: "{{ acm_switchover_report }}"
"""

from ansible.module_utils.basic import AnsibleModule

from ansible_collections.tomazb.acm_switchover.plugins.module_utils.artifacts import (
    ArtifactWriteError,
    write_json_artifact,
)
from ansible_collections.tomazb.acm_switchover.plugins.module_utils.validation import ValidationError


def main() -> None:
    module = AnsibleModule(
        argument_spec={
            "path": {"type": "str", "required": True},
            "report": {"type": "dict", "required": True},
        },
        supports_check_mode=True,
    )

    destination = module.params["path"]
    report = module.params["report"]

    try:
        output_path = write_json_artifact(report=report, destination=destination, check_mode=module.check_mode)
    except (ArtifactWriteError, ValidationError) as exc:
        module.fail_json(msg=str(exc), path=destination)
        return

    module.exit_json(changed=not module.check_mode, path=output_path)


if __name__ == "__main__":
    main()
