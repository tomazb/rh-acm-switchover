"""Semantic path-safety properties shared by the Python and collection forms."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ansible_collections.tomazb.acm_switchover.plugins.module_utils import path_safety as collection_path_safety
from lib import path_safety as python_path_safety
from lib.exceptions import SecurityValidationError as PythonSecurityValidationError
from lib.exceptions import ValidationError as PythonValidationError
from tests.properties.strategies import (
    UNSAFE_PATH_CHARS,
    artifact_relative_paths,
    broad_path_syntax_candidates,
    filesystem_resolvable_relative_paths,
    missing_descendant_suffixes,
    safe_relative_paths,
    traversal_path_candidates,
    unsafe_metacharacter_paths,
)

ARTIFACT_MARKER = ".artifact-path-check"
OUT_OF_POLICY_PATH = "/etc/acm-switchover-pbt-04-do-not-create"


@dataclass(frozen=True)
class ValidationOutcome:
    """Accepted/rejected classification with actionable diagnostic detail."""

    accepted: bool
    detail: str


def _classify_python(operation: Callable[[], object]) -> ValidationOutcome:
    """Classify only documented Python validation failures."""
    try:
        result = operation()
    except PythonSecurityValidationError as exc:
        return ValidationOutcome(False, f"{type(exc).__name__}: {exc}")
    except PythonValidationError as exc:
        return ValidationOutcome(False, f"{type(exc).__name__}: {exc}")
    return ValidationOutcome(True, f"returned {result!r}")


def _classify_collection(operation: Callable[[], object]) -> ValidationOutcome:
    """Classify only the collection's documented validation failure."""
    try:
        result = operation()
    except collection_path_safety.ValidationError as exc:
        return ValidationOutcome(False, f"{type(exc).__name__}: {exc}")
    return ValidationOutcome(True, f"returned {result!r}")


def _assert_agreement(
    candidate: str,
    python_outcome: ValidationOutcome,
    collection_outcome: ValidationOutcome,
) -> None:
    assert python_outcome.accepted is collection_outcome.accepted, (
        f"path classification drift for {candidate!r}: "
        f"Python={python_outcome.detail}; collection={collection_outcome.detail}"
    )


def _assert_both_reject(
    candidate: str,
    python_outcome: ValidationOutcome,
    collection_outcome: ValidationOutcome,
) -> None:
    _assert_agreement(candidate, python_outcome, collection_outcome)
    assert not python_outcome.accepted, (
        f"unsafe path {candidate!r} was accepted: "
        f"Python={python_outcome.detail}; collection={collection_outcome.detail}"
    )


def _is_within(path: Path, root: Path) -> bool:
    """Return whether ``path`` is beneath ``root`` without using production helpers."""
    try:
        return os.path.commonpath((str(path), str(root))) == str(root)
    except ValueError:
        return False


def _modeled_safe_roots() -> tuple[Path, ...]:
    """Independently model the documented general absolute-path roots."""
    roots = (Path("/tmp"), Path("/var"), Path.cwd(), Path.home())
    return tuple(dict.fromkeys(root.resolve() for root in roots))


def _selected_absolute_artifact_root(path: Path) -> Path:
    """Independently select the longest documented root containing ``path``."""
    assert path.is_absolute(), f"artifact root selection requires an absolute path: {path}"
    lexical_path = path.absolute()
    matching = [root for root in _modeled_safe_roots() if _is_within(lexical_path, root)]
    assert matching, f"test candidate has no modeled safe root: {path}"
    return max(matching, key=lambda root: len(str(root)))


def _resolved_artifact_path(candidate: str, relative_root: Path) -> tuple[Path, Path]:
    path = Path(candidate)
    if path.is_absolute():
        root = _selected_absolute_artifact_root(path)
        absolute_path = path
    else:
        root = relative_root.resolve()
        absolute_path = root / path
    return absolute_path.resolve(strict=False), root


def _symlink_or_skip(link: Path, target: Path, *, target_is_directory: bool) -> None:
    """Create one symlink or skip only when the host cannot create symlinks."""
    try:
        link.symlink_to(target, target_is_directory=target_is_directory)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"host does not support this symlink fixture: {exc}")


