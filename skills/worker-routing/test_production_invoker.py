#!/usr/bin/env python3
"""Unit tests for the production worker invoker."""
from __future__ import annotations

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

        def fake_make_journaled_invoke_worker(task_id: str, *, root_dir: Path) -> object:
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

    def test_a_record_that_cannot_be_built_is_reported_not_silent(self) -> None:
        """A malformed record is a call-site bug, and by `learning_journal`'s
        contract a call-site bug must be loud. It cannot be loud by raising
        here — the worker has already run, and raising would destroy a real
        result to report a bookkeeping fault — so it is loud by being
        reported. Silence was the defect: `except Exception: pass` made a
        `WorkerExecutionRecord` bug undetectable in production.

        Reached through an unjournalable `run_id`, which is now the field this
        factory accepts without checking it at wiring time — `task_id` is
        checked there (see the test above), while a caller-supplied `run_id`
        is carried straight into each record. That asymmetry is what makes
        this contract still reachable, and the outcome is the one it
        promises: the worker's real output survives, nothing is journaled, and
        the failure is named rather than swallowed.
        """
        reported: list[str] = []
        runner = Mock(return_value=subprocess.CompletedProcess([], 0, "worker output", ""))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            journaled = production_invoker.make_journaled_invoke_worker(
                "task-bad-run-id",
                root_dir=root,
                run_id="run one, or perhaps two",
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
