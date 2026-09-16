"""R4-03 PR D: dual-supported ManagedCluster teardown parity and request surfaces."""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from lib.constants import (
    HIVE_CLUSTERDEPLOYMENT_API_GROUP,
    HIVE_CLUSTERDEPLOYMENT_API_VERSION,
    HIVE_CLUSTERDEPLOYMENT_PLURAL,
    MANAGED_CLUSTER_API_GROUP,
    MANAGED_CLUSTER_API_VERSION,
    MANAGED_CLUSTER_PLURAL,
)
from lib.decommission_outcome import SubstepOutcome
from lib.run_record import RunRecord
from lib.strict_read import StrictReadOutcome, StrictReadStatus
from lib.utils import StateManager
from modules.decommission import Decommission

ROOT = Path(__file__).resolve().parents[1]
COLLECTION_MC = ROOT / "ansible_collections/tomazb/acm_switchover/roles/decommission/tasks/delete_managed_clusters.yml"
COLLECTION_ONE = (
    ROOT / "ansible_collections/tomazb/acm_switchover/roles/decommission/tasks/teardown_one_managed_cluster.yml"
)


def _strict(status, items=None, resource=None, resource_version="1"):
    status_enum = getattr(StrictReadStatus, status)
    kwargs = {}
    if status_enum is StrictReadStatus.OBJECT_ABSENT:
        kwargs = {
            "status": status_enum,
            "items": [],
            "resource": None,
            "resource_version": None,
        }
    elif status_enum is StrictReadStatus.ITEMS and resource is not None and not items:
        kwargs = {
            "status": status_enum,
            "items": [],
            "resource": resource,
            "resource_version": resource_version,
        }
    else:
        kwargs = {
            "status": status_enum,
            "items": items or [],
            "resource": resource,
            "resource_version": resource_version,
        }
    return StrictReadOutcome(**kwargs)


def _mc(name="spoke-a", uid="uid-1"):
    return {
        "apiVersion": f"{MANAGED_CLUSTER_API_GROUP}/{MANAGED_CLUSTER_API_VERSION}",
        "kind": "ManagedCluster",
        "metadata": {"name": name, "uid": uid, "resourceVersion": "9"},
    }


@pytest.mark.unit
class TestManagedClusterTeardownStaticParity:
    """Collection YAML must declare the same identity-bound primitives as Python."""

    def test_collection_uses_strict_read_and_uid_guarded_delete(self):
        family = COLLECTION_MC.read_text()
        one = COLLECTION_ONE.read_text()
        assert "acm_k8s_read_outcome" in family
        assert "resource_name: managedclusters" in family or "resource_name: managedclusters" in family
        assert "clusterdeployments" in family
        assert "acm_uid_guarded_delete" in one
        assert "state: absent" not in one
        assert "state: absent" not in family
        assert "ignore_errors" not in family
        assert "ignore_errors" not in one
        assert "failed_when: false" not in family.lower()
        assert "failed_when: false" not in one.lower()

    def test_collection_declares_canonical_resource_names(self):
        family = yaml.safe_load(COLLECTION_MC.read_text()) or []
        one = yaml.safe_load(COLLECTION_ONE.read_text()) or []
        reads = []
        for tasks in (family, one):
            for task in tasks:
                if "tomazb.acm_switchover.acm_k8s_read_outcome" in task:
                    reads.append(task["tomazb.acm_switchover.acm_k8s_read_outcome"])
        resource_names = {r.get("resource_name") for r in reads}
        assert "managedclusters" in resource_names
        assert "clusterdeployments" in resource_names

    def test_no_drain_reads_in_collection_mc_path(self):
        blob = COLLECTION_MC.read_text() + "\n" + COLLECTION_ONE.read_text()
        assert "open-cluster-management-observability" not in blob
        assert re.search(r"kind:\s*Pod\b", blob) is None
        assert "drain_pending" not in blob
        assert "drain_pods" not in blob


