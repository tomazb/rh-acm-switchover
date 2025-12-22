# ACM Switchover Helm Chart

This Helm chart deploys the ACM Switchover tool as a Kubernetes Job or CronJob.

## Prerequisites

- Kubernetes 1.23+
- Helm 3.8+
- A kubeconfig secret with contexts for both hub clusters

## Installation

### Basic Installation

```bash
helm install acm-switchover ./packaging/helm/acm-switchover \
  --namespace acm-switchover \
  --create-namespace \
  --set kubeconfig.secretName=my-kubeconfig \
  --set switchover.primaryContext=primary-hub \
  --set switchover.secondaryContext=secondary-hub
```

### With Custom Values

```bash
helm install acm-switchover ./packaging/helm/acm-switchover \
  --namespace acm-switchover \
  --create-namespace \
  -f my-values.yaml
```

## Configuration

See [values.yaml](values.yaml) for all configurable options.

### Key Configuration Options

| Parameter | Description | Default |
|-----------|-------------|---------|
| `image.repository` | Container image repository | `quay.io/tomazb/acm-switchover` |
| `image.tag` | Container image tag | Chart appVersion |
| `switchover.primaryContext` | Primary hub context name | `""` (required) |
| `switchover.secondaryContext` | Secondary hub context name | `""` (required) |
| `switchover.method` | Switchover method | `passive-sync` |
| `switchover.oldHubAction` | Action for old hub | `secondary` |
| `switchover.dryRun` | Enable dry-run mode | `false` |
| `kubeconfig.secretName` | Secret containing kubeconfig | `""` (required) |
| `persistence.enabled` | Enable PVC for state | `true` |
| `job.enabled` | Deploy as Job | `true` |
| `cronjob.enabled` | Deploy as CronJob | `false` |

### Kubeconfig Secret

Create a secret with your kubeconfig:

```bash
kubectl create secret generic acm-kubeconfig \
  --namespace acm-switchover \
  --from-file=config=$HOME/.kube/config
```

Then reference it in values:

```yaml
kubeconfig:
  secretName: acm-kubeconfig
  key: config
```

### State Persistence

The chart creates a PVC for state file persistence. This allows resuming interrupted operations:

```yaml
persistence:
  enabled: true
  size: 100Mi
  storageClass: ""  # Use default storage class
```

## CronJob Mode

For scheduled validation runs:

```yaml
job:
  enabled: false

cronjob:
  enabled: true
  schedule: "0 */6 * * *"  # Every 6 hours

switchover:
  dryRun: true  # Always use dry-run for scheduled runs
```

## RBAC Only

To deploy only RBAC resources without the application, use the standalone RBAC chart:

```bash
helm install acm-switchover-rbac ./deploy/helm/acm-switchover-rbac \
  --namespace acm-switchover \
  --create-namespace
```

## Uninstallation

```bash
helm uninstall acm-switchover --namespace acm-switchover
kubectl delete namespace acm-switchover
```
