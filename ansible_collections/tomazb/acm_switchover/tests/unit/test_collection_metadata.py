from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[5]
COLLECTION_ROOT = REPO_ROOT / "ansible_collections" / "tomazb" / "acm_switchover"
PACKAGED_RBAC_ROOT = COLLECTION_ROOT / "roles" / "rbac_bootstrap" / "files" / "deploy" / "rbac"
REPO_RBAC_ROOT = REPO_ROOT / "deploy" / "rbac"


def test_galaxy_yml_parses():
    data = yaml.safe_load((COLLECTION_ROOT / "galaxy.yml").read_text())
    assert data["namespace"] == "tomazb"
    assert data["name"] == "acm_switchover"


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
