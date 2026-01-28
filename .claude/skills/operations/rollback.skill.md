# Rollback Procedure

Guide the operator through rolling back to the primary hub if switchover issues occur.

> **Reference**: [docs/ACM_SWITCHOVER_RUNBOOK.md — Rollback Procedure](../../../docs/ACM_SWITCHOVER_RUNBOOK.md#rollback-procedure-if-needed)

---

## When to Rollback

Consider rollback when:
- Managed clusters not connecting to new hub after 15+ minutes
- Critical observability failures on new hub
- Unexpected application/policy issues after switchover
- Stakeholder decision to abort switchover

**⚠️ Rollback becomes more complex after:**
- Backups have been enabled on new hub
- Old hub has been decommissioned
- Significant time has passed (24+ hours)

---

## Conversation Flow

Ask: **"At which step did the switchover fail or need to abort?"**

**Decision Tree:**
- During activation (Steps 1-5) → Full rollback possible
- After post-activation (Steps 6-10) → Rollback with caution
- After decommission (Step 14) → Rollback NOT possible, forward recovery only
- Which restore method was used? → Affects which restore to delete

---

## Step 1: Delete/Stop Activation on Secondary Hub

### Identify which restore was created

```bash
oc get restore.cluster.open-cluster-management.io -n open-cluster-management-backup --context <secondary>
```

### Delete the activation restore

**If using Method 1 (passive restore activation):**
```bash
# Delete patched passive restore OR new activation restore
oc delete restore.cluster.open-cluster-management.io restore-acm-passive-sync \
  -n open-cluster-management-backup --context <secondary> --ignore-not-found

oc delete restore.cluster.open-cluster-management.io restore-acm-activate \
  -n open-cluster-management-backup --context <secondary> --ignore-not-found
```

**If using Method 2 (full restore):**
```bash
oc delete restore.cluster.open-cluster-management.io restore-acm-full \
  -n open-cluster-management-backup --context <secondary> --ignore-not-found
```

---

## Step 2: Re-enable Primary Hub

### Remove disable-auto-import annotations

```bash
for cluster in $(oc get managedcluster.cluster.open-cluster-management.io -o name --context <primary> | grep -v local-cluster); do
  echo "Removing annotation from $cluster"
  oc annotate $cluster import.open-cluster-management.io/disable-auto-import- --context <primary>
done
```

### Verify annotations removed

```bash
oc get managedcluster.cluster.open-cluster-management.io --context <primary> \
  -o custom-columns=NAME:.metadata.name,DISABLE-IMPORT:.metadata.annotations.import\\.open-cluster-management\\.io/disable-auto-import
# DISABLE-IMPORT column should be empty or <none>
```

---

## Step 3: Restart Thanos Compactor on Primary

```bash
oc scale statefulset observability-thanos-compact \
  -n open-cluster-management-observability --context <primary> --replicas=1
```

### Verify compactor running

```bash
oc get pods -n open-cluster-management-observability --context <primary> \
  -l app.kubernetes.io/name=thanos-compact
```

---

## Step 4: Re-enable Observatorium API (If Paused)

> Only if you paused it in Step 3 of switchover

```bash
oc scale deployment observability-observatorium-api \
  -n open-cluster-management-observability --context <primary> --replicas=2
```

### Verify API running

```bash
oc get pods -n open-cluster-management-observability --context <primary> \
  -l app.kubernetes.io/name=observatorium-api
```

---

## Step 5: Unpause BackupSchedule on Primary

### For ACM 2.12+:

```bash
BACKUP_SCHEDULE_NAME=$(oc get backupschedule.cluster.open-cluster-management.io \
  -n open-cluster-management-backup --context <primary> \
  -o jsonpath='{.items[0].metadata.name}')

oc patch backupschedule.cluster.open-cluster-management.io "$BACKUP_SCHEDULE_NAME" \
  -n open-cluster-management-backup --context <primary> \
  --type='merge' -p '{"spec":{"paused":false}}'
```

### For ACM 2.11:

Set the BackupSchedule name (if not already set):
```bash
BACKUP_SCHEDULE_NAME=$(oc get backupschedule.cluster.open-cluster-management.io \
  -n open-cluster-management-backup --context <primary> \
  -o jsonpath='{.items[0].metadata.name}')
```

Re-apply saved YAML:
```bash
yq 'del(.metadata.uid, .metadata.resourceVersion, .metadata.managedFields, .status)' \
  "${BACKUP_SCHEDULE_NAME}.yaml" | oc apply --context <primary> -f -
```

---

## Step 6: Wait for Clusters to Reconnect

```bash
# Watch cluster status (Ctrl+C to exit)
oc get managedcluster.cluster.open-cluster-management.io --context <primary> -w
```

**Timeline:** Clusters should show Available within 5-10 minutes.

### Verify all clusters connected

```bash
oc get managedcluster.cluster.open-cluster-management.io --context <primary> \
  -o custom-columns='NAME:.metadata.name,AVAILABLE:.status.conditions[?(@.type=="ManagedClusterConditionAvailable")].status'
```

---

## Step 7 (Optional): Recreate Passive Sync on Secondary

> If you want to maintain DR readiness for future switchover attempts

```bash
oc apply --context <secondary> -f - <<EOF
apiVersion: cluster.open-cluster-management.io/v1beta1
kind: Restore
metadata:
  name: restore-acm-passive-sync
  namespace: open-cluster-management-backup
spec:
  cleanupBeforeRestore: CleanupRestored
  syncRestoreWithNewBackups: true
  veleroManagedClustersBackupName: skip
  veleroCredentialsBackupName: latest
  veleroResourcesBackupName: latest
EOF
```

### Verify passive sync running

```bash
oc get restore.cluster.open-cluster-management.io restore-acm-passive-sync \
  -n open-cluster-management-backup --context <secondary> -w
# Wait for Phase="Enabled"
```

---

## What If...

### "Clusters not reconnecting to primary after rollback"

Check if annotations were properly removed:
```bash
oc get managedcluster.cluster.open-cluster-management.io --context <primary> -o yaml | grep disable-auto-import
```

If annotations remain, remove them:
```bash
oc annotate managedcluster.cluster.open-cluster-management.io <cluster-name> \
  import.open-cluster-management.io/disable-auto-import- --context <primary>
```

### "Clusters connected to BOTH hubs"

This is a split-brain scenario:
1. Determine which hub should be primary
2. On the hub that should NOT be primary, scale down ACM controllers:
   ```bash
   oc scale deployment -n open-cluster-management --all --replicas=0 --context <wrong-hub>
   ```
3. Wait for clusters to stabilize on correct hub
4. Re-enable controllers on wrong hub OR decommission it

### "Primary hub Thanos shows errors after restart"

Check storage connectivity:
```bash
oc logs -n open-cluster-management-observability statefulset/observability-thanos-compact --context <primary>
```

Common issues:
- Storage backend connectivity
- Object storage bucket permissions
- Compaction conflicts (wait for automatic resolution)

### "Can I rollback after decommissioning?"

**No.** If old hub was decommissioned (Step 14 completed):
- MCH has been deleted
- ManagedClusters have been deleted from old hub
- Recovery requires rebuilding the hub

**Forward recovery options:**
1. Fix issues on new hub
2. Perform new full restore from another backup
3. Rebuild old hub and perform fresh switchover

---

## Rollback Verification Checklist

| Check | Status | Notes |
|-------|--------|-------|
| Activation restore deleted | ✅/❌ | |
| Annotations removed | ✅/❌ | |
| Thanos compactor running | ✅/❌ | |
| Observatorium API running | ✅/❌ | |
| BackupSchedule unpaused | ✅/❌ | |
| Clusters connected | ✅/❌ | |
| Metrics flowing | ✅/❌ | |
| Passive sync recreated (optional) | ✅/N/A | |

---

## Post-Rollback Actions

1. **Document the failure** — Record what went wrong for future attempts
2. **Investigate root cause** — Check logs, events, and connectivity
3. **Notify stakeholders** — Switchover was rolled back
4. **Plan retry** — Address issues before next attempt
