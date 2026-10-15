"""Design 2026-07-29 §1 fail-closed kubeconfig merge (R4-06 PR 1, #349): the pure loader.

Every input file is read once, bounded by one inclusive size limit, parsed with a loader
that rejects duplicate keys, and structurally validated before anything is merged. Any
failure aborts the whole merge with a sanitized reason code and the file path.
"""

import os
from pathlib import Path

import pytest
import yaml

from lib.constants import DEFAULT_KUBECONFIG_SIZE, kubeconfig_size_limit
from lib.exceptions import SwitchoverError
from lib.kubeconfig_merge import kubeconfig_paths, load_merged_kubeconfig


def _doc(name, server="https://api.example:6443"):
    return {
        "apiVersion": "v1",
        "clusters": [{"name": name, "cluster": {"server": server}}],
        "contexts": [{"name": name, "context": {"cluster": name, "user": name}}],
        "users": [{"name": name, "user": {"token": "t"}}],
    }


def _write(path: Path, data) -> str:
    path.write_text(data if isinstance(data, str) else yaml.safe_dump(data), encoding="utf-8")
    return str(path)


# --- precedence -----------------------------------------------------------------------------


def test_kubeconfig_entries_are_read_left_to_right_once_each(tmp_path):
    a, b = str(tmp_path / "a"), str(tmp_path / "b")
    assert kubeconfig_paths(f"{a}::{b}:{a}:") == [a, b]


@pytest.mark.parametrize("env", [None, "", ":", "::"])
def test_the_default_path_is_used_only_when_kubeconfig_names_nothing(env):
    assert kubeconfig_paths(env) == [os.path.expanduser("~/.kube/config")]


def test_entries_are_taken_verbatim_as_the_client_loader_takes_them(tmp_path):
    """No whitespace trimming: the validated files are exactly the files the client loads."""
    a = str(tmp_path / "a")
    assert kubeconfig_paths(f"{a}: {a}") == [a, f" {a}"]


def test_the_default_path_is_never_appended_to_a_named_list(tmp_path):
    assert os.path.expanduser("~/.kube/config") not in kubeconfig_paths(str(tmp_path / "a"))


def test_reordering_the_files_reorders_the_merge(tmp_path):
    a = _write(tmp_path / "a.yaml", _doc("a"))
    b = _write(tmp_path / "b.yaml", _doc("b"))
    forward = load_merged_kubeconfig(f"{a}:{b}", DEFAULT_KUBECONFIG_SIZE)
    backward = load_merged_kubeconfig(f"{b}:{a}", DEFAULT_KUBECONFIG_SIZE)
    assert [c["name"] for c in forward["contexts"]] == ["a", "b"]
    assert [c["name"] for c in backward["contexts"]] == ["b", "a"]


# --- size limit -----------------------------------------------------------------------------


def test_the_size_limit_is_inclusive(tmp_path):
    body = yaml.safe_dump(_doc("a"))
    exact = _write(tmp_path / "exact.yaml", body)
    assert load_merged_kubeconfig(exact, len(body.encode()))["contexts"][0]["name"] == "a"
    with pytest.raises(SwitchoverError, match=r"kubeconfig_load_failed \(oversized\)"):
        load_merged_kubeconfig(exact, len(body.encode()) - 1)


@pytest.mark.parametrize("raw", [None, "", "abc", "0", "-1", "-4096", "1.5"])
def test_an_override_can_never_disable_the_size_limit(raw):
    assert kubeconfig_size_limit(raw) == DEFAULT_KUBECONFIG_SIZE


@pytest.mark.parametrize("raw", ["1024", str(64 * 1024 * 1024)])
def test_an_override_can_raise_or_lower_the_size_limit(raw):
    assert kubeconfig_size_limit(raw) == int(raw)


# --- file-level failures ----------------------------------------------------------------------


def test_a_missing_file_aborts_the_whole_merge(tmp_path):
    good = _write(tmp_path / "good.yaml", _doc("a"))
    with pytest.raises(SwitchoverError, match=r"kubeconfig_load_failed \(missing\).*absent\.yaml"):
        load_merged_kubeconfig(f"{good}:{tmp_path / 'absent.yaml'}", DEFAULT_KUBECONFIG_SIZE)


def test_an_unreadable_path_aborts_the_whole_merge(tmp_path):
    (tmp_path / "dir.yaml").mkdir()
    with pytest.raises(SwitchoverError, match=r"kubeconfig_load_failed \(unreadable\)"):
        load_merged_kubeconfig(str(tmp_path / "dir.yaml"), DEFAULT_KUBECONFIG_SIZE)


