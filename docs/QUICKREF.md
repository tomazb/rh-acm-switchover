# ACM Switchover - Quick Reference

## Installation

```bash

# Clone repository
git clone https://github.com/tomazb/rh-acm-switchover.git
cd rh-acm-switchover

# Install dependencies
pip install -r requirements.txt

# OR use interactive quick-start
./quick-start.sh

```

## Common Commands

### Validation & Dry-Run

```bash

# Validate everything (recommended first step)
python acm_switchover.py \
  --validate-only \
  --primary-context <primary> \
  --secondary-context <secondary>

# Dry-run to preview actions
python acm_switchover.py \
  --dry-run \
  --primary-context <primary> \
  --secondary-context <secondary> \
  --old-hub-action secondary \
  --method passive

```

### Switchover Execution

```bash

# Method 1: Passive sync (continuous restore)
python acm_switchover.py \
  --primary-context <primary> \
  --secondary-context <secondary> \
  --method passive \
  --old-hub-action secondary \
  --verbose

# Method 2: Full restore (one-time)
python acm_switchover.py \
  --primary-context <primary> \
  --secondary-context <secondary> \
  --method full \
  --old-hub-action decommission \
  --verbose

```

### Resume & Reverse Switchover

```bash

# Resume from interruption (same command as execution)
python acm_switchover.py \
  --primary-context <primary> \
  --secondary-context <secondary> \
  --old-hub-action secondary \
  --method passive

# Reverse switchover (return to original hub)
# Swap --primary-context and --secondary-context values
python acm_switchover.py \
  --primary-context <secondary> \
  --secondary-context <primary> \
  --old-hub-action secondary \
  --method passive

```

### Decommission

```bash

# Interactive decommission
python acm_switchover.py \
  --decommission \
  --primary-context <old-hub>

# Non-interactive (automated)
python acm_switchover.py \
  --decommission \
  --primary-context <old-hub> \
  --non-interactive

```

### State Management

```bash

# View current state
cat .state/switchover-<primary>__<secondary>.json | python -m json.tool

# Use custom state file
python acm_switchover.py \
  --state-file .state/custom-state.json \
  --primary-context <primary> \
  --secondary-context <secondary>

# Reset state (start fresh)
python acm_switchover.py \
  --reset-state \
  --validate-only \
  --primary-context <primary> \
  --secondary-context <secondary>

```

## Workflow Cheat Sheet

```text
1. VALIDATE    →  --validate-only          (2-3 min)
2. DRY-RUN     →  --dry-run                (2-3 min)
3. EXECUTE     →  (no flags)               (30-45 min)
4. VERIFY      →  oc get managedclusters   (manual check)
5. DECOMMISSION → --decommission           (15-20 min)
```

## Pre-Flight Checks (Manual)

```bash
# List contexts
oc config get-contexts

# Check ACM version
oc get multiclusterhub -n open-cluster-management \
  -o jsonpath='{.items[0].status.currentVersion}'

# Check backups
oc get backup -n open-cluster-management-backup

# Check ClusterDeployment preservation
oc get clusterdeployment --all-namespaces \
  -o custom-columns=NAME:.metadata.name,NAMESPACE:.metadata.namespace,PRESERVE:.spec.preserveOnDelete

# Fix missing preserveOnDelete
oc patch clusterdeployment <name> -n <namespace> \
  --type='merge' -p '{"spec":{"preserveOnDelete":true}}'

# Check auto-import strategy (ACM 2.14+)
oc get configmap import-controller -n multicluster-engine \
  -o jsonpath='{.data.autoImportStrategy}'
# If not found or returns empty, default (ImportOnly) is in use
```

## Post-Switchover Verification

```bash
# Check ManagedClusters on new hub
oc --context <secondary> get managedclusters

# Verify all Available=True
oc --context <secondary> get managedclusters \
  -o custom-columns=NAME:.metadata.name,AVAILABLE:.status.conditions[?(@.type==\"ManagedClusterConditionAvailable\")].status

# Check BackupSchedule
oc --context <secondary> get backupschedule -n open-cluster-management-backup

# Check new backups
oc --context <secondary> get backup -n open-cluster-management-backup \
  --sort-by=.metadata.creationTimestamp

# Check Observability pods (if applicable)
oc --context <secondary> get pods -n open-cluster-management-observability
```

## Troubleshooting Commands

```bash
# Check Velero logs
oc logs -n open-cluster-management-backup deployment/velero --tail=100

# Check restore status
oc get restore -n open-cluster-management-backup
oc describe restore <restore-name> -n open-cluster-management-backup

# Check ManagedCluster events
oc describe managedcluster <cluster-name>

# Check import secrets
oc get secret -n <cluster-namespace> | grep import

# Restart observatorium-api manually
oc rollout restart deployment/observability-observatorium-api \
  -n open-cluster-management-observability
```

## Options Reference

| Option | Description |
|--------|-------------|
| `--primary-context` | Kubernetes context for primary hub (required) |
| `--secondary-context` | Kubernetes context for secondary hub (required for switchover) |
| `--method {passive,full}` | Switchover method: `passive` or `full` (required) |
| `--old-hub-action {secondary,decommission,none}` | Action for old hub after switchover (required) |
| `--validate-only` | Run validation checks only, no changes |
| `--dry-run` | Show planned actions without executing |
| `--decommission` | Decommission old hub (interactive) |
| `--state-file PATH` | Path to state file (default: .state/switchover-<primary>__<secondary>.json) |
| `--reset-state` | Reset state file and start fresh |
| `--skip-observability-checks` | Skip Observability steps even if detected |
| `--non-interactive` | Non-interactive mode (only valid with `--decommission`) |
| `--verbose, -v` | Enable verbose logging |

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Failure (validation failed, operation error) |
| 130 | Interrupted by user (Ctrl+C) |

## Important Files

| File | Purpose |
|------|---------|
| `acm_switchover.py` | Main script |
| `quick-start.sh` | Interactive setup wizard |
| `.state/switchover-<primary>__<secondary>.json` | State tracking (auto-created) |
| `requirements.txt` | Python dependencies |
| `README.md` | Project overview |
| `USAGE.md` | Detailed examples |
| `ARCHITECTURE.md` | Design documentation |

## Safety Checklist

- [ ] Validated with `--validate-only`
- [ ] Previewed with `--dry-run`
- [ ] All ClusterDeployments have `preserveOnDelete=true`
- [ ] All ManagedClusters included in latest backup
- [ ] Latest backup completed successfully
- [ ] ACM versions match between hubs
- [ ] (ACM 2.14+) Verified `autoImportStrategy` configuration
- [ ] Decided on `--old-hub-action` (secondary/decommission/none)
- [ ] Stakeholders informed of maintenance window
- [ ] Reverse switchover procedure tested in non-production
- [ ] State file location noted for resume

## Support

For issues or questions:

- Review `USAGE.md` for detailed examples
- Check `ARCHITECTURE.md` for design details
- Inspect `.state/switchover-<primary>__<secondary>.json` for progress
- Enable `--verbose` for detailed logging

## Quick Start (New Users)

```bash

# Interactive setup and execution
./quick-start.sh
```

This guides you through:

1. Dependency installation
2. Context selection
3. Method selection
4. Validation
5. Dry-run
6. Execution (optional)
