"""#345 at the real Collection decommission boundary over a local HTTP fake."""

import copy
import pathlib
import sys

import pytest

_UNIT_TESTS_DIR = pathlib.Path(__file__).resolve().parent
if str(_UNIT_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_UNIT_TESTS_DIR))

from test_decommission_role_contracts import FakeDecommissionAPI, run_decommission_role  # noqa: E402

RESPONSE_CANARY = "HIVE-CONTINUATION-RESPONSE-CANARY"


@pytest.mark.parametrize("malformed", [True, False], ids=["malformed", "complete-empty-control"])
def test_hive_continuation_controls_managed_cluster_delete_admission(monkeypatch, malformed):
    original_handler = FakeDecommissionAPI._handler
    served = []

    def handler(api):
        parent = original_handler(api)
        write_json = parent._write_json

        def injected_write_json(self, payload, status=200):
            if payload.get("kind") == "ClusterDeploymentList":
                payload = copy.deepcopy(payload)
                assert payload["items"] == []
                payload["metadata"]["continue"] = False if malformed else ""
                payload["fixture_credentials"] = RESPONSE_CANARY
                served.append(copy.deepcopy(payload))
            return write_json(self, payload, status)

        parent._write_json = injected_write_json
        return parent

    monkeypatch.setattr(FakeDecommissionAPI, "_handler", handler)
    result = run_decommission_role(
        managed_clusters=["spoke-a"],
        observability_outcome="precondition_noop",
        multiclusterhub_outcome="precondition_noop",
    )

    assert served, "the production strict reader must exercise the injected Hive response"
    assert served[0]["metadata"]["continue"] == (False if malformed else "")
    assert isinstance(served[0]["metadata"]["resourceVersion"], str)
    summary = result["acm_switchover_decommission_result"]
    deletes = [call for call in result["delete_calls"] if "managedclusters" in call["path"]]
    if malformed:
        assert result["returncode"] != 0
        assert summary["substeps"]["managed_clusters"] == "failed"
        assert summary["changed"] is False
        assert deletes == []
    else:
        assert result["returncode"] == 0
        assert summary["substeps"]["managed_clusters"] == "completed"
        assert summary["changed"] is True
        assert len(deletes) == 1
        assert deletes[0]["path"].endswith("/managedclusters/spoke-a")
        assert deletes[0]["expected_uid"] == "mc-uid-spoke-a"
    assert RESPONSE_CANARY not in str(result)
