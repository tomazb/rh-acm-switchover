"""Unit tests for lib/argocd.py.

Tests cover Argo CD discovery, ACM-touching app detection,
pause/resume autosync logic, and run_id handling.
"""

from unittest.mock import MagicMock

import pytest
from kubernetes.client.rest import ApiException

from lib import argocd as argocd_lib


@pytest.mark.unit
class TestRunIdOrNew:
    """Test run_id_or_new."""

    def test_returns_existing_when_provided(self):
        assert argocd_lib.run_id_or_new("existing-id") == "existing-id"

    def test_returns_new_when_none(self):
        out = argocd_lib.run_id_or_new(None)
        assert out is not None
        assert len(out) == 12
        assert out.isalnum()

    def test_returns_new_when_empty_string(self):
        out = argocd_lib.run_id_or_new("")
        assert out is not None
        assert len(out) == 12


@pytest.mark.unit
class TestFindAcmTouchingApps:
    """Test find_acm_touching_apps filtering."""

    def test_includes_app_with_acm_kind_in_status_resources(self):
        apps = [
            {
                "metadata": {"namespace": "openshift-gitops", "name": "acm-app"},
                "status": {
                    "resources": [
                        {
                            "kind": "BackupSchedule",
                            "namespace": "open-cluster-management-backup",
                            "name": "x",
                        },
                    ]
                },
            }
        ]
        result = argocd_lib.find_acm_touching_apps(apps)
        assert len(result) == 1
        assert result[0].namespace == "openshift-gitops"
        assert result[0].name == "acm-app"
        assert result[0].resource_count == 1

    def test_includes_app_with_acm_namespace_in_status_resources(self):
        apps = [
            {
                "metadata": {"namespace": "argocd", "name": "acm-ns"},
                "status": {
                    "resources": [
                        {
                            "kind": "ConfigMap",
                            "namespace": "open-cluster-management",
                            "name": "y",
                        },
                    ]
                },
            }
        ]
        result = argocd_lib.find_acm_touching_apps(apps)
        assert len(result) == 1
        assert result[0].resource_count == 1

    def test_excludes_app_with_no_acm_resources(self):
        apps = [
            {
                "metadata": {"namespace": "argocd", "name": "other"},
                "status": {
                    "resources": [
                        {"kind": "ConfigMap", "namespace": "default", "name": "z"},
                    ]
                },
            }
        ]
        result = argocd_lib.find_acm_touching_apps(apps)
        assert len(result) == 0

    def test_excludes_app_with_no_status_resources(self):
        apps = [
            {"metadata": {"namespace": "argocd", "name": "no-status"}, "status": {}}
        ]
        result = argocd_lib.find_acm_touching_apps(apps)
        assert len(result) == 0

    def test_logs_debug_for_empty_resources(self, caplog):
        """Apps with empty status.resources should emit a debug log."""
        import logging

        apps = [
            {
                "metadata": {"namespace": "argocd", "name": "empty-res"},
                "status": {"resources": []},
            }
        ]
        with caplog.at_level(logging.DEBUG, logger="acm_switchover"):
            result = argocd_lib.find_acm_touching_apps(apps)
        assert len(result) == 0
        assert any(
            "empty-res" in msg and "no status.resources" in msg
            for msg in caplog.messages
        )

    def test_excludes_app_with_missing_status(self):
        apps = [{"metadata": {"namespace": "argocd", "name": "x"}}]
        result = argocd_lib.find_acm_touching_apps(apps)
        assert len(result) == 0

    def test_cluster_scoped_acm_kind_matches(self):
        apps = [
            {
                "metadata": {"namespace": "argocd", "name": "mco"},
                "status": {
                    "resources": [
                        {"kind": "MultiClusterObservability", "name": "observability"},
                    ]
                },
            }
        ]
        result = argocd_lib.find_acm_touching_apps(apps)
        assert len(result) == 1
        assert result[0].resource_count == 1

    def test_includes_app_with_acm_sub_namespace(self):
        """Regression: open-cluster-management-* sub-namespaces must match (mirrors lib-common.sh)."""
        for sub_ns in ("open-cluster-management-hub", "open-cluster-management-addon"):
            apps = [
                {
                    "metadata": {"namespace": "argocd", "name": "hub-app"},
                    "status": {
                        "resources": [
                            {"kind": "ConfigMap", "namespace": sub_ns, "name": "z"},
                        ]
                    },
                }
            ]
            result = argocd_lib.find_acm_touching_apps(apps)
            assert len(result) == 1, f"Expected match for sub-namespace {sub_ns!r}"

    def test_includes_app_with_local_cluster_namespace_by_design(self):
        apps = [
            {
                "metadata": {"namespace": "argocd", "name": "local-cluster-app"},
                "status": {
                    "resources": [
                        {
                            "kind": "ConfigMap",
                            "namespace": "local-cluster",
                            "name": "cfg",
                        },
                    ]
                },
            }
        ]

        result = argocd_lib.find_acm_touching_apps(apps)

        assert len(result) == 1
        assert result[0].name == "local-cluster-app"


