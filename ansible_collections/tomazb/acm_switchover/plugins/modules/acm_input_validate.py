# SPDX-License-Identifier: MIT

from __future__ import annotations

DOCUMENTATION = r"""
---
module: acm_input_validate
short_description: Validate switchover input variables
description:
  - Validates hub contexts, kubeconfig paths, and operation consistency before
    preflight or switchover execution. Returns a list of structured validation results.
author:
  - ACM Switchover Contributors (@tomazb)
options:
  hubs:
    description: Hub connection configuration with C(primary) and C(secondary) sub-keys.
    required: true
    type: dict
  operation:
    description: Operation parameters including C(method) and C(activation_method).
    required: true
    type: dict
  execution:
    description: Execution mode and report directory configuration.
    required: true
    type: dict
  features:
    description: Feature flags including Argo CD mode and observability skip.
    required: true
    type: dict
"""

EXAMPLES = r"""
- name: Validate switchover inputs
  tomazb.acm_switchover.acm_input_validate:
    hubs: "{{ acm_switchover_hubs }}"
    operation: "{{ acm_switchover_operation }}"
    execution: "{{ acm_switchover_execution }}"
    features: "{{ acm_switchover_features }}"
  register: validation_result
"""

from ansible.module_utils.basic import AnsibleModule

from ansible_collections.tomazb.acm_switchover.plugins.module_utils.result import ValidationResult
from ansible_collections.tomazb.acm_switchover.plugins.module_utils.validation import (
    ValidationError,
    validate_context_name,
    validate_operation_inputs,
    validate_safe_path,
)


def _pass_result(result_id: str, message: str, details: dict | None = None) -> dict:
    return ValidationResult(
        id=result_id,
        severity="info",
        status="pass",
        message=message,
        details=details or {},
    ).to_dict()


def _fail_result(result_id: str, message: str, recommended_action: str) -> dict:
    return ValidationResult(
        id=result_id,
        severity="critical",
        status="fail",
        message=message,
        recommended_action=recommended_action,
    ).to_dict()


def build_input_validation_results(params: dict) -> list[dict]:
    hubs = params.get("hubs", {})
    operation = params.get("operation", {})
    execution = params.get("execution", {})
    features = params.get("features", {})

    results: list[dict] = []

    primary_context = hubs.get("primary", {}).get("context", "")
    secondary_context = hubs.get("secondary", {}).get("context", "")
    primary_kubeconfig = hubs.get("primary", {}).get("kubeconfig", "")
    secondary_kubeconfig = hubs.get("secondary", {}).get("kubeconfig", "")
    checkpoint_path = execution.get("checkpoint", {}).get("path")
    mode = execution.get("mode", "execute")

    try:
        validate_context_name(primary_context)
        results.append(_pass_result("preflight-input-primary-context", "primary context is valid"))
    except ValidationError as exc:
        results.append(_fail_result("preflight-input-primary-context", str(exc), "Set a valid primary context"))

    if mode in {"execute", "validate", "dry_run"} and not secondary_context:
        results.append(
            _fail_result(
                "preflight-input-secondary-context",
                "secondary context is required for collection preflight and switchover runs",
                "Set acm_switchover_hubs.secondary.context",
            )
        )
    else:
        try:
            validate_context_name(secondary_context)
            results.append(_pass_result("preflight-input-secondary-context", "secondary context is valid"))
        except ValidationError as exc:
            results.append(
                _fail_result("preflight-input-secondary-context", str(exc), "Set a valid secondary context")
            )

    for result_id, path_value in (
        ("preflight-input-primary-kubeconfig", primary_kubeconfig),
        ("preflight-input-secondary-kubeconfig", secondary_kubeconfig),
        ("preflight-input-checkpoint-path", checkpoint_path),
    ):
        if not path_value:
            continue
        try:
            validate_safe_path(path_value)
            results.append(_pass_result(result_id, f"{result_id} is safe", {"path": path_value}))
        except ValidationError as exc:
            results.append(
                _fail_result(
                    result_id,
                    str(exc),
                    "Use a relative path without traversal or shell metacharacters",
                )
            )

    try:
        normalized_operation = validate_operation_inputs(operation=operation, execution=execution, features=features)
        results.append(
            _pass_result(
                "preflight-input-operation",
                "operation inputs are internally consistent",
                normalized_operation,
            )
        )
    except ValidationError as exc:
        results.append(
            _fail_result(
                "preflight-input-operation",
                str(exc),
                "Adjust method, activation_method, execution mode, or Argo CD flags to a supported combination",
            )
        )

    return results


def summarize_input_validation(results: list[dict]) -> dict:
    critical_failures = [
        item for item in results if item["severity"] == "critical" and item["status"] in {"fail", "error"}
    ]
    return {
        "passed": len(critical_failures) == 0,
        "critical_failures": len(critical_failures),
        "results": results,
    }


def main() -> None:
    module = AnsibleModule(
        argument_spec={
            "hubs": {"type": "dict", "required": True},
            "operation": {"type": "dict", "required": True},
            "execution": {"type": "dict", "required": True},
            "features": {"type": "dict", "required": True},
        },
        supports_check_mode=True,
    )

    results = build_input_validation_results(module.params)
    summary = summarize_input_validation(results)
    module.exit_json(changed=False, **summary)


if __name__ == "__main__":
    main()
