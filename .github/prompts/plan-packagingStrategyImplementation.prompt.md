# Plan: Complete Packaging Strategy—Container, Python, RPM/COPR, DEB, and Helm

Implement the complete packaging strategy on a dedicated `packaging` branch starting at version 1.5.0, keeping `main` as the bugfix-only branch with weekly rebases.

## Branch Strategy

- **`main` branch**: Bugfixes only, version 1.4.x
- **`packaging` branch**: Packaging work, version 1.5.x
- **Rebase cadence**: Weekly rebase of `packaging` onto `main`

## Steps

### 1. Create `packaging` branch and set up directory structure

- Branch from `main`, create `packaging/common/`, `packaging/python/`, `packaging/rpm/`, `packaging/deb/`, `packaging/container/`, `packaging/helm/acm-switchover/`
- Add `packaging/README.md` documenting layout, version sync process, and weekly rebase workflow from main

### 2. Establish single version source at 1.5.0 with sync tooling

- Create `packaging/common/VERSION` (1.5.0) and `VERSION_DATE` (2025-12-22)
- Create `packaging/common/version-bump.sh` to update all locations (`lib/__init__.py`, `scripts/constants.sh`, `setup.cfg`, Containerfile labels, Chart.yaml)
- Add `packaging/common/validate-versions.sh` for CI validation
- Update all version sources to 1.5.0 on branch creation

### 3. Create Python packaging (`pyproject.toml`, `MANIFEST.in`)

- Root `pyproject.toml` with `requires-python = ">=3.9"`, dependencies from `requirements.txt`, console_scripts (`acm-switchover`, `acm-switchover-rbac`, `acm-switchover-state`)
- `MANIFEST.in` to include `scripts/`, `completions/`, `deploy/`, `docs/`, `LICENSE`, `README.md`, `packaging/common/man/*.1.gz`
- Add `--version` flag to all three CLIs using `lib.__version__`

### 4. Create man pages with pandoc build system

- Create `packaging/common/man/` with markdown sources: `acm-switchover.1.md`, `acm-switchover-rbac.1.md`, `acm-switchover-state.1.md`
- Add `packaging/common/man/Makefile` to convert `.md` → `.1` → `.1.gz` via `pandoc -s -t man`
- Pre-generate `.1.gz` files in repo so pandoc is only needed by maintainers
- Document in `packaging/README.md`: "Run `make -C packaging/common/man` after editing .md sources"

### 5. Create RPM spec and DEB packaging

- Add `packaging/rpm/acm-switchover.spec` with FHS layout, `/usr/bin/` wrapper scripts setting `ACM_SWITCHOVER_STATE_DIR=/var/lib/acm-switchover`
- Install man pages to `/usr/share/man/man1/`
- Add `packaging/deb/debian/` with control, rules, changelog, postinst
- Include COPR setup docs in `packaging/rpm/copr/README.md`

### 6. Create full Helm chart and fix container image

- Create `packaging/helm/acm-switchover/` (Job/CronJob, PVC, RBAC, ConfigMap templates) with version 1.5.0
- Fix `container-bootstrap/Containerfile`: add `check_rbac.py`, `show_state.py`, `completions/`, set `ENV ACM_SWITCHOVER_STATE_DIR=/var/lib/acm-switchover`
- Add `packaging/container/SECURITY.md` for OpenShift SCC docs

### 7. Add CI workflows and helper scripts

- Add `.github/workflows/version-sync.yml` to validate version consistency
- Create `packaging/common/build-all.sh` and `packaging/common/test-install.sh`
- Optional: `.github/workflows/pypi-publish.yml`, `.github/workflows/packaging-release.yml`

## Critical Gaps to Address

1. **Container image missing `check_rbac.py`, `show_state.py`, and `completions/`** — required for parity with repo CLIs.
2. **State persistence mismatch in container/helm** — ensure `ACM_SWITCHOVER_STATE_DIR` is set to the mounted state volume path.
3. **Version drift across repo files** — `setup.cfg` at 1.3.0, Helm chart at 1.0.0/1.2.0, Containerfile label at 1.0.0.
4. **`--version` flags missing on Python CLIs** — make deterministic and testable.

## Further Considerations

1. **setup.cfg fate** — Migrate tool configs (pytest/flake8/mypy) to `pyproject.toml` entirely, or keep `setup.cfg` for tool configs only and remove version from it?
2. **Helm chart namespace** — Create conditionally (`createNamespace: true`) or assume exists?
3. **SELinux for `/var/lib/acm-switchover/`** — Document `semanage fcontext` guidance in RPM notes.
