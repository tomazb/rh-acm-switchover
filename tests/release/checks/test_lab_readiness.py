from tests.release.checks.lab_readiness import assert_lab_readiness


def test_lab_readiness_passes_when_required_facts_exist() -> None:
    result = assert_lab_readiness(
        fingerprint={
            "hubs": {
                "primary": {
                    "acm_version": "2.12.0",
                    "argocd": {"present": True},
                    "backup_storage_location": {
                        "present": True,
                        "health": "Available",
                    },
                },
                "secondary": {
                    "acm_version": "2.12.0",
                    "argocd": {"present": True},
                    "backup_storage_location": {
                        "present": True,
                        "health": "Available",
                    },
                },
            },
            "managed_clusters": {"observed_active_count": 2},
        },
        require_argocd=True,
        require_backup_storage=True,
    )

    assert result.status == "passed"
    assert result.assertions[0]["status"] == "passed"


def test_lab_readiness_fails_missing_argocd() -> None:
    result = assert_lab_readiness(
        fingerprint={
            "hubs": {
                "primary": {
                    "acm_version": "2.12.0",
                    "argocd": {"present": False},
                    "backup_storage_location": {
                        "present": True,
                        "health": "Available",
                    },
                },
                "secondary": {
                    "acm_version": "2.12.0",
                    "argocd": {"present": True},
                    "backup_storage_location": {
                        "present": True,
                        "health": "Available",
                    },
                },
            },
            "managed_clusters": {"observed_active_count": 2},
        },
        require_argocd=True,
        require_backup_storage=True,
    )

    assert result.status == "failed"
    assert any(item["name"] == "primary-argocd-present" for item in result.assertions)


def test_lab_readiness_returns_failed_assertions_for_malformed_fingerprint() -> None:
    result = assert_lab_readiness(
        fingerprint={},
        require_argocd=True,
        require_backup_storage=True,
    )

    assert result.status == "failed"
    assert any(item["name"] == "primary-acm-version" for item in result.assertions)
    assert any(item["name"] == "managed-clusters-present" for item in result.assertions)


def test_lab_readiness_handles_malformed_nested_argocd_payload() -> None:
    result = assert_lab_readiness(
        fingerprint={
            "hubs": {
                "primary": {
                    "acm_version": "2.12.0",
                    "argocd": "broken",
                    "backup_storage_location": {
                        "present": True,
                        "health": "Available",
                    },
                },
                "secondary": {
                    "acm_version": "2.12.0",
                    "argocd": {"present": True},
                    "backup_storage_location": {
                        "present": True,
                        "health": "Available",
                    },
                },
            },
            "managed_clusters": {"observed_active_count": 1},
        },
        require_argocd=True,
        require_backup_storage=True,
    )

    assert result.status == "failed"
    assert any(item["name"] == "primary-argocd-present" for item in result.assertions)


def test_lab_readiness_handles_none_managed_cluster_count() -> None:
    result = assert_lab_readiness(
        fingerprint={
            "hubs": {
                "primary": {"acm_version": "2.12.0", "argocd": {"present": True}},
                "secondary": {"acm_version": "2.12.0", "argocd": {"present": True}},
            },
            "managed_clusters": {"observed_active_count": None},
        },
        require_argocd=True,
        require_backup_storage=False,
    )

    assert result.status == "failed"
    assert any(item["name"] == "managed-clusters-present" for item in result.assertions)


def test_lab_readiness_handles_boolean_managed_cluster_count() -> None:
    result = assert_lab_readiness(
        fingerprint={
            "hubs": {
                "primary": {"acm_version": "2.12.0", "argocd": {"present": True}},
                "secondary": {"acm_version": "2.12.0", "argocd": {"present": True}},
            },
            "managed_clusters": {"observed_active_count": True},
        },
        require_argocd=True,
        require_backup_storage=False,
    )

    assert result.status == "failed"
    assert any(item["name"] == "managed-clusters-present" for item in result.assertions)
