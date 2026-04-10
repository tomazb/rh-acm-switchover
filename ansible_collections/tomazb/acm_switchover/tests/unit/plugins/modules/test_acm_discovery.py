from ansible_collections.tomazb.acm_switchover.plugins.modules.acm_discovery import classify_hub_state


def test_classify_hub_state_marks_passive_sync_secondary():
    state = classify_hub_state({"restore_state": "passive-sync", "managed_clusters": 0})
    assert state == "secondary"


def test_classify_hub_state_marks_active_primary():
    state = classify_hub_state({"restore_state": "none", "managed_clusters": 3})
    assert state == "primary"
