# Lab Role Controller External Live Lab Config Schema Design

## Status

This Phase 8B document is design-only. It does not introduce live config loading, does not provide a real config,
does not execute anything live, and does not finalize a production JSON schema.

Live config files must remain outside Git. Any future live config examples in this repository must be sanitized and
fake. Runtime-only fields must not appear in artifacts, and a future implementation must validate redaction before
artifact creation. This document defines guardrails and schema concepts only.

Guardrail wording intentionally pinned by tests:

- live config files must remain outside Git
- examples are sanitized and fake
- runtime-only fields must not appear in artifacts
- future implementation must validate redaction before artifact creation
- production JSON schema finalization remains unsupported

Phase 8B remains non-live. It adds no live discovery, no kubeconfig reading, no environment reading, no `oc`,
`kubectl`, or `ansible-playbook` calls, no live release adapter execution, no automatic live recovery, no committed
generated profiles, no default `.release` output, and no live certification evidence.

## Design Goals

- Keep future live lab configuration external to Git.
- Separate runtime-only inputs from artifact-safe metadata.
- Name every future live gate from L0 through L10 without making the gates executable.
- Make read-only/preflight-only the first future live scenario family.
- Keep mutating, restore, decommission, failure-injection, and soak scenarios later-phase only.
- Keep Agent behavior subordinate to controller decisions.
- Preserve fail-closed defaults until a later audited live phase implements real loading and execution.

## Conceptual Schema

The future external live lab config is a caller-provided runtime input. Phase 8B does not load it. The shape below is a
conceptual model for Phase 8C and later.

Top-level sections:

- `schema_version`: config schema version. This is conceptual in Phase 8B, not a production JSON schema.
- `lab_id`: optional redacted lab label suitable for artifacts.
- `plan_id`: optional redacted plan label suitable for artifacts.
- `physical_hubs`: two or more physical hub entries with runtime references and redacted identity expectations.
- `managed_clusters`: expected managed cluster inventory.
- `approval`: operator approval metadata and mutation boundaries.
- `credentials`: runtime-only credential policy.
- `identity_expectations`: expected physical identity signals, expressed as fingerprints or redacted summaries.
- `role_discovery`: required logical-role evidence categories.
- `rbac_prerequisites`: required read or mutation prerequisites by scenario family.
- `scenario_allowlist`: explicit future live scenario allowlist.
- `artifact_policy`: caller-provided output, redaction, retention, and publication boundaries.
- `redaction_policy`: rejected values and required fingerprinting/redaction behavior.
- `execution_policy`: fail-closed execution defaults.

## Field Sensitivity Model

Runtime-only fields are accepted only by a future executing process and are never artifact-facing. Artifact-safe fields
are fingerprints, booleans, counts, labels, or redacted summaries.

