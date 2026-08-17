#!/usr/bin/env python3
"""Unit tests for the production worker invoker."""
from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

from test_routing import _approve

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


class WorkerExecutionResultTests(unittest.TestCase):
    def test_construction_preserves_all_fields(self) -> None:
        payload = {"vote": "approve"}
        result = production_invoker.WorkerExecutionResult(
            raw_output="worker output",
            duration_ms=125,
            cost_estimate_usd=0.25,
            success=False,
            error="worker failed",
            parsed_payload=payload,
        )

        self.assertEqual(result.raw_output, "worker output")
        self.assertEqual(result.duration_ms, 125)
        self.assertEqual(result.cost_estimate_usd, 0.25)
        self.assertFalse(result.success)
        self.assertEqual(result.error, "worker failed")
        self.assertEqual(result.parsed_payload, payload)

    def test_defaults_and_immutability(self) -> None:
        result = production_invoker.WorkerExecutionResult("", 0, 0.0, True)

        self.assertIsNone(result.error)
        self.assertIsNone(result.parsed_payload)
        with self.assertRaises(AttributeError):
            result.success = False  # type: ignore[misc]

    def test_negative_duration_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "duration_ms"):
            production_invoker.WorkerExecutionResult("", -1, 0.0, True)

    def test_negative_cost_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "cost_estimate_usd"):
            production_invoker.WorkerExecutionResult("", 0, -0.01, True)


class ExtractReviewPayloadTests(unittest.TestCase):
    def test_empty_output_returns_defaults(self) -> None:
        self.assertEqual(
            production_invoker.extract_review_payload("  \n\t "),
            {
                "vote": "approve",
                "confidence": 1.0,
                "findings": [],
                "candidate_hash": "synth1",
            },
        )

    def test_json_payload_is_extracted_and_normalized(self) -> None:
        payload = production_invoker.extract_review_payload(
            'Result: {"vote": "APPROVE", "confidence": "0.75", '
            '"findings": ["minor"], "candidate_hash": "abc", "note": "kept"}'
        )

        self.assertEqual(payload["vote"], "approve")
        self.assertEqual(payload["confidence"], 0.75)
        self.assertEqual(payload["findings"], ["minor"])
        self.assertEqual(payload["candidate_hash"], "abc")
        self.assertEqual(payload["note"], "kept")

    def test_markdown_embedded_json_is_extracted(self) -> None:
        payload = production_invoker.extract_review_payload(
            "```json\n{\"vote\": \"REVISE\", \"confidence\": 0.2}\n```"
        )

        self.assertEqual(payload["vote"], "revise")
        self.assertEqual(payload["confidence"], 0.2)
        self.assertEqual(payload["findings"], [])
        self.assertEqual(payload["candidate_hash"], "synth1")

    def test_nested_findings_dictionaries_are_preserved(self) -> None:
        payload = production_invoker.extract_review_payload(
            'Review: {"vote": "approve", "findings": '
            '[{"rule": "no-eval", "severity": "high"}], '
            '"confidence": 0.9} trailing notes'
        )

        self.assertEqual(payload["vote"], "approve")
        self.assertEqual(payload["confidence"], 0.9)
        self.assertEqual(
            payload["findings"], [{"rule": "no-eval", "severity": "high"}]
        )

    def test_invalid_json_field_values_use_safe_defaults(self) -> None:
        payload = production_invoker.extract_review_payload(
            '{"vote": 123, "confidence": "unknown", "findings": "none", '
            '"candidate_hash": 9}',
            default_candidate_hash="candidate-9",
        )

        self.assertEqual(payload["vote"], "123")
        self.assertEqual(payload["confidence"], 1.0)
        self.assertEqual(payload["findings"], [])
        self.assertEqual(payload["candidate_hash"], "candidate-9")

    def test_invalid_or_non_object_json_uses_text_heuristics(self) -> None:
        cases = (
            ("{not valid} critical concern", "block", -1.0),
            ("[1, 2] changes requested", "revise", -0.3),
            ("plain output: approve", "approve", 1.0),
            ("unstructured output", "approve", 1.0),
        )

        for output, vote, confidence in cases:
            with self.subTest(output=output):
                payload = production_invoker.extract_review_payload(output)
                self.assertEqual(payload["vote"], vote)
                self.assertEqual(payload["confidence"], confidence)
                self.assertEqual(payload["findings"], [])
                self.assertEqual(payload["candidate_hash"], "synth1")


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
            # `_approve` (shared with test_routing.py) is what actually
            # satisfies spec 0003's VerdictContract here: rationale, one
            # quote verifiable against the Planner's plan, verdict line
            # last. A bare "VERDICT: APPROVE" parses as `unparseable` by
            # design — an approval backed by no engagement is the
            # rubber-stamp the contract exists to refuse — and would never
            # reach the consensus this test asserts.
            return "Planner plan" if len(calls) == 1 else _approve("Planner plan")

        def fake_make_journaled_invoke_worker(
            task_id: str, *, root_dir: Path, run_id: str | None = None
        ) -> object:
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


