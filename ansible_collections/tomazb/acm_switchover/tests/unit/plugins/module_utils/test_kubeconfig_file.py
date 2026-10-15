# SPDX-License-Identifier: MIT
"""Mapped managed-cluster kubeconfig validation (R4-06 PR 2, amendment §2.6 and §4)."""

from __future__ import annotations

import json
import pathlib

import pytest

from ansible_collections.tomazb.acm_switchover.plugins.module_utils.kubeconfig_file import (
    KubeconfigFileError,
    load_kubeconfig_file,
    select_context,
)

VECTORS = json.loads(
    (pathlib.Path(__file__).resolve().parents[7] / "tests" / "fixtures" / "r4_06_kubeconfig_vectors.json").read_text()
)

TWO_CONTEXTS = """
clusters:
- {name: spoke, cluster: {server: 'https://api.spoke:6443'}}
- {name: other, cluster: {server: 'https://api.other:6443'}}
contexts:
- {name: spoke-admin, context: {cluster: spoke, user: admin}}
- {name: other-admin, context: {cluster: other, user: admin}}
- {name: dangling, context: {cluster: gone, user: admin}}
users:
- {name: admin, user: {token: t}}
"""


@pytest.mark.parametrize("row", VECTORS["single_file"], ids=lambda row: row["case"])
def test_shared_single_file_vector(row, tmp_path):
    path = tmp_path / "kubeconfig"
    path.write_text(row["yaml"])
    try:
        document = load_kubeconfig_file(str(path), 1024 * 1024)
    except KubeconfigFileError as exc:
        assert exc.reason == row["result"]
        assert exc.__context__ is None
        return
    assert row["result"] == "ok"
    assert {section: [entry["name"] for entry in document[section]] for section in row["names"]} == row["names"]


def test_a_path_with_a_colon_is_refused_before_any_read(tmp_path):
    with pytest.raises(KubeconfigFileError) as excinfo:
        load_kubeconfig_file(f"{tmp_path}/a:{tmp_path}/b")
    assert excinfo.value.reason == "kubeconfig_path_unsupported"


def test_the_size_limit_is_inclusive(tmp_path):
    path = tmp_path / "kubeconfig"
    path.write_text("clusters: []\n")
    size = path.stat().st_size
    assert load_kubeconfig_file(str(path), size)["clusters"] == []
    with pytest.raises(KubeconfigFileError) as excinfo:
        load_kubeconfig_file(str(path), size - 1)
    assert excinfo.value.reason == "kubeconfig_load_failed (oversized)"


def test_a_missing_file_is_refused(tmp_path):
    with pytest.raises(KubeconfigFileError) as excinfo:
        load_kubeconfig_file(str(tmp_path / "absent"))
    assert excinfo.value.reason == "kubeconfig_load_failed (missing)"


def test_a_duplicate_conflict_locates_every_occurrence_without_content(tmp_path):
    path = tmp_path / "kubeconfig"
    path.write_text(
        "clusters:\n- {name: k, cluster: {server: 'https://one'}}\n- {name: k, cluster: {server: 'https://one'}}\n"
        "- {name: k, cluster: {server: 'https://two'}}\n"
    )
    with pytest.raises(KubeconfigFileError) as excinfo:
        load_kubeconfig_file(str(path))
    assert excinfo.value.reason == "kubeconfig_duplicate_conflict"
    assert excinfo.value.detail == "cluster 'k' at [0] (variant 1), [1] (variant 1), [2] (variant 2)"


def _document(tmp_path, text=TWO_CONTEXTS):
    path = tmp_path / "kubeconfig"
    path.write_text(text)
    return load_kubeconfig_file(str(path))


def test_a_named_context_selects_its_cluster_entry(tmp_path):
    name, cluster = select_context(_document(tmp_path), "spoke-admin")
    assert name == "spoke-admin"
    assert cluster["cluster"]["server"] == "https://api.spoke:6443"


def test_a_missing_named_context_is_refused(tmp_path):
    with pytest.raises(KubeconfigFileError) as excinfo:
        select_context(_document(tmp_path), "spoke")
    assert excinfo.value.reason == "context_missing"


def test_an_omitted_context_without_current_context_is_ambiguous(tmp_path):
    with pytest.raises(KubeconfigFileError) as excinfo:
        select_context(_document(tmp_path), None)
    assert excinfo.value.reason == "context_ambiguous"


@pytest.mark.parametrize("current", ["''", "nope", "1"])
def test_an_omitted_context_with_an_unusable_current_context_is_ambiguous(tmp_path, current):
    with pytest.raises(KubeconfigFileError) as excinfo:
        select_context(_document(tmp_path, TWO_CONTEXTS + f"current-context: {current}\n"), None)
    assert excinfo.value.reason == "context_ambiguous"


def test_an_omitted_context_uses_a_current_context_naming_one_context(tmp_path):
    name, cluster = select_context(_document(tmp_path, TWO_CONTEXTS + "current-context: other-admin\n"), None)
    assert (name, cluster["name"]) == ("other-admin", "other")


def test_a_context_naming_no_cluster_entry_selects_no_cluster(tmp_path):
    assert select_context(_document(tmp_path), "dangling") == ("dangling", None)