@pytest.mark.unit
class TestManagedClusterTeardownRequestSurface:
    """Exercise Python family and record the API shapes that feed D6 RBAC measurement."""

    def test_python_success_path_exercises_list_get_delete_and_hive_list(self, tmp_path):
        client = Mock()
        items = [_mc("spoke-a")]
        client.list_managed_clusters_strict = Mock(return_value=_strict("ITEMS", items=items, resource_version="mc-1"))
        client.list_custom_resources_strict = Mock(return_value=_strict("ITEMS", items=[], resource_version="cd-1"))
        present = {"spoke-a": _mc("spoke-a")}

        def named_get(group, version, plural, name, namespace=None):
            resource = present.get(name)
            return _strict("ITEMS", resource=resource) if resource else _strict("OBJECT_ABSENT")

        def delete(group, version, plural, name, uid, namespace=None, timeout_seconds=None):
            present.pop(name, None)

        client.get_custom_resource_strict = Mock(side_effect=named_get)
        client.delete_custom_resource_preconditioned = Mock(side_effect=delete)
        client.get_namespace_strict = Mock(side_effect=AssertionError("no drain"))
        client.list_pods_strict = Mock(side_effect=AssertionError("no drain"))

        decommission = Decommission(
            primary_client=client,
            has_observability=False,
            run_record=RunRecord(StateManager(str(tmp_path / "state.json"))),
        )
        execution = decommission.teardown_managed_clusters()
        assert execution.outcome is SubstepOutcome.COMPLETED
        assert execution.changed is True

        # Inventory LIST
        client.list_managed_clusters_strict.assert_called()
        # Hive LIST
        hive = client.list_custom_resources_strict.call_args
        assert hive.kwargs["group"] == HIVE_CLUSTERDEPLOYMENT_API_GROUP
        assert hive.kwargs["version"] == HIVE_CLUSTERDEPLOYMENT_API_VERSION
        assert hive.kwargs["plural"] == HIVE_CLUSTERDEPLOYMENT_PLURAL
        # Named GET + DELETE for managedclusters
        get_calls = client.get_custom_resource_strict.call_args_list
        assert any(
            c.args[:3]
            == (
                MANAGED_CLUSTER_API_GROUP,
                MANAGED_CLUSTER_API_VERSION,
                MANAGED_CLUSTER_PLURAL,
            )
            or (c.kwargs.get("group") == MANAGED_CLUSTER_API_GROUP and c.kwargs.get("plural") == MANAGED_CLUSTER_PLURAL)
            for c in get_calls
        )
        delete = client.delete_custom_resource_preconditioned.call_args
        assert delete.args[0] == MANAGED_CLUSTER_API_GROUP
        assert delete.args[2] == MANAGED_CLUSTER_PLURAL
        assert delete.kwargs.get("uid") == "uid-1" or (len(delete.args) > 4 and delete.args[4] == "uid-1")
        client.get_namespace_strict.assert_not_called()
        client.list_pods_strict.assert_not_called()

        # Persist measured surface for D6 report consumers
        surface = {
            "form_factor": "python",
            "requests": [
                {
                    "group": MANAGED_CLUSTER_API_GROUP,
                    "resource": MANAGED_CLUSTER_PLURAL,
                    "verb": "list",
                },
                {
                    "group": MANAGED_CLUSTER_API_GROUP,
                    "resource": MANAGED_CLUSTER_PLURAL,
                    "verb": "get",
                },
                {
                    "group": MANAGED_CLUSTER_API_GROUP,
                    "resource": MANAGED_CLUSTER_PLURAL,
                    "verb": "delete",
                },
                {
                    "group": HIVE_CLUSTERDEPLOYMENT_API_GROUP,
                    "resource": HIVE_CLUSTERDEPLOYMENT_PLURAL,
                    "verb": "list",
                },
            ],
        }
        out = tmp_path / "mc_teardown_request_surface.json"
        import json

        out.write_text(json.dumps(surface, indent=2))
        assert out.exists()