@pytest.mark.unit
class TestFindArgocdPauseBlockers:
    """Test unsafe Argo CD Application cases that must block managed pause."""

    def test_blocks_autosync_app_with_empty_resources(self):
        apps = [
            {
                "metadata": {"namespace": "argocd", "name": "unknown-impact"},
                "spec": {"syncPolicy": {"automated": {"prune": True}}},
                "status": {"resources": []},
            }
        ]

        blockers = argocd_lib.find_argocd_pause_blockers(apps)

        assert len(blockers) == 1
        assert blockers[0].namespace == "argocd"
        assert blockers[0].name == "unknown-impact"
        assert blockers[0].reason == argocd_lib.PAUSE_BLOCK_REASON_UNKNOWN_ACM_IMPACT
        assert "cannot determine whether it touches ACM" in blockers[0].message

    def test_blocks_autosync_app_with_stale_resources(self):
        apps = [
            {
                "metadata": {
                    "namespace": "argocd",
                    "name": "stale-impact",
                    "generation": 5,
                },
                "spec": {"syncPolicy": {"automated": {"prune": True}}},
                "status": {
                    "observedGeneration": 4,
                    "resources": [{"kind": "Deployment", "namespace": "default"}],
                },
            }
        ]

        blockers = argocd_lib.find_argocd_pause_blockers(apps)

        assert len(blockers) == 1
        assert blockers[0].reason == argocd_lib.PAUSE_BLOCK_REASON_UNKNOWN_ACM_IMPACT
        assert "status.resources is empty or stale" in blockers[0].message

    def test_blocks_applicationset_owned_acm_app(self):
        apps = [
            {
                "metadata": {
                    "namespace": "argocd",
                    "name": "child-app",
                    "ownerReferences": [
                        {"kind": "ApplicationSet", "name": "parent-set"}
                    ],
                },
                "spec": {"syncPolicy": {"automated": {"selfHeal": True}}},
                "status": {
                    "resources": [
                        {
                            "kind": "BackupSchedule",
                            "namespace": "open-cluster-management-backup",
                        }
                    ]
                },
            }
        ]

        blockers = argocd_lib.find_argocd_pause_blockers(apps)

        assert len(blockers) == 1
        assert (
            blockers[0].reason == argocd_lib.PAUSE_BLOCK_REASON_APPLICATIONSET_MANAGED
        )
        assert "parent-set" in blockers[0].message
        assert "pause/update the ApplicationSet" in blockers[0].message

    @pytest.mark.xfail(
        strict=True,
        reason="Known gap: disabled ApplicationSet-managed ACM apps are not blocked",
    )
    def test_blocks_applicationset_owned_acm_app_even_when_autosync_is_already_disabled(
        self,
    ):
        """ApplicationSet-managed ACM apps should remain blockers even if auto-sync is already off."""
        apps = [
            {
                "metadata": {
                    "namespace": "argocd",
                    "name": "child-app",
                    "ownerReferences": [
                        {"kind": "ApplicationSet", "name": "parent-set"}
                    ],
                },
                "spec": {"syncPolicy": {"automated": None}},
                "status": {
                    "resources": [
                        {
                            "kind": "BackupSchedule",
                            "namespace": "open-cluster-management-backup",
                        }
                    ]
                },
            }
        ]

        blockers = argocd_lib.find_argocd_pause_blockers(apps)

        assert len(blockers) == 1
        assert blockers[0].namespace == "argocd"
        assert blockers[0].name == "child-app"
        assert (
            blockers[0].reason == argocd_lib.PAUSE_BLOCK_REASON_APPLICATIONSET_MANAGED
        )

    def test_does_not_block_disabled_autosync_with_empty_resources(self):
        apps = [
            {
                "metadata": {"namespace": "argocd", "name": "manual-app"},
                "spec": {"syncPolicy": {}},
                "status": {"resources": []},
            }
        ]

        assert argocd_lib.find_argocd_pause_blockers(apps) == []

    def test_does_not_block_null_automated_autosync(self):
        apps = [
            {
                "metadata": {"namespace": "argocd", "name": "null-auto-app"},
                "spec": {"syncPolicy": {"automated": None}},
                "status": {"resources": []},
            }
        ]

        assert argocd_lib.is_autosync_enabled(apps[0]) is False
        assert argocd_lib.find_argocd_pause_blockers(apps) == []


