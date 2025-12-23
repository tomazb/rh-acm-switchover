# ACM Policies for RBAC Enforcement

This directory contains Red Hat Advanced Cluster Management (ACM) Policy manifests to enforce and validate RBAC resources for the ACM Switchover tool across managed clusters.

## Overview

ACM Policies provide automated governance and compliance for Kubernetes resources. These policies ensure that the required RBAC resources for ACM Switchover are properly configured and maintained across your fleet of managed clusters.

## Policy Files

### policy-rbac.yaml

Main hub cluster RBAC policy that includes:
- **Policy**: Defines RBAC validation and enforcement rules for hub clusters
- **PlacementRule**: Targets OpenShift clusters (can be customized)
- **PlacementBinding**: Binds the policy to the placement rule

This policy deploys RBAC resources needed for switchover operations on hub clusters.

### policy-managed-cluster-rbac.yaml

Managed cluster RBAC policy for klusterlet reconnection operations:
- **Policy**: Deploys RBAC for `open-cluster-management-agent` namespace
- **PlacementRule**: Targets managed clusters only (excludes local-cluster/hub)
- **PlacementBinding**: Binds the policy to managed clusters

This policy is required for the klusterlet reconnection feature during hub switchover.
The `open-cluster-management-agent` namespace exists only on managed clusters and contains
the klusterlet agent. During switchover, the tool may need to:
- Delete and recreate the `bootstrap-hub-kubeconfig` secret
- Restart the klusterlet deployment to connect to the new hub

## What the Policies Validate

### Hub Cluster Policy (policy-rbac.yaml)

The policy checks for the presence and correct configuration of:

1. **Namespace**: `acm-switchover`
2. **Service Accounts**:
   - `acm-switchover-operator` (full operational permissions)
   - `acm-switchover-validator` (read-only permissions)
3. **ClusterRoles**:
   - `acm-switchover-operator` (cluster-wide operations)
   - `acm-switchover-validator` (cluster-wide read-only)
4. **ClusterRoleBindings**: Binds ClusterRoles to ServiceAccounts
5. **Roles**: Namespace-scoped roles in ACM namespaces
6. **RoleBindings**: Binds namespace-scoped roles to ServiceAccounts

### Managed Cluster Policy (policy-managed-cluster-rbac.yaml)

The policy deploys RBAC for klusterlet management:

1. **Namespace**: `acm-switchover`
2. **Service Accounts**: `acm-switchover-operator`, `acm-switchover-validator`
3. **Roles** in `open-cluster-management-agent` namespace:
   - `acm-switchover-operator`: secrets (get, create, delete), deployments (get, patch)
   - `acm-switchover-validator`: secrets (get), deployments (get)
4. **RoleBindings**: Binds Roles to ServiceAccounts

## Deployment

### Prerequisites

- ACM 2.11+ installed on hub cluster
- Access to ACM hub cluster with policy creation permissions

### Deploy Hub Cluster Policy

```bash
# Apply the hub cluster RBAC policy
kubectl apply -f deploy/acm-policies/policy-rbac.yaml

# Verify policy created
kubectl get policy -n open-cluster-management-policies
```

### Deploy Managed Cluster Policy

```bash
# Apply the managed cluster RBAC policy (for klusterlet operations)
kubectl apply -f deploy/acm-policies/policy-managed-cluster-rbac.yaml

# Verify both policies created
kubectl get policy -n open-cluster-management-policies

# Check managed cluster policy targets (should exclude local-cluster)
kubectl get placementrule placement-policy-switchover-mc-rbac \
  -n open-cluster-management-policies \
  -o jsonpath='{.status.decisions[*].clusterName}'
```

### Check Policy Status

```bash
# View hub policy status
kubectl get policy policy-acm-switchover-rbac \
  -n open-cluster-management-policies \
  -o yaml

# View managed cluster policy status
kubectl get policy policy-switchover-mc-rbac \
  -n open-cluster-management-policies \
  -o yaml

# Check policy compliance across clusters
kubectl get policy policy-acm-switchover-rbac \
  -n open-cluster-management-policies \
  -o jsonpath='{.status.status[*].clustername}'
```

## Policy Modes

### Inform Mode (Default)

The policy is set to `remediationAction: inform` by default, which means:
- Policy violations are reported but not automatically fixed
- Cluster administrators are notified of non-compliance
- Manual remediation is required

```yaml
spec:
  remediationAction: inform
```

### Enforce Mode

To automatically remediate violations, change to `enforce` mode:

```yaml
spec:
  remediationAction: enforce
```

**Warning**: Enforce mode will automatically create/update RBAC resources on managed clusters. Test thoroughly before enabling.

To update:

```bash
# Edit the policy
kubectl edit policy policy-acm-switchover-rbac \
  -n open-cluster-management-policies

# Change remediationAction to "enforce"
```

## Customizing PlacementRule

### Apply to All OpenShift Clusters

Default configuration (already included):

```yaml
spec:
  clusterSelector:
    matchExpressions:
      - key: vendor
        operator: In
        values:
          - OpenShift
```

### Apply to Specific Clusters by Label

```yaml
spec:
  clusterSelector:
    matchLabels:
      environment: production
      acm-switchover-enabled: "true"
```

### Apply to Clusters by Name

```yaml
spec:
  clusterConditions:
    - status: "True"
      type: ManagedClusterConditionAvailable
  clusterSelector:
    matchExpressions:
      - key: name
        operator: In
        values:
          - cluster1
          - cluster2
          - cluster3
```

## Monitoring Compliance

### View Compliance Dashboard

Navigate to ACM Console:
1. Go to **Governance** → **Policies**
2. Click on `policy-acm-switchover-rbac`
3. View compliance status for each cluster

