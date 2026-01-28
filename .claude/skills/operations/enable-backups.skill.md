# Enable Backups on New Hub

Guide the operator through enabling BackupSchedule on the newly activated hub to resume regular backups.

> **Reference**: [docs/ACM_SWITCHOVER_RUNBOOK.md — Steps 11-12](../../../docs/ACM_SWITCHOVER_RUNBOOK.md#step-11-enable-backupschedule-on-new-active-hub)

---

## Conversation Flow

### 1. Identify ACM Version

Ask: **"What ACM version is running on your new primary hub?"**

```bash
oc get multiclusterhub -A -o jsonpath='{.items[0].status.currentVersion}' --context <secondary>
```

**Decision Tree:**
- ACM 2.12+ → Use unpause method
- ACM 2.11 → Re-apply saved YAML

---

## ACM 2.12+ Instructions

### Check if BackupSchedule exists

```bash
BACKUP_SCHEDULE_NAME=$(oc get backupschedule.cluster.open-cluster-management.io \
  -n open-cluster-management-backup --context <secondary> \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)

if [ -z "$BACKUP_SCHEDULE_NAME" ]; then
  echo "No BackupSchedule found - need to create one"
else
  echo "Found BackupSchedule: $BACKUP_SCHEDULE_NAME"
fi
```

**Decision Tree:**
- BackupSchedule exists → Unpause it
- No BackupSchedule → Create new one

### Unpause existing BackupSchedule

```bash
oc patch backupschedule.cluster.open-cluster-management.io "$BACKUP_SCHEDULE_NAME" \
  -n open-cluster-management-backup --context <secondary> \
  --type='merge' -p '{"spec":{"paused":false}}'
```

### Verify unpaused

```bash
oc get backupschedule.cluster.open-cluster-management.io "$BACKUP_SCHEDULE_NAME" \
  -n open-cluster-management-backup --context <secondary> \
  -o jsonpath='{.spec.paused}'
# Should return: false (or empty)
```

---

## ACM 2.11 Instructions

### Re-apply saved BackupSchedule YAML

If you saved the YAML from the old hub in [pause-backups.skill.md](pause-backups.skill.md):

```bash
# Option 1: Using yq
yq 'del(.metadata.uid, .metadata.resourceVersion, .metadata.managedFields, .status)' \
  "${BACKUP_SCHEDULE_NAME}.yaml" | oc apply --context <secondary> -f -

# Option 2: Without yq
oc create -f "${BACKUP_SCHEDULE_NAME}.yaml" --context <secondary> --dry-run=client -o yaml | oc apply -f -
```

---

## Create New BackupSchedule (If None Exists)

### Example BackupSchedule

```bash
oc apply --context <secondary> -f - <<EOF
apiVersion: cluster.open-cluster-management.io/v1beta1
kind: BackupSchedule
metadata:
  name: schedule-rhacm
  namespace: open-cluster-management-backup
spec:
  # Schedule in cron format (every 4 hours)
  veleroSchedule: "0 */4 * * *"
  
  # Retention policy - keep backups for 7 days
  veleroTtl: 168h
  
  # Use managed service account for backup operations (ACM 2.11+)
  useManagedServiceAccount: true
EOF
```

Ask: **"What backup schedule do you want?"**

Common options:
- Every 4 hours: `"0 */4 * * *"`
- Every 6 hours: `"0 */6 * * *"`
- Daily at midnight: `"0 0 * * *"`
- Every 12 hours: `"0 0,12 * * *"`

---

## Step 12: Verify Backup Integrity

### Wait for first backup

```bash
# Wait 5-10 minutes, then check newest backups
oc get backup.velero.io -n open-cluster-management-backup --context <secondary> \
  --sort-by=.metadata.creationTimestamp | tail -5
```

### Verify backup completed successfully

```bash
BACKUP_NAME=$(oc get backup.velero.io -n open-cluster-management-backup --context <secondary> \
  --sort-by=.metadata.creationTimestamp -o name | tail -n1 | cut -d/ -f2)

oc get backup.velero.io "$BACKUP_NAME" -n open-cluster-management-backup --context <secondary> \
  -o jsonpath='{.status.phase}'
# Should return: Completed
```

### Verify backup timestamp is recent

```bash
oc get backup.velero.io "$BACKUP_NAME" -n open-cluster-management-backup --context <secondary> \
  -o jsonpath='{.metadata.creationTimestamp}'
# Should be within the last 10 minutes
```

**Decision Tree:**
- ✅ Phase=Completed → Backups working
- ❌ Phase=Failed or PartiallyFailed → Check Velero logs

### Check backup logs if issues

```bash
oc logs -n open-cluster-management-backup deployment/velero -c velero --context <secondary> --since=10m | \
  grep "$BACKUP_NAME" | grep -iE "error|failed"
```

---

## ⚠️ Critical Warning: Old Hub Thanos

> **DO NOT re-enable Thanos Compactor or Observatorium API on the old hub!**

Both hubs share the same object storage. Re-enabling on old hub will cause:
- Data corruption (two compactors writing to same storage)
- Write conflicts
- Split-brain scenarios

**Only re-enable when:**
1. You are switching BACK to that hub as primary
2. You have stopped Thanos on the current active hub first
3. As part of a complete rollback procedure

---

## What If...

### "BackupSchedule shows Phase=BackoffLimit"

Check events for error details:
```bash
oc describe backupschedule.cluster.open-cluster-management.io "$BACKUP_SCHEDULE_NAME" \
  -n open-cluster-management-backup --context <secondary>
```

Common issues:
- OADP operator issues
- Storage backend connectivity
- Missing secrets

### "Velero pod not running"

```bash
oc get pods -n open-cluster-management-backup --context <secondary> | grep velero
```

If not running, check OADP operator:
```bash
oc get pods -n openshift-adp --context <secondary>
oc get dataprotectionapplication -n open-cluster-management-backup --context <secondary> -o yaml
```

### "First backup is taking very long"

Check backup progress:
```bash
oc describe backup.velero.io "$BACKUP_NAME" -n open-cluster-management-backup --context <secondary>
```

First backup after switchover may take longer due to volume of changed resources.

---

## Confirmation

Before proceeding, confirm:
- [ ] BackupSchedule is enabled (not paused)
- [ ] BackupSchedule shows Phase=Enabled
- [ ] First backup completed successfully
- [ ] Old hub Thanos/Observatorium remain scaled to 0

---

## Next Steps

- Notify stakeholders (Step 13) - switchover complete
- Consider [decommission.skill.md](decommission.skill.md) for old hub (Step 14)
- Or keep old hub available for rollback (24-48 hours recommended)
