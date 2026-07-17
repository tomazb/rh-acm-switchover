"""Validation result reporting for pre-flight checks."""

import logging
from typing import Any, Dict, List

from lib.constants import (
    PREFLIGHT_PUBLIC_CHECK_CATEGORIES,
    PREFLIGHT_PUBLIC_CHECK_FALLBACK,
    PREFLIGHT_PUBLIC_CHECK_MAX_INPUT_LENGTH,
)

logger = logging.getLogger("acm_switchover")


def _public_check_category(check: Any) -> str:
    """Return a bounded, code-controlled category for public logging."""
    if (
        not isinstance(check, str)
        or not check
        or len(check) > PREFLIGHT_PUBLIC_CHECK_MAX_INPUT_LENGTH
        or not check.isprintable()
    ):
        return PREFLIGHT_PUBLIC_CHECK_FALLBACK

    for prefix, public_category in PREFLIGHT_PUBLIC_CHECK_CATEGORIES:
        if check == prefix or check.startswith(f"{prefix} ") or check.startswith(f"{prefix} ("):
            return public_category
    return PREFLIGHT_PUBLIC_CHECK_FALLBACK


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

        public_check = _public_check_category(check)
        if passed:
            logger.info("✓ %s: passed", public_check)
        elif critical:
            logger.error("✗ %s: failed", public_check)
        else:
            logger.warning("⚠ %s: warning", public_check)

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
                logger.error("  ✗ %s: failed", _public_check_category(result.get("check")))
        else:
            logger.info("All critical validations passed!")

        logger.info("=" * 60 + "\n")
