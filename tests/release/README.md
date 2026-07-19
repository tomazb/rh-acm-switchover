# Release Validation Tests

`tests/release/` contains the pytest-native release validation framework. The non-live helper tests in this tree are now run explicitly by `./run_tests.sh` and by CI. The live certification entrypoint still requires an explicit profile and is skipped without one.

The gated multi-segment live lab-controller design is documented in
[`Phase 9A — RC Hardening Re-baseline and Gated Live Lab-Controller Design`](../../docs/plans/2026-07-17-phase-9a-rc-hardening-rebaseline-and-live-controller-design.md).
Current lab-controller fake/injected clients, dry runs, static fixtures, local harnesses, and read-only transport
tests are not live ACM certification evidence. The existing profile-driven release runner has a live-capable
`OcDiscoveryClient`, but it is not the Phase 9 controller authority. Phase 9B now provides
`tests.release.lab_controller.run_phase9b_live_discovery`, a disabled-by-default controller entrypoint over explicit
runtime-only typed read APIs. It proves physical identities only and always emits non-certification, non-mutation
artifacts. Deterministic fake API coverage is not live evidence; the Phase 9B live exit gate remains blocked until an
operator authorizes a real two-hub read-only run. Phase 9C remains blocked.

## Phase 9B Read-Only Physical Identity

The Phase 9B entrypoint requires two explicit runtime handles, a clean bound source revision, config/profile hashes,
operator opt-in, and all L0-L9 gates. It inherits no default kubeconfig, context, endpoint, environment credential, or
client factory. An exact controller-owned binding object ties each injected page reader to one public hub ID and passes
the corresponding runtime access object, context object, and exact PEM API trust-anchor bundle into every reader call.
Admission uses side-effect-free static lookup and requires a callable reader before contact. The binding must explicitly
select the typed request-timeout contract; its adapter must enforce every `TypedReadRequest.timeout_seconds` value in
the underlying API call, while the controller independently measures and enforces the request and collection deadlines.

The controller performs only fixed bounded list queries for the `kube-system` Namespace, OpenShift
`Infrastructure/cluster`, and OpenShift `ClusterVersion/version`. It collects each hub twice, requires complete
pagination and consistent collection resource versions, recomputes freshness at collection completion, and validates
skew/source/origin. Its four-signal physical identity includes a versioned SHA-256 canonicalization of the validated
API trust-anchor bundle used by the same connection; raw certificates are never published. Stable, distinct physical
fingerprints and separate trust-anchor fingerprints must match immutable controller enrollment bound to the clean
source revision and config/profile hashes. Missing, malformed, changed, mismatched, or tampered trust/enrollment data
blocks before contact. Call traces contain only safe hub IDs, fixed query IDs, list verbs, page ordinals, completeness,
and `mutation_attempted=false`.

Artifacts force:

- `purpose: live_read_only`
- `certification_eligible: false`
- `live_certification_evidence: false`
- `mutation_attempted: false`

Publication is all-or-nothing after a recursive key/value/type audit. Raw UIDs, infrastructure names, credentials,
paths, contexts, endpoints, runtime handles, exception text, and arbitrary object representations are never
published. Phase 9B does not infer logical primary/secondary roles, known state, readiness, mutation/recovery
authority, or executable profiles.

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

Future lab preparation artifacts use the separate `LAB_PREPARATION_ONLY` evidence class and namespace. They remain
non-certification-eligible, cannot be relabeled or merged into passing scenario results, and require fresh controller
discovery and authorization before any certification segment.

The orchestrator writes durable artifacts under the profile artifact root, including `manifest.json`, `scenario-results.json`, `runtime-parity.json`, `summary.json`, and `release-report.md`.

Dirty certification runs fail fast unless `--allow-dirty` is set. Even with `--allow-dirty`, the run remains not certification eligible. When a profile declares `release.metadata_files`, the harness also validates those files against `release.expected_version` and records the metadata hash/status in `manifest.json`.

See `docs/development/release-validation-framework.md` for the full framework contract.
