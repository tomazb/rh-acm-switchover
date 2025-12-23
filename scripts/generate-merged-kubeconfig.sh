#!/bin/bash
#
# Generate and Merge Kubeconfigs for Multiple Clusters
#
# This script generates kubeconfigs for multiple clusters/contexts and merges them
# into a single kubeconfig file. It supports both ACM hub clusters and managed clusters.
#
# Usage:
#   ./generate-merged-kubeconfig.sh [OPTIONS] <context:role>[,<context:role>...]
#
# Required:
#   Context list - Comma-separated list of context:role pairs
#                  Roles: operator, validator
#
# Options:
#   --admin-kubeconfig <path>  - Admin kubeconfig for generating tokens (default: current)
#   --token-duration <dur>     - Token validity duration (default: 48h)
#   --output <file>            - Output merged kubeconfig file (default: ./merged-kubeconfig.yaml)
#   --namespace <ns>           - Namespace where SAs exist (default: acm-switchover)
#   --managed-cluster          - Flag for managed cluster contexts (uses different SA pattern)
#   -h, --help                 - Show this help message
#
# Examples:
#   # Generate merged kubeconfig for two hubs with operator role
#   ./generate-merged-kubeconfig.sh hub1:operator,hub2:operator
#
#   # Generate merged kubeconfig with custom output file
#   ./generate-merged-kubeconfig.sh --output ~/switchover.yaml hub1:operator,hub2:operator
#
#   # Generate with explicit admin kubeconfig
#   ./generate-merged-kubeconfig.sh --admin-kubeconfig ~/.kube/admin.yaml hub1:operator,hub2:validator
#
#   # Include managed clusters for klusterlet validation
#   ./generate-merged-kubeconfig.sh hub1:operator,hub2:operator,managed1:operator --managed-cluster
#

set -euo pipefail

# Determine script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source shared libraries
source "${SCRIPT_DIR}/constants.sh"
source "${SCRIPT_DIR}/lib-common.sh"

# =============================================================================
# Default values
# =============================================================================
ADMIN_KUBECONFIG=""
TOKEN_DURATION="48h"
OUTPUT_FILE="./merged-kubeconfig.yaml"
# Use centralized namespace from constants.sh (SWITCHOVER_NAMESPACE)
NAMESPACE="${SWITCHOVER_NAMESPACE:-acm-switchover}"
MANAGED_CLUSTER_MODE=false
CONTEXT_LIST=""

# Service account names from constants.sh
# These are exported by constants.sh, use defaults as fallback
OPERATOR_SA="${OPERATOR_SA:-acm-switchover-operator}"
VALIDATOR_SA="${VALIDATOR_SA:-acm-switchover-validator}"

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
        --token-duration)
            if [[ -n "${2:-}" && "${2:0:1}" != "-" ]]; then
                TOKEN_DURATION="$2"
                shift 2
            else
                echo "Error: --token-duration requires a value (e.g., 48h)" >&2
                exit 1
            fi
            ;;
        --output)
            if [[ -n "${2:-}" && "${2:0:1}" != "-" ]]; then
                OUTPUT_FILE="$2"
                shift 2
            else
                echo "Error: --output requires a file path" >&2
                exit 1
            fi
            ;;
        --namespace)
            if [[ -n "${2:-}" && "${2:0:1}" != "-" ]]; then
                NAMESPACE="$2"
                shift 2
            else
                echo "Error: --namespace requires a namespace name" >&2
                exit 1
            fi
            ;;
        --managed-cluster)
            MANAGED_CLUSTER_MODE=true
            shift
            ;;
        --help|-h)
            echo "Generate and Merge Kubeconfigs for Multiple Clusters"
            echo ""
            echo "Usage: $0 [OPTIONS] <context:role>[,<context:role>...]"
            echo ""
            echo "Context List Format:"
            echo "  Comma-separated list of context:role pairs"
            echo "  Roles: operator, validator"
            echo ""
            echo "Options:"
            echo "  --admin-kubeconfig <path>  Admin kubeconfig for generating tokens (default: current)"
            echo "  --token-duration <dur>     Token validity duration (default: 48h)"
            echo "  --output <file>            Output merged kubeconfig file (default: ./merged-kubeconfig.yaml)"
            echo "  --namespace <ns>           Namespace where SAs exist (default: acm-switchover)"
            echo "  --managed-cluster          Flag for managed cluster contexts"
            echo "  -h, --help                 Show this help message"
            echo ""
            echo "Examples:"
            echo "  $0 hub1:operator,hub2:operator"
            echo "  $0 --output ~/switchover.yaml hub1:operator,hub2:operator"
            echo "  $0 --admin-kubeconfig ~/.kube/admin.yaml hub1:operator,hub2:validator"
            exit 0
            ;;
        -*)
            echo "Unknown option: $1" >&2
            exit 1
            ;;
        *)
            # Assume this is the context list
            if [[ -z "$CONTEXT_LIST" ]]; then
                CONTEXT_LIST="$1"
            else
                echo "Error: Multiple context lists provided" >&2
                exit 1
            fi
            shift
            ;;
    esac