@pytest.mark.unit
class TestPauseAutosync:
    """Test pause_autosync behavior."""

    def test_returns_patched_false_when_no_automated(self):
        client = MagicMock()
        app = {
            "metadata": {"namespace": "argocd", "name": "app"},
            "spec": {"syncPolicy": {"allowEmpty": True}},
        }
        result = argocd_lib.pause_autosync(client, app, "run-1")
        assert result.patched is False
        assert result.skip_reason == argocd_lib.PAUSE_SKIP_REASON_AUTOSYNC_DISABLED
        assert result.error is None
        assert result.namespace == "argocd"
        assert result.name == "app"
        client.patch_custom_resource.assert_not_called()

    def test_patches_and_returns_patched_true_when_has_automated(self):
        client = MagicMock()
        client.patch_custom_resource.return_value = {
            "metadata": {"resourceVersion": "1001"}
        }
        client.get_custom_resource.return_value = {
            "metadata": {"namespace": "argocd", "name": "app"},
            "spec": {"syncPolicy": {"syncOptions": []}},
        }
        app = {
            "metadata": {
                "namespace": "argocd",
                "name": "app",
                "resourceVersion": "1000",
            },
            "spec": {"syncPolicy": {"automated": {"prune": True}, "syncOptions": []}},
        }
        result = argocd_lib.pause_autosync(client, app, "run-1")
        assert result.patched is True
        assert result.original_sync_policy == {
            "automated": {"prune": True},
            "syncOptions": [],
        }
        client.patch_custom_resource.assert_called_once()
        call_kw = client.patch_custom_resource.call_args[1]
        assert call_kw["namespace"] == "argocd"
        assert call_kw["name"] == "app"
        patch = call_kw["patch"]
        assert (
            patch["metadata"]["annotations"][argocd_lib.ARGOCD_PAUSED_BY_ANNOTATION]
            == "run-1"
        )
        assert patch["spec"]["syncPolicy"]["automated"] is None
        assert patch["spec"]["syncPolicy"].get("syncOptions") == []
        client.get_custom_resource.assert_called_once()

    def test_patch_failure_rereads_ground_truth_when_pause_was_applied(self):
        """A patch exception after server-side apply must be classified from the re-read state."""
        client = MagicMock()
        client.patch_custom_resource.side_effect = ApiException(
            status=500, reason="Internal Server Error"
        )
        client.get_custom_resource.return_value = {
            "metadata": {
                "namespace": "argocd",
                "name": "app",
                "resourceVersion": "1002",
                "annotations": {argocd_lib.ARGOCD_PAUSED_BY_ANNOTATION: "run-1"},
            },
            "spec": {"syncPolicy": {"syncOptions": []}},
        }
        app = {
            "metadata": {"namespace": "argocd", "name": "app"},
            "spec": {"syncPolicy": {"automated": {"prune": True}, "syncOptions": []}},
        }

        result = argocd_lib.pause_autosync(client, app, "run-1")

        assert result.patched is True
        assert result.patch_applied is True
        assert result.error is None
        client.get_custom_resource.assert_called_once()

    def test_patch_failure_treats_automated_null_ground_truth_as_paused(self):
        client = MagicMock()
        client.patch_custom_resource.side_effect = ApiException(
            status=500, reason="Internal Server Error"
        )
        client.get_custom_resource.return_value = {
            "metadata": {
                "namespace": "argocd",
                "name": "app",
                "resourceVersion": "1002",
                "annotations": {argocd_lib.ARGOCD_PAUSED_BY_ANNOTATION: "run-1"},
            },
            "spec": {"syncPolicy": {"automated": None, "syncOptions": []}},
        }
        app = {
            "metadata": {"namespace": "argocd", "name": "app"},
            "spec": {"syncPolicy": {"automated": {"prune": True}, "syncOptions": []}},
        }

        result = argocd_lib.pause_autosync(client, app, "run-1")

        assert result.patched is True
        assert result.patch_applied is True
        assert result.error is None

    def test_patch_failure_rereads_ground_truth_when_pause_was_not_applied(self):
        """A patch exception with unchanged server state must report an unapplied pause."""
        client = MagicMock()
        client.patch_custom_resource.side_effect = ApiException(
            status=403, reason="Forbidden"
        )
        client.get_custom_resource.return_value = {
            "metadata": {
                "namespace": "argocd",
                "name": "app",
                "resourceVersion": "1002",
                "annotations": {},
            },
            "spec": {"syncPolicy": {"automated": {"prune": True}}},
        }
        app = {
            "metadata": {"namespace": "argocd", "name": "app"},
            "spec": {"syncPolicy": {"automated": {"prune": True}}},
        }

        result = argocd_lib.pause_autosync(client, app, "run-1")

        assert result.patched is False
        assert result.patch_applied is False
        assert result.error is not None
        assert "403 Forbidden" in result.error
        assert "not applied" in result.error
        assert result.original_sync_policy == {"automated": {"prune": True}}
        client.get_custom_resource.assert_called_once()

    def test_patch_failure_reports_unknown_when_ground_truth_reread_fails(self):
        """A patch exception plus failed re-read must report unknown state, not definitely unpatched."""
        client = MagicMock()
        client.patch_custom_resource.side_effect = ApiException(
            status=500, reason="Internal Server Error"
        )
        client.get_custom_resource.side_effect = ApiException(
            status=503, reason="Service Unavailable"
        )
        app = {
            "metadata": {"namespace": "argocd", "name": "app"},
            "spec": {"syncPolicy": {"automated": {"prune": True}}},
        }

        result = argocd_lib.pause_autosync(client, app, "run-1")

        assert result.patched is False
        assert result.patch_applied is None
        assert result.error is not None
        assert "patch state unknown" in result.error
        assert "500 Internal Server Error" in result.error
        assert "503 Service Unavailable" in result.error
        client.get_custom_resource.assert_called_once()

    def test_returns_patched_false_when_automated_is_null(self):
        client = MagicMock()
        app = {
            "metadata": {"namespace": "argocd", "name": "app"},
            "spec": {"syncPolicy": {"automated": None}},
        }

        result = argocd_lib.pause_autosync(client, app, "run-1")

        assert result.patched is False
        assert result.skip_reason == argocd_lib.PAUSE_SKIP_REASON_AUTOSYNC_DISABLED
        client.patch_custom_resource.assert_not_called()

    def test_skip_reason_is_autosync_disabled_not_annotation_when_sync_disabled_and_foreign_annotation_present(
        self,
    ):
        """Skip is determined by disabled auto-sync alone — not by the paused-by annotation.

        If an app was paused by a previous switchover run (has a foreign paused-by annotation)
        and now has auto-sync disabled, pause_autosync must return PAUSE_SKIP_REASON_AUTOSYNC_DISABLED
        and must not call patch_custom_resource, leaving the existing annotation untouched.

        This guards against a refactoring that moves the annotation check before the auto-sync
        check, which would produce the wrong skip reason and could incorrectly overwrite the
        foreign annotation with the new run_id.
        """
        client = MagicMock()
        app = {
            "metadata": {
                "namespace": "argocd",
                "name": "app",
                "annotations": {argocd_lib.ARGOCD_PAUSED_BY_ANNOTATION: "other-run"},
            },
            "spec": {"syncPolicy": {}},
        }

        result = argocd_lib.pause_autosync(client, app, "run-new")

        assert result.patched is False
        assert result.skip_reason == argocd_lib.PAUSE_SKIP_REASON_AUTOSYNC_DISABLED
        client.patch_custom_resource.assert_not_called()

    def test_patches_when_automated_is_empty_map(self):
        client = MagicMock()
        client.patch_custom_resource.return_value = {
            "metadata": {"resourceVersion": "1001"}
        }
        client.get_custom_resource.return_value = {
            "metadata": {"namespace": "argocd", "name": "app"},
            "spec": {"syncPolicy": {}},
        }
        app = {
            "metadata": {
                "namespace": "argocd",
                "name": "app",
                "resourceVersion": "1000",
            },
            "spec": {"syncPolicy": {"automated": {}}},
        }
        result = argocd_lib.pause_autosync(client, app, "run-1")
        assert result.patched is True
        client.patch_custom_resource.assert_called_once()
        patch = client.patch_custom_resource.call_args[1]["patch"]
        assert patch["spec"]["syncPolicy"]["automated"] is None

    def test_blocks_applicationset_child_without_patching(self):
        client = MagicMock()
        app = {
            "metadata": {
                "namespace": "argocd",
                "name": "child-app",
                "ownerReferences": [{"kind": "ApplicationSet", "name": "parent-set"}],
            },
            "spec": {"syncPolicy": {"automated": {"prune": True}}},
        }

        result = argocd_lib.pause_autosync(client, app, "run-1")

        assert result.patched is False
        assert result.error is not None
        assert "ApplicationSet parent-set" in result.error
        assert "pause/update the ApplicationSet" in result.error
        client.patch_custom_resource.assert_not_called()

    @pytest.mark.xfail(
        strict=True,
        reason="Known gap: verification race still reports patch_applied=True",
    )
    def test_pause_autosync_returns_patch_applied_false_when_controller_reenables_sync_after_patch(
        self,
    ):
        """If re-read shows auto-sync re-enabled, pause should be reported as not applied."""
        client = MagicMock()
        client.patch_custom_resource.return_value = {
            "metadata": {"resourceVersion": "1001"}
        }
        client.get_custom_resource.return_value = {
            "metadata": {"namespace": "argocd", "name": "app"},
            "spec": {"syncPolicy": {"automated": {"prune": True}}},
        }
        app = {
            "metadata": {"namespace": "argocd", "name": "app"},
            "spec": {"syncPolicy": {"automated": {"prune": True}}},
        }

        result = argocd_lib.pause_autosync(client, app, "run-1")

        assert result.patched is False
        assert result.patch_applied is False
        assert result.error is not None
        assert "auto-sync remains enabled after pause" in result.error
        client.patch_custom_resource.assert_called_once()
        client.get_custom_resource.assert_called_once()

    def test_dry_run_pause_supports_keyword_arguments(self):
        client = MagicMock()
        client.dry_run = True
        app = {
            "metadata": {"namespace": "argocd", "name": "app"},
            "spec": {"syncPolicy": {"automated": {"prune": True}}},
        }

        result = argocd_lib.pause_autosync(client, app=app, run_id="run-1")

        assert result.namespace == "argocd"
        assert result.name == "app"
        assert result.patched is True
        client.patch_custom_resource.assert_not_called()


