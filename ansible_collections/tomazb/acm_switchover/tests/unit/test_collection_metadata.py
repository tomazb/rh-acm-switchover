from pathlib import Path
import re

import yaml

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
