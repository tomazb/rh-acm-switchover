from __future__ import annotations

import pytest


@pytest.mark.release
def test_release_certification(release_options) -> None:
    assert release_options.profile_path is not None