@pytest.mark.unit
class TestResumeAutosync:
    """Test resume_autosync behavior."""

    def test_skip_when_marker_mismatch(self):
        client = MagicMock()
        client.get_custom_resource.return_value = {
            "metadata": {
                "resourceVersion": "500",
                "annotations": {argocd_lib.ARGOCD_PAUSED_BY_ANNOTATION: "other-run"},
            },
        }
        result = argocd_lib.resume_autosync(
            client, "argocd", "app", {"automated": {}}, "run-1"
        )
        assert result.restored is False
        assert result.skip_reason == argocd_lib.RESUME_SKIP_REASON_MARKER_MISMATCH
        client.patch_custom_resource.assert_not_called()

    def test_skip_when_marker_missing(self):
        client = MagicMock()
        client.get_custom_resource.return_value = {
            "metadata": {"resourceVersion": "500"}
        }
        result = argocd_lib.resume_autosync(
            client, "argocd", "app", {"automated": {}}, "run-1"
        )
        assert result.restored is False
        assert result.skip_reason == argocd_lib.RESUME_SKIP_REASON_MARKER_MISSING
        client.patch_custom_resource.assert_not_called()

    def test_restores_when_marker_matches(self):
        client = MagicMock()
        client.get_custom_resource.return_value = {
            "metadata": {
                "resourceVersion": "500",
                "annotations": {argocd_lib.ARGOCD_PAUSED_BY_ANNOTATION: "run-1"},
            },
        }
        client.patch_custom_resource.return_value = {
            "metadata": {"resourceVersion": "501"}
        }
        result = argocd_lib.resume_autosync(
            client, "argocd", "app", {"automated": {"prune": True}}, "run-1"
        )
        assert result.restored is True
        client.patch_custom_resource.assert_called_once()
        call_kw = client.patch_custom_resource.call_args[1]
        assert call_kw["patch"]["spec"]["syncPolicy"] == {"automated": {"prune": True}}
        assert (
            call_kw["patch"]["metadata"]["annotations"][
                argocd_lib.ARGOCD_PAUSED_BY_ANNOTATION
            ]
            is None
        )

    def test_skip_when_app_not_found(self):
        client = MagicMock()
        client.get_custom_resource.return_value = None
        result = argocd_lib.resume_autosync(
            client, "argocd", "missing", {"automated": {}}, "run-1"
        )
        assert result.restored is False
        client.patch_custom_resource.assert_not_called()

    def test_marker_mismatch_with_autosync_cleans_stale_marker(self):
        """When marker doesn't match but auto-sync is already enabled,
        the stale marker should be cleaned and result treated as noop."""
        client = MagicMock()
        client.get_custom_resource.return_value = {
            "metadata": {
                "resourceVersion": "500",
                "annotations": {argocd_lib.ARGOCD_PAUSED_BY_ANNOTATION: "old-run"},
            },
            "spec": {
                "syncPolicy": {
                    "automated": {"prune": True, "selfHeal": True},
                },
            },
        }
        result = argocd_lib.resume_autosync(
            client, "argocd", "app", {"automated": {}}, "run-1"
        )
        assert result.restored is False
        assert result.skip_reason == argocd_lib.RESUME_SKIP_REASON_MARKER_MISSING
        assert argocd_lib.is_resume_noop(result)
        # Should have patched to remove the stale marker
        client.patch_custom_resource.assert_called_once()
        patch_kw = client.patch_custom_resource.call_args[1]
        assert (
            patch_kw["patch"]["metadata"]["annotations"][
                argocd_lib.ARGOCD_PAUSED_BY_ANNOTATION
            ]
            is None
        )

    def test_marker_mismatch_with_autosync_logs_warning_when_stale_marker_cleanup_fails(
        self, caplog
    ):
        """Cleanup failure should warn and leave the already-resumed app in a noop state."""
        import logging

        client = MagicMock()
        client.get_custom_resource.return_value = {
            "metadata": {
                "resourceVersion": "500",
                "annotations": {argocd_lib.ARGOCD_PAUSED_BY_ANNOTATION: "old-run"},
            },
            "spec": {
                "syncPolicy": {
                    "automated": {"prune": True, "selfHeal": True},
                },
            },
        }
        client.patch_custom_resource.side_effect = ApiException(
            status=403, reason="Forbidden"
        )

        with caplog.at_level(logging.WARNING, logger="acm_switchover"):
            result = argocd_lib.resume_autosync(
                client, "argocd", "app", {"automated": {}}, "run-1"
            )

        assert result.restored is False
        assert result.skip_reason == argocd_lib.RESUME_SKIP_REASON_MARKER_MISSING
        assert argocd_lib.is_resume_noop(result) is True
        client.patch_custom_resource.assert_called_once()
        assert any(
            "Failed to clean stale marker on argocd/app" in msg
            for msg in caplog.messages
        )

    @pytest.mark.parametrize(
        ("status", "reason"),
        [(403, "Forbidden"), (500, "Internal Server Error")],
    )
    def test_resume_autosync_fetch_api_error_leaves_application_paused(
        self, status, reason
    ):
        """Fetch errors must not be treated as successful resume operations."""
        client = MagicMock()
        client.get_custom_resource.side_effect = ApiException(status=status, reason=reason)

        result = argocd_lib.resume_autosync(
            client, "argocd", "app", {"automated": {"prune": True}}, "run-1"
        )

        assert result.restored is False
        assert result.skip_reason == f"fetch error: {status}"
        client.patch_custom_resource.assert_not_called()

    def test_patch_exception_returns_skip_reason(self):
        client = MagicMock()
        client.get_custom_resource.return_value = {
            "metadata": {
                "resourceVersion": "500",
                "annotations": {argocd_lib.ARGOCD_PAUSED_BY_ANNOTATION: "run-1"},
            },
        }
        client.patch_custom_resource.side_effect = RuntimeError("boom")
        result = argocd_lib.resume_autosync(
            client, "argocd", "app", {"automated": {"prune": True}}, "run-1"
        )
        assert result.restored is False
        assert "patch failed" in (result.skip_reason or "").lower()

    def test_dry_run_resume_supports_keyword_arguments(self):
        client = MagicMock()
        client.dry_run = True

        result = argocd_lib.resume_autosync(
            client,
            namespace="argocd",
            name="app",
            original_sync_policy={"automated": {"prune": True}},
            run_id="run-1",
        )

        assert result.namespace == "argocd"
        assert result.name == "app"
        assert result.restored is True
        client.get_custom_resource.assert_not_called()

    def test_is_resume_noop_true_for_marker_missing(self):
        result = argocd_lib.ResumeResult(
            namespace="argocd",
            name="app",
            restored=False,
            skip_reason=argocd_lib.RESUME_SKIP_REASON_MARKER_MISSING,
        )
        assert argocd_lib.is_resume_noop(result) is True

    def test_is_resume_noop_false_for_marker_mismatch(self):
        result = argocd_lib.ResumeResult(
            namespace="argocd",
            name="app",
            restored=False,
            skip_reason=argocd_lib.RESUME_SKIP_REASON_MARKER_MISMATCH,
        )
        assert argocd_lib.is_resume_noop(result) is False

    def test_is_resume_noop_false_for_patch_failure(self):
        result = argocd_lib.ResumeResult(
            namespace="argocd",
            name="app",
            restored=False,
            skip_reason="patch failed: 403 Forbidden",
        )
        assert argocd_lib.is_resume_noop(result) is False

    def test_resume_recorded_applications_fails_unconfirmed_pause_entries(self):
        logger = MagicMock()
        summary = argocd_lib.resume_recorded_applications(
            paused_apps=[
                {
                    "hub": "primary",
                    "namespace": "argocd",
                    "name": "app",
                    "original_sync_policy": {"automated": {}},
                    "pause_applied": False,
                }
            ],
            run_id="run-1",
            primary=MagicMock(),
            secondary=MagicMock(),
            logger=logger,
        )

        assert summary.failed == 1
        assert summary.restored == 0
        logger.warning.assert_called_with(
            "  Skip %s/%s (pause state was recorded but not confirmed)", "argocd", "app"
        )


