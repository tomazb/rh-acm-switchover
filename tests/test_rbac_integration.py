"""
Integration tests for RBAC validation.

These tests verify that:
1. RBAC permissions in code match what's needed by scripts
2. RBAC manifests are consistent with code definitions
3. check_rbac.py argument parsing handles all context combinations
"""

import shutil
import subprocess
from pathlib import Path
from typing import List

import pytest
import yaml

from lib.constants import (
    ACM_NAMESPACE,
    BACKUP_NAMESPACE,
    MANAGED_CLUSTER_AGENT_NAMESPACE,
    MCE_NAMESPACE,
    OBSERVABILITY_NAMESPACE,
)
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
            (
                "namespace",
                "open-cluster-management-backup",
                "pods",
                None,
                ["get", "list"],
            ),
            (
                "namespace",
                "open-cluster-management-backup",
                "backupstoragelocations",
                "velero.io",
                ["get", "list"],
            ),
            (
                "namespace",
                "open-cluster-management-observability",
                "secrets",
                None,
                ["get"],
            ),
            (
                "namespace",
                "open-cluster-management-observability",
                "routes",
                "route.openshift.io",
                ["get"],
            ),
            ("namespace", "open-cluster-management", "pods", None, ["get", "list"]),
            (
                "namespace",
                "open-cluster-management-backup",
                "backupschedules",
                None,
                ["delete"],
            ),
            (
                "managed_cluster_namespace",
                "open-cluster-management-agent",
                "secrets",
                None,
                ["create", "patch"],
            ),
            (
                "managed_cluster_namespace",
                "open-cluster-management-agent",
                "deployments",
                "apps",
                ["patch"],
            ),
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
        self,
        validator_permissions,
        perm_source,
        namespace,
        resource,
        expected_api_group,
        expected_verbs,
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
            ("namespaces", "", ["get", "list"]),
            ("managedclusters", None, ["get", "list", "patch"]),
            ("nodes", "", ["get", "list"]),
            ("clusteroperators", "config.openshift.io", ["get", "list"]),
            ("clusterversions", "config.openshift.io", ["get", "list"]),
        ],
        ids=[
            "namespaces-for-preflight-discovery",
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
    def kustomize_rbac_dir(self) -> Path:
        """Get the baseline Kustomize RBAC directory."""
        return Path(__file__).parent.parent / "deploy" / "rbac"

    @pytest.fixture
    def acm_policy_path(self) -> Path:
        """Get the ACM Policy RBAC manifest path."""
        return Path(__file__).parent.parent / "deploy" / "acm-policies" / "policy-rbac.yaml"

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
    def helm_helpers_content(self) -> str:
        """Read Helm helpers template as text."""
        path = Path(__file__).parent.parent / "deploy" / "helm" / "acm-switchover-rbac" / "templates" / "_helpers.tpl"
        if not path.exists():
            pytest.skip("Helm _helpers.tpl not found")
        return path.read_text(encoding="utf-8")

    @pytest.fixture
    def helm_chart_dir(self) -> Path:
        """Get the Helm chart directory."""
        path = Path(__file__).parent.parent / "deploy" / "helm" / "acm-switchover-rbac"
        if not path.exists():
            pytest.skip("Helm chart not found")
        return path

    @pytest.fixture
    def helm_binary(self) -> str:
        """Get Helm binary path or skip render tests."""
        helm = shutil.which("helm")
        if not helm:
            pytest.skip("helm binary not available")
        return helm

    @pytest.fixture
    def helm_namespace_content(self) -> str:
        """Read Helm namespace template as text."""
        path = Path(__file__).parent.parent / "deploy" / "helm" / "acm-switchover-rbac" / "templates" / "namespace.yaml"
        if not path.exists():
            pytest.skip("Helm namespace.yaml not found")
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

    def test_acm_policy_embeds_baseline_rbac_manifests(self, kustomize_rbac_dir, acm_policy_path):
        """ACM Policy governance manifest must stay aligned with baseline RBAC."""
        baseline_objects = []
        for name in (
            "namespace.yaml",
            "serviceaccount.yaml",
            "clusterrole.yaml",
            "clusterrolebinding.yaml",
            "role.yaml",
            "rolebinding.yaml",
        ):
            baseline_objects.extend(
                doc for doc in yaml.safe_load_all((kustomize_rbac_dir / name).read_text(encoding="utf-8")) if doc
            )

        policy_docs = list(yaml.safe_load_all(acm_policy_path.read_text(encoding="utf-8")))
        policy = next(doc for doc in policy_docs if doc.get("kind") == "Policy")
        policy_objects = []
        for template in policy["spec"]["policy-templates"]:
            config_policy = template["objectDefinition"]
            for object_template in config_policy["spec"]["object-templates"]:
                policy_objects.append(object_template["objectDefinition"])

        def key(obj):
            metadata = obj["metadata"]
            return (
                obj["kind"],
                metadata.get("namespace", ""),
                metadata["name"],
            )

        def assert_unique_keys(label, objects):
            keys = [key(obj) for obj in objects]
            duplicates = sorted(item for item in set(keys) if keys.count(item) > 1)
            assert not duplicates, f"duplicate {label} RBAC key: {duplicates[0]}"

        assert_unique_keys("policy", policy_objects)
        assert_unique_keys("baseline", baseline_objects)
        assert {key(obj): obj for obj in policy_objects} == {key(obj): obj for obj in baseline_objects}

    def test_acm_policy_has_cross_namespace_selector(self, acm_policy_path):
        """ACM Policy must evaluate the namespaces that contain embedded baseline RBAC objects."""
        policy_docs = list(yaml.safe_load_all(acm_policy_path.read_text(encoding="utf-8")))
        policy = next(doc for doc in policy_docs if doc.get("kind") == "Policy")
        config_policy = policy["spec"]["policy-templates"][0]["objectDefinition"]

        assert config_policy["spec"]["namespaceSelector"] == {
            "exclude": ["kube-*"],
            "include": ["*"],
        }

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

    def test_kustomize_acm_role_has_namespaced_multiclusterhub_discovery_rule(self, kustomize_roles):
        """Baseline ACM namespace Role must keep MCH access non-destructive."""
        acm_operator_role = next(
            (
                r
                for r in kustomize_roles
                if r["metadata"]["namespace"] == "open-cluster-management"
                and r["metadata"]["name"] == "acm-switchover-operator"
            ),
            None,
        )

        assert acm_operator_role is not None, "Expected ACM namespace operator role"
        mch_rule = next(
            (
                rule
                for rule in acm_operator_role["rules"]
                if rule.get("apiGroups") == ["operator.open-cluster-management.io"]
                and "multiclusterhubs" in rule.get("resources", [])
            ),
            None,
        )

        assert mch_rule is not None, "Expected namespaced MultiClusterHub rule in ACM operator role"
        assert mch_rule["verbs"] == ["list"]

    def test_helm_acm_role_has_namespaced_multiclusterhub_discovery_rule(self, helm_role_path):
        """Helm Role template must keep the same non-destructive namespaced MCH rule."""
        content = helm_role_path.read_text(encoding="utf-8")
        snippet = (
            '  - apiGroups: ["operator.open-cluster-management.io"]\n'
            '    resources: ["multiclusterhubs"]\n'
            '    verbs: ["list"]'
        )

        assert snippet in content

    ARGOCD_SNIPPETS = [
        '  - apiGroups: ["argoproj.io"]\n    resources: ["applications"]\n    verbs: ["get", "list", "patch"]',
        '  - apiGroups: ["argoproj.io"]\n    resources: ["applications"]\n    verbs: ["get", "list"]',
        '  - apiGroups: ["argoproj.io"]\n    resources: ["argocds"]\n    verbs: ["get", "list"]',
        '  - apiGroups: ["apiextensions.k8s.io"]\n    resources: ["customresourcedefinitions"]\n    verbs: ["get"]',
    ]

    DECOMMISSION_FORBIDDEN_SNIPPETS = [
        '  - apiGroups: ["cluster.open-cluster-management.io"]\n    resources: ["managedclusters"]\n    verbs: ["get", "list", "patch", "delete"]',
        '  - apiGroups: ["operator.open-cluster-management.io"]\n    resources: ["multiclusterhubs"]\n    verbs: ["get", "list", "delete"]',
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
    def test_clusterrole_namespace_discovery_rule_allows_list(self, variant):
        """ClusterRole manifests must allow listing namespaces for preflight discovery."""
        content = self._read_clusterrole(variant)
        snippet = 'resources: ["namespaces"]\n    verbs: ["get", "list"]'

        assert snippet in content, f"Missing namespace list permission in {variant} clusterrole"

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
            'resources: ["managedclusters"]\n    verbs: ["get", "delete"]',
            'resources: ["multiclusterhubs"]\n    verbs: ["get", "delete"]',
            'resources: ["multiclusterobservabilities"]\n    verbs: ["get", "delete"]',
            'resources: ["clusterdeployments"]\n    verbs: ["list"]',
        ]
        for snippet in required_snippets:
            assert snippet in content
        assert 'resources: ["clusterdeployments"]\n    verbs: ["get", "list"]' not in content
        # Extension isolation: ManagedCluster named-GET is required; list/patch/* must not appear.
        mc_rule = content.split('resources: ["managedclusters"]', 1)[1].split("- apiGroups:", 1)[0]
        assert 'verbs: ["get", "delete"]' in mc_rule
        assert "list" not in mc_rule
        assert "patch" not in mc_rule
        assert '"*"' not in mc_rule

    def test_static_decommission_clusterrolebinding_exists(self, decommission_clusterrolebinding_path):
        """Test that static decommission binding exists for opt-in operator escalation."""
        assert decommission_clusterrolebinding_path.exists(), "Expected static decommission ClusterRoleBinding manifest"
        content = decommission_clusterrolebinding_path.read_text(encoding="utf-8")
        assert "name: acm-switchover-decommission" in content
        assert "kind: ClusterRoleBinding" in content

    def test_helm_clusterrole_supports_optional_decommission_role(self, helm_clusterrole_content):
        """Test that Helm templates expose an opt-in decommission ClusterRole."""
        decommission_block = helm_clusterrole_content.split("# ClusterRole for ACM Switchover Decommission", 1)[
            1
        ].split("# ClusterRole for ACM Switchover Validator", 1)[0]
        required_snippets = [
            ".Values.clusterRole.decommission.name",
            'resources: ["managedclusters"]',
            'verbs: ["get", "delete"]',
            'resources: ["clusterdeployments"]\n    verbs: ["list"]',
            'resources: ["multiclusterobservabilities"]\n    verbs: ["get", "delete"]',
        ]
        assert ".Values.rbac.includeDecommissionClusterRole" in helm_clusterrole_content
        for snippet in required_snippets:
            assert snippet in decommission_block
        assert 'resources: ["clusterdeployments"]\n    verbs: ["get", "list"]' not in decommission_block
        mc_rule = decommission_block.split('resources: ["managedclusters"]', 1)[1].split("- apiGroups:", 1)[0]
        assert 'verbs: ["get", "delete"]' in mc_rule
        assert "list" not in mc_rule
        assert "patch" not in mc_rule
        assert '"*"' not in mc_rule

    def test_bundled_decommission_clusterrole_matches_root_extension(self, decommission_clusterrole_path):
        """Collection-bundled decommission extension must stay byte-aligned with the root manifest."""
        bundled = (
            Path(__file__).resolve().parents[1]
            / "ansible_collections/tomazb/acm_switchover/roles/rbac_bootstrap/files"
            / "deploy/rbac/extensions/decommission/clusterrole.yaml"
        )
        assert bundled.exists()
        assert bundled.read_text(encoding="utf-8") == decommission_clusterrole_path.read_text(encoding="utf-8")

    def test_decommission_extension_managedcluster_verbs_are_get_delete_only(
        self, decommission_clusterrole_path, helm_clusterrole_content
    ):
        """Negative coverage: extension isolation grants MC get+delete and nothing broader."""
        for label, content in (
            ("static", decommission_clusterrole_path.read_text(encoding="utf-8")),
            (
                "helm",
                helm_clusterrole_content.split("# ClusterRole for ACM Switchover Decommission", 1)[1].split(
                    "# ClusterRole for ACM Switchover Validator", 1
                )[0],
            ),
        ):
            mc_rule = content.split('resources: ["managedclusters"]', 1)[1].split("- apiGroups:", 1)[0]
            assert 'verbs: ["get", "delete"]' in mc_rule, label
            verbs_line = next(line for line in mc_rule.splitlines() if "verbs:" in line)
            assert verbs_line.strip() == 'verbs: ["get", "delete"]', (label, verbs_line)
            for forbidden in ("list", "patch", "update", "create", "watch", "*"):
                assert forbidden not in verbs_line, (label, forbidden, verbs_line)

    def test_helm_namespace_template_marks_shared_resource_common(self, helm_namespace_content):
        """Helm namespace output must carry the same common marker used by role filtering."""
        assert "app.kubernetes.io/part-of: acm-switchover-rbac" in helm_namespace_content
        assert "app.kubernetes.io/role: common" in helm_namespace_content

    def test_helm_validator_custom_rule_guardrail_is_wired(self, helm_helpers_content, helm_clusterrole_content):
        """Helm must validate custom validator verbs before rendering the read-only ClusterRole."""
        assert 'define "acm-switchover-rbac.validateValidatorCustomRules"' in helm_helpers_content
        assert 'include "acm-switchover-rbac.validateValidatorCustomRules" .' in helm_clusterrole_content
        assert helm_clusterrole_content.index('include "acm-switchover-rbac.validateValidatorCustomRules" .') < (
            helm_clusterrole_content.index(".Values.rbac.customValidatorRules")
        )

    def test_helm_allows_read_only_custom_validator_rules(self, helm_binary, helm_chart_dir, tmp_path):
        """Read-only validator custom rules should render successfully."""
        values_file = tmp_path / "values.yaml"
        values_file.write_text(
            yaml.safe_dump(
                {
                    "rbac": {
                        "customValidatorRules": [
                            {
                                "apiGroups": ["custom.example.com"],
                                "resources": ["readablewidgets"],
                                "verbs": ["get", "list", "watch"],
                            }
                        ]
                    }
                }
            ),
            encoding="utf-8",
        )

        result = subprocess.run(
            [
                helm_binary,
                "template",
                "acm-switchover-rbac",
                str(helm_chart_dir),
                "-f",
                str(values_file),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )

        assert result.returncode == 0, result.stderr
        assert "readablewidgets" in result.stdout
        assert "watch" in result.stdout

    @pytest.mark.parametrize("forbidden_verb", ["delete", "*"])
    def test_helm_rejects_mutating_custom_validator_rules(self, helm_binary, helm_chart_dir, tmp_path, forbidden_verb):
        """Validator custom rules must not grant verbs outside the read-only set."""
        values_file = tmp_path / f"values-{forbidden_verb.replace('*', 'star')}.yaml"
        values_file.write_text(
            yaml.safe_dump(
                {
                    "rbac": {
                        "customValidatorRules": [
                            {
                                "apiGroups": ["custom.example.com"],
                                "resources": ["dangerouswidgets"],
                                "verbs": ["get", forbidden_verb],
                            }
                        ]
                    }
                }
            ),
            encoding="utf-8",
        )

        result = subprocess.run(
            [
                helm_binary,
                "template",
                "acm-switchover-rbac",
                str(helm_chart_dir),
                "-f",
                str(values_file),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )

        assert result.returncode != 0
        assert "rbac.customValidatorRules may only use read-only verbs" in result.stderr
        assert forbidden_verb in result.stderr

    @pytest.mark.parametrize(
        "rule",
        [
            {
                "apiGroups": ["custom.example.com"],
                "resources": ["invalidwidgets"],
            },
            {
                "apiGroups": ["custom.example.com"],
                "resources": ["invalidwidgets"],
                "verbs": "get",
            },
        ],
        ids=["missing-verbs", "scalar-verbs"],
    )
    def test_helm_rejects_invalid_custom_validator_rule_verbs_shape(self, helm_binary, helm_chart_dir, tmp_path, rule):
        """Validator custom rules must define verbs as a YAML list."""
        values_file = tmp_path / "values-invalid-verbs-shape.yaml"
        values_file.write_text(
            yaml.safe_dump({"rbac": {"customValidatorRules": [rule]}}),
            encoding="utf-8",
        )

        result = subprocess.run(
            [
                helm_binary,
                "template",
                "acm-switchover-rbac",
                str(helm_chart_dir),
                "-f",
                str(values_file),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )

        assert result.returncode != 0
        assert "rbac.customValidatorRules verbs must be a list of strings" in result.stderr
        assert "rule 0" in result.stderr

    def test_helm_rejects_non_mapping_custom_validator_rule_entry(self, helm_binary, helm_chart_dir, tmp_path):
        """Validator custom rules must be YAML mappings before rule fields are read."""
        values_file = tmp_path / "values-invalid-rule-entry.yaml"
        values_file.write_text(
            yaml.safe_dump({"rbac": {"customValidatorRules": ["invalid-rule-entry"]}}),
            encoding="utf-8",
        )

        result = subprocess.run(
            [
                helm_binary,
                "template",
                "acm-switchover-rbac",
                str(helm_chart_dir),
                "-f",
                str(values_file),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )

        assert result.returncode != 0
        assert "rbac.customValidatorRules entries must be mappings" in result.stderr
        assert "rule 0" in result.stderr


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

    def test_decommission_cluster_permissions_include_managedcluster_named_get(self):
        """Standalone decommission table must require ManagedCluster get (named UID/proof reads)."""
        mc = next(
            (
                p
                for p in RBACValidator.DECOMMISSION_CLUSTER_PERMISSIONS
                if p[0] == "cluster.open-cluster-management.io" and p[1] == "managedclusters"
            ),
            None,
        )
        assert mc is not None
        assert mc[2] == ["get", "list", "delete"]
        assert "patch" not in mc[2]
        assert "*" not in mc[2]

    def test_decommission_permissions_overlay_keeps_managedcluster_delete_only(self):
        """Additive DECOMMISSION_PERMISSIONS must not grow ManagedCluster reads or wildcards."""
        mc = next(
            (
                p
                for p in RBACValidator.DECOMMISSION_PERMISSIONS
                if p[0] == "cluster.open-cluster-management.io" and p[1] == "managedclusters"
            ),
            None,
        )
        assert mc is not None
        assert mc[2] == ["delete"]

    def test_baseline_operator_managedclusters_remain_without_delete(self):
        """Baseline operator ClusterRole table must not absorb decommission ManagedCluster delete."""
        mc = next(
            (
                p
                for p in RBACValidator.OPERATOR_CLUSTER_PERMISSIONS
                if p[0] == "cluster.open-cluster-management.io" and p[1] == "managedclusters"
            ),
            None,
        )
        assert mc is not None
        assert mc[2] == ["get", "list", "patch"]
        assert "delete" not in mc[2]

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
        backup_perms = RBACValidator.OPERATOR_HUB_NAMESPACE_PERMISSIONS.get(BACKUP_NAMESPACE, [])
        configmaps_perm = next((p for p in backup_perms if p[1] == "configmaps"), None)

        assert configmaps_perm is not None
        assert "create" in configmaps_perm[2], "Operator should have create on configmaps"
        assert "patch" in configmaps_perm[2], "Operator should have patch on configmaps"
        assert "delete" in configmaps_perm[2], "Operator should have delete on configmaps"

    def test_managed_cluster_permissions_exist_for_both_roles(self):
        """Test that managed cluster permissions are defined for both roles."""
        assert MANAGED_CLUSTER_AGENT_NAMESPACE in RBACValidator.OPERATOR_MANAGED_CLUSTER_NAMESPACE_PERMISSIONS
        assert MANAGED_CLUSTER_AGENT_NAMESPACE in RBACValidator.VALIDATOR_MANAGED_CLUSTER_NAMESPACE_PERMISSIONS

    def test_validator_backup_namespace_has_secrets_get(self):
        """Test that validator backup namespace includes secrets get permission."""
        backup_perms = RBACValidator.VALIDATOR_HUB_NAMESPACE_PERMISSIONS.get(BACKUP_NAMESPACE, [])
        secrets_perm = next((p for p in backup_perms if p[1] == "secrets"), None)

        assert secrets_perm is not None, "Validator should have secrets permission in backup namespace"
        assert "get" in secrets_perm[2], "Validator should have 'get' verb for secrets"

    def test_namespace_permission_maps_cover_centralized_namespaces(self):
        """RBAC namespace permission maps should align with the shared constants module."""
        assert BACKUP_NAMESPACE in RBACValidator.OPERATOR_HUB_NAMESPACE_PERMISSIONS
        assert ACM_NAMESPACE in RBACValidator.OPERATOR_HUB_NAMESPACE_PERMISSIONS
        assert OBSERVABILITY_NAMESPACE in RBACValidator.OPERATOR_HUB_NAMESPACE_PERMISSIONS
        assert MCE_NAMESPACE in RBACValidator.OPERATOR_HUB_NAMESPACE_PERMISSIONS
        assert MANAGED_CLUSTER_AGENT_NAMESPACE in RBACValidator.OPERATOR_MANAGED_CLUSTER_NAMESPACE_PERMISSIONS


# ---------------------------------------------------------------------------
# Measured decommission read surface (E7): the operator-identity capture and
# drain classification reads must be granted identically by every shipped form
# of the RBAC manifests, and by none of the read-only or baseline forms.
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[1]
_RBAC_DIR = _REPO_ROOT / "deploy" / "rbac"
_BUNDLED_RBAC_DIR = (
    _REPO_ROOT / "ansible_collections/tomazb/acm_switchover/roles/rbac_bootstrap/files" / "deploy" / "rbac"
)
_HELM_CHART_DIR = _REPO_ROOT / "deploy" / "helm" / "acm-switchover-rbac"
_POLICY_PATH = _REPO_ROOT / "deploy" / "acm-policies" / "policy-rbac.yaml"

_BASELINE_MANIFESTS = (
    "namespace.yaml",
    "serviceaccount.yaml",
    "clusterrole.yaml",
    "clusterrolebinding.yaml",
    "role.yaml",
    "rolebinding.yaml",
)
_EXTENSION_MANIFESTS = (
    "extensions/decommission/clusterrole.yaml",
    "extensions/decommission/clusterrolebinding.yaml",
)

_ACM_OPERATOR_ROLE = ("Role", "open-cluster-management", "acm-switchover-operator")
_ACM_VALIDATOR_ROLE = ("Role", "open-cluster-management", "acm-switchover-validator")
_OPERATOR_CLUSTERROLE = ("ClusterRole", "", "acm-switchover-operator")
_VALIDATOR_CLUSTERROLE = ("ClusterRole", "", "acm-switchover-validator")
_DECOMMISSION_CLUSTERROLE = ("ClusterRole", "", "acm-switchover-decommission")

# The complete, exact permission surface of the operator Role in the ACM namespace
# after E7. Equality (not membership) is the point: an accidental extra grant fails.
_ACM_OPERATOR_ROLE_PERMISSIONS = frozenset(
    {
        ("", "pods", "get"),
        ("", "pods", "list"),
        ("operator.open-cluster-management.io", "multiclusterhubs", "list"),
        ("operators.coreos.com", "clusterserviceversions", "get"),
        ("operators.coreos.com", "clusterserviceversions", "list"),
        ("apps", "deployments", "get"),
        ("apps", "replicasets", "get"),
    }
)

# The three rules E7 adds, and nothing else, expanded to (group, resource, verb).
_E7_OPERATOR_ONLY_PERMISSIONS = frozenset(
    {
        ("operators.coreos.com", "clusterserviceversions", "get"),
        ("operators.coreos.com", "clusterserviceversions", "list"),
        ("apps", "deployments", "get"),
        ("apps", "replicasets", "get"),
    }
)
_E7_OPERATOR_ONLY_RESOURCES = frozenset({"clusterserviceversions", "deployments", "replicasets"})

_ACM_VALIDATOR_ROLE_PERMISSIONS = frozenset({("", "pods", "get"), ("", "pods", "list")})

_MCH_DECOMMISSION_VERBS = ["get", "delete"]

_OPERATOR_SUBJECTS = frozenset({("ServiceAccount", "acm-switchover", "acm-switchover-operator")})
_VALIDATOR_SUBJECTS = frozenset({("ServiceAccount", "acm-switchover", "acm-switchover-validator")})
_BASELINE_BINDINGS = frozenset(
    {
        ("ClusterRoleBinding", "", "acm-switchover-operator", "acm-switchover-operator", _OPERATOR_SUBJECTS),
        ("ClusterRoleBinding", "", "acm-switchover-validator", "acm-switchover-validator", _VALIDATOR_SUBJECTS),
    }
    | {
        ("RoleBinding", namespace, f"acm-switchover-{role}", f"acm-switchover-{role}", subjects)
        for namespace in (
            "open-cluster-management",
            "open-cluster-management-backup",
            "open-cluster-management-observability",
            "multicluster-engine",
        )
        for role, subjects in (("operator", _OPERATOR_SUBJECTS), ("validator", _VALIDATOR_SUBJECTS))
    }
)
_DECOMMISSION_BINDING = (
    "ClusterRoleBinding",
    "",
    "acm-switchover-decommission",
    "acm-switchover-decommission",
    _OPERATOR_SUBJECTS,
)


def _permission_tuples(rules) -> frozenset:
    """Expand RBAC rules into (apiGroup, resource, verb) triples.

    Helm groups some resources into a single rule where the raw manifests use one
    rule per resource; only the expanded triples are comparable across forms.
    """
    return frozenset(
        (group, resource, verb)
        for rule in rules or []
        for group in rule.get("apiGroups", [])
        for resource in rule.get("resources", [])
        for verb in rule.get("verbs", [])
    )


def _index_objects(docs) -> dict:
    """Key parsed Kubernetes objects by (kind, namespace, name)."""
    indexed = {}
    for doc in docs:
        if not doc:
            continue
        metadata = doc["metadata"]
        indexed[(doc["kind"], metadata.get("namespace", ""), metadata["name"])] = doc
    return indexed


def _load_manifest_dir(directory: Path, include_extension: bool) -> dict:
    names = _BASELINE_MANIFESTS + (_EXTENSION_MANIFESTS if include_extension else ())
    docs = []
    for name in names:
        docs.extend(yaml.safe_load_all((directory / name).read_text(encoding="utf-8")))
    return _index_objects(docs)


def _load_policy_objects() -> dict:
    policy = next(
        doc for doc in yaml.safe_load_all(_POLICY_PATH.read_text(encoding="utf-8")) if doc.get("kind") == "Policy"
    )
    return _index_objects(
        object_template["objectDefinition"]
        for template in policy["spec"]["policy-templates"]
        for object_template in template["objectDefinition"]["spec"]["object-templates"]
    )


def _render_chart(*set_values: str) -> dict:
    helm = shutil.which("helm")
    if not helm:
        pytest.skip("helm binary not available")
    command = [helm, "template", "acm-switchover-rbac", str(_HELM_CHART_DIR)]
    for value in set_values:
        command.extend(["--set", value])
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return _index_objects(yaml.safe_load_all(result.stdout))


@pytest.fixture(scope="module")
def helm_objects() -> dict:
    """Chart rendered with the optional decommission ClusterRole enabled."""
    return _render_chart("rbac.includeDecommissionClusterRole=true")


@pytest.fixture(scope="module")
def helm_default_objects() -> dict:
    """Chart rendered with default values (decommission extension disabled)."""
    return _render_chart()


@pytest.fixture(params=["raw", "bundled", "helm", "policy"])
def rbac_objects(request) -> dict:
    """Every shipped form of the baseline RBAC objects, parsed and keyed."""
    if request.param == "raw":
        return _load_manifest_dir(_RBAC_DIR, include_extension=True)
    if request.param == "bundled":
        return _load_manifest_dir(_BUNDLED_RBAC_DIR, include_extension=True)
    if request.param == "helm":
        return request.getfixturevalue("helm_objects")
    return _load_policy_objects()


class TestMeasuredDecommissionReadSurface:
    """Every shipped manifest form must grant exactly the approved operator reads."""

    def test_acm_operator_role_grants_exactly_the_measured_surface(self, rbac_objects):
        """Operator Role in the ACM namespace: exact permission set, no extra grant."""
        role = rbac_objects[_ACM_OPERATOR_ROLE]
        assert _permission_tuples(role["rules"]) == _ACM_OPERATOR_ROLE_PERMISSIONS

    def test_acm_operator_role_satisfies_the_decommission_namespace_table(self, rbac_objects):
        """The manifests must grant what the decommission validators now require."""
        required = _permission_tuples(
            [
                {"apiGroups": [group], "resources": [resource], "verbs": list(verbs)}
                for group, resource, verbs in RBACValidator.DECOMMISSION_NAMESPACE_PERMISSIONS[ACM_NAMESPACE]
            ]
        )
        granted = _permission_tuples(rbac_objects[_ACM_OPERATOR_ROLE]["rules"])
        assert required <= granted, sorted(required - granted)

    def test_acm_validator_role_excludes_the_measured_operator_reads(self, rbac_objects):
        """Read-only validator Role must stay pods-only in the ACM namespace."""
        granted = _permission_tuples(rbac_objects[_ACM_VALIDATOR_ROLE]["rules"])
        assert granted == _ACM_VALIDATOR_ROLE_PERMISSIONS
        assert not granted & _E7_OPERATOR_ONLY_PERMISSIONS

    @pytest.mark.parametrize("clusterrole_key", [_OPERATOR_CLUSTERROLE, _VALIDATOR_CLUSTERROLE])
    def test_baseline_clusterroles_stay_clear_of_the_decommission_surface(self, rbac_objects, clusterrole_key):
        """Operator identity reads are namespace-scoped; MCH delete stays in the extension."""
        granted = _permission_tuples(rbac_objects[clusterrole_key]["rules"])
        assert not {resource for _, resource, _ in granted} & _E7_OPERATOR_ONLY_RESOURCES
        assert ("operator.open-cluster-management.io", "multiclusterhubs", "delete") not in granted

    def test_no_wildcard_grants_anywhere(self, rbac_objects):
        """No wildcard apiGroup, resource or verb in any shipped Role or ClusterRole."""
        for (kind, namespace, name), obj in rbac_objects.items():
            if kind not in ("Role", "ClusterRole"):
                continue
            for rule in obj.get("rules") or []:
                for field in ("apiGroups", "resources", "verbs"):
                    assert "*" not in rule.get(field, []), (kind, namespace, name, field)

    def test_binding_set_is_unchanged(self, request, rbac_objects):
        """The delta grants permissions; it must not add or retarget any binding."""
        expected = _BASELINE_BINDINGS
        if request.node.callspec.params["rbac_objects"] != "policy":
            expected = expected | {_DECOMMISSION_BINDING}
        actual = {
            (
                kind,
                namespace,
                name,
                obj["roleRef"]["name"],
                frozenset(
                    (subject["kind"], subject.get("namespace", ""), subject["name"]) for subject in obj["subjects"]
                ),
            )
            for (kind, namespace, name), obj in rbac_objects.items()
            if kind in ("RoleBinding", "ClusterRoleBinding")
        }
        assert actual == expected


class TestDecommissionExtensionMultiClusterHubVerbs:
    """The optional extension must grant MCH get+delete and nothing broader."""

    @pytest.fixture(params=["raw", "bundled", "helm"])
    def decommission_clusterrole(self, request) -> dict:
        if request.param == "helm":
            return request.getfixturevalue("helm_objects")[_DECOMMISSION_CLUSTERROLE]
        directory = _RBAC_DIR if request.param == "raw" else _BUNDLED_RBAC_DIR
        return _load_manifest_dir(directory, include_extension=True)[_DECOMMISSION_CLUSTERROLE]

    def test_multiclusterhub_verbs_are_get_delete_only(self, decommission_clusterrole):
        """Named GET before the UID-preconditioned delete, and for the absence proof."""
        mch_rule = next(
            rule
            for rule in decommission_clusterrole["rules"]
            if rule["apiGroups"] == ["operator.open-cluster-management.io"] and "multiclusterhubs" in rule["resources"]
        )
        assert mch_rule["verbs"] == _MCH_DECOMMISSION_VERBS
        assert set(mch_rule["verbs"]) == {"get", "delete"}

    def test_extension_grants_no_operator_identity_reads(self, decommission_clusterrole):
        """Operator identity capture is namespace-scoped; the extension must not grow it."""
        granted = _permission_tuples(decommission_clusterrole["rules"])
        assert not {resource for _, resource, _ in granted} & _E7_OPERATOR_ONLY_RESOURCES


class TestHelmRenderMatchesRawManifests:
    """Rendered chart semantics must match the raw manifests under the same configuration."""

    def test_rendered_permissions_match_raw_manifests(self, helm_objects):
        """Full-set comparison of every rendered Role/ClusterRole against the raw form."""
        raw = _load_manifest_dir(_RBAC_DIR, include_extension=True)
        raw_rules = {
            key: _permission_tuples(obj["rules"]) for key, obj in raw.items() if key[0] in ("Role", "ClusterRole")
        }
        rendered_rules = {
            key: _permission_tuples(obj["rules"])
            for key, obj in helm_objects.items()
            if key[0] in ("Role", "ClusterRole")
        }
        assert set(rendered_rules) == set(raw_rules)
        mismatched = {
            key: (raw_rules[key], rendered_rules[key]) for key in raw_rules if raw_rules[key] != rendered_rules[key]
        }
        assert not mismatched

    def test_default_render_omits_the_decommission_clusterrole(self, helm_default_objects):
        """The extension stays opt-in: default values grant no MCH delete anywhere."""
        assert _DECOMMISSION_CLUSTERROLE not in helm_default_objects
        for key, obj in helm_default_objects.items():
            if key[0] not in ("Role", "ClusterRole"):
                continue
            granted = _permission_tuples(obj["rules"])
            assert ("operator.open-cluster-management.io", "multiclusterhubs", "delete") not in granted
