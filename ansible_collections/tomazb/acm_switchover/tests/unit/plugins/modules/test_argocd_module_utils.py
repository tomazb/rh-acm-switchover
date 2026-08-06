"""Unit tests for shipped module_utils/argocd.py helpers.

(The former test_acm_argocd_autosync.py also certified build_pause_patch(),
a helper no task or module called; that helper and its tests were removed —
the shipped pause/resume patch shape is guarded by the shipped-YAML contract
tests instead. See ADR-0001 / issue #207.)
"""

from ansible_collections.tomazb.acm_switchover.plugins.module_utils.argocd import (
    is_acm_touching_application,
    is_autosync_enabled,
)


def test_acm_touching_app_matches_backup_schedule_kind():
    assert (
        is_acm_touching_application(
            {
                "metadata": {"namespace": "argocd", "name": "acm-app"},
                "status": {
                    "resources": [
                        {
                            "kind": "BackupSchedule",
                            "namespace": "open-cluster-management-backup",
                        }
                    ]
                },
            }
        )
        is True
    )


def test_is_autosync_enabled_false_without_sync_policy():
    assert is_autosync_enabled({"spec": {}}) is False


def test_is_autosync_enabled_false_when_automated_missing():
    assert is_autosync_enabled({"spec": {"syncPolicy": {"syncOptions": ["CreateNamespace=true"]}}}) is False


def test_is_autosync_enabled_false_when_automated_is_null():
    assert is_autosync_enabled({"spec": {"syncPolicy": {"automated": None}}}) is False


def test_is_autosync_enabled_true_when_automated_is_empty_map():
    assert is_autosync_enabled({"spec": {"syncPolicy": {"automated": {}}}}) is True


def test_is_autosync_enabled_true_when_automated_has_fields():
    assert is_autosync_enabled({"spec": {"syncPolicy": {"automated": {"prune": True}}}}) is True
