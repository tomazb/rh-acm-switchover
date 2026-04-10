"""Integration tests for the preflight role contract."""


def test_preflight_input_failure_writes_report_and_fails(run_preflight_fixture):
    completed, report = run_preflight_fixture("input_failure.yml")
    assert completed.returncode != 0
    assert report["phase"] == "preflight"
    assert report["status"] == "fail"
    assert any(item["id"] == "preflight-input-secondary-context" for item in report["results"])
