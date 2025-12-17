# ACM Switchover RBAC Helm Chart

This Helm chart deploys RBAC resources for the ACM Switchover automation tool.

## TL;DR

```bash
helm install acm-switchover-rbac ./deploy/helm/acm-switchover-rbac
```

## Introduction

This chart bootstraps RBAC resources required for ACM Switchover operations on a Kubernetes cluster using the Helm package manager.

## Prerequisites

- Kubernetes 1.19+
- Helm 3.0+
- ACM (Advanced Cluster Management) 2.11+ installed

## Installing the Chart

### Install with default configuration

```bash
helm install acm-switchover-rbac ./deploy/helm/acm-switchover-rbac
```

### Install in custom namespace

```bash
helm install acm-switchover-rbac ./deploy/helm/acm-switchover-rbac \
  --set namespace=my-custom-namespace
```

### Install with custom ACM namespaces

If your ACM installation uses custom namespaces:

```bash
helm install acm-switchover-rbac ./deploy/helm/acm-switchover-rbac \
  --set role.namespaces.backup=my-backup-namespace \
  --set role.namespaces.observability=my-observability-namespace \
  --set role.namespaces.mce=my-mce-namespace
```

## Uninstalling the Chart

```bash
helm uninstall acm-switchover-rbac
```

This removes all Kubernetes resources associated with the chart.

## Configuration

The following table lists the configurable parameters and their default values.

| Parameter | Description | Default |
|-----------|-------------|---------|
| `namespace` | Namespace for service accounts | `acm-switchover` |
| `serviceAccount.create` | Create service accounts | `true` |
| `serviceAccount.operator.name` | Operator service account name | `acm-switchover-operator` |
| `serviceAccount.operator.annotations` | Operator service account annotations | `{}` |
| `serviceAccount.operator.labels` | Operator service account labels | `{}` |
| `serviceAccount.validator.name` | Validator service account name | `acm-switchover-validator` |
| `serviceAccount.validator.annotations` | Validator service account annotations | `{}` |
| `serviceAccount.validator.labels` | Validator service account labels | `{}` |
| `clusterRole.create` | Create cluster roles | `true` |
| `clusterRole.operator.name` | Operator cluster role name | `acm-switchover-operator` |
| `clusterRole.validator.name` | Validator cluster role name | `acm-switchover-validator` |
| `clusterRoleBinding.create` | Create cluster role bindings | `true` |
| `role.create` | Create namespace-scoped roles | `true` |
| `role.namespaces.backup` | ACM backup namespace | `open-cluster-management-backup` |
| `role.namespaces.observability` | ACM observability namespace | `open-cluster-management-observability` |
| `role.namespaces.mce` | Multicluster engine namespace | `multicluster-engine` |
| `roleBinding.create` | Create role bindings | `true` |
| `commonLabels` | Labels applied to all resources | See values.yaml |
| `commonAnnotations` | Annotations applied to all resources | `{}` |
| `environment` | Environment designation | `production` |
| `rbac.enabled` | Enable RBAC resource creation | `true` |
| `rbac.customOperatorRules` | Additional operator ClusterRole rules | `[]` |
| `rbac.customValidatorRules` | Additional validator ClusterRole rules | `[]` |
| `rbac.customNamespaces` | Additional namespaces for Roles | `[]` |

## Examples

### Production Deployment with Annotations

```bash
helm install acm-switchover-rbac ./deploy/helm/acm-switchover-rbac \
  --set environment=production \
  --set serviceAccount.operator.annotations."vault\.hashicorp\.com/role"=acm-switchover \
  --set serviceAccount.operator.annotations."eks\.amazonaws\.com/role-arn"=arn:aws:iam::123456789012:role/acm-switchover
```

### Development Deployment

```bash
helm install acm-switchover-rbac ./deploy/helm/acm-switchover-rbac \
  --set environment=development \
  --set commonLabels."environment"=dev
```

### Add Custom Permissions

Create a `custom-values.yaml` file:

