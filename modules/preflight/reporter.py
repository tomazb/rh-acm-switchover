"""Validation result reporting for pre-flight checks."""

import logging
from typing import Any, Dict, List

logger = logging.getLogger("acm_switchover")


class ValidationReporter:
    """Collects validation results and handles summary logging."""

    def __init__(self) -> None:
        self.results: List[Dict[str, Any]] = []

    def add_result(
        self,
        check: str,
        passed: bool,
        message: str,
        critical: bool = True,
    ) -> None:
        """Add a validation result.

        Args:
            check: Name of the validation check
            passed: Whether the check passed
            message: Descriptive message about the result
            critical: Whether failure is critical (default: True)
        """
        self.results.append(
            {
                "check": check,
                "passed": passed,
                "message": message,
                "critical": critical,
            }
        )

        if passed:
            logger.info(f"✓ {check}: {message}")
        elif critical:
            logger.error(f"✗ {check}: {message}")
        else:
            logger.warning(f"⚠ {check}: {message}")

    def critical_failures(self) -> List[Dict[str, Any]]:
        """Get list of critical validation failures."""
        return [r for r in self.results if not r["passed"] and r["critical"]]

    def print_summary(self) -> None:
        """Print validation summary to the log."""
        passed = sum(1 for r in self.results if r["passed"])
        total = len(self.results)
        critical_failed = len(self.critical_failures())

        logger.info("\n" + "=" * 60)
        logger.info(f"Validation Summary: {passed}/{total} checks passed")

        if critical_failed > 0:
            logger.error(f"{critical_failed} critical validation(s) failed!")
            logger.info("\nFailed checks:")
            for result in self.critical_failures():
                logger.error(f"  ✗ {result['check']}: {result['message']}")
        else:
            logger.info("All critical validations passed!")

        logger.info("=" * 60 + "\n")
