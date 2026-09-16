"""Shared MCH identity vector source (R4-03 PR E, plan §11C.3).

`OWNER_CHAIN_VECTORS` and `IDENTITY_CAPTURE_VECTORS` are the SINGLE shared
source of MCH operator-identity test data: plain, JSON-compatible dicts and
tuples with no production imports beyond `lib.constants`. They are consumed
by the Python tests in this module now, and will be consumed unchanged by
the Ansible collection classifier tests later (E5).

Each form factor (Python CLI, collection module) renders its own real API
shape (Kubernetes OwnerReference objects, CSV/Deployment resources, ...)
from this abstract topology. Parity between the two form factors compares
DECISIONS and REASON CODES ONLY -- never raw member spelling, field
ordering, or any other incidental shape of the rendered API objects.

This module must stay import-safe without ansible-core installed
(`test_vector_module_is_import_safe_without_ansible` enforces this), so it
imports nothing from the `ansible_collections` tree and nothing from
`modules.decommission_identity` (which does not exist yet -- that is E3).
"""

import json
import subprocess
import sys
from pathlib import Path

import lib.constants as py_constants

_MISSING = object()

# ---------------------------------------------------------------------------
# Shared literals (plan §11C.3)
# ---------------------------------------------------------------------------

ACM_NS = "open-cluster-management"
RECORDED_DEPLOYMENT_NAME = "multiclusterhub-operator"
RECORDED_DEPLOYMENT_UID = "uid-deploy-recorded"
MCH_CRD = "multiclusterhubs.operator.open-cluster-management.io"

# Local literals for the closed pod-classification vocabulary. These are
# checked against `lib.constants` by `test_classification_vocabulary_constants_have_approved_values`
# and by `test_owner_chain_expectations_use_the_closed_vocabulary` -- both via
# `getattr(py_constants, name, _MISSING)`, never by importing the names
# directly, so a missing constant fails an ASSERTION rather than an import.
_OPERATOR_OWNED = "operator_owned"
_DRAIN_BLOCKING = "drain_blocking"
_IDENTITY_UNAVAILABLE = "operator_identity_unavailable"
_IDENTITY_INCONSISTENT = "operator_identity_inconsistent"


# ---------------------------------------------------------------------------
# A. OWNER_CHAIN_VECTORS builders
# ---------------------------------------------------------------------------


def _owner_ref(*, api_version="apps/v1", kind="ReplicaSet", name="rs-primary", uid="uid-rs-primary", controller=True):
    return {"api_version": api_version, "kind": kind, "name": name, "uid": uid, "controller": controller}


def _deployment_owner_ref(*, name=RECORDED_DEPLOYMENT_NAME, uid=RECORDED_DEPLOYMENT_UID, controller=True):
    return _owner_ref(kind="Deployment", name=name, uid=uid, controller=controller)


def _pod(name, *, namespace=ACM_NS, owner_references):
    return {"name": name, "namespace": namespace, "owner_references": list(owner_references)}


def _rs_items(uid, owner_references):
    return {"read": "items", "uid": uid, "owner_references": list(owner_references)}


def _rs_absent():
    return {"read": "object_absent"}


def _rs_error():
    return {"read": "error"}


def _recorded_ok():
    return {"read": "items", "uid": RECORDED_DEPLOYMENT_UID}


def _recorded_absent():
    return {"read": "object_absent"}


def _recorded_error():
    return {"read": "error"}


def _recorded_replaced(uid="uid-deploy-replacement"):
    return {"read": "items", "uid": uid}


def _pass(recorded_deployment, expected_status, expected_decisions):
    return {
        "recorded_deployment": recorded_deployment,
        "expected_status": expected_status,
        "expected_decisions": dict(expected_decisions),
    }


def _vector(vector_id, matrix_row, *, identity, pod_list_read="items", pods=(), replicasets=None, passes=()):
    return {
        "id": vector_id,
        "matrix_row": matrix_row,
        "identity": identity,
        "pod_list_read": pod_list_read,
        "pods": list(pods),
        "replicasets": dict(replicasets or {}),
        "passes": list(passes),
    }


