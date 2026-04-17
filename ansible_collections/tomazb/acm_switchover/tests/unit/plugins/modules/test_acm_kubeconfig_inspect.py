"""Tests for the acm_kubeconfig_inspect collection module."""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone

import pytest
import yaml

from ansible_collections.tomazb.acm_switchover.plugins.modules.acm_kubeconfig_inspect import (
    inspect_kubeconfig_auth,
    run_module,
)


def write_kubeconfig(tmp_path, data):
    path = tmp_path / "kubeconfig.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def _jwt_with_exp(expiry: datetime) -> str:
    payload = {"exp": int(expiry.timestamp())}
    payload_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).decode("utf-8").rstrip("=")
    return f"header.{payload_b64}.signature"


def _pem_data(label: str) -> str:
    return base64.b64encode(
        (
            f"-----BEGIN {label}-----\n"
            f"{label.lower()}-fixture\n"
            f"-----END {label}-----\n"
        ).encode("utf-8")
    ).decode("utf-8")


def _kubeconfig_for_user(user: dict, context_name: str = "primary-hub") -> dict:
    return {
        "apiVersion": "v1",
        "kind": "Config",
        "clusters": [{"name": "primary-cluster", "cluster": {"server": "https://example.invalid"}}],
        "contexts": [{"name": context_name, "context": {"cluster": "primary-cluster", "user": "primary-user"}}],
        "current-context": context_name,
        "users": [{"name": "primary-user", "user": user}],
    }


def test_bearer_jwt_valid_returns_pass(tmp_path):
    expiry = datetime(2099, 1, 1, 12, 0, tzinfo=timezone.utc)
    kubeconfig = write_kubeconfig(
        tmp_path,
        _kubeconfig_for_user({"token": _jwt_with_exp(expiry)}),
    )

    result = inspect_kubeconfig_auth(str(kubeconfig), "primary-hub")

    assert result["status"] == "pass"
    assert result["severity"] == "info"
    assert result["auth_type"] == "bearer_jwt"


def test_bearer_jwt_expired_returns_critical_fail(tmp_path):
    expiry = datetime(2000, 1, 1, 12, 0, tzinfo=timezone.utc)
    kubeconfig = write_kubeconfig(
        tmp_path,
        _kubeconfig_for_user({"token": _jwt_with_exp(expiry)}),
    )

    result = inspect_kubeconfig_auth(str(kubeconfig), "primary-hub")

    assert result["status"] == "fail"
    assert result["severity"] == "critical"
    assert result["auth_type"] == "bearer_jwt"


def test_bearer_jwt_near_expiry_returns_warn(tmp_path):
    expiry = datetime(2099, 1, 1, 15, 0, tzinfo=timezone.utc)
    kubeconfig = write_kubeconfig(
        tmp_path,
        _kubeconfig_for_user({"token": _jwt_with_exp(expiry)}),
    )

    result = inspect_kubeconfig_auth(str(kubeconfig), "primary-hub", warning_hours=1_000_000)

    assert result["status"] == "warn"
    assert result["severity"] == "warning"
    assert result["auth_type"] == "bearer_jwt"


def test_bearer_jwt_without_exp_returns_warn(tmp_path):
    payload_b64 = base64.urlsafe_b64encode(json.dumps({"sub": "system:serviceaccount:test"}).encode("utf-8")).decode(
        "utf-8"
    ).rstrip("=")
    kubeconfig = write_kubeconfig(
        tmp_path,
        _kubeconfig_for_user({"token": f"header.{payload_b64}.signature"}),
    )

    result = inspect_kubeconfig_auth(str(kubeconfig), "primary-hub")

    assert result["status"] == "warn"
    assert result["severity"] == "warning"
    assert result["auth_type"] == "bearer_jwt"


def test_bearer_jwt_with_non_numeric_exp_returns_warn(tmp_path):
    payload_b64 = base64.urlsafe_b64encode(json.dumps({"exp": "not-a-timestamp"}).encode("utf-8")).decode(
        "utf-8"
    ).rstrip("=")
    kubeconfig = write_kubeconfig(
        tmp_path,
        _kubeconfig_for_user({"token": f"header.{payload_b64}.signature"}),
    )

    result = inspect_kubeconfig_auth(str(kubeconfig), "primary-hub")

    assert result["status"] == "warn"
    assert result["severity"] == "warning"
    assert result["auth_type"] == "bearer_opaque"


