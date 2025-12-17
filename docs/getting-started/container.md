# Container Usage Guide

## Overview

The ACM Switchover tool is available as a container image based on Red Hat Universal Base Image (UBI) 9. This provides a consistent, portable runtime environment with all prerequisites pre-installed.

## Prerequisites Included in Container

The container image includes:

- **Python 3.9** - Runtime environment
- **OpenShift CLI (oc)** - Kubernetes API client (stable-4.14+)
- **kubectl** - Kubernetes command-line tool (via oc)
- **jq** - JSON processor for debugging
- **curl** - HTTP client for downloads
- **Python packages**:
  - `kubernetes>=28.0.0`
  - `PyYAML>=6.0`
  - `rich>=13.0.0`

## Image Details

- **Registry**: `quay.io/tomazborstnar/acm-switchover`
- **Base Image**: Red Hat UBI 9 Minimal
- **Architectures**: linux/amd64, linux/arm64
- **User**: Non-root (UID 1001)
- **Size**: ~200-250MB compressed

## Quick Start

### Pull the Image

```bash
# Using podman (recommended for RHEL/Fedora)
podman pull quay.io/tomazborstnar/acm-switchover:latest

# Using docker
docker pull quay.io/tomazborstnar/acm-switchover:latest
```

### Basic Usage

```bash
# Display help
podman run --rm quay.io/tomazborstnar/acm-switchover:latest --help

# Validate switchover prerequisites
podman run --rm \
  -v ~/.kube:/app/.kube:ro \
  quay.io/tomazborstnar/acm-switchover:latest \
  --validate-only \
  --primary-context primary-hub \
  --secondary-context secondary-hub
```

## Volume Mounts

### Required Volumes

#### 1. Kubeconfig (Read-Only)

Mount your Kubernetes configuration:

```bash
-v ~/.kube:/app/.kube:ro
```

**Alternative**: Use custom kubeconfig location:

```bash
-v /path/to/kubeconfig:/config:ro \
-e KUBECONFIG=/config/kubeconfig
```

#### 2. State Directory (Read-Write)

Mount a directory for state persistence:

```bash
-v ./state:/var/lib/acm-switchover
```

This ensures state files survive container restarts and enable resume capability.

## Complete Examples

### 1. Validation Only

```bash
podman run -it --rm \
  -v ~/.kube:/app/.kube:ro \
  quay.io/tomazborstnar/acm-switchover:latest \
  --validate-only \
  --primary-context primary-hub \
  --secondary-context secondary-hub
```

### 2. Dry-Run Mode

```bash
podman run -it --rm \
  -v ~/.kube:/app/.kube:ro \
  -v $(pwd)/state:/var/lib/acm-switchover \
  quay.io/tomazborstnar/acm-switchover:latest \
  --dry-run \
  --primary-context primary-hub \
  --secondary-context secondary-hub \
  --method passive
```

### 3. Execute Switchover (Passive Sync)

```bash
podman run -it --rm \
  -v ~/.kube:/app/.kube:ro \
  -v $(pwd)/state:/var/lib/acm-switchover \
  quay.io/tomazborstnar/acm-switchover:latest \
  --primary-context primary-hub \
  --secondary-context secondary-hub \
  --method passive
```

### 4. Execute Switchover (Full Restore)

```bash
podman run -it --rm \
  -v ~/.kube:/app/.kube:ro \
  -v $(pwd)/state:/var/lib/acm-switchover \
  quay.io/tomazborstnar/acm-switchover:latest \
  --primary-context primary-hub \
  --secondary-context secondary-hub \
  --method full
```

### 5. Rollback to Primary

```bash
podman run -it --rm \
  -v ~/.kube:/app/.kube:ro \
  -v $(pwd)/state:/var/lib/acm-switchover \
  quay.io/tomazborstnar/acm-switchover:latest \
  --rollback \
  --primary-context primary-hub \
  --secondary-context secondary-hub
```

### 6. Decommission Old Hub

```bash
podman run -it --rm \
  -v ~/.kube:/app/.kube:ro \
  quay.io/tomazborstnar/acm-switchover:latest \
  --decommission \
  --primary-context old-hub
```

