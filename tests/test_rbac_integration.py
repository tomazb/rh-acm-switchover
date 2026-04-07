"""
Integration tests for RBAC validation.

These tests verify that:
1. RBAC permissions in code match what's needed by scripts
2. RBAC manifests are consistent with code definitions
3. check_rbac.py argument parsing handles all context combinations
"""

from pathlib import Path
from typing import List

import pytest
import yaml

from lib.rbac_validator import RBACValidator


class TestRBACPermissionCoverage:
    """Test that RBAC validator covers all permissions needed by scripts."""

    @pytest.fixture
    def scripts_dir(self) -> Path:
        """Get the scripts directory path."""
        return Path(__file__).parent.parent / "scripts"

    @pytest.fixture
    def validator_permissions(self) -> dict:
        """Get all permissions defined in RBACValidator."""
        return {
            "cluster": RBACValidator.CLUSTER_PERMISSIONS,
            "namespace": RBACValidator.NAMESPACE_PERMISSIONS,  # Alias for hub permissions
            "hub_namespace": RBACValidator.OPERATOR_HUB_NAMESPACE_PERMISSIONS,
            "managed_cluster_namespace": RBACValidator.OPERATOR_MANAGED_CLUSTER_NAMESPACE_PERMISSIONS,
            "decommission": RBACValidator.DECOMMISSION_PERMISSIONS,
        }

    @pytest.mark.parametrize(
        "perm_source, namespace, resource, expected_api_group, expected_verbs",
        [
            ("namespace", "open-cluster-management-backup", "pods", None, ["get", "list"]),
            ("namespace", "open-cluster-management-backup", "backupstoragelocations", "velero.io", ["get", "list"]),
            ("namespace", "open-cluster-management-observability", "secrets", None, ["get"]),
            ("namespace", "open-cluster-management-observability", "routes", "route.openshift.io", ["get"]),
            ("namespace", "open-cluster-management", "pods", None, ["get", "list"]),
            ("namespace", "open-cluster-management-backup", "backupschedules", None, ["delete"]),
            ("managed_cluster_namespace", "open-cluster-management-agent", "secrets", None, ["create", "delete"]),
            ("managed_cluster_namespace", "open-cluster-management-agent", "deployments", "apps", ["patch"]),
        ],
        ids=[
            "backup-pods-for-velero-health",
            "backup-backupstoragelocations-for-storage-health",
            "observability-secrets-for-thanos-config",
            "observability-routes-for-grafana-access",
            "acm-pods-for-health-checks",
            "backup-backupschedules-delete-for-primary-prep",
            "agent-secrets-for-klusterlet-reconnection",
            "agent-deployments-for-klusterlet-restart",
        ],
    )
    def test_namespace_permission_exists(
        self, validator_permissions, perm_source, namespace, resource, expected_api_group, expected_verbs
    ):
        """Verify that a required namespaced permission is defined with the correct API group and verbs."""
        perms = validator_permissions[perm_source].get(namespace, [])
        matched = [p for p in perms if p[1] == resource]
        assert len(matched) == 1, f"Expected exactly one {resource} permission in {namespace}"
        if expected_api_group is not None:
            assert matched[0][0] == expected_api_group, f"Expected API group '{expected_api_group}' for {resource}"
        for verb in expected_verbs:
            assert verb in matched[0][2], f"Expected '{verb}' verb for {resource} in {namespace}"

    def test_all_expected_namespaces_covered(self, validator_permissions):
        """Test that all expected namespaces are covered in hub and managed cluster permissions."""
        # Hub namespaces (on ACM hub clusters)
        expected_hub_namespaces = {
            "open-cluster-management",
            "open-cluster-management-backup",
            "open-cluster-management-observability",
            "multicluster-engine",
        }
        actual_hub_namespaces = set(validator_permissions["hub_namespace"].keys())
        missing_hub = expected_hub_namespaces - actual_hub_namespaces
        assert not missing_hub, f"Missing namespaces in HUB_NAMESPACE_PERMISSIONS: {missing_hub}"

        # Managed cluster namespaces (on spoke clusters)
        expected_managed_namespaces = {
            "open-cluster-management-agent",
        }
        actual_managed_namespaces = set(validator_permissions["managed_cluster_namespace"].keys())
        missing_managed = expected_managed_namespaces - actual_managed_namespaces
        assert not missing_managed, f"Missing namespaces in MANAGED_CLUSTER_NAMESPACE_PERMISSIONS: {missing_managed}"

    @pytest.mark.parametrize(
        "resource, expected_api_group, expected_verbs",
        [
            ("managedclusters", None, ["get", "list", "patch"]),
            ("nodes", "", ["get", "list"]),
            ("clusteroperators", "config.openshift.io", ["get", "list"]),
            ("clusterversions", "config.openshift.io", ["get", "list"]),
        ],
        ids=[
            "managedclusters-core-functionality",
            "nodes-cluster-health-validation",
            "clusteroperators-openshift-health",
            "clusterversions-upgrade-status",
        ],
    )
    def test_cluster_permission_exists(self, validator_permissions, resource, expected_api_group, expected_verbs):
        """Verify that a required cluster-scoped permission is defined with the correct API group and verbs."""
        matched = [p for p in validator_permissions["cluster"] if p[1] == resource]
        assert len(matched) == 1, f"Expected exactly one {resource} cluster permission"
        if expected_api_group is not None:
            assert matched[0][0] == expected_api_group, f"Expected API group '{expected_api_group}' for {resource}"
        for verb in expected_verbs:
            assert verb in matched[0][2], f"Expected '{verb}' verb for {resource}"


