# Release Validation Tests

`tests/release/` contains the pytest-native release validation framework. The non-live helper tests in this tree are now run explicitly by `./run_tests.sh` and by CI. The live certification entrypoint still requires an explicit profile and is skipped without one.

The gated multi-segment live lab-controller design is documented in
[`Phase 9A — RC Hardening Re-baseline and Gated Live Lab-Controller Design`](../../docs/plans/2026-07-17-phase-9a-rc-hardening-rebaseline-and-live-controller-design.md).
Current fake clients, dry runs, static fixtures, local harnesses, and read-only transport tests are not live ACM
certification evidence. Phase 9B remains blocked until that design is merged and independently validated.

## Framework Tests

Run framework unit and contract tests directly:

```bash
python -m pytest tests/release -q
```

Without a release profile, the live certification entrypoint is skipped. This is expected.

## Live Certification

Live release certification requires an operator-provided profile and real lab access:

```bash
python -m pytest tests/release/test_release_certification.py \
  --release-profile /path/to/release-profile.yaml \
  --release-mode certification
```

Example profile templates live in `tests/release/profiles/`. Do not commit real kubeconfig paths, credentials, or private lab identifiers.

Supported operator controls are:

- `--release-profile` or `ACM_RELEASE_PROFILE`
- `--release-mode`
- `--release-scenario`
- `--release-stream`
- `--release-artifact-dir`
- `--allow-dirty`

Focused reruns are filter-based only. The current harness does not support resuming or rerunning from a previous artifact directory.

## Live RBAC Bootstrap Certification

The `rbac-bootstrap-live` scenario (module:
`tests/release/checks/rbac_certification.py`) validates that applied cluster
permissions match the Python RBAC validator's positive matrix end-to-end using
`SubjectAccessReview` against the lab clusters declared in the profile. It also
runs least-privilege deny checks so over-permissioned service accounts fail
certification instead of passing on allow checks alone.

The scenario is **opt-in**: it is skipped unless
`ACM_ENABLE_LIVE_RBAC_CERTIFICATION=1` is set, keeping ordinary release
validation safe for production environments. Select it via the example profile
`tests/release/profiles/full-release-with-rbac-cert.example.yaml`; when chosen
explicitly, live RBAC certification is treated as blocking. SAR request and
evidence artifacts are written with collision-safe paths so concurrent scenarios
do not overwrite each other.

See [Live RBAC Bootstrap Certification](../../docs/deployment/rbac-live-certification.md)
for full setup, expected artifacts, and comparison to static RBAC parity checks.

## Certification Eligibility

Real discovery and real stream adapters are necessary for the existing framework's eligibility checks, but they are
not sufficient for future Phase 9 live certification. The profile-driven entrypoint can produce live framework
evidence. A Phase 9 certification claim additionally requires the gated controller lifecycle, complete provenance and
freshness evidence, physical-identity and logical-role proof, a bound role-aware profile, one-segment mutation
authorization, and the applicable independently validated Phase 9B–9F gates. Unit tests may inject fake discovery
clients or fake adapters, but those runs are marked not certification eligible in the generated summary.

The orchestrator writes durable artifacts under the profile artifact root, including `manifest.json`, `scenario-results.json`, `runtime-parity.json`, `summary.json`, and `release-report.md`.

Dirty certification runs fail fast unless `--allow-dirty` is set. Even with `--allow-dirty`, the run remains not certification eligible. When a profile declares `release.metadata_files`, the harness also validates those files against `release.expected_version` and records the metadata hash/status in `manifest.json`.

See `docs/development/release-validation-framework.md` for the full framework contract.