@pytest.mark.unit
class TestDetectArgocdInstallation:
    """Test detect_argocd_installation."""

    def test_none_when_app_crd_missing(self):
        client = MagicMock()
        client.get_custom_resource.side_effect = [None, None]
        result = argocd_lib.detect_argocd_installation(client)
        assert result.has_applications_crd is False
        assert result.install_type == "none"

    def test_vanilla_when_app_crd_only(self):
        client = MagicMock()
        client.get_custom_resource.side_effect = [
            {"metadata": {"name": "applications.argoproj.io"}},
            None,
        ]
        result = argocd_lib.detect_argocd_installation(client)
        assert result.has_applications_crd is True
        assert result.has_argocds_crd is False
        assert result.install_type == "vanilla"

    def test_operator_when_both_crds_and_instances(self):
        client = MagicMock()
        client.get_custom_resource.side_effect = [
            {"metadata": {"name": "applications.argoproj.io"}},
            {"metadata": {"name": "argocds.argoproj.io"}},
        ]
        client.list_custom_resources.return_value = [
            {"metadata": {"namespace": "openshift-gitops", "name": "openshift-gitops"}},
        ]
        result = argocd_lib.detect_argocd_installation(client)
        assert result.has_applications_crd is True
        assert result.has_argocds_crd is True
        assert result.install_type == "operator"
        assert len(result.argocd_instances) == 1
        assert result.argocd_instances[0]["namespace"] == "openshift-gitops"

    def test_argocds_crd_lookup_failure_preserves_application_crd_result(self):
        client = MagicMock()
        client.get_custom_resource.side_effect = [
            {"metadata": {"name": "applications.argoproj.io"}},
            ApiException(status=500, reason="Internal Server Error"),
        ]

        result = argocd_lib.detect_argocd_installation(client)

        assert result.has_applications_crd is True
        assert result.has_argocds_crd is False
        assert result.install_type == "unknown"

    def test_application_crd_lookup_failure_raises(self):
        client = MagicMock()
        client.get_custom_resource.side_effect = ApiException(
            status=403, reason="Forbidden"
        )

        with pytest.raises(ApiException):
            argocd_lib.detect_argocd_installation(client)

    def test_unknown_when_argocds_crd_presence_is_indeterminate(self, monkeypatch):
        """Indeterminate argocds CRD detection should surface an unknown install type."""
        client = MagicMock()

        def fake_get_crd_presence(_client, crd_name, *, required):
            if crd_name == "applications.argoproj.io":
                return True
            if crd_name == "argocds.argoproj.io":
                return None
            raise AssertionError(f"Unexpected CRD lookup: {crd_name}")

        monkeypatch.setattr(argocd_lib, "_get_crd_presence", fake_get_crd_presence)

        result = argocd_lib.detect_argocd_installation(client)

        assert result.has_applications_crd is True
        assert result.has_argocds_crd is False
        assert result.install_type == "unknown"
        assert result.argocd_instances == []
        client.list_custom_resources.assert_not_called()


