"""Guardrails for the single canonical Phase -> report/resume name mapping (Thermos R2-M4)."""

import lib.cli_outcomes as cli_outcomes
import lib.utils as utils
import lib.workflow as workflow
from lib.utils import CANONICAL_PHASE_NAMES, Phase


def test_canonical_phase_names_cover_exactly_the_executable_phases():
    """INIT/COMPLETED/FAILED are lifecycle markers, not executable phases, and stay unmapped."""
    assert set(CANONICAL_PHASE_NAMES) == {
        Phase.PREFLIGHT,
        Phase.PRIMARY_PREP,
        Phase.SECONDARY_VERIFY,
        Phase.ACTIVATION,
        Phase.POST_ACTIVATION,
        Phase.FINALIZATION,
    }


def test_legacy_secondary_verify_folds_into_activation():
    assert CANONICAL_PHASE_NAMES[Phase.SECONDARY_VERIFY] == "activation"
    assert CANONICAL_PHASE_NAMES[Phase.ACTIVATION] == "activation"


def test_single_mapping_object_shared_by_all_consumers():
    """workflow and cli_outcomes must use the exact same dict object as lib.utils."""
    assert workflow.CANONICAL_PHASE_NAMES is CANONICAL_PHASE_NAMES
    assert cli_outcomes.CANONICAL_PHASE_NAMES is CANONICAL_PHASE_NAMES


def test_old_duplicate_names_are_gone():
    assert not hasattr(utils, "REPORT_PHASE_NAMES")
    assert not hasattr(workflow, "_CANONICAL_RESUME_START_PHASES")
