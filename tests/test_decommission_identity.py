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
imports nothing from the `ansible_collections` tree. `OWNER_CHAIN_VECTORS` and
`IDENTITY_CAPTURE_VECTORS` themselves still import nothing from
`modules.decommission_identity`, keeping them implementation-invariant; the
task E2 tests below (the Python capture producer) do import it, and that
module has no ansible-core dependency either, so import safety still holds.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

import lib.constants as py_constants
from lib.constants import (
    OPERATOR_IDENTITY_DISCOVERY_METHOD,
    OPERATOR_IDENTITY_UNAVAILABLE_REASONS,
    STRICT_READ_REASON_KIND_NOT_SERVED,
    STRICT_READ_REASON_OBJECT_NOT_FOUND,
    STRICT_READ_REASON_READ_FAILED,
)
from lib.exceptions import SwitchoverError, ValidationError
from lib.strict_read import StrictReadOutcome
from lib.teardown_record import (
    MCH_OWNED_CRD,
    MalformedTeardownRecord,
    TeardownPhase,
    TeardownRecord,
    teardown_key,
    to_stored,
    validate,
    validate_stored,
)
from modules.decommission_identity import EVIDENCE_SUMMARY_BY_REASON, OperatorIdentity, capture_operator_identity

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


# ---------------------------------------------------------------------------
# E2. Python operator-identity capture producer (modules/decommission_identity.py)
# ---------------------------------------------------------------------------
#
# `_FakeIdentityClient` renders each `IDENTITY_CAPTURE_VECTORS` entry's abstract
# `csv_list` / `csv_get` / `deployment_get` shapes into the real API shapes the
# brief specifies, and records every call as `(method, kwargs)` so tests can
# assert both the outcome and the exact request provenance.

MCH_KEY = teardown_key(
    "operator.open-cluster-management.io/v1", "MultiClusterHub", "open-cluster-management", "multiclusterhub"
)
MCH_EXPECTED_UID = "uid-mch"
CAPTURED_AT = "2026-09-16T00:00:00Z"

_SANITIZATION_FORBIDDEN_SUBSTRINGS = (
    "bearer",
    "token",
    "kubeconfig",
    "http",
    "authorization",
    "-----begin",
)


def _find_capture_vector(vector_id):
    for vector in IDENTITY_CAPTURE_VECTORS:
        if vector["id"] == vector_id:
            return vector
    raise AssertionError(f"no such IDENTITY_CAPTURE_VECTORS id: {vector_id!r}")


def _render_csv(csv):
    """Render one abstract `_csv()` dict into the real camelCase CSV resource shape."""
    owned_crds = csv["owned_crds"]
    owned = None if owned_crds is None else [{"name": crd} for crd in owned_crds]
    install_deployments = csv["install_deployments"]
    deployments = None if install_deployments is None else [{"name": name} for name in install_deployments]
    return {
        "metadata": {"name": csv["name"], "uid": csv["uid"], "resourceVersion": "1"},
        "spec": {
            "customresourcedefinitions": {"owned": owned},
            "install": {"strategy": csv["install_strategy"], "spec": {"deployments": deployments}},
        },
        "status": {"phase": csv["phase"]},
    }


def _select_unchanged_csv(csv_list_spec, vector_id):
    """The candidate a correct implementation would select from `csv_list["items"]`.

    Used only to render a `csv_get: null` ("the named GET returns the selected LIST
    candidate unchanged") response -- never to decide the vector's expected outcome.
    """
    items = csv_list_spec["items"]
    candidates = [
        item
        for item in items
        if isinstance(item.get("owned_crds"), list)
        and MCH_CRD in item["owned_crds"]
        and item.get("phase") == "Succeeded"
    ]
    assert len(candidates) == 1, f"{vector_id}: ambiguous unchanged-CSV selection: {candidates}"
    return candidates[0]


