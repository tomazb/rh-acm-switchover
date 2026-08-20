# Shared Parity Scenario Catalog

Date: 2026-05-12
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
- scoped discovery: every normalized namespace returns exactly one positive
  result before any Application is aggregated or patched
- retry expectation: reconciled Applications are re-paused, already-correct
  Applications are unchanged, and unrelated Applications are never patched
- standalone expectation: checkpoint identity is validated before either hub
  role runs; the result reports exact per-hub and total changed-patch counts

### SCENARIO-009 RBAC bootstrap and validation

- role: operator or validator
- optional decommission extension: operator only
- expected validation: shipped manifest permissions are covered, including Argo CD Application patch for operator manage mode
- expected artifacts: applied manifest list and optional generated kubeconfig path are exposed in the bootstrap result; release guardrails track this separately from switchover reports

### SCENARIO-010 distinct-physical-hub guard

- normal two-hub inputs with matching context names fail before mutation
- different contexts that return an equal live `kube-system` Namespace UID fail
  before mutation
- malformed or unavailable live role evidence fails closed without trusting
  public, registered, cached, or caller-supplied UID data
- checkpoint identity drift still fails after the trusted UID comparison
- a pre-barrier refusal enters neither recovery nor checkpoint reset; a
  post-barrier failure retains the existing recovery path
- execute plus native Ansible check mode performs fresh UID GETs and makes no
  writes; `validate` and `dry_run` use only the explicit non-live test override
- restore-only remains secondary-only, and standalone decommission is excluded

Concrete collection coverage is
`ansible_collections/tomazb/acm_switchover/tests/integration/test_distinct_hub_identity_barrier.py`:
Cases A-F are `test_same_live_cluster_rejects_spoofed_distinct_extra_vars`,
`test_unavailable_live_uid_rejects_spoofed_identity`,
`test_checkpoint_drift_rejects_spoofed_stored_identity`,
`test_pre_barrier_failure_ignores_spoofed_recovery_values`,
`test_post_barrier_failure_retains_recovery`, and
`test_execute_check_mode_uses_fresh_uids_without_mutation`. Checkpoint and
resume compatibility remain in
`ansible_collections/tomazb/acm_switchover/tests/scenario/test_checkpoint_resume.py`.

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
| `release-1.7.10-artifact-guardrails` | yes | yes | Release tests cover required fields for switchover, restore-only, decommission, RBAC/bootstrap, checkpoint, and report artifacts |
| `distinct-physical-hub` | yes | yes | Same-context, same-live-UID, unreadable evidence, resume drift, recovery boundary, native-check freshness, restore-only, and decommission exclusions are pinned by focused tests |
