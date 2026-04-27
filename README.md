# ACM Hub Switchover Automation

**Version 1.7.6** (2026-04-27)

Automated, idempotent tool for switching over Red Hat Advanced Cluster Management (ACM) from a primary hub to a secondary hub cluster. Available in two form factors:

- **Python CLI** (`acm_switchover.py`) — standalone script with full phase orchestration, state persistence, and rich CLI surface.
- **Ansible Collection** (`tomazb.acm_switchover`) — production-ready collection for `ansible-core` CLI and Ansible Automation Platform (AAP), with roles and playbooks covering the full switchover workflow (see [`ansible_collections/tomazb/acm_switchover/`](ansible_collections/tomazb/acm_switchover/)).

## Features

- ✅ **Idempotent execution** - Resume from last successful step
- ✅ **Comprehensive validation** - Pre-flight and post-flight checks for safety
- ✅ **RBAC enforcement** - Least privilege access control with validation
- ✅ **ArgoCD support (production-ready)** - Pause/resume ACM-touching Applications with automatic CRD detection
- ✅ **Data protection** - Verifies `preserveOnDelete` on ClusterDeployments
- ✅ **Auto-detection** - Automatically detects ACM Observability and version
- ✅ **Dry-run mode** - Preview actions without making changes
- ✅ **Validate-only mode** - Run all validations without execution
- ✅ **State tracking** - JSON state file for resume capability
- ✅ **Two methods supported** - Continuous passive restore (Method 1) or one-time full restore (Method 2)
- ✅ **Restore-only mode** - Restore managed clusters from S3 backups onto a new hub when the old hub is gone
- ✅ **Multi-deployment support** - RBAC via Kustomize, Helm, or ACM Policies

---

## ✅ ArgoCD Support Is Production-Ready

ArgoCD integration is fully available and stable in the switchover workflow.

- Automatic read-only ArgoCD discovery runs when the Applications CRD is present
- Optional managed pause/resume is available through `--argocd-manage`
- Resume-only mode is available through `--argocd-resume-only` (after updating Git for the new hub)

For full ArgoCD behavior, constraints, and examples, see [Detailed Usage Guide](docs/operations/usage.md) and [Scripts README](scripts/README.md).

---

## 🔒 RBAC Security Model

The ACM Switchover tool uses a **least-privilege RBAC model** with two distinct roles:

| Role | Purpose | Access Level |
|------|---------|--------------|
| **Operator** | Execute switchovers | Full read/write to ACM resources |
| **Validator** | Pre-flight validation, dry-runs | Read-only access |

### Quick RBAC Setup

```bash
# Deploy RBAC to both hubs (requires cluster-admin)
kubectl --context primary-hub apply -f deploy/rbac/
kubectl --context secondary-hub apply -f deploy/rbac/

# Deploy managed cluster RBAC via ACM Policy (optional, for klusterlet operations)
kubectl --context primary-hub apply -f deploy/acm-policies/policy-managed-cluster-rbac.yaml

# Validate RBAC permissions
python check_rbac.py --context primary-hub --role operator
python check_rbac.py --context secondary-hub --role operator

# Validate managed cluster RBAC
python check_rbac.py --context prod1 --managed-cluster --role operator
```

📖 **Full Guide:** [RBAC Deployment Guide](docs/deployment/rbac-deployment.md) | [RBAC Requirements](docs/deployment/rbac-requirements.md)

---

## ✅ Pre-flight & Post-flight Validation

Standalone validation scripts ensure safe and successful switchovers:

### Pre-flight Checks (Before Switchover)

```bash
# Auto-discover ACM hubs from kubeconfig
./scripts/discover-hub.sh --auto

# Run comprehensive pre-flight validation
./scripts/preflight-check.sh --primary-context primary-hub --secondary-context secondary-hub
```

**Validates:** ACM versions match, backups complete, OADP healthy, passive sync ready, ClusterDeployments protected, ManagedClusters in backup

### Post-flight Checks (After Switchover)

```bash
# Verify switchover completed successfully
./scripts/postflight-check.sh --old-hub-context primary-hub --new-hub-context secondary-hub
```

**Verifies:** ManagedClusters connected, backups running on new hub, Observability healthy, old hub properly configured

📖 **Full Guide:** [Scripts README](scripts/README.md) | [Quick Reference](docs/operations/quickref.md)