class _FakeAsyncProcess:
    """A fake standing in for the `AsyncWorkerProcess` slice of
    `asyncio.subprocess.Process` this module depends on.

    ``hang_seconds`` is how the timeout-reaping tests force a real
    `asyncio.TimeoutError` out of `asyncio.wait_for` deterministically: the
    fake sleeps longer than the caller's timeout instead of a mock raising
    the exception itself, so the same code path a real hung subprocess would
    take (`wait_for` cancelling `communicate()`) is what actually runs.
    """

    def __init__(
        self,
        *,
        stdout: bytes = b"",
        stderr: bytes = b"",
        returncode: int = 0,
        hang_seconds: float | None = None,
    ) -> None:
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr
        self._hang_seconds = hang_seconds
        self.killed = False
        self.waited = False

    async def communicate(self) -> tuple[bytes, bytes]:
        if self._hang_seconds is not None:
            await asyncio.sleep(self._hang_seconds)
        return self._stdout, self._stderr

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        self.waited = True
        return self.returncode


class _RecordingAsyncRunner:
    """An injectable `AsyncRunner` returning one fixed process and recording every call."""

    def __init__(self, process: _FakeAsyncProcess) -> None:
        self.process = process
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    async def __call__(self, *args: Any, **kwargs: Any) -> _FakeAsyncProcess:
        self.calls.append((args, kwargs))
        return self.process


