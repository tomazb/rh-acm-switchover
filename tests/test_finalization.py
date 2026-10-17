"""Unit tests for modules/finalization.py.

Tests cover Finalization class for completing the switchover.
"""

import copy
import inspect
import json
import logging
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import ANY, Mock, call, patch

import pytest
from kubernetes.client.rest import ApiException

# Add parent to path to import modules directly
sys.path.insert(0, str(Path(__file__).parent.parent))

import modules.decommission as decommission_module
import modules.finalization as finalization_module
from lib.constants import BACKUP_SCHEDULE_DEFAULT_NAME, BACKUP_SCHEDULE_PLURAL
from lib.decommission_outcome import DecommissionResult, SubstepExecution, SubstepOutcome
from lib.exceptions import FatalError, SwitchoverError
from lib.run_record import RunRecord
from lib.strict_read import StrictReadOutcome, StrictReadStatus
from lib.waiter import WaitConditionResult

Finalization = finalization_module.Finalization
ACM_BACKUP_LABEL = {"cluster.open-cluster-management.io/backup-schedule-type": "managedClusters"}


def create_mock_step_context(is_step_completed_func, mark_step_completed_func):
    """Create a mock step context manager that mimics StepContext behavior."""

    @contextmanager
    def mock_step(step_name, logger=None):
        if is_step_completed_func(step_name):
            if logger:
                logger.info("Step already completed: %s", step_name)
            yield False
        else:
            yield True
            mark_step_completed_func(step_name)

    return mock_step


@pytest.fixture
def mock_secondary_client():
    """Create a mock KubeClient for secondary hub.

    Carries the destination-observability reads the integrated teardown's July
    section 4 gate makes, defaulted to a migrated destination so the gate passes.
    Tests that need a blocked gate override them.
    """
    secondary = Mock()
    secondary.list_custom_resources_strict.return_value = StrictReadOutcome(
        status=StrictReadStatus.ITEMS,
        items=[{"metadata": {"name": "observability", "uid": "destination-uid"}}],
        resource_version="dest-1",
    )
    secondary.get_namespace_strict.return_value = StrictReadOutcome(
        status=StrictReadStatus.ITEMS,
        resource={"metadata": {"name": decommission_module.OBSERVABILITY_NAMESPACE}},
        resource_version="dest-ns-1",
    )
    return secondary


def _serve_schedules(secondary, reads=None):
    """Serve strict BackupSchedule LISTs in order (verification, then the collision check and later).

    Every other strict LIST keeps the fixture's migrated-destination default.
    """
    default = secondary.list_custom_resources_strict.return_value
    reads = list(
        reads
        or [
            [{"metadata": {"name": "schedule"}, "spec": {"paused": False}}],
            [{"metadata": {"name": "schedule"}, "spec": {}, "status": {"phase": "Enabled"}}],
        ]
    )

    def _list(**kwargs):
        if kwargs.get("plural") == BACKUP_SCHEDULE_PLURAL:
            # The last schedule state persists once the collision check has run.
            return StrictReadOutcome.from_items(reads.pop(0) if len(reads) > 1 else reads[0])
        return default

    secondary.list_custom_resources_strict.side_effect = _list


def _serve_backups(secondary, reads):
    """Serve backup strict outcomes without replacing other strict resource reads."""
    previous = secondary.list_custom_resources_strict.side_effect
    default = secondary.list_custom_resources_strict.return_value
    outcomes = [
        StrictReadOutcome.error("read_failed") if isinstance(read, Exception) else StrictReadOutcome.from_items(read)
        for read in reads
    ]

    def _list(**kwargs):
        if kwargs.get("plural") == "backups":
            return outcomes.pop(0) if len(outcomes) > 1 else outcomes[0]
        return previous(**kwargs) if callable(previous) else default

    secondary.list_custom_resources_strict.side_effect = _list


@pytest.fixture
def mock_mco_primary():
    """Strict MCO reads for the shared teardown reached through Finalization.

    One coherent source hub across the whole sequence: observability is present
    when the gate re-reads it, and both the CR and its namespace are gone once the
    delete lands.
    """
    primary = Mock()
    mco = StrictReadOutcome(
        status=StrictReadStatus.ITEMS,
        resource={
            "metadata": {"name": "observability", "uid": "uid-1", "labels": {}},
        },
    )
    primary.get_custom_resource_strict.side_effect = [
        mco,  # the phase machine's guarded read
        mco,  # the destination gate's own fresh source read
        StrictReadOutcome(status=StrictReadStatus.OBJECT_ABSENT),  # absence poll
        StrictReadOutcome(status=StrictReadStatus.OBJECT_ABSENT),  # final verification
    ]
    primary.get_namespace_strict.side_effect = [
        StrictReadOutcome(  # the gate: source observability is present
            status=StrictReadStatus.ITEMS,
            resource={"metadata": {"name": decommission_module.OBSERVABILITY_NAMESPACE}},
            resource_version="source-ns-1",
        ),
        StrictReadOutcome(status=StrictReadStatus.NAMESPACE_ABSENT),  # drain
        StrictReadOutcome(status=StrictReadStatus.NAMESPACE_ABSENT),  # final verification
    ]
    primary.delete_custom_resource_preconditioned.return_value = None
    return primary


@pytest.fixture
def mock_state_manager():
    """Create a mock StateManager with step() context manager support."""
    mock = Mock()
    mock.is_step_completed.return_value = False
    # Set up step() to return a proper context manager
    mock.step.side_effect = create_mock_step_context(
        mock.is_step_completed,
        mock.mark_step_completed,
    )
    mock.state = {"completed_steps": []}
    # Back the config accessors with a real dict so tests can seed and read
    # cross-phase facts through RunRecord instead of raw key literals. The
    # deep copies mirror StateManager's ownership contract (stored and returned
    # values are isolated from the caller); unseeded keys resolve to the
    # caller-supplied default.
    config: dict = {}
    mock.config = config

    def _set_config(key, value):
        config[key] = copy.deepcopy(value)

    def _get_config(key, default=None):
        if key not in config:
            return default
        return copy.deepcopy(config[key])

    mock._set_config.side_effect = _set_config
    mock._get_config.side_effect = _get_config
    return mock


@pytest.fixture
def mock_backup_manager():
    """Create a mock BackupScheduleManager."""
    with patch("modules.finalization.BackupScheduleManager") as mock:
        yield mock.return_value


def _journal_vector(case_id):
    path = Path(__file__).parent / "fixtures" / "r4_04_migration_evidence_vectors.json"
    case = next(case for case in json.loads(path.read_text(encoding="utf-8"))["cases"] if case["id"] == case_id)
    return case["input"]["candidate"]


CLEANUP_DONE = _journal_vector("journal-cleanup-completed")


@pytest.fixture
def terminal_cleanup():
    """The journaled Restore cleanup is already terminal and its evidence complete: the test covers other
    finalization work."""
    with (
        patch.object(Finalization, "_run_restore_cleanup_transaction", return_value=CLEANUP_DONE),
        patch.object(Finalization, "_require_journaled_locator_absent"),
        patch.object(Finalization, "_require_old_hub_evidence"),
        patch.object(Finalization, "_cleanup_restore_resources") as generic,
    ):
        yield generic


@pytest.fixture
def old_hub_evidence_complete():
    """The migration evidence gate at the old-hub adapter entry passes: the test covers the adapter's own work."""
    with patch.object(Finalization, "_require_old_hub_evidence") as gate:
        yield gate


@pytest.fixture
def finalization(mock_secondary_client, mock_state_manager, mock_backup_manager):
    """Create Finalization instance."""
    return Finalization(
        secondary_client=mock_secondary_client,
        state_manager=mock_state_manager,
        acm_version="2.12.0",
    )


@pytest.fixture
def finalization_with_primary(mock_secondary_client, mock_state_manager, mock_backup_manager):
    """Create Finalization instance with primary client."""
    primary = Mock()
    fin = Finalization(
        secondary_client=mock_secondary_client,
        state_manager=mock_state_manager,
        acm_version="2.12.0",
        primary_client=primary,
        primary_has_observability=True,
    )
    return fin, primary


