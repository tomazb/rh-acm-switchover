"""Phase-failure Argo CD rescue under a migration journal (PR C decisions amendment section 2.2).

With a journal present (valid or invalid) the rescue never rewinds to a pre-freeze phase:
it durably records a re-pause marker through RunRecord before any resume mutation, keeps
the retry positioned at the failed phase, and a retry re-pauses before any phase work.
"""

import copy
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

import acm_switchover
import lib.utils as utils_module
from lib import argocd as argocd_lib
from lib.argocd_register import ArgocdPauseRegister, PauseSummary
from lib.constants import STEP_PAUSE_ARGOCD_APPS
from lib.run_record import RunRecord
from lib.utils import Phase, StateManager
from lib.workflow import FailedStateConfig, handle_failed_state

VECTORS = Path(__file__).parent / "fixtures" / "r4_04_migration_evidence_vectors.json"
JOURNAL = next(
    case["input"]["previous"]
    for case in json.loads(VECTORS.read_text(encoding="utf-8"))["cases"]
    if case["id"] == "transition-pre-to-completed"
)
PAUSED = [
    {
        "hub": "primary",
        "namespace": "argocd",
        "name": "app1",
        "original_sync_policy": {"automated": {}},
        "pause_applied": True,
    },
    {
        "hub": "secondary",
        "namespace": "argocd",
        "name": "app2",
        "original_sync_policy": {"automated": {}},
        "pause_applied": True,
    },
]
LOGGER = logging.getLogger("test")


def _clients():
    primary = Mock(name="primary-client", context="hub-a", dry_run=False)
    primary.get_cluster_identity.return_value = {"context": "hub-a", "cluster_uid": "uid-primary"}
    secondary = Mock(name="secondary-client", context="hub-b", dry_run=False)
    secondary.get_cluster_identity.return_value = {"context": "hub-b", "cluster_uid": "uid-secondary"}
    return primary, secondary


def _failed_state(tmp_path, *, phase=Phase.ACTIVATION, journal=JOURNAL, restore_only=False):
    """A real state file that failed in `phase`, with Argo CD paused and a journal slot present."""
    path = tmp_path / "state.json"
    state = StateManager(str(path))
    state.ensure_contexts(None if restore_only else "hub-a", "hub-b")
    identities = {"secondary": {"context": "hub-b", "cluster_uid": "uid-secondary"}}
    if not restore_only:
        identities["primary"] = {"context": "hub-a", "cluster_uid": "uid-primary"}
    state.ensure_hub_identities(identities)
    if journal is not None:
        state.state["config"]["migration_backups"] = copy.deepcopy(journal)
    state._set_config("argocd_run_id", "run-1")
    state._set_config("argocd_paused_apps", PAUSED if not restore_only else PAUSED[1:])
    state.mark_step_completed(STEP_PAUSE_ARGOCD_APPS)
    state.set_phase(phase)
    state.add_error(f"{phase.value} failed", phase.value)
    state.set_phase(Phase.FAILED)
    return path, state


def _args(**overrides):
    values = {"argocd_resume_on_failure": True, "restore_only": False, "force": False, "dry_run": False}
    values.update(overrides)
    return SimpleNamespace(**values)


def _on_disk(path):
    return json.loads(path.read_text())


def _marker(path):
    return _on_disk(path)["config"].get("argocd_repause_required")


RESUMED = argocd_lib.ResumeSummary(restored=2, already_resumed=0, failed=0)
# Restore completion recorded, post-activation completion not.
NO_POST_ACTIVATION = next(
    case["input"]["candidate"]
    for case in json.loads(VECTORS.read_text(encoding="utf-8"))["cases"]
    if case["id"] == "journal-completed-full-restore-2.17"
)