def test_invalid_yaml_aborts_with_a_position_and_no_parser_text(tmp_path):
    bad = _write(tmp_path / "bad.yaml", "clusters:\n  - name: x\n    cluster: {server: \n")
    with pytest.raises(SwitchoverError) as exc:
        load_merged_kubeconfig(bad, DEFAULT_KUBECONFIG_SIZE)
    message = str(exc.value)
    assert "kubeconfig_load_failed (yaml_invalid)" in message and "line " in message
    assert "server" not in message and "expected" not in message


def test_a_duplicate_mapping_key_aborts_instead_of_keeping_the_last(tmp_path):
    dup = _write(tmp_path / "dup.yaml", "contexts: []\nclusters: []\ncontexts: []\n")
    with pytest.raises(SwitchoverError, match=r"kubeconfig_load_failed \(duplicate_yaml_key\).*line 3"):
        load_merged_kubeconfig(dup, DEFAULT_KUBECONFIG_SIZE)


def test_a_constructor_error_is_sanitized_and_never_quotes_the_value(tmp_path):
    """`!!int <token>` raises a plain ValueError quoting the scalar, not a YAMLError."""
    bad = _write(tmp_path / "tag.yaml", "users:\n- name: u\n  user: {token: !!int SUPERSECRETTOKEN}\n")
    with pytest.raises(SwitchoverError) as exc:
        load_merged_kubeconfig(bad, DEFAULT_KUBECONFIG_SIZE)
    assert "kubeconfig_load_failed (yaml_invalid)" in str(exc.value)
    assert "SUPERSECRET" not in str(exc.value)
    assert exc.value.__cause__ is None and exc.value.__context__ is None


def test_yaml_merge_keys_are_accepted_and_explicit_keys_override_them(tmp_path):
    merged_doc = _write(
        tmp_path / "merge.yaml",
        "base: &base {server: https://merged:6443, insecure-skip-tls-verify: false}\n"
        "clusters:\n- name: k\n  cluster:\n    <<: *base\n    server: https://explicit:6443\n",
    )
    merged = load_merged_kubeconfig(merged_doc, DEFAULT_KUBECONFIG_SIZE)
    assert merged["clusters"][0]["cluster"]["server"] == "https://explicit:6443"


def test_a_duplicate_explicit_key_beside_a_merge_is_still_refused(tmp_path):
    dup = _write(tmp_path / "dup.yaml", "b: &b {x: 1}\nm:\n  <<: *b\n  y: 1\n  y: 2\n")
    with pytest.raises(SwitchoverError, match=r"kubeconfig_load_failed \(duplicate_yaml_key\)"):
        load_merged_kubeconfig(dup, DEFAULT_KUBECONFIG_SIZE)


@pytest.mark.parametrize("section", ["clusters", "contexts", "users"])
def test_a_null_section_is_an_empty_section_as_kubectl_writes_it(tmp_path, section):
    doc = _write(tmp_path / "null.yaml", f"apiVersion: v1\n{section}: null\n")
    assert load_merged_kubeconfig(doc, DEFAULT_KUBECONFIG_SIZE)[section] == []


def test_a_nested_duplicate_key_is_also_refused(tmp_path):
    dup = _write(tmp_path / "dup.yaml", "users:\n- name: u\n  user: {token: a, token: b}\n")
    with pytest.raises(SwitchoverError, match=r"kubeconfig_load_failed \(duplicate_yaml_key\)"):
        load_merged_kubeconfig(dup, DEFAULT_KUBECONFIG_SIZE)


# --- structure ---------------------------------------------------------------------------------


STRUCTURE = [
    pytest.param("", id="empty-document"),
    pytest.param("null\n", id="null-root"),
    pytest.param("just-a-string\n", id="scalar-root"),
    pytest.param("- a\n- b\n", id="list-root"),
    pytest.param("clusters: {}\n", id="clusters-not-a-list"),
    pytest.param("contexts: [plain]\n", id="context-entry-not-a-mapping"),
    pytest.param("users:\n- user: {}\n", id="nameless-entry"),
    pytest.param("clusters:\n- name: ''\n", id="empty-name"),
    pytest.param("clusters:\n- name: 7\n", id="non-string-name"),
    pytest.param("contexts:\n- name: c\n  context: [x]\n", id="context-member-not-a-mapping"),
    pytest.param("contexts:\n- name: c\n  context: {cluster: 7}\n", id="context-cluster-not-a-string"),
    pytest.param("contexts:\n- name: c\n  context: {cluster: k, user: [u]}\n", id="context-user-not-a-string"),
    pytest.param("contexts:\n- name: c\n  context: null\n", id="context-member-null"),
    pytest.param("clusters:\n- name: k\n  cluster: null\n", id="cluster-member-null"),
    pytest.param("users:\n- name: u\n  user: null\n", id="user-member-null"),
    pytest.param("clusters:\n- name: k\n  cluster: plain\n", id="cluster-member-not-a-mapping"),
    pytest.param("users:\n- name: u\n  user: [x]\n", id="user-member-not-a-mapping"),
]