| Section | Field | Sensitivity | Artifact rule |
| --- | --- | --- | --- |
| `physical_hubs` | `physical_label` | artifact-safe label | May appear in artifacts. |
| `physical_hubs` | `context_ref` | runtime-only | Must not appear in artifacts. |
| `physical_hubs` | `kubeconfig_ref` | runtime-only | Must not appear in artifacts. |
| `physical_hubs` | `expected_identity_fingerprint` | artifact-safe fingerprint | May appear in artifacts. |
| `physical_hubs` | `expected_api_fingerprint` | artifact-safe fingerprint | May appear in artifacts. |
| `physical_hubs` | `expected_cluster_version` | artifact-safe optional value | May appear only when operator-approved. |
| `physical_hubs` | `acm_hub_evidence_requirements` | artifact-safe policy | May appear in artifacts. |
| `managed_clusters` | `expected_names` | artifact-sensitive inventory | Future artifacts should prefer counts and hashes. |
| `managed_clusters` | `cluster_identity_fingerprints` | artifact-safe fingerprints | May appear in artifacts. |
| `approval` | `approver_reference` | redacted approval handle | May appear only as redacted value. |
| `approval` | `approval_timestamp` | artifact-safe timestamp | May appear in artifacts. |
| `credentials` | `credentials.runtime_only` | runtime-only policy | May appear as policy only, never as values. |
| `credentials` | `allowed_env_vars` | artifact-safe allowlist | May appear in artifacts. |
| `credentials` | `forbidden_env_patterns` | artifact-safe policy | May appear in artifacts. |
| `identity_expectations` | `mismatch_policy` | artifact-safe policy | May appear in artifacts. |
| `role_discovery` | `ambiguity_policy` | artifact-safe policy | May appear in artifacts. |
| `rbac_prerequisites` | `read_only_checks_required` | artifact-safe policy | May appear in artifacts. |
| `rbac_prerequisites` | `mutation_checks_required` | artifact-safe policy | May appear in artifacts. |
| `scenario_allowlist` | `allowlist_version` | artifact-safe label | May appear in artifacts. |
| `redaction_policy` | `reject_raw_api_urls` | artifact-safe policy | May appear in artifacts. |
| `redaction_policy` | `fingerprint_identity_values` | artifact-safe policy | May appear in artifacts. |
| `artifact_policy` | `artifact_dir` | caller-provided runtime path | Must be summarized, never copied raw when sensitive. |

Runtime-only fields include `context_ref`, `kubeconfig_ref`, credential handles, process environment references, and any
temporary profile references. Artifact-facing metadata must use redacted values, fingerprints, booleans, counts, or
hashes.

The forbidden committed values include real kubeconfig contents, real kubeconfig paths, raw API URLs,
token/password/secret or credential values, private lab identifiers, generated live profiles, live artifacts, and
release runtime output. If a future field cannot be safely classified as runtime-only, artifact-safe, or
redacted/fingerprint-only, it must not be committed or emitted in publishable artifacts.

## Section Requirements

### `physical_hubs`

Future fields:

- `physical_label`: stable operator label, for example `hub-a` or `hub-b`.
- `context_ref`: runtime-only.
- `kubeconfig_ref`: runtime-only.
- `expected_identity_fingerprint`: fingerprint/redacted value only.
- `expected_api_fingerprint`: fingerprint/redacted value only.
- `expected_cluster_version`: optional.
- `acm_hub_evidence_requirements`: required ACM hub evidence categories.

### `managed_clusters`

Future fields:

- `expected_names`: exact expected names supplied by the operator.
- `exact_match_required: true`
- `unexpected_cluster_policy: block`
- `cluster_identity_fingerprints`: optional redacted/fingerprint-only values.

Certification must block when the observed managed cluster set differs from the expected set.

### `approval`

Future fields:

- `operator_confirmed_live_mode`
- `mutation_allowed`
- `mutation_confirmation_required`
- `approved_scenarios`
- `approval_timestamp`: optional.
- `approver_reference`: optional redacted value.

Human approval gates are controller-enforced. Agent agreement or Agent summary text is not approval.

### `credentials`

Future fields:

- `credentials.runtime_only: true`
- `persist_to_artifacts: false`
- `inherit_environment: false`
- `allowed_env_vars`: explicit allowlist only.
- `forbidden_env_patterns`: explicit deny patterns for credential-like data.

The future implementation must not inherit the process environment wholesale. It must not write credential values,
credential paths, or raw kubeconfig references into artifacts.

### `identity_expectations`

Future fields:

- `hub_identity_fingerprints`: expected hub identity values as fingerprints only.
- `api_identity_fingerprints`: expected API identity values as fingerprints only.
- `cluster_version_expectations`: optional redacted version expectations.
- `mismatch_policy: block`

Raw API URLs and private cluster identifiers are forbidden in examples and artifacts. Identity mismatches must block
certification until a future controller can prove safe live identity evidence.

### `role_discovery`

Future fields:

- `required_evidence`: logical-role evidence categories required before a scenario.
- `active_role_policy: exactly-one-primary`
- `ambiguity_policy: block`
- `fresh_discovery_required: true`

