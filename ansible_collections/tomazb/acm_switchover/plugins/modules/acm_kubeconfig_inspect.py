# SPDX-License-Identifier: MIT

from __future__ import annotations

import base64
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import yaml
from ansible.module_utils.basic import AnsibleModule
from yaml import YAMLError

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


def _require_list_field(config: dict, field_name: str) -> list[dict]:
    if field_name not in config or config[field_name] is None:
        raise ValueError(f"kubeconfig is missing required '{field_name}' list")
    value = config.get(field_name, [])
    if not isinstance(value, list):
        raise ValueError(f"'{field_name}' must be a list in kubeconfig")
    return value


def _require_mapping_entries(items: list[dict], field_name: str) -> None:
    if any(not isinstance(item, dict) for item in items):
        raise ValueError(f"'{field_name}' entries must be mappings")


def _require_string_field(user_cfg: dict, field_name: str, user_name: str) -> None:
    if field_name in user_cfg and not isinstance(user_cfg[field_name], str):
        raise ValueError(f"user entry '{user_name}' must define '{field_name}' as a string")


def _decode_jwt_exp(token: str) -> tuple[datetime | None, str | None]:
    parts = token.split(".")
    if len(parts) != 3:
        return None, "invalid JWT format"

    payload = parts[1]
    payload += "=" * (-len(payload) % 4)

    try:
        claims = json.loads(base64.urlsafe_b64decode(payload.encode("utf-8")))
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
        return None, "invalid JWT payload"
    if not isinstance(claims, dict):
        return None, "invalid JWT payload"

    exp = claims.get("exp")
    if exp is None:
        return None, "token has no expiration claim"
    try:
        return datetime.fromtimestamp(exp, tz=timezone.utc), None
    except (TypeError, ValueError, OverflowError):
        return None, "invalid JWT expiration claim"


def _load_kubeconfig(kubeconfig: str) -> dict:
    try:
        content = Path(kubeconfig).read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"unable to read kubeconfig '{kubeconfig}': {exc}") from exc

    try:
        config = yaml.safe_load(content) or {}
    except YAMLError as exc:
        raise ValueError(f"invalid kubeconfig YAML in '{kubeconfig}': {exc}") from exc

    if not isinstance(config, dict):
        raise ValueError("kubeconfig must be a YAML mapping")
    return config


def _normalize_token_file_path(token_file: object, kubeconfig: str, user_name: str) -> Path:
    try:
        token_file_str = os.fspath(token_file)
    except TypeError as exc:
        raise ValueError(f"user entry '{user_name}' must define 'tokenFile' as a string or path") from exc
    if not token_file_str or not token_file_str.strip():
        raise ValueError(f"user entry '{user_name}' defines an empty tokenFile path")

    token_file_path = Path(token_file_str)

    if not token_file_path.is_absolute():
        token_file_path = Path(kubeconfig).resolve().parent / token_file_path
    return token_file_path


def _load_token_file(token_file: object, kubeconfig: str, user_name: str) -> str:
    token_file_path = _normalize_token_file_path(token_file, kubeconfig, user_name)
    try:
        token_value = token_file_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ValueError(f"unable to read tokenFile '{token_file_path}': {exc}") from exc
    if not token_value:
        raise ValueError(f"user entry '{user_name}' tokenFile resolved to empty content")
    return token_value


def inspect_kubeconfig_auth(kubeconfig: str, context: str, warning_hours: int = 4) -> dict:
    if warning_hours < 0:
        raise ValueError("warning_hours must be non-negative")

    config = _load_kubeconfig(kubeconfig)
    contexts = _require_list_field(config, "contexts")
    users = _require_list_field(config, "users")
    _require_mapping_entries(contexts, "contexts")
    _require_mapping_entries(users, "users")

    ctx = _find_named(contexts, context)
    if ctx is None:
        raise ValueError(f"context '{context}' not found in kubeconfig")

    if "context" not in ctx:
        raise ValueError(f"context entry '{context}' is missing required 'context' mapping")

    context_cfg = ctx.get("context")
    if not isinstance(context_cfg, dict):
        raise ValueError(f"context entry '{context}' must contain a mapping under 'context'")

    user_name = context_cfg.get("user")
    if not user_name:
        raise ValueError(f"context entry '{context}' is missing required 'user' reference")

    user_entry = _find_named(users, user_name)
    if user_entry is None:
        raise ValueError(f"user '{user_name}' not found for context '{context}'")

    if "user" not in user_entry:
        raise ValueError(f"user entry '{user_name}' is missing required 'user' mapping")

    user_cfg = user_entry.get("user")
    if not isinstance(user_cfg, dict):
        raise ValueError(f"user entry '{user_name}' must contain a mapping under 'user'")

    if "exec" in user_cfg and not isinstance(user_cfg["exec"], dict):
        raise ValueError(f"user entry '{user_name}' must define 'exec' as a mapping")
    if "auth-provider" in user_cfg and not isinstance(user_cfg["auth-provider"], dict):
        raise ValueError(f"user entry '{user_name}' must define 'auth-provider' as a mapping")
    for field_name in ("client-certificate", "client-certificate-data", "client-key", "client-key-data"):
        _require_string_field(user_cfg, field_name, user_name)

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

    token_file = user_cfg.get("tokenFile")
    token = None
    if token_file is not None:
        token = _load_token_file(token_file, kubeconfig, user_name)
    else:
        token = user_cfg.get("token")
        if token is not None and not isinstance(token, str):
            raise ValueError(f"user entry '{user_name}' must define 'token' as a string")
        if token is not None and not token.strip():
            raise ValueError(f"user entry '{user_name}' defines an empty bearer token")
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
        if "username" not in user_cfg or "password" not in user_cfg:
            raise ValueError(f"user entry '{user_name}' basic auth requires both 'username' and 'password'")
        if not isinstance(user_cfg["username"], str) or not isinstance(user_cfg["password"], str):
            raise ValueError(f"user entry '{user_name}' must define basic auth 'username' and 'password' as strings")
        if not user_cfg["username"].strip() or not user_cfg["password"].strip():
            raise ValueError(f"user entry '{user_name}' basic auth 'username' and 'password' must be non-empty")
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
