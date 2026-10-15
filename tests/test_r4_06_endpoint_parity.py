"""R4-06 PR 2 parity: Python and the collection agree on every shared endpoint vector.

The vectors (tests/fixtures/r4_06_endpoint_vectors.json) are hand-authored from the
amendment (docs/plans/2026-10-15-r4-06-current-base-amendment.md §2.1-§2.3); each form factor
must reproduce them, so neither can accept a URL the other rejects or classify a pair
differently (amendment §5).
"""

import json
import pathlib

import pytest

import ansible_collections.tomazb.acm_switchover.plugins.module_utils.endpoint_identity as col
import lib.endpoint_identity as py

VECTORS = json.loads((pathlib.Path(__file__).parent / "fixtures" / "r4_06_endpoint_vectors.json").read_text())
IMPLEMENTATIONS = [pytest.param(py, id="python"), pytest.param(col, id="collection")]


def _id(row):
    return repr(row.get("input", row.get("case")))[:60]


@pytest.mark.parametrize("impl", IMPLEMENTATIONS)
@pytest.mark.parametrize("row", VECTORS["canonicalization"], ids=_id)
def test_canonicalization_vector(impl, row):
    endpoint, code = impl.parse_endpoint(row["input"])
    if "canonical" in row:
        assert code is None
        assert str(endpoint) == row["canonical"]
    else:
        assert endpoint is None
        assert code == row["reject"]


@pytest.mark.parametrize("impl", IMPLEMENTATIONS)
@pytest.mark.parametrize("row", VECTORS["classification"], ids=lambda row: row["case"])
def test_classification_vector(impl, row):
    assert impl.classify_hub(row["expected"], row["observed"]) == (row["outcome"], row["reason"])
    if row["py_legacy_equal"] is not None:
        assert (impl.py_legacy_host(row["expected"]) == impl.py_legacy_host(row["observed"])) is row["py_legacy_equal"]
        assert (impl.col_legacy_host(row["expected"]) == impl.col_legacy_host(row["observed"])) is row[
            "col_legacy_equal"
        ]


@pytest.mark.parametrize("impl", IMPLEMENTATIONS)
@pytest.mark.parametrize("row", VECTORS["redaction"], ids=_id)
def test_redaction_vector(impl, row):
    assert impl.redact(row["input"]) == row["output"]


@pytest.mark.parametrize("impl", IMPLEMENTATIONS)
@pytest.mark.parametrize("row", VECTORS["managed_cluster_url"], ids=lambda row: row["case"])
def test_managed_cluster_url_vector(impl, row):
    assert impl.managed_cluster_url(row["managed_cluster"]) == (row["url"], row["reason"])


def test_wrong_hub_never_follows_a_legacy_equal_pair():
    """Amendment §2.2: no pair either legacy comparator called equal is ever `wrong_hub`."""
    for row in VECTORS["classification"]:
        if row["py_legacy_equal"] or row["col_legacy_equal"]:
            assert row["outcome"] != "wrong_hub", row["case"]


def test_rejected_values_are_never_echoed():
    secret_bearing = "https://alice:hunter2@api.hub:6443"
    for impl in (py, col):
        assert "hunter2" not in impl.redact(secret_bearing)
        assert "alice" not in impl.redact(secret_bearing)


@pytest.mark.parametrize(
    ("url", "server", "reason"),
    [
        ("https://api.spoke:6443", "https://API.spoke:6443/", None),
        ("https://api.spoke:6443", "https://api.spoke:443", "endpoint_mismatch_unverified"),
        ("https://api.spoke:6443", "http://api.spoke:6443", "endpoint_mismatch_unverified"),
        ("https://api.spoke:6443", "https://api.spoke:6443/x", "endpoint_mismatch_unverified"),
        ("https://api.spoke:6443", "https://api.other:6443", "target_identity_mismatch"),
        ("https://api.spoke:6443", "api.spoke:6443", "endpoint_malformed"),
        ("https://api.spoke:6443", None, "endpoint_malformed"),
        (None, "https://api.spoke:6443", "endpoint_malformed"),
    ],
)
def test_collection_target_identity_proof(url, server, reason):
    assert col.prove_target_identity(url, server) == reason
