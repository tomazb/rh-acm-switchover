# ACM Switchover - Architecture & Design

**Version**: 1.4.10  
**Last Updated**: January 10, 2026

## Project Structure

```
rh-acm-switchover/
├── acm_switchover.py          # Main orchestrator script
├── check_rbac.py              # RBAC permission validation tool
├── show_state.py              # State file viewer utility
├── quick-start.sh             # Interactive setup wizard
├── run_tests.sh               # Test execution wrapper
├── requirements.txt           # Python dependencies
├── requirements-dev.txt       # Development/testing dependencies
├── setup.cfg                  # Tool configuration (flake8, pytest, etc.)
├── README.md                  # Project overview
├── LICENSE                    # MIT License
├── SECURITY.md                # Security policy
├── .gitignore                 # Git ignore patterns
│
├── container-bootstrap/       # Container build resources
│   ├── Containerfile          # Multi-stage container build definition
│   └── get-pip.py             # Python package installer bootstrapper
│
├── lib/                       # Core utilities
│   ├── __init__.py
│   ├── constants.py           # Shared constants
│   ├── exceptions.py          # Custom exception hierarchy
│   ├── kube_client.py         # Kubernetes API wrapper
│   ├── rbac_validator.py      # RBAC permission validation
│   ├── utils.py               # State management, logging, helpers
│   ├── validation.py          # Input validation and sanitization
│   └── waiter.py              # Resource polling and waiting logic
│
├── modules/                   # Switchover modules
│   ├── __init__.py
│   ├── preflight/             # Modular pre-flight validation package
│   │   ├── __init__.py
│   │   ├── base_validator.py      # BaseValidator class for all validators
│   │   ├── reporter.py            # ValidationReporter for result collection
│   │   ├── backup_validators.py   # Backup and restore validations
│   │   ├── cluster_validators.py  # Cluster-related validations
│   │   ├── namespace_validators.py # Namespace and resource validations
│   │   └── version_validators.py  # Version and compatibility validations
│   ├── preflight_coordinator.py   # PreflightValidator orchestrator
│   ├── preflight_validators.py    # Backward-compat shim (deprecated)
│   ├── primary_prep.py        # Primary hub preparation
│   ├── activation.py          # Secondary hub activation
│   ├── post_activation.py     # Post-activation verification
│   ├── finalization.py        # Finalization & rollback
│   ├── decommission.py        # Old hub decommission
│   └── backup_schedule.py     # Backup schedule management
│
├── scripts/                   # Shell helper scripts
│   ├── constants.sh           # Shared shell variables
│   ├── lib-common.sh          # Shared helper functions
│   ├── preflight-check.sh     # Standalone pre-flight check
│   ├── postflight-check.sh    # Standalone post-flight check
│   ├── discover-hub.sh        # Auto-discover ACM hubs
│   ├── setup-rbac.sh          # RBAC bootstrap and kubeconfig generation
│   ├── generate-sa-kubeconfig.sh     # Service account kubeconfig generator
│   └── generate-merged-kubeconfig.sh # Multi-cluster kubeconfig merger
│
├── deploy/                    # Deployment manifests
│   ├── rbac/                  # RBAC resources (SA, roles, bindings)
│   ├── kustomize/             # Kustomize overlays
│   ├── helm/                  # Helm chart
│   └── acm-policies/          # ACM governance policies
│
├── tests/                     # Unit and integration tests
│   ├── __init__.py
│   ├── test_main.py           # Tests for main orchestrator
│   ├── test_utils.py          # Tests for lib/utils.py
│   ├── test_kube_client.py    # Tests for lib/kube_client.py
│   ├── test_preflight.py      # Tests for modules/preflight.py
│   ├── test_scripts.py        # Tests for shell scripts
│   └── ... (comprehensive test suite for all modules)
│
├── .github/                   # CI/CD configuration
│   ├── workflows/
│   │   ├── ci-cd.yml          # Main CI/CD pipeline
│   │   └── security.yml       # Security scanning workflow
│   └── ...
│
└── docs/                      # Documentation
    ├── README.md
    ├── ACM_SWITCHOVER_RUNBOOK.md
    ├── getting-started/install.md
    ├── getting-started/container.md
    ├── operations/quickref.md
    ├── operations/usage.md
    ├── deployment/rbac-deployment.md
    ├── development/architecture.md
    ├── project/prd.md
    └── ...
```

