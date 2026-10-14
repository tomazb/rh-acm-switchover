# SPDX-License-Identifier: MIT
"""R4-04 PR C Task 8a checkpoint safeguards.

The Argo CD re-pause marker (PR C decisions amendment section 2.2) and the #332
reset_from fence and pre-freeze entry refusal, at the checkpoint_phase boundary.
"""

import json

import pytest

from ansible_collections.tomazb.acm_switchover.tests.unit.plugins.action.test_checkpoint_phase_runtime import (
    _dir_snapshot,
    _enter_action,
    _fail_action,
    _first_journal,
    _make_checkpoint_action,
    _stored,
    _task_vars_with_operation_identity,
    _write_phase_checkpoint,
)

# The PID-keyed fact checkpoint_phase returns once a rewind was applied (#332).
REWIND_FENCE_FACT = "_acm_switchover_rewind_consumed"
_JOURNALS = {"valid": _first_journal, "invalid": lambda: {"schema_version": 9}}


def _repause_fail(path, phase, *, check_mode=False, **extra):
    action = _fail_action(path, phase, check_mode=check_mode)
    action._task.args["argocd_repause"] = True
    action._task.args.update(extra)
    return action


def _clear_action(path, *, enabled=True, check_mode=False, **extra):
    args = {"clear_argocd_repause": True, "checkpoint": {"enabled": enabled, "backend": "file", "path": str(path)}}
    args.update(extra)
    action = _make_checkpoint_action(args)
    action._play_context.check_mode = check_mode
    return action


def _marker(phase="activation"):
    return {"failed_phase": phase, "recorded_at": "2026-10-01T00:00:00+00:00"}


# --- the fail transition records the marker in the same durable write -------------------------


@pytest.mark.parametrize("journal", ["valid", "invalid"])
@pytest.mark.parametrize("phase", ["activation", "post_activation", "finalization"])
def test_a_fail_under_a_journal_records_the_repause_marker_with_the_failure(tmp_path, journal, phase):
    stored = _JOURNALS[journal]()
    path = _write_phase_checkpoint(
        tmp_path, phase=phase, completed=("preflight", "primary_prep", "activation"), data={"migration_backups": stored}
    )
    result = _repause_fail(path, phase).run(task_vars=_task_vars_with_operation_identity())

    assert result.get("failed") is not True, result
    assert result["argocd_repause_recorded"] is True
    after = _stored(path)
    assert after["operational_data"]["argocd_repause_required"]["failed_phase"] == phase
    assert after["operational_data"]["argocd_repause_required"]["recorded_at"]
    assert after["operational_data"]["migration_backups"] == stored
    assert after["errors"][-1] == {"phase": phase, "error": "boom"}
    assert after["phase_status"] == "fail"
    assert phase not in after["completed_phases"]


@pytest.mark.parametrize("value", ["True", "yes"])
def test_a_templated_repause_request_is_a_boolean(tmp_path, value):
    path = _write_phase_checkpoint(tmp_path, data={"migration_backups": _first_journal()})
    result = _repause_fail(path, "activation", argocd_repause=value).run(task_vars=_task_vars_with_operation_identity())
    assert result["argocd_repause_recorded"] is True


def test_a_false_repause_request_records_nothing(tmp_path):
    path = _write_phase_checkpoint(tmp_path, data={"migration_backups": _first_journal()})
    result = _repause_fail(path, "activation", argocd_repause="False").run(
        task_vars=_task_vars_with_operation_identity()
    )
    assert result.get("failed") is not True
    assert "argocd_repause_required" not in _stored(path)["operational_data"]


def test_a_journal_free_fail_records_no_marker_and_is_unchanged(tmp_path):
    marked_dir, plain_dir = tmp_path / "marked", tmp_path / "plain"
    marked_dir.mkdir()
    plain_dir.mkdir()
    marked, plain = _write_phase_checkpoint(marked_dir), _write_phase_checkpoint(plain_dir)

    result = _repause_fail(marked, "activation").run(task_vars=_task_vars_with_operation_identity())
    _fail_action(plain, "activation").run(task_vars=_task_vars_with_operation_identity())

    assert result["argocd_repause_recorded"] is False
    after, reference = _stored(marked), _stored(plain)
    assert "argocd_repause_required" not in after["operational_data"]
    for key in ("phase", "phase_status", "completed_phases", "operational_data", "errors"):
        assert after[key] == reference[key], key