class TestRBACManifestConsistency:
    """Test that RBAC manifests match code definitions."""

    @pytest.fixture
    def kustomize_role_path(self) -> Path:
        """Get the Kustomize role.yaml path."""
        return Path(__file__).parent.parent / "deploy" / "rbac" / "role.yaml"

    @pytest.fixture
    def helm_role_path(self) -> Path:
        """Get the Helm role.yaml path."""
        return Path(__file__).parent.parent / "deploy" / "helm" / "acm-switchover-rbac" / "templates" / "role.yaml"

    @pytest.fixture
    def helm_clusterrole_content(self) -> str:
        """Read Helm clusterrole template as text."""
        path = (
            Path(__file__).parent.parent / "deploy" / "helm" / "acm-switchover-rbac" / "templates" / "clusterrole.yaml"
        )
        if not path.exists():
            pytest.skip("Helm clusterrole.yaml not found")
        return path.read_text(encoding="utf-8")

    @pytest.fixture
    def decommission_clusterrole_path(self) -> Path:
        """Get the static decommission ClusterRole manifest path."""
        return Path(__file__).parent.parent / "deploy" / "rbac" / "extensions" / "decommission" / "clusterrole.yaml"

    @pytest.fixture
    def decommission_clusterrolebinding_path(self) -> Path:
        """Get the static decommission ClusterRoleBinding manifest path."""
        return (
            Path(__file__).parent.parent / "deploy" / "rbac" / "extensions" / "decommission" / "clusterrolebinding.yaml"
        )

    @pytest.fixture
    def kustomize_roles(self, kustomize_role_path) -> List[dict]:
        """Parse Kustomize role.yaml into list of role definitions."""
        if not kustomize_role_path.exists():
            pytest.skip("Kustomize role.yaml not found")

        with open(kustomize_role_path) as f:
            content = f.read()

        # Split on YAML document separator and parse each
        docs = content.split("---")
        roles = []
        for doc in docs:
            doc = doc.strip()
            if not doc:
                continue
            try:
                parsed = yaml.safe_load(doc)
                if parsed and parsed.get("kind") == "Role":
                    roles.append(parsed)
            except yaml.YAMLError:
                continue
        return roles

    def test_kustomize_role_yaml_parseable(self, kustomize_role_path):
        """Test that Kustomize role.yaml is valid YAML."""
        if not kustomize_role_path.exists():
            pytest.skip("Kustomize role.yaml not found")

        with open(kustomize_role_path) as f:
            content = f.read()

        # Should not raise
        docs = list(yaml.safe_load_all(content))
        assert len(docs) > 0, "Expected at least one YAML document"

    def test_kustomize_roles_cover_expected_namespaces(self, kustomize_roles):
        """Test that Kustomize roles cover all expected namespaces."""
        expected_namespaces = {
            "open-cluster-management",
            "open-cluster-management-backup",
            "open-cluster-management-observability",
            "multicluster-engine",
        }

        # Get namespaces from operator roles
        operator_namespaces = {
            r["metadata"]["namespace"] for r in kustomize_roles if r["metadata"]["name"] == "acm-switchover-operator"
        }

        missing = expected_namespaces - operator_namespaces
        assert not missing, f"Missing operator roles for namespaces: {missing}"

    def test_kustomize_backup_role_has_pods(self, kustomize_roles):
        """Test that Kustomize backup namespace role includes pods permission."""
        backup_operator_role = next(
            (
                r
                for r in kustomize_roles
                if r["metadata"]["namespace"] == "open-cluster-management-backup"
                and r["metadata"]["name"] == "acm-switchover-operator"
            ),
            None,
        )

        assert backup_operator_role is not None, "Expected backup operator role"

        pods_rule = next(
            (rule for rule in backup_operator_role["rules"] if "pods" in rule.get("resources", [])),
            None,
        )

        assert pods_rule is not None, "Expected pods rule in backup operator role"
        assert "get" in pods_rule["verbs"], "Expected 'get' verb for pods"
        assert "list" in pods_rule["verbs"], "Expected 'list' verb for pods"

    def test_kustomize_observability_role_has_routes(self, kustomize_roles):
        """Test that Kustomize observability role includes routes permission."""
        obs_operator_role = next(
            (
                r
                for r in kustomize_roles
                if r["metadata"]["namespace"] == "open-cluster-management-observability"
                and r["metadata"]["name"] == "acm-switchover-operator"
            ),
            None,
        )

        assert obs_operator_role is not None, "Expected observability operator role"

        routes_rule = next(
            (rule for rule in obs_operator_role["rules"] if "routes" in rule.get("resources", [])),
            None,
        )

        assert routes_rule is not None, "Expected routes rule in observability operator role"
        # Check that route.openshift.io is in the apiGroups list
        api_groups = routes_rule.get("apiGroups", [])
        assert any(group == "route.openshift.io" for group in api_groups), "Expected route.openshift.io API group"

    def test_kustomize_observability_role_has_secrets(self, kustomize_roles):
        """Test that Kustomize observability role includes secrets permission."""
        obs_operator_role = next(
            (
                r
                for r in kustomize_roles
                if r["metadata"]["namespace"] == "open-cluster-management-observability"
                and r["metadata"]["name"] == "acm-switchover-operator"
            ),
            None,
        )

        assert obs_operator_role is not None, "Expected observability operator role"

        secrets_rule = next(
            (rule for rule in obs_operator_role["rules"] if "secrets" in rule.get("resources", [])),
            None,
        )

        assert secrets_rule is not None, "Expected secrets rule in observability operator role"

    ARGOCD_SNIPPETS = [
        '  - apiGroups: ["argoproj.io"]\n    resources: ["applications"]\n    verbs: ["get", "list", "patch"]',
        '  - apiGroups: ["argoproj.io"]\n    resources: ["applications"]\n    verbs: ["get", "list"]',
        '  - apiGroups: ["argoproj.io"]\n    resources: ["argocds"]\n    verbs: ["get", "list"]',
        '  - apiGroups: ["apiextensions.k8s.io"]\n    resources: ["customresourcedefinitions"]\n    verbs: ["get"]',
    ]

    DECOMMISSION_FORBIDDEN_SNIPPETS = [
        '  - apiGroups: ["cluster.open-cluster-management.io"]\n    resources: ["managedclusters"]\n    verbs: ["get", "list", "patch", "delete"]',
        '  - apiGroups: ["operator.open-cluster-management.io"]\n    resources: ["multiclusterhubs"]\n    verbs: ["get", "list", "delete"]',
        '  - apiGroups: ["observability.open-cluster-management.io"]\n    resources: ["multiclusterobservabilities"]\n    verbs: ["get", "list", "delete"]',
    ]

    CLUSTERROLE_PATHS = {
        "kustomize": Path(__file__).parent.parent / "deploy" / "rbac" / "clusterrole.yaml",
        "helm": Path(__file__).parent.parent
        / "deploy"
        / "helm"
        / "acm-switchover-rbac"
        / "templates"
        / "clusterrole.yaml",
    }

    def _read_clusterrole(self, variant: str) -> str:
        path = self.CLUSTERROLE_PATHS[variant]
        if not path.exists():
            pytest.skip(f"{variant} clusterrole.yaml not found")
        return path.read_text(encoding="utf-8")

    @pytest.mark.parametrize("variant", ["kustomize", "helm"])
    def test_clusterrole_has_argocd_rules(self, variant):
        """Test that clusterrole includes Argo CD read/manage permissions."""
        content = self._read_clusterrole(variant)
        for snippet in self.ARGOCD_SNIPPETS:
            assert snippet in content, f"Missing Argo CD snippet in {variant} clusterrole: {snippet}"

    @pytest.mark.parametrize("variant", ["kustomize", "helm"])
    def test_operator_clusterrole_omits_decommission_delete_verbs(self, variant):
        """Test that baseline operator ClusterRole excludes cluster-wide delete verbs."""
        content = self._read_clusterrole(variant)
        for snippet in self.DECOMMISSION_FORBIDDEN_SNIPPETS:
            assert snippet not in content, f"Forbidden decommission snippet found in {variant} clusterrole"

    def test_static_decommission_clusterrole_exists_with_delete_verbs(self, decommission_clusterrole_path):
        """Test that delete verbs live in a dedicated static decommission ClusterRole."""
        assert decommission_clusterrole_path.exists(), "Expected static decommission ClusterRole manifest"
        content = decommission_clusterrole_path.read_text(encoding="utf-8")
        required_snippets = [
            "name: acm-switchover-decommission",
            'resources: ["managedclusters"]\n    verbs: ["delete"]',
            'resources: ["multiclusterhubs"]\n    verbs: ["delete"]',
            'resources: ["multiclusterobservabilities"]\n    verbs: ["delete"]',
        ]
        for snippet in required_snippets:
            assert snippet in content

    def test_static_decommission_clusterrolebinding_exists(self, decommission_clusterrolebinding_path):
        """Test that static decommission binding exists for opt-in operator escalation."""
        assert decommission_clusterrolebinding_path.exists(), "Expected static decommission ClusterRoleBinding manifest"
        content = decommission_clusterrolebinding_path.read_text(encoding="utf-8")
        assert "name: acm-switchover-decommission" in content
        assert "kind: ClusterRoleBinding" in content

    def test_helm_clusterrole_supports_optional_decommission_role(self, helm_clusterrole_content):
        """Test that Helm templates expose an opt-in decommission ClusterRole."""
        required_snippets = [
            ".Values.rbac.includeDecommissionClusterRole",
            ".Values.clusterRole.decommission.name",
            'resources: ["managedclusters"]',
            'verbs: ["delete"]',
        ]
        for snippet in required_snippets:
            assert snippet in helm_clusterrole_content


