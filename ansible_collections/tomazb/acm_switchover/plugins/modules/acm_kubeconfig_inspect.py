# SPDX-License-Identifier: MIT

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from pathlib import Path

import yaml
from ansible.module_utils.basic import AnsibleModule

DOCUMENTATION = r"""
---
module: acm_kubeconfig_inspect
short_description: Inspect kubeconfig auth without loading global client state
description:
  - Parses a kubeconfig file directly from YAML and classifies the configured
    authentication method for a requested context.
  - Warns for dynamic auth plugins and opaque bearer tokens, and evaluates JWT
    bearer token expiration without executing external commands.
author:
  - ACM Switchover Contributors (@tomazb)
options:
  kubeconfig:
    description: Path to the kubeconfig file to inspect.
    required: true
    type: path
  context:
    description: Context name to inspect in the kubeconfig.
    required: true
    type: str
  warning_hours:
    description: Warn when a bearer JWT expires in fewer than this many hours.
    type: int
    default: 4
"""

EXAMPLES = r"""
- name: Inspect kubeconfig auth for a hub context
  tomazb.acm_switchover.acm_kubeconfig_inspect:
    kubeconfig: "{{ acm_switchover_hubs.primary.kubeconfig }}"
    context: "{{ acm_switchover_hubs.primary.context }}"
    warning_hours: 4
  register: kubeconfig_auth
"""


def _find_named(items: list[dict], name: str) -> dict | None:
    return next((item for item in items if item.get("name") == name), None)


def _decode_jwt_exp(token: str) -> tuple[datetime | None, str | None]:
    parts = token.split(".")
    if len(parts) != 3:
        return None, "invalid JWT format"

    payload = parts[1]
    payload += "=" * (-len(payload) % 4)

    try:
        claims = json.loads(base64.urlsafe_b64decode(payload.encode("utf-8")))
    except (ValueError, json.JSONDecodeError):
        return None, "invalid JWT payload"

    exp = claims.get("exp")
    if exp is None:
        return None, "token has no expiration claim"
    return datetime.fromtimestamp(exp, tz=timezone.utc), None


def inspect_kubeconfig_auth(kubeconfig: str, context: str, warning_hours: int = 4) -> dict:
    config = yaml.safe_load(Path(kubeconfig).read_text(encoding="utf-8")) or {}
    contexts = config.get("contexts", [])
    users = config.get("users", [])

    ctx = _find_named(contexts, context)
    if ctx is None:
        raise ValueError(f"context '{context}' not found in kubeconfig")

    user_name = ctx.get("context", {}).get("user")
    user_entry = _find_named(users, user_name)
    if user_entry is None:
        raise ValueError(f"user '{user_name}' not found for context '{context}'")

    user_cfg = user_entry.get("user", {})

    if "exec" in user_cfg:
        return {
            "status": "warn",
            "severity": "warning",
            "auth_type": "exec",
            "message": "kubeconfig uses exec authentication; external auth plugins are not executed during inspection",
        }

    if "auth-provider" in user_cfg:
        return {
            "status": "warn",
            "severity": "warning",
            "auth_type": "auth_provider",
            "message": "kubeconfig uses auth-provider authentication; provider plugins are not executed during inspection",
        }

    if "client-certificate" in user_cfg or "client-certificate-data" in user_cfg:
        return {
            "status": "pass",
            "severity": "info",
            "auth_type": "client_cert",
            "message": "kubeconfig uses client certificate authentication; token expiry is not applicable",
        }

    token = user_cfg.get("token") or user_cfg.get("tokenFile")
    if token:
        expires_at, decode_error = _decode_jwt_exp(token)
        if decode_error:
            auth_type = "bearer_jwt" if decode_error == "token has no expiration claim" else "bearer_opaque"
            return {
                "status": "warn",
                "severity": "warning",
                "auth_type": auth_type,
                "message": f"static bearer token could not be fully evaluated: {decode_error}",
            }

        hours_until_expiry = (expires_at - datetime.now(timezone.utc)).total_seconds() / 3600
        result = {
            "auth_type": "bearer_jwt",
            "expires_at": expires_at.isoformat(),
            "hours_until_expiry": hours_until_expiry,
        }

        if hours_until_expiry < 0:
            return {
                **result,
                "status": "fail",
                "severity": "critical",
                "message": "static bearer JWT is expired",
            }
        if hours_until_expiry < warning_hours:
            return {
                **result,
                "status": "warn",
                "severity": "warning",
                "message": "static bearer JWT is nearing expiration",
            }
        return {
            **result,
            "status": "pass",
            "severity": "info",
            "message": "static bearer JWT is valid and not nearing expiration",
        }

    if "username" in user_cfg or "password" in user_cfg:
        return {
            "status": "pass",
            "severity": "info",
            "auth_type": "basic",
            "message": "kubeconfig uses basic authentication; token expiry is not applicable",
        }

    return {
        "status": "pass",
        "severity": "info",
        "auth_type": "non_bearer",
        "message": "kubeconfig does not use bearer token authentication; token expiry is not applicable",
    }


def run_module() -> None:
    module = AnsibleModule(
        argument_spec={
            "kubeconfig": {"type": "path", "required": True},
            "context": {"type": "str", "required": True},
            "warning_hours": {"type": "int", "required": False, "default": 4},
        },
        supports_check_mode=True,
    )

    try:
        result = inspect_kubeconfig_auth(
            module.params["kubeconfig"],
            module.params["context"],
            warning_hours=module.params["warning_hours"],
        )
    except ValueError as exc:
        module.fail_json(msg=str(exc))
        return

    module.exit_json(changed=False, **result)


def main() -> None:
    run_module()


if __name__ == "__main__":
    main()
