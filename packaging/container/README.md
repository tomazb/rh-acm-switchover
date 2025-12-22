# Container Image

Documentation for the ACM Switchover container image.

## Quick Start

```bash
# Pull the image
podman pull quay.io/tomazb/acm-switchover:latest

# Run with kubeconfig mounted
podman run --rm -it \
  -v ~/.kube:/app/.kube:ro \
  -v ./state:/var/lib/acm-switchover \
  quay.io/tomazb/acm-switchover:latest \
  --primary-context hub1 \
  --secondary-context hub2 \
  --method passive-sync \
  --old-hub-action secondary
```

## Image Details

| Property | Value |
|----------|-------|
| Base Image | Red Hat UBI 9 Minimal |
| Architectures | amd64, arm64 |
| User | 1001 (non-root) |
| Entrypoint | `python3 /app/acm_switchover.py` |

## Available Commands

The container includes multiple CLI tools:

| Tool | Path | Description |
|------|------|-------------|
| acm-switchover | `/app/acm_switchover.py` | Main switchover tool |
| acm-switchover-rbac | `/app/check_rbac.py` | RBAC permission checker |
| acm-switchover-state | `/app/show_state.py` | State file viewer |

## Building Locally

```bash
# From repository root
podman build -f container-bootstrap/Containerfile -t acm-switchover:local .

# Multi-arch build
podman build \
  --platform linux/amd64,linux/arm64 \
  --manifest acm-switchover:local \
  -f container-bootstrap/Containerfile .
```

## Security

See [SECURITY.md](SECURITY.md) for security considerations and OpenShift SCC compatibility.
