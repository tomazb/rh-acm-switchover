"""Tests for the acm_argocd_filter module."""

from ansible_collections.tomazb.acm_switchover.plugins.module_utils.argocd import (
    PAUSE_BLOCK_REASON_APPLICATIONSET_MANAGED,
    PAUSE_BLOCK_REASON_UNKNOWN_ACM_IMPACT,
    filter_acm_applications,
    find_argocd_pause_blockers,
    has_applicationset_owner,
    is_acm_touching_application,
)


def _app(name: str, resources: list[dict]) -> dict:
    return {
        "metadata": {"namespace": "argocd", "name": name},
        "status": {"resources": resources},
    }


def test_filters_to_only_acm_touching_apps():
    apps = [
        _app(
            "acm-app",
            [{"kind": "BackupSchedule", "namespace": "open-cluster-management-backup"}],
        ),
        _app("unrelated-app", [{"kind": "Deployment", "namespace": "my-namespace"}]),
        _app("mce-app", [{"kind": "Deployment", "namespace": "multicluster-engine"}]),
    ]
    filtered = filter_acm_applications(apps)
    names = [a["metadata"]["name"] for a in filtered]
    assert names == ["acm-app", "mce-app"]
    assert filtered[0]["acm_resource_count"] == 1
    assert filtered[0]["namespace"] == "argocd"
    assert filtered[0]["name"] == "acm-app"


def test_empty_list_returns_empty():
    assert filter_acm_applications([]) == []


def test_app_with_no_status_resources_is_excluded():
    apps = [_app("no-status", [])]
    assert filter_acm_applications(apps) == []


def test_autosync_app_with_no_status_resources_is_blocked():
    app = _app("unknown-impact", [])
    app["spec"] = {"syncPolicy": {"automated": {"prune": True}}}

    blockers = find_argocd_pause_blockers([app])

    assert len(blockers) == 1
    assert blockers[0]["reason"] == PAUSE_BLOCK_REASON_UNKNOWN_ACM_IMPACT
    assert "cannot determine whether it touches ACM" in blockers[0]["message"]


def test_autosync_app_with_stale_status_resources_is_blocked():
    app = _app("stale-impact", [{"kind": "Deployment", "namespace": "default"}])
    app["metadata"]["generation"] = 7
    app["spec"] = {"syncPolicy": {"automated": {"prune": True}}}
    app["status"]["observedGeneration"] = 6

    blockers = find_argocd_pause_blockers([app])

    assert len(blockers) == 1
    assert blockers[0]["reason"] == PAUSE_BLOCK_REASON_UNKNOWN_ACM_IMPACT


def test_applicationset_owned_acm_app_is_blocked():
    app = _app(
        "child-app",
        [{"kind": "BackupSchedule", "namespace": "open-cluster-management-backup"}],
    )
    app["metadata"]["ownerReferences"] = [{"kind": "ApplicationSet", "name": "parent-set"}]
    app["spec"] = {"syncPolicy": {"automated": {"selfHeal": True}}}

    blockers = find_argocd_pause_blockers([app])

    assert len(blockers) == 1
    assert blockers[0]["reason"] == PAUSE_BLOCK_REASON_APPLICATIONSET_MANAGED
    assert "parent-set" in blockers[0]["message"]
    assert "pause/update the ApplicationSet" in blockers[0]["message"]


def test_non_acm_app_is_excluded():
    assert (
        is_acm_touching_application(
            {
                "metadata": {"name": "frontend"},
                "status": {"resources": [{"kind": "Deployment", "namespace": "web"}]},
            }
        )
        is False
    )


def test_policy_kind_is_acm_touching():
    """Policy kind (newly added) should be recognized as ACM-touching."""
    assert (
        is_acm_touching_application(
            {
                "metadata": {"name": "policy-app"},
                "status": {"resources": [{"kind": "Policy", "namespace": "default"}]},
            }
        )
        is True
    )


def test_placement_binding_kind_is_acm_touching():
    """PlacementBinding kind (newly added) should be recognized as ACM-touching."""
    assert (
        is_acm_touching_application(
            {
                "metadata": {"name": "placement-app"},
                "status": {"resources": [{"kind": "PlacementBinding", "namespace": "default"}]},
            }
        )
        is True
    )


def test_has_applicationset_owner_true():
    """App owned by an ApplicationSet should be detected."""
    app = {
        "metadata": {
            "name": "my-app",
            "ownerReferences": [{"kind": "ApplicationSet", "name": "my-appset"}],
        },
    }
    assert has_applicationset_owner(app) is True


def test_has_applicationset_owner_false_no_refs():
    """App without ownerReferences returns False."""
    assert has_applicationset_owner({"metadata": {"name": "solo-app"}}) is False


def test_has_applicationset_owner_false_other_owner():
    """App owned by something other than ApplicationSet returns False."""
    app = {
        "metadata": {
            "name": "my-app",
            "ownerReferences": [{"kind": "Deployment", "name": "my-deploy"}],
        },
    }
    assert has_applicationset_owner(app) is False