class InvokeWorkerAsyncTests(unittest.IsolatedAsyncioTestCase):
    """`invoke_worker_async` is the batch-friendly counterpart to `invoke_worker`:
    it reports a worker's own runtime outcome (non-zero exit, timeout) as a
    failed `WorkerExecutionResult` instead of raising, so a caller awaiting
    many of these concurrently never has one worker's failure cancel its
    siblings. A malformed request (unsupported model/effort) still raises,
    synchronously, before any process is spawned — that is a call-site bug,
    not a worker outcome.
    """

    async def test_success_returns_result_with_parsed_payload(self) -> None:
        process = _FakeAsyncProcess(stdout=b'{"vote": "approve", "confidence": 0.9}')
        runner = _RecordingAsyncRunner(process)

        result = await production_invoker.invoke_worker_async(
            "claude-sonnet-5",
            "high",
            "Review this",
            runner=runner,
            clock=_FakeClock([100.0, 100.25]),
        )

        self.assertTrue(result.success)
        self.assertIsNone(result.error)
        self.assertEqual(result.raw_output, '{"vote": "approve", "confidence": 0.9}')
        self.assertEqual(result.duration_ms, 250)
        self.assertEqual(
            result.cost_estimate_usd,
            production_invoker.estimate_cost_usd("claude-sonnet-5", 250),
        )
        assert result.parsed_payload is not None
        self.assertEqual(result.parsed_payload["vote"], "approve")
        self.assertEqual(result.parsed_payload["confidence"], 0.9)

    async def test_forwards_noninteractive_environment_and_worker_token(self) -> None:
        process = _FakeAsyncProcess(stdout=b"ok")
        runner = _RecordingAsyncRunner(process)

        await production_invoker.invoke_worker_async(
            "gpt-5.6-terra", "medium", "Implement it", runner=runner
        )

        self.assertEqual(len(runner.calls), 1)
        args, kwargs = runner.calls[0]
        self.assertEqual(args[0:2], ("codex", "exec"))
        self.assertEqual(args[-1], "[WORKER-MODE: AGY-NESTED-EXEC] Implement it")
        self.assertIs(kwargs["stdin"], asyncio.subprocess.DEVNULL)
        self.assertIs(kwargs["stdout"], asyncio.subprocess.PIPE)
        self.assertIs(kwargs["stderr"], asyncio.subprocess.PIPE)
        self.assertEqual(kwargs["env"]["IN_WORKER_ROUTING"], "true")
        self.assertEqual(kwargs["env"].get("PATH"), os.environ.get("PATH"))

    async def test_nonzero_exit_returns_failed_result_without_raising(self) -> None:
        process = _FakeAsyncProcess(stdout=b"partial", stderr=b"boom", returncode=3)
        runner = _RecordingAsyncRunner(process)

        result = await production_invoker.invoke_worker_async(
            "claude-opus-5", "ultra", "Plan", runner=runner
        )

        self.assertFalse(result.success)
        self.assertEqual(result.raw_output, "partial")
        assert result.error is not None
        self.assertIn("exit code 3", result.error)
        self.assertIn("boom", result.error)

    async def test_timeout_kills_and_reaps_the_child_process(self) -> None:
        process = _FakeAsyncProcess(hang_seconds=5.0)
        runner = _RecordingAsyncRunner(process)

        result = await production_invoker.invoke_worker_async(
            "agy", "high", "Research", timeout=0.01, runner=runner
        )

        self.assertFalse(result.success)
        assert result.error is not None
        self.assertIn("timed out", result.error)
        self.assertEqual(result.raw_output, "")
        self.assertTrue(process.killed)
        self.assertTrue(process.waited)

    async def test_unsupported_model_raises_before_spawning_a_process(self) -> None:
        runner = _RecordingAsyncRunner(_FakeAsyncProcess())

        with self.assertRaisesRegex(ValueError, "Unsupported worker model"):
            await production_invoker.invoke_worker_async(
                "acme-model", "low", "Do work", runner=runner
            )

        self.assertEqual(runner.calls, [])

    async def test_process_lookup_error_on_kill_is_handled_gracefully(self) -> None:
        """A process that exits on its own right at the timeout boundary can
        make `kill()`/`wait()` raise `ProcessLookupError` for a pid that no
        longer exists. That race must not surface as an unhandled exception —
        the timeout result still comes back as a failed `WorkerExecutionResult`.
        """

        class _RaceConditionProcess(_FakeAsyncProcess):
            def kill(self) -> None:
                self.killed = True
                raise ProcessLookupError("process already exited")

            async def wait(self) -> int:
                self.waited = True
                raise ProcessLookupError("process already exited")

        process = _RaceConditionProcess(hang_seconds=5.0)
        runner = _RecordingAsyncRunner(process)

        result = await production_invoker.invoke_worker_async(
            "agy", "high", "Research", timeout=0.01, runner=runner
        )

        self.assertFalse(result.success)
        assert result.error is not None
        self.assertIn("timed out", result.error)
        self.assertEqual(result.raw_output, "")
        self.assertTrue(process.killed)
        self.assertTrue(process.waited)

    async def test_communicate_exception_terminates_and_reaps_with_accurate_result(
        self,
    ) -> None:
        """An exception raised by `communicate()` itself (not a timeout) is a
        worker outcome, not a call-site bug — but unlike that clean non-zero
        exit or timeout, the child process is still running when this hits.
        It must still be killed and reaped, and the diagnostic must say
        "failed during execution", not "failed to spawn": spawning already
        succeeded (`runner()` returned a process handle); it is
        `communicate()` that failed.
        """

        class _CommunicateFailsProcess(_FakeAsyncProcess):
            async def communicate(self) -> tuple[bytes, bytes]:
                raise OSError("pipe broken")

        process = _CommunicateFailsProcess()
        runner = _RecordingAsyncRunner(process)

        result = await production_invoker.invoke_worker_async(
            "claude-sonnet-5",
            "high",
            "Review this",
            runner=runner,
            clock=_FakeClock([100.0, 100.5]),
        )

        self.assertFalse(result.success)
        assert result.error is not None
        self.assertIn("failed during execution", result.error)
        self.assertIn("pipe broken", result.error)
        self.assertNotIn("failed to spawn", result.error)
        self.assertEqual(result.raw_output, "")
        self.assertEqual(result.duration_ms, 500)
        self.assertEqual(
            result.cost_estimate_usd,
            production_invoker.estimate_cost_usd("claude-sonnet-5", 500),
        )
        self.assertTrue(process.killed)
        self.assertTrue(process.waited)


