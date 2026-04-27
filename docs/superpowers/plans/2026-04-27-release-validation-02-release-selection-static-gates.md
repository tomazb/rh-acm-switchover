# Release Validation Selection And Static Gates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add pytest option handling, release modes, selected matrix filtering/hashing, release metadata checks, artifact reuse preflight checks, and fail-closed static gate execution.

**Architecture:** Keep pytest integration in `tests/release/conftest.py` and put reusable selection/static gate logic under `tests/release/checks/` and `tests/release/scenarios/`. Static gates run as subprocesses and return structured records; they must never hide non-zero return codes.

**Tech Stack:** pytest hooks, dataclasses, subprocess, hashlib, json, pathlib, existing profile contract package.

---

## File Map

- Create: `tests/release/conftest.py` for release CLI options, skip behavior, and session fixtures.
- Create: `tests/release/scenarios/catalog.py` for V1 scenario definitions and matrix selection.
- Create: `tests/release/checks/static_gates.py` for gate command definitions and subprocess execution.
- Create: `tests/release/checks/metadata.py` for release metadata consistency checks.
- Create: `tests/release/checks/artifact_reuse.py` for manifest compatibility checks.
- Create: `tests/release/test_release_certification.py` with a single lifecycle-owning release test.
- Create: `tests/release/test_options.py`
- Create: `tests/release/scenarios/test_catalog.py`
- Create: `tests/release/checks/test_static_gates.py`
- Create: `tests/release/checks/test_metadata.py`
- Create: `tests/release/checks/test_artifact_reuse.py`
- Modify: `docs/superpowers/plans/2026-04-27-release-validation-progress.md`

## Task 1: Pytest Options And No-Profile Skip

**Files:**
- Create: `tests/release/conftest.py`
- Create: `tests/release/test_release_certification.py`
- Create: `tests/release/test_options.py`

- [ ] **Step 1: Add tests for option defaults and skip behavior**

```python
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
```

If `pytester` is not enabled in this repo, add `pytest_plugins = ("pytester",)` at the top of this test file.

- [ ] **Step 2: Run the option tests and confirm they fail**

Run: `python -m pytest tests/release/test_options.py -q`

Expected: failure because release options and hooks are missing.

- [ ] **Step 3: Implement release options and marker registration**

```python
# tests/release/conftest.py
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import pytest


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
    group.addoption("--release-mode", action="store", choices=("certification", "focused-rerun", "debug"), default=None)
    group.addoption("--release-scenario", action="append", default=[])
    group.addoption("--release-stream", action="append", choices=("bash", "python", "ansible"), default=[])
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
    *, explicit_mode: str | None, scenario_filters: tuple[str, ...], stream_filters: tuple[str, ...]
) -> str:
    if explicit_mode:
        return explicit_mode
    return "focused-rerun" if scenario_filters or stream_filters else "certification"


def pytest_collection_modifyitems(config: pytest.Config, items: Sequence[pytest.Item]) -> None:
    if not should_skip_release_items(profile_path=_profile_path(config)):
        return
    skip_release = pytest.mark.skip(reason=RELEASE_PROFILE_SKIP_REASON)
    for item in items:
        if "release" in item.keywords:
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
        resume_from_artifacts=Path(pytestconfig.getoption("--release-resume-from-artifacts"))
        if pytestconfig.getoption("--release-resume-from-artifacts")
        else None,
        rerun_from_artifacts=Path(pytestconfig.getoption("--release-rerun-from-artifacts"))
        if pytestconfig.getoption("--release-rerun-from-artifacts")
        else None,
        artifact_dir=Path(pytestconfig.getoption("--release-artifact-dir"))
        if pytestconfig.getoption("--release-artifact-dir")
        else None,
        allow_dirty=bool(pytestconfig.getoption("--allow-dirty")),
    )
```

```python
# tests/release/test_release_certification.py
from __future__ import annotations

import pytest


@pytest.mark.release
def test_release_certification(release_options) -> None:
    assert release_options.profile_path is not None
```

- [ ] **Step 4: Run the option tests**

Run: `python -m pytest tests/release/test_options.py tests/release/test_release_certification.py -q`

Expected: option tests pass and the lifecycle test is skipped when no profile is supplied.

- [ ] **Step 5: Commit pytest option handling**

```bash
git add tests/release/conftest.py tests/release/test_release_certification.py tests/release/test_options.py
git commit -m "feat: add release pytest options"
```