class TestRBACValidatorPermissionStructure:
    """Test the structure and format of RBAC permissions."""

    def test_cluster_permissions_format(self):
        """Test that cluster permissions have correct tuple format."""
        for perm in RBACValidator.CLUSTER_PERMISSIONS:
            assert isinstance(perm, tuple), f"Expected tuple, got {type(perm)}"
            assert len(perm) == 3, f"Expected 3 elements, got {len(perm)}"
            api_group, resource, verbs = perm
            assert isinstance(api_group, str), f"API group should be string: {api_group}"
            assert isinstance(resource, str), f"Resource should be string: {resource}"
            assert isinstance(verbs, list), f"Verbs should be list: {verbs}"
            for verb in verbs:
                assert isinstance(verb, str), f"Verb should be string: {verb}"

    def test_namespace_permissions_format(self):
        """Test that namespace permissions have correct dict/tuple format."""
        assert isinstance(RBACValidator.NAMESPACE_PERMISSIONS, dict)

        for namespace, perms in RBACValidator.NAMESPACE_PERMISSIONS.items():
            assert isinstance(namespace, str), f"Namespace should be string: {namespace}"
            assert isinstance(perms, list), f"Permissions should be list: {perms}"

            for perm in perms:
                assert isinstance(perm, tuple), f"Expected tuple, got {type(perm)}"
                assert len(perm) == 3, f"Expected 3 elements, got {len(perm)}"
                api_group, resource, verbs = perm
                assert isinstance(api_group, str), f"API group should be string: {api_group}"
                assert isinstance(resource, str), f"Resource should be string: {resource}"
                assert isinstance(verbs, list), f"Verbs should be list: {verbs}"

    def test_decommission_permissions_format(self):
        """Test that decommission permissions have correct tuple format."""
        for perm in RBACValidator.DECOMMISSION_PERMISSIONS:
            assert isinstance(perm, tuple), f"Expected tuple, got {type(perm)}"
            assert len(perm) == 3, f"Expected 3 elements, got {len(perm)}"
            api_group, resource, verbs = perm
            assert isinstance(api_group, str), f"API group should be string: {api_group}"
            assert isinstance(resource, str), f"Resource should be string: {resource}"
            assert isinstance(verbs, list), f"Verbs should be list: {verbs}"
            # Decommission should include 'delete' verb
            assert "delete" in verbs, f"Expected 'delete' in decommission verbs: {verbs}"

    def test_no_duplicate_permissions(self):
        """Test that there are no duplicate permission definitions."""
        seen = set()
        for perm in RBACValidator.CLUSTER_PERMISSIONS:
            key = (perm[0], perm[1])
            assert key not in seen, f"Duplicate cluster permission: {key}"
            seen.add(key)

        for namespace, perms in RBACValidator.NAMESPACE_PERMISSIONS.items():
            seen_ns = set()
            for perm in perms:
                key = (perm[0], perm[1])
                assert key not in seen_ns, f"Duplicate namespace permission in {namespace}: {key}"
                seen_ns.add(key)


