# SPDX-License-Identifier: MIT

from __future__ import annotations

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
"""

EXAMPLES = r"""
- name: Validate checkpoint path before reading it on the controller
  tomazb.acm_switchover.acm_safe_path_validate:
    path: "{{ _argocd_resume_checkpoint_path_abs }}"
"""

from ansible.module_utils.basic import AnsibleModule

from ansible_collections.tomazb.acm_switchover.plugins.module_utils.validation import (
    ValidationError,
    validate_safe_path,
)


def main() -> None:
    module = AnsibleModule(
        argument_spec={
            "path": {"type": "str", "required": True},
        },
        supports_check_mode=True,
    )

    try:
        validate_safe_path(module.params["path"])
    except ValidationError as exc:
        module.fail_json(msg=str(exc))

    module.exit_json(changed=False)


if __name__ == "__main__":
    main()
