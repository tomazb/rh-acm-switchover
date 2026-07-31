# SPDX-License-Identifier: MIT

from __future__ import annotations

import os

DOCUMENTATION = r"""
---
module: acm_safe_path_validate
short_description: Validate a controller-side path against the collection safety policy
description:
  - Validates a single controller-side path using the collection's shared safe-path rules.
  - Fails fast before playbooks touch files with modules such as C(stat) or C(slurp).
author:
  - ACM Switchover Contributors (@tomazb)
options:
  path:
    description: Controller-side path to validate.
    required: true
    type: str
  path_type:
    description:
      - Validation policy to apply.
      - C(safe) checks path syntax and allowed absolute roots.
      - C(artifact) also rejects symlink escapes before controller-side artifact reads or writes.
    type: str
    choices: [safe, artifact]
    default: safe
"""

EXAMPLES = r"""
- name: Validate checkpoint path before reading it on the controller
  tomazb.acm_switchover.acm_safe_path_validate:
    path: "{{ _argocd_resume_checkpoint_path_abs }}"
    path_type: artifact
"""

from ansible.module_utils.basic import AnsibleModule  # noqa: E402

from ansible_collections.tomazb.acm_switchover.plugins.module_utils.validation import (  # noqa: E402
    ValidationError,
    validate_report_artifact_path,
    validate_safe_path,
)


def _validate_existing_parent(path: str) -> None:
    absolute_path = os.path.abspath(path)
    parent = os.path.dirname(absolute_path) or os.getcwd()
    if not os.path.exists(parent):
        raise ValidationError(f"Parent directory '{parent}' does not exist for safe path '{path}'.")
    if not os.path.isdir(parent):
        raise ValidationError(f"Parent path '{parent}' is not a directory for safe path '{path}'.")


def main() -> None:
    module = AnsibleModule(
        argument_spec={
            "path": {"type": "str", "required": True},
            "path_type": {
                "type": "str",
                "choices": ["safe", "artifact"],
                "default": "safe",
            },
        },
        supports_check_mode=True,
    )

    try:
        if module.params["path_type"] == "artifact":
            validate_report_artifact_path(module.params["path"])
        else:
            validate_safe_path(module.params["path"])
            _validate_existing_parent(module.params["path"])
    except ValidationError as exc:
        module.fail_json(msg=str(exc))

    module.exit_json(changed=False)


if __name__ == "__main__":
    main()
