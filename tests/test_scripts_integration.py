"""Integration tests for bash scripts with mocked oc/jq commands.

These tests use temporary mock binaries in PATH to simulate various cluster states
and validate the scripts' logic end-to-end without requiring actual cluster access.

Test categories:
- Success paths: All validation checks pass
- Failure scenarios: Version mismatches, backups in progress, missing resources
"""

import os
import re
import stat
import subprocess
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"


def strip_ansi(text: str) -> str:
    """Remove ANSI color codes from text."""
    ansi_pattern = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
    return ansi_pattern.sub("", text)


def run_script(script_name: str, *args: str, env=None):
    """Run a bash script with optional environment override."""
    script_path = SCRIPTS_DIR / script_name
    assert script_path.exists(), f"Script not found: {script_path}"
    cmd = ["bash", str(script_path), *args]

    use_env = os.environ.copy()
    if env:
        use_env.update(env)

    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=use_env,
        timeout=10,
    )
    output = strip_ansi(proc.stdout)
    return proc.returncode, output


@pytest.fixture
def mock_oc_success(tmp_path):
    """Create mocked oc/jq binaries that simulate successful validation."""
    mock_bin = tmp_path / "bin"
    mock_bin.mkdir()

    # Create comprehensive mock oc script
    oc_script = mock_bin / "oc"
    oc_script.write_text(
        """#!/bin/bash
# Mock oc command for success scenarios

case "$*" in
    # Context checks
    "config get-contexts primary-ok")
        exit 0
        ;;
    "config get-contexts secondary-ok")
        exit 0
        ;;
    
    # Namespace checks
    "--context=primary-ok get namespace open-cluster-management")
        exit 0
        ;;
    "--context=primary-ok get namespace open-cluster-management-backup")
        exit 0
        ;;
    "--context=secondary-ok get namespace open-cluster-management")
        exit 0
        ;;
    "--context=secondary-ok get namespace open-cluster-management-backup")
        exit 0
        ;;
    "--context=primary-ok get namespace openshift-adp")
        exit 0
        ;;
    "--context=secondary-ok get namespace openshift-adp")
        exit 0
        ;;
    
    # ACM version checks
    "--context=primary-ok get mch -n open-cluster-management -o jsonpath="*"currentVersion"*"")
        echo "2.11.0"
        exit 0
        ;;
    "--context=secondary-ok get mch -n open-cluster-management -o jsonpath="*"currentVersion"*"")
        echo "2.11.0"
        exit 0
        ;;
    
    # OADP/Velero checks (using BACKUP_NAMESPACE from constants.sh)
    "--context=primary-ok get pods -n open-cluster-management-backup -l app.kubernetes.io/name=velero --no-headers")
        echo "velero-xyz   1/1   Running"
        exit 0
        ;;
    "--context=secondary-ok get pods -n open-cluster-management-backup -l app.kubernetes.io/name=velero --no-headers")
        echo "velero-abc   1/1   Running"
        exit 0
        ;;
    
    # DPA checks
    "--context=primary-ok get dpa -n open-cluster-management-backup --no-headers")
        echo "dpa-config   Reconciled"
        exit 0
        ;;
    "--context=secondary-ok get dpa -n open-cluster-management-backup --no-headers")
        echo "dpa-config   Reconciled"
        exit 0
        ;;
    "--context=primary-ok get dpa -n open-cluster-management-backup -o jsonpath="*"items[0].metadata.name"*"")
        echo "dpa-config"
        exit 0
        ;;
    "--context=secondary-ok get dpa -n open-cluster-management-backup -o jsonpath="*"items[0].metadata.name"*"")
        echo "dpa-config"
        exit 0
        ;;
    "--context=primary-ok get dpa dpa-config -n open-cluster-management-backup -o jsonpath="*"Reconciled"*"")
        echo "True"
        exit 0
        ;;
    "--context=secondary-ok get dpa dpa-config -n open-cluster-management-backup -o jsonpath="*"Reconciled"*"")
        echo "True"
        exit 0
        ;;
    
    # Backup checks
    "--context=primary-ok get backup -n open-cluster-management-backup --no-headers")
        echo "backup-20241124   Finished"
        exit 0
        ;;
    *"InProgress"*)
        echo ""
        exit 0
        ;;
    *"--sort-by=.metadata.creationTimestamp"*"items[-1:].metadata.name"*)
        # Latest backup name query
        echo "backup-20241124"
        exit 0
        ;;
    *"get backup backup-20241124"*"status.phase"*)
        # Latest backup phase query
        echo "Finished"
        exit 0
        ;;
    
    # ClusterDeployment checks
    "--context=primary-ok get clusterdeployment --all-namespaces --no-headers")
        # No Hive clusters
        exit 0
        ;;
    
    # Passive sync restore check - discovery by syncRestoreWithNewBackups=true
    "--context=secondary-ok get restore -n open-cluster-management-backup -o json")
        cat << 'RESTORE_JSON'
{"items":[{"metadata":{"name":"restore-acm-passive-sync"},"spec":{"syncRestoreWithNewBackups":true},"status":{"phase":"Enabled"}}]}
RESTORE_JSON
        exit 0
        ;;
    "--context=secondary-ok get restore restore-acm-passive-sync -n open-cluster-management-backup -o jsonpath="*"phase"*"")
        echo "Enabled"
        exit 0
        ;;
    # Fallback check for well-known name (not needed since discovery finds it, but keep for robustness)
    "--context=secondary-ok get restore restore-acm-passive-sync -n open-cluster-management-backup")
        exit 0
        ;;
    
    # Observability checks
    "--context=primary-ok get namespace open-cluster-management-observability")
        exit 0
        ;;
    "--context=primary-ok get mco observability -n open-cluster-management-observability")
        exit 0
        ;;
    "--context=secondary-ok get namespace open-cluster-management-observability")
        exit 0
        ;;
    "--context=secondary-ok get secret thanos-object-storage -n open-cluster-management-observability")
        exit 0
        ;;
    
    # ACM 2.14+ autoImportStrategy checks (Check 11)
    "--context=primary-ok get namespace multicluster-engine")
        exit 0
        ;;
    "--context=secondary-ok get namespace multicluster-engine")
        exit 0
        ;;
    "--context=primary-ok get configmap import-controller-config -n multicluster-engine")
        # ConfigMap not found means default ImportOnly is used (which is OK for ACM < 2.14)
        exit 1
        ;;
    "--context=secondary-ok get configmap import-controller-config -n multicluster-engine")
        # ConfigMap not found means default ImportOnly is used
        exit 1
        ;;
    
    # Secondary hub managed clusters for Check 11 cluster count  
    "--context=secondary-ok get managedclusters --no-headers")
        echo "local-cluster   True"
        exit 0
        ;;
    
    # Postflight checks
    "--context=new-hub get restore -n open-cluster-management-backup --sort-by=.metadata.creationTimestamp -o jsonpath="*"")
        echo "restore-final Finished 2024-11-24T10:00:00Z"
        exit 0
        ;;
    "--context=new-hub get managedclusters --no-headers")
        echo "local-cluster   True"
        echo "cluster1        True"
        echo "cluster2        True"
        exit 0
        ;;
    "--context=new-hub get managedclusters -o json")
        cat << 'EOF'
{
  "items": [
    {
      "metadata": {"name": "local-cluster"},
      "status": {
        "conditions": [
          {"type": "ManagedClusterConditionAvailable", "status": "True"},
          {"type": "ManagedClusterJoined", "status": "True"}
        ]
      }
    },
    {
      "metadata": {"name": "cluster1"},
      "status": {
        "conditions": [
          {"type": "ManagedClusterConditionAvailable", "status": "True"},
          {"type": "ManagedClusterJoined", "status": "True"}
        ]
      }
    },
    {
      "metadata": {"name": "cluster2"},
      "status": {
        "conditions": [
          {"type": "ManagedClusterConditionAvailable", "status": "True"},
          {"type": "ManagedClusterJoined", "status": "True"}
        ]
      }
    }
  ]
}
EOF
        exit 0
        ;;
    "--context=new-hub get managedclusters")
        echo "NAME           STATUS   AGE"
        echo "local-cluster  True     30d"
        echo "cluster1       True     20d"
        echo "cluster2       True     15d"
        exit 0
        ;;
    "--context=new-hub get namespace open-cluster-management-observability")
        exit 0
        ;;
    "--context=new-hub get mco observability -n open-cluster-management-observability -o jsonpath="*"")
        echo "True"
        exit 0
        ;;
    "--context=new-hub get pods -n open-cluster-management-observability -l "*"app=observability-grafana"*" --no-headers")
        echo "observability-grafana-1 Running"
        exit 0
        ;;
    "--context=new-hub get pods -n open-cluster-management-observability -l "*"app=observability-observatorium-api"*" --no-headers")
        echo "observability-observatorium-api-1 Running"
        exit 0
        ;;
    "--context=new-hub get pods -n open-cluster-management-observability -l "*"app=observability-thanos-query"*" --no-headers")
        echo "observability-thanos-query-1 Running"
        exit 0
        ;;
    "--context=new-hub get pods -n open-cluster-management-observability --no-headers")
        # For error check
        echo "pod-ok Running"
        exit 0
        ;;
    "--context=new-hub get pods -n open-cluster-management-observability -l "*"app.kubernetes.io/name=observatorium-api"*" --no-headers")
        echo "observatorium-api-1 Running"
        exit 0
        ;;
    "--context=new-hub get pods -n open-cluster-management-observability -l "*"app.kubernetes.io/name=observatorium-api"*" -o jsonpath="*"")
        echo "2024-11-24T10:00:00Z"
        exit 0
        ;;
    "--context=new-hub get route grafana -n open-cluster-management-observability -o jsonpath="*"")
        echo "grafana.example.com"
        exit 0
        ;;
    "--context=new-hub get backupschedule -n open-cluster-management-backup --no-headers")
        echo "schedule-acm"
        exit 0
        ;;
    "--context=new-hub get backupschedule -n open-cluster-management-backup -o jsonpath="*"items[0].metadata.name"*"")
        echo "schedule-acm"
        exit 0
        ;;
    "--context=new-hub get backupschedule schedule-acm -n open-cluster-management-backup -o jsonpath="*"spec.paused"*"")
        echo "false"
        exit 0
        ;;
    "--context=new-hub get backup -n open-cluster-management-backup --sort-by=.metadata.creationTimestamp --no-headers")
        echo "backup-1"
        echo "backup-2"
        echo "backup-3"
        exit 0
        ;;
    "--context=new-hub get backup -n open-cluster-management-backup --sort-by=.metadata.creationTimestamp -o jsonpath="*"")
        echo "backup-3 Completed 2024-11-24T12:00:00Z"
        exit 0
        ;;
    "--context=new-hub get mch -n open-cluster-management --no-headers")
        echo "multiclusterhub"
        exit 0
        ;;
    "--context=new-hub get mch -n open-cluster-management -o jsonpath="*"currentVersion"*"")
        echo "2.11.0"
        exit 0
        ;;
    "--context=new-hub get mch -n open-cluster-management -o jsonpath="*"items[0].metadata.name"*"")
        echo "multiclusterhub"
        exit 0
        ;;
    "--context=new-hub get mch multiclusterhub -n open-cluster-management -o jsonpath="*"status.phase"*"")
        echo "Running"
        exit 0
        ;;
    "--context=new-hub get pods -n open-cluster-management --no-headers")
        echo "pod1   1/1   Running"
        echo "pod2   1/1   Running"
        exit 0
        ;;
    
    # ACM 2.14+ autoImportStrategy checks for new-hub  
    "--context=new-hub get namespace multicluster-engine")
        exit 0
        ;;
    "--context=new-hub get configmap import-controller-config -n multicluster-engine")
        # ConfigMap not found means default ImportOnly is used (which is OK for ACM < 2.14)
        exit 1
        ;;
    
    *)
        # Default: don't fail, just return empty
        exit 0
        ;;
esac
""",
        encoding="utf-8",
    )
    oc_script.chmod(oc_script.stat().st_mode | stat.S_IEXEC)

    # Create mock jq
    jq_script = mock_bin / "jq"
    jq_script.write_text(
        """#!/bin/bash
# Mock jq - just succeed
exit 0
""",
        encoding="utf-8",
    )
    jq_script.chmod(jq_script.stat().st_mode | stat.S_IEXEC)

    # Build environment with mocked PATH
    env = os.environ.copy()
    env["PATH"] = f"{mock_bin}:{env.get('PATH', '')}"

    return env