**Total Lines of Code**: ~2,156 lines (excluding documentation and tests)

## Design Principles

### 1. Idempotency

Every operation is designed to be safely re-runnable:

- **State Tracking**: JSON state file tracks completed steps
- **Step Checking**: Each step checks if already completed before executing
- **Resume Capability**: Can resume from last successful step after interruption
- **Safe Re-runs**: Patches and updates are conditional on current resource state

**Implementation:**
```python
if not self.state.is_step_completed("pause_backup_schedule"):
    self._pause_backup_schedule()
    self.state.mark_step_completed("pause_backup_schedule")
else:
    logger.info("Step already completed: pause_backup_schedule")
```

### 2. Comprehensive Validation

Pre-flight checks ensure safety before any changes:

- **Required Resources**: Verify namespaces, operators, configurations exist
- **Version Matching**: Ensure ACM versions match between hubs
- **Backup Status**: Check for completed, recent backups
- **Data Protection**: CRITICAL check for `preserveOnDelete=true` on ClusterDeployments
- **Component Detection**: Auto-detect optional components (Observability)

**Validation Categories:**
- ✓ Critical validations (must pass)
- ⚠ Warning validations (logged but not blocking)

### 3. Auto-Detection

No manual configuration of environment-specific details:

- **ACM Version**: Detected from MultiClusterHub resource
- **Observability**: Detected by namespace existence
- **Method Selection**: User chooses, but script validates compatibility
- **Version-Specific Logic**: ACM 2.11 vs 2.12+ handled automatically

**Example:**
```python
if is_acm_version_ge(self.acm_version, "2.12.0"):
    # Use spec.paused for ACM 2.12+
    patch = {"spec": {"paused": True}}
else:
    # ACM 2.11: Delete and save BackupSchedule
    self.state.set_config("saved_backup_schedule", bs)
    self.client.delete_custom_resource(...)
```

### 4. Data Protection

Multiple layers to prevent accidental data loss:

- **preserveOnDelete Check**: Mandatory validation before switchover
- **Dry-Run Mode**: Preview all actions without execution
- **Validate-Only Mode**: Run all checks without any changes
- **Rollback Capability**: Revert to primary hub if issues occur
- **Interactive Decommission**: Confirmation prompts for destructive operations

### 5. Graceful Degradation

Handle optional components gracefully:

- **Observability**: If not present, skip related steps automatically
- **Hive ClusterDeployments**: If not present, skip preservation checks
- **Missing Resources**: Log warnings for non-critical missing resources
- **API Errors**: Distinguish between 404 (expected) and real errors

## Module Architecture

### Constants (`lib/constants.py`)

Centralized constants for maintainability and consistency:

**Namespaces:**
- `BACKUP_NAMESPACE`: `open-cluster-management-backup`
- `OBSERVABILITY_NAMESPACE`: `open-cluster-management-observability`
- `ACM_NAMESPACE`: `open-cluster-management`

**ACM Resource Names:**
- `RESTORE_PASSIVE_SYNC_NAME`: `restore-acm-passive-sync`
- `RESTORE_FULL_NAME`: `restore-acm-full`
- `BACKUP_SCHEDULE_DEFAULT_NAME`: `acm-hub-backup`

**ACM Spec Fields:**
- `SPEC_VELERO_MANAGED_CLUSTERS_BACKUP_NAME`: `veleroManagedClustersBackupName`
- `SPEC_SYNC_RESTORE_WITH_NEW_BACKUPS`: `syncRestoreWithNewBackups`
- `VELERO_BACKUP_LATEST`: `latest`
- `VELERO_BACKUP_SKIP`: `skip`

**Timeouts:**
- `RESTORE_WAIT_TIMEOUT`: 1800s (30 min)
- `CLUSTER_VERIFY_TIMEOUT`: 600s (10 min)
- `DECOMMISSION_POD_TIMEOUT`: 1200s (20 min)

**Resource Limits:**
- `MAX_KUBECONFIG_SIZE`: 10MB default (configurable via `ACM_KUBECONFIG_MAX_SIZE` environment variable). Prevents memory exhaustion when loading large kubeconfig files. Set to 0 or negative to disable size checking.

