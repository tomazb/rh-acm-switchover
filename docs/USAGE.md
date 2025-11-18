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

**What this checks:**
- ✓ Required namespaces exist on both hubs
- ✓ ACM versions match between hubs
- ✓ OADP operator installed on both hubs
- ✓ DataProtectionApplication configured correctly
- ✓ Latest backup completed successfully
- ✓ **CRITICAL**: All ClusterDeployments have `preserveOnDelete=true`
- ✓ Passive sync restore is current (Method 1 only)
- ✓ ACM Observability detected (if present)

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
  --verbose
```

**Timeline (typical execution):**
- Pre-flight validation: 2-3 minutes
- Primary preparation: 1-2 minutes
- Activation: 5-15 minutes (waiting for restore)
- Post-activation: 10-15 minutes (cluster connections)
- Finalization: 5-10 minutes (backup verification)
- **Total: ~30-45 minutes**

**State file tracking:**
The script creates `.state/switchover-state.json` tracking progress:
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
  --verbose
```

The script will:
- Load state from `.state/switchover-state.json`
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
  --method passive
```

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
  --method full
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
  --method passive
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
  --skip-observability-checks
```

**Use case:** Observability issues shouldn't block cluster migration.

### Scenario 3: Different ACM Versions (2.11 vs 2.12+)

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

## Rollback Procedure

If issues occur after switchover, rollback to the primary hub:

```bash
python acm_switchover.py \
  --rollback \
  --primary-context primary-acm-hub \
  --secondary-context secondary-acm-hub \
  --state-file .state/switchover-state.json
```

**What rollback does:**
1. Deletes/pauses activation restore on secondary hub
2. Removes disable-auto-import annotations on primary
3. Restarts Thanos compactor on primary (if Observability)
4. Unpauses BackupSchedule on primary

**After rollback:**
```
✓ Rollback completed successfully!
Allow 5-10 minutes for ManagedClusters to reconnect to primary hub
```

Wait 5-10 minutes and verify clusters are `Available=True` on primary.

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

⚠️ **WARNING:** Non-interactive mode proceeds without confirmation. Use only in fully automated environments.

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
cat .state/switchover-state.json | python -m json.tool
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

### 4. Maintain State Files
- Keep `.state/` directory in version control (optional)
- Provides audit trail of switchovers
- Enables rollback with context

### 5. Plan Maintenance Window
- Estimated time: 30-60 minutes
- Brief managed cluster disconnect (5-10 minutes)
- Inform stakeholders beforehand

### 6. Verify Post-Switchover
```bash
# On new secondary hub, check:
oc get managedclusters
oc get backup -n open-cluster-management-backup

# Verify all clusters show AVAILABLE=True
```

### 7. Test Rollback Procedure
In a test environment, practice rollback before production use.

## Complete Example: Production Switchover

```bash
#!/bin/bash
# Production ACM Switchover Script

set -e  # Exit on error

PRIMARY="prod-acm-primary"
SECONDARY="prod-acm-secondary"
STATE_FILE=".state/prod-switchover-$(date +%Y%m%d-%H%M%S).json"

echo "=== ACM Production Switchover ==="
echo "Started at: $(date)"
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
  --state-file "$STATE_FILE" \
  --verbose

read -p "Dry-run complete. Execute switchover? (y/N): " confirm
[[ "$confirm" != "y" ]] && exit 0

# Step 3: Execute
echo "Step 3: Executing switchover..."
python acm_switchover.py \
  --primary-context "$PRIMARY" \
  --secondary-context "$SECONDARY" \
  --state-file "$STATE_FILE" \
  --verbose

echo ""
echo "=== Switchover Completed ==="
echo "Completed at: $(date)"
echo "State file: $STATE_FILE"
echo ""
echo "Next steps:"
echo "  1. Verify clusters on new hub"
echo "  2. Test application functionality"
echo "  3. Inform stakeholders"
echo "  4. Decommission old hub (optional)"
```

Save as `run-production-switchover.sh` and execute when ready.
