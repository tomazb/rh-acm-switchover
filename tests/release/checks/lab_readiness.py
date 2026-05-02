from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class ReadinessResult:
    status: str
    assertions: list[dict]


def _assertion(name: str, passed: bool, message: str) -> dict:
    return {
        "capability": "lab-readiness",
        "name": name,
        "status": "passed" if passed else "failed",
        "message": message,
    }


def _mapping(value: object) -> Mapping[str, object]:
    if isinstance(value, Mapping):
        return value
    return {}


def _int_or_zero(value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return 0


def assert_lab_readiness(*, fingerprint: dict, require_argocd: bool, require_backup_storage: bool) -> ReadinessResult:
    assertions: list[dict] = []
    hubs = _mapping(fingerprint.get("hubs"))
    for role in ("primary", "secondary"):
        hub = _mapping(hubs.get(role))
        assertions.append(
            _assertion(
                f"{role}-acm-version",
                hub.get("acm_version") not in (None, "unknown"),
                "ACM version discovered",
            )
        )
        if require_argocd:
            argocd = _mapping(hub.get("argocd"))
            assertions.append(
                _assertion(
                    f"{role}-argocd-present",
                    bool(argocd.get("present")),
                    f"Argo CD present on {role}",
                )
            )
        if require_backup_storage:
            bsl = _mapping(hub.get("backup_storage_location"))
            assertions.append(
                _assertion(
                    f"{role}-backup-storage",
                    bool(bsl.get("present")) and bsl.get("health") in {"Available", "Ready"},
                    "Backup storage is acceptable",
                )
            )
    assertions.append(
        _assertion(
            "managed-clusters-present",
            _int_or_zero(_mapping(fingerprint.get("managed_clusters")).get("observed_active_count")) > 0,
            "Managed clusters observed on active hub",
        )
    )
    return ReadinessResult(
        status=("passed" if all(item["status"] == "passed" for item in assertions) else "failed"),
        assertions=assertions,
    )
