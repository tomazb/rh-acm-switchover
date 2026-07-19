# Release Validation Framework

## Overview

The release validation framework is a pytest-native certification path for ACM switchover releases. It is separate from ordinary unit, integration, and E2E tests because it coordinates profile-driven checks across the Python CLI, Ansible collection, and Bash surfaces, then writes a durable artifact bundle for operator review.

Release validation is intentionally explicit. `./run_tests.sh` now runs the non-live `tests/release/` helper suite as its own local lane, and CI runs the same helper suite in a dedicated `Release Framework Tests` job. The live certification entrypoint is still marked `release` and is skipped unless an operator supplies a profile with `--release-profile` or `ACM_RELEASE_PROFILE`.

The existing profile-driven entrypoint is not the future multi-segment live safety authority. The authoritative
Phase 9 trust boundary and implementation gates are defined in
[`Phase 9A — RC Hardening Re-baseline and Gated Live Lab-Controller Design`](../plans/2026-07-17-phase-9a-rc-hardening-rebaseline-and-live-controller-design.md).
Phase 9B now provides a controller-owned, typed read-only discovery entrypoint for physical identity proof. It is
disabled by default, requires a frozen controller enrollment registry supplied independently of each request plus
explicit runtime-only enrollment references and L0-L9/source/config/profile gates, performs only fixed bounded list
queries, and publishes only recursively audited non-certification artifacts. Fake, dry-run,
static-fixture, local-harness, and Phase 9B read-only-contact evidence still cannot establish live ACM certification
through the lab role controller.

The existing profile-driven runner's `OcDiscoveryClient` remains distinct from controller authority and is not used
by Phase 9B. The Phase 9B controller registry binds stable enrollment IDs and private inventory labels to safe public
hub IDs, expected physical/trust-anchor fingerprints, evidence origins, and clean source/config/profile values.
Requests may reference but cannot define or replace those entries. Caller-injected page readers are admitted only
through exact bindings tied to one registry entry and their runtime access/context object identities. The same binding
passes its validated exact PEM API trust-anchor bundle into each reader call. It repeatedly collects `kube-system` Namespace,
OpenShift Infrastructure, and ClusterVersion identity signals; combines them with a versioned trust-anchor
fingerprint; enforces controller-measured request/collection deadlines, complete pagination, source revision, evidence
origin, completion-time freshness, and skew; and derives stable distinct SHA-256 fingerprints. It deliberately emits
no certification-authoritative, logical-role, known-state, readiness, mutation, recovery, or executable-profile
claims. Phase 9C remains blocked.

## Orchestration Flow

The live certification entrypoint calls `tests.release.orchestrator.run_release_certification`, which wires:

1. profile loading and profile-aware matrix selection
2. matrix lifecycle/support validation
3. git checkout inspection plus release metadata consistency validation
4. static gates
5. initial live discovery, lab readiness, and baseline assertions
6. Bash, Python, and Ansible stream adapters for executable scenario/stream pairs
7. runtime parity for supported normalized report data
8. final live discovery, baseline assertions, and recovery artifact state
9. `scenario-results.json`, `runtime-parity.json`, `summary.json`, and `release-report.md`

Injected fake discovery clients or fake stream adapters are accepted for unit tests only. A run using participants marked `test_only` is not certification eligible.

## Profiles

Profiles describe the lab, enabled streams, required scenarios, release metadata, recovery expectations, and artifact policy. Checked-in examples live under:

- `tests/release/profiles/dev-minimal.example.yaml`
- `tests/release/profiles/full-release.example.yaml`
- `tests/release/profiles/argocd-release.example.yaml`
- `tests/release/profiles/full-release-with-rbac-cert.example.yaml` — full release matrix plus the live RBAC bootstrap certification scenario (`rbac-bootstrap-live`). The scenario is gated by `ACM_ENABLE_LIVE_RBAC_CERTIFICATION=1`; when an operator selects this profile, live RBAC certification is treated as blocking. See [Live RBAC Bootstrap Certification](../deployment/rbac-live-certification.md) for the certification flow, artifacts, and least-privilege deny checks.

