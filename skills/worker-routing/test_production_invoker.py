#!/usr/bin/env python3
"""Unit tests for the production worker invoker."""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock

MODULE_PATH = Path(__file__).with_name("production_invoker.py")
LEARNING_JOURNAL_PATH = Path(__file__).with_name("learning_journal.py")

learning_journal_spec = importlib.util.spec_from_file_location(
    "learning_journal", LEARNING_JOURNAL_PATH
)
assert learning_journal_spec is not None and learning_journal_spec.loader is not None
learning_journal = importlib.util.module_from_spec(learning_journal_spec)
sys.modules["learning_journal"] = learning_journal
learning_journal_spec.loader.exec_module(learning_journal)

SPEC = importlib.util.spec_from_file_location("production_invoker", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
production_invoker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(production_invoker)


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


class BuildWorkerCommandTests(unittest.TestCase):
    def test_codex_command_prepends_worker_token(self) -> None:
        command = production_invoker.build_worker_command(
            "gpt-5.6-sol", "high", "Review this plan"
        )

        self.assertEqual(
            command,
            [
                "codex",
                "exec",
                "--model",
                "gpt-5.6-sol",
                "-c",
                'model_reasoning_effort="high"',
                "-s",
                "workspace-write",
                "[WORKER-MODE: AGY-NESTED-EXEC] Review this plan",
            ],
        )

    def test_claude_command_preserves_existing_worker_token(self) -> None:
        prompt = "[WORKER-MODE: AGY-NESTED-EXEC] Draft the plan"

        command = production_invoker.build_worker_command("claude-sonnet-5", "high", prompt)

        self.assertEqual(
            command,
            [
                "claude",
                "-p",
                "--no-session-persistence",
                "--model",
                "claude-sonnet-5",
                "--effort",
                "high",
                "--allow-dangerously-skip-permissions",
                "--permission-mode",
                "bypassPermissions",
                prompt,
            ],
        )

    def test_prepends_token_when_token_is_only_mentioned_mid_prompt(self) -> None:
        prompt = "Explain why [WORKER-MODE: AGY-NESTED-EXEC] is required"

        command = production_invoker.build_worker_command("gpt-5.6-sol", "high", prompt)

        self.assertEqual(command[-1], f"{production_invoker.WORKER_MODE_TOKEN} {prompt}")

    def test_display_model_names_resolve_to_cli_model_ids(self) -> None:
        cases = (
            ("Claude Opus 5 (Thinking)", "claude", "claude-opus-5"),
            ("Claude Sonnet 5 (Thinking)", "claude", "claude-sonnet-5"),
            ("Codex 5.6 Sol", "codex", "gpt-5.6-sol"),
            ("Gemini 3.6 Flash (High)", "agy", None),
        )

        for display_name, executable, cli_model in cases:
            with self.subTest(display_name=display_name):
                command = production_invoker.build_worker_command(display_name, "high", "Work")
                self.assertEqual(command[0], executable)
                if cli_model is not None:
                    self.assertEqual(command[command.index("--model") + 1], cli_model)


class AdvisoryConsultationIntegrationTests(unittest.TestCase):
    def test_defaults_to_production_invoke_worker(self) -> None:
        advisory_path = Path(__file__).with_name("advisory_consultation.py")
        advisory_spec = importlib.util.spec_from_file_location(
            "advisory_consultation_for_production_invoker_test", advisory_path
        )
        assert advisory_spec is not None and advisory_spec.loader is not None
        advisory_consultation = importlib.util.module_from_spec(advisory_spec)
        previous_advisory = sys.modules.get(advisory_spec.name)
        sys.modules[advisory_spec.name] = advisory_consultation
        try:
            advisory_spec.loader.exec_module(advisory_consultation)
        finally:
            if previous_advisory is None:
                del sys.modules[advisory_spec.name]
            else:
                sys.modules[advisory_spec.name] = previous_advisory

        calls: list[tuple[str, str, str]] = []

        def fake_invoke_worker(model: str, effort: str, prompt: str) -> str:
            calls.append((model, effort, prompt))
            return "Planner plan" if len(calls) == 1 else "VERDICT: APPROVE"

        def fake_make_journaled_invoke_worker(task: object, *, root_dir: Path) -> object:
            # This test's own subject is "the production default is reached
            # and used end-to-end" — journaling itself is covered directly by
            # JournaledInvokeWorkerTests below, so the fake factory here just
            # hands back the unwrapped fake rather than re-implementing
            # instrumentation.
            return fake_invoke_worker

        previous_module = sys.modules.get("production_invoker")
        production_module = types.ModuleType("production_invoker")
        production_module.invoke_worker = fake_invoke_worker  # type: ignore[attr-defined]
        production_module.make_journaled_invoke_worker = (  # type: ignore[attr-defined]
            fake_make_journaled_invoke_worker
        )
        sys.modules["production_invoker"] = production_module
        try:
            with tempfile.TemporaryDirectory() as tmp:
                result = advisory_consultation.run_advisory_consultation_debate(
                    "Plan the implementation", root_dir=Path(tmp)
                )
        finally:
            if previous_module is None:
                del sys.modules["production_invoker"]
            else:
                sys.modules["production_invoker"] = previous_module

        self.assertTrue(result.consensus_reached)
        self.assertEqual(len(calls), 2)

    def test_agy_command_uses_tokenized_prompt(self) -> None:
        command = production_invoker.build_worker_command("gemini-3.6-flash", "medium", "Research")

        self.assertEqual(
            command,
            ["agy", "-p", "[WORKER-MODE: AGY-NESTED-EXEC] Research"],
        )

    def test_unknown_model_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported worker model"):
            production_invoker.build_worker_command("acme-model", "low", "Do work")


class InvokeWorkerTests(unittest.TestCase):
    def test_invokes_injected_runner_with_noninteractive_environment(self) -> None:
        runner = Mock(return_value=subprocess.CompletedProcess([], 0, "worker output", ""))

        output = production_invoker.invoke_worker(
            "gpt-5.6-terra", "medium", "Implement it", timeout=12.5, runner=runner
        )

        self.assertEqual(output, "worker output")
        command = runner.call_args.args[0]
        kwargs = runner.call_args.kwargs
        self.assertEqual(command[0:2], ["codex", "exec"])
        self.assertEqual(command[-1], "[WORKER-MODE: AGY-NESTED-EXEC] Implement it")
        self.assertEqual(kwargs["timeout"], 12.5)
        self.assertIs(kwargs["stdin"], subprocess.DEVNULL)
        self.assertTrue(kwargs["text"])
        self.assertTrue(kwargs["capture_output"])
        self.assertFalse(kwargs["check"])
        self.assertEqual(kwargs["env"]["IN_WORKER_ROUTING"], "true")
        self.assertEqual(kwargs["env"].get("PATH"), os.environ.get("PATH"))

    def test_nonzero_exit_fails_closed_with_output_diagnostics(self) -> None:
        runner = Mock(
            return_value=subprocess.CompletedProcess([], 17, "partial stdout", "failure stderr")
        )

        with self.assertRaisesRegex(
            RuntimeError, r"exit code 17.*partial stdout.*failure stderr"
        ):
            production_invoker.invoke_worker("claude-opus-5", "ultra", "Plan", runner=runner)

    def test_timeout_fails_closed_with_output_diagnostics(self) -> None:
        timeout = subprocess.TimeoutExpired(
            ["agy"], 30, output="partial stdout", stderr="timeout stderr"
        )
        runner = Mock(side_effect=timeout)

        with self.assertRaisesRegex(
            RuntimeError, r"timed out.*partial stdout.*timeout stderr"
        ):
            production_invoker.invoke_worker("agy", "high", "Research", runner=runner)


class _FakeClock:
    """A scripted monotonic clock: pops one value per call."""

    def __init__(self, times: list[float]) -> None:
        self._times = list(times)

    def __call__(self) -> float:
        return self._times.pop(0)


class JournaledInvokeWorkerTests(unittest.TestCase):
    """`make_journaled_invoke_worker` wraps `invoke_worker` from the outside.

    Its own signature never changes (still `(model, effort, prompt) -> str`),
    and the worker's result or exception reaches the caller unchanged either
    way; the wrapping is purely a side effect — exactly one
    `WorkerExecutionRecord` per call, `success` matching what actually
    happened.
    """

    JOURNAL_RELATIVE_PATH = Path(".ralph") / "learning_journal.jsonl"

    def _task(self, task_id: str = "task-1") -> object:
        return learning_journal.TaskLabel.for_task(task_id, task_type="feature")

    def test_success_appends_one_record_with_full_fields(self) -> None:
        runner = Mock(return_value=subprocess.CompletedProcess([], 0, "worker output", ""))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            journaled = production_invoker.make_journaled_invoke_worker(
                self._task("task-success"),
                root_dir=root,
                runner=runner,
                clock=_FakeClock([100.0, 101.5]),
            )
            output = journaled("claude-sonnet-5", "high", "Implement it")
            records = _read_jsonl(root / self.JOURNAL_RELATIVE_PATH)

        self.assertEqual(output, "worker output")
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["kind"], "worker_execution")
        self.assertEqual(record["task_id"], "task-success")
        self.assertEqual(record["task_type"], "feature")
        self.assertEqual(record["duration_ms"], 1500)
        self.assertEqual(
            record["cost_estimate_usd"],
            production_invoker.estimate_cost_usd("claude-sonnet-5", 1500),
        )
        self.assertTrue(record["success"])
        self.assertEqual(record["retry_count"], 0)
        self.assertEqual(record["effort"], "high")
        self.assertEqual(record["model_id"], "claude-sonnet-5")
        self.assertEqual(record["model_family"], "claude")

    def test_nonzero_exit_appends_one_failed_record_and_reraises(self) -> None:
        runner = Mock(
            return_value=subprocess.CompletedProcess([], 17, "partial stdout", "failure stderr")
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            journaled = production_invoker.make_journaled_invoke_worker(
                self._task("task-nonzero"),
                root_dir=root,
                runner=runner,
                clock=_FakeClock([10.0, 10.25]),
            )
            with self.assertRaisesRegex(RuntimeError, "exit code 17"):
                journaled("gpt-5.6-sol", "medium", "Do work")
            records = _read_jsonl(root / self.JOURNAL_RELATIVE_PATH)

        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["task_id"], "task-nonzero")
        self.assertFalse(record["success"])
        self.assertEqual(record["duration_ms"], 250)
        self.assertEqual(record["model_id"], "gpt-5.6-sol")
        self.assertEqual(record["model_family"], "codex")

    def test_timeout_appends_one_failed_record_and_reraises(self) -> None:
        timeout = subprocess.TimeoutExpired(
            ["agy"], 30, output="partial stdout", stderr="timeout stderr"
        )
        runner = Mock(side_effect=timeout)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            journaled = production_invoker.make_journaled_invoke_worker(
                self._task("task-timeout"),
                root_dir=root,
                runner=runner,
                clock=_FakeClock([0.0, 30.0]),
            )
            with self.assertRaisesRegex(RuntimeError, "timed out"):
                journaled("agy", "high", "Research")
            records = _read_jsonl(root / self.JOURNAL_RELATIVE_PATH)

        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["task_id"], "task-timeout")
        self.assertFalse(record["success"])
        self.assertEqual(record["duration_ms"], 30000)
        self.assertEqual(record["model_id"], "agy")
        self.assertEqual(record["model_family"], "agy")

    def test_journal_write_failure_never_breaks_the_observed_invocation(self) -> None:
        runner = Mock(return_value=subprocess.CompletedProcess([], 0, "worker output", ""))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # `.ralph` is a plain file, not a directory: `append_journal_record`'s
            # `mkdir(parents=True, exist_ok=True)` fails with an `OSError` no
            # matter what record it's asked to write.
            (root / ".ralph").write_text("not a directory")
            journaled = production_invoker.make_journaled_invoke_worker(
                self._task("task-unwritable"), root_dir=root, runner=runner
            )

            output = journaled("claude-opus-5", "ultra", "Plan")

        self.assertEqual(output, "worker output")

    def test_journal_write_failure_never_breaks_a_worker_exception_either(self) -> None:
        runner = Mock(
            return_value=subprocess.CompletedProcess([], 3, "partial stdout", "failure stderr")
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".ralph").write_text("not a directory")
            journaled = production_invoker.make_journaled_invoke_worker(
                self._task("task-unwritable-failure"), root_dir=root, runner=runner
            )

            with self.assertRaisesRegex(RuntimeError, "exit code 3"):
                journaled("claude-opus-5", "ultra", "Plan")

    def test_retry_count_is_always_zero(self) -> None:
        """`invoke_worker` performs no retries; the record says so honestly
        rather than a value a future retry mechanism would imply."""
        runner = Mock(return_value=subprocess.CompletedProcess([], 0, "ok", ""))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            journaled = production_invoker.make_journaled_invoke_worker(
                self._task(), root_dir=root, runner=runner
            )
            journaled("claude-fable-5", "low", "Draft")
            records = _read_jsonl(root / self.JOURNAL_RELATIVE_PATH)

        self.assertEqual(records[0]["retry_count"], 0)


if __name__ == "__main__":
    unittest.main()
