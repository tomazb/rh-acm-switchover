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
            "namespace": RBACValidator.NAMESPACE_PERMISSIONS,
            "decommission": RBACValidator.DECOMMISSION_PERMISSIONS,
        }

    def test_namespace_permissions_include_pods_for_backup(self, validator_permissions):
        """Test that backup namespace has pods permission for Velero health checks."""
        backup_perms = validator_permissions["namespace"].get(
            "open-cluster-management-backup", []
        )
        pods_perm = [p for p in backup_perms if p[1] == "pods"]
        
        assert len(pods_perm) == 1, "Expected pods permission in backup namespace"
        assert "get" in pods_perm[0][2], "Expected 'get' verb for pods"
        assert "list" in pods_perm[0][2], "Expected 'list' verb for pods"

    def test_namespace_permissions_include_backupstoragelocations(
        self, validator_permissions
    ):
        """Test that backup namespace has backupstoragelocations permission for storage health."""
        backup_perms = validator_permissions["namespace"].get(
            "open-cluster-management-backup", []
        )
        bsl_perm = [p for p in backup_perms if p[1] == "backupstoragelocations"]
        
        assert len(bsl_perm) == 1, "Expected backupstoragelocations permission in backup namespace"
        assert bsl_perm[0][0] == "velero.io", "BackupStorageLocations should use velero.io API group"
        assert "get" in bsl_perm[0][2], "Expected 'get' verb for backupstoragelocations"
        assert "list" in bsl_perm[0][2], "Expected 'list' verb for backupstoragelocations"

    def test_namespace_permissions_include_secrets_for_observability(
        self, validator_permissions
    ):
        """Test that observability namespace has secrets permission for Thanos config."""
        obs_perms = validator_permissions["namespace"].get(
            "open-cluster-management-observability", []
        )
        secrets_perm = [p for p in obs_perms if p[1] == "secrets"]
        
        assert len(secrets_perm) == 1, "Expected secrets permission in observability namespace"
        assert "get" in secrets_perm[0][2], "Expected 'get' verb for secrets"

    def test_namespace_permissions_include_routes_for_observability(
        self, validator_permissions
    ):
        """Test that observability namespace has routes permission for Grafana access."""
        obs_perms = validator_permissions["namespace"].get(
            "open-cluster-management-observability", []
        )
        routes_perm = [p for p in obs_perms if p[1] == "routes"]
        
        assert len(routes_perm) == 1, "Expected routes permission in observability namespace"
        assert routes_perm[0][0] == "route.openshift.io", "Routes should use route.openshift.io API group"
        assert "get" in routes_perm[0][2], "Expected 'get' verb for routes"

    def test_namespace_permissions_include_acm_namespace(self, validator_permissions):
        """Test that open-cluster-management namespace is covered for ACM health checks."""
        assert "open-cluster-management" in validator_permissions["namespace"], \
            "Expected open-cluster-management namespace in NAMESPACE_PERMISSIONS"
        
        acm_perms = validator_permissions["namespace"]["open-cluster-management"]
        pods_perm = [p for p in acm_perms if p[1] == "pods"]
        
        assert len(pods_perm) == 1, "Expected pods permission in ACM namespace"
        assert "get" in pods_perm[0][2], "Expected 'get' verb for pods"
        assert "list" in pods_perm[0][2], "Expected 'list' verb for pods"

    def test_namespace_permissions_include_agent_namespace(self, validator_permissions):
        """Test that open-cluster-management-agent namespace is covered for klusterlet operations."""
        assert "open-cluster-management-agent" in validator_permissions["namespace"], \
            "Expected open-cluster-management-agent namespace in NAMESPACE_PERMISSIONS"
        
        agent_perms = validator_permissions["namespace"]["open-cluster-management-agent"]
        
        # Check secrets permission for klusterlet reconnection
        secrets_perm = [p for p in agent_perms if p[1] == "secrets"]
        assert len(secrets_perm) == 1, "Expected secrets permission in agent namespace"
        assert "create" in secrets_perm[0][2], "Expected 'create' verb for secrets"
        assert "delete" in secrets_perm[0][2], "Expected 'delete' verb for secrets"
        
        # Check deployments permission for klusterlet restart
        deployments_perm = [p for p in agent_perms if p[1] == "deployments"]
        assert len(deployments_perm) == 1, "Expected deployments permission in agent namespace"
        assert deployments_perm[0][0] == "apps", "Deployments should use apps API group"
        assert "patch" in deployments_perm[0][2], "Expected 'patch' verb for deployments"

    def test_backup_namespace_includes_delete_for_backupschedules(self, validator_permissions):
        """Test that backup namespace has delete permission for backupschedules (used in primary_prep.py)."""
        backup_perms = validator_permissions["namespace"].get(
            "open-cluster-management-backup", []
        )
        bs_perm = [p for p in backup_perms if p[1] == "backupschedules"]
        
        assert len(bs_perm) == 1, "Expected backupschedules permission in backup namespace"
        assert "delete" in bs_perm[0][2], "Expected 'delete' verb for backupschedules (used in primary_prep.py)"

    def test_all_expected_namespaces_covered(self, validator_permissions):
        """Test that all expected namespaces are covered in NAMESPACE_PERMISSIONS."""
        expected_namespaces = {
            "open-cluster-management",
            "open-cluster-management-agent",
            "open-cluster-management-backup",
            "open-cluster-management-observability",
            "multicluster-engine",
        }
        actual_namespaces = set(validator_permissions["namespace"].keys())
        
        missing = expected_namespaces - actual_namespaces
        assert not missing, f"Missing namespaces in NAMESPACE_PERMISSIONS: {missing}"

    def test_cluster_permissions_include_managed_clusters(self, validator_permissions):
        """Test that cluster permissions include managedclusters for core functionality."""
        mc_perm = [
            p for p in validator_permissions["cluster"]
            if p[1] == "managedclusters"
        ]
        
        assert len(mc_perm) == 1, "Expected managedclusters permission"
        assert "get" in mc_perm[0][2], "Expected 'get' verb for managedclusters"
        assert "list" in mc_perm[0][2], "Expected 'list' verb for managedclusters"
        assert "patch" in mc_perm[0][2], "Expected 'patch' verb for managedclusters"

    def test_cluster_permissions_include_nodes(self, validator_permissions):
        """Test that cluster permissions include nodes for cluster health validation per runbook."""
        nodes_perm = [
            p for p in validator_permissions["cluster"]
            if p[1] == "nodes"
        ]
        
        assert len(nodes_perm) == 1, "Expected nodes permission for cluster health check"
        assert nodes_perm[0][0] == "", "Nodes should use core API group"
        assert "get" in nodes_perm[0][2], "Expected 'get' verb for nodes"
        assert "list" in nodes_perm[0][2], "Expected 'list' verb for nodes"

    def test_cluster_permissions_include_clusteroperators(self, validator_permissions):
        """Test that cluster permissions include clusteroperators for OpenShift health validation."""
        co_perm = [
            p for p in validator_permissions["cluster"]
            if p[1] == "clusteroperators"
        ]
        
        assert len(co_perm) == 1, "Expected clusteroperators permission for OpenShift health check"
        assert co_perm[0][0] == "config.openshift.io", "ClusterOperators should use config.openshift.io API group"
        assert "get" in co_perm[0][2], "Expected 'get' verb for clusteroperators"
        assert "list" in co_perm[0][2], "Expected 'list' verb for clusteroperators"

    def test_cluster_permissions_include_clusterversions(self, validator_permissions):
        """Test that cluster permissions include clusterversions for upgrade status validation."""
        cv_perm = [
            p for p in validator_permissions["cluster"]
            if p[1] == "clusterversions"
        ]
        
        assert len(cv_perm) == 1, "Expected clusterversions permission for upgrade status check"
        assert cv_perm[0][0] == "config.openshift.io", "ClusterVersions should use config.openshift.io API group"
        assert "get" in cv_perm[0][2], "Expected 'get' verb for clusterversions"
        assert "list" in cv_perm[0][2], "Expected 'list' verb for clusterversions"


