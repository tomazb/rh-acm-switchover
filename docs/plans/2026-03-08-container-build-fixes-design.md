# Container Build Critical Fixes

**Date**: 2026-03-08
**Scope**: Fix three critical container build issues + upgrade to Python 3.12

## Problem

The container build has three correctness issues:

1. **`.containerignore` is never used** — it lives in `container-bootstrap/` but the build context is the repo root. Docker only reads `.containerignore`/`.dockerignore` from the context root. The entire repo (tests, docs, `.git`) is sent as build context.

2. **CI validates a fake container, not the real one** — `ci-cd.yml` (`build-container` job) and `security.yml` (`container-security` job) generate throwaway `python:3.11-slim` Dockerfiles inline. They never test `container-bootstrap/Containerfile`. Scan results are meaningless.

3. **Python site-packages path may not be on `sys.path`** — The builder copies packages from `/opt/app-root/lib/python3.9/site-packages` to `/usr/lib/python3.9/site-packages` in the runtime image. UBI-minimal's Python may primarily search `/usr/lib64/` paths.

Additionally, the Containerfile is hardcoded to Python 3.9 while CI already tests 3.9–3.12.

## Approach

Targeted surgical fixes with a Python version bump to 3.12.

### Fix 1: Move `.containerignore` to repo root

- Move `container-bootstrap/.containerignore` → `.containerignore` (repo root)
- Add missing exclusions: `tests/`, `container-bootstrap/get-pip.py`
- Build context sent to daemon shrinks significantly

### Fix 2: CI workflows use the real Containerfile

**`ci-cd.yml` — `build-container` job:**
- Remove inline `cat > Dockerfile` step
- Use `file: ./container-bootstrap/Containerfile` with `context: .`

**`security.yml` — `container-security` job:**
- Remove inline `cat > Dockerfile` step
- Use `docker build -f container-bootstrap/Containerfile .`

### Fix 3: Python 3.12 upgrade + `PYTHONPATH` safety net

**Builder stage:**
- `FROM registry.access.redhat.com/ubi9/python-312:latest AS builder`
- `python312-devel` build dependency

**Runtime stage:**
- `microdnf install python3.12 python3.12-pip python3.12-setuptools`
- `ARG PYTHON_VERSION=3.12`
- `COPY --from=builder /opt/app-root/lib/python3.12/site-packages /usr/lib/python3.12/site-packages`
- Add `PYTHONPATH=/usr/lib/python3.12/site-packages` to `ENV`
- Use `python3.12` in ENTRYPOINT and HEALTHCHECK

## Files Changed

| File | Change |
|------|--------|
| `.containerignore` | New file at repo root (moved from `container-bootstrap/`) |
| `container-bootstrap/.containerignore` | Deleted (moved to root) |
| `container-bootstrap/Containerfile` | Python 3.12 upgrade, PYTHONPATH, version references |
| `.github/workflows/ci-cd.yml` | `build-container` job uses real Containerfile |
| `.github/workflows/security.yml` | `container-security` job uses real Containerfile |

## Out of Scope

- Upgrading OC version (4.14 → newer) — separate concern
- Removing `get-pip.py` — protected file per `.claude/settings.json`
- HEALTHCHECK improvements — not a correctness issue
- Cosign deprecation warnings — not a correctness issue
