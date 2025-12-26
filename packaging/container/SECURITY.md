# Container Security

This document describes security considerations for the ACM Switchover container image.

## OpenShift SCC Compatibility

The container image is designed to be compatible with OpenShift's Security Context Constraints (SCC):

### restricted-v2 SCC (Default)

The image is compatible with the `restricted-v2` SCC:

- **runAsNonRoot**: The container runs as user 1001 (non-root)
- **Arbitrary UID**: File permissions use group 0 with `g=u` so any UID can read/write
- **No privileged operations**: No capabilities required
- **Read-only root filesystem**: Supported with writable volumes for state and tmp

### Deployment Example

```yaml
securityContext:
  runAsNonRoot: true
  seccompProfile:
    type: RuntimeDefault
containerSecurityContext:
  allowPrivilegeEscalation: false
  readOnlyRootFilesystem: true
  runAsNonRoot: true
  capabilities:
    drop:
      - ALL
```

## Container User

The container runs as:
- **UID**: 1001
- **GID**: 0 (root group)
- **Home**: /app

All application files are owned by `1001:0` with group-writable permissions.

## Volume Mounts

The container expects these volume mounts:

| Path | Purpose | Required |
|------|---------|----------|
| `/var/lib/acm-switchover` | State file persistence | Yes (for resume capability) |
| `/app/.kube` | Kubeconfig mount | Yes |
| `/tmp` | Temporary files | Yes (emptyDir for read-only root) |

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `ACM_SWITCHOVER_STATE_DIR` | State directory path | `/var/lib/acm-switchover` |
| `KUBECONFIG` | Path to kubeconfig file | `/app/.kube/config` |
| `HOME` | Home directory | `/app` |

## Running Alternative Commands

The container entrypoint is `acm-switchover`. To run other tools:

```bash
# Check RBAC permissions
podman run --rm \
  -v ~/.kube:/app/.kube:ro \
  --entrypoint python3 \
  quay.io/tomazb/acm-switchover:latest \
  /app/check_rbac.py --context my-hub

# View state file
podman run --rm \
  -v ./state:/var/lib/acm-switchover:ro \
  --entrypoint python3 \
  quay.io/tomazb/acm-switchover:latest \
  /app/show_state.py
```

Or using the wrapper approach:

```bash
# Override entrypoint
podman run --rm \
  --entrypoint /bin/sh \
  quay.io/tomazb/acm-switchover:latest \
  -c "python3 /app/check_rbac.py --help"
```

## Image Scanning

The image is based on Red Hat UBI 9 and is regularly scanned for vulnerabilities:

- Base image: `registry.access.redhat.com/ubi9/ubi-minimal`
- Python packages are pinned to known-good versions
- CVE scanning via Quay.io and GitHub Dependabot

## Signing

Container images are signed using cosign. Verify signatures:

```bash
cosign verify \
  --certificate-identity-regexp "https://github.com/tomazb/rh-acm-switchover" \
  --certificate-oidc-issuer "https://token.actions.githubusercontent.com" \
  quay.io/tomazb/acm-switchover:latest
```

## Network Requirements

The container needs network access to:

- Kubernetes API servers for both hub clusters
- No inbound ports are exposed

## Secrets Handling

- Kubeconfig is mounted read-only
- No secrets are written to the container filesystem
- State files may contain context names but not credentials
