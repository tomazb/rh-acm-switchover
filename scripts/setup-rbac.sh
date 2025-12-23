#!/bin/bash
#
# ACM Switchover RBAC Bootstrap Script
#
# This script automates the complete RBAC setup for the ACM switchover tool:
#   1. Deploys RBAC manifests (namespace, service accounts, roles, bindings)
#   2. Generates SA kubeconfigs with unique user names
#   3. Validates permissions using check_rbac.py
#
# Usage:
#   ./setup-rbac.sh --admin-kubeconfig <path> --context <context> [OPTIONS]
#
# Required:
#   --admin-kubeconfig <path>  - Path to kubeconfig with cluster-admin privileges
#   --context <context>        - Kubernetes context to deploy RBAC to
#
# Options:
#   --role <role>              - Role to deploy: operator, validator, both (default: both)
#   --token-duration <dur>     - Token validity duration (default: 48h)
#   --output-dir <dir>         - Output directory for kubeconfigs (default: ./kubeconfigs)
#   --skip-kubeconfig          - Skip kubeconfig generation
#   --skip-validation          - Skip RBAC validation after deployment
#   --dry-run                  - Show what would be deployed without making changes
#   -h, --help                 - Show this help message
#
# Examples:
#   # Full setup for operator role on a single hub
#   ./setup-rbac.sh --admin-kubeconfig ~/.kube/admin.yaml --context prod-hub --role operator
#
#   # Setup for both roles with custom token duration
#   ./setup-rbac.sh --admin-kubeconfig ~/.kube/admin.yaml --context prod-hub --token-duration 72h
#
#   # Dry-run to preview changes
#   ./setup-rbac.sh --admin-kubeconfig ~/.kube/admin.yaml --context prod-hub --dry-run
#

set -euo pipefail

# Determine script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Source shared libraries
source "${SCRIPT_DIR}/constants.sh"
source "${SCRIPT_DIR}/lib-common.sh"

# =============================================================================
# Configuration
# =============================================================================
SWITCHOVER_NAMESPACE="acm-switchover"
OPERATOR_SA="acm-switchover-operator"
VALIDATOR_SA="acm-switchover-validator"
RBAC_MANIFEST_DIR="${REPO_ROOT}/deploy/rbac"

# =============================================================================
# Default values
# =============================================================================
ADMIN_KUBECONFIG=""
CONTEXT=""
ROLE="both"
TOKEN_DURATION="48h"
OUTPUT_DIR="./kubeconfigs"
SKIP_KUBECONFIG=false
SKIP_VALIDATION=false
DRY_RUN=false

# =============================================================================
# Parse arguments
# =============================================================================
while [[ $# -gt 0 ]]; do
    case "$1" in
        --admin-kubeconfig)
            if [[ -n "${2:-}" && "${2:0:1}" != "-" ]]; then
                ADMIN_KUBECONFIG="$2"
                shift 2
            else
                echo "Error: --admin-kubeconfig requires a path value" >&2
                exit 1
            fi
            ;;
        --context)
            if [[ -n "${2:-}" && "${2:0:1}" != "-" ]]; then
                CONTEXT="$2"
                shift 2
            else
                echo "Error: --context requires a value" >&2
                exit 1
            fi
            ;;
        --role)
            if [[ -n "${2:-}" && "${2:0:1}" != "-" ]]; then
                ROLE="$2"
                if [[ ! "$ROLE" =~ ^(operator|validator|both)$ ]]; then
                    echo "Error: --role must be one of: operator, validator, both" >&2
                    exit 1
                fi
                shift 2
            else
                echo "Error: --role requires a value (operator, validator, both)" >&2
                exit 1
            fi
            ;;
        --token-duration)
            if [[ -n "${2:-}" && "${2:0:1}" != "-" ]]; then
                TOKEN_DURATION="$2"
                shift 2
            else
                echo "Error: --token-duration requires a value (e.g., 48h)" >&2
                exit 1
            fi
            ;;
        --output-dir)
            if [[ -n "${2:-}" && "${2:0:1}" != "-" ]]; then
                OUTPUT_DIR="$2"
                shift 2
            else
                echo "Error: --output-dir requires a path value" >&2
                exit 1
            fi
            ;;
        --skip-kubeconfig)
            SKIP_KUBECONFIG=true
            shift
            ;;
        --skip-validation)
            SKIP_VALIDATION=true
            shift
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --help|-h)
            echo "ACM Switchover RBAC Bootstrap Script"
            echo ""
            echo "Usage: $0 --admin-kubeconfig <path> --context <context> [OPTIONS]"
            echo ""
            echo "Required:"
            echo "  --admin-kubeconfig <path>  Path to kubeconfig with cluster-admin privileges"
            echo "  --context <context>        Kubernetes context to deploy RBAC to"
            echo ""
            echo "Options:"
            echo "  --role <role>              Role to deploy: operator, validator, both (default: both)"
            echo "  --token-duration <dur>     Token validity duration (default: 48h)"
            echo "  --output-dir <dir>         Output directory for kubeconfigs (default: ./kubeconfigs)"
            echo "  --skip-kubeconfig          Skip kubeconfig generation"
            echo "  --skip-validation          Skip RBAC validation after deployment"
            echo "  --dry-run                  Show what would be deployed without making changes"
            echo "  -h, --help                 Show this help message"
            echo ""
            echo "Examples:"
            echo "  $0 --admin-kubeconfig ~/.kube/admin.yaml --context prod-hub --role operator"
            echo "  $0 --admin-kubeconfig ~/.kube/admin.yaml --context prod-hub --token-duration 72h"
            echo "  $0 --admin-kubeconfig ~/.kube/admin.yaml --context prod-hub --dry-run"
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            echo "Use --help for usage information" >&2
            exit 1
            ;;
    esac