@pytest.mark.property
def test_unsafe_path_character_domains_match_strategy() -> None:
    """Keep the generated unsafe domain aligned with both shipped implementations."""
    strategy_domain = set(UNSAFE_PATH_CHARS)
    python_domain = set(python_path_safety.UNSAFE_PATH_CHARS)
    collection_domain = set(collection_path_safety.UNSAFE_PATH_CHARS)

    assert python_domain == collection_domain == strategy_domain, (
        "unsafe path-character domains drifted: "
        f"Python={sorted(python_domain)}, collection={sorted(collection_domain)}, "
        f"strategy={sorted(strategy_domain)}"
    )


@pytest.mark.property
@given(broad_path_syntax_candidates())
def test_path_syntax_classification_agrees_and_is_total(candidate: str) -> None:
    """Syntax gates agree even for values that host filesystem APIs cannot represent."""
    python_outcome = _classify_python(lambda: python_path_safety.validate_path_syntax(candidate, "property"))
    collection_outcome = _classify_collection(lambda: collection_path_safety.validate_path_syntax(candidate))

    _assert_agreement(candidate, python_outcome, collection_outcome)


@pytest.mark.property
@given(traversal_path_candidates())
def test_exact_traversal_components_are_rejected(candidate: str) -> None:
    assert ".." in candidate.split("/"), f"strategy did not generate an exact traversal component: {candidate!r}"
    python_outcome = _classify_python(lambda: python_path_safety.validate_path_syntax(candidate, "property"))
    collection_outcome = _classify_collection(lambda: collection_path_safety.validate_path_syntax(candidate))

    _assert_both_reject(candidate, python_outcome, collection_outcome)


@pytest.mark.property
@given(unsafe_metacharacter_paths())
def test_shell_metacharacters_are_rejected(candidate: str) -> None:
    assert ".." not in candidate.split("/"), f"unsafe-character candidate also contains traversal: {candidate!r}"
    assert any(
        char in candidate for char in UNSAFE_PATH_CHARS
    ), f"strategy did not generate a shipped unsafe character: {candidate!r}"
    python_outcome = _classify_python(lambda: python_path_safety.validate_path_syntax(candidate, "property"))
    collection_outcome = _classify_collection(lambda: collection_path_safety.validate_path_syntax(candidate))

    _assert_both_reject(candidate, python_outcome, collection_outcome)


@pytest.mark.property
@given(filesystem_resolvable_relative_paths())
def test_general_relative_path_classification_agrees(candidate: str) -> None:
    """General relative paths receive syntax checks, not filesystem containment checks."""
    python_outcome = _classify_python(lambda: python_path_safety.validate_safe_filesystem_path(candidate, "property"))
    collection_outcome = _classify_collection(lambda: collection_path_safety.validate_safe_path(candidate))

    _assert_agreement(candidate, python_outcome, collection_outcome)


@pytest.mark.property
@given(safe_relative_paths())
def test_safe_relative_general_paths_are_accepted(candidate: str) -> None:
    """Safe relative general paths are intentionally governed by syntax alone."""
    outcomes = (
        _classify_python(lambda: python_path_safety.validate_path_syntax(candidate, "property")),
        _classify_collection(lambda: collection_path_safety.validate_path_syntax(candidate)),
        _classify_python(lambda: python_path_safety.validate_safe_filesystem_path(candidate, "property")),
        _classify_collection(lambda: collection_path_safety.validate_safe_path(candidate)),
    )

    assert all(
        outcome.accepted for outcome in outcomes
    ), f"safe relative path {candidate!r} was rejected: " + "; ".join(outcome.detail for outcome in outcomes)


