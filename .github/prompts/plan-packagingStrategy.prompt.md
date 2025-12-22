# Plan: Complete Packaging Strategy—Container, Python, RPM/COPR, DEB, and Helm

A production-ready packaging strategy for rh-acm-switchover supporting:
- Container image (non-root, OpenShift-safe)
- Python packaging (pip-installable; PyPI publishing optional)
- RPM + COPR
- Debian/Ubuntu (.deb)
- Kubernetes/OpenShift deployment via Helm

This plan is aligned with the current repo state (v1.4.0, 2025-12-22) including improved state-file defaults and atomic state persistence.

## Current repo facts (baseline)

- **Python layout is intentionally flat**:
  - Top-level CLI modules: `acm_switchover.py`, `check_rbac.py`, `show_state.py`
  - Python packages: `lib/`, `modules/`

- **Version constants already exist**:
  - `lib/__init__.py`: `__version__ = "1.4.0"`, `__version_date__ = "2025-12-22"`
  - `scripts/constants.sh`: `SCRIPT_VERSION="1.4.0"`, `SCRIPT_VERSION_DATE="2025-12-22"`
  - Some repo files still contain older version strings (e.g. `setup.cfg`, `README.md`) and must be treated as **needing sync**, not canonical.

- **State file handling has been improved**:
  - Precedence order (used by `acm_switchover.py`):
    1. `--state-file <path>` if provided
    2. else `$ACM_SWITCHOVER_STATE_DIR/switchover-<primary>__<secondary>.json` when `ACM_SWITCHOVER_STATE_DIR` is set
    3. else `.state/switchover-<primary>__<secondary>.json`
  - State is written **atomically** (temp file + `os.replace`) to avoid corruption.
  - Paths are validated (safe relative paths, plus limited absolute-path allowances).

- **Container build is already wired to CI**:
  - `.github/workflows/container-build.yml` builds `container-bootstrap/Containerfile` with Docker Buildx.

## Steps

### 1. Create unified `packaging/` directory structure (`packaging/`)

Introduce `packaging/` for packaging artifacts and docs, while keeping existing repo layout stable.

- `packaging/common/` — shared metadata, version sync tooling, helper scripts
- `packaging/python/` — Python packaging docs/templates (see Step 4)
- `packaging/rpm/` — RPM spec, build helpers, COPR docs/config
- `packaging/deb/` — Debian packaging (debian/ control/rules/changelog)
- `packaging/container/` — container docs/scripts (see Step 8)
- `packaging/helm/acm-switchover/` — full application Helm chart (Job/CronJob, PVC, RBAC, ConfigMap, Secret templates)

**Note (flat layout)**: Python packages `lib/` and `modules/` plus top-level modules (`acm_switchover.py`, `check_rbac.py`, `show_state.py`) must be included by all installers.

### 2. Establish single version source + synchronization (v1.4.0 baseline)

Goal: one command to bump version/date and keep everything consistent.

- Create `packaging/common/VERSION` containing `1.4.0`.
- Create `packaging/common/VERSION_DATE` containing `2025-12-12`.
- Create `packaging/common/version-bump.sh` to update **at minimum**:
  - `packaging/common/VERSION`, `packaging/common/VERSION_DATE`
  - `lib/__init__.py` (`__version__`, `__version_date__`)
  - `scripts/constants.sh` (`SCRIPT_VERSION`, `SCRIPT_VERSION_DATE`)
  - Root `README.md` version banner
  - `setup.cfg` version field (or remove it entirely and treat `setup.cfg` as tool config only)
  - Container labels (or OCI labels) + build args
  - Helm `Chart.yaml` `version` and `appVersion`
  - RPM spec `Version:` and Debian changelog version

- Add `--version` support to the three Python CLIs:
  - `acm_switchover.py --version`
  - `check_rbac.py --version`
  - `show_state.py --version`

- Add `packaging/common/validate-versions.sh` (or python script) to validate that all version sources match (CI + pre-commit optional).

### 3. Generate man pages from Markdown source

- Create `packaging/common/man/` with:
  - `acm-switchover.1.md`
  - `acm-switchover-rbac.1.md`
  - `acm-switchover-state.1.md`

- Provide `packaging/common/man/Makefile` to convert `.md → .1` using `pandoc -s -t man` and gzip.

- Treat `pandoc` as a **system dependency** (not a pip dependency):
  - Document installation in `packaging/README.md`.
  - Optionally add a CI check that fails with a clear message when `pandoc` is missing.

### 4. Python packaging (keep current flat layout)

Yes: we can keep the current layout and still add solid Python packaging.

**Important tooling constraint:** Standard PEP 517/518 tooling expects `pyproject.toml` in the **project root** you build from (`pip install .`, `python -m build .`). So we keep `pyproject.toml` at repo root, while keeping packaging docs/templates under `packaging/python/`.