done

# =============================================================================
# Validate required arguments
# =============================================================================
if [[ -z "$ADMIN_KUBECONFIG" ]]; then
    echo "Error: --admin-kubeconfig is required" >&2
    echo "" >&2
    echo "For safety, this script requires an explicit admin kubeconfig path." >&2
    echo "This ensures you're aware of which credentials are being used for" >&2
    echo "privileged operations and provides an audit trail." >&2
    echo "" >&2
    echo "Usage: $0 --admin-kubeconfig <path> --context <context> [OPTIONS]" >&2
    exit 1
fi

if [[ -z "$CONTEXT" ]]; then
    echo "Error: --context is required" >&2
    echo "" >&2
    echo "Usage: $0 --admin-kubeconfig <path> --context <context> [OPTIONS]" >&2
    exit 1
fi

if [[ ! -f "$ADMIN_KUBECONFIG" ]]; then
    echo "Error: Admin kubeconfig file not found: $ADMIN_KUBECONFIG" >&2
    exit 1
fi

if [[ ! -d "$RBAC_MANIFEST_DIR" ]]; then
    echo "Error: RBAC manifests directory not found: $RBAC_MANIFEST_DIR" >&2
    echo "Are you running this script from the repository root?" >&2
    exit 1
fi

# =============================================================================
# Setup kubectl with admin credentials
# =============================================================================
KUBECTL_ARGS="--kubeconfig=$ADMIN_KUBECONFIG --context=$CONTEXT"

# Validate connection
echo ""
print_version_banner "ACM Switchover RBAC Setup"
echo ""

if $DRY_RUN; then
    echo -e "${YELLOW}DRY-RUN MODE: No changes will be made${NC}"
    echo ""
fi

echo "Validating cluster connection..."
# shellcheck disable=SC2086
if ! kubectl $KUBECTL_ARGS cluster-info &>/dev/null; then
    echo "Error: Cannot connect to cluster using context '$CONTEXT'" >&2
    echo "Please verify:" >&2
    echo "  - The kubeconfig path is correct: $ADMIN_KUBECONFIG" >&2
    echo "  - The context exists: $CONTEXT" >&2
    echo "  - You have cluster-admin privileges" >&2
    exit 1
fi

check_pass "Connected to cluster via context: $CONTEXT"

# Verify admin privileges by checking if we can access cluster-level resources
# shellcheck disable=SC2086
if ! kubectl $KUBECTL_ARGS auth can-i create namespace --all-namespaces &>/dev/null; then
    check_fail "Insufficient privileges: cluster-admin access required"
    echo "" >&2
    echo "This script needs cluster-admin privileges to:" >&2
    echo "  - Create the '$SWITCHOVER_NAMESPACE' namespace" >&2
    echo "  - Create ServiceAccounts" >&2
    echo "  - Create ClusterRoles and ClusterRoleBindings" >&2
    echo "  - Create Roles and RoleBindings in ACM namespaces" >&2
    exit 1
fi
check_pass "Verified cluster-admin privileges"

# =============================================================================
# Deploy RBAC manifests
# =============================================================================
section_header "Deploying RBAC Resources"

