# Documentation Index

Welcome to the ACM Switchover Automation documentation.

## Quick Links

| I want to... | Go to... |
|--------------|----------|
| Get started quickly | [Installation Guide](getting-started/install.md) |
| Run a switchover | [Quick Reference](operations/quickref.md) |
| Use the container image | [Container Guide](getting-started/container.md) |
| Deploy RBAC | [RBAC Deployment](deployment/rbac-deployment.md) |
| Understand the architecture | [Architecture](development/architecture.md) |
| Contribute to the project | [Contributing](../CONTRIBUTING.md) |

---

## For Operators

Day-to-day usage and operations.

- **[Quick Reference](operations/quickref.md)** - Command cheat sheet (includes container usage)
- **[Usage Guide](operations/usage.md)** - Detailed usage examples and scenarios
- **[ACM Switchover Runbook](ACM_SWITCHOVER_RUNBOOK.md)** - Comprehensive operational runbook

---

## For Deployers

Installation, configuration, and RBAC setup.

### Getting Started

- **[Installation Guide](getting-started/install.md)** - How to install and configure
- **[Container Guide](getting-started/container.md)** - Container deployment guide

### Deployment

- **[RBAC Requirements](deployment/rbac-requirements.md)** - Complete RBAC permissions documentation
- **[RBAC Deployment Guide](deployment/rbac-deployment.md)** - Step-by-step RBAC deployment

---

## For Developers

Architecture, testing, and contribution guidelines.

### Core Documentation

- **[Architecture](development/architecture.md)** - Design principles and implementation details
- **[Testing Guide](development/testing.md)** - How to run tests and CI/CD pipelines
- **[CI/CD Setup](development/ci.md)** - GitHub Actions pipeline configuration
- **[RBAC Implementation](development/rbac-implementation.md)** - Overview of RBAC features

### Implementation Notes

- **[Validation & Error Handling](development/notes/validation-and-error-handling.md)** - Validation implementation details
- **[Exception Handling](development/notes/exception-handling.md)** - Exception handling improvements
- **[Shell Safety](development/notes/shell-safety.md)** - Security fix documentation

### Contributing

- **[Contributing Guide](../CONTRIBUTING.md)** - Development guidelines
- **[Changelog](../CHANGELOG.md)** - Version history and changes

---

## Reference

Technical reference documentation.

- **[Validation Rules](reference/validation-rules.md)** - Input validation rules reference

---

## Project

Project planning and management documentation.

- **[Product Requirements (PRD)](project/prd.md)** - Complete requirements specification
- **[Project Summary](project/summary.md)** - Comprehensive project overview
- **[Deliverables](project/deliverables.md)** - Complete project inventory

---

## Directory Structure

```
docs/
├── README.md                    # This file
├── ACM_SWITCHOVER_RUNBOOK.md    # Operational runbook (protected)
├── getting-started/
│   ├── install.md               # Installation guide
│   └── container.md             # Container usage guide
├── operations/
│   ├── quickref.md              # Quick reference (includes container)
│   └── usage.md                 # Detailed usage guide
├── deployment/
│   ├── rbac-requirements.md     # RBAC requirements
│   └── rbac-deployment.md       # RBAC deployment guide
├── reference/
│   └── validation-rules.md      # Validation rules reference
├── development/
│   ├── architecture.md          # Architecture documentation
│   ├── testing.md               # Testing guide
│   ├── ci.md                    # CI/CD setup
│   ├── rbac-implementation.md   # RBAC implementation details
│   └── notes/                   # Implementation notes
│       ├── validation-and-error-handling.md
│       ├── exception-handling.md
│       └── shell-safety.md
└── project/
    ├── prd.md                   # Product requirements
    ├── summary.md               # Project summary
    └── deliverables.md          # Project deliverables
```

---

## Security

- **[Security Policy](../SECURITY.md)** - Security policy and vulnerability reporting
- **[RBAC Requirements](deployment/rbac-requirements.md)** - RBAC permissions and security controls
