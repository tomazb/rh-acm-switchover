# RBAC Deployment Guide

This guide provides step-by-step instructions for deploying RBAC resources for the ACM Switchover tool.

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Deployment Options](#deployment-options)
3. [Quick Start](#quick-start)
4. [Deployment Methods](#deployment-methods)
5. [Validation](#validation)
6. [Usage](#usage)
7. [Troubleshooting](#troubleshooting)

## Prerequisites

- Kubernetes 1.19+ or OpenShift 4.11+
- ACM (Advanced Cluster Management) 2.11+ installed
- `kubectl` or `oc` CLI configured
- Cluster admin privileges to create RBAC resources

## Deployment Options

Choose the deployment method that best fits your environment:

| Method | Best For | Complexity |
|--------|----------|------------|
| **kubectl apply** | Quick testing, simple deployments | Low |
| **Kustomize** | GitOps, environment-specific configs | Medium |
| **Helm** | Template-based deployments, version management | Medium |
| **ACM Policy** | Multi-cluster governance, automated compliance | High |

## Quick Start

### Option 1: Direct kubectl/oc Apply

```bash
# Clone repository
git clone https://github.com/tomazb/rh-acm-switchover.git
cd rh-acm-switchover

# Apply RBAC resources
kubectl apply -f deploy/rbac/

# Verify deployment
kubectl get sa,clusterrole,role -n acm-switchover
```

### Option 2: Kustomize

```bash
# Apply base configuration
kubectl apply -k deploy/kustomize/base/

# OR apply production overlay
kubectl apply -k deploy/kustomize/overlays/production/

# Verify
kubectl get all -n acm-switchover
```

### Option 3: Helm

```bash
# Install Helm chart
helm install acm-switchover-rbac deploy/helm/acm-switchover-rbac/

# Verify
helm status acm-switchover-rbac
kubectl get sa -n acm-switchover
```

## Deployment Methods

### Method 1: kubectl/oc Direct Apply

#### Step 1: Create Namespace

```bash
kubectl apply -f deploy/rbac/namespace.yaml
```

#### Step 2: Create Service Accounts

```bash
kubectl apply -f deploy/rbac/serviceaccount.yaml
```

#### Step 3: Create RBAC Resources

```bash
# Create ClusterRoles
kubectl apply -f deploy/rbac/clusterrole.yaml

# Create ClusterRoleBindings
kubectl apply -f deploy/rbac/clusterrolebinding.yaml

# Create namespace-scoped Roles
kubectl apply -f deploy/rbac/role.yaml

# Create RoleBindings
kubectl apply -f deploy/rbac/rolebinding.yaml
```

#### Step 4: Verify

```bash
kubectl get namespace acm-switchover
kubectl get sa -n acm-switchover
kubectl get clusterrole | grep acm-switchover
kubectl get role -n open-cluster-management-backup
```

### Method 2: Kustomize

#### Base Configuration

```bash
# Apply all resources at once
kubectl apply -k deploy/kustomize/base/

# View what would be applied (dry-run)
kubectl apply -k deploy/kustomize/base/ --dry-run=client -o yaml
```

#### Production Environment

```bash
# Apply production overlay
kubectl apply -k deploy/kustomize/overlays/production/

# Customize with your own overlay
cp -r deploy/kustomize/overlays/production deploy/kustomize/overlays/my-env
# Edit deploy/kustomize/overlays/my-env/kustomization.yaml
kubectl apply -k deploy/kustomize/overlays/my-env/
```

#### Custom Namespaces

If your ACM installation uses custom namespaces:

```bash
# Create custom kustomization
cat > /tmp/kustomization.yaml <<EOF
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization

bases:
  - ../../base

patches:
  - patch: |-
      apiVersion: rbac.authorization.k8s.io/v1
      kind: Role
      metadata:
        name: acm-switchover-operator
        namespace: my-custom-backup-namespace
    target:
      kind: Role
      namespace: open-cluster-management-backup
EOF

kubectl apply -k /tmp/
```

### Method 3: Helm

#### Basic Installation

```bash
# Install with defaults
helm install acm-switchover-rbac deploy/helm/acm-switchover-rbac/

# Install in custom namespace
helm install acm-switchover-rbac deploy/helm/acm-switchover-rbac/ \
  --set namespace=my-namespace
```

#### Custom Values

Create a `values.yaml` file:

```yaml
# custom-values.yaml
namespace: acm-switchover

serviceAccount:
  operator:
    annotations:
      description: "My custom operator SA"

role:
  namespaces:
    backup: my-custom-backup-namespace
    observability: my-custom-observability-namespace
    mce: my-custom-mce-namespace

commonLabels:
  environment: production
  team: platform
```

Install with custom values:

```bash
helm install acm-switchover-rbac deploy/helm/acm-switchover-rbac/ \
  -f custom-values.yaml
```

#### Upgrade Existing Installation

```bash
# Upgrade to new configuration
helm upgrade acm-switchover-rbac deploy/helm/acm-switchover-rbac/ \
  -f custom-values.yaml

# Verify upgrade
helm history acm-switchover-rbac
```

### Method 4: ACM Policy Governance

Deploy RBAC resources via ACM Policy for multi-cluster enforcement:

```bash
# Apply policy to ACM hub
kubectl apply -f deploy/acm-policies/policy-rbac.yaml

# Check policy status
kubectl get policy -n open-cluster-management-policies

# View compliance across clusters
kubectl get policy policy-acm-switchover-rbac \
  -n open-cluster-management-policies \
  -o jsonpath='{.status.status[*].clustername}'
```

For detailed ACM Policy usage, see [deploy/acm-policies/README.md](../../deploy/acm-policies/README.md).

## Validation

### Check RBAC Permissions

Use the built-in RBAC checker:

```bash
# Check current context
python check_rbac.py

# Check specific context
python check_rbac.py --context primary-hub

# Check both hubs
python check_rbac.py \
  --primary-context primary-hub \
  --secondary-context secondary-hub

# Include decommission permissions
python check_rbac.py --include-decommission
```

### Manual Verification

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

### Comprehensive Permission Check

```bash
# List all permissions for operator
kubectl auth can-i --list \
  --as=system:serviceaccount:acm-switchover:acm-switchover-operator

# List all permissions for validator
kubectl auth can-i --list \
  --as=system:serviceaccount:acm-switchover:acm-switchover-validator
```

## Usage

### Generate Service Account Token

```bash
# Create long-lived token (24 hours)
kubectl create token acm-switchover-operator \
  -n acm-switchover \
  --duration=24h

# Store in variable
SA_TOKEN=$(kubectl create token acm-switchover-operator \
  -n acm-switchover \
  --duration=24h)
```

### Generate kubeconfig for Service Account

Use the helper script (create it first):

```bash
# Create kubeconfig generator script
cat > /tmp/generate-sa-kubeconfig.sh <<'EOF'
#!/bin/bash
# Generate kubeconfig for service account

NAMESPACE=${1:-acm-switchover}
SA_NAME=${2:-acm-switchover-operator}

# Get cluster info
CLUSTER_NAME=$(kubectl config view --minify -o jsonpath='{.clusters[0].name}')
SERVER=$(kubectl config view --minify -o jsonpath='{.clusters[0].cluster.server}')
CA_DATA=$(kubectl config view --minify --raw -o jsonpath='{.clusters[0].cluster.certificate-authority-data}')

# Generate token
TOKEN=$(kubectl create token ${SA_NAME} -n ${NAMESPACE} --duration=24h)

# Create kubeconfig
cat <<KUBECONFIG
apiVersion: v1
kind: Config
clusters:
- cluster:
    certificate-authority-data: ${CA_DATA}
    server: ${SERVER}
  name: ${CLUSTER_NAME}
contexts:
- context:
    cluster: ${CLUSTER_NAME}
    user: ${SA_NAME}
  name: ${SA_NAME}@${CLUSTER_NAME}
current-context: ${SA_NAME}@${CLUSTER_NAME}
users:
- name: ${SA_NAME}
  user:
    token: ${TOKEN}
KUBECONFIG
EOF

chmod +x /tmp/generate-sa-kubeconfig.sh
```

Generate kubeconfig:

```bash
# Generate for operator
/tmp/generate-sa-kubeconfig.sh acm-switchover acm-switchover-operator > /tmp/operator-kubeconfig.yaml

# Test it
kubectl --kubeconfig=/tmp/operator-kubeconfig.yaml get managedclusters
```

### Run Switchover with Service Account

```bash
# Export kubeconfig
export KUBECONFIG=/tmp/operator-kubeconfig.yaml

# Run switchover
python acm_switchover.py \
  --primary-context default \
  --secondary-context default \
  --method passive \
  --old-hub-action secondary
```

## Troubleshooting

### Issue: Namespace Already Exists

```bash
# If namespace exists, skip namespace creation
kubectl apply -f deploy/rbac/serviceaccount.yaml
kubectl apply -f deploy/rbac/clusterrole.yaml
kubectl apply -f deploy/rbac/clusterrolebinding.yaml
kubectl apply -f deploy/rbac/role.yaml
kubectl apply -f deploy/rbac/rolebinding.yaml
```

### Issue: ACM Namespaces Don't Exist

```bash
# Create ACM namespaces manually
kubectl create namespace open-cluster-management-backup
kubectl create namespace open-cluster-management-observability
kubectl create namespace multicluster-engine

# Then apply RBAC
kubectl apply -f deploy/rbac/
```

### Issue: Permission Denied

```bash
# Check if you have admin privileges
kubectl auth can-i create clusterrole

# If not, ask cluster admin to apply RBAC resources
# Or use your existing admin credentials
```

### Issue: RoleBinding Creation Fails

```bash
# Check if namespace exists
kubectl get namespace open-cluster-management-backup

# If not, either create it or skip that RoleBinding
kubectl create namespace open-cluster-management-backup
```

### Issue: Helm Install Fails

```bash
# Debug with dry-run
helm install acm-switchover-rbac deploy/helm/acm-switchover-rbac/ --dry-run --debug

# Check template rendering
helm template acm-switchover-rbac deploy/helm/acm-switchover-rbac/

# Verify chart
helm lint deploy/helm/acm-switchover-rbac/
```

## Cleanup

### Remove All RBAC Resources

```bash
# With kubectl
kubectl delete -f deploy/rbac/

# With Kustomize
kubectl delete -k deploy/kustomize/base/

# With Helm
helm uninstall acm-switchover-rbac

# With ACM Policy
kubectl delete -f deploy/acm-policies/policy-rbac.yaml
```

### Remove Specific Resources

```bash
# Remove just service accounts
kubectl delete sa -n acm-switchover acm-switchover-operator acm-switchover-validator

# Remove just cluster roles
kubectl delete clusterrole acm-switchover-operator acm-switchover-validator

# Remove namespace (WARNING: removes everything)
kubectl delete namespace acm-switchover
```

## Best Practices

1. **Use GitOps**: Store RBAC manifests in Git and deploy via ArgoCD/Flux
2. **Separate Environments**: Use Kustomize overlays for dev/staging/prod
3. **Regular Audits**: Review RBAC permissions quarterly
4. **Least Privilege**: Use validator service account for read-only operations
5. **Token Rotation**: Regenerate service account tokens regularly
6. **Namespace Isolation**: Deploy RBAC in dedicated namespace
7. **Policy Enforcement**: Use ACM policies for multi-cluster governance

## Next Steps

- Review [RBAC Requirements](rbac-requirements.md)
- Check [Kustomize README](../../deploy/kustomize/README.md)
- Check [Helm Chart README](../../deploy/helm/acm-switchover-rbac/README.md)
- Check [ACM Policy README](../../deploy/acm-policies/README.md)
- Run [RBAC validation](../../check_rbac.py)

## Support

For issues or questions:
- GitHub Issues: https://github.com/tomazb/rh-acm-switchover/issues
- Documentation: https://github.com/tomazb/rh-acm-switchover/tree/main/docs