@pytest.mark.unit
class TestFinalization:
    """Tests for Finalization class."""

    @patch("modules.finalization.time")
    def test_finalize_success(
        self,
        mock_time,
        terminal_cleanup,
        finalization,
        mock_secondary_client,
        mock_state_manager,
        mock_backup_manager,
    ):
        """Test successful finalization workflow."""
        # Mock time to avoid loops
        mock_time.time.side_effect = [0, 1, 2, 3]
        backup_ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        # Strict BackupSchedule reads (verification, collision check); then initial backups, loop 1
        _serve_schedules(mock_secondary_client)
        _serve_backups(
            mock_secondary_client,
            [
                [],  # Initial backups
                [
                    {
                        "metadata": {"name": "backup-1", "labels": ACM_BACKUP_LABEL},
                        "status": {"phase": "InProgress"},
                    }
                ],  # Loop iteration 1 - new ACM backup
            ],
        )

        mch_response = {"metadata": {"name": "multiclusterhub"}, "status": {"phase": "Running"}}
        # _fix_backup_schedule_collision calls get_custom_resource twice (before-delete read + post-delete
        # re-read), _verify_backup_integrity reads the recorded backup by name, then _verify_mch_health
        # calls it once for the MCH object.
        mock_secondary_client.get_custom_resource.side_effect = [
            {"metadata": {"name": "schedule", "uid": "uid-1"}, "spec": {}},  # before deletion
            None,  # after deletion — schedule gone, safe to create
            {
                "metadata": {
                    "name": "backup-1",
                    "creationTimestamp": backup_ts,
                    "labels": ACM_BACKUP_LABEL,
                },
                "status": {"phase": "Completed", "completionTimestamp": backup_ts},
            },  # verify_backup_integrity re-reads the recorded backup by name
            mch_response,  # MCH health check
        ]
        mock_secondary_client.get_pods.return_value = []

        result = finalization.finalize()

        assert result is True

        # Verify steps (now 7 steps with backup integrity and old hub handling)
        mock_backup_manager.ensure_enabled.assert_called_with("2.12.0")
        assert mock_state_manager.mark_step_completed.call_count == 7
        mock_state_manager.mark_step_completed.assert_has_calls(
            [
                call("enable_backup_schedule"),
                call("verify_backup_schedule_enabled"),
                call("fix_backup_collision"),
                call("verify_new_backups"),
                call("verify_backup_integrity"),
                call("verify_mch_health"),
                call("handle_old_hub"),
            ]
        )

    def test_finalize_skips_completed_steps(
        self, terminal_cleanup, finalization, mock_state_manager, mock_backup_manager
    ):
        """Test that completed steps are skipped."""
        mock_state_manager.is_step_completed.return_value = True

        result = finalization.finalize()

        assert result is True
        mock_backup_manager.ensure_enabled.assert_not_called()
        # verify_new_backups is internal method, hard to assert not called directly without mocking class method,
        # but we can infer from lack of client calls if we didn't mock list_custom_resources

    def test_decommission_old_hub_raises_when_decommission_returns_false(
        self, mock_secondary_client, mock_state_manager, mock_backup_manager
    ):
        """Decommission failure must raise instead of being downgraded to a warning."""
        primary = Mock()
        fin = Finalization(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            acm_version="2.12.0",
            primary_client=primary,
            primary_has_observability=True,
            old_hub_action="decommission",
        )

        with patch("modules.finalization.Decommission") as decommission_class:
            decommission_class.return_value.decommission.return_value = DecommissionResult(
                substeps={"multiclusterhub": SubstepOutcome.FAILED},
                not_attempted=(),
            )

            with pytest.raises(SwitchoverError, match="decommission failed"):
                fin._decommission_old_hub()

    def test_integrated_decommission_failure_keeps_its_own_message_context(
        self, mock_secondary_client, mock_state_manager, mock_backup_manager
    ):
        """The caller-specific message context survives the outcome-algebra rewrite."""
        primary = Mock()
        fin = Finalization(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            acm_version="2.12.0",
            primary_client=primary,
            primary_has_observability=True,
            old_hub_action="decommission",
        )

        with patch("modules.finalization.Decommission") as decommission_class:
            decommission_class.return_value.decommission.return_value = DecommissionResult(
                substeps={"observability": SubstepOutcome.FAILED},
                not_attempted=("managed_clusters",),
            )

            with pytest.raises(SwitchoverError) as excinfo:
                fin._decommission_old_hub()

        assert "Manual cleanup is required" in str(excinfo.value)
        assert "observability" in str(excinfo.value)

    def test_integrated_decommission_passes_its_own_run_record(
        self, mock_secondary_client, mock_state_manager, mock_backup_manager
    ):
        """The integrated path opens the durable channel with finalization's own RunRecord."""
        primary = Mock()
        fin = Finalization(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            acm_version="2.12.0",
            primary_client=primary,
            primary_has_observability=True,
            old_hub_action="decommission",
        )

        with patch("modules.finalization.Decommission") as decommission_class:
            decommission_class.return_value.decommission.return_value = DecommissionResult(
                substeps={"observability": SubstepOutcome.COMPLETED},
                not_attempted=(),
            )

            fin._decommission_old_hub()

        assert decommission_class.call_args.kwargs["run_record"] is fin.run_record
        decommission_class.return_value.decommission.assert_called_once_with(interactive=False)

    def test_finalization_maps_succeeded_explicitly_not_object_truthiness(self):
        source = inspect.getsource(Finalization._decommission_old_hub)
        assert ".succeeded" in source
        assert "if result:" not in source

    @patch("modules.finalization.time")
    def test_verify_new_backups_success(self, mock_time, finalization, mock_secondary_client):
        """Test backup verification logic finding a new backup."""
        # Mock time.time() to increment, avoiding real sleep calls
        # Calls: start_time, check 1, check 2, check 3
        mock_time.time.side_effect = [0, 0, 1, 2]

        # Sequence of API calls:
        # 1. Initial list (empty)
        # 2. Loop 1 list (still empty)
        # 3. Loop 2 list (new backup found)
        # Velero uses "Completed" phase, not "Finished"
        _serve_backups(
            mock_secondary_client,
            [
                [],
                [],
                [
                    {
                        "metadata": {"name": "new-backup", "labels": ACM_BACKUP_LABEL},
                        "status": {"phase": "Completed"},
                    }
                ],
            ],
        )

        finalization._verify_new_backups(timeout=10)

        assert mock_secondary_client.list_custom_resources_strict.call_count == 3

    @patch("modules.finalization.time")
    def test_verify_new_backups_timeout(self, mock_time, finalization, mock_secondary_client):
        """Backup verification timeout must raise SwitchoverError (fail closed)."""
        mock_time.time.side_effect = [0, 10, 45, 51]
        _serve_backups(mock_secondary_client, [[]])

        with pytest.raises(SwitchoverError, match="No new backup created"):
            finalization._verify_new_backups(timeout=50)

    @patch("modules.finalization.time")
    def test_verify_new_backups_timeout_without_recorded_backup_raises_hard_failure(
        self, mock_time, finalization, mock_secondary_client
    ):
        """Without a recorded post-switchover backup, timeout must raise instead of silently succeeding."""
        mock_time.time.side_effect = [0, 15, 45, 61]
        _serve_backups(mock_secondary_client, [[]])

        with pytest.raises(SwitchoverError, match="No new backup created within 60s"):
            finalization._verify_new_backups(timeout=60)

        mock_secondary_client.get_custom_resource.assert_not_called()

    @patch("modules.finalization.time")
    def test_verify_new_backups_stores_backup_name(self, mock_time, finalization, mock_secondary_client):
        """Successful backup detection must record the backup name in state."""
        mock_time.time.side_effect = [0, 1]
        _serve_backups(
            mock_secondary_client,
            [
                [],
                [
                    {
                        "metadata": {"name": "acm-backup-001", "labels": ACM_BACKUP_LABEL},
                        "status": {"phase": "Completed"},
                    }
                ],
            ],
        )

        finalization._verify_new_backups(timeout=10)

        assert RunRecord(finalization.state).new_backup() == "acm-backup-001"

    def test_verify_new_backups_reuses_recorded_backup_name(self, finalization, mock_secondary_client):
        """If a recorded post-switchover backup still exists, resume should succeed immediately."""
        recorded_backup = {
            "metadata": {"name": "acm-backup-001", "labels": ACM_BACKUP_LABEL},
            "status": {"phase": "Completed"},
        }
        _serve_backups(mock_secondary_client, [[recorded_backup]])
        mock_secondary_client.get_custom_resource.return_value = recorded_backup
        RunRecord(finalization.state).record_new_backup("acm-backup-001")
        finalization.state._set_config.reset_mock()

        finalization._verify_new_backups(timeout=10)

        mock_secondary_client.get_custom_resource.assert_called_once()
        # The reuse path must re-record the name, not merely leave the seeded value in place.
        assert finalization.state._set_config.called
        assert RunRecord(finalization.state).new_backup() == "acm-backup-001"

    def test_verify_new_backups_accepts_existing_post_enable_backup_on_resume(
        self, finalization, mock_secondary_client
    ):
        """If the first post-enable ACM backup already exists on resume, do not require a second backup."""
        backup_ts = "2026-03-06T10:05:00Z"
        enabled_ts = "2026-03-06T10:00:00Z"
        existing_backup = {
            "metadata": {
                "name": "acm-backup-001",
                "creationTimestamp": backup_ts,
                "labels": ACM_BACKUP_LABEL,
            },
            "status": {"phase": "Completed", "completionTimestamp": backup_ts},
        }
        _serve_backups(mock_secondary_client, [[existing_backup]])
        mock_secondary_client.get_custom_resource.return_value = None
        RunRecord(finalization.state).record_backup_watch_started(enabled_ts)

        finalization._verify_new_backups(timeout=10)

        assert RunRecord(finalization.state).new_backup() == "acm-backup-001"

    @patch("modules.finalization.time")
    def test_verify_new_backups_accepts_known_acm_name_without_label_and_logs_warning(
        self, mock_time, finalization, mock_secondary_client, caplog
    ):
        """Known ACM backup names are accepted with a warning when the ACM label is missing."""
        mock_time.time.side_effect = [0, 0, 1, 2]
        _serve_backups(
            mock_secondary_client,
            [
                [],
                [],
                [
                    {
                        "metadata": {"name": "acm-managed-clusters-schedule-20260306100000"},
                        "status": {"phase": "Completed"},
                    }
                ],
            ],
        )

        with caplog.at_level(logging.WARNING):
            finalization._verify_new_backups(timeout=10)

        assert RunRecord(finalization.state).new_backup() == "acm-managed-clusters-schedule-20260306100000"
        assert finalization_module.ACM_BACKUP_SCHEDULE_TYPE_LABEL in caplog.text
        assert "name-pattern fallback" in caplog.text

    @patch("modules.finalization.time")
    def test_verify_new_backups_ignores_unrelated_velero_backups(self, mock_time, finalization, mock_secondary_client):
        """Only ACM-owned backups should count as post-switchover evidence."""
        mock_time.time.side_effect = [0, 0, 1, 2]
        _serve_backups(
            mock_secondary_client,
            [
                [],
                [{"metadata": {"name": "manual-backup"}, "status": {"phase": "Completed"}}],
                [
                    {
                        "metadata": {"name": "acm-backup-001", "labels": ACM_BACKUP_LABEL},
                        "status": {"phase": "Completed"},
                    }
                ],
            ],
        )

        finalization._verify_new_backups(timeout=10)

        assert RunRecord(finalization.state).new_backup() == "acm-backup-001"

    @patch("modules.finalization.time")
    def test_verify_new_backups_retries_after_transient_list_error(
        self, mock_time, finalization, mock_secondary_client
    ):
        """Transient backup list failures should be tolerated until a later poll succeeds."""
        mock_time.time.side_effect = [0, 0, 1]
        _serve_backups(
            mock_secondary_client,
            [
                [],
                ApiException(status=500, reason="temporary failure"),
                [
                    {
                        "metadata": {"name": "acm-backup-001", "labels": ACM_BACKUP_LABEL},
                        "status": {"phase": "Completed"},
                    }
                ],
            ],
        )

        finalization._verify_new_backups(timeout=10)

        assert mock_secondary_client.list_custom_resources_strict.call_count == 3
        assert RunRecord(finalization.state).new_backup() == "acm-backup-001"

    def test_verify_new_backups_wraps_initial_transient_list_error(self, finalization, mock_secondary_client):
        """Initial backup discovery should not leak raw ApiException on retryable failures."""
        _serve_backups(mock_secondary_client, [ApiException(status=500, reason="temporary failure")])

        with pytest.raises(
            SwitchoverError,
            match="Failed to list Velero backups before waiting for a new ACM backup",
        ):
            finalization._verify_new_backups(timeout=10)

        assert mock_secondary_client.list_custom_resources_strict.call_count == 1

    def test_cleanup_restore_resources_raises_when_delete_fails(
        self, finalization, mock_secondary_client, mock_state_manager
    ):
        """Restore cleanup must fail closed when a delete request does not succeed."""
        mock_secondary_client.list_custom_resources_strict.return_value = StrictReadOutcome.from_items(
            [{"metadata": {"name": "restore-acm-full"}, "status": {"phase": "Running"}}]
        )
        mock_secondary_client.delete_custom_resource.side_effect = ApiException(status=403, reason="Forbidden")

        with pytest.raises(SwitchoverError, match="Failed to delete restore resource"):
            finalization._cleanup_restore_resources(CLEANUP_DONE)

        mock_state_manager._set_config.assert_called_once_with("archived_restores", ANY)

    @patch("modules.finalization.time")
    def test_verify_new_backups_exhausts_unreadable_poll_inventory(
        self, mock_time, finalization, mock_secondary_client
    ):
        """Strict outcomes do not expose HTTP classes; repeated unreadable polls exhaust the wait."""
        mock_time.time.side_effect = [0, 0, 11]
        _serve_backups(
            mock_secondary_client,
            [
                [],
                ApiException(status=403, reason="Forbidden"),
            ],
        )

        with pytest.raises(SwitchoverError, match="No new backup created"):
            finalization._verify_new_backups(timeout=10)

        mock_time.sleep.assert_called_once()
        assert mock_secondary_client.list_custom_resources_strict.call_count == 2

    def test_verify_backup_integrity_success(self, finalization, mock_secondary_client):
        """Backup integrity should pass for a recent completed backup with no errors (recorded name path)."""
        backup_ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        backup = {
            "metadata": {
                "name": "backup-1",
                "creationTimestamp": backup_ts,
                "labels": ACM_BACKUP_LABEL,
            },
            "status": {
                "phase": "Completed",
                "completionTimestamp": backup_ts,
                "errors": 0,
                "warnings": 0,
            },
        }
        mock_secondary_client.get_custom_resource.return_value = backup
        mock_secondary_client.get_pods.return_value = []
        finalization._cached_schedules = []
        RunRecord(finalization.state).record_new_backup("backup-1")

        finalization._verify_backup_integrity(max_age_seconds=600)

    def test_verify_backup_schedule_enabled_raises_switchover_error_when_missing(self, finalization):
        finalization._cached_schedules = []

        with pytest.raises(
            SwitchoverError,
            match="No BackupSchedule found while verifying finalization",
        ):
            finalization._verify_backup_schedule_enabled()

    def test_verify_backup_schedule_enabled_raises_when_multiple_schedules_exist(self, finalization):
        finalization._cached_schedules = [
            {"metadata": {"name": "schedule-a"}, "spec": {"paused": False}},
            {"metadata": {"name": "schedule-b"}, "spec": {"paused": False}},
        ]

        with pytest.raises(SwitchoverError, match="Multiple BackupSchedules"):
            finalization._verify_backup_schedule_enabled()

    def test_verify_backup_schedule_enabled_warns_when_missing_in_restore_only(
        self, mock_secondary_client, mock_state_manager, mock_backup_manager
    ):
        fin = Finalization(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            acm_version="2.12.0",
            restore_only=True,
        )
        fin._cached_schedules = []

        with patch.object(finalization_module, "logger") as logger:
            fin._verify_backup_schedule_enabled()

        logger.warning.assert_called_with(
            "No BackupSchedule found during restore-only finalization; manual backup setup is required"
        )

    def test_fix_backup_schedule_collision_warns_when_missing_in_restore_only(
        self, mock_secondary_client, mock_state_manager, mock_backup_manager
    ):
        fin = Finalization(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            acm_version="2.12.0",
            restore_only=True,
        )
        fin._cached_schedules = []
        mock_secondary_client.list_custom_resources_strict.return_value = StrictReadOutcome.from_items([])

        with patch.object(finalization_module, "logger") as logger:
            fin._fix_backup_schedule_collision()

        logger.warning.assert_called_with(
            "Skipping BackupSchedule collision repair in restore-only mode because no BackupSchedule exists yet"
        )

    def test_verify_new_backups_skips_when_restore_only_has_no_backup_schedule(
        self, mock_secondary_client, mock_state_manager, mock_backup_manager
    ):
        fin = Finalization(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            acm_version="2.12.0",
            restore_only=True,
        )
        fin._cached_schedules = []

        with patch.object(finalization_module, "logger") as logger:
            fin._verify_new_backups(timeout=10)

        logger.warning.assert_called_with(
            "Skipping backup continuity verification in restore-only mode because no BackupSchedule exists yet"
        )
        mock_secondary_client.list_custom_resources.assert_not_called()

    def test_verify_backup_integrity_skips_when_restore_only_has_no_backup_schedule(
        self, mock_secondary_client, mock_state_manager, mock_backup_manager
    ):
        fin = Finalization(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            acm_version="2.12.0",
            restore_only=True,
        )
        fin._cached_schedules = []

        with patch.object(finalization_module, "logger") as logger:
            fin._verify_backup_integrity(max_age_seconds=600)

        logger.warning.assert_called_with(
            "Skipping backup integrity verification in restore-only mode because no BackupSchedule exists yet"
        )
        mock_secondary_client.get_custom_resource.assert_not_called()

    def test_verify_multiclusterhub_health_raises_switchover_error_when_missing(
        self, finalization, mock_secondary_client
    ):
        mock_secondary_client.get_custom_resource.return_value = None
        mock_secondary_client.list_custom_resources.return_value = []

        with pytest.raises(SwitchoverError, match="No MultiClusterHub resource found on secondary hub"):
            finalization._verify_multiclusterhub_health(timeout=1, interval=0)

    def test_ensure_auto_import_default_raises_when_delete_fails(
        self, mock_secondary_client, mock_state_manager, mock_backup_manager
    ):
        fin = Finalization(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            acm_version="2.14.0",
        )
        mock_secondary_client.get_configmap.return_value = {
            "data": {finalization_module.AUTO_IMPORT_STRATEGY_KEY: finalization_module.AUTO_IMPORT_STRATEGY_SYNC}
        }
        mock_secondary_client.delete_configmap.side_effect = ApiException(status=403, reason="Forbidden")
        RunRecord(mock_state_manager).record_auto_import_override()

        with pytest.raises(SwitchoverError, match="Failed to reset autoImportStrategy to default"):
            fin._ensure_auto_import_default()

    def test_auto_import_reset_missing_configmap_clears_state(
        self, mock_secondary_client, mock_backup_manager, tmp_path
    ):
        """A missing configmap means reset already completed and state should clear."""
        from lib.utils import StateManager

        state = StateManager(str(tmp_path / "state.json"))
        fin = Finalization(
            secondary_client=mock_secondary_client,
            state_manager=state,
            acm_version="2.14.0",
        )
        RunRecord(state).record_auto_import_override()
        mock_secondary_client.get_configmap.return_value = None

        assert fin._ensure_auto_import_default() is True

        assert RunRecord(state).auto_import_override_pending() is False
        mock_secondary_client.delete_configmap.assert_not_called()

    def test_auto_import_reset_non_sync_strategy_clears_state_without_delete(
        self, mock_secondary_client, mock_backup_manager, tmp_path
    ):
        """A non-Sync strategy is already safe and must not trigger a delete."""
        from lib.utils import StateManager

        state = StateManager(str(tmp_path / "state.json"))
        fin = Finalization(
            secondary_client=mock_secondary_client,
            state_manager=state,
            acm_version="2.14.0",
        )
        RunRecord(state).record_auto_import_override()
        mock_secondary_client.get_configmap.return_value = {
            "data": {finalization_module.AUTO_IMPORT_STRATEGY_KEY: finalization_module.AUTO_IMPORT_STRATEGY_DEFAULT}
        }

        assert fin._ensure_auto_import_default() is True

        assert RunRecord(state).auto_import_override_pending() is False
        mock_secondary_client.delete_configmap.assert_not_called()

    def test_auto_import_reset_delete_404_is_treated_as_complete(
        self, mock_secondary_client, mock_backup_manager, tmp_path
    ):
        """Deleting an already-absent configmap should succeed idempotently."""
        from lib.utils import StateManager

        state = StateManager(str(tmp_path / "state.json"))
        fin = Finalization(
            secondary_client=mock_secondary_client,
            state_manager=state,
            acm_version="2.14.0",
        )
        RunRecord(state).record_auto_import_override()
        mock_secondary_client.get_configmap.return_value = {
            "data": {finalization_module.AUTO_IMPORT_STRATEGY_KEY: finalization_module.AUTO_IMPORT_STRATEGY_SYNC}
        }
        mock_secondary_client.delete_configmap.side_effect = ApiException(status=404, reason="Not Found")

        assert fin._ensure_auto_import_default() is True

        assert RunRecord(state).auto_import_override_pending() is False
        mock_secondary_client.delete_configmap.assert_called_once_with(
            finalization_module.MCE_NAMESPACE,
            finalization_module.IMPORT_CONTROLLER_CONFIG_CM,
        )

    def test_auto_import_reset_delete_success_clears_persisted_state(
        self, mock_secondary_client, mock_backup_manager, tmp_path
    ):
        """Deleting the Sync override should restore default state persistently."""
        from lib.utils import StateManager

        state_path = tmp_path / "state.json"
        state = StateManager(str(state_path))
        fin = Finalization(
            secondary_client=mock_secondary_client,
            state_manager=state,
            acm_version="2.14.0",
        )
        RunRecord(state).record_auto_import_override()
        mock_secondary_client.get_configmap.return_value = {
            "data": {finalization_module.AUTO_IMPORT_STRATEGY_KEY: finalization_module.AUTO_IMPORT_STRATEGY_SYNC}
        }

        assert fin._ensure_auto_import_default() is True

        assert RunRecord(StateManager(str(state_path))).auto_import_override_pending() is False
        mock_secondary_client.delete_configmap.assert_called_once_with(
            finalization_module.MCE_NAMESPACE,
            finalization_module.IMPORT_CONTROLLER_CONFIG_CM,
        )

    def test_ensure_auto_import_default_skips_state_updates_in_dry_run(
        self, mock_secondary_client, mock_state_manager, mock_backup_manager
    ):
        """Dry-run must not mutate state for the auto-import strategy reset step."""
        fin = Finalization(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            acm_version="2.14.0",
            dry_run=True,
        )

        assert fin._ensure_auto_import_default() is True

        mock_secondary_client.get_configmap.assert_not_called()
        mock_secondary_client.delete_configmap.assert_not_called()
        mock_state_manager._set_config.assert_not_called()
        mock_state_manager.mark_step_completed.assert_not_called()

    def test_ensure_auto_import_default_warns_and_skips_when_lookup_fails(
        self, mock_secondary_client, mock_state_manager, mock_backup_manager, caplog
    ):
        fin = Finalization(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            acm_version="2.14.0",
        )
        mock_secondary_client.get_configmap.side_effect = ApiException(status=403, reason="Forbidden")

        with caplog.at_level(logging.WARNING):
            assert fin._ensure_auto_import_default() is True

        assert "Unable to verify auto-import strategy" in caplog.text
        mock_secondary_client.delete_configmap.assert_not_called()

    def test_ensure_auto_import_default_raises_when_owned_lookup_fails(
        self, mock_secondary_client, mock_state_manager, mock_backup_manager
    ):
        fin = Finalization(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            acm_version="2.14.0",
        )
        mock_secondary_client.get_configmap.side_effect = ApiException(status=403, reason="Forbidden")
        RunRecord(mock_state_manager).record_auto_import_override()

        with pytest.raises(SwitchoverError, match="Failed to verify autoImportStrategy"):
            fin._ensure_auto_import_default()

    def test_ensure_auto_import_default_is_noop_before_acm_2_14(
        self, mock_secondary_client, mock_state_manager, mock_backup_manager
    ):
        fin = Finalization(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            acm_version="2.13.9",
        )

        assert fin._ensure_auto_import_default() is True

        mock_secondary_client.get_configmap.assert_not_called()
        mock_secondary_client.delete_configmap.assert_not_called()

    def test_finalize_exposes_reset_auto_import_strategy_as_explicit_step(
        self, terminal_cleanup, mock_secondary_client, mock_state_manager, mock_backup_manager
    ):
        """Finalization should expose auto-import reset in the visible step sequence."""
        fin = Finalization(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            acm_version="2.14.0",
        )

        with (
            patch.object(fin, "_enable_backup_schedule"),
            patch.object(fin, "_verify_backup_schedule_enabled"),
            patch.object(fin, "_fix_backup_schedule_collision"),
            patch.object(fin, "_verify_new_backups"),
            patch.object(fin, "_verify_backup_integrity"),
            patch.object(fin, "_verify_multiclusterhub_health"),
            patch.object(fin, "_ensure_auto_import_default", return_value=True) as reset_auto_import,
            patch.object(fin, "_handle_old_hub"),
            patch.object(fin, "_get_backup_verify_timeout", return_value=600),
        ):
            assert fin.finalize() is True

        mock_state_manager.is_step_completed.assert_any_call("reset_auto_import_strategy")
        reset_auto_import.assert_called_once()
        mock_state_manager.mark_step_completed.assert_any_call("reset_auto_import_strategy")

    def test_backup_verify_timeout_derived_from_schedule(self, finalization):
        schedule = {
            "metadata": {"name": "schedule-rhacm"},
            "spec": {"veleroSchedule": "0 */4 * * *"},
        }
        finalization._cached_schedules = [schedule]

        timeout = finalization._get_backup_verify_timeout()

        assert timeout == 4 * 3600

    def test_backup_max_age_derived_from_schedule(self, finalization):
        schedule = {
            "metadata": {"name": "schedule-rhacm"},
            "spec": {"veleroSchedule": "0 */4 * * *"},
        }
        finalization._cached_schedules = [schedule]

        max_age = finalization._get_backup_max_age_seconds(600)

        assert max_age == (4 * 3600 + 600)

    @pytest.mark.parametrize(
        ("cron_expr", "expected_seconds"),
        [
            ("*/15 * * * *", 15 * 60),
            ("0 */2 * * *", 2 * 3600),
            ("0 0 * * *", 24 * 3600),
        ],
    )
    def test_parse_cron_interval_seconds(self, finalization, cron_expr, expected_seconds):
        assert finalization._parse_cron_interval_seconds(cron_expr) == expected_seconds

    @pytest.mark.parametrize("cron_expr", ["@monthly", "not-a-cron"])
    def test_get_backup_schedule_interval_seconds_returns_none_for_invalid_cron(self, finalization, cron_expr):
        """Unsupported BackupSchedule cron expressions must fall back to None instead of raising."""
        finalization._cached_schedules = [
            {
                "metadata": {"name": "schedule-rhacm"},
                "spec": {"veleroSchedule": cron_expr},
            }
        ]

        assert finalization._get_backup_schedule_interval_seconds() is None

    def test_verify_backup_integrity_skips_age_without_new_backup(self, finalization, mock_secondary_client):
        """Backup age enforcement should be skipped if no post-switchover backup name is recorded."""
        backup_ts = (datetime.now(timezone.utc) - timedelta(seconds=1200)).isoformat().replace("+00:00", "Z")
        backup = {
            "metadata": {
                "name": "backup-1",
                "creationTimestamp": backup_ts,
                "labels": ACM_BACKUP_LABEL,
            },
            "status": {
                "phase": "Completed",
                "completionTimestamp": backup_ts,
                "errors": 0,
                "warnings": 0,
            },
        }
        _serve_backups(mock_secondary_client, [[backup]])
        mock_secondary_client.get_pods.return_value = []
        # Nothing recorded (fixture default) → falls back to latest-by-timestamp, age check skipped

        finalization._verify_backup_integrity(max_age_seconds=600)

    def test_verify_backup_integrity_fallback_ignores_unrelated_backups(self, finalization, mock_secondary_client):
        """Fallback integrity path should consider only ACM-owned backups."""
        acm_backup_ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        manual_backup_ts = (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
        _serve_backups(
            mock_secondary_client,
            [
                [
                    {
                        "metadata": {
                            "name": "manual-backup",
                            "creationTimestamp": manual_backup_ts,
                        },
                        "status": {
                            "phase": "Completed",
                            "completionTimestamp": manual_backup_ts,
                            "errors": 99,
                        },
                    },
                    {
                        "metadata": {
                            "name": "backup-1",
                            "creationTimestamp": acm_backup_ts,
                            "labels": ACM_BACKUP_LABEL,
                        },
                        "status": {
                            "phase": "Completed",
                            "completionTimestamp": acm_backup_ts,
                            "errors": 0,
                            "warnings": 0,
                        },
                    },
                ]
            ],
        )
        mock_secondary_client.get_pods.return_value = []
        # Nothing recorded (fixture default) → the fallback path selects the latest ACM-owned backup
        finalization._cached_schedules = [
            {
                "metadata": {"name": "acm-hub-backup"},
                "spec": {"veleroSchedule": "*/30 * * * *"},
            }
        ]

        finalization._verify_backup_integrity(max_age_seconds=600)

    def test_verify_backup_integrity_enforces_age_with_recorded_backup_name(self, finalization, mock_secondary_client):
        """Age enforcement fires when backup name is recorded and backup is too old."""
        backup_ts = (datetime.now(timezone.utc) - timedelta(seconds=1200)).isoformat().replace("+00:00", "Z")
        backup = {
            "metadata": {
                "name": "backup-1",
                "creationTimestamp": backup_ts,
                "labels": ACM_BACKUP_LABEL,
            },
            "status": {
                "phase": "Completed",
                "completionTimestamp": backup_ts,
                "errors": 0,
                "warnings": 0,
            },
        }
        mock_secondary_client.get_custom_resource.return_value = backup
        mock_secondary_client.get_pods.return_value = []
        finalization._cached_schedules = []
        RunRecord(finalization.state).record_new_backup("backup-1")

        with pytest.raises(SwitchoverError):
            finalization._verify_backup_integrity(max_age_seconds=600)

    def test_verify_backup_integrity_skips_age_if_backup_before_enable(self, finalization, mock_secondary_client):
        """Backup age enforcement should be skipped if backup predates enable timestamp."""
        backup_ts = (datetime.now(timezone.utc) - timedelta(seconds=1200)).isoformat().replace("+00:00", "Z")
        enabled_ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        backup = {
            "metadata": {
                "name": "backup-1",
                "creationTimestamp": backup_ts,
                "labels": ACM_BACKUP_LABEL,
            },
            "status": {
                "phase": "Completed",
                "completionTimestamp": backup_ts,
                "errors": 0,
                "warnings": 0,
            },
        }
        _serve_backups(mock_secondary_client, [[backup]])
        mock_secondary_client.get_pods.return_value = []
        RunRecord(finalization.state).record_backup_watch_started(enabled_ts)

        finalization._verify_backup_integrity(max_age_seconds=600)

    def test_verify_backup_integrity_uses_recorded_name_not_latest(self, finalization, mock_secondary_client):
        """When backup name is recorded, that specific backup is verified — not the latest in the namespace."""
        switchover_ts = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat().replace("+00:00", "Z")
        switchover_backup = {
            "metadata": {
                "name": "acm-backup-switchover",
                "creationTimestamp": switchover_ts,
                "labels": ACM_BACKUP_LABEL,
            },
            "status": {
                "phase": "Completed",
                "completionTimestamp": switchover_ts,
                "errors": 0,
                "warnings": 0,
            },
        }
        mock_secondary_client.get_custom_resource.return_value = switchover_backup
        mock_secondary_client.get_pods.return_value = []
        finalization._cached_schedules = []
        RunRecord(finalization.state).record_new_backup("acm-backup-switchover")

        finalization._verify_backup_integrity(max_age_seconds=600)

        # Must fetch by name, not list the namespace and pick the latest
        mock_secondary_client.get_custom_resource.assert_called_once()
        call_kwargs = mock_secondary_client.get_custom_resource.call_args
        assert call_kwargs.kwargs.get("name") == "acm-backup-switchover"
        mock_secondary_client.list_custom_resources.assert_not_called()

    def test_verify_backup_integrity_fails_when_recorded_backup_missing(self, finalization, mock_secondary_client):
        """If the recorded post-switchover backup is pruned, fall back to latest ACM-owned backup."""
        backup_ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        fallback_backup = {
            "metadata": {
                "name": "acm-backup-newer",
                "creationTimestamp": backup_ts,
                "labels": ACM_BACKUP_LABEL,
            },
            "status": {
                "phase": "Completed",
                "completionTimestamp": backup_ts,
                "errors": 0,
                "warnings": 0,
            },
        }
        # get_custom_resource returns None for the recorded name (pruned)
        mock_secondary_client.get_custom_resource.return_value = None
        # list_custom_resources returns a fallback backup
        _serve_backups(mock_secondary_client, [[fallback_backup]])
        mock_secondary_client.get_pods.return_value = []
        finalization._cached_schedules = []
        RunRecord(finalization.state).record_new_backup("acm-backup-switchover")

        # Should NOT raise — should fall back to latest ACM-owned backup
        finalization._verify_backup_integrity(max_age_seconds=600)

    def test_verify_backup_integrity_fails_when_recorded_backup_is_not_acm_owned(
        self, finalization, mock_secondary_client
    ):
        """Recorded backup names must still resolve to ACM-owned backups."""
        backup_ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        mock_secondary_client.get_custom_resource.return_value = {
            "metadata": {"name": "manual-backup", "creationTimestamp": backup_ts},
            "status": {
                "phase": "Completed",
                "completionTimestamp": backup_ts,
                "errors": 0,
                "warnings": 0,
            },
        }
        mock_secondary_client.get_pods.return_value = []
        finalization._cached_schedules = []
        RunRecord(finalization.state).record_new_backup("manual-backup")

        with pytest.raises(SwitchoverError, match="not ACM-owned"):
            finalization._verify_backup_integrity(max_age_seconds=600)

    def test_verify_backup_integrity_rejects_recorded_backup_with_non_acm_owner_labels(
        self, finalization, mock_secondary_client
    ):
        """Recorded backups with unrelated owner labels must hard-fail integrity verification."""
        backup_ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        mock_secondary_client.get_custom_resource.return_value = {
            "metadata": {
                "name": "manual-backup",
                "creationTimestamp": backup_ts,
                "labels": {"cluster.open-cluster-management.io/backup-schedule-type": "manual"},
            },
            "status": {
                "phase": "Completed",
                "completionTimestamp": backup_ts,
                "errors": 0,
                "warnings": 0,
            },
        }
        mock_secondary_client.get_pods.return_value = []
        finalization._cached_schedules = []
        RunRecord(finalization.state).record_new_backup("manual-backup")

        with pytest.raises(SwitchoverError, match="not ACM-owned"):
            finalization._verify_backup_integrity(max_age_seconds=600)

        mock_secondary_client.list_custom_resources.assert_not_called()

    @patch("modules.finalization.wait_for_condition")
    def test_verify_backup_integrity_waits_for_completion(self, mock_wait, finalization, mock_secondary_client):
        """Backup integrity should wait for the recorded post-switchover backup to complete."""
        backup_ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        mock_wait.return_value = True
        in_progress_backup = {
            "metadata": {
                "name": "backup-1",
                "creationTimestamp": backup_ts,
                "labels": ACM_BACKUP_LABEL,
            },
            "status": {"phase": "InProgress"},
        }
        completed_backup = {
            "metadata": {
                "name": "backup-1",
                "creationTimestamp": backup_ts,
                "labels": ACM_BACKUP_LABEL,
            },
            "status": {
                "phase": "Completed",
                "completionTimestamp": backup_ts,
                "errors": 0,
                "warnings": 0,
            },
        }
        # First call returns InProgress backup (initial fetch), second returns Completed (after wait)
        mock_secondary_client.get_custom_resource.side_effect = [
            in_progress_backup,
            completed_backup,
        ]
        mock_secondary_client.get_pods.return_value = []
        finalization._cached_schedules = []
        RunRecord(finalization.state).record_new_backup("backup-1")

        finalization._verify_backup_integrity(max_age_seconds=600)

        mock_wait.assert_called_once()

    def test_verify_backup_integrity_fails_on_errors(self, finalization, mock_secondary_client):
        """Backup integrity should fail when the recorded backup reports errors."""
        backup_ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        mock_secondary_client.get_custom_resource.return_value = {
            "metadata": {
                "name": "backup-1",
                "creationTimestamp": backup_ts,
                "labels": ACM_BACKUP_LABEL,
            },
            "status": {
                "phase": "Completed",
                "completionTimestamp": backup_ts,
                "errors": 2,
            },
        }
        mock_secondary_client.get_pods.return_value = []
        finalization._cached_schedules = []
        RunRecord(finalization.state).record_new_backup("backup-1")

        with pytest.raises(SwitchoverError):
            finalization._verify_backup_integrity(max_age_seconds=600)

    @patch("modules.finalization.time.sleep")
    def test_fix_backup_schedule_collision_skips_create_on_uid_change_after_delete(
        self, mock_sleep, terminal_cleanup, finalization, mock_secondary_client
    ):
        """If schedule UID changes after delete, fail the step so resume does not mark it complete."""
        mock_sleep.return_value = None
        mock_secondary_client.list_custom_resources_strict.return_value = StrictReadOutcome.from_items(
            [
                {
                    "metadata": {"name": "schedule", "uid": "uid-old"},
                    "spec": {"veleroSchedule": "*/15 * * * *"},
                    "status": {"phase": "Enabled"},
                }
            ]
        )
        mock_secondary_client.get_custom_resource.side_effect = [
            {
                "metadata": {"name": "schedule", "uid": "uid-old"},
                "spec": {"veleroSchedule": "*/15 * * * *"},
            },
            {
                "metadata": {"name": "schedule", "uid": "uid-new"},
                "status": {"phase": "Enabled"},
            },
        ]

        with pytest.raises(SwitchoverError, match="reappeared with a different uid"):
            finalization._fix_backup_schedule_collision()

        mock_secondary_client.delete_custom_resource.assert_called_once()
        mock_secondary_client.create_custom_resource.assert_not_called()

    @patch("modules.finalization.wait_for_condition", return_value=True)
    @patch("modules.finalization.time.sleep")
    def test_fix_backup_schedule_collision_polls_for_delete_before_create(
        self, mock_sleep, mock_wait, terminal_cleanup, finalization, mock_secondary_client
    ):
        """Collision repair should poll for absence instead of using a fixed delete sleep."""
        mock_secondary_client.list_custom_resources_strict.return_value = StrictReadOutcome.from_items(
            [
                {
                    "metadata": {"name": "schedule", "uid": "uid-old"},
                    "spec": {"veleroSchedule": "*/15 * * * *"},
                    "status": {"phase": "Enabled"},
                }
            ]
        )
        mock_secondary_client.get_custom_resource.return_value = {
            "metadata": {"name": "schedule", "uid": "uid-old"},
            "spec": {"veleroSchedule": "*/15 * * * *"},
        }

        finalization._fix_backup_schedule_collision()

        mock_wait.assert_called_once()
        wait_kwargs = mock_wait.call_args.kwargs
        assert wait_kwargs["timeout"] > 0
        assert wait_kwargs["interval"] > 0
        mock_sleep.assert_not_called()
        mock_secondary_client.create_custom_resource.assert_called_once()

    @patch("modules.finalization.wait_for_condition", return_value=False)
    def test_wait_for_backup_schedule_deletion_accepts_absent_final_reread(
        self, mock_wait, finalization, mock_secondary_client
    ):
        """If the final re-check sees absence after timeout, deletion completed."""
        mock_secondary_client.get_custom_resource.return_value = None
        finalization._cached_schedules = [{"metadata": {"name": "cached"}}]

        finalization._wait_for_backup_schedule_deletion("schedule", "uid-old", timeout=1, interval=1)

        mock_wait.assert_called_once_with(
            "BackupSchedule schedule deletion",
            ANY,
            timeout=1,
            interval=1,
            logger=finalization_module.logger,
        )
        mock_secondary_client.get_custom_resource.assert_called_once_with(
            group="cluster.open-cluster-management.io",
            version="v1beta1",
            plural="backupschedules",
            name="schedule",
            namespace=finalization_module.BACKUP_NAMESPACE,
        )
        assert finalization._cached_schedules is None

    @patch("modules.finalization.wait_for_condition")
    def test_wait_for_backup_schedule_deletion_polls_exact_schedule_target(
        self, mock_wait, finalization, mock_secondary_client
    ):
        """Deletion polling should query the exact BackupSchedule object and report pending while it remains."""
        mock_secondary_client.get_custom_resource.return_value = {"metadata": {"name": "schedule", "uid": "uid-old"}}

        def capture_wait(description, condition_fn, **kwargs):
            assert description == "BackupSchedule schedule deletion"
            assert kwargs["timeout"] == 7
            assert kwargs["interval"] == 3
            result = condition_fn()
            assert isinstance(result, WaitConditionResult)
            assert result.done is False
            assert result.public_detail == "still present (uid=uid-old)"
            return True

        mock_wait.side_effect = capture_wait

        finalization._wait_for_backup_schedule_deletion("schedule", "uid-old", timeout=7, interval=3)

        mock_secondary_client.get_custom_resource.assert_called_once_with(
            group="cluster.open-cluster-management.io",
            version="v1beta1",
            plural="backupschedules",
            name="schedule",
            namespace=finalization_module.BACKUP_NAMESPACE,
        )

    @patch("modules.finalization.wait_for_condition")
    def test_wait_for_backup_schedule_deletion_fails_closed_on_uid_change(
        self, mock_wait, finalization, mock_secondary_client
    ):
        """Deletion polling must stop if another BackupSchedule appears with a different uid."""
        mock_secondary_client.get_custom_resource.return_value = {"metadata": {"name": "schedule", "uid": "uid-new"}}

        def capture_wait(_description, condition_fn, **_kwargs):
            condition_fn()
            return True

        mock_wait.side_effect = capture_wait

        with pytest.raises(SwitchoverError, match="different uid"):
            finalization._wait_for_backup_schedule_deletion("schedule", "uid-old", timeout=7, interval=3)

        mock_secondary_client.get_custom_resource.assert_called_once_with(
            group="cluster.open-cluster-management.io",
            version="v1beta1",
            plural="backupschedules",
            name="schedule",
            namespace=finalization_module.BACKUP_NAMESPACE,
        )

    @patch("modules.finalization.time.sleep")
    def test_fix_backup_schedule_collision_treats_409_with_healthy_schedule_as_success(
        self, mock_sleep, terminal_cleanup, finalization, mock_secondary_client
    ):
        """A 409 create conflict should be acceptable when schedule exists in a non-collision phase."""
        mock_sleep.return_value = None
        mock_secondary_client.list_custom_resources_strict.return_value = StrictReadOutcome.from_items(
            [
                {
                    "metadata": {"name": "schedule", "uid": "uid-old"},
                    "spec": {"veleroSchedule": "*/15 * * * *"},
                    "status": {"phase": "Enabled"},
                }
            ]
        )
        mock_secondary_client.get_custom_resource.side_effect = [
            {
                "metadata": {"name": "schedule", "uid": "uid-old"},
                "spec": {"veleroSchedule": "*/15 * * * *"},
            },
            None,
            {
                "metadata": {"name": "schedule", "uid": "uid-new"},
                "status": {"phase": "Enabled"},
            },
        ]
        mock_secondary_client.create_custom_resource.side_effect = ApiException(status=409)

        finalization._cached_schedules = [{"metadata": {"name": "cached"}}]
        finalization._fix_backup_schedule_collision()

        mock_secondary_client.delete_custom_resource.assert_called_once()
        mock_secondary_client.create_custom_resource.assert_called_once()
        assert finalization._cached_schedules is None

    @patch("modules.finalization.time.sleep")
    def test_fix_backup_schedule_collision_raises_when_409_reuses_original_uid(
        self, mock_sleep, terminal_cleanup, finalization, mock_secondary_client
    ):
        """A 409 create conflict must fail when the original schedule object still exists."""
        mock_sleep.return_value = None
        mock_secondary_client.list_custom_resources_strict.return_value = StrictReadOutcome.from_items(
            [
                {
                    "metadata": {"name": "schedule", "uid": "uid-old"},
                    "spec": {"veleroSchedule": "*/15 * * * *"},
                    "status": {"phase": "Enabled"},
                }
            ]
        )
        mock_secondary_client.get_custom_resource.side_effect = [
            {
                "metadata": {"name": "schedule", "uid": "uid-old"},
                "spec": {"veleroSchedule": "*/15 * * * *"},
            },
            None,
            {
                "metadata": {"name": "schedule", "uid": "uid-old"},
                "status": {"phase": "Enabled"},
            },
        ]
        mock_secondary_client.create_custom_resource.side_effect = ApiException(status=409)

        with pytest.raises(SwitchoverError, match="still has the original uid"):
            finalization._fix_backup_schedule_collision()

        assert finalization._cached_schedules is None

    @patch("modules.finalization.time.sleep")
    def test_fix_backup_schedule_collision_raises_when_409_schedule_is_in_collision(
        self, mock_sleep, terminal_cleanup, finalization, mock_secondary_client, caplog
    ):
        """A 409 create conflict with BackupCollision phase must fail closed."""
        mock_sleep.return_value = None
        mock_secondary_client.list_custom_resources_strict.return_value = StrictReadOutcome.from_items(
            [
                {
                    "metadata": {"name": "schedule", "uid": "uid-old"},
                    "spec": {"veleroSchedule": "*/15 * * * *"},
                    "status": {"phase": "Enabled"},
                }
            ]
        )
        mock_secondary_client.get_custom_resource.side_effect = [
            {
                "metadata": {"name": "schedule", "uid": "uid-old"},
                "spec": {"veleroSchedule": "*/15 * * * *"},
            },
            None,
            {
                "metadata": {"name": "schedule", "uid": "uid-new"},
                "status": {"phase": "BackupCollision"},
            },
        ]
        mock_secondary_client.create_custom_resource.side_effect = ApiException(status=409)

        with caplog.at_level(logging.WARNING, logger="acm_switchover"):
            with pytest.raises(SwitchoverError, match="remains in BackupCollision"):
                finalization._fix_backup_schedule_collision()

        assert "already exists during recreation" in caplog.text

    def test_a_rejected_mco_delete_never_writes_a_raw_response_into_the_state_file(
        self, terminal_cleanup, mock_secondary_client, mock_backup_manager, tmp_path, caplog
    ):
        """The persisted end of the leak, through the real StateManager.

        ``finalize``'s ``except Exception`` arm stringifies whatever reaches it, and
        ``str(ApiException)`` is the status line plus the response headers plus the
        response body. Keeping the failure on the execution-result channel means the
        state file records finalization's own message context instead.
        """
        from lib.utils import StateManager

        canary = "RAW-RESPONSE-CANARY"
        exc = ApiException(status=403, reason="Forbidden")
        exc.body = '{"message":"%s"}' % canary
        exc.headers = {"X-Canary": canary}

        primary = Mock()
        primary.get_custom_resource_strict.return_value = StrictReadOutcome(
            status=StrictReadStatus.ITEMS,
            resource={"metadata": {"name": "observability", "uid": "uid-1"}},
        )
        # A COHERENT source hub: the MultiClusterObservability and its namespace are
        # both present. An absent namespace beside a present CR is half-removed, which
        # the destination gate refuses as source-ambiguous BEFORE the delete -- and this
        # test needs the delete to be issued and rejected.
        primary.get_namespace_strict.return_value = StrictReadOutcome(
            status=StrictReadStatus.ITEMS,
            resource={"metadata": {"name": decommission_module.OBSERVABILITY_NAMESPACE}},
            resource_version="source-ns-1",
        )
        primary.delete_custom_resource_preconditioned.side_effect = exc

        state = StateManager(str(tmp_path / "state.json"))
        fin = Finalization(
            secondary_client=mock_secondary_client,
            state_manager=state,
            acm_version="2.14.0",
            primary_client=primary,
            primary_has_observability=True,
            old_hub_action="secondary",
        )

        with caplog.at_level(logging.ERROR):
            assert fin.finalize() is False

        state.flush_state()
        persisted = (tmp_path / "state.json").read_text(encoding="utf-8")
        # Without this the redaction assertions are vacuous: any refusal BEFORE the
        # DELETE (the destination gate reading a half-removed source, say) also keeps
        # the canary out of the state file, and the rejected delete this test is named
        # for would never have been issued at all.
        primary.delete_custom_resource_preconditioned.assert_called_once()
        assert canary not in persisted
        assert canary not in caplog.text
        assert (
            "Failed to disable observability on the old hub" in persisted
        ), "amendment section 11 item 2: finalization's own message context is what reaches the operator"

    def test_finalization_imports_no_second_gitops_marker_path(self):
        """GLM-H6: the consolidation left finalization with zero MCO teardown surface.

        A still-imported marker recorder is not merely a dead import: it is a
        ready-made second marker path that a later edit could call, which is exactly
        the duplication ``Decommission.teardown_observability`` was made to erase.
        """
        assert not hasattr(finalization_module, "safe_record_gitops_markers")

    @patch("modules.decommission.wait_for_condition")
    @pytest.mark.usefixtures("old_hub_evidence_complete")
    def test_disable_observability_on_old_hub_deletes_mco(
        self, mock_wait, mock_secondary_client, mock_state_manager, mock_backup_manager, mock_mco_primary
    ):
        """Optional MCO deletion should remove observability on old hub."""
        primary = mock_mco_primary
        fin = Finalization(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            acm_version="2.14.0",
            primary_client=primary,
            old_hub_action="secondary",
        )

        def capture_wait(_description, condition_fn, **_kwargs):
            result = condition_fn()
            assert isinstance(result, WaitConditionResult)
            assert result.done is True
            return True

        mock_wait.side_effect = capture_wait

        fin._disable_observability_on_old_hub()

        # Guarded read, the destination gate's own fresh source read, the absence
        # poll and the final verification.
        assert primary.get_custom_resource_strict.call_count == 4
        primary.get_custom_resource_strict.assert_called_with(
            group="observability.open-cluster-management.io",
            version="v1beta2",
            plural="multiclusterobservabilities",
            name="observability",
            namespace=None,
        )
        primary.delete_custom_resource_preconditioned.assert_called_once_with(
            "observability.open-cluster-management.io",
            "v1beta2",
            "multiclusterobservabilities",
            "observability",
            uid="uid-1",
            namespace=None,
            timeout_seconds=finalization_module.DELETE_REQUEST_TIMEOUT,
        )
        # The gate's source-namespace read, the drain, and the final verification --
        # all against the one fixed drain namespace.
        assert primary.get_namespace_strict.call_args_list == [
            call(decommission_module.OBSERVABILITY_NAMESPACE),
            call(decommission_module.OBSERVABILITY_NAMESPACE),
            call(decommission_module.OBSERVABILITY_NAMESPACE),
        ]
        # The destination hub is read fresh, through the secondary client only.
        mock_secondary_client.list_custom_resources_strict.assert_called_once()
        mock_secondary_client.get_namespace_strict.assert_called_once_with(decommission_module.OBSERVABILITY_NAMESPACE)
        assert mock_wait.call_count == 2
        for wait_call in mock_wait.call_args_list:
            assert wait_call.kwargs["timeout"] == decommission_module.OBSERVABILITY_TERMINATE_TIMEOUT
            assert wait_call.kwargs["interval"] == decommission_module.OBSERVABILITY_TERMINATE_INTERVAL
            assert wait_call.kwargs["allow_success_after_timeout"] is True
        primary.delete_custom_resource.assert_not_called()

    @patch("lib.gitops_detector.record_gitops_markers")
    @patch("modules.decommission.wait_for_condition")
    @pytest.mark.usefixtures("old_hub_evidence_complete")
    def test_disable_observability_on_old_hub_warns_for_gitops_managed_mco(
        self,
        mock_wait,
        mock_record_markers,
        mock_secondary_client,
        mock_state_manager,
        mock_backup_manager,
        mock_mco_primary,
        caplog,
    ):
        """MCO deletion should emit immediate warning when GitOps markers are detected."""
        mock_wait.return_value = True
        mock_record_markers.return_value = ["label:app.kubernetes.io/managed-by"]
        primary = mock_mco_primary
        fin = Finalization(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            acm_version="2.14.0",
            primary_client=primary,
            old_hub_action="secondary",
        )

        with caplog.at_level(logging.WARNING, logger="acm_switchover"):
            fin._disable_observability_on_old_hub()

        assert "appears GitOps-managed" in caplog.text
        assert "observability" in caplog.text
        mock_record_markers.assert_called_once_with(
            context="primary",
            namespace="",
            kind="MultiClusterObservability",
            name="observability",
            metadata={"name": "observability", "uid": "uid-1", "labels": {}},
        )
        primary.delete_custom_resource_preconditioned.assert_called_once()

    @patch("lib.gitops_detector.record_gitops_markers")
    @patch("modules.decommission.wait_for_condition")
    @pytest.mark.usefixtures("old_hub_evidence_complete")
    def test_disable_observability_on_old_hub_no_gitops_warning_without_markers(
        self,
        mock_wait,
        mock_record_markers,
        mock_secondary_client,
        mock_state_manager,
        mock_backup_manager,
        mock_mco_primary,
        caplog,
    ):
        """MCO deletion should not emit GitOps warning when no markers are detected."""
        mock_wait.return_value = True
        mock_record_markers.return_value = []
        primary = mock_mco_primary
        fin = Finalization(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            acm_version="2.14.0",
            primary_client=primary,
            old_hub_action="secondary",
        )

        with caplog.at_level(logging.WARNING, logger="acm_switchover"):
            fin._disable_observability_on_old_hub()

        assert "appears GitOps-managed" not in caplog.text
        primary.delete_custom_resource_preconditioned.assert_called_once()

    @patch("lib.gitops_detector.record_gitops_markers")
    @patch("modules.decommission.wait_for_condition")
    @pytest.mark.usefixtures("old_hub_evidence_complete")
    def test_disable_observability_on_old_hub_continues_when_marker_recording_fails(
        self,
        mock_wait,
        mock_record_markers,
        mock_secondary_client,
        mock_state_manager,
        mock_backup_manager,
        mock_mco_primary,
        caplog,
    ):
        """Marker recording failures must not abort optional MCO deletion flow."""
        mock_wait.return_value = True
        mock_record_markers.side_effect = RuntimeError("marker failure")
        primary = mock_mco_primary
        fin = Finalization(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            acm_version="2.14.0",
            primary_client=primary,
            old_hub_action="secondary",
        )

        with caplog.at_level(logging.WARNING, logger="acm_switchover"):
            fin._disable_observability_on_old_hub()

        assert "marker recording failed" in caplog.text.lower()
        primary.delete_custom_resource_preconditioned.assert_called_once()

    def test_finalize_failure_handling(self, terminal_cleanup, finalization, mock_backup_manager):
        """Test finalization failure handling."""
        mock_backup_manager.ensure_enabled.side_effect = Exception("Backup Error")

        result = finalization.finalize()

        assert result is False

    def test_verify_backup_schedule_enabled_failure(self, finalization, mock_secondary_client):
        """Backup schedule verification should fail when paused."""
        mock_secondary_client.list_custom_resources_strict.return_value = StrictReadOutcome.from_items(
            [{"metadata": {"name": "schedule"}, "spec": {"paused": True}}]
        )

        with pytest.raises(SwitchoverError):
            finalization._verify_backup_schedule_enabled()

    def test_verify_backup_schedule_enabled_uses_default_name_in_error(self, finalization, mock_secondary_client):
        """Fallback error text should use the canonical BackupSchedule default name."""
        mock_secondary_client.list_custom_resources_strict.return_value = StrictReadOutcome.from_items(
            [{"metadata": {}, "spec": {"paused": True}}]
        )

        with pytest.raises(SwitchoverError, match=BACKUP_SCHEDULE_DEFAULT_NAME):
            finalization._verify_backup_schedule_enabled()

    @patch("modules.finalization.time")
    def test_verify_multiclusterhub_health_failure(self, mock_time, finalization, mock_secondary_client):
        """MCH verification should fail when not running, without real-time waits."""
        # Simulate fast timeout without real sleeping:
        # start=0, then enough time has elapsed on next checks to exceed timeout.
        mock_time.time.side_effect = [0, 601, 602]
        mock_time.sleep.return_value = None

        mock_secondary_client.get_custom_resource.return_value = {
            "metadata": {"name": "multiclusterhub"},
            "status": {"phase": "Degraded"},
        }
        mock_secondary_client.get_pods.return_value = [
            {"metadata": {"name": "acm-pod"}, "status": {"phase": "Running"}}
        ]

        with pytest.raises(SwitchoverError):
            finalization._verify_multiclusterhub_health()

    @patch("modules.finalization.time")
    def test_verify_multiclusterhub_health_accepts_succeeded_pods(self, mock_time, finalization, mock_secondary_client):
        """Completed job pods should not block MultiClusterHub health verification."""
        mock_time.time.side_effect = [0]
        mock_secondary_client.get_custom_resource.return_value = {
            "metadata": {"name": "multiclusterhub"},
            "status": {"phase": "Running"},
        }
        mock_secondary_client.get_pods.return_value = [
            {"metadata": {"name": "acm-operator"}, "status": {"phase": "Running"}},
            {"metadata": {"name": "mch-hook-job"}, "status": {"phase": "Succeeded"}},
        ]

        finalization._verify_multiclusterhub_health(timeout=300)

    def test_verify_old_hub_state(self, finalization_with_primary, mock_secondary_client):
        """Old hub checks should inspect clusters and backups without mutating observability."""
        fin, primary = finalization_with_primary
        primary.list_custom_resources.side_effect = [
            [
                {
                    "metadata": {"name": "cluster1"},
                    "status": {
                        "conditions": [
                            {
                                "type": "ManagedClusterConditionAvailable",
                                "status": "False",
                            }
                        ]
                    },
                }
            ],
            [{"metadata": {"name": "schedule"}, "spec": {"paused": True}}],
        ]
        primary.get_pods.return_value = []

        fin._verify_old_hub_state()

        assert primary.list_custom_resources.call_count == 2
        primary.get_pods.assert_not_called()

    @patch("modules.decommission.OBSERVABILITY_TERMINATE_TIMEOUT", 0)
    @pytest.mark.usefixtures("old_hub_evidence_complete")
    def test_disable_observability_on_old_hub_blocks_when_pods_remain_after_mco_deletion(
        self, mock_secondary_client, mock_state_manager, mock_backup_manager, mock_mco_primary, caplog
    ):
        """MCO deletion should block when observability pods remain after the wait window."""
        primary = mock_mco_primary
        fin = Finalization(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            acm_version="2.14.0",
            primary_client=primary,
            old_hub_action="secondary",
        )
        # Replace the fixture's staged sequence: here the namespace survives every
        # read, including the destination gate's source check.
        primary.get_namespace_strict.side_effect = None
        primary.get_namespace_strict.return_value = StrictReadOutcome(
            status=StrictReadStatus.ITEMS, resource_version="ns-1"
        )
        primary.list_pods_strict.return_value = StrictReadOutcome(
            status=StrictReadStatus.ITEMS, items=[{"metadata": {"name": "obs-pod"}}], resource_version="pods-1"
        )

        with (
            caplog.at_level(logging.ERROR, logger="acm_switchover"),
            pytest.raises(SwitchoverError) as exc_info,
        ):
            fin._disable_observability_on_old_hub()

        primary.delete_custom_resource_preconditioned.assert_called_once()
        assert "Failed to disable observability on the old hub" in str(exc_info.value)
        assert "teardown reported failed" in str(exc_info.value)
        assert "pods still running" in caplog.text

    def test_disable_observability_on_old_hub_dry_run_only_reports_delete_intent(
        self, mock_secondary_client, mock_state_manager, mock_backup_manager, mock_mco_primary
    ):
        """Dry-run observability disablement should report MCO deletion intent without mutating the cluster."""
        primary = mock_mco_primary
        fin = Finalization(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            acm_version="2.14.0",
            primary_client=primary,
            old_hub_action="secondary",
            dry_run=True,
        )

        with patch("modules.decommission.logger") as logger:
            fin._disable_observability_on_old_hub()

        # The guarded read plus the destination gate's own source read: a preview
        # runs the gate for real, because a predicted blocker is a real result.
        assert primary.get_custom_resource_strict.call_count == 2
        primary.get_custom_resource_strict.assert_called_with(
            group="observability.open-cluster-management.io",
            version="v1beta2",
            plural="multiclusterobservabilities",
            name="observability",
            namespace=None,
        )
        primary.delete_custom_resource_preconditioned.assert_not_called()
        primary.delete_custom_resource.assert_not_called()
        primary.list_pods_strict.assert_not_called()
        mock_state_manager._set_config.assert_not_called()
        logger.info.assert_any_call("[DRY-RUN] Would delete %s %s", "MultiClusterObservability", "observability")

    @patch("modules.finalization.time")
    def test_finalize_skips_verify_old_hub_state_when_action_none(
        self, mock_time, terminal_cleanup, mock_secondary_client, mock_state_manager, mock_backup_manager
    ):
        """Test that _verify_old_hub_state is not called when old_hub_action is 'none'.

        This ensures the CLI contract is respected: --old-hub-action none should
        leave the old hub unchanged for manual handling.
        """
        # Mock time to avoid loops and sleep delays
        mock_time.time.side_effect = [0, 1, 2, 3]
        mock_time.sleep.return_value = None
        backup_ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        primary = Mock()
        fin = Finalization(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            acm_version="2.12.0",
            primary_client=primary,
            primary_has_observability=True,
            old_hub_action="none",
        )

        # Mock all required responses with side_effect for sequential calls
        _serve_schedules(mock_secondary_client)
        _serve_backups(
            mock_secondary_client,
            [
                [],  # Initial backups
                [
                    {
                        "metadata": {"name": "backup-1", "labels": ACM_BACKUP_LABEL},
                        "status": {"phase": "InProgress"},
                    }
                ],  # Loop iteration 1 - new ACM backup
            ],
        )
        mock_secondary_client.get_custom_resource.side_effect = [
            {"metadata": {"name": "schedule", "uid": "uid-1"}, "spec": {}},  # before deletion
            None,  # after deletion
            {
                "metadata": {
                    "name": "backup-1",
                    "creationTimestamp": backup_ts,
                    "labels": ACM_BACKUP_LABEL,
                },
                "status": {"phase": "Completed", "completionTimestamp": backup_ts},
            },  # verify_backup_integrity re-reads the recorded backup by name
            {"metadata": {"name": "multiclusterhub"}, "status": {"phase": "Running"}},  # MCH health
        ]
        mock_secondary_client.get_pods.return_value = []

        # Ensure we track if _verify_old_hub_state was called
        with patch.object(fin, "_verify_old_hub_state") as mock_verify:
            result = fin.finalize()

            assert result is True
            # _verify_old_hub_state should NOT be called when old_hub_action is 'none'
            mock_verify.assert_not_called()
            # Primary client should not have scaling methods called
            primary.scale_statefulset.assert_not_called()
            primary.scale_deployment.assert_not_called()

    @patch("time.sleep")
    @patch("time.time")
    def test_finalize_skips_old_hub_observability_delete_when_preflight_skipped_observability(
        self,
        mock_time_time,
        mock_time_sleep,
        terminal_cleanup,
        mock_secondary_client,
        mock_state_manager,
        mock_backup_manager,
    ):
        """Skipped observability preflight must prevent destructive old-hub MCO deletion."""
        # Mock time to avoid real waits
        mock_time_time.return_value = 0
        mock_time_sleep.return_value = None
        backup_ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        primary = Mock()
        primary.list_custom_resources.return_value = []
        primary.get_pods.return_value = []
        # _setup_old_hub_as_secondary checks for an existing restore on the
        # old hub via primary.get_custom_resource.  Return None so it skips
        # the delete-wait cycle (which would hang because time.time is mocked
        # to always return 0, preventing the timeout from ever firing).
        primary.get_custom_resource.return_value = None

        fin = Finalization(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            acm_version="2.12.0",
            primary_client=primary,
            primary_has_observability=False,
            old_hub_action="secondary",
        )

        # Mock all required responses with side_effect for sequential calls
        # Order (the strict BackupSchedule reads, including _get_backup_verify_timeout's, are
        # served separately): verify_new_backups (2x). verify_backup_integrity re-reads the
        # recorded backup by name via get_custom_resource, so it consumes no list response.
        _serve_schedules(
            mock_secondary_client,
            [
                [{"metadata": {"name": "schedule"}, "spec": {"paused": False}}],
                [{"metadata": {"name": "schedule"}, "spec": {}, "status": {"phase": "Enabled"}}],
                [{"metadata": {"name": "schedule"}, "spec": {"veleroSchedule": "*/15 * * * *"}}],
            ],
        )
        _serve_backups(
            mock_secondary_client,
            [
                [],  # Initial backups
                [
                    {
                        "metadata": {"name": "backup-1", "labels": ACM_BACKUP_LABEL},
                        "status": {"phase": "InProgress"},
                    }
                ],  # New ACM backup detected
            ],
        )
        mock_secondary_client.get_custom_resource.side_effect = [
            {"metadata": {"name": "schedule", "uid": "uid-1"}, "spec": {}},  # before deletion
            None,  # after deletion
            {
                "metadata": {
                    "name": "backup-1",
                    "creationTimestamp": backup_ts,
                    "labels": ACM_BACKUP_LABEL,
                },
                "status": {"phase": "Completed", "completionTimestamp": backup_ts},
            },  # verify_backup_integrity re-reads the recorded backup by name
            {"metadata": {"name": "multiclusterhub"}, "status": {"phase": "Running"}},  # MCH health
        ]
        mock_secondary_client.get_pods.return_value = []

        with patch.object(fin, "_disable_observability_on_old_hub") as mock_disable:
            with patch.object(fin, "_verify_old_hub_state") as mock_verify:
                result = fin.finalize()

                assert result is True
                mock_disable.assert_not_called()
                mock_verify.assert_called_once()

    def test_finalize_deletes_old_hub_observability_when_deprecated_flag_is_false(
        self, terminal_cleanup, mock_secondary_client, mock_state_manager, mock_backup_manager
    ):
        """Detected observability should trigger old-hub MCO deletion without the deprecated flag."""
        primary = Mock()
        fin = Finalization(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            acm_version="2.14.0",
            primary_client=primary,
            primary_has_observability=True,
            old_hub_action="secondary",
            disable_observability_on_secondary=False,
        )

        with (
            patch.object(fin, "_disable_observability_on_old_hub") as mock_disable,
            patch.object(fin, "_enable_backup_schedule"),
            patch.object(fin, "_verify_backup_schedule_enabled"),
            patch.object(fin, "_fix_backup_schedule_collision"),
            patch.object(fin, "_get_backup_verify_timeout", return_value=1),
            patch.object(fin, "_verify_new_backups"),
            patch.object(fin, "_verify_backup_integrity"),
            patch.object(fin, "_verify_multiclusterhub_health"),
            patch.object(fin, "_run_reset_auto_import_strategy_step"),
            patch.object(fin, "_setup_old_hub_as_secondary") as mock_setup_old_hub,
            patch.object(fin, "_verify_old_hub_state"),
        ):
            result = fin.finalize()

        assert result is True
        mock_disable.assert_called_once_with()
        mock_setup_old_hub.assert_called_once_with()
        assert "disable_observability_on_secondary" not in vars(fin)

    def test_handle_old_hub_secondary_skips_safely_when_primary_client_is_absent(
        self, mock_secondary_client, mock_state_manager, mock_backup_manager
    ):
        """Secondary old-hub handling must be a safe no-op when restore-only mode has no primary client."""
        fin = Finalization(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            acm_version="2.12.0",
            primary_client=None,
            old_hub_action="secondary",
        )

        with (
            patch.object(fin, "_setup_old_hub_as_secondary") as setup_old_hub,
            patch.object(fin, "_decommission_old_hub") as decommission_old_hub,
        ):
            fin._handle_old_hub()

        setup_old_hub.assert_not_called()
        decommission_old_hub.assert_not_called()

    def test_handle_old_hub_raises_on_unknown_old_hub_action(
        self, mock_secondary_client, mock_state_manager, mock_backup_manager
    ):
        """Unknown old_hub_action values must fail closed."""
        primary = Mock()
        fin = Finalization(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            acm_version="2.12.0",
            primary_client=primary,
            old_hub_action="bogus",
        )

        with (
            patch.object(fin, "_setup_old_hub_as_secondary") as setup_old_hub,
            patch.object(fin, "_decommission_old_hub") as decommission_old_hub,
        ):
            with pytest.raises(SwitchoverError, match="Unknown old_hub_action 'bogus'"):
                fin._handle_old_hub()

        setup_old_hub.assert_not_called()
        decommission_old_hub.assert_not_called()

    def test_setup_old_hub_as_secondary_failure_propagates(
        self, mock_secondary_client, mock_state_manager, mock_backup_manager
    ):
        """Failed passive restore creation on old hub must raise SwitchoverError with context."""

        primary = Mock()
        primary.get_custom_resource.return_value = None
        primary.create_custom_resource.side_effect = ApiException(status=500, reason="Internal Server Error")

        fin = Finalization(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            acm_version="2.12.0",
            primary_client=primary,
            old_hub_action="secondary",
        )

        with pytest.raises(
            SwitchoverError,
            match="Failed to create passive sync restore on old primary hub",
        ):
            fin._setup_old_hub_as_secondary()

    def test_setup_old_hub_as_secondary_resets_stale_active_restore(
        self, mock_secondary_client, mock_state_manager, mock_backup_manager
    ):
        """Existing restore with veleroManagedClustersBackupName=latest must be deleted, waited on, and recreated."""
        primary = Mock()
        primary.dry_run = False
        # First call: return stale restore for initial check.
        # Subsequent calls (during wait polling): return None to indicate deletion completed.
        primary.get_custom_resource.side_effect = [
            {"spec": {"veleroManagedClustersBackupName": "latest"}},
            None,
        ]

        fin = Finalization(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            acm_version="2.12.0",
            primary_client=primary,
            old_hub_action="secondary",
        )
        fin._setup_old_hub_as_secondary()

        primary.delete_custom_resource.assert_called_once()
        # Verify wait polling happened (at least 2 get_custom_resource calls:
        # initial check + at least one deletion poll)
        assert primary.get_custom_resource.call_count >= 2
        primary.create_custom_resource.assert_called_once()
        created_spec = primary.create_custom_resource.call_args.kwargs["body"]["spec"]
        assert created_spec["veleroManagedClustersBackupName"] == "skip"

    def test_setup_old_hub_as_secondary_recreates_existing_passive_restore_even_when_spec_is_skip(
        self, mock_secondary_client, mock_state_manager, mock_backup_manager
    ):
        """Existing passive-sync restore must be recreated so stale controller state cannot be reused."""
        primary = Mock()
        primary.dry_run = False
        primary.get_custom_resource.side_effect = [
            {"spec": {"veleroManagedClustersBackupName": "skip", "syncRestoreWithNewBackups": True}},
            None,
        ]

        fin = Finalization(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            acm_version="2.12.0",
            primary_client=primary,
            old_hub_action="secondary",
        )
        fin._setup_old_hub_as_secondary()

        primary.delete_custom_resource.assert_called_once()
        assert primary.get_custom_resource.call_count >= 2
        primary.create_custom_resource.assert_called_once()
        created_spec = primary.create_custom_resource.call_args.kwargs["body"]["spec"]
        assert created_spec["veleroManagedClustersBackupName"] == "skip"

    def test_setup_old_hub_as_secondary_raises_on_delete_failure(
        self, mock_secondary_client, mock_state_manager, mock_backup_manager
    ):
        """Delete failure for stale active restore must raise SwitchoverError."""
        primary = Mock()
        primary.get_custom_resource.return_value = {"spec": {"veleroManagedClustersBackupName": "latest"}}
        primary.delete_custom_resource.side_effect = ApiException(status=500, reason="Internal Server Error")

        fin = Finalization(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            acm_version="2.12.0",
            primary_client=primary,
            old_hub_action="secondary",
        )

        with pytest.raises(
            SwitchoverError,
            match="Failed to delete stale passive sync restore on old primary hub",
        ):
            fin._setup_old_hub_as_secondary()

    def test_setup_old_hub_as_secondary_raises_on_deletion_timeout(
        self, mock_secondary_client, mock_state_manager, mock_backup_manager
    ):
        """Timeout waiting for restore deletion must raise FatalError without attempting create."""
        from lib.exceptions import FatalError

        primary = Mock()
        primary.dry_run = False
        # First call returns stale restore; all subsequent calls also return it (never deleted)
        primary.get_custom_resource.return_value = {"spec": {"veleroManagedClustersBackupName": "latest"}}

        fin = Finalization(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            acm_version="2.12.0",
            primary_client=primary,
            old_hub_action="secondary",
        )

        with patch("lib.waiter.wait_for_condition", return_value=False):
            with pytest.raises(FatalError, match="Timeout waiting for restore"):
                fin._setup_old_hub_as_secondary()

        primary.delete_custom_resource.assert_called_once()
        primary.create_custom_resource.assert_not_called()

    def test_cleanup_restore_resources_archives_before_deletion(
        self, finalization, mock_secondary_client, mock_state_manager
    ):
        """Test that restore resources are archived before deletion."""
        # Mock a restore resource with full details
        mock_restore = {
            "metadata": {
                "name": "restore-acm-passive-sync",
                "namespace": "open-cluster-management-backup",
                "creationTimestamp": "2025-11-28T10:00:00Z",
            },
            "spec": {
                "veleroManagedClustersBackupName": "latest",
                "veleroCredentialsBackupName": "latest",
                "veleroResourcesBackupName": "latest",
                "syncRestoreWithNewBackups": True,
                "cleanupBeforeRestore": "CleanupRestored",
            },
            "status": {
                "phase": "Finished",
                "lastMessage": "Restore completed successfully",
                "veleroManagedClustersRestoreName": "acm-managed-clusters-12345",
                "veleroCredentialsRestoreName": "acm-credentials-12345",
                "veleroResourcesRestoreName": "acm-resources-12345",
            },
        }

        # The strict Restore list returns the restore
        mock_secondary_client.list_custom_resources_strict.return_value = StrictReadOutcome.from_items([mock_restore])

        finalization._cleanup_restore_resources(CLEANUP_DONE)

        mock_secondary_client.list_custom_resources_strict.assert_called_once_with(
            group="cluster.open-cluster-management.io",
            version="v1beta1",
            plural="restores",
            namespace=finalization_module.BACKUP_NAMESPACE,
        )

        # Verify archive was saved to state
        mock_state_manager._set_config.assert_called_once()
        call_args = mock_state_manager._set_config.call_args
        assert call_args[0][0] == "archived_restores"

        archived = call_args[0][1]
        assert len(archived) == 1
        assert archived[0]["name"] == "restore-acm-passive-sync"
        assert archived[0]["phase"] == "Finished"
        assert archived[0]["velero_backups"]["veleroManagedClustersBackupName"] == "latest"
        assert archived[0]["archived_at"] is not None

        # Verify delete was called
        mock_secondary_client.delete_custom_resource.assert_called_once_with(
            group="cluster.open-cluster-management.io",
            version="v1beta1",
            plural="restores",
            name="restore-acm-passive-sync",
            namespace=finalization_module.BACKUP_NAMESPACE,
            timeout_seconds=finalization_module.DELETE_REQUEST_TIMEOUT,
        )

    @pytest.mark.parametrize(
        ("delete_error", "expect_warning"),
        [
            (ApiException(status=404, reason="Not Found"), False),
            (ApiException(status=500, reason="Internal Server Error"), True),
            (RuntimeError("boom"), True),
        ],
    )
    def test_cleanup_restore_resources_handles_delete_outcomes(
        self,
        finalization,
        mock_secondary_client,
        mock_state_manager,
        delete_error,
        expect_warning,
        caplog,
    ):
        """Cleanup should ignore 404s but fail closed for other delete errors."""
        mock_restore = {
            "metadata": {"name": "restore-acm-passive-sync"},
            "spec": {},
            "status": {"phase": "Finished"},
        }
        mock_secondary_client.list_custom_resources_strict.return_value = StrictReadOutcome.from_items([mock_restore])
        mock_secondary_client.delete_custom_resource.side_effect = delete_error

        with caplog.at_level(logging.WARNING):
            if expect_warning:
                with pytest.raises(SwitchoverError, match="Failed to delete restore resource"):
                    finalization._cleanup_restore_resources(CLEANUP_DONE)
            else:
                finalization._cleanup_restore_resources(CLEANUP_DONE)

        mock_state_manager._set_config.assert_called_once()
        if expect_warning:
            assert "Error deleting restore restore-acm-passive-sync" in caplog.text
        else:
            assert "Error deleting restore restore-acm-passive-sync" not in caplog.text

    def test_cleanup_restore_resources_rejects_unexpected_restores(
        self, finalization, mock_secondary_client, mock_state_manager
    ):
        """Cleanup must fail before deleting unrelated Restore resources."""
        mock_secondary_client.list_custom_resources_strict.return_value = StrictReadOutcome.from_items(
            [
                {
                    "metadata": {"name": "restore-acm-full"},
                    "spec": {},
                    "status": {"phase": "Finished"},
                },
                {
                    "metadata": {"name": "manual-restore"},
                    "spec": {},
                    "status": {"phase": "Finished"},
                },
            ]
        )

        with pytest.raises(SwitchoverError, match="manual-restore"):
            finalization._cleanup_restore_resources(CLEANUP_DONE)

        mock_secondary_client.delete_custom_resource.assert_not_called()
        mock_state_manager._set_config.assert_not_called()

    def test_archive_restore_details_extracts_all_fields(self, finalization):
        """Test that _archive_restore_details extracts all important fields."""
        restore = {
            "metadata": {
                "name": "test-restore",
                "namespace": "test-ns",
                "uid": "abc-123-def-456",
                "resourceVersion": "12345",
                "generation": 2,
                "creationTimestamp": "2025-11-28T12:00:00Z",
                "labels": {"app": "acm-backup"},
                "annotations": {"note": "switchover test"},
                "ownerReferences": [{"name": "backup-operator", "kind": "Deployment"}],
            },
            "spec": {
                "veleroManagedClustersBackupName": "backup-mc",
                "veleroCredentialsBackupName": "backup-creds",
                "veleroResourcesBackupName": "backup-res",
                "syncRestoreWithNewBackups": False,
                "restoreSyncInterval": "10m",
                "cleanupBeforeRestore": "None",
            },
            "status": {
                "phase": "Enabled",
                "lastMessage": "Sync in progress",
                "veleroManagedClustersRestoreName": "restore-mc-123",
                "veleroCredentialsRestoreName": "restore-creds-123",
                "veleroResourcesRestoreName": "restore-res-123",
            },
        }

        result = finalization._archive_restore_details(restore)

        # Metadata fields
        assert result["name"] == "test-restore"
        assert result["namespace"] == "test-ns"
        assert result["uid"] == "abc-123-def-456"
        assert result["resource_version"] == "12345"
        assert result["generation"] == 2
        assert result["creation_timestamp"] == "2025-11-28T12:00:00Z"
        assert result["labels"] == {"app": "acm-backup"}
        assert result["annotations"] == {"note": "switchover test"}
        assert result["owner_references"] == [{"name": "backup-operator", "kind": "Deployment"}]
        assert result["archived_at"] is not None
        # Spec fields
        assert result["velero_backups"]["veleroManagedClustersBackupName"] == "backup-mc"
        assert result["restore_sync_interval"] == "10m"
        # Status fields
        assert result["phase"] == "Enabled"
        assert result["last_message"] == "Sync in progress"
        assert result["velero_managed_clusters_restore_name"] == "restore-mc-123"


@pytest.mark.integration
class TestFinalizationBackupOwnershipFallbackIntegration:
    """Integration-style checks for ACM backup ownership fallback with real state persistence."""

    @patch("modules.finalization.time")
    def test_verify_new_backups_persists_fallback_detected_backup(self, mock_time, mock_secondary_client, tmp_path):
        """A label-missing ACM-style backup should still be persisted via the fallback signal."""
        from lib.utils import StateManager

        mock_time.time.side_effect = [0, 0, 1, 2]
        _serve_backups(
            mock_secondary_client,
            [
                [],
                [],
                [
                    {
                        "metadata": {"name": "acm-managed-clusters-schedule-20260306100000"},
                        "status": {"phase": "Completed"},
                    }
                ],
            ],
        )

        state = StateManager(str(tmp_path / "state.json"))
        fin = Finalization(
            secondary_client=mock_secondary_client,
            state_manager=state,
            acm_version="2.12.0",
        )

        fin._verify_new_backups(timeout=10)

        assert RunRecord(state).new_backup() == "acm-managed-clusters-schedule-20260306100000"


@pytest.mark.unit
class TestIntegratedDestinationGateWiring:
    """C5: both integrated ``Decommission`` constructions carry the destination hub.

    Without the destination client the shared teardown silently degrades to the
    standalone path, which never runs the July section 4 gate -- the exact failure
    the gate exists to prevent.
    """

    def _finalization(self, secondary, state, *, old_hub_action, acknowledge):
        return Finalization(
            secondary_client=secondary,
            state_manager=state,
            acm_version="2.12.0",
            primary_client=Mock(),
            primary_has_observability=True,
            old_hub_action=old_hub_action,
            acknowledge_observability_not_migrated=acknowledge,
        )

    def test_decommission_old_hub_passes_the_destination_client_and_the_acknowledgement(
        self, mock_secondary_client, mock_state_manager, mock_backup_manager
    ):
        fin = self._finalization(
            mock_secondary_client, mock_state_manager, old_hub_action="decommission", acknowledge=True
        )

        with patch("modules.finalization.Decommission") as decommission_class:
            decommission_class.return_value.decommission.return_value = DecommissionResult(
                substeps={"observability": SubstepOutcome.COMPLETED},
                not_attempted=(),
            )
            fin._decommission_old_hub()

        kwargs = decommission_class.call_args.kwargs
        assert kwargs["secondary_client"] is mock_secondary_client
        assert kwargs["acknowledge_observability_not_migrated"] is True

    @pytest.mark.usefixtures("old_hub_evidence_complete")
    def test_disable_observability_on_old_hub_passes_the_destination_client_and_the_acknowledgement(
        self, mock_secondary_client, mock_state_manager, mock_backup_manager
    ):
        fin = self._finalization(
            mock_secondary_client, mock_state_manager, old_hub_action="secondary", acknowledge=False
        )

        with patch("modules.finalization.Decommission") as decommission_class:
            decommission_class.return_value.teardown_observability.return_value = SubstepExecution(
                SubstepOutcome.COMPLETED, changed=True
            )
            fin._disable_observability_on_old_hub()

        kwargs = decommission_class.call_args.kwargs
        assert kwargs["secondary_client"] is mock_secondary_client
        assert kwargs["acknowledge_observability_not_migrated"] is False

    def test_the_acknowledgement_defaults_off(self, mock_secondary_client, mock_state_manager, mock_backup_manager):
        """A destructive acknowledgement that defaulted on would gate nothing."""
        fin = Finalization(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            acm_version="2.12.0",
            primary_client=Mock(),
            old_hub_action="decommission",
        )
        assert fin.acknowledge_observability_not_migrated is False

    @pytest.mark.usefixtures("old_hub_evidence_complete")
    def test_a_blocked_destination_gate_fails_the_secondary_action_teardown(
        self, mock_secondary_client, mock_state_manager, mock_backup_manager, mock_mco_primary
    ):
        """End to end through the real shared teardown: no destination observability
        means finalization refuses, with its own message context."""
        mock_secondary_client.list_custom_resources_strict.return_value = StrictReadOutcome.crd_absent(
            "kind_not_served"
        )
        mock_secondary_client.get_namespace_strict.return_value = StrictReadOutcome.namespace_absent(
            "namespace_not_found"
        )
        fin = Finalization(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            acm_version="2.14.0",
            primary_client=mock_mco_primary,
            old_hub_action="secondary",
        )

        with pytest.raises(SwitchoverError, match="Failed to disable observability on the old hub"):
            fin._disable_observability_on_old_hub()

        mock_mco_primary.delete_custom_resource_preconditioned.assert_not_called()


# -- R4-04 Task 10: the durable Restore-cleanup transaction (July design section 4a) --

CLEANUP_POST = _journal_vector("journal-post-activation-completed")
CLEANUP_REVALIDATED = _journal_vector("journal-teardown-revalidated")
CLEANUP_INTENT = _journal_vector("journal-cleanup-intent-persisted")
CLEANUP_ACCEPTED = _journal_vector("journal-cleanup-delete-accepted")
CLEANUP_RECOVERY = _journal_vector("journal-cleanup-recovery-required")
CLEANUP_REPAIRED = _journal_vector("journal-cleanup-repaired")
RESTORE_UID = CLEANUP_INTENT["restore"]["uid"]
REPAIR_REQUEST = {
    key: value for key, value in CLEANUP_REPAIRED["cleanup"]["repair"].items() if key != "acknowledged_at"
}
ABSENT = StrictReadOutcome.object_absent("object_not_found")


def _live(uid=RESTORE_UID, rv="2002", generation=4, **spec):
    restore = CLEANUP_INTENT["restore"]
    body = {
        "apiVersion": "cluster.open-cluster-management.io/v1beta1",
        "kind": "Restore",
        "metadata": {
            "name": restore["name"],
            "namespace": restore["namespace"],
            "uid": uid,
            "resourceVersion": rv,
            "generation": generation,
        },
        "spec": {**restore["backup_fields"], "cleanupBeforeRestore": "CleanupRestored", **spec},
    }
    return StrictReadOutcome.from_resource(body, resource_version=rv)


def _accepted(rv="2002"):
    return {"accepted": True, "reason": "ok", "uid": RESTORE_UID, "resource_version": rv}


CONFLICT = {"accepted": False, "conflict": True, "reason": "precondition_failed"}


def _cleanup_state(tmp_path, journal):
    from lib.utils import StateManager

    state = StateManager(str(tmp_path / "state.json"))
    if journal is not None:
        state.state["config"]["migration_backups"] = copy.deepcopy(journal)
    state.flush_state()
    return state


def _stored(tmp_path):
    return json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))["config"].get("migration_backups")