class InvokeWorkersParallelTests(unittest.IsolatedAsyncioTestCase):
    """`invoke_workers_parallel` batches `invoke_worker_async` calls and is the
    seam review panels use to fan out to several models at once (spec 0005
    user story 2 / ticket 03).
    """

    async def test_runs_batch_and_preserves_request_order(self) -> None:
        outputs = {
            "first prompt": _FakeAsyncProcess(stdout=b"first output"),
            "second prompt": _FakeAsyncProcess(stdout=b"second output"),
            "third prompt": _FakeAsyncProcess(stdout=b"third output"),
        }

        async def runner(*args: Any, **kwargs: Any) -> _FakeAsyncProcess:
            prompt = args[-1]
            for key, process in outputs.items():
                if key in prompt:
                    return process
            raise AssertionError(f"unexpected prompt: {prompt}")

        requests: list[production_invoker.WorkerRequest] = [
            ("claude-sonnet-5", "high", "first prompt"),
            ("gpt-5.6-terra", "medium", "second prompt"),
            ("agy", "low", "third prompt"),
        ]

        results = await production_invoker.invoke_workers_parallel(requests, runner=runner)

        self.assertEqual(len(results), 3)
        self.assertEqual(results[0].raw_output, "first output")
        self.assertEqual(results[1].raw_output, "second output")
        self.assertEqual(results[2].raw_output, "third output")
        self.assertTrue(all(result.success for result in results))

    async def test_a_malformed_request_becomes_a_failed_result_not_a_raised_batch(
        self,
    ) -> None:
        good_process = _FakeAsyncProcess(stdout=b"ok")

        async def runner(*args: Any, **kwargs: Any) -> _FakeAsyncProcess:
            return good_process

        requests: list[production_invoker.WorkerRequest] = [
            ("claude-sonnet-5", "high", "good request"),
            ("acme-model", "low", "bad request"),
        ]

        results = await production_invoker.invoke_workers_parallel(requests, runner=runner)

        self.assertEqual(len(results), 2)
        self.assertTrue(results[0].success)
        self.assertFalse(results[1].success)
        assert results[1].error is not None
        self.assertIn("Unsupported worker model", results[1].error)

    async def test_a_timeout_in_one_request_does_not_cancel_its_siblings(self) -> None:
        hung_process = _FakeAsyncProcess(hang_seconds=5.0)
        fast_process = _FakeAsyncProcess(stdout=b"fast output")

        async def runner(*args: Any, **kwargs: Any) -> _FakeAsyncProcess:
            prompt = args[-1]
            return hung_process if "slow" in prompt else fast_process

        requests: list[production_invoker.WorkerRequest] = [
            ("agy", "high", "slow request"),
            ("gpt-5.6-terra", "medium", "fast request"),
        ]

        results = await production_invoker.invoke_workers_parallel(
            requests, timeout=0.01, runner=runner
        )

        self.assertFalse(results[0].success)
        assert results[0].error is not None
        self.assertIn("timed out", results[0].error)
        self.assertTrue(hung_process.killed)
        self.assertTrue(hung_process.waited)
        self.assertTrue(results[1].success)
        self.assertEqual(results[1].raw_output, "fast output")

    async def test_spawn_failure_returns_failed_result_without_crashing_batch(
        self,
    ) -> None:
        """A missing CLI binary (or any other process-creation error) raises
        from the runner itself, before a process handle even exists. One
        request hitting that must not cancel its siblings, and must not
        propagate out of the batch — it becomes a failed result like any
        other worker outcome.
        """
        good_process = _FakeAsyncProcess(stdout=b"ok")

        async def runner(*args: Any, **kwargs: Any) -> _FakeAsyncProcess:
            prompt = args[-1]
            if "missing binary" in prompt:
                raise FileNotFoundError("no such file or directory: 'codex'")
            return good_process

        requests: list[production_invoker.WorkerRequest] = [
            ("claude-sonnet-5", "high", "good request"),
            ("gpt-5.6-terra", "medium", "missing binary request"),
        ]

        results = await production_invoker.invoke_workers_parallel(requests, runner=runner)

        self.assertEqual(len(results), 2)
        self.assertTrue(results[0].success)
        self.assertEqual(results[0].raw_output, "ok")
        self.assertFalse(results[1].success)
        assert results[1].error is not None
        self.assertIn("failed to spawn", results[1].error)
        self.assertIn("no such file or directory", results[1].error)
        self.assertEqual(results[1].raw_output, "")
        self.assertEqual(results[1].cost_estimate_usd, 0.0)


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

    The factory takes a `task_id` — not a built `TaskLabel` — for the reason
    its docstring gives: an id is the boundary type for every entry point into
    this loop, and the label is built at the seam by the code that owns the
    schema. These tests hand it ids for that reason, and the `_task` helper
    that used to build a label for each of them is gone with the argument it
    served.
    """

    def test_success_appends_one_record_with_full_fields(self) -> None:
        runner = Mock(return_value=subprocess.CompletedProcess([], 0, "worker output", ""))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            journaled = production_invoker.make_journaled_invoke_worker(
                "task-success",
                root_dir=root,
                task_type="feature",
                runner=runner,
                clock=_FakeClock([100.0, 101.5]),
            )
            output = journaled("claude-sonnet-5", "high", "Implement it")
            records = _read_jsonl(learning_journal.journal_path(root))

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
        self.assertTrue(
            learning_journal.TASK_ID_RE.fullmatch(record["run_id"]),
            "a generated run identity must satisfy the journal's own pattern",
        )

    def test_nonzero_exit_appends_one_failed_record_and_reraises(self) -> None:
        runner = Mock(
            return_value=subprocess.CompletedProcess([], 17, "partial stdout", "failure stderr")
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            journaled = production_invoker.make_journaled_invoke_worker(
                "task-nonzero",
                root_dir=root,
                runner=runner,
                clock=_FakeClock([10.0, 10.25]),
            )
            with self.assertRaisesRegex(RuntimeError, "exit code 17"):
                journaled("gpt-5.6-sol", "medium", "Do work")
            records = _read_jsonl(learning_journal.journal_path(root))

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
                "task-timeout",
                root_dir=root,
                runner=runner,
                clock=_FakeClock([0.0, 30.0]),
            )
            with self.assertRaisesRegex(RuntimeError, "timed out"):
                journaled("agy", "high", "Research")
            records = _read_jsonl(learning_journal.journal_path(root))

        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["task_id"], "task-timeout")
        self.assertFalse(record["success"])
        self.assertEqual(record["duration_ms"], 30000)
        self.assertEqual(record["model_id"], "agy")
        self.assertEqual(record["model_family"], "agy")

    def test_journal_write_failure_never_breaks_the_observed_invocation(self) -> None:
        reported: list[str] = []
        runner = Mock(return_value=subprocess.CompletedProcess([], 0, "worker output", ""))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # `.ralph` is a plain file, not a directory: `append_journal_record`'s
            # `mkdir(parents=True, exist_ok=True)` fails with an `OSError` no
            # matter what record it's asked to write.
            (root / ".ralph").write_text("not a directory")
            journaled = production_invoker.make_journaled_invoke_worker(
                "task-unwritable",
                root_dir=root,
                runner=runner,
                report_journal_error=reported.append,
            )

            output = journaled("claude-opus-5", "ultra", "Plan")

        self.assertEqual(output, "worker output")
        # Reported, not swallowed: `append_journal_record`'s returned message
        # used to be discarded, so a journal could go dark for a whole run
        # with nothing anywhere saying so.
        self.assertEqual(len(reported), 1)
        self.assertIn("failed to write learning journal record", reported[0])

    def test_journal_write_failure_never_breaks_a_worker_exception_either(self) -> None:
        reported: list[str] = []
        runner = Mock(
            return_value=subprocess.CompletedProcess([], 3, "partial stdout", "failure stderr")
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".ralph").write_text("not a directory")
            journaled = production_invoker.make_journaled_invoke_worker(
                "task-unwritable-failure",
                root_dir=root,
                runner=runner,
                report_journal_error=reported.append,
            )

            with self.assertRaisesRegex(RuntimeError, "exit code 3"):
                journaled("claude-opus-5", "ultra", "Plan")

        self.assertEqual(len(reported), 1)

    def test_an_unjournalable_task_id_is_refused_at_wiring_time(self) -> None:
        """The factory takes an id, so the id is checked once, where the label
        is built, before any worker runs — rather than once per invocation
        afterwards. `advisory_consultation` wraps this call in the try that
        degrades to "journaling disabled for this run", so a bad id costs the
        run its instrumentation and nothing else.
        """
        runner = Mock(return_value=subprocess.CompletedProcess([], 0, "worker output", ""))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            with self.assertRaises(ValueError):
                production_invoker.make_journaled_invoke_worker(
                    "fix the login 500 for the ACME account",
                    root_dir=root,
                    runner=runner,
                )

            runner.assert_not_called()
            self.assertFalse(learning_journal.journal_path(root).exists())

    def test_an_unjournalable_run_id_is_refused_at_wiring_time(self) -> None:
        """A caller-supplied `run_id` is carried, not composed — the same
        rule `task_id` faces in the test above — and is now checked at the
        same moment: once, where the factory is built, rather than once per
        invocation against a record that then silently fails to write.
        """
        runner = Mock(return_value=subprocess.CompletedProcess([], 0, "worker output", ""))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            with self.assertRaises(ValueError):
                production_invoker.make_journaled_invoke_worker(
                    "task-1",
                    root_dir=root,
                    run_id="not a valid run id",
                    runner=runner,
                )

            runner.assert_not_called()
            self.assertFalse(learning_journal.journal_path(root).exists())

    def test_a_record_that_cannot_be_built_is_reported_not_silent(self) -> None:
        """A malformed record is a call-site bug, and by `learning_journal`'s
        contract a call-site bug must be loud. It cannot be loud by raising
        here — the worker has already run, and raising would destroy a real
        result to report a bookkeeping fault — so it is loud by being
        reported. Silence was the defect: `except Exception: pass` made a
        `WorkerExecutionRecord` bug undetectable in production.

        Every field this factory itself hands to `WorkerExecutionRecord` is
        safe by the time it is built: `task_id` and a caller-supplied
        `run_id` are both refused at wiring time now (the two tests above),
        `effort` is refused before the worker ever runs, and `model_id` /
        `model_family` come from a fixed, closed mapping that cannot produce
        an unjournalable pair. So this path — a record the constructor
        itself refuses — is reached the only way left open: the constructor
        is patched to fail, standing in for whatever a future field adds
        that this factory does not yet validate up front.
        """
        reported: list[str] = []
        runner = Mock(return_value=subprocess.CompletedProcess([], 0, "worker output", ""))
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            learning_journal,
            "WorkerExecutionRecord",
            side_effect=ValueError("boom: unbuildable record"),
        ):
            root = Path(tmp)
            journaled = production_invoker.make_journaled_invoke_worker(
                "task-bad-record",
                root_dir=root,
                runner=runner,
                report_journal_error=reported.append,
            )

            output = journaled("claude-opus-5", "ultra", "Plan")
            journal_written = learning_journal.journal_path(root).exists()

        self.assertEqual(output, "worker output")
        self.assertFalse(journal_written)
        self.assertEqual(len(reported), 1)
        self.assertIn("call-site bug", reported[0])
        self.assertIn("could not", reported[0])

    def test_default_error_sink_writes_one_stderr_line(self) -> None:
        """Production has no injected sink, so the default is what actually
        carries the signal. stdout is off limits — a routed worker's stdout
        is the consultation's payload."""
        stream = io.StringIO()
        with contextlib.redirect_stderr(stream):
            production_invoker.report_journal_error_to_stderr("journal went dark")

        self.assertEqual(len(stream.getvalue().strip().splitlines()), 1)
        self.assertIn("journal went dark", stream.getvalue())

    def test_an_unjournalable_effort_is_rejected_before_any_invocation(self) -> None:
        """Ticket 13 promises exactly one record per invocation. An effort
        outside the journal's vocabulary cannot be represented in a record at
        all, so validating it after the call would leave an invocation that
        journaled nothing. Rejected up front, there is no invocation — the
        runner is never touched — and the promise stays exactly true."""
        runner = Mock(return_value=subprocess.CompletedProcess([], 0, "worker output", ""))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            journaled = production_invoker.make_journaled_invoke_worker(
                "task-bad-effort", root_dir=root, runner=runner
            )

            with self.assertRaisesRegex(ValueError, "Unsupported worker effort"):
                journaled("claude-opus-5", "exhaustive", "Plan")

            journal_written = learning_journal.journal_path(root).exists()

        runner.assert_not_called()
        self.assertFalse(journal_written)

    def test_every_valid_effort_journals(self) -> None:
        """The guard rejects what the journal cannot hold and nothing more."""
        for effort in sorted(learning_journal.VALID_EFFORTS):
            with self.subTest(effort=effort), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                runner = Mock(
                    return_value=subprocess.CompletedProcess([], 0, "ok", "")
                )
                journaled = production_invoker.make_journaled_invoke_worker(
                    "task-1", root_dir=root, runner=runner
                )
                journaled("claude-opus-5", effort, "Plan")
                records = _read_jsonl(learning_journal.journal_path(root))

                self.assertEqual(len(records), 1)
                self.assertEqual(records[0]["effort"], effort)

    def test_retry_count_is_always_zero(self) -> None:
        """`invoke_worker` performs no retries; the record says so honestly
        rather than a value a future retry mechanism would imply."""
        runner = Mock(return_value=subprocess.CompletedProcess([], 0, "ok", ""))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            journaled = production_invoker.make_journaled_invoke_worker(
                "task-1", root_dir=root, runner=runner
            )
            journaled("claude-fable-5", "low", "Draft")
            records = _read_jsonl(learning_journal.journal_path(root))

        self.assertEqual(records[0]["retry_count"], 0)

    def test_a_run_identity_is_per_factory_and_fresh_per_factory(self) -> None:
        """The granularity the default is chosen for: one factory is built per
        consultation, so a factory's records are a run's records. Every call
        through one factory shares its identity; a second factory over the
        same `task_id` gets its own, which is what makes a repeat of that task
        countable as rework rather than invisible inside one summed identity.

        `retry_count` stays 0 through all of it: no invocation is ever attempt
        two *of itself*, which is the different question it answers.
        """
        runner = Mock(return_value=subprocess.CompletedProcess([], 0, "ok", ""))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = production_invoker.make_journaled_invoke_worker(
                "task-repeated", root_dir=root, runner=runner
            )
            first("claude-fable-5", "low", "Draft")
            first("claude-fable-5", "low", "Draft again")
            production_invoker.make_journaled_invoke_worker(
                "task-repeated", root_dir=root, runner=runner
            )("claude-fable-5", "low", "Draft once more")
            records = _read_jsonl(learning_journal.journal_path(root))

        self.assertEqual(len(records), 3)
        self.assertEqual({record["task_id"] for record in records}, {"task-repeated"})
        self.assertEqual(records[0]["run_id"], records[1]["run_id"])
        self.assertNotEqual(records[1]["run_id"], records[2]["run_id"])
        self.assertEqual({record["retry_count"] for record in records}, {0})

    def test_a_caller_supplied_run_id_is_carried_rather_than_replaced(self) -> None:
        """The generated identity is a default, not a policy: a caller that
        already has a run identity passes it and the record carries it."""
        runner = Mock(return_value=subprocess.CompletedProcess([], 0, "ok", ""))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            production_invoker.make_journaled_invoke_worker(
                "task-1", root_dir=root, run_id="run-orchestrated-7", runner=runner
            )("claude-fable-5", "low", "Draft")
            records = _read_jsonl(learning_journal.journal_path(root))

        self.assertEqual(records[0]["run_id"], "run-orchestrated-7")