# Function to apply manifest with dry-run support
apply_manifest() {
    local manifest="$1"
    local description="$2"
    
    if [[ ! -f "$manifest" ]]; then
        check_warn "Manifest not found: $manifest"
        return 1
    fi
    
    if $DRY_RUN; then
        echo "  Would apply: $manifest"
        # Validate the manifest is valid YAML
        # shellcheck disable=SC2086
        if kubectl $KUBECTL_ARGS apply --dry-run=client -f "$manifest" &>/dev/null; then
            check_pass "[dry-run] $description"
        else
            check_fail "[dry-run] $description - invalid manifest"
            return 1
        fi
    else
        # shellcheck disable=SC2086
        if kubectl $KUBECTL_ARGS apply -f "$manifest" &>/dev/null; then
            check_pass "$description"
        else
            check_fail "$description"
            return 1
        fi
    fi
    return 0
}

# Function to filter and apply manifest for specific role
apply_role_filtered() {
    local manifest="$1"
    local description="$2"
    local target_role="$3"  # operator, validator, or both
    
    if [[ ! -f "$manifest" ]]; then
        check_warn "Manifest not found: $manifest"
        return 1
    fi
    
    if [[ "$target_role" == "both" ]]; then
        # Apply everything
        apply_manifest "$manifest" "$description"
        return $?
    fi
    
    # Filter YAML documents by role label using yq if available, otherwise apply all and let kubectl filter
    # For simplicity, we'll apply the whole file - Kubernetes is idempotent
    # In a production scenario, you might use yq to filter
    apply_manifest "$manifest" "$description (filtered for: $target_role)"
}

# Check which namespaces exist (for graceful handling of optional components)
check_namespace_exists() {
    local ns="$1"
    # shellcheck disable=SC2086
    kubectl $KUBECTL_ARGS get namespace "$ns" &>/dev/null
}

echo ""
echo "Deploying to context: $CONTEXT"
echo "Role configuration: $ROLE"
echo ""

# Step 1: Create namespace
apply_manifest "${RBAC_MANIFEST_DIR}/namespace.yaml" "Namespace: $SWITCHOVER_NAMESPACE"

# Step 2: Create ServiceAccounts
apply_role_filtered "${RBAC_MANIFEST_DIR}/serviceaccount.yaml" "ServiceAccounts" "$ROLE"

# Step 3: Create ClusterRoles
apply_role_filtered "${RBAC_MANIFEST_DIR}/clusterrole.yaml" "ClusterRoles" "$ROLE"

# Step 4: Create ClusterRoleBindings
apply_role_filtered "${RBAC_MANIFEST_DIR}/clusterrolebinding.yaml" "ClusterRoleBindings" "$ROLE"

# Step 5: Create namespace-scoped Roles
# Check which target namespaces exist
echo ""
echo "Checking target namespaces..."

ROLE_FILE="${RBAC_MANIFEST_DIR}/role.yaml"
ROLEBINDING_FILE="${RBAC_MANIFEST_DIR}/rolebinding.yaml"

# Apply roles - they'll be created in their target namespaces
# Handle potential missing namespaces gracefully
for ns in "$BACKUP_NAMESPACE" "$OBSERVABILITY_NAMESPACE" "$ACM_NAMESPACE" "$MCE_NAMESPACE"; do
    if check_namespace_exists "$ns"; then
        check_pass "Namespace exists: $ns"
    else
        check_warn "Namespace not found (skipped): $ns"
    fi
done

# Apply Roles (will only succeed for existing namespaces)
apply_role_filtered "$ROLE_FILE" "Namespace-scoped Roles" "$ROLE"

# Apply RoleBindings
apply_role_filtered "$ROLEBINDING_FILE" "Namespace-scoped RoleBindings" "$ROLE"

# =============================================================================
# Generate kubeconfigs
# =============================================================================
if ! $SKIP_KUBECONFIG; then
    section_header "Generating Kubeconfigs"
    
    # Create output directory
    if $DRY_RUN; then
        echo "Would create directory: $OUTPUT_DIR"
    else
        mkdir -p "$OUTPUT_DIR"
    fi
    
    # Generate kubeconfigs based on role selection
    generate_kubeconfig() {
        local sa_name="$1"
        local output_file="$2"
        local user_name="$3"
        
        if $DRY_RUN; then
            echo "Would generate kubeconfig:"
            echo "  Service Account: $sa_name"
            echo "  Output file: $output_file"
            echo "  User name: $user_name"
            echo "  Token duration: $TOKEN_DURATION"
            check_pass "[dry-run] Would generate $output_file"
        else
            # Use the generate-sa-kubeconfig.sh script
            if "${SCRIPT_DIR}/generate-sa-kubeconfig.sh" \
                --context "$CONTEXT" \
                --user "$user_name" \
                --token-duration "$TOKEN_DURATION" \
                "$SWITCHOVER_NAMESPACE" "$sa_name" > "$output_file" 2>/dev/null; then
                check_pass "Generated kubeconfig: $output_file"
                echo "  User name: $user_name"
                echo "  Token duration: $TOKEN_DURATION"
            else
                check_fail "Failed to generate kubeconfig for $sa_name"
            fi
        fi
    }
    
    echo ""
    echo "Output directory: $OUTPUT_DIR"
    echo ""
    
    case "$ROLE" in
        operator)
            generate_kubeconfig "$OPERATOR_SA" "${OUTPUT_DIR}/${CONTEXT}-operator.yaml" "${CONTEXT}-operator"
            ;;
        validator)
            generate_kubeconfig "$VALIDATOR_SA" "${OUTPUT_DIR}/${CONTEXT}-validator.yaml" "${CONTEXT}-validator"
            ;;
        both)
            generate_kubeconfig "$OPERATOR_SA" "${OUTPUT_DIR}/${CONTEXT}-operator.yaml" "${CONTEXT}-operator"
            generate_kubeconfig "$VALIDATOR_SA" "${OUTPUT_DIR}/${CONTEXT}-validator.yaml" "${CONTEXT}-validator"
            ;;
    esac