@pytest.mark.parametrize("mode, check_mode", [("dry_run", False), ("execute", True)])
def test_a_non_mutating_fail_records_nothing(tmp_path, mode, check_mode):
    path = _write_phase_checkpoint(tmp_path, data={"migration_backups": _first_journal()})
    before = _dir_snapshot(tmp_path)
    result = _repause_fail(path, "activation", check_mode=check_mode).run(
        task_vars=_task_vars_with_operation_identity(mode=mode)
    )
    assert result.get("failed") is not True
    assert _dir_snapshot(tmp_path) == before


@pytest.mark.parametrize(
    "phase, status, value",
    [("primary_prep", "fail", True), ("activation", "pass", True), ("activation", "fail", "banana")],
)
def test_the_repause_request_is_refused_outside_a_post_freeze_fail(tmp_path, phase, status, value):
    path = _write_phase_checkpoint(tmp_path, data={"migration_backups": _first_journal()})
    before = _dir_snapshot(tmp_path)
    action = _make_checkpoint_action(
        {
            "phase": phase,
            "checkpoint": {"enabled": True, "backend": "file", "path": str(path)},
            "status": status,
            "argocd_repause": value,
        }
    )
    result = action.run(task_vars=_task_vars_with_operation_identity())
    assert result["failed"] is True
    assert "argocd_repause" in result["msg"]
    assert _dir_snapshot(tmp_path) == before


# --- facts publish the obligation and the journal slot -----------------------------------------


@pytest.mark.parametrize("journal", ["valid", "invalid"])
def test_enter_publishes_the_marker_and_journal_presence(tmp_path, journal):
    path = _write_phase_checkpoint(
        tmp_path,
        completed=("preflight", "primary_prep"),
        data={"migration_backups": _JOURNALS[journal](), "argocd_repause_required": _marker()},
    )
    result = _enter_action(path).run(task_vars=_task_vars_with_operation_identity())
    assert result.get("failed") is not True, result
    assert result["facts"]["argocd_repause_required"] == "activation"
    assert result["facts"]["migration_journal_present"] is True


def test_journal_free_facts_carry_no_obligation(tmp_path):
    path = _write_phase_checkpoint(tmp_path, completed=("preflight", "primary_prep"))
    result = _enter_action(path).run(task_vars=_task_vars_with_operation_identity())
    assert result["facts"]["argocd_repause_required"] is None
    assert result["facts"]["migration_journal_present"] is False


@pytest.mark.parametrize(
    "marker",
    [None, "activation", {}, {"failed_phase": "primary_prep", "recorded_at": "x"}, {"failed_phase": "activation"}],
)
def test_a_malformed_marker_is_never_read_as_discharged(tmp_path, marker):
    path = _write_phase_checkpoint(
        tmp_path,
        completed=("preflight", "primary_prep"),
        data={"migration_backups": _first_journal(), "argocd_repause_required": marker},
    )
    entered = _enter_action(path).run(task_vars=_task_vars_with_operation_identity())
    read = _make_checkpoint_action(
        {"read_facts": True, "checkpoint": {"enabled": True, "backend": "file", "path": str(path)}}
    ).run(task_vars=_task_vars_with_operation_identity())
    for result in (entered, read):
        assert result["failed"] is True
        assert "malformed" in result["msg"]


# --- the explicit clear keeps the journal and the retry position -------------------------------


