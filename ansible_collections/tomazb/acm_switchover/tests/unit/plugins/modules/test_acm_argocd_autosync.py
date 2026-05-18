from ansible_collections.tomazb.acm_switchover.plugins.module_utils.argocd import (
    build_pause_patch,
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


def test_build_pause_patch_nulls_automated_and_sets_run_id():
    patch = build_pause_patch({"automated": {"prune": True}}, "run-123")
    assert patch["metadata"]["annotations"]["acm-switchover.argoproj.io/paused-by"] == "run-123"
    assert patch["spec"]["syncPolicy"]["automated"] is None


def test_build_pause_patch_handles_missing_sync_policy():
    patch = build_pause_patch(None, "run-123")
    assert patch["metadata"]["annotations"]["acm-switchover.argoproj.io/paused-by"] == "run-123"
    assert patch["spec"]["syncPolicy"] == {}


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
