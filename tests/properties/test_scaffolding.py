"""Smoke coverage for the property-test scaffolding."""

import os

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

PROFILE_MAX_EXAMPLES = {
    "dev": 50,
    "ci": 100,
    "deep": 1000,
}


def test_hypothesis_profile_loaded() -> None:
    """Verify the selected Hypothesis profile is active."""
    profile = os.environ.get("HYPOTHESIS_PROFILE", "dev")
    assert settings.default.max_examples == PROFILE_MAX_EXAMPLES[profile]


@pytest.mark.property
@given(st.integers(min_value=-10, max_value=10))
def test_property_scaffolding_smoke(value: int) -> None:
    """Verify normal pytest/Hypothesis collection and a simple invariant."""
    assert int(str(value)) == value
