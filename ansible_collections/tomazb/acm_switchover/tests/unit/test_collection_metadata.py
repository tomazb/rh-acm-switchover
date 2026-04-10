from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[5]
COLLECTION_ROOT = REPO_ROOT / "ansible_collections" / "tomazb" / "acm_switchover"


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