OWNER_CHAIN_VECTORS = (
    _vector(
        "row01_prefixed_pod_owned",
        1,
        identity="captured",
        pods=[
            _pod(
                "multiclusterhub-operator-abc123",
                owner_references=[_owner_ref(name="multiclusterhub-operator-7f9c8d", uid="uid-rs-1")],
            )
        ],
        replicasets={"multiclusterhub-operator-7f9c8d": _rs_items("uid-rs-1", [_deployment_owner_ref()])},
        passes=[_pass(_recorded_ok(), None, {"multiclusterhub-operator-abc123": _OPERATOR_OWNED})],
    ),
    _vector(
        "row01_zero_pods_verified_empty",
        1,
        identity="captured",
        pods=[],
        replicasets={},
        passes=[_pass(_recorded_ok(), None, {})],
    ),
    _vector(
        "row02_bare_pod_no_owner_refs",
        2,
        identity="captured",
        pods=[_pod("multiclusterhub-operator-bare", owner_references=[])],
        passes=[_pass(_recorded_ok(), None, {"multiclusterhub-operator-bare": _DRAIN_BLOCKING})],
    ),
    _vector(
        "row03_job_controller_blocking",
        3,
        identity="captured",
        pods=[
            _pod(
                "multiclusterhub-operator-job",
                owner_references=[_owner_ref(api_version="batch/v1", kind="Job", name="job-1", uid="uid-job-1")],
            )
        ],
        passes=[_pass(_recorded_ok(), None, {"multiclusterhub-operator-job": _DRAIN_BLOCKING})],
    ),
    _vector(
        "row04_statefulset_controller_blocking",
        4,
        identity="captured",
        pods=[
            _pod(
                "multiclusterhub-operator-sts",
                owner_references=[_owner_ref(kind="StatefulSet", name="sts-1", uid="uid-sts-1")],
            )
        ],
        passes=[_pass(_recorded_ok(), None, {"multiclusterhub-operator-sts": _DRAIN_BLOCKING})],
    ),
    _vector(
        "row05_rs_owned_by_other_deployment",
        5,
        identity="captured",
        pods=[
            _pod("multiclusterhub-operator-other", owner_references=[_owner_ref(name="other-rs", uid="uid-rs-other")])
        ],
        replicasets={
            "other-rs": _rs_items(
                "uid-rs-other", [_deployment_owner_ref(name="other-operator", uid="uid-deploy-other")]
            )
        },
        passes=[_pass(_recorded_ok(), None, {"multiclusterhub-operator-other": _DRAIN_BLOCKING})],
    ),
    _vector(
        "row06_dangling_deployment_uid",
        6,
        identity="captured",
        pods=[
            _pod(
                "multiclusterhub-operator-dangling",
                owner_references=[_owner_ref(name="rs-dangling", uid="uid-rs-dangling")],
            )
        ],
        replicasets={
            "rs-dangling": _rs_items("uid-rs-dangling", [_deployment_owner_ref(uid="uid-deploy-old")]),
        },
        passes=[_pass(_recorded_ok(), None, {"multiclusterhub-operator-dangling": _DRAIN_BLOCKING})],
    ),
    _vector(
        "row06_recorded_deployment_replaced",
        6,
        identity="captured",
        pods=[
            _pod(
                "multiclusterhub-operator-replaced",
                owner_references=[_owner_ref(name="rs-replaced", uid="uid-rs-replaced")],
            )
        ],
        replicasets={"rs-replaced": _rs_items("uid-rs-replaced", [_deployment_owner_ref()])},
        passes=[
            _pass(_recorded_replaced(), _IDENTITY_INCONSISTENT, {"multiclusterhub-operator-replaced": _DRAIN_BLOCKING})
        ],
    ),
    _vector(
        "row07_non_prefixed_pod_owned",
        7,
        identity="captured",
        pods=[_pod("acm-helper-xyz", owner_references=[_owner_ref(name="acm-helper-rs", uid="uid-rs-helper")])],
        replicasets={"acm-helper-rs": _rs_items("uid-rs-helper", [_deployment_owner_ref()])},
        passes=[_pass(_recorded_ok(), None, {"acm-helper-xyz": _OPERATOR_OWNED})],
    ),
    _vector(
        "row08_rolling_update_two_replicasets_owned",
        8,
        identity="captured",
        pods=[
            _pod("multiclusterhub-operator-pod-a", owner_references=[_owner_ref(name="rs-a", uid="uid-rs-a")]),
            _pod("multiclusterhub-operator-pod-b", owner_references=[_owner_ref(name="rs-b", uid="uid-rs-b")]),
        ],
        replicasets={
            "rs-a": _rs_items("uid-rs-a", [_deployment_owner_ref()]),
            "rs-b": _rs_items("uid-rs-b", [_deployment_owner_ref()]),
        },
        passes=[
            _pass(
                _recorded_ok(),
                None,
                {
                    "multiclusterhub-operator-pod-a": _OPERATOR_OWNED,
                    "multiclusterhub-operator-pod-b": _OPERATOR_OWNED,
                },
            )
        ],
    ),
    _vector(
        "row08_mixed_chains_partial_owned",
        8,
        identity="captured",
        pods=[
            _pod("multiclusterhub-operator-pod-c", owner_references=[_owner_ref(name="rs-c", uid="uid-rs-c")]),
            _pod("multiclusterhub-operator-pod-d", owner_references=[_owner_ref(name="rs-d", uid="uid-rs-d")]),
        ],
        replicasets={
            "rs-c": _rs_items("uid-rs-c", [_deployment_owner_ref()]),
            "rs-d": _rs_items("uid-rs-d", [_deployment_owner_ref(name="other-operator", uid="uid-deploy-other")]),
        },
        passes=[
            _pass(
                _recorded_ok(),
                None,
                {
                    "multiclusterhub-operator-pod-c": _OPERATOR_OWNED,
                    "multiclusterhub-operator-pod-d": _DRAIN_BLOCKING,
                },
            )
        ],
    ),
    _vector(
        "row09_no_controller_owner_ref",
        9,
        identity="captured",
        pods=[
            _pod(
                "multiclusterhub-operator-nc",
                owner_references=[
                    _owner_ref(name="rs-nc1", uid="uid-rs-nc1", controller=False),
                    _owner_ref(name="rs-nc2", uid="uid-rs-nc2", controller=None),
                ],
            )
        ],
        passes=[_pass(_recorded_ok(), None, {"multiclusterhub-operator-nc": _DRAIN_BLOCKING})],
    ),
    _vector(
        "row10_pod_two_controller_refs",
        10,
        identity="captured",
        pods=[
            _pod(
                "multiclusterhub-operator-tworef",
                owner_references=[
                    _owner_ref(name="rs-tworef-a", uid="uid-rs-tworef-a"),
                    _owner_ref(api_version="batch/v1", kind="Job", name="job-tworef", uid="uid-job-tworef"),
                ],
            )
        ],
        passes=[_pass(_recorded_ok(), None, {"multiclusterhub-operator-tworef": _DRAIN_BLOCKING})],
    ),
    _vector(
        "row10_controller_ref_empty_uid",
        10,
        identity="captured",
        pods=[_pod("multiclusterhub-operator-emptyuid", owner_references=[_owner_ref(name="rs-emptyuid", uid="")])],
        passes=[_pass(_recorded_ok(), None, {"multiclusterhub-operator-emptyuid": _DRAIN_BLOCKING})],
    ),
    _vector(
        "row10_controller_ref_wrong_api_version",
        10,
        identity="captured",
        pods=[
            _pod(
                "multiclusterhub-operator-oldapi",
                owner_references=[
                    _owner_ref(
                        api_version="extensions/v1beta1", kind="ReplicaSet", name="rs-oldapi", uid="uid-rs-oldapi"
                    )
                ],
            )
        ],
        replicasets={"rs-oldapi": _rs_items("uid-rs-oldapi", [_deployment_owner_ref()])},
        passes=[_pass(_recorded_ok(), None, {"multiclusterhub-operator-oldapi": _DRAIN_BLOCKING})],
    ),
    _vector(
        "row10_non_mapping_owner_ref_entry",
        10,
        identity="captured",
        pods=[_pod("multiclusterhub-operator-nonmapping", owner_references=["not-a-mapping"])],
        passes=[_pass(_recorded_ok(), None, {"multiclusterhub-operator-nonmapping": _DRAIN_BLOCKING})],
    ),
    _vector(
        "row10_rs_no_controller_owner",
        10,
        identity="captured",
        pods=[_pod("multiclusterhub-operator-rsnc", owner_references=[_owner_ref(name="rs-nc", uid="uid-rs-nc")])],
        replicasets={"rs-nc": _rs_items("uid-rs-nc", [_deployment_owner_ref(controller=False)])},
        passes=[_pass(_recorded_ok(), None, {"multiclusterhub-operator-rsnc": _DRAIN_BLOCKING})],
    ),
    _vector(
        "row10_rs_two_controller_owners",
        10,
        identity="captured",
        pods=[_pod("multiclusterhub-operator-rstc", owner_references=[_owner_ref(name="rs-tc", uid="uid-rs-tc")])],
        replicasets={
            "rs-tc": _rs_items(
                "uid-rs-tc",
                [
                    _deployment_owner_ref(),
                    _deployment_owner_ref(name="other-operator", uid="uid-deploy-other"),
                ],
            )
        },
        passes=[_pass(_recorded_ok(), None, {"multiclusterhub-operator-rstc": _DRAIN_BLOCKING})],
    ),
    _vector(
        "row10_rs_controller_kind_statefulset",
        10,
        identity="captured",
        pods=[_pod("multiclusterhub-operator-rssts", owner_references=[_owner_ref(name="rs-sts", uid="uid-rs-sts")])],
        replicasets={
            "rs-sts": _rs_items(
                "uid-rs-sts",
                [_owner_ref(kind="StatefulSet", name=RECORDED_DEPLOYMENT_NAME, uid=RECORDED_DEPLOYMENT_UID)],
            )
        },
        passes=[_pass(_recorded_ok(), None, {"multiclusterhub-operator-rssts": _DRAIN_BLOCKING})],
    ),
    _vector(
        "row10_pod_namespace_other_namespace",
        10,
        identity="captured",
        pods=[
            _pod(
                "multiclusterhub-operator-otherns",
                namespace="other-namespace",
                owner_references=[_owner_ref(name="rs-otherns", uid="uid-rs-otherns")],
            )
        ],
        replicasets={"rs-otherns": _rs_items("uid-rs-otherns", [_deployment_owner_ref()])},
        passes=[_pass(_recorded_ok(), None, {"multiclusterhub-operator-otherns": _DRAIN_BLOCKING})],
    ),
    _vector(
        # The Pod's own controller ref to the ReplicaSet carries the invalid DNS-1123
        # name -- not the RS's ref to the Deployment. A classifier under test must
        # reject this before ever reading the "Bad Name!" ReplicaSet: no entry for it
        # exists in `replicasets`, so a lookup by that name proves a bug.
        "row10_pod_rs_ref_name_invalid_dns",
        10,
        identity="captured",
        pods=[
            _pod(
                "multiclusterhub-operator-badname",
                owner_references=[_owner_ref(name="Bad Name!", uid="uid-rs-badname")],
            )
        ],
        replicasets={},
        passes=[_pass(_recorded_ok(), None, {"multiclusterhub-operator-badname": _DRAIN_BLOCKING})],
    ),
    _vector(
        "row11_rs_object_absent",
        11,
        identity="captured",
        pods=[
            _pod(
                "multiclusterhub-operator-rsabsent",
                owner_references=[_owner_ref(name="rs-absent", uid="uid-rs-absent-ref")],
            )
        ],
        replicasets={"rs-absent": _rs_absent()},
        passes=[_pass(_recorded_ok(), None, {"multiclusterhub-operator-rsabsent": _DRAIN_BLOCKING})],
    ),
    _vector(
        "row11_rs_read_error",
        11,
        identity="captured",
        pods=[
            _pod(
                "multiclusterhub-operator-rserror",
                owner_references=[_owner_ref(name="rs-error", uid="uid-rs-error-ref")],
            )
        ],
        replicasets={"rs-error": _rs_error()},
        passes=[_pass(_recorded_ok(), None, {"multiclusterhub-operator-rserror": _DRAIN_BLOCKING})],
    ),
    _vector(
        "row11_rs_uid_mismatch",
        11,
        identity="captured",
        pods=[
            _pod(
                "multiclusterhub-operator-rsmismatch",
                owner_references=[_owner_ref(name="rs-mismatch", uid="uid-rs-ref-value")],
            )
        ],
        replicasets={"rs-mismatch": _rs_items("uid-rs-actual-value", [_deployment_owner_ref()])},
        passes=[_pass(_recorded_ok(), None, {"multiclusterhub-operator-rsmismatch": _DRAIN_BLOCKING})],
    ),
    _vector(
        "row12_recorded_deployment_object_absent",
        12,
        identity="captured",
        pods=[
            _pod(
                "multiclusterhub-operator-rec-absent",
                owner_references=[_owner_ref(name="rs-rec-absent", uid="uid-rs-rec-absent")],
            )
        ],
        replicasets={"rs-rec-absent": _rs_items("uid-rs-rec-absent", [_deployment_owner_ref()])},
        passes=[
            _pass(_recorded_absent(), _IDENTITY_INCONSISTENT, {"multiclusterhub-operator-rec-absent": _DRAIN_BLOCKING})
        ],
    ),
    _vector(
        "row12_recorded_deployment_read_error",
        12,
        identity="captured",
        pods=[
            _pod(
                "multiclusterhub-operator-rec-error",
                owner_references=[_owner_ref(name="rs-rec-error", uid="uid-rs-rec-error")],
            )
        ],
        replicasets={"rs-rec-error": _rs_items("uid-rs-rec-error", [_deployment_owner_ref()])},
        passes=[
            _pass(_recorded_error(), _IDENTITY_INCONSISTENT, {"multiclusterhub-operator-rec-error": _DRAIN_BLOCKING})
        ],
    ),
    _vector(
        "row12_recorded_deployment_uid_replaced",
        12,
        identity="captured",
        pods=[
            _pod(
                "multiclusterhub-operator-rec-replaced",
                owner_references=[_owner_ref(name="rs-rec-replaced", uid="uid-rs-rec-replaced")],
            )
        ],
        replicasets={"rs-rec-replaced": _rs_items("uid-rs-rec-replaced", [_deployment_owner_ref()])},
        passes=[
            _pass(
                _recorded_replaced(),
                _IDENTITY_INCONSISTENT,
                {"multiclusterhub-operator-rec-replaced": _DRAIN_BLOCKING},
            )
        ],
    ),
    _vector(
        "row12_zero_pods_recorded_deployment_absent",
        12,
        identity="captured",
        pods=[],
        replicasets={},
        passes=[_pass(_recorded_absent(), _IDENTITY_INCONSISTENT, {})],
    ),
    _vector(
        "row13_pod_list_read_error",
        13,
        identity="captured",
        pod_list_read="error",
        pods=[],
        replicasets={},
        passes=[],
    ),
    _vector(
        "row14_unavailable_identity_zero_pods",
        14,
        identity="unavailable",
        pods=[],
        replicasets={},
        passes=[_pass(None, _IDENTITY_UNAVAILABLE, {})],
    ),
    _vector(
        "row15_unavailable_identity_valid_chain_pod",
        15,
        identity="unavailable",
        pods=[
            _pod(
                "multiclusterhub-operator-wouldbe",
                owner_references=[_owner_ref(name="rs-wouldbe", uid="uid-rs-wouldbe")],
            )
        ],
        replicasets={"rs-wouldbe": _rs_items("uid-rs-wouldbe", [_deployment_owner_ref()])},
        passes=[_pass(None, _IDENTITY_UNAVAILABLE, {"multiclusterhub-operator-wouldbe": _DRAIN_BLOCKING})],
    ),
    _vector(
        "row16_two_passes_deployment_replaced_mid_stream",
        16,
        identity="captured",
        pods=[
            _pod(
                "multiclusterhub-operator-twopass",
                owner_references=[_owner_ref(name="rs-twopass", uid="uid-rs-twopass")],
            )
        ],
        replicasets={"rs-twopass": _rs_items("uid-rs-twopass", [_deployment_owner_ref()])},
        passes=[
            _pass(_recorded_ok(), None, {"multiclusterhub-operator-twopass": _OPERATOR_OWNED}),
            _pass(_recorded_replaced(), _IDENTITY_INCONSISTENT, {"multiclusterhub-operator-twopass": _DRAIN_BLOCKING}),
        ],
    ),
    _vector(
        "row17_prefix_irrelevant_both_job_owned",
        17,
        identity="captured",
        pods=[
            _pod(
                "job-worker-abc",
                owner_references=[_owner_ref(api_version="batch/v1", kind="Job", name="job-1", uid="uid-job-1")],
            ),
            _pod(
                "multiclusterhub-operator-abc",
                owner_references=[_owner_ref(api_version="batch/v1", kind="Job", name="job-2", uid="uid-job-2")],
            ),
        ],
        passes=[
            _pass(
                _recorded_ok(),
                None,
                {
                    "job-worker-abc": _DRAIN_BLOCKING,
                    "multiclusterhub-operator-abc": _DRAIN_BLOCKING,
                },
            )
        ],
    ),
)


