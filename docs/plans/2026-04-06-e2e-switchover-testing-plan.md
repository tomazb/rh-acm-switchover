# E2E Switchover Testing — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Validate ACM switchover tool by running real switchovers against live clusters (mgmt1↔mgmt2, prod1-prod3), fixing bugs found, and tracking success rate.

**Architecture:** Incremental three-phase approach — fix cluster state, single switchover, then multi-cycle testing. Bash scripts for discovery/verification, Python tool for switchover execution. Passive sync method throughout.

**Tech Stack:** Bash (discover-hub.sh, preflight-check.sh, postflight-check.sh), Python (acm_switchover.py), oc/kubectl CLI, Velero/OADP

---

## Phase 1: Fix & Validate

### Task 1: Investigate mgmt2 Restore Error

**Context:** `discover-hub.sh` reports mgmt2 passive restore is in Error state. We need to understand what went wrong before fixing it.

**Step 1: Check restore resources on mgmt2**

```bash
oc get restore -n open-cluster-management-backup --context mgmt2 -o yaml
```

Look for: `.status.phase`, `.status.validationErrors`, `.status.errors` on the failed restore.

**Step 2: Check Velero restore logs**

```bash
oc get restore -n open-cluster-management-backup --context mgmt2 --sort-by=.metadata.creationTimestamp
```

Identify the most recent failed restore name from the output.

```bash
oc logs -n open-cluster-management-backup -l app.kubernetes.io/name=velero --tail=100 --context mgmt2 | grep -i "error\|fail\|restore"
```

**Step 3: Check BackupSchedule health on mgmt1**

```bash
oc get backupschedule -n open-cluster-management-backup --context mgmt1 -o yaml
```

Verify: `.status.phase` should be `Enabled` or `Running`, `.status.lastBackup` should be recent.

**Step 4: Document findings**

Record what the error is, whether it's a tool bug or cluster state issue.

---

### Task 2: Fix mgmt2 Restore State

**Context:** Based on Task 1 findings, fix the restore error. The typical fix for a stale/errored passive-sync restore is to delete it and let the system recreate it, or create a fresh one.

**Step 1: Delete the failed restore resource**

```bash
oc delete restore -n open-cluster-management-backup --all --context mgmt2
```

**Step 2: Verify the restore is gone**

```bash
oc get restore -n open-cluster-management-backup --context mgmt2
```

Expected: No resources found.

**Step 3: Create fresh passive-sync restore (if not auto-created)**

Check if the cluster-backup-operator auto-creates it. If not, the switchover tool's preflight may handle it, or we create it manually:

```bash
# Only if needed — check first whether the operator recreates it
oc get restore -n open-cluster-management-backup --context mgmt2 -w
```

Wait up to 2 minutes. If no restore appears, we'll address it in the preflight step.

---

### Task 3: Run Preflight Validation

**Context:** With mgmt2's restore fixed, validate both hubs are ready for switchover.

**Step 1: Run preflight check script**

```bash
./scripts/preflight-check.sh --primary-context mgmt1 --secondary-context mgmt2 --method passive
```

Expected: All checks PASS or WARNING (no FAIL).

**Step 2: If preflight fails — investigate and fix**

Common issues:
- Missing backup storage credentials → check `cloud-credentials` secret
- Stale restore → delete and recreate (Task 2)
- ACM version mismatch → informational only for same-version

**Step 3: Run discovery to confirm healthy state**

```bash
./scripts/discover-hub.sh --auto
```

Expected:
- mgmt1: `primary`, 3/3 clusters, BackupSchedule active
- mgmt2: `standby`, 0/3 clusters, passive sync healthy (or ready for sync)

**Step 4: Commit checkpoint — document pre-switchover state**

Save the discovery output for comparison:

```bash
./scripts/discover-hub.sh --auto > e2e-15-switchover/discovery_phase1_baseline.log 2>&1
```

---

## Phase 2: Single Switchover

### Task 4: Dry-Run Switchover

**Context:** Run the switchover in dry-run mode first to catch obvious issues without making changes.

**Step 1: Activate virtual environment**

```bash
source .venv/bin/activate
```

**Step 2: Clean stale state files**

```bash
rm -f .state/switchover-mgmt1__mgmt2.json .state/switchover-mgmt1__mgmt2.json.lock
```

**Step 3: Execute dry-run**

```bash
python acm_switchover.py \
  --primary-context mgmt1 \
  --secondary-context mgmt2 \
  --method passive \
  --old-hub-action secondary \
  --argocd-manage \
  --dry-run
```

Expected: All phases complete with "DRY RUN" messages, no errors, exit code 0.

**Step 4: If dry-run fails — investigate and fix**

Check the output for the specific phase and error. Fix tool code if it's a bug, fix cluster state if operational.

---

### Task 5: Execute Real Switchover (mgmt1 → mgmt2)

**Context:** With dry-run passing, execute the real switchover.

**Step 1: Clean state files**

```bash
rm -f .state/switchover-mgmt1__mgmt2.json .state/switchover-mgmt1__mgmt2.json.lock
```

**Step 2: Execute switchover**

```bash
python acm_switchover.py \
  --primary-context mgmt1 \
  --secondary-context mgmt2 \
  --method passive \
  --old-hub-action secondary \
  --argocd-manage 2>&1 | tee e2e-15-switchover/phase2_switchover_mgmt1_to_mgmt2.log
```

Expected: All phases complete (PREFLIGHT → PRIMARY_PREP → ACTIVATION → POST_ACTIVATION → FINALIZATION → COMPLETED), exit code 0.