def _cleanup_fin(state, secondary, **kwargs):
    with patch("modules.finalization.BackupScheduleManager"):
        return Finalization(secondary_client=secondary, state_manager=state, acm_version="2.14.0", **kwargs)


@pytest.fixture
def cleanup_secondary():
    secondary = Mock(name="secondary")
    secondary.dry_run = False
    # The live destination lane: the journals below were frozen on ACM 2.14.
    secondary.list_custom_resources_strict.return_value = StrictReadOutcome.from_items(
        [{"metadata": {"name": "multiclusterhub"}, "status": {"currentVersion": "2.14.3"}}]
    )
    return secondary


@pytest.fixture
def barrier():
    with patch("modules.finalization.SecondaryActivation.revalidate_completion_evidence") as proof:
        yield proof


@pytest.fixture(autouse=False)
def no_sleep():
    with patch("modules.finalization.time.sleep") as sleep:
        yield sleep


@pytest.mark.unit
class TestRestoreCleanupTransaction:
    """Items 1-14 and the repair contract of plan Task 10, Python form factor."""

    def test_no_journal_blocks_before_any_read_or_delete(self, tmp_path, cleanup_secondary, barrier):
        fin = _cleanup_fin(_cleanup_state(tmp_path, None), cleanup_secondary)

        with pytest.raises(FatalError, match="journal_required"):
            fin._run_restore_cleanup_transaction()

        barrier.assert_not_called()
        cleanup_secondary.get_custom_resource_strict.assert_not_called()
        cleanup_secondary.delete_restore_guarded.assert_not_called()

    def test_invalid_journal_blocks_finalization_before_the_old_hub_and_backups(
        self, tmp_path, cleanup_secondary, barrier
    ):
        state = _cleanup_state(tmp_path, dict(CLEANUP_INTENT, waiver=False))
        fin = _cleanup_fin(state, cleanup_secondary, primary_client=Mock(), primary_has_observability=True)

        with (
            patch.object(fin, "_disable_observability_on_old_hub") as mco_teardown,
            patch.object(fin, "_enable_backup_schedule") as enable,
        ):
            assert fin.finalize() is False

        mco_teardown.assert_not_called()
        enable.assert_not_called()
        cleanup_secondary.delete_restore_guarded.assert_not_called()

    def test_fresh_run_revalidates_records_intent_then_deletes_and_completes(
        self, tmp_path, cleanup_secondary, barrier, no_sleep
    ):
        state = _cleanup_state(tmp_path, CLEANUP_POST)
        seen = []

        def _delete(name, **kwargs):
            seen.append(_stored(tmp_path)["cleanup"]["state"])
            return _accepted()

        cleanup_secondary.get_custom_resource_strict.side_effect = [_live(), _live(), ABSENT, ABSENT]
        cleanup_secondary.delete_restore_guarded.side_effect = _delete
        fin = _cleanup_fin(state, cleanup_secondary)

        journal = fin._run_restore_cleanup_transaction()

        barrier.assert_called_once()
        assert barrier.call_args.args[0] == CLEANUP_POST
        # Intent was durable before the DELETE was sent.
        assert seen == ["intent_persisted"]
        cleanup_secondary.delete_restore_guarded.assert_called_once_with(
            "acm-restore",
            namespace="open-cluster-management-backup",
            uid=RESTORE_UID,
            resource_version="2002",
            timeout_seconds=30,
        )
        cleanup_secondary.delete_custom_resource.assert_not_called()
        stored = _stored(tmp_path)
        assert stored == journal
        cleanup = stored["cleanup"]
        assert cleanup["state"] == "completed"
        assert stored["restore"]["teardown_revalidated_at"] is not None
        assert cleanup["final_get_resource_version"] == "2002"
        assert cleanup["absence_verified_at"] == cleanup["completed_at"]
        assert {key: cleanup[key] for key in ("uid", "generation", "spec_fingerprint", "backup_fields")} == {
            key: stored["restore"][key] for key in ("uid", "generation", "spec_fingerprint", "backup_fields")
        }

    def test_a_failed_barrier_records_nothing_and_sends_no_delete(self, tmp_path, cleanup_secondary, barrier):
        barrier.side_effect = FatalError("Journaled credentials child was replaced or changed")
        fin = _cleanup_fin(_cleanup_state(tmp_path, CLEANUP_POST), cleanup_secondary)

        with pytest.raises(FatalError, match="replaced or changed"):
            fin._run_restore_cleanup_transaction()

        assert _stored(tmp_path) == CLEANUP_POST
        cleanup_secondary.delete_restore_guarded.assert_not_called()

    def test_an_existing_revalidation_timestamp_is_never_fresh_success(self, tmp_path, cleanup_secondary, barrier):
        barrier.side_effect = FatalError("generation drift")
        fin = _cleanup_fin(_cleanup_state(tmp_path, CLEANUP_REVALIDATED), cleanup_secondary)

        with pytest.raises(FatalError, match="generation drift"):
            fin._run_restore_cleanup_transaction()

        barrier.assert_called_once()
        assert _stored(tmp_path) == CLEANUP_REVALIDATED
        cleanup_secondary.delete_restore_guarded.assert_not_called()

    def test_the_live_barrier_fails_closed_on_an_absent_restore(self, tmp_path, cleanup_secondary):
        """The real completion proof, not a stub: an absent not_started Restore never recovers."""
        cleanup_secondary.get_custom_resource_strict.return_value = ABSENT
        fin = _cleanup_fin(_cleanup_state(tmp_path, CLEANUP_POST), cleanup_secondary)

        with pytest.raises(FatalError, match="recorded by the migration journal is absent"):
            fin._run_restore_cleanup_transaction()

        assert _stored(tmp_path) == CLEANUP_POST
        cleanup_secondary.delete_restore_guarded.assert_not_called()

    def test_a_failed_intent_write_sends_no_delete(self, tmp_path, cleanup_secondary, barrier):
        fin = _cleanup_fin(_cleanup_state(tmp_path, CLEANUP_REVALIDATED), cleanup_secondary)

        with patch("lib.run_record.RunRecord.record_migration_backups", side_effect=OSError("disk full")):
            with pytest.raises(FatalError, match="could not be recorded durably"):
                fin._run_restore_cleanup_transaction()

        cleanup_secondary.get_custom_resource_strict.assert_not_called()
        cleanup_secondary.delete_restore_guarded.assert_not_called()

    @pytest.mark.parametrize(
        "live",
        [
            _live(generation=5),
            _live(veleroResourcesBackupName="acm-resources-schedule-other"),
            _live(veleroCredentialsBackupName="latest"),
            _live(cleanupBeforeRestore="None"),
        ],
        ids=["generation", "backup-field", "latest-alias", "cleanup-policy"],
    )
    def test_final_get_drift_blocks_with_no_delete_and_no_state_change(self, tmp_path, cleanup_secondary, live):
        cleanup_secondary.get_custom_resource_strict.return_value = live
        fin = _cleanup_fin(_cleanup_state(tmp_path, CLEANUP_INTENT), cleanup_secondary)

        with pytest.raises(FatalError, match="restore_spec_drift"):
            fin._run_restore_cleanup_transaction()

        cleanup_secondary.delete_restore_guarded.assert_not_called()
        assert _stored(tmp_path) == CLEANUP_INTENT

    def test_a_status_only_conflict_retries_the_full_validation(self, tmp_path, cleanup_secondary, no_sleep):
        cleanup_secondary.get_custom_resource_strict.side_effect = [_live(), _live(rv="2009"), ABSENT, ABSENT]
        cleanup_secondary.delete_restore_guarded.side_effect = [CONFLICT, _accepted("2009")]
        fin = _cleanup_fin(_cleanup_state(tmp_path, CLEANUP_INTENT), cleanup_secondary)

        fin._run_restore_cleanup_transaction()

        revisions = [
            call.kwargs["resource_version"] for call in cleanup_secondary.delete_restore_guarded.call_args_list
        ]
        assert revisions == ["2002", "2009"]
        stored = _stored(tmp_path)["cleanup"]
        assert (stored["state"], stored["final_get_resource_version"]) == ("completed", "2009")

    def test_conflict_retry_exhaustion_blocks_and_stays_resumable(self, tmp_path, cleanup_secondary):
        cleanup_secondary.get_custom_resource_strict.return_value = _live()
        cleanup_secondary.delete_restore_guarded.return_value = CONFLICT
        fin = _cleanup_fin(_cleanup_state(tmp_path, CLEANUP_INTENT), cleanup_secondary)

        with pytest.raises(FatalError, match="cleanup_conflict_retries_exhausted"):
            fin._run_restore_cleanup_transaction()

        assert cleanup_secondary.delete_restore_guarded.call_count == 4
        assert cleanup_secondary.get_custom_resource_strict.call_count == 4
        cleanup_secondary.delete_custom_resource.assert_not_called()
        assert _stored(tmp_path) == CLEANUP_INTENT

    def test_an_unverifiable_delete_is_not_accepted(self, tmp_path, cleanup_secondary):
        cleanup_secondary.get_custom_resource_strict.return_value = _live()
        cleanup_secondary.delete_restore_guarded.return_value = {"accepted": False, "reason": "unverifiable"}
        fin = _cleanup_fin(_cleanup_state(tmp_path, CLEANUP_INTENT), cleanup_secondary)

        with pytest.raises(FatalError, match="delete_not_accepted"):
            fin._run_restore_cleanup_transaction()

        assert _stored(tmp_path) == CLEANUP_INTENT

    def test_polling_follows_the_exact_uid_until_the_final_read_proves_absence(
        self, tmp_path, cleanup_secondary, no_sleep
    ):
        cleanup_secondary.get_custom_resource_strict.side_effect = [_live(), _live(), _live(), ABSENT, ABSENT]
        cleanup_secondary.delete_restore_guarded.return_value = _accepted()
        fin = _cleanup_fin(_cleanup_state(tmp_path, CLEANUP_INTENT), cleanup_secondary)

        fin._run_restore_cleanup_transaction()

        assert cleanup_secondary.get_custom_resource_strict.call_count == 5
        # The first poll reads at once; each later poll waits the shared interval.
        assert [call.args[0] for call in no_sleep.call_args_list] == [5, 5]
        assert _stored(tmp_path)["cleanup"]["state"] == "completed"

    def test_a_replacement_at_the_final_read_recovers_and_never_completes(self, tmp_path, cleanup_secondary, no_sleep):
        cleanup_secondary.get_custom_resource_strict.side_effect = [_live(), ABSENT, _live(uid="uid-new", rv="3003")]
        cleanup_secondary.delete_restore_guarded.return_value = _accepted()
        fin = _cleanup_fin(_cleanup_state(tmp_path, CLEANUP_INTENT), cleanup_secondary)

        with pytest.raises(FatalError, match="replacement_during_poll, observed UID uid-new"):
            fin._run_restore_cleanup_transaction()

        cleanup = _stored(tmp_path)["cleanup"]
        assert cleanup["state"] == "recovery_required"
        assert cleanup["recovery"]["observed_resource_version"] == "3003"
        assert cleanup["delete_accepted_at"] is not None and cleanup["completed_at"] is None
        assert cleanup_secondary.delete_restore_guarded.call_count == 1

    def test_a_replacement_during_polling_recovers(self, tmp_path, cleanup_secondary, no_sleep):
        cleanup_secondary.get_custom_resource_strict.side_effect = [_live(), _live(uid="uid-new", rv="3003")]
        cleanup_secondary.delete_restore_guarded.return_value = _accepted()
        fin = _cleanup_fin(_cleanup_state(tmp_path, CLEANUP_INTENT), cleanup_secondary)

        with pytest.raises(FatalError, match="replacement_during_poll"):
            fin._run_restore_cleanup_transaction()

        assert _stored(tmp_path)["cleanup"]["recovery"]["reason_code"] == "replacement_during_poll"

    def test_a_replacement_before_the_delete_is_kept_and_recovers(self, tmp_path, cleanup_secondary):
        cleanup_secondary.get_custom_resource_strict.return_value = _live(uid="uid-new", rv="3003")
        fin = _cleanup_fin(_cleanup_state(tmp_path, CLEANUP_INTENT), cleanup_secondary)

        with pytest.raises(FatalError, match="replacement_uid") as excinfo:
            fin._run_restore_cleanup_transaction()

        cleanup_secondary.delete_restore_guarded.assert_not_called()
        cleanup_secondary.delete_custom_resource.assert_not_called()
        cleanup = _stored(tmp_path)["cleanup"]
        assert (cleanup["state"], cleanup["recovery"]["observed_uid"]) == ("recovery_required", "uid-new")
        assert CLEANUP_INTENT["run_id"] in str(excinfo.value)
        assert CLEANUP_INTENT["cleanup"]["operation_id"] in str(excinfo.value)

    @pytest.mark.parametrize("journal", [CLEANUP_INTENT, CLEANUP_ACCEPTED], ids=["intent", "delete-accepted"])
    def test_absence_on_resume_without_completion_recovers_never_succeeds(self, tmp_path, cleanup_secondary, journal):
        cleanup_secondary.get_custom_resource_strict.return_value = ABSENT
        fin = _cleanup_fin(_cleanup_state(tmp_path, journal), cleanup_secondary)

        with pytest.raises(FatalError, match="absent_without_completion"):
            fin._run_restore_cleanup_transaction()

        cleanup = _stored(tmp_path)["cleanup"]
        assert cleanup["state"] == "recovery_required"
        assert (cleanup["recovery"]["observed_uid"], cleanup["completed_at"]) == (None, None)
        assert cleanup["delete_accepted_at"] == journal["cleanup"]["delete_accepted_at"]
        cleanup_secondary.delete_restore_guarded.assert_not_called()

    def test_resume_after_an_accepted_delete_needs_a_new_accepted_delete(self, tmp_path, cleanup_secondary, no_sleep):
        cleanup_secondary.get_custom_resource_strict.side_effect = [_live(rv="2007"), ABSENT, ABSENT]
        cleanup_secondary.delete_restore_guarded.return_value = _accepted("2007")
        fin = _cleanup_fin(_cleanup_state(tmp_path, CLEANUP_ACCEPTED), cleanup_secondary)

        fin._run_restore_cleanup_transaction()

        cleanup = _stored(tmp_path)["cleanup"]
        assert (cleanup["state"], cleanup["final_get_resource_version"]) == ("completed", "2007")
        assert cleanup["delete_accepted_at"] != CLEANUP_ACCEPTED["cleanup"]["delete_accepted_at"]
        assert cleanup["operation_id"] == CLEANUP_ACCEPTED["cleanup"]["operation_id"]

    @pytest.mark.parametrize(
        "outcome",
        [StrictReadOutcome.error("read_failed"), StrictReadOutcome.crd_absent("kind_not_served")],
        ids=["read-failed", "kind-not-served"],
    )
    def test_an_unreadable_locator_is_never_absence(self, tmp_path, cleanup_secondary, outcome):
        cleanup_secondary.get_custom_resource_strict.return_value = outcome
        fin = _cleanup_fin(_cleanup_state(tmp_path, CLEANUP_ACCEPTED), cleanup_secondary)

        with pytest.raises(FatalError, match="unreadable is never absent"):
            fin._run_restore_cleanup_transaction()

        assert _stored(tmp_path) == CLEANUP_ACCEPTED
        cleanup_secondary.delete_restore_guarded.assert_not_called()

    def test_a_poll_timeout_writes_no_completion_and_stays_resumable(self, tmp_path, cleanup_secondary, no_sleep):
        cleanup_secondary.get_custom_resource_strict.return_value = _live()
        cleanup_secondary.delete_restore_guarded.return_value = _accepted()
        fin = _cleanup_fin(_cleanup_state(tmp_path, CLEANUP_INTENT), cleanup_secondary)

        with pytest.raises(FatalError, match="Timeout waiting for the journaled Restore"):
            fin._run_restore_cleanup_transaction()

        assert _stored(tmp_path)["cleanup"]["state"] == "delete_accepted"
        assert cleanup_secondary.get_custom_resource_strict.call_count == 62

    @pytest.mark.parametrize("status", [200, 202])
    def test_a_delete_the_installed_client_sees_answered_2xx_advances_to_delete_accepted(
        self, tmp_path, cleanup_secondary, no_sleep, status
    ):
        """The guarded DELETE runs through the installed kubernetes client down to a faked socket:
        a 202 carrying the deleted Restore is an accepted delete, exactly like a 200."""
        from tests.test_kube_client import _real_kube_client, _RecordingPoolManager

        deleted = {
            "kind": "Restore",
            "metadata": {"name": "acm-restore", "uid": RESTORE_UID, "resourceVersion": "2003"},
        }
        pool_manager = _RecordingPoolManager(status=status, payload=deleted)
        cleanup_secondary.get_custom_resource_strict.return_value = _live()
        cleanup_secondary.delete_restore_guarded.side_effect = _real_kube_client(pool_manager).delete_restore_guarded
        fin = _cleanup_fin(_cleanup_state(tmp_path, CLEANUP_INTENT), cleanup_secondary)

        with pytest.raises(FatalError, match="Timeout waiting for the journaled Restore"):
            fin._run_restore_cleanup_transaction()

        assert [call["method"] for call in pool_manager.calls] == ["DELETE"]
        assert _stored(tmp_path)["cleanup"]["state"] == "delete_accepted"

    @pytest.mark.parametrize("journal", [CLEANUP_DONE, CLEANUP_REPAIRED], ids=["completed", "repaired"])
    def test_terminal_cleanup_touches_nothing(self, tmp_path, cleanup_secondary, barrier, journal):
        fin = _cleanup_fin(_cleanup_state(tmp_path, journal), cleanup_secondary)

        assert fin._run_restore_cleanup_transaction() == journal

        barrier.assert_not_called()
        cleanup_secondary.get_custom_resource_strict.assert_not_called()
        cleanup_secondary.delete_restore_guarded.assert_not_called()

    def test_recovery_required_blocks_with_repair_guidance(self, tmp_path, cleanup_secondary):
        state = _cleanup_state(tmp_path, CLEANUP_RECOVERY)
        fin = _cleanup_fin(state, cleanup_secondary, primary_client=Mock(), primary_has_observability=True)

        with (
            patch.object(fin, "_disable_observability_on_old_hub") as mco_teardown,
            patch.object(fin, "_enable_backup_schedule") as enable,
        ):
            assert fin.finalize() is False

        mco_teardown.assert_not_called()
        enable.assert_not_called()
        cleanup_secondary.get_custom_resource_strict.assert_not_called()
        assert "cleanup_recovery_required" in state.state["errors"][-1]["error"]

    def test_an_operator_repair_is_one_audited_write_after_fresh_absence(self, tmp_path, cleanup_secondary):
        cleanup_secondary.get_custom_resource_strict.return_value = ABSENT
        fin = _cleanup_fin(
            _cleanup_state(tmp_path, CLEANUP_RECOVERY), cleanup_secondary, migration_cleanup_repair=REPAIR_REQUEST
        )

        original = RunRecord.record_migration_backups
        with patch.object(RunRecord, "record_migration_backups", autospec=True, side_effect=original) as record:
            fin._run_restore_cleanup_transaction()

        assert record.call_count == 1
        candidate = record.call_args.args[1]
        assert candidate["cleanup"]["state"] == "repaired"
        assert {key: value for key, value in candidate["cleanup"]["repair"].items() if key != "acknowledged_at"} == (
            REPAIR_REQUEST
        )
        assert candidate["cleanup"]["recovery"] == CLEANUP_RECOVERY["cleanup"]["recovery"]
        for field in ("delete_accepted_at", "absence_verified_at", "completed_at"):
            assert candidate["cleanup"][field] is None
        cleanup_secondary.delete_restore_guarded.assert_not_called()

    def test_a_repair_is_persisted_and_terminal(self, tmp_path, cleanup_secondary):
        cleanup_secondary.get_custom_resource_strict.return_value = ABSENT
        fin = _cleanup_fin(
            _cleanup_state(tmp_path, CLEANUP_RECOVERY), cleanup_secondary, migration_cleanup_repair=REPAIR_REQUEST
        )

        journal = fin._run_restore_cleanup_transaction()

        assert _stored(tmp_path) == journal
        assert journal["cleanup"]["state"] == "repaired"

    def test_a_repair_with_an_occupied_locator_is_refused_and_touches_nothing(self, tmp_path, cleanup_secondary):
        cleanup_secondary.get_custom_resource_strict.return_value = _live(uid="uid-new", rv="3003")
        fin = _cleanup_fin(
            _cleanup_state(tmp_path, CLEANUP_RECOVERY), cleanup_secondary, migration_cleanup_repair=REPAIR_REQUEST
        )

        with pytest.raises(FatalError, match="repair_locator_occupied"):
            fin._run_restore_cleanup_transaction()

        assert _stored(tmp_path) == CLEANUP_RECOVERY
        cleanup_secondary.delete_restore_guarded.assert_not_called()

    def test_a_repair_request_for_another_operation_is_refused(self, tmp_path, cleanup_secondary):
        cleanup_secondary.get_custom_resource_strict.return_value = ABSENT
        request = dict(REPAIR_REQUEST, operation_id="00000000-0000-4000-8000-000000000001")
        fin = _cleanup_fin(
            _cleanup_state(tmp_path, CLEANUP_RECOVERY), cleanup_secondary, migration_cleanup_repair=request
        )

        with pytest.raises(FatalError, match="repair_identity_mismatch"):
            fin._run_restore_cleanup_transaction()

        assert _stored(tmp_path) == CLEANUP_RECOVERY

    def test_a_repair_request_without_recovery_is_refused(self, tmp_path, cleanup_secondary):
        fin = _cleanup_fin(
            _cleanup_state(tmp_path, CLEANUP_DONE), cleanup_secondary, migration_cleanup_repair=REPAIR_REQUEST
        )

        with pytest.raises(FatalError, match="repair_not_permitted"):
            fin._run_restore_cleanup_transaction()

        assert _stored(tmp_path) == CLEANUP_DONE

    def test_a_dry_run_reads_and_writes_nothing(self, tmp_path, cleanup_secondary, barrier, caplog):
        cleanup_secondary.dry_run = True
        fin = _cleanup_fin(_cleanup_state(tmp_path, CLEANUP_INTENT), cleanup_secondary, dry_run=True)

        with caplog.at_level(logging.INFO):
            assert fin._run_restore_cleanup_transaction() == CLEANUP_INTENT

        assert "cleanup step: delete" in caplog.text
        barrier.assert_not_called()
        cleanup_secondary.get_custom_resource_strict.assert_not_called()
        cleanup_secondary.delete_restore_guarded.assert_not_called()
        assert _stored(tmp_path) == CLEANUP_INTENT

    def test_enablement_requires_a_fresh_absent_locator(self, tmp_path, cleanup_secondary):
        cleanup_secondary.list_custom_resources_strict.return_value = StrictReadOutcome.from_items([])
        cleanup_secondary.get_custom_resource_strict.return_value = _live(uid="uid-new", rv="3003")
        fin = _cleanup_fin(_cleanup_state(tmp_path, CLEANUP_DONE), cleanup_secondary)

        with pytest.raises(FatalError, match="locator_occupied"):
            fin._enable_backup_schedule(CLEANUP_DONE)

        fin.backup_manager.ensure_enabled.assert_not_called()

    @pytest.mark.parametrize("journal", [CLEANUP_DONE, CLEANUP_REPAIRED], ids=["completed", "repaired"])
    def test_enablement_after_terminal_cleanup_and_proven_absence(self, tmp_path, cleanup_secondary, journal):
        cleanup_secondary.list_custom_resources_strict.return_value = StrictReadOutcome.from_items([])
        cleanup_secondary.get_custom_resource_strict.return_value = ABSENT
        fin = _cleanup_fin(_cleanup_state(tmp_path, journal), cleanup_secondary)

        fin._enable_backup_schedule(journal)

        fin.backup_manager.ensure_enabled.assert_called_once_with("2.14.0")
        cleanup_secondary.get_custom_resource_strict.assert_called_once()

    @pytest.mark.parametrize("journal", [CLEANUP_ACCEPTED, CLEANUP_RECOVERY], ids=["delete-accepted", "recovery"])
    def test_enablement_refuses_a_non_terminal_cleanup(self, tmp_path, cleanup_secondary, journal):
        cleanup_secondary.list_custom_resources_strict.return_value = StrictReadOutcome.from_items([])
        fin = _cleanup_fin(_cleanup_state(tmp_path, journal), cleanup_secondary)

        with pytest.raises(FatalError, match="cleanup_not_terminal"):
            fin._enable_backup_schedule(journal)

        fin.backup_manager.ensure_enabled.assert_not_called()

    def test_generic_cleanup_never_routes_the_journaled_locator(self, tmp_path, cleanup_secondary):
        cleanup_secondary.list_custom_resources_strict.return_value = StrictReadOutcome.from_items(
            [_live(uid="uid-new", rv="3003").resource, {"metadata": {"name": "restore-acm-passive-sync"}}]
        )
        fin = _cleanup_fin(_cleanup_state(tmp_path, CLEANUP_DONE), cleanup_secondary)

        with pytest.raises(FatalError, match="locator_occupied"):
            fin._enable_backup_schedule(CLEANUP_DONE)

        cleanup_secondary.delete_custom_resource.assert_not_called()
        fin.backup_manager.ensure_enabled.assert_not_called()

    def test_generic_cleanup_deletes_other_switchover_restores_after_the_terminal_locator(
        self, tmp_path, cleanup_secondary
    ):
        other = {"metadata": {"name": "restore-acm-passive-sync"}, "spec": {"syncRestoreWithNewBackups": True}}
        cleanup_secondary.list_custom_resources_strict.return_value = StrictReadOutcome.from_items([other])
        cleanup_secondary.get_custom_resource_strict.return_value = ABSENT
        fin = _cleanup_fin(_cleanup_state(tmp_path, CLEANUP_DONE), cleanup_secondary)

        fin._enable_backup_schedule(CLEANUP_DONE)

        assert cleanup_secondary.delete_custom_resource.call_args.kwargs["name"] == "restore-acm-passive-sync"
        fin.backup_manager.ensure_enabled.assert_called_once()

    def test_an_incomplete_restore_inventory_blocks_generic_cleanup(self, tmp_path, cleanup_secondary):
        cleanup_secondary.list_custom_resources_strict.return_value = StrictReadOutcome.error("inventory_incomplete")
        fin = _cleanup_fin(_cleanup_state(tmp_path, CLEANUP_DONE), cleanup_secondary)

        with pytest.raises(SwitchoverError, match="Restore inventory read failed"):
            fin._enable_backup_schedule(CLEANUP_DONE)

        cleanup_secondary.delete_custom_resource.assert_not_called()
        fin.backup_manager.ensure_enabled.assert_not_called()

    def test_the_transaction_runs_before_the_old_hub_mco_teardown(self, tmp_path, cleanup_secondary):
        order: list[str] = []
        fin = _cleanup_fin(
            _cleanup_state(tmp_path, CLEANUP_DONE),
            cleanup_secondary,
            primary_client=Mock(),
            primary_has_observability=True,
        )
        with (
            patch.object(fin, "_run_restore_cleanup_transaction", side_effect=lambda: order.append("cleanup")),
            patch.object(fin, "_disable_observability_on_old_hub", side_effect=lambda: order.append("mco")),
            patch.object(fin, "_enable_backup_schedule", side_effect=lambda journal: order.append("enable")),
            patch.object(fin, "_verify_backup_schedule_enabled"),
            patch.object(fin, "_fix_backup_schedule_collision"),
            patch.object(fin, "_get_backup_verify_timeout", return_value=1),
            patch.object(fin, "_verify_new_backups"),
            patch.object(fin, "_verify_backup_integrity"),
            patch.object(fin, "_verify_multiclusterhub_health"),
            patch.object(fin, "_run_reset_auto_import_strategy_step"),
            patch.object(fin, "_handle_old_hub"),
            patch.object(fin, "_verify_old_hub_state"),
        ):
            assert fin.finalize() is True

        assert order == ["cleanup", "mco", "enable"]


