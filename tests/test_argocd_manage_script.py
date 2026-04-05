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
        "--context=test-hub get crd applications.argoproj.io -o json")
            echo '{"metadata":{"name":"applications.argoproj.io"}}'
            exit 0
            ;;
        "--context=test-hub get applications.argoproj.io -A -o json")
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

if [[ "$scenario" == "pause_retry_merge" ]]; then
    retry_step="${MOCK_RETRY_STEP:-1}"
    case "$cmd" in
        "--context=test-hub get crd applications.argoproj.io -o json")
            echo '{"metadata":{"name":"applications.argoproj.io"}}'
            exit 0
            ;;
        "--context=test-hub get applications.argoproj.io -A -o json")
            cat <<'JSON'
{"items":[
  {"metadata":{"namespace":"argocd","name":"app-a"},"status":{"resources":[{"kind":"MultiClusterHub","namespace":"open-cluster-management"}]}},
  {"metadata":{"namespace":"argocd","name":"app-b"},"status":{"resources":[{"kind":"ManagedCluster","namespace":"open-cluster-management"}]}}
]}
JSON
            exit 0
            ;;
        "--context=test-hub -n argocd get application.argoproj.io app-a -o json")
            if [[ "$retry_step" == "1" ]]; then
                cat <<'JSON'
{"metadata":{"namespace":"argocd","name":"app-a","resourceVersion":"4001"},"spec":{"syncPolicy":{"automated":{},"syncOptions":["CreateNamespace=true"]}}}
JSON
            else
                cat <<'JSON'
{"metadata":{"namespace":"argocd","name":"app-a","resourceVersion":"4001","annotations":{"acm-switchover.argoproj.io/paused-by":"retry-run"}},"spec":{"syncPolicy":{"syncOptions":["CreateNamespace=true"]}}}
JSON
            fi
            exit 0
            ;;
        "--context=test-hub -n argocd get application.argoproj.io app-b -o json")
            cat <<'JSON'
{"metadata":{"namespace":"argocd","name":"app-b","resourceVersion":"4002"},"spec":{"syncPolicy":{"automated":{"prune":true}}}}
JSON
            exit 0
            ;;
    esac

    if [[ "$cmd" == *"--context=test-hub -n argocd patch application.argoproj.io app-a --type=merge -p "* ]]; then
        exit 0
    fi
    if [[ "$cmd" == *"--context=test-hub -n argocd patch application.argoproj.io app-b --type=merge -p "* ]]; then
        if [[ "$retry_step" == "1" ]]; then
            exit 1
        fi
        exit 0
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

if [[ "$scenario" == "pause_operator_watched_namespace" ]]; then
    case "$cmd" in
        "--context=ctx-a get crd applications.argoproj.io -o json"|\
        "--context=ctx-b get crd applications.argoproj.io -o json")
            echo '{"metadata":{"name":"applications.argoproj.io"}}'
            exit 0
            ;;
        "--context=ctx-a get applications.argoproj.io -A -o json")
            cat <<'JSON'
{"items":[
  {"metadata":{"namespace":"argocd","name":"app-a"},"status":{"resources":[{"kind":"MultiClusterHub","namespace":"open-cluster-management"}]}}
]}
JSON
            exit 0
            ;;
        "--context=ctx-b get applications.argoproj.io -A -o json")
            cat <<'JSON'
{"items":[
  {"metadata":{"namespace":"argocd","name":"app-b"},"status":{"resources":[{"kind":"ManagedCluster","namespace":"open-cluster-management"}]}}
]}
JSON
            exit 0
            ;;
        "--context=ctx-a -n argocd get application.argoproj.io app-a -o json")
            cat <<'JSON'
{"metadata":{"namespace":"argocd","name":"app-a","resourceVersion":"3001"},"spec":{"syncPolicy":{"automated":{"prune":true}}}}
JSON
            exit 0
            ;;
        "--context=ctx-b -n argocd get application.argoproj.io app-b -o json")
            cat <<'JSON'
{"metadata":{"namespace":"argocd","name":"app-b","resourceVersion":"3002"},"spec":{"syncPolicy":{"automated":{"selfHeal":true}}}}
JSON
            exit 0
            ;;
        "--context=test-hub get crd applications.argoproj.io -o json")
            echo '{"metadata":{"name":"applications.argoproj.io"}}'
            exit 0
            ;;
        "--context=test-hub get applications.argoproj.io -A -o json")
            cat <<'JSON'
{"items":[
  {"metadata":{"namespace":"team-gitops","name":"managed-hub"},"status":{"resources":[{"kind":"MultiClusterHub","namespace":"open-cluster-management"}]}}
]}
JSON
            exit 0
            ;;
        "--context=test-hub -n team-gitops get application.argoproj.io managed-hub -o json")
            cat <<'JSON'
{"metadata":{"namespace":"team-gitops","name":"managed-hub","resourceVersion":"3001"},"spec":{"syncPolicy":{"automated":{"prune":true}}}}
JSON
            exit 0
            ;;
    esac

    if [[ "$cmd" == *"--context=ctx-a -n argocd patch application.argoproj.io app-a --type=merge -p "* ]]; then
        exit 0
    fi
    if [[ "$cmd" == *"--context=ctx-b -n argocd patch application.argoproj.io app-b --type=merge -p "* ]]; then
        exit 0
    fi
    if [[ "$cmd" == *"--context=test-hub -n team-gitops patch application.argoproj.io managed-hub --type=merge -p "* ]]; then
        exit 0
    fi