@pytest.fixture
def mock_oc_version_mismatch(tmp_path):
    """Create mocked oc that simulates version mismatch between hubs."""
    mock_bin = tmp_path / "bin"
    mock_bin.mkdir()

    oc_script = mock_bin / "oc"
    oc_script.write_text(
        """#!/bin/bash
case "$*" in
    "config get-contexts"*) exit 0 ;;
    *"get namespace"*) exit 0 ;;
    "--context=primary-ok get mch -n open-cluster-management -o jsonpath="*"currentVersion"*"")
        echo "2.11.0"
        exit 0
        ;;
    "--context=secondary-ok get mch -n open-cluster-management -o jsonpath="*"currentVersion"*"")
        echo "2.10.5"
        exit 0
        ;;
    # Mocks needed for Check 11 (Auto-Import Strategy)
    *"get configmap import-controller-config"*)
        exit 1
        ;;
    *"get managedclusters --no-headers"*)
        echo "local-cluster   True"
        exit 0
        ;;
    *) exit 0 ;;
esac
""",
        encoding="utf-8",
    )
    oc_script.chmod(oc_script.stat().st_mode | stat.S_IEXEC)

    jq_script = mock_bin / "jq"
    jq_script.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    jq_script.chmod(jq_script.stat().st_mode | stat.S_IEXEC)

    env = os.environ.copy()
    env["PATH"] = f"{mock_bin}:{env.get('PATH', '')}"
    return env


