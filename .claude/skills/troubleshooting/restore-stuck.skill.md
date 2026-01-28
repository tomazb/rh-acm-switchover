# Troubleshoot: Restore Stuck in Running State

Diagnose and resolve ACM Restore resources stuck in "Running" state.

> **Reference**: [docs/ACM_SWITCHOVER_RUNBOOK.md — Troubleshooting](../../../docs/ACM_SWITCHOVER_RUNBOOK.md#issue-restore-stuck-in-running-state)

---

## Symptoms

- Restore shows `Phase: Running` for extended period (>30 minutes)
- Progress appears stalled
- No errors visible in status

```bash
oc get restore.cluster.open-cluster-management.io -n open-cluster-management-backup --context <secondary>
# NAME                   PHASE     MESSAGE
# restore-acm-activate   Running   ...
```

---

## Diagnostic Decision Tree

### 1. Check Restore Details

```bash
RESTORE_NAME="restore-acm-activate"  # or restore-acm-passive-sync, restore-acm-full

oc describe restore.cluster.open-cluster-management.io "$RESTORE_NAME" \
  -n open-cluster-management-backup --context <secondary>
```

**Look for:**
- `Status.VeleroManagedClustersRestoreName` — underlying Velero restore
- `Events` section — any errors or warnings
- `Status.Phase` and `Status.Message` — current state

---

### 2. Check Underlying Velero Restores

```bash
# List Velero restores
oc get restore.velero.io -n open-cluster-management-backup --context <secondary>

# Get detailed status for each
oc describe restore.velero.io -n open-cluster-management-backup --context <secondary>
```

**Decision Tree:**
- Velero restore `Completed` but ACM restore stuck → ACM operator issue
- Velero restore `InProgress` → Check Velero logs
- Velero restore `PartiallyFailed` → Check failure details
- Velero restore `Failed` → Major issue, check logs

---

### 3. Check Velero Pod Logs

```bash
oc logs -n open-cluster-management-backup deployment/velero -c velero --context <secondary> | tail -100
```

**Common errors:**
- `"error restoring resource"` → Resource conflicts
- `"backup not found"` → Backup storage issues
- `"timeout"` → Slow restore or connectivity issues

---

### 4. Check ACM Backup Controller Logs

```bash
oc logs -n open-cluster-management-backup deployment/cluster-backup-chart-clusterbackup \
  --context <secondary> | tail -100
```

**Look for:**
- Reconciliation errors
- Resource creation failures
- Timeout messages

---

### 5. Check BackupStorageLocation Health

```bash
oc get backupstoragelocation.velero.io -n open-cluster-management-backup --context <secondary> \
  -o custom-columns=NAME:.metadata.name,PHASE:.status.phase,LAST-VALIDATED:.status.lastValidationTime
```

**Decision Tree:**
- ✅ Phase=Available, recent validation → Storage OK
- ⚠️ Phase=Available, stale validation → Check connectivity
- ❌ Phase=Unavailable → Storage backend issue

---

## Common Issues and Fixes

### Issue: Velero Restore PartiallyFailed

**Get failure details:**
```bash
VELERO_RESTORE=$(oc get restore.velero.io -n open-cluster-management-backup --context <secondary> \
  --sort-by=.metadata.creationTimestamp -o name | tail -n1)

oc get $VELERO_RESTORE -n open-cluster-management-backup --context <secondary> \
  -o jsonpath='{.status.errors}'

# Or full describe
oc describe $VELERO_RESTORE -n open-cluster-management-backup --context <secondary>
```

**Common partial failures:**
- `"resource already exists"` → Usually safe, resource was already restored
- `"CRD not found"` → Missing operator on secondary hub
- `"forbidden"` → RBAC issue

**Resolution:** For non-critical partial failures, ACM restore may still complete. Wait and monitor.

---

### Issue: Backup Not Found

```bash
# Check if backups are visible
oc get backup.velero.io -n open-cluster-management-backup --context <secondary>
```

**If no backups visible:**
```bash
# Check storage connectivity
oc get backupstoragelocation.velero.io -n open-cluster-management-backup --context <secondary> -o yaml

# Check Velero can sync
oc logs -n open-cluster-management-backup deployment/velero -c velero --context <secondary> | grep -i "sync\|backup"
```

**Resolution:**
- Verify S3/storage credentials
- Check network connectivity to storage
- Verify bucket name and path

---

### Issue: Restore Timeout

**For very large restores (many managed clusters):**

```bash
# Check how many resources are being restored
oc get $VELERO_RESTORE -n open-cluster-management-backup --context <secondary> \
  -o jsonpath='{.status.progress}'
```

**Resolution:** Large restores can take 30-60 minutes. If progress is incrementing, wait.

---

### Issue: Resource Conflicts

**Check for conflicting resources:**
```bash
# Look for existing resources that might conflict
oc get managedcluster.cluster.open-cluster-management.io --context <secondary>
oc get secret -n open-cluster-management-backup --context <secondary> | grep -v Opaque
```

**Resolution:**
- Delete conflicting resources before restore
- Or use `cleanupBeforeRestore: CleanupRestored` in restore spec

---

## Resolution Steps

### Option 1: Wait (If Progress Is Being Made)

If Velero restore shows incremental progress, continue waiting. Large restores can take 60+ minutes.

### Option 2: Delete and Recreate Restore

```bash
# Delete stuck restore
oc delete restore.cluster.open-cluster-management.io "$RESTORE_NAME" \
  -n open-cluster-management-backup --context <secondary>

# Wait for cleanup
sleep 30

# Recreate (example for passive activation)
oc apply --context <secondary> -f - <<EOF
apiVersion: cluster.open-cluster-management.io/v1beta1
kind: Restore
metadata:
  name: restore-acm-activate
  namespace: open-cluster-management-backup
spec:
  cleanupBeforeRestore: CleanupRestored
  veleroManagedClustersBackupName: latest
  veleroCredentialsBackupName: skip
  veleroResourcesBackupName: skip
EOF
```

### Option 3: Fix Underlying Issue First

If specific error identified:
1. Fix the issue (storage, RBAC, missing CRD)
2. Delete stuck restore
3. Recreate restore

---

## Verification

```bash
# Watch for phase change to Finished
oc get restore.cluster.open-cluster-management.io "$RESTORE_NAME" \
  -n open-cluster-management-backup --context <secondary> -w
```

**Expected completion:**
- Passive activation: 5-15 minutes
- Full restore: 20-40 minutes

---

## Escalation

If restore fails repeatedly:

1. **Collect diagnostics:**
   ```bash
   # Velero debug logs
   oc logs -n open-cluster-management-backup deployment/velero -c velero --context <secondary> > velero-logs.txt
   
   # ACM backup controller logs
   oc logs -n open-cluster-management-backup deployment/cluster-backup-chart-clusterbackup --context <secondary> > backup-controller-logs.txt
   
   # Full must-gather
   oc adm must-gather --image=quay.io/stolostron/must-gather:latest --context <secondary>
   ```

2. **Check Velero troubleshooting docs:**
   - [Velero Troubleshooting](https://velero.io/docs/main/troubleshooting/)

3. **Consider alternative approach:**
   - If passive restore failing, try full restore
   - If activation failing, check if clusters can be manually imported

---

## Prevention

For future switchovers:
- Verify backups complete successfully before switchover
- Ensure storage backend is healthy and accessible from both hubs
- Test restore in non-production environment first
