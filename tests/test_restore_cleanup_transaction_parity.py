"""Parity contract: the Restore-cleanup transaction decides identically in both form factors.

The Python CLI's Finalization._run_restore_cleanup_transaction and the collection's
acm_migration_evidence cleanup decisions (module_utils/migration_cleanup.py, driven in the
order roles/finalization/tasks/cleanup_journaled_restore.yml and its attempt and poll files run
them) start from the same journal, see the same strict reads of the journaled locator and the
same guarded DELETE results. The sequence of journal writes and the refusal must agree. The
live completion proof of the barrier is stubbed to pass; timestamps and the operation ID are
pinned. The YAML wiring itself is covered by the collection's integration tests against a fake
API.
"""

import copy
import json
import re
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

import modules.finalization as finalization_module
from ansible_collections.tomazb.acm_switchover.plugins.module_utils import migration_activation as col_activation
from ansible_collections.tomazb.acm_switchover.plugins.module_utils import migration_cleanup as col
from ansible_collections.tomazb.acm_switchover.plugins.module_utils.migration_evidence import (
    MigrationEvidenceError as ColMigrationEvidenceError,
)
from lib.exceptions import SwitchoverError
from lib.run_record import RunRecord
from lib.strict_read import StrictReadOutcome
from lib.utils import StateManager

NOW = "2026-10-01T00:00:00+00:00"
OPERATION = "11111111-2222-4333-8444-555555555555"
_CASES = {
    case["id"]: case
    for case in json.loads(
        (Path(__file__).parent / "fixtures" / "r4_04_migration_evidence_vectors.json").read_text(encoding="utf-8")
    )["cases"]
}
POST = _CASES["journal-post-activation-completed"]["input"]["candidate"]
INTENT = _CASES["journal-cleanup-intent-persisted"]["input"]["candidate"]
ACCEPTED = _CASES["journal-cleanup-delete-accepted"]["input"]["candidate"]
RECOVERY = _CASES["journal-cleanup-recovery-required"]["input"]["candidate"]
DONE = _CASES["journal-cleanup-completed"]["input"]["candidate"]
UID = INTENT["restore"]["uid"]
REPAIR = {
    key: value
    for key, value in _CASES["journal-cleanup-repaired"]["input"]["candidate"]["cleanup"]["repair"].items()
    if key != "acknowledged_at"
}
_CODE = re.compile(r"\(([a-z]+_[a-z_]+)[:)]")


def _live(uid=UID, rv="2002", generation=4):
    restore = INTENT["restore"]
    return {
        "apiVersion": "cluster.open-cluster-management.io/v1beta1",
        "kind": "Restore",
        "metadata": {
            "name": restore["name"],
            "namespace": restore["namespace"],
            "uid": uid,
            "resourceVersion": rv,
            "generation": generation,
        },
        "spec": dict(restore["backup_fields"], cleanupBeforeRestore="CleanupRestored"),
    }


def _accepted(rv="2002"):
    return {"accepted": True, "reason": "ok", "uid": UID, "resource_version": rv}


ABSENT = None
CONFLICT = {"accepted": False, "conflict": True, "reason": "precondition_failed"}
NOT_FOUND = {"accepted": False, "reason": "not_found"}
REPLACEMENT = _live(uid="uid-replacement", rv="3003")


class Recovered(Exception):
    """The transaction recorded recovery_required and blocked."""