Role discovery artifacts must summarize signal categories and ambiguity status without publishing raw live resource
payloads, kubeconfig references, or private lab identifiers.

### `rbac_prerequisites`

Future fields:

- `read_only_checks_required: true`
- `mutation_checks_required: false` by default.
- `deny_checks_required`: optional, scenario-family dependent.
- `prerequisite_health_checks`: ACM, MCE, MCH, backup/restore, Argo CD, and tool-version categories.

Read-only prerequisite checks still require live approval in a future phase because read-only cluster contact is live
execution. Mutation prerequisites must remain false until a later audited mutating phase.

### `scenario_allowlist`

Future fields:

- `approved_scenarios`: catalog scenario IDs allowed for the specific future phase.
- `allowlist_version`: audited allowlist label.
- `first_live_family: read-only-preflight-only`
- `unknown_scenario_policy: block`

The allowlist must use scenario IDs from `tests/release/scenarios/catalog.py`. It must not permit arbitrary shell
commands or Agent-invented live commands.

### `artifact_policy`

Future fields:

- `artifact_dir` must be caller-provided. In plain guardrail wording: artifact_dir must be caller-provided.
- no default `.release` output.
- no committed live artifacts.
- redaction required.
- stdout/stderr sanitization required.
- retention policy must be explicit.

### `redaction_policy`

Future fields:

- `reject_raw_api_urls: true`
- `reject_private_ids: true`
- `fingerprint_identity_values: true`
- `reject_credential_values: true`
- `forbidden_artifact_patterns`: credential-like, kubeconfig-like, endpoint-like, and private-ID-like patterns.

A future implementation must validate redaction before artifact creation. Redaction failure must block certification
and must not produce live certification evidence.

### `execution_policy`

Required fail-closed defaults:

- `live_execution_enabled: false`
- `read_only_discovery_enabled: false`
- `mutation_enabled: false`
- `automatic_recovery_enabled: false`
- `live_certification_evidence_enabled: false`

Read-only cluster contact is still live execution. A future phase must explicitly enable it after independent review.

## Future Live Gate Model

The L0-L10 gates are schema/design concepts in Phase 8B. They are design-only / not executable.

| Gate ID | Purpose | Input evidence | Artifact evidence | Failure decision | Retry/recovery stance | Current Phase 8B status |
| --- | --- | --- | --- | --- | --- | --- |
| L0: explicit live mode selected | Prove the operator intentionally entered live scope. | Explicit live mode input and approval reference. | Selected mode, approval reference, timestamp summary. | `BLOCKED` before execution. | Retry only after corrected invocation. | design-only / not executable |
| L1: clean working tree and expected branch/commit verified | Prevent unreviewed source from producing evidence. | Expected branch, commit, clean tree, release metadata state. | Redacted status summary and commit hash. | `NO_GO` for certification or `BLOCKED` for readiness. | Retry after checkout correction. | design-only / not executable |
| L2: external live lab config provided from outside Git | Prevent committed or implicit live config use. | Runtime config reference supplied outside Git. | Config hash, schema version, source category, no raw path. | `BLOCKED`. | Retry after config is supplied. | design-only / not executable |
| L3: runtime-only kubeconfig/credential references validated | Prove runtime handles exist without publishing them. | Credential handle presence and permission prechecks. | Redacted handle fingerprints and presence status. | `BLOCKED` before contact or `NO_GO` on unsafe publication risk. | Retry after credential correction. | design-only / not executable |
| L4: physical hub identity proof passes | Bind physical labels to real hub identities. | Multiple identity signals and expected fingerprints. | Fingerprinted identity comparison result. | `NO_GO` before mutation. | Retry only before mutation and after approval. | design-only / not executable |
| L5: logical role discovery proof passes | Prove current primary and secondary roles. | Active/passive ACM role evidence from both hubs. | Role evidence summary with ambiguity status. | `NO_GO` or `RECOVERY_REQUIRED`. | Retry only after fresh discovery and approval. | design-only / not executable |
| L6: managed cluster set exactly matches expectation | Ensure the lab inventory matches the approved plan. | Exact observed names and expected set. | Expected count and hashed-name comparison summary. | `NO_GO` for certification. | Retry after operator resolves drift. | design-only / not executable |
| L7: RBAC/live prerequisites pass | Confirm required permissions and service health. | RBAC checks, ACM/MCE/MCH health, backup/restore health, tool versions. | Prerequisite summary and failing capability list. | `NO_GO`. | Retry after remediation and fresh checks. | design-only / not executable |
| L8: scenario live allowlist permits scenario | Prevent unsupported scenario execution. | Scenario ID in audited live allowlist. | Scenario classification, allowlist version, reason. | `BLOCKED`. | Retry only after reviewed code/config change. | design-only / not executable |
| L9: dry-run/materialized invocation reviewed | Let the operator review the exact plan before execution. | Sanitized argv summary, redacted env plan, profile hash, artifact plan. | Reviewed materialization hash and approval reference. | `BLOCKED`. | Retry after re-materialization. | design-only / not executable |
| L10: final human confirmation before mutation | Require immediate approval before first mutation. | Fresh approval after L0-L9 and non-stale evidence. | Confirmation timestamp, scenario ID, role state, profile hash. | `BLOCKED`. | No automatic retry; each mutation requires new confirmation. | design-only / not executable |