class CostEstimateTests(unittest.TestCase):
    """A record must let a reader tell "we cannot price this call" from "this
    call was expensive."

    The previous answer put the distinction *in the amount*: an unpriced
    model billed at 9,999 USD/second, which turned one 300-second call into a
    $2,999,700 entry in a field the scoreboard averages as cost per completed
    task — a single such record dominates a week's mean, and nothing on it
    marks it as a placeholder. The comments guarding that constant claimed
    tests pinned the rate table and that only a maintenance gap could reach
    the sentinel; neither was true, and `_resolve_model_id_and_family`
    returned an id absent from the table on the ordinary unknown-model path.

    The distinction now lives in `model_id`, and the amount stays an amount.
    """

    def test_rate_table_prices_every_invocable_model(self) -> None:
        """The totality `estimate_cost_usd`'s reasoning depends on: if every
        model that can be invoked has a rate, then falling through to "no
        rate" proves nothing was invoked. This is the test the old comment
        claimed existed — no test referenced `USD_PER_SECOND` at all."""
        invocable = set(production_invoker.MODEL_ALIASES.values())
        self.assertTrue(invocable)

        missing = sorted(invocable - set(production_invoker.USD_PER_SECOND))
        self.assertEqual(
            missing,
            [],
            "these models can be invoked but have no rate, so a real "
            "invocation would record a cost of 0.0",
        )

        stale = sorted(set(production_invoker.USD_PER_SECOND) - invocable)
        self.assertEqual(stale, [], "these rates price a model nothing can route to")

    def test_the_unpriced_id_is_not_a_model_anything_can_route_to(self) -> None:
        """`UNPRICED_MODEL_ID` marks a record as unpriceable, so it must never
        collide with a real routing key — otherwise the marker and a genuine
        model become indistinguishable."""
        self.assertNotIn(
            production_invoker.UNPRICED_MODEL_ID,
            set(production_invoker.MODEL_ALIASES.values()),
        )
        self.assertNotIn(
            production_invoker.UNPRICED_MODEL_ID, production_invoker.USD_PER_SECOND
        )

    def test_a_priced_model_costs_rate_times_wall_time(self) -> None:
        self.assertEqual(
            production_invoker.estimate_cost_usd("claude-opus-5", 300_000),
            round(production_invoker.USD_PER_SECOND["claude-opus-5"] * 300.0, 6),
        )

    def test_an_unknown_model_is_not_billed_a_sentinel_amount(self) -> None:
        """The regression this class exists for, stated as an amount: a
        300-second call must not produce a six-figure estimate."""
        cost = production_invoker.estimate_cost_usd(
            production_invoker.UNPRICED_MODEL_ID, 300_000
        )

        self.assertEqual(cost, 0.0)
        self.assertLess(cost, 1.0)

    def test_an_unknown_model_records_the_marker_and_never_ran(self) -> None:
        """End to end: the record a caller-supplied unknown model produces.

        `model_id` carries the marker, `success` is False, and the cost is
        0.0 — which here is the measurement rather than a default, because
        `build_worker_command` rejected the model before any process was
        launched. The runner proves that: it is never called.
        """
        runner = Mock(return_value=subprocess.CompletedProcess([], 0, "unused", ""))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            journaled = production_invoker.make_journaled_invoke_worker(
                "task-unknown-model",
                root_dir=root,
                runner=runner,
                clock=_FakeClock([0.0, 300.0]),
            )

            with self.assertRaisesRegex(ValueError, "Unsupported worker model"):
                journaled("acme-model-9", "high", "Plan")

            records = _read_jsonl(learning_journal.journal_path(root))

        runner.assert_not_called()
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["model_id"], production_invoker.UNPRICED_MODEL_ID)
        self.assertEqual(record["model_family"], "unknown")
        self.assertFalse(record["success"])
        self.assertEqual(record["cost_estimate_usd"], 0.0)

    def test_every_routable_model_has_a_positive_rate(self) -> None:
        """A priced model with a measurable duration always produces a
        non-zero estimate, so a 0.0 on a real model means only "this call
        took no measurable time" — never "we could not price it", which is
        what `model_id` says."""
        for model_id in sorted(set(production_invoker.MODEL_ALIASES.values())):
            with self.subTest(model_id=model_id):
                self.assertGreater(production_invoker.USD_PER_SECOND[model_id], 0.0)
                self.assertGreater(
                    production_invoker.estimate_cost_usd(model_id, 60_000), 0.0
                )


if __name__ == "__main__":
    unittest.main()
