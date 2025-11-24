# Container Quick Reference

Quick command reference for using the ACM Switchover container image.

## Pull Image

```bash
podman pull quay.io/tomazborstnar/acm-switchover:latest
```

## Basic Commands

### Help

```bash
podman run --rm quay.io/tomazborstnar/acm-switchover:latest --help
```

### Validate Only

```bash
podman run -it --rm \
  -v ~/.kube:/app/.kube:ro \
  quay.io/tomazborstnar/acm-switchover:latest \
  --validate-only \
  --primary-context primary-hub \
  --secondary-context secondary-hub
```

### Dry Run

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

### Execute Switchover

```bash
podman run -it --rm \
  -v ~/.kube:/app/.kube:ro \
  -v $(pwd)/state:/var/lib/acm-switchover \
  quay.io/tomazborstnar/acm-switchover:latest \
  --primary-context primary-hub \
  --secondary-context secondary-hub \
  --method passive
```

### Rollback

```bash
podman run -it --rm \
  -v ~/.kube:/app/.kube:ro \
  -v $(pwd)/state:/var/lib/acm-switchover \
  quay.io/tomazborstnar/acm-switchover:latest \
  --rollback \
  --primary-context primary-hub \
  --secondary-context secondary-hub
```

## Volume Mounts

| Mount | Path | Mode | Purpose |
|-------|------|------|---------|
| Kubeconfig | `-v ~/.kube:/app/.kube:ro` | Read-only | Cluster access |
| State | `-v $(pwd)/state:/var/lib/acm-switchover` | Read-write | State persistence |

## Common Flags

| Flag | Description |
|------|-------------|
| `--validate-only` | Run validations only, no changes |
| `--dry-run` | Preview actions without executing |
| `--method passive` | Use passive sync method |
| `--method full` | Use full restore method |
| `--rollback` | Revert to primary hub |
| `--verbose` | Enable debug logging |

## Aliases (Optional)

Add to your `~/.bashrc` or `~/.zshrc`:

```bash
alias acm-switchover='podman run -it --rm \
  -v ~/.kube:/app/.kube:ro \
  -v $(pwd)/state:/var/lib/acm-switchover \
  quay.io/tomazborstnar/acm-switchover:latest'
```

Then use:

```bash
acm-switchover --validate-only --primary-context primary-hub --secondary-context secondary-hub
```

## Environment Variables

```bash
podman run -it --rm \
  -v /path/to/kubeconfig:/config:ro \
  -e KUBECONFIG=/config/kubeconfig \
  quay.io/tomazborstnar/acm-switchover:latest \
  --help
```

## Troubleshooting

### Check Prerequisites

```bash
podman run --rm quay.io/tomazborstnar/acm-switchover:latest \
  sh -c "oc version --client && jq --version && python3 --version"
```

### Interactive Shell

```bash
podman run -it --rm \
  -v ~/.kube:/app/.kube:ro \
  --entrypoint /bin/bash \
  quay.io/tomazborstnar/acm-switchover:latest
```

### List Contexts

```bash
podman run --rm \
  -v ~/.kube:/app/.kube:ro \
  --entrypoint oc \
  quay.io/tomazborstnar/acm-switchover:latest \
  config get-contexts
```

## More Information

- Full documentation: `docs/CONTAINER_USAGE.md`
- Setup guide: `docs/GITHUB_ACTIONS_SETUP.md`
- Source code: https://github.com/tomazb/rh-acm-switchover
