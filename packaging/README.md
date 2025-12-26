# ACM Switchover Packaging

This directory contains packaging artifacts for distributing ACM Switchover across multiple formats.

## Directory Structure

```
packaging/
├── common/         # Shared metadata, version sync tooling
│   ├── VERSION           # Authoritative version number (1.5.0)
│   ├── VERSION_DATE      # Version release date (2025-12-22)
│   ├── version-bump.sh   # Update version across all sources
│   ├── validate-versions.sh  # CI validation script
│   └── man/              # Man page sources (Markdown → troff)
├── python/         # Python packaging docs
├── rpm/            # RPM spec and COPR docs
├── deb/            # Debian packaging (control, rules, changelog)
├── container/      # Container docs and helpers
└── helm/           # Full application Helm chart
    └── acm-switchover/
```

## Supported Package Formats

| Format | Location | Description |
|--------|----------|-------------|
| Container | `container-bootstrap/Containerfile` | Multi-arch OCI image (amd64, arm64) |
| Python (pip) | Root `pyproject.toml` | pip-installable with console_scripts |
| RPM | `packaging/rpm/` | Fedora/RHEL/CentOS packages |
| DEB | `packaging/deb/` | Debian/Ubuntu packages |
| Helm | `packaging/helm/acm-switchover/` | Full Kubernetes deployment (includes RBAC) |

> Note: The legacy standalone RBAC chart at `deploy/helm/acm-switchover-rbac` is deprecated. Use the packaging chart with `rbac.create=true` (default) instead.

Helm compatibility: chart v2 tested on Helm 3.14 and Helm 4.0.

## Version Management

### Single Source of Truth

The authoritative version is stored in `packaging/common/VERSION`. All other version locations are synchronized from this source.

### Version Locations

| File | Variable/Field |
|------|---------------|
| `packaging/common/VERSION` | Plain text version number |
| `packaging/common/VERSION_DATE` | Release date (YYYY-MM-DD) |
| `lib/__init__.py` | `__version__`, `__version_date__` |
| `scripts/constants.sh` | `SCRIPT_VERSION`, `SCRIPT_VERSION_DATE` |
| `container-bootstrap/Containerfile` | Label `version` |
| `packaging/helm/acm-switchover/Chart.yaml` | `version`, `appVersion` |

### Bumping Versions

```bash
# Bump to a new version
./packaging/common/version-bump.sh 1.5.0

# Validate all versions are in sync
./packaging/common/validate-versions.sh
```

## Branch Strategy

- **`main` branch**: Bugfixes only, version 1.4.x
- **`packaging` branch**: Packaging work, version 1.5.x
- **Rebase cadence**: Weekly rebase of `packaging` onto `main`

## State Directory Defaults

Different installation methods configure different default state directories:

| Install Method | Default `ACM_SWITCHOVER_STATE_DIR` |
|----------------|-----------------------------------|
| Git clone / pip install | `.state/` (relative to CWD) |
| RPM / DEB packages | `/var/lib/acm-switchover` |
| Container image | `/var/lib/acm-switchover` |
| Helm chart | `/var/lib/acm-switchover` (PVC mount) |

The `--state-file` CLI flag always takes precedence over environment defaults.

## Man Pages

Man pages are written in Markdown and converted to troff format using `pandoc`.

```bash
# Build man pages (requires pandoc)
make -C packaging/common/man

# Install pandoc (if needed)
# Fedora/RHEL: dnf install pandoc
# Debian/Ubuntu: apt install pandoc
# macOS: brew install pandoc
```

Pre-generated `.1.gz` files are committed to the repository, so `pandoc` is only required when editing man page sources.

## Building Packages

```bash
# Build all package formats
./packaging/common/build-all.sh

# Test installation in clean containers
./packaging/common/test-install.sh
```

## CI/CD

The following workflows support packaging:

- `.github/workflows/version-sync.yml` - Validates version consistency
- `.github/workflows/container-build.yml` - Builds container images
- `.github/workflows/pypi-publish.yml` - Publishes to PyPI (optional)
- `.github/workflows/packaging-release.yml` - Builds all artifacts on tags
