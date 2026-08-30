#!/usr/bin/env python3
"""Unit tests for the production worker invoker."""
from __future__ import annotations

import asyncio
import contextlib
import inspect
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

if __package__:
    from . import critical_dialogue, learning_journal, production_invoker
    from .test_routing import _approve
else:
    import critical_dialogue  # type: ignore[no-redef]
    import learning_journal  # type: ignore[no-redef]
    import production_invoker  # type: ignore[no-redef]
    from test_routing import _approve  # type: ignore[no-redef]


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
            ("{not valid} critical concern", "block", -1.0, False),
            ("[1, 2] changes requested", "revise", -0.3, False),
            ("plain output: approve", "approve", 1.0, False),
            ("unstructured output", "approve", 1.0, False),
        )

        for output, vote, confidence, has_veto in cases:
            with self.subTest(output=output):
                payload = production_invoker.extract_review_payload(output)
                self.assertEqual(payload["vote"], vote)
                self.assertEqual(payload["confidence"], confidence)
                if has_veto:
                    self.assertEqual(payload["findings"][0]["id"], "PROSE-VETO")
                    self.assertEqual(payload["findings"][0]["severity"], "critical")
                else:
                    self.assertEqual(payload["findings"], [])
                self.assertEqual(payload["candidate_hash"], "synth1")

    def test_security_prose_populates_fail_closed_finding(self) -> None:
        cases = (
            "SECURITY_HALT: unsafe request validation",
            "Critical vulnerability found in request validation",
            "BLOCK: Possible SQL injection in the query builder",
            "REVISE: CWE-89 applies here",
            '{"vote": "block"}\nCVE-2026-1234 is exploitable',
        )

        for output in cases:
            with self.subTest(output=output):
                payload = production_invoker.extract_review_payload(output)
                self.assertIn(
                    {
                        "id": "PROSE-VETO",
                        "severity": "critical",
                        "confidence": 1.0,
                        "claim": "Critical security finding detected in review prose",
                    },
                    payload["findings"],
                )

    def test_additional_security_indicators_populate_fail_closed_finding(self) -> None:
        indicators = (
            "remote code execution",
            "rce",
            "command injection",
            "privilege escalation",
            "auth bypass",
            "authentication bypass",
            "unauthorized access",
            "arbitrary file read",
            "arbitrary file write",
            "high severity",
            "critical severity",
        )

        for indicator in indicators:
            with self.subTest(indicator=indicator):
                payload = production_invoker.extract_review_payload(
                    f'{{"vote": "revise"}}\n{indicator} remains exploitable'
                )
                self.assertIn(
                    "PROSE-VETO",
                    [
                        finding.get("id")
                        for finding in payload["findings"]
                        if isinstance(finding, dict)
                    ],
                )

    def test_benign_security_prose_does_not_populate_prose_veto(self) -> None:
        cases = (
            "SQL injection is prevented by query parameterization",
            "This change prevents CWE-89",
            "No vulnerabilities found",
            '{"vote": "block"}\nSQL injection is prevented by query parameterization',
            '{"vote": "revise"}\nThis change prevents CWE-89',
            '{"vote": "block"}\nNo SQL injection is present',
            '{"vote": "revise"}\nCVE-2026-1234 is resolved',
            '{"vote": "block"}\nThe query is parameterized against SQL injection',
            '{"vote": "block"}\nThe source and resource paths are valid',
        )

        for output in cases:
            with self.subTest(output=output):
                payload = production_invoker.extract_review_payload(output)
                self.assertNotIn(
                    "PROSE-VETO",
                    [
                        finding.get("id")
                        for finding in payload["findings"]
                        if isinstance(finding, dict)
                    ],
                )

    def test_resolved_security_phrase_does_not_hide_active_finding_on_same_line(self) -> None:
        payload = production_invoker.extract_review_payload(
            '{"vote": "block"}\n'
            "SQL injection remains exploitable; the other query is parameterized"
        )

        self.assertIn("PROSE-VETO", [finding["id"] for finding in payload["findings"]])

    def test_approve_vote_with_unnegated_security_indicator_does_not_veto(self) -> None:
        payload = production_invoker.extract_review_payload(
            '{"vote": "approve"}\nSQL injection remains exploitable'
        )

        self.assertEqual(payload["vote"], "approve")
        self.assertEqual(payload["findings"], [])

    def test_generic_structured_block_does_not_populate_prose_veto(self) -> None:
        payload = production_invoker.extract_review_payload(
            '{"vote": "block", "findings": []}'
        )

        self.assertEqual(payload["vote"], "block")
        self.assertEqual(payload["findings"], [])

    def test_revised_plan_prose_does_not_become_a_revise_vote(self) -> None:
        payload = production_invoker.extract_review_payload(
            'QUOTE: "Planner\'s revised plan."\nVERDICT: APPROVE'
        )

        self.assertEqual(payload["vote"], "approve")


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
                "[WORKER-MODE: NESTED-EXEC] Review this plan",
            ],
        )

    def test_claude_command_preserves_existing_worker_token(self) -> None:
        prompt = "[WORKER-MODE: NESTED-EXEC] Draft the plan"

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

    def test_claude_command_preserves_existing_legacy_worker_token(self) -> None:
        prompt = "[WORKER-MODE: AGY-NESTED-EXEC] Draft the plan"

        command = production_invoker.build_worker_command("claude-sonnet-5", "high", prompt)

        self.assertEqual(command[-1], prompt)

    def test_with_worker_mode_token_recognizes_both_current_and_legacy_tokens(self) -> None:
        current = f"{production_invoker.WORKER_MODE_TOKEN} Draft the plan"
        legacy = f"{production_invoker.LEGACY_WORKER_MODE_TOKEN} Draft the plan"
        bare = "Draft the plan"

        self.assertEqual(production_invoker._with_worker_mode_token(current), current)
        self.assertEqual(production_invoker._with_worker_mode_token(legacy), legacy)
        self.assertEqual(
            production_invoker._with_worker_mode_token(bare),
            f"{production_invoker.WORKER_MODE_TOKEN} {bare}",
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

    def test_local_model_routes_as_a_direct_http_call(self) -> None:
        for model in sorted(production_invoker.LOCAL_MODELS):
            with self.subTest(model=model):
                command = production_invoker.build_worker_command(model, "low", "ping")

                self.assertEqual(command[0], "curl")
                self.assertIn(production_invoker.LOCAL_MODEL_CHAT_ENDPOINT, command)
                payload = json.loads(command[-1])
                self.assertEqual(payload["model"], model)
                self.assertEqual(
                    payload["messages"],
                    [{"role": "user", "content": f"{production_invoker.WORKER_MODE_TOKEN} ping"}],
                )

    def test_local_model_curl_fails_closed_on_http_errors_and_bounds_its_own_timeout(self) -> None:
        # `curl -s` alone swallows a non-2xx response as a "successful" empty
        # body, and has no caller-side timeout of its own the way
        # `subprocess`/`asyncio` enforce for every other provider — without
        # `--fail`/`--max-time` a stalled or erroring local server would
        # neither raise nor time out.
        command = production_invoker.build_worker_command(
            "local-lmstudio", "low", "ping", timeout=42.9
        )

        self.assertIn("--fail", command)
        self.assertIn("--max-time", command)
        self.assertEqual(command[command.index("--max-time") + 1], "42.9")

    def test_local_model_curl_max_time_defaults_to_the_module_default_timeout(self) -> None:
        command = production_invoker.build_worker_command("local-lmstudio", "low", "ping")

        self.assertEqual(
            command[command.index("--max-time") + 1],
            str(int(production_invoker.DEFAULT_TIMEOUT_SECONDS)),
        )

    def test_local_model_curl_sub_second_timeout_is_not_truncated_to_zero(self) -> None:
        """Round 2 fix: `int(0.2)` is `0`, and `curl --max-time 0` disables the
        limit entirely — the opposite of what a caller asking for a fast
        0.2s bound wants. `--max-time` must carry sub-second precision."""
        command = production_invoker.build_worker_command(
            "local-lmstudio", "low", "ping", timeout=0.2
        )

        self.assertEqual(command[command.index("--max-time") + 1], "0.2")


class ProbeLocalModelAvailabilityTests(unittest.TestCase):
    """`probe_local_model_availability` is the active, sub-second discovery
    probe against an LM Studio-style `/v1/models` endpoint (spec 0011 ticket
    02): it fails closed to `None` on anything short of a genuine loaded
    model, since callers use the result to decide between Tier 0 (local) and
    a cloud fallback.
    """

    @staticmethod
    def _fake_response(payload: dict[str, Any] | bytes) -> Any:
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")

        class _Response:
            def __enter__(self) -> _Response:  # noqa: PYI034 - see test_routing.py's _InstalledHarness.__enter__ for the same local-class rationale
                return self

            def __exit__(self, *exc_info: object) -> None:
                return None

            def read(self) -> bytes:
                return body

        return _Response()

    def test_discovers_a_qwen_model(self) -> None:
        urlopen_fn = Mock(
            return_value=self._fake_response({"data": [{"id": "qwen3.8-27b-mlx"}]})
        )

        result = production_invoker.probe_local_model_availability(urlopen_fn=urlopen_fn)

        assert result is not None
        self.assertEqual(result.model_id, "qwen3.8-27b-mlx")
        self.assertEqual(result.family, "qwen")
        self.assertEqual(result.parameter_class, "27B")
        self.assertTrue(result.loaded)
        self.assertEqual(result.endpoint, "http://127.0.0.1:1234/v1")
        url = urlopen_fn.call_args.args[0]
        self.assertEqual(url, "http://127.0.0.1:1234/v1/models")
        self.assertEqual(urlopen_fn.call_args.kwargs["timeout"], 0.2)

    def test_discovers_a_gemma_model(self) -> None:
        urlopen_fn = Mock(
            return_value=self._fake_response({"data": [{"id": "gemma-4-e4b"}]})
        )

        result = production_invoker.probe_local_model_availability(urlopen_fn=urlopen_fn)

        assert result is not None
        self.assertEqual(result.family, "gemma")
        self.assertEqual(result.parameter_class, "4B")

    def test_discovers_a_deepseek_model(self) -> None:
        urlopen_fn = Mock(
            return_value=self._fake_response({"data": [{"id": "deepseek-r1"}]})
        )

        result = production_invoker.probe_local_model_availability(urlopen_fn=urlopen_fn)

        assert result is not None
        self.assertEqual(result.family, "deepseek")
        self.assertEqual(result.parameter_class, "R1")

    def test_empty_model_list_returns_none(self) -> None:
        urlopen_fn = Mock(return_value=self._fake_response({"data": []}))

        result = production_invoker.probe_local_model_availability(urlopen_fn=urlopen_fn)

        self.assertIsNone(result)

    def test_missing_data_key_returns_none(self) -> None:
        urlopen_fn = Mock(return_value=self._fake_response({}))

        result = production_invoker.probe_local_model_availability(urlopen_fn=urlopen_fn)

        self.assertIsNone(result)

    def test_connection_refused_returns_none(self) -> None:
        urlopen_fn = Mock(side_effect=ConnectionRefusedError("connection refused"))

        result = production_invoker.probe_local_model_availability(urlopen_fn=urlopen_fn)

        self.assertIsNone(result)

    def test_url_error_returns_none(self) -> None:
        from urllib.error import URLError

        urlopen_fn = Mock(side_effect=URLError("offline"))

        result = production_invoker.probe_local_model_availability(urlopen_fn=urlopen_fn)

        self.assertIsNone(result)

    def test_timeout_returns_none(self) -> None:
        urlopen_fn = Mock(side_effect=TimeoutError("timed out"))

        result = production_invoker.probe_local_model_availability(
            urlopen_fn=urlopen_fn, timeout=0.2
        )

        self.assertIsNone(result)

    def test_json_decode_error_returns_none(self) -> None:
        urlopen_fn = Mock(return_value=self._fake_response(b"not json"))

        result = production_invoker.probe_local_model_availability(urlopen_fn=urlopen_fn)

        self.assertIsNone(result)

    def test_custom_endpoint_is_probed_and_carried_on_the_result(self) -> None:
        urlopen_fn = Mock(
            return_value=self._fake_response({"data": [{"id": "llama-70b"}]})
        )

        result = production_invoker.probe_local_model_availability(
            "http://127.0.0.1:9999/v1", urlopen_fn=urlopen_fn
        )

        assert result is not None
        self.assertEqual(result.endpoint, "http://127.0.0.1:9999/v1")
        self.assertEqual(result.family, "llama")
        self.assertEqual(result.parameter_class, "70B")
        self.assertEqual(
            urlopen_fn.call_args.args[0], "http://127.0.0.1:9999/v1/models"
        )

    def test_unrecognized_family_falls_back_to_custom(self) -> None:
        urlopen_fn = Mock(
            return_value=self._fake_response({"data": [{"id": "acme-widget-9b"}]})
        )

        result = production_invoker.probe_local_model_availability(urlopen_fn=urlopen_fn)

        assert result is not None
        self.assertEqual(result.family, "custom")
        self.assertEqual(result.parameter_class, "9B")


class PromptLocalFallbackDecisionTests(unittest.TestCase):
    """`prompt_local_fallback_decision` is the interactive gate a caller hits
    when `probe_local_model_availability` returns `None` (spec 0011 ticket
    02's "explicit, interactive user decision" requirement).
    """

    def test_choosing_1_launches_lmstudio(self) -> None:
        result = production_invoker.prompt_local_fallback_decision(prompt_fn=lambda _: "1")

        self.assertEqual(result, "launch-lmstudio")

    def test_choosing_2_falls_back_to_gemini_flash(self) -> None:
        result = production_invoker.prompt_local_fallback_decision(prompt_fn=lambda _: "2")

        self.assertEqual(result, "gemini-3.7-flash")

    def test_empty_input_falls_back_to_gemini_flash(self) -> None:
        result = production_invoker.prompt_local_fallback_decision(prompt_fn=lambda _: "")

        self.assertEqual(result, "gemini-3.7-flash")

    def test_unrecognized_input_falls_back_to_gemini_flash(self) -> None:
        result = production_invoker.prompt_local_fallback_decision(
            prompt_fn=lambda _: "banana"
        )

        self.assertEqual(result, "gemini-3.7-flash")

    def test_prompt_message_lists_both_options(self) -> None:
        captured: list[str] = []

        def fake_prompt(message: str) -> str:
            captured.append(message)
            return "1"

        production_invoker.prompt_local_fallback_decision(prompt_fn=fake_prompt)

        self.assertEqual(len(captured), 1)
        self.assertIn("LM Studio is offline", captured[0])
        self.assertIn("[1] Launch local model", captured[0])
        self.assertIn("[2] Fallback to Gemini Flash (Cloud)", captured[0])

    def test_writes_message_to_an_injected_stream_when_provided(self) -> None:
        stream = io.StringIO()

        production_invoker.prompt_local_fallback_decision(
            prompt_fn=lambda _: "1", stream=stream
        )

        self.assertIn("LM Studio is offline", stream.getvalue())


class BackwardsCompatibilityAndSyncCallerTests(unittest.TestCase):
    """Ticket 04's compatibility contract for synchronous worker callers.

    The async batch API is intentionally a separate contract.  These tests
    pin the older three-argument callable that both existing callers and the
    advisory debate loop continue to use.
    """

    def test_sync_caller_signatures_and_defaults_are_unchanged(self) -> None:
        invoke = inspect.signature(production_invoker.invoke_worker)
        self.assertEqual(tuple(invoke.parameters), ("model", "effort", "prompt", "timeout", "runner"))
        self.assertEqual(invoke.parameters["model"].kind, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        self.assertEqual(invoke.parameters["effort"].kind, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        self.assertEqual(invoke.parameters["prompt"].kind, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        self.assertEqual(invoke.parameters["timeout"].kind, inspect.Parameter.KEYWORD_ONLY)
        self.assertEqual(invoke.parameters["timeout"].default, production_invoker.DEFAULT_TIMEOUT_SECONDS)
        self.assertEqual(invoke.parameters["runner"].kind, inspect.Parameter.KEYWORD_ONLY)
        self.assertIs(invoke.parameters["runner"].default, subprocess.run)

        factory = inspect.signature(production_invoker.make_journaled_invoke_worker)
        self.assertEqual(
            tuple(factory.parameters),
            ("task_id", "root_dir", "task_type", "run_id", "timeout", "runner", "clock", "report_journal_error"),
        )
        self.assertEqual(factory.parameters["task_id"].kind, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        for name in tuple(factory.parameters)[1:]:
            self.assertEqual(factory.parameters[name].kind, inspect.Parameter.KEYWORD_ONLY)
        self.assertIsNone(factory.parameters["task_type"].default)
        self.assertIsNone(factory.parameters["run_id"].default)
        self.assertEqual(factory.parameters["timeout"].default, production_invoker.DEFAULT_TIMEOUT_SECONDS)
        self.assertIs(factory.parameters["runner"].default, subprocess.run)

    def test_journaled_sync_caller_closes_context_and_preserves_result_and_exception(self) -> None:
        runner = Mock(
            side_effect=[
                subprocess.CompletedProcess([], 0, "worker output", ""),
                subprocess.CompletedProcess([], 9, "partial", "failure"),
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            journaled = production_invoker.make_journaled_invoke_worker(
                "task-compat-04", root_dir=root, runner=runner,
                clock=_FakeClock([0.0, 0.1, 1.0, 1.2]),
            )
            self.assertEqual(journaled("claude-sonnet-5", "high", "Implement"), "worker output")
            with self.assertRaisesRegex(RuntimeError, r"exit code 9.*partial.*failure"):
                journaled("gpt-5.6-sol", "medium", "Review")
            records = _read_jsonl(learning_journal.journal_path(root))

        self.assertEqual(len(records), 2)
        self.assertEqual({record["task_id"] for record in records}, {"task-compat-04"})
        self.assertEqual([record["success"] for record in records], [True, False])
        self.assertEqual([record["duration_ms"] for record in records], [100, 200])

    def test_defaults_to_production_invoke_worker(self) -> None:
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

        with (
            patch.object(production_invoker, "invoke_worker", fake_invoke_worker),
            patch.object(
                production_invoker,
                "make_journaled_invoke_worker",
                fake_make_journaled_invoke_worker,
            ),
            tempfile.TemporaryDirectory() as tmp,
        ):
            result = critical_dialogue.run_advisory_consultation_debate(
                "Plan the implementation", root_dir=Path(tmp)
            )

        self.assertTrue(result.consensus_reached)
        self.assertEqual(len(calls), 2)

    def test_debate_loop_accepts_a_journaled_sync_caller(self) -> None:
        runner = Mock(
            side_effect=[
                subprocess.CompletedProcess([], 0, "Planner plan", ""),
                subprocess.CompletedProcess([], 0, _approve("Planner plan"), ""),
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            journaled = production_invoker.make_journaled_invoke_worker(
                "task-advisory-compat-04", root_dir=root, runner=runner
            )
            result = critical_dialogue.run_advisory_consultation_debate(
                "Plan the implementation",
                invoke_worker=journaled,
                root_dir=root,
                task_id="task-advisory-compat-04",
            )
            records = _read_jsonl(learning_journal.journal_path(root))

        self.assertTrue(result.consensus_reached)
        worker_records = [record for record in records if record["kind"] == "worker_execution"]
        self.assertEqual(len(worker_records), 2)
        self.assertEqual({record["task_id"] for record in worker_records}, {"task-advisory-compat-04"})

    def test_agy_command_uses_tokenized_prompt(self) -> None:
        command = production_invoker.build_worker_command("gemini-3.6-flash", "medium", "Research")

        self.assertEqual(
            command,
            ["agy", "-p", "[WORKER-MODE: NESTED-EXEC] Research"],
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
        self.assertEqual(command[-1], "[WORKER-MODE: NESTED-EXEC] Implement it")
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


class InvokeWorkerLocalModelParsingTests(unittest.TestCase):
    """`LOCAL_MODELS` are routed through raw `curl`, so `invoke_worker` has

    to unwrap the OpenAI-compatible chat-completions JSON body itself
    rather than returning it as-is — otherwise a caller expecting worker
    prose gets a raw JSON envelope instead.
    """

    def test_success_extracts_the_message_content(self) -> None:
        runner = Mock(
            return_value=subprocess.CompletedProcess(
                [],
                0,
                json.dumps(
                    {"choices": [{"message": {"content": "the actual reply"}}]}
                ),
                "",
            )
        )

        output = production_invoker.invoke_worker(
            "local-lmstudio", "low", "ping", runner=runner
        )

        self.assertEqual(output, "the actual reply")

    def test_invalid_json_raises(self) -> None:
        runner = Mock(
            return_value=subprocess.CompletedProcess([], 0, "not json at all", "")
        )

        with self.assertRaisesRegex(RuntimeError, "unparseable"):
            production_invoker.invoke_worker("local-lmstudio", "low", "ping", runner=runner)

    def test_missing_choices_key_raises(self) -> None:
        runner = Mock(
            return_value=subprocess.CompletedProcess([], 0, json.dumps({"other": "shape"}), "")
        )

        with self.assertRaisesRegex(RuntimeError, "unparseable"):
            production_invoker.invoke_worker("local-lmstudio", "low", "ping", runner=runner)

    def test_empty_choices_list_raises(self) -> None:
        runner = Mock(
            return_value=subprocess.CompletedProcess([], 0, json.dumps({"choices": []}), "")
        )

        with self.assertRaisesRegex(RuntimeError, "unparseable"):
            production_invoker.invoke_worker("local-lmstudio", "low", "ping", runner=runner)

    def test_non_local_model_stdout_is_returned_unparsed(self) -> None:
        # A non-`LOCAL_MODELS` worker's stdout is prose, not a
        # chat-completions envelope — it must pass through untouched even
        # when it happens to look like JSON.
        runner = Mock(
            return_value=subprocess.CompletedProcess(
                [], 0, json.dumps({"choices": [{"message": {"content": "x"}}]}), ""
            )
        )

        output = production_invoker.invoke_worker(
            "claude-sonnet-5", "high", "Implement it", runner=runner
        )

        self.assertEqual(output, json.dumps({"choices": [{"message": {"content": "x"}}]}))


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
        self.assertEqual(args[-1], "[WORKER-MODE: NESTED-EXEC] Implement it")
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

    async def test_local_model_success_extracts_the_message_content(self) -> None:
        process = _FakeAsyncProcess(
            stdout=json.dumps(
                {"choices": [{"message": {"content": "the actual reply"}}]}
            ).encode("utf-8")
        )
        runner = _RecordingAsyncRunner(process)

        result = await production_invoker.invoke_worker_async(
            "local-lmstudio", "low", "ping", runner=runner
        )

        self.assertTrue(result.success)
        self.assertEqual(result.raw_output, "the actual reply")
        # Downstream parsing (`extract_review_payload`) must see the pure
        # content too, not the JSON envelope it was pulled from.
        assert result.parsed_payload is not None

    async def test_local_model_malformed_response_returns_a_failed_result_not_a_raise(
        self,
    ) -> None:
        process = _FakeAsyncProcess(stdout=b"not json at all")
        runner = _RecordingAsyncRunner(process)

        result = await production_invoker.invoke_worker_async(
            "local-lmstudio", "low", "ping", runner=runner
        )

        self.assertFalse(result.success)
        self.assertEqual(result.raw_output, "")
        assert result.error is not None
        self.assertIn("unparseable", result.error)

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

        requests: list[tuple[str, str, str]] = [
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

        requests: list[tuple[str, str, str]] = [
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

        requests: list[tuple[str, str, str]] = [
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

        requests: list[tuple[str, str, str]] = [
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
        afterwards. `critical_dialogue` wraps this call in the try that
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
            production_invoker.learning_journal,
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

    def test_every_routable_cloud_model_has_a_positive_rate(self) -> None:
        """A priced cloud model with a measurable duration always produces a
        non-zero estimate, so a 0.0 on a real cloud model means only "this
        call took no measurable time" — never "we could not price it", which
        is what `model_id` says."""
        cloud_models = (
            set(production_invoker.MODEL_ALIASES.values())
            - production_invoker.LOCAL_MODELS
        )
        for model_id in sorted(cloud_models):
            with self.subTest(model_id=model_id):
                self.assertGreater(production_invoker.USD_PER_SECOND[model_id], 0.0)
                self.assertGreater(
                    production_invoker.estimate_cost_usd(model_id, 60_000), 0.0
                )

    def test_every_local_model_is_priced_at_zero(self) -> None:
        """Tier 0 (local) models cost nothing to invoke — `0.0` here is a real
        measurement, not a missing rate: `test_rate_table_prices_every_invocable_model`
        already pins that every invocable model, local included, has an entry."""
        for model_id in sorted(production_invoker.LOCAL_MODELS):
            with self.subTest(model_id=model_id):
                self.assertEqual(production_invoker.USD_PER_SECOND[model_id], 0.0)
                self.assertEqual(
                    production_invoker.estimate_cost_usd(model_id, 60_000), 0.0
                )


class RoleResolverTests(unittest.TestCase):
    """Unit tests for RoleResolver parsing, lookup, and metadata."""

    def setUp(self) -> None:
        production_invoker.reset_default_role_resolver()
        self.resolver = production_invoker.get_default_role_resolver()

    def test_parses_all_declared_roles(self) -> None:
        expected_roles = {
            "planner",
            "builder_heavy",
            "builder_light",
            "reviewer_architecture",
            "reviewer_risk",
            "reviewer_maintainability",
            "reviewer_security",
            "adjudicator",
            "sensitive_executor",
        }
        roles = set(self.resolver.list_roles())
        self.assertTrue(expected_roles.issubset(roles))
        for role in expected_roles:
            self.assertTrue(self.resolver.is_role(role))

    def test_parses_all_declared_providers(self) -> None:
        expected_providers = {
            "claude_opus_5",
            "claude_fable_5",
            "claude_sonnet_5",
            "codex_sol",
            "codex_terra",
            "codex_luna",
            "gemini_flash_high",
            "gemini_pro",
            "lm_studio_local",
        }
        providers = set(self.resolver.list_providers())
        self.assertTrue(expected_providers.issubset(providers))

    def test_get_role_config_returns_typed_dataclass(self) -> None:
        cfg = self.resolver.get_role_config("builder_heavy")
        self.assertEqual(cfg.role_id, "builder_heavy")
        self.assertEqual(cfg.capability_requirements.reasoning_tier, "high")
        self.assertEqual(cfg.capability_requirements.tool_access, "workspace-write")
        self.assertEqual(cfg.capability_requirements.min_context, 128000)
        self.assertFalse(cfg.capability_requirements.local_only)
        self.assertEqual(cfg.preferred_providers, ("claude_sonnet_5", "gemini_flash_high"))

    def test_get_provider_config_returns_typed_dataclass(self) -> None:
        pcfg = self.resolver.get_provider_config("claude_sonnet_5")
        self.assertEqual(pcfg.provider_id, "claude_sonnet_5")
        self.assertEqual(pcfg.adapter, "claude_code_cli")
        self.assertEqual(pcfg.model, "claude-sonnet-5")
        self.assertEqual(pcfg.default_reasoning_effort, "high")

    def test_unknown_role_raises_value_error(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unknown role: 'unknown_role'"):
            self.resolver.get_role_config("unknown_role")
        with self.assertRaisesRegex(ValueError, "Unknown role: 'unknown_role'"):
            self.resolver.resolve_role("unknown_role")
        self.assertFalse(self.resolver.is_role("unknown_role"))

    def test_unknown_provider_raises_value_error(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unknown provider: 'unknown_provider'"):
            self.resolver.get_provider_config("unknown_provider")

    def test_custom_config_injection(self) -> None:
        custom_cfg = {
            "roles": {
                "custom_worker": {
                    "capability_requirements": {
                        "reasoning_tier": "medium",
                        "tool_access": "read",
                        "min_context": 16000,
                        "local_only": False,
                    },
                    "preferred_providers": ["test_prov"],
                }
            },
            "providers": {
                "test_prov": {
                    "adapter": "codex_cli",
                    "model": "gpt-5.6-terra",
                    "default_reasoning_effort": "medium",
                }
            },
        }
        custom_resolver = production_invoker.RoleResolver(config=custom_cfg)
        self.assertTrue(custom_resolver.is_role("custom_worker"))
        resolved = custom_resolver.resolve_role("custom_worker")
        self.assertEqual(resolved.role_id, "custom_worker")
        self.assertEqual(resolved.provider_id, "test_prov")
        self.assertEqual(resolved.model, "gpt-5.6-terra")


class RoleResolutionFallbackTests(unittest.TestCase):
    """Unit tests for preference fallback resolution along provider chains."""

    def setUp(self) -> None:
        self.resolver = production_invoker.get_default_role_resolver()

    def test_resolves_primary_provider_by_default(self) -> None:
        resolved = self.resolver.resolve_role("planner")
        self.assertEqual(resolved.role_id, "planner")
        self.assertEqual(resolved.provider_id, "claude_opus_5")
        self.assertEqual(resolved.model, "claude-opus-5")
        self.assertEqual(resolved.reasoning_effort, "high")

    def test_falls_back_to_second_provider_when_primary_unavailable(self) -> None:
        resolved = self.resolver.resolve_role(
            "planner", unavailable_providers=["claude_opus_5"]
        )
        self.assertEqual(resolved.provider_id, "claude_fable_5")
        self.assertEqual(resolved.model, "claude-fable-5")

    def test_falls_back_to_third_provider_when_first_two_unavailable(self) -> None:
        resolved = self.resolver.resolve_role(
            "planner", unavailable_providers=["claude_opus_5", "claude_fable_5"]
        )
        self.assertEqual(resolved.provider_id, "codex_sol")
        self.assertEqual(resolved.model, "gpt-5.6-sol")

    def test_raises_runtime_error_when_all_preferred_providers_exhausted(self) -> None:
        with self.assertRaisesRegex(RuntimeError, r"No available provider found for role 'planner'"):
            self.resolver.resolve_role(
                "planner",
                unavailable_providers=["claude_opus_5", "claude_fable_5", "codex_sol"],
            )

    def test_availability_checker_callable(self) -> None:
        def fake_checker(pid: str) -> bool:
            return pid == "gemini_flash_high"

        resolved = self.resolver.resolve_role(
            "builder_heavy", availability_checker=fake_checker
        )
        self.assertEqual(resolved.provider_id, "gemini_flash_high")
        self.assertEqual(resolved.model, "gemini-3.6-flash")

    def test_effort_override_is_preserved_on_resolution(self) -> None:
        resolved = self.resolver.resolve_role("planner", effort="ultra")
        self.assertEqual(resolved.reasoning_effort, "ultra")


class LocalFailClosedTests(unittest.TestCase):
    """Unit tests for strict fail-closed enforcement of local_only roles."""

    def setUp(self) -> None:
        self.resolver = production_invoker.get_default_role_resolver()

    def test_adjudicator_resolves_local_model_when_available(self) -> None:
        resolved = self.resolver.resolve_role("adjudicator")
        self.assertEqual(resolved.provider_id, "lm_studio_local")
        self.assertEqual(resolved.model, "qwen3-coder-30b")
        self.assertTrue(resolved.capability_requirements.local_only)

    def test_sensitive_executor_resolves_local_model_when_available(self) -> None:
        resolved = self.resolver.resolve_role("sensitive_executor")
        self.assertEqual(resolved.provider_id, "lm_studio_local")
        self.assertEqual(resolved.model, "qwen3-coder-30b")
        self.assertTrue(resolved.capability_requirements.local_only)

    def test_adjudicator_fails_closed_when_local_offline(self) -> None:
        with self.assertRaisesRegex(
            RuntimeError,
            r"Local provider 'lm_studio_local' for role 'adjudicator' is unavailable; cloud fallback is rejected for local_only roles",
        ):
            self.resolver.resolve_role(
                "adjudicator", unavailable_providers=["lm_studio_local"]
            )

    def test_sensitive_executor_fails_closed_when_local_offline(self) -> None:
        with self.assertRaisesRegex(
            RuntimeError,
            r"Local provider 'lm_studio_local' for role 'sensitive_executor' is unavailable; cloud fallback is rejected for local_only roles",
        ):
            self.resolver.resolve_role(
                "sensitive_executor", unavailable_providers=["lm_studio_local"]
            )

    def test_local_only_role_with_failing_checker_fails_closed(self) -> None:
        with self.assertRaisesRegex(
            RuntimeError,
            r"Local provider 'lm_studio_local' for role 'sensitive_executor' is unavailable; cloud fallback is rejected for local_only roles",
        ):
            self.resolver.resolve_role(
                "sensitive_executor", availability_checker=lambda _: False
            )


class BuildWorkerCommandRoleResolutionTests(unittest.TestCase):
    """Unit tests for build_worker_command with role names and fallback kwargs."""

    def test_builder_heavy_resolves_to_claude_command(self) -> None:
        command = production_invoker.build_worker_command(
            "builder_heavy", "high", "Write code"
        )
        self.assertEqual(command[0], "claude")
        self.assertIn("--model", command)
        self.assertEqual(command[command.index("--model") + 1], "claude-sonnet-5")
        self.assertEqual(command[command.index("--effort") + 1], "high")
        self.assertEqual(command[-1], "[WORKER-MODE: NESTED-EXEC] Write code")

    def test_builder_light_resolves_to_codex_command(self) -> None:
        command = production_invoker.build_worker_command(
            "builder_light", "medium", "Fix typo"
        )
        self.assertEqual(command[0:2], ["codex", "exec"])
        self.assertEqual(command[command.index("--model") + 1], "gpt-5.6-terra")
        self.assertIn('model_reasoning_effort="medium"', command)

    def test_reviewer_maintainability_resolves_to_agy_command(self) -> None:
        command = production_invoker.build_worker_command(
            "reviewer_maintainability", "medium", "Audit DRY"
        )
        self.assertEqual(command[0], "agy")
        self.assertEqual(command[1], "-p")
        self.assertEqual(command[-1], "[WORKER-MODE: NESTED-EXEC] Audit DRY")

    def test_adjudicator_resolves_to_local_curl_command(self) -> None:
        command = production_invoker.build_worker_command(
            "adjudicator", "high", "Adjudicate"
        )
        self.assertEqual(command[0], "curl")
        self.assertIn(production_invoker.LOCAL_MODEL_CHAT_ENDPOINT, command)
        payload = json.loads(command[-1])
        self.assertEqual(payload["model"], "qwen3-coder-30b")

    def test_sensitive_executor_resolves_to_local_curl_command(self) -> None:
        command = production_invoker.build_worker_command(
            "sensitive_executor", "high", "Process secret"
        )
        self.assertEqual(command[0], "curl")
        payload = json.loads(command[-1])
        self.assertEqual(payload["model"], "qwen3-coder-30b")

    def test_build_worker_command_with_unavailable_providers_fallback(self) -> None:
        command = production_invoker.build_worker_command(
            "planner",
            "high",
            "Plan",
            unavailable_providers=["claude_opus_5", "claude_fable_5"],
        )
        self.assertEqual(command[0:2], ["codex", "exec"])
        self.assertEqual(command[command.index("--model") + 1], "gpt-5.6-sol")

    def test_build_worker_command_local_only_fail_closed(self) -> None:
        with self.assertRaisesRegex(
            RuntimeError,
            r"Local provider 'lm_studio_local' for role 'sensitive_executor' is unavailable; cloud fallback is rejected for local_only roles",
        ):
            production_invoker.build_worker_command(
                "sensitive_executor",
                "high",
                "Process secret",
                unavailable_providers=["lm_studio_local"],
            )

    def test_build_worker_command_explicit_model_remains_backward_compatible(self) -> None:
        command = production_invoker.build_worker_command(
            "claude-sonnet-5", "high", "Implement"
        )
        self.assertEqual(command[0], "claude")
        self.assertEqual(command[command.index("--model") + 1], "claude-sonnet-5")


class ModelIdAndFamilyResolutionWithRolesTests(unittest.TestCase):
    """Unit tests for _resolve_model_id_and_family with abstract roles."""

    def test_resolves_role_to_concrete_model_id_and_family(self) -> None:
        cases = (
            ("planner", "claude-opus-5", "claude"),
            ("builder_heavy", "claude-sonnet-5", "claude"),
            ("builder_light", "gpt-5.6-terra", "codex"),
            ("reviewer_architecture", "gpt-5.6-sol", "codex"),
            ("reviewer_maintainability", "gemini-3.6-flash", "agy"),
            ("adjudicator", "qwen3-coder-30b", "lm-studio"),
            ("sensitive_executor", "qwen3-coder-30b", "lm-studio"),
        )
        for role_name, expected_model, expected_family in cases:
            with self.subTest(role=role_name):
                model_id, family = production_invoker._resolve_model_id_and_family(role_name)
                self.assertEqual(model_id, expected_model)
                self.assertEqual(family, expected_family)


class JournaledInvokeWorkerRoleTests(unittest.TestCase):
    """Unit tests for make_journaled_invoke_worker with abstract role names."""

    def test_journal_records_concrete_model_and_cost_for_role_invocation(self) -> None:
        runner = Mock(
            return_value=subprocess.CompletedProcess([], 0, "builder output", "")
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            journaled = production_invoker.make_journaled_invoke_worker(
                "task-role-03",
                root_dir=root,
                runner=runner,
                clock=_FakeClock([0.0, 1.0]),
            )
            output = journaled("builder_heavy", "high", "Build it")
            self.assertEqual(output, "builder output")

            records = _read_jsonl(learning_journal.journal_path(root))

        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["task_id"], "task-role-03")
        self.assertEqual(record["model_id"], "claude-sonnet-5")
        self.assertEqual(record["model_family"], "claude")
        self.assertTrue(record["success"])
        self.assertEqual(record["duration_ms"], 1000)
        self.assertAlmostEqual(record["cost_estimate_usd"], 0.006, places=4)


class DeclaredRoleCommandBuildTests(unittest.TestCase):
    """End-to-end dry-run validation (spec 0012 ticket 08): every declared
    role in routing-config.json must build a valid, non-interactive worker
    command without invoking a real worker."""

    DECLARED_ROLES = (
        "planner",
        "builder_heavy",
        "builder_light",
        "reviewer_architecture",
        "reviewer_risk",
        "reviewer_maintainability",
        "reviewer_security",
        "adjudicator",
        "sensitive_executor",
    )

    def test_every_declared_role_builds_a_valid_command(self) -> None:
        resolver = production_invoker.get_default_role_resolver()
        self.assertEqual(set(resolver.list_roles()), set(self.DECLARED_ROLES))
        for role in self.DECLARED_ROLES:
            with self.subTest(role=role):
                command = production_invoker.build_worker_command(role, "high", "Dry run")
                self.assertTrue(command)
                self.assertTrue(all(isinstance(part, str) for part in command))
                self.assertIn(production_invoker.WORKER_MODE_TOKEN, command[-1])

    def test_cli_commands_carry_worker_mode_token_and_non_interactive_args(self) -> None:
        cli_roles = tuple(
            role for role in self.DECLARED_ROLES if role not in ("adjudicator", "sensitive_executor")
        )
        for role in cli_roles:
            with self.subTest(role=role):
                command = production_invoker.build_worker_command(role, "high", "Dry run")
                self.assertIn(production_invoker.WORKER_MODE_TOKEN, command[-1])
                if command[0] == "codex":
                    self.assertIn("exec", command)
                    self.assertIn("-s", command)
                    self.assertIn("workspace-write", command)
                elif command[0] == "claude":
                    self.assertIn("-p", command)
                    self.assertIn("--no-session-persistence", command)
                    self.assertIn("--allow-dangerously-skip-permissions", command)
                elif command[0] == "agy":
                    self.assertIn("-p", command)
                else:
                    self.fail(f"unexpected CLI family for role {role!r}: {command[0]!r}")

    def test_local_roles_generate_curl_payloads_targeting_lmstudio_endpoint(self) -> None:
        for role in ("adjudicator", "sensitive_executor"):
            with self.subTest(role=role):
                command = production_invoker.build_worker_command(role, "high", "Dry run")
                self.assertEqual(command[0], "curl")
                self.assertIn(production_invoker.LOCAL_MODEL_CHAT_ENDPOINT, command)
                payload = json.loads(command[-1])
                self.assertIn("model", payload)
                self.assertEqual(
                    payload["messages"][0]["content"],
                    f"{production_invoker.WORKER_MODE_TOKEN} Dry run",
                )


if __name__ == "__main__":
    unittest.main()
