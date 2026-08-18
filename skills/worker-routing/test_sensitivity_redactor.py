"""Hermetic coverage for pure sensitivity scanning and safe identities."""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("sensitivity_redactor.py")
SPEC = importlib.util.spec_from_file_location("sensitivity_redactor", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
sensitivity_redactor = importlib.util.module_from_spec(SPEC)
sys.modules["sensitivity_redactor"] = sensitivity_redactor
SPEC.loader.exec_module(sensitivity_redactor)


class SensitivityRedactorTests(unittest.TestCase):
    def test_scan_returns_marker_not_surrounding_sensitive_text(self) -> None:
        marker = sensitivity_redactor.scan_sensitivity_markers("never print API_KEY=top-secret")

        self.assertEqual(marker, "api_key")
        self.assertNotIn("top-secret", marker or "")

    def test_scan_is_case_insensitive_and_first_match_wins(self) -> None:
        self.assertEqual(
            sensitivity_redactor.scan_sensitivity_markers("password and BEARER token"),
            "bearer ",
        )

    def test_scan_without_marker_returns_none(self) -> None:
        self.assertIsNone(sensitivity_redactor.scan_sensitivity_markers("ordinary task"))

    def test_halted_identity_is_random_and_never_derived_from_task_text(self) -> None:
        identity = sensitivity_redactor.derive_safe_task_identity(
            "contains secret",
            outcome="sensitivity_halt",
            token_factory=lambda size: "random-token",
        )

        self.assertEqual(identity.task_id, "random-token")
        self.assertTrue(identity.sensitivity_halted)
        self.assertEqual(identity.marker, "secret")
        self.assertFalse(identity.caller_supplied)

    def test_caller_identity_is_preserved(self) -> None:
        identity = sensitivity_redactor.derive_safe_task_identity(
            "ordinary task", "task-123"
        )

        self.assertEqual(identity.task_id, "task-123")
        self.assertFalse(identity.sensitivity_halted)
        self.assertIsNone(identity.marker)
        self.assertTrue(identity.caller_supplied)

    def test_halt_preserves_a_caller_supplied_identity(self) -> None:
        identity = sensitivity_redactor.derive_safe_task_identity(
            "password=top-secret",
            "leaking-id",
            outcome="sensitivity_halt",
            token_factory=lambda size: "random-token",
        )

        self.assertEqual(identity.task_id, "leaking-id")
        self.assertTrue(identity.sensitivity_halted)
        self.assertEqual(identity.marker, "password")
        self.assertTrue(identity.caller_supplied)


if __name__ == "__main__":
    unittest.main()