## Task 2: Scenario Catalog, Filters, And Matrix Hash

**Files:**
- Create: `tests/release/scenarios/catalog.py`
- Create: `tests/release/scenarios/test_catalog.py`
- Modify: `tests/release/conftest.py`

- [ ] **Step 1: Add catalog tests for required order and filter expansion**

```python
import pytest

from tests.release.scenarios.catalog import select_release_matrix


def test_full_matrix_contains_required_scenarios_in_order() -> None:
    selected = select_release_matrix(enabled_streams=("python", "ansible"), scenario_filters=(), stream_filters=())

    assert [item.id for item in selected.scenarios[:4]] == [
        "static-gates",
        "lab-readiness",
        "baseline-check",
        "preflight",
    ]
    assert "runtime-parity" in selected.scenario_ids
    assert "final-baseline-check" in selected.scenario_ids
    assert len(selected.matrix_hash) == 64


def test_mutating_filter_adds_prerequisites_and_final_checks() -> None:
    selected = select_release_matrix(
        enabled_streams=("python", "ansible"),
        scenario_filters=("python-passive-switchover",),
        stream_filters=(),
    )

    assert selected.scenario_ids == (
        "static-gates",
        "lab-readiness",
        "baseline-check",
        "python-passive-switchover",
        "runtime-parity",
        "final-baseline-check",
    )


def test_unknown_scenario_fails_before_mutation() -> None:
    with pytest.raises(ValueError, match="unknown release scenario"):
        select_release_matrix(enabled_streams=("python",), scenario_filters=("missing",), stream_filters=())
```

- [ ] **Step 2: Run catalog tests and confirm they fail**

Run: `python -m pytest tests/release/scenarios/test_catalog.py -q`

Expected: import failure for missing catalog.

- [ ] **Step 3: Implement catalog selection**

```python
# tests/release/scenarios/catalog.py
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass


@dataclass(frozen=True)
class ScenarioDefinition:
    id: str
    required: bool
    streams: tuple[str, ...]
    mutates_lab: bool
    runtime_parity_required: bool


@dataclass(frozen=True)
class SelectedReleaseMatrix:
    scenarios: tuple[ScenarioDefinition, ...]
    selected_streams: tuple[str, ...]
    matrix_hash: str

    @property
    def scenario_ids(self) -> tuple[str, ...]:
        return tuple(item.id for item in self.scenarios)


V1_SCENARIOS: tuple[ScenarioDefinition, ...] = (
    ScenarioDefinition("static-gates", True, ("local",), False, False),
    ScenarioDefinition("lab-readiness", True, ("local",), False, False),
    ScenarioDefinition("baseline-check", True, ("local",), False, False),
    ScenarioDefinition("preflight", True, ("bash", "python", "ansible"), False, True),
    ScenarioDefinition("python-passive-switchover", True, ("python",), True, True),
    ScenarioDefinition("ansible-passive-switchover", True, ("ansible",), True, True),
    ScenarioDefinition("python-restore-only", True, ("python",), True, True),
    ScenarioDefinition("ansible-restore-only", True, ("ansible",), True, True),
    ScenarioDefinition("argocd-managed-switchover", True, ("python", "ansible"), True, True),
    ScenarioDefinition("runtime-parity", True, ("local",), False, True),
    ScenarioDefinition("final-baseline-check", True, ("local",), False, False),
    ScenarioDefinition("full-restore", False, ("python", "ansible"), True, True),
    ScenarioDefinition("checkpoint-resume", False, ("python", "ansible"), True, True),
    ScenarioDefinition("decommission", False, ("python", "ansible"), True, True),
    ScenarioDefinition("failure-injection", False, ("python", "ansible"), True, False),
    ScenarioDefinition("soak", False, ("python", "ansible"), True, True),
)
SCENARIOS_BY_ID = {item.id: item for item in V1_SCENARIOS}
PREREQUISITES = ("static-gates", "lab-readiness", "baseline-check")
POST_MUTATION = ("runtime-parity", "final-baseline-check")


def _hash_matrix(scenario_ids: tuple[str, ...], selected_streams: tuple[str, ...]) -> str:
    payload = json.dumps({"scenarios": scenario_ids, "streams": selected_streams}, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def select_release_matrix(
    *,
    enabled_streams: tuple[str, ...],
    scenario_filters: tuple[str, ...],
    stream_filters: tuple[str, ...],
) -> SelectedReleaseMatrix:
    unknown = [item for item in scenario_filters if item not in SCENARIOS_BY_ID]
    if unknown:
        raise ValueError(f"unknown release scenario: {unknown[0]}")
    selected_streams = tuple(stream for stream in enabled_streams if not stream_filters or stream in stream_filters)
    if scenario_filters:
        requested = tuple(dict.fromkeys(scenario_filters))
        mutating = any(SCENARIOS_BY_ID[item].mutates_lab for item in requested)
        scenario_ids = PREREQUISITES + requested + (POST_MUTATION if mutating else ())
        scenario_ids = tuple(dict.fromkeys(scenario_ids))
    else:
        scenario_ids = tuple(item.id for item in V1_SCENARIOS if item.required)
    scenarios = tuple(SCENARIOS_BY_ID[item] for item in scenario_ids)
    return SelectedReleaseMatrix(
        scenarios=scenarios,
        selected_streams=selected_streams,
        matrix_hash=_hash_matrix(scenario_ids, selected_streams),
    )
```