- Create root `pyproject.toml` with:
  - `requires-python = ">=3.9"`
  - Dependencies matching `requirements.txt` (`kubernetes`, `PyYAML`, `rich`, `tenacity`)
  - Dev dependencies: `pip-audit` (replaced Safety CLI in v1.3.3 for vulnerability scanning)
  - Console scripts:
    - `acm-switchover = "acm_switchover:main"`
    - `acm-switchover-rbac = "check_rbac:main"`
    - `acm-switchover-state = "show_state:main"`

- Configure setuptools to package the flat layout:
  - `py-modules`: `acm_switchover`, `check_rbac`, `show_state`
  - `packages`: include `lib` and `modules` (and subpackages)

- Version wiring:
  - Prefer reading version from `packaging/common/VERSION` (and syncing `lib/__init__.py` from it) or from `lib.__version__`.
  - Ensure the packaging release process updates **all** version sources.

- Put guidance in `packaging/python/README.md` explaining:
  - Why we keep the flat layout
  - The trade-off that installed import packages remain `lib` and `modules`
  - If public PyPI publishing is desired, a future refactor to a namespaced package (`acm_switchover/...`) may be warranted

- Use root `MANIFEST.in` (sdist uses root-level manifests) to include needed non-code assets.

- Add optional `.github/workflows/pypi-publish.yml` for tag-based publishing (Trusted Publishing / OIDC) if/when public PyPI is desired.

### 5. RPM spec file and COPR integration

- Add `packaging/rpm/acm-switchover.spec`:
  - Version: from `packaging/common/VERSION` (or git tag)
  - Requires: Python 3.9+, plus python dependencies (distro equivalents)

- Install layout (FHS):
  - `/usr/bin/` — launchers (`acm-switchover`, `acm-switchover-rbac`, `acm-switchover-state`)
  - `/usr/libexec/acm-switchover/` — helper scripts (include `quick-start.sh` here)
  - `/etc/acm-switchover/` — config (optional)
  - `/var/lib/acm-switchover/` — state directory (owned appropriately)
  - `/usr/share/doc/acm-switchover/` — docs
  - `/usr/share/man/man1/` — man pages
  - `/usr/share/bash-completion/completions/` — bash completions
  - `/usr/share/acm-switchover/deploy/` — RBAC manifests, kustomize, ACM policies

- State directory defaults for packaged installs (RPM/DEB):
  - Ship explicit `/usr/bin` wrappers that set a default **only if not already set**:
    - `/usr/bin/acm-switchover`
    - `/usr/bin/acm-switchover-rbac`
    - `/usr/bin/acm-switchover-state`
  - Wrapper behavior:
    - If `ACM_SWITCHOVER_STATE_DIR` is unset/empty, export `ACM_SWITCHOVER_STATE_DIR=/var/lib/acm-switchover`.
    - Then exec the Python module entrypoint (e.g. `python3 -m acm_switchover`).
    - This preserves precedence:
      - `--state-file` still wins (the app resolves it first).
      - The code validates `ACM_SWITCHOVER_STATE_DIR` only when `--state-file` is not provided.
  - Example wrapper (`/usr/bin/acm-switchover`):

    ```sh
    #!/bin/sh
    # Optional admin overrides (RPM/Fedora/RHEL convention):
    [ -r /etc/sysconfig/acm-switchover ] && . /etc/sysconfig/acm-switchover
    # Optional admin overrides (Debian/Ubuntu convention):
    [ -r /etc/default/acm-switchover ] && . /etc/default/acm-switchover

    : "${ACM_SWITCHOVER_STATE_DIR:=/var/lib/acm-switchover}"
    export ACM_SWITCHOVER_STATE_DIR

    exec /usr/bin/python3 -m acm_switchover "$@"
    ```

  - Create `/var/lib/acm-switchover/` in post-install with secure perms (recommend `0750 root:root`).
    - If non-root users need per-user state without sudo, they can override via `ACM_SWITCHOVER_STATE_DIR=$HOME/.local/state/acm-switchover` or pass `--state-file`.
  - Optional: ship `/etc/sysconfig/acm-switchover` (RPM) with `ACM_SWITCHOVER_STATE_DIR=...` for persistent overrides (the wrapper will source it).

- COPR docs:
  - `packaging/rpm/copr/README.md` with project setup + webhook instructions

### 6. Debian/Ubuntu packaging

- Add `packaging/deb/debian/`:
  - `control`, `rules`, `changelog`, `copyright`,
  - `install` mappings and `postinst` to create `/var/lib/acm-switchover/`.

- Install the same `/usr/bin` wrapper scripts as RPM (see Step 5) so system packages default to `/var/lib/acm-switchover` without affecting `--state-file` precedence.
- In `postinst`, create `/var/lib/acm-switchover/` with secure perms (recommend `0750 root:root`).
- Optional: ship `/etc/default/acm-switchover` for admins to set `ACM_SWITCHOVER_STATE_DIR` persistently (the wrapper will source it).

- Optional CI: `.github/workflows/deb-build.yml` to build artifacts and attach to GitHub Releases.

