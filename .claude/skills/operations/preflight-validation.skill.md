# Preflight Validation

Guide the operator through ACM switchover pre-flight validation with interactive checks and go/no-go decision points.

> **Reference**: [docs/ACM_SWITCHOVER_RUNBOOK.md — Step 0](../../../docs/ACM_SWITCHOVER_RUNBOOK.md#step-0-verify-prerequisites-before-starting-switchover)

---

## Conversation Flow

### 1. Gather Context

Ask the operator:

1. **"Which switchover method are you using?"**
   - **Method 1 (Passive Sync)**: Secondary hub has continuous passive sync running
   - **Method 2 (Full Restore)**: No passive sync, performing one-time full restore

2. **"What are your kubeconfig contexts?"**
   - Primary hub context name (e.g., `hub1-admin`)
   - Secondary hub context name (e.g., `hub2-admin`)

3. **"Is the primary hub accessible?"**
   - If NO and using Method 2: Proceed with secondary-only checks
   - If NO and using Method 1: ⚠️ Consider Method 2 instead

---

## Pre-Flight Checklist

### Critical Safety Check: preserveOnDelete

> ⚠️ **BLOCKER** — This MUST pass before proceeding

```bash
oc get clusterdeployment.hive.openshift.io --all-namespaces \
  -o custom-columns=NAME:.metadata.name,NAMESPACE:.metadata.namespace,PRESERVE:.spec.preserveOnDelete
```

**Decision Tree:**
- ✅ ALL show `true` → Proceed
- ❌ ANY show `false` or `<none>` → **STOP** — Fix with:
  ```bash
  oc patch clusterdeployment.hive.openshift.io <name> -n <namespace> \
    --type='merge' -p '{"spec":{"preserveOnDelete":true}}'
  ```

---

### Backup Verification (Primary Hub)

```bash
oc get backup.velero.io -n open-cluster-management-backup --context <primary>
```

**Check:**
- [ ] Most recent backup shows `Completed`
- [ ] No backups in `InProgress` state
- [ ] Backup timestamp is recent (within expected schedule)

**If backups are failing:** See [troubleshooting/restore-stuck.skill.md](../troubleshooting/restore-stuck.skill.md)

---

### ACM Version Match

```bash
# On primary
oc get multiclusterhub -A -o jsonpath='{.items[0].status.currentVersion}' --context <primary>

# On secondary
oc get multiclusterhub -A -o jsonpath='{.items[0].status.currentVersion}' --context <secondary>
```

**Decision Tree:**
- ✅ Versions match exactly → Proceed
- ⚠️ Minor version differs → Proceed with caution, document
- ❌ Major version differs → **STOP** — Upgrade secondary first

---

### OADP/Velero Status

**Both hubs must have (run on each hub):**

```bash
# BackupStorageLocation available (run on both primary and secondary)
oc get backupstoragelocation.velero.io -n open-cluster-management-backup --context <primary|secondary> \
  -o custom-columns=NAME:.metadata.name,PHASE:.status.phase

# DataProtectionApplication ready (run on both primary and secondary)
oc get dataprotectionapplication.oadp.openshift.io -n open-cluster-management-backup --context <primary|secondary> \
  -o custom-columns=NAME:.metadata.name,CONDITION:.status.conditions[-1:].type,STATUS:.status.conditions[-1:].status
```

**Decision Tree:**
- ✅ Both show `Available`/`True` → Proceed
- ❌ Either shows error → **STOP** — Fix OADP configuration

---

### Method 1 Only: Passive Sync Status

```bash
oc get restore.cluster.open-cluster-management.io restore-acm-passive-sync \
  -n open-cluster-management-backup --context <secondary>
```

**Expected:** `Phase="Enabled"`, Message shows recent sync

**Decision Tree:**
- ✅ Phase=Enabled, recent sync → Proceed with Method 1
- ⚠️ Phase=Enabled but stale → Wait for sync or check storage connectivity
- ❌ No passive sync restore exists → Switch to Method 2

---

### ACM 2.14+: Auto-Import Strategy Check

```bash
oc get configmap import-controller-config -n multicluster-engine -o yaml 2>/dev/null \
  || echo "ConfigMap not found - using default ImportOnly"
```

**Note:** Default `ImportOnly` is correct for most cases. Document if `ImportAndSync` is set.

---

## Go/No-Go Decision

Present summary to operator:

| Check | Status | Notes |
|-------|--------|-------|
| preserveOnDelete | ✅/❌ | |
| Backups complete | ✅/❌ | |
| ACM versions match | ✅/⚠️/❌ | |
| OADP ready (both hubs) | ✅/❌ | |
| Passive sync (Method 1) | ✅/⚠️/❌ | |

**Final Decision:**
- ✅ All green → **GO** — Proceed with switchover
- ⚠️ Warnings present → **CONDITIONAL GO** — Document risks, get approval
- ❌ Any blocker → **NO-GO** — Resolve issues first

---

## Automated Alternative

Suggest using the automated script:

```bash
./scripts/preflight-check.sh \
  --primary-context <primary> \
  --secondary-context <secondary> \
  --method passive  # or "full" for Method 2
```

---

## Next Steps

Based on method chosen:
- **Method 1**: Proceed to [pause-backups.skill.md](pause-backups.skill.md)
- **Method 2**: Proceed to [activate-full-restore.skill.md](activate-full-restore.skill.md)
