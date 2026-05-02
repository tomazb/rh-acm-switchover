# Shared Parity Scenario Catalog

Date: 2026-04-10
Purpose: Define scenarios that both implementations must eventually satisfy

## Scenario Schema

Each scenario records:

- inputs
- initial cluster state assumptions
- expected phase outcomes
- expected validation findings
- expected mutated resources
- expected report and checkpoint artifacts

## Initial Scenarios

### SCENARIO-001 Passive switchover happy path

- method: passive
- old hub action: secondary
- expected phases: all pass
- expected artifacts: report present, checkpoint optional

### SCENARIO-002 Full restore switchover happy path

- method: full
- expected phases: all pass
- expected artifacts: report present

### SCENARIO-003 Preflight version mismatch

- expected preflight: fail
- expected later phases: not run
- expected artifacts: report present

### SCENARIO-004 Validate-only mode

- expected mutations: none
- expected artifact: report present

### SCENARIO-005 Dry-run mode

- expected mutations: none
- expected artifact: report present

## Collection Coverage (Phase 2)

| Scenario ID | Python | Collection | Notes |
|-------------|--------|------------|-------|
| `preflight-passive-success` | yes | yes | Matching report contract required |
| `preflight-input-failure` | yes | yes | Missing secondary context blocks execution |
| `preflight-version-mismatch` | yes | yes | Minor version mismatch fails preflight |
| `preflight-backup-failure` | yes | yes | Missing backup artifacts or BSL health fails preflight |

## Collection Coverage (Phase 3)

| Scenario ID | Python | Collection | Notes |
|-------------|--------|------------|-------|
| `switchover-passive-success` | yes | yes | All phases pass; all four phase reports present in report artifact |
| `switchover-post-activation-cluster-failure` | yes | yes | Cluster not joined/available; post_activation status=fail, report written before play exits |
| `switchover-finalization-backup-recovery` | yes | yes | Full end-to-end fixture; backup enable + MCH verify + old hub disposition emitted |
