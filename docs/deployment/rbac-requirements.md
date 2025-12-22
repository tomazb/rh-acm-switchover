# RBAC Requirements for ACM Switchover

**Version**: 1.0.0  
**Last Updated**: December 7, 2024

## Overview

This document details the Role-Based Access Control (RBAC) requirements for the ACM Switchover automation tool. The tool requires specific Kubernetes API permissions to perform hub switchover operations safely and reliably.

## Principle of Least Privilege

The RBAC model is designed following the principle of least privilege:
- **Minimal permissions**: Only permissions required for tool functionality are granted
- **Namespace-scoped when possible**: Permissions are scoped to specific namespaces where applicable
- **Read-only by default**: Write operations are explicitly granted only where needed
- **No wildcard permissions**: All resource types and verbs are explicitly enumerated

## User Roles

### 1. ACM Switchover Operator
**Purpose**: Full automation service account for executing switchover operations

**Use Cases**:
- Automated switchover execution via CI/CD pipelines
- Container-based deployments running as pods
- Service accounts in production environments

**Permission Level**: Full operational permissions (read, create, patch, delete)

### 2. ACM Switchover Validator
**Purpose**: Read-only service account for validation and dry-run operations

**Use Cases**:
- Pre-flight validation checks
- Dry-run mode execution
- Monitoring and health checks
- Compliance verification

**Permission Level**: Read-only access to all required resources

### 3. ACM Administrator (Human User)
**Purpose**: Interactive administration with additional privileges

**Use Cases**:
- Manual switchover execution
- Troubleshooting
- Emergency operations

**Permission Level**: Full operational permissions plus cluster-admin for emergency operations

## Required Kubernetes API Permissions

### Core API Group (v1)

#### Namespaces
- **Resources**: `namespaces`
- **Verbs**: `get`
- **Scope**: Cluster-wide
- **Purpose**: Validate existence of ACM, backup, and observability namespaces

#### Secrets
- **Resources**: `secrets`
- **Verbs**: `get`
- **Scope**: Namespace-scoped (`open-cluster-management-backup`, `open-cluster-management-observability`)
- **Purpose**: Verify Thanos object storage configuration

#### ConfigMaps
- **Resources**: `configmaps`
- **Verbs**: `get`, `list`, `create`, `patch`, `delete`
- **Scope**: Namespace-scoped (`multicluster-engine`)
- **Purpose**: Manage auto-import strategy configuration

#### Pods
- **Resources**: `pods`
- **Verbs**: `get`, `list`
- **Scope**: Namespace-scoped (`open-cluster-management-observability`)
- **Purpose**: Monitor observability component health and verify readiness

### Apps API Group (apps/v1)

#### Deployments
- **Resources**: `deployments`
- **Verbs**: `get`, `patch`
- **Scope**: Namespace-scoped (`open-cluster-management-observability`)
- **Purpose**: Restart observatorium-api deployment during post-activation

#### StatefulSets
- **Resources**: `statefulsets`
- **Verbs**: `get`, `patch`
- **Scope**: Namespace-scoped (`open-cluster-management-observability`)
- **Purpose**: Scale Thanos compactor during primary hub preparation

### ACM Cluster API Group (cluster.open-cluster-management.io)

#### ManagedClusters
- **Resources**: `managedclusters`
- **Verbs**: `get`, `list`, `patch`, `delete`
- **Scope**: Cluster-wide
- **Purpose**: 
  - List and monitor managed cluster status
  - Add disable-auto-import annotations during preparation
  - Verify cluster connection post-activation
  - Clean up during decommission

#### BackupSchedules
- **Resources**: `backupschedules`
- **Verbs**: `get`, `list`, `create`, `patch`, `delete`
- **Scope**: Namespace-scoped (`open-cluster-management-backup`)
- **Purpose**: 
  - Pause/unpause backup schedules
  - Verify backup configuration
  - Fix collision issues
  - Clean up during decommission