Use the examples as templates for real lab profiles. Do not commit real kubeconfig paths, cluster identifiers that should stay private, or credentials. When a profile defines `release.metadata_files`, the harness validates that each listed file exists and references `release.expected_version`, then records a stable metadata hash in the manifest. `recovery.total_budget_minutes` is also copied into `recovery.json` so the emitted artifacts reflect the configured recovery budget even when no automatic recovery action runs.

The Phase 1 matrix validator is intentionally conservative. It blocks required scenario/stream pairs that are not implemented by the current adapters, records optional unsupported pairs as `not_applicable`, and rejects multi-mutation certification sequences until reset/recovery sequencing exists. The checked-in full profiles document the intended full matrix, but they are expected to produce a NO-GO matrix-validation result rather than a fully RC-ready certification pass in Phase 1.

## Invocation

Attempt the full certification matrix with an explicit profile. In Phase 1, the checked-in full profiles are expected to produce a matrix-validation NO-GO until reset/recovery sequencing is implemented:

```bash
python -m pytest tests/release/test_release_certification.py --release-profile tests/release/profiles/full-release.example.yaml --release-mode certification
```

Run a focused-rerun for one scenario after correcting a lab issue:

```bash
python -m pytest tests/release/test_release_certification.py --release-profile tests/release/profiles/full-release.example.yaml --release-mode focused-rerun --release-scenario preflight
```

Run debug mode while developing the framework or investigating local behavior:

```bash
python -m pytest tests/release/test_release_certification.py --release-profile tests/release/profiles/dev-minimal.example.yaml --release-mode debug --allow-dirty
```

You can also filter by stream with `--release-stream python`, `--release-stream ansible`, or `--release-stream bash`. When no mode is supplied, the framework defaults to `certification` for unfiltered runs and `focused-rerun` when scenario or stream filters are present.

Profile scenario declarations define the default matrix for a run. CLI scenario and stream filters narrow that profile-declared set, while mutating scenario filters automatically add prerequisites and final checks. Focused reruns are currently filter-based only; the harness does not support resuming or rerunning from a previous artifact directory.

A focused rerun may select one mutating scenario plus its automatic prerequisites and final checks. Because focused reruns are not `certification` mode, they remain non-certification-eligible even when all selected checks pass.

## Known-State Lab Control

This section documents the intended direction for live lab-mutating certification; it is not a complete
implementation spec. Phase 9B physical identity collection now exists, but the logical-role and mutating lifecycle
described below remains Phase 9C and later work.

Terminology:

- **Physical hub**: a stable cluster identity, for example `hub-a` or `hub-b`.
- **Logical role**: the current `primary` or current `secondary` role assigned to a physical hub.
- **Desired state**: the physical-hub-to-logical-role assignment required before a scenario starts.
- **Observed state**: the role assignment discovered from live cluster evidence.
- **Known-state segment**: one release-validation segment that starts from a proven lab state.
- **Recovery-required state**: a state where the harness cannot safely prove the next starting state.

“A release certification run is not a single linear script over a static primary/secondary profile. It is a
sequence of known-state segments. Each segment starts with live discovery, proves the
physical-hub-to-logical-role mapping, executes at most one lab-mutating scenario, verifies the expected final
state, and either hands a proven state to the next segment or stops with a recovery-required NO-GO.”

Static primary/secondary profiles are insufficient for full certification across multiple lab-mutating
scenarios because successful mutations may change logical hub roles. A successful passive switchover normally
makes the old secondary physical hub the active primary. Failed or partially completed scenarios can leave the
lab in an unknown or unsafe intermediate state. Therefore, full multi-mutation certification must remain
blocked or produce a NO-GO decision until reset/recovery sequencing can prove each next starting state.

The lab role controller under `tests/release/lab_controller/` is the intended mechanism for making multi-mutation
certification safe. Its Phase 1-8 implementation remains deterministic and non-live for known-state sequencing,
profile generation, provisional artifacts, request materialization, and the local harness. Phase 9B supports only
explicitly gated live read-only physical identity discovery. Logical-role mapping and all mutating lifecycle execution
remain unsupported and fail-closed pending Phase 9C and later slices.

