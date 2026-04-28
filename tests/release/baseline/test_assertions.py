from tests.release.baseline.assertions import assert_baseline


def test_baseline_passes_when_initial_primary_matches() -> None:
    result = assert_baseline(
        fingerprint={
            "hubs": {
                "primary": {
                    "hub_role": "primary",
                    "backup_schedule": {"present": True},
                },
                "secondary": {
                    "hub_role": "secondary",
                    "restore": {"present": True},
                },
            },
            "managed_clusters": {
                "expectation_type": "count",
                "expected_count": 2,
                "observed_active_count": 2,
            },
        },
        initial_primary="primary",
    )

    assert result.status == "passed"


def test_baseline_fails_wrong_primary_role() -> None:
    result = assert_baseline(
        fingerprint={
            "hubs": {
                "primary": {
                    "hub_role": "secondary",
                    "backup_schedule": {"present": False},
                },
                "secondary": {
                    "hub_role": "primary",
                    "restore": {"present": False},
                },
            },
            "managed_clusters": {
                "expectation_type": "count",
                "expected_count": 2,
                "observed_active_count": 2,
            },
        },
        initial_primary="primary",
    )

    assert result.status == "failed"
    assert any(item["name"] == "initial-primary-role" for item in result.assertions)


def test_baseline_fails_without_backup_and_restore_evidence() -> None:
    result = assert_baseline(
        fingerprint={
            "hubs": {
                "primary": {
                    "hub_role": "primary",
                    "backup_schedule": {"present": False},
                },
                "secondary": {
                    "hub_role": "secondary",
                    "restore": {"present": False},
                },
            },
            "managed_clusters": {
                "expectation_type": "count",
                "expected_count": 2,
                "observed_active_count": 2,
            },
        },
        initial_primary="primary",
    )

    assert result.status == "failed"
    assert any(
        item["name"] == "initial-primary-backup-schedule" for item in result.assertions
    )
    assert any(item["name"] == "secondary-restore" for item in result.assertions)


def test_baseline_returns_failed_assertions_for_malformed_fingerprint() -> None:
    result = assert_baseline(
        fingerprint={"managed_clusters": {}},
        initial_primary="primary",
    )

    assert result.status == "failed"
    assert any(item["name"] == "initial-primary-role" for item in result.assertions)


def test_baseline_fails_when_count_expectation_fields_are_missing() -> None:
    result = assert_baseline(
        fingerprint={
            "hubs": {
                "primary": {
                    "hub_role": "primary",
                    "backup_schedule": {"present": True},
                },
                "secondary": {
                    "hub_role": "secondary",
                    "restore": {"present": True},
                },
            },
            "managed_clusters": {
                "expectation_type": "count",
            },
        },
        initial_primary="primary",
    )

    assert result.status == "failed"
    assert any(item["name"] == "managed-cluster-count" for item in result.assertions)
