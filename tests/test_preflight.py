"""Unit tests for preflight validation helpers."""

import os
import sys
import unittest

# Add parent directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from modules.preflight_validators import ValidationReporter


class TestValidationReporter(unittest.TestCase):
    """Tests for the ValidationReporter helper."""

    def setUp(self) -> None:
        self.reporter = ValidationReporter()

    def test_add_result_passed(self) -> None:
        self.reporter.add_result("demo", True, "all good", critical=True)

        self.assertEqual(len(self.reporter.results), 1)
        result = self.reporter.results[0]
        self.assertEqual(result["check"], "demo")
        self.assertTrue(result["passed"])
        self.assertEqual(result["message"], "all good")
        self.assertTrue(result["critical"])

    def test_add_result_warning(self) -> None:
        self.reporter.add_result("demo", False, "warn", critical=False)

        result = self.reporter.results[0]
        self.assertFalse(result["passed"])
        self.assertFalse(result["critical"])

    def test_critical_failures(self) -> None:
        self.reporter.add_result("ok", True, "fine", critical=True)
        self.reporter.add_result("bad", False, "nope", critical=True)
        self.reporter.add_result("warn", False, "heads up", critical=False)

        failures = self.reporter.critical_failures()
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["check"], "bad")


if __name__ == '__main__':
    unittest.main()
