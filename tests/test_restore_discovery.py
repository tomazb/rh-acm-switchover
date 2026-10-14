"""Unit tests for modules/restore_discovery.py (passive sync Restore discovery)."""

from unittest.mock import Mock

import pytest

import modules.restore_discovery as restore_discovery
from lib.constants import BACKUP_NAMESPACE, RESTORE_PASSIVE_SYNC_NAME, SPEC_SYNC_RESTORE_WITH_NEW_BACKUPS
from lib.exceptions import FatalError
from lib.strict_read import StrictReadOutcome


@pytest.fixture
def mock_secondary_client():
    mock = Mock()
    mock.dry_run = False
    return mock


@pytest.mark.unit
class TestFindPassiveSyncRestore:
    """Tests for the find_passive_sync_restore discovery function."""

    def test_find_by_sync_restore_with_new_backups(self, mock_secondary_client):
        """Test discovery finds restore by syncRestoreWithNewBackups=true."""
        mock_secondary_client.list_custom_resources.return_value = [
            {
                "metadata": {"name": "my-custom-restore"},
                "spec": {SPEC_SYNC_RESTORE_WITH_NEW_BACKUPS: True},
            }
        ]
        mock_secondary_client.get_custom_resource.return_value = None

        result = restore_discovery.find_passive_sync_restore(mock_secondary_client)

        assert result is not None
        assert result["metadata"]["name"] == "my-custom-restore"
        # Should not have called get_custom_resource since found via list
        mock_secondary_client.get_custom_resource.assert_not_called()

    def test_find_rejects_well_known_name_without_sync_restore_flag(self, mock_secondary_client):
        """Conventional restore name is not enough without syncRestoreWithNewBackups=true."""
        mock_secondary_client.list_custom_resources.return_value = [
            {
                "metadata": {"name": RESTORE_PASSIVE_SYNC_NAME},
                "spec": {},
            },
            {
                "metadata": {"name": "sync-disabled-restore"},
                "spec": {SPEC_SYNC_RESTORE_WITH_NEW_BACKUPS: False},
            },
        ]

        result = restore_discovery.find_passive_sync_restore(mock_secondary_client)

        assert result is None
        mock_secondary_client.get_custom_resource.assert_not_called()

    def test_find_no_restore_found(self, mock_secondary_client):
        """Test discovery returns None when no restore found."""
        mock_secondary_client.list_custom_resources.return_value = []
        mock_secondary_client.get_custom_resource.return_value = None

        result = restore_discovery.find_passive_sync_restore(mock_secondary_client)

        assert result is None
        mock_secondary_client.get_custom_resource.assert_not_called()

    def test_find_prefers_sync_restore_over_well_known(self, mock_secondary_client):
        """Test that syncRestoreWithNewBackups is preferred over well-known name."""
        mock_secondary_client.list_custom_resources.return_value = [
            {
                "metadata": {"name": "custom-passive-sync"},
                "spec": {SPEC_SYNC_RESTORE_WITH_NEW_BACKUPS: True},
            },
            {
                "metadata": {"name": RESTORE_PASSIVE_SYNC_NAME},
                "spec": {SPEC_SYNC_RESTORE_WITH_NEW_BACKUPS: False},
            },
        ]

        result = restore_discovery.find_passive_sync_restore(mock_secondary_client)

        assert result is not None
        assert result["metadata"]["name"] == "custom-passive-sync"
        # Should not have tried fallback
        mock_secondary_client.get_custom_resource.assert_not_called()

    def test_find_prefers_newest_passive_sync_restore(self, mock_secondary_client):
        """Passive restore discovery should prefer the newest sync-enabled restore."""
        mock_secondary_client.list_custom_resources.return_value = [
            {
                "metadata": {"name": "older-passive-sync", "creationTimestamp": "2026-03-29T09:00:00Z"},
                "spec": {SPEC_SYNC_RESTORE_WITH_NEW_BACKUPS: True},
            },
            {
                "metadata": {"name": "newer-passive-sync", "creationTimestamp": "2026-03-30T09:00:00Z"},
                "spec": {SPEC_SYNC_RESTORE_WITH_NEW_BACKUPS: True},
            },
        ]

        result = restore_discovery.find_passive_sync_restore(mock_secondary_client)

        assert result is not None
        assert result["metadata"]["name"] == "newer-passive-sync"
        mock_secondary_client.get_custom_resource.assert_not_called()


def _restore(name, sync=True, created="2026-03-30T09:00:00Z"):
    metadata = {"name": name, "namespace": BACKUP_NAMESPACE}
    if created is not None:
        metadata["creationTimestamp"] = created
    return {"metadata": metadata, "spec": {SPEC_SYNC_RESTORE_WITH_NEW_BACKUPS: sync}}


@pytest.mark.unit
class TestFindPassiveSyncRestoreStrict:
    """R4-04: "no passive Restore" is concluded only from a positively complete strict LIST."""

    def _find(self, outcome):
        client = Mock()
        client.list_custom_resources_strict.return_value = outcome
        result = restore_discovery.find_passive_sync_restore_strict(client)
        client.list_custom_resources.assert_not_called()
        return result

    def test_newest_sync_restore_is_selected(self):
        older = _restore("older", created="2026-03-29T09:00:00Z")
        newer = _restore("newer")
        assert self._find(StrictReadOutcome.from_items([older, newer, _restore("off", sync=False)])) is newer

    def test_a_complete_list_without_a_sync_restore_is_none(self):
        assert self._find(StrictReadOutcome.from_items([_restore("off", sync=False)])) is None
        assert self._find(StrictReadOutcome.from_items([])) is None

    @pytest.mark.parametrize(
        "outcome",
        [
            StrictReadOutcome.error("read_failed"),
            StrictReadOutcome.error("inventory_incomplete"),
            StrictReadOutcome.crd_absent("kind_not_served"),
            StrictReadOutcome.namespace_absent("namespace_not_found"),
        ],
    )
    def test_a_failed_or_absent_read_is_never_no_restore(self, outcome):
        with pytest.raises(FatalError, match="discovery"):
            self._find(outcome)

    @pytest.mark.parametrize(
        "item",
        [
            {"metadata": {"name": ""}, "spec": {}},
            {"metadata": "x", "spec": {}},
            {"metadata": {"name": "r"}, "spec": []},
            _restore("no-created", created=None),
            _restore("bad-created", created="yesterday"),
        ],
    )
    def test_a_malformed_restore_blocks(self, item):
        with pytest.raises(FatalError):
            self._find(StrictReadOutcome.from_items([item]))

    def test_a_tie_for_the_newest_blocks(self):
        with pytest.raises(FatalError, match="share the newest"):
            self._find(StrictReadOutcome.from_items([_restore("a"), _restore("b")]))

    def test_the_sync_flag_must_be_boolean_true(self):
        assert self._find(StrictReadOutcome.from_items([_restore("string-true", sync="true")])) is None
