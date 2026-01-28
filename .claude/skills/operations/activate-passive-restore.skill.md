# Activate Passive Restore (Method 1)

Guide the operator through Method 1 switchover: disable auto-import, stop Thanos, verify sync, and activate managed clusters via passive restore.

> **Reference**: [docs/ACM_SWITCHOVER_RUNBOOK.md — Steps 2-5](../../../docs/ACM_SWITCHOVER_RUNBOOK.md#method-1-continuous-passive-restore-activation-path)

---

## Prerequisites Check

Before starting, confirm:
- [ ] Backups are paused on primary hub (see [pause-backups.skill.md](pause-backups.skill.md))
- [ ] Passive restore is running on secondary hub
- [ ] Both kubeconfig contexts are available

---

## Step 2: Disable Auto-Import on Primary Hub

> This prevents the old hub from trying to recover clusters after switchover

### Apply disable-auto-import annotation

**Batch command (all clusters except local-cluster):**

```bash
for cluster in $(oc get managedcluster.cluster.open-cluster-management.io -o name --context <primary> | grep -v local-cluster); do
  echo "Annotating $cluster"
  oc annotate $cluster import.open-cluster-management.io/disable-auto-import='' --context <primary>
done
```

### Verify annotations applied

```bash
oc get managedcluster.cluster.open-cluster-management.io --context <primary> \
  -o custom-columns=NAME:.metadata.name,DISABLE-IMPORT:.metadata.annotations.import\\.open-cluster-management\\.io/disable-auto-import
```

**Decision Tree:**
- ✅ All clusters (except local-cluster) show annotation → Proceed
- ❌ Some missing → Re-run annotation command for missing clusters

---

## Step 3: Stop Thanos Compactor on Primary Hub

> Prevents write conflicts on shared object storage during switchover

```bash
oc scale statefulset observability-thanos-compact \
  -n open-cluster-management-observability --context <primary> --replicas=0
```

### Verify compactor stopped

```bash
oc get pods -n open-cluster-management-observability --context <primary> \
  -l app.kubernetes.io/name=thanos-compact --field-selector=status.phase=Running --no-headers | wc -l
# Should return: 0
```

### Optional: Stop Observatorium API too

> Recommended to avoid any write contention during switchover window

```bash
oc scale deployment observability-observatorium-api \
  -n open-cluster-management-observability --context <primary> --replicas=0
```

---

## Step 4: Verify Passive Restore on Secondary Hub

### Check passive sync status

```bash
oc get restore.cluster.open-cluster-management.io restore-acm-passive-sync \
  -n open-cluster-management-backup --context <secondary>
```

**Expected output:**
```
NAME                       PHASE     MESSAGE
restore-acm-passive-sync   Enabled   Velero restores have run to completion, restore will continue to sync with new backups
```

### Check last restored backup timestamps

```bash
# Get restored backup names
oc get restore.cluster.open-cluster-management.io restore-acm-passive-sync \
  -n open-cluster-management-backup --context <secondary> \
  -o jsonpath='{.status.veleroCredentialsRestoreName}'

oc get restore.cluster.open-cluster-management.io restore-acm-passive-sync \
  -n open-cluster-management-backup --context <secondary> \
  -o jsonpath='{.status.veleroResourcesRestoreName}'
```

### Compare with latest backups from primary

```bash
for s in $(oc get backup.velero.io -n open-cluster-management-backup --context <primary> -o json \
  | jq -r '.items[].metadata.labels["velero.io/schedule-name"]' | sort -u); do
  echo -n "$s: "
  oc get backup.velero.io -n open-cluster-management-backup --context <primary> \
    -l velero.io/schedule-name="$s" \
    --sort-by=.metadata.creationTimestamp --no-headers | tail -n1 | awk '{print $1 " (" $2 ")"}'
done
```

**Decision Tree:**
- ✅ Passive sync shows latest backups → Proceed to activation
- ⚠️ Sync is behind → Wait 5-10 minutes, recheck
- ❌ Passive sync not running → Consider [activate-full-restore.skill.md](activate-full-restore.skill.md)

---

## Step 4b (ACM 2.14+ with existing clusters): Set ImportAndSync

> Only needed if secondary hub has non-local-cluster ManagedClusters AND you plan to switch back later

Ask: **"Does your secondary hub have existing managed clusters (besides local-cluster)?"**

```bash
oc get managedcluster.cluster.open-cluster-management.io --context <secondary> | grep -v local-cluster | grep -v NAME
```

**Decision Tree:**
- No clusters found → Skip this step
- Clusters exist → Apply ConfigMap:

```bash
oc apply --context <secondary> -f - <<EOF
apiVersion: v1
kind: ConfigMap
metadata:
  name: import-controller-config
  namespace: multicluster-engine
data:
  autoImportStrategy: ImportAndSync
EOF
```

> **Note**: This will be removed in post-activation Step 7

---

## Step 5: Activate Managed Clusters

> ⚠️ **CRITICAL**: This is the point of no return for activation

### Option A: Patch Existing Restore (Recommended)

```bash
oc patch restore.cluster.open-cluster-management.io restore-acm-passive-sync \
  -n open-cluster-management-backup --context <secondary> \
  --type='merge' \
  -p '{"spec":{"veleroManagedClustersBackupName":"latest"}}'
```

### Monitor activation progress

```bash
# Watch for transition to Finished state (Ctrl+C to exit)
oc get restore.cluster.open-cluster-management.io restore-acm-passive-sync \
  -n open-cluster-management-backup --context <secondary> -w
```

**Expected final state:**
```
NAME                       PHASE      MESSAGE
restore-acm-passive-sync   Finished   All Velero restores have run successfully
```

### Alternative Option B: Delete and Create New Restore

If patching fails or you prefer a clean activation:

```bash
# Delete existing
oc delete restore.cluster.open-cluster-management.io restore-acm-passive-sync \
  -n open-cluster-management-backup --context <secondary>

# Create activation restore
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

# Monitor
oc get restore.cluster.open-cluster-management.io restore-acm-activate \
  -n open-cluster-management-backup --context <secondary> -w
```

---

## What If...

### "Restore is stuck in Running state"

See [troubleshooting/restore-stuck.skill.md](../troubleshooting/restore-stuck.skill.md)

### "Patch command returns error"

Check restore exists:
```bash
oc get restore.cluster.open-cluster-management.io -n open-cluster-management-backup --context <secondary>
```

If name differs, adjust command accordingly.

### "I used the wrong restore name"

Find correct name:
```bash
oc get restore.cluster.open-cluster-management.io -n open-cluster-management-backup --context <secondary>
```

Look for one with `syncRestoreWithNewBackups: true` in spec.

---

## Confirmation

Before proceeding, confirm:
- [ ] Restore shows `Phase=Finished`
- [ ] No errors in restore events

---

## Next Step

Proceed to: [verify-switchover.skill.md](verify-switchover.skill.md) (Post-Activation Verification)