class TestRescueUnderJournal:
    @pytest.mark.parametrize("phase", [Phase.ACTIVATION, Phase.POST_ACTIVATION, Phase.FINALIZATION])
    def test_marker_is_durable_before_resume_and_the_failed_phase_is_kept(self, tmp_path, phase):
        path, state = _failed_state(tmp_path, phase=phase)
        seen = {}

        def resume(_self, _primary, _secondary):
            seen["marker"] = _marker(path)
            return RESUMED

        with patch.object(ArgocdPauseRegister, "resume", autospec=True, side_effect=resume):
            acm_switchover._attempt_argocd_resume_on_failure(_args(), state, *_clients(), LOGGER)

        assert seen["marker"]["failed_phase"] == phase.value
        stored = _on_disk(path)
        assert stored["config"]["argocd_repause_required"]["failed_phase"] == phase.value
        assert stored["config"]["migration_backups"] == JOURNAL
        assert stored["current_phase"] == Phase.FAILED.value
        assert stored["errors"][-1]["phase"] == phase.value
        assert STEP_PAUSE_ARGOCD_APPS in [step["name"] for step in stored["completed_steps"]]

    def test_an_invalid_journal_is_still_a_journal(self, tmp_path):
        path, state = _failed_state(tmp_path, journal={"schema_version": 99})
        with patch.object(ArgocdPauseRegister, "resume", return_value=RESUMED):
            acm_switchover._attempt_argocd_resume_on_failure(_args(), state, *_clients(), LOGGER)
        stored = _on_disk(path)
        assert stored["config"]["argocd_repause_required"]["failed_phase"] == Phase.ACTIVATION.value
        assert stored["errors"][-1]["phase"] == Phase.ACTIVATION.value

    def test_restore_only_keeps_the_failed_phase_instead_of_preflight(self, tmp_path):
        path, state = _failed_state(tmp_path, phase=Phase.POST_ACTIVATION, restore_only=True)
        _, secondary = _clients()
        with patch.object(ArgocdPauseRegister, "resume", return_value=RESUMED):
            acm_switchover._attempt_argocd_resume_on_failure(_args(restore_only=True), state, None, secondary, LOGGER)
        stored = _on_disk(path)
        assert stored["config"]["argocd_repause_required"]["failed_phase"] == Phase.POST_ACTIVATION.value
        assert stored["errors"][-1]["phase"] == Phase.POST_ACTIVATION.value
        assert STEP_PAUSE_ARGOCD_APPS in [step["name"] for step in stored["completed_steps"]]

    def test_a_marker_write_failure_issues_no_resume(self, tmp_path, caplog):
        path, state = _failed_state(tmp_path)
        with patch.object(RunRecord, "record_argocd_repause_required", side_effect=OSError("EIO")), patch.object(
            ArgocdPauseRegister, "resume"
        ) as resume:
            acm_switchover._attempt_argocd_resume_on_failure(_args(), state, *_clients(), LOGGER)
        resume.assert_not_called()
        stored = _on_disk(path)
        assert stored["errors"][-1]["phase"] == Phase.ACTIVATION.value
        assert "re-pause marker" in stored["errors"][-1]["error"]
        assert any(record.levelno == logging.ERROR and "re-pause marker" in record.message for record in caplog.records)

    @pytest.mark.parametrize(
        "outcome",
        [
            pytest.param(argocd_lib.ResumeSummary(restored=1, failed=1, remaining_in_register=1), id="partial"),
            pytest.param(RuntimeError("apiserver down"), id="raised"),
        ],
    )
    def test_a_failed_resume_keeps_the_marker_and_is_visible(self, tmp_path, caplog, outcome):
        path, state = _failed_state(tmp_path)
        kwargs = {"side_effect": outcome} if isinstance(outcome, Exception) else {"return_value": outcome}
        with patch.object(ArgocdPauseRegister, "resume", **kwargs):
            acm_switchover._attempt_argocd_resume_on_failure(_args(), state, *_clients(), LOGGER)
        stored = _on_disk(path)
        assert stored["config"]["argocd_repause_required"]["failed_phase"] == Phase.ACTIVATION.value
        assert stored["errors"][-1]["phase"] == Phase.ACTIVATION.value
        assert "resume-on-failure" in stored["errors"][-1]["error"]
        assert any(record.levelno == logging.ERROR for record in caplog.records)

    def test_an_interrupted_resume_leaves_the_marker(self, tmp_path):
        path, state = _failed_state(tmp_path)
        with patch.object(ArgocdPauseRegister, "resume", side_effect=KeyboardInterrupt):
            with pytest.raises(KeyboardInterrupt):
                acm_switchover._attempt_argocd_resume_on_failure(_args(), state, *_clients(), LOGGER)
        assert _marker(path)["failed_phase"] == Phase.ACTIVATION.value

    def test_dry_run_records_nothing(self, tmp_path, caplog):
        caplog.set_level(logging.INFO)
        path, state = _failed_state(tmp_path)
        before = path.read_bytes()
        with patch.object(ArgocdPauseRegister, "resume") as resume:
            acm_switchover._attempt_argocd_resume_on_failure(_args(dry_run=True), state, *_clients(), LOGGER)
        resume.assert_not_called()
        assert path.read_bytes() == before
        assert any("[DRY-RUN]" in record.message for record in caplog.records)

    def test_journal_free_rescue_still_rewinds(self, tmp_path):
        path, state = _failed_state(tmp_path, journal=None)
        with patch.object(ArgocdPauseRegister, "resume", return_value=RESUMED):
            acm_switchover._attempt_argocd_resume_on_failure(_args(), state, *_clients(), LOGGER)
        stored = _on_disk(path)
        assert "argocd_repause_required" not in stored["config"]
        assert stored["errors"][-1]["phase"] == Phase.PRIMARY_PREP.value
        assert STEP_PAUSE_ARGOCD_APPS not in [step["name"] for step in stored["completed_steps"]]