class TestRBACValidatorRoleAware:
    """Test role-aware RBAC validation functionality."""

    def test_valid_roles_defined(self):
        """Test that valid roles are defined."""
        from lib.rbac_validator import VALID_ROLES

        assert VALID_ROLES == ("operator", "validator")

    def test_operator_role_has_more_permissions_than_validator(self):
        """Test that operator role has more permissions than validator."""
        # Cluster permissions - operator should have patch on managedclusters
        operator_mc = next(
            (p for p in RBACValidator.OPERATOR_CLUSTER_PERMISSIONS if p[1] == "managedclusters"),
            None,
        )
        validator_mc = next(
            (p for p in RBACValidator.VALIDATOR_CLUSTER_PERMISSIONS if p[1] == "managedclusters"),
            None,
        )

        assert operator_mc is not None
        assert validator_mc is not None
        assert "patch" in operator_mc[2], "Operator should have patch on managedclusters"
        assert "patch" not in validator_mc[2], "Validator should NOT have patch on managedclusters"

    def test_validator_namespace_permissions_are_read_only(self):
        """Test that validator namespace permissions are read-only."""
        write_verbs = {"create", "patch", "delete", "update"}

        for (
            namespace,
            perms,
        ) in RBACValidator.VALIDATOR_HUB_NAMESPACE_PERMISSIONS.items():
            for api_group, resource, verbs in perms:
                has_write = any(v in write_verbs for v in verbs)
                assert not has_write, (
                    f"Validator should not have write permissions in {namespace}: " f"{resource} has {verbs}"
                )

    def test_operator_hub_permissions_include_write_verbs(self):
        """Test that operator hub permissions include write verbs where needed."""
        backup_perms = RBACValidator.OPERATOR_HUB_NAMESPACE_PERMISSIONS.get("open-cluster-management-backup", [])
        configmaps_perm = next((p for p in backup_perms if p[1] == "configmaps"), None)

        assert configmaps_perm is not None
        assert "create" in configmaps_perm[2], "Operator should have create on configmaps"
        assert "patch" in configmaps_perm[2], "Operator should have patch on configmaps"
        assert "delete" in configmaps_perm[2], "Operator should have delete on configmaps"

    def test_managed_cluster_permissions_exist_for_both_roles(self):
        """Test that managed cluster permissions are defined for both roles."""
        assert "open-cluster-management-agent" in RBACValidator.OPERATOR_MANAGED_CLUSTER_NAMESPACE_PERMISSIONS
        assert "open-cluster-management-agent" in RBACValidator.VALIDATOR_MANAGED_CLUSTER_NAMESPACE_PERMISSIONS

    def test_validator_backup_namespace_has_secrets_get(self):
        """Test that validator backup namespace includes secrets get permission."""
        backup_perms = RBACValidator.VALIDATOR_HUB_NAMESPACE_PERMISSIONS.get("open-cluster-management-backup", [])
        secrets_perm = next((p for p in backup_perms if p[1] == "secrets"), None)

        assert secrets_perm is not None, "Validator should have secrets permission in backup namespace"
        assert "get" in secrets_perm[2], "Validator should have 'get' verb for secrets"
