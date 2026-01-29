# Bug Report: Resume Logic Incorrectly Treats Failed State as Completed

> **Status: RESOLVED** ✅
> **Resolution Date:** 2026-01-29
> **Fixed In:** Commit 19ac153 (post v1.5.3)
> **Fix Summary:** Added explicit handling for `Phase.FAILED` state in resume logic. When resuming from a failed state, the tool now:
> - Detects `current_phase == Phase.FAILED` explicitly
> - Retrieves the last error phase from state history
> - Logs clear resume context to user
> - Resets to the failed phase for retry
> - Requires `--force` if unable to determine failed phase
>
> The fix ensures FINALIZATION and other remaining phases execute correctly when resuming after failures.

## Summary
When a switchover fails during POST_ACTIVATION phase and is subsequently resumed, the tool incorrectly reports "SWITCHOVER COMPLETED SUCCESSFULLY" without executing the remaining FINALIZATION phase, even though the state file shows `phase: failed` and contains recorded errors.

## Environment
- **Tool Version**: 1.5.3 (2026-01-29)
- **ACM Version**: 2.14.1
- **OCP Version**: 4.19.21
- **Switchover Method**: passive
- **Old Hub Action**: secondary

## Steps to Reproduce

### 1. Initial Switchover Run
```bash
python acm_switchover.py \
  --primary-context mgmt1 \
  --secondary-context mgmt2 \
  --method passive \
  --old-hub-action secondary
```

### 2. Failure During POST_ACTIVATION
The switchover progressed through:
- ✓ PHASE 1: PRE-FLIGHT VALIDATION
- ✓ PHASE 2: PRIMARY HUB PREPARATION
- ✓ PHASE 3: SECONDARY HUB ACTIVATION
- ✗ PHASE 4: POST_ACTIVATION VERIFICATION (failed)

**Error logged:**
```text
2026-01-29 13:29:10 - ERROR - Post-activation verification failed: disable-auto-import annotation still present on: prod1, prod2, prod3
2026-01-29 13:29:10 - ERROR - Post-activation verification failed!
2026-01-29 13:29:10 - ERROR -
✗ Operation failed!
```

**State file shows:**
- `current_phase: failed`
- `errors: [{"phase": "post_activation_verification", "error": "...", "timestamp": "..."}]`
- 9 completed steps (stopped at POST_ACTIVATION)

### 3. Manual Fix Applied
User manually removed the problematic annotations:
```bash
for cluster in prod1 prod2 prod3; do
  oc --context=mgmt2 annotate managedcluster $cluster import.open-cluster-management.io/disable-auto-import-
done
```

### 4. Resume Attempt
```bash
python acm_switchover.py \
  --primary-context mgmt1 \
  --secondary-context mgmt2 \
  --method passive \
  --old-hub-action secondary
```

**Expected Behavior:**
- Detect state is in `failed` phase
- Resume from POST_ACTIVATION phase
- Retry the failed verification step
- Continue to FINALIZATION phase
- Complete remaining steps (backup schedule setup, old hub as secondary, etc.)

**Actual Behavior:**
```text
2026-01-29 13:29:57 - INFO - ACM Hub Switchover Automation v1.5.3 (2026-01-29)
2026-01-29 13:29:57 - INFO - Started at: 2026-01-29T12:29:57.042829+00:00
2026-01-29 13:29:57 - INFO - Using state file: .state/switchover-mgmt1__mgmt2.json
2026-01-29 13:29:57 - INFO - Connecting to primary hub: mgmt1
2026-01-29 13:29:57 - INFO - Initialized Kubernetes client for context: mgmt1 (timeout: 30s)
2026-01-29 13:29:57 - INFO - Connecting to secondary hub: mgmt2
2026-01-29 13:29:57 - INFO - Initialized Kubernetes client for context: mgmt2 (timeout: 30s)
2026-01-29 13:29:57 - INFO -
============================================================
2026-01-29 13:29:57 - INFO - SWITCHOVER COMPLETED SUCCESSFULLY!
2026-01-29 13:29:57 - INFO - ============================================================
2026-01-29 13:29:57 - INFO -
Switchover completed at: 2026-01-29T13:29:57.081352+01:00
2026-01-29 13:29:57 - INFO - State file: .state/switchover-mgmt1__mgmt2.json
2026-01-29 13:29:57 - INFO -
Next steps:
2026-01-29 13:29:57 - INFO -   1. Inform stakeholders that switchover is complete
2026-01-29 13:29:57 - INFO -   2. Provide new hub connection details
2026-01-29 13:29:57 - INFO -   3. Verify applications are functioning correctly
2026-01-29 13:29:57 - INFO -   4. Optionally decommission old hub with: --decommission
2026-01-29 13:29:57 - INFO -
✓ Operation completed successfully!
```

