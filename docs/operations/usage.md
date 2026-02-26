# ACM Switchover - Detailed Usage Examples

## Complete Switchover Workflow

### Step 1: Pre-Validation

Before attempting any switchover, always run validation first:

```bash
python acm_switchover.py \
  --validate-only \
  --primary-context primary-acm-hub \
  --secondary-context secondary-acm-hub \
  --method passive
```

Optional: add `--argocd-check` to include Argo CD discovery and a summary of which Argo CD Applications touch ACM resources (read-only; no changes). If `--skip-gitops-check` is set, `--argocd-check` is ignored.

Note: `--secondary-context` is required for all switchover operations unless you are using `--decommission`.

**What this checks:**
- ✓ Required namespaces exist on both hubs
- ✓ ACM versions match between hubs
- ✓ OADP operator installed on both hubs
- ✓ DataProtectionApplication configured correctly
- ✓ Latest backup completed successfully
- ✓ **CRITICAL**: All ClusterDeployments have `preserveOnDelete=true`
- ✓ Passive sync restore is current (Method 1 only)
- ✓ ACM Observability detected (if present)
- ✓ GitOps marker detection warnings for ArgoCD/Flux (unless `--skip-gitops-check`)
- ✓ Argo CD discovery and ACM-impact summary on both hubs (when `--argocd-check` is set)

Note: GitOps marker detection is heuristic. The generic label `app.kubernetes.io/instance` is flagged as `UNRELIABLE` when present and should not be treated as a definitive GitOps signal.

**Expected output:**
```
Pre-flight Validation Summary: 15/15 checks passed
All critical validations passed!
```

If any critical validations fail, **DO NOT PROCEED** until issues are resolved.

### Step 2: Dry-Run

Preview exactly what the script will do:

```bash
python acm_switchover.py \
  --dry-run \
  --primary-context primary-acm-hub \
  --secondary-context secondary-acm-hub \
  --old-hub-action secondary \
  --method passive
```

**What you'll see:**
- All planned actions (patch, create, delete operations)
- No actual changes will be made
- Gives confidence in what will happen

**Sample dry-run output:**
```
[DRY-RUN] Would patch backupschedules/schedule-rhacm with: {'spec': {'paused': True}}
[DRY-RUN] Would patch managedclusters/cluster-1 with annotations
[DRY-RUN] Would scale statefulset observability-thanos-compact to 0 replicas
```

### Step 3: Execute Switchover

When ready, execute the actual switchover:

```bash
python acm_switchover.py \
  --primary-context primary-acm-hub \
  --secondary-context secondary-acm-hub \
  --method passive \
  --old-hub-action secondary \
  --verbose
```

**The `--old-hub-action` parameter is required** and controls what happens to the old primary hub:
- `secondary`: Set up passive sync restore for failback capability (recommended)
- `decommission`: Remove ACM components from old hub automatically
- `none`: Leave unchanged for manual handling

**Timeline (typical execution):**
- Pre-flight validation: 2-3 minutes
- Primary preparation: 1-2 minutes
- Activation: 5-15 minutes (waiting for restore)
- Post-activation: 10-15 minutes (cluster connections)
- Finalization: depends on BackupSchedule cadence (typical 5-10 minutes; longer for hourly/daily schedules)
- **Total: ~30-45 minutes**

> **Safety warning:** Do **NOT** re-enable Thanos Compactor or Observatorium API on the old hub after switchover.
> Both hubs share the same object storage backend; re-enabling on the old hub can cause data corruption and split-brain.
> Only re-enable on the old hub if you are switching back and have shut down these components on the current primary first.

**Argo CD / GitOps:** If you use Argo CD to manage ACM resources, enable `--argocd-manage` so the tool pauses auto-sync on ACM-touching Applications during primary prep (on both hubs). Applications are left paused by default; resume only after updating Git/desired state for the new hub, using `--argocd-resume-after-switchover` during finalization or `--argocd-resume-only` as a standalone step. Resume treats already-resumed apps (marker mismatch) as idempotent no-op and fails only when an app cannot be restored for actionable reasons.

