# GitHub Actions Setup Guide

This guide explains how to configure GitHub repository secrets for automated container image builds and publishing.

## Required Secrets

To enable the full CI/CD pipeline for container image building, you need to configure the following secrets in your GitHub repository.

### 1. Quay.io Credentials

The container images are published to Quay.io registry.

#### Create Quay.io Account and Repository

1. Go to [quay.io](https://quay.io) and sign up/login
2. Create a new repository: `acm-switchover`
3. Set repository visibility (public or private)
4. Create a robot account or use your personal credentials

#### Generate Quay.io Token

**Option A: Robot Account (Recommended)**

1. Go to your Quay.io account settings
2. Navigate to "Robot Accounts"
3. Click "Create Robot Account"
4. Name it: `acm_switchover_publisher`
5. Grant it "Write" permissions to your `acm-switchover` repository
6. Download the credentials (username and token)

**Option B: Personal Account**

1. Go to Account Settings → CLI Password
2. Generate encrypted password
3. Use your username and encrypted password

#### Add Secrets to GitHub

1. Go to your GitHub repository
2. Navigate to **Settings → Secrets and variables → Actions**
3. Click "New repository secret"
4. Add the following secrets:

**QUAY_USERNAME**

```text
Value: <your-quay-username>
# Example: tomazborstnar+acm_switchover_publisher (for robot account)
# Or: tomazborstnar (for personal account)
```

**QUAY_PASSWORD**

```text
Value: <your-quay-token>
# Example: ABC123XYZ... (the encrypted password or robot token)
```

### 2. GitHub Token (Optional)

GitHub automatically provides `GITHUB_TOKEN` for workflows. No additional configuration needed for:
- Creating releases
- Uploading artifacts
- Uploading security scans

## Workflow Permissions

Ensure the GitHub Actions workflow has required permissions:

1. Go to **Settings → Actions → General**
2. Under "Workflow permissions":
   - Select "Read and write permissions"
   - Check "Allow GitHub Actions to create and approve pull requests"
3. Click "Save"

## Testing the Setup

### Test Container Build (Without Publishing)

Create a test branch and push:

```bash
git checkout -b test-container-build
git push origin test-container-build
```

This will trigger the workflow without publishing (PR builds don't push images).

### Test Full Pipeline (With Publishing)

1. **Commit to main branch**:
   ```bash
   git checkout main
   git commit --allow-empty -m "Test container build"
   git push origin main
   ```
   
   This will build and push to `quay.io/tomazborstnar/acm-switchover:latest`

2. **Create a version tag**:
   ```bash
   git tag v1.0.0
   git push origin v1.0.0
   ```
   
   This will build and push:
   - `quay.io/tomazborstnar/acm-switchover:v1.0.0`
   - `quay.io/tomazborstnar/acm-switchover:1.0.0`
   - `quay.io/tomazborstnar/acm-switchover:1.0`
   - `quay.io/tomazborstnar/acm-switchover:1`
   - `quay.io/tomazborstnar/acm-switchover:latest`
   
   And create a GitHub Release with SBOM attached.

## Verify Successful Build

### Check GitHub Actions

1. Go to **Actions** tab in your repository
2. Click on the latest workflow run
3. Verify all jobs completed successfully:
   - ✅ build-and-push
   - ✅ test-container
   - ✅ publish-release (for tags only)

### Verify Quay.io

1. Go to [quay.io/repository/tomazborstnar/acm-switchover](https://quay.io/repository/tomazborstnar/acm-switchover)
2. Check "Tags" tab
3. Verify new images are listed
4. Check image metadata (labels, architecture support)

### Verify Image Locally

```bash
# Pull the image
podman pull quay.io/tomazborstnar/acm-switchover:latest

# Run basic test
podman run --rm quay.io/tomazborstnar/acm-switchover:latest --help

# Verify prerequisites
podman run --rm quay.io/tomazborstnar/acm-switchover:latest sh -c "oc version --client && jq --version"
```

## Security Configuration

### Enable Image Scanning on Quay.io

1. Go to your Quay.io repository settings
2. Enable "Security Scanning"
3. Quay will automatically scan for vulnerabilities

### Review GitHub Security Alerts

1. Go to **Security** tab in GitHub
2. Check "Code scanning alerts" for Trivy results
3. Review and address any critical/high vulnerabilities

## Troubleshooting

### Build Fails: "denied: access forbidden"

**Issue**: Incorrect Quay.io credentials

**Solution**:
1. Verify `QUAY_USERNAME` and `QUAY_PASSWORD` are correct
2. Check robot account has "Write" permissions
3. Ensure repository name matches: `tomazborstnar/acm-switchover`

### Build Fails: "manifest unknown"

**Issue**: First push to a new repository

**Solution**:
1. Ensure repository exists on Quay.io
2. Make it public or grant robot account access

### Multi-Arch Build Issues

**Issue**: ARM64 build fails

**Solution**:
1. Check if base image supports ARM64
2. Review QEMU setup in workflow
3. Verify architecture-specific binaries (oc, jq) download correctly

### Cosign Signing Fails

**Issue**: "COSIGN_EXPERIMENTAL required"

**Solution**: Already configured in workflow. If issues persist:
1. Check cosign version
2. Verify GitHub OIDC permissions
3. Review workflow logs

## Advanced Configuration

### Custom Image Registry

To use a different registry (e.g., Docker Hub, GitHub Container Registry):

1. Update workflow file `.github/workflows/container-build.yml`:
   ```yaml
   env:
     REGISTRY: ghcr.io  # or docker.io
     IMAGE_NAME: ${{ github.repository }}
   ```

2. Add corresponding secrets:
   - `DOCKER_USERNAME` and `DOCKER_PASSWORD` (Docker Hub)
   - `GHCR_TOKEN` (GitHub Container Registry)

### Build on Schedule

Add scheduled builds to workflow:

```yaml
on:
  schedule:
    - cron: '0 2 * * 0'  # Weekly on Sunday at 2 AM
```

### Matrix Builds

Build for additional platforms:

```yaml
platforms: linux/amd64,linux/arm64,linux/ppc64le,linux/s390x
```

Note: Ensure base image and binaries support these architectures.

## Maintenance

### Regular Updates

1. **Weekly**: Review security scan results
2. **Monthly**: Update base image version
3. **Quarterly**: Review and update OC CLI version
4. **As needed**: Update Python dependencies

### Rotating Credentials

1. Generate new Quay.io robot account token
2. Update `QUAY_PASSWORD` secret in GitHub
3. Delete old robot account (after verifying new one works)

## Support

For issues with:
- **Quay.io**: https://access.redhat.com/support
- **GitHub Actions**: https://docs.github.com/en/actions
- **This project**: Create an issue at https://github.com/tomazb/rh-acm-switchover/issues

## Checklist

Before enabling automatic builds:

- [ ] Quay.io account created
- [ ] Repository `acm-switchover` created on Quay.io
- [ ] Robot account created (or credentials ready)
- [ ] `QUAY_USERNAME` secret added to GitHub
- [ ] `QUAY_PASSWORD` secret added to GitHub
- [ ] Workflow permissions set to "Read and write"
- [ ] Test build triggered and successful
- [ ] Image verified on Quay.io
- [ ] Image tested locally
- [ ] Security scanning enabled
- [ ] Documentation updated with correct image paths