### 7. Resume from Interruption

```bash
# State is automatically resumed from the default state directory.
# In containers, set ACM_SWITCHOVER_STATE_DIR to control where state is written.
podman run -it --rm \
  -v ~/.kube:/app/.kube:ro \
  -v $(pwd)/state:/var/lib/acm-switchover \
  -e ACM_SWITCHOVER_STATE_DIR=/var/lib/acm-switchover \
  quay.io/tomazborstnar/acm-switchover:latest \
  --primary-context primary-hub \
  --secondary-context secondary-hub \
  --method passive
```

## Environment Variables

### Supported Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `KUBECONFIG` | `/app/.kube/config` | Path to Kubernetes config file |
| `ACM_SWITCHOVER_STATE_DIR` | `/var/lib/acm-switchover` | Directory for state files |
| `PYTHONUNBUFFERED` | `1` | Disable Python output buffering |
| `LOG_LEVEL` | - | Set logging verbosity (not implemented yet) |

### Example with Environment Variables

```bash
podman run -it --rm \
  -v /custom/kubeconfig:/config:ro \
  -v $(pwd)/state:/state \
  -e KUBECONFIG=/config/config \
  -e ACM_SWITCHOVER_STATE_DIR=/state \
  quay.io/tomazborstnar/acm-switchover:latest \
  --validate-only \
  --primary-context primary-hub \
  --secondary-context secondary-hub
```

## Advanced Usage

### Running with Verbose Logging

```bash
podman run -it --rm \
  -v ~/.kube:/app/.kube:ro \
  -v $(pwd)/state:/var/lib/acm-switchover \
  quay.io/tomazborstnar/acm-switchover:latest \
  --verbose \
  --primary-context primary-hub \
  --secondary-context secondary-hub \
  --method passive
```

### Using Specific Version

```bash
# Use specific version tag
podman run -it --rm \
  -v ~/.kube:/app/.kube:ro \
  quay.io/tomazborstnar/acm-switchover:v1.0.0 \
  --help

# Use version 1.x latest
podman run -it --rm \
  -v ~/.kube:/app/.kube:ro \
  quay.io/tomazborstnar/acm-switchover:1 \
  --help
```

### Interactive Shell (Debugging)

```bash
podman run -it --rm \
  -v ~/.kube:/app/.kube:ro \
  --entrypoint /bin/bash \
  quay.io/tomazborstnar/acm-switchover:latest

# Inside container:
$ oc version
$ jq --version
$ python3 acm_switchover.py --help
```

### Inspect Container Prerequisites

```bash
# Check installed tools
podman run --rm quay.io/tomazborstnar/acm-switchover:latest \
  sh -c "oc version --client && jq --version && python3 --version"
```

## OpenShift/Kubernetes Integration

### Running as a Job

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: acm-switchover-validate
  namespace: acm-automation
spec:
  template:
    spec:
      containers:
      - name: acm-switchover
        image: quay.io/tomazborstnar/acm-switchover:latest
        args:
          - "--validate-only"
          - "--primary-context"
          - "primary-hub"
          - "--secondary-context"
          - "secondary-hub"
        volumeMounts:
        - name: kubeconfig
          mountPath: /app/.kube
          readOnly: true
        - name: state
          mountPath: /var/lib/acm-switchover
      volumes:
      - name: kubeconfig
        secret:
          secretName: acm-kubeconfig
      - name: state
        persistentVolumeClaim:
          claimName: acm-switchover-state
      restartPolicy: Never
  backoffLimit: 3
```

### Running as a CronJob

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: acm-switchover-validation
  namespace: acm-automation
spec:
  schedule: "0 2 * * *"  # Daily at 2 AM
  jobTemplate:
    spec:
      template:
        spec:
          containers:
          - name: acm-switchover
            image: quay.io/tomazborstnar/acm-switchover:latest
            args:
              - "--validate-only"
              - "--primary-context"
              - "primary-hub"
              - "--secondary-context"
              - "secondary-hub"
            volumeMounts:
            - name: kubeconfig
              mountPath: /app/.kube
              readOnly: true
          volumes:
          - name: kubeconfig
            secret:
              secretName: acm-kubeconfig
          restartPolicy: OnFailure
```

