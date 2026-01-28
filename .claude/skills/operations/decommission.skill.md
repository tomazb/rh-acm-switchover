# Decommission Old Hub

Guide the operator through safely decommissioning the old primary hub after successful switchover.

> **Reference**: [docs/ACM_SWITCHOVER_RUNBOOK.md — Step 14](../../../docs/ACM_SWITCHOVER_RUNBOOK.md#step-14-decommission-old-primary-hub-optional-but-recommended)

---

## Prerequisites

> ⚠️ **Complete these checks before decommissioning**

Ask operator to confirm:

1. **"Has the switchover been validated for at least 24 hours?"**
   - Recommended wait period to catch delayed issues
   - Shorter periods acceptable in test environments

2. **"Are all managed clusters showing Available on the new hub?"**
   ```bash
   oc get managedcluster.cluster.open-cluster-management.io --context <secondary> \
     -o custom-columns='NAME:.metadata.name,AVAILABLE:.status.conditions[?(@.type=="ManagedClusterConditionAvailable")].status' \
     | grep -v True
   # Should return empty (all True)
   ```

3. **"Has a successful backup been created on the new hub?"**
   ```bash
   oc get backup.velero.io -n open-cluster-management-backup --context <secondary> \
     --sort-by=.metadata.creationTimestamp -o custom-columns=NAME:.metadata.name,PHASE:.status.phase | tail -3
   # Should show Completed
   ```

4. **"Do you have rollback capability if needed?"**
   - After decommission, rollback is NOT possible
   - Ensure you're committed to the new hub

---

## Critical Safety Check: preserveOnDelete

> ⚠️ **MANDATORY** — Verify before ANY ManagedCluster deletion

```bash
oc get clusterdeployment.hive.openshift.io --all-namespaces --context <primary> \
  -o custom-columns=NAME:.metadata.name,NAMESPACE:.metadata.namespace,PRESERVE:.spec.preserveOnDelete
```

**Decision Tree:**
- ✅ ALL show `true` → Safe to proceed
- ❌ ANY show `false` or `<none>` → **STOP AND FIX FIRST**

```bash
# Fix any missing preserveOnDelete
oc patch clusterdeployment.hive.openshift.io <name> -n <namespace> --context <primary> \
  --type='merge' -p '{"spec":{"preserveOnDelete":true}}'
```

> **WITHOUT THIS:** Deleting ManagedClusters will DESTROY your production cluster infrastructure!

---

## Step 14.1: Delete MultiClusterObservability

### Find MCO resource

```bash
oc get multiclusterobservability.observability.open-cluster-management.io -A --context <primary>
```

### Delete MCO

```bash
oc delete multiclusterobservability.observability.open-cluster-management.io observability \
  -n open-cluster-management-observability --context <primary>
```

### Wait for observability pods to terminate

```bash
# Watch pods terminate (2-5 minutes)
oc get pods -n open-cluster-management-observability --context <primary> -w
```

---

## Step 14.2: Verify Observability Removed

```bash
oc get pods -n open-cluster-management-observability --context <primary>
# Should return: No resources found (or only terminating pods)
```

---

## Step 14.3: Delete ManagedClusters

> ⚠️ **CRITICAL** — Only proceed after preserveOnDelete verification

### Final safety check: Verify clusters are on new hub

```bash
# On NEW HUB - all should be Available
oc get managedcluster.cluster.open-cluster-management.io --context <secondary> \
  -o custom-columns='NAME:.metadata.name,AVAILABLE:.status.conditions[?(@.type=="ManagedClusterConditionAvailable")].status'
```

### Check cluster status on old hub

```bash
# On OLD HUB - should show Unknown (disconnected)
oc get managedcluster.cluster.open-cluster-management.io --context <primary>
```

**Decision Tree:**
- Clusters show Unknown on old hub → Safe to delete
- Clusters show Available on old hub → **STOP** — They haven't moved yet!

### Delete ManagedClusters (keep local-cluster)

```bash
for cluster in $(oc get managedcluster.cluster.open-cluster-management.io -o name --context <primary> | grep -v local-cluster); do
  echo "Deleting $cluster"
  oc delete $cluster --context <primary>
done
```

### Verify deletion

```bash
oc get managedcluster.cluster.open-cluster-management.io --context <primary>
# Should only show local-cluster
```

---

## Step 14.4: Delete MultiClusterHub

### Find MCH resource

```bash
oc get multiclusterhub.operator.open-cluster-management.io -A --context <primary>
```

### Delete MCH

```bash
oc delete multiclusterhub.operator.open-cluster-management.io multiclusterhub \
  -n open-cluster-management --context <primary>
```

### Monitor deletion (can take up to 20 minutes)

```bash
oc get multiclusterhub.operator.open-cluster-management.io -A --context <primary> -w
```

---

## Step 14.5: Verify ACM Removed

```bash
# Check ACM namespace
oc get pods -n open-cluster-management --context <primary>
# Operator pods may remain until operator is uninstalled
# Application/controller pods should be gone
```

---

## What If...

### "ManagedClusters show Available on old hub during decommission"

**STOP** — Clusters haven't fully moved:
1. Verify [verify-switchover.skill.md](verify-switchover.skill.md) was completed
2. Check clusters are Available on new hub first
3. Wait for clusters to disconnect from old hub (5-10 minutes)

### "MCH deletion is stuck"

Check for finalizers:
```bash
oc get multiclusterhub.operator.open-cluster-management.io multiclusterhub \
  -n open-cluster-management --context <primary> -o yaml | grep -A5 finalizers
```

If stuck on finalizers:
```bash
# CAUTION: Only use if resources are verified cleaned up
oc patch multiclusterhub.operator.open-cluster-management.io multiclusterhub \
  -n open-cluster-management --context <primary> \
  --type='merge' -p '{"metadata":{"finalizers":null}}'
```

### "Some pods won't terminate"

Force delete stuck pods:
```bash
oc delete pod <pod-name> -n <namespace> --context <primary> --force --grace-period=0
```

### "Can I undo decommission?"

**No.** Once MCH is deleted:
- You would need to reinstall ACM from scratch
- Clusters would need to be reimported
- Previous configuration is lost

This is why 24-hour validation period is recommended before decommission.

---

## Post-Decommission

### What's preserved:
- Backup data in object storage (shared with new hub)
- Underlying managed cluster infrastructure
- Applications on managed clusters

### What's removed:
- ACM control plane on old hub
- ManagedCluster registrations
- Observability components
- Policies and configurations (except in backups)

### Next steps:
1. Document decommission completion
2. Update runbooks/documentation with new hub info
3. Consider repurposing the old hub cluster
4. Monitor new hub for any issues

---

## Decommission Checklist

| Step | Status | Notes |
|------|--------|-------|
| 24h validation period | ✅/⚠️ | |
| All clusters on new hub | ✅/❌ | |
| Backup verified on new hub | ✅/❌ | |
| preserveOnDelete verified | ✅/❌ | |
| MCO deleted | ✅/❌ | |
| Observability pods gone | ✅/❌ | |
| Clusters show Unknown on old hub | ✅/❌ | |
| ManagedClusters deleted | ✅/❌ | |
| MCH deleted | ✅/❌ | |
| ACM pods terminated | ✅/❌ | |
