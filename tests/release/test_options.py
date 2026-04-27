import pytest

from tests.release.conftest import RELEASE_PROFILE_SKIP_REASON, ReleaseOptions, resolve_release_mode, should_skip_release_items


def test_should_skip_release_items_without_profile() -> None:
    assert should_skip_release_items(profile_path=None) is True
    assert RELEASE_PROFILE_SKIP_REASON == "release tests require an explicit release profile"


def test_should_not_skip_release_items_with_profile(tmp_path) -> None:
    assert should_skip_release_items(profile_path=tmp_path / "profile.yaml") is False


def test_resolve_release_mode_defaults_to_certification_without_filters() -> None:
    assert resolve_release_mode(explicit_mode=None, scenario_filters=(), stream_filters=()) == "certification"


def test_resolve_release_mode_defaults_to_focused_rerun_with_filters() -> None:
    assert resolve_release_mode(explicit_mode=None, scenario_filters=("preflight",), stream_filters=()) == "focused-rerun"


def test_release_options_registered(pytestconfig: pytest.Config) -> None:
    assert pytestconfig.getoption("--release-profile", default=None) is None
    assert ReleaseOptions.__name__ == "ReleaseOptions"
