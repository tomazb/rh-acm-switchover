# Verify Switchover Success

Guide the operator through post-activation verification: clusters connected, observability working, metrics flowing.

> **Reference**: [docs/ACM_SWITCHOVER_RUNBOOK.md — Steps 6-10](../../../docs/ACM_SWITCHOVER_RUNBOOK.md#post-activation-common-steps-applies-to-both-methods)

---

## Step 6: Verify ManagedClusters Are Connected

### Check cluster availability

```bash
oc get managedcluster.cluster.open-cluster-management.io --context <secondary> \
  -o custom-columns='NAME:.metadata.name,AVAILABLE:.status.conditions[?(@.type=="ManagedClusterConditionAvailable")].status,JOINED:.status.conditions[?(@.type=="ManagedClusterJoined")].status'
```

**Expected:** All clusters show `AVAILABLE=True` and `JOINED=True`

**Decision Tree:**
- ✅ All True → Proceed to Step 7
- ⚠️ Some Unknown → Wait 5-10 minutes, recheck (clusters are reconnecting)
- ❌ Pending Import → See "What If" section below

### Check for stuck clusters

```bash
oc get managedcluster.cluster.open-cluster-management.io --context <secondary> | grep -E "Pending|Unknown"
```

**Timeline:** Clusters should connect within 5-10 minutes. If still not connected after 15 minutes, investigate.

---

## Step 7: Reset Auto-Import Strategy (ACM 2.14+)

> Only if you set ImportAndSync in Step 4b or F4

### Check if ConfigMap exists

```bash
oc get configmap import-controller-config -n multicluster-engine --context <secondary> 2>/dev/null
```

### If exists, remove it

```bash
oc delete configmap import-controller-config -n multicluster-engine --context <secondary> --ignore-not-found
```

---

## Step 8: Restart Observatorium API

> Required to refresh stale tenant data after restore

```bash
oc rollout restart deployment observability-observatorium-api \
  -n open-cluster-management-observability --context <secondary>
```

### Wait for pods to be ready

```bash
oc wait --for=condition=Ready pod \
  -l app.kubernetes.io/name=observatorium-api \
  -n open-cluster-management-observability --context <secondary> --timeout=5m
```

---

## Step 9: Verify Observability Pods

### Check all pods running

```bash
oc get pods -n open-cluster-management-observability --context <secondary>
```

**Expected pods (all Running/Ready):**
- `observability-alertmanager-*`
- `observability-grafana-*`
- `observability-observatorium-api-*`
- `observability-thanos-compact-*`
- `observability-thanos-query-*`
- `observability-thanos-receive-*`
- `observability-thanos-store-*`

### Check for unhealthy pods

```bash
oc get pods -n open-cluster-management-observability --context <secondary> | grep -vE "Running|Completed"
```

**Decision Tree:**
- ✅ All Running/Completed → Proceed
- ❌ CrashLoopBackOff or Error → Check pod logs, see [troubleshooting/grafana-no-data.skill.md](../troubleshooting/grafana-no-data.skill.md)

---

## Step 10: Verify Metrics Collection

### Get Grafana URL

```bash
oc get route grafana -n open-cluster-management-observability --context <secondary> \
  -o jsonpath='{.spec.host}'
```

### In Grafana, verify:

1. Navigate to **"ACM - Clusters Overview"** dashboard
2. Check that data is visible for all clusters
3. Verify metrics are recent (within last 5-10 minutes)

### Alternative: Query metrics via CLI

```bash
# From OpenShift console: Observe > Metrics
# Query: acm_managed_cluster_info
# Should show entries for all managed clusters
```

**Decision Tree:**
- ✅ Recent data visible for all clusters → Success!
- ⚠️ Data visible but stale → Wait 5-10 minutes for collection to resume
- ❌ No data → See [troubleshooting/grafana-no-data.skill.md](../troubleshooting/grafana-no-data.skill.md)

---

## Optional: Disable Observability on Old Hub (between Steps 10-11)

> Use this when keeping the old hub as a secondary (not decommissioning).
> If GitOps manages the MCO, coordinate deletion to avoid drift.

```bash
oc delete multiclusterobservability.observability.open-cluster-management.io observability \
  -n open-cluster-management-observability --context <primary>
```

**Verify pods terminate:**
```bash
oc get pods -n open-cluster-management-observability --context <primary>
```

> **Note:** If Observability pods remain after MCO deletion (and GitOps is not recreating it), capture logs and open a support case (product bug).

---

## What If...

### "Clusters stuck in Pending Import"

See [troubleshooting/pending-import.skill.md](../troubleshooting/pending-import.skill.md)

Quick check:
```bash
oc get secrets -n <cluster-namespace> --context <secondary> | grep import
```

### "Clusters showing Unknown on BOTH hubs"

This indicates clusters haven't connected to either hub:
1. Check network connectivity from managed clusters to new hub
2. Verify klusterlet pods running on managed clusters
3. Check klusterlet logs for certificate issues

### "Grafana shows no data after 15 minutes"

See [troubleshooting/grafana-no-data.skill.md](../troubleshooting/grafana-no-data.skill.md)

Quick fix:
```bash
# Restart observatorium-api (if not done already)
oc rollout restart deployment observability-observatorium-api \
  -n open-cluster-management-observability --context <secondary>
```

### "Some observability pods in CrashLoopBackOff"

Check pod logs:
```bash
oc logs -n open-cluster-management-observability <pod-name> --context <secondary>
```

Common issues:
- Storage connectivity problems
- Missing secrets after restore
- Resource constraints

---

## Verification Summary

Present checklist to operator:

| Check | Status | Notes |
|-------|--------|-------|
| ManagedClusters Available | ✅/❌ | |
| ManagedClusters Joined | ✅/❌ | |
| ImportAndSync removed (2.14+) | ✅/N/A | |
| Observatorium API restarted | ✅/❌ | |
| Observability pods healthy | ✅/❌ | |
| Grafana showing recent data | ✅/❌ | |

---

## Automated Alternative

Use the post-flight validation script:

```bash
./scripts/postflight-check.sh \
  --new-hub-context <secondary> \
  --old-hub-context <primary>
```

---

## Next Steps

- ✅ All verified → Proceed to [enable-backups.skill.md](enable-backups.skill.md)
- ❌ Issues found → Address using troubleshooting guides, then re-verify