### State Manager (`lib/utils.py`)

**Responsibilities:**
- Load/save state to JSON file with optimized write batching
- Track current phase and completed steps
- Store configuration detected during execution
- Record errors for debugging
- **Logging**: Configure structured JSON logging or human-readable text logging
- **Dry-run decorator**: `dry_run_skip` decorator for consistent dry-run handling
- **State persistence**: Automatic state flushing on critical checkpoints and program termination

**State Persistence Strategy:**

The StateManager uses a two-tier write strategy to optimize performance while ensuring data safety:

- **`save_state()`**: Writes state to disk only if there are pending changes (dirty state). Used for non-critical updates like marking steps completed or setting configuration values.

- **`flush_state()`**: Forces immediate write to disk regardless of dirty state. Used for critical checkpoints:
  - Phase transitions (`set_phase()`)
  - Error recording (`add_error()`)
  - State resets (`reset()`)
  - Context changes (`ensure_contexts()`)

**Automatic State Protection:**

The StateManager includes multiple safety mechanisms to prevent state loss:

- **Signal handlers**: Registered for `SIGTERM` and `SIGINT` to flush dirty state before process termination
- **Atexit handlers**: Flush pending state changes and clean up temporary files on normal program exit
- **Dirty state tracking**: Tracks whether state has pending writes to avoid unnecessary disk I/O
- **Atomic writes**: Uses temporary files and atomic rename operations to prevent corruption
- **File locking**: Uses `fcntl` locks (when available) to prevent concurrent write conflicts

**Dry-Run Decorator:**

```python
from lib.utils import dry_run_skip

class MyModule:
    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run

    @dry_run_skip(message="Would perform action", return_value=True)
    def perform_action(self):
        # This code only runs when dry_run=False
        return do_something()
```