class _FakeIdentityClient:
    """Records every strict-read call; renders vector shapes into real API shapes."""

    def __init__(self, vector):
        self.vector = vector
        self.calls = []

    def list_custom_resources_strict(self, *, group, version, plural, namespace):
        self.calls.append(
            (
                "list_custom_resources_strict",
                {"group": group, "version": version, "plural": plural, "namespace": namespace},
            )
        )
        spec = self.vector["csv_list"]
        read = spec["read"]
        if read == "items":
            items = [_render_csv(csv) for csv in spec["items"]]
            return StrictReadOutcome.from_items(items, resource_version="1")
        if read == "crd_absent":
            return StrictReadOutcome.crd_absent(STRICT_READ_REASON_KIND_NOT_SERVED)
        if read == "error":
            return StrictReadOutcome.error(STRICT_READ_REASON_READ_FAILED)
        raise AssertionError(f"{self.vector['id']}: unknown csv_list read {read!r}")

    def get_custom_resource_strict(self, *, group, version, plural, name, namespace):
        self.calls.append(
            (
                "get_custom_resource_strict",
                {"group": group, "version": version, "plural": plural, "name": name, "namespace": namespace},
            )
        )
        spec = self.vector["csv_get"]
        if spec == _CSV_GET_NOT_REACHED:
            raise AssertionError(f"{self.vector['id']}: get_custom_resource_strict must not be called")
        if spec is None:
            csv = _select_unchanged_csv(self.vector["csv_list"], self.vector["id"])
            return StrictReadOutcome.from_resource(_render_csv(csv), resource_version="1")
        read = spec["read"]
        if read == "items":
            return StrictReadOutcome.from_resource(_render_csv(spec["csv"]), resource_version="1")
        if read == "object_absent":
            return StrictReadOutcome.object_absent(STRICT_READ_REASON_OBJECT_NOT_FOUND)
        if read == "error":
            return StrictReadOutcome.error(STRICT_READ_REASON_READ_FAILED)
        raise AssertionError(f"{self.vector['id']}: unknown csv_get read {read!r}")

    def get_deployment_strict(self, *, name, namespace):
        self.calls.append(("get_deployment_strict", {"name": name, "namespace": namespace}))
        spec = self.vector["deployment_get"]
        if spec is None:
            raise AssertionError(f"{self.vector['id']}: get_deployment_strict must not be called")
        read = spec["read"]
        if read == "items":
            resource = {"metadata": {"name": name, "namespace": namespace, "uid": spec["uid"], "resource_version": "1"}}
            return StrictReadOutcome.from_resource(resource, resource_version="1")
        if read == "object_absent":
            return StrictReadOutcome.object_absent(STRICT_READ_REASON_OBJECT_NOT_FOUND)
        if read == "error":
            return StrictReadOutcome.error(STRICT_READ_REASON_READ_FAILED)
        raise AssertionError(f"{self.vector['id']}: unknown deployment_get read {read!r}")


def _run_capture(vector, **overrides):
    client = _FakeIdentityClient(vector)
    kwargs = {"mch_teardown_key": MCH_KEY, "mch_expected_uid": MCH_EXPECTED_UID, "captured_at": CAPTURED_AT}
    kwargs.update(overrides)
    identity = capture_operator_identity(client, **kwargs)
    return client, identity


def _expected_operator_deployment(vector, *, mch_key=MCH_KEY, mch_uid=MCH_EXPECTED_UID, captured_at=CAPTURED_AT):
    csv_defaults = _csv()
    return {
        "namespace": ACM_NS,
        "name": RECORDED_DEPLOYMENT_NAME,
        "uid": vector["deployment_get"]["uid"],
        "discovery_method": OPERATOR_IDENTITY_DISCOVERY_METHOD,
        "captured_at": captured_at,
        "csv": {
            "namespace": ACM_NS,
            "name": csv_defaults["name"],
            "uid": csv_defaults["uid"],
            "owned_crd": MCH_CRD,
        },
        "mch_teardown_key": mch_key,
        "mch_expected_uid": mch_uid,
    }


@pytest.mark.parametrize("vector", IDENTITY_CAPTURE_VECTORS, ids=lambda v: v["id"])
def test_capture_operator_identity_vectors(vector):
    expected = vector["expected"]
    if expected["outcome"] == "fatal":
        client = _FakeIdentityClient(vector)
        with pytest.raises(SwitchoverError):
            capture_operator_identity(
                client, mch_teardown_key=MCH_KEY, mch_expected_uid=MCH_EXPECTED_UID, captured_at=CAPTURED_AT
            )
        return

    _client, identity = _run_capture(vector)
    if expected["outcome"] == "operator_identity_unavailable":
        assert identity.available is False
        assert identity.operator_deployment is None
        assert identity.operator_identity_unavailable["reason"] == expected["reason"]
        assert identity.operator_identity_unavailable["discovery_method"] == OPERATOR_IDENTITY_DISCOVERY_METHOD
        assert identity.operator_identity_unavailable["captured_at"] == CAPTURED_AT
        assert identity.operator_identity_unavailable["mch_teardown_key"] == MCH_KEY
        assert identity.operator_identity_unavailable["mch_expected_uid"] == MCH_EXPECTED_UID
        assert (
            identity.operator_identity_unavailable["evidence_summary"] == EVIDENCE_SUMMARY_BY_REASON[expected["reason"]]
        )
    else:
        assert expected["outcome"] == "operator_deployment"
        assert identity.available is True
        assert identity.operator_identity_unavailable is None
        assert identity.operator_deployment == _expected_operator_deployment(vector)