fi

if [[ "$scenario" == "list_error" ]]; then
    case "$cmd" in
        "--context=test-hub get crd applications.argoproj.io -o json")
            echo '{"metadata":{"name":"applications.argoproj.io"}}'
            exit 0
            ;;
        "--context=test-hub get applications.argoproj.io -A -o json")
            echo "error: You must be logged in to the server" >&2
            exit 1
            ;;
    esac
fi

if [[ "$scenario" == "list_warning_success" ]]; then
    case "$cmd" in
        "--context=test-hub get crd applications.argoproj.io -o json")
            echo '{"metadata":{"name":"applications.argoproj.io"}}'
            exit 0
            ;;
        "--context=test-hub get applications.argoproj.io -A -o json")
            echo "Warning: would violate PodSecurity" >&2
            cat <<'JSON'
{"items":[
  {"metadata":{"namespace":"argocd","name":"app-a"},"status":{"resources":[{"kind":"ManagedCluster","namespace":"open-cluster-management"}]}}
]}
JSON
            exit 0
            ;;
        "--context=test-hub -n argocd get application.argoproj.io app-a -o json")
            cat <<'JSON'
{"metadata":{"namespace":"argocd","name":"app-a","resourceVersion":"5001"},"spec":{"syncPolicy":{"automated":{"prune":true}}}}
JSON
            exit 0
            ;;
    esac

    if [[ "$cmd" == *"--context=test-hub -n argocd patch application.argoproj.io app-a --type=merge -p "* ]]; then
        exit 0
    fi
fi

if [[ "$scenario" == "get_error" ]]; then
    case "$cmd" in
        "--context=test-hub get crd applications.argoproj.io -o json")
            echo '{"metadata":{"name":"applications.argoproj.io"}}'
            exit 0
            ;;
        "--context=test-hub get applications.argoproj.io -A -o json")
            cat <<'JSON'
{"items":[
  {"metadata":{"namespace":"argocd","name":"app-a"},"status":{"resources":[{"kind":"ManagedCluster","namespace":"open-cluster-management"}]}}
]}
JSON
            exit 0
            ;;
        "--context=test-hub -n argocd get application.argoproj.io app-a -o json")
            echo "error: the server has asked for the client to provide credentials" >&2
            exit 1
            ;;
    esac
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
    mock_bin.mkdir(exist_ok=True)
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
    entry = payload["test-hub"] if "test-hub" in payload else payload
    assert len(entry["apps"]) == 1
    assert entry["apps"][0]["namespace"] == "argocd"
    assert entry["apps"][0]["name"] == "app-a"
    assert entry["apps"][0]["original_sync_policy"]["automated"] == {}


def test_pause_retry_preserves_existing_apps_and_run_id(tmp_path):
    """Retrying pause with the same state file must merge prior paused apps for that context."""
    state_file = tmp_path / "pause-state.json"

    first_code, first_out = run_argocd_manage(
        "--context",
        "test-hub",
        "--mode",
        "pause",
        "--state-file",
        str(state_file),
        env={**mock_env(tmp_path, "pause_retry_merge"), "MOCK_RETRY_STEP": "1"},
    )

    assert first_code == 1, first_out
    payload = json.loads(state_file.read_text(encoding="utf-8"))
    first_entry = payload["test-hub"] if "test-hub" in payload else payload
    first_run_id = first_entry["run_id"]
    assert [app["name"] for app in first_entry["apps"]] == ["app-a"]

    second_code, second_out = run_argocd_manage(
        "--context",
        "test-hub",
        "--mode",
        "pause",
        "--state-file",
        str(state_file),
        env={**mock_env(tmp_path, "pause_retry_merge"), "MOCK_RETRY_STEP": "2"},
    )

    assert second_code == 0, second_out
    payload = json.loads(state_file.read_text(encoding="utf-8"))
    entry = payload["test-hub"] if "test-hub" in payload else payload
    assert entry["run_id"] == first_run_id
    assert {app["name"] for app in entry["apps"]} == {"app-a", "app-b"}


