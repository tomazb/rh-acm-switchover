# Activate Full Restore (Method 2)

Guide the operator through Method 2 switchover: one-time full restore when passive sync is not running.

> **Reference**: [docs/ACM_SWITCHOVER_RUNBOOK.md — Method 2](../../../docs/ACM_SWITCHOVER_RUNBOOK.md#method-2-one-time-full-restore-no-prior-passive-sync)

---

## When to Use Method 2

- Passive sync was NOT running on secondary hub
- Bringing up a new hub from scratch
- Primary hub is inaccessible (disaster recovery)
- Passive sync is broken and needs fresh restore

---

## Conversation Flow

Ask: **"Is your primary hub accessible?"**

**Decision Tree:**
- ✅ Primary accessible → Perform optional prep steps (F1-F4)
- ❌ Primary inaccessible → Skip to Step F5 (Full Restore)

---

## Optional Prep Steps (Primary Hub Accessible)

### F1: Pause BackupSchedule on Primary

See [pause-backups.skill.md](pause-backups.skill.md) for version-specific instructions.

### F2: Disable Auto-Import on Primary

```bash
for cluster in $(oc get managedcluster.cluster.open-cluster-management.io -o name --context <primary> | grep -v local-cluster); do
  oc annotate $cluster import.open-cluster-management.io/disable-auto-import='' --context <primary>
done
```

### F3: Stop Thanos Compactor on Primary

```bash
oc scale statefulset observability-thanos-compact \
  -n open-cluster-management-observability --context <primary> --replicas=0
```

### F4a (ACM 2.14+ ImportOnly): Apply immediate-import on Secondary

> Prefer this when the destination hub uses the default `ImportOnly` strategy

```bash
oc get managedcluster.cluster.open-cluster-management.io -o name --context <secondary> | \
  grep -v '/local-cluster$' | \
  xargs -I{} oc annotate {} import.open-cluster-management.io/immediate-import='' --overwrite --context <secondary>
```

### F4b (ACM 2.14+): Set ImportAndSync on Secondary (optional)

> Only if secondary has existing managed clusters and you plan to switch back later

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

---

## Step F5: Create Full Restore on Secondary Hub

> ⚠️ **CRITICAL**: This restores credentials, resources, AND managed clusters in one operation

### Verify backups are available

```bash
oc get backup.velero.io -n open-cluster-management-backup --context <secondary> \
  --sort-by=.metadata.creationTimestamp | tail -5
```

**Decision Tree:**
- ✅ Recent backups visible → Proceed
- ❌ No backups or old timestamps → Check BackupStorageLocation connectivity

### Create full restore

```bash
oc apply --context <secondary> -f - <<EOF
apiVersion: cluster.open-cluster-management.io/v1beta1
kind: Restore
metadata:
  name: restore-acm-full
  namespace: open-cluster-management-backup
spec:
  cleanupBeforeRestore: CleanupRestored
  veleroManagedClustersBackupName: latest
  veleroCredentialsBackupName: latest
  veleroResourcesBackupName: latest
EOF
```

### Monitor restore progress

```bash
# Watch status (Ctrl+C to exit when Finished)
oc get restore.cluster.open-cluster-management.io restore-acm-full \
  -n open-cluster-management-backup --context <secondary> -w
```

### Check for errors

```bash
oc describe restore.cluster.open-cluster-management.io restore-acm-full \
  -n open-cluster-management-backup --context <secondary>
# Review Events section
```

---

## Expected Timeline

Full restore typically takes longer than passive activation:

| Phase | Duration |
|-------|----------|
| Credentials restore | 2-5 minutes |
| Resources restore | 5-15 minutes |
| Managed clusters restore | 5-10 minutes |
| **Total** | **15-30 minutes** |

---

## What If...

### "Restore stuck in Running state for >30 minutes"

See [troubleshooting/restore-stuck.skill.md](../troubleshooting/restore-stuck.skill.md)

### "Some Velero restores show PartiallyFailed"

Check which items failed:
```bash
# Get Velero restore names from ACM restore status
oc get restore.cluster.open-cluster-management.io restore-acm-full \
  -n open-cluster-management-backup --context <secondary> -o yaml | grep velero

# Check specific Velero restore
oc describe restore.velero.io <velero-restore-name> \
  -n open-cluster-management-backup --context <secondary>
```

Common partial failures:
- Already-existing resources (usually safe to ignore)
- Missing CRDs (install required operators first)

### "No backups found on secondary"

Check storage connectivity:
```bash
oc get backupstoragelocation.velero.io -n open-cluster-management-backup --context <secondary> \
  -o custom-columns=NAME:.metadata.name,PHASE:.status.phase,MESSAGE:.status.message
```

If not Available:
- Check S3/storage credentials
- Verify network connectivity to storage backend
- Check Velero pod logs

### "Primary is down - can I still restore?"

Yes! As long as:
- Secondary can access the same storage backend
- Recent backups exist in storage
- OADP/Velero is configured on secondary

Proceed directly to Step F5.

---

## Confirmation

Before proceeding, confirm:
- [ ] Restore shows `Phase=Finished`
- [ ] No critical errors in events
- [ ] All three Velero restores (credentials, resources, managed-clusters) completed

---

## Next Step

Proceed to: [verify-switchover.skill.md](verify-switchover.skill.md) (Post-Activation Verification)