@pytest.mark.unit
@pytest.mark.parametrize(
    "outcome", [StrictReadOutcome.error("inventory_incomplete"), StrictReadOutcome.error("read_failed")]
)
def test_an_unproven_finalization_schedule_inventory_fails_closed(finalization, mock_secondary_client, outcome):
    """R4-04 Task 10 (Task 0 carry-forward): no bounded, advisory BackupSchedule read feeds a decision."""
    mock_secondary_client.list_custom_resources_strict.return_value = outcome
    mock_secondary_client.list_custom_resources.return_value = []

    with pytest.raises(SwitchoverError, match="BackupSchedule inventory"):
        finalization._get_backup_schedules()


@pytest.mark.unit
def test_a_dry_run_records_no_finalization_step_marker(tmp_path, cleanup_secondary):
    """Task 9 B1 for finalization: a dry-run skips each step's work, so a marker it recorded
    would let a later executing run skip work that never ran."""
    state = _cleanup_state(tmp_path, CLEANUP_INTENT)
    cleanup_secondary.dry_run = True
    fin = _cleanup_fin(state, cleanup_secondary, dry_run=True, primary_client=Mock(), primary_has_observability=True)
    steps = {
        "_disable_observability_on_old_hub": (),
        "_enable_backup_schedule": ("journal",),
        "_verify_backup_schedule_enabled": (),
        "_fix_backup_schedule_collision": (),
        "_verify_new_backups": ("timeout",),
        "_verify_backup_integrity": (),
        "_verify_multiclusterhub_health": (),
        "_handle_old_hub": (),
        "_verify_old_hub_state": (),
    }
    with (
        patch.object(fin, "_get_backup_verify_timeout", return_value=1),
        patch.object(fin, "_run_reset_auto_import_strategy_step"),
    ):
        mocks = {}
        for name in steps:
            mocks[name] = patch.object(fin, name).start()
        try:
            assert fin.finalize() is True
        finally:
            patch.stopall()

    assert all(mock.called for mock in mocks.values())
    assert state.state["completed_steps"] == []
    assert json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))["completed_steps"] == []


