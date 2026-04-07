"""Minimal smoke tests for the switchover exception hierarchy."""

import pytest

from lib.exceptions import (
    ConfigurationError,
    FatalError,
    SecurityValidationError,
    StateLoadError,
    StateLockError,
    SwitchoverError,
    TransientError,
    ValidationError,
)


@pytest.mark.unit
class TestExceptionHierarchy:
    """Guard the intended inheritance tree for custom exceptions."""

    @pytest.mark.parametrize(
        "exc_class, direct_parent",
        [
            (SwitchoverError, Exception),
            (TransientError, SwitchoverError),
            (FatalError, SwitchoverError),
            (ConfigurationError, FatalError),
            (ValidationError, ConfigurationError),
            (SecurityValidationError, ValidationError),
            (StateLoadError, FatalError),
            (StateLockError, FatalError),
        ],
    )
    def test_direct_parent_chain(self, exc_class, direct_parent):
        """Each custom exception should keep its direct parent intact."""
        assert exc_class.__bases__ == (direct_parent,)
        assert isinstance(exc_class("test"), direct_parent)
