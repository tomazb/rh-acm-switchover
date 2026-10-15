"""Design 2026-07-29 §1 fail-closed kubeconfig merge (R4-06 PR 1, #349): the pure loader.

Every input file is read once, bounded by one inclusive size limit, parsed with a loader
that rejects duplicate keys, and structurally validated before anything is merged. Any
failure aborts the whole merge with a sanitized reason code and the file path.
"""

import os
from pathlib import Path

import pytest
import yaml

from lib.constants import DEFAULT_KUBECONFIG_SIZE, kubeconfig_size_limit, kubeconfig_size_override_ignored
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


MERGES = [
    pytest.param(
        "base: &base {server: https://merged:6443}\n"
        "clusters:\n- name: k\n  cluster:\n    <<: *base\n    server: https://explicit:6443\n",
        id="override-beside-merge",
    ),
    pytest.param(
        "b: &b {server: https://b:6443}\na: &a {<<: *b, server: https://a:6443}\n"
        "clusters: [{name: k, cluster: {<<: *a}}]\n",
        id="chained-anchors",
    ),
    pytest.param("users:\n- name: u\n  user: {<<: {<<: {token: first, token: second}}}\n", id="nested-inline"),
    pytest.param("a: &a {x: 1}\nb: &b {y: 1}\nm:\n  <<: *a\n  <<: *b\n", id="repeated-merge-key"),
]


@pytest.mark.parametrize("content", MERGES)
def test_yaml_merge_keys_are_refused(tmp_path, content):
    """A `<<` merge's override order is parser-specific; an endpoint must not depend on it."""
    doc = _write(tmp_path / "merge.yaml", content)
    with pytest.raises(SwitchoverError, match=r"kubeconfig_load_failed \(yaml_invalid\).*merge keys are not supported"):
        load_merged_kubeconfig(doc, DEFAULT_KUBECONFIG_SIZE)


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


def test_a_huge_limit_never_preallocates_or_crashes(tmp_path):
    small = _write(tmp_path / "small.yaml", _doc("a"))
    assert load_merged_kubeconfig(small, 2**62)["contexts"][0]["name"] == "a"


def test_no_read_ever_asks_for_more_than_one_chunk(tmp_path, monkeypatch):
    """The bound holds per read call: an oversized file is never read whole into memory."""
    import lib.kubeconfig_merge as merge

    big = tmp_path / "big.yaml"
    big.write_bytes(b"#" * (512 * 1024))
    requested = []
    real_open = open

    class Recording:
        def __init__(self, handle):
            self._handle = handle

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self._handle.close()
            return False

        def read(self, size=-1):
            requested.append(size)
            return self._handle.read(size)

    monkeypatch.setattr(merge, "open", lambda path, mode="r": Recording(real_open(path, mode)), raising=False)
    with pytest.raises(SwitchoverError, match=r"\(oversized\)"):
        load_merged_kubeconfig(str(big), 100 * 1024)
    assert requested and all(0 < size <= 64 * 1024 for size in requested), requested
    assert sum(requested) <= 100 * 1024 + 64 * 1024


def test_a_pipe_is_bounded_without_preallocating(tmp_path):
    """A FIFO (`KUBECONFIG=<(oc config view --raw)`) has no size to stat; the read is chunked."""
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    body = yaml.safe_dump(_doc("piped")).encode()

    def feed():
        with open(fifo, "wb") as writer:
            writer.write(body)

    import threading

    thread = threading.Thread(target=feed)
    thread.start()
    try:
        assert load_merged_kubeconfig(str(fifo), 2**62)["contexts"][0]["name"] == "piped"
    finally:
        thread.join(timeout=5)


CONSTRUCTOR_ERRORS = [
    pytest.param("users:\n- name: u\n  user: {token: !!int SUPERSECRETTOKEN}\n", id="int-value"),
    pytest.param("users:\n- name: u\n  user: {token: !!bool SUPERSECRETTOKEN}\n", id="bool-keyerror"),
    pytest.param("users:\n- name: u\n  user: {token: !!int ''}\n", id="empty-int-indexerror"),
    pytest.param("users:\n- name: u\n  user: {token: !!float ''}\n", id="empty-float-indexerror"),
    pytest.param("users:\n- name: u\n  user: {token: !!timestamp SUPERSECRET-99-99}\n", id="timestamp"),
]


@pytest.mark.parametrize("content", CONSTRUCTOR_ERRORS)
def test_any_constructor_error_is_a_sanitized_yaml_invalid(tmp_path, content):
    with pytest.raises(SwitchoverError) as exc:
        load_merged_kubeconfig(_write(tmp_path / "c.yaml", content), DEFAULT_KUBECONFIG_SIZE)
    assert "kubeconfig_load_failed (yaml_invalid)" in str(exc.value)
    assert "SUPERSECRET" not in str(exc.value).upper()
    assert exc.value.__cause__ is None and exc.value.__context__ is None


