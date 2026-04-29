# Release Validation Framework

## Overview

The release validation framework is a pytest-native certification path for ACM switchover releases. It is separate from ordinary unit, integration, and E2E tests because it coordinates profile-driven checks across the Python CLI, Ansible collection, and Bash surfaces, then writes a durable artifact bundle for operator review.

Release validation is intentionally explicit. Tests marked `release` are skipped unless an operator supplies a profile with `--release-profile` or `ACM_RELEASE_PROFILE`.

## Profiles

Profiles describe the lab, enabled streams, required scenarios, release metadata, recovery expectations, and artifact policy. Checked-in examples live under:

- `tests/release/profiles/dev-minimal.example.yaml`
- `tests/release/profiles/full-release.example.yaml`
- `tests/release/profiles/argocd-release.example.yaml`

Use the examples as templates for real lab profiles. Do not commit real kubeconfig paths, cluster identifiers that should stay private, or credentials.

## Invocation

Run a full certification pass with an explicit profile:

```bash
python -m pytest tests/release -m release --release-profile tests/release/profiles/full-release.example.yaml --release-mode certification
```

Run a focused-rerun for one scenario after correcting a lab issue:

```bash
python -m pytest tests/release -m release --release-profile tests/release/profiles/full-release.example.yaml --release-mode focused-rerun --release-scenario preflight
```

Run debug mode while developing the framework or investigating local behavior:

```bash
python -m pytest tests/release -m release --release-profile tests/release/profiles/dev-minimal.example.yaml --release-mode debug --allow-dirty
```

You can also filter by stream with `--release-stream python`, `--release-stream ansible`, or `--release-stream bash`. When no mode is supplied, the framework defaults to `certification` for unfiltered runs and `focused-rerun` when scenario or stream filters are present.

## Artifacts

Each run writes a timestamped artifact directory under the profile's artifact root, unless overridden with `--release-artifact-dir`. Required outputs are:

- `manifest.json` records run identity, profile data, command context, and eligibility state.
- `scenario-results.json` records scenario outcomes.
- `runtime-parity.json` records normalized cross-stream parity comparisons.
- `recovery.json` records recovery budget and hard-stop state.
- `redaction.json` records artifact scanning, redaction counts, and rejected outputs.
- `summary.json` records final fail-closed status and failure reasons.
- `release-report.md` renders the operator-readable release validation report.

The final `release-report.md` includes run identity, release metadata consistency, required and optional scenario results, mandatory Argo CD certification, runtime parity, recovery, artifact redaction, final baseline status, and the final GO/NO-GO decision.

## Safety Notes

Certification runs should start from a clean checkout. Dirty checkouts are not certification eligible unless a debug workflow explicitly passes `--allow-dirty`.

Profiles are mandatory for release-marked tests. This prevents accidental live-cluster execution against implicit local contexts.

Do not update protected operational runbook or `.claude/skills/` files as part of release validation framework work. Those files require explicit operator approval and separate review.

Artifact redaction is fail-closed. If an artifact contains content that cannot be safely sanitized, the write is rejected and recorded in `redaction.json`; unresolved redaction failures must block certification.