@pytest.mark.unit
def test_a_dry_run_still_honours_markers_an_executing_run_recorded(tmp_path, cleanup_secondary):
    state = _cleanup_state(tmp_path, CLEANUP_INTENT)
    state.mark_step_completed("enable_backup_schedule")
    cleanup_secondary.dry_run = True
    fin = _cleanup_fin(state, cleanup_secondary, dry_run=True)
    with (
        patch.object(fin, "_enable_backup_schedule") as enable,
        patch.object(fin, "_verify_backup_schedule_enabled"),
        patch.object(fin, "_fix_backup_schedule_collision"),
        patch.object(fin, "_get_backup_verify_timeout", return_value=1),
        patch.object(fin, "_verify_new_backups"),
        patch.object(fin, "_verify_backup_integrity"),
        patch.object(fin, "_verify_multiclusterhub_health"),
        patch.object(fin, "_run_reset_auto_import_strategy_step"),
        patch.object(fin, "_handle_old_hub"),
    ):
        assert fin.finalize() is True

    enable.assert_not_called()


# -- Task 10 review findings (Codex, b0a5fae0..a0e41fac) -------------------------------------------


def _mch(version):
    return StrictReadOutcome.from_items(
        [{"metadata": {"name": "multiclusterhub"}, "status": {"currentVersion": version}}]
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "journal", [CLEANUP_POST, CLEANUP_INTENT, CLEANUP_ACCEPTED], ids=["barrier", "intent", "accepted"]
)
def test_a_changed_controller_lane_refuses_before_the_barrier_or_a_resumed_delete(
    tmp_path, cleanup_secondary, barrier, journal
):
    """Finding 3: an ACM 2.14 journal on a destination upgraded to 2.17 never changes contract (A section 10)."""
    cleanup_secondary.list_custom_resources_strict.return_value = _mch("2.17.1")
    cleanup_secondary.get_custom_resource_strict.return_value = _live()
    fin = _cleanup_fin(_cleanup_state(tmp_path, journal), cleanup_secondary)

    with pytest.raises(FatalError, match="frozen on ACM 2.14, but the destination hub now runs ACM 2.17") as refused:
        fin._run_restore_cleanup_transaction()

    assert "(controller_lane_changed)" in str(refused.value)
    barrier.assert_not_called()
    cleanup_secondary.delete_restore_guarded.assert_not_called()
    assert _stored(tmp_path) == journal