---

## Documentation

- **[Quick Reference](docs/operations/quickref.md)** - Command cheat sheet and common tasks
- **[Detailed Usage Guide](docs/operations/usage.md)** - Complete examples and scenarios
- **[RBAC Requirements](docs/deployment/rbac-requirements.md)** - Complete RBAC permissions guide
- **[RBAC Deployment](docs/deployment/rbac-deployment.md)** - Step-by-step RBAC deployment instructions
- **[ACM Switchover Runbook](docs/ACM_SWITCHOVER_RUNBOOK.md)** - Detailed operational procedures
- **[Installation Guide](docs/getting-started/install.md)** - Detailed installation instructions
- **[Architecture](docs/development/architecture.md)** - Design and implementation details
- **[Testing Guide](docs/development/testing.md)** - How to run tests and CI/CD
- **[Contributing](CONTRIBUTING.md)** - Development guidelines

See [docs/README.md](docs/README.md) for complete documentation index.

## Prerequisites

- Python 3.10+
- `kubectl` or `oc` CLI configured for both primary and secondary hubs
- ACM Backup configured on both hubs
- OADP operator installed on both hubs
- Network access to both Kubernetes clusters
- **RBAC permissions**: Required permissions for switchover operations (see [RBAC Requirements](docs/deployment/rbac-requirements.md))

## Installation

### Option 1: From Source

```bash
# Clone the repository
git clone https://github.com/tomazb/rh-acm-switchover.git
cd rh-acm-switchover

# Install dependencies
pip install -r requirements.txt
```


## Usage

### Basic Switchover (Method 1 - Continuous Passive Restore)

```bash
# Validate everything first
python acm_switchover.py --validate-only \
  --primary-context primary-hub \
  --secondary-context secondary-hub

# Dry-run to see what would happen
python acm_switchover.py --dry-run \
  --primary-context primary-hub \
  --secondary-context secondary-hub \
  --old-hub-action secondary \
  --method passive

# Actual execution
python acm_switchover.py \
  --primary-context primary-hub \
  --secondary-context secondary-hub \
  --old-hub-action secondary \
  --method passive
```

### One-Time Full Restore (Method 2)

```bash
python acm_switchover.py \
  --primary-context primary-hub \
  --secondary-context secondary-hub \
  --old-hub-action decommission \
  --method full
```

### ArgoCD-Managed Switchover (Production)

Use this when ArgoCD manages ACM resources and you want the tool to coordinate pause/resume safely.

```bash
python acm_switchover.py \
  --primary-context primary-hub \
  --secondary-context secondary-hub \
  --old-hub-action secondary \
  --method passive \
  --argocd-manage
```

Applications are left paused by default. After updating Git/desired state for the new hub, resume explicitly with `--argocd-resume-only`.

### Resume from Previous Run

```bash
# Script automatically resumes from last successful step
python acm_switchover.py \
  --primary-context primary-hub \
  --secondary-context secondary-hub \
  --old-hub-action secondary \
  --method passive \
  --state-file .state/switchover-<primary>__<secondary>.json
```

### Returning to Original Hub

To return to the original hub, perform a reverse switchover by swapping contexts:

```bash
# Swap --primary-context and --secondary-context values
python acm_switchover.py \
  --primary-context secondary-hub \
  --secondary-context primary-hub \
  --method passive \
  --old-hub-action secondary
```

If you later use `--argocd-resume-only` after a reverse switchover, the CLI will reuse the original state file automatically when the swapped-context match is unambiguous. If both context orderings have state files, pass `--state-file` explicitly.

> **Note:** Requires original switchover used `--old-hub-action secondary` to enable passive sync.

### Decommission Old Hub

```bash
python acm_switchover.py --decommission \
  --primary-context primary-hub
```

### Restore-Only (Single Hub — No Primary Needed)

When the old hub is gone and you need to restore managed clusters from existing S3 backups:

```bash
# Validate new hub readiness
python acm_switchover.py --restore-only --validate-only \
  --secondary-context new-hub

# Dry-run to preview planned actions
python acm_switchover.py --restore-only --dry-run \
  --secondary-context new-hub

# Execute the restore
python acm_switchover.py --restore-only \
  --secondary-context new-hub
```

> **Note:** Requires ACM installed on the new hub with a BackupStorageLocation pointing to the same S3 bucket as the old hub's backups.

