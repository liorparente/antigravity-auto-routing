#!/usr/bin/env python3
"""Unit tests for the production worker invoker."""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock

from test_routing import _approve

MODULE_PATH = Path(__file__).with_name("production_invoker.py")
SPEC = importlib.util.spec_from_file_location("production_invoker", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
production_invoker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(production_invoker)


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
            return "Planner plan" if len(calls) == 1 else _approve("Planner plan")

        previous_module = sys.modules.get("production_invoker")
        production_module = types.ModuleType("production_invoker")
        production_module.invoke_worker = fake_invoke_worker  # type: ignore[attr-defined]
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


if __name__ == "__main__":
    unittest.main()
