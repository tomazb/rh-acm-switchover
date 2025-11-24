# ACM Hub Switchover Automation

Automated, idempotent script for switching over Red Hat Advanced Cluster Management (ACM) from a primary hub to a secondary hub cluster.

## Features

- ✅ **Idempotent execution** - Resume from last successful step
- ✅ **Comprehensive validation** - Pre-flight checks for safety
- ✅ **Data protection** - Verifies `preserveOnDelete` on ClusterDeployments
- ✅ **Auto-detection** - Automatically detects ACM Observability and version
- ✅ **Dry-run mode** - Preview actions without making changes
- ✅ **Validate-only mode** - Run all validations without execution
- ✅ **State tracking** - JSON state file for resume capability
- ✅ **Two methods supported** - Continuous passive restore (Method 1) or one-time full restore (Method 2)

## Documentation

- **[ACM Switchover Runbook](docs/ACM_SWITCHOVER_RUNBOOK.md)** - Detailed operational procedures
- **[Quick Reference](docs/QUICKREF.md)** - Command cheat sheet and common tasks
- **[Detailed Usage Guide](docs/USAGE.md)** - Complete examples and scenarios
- **[Installation Guide](docs/INSTALL.md)** - Detailed installation instructions
- **[Architecture](docs/ARCHITECTURE.md)** - Design and implementation details
- **[Testing Guide](docs/TESTING.md)** - How to run tests and CI/CD
- **[Contributing](docs/CONTRIBUTING.md)** - Development guidelines

See [docs/README.md](docs/README.md) for complete documentation index.

## Automation Scripts

Automated validation scripts to ensure safe and successful switchovers:

- **[Pre-flight Validation](scripts/preflight-check.sh)** - Verify all prerequisites before switchover
- **[Post-flight Validation](scripts/postflight-check.sh)** - Confirm switchover completed successfully

See [scripts/README.md](scripts/README.md) for detailed usage and workflow diagrams.

## Prerequisites

- Python 3.8+
- `kubectl` or `oc` CLI configured for both primary and secondary hubs
- ACM Backup configured on both hubs
- OADP operator installed on both hubs
- Network access to both Kubernetes clusters

## Installation

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
  --method passive

# Actual execution
python acm_switchover.py \
  --primary-context primary-hub \
  --secondary-context secondary-hub \
  --method passive
```

### One-Time Full Restore (Method 2)

```bash
python acm_switchover.py \
  --primary-context primary-hub \
  --secondary-context secondary-hub \
  --method full
```

### Resume from Previous Run

```bash
# Script automatically resumes from last successful step
python acm_switchover.py \
  --primary-context primary-hub \
  --secondary-context secondary-hub \
  --method passive \
  --state-file .state/switchover-state.json
```

### Rollback

```bash
python acm_switchover.py --rollback \
  --primary-context primary-hub \
  --secondary-context secondary-hub \
  --state-file .state/switchover-state.json
```

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
| `--method` | Switchover method: `passive` (default) or `full` |
| `--validate-only` | Run validation checks only, no changes |
| `--dry-run` | Show planned actions without executing |
| `--state-file` | Path to state file (default: `.state/switchover-state.json`) |
| `--rollback` | Rollback to primary hub |
| `--decommission` | Decommission old hub (interactive) |
| `--skip-observability-checks` | Skip Observability-related steps even if detected |
| `--verbose` | Enable verbose logging |

## How It Works

### Workflow Steps

1. **Pre-flight Validation**
   - Verify backup completion and status
   - Check ACM version matching between hubs
   - Validate OADP operator and DataProtectionApplication
   - **Verify all ClusterDeployments have `spec.preserveOnDelete=true`**
   - Check passive sync status (Method 1 only)

2. **Primary Hub Preparation**
   - Pause BackupSchedule (version-aware: ACM 2.11 vs 2.12+)
   - Add disable-auto-import annotations to ManagedClusters
   - Scale down Thanos compactor (if Observability detected)

3. **Secondary Hub Activation**
   - Verify latest passive restore (Method 1) or create full restore (Method 2)
   - Activate managed clusters on secondary hub
   - Poll until restore completes

4. **Post-Activation Verification**
   - Monitor ManagedCluster connection status (5-10 minutes)
   - Restart observatorium-api deployment (if Observability detected)
   - Verify Observability pod health
   - Check metrics collection

5. **Finalization**
   - Enable BackupSchedule on new hub
   - Verify new backups are created
   - Generate completion report

### State Management

The script maintains a JSON state file tracking:

- Completed steps
- Current phase
- Timestamp of each operation
- Detected configuration (ACM version, Observability presence)
- Errors encountered

This enables:

- Resume from failure point
- Audit trail of operations
- Rollback with context awareness

## Safety Features

- **ClusterDeployment Protection**: Mandatory check for `preserveOnDelete=true` prevents accidental cluster destruction
- **Backup State Verification**: Ensures no backups in progress during switchover
- **Progressive Validation**: Validates at each step before proceeding
- **Dry-run Mode**: Preview all actions before execution
- **Rollback Capability**: Revert changes if issues occur
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

### Test Coverage

- Unit tests for core utilities and validation modules
- Code quality checks (flake8, pylint, black, isort)
- Security scanning (bandit, safety)
- Type checking (mypy)
- CI/CD integration with GitHub Actions

See [TESTING.md](docs/TESTING.md) for detailed testing guide.

### CI/CD Pipelines

**Main Pipeline** (`.github/workflows/ci-cd.yml`):

- Runs on every push and pull request
- Tests across Python 3.8-3.12
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

- **[Quick Reference](docs/QUICKREF.md)** - Command cheat sheet and examples
- **[Usage Guide](docs/USAGE.md)** - Detailed usage guide with scenarios
- **[Installation Guide](docs/INSTALL.md)** - Installation and deployment
- **[Architecture](docs/ARCHITECTURE.md)** - Design and implementation details
- **[Testing Guide](docs/TESTING.md)** - Testing strategy and CI/CD
- **[Contributing](docs/CONTRIBUTING.md)** - Development guidelines
- **[Changelog](docs/CHANGELOG.md)** - Version history and changes
- **[PRD](docs/PRD.md)** - Product Requirements Document
- **[Project Summary](docs/PROJECT_SUMMARY.md)** - Comprehensive overview
- **[Deliverables](docs/DELIVERABLES.md)** - Complete project inventory

## References

- [ACM Backup and Restore Documentation](https://access.redhat.com/documentation/en-us/red_hat_advanced_cluster_management_for_kubernetes/)
- Based on: `docs/ACM_switchover_runbook_Complete_Nov12.txt`

## Contributing

Contributions are welcome! Please see [CONTRIBUTING.md](docs/CONTRIBUTING.md) for guidelines.

## License

MIT License - See [LICENSE](LICENSE) file for details
