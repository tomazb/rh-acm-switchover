# ACM Switchover RBAC Implementation Summary

## Overview

This document summarizes the comprehensive RBAC (Role-Based Access Control) implementation for the ACM Switchover automation tool. The implementation follows the principle of least privilege and provides multiple deployment options for different environments and use cases.

## What Was Implemented

### 1. RBAC Requirements Documentation
- **File**: `docs/deployment/rbac-requirements.md`
- Comprehensive analysis of all Kubernetes API operations
- Detailed permission requirements by API group
- Security considerations and risk mitigation strategies
- User roles and expected operations
- Compliance and auditing guidelines

### 2. RBAC Manifests (deploy/rbac/)
- **Namespace**: `acm-switchover` - Dedicated namespace for service accounts
- **Service Accounts**:
  - `acm-switchover-operator` - Full operational permissions
  - `acm-switchover-validator` - Read-only permissions for validation
- **ClusterRoles**: Cluster-wide permissions for both operator and validator
- **Roles**: Namespace-scoped permissions in:
  - `open-cluster-management-backup`
  - `open-cluster-management-observability`
  - `multicluster-engine`
- **Bindings**: ClusterRoleBindings and RoleBindings to link roles to service accounts

### 3. Kustomize Integration (deploy/kustomize/)
- **Base configuration**: Common RBAC resources for all environments
- **Overlays**:
  - Production: Production-specific labels and annotations
  - Development: Development-specific customizations
- **Features**:
  - Easy environment-specific customization
  - GitOps-ready structure
  - Comprehensive README with examples

### 4. Helm Chart (packaging/helm/acm-switchover/)
- **Chart structure**: Application + RBAC packaged together (Option A)
- **Templates**: Job/CronJob, PVC, service accounts, ClusterRoles/Bindings, Roles/Bindings, optional import-controller ConfigMap
- **Values**: Configurable parameters for customization:
  - Namespace names and creation
  - Operator/validator service account names/annotations
  - RBAC rule customization and custom namespaces
  - Observability/decommission toggles
- **Documentation**: `packaging/helm/acm-switchover/README.md`

### 5. ACM Policy Governance (deploy/acm-policies/)
- **Policy**: Validates and enforces RBAC resources across managed clusters
- **PlacementRule**: Determines which clusters receive the policy
- **PlacementBinding**: Binds policy to placement rule
- **Features**:
  - Automated compliance checking
  - Multi-cluster governance
  - Inform or enforce modes
  - NIST CSF compliance annotations

### 6. RBAC Validation Module (lib/rbac_validator.py)
- **RBACValidator class**: Programmatic RBAC permission validation
- **Methods**:
  - `check_permission()` - Check individual permissions
  - `validate_cluster_permissions()` - Validate cluster-scoped permissions
  - `validate_namespace_permissions()` - Validate namespace-scoped permissions
  - `validate_all_permissions()` - Complete validation
  - `generate_permission_report()` - Detailed validation report
- **Integration**: Integrated into pre-flight validation workflow

### 7. Standalone RBAC Checker (check_rbac.py)
- **Purpose**: Validate RBAC permissions without running full switchover
- **Features**:
  - Check single or both hubs
  - Include/exclude decommission permissions
  - Skip observability checks
  - Detailed error reporting
  - Exit codes for CI/CD integration

### 8. Script Integration
- **Pre-flight validation**: Automatic RBAC validation before switchover
- **CLI flag**: `--skip-rbac-validation` to bypass checks if needed
- **Error handling**: Clear error messages with remediation guidance
- **Logging**: Comprehensive logging of permission checks

### 9. Unit Tests (tests/test_rbac_validator.py)
- **Coverage**: Tests for all RBACValidator methods
- **Test cases**:
  - Permission checking (allowed/denied)
  - Cluster permission validation
  - Namespace permission validation
  - Combined validation
  - Report generation
  - Edge cases (missing namespaces, etc.)

### 10. Documentation
- **RBAC Requirements**: `docs/deployment/rbac-requirements.md`
- **RBAC Deployment Guide**: `docs/deployment/rbac-deployment.md`
- **Kustomize README**: `deploy/kustomize/README.md`
- **Helm Chart README**: `deploy/helm/acm-switchover-rbac/README.md`
- **ACM Policy README**: `deploy/acm-policies/README.md`
- **Updated main README**: Added RBAC section and prerequisites
- **Updated CHANGELOG**: Documented all RBAC additions

## Deployment Options

Users can choose from multiple deployment methods based on their requirements:

| Method | Use Case | Complexity | Best For |
|--------|----------|------------|----------|
| **kubectl apply** | Quick testing | Low | Development, POC |
| **Kustomize** | GitOps workflows | Medium | Multiple environments |
| **Helm** | Template-based | Medium | Package management |
| **ACM Policy** | Multi-cluster | High | Enterprise governance |

## Security Features

### Least Privilege
- Separate service accounts for operator (read/write) and validator (read-only)
- No wildcard permissions
- Namespace-scoped permissions where possible
- Explicit enumeration of all required verbs

### Risk Mitigation
- Pre-flight RBAC validation prevents runtime failures
- Read-only validator for safe validation operations
- Audit logging of all permission usage
- Compliance annotations for governance

### Edge Case Handling
- Graceful handling of missing namespaces
- Support for custom ACM namespace names
- Optional observability checks
- Separate decommission permissions

## Usage Examples

