#!/usr/bin/env python3
"""Non-live CLI wrapper for deterministic lab role controller planning."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any, TextIO

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.release.lab_controller.artifacts import (  # noqa: E402
    sanitize_artifact_payload,
    sanitize_artifact_text,
    validate_artifact_payload_redacted,
)
from tests.release.lab_controller.discovery import fake_identity  # noqa: E402
from tests.release.lab_controller.execution import (  # noqa: E402
    ExecutionMode,
    ReleaseFrameworkLocalBackend,
)
from tests.release.lab_controller.harness import CommandRunResult, FakeCommandRunner  # noqa: E402
from tests.release.lab_controller.models import (  # noqa: E402
    CertificationDecision,
    HubIdentityEvidence,
    PhysicalHubConfig,
    PhysicalHubLabel,
    StableLabConfig,
)
from tests.release.lab_controller.planner import (  # noqa: E402
    CertificationRunResult,
    build_ping_pong_plan,
    run_certification_plan,
)

SCRIPT_NAME = "scripts/release/run_lab_role_controller.py"
RUN_ARTIFACT_FILENAME = "lab-controller-run.json"
SUPPORTED_PLANS = {"ping-pong"}
SUPPORTED_MODES = {"fake", "release-framework-dry-run", "release-framework-local"}
LIVE_MODES = {"live", "release-framework-live", "release_framework_live"}
EXIT_SUCCESS = 0
EXIT_STRICT_NON_PASS = 1
EXIT_USAGE = 2
EXIT_ARTIFACT = 3
EXPECTED_MANAGED_CLUSTERS = ("mc-1", "mc-2", "mc-3")
_UNSAFE_ARTIFACT_COMPONENTS = {".release", ".kube"}
_UNSAFE_ARTIFACT_MARKERS = ("kubeconfig", "token", "secret", "credential", "password")


def _supported_values(values: set[str]) -> str:
    return ", ".join(sorted(values))


class CliUsageError(ValueError):
    """Raised for deterministic CLI validation failures."""


class LabControllerArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise CliUsageError(message)

    def exit(self, status: int = 0, message: str | None = None) -> None:
        if status:
            raise CliUsageError(message or f"argument parser exited with status {status}")
        raise SystemExit(status)


class LabControllerHelpFormatter(argparse.HelpFormatter):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("width", 160)
        super().__init__(*args, **kwargs)


def _identity(label: PhysicalHubLabel) -> HubIdentityEvidence:
    return fake_identity(label)


def _hub_config(label: PhysicalHubLabel) -> PhysicalHubConfig:
    return PhysicalHubConfig(
        physical_label=label,
        kubeconfig_reference=f"runtime-placeholder-{label.value}",
        context_name=f"{label.value}-context",
        expected_identity=_identity(label),
    )


def build_sanitized_lab_config() -> StableLabConfig:
    """Build the Phase 7A built-in fake 2-hub / 3-managed-cluster lab fixture."""
    return StableLabConfig(
        physical_hubs={
            PhysicalHubLabel.HUB_A: _hub_config(PhysicalHubLabel.HUB_A),
            PhysicalHubLabel.HUB_B: _hub_config(PhysicalHubLabel.HUB_B),
        },
        expected_managed_cluster_names=EXPECTED_MANAGED_CLUSTERS,
        enabled_streams=("bash", "python", "ansible"),
        scenario_ids=("preflight",),
        profile_name="phase7a-sanitized-lab",
        artifact_root="artifacts/release-lab/phase7a",
    )


def expected_identities(config: StableLabConfig) -> dict[PhysicalHubLabel, HubIdentityEvidence]:
    return {
        label: hub.expected_identity for label, hub in config.physical_hubs.items() if hub.expected_identity is not None
    }


def _parser() -> LabControllerArgumentParser:
    parser = LabControllerArgumentParser(
        description="Run the non-live deterministic ACM lab role controller.",
        formatter_class=LabControllerHelpFormatter,
    )
    parser.add_argument(
        "--plan",
        default="ping-pong",
        metavar="PLAN",
        help=f"deterministic controller plan to run; supported: {_supported_values(SUPPORTED_PLANS)}",
    )
    parser.add_argument(
        "--mode",
        default="fake",
        metavar="MODE",
        help=(
            "non-live execution boundary mode; supported: "
            f"{_supported_values(SUPPORTED_MODES)}; live modes are unsupported"
        ),
    )
    parser.add_argument("--artifact-dir", help="caller-provided output directory for the redacted artifact bundle")
    parser.add_argument("--plan-id", help="deterministic plan id override")
    parser.add_argument("--output-format", default="summary", choices=("summary", "json"))
    parser.add_argument("--allow-local-execution", action="store_true", default=False)
    parser.add_argument("--no-write", action="store_true", default=False)
    parser.add_argument("--strict", action="store_true", default=False)
    return parser


def _normalize_mode(mode: str) -> str:
    return mode.strip().replace("_", "-")


def _validate_plan(plan: str) -> str:
    if plan not in SUPPORTED_PLANS:
        raise CliUsageError(f"unsupported plan: {plan}")
    return plan


def _validate_mode(mode: str, *, allow_local_execution: bool) -> str:
    normalized = _normalize_mode(mode)
    if normalized in LIVE_MODES:
        raise CliUsageError("live execution mode is unsupported for Phase 7A")
    if normalized not in SUPPORTED_MODES:
        raise CliUsageError(f"unsupported mode: {mode}")
    if normalized == "release-framework-local" and not allow_local_execution:
        raise CliUsageError("release-framework-local mode requires --allow-local-execution")
    return normalized


def _unsafe_artifact_dir_reason(raw_value: str) -> str | None:
    if not raw_value:
        return "missing artifact directory"
    raw_path = Path(raw_value)
    raw_parts = tuple(part for part in raw_path.parts if part not in {"", "."})
    if any(part == ".." for part in raw_parts):
        return "path traversal"
    lowered_parts = tuple(part.lower() for part in raw_parts)
    if any(part in _UNSAFE_ARTIFACT_COMPONENTS for part in lowered_parts):
        return "unsafe artifact directory component"
    if any(any(marker in part for marker in _UNSAFE_ARTIFACT_MARKERS) for part in lowered_parts):
        return "unsafe artifact directory component"
    if raw_path.is_absolute():
        resolved = raw_path.resolve(strict=False)
        try:
            resolved.relative_to(Path(tempfile.gettempdir()).resolve())
        except ValueError:
            return "unsafe artifact directory absolute path"
    return None


def validate_artifact_dir(value: str | None, *, no_write: bool) -> Path | None:
    if no_write and value is None:
        return None
    if value is None:
        raise CliUsageError("--artifact-dir is required unless --no-write is set")
    reason = _unsafe_artifact_dir_reason(value)
    if reason is not None:
        raise CliUsageError(f"unsafe artifact directory: {reason}")
    return Path(value)


def _controller_artifact_root(plan_id: str) -> str:
    safe_plan_id = plan_id if sanitize_artifact_text(plan_id) == plan_id else "phase7a-plan"
    return f"artifacts/release-lab/{safe_plan_id}"


def _fake_local_backend(plan_id: str) -> ReleaseFrameworkLocalBackend:
    runner = FakeCommandRunner(results=tuple(CommandRunResult(return_code=0, stdout="ok", stderr="") for _ in range(5)))
    return ReleaseFrameworkLocalBackend(
        command_runner=runner,
        allow_local_execution=True,
        plan_id=plan_id,
    )


def run_controller(
    *,
    plan_name: str,
    mode: str,
    plan_id: str,
) -> CertificationRunResult:
    """Invoke the existing deterministic planner with the built-in sanitized lab fixture."""
    if plan_name != "ping-pong":
        raise CliUsageError(f"unsupported plan: {plan_name}")

    config = build_sanitized_lab_config()
    plan = build_ping_pong_plan(plan_id=plan_id)
    artifact_root = _controller_artifact_root(plan_id)
    if mode == "fake":
        return run_certification_plan(
            plan,
            lab_config=config,
            expected_identities=expected_identities(config),
            artifact_root=artifact_root,
            execution_mode=ExecutionMode.FAKE,
        )
    if mode == "release-framework-dry-run":
        return run_certification_plan(
            plan,
            lab_config=config,
            expected_identities=expected_identities(config),
            artifact_root=artifact_root,
            execution_mode=ExecutionMode.RELEASE_FRAMEWORK_DRY_RUN,
        )
    if mode == "release-framework-local":
        return run_certification_plan(
            plan,
            lab_config=config,
            expected_identities=expected_identities(config),
            artifact_root=artifact_root,
            execution_backend=_fake_local_backend(plan_id),
        )
    raise CliUsageError(f"unsupported mode: {mode}")


def with_final_decision(result: CertificationRunResult, decision: CertificationDecision) -> CertificationRunResult:
    """Return a copied result with run-level final decision fields changed for deterministic tests."""
    payload = json.loads(json.dumps(result.artifact_bundle.payload, sort_keys=True))
    payload["final_decision"] = decision.value
    payload["safe_to_continue"] = decision is CertificationDecision.PASS
    payload["first_blocking_segment"] = None if decision is CertificationDecision.PASS else "simulated"
    payload["first_blocking_scenario"] = None if decision is CertificationDecision.PASS else "simulated"
    payload["first_blocking_reason"] = None if decision is CertificationDecision.PASS else "simulated final decision"
    payload["manual_recovery_required"] = decision is CertificationDecision.RECOVERY_REQUIRED
    return replace(
        result,
        decision=decision,
        reason="simulated final decision" if decision is not CertificationDecision.PASS else result.reason,
        first_blocking_reason=payload["first_blocking_reason"],
        artifact_bundle=replace(result.artifact_bundle, payload=payload),
    )


def _artifact_dir_summary(artifact_dir: Path | None) -> str:
    if artifact_dir is None:
        return "not_written"
    name = artifact_dir.name or "artifact-dir"
    sanitized = sanitize_artifact_text(name)
    return sanitized if sanitized == name else "[REDACTED]"


def _cli_metadata(
    *,
    plan_name: str,
    mode: str,
    strict: bool,
    no_write: bool,
    artifact_dir: Path | None,
) -> dict[str, Any]:
    return {
        "script_name": SCRIPT_NAME,
        "selected_plan": plan_name,
        "selected_mode": mode,
        "strict": strict,
        "write_mode": "no-write" if no_write else "write",
        "artifact_dir_summary": _artifact_dir_summary(artifact_dir),
        "no_live_execution_evidence": True,
        "no_live_certification_evidence": True,
        "live_mode_supported": False,
    }


def build_run_artifact(
    *,
    result: CertificationRunResult,
    plan_name: str,
    mode: str,
    strict: bool,
    no_write: bool,
    artifact_dir: Path | None,
) -> dict[str, Any]:
    payload = json.loads(json.dumps(result.artifact_bundle.payload, sort_keys=True))
    if bool(payload.get("live_certification_evidence", False)):
        raise ValueError("Phase 7A artifacts cannot claim live certification evidence")
    execution_backends = payload.get("execution_backends", {})
    if isinstance(execution_backends, dict) and bool(execution_backends.get("live_certification_evidence_exists")):
        raise ValueError("Phase 7A artifacts cannot claim live certification evidence")

    payload["controller_phase"] = "phase7a"
    payload["cli_metadata"] = _cli_metadata(
        plan_name=plan_name,
        mode=mode,
        strict=strict,
        no_write=no_write,
        artifact_dir=artifact_dir,
    )
    payload["real_execution_evidence"] = bool(
        isinstance(execution_backends, dict) and execution_backends.get("real_execution_evidence_exists")
    )
    payload["live_certification_evidence"] = False
    payload["live_execution_evidence"] = False
    payload["artifact_files"] = [RUN_ARTIFACT_FILENAME] if not no_write else []
    sanitized = sanitize_artifact_payload(payload)
    validate_artifact_payload_redacted(sanitized)
    if bool(sanitized.get("live_certification_evidence", False)):
        raise ValueError("Phase 7A artifacts cannot claim live certification evidence")
    return sanitized


def _summary_payload(
    *,
    artifact: dict[str, Any],
    artifact_written: bool,
) -> dict[str, Any]:
    return {
        "plan_id": artifact.get("plan_id"),
        "mode": artifact.get("cli_metadata", {}).get("selected_mode"),
        "final_decision": artifact.get("final_decision"),
        "safe_to_continue": bool(artifact.get("safe_to_continue", False)),
        "retry_allowed": bool(artifact.get("retry_allowed", False)),
        "manual_recovery_required": bool(artifact.get("manual_recovery_required", False)),
        "first_blocking_segment": artifact.get("first_blocking_segment"),
        "first_blocking_scenario": artifact.get("first_blocking_scenario"),
        "first_blocking_reason": artifact.get("first_blocking_reason"),
        "artifact_path": RUN_ARTIFACT_FILENAME if artifact_written else "not_written",
        "live_certification_evidence": False,
    }


def _write_summary(summary: dict[str, Any], *, output_format: str, stdout: TextIO) -> None:
    if output_format == "json":
        stdout.write(json.dumps(summary, sort_keys=True) + "\n")
        return
    for key in (
        "plan_id",
        "mode",
        "final_decision",
        "safe_to_continue",
        "retry_allowed",
        "manual_recovery_required",
        "first_blocking_segment",
        "first_blocking_scenario",
        "first_blocking_reason",
        "artifact_path",
        "live_certification_evidence",
    ):
        value = summary.get(key)
        if isinstance(value, bool):
            rendered = str(value).lower()
        elif value is None:
            rendered = "none"
        else:
            rendered = sanitize_artifact_text(str(value)) or "[REDACTED]"
        stdout.write(f"{key}={rendered}\n")


def write_artifact_bundle(artifact_dir: Path, artifact: dict[str, Any]) -> Path:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = artifact_dir / RUN_ARTIFACT_FILENAME
    artifact_path.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return artifact_path


def _parse(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = _parser()
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    try:
        args = _parse(argv)
        plan_name = _validate_plan(str(args.plan))
        mode = _validate_mode(str(args.mode), allow_local_execution=bool(args.allow_local_execution))
        artifact_dir = validate_artifact_dir(args.artifact_dir, no_write=bool(args.no_write))
    except CliUsageError as exc:
        stderr.write(f"{sanitize_artifact_text(str(exc)) or '[REDACTED]'}\n")
        return EXIT_USAGE

    plan_id = str(args.plan_id or f"phase7a-{plan_name}")
    try:
        result = run_controller(plan_name=plan_name, mode=mode, plan_id=plan_id)
        artifact = build_run_artifact(
            result=result,
            plan_name=plan_name,
            mode=mode,
            strict=bool(args.strict),
            no_write=bool(args.no_write),
            artifact_dir=artifact_dir,
        )
        validate_artifact_payload_redacted(artifact)
        if bool(artifact.get("live_certification_evidence", False)):
            raise ValueError("Phase 7A artifacts cannot claim live certification evidence")
        artifact_written = False
        if not args.no_write:
            if artifact_dir is None:
                raise ValueError("--artifact-dir is required unless --no-write is set")
            write_artifact_bundle(artifact_dir, artifact)
            artifact_written = True
    except (OSError, ValueError) as exc:
        stderr.write(f"{sanitize_artifact_text(str(exc)) or '[REDACTED]'}\n")
        return EXIT_ARTIFACT

    _write_summary(
        _summary_payload(artifact=artifact, artifact_written=artifact_written),
        output_format=str(args.output_format),
        stdout=stdout,
    )
    if args.strict and artifact.get("final_decision") != CertificationDecision.PASS.value:
        return EXIT_STRICT_NON_PASS
    return EXIT_SUCCESS


if __name__ == "__main__":
    raise SystemExit(main())
