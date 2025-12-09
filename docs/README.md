# Documentation Index

Last Updated: 2025-12-09

Welcome to the ACM Switchover Automation documentation.

## 📖 Documentation Overview

### Getting Started

1. **[README](../README.md)** - Start here! Project overview and quick start
2. **[Quick Reference (QUICKREF)](QUICKREF.md)** - Command cheat sheet for quick lookups
3. **[Container Quick Reference](CONTAINER_QUICKREF.md)** - Cheat sheet for container usage
4. **[Installation Guide (INSTALL)](INSTALL.md)** - How to install and configure

### User Guides

1. **[Usage Guide (USAGE)](USAGE.md)** - Detailed usage examples and scenarios
2. **[Container Usage Guide](CONTAINER_USAGE.md)** - Detailed guide for container deployment
3. **[Testing Guide (TESTING)](TESTING.md)** - How to run tests and CI/CD pipelines

### Technical Documentation

1. **[Architecture (ARCHITECTURE)](ARCHITECTURE.md)** - Design principles and implementation details
2. **[Product Requirements (PRD)](PRD.md)** - Complete requirements specification
3. **[Project Summary](PROJECT_SUMMARY.md)** - Comprehensive project overview
4. **[Deliverables](DELIVERABLES.md)** - Complete project inventory
5. **[RBAC Requirements](RBAC_REQUIREMENTS.md)** - Complete RBAC permissions documentation
6. **[RBAC Deployment Guide](RBAC_DEPLOYMENT.md)** - Step-by-step RBAC deployment
7. **[RBAC Implementation Summary](RBAC_IMPLEMENTATION_SUMMARY.md)** - Overview of RBAC features

### Security

1. **[Contributing (CONTRIBUTING)](CONTRIBUTING.md)** - Development guidelines
2. **[GitHub Actions Setup](GITHUB_ACTIONS_SETUP.md)** - CI/CD pipeline configuration
3. **[Changelog (CHANGELOG)](CHANGELOG.md)** - Version history and changes

### Source Material

1. **[ACM Switchover Runbook (Markdown)](ACM_SWITCHOVER_RUNBOOK.md)** - Detailed operational runbook

---

## 🚀 Quick Navigation

### For New Users

1. Read [README](../README.md)
2. Review [QUICKREF](QUICKREF.md)
3. Follow [INSTALL](INSTALL.md)
4. Try examples from [USAGE](USAGE.md)

### Modes Overview

- **Switchover Mode**: Migrates from the current primary hub to a secondary hub.
	- Requires `--secondary-context` to target the new hub.
	- Phases: INIT → PREFLIGHT → PRIMARY_PREP → ACTIVATION → POST_ACTIVATION → FINALIZATION → COMPLETED.
	- Use `--old-hub-action` to decide treatment of the old hub (`secondary`, `decommission`, `none`).

- **Decommission Mode**: Removes ACM components from a hub.
	- Enabled with `--decommission`; operates on the hub provided by `--primary-context`.
	- Does not require `--secondary-context`.
	- `--non-interactive` is only valid together with `--decommission` for unattended cleanup.

Quick examples:

```bash
# Switchover to secondary hub
python acm_switchover.py \
	--primary-context hub-A \
	--secondary-context hub-B \
	--method passive \
	--old-hub-action secondary

# Decommission old hub (no secondary context needed)
python acm_switchover.py \
	--decommission \
	--primary-context hub-A \
	--non-interactive
```

### For Developers

1. Review [ARCHITECTURE](ARCHITECTURE.md)
2. Read [CONTRIBUTING](CONTRIBUTING.md)
3. Study [TESTING](TESTING.md)
4. Check [PRD](PRD.md) for requirements

### For Operations

1. Review [USAGE](USAGE.md) for scenarios
2. Check [QUICKREF](QUICKREF.md) for commands
3. Read troubleshooting in [USAGE](USAGE.md)

---

## 📊 Documentation Statistics

- **Total documentation**: 13 markdown files
- **Total lines**: ~12,000+ lines
- **User guides**: 4 documents
- **Technical docs**: 7 documents
- **Security**: 3 documents
- **Development**: 2 documents

---

## 🔍 Finding Information

### By Topic

#### Installation & Setup

- Installation: [INSTALL](INSTALL.md)
- Configuration: [INSTALL](INSTALL.md) + [USAGE](USAGE.md)

#### Usage & Operations

- Quick commands: [QUICKREF](QUICKREF.md)
- Detailed usage: [USAGE](USAGE.md)
- Troubleshooting: [USAGE](USAGE.md)

#### Security

1. **[Security Policy](../SECURITY.md)** - Security policy and vulnerability reporting
2. **[RBAC Requirements](RBAC_REQUIREMENTS.md)** - RBAC permissions and security controls

### Development Resources

- Architecture: [ARCHITECTURE](ARCHITECTURE.md)
- Contributing: [CONTRIBUTING](CONTRIBUTING.md)
- Testing: [TESTING](TESTING.md)

#### Requirements & Planning

- Requirements: [PRD](PRD.md)
- Project info: [PROJECT_SUMMARY](PROJECT_SUMMARY.md)
- Deliverables: [DELIVERABLES](DELIVERABLES.md)

#### Version History

- Changes: [CHANGELOG](CHANGELOG.md)

---

## 📝 Documentation Guidelines

When contributing to documentation:

1. **Keep it current**: Update docs with code changes
2. **Be specific**: Include examples and code snippets
3. **Stay organized**: Follow existing structure
4. **Link appropriately**: Cross-reference related docs
5. **Test examples**: Verify all commands work

See [CONTRIBUTING](CONTRIBUTING.md) for more details.

---

 
