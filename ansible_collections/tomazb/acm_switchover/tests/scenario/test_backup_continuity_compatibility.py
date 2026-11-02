"""Completed state preserves bytes and makes zero checkpoint save attempts.

The test wrapper instruments Ansible's action loader and then executes its actual
entrypoint. Both shipped playbooks and the shipped direct role run unchanged.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from ansible_collections.tomazb.acm_switchover.tests.integration.test_backup_continuity_runtime import (  # noqa: F401
    COLLECTION,
    run_backup_entry,
)


@pytest.mark.parametrize("route", ["role", "playbook"])
@pytest.mark.parametrize("restore", [False, True])
@pytest.mark.parametrize("case", ["unproved", "proved", "interrupted"])
def test_completed_paths_attempt_no_checkpoint_writes_or_replay(run_backup_entry, route, restore, case):  # noqa: F811
    result, p, s, path, before, stat = run_backup_entry(
        route=route, restore=restore, good=case != "unproved", trace=True, interrupt=case == "interrupted"
    )
    output = result.stdout + result.stderr
    assert (path.parent / "backup-action-trace.jsonl").exists(), output
    events = [json.loads(line) for line in (path.parent / "backup-action-trace.jsonl").read_text().splitlines()]
    actions = [e for e in events if e["event"] == "checkpoint_action"]
    assert actions and all(
        e["source_sha256"]
        == hashlib.sha256((COLLECTION / "plugins/action/checkpoint_phase.py").read_bytes()).hexdigest()
        for e in actions
    )
    assert all(
        e["read_facts"]
        or (e["identity_barrier"] and e["phase"] == "preflight" and e["status"] == "enter" and e["check_mode"])
        for e in actions
    ), actions
    assert not any(e["event"] == "checkpoint_save_attempt" for e in events), events
    modules = [e for e in events if e["event"] == "backup_module_returned"]
    assert (
        len(modules) == 1
        and modules[0]["source_sha256"]
        == hashlib.sha256((COLLECTION / "plugins/modules/acm_backup_continuity.py").read_bytes()).hexdigest()
    ), events
    assert bool(modules[0].get("proof")) == (case != "unproved"), events
    assert (result.returncode == 0) == (case == "proved"), output
    assert any(e["event"] == "interrupt_after_actual_proof" for e in events) == (case == "interrupted"), events
    assert (
        path.read_bytes() == before
        and path.stat().st_mtime_ns == stat.st_mtime_ns
        and path.stat().st_ino == stat.st_ino
    )
    assert not p.writes and not s.writes


@pytest.mark.parametrize("route", ["role", "playbook"])
@pytest.mark.parametrize("restore", [False, True])
@pytest.mark.parametrize("reset", [None, {"reset": True}, {"reset_from": "preflight"}])
def test_completed_identity_and_reset_refusal_attempt_no_saves(run_backup_entry, route, restore, reset):  # noqa: F811
    result, p, s, path, before, stat = run_backup_entry(
        route=route,
        restore=restore,
        good=True,
        wrong_identity=True,
        reset=reset,
        trace=True,
        extra={
            "acm_switchover_backup_completed_assessed": True,
            "acm_switchover_features": {"argocd": {"manage": True, "resume_on_failure": True}},
        },
    )
    output = result.stdout + result.stderr
    assert result.returncode != 0, output
    events = [json.loads(line) for line in (path.parent / "backup-action-trace.jsonl").read_text().splitlines()]
    actions = [e for e in events if e["event"] == "checkpoint_action"]
    assert actions and all(e["read_facts"] or (e["identity_barrier"] and e["check_mode"]) for e in actions), events
    assert not any(e["event"] in ("checkpoint_save_attempt", "backup_module_returned") for e in events), events
    assert path.read_bytes() == before and path.stat().st_mtime_ns == stat.st_mtime_ns
    assert not p.writes and not s.writes
