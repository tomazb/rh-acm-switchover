import re
from pathlib import Path

import yaml

from ansible_collections.tomazb.acm_switchover.plugins.module_utils.constants import (
    RBAC_BASE_ASSETS,
    RBAC_DECOMMISSION_ASSETS,
)

REPO_ROOT = Path(__file__).resolve().parents[5]
COLLECTION_ROOT = REPO_ROOT / "ansible_collections" / "tomazb" / "acm_switchover"
PACKAGED_RBAC_ROOT = COLLECTION_ROOT / "roles" / "rbac_bootstrap" / "files" / "deploy" / "rbac"
REPO_RBAC_ROOT = REPO_ROOT / "deploy" / "rbac"
ROLE_META_FILES = sorted(COLLECTION_ROOT.glob("roles/*/meta/main.yml"))


def test_galaxy_yml_parses():
    data = yaml.safe_load((COLLECTION_ROOT / "galaxy.yml").read_text())
    assert data["namespace"] == "tomazb"
    assert data["name"] == "acm_switchover"


def test_collection_version_matches_repo_release_version():
    galaxy_data = yaml.safe_load((COLLECTION_ROOT / "galaxy.yml").read_text())
    chart_data = yaml.safe_load((REPO_ROOT / "deploy" / "helm" / "acm-switchover-rbac" / "Chart.yaml").read_text())
    init_text = (REPO_ROOT / "lib" / "__init__.py").read_text()
    match = re.search(r'__version__ = "([^"]+)"', init_text)

    assert match, "Could not find lib.__version__"
    expected_version = match.group(1)

    assert galaxy_data["version"] == expected_version
    assert chart_data["version"] == expected_version
    assert chart_data["appVersion"] == expected_version


def test_all_release_version_surfaces_match_repo_release_version():
    init_text = (REPO_ROOT / "lib" / "__init__.py").read_text()
    match = re.search(r'__version__ = "([^"]+)"', init_text)
    assert match, "Could not find lib.__version__"
    expected_version = match.group(1)

    version_surfaces = {
        "README.md": rf"\*\*Version {re.escape(expected_version)}\*\*",
        "setup.cfg": rf"^version = {re.escape(expected_version)}$",
        "scripts/constants.sh": rf'^export SCRIPT_VERSION="{re.escape(expected_version)}"$',
        "container-bootstrap/Containerfile": rf'version="{re.escape(expected_version)}"',
        "deploy/helm/acm-switchover-rbac/Chart.yaml": rf"^version: {re.escape(expected_version)}$",
        "ansible_collections/tomazb/acm_switchover/galaxy.yml": rf"^version: {re.escape(expected_version)}$",
        "tests/release/profiles/full-release.example.yaml": rf"^\s+expected_version: {re.escape(expected_version)}$",
        "tests/release/profiles/argocd-release.example.yaml": rf"^\s+expected_version: {re.escape(expected_version)}$",
    }

    for relative_path, pattern in version_surfaces.items():
        text = (REPO_ROOT / relative_path).read_text()
        assert re.search(pattern, text, re.MULTILINE), f"{relative_path} should reference {expected_version}"


def test_collection_license_metadata_matches_repo_license():
    galaxy_data = yaml.safe_load((COLLECTION_ROOT / "galaxy.yml").read_text())
    license_text = (REPO_ROOT / "LICENSE").read_text()

    assert galaxy_data["license"] == ["MIT"]
    assert license_text.startswith("MIT License")

    for meta_file in ROLE_META_FILES:
        role_meta = yaml.safe_load(meta_file.read_text())
        assert role_meta["galaxy_info"]["license"] == "MIT", f"{meta_file} should declare MIT"


def test_runtime_yml_parses():
    data = yaml.safe_load((COLLECTION_ROOT / "meta" / "runtime.yml").read_text())
    assert data["requires_ansible"].startswith(">=")


def test_example_group_vars_parse():
    data = yaml.safe_load((COLLECTION_ROOT / "examples" / "group_vars" / "all.yml").read_text())
    assert "acm_switchover_hubs" in data
    assert "acm_switchover_execution" in data


def test_packaged_rbac_manifests_match_repo_assets():
    repo_files = sorted(path for path in REPO_RBAC_ROOT.rglob("*.yaml"))
    packaged_files = sorted(path for path in PACKAGED_RBAC_ROOT.rglob("*.yaml"))

    assert [path.relative_to(REPO_RBAC_ROOT) for path in repo_files] == [
        path.relative_to(PACKAGED_RBAC_ROOT) for path in packaged_files
    ]

    for repo_file in repo_files:
        packaged_file = PACKAGED_RBAC_ROOT / repo_file.relative_to(REPO_RBAC_ROOT)
        assert packaged_file.read_text() == repo_file.read_text()


def test_rbac_bootstrap_asset_constants_cover_all_packaged_manifests():
    packaged_files = sorted(
        str(path.relative_to(PACKAGED_RBAC_ROOT.parent.parent)) for path in PACKAGED_RBAC_ROOT.rglob("*.yaml")
    )
    selected_assets = sorted(RBAC_BASE_ASSETS + RBAC_DECOMMISSION_ASSETS)

    assert selected_assets == packaged_files


def test_shared_rbac_manifest_resources_are_explicitly_marked_common():
    """Role filtering must have an explicit marker for shared bootstrap resources."""
    shared_resources = {
        ("Namespace", "acm-switchover"),
    }

    for manifest_file in sorted(REPO_RBAC_ROOT.rglob("*.yaml")):
        for resource in yaml.safe_load_all(manifest_file.read_text()):
            if not resource:
                continue
            metadata = resource.get("metadata") or {}
            labels = metadata.get("labels") or {}
            identity = (resource.get("kind"), metadata.get("name"))
            if identity in shared_resources:
                assert labels.get("app.kubernetes.io/part-of") == "acm-switchover-rbac"
                assert labels.get("app.kubernetes.io/role") == "common"
            else:
                assert labels.get("app.kubernetes.io/role") in {"operator", "validator"}


def test_helm_values_do_not_document_unused_custom_namespaces():
    """Helm values and README must not expose values that no template consumes."""
    chart_root = REPO_ROOT / "deploy" / "helm" / "acm-switchover-rbac"
    values = yaml.safe_load((chart_root / "values.yaml").read_text())
    readme = (chart_root / "README.md").read_text()

    assert "customNamespaces" not in values.get("rbac", {})
    assert "rbac.customNamespaces" not in readme
