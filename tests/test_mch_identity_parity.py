"""Python/collection parity for MCH operator identity and owner-chain classification (plan §11C.3 row 18).

The shared abstract vectors in `tests/test_decommission_identity.py` are the single source of
test data. Each form factor renders them into its own real read shapes -- the Python strict
reads yield snake_case `to_dict()` members, the collection's strict reads yield the camelCase
objects the dynamic client returns -- and this module compares DECISIONS, STATUSES, REASONS and
the identity values both sides produce, plus which objects each side read.

Only the collection's pure `module_utils/pod_owner_classify.py` is imported here, never the
Ansible module, so the root lane stays import-safe without ansible-core.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from ansible_collections.tomazb.acm_switchover.plugins.module_utils import pod_owner_classify as collection
from lib.exceptions import SwitchoverError
from lib.validation import K8S_NAME_MAX_LENGTH, K8S_NAME_PATTERN
from modules.decommission_identity import EVIDENCE_SUMMARY_BY_REASON, capture_operator_identity, classify_pods
from tests.test_decommission_identity import (
    _CLASSIFIER_SKIPPED_VECTOR_ID,
    _CLASSIFIER_VECTORS,
    _CSV_GET_NOT_REACHED,
    ACM_NS,
    CAPTURED_AT,
    IDENTITY_CAPTURE_VECTORS,
    MCH_EXPECTED_UID,
    MCH_KEY,
    OWNER_CHAIN_VECTORS,
    _client_for_vector,
    _FakeIdentityClient,
    _identity_for,
    _render_csv,
    _render_pods,
)

_PYTHON_READ_KINDS = {
    "list_custom_resources_strict": ("list", "ClusterServiceVersion"),
    "get_custom_resource_strict": ("get", "ClusterServiceVersion"),
    "get_deployment_strict": ("get", "Deployment"),
    "get_replicaset_strict": ("get", "ReplicaSet"),
}


# --------------------------------------------------------------------------------------- adapters


def _camel_owner_references(references):
    """Render abstract snake_case owner references into the dynamic client's camelCase shape."""
    rendered = []
    for reference in references:
        if not isinstance(reference, dict):
            rendered.append(reference)  # malformed entries stay malformed on both sides
            continue
        rendered.append(
            {
                "apiVersion": reference["api_version"],
                "kind": reference["kind"],
                "name": reference["name"],
                "uid": reference["uid"],
                "controller": reference["controller"],
            }
        )
    return rendered


def _collection_pods(vector):
    pods = []
    for pod in vector["pods"]:
        metadata = {"name": pod["name"], "namespace": pod["namespace"]}
        if pod["owner_references"] is not None:  # the API omits the field on a bare Pod
            metadata["ownerReferences"] = _camel_owner_references(pod["owner_references"])
        pods.append({"metadata": metadata})
    return pods


class _CollectionOwnerChainReader:
    """A collection `read` callable answering one owner-chain vector with strict-read triples."""

    def __init__(self, vector):
        self.vector = vector
        self.deployment_reads = (
            [one_pass["recorded_deployment"] for one_pass in vector["passes"]]
            if vector["identity"] == "captured"
            else []
        )
        self.calls = []

    def __call__(self, read_mode, api_version, kind, resource_name, namespace=None, name=None):
        self.calls.append((read_mode, kind, name))
        if kind == "Deployment":
            index = sum(1 for call in self.calls if call[1] == "Deployment") - 1
            if index >= len(self.deployment_reads):
                raise AssertionError(f"{self.vector['id']}: unexpected Deployment read #{index}")
            spec = self.deployment_reads[index]
            if spec["read"] == "items":
                revision = f"rv-dep-{index}"
                body = {"metadata": {"name": name, "namespace": namespace, "uid": spec["uid"]}}
                body["metadata"]["resourceVersion"] = revision
                return "ok", [body], revision
            return ("not_found" if spec["read"] == "object_absent" else "error"), [], None
        if kind == "ReplicaSet":
            spec = self.vector["replicasets"].get(name)
            if spec is None or spec["read"] == "object_absent":
                return "not_found", [], None
            if spec["read"] == "error":
                return "error", [], None
            revision = f"rv-rs-{name}"
            metadata = {
                "name": name,
                "namespace": namespace,
                "uid": spec["uid"],
                "resourceVersion": revision,
                "ownerReferences": _camel_owner_references(spec["owner_references"]),
            }
            return "ok", [{"metadata": metadata}], revision
        raise AssertionError(f"{self.vector['id']}: unexpected read of {kind}")