**State file tracking:**
The script creates `.state/switchover-<primary>__<secondary>.json` tracking progress:
```json
{
  "version": "1.0",
  "current_phase": "post_activation_verification",
  "completed_steps": [
    {"name": "pause_backup_schedule", "timestamp": "2025-11-18T10:15:00Z"},
    {"name": "disable_auto_import", "timestamp": "2025-11-18T10:16:30Z"},
    ...
  ]
}
```

### Step 4: Resume from Interruption

If the script is interrupted (Ctrl+C, network issue, etc.), simply re-run the same command:

```bash
# Same command as Step 3 - it will resume automatically
python acm_switchover.py \
  --primary-context primary-acm-hub \
  --secondary-context secondary-acm-hub \
  --method passive \
  --old-hub-action secondary \
  --verbose
```

The script will:
- Load state from `.state/switchover-<primary>__<secondary>.json`
- Skip already-completed steps
- Continue from the last successful step

**Example resume output:**
```
Step already completed: pause_backup_schedule
Step already completed: disable_auto_import
Step already completed: scale_down_thanos
Continuing with: verify_passive_sync
```

## Method Comparison

### Method 1: Continuous Passive Restore (Recommended)

**Use when:**
- You have passive sync already configured and running
- You want minimal downtime
- Secondary hub has been continuously syncing backup data

```bash
python acm_switchover.py \
  --primary-context primary-acm-hub \
  --secondary-context secondary-acm-hub \
  --method passive \
  --old-hub-action secondary
```

**Activation options (Method 1):**
- **Default (Option A):** Patch the passive sync restore in place.
- **Option B:** Delete passive sync and create `restore-acm-activate`:

```bash
python acm_switchover.py \
  --primary-context primary-acm-hub \
  --secondary-context secondary-acm-hub \
  --method passive \
  --activation-method restore \
  --old-hub-action secondary
```

> **Note:** The restore controller may briefly treat a deleted passive restore as still active.
> The tool now waits for deletion to fully propagate before creating `restore-acm-activate`.
> If you run this manually, wait for deletion to complete and re-create the activation restore
> if the phase shows `FinishedWithErrors`.

> **Note:** `--activation-method` applies only to `--method passive`.

**Advantages:**
- Faster activation (data already restored)
- Lower risk (passive sync proven working)
- Minimal data loss window

### Method 2: One-Time Full Restore

**Use when:**
- Setting up a new secondary hub
- Passive sync was not previously configured
- You need a complete fresh restore

```bash
python acm_switchover.py \
  --primary-context primary-acm-hub \
  --secondary-context secondary-acm-hub \
  --method full \
  --old-hub-action decommission
```

**Considerations:**
- Longer restore time (all resources restored)
- Requires `latest` backups for credentials, resources, and managed clusters
- CleanupBeforeRestore applied

## Advanced Scenarios

### Scenario 1: Switchover Without Observability

If ACM Observability is not deployed, the script automatically detects this and skips related steps:

```bash
python acm_switchover.py \
  --primary-context primary-acm-hub \
  --secondary-context secondary-acm-hub \
  --method passive \
  --old-hub-action secondary
```

**Automatic detection:**
```
✓ ACM Observability: not detected (optional component)
Skipping Thanos compactor scaling (Observability not detected)
Skipping Observability verification (not detected)
```

### Scenario 2: Force Skip Observability Checks

Even if Observability is detected, you can skip related steps:

```bash
python acm_switchover.py \
  --primary-context primary-acm-hub \
  --secondary-context secondary-acm-hub \
  --method passive \
  --old-hub-action secondary \
  --skip-observability-checks
```

**Use case:** Observability issues shouldn't block cluster migration.

