# Troubleshoot: Clusters Stuck in Pending Import

Diagnose and resolve managed clusters stuck in "Pending Import" state after switchover.

> **Reference**: [docs/ACM_SWITCHOVER_RUNBOOK.md — Troubleshooting](../../../docs/ACM_SWITCHOVER_RUNBOOK.md#issue-managed-clusters-stuck-in-pending-import)

---

## Symptoms

- ManagedCluster shows `Pending Import` status on new hub
- Cluster not connecting after 15+ minutes post-activation
- Import status shows waiting for agent connection

```bash
oc get managedcluster.cluster.open-cluster-management.io --context <secondary> | grep "Pending Import"
```

---

## Diagnostic Decision Tree

### 1. Check Import Secrets

```bash
CLUSTER_NAME="<cluster-name>"
oc get secrets -n "$CLUSTER_NAME" --context <secondary> | grep import
```

**Decision Tree:**
- ✅ Import secret exists → Check klusterlet on managed cluster
- ❌ No import secret → Regenerate import secret

---

### 2. Is This a Hive-Provisioned or Manually-Imported Cluster?

**Check for ClusterDeployment:**
```bash
oc get clusterdeployment.hive.openshift.io -n "$CLUSTER_NAME" --context <secondary>
```

**Decision Tree:**
- ClusterDeployment exists → **Hive-provisioned** (should auto-connect)
- No ClusterDeployment → **Manually-imported** (may need reimport)

---

### 3. For Hive-Provisioned Clusters

These should auto-connect. If not:

**Check Hive SyncSet status:**
```bash
oc get syncset -n "$CLUSTER_NAME" --context <secondary>
oc describe syncset -n "$CLUSTER_NAME" --context <secondary>
```

**Check Hive logs:**
```bash
oc logs -n hive deployment/hive-controllers --context <secondary> | grep "$CLUSTER_NAME" | tail -20
```

**Common issues:**
- Network connectivity to managed cluster API
- Managed cluster kubeconfig expired
- Hive unable to reach managed cluster

---

### 4. For Manually-Imported Clusters

**Option A: Wait for auto-import (ACM 2.14+ with ImportAndSync)**

If you configured `ImportAndSync` strategy (see [ACM_SWITCHOVER_RUNBOOK.md](../../docs/ACM_SWITCHOVER_RUNBOOK.md) Step 4b: "Configure auto-import strategy"), clusters should auto-import. Wait 10-15 minutes.

**Option B: Trigger reimport**

```bash
# Get import command from ACM console OR:
oc get secret "$CLUSTER_NAME-import" -n "$CLUSTER_NAME" --context <secondary> \
  -o jsonpath='{.data.crds\.yaml}' | base64 -d > /tmp/crds.yaml

oc get secret "$CLUSTER_NAME-import" -n "$CLUSTER_NAME" --context <secondary> \
  -o jsonpath='{.data.import\.yaml}' | base64 -d > /tmp/import.yaml

# Apply on managed cluster:
# oc apply -f /tmp/crds.yaml
# oc apply -f /tmp/import.yaml
```

---

### 5. Check Klusterlet on Managed Cluster

> Requires access to the managed cluster

**Check klusterlet pods:**
```bash
oc get pods -n open-cluster-management-agent --context <managed-cluster>
```

**Expected pods:**
- `klusterlet-*`
- `klusterlet-registration-agent-*`
- `klusterlet-work-agent-*`

**Check klusterlet logs:**
```bash
oc logs -n open-cluster-management-agent deployment/klusterlet --context <managed-cluster>
oc logs -n open-cluster-management-agent deployment/klusterlet-registration-agent --context <managed-cluster>
```

**Common issues found in logs:**
- Certificate errors → Hub URL or CA changed
- Connection refused → Network/firewall issues
- Unauthorized → Import secret invalid

---

### 6. Check Network Connectivity

**From managed cluster, test connectivity to new hub:**
```bash
# Get hub API URL
HUB_API=$(oc whoami --show-server --context <secondary>)

# On managed cluster, test connectivity
curl -k "$HUB_API/healthz"
```

**Decision Tree:**
- ✅ Connection successful → Check klusterlet configuration
- ❌ Connection failed → Network/firewall issue

---

## Resolution Steps

### If Import Secret Missing

Regenerate by deleting and recreating the ManagedCluster:
```bash
# Save current config
oc get managedcluster.cluster.open-cluster-management.io "$CLUSTER_NAME" --context <secondary> -o yaml > /tmp/mc-backup.yaml

# Delete (this won't affect actual cluster if preserveOnDelete=true)
oc delete managedcluster.cluster.open-cluster-management.io "$CLUSTER_NAME" --context <secondary>

# Recreate
oc apply -f /tmp/mc-backup.yaml --context <secondary>
```

---

### If Klusterlet Not Running

**Restart klusterlet pods:**
```bash
oc delete pods -n open-cluster-management-agent -l app=klusterlet --context <managed-cluster>
```

**If klusterlet deployment missing:**
```bash
# Re-apply import manifests (from step 4 above)
oc apply -f /tmp/crds.yaml --context <managed-cluster>
oc apply -f /tmp/import.yaml --context <managed-cluster>
```

---

### If Klusterlet Has Wrong Hub Configuration

**Check klusterlet config:**
```bash
oc get klusterlet klusterlet -o yaml --context <managed-cluster> | grep -A5 externalServerURLs
```

**If pointing to old hub, update:**
```bash
# Get new hub URL
NEW_HUB_URL=$(oc whoami --show-server --context <secondary>)

# Re-apply import manifests which contain new hub info
oc apply -f /tmp/import.yaml --context <managed-cluster>
```

---

### If Certificate Issues

**Regenerate klusterlet bootstrap:**
```bash
# Delete klusterlet on managed cluster
oc delete klusterlet klusterlet --context <managed-cluster>

# Re-apply import manifests
oc apply -f /tmp/crds.yaml --context <managed-cluster>
oc apply -f /tmp/import.yaml --context <managed-cluster>
```

---

## Verification

After applying fixes:

```bash
# Watch cluster status (should transition from Pending Import to Available)
oc get managedcluster.cluster.open-cluster-management.io "$CLUSTER_NAME" --context <secondary> -w
```

**Expected timeline:** 2-5 minutes for klusterlet to reconnect

---

## Escalation

If cluster still won't import after all steps:

1. **Collect diagnostics:**
   ```bash
   # From managed cluster
   oc adm must-gather --image=quay.io/stolostron/must-gather:latest --context <managed-cluster>
   ```

2. **Check ACM support documentation:**
   - [Official troubleshooting guide](https://docs.redhat.com/en/documentation/red_hat_advanced_cluster_management_for_kubernetes/2.14/html/troubleshooting/index)

3. **Consider manual cluster reimport:**
   - Delete ManagedCluster from ACM
   - Delete klusterlet from managed cluster
   - Perform fresh import through ACM console
