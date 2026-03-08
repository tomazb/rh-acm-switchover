# Container Build Critical Fixes Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix three critical container build issues (`.containerignore` location, CI fake Dockerfiles, Python site-packages path) and upgrade to Python 3.12.

**Architecture:** The Containerfile uses a multi-stage build (UBI9 builder → UBI9-minimal runtime). We upgrade both stages from Python 3.9 to 3.12, add `PYTHONPATH` for package discoverability, move `.containerignore` to the repo root, and point CI workflows at the real Containerfile.

**Tech Stack:** Containerfile (Docker/Podman), GitHub Actions workflows, UBI9 base images, Python 3.12

---

### Task 1: Move `.containerignore` to repo root

**Files:**
- Move: `container-bootstrap/.containerignore` → `.containerignore` (repo root)

**Step 1: Move the file**

```bash
cd /home/tomaz/sources/rh-acm-switchover
git mv container-bootstrap/.containerignore .containerignore
```

**Step 2: Add missing exclusions**

Add these lines to `.containerignore` (they're currently missing — tests, plans, and the container-bootstrap helper files should not be in the image):

After the `# CI/CD` section (after `.github/`), add:

```
# Tests (not needed in container)
tests/

# Container build helpers
container-bootstrap/
```

After the `# Development` section, add:

```
# Plans and session artifacts
docs/plans/
```

**Step 3: Verify the ignore file is correct**

```bash
cat .containerignore
```

Expected: File at repo root with `tests/`, `container-bootstrap/`, and `docs/plans/` added.

**Step 4: Commit**

```bash
git add .containerignore
git commit -m "fix: move .containerignore to repo root so it is actually used

The .containerignore was inside container-bootstrap/ but the build context
is the repo root. Docker/Buildx only reads ignore files from the context
root. Also adds missing exclusions for tests/ and container-bootstrap/.

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

### Task 2: Upgrade Containerfile to Python 3.12 + add PYTHONPATH

**Files:**
- Modify: `container-bootstrap/Containerfile`

**Step 1: Update builder stage base image (line 5)**

```dockerfile
# Was: FROM registry.access.redhat.com/ubi9/python-39:latest AS builder
FROM registry.access.redhat.com/ubi9/python-312:latest AS builder
```

**Step 2: Update builder build dependencies (lines 10-13)**

```dockerfile
# Was: python39-devel
RUN dnf install -y \
    gcc \
    python3.12-devel \
    && dnf clean all
```

Note: On UBI9 python-312 image, the devel package name may be `python3.12-devel` or `python312-devel`. The `ubi9/python-312` image may already have the devel headers. If the package name differs, the build will fail clearly at this step.

**Step 3: Update ARG PYTHON_VERSION (line 37)**

```dockerfile
# Was: ARG PYTHON_VERSION=3.9
ARG PYTHON_VERSION=3.12
```

**Step 4: Update runtime microdnf install (lines 42-50)**

```dockerfile
RUN microdnf install -y \
    python3.12 \
    python3.12-pip \
    python3.12-setuptools \
    tar \
    gzip \
    curl \
    ca-certificates \
    && microdnf clean all
```

**Step 5: Update COPY --from=builder site-packages path (line 70)**

```dockerfile
# Was: COPY --from=builder /opt/app-root/lib/python3.9/site-packages /usr/lib/python3.9/site-packages
COPY --from=builder /opt/app-root/lib/python3.12/site-packages /usr/lib/python3.12/site-packages
```

**Step 6: Add PYTHONPATH to ENV block (lines 99-103)**

```dockerfile
ENV PATH="/app:/app/scripts:${PATH}" \
    PYTHONPATH="/usr/lib/python3.12/site-packages" \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=UTF-8 \
    STATE_DIR=/var/lib/acm-switchover \
    HOME=/app
```

**Step 7: Update HEALTHCHECK to use python3.12 (lines 109-110)**

```dockerfile
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python3.12 -c "import sys; sys.exit(0)" || exit 1
```

**Step 8: Update ENTRYPOINT to use python3.12 (line 113)**

```dockerfile
ENTRYPOINT ["python3.12", "/app/acm_switchover.py"]
```

**Step 9: Verify changes look correct**

```bash
grep -n "python3\.\|python-3\|PYTHON" container-bootstrap/Containerfile
```

Expected: All Python references should show 3.12, no lingering 3.9 references.

**Step 10: Commit**

```bash
git add container-bootstrap/Containerfile
git commit -m "fix: upgrade container to Python 3.12 and add PYTHONPATH

- Builder: ubi9/python-312 (was python-39)
- Runtime: python3.12 packages via microdnf (was python3.9)
- Add PYTHONPATH=/usr/lib/python3.12/site-packages to guarantee
  copied packages are found on sys.path regardless of ubi-minimal
  default search paths
- Update ENTRYPOINT and HEALTHCHECK to use python3.12

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

### Task 3: Fix CI `build-container` job in `ci-cd.yml`

**Files:**
- Modify: `.github/workflows/ci-cd.yml` (lines 293-330, the `build-container` job)

**Step 1: Replace the `build-container` job**

Replace the entire `build-container` job (lines 293-330) with a version that uses the real Containerfile:

```yaml
  build-container:
    name: Container Build Test
    runs-on: ubuntu-latest
    needs: [test, lint]

    steps:
    - name: Checkout code
      uses: actions/checkout@v4

    - name: Set up Docker Buildx
      uses: docker/setup-buildx-action@v3

    - name: Build container image
      uses: docker/build-push-action@v5
      with:
        context: .
        file: ./container-bootstrap/Containerfile
        push: false
        load: true
        tags: acm-switchover:test
        cache-from: type=gha
        cache-to: type=gha,mode=max

    - name: Test container
      run: |
        docker run --rm acm-switchover:test --help
```

Key changes:
- Removed the `Create Dockerfile for testing` step that generated a fake Dockerfile
- Added `file: ./container-bootstrap/Containerfile` to use the real Containerfile
- Kept the test step that runs `--help`

**Step 2: Verify no references to inline Dockerfile remain**

```bash
grep -n "cat > Dockerfile" .github/workflows/ci-cd.yml
```

Expected: No output (no inline Dockerfile creation).

**Step 3: Commit**

```bash
git add .github/workflows/ci-cd.yml
git commit -m "fix: CI build-container job uses real Containerfile

The job was generating an inline python:3.11-slim Dockerfile that never
tested the actual container-bootstrap/Containerfile. Now uses the real
Containerfile so CI validates what ships to production.

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

### Task 4: Fix `container-security` job in `security.yml`

**Files:**
- Modify: `.github/workflows/security.yml` (lines 138-182, the `container-security` job)

**Step 1: Replace the `container-security` job**

Replace the `container-security` job (lines 138-182) with a version that uses the real Containerfile:

```yaml
  container-security:
    name: Container Image Security
    runs-on: ubuntu-latest

    steps:
    - name: Checkout code
      uses: actions/checkout@v4

    - name: Set up Docker Buildx
      uses: docker/setup-buildx-action@v3

    - name: Build image
      uses: docker/build-push-action@v5
      with:
        context: .
        file: ./container-bootstrap/Containerfile
        push: false
        load: true
        tags: acm-switchover:scan

    - name: Run Trivy vulnerability scanner
      uses: aquasecurity/trivy-action@master
      with:
        image-ref: 'acm-switchover:scan'
        format: 'sarif'
        output: 'trivy-results.sarif'
        severity: 'CRITICAL,HIGH'
      continue-on-error: true

    - name: Upload Trivy results
      uses: github/codeql-action/upload-sarif@v3
      if: always()
      with:
        sarif_file: 'trivy-results.sarif'
      continue-on-error: true

    - name: Run Trivy for detailed report
      uses: aquasecurity/trivy-action@master
      with:
        image-ref: 'acm-switchover:scan'
        format: 'table'
        severity: 'CRITICAL,HIGH,MEDIUM'
      continue-on-error: true
```

Key changes:
- Removed the `Create Dockerfile` step that generated a fake Dockerfile
- Added Docker Buildx setup step
- Used `docker/build-push-action@v5` with `file: ./container-bootstrap/Containerfile`
- Kept both Trivy scan steps (SARIF upload + table report)

**Step 2: Verify no references to inline Dockerfile remain**

```bash
grep -n "cat > Dockerfile" .github/workflows/security.yml
```

Expected: No output.

**Step 3: Commit**

```bash
git add .github/workflows/security.yml
git commit -m "fix: security scan uses real Containerfile

The container-security job was generating an inline python:3.11-slim
Dockerfile, making Trivy scan results meaningless. Now builds and scans
the actual container-bootstrap/Containerfile.

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

### Task 5: Verify all changes

**Step 1: Check no stray Python 3.9 references remain in container files**

```bash
grep -rn "python.*3\.9\|python-39\|python39" container-bootstrap/ .github/workflows/container-build.yml
```

Expected: No output (all references updated to 3.12).

**Step 2: Check no inline Dockerfiles remain in CI**

```bash
grep -rn "cat > Dockerfile" .github/workflows/
```

Expected: No output.

**Step 3: Verify `.containerignore` is at repo root**

```bash
ls -la .containerignore && ! ls container-bootstrap/.containerignore 2>/dev/null && echo "OK: ignore file at correct location"
```

Expected: `OK: ignore file at correct location`

**Step 4: Run Python tests to confirm nothing broke**

```bash
source .venv/bin/activate && pytest tests/ -v --tb=short -q 2>&1 | tail -20
```

Expected: All tests pass (container changes don't affect Python tests, but verify anyway).

---

### Task 6: Update `container-build.yml` Python 3.9 reference

**Files:**
- Modify: `.github/workflows/container-build.yml` (the real build/push workflow)

Check if `container-build.yml` has any hardcoded Python 3.9 references that need updating. The Containerfile reference is indirect (via `file: ./container-bootstrap/Containerfile`), but the test-container job (lines 150-208) may have Python version references.

**Step 1: Check for Python 3.9 references**

```bash
grep -n "python.*3\.9\|python-39\|python39" .github/workflows/container-build.yml
```

If any found, update them to 3.12. If none, skip this task.

**Step 2: Commit if changes were made**

```bash
git add .github/workflows/container-build.yml
git commit -m "fix: update container-build.yml Python references to 3.12

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```
