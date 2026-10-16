"""Real-reader regression for the Hive gate before ManagedCluster teardown (#345)."""

import copy
from unittest.mock import Mock

import pytest
from kubernetes.client.rest import ApiException

from lib.decommission_outcome import SubstepOutcome
from lib.exceptions import SwitchoverError
from lib.run_record import RunRecord
from lib.utils import StateManager
from modules.decommission import Decommission
from tests import test_strict_read_parity as readers

RESPONSE_CANARY = "HIVE-RESPONSE-CREDENTIAL-CANARY"


def _teardown(tmp_path, token, *, preview=False):
    client = readers._python_client()
    client.dry_run = preview
    spoke = {
        "apiVersion": "cluster.open-cluster-management.io/v1",
        "kind": "ManagedCluster",
        "metadata": {"name": "spoke", "uid": "uid-spoke", "resourceVersion": "10"},
    }
    store = {"spoke": spoke, "local-cluster": {"metadata": {"name": "local-cluster"}}}
    hive_page = {
        "items": [],
        "metadata": {"resourceVersion": "100", "continue": copy.deepcopy(token)},
        "fixture_credentials": RESPONSE_CANARY,
    }

    def discovery(path, *_args, **_kwargs):
        if path == "/apis/cluster.open-cluster-management.io/v1":
            plural, kind = "managedclusters", "ManagedCluster"
        else:
            assert path == "/apis/hive.openshift.io/v1"
            plural, kind = "clusterdeployments", "ClusterDeployment"
        return readers._raw_body(
            {
                "kind": "APIResourceList",
                "groupVersion": path.removeprefix("/apis/"),
                "resources": [{"name": plural, "kind": kind}],
            }
        )

    def inventory(**params):
        assert params["version"] == "v1"
        if params["plural"] == "managedclusters":
            assert params["group"] == "cluster.open-cluster-management.io"
            return {"items": copy.deepcopy(list(store.values())), "metadata": {"resourceVersion": "100"}}
        assert params["group"] == "hive.openshift.io"
        assert params["plural"] == "clusterdeployments"
        return copy.deepcopy(hive_page)

    def named_get(**params):
        assert params["group"] == "cluster.open-cluster-management.io"
        assert params["plural"] == "managedclusters"
        assert params["name"] == "spoke"
        if "spoke" not in store:
            raise ApiException(status=404)
        return copy.deepcopy(store["spoke"])

    def delete(**params):
        assert params["group"] == "cluster.open-cluster-management.io"
        assert params["plural"] == "managedclusters"
        assert params["name"] == "spoke"
        assert params["body"].preconditions.uid == "uid-spoke"
        store.pop("spoke")

    client._api_client.call_api.side_effect = discovery
    client.custom_api.list_cluster_custom_object.side_effect = inventory
    client.custom_api.get_cluster_custom_object.side_effect = named_get
    client.custom_api.delete_cluster_custom_object.side_effect = delete
    # Spy through the real guarded primitive, not a replacement error/result.
    client.delete_custom_resource_preconditioned = Mock(wraps=client.delete_custom_resource_preconditioned)
    state = StateManager(str(tmp_path / "state.json"))
    record = RunRecord(state)
    return Decommission(client, False, run_record=record, dry_run=preview), client, hive_page, store


@pytest.mark.parametrize("token", [False, 0, [], {}], ids=["false", "zero", "list", "mapping"])
@pytest.mark.parametrize("preview", [False, True], ids=["execute", "dry-run"])
def test_malformed_hive_inventory_blocks_teardown_before_mutation(token, preview, tmp_path, caplog):
    teardown, client, hive_page, store = _teardown(tmp_path, token, preview=preview)
    assert hive_page["items"] == []
    assert hive_page["metadata"] == {"resourceVersion": "100", "continue": token}

    for _ in range(2):
        if preview:
            with pytest.raises(SwitchoverError, match="preserveOnDelete safety"):
                teardown._preview_managed_clusters()
        else:
            result = teardown.teardown_managed_clusters()
            assert result.outcome is SubstepOutcome.FAILED
            assert result.changed is False
            assert "preserveOnDelete safety" in caplog.text
        client.delete_custom_resource_preconditioned.assert_not_called()
        client.custom_api.delete_cluster_custom_object.assert_not_called()
        assert teardown.run_record.all_teardown_records() == {}
        assert set(store) == {"spoke", "local-cluster"}
    assert client.custom_api.list_cluster_custom_object.call_count == 4
    assert RESPONSE_CANARY not in caplog.text


def test_complete_empty_hive_inventory_allows_uid_guarded_teardown_and_idempotent_resume(tmp_path):
    teardown, client, hive_page, store = _teardown(tmp_path, "")
    assert hive_page["items"] == [] and hive_page["metadata"]["continue"] == ""

    first = teardown.teardown_managed_clusters()
    assert first.outcome is SubstepOutcome.COMPLETED
    assert first.changed is True
    client.delete_custom_resource_preconditioned.assert_called_once()
    client.custom_api.delete_cluster_custom_object.assert_called_once()
    assert set(store) == {"local-cluster"}
    records = teardown.run_record.all_teardown_records()
    assert len(records) == 1
    assert next(iter(records.values())).phase.value == "completed"

    resumed = teardown.teardown_managed_clusters()
    assert resumed.outcome is SubstepOutcome.COMPLETED
    assert resumed.changed is False
    client.delete_custom_resource_preconditioned.assert_called_once()
