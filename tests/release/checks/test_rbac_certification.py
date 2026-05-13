"""Tests for live RBAC certification."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tests.release.checks.rbac_certification import (
    CertificationAssertion,
    CertificationResult,
    PermissionCheck,
    _certification_enabled,
    _get_required_permissions,
    certify_rbac_permissions,
)
from tests.release.contracts.models import HubProfile


def test_permission_check_as_sar_spec_cluster_scoped():
    """Cluster-scoped permission creates SAR spec without namespace."""
    check = PermissionCheck(
        api_group="cluster.open-cluster-management.io",
        resource="managedclusters",
        verb="get",
    )
    spec = check.as_sar_spec()
    assert spec["resourceAttributes"]["verb"] == "get"
    assert spec["resourceAttributes"]["resource"] == "managedclusters"
    assert spec["resourceAttributes"]["group"] == "cluster.open-cluster-management.io"
    assert "namespace" not in spec["resourceAttributes"]


def test_permission_check_as_sar_spec_namespace_scoped():
    """Namespace-scoped permission includes namespace in SAR spec."""
    check = PermissionCheck(
        api_group="", resource="pods", verb="list", namespace="open-cluster-management"
    )
    spec = check.as_sar_spec()
    assert spec["resourceAttributes"]["verb"] == "list"
    assert spec["resourceAttributes"]["resource"] == "pods"
    assert "group" not in spec["resourceAttributes"]
    assert spec["resourceAttributes"]["namespace"] == "open-cluster-management"


def test_get_required_permissions_operator_basic():
    """Operator role includes write permissions."""
    perms = _get_required_permissions(
        role="operator",
        include_decommission=False,
        include_old_hub_finalization=False,
    )
    # Should include managedclusters patch
    assert any(
        p.resource == "managedclusters"
        and p.verb == "patch"
        and p.api_group == "cluster.open-cluster-management.io"
        for p in perms
    )


def test_get_required_permissions_validator_readonly():
    """Validator role excludes write permissions."""
    perms = _get_required_permissions(
        role="validator",
        include_decommission=False,
        include_old_hub_finalization=False,
    )
    # Should NOT include managedclusters patch
    assert not any(
        p.resource == "managedclusters"
        and p.verb == "patch"
        and p.api_group == "cluster.open-cluster-management.io"
        for p in perms
    )


def test_get_required_permissions_mco_delete_with_old_hub_finalization():
    """Old hub finalization adds MCO delete permission."""
    perms = _get_required_permissions(
        role="operator",
        include_decommission=False,
        include_old_hub_finalization=True,
    )
    # Should include multiclusterobservabilities delete
    assert any(
        p.resource == "multiclusterobservabilities"
        and p.verb == "delete"
        and p.api_group == "observability.open-cluster-management.io"
        for p in perms
    )


def test_get_required_permissions_full_decommission():
    """Decommission adds delete permissions for ManagedCluster and MCH."""
    perms = _get_required_permissions(
        role="operator",
        include_decommission=True,
        include_old_hub_finalization=False,
    )
    # Should include managedclusters delete
    assert any(
        p.resource == "managedclusters"
        and p.verb == "delete"
        and p.api_group == "cluster.open-cluster-management.io"
        for p in perms
    )
    # Should include multiclusterhubs delete
    assert any(
        p.resource == "multiclusterhubs"
        and p.verb == "delete"
        and p.api_group == "operator.open-cluster-management.io"
        for p in perms
    )
    # Should include multiclusterobservabilities delete
    assert any(
        p.resource == "multiclusterobservabilities"
        and p.verb == "delete"
        and p.api_group == "observability.open-cluster-management.io"
        for p in perms
    )


def test_certification_enabled_false_by_default():
    """Certification is disabled when env var is not set."""
    with patch.dict(os.environ, {}, clear=True):
        assert _certification_enabled() is False


@pytest.mark.parametrize(
    "value",
    ["1", "true", "TRUE", "yes", "YES"],
)
def test_certification_enabled_truthy_values(value):
    """Certification is enabled with truthy env var values."""
    with patch.dict(os.environ, {"ACM_ENABLE_LIVE_RBAC_CERTIFICATION": value}):
        assert _certification_enabled() is True


@pytest.mark.parametrize(
    "value",
    ["0", "false", "no", ""],
)
def test_certification_enabled_falsy_values(value):
    """Certification is disabled with falsy env var values."""
    with patch.dict(os.environ, {"ACM_ENABLE_LIVE_RBAC_CERTIFICATION": value}):
        assert _certification_enabled() is False


def test_certify_rbac_permissions_skipped_when_disabled(tmp_path):
    """Certification returns skipped status when env var is not set."""
    with patch.dict(os.environ, {}, clear=True):
        hub = HubProfile(
            kubeconfig="/path/to/kubeconfig",
            context="test-context",
            acm_namespace="open-cluster-management",
        )
        result = certify_rbac_permissions(
            hub=hub,
            hub_name="test-hub",
            artifact_dir=tmp_path,
        )
        assert result.status == "skipped"
        assert result.reason == "ACM_ENABLE_LIVE_RBAC_CERTIFICATION is not set"
        assert len(result.assertions) == 0


def test_certify_rbac_permissions_invalid_role(tmp_path):
    """Certification fails with invalid role."""
    with patch.dict(
        os.environ, {"ACM_ENABLE_LIVE_RBAC_CERTIFICATION": "1"}, clear=True
    ):
        hub = HubProfile(
            kubeconfig="/path/to/kubeconfig",
            context="test-context",
            acm_namespace="open-cluster-management",
        )
        result = certify_rbac_permissions(
            hub=hub,
            hub_name="test-hub",
            artifact_dir=tmp_path,
            role="invalid-role",
        )
        assert result.status == "failed"
        assert "Invalid role" in result.reason or ""
        assert len(result.assertions) == 1
        assert result.assertions[0].status == "failed"


def test_certification_result_dataclass():
    """CertificationResult can be constructed with expected fields."""
    assertion = CertificationAssertion(
        capability="rbac-certification",
        name="test-permission",
        status="passed",
        expected="allowed",
        actual="allowed",
        evidence_path="/path/to/evidence",
        message="Test message",
    )
    result = CertificationResult(
        status="passed",
        assertions=[assertion],
        reason=None,
    )
    assert result.status == "passed"
    assert len(result.assertions) == 1
    assert result.reason is None


def test_get_required_permissions_includes_namespace_scoped():
    """Permission matrix includes namespace-scoped permissions."""
    perms = _get_required_permissions(
        role="operator",
        include_decommission=False,
        include_old_hub_finalization=False,
    )
    # Should include backup namespace permissions
    backup_perms = [p for p in perms if p.namespace == "open-cluster-management-backup"]
    assert len(backup_perms) > 0
    # Should include pods get in backup namespace
    assert any(
        p.resource == "pods" and p.verb == "get" and p.namespace == "open-cluster-management-backup"
        for p in perms
    )