# ---------------------------------------------------------------------------
# B. IDENTITY_CAPTURE_VECTORS builders
# ---------------------------------------------------------------------------


def _csv(**overrides):
    """A fresh, deep-copy-safe CSV dict; no aliasing across calls or vectors."""
    csv = {
        "name": "advanced-cluster-management.v2.13.0",
        "uid": "uid-csv-acm",
        "phase": "Succeeded",
        "owned_crds": [MCH_CRD],
        "install_strategy": "deployment",
        "install_deployments": [RECORDED_DEPLOYMENT_NAME],
    }
    csv.update(overrides)
    return csv


def _csv_list_items(*csvs):
    return {"read": "items", "items": list(csvs)}


def _csv_list_crd_absent():
    return {"read": "crd_absent"}


def _csv_list_error():
    return {"read": "error"}


def _csv_get_items(csv):
    return {"read": "items", "csv": csv}


def _csv_get_absent():
    return {"read": "object_absent"}


def _csv_get_error():
    return {"read": "error"}


# `csv_get` carries exactly one meaning per value, unlike the brief's original
# schema comment (which conflated two): `None` means the named GET was attempted
# and returned the selected candidate unchanged; `{"read": "not_reached"}` means
# the classification concluded (fatal or operator_identity_unavailable) before the
# named GET was ever attempted, so no client call for it should occur. The two
# must never be interchanged -- a mock built strictly from `None` == "unchanged"
# would wrongly configure a passthrough response for a GET that is never issued.
_CSV_GET_NOT_REACHED = {"read": "not_reached"}


