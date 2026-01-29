# ACM Switchover TLDR

**Quick reference for experienced operators. See [full runbook](ACM_SWITCHOVER_RUNBOOK.md) for details.**

---

## ⚠️ Critical Pre-Checks

Quick prerequisites (stop if any are false; use full runbook or preflight script below):
- ACM versions match; OADP + DPA installed on both hubs; both hubs access same backup storage
- Secondary already running passive sync if using Method 1
- Required operators/add-ons and secrets mirrored on secondary (GitOps/Observability)
- Managed clusters can reach secondary hub (network/DNS/firewall)

```bash
# MANDATORY: Verify preserveOnDelete=true on ALL ClusterDeployments
oc get clusterdeployment.hive.openshift.io --all-namespaces \
  -o custom-columns=NAME:.metadata.name,PRESERVE:.spec.preserveOnDelete
# ALL must show "true" - STOP if any show "false" or "<none>"!

# Verify backups are healthy
oc get backup.velero.io -n open-cluster-management-backup --sort-by=.metadata.creationTimestamp | tail -5

# Verify BSL available on both hubs
oc get backupstoragelocation.velero.io -n open-cluster-management-backup
```

---

## Method 1: Passive Restore Activation (Recommended)

### On PRIMARY Hub

```bash
# 1. Pause BackupSchedule (ACM 2.12+)
BACKUP_SCHEDULE_NAME=$(oc get backupschedule.cluster.open-cluster-management.io \
  -n open-cluster-management-backup -o jsonpath='{.items[0].metadata.name}')
oc patch backupschedule.cluster.open-cluster-management.io "$BACKUP_SCHEDULE_NAME" \
  -n open-cluster-management-backup --type='merge' -p '{"spec":{"paused":true}}'

# ACM 2.11 only (use instead of patch above) - re-apply in Step 11
oc get backupschedule.cluster.open-cluster-management.io "$BACKUP_SCHEDULE_NAME" \
  -n open-cluster-management-backup -o yaml > "${BACKUP_SCHEDULE_NAME}.yaml"
oc delete backupschedule.cluster.open-cluster-management.io "$BACKUP_SCHEDULE_NAME" \
  -n open-cluster-management-backup

# 2. Disable auto-import on all ManagedClusters
for cluster in $(oc get managedcluster.cluster.open-cluster-management.io -o name | grep -v local-cluster); do
  oc annotate $cluster import.open-cluster-management.io/disable-auto-import=''
done

# 3. Stop Thanos compactor
oc scale statefulset observability-thanos-compact \
  -n open-cluster-management-observability --replicas=0

# Optional: pause Observatorium API on OLD hub during switchover window
oc scale deployment observability-observatorium-api \
  -n open-cluster-management-observability --replicas=0
```

### On SECONDARY Hub

```bash
# 4. Verify passive sync is current
oc get restore.cluster.open-cluster-management.io restore-acm-passive-sync \
  -n open-cluster-management-backup
# Expect Phase=Enabled and latest restores; do not proceed if stale

# 4a. (ACM 2.14+ with ImportOnly) Add immediate-import annotation
for mc in $(oc get managedcluster.cluster.open-cluster-management.io -o name | grep -v local-cluster); do
  oc annotate "$mc" import.open-cluster-management.io/immediate-import='' --overwrite
done

# 4b. (ACM 2.14+, optional) If you need continuous sync for switchback, set ImportAndSync
cat <<EOF | oc apply -f -
apiVersion: v1
kind: ConfigMap
metadata:
  name: import-controller-config
  namespace: multicluster-engine
data:
  autoImportStrategy: ImportAndSync
EOF

# 5. ACTIVATE - Patch restore to latest
oc patch restore.cluster.open-cluster-management.io restore-acm-passive-sync \
  -n open-cluster-management-backup --type='merge' \
  -p '{"spec":{"veleroManagedClustersBackupName":"latest"}}'

# Watch for Finished
oc get restore.cluster.open-cluster-management.io restore-acm-passive-sync \
  -n open-cluster-management-backup -w
```

---

## Method 2: Full Restore (No Prior Passive Sync)

### On SECONDARY Hub

```bash
# Create full restore
cat <<EOF | oc apply -f -
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

# Watch for Finished
oc get restore.cluster.open-cluster-management.io restore-acm-full \
  -n open-cluster-management-backup -w
```

---

## Post-Activation (Both Methods)

### On NEW PRIMARY Hub