### Scenario 3: Disable Observability on Old Hub (Non-Decommission)

If you are keeping the old hub as a secondary and want Observability disabled there,
you can request deletion of the MultiClusterObservability resource:

```bash
python acm_switchover.py \
  --primary-context primary-acm-hub \
  --secondary-context secondary-acm-hub \
  --method passive \
  --old-hub-action secondary \
  --disable-observability-on-secondary
```

**Notes:**
- Only valid when `--old-hub-action secondary` (not for decommission).
- If the MCO is managed by GitOps (ArgoCD/Flux), coordinate deletion to avoid drift.

### Scenario 4: Different ACM Versions (2.11 vs 2.12+)

The script auto-detects ACM version and adjusts BackupSchedule handling:

**ACM 2.12+:**
```
Using spec.paused for ACM 2.12.0
BackupSchedule schedule-rhacm paused successfully
```

**ACM 2.11:**
```
ACM 2.11.3 requires deleting BackupSchedule
BackupSchedule schedule-rhacm deleted (saved to state)
```

No manual intervention needed - version-aware handling is automatic.

## Returning to Original Hub (Reverse Switchover)

To return to the original hub after a switchover, perform a **reverse switchover** by swapping the primary and secondary contexts. This is the recommended approach as it uses the same proven switchover workflow.

> **Prerequisite:** Your original switchover must have used `--old-hub-action secondary` to set up passive sync on the old hub. This is why `secondary` is the recommended value for this option.

### Step 1: Verify Old Hub Has Passive Sync

On the original primary hub (now acting as secondary):

```bash
oc get restore restore-acm-passive-sync -n open-cluster-management-backup
# Should show Phase="Enabled"
```

### Step 2: Run Reverse Switchover

Simply swap the `--primary-context` and `--secondary-context` values:

```bash
# Original switchover was:
# python acm_switchover.py --primary-context hub-A --secondary-context hub-B ...

# Reverse switchover (swap contexts):
python acm_switchover.py \
  --primary-context hub-B \
  --secondary-context hub-A \
  --method passive \
  --old-hub-action secondary
```

### Step 3: Verify Clusters Reconnect

Wait 5-10 minutes and verify clusters are `Available=True` on the original hub:

```bash
oc get managedclusters -o custom-columns=NAME:.metadata.name,AVAILABLE:.status.conditions[?(@.type=="ManagedClusterConditionAvailable")].status
```

> **Tip:** The same validate → dry-run → execute workflow applies to reverse switchovers.

## Decommission Old Hub

After confirming successful switchover, decommission the old primary hub:

```bash
python acm_switchover.py \
  --decommission \
  --primary-context old-primary-hub
```

**Interactive prompts:**
```
DECOMMISSION MODE - This will remove ACM from the old hub!

Are you sure you want to proceed with decommissioning the old hub? [y/N]: y

Delete MultiClusterObservability resource? [y/N]: y

Delete ManagedCluster resources (excluding local-cluster)? [y/N]: y

Delete MultiClusterHub resource? (This will remove all ACM components) [y/N]: y
```

**Timeline:**
- Delete Observability: 3-5 minutes
- Delete ManagedClusters: 1-2 minutes
- Delete MultiClusterHub: 15-20 minutes

**Non-interactive mode (automation):**
```bash
python acm_switchover.py \
  --decommission \
  --primary-context old-primary-hub \
  --non-interactive
```

⚠️ **WARNING:** Non-interactive mode proceeds without confirmation and can only be used together with `--decommission`. Use only in fully automated environments.

## Troubleshooting

### Issue: ClusterDeployments Missing preserveOnDelete

**Validation fails with:**
```
✗ ClusterDeployment preserveOnDelete: ClusterDeployments missing preserveOnDelete=true: 
  cluster1/cluster1-cd, cluster2/cluster2-cd
```