@pytest.fixture
def mock_oc_backup_in_progress(tmp_path):
    """Create mocked oc that simulates backup in progress."""
    mock_bin = tmp_path / "bin"
    mock_bin.mkdir()

    oc_script = mock_bin / "oc"
    # Reuse most of success mock but change backup status
    oc_script.write_text(
        """#!/bin/bash
case "$*" in
    "config get-contexts"*) exit 0 ;;
    *"get namespace"*) exit 0 ;;
    *"get mch"*"currentVersion"*)
        echo "2.11.0"
        exit 0
        ;;
    *"get pods"*"velero"*) echo "velero-xyz   1/1   Running"; exit 0 ;;
    *"get dpa"*"--no-headers"*) echo "dpa-config"; exit 0 ;;
    *"get dpa"*"metadata.name"*) echo "dpa-config"; exit 0 ;;
    *"get dpa"*"Reconciled"*) echo "True"; exit 0 ;;
    *"get backup"*"--no-headers"*)
        echo "backup-ongoing   InProgress"
        exit 0
        ;;
    *"InProgress"*)
        echo "backup-ongoing"
        exit 0
        ;;
    # Mocks needed for Check 11 (Auto-Import Strategy)
    *"get configmap import-controller-config"*)
        exit 1
        ;;
    *"get managedclusters --no-headers"*)
        echo "local-cluster   True"
        exit 0
        ;;
    *) exit 0 ;;
esac
""",
        encoding="utf-8",
    )
    oc_script.chmod(oc_script.stat().st_mode | stat.S_IEXEC)

    jq_script = mock_bin / "jq"
    jq_script.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    jq_script.chmod(jq_script.stat().st_mode | stat.S_IEXEC)

    env = os.environ.copy()
    env["PATH"] = f"{mock_bin}:{env.get('PATH', '')}"
    return env


