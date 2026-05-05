# Release Validation Tests

`tests/release/` contains the pytest-native release validation framework. These tests are explicit and are not run by the default `./run_tests.sh` command.

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

## Certification Eligibility

Certification-eligible runs use real discovery and real stream adapters. Unit tests may inject fake discovery clients or fake adapters, but those runs are marked not certification eligible in the generated summary.

The orchestrator writes durable artifacts under the profile artifact root, including `manifest.json`, `scenario-results.json`, `runtime-parity.json`, `summary.json`, and `release-report.md`.

See `docs/development/release-validation-framework.md` for the full framework contract.
