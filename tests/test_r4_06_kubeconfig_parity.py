"""R4-06 PR 2 parity: the single-file kubeconfig contract and the duplicate rule agree.

Python validates a whole KUBECONFIG chain and the collection one mapped file per cluster
(amendment §1 Decision 1); on one file both must reach the same result for every vector in
tests/fixtures/r4_06_kubeconfig_vectors.json (amendment §5).
"""

import json
import pathlib
import re
import time

import pytest

from ansible_collections.tomazb.acm_switchover.plugins.module_utils.kubeconfig_file import (
    KubeconfigFileError,
    load_kubeconfig_file,
)
from lib.exceptions import SwitchoverError
from lib.kubeconfig_merge import load_merged_kubeconfig

VECTORS = json.loads((pathlib.Path(__file__).parent / "fixtures" / "r4_06_kubeconfig_vectors.json").read_text())
LIMIT = 1024 * 1024


def _python(path):
    try:
        merged = load_merged_kubeconfig(str(path), LIMIT)
    except SwitchoverError as exc:
        match = re.match(r"(kubeconfig_load_failed \([a-z_]+\)|kubeconfig_duplicate_conflict)", str(exc))
        assert match, str(exc)
        return match.group(1), None
    return "ok", {
        section: [entry["name"] for entry in merged[section]] for section in ("clusters", "contexts", "users")
    }


def _collection(path):
    try:
        document = load_kubeconfig_file(str(path), LIMIT)
    except KubeconfigFileError as exc:
        return exc.reason, None
    return "ok", {
        section: [entry["name"] for entry in document[section]] for section in ("clusters", "contexts", "users")
    }


@pytest.mark.parametrize("row", VECTORS["single_file"], ids=lambda row: row["case"])
@pytest.mark.parametrize("load", [_python, _collection], ids=["python", "collection"])
def test_single_file_vector(load, row, tmp_path):
    path = tmp_path / "kubeconfig"
    path.write_text(row["yaml"])
    result, names = load(path)
    assert result == row["result"]
    if row["result"] == "ok":
        assert names == row["names"]


@pytest.mark.parametrize("row", VECTORS["single_file"], ids=lambda row: row["case"])
def test_failures_never_carry_file_content(row, tmp_path):
    path = tmp_path / "kubeconfig"
    path.write_text(row["yaml"])
    for load in (load_merged_kubeconfig, load_kubeconfig_file):
        try:
            load(str(path), LIMIT)
        except (SwitchoverError, KubeconfigFileError) as exc:
            assert "https://" not in str(exc)
            assert exc.__context__ is None


@pytest.mark.parametrize("load", [_python, _collection], ids=["python", "collection"])
def test_nested_shared_aliases_load_in_linear_time(load, tmp_path):
    """Codex PR 2 review: each alias level doubled the work; memoization keeps it linear."""
    lines = ["a0: &a0 [x, y]"]
    for level in range(1, 30):
        lines.append(f"a{level}: &a{level} [*a{level - 1}, *a{level - 1}]")
    lines.append("users:\n- {name: u, user: {deep: *a29}}\n- {name: u, user: {deep: *a29}}")
    path = tmp_path / "kubeconfig"
    path.write_text("\n".join(lines) + "\n")

    started = time.monotonic()
    result, names = load(path)

    assert result == "ok" and names["users"] == ["u"]
    assert time.monotonic() - started < 5


@pytest.mark.parametrize("load", [_python, _collection], ids=["python", "collection"])
def test_independent_identical_chains_compare_in_linear_time(load, tmp_path):
    """Two separately built anchor chains with equal content are identical without full expansion."""
    lines = []
    for chain in ("a", "b"):
        lines.append(f"{chain}0: &{chain}0 [x, y]")
        for level in range(1, 30):
            lines.append(f"{chain}{level}: &{chain}{level} [*{chain}{level - 1}, *{chain}{level - 1}]")
    lines.append("users:\n- {name: u, user: {deep: *a29}}\n- {name: u, user: {deep: *b29}}")
    path = tmp_path / "kubeconfig"
    path.write_text("\n".join(lines) + "\n")

    started = time.monotonic()
    result, names = load(path)

    assert result == "ok" and names["users"] == ["u"]
    assert time.monotonic() - started < 5

