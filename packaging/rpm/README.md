# RPM Packaging for ACM Switchover

This directory contains RPM packaging files for building Fedora/RHEL/CentOS packages.

## Building Locally

### Prerequisites

```bash
# Fedora/RHEL
dnf install rpm-build rpmdevtools python3-devel pandoc

# Set up RPM build tree
rpmdev-setuptree
```

### Build from Source

```bash
# Create source tarball
VERSION=$(cat ../common/VERSION)
git archive --prefix=acm-switchover-${VERSION}/ -o ~/rpmbuild/SOURCES/acm-switchover-${VERSION}.tar.gz HEAD

# Build RPM
rpmbuild -ba acm-switchover.spec
```

### Install

```bash
sudo dnf install ~/rpmbuild/RPMS/noarch/acm-switchover-*.rpm
```

## COPR Integration

See [copr/README.md](copr/README.md) for automated builds via Fedora COPR.

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
| `/etc/sysconfig/acm-switchover` | Configuration overrides |

## State Directory

The RPM package defaults to `/var/lib/acm-switchover/` for state files. This can be overridden:

1. Use `--state-file` CLI option (highest precedence)
2. Set `ACM_SWITCHOVER_STATE_DIR` environment variable
3. Edit `/etc/sysconfig/acm-switchover`
