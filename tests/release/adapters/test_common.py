from tests.release.adapters.common import AssertionRecord, ReportArtifact, StreamResult


def test_stream_result_serializes_to_json_ready_dict() -> None:
    result = StreamResult(
        stream="python",
        scenario_id="preflight",
        status="passed",
        command=["python", "acm_switchover.py", "--validate-only"],
        returncode=0,
        stdout_path="scenarios/preflight/stdout.txt",
        stderr_path="scenarios/preflight/stderr.txt",
        reports=[ReportArtifact(type="preflight", path="preflight-report.json", schema_version="1", required=True)],
        assertions=[AssertionRecord(capability="preflight validation", name="exit-code", status="passed", expected="0", actual="0", evidence_path=None, message="command succeeded")],
        started_at="2026-04-27T00:00:00+00:00",
        ended_at="2026-04-27T00:00:01+00:00",
    )

    payload = result.to_dict()

    assert payload["stream"] == "python"
    assert payload["reports"][0]["type"] == "preflight"
    assert payload["assertions"][0]["capability"] == "preflight validation"


def test_stream_result_none_optional_fields_serialize_as_none() -> None:
    result = StreamResult(
        stream="python",
        scenario_id="preflight",
        status="failed",
        command=[],
        returncode=None,
        stdout_path=None,
        stderr_path=None,
        reports=[],
        assertions=[],
        started_at="2026-04-27T00:00:00+00:00",
        ended_at="2026-04-27T00:00:01+00:00",
    )
    payload = result.to_dict()
    assert payload["returncode"] is None
    assert payload["stdout_path"] is None
    assert payload["reports"] == []
    assert payload["assertions"] == []


def test_report_artifact_schema_version_variants() -> None:
    assert ReportArtifact(type="preflight", path="p", schema_version=1, required=True).schema_version == 1
    assert ReportArtifact(type="preflight", path="p", schema_version="1", required=True).schema_version == "1"
    assert ReportArtifact(type="preflight", path="p", schema_version=None, required=True).schema_version is None
