"""Static tests ensuring Argo CD report summary aggregates across hubs."""

import pathlib

import yaml

COLLECTION_DIR = pathlib.Path(__file__).resolve().parents[2]
PLAYBOOKS_DIR = COLLECTION_DIR / "playbooks"


def _load_playbook(name: str) -> list[dict]:
    return yaml.safe_load((PLAYBOOKS_DIR / name).read_text())


def _get_report_contract(playbook: list[dict]) -> dict:
    for play in playbook:
        for task in play.get("tasks", []):
            if "block" not in task:
                continue
            for always_task in task.get("always", []) or []:
                sf = always_task.get("ansible.builtin.set_fact")
                if sf and "acm_switchover_report" in sf:
                    return sf["acm_switchover_report"]
    raise AssertionError("Could not locate acm_switchover_report set_fact in playbook always block")


def test_switchover_report_aggregates_argocd_summary_by_hub_when_available():
    report = _get_report_contract(_load_playbook("switchover.yml"))
    summary = report.get("argocd", {}).get("summary")
    assert isinstance(summary, str)
    assert "acm_switchover_argocd_summary_by_hub" in summary
    assert "namespace(" in summary
    assert "hubs.items()" in summary
    assert "ns.paused" in summary
    assert "ns.restored" in summary


def test_restore_only_report_aggregates_argocd_summary_by_hub_when_available():
    report = _get_report_contract(_load_playbook("restore_only.yml"))
    summary = report.get("argocd", {}).get("summary")
    assert isinstance(summary, str)
    assert "acm_switchover_argocd_summary_by_hub" in summary
    assert "namespace(" in summary
    assert "hubs.items()" in summary
    assert "ns.paused" in summary
    assert "ns.restored" in summary
