"""Contract: summary-path resolution goes through the shared filter (Thermos R2-M5)."""

from pathlib import Path

COLLECTION_ROOT = Path(__file__).resolve().parents[2]
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
