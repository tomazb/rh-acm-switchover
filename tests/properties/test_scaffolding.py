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


@pytest.mark.property
def test_property_scaffolding_uses_selected_profile() -> None:
    """Exercise generation and verify the profile selected during collection."""
    profile = os.environ.get("HYPOTHESIS_PROFILE", "dev")
    generated_examples: set[int] = set()

    @given(st.integers(min_value=-10, max_value=10))
    def exercise_generated_examples(value: int) -> None:
        generated_examples.add(value)

        assert settings.default.max_examples == PROFILE_MAX_EXAMPLES[profile]
        assert int(str(value)) == value

    exercise_generated_examples()

    assert len(generated_examples) > 1
