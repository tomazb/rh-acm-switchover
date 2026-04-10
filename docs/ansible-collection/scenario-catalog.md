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
