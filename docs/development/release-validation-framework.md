# Release Validation Framework

## Overview

The release validation framework is a pytest-native certification path for ACM switchover releases. It is separate from ordinary unit, integration, and E2E tests because it coordinates profile-driven checks across the Python CLI, Ansible collection, and Bash surfaces, then writes a durable artifact bundle for operator review.

Release validation is intentionally explicit. `./run_tests.sh` now runs the non-live `tests/release/` helper suite as its own local lane, and CI runs the same helper suite in a dedicated `Release Framework Tests` job. The live certification entrypoint is still marked `release` and is skipped unless an operator supplies a profile with `--release-profile` or `ACM_RELEASE_PROFILE`.

## Orchestration Flow

The live certification entrypoint calls `tests.release.orchestrator.run_release_certification`, which wires:

1. profile loading and profile-aware matrix selection
2. git checkout inspection plus release metadata consistency validation
3. static gates
4. initial live discovery, lab readiness, and baseline assertions
5. Bash, Python, and Ansible stream adapters
6. runtime parity for supported normalized report data
7. final live discovery, baseline assertions, and recovery artifact state
8. `scenario-results.json`, `runtime-parity.json`, `summary.json`, and `release-report.md`

Injected fake discovery clients or fake stream adapters are accepted for unit tests only. A run using participants marked `test_only` is not certification eligible.

## Profiles

Profiles describe the lab, enabled streams, required scenarios, release metadata, recovery expectations, and artifact policy. Checked-in examples live under:

- `tests/release/profiles/dev-minimal.example.yaml`
- `tests/release/profiles/full-release.example.yaml`
- `tests/release/profiles/argocd-release.example.yaml`
- `tests/release/profiles/full-release-with-rbac-cert.example.yaml` — full release matrix plus the live RBAC bootstrap certification scenario (`rbac-bootstrap-live`). The scenario is gated by `ACM_ENABLE_LIVE_RBAC_CERTIFICATION=1`; when an operator selects this profile, live RBAC certification is treated as blocking. See [Live RBAC Bootstrap Certification](../deployment/rbac-live-certification.md) for the certification flow, artifacts, and least-privilege deny checks.

Use the examples as templates for real lab profiles. Do not commit real kubeconfig paths, cluster identifiers that should stay private, or credentials. When a profile defines `release.metadata_files`, the harness validates that each listed file exists and references `release.expected_version`, then records a stable metadata hash in the manifest. `recovery.total_budget_minutes` is also copied into `recovery.json` so the emitted artifacts reflect the configured recovery budget even when no automatic recovery action runs.

## Invocation

Run a full certification pass with an explicit profile:

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

## Artifacts

Each run writes a timestamped artifact directory under the profile's artifact root, unless overridden with `--release-artifact-dir`. Required outputs are:

- `manifest.json` records run identity, profile data, git checkout state, release metadata status/hash, command context, and eligibility state.
- `scenario-results.json` records scenario outcomes.
- `runtime-parity.json` records normalized cross-stream parity comparisons.
- `recovery.json` records the configured recovery budget and any hard-stop state observed by the harness.
- `redaction.json` records artifact scanning, redaction counts, and rejected outputs from the shared artifact writer used by stream adapters and static gates.
- `summary.json` records final fail-closed status and failure reasons.
- `release-report.md` renders the operator-readable release validation report.

The final `release-report.md` includes run identity, release metadata consistency, required and optional scenario results, mandatory Argo CD certification, runtime parity, recovery, artifact redaction, final baseline status, and the final GO/NO-GO decision.

## Safety Notes

Certification runs should start from a clean checkout. In certification mode, a dirty checkout fails fast unless the operator passes `--allow-dirty`. Using `--allow-dirty` permits diagnostic execution, but the run remains not certification eligible.

Profiles are mandatory for release-marked tests. This prevents accidental live-cluster execution against implicit local contexts.

Do not update protected operational runbook or `.claude/skills/` files as part of release validation framework work. Those files require explicit operator approval and separate review.

Artifact redaction is fail-closed. If an artifact contains content that cannot be safely sanitized, the write is rejected and recorded in `redaction.json`; unresolved redaction failures must block certification.

Release metadata validation is also fail-closed. If a configured metadata file is missing or no longer references the profile's `release.expected_version`, the harness records a failed release metadata gate and the run cannot produce a passing certification summary.

Live discovery is fail-closed as well. Missing `oc`, authentication failures, command errors, or malformed discovery payloads now surface as explicit release-run failures instead of silently degrading into empty hub fingerprints.