## Command Line Options

| Option | Description |
|--------|-------------|
| `--primary-context` | Kubernetes context for primary hub (required) |
| `--secondary-context` | Kubernetes context for secondary hub (required for switchover) |
| `--method` | Switchover method: `passive` or `full` (required) |
| `--activation-method` | Activation option for passive method: `patch` (default) or `restore` |
| `--min-managed-clusters` | Minimum restored non-local `ManagedCluster` count to enforce after activation; must be non-negative (`0` = informational only) |
| `--old-hub-action` | Action for old hub: `secondary` (**recommended** - enables reverse switchover), `decommission`, or `none` (required) |
| `--validate-only` | Run validation checks only, no changes |
| `--dry-run` | Show planned actions without executing |
| `--state-file` | Path to state file (default: `.state/switchover-<primary>__<secondary>.json`) |
| `--decommission` | Decommission old hub (interactive) |
| `--restore-only` | Restore managed clusters from S3 backups onto a single hub (no primary needed; implies `--method full`) |
| `--manage-auto-import-strategy` | Temporarily set ImportAndSync on destination hub (ACM 2.14+) |
| `--skip-observability-checks` | Skip Observability-related steps even if detected |
| `--disable-observability-on-secondary` | Deprecated compatibility flag; `--old-hub-action secondary` now deletes MCO automatically |
| `--skip-rbac-validation` | Skip RBAC permission validation during pre-flight checks |
| `--argocd-manage` | Pause ACM-touching ArgoCD Applications during switchover (left paused by default) |
| `--argocd-resume-only` | Resume previously paused ArgoCD Applications (standalone; after updating Git for the new hub) |
| `--verbose` | Enable verbose logging |

## How It Works

### Workflow Steps

1. **Pre-flight Validation**
   - Verify backup completion and status
   - Check ACM version matching between hubs
   - Validate OADP operator and DataProtectionApplication
   - **Verify all ClusterDeployments have `spec.preserveOnDelete=true`**
   - **Verify all ManagedClusters are included in latest backup**
   - Check passive sync status (Method 1 only)

2. **Primary Hub Preparation**
   - Pause BackupSchedule (version-aware: ACM 2.11 vs 2.12+)
   - Add disable-auto-import annotations to ManagedClusters
   - Scale down Thanos compactor (if Observability detected)

3. **Secondary Hub Activation**
   - Verify latest passive restore (Method 1) or create full restore (Method 2)
   - Activate managed clusters on secondary hub (patch or create `restore-acm-activate`)
   - Apply `immediate-import` annotations when `autoImportStrategy=ImportOnly` (ACM 2.14+)
   - Poll until restore completes

4. **Post-Activation Verification**
   - Monitor ManagedCluster connection status (5-10 minutes)
   - Restart observatorium-api deployment (if Observability detected)
   - Verify Observability pod health
   - Check metrics collection

5. **Finalization**
   - Enable BackupSchedule on new hub
   - Fix BackupSchedule collision if detected
   - Verify new backups are created
   - Verify backup integrity (status, age, and Velero logs)
   - Handle old hub based on `--old-hub-action`:
     - `secondary`: Set up passive sync restore (**recommended** - enables reverse switchover)
     - `decommission`: Remove ACM components automatically
     - `none`: Leave unchanged for manual handling
   - When `--old-hub-action secondary` is used, delete MultiClusterObservability on the old hub automatically
   - Generate completion report

### Restore-Only Flow

When using `--restore-only` (no primary hub available), the workflow is simplified:

```mermaid
flowchart LR
    A[PREFLIGHT] --> B[ACTIVATION]
    B --> C[POST_ACTIVATION]
    C --> D[FINALIZATION]
    D --> E[COMPLETED]
    style A fill:#e1f5fe
    style E fill:#c8e6c9
```

1. **Preflight** — validates target hub only (ACM version, BSL credentials, namespaces)
2. **Activation** — creates a one-time full Restore from the latest S3 backup
3. **Post-Activation** — waits for ManagedClusters to connect, verifies klusterlet agents
4. **Finalization** — enables BackupSchedule on the new hub

### State Management

The script maintains a JSON state file tracking:

- Completed steps
- Current phase
- Timestamp of each operation
- Detected configuration (ACM version, Observability presence)
- Errors encountered

**Optimized State Persistence:**

