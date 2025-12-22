# Kustomize Deployment for ACM Switchover RBAC

This directory contains Kustomize manifests for deploying RBAC resources for the ACM Switchover tool.

## Directory Structure

```
deploy/kustomize/
├── base/
│   └── kustomization.yaml          # Base RBAC configuration
└── overlays/
    ├── production/
    │   └── kustomization.yaml      # Production-specific configuration
    └── development/
        └── kustomization.yaml      # Development-specific configuration
```

## Quick Start

### Deploy Base RBAC Configuration

```bash
# Apply RBAC resources to the cluster
kubectl apply -k deploy/kustomize/base/

# Verify resources
kubectl get sa,clusterrole,role,clusterrolebinding,rolebinding -n acm-switchover
```

### Deploy with Production Overlay

```bash
# Apply production configuration
kubectl apply -k deploy/kustomize/overlays/production/

# Verify deployment
kubectl get sa -n acm-switchover -o wide
```

### Deploy with Development Overlay

```bash
# Apply development configuration
kubectl apply -k deploy/kustomize/overlays/development/

# Verify deployment
kubectl get sa -n acm-switchover -o wide
```

## What Gets Deployed

### Namespace
- `acm-switchover`: Dedicated namespace for service accounts

### Service Accounts
- `acm-switchover-operator`: Full operational permissions
- `acm-switchover-validator`: Read-only permissions for validation

### ClusterRoles
- `acm-switchover-operator`: Cluster-wide permissions for operator
- `acm-switchover-validator`: Cluster-wide read-only permissions

### Roles (Namespace-scoped)
Roles created in the following namespaces:
- `open-cluster-management-backup`
- `open-cluster-management-observability`
- `multicluster-engine`

### ClusterRoleBindings
- Binds ClusterRoles to ServiceAccounts

### RoleBindings
- Binds namespace-scoped Roles to ServiceAccounts

## Using Service Accounts

### Option 1: Generate kubeconfig for Service Account

```bash
# For operator service account
./scripts/generate-sa-kubeconfig.sh acm-switchover acm-switchover-operator > /tmp/operator-kubeconfig.yaml

# For validator service account
./scripts/generate-sa-kubeconfig.sh acm-switchover acm-switchover-validator > /tmp/validator-kubeconfig.yaml

# Use with switchover tool
export KUBECONFIG=/tmp/operator-kubeconfig.yaml
python acm_switchover.py --primary-context default --secondary-context default ...
```

### Option 2: Run as Pod in Cluster

Create a Job or CronJob that uses the service account:

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: acm-switchover-job
  namespace: acm-switchover
spec:
  template:
    spec:
      serviceAccountName: acm-switchover-operator
      containers:
      - name: switchover
        image: quay.io/tomazborstnar/acm-switchover:latest
        args:
          - --primary-context
          - primary-hub
          - --secondary-context
          - secondary-hub
          - --method
          - passive
          - --old-hub-action
          - secondary
      restartPolicy: Never
```

### Option 3: Impersonate Service Account (for testing)

```bash
# Test operator permissions
kubectl auth can-i list managedclusters \
  --as=system:serviceaccount:acm-switchover:acm-switchover-operator

# Test validator permissions (should be read-only)
kubectl auth can-i patch managedclusters \
  --as=system:serviceaccount:acm-switchover:acm-switchover-validator
# Should return "no"
```

## Customization

### Custom Namespace

If ACM namespaces differ in your environment, update the Role and RoleBinding namespaces:

```yaml
# In deploy/rbac/role.yaml
metadata:
  namespace: your-custom-backup-namespace
```

### Additional Permissions

To grant additional permissions, edit the ClusterRole or Role files:

```yaml
# deploy/rbac/clusterrole.yaml
rules:
  - apiGroups: ["your.api.group"]
    resources: ["your-resources"]
    verbs: ["get", "list"]
```

Then rebuild with Kustomize:

```bash
kubectl apply -k deploy/kustomize/base/
```

## Verification

### Verify All Resources Deployed

```bash
# Check namespace
kubectl get namespace acm-switchover

# Check service accounts
kubectl get sa -n acm-switchover

# Check cluster roles
kubectl get clusterrole | grep acm-switchover

# Check cluster role bindings
kubectl get clusterrolebinding | grep acm-switchover

# Check roles in ACM namespaces
kubectl get role -n open-cluster-management-backup
kubectl get role -n open-cluster-management-observability
kubectl get role -n multicluster-engine

# Check role bindings
kubectl get rolebinding -n open-cluster-management-backup
kubectl get rolebinding -n open-cluster-management-observability
kubectl get rolebinding -n multicluster-engine
```

### Verify Permissions

```bash
# Test operator can perform operations
kubectl auth can-i --list \
  --as=system:serviceaccount:acm-switchover:acm-switchover-operator

# Test validator has read-only access
kubectl auth can-i patch backupschedules \
  --as=system:serviceaccount:acm-switchover:acm-switchover-validator \
  -n open-cluster-management-backup
# Should return "no"

kubectl auth can-i get backupschedules \
  --as=system:serviceaccount:acm-switchover:acm-switchover-validator \
  -n open-cluster-management-backup
# Should return "yes"
```

## Cleanup

```bash
# Remove all RBAC resources
kubectl delete -k deploy/kustomize/base/

# Verify cleanup
kubectl get namespace acm-switchover
# Should not exist
```

## Troubleshooting

### Permission Denied Errors

If you get permission denied errors:

1. Verify the service account exists:
   ```bash
   kubectl get sa acm-switchover-operator -n acm-switchover
   ```

2. Check ClusterRoleBinding:
   ```bash
   kubectl get clusterrolebinding acm-switchover-operator -o yaml
   ```

3. Verify permissions:
   ```bash
   kubectl auth can-i <verb> <resource> \
     --as=system:serviceaccount:acm-switchover:acm-switchover-operator
   ```

### Missing Namespaces

If ACM namespaces don't exist yet:

```bash
# Create namespaces (ACM operator usually creates these)
kubectl create namespace open-cluster-management-backup
kubectl create namespace open-cluster-management-observability
kubectl create namespace multicluster-engine

# Then apply RBAC
kubectl apply -k deploy/kustomize/base/
```

### Kustomize Build Errors

If you encounter build errors:

```bash
# Build without applying to check for errors
kubectl kustomize deploy/kustomize/base/

# Check specific overlay
kubectl kustomize deploy/kustomize/overlays/production/
```

## Integration with CI/CD

### GitOps with ArgoCD

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: acm-switchover-rbac
  namespace: argocd
spec:
  project: default
  source:
    repoURL: https://github.com/tomazb/rh-acm-switchover.git
    targetRevision: main
    path: deploy/kustomize/overlays/production
  destination:
    server: https://kubernetes.default.svc
    namespace: acm-switchover
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
```

### Flux CD

```yaml
apiVersion: kustomize.toolkit.fluxcd.io/v1beta2
kind: Kustomization
metadata:
  name: acm-switchover-rbac
  namespace: flux-system
spec:
  interval: 10m
  path: ./deploy/kustomize/overlays/production
  prune: true
  sourceRef:
    kind: GitRepository
    name: acm-switchover
```

## References

- [Kustomize Documentation](https://kubernetes.io/docs/tasks/manage-kubernetes-objects/kustomization/)
- [Kubernetes RBAC](https://kubernetes.io/docs/reference/access-authn-authz/rbac/)
- [ACM Switchover RBAC Requirements](../../docs/deployment/rbac-requirements.md)
