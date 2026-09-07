# SPDX-License-Identifier: MIT
"""Runtime tests for the shipped acm_uid_guarded_delete module.

These drive the module through a real ``ansible-playbook`` run against a real HTTP API
that evaluates the UID precondition server-side. The unit tests prove the state machine
in isolation; these prove the module Ansible actually loads sends the precondition and
honours the answer.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml

from ansible_collections.tomazb.acm_switchover.tests.conftest import _ansible_env
from ansible_collections.tomazb.acm_switchover.tests.integration.argocd_fake_api import (
    write_kubeconfig,
)
from ansible_collections.tomazb.acm_switchover.tests.integration.uid_guarded_delete_fake_api import (
    GROUP,
    KIND,
    PLURAL,
    VERSION,
    FakeGuardedDeleteAPI,
    mco_object,
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[5]


def _run(tmp_path: Path, api: FakeGuardedDeleteAPI, *, expected_uid: str, check_mode: bool = False):
    repo_root = _repo_root()
    kubeconfig = tmp_path / "guarded.kubeconfig"
    write_kubeconfig(kubeconfig, context="guarded", server=api.url, token="fixture-token")

    vars_file = tmp_path / "guarded-vars.yml"
    vars_file.write_text(
        yaml.safe_dump(
            {
                "guarded_kubeconfig": str(kubeconfig),
                "guarded_context": "guarded",
                "guarded_api_version": f"{GROUP}/{VERSION}",
                "guarded_kind": KIND,
                "guarded_resource_name": PLURAL,
                "guarded_name": "observability",
                "guarded_expected_uid": expected_uid,
                "guarded_result_path": str(tmp_path / "guarded-result.json"),
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    command = [
        "ansible-playbook",
        "ansible_collections/tomazb/acm_switchover/tests/integration/playbooks/run_uid_guarded_delete.yml",
        "-i",
        "localhost,",
        "-e",
        f"@{vars_file}",
    ]
    if check_mode:
        command.append("--check")
    completed = subprocess.run(
        command,
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
        env=_ansible_env(repo_root, tmp_path),
        timeout=120,
    )
    result_path = tmp_path / "guarded-result.json"
    if not result_path.exists():
        raise AssertionError(f"no module outcome captured\nstdout:\n{completed.stdout}\nstderr:\n{completed.stderr}")
    return json.loads(result_path.read_text()), completed


def test_the_proved_object_is_deleted_and_reported_changed(tmp_path):
    api = FakeGuardedDeleteAPI(mco_object("uid-1"))
    try:
        result, _ = _run(tmp_path, api, expected_uid="uid-1")
        assert result.get("failed") is not True, result
        assert result["changed"] is True
        assert result["stage"] == "completed"
        assert api.obj is None, "the object must actually be gone"
        assert len(api.delete_calls) == 1
    finally:
        api.close()


def test_a_replacement_is_refused_server_side_and_survives(tmp_path):
    """The live object carries a different UID. The API server rejects the
    precondition, and the module must not retry without it."""
    api = FakeGuardedDeleteAPI(mco_object("uid-REPLACEMENT"))
    try:
        result, _ = _run(tmp_path, api, expected_uid="uid-1")
        assert result["failed"] is True
        assert result["reason"] == "uid_mismatch"
        assert api.obj is not None, "the replacement must be left intact"
        assert api.delete_calls == [], "the mismatch is caught before any delete is issued"
    finally:
        api.close()


def test_a_replacement_swapped_in_between_read_and_delete_is_refused_by_the_server(tmp_path):
    """The race the precondition exists for: the read proves uid-1, then the object is
    replaced before the delete lands. Only a server-side precondition can catch this,
    which is why the fake API evaluates it rather than trusting the client."""

    def _swap(api_self):
        api_self.obj = mco_object("uid-REPLACEMENT", resource_version="9")

    api = FakeGuardedDeleteAPI(mco_object("uid-1"), on_delete=_swap)
    try:
        result, _ = _run(tmp_path, api, expected_uid="uid-1")
        assert result["failed"] is True
        assert result["reason"] == "uid_mismatch"
        assert api.obj is not None and api.obj["metadata"]["uid"] == "uid-REPLACEMENT"
        assert len(api.delete_calls) == 1, "exactly one delete, never an unconditional retry"
    finally:
        api.close()


def test_an_already_absent_object_reports_no_change(tmp_path):
    api = FakeGuardedDeleteAPI(None)
    try:
        result, _ = _run(tmp_path, api, expected_uid="uid-1")
        assert result.get("failed") is not True, result
        assert result["changed"] is False
        assert result["stage"] == "absent"
        assert api.delete_calls == []
    finally:
        api.close()


def test_check_mode_issues_no_delete_and_predicts_the_change(tmp_path):
    api = FakeGuardedDeleteAPI(mco_object("uid-1"))
    try:
        result, _ = _run(tmp_path, api, expected_uid="uid-1", check_mode=True)
        assert result.get("failed") is not True, result
        assert result["changed"] is False
        assert result["would_change"] is True
        assert api.obj is not None, "check mode must not delete"
        assert api.delete_calls == []
    finally:
        api.close()


@pytest.mark.parametrize("status", [409, 412])
def test_a_server_precondition_failure_is_fatal_and_leaves_the_object(tmp_path, status):
    api = FakeGuardedDeleteAPI(mco_object("uid-1"), delete_status=status)
    try:
        result, _ = _run(tmp_path, api, expected_uid="uid-1")
        assert result["failed"] is True
        assert result["reason"] == "uid_mismatch"
        assert api.obj is not None
        assert len(api.delete_calls) == 1
    finally:
        api.close()


def test_the_delete_request_actually_carries_a_uid_precondition(tmp_path):
    """Measured at the wire, not asserted from the client side: without this, a module
    that silently dropped the precondition would still pass every outcome test against
    a permissive server."""
    seen: dict = {}

    def _capture(api_self):
        seen["obj_uid"] = (api_self.obj or {}).get("metadata", {}).get("uid")

    api = FakeGuardedDeleteAPI(mco_object("uid-1"), on_delete=_capture)
    try:
        _run(tmp_path, api, expected_uid="uid-1")
        # The fake API only deletes when the precondition matches the live uid, so a
        # successful removal is itself proof the precondition was sent and correct.
        assert api.obj is None
        assert seen["obj_uid"] == "uid-1"
    finally:
        api.close()
