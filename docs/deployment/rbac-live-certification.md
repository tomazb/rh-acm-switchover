# Live RBAC Bootstrap Certification

## Overview

The live RBAC bootstrap certification validates applied RBAC permissions end-to-end using SubjectAccessReview against a live or disposable cluster. It confirms that bootstrapped service accounts have the required permissions for switchover, restore-only, decommission, and old-hub finalization flows.

## Opt-In Activation

The certification scenario is **opt-in** via environment variable:

```bash
export ACM_ENABLE_LIVE_RBAC_CERTIFICATION=1
```

Accepted values: `1`, `true`, `TRUE`, `yes`, `YES`

## Release Profile Configuration

Add the `rbac-bootstrap-live` scenario to your release profile:

```yaml
scenarios:
  - id: rbac-bootstrap-live
    required: false
```

The scenario is marked as optional (`required: false`) to allow release validation to proceed even when live RBAC certification is not enabled.

## Prerequisites

1. **Bootstrapped RBAC**: The service accounts must already exist on both hubs:
   - `acm-switchover-operator` in the `acm-switchover` namespace

2. **Admin kubeconfig**: The release profile must reference kubeconfig files with sufficient privileges to create SubjectAccessReview resources and impersonate service accounts.

3. **Live cluster access**: The certification requires actual cluster connectivity (not mock/fake fixtures).

## What Gets Validated

### Primary Hub (Full Permissions)

The certification validates that the `acm-switchover-operator` service account has:

- **Cluster-scoped permissions**:
  - Read: namespaces, nodes, clusteroperators, clusterversions, managedclusters, clusterdeployments, multiclusterhubs, multiclusterobservabilities
  - Write: managedclusters (patch)
  - Delete: multiclusterobservabilities (for old-hub finalization)
  - Delete: managedclusters, multiclusterhubs (for decommission)

- **Namespace-scoped permissions**:
  - `open-cluster-management-backup`: backupschedules, restores, backups, configmaps, secrets, pods
  - `open-cluster-management`: pods
  - `open-cluster-management-observability`: statefulsets, deployments, pods, routes
  - `multicluster-engine`: configmaps

### Secondary Hub (Subset Permissions)

The certification validates the same read and write permissions, but excludes:
- Old-hub finalization delete permissions
- Full decommission delete permissions

## Execution Flow

1. Check if `ACM_ENABLE_LIVE_RBAC_CERTIFICATION` is set
2. If not set, skip with status `not_applicable`
3. If set, for each hub:
   - Build permission matrix based on role and flags
   - For each permission:
     - Create a SubjectAccessReview manifest
     - Submit via `oc create -f` with impersonation
     - Parse the `status.allowed` field
     - Record assertion (passed/failed)
4. Write certification summary artifact
5. Aggregate results and return status

## Artifacts

The certification produces the following artifacts in the run directory:

```
scenarios/rbac-bootstrap-live/
├── primary/
│   ├── rbac-certification-primary.json      # Primary hub summary
│   └── sar-*.json                            # Individual SAR manifests
└── secondary/
    ├── rbac-certification-secondary.json    # Secondary hub summary
    └── sar-*.json                            # Individual SAR manifests
```

### Summary Artifact Schema

```json
{
  "schema_version": 1,
  "status": "passed",
  "hub": "primary",
  "role": "operator",
  "service_account": "system:serviceaccount:acm-switchover:acm-switchover-operator",
  "include_decommission": true,
  "include_old_hub_finalization": true,
  "total_permissions": 145,
  "denied_count": 0,
  "assertions": [
    {
      "capability": "rbac-certification",
      "name": "cluster.open-cluster-management.io/managedclusters:get@cluster",
      "status": "passed",
      "expected": "allowed",
      "actual": "allowed",
      "evidence_path": "/path/to/sar-managedclusters-get.json",
      "message": "Permission allowed for system:serviceaccount:acm-switchover:acm-switchover-operator"
    }
  ]
}
```

## Integration with Release Validation

The live RBAC certification is a **local scenario** (stream: `local`) that executes as part of the main release certification flow:

1. Static gates (formatting, linting)
2. Lab readiness check
3. Baseline check
4. **Stream scenarios** (preflight, switchover, restore-only, etc.)
5. **Live RBAC certification** ← Runs after mutating scenarios
6. Runtime parity
7. Final baseline check

This ensures that RBAC is validated after the actual RBAC bootstrap scenario (`rbac-bootstrap`) has deployed the manifests.

## Comparison to Static RBAC Parity

| Aspect | Static RBAC Parity | Live RBAC Certification |
|--------|-------------------|------------------------|
| **Scope** | Manifest syntax and structure | Applied permissions |
| **Method** | YAML parsing and comparison | SubjectAccessReview API |
| **Clusters** | None required | Live/disposable cluster required |
| **Always runs** | Yes (part of static-gates) | No (opt-in via env var) |
| **CI-safe** | Yes | No (requires cluster access) |
| **Validation depth** | ClusterRole/Role YAML matches | Actual RBAC allows/denies |

Both are necessary for complete RBAC validation:
- **Static parity** ensures Python and Ansible manifest sets match
- **Live certification** proves the manifests grant the required permissions when applied

## Example Usage

### Local Development

```bash
# Bootstrap RBAC first (using Ansible collection)
ansible-playbook ansible_collections/tomazb/acm_switchover/playbooks/rbac_bootstrap.yml \
  -e acm_switchover_hubs='{"primary":{"kubeconfig":"/path/to/primary","context":"primary-ctx"}}' \
  -e acm_switchover_rbac_bootstrap='{"role":"operator","include_decommission":true}'

# Enable live certification
export ACM_ENABLE_LIVE_RBAC_CERTIFICATION=1

# Run release validation with rbac-bootstrap-live scenario
pytest tests/release/test_release_certification.py \
  --release-profile /path/to/release-profile.yaml \
  --release-scenario rbac-bootstrap-live
```

### CI/CD Pipeline (Gated)

```yaml
# In GitHub Actions or similar
- name: Live RBAC Certification
  if: env.RUN_LIVE_RBAC_CERT == 'true'
  env:
    ACM_ENABLE_LIVE_RBAC_CERTIFICATION: 1
  run: |
    pytest tests/release/test_release_certification.py \
      --release-profile profiles/disposable-cluster.yaml \
      --release-scenario rbac-bootstrap-live
```

## Troubleshooting

### Certification skipped (status: not_applicable)

**Cause**: `ACM_ENABLE_LIVE_RBAC_CERTIFICATION` is not set

**Solution**: Export the environment variable with a truthy value before running tests

### Permission denied assertions

**Cause**: Service account does not exist or lacks the expected permissions

**Solution**:
1. Verify the service account exists: `oc get sa -n acm-switchover`
2. Check ClusterRoleBindings: `oc get clusterrolebinding acm-switchover-operator`
3. Inspect SAR evidence files in `scenarios/rbac-bootstrap-live/*/sar-*.json`
4. Compare manifests in `deploy/rbac/` against applied resources

### SubjectAccessReview API errors

**Cause**: Kubeconfig lacks privileges to create SubjectAccessReview or impersonate

**Solution**: Ensure the kubeconfig in the release profile has cluster-admin or equivalent permissions

## See Also

- [RBAC Requirements](../deployment/rbac-requirements.md)
- [RBAC Deployment](../deployment/rbac-deployment.md)
- [RBAC Implementation](../development/rbac-implementation.md)
- [Release Validation Guide](release-validation.md)
- [Parity Matrix](../ansible-collection/parity-matrix.md)