class _CollectionCaptureReader:
    """A collection `read` callable answering one capture vector with strict-read triples."""

    def __init__(self, vector):
        self.vector = vector
        self.calls = []

    def __call__(self, read_mode, api_version, kind, resource_name, namespace=None, name=None):
        self.calls.append((read_mode, kind, name))
        if kind == "ClusterServiceVersion" and read_mode == "list":
            spec = self.vector["csv_list"]
            if spec["read"] == "items":
                return "ok", [_render_csv(csv) for csv in spec["items"]], "1"
            return ("kind_not_served" if spec["read"] == "crd_absent" else "error"), [], None
        if kind == "ClusterServiceVersion":
            spec = self.vector["csv_get"]
            if spec == _CSV_GET_NOT_REACHED:
                raise AssertionError(f"{self.vector['id']}: the named CSV read must not happen")
            if spec is None:
                matches = [csv for csv in self.vector["csv_list"].get("items", []) if csv["name"] == name]
                return ("ok", [_render_csv(matches[0])], "1") if matches else ("not_found", [], None)
            if spec["read"] == "items":
                return "ok", [_render_csv(spec["csv"])], "1"
            return ("not_found" if spec["read"] == "object_absent" else "error"), [], None
        if kind == "Deployment":
            spec = self.vector["deployment_get"]
            if spec is None:
                raise AssertionError(f"{self.vector['id']}: the Deployment read must not happen")
            if spec["read"] == "items":
                body = {"metadata": {"name": name, "namespace": namespace, "uid": spec["uid"], "resourceVersion": "1"}}
                return "ok", [body], "1"
            return ("not_found" if spec["read"] == "object_absent" else "error"), [], None
        raise AssertionError(f"{self.vector['id']}: unexpected read of {kind}")


def _collection_identity(vector):
    python_identity = _identity_for(vector)
    return {
        "operator_deployment": python_identity.operator_deployment,
        "operator_identity_unavailable": python_identity.operator_identity_unavailable,
    }


def _python_reads(client_calls):
    return [(_PYTHON_READ_KINDS[method][1], kwargs.get("name")) for method, kwargs in client_calls]


# ------------------------------------------------------------------------------------ row 18 core


@pytest.mark.parametrize("vector", _CLASSIFIER_VECTORS, ids=lambda v: v["id"])
def test_row18_python_and_collection_classifiers_agree_over_every_shared_vector(vector):
    """Matrix rows 1-17 (except row 13, which is not a classifier input) on both form factors."""
    python_client = _client_for_vector(vector)
    python_identity = _identity_for(vector)
    python_pods = _render_pods(vector["pods"])
    reader = _CollectionOwnerChainReader(vector)
    identity = _collection_identity(vector)
    pods = _collection_pods(vector)

    for index, one_pass in enumerate(vector["passes"]):
        python_calls_before = len(python_client.calls)
        collection_calls_before = len(reader.calls)
        python_result = classify_pods(python_client, python_pods, python_identity)
        collection_result = collection.classify_pods(reader, pods, identity, namespace=ACM_NS)

        python_decisions = [(d.name, d.decision) for d in python_result.decisions]
        collection_decisions = [(d["name"], d["decision"]) for d in collection_result["decisions"]]
        assert collection_decisions == python_decisions, (vector["id"], index)
        assert dict(collection_decisions) == one_pass["expected_decisions"], (vector["id"], index)
        assert collection_result["identity_status"] == python_result.identity_status == one_pass["expected_status"]
        assert collection_result["deployment_resource_version"] == python_result.operator_deployment_resource_version
        assert sum(1 for _, decision in collection_decisions if decision == "drain_blocking") == len(
            python_result.blocking
        )
        # Same objects read, in the same order, including the recorded-Deployment re-read and memoization.
        python_reads = _python_reads(python_client.calls[python_calls_before:])
        collection_reads = [(kind, name) for _, kind, name in reader.calls[collection_calls_before:]]
        assert collection_reads == python_reads, (vector["id"], index)


