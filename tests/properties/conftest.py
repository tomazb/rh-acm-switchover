"""Hypothesis profiles shared by property-based tests."""

import os

from hypothesis import settings

settings.register_profile("dev", max_examples=50, deadline=None)
settings.register_profile("ci", max_examples=100, deadline=None)
settings.register_profile("deep", max_examples=1000, deadline=None)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "dev"))
