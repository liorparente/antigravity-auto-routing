"""Hermetic unit tests for the isolated debate worker transport."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).with_name("debate_transport.py")
SPEC = importlib.util.spec_from_file_location("debate_transport", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
transport = importlib.util.module_from_spec(SPEC)
sys.modules["debate_transport"] = transport
SPEC.loader.exec_module(transport)


class DebateTransportTests(unittest.TestCase):
    def test_successful_invocation_returns_output_and_enforces_process_boundary(self) -> None:
        calls: list[dict[str, object]] = []

        def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append({"command": command, **kwargs})
            return subprocess.CompletedProcess(command, 0, stdout="worker output", stderr="")

        notifier = transport.RecurringFailureNotifier()
        worker = transport.DebateTransport(runner=runner, timeout_seconds=12.5, notifier=notifier)

        self.assertEqual(worker.invoke_worker("gpt-5.6-sol", "high", "Review"), "worker output")
        self.assertEqual(len(calls), 1)
        call = calls[0]
        self.assertEqual(call["timeout"], 12.5)
        self.assertIs(call["stdin"], subprocess.DEVNULL)
        self.assertEqual(call["env"]["IN_WORKER_ROUTING"], "true")  # type: ignore[index]
        self.assertIn("[WORKER-MODE: AGY-NESTED-EXEC]", call["command"][-1])  # type: ignore[index]

    def test_timeout_becomes_safe_abstention(self) -> None:
        def timeout_runner(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
            raise subprocess.TimeoutExpired("codex", 4.0, output=b"partial", stderr=b"stuck")

        worker = transport.DebateTransport(runner=timeout_runner, timeout_seconds=4.0)
        result = worker.invoke_critic_safe("gpt-5.6-sol", "high", "Review", "critic-a")

        self.assertEqual(result.critic_id, "critic-a")
        self.assertEqual(result.verdict, "abstain")
        self.assertEqual(result.confidence, 0.0)
        self.assertIn("timed out after 4.0 seconds", result.response)

    def test_unhandled_worker_exception_becomes_safe_abstention(self) -> None:
        def broken_runner(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
            raise OSError("worker executable missing")

        worker = transport.DebateTransport(runner=broken_runner)
        result = worker.invoke_critic_safe("gpt-5.6-sol", "high", "Review")

        self.assertEqual(result.verdict, "abstain")
        self.assertEqual(result.confidence, 0.0)
        self.assertIn("worker executable missing", result.response)

    def test_successful_critic_uses_normalized_vote_and_confidence(self) -> None:
        def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(
                command, 0, stdout='{"vote": "revise", "confidence": 0.75}', stderr=""
            )

        result = transport.DebateTransport(runner=runner).invoke_critic_safe(
            "gpt-5.6-sol", "high", "Review"
        )
        self.assertEqual((result.verdict, result.confidence), ("revise", 0.75))


class RecurringFailureNotifierTests(unittest.TestCase):
    def test_alert_triggers_at_threshold_and_repeated_errors_append(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            error_log = root / "ERRORS.md"
            error_log.write_text("# Existing notes\n", encoding="utf-8")
            notifier = transport.RecurringFailureNotifier(threshold=2)

            self.assertIsNone(notifier.record_failure("model-a", "first", root))
            alert = notifier.record_failure("model-a", "second", root)
            self.assertIn("failed 2 times consecutively", alert or "")
            notifier.record_failure("model-a", "third", root)

            contents = error_log.read_text(encoding="utf-8")
            self.assertTrue(contents.startswith("# Existing notes\n"))
            self.assertEqual(contents.count("## Recurring worker failure"), 2)
            self.assertIn("- Error: second", contents)
            self.assertIn("- Error: third", contents)

    def test_success_resets_failure_count(self) -> None:
        notifier = transport.RecurringFailureNotifier(threshold=2)
        self.assertIsNone(notifier.record_failure("model-a", "first"))
        notifier.record_success("model-a")
        self.assertIsNone(notifier.record_failure("model-a", "new first"))
        self.assertIn("failed 2 times", notifier.record_failure("model-a", "new second") or "")

    def test_creates_error_log_without_destroying_existing_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            notifier = transport.RecurringFailureNotifier(threshold=1)

            notifier.record_failure("model-a", "offline", root)
            self.assertTrue((root / "ERRORS.md").is_file())
            self.assertIn("offline", (root / "ERRORS.md").read_text(encoding="utf-8"))

    def test_unwritable_destination_does_not_mask_alert(self) -> None:
        notifier = transport.RecurringFailureNotifier(threshold=1)
        with patch.object(transport.os, "access", return_value=False):
            alert = notifier.record_failure("model-a", "offline", Path.cwd())
        self.assertIn("could not be written", alert or "")


if __name__ == "__main__":
    unittest.main()
