#!/bin/bash
#
# Generate a kubeconfig for a Kubernetes service account.
# This role-packaged copy keeps collection RBAC bootstrap independent of the
# repository working directory.

set -euo pipefail

KUBECONFIG_PATH=""
CONTEXT=""
USER_NAME=""
DURATION="48h"

usage() {
    echo "Usage: $0 [OPTIONS] <namespace> <service-account-name>" >&2
    echo "Options:" >&2
    echo "  --kubeconfig <path>       Kubeconfig to read cluster metadata from" >&2
    echo "  --context <context>       Kubernetes context to use" >&2
    echo "  --user <name>             User name in generated kubeconfig" >&2
    echo "  --token-duration <dur>    Token validity duration (default: 48h)" >&2
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --kubeconfig)
            if [[ -n "${2:-}" && "${2:0:1}" != "-" ]]; then
                KUBECONFIG_PATH="$2"
                shift 2
            else
                echo "Error: --kubeconfig requires a value" >&2
                usage
                exit 1
            fi
            ;;
        --context)
            if [[ -n "${2:-}" && "${2:0:1}" != "-" ]]; then
                CONTEXT="$2"
                shift 2
            else
                echo "Error: --context requires a value" >&2
                usage
                exit 1
            fi
            ;;
        --user)
            if [[ -n "${2:-}" && "${2:0:1}" != "-" ]]; then
                USER_NAME="$2"
                shift 2
            else
                echo "Error: --user requires a value" >&2
                usage
                exit 1
            fi
            ;;
        --token-duration)
            if [[ -n "${2:-}" && "${2:0:1}" != "-" ]]; then
                DURATION="$2"
                shift 2
            else
                echo "Error: --token-duration requires a value" >&2
                usage
                exit 1
            fi
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        -*)
            echo "Unknown option: $1" >&2
            usage
            exit 1
            ;;
        *)
            break
            ;;
    esac
done

NAMESPACE="${1:-}"
SA_NAME="${2:-}"

if [[ -z "$NAMESPACE" || -z "$SA_NAME" ]]; then
    usage
    exit 1
fi

KUBECTL_ARGS=()
if [[ -n "$KUBECONFIG_PATH" ]]; then
    KUBECTL_ARGS+=(--kubeconfig="$KUBECONFIG_PATH")
fi

KUBECTL_CONTEXT_ARGS=("${KUBECTL_ARGS[@]}")
if [[ -n "$CONTEXT" ]]; then
    KUBECTL_CONTEXT_ARGS+=(--context="$CONTEXT")
fi

if ! kubectl "${KUBECTL_CONTEXT_ARGS[@]}" get serviceaccount "$SA_NAME" -n "$NAMESPACE" &>/dev/null; then
    echo "Error: Service account '$SA_NAME' not found in namespace '$NAMESPACE'" >&2
    exit 1
fi

if [[ -n "$CONTEXT" ]]; then
    CLUSTER_NAME=$(kubectl "${KUBECTL_ARGS[@]}" config view -o jsonpath="{.contexts[?(@.name=='$CONTEXT')].context.cluster}")
    SERVER=$(kubectl "${KUBECTL_ARGS[@]}" config view -o jsonpath="{.clusters[?(@.name=='$CLUSTER_NAME')].cluster.server}")
    CA_DATA=$(kubectl "${KUBECTL_ARGS[@]}" config view --raw -o jsonpath="{.clusters[?(@.name=='$CLUSTER_NAME')].cluster.certificate-authority-data}")
else
    CLUSTER_NAME=$(kubectl "${KUBECTL_ARGS[@]}" config view --minify -o jsonpath='{.clusters[0].name}')
    SERVER=$(kubectl "${KUBECTL_ARGS[@]}" config view --minify -o jsonpath='{.clusters[0].cluster.server}')
    CA_DATA=$(kubectl "${KUBECTL_ARGS[@]}" config view --minify --raw -o jsonpath='{.clusters[0].cluster.certificate-authority-data}')
fi

if [[ -z "$SERVER" ]]; then
    echo "Error: Could not determine cluster server URL from context" >&2
    exit 1
fi

if [[ -z "$CA_DATA" ]]; then
    echo "Error: Could not determine cluster certificate-authority-data from context or kubeconfig" >&2
    exit 1
fi

if [[ -z "$USER_NAME" ]]; then
    if [[ -n "$CONTEXT" ]]; then
        USER_NAME="${CONTEXT}-${SA_NAME}"
    else
        USER_NAME="${CLUSTER_NAME}-${SA_NAME}"
    fi
fi

CONTEXT_NAME="${USER_NAME}@${CLUSTER_NAME}"
TOKEN=$(kubectl "${KUBECTL_CONTEXT_ARGS[@]}" create token "$SA_NAME" -n "$NAMESPACE" --duration="$DURATION")

if [[ -z "$TOKEN" ]]; then
    echo "Error: Failed to generate token for service account" >&2
    exit 1
fi

echo "Generated kubeconfig with token valid for ${DURATION}" >&2
echo "  User name: ${USER_NAME}" >&2
echo "  Context: ${CONTEXT_NAME}" >&2

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
    user: ${USER_NAME}
  name: ${CONTEXT_NAME}
current-context: ${CONTEXT_NAME}
users:
- name: ${USER_NAME}
  user:
    token: ${TOKEN}
KUBECONFIG
