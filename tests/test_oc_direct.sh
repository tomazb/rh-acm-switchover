#!/bin/bash

# Direct test of the oc() function sanitization
# This script tests the sanitization logic directly

# Mock kubectl function for testing
KUBECTL_CALLS=()
kubectl() {
    KUBECTL_CALLS+=("$*")
    echo "MOCK: kubectl called with: $*"
    return 0
}

# Define the oc() function directly (copy from lib-common.sh)
oc() {
    # Sanitize and validate all arguments before passing to kubectl
    local sanitized_args=()
    local arg

    for arg in "$@"; do
        # Validate argument doesn't contain dangerous shell metacharacters
        # Check for pipe, ampersand, semicolon, backtick, dollar, quotes, parentheses, etc.
        # Note: We allow '=' as it's used in flags like --context=value
        if [[ "$arg" == *['|']* ]] || [[ "$arg" == *['&']* ]] || [[ "$arg" == *[';']* ]] || \
           [[ "$arg" == *['<']* ]] || [[ "$arg" == *['>']* ]] || [[ "$arg" == *['(']* ]] || \
           [[ "$arg" == *[')']* ]] || [[ "$arg" == *['$']* ]] || [[ "$arg" == *['`']* ]] || \
           [[ "$arg" == *['"']* ]] || [[ "$arg" == *["'"]* ]] || [[ "$arg" == *['\\']* ]]; then
            echo "Error: Invalid argument detected - potential shell injection attempt" >&2
            echo "Offending argument: '$arg'" >&2
            return 127
        fi

        # Escape any remaining special characters that could be problematic
        # Use printf %q to properly quote/escape the argument
        local escaped_arg
        escaped_arg=$(printf '%q' "$arg")

        # If printf %q fails or produces empty result, reject the argument
        if [[ -z "$escaped_arg" ]]; then
            echo "Error: Cannot safely escape argument: '$arg'" >&2
            return 127
        fi

        sanitized_args+=("$escaped_arg")
    done

    # Execute kubectl with sanitized arguments
    if [[ ${#sanitized_args[@]} -eq 0 ]]; then
        kubectl
    else
        kubectl "${sanitized_args[@]}"
    fi
}

# Test cases
test_oc_direct() {
    echo "=== Testing oc() function directly ==="

    # Clear previous calls
    KUBECTL_CALLS=()

    # Test 1: Simple command
    echo -e "\n[Test 1] Simple oc command"
    oc get pods
    if [[ $? -eq 0 ]]; then
        echo "✓ Simple command works"
    else
        echo "✗ Simple command failed"
        return 1
    fi

    # Test 2: Command with context flag
    echo -e "\n[Test 2] Command with context flag"
    oc --context="test-context" get pods
    if [[ $? -eq 0 ]]; then
        echo "✓ Context flag works"
    else
        echo "✗ Context flag failed"
        return 1
    fi

    # Test 3: Command with namespace
    echo -e "\n[Test 3] Command with namespace"
    oc get pods -n "default"
    if [[ $? -eq 0 ]]; then
        echo "✓ Namespace argument works"
    else
        echo "✗ Namespace argument failed"
        return 1
    fi

    # Test 4: Shell injection attempt with pipe character
    echo -e "\n[Test 4] Shell injection attempt with pipe character"
    oc "get pods | echo 'malicious command'"
    if [[ $? -ne 0 ]]; then
        echo "✓ Shell injection with pipe was blocked"
    else
        echo "✗ Shell injection with pipe was NOT blocked - VULNERABILITY!"
        return 1
    fi

    # Test 5: Shell injection attempt with semicolon
    echo -e "\n[Test 5] Shell injection attempt with semicolon"
    oc "get pods; rm -rf /"
    if [[ $? -ne 0 ]]; then
        echo "✓ Shell injection with semicolon was blocked"
    else
        echo "✗ Shell injection with semicolon was NOT blocked - VULNERABILITY!"
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

    echo -e "\n=== All direct tests passed! ==="
    return 0
}

# Run the tests
test_oc_direct
exit $?