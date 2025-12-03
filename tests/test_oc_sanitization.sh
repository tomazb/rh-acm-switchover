#!/bin/bash

# Test script for oc() function sanitization
# This script tests the security fix for shell injection vulnerability

# Source the common library
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$SCRIPT_DIR/scripts/lib-common.sh"

# Mock kubectl function for testing
kubectl() {
    echo "kubectl called with args: $*"
    return 0
}

# Test cases
test_oc_sanitization() {
    echo "=== Testing oc() function sanitization ==="

    # Test 1: Normal valid arguments
    echo -e "\n[Test 1] Normal valid arguments"
    if oc get pods -n default; then
        echo "✓ Normal arguments work correctly"
    else
        echo "✗ Normal arguments failed"
        return 1
    fi

    # Test 2: Arguments with spaces (should be handled properly)
    echo -e "\n[Test 2] Arguments with spaces"
    if oc get pods -n "my namespace"; then
        echo "✓ Arguments with spaces work correctly"
    else
        echo "✗ Arguments with spaces failed"
        return 1
    fi

    # Test 3: Shell injection attempt with pipe character
    echo -e "\n[Test 3] Shell injection attempt with pipe character"
    if oc "get pods | echo 'malicious command'"; then
        echo "✗ Shell injection with pipe was NOT blocked - VULNERABILITY!"
        return 1
    else
        echo "✓ Shell injection with pipe was blocked"
    fi

    # Test 4: Shell injection attempt with semicolon
    echo -e "\n[Test 4] Shell injection attempt with semicolon"
    if oc "get pods; rm -rf /"; then
        echo "✗ Shell injection with semicolon was NOT blocked - VULNERABILITY!"
        return 1
    else
        echo "✓ Shell injection with semicolon was blocked"
    fi

    # Test 5: Shell injection attempt with ampersand
    echo -e "\n[Test 5] Shell injection attempt with ampersand"
    if oc "get pods & echo 'malicious'"; then
        echo "✗ Shell injection with ampersand was NOT blocked - VULNERABILITY!"
        return 1
    else
        echo "✓ Shell injection with ampersand was blocked"
    fi

    # Test 6: Shell injection attempt with backticks
    echo -e "\n[Test 6] Shell injection attempt with backticks"
    if oc "get pods \`echo malicious\`"; then
        echo "✗ Shell injection with backticks was NOT blocked - VULNERABILITY!"
        return 1
    else
        echo "✓ Shell injection with backticks was blocked"
    fi

    # Test 7: Shell injection attempt with dollar sign
    echo -e "\n[Test 7] Shell injection attempt with dollar sign"
    if oc "get pods \$USER"; then
        echo "✗ Shell injection with dollar sign was NOT blocked - VULNERABILITY!"
        return 1
    else
        echo "✓ Shell injection with dollar sign was blocked"
    fi

    # Test 8: Shell injection attempt with parentheses
    echo -e "\n[Test 8] Shell injection attempt with parentheses"
    if oc "get pods (echo malicious)"; then
        echo "✗ Shell injection with parentheses was NOT blocked - VULNERABILITY!"
        return 1
    else
        echo "✓ Shell injection with parentheses was blocked"
    fi

    # Test 9: Shell injection attempt with quotes
    echo -e "\n[Test 9] Shell injection attempt with quotes"
    if oc 'get pods" | echo malicious'; then
        echo "✗ Shell injection with quotes was NOT blocked - VULNERABILITY!"
        return 1
    else
        echo "✓ Shell injection with quotes was blocked"
    fi

    # Test 10: Empty arguments
    echo -e "\n[Test 10] Empty arguments"
    if oc; then
        echo "✓ Empty arguments work correctly"
    else
        echo "✗ Empty arguments failed"
        return 1
    fi

    echo -e "\n=== All tests passed! ==="
    return 0
}

# Run the tests
test_oc_sanitization
exit $?