def _csv_get_not_reached():
    return dict(_CSV_GET_NOT_REACHED)


def _deployment_get_items(uid):
    return {"read": "items", "uid": uid}


def _deployment_get_absent():
    return {"read": "object_absent"}


def _deployment_get_error():
    return {"read": "error"}


def _outcome_operator_deployment():
    return {"outcome": "operator_deployment"}


def _outcome_unavailable(reason):
    return {"outcome": "operator_identity_unavailable", "reason": reason}


def _outcome_fatal():
    return {"outcome": "fatal"}


def _capture_vector(vector_id, *, csv_list, csv_get=None, deployment_get=None, expected):
    return {
        "id": vector_id,
        "csv_list": csv_list,
        "csv_get": csv_get,
        "deployment_get": deployment_get,
        "expected": expected,
    }


IDENTITY_CAPTURE_VECTORS = (
    _capture_vector(
        "capture_valid_identity",
        csv_list=_csv_list_items(_csv()),
        deployment_get=_deployment_get_items("uid-deploy-captured"),
        expected=_outcome_operator_deployment(),
    ),
    _capture_vector(
        "capture_csv_kind_not_served",
        csv_list=_csv_list_crd_absent(),
        csv_get=_csv_get_not_reached(),
        expected=_outcome_unavailable("csv_absent"),
    ),
    _capture_vector(
        "capture_zero_csvs",
        csv_list=_csv_list_items(),
        csv_get=_csv_get_not_reached(),
        expected=_outcome_unavailable("csv_absent"),
    ),
    _capture_vector(
        "capture_csv_owns_other_crd_only",
        csv_list=_csv_list_items(_csv(owned_crds=["someothercrd.example.io"])),
        csv_get=_csv_get_not_reached(),
        expected=_outcome_unavailable("csv_owned_crd_mismatch"),
    ),
    _capture_vector(
        "capture_csv_owned_crds_null",
        csv_list=_csv_list_items(_csv(owned_crds=None)),
        csv_get=_csv_get_not_reached(),
        expected=_outcome_unavailable("csv_owned_crd_mismatch"),
    ),
    _capture_vector(
        "capture_csv_phase_installing",
        csv_list=_csv_list_items(_csv(phase="Installing")),
        csv_get=_csv_get_not_reached(),
        expected=_outcome_unavailable("csv_not_succeeded"),
    ),
    _capture_vector(
        "capture_two_succeeded_owning_csvs",
        csv_list=_csv_list_items(
            _csv(name="advanced-cluster-management.v2.13.0-a", uid="uid-csv-a"),
            _csv(name="advanced-cluster-management.v2.13.0-b", uid="uid-csv-b"),
        ),
        csv_get=_csv_get_not_reached(),
        expected=_outcome_unavailable("csv_ambiguous"),
    ),
    _capture_vector(
        "capture_replacing_and_succeeded_owning",
        csv_list=_csv_list_items(
            _csv(name="advanced-cluster-management.v2.12.0", uid="uid-csv-old", phase="Replacing"),
            _csv(),
        ),
        deployment_get=_deployment_get_items("uid-deploy-captured"),
        expected=_outcome_operator_deployment(),
    ),
    _capture_vector(
        "capture_csv_list_error",
        csv_list=_csv_list_error(),
        csv_get=_csv_get_not_reached(),
        expected=_outcome_fatal(),
    ),
    _capture_vector(
        "capture_selected_csv_empty_uid",
        csv_list=_csv_list_items(_csv(uid="")),
        csv_get=_csv_get_not_reached(),
        expected=_outcome_fatal(),
    ),
    _capture_vector(
        "capture_selected_csv_invalid_name",
        csv_list=_csv_list_items(_csv(name="Bad Name!")),
        csv_get=_csv_get_not_reached(),
        expected=_outcome_fatal(),
    ),
    _capture_vector(
        "capture_csv_get_error",
        csv_list=_csv_list_items(_csv()),
        csv_get=_csv_get_error(),
        expected=_outcome_fatal(),
    ),
    _capture_vector(
        "capture_csv_get_object_absent",
        csv_list=_csv_list_items(_csv()),
        csv_get=_csv_get_absent(),
        expected=_outcome_unavailable("csv_absent"),
    ),
    _capture_vector(
        "capture_csv_get_uid_changed",
        csv_list=_csv_list_items(_csv()),
        csv_get=_csv_get_items(_csv(uid="uid-csv-changed")),
        expected=_outcome_unavailable("csv_ambiguous"),
    ),
    _capture_vector(
        "capture_csv_get_phase_failed",
        csv_list=_csv_list_items(_csv()),
        csv_get=_csv_get_items(_csv(phase="Failed")),
        expected=_outcome_unavailable("csv_not_succeeded"),
    ),
    _capture_vector(
        "capture_csv_get_owned_crd_changed",
        csv_list=_csv_list_items(_csv()),
        csv_get=_csv_get_items(_csv(owned_crds=["other-crd.example.io"])),
        expected=_outcome_unavailable("csv_owned_crd_mismatch"),
    ),
    _capture_vector(
        "capture_install_strategy_unknown",
        csv_list=_csv_list_items(_csv()),
        csv_get=_csv_get_items(_csv(install_strategy="unknown")),
        expected=_outcome_unavailable("install_deployment_absent"),
    ),
    _capture_vector(
        "capture_install_deployments_null",
        csv_list=_csv_list_items(_csv()),
        csv_get=_csv_get_items(_csv(install_deployments=None)),
        expected=_outcome_unavailable("install_deployment_absent"),
    ),
    _capture_vector(
        "capture_install_deployments_empty",
        csv_list=_csv_list_items(_csv()),
        csv_get=_csv_get_items(_csv(install_deployments=[])),
        expected=_outcome_unavailable("install_deployment_absent"),
    ),
    _capture_vector(
        "capture_install_deployments_ambiguous",
        csv_list=_csv_list_items(_csv()),
        csv_get=_csv_get_items(_csv(install_deployments=[RECORDED_DEPLOYMENT_NAME, "other-deployment"])),
        expected=_outcome_unavailable("install_deployment_ambiguous"),
    ),
    _capture_vector(
        "capture_install_deployment_invalid_name",
        csv_list=_csv_list_items(_csv()),
        csv_get=_csv_get_items(_csv(install_deployments=["Bad Name!"])),
        expected=_outcome_unavailable("install_deployment_absent"),
    ),
    _capture_vector(
        "capture_deployment_get_object_absent",
        csv_list=_csv_list_items(_csv()),
        deployment_get=_deployment_get_absent(),
        expected=_outcome_unavailable("install_deployment_absent"),
    ),
    _capture_vector(
        "capture_deployment_get_error",
        csv_list=_csv_list_items(_csv()),
        deployment_get=_deployment_get_error(),
        expected=_outcome_unavailable("deployment_read_failed"),
    ),
    _capture_vector(
        "capture_deployment_get_empty_uid",
        csv_list=_csv_list_items(_csv()),
        deployment_get=_deployment_get_items(""),
        expected=_outcome_unavailable("deployment_identity_incomplete"),
    ),
)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