@pytest.mark.unit
class TestListArgocdApplications:
    def test_cluster_wide_404_returns_empty(self):
        client = MagicMock()
        client.list_custom_resources.side_effect = ApiException(
            status=404, reason="Not Found"
        )

        assert argocd_lib.list_argocd_applications(client, namespaces=None) == []

    def test_cluster_wide_non_404_raises(self):
        client = MagicMock()
        client.list_custom_resources.side_effect = ApiException(
            status=403, reason="Forbidden"
        )

        with pytest.raises(ApiException):
            argocd_lib.list_argocd_applications(client, namespaces=None)

    def test_namespaced_listing_aggregates_results(self):
        client = MagicMock()
        client.list_custom_resources.side_effect = [
            [{"metadata": {"namespace": "argocd", "name": "app-1"}}],
            [{"metadata": {"namespace": "openshift-gitops", "name": "app-2"}}],
        ]

        apps = argocd_lib.list_argocd_applications(
            client, namespaces=["argocd", "openshift-gitops"]
        )

        assert [app["metadata"]["name"] for app in apps] == ["app-1", "app-2"]

    def test_namespaced_listing_skips_empty_namespace_entries(self):
        """Empty namespace strings should be ignored without suppressing valid results."""
        client = MagicMock()
        client.list_custom_resources.side_effect = [
            [{"metadata": {"namespace": "argocd", "name": "app-1"}}],
            [{"metadata": {"namespace": "test-ns", "name": "app-2"}}],
        ]

        apps = argocd_lib.list_argocd_applications(
            client, namespaces=["", "argocd", "", "test-ns"]
        )

        assert [app["metadata"]["name"] for app in apps] == ["app-1", "app-2"]
        assert [call.kwargs["namespace"] for call in client.list_custom_resources.call_args_list] == [
            "argocd",
            "test-ns",
        ]
