# Security Policy

## Supported Versions

Currently supported versions:

| Version | Supported          |
| ------- | ------------------ |
| 1.0.x   | :white_check_mark: |

## Reporting a Vulnerability

If you discover a security vulnerability in this project, please report it responsibly:

### Where to Report

Please report security vulnerabilities by:

1. **Opening a private security advisory** on GitHub
2. **Emailing** the maintainers directly (see CONTRIBUTING.md for contact info)

**Do not** report security vulnerabilities through public GitHub issues.

### What to Include

When reporting a vulnerability, please include:

- Description of the vulnerability
- Steps to reproduce the issue
- Potential impact
- Suggested fix (if available)
- Your contact information

### Response Timeline

- **Initial Response**: Within 48 hours of report
- **Status Update**: Within 7 days
- **Fix Timeline**: Varies based on severity
  - Critical: Within 7 days
  - High: Within 30 days
  - Medium/Low: Next release cycle

### Security Best Practices

When using this tool:

1. **Credentials**: Never commit Kubernetes credentials or tokens
2. **State Files**: Protect `.state/` directory - contains switchover history
3. **Dry-Run**: Always use `--dry-run` first in production environments
4. **Validation**: Run `--validate-only` before actual switchover
5. **RBAC**: Use minimal required permissions for service accounts
6. **Audit**: Review logs and state files after each operation
7. **Backups**: Verify backups before switchover operations

## Security Scanning

This project includes automated security scanning:

- **Bandit**: Static security analysis for Python
- **Safety**: Dependency vulnerability checking
- **pip-audit**: PyPI package vulnerability scanning
- **CodeQL**: Semantic code analysis
- **Trivy**: Container and filesystem scanning
- **Gitleaks/TruffleHog**: Secrets detection

See `.github/workflows/security.yml` for implementation details.

## Known Security Considerations

### Kubernetes Access

This tool requires cluster-admin level access to both primary and secondary ACM hubs for:

- Managing ACM resources (MultiClusterHub, ManagedClusters)
- Scaling deployments and statefulsets
- Creating and managing backup/restore resources

**Recommendation**: Use dedicated service accounts with minimal required permissions.

### State File Security

The `.state/` directory contains operational history including:

- Timestamps of operations
- Configuration detected during execution
- Error messages (may contain cluster information)

**Recommendation**: 
- Add `.state/` to `.gitignore` (already included)
- Restrict file permissions: `chmod 600 .state/*.json`
- Do not share state files publicly

### Credentials in Backups

OADP backups contain sensitive data including:

- Kubernetes secrets
- Service account tokens
- Pull secrets
- Certificate authorities

**Recommendation**: 
- Ensure backup storage has appropriate access controls
- Use encryption for backup storage
- Regularly audit backup access logs

### TLS Hostname Verification

By default, the tool enforces TLS hostname verification for all Kubernetes API connections. However, the `--disable-hostname-verification` flag is available for non-production environments with self-signed certificates.

**Security Implications**:
- Disabling hostname verification makes connections vulnerable to man-in-the-middle attacks
- Each KubeClient instance has isolated TLS settings (does not affect other clients)
- Hostname verification status is logged for audit purposes

**Recommendations**:
- **Never** use `--disable-hostname-verification` in production environments
- Use properly signed certificates from trusted CAs
- If self-signed certificates are required, add them to the system trust store instead
- Review logs to ensure hostname verification is enabled for production operations

## Disclosure Policy

When a security vulnerability is reported:

1. We will investigate and assess the vulnerability
2. We will develop a fix if needed
3. We will coordinate disclosure with the reporter
4. We will release a fix and advisory
5. We will credit the reporter (unless they prefer anonymity)

Thank you for helping keep this project secure!
