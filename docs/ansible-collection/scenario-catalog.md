# Shared Parity Scenario Catalog

Date: 2026-05-04
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

### SCENARIO-006 Restore-only activation

- method: full
- restore_only: true
- expected phases: preflight, activation, post_activation, finalization
- expected artifact: `restore-only-report.json`

### SCENARIO-007 Decommission old hub

- decommission confirmed or dry-run
- expected effective observability: auto-detected from namespace unless explicitly set
- expected artifact: `decommission-report.json`

### SCENARIO-008 Argo CD pause and failure recovery

- argocd manage: true
- resume_on_failure: true
- expected checkpoint/report: pause `run_id` preserved across retry

### SCENARIO-009 RBAC bootstrap and validation

- role: operator or validator
- optional decommission extension: operator only
- expected validation: shipped manifest permissions are covered, including Argo CD Application patch for operator manage mode

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

## Collection Coverage (Phase 6 and Safety Realignment)

| Scenario ID | Python | Collection | Notes |
|-------------|--------|------------|-------|
| `restore-only-success` | yes | yes | Restore-only report and secondary-only preflight are dual-supported |
| `argocd-resume-on-failure` | yes | yes | Collection preserves pause `run_id` through retry checkpoints |
| `decommission-observability-auto` | yes | yes | Collection defaults to namespace autodetection with explicit override support |
| `rbac-bootstrap-operator` | yes | yes | Bootstrap validation covers the permissions shipped by manifests |
| `machine-readable-reports` | yes | yes | Python and collection emit schema version `1.0` report artifacts |
| `runtime-parity-safety` | yes | yes | Backup, restore, post-activation, finalization, RBAC, and decommission safety paths are covered by targeted regression tests |