```bash
# 6. Verify clusters connected (wait 5-10 min)
oc get managedcluster.cluster.open-cluster-management.io \
  -o custom-columns='NAME:.metadata.name,AVAILABLE:.status.conditions[?(@.type=="ManagedClusterConditionAvailable")].status'

# 7. (ACM 2.14+) Remove ImportAndSync ConfigMap if set in Step 4b
oc delete configmap import-controller-config -n multicluster-engine --ignore-not-found

# 8. Restart Observatorium API (fixes stale tenant data)
oc rollout restart deployment observability-observatorium-api \
  -n open-cluster-management-observability
oc wait --for=condition=Ready pod -l app.kubernetes.io/name=observatorium-api \
  -n open-cluster-management-observability --timeout=5m

# 9. Verify Observability pods
oc get pods -n open-cluster-management-observability

# 10. Verify metrics in Grafana (wait 5-10 min after restart)
oc get route grafana -n open-cluster-management-observability -o jsonpath='{.spec.host}'

# 11. Enable BackupSchedule
oc patch backupschedule.cluster.open-cluster-management.io "$BACKUP_SCHEDULE_NAME" \
  -n open-cluster-management-backup --type='merge' -p '{"spec":{"paused":false}}'

# 12. Verify backup integrity
oc get backup.velero.io -n open-cluster-management-backup --sort-by=.metadata.creationTimestamp | tail -3

# Quick integrity check (status + velero logs)
BACKUP_NAME=$(oc get backup.velero.io -n open-cluster-management-backup \
  --sort-by=.metadata.creationTimestamp -o name | tail -n1 | cut -d/ -f2)
oc get backup.velero.io "$BACKUP_NAME" -n open-cluster-management-backup -o yaml | grep -A 5 "status:"
oc logs -n open-cluster-management-backup deployment/velero -c velero | grep "$BACKUP_NAME"
```

---

## Decommission Old Hub (Optional)

WARNING: Do NOT re-enable Thanos compactor or observatorium-api on the OLD hub while the new hub is active.
If keeping the old hub as a long-lived secondary, delete MCO there to avoid dual writers.

```bash
# On OLD HUB - only after verifying all clusters AVAILABLE on new hub

# Safety checks before deleting ManagedClusters
oc get clusterdeployment.hive.openshift.io --all-namespaces \
  -o custom-columns=NAME:.metadata.name,PRESERVE:.spec.preserveOnDelete
# On NEW HUB:
oc get managedcluster.cluster.open-cluster-management.io \
  -o custom-columns='NAME:.metadata.name,AVAILABLE:.status.conditions[?(@.type=="ManagedClusterConditionAvailable")].status'

# 1. Delete MCO
oc delete multiclusterobservability.observability.open-cluster-management.io observability

# 2. Delete ManagedClusters (keep local-cluster)
for cluster in $(oc get managedcluster.cluster.open-cluster-management.io -o name | grep -v local-cluster); do
  oc delete $cluster
done

# 3. Delete MCH (takes ~20 min)
oc delete multiclusterhub.operator.open-cluster-management.io multiclusterhub -n open-cluster-management
```

---

## Rollback

### On SECONDARY Hub (failed activation)

```bash
# Delete activation restore
oc delete restore.cluster.open-cluster-management.io restore-acm-activate \
  -n open-cluster-management-backup --ignore-not-found
oc delete restore.cluster.open-cluster-management.io restore-acm-passive-sync \
  -n open-cluster-management-backup --ignore-not-found

# If you used Method 2 (full restore)
oc delete restore.cluster.open-cluster-management.io restore-acm-full \
  -n open-cluster-management-backup --ignore-not-found
```

### On PRIMARY Hub (restore original state)

```bash
# Remove disable-auto-import annotations
for cluster in $(oc get managedcluster.cluster.open-cluster-management.io -o name | grep -v local-cluster); do
  oc annotate $cluster import.open-cluster-management.io/disable-auto-import-
done

# Restart Thanos compactor
oc scale statefulset observability-thanos-compact \
  -n open-cluster-management-observability --replicas=1

# Re-enable Observatorium API if you paused it in Step 3
oc scale deployment observability-observatorium-api \
  -n open-cluster-management-observability --replicas=1

# Unpause BackupSchedule
oc patch backupschedule.cluster.open-cluster-management.io "$BACKUP_SCHEDULE_NAME" \
  -n open-cluster-management-backup --type='merge' -p '{"spec":{"paused":false}}'

# Optional: re-create passive sync for DR readiness (see full runbook)
```

---

## Quick Troubleshooting

| Issue | Quick Fix |
|-------|-----------|
| Clusters stuck "Pending Import" | Check: `oc get secrets -n <cluster-ns> \| grep import` |
| Grafana no data after 15 min | Restart observatorium-api, check metrics-collector on managed clusters |
| Restore stuck "Running" | Check: `oc describe restore.cluster.open-cluster-management.io <name> -n open-cluster-management-backup` |
| BSL unavailable | Check: `oc get backupstoragelocation.velero.io -n open-cluster-management-backup -o yaml` |

---

## Automation Scripts

```bash
# Pre-flight validation
./scripts/preflight-check.sh --primary-context <primary> --secondary-context <secondary> --method passive

# Post-flight validation
./scripts/postflight-check.sh --new-hub-context <new-hub> --old-hub-context <old-hub>

# Discover hub contexts
./scripts/discover-hub.sh --auto --run
```