@pytest.mark.property
def test_general_relative_parent_symlink_is_syntax_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Characterize current behavior without reading or writing through the symlink."""
    workspace = tmp_path / "workspace"
    outside_workspace = tmp_path / "outside-workspace"
    workspace.mkdir()
    outside_workspace.mkdir()
    _symlink_or_skip(workspace / "escape", outside_workspace, target_is_directory=True)
    monkeypatch.chdir(workspace)
    candidate = "escape/etc-passwd"

    python_outcome = _classify_python(lambda: python_path_safety.validate_safe_filesystem_path(candidate, "property"))
    collection_outcome = _classify_collection(lambda: collection_path_safety.validate_safe_path(candidate))

    _assert_agreement(candidate, python_outcome, collection_outcome)
    assert python_outcome.accepted, (
        "general relative validation unexpectedly followed the symlink; the current contract is syntax-only: "
        f"Python={python_outcome.detail}; collection={collection_outcome.detail}"
    )
    assert not (outside_workspace / "etc-passwd").exists()


@pytest.mark.property
def test_general_absolute_existing_and_missing_paths_stay_in_safe_roots(tmp_path: Path) -> None:
    existing_root = tmp_path / "existing-root"
    missing_root = tmp_path / "missing-root"
    existing_root.mkdir()
    missing_root.mkdir()

    @given(safe_relative_paths(), missing_descendant_suffixes())
    def check(existing_relative: str, missing_suffix: str) -> None:
        existing_path = existing_root / existing_relative
        existing_path.mkdir(parents=True, exist_ok=True)
        missing_path = missing_root / missing_suffix

        for candidate in (existing_path, missing_path):
            candidate_text = str(candidate)
            python_outcome = _classify_python(
                lambda: python_path_safety.validate_safe_filesystem_path(candidate_text, "property")
            )
            collection_outcome = _classify_collection(lambda: collection_path_safety.validate_safe_path(candidate_text))
            _assert_agreement(candidate_text, python_outcome, collection_outcome)
            assert python_outcome.accepted, (
                f"safe absolute path {candidate_text!r} was rejected: "
                f"Python={python_outcome.detail}; collection={collection_outcome.detail}"
            )

            resolved = candidate.resolve(strict=False)
            roots = _modeled_safe_roots()
            assert any(_is_within(resolved, root) for root in roots), (
                f"accepted absolute path resolved outside modeled safe roots: "
                f"candidate={candidate_text!r}, resolved={resolved}, roots={roots}"
            )

    check()


@pytest.mark.property
def test_out_of_policy_absolute_path_is_controlled_rejection() -> None:
    python_outcome = _classify_python(
        lambda: python_path_safety.validate_safe_filesystem_path(OUT_OF_POLICY_PATH, "property")
    )
    collection_outcome = _classify_collection(lambda: collection_path_safety.validate_safe_path(OUT_OF_POLICY_PATH))

    _assert_both_reject(OUT_OF_POLICY_PATH, python_outcome, collection_outcome)


@pytest.mark.property
def test_general_absolute_symlinks_follow_resolved_safe_root_policy(tmp_path: Path) -> None:
    safe_target = tmp_path / "safe-target"
    safe_target.mkdir()
    safe_link = tmp_path / "safe-link"
    outside_link = tmp_path / "outside-link"
    _symlink_or_skip(safe_link, safe_target, target_is_directory=True)
    _symlink_or_skip(outside_link, Path("/etc"), target_is_directory=True)

    safe_candidate = str(safe_link / "state.json")
    outside_candidate = str(outside_link / "acm-switchover-pbt-04-do-not-create")
    safe_python = _classify_python(lambda: python_path_safety.validate_safe_filesystem_path(safe_candidate, "property"))
    safe_collection = _classify_collection(lambda: collection_path_safety.validate_safe_path(safe_candidate))
    outside_python = _classify_python(
        lambda: python_path_safety.validate_safe_filesystem_path(outside_candidate, "property")
    )
    outside_collection = _classify_collection(lambda: collection_path_safety.validate_safe_path(outside_candidate))

    _assert_agreement(safe_candidate, safe_python, safe_collection)
    assert (
        safe_python.accepted
    ), f"in-root absolute symlink was rejected: Python={safe_python.detail}; collection={safe_collection.detail}"
    safe_resolved = Path(safe_candidate).resolve(strict=False)
    assert any(
        _is_within(safe_resolved, root) for root in _modeled_safe_roots()
    ), f"accepted symlink target escaped all modeled safe roots: {safe_resolved}"
    _assert_both_reject(outside_candidate, outside_python, outside_collection)


@pytest.mark.property
def test_artifact_paths_agree_and_accepted_paths_are_contained(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    @given(
        st.one_of(filesystem_resolvable_relative_paths(), artifact_relative_paths()),
        st.booleans(),
    )
    def check(relative_candidate: str, absolute: bool) -> None:
        candidate = str(tmp_path / "absolute-artifacts" / relative_candidate) if absolute else relative_candidate
        python_outcome = _classify_python(
            lambda: python_path_safety.validate_report_artifact_path(candidate, "property artifact")
        )
        collection_outcome = _classify_collection(
            lambda: collection_path_safety.validate_report_artifact_path(candidate)
        )

        _assert_agreement(candidate, python_outcome, collection_outcome)
        if python_outcome.accepted:
            resolved, root = _resolved_artifact_path(candidate, tmp_path)
            assert _is_within(resolved, root), (
                f"accepted artifact escaped its selected root: "
                f"candidate={candidate!r}, resolved={resolved}, root={root}, "
                f"Python={python_outcome.detail}; collection={collection_outcome.detail}"
            )

    check()


@pytest.mark.property
def test_artifact_directory_matches_marker_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    @given(filesystem_resolvable_relative_paths(), st.booleans())
    def check(relative_candidate: str, absolute: bool) -> None:
        candidate = str(tmp_path / "directory-candidates" / relative_candidate) if absolute else relative_candidate
        marker_path = str(Path(candidate) / ARTIFACT_MARKER)
        python_directory = _classify_python(
            lambda: python_path_safety.validate_report_artifact_directory(candidate, "property directory")
        )
        python_marker = _classify_python(
            lambda: python_path_safety.validate_report_artifact_path(marker_path, "property directory")
        )
        collection_directory = _classify_collection(
            lambda: collection_path_safety.validate_report_artifact_directory(candidate)
        )
        collection_marker = _classify_collection(
            lambda: collection_path_safety.validate_report_artifact_path(marker_path)
        )

        assert python_directory.accepted is python_marker.accepted, (
            f"Python directory/marker classification differs for {candidate!r}: "
            f"directory={python_directory.detail}; marker={python_marker.detail}"
        )
        assert collection_directory.accepted is collection_marker.accepted, (
            f"collection directory/marker classification differs for {candidate!r}: "
            f"directory={collection_directory.detail}; marker={collection_marker.detail}"
        )
        _assert_agreement(candidate, python_directory, collection_directory)

    check()


@pytest.mark.property
@pytest.mark.parametrize("absolute", (False, True), ids=("relative", "absolute"))
def test_final_artifact_symlink_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    absolute: bool,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "real-report.json"
    target.write_text("{}\n", encoding="utf-8")
    link = workspace / "report-link.json"
    _symlink_or_skip(link, target, target_is_directory=False)
    monkeypatch.chdir(workspace)
    candidate = str(link) if absolute else link.name

    python_outcome = _classify_python(
        lambda: python_path_safety.validate_report_artifact_path(candidate, "property artifact")
    )
    collection_outcome = _classify_collection(lambda: collection_path_safety.validate_report_artifact_path(candidate))

    _assert_both_reject(candidate, python_outcome, collection_outcome)


@pytest.mark.property
@pytest.mark.parametrize("absolute", (False, True), ids=("relative", "absolute"))
def test_artifact_parent_symlink_escape_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    absolute: bool,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    escape_link = workspace / "escape"
    _symlink_or_skip(escape_link, Path("/etc"), target_is_directory=True)
    monkeypatch.chdir(workspace)
    candidate_path = escape_link / "acm-switchover-pbt-04-do-not-create.json"
    candidate = str(candidate_path) if absolute else str(candidate_path.relative_to(workspace))

    python_outcome = _classify_python(
        lambda: python_path_safety.validate_report_artifact_path(candidate, "property artifact")
    )
    collection_outcome = _classify_collection(lambda: collection_path_safety.validate_report_artifact_path(candidate))

    _assert_both_reject(candidate, python_outcome, collection_outcome)


@pytest.mark.property
@pytest.mark.parametrize("absolute", (False, True), ids=("relative", "absolute"))
def test_safe_in_root_artifact_parent_symlink_is_accepted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    absolute: bool,
) -> None:
    workspace = tmp_path / "workspace"
    real_directory = workspace / "real-directory"
    real_directory.mkdir(parents=True)
    linked_directory = workspace / "linked-directory"
    _symlink_or_skip(linked_directory, real_directory, target_is_directory=True)
    monkeypatch.chdir(workspace)
    candidate_path = linked_directory / "report.json"
    candidate = str(candidate_path) if absolute else str(candidate_path.relative_to(workspace))

    python_outcome = _classify_python(
        lambda: python_path_safety.validate_report_artifact_path(candidate, "property artifact")
    )
    collection_outcome = _classify_collection(lambda: collection_path_safety.validate_report_artifact_path(candidate))

    _assert_agreement(candidate, python_outcome, collection_outcome)
    assert python_outcome.accepted, (
        f"safe in-root artifact symlink parent was rejected: "
        f"Python={python_outcome.detail}; collection={collection_outcome.detail}"
    )
    resolved, root = _resolved_artifact_path(candidate, workspace)
    assert _is_within(
        resolved, root
    ), f"accepted in-root artifact symlink escaped containment: candidate={candidate!r}, resolved={resolved}, root={root}"