@pytest.mark.parametrize("journal", ["valid", "invalid"])
def test_clear_removes_only_the_marker(tmp_path, journal):
    stored = _JOURNALS[journal]()
    path = _write_phase_checkpoint(
        tmp_path,
        phase="post_activation",
        completed=("preflight", "primary_prep", "activation"),
        data={"migration_backups": stored, "argocd_repause_required": _marker("post_activation"), "argocd_run_id": "r"},
    )
    before = _stored(path)
    result = _clear_action(path).run(task_vars=_task_vars_with_operation_identity())

    assert result.get("failed") is not True, result
    assert result["changed"] is True
    after = _stored(path)
    assert after["operational_data"] == {"migration_backups": stored, "argocd_run_id": "r"}
    for key in ("phase", "phase_status", "completed_phases", "errors", "report_refs", "operation_identity"):
        assert after[key] == before[key], key


def test_clearing_an_absent_marker_writes_nothing(tmp_path):
    path = _write_phase_checkpoint(tmp_path, data={"migration_backups": _first_journal()})
    before = _dir_snapshot(tmp_path)
    result = _clear_action(path).run(task_vars=_task_vars_with_operation_identity())
    assert result == {"changed": False, "checkpoint": json.loads(before["checkpoint.json"])}
    assert _dir_snapshot(tmp_path) == before


@pytest.mark.parametrize("mode, check_mode", [("dry_run", False), ("validate", False), ("execute", True)])
def test_a_non_mutating_clear_writes_nothing(tmp_path, mode, check_mode):
    path = _write_phase_checkpoint(
        tmp_path, data={"migration_backups": _first_journal(), "argocd_repause_required": _marker()}
    )
    before = _dir_snapshot(tmp_path)
    result = _clear_action(path, check_mode=check_mode).run(task_vars=_task_vars_with_operation_identity(mode=mode))
    assert result.get("failed") is not True
    assert result["changed"] is False
    assert _dir_snapshot(tmp_path) == before


@pytest.mark.parametrize(
    "extra, enabled",
    [({}, False), ({"phase": "activation"}, True), ({"status": "pass"}, True), ({"clear_argocd_repause": "yes"}, True)],
)
def test_the_clear_is_refused_when_misused(tmp_path, extra, enabled):
    path = _write_phase_checkpoint(
        tmp_path, data={"migration_backups": _first_journal(), "argocd_repause_required": _marker()}
    )
    before = _dir_snapshot(tmp_path)
    result = _clear_action(path, enabled=enabled, **extra).run(task_vars=_task_vars_with_operation_identity())
    assert result["failed"] is True
    assert _dir_snapshot(tmp_path) == before


def test_a_failed_clear_write_keeps_the_marker(tmp_path, monkeypatch):
    path = _write_phase_checkpoint(
        tmp_path, data={"migration_backups": _first_journal(), "argocd_repause_required": _marker()}
    )
    action = _clear_action(path)
    monkeypatch.setattr(action, "_save_checkpoint", lambda *_: {"failed": True, "msg": "disk full"})
    result = action.run(task_vars=_task_vars_with_operation_identity())
    assert result["failed"] is True
    assert _stored(path)["operational_data"]["argocd_repause_required"] == _marker()


def test_a_clear_replaced_without_directory_durability_writes_the_marker_back(tmp_path, monkeypatch):
    """A-28/D-42: the cleared checkpoint is visible but not durable; the obligation is restored, visibly."""
    path = _write_phase_checkpoint(
        tmp_path, data={"migration_backups": _first_journal(), "argocd_repause_required": _marker()}
    )
    action = _clear_action(path)
    calls = []

    def fsync_parent_directory(_path):
        calls.append(_stored(path)["operational_data"].get("argocd_repause_required"))
        if len(calls) == 1:
            raise OSError("EIO")

    monkeypatch.setattr(action, "_fsync_parent_directory", fsync_parent_directory)
    result = action.run(task_vars=_task_vars_with_operation_identity())

    assert result["failed"] is True
    assert "indeterminate" in result["msg"]
    # The first replacement landed without the marker; the restoring write put it back.
    assert calls == [None, _marker()]
    assert _stored(path)["operational_data"]["argocd_repause_required"] == _marker()