@pytest.mark.unit
def test_collision_recreation_requires_a_fresh_absent_locator(tmp_path, cleanup_secondary):
    """Finding 1: with the enablement marker complete, collision repair alone may still recreate the schedule."""
    cleanup_secondary.list_custom_resources_strict.return_value = StrictReadOutcome.from_items(
        [{"metadata": {"name": "schedule", "uid": "uid-1"}, "spec": {}, "status": {"phase": "Enabled"}}]
    )
    cleanup_secondary.get_custom_resource.return_value = {"metadata": {"name": "schedule", "uid": "uid-1"}, "spec": {}}
    cleanup_secondary.get_custom_resource_strict.return_value = _live(uid="uid-new", rv="3003")
    state = _cleanup_state(tmp_path, CLEANUP_DONE)
    state.mark_step_completed("enable_backup_schedule")
    fin = _cleanup_fin(state, cleanup_secondary)
    fin._cleanup_journal = CLEANUP_DONE

    with pytest.raises(FatalError, match="locator_occupied"):
        fin._fix_backup_schedule_collision()

    cleanup_secondary.delete_custom_resource.assert_not_called()
    cleanup_secondary.delete_custom_resource_preconditioned.assert_not_called()
    cleanup_secondary.create_custom_resource.assert_not_called()


