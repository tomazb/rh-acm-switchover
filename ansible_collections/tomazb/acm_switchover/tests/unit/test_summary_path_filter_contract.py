"""Contract: summary-path resolution goes through the shared filter (Thermos R2-M5)."""

from pathlib import Path


def _parent_at(path: Path, index: int) -> Path:
    if len(path.parents) <= index:
        raise ValueError(f"Path {path} does not have enough parent directories to access index {index}")
    return path.parents[index]


COLLECTION_ROOT = _parent_at(Path(__file__).resolve(), 2)
SITES = (
    COLLECTION_ROOT / "roles" / "discovery" / "tasks" / "main.yml",
    COLLECTION_ROOT / "roles" / "decommission" / "tasks" / "main.yml",
    COLLECTION_ROOT / "roles" / "rbac_bootstrap" / "tasks" / "main.yml",
    COLLECTION_ROOT / "playbooks" / "argocd_manage_test.yml",
)


def test_all_summary_path_sites_use_the_shared_filter():
    for site in SITES:
        text = site.read_text()
        assert "acm_abs_path" in text, f"{site} must resolve the summary path via the shared filter"
        assert "startswith('/')" not in text, f"{site} still inlines the absolute-path expression"
