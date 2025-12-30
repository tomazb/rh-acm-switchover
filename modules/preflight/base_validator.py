"""Base validator classes for pre-flight validation."""

from typing import Any, Dict

from .reporter import ValidationReporter


class BaseValidator:
    """Base class for all pre-flight validators.

    Provides common functionality for validation result reporting.
    Validators implement run() methods with varying signatures based on their needs.
    """

    def __init__(self, reporter: ValidationReporter) -> None:
        """Initialize validator with a validation reporter.

        Args:
            reporter: ValidationReporter instance for collecting results
        """
        self.reporter = reporter

    def add_result(
        self,
        name: str,
        passed: bool,
        message: str,
        critical: bool = True,
    ) -> None:
        """Add a validation result to the reporter.

        Args:
            name: Name of the validation check
            passed: Whether the check passed
            message: Descriptive message about the result
            critical: Whether failure is critical (default: True)
        """
        self.reporter.add_result(name, passed, message, critical)