**State Structure:**
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
    "has_observability": true
  },
  "errors": [],
  "last_updated": "2025-11-18T10:30:00Z"
}
```

### Kubernetes Client (`lib/kube_client.py`)

**Responsibilities:**
- Abstract Kubernetes API interactions
- Provide high-level methods for ACM resources
- Support dry-run mode (log actions without execution)
- Handle custom resources (CRDs)
- Manage deployments, statefulsets, pods
- **Reliability**: Automatic retries with exponential backoff for transient errors (5xx, 429)
- **Timeouts**: Enforced client-side timeouts (default 30s) to prevent hanging operations

**Key Methods:**
- `get_custom_resource()`: Retrieve ACM custom resources
- `patch_custom_resource()`: Update resources (dry-run aware)
- `get_deployment()`: Get deployment by name and namespace
- `get_statefulset()`: Get statefulset by name and namespace
- `scale_deployment()`: Scale deployments
- `scale_statefulset()`: Scale statefulsets
- `wait_for_pods_ready()`: Poll until pods are ready

### Pre-Flight Validator (`modules/preflight.py`)

**Validations:**
1. Namespace existence (both hubs)
2. ACM version detection and matching
3. OADP operator presence and health
4. DataProtectionApplication configuration
5. Backup status and completion
6. **ClusterDeployment preserveOnDelete** (CRITICAL)
7. Passive sync status (Method 1 only)
8. Observability detection (optional)

**Output:**
```
✓ Namespace open-cluster-management (primary): exists
✓ Namespace open-cluster-management (secondary): exists
✓ ACM version (primary): detected: 2.12.0
✓ ACM version (secondary): detected: 2.12.0
✓ ACM version matching: both hubs running 2.12.0
✓ OADP operator (primary): installed, 1 Velero pod(s) found
✓ ClusterDeployment preserveOnDelete: all 5 ClusterDeployments have preserveOnDelete=true
✓ Passive sync restore: passive sync enabled and running
```

### Primary Preparation (`modules/primary_prep.py`)

**Steps:**
1. Pause BackupSchedule (version-aware)
2. Add disable-auto-import annotations to ManagedClusters
3. Scale down Thanos compactor (if Observability)

**Version Handling:**
- ACM 2.12+: Patch `spec.paused=true`
- ACM 2.11: Delete BackupSchedule, save to state

### Secondary Activation (`modules/activation.py`)

**Method 1 (Passive Sync):**
1. Verify passive sync restore status
2. Patch restore with `veleroManagedClustersBackupName: latest`
3. Poll until restore Phase="Finished"

**Method 2 (Full Restore):**
1. Create new Restore resource with all backup names
2. Poll until restore Phase="Finished"

**Polling Strategy:**
- Check every 30 seconds
- Timeout after 30 minutes
- Log current phase and elapsed time

### Post-Activation Verification (`modules/post_activation.py`)

**Steps:**
1. Wait for ManagedClusters to connect (Available=True, Joined=True)
2. Restart observatorium-api deployment (if Observability)
3. Verify Observability pods are running
4. Guide metrics collection verification

**Timeouts:**
- ManagedCluster connection: 10 minutes
- Observability pod readiness: 5 minutes

### Finalization (`modules/finalization.py`)

**Steps:**
1. Enable BackupSchedule on secondary hub (version-aware)
2. Verify new backups are being created
3. Generate completion report

**Rollback:**
1. Delete/pause activation restore on secondary
2. Remove disable-auto-import annotations on primary
3. Restart Thanos compactor on primary
4. Unpause BackupSchedule on primary

### Decommission (`modules/decommission.py`)

**Interactive Steps:**
1. Delete MultiClusterObservability (if present)
2. Verify Observability pods terminated
3. Delete ManagedClusters (excluding local-cluster)
4. Delete MultiClusterHub
5. Verify ACM pods removed

**Safety:**
- Confirmation prompt for each destructive step
- Verify clusters available on new hub before deletion
- Non-interactive mode for automation (use with caution)

## Workflow Phases

```
┌─────────────────────────────────────────────────────────────┐
│                    INIT (Initial State)                      │
└─────────────────────┬───────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────┐
│           PREFLIGHT (Pre-Flight Validation)                  │
│  • Check namespaces, versions, operators                    │
│  • Verify backups, preserveOnDelete                         │
│  • Detect Observability, ACM version                        │
└─────────────────────┬───────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────┐
│         PRIMARY_PREP (Primary Hub Preparation)               │
│  • Pause BackupSchedule                                     │
│  • Disable auto-import                                      │
│  • Scale down Thanos compactor                              │
└─────────────────────┬───────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────┐
│           ACTIVATION (Secondary Hub Activation)              │
│  • Verify passive sync OR create full restore               │
│  • Activate managed clusters                                │
│  • Wait for restore completion                              │
└─────────────────────┬───────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────┐
│    POST_ACTIVATION (Post-Activation Verification)            │
│  • Wait for ManagedClusters to connect                      │
│  • Restart observatorium-api                                │
│  • Verify Observability pods                                │
└─────────────────────┬───────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────┐
│            FINALIZATION (Finalization)                       │
│  • Enable BackupSchedule on new hub                         │
│  • Verify new backups created                               │
│  • Generate completion report                               │
└─────────────────────┬───────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────┐
│                  COMPLETED (Success)                         │
└─────────────────────────────────────────────────────────────┘
```

**Failure Handling:**
- Any phase can transition to `FAILED` state
- State file retains completed steps
- Re-running resumes from last successful step
- Rollback available at any point

## Dry-Run Implementation

Dry-run mode is implemented at the Kubernetes client level:

```python
class KubeClient:
    def __init__(self, context: str, dry_run: bool = False):
        self.dry_run = dry_run
        
    def patch_custom_resource(self, ...):
        if self.dry_run:
            logger.info(f"[DRY-RUN] Would patch {plural}/{name} with: {patch}")
            return self.get_custom_resource(...)  # Return current state
        
        # Actual patch operation
        return self.custom_api.patch_namespaced_custom_object(...)
```

**Benefits:**
- Consistent dry-run behavior across all operations
- No code duplication in modules
- Easy to verify planned actions

## Error Handling Strategy

### Exception Hierarchy
The project uses a custom exception hierarchy defined in `lib/exceptions.py`:
- `SwitchoverError`: Base class for all application errors
- `FatalError`: Non-recoverable errors that stop execution immediately
- `TransientError`: Temporary errors that may be resolved by retrying
- `ValidationError`: Pre-flight check failures
- `ConfigurationError`: Missing or invalid configuration

### Validation Errors
- Stop immediately if critical validations fail
- Provide actionable error messages
- Guide user to fix issues

### Runtime Errors
- Catch specific exceptions (`SwitchoverError`) at module level
- Record error in state file
- Log detailed error for debugging
- Allow resume from last successful step

### Kubernetes API Errors
- Distinguish 404 (not found) from real errors
- Handle transient errors with retries (for polling)
- Provide context in error messages

**Example:**
```python
try:
    self.client.delete_custom_resource(...)
