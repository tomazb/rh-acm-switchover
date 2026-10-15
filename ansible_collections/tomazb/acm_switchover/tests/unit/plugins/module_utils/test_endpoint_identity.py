# SPDX-License-Identifier: MIT
"""Collection side of the R4-06 PR 2 shared endpoint vectors.

The root parity test runs both form factors; this runs the collection alone in the
collection lanes, against the same hand-authored fixture.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from ansible_collections.tomazb.acm_switchover.plugins.module_utils import endpoint_identity

VECTORS = json.loads(
    (pathlib.Path(__file__).resolve().parents[7] / "tests" / "fixtures" / "r4_06_endpoint_vectors.json").read_text()
)


@pytest.mark.parametrize("row", VECTORS["canonicalization"], ids=lambda row: repr(row["input"])[:60])
def test_canonicalization(row):
    endpoint, code = endpoint_identity.parse_endpoint(row["input"])
    if "canonical" in row:
        assert (str(endpoint), code) == (row["canonical"], None)
    else:
        assert (endpoint, code) == (None, row["reject"])


@pytest.mark.parametrize("row", VECTORS["classification"], ids=lambda row: row["case"])
def test_classification(row):
    assert endpoint_identity.classify_hub(row["expected"], row["observed"]) == (row["outcome"], row["reason"])


@pytest.mark.parametrize("row", VECTORS["redaction"], ids=lambda row: repr(row["input"])[:60])
def test_redaction(row):
    assert endpoint_identity.redact(row["input"]) == row["output"]


@pytest.mark.parametrize("row", VECTORS["managed_cluster_url"], ids=lambda row: row["case"])
def test_managed_cluster_url(row):
    assert endpoint_identity.managed_cluster_url(row["managed_cluster"]) == (row["url"], row["reason"])
