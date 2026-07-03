from lib.constants import CAPTURE_REDACTION_REJECTED_MESSAGE
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
        assertions=[
            AssertionRecord(
                capability="preflight validation",
                name="exit-code",
                status="passed",
                expected="0",
                actual="0",
                evidence_path=None,
                message="command succeeded",
            )
        ],
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


def _run_helper(tmp_path, command, **overrides):
    from tests.release.adapters.common import run_stream_subprocess

    scenario_dir = tmp_path / "scenarios" / "demo" / "stream"
    kwargs = dict(
        stream="demo",
        scenario_id="demo-scenario",
        command=command,
        cwd=tmp_path,
        artifact_dir=tmp_path,
        scenario_dir=scenario_dir,
        capability="demo-capability",
        timeout_message_template="Demo timed out after {timeout} seconds",
        success_message="Demo completed",
        failure_message="Demo returned a non-zero exit code",
    )
    kwargs.update(overrides)
    return run_stream_subprocess(**kwargs), scenario_dir


def test_run_stream_subprocess_success_writes_captures_and_passes(tmp_path):
    result, scenario_dir = _run_helper(tmp_path, ["sh", "-c", "echo out; echo err >&2"])

    assert result.status == "passed"
    assert result.stream == "demo"
    assert result.returncode == 0
    assert result.reports == []
    assert (scenario_dir / "stdout.txt").read_text() == "out\n"
    assert (scenario_dir / "stderr.txt").read_text() == "err\n"
    (assertion,) = result.assertions
    assert assertion.capability == "demo-capability"
    assert assertion.name == "exit-code"
    assert assertion.status == "passed"
    assert assertion.actual == "0"
    assert assertion.message == "Demo completed"
    assert assertion.evidence_path == str(scenario_dir / "stdout.txt")


def test_run_stream_subprocess_failure_uses_stderr_evidence(tmp_path):
    result, scenario_dir = _run_helper(tmp_path, ["sh", "-c", "exit 3"])

    assert result.status == "failed"
    assert result.returncode == 3
    (assertion,) = result.assertions
    assert assertion.status == "failed"
    assert assertion.actual == "3"
    assert assertion.message == "Demo returned a non-zero exit code"
    assert assertion.evidence_path == str(scenario_dir / "stderr.txt")


def test_run_stream_subprocess_timeout_reports_formatted_message(tmp_path):
    result, scenario_dir = _run_helper(tmp_path, ["sleep", "5"], timeout_seconds=1)

    assert result.status == "failed"
    assert result.returncode == -1
    (assertion,) = result.assertions
    assert assertion.actual == "timeout"
    assert assertion.message == "Demo timed out after 1 seconds"
    assert assertion.evidence_path == str(scenario_dir / "stderr.txt")


def test_run_stream_subprocess_evaluates_reports_callable(tmp_path):
    report = ReportArtifact(type="demo", path="p.json", schema_version=1, required=True)
    result, _ = _run_helper(tmp_path, ["true"], reports=lambda: [report])

    assert result.reports == [report]


def test_run_stream_subprocess_rejected_capture_forces_failure(tmp_path, monkeypatch):
    from tests.release.adapters import common as common_module

    def fake_write_capture_artifact(*, run_dir, relative_path, content, rejected_placeholder):
        return run_dir / relative_path, False

    monkeypatch.setattr(common_module, "write_capture_artifact", fake_write_capture_artifact)
    result, _ = _run_helper(tmp_path, ["sh", "-c", "echo out"])

    assert result.status == "failed"
    assert [a.name for a in result.assertions] == ["exit-code", "artifact-redaction"]
    redaction = result.assertions[1]
    assert redaction.status == "failed"
    assert redaction.expected == "clean"
    assert redaction.actual == "rejected"
    assert redaction.evidence_path is None
    assert redaction.message == CAPTURE_REDACTION_REJECTED_MESSAGE
