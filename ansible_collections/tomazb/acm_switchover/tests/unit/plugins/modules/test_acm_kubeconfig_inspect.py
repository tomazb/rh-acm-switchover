"""Tests for the acm_kubeconfig_inspect collection module."""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone

import pytest
import yaml

from ansible_collections.tomazb.acm_switchover.plugins.modules.acm_kubeconfig_inspect import (
    inspect_kubeconfig_auth,
)


def write_kubeconfig(tmp_path, data):
    path = tmp_path / "kubeconfig.yaml"
    path.write_text(yaml.safe_dump(data))
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

    result = inspect_kubeconfig_auth(str(kubeconfig), "primary-hub")

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


def test_invalid_jwt_format_returns_warn(tmp_path):
    kubeconfig = write_kubeconfig(tmp_path, _kubeconfig_for_user({"token": "not.a-valid-jwt"}))

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


def test_client_certificate_auth_returns_pass(tmp_path):
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
