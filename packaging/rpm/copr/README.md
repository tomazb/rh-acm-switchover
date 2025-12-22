# COPR Integration for ACM Switchover

This directory contains documentation for automated RPM builds via [Fedora COPR](https://copr.fedorainfracloud.org/).

## Setting Up COPR

### 1. Create a COPR Project

1. Log in to [copr.fedorainfracloud.org](https://copr.fedorainfracloud.org/)
2. Click "New Project"
3. Configure:
   - **Name**: `acm-switchover`
   - **Description**: ACM Hub Switchover Automation Tool
   - **Chroots**: Select Fedora and EPEL versions (e.g., `fedora-39-x86_64`, `epel-9-x86_64`)
   - **Build options**: Enable "Auto-rebuild on package update"

### 2. Configure GitHub Webhook

1. In COPR, go to your project → Settings → Integrations
2. Copy the webhook URL
3. In GitHub, go to Settings → Webhooks → Add webhook
4. Configure:
   - **Payload URL**: The COPR webhook URL
   - **Content type**: `application/json`
   - **Events**: Select "Releases"

### 3. Create Source Build Configuration

Create a `.copr/Makefile` in the repo root:

```makefile
srpm:
	dnf install -y git rpm-build
	VERSION=$$(cat packaging/common/VERSION)
	git archive --prefix=acm-switchover-$${VERSION}/ -o acm-switchover-$${VERSION}.tar.gz HEAD
	rpmbuild -bs --define "_sourcedir ." --define "_srcrpmdir $(outdir)" packaging/rpm/acm-switchover.spec
```

## Manual Builds

### Build SRPM Locally

```bash
VERSION=$(cat packaging/common/VERSION)
git archive --prefix=acm-switchover-${VERSION}/ -o acm-switchover-${VERSION}.tar.gz HEAD
rpmbuild -bs --define "_sourcedir ." packaging/rpm/acm-switchover.spec
```

### Submit to COPR

```bash
copr-cli build acm-switchover ~/rpmbuild/SRPMS/acm-switchover-*.src.rpm
```

## Installing from COPR

Once the COPR project is set up:

```bash
# Enable the repository
sudo dnf copr enable <username>/acm-switchover

# Install
sudo dnf install acm-switchover
```

## Version Updates

When releasing a new version:

1. Run `./packaging/common/version-bump.sh <new-version>`
2. Update the `%changelog` section in `acm-switchover.spec`
3. Commit and create a GitHub release
4. COPR will automatically build the new version (if webhook is configured)