## Security Considerations

### Non-Root User

The container runs as a non-root user (UID 1001) for security:

```bash
# Verify non-root execution
podman run --rm quay.io/tomazborstnar/acm-switchover:latest id
# Output: uid=1001(acm-switchover) gid=0(root) groups=0(root)
```

### Read-Only Kubeconfig

Always mount kubeconfig as read-only:

```bash
-v ~/.kube:/app/.kube:ro  # Note the :ro flag
```

### Rootless Podman

Use rootless podman for additional security:

```bash
# Run as regular user (no sudo)
podman run --rm \
  -v ~/.kube:/app/.kube:ro \
  quay.io/tomazborstnar/acm-switchover:latest --help
```

### Image Verification

```bash
# Verify image signature (requires cosign)
cosign verify quay.io/tomazborstnar/acm-switchover:latest

# Check SBOM
cosign download sbom quay.io/tomazborstnar/acm-switchover:latest
```

## Troubleshooting

### Permission Denied on State Directory

```bash
# Ensure state directory is writable
mkdir -p ./state
chmod 777 ./state  # Or set proper ownership

podman run -it --rm \
  -v $(pwd)/state:/var/lib/acm-switchover \
  quay.io/tomazborstnar/acm-switchover:latest --help
```

### Kubeconfig Not Found

```bash
# Verify kubeconfig path
ls -la ~/.kube/config

# Use explicit KUBECONFIG
podman run -it --rm \
  -v ~/.kube:/config:ro \
  -e KUBECONFIG=/config/config \
  quay.io/tomazborstnar/acm-switchover:latest \
  --validate-only \
  --primary-context primary-hub \
  --secondary-context secondary-hub
```

### Context Not Found

```bash
# List available contexts in container
podman run -it --rm \
  -v ~/.kube:/app/.kube:ro \
  --entrypoint oc \
  quay.io/tomazborstnar/acm-switchover:latest \
  config get-contexts
```

### SELinux Issues (RHEL/Fedora)

```bash
# Add :Z flag for SELinux relabeling
podman run -it --rm \
  -v ~/.kube:/app/.kube:ro,Z \
  -v $(pwd)/state:/var/lib/acm-switchover:Z \
  quay.io/tomazborstnar/acm-switchover:latest --help
```

## Building Custom Image

### Build from Source

```bash
# Clone repository
git clone https://github.com/tomazb/rh-acm-switchover.git
cd rh-acm-switchover

# Build with podman
podman build -f container-bootstrap/Containerfile -t acm-switchover:custom .

# Build with docker
docker build -f container-bootstrap/Containerfile -t acm-switchover:custom .
```

### Build Multi-Arch Image

```bash
# Build for multiple architectures
podman build --platform linux/amd64,linux/arm64 \
  -f container-bootstrap/Containerfile \
  -t acm-switchover:multi-arch .
```

### Customize OC Version

```bash
# Build with specific OpenShift CLI version
podman build \
  --build-arg OC_VERSION=4.15 \
  -f container-bootstrap/Containerfile \
  -t acm-switchover:oc415 .
```

## Best Practices

1. **Always use version tags** in production (not `latest`)
2. **Mount kubeconfig read-only** (`:ro`) for security
3. **Use validate-only mode** first to check prerequisites
4. **Persist state directory** to enable resume capability
5. **Use non-root user** (default) for better security

## Related Documentation

- **[Quick Reference](../operations/quickref.md)** - Command cheat sheet (includes container commands)
- **[CI/CD Setup](../development/ci.md)** - Guide for setting up the build pipeline
- **[Usage Guide](../operations/usage.md)** - General usage scenarios and examples


## Support

For issues or questions:

- GitHub Issues: <https://github.com/tomazb/rh-acm-switchover/issues>
- Documentation: <https://github.com/tomazb/rh-acm-switchover/docs>

## Version History

- **v1.0.0**: Initial container image release
  - UBI 9 minimal base
  - Multi-arch support (amd64, arm64)
  - Includes oc, kubectl, jq, curl
  - Non-root user execution
  - SBOM and signature support
