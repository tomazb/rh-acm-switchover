# ACM Switchover Automation - Project Summary

## Overview

A comprehensive Python-based automation tool for executing Red Hat Advanced Cluster Management (ACM) hub switchover from a primary cluster to a secondary cluster. The script implements the complete workflow from the ACM switchover runbook with enhanced safety, validation, and idempotency features.

## Key Design Decisions

### Why Python?

**Selected over Bash and Go for:**
- Native Kubernetes Python client library
- Better structured error handling and state management
- Easier to maintain than Bash for complex logic
- Simpler than Go while maintaining flexibility
- Rich ecosystem for YAML, JSON, and logging

### Idempotency via State Management

**Resume capability implemented through:**
- JSON state file tracking completed steps
- Phase-based workflow with checkpoint tracking
- Conditional execution: check before run
- Safe re-execution without duplicating work

**Example flow:**
```
Run 1: Complete steps 1-5, interrupted
Run 2: Skip steps 1-5, resume from step 6
Run 3: All steps completed, no operations
```

### Auto-Detection Philosophy

**No manual configuration required:**
- ACM version detected from MultiClusterHub
- Observability auto-detected by namespace existence
- Version-specific logic handled automatically
- Optional components gracefully skipped if absent

### Safety-First Approach

**Multiple protection layers:**
1. **Validation** - Critical checks before any changes
2. **Dry-run** - Preview mode for verification
3. **Validate-only** - Check without execution
4. **preserveOnDelete** - Mandatory check prevents cluster destruction
5. **Reverse Switchover** - Return to original hub by swapping contexts
6. **Interactive decommission** - Confirmation for destructive ops

## Project Structure

```
rh-acm-switchover/
├── acm_switchover.py         # Main orchestrator (318 lines)
├── quick-start.sh            # Interactive wizard
│
├── lib/                      # Core utilities
│   ├── utils.py             # State management, logging (203 lines)
│   └── kube_client.py       # K8s API wrapper (358 lines)
│
├── modules/                  # Workflow modules
│   ├── preflight.py         # Validation (366 lines)
│   ├── primary_prep.py      # Primary preparation (143 lines)
│   ├── activation.py        # Secondary activation (169 lines)
│   ├── post_activation.py   # Post-activation verification (218 lines)
│   ├── finalization.py      # Finalization & old hub handling (237 lines)
│   └── decommission.py      # Decommission old hub (144 lines)
│
└── docs/
    ├── README.md                    # Documentation index
    ├── ACM_SWITCHOVER_RUNBOOK.md    # Operator runbook
    ├── getting-started/             # Installation and container usage
    │   ├── install.md
    │   └── container.md
    ├── operations/                  # Operator guides
    │   ├── quickref.md
    │   └── usage.md
    └── ...
```

**Total Python code: ~2,156 lines**

## Implemented Features

### ✅ Core Functionality

- [x] Pre-flight validation with 15+ checks
- [x] Primary hub preparation (pause, annotations, scale-down)
- [x] Secondary hub activation (passive sync & full restore)
- [x] Post-activation verification (clusters, observability)
- [x] Finalization (enable backups, verify)
- [x] Old hub handling (secondary/decommission/none)
- [x] Interactive decommission workflow

### ✅ Safety & Validation

- [x] ClusterDeployment preserveOnDelete verification (CRITICAL)
- [x] Backup status and completion checks
- [x] ACM version matching validation
- [x] OADP operator and DPA verification
- [x] Observability auto-detection
- [x] Passive sync status verification

### ✅ Operational Features

- [x] Idempotent execution with state tracking
- [x] Resume from interruption
- [x] Dry-run mode (preview without execution)
- [x] Validate-only mode (checks without changes)
- [x] Verbose logging for debugging
- [x] Custom state file support
- [x] State reset capability

### ✅ Version Support

- [x] ACM 2.11 support (delete BackupSchedule)
- [x] ACM 2.12+ support (pause BackupSchedule)
- [x] Auto-detection of ACM version
- [x] Version-aware logic throughout

### ✅ Documentation

- [x] README with overview
- [x] Quick reference card (docs/operations/quickref.md)
- [x] Detailed usage guide (docs/operations/usage.md)
- [x] Architecture documentation (docs/development/architecture.md)
- [x] Interactive quick-start script
- [x] Inline code comments

## Module Breakdown

### PreflightValidator (modules/preflight.py)

**Validates:**
- Namespace existence on both hubs
- ACM version detection and matching
- OADP operator installation
- DataProtectionApplication configuration
- Backup completion status
- **ClusterDeployment preserveOnDelete** (prevents cluster destruction)
- Passive sync status (Method 1)
- Observability detection

**Output:**
```
Validation Summary: 15/15 checks passed
✓ All critical validations passed!
```

### PrimaryPreparation (modules/primary_prep.py)

**Executes:**
1. Pause BackupSchedule (ACM 2.12+) or delete (ACM 2.11)
2. Add `disable-auto-import` annotations to ManagedClusters
3. Scale down Thanos compactor StatefulSet (if Observability)

**Idempotent:** Checks current state before each action

### SecondaryActivation (modules/activation.py)

**Method 1 (Passive Sync):**
1. Verify passive sync restore status
2. Patch restore: `veleroManagedClustersBackupName: latest`
3. Poll until Phase="Finished"

**Method 2 (Full Restore):**
1. Create Restore with all backup names
2. Poll until Phase="Finished"

**Timeout:** 30 minutes with 30-second polling

### PostActivationVerification (modules/post_activation.py)