except ApiException as e:
    if e.status == 404:
        logger.debug("Resource already deleted")
        return False
    else:
        logger.error(f"Failed to delete: {e}")
        raise
```

## Testing Considerations

### Unit Testing
- Mock Kubernetes API responses
- Test state management logic
- Verify version comparison functions
- Test dry-run mode

### Integration Testing
- Test against real ACM hubs in test environment
- Verify idempotency (run twice, verify same result)
- Test rollback procedure
- Validate error recovery

### Production Testing
1. Run `--validate-only` first
2. Run `--dry-run` to preview
3. Test in non-production environment
4. Practice rollback procedure
5. Execute in production with monitoring

## Security Considerations

### Credentials
- Uses existing Kubernetes context credentials
- No credentials stored in script or state file
- Relies on RBAC permissions

### Required Permissions
- Read/write access to ACM custom resources
- Deployment/StatefulSet scaling permissions
- Pod listing for health checks
- Namespace read access

### State File
- Contains configuration but no secrets
- Safe to commit to version control (optional)
- Provides audit trail

## Performance Optimization

### Parallel Operations
- Future enhancement: Parallel validation checks
- Future enhancement: Concurrent ManagedCluster annotation updates

### Polling Efficiency
- 30-second intervals for most polls
- Longer timeouts for expected long operations
- Early exit when success detected

### Resource Efficiency
- Minimal API calls during polling
- Conditional resource fetching
- Efficient JSON serialization for state

## Extension Points

### Adding New Validation Checks
```python
# In modules/preflight.py
def _check_custom_validation(self):
    # Your validation logic
    self.add_result(
        "Custom Check",
        passed,
        "message",
        critical=True
    )
```

### Adding New Preparation Steps
```python
# In modules/primary_prep.py
def prepare(self):
    # Existing steps...
    
    if not self.state.is_step_completed("custom_step"):
        self._custom_preparation_step()
        self.state.mark_step_completed("custom_step")
```

### Custom Phases
- Extend `Phase` enum in `lib/utils.py`
- Add phase handling in `acm_switchover.py`
- Implement new module for phase logic

## Future Enhancements

1. **Parallel Execution**: Concurrent operations where safe
2. **Progress Bar**: Visual progress indicator using rich library
3. **Email/Slack Notifications**: Alert on completion or failure
4. **Metrics Collection**: Track switchover duration and success rate
5. **Multi-Hub Support**: Switch multiple hub pairs in sequence
6. **Pre-Switchover Snapshots**: Etcd backups for additional safety
7. **Automated Testing**: Verify cluster functionality post-switchover
8. **Web UI**: Browser-based interface for monitoring

## Maintenance

### Updating for New ACM Versions
1. Test with new ACM version
2. Update version detection logic if needed
3. Add version-specific handling if APIs change
4. Update documentation

### Dependency Updates
```bash
# Update dependencies
pip install --upgrade -r requirements.txt

# Test with updated dependencies
python acm_switchover.py --validate-only --help
```

### Contributing
1. Follow existing code patterns
2. Maintain idempotency
3. Add validation before operations
4. Update documentation
5. Test in non-production first

## Support and Troubleshooting

### Debug Mode
```bash
python acm_switchover.py --verbose ...
```

### State Inspection
```bash
cat .state/switchover-<primary>__<secondary>.json | python -m json.tool
```

### Log Analysis
- Check for `[DRY-RUN]` prefix in dry-run mode
- Look for `✓` (success) or `✗` (failure) markers
- Review timestamps in state file for duration analysis

### Common Issues
- See docs/operations/usage.md "Troubleshooting" section
- Check state file for error messages
- Verify Kubernetes contexts are accessible
- Ensure RBAC permissions are sufficient