Future implementations must record gate outcomes without exposing runtime-only fields. Phase 8B stores no gate state and
executes no gates.

## Scenario And Command Guardrails

The first future live scenario remains read-only/preflight-only. Passive switchover is not the first live scenario.
Restore, decommission, failure injection, and mutating scenarios are later-phase only. Arbitrary shell commands are
forbidden. Agent-invented live commands are forbidden. Decommission is disposable-lab-only unless separately designed.

Guardrail wording intentionally pinned by tests:

- passive switchover is not the first live scenario
- restore, decommission, failure injection, and mutating scenarios are later-phase only
- decommission is disposable-lab-only unless separately designed
- arbitrary shell commands are forbidden
- Agent-invented live commands are forbidden

Current catalog IDs that must remain explicitly classified before live support can exist:

- `static-gates`
- `lab-readiness`
- `baseline-check`
- `preflight`
- `python-passive-switchover`
- `ansible-passive-switchover`
- `python-restore-only`
- `ansible-restore-only`
- `argocd-managed-switchover`
- `runtime-parity`
- `final-baseline-check`
- `bash-discovery`
- `bash-postflight`
- `full-restore`
- `checkpoint-resume`
- `decommission`
- `rbac-bootstrap`
- `rbac-bootstrap-live`
- `failure-injection`
- `soak`

Read-only/preflight candidates for a later audited phase:

- `preflight`
- `lab-readiness`
- `baseline-check`
- `final-baseline-check`

Later-phase-only or separately designed scenarios:

- `python-passive-switchover`
- `ansible-passive-switchover`
- `python-restore-only`
- `ansible-restore-only`
- `argocd-managed-switchover`
- `full-restore`
- `checkpoint-resume`
- `decommission`
- `rbac-bootstrap`
- `rbac-bootstrap-live`
- `failure-injection`
- `soak`

`static-gates` and `runtime-parity` remain supporting non-live or artifact-analysis concepts unless a later audited
phase explicitly ties them to live evidence collected by the controller.

## Sanitized Illustrative Example

This example is fake and sanitized. It is not a real config and must not be consumed by current code.