**Verifies:**
1. ManagedClusters connected (Available=True, Joined=True)
2. Restart observatorium-api deployment (ACM 2.12 issue workaround)
3. Observability pods running and ready
4. Metrics collection resuming

**Timeouts:**
- Cluster connection: 10 minutes
- Pod readiness: 5 minutes

### Finalization (modules/finalization.py)

**Finalizes:**
1. Enable BackupSchedule on secondary hub
2. Verify new backups being created
3. Handle old hub based on `--old-hub-action`:
   - `secondary`: Set up passive sync restore for reverse switchover
   - `decommission`: Remove ACM components automatically
   - `none`: Leave unchanged for manual handling
4. Generate completion report

### Decommission (modules/decommission.py)

**Interactive steps:**
1. Delete MultiClusterObservability
2. Verify Observability pods terminated
3. Delete ManagedClusters (excluding local-cluster)
4. Delete MultiClusterHub
5. Verify ACM pods removed

**Safety:** Confirmation prompts at each destructive step

## State Management

**State file structure:**
```json
{
  "version": "1.0",
  "created_at": "2025-11-18T10:00:00Z",
  "current_phase": "post_activation_verification",
  "completed_steps": [
    {"name": "pause_backup_schedule", "timestamp": "2025-11-18T10:15:00Z"},
    {"name": "disable_auto_import", "timestamp": "2025-11-18T10:16:30Z"}
  ],
  "config": {
    "primary_version": "2.12.0",
    "secondary_version": "2.12.0",
    "has_observability": true,
    "saved_backup_schedule": {...}
  },
  "errors": [],
  "last_updated": "2025-11-18T10:30:00Z"
}
```

**Benefits:**
- Resume from exact point of interruption
- Audit trail of operations
- Configuration persistence
- Error tracking

## Usage Patterns

### Basic Workflow
```bash
# 1. Validate
python acm_switchover.py --validate-only \
  --primary-context primary --secondary-context secondary

# 2. Dry-run
python acm_switchover.py --dry-run \
  --primary-context primary --secondary-context secondary

# 3. Execute
python acm_switchover.py \
  --primary-context primary --secondary-context secondary --verbose
```

### Resume After Interruption
```bash
# Same command - automatically resumes
python acm_switchover.py \
  --primary-context primary --secondary-context secondary --verbose
```

### Reverse Switchover

Return to original hub by swapping contexts:

```bash
python acm_switchover.py \
  --primary-context secondary \
  --secondary-context primary \
  --old-hub-action secondary \
  --method passive
```

> **Note:** Requires original switchover used `--old-hub-action secondary` to enable passive sync.

### Decommission
```bash
python acm_switchover.py --decommission \
  --primary-context old-hub
```

## Error Handling

**Strategy:**
1. Validate before executing
2. Catch exceptions at module level
3. Record errors in state file
4. Log detailed context
5. Allow resume from last successful step

**Example:**
```python
try:
    if not prep.prepare():
        state.set_phase(Phase.FAILED)
        return False
except Exception as e:
    logger.error(f"Preparation failed: {e}")
    state.add_error(str(e), "primary_preparation")
    return False
```

## Testing Strategy

**Levels:**
1. **Validation** - `--validate-only` before any execution
2. **Dry-run** - `--dry-run` to preview actions
3. **Non-production** - Test full workflow in test environment
4. **Reverse switchover testing** - Practice returning to original hub
5. **Production** - Execute with `--verbose` and monitoring

## Performance

**Typical execution timeline:**
- Pre-flight validation: 2-3 minutes
- Primary preparation: 1-2 minutes
- Activation: 5-15 minutes (restore polling)
- Post-activation: 10-15 minutes (cluster connections)
- Finalization: 5-10 minutes (backup verification)

**Total: 30-45 minutes**

## Dependencies

**Python packages:**
- `kubernetes>=28.0.0` - K8s API client
- `PyYAML>=6.0` - YAML parsing
- `rich>=13.0.0` - Rich text formatting (future use)

**System requirements:**
- Python 3.9+
- `kubectl` or `oc` CLI
- Kubernetes contexts configured
- RBAC permissions for ACM resources

## Future Enhancements

**Potential additions:**
1. Parallel validation checks
2. Progress bar with rich library
3. Email/Slack notifications
4. Metrics collection and reporting
5. Multi-hub batch switchover
6. Web UI for monitoring
7. Automated post-switchover testing
8. Prometheus metrics export

## Success Criteria

**Project achieves:**
- ✅ Complete runbook automation
- ✅ Idempotent, resumable execution
- ✅ Data protection through validation
- ✅ Auto-detection of environment
- ✅ Dry-run and validate-only modes
- ✅ Comprehensive documentation
- ✅ Reverse switchover capability
- ✅ Interactive decommission

## Lessons Learned

**Design choices that worked well:**
1. State-based idempotency
2. Module separation by workflow phase
3. KubeClient abstraction for dry-run
4. Auto-detection over configuration
5. Validation before execution

**Areas for improvement:**
1. Could add parallel validation
2. More granular error types
3. Metrics/telemetry collection
4. Automated testing suite

## Conclusion

The ACM Switchover Automation tool successfully implements a production-ready solution for ACM hub migration. The Python implementation provides the right balance of simplicity and flexibility, with robust safety features including comprehensive validation, dry-run capabilities, and state-based idempotency.

The modular architecture supports both continuous passive restore and one-time full restore methods, with automatic detection of ACM version and optional components like Observability. The script gracefully handles edge cases and provides clear guidance for troubleshooting.

With complete documentation including quick reference, detailed usage examples, and architecture details, the tool is ready for production use while maintaining extensibility for future enhancements.

**Project Status: Complete and Ready for Use** ✅