### CLI Compliance Check

```bash
# Get overall policy status
kubectl get policy policy-acm-switchover-rbac \
  -n open-cluster-management-policies

# View detailed status
kubectl describe policy policy-acm-switchover-rbac \
  -n open-cluster-management-policies

# Check specific cluster compliance
kubectl get policy policy-acm-switchover-rbac \
  -n <managed-cluster-namespace> \
  -o yaml
```

## Troubleshooting

### Governance Addon Not Installed on Managed Clusters

The managed cluster policy requires the ACM governance addon to be installed on managed
clusters for ConfigurationPolicy enforcement to work. If policies propagate but resources
are not created, check if the governance addon is installed:

```bash
# Check for governance addon on managed cluster
kubectl get deploy -n open-cluster-management-agent-addon | grep governance

# If no governance addon, you have two options:
# 1. Enable governance addon via ManagedClusterAddon
kubectl apply -f - <<EOF
apiVersion: addon.open-cluster-management.io/v1alpha1
kind: ManagedClusterAddOn
metadata:
  name: governance-policy-framework
  namespace: <managed-cluster-name>
spec:
  installNamespace: open-cluster-management-agent-addon
EOF

# 2. Deploy RBAC directly via kubectl (if governance addon not available)
kubectl --context <managed-cluster-context> apply -f - <<EOF
apiVersion: v1
kind: Namespace
metadata:
  name: acm-switchover
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: acm-switchover-operator
  namespace: acm-switchover
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: acm-switchover-operator
  namespace: open-cluster-management-agent
rules:
  - apiGroups: [""]
    resources: ["secrets"]
    verbs: ["get", "create", "delete"]
  - apiGroups: ["apps"]
    resources: ["deployments"]
    verbs: ["get", "patch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: acm-switchover-operator
  namespace: open-cluster-management-agent
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: acm-switchover-operator
subjects:
  - kind: ServiceAccount
    name: acm-switchover-operator
    namespace: acm-switchover
EOF
```

### Policy Not Applying to Clusters

```bash
# Check PlacementRule
kubectl get placementrule placement-policy-acm-switchover-rbac \
  -n open-cluster-management-policies \
  -o yaml

# View matched clusters
kubectl get placementrule placement-policy-acm-switchover-rbac \
  -n open-cluster-management-policies \
  -o jsonpath='{.status.decisions}'
```

### Policy Shows as Non-Compliant

```bash
# Get policy violation details
kubectl get policy policy-acm-switchover-rbac \
  -n <cluster-namespace> \
  -o jsonpath='{.status.details}'

# View specific configuration policy status
kubectl get configurationpolicy -n <cluster-namespace>
```

### Remediation Not Working

1. Verify policy is set to `enforce` mode
2. Check ACM policy controller logs:
   ```bash
   kubectl logs -n open-cluster-management \
     deployment/governance-policy-framework \
     -c governance-policy-framework
   ```

## Integration with GitOps

### ArgoCD Application

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: acm-switchover-policies
  namespace: argocd
spec:
  project: default
  source:
    repoURL: https://github.com/tomazb/rh-acm-switchover.git
    targetRevision: main
    path: deploy/acm-policies
  destination:
    server: https://kubernetes.default.svc
    namespace: open-cluster-management-policies
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
```

### Flux Kustomization

```yaml
apiVersion: kustomize.toolkit.fluxcd.io/v1beta2
kind: Kustomization
metadata:
  name: acm-switchover-policies
  namespace: flux-system
spec:
  interval: 10m
  path: ./deploy/acm-policies
  prune: true
  sourceRef:
    kind: GitRepository
    name: acm-switchover
  targetNamespace: open-cluster-management-policies
```

## Security Considerations

### Least Privilege

The policy enforces least privilege principles:
- Operator service account has minimal required permissions
- Validator service account is read-only
- No wildcard permissions granted
- Namespace-scoped permissions where possible

### Audit and Compliance

Policy compliance is tracked and reported:
- NIST CSF category: PR.AC (Identity Management and Access Control)
- NIST control: PR.AC-4 (Access Control)
- Regular compliance reports available in ACM console
- Audit logs maintained for policy changes

### Policy Violations

When violations occur:
1. Notification sent to cluster administrators
2. Violation details logged in ACM hub
3. Compliance status updated in real-time
4. Automatic remediation (if enforce mode enabled)

## Testing

### Dry-Run Policy Validation

```bash
# Test policy without applying
kubectl apply -f deploy/acm-policies/policy-rbac.yaml --dry-run=client

# Validate YAML syntax
kubectl apply -f deploy/acm-policies/policy-rbac.yaml --dry-run=server
```

### Simulate Non-Compliance

```bash
# Delete a required resource to trigger violation
kubectl delete clusterrole acm-switchover-operator

# Check policy status (should show non-compliant)
kubectl get policy policy-acm-switchover-rbac \
  -n open-cluster-management-policies

# In enforce mode, resource will be automatically recreated
```

## Cleanup

```bash
# Remove policy
kubectl delete -f deploy/acm-policies/policy-rbac.yaml

# Verify removal
kubectl get policy -n open-cluster-management-policies
```

## References

- [ACM Policy Documentation](https://access.redhat.com/documentation/en-us/red_hat_advanced_cluster_management_for_kubernetes/2.11/html/governance/governance)
- [Policy Examples](https://github.com/open-cluster-management-io/policy-collection)
- [RBAC Requirements](../../docs/deployment/rbac-requirements.md)
- [Governance Risk and Compliance](https://access.redhat.com/documentation/en-us/red_hat_advanced_cluster_management_for_kubernetes/2.11/html/governance/governance)
