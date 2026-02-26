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

### Argo CD / GitOps (optional)

If Argo CD or OpenShift GitOps manages ACM resources (BackupSchedule, Restore, ManagedCluster, MCO, etc.), auto-sync can revert switchover steps. Detect and pause before Step 1; resume only after Git/desired state has been updated for the new hub.

**Detection (preflight):**
```bash
# Bash preflight with Argo CD report
./scripts/preflight-check.sh --primary-context <primary> --secondary-context <secondary> --method passive --argocd-check

# Python validation-only with Argo CD report
python acm_switchover.py --validate-only --primary-context <primary> --secondary-context <secondary> --argocd-check
```

> **Note:** GitOps marker detection is heuristic. The label `app.kubernetes.io/instance` is flagged as `UNRELIABLE` and must not be treated as a definitive GitOps signal.

**Pause (before starting switchover steps):**
- **Automated (Python):** Run switchover with `--argocd-manage`; pauses ACM-touching Applications during primary prep. (Note: cannot be used with `--validate-only`)
- **Manual (Bash):**
  ```bash
  ./scripts/argocd-manage.sh --context <primary> --mode pause --state-file .state/argocd-pause.json
  # Optionally on secondary (to prevent Restore/BackupSchedule mutation before activation):
  ./scripts/argocd-manage.sh --context <secondary> --mode pause --state-file .state/argocd-pause.json
  ```

**Resume (only after Git/desired state reflects the new hub):**
- **During finalization (Python):** Add `--argocd-resume-after-switchover` to the switchover run (requires `--argocd-manage`; cannot be used with `--validate-only`).
- **Standalone (Python):** `python acm_switchover.py --argocd-resume-only --primary-context <p> --secondary-context <s>` (cannot be used with `--validate-only`, `--argocd-manage`, or `--argocd-resume-after-switchover`)
- **Bash:** `./scripts/argocd-manage.sh --context <new-hub> --mode resume --state-file .state/argocd-pause.json`

**Decision:** If the report shows ACM-touching Applications → advise pausing before Step 1. If pausing is skipped, warn that GitOps may re-apply Git state and undo pause-backup, disable-auto-import, or activation changes. Do not resume until Git reflects the new primary.

---

### ACM 2.14+: Auto-Import Strategy Check

```bash
oc get configmap import-controller-config -n multicluster-engine -o yaml 2>/dev/null \
  || echo "ConfigMap not found - using default ImportOnly"
```

**Note:** Default `ImportOnly` is correct for most cases. Document if `ImportAndSync` is set.

**If ImportOnly is in effect and you expect clusters to re-import on the destination hub:**
- Prefer **immediate-import** annotations for non-local clusters (per runbook)
- Use `ImportAndSync` only when you plan a future switchback, and remove it after activation

```bash
# Apply immediate-import to all non-local clusters (destination hub)
oc get managedcluster.cluster.open-cluster-management.io -o name --context <secondary> | \
  grep -v '/local-cluster$' | \
  xargs -I{} oc annotate {} import.open-cluster-management.io/immediate-import='' --overwrite --context <secondary>
```

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
# If GitOps (Argo CD) manages ACM resources, add: --argocd-check
```

---

## Next Steps

Based on method chosen:
- **Method 1**: Proceed to [pause-backups.skill.md](pause-backups.skill.md)
- **Method 2**: Proceed to [activate-full-restore.skill.md](activate-full-restore.skill.md)
