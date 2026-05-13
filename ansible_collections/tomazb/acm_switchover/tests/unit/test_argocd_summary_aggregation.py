"""Static tests for Argo CD summary aggregation across hub invocations."""

import pathlib

import yaml

ROLE_DIR = pathlib.Path(__file__).resolve().parents[2] / "roles" / "argocd_manage" / "tasks"


def _load_yaml(name: str) -> list[dict]:
    return yaml.safe_load((ROLE_DIR / name).read_text())


def _extract_set_fact_values(tasks: list[dict], key: str) -> list[str]:
    values: list[str] = []
    for task in tasks:
        for block_task in task.get("block", []) or []:
            sf = block_task.get("ansible.builtin.set_fact")
            if sf and key in sf:
                values.append(str(sf[key]))
    return values


def test_pause_summary_is_aggregated_not_overwritten():
    """pause.yml must accumulate paused totals instead of overwriting per hub."""
    tasks = _load_yaml("pause.yml")
    values = _extract_set_fact_values(tasks, "acm_switchover_argocd_summary")

    assert values, "pause.yml must set acm_switchover_argocd_summary"
    for value in values:
        assert (
            "acm_switchover_argocd_summary" in value
        ), "pause.yml must reference the existing summary when updating totals"
        assert "get('paused'" in value, "pause.yml must accumulate paused totals using the previous summary"

    by_hub_values = _extract_set_fact_values(tasks, "acm_switchover_argocd_summary_by_hub")
    assert by_hub_values, "pause.yml must set acm_switchover_argocd_summary_by_hub"
    for value in by_hub_values:
        assert "acm_switchover_argocd_summary_by_hub" in value
        assert "_argocd_discover_hub" in value


def test_resume_summary_is_aggregated_not_overwritten():
    """resume.yml must accumulate restored totals instead of overwriting per hub."""
    tasks = _load_yaml("resume.yml")
    values = _extract_set_fact_values(tasks, "acm_switchover_argocd_summary")

    assert values, "resume.yml must set acm_switchover_argocd_summary"
    for value in values:
        assert (
            "acm_switchover_argocd_summary" in value
        ), "resume.yml must reference the existing summary when updating totals"
        assert "get('restored'" in value, "resume.yml must accumulate restored totals using the previous summary"

    by_hub_values = _extract_set_fact_values(tasks, "acm_switchover_argocd_summary_by_hub")
    assert by_hub_values, "resume.yml must set acm_switchover_argocd_summary_by_hub"
    for value in by_hub_values:
        assert "acm_switchover_argocd_summary_by_hub" in value
        assert "_argocd_discover_hub" in value