def test_a_clear_whose_restoring_write_also_fails_reports_the_marker_indeterminate(tmp_path, monkeypatch):
    path = _write_phase_checkpoint(
        tmp_path, data={"migration_backups": _first_journal(), "argocd_repause_required": _marker()}
    )
    action = _clear_action(path)

    def fsync_parent_directory(_path):
        raise OSError("EIO")

    monkeypatch.setattr(action, "_fsync_parent_directory", fsync_parent_directory)
    result = action.run(task_vars=_task_vars_with_operation_identity())

    assert result["failed"] is True
    assert "re-pause marker is indeterminate" in result["msg"]


# --- #332: an applied rewind is consumed once per invocation -----------------------------------


def _run_invocation(path, steps, *, pid_facts=None, reset_from="primary_prep"):
    """Drive transitions like one ansible-playbook process: returned facts persist."""
    task_vars = _task_vars_with_operation_identity()
    task_vars.update(pid_facts or {})
    results = []
    for phase, status, extra in steps:
        action = _make_checkpoint_action(
            {
                "phase": phase,
                "checkpoint": {"enabled": True, "backend": "file", "path": str(path), "reset_from": reset_from},
                "status": status,
                **extra,
            }
        )
        result = action.run(task_vars=dict(task_vars))
        results.append(result)
        if result.get("failed"):
            break
        task_vars.update(result.get("ansible_facts") or {})
    return results, task_vars


def test_a_rewind_is_applied_once_and_later_enters_continue_after_it(tmp_path):
    path = _write_phase_checkpoint(
        tmp_path,
        phase="finalization",
        completed=("preflight", "primary_prep", "activation", "post_activation"),
        data={},
    )
    results, task_vars = _run_invocation(
        path,
        [
            ("preflight", "enter", {}),
            ("primary_prep", "enter", {}),
            ("primary_prep", "pass", {}),
            ("activation", "enter", {}),
            ("activation", "update", {"operational_data": {"migration_backups": _first_journal()}}),
            ("activation", "pass", {}),
            ("post_activation", "enter", {}),
            ("post_activation", "pass", {}),
        ],
    )
    assert all(result.get("failed") is not True for result in results), results[-1]
    assert task_vars[REWIND_FENCE_FACT].endswith(":primary_prep")
    after = _stored(path)
    # The activation enter did not prune the primary_prep this run just completed.
    assert after["completed_phases"] == ["preflight", "primary_prep", "activation", "post_activation"]
    assert after["operational_data"]["migration_backups"] == _first_journal()


def test_a_later_invocation_processes_the_rewind_again(tmp_path):
    path = _write_phase_checkpoint(
        tmp_path, phase="activation", completed=("preflight", "primary_prep", "activation"), data={}
    )
    _run_invocation(path, [("preflight", "enter", {})])
    assert _stored(path)["completed_phases"] == ["preflight"]
    _run_invocation(path, [("primary_prep", "enter", {}), ("primary_prep", "pass", {})])
    # A stale fence from another process does not suppress the new invocation's rewind.
    _run_invocation(path, [("preflight", "enter", {})], pid_facts={REWIND_FENCE_FACT: "1:primary_prep"})
    assert _stored(path)["completed_phases"] == ["preflight"]


def test_a_later_invocation_rewind_over_the_journal_is_still_refused(tmp_path):
    path = _write_phase_checkpoint(
        tmp_path,
        phase="post_activation",
        completed=("preflight", "primary_prep", "activation"),
        data={"migration_backups": _first_journal()},
    )
    before = _dir_snapshot(tmp_path)
    results, _ = _run_invocation(path, [("preflight", "enter", {})])
    assert results[-1]["failed"] is True
    assert "full checkpoint reset" in results[-1]["msg"]
    assert _dir_snapshot(tmp_path) == before


@pytest.mark.parametrize("mode, check_mode", [("dry_run", False), ("execute", True)])
def test_a_preview_never_fences_the_rewind(tmp_path, mode, check_mode):
    path = _write_phase_checkpoint(
        tmp_path, phase="activation", completed=("preflight", "primary_prep", "activation"), data={}
    )
    action = _enter_action(path, phase="preflight", reset_from="primary_prep")
    action._play_context.check_mode = check_mode
    result = action.run(task_vars=_task_vars_with_operation_identity(mode=mode))
    assert REWIND_FENCE_FACT not in (result.get("ansible_facts") or {})