def test_invalid_jwt_format_returns_warn(tmp_path):
    kubeconfig = write_kubeconfig(tmp_path, _kubeconfig_for_user({"token": "not.a-valid-jwt"}))

    result = inspect_kubeconfig_auth(str(kubeconfig), "primary-hub")

    assert result["status"] == "warn"
    assert result["severity"] == "warning"
    assert result["auth_type"] == "bearer_opaque"


@pytest.mark.parametrize(
    "payload_bytes",
    [
        b"\xff\xfe\xfd",
        json.dumps(["not", "an", "object"]).encode("utf-8"),
    ],
)
def test_malformed_jwt_payload_returns_warn_opaque(tmp_path, payload_bytes):
    payload_b64 = base64.urlsafe_b64encode(payload_bytes).decode("utf-8").rstrip("=")
    kubeconfig = write_kubeconfig(
        tmp_path,
        _kubeconfig_for_user({"token": f"header.{payload_b64}.signature"}),
    )

    result = inspect_kubeconfig_auth(str(kubeconfig), "primary-hub")

    assert result["status"] == "warn"
    assert result["severity"] == "warning"
    assert result["auth_type"] == "bearer_opaque"


def test_exec_auth_returns_warn_without_execution(tmp_path):
    kubeconfig = write_kubeconfig(tmp_path, _kubeconfig_for_user({"exec": {"command": "oc", "args": ["whoami"]}}))

    result = inspect_kubeconfig_auth(str(kubeconfig), "primary-hub")

    assert result["status"] == "warn"
    assert result["severity"] == "warning"
    assert result["auth_type"] == "exec"
    assert "not executed" in result["message"]


def test_auth_provider_returns_warn_without_execution(tmp_path):
    kubeconfig = write_kubeconfig(
        tmp_path,
        _kubeconfig_for_user({"auth-provider": {"name": "oidc", "config": {"id-token": "example"}}}),
    )

    result = inspect_kubeconfig_auth(str(kubeconfig), "primary-hub")

    assert result["status"] == "warn"
    assert result["severity"] == "warning"
    assert result["auth_type"] == "auth_provider"
    assert "not executed" in result["message"]


def test_client_certificate_auth_fields_are_recognized(tmp_path):
    kubeconfig = write_kubeconfig(
        tmp_path,
        _kubeconfig_for_user(
            {
                "client-certificate-data": _pem_data("CERTIFICATE"),
                "client-key-data": _pem_data("PRIVATE KEY"),
            }
        ),
    )

    result = inspect_kubeconfig_auth(str(kubeconfig), "primary-hub")

    assert result["status"] == "pass"
    assert result["severity"] == "info"
    assert result["auth_type"] == "client_cert"


def test_missing_context_raises_validation_error(tmp_path):
    kubeconfig = write_kubeconfig(tmp_path, _kubeconfig_for_user({"token": "header.payload.signature"}))

    with pytest.raises(ValueError, match="context 'missing-context' not found"):
        inspect_kubeconfig_auth(str(kubeconfig), "missing-context")


def test_missing_user_raises_validation_error(tmp_path):
    kubeconfig = write_kubeconfig(
        tmp_path,
        {
            "apiVersion": "v1",
            "kind": "Config",
            "clusters": [{"name": "primary-cluster", "cluster": {"server": "https://example.invalid"}}],
            "contexts": [{"name": "primary-hub", "context": {"cluster": "primary-cluster", "user": "missing-user"}}],
            "users": [],
        },
    )

    with pytest.raises(ValueError, match="user 'missing-user' not found for context 'primary-hub'"):
        inspect_kubeconfig_auth(str(kubeconfig), "primary-hub")