_APPROVED_POD_CLASSIFICATION_VALUES = {
    "POD_CLASSIFICATION_OPERATOR_OWNED": _OPERATOR_OWNED,
    "POD_CLASSIFICATION_DRAIN_BLOCKING": _DRAIN_BLOCKING,
    "POD_CLASSIFICATION_IDENTITY_UNAVAILABLE": _IDENTITY_UNAVAILABLE,
    "POD_CLASSIFICATION_IDENTITY_INCONSISTENT": _IDENTITY_INCONSISTENT,
}


def test_classification_vocabulary_constants_have_approved_values():
    mismatches = []
    for name, approved in _APPROVED_POD_CLASSIFICATION_VALUES.items():
        actual = getattr(py_constants, name, _MISSING)
        if actual != approved:
            mismatches.append(f"{name}={actual!r} (expected {approved!r})")
    assert not mismatches, "Classification vocabulary drift:\n  " + "\n  ".join(mismatches)


def test_owner_chain_vector_ids_are_unique():
    ids = [v["id"] for v in OWNER_CHAIN_VECTORS]
    duplicates = sorted({vid for vid in ids if ids.count(vid) > 1})
    assert len(ids) == len(set(ids)), f"Duplicate owner-chain vector ids: {duplicates}"


def test_capture_vector_ids_are_unique():
    ids = [v["id"] for v in IDENTITY_CAPTURE_VECTORS]
    duplicates = sorted({vid for vid in ids if ids.count(vid) > 1})
    assert len(ids) == len(set(ids)), f"Duplicate capture vector ids: {duplicates}"