def _collision_fin(tmp_path, cleanup_secondary):
    schedule = {"metadata": {"name": "schedule", "uid": "uid-1"}, "spec": {"paused": False}, "status": {}}
    cleanup_secondary.list_custom_resources_strict.return_value = StrictReadOutcome.from_items([schedule])
    cleanup_secondary.get_custom_resource.return_value = copy.deepcopy(schedule)
    state = _cleanup_state(tmp_path, CLEANUP_DONE)
    state.mark_step_completed("enable_backup_schedule")
    fin = _cleanup_fin(state, cleanup_secondary)
    fin._cleanup_journal = CLEANUP_DONE
    return fin


@pytest.mark.unit
def test_collision_repair_never_reads_the_locator_between_the_delete_and_the_recreate(tmp_path, cleanup_secondary):
    """CodeRabbit 4274709915: the last locator proof precedes the BackupSchedule DELETE. A read
    failure that would have struck between the DELETE and the re-create can no longer leave
    the hub without a BackupSchedule."""
    fin = _collision_fin(tmp_path, cleanup_secondary)
    cleanup_secondary.get_custom_resource_strict.side_effect = [ABSENT, StrictReadOutcome.error("Timeout")]

    with patch.object(fin, "_wait_for_backup_schedule_deletion"):
        fin._fix_backup_schedule_collision()

    assert cleanup_secondary.get_custom_resource_strict.call_count == 1
    cleanup_secondary.delete_custom_resource.assert_called_once()
    cleanup_secondary.create_custom_resource.assert_called_once()