**Step 3: If switchover fails — investigate, fix, and retry**

- Check which phase failed in the log output
- Check the state file: `cat .state/switchover-mgmt1__mgmt2.json | python3 -m json.tool`
- If tool bug: fix the code, commit, retry (tool supports resume from failed phase)
- If cluster state issue: fix via oc commands, then retry

---

### Task 6: Verify Switchover Success

**Context:** Confirm the switchover worked correctly.

**Step 1: Run postflight check**

```bash
./scripts/postflight-check.sh --new-hub-context mgmt2 --old-hub-context mgmt1
```

Expected: All checks PASS.

**Step 2: Run discovery**

```bash
./scripts/discover-hub.sh --auto
```

Expected:
- mgmt2: `primary`, 3/3 clusters, BackupSchedule active
- mgmt1: `standby`, 0/3 clusters

**Step 3: Verify managed clusters directly**

```bash
oc get managedclusters --context mgmt2
```

Expected: prod1, prod2, prod3 all in `True/True/True` (Joined/Available/HubAccepted).

```bash
oc get managedclusters --context mgmt1
```

Expected: Either no managed clusters, or all detached.

**Step 4: Save verification output**

```bash
./scripts/discover-hub.sh --auto > e2e-15-switchover/discovery_phase2_after_switchover.log 2>&1
```

---

## Phase 3: Multi-Cycle Testing

### Task 7: Prepare for Multi-Cycle Testing

**Context:** Before running 15 consecutive switchovers, ensure clean starting state.

**Step 1: Clean old test artifacts**

```bash
rm -rf e2e-15-switchover/cycle_*.log e2e-15-switchover/cycle_*_postflight.log
rm -f e2e-15-switchover/summary.json
rm -f e2e-15-switchover/discovery_before.log e2e-15-switchover/discovery_after.log
```

**Step 2: Clean state files**

```bash
rm -f .state/switchover-mgmt1__mgmt2.json .state/switchover-mgmt1__mgmt2.json.lock
rm -f .state/switchover-mgmt2__mgmt1.json .state/switchover-mgmt2__mgmt1.json.lock
```

**Step 3: Verify current state is correct starting point**

```bash
./scripts/discover-hub.sh --auto
```

Note which hub is currently primary — `run_15_switchover_test.sh` starts with HUB_A=mgmt2, HUB_B=mgmt1 and alternates. Odd cycles: mgmt2→mgmt1, even cycles: mgmt1→mgmt2. If mgmt2 is currently primary (from Phase 2), cycle 1 will go mgmt2→mgmt1 which is correct.

If mgmt1 is still primary, consider whether to adjust the test script or do one more switchover to get mgmt2 as primary first.

---

### Task 8: Run 15-Cycle Switchover Test

**Context:** Execute the full automated test suite.

**Step 1: Launch the test with stop-on-failure**

```bash
./run_15_switchover_test.sh --stop-on-failure 2>&1 | tee e2e-15-switchover/full_run.log
```

This will:
- Run discovery before starting
- Execute 15 alternating switchovers (mgmt2↔mgmt1)
- Run postflight after each cycle
- 30s cooldown between cycles
- Stop on first failure
- Write summary.json

**Step 2: Monitor progress**

In a separate terminal (or check periodically):

```bash
ls -la e2e-15-switchover/cycle_*.log | wc -l  # completed cycles
cat e2e-15-switchover/summary.json 2>/dev/null  # current results
```

**Step 3: If a cycle fails**

1. Check which cycle failed: `cat e2e-15-switchover/full_run.log | tail -50`
2. Check the cycle log: `cat e2e-15-switchover/cycle_NN.log`
3. Run discovery to see current state: `./scripts/discover-hub.sh --auto`
4. Investigate and fix (tool bug → fix code + commit; cluster issue → fix via oc)
5. Decision: restart from scratch or manually continue

**Step 4: After completion (or stop-on-failure)**

```bash
cat e2e-15-switchover/summary.json | python3 -m json.tool
```

Record: success_rate%, total passed/failed, cycle durations.

---

### Task 9: Analyze Results

**Context:** After the test completes, analyze results and generate report.

**Step 1: Check summary**

```bash
cat e2e-15-switchover/summary.json | python3 -m json.tool
```

**Step 2: Run analysis script (if soak-format logs exist)**

```bash
./analyze_soak_results.sh e2e-15-switchover/
```

**Step 3: Check for patterns in failures**

```bash
# Look for common error patterns across cycle logs
grep -l "FAILED\|ERROR\|error\|Traceback" e2e-15-switchover/cycle_*.log
```

For each failed cycle, examine:
- Which phase failed
- What error occurred
- Whether it was transient or systematic

**Step 4: Run final discovery**

```bash
./scripts/discover-hub.sh --auto > e2e-15-switchover/discovery_final.log 2>&1
./scripts/discover-hub.sh --auto
```

Confirm clusters are healthy and in a known state.

**Step 5: Summarize findings**

Document:
- Overall success rate
- Bugs found and fixed (with commit references)
- Average cycle duration
- Any remaining issues

---

## Bug Fix Protocol (Use When Issues Found)

When a switchover fails due to a tool bug:

1. **Identify**: Check log output and state file
2. **Reproduce**: Understand the exact conditions
3. **Fix**: Edit the relevant Python module or bash script
4. **Test**: Run `source .venv/bin/activate && pytest tests/ -v` to ensure no regressions
5. **Commit**: `git add -A && git commit -m "fix: description of fix"`
6. **Retry**: Re-run the failed switchover or restart the multi-cycle test