def _retry(
    tmp_path,
    *,
    restore_only=False,
    dry_run=False,
    pause=None,
    clear_error=None,
    phase=Phase.ACTIVATION,
    journal=JOURNAL,
    rescued=False,
):
    """Rescue a failed `phase`, then retry the same flow with every phase handler mocked.

    With rescued=True the state file from an earlier call is retried again without a new rescue.
    """
    primary, secondary = _clients()
    path = tmp_path / "state.json"
    if not rescued:
        path, state = _failed_state(tmp_path, phase=phase, journal=journal, restore_only=restore_only)
        with patch.object(ArgocdPauseRegister, "resume", return_value=RESUMED):
            acm_switchover._attempt_argocd_resume_on_failure(
                _args(restore_only=restore_only), state, None if restore_only else primary, secondary, LOGGER
            )
    order = []
    handlers = {}
    phases = {
        "preflight": Phase.PREFLIGHT,
        "primary_prep": Phase.PRIMARY_PREP,
        "activation": Phase.ACTIVATION,
        "post_activation": Phase.POST_ACTIVATION,
        "finalization": Phase.FINALIZATION,
        "restore_only_argocd": Phase.PREFLIGHT,
    }
    for name, phase in phases.items():

        def run(_args, run_state, *_rest, _name=name, _phase=phase):
            order.append(_name)
            run_state.set_phase(_phase)
            return True

        handlers[name] = Mock(name=name, side_effect=run)

    def pause_hubs(_self, hubs):
        order.append(("pause", [role for _, role in hubs]))
        return pause if pause is not None else PauseSummary(newly_paused=len(hubs), run_id="run-2")

    args = SimpleNamespace(
        argocd_resume_on_failure=True,
        argocd_manage=True,
        restore_only=restore_only,
        force=False,
        dry_run=dry_run,
        validate_only=False,
        state_file=str(path),
        method="passive",
        old_hub_action="secondary",
    )
    patches = [
        patch.object(ArgocdPauseRegister, "pause_hubs", autospec=True, side_effect=pause_hubs),
        patch("acm_switchover._run_phase_preflight", handlers["preflight"]),
        patch("acm_switchover._run_phase_primary_prep", handlers["primary_prep"]),
        patch("acm_switchover._run_phase_activation", handlers["activation"]),
        patch("acm_switchover._run_phase_post_activation", handlers["post_activation"]),
        patch("acm_switchover._run_phase_finalization", handlers["finalization"]),
        patch("acm_switchover._run_restore_only_argocd_pause", handlers["restore_only_argocd"]),
    ]
    if clear_error is not None:
        patches.append(patch.object(RunRecord, "clear_argocd_repause_required", side_effect=clear_error))
    for item in patches:
        item.start()
    try:
        state = StateManager(str(path))
        if restore_only:
            result = acm_switchover._run_restore_only_impl(args, state, secondary, LOGGER)
        else:
            result = acm_switchover._run_switchover_impl(args, state, primary, secondary, LOGGER)
    finally:
        for item in reversed(patches):
            item.stop()
    return path, state, result, order, (primary, secondary)


