# ACM Policies for RBAC Enforcement

This directory contains Red Hat Advanced Cluster Management (ACM) Policy manifests to enforce and validate RBAC resources for the ACM Switchover tool across managed clusters.

## Overview

ACM Policies provide automated governance and compliance for Kubernetes resources. These policies ensure that the required RBAC resources for ACM Switchover are properly configured and maintained across your fleet of managed clusters.

## Policy Files

### policy-rbac.yaml

Main policy file that includes:
- **Policy**: Defines RBAC validation and enforcement rules
- **PlacementRule**: Determines which clusters the policy applies to
- **PlacementBinding**: Binds the policy to the placement rule

## What the Policy Validates

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

## Deployment

### Prerequisites

- ACM 2.11+ installed on hub cluster
- Access to ACM hub cluster with policy creation permissions

### Deploy the Policy

```bash
# Apply the policy to ACM hub
kubectl apply -f deploy/acm-policies/policy-rbac.yaml

# Verify policy created
kubectl get policy -n open-cluster-management-policies
```

### Check Policy Status

```bash
# View policy status
kubectl get policy policy-acm-switchover-rbac \
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
