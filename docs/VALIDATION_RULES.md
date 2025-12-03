# Input Validation Rules and Patterns

This document describes the comprehensive input validation implemented in the ACM switchover automation tool to improve security, reliability, and user experience.

## Table of Contents

- [Overview](#overview)
- [Validation Categories](#validation-categories)
- [Kubernetes Resource Name Validation](#kubernetes-resource-name-validation)
- [Kubernetes Namespace Validation](#kubernetes-namespace-validation)
- [Kubernetes Label Validation](#kubernetes-label-validation)
- [Context Name Validation](#context-name-validation)
- [CLI Argument Validation](#cli-argument-validation)
- [Filesystem Path Validation](#filesystem-path-validation)
- [String Validation](#string-validation)
- [Error Handling](#error-handling)
- [Security Considerations](#security-considerations)
- [Testing Strategy](#testing-strategy)
- [Usage Examples](#usage-examples)

## Overview

The ACM switchover automation tool implements comprehensive input validation to:

1. **Improve Security**: Prevent path traversal, command injection, and other security vulnerabilities
2. **Enhance Reliability**: Ensure all inputs conform to expected formats before processing
3. **Provide Better User Experience**: Give clear, actionable error messages when validation fails
4. **Maintain Kubernetes Compliance**: Follow Kubernetes naming conventions and best practices

## Validation Categories

### 1. Kubernetes Resource Name Validation

**Pattern**: DNS-1123 subdomain format
**Regex**: `^[a-z]([-a-z0-9]*[a-z0-9])?$|^[a-z]([-a-z0-9]*[a-z0-9])?(\.[a-z]([-a-z0-9]*[a-z0-9])?)*$`
**Max Length**: 253 characters

**Rules**:
- Must contain only lowercase alphanumeric characters (`a-z`, `0-9`)
- May contain hyphens (`-`) and dots (`.`)
- Must start with a lowercase letter (not a digit)
- Must end with an alphanumeric character
- Dots cannot be consecutive or at start/end

**Valid Examples**:
- `my-pod`
- `my-pod-123`
- `my.pod.name`
- `my-pod-name`

**Invalid Examples**:
- `My-Pod` (uppercase)
- `my pod` (spaces)
- `my@pod` (invalid characters)
- `123pod` (starts with number)
- `my-pod-` (ends with hyphen)

### 2. Kubernetes Namespace Validation

**Pattern**: DNS-1123 label format
**Regex**: `^[a-z]([-a-z0-9]*[a-z0-9])?$`
**Max Length**: 63 characters

**Rules**:
- Must contain only lowercase alphanumeric characters (`a-z`, `0-9`)
- May contain hyphens (`-`)
- Must start with a lowercase letter (not a digit)
- Must end with an alphanumeric character
- Cannot contain dots (`.`) - this is for labels only

**Valid Examples**:
- `default`
- `kube-system`
- `my-namespace`
- `my-namespace-123`

**Invalid Examples**:
- `My-Namespace` (uppercase)
- `my namespace` (spaces)
- `my@namespace` (invalid characters)
- `123namespace` (starts with number)
- `my-namespace-` (ends with hyphen)

### 3. Kubernetes Label Validation

#### Label Keys

**Pattern**: Label key format
**Regex**: `^[a-zA-Z0-9]([a-zA-Z0-9-_.]*[a-zA-Z0-9])?$|^[a-zA-Z0-9]([a-zA-Z0-9-_.]*[a-zA-Z0-9])?/[a-zA-Z0-9]([a-zA-Z0-9-_.]*[a-zA-Z0-9])?$`
**Max Length**: 63 characters

**Rules**:
- May contain alphanumeric characters (`a-z`, `A-Z`, `0-9`)
- May contain hyphens (`-`), underscores (`_`), and dots (`.`)
- Optional prefix and name separated by slash (`/`)
- Must start and end with alphanumeric character

**Valid Examples**:
- `app`
- `app.kubernetes.io/name`
- `my-label`
- `my-label-123`

#### Label Values

**Pattern**: Label value format
**Regex**: `^[a-zA-Z0-9]([a-zA-Z0-9-_.]*[a-zA-Z0-9])?$`
**Max Length**: 63 characters

**Rules**:
- Same as label keys but cannot contain slashes

### 4. Context Name Validation

**Pattern**: More permissive than Kubernetes names
**Regex**: `^[A-Za-z0-9][A-Za-z0-9_.:\-/]*[A-Za-z0-9]$|^[A-Za-z0-9]$`
**Max Length**: 128 characters

**Rules**:
- May contain alphanumeric characters (`a-z`, `A-Z`, `0-9`)
- May contain hyphens (`-`), underscores (`_`), dots (`.`), colons (`:`), and forward slashes (`/`)
- Must start and end with alphanumeric character
- Accommodates default `oc login` contexts like `admin/api-ci-aws` or `default/api.example.com:6443/admin`

**Valid Examples**:
- `primary-hub`
- `secondary_hub`
- `my-cluster-123`
- `prod.acm.example.com`
- `dev-hub-2024`
- `admin/api-ci-aws`
- `default/api.example.com:6443/admin`
- `system:admin/api-ocp-cluster:6443`

**Invalid Examples**:
- `my cluster` (spaces)
- `my@cluster` (invalid characters)
- `/admin` (starts with slash)
- `admin/` (ends with slash)
- `:6443` (starts with colon)

### 5. CLI Argument Validation

#### Method Validation

**Valid Values**: `["passive", "full"]`

#### Old Hub Action Validation

**Valid Values**: `["secondary", "decommission", "none"]`

#### Log Format Validation

**Valid Values**: `["text", "json"]`

### 6. Filesystem Path Validation

**Security Rules**:
- **Path Traversal Prevention**: Blocks `..` as a path component (prevents `../malicious`)
- **Command Injection Prevention**: Blocks `~`, `$`, `{`, `}`, `|`, `&`, `;`, `<`, `>`, backtick characters
- **Absolute Path Restriction**: Only allows `/tmp/` and `/var/` prefixes for absolute paths
- **Empty Path Prevention**: Rejects empty or whitespace-only paths

**Valid Examples**:
- `state-file.json`
- `.state/switchover-state.json`
- `relative/path/to/file`
- `/tmp/valid-file`
- `/var/log/app.log`
- `my_file_123.txt`

**Invalid Examples**:
- `../malicious` (path traversal)
- `~/home/user` (home directory)
- `$HOME/file` (environment variable)
- `file|command` (pipe)
- `/etc/passwd` (absolute path outside allowed)
- `.hidden/file` (hidden file)

### 7. String Validation

**Rules**:
- Rejects empty strings
- Rejects whitespace-only strings
- Provides clear error messages indicating which field failed validation

## Error Handling

### Exception Hierarchy

```
ConfigurationError (base)
├── ValidationError (general validation failures)
└── SecurityValidationError (security-related failures)
```

### Error Message Format

All validation errors follow a consistent format:

1. **General Validation Errors**: Clear description of what's wrong and how to fix it
   - Example: `"Invalid context name 'my context'. Must consist of alphanumeric characters, '-', '_', or '.', and must start and end with an alphanumeric character"`

2. **Security Validation Errors**: Prefixed with "SECURITY:" and provide detailed explanation
   - Example: `"SECURITY: Invalid characters in test path '../malicious'. Path contains unsafe characters that could be used for path traversal or command injection. Only alphanumeric characters, hyphens, underscores, dots, and forward slashes are allowed."`

## Security Considerations

### Path Traversal Protection

- Blocks common path traversal patterns (`../`, `~/`, etc.)
- Prevents access to sensitive system directories
- Restricts absolute paths to safe directories only

### Command Injection Protection

- Blocks shell metacharacters (`|`, `&`, `;`, etc.)
- Prevents environment variable expansion (`$`, `{}`)
- Sanitizes all filesystem operations

### Kubernetes API Safety

- Validates all resource names before API calls
- Prevents invalid names from reaching Kubernetes API
- Provides early failure with clear error messages

## Testing Strategy

### Test Coverage

The validation module includes comprehensive test coverage:

1. **Positive Testing**: Valid inputs that should pass validation
2. **Negative Testing**: Invalid inputs that should fail validation
3. **Edge Case Testing**: Boundary conditions (max lengths, empty strings, etc.)
4. **Security Testing**: Path traversal attempts, command injection patterns
5. **Integration Testing**: End-to-end validation in real usage scenarios

### Test Categories

- **CLI Argument Validation Tests**: Context names, methods, actions, formats
- **Kubernetes Resource Validation Tests**: Names, namespaces, labels
- **Filesystem Validation Tests**: Path security, traversal prevention
- **String Validation Tests**: Empty/whitespace detection
- **Error Handling Tests**: Exception types, message formats
- **Integration Tests**: Full CLI argument validation workflows

## Usage Examples

### Basic Validation

```python
from lib.validation import InputValidator, ValidationError

try:
    # Validate a Kubernetes resource name
    InputValidator.validate_kubernetes_name("my-pod")

    # Validate a context name
    InputValidator.validate_context_name("primary-hub")

    # Validate a filesystem path
    InputValidator.validate_safe_filesystem_path("config.yaml", "config-file")

except ValidationError as e:
    print(f"Validation failed: {e}")
    # Handle validation error appropriately
except SecurityValidationError as e:
    print(f"Security validation failed: {e}")
    # Handle security error - may require logging and termination
```

### CLI Argument Validation

```python
from lib.validation import InputValidator

# Parse CLI arguments (using argparse)
args = parser.parse_args()

# Validate all arguments comprehensively
try:
    InputValidator.validate_all_cli_args(args)
except ValidationError as e:
    print(f"Error: {e}")
    sys.exit(1)
```

### Integration with Existing Code

The validation is integrated throughout the codebase:

1. **Main Entry Point** (`acm_switchover.py`):
   - Validates all CLI arguments before processing
   - Provides early failure with clear error messages

2. **Kubernetes Client** (`lib/kube_client.py`):
   - Validates all resource names and namespaces before API calls
   - Prevents invalid inputs from reaching Kubernetes API

3. **Preflight Validators** (`modules/preflight_validators.py`):
   - Validates context names and namespaces during preflight checks
   - Ensures all validation happens before critical operations

## Best Practices

### When to Validate

1. **Early Validation**: Validate inputs as early as possible
2. **Fail Fast**: Provide immediate feedback when validation fails
3. **Comprehensive Coverage**: Validate all external inputs
4. **Clear Messages**: Provide actionable error messages

### Validation Placement

1. **CLI Arguments**: Validate immediately after parsing
2. **API Parameters**: Validate before making API calls
3. **Filesystem Operations**: Validate paths before file operations
4. **Configuration Values**: Validate configuration file contents

### Error Handling

1. **Catch Specific Exceptions**: Handle `ValidationError` and `SecurityValidationError` separately
2. **Log Security Issues**: Security validation failures should be logged for audit
3. **User-Friendly Messages**: Provide clear guidance on how to fix validation errors
4. **Graceful Degradation**: Fail gracefully with appropriate exit codes

## Performance Considerations

- **Pattern Compilation**: Regex patterns are compiled once at module load
- **Early Validation**: Prevents expensive operations on invalid inputs
- **Minimal Overhead**: Validation adds negligible overhead to normal operations
- **Caching**: Consider caching validation results for repeated operations

## Future Enhancements

1. **Custom Validation Rules**: Allow configuration of validation patterns
2. **Internationalization**: Support for localized error messages
3. **Performance Optimization**: Benchmark and optimize validation performance
4. **Additional Security Checks**: Expand security validation coverage
5. **Validation Reporting**: Generate reports on validation failures and trends

## References

- [Kubernetes Naming Conventions](https://kubernetes.io/docs/concepts/overview/working-with-objects/names/)
- [OWASP Path Traversal Prevention](https://cheatsheetseries.owasp.org/cheatsheets/Path_Traversal_Prevention_Cheat_Sheet.html)
- [Python Input Validation Best Practices](https://docs.python.org/3/howto/doanddont.html#input-validation)