class TestRetryRepausesBeforePhaseWork:
    def test_switchover_retry_repauses_both_hubs_first_and_clears_the_marker(self, tmp_path):
        path, state, result, order, _ = _retry(tmp_path)
        assert result is True
        assert order[0] == ("pause", ["primary", "secondary"])
        assert order[1:] == ["activation", "post_activation", "finalization"]
        stored = _on_disk(path)
        assert stored["config"].get("argocd_repause_required") is None
        assert stored["config"]["migration_backups"] == JOURNAL

    def test_restore_only_retry_repauses_the_secondary_hub_only(self, tmp_path):
        path, state, result, order, _ = _retry(tmp_path, restore_only=True)
        assert result is True
        assert order[0] == ("pause", ["secondary"])
        assert "restore_only_argocd" not in order and "preflight" not in order
        assert _on_disk(path)["config"].get("argocd_repause_required") is None

    @pytest.mark.parametrize(
        "pause",
        [
            pytest.param(PauseSummary(failed=1, run_id="run-2"), id="failed"),
            pytest.param(PauseSummary(blocked=1, run_id="run-2"), id="blocked"),
        ],
    )
    def test_a_failed_repause_blocks_the_retry_visibly(self, tmp_path, pause):
        path, state, result, order, _ = _retry(tmp_path, pause=pause)
        assert result is False
        assert order == [("pause", ["primary", "secondary"])]
        stored = _on_disk(path)
        assert stored["config"]["argocd_repause_required"]["failed_phase"] == Phase.ACTIVATION.value
        assert stored["current_phase"] == Phase.FAILED.value
        assert stored["errors"][-1]["phase"] == Phase.ACTIVATION.value
        assert "re-pause" in stored["errors"][-1]["error"]

    def test_a_failed_marker_clearing_blocks_and_keeps_the_marker(self, tmp_path):
        path, state, result, order, _ = _retry(tmp_path, clear_error=OSError("EIO"))
        assert result is False
        assert order == [("pause", ["primary", "secondary"])]
        stored = _on_disk(path)
        assert stored["config"]["argocd_repause_required"]["failed_phase"] == Phase.ACTIVATION.value
        assert stored["errors"][-1]["phase"] == Phase.ACTIVATION.value

    def test_a_clear_replaced_on_disk_without_directory_durability_restores_the_marker(self, tmp_path):
        """A-28/D-42: the cleared file is visible, its directory fsync fails; nothing is silently discharged."""
        original_clear = RunRecord.clear_argocd_repause_required
        original_fsync = utils_module._fsync_directory
        fault = {"armed": False, "seen": None}

        def clear(run_record):
            fault["armed"] = True
            original_clear(run_record)

        def fsync_directory(path):
            if fault["armed"]:
                fault["armed"] = False
                # The replacement already landed: the marker is gone from the visible file.
                fault["seen"] = _marker(tmp_path / "state.json")
                raise OSError("EIO")
            original_fsync(path)

        with (
            patch.object(RunRecord, "clear_argocd_repause_required", autospec=True, side_effect=clear),
            patch("lib.utils._fsync_directory", side_effect=fsync_directory),
        ):
            path, state, result, order, _ = _retry(tmp_path)

        assert fault["seen"] is None
        assert result is False
        assert order == [("pause", ["primary", "secondary"])]
        stored = _on_disk(path)
        assert stored["config"]["argocd_repause_required"]["failed_phase"] == Phase.ACTIVATION.value
        assert "marker could not be cleared" in stored["errors"][-1]["error"]
        assert stored["errors"][-1]["phase"] == Phase.ACTIVATION.value

    def test_dry_run_retry_reports_and_writes_no_marker_change(self, tmp_path, caplog):
        caplog.set_level(logging.INFO)
        path, state, result, order, _ = _retry(tmp_path, dry_run=True)
        assert ("pause", ["primary", "secondary"]) not in order
        assert _on_disk(path)["config"]["argocd_repause_required"]["failed_phase"] == Phase.ACTIVATION.value
        assert any("[DRY-RUN]" in record.message and "re-pause" in record.message for record in caplog.records)


class TestFinalizationRetryWithAnIncompleteJournal:
    """D-41 composed: a finalization rescue's marker, then a retry over a journal lacking post-activation."""

    def test_the_refusal_comes_first_and_keeps_the_marker_for_the_next_run(self, tmp_path):
        path, _, result, order, clients = _retry(tmp_path, phase=Phase.FINALIZATION, journal=NO_POST_ACTIVATION)
        stored = _on_disk(path)

        # lib/workflow.py run_phase_flow refuses the journal before the before-phases re-pause:
        # no re-pause, no phase work, no cluster call and no journal or evidence write.
        assert result is False
        assert order == []
        for client in clients:
            assert [name for name, *_ in client.mock_calls if name != "get_cluster_identity"] == []
        assert stored["config"]["migration_backups"] == NO_POST_ACTIVATION
        assert "post_activation_not_completed" in stored["errors"][-1]["error"]
        # The obligation stays recorded for the run that follows.
        assert stored["config"]["argocd_repause_required"]["failed_phase"] == Phase.FINALIZATION.value

        # The operator follows the refusal's guidance; that next run re-pauses before any phase work.
        stored["current_phase"] = Phase.POST_ACTIVATION.value
        path.write_text(json.dumps(stored), encoding="utf-8")
        _, _, result, order, _ = _retry(tmp_path, rescued=True)

        assert result is True
        assert order == [("pause", ["primary", "secondary"]), "post_activation", "finalization"]
        assert _marker(path) is None


def test_the_marker_positions_the_retry_when_the_last_error_lost_the_phase(tmp_path):
    path, state = _failed_state(tmp_path)
    with patch.object(ArgocdPauseRegister, "resume", return_value=RESUMED):
        acm_switchover._attempt_argocd_resume_on_failure(_args(), state, *_clients(), LOGGER)
    state.add_error("an outer catch recorded this", Phase.FAILED.value)
    config = FailedStateConfig(resumable_phases=(Phase.ACTIVATION,), operation_noun="switchover")
    handle_failed_state(SimpleNamespace(force=False, validate_only=False), state, LOGGER, config)
    assert state.get_current_phase() == Phase.ACTIVATION