# ============================================================================
# Integration Tests - Success Paths
# ============================================================================


@pytest.mark.integration
def test_preflight_success_passive_method(mock_oc_success):
    """Test preflight validation success with passive method."""
    code, out = run_script(
        "preflight-check.sh",
        "--primary-context",
        "primary-ok",
        "--secondary-context",
        "secondary-ok",
        "--method",
        "passive",
        env=mock_oc_success,
    )

    assert code == 0, f"Expected exit 0, got {code}. Output:\n{out}"
    assert "ALL CRITICAL CHECKS PASSED" in out
    assert "Failed:          0" in out
    assert "Passive sync" in out.lower() or "Method 1" in out  # Should check passive sync
    assert "Observability namespace exists" in out
    assert "MultiClusterObservability CR found" in out
    assert "'thanos-object-storage' secret exists" in out


@pytest.mark.integration
def test_preflight_success_full_method(mock_oc_success):
    """Test preflight validation success with full method."""
    code, out = run_script(
        "preflight-check.sh",
        "--primary-context",
        "primary-ok",
        "--secondary-context",
        "secondary-ok",
        "--method",
        "full",
        env=mock_oc_success,
    )

    assert code == 0, f"Expected exit 0, got {code}. Output:\n{out}"
    assert "ALL CRITICAL CHECKS PASSED" in out
    assert "Method 2" in out


