"""Render the shipped cadence expression, including its fallback policy (#367)."""

from pathlib import Path

import pytest
import yaml
from ansible.errors import AnsibleError
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar
from jinja2 import Environment

try:
    from ansible.template import trust_as_template
except ImportError:

    def trust_as_template(value):
        return value


TASK_FILE = Path(__file__).resolve().parents[2] / "roles/finalization/tasks/verify_backups.yml"


def _expression():
    tasks = yaml.safe_load(TASK_FILE.read_text(encoding="utf-8"))
    return next(
        task["ansible.builtin.set_fact"]["_acm_secondary_backup_verify_timeout_seconds"]
        for task in tasks
        if "_acm_secondary_backup_verify_timeout_seconds" in task.get("ansible.builtin.set_fact", {})
    )


def _render(schedule_info):
    return Templar(loader=DataLoader(), variables={"acm_finalization_backup_schedules_info": schedule_info}).template(
        trust_as_template(_expression())
    )


def test_shipped_backup_cadence_expression_parses():
    Environment().parse(_expression())


@pytest.mark.parametrize(
    ("cron", "seconds"),
    [
        ("*/5 * * * *", 900),
        ("*/1 * * * *", 660),
        ("*/0 * * * *", 600),
        ("5 */2 * * *", 7800),
        ("5 2 */3 * *", 259800),
        ("5 2 * * *", 87000),
        ("5 2 * * 1", 605400),
        ("5 2 1 * *", 2592600),
        ("* * * * *", 600),
        ("not a cron expression", 600),
        ("", 600),
    ],
)
def test_approved_cadences_preserve_timeout_and_completion_grace(cron, seconds):
    assert int(_render({"resources": [{"spec": {"veleroSchedule": cron}}]})) == seconds


def test_supplemental_negative_interval_uses_existing_fallback():
    assert int(_render({"resources": [{"spec": {"veleroSchedule": "*/-1 * * * *"}}]})) == 600


@pytest.mark.parametrize("schedule_info", [{}, {"resources": []}, {"resources": [{}]}])
def test_missing_schedule_uses_minimum_timeout(schedule_info):
    assert int(_render(schedule_info)) == 600


def test_malformed_schedule_structure_fails_instead_of_inventing_a_cadence():
    with pytest.raises(AnsibleError):
        _render({"resources": [{"spec": "invalid"}]})