#### Restores (ACM)
- **Resources**: `restores`
- **Verbs**: `get`, `list`, `create`, `patch`, `delete`
- **Scope**: Namespace-scoped (`open-cluster-management-backup`)
- **Purpose**: 
  - Create and manage restore operations
  - Monitor restore status
  - Activate managed clusters on secondary hub
  - Set up passive sync for reverse switchover

### Velero API Group (velero.io)

#### Backups
- **Resources**: `backups`
- **Verbs**: `get`, `list`
- **Scope**: Namespace-scoped (`open-cluster-management-backup`)
- **Purpose**: Verify backup completion and status during pre-flight validation

#### Restores (Velero)
- **Resources**: `restores`
- **Verbs**: `get`, `list`
- **Scope**: Namespace-scoped (`open-cluster-management-backup`)
- **Purpose**: Monitor Velero restore operations

### OADP API Group (oadp.openshift.io)

#### DataProtectionApplications
- **Resources**: `dataprotectionapplications`
- **Verbs**: `get`, `list`
- **Scope**: Namespace-scoped (`open-cluster-management-backup`)
- **Purpose**: Verify OADP operator installation and configuration

### Hive API Group (hive.openshift.io)

#### ClusterDeployments
- **Resources**: `clusterdeployments`
- **Verbs**: `get`, `list`
- **Scope**: Cluster-wide
- **Purpose**: Validate `preserveOnDelete=true` to prevent accidental cluster destruction

### ACM Operator API Group (operator.open-cluster-management.io)

#### MultiClusterHubs
- **Resources**: `multiclusterhubs`
- **Verbs**: `get`, `list`, `delete`
- **Scope**: Cluster-wide
- **Purpose**: 
  - Detect ACM version
  - Verify ACM operator installation
  - Decommission old hub (delete during cleanup)

### Observability API Group (observability.open-cluster-management.io)

#### MultiClusterObservabilities
- **Resources**: `multiclusterobservabilities`
- **Verbs**: `get`, `list`, `delete`
- **Scope**: Cluster-wide
- **Purpose**: 
  - Auto-detect observability component presence
  - Clean up during decommission

### Route API Group (route.openshift.io/v1) - OpenShift Only

#### Routes
- **Resources**: `routes`
- **Verbs**: `get`, `list`
- **Scope**: Namespace-scoped (various)
- **Purpose**: Retrieve route hostnames for connectivity verification

## Namespace-Scoped vs Cluster-Scoped Permissions

### Cluster-Scoped Resources
These resources require ClusterRole and ClusterRoleBinding:
- `namespaces` (validation only)
- `managedclusters` (ACM-wide operations)
- `multiclusterhubs` (ACM version detection and decommission)
- `multiclusterobservabilities` (auto-detection and decommission)
- `clusterdeployments` (safety validation)

### Namespace-Scoped Resources
These resources use Role and RoleBinding for specific namespaces:

#### open-cluster-management-backup
- `secrets` (get)
- `configmaps` (get, create, patch, delete)
- `backupschedules` (get, list, create, patch, delete)
- `restores` (get, list, create, patch, delete)
- `backups` (get, list - velero.io)
- `restores` (get, list - velero.io)
- `dataprotectionapplications` (get, list)

#### open-cluster-management-observability
- `secrets` (get)
- `pods` (get, list)
- `deployments` (get, patch)
- `statefulsets` (get, patch)

#### multicluster-engine
- `configmaps` (get, create, patch, delete)

#### open-cluster-management (if needed)
- Additional namespace-scoped operations as required

## Security Considerations

### Mitigations for Security Risks

1. **Credential Exposure**
   - **Risk**: Service account tokens could be exposed
   - **Mitigation**: 
     - Use short-lived tokens with token request API
     - Bind RBAC to specific namespaces where possible
     - Audit service account usage regularly
     - Use Pod Security Standards to restrict token mounting