# --- #332: missing pre-freeze work never runs over a journal ------------------------------------


@pytest.mark.parametrize("journal", ["valid", "invalid"])
@pytest.mark.parametrize("mode, check_mode", [("execute", False), ("dry_run", False), ("execute", True)])
def test_a_missing_pre_freeze_phase_is_refused_over_a_journal(tmp_path, journal, mode, check_mode):
    path = _write_phase_checkpoint(
        tmp_path,
        phase="activation",
        completed=("preflight", "activation"),
        data={"migration_backups": _JOURNALS[journal]()},
    )
    before = _dir_snapshot(tmp_path)
    action = _enter_action(path, phase="primary_prep")
    action._play_context.check_mode = check_mode
    result = action.run(task_vars=_task_vars_with_operation_identity(mode=mode))
    assert result["failed"] is True
    assert "primary_prep" in result["msg"]
    assert _dir_snapshot(tmp_path) == before


def test_a_completed_pre_freeze_phase_is_skipped_over_a_journal(tmp_path):
    path = _write_phase_checkpoint(
        tmp_path, completed=("preflight", "primary_prep"), data={"migration_backups": _first_journal()}
    )
    result = _enter_action(path, phase="primary_prep").run(task_vars=_task_vars_with_operation_identity())
    assert result.get("failed") is not True
    assert result["skipped_phase"] is True


def test_a_journal_free_missing_pre_freeze_phase_still_runs(tmp_path):
    path = _write_phase_checkpoint(tmp_path, completed=("preflight",), data={})
    result = _enter_action(path, phase="primary_prep").run(task_vars=_task_vars_with_operation_identity())
    assert result.get("failed") is not True
    assert result["skipped_phase"] is False


# --- the validated journal facts activation consumes ------------------------------------------


@pytest.mark.parametrize("journal, outcome", [("valid", "valid"), ("invalid", "invalid"), (None, "absent")])
def test_enter_publishes_the_strict_journal_outcome_and_only_a_valid_journal(tmp_path, journal, outcome):
    data = {} if journal is None else {"migration_backups": _JOURNALS[journal]()}
    path = _write_phase_checkpoint(tmp_path, completed=("preflight", "primary_prep"), data=data)
    facts = _enter_action(path).run(task_vars=_task_vars_with_operation_identity())["facts"]
    assert facts["migration_journal_outcome"] == outcome
    assert facts["migration_journal"] == (_first_journal() if journal == "valid" else None)


def test_the_fence_survives_a_new_task_worker_for_every_task(tmp_path, monkeypatch):
    """Review finding 4: each task runs in a fresh worker; only the playbook process is stable."""
    _controller(monkeypatch, start="boot-a:100")
    path = _write_phase_checkpoint(
        tmp_path, phase="activation", completed=("preflight", "primary_prep", "activation"), data={}
    )
    results, task_vars = _run_invocation(
        path,
        [
            ("preflight", "enter", {}),
            ("primary_prep", "enter", {}),
            ("primary_prep", "pass", {}),
            ("activation", "enter", {}),
        ],
    )
    assert all(result.get("failed") is not True for result in results), results[-1]
    assert task_vars[REWIND_FENCE_FACT] == f"4242@boot-a:100:{path}:primary_prep"
    assert _stored(path)["completed_phases"] == ["preflight", "primary_prep"]


# --- round-2 findings 3 and 4: the invocation identity resists a recycled PID ---------------


def _controller(monkeypatch, *, start, ppid=4242):
    """One ansible-playbook process: every task worker gets a fresh PID under the same parent."""
    import itertools

    from ansible_collections.tomazb.acm_switchover.plugins.action import checkpoint_phase

    pids = itertools.count(5000)
    monkeypatch.setattr(checkpoint_phase.os, "getpid", lambda: next(pids))
    monkeypatch.setattr(checkpoint_phase.os, "getppid", lambda: ppid)
    monkeypatch.setattr(checkpoint_phase, "_process_start", lambda pid: start)