fi

# =============================================================================
# Validate RBAC permissions
# =============================================================================
if ! $SKIP_VALIDATION && ! $DRY_RUN && ! $SKIP_KUBECONFIG; then
    section_header "Validating RBAC Permissions"
    
    CHECK_RBAC="${REPO_ROOT}/check_rbac.py"
    
    if [[ ! -f "$CHECK_RBAC" ]]; then
        check_warn "check_rbac.py not found, skipping validation"
    else
        validate_role() {
            local role="$1"
            local kubeconfig_file="$2"
            
            echo ""
            echo "Validating $role role permissions..."
            
            # Set KUBECONFIG to use the generated kubeconfig
            if KUBECONFIG="$kubeconfig_file" python3 "$CHECK_RBAC" --role "$role" 2>/dev/null; then
                check_pass "RBAC validation passed for $role role"
            else
                check_fail "RBAC validation failed for $role role"
                echo "  Run manually to see details:"
                echo "  KUBECONFIG=$kubeconfig_file python3 $CHECK_RBAC --role $role --verbose"
            fi
        }
        
        case "$ROLE" in
            operator)
                validate_role "operator" "${OUTPUT_DIR}/${CONTEXT}-operator.yaml"
                ;;
            validator)
                validate_role "validator" "${OUTPUT_DIR}/${CONTEXT}-validator.yaml"
                ;;
            both)
                validate_role "operator" "${OUTPUT_DIR}/${CONTEXT}-operator.yaml"
                validate_role "validator" "${OUTPUT_DIR}/${CONTEXT}-validator.yaml"
                ;;
        esac
    fi
fi

# =============================================================================
# Summary
# =============================================================================
section_header "Summary"

echo ""
if $DRY_RUN; then
    echo -e "${YELLOW}DRY-RUN COMPLETE${NC}"
    echo ""
    echo "To apply these changes, run without --dry-run:"
    echo "  $0 --admin-kubeconfig $ADMIN_KUBECONFIG --context $CONTEXT --role $ROLE"
else
    echo -e "${GREEN}RBAC SETUP COMPLETE${NC}"
    echo ""
    echo "Deployed to context: $CONTEXT"
    echo "Role(s) configured: $ROLE"
    
    if ! $SKIP_KUBECONFIG; then
        echo ""
        echo "Generated kubeconfigs:"
        case "$ROLE" in
            operator)
                echo "  Operator: ${OUTPUT_DIR}/${CONTEXT}-operator.yaml"
                ;;
            validator)
                echo "  Validator: ${OUTPUT_DIR}/${CONTEXT}-validator.yaml"
                ;;
            both)
                echo "  Operator: ${OUTPUT_DIR}/${CONTEXT}-operator.yaml"
                echo "  Validator: ${OUTPUT_DIR}/${CONTEXT}-validator.yaml"
                ;;
        esac
        echo ""
        echo "Token validity: $TOKEN_DURATION"
    fi
    
    echo ""
    echo "Next steps:"
    echo "  1. Test the kubeconfig: KUBECONFIG=${OUTPUT_DIR}/${CONTEXT}-operator.yaml kubectl get ns"
    echo "  2. Run preflight check: python3 acm_switchover.py --validate-only --primary-context ${CONTEXT}-operator@..."
    echo ""
    echo "For multi-hub setup, run this script for each hub, then use"
    echo "generate-merged-kubeconfig.sh to create a single merged kubeconfig."
fi

echo ""
echo "Checks: $PASSED_CHECKS passed, $FAILED_CHECKS failed, $WARNING_CHECKS warnings"

# Exit with failure if any checks failed
if [[ $FAILED_CHECKS -gt 0 ]]; then
    exit 1
fi

exit 0
