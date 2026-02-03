# Pause Backups on Primary Hub

Guide the operator through pausing BackupSchedule on the primary hub, with version-specific instructions for ACM 2.11 vs 2.12+.

> **Reference**: [docs/ACM_SWITCHOVER_RUNBOOK.md — Step 1](../../../docs/ACM_SWITCHOVER_RUNBOOK.md#step-1-pause-backupschedule-on-primary-hub)

**If Argo CD / GitOps manages the BackupSchedule:** Pause Argo CD auto-sync for ACM-touching Applications first (runbook: "Optional: Pause Argo CD Auto-Sync"), or run the switchover tool with `--argocd-manage` so it pauses those Applications during primary prep. Otherwise GitOps may immediately revert the pause.

---

## Conversation Flow

### 1. Identify ACM Version

Ask: **"What ACM version is running on your primary hub?"**

```bash
oc get multiclusterhub -A -o jsonpath='{.items[0].status.currentVersion}' --context <primary>
```

**Decision Tree:**
- ACM 2.12+ → Use patch method (simpler, reversible)
- ACM 2.11 → Use delete method (save YAML first)

---

## ACM 2.12+ Instructions

### Step 1: Find BackupSchedule Name

```bash
# List BackupSchedules
oc get backupschedule.cluster.open-cluster-management.io -n open-cluster-management-backup --context <primary>

# Set the BackupSchedule name from the list above
BACKUP_SCHEDULE_NAME="<paste-backupschedule-name-here>"

echo "BackupSchedule name: $BACKUP_SCHEDULE_NAME"
```

### Step 2: Pause BackupSchedule

```bash
oc patch backupschedule.cluster.open-cluster-management.io "$BACKUP_SCHEDULE_NAME" \
  -n open-cluster-management-backup --context <primary> \
  --type='merge' -p '{"spec":{"paused":true}}'
```

### Step 3: Verify

```bash
oc get backupschedule.cluster.open-cluster-management.io "$BACKUP_SCHEDULE_NAME" \
  -n open-cluster-management-backup --context <primary> \
  -o jsonpath='{.spec.paused}'
# Should return: true
```

**Decision Tree:**
- ✅ Returns `true` → Proceed to next step
- ❌ Returns `false` or empty → Retry patch command

---

## ACM 2.11 Instructions

### Step 1: Save BackupSchedule YAML First

> ⚠️ **IMPORTANT**: Save before deleting — you'll need this to recreate later

```bash
# Set the BackupSchedule name from the list above
BACKUP_SCHEDULE_NAME="<paste-backupschedule-name-here>"

# Save to file
oc get backupschedule.cluster.open-cluster-management.io "$BACKUP_SCHEDULE_NAME" \
  -n open-cluster-management-backup --context <primary> \
  -o yaml > "${BACKUP_SCHEDULE_NAME}.yaml"

echo "Saved to: ${BACKUP_SCHEDULE_NAME}.yaml"
```

### Step 2: Delete BackupSchedule

```bash
oc delete backupschedule.cluster.open-cluster-management.io "$BACKUP_SCHEDULE_NAME" \
  -n open-cluster-management-backup --context <primary>
```

### Step 3: Verify Deletion

```bash
oc get backupschedule.cluster.open-cluster-management.io -n open-cluster-management-backup --context <primary>
# Should return: No resources found
```

### To Restore Later (if rollback needed)

```bash
# Option 1: Using yq
yq 'del(.metadata.uid, .metadata.resourceVersion, .metadata.managedFields, .status)' \
  "${BACKUP_SCHEDULE_NAME}.yaml" | oc apply -f -

# Option 2: Without yq
oc create -f "${BACKUP_SCHEDULE_NAME}.yaml" --dry-run=client -o yaml | oc apply -f -
```

---

## What If...

### "I can't find any BackupSchedule"

```bash
# Check all namespaces
oc get backupschedule.cluster.open-cluster-management.io -A --context <primary>
```

If none exist:
- Backups may be disabled on this hub
- Check if using external backup mechanism
- Proceed to next step (no action needed here)

### "BackupSchedule shows error status"

Check events:
```bash
oc describe backupschedule.cluster.open-cluster-management.io "$BACKUP_SCHEDULE_NAME" \
  -n open-cluster-management-backup --context <primary>
```

Common issues:
- OADP not configured → Fix before switchover
- Storage backend unavailable → Check BackupStorageLocation

### "I need to verify no backups are running"

```bash
oc get backup.velero.io -n open-cluster-management-backup --context <primary> \
  --field-selector=status.phase=InProgress
# Should return: No resources found
```

If backup in progress:
- Wait for completion (typically 5-15 minutes)
- If urgent, delete the backup CR (this removes only the CR; the backup will NOT be restorable):
  ```bash
  oc delete backup.velero.io <name> -n open-cluster-management-backup --context <primary>
  ```
- After switchover stabilizes, take a fresh backup on the new primary hub

---

## Confirmation

Before proceeding, confirm:
- [ ] BackupSchedule is paused (2.12+) or deleted with YAML saved (2.11)
- [ ] No backups currently in progress
- [ ] YAML file saved securely (for potential rollback)

---

## Next Step

Proceed to: [activate-passive-restore.skill.md](activate-passive-restore.skill.md) (Step 2: Disable Auto-Import)