Tool immediately reported completion **without**:
- Re-running POST_ACTIVATION verification
- Executing FINALIZATION phase (steps 10-21)
- Setting up BackupSchedule on mgmt2
- Configuring old hub (mgmt1) as secondary

## Impact

### Critical Issues
1. **Incomplete Switchover**: Finalization phase never runs, leaving:
   - No BackupSchedule on new hub (mgmt2)
   - Old hub (mgmt1) not configured as secondary
   - Observability components potentially not verified
   - No backup integrity verification

2. **False Success Report**: User receives "SWITCHOVER COMPLETED SUCCESSFULLY" message even though:
   - State file shows `phase: failed`
   - Error is recorded in state
   - Multiple critical steps remain incomplete

3. **Data Loss Risk**: Without BackupSchedule on new hub, no backups are being created, creating a window of vulnerability.

## Actual State After "Successful" Resume

### State File Analysis
```bash
$ python show_state.py

Current Phase: Completed  # ❌ INCORRECT - should be "failed" or show incomplete finalization

Completed Steps:
  ✓  1. Paused BackupSchedule on primary hub
  ✓  2. disable_auto_import
  ✓  3. scale_down_thanos
  ✓  4. Verified passive sync restore is running
  ✓  5. Patched restore to activate managed clusters
  ✓  6. Waited for restore to complete
  ✓  7. apply_immediate_import_annotations
  ✓  8. verify_klusterlet_connections
  ✓  9. Verified ManagedClusters are connected
  # Missing steps 10-21 (entire FINALIZATION phase)

Errors (1):
  ✗ [post_activation_verification] disable-auto-import annotation still present on: prod1, prod2, prod3
       2026-01-29 12:29:10
```

### Cluster State After "Successful" Resume
```bash
# mgmt2 (new hub) - Missing BackupSchedule!
$ oc --context=mgmt2 get backupschedule -n open-cluster-management-backup
No resources found in open-cluster-management-backup namespace.

# mgmt2 clusters - Successfully migrated (this part worked correctly)
$ oc --context=mgmt2 get managedclusters
NAME            HUB ACCEPTED   MANAGED CLUSTER URLS                      JOINED   AVAILABLE   AGE
local-cluster   true           https://api.mgmt2.htz1.all-it.tech:6443   True     True        2d18h
prod1           true           https://api.prod1.htz1.all-it.tech:6443   True     True        28h
prod2           true           https://api.prod2.htz1.all-it.tech:6443   True     True        28h
prod3           true           https://api.prod3.htz1.all-it.tech:6443   True     True        28h

# mgmt1 (old hub) - Not configured as secondary
$ oc --context=mgmt1 get restore.cluster.open-cluster-management.io -n open-cluster-management-backup
No resources found in open-cluster-management-backup namespace.
```

## Root Cause Analysis (Preliminary)

The issue appears to be in the resume logic in `acm_switchover.py`. When resuming from a state file:

1. **State Loading**: Tool correctly loads state showing `phase: failed`
2. **Phase Detection**: Resume logic misinterprets the state
3. **Completion Check**: Incorrectly determines switchover is "complete"
4. **Early Exit**: Exits with success message without executing remaining phases

**Suspected Code Location**:
- Main orchestrator logic in `acm_switchover.py` (phase transition handling)
- StateManager resume detection in `lib/utils.py`
- Completion criteria logic (may be checking completed steps count instead of phase)

## Expected Resume Behavior

The tool should:

1. **Detect Failed State**: Recognize `phase: failed` in state file
2. **Log Resume Context**:
   ```text
   INFO - Resuming from failed state
   INFO - Last error: disable-auto-import annotation still present on: prod1, prod2, prod3
   INFO - Failed at phase: post_activation_verification
   INFO - Will retry from step: verify_klusterlet_connections
   ```
3. **Retry Failed Step**: Re-run the verification that failed
4. **Continue Execution**: Proceed through remaining phases:
   - Complete POST_ACTIVATION (steps 10+)
   - Execute FINALIZATION (enable backups, verify integrity, set up old hub)
5. **Only Report Success When**: All phases truly complete and `phase: completed` is set

## Workaround

Users can manually complete finalization:

```bash
# 1. Enable BackupSchedule on new hub
oc --context=mgmt2 apply -f - <<EOF
apiVersion: cluster.open-cluster-management.io/v1beta1
kind: BackupSchedule
metadata:
  name: acm-hub-backup
  namespace: open-cluster-management-backup
spec:
  useManagedServiceAccount: true
  veleroSchedule: "0 */4 * * *"
  veleroTtl: 720h
  managedServiceAccountTTL: 720h
EOF

# 2. Set up old hub as secondary
oc --context=mgmt1 apply -f - <<EOF
apiVersion: cluster.open-cluster-management.io/v1beta1
kind: Restore
metadata:
  name: restore-acm-passive-sync
  namespace: open-cluster-management-backup
spec:
  cleanupBeforeRestore: CleanupRestored
  veleroManagedClustersBackupName: skip
  veleroCredentialsBackupName: latest
  veleroResourcesBackupName: latest
  syncRestoreWithNewBackups: true
  restoreSyncInterval: 10m
EOF

# 3. Verify observability components
# 4. Test backup creation
```

## Reproduction Rate
**100%** - Consistently reproducible when:
- Switchover fails during POST_ACTIVATION or later phases
- State is manually corrected (e.g., annotations removed)
- Tool is invoked again with same arguments

## Additional Context

### Similar Issue Observed Earlier
This same pattern occurred in an earlier test run:
```bash
# State showed "completed" even though only 5 steps done
$ cat .state/switchover-mgmt1__mgmt2.json | jq -r '.current_phase, (.completed_steps | length)'
completed
5
```

This suggests the bug is systemic in the resume logic, not a one-time occurrence.

### Positive Note
Despite the resume bug, the **core switchover functionality works correctly**:
- ✓ Passive sync restore activation
- ✓ ManagedCluster restoration (239 items)
- ✓ Klusterlet reconnection to new hub
- ✓ All 3 managed clusters showing "Available" on mgmt2

The bug only affects resume/completion detection, not the actual migration logic.

## Recommended Fix

1. **Immediate Fix** (StateManager):
   ```python
   # In lib/utils.py - StateManager
   def is_switchover_complete(self) -> bool:
       """Check if switchover is truly complete"""
       current_phase = self.get_current_phase()
       # Don't rely solely on completed steps count
       return current_phase == Phase.COMPLETED  # Must be explicit COMPLETED phase
   ```

2. **Resume Logic Enhancement**:
   ```python
   # In acm_switchover.py main orchestrator
   if state.get_current_phase() == Phase.FAILED:
       logger.info(f"Resuming from failed state")
       logger.info(f"Last error: {state.get_errors()[-1]}")
       # Resume from the phase that failed, not skip to end
       current_phase = state.get_phase_before_failure()
   ```

3. **Add Phase Validation**:
   - Verify all required steps completed before setting `phase: completed`
   - Log warning if transitioning to completed with missing steps
   - Fail-safe: Don't claim success unless FINALIZATION phase explicitly sets it

## Related Files
- `acm_switchover.py` (main orchestrator)
- `lib/utils.py` (StateManager class)
- `modules/finalization.py` (missing execution)
- `.state/switchover-mgmt1__mgmt2.json` (state file with bug evidence)

## Testing Evidence
- Full test log: `/tmp/switchover-final-run.log`
- State file snapshot: `.state/switchover-mgmt1__mgmt2.json`
- Test execution date: 2026-01-29

## Priority
**HIGH** - This bug causes:
- Incomplete switchovers reported as successful
- Missing critical finalization steps (backup setup)
- Potential data loss window
- User confusion (false success message)