- [ ] **Step 4: Add session fixture wiring**

```python
# tests/release/conftest.py
from tests.release.contracts import load_profile
from tests.release.scenarios.catalog import select_release_matrix


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
```

- [ ] **Step 5: Run catalog tests**

Run: `python -m pytest tests/release/scenarios/test_catalog.py -q`

Expected: `3 passed`.

- [ ] **Step 6: Commit catalog selection**

```bash
git add tests/release/scenarios/catalog.py tests/release/scenarios/test_catalog.py tests/release/conftest.py
git commit -m "feat: select release scenario matrix"
```

## Task 3: Static Gate Command Runner

**Files:**
- Create: `tests/release/checks/static_gates.py`
- Create: `tests/release/checks/test_static_gates.py`

- [ ] **Step 1: Add subprocess behavior tests**

```python
from pathlib import Path

from tests.release.checks.static_gates import GateCommand, run_gate_command


def test_run_gate_command_records_returncode_and_output(tmp_path: Path) -> None:
    result = run_gate_command(
        GateCommand(gate_id="sample", label="python-version", command=["python", "-c", "print('ok')"], cwd=Path.cwd()),
        artifact_dir=tmp_path,
    )

    assert result.gate_id == "sample"
    assert result.status == "passed"
    assert result.returncode == 0
    assert Path(result.stdout_path).read_text(encoding="utf-8").strip() == "ok"


def test_run_gate_command_fails_on_nonzero_return(tmp_path: Path) -> None:
    result = run_gate_command(
        GateCommand(gate_id="sample", label="bad", command=["python", "-c", "import sys; sys.exit(7)"], cwd=Path.cwd()),
        artifact_dir=tmp_path,
    )

    assert result.status == "failed"
    assert result.returncode == 7
```

- [ ] **Step 2: Run static gate tests and confirm they fail**

Run: `python -m pytest tests/release/checks/test_static_gates.py -q`

Expected: import failure for missing module.

- [ ] **Step 3: Implement gate runner**

```python
# tests/release/checks/static_gates.py
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GateCommand:
    gate_id: str
    label: str
    command: list[str]
    cwd: Path
    required: bool = True


@dataclass(frozen=True)
class GateResult:
    gate_id: str
    label: str
    command: list[str]
    returncode: int
    status: str
    stdout_path: str
    stderr_path: str
    required: bool


def run_gate_command(command: GateCommand, artifact_dir: Path) -> GateResult:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(command.command, cwd=command.cwd, text=True, capture_output=True, check=False)
    stdout_path = artifact_dir / f"{command.gate_id}-{command.label}.stdout"
    stderr_path = artifact_dir / f"{command.gate_id}-{command.label}.stderr"
    stdout_path.write_text(completed.stdout, encoding="utf-8")
    stderr_path.write_text(completed.stderr, encoding="utf-8")
    return GateResult(
        gate_id=command.gate_id,
        label=command.label,
        command=command.command,
        returncode=completed.returncode,
        status="passed" if completed.returncode == 0 else "failed",
        stdout_path=str(stdout_path),
        stderr_path=str(stderr_path),
        required=command.required,
    )
```

- [ ] **Step 4: Run static gate tests**

Run: `python -m pytest tests/release/checks/test_static_gates.py -q`