```yaml
schema_version: "design.phase8b"
lab_id: "redacted-lab"
plan_id: "read-only-preflight-design"
physical_hubs:
  - physical_label: "hub-a"
    context_ref: "<runtime-only-context-ref>"
    kubeconfig_ref: "<runtime-only-kubeconfig-ref>"
    expected_identity_fingerprint: "<redacted-identity-fingerprint>"
    expected_api_fingerprint: "<redacted-api-fingerprint>"
    expected_cluster_version: "optional-redacted-version"
    acm_hub_evidence_requirements:
      - "managed-cluster-inventory"
      - "backup-restore-evidence"
  - physical_label: "hub-b"
    context_ref: "<runtime-only-context-ref>"
    kubeconfig_ref: "<runtime-only-kubeconfig-ref>"
    expected_identity_fingerprint: "<redacted-identity-fingerprint>"
    expected_api_fingerprint: "<redacted-api-fingerprint>"
managed_clusters:
  expected_names: ["mc-1", "mc-2", "mc-3"]
  exact_match_required: true
  unexpected_cluster_policy: "block"
  cluster_identity_fingerprints: ["<redacted-managed-cluster-fingerprint>"]
approval:
  operator_confirmed_live_mode: false
  mutation_allowed: false
  mutation_confirmation_required: true
  approved_scenarios: ["preflight"]
  approval_timestamp: "optional-redacted-timestamp"
  approver_reference: "<operator-provided-approval-ref>"
credentials:
  runtime_only: true
  persist_to_artifacts: false
  inherit_environment: false
  allowed_env_vars: ["<explicit-runtime-env-name>"]
  forbidden_env_patterns: ["credential-like-values", "raw-kubeconfig-values"]
identity_expectations:
  hub_identity_fingerprints: ["<redacted-identity-fingerprint>"]
  api_identity_fingerprints: ["<redacted-api-fingerprint>"]
  mismatch_policy: "block"
role_discovery:
  required_evidence: ["managed-cluster-inventory", "backup-restore-evidence"]
  active_role_policy: "exactly-one-primary"
  ambiguity_policy: "block"
  fresh_discovery_required: true
rbac_prerequisites:
  read_only_checks_required: true
  mutation_checks_required: false
  prerequisite_health_checks: ["acm-health", "backup-restore-health"]
scenario_allowlist:
  approved_scenarios: ["preflight"]
  allowlist_version: "design-only"
  unknown_scenario_policy: "block"
artifact_policy:
  artifact_dir: "<caller-provided-artifact-dir>"
  default_release_output: false
  commit_live_artifacts: false
  redaction_required: true
  stdout_stderr_sanitization_required: true
redaction_policy:
  reject_raw_api_urls: true
  reject_private_ids: true
  fingerprint_identity_values: true
  reject_credential_values: true
execution_policy:
  live_execution_enabled: false
  read_only_discovery_enabled: false
  mutation_enabled: false
  automatic_recovery_enabled: false
  live_certification_evidence_enabled: false
```

## Runtime-Only Values That Must Never Be Artifact-Facing

- Raw kubeconfig references.
- Raw context references.
- Credential handles.
- Environment values.
- Temporary profile paths when they reveal runtime locations.
- Raw API identities or endpoint-like values.
- Private lab identifiers that are not already redacted or fingerprinted.

If a future implementation cannot prove a value is artifact-safe, it must omit, redact, fingerprint, or reject it.

## Phase 8C Requirements

Phase 8C must implement the external live lab config model without execution:

- pure config dataclasses or schema-model primitives only
- no file loading of real live config
- no YAML parser for real runtime config
- no kubeconfig reading
- no environment reading
- no live discovery
- no live release adapter execution
- no generated live profiles committed to Git
- tests for runtime-only versus artifact-safe field classification
- tests for L0-L10 gate names and fail-closed defaults
- tests that sanitized examples remain free of real live values

## Explicitly Unsupported

The following remain unsupported:

- live execution
- live config loading
- live discovery
- live ACM certification through the lab role controller
- live release-framework or adapter execution through the controller
- automatic live recovery
- Agent live behavior
- arbitrary shell execution
- committed live config files
- committed generated live profiles
- committed live artifacts
- default `.release` output
- production JSON schema finalization

Recommendation: READY_FOR_PHASE_8C_EXTERNAL_LIVE_CONFIG_MODEL