def test_capture_reason_vocabulary_is_exercised_by_the_validator():
    """One test per closed reason: a vector producing it yields a validator-accepted payload."""
    for reason in OPERATOR_IDENTITY_UNAVAILABLE_REASONS:
        matching = [
            v
            for v in IDENTITY_CAPTURE_VECTORS
            if v["expected"]["outcome"] == "operator_identity_unavailable" and v["expected"]["reason"] == reason
        ]
        assert matching, f"no IDENTITY_CAPTURE_VECTORS vector exercises reason {reason!r}"
        _client, identity = _run_capture(matching[0])
        record = TeardownRecord(
            key=MCH_KEY,
            expected_uid=MCH_EXPECTED_UID,
            phase=TeardownPhase.DELETE_STARTED,
            operator_identity_unavailable=identity.operator_identity_unavailable,
        )
        validate(record)  # raises MalformedTeardownRecord on failure


@pytest.mark.parametrize(
    "vector",
    [v for v in IDENTITY_CAPTURE_VECTORS if v["expected"]["outcome"] != "fatal"],
    ids=lambda v: v["id"],
)
def test_capture_output_is_accepted_by_the_teardown_record_validator_and_round_trips(vector):
    _client, identity = _run_capture(vector)
    record = TeardownRecord(
        key=MCH_KEY,
        expected_uid=MCH_EXPECTED_UID,
        phase=TeardownPhase.DELETE_STARTED,
        operator_deployment=identity.operator_deployment,
        operator_identity_unavailable=identity.operator_identity_unavailable,
    )
    validate(record)
    round_tripped = validate_stored(MCH_KEY, to_stored(record))
    assert round_tripped == record


def test_malformed_captured_output_is_rejected_not_repaired():
    vector = _find_capture_vector("capture_valid_identity")
    _client, identity = _run_capture(vector)
    base = dict(identity.operator_deployment)

    def _record_with(deployment):
        return TeardownRecord(
            key=MCH_KEY,
            expected_uid=MCH_EXPECTED_UID,
            phase=TeardownPhase.DELETE_STARTED,
            operator_deployment=deployment,
        )

    wrong_key = dict(base)
    wrong_key["mch_teardown_key"] = wrong_key["mch_teardown_key"] + "-mutated"
    with pytest.raises(MalformedTeardownRecord):
        validate(_record_with(wrong_key))

    wrong_uid = dict(base)
    wrong_uid["mch_expected_uid"] = wrong_uid["mch_expected_uid"] + "-mutated"
    with pytest.raises(MalformedTeardownRecord):
        validate(_record_with(wrong_uid))

    extra_field = dict(base)
    extra_field["extra"] = "unexpected"
    with pytest.raises(MalformedTeardownRecord):
        validate(_record_with(extra_field))

    wrong_owned_crd = dict(base)
    wrong_owned_crd["csv"] = dict(base["csv"])
    wrong_owned_crd["csv"]["owned_crd"] = "wrong-crd.example.io"
    with pytest.raises(MalformedTeardownRecord):
        validate(_record_with(wrong_owned_crd))


def test_capture_request_provenance_for_the_valid_vector():
    vector = _find_capture_vector("capture_valid_identity")
    client, identity = _run_capture(vector)
    assert identity.available is True
    assert [call[0] for call in client.calls] == [
        "list_custom_resources_strict",
        "get_custom_resource_strict",
        "get_deployment_strict",
    ]
    list_call, get_call, deployment_call = client.calls
    assert list_call[1]["namespace"] == ACM_NS
    assert get_call[1]["namespace"] == ACM_NS
    assert deployment_call[1]["namespace"] == ACM_NS


def test_deployment_get_uses_the_csv_get_body_install_deployment_name_not_the_stale_list_name():
    vector = _capture_vector(
        "adhoc_deployment_name_from_get_body_not_list",
        csv_list=_csv_list_items(_csv(install_deployments=["stale-name"])),
        csv_get=_csv_get_items(_csv(install_deployments=[RECORDED_DEPLOYMENT_NAME])),
        deployment_get=_deployment_get_items("uid-deploy-captured"),
        expected=_outcome_operator_deployment(),
    )
    client, identity = _run_capture(vector)
    assert identity.available is True
    deployment_calls = [call for call in client.calls if call[0] == "get_deployment_strict"]
    assert len(deployment_calls) == 1
    assert deployment_calls[0][1]["name"] == RECORDED_DEPLOYMENT_NAME


