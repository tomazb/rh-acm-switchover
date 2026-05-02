from datetime import datetime

from tests.release.baseline.discovery import HubFacts
from tests.release.baseline.fingerprint import build_environment_fingerprint


def hub(context: str, role: str) -> HubFacts:
    return HubFacts(
        context=context,
        acm_namespace="open-cluster-management",
        acm_version="2.12.0",
        hub_role=role,
        backup_schedule={
            "present": role == "primary",
            "name": "acm-backup",
            "paused": False,
        },
        restore={
            "present": role != "primary",
            "name": "restore",
            "phase": "Finished",
            "sync_restore_enabled": True,
        },
        managed_cluster_names=("cluster-a", "cluster-b"),
        observability={"present": True, "status": "Ready"},
        argocd={
            "present": True,
            "namespaces": ("openshift-gitops",),
            "application_count": 1,
            "fixture_application_count": 1,
        },
    )


def test_fingerprint_contains_stable_lab_contract_fields() -> None:
    fingerprint = build_environment_fingerprint(
        primary=hub("primary", "primary"),
        secondary=hub("secondary", "secondary"),
        expected_names=("cluster-a", "cluster-b"),
        expected_count=None,
        lab_readiness_status="passed",
    )

    assert fingerprint["hubs"]["primary"]["context"] == "primary"
    assert fingerprint["managed_clusters"]["expectation_type"] == "names"
    assert fingerprint["managed_clusters"]["observed_active_names"] == [
        "cluster-a",
        "cluster-b",
    ]
    assert fingerprint["lab_readiness"]["status"] == "passed"
    assert datetime.fromisoformat(fingerprint["generated_at"])


def test_fingerprint_is_deterministic_for_same_inputs() -> None:
    first = build_environment_fingerprint(
        primary=hub("primary", "primary"),
        secondary=hub("secondary", "secondary"),
        expected_names=("cluster-a", "cluster-b"),
        expected_count=None,
        lab_readiness_status="passed",
    )
    second = build_environment_fingerprint(
        primary=hub("primary", "primary"),
        secondary=hub("secondary", "secondary"),
        expected_names=("cluster-a", "cluster-b"),
        expected_count=None,
        lab_readiness_status="passed",
    )

    assert "generated_at" in first
    assert "generated_at" in second
    assert {k: v for k, v in first.items() if k != "generated_at"} == {
        k: v for k, v in second.items() if k != "generated_at"
    }


def test_fingerprint_does_not_select_active_hub_when_none_is_primary() -> None:
    fingerprint = build_environment_fingerprint(
        primary=hub("primary", "standby"),
        secondary=hub("secondary", "secondary"),
        expected_names=("cluster-a", "cluster-b"),
        expected_count=None,
        lab_readiness_status="failed",
    )

    assert fingerprint["managed_clusters"]["observed_active_names"] == []
    assert fingerprint["managed_clusters"]["observed_active_count"] == 0