@pytest.mark.parametrize("content", STRUCTURE)
def test_a_structurally_invalid_document_aborts_the_whole_merge(tmp_path, content):
    good = _write(tmp_path / "good.yaml", _doc("a"))
    bad = _write(tmp_path / "bad.yaml", content)
    with pytest.raises(SwitchoverError, match=r"kubeconfig_load_failed \(structure_invalid\)"):
        load_merged_kubeconfig(f"{good}:{bad}", DEFAULT_KUBECONFIG_SIZE)


def test_kubectl_written_empty_context_fields_are_accepted(tmp_path):
    """`kubectl config set-context x --cluster=c` writes `user: ""` (and `cluster: ""` without
    --cluster); an unrelated such context must not abort the whole merge."""
    doc = _write(
        tmp_path / "kubectl.yaml",
        "contexts:\n- name: x\n  context: {cluster: c, user: ''}\n- name: y\n  context: {cluster: '', user: ''}\n",
    )
    assert [c["name"] for c in load_merged_kubeconfig(doc, DEFAULT_KUBECONFIG_SIZE)["contexts"]] == ["x", "y"]


def test_a_repeated_merge_key_is_refused(tmp_path):
    dup = _write(tmp_path / "dup.yaml", "a: &a {x: 1}\nb: &b {y: 1}\nm:\n  <<: *a\n  <<: *b\n")
    with pytest.raises(SwitchoverError, match=r"kubeconfig_load_failed \(duplicate_yaml_key\)"):
        load_merged_kubeconfig(dup, DEFAULT_KUBECONFIG_SIZE)


def test_a_duplicate_key_inside_an_inline_merge_source_is_refused(tmp_path):
    dup = _write(tmp_path / "dup.yaml", "users:\n- name: u\n  user: {<<: {token: first, token: second}}\n")
    with pytest.raises(SwitchoverError, match=r"kubeconfig_load_failed \(duplicate_yaml_key\)"):
        load_merged_kubeconfig(dup, DEFAULT_KUBECONFIG_SIZE)


def test_a_huge_limit_never_preallocates_or_crashes(tmp_path):
    small = _write(tmp_path / "small.yaml", _doc("a"))
    assert load_merged_kubeconfig(small, 2**62)["contexts"][0]["name"] == "a"


def test_no_failure_carries_the_original_exception(tmp_path):
    """The original parser/constructor exception (whose text or Mark buffer can hold key
    material) is never attached, not even as an implicit __context__."""
    for name, content in [
        ("int.yaml", "users:\n- name: u\n  user: {token: !!int SUPERSECRETTOKEN}\n"),
        ("bad.yaml", "users:\n- {name: u, user: {token: SUPERSECRET\n"),
        ("dup.yaml", "users:\n- name: u\n  user: {token: SUPERSECRET, token: x}\n"),
    ]:
        with pytest.raises(SwitchoverError) as exc:
            load_merged_kubeconfig(_write(tmp_path / name, content), DEFAULT_KUBECONFIG_SIZE)
        assert exc.value.__cause__ is None and exc.value.__context__ is None, name


def test_a_disabling_override_is_reported(caplog):
    with caplog.at_level("WARNING", logger="acm_switchover"):
        assert kubeconfig_size_limit("0") == DEFAULT_KUBECONFIG_SIZE
    assert "ACM_KUBECONFIG_MAX_SIZE" in caplog.text


def test_absent_sections_and_an_absent_user_are_accepted(tmp_path):
    minimal = _write(tmp_path / "m.yaml", "apiVersion: v1\ncontexts:\n- name: c\n  context: {cluster: k}\n")
    merged = load_merged_kubeconfig(minimal, DEFAULT_KUBECONFIG_SIZE)
    assert merged == {"contexts": [{"name": "c", "context": {"cluster": "k"}}], "clusters": [], "users": []}


def test_repeated_entries_are_merged_in_order_unchanged(tmp_path):
    """Duplicate-name policy is R4-06 PR 2 (§2); this slice keeps today's ordered merge."""
    a = _write(tmp_path / "a.yaml", _doc("same", "https://one:6443"))
    b = _write(tmp_path / "b.yaml", _doc("same", "https://two:6443"))
    merged = load_merged_kubeconfig(f"{a}:{b}", DEFAULT_KUBECONFIG_SIZE)
    assert [c["cluster"]["server"] for c in merged["clusters"]] == ["https://one:6443", "https://two:6443"]