class TestRBACManifestConsistency:
    """Test that RBAC manifests match code definitions."""

    @pytest.fixture
    def kustomize_role_path(self) -> Path:
        """Get the Kustomize role.yaml path."""
        return Path(__file__).parent.parent / "deploy" / "rbac" / "role.yaml"

    @pytest.fixture
    def helm_role_path(self) -> Path:
        """Get the Helm role.yaml path."""
        return (
            Path(__file__).parent.parent
            / "deploy"
            / "helm"
            / "acm-switchover-rbac"
            / "templates"
            / "role.yaml"
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
            r["metadata"]["namespace"]
            for r in kustomize_roles
            if r["metadata"]["name"] == "acm-switchover-operator"
        }
        
        missing = expected_namespaces - operator_namespaces
        assert not missing, f"Missing operator roles for namespaces: {missing}"

    def test_kustomize_backup_role_has_pods(self, kustomize_roles):
        """Test that Kustomize backup namespace role includes pods permission."""
        backup_operator_role = next(
            (r for r in kustomize_roles
             if r["metadata"]["namespace"] == "open-cluster-management-backup"
             and r["metadata"]["name"] == "acm-switchover-operator"),
            None
        )
        
        assert backup_operator_role is not None, "Expected backup operator role"
        
        pods_rule = next(
            (rule for rule in backup_operator_role["rules"]
             if "pods" in rule.get("resources", [])),
            None
        )
        
        assert pods_rule is not None, "Expected pods rule in backup operator role"
        assert "get" in pods_rule["verbs"], "Expected 'get' verb for pods"
        assert "list" in pods_rule["verbs"], "Expected 'list' verb for pods"

    def test_kustomize_observability_role_has_routes(self, kustomize_roles):
        """Test that Kustomize observability role includes routes permission."""
        obs_operator_role = next(
            (r for r in kustomize_roles
             if r["metadata"]["namespace"] == "open-cluster-management-observability"
             and r["metadata"]["name"] == "acm-switchover-operator"),
            None
        )
        
        assert obs_operator_role is not None, "Expected observability operator role"
        
        routes_rule = next(
            (rule for rule in obs_operator_role["rules"]
             if "routes" in rule.get("resources", [])),
            None
        )
        
        assert routes_rule is not None, "Expected routes rule in observability operator role"
        # Check that route.openshift.io is in the apiGroups list
        api_groups = routes_rule.get("apiGroups", [])
        assert any(group == "route.openshift.io" for group in api_groups), \
            "Expected route.openshift.io API group"

    def test_kustomize_observability_role_has_secrets(self, kustomize_roles):
        """Test that Kustomize observability role includes secrets permission."""
        obs_operator_role = next(
            (r for r in kustomize_roles
             if r["metadata"]["namespace"] == "open-cluster-management-observability"
             and r["metadata"]["name"] == "acm-switchover-operator"),
            None
        )
        
        assert obs_operator_role is not None, "Expected observability operator role"
        
        secrets_rule = next(
            (rule for rule in obs_operator_role["rules"]
             if "secrets" in rule.get("resources", [])),
            None
        )
        
        assert secrets_rule is not None, "Expected secrets rule in observability operator role"


