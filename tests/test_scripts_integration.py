"""Integration tests for bash scripts with mocked oc/jq commands.

These tests use temporary mock binaries in PATH to simulate various cluster states
and validate the scripts' logic end-to-end without requiring actual cluster access.

Test categories:
- Success paths: All validation checks pass
- Failure scenarios: Version mismatches, backups in progress, missing resources
"""

import os
import re
import shutil
import stat
import subprocess
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
        timeout=30,
    )
    output = strip_ansi(proc.stdout)
    return proc.returncode, output


def write_shared_jq_mock(mock_bin: Path) -> None:
    """Create a mock jq that handles minimal cases and delegates to real jq."""
    jq_script = mock_bin / "jq"
    jq_script.write_text(
        """#!/bin/bash
# Mock jq that handles minimal cases and delegates to real jq when available

ALL_ARGS=("$@")

# Get the jq expression (first non-flag argument) without shifting args
EXPR=""
skip_next=0
for ((i=0; i<${#ALL_ARGS[@]}; i++)); do
    arg="${ALL_ARGS[$i]}"
    if [[ $skip_next -gt 0 ]]; then
        skip_next=$((skip_next-1))
        continue
    fi
    case "$arg" in
        -r|--raw-output) ;;
        --arg)
            skip_next=2
            ;;
        -*) ;;
        *)
            EXPR="$arg"
            break
            ;;
    esac
done

# Read stdin
INPUT=$(cat)

# Handle common expressions; otherwise delegate to real jq
case "$EXPR" in
    ".items | length")
        # Return 0 for empty/missing arrays to keep numeric comparisons stable
        echo "0"
        ;;
    *)
        if [[ -n "${REAL_JQ:-}" ]]; then
            printf "%s" "$INPUT" | "$REAL_JQ" "${ALL_ARGS[@]}" 2>/dev/null || echo ""
        else
            echo ""
        fi
        ;;
esac
""",
        encoding="utf-8",
    )
    jq_script.chmod(jq_script.stat().st_mode | stat.S_IEXEC)


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
    
    # ACM version checks - match both short (mch) and full API group names
    *"--context=primary-ok"*"get "*"multiclusterhub"*"-n open-cluster-management"*"currentVersion"*)
        echo "2.11.0"
        exit 0
        ;;
    *"--context=secondary-ok"*"get "*"multiclusterhub"*"-n open-cluster-management"*"currentVersion"*)
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
    
    # DPA checks - match both short 'dpa' and full API group name
    *"--context=primary-ok"*"get "*"dataprotectionapplication"*"-n open-cluster-management-backup"*"--no-headers"*)
        echo "dpa-config   Reconciled"
        exit 0
        ;;
    *"--context=secondary-ok"*"get "*"dataprotectionapplication"*"-n open-cluster-management-backup"*"--no-headers"*)
        echo "dpa-config   Reconciled"
        exit 0
        ;;
    *"--context=primary-ok"*"get "*"dataprotectionapplication"*"-n open-cluster-management-backup"*"items[0].metadata.name"*)
        echo "dpa-config"
        exit 0
        ;;
    *"--context=secondary-ok"*"get "*"dataprotectionapplication"*"-n open-cluster-management-backup"*"items[0].metadata.name"*)
        echo "dpa-config"
        exit 0
        ;;
    *"--context=primary-ok"*"get "*"dataprotectionapplication"*"dpa-config"*"-n open-cluster-management-backup"*"Reconciled"*)
        echo "True"
        exit 0
        ;;
    *"--context=secondary-ok"*"get "*"dataprotectionapplication"*"dpa-config"*"-n open-cluster-management-backup"*"Reconciled"*)
        echo "True"
        exit 0
        ;;
    
    # BackupStorageLocation checks (Check 7) - match both short and full API group names
    *"--context=primary-ok"*"get "*"backupstoragelocation"*"-n open-cluster-management-backup"*"--no-headers"*)
        echo "default   Available"
        exit 0
        ;;
    *"--context=secondary-ok"*"get "*"backupstoragelocation"*"-n open-cluster-management-backup"*"--no-headers"*)
        echo "default   Available"
        exit 0
        ;;
    *"get "*"backupstoragelocation"*"open-cluster-management-backup"*"status.phase"*)
        echo "Available"
        exit 0
        ;;
    *"--context=primary-ok"*"get "*"backupstoragelocation"*"default"*"-n open-cluster-management-backup"*"-o json"*)
        cat << 'BSL_JSON'
{"status":{"conditions":[{"type":"Available","status":"True"}]}}
BSL_JSON
        exit 0
        ;;
    *"--context=secondary-ok"*"get "*"backupstoragelocation"*"default"*"-n open-cluster-management-backup"*"-o json"*)
        cat << 'BSL_JSON'
{"status":{"conditions":[{"type":"Available","status":"True"}]}}
BSL_JSON
        exit 0
        ;;
    *"--context=secondary-ok"*"get "*"backupstoragelocation"*"-n open-cluster-management-backup"*"-o json"*)
        cat << 'BSL_JSON'
{"items":[{"metadata":{"name":"default"},"status":{"phase":"Available","conditions":[{"type":"Available","status":"True"}]}}]}
BSL_JSON
        exit 0
        ;;
    
    # Cluster Health checks (Check 8) - nodes and clusteroperators
    "--context=primary-ok get nodes --no-headers")
        echo "master-0   Ready   control-plane   10d   v1.28.0"
        echo "master-1   Ready   control-plane   10d   v1.28.0"
        echo "master-2   Ready   control-plane   10d   v1.28.0"
        echo "worker-0   Ready   worker          10d   v1.28.0"
        exit 0
        ;;
    "--context=secondary-ok get nodes --no-headers")
        echo "master-0   Ready   control-plane   10d   v1.28.0"
        echo "master-1   Ready   control-plane   10d   v1.28.0"
        echo "master-2   Ready   control-plane   10d   v1.28.0"
        echo "worker-0   Ready   worker          10d   v1.28.0"
        exit 0
        ;;
    *"--context=primary-ok get nodes"*"-o json"*)
        # JSON output for nodes - all Ready
        cat << 'NODES_JSON'
{"items":[
  {"metadata":{"name":"master-0"},"status":{"conditions":[{"type":"Ready","status":"True"}]}},
  {"metadata":{"name":"master-1"},"status":{"conditions":[{"type":"Ready","status":"True"}]}},
  {"metadata":{"name":"master-2"},"status":{"conditions":[{"type":"Ready","status":"True"}]}},
  {"metadata":{"name":"worker-0"},"status":{"conditions":[{"type":"Ready","status":"True"}]}}
]}
NODES_JSON
        exit 0
        ;;
    *"--context=secondary-ok get nodes"*"-o json"*)
        # JSON output for nodes - all Ready
        cat << 'NODES_JSON'
{"items":[
  {"metadata":{"name":"master-0"},"status":{"conditions":[{"type":"Ready","status":"True"}]}},
  {"metadata":{"name":"master-1"},"status":{"conditions":[{"type":"Ready","status":"True"}]}},
  {"metadata":{"name":"master-2"},"status":{"conditions":[{"type":"Ready","status":"True"}]}},
  {"metadata":{"name":"worker-0"},"status":{"conditions":[{"type":"Ready","status":"True"}]}}
]}
NODES_JSON
        exit 0
        ;;
    "--context=primary-ok get clusteroperators --no-headers")
        echo "authentication                      True   False   False   10d"
        echo "console                             True   False   False   10d"
        echo "etcd                                True   False   False   10d"
        echo "kube-apiserver                      True   False   False   10d"
        echo "machine-config                      True   False   False   10d"
        exit 0
        ;;
    "--context=secondary-ok get clusteroperators --no-headers")
        echo "authentication                      True   False   False   10d"
        echo "console                             True   False   False   10d"
        echo "etcd                                True   False   False   10d"
        echo "kube-apiserver                      True   False   False   10d"
        echo "machine-config                      True   False   False   10d"
        exit 0
        ;;
    *"get clusteroperators"*"-o json"*)
        # JSON output for clusteroperators - all healthy
        cat << 'CO_JSON'
{"items":[
  {"metadata":{"name":"authentication"},"status":{"conditions":[{"type":"Available","status":"True"},{"type":"Degraded","status":"False"},{"type":"Progressing","status":"False"}]}},
  {"metadata":{"name":"console"},"status":{"conditions":[{"type":"Available","status":"True"},{"type":"Degraded","status":"False"},{"type":"Progressing","status":"False"}]}},
  {"metadata":{"name":"etcd"},"status":{"conditions":[{"type":"Available","status":"True"},{"type":"Degraded","status":"False"},{"type":"Progressing","status":"False"}]}}
]}
CO_JSON
        exit 0
        ;;
    
    # ClusterVersion checks (for upgrade status)
    *"get clusterversion version -o json"*)
        # ClusterVersion output - stable, no upgrade in progress
        cat << 'CV_JSON'
{"status":{"desired":{"version":"4.14.10"},"conditions":[{"type":"Available","status":"True"},{"type":"Progressing","status":"False","message":"Cluster version is 4.14.10"},{"type":"Degraded","status":"False"}]}}
CV_JSON
        exit 0
        ;;
    
    # BackupSchedule checks (for useManagedServiceAccount) - MUST be before backup patterns!
    *"--context=primary-ok"*"get "*"backupschedule"*"-n open-cluster-management-backup"*"-o json"*)
        cat << 'BACKUPSCHEDULE_JSON'
{"items":[{"metadata":{"name":"schedule-acm"},"spec":{"useManagedServiceAccount":true,"veleroSchedule":"0 */4 * * *"}}]}
BACKUPSCHEDULE_JSON
        exit 0
        ;;
    
    # Backup checks - match both short 'backup' and full API group name
    *"--context=primary-ok"*"get "*"backup"*"-n open-cluster-management-backup"*"--no-headers"*)
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
    *"get "*"backup"*"backup-20241124"*"-n open-cluster-management-backup"*"status.phase"*)
        # Latest backup phase query
        echo "Finished"
        exit 0
        ;;
    
    # ClusterDeployment checks
    "--context=primary-ok get clusterdeployment --all-namespaces --no-headers")
        # No Hive clusters
        exit 0
        ;;
    
    # Passive sync restore check - discovery by syncRestoreWithNewBackups=true (match both short and full API group)
    # Order matters: more specific patterns first
    *"--context=secondary-ok"*"get "*"restore"*"restore-acm-passive-sync"*"-n open-cluster-management-backup"*"jsonpath"*"phase"*)
        echo "Enabled"
        exit 0
        ;;
    *"--context=secondary-ok"*"get "*"restore"*"-n open-cluster-management-backup"*"-o json"*)
        cat << 'RESTORE_JSON'
{"items":[{"metadata":{"name":"restore-acm-passive-sync"},"spec":{"syncRestoreWithNewBackups":true},"status":{"phase":"Enabled"}}]}
RESTORE_JSON
        exit 0
        ;;
    # Fallback check for well-known name (not needed since discovery finds it, but keep for robustness)
    *"--context=secondary-ok"*"get "*"restore"*"restore-acm-passive-sync"*"-n open-cluster-management-backup"*)
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
    # ManagedCluster checks - match both short and full API group names
    *"--context=new-hub"*"get "*"managedcluster"*"--no-headers"*)
        echo "local-cluster   True"
        echo "cluster1        True"
        echo "cluster2        True"
        exit 0
        ;;
    *"--context=new-hub"*"get "*"managedcluster"*"-o json"*)
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
    # Simple get managedclusters without options (may be either old or new format)
    *"--context=new-hub"*"get "*"managedcluster"*)
        echo "NAME           STATUS   AGE"
        echo "local-cluster  True     30d"
        echo "cluster1       True     20d"
        echo "cluster2       True     15d"
        exit 0
        ;;
    "--context=new-hub get namespace open-cluster-management-observability")
        exit 0
        ;;
    *"--context=new-hub"*"get "*"multiclusterobservability"*"observability"*"-n open-cluster-management-observability"*)
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
    *"--context=new-hub"*"get "*"backupschedule"*"-n open-cluster-management-backup"*"--no-headers"*)
        echo "schedule-acm"
        exit 0
        ;;
    *"--context=new-hub"*"get "*"backupschedule"*"-n open-cluster-management-backup"*"items[0].metadata.name"*)
        echo "schedule-acm"
        exit 0
        ;;
    *"--context=new-hub"*"get "*"backupschedule"*"schedule-acm"*"-n open-cluster-management-backup"*"spec.paused"*)
        echo "false"
        exit 0
        ;;
    *"--context=new-hub"*"get "*"backup"*"-n open-cluster-management-backup"*"--sort-by"*"--no-headers"*)
        echo "backup-1"
        echo "backup-2"
        echo "backup-3"
        exit 0
        ;;
    *"--context=new-hub"*"get "*"backup"*"-n open-cluster-management-backup"*"--sort-by"*)
        echo "backup-3 Completed 2024-11-24T12:00:00Z"
        exit 0
        ;;
    *"--context=new-hub"*"get "*"backupstoragelocation"*"-n open-cluster-management-backup"*"--no-headers"*)
        echo "default   Available"
        exit 0
        ;;
    *"--context=new-hub"*"get "*"backupstoragelocation"*"default"*"-n open-cluster-management-backup"*"status.phase"*)
        echo "Available"
        exit 0
        ;;
    *"--context=new-hub"*"get "*"backupstoragelocation"*"default"*"-n open-cluster-management-backup"*"-o json"*)
        cat << 'BSL_JSON'
{"status":{"conditions":[{"type":"Available","status":"True"}]}}
BSL_JSON
        exit 0
        ;;
    *"--context=new-hub"*"get "*"backupstoragelocation"*"-n open-cluster-management-backup"*"-o json"*)
        cat << 'BSL_JSON'
{"items":[{"metadata":{"name":"default"},"status":{"phase":"Available","conditions":[{"type":"Available","status":"True"}]}}]}
BSL_JSON
        exit 0
        ;;
    *"get "*"restore"*"lastMessage"*)
        echo ""
        exit 0
        ;;
    # MCH checks for new-hub - match both short and full API group names
    *"--context=new-hub"*"get "*"multiclusterhub"*"-n open-cluster-management"*"--no-headers"*)
        echo "multiclusterhub"
        exit 0
        ;;
    *"--context=new-hub"*"get "*"multiclusterhub"*"-n open-cluster-management"*"currentVersion"*)
        echo "2.11.0"
        exit 0
        ;;
    *"--context=new-hub"*"get "*"multiclusterhub"*"-n open-cluster-management"*"items[0].metadata.name"*)
        echo "multiclusterhub"
        exit 0
        ;;
    *"--context=new-hub"*"get "*"multiclusterhub"*"multiclusterhub"*"-n open-cluster-management"*"status.phase"*)
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

        # Create shared mock jq for consistency with other fixtures
        write_shared_jq_mock(mock_bin)

    # Build environment with mocked PATH
    env = os.environ.copy()
    env["REAL_JQ"] = shutil.which("jq") or ""
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
    # Match both short (mch) and full API group names for MCH version check
    *"--context=primary-ok"*"get "*"multiclusterhub"*"-n open-cluster-management"*"currentVersion"*)
        echo "2.11.0"
        exit 0
        ;;
    *"--context=secondary-ok"*"get "*"multiclusterhub"*"-n open-cluster-management"*"currentVersion"*)
        echo "2.10.5"
        exit 0
        ;;
    # OADP checks - return empty output with exit 0 to avoid pipefail
    *"get pods"*"velero"*"--no-headers"*) exit 0 ;;
    # DPA checks - return empty output with exit 0 to avoid pipefail
    *"get "*"dataprotectionapplication"*"--no-headers"*) exit 0 ;;
    *"get "*"dataprotectionapplication"*"metadata.name"*) exit 0 ;;
    *"get dpa"*"--no-headers"*) exit 0 ;;
    *"get dpa"*"metadata.name"*) exit 0 ;;
    # Backup checks - return empty output with exit 0 to avoid pipefail
    *"get "*"backup"*"--no-headers"*) exit 0 ;;
    *"InProgress"*) exit 0 ;;
    # Passive restore checks
    *"get restore"*"-o json"*) echo '{"items":[]}'; exit 0 ;;
    *"get restore restore-acm-passive-sync"*) exit 1 ;;
    # ClusterDeployment
    *"get "*"clusterdeployment"*"--all-namespaces --no-headers"*) exit 0 ;;
    # Mocks needed for Check 11 (Auto-Import Strategy)
    *"get configmap import-controller-config"*)
        echo 'Error from server (NotFound): configmaps "import-controller-config" not found' >&2
        exit 1
        ;;
    *"get "*"managedcluster"*"--no-headers"*)
        echo "local-cluster   True"
        exit 0
        ;;
    *) exit 0 ;;
