"""Shared declared vectors, independently implemented Backup proof decisions."""

import copy
import json
from datetime import datetime
from pathlib import Path

import pytest

from ansible_collections.tomazb.acm_switchover.plugins.module_utils import backup_continuity as collection_owner
from modules import backup_continuity as python_owner

VECTORS = Path(__file__).parent / "fixtures/backup_continuity_compatibility.json"


@pytest.mark.parametrize("case", json.loads(VECTORS.read_text()), ids=lambda c: c["id"])
@pytest.mark.parametrize("owner,expected_key", [(python_owner, "reason"), (collection_owner, "collection_reason")])
def test_independent_creation_boundary_decisions(case, owner, expected_key):
    before = copy.deepcopy(case)
    observed = datetime.fromisoformat(case["observed_at"].replace("Z", "+00:00"))
    try:
        boundary = owner.resolve_enable_boundary(case["inputs"], observed_at=observed)
        result = owner.validate_backup(case["backup"], enabled_at=boundary, observed_at=observed)
    except owner.BackupContinuityError as exc:
        assert exc.reason == case[expected_key]
    else:
        assert case[expected_key] is None
        assert result["uid"] == "proof-uid"
    assert case == before


def test_backup_ownership_constants_stay_with_each_form_factor_owner():
    from ansible_collections.tomazb.acm_switchover.plugins.module_utils import constants as collection_constants
    from lib import constants as python_constants

    assert python_constants.ACM_BACKUP_SCHEDULE_TYPE_LABEL == collection_constants.ACM_BACKUP_SCHEDULE_TYPE_LABEL
    assert python_constants.ACM_BACKUP_SCHEDULE_TYPES == collection_constants.ACM_BACKUP_SCHEDULE_TYPES
    assert python_constants.ACM_BACKUP_NAME_RE.pattern == collection_constants.ACM_BACKUP_NAME_RE.pattern