@pytest.mark.parametrize(
    "vector_id",
    ["capture_csv_list_error", "capture_csv_get_error", "capture_selected_csv_invalid_name"],
)
def test_fatal_csv_errors_never_reach_a_deployment_read(vector_id):
    vector = _find_capture_vector(vector_id)
    client = _FakeIdentityClient(vector)
    with pytest.raises(SwitchoverError):
        capture_operator_identity(
            client, mch_teardown_key=MCH_KEY, mch_expected_uid=MCH_EXPECTED_UID, captured_at=CAPTURED_AT
        )
    assert not any(call[0] == "get_deployment_strict" for call in client.calls)


def test_invalid_selected_csv_name_is_fatal_without_the_named_get_and_raises_no_validation_error():
    vector = _find_capture_vector("capture_selected_csv_invalid_name")
    client = _FakeIdentityClient(vector)
    with pytest.raises(SwitchoverError) as exc_info:
        capture_operator_identity(
            client, mch_teardown_key=MCH_KEY, mch_expected_uid=MCH_EXPECTED_UID, captured_at=CAPTURED_AT
        )
    assert not isinstance(exc_info.value, ValidationError)
    assert not any(call[0] == "get_custom_resource_strict" for call in client.calls)


def test_invalid_install_deployment_name_is_unavailable_without_a_deployment_get():
    vector = _find_capture_vector("capture_install_deployment_invalid_name")
    client, identity = _run_capture(vector)
    assert identity.available is False
    assert identity.operator_identity_unavailable["reason"] == "install_deployment_absent"
    assert not any(call[0] == "get_deployment_strict" for call in client.calls)


def test_fatal_message_is_sanitized_and_omits_the_raw_read_layer_reason_code():
    vector = _find_capture_vector("capture_csv_list_error")
    client = _FakeIdentityClient(vector)
    with pytest.raises(SwitchoverError) as exc_info:
        capture_operator_identity(
            client, mch_teardown_key=MCH_KEY, mch_expected_uid=MCH_EXPECTED_UID, captured_at=CAPTURED_AT
        )
    message = str(exc_info.value)
    lowered = message.lower()
    for needle in _SANITIZATION_FORBIDDEN_SUBSTRINGS:
        assert needle not in lowered, f"forbidden substring {needle!r} in fatal message: {message!r}"
    assert STRICT_READ_REASON_READ_FAILED not in message


def test_evidence_summary_covers_the_closed_reason_vocabulary_and_is_sanitized():
    assert set(EVIDENCE_SUMMARY_BY_REASON) == set(OPERATOR_IDENTITY_UNAVAILABLE_REASONS)
    for reason, sentence in EVIDENCE_SUMMARY_BY_REASON.items():
        lowered = sentence.lower()
        for needle in _SANITIZATION_FORBIDDEN_SUBSTRINGS:
            assert (
                needle not in lowered
            ), f"forbidden substring {needle!r} in evidence_summary[{reason!r}]: {sentence!r}"


def test_caller_supplied_binding_is_echoed_verbatim():
    vector = _find_capture_vector("capture_valid_identity")
    custom_key = "apps/v1/Deployment/other-namespace/other-name"
    custom_uid = "custom-uid-value"
    custom_captured_at = "2026-01-02T03:04:05Z"
    _client, identity = _run_capture(
        vector, mch_teardown_key=custom_key, mch_expected_uid=custom_uid, captured_at=custom_captured_at
    )
    assert identity.operator_deployment["mch_teardown_key"] == custom_key
    assert identity.operator_deployment["mch_expected_uid"] == custom_uid
    assert identity.operator_deployment["captured_at"] == custom_captured_at


@pytest.mark.parametrize(
    "overrides",
    [
        {"mch_teardown_key": ""},
        {"mch_expected_uid": ""},
        {"captured_at": ""},
        {"mch_teardown_key": None},
        {"mch_expected_uid": None},
        {"captured_at": None},
    ],
)
def test_capture_operator_identity_rejects_empty_or_non_string_binding_values(overrides):
    vector = _find_capture_vector("capture_valid_identity")
    client = _FakeIdentityClient(vector)
    kwargs = {"mch_teardown_key": MCH_KEY, "mch_expected_uid": MCH_EXPECTED_UID, "captured_at": CAPTURED_AT}
    kwargs.update(overrides)
    with pytest.raises(ValueError):
        capture_operator_identity(client, **kwargs)


def test_operator_identity_requires_exactly_one_of_the_two_values():
    with pytest.raises(ValueError):
        OperatorIdentity()
    with pytest.raises(ValueError):
        OperatorIdentity(operator_deployment={"a": 1}, operator_identity_unavailable={"b": 2})
    assert OperatorIdentity(operator_deployment={"a": 1}).available is True
    assert OperatorIdentity(operator_identity_unavailable={"b": 2}).available is False
