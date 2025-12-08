#!/bin/bash
#
# Generate kubeconfig for a Kubernetes service account
#
# This script generates a kubeconfig file that can be used to authenticate
# as a specific service account. The generated kubeconfig uses a short-lived
# token (24 hours by default) created via `kubectl create token`.
#
# Usage:
#   ./generate-sa-kubeconfig.sh <namespace> <service-account-name> [duration]
#
# Arguments:
#   namespace           - Namespace where the service account exists
#   service-account     - Name of the service account
#   duration            - Token duration (default: 24h)
#
# Examples:
#   # Generate kubeconfig for operator service account
#   ./generate-sa-kubeconfig.sh acm-switchover acm-switchover-operator > operator-kubeconfig.yaml
#
#   # Generate kubeconfig with custom token duration
#   ./generate-sa-kubeconfig.sh acm-switchover acm-switchover-operator 8h > operator-kubeconfig.yaml
#
# Prerequisites:
#   - kubectl configured with cluster access
#   - Service account must exist in the target namespace
#   - User must have permission to create tokens for the service account
#

set -euo pipefail

# Parse arguments
NAMESPACE="${1:-}"
SA_NAME="${2:-}"
DURATION="${3:-24h}"

# Validate required arguments
if [[ -z "$NAMESPACE" || -z "$SA_NAME" ]]; then
    echo "Usage: $0 <namespace> <service-account-name> [duration]" >&2
    echo "" >&2
    echo "Arguments:" >&2
    echo "  namespace           - Namespace where the service account exists" >&2
    echo "  service-account     - Name of the service account" >&2
    echo "  duration            - Token duration (default: 24h)" >&2
    echo "" >&2
    echo "Example:" >&2
    echo "  $0 acm-switchover acm-switchover-operator > kubeconfig.yaml" >&2
    exit 1
fi

# Check if service account exists
if ! kubectl get serviceaccount "$SA_NAME" -n "$NAMESPACE" &>/dev/null; then
    echo "Error: Service account '$SA_NAME' not found in namespace '$NAMESPACE'" >&2
    exit 1
fi

# Get cluster info from current context
CLUSTER_NAME=$(kubectl config view --minify -o jsonpath='{.clusters[0].name}')
SERVER=$(kubectl config view --minify -o jsonpath='{.clusters[0].cluster.server}')
CA_DATA=$(kubectl config view --minify --raw -o jsonpath='{.clusters[0].cluster.certificate-authority-data}')

# Validate cluster info
if [[ -z "$SERVER" ]]; then
    echo "Error: Could not determine cluster server URL from current context" >&2
    exit 1
fi

# Generate token
TOKEN=$(kubectl create token "$SA_NAME" -n "$NAMESPACE" --duration="$DURATION")

if [[ -z "$TOKEN" ]]; then
    echo "Error: Failed to generate token for service account" >&2
    exit 1
fi

# Generate kubeconfig
cat <<KUBECONFIG
apiVersion: v1
kind: Config
clusters:
- cluster:
    certificate-authority-data: ${CA_DATA}
    server: ${SERVER}
  name: ${CLUSTER_NAME}
contexts:
- context:
    cluster: ${CLUSTER_NAME}
    namespace: ${NAMESPACE}
    user: ${SA_NAME}
  name: ${SA_NAME}@${CLUSTER_NAME}
current-context: ${SA_NAME}@${CLUSTER_NAME}
users:
- name: ${SA_NAME}
  user:
    token: ${TOKEN}
KUBECONFIG
