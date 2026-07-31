# Live RBAC Bootstrap Certification - Implementation Summary

## Overview

Successfully implemented a live RBAC bootstrap certification scenario that validates applied cluster permissions end-to-end using SubjectAccessReview. This addresses the follow-up from PR #56 where the mutating Python release adapter path for rbac-bootstrap was intentionally removed due to lack of safe non-mutating dry-run support.

## Implementation Details

### Core Components

1. **Scenario Definition** (`tests/release/scenarios/catalog.py`)
   - Added `rbac-bootstrap-live` as opt-in scenario; explicitly filtered runs are blocking
   - Stream: `local` (no Python/Ansible adapter required)
   - Mutates lab: `True` (applies SubjectAccessReview resources)
   - Runtime parity: `False` (release-only capability)

2. **Certification Module** (`tests/release/checks/rbac_certification.py`)
   - Permission matrix derived from `lib/rbac_validator.py`; existing parity tests keep `acm_rbac_validate.py` aligned
   - SubjectAccessReview-based validation with service account impersonation
   - Support for operator and validator roles
   - Decommission and old-hub finalization permission flags
   - Structured assertion results with collision-safe SAR request/evidence paths
   - Least-privilege deny checks for dangerous unrelated permissions
   - Schema version 1 JSON artifacts

3. **Orchestrator Integration** (`tests/release/orchestrator.py`)
   - Executes after stream scenarios, before runtime parity
   - Certifies primary and secondary hubs using profile-driven RBAC scopes
   - Aggregates results into local scenario result

### Permission Coverage

**Cluster-scoped:**
- Core: namespaces, nodes
- OpenShift: clusteroperators, clusterversions
- ACM: managedclusters (get/list/patch), multiclusterhubs (get/list)
- Hive: clusterdeployments (get/list)
- Observability: multiclusterobservabilities (get/list/delete)

**Namespace-scoped:**
- `open-cluster-management-backup`: backupschedules, restores, backups, configmaps, secrets, pods
- `open-cluster-management`: pods
- `open-cluster-management-observability`: statefulsets, deployments, pods, routes
- `multicluster-engine`: configmaps

**Decommission extensions:**
- ManagedCluster delete
- MultiClusterHub delete
- MultiClusterObservability delete (also for old-hub finalization)

### Opt-In Control

- Environment variable: `ACM_ENABLE_LIVE_RBAC_CERTIFICATION=1`
- Accepted values: `1`, `true`, `TRUE`, `yes`, `YES`
- Scenario skipped when not set (status: `not_applicable`)
- Safe for production release validation

### Test Coverage

Added and extended comprehensive tests (`tests/release/checks/test_rbac_certification.py`):
- Permission matrix construction for operator/validator roles
- Decommission and old-hub finalization flag handling
- Environment variable opt-in logic
- SAR spec generation (cluster-scoped and namespace-scoped)
- Certification result dataclass
- Invalid role handling
- Skipped scenario behavior
- RBACValidator matrix parity
- Least-privilege deny checks
- Collision-safe evidence artifact generation
- Orchestrator profile-driven certification scope

Targeted release framework tests cover the new certification path; run `python -m pytest tests/release/ -q` before using the PR as merge evidence.

## Documentation

1. **Live Certification Guide** (`docs/deployment/rbac-live-certification.md`)
   - Overview and opt-in activation
   - Release profile configuration
   - Prerequisites and cluster requirements
   - Permission coverage details
   - Execution flow
   - Artifact schema
   - Integration with release validation
   - Comparison to static RBAC parity
   - Example usage and troubleshooting

2. **Parity Matrix Update** (`docs/ansible-collection/parity-matrix.md`)
   - Added RBAC live certification as `release-only` status
   - Documented as complementary to static RBAC parity checks

3. **Example Profile** (`tests/release/profiles/full-release-with-rbac-cert.example.yaml`)
   - Demonstrates `rbac-bootstrap-live` scenario configuration
   - Documents opt-in requirement

4. **CHANGELOG** (`CHANGELOG.md`)
   - Added features section with all components
   - Clear description of opt-in mechanism

## Acceptance Criteria Status

✅ **Documented live/disposable-cluster fixture exists**
- Guide explains cluster requirements and opt-in mechanism

✅ **Validates applied RBAC permissions end-to-end**
- Uses SubjectAccessReview API with service account impersonation

✅ **Confirms MCO delete permission**
- Included for old-hub finalization and full decommission

✅ **Skipped unless explicitly configured**
- Controlled by `ACM_ENABLE_LIVE_RBAC_CERTIFICATION` environment variable and profile/CLI scenario selection

✅ **Integrates with release validation safely**
- Executes as opt-in local scenario; explicitly selected live certification failures are blocking
- Does not affect ordinary unit/integration test runs

✅ **Documentation explains execution and assumptions**
- Comprehensive guide with prerequisites, usage, and troubleshooting

✅ **CI/release profile metadata distinguishes checks**
- Parity matrix documents `release-only` status
- Static parity remains in static-gates
- Live certification is opt-in scenario

## Deployment Path

1. **Bootstrap RBAC** (prerequisite):
   ```bash
   ansible-playbook ansible_collections/tomazb/acm_switchover/playbooks/rbac_bootstrap.yml \
     -e acm_switchover_hubs='...' \
     -e acm_switchover_rbac_bootstrap='{"role":"operator","include_decommission":true}'
   ```

2. **Enable certification**:
   ```bash
   export ACM_ENABLE_LIVE_RBAC_CERTIFICATION=1
   ```

3. **Run release validation**:
   ```bash
   pytest tests/release/test_release_certification.py \
     --release-profile /path/to/profile.yaml \
     --release-scenario rbac-bootstrap-live
   ```

## Design Decisions

1. **Local stream over adapter**: Certification logic lives in release framework, not stream adapters, because it's release-specific validation, not a switchover workflow capability.

2. **Opt-in by default**: Live cluster access is not always available in CI/dev environments; opt-in keeps normal test runs safe.

3. **SubjectAccessReview over SSAR**: Uses impersonation (`user` field) rather than self-subject checks to validate the bootstrapped service account permissions, not the admin kubeconfig.

4. **Profile-driven scope**: Primary and secondary certification scopes are declared in the release profile so baseline RBAC and optional decommission/finalization extensions can be certified separately.

5. **Evidence artifacts**: Individual SAR request and sanitized evidence files provide troubleshooting context when permissions are denied.

## Future Enhancements

1. **Python CLI --setup --dry-run**: Consider adding non-mutating Python RBAC bootstrap release adapter path (blocked this issue but not required).

2. **Validator role certification**: Profile-driven certification supports the validator role; add a dedicated checked-in validator example profile if release process needs one.

3. **Managed cluster RBAC**: Extend to validate spoke cluster permissions for klusterlet reconnection.

4. **ArgoCD permissions**: Extend to validate application management permissions for GitOps workflows.

## Related

- PR #56: 1.7.10 production resilience hardening
- Issue #57: Add live RBAC bootstrap certification release scenario
- Parity matrix: `docs/ansible-collection/parity-matrix.md`
- RBAC requirements: `docs/deployment/rbac-requirements.md`
- RBAC deployment: `docs/deployment/rbac-deployment.md`