The state manager uses intelligent write batching to optimize performance:
- **Non-critical updates** (step completion, configuration) are batched and written only when needed
- **Critical checkpoints** (phase transitions, errors, resets) are immediately persisted
- **Automatic protection**: State is automatically flushed on program termination (SIGTERM/SIGINT/atexit) to prevent data loss

This enables:

- Resume from failure point
- Audit trail of operations
- Context awareness across sessions
- Reduced disk I/O for better performance

## Safety Features

- **ClusterDeployment Protection**: Mandatory check for `preserveOnDelete=true` prevents accidental cluster destruction
- **Backup State Verification**: Ensures no backups in progress during switchover
- **Progressive Validation**: Validates at each step before proceeding
- **Dry-run Mode**: Preview all actions before execution
- **Reverse Switchover**: Return to original hub by swapping contexts (when using `--old-hub-action secondary`)
- **Auto-detection**: No manual configuration of optional components

## Troubleshooting

### Clusters Stuck in "Pending Import"

Wait 10-15 minutes for auto-import. Check import secrets in managed cluster namespaces.

### No Metrics in Grafana After Switchover

Ensure observatorium-api pods were restarted. Wait 10 minutes for metrics collection to resume.

### Restore Stuck in "Running" Phase

Check Velero restore logs: `oc logs -n open-cluster-management-backup deployment/velero`

### Resume from Specific Step

Edit state file manually or use `--reset-state` to start fresh (use with caution).

## Testing

### Run Tests

```bash
# Run all tests with coverage
./run_tests.sh

# Or manually
python -m pytest tests/ -v --cov=. --cov-report=html
```

### E2E Testing

End-to-end tests validate complete switchover cycles on real clusters:

```bash
# Dry-run (no cluster changes)
pytest -m e2e tests/e2e/ --e2e-dry-run

# Real switchover with soak testing controls
pytest -m e2e tests/e2e/ \
  --primary-context mgmt1 \
  --secondary-context mgmt2 \
  --e2e-cycles 5 \
  --e2e-run-hours 2 \
  --e2e-max-failures 2
```

**Note**: Legacy bash E2E scripts (`quick_start_e2e.sh`, `e2e_test_orchestrator.sh`, `phase_monitor.sh`) are deprecated and will be removed in v2.0. See [tests/e2e/MIGRATION.md](tests/e2e/MIGRATION.md) for migration guide.

### Test Coverage

- Unit tests for core utilities and validation modules
- E2E tests with Python orchestrator and monitoring
- Code quality checks (flake8, pylint, black, isort)
- Security scanning (bandit, safety)
- Type checking (mypy)
- CI/CD integration with GitHub Actions

See [docs/development/testing.md](docs/development/testing.md) for detailed testing guide.

### CI/CD Pipelines

**Main Pipeline** (`.github/workflows/ci-cd.yml`):

- Runs on every push and pull request
- Tests across Python 3.10-3.12
- Code quality and security checks
- Syntax validation
- Documentation verification

**Security Pipeline** (`.github/workflows/security.yml`):

- Runs daily and on security-related changes
- Dependency vulnerability scanning
- Static security analysis
- Secrets detection
- Container image scanning
- SBOM generation

## Related Resources

- **[Quick Reference](docs/operations/quickref.md)** - Command cheat sheet and examples
- **[Usage Guide](docs/operations/usage.md)** - Detailed usage guide with scenarios
- **[Installation Guide](docs/getting-started/install.md)** - Installation and deployment
- **[Architecture](docs/development/architecture.md)** - Design and implementation details
- **[Testing Guide](docs/development/testing.md)** - Testing strategy and CI/CD
- **[Contributing](CONTRIBUTING.md)** - Development guidelines
- **[Changelog](CHANGELOG.md)** - Version history and changes
- **[PRD](docs/project/prd.md)** - Product Requirements Document
- **[Project Summary](docs/project/summary.md)** - Comprehensive overview
- **[Deliverables](docs/project/deliverables.md)** - Complete project inventory

## References

- [ACM Backup and Restore Documentation](https://access.redhat.com/documentation/en-us/red_hat_advanced_cluster_management_for_kubernetes/)
- Based on: [ACM Switchover Runbook](docs/ACM_SWITCHOVER_RUNBOOK.md)

## Contributing

Contributions are welcome! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License

MIT License - See [LICENSE](LICENSE) file for details