def _python(tmp_path, journal, reads, deletes, repair=None, live_version="2.14.3"):
    """Run Finalization._run_restore_cleanup_transaction; return (writes, outcome)."""
    state = StateManager(str(tmp_path / "state.json"))
    if journal is not None:
        state.state["config"]["migration_backups"] = copy.deepcopy(journal)
    state.flush_state()
    secondary = Mock(name="secondary")
    secondary.dry_run = False
    secondary.list_custom_resources_strict.return_value = StrictReadOutcome.from_items(
        [{"metadata": {"name": "multiclusterhub"}, "status": {"currentVersion": live_version}}]
    )
    secondary.get_custom_resource_strict.side_effect = [
        (
            StrictReadOutcome.object_absent("object_not_found")
            if read is None
            else StrictReadOutcome.from_resource(read, resource_version=read["metadata"]["resourceVersion"])
        )
        for read in reads
    ]
    secondary.delete_restore_guarded.side_effect = list(deletes)
    with patch("modules.finalization.BackupScheduleManager"):
        fin = finalization_module.Finalization(
            secondary_client=secondary,
            state_manager=state,
            acm_version="2.14.0",
            migration_cleanup_repair=repair,
        )
    writes = []
    original = RunRecord.record_migration_backups

    def _record(run_record, candidate):
        writes.append(copy.deepcopy(candidate))
        original(run_record, candidate)

    with (
        patch.object(RunRecord, "record_migration_backups", autospec=True, side_effect=_record),
        patch("modules.finalization.SecondaryActivation.revalidate_completion_evidence"),
        patch("modules.finalization._now_iso", return_value=NOW),
        patch("modules.finalization.uuid.uuid4", return_value=OPERATION),
        patch("modules.finalization.time.sleep"),
    ):
        try:
            fin._run_restore_cleanup_transaction()
        except SwitchoverError as exc:
            if writes and writes[-1]["cleanup"]["state"] == "recovery_required":
                return writes, "recovery_required"
            match = _CODE.search(str(exc))
            return writes, match.group(1) if match else "refused"
    return writes, "ok"


def _collection(journal, reads, deletes, repair=None, live_version="2.14.3"):
    """The collection decisions in cleanup_journaled_restore.yml order; return (writes, outcome)."""
    reads, deletes, writes = iter(reads), iter(deletes), []
    try:
        step = col.cleanup_step(journal=journal, repair_requested=repair is not None)["step"]
        if step == "repair":
            writes.append(col.repair_cleanup(journal=journal, request=repair, live=next(reads), now=NOW)["candidate"])
            return writes, "ok"
        if step in ("revalidate", "delete"):
            minor = col_activation.acm_minor(_mch_read(live_version))["acm_minor"]
            col_activation.require_journal_matches_run(journal, journal["restore"]["mutation_kind"], minor)
        if step == "revalidate":
            decision = col.teardown_revalidated(journal=journal, now=NOW)
            if decision["write"]:
                writes.append(decision["candidate"])
            journal = col.cleanup_intent(journal=decision["candidate"], operation_id=OPERATION, now=NOW)["candidate"]
            writes.append(journal)
            step = "delete"
        if step == "delete":
            schedule = col.cleanup_schedule()
            for attempt in range(1, schedule["attempts"] + 1):
                decision = col.pre_delete(journal=journal, live=next(reads), now=NOW)
                if decision["action"] == "recover":
                    writes.append(decision["candidate"])
                    raise Recovered()
                outcome = col.delete_outcome(
                    journal=journal,
                    result=next(deletes),
                    resource_version=decision["resource_version"],
                    attempt=attempt,
                    now=NOW,
                )
                if outcome["outcome"] == "accepted":
                    journal = outcome["candidate"]
                    writes.append(journal)
                    break
            for _ in schedule["poll_delays"]:
                poll = col.absence_poll(journal=journal, live=next(reads), now=NOW)
                if poll["state"] == "recover":
                    writes.append(poll["candidate"])
                    raise Recovered()
                if poll["state"] == "absent":
                    final = col.final_absence(journal=journal, live=next(reads), now=NOW)
                    writes.append(final["candidate"])
                    if final["state"] == "recover":
                        raise Recovered()
                    return writes, "ok"
            return writes, "poll_timeout"
    except Recovered:
        return writes, "recovery_required"
    except ColMigrationEvidenceError as exc:
        return writes, exc.code
    except col_activation.ActivationRefused:
        return writes, "refused"
    return writes, "ok"


def _mch_read(version):
    return {"read_status": "ok", "resources": [{"metadata": {"name": "hub"}, "status": {"currentVersion": version}}]}


