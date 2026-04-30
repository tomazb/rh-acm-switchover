from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

import pytest

from tests.release.contracts import load_profile
from tests.release.reporting.artifacts import ReleaseArtifacts
from tests.release.scenarios.catalog import select_release_matrix

RELEASE_PROFILE_SKIP_REASON = "release tests require an explicit release profile"


@dataclass(frozen=True)
class ReleaseOptions:
    profile_path: Path | None
    mode: str | None
    scenarios: tuple[str, ...]
    streams: tuple[str, ...]
    resume_from_artifacts: Path | None
    rerun_from_artifacts: Path | None
    artifact_dir: Path | None
    allow_dirty: bool


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("release validation")
    group.addoption("--release-profile", action="store", default=None)
    group.addoption(
        "--release-mode",
        action="store",
        choices=("certification", "focused-rerun", "debug"),
        default=None,
    )
    group.addoption("--release-scenario", action="append", default=[])
    group.addoption(
        "--release-stream",
        action="append",
        choices=("bash", "python", "ansible"),
        default=[],
    )
    group.addoption("--release-resume-from-artifacts", action="store", default=None)
    group.addoption("--release-rerun-from-artifacts", action="store", default=None)
    group.addoption("--release-artifact-dir", action="store", default=None)
    group.addoption("--allow-dirty", action="store_true", default=False)


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "release: real-cluster release certification tests")


def _profile_path(config: pytest.Config) -> Path | None:
    raw = config.getoption("--release-profile") or os.environ.get("ACM_RELEASE_PROFILE")
    return Path(raw) if raw else None


def should_skip_release_items(*, profile_path: Path | None) -> bool:
    return profile_path is None


def resolve_release_mode(
    *,
    explicit_mode: str | None,
    scenario_filters: tuple[str, ...],
    stream_filters: tuple[str, ...],
) -> str:
    if explicit_mode:
        return explicit_mode
    return "focused-rerun" if scenario_filters or stream_filters else "certification"


def pytest_collection_modifyitems(config: pytest.Config, items: Sequence[pytest.Item]) -> None:
    if not should_skip_release_items(profile_path=_profile_path(config)):
        return
    skip_release = pytest.mark.skip(reason=RELEASE_PROFILE_SKIP_REASON)
    for item in items:
        if list(item.iter_markers("release")):
            item.add_marker(skip_release)


@pytest.fixture(scope="session")
def release_options(pytestconfig: pytest.Config) -> ReleaseOptions:
    scenario_filter = tuple(pytestconfig.getoption("--release-scenario") or ())
    stream_filter = tuple(pytestconfig.getoption("--release-stream") or ())
    mode = resolve_release_mode(
        explicit_mode=pytestconfig.getoption("--release-mode"),
        scenario_filters=scenario_filter,
        stream_filters=stream_filter,
    )
    return ReleaseOptions(
        profile_path=_profile_path(pytestconfig),
        mode=mode,
        scenarios=scenario_filter,
        streams=stream_filter,
        resume_from_artifacts=(
            Path(pytestconfig.getoption("--release-resume-from-artifacts"))
            if pytestconfig.getoption("--release-resume-from-artifacts")
            else None
        ),
        rerun_from_artifacts=(
            Path(pytestconfig.getoption("--release-rerun-from-artifacts"))
            if pytestconfig.getoption("--release-rerun-from-artifacts")
            else None
        ),
        artifact_dir=(
            Path(pytestconfig.getoption("--release-artifact-dir"))
            if pytestconfig.getoption("--release-artifact-dir")
            else None
        ),
        allow_dirty=bool(pytestconfig.getoption("--allow-dirty")),
    )


@pytest.fixture(scope="session")
def release_profile(release_options: ReleaseOptions):
    if release_options.profile_path is None:
        pytest.skip(RELEASE_PROFILE_SKIP_REASON)
    return load_profile(release_options.profile_path)


@pytest.fixture(scope="session")
def selected_release_matrix(release_profile, release_options: ReleaseOptions):
    enabled_streams = tuple(stream.id for stream in release_profile.profile.streams if stream.enabled)
    return select_release_matrix(
        enabled_streams=enabled_streams,
        scenario_filters=release_options.scenarios,
        stream_filters=release_options.streams,
    )


@pytest.fixture(scope="session")
def release_artifacts(release_profile, release_options: ReleaseOptions):
    root = release_options.artifact_dir or Path(release_profile.profile.artifacts.root)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return ReleaseArtifacts.create(root=root, run_id=run_id)


BASELINE_MANAGER_SKIP_REASON = "release baseline manager requires a live Kubernetes discovery client"


@pytest.fixture(scope="session")
def baseline_manager():
    pytest.skip(BASELINE_MANAGER_SKIP_REASON)