```yaml
rbac:
  customOperatorRules:
    - apiGroups: ["custom.example.com"]
      resources: ["customresources"]
      verbs: ["get", "list", "create"]
```

Install with custom values:

```bash
helm install acm-switchover-rbac ./deploy/helm/acm-switchover-rbac \
  -f custom-values.yaml
```

### Disable Certain Resources

```bash
# Install without ClusterRoles (use existing ones)
helm install acm-switchover-rbac ./deploy/helm/acm-switchover-rbac \
  --set clusterRole.create=false
```

## Using the Service Accounts

### Get Service Account Token

```bash
# Get operator token
kubectl create token acm-switchover-operator \
  -n acm-switchover \
  --duration=24h

# Get validator token
kubectl create token acm-switchover-validator \
  -n acm-switchover \
  --duration=24h
```

### Create kubeconfig for Service Account

```bash
# Create kubeconfig with operator permissions
kubectl config set-credentials acm-switchover-operator \
  --token=$(kubectl create token acm-switchover-operator -n acm-switchover --duration=24h)

kubectl config set-context acm-switchover \
  --cluster=your-cluster \
  --user=acm-switchover-operator

# Use the context
kubectl config use-context acm-switchover
```

### Run as Pod with Service Account

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: acm-switchover
  namespace: acm-switchover
spec:
  serviceAccountName: acm-switchover-operator
  containers:
  - name: switchover
    image: quay.io/tomazborstnar/acm-switchover:latest
    args:
      - --primary-context=primary-hub
      - --secondary-context=secondary-hub
      - --method=passive
      - --old-hub-action=secondary
```

## Verification

### Verify Resources Created

```bash
# Check namespace
helm status acm-switchover-rbac

# List all resources
kubectl get sa,clusterrole,role,clusterrolebinding,rolebinding \
  -l app.kubernetes.io/name=acm-switchover-rbac

# Verify service accounts
kubectl get sa -n acm-switchover
```

### Test Permissions

```bash
# Test operator permissions
kubectl auth can-i list managedclusters \
  --as=system:serviceaccount:acm-switchover:acm-switchover-operator

# Test validator permissions (should be read-only)
kubectl auth can-i patch managedclusters \
  --as=system:serviceaccount:acm-switchover:acm-switchover-validator
# Should return "no"

kubectl auth can-i get managedclusters \
  --as=system:serviceaccount:acm-switchover:acm-switchover-validator
# Should return "yes"
```

## Upgrading

```bash
# Upgrade to new version
helm upgrade acm-switchover-rbac ./deploy/helm/acm-switchover-rbac

# Upgrade with new values
helm upgrade acm-switchover-rbac ./deploy/helm/acm-switchover-rbac \
  --set environment=staging
```

## Troubleshooting

### Chart Installation Fails

```bash
# Dry-run to see what would be created
helm install acm-switchover-rbac ./deploy/helm/acm-switchover-rbac --dry-run --debug

# Template chart to see generated manifests
helm template acm-switchover-rbac ./deploy/helm/acm-switchover-rbac
```

### Permission Denied Errors

```bash
# Verify RBAC resources exist
kubectl get clusterrole acm-switchover-operator -o yaml

# Check role bindings
kubectl get rolebinding -n open-cluster-management-backup

# Test specific permission
kubectl auth can-i <verb> <resource> \
  --as=system:serviceaccount:acm-switchover:acm-switchover-operator
```

### Missing Namespaces

If ACM namespaces don't exist:

```bash
# Create namespaces manually
kubectl create namespace open-cluster-management-backup
kubectl create namespace open-cluster-management-observability
kubectl create namespace multicluster-engine

# Then install chart
helm install acm-switchover-rbac ./deploy/helm/acm-switchover-rbac
```

## License

MIT License - See repository LICENSE file

## Links

- [GitHub Repository](https://github.com/tomazb/rh-acm-switchover)
- [RBAC Requirements Documentation](../../../docs/deployment/rbac-requirements.md)
- [ACM Documentation](https://access.redhat.com/documentation/en-us/red_hat_advanced_cluster_management_for_kubernetes/)