def test_row13_an_unreadable_pod_list_is_an_error_pass_never_an_empty_inventory():
    vector = next(v for v in OWNER_CHAIN_VECTORS if v["id"] == _CLASSIFIER_SKIPPED_VECTOR_ID)
    assert vector["pod_list_read"] == "error"

    def reader(read_mode, api_version, kind, resource_name, namespace=None, name=None):
        if kind == "Namespace":
            return "ok", [{"metadata": {"name": name, "resourceVersion": "ns-1"}}], "ns-1"
        if kind == "Pod":
            return "error", [], None
        raise AssertionError(f"no read of {kind} may follow an unreadable Pod list")

    result = collection.classify_pass(reader, _collection_identity(vector), namespace=ACM_NS)

    assert result["read_status"] == "error"
    assert result["decisions"] == [] and result["blocking_count"] is None


@pytest.mark.parametrize("vector", IDENTITY_CAPTURE_VECTORS, ids=lambda v: v["id"])
def test_python_and_collection_capture_agree_over_every_shared_vector(vector):
    python_client = _FakeIdentityClient(vector)
    reader = _CollectionCaptureReader(vector)
    binding = {"mch_teardown_key": MCH_KEY, "mch_expected_uid": MCH_EXPECTED_UID, "captured_at": CAPTURED_AT}

    if vector["expected"]["outcome"] == "fatal":
        with pytest.raises(SwitchoverError):
            capture_operator_identity(python_client, **binding)
        with pytest.raises(collection.IdentityCaptureError):
            collection.capture_identity(reader, namespace=ACM_NS, **binding)
    else:
        python_identity = capture_operator_identity(python_client, **binding)
        collection_identity = collection.capture_identity(reader, namespace=ACM_NS, **binding)
        assert collection_identity == {
            "operator_deployment": python_identity.operator_deployment,
            "operator_identity_unavailable": python_identity.operator_identity_unavailable,
        }
        if vector["expected"]["outcome"] == "operator_identity_unavailable":
            assert collection_identity["operator_identity_unavailable"]["reason"] == vector["expected"]["reason"]
    assert [(kind, name) for _, kind, name in reader.calls] == _python_reads(python_client.calls)


@pytest.mark.parametrize(
    "vector", [v for v in IDENTITY_CAPTURE_VECTORS if v["expected"]["outcome"] == "fatal"], ids=lambda v: v["id"]
)
def test_collection_fatal_capture_messages_are_sanitized(vector):
    reader = _CollectionCaptureReader(vector)
    with pytest.raises(collection.IdentityCaptureError) as raised:
        collection.capture_identity(
            reader,
            namespace=ACM_NS,
            mch_teardown_key=MCH_KEY,
            mch_expected_uid=MCH_EXPECTED_UID,
            captured_at=CAPTURED_AT,
        )
    message = str(raised.value).lower()
    for needle in ("bearer", "token", "kubeconfig", "http", "authorization", "-----begin"):
        assert needle not in message
    for csv in vector["csv_list"].get("items", []):
        for value in (csv["name"], csv["uid"]):
            assert not value or value.lower() not in message


# ------------------------------------------------------------------------------ mirrored knowledge


def test_the_evidence_summaries_are_mirrored_exactly():
    assert collection.EVIDENCE_SUMMARY_BY_REASON == EVIDENCE_SUMMARY_BY_REASON


def test_the_kubernetes_name_rule_is_mirrored_exactly():
    """Both classifiers refuse to read an owner or CSV whose name fails DNS-1123 (row 10)."""
    assert collection.K8S_NAME_PATTERN.pattern == K8S_NAME_PATTERN.pattern
    assert collection.K8S_NAME_MAX_LENGTH == K8S_NAME_MAX_LENGTH


def test_the_collection_decision_layer_is_import_safe_without_ansible():
    repo_root = Path(__file__).resolve().parent.parent
    code = (
        "import sys\n"
        "sys.modules['ansible'] = None\n"
        "import ansible_collections.tomazb.acm_switchover.plugins.module_utils.pod_owner_classify\n"
    )
    result = subprocess.run([sys.executable, "-c", code], cwd=str(repo_root), capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
