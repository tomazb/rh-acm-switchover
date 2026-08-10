"""Guardrail tests for the collection compatibility contract (issue #244).

The supported combination of ``ansible-core``, ``kubernetes.core``, and Python is
declared in ``docs/compatibility.md``. This module is the executable form of that
document: it holds the policy once, in the constants below, and asserts that the
collection metadata, the execution-environment build input, the CI matrix, and
the document itself all agree with it.

Every constant here must change together with ``docs/compatibility.md``. A change
to one surface alone is the failure mode these tests exist to catch.
"""

import os
import re
from pathlib import Path

import pytest
import yaml
from packaging.specifiers import SpecifierSet
from packaging.version import Version

REPO_ROOT = Path(__file__).resolve().parents[5]
COLLECTION_ROOT = REPO_ROOT / "ansible_collections" / "tomazb" / "acm_switchover"
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "ansible-collection-foundation.yml"
COMPATIBILITY_DOC = COLLECTION_ROOT / "docs" / "compatibility.md"

# --- Policy: the single in-code statement of the supported matrix. -----------

REQUIRES_ANSIBLE = ">=2.16.0,<2.22"
KUBERNETES_CORE_CONSTRAINT = ">=6.0.0,<7.0.0"

# Lane name -> (pip specifier used by CI, representative version, control-node Python).
LANES = {
    "min": ("2.16.*", "2.16.0", "3.11"),
    "current": ("2.21.*", "2.21.0", "3.12"),
}

# Versions that must be outside the supported range, one on each side of it.
UNSUPPORTED_CORE_VERSIONS = ("2.15.13", "2.22.0")

# The collection workflow must run pre-merge and on the integration branch.
REQUIRED_PUSH_BRANCHES = {"main", "ansible"}

# Unmaintained since 2022; its stable-2.15-latest tag never existed.
ABANDONED_EE_BASE_IMAGE = "ansible/ansible-runner"


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


def _workflow_triggers(workflow: dict) -> dict:
    """Return the workflow's trigger mapping.

    PyYAML resolves the bare ``on:`` key to the boolean ``True`` under YAML 1.1,
    so both spellings have to be accepted.
    """
    triggers = workflow.get("on", workflow.get(True))
    assert isinstance(triggers, dict), "workflow triggers should parse as a mapping"
    return triggers


def _workflow_lanes(workflow: dict) -> dict:
    entries = workflow["jobs"]["foundation"]["strategy"]["matrix"]["include"]
    return {entry["lane"]: entry for entry in entries}


def test_runtime_yml_declares_the_policy_range():
    data = _load_yaml(COLLECTION_ROOT / "meta" / "runtime.yml")

    assert data["requires_ansible"] == REQUIRES_ANSIBLE


def test_requires_ansible_admits_every_lane_and_excludes_unsupported_cores():
    specifier = SpecifierSet(REQUIRES_ANSIBLE)

    mismatches = []
    for lane, (_pip_specifier, representative, _python) in LANES.items():
        if Version(representative) not in specifier:
            mismatches.append(f"lane {lane}: {representative} is not admitted by {REQUIRES_ANSIBLE}")
    for version in UNSUPPORTED_CORE_VERSIONS:
        if Version(version) in specifier:
            mismatches.append(f"{version} should be outside {REQUIRES_ANSIBLE}")

    assert not mismatches, "; ".join(mismatches)


def test_kubernetes_core_constraint_is_declared_identically_in_both_files():
    galaxy_data = _load_yaml(COLLECTION_ROOT / "galaxy.yml")
    requirements_data = _load_yaml(COLLECTION_ROOT / "requirements.yml")

    declared = {entry["name"]: entry["version"] for entry in requirements_data["collections"]}

    assert galaxy_data["dependencies"]["kubernetes.core"] == KUBERNETES_CORE_CONSTRAINT
    assert declared["kubernetes.core"] == KUBERNETES_CORE_CONSTRAINT


