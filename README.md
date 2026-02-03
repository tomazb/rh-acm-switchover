# ACM Hub Switchover Automation

**Version 1.5.5** (2026-02-03)

Automated, idempotent script for switching over Red Hat Advanced Cluster Management (ACM) from a primary hub to a secondary hub cluster.

## Features

- ✅ **Idempotent execution** - Resume from last successful step
- ✅ **Comprehensive validation** - Pre-flight and post-flight checks for safety
- ✅ **RBAC enforcement** - Least privilege access control with validation
- ✅ **Data protection** - Verifies `preserveOnDelete` on ClusterDeployments
- ✅ **Auto-detection** - Automatically detects ACM Observability and version
- ✅ **Dry-run mode** - Preview actions without making changes
- ✅ **Validate-only mode** - Run all validations without execution
- ✅ **State tracking** - JSON state file for resume capability
- ✅ **Two methods supported** - Continuous passive restore (Method 1) or one-time full restore (Method 2)
- ✅ **Multi-deployment support** - RBAC via Kustomize, Helm, or ACM Policies

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

- Python 3.9+
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

> **Note:** Requires original switchover used `--old-hub-action secondary` to enable passive sync.

### Decommission Old Hub

```bash
python acm_switchover.py --decommission \
  --primary-context primary-hub
```

## Command Line Options

| Option | Description |
|--------|-------------|
| `--primary-context` | Kubernetes context for primary hub (required) |
| `--secondary-context` | Kubernetes context for secondary hub (required for switchover) |
| `--method` | Switchover method: `passive` or `full` (required) |
| `--activation-method` | Activation option for passive method: `patch` (default) or `restore` |
| `--old-hub-action` | Action for old hub: `secondary` (**recommended** - enables reverse switchover), `decommission`, or `none` (required) |
| `--validate-only` | Run validation checks only, no changes |
| `--dry-run` | Show planned actions without executing |
| `--state-file` | Path to state file (default: `.state/switchover-<primary>__<secondary>.json`) |
| `--decommission` | Decommission old hub (interactive) |
| `--manage-auto-import-strategy` | Temporarily set ImportAndSync on destination hub (ACM 2.14+) |
| `--skip-observability-checks` | Skip Observability-related steps even if detected |
| `--disable-observability-on-secondary` | Delete MCO on old hub when keeping it as secondary |
| `--skip-rbac-validation` | Skip RBAC permission validation during pre-flight checks |
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
   - Optional: delete MultiClusterObservability on old hub when keeping it as secondary
   - Generate completion report

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
- Tests across Python 3.9-3.12
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
