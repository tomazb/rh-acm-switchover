# Debian Packaging for ACM Switchover

This directory contains Debian packaging files for building `.deb` packages for Debian/Ubuntu.

## Building Locally

### Prerequisites

```bash
# Debian/Ubuntu
apt install build-essential debhelper dh-python python3-all python3-setuptools pandoc devscripts
```

### Build Package

```bash
# From repository root
cd /path/to/rh-acm-switchover

# Build the package
dpkg-buildpackage -us -uc -b
```

### Install

```bash
sudo dpkg -i ../acm-switchover_*.deb
sudo apt-get install -f  # Install dependencies
```

## Package Contents

After installation, the package provides:

| Path | Description |
|------|-------------|
| `/usr/bin/acm-switchover` | Main CLI wrapper |
| `/usr/bin/acm-switchover-rbac` | RBAC checker wrapper |
| `/usr/bin/acm-switchover-state` | State viewer wrapper |
| `/usr/libexec/acm-switchover/` | Helper scripts |
| `/usr/share/acm-switchover/` | Python code and deploy manifests |
| `/usr/share/man/man1/` | Man pages |
| `/usr/share/bash-completion/completions/` | Bash completions |
| `/var/lib/acm-switchover/` | State directory |
| `/etc/default/acm-switchover` | Configuration overrides |

## State Directory

The DEB package defaults to `/var/lib/acm-switchover/` for state files. This can be overridden:

1. Use `--state-file` CLI option (highest precedence)
2. Set `ACM_SWITCHOVER_STATE_DIR` environment variable
3. Edit `/etc/default/acm-switchover`

## Lintian

Check the package for policy compliance:

```bash
lintian ../acm-switchover_*.deb
```