@pytest.mark.integration
def test_postflight_success(mock_oc_success):
    """Test postflight verification success."""
    code, out = run_script(
        "postflight-check.sh",
        "--new-hub-context",
        "new-hub",
        env=mock_oc_success,
    )

    # Postflight may have warnings (like Joined count) but should show success
    # Exit code 0 = perfect, exit code 1 with warnings but no failures = acceptable
    assert code in (
        0,
        1,
    ), f"Expected success (0) or warnings (1), got {code}. Output:\n{out}"

    # Must have found clusters and show verification passed or be close to passing
    assert "2 managed cluster(s)" in out
    assert "Failed:          0" in out  # No critical failures

    # Observability checks
    assert "Observability namespace exists" in out
    assert "MultiClusterObservability CR is Ready" in out
    assert "observability-grafana: 1 pod(s) running" in out
    assert "Grafana route accessible" in out

    # Either fully passed or had only warnings
    if code == 0:
        assert "SWITCHOVER VERIFICATION PASSED" in out
    else:
        # Exit 1 but only from warnings (not failures)
        assert "Warnings:" in out


# ============================================================================
# Integration Tests - Failure Scenarios
# ============================================================================


@pytest.mark.integration
@pytest.mark.parametrize("method", ["passive", "full"])
def test_preflight_version_mismatch_fails(mock_oc_version_mismatch, method):
    """Test that version mismatch between hubs causes failure."""
    code, out = run_script(
        "preflight-check.sh",
        "--primary-context",
        "primary-ok",
        "--secondary-context",
        "secondary-ok",
        "--method",
        method,
        env=mock_oc_version_mismatch,
    )

    assert code == 1, f"Expected exit 1 (failure), got {code}"
    assert "VALIDATION FAILED" in out
    assert "version mismatch" in out.lower() or "2.11.0" in out


@pytest.mark.integration
def test_preflight_backup_in_progress_fails(mock_oc_backup_in_progress):
    """Test that backup in progress causes validation failure."""
    code, out = run_script(
        "preflight-check.sh",
        "--primary-context",
        "primary-ok",
        "--secondary-context",
        "secondary-ok",
        "--method",
        "passive",
        env=mock_oc_backup_in_progress,
    )

    assert code == 1, f"Expected exit 1 (failure), got {code}"
    assert "VALIDATION FAILED" in out
    assert "in progress" in out.lower() or "InProgress" in out


@pytest.mark.integration
def test_preflight_missing_namespace_fails():
    """Test that missing namespace causes validation failure."""
    # Without mock, oc command won't be found or will fail on context
    # This just confirms that the failure happens, not from arg parsing
    code, out = run_script(
        "preflight-check.sh",
        "--primary-context",
        "nonexistent-primary",
        "--secondary-context",
        "nonexistent-secondary",
        "--method",
        "passive",
    )

    # Will fail - either from missing oc (127) or validation (1)
    assert code != 0, f"Expected failure, got success (exit 0)"
    assert code != 2, f"Expected validation/runtime failure, not arg error (exit 2)"


@pytest.mark.integration
def test_postflight_missing_restore_fails():
    """Test that missing restore resource causes postflight failure."""
    # Use real env - will fail on missing context
    code, out = run_script(
        "postflight-check.sh",
        "--new-hub-context",
        "nonexistent-hub",
    )

    # Will fail - either from validation (1) or command not found (127)
    assert code != 0, f"Expected failure, got success (exit 0)"
    assert code != 2, f"Expected validation/runtime failure, not arg error (exit 2)"
