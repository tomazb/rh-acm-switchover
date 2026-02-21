"""Regression tests for scripts/argocd-manage.sh failure handling."""

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "argocd-manage.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("jq") is None,
    reason="jq is required for argocd-manage.sh script tests",
)


def run_argocd_manage(*args: str, env=None) -> tuple[int, str]:
    """Run the Argo CD management script and return exit code + merged output."""
    use_env = os.environ.copy()
    if env:
        use_env.update(env)

    proc = subprocess.run(
        ["bash", str(SCRIPT_PATH), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=use_env,
        timeout=30,
    )
    return proc.returncode, proc.stdout


def write_mock_oc(mock_bin: Path) -> None:
    """Create a scenario-driven mock oc binary."""
    oc_script = mock_bin / "oc"
    oc_script.write_text(
        """#!/bin/bash
set -euo pipefail

scenario="${MOCK_SCENARIO:-}"
cmd="$*"

if [[ "$scenario" == "pause_partial_failure" ]]; then
    case "$cmd" in
        "--context=test-hub get crd applications.argoproj.io")
            exit 0
            ;;
        "--context=test-hub get crd argocds.argoproj.io")
            exit 1
            ;;
        "--context=test-hub get applications.argoproj.io -A -o json")
            cat <<'JSON'
{"items":[
  {"metadata":{"namespace":"argocd","name":"app-a"}},
  {"metadata":{"namespace":"argocd","name":"app-b"}}
]}
JSON
            exit 0
            ;;
        "--context=test-hub -n argocd get applications.argoproj.io -o json")
            cat <<'JSON'
{"items":[
  {"metadata":{"namespace":"argocd","name":"app-a"},"status":{"resources":[{"kind":"MultiClusterHub","namespace":"open-cluster-management"}]}},
  {"metadata":{"namespace":"argocd","name":"app-b"},"status":{"resources":[{"kind":"ManagedCluster","namespace":"open-cluster-management"}]}}
]}
JSON
            exit 0
            ;;
        "--context=test-hub -n argocd get application.argoproj.io app-a -o json")
            cat <<'JSON'
{"metadata":{"namespace":"argocd","name":"app-a","resourceVersion":"1001"},"spec":{"syncPolicy":{"automated":{},"syncOptions":["CreateNamespace=true"]}}}
JSON
            exit 0
            ;;
        "--context=test-hub -n argocd get application.argoproj.io app-b -o json")
            cat <<'JSON'
{"metadata":{"namespace":"argocd","name":"app-b","resourceVersion":"1002"},"spec":{"syncPolicy":{"automated":{"prune":true}}}}
JSON
            exit 0
            ;;
    esac

    if [[ "$cmd" == *"--context=test-hub -n argocd patch application.argoproj.io app-a --type=merge -p "* ]]; then
        exit 0
    fi
    if [[ "$cmd" == *"--context=test-hub -n argocd patch application.argoproj.io app-b --type=merge -p "* ]]; then
        exit 1
    fi
fi

if [[ "$scenario" == "resume_partial_failure" || "$scenario" == "resume_success" ]]; then
    case "$cmd" in
        "--context=test-hub -n argocd get application.argoproj.io app-a -o json")
            cat <<'JSON'
{"metadata":{"namespace":"argocd","name":"app-a","resourceVersion":"2001","annotations":{"acm-switchover.argoproj.io/paused-by":"run-123"}}}
JSON
            exit 0
            ;;
        "--context=test-hub -n argocd get application.argoproj.io app-b -o json")
            cat <<'JSON'
{"metadata":{"namespace":"argocd","name":"app-b","resourceVersion":"2002","annotations":{"acm-switchover.argoproj.io/paused-by":"run-123"}}}
JSON
            exit 0
            ;;
    esac

    if [[ "$cmd" == *"--context=test-hub -n argocd patch application.argoproj.io app-a --type=merge -p "* ]]; then
        exit 0
    fi
    if [[ "$cmd" == *"--context=test-hub -n argocd patch application.argoproj.io app-b --type=merge -p "* ]]; then
        if [[ "$scenario" == "resume_partial_failure" ]]; then
            exit 1
        fi
        exit 0
    fi
fi

echo "unexpected command for scenario '$scenario': $cmd" >&2
exit 1
""",
        encoding="utf-8",
    )
    oc_script.chmod(oc_script.stat().st_mode | stat.S_IEXEC)


def mock_env(tmp_path: Path, scenario: str) -> dict:
    """Build an environment with mocked oc command for a specific scenario."""
    mock_bin = tmp_path / "bin"
    mock_bin.mkdir()
    write_mock_oc(mock_bin)
    return {
        "PATH": f"{mock_bin}:{os.environ['PATH']}",
        "MOCK_SCENARIO": scenario,
    }


def write_resume_state(path: Path) -> None:
    """Write a state file used by resume-mode tests."""
    payload = {
        "run_id": "run-123",
        "context": "test-hub",
        "paused_at": "2026-02-21T00:00:00Z",
        "apps": [
            {
                "namespace": "argocd",
                "name": "app-a",
                "original_sync_policy": {"automated": {}, "syncOptions": ["CreateNamespace=true"]},
            },
            {
                "namespace": "argocd",
                "name": "app-b",
                "original_sync_policy": {"automated": {"prune": True}},
            },
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_pause_writes_partial_state_before_returning_failure(tmp_path):
    """Pause mode must persist already-paused apps before aborting on patch error."""
    state_file = tmp_path / "pause-state.json"
    code, out = run_argocd_manage(
        "--context",
        "test-hub",
        "--mode",
        "pause",
        "--state-file",
        str(state_file),
        env=mock_env(tmp_path, "pause_partial_failure"),
    )

    assert code == 1
    assert "Error: Failed to patch argocd/app-b" in out
    assert "Partial state written to" in out
    assert state_file.exists()

    payload = json.loads(state_file.read_text(encoding="utf-8"))
    assert payload["context"] == "test-hub"
    assert len(payload["apps"]) == 1
    assert payload["apps"][0]["namespace"] == "argocd"
    assert payload["apps"][0]["name"] == "app-a"
    assert payload["apps"][0]["original_sync_policy"]["automated"] == {}


def test_resume_returns_failure_when_any_patch_fails(tmp_path):
    """Resume mode should return non-zero if at least one app fails to patch."""
    state_file = tmp_path / "pause-state.json"
    write_resume_state(state_file)

    code, out = run_argocd_manage(
        "--context",
        "test-hub",
        "--mode",
        "resume",
        "--state-file",
        str(state_file),
        env=mock_env(tmp_path, "resume_partial_failure"),
    )

    assert code == 1
    assert "Resumed argocd/app-a" in out
    assert "Error: Failed to patch argocd/app-b" in out
    assert "Resume completed with 1 patch failure(s)." in out


def test_resume_returns_success_when_all_patches_succeed(tmp_path):
    """Resume mode should return zero when all tracked apps patch successfully."""
    state_file = tmp_path / "pause-state.json"
    write_resume_state(state_file)

    code, out = run_argocd_manage(
        "--context",
        "test-hub",
        "--mode",
        "resume",
        "--state-file",
        str(state_file),
        env=mock_env(tmp_path, "resume_success"),
    )

    assert code == 0
    assert "Resumed argocd/app-a" in out
    assert "Resumed argocd/app-b" in out
    assert "patch failure" not in out
