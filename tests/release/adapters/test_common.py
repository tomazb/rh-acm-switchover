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