done

# =============================================================================
# Validate arguments
# =============================================================================
if [[ -z "$CONTEXT_LIST" ]]; then
    echo "Error: Context list is required" >&2
    echo "" >&2
    echo "Usage: $0 [OPTIONS] <context:role>[,<context:role>...]" >&2
    echo "" >&2
    echo "Examples:" >&2
    echo "  $0 hub1:operator,hub2:operator" >&2
    echo "  $0 hub1:operator,hub2:operator,hub3:validator" >&2
    exit 1
fi

# Validate admin kubeconfig if specified
if [[ -n "$ADMIN_KUBECONFIG" && ! -f "$ADMIN_KUBECONFIG" ]]; then
    echo "Error: Admin kubeconfig file not found: $ADMIN_KUBECONFIG" >&2
    exit 1
fi

# =============================================================================
# Main logic
# =============================================================================
print_version_banner "Merged Kubeconfig Generator"
echo ""

echo "Configuration:"
echo "  Token duration: $TOKEN_DURATION"
echo "  Output file: $OUTPUT_FILE"
echo "  Namespace: $NAMESPACE"
if [[ -n "$ADMIN_KUBECONFIG" ]]; then
    echo "  Admin kubeconfig: $ADMIN_KUBECONFIG"
fi
if $MANAGED_CLUSTER_MODE; then
    echo "  Mode: managed-cluster (using klusterlet namespace pattern)"
fi
echo ""

# Create temporary directory for individual kubeconfigs
TEMP_DIR=$(mktemp -d)
trap 'rm -rf "$TEMP_DIR"' EXIT

# Parse context list and generate kubeconfigs
section_header "Generating Individual Kubeconfigs"

KUBECONFIG_PATHS=""
GENERATED_COUNT=0
FAILED_COUNT=0

# Build admin kubeconfig args if specified
if [[ -n "$ADMIN_KUBECONFIG" ]]; then
    # Set KUBECONFIG for child process (generate-sa-kubeconfig.sh) to use admin credentials
    # NOTE: This export only affects this script's subshell and child processes.
    # The parent shell that invoked this script is NOT affected because exports
    # do not propagate upward to parent processes.
    export KUBECONFIG="$ADMIN_KUBECONFIG"
fi

# Split context list by comma
IFS=',' read -ra CONTEXTS <<< "$CONTEXT_LIST"

for entry in "${CONTEXTS[@]}"; do
    # Trim whitespace
    entry=$(echo "$entry" | xargs)
    
    # Split by colon
    if [[ "$entry" == *":"* ]]; then
        context="${entry%%:*}"
        role="${entry##*:}"
    else
        echo "Error: Invalid format '$entry' - expected 'context:role'" >&2
        echo "  Valid roles: operator, validator" >&2
        ((FAILED_COUNT++)) || true
        continue
    fi
    
    # Validate role
    if [[ ! "$role" =~ ^(operator|validator)$ ]]; then
        echo "Error: Invalid role '$role' for context '$context'" >&2
        echo "  Valid roles: operator, validator" >&2
        ((FAILED_COUNT++)) || true
        continue
    fi
    
    # Determine service account and namespace based on mode
    sa_namespace="$NAMESPACE"
    sa_name=""
    
    if $MANAGED_CLUSTER_MODE; then
        # For managed clusters, use the klusterlet namespace and service account
        # Managed clusters use 'open-cluster-management-agent' namespace
        # with klusterlet service accounts for validation operations
        sa_namespace="open-cluster-management-agent"
        if [[ "$role" == "operator" ]]; then
            sa_name="klusterlet"
        else
            sa_name="klusterlet"  # Validator uses same SA on managed clusters
        fi
    else
        # Hub clusters use the standard switchover service accounts
        if [[ "$role" == "operator" ]]; then
            sa_name="$OPERATOR_SA"
        else
            sa_name="$VALIDATOR_SA"
        fi
    fi
    
    # Generate unique user name: context-role pattern
    user_name="${context}-${role}"
    
    # Output file for this kubeconfig
    output_path="${TEMP_DIR}/${context}-${role}.yaml"
    
    echo ""
    echo "Generating kubeconfig for: $context ($role)"
    if $MANAGED_CLUSTER_MODE; then
        echo "  Mode: managed-cluster (namespace: $sa_namespace, SA: $sa_name)"
    fi
    
    # Generate kubeconfig using the determined namespace and service account
    if "${SCRIPT_DIR}/generate-sa-kubeconfig.sh" \
        --context "$context" \
        --user "$user_name" \
        --token-duration "$TOKEN_DURATION" \
        "$sa_namespace" "$sa_name" > "$output_path" 2>/dev/null; then
        
        check_pass "Generated: $context ($role) -> user: $user_name"
        
        # Add to merge list
        if [[ -z "$KUBECONFIG_PATHS" ]]; then
            KUBECONFIG_PATHS="$output_path"
        else
            KUBECONFIG_PATHS="${KUBECONFIG_PATHS}:${output_path}"
        fi
        ((GENERATED_COUNT++)) || true
    else
        check_fail "Failed to generate kubeconfig for $context"
        echo "  Verify that:" >&2
        echo "    - Context '$context' exists in your kubeconfig" >&2
        echo "    - ServiceAccount '$sa_name' exists in namespace '$sa_namespace'" >&2
        echo "    - You have permission to create tokens" >&2
        ((FAILED_COUNT++)) || true
    fi
