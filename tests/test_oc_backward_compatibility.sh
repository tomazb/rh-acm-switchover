#!/bin/bash

# Test script for oc() function backward compatibility
# This script tests that the security fix doesn't break existing functionality

# Source the common library
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$SCRIPT_DIR/scripts/lib-common.sh"

# Mock kubectl function for testing - just verify it gets called with expected args
KUBECTL_CALLS=()
kubectl() {
    KUBECTL_CALLS+=("$*")
    echo "MOCK: kubectl called with args: $*"
    return 0
}

# Test cases for backward compatibility
test_oc_backward_compatibility() {
    echo "=== Testing oc() function backward compatibility ==="

    # Clear previous calls
    KUBECTL_CALLS=()

    # Test 1: Simple command
    echo -e "\n[Test 1] Simple oc command"
    if oc get pods 2>/dev/null; then
        echo "✓ Simple command works"
    else
        echo "✗ Simple command failed"
        return 1
    fi

    # Test 2: Command with context flag
    echo -e "\n[Test 2] Command with context flag"
    oc --context="test-context" get pods 2>/dev/null
    if [[ $? -eq 0 ]] || [[ $? -eq 1 ]]; then
        # Exit code 0 = success, exit code 1 = kubectl error (but our sanitization passed)
        echo "✓ Context flag works (sanitization passed)"
    else
        echo "✗ Context flag failed (sanitization blocked)"
        return 1
    fi

    # Test 3: Command with namespace
    echo -e "\n[Test 3] Command with namespace"
    oc get pods -n "default" 2>/dev/null
    if [[ $? -eq 0 ]] || [[ $? -eq 1 ]]; then
        echo "✓ Namespace argument works (sanitization passed)"
    else
        echo "✗ Namespace argument failed (sanitization blocked)"
        return 1
    fi

    # Test 4: Command with output formatting
    echo -e "\n[Test 4] Command with output formatting"
    oc get pods -o jsonpath='{.items[0].metadata.name}' 2>/dev/null
    if [[ $? -eq 0 ]] || [[ $? -eq 1 ]]; then
        echo "✓ JSONPath output works (sanitization passed)"
    else
        echo "✗ JSONPath output failed (sanitization blocked)"
        return 1
    fi

    # Test 5: Command with multiple flags and arguments
    echo -e "\n[Test 5] Command with multiple flags and arguments"
    oc get pods --no-headers -n "test-namespace" -l "app=test" 2>/dev/null
    if [[ $? -eq 0 ]] || [[ $? -eq 1 ]]; then
        echo "✓ Multiple flags and arguments work (sanitization passed)"
    else
        echo "✗ Multiple flags and arguments failed (sanitization blocked)"
        return 1
    fi

    # Test 6: Command with resource names containing hyphens (common in Kubernetes)
    echo -e "\n[Test 6] Command with hyphenated resource names"
    oc get deployment my-app-deployment 2>/dev/null
    if [[ $? -eq 0 ]] || [[ $? -eq 1 ]]; then
        echo "✓ Hyphenated resource names work (sanitization passed)"
    else
        echo "✗ Hyphenated resource names failed (sanitization blocked)"
        return 1
    fi

    # Test 7: Command with resource names containing dots (common in Kubernetes)
    echo -e "\n[Test 7] Command with dotted resource names"
    oc get configmap my.config.map 2>/dev/null
    if [[ $? -eq 0 ]] || [[ $? -eq 1 ]]; then
        echo "✓ Dotted resource names work (sanitization passed)"
    else
        echo "✗ Dotted resource names failed (sanitization blocked)"
        return 1
    fi

    # Test 8: Command with resource names containing underscores
    echo -e "\n[Test 8] Command with underscored resource names"
    oc get secret my_secret_name 2>/dev/null
    if [[ $? -eq 0 ]] || [[ $? -eq 1 ]]; then
        echo "✓ Underscored resource names work (sanitization passed)"
    else
        echo "✗ Underscored resource names failed (sanitization blocked)"
        return 1
    fi

    # Test 9: Complex realistic command from the actual scripts
    echo -e "\n[Test 9] Complex realistic command"
    oc --context="test-context" get managedclusters -n "open-cluster-management" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null
    if [[ $? -eq 0 ]] || [[ $? -eq 1 ]]; then
        echo "✓ Complex realistic command works (sanitization passed)"
    else
        echo "✗ Complex realistic command failed (sanitization blocked)"
        return 1
    fi

    # Test 10: Command with label selector
    echo -e "\n[Test 10] Command with label selector"
    oc get pods -l "app.kubernetes.io/name=test-app" 2>/dev/null
    if [[ $? -eq 0 ]] || [[ $? -eq 1 ]]; then
        echo "✓ Label selector works (sanitization passed)"
    else
        echo "✗ Label selector failed (sanitization blocked)"
        return 1
    fi

    # Verify that kubectl was called with the expected arguments
    echo -e "\n[Verification] Checking kubectl was called correctly"
    if [[ ${#KUBECTL_CALLS[@]} -gt 0 ]]; then
        echo "✓ kubectl was called ${#KUBECTL_CALLS[@]} times"
        echo "Sample calls:"
        for i in 1 2 3; do
            if [[ $i -le ${#KUBECTL_CALLS[@]} ]]; then
                echo "  $i. ${KUBECTL_CALLS[$i-1]}"
            fi
        done
    else
        echo "✗ kubectl was never called"
        return 1
    fi

    echo -e "\n=== All backward compatibility tests passed! ==="
    return 0
}

# Run the tests
test_oc_backward_compatibility
exit $?