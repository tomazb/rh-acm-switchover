# Troubleshoot: Grafana Shows No Data

Diagnose and resolve missing metrics in Grafana dashboards after switchover.

> **Reference**: [docs/ACM_SWITCHOVER_RUNBOOK.md — Troubleshooting](../../../docs/ACM_SWITCHOVER_RUNBOOK.md#issue-grafana-shows-no-data-after-15-minutes)

---

## Symptoms

- Grafana dashboards show "No data" after switchover
- ACM Clusters Overview dashboard is empty
- Metrics not appearing even after 15+ minutes

---

## Diagnostic Decision Tree

### 1. Was Observatorium API Restarted?

> This is the most common cause — required after every switchover

**Check if pods were restarted recently:**
```bash
oc get pods -n open-cluster-management-observability --context <secondary> \
  -l app.kubernetes.io/name=observatorium-api \
  -o custom-columns=NAME:.metadata.name,AGE:.metadata.creationTimestamp
```

**Decision Tree:**
- Pod age > 30 minutes since switchover → **Restart needed**
- Pod age < 15 minutes → Wait for metrics to flow (5-10 minutes)

### Fix: Restart Observatorium API

```bash
oc rollout restart deployment observability-observatorium-api \
  -n open-cluster-management-observability --context <secondary>

# Wait for ready
oc wait --for=condition=Ready pod \
  -l app.kubernetes.io/name=observatorium-api \
  -n open-cluster-management-observability --context <secondary> --timeout=5m
```

**Wait 5-10 minutes after restart for data to appear.**

---

### 2. Check Observability Pod Health

```bash
oc get pods -n open-cluster-management-observability --context <secondary>
```

**Look for:**
- All pods in `Running` state
- All pods showing ready (e.g., `1/1`, `2/2`)
- No `CrashLoopBackOff` or `Error` states

**If pods are unhealthy:**
```bash
# Check pod events
oc describe pod <pod-name> -n open-cluster-management-observability --context <secondary>

# Check pod logs
oc logs <pod-name> -n open-cluster-management-observability --context <secondary>
```

---

### 3. Check Observatorium API Logs

```bash
oc logs -n open-cluster-management-observability \
  deployment/observability-observatorium-api --context <secondary> | tail -50
```

**Look for:**
- `tenant not found` errors → ConfigMap needs refresh (restart fixed this)
- Connection errors → Storage backend issues
- Authentication errors → Token/secret issues

---

### 4. Check Thanos Components

```bash
# Thanos Receive (accepts metrics from managed clusters)
oc get pods -n open-cluster-management-observability --context <secondary> \
  -l app.kubernetes.io/name=thanos-receive

# Thanos Store (reads historical data)
oc get pods -n open-cluster-management-observability --context <secondary> \
  -l app.kubernetes.io/name=thanos-store-shard

# Thanos Query (queries data for Grafana)
oc get pods -n open-cluster-management-observability --context <secondary> \
  -l app.kubernetes.io/name=thanos-query
```

**If any are unhealthy, check logs:**
```bash
oc logs -n open-cluster-management-observability \
  deployment/observability-thanos-query --context <secondary> | tail -50
```

---

### 5. Check Metrics Collector on Managed Clusters

> Requires access to managed clusters

**Check metrics-collector pods:**
```bash
oc get pods -n open-cluster-management-addon-observability --context <managed-cluster>
```

**Check metrics-collector logs:**
```bash
oc logs -n open-cluster-management-addon-observability \
  deployment/metrics-collector-deployment --context <managed-cluster> | tail -30
```

**Look for:**
- Connection errors to hub → Network/certificate issues
- Authentication failures → Token expired/invalid
- Push failures → Hub not accepting metrics

---

### 6. Verify Metrics Endpoint Connectivity

**From managed cluster, test connectivity to observatorium:**
```bash
# Get observatorium route
oc get route observatorium-api -n open-cluster-management-observability --context <secondary> \
  -o jsonpath='{.spec.host}'

# Test from managed cluster (if network accessible)
curl -k https://<observatorium-route>/api/metrics/v1/default/api/v1/query?query=up
```

---

## Resolution Steps

### Quick Fix: Restart All Observability Components

If specific diagnosis unclear, restart key components:

```bash
# Restart observatorium-api (most common fix)
oc rollout restart deployment observability-observatorium-api \
  -n open-cluster-management-observability --context <secondary>

# Restart Thanos query
oc rollout restart deployment observability-thanos-query \
  -n open-cluster-management-observability --context <secondary>

# Restart Thanos query-frontend
oc rollout restart deployment observability-thanos-query-frontend \
  -n open-cluster-management-observability --context <secondary>
```

---

### If Storage Backend Issues

**Check ObjectBucketClaim status:**
```bash
oc get objectbucketclaim -n open-cluster-management-observability --context <secondary>
```

**Check Thanos storage secret:**
```bash
oc get secret thanos-object-storage -n open-cluster-management-observability --context <secondary>
```

**If secret missing or incorrect:**
- Verify MCO configuration
- Check if storage credentials were restored properly
- May need to recreate MCO with correct storage config

---

### If Managed Cluster Metrics-Collector Not Connecting

**Check addon status on hub:**
```bash
oc get managedclusteraddon observability-controller -n <cluster-name> --context <secondary> -o yaml
```

**Restart metrics-collector on managed cluster:**
```bash
oc rollout restart deployment metrics-collector-deployment \
  -n open-cluster-management-addon-observability --context <managed-cluster>
```

**If addon missing, verify MCO is properly configured:**
```bash
oc get multiclusterobservability.observability.open-cluster-management.io observability \
  -n open-cluster-management-observability --context <secondary> -o yaml
```

---

## Verification

### Check data in Grafana

1. Access Grafana:
   ```bash
   oc get route grafana -n open-cluster-management-observability --context <secondary> \
     -o jsonpath='{.spec.host}'
   ```

2. Navigate to **ACM - Clusters Overview** dashboard
3. Verify data is visible for managed clusters
4. Check that metrics are recent (within last 5 minutes)

### Check data via query

```bash
# From OpenShift console: Observe > Metrics
# Query: acm_managed_cluster_info
# Should return entries for all managed clusters
```

---

## Timeline Expectations

| Action | Expected Time |
|--------|---------------|
| Observatorium restart | 2-3 minutes |
| First metrics appear | 5-10 minutes after restart |
| Full dashboard population | 10-15 minutes |

---

## Escalation

If metrics still not appearing after 30 minutes:

1. **Collect diagnostics:**
   ```bash
   oc adm must-gather --image=quay.io/stolostron/must-gather:latest --context <secondary>
   ```

2. **Check known issues:**
   - [ACM 2.12 Release Notes](https://docs.redhat.com/en/documentation/red_hat_advanced_cluster_management_for_kubernetes/2.12/html/release_notes/acm-release-notes) (stale tenant data issue)

3. **Consider recreating MCO:**
   - Last resort if observability completely broken
   - Will lose historical metrics