### Quick Start
```bash
# Deploy with kubectl
kubectl apply -f deploy/rbac/

# Validate permissions
python check_rbac.py --primary-context primary-hub --secondary-context secondary-hub

# Run switchover
python acm_switchover.py --primary-context primary-hub --secondary-context secondary-hub --method passive --old-hub-action secondary
```

### Production Deployment
```bash
# Deploy with Kustomize (production overlay)
kubectl apply -k deploy/kustomize/overlays/production/

# Or deploy with Helm
helm install acm-switchover-rbac deploy/helm/acm-switchover-rbac/ -f custom-values.yaml

# Validate
python check_rbac.py --include-decommission
```

### Multi-Cluster Governance
```bash
# Deploy ACM Policy
kubectl apply -f deploy/acm-policies/policy-rbac.yaml

# Check compliance
kubectl get policy -n open-cluster-management-policies
```

## Integration Points

### Python Scripts
- `lib/rbac_validator.py` - Core validation logic
- `lib/__init__.py` - Exports RBAC validator
- `modules/preflight.py` - Integrates RBAC validation into pre-flight checks
- `acm_switchover.py` - Adds `--skip-rbac-validation` flag

### Shell Scripts
While the primary implementation is in Python, the RBAC model also supports:
- ServiceAccount token generation
- kubeconfig creation for service accounts
- Manual permission validation with `kubectl auth can-i`

## Testing

### Unit Tests
```bash
# Run RBAC validator tests
pytest tests/test_rbac_validator.py -v
```

### Integration Testing
```bash
# Validate RBAC on test cluster
python check_rbac.py --context test-cluster

# Dry-run switchover with RBAC validation
python acm_switchover.py --dry-run --primary-context primary --secondary-context secondary --method passive --old-hub-action secondary
```

### Validation Steps
1. Deploy RBAC resources
2. Run `check_rbac.py` to validate permissions
3. Run switchover with `--validate-only` flag
4. Execute full switchover in test environment
5. Verify audit logs for permission usage

## Compliance

The RBAC implementation addresses:
- **NIST CSF**: PR.AC-4 (Access Control)
- **PCI-DSS**: Least privilege principle
- **SOC 2**: Access control and segregation of duties
- **NIST 800-53**: AC-6 (Least Privilege), AC-2 (Account Management)

## Files Added/Modified

### New Files (32 total)
```
deploy/rbac/namespace.yaml
deploy/rbac/serviceaccount.yaml
deploy/rbac/clusterrole.yaml
deploy/rbac/clusterrolebinding.yaml
deploy/rbac/role.yaml
deploy/rbac/rolebinding.yaml

deploy/kustomize/base/kustomization.yaml
deploy/kustomize/overlays/production/kustomization.yaml
deploy/kustomize/overlays/development/kustomization.yaml
deploy/kustomize/README.md

deploy/helm/acm-switchover-rbac/Chart.yaml
deploy/helm/acm-switchover-rbac/values.yaml
deploy/helm/acm-switchover-rbac/README.md
deploy/helm/acm-switchover-rbac/templates/_helpers.tpl
deploy/helm/acm-switchover-rbac/templates/namespace.yaml
deploy/helm/acm-switchover-rbac/templates/serviceaccount.yaml
deploy/helm/acm-switchover-rbac/templates/clusterrole.yaml
deploy/helm/acm-switchover-rbac/templates/clusterrolebinding.yaml
deploy/helm/acm-switchover-rbac/templates/role.yaml
deploy/helm/acm-switchover-rbac/templates/rolebinding.yaml

deploy/acm-policies/policy-rbac.yaml
deploy/acm-policies/README.md

lib/rbac_validator.py
check_rbac.py
tests/test_rbac_validator.py

docs/deployment/rbac-requirements.md
docs/deployment/rbac-deployment.md
docs/development/rbac-implementation.md (this file)
```

### Modified Files (5 total)
```
lib/__init__.py - Added RBAC validator exports
modules/preflight.py - Integrated RBAC validation
acm_switchover.py - Added --skip-rbac-validation flag
README.md - Added RBAC documentation links and prerequisites
CHANGELOG.md - Documented all RBAC changes
```

## Future Enhancements

Potential improvements for future iterations:
1. **OpenShift RBAC**: Add OpenShift-specific Security Context Constraints (SCCs)
2. **Dynamic Permission Discovery**: Auto-generate RBAC from actual API calls
3. **Permission Minimization**: Tools to identify unused permissions
4. **RBAC Templates**: Pre-built templates for common scenarios
5. **Policy Generator**: Auto-generate ACM policies from RBAC manifests
6. **Audit Dashboard**: UI for visualizing permission usage
7. **Token Management**: Automated token rotation and management
8. **Multi-Tenancy**: Support for multiple teams/service accounts

## Conclusion

This RBAC implementation provides a comprehensive, production-ready solution for securing ACM switchover operations. It follows security best practices, offers multiple deployment options, and includes extensive documentation and validation tools. The implementation is modular, extensible, and designed for enterprise use cases.

## References

- [RBAC Requirements Documentation](../deployment/rbac-requirements.md)
- [RBAC Deployment Guide](../deployment/rbac-deployment.md)
- [Kubernetes RBAC](https://kubernetes.io/docs/reference/access-authn-authz/rbac/)
- [ACM Security](https://access.redhat.com/documentation/en-us/red_hat_advanced_cluster_management_for_kubernetes/)
- [NIST 800-53](https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-53r5.pdf)