def test_no_failure_carries_the_original_exception(tmp_path):
    """The original parser/constructor exception (whose text or Mark buffer can hold key
    material) is never attached, not even as an implicit __context__."""
    for name, content in [
        ("bad.yaml", "users:\n- {name: u, user: {token: SUPERSECRET\n"),
        ("dup.yaml", "users:\n- name: u\n  user: {token: SUPERSECRET, token: x}\n"),
        ("merge.yaml", "a: &a {token: SUPERSECRET}\nusers: [{name: u, user: {<<: *a}}]\n"),
    ]:
        with pytest.raises(SwitchoverError) as exc:
            load_merged_kubeconfig(_write(tmp_path / name, content), DEFAULT_KUBECONFIG_SIZE)
        assert exc.value.__cause__ is None and exc.value.__context__ is None, name


@pytest.mark.parametrize(
    ("raw", "ignored"), [(None, False), ("4096", False), ("0", True), ("-1", True), ("10MB", True)]
)
def test_an_override_that_cannot_be_honoured_is_detected(raw, ignored):
    assert kubeconfig_size_override_ignored(raw) is ignored


def test_absent_sections_and_an_absent_user_are_accepted(tmp_path):
    minimal = _write(tmp_path / "m.yaml", "apiVersion: v1\ncontexts:\n- name: c\n  context: {cluster: k}\n")
    merged = load_merged_kubeconfig(minimal, DEFAULT_KUBECONFIG_SIZE)
    assert merged == {"contexts": [{"name": "c", "context": {"cluster": "k"}}], "clusters": [], "users": []}


def test_conflicting_duplicates_across_files_refuse_the_whole_merge(tmp_path):
    """Amendment §3: a (kind, name) group with two variants anywhere in the chain fails."""
    a = _write(tmp_path / "a.yaml", _doc("same", "https://one:6443"))
    b = _write(tmp_path / "b.yaml", _doc("same", "https://two:6443"))
    with pytest.raises(SwitchoverError) as excinfo:
        load_merged_kubeconfig(f"{a}:{b}", DEFAULT_KUBECONFIG_SIZE)
    message = str(excinfo.value)
    assert message.startswith("kubeconfig_duplicate_conflict: ")
    assert f"{a}[0] (variant 1)" in message and f"{b}[0] (variant 2)" in message
    assert "https://" not in message.replace(str(tmp_path), "")


def test_identical_duplicates_across_files_resolve_first_wins(tmp_path):
    a = _write(tmp_path / "a.yaml", _doc("same", "https://one:6443"))
    b = _write(tmp_path / "b.yaml", "clusters:\n- cluster:\n    server: https://one:6443\n  name: same\n")
    merged = load_merged_kubeconfig(f"{a}:{b}", DEFAULT_KUBECONFIG_SIZE)
    assert [c["name"] for c in merged["clusters"]] == ["same"]


def test_every_occurrence_of_a_conflicting_group_is_located(tmp_path):
    """Three occurrences with two variants: all three are reported, each labelled."""
    a = _write(tmp_path / "a.yaml", _doc("same", "https://one:6443"))
    b = _write(tmp_path / "b.yaml", _doc("same", "https://one:6443"))
    c = _write(tmp_path / "c.yaml", _doc("same", "https://two:6443"))
    with pytest.raises(SwitchoverError) as excinfo:
        load_merged_kubeconfig(f"{a}:{b}:{c}", DEFAULT_KUBECONFIG_SIZE)
    message = str(excinfo.value)
    assert f"{a}[0] (variant 1)" in message
    assert f"{b}[0] (variant 1)" in message
    assert f"{c}[0] (variant 2)" in message


def test_a_conflict_in_one_kind_is_reported_with_its_kind(tmp_path):
    a = _write(tmp_path / "a.yaml", "users:\n- {name: u, user: {token: one}}\n")
    b = _write(tmp_path / "b.yaml", "users:\n- {name: u, user: {token: two}}\n")
    with pytest.raises(SwitchoverError) as excinfo:
        load_merged_kubeconfig(f"{a}:{b}", DEFAULT_KUBECONFIG_SIZE)
    assert "user 'u' at" in str(excinfo.value)
    assert "one" not in str(excinfo.value).replace(str(tmp_path), "")


def test_a_yaml_set_is_refused_with_its_position(tmp_path):
    """Amendment §3: `!!set` has no defined duplicate comparison; the refusal names where it is."""
    path = _write(tmp_path / "set.yaml", "clusters: []\nusers: !!set {a, b}\n")
    with pytest.raises(SwitchoverError) as excinfo:
        load_merged_kubeconfig(path, DEFAULT_KUBECONFIG_SIZE)
    assert str(excinfo.value) == (
        f"kubeconfig_load_failed (structure_invalid): {path} line 2 column 8 (YAML sets are not supported)"
    )