esac
""",
        encoding="utf-8",
    )
    oc_script.chmod(oc_script.stat().st_mode | stat.S_IEXEC)

    write_shared_jq_mock(mock_bin)

    env = os.environ.copy()
    env["REAL_JQ"] = shutil.which("jq") or ""
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
    # Match both short (mch) and full API group names for MCH version
    *"get "*"multiclusterhub"*"currentVersion"*)
        echo "2.11.0"
        exit 0
        ;;
    *"get pods"*"velero"*) echo "velero-xyz   1/1   Running"; exit 0 ;;
    *"get "*"dataprotectionapplication"*"--no-headers"*) echo "dpa-config"; exit 0 ;;
    *"get "*"dataprotectionapplication"*"metadata.name"*) echo "dpa-config"; exit 0 ;;
    *"get "*"dataprotectionapplication"*"Reconciled"*) echo "True"; exit 0 ;;
    *"get dpa"*"--no-headers"*) echo "dpa-config"; exit 0 ;;
    *"get dpa"*"metadata.name"*) echo "dpa-config"; exit 0 ;;
    *"get dpa"*"Reconciled"*) echo "True"; exit 0 ;;
    *"get "*"backup"*"--no-headers"*)
        echo "backup-ongoing   InProgress"
        exit 0
        ;;
    *"get "*"backup"*"jsonpath"*"InProgress"*)
        # Return backup name for in-progress check
        echo "backup-ongoing"
        exit 0
        ;;
    *"get "*"backup"*"sort-by"*)
        # Latest backup query
        echo "backup-ongoing"
        exit 0
        ;;
    *"get "*"backup"*"backup-ongoing"*"jsonpath"*"phase"*)
        # Latest backup phase check
        echo "InProgress"
        exit 0
        ;;
    # ClusterDeployment
    *"get "*"clusterdeployment"*"--all-namespaces --no-headers"*) exit 0 ;;
    # Passive restore checks (for method=passive tests)
    *"get restore"*"-o json"*) echo '{"items":[{"metadata":{"name":"restore-acm-passive-sync"},"spec":{"syncRestoreWithNewBackups":true},"status":{"phase":"Enabled"}}]}'; exit 0 ;;
    *"get restore restore-acm-passive-sync"*"phase"*) echo "Enabled"; exit 0 ;;
    # Mocks needed for Check 11 (Auto-Import Strategy)
    *"get configmap import-controller-config"*)
        echo 'Error from server (NotFound): configmaps "import-controller-config" not found' >&2
        exit 1
        ;;
    *"get "*"managedcluster"*"--no-headers"*)
        echo "local-cluster   True"
        exit 0
        ;;
    *"get "*"managedcluster"*"-o json"*)
        echo '{"items":[{"metadata":{"name":"local-cluster"}}]}'
        exit 0
        ;;
    # BackupSchedule checks
    *"get "*"backupschedule"*"metadata.name"*)
        echo "schedule-acm"
        exit 0
        ;;
    *"get "*"backupschedule"*"paused"*)
        echo "false"
        exit 0
        ;;
    *"get "*"backupschedule"*"phase"*)
        echo "Enabled"
        exit 0
        ;;
    # BackupStorageLocation checks
    *"get "*"backupstoragelocation"*)
        echo "default   Available"
        exit 0
        ;;
    # Cluster health checks
    *"get nodes"*"-o json"*)
        echo '{"items":[{"status":{"conditions":[{"type":"Ready","status":"True"}]}}]}'
        exit 0
        ;;
    *"get nodes"*)
        echo "node1   Ready"
        exit 0
        ;;
    *"get clusteroperator"*)
        echo "NAME   VERSION   AVAILABLE"
        exit 0
        ;;
    *) exit 0 ;;
esac
""",
        encoding="utf-8",
    )
    oc_script.chmod(oc_script.stat().st_mode | stat.S_IEXEC)

    # Create shared mock jq for consistency with other fixtures
    write_shared_jq_mock(mock_bin)

    env = os.environ.copy()
    env["REAL_JQ"] = shutil.which("jq") or ""
    env["PATH"] = f"{mock_bin}:{env.get('PATH', '')}"
    # Set short wait times so test doesn't timeout waiting for backup completion
    env["BACKUP_IN_PROGRESS_WAIT_SECONDS"] = "2"
    env["BACKUP_IN_PROGRESS_POLL_SECONDS"] = "1"
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
    assert "autoImportStrategy not applicable" in out


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