SCENARIOS = {
    "fresh-run-completes": (POST, [_live(), ABSENT, ABSENT], [_accepted()], None),
    "conflict-then-accepted": (
        INTENT,
        [_live(), _live(rv="2009"), ABSENT, ABSENT],
        [CONFLICT, _accepted("2009")],
        None,
    ),
    "conflict-exhausted": (INTENT, [_live()] * 4, [CONFLICT] * 4, None),
    "not-found-rereads-into-recovery": (INTENT, [_live(), ABSENT], [NOT_FOUND], None),
    "unverifiable-delete": (INTENT, [_live()], [{"accepted": False, "reason": "unverifiable"}], None),
    "drift-blocks": (INTENT, [_live(generation=5)], [], None),
    "replacement-before-delete": (INTENT, [REPLACEMENT], [], None),
    "replacement-while-polling": (INTENT, [_live(), _live(), REPLACEMENT], [_accepted()], None),
    "replacement-at-final-read": (INTENT, [_live(), ABSENT, REPLACEMENT], [_accepted()], None),
    "absent-on-resume-after-intent": (INTENT, [ABSENT], [], None),
    "absent-on-resume-after-accepted-delete": (ACCEPTED, [ABSENT], [], None),
    "resume-needs-a-new-accepted-delete": (ACCEPTED, [_live(rv="2007"), ABSENT, ABSENT], [_accepted("2007")], None),
    "recovery-blocks-without-repair": (RECOVERY, [], [], None),
    "repair-after-proven-absence": (RECOVERY, [ABSENT], [], REPAIR),
    "repair-with-occupied-locator": (RECOVERY, [REPLACEMENT], [], REPAIR),
    "repair-without-recovery": (DONE, [], [], REPAIR),
    "terminal-touches-nothing": (DONE, [], [], None),
    "no-journal": (None, [], [], None),
    "changed-lane-before-the-barrier": (POST, [], [], None, "2.17.1"),
    "changed-lane-before-a-resumed-delete": (ACCEPTED, [], [], None, "2.17.1"),
    "truncated-poll-response": (
        INTENT,
        [_live(), {"metadata": {"uid": UID, "resourceVersion": "123"}}],
        [_accepted()],
        None,
    ),
}
EXPECTED = {
    "fresh-run-completes": "ok",
    "conflict-then-accepted": "ok",
    "conflict-exhausted": "cleanup_conflict_retries_exhausted",
    "not-found-rereads-into-recovery": "recovery_required",
    "unverifiable-delete": "delete_not_accepted",
    "drift-blocks": "restore_spec_drift",
    "replacement-before-delete": "recovery_required",
    "replacement-while-polling": "recovery_required",
    "replacement-at-final-read": "recovery_required",
    "absent-on-resume-after-intent": "recovery_required",
    "absent-on-resume-after-accepted-delete": "recovery_required",
    "resume-needs-a-new-accepted-delete": "ok",
    "recovery-blocks-without-repair": "cleanup_recovery_required",
    "repair-after-proven-absence": "ok",
    "repair-with-occupied-locator": "repair_locator_occupied",
    "repair-without-recovery": "repair_not_permitted",
    "terminal-touches-nothing": "ok",
    "no-journal": "journal_required",
    "changed-lane-before-the-barrier": "refused",
    "changed-lane-before-a-resumed-delete": "refused",
    "truncated-poll-response": "malformed_restore_read",
}


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_both_form_factors_write_and_refuse_the_same(tmp_path, name):
    journal, reads, deletes, repair, *version = SCENARIOS[name]
    python_writes, python_outcome = _python(tmp_path, journal, reads, deletes, repair, *version)
    collection_writes, collection_outcome = _collection(journal, reads, deletes, repair, *version)

    assert python_outcome == EXPECTED[name]
    assert collection_outcome == python_outcome
    assert collection_writes == python_writes


def test_the_scenarios_cover_every_cleanup_state_and_recovery_reason():
    states = {scenario[0]["cleanup"]["state"] for scenario in SCENARIOS.values() if scenario[0] is not None}
    assert states == {"not_started", "intent_persisted", "delete_accepted", "recovery_required", "completed"}
    reasons = set()
    for name, (journal, reads, deletes, repair, *version) in SCENARIOS.items():
        writes, _ = _collection(journal, reads, deletes, repair, *version)
        reasons.update(write["cleanup"]["recovery"]["reason_code"] for write in writes if write["cleanup"]["recovery"])
    assert reasons == {"absent_without_completion", "replacement_uid", "replacement_during_poll"}