def test_a_cached_fence_with_a_recycled_parent_pid_does_not_suppress_a_later_rewind(tmp_path, monkeypatch):
    """Finding 3: a persistent fact cache carries the fence into a new playbook process that
    reuses the old one's PID; that process's explicit rewind is applied, not fenced."""
    path = _write_phase_checkpoint(
        tmp_path, phase="activation", completed=("preflight", "primary_prep", "activation"), data={}
    )
    _controller(monkeypatch, start="boot-a:100")
    _, cached = _run_invocation(path, [("preflight", "enter", {}), ("primary_prep", "enter", {})])
    _run_invocation(path, [("primary_prep", "pass", {})], pid_facts={REWIND_FENCE_FACT: cached[REWIND_FENCE_FACT]})
    assert _stored(path)["completed_phases"] == ["preflight", "primary_prep"]
    _controller(monkeypatch, start="boot-a:900")
    results, _ = _run_invocation(
        path, [("preflight", "enter", {})], pid_facts={REWIND_FENCE_FACT: cached[REWIND_FENCE_FACT]}
    )
    assert results[-1].get("failed") is not True, results[-1]
    assert _stored(path)["completed_phases"] == ["preflight"]


def test_the_resume_summary_is_recorded_once_per_invocation_across_task_workers(tmp_path, monkeypatch):
    """Finding 4: the first executing enter of a resumed run records where it starts; later
    enters of the same process, each in a fresh worker, keep it."""
    path = _write_phase_checkpoint(tmp_path, phase="primary_prep", completed=("preflight", "primary_prep"), data={})
    _controller(monkeypatch, start="boot-a:100")
    results, task_vars = _run_invocation(
        path,
        [("activation", "enter", {}), ("activation", "pass", {}), ("post_activation", "enter", {})],
        reset_from=None,
    )
    assert all(result.get("failed") is not True for result in results), results[-1]
    assert _stored(path)["operational_data"]["resume_summary"] == {"resume_start_phase": "activation"}
    assert "ansible_facts" not in results[-1]

    # A new process that recycles the PID, with the cached sentinel, records its own start.
    _controller(monkeypatch, start="boot-a:900")
    results, _ = _run_invocation(path, [("post_activation", "enter", {})], pid_facts=task_vars, reset_from=None)
    assert results[-1].get("failed") is not True, results[-1]
    assert _stored(path)["operational_data"]["resume_summary"] == {"resume_start_phase": "post_activation"}


def _start_of_a_new_process():
    import subprocess
    import sys

    from ansible_collections.tomazb.acm_switchover.plugins.action import checkpoint_phase

    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        return checkpoint_phase._process_start(child.pid)
    finally:
        child.kill()
        child.wait()


def test_the_process_start_identifies_one_process_on_this_controller():
    import os

    from ansible_collections.tomazb.acm_switchover.plugins.action import checkpoint_phase

    own = checkpoint_phase._process_start(os.getpid())
    assert own and own == checkpoint_phase._process_start(os.getpid())
    assert checkpoint_phase.invocation_id() == f"{os.getppid()}@{checkpoint_phase._process_start(os.getppid())}"
    assert _start_of_a_new_process() != own


def test_the_ps_fallback_used_off_linux_reads_an_absolute_start(monkeypatch):
    """macOS and the BSDs have no /proc: ps reports the absolute start time instead."""
    import builtins
    import os
    import shutil

    from ansible_collections.tomazb.acm_switchover.plugins.action import checkpoint_phase

    if shutil.which("ps") is None:
        pytest.skip("no ps on this controller")
    real_open = builtins.open

    def no_proc(file, *args, **kwargs):
        if str(file).startswith("/proc/"):
            raise FileNotFoundError(file)
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", no_proc)
    start = checkpoint_phase._process_start(os.getpid())
    assert len(start.split()) == 6 and start.endswith(" UTC")  # e.g. "Thu Oct  1 05:32:09 2026 UTC"
    assert start == checkpoint_phase._process_start(os.getpid())


