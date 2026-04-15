# Restore-Only Mode (Single Hub)

Guide the operator through restoring managed clusters from S3 backups onto a new hub when the original hub is gone.

> **Reference**: [docs/operations/usage.md — Restore-Only Mode](../../../docs/operations/usage.md#restore-only-mode-single-hub)

---

## When to Use

- Original hub is **destroyed, decommissioned, or unreachable**
- Backups exist in S3 from the old hub
- A new hub is ready with ACM installed and BSL pointing to the same S3 bucket
- No primary hub is available for a normal two-hub switchover

---

## Prerequisites Checklist

Ask: **"Let's verify your new hub is ready for restore."**

1. ✅ ACM is installed on the new hub
2. ✅ OADP operator is deployed
3. ✅ BackupStorageLocation (BSL) exists and points to the old hub's S3 bucket
4. ✅ BSL credentials are valid (can access the S3 bucket)
5. ✅ Valid backups exist in the S3 bucket

**Check BSL status:**
```bash
oc get backupstoragelocation -n open-cluster-management-backup --context <new-hub>
```

**Check for available backups:**
```bash
oc get backup -n open-cluster-management-backup --context <new-hub>
```

---

## Conversation Flow

### Step 1: Validate the New Hub

```bash
python acm_switchover.py \
  --restore-only \
  --validate-only \
  --secondary-context <new-hub>
```

**Decision Tree:**
- ✅ All checks pass → Proceed to Step 2
- ❌ BSL check fails → Fix BSL credentials/connectivity first
- ❌ Namespace check fails → Verify ACM and OADP are installed

### Step 2: Dry-Run

```bash
python acm_switchover.py \
  --restore-only \
  --dry-run \
  --secondary-context <new-hub>
```

Review the planned actions. The tool will:
1. Create a one-time full Restore from the latest S3 backup
2. Wait for ManagedClusters to reconnect
3. Verify klusterlet agents on managed clusters
4. Attempt to enable BackupSchedule on the new hub (warns if none found — see Post-Restore section)

### Step 3: Execute Restore

```bash
python acm_switchover.py \
  --restore-only \
  --secondary-context <new-hub>
```

**What happens (no user interaction required):**
1. **Preflight** — validates target hub only (ACM version, BSL, namespaces)
2. **Activation** — creates a one-time full Restore from latest backup
3. **Post-Activation** — waits for ManagedClusters to connect, verifies klusterlet agents
4. **Finalization** — attempts to enable BackupSchedule; warns if none found (ACM excludes BackupSchedule from Velero backups — must be created manually post-restore)

---

## Phase Flow
```text
INIT → PREFLIGHT (secondary-only) → ACTIVATION → POST_ACTIVATION → FINALIZATION (backups-only) → COMPLETED
```

**Skipped phases** (vs. normal switchover):
- PRIMARY_PREP — no primary hub exists
- Primary-side RBAC validation — skipped because primary=None; secondary-hub RBAC checks still run during PREFLIGHT

> Keep runbook and SKILLS synchronized whenever Restore-only PREFLIGHT behavior or primary=None assumptions change.

---

## Post-Restore Verification

After successful restore, verify:

```bash
# Check ManagedClusters are connected
oc get managedcluster --context <new-hub>

# BackupSchedule is NOT restored automatically (ACM excludes it from Velero backups)
# Create it manually — example:
# oc apply -f your-backupschedule.yaml --context <new-hub>
# Then verify:
oc get backupschedule -n open-cluster-management-backup --context <new-hub>

# Check klusterlet agents
oc get klusterletaddonconfig -A --context <new-hub>
```

See [verify-switchover.skill.md](verify-switchover.skill.md) for full post-activation verification.

---

## Troubleshooting

### Clusters Not Reconnecting

If ManagedClusters stay in "Unknown" or "Pending Import" after restore:
- See [pending-import.skill.md](../troubleshooting/pending-import.skill.md)

### Restore Stuck

If the Restore resource stays in "Running" state:
- See [restore-stuck.skill.md](../troubleshooting/restore-stuck.skill.md)

### BSL Connectivity Issues

```bash
# Verify BSL is Available
oc get backupstoragelocation -n open-cluster-management-backup --context <new-hub> -o jsonpath='{.items[0].status.phase}'
```

Expected: `Available`. If not, check S3 credentials and bucket access.

---

## Key Constraints

- `--primary-context` is **not accepted** (no primary hub)
- `--method full` is implied and enforced (passive sync needs a live primary)
- `--old-hub-action` is **not accepted** (no old hub to manage)
- Cannot combine with `--decommission`, `--setup`, or `--argocd-resume-only`
- Can combine with `--argocd-manage`
- `--argocd-resume-after-switchover` is allowed with `--argocd-manage` and cannot be combined with `--validate-only`
- Can combine with `--validate-only` and `--dry-run`

---

## Resumability

Like normal switchover, restore-only tracks state and can resume from the last successful phase:

```bash
# Resume after interruption (same command)
python acm_switchover.py \
  --restore-only \
  --secondary-context <new-hub>
```

State file location: `.state/switchover-restore-only__<new-hub>.json`
