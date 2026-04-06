# E2E Switchover Testing Design

## Problem

Validate the ACM switchover tool by running real switchovers against live clusters
(mgmt1, mgmt2 as hubs; prod1-prod3 as managed clusters). Fix any bugs found in
the tool or scripts, track success rate across multiple cycles.

## Current State

- **mgmt1**: Active primary hub (ACM 2.14.2, 3/3 clusters, backups active)
- **mgmt2**: Standby hub (ACM 2.14.2, 0/3 clusters, passive restore in Error state)
- **prod1-prod3**: Managed clusters on OCP 4.19.21

## Approach

Incremental: fix → single switchover → multi-cycle testing.
Use bash scripts for discovery/verification, Python tool for switchover execution.
Passive sync method for all switchovers.

## Phase 1: Fix & Validate

**Goal**: Get mgmt2 into a healthy standby state with passive sync working.

1. Investigate Velero restore error on mgmt2
2. Delete failed restore resource
3. Verify BackupSchedule is healthy on mgmt1
4. Recreate passive-sync restore on mgmt2 if needed
5. Run `preflight-check.sh --primary-context mgmt1 --secondary-context mgmt2 --method passive`
6. Run `discover-hub.sh --auto` to confirm both hubs healthy

Fix strategy: fix tool code if bugs caused the error; fix cluster state via `oc` if operational.

## Phase 2: Single Switchover

**Goal**: One successful mgmt1→mgmt2 switchover.

1. Dry-run: `acm_switchover.py --primary-context mgmt1 --secondary-context mgmt2 --method passive --old-hub-action secondary --dry-run`
2. Real execution: same command without `--dry-run`, with `--argocd-manage`
3. Postflight: `postflight-check.sh --new-hub-context mgmt2 --old-hub-context mgmt1`
4. Discovery: `discover-hub.sh --auto` — confirm mgmt2 is primary with 3/3 clusters
5. If failure: investigate logs, fix code, rollback if needed, retry

## Phase 3: Multi-Cycle Testing

**Goal**: Repeated back-and-forth switchovers with success rate tracking.

1. Clean state files
2. Run `run_15_switchover_test.sh` (alternating mgmt2↔mgmt1, 30s cooldowns, postflight each cycle)
3. Monitor via `e2e-15-switchover/` output logs
4. On failure: investigate, fix tool bugs, resume or restart
5. Analyze: `summary.json` for success rate, `analyze_soak_results.sh` for timing

## Success Criteria

- Phase 1: Both hubs healthy, preflight passes
- Phase 2: Single switchover completes, postflight passes
- Phase 3: Track and report actual success rate; fix bugs found along the way

## Tracking

Existing `e2e-15-switchover/summary.json` provides: total_cycles, passed, failed,
success_rate%, per-cycle durations, and results array.

## Tools Used

| Tool | Purpose |
|------|---------|
| `discover-hub.sh --auto` | State discovery |
| `preflight-check.sh` | Pre-switchover validation |
| `acm_switchover.py` | Switchover execution |
| `postflight-check.sh` | Post-switchover verification |
| `run_15_switchover_test.sh` | Multi-cycle automated testing |
| `analyze_soak_results.sh` | Results analysis |