done

# Check if any kubeconfigs were generated
if [[ $GENERATED_COUNT -eq 0 ]]; then
    echo "" >&2
    echo "Error: No kubeconfigs were successfully generated" >&2
    exit 1
fi

# =============================================================================
# Merge kubeconfigs
# =============================================================================
section_header "Merging Kubeconfigs"

echo ""
echo "Merging $GENERATED_COUNT kubeconfigs..."

# Create output directory if needed
OUTPUT_DIR=$(dirname "$OUTPUT_FILE")
if [[ ! -d "$OUTPUT_DIR" ]]; then
    mkdir -p "$OUTPUT_DIR"
fi

# Rename cluster names in each kubeconfig to avoid collision
# When merging kubeconfigs with identical cluster names (e.g., multiple clusters
# with cluster name "kubernetes"), tokens may overwrite each other.
# We rename each cluster to <context>-cluster for safety.
echo "Renaming cluster names to avoid collisions..."
for kubeconfig_file in ${KUBECONFIG_PATHS//:/ }; do
    if [[ -f "$kubeconfig_file" ]]; then
        # Get context name from filename (e.g., hub1-operator.yaml -> hub1)
        filename=$(basename "$kubeconfig_file" .yaml)
        context_name="${filename%-*}"  # Remove -operator or -validator suffix
        
        # Get current cluster name and rename it
        current_cluster=$(KUBECONFIG="$kubeconfig_file" kubectl config view -o jsonpath='{.clusters[0].name}' 2>/dev/null || echo "")
        if [[ -n "$current_cluster" ]]; then
            new_cluster="${context_name}-cluster"
            if [[ "$current_cluster" != "$new_cluster" ]]; then
                # Use yq if available for safer YAML manipulation, otherwise use sed
                if command -v yq &>/dev/null; then
                    yq -i "(.clusters[0].name = \"$new_cluster\") | (.contexts[0].context.cluster = \"$new_cluster\")" "$kubeconfig_file" 2>/dev/null || true
                else
                    # Fallback: kubectl doesn't support renaming clusters directly
                    # Create a new kubeconfig with renamed cluster
                    sed -i "s/cluster: ${current_cluster}/cluster: ${new_cluster}/g; s/name: ${current_cluster}/name: ${new_cluster}/g" "$kubeconfig_file" 2>/dev/null || true
                fi
            fi
        fi
    fi
done

# Merge using KUBECONFIG path stacking
# kubectl config view --merge --flatten combines all configs
# NOTE: KUBECONFIG is set only for this subcommand and does not affect the parent shell
if KUBECONFIG="$KUBECONFIG_PATHS" kubectl config view --merge --flatten > "$OUTPUT_FILE"; then
    check_pass "Merged kubeconfig written to: $OUTPUT_FILE"
else
    check_fail "Failed to merge kubeconfigs"
    exit 1
fi

# =============================================================================
# Summary
# =============================================================================
section_header "Summary"

echo ""
echo -e "${GREEN}KUBECONFIG GENERATION COMPLETE${NC}"
echo ""
echo "Generated kubeconfigs: $GENERATED_COUNT"
if [[ $FAILED_COUNT -gt 0 ]]; then
    echo -e "${YELLOW}Failed: $FAILED_COUNT${NC}"
fi
echo ""
echo "Output file: $OUTPUT_FILE"
echo "Token duration: $TOKEN_DURATION"
echo ""

# List contexts in merged kubeconfig
echo "Available contexts:"
KUBECONFIG="$OUTPUT_FILE" kubectl config get-contexts -o name | while read -r ctx; do
    echo "  - $ctx"
done

echo ""
echo "Usage:"
echo "  export KUBECONFIG=$OUTPUT_FILE"
echo "  kubectl config get-contexts"
echo "  kubectl --context <context-name> get ns"
echo ""
echo "Or use with acm_switchover.py:"
echo "  KUBECONFIG=$OUTPUT_FILE python3 acm_switchover.py \\"
echo "    --primary-context <primary-context> \\"
echo "    --secondary-context <secondary-context> \\"
echo "    --method passive --validate-only"

# Exit with warning if some failed
if [[ $FAILED_COUNT -gt 0 ]]; then
    exit 1
fi

exit 0
