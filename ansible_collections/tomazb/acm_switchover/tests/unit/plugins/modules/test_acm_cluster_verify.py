"""Tests for the acm_cluster_verify collection module."""

from ansible_collections.tomazb.acm_switchover.plugins.module_utils.constants import (
    NO_MANAGED_CLUSTERS_PENDING_REASON,
)
from ansible_collections.tomazb.acm_switchover.plugins.modules.acm_cluster_verify import (
    main,
    summarize_cluster_group,
)


def _run_module(
    monkeypatch,
    *,
    cluster_status: list[dict] | None = None,
    min_managed_clusters: int = 1,
    expected_names: list[str] | None = None,
    allow_zero_managed_clusters: bool = False,
    check_mode: bool = False,
) -> dict:
    captured: dict = {}

    class FakeModule:
        def __init__(self, *args, **kwargs):
            self.params = {
                "cluster_status": cluster_status or [],
                "min_managed_clusters": min_managed_clusters,
                "expected_names": expected_names or [],
                "allow_zero_managed_clusters": allow_zero_managed_clusters,
            }
            self.check_mode = check_mode

        def exit_json(self, **kwargs):
            captured["exit"] = kwargs
            raise SystemExit(0)

        def fail_json(self, **kwargs):
            captured["fail"] = kwargs
            raise SystemExit(1)

    monkeypatch.setattr(
        "ansible_collections.tomazb.acm_switchover.plugins.modules.acm_cluster_verify.AnsibleModule",
        FakeModule,
    )

    try:
        main()
    except SystemExit:
        pass
    return captured


def test_cluster_group_fails_when_threshold_not_met():
    summary = summarize_cluster_group(
        [
            {"name": "cluster-a", "joined": True, "available": True},
            {"name": "cluster-b", "joined": False, "available": False},
        ],
        min_managed_clusters=2,
    )
    assert summary["passed"] is False
    assert "cluster-b" in summary["pending"]


def test_cluster_group_fails_zero_without_explicit_allowance():
    summary = summarize_cluster_group(
        [],
        min_managed_clusters=0,
        expected_names=[],
        allow_zero_managed_clusters=False,
    )

    assert summary["passed"] is False
    assert NO_MANAGED_CLUSTERS_PENDING_REASON in summary["pending"]


def test_run_module_reports_all_unready_clusters_without_silent_pass(monkeypatch):
    result = _run_module(
        monkeypatch,
        cluster_status=[
            {"name": "cluster-a", "joined": False, "available": False},
            {"name": "cluster-b", "joined": True, "available": False},
        ],
        min_managed_clusters=2,
        expected_names=["cluster-a", "cluster-b"],
    )

    assert result["exit"]["changed"] is False
    assert result["exit"]["passed"] is False
    assert set(result["exit"]["pending"]) == {"cluster-a", "cluster-b"}
    assert result["exit"]["missing"] == []


def test_run_module_check_mode_reads_status_without_changes(monkeypatch):
    result = _run_module(
        monkeypatch,
        cluster_status=[{"name": "cluster-a", "joined": True, "available": True}],
        check_mode=True,
    )

    assert result["exit"] == {
        "changed": False,
        "passed": True,
        "total": 1,
        "pending": [],
        "missing": [],
    }


def test_run_module_reports_expected_cluster_names_missing_from_observation(
    monkeypatch,
):
    result = _run_module(
        monkeypatch,
        cluster_status=[{"name": "cluster-a", "joined": True, "available": True}],
        min_managed_clusters=2,
        expected_names=["cluster-a", "cluster-b"],
    )

    assert result["exit"]["changed"] is False
    assert result["exit"]["passed"] is False
    assert result["exit"]["missing"] == ["cluster-b"]