@pytest.mark.parametrize(
    ("kubeconfig_data", "error_match"),
    [
        (["not", "a", "mapping"], "kubeconfig must be a YAML mapping"),
        (_kubeconfig_for_user({"token": "header.payload.signature"}) | {"contexts": "bad"}, "'contexts' must be a list"),
        (_kubeconfig_for_user({"token": "header.payload.signature"}) | {"users": "bad"}, "'users' must be a list"),
        (_kubeconfig_for_user({"token": "header.payload.signature"}) | {"contexts": ["bad"]}, "'contexts' entries must be mappings"),
        (_kubeconfig_for_user({"token": "header.payload.signature"}) | {"users": ["bad"]}, "'users' entries must be mappings"),
        (
            _kubeconfig_for_user({"token": "header.payload.signature"})
            | {"contexts": [{"name": "primary-hub", "context": "bad"}]},
            "context entry 'primary-hub' must contain a mapping under 'context'",
        ),
        (
            _kubeconfig_for_user({"token": "header.payload.signature"})
            | {"contexts": [{"name": "primary-hub"}]},
            "context entry 'primary-hub' is missing required 'context' mapping",
        ),
        (
            _kubeconfig_for_user({"token": "header.payload.signature"})
            | {"users": [{"name": "primary-user", "user": "bad"}]},
            "user entry 'primary-user' must contain a mapping under 'user'",
        ),
        (
            _kubeconfig_for_user({"token": "header.payload.signature"})
            | {"users": [{"name": "primary-user"}]},
            "user entry 'primary-user' is missing required 'user' mapping",
        ),
    ],
)
def test_malformed_kubeconfig_structure_raises_value_error(tmp_path, kubeconfig_data, error_match):
    kubeconfig = write_kubeconfig(tmp_path, kubeconfig_data)

    with pytest.raises(ValueError, match=error_match):
        inspect_kubeconfig_auth(str(kubeconfig), "primary-hub")


def test_run_module_exits_with_inspection_result(monkeypatch):
    captured = {}

    class FakeModule:
        def __init__(self, *args, **kwargs):
            self.params = {
                "kubeconfig": "/tmp/fake-kubeconfig",
                "context": "primary-hub",
                "warning_hours": 9,
            }

        def exit_json(self, **kwargs):
            captured["exit"] = kwargs

        def fail_json(self, **kwargs):
            raise AssertionError(f"unexpected fail_json: {kwargs}")

    monkeypatch.setattr(
        "ansible_collections.tomazb.acm_switchover.plugins.modules.acm_kubeconfig_inspect.AnsibleModule",
        FakeModule,
    )
    monkeypatch.setattr(
        "ansible_collections.tomazb.acm_switchover.plugins.modules.acm_kubeconfig_inspect.inspect_kubeconfig_auth",
        lambda kubeconfig, context, warning_hours=4: {
            "status": "pass",
            "severity": "info",
            "auth_type": "bearer_jwt",
            "message": f"{kubeconfig}:{context}:{warning_hours}",
        },
    )

    run_module()

    assert captured["exit"] == {
        "changed": False,
        "status": "pass",
        "severity": "info",
        "auth_type": "bearer_jwt",
        "message": "/tmp/fake-kubeconfig:primary-hub:9",
    }


def test_run_module_maps_value_error_to_fail_json(monkeypatch):
    captured = {}

    class FakeModule:
        def __init__(self, *args, **kwargs):
            self.params = {
                "kubeconfig": "/tmp/fake-kubeconfig",
                "context": "missing",
                "warning_hours": 4,
            }

        def exit_json(self, **kwargs):
            raise AssertionError(f"unexpected exit_json: {kwargs}")

        def fail_json(self, **kwargs):
            captured["fail"] = kwargs

    monkeypatch.setattr(
        "ansible_collections.tomazb.acm_switchover.plugins.modules.acm_kubeconfig_inspect.AnsibleModule",
        FakeModule,
    )
    monkeypatch.setattr(
        "ansible_collections.tomazb.acm_switchover.plugins.modules.acm_kubeconfig_inspect.inspect_kubeconfig_auth",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("bad kubeconfig")),
    )

    run_module()

    assert captured["fail"] == {"msg": "bad kubeconfig"}