### 7. Helm chart for full deployment

- Create `packaging/helm/acm-switchover/` with:
  - `Chart.yaml` version `1.4.0`, appVersion `1.4.0`
  - `values.yaml` with image repo/tag, resources, tolerations, etc.

- State handling in-cluster:
  - Mount a PVC to `/var/lib/acm-switchover`.
  - Set `ACM_SWITCHOVER_STATE_DIR=/var/lib/acm-switchover` in the Job env.
  - Do **not** assume `.state/` under the container workdir.

- Security context for OpenShift:
  - Prefer `runAsNonRoot: true` and avoid hard-coding `runAsUser` unless required.
  - Ensure image file permissions support arbitrary UID (group 0, `g=u`).

- Include `rbacOnly` mode and keep the existing standalone RBAC chart under `deploy/helm/acm-switchover-rbac/` for backward compatibility.

### 8. Container image (OpenShift compliant) + multi-arch builds

Keep `container-bootstrap/Containerfile` as the CI build input (since workflows already reference it). Use `packaging/container/` for docs and helper scripts.

- Fix the Containerfile to include missing runtime files:
  - `check_rbac.py`, `show_state.py`
  - `completions/`

- Fix container state persistence alignment:
  - Set `ENV ACM_SWITCHOVER_STATE_DIR=/var/lib/acm-switchover` in the image.
  - Update container docs accordingly (the code uses `ACM_SWITCHOVER_STATE_DIR`, not `STATE_DIR`).

- Multi-arch build scripts (standard tooling):
  - Docker (Buildx):
    - `docker buildx build --platform linux/amd64,linux/arm64 --tag <img>:<tag> --push .`
  - Podman (manifest flow):
    - `podman build --platform linux/amd64,linux/arm64 --manifest <img>:<tag> .`
    - `podman manifest push <img>:<tag>`

- Add `packaging/container/SECURITY.md` covering:
  - OpenShift SCC compatibility
  - Example `--entrypoint` usage for `check_rbac.py` and `show_state.py`

### 9. Package `deploy/` directory contents

- Include `deploy/kustomize/`, `deploy/acm-policies/`, `deploy/rbac/` in RPM/DEB under `/usr/share/acm-switchover/deploy/`.

- Keep `deploy/helm/acm-switchover-rbac/` as the RBAC-only chart; `packaging/helm/acm-switchover/` is the full app chart.

- Install `quick-start.sh` to `/usr/libexec/acm-switchover/quick-start.sh`.

### 10. Helper scripts for packaging workflows

- `packaging/common/build-all.sh` — Build all formats (container, wheel/sdist, RPM, DEB).
- `packaging/common/test-install.sh` — Smoke-test installs in clean containers.
- `packaging/common/validate-versions.sh` — Fail if version sources drift.

- Add `packaging/README.md` describing:
  - directory layout
  - supported packaging formats
  - version bump process
  - how state dir defaults work across install methods

### 11. CI/CD + documentation updates

- Add `.github/workflows/version-sync.yml` to validate version consistency.
- Add (optional) `.github/workflows/packaging-release.yml` to build/publish artifacts on tags:
  - container (already exists)
  - PyPI (if desired)
  - RPM via COPR
  - DEB artifacts to GitHub Releases
  - Helm chart packaging + repo index

- Update docs to match the improved state handling:
  - Ensure docs consistently describe `ACM_SWITCHOVER_STATE_DIR` precedence and container defaults.

---

## Critical Gaps Identified (Must Fix)

1. **Container image missing `check_rbac.py`, `show_state.py`, and `completions/`** — required for parity with repo CLIs.

2. **State persistence mismatch in container/helm** — ensure `ACM_SWITCHOVER_STATE_DIR` is set to the mounted state volume path.

3. **Version drift across repo files** — versions exist (v1.4.0) but are not yet consistently reflected in all metadata/docs.

4. **`--version` flags missing on Python CLIs** — CI currently treats this as optional; make it deterministic and testable.

5. ~~**State viewer default dir should align with code**~~ — ✅ RESOLVED in v1.3.2: `show_state.py` now honors `ACM_SWITCHOVER_STATE_DIR` when listing/locating state files.

---

## Further Considerations

1. **Helm chart namespace** — Create or assume namespace exists? Recommend: create conditionally (`createNamespace: true`).

2. **Kubeconfig strategy** — Support either:
  - Secret-mounted kubeconfig with both contexts
  - In-cluster SA (where feasible)

3. **Job retry policy** — Recommend `backoffLimit: 0` and rely on state-based resume.

4. **CronJob for validation** — Optional (disabled by default).

5. **SELinux for `/var/lib/acm-switchover/`** — Document `semanage fcontext` guidance in RPM notes.

6. **Shell completion beyond bash** — Keep bash first; zsh/fish can be post-1.4.x enhancement.

7. **`container-bootstrap/get-pip.py`** — Decide to remove or document its purpose.