2. **Privilege Escalation**
   - **Risk**: Compromised service account could modify cluster-wide resources
   - **Mitigation**: 
     - No wildcard permissions granted
     - No access to RoleBindings or ClusterRoleBindings
     - Read-only validator role for non-destructive operations
     - ManagedCluster patch permissions limited to specific annotations

3. **Data Deletion**
   - **Risk**: Accidental or malicious deletion of critical resources
   - **Mitigation**: 
     - Delete permissions only granted where operationally required
     - Pre-flight checks verify `preserveOnDelete=true` on ClusterDeployments
     - State tracking prevents repeat destructive operations
     - Dry-run mode available for validation

4. **Secret Access**
   - **Risk**: Unauthorized access to sensitive credentials
   - **Mitigation**: 
     - Secret access limited to specific namespaces
     - Only `get` verb granted (no list, create, patch, delete)
     - Secrets not logged or exposed in output
     - Read-only validator role doesn't need secret access

5. **Cluster-Wide Impact**
   - **Risk**: Operations affecting entire cluster
   - **Mitigation**: 
     - Cluster-scoped permissions limited to specific resource types
     - No access to critical cluster resources (nodes, CSRs, etc.)
     - Validation-only mode for risk assessment
     - Comprehensive logging and audit trails

### Edge Cases

1. **Multi-Hub Environments**
   - Service account needs permissions on both primary and secondary hubs
   - Separate ServiceAccount per cluster recommended
   - Use different contexts/kubeconfig for each hub

2. **Namespace Customization**
   - Default namespaces may differ in some environments
   - RoleBindings must be adjusted if custom namespaces are used
   - Validate namespace existence before operations

3. **ACM Version Differences**
   - Different ACM versions may have different API versions
   - RBAC must grant permissions for all supported API versions
   - Version detection logic requires MultiClusterHub read access

4. **Observability Optional**
   - Observability components may not be installed
   - RBAC includes observability permissions for when it's present
   - Tool gracefully handles missing observability resources

5. **OADP Installation Variations**
   - OADP namespace might be customized
   - DataProtectionApplication permissions must match installation

## Testing RBAC Permissions

### Validation Commands

```bash
# Test as service account
kubectl auth can-i get managedclusters --as=system:serviceaccount:acm-switchover:acm-switchover-operator

# Test all required permissions
kubectl auth can-i --list --as=system:serviceaccount:acm-switchover:acm-switchover-operator

# Verify ClusterRole exists
kubectl get clusterrole acm-switchover-operator -o yaml

# Verify RoleBindings
kubectl get rolebinding -n open-cluster-management-backup acm-switchover-operator
```

### Integration Testing

1. **Dry-Run Validation**: Execute tool with `--dry-run` using the service account
2. **Read-Only Test**: Use validator service account to ensure no write operations succeed
3. **Full Execution Test**: Complete switchover in test environment
4. **Negative Testing**: Verify operations fail gracefully when permissions are missing

## Compliance and Auditing

### Audit Logging
- All RBAC permission usage is logged via Kubernetes audit logs
- Tool maintains JSON state file with operation history
- Failed permission checks logged with context

### Compliance Standards
- **PCI-DSS**: Least privilege principle satisfied
- **SOC 2**: Access control and segregation of duties
- **NIST 800-53**: AC-6 (Least Privilege), AC-2 (Account Management)

### Regular Review
- Review RBAC permissions quarterly
- Audit service account usage monthly
- Update permissions when tool functionality changes
- Remove unused permissions proactively

## References

- [Kubernetes RBAC Documentation](https://kubernetes.io/docs/reference/access-authn-authz/rbac/)
- [ACM Security Documentation](https://access.redhat.com/documentation/en-us/red_hat_advanced_cluster_management_for_kubernetes/)
- [OpenShift RBAC Best Practices](https://docs.openshift.com/container-platform/latest/authentication/using-rbac.html)
- [NIST 800-53 Access Control](https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-53r5.pdf)