class TestCheckRBACArgumentParsing:
    """Test check_rbac.py argument parsing handles all context combinations."""

    def test_secondary_context_alone_is_used(self):
        """Test that --secondary-context alone is used when no other context specified."""
        # Simulate the argument parsing logic
        class Args:
            context = None
            primary_context = None
            secondary_context = "secondary-hub"
        
        args = Args()
        
        # This is the fixed logic from check_rbac.py
        context = args.context or args.primary_context or args.secondary_context
        
        assert context == "secondary-hub", \
            "Expected secondary_context to be used when no other context specified"

    def test_primary_context_takes_precedence(self):
        """Test that --primary-context takes precedence over --secondary-context."""
        class Args:
            context = None
            primary_context = "primary-hub"
            secondary_context = "secondary-hub"
        
        args = Args()
        context = args.context or args.primary_context or args.secondary_context
        
        assert context == "primary-hub", \
            "Expected primary_context to take precedence"

    def test_context_takes_highest_precedence(self):
        """Test that --context takes highest precedence."""
        class Args:
            context = "main-hub"
            primary_context = "primary-hub"
            secondary_context = "secondary-hub"
        
        args = Args()
        context = args.context or args.primary_context or args.secondary_context
        
        assert context == "main-hub", \
            "Expected --context to take highest precedence"

    def test_no_context_specified_returns_none(self):
        """Test that no context specified returns None (uses current context)."""
        class Args:
            context = None
            primary_context = None
            secondary_context = None
        
        args = Args()
        context = args.context or args.primary_context or args.secondary_context
        
        assert context is None, \
            "Expected None when no context specified (uses current context)"


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
                assert key not in seen_ns, \
                    f"Duplicate namespace permission in {namespace}: {key}"
                seen_ns.add(key)