def test_vector_ids_are_unique_across_both_sets():
    owner_ids = {v["id"] for v in OWNER_CHAIN_VECTORS}
    capture_ids = {v["id"] for v in IDENTITY_CAPTURE_VECTORS}
    overlap = sorted(owner_ids & capture_ids)
    assert not overlap, f"ids shared between owner-chain and capture vectors: {overlap}"


def test_owner_chain_vectors_cover_matrix_rows_1_to_17():
    rows = {v["matrix_row"] for v in OWNER_CHAIN_VECTORS}
    assert rows == set(range(1, 18)), f"matrix rows covered: {sorted(rows)}"


def test_owner_chain_expectations_use_the_closed_vocabulary():
    decision_values = {
        getattr(py_constants, "POD_CLASSIFICATION_OPERATOR_OWNED", _MISSING),
        getattr(py_constants, "POD_CLASSIFICATION_DRAIN_BLOCKING", _MISSING),
    }
    status_values = {
        None,
        getattr(py_constants, "POD_CLASSIFICATION_IDENTITY_UNAVAILABLE", _MISSING),
        getattr(py_constants, "POD_CLASSIFICATION_IDENTITY_INCONSISTENT", _MISSING),
    }
    violations = []
    for vector in OWNER_CHAIN_VECTORS:
        pod_names = {pod["name"] for pod in vector["pods"]}
        identity_unavailable = vector["identity"] == "unavailable"

        if vector["pod_list_read"] == "error":
            if vector["passes"] != []:
                violations.append(f"{vector['id']}: pod_list_read=error but passes is not empty")
            continue

        if vector["passes"] == []:
            violations.append(f"{vector['id']}: pod_list_read={vector['pod_list_read']!r} but passes is empty")
            continue

        for i, one_pass in enumerate(vector["passes"]):
            status = one_pass["expected_status"]
            if status not in status_values:
                violations.append(f"{vector['id']}[{i}]: expected_status {status!r} not in closed vocabulary")

            decisions = one_pass["expected_decisions"]
            for pod_name, decision in decisions.items():
                if decision not in decision_values:
                    violations.append(
                        f"{vector['id']}[{i}]: decision for {pod_name!r} = {decision!r} not in closed vocabulary"
                    )
            if set(decisions.keys()) != pod_names:
                violations.append(
                    f"{vector['id']}[{i}]: expected_decisions keys {sorted(decisions)} != pod names {sorted(pod_names)}"
                )

            recorded_is_none = one_pass["recorded_deployment"] is None
            if recorded_is_none != identity_unavailable:
                violations.append(
                    f"{vector['id']}[{i}]: recorded_deployment is None={recorded_is_none} "
                    f"but identity=={vector['identity']!r}"
                )

    assert not violations, "Owner-chain vector vocabulary violations:\n  " + "\n  ".join(violations)


