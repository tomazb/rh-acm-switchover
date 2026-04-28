from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class BaselineResult:
    status: str
    assertions: list[dict]


def _record(name: str, passed: bool, message: str) -> dict:
    return {
        "capability": "baseline",
        "name": name,
        "status": "passed" if passed else "failed",
        "message": message,
    }


def _mapping(value: object) -> Mapping[str, object]:
    if isinstance(value, Mapping):
        return value
    return {}


def _valid_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def assert_baseline(*, fingerprint: dict, initial_primary: str) -> BaselineResult:
    assertions = []
    expected_secondary = "secondary" if initial_primary == "primary" else "primary"
    hubs = _mapping(fingerprint.get("hubs"))
    primary_hub = _mapping(hubs.get(initial_primary))
    secondary_hub = _mapping(hubs.get(expected_secondary))

    assertions.append(
        _record(
            "initial-primary-role",
            primary_hub.get("hub_role") == "primary",
            f"{initial_primary} is primary",
        )
    )
    assertions.append(
        _record(
            "initial-primary-backup-schedule",
            bool(_mapping(primary_hub.get("backup_schedule")).get("present")),
            f"{initial_primary} has backup schedule evidence",
        )
    )
    assertions.append(
        _record(
            "secondary-role",
            secondary_hub.get("hub_role") in {"secondary", "standby"},
            f"{expected_secondary} is passive",
        )
    )
    assertions.append(
        _record(
            "secondary-restore",
            bool(_mapping(secondary_hub.get("restore")).get("present")),
            f"{expected_secondary} has restore evidence",
        )
    )

    managed = _mapping(fingerprint.get("managed_clusters"))
    expectation_type = managed.get("expectation_type")
    if expectation_type == "count":
        expected_count = managed.get("expected_count")
        observed_count = managed.get("observed_active_count")
        assertions.append(
            _record(
                "managed-cluster-count",
                _valid_int(expected_count)
                and _valid_int(observed_count)
                and observed_count == expected_count,
                "Managed cluster count matches profile",
            )
        )
    elif expectation_type == "names":
        expected_names = managed.get("expected_names")
        observed_names = managed.get("observed_active_names")
        assertions.append(
            _record(
                "managed-cluster-names",
                expected_names is not None
                and observed_names is not None
                and observed_names == expected_names,
                "Managed cluster names match profile",
            )
        )
    else:
        assertions.append(
            _record(
                "managed-cluster-expectation",
                False,
                "Managed cluster expectation contract is missing",
            )
        )

    return BaselineResult(
        status=(
            "passed"
            if all(item["status"] == "passed" for item in assertions)
            else "failed"
        ),
        assertions=assertions,
    )