@pytest.mark.unit
def test_a_transient_locator_read_error_leaves_the_backup_schedule_intact_and_retryable(tmp_path, cleanup_secondary):
    fin = _collision_fin(tmp_path, cleanup_secondary)
    cleanup_secondary.get_custom_resource_strict.side_effect = [StrictReadOutcome.error("Timeout")]

    with patch.object(fin, "_wait_for_backup_schedule_deletion"):
        with pytest.raises(FatalError, match="unreadable is never absent") as refused:
            fin._fix_backup_schedule_collision()

    # Raised as the real cause, not wrapped as a recreate failure.
    assert "Failed to recreate" not in str(refused.value)
    cleanup_secondary.delete_custom_resource.assert_not_called()
    cleanup_secondary.create_custom_resource.assert_not_called()

    # The BackupSchedule is still there, so a plain retry repairs it.
    cleanup_secondary.get_custom_resource_strict.side_effect = None
    cleanup_secondary.get_custom_resource_strict.return_value = ABSENT
    with patch.object(fin, "_wait_for_backup_schedule_deletion"):
        fin._fix_backup_schedule_collision()
    cleanup_secondary.delete_custom_resource.assert_called_once()
    cleanup_secondary.create_custom_resource.assert_called_once()


@pytest.mark.unit
def test_the_poll_deadline_covers_slow_reads_and_writes_no_completion(tmp_path, cleanup_secondary, no_sleep):
    """Finding 4: two-second reads with absence on the last poll would finish after the 300 s timeout."""
    clock = {"now": 0.0}

    def _slow_read(**kwargs):
        clock["now"] += 2.0
        return _live() if clock["now"] < 400 else ABSENT

    no_sleep.side_effect = lambda seconds: clock.__setitem__("now", clock["now"] + seconds)
    cleanup_secondary.get_custom_resource_strict.side_effect = _slow_read
    cleanup_secondary.delete_restore_guarded.return_value = _accepted()
    fin = _cleanup_fin(_cleanup_state(tmp_path, CLEANUP_INTENT), cleanup_secondary)

    with patch("modules.finalization.time.monotonic", side_effect=lambda: clock["now"]):
        with pytest.raises(FatalError, match="Timeout waiting for the journaled Restore"):
            fin._run_restore_cleanup_transaction()

    assert _stored(tmp_path)["cleanup"]["state"] == "delete_accepted"
    assert clock["now"] <= 300 + 30


@pytest.mark.unit
def test_a_final_read_ending_past_the_deadline_never_completes(tmp_path, cleanup_secondary, no_sleep):
    """Finding 4: absence seen just before the deadline, then a slow final read ending after it."""
    clock = {"now": 0.0}
    reads = {"count": 0}

    def _read(**kwargs):
        reads["count"] += 1
        if reads["count"] == 1:  # the pre-delete read
            clock["now"] += 1
            return _live()
        if clock["now"] < 290:
            clock["now"] += 1
            return _live()
        clock["now"] += 1 if reads.setdefault("absent", 0) == 0 else 10
        reads["absent"] += 1
        return ABSENT

    no_sleep.side_effect = lambda seconds: clock.__setitem__("now", clock["now"] + seconds)
    cleanup_secondary.get_custom_resource_strict.side_effect = _read
    cleanup_secondary.delete_restore_guarded.return_value = _accepted()
    fin = _cleanup_fin(_cleanup_state(tmp_path, CLEANUP_INTENT), cleanup_secondary)

    with patch("modules.finalization.time.monotonic", side_effect=lambda: clock["now"]):
        with pytest.raises(FatalError, match="Timeout waiting for the journaled Restore"):
            fin._run_restore_cleanup_transaction()

    assert reads["absent"] == 2  # the first absence and the late final read
    assert _stored(tmp_path)["cleanup"]["state"] == "delete_accepted"


# -- R4-04 Task 11: the evidence gate before integrated old-hub actions (July design section 4) --

_LATER_STEPS = (
    "_verify_backup_schedule_enabled",
    "_fix_backup_schedule_collision",
    "_verify_new_backups",
    "_verify_backup_integrity",
    "_verify_multiclusterhub_health",
    "_run_reset_auto_import_strategy_step",
    "_verify_old_hub_state",
)


def _mutated(journal, **changes):
    """A deep copy with each "__"-separated path set."""
    out = copy.deepcopy(journal)
    for path, value in changes.items():
        *parents, last = path.split("__")
        target = out
        for part in parents:
            target = target[part]
        target[last] = value
    return out


def _old_hub_fin(tmp_path, secondary, journal, *, old_hub_action="secondary", dry_run=False):
    return _cleanup_fin(
        _cleanup_state(tmp_path, journal),
        secondary,
        primary_client=Mock(name="primary"),
        primary_has_observability=True,
        old_hub_action=old_hub_action,
        dry_run=dry_run,
    )


@contextmanager
def _old_hub_actions(fin, order):
    """Record the old-hub actions (the MCO adapter runs, up to its shared teardown); every other
    finalization step after the cleanup is inert."""
    with (
        patch.object(fin, "_get_backup_verify_timeout", return_value=1),
        patch.object(fin, "_enable_backup_schedule", side_effect=lambda journal: order.append("enable")),
        patch("modules.finalization.Decommission") as decommission_class,
        patch.object(fin, "_setup_old_hub_as_secondary", side_effect=lambda: order.append("secondary")),
        patch.object(fin, "_decommission_old_hub", side_effect=lambda: order.append("decommission")),
    ):
        teardown = decommission_class.return_value.teardown_observability
        teardown.side_effect = lambda **kwargs: order.append("mco") or SubstepExecution(
            SubstepOutcome.COMPLETED, changed=True
        )
        for name in _LATER_STEPS:
            patch.object(fin, name).start()
        try:
            yield
        finally:
            patch.stopall()


@pytest.mark.unit
class TestOldHubEvidenceGate:
    """Plan Task 11, Python form factor: the gate precedes every integrated old-hub mutation."""

    @pytest.mark.parametrize("journal", [CLEANUP_DONE, CLEANUP_REPAIRED], ids=["completed", "repaired"])
    def test_a_terminal_cleanup_and_a_fresh_absent_locator_open_every_old_hub_action(
        self, tmp_path, cleanup_secondary, journal
    ):
        cleanup_secondary.get_custom_resource_strict.return_value = ABSENT
        fin = _old_hub_fin(tmp_path, cleanup_secondary, journal)
        order: list[str] = []

        with _old_hub_actions(fin, order):
            assert fin.finalize() is True

        assert order == ["mco", "enable", "secondary"]
        # One fresh strict locator read before the MCO teardown and one before the passive Restore.
        assert cleanup_secondary.get_custom_resource_strict.call_count == 2
        cleanup_secondary.delete_restore_guarded.assert_not_called()

    def test_the_teardown_barrier_of_this_pass_precedes_the_gate_and_the_mco_teardown(
        self, tmp_path, cleanup_secondary, barrier, no_sleep
    ):
        """A pass that starts the cleanup proves the barrier by control flow before any old-hub action."""
        order: list[str] = []
        barrier.side_effect = lambda journal: order.append("barrier")
        cleanup_secondary.get_custom_resource_strict.side_effect = [_live(), ABSENT, ABSENT, ABSENT, ABSENT]
        cleanup_secondary.delete_restore_guarded.return_value = _accepted()
        fin = _old_hub_fin(tmp_path, cleanup_secondary, CLEANUP_POST)

        with _old_hub_actions(fin, order):
            assert fin.finalize() is True

        assert order == ["barrier", "mco", "enable", "secondary"]
        stored = _stored(tmp_path)
        assert stored["cleanup"]["state"] == "completed"
        assert stored["restore"]["teardown_revalidated_at"] is not None

    def test_a_failed_teardown_barrier_reaches_no_old_hub_action(self, tmp_path, cleanup_secondary, barrier):
        barrier.side_effect = FatalError("Journaled credentials child was replaced or changed")
        fin = _old_hub_fin(tmp_path, cleanup_secondary, CLEANUP_POST)
        order: list[str] = []

        with _old_hub_actions(fin, order):
            assert fin.finalize() is False

        assert order == []

    @pytest.mark.parametrize(
        "journal, read, code",
        [
            (CLEANUP_DONE, _live(uid="uid-new", rv="3003"), "locator_occupied"),
            (_mutated(CLEANUP_DONE, restore__names_verified_at=None), ABSENT, "activation_names_unverified"),
        ],
        ids=["occupied-locator", "unverified-activation-names"],
    )
    def test_missing_evidence_refuses_before_the_mco_teardown(self, tmp_path, cleanup_secondary, journal, read, code):
        cleanup_secondary.get_custom_resource_strict.return_value = read
        fin = _old_hub_fin(tmp_path, cleanup_secondary, journal)
        order: list[str] = []

        with _old_hub_actions(fin, order):
            assert fin.finalize() is False

        assert order == []
        assert code in fin.state.state["errors"][-1]["error"]
        assert _stored(tmp_path) == journal

    def test_the_gate_is_re_evaluated_freshly_before_the_passive_restore(self, tmp_path, cleanup_secondary):
        """A replacement that appears after the MCO teardown gate still blocks the later disposition."""
        cleanup_secondary.get_custom_resource_strict.side_effect = [ABSENT, _live(uid="uid-new", rv="3003")]
        fin = _old_hub_fin(tmp_path, cleanup_secondary, CLEANUP_DONE)
        order: list[str] = []

        with _old_hub_actions(fin, order):
            assert fin.finalize() is False

        assert order == ["mco", "enable"]
        assert "locator_occupied" in fin.state.state["errors"][-1]["error"]

    def test_integrated_decommission_requires_the_evidence(self, tmp_path, cleanup_secondary):
        cleanup_secondary.get_custom_resource_strict.return_value = ABSENT
        fin = _old_hub_fin(
            tmp_path,
            cleanup_secondary,
            _mutated(CLEANUP_DONE, restore__names_verified_at=None),
            old_hub_action="decommission",
        )
        order: list[str] = []

        with _old_hub_actions(fin, order):
            assert fin.finalize() is False

        assert order == ["enable"]
        assert "activation_names_unverified" in fin.state.state["errors"][-1]["error"]

    def test_an_unreadable_locator_is_never_absence(self, tmp_path, cleanup_secondary):
        cleanup_secondary.get_custom_resource_strict.return_value = StrictReadOutcome.error("read_failed")
        fin = _old_hub_fin(tmp_path, cleanup_secondary, CLEANUP_DONE)
        order: list[str] = []

        with _old_hub_actions(fin, order):
            assert fin.finalize() is False

        assert order == []

    def test_old_hub_action_none_reads_nothing(self, tmp_path, cleanup_secondary):
        fin = _old_hub_fin(tmp_path, cleanup_secondary, CLEANUP_DONE, old_hub_action="none")
        order: list[str] = []

        with _old_hub_actions(fin, order):
            assert fin.finalize() is True

        assert order == ["enable"]
        cleanup_secondary.get_custom_resource_strict.assert_not_called()

    def test_a_dry_run_previews_the_old_hub_actions_without_the_gate(self, tmp_path, cleanup_secondary):
        """A preview keeps the journal non-terminal; it reads nothing and is never gated."""
        cleanup_secondary.dry_run = True
        fin = _old_hub_fin(tmp_path, cleanup_secondary, CLEANUP_INTENT, dry_run=True)
        order: list[str] = []

        with _old_hub_actions(fin, order):
            assert fin.finalize() is True

        assert order == ["mco", "enable", "secondary"]
        cleanup_secondary.get_custom_resource_strict.assert_not_called()
        assert _stored(tmp_path) == CLEANUP_INTENT

    def test_standalone_decommission_never_consults_the_migration_journal(self):
        """J section 4: standalone decommission keeps its own target safety, outside this transaction."""
        import acm_switchover

        source = inspect.getsource(acm_switchover.run_decommission) + inspect.getsource(decommission_module)
        for name in ("migration_backups", "migration_cleanup", "require_old_hub_evidence"):
            assert name not in source


# -- Task 10 review round 2: the polling deadline holds at classification ---------------------------


@pytest.mark.unit
def test_a_completion_classified_past_the_deadline_is_never_persisted(tmp_path, cleanup_secondary, no_sleep):
    """Edge 1 (Codex): the read ends at 299.9 s, classification happens at 300.1 s: no completion."""
    clock = {"now": 0.0, "bump": False}
    reads = {"count": 0}

    def _monotonic():
        now = clock["now"]
        if clock["bump"]:  # the first check after the final read passes; time moves on before persisting
            clock["bump"] = False
            clock["now"] += 0.2
        return now

    def _read(**kwargs):
        reads["count"] += 1
        if reads["count"] == 1:
            return _live()
        if reads["count"] == 2:
            return ABSENT
        clock["now"] = 299.9  # the final read ends just inside the deadline (set at 0 + 300)
        clock["bump"] = True
        return ABSENT

    cleanup_secondary.get_custom_resource_strict.side_effect = _read
    cleanup_secondary.delete_restore_guarded.return_value = _accepted()
    fin = _cleanup_fin(_cleanup_state(tmp_path, CLEANUP_INTENT), cleanup_secondary)

    with patch("modules.finalization.time.monotonic", side_effect=_monotonic):
        with pytest.raises(FatalError, match="poll_deadline_passed"):
            fin._run_restore_cleanup_transaction()

    assert reads["count"] == 3
    assert _stored(tmp_path)["cleanup"]["state"] == "delete_accepted"


@pytest.mark.unit
@pytest.mark.parametrize(("remaining", "expected"), [(0.2, None), (0.999, None), (1.7, 1), (299.5, 30)])
def test_a_poll_read_is_capped_to_the_floored_remaining_budget(tmp_path, cleanup_secondary, remaining, expected):
    """Edge 2 (Codex): with 0.2 s left no 1 s read is allowed; each read is floored and capped."""
    cleanup_secondary.get_custom_resource_strict.return_value = ABSENT
    fin = _cleanup_fin(_cleanup_state(tmp_path, CLEANUP_ACCEPTED), cleanup_secondary)

    with patch("modules.finalization.time.monotonic", return_value=1000.0):
        result = fin._strict_get_before(CLEANUP_ACCEPTED, 1000.0 + remaining)

    if expected is None:
        assert result is finalization_module._DEADLINE_PASSED
        cleanup_secondary.get_custom_resource_strict.assert_not_called()
    else:
        assert result is None
        assert cleanup_secondary.get_custom_resource_strict.call_args.kwargs["request_timeout"] == expected


@pytest.mark.unit
class TestOldHubAdapterEntryGates:
    """Codex warning on cb0302ff: each old-hub adapter gates itself, not only through finalize()."""

    def test_the_mco_adapter_refuses_without_the_journal_context(self, tmp_path, cleanup_secondary):
        fin = _old_hub_fin(tmp_path, cleanup_secondary, None)

        with patch("modules.finalization.Decommission") as decommission_class:
            with pytest.raises(FatalError, match="journal_required"):
                fin._disable_observability_on_old_hub()

        decommission_class.assert_not_called()

    def test_the_mco_adapter_refuses_an_occupied_locator(self, tmp_path, cleanup_secondary):
        cleanup_secondary.get_custom_resource_strict.return_value = _live(uid="uid-new", rv="3003")
        fin = _old_hub_fin(tmp_path, cleanup_secondary, CLEANUP_DONE)
        fin._cleanup_journal = CLEANUP_DONE

        with patch("modules.finalization.Decommission") as decommission_class:
            with pytest.raises(FatalError, match="locator_occupied"):
                fin._disable_observability_on_old_hub()

        decommission_class.assert_not_called()

    @pytest.mark.parametrize(
        "action, adapter", [("secondary", "_setup_old_hub_as_secondary"), ("decommission", "_decommission_old_hub")]
    )
    def test_the_disposition_refuses_without_the_journal_context(self, tmp_path, cleanup_secondary, action, adapter):
        fin = _old_hub_fin(tmp_path, cleanup_secondary, None, old_hub_action=action)

        with patch.object(fin, adapter) as mutation:
            with pytest.raises(FatalError, match="journal_required"):
                fin._handle_old_hub()

        mutation.assert_not_called()