The static release-lab Kustomize fixtures under `tests/release/kustomize/` model the intended two-hub,
three-managed-SNO topology and Argo CD ACM-object ownership interference modes for non-live validation only. These
fixtures are not live ACM certification evidence, are not applied by the release framework, and intentionally rely on
static YAML/Kustomize checks because server-side live validation is Phase 9 work. Phase 8P/8Q wires these fixtures into
controller-local GitOps ownership evidence, Argo CD interference classification, capability evidence for
`spec.syncPolicy.automated.enabled`, coordination-strategy modeling, and provisional dry-run/materialized artifact
summaries. The controller still performs no live CRD/schema detection and does not change production switchover runtime
behavior.

For live certification, the controller's responsibilities are to:

- discover physical hub identities before mutation
- map physical hubs to current logical roles
- verify that the lab is in the required initial state for the scenario
- generate or select a role-aware release profile
- run at most one lab-mutating scenario per known-state segment
- verify the expected final role state
- record role transitions and recovery decisions in release artifacts
- refuse to continue when active hub, passive hub, managed-cluster set, Argo CD state, RBAC state, or restore
  evidence cannot be proven

The intended hierarchy is a Python lab role controller as the authoritative implementation, with Agent skills
or Agent instructions as optional orchestration conveniences. The controller owns truth and safety; the Agent
owns orchestration convenience and explanation. An Agent may invoke deterministic release tooling and summarize
artifacts, but it must not improvise live-cluster mutations or override controller GO/NO-GO decisions.

Focused reruns remain useful for diagnostics or for gathering single-scenario evidence after an operator has
corrected a lab issue. They are not full multi-mutation certification unless they are tied into known-state
sequencing.

## Artifacts

Each run writes a timestamped artifact directory under the profile's artifact root, unless overridden with `--release-artifact-dir`. Required outputs are:

- `manifest.json` records run identity, profile data, matrix hash and validation status, git checkout state, release metadata status/hash, command context, and eligibility state.
- `scenario-results.json` records scenario outcomes plus matrix validation issues.
- `runtime-parity.json` records normalized cross-stream parity comparisons.
- `recovery.json` records the configured recovery budget and any hard-stop state observed by the harness.
- `redaction.json` records artifact scanning, redaction counts, and rejected outputs from the shared artifact writer used by stream adapters and static gates.
- `summary.json` records final fail-closed status and failure reasons.
- `release-report.md` renders the operator-readable release validation report.

The final `release-report.md` includes run identity, release metadata consistency, matrix validation, required and optional scenario results, mandatory Argo CD certification, runtime parity, recovery, artifact redaction, final baseline status, and the final GO/NO-GO decision.

Future Phase 9 preparation output uses the separate allowlisted `LAB_PREPARATION_ONLY` evidence class and artifact
namespace. It is always non-certification-eligible, cannot be relabeled or merged into passing scenario results, and
may be referenced only as provenance before fresh controller discovery and authorization.

Phase 9B live read-only output uses a separate `live_read_only` purpose. Every artifact records the schema, writer,
and controller revisions; clean source revision; config/profile hashes; immutable controller-registry hash; collection
timestamps; freshness/skew result; evidence-origin fingerprints; per-query pagination completeness; physical identity
fingerprints; allowlisted call trace; and recursive redaction audit. The controller forces certification eligibility,
live-certification evidence, and mutation attempted to false. Raw enrollment IDs, private inventory labels, evidence
origins, certificates, credentials, paths, and API locations are excluded. Redaction failure prevents publication.

## Safety Notes

Certification runs should start from a clean checkout. In certification mode, a dirty checkout fails fast unless the operator passes `--allow-dirty`. Using `--allow-dirty` permits diagnostic execution, but the run remains not certification eligible.

Profiles are mandatory for release-marked tests. This prevents accidental live-cluster execution against implicit local contexts.

Do not update protected operational runbook or `.claude/skills/` files as part of release validation framework work. Those files require explicit operator approval and separate review.

Artifact redaction is fail-closed. If an artifact contains content that cannot be safely sanitized, the write is rejected and recorded in `redaction.json`; unresolved redaction failures must block certification.

Release metadata validation is also fail-closed. If a configured metadata file is missing or no longer references the profile's `release.expected_version`, the harness records a failed release metadata gate and the run cannot produce a passing certification summary.

Live discovery is fail-closed as well. Missing `oc`, authentication failures, command errors, or malformed discovery payloads now surface as explicit release-run failures instead of silently degrading into empty hub fingerprints.