def test_capture_expectations_use_the_closed_reason_vocabulary():
    reasons = getattr(py_constants, "OPERATOR_IDENTITY_UNAVAILABLE_REASONS", _MISSING)
    assert reasons is not _MISSING, "lib.constants.OPERATOR_IDENTITY_UNAVAILABLE_REASONS is missing"

    outcomes = {"operator_deployment", "operator_identity_unavailable", "fatal"}
    seen_reasons = set()
    violations = []
    for vector in IDENTITY_CAPTURE_VECTORS:
        expected = vector["expected"]
        outcome = expected["outcome"]
        if outcome not in outcomes:
            violations.append(f"{vector['id']}: outcome {outcome!r} not in {sorted(outcomes)}")
            continue
        if outcome == "operator_identity_unavailable":
            reason = expected.get("reason")
            if reason not in reasons:
                violations.append(f"{vector['id']}: reason {reason!r} not in OPERATOR_IDENTITY_UNAVAILABLE_REASONS")
            else:
                seen_reasons.add(reason)

    missing = set(reasons) - seen_reasons if reasons is not _MISSING else set()
    assert not violations, "Capture vector vocabulary violations:\n  " + "\n  ".join(violations)
    assert not missing, f"Reasons never exercised by a capture vector: {sorted(missing)}"