Expected: `2 passed`.

- [ ] **Step 5: Commit gate runner**

```bash
git add tests/release/checks/static_gates.py tests/release/checks/test_static_gates.py
git commit -m "feat: execute release static gates"
```

## Task 4: Default Static Gate Definitions

**Files:**
- Modify: `tests/release/checks/static_gates.py`
- Modify: `tests/release/checks/test_static_gates.py`

- [ ] **Step 1: Add definition tests**

```python
from tests.release.checks.static_gates import build_default_gate_commands


def test_python_and_ansible_streams_enable_expected_gate_ids() -> None:
    gates = build_default_gate_commands(enabled_streams=("python", "ansible"), repo_root=Path("/repo"))
    gate_ids = {gate.gate_id for gate in gates}

    assert "root-non-e2e-tests" in gate_ids
    assert "static-parity-tests" in gate_ids
    assert "python-cli-smoke" in gate_ids
    assert "collection-build-install" in gate_ids
    assert "collection-playbook-syntax" in gate_ids


def test_bash_only_profile_still_runs_local_root_gate() -> None:
    gates = build_default_gate_commands(enabled_streams=("bash",), repo_root=Path("/repo"))
    assert [gate.gate_id for gate in gates] == ["root-non-e2e-tests"]
```

- [ ] **Step 2: Run definition tests and confirm they fail**

Run: `python -m pytest tests/release/checks/test_static_gates.py::test_python_and_ansible_streams_enable_expected_gate_ids tests/release/checks/test_static_gates.py::test_bash_only_profile_still_runs_local_root_gate -q`

Expected: import failure for missing function.

- [ ] **Step 3: Implement command definition builder**

```python
def build_default_gate_commands(*, enabled_streams: tuple[str, ...], repo_root: Path) -> list[GateCommand]:
    gates = [
        GateCommand(
            gate_id="root-non-e2e-tests",
            label="pytest-root",
            command=["python", "-m", "pytest", "tests/", "-m", "not e2e and not release"],
            cwd=repo_root,
        )
    ]
    if {"python", "ansible"}.issubset(set(enabled_streams)):
        gates.append(
            GateCommand(
                gate_id="static-parity-tests",
                label="pytest-parity",
                command=[
                    "python",
                    "-m",
                    "pytest",
                    "tests/test_constants_parity.py",
                    "tests/test_rbac_collection_parity.py",
                    "tests/test_argocd_constants_parity.py",
                ],
                cwd=repo_root,
            )
        )
    if "python" in enabled_streams:
        gates.extend(
            [
                GateCommand("python-style-security-gates", "black", ["black", "--check", "--line-length", "120", "acm_switchover.py", "lib/", "modules/"], repo_root),
                GateCommand("python-style-security-gates", "isort", ["isort", "--check-only", "--profile", "black", "--line-length", "120", "acm_switchover.py", "lib/", "modules/"], repo_root),
                GateCommand("python-cli-smoke", "help", ["python", "acm_switchover.py", "--help"], repo_root),
            ]
        )
    if "ansible" in enabled_streams:
        collection_root = repo_root / "ansible_collections/tomazb/acm_switchover"
        gates.extend(
            [
                GateCommand("collection-ansible-test-sanity", "ansible-test-sanity", ["ansible-test", "sanity", "--docker", "default", "-v"], collection_root),
                GateCommand("collection-unit-tests", "pytest-collection-unit", ["python", "-m", "pytest", "tests/unit", "-q"], collection_root),
                GateCommand("collection-build-install", "ansible-galaxy-build", ["ansible-galaxy", "collection", "build", "--force"], collection_root),
                GateCommand("collection-playbook-syntax", "preflight", ["ansible-playbook", "--syntax-check", "playbooks/preflight.yml"], collection_root),
            ]
        )
    return gates
```

- [ ] **Step 4: Run definition tests**

Run: `python -m pytest tests/release/checks/test_static_gates.py::test_python_and_ansible_streams_enable_expected_gate_ids tests/release/checks/test_static_gates.py::test_bash_only_profile_still_runs_local_root_gate -q`

Expected: `2 passed`.

- [ ] **Step 5: Commit default gate definitions**

```bash
git add tests/release/checks/static_gates.py tests/release/checks/test_static_gates.py
git commit -m "feat: define release static gate commands"
```

## Task 5: Release Metadata And Artifact Reuse Validation

