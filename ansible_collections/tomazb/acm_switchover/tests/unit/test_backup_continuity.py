"""Collection raw checkpoint facts and independent Backup chronology decisions."""

import copy
import json
from datetime import datetime
from pathlib import Path

import pytest

from ansible_collections.tomazb.acm_switchover.plugins.module_utils import backup_continuity as owner
from ansible_collections.tomazb.acm_switchover.plugins.module_utils.checkpoint import (
    backup_continuity_facts,
    checkpoint_facts,
)

VECTORS = Path(__file__).resolve().parents[5] / "tests/fixtures/backup_continuity_compatibility.json"


@pytest.mark.parametrize("case", json.loads(VECTORS.read_text()), ids=lambda c: c["id"])
def test_collection_boundary_creation_vectors(case):
    before = copy.deepcopy(case)
    now = datetime.fromisoformat(case["observed_at"].replace("Z", "+00:00"))
    try:
        boundary = owner.resolve_enable_boundary(case["inputs"], observed_at=now)
        owner.validate_backup(case["backup"], enabled_at=boundary, observed_at=now)
    except owner.BackupContinuityError as exc:
        assert exc.reason == case["collection_reason"]
    else:
        assert case["collection_reason"] is None
    assert case == before


@pytest.mark.parametrize("value", [None, False, 0, "", [], {}, "not-a-time"])
def test_raw_fact_presence_survives_falsey_or_malformed_value(value):
    checkpoint = {
        "operational_data": {"backup_schedule_enabled_at": value},
        "completed_phases": ["preflight", "finalization"],
        "phase": "finalization",
        "phase_status": "passed",
        "operation_identity": {"restore_only": False},
    }
    before = copy.deepcopy(checkpoint)
    facts = backup_continuity_facts(checkpoint)
    assert facts["enabled_at_present"] is True and facts["enabled_at"] == value
    assert facts["finalization_completed"] is True
    assert checkpoint_facts(checkpoint)["backup_continuity"] == facts
    assert checkpoint == before


@pytest.mark.parametrize("checkpoint", [None, {"operational_data": []}, {"completed_phases": "finalization"}])
def test_malformed_checkpoint_fact_containers_refuse(checkpoint):
    with pytest.raises(owner.BackupContinuityError, match="invalid_backup_evidence"):
        owner.resolve_enable_boundary(
            backup_continuity_facts(checkpoint), observed_at=datetime.fromisoformat("2026-10-17T11:00:00+00:00")
        )