**Fix before proceeding:**
```bash
# For each ClusterDeployment listed:
oc patch clusterdeployment <name> -n <namespace> \
  --type='merge' \
  -p '{"spec":{"preserveOnDelete":true}}'
```

Then re-run validation.

### Issue: Backup in Progress

**Validation fails with:**
```
✗ Backup status: backup(s) in progress: acm-resources-backup-20251118103045
```

**Resolution:**
Wait for backup to complete, then re-run. Check backup status:
```bash
oc get backup -n open-cluster-management-backup
```

### Issue: Clusters Stuck in "Pending Import"

**After activation, some clusters not connecting:**

**Check:**
1. Wait 10-15 minutes (auto-import can be slow)
2. Verify import secrets exist:
   ```bash
   oc get secret -n <cluster-namespace> | grep import
   ```
3. Check ManagedCluster events:
   ```bash
   oc describe managedcluster <cluster-name>
   ```

### Issue: No Metrics in Grafana After Switchover

**Metrics not appearing after activation:**

**Solution:**
Verify observatorium-api was restarted:
```bash
oc get pods -n open-cluster-management-observability | grep observatorium-api
```

If not restarted, manually restart:
```bash
oc rollout restart deployment/observability-observatorium-api \
  -n open-cluster-management-observability
```

Wait 10 minutes for metrics collection to resume.

### Argo CD Detection and Management

When Argo CD manages ACM resources (BackupSchedule, Restore, ManagedCluster, etc.), auto-sync can fight switchover steps. The tool can detect and optionally manage Argo CD:

**Detection only (no changes):**
```bash
python acm_switchover.py \
  --validate-only \
  --primary-context primary-acm-hub \
  --secondary-context secondary-acm-hub \
  --argocd-check
```

**Pause auto-sync during switchover (recommended if GitOps manages ACM):**
```bash
python acm_switchover.py \
  --primary-context primary-acm-hub \
  --secondary-context secondary-acm-hub \
  --method passive \
  --old-hub-action secondary \
  --argocd-manage
```

Applications that touch ACM namespaces/kinds are paused (auto-sync removed) and left paused by default. State is stored in the switchover state file.

**Resume auto-sync after updating Git for the new hub:**
- During finalization (opt-in): add `--argocd-resume-after-switchover` to the same run.
- Standalone later: `--argocd-resume-only` with `--primary-context` and `--secondary-context` to restore from state.

Note: `--argocd-manage`, `--argocd-resume-after-switchover`, and `--argocd-resume-only` are not compatible with `--validate-only` (they imply restore/pause behavior). `--argocd-resume-only` is also not compatible with `--decommission` or `--setup`. If `--argocd-manage` was run with `--dry-run`, resume is blocked because the pause was not actually applied—re-run without `--dry-run` to generate resumable state.

⚠️ Only resume after Git/desired state reflects the **new** hub; otherwise Argo CD can revert switchover changes.

**Bash alternative:** Use `./scripts/preflight-check.sh --argocd-check` and `./scripts/argocd-manage.sh` for detection and pause/resume with a state file. See [scripts/README.md](../../scripts/README.md).

### Issue: Script Hangs During Restore

**Restore phase stuck in "Running":**

**Check Velero logs:**
```bash
oc logs -n open-cluster-management-backup deployment/velero --tail=100
```

**Check restore details:**
```bash
oc describe restore restore-acm-passive-sync -n open-cluster-management-backup
```

Look for specific errors and resolve before re-running.

## State Management

### View Current State

```bash
cat .state/switchover-<primary>__<secondary>.json | python -m json.tool
```

### Reset State (Start Fresh)

⚠️ **Caution:** Only use if you need to completely restart:

```bash
python acm_switchover.py \
  --reset-state \
  --validate-only \
  --primary-context primary-acm-hub \
  --secondary-context secondary-acm-hub
```

This deletes all progress tracking and starts from scratch.

### Use Custom State File

Track multiple switchovers separately:

```bash
python acm_switchover.py \
  --primary-context hub-a \
  --secondary-context hub-b \
  --state-file .state/hub-a-to-hub-b.json
```

**Path Validation Rules**:
- Relative paths are allowed and recommended (e.g., `.state/myfile.json`)
- Absolute paths permitted under:
  - `/tmp/` directory
  - `/var/` directory  
  - Current workspace directory
  - User home directory
- Path traversal (`../`) and command injection characters are blocked
- See `docs/reference/validation-rules.md` for complete details

**Examples**:
```bash
# Relative path (recommended)
--state-file .state/custom-state.json

# Absolute path under workspace
--state-file /home/user/acm-project/.state/state.json

# Temporary file
--state-file /tmp/switchover-state.json
```

## Best Practices

### 1. Always Validate First
```bash
# Never skip this step
--validate-only
```

### 2. Test with Dry-Run
```bash
# Preview before executing
--dry-run
```

### 3. Enable Verbose Logging
```bash
# Better debugging and audit trail
--verbose
```

### 4. Structured Logging
```bash
# Use JSON format for log aggregation systems
--log-format json
```

### 5. Maintain State Files
- Keep `.state/` directory in version control (optional)
- Provides audit trail of switchovers
- Enables resume from failure

### 6. Plan Maintenance Window
- Estimated time: 30-60 minutes
- Brief managed cluster disconnect (5-10 minutes)
- Inform stakeholders beforehand

### 7. Verify Post-Switchover
```bash
# On new secondary hub, check:
oc get managedclusters
oc get backup -n open-cluster-management-backup

# Verify all clusters show AVAILABLE=True
```

### 8. Test Reverse Switchover
In a test environment, practice reverse switchover (swapping contexts) before production use.

## Complete Example: Production Switchover

```bash
#!/bin/bash
# Production ACM Switchover Script

set -e  # Exit on error

PRIMARY="prod-acm-primary"
SECONDARY="prod-acm-secondary"
OLD_HUB_ACTION="secondary"  # Options: secondary, decommission, none
STATE_FILE=".state/prod-switchover-$(date +%Y%m%d-%H%M%S).json"

echo "=== ACM Production Switchover ==="
echo "Started at: $(date)"
echo "Old hub action: $OLD_HUB_ACTION"
echo ""

# Step 1: Validation
echo "Step 1: Running validation..."
python acm_switchover.py \
  --validate-only \
  --primary-context "$PRIMARY" \
  --secondary-context "$SECONDARY" \
  --verbose

read -p "Validation passed. Continue with dry-run? (y/N): " confirm
[[ "$confirm" != "y" ]] && exit 0

# Step 2: Dry-run
echo "Step 2: Running dry-run..."
python acm_switchover.py \
  --dry-run \
  --primary-context "$PRIMARY" \
  --secondary-context "$SECONDARY" \
  --old-hub-action "$OLD_HUB_ACTION" \
  --state-file "$STATE_FILE" \
  --verbose

read -p "Dry-run complete. Execute switchover? (y/N): " confirm
[[ "$confirm" != "y" ]] && exit 0

# Step 3: Execute
echo "Step 3: Executing switchover..."
python acm_switchover.py \
  --primary-context "$PRIMARY" \
  --secondary-context "$SECONDARY" \
  --old-hub-action "$OLD_HUB_ACTION" \
  --state-file "$STATE_FILE" \
  --verbose

echo ""
echo "=== Switchover Completed ==="
echo "Completed at: $(date)"
echo "State file: $STATE_FILE"
echo "Old hub action: $OLD_HUB_ACTION"
echo ""
echo "Next steps:"
echo "  1. Verify clusters on new hub"
echo "  2. Test application functionality"
echo "  3. Inform stakeholders"
if [[ "$OLD_HUB_ACTION" == "none" ]]; then
  echo "  4. Manually handle old hub (action was 'none')"
fi
```

Save as `run-production-switchover.sh` and execute when ready.
