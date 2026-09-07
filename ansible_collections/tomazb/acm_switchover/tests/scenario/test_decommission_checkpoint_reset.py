"""Scenario tests for decommission teardown records across a checkpoint reset.

Two reset surfaces are pinned here, both driven through the shipped
``checkpoint_phase`` action rather than by calling library code directly:

1. A full ``checkpoint.reset`` writes a fresh record and therefore DROPS
   ``decommission_teardown_records``. That is asserted as **current, documented
   behaviour** so the R4-05 coordination stays visible instead of being silently
   mitigated here.
2. ``reset_from`` retains ``operational_data`` and therefore retains the teardown
   records **whole** -- ``absence_proofs`` is a sibling field inside the record,
   not a separate durable key, so it survives with the record. A retained record
   is revalidated on the post-reset read: a valid one still loads, and a
   ``MALFORMED_COMPLETION_RECORDS`` payload still fails closed rather than being
   accepted because it happened to be stored already.

No new reset mechanism is introduced and the accepted R4-05 reset-laundering
limitation is unchanged. The decommission role does not read these records yet:
that wiring belongs to PRs C, D and E, so the post-reset revalidation is asserted
against the collection's own reader on the file the playbook left behind.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest
import yaml

from ansible_collections.tomazb.acm_switchover.plugins.module_utils.checkpoint import (
    KEY_DECOMMISSION_TEARDOWN_RECORDS,
    MalformedTeardownRecord,
    build_operation_identity,
    teardown_key,
    teardown_records,
)
from ansible_collections.tomazb.acm_switchover.tests.conftest import _ansible_env

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[5]
_UNIT_TESTS_DIR = _REPO_ROOT / "ansible_collections/tomazb/acm_switchover/tests/unit"
if str(_UNIT_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_UNIT_TESTS_DIR))

from test_decommission_role_contracts import run_decommission_role  # noqa: E402

_MCO_KEY = teardown_key(
    "observability.open-cluster-management.io/v1beta2",
    "MultiClusterObservability",
    None,
    "observability",
)

_HUBS = {
    "primary": {"context": "primary-hub", "kubeconfig": ""},
    "secondary": {"context": "secondary-hub", "kubeconfig": ""},
}
_HUB_IDENTITIES = {
    "primary": {"cluster_uid": "fixture-primary-uid"},
    "secondary": {"cluster_uid": "fixture-secondary-uid"},
}

#: A valid completed MCO record in the namespace-present mode (plan 10.2.1b/c).
_VALID_RECORD = {
    "expected_uid": "u-mco",
    "phase": "completed",
    "observed_at": "2026-09-04T00:00:00Z",
    "resource_versions": {"drain_namespace": "88190", "drain_pods": "88219"},
    "absence_proofs": {"target_cr": {"proof_type": "object_absent", "resource_key": _MCO_KEY}},
}

#: The ``completed_missing_absence_proofs`` member of MALFORMED_COMPLETION_RECORDS.
_MALFORMED_RECORD = {
    "expected_uid": "u-mco",
    "phase": "completed",
    "observed_at": "2026-09-04T00:00:00Z",
    "resource_versions": {"drain_namespace": "88190", "drain_pods": "88219"},
}


def _seed_checkpoint(path: pathlib.Path, record: dict) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": "2.0",
                "phase": "activation",
                "completed_phases": ["preflight", "primary_prep", "activation"],
                "operational_data": {KEY_DECOMMISSION_TEARDOWN_RECORDS: {_MCO_KEY: record}},
                "errors": [],
                "report_refs": [],
                "operation_identity": build_operation_identity(
                    hubs=_HUBS,
                    operation={},
                    collection_version="",
                    hub_identities=_HUB_IDENTITIES,
                ),
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _run_checkpoint_enter(tmp_path: pathlib.Path, checkpoint_config: dict) -> subprocess.CompletedProcess[str]:
    playbook = tmp_path / "checkpoint-reset.yml"
    playbook.write_text(
        yaml.safe_dump(
            [
                {
                    "hosts": "localhost",
                    "connection": "local",
                    "gather_facts": False,
                    "vars": {
                        "acm_switchover_execution": {"mode": "execute"},
                        "acm_switchover_hubs": _HUBS,
                        "acm_switchover_operation": {},
                        "acm_switchover_collection_version": "",
                        "acm_switchover_hub_identities": _HUB_IDENTITIES,
                    },
                    "tasks": [
                        {
                            "name": "Enter the initial checkpointed phase",
                            "tomazb.acm_switchover.checkpoint_phase": {
                                "phase": "preflight",
                                "status": "enter",
                                "checkpoint": "{{ checkpoint_config }}",
                            },
                        }
                    ],
                }
            ],
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return subprocess.run(
        [
            "ansible-playbook",
            str(playbook),
            "-i",
            "ansible_collections/tomazb/acm_switchover/examples/inventory.yml",
            "-e",
            json.dumps({"checkpoint_config": checkpoint_config}),
        ],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=_ansible_env(_REPO_ROOT, tmp_path),
        timeout=300,
    )


def test_full_checkpoint_reset_drops_the_decommission_teardown_records(tmp_path):
    """Documented R4-05 behaviour: a full reset discards the durable teardown records.

    What is asserted here is the record side: after a full reset no record exists
    for the resource key, so a rerun has no completion evidence to consult and
    treats an already-absent CR as a clean skip. The rerun's own behaviour is NOT
    asserted here -- at the B stage the decommission role reads no teardown record
    (PRs C, D and E add the readers), so the causal second half is PR C's to pin.
    This is documented, not mitigated: no new reset mechanism is introduced and the
    accepted R4-05 reset-laundering limitation is unchanged.
    """
    checkpoint_path = tmp_path / "checkpoint.json"
    _seed_checkpoint(checkpoint_path, _VALID_RECORD)

    completed = _run_checkpoint_enter(
        tmp_path,
        {"enabled": True, "backend": "file", "path": str(checkpoint_path), "reset": True},
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    reloaded = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert KEY_DECOMMISSION_TEARDOWN_RECORDS not in reloaded["operational_data"]
    assert teardown_records(reloaded) == {}


def test_reset_from_retains_the_decommission_teardown_records_whole(tmp_path):
    """``reset_from`` prunes phases but keeps operational_data, records included."""
    checkpoint_path = tmp_path / "checkpoint.json"
    _seed_checkpoint(checkpoint_path, _VALID_RECORD)
    before = json.loads(checkpoint_path.read_text(encoding="utf-8"))["operational_data"]

    completed = _run_checkpoint_enter(
        tmp_path,
        {
            "enabled": True,
            "backend": "file",
            "path": str(checkpoint_path),
            "reset_from": "primary_prep",
        },
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    reloaded = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert reloaded["completed_phases"] == ["preflight"]
    # Byte-identical: resource_versions and absence_proofs travel inside the record.
    assert json.dumps(reloaded["operational_data"], sort_keys=True) == json.dumps(before, sort_keys=True)

    loaded = teardown_records(reloaded)[_MCO_KEY]
    assert loaded["resource_versions"] == _VALID_RECORD["resource_versions"]
    assert loaded["absence_proofs"] == _VALID_RECORD["absence_proofs"]


def test_reset_from_does_not_launder_a_malformed_teardown_record(tmp_path):
    """A retained malformed record is revalidated on the post-reset read and fails closed."""
    checkpoint_path = tmp_path / "checkpoint.json"
    _seed_checkpoint(checkpoint_path, _MALFORMED_RECORD)

    completed = _run_checkpoint_enter(
        tmp_path,
        {
            "enabled": True,
            "backend": "file",
            "path": str(checkpoint_path),
            "reset_from": "primary_prep",
        },
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    reloaded = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert _MCO_KEY in reloaded["operational_data"][KEY_DECOMMISSION_TEARDOWN_RECORDS]
    with pytest.raises(MalformedTeardownRecord):
        teardown_records(reloaded)


def test_decommission_artifact_reports_the_real_outcome_on_this_lane():
    """Run the B4.1 harness on the scenario lane's ansible-core, not only the unit lane's.

    The unit gates do not set PATH, so they always exercise the ambient
    controller; this test is the one that drives the same role and the same
    published artifact through the scenario lane's interpreter and ansible-core.
    """
    result = run_decommission_role(observability_outcome="failed")
    summary = result["acm_switchover_decommission_result"]
    assert summary["status"] == "fail"
    assert summary["substeps"] == {"observability": "failed"}
    assert summary["would_change"] is False