def test_pause_keeps_json_valid_when_kubectl_warns_on_stderr(tmp_path):
    """Successful JSON queries must still work when oc prints warnings on stderr."""
    state_file = tmp_path / "pause-state.json"

    code, out = run_argocd_manage(
        "--context",
        "test-hub",
        "--mode",
        "pause",
        "--state-file",
        str(state_file),
        env=mock_env(tmp_path, "list_warning_success"),
    )

    assert code == 0
    assert "Warning: would violate PodSecurity" in out
    assert "Paused argocd/app-a" in out

    payload = json.loads(state_file.read_text(encoding="utf-8"))
    entry = payload["test-hub"] if "test-hub" in payload else payload
    assert entry["apps"][0]["name"] == "app-a"


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


def test_pause_scans_cluster_wide_for_operator_watched_namespaces(tmp_path):
    """Pause mode must not miss ACM apps outside the Argo CD control plane namespace."""
    state_file = tmp_path / "pause-state.json"

    code, out = run_argocd_manage(
        "--context",
        "test-hub",
        "--mode",
        "pause",
        "--state-file",
        str(state_file),
        env=mock_env(tmp_path, "pause_operator_watched_namespace"),
    )

    assert code == 0
    assert "Paused team-gitops/managed-hub" in out

    payload = json.loads(state_file.read_text(encoding="utf-8"))
    entry = payload["test-hub"] if "test-hub" in payload else payload
    assert entry["apps"] == [
        {
            "namespace": "team-gitops",
            "name": "managed-hub",
            "original_sync_policy": {"automated": {"prune": True}},
        }
    ]


def test_pause_namespaces_state_by_context_in_shared_file(tmp_path):
    """Pause state file must preserve entries for multiple hub contexts."""
    state_file = tmp_path / "pause-state.json"

    first_code, first_out = run_argocd_manage(
        "--context",
        "ctx-a",
        "--mode",
        "pause",
        "--state-file",
        str(state_file),
        env=mock_env(tmp_path, "pause_operator_watched_namespace"),
    )
    second_code, second_out = run_argocd_manage(
        "--context",
        "ctx-b",
        "--mode",
        "pause",
        "--state-file",
        str(state_file),
        env=mock_env(tmp_path, "pause_operator_watched_namespace"),
    )

    assert first_code == 0, first_out
    assert second_code == 0, second_out

    payload = json.loads(state_file.read_text(encoding="utf-8"))
    assert set(payload) == {"ctx-a", "ctx-b"}
    assert payload["ctx-a"]["apps"][0]["name"] == "app-a"
    assert payload["ctx-b"]["apps"][0]["name"] == "app-b"


def test_pause_exits_non_zero_on_application_list_error(tmp_path):
    """Generic API/auth failures must not be swallowed during list operations."""
    state_file = tmp_path / "pause-state.json"
    code, out = run_argocd_manage(
        "--context",
        "test-hub",
        "--mode",
        "pause",
        "--state-file",
        str(state_file),
        env=mock_env(tmp_path, "list_error"),
    )

    assert code == 1
    assert "You must be logged in to the server" in out


def test_pause_exits_non_zero_on_application_get_error(tmp_path):
    """Generic API/auth failures must not be treated as missing Applications."""
    state_file = tmp_path / "pause-state.json"
    code, out = run_argocd_manage(
        "--context",
        "test-hub",
        "--mode",
        "pause",
        "--state-file",
        str(state_file),
        env=mock_env(tmp_path, "get_error"),
    )

    assert code == 1
    assert "provide credentials" in out


def test_rejects_unsupported_target_value():
    """Unsupported --target values should fail fast with invalid-args exit code."""
    code, out = run_argocd_manage(
        "--context",
        "test-hub",
        "--mode",
        "pause",
        "--target",
        "all",
    )

    assert code == 2
    assert "unsupported --target value: all" in out