def test_an_unreadable_invocation_identity_fails_the_enter_closed(tmp_path, monkeypatch):
    from ansible_collections.tomazb.acm_switchover.plugins.action import checkpoint_phase

    def unreadable(pid):
        raise RuntimeError(f"Cannot read the start time of the ansible-playbook process {pid}.")

    monkeypatch.setattr(checkpoint_phase, "_process_start", unreadable)
    path = _write_phase_checkpoint(
        tmp_path, phase="activation", completed=("preflight", "primary_prep", "activation"), data={}
    )
    before = _dir_snapshot(tmp_path)
    results, _ = _run_invocation(path, [("preflight", "enter", {})])
    assert results[-1]["failed"] is True
    assert "Cannot read the start time" in results[-1]["msg"]
    assert _dir_snapshot(tmp_path) == before


# --- round-3 findings 2 and 4 -----------------------------------------------------------------


@pytest.mark.parametrize("zones", [("Europe/Ljubljana", "America/New_York"), ("UTC", "Asia/Tokyo")])
def test_the_ps_start_does_not_depend_on_the_controller_time_zone(monkeypatch, zones):
    """Finding 2: a local-time start repeats across a DST fall-back (02:30+02:00 and 02:30+01:00
    print alike), so a recycled PID could match; the fallback reports an absolute UTC start."""
    import builtins
    import os
    import shutil

    from ansible_collections.tomazb.acm_switchover.plugins.action import checkpoint_phase

    if shutil.which("ps") is None:
        pytest.skip("no ps on this controller")
    real_open = builtins.open

    def no_proc(file, *args, **kwargs):
        if str(file).startswith("/proc/"):
            raise FileNotFoundError(file)
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", no_proc)
    starts = []
    for zone in zones:
        monkeypatch.setenv("TZ", zone)
        starts.append(checkpoint_phase._process_start(os.getpid()))
    assert starts[0] == starts[1]


@pytest.mark.parametrize(
    "mode, check_mode",
    [("dry_run", False), ("validate", False), ("execute", True)],
    ids=["dry-run", "validate", "check"],
)
def test_a_preview_enter_with_a_rewind_needs_no_invocation_identity(tmp_path, monkeypatch, mode, check_mode):
    """Finding 4: a preview persists no fence, so unreadable process metadata cannot fail it."""
    from ansible_collections.tomazb.acm_switchover.plugins.action import checkpoint_phase

    def unreadable(pid):
        raise RuntimeError(f"Cannot read the start time of the ansible-playbook process {pid}.")

    monkeypatch.setattr(checkpoint_phase, "_process_start", unreadable)
    path = _write_phase_checkpoint(
        tmp_path, phase="activation", completed=("preflight", "primary_prep", "activation"), data={}
    )
    before = _dir_snapshot(tmp_path)
    action = _enter_action(path, phase="preflight", reset_from="primary_prep")
    action._play_context.check_mode = check_mode
    result = action.run(task_vars=_task_vars_with_operation_identity(mode=mode))
    assert result.get("failed") is not True, result
    assert REWIND_FENCE_FACT not in (result.get("ansible_facts") or {})
    assert _dir_snapshot(tmp_path) == before


@pytest.mark.parametrize("enabled, refused", [("false", True), ("False", True), ("true", False), (None, True)])
def test_the_clear_reads_a_templated_enabled_flag_as_a_boolean(tmp_path, enabled, refused):
    path = _write_phase_checkpoint(
        tmp_path, data={"migration_backups": _first_journal(), "argocd_repause_required": _marker()}
    )
    action = _clear_action(path, enabled=enabled)
    if enabled is None:
        del action._task.args["checkpoint"]["enabled"]
    result = action.run(task_vars=_task_vars_with_operation_identity())
    assert (result.get("failed") is True) is refused, result
    assert ("argocd_repause_required" in _stored(path)["operational_data"]) is refused