# Vectors whose `csv_list` already reads "items" (so the failure isn't visible from
# `csv_list` alone) but whose classification concludes before the named CSV GET per
# the brief's evaluation order, steps 1-4: zero CSVs / no owning CSV / no Succeeded
# owning CSV / more than one Succeeded owning CSV / the selected candidate itself
# being unusable (empty uid or an invalid name). Deliberately excludes
# capture_csv_kind_not_served and capture_csv_list_error -- those are already
# provable from `csv_list["read"] != "items"` below, so listing them here too would
# make the check tautological rather than an independent cross-check.
_PRE_CSV_GET_ITEMS_VECTOR_IDS = frozenset(
    {
        "capture_zero_csvs",
        "capture_csv_owns_other_crd_only",
        "capture_csv_owned_crds_null",
        "capture_csv_phase_installing",
        "capture_two_succeeded_owning_csvs",
        "capture_selected_csv_empty_uid",
        "capture_selected_csv_invalid_name",
    }
)


def test_capture_csv_get_not_reached_matches_pre_get_failures():
    """`csv_get` is the `not_reached` sentinel iff the named CSV GET is never issued.

    Structural half: `csv_list["read"] != "items"` (crd_absent / error) always means
    no candidate was ever selected, so the GET cannot have been attempted.
    Explicit half: `csv_list["read"] == "items"` but classification concludes on the
    listed CSVs themselves (steps 1-4), named by `_PRE_CSV_GET_ITEMS_VECTOR_IDS`.
    Every other vector must show the GET as attempted (`None`, or an explicit
    items/object_absent/error outcome) -- never the sentinel.
    """
    violations = []
    for vector in IDENTITY_CAPTURE_VECTORS:
        is_not_reached = vector["csv_get"] == _CSV_GET_NOT_REACHED
        csv_list_is_items = vector["csv_list"].get("read") == "items"
        should_be_not_reached = (not csv_list_is_items) or (vector["id"] in _PRE_CSV_GET_ITEMS_VECTOR_IDS)
        if is_not_reached != should_be_not_reached:
            violations.append(
                f"{vector['id']}: csv_get not_reached={is_not_reached} but expected {should_be_not_reached}"
            )
    assert not violations, "csv_get not_reached sentinel drift:\n  " + "\n  ".join(violations)


def test_vectors_are_json_compatible():
    for vector in OWNER_CHAIN_VECTORS:
        assert json.loads(json.dumps(vector)) == vector, f"not JSON round-trip stable: {vector['id']}"
    for vector in IDENTITY_CAPTURE_VECTORS:
        assert json.loads(json.dumps(vector)) == vector, f"not JSON round-trip stable: {vector['id']}"


_FORBIDDEN_SUBSTRINGS = (
    "bearer",
    "authorization",
    "kubeconfig",
    "token",
    "-----begin",
    "http://",
    "https://",
    "password",
    "client-certificate",
)


def test_vectors_carry_no_credential_or_raw_api_material():
    dumped = (json.dumps(OWNER_CHAIN_VECTORS) + json.dumps(IDENTITY_CAPTURE_VECTORS)).lower()
    hits = [needle for needle in _FORBIDDEN_SUBSTRINGS if needle in dumped]
    assert not hits, f"Forbidden substrings present in vectors: {hits}"


def test_vector_module_is_import_safe_without_ansible():
    """This module must import cleanly even when ansible-core is unavailable."""
    repo_root = Path(__file__).resolve().parent.parent
    code = "import sys\n" "sys.modules['ansible'] = None\n" "import tests.test_decommission_identity\n"
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
