# Packaging Strategy Implementation (Option A)

## Problem Statement
Packaging groundwork exists but critical gaps block a reliable distribution story: the Helm chart in `packaging/helm/acm-switchover` still lacks the RBAC and ConfigMap resources promised in the docs, the repo keeps a duplicate RBAC-only chart under `deploy/helm`, generated man page artifacts are missing even though RPM/DEB specs expect `*.1.gz`, Debian metadata is stale, and CI workflows referenced in documentation (`pypi-publish.yml`, `packaging-release.yml`) do not exist. These mismatches will cause packaging builds to fail and keep downstream users confused unless we reconcile the implementation with the documented strategy.

## Current State
- Packaging directories and helper scripts exist, but `packaging/common/man/` only holds Markdown sources—no pre-generated `.1`/`.1.gz` assets—so `rpmbuild`, `dpkg-buildpackage`, and Python sdists fail when they look for compressed man pages.
- Debian packaging metadata can drift from the canonical `VERSION_DATE` (2025-12-22); keep `debian/changelog` aligned (including weekday) so lintian won’t reject builds.
- The shipping Helm chart only templatizes the Job/CronJob/PVC pieces; `values.yaml` fields for `namespace.*` and `rbac.*` are unused, and RBAC resources remain in the separate `deploy/helm/acm-switchover-rbac` chart despite docs (CHANGELOG, AGENTS, packaging README) claiming the app chart already bundles them.
- There is no template to automate the `import-controller-config` ConfigMap described in the runbook for auto-import strategy changes.
- `.github/workflows` lacks the documented `pypi-publish.yml` and `packaging-release.yml`, so publishing flows cannot run.
- `packaging/common/version-bump.sh` / `validate-versions.sh` still try to sync the deprecated RBAC chart.
- Helm 4.0 (released 12 Nov 2025) is now mainstream, so the updated chart must be tested against both Helm 3.x LTS and Helm 4 to avoid regressions.

## Proposed Changes
### 1. Unify Helm packaging around Option A
- Migrate the RBAC templates (namespace, service accounts for operator/validator, cluster roles, roles, clusterrolebindings, rolebindings) from `deploy/helm/acm-switchover-rbac` into `packaging/helm/acm-switchover/templates/` and wire them to the existing `rbac.*` values plus `namespace.create`.
- Extend values to support:
  - distinct operator vs validator service-account names/annotations;
  - namespace maps for backup/observability/acm/mce plus user-supplied extras;
  - conditionally including decommission- and observability-specific permissions;
  - supplying custom RBAC rules (carry over `customOperatorRules`, `customValidatorRules`, `customNamespaces`).
- Teach the Job/CronJob templates to pick the operator SA by default and allow the CronJob to switch to the validator SA automatically when running in dry-run/validation mode, while permitting "use existing SA" overrides.
- Add a `namespace.yaml` template governed by `namespace.create` so users who do not pass `--create-namespace` still get the NS when desired.
- Update helper `_helpers.tpl`/notes to ensure new resources share consistent labels/selectors.
- Replace `deploy/helm/acm-switchover-rbac` with a deprecation notice (README + optional Chart.yaml stub) pointing to the packaging chart, and update `packaging/common/version-bump.sh` + `validate-versions.sh` to stop touching the old Chart.
- Document migration guidance (values changes, how to disable RBAC creation when bringing your own SAs) in `packaging/helm/acm-switchover/README.md`, `packaging/README.md`, `docs/deployment/rbac-deployment.md`, `docs/development/rbac-implementation.md`, `docs/ACM_SWITCHOVER_RUNBOOK.md`, `deploy/acm-policies/README.md`, and `CHANGELOG.md` (Unreleased).
- Add a CI smoke test (`helm lint` or `helm template`) in the forthcoming packaging workflow to catch template regressions on Helm 3/4.

### 2. Model the import-controller ConfigMap in the chart
- Add a template (disabled by default) that can create/update the `import-controller-config` ConfigMap in `multicluster-engine` with a configurable `autoImportStrategy` so that users who rely on the Helm deployment can declaratively set `ImportAndSync` before activation.
- Surface values such as `autoImportStrategy.enabled`, `mode`, and `cleanupOnDelete`, and document how the chart leaves the ConfigMap untouched when disabled.

### 3. Commit generated man page artifacts
- Run `make -C packaging/common/man` to produce `.1` and `.1.gz` for each CLI and commit at least the compressed variants that RPM/DEB and MANIFEST expect.
- Update `packaging/README.md` (and, if helpful, add a short `README` under `packaging/common/man/`) to remind contributors to rerun the make target whenever they edit the Markdown sources.
- Consider adding a guard (e.g., `git diff --exit-code -- packaging/common/man/*.1.gz`) to the packaging-release workflow so CI fails if the artifacts fall out of sync again.

### 4. Fix Debian changelog metadata
- Update `packaging/deb/debian/changelog` to use the canonical release date (`Mon, 22 Dec 2025 12:00:00 +0000`), ensuring the entry matches `VERSION_DATE` and lintian won’t reject the package.

### 5. Restore the documented CI workflows
- Add `.github/workflows/pypi-publish.yml` that:
  - triggers on version tags (and manual dispatch),
  - builds sdist/wheel via `python -m build`,
  - publishes to PyPI using trusted publishing (`pypa/gh-action-pypi-publish`) with `id-token: write`,
  - optionally checks that `packaging/common/VERSION` matches the tag before publishing.
- Add `.github/workflows/packaging-release.yml` that:
  - triggers on tags/releases,
  - runs sanity tests (`pytest`, `./packaging/common/validate-versions.sh`, `helm lint`),
  - runs `./packaging/common/build-all.sh --python --helm` (and optionally gated RPM/DEB builds where tooling is available),
  - uploads the resulting wheels/sdists/helm tgz/man pages as artifacts and, when invoked on a release, attaches them to the GitHub Release.
- Document the new workflows (and required secrets/permissions) in `packaging/README.md`, `packaging/python/README.md`, and `docs/development/ci.md` so contributors know how publishing is automated.

### 6. Tooling and documentation cleanup
- Update `AGENTS.md`, `packaging/README.md`, `CHANGELOG.md`, and any other references so they only mention the packaging Helm chart (Option A) and clarify how RBAC is delivered now.
- Ensure `MANIFEST.in` still matches the committed man-page artifacts; adjust if the `.1` intermediates are excluded.
- Add an `[Unreleased]` changelog entry describing the Option A migration, man-page fix, Debian metadata fix, and CI workflow additions.
- Call out in docs that Python still targets 3.9+ (aligns with RHEL 9 toolchain) but note that upstream 3.9 entered security-fixes-only mode in late 2025 so future releases may bump the floor.

## Risks / Open Questions
- Need to communicate the RBAC chart deprecation clearly so existing automation (e.g., GitOps overlays pointing at `deploy/helm/acm-switchover-rbac`) can migrate—consider keeping a shim Chart that depends on the new chart for one release cycle.
- Helm 4 introduces minor linting/validation differences; we must ensure testing covers both Helm 3 (still common on clusters) and Helm 4 to avoid breaking users on older toolchains.
- Building RPM/DEB inside GitHub-hosted runners can be flaky; the packaging-release workflow may need conditional steps or containerized builders to keep runtimes manageable.
- The optional ConfigMap template must not fight with the Python CLI’s runtime changes; we need to document that Helm-managed ConfigMaps should be disabled if the CLI already toggles auto-import during execution.
