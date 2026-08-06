"""Parity contract: cross-phase state key names shared between runtimes (issue #214).

The Python CLI persists cross-phase facts through the RunRecord facade
(lib/run_record.py); the collection persists them in checkpoint
operational_data (module_utils/checkpoint.py). Shared names are pinned equal;
intentional divergences are pinned explicitly so silent drift is impossible.
"""

import lib.constants as py_constants
import lib.run_record as py_run_record
from ansible_collections.tomazb.acm_switchover.plugins.module_utils import checkpoint as ansible_checkpoint


def test_shared_key_names_match():
    assert ansible_checkpoint.KEY_RESUME_SUMMARY == py_constants.STATE_KEY_RESUME_SUMMARY
    assert ansible_checkpoint.KEY_RESUME_START_PHASE == py_constants.RESUME_START_PHASE_KEY
    assert ansible_checkpoint.KEY_EXPECTED_MANAGED_CLUSTER_NAMES == py_constants.EXPECTED_MANAGED_CLUSTER_NAMES_KEY
    assert ansible_checkpoint.KEY_EXPECTED_MANAGED_CLUSTER_COUNT == py_constants.EXPECTED_MANAGED_CLUSTER_COUNT_KEY
    assert ansible_checkpoint.KEY_PRIMARY_HAS_OBSERVABILITY == py_run_record._KEY_PRIMARY_HAS_OBS
    assert ansible_checkpoint.KEY_SECONDARY_HAS_OBSERVABILITY == py_run_record._KEY_SECONDARY_HAS_OBS
    assert ansible_checkpoint.KEY_SAVED_BACKUP_SCHEDULE == py_run_record._KEY_SAVED_BACKUP_SCHEDULE
    assert ansible_checkpoint.KEY_BACKUP_SCHEDULE_ENABLED_AT == py_run_record._KEY_BACKUP_WATCH_STARTED_AT


def test_intentional_divergences_are_pinned():
    """auto-import obligation: Python records auto_import_strategy_set (state
    file, always on); the collection records auto_import_strategy_changed
    (checkpoint) plus the cluster marker. Renaming either side without
    updating this contract is a parity break."""
    assert py_run_record._KEY_AUTO_IMPORT_SET == "auto_import_strategy_set"
    assert ansible_checkpoint.KEY_AUTO_IMPORT_STRATEGY_CHANGED == "auto_import_strategy_changed"