def test_execution_environment_is_buildable_and_matches_the_core_policy():
    data = _load_yaml(COLLECTION_ROOT / "execution-environment.yml")
    base_image = data["images"]["base_image"]["name"]
    dependencies = data["dependencies"]

    assert ABANDONED_EE_BASE_IMAGE not in base_image, (
        f"{base_image} is unmaintained and publishes no current tags; "
        "the execution environment cannot be built from it"
    )

    ee_specifier = SpecifierSet(dependencies["ansible_core"]["package_pip"].removeprefix("ansible-core"))
    mismatches = []
    for lane, (_pip_specifier, representative, _python) in LANES.items():
        if Version(representative) not in ee_specifier:
            mismatches.append(f"lane {lane}: EE pin excludes {representative}")
    for version in UNSUPPORTED_CORE_VERSIONS:
        if Version(version) in ee_specifier:
            mismatches.append(f"EE pin admits unsupported {version}")
    assert not mismatches, "; ".join(mismatches)

    for key in ("galaxy", "python", "system"):
        referenced = COLLECTION_ROOT / dependencies[key]
        assert referenced.is_file(), f"execution-environment.yml references missing {key} input {dependencies[key]}"


def test_ci_matrix_lanes_match_the_policy():
    lanes = _workflow_lanes(_load_yaml(WORKFLOW_PATH))

    assert set(lanes) == set(LANES), "workflow lanes and the documented lanes must be the same set"

    mismatches = []
    for lane, (pip_specifier, _representative, python) in LANES.items():
        if lanes[lane]["ansible_core"] != pip_specifier:
            mismatches.append(
                f"lane {lane}: workflow installs {lanes[lane]['ansible_core']}, policy says {pip_specifier}"
            )
        if str(lanes[lane]["python"]) != python:
            mismatches.append(f"lane {lane}: workflow uses Python {lanes[lane]['python']}, policy says {python}")

    assert not mismatches, "; ".join(mismatches)


def test_ci_runs_on_pull_requests_and_on_the_integration_branch():
    triggers = _workflow_triggers(_load_yaml(WORKFLOW_PATH))

    assert "pull_request" in triggers, "collection CI must gate pull requests"
    assert REQUIRED_PUSH_BRANCHES.issubset(set(triggers["push"]["branches"])), (
        "collection CI must also run post-merge on the integration branch; " f"found {triggers['push']['branches']}"
    )


def test_compatibility_document_states_the_same_matrix():
    """The document is the authority, so it must carry the values verbatim."""
    text = COMPATIBILITY_DOC.read_text()

    missing = [value for value in (REQUIRES_ANSIBLE, KUBERNETES_CORE_CONSTRAINT) if value not in text]
    for lane, (pip_specifier, _representative, python) in LANES.items():
        if not re.search(rf"\b{re.escape(lane)}\b", text):
            missing.append(f"lane name {lane}")
        if pip_specifier not in text:
            missing.append(f"lane specifier {pip_specifier}")
        if f"Python {python}" not in text and f"py{python}" not in text:
            missing.append(f"lane Python {python}")

    assert not missing, f"{COMPATIBILITY_DOC.name} does not state: {', '.join(missing)}"


def _installed_kubernetes_core_runtime() -> Path | None:
    search_roots = [Path(entry) for entry in os.environ.get("ANSIBLE_COLLECTIONS_PATH", "").split(os.pathsep) if entry]
    search_roots.append(Path.home() / ".ansible" / "collections")

    for root in search_roots:
        candidate = root / "ansible_collections" / "kubernetes" / "core" / "meta" / "runtime.yml"
        if candidate.is_file():
            return candidate
    return None


def _installed_ansible_core_version() -> str | None:
    try:
        from ansible.release import __version__  # type: ignore[import-not-found]
    except ImportError:
        return None
    return __version__


def test_resolved_kubernetes_core_supports_the_running_ansible_core():
    """Fail the lane that Ansible would only warn about.

    ``ansible-playbook`` prints ``Collection kubernetes.core does not support
    Ansible version X`` and carries on, so an incompatible resolution can pass CI
    unnoticed. This evaluates the same condition as an assertion, which also
    catches an upstream floor bump the moment it is resolved.
    """
    runtime_path = _installed_kubernetes_core_runtime()
    if runtime_path is None:
        pytest.skip("kubernetes.core is not installed; this check runs in the CI lanes")

    core_version = _installed_ansible_core_version()
    if core_version is None:
        pytest.skip("ansible-core is not installed; this check runs in the CI lanes")

    required = _load_yaml(runtime_path).get("requires_ansible")
    assert required, f"{runtime_path} declares no requires_ansible"

    assert Version(core_version) in SpecifierSet(required), (
        f"resolved kubernetes.core requires ansible-core {required}, "
        f"but this lane runs {core_version}; the declared range is {REQUIRES_ANSIBLE}"
    )