**Files:**
- Create: `tests/release/checks/metadata.py`
- Create: `tests/release/checks/artifact_reuse.py`
- Create: `tests/release/checks/test_metadata.py`
- Create: `tests/release/checks/test_artifact_reuse.py`

- [ ] **Step 1: Add metadata and reuse tests**

```python
from pathlib import Path

import pytest

from tests.release.checks.artifact_reuse import validate_artifact_reuse_manifest
from tests.release.checks.metadata import compute_release_metadata_hash


def test_metadata_hash_changes_when_authoritative_file_changes(tmp_path: Path) -> None:
    readme = tmp_path / "README.md"
    readme.write_text("version 1.0.0\n", encoding="utf-8")
    first = compute_release_metadata_hash(repo_root=tmp_path, metadata_files=("README.md",), profile_hash="a", matrix_hash="b")
    readme.write_text("version 1.0.1\n", encoding="utf-8")
    second = compute_release_metadata_hash(repo_root=tmp_path, metadata_files=("README.md",), profile_hash="a", matrix_hash="b")
    assert first != second


def test_reuse_manifest_requires_schema_version(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="schema_version"):
        validate_artifact_reuse_manifest(manifest, expected_profile_hash="p", expected_matrix_hash="m")
```

- [ ] **Step 2: Run tests and confirm they fail**

Run: `python -m pytest tests/release/checks/test_metadata.py tests/release/checks/test_artifact_reuse.py -q`

Expected: import failures.

- [ ] **Step 3: Implement metadata hashing and manifest compatibility**

```python
# tests/release/checks/metadata.py
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def compute_release_metadata_hash(
    *, repo_root: Path, metadata_files: tuple[str, ...], profile_hash: str, matrix_hash: str
) -> str:
    values = {"profile_hash": profile_hash, "matrix_hash": matrix_hash, "files": []}
    for relative_path in metadata_files:
        path = repo_root / relative_path
        values["files"].append({"path": relative_path, "content": path.read_text(encoding="utf-8") if path.exists() else ""})
    payload = json.dumps(values, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
```

```python
# tests/release/checks/artifact_reuse.py
from __future__ import annotations

import json
from pathlib import Path


def validate_artifact_reuse_manifest(
    manifest_path: Path, *, expected_profile_hash: str, expected_matrix_hash: str
) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1:
        raise ValueError(f"{manifest_path}: schema_version must be 1")
    if manifest.get("profile", {}).get("sha256") != expected_profile_hash:
        raise ValueError(f"{manifest_path}: profile.sha256 does not match active profile")
    if manifest.get("selected_matrix_hash") != expected_matrix_hash:
        raise ValueError(f"{manifest_path}: selected_matrix_hash does not match active matrix")
    return manifest
```

- [ ] **Step 4: Run metadata and reuse tests**

Run: `python -m pytest tests/release/checks/test_metadata.py tests/release/checks/test_artifact_reuse.py -q`

Expected: `2 passed`.

- [ ] **Step 5: Commit metadata and reuse checks**

```bash
git add tests/release/checks/metadata.py tests/release/checks/artifact_reuse.py tests/release/checks/test_metadata.py tests/release/checks/test_artifact_reuse.py
git commit -m "feat: validate release metadata and artifact reuse"
```

## Final Verification

- [ ] Run focused tests:

Run: `python -m pytest tests/release -k "options or catalog or static_gates or metadata or artifact_reuse" -q`

Expected: all selected tests pass, with release lifecycle tests skipped when no profile is supplied.

- [ ] Run default discovery guard:

Run: `python -m pytest tests/ -m "not e2e and not release" -q`

Expected: no release lifecycle test mutates a lab or requires a profile.

- [ ] Run the planning placeholder scan:

Run:

```bash
python - <<'PY'
from pathlib import Path
bad = ["TB" + "D", "TO" + "DO", "implement " + "later", "fill " + "in details", "handle " + "edge cases", "appropriate " + "error handling", "Similar " + "to Task"]
hits = []
for path in sorted(Path("docs/superpowers/plans").glob("*.md")):
    text = path.read_text(encoding="utf-8")
    for phrase in bad:
        if phrase in text:
            hits.append(f"{path}: contains rejected planning phrase {phrase!r}")
if hits:
    raise SystemExit("\n".join(hits))
PY
```

Expected: no matches.

- [ ] Update the progress tracker with status and verification evidence.
