"""Hermetic unit tests for pure debate orchestration state."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parent))

if __package__:
    from . import advisory_consultation, debate_orchestrator, dialogue_contracts, learned_state
else:
    import advisory_consultation  # type: ignore[no-redef]
    import debate_orchestrator  # type: ignore[no-redef]
    import dialogue_contracts  # type: ignore[no-redef]
    import learned_state  # type: ignore[no-redef]


class PanelTopologyTests(unittest.TestCase):
    def test_only_complex_reviews_use_the_panel(self) -> None:
        panel_occasions: tuple[dialogue_contracts.Occasion, ...] = ("plan-review", "code-review")
        for occasion in panel_occasions:
            self.assertTrue(debate_orchestrator.is_panel_topology(occasion, " Complex "))
        non_panel_occasions: tuple[dialogue_contracts.Occasion, ...] = ("ambiguity", "post-mortem")
        for occasion in non_panel_occasions:
            self.assertFalse(debate_orchestrator.is_panel_topology(occasion, "complex"))
        for complexity in ("trivial", "simple", "medium", "unknown"):
            self.assertFalse(debate_orchestrator.is_panel_topology("plan-review", complexity))


class StalemateReportTests(unittest.TestCase):
    def test_pair_report_keeps_one_critic_and_three_options(self) -> None:
        report = debate_orchestrator.build_stalemate_report("planner", "critic")

        self.assertEqual(report.planner_position, "planner")
        self.assertEqual(report.critic_position, "critic")
        self.assertIsNone(report.critic_b_position)
        self.assertEqual([option.id for option in report.options], [1, 2, 3])
        self.assertEqual(report.options[1].label, "Approve Critic Architecture")

    def test_panel_report_preserves_each_critic_and_combines_option_text(self) -> None:
        report = debate_orchestrator.build_stalemate_report("planner", "critic a", "critic b")

        self.assertEqual(report.critic_position, "critic a")
        self.assertEqual(report.critic_b_position, "critic b")
        self.assertEqual(report.options[1].label, "Approve Critics' Architecture")
        self.assertEqual(
            report.options[1].description,
            "Critic A:\ncritic a\n\nCritic B:\ncritic b",
        )


class VerdictEvaluationTests(unittest.TestCase):
    def test_single_critic_verdicts(self) -> None:
        self.assertEqual(debate_orchestrator.evaluate_round_verdicts("APPROVE"), (True, None))
        self.assertEqual(debate_orchestrator.evaluate_round_verdicts("REVISE"), (False, None))
        self.assertEqual(
            debate_orchestrator.evaluate_round_verdicts(None),
            (False, "unparseable verdict: None"),
        )

    def test_canonical_contract_verdicts_are_case_insensitive(self) -> None:
        approved = dialogue_contracts.VerdictContractResult("approved", 1, 0)
        revise = dialogue_contracts.VerdictContractResult("revise", 1, 1)
        malformed = dialogue_contracts.VerdictContractResult("unparseable", 0, 0)

        self.assertEqual(
            debate_orchestrator.evaluate_round_verdicts(approved.verdict), (True, None)
        )
        self.assertEqual(
            debate_orchestrator.evaluate_round_verdicts(revise.verdict), (False, None)
        )
        self.assertEqual(
            debate_orchestrator.evaluate_round_verdicts(malformed.verdict),
            (False, "unparseable verdict: unparseable"),
        )

    def test_panel_verdicts_require_both_approvals(self) -> None:
        self.assertEqual(
            debate_orchestrator.evaluate_round_verdicts("APPROVE", "APPROVE", is_panel=True),
            (True, None),
        )
        self.assertEqual(
            debate_orchestrator.evaluate_round_verdicts("APPROVE", "REVISE", is_panel=True),
            (False, None),
        )
        self.assertEqual(
            debate_orchestrator.evaluate_round_verdicts("APPROVE", None, is_panel=True),
            (False, "unparseable verdict: critic_a=APPROVE, critic_b=None"),
        )


class CriticResponsePayloadTests(unittest.TestCase):
    def test_vote_identity_uses_provider_family_for_weight_lookup(self) -> None:
        raw_response = '{"vote": "approve", "confidence": 1.0}'
        cases = (
            ("critic_a", "Claude Opus 5 (Thinking)", "claude"),
            ("critic_a", "Codex 5.6 Sol", "codex"),
            ("critic_b", "Gemini 3.6 Flash (High)", "gemini"),
            ("codex", None, "codex"),
            ("critic", None, "critic"),
        )

        for critic_id, model_name, expected_identity in cases:
            with self.subTest(model_name=model_name, critic_id=critic_id):
                response = debate_orchestrator._critic_response_from_payload(
                    critic_id, raw_response, model_name
                )
                self.assertEqual(response.critic_id, expected_identity)

    def test_missing_candidate_hash_is_not_replaced_with_expected_hash(self) -> None:
        response = debate_orchestrator._critic_response_from_payload(
            "critic_a",
            '{"vote": "approve", "confidence": 1.0}',
            "Codex 5.6 Sol",
        )

        self.assertIsNone(response.candidate_hash)


class ScopedMemoryPromptWiringTests(unittest.TestCase):
    def test_planner_prompt_embeds_scoped_institutional_memory_by_default(self) -> None:
        task = "Fix a flaky test that leaks mocks across the CI suite"
        expected_memory = debate_orchestrator.extract_scoped_memory(task)

        prompt = debate_orchestrator._build_planner_prompt(task)

        self.assertIn(expected_memory, prompt)
        self.assertLess(
            prompt.index(expected_memory), prompt.index("=== BEGIN TASK DESCRIPTION ===")
        )

    def test_critic_prompt_embeds_scoped_institutional_memory_by_default(self) -> None:
        task = "Fix a flaky test that leaks mocks across the CI suite"
        expected_memory = debate_orchestrator.extract_scoped_memory(task)

        prompt = debate_orchestrator._build_critic_prompt(task, "Proposed plan")

        self.assertIn(expected_memory, prompt)
        self.assertLess(
            prompt.index(expected_memory), prompt.index("=== BEGIN TASK DESCRIPTION ===")
        )

    def test_planner_prompt_scopes_against_adopted_memory_when_root_dir_given(self) -> None:
        task = "Improve the router keyword scoring"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            memory_content = (
                "Entry about database migration ordering.\n\n"
                "Entry about router keyword scoring and prompt injection.\n\n"
                "Entry about unrelated wombats.\n\n"
                "Entry about unrelated gadgets."
            )
            learned_state.adopt(
                [learned_state.DocumentChange(document="memory", content=memory_content)],
                root_dir=root,
                now=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )
            expected_memory = debate_orchestrator.get_scoped_memory(root, task)
            default_memory = debate_orchestrator.extract_scoped_memory(task)
            self.assertNotEqual(expected_memory, default_memory)

            prompt = debate_orchestrator._build_planner_prompt(task, root_dir=root)

            self.assertIn(expected_memory, prompt)
            self.assertNotIn(default_memory, prompt)

    def test_critic_prompt_scopes_against_adopted_memory_when_root_dir_given(self) -> None:
        task = "Improve the router keyword scoring"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            memory_content = (
                "Entry about database migration ordering.\n\n"
                "Entry about router keyword scoring and prompt injection.\n\n"
                "Entry about unrelated wombats.\n\n"
                "Entry about unrelated gadgets."
            )
            learned_state.adopt(
                [learned_state.DocumentChange(document="memory", content=memory_content)],
                root_dir=root,
                now=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )
            expected_memory = debate_orchestrator.get_scoped_memory(root, task)
            default_memory = debate_orchestrator.extract_scoped_memory(task)
            self.assertNotEqual(expected_memory, default_memory)

            prompt = debate_orchestrator._build_critic_prompt(
                task, "Proposed plan", root_dir=root
            )

            self.assertIn(expected_memory, prompt)
            self.assertNotIn(default_memory, prompt)

    def test_run_advisory_consultation_debate_scopes_worker_prompts_against_adopted_memory(
        self,
    ) -> None:
        """End-to-end: `run_advisory_consultation_debate` must thread its
        `root_dir` into both `_build_planner_prompt` and `_build_critic_prompt`
        so the Planner and Critic worker prompts it actually dispatches carry
        the adopted-memory-scoped rules, not the built-in `GOLDEN_RULES`
        fallback `extract_scoped_memory` uses when no `root_dir` reaches it.
        """
        task = "Improve the router keyword scoring"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            memory_content = (
                "Entry about database migration ordering.\n\n"
                "Entry about router keyword scoring and prompt injection.\n\n"
                "Entry about unrelated wombats.\n\n"
                "Entry about unrelated gadgets."
            )
            learned_state.adopt(
                [learned_state.DocumentChange(document="memory", content=memory_content)],
                root_dir=root,
                now=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )
            expected_memory = debate_orchestrator.get_scoped_memory(root, task)
            default_memory = debate_orchestrator.extract_scoped_memory(task)
            self.assertNotEqual(expected_memory, default_memory)

            planner_prompts: list[str] = []
            critic_prompts: list[str] = []

            def invoker(_model: str, _effort: str, prompt: str) -> str:
                if "You are the Planner" in prompt:
                    planner_prompts.append(prompt)
                    return "Proposed plan"
                critic_prompts.append(prompt)
                return 'QUOTE: "Proposed plan"\nVERDICT: APPROVE'

            result = debate_orchestrator.run_advisory_consultation_debate(
                task, invoker, root_dir=root
            )

            self.assertEqual(result.outcome, "consensus")
            self.assertEqual(len(planner_prompts), 1)
            self.assertEqual(len(critic_prompts), 1)
            self.assertIn(expected_memory, planner_prompts[0])
            self.assertNotIn(default_memory, planner_prompts[0])
            self.assertIn(expected_memory, critic_prompts[0])
            self.assertNotIn(default_memory, critic_prompts[0])

    def test_planner_prompt_scopes_by_target_files_file_pattern(self) -> None:
        # Keyword-neutral task (mirrors
        # test_prompt_assembler.ExtractScopedMemoryTests
        # .test_target_file_pattern_match_influences_ranking): any change in
        # ranking has to come from the `target_files` file-pattern match
        # alone, not from keyword overlap with the task text.
        task = "Perform regular repository maintenance"
        expected_memory = debate_orchestrator.extract_scoped_memory(
            task, target_files=["NOTES.md"]
        )
        default_memory = debate_orchestrator.extract_scoped_memory(task)
        self.assertNotEqual(expected_memory, default_memory)

        prompt = debate_orchestrator._build_planner_prompt(task, target_files=["NOTES.md"])

        self.assertIn(expected_memory, prompt)
        self.assertNotIn(default_memory, prompt)

    def test_critic_prompt_scopes_by_target_files_file_pattern(self) -> None:
        task = "Perform regular repository maintenance"
        expected_memory = debate_orchestrator.extract_scoped_memory(
            task, target_files=["NOTES.md"]
        )
        default_memory = debate_orchestrator.extract_scoped_memory(task)
        self.assertNotEqual(expected_memory, default_memory)

        prompt = debate_orchestrator._build_critic_prompt(
            task, "Proposed plan", target_files=["NOTES.md"]
        )

        self.assertIn(expected_memory, prompt)
        self.assertNotIn(default_memory, prompt)

    def test_run_advisory_consultation_debate_threads_target_files_into_worker_prompts(
        self,
    ) -> None:
        task = "Perform regular repository maintenance"
        expected_memory = debate_orchestrator.extract_scoped_memory(
            task, target_files=["NOTES.md"]
        )
        default_memory = debate_orchestrator.extract_scoped_memory(task)
        self.assertNotEqual(expected_memory, default_memory)

        planner_prompts: list[str] = []
        critic_prompts: list[str] = []

        def invoker(_model: str, _effort: str, prompt: str) -> str:
            if "You are the Planner" in prompt:
                planner_prompts.append(prompt)
                return "Proposed plan"
            critic_prompts.append(prompt)
            return 'QUOTE: "Proposed plan"\nVERDICT: APPROVE'

        with tempfile.TemporaryDirectory() as tmp:
            result = debate_orchestrator.run_advisory_consultation_debate(
                task, invoker, root_dir=Path(tmp), target_files=["NOTES.md"]
            )

        self.assertEqual(result.outcome, "consensus")
        self.assertIn(expected_memory, planner_prompts[0])
        self.assertNotIn(default_memory, planner_prompts[0])
        self.assertIn(expected_memory, critic_prompts[0])
        self.assertNotIn(default_memory, critic_prompts[0])


class DebateStateTests(unittest.TestCase):
    def test_round_and_session_state_construct_with_safe_defaults(self) -> None:
        record = debate_orchestrator.DebateRoundRecord(
            1, "plan", "critic", "critic b", "REVISE", "APPROVE"
        )
        state = debate_orchestrator.DebateSessionState("plan-review", "complex", 3, True)

        self.assertEqual(record.round_index, 1)
        self.assertFalse(record.is_consensus)
        self.assertEqual(state.rounds, ())
        self.assertFalse(state.consensus_reached)
        with self.assertRaises(FrozenInstanceError):
            state.rounds = (record,)

    def test_advance_returns_a_new_pair_or_panel_state(self) -> None:
        pair = debate_orchestrator.DebateSessionState("ambiguity", "medium", 2, False)
        revised = debate_orchestrator.advance_debate_state(
            pair, debate_orchestrator.DebateRoundRecord(1, "plan", "critique", critic_a_verdict="REVISE")
        )
        approved = debate_orchestrator.advance_debate_state(
            debate_orchestrator.DebateSessionState("plan-review", "complex", 2, True),
            debate_orchestrator.DebateRoundRecord(1, "panel plan", "a", "b", "APPROVE", "APPROVE"),
        )
        self.assertEqual(pair.rounds, ())
        self.assertIsInstance(revised.rounds, tuple)
        self.assertEqual(len(revised.rounds), 1)
        self.assertTrue(approved.consensus_reached)
        self.assertEqual(approved.final_plan, "panel plan")

    def test_advance_normalizes_record_outcome_fields(self) -> None:
        state = debate_orchestrator.DebateSessionState("ambiguity", "medium", 2, False)
        result = debate_orchestrator.advance_debate_state(
            state,
            debate_orchestrator.DebateRoundRecord(
                1, "plan", "critique", critic_a_verdict="APPROVE", is_consensus=False, error="stale"
            ),
        )

        self.assertTrue(result.consensus_reached)
        self.assertTrue(result.rounds[0].is_consensus)
        self.assertIsNone(result.rounds[0].error)

    def test_advance_leaves_terminal_states_unchanged(self) -> None:
        record = debate_orchestrator.DebateRoundRecord(2, "new", "critic", critic_a_verdict="APPROVE")
        terminal_states = (
            debate_orchestrator.DebateSessionState("ambiguity", "medium", 2, False, consensus_reached=True),
            debate_orchestrator.DebateSessionState("ambiguity", "medium", 2, False, error="failed"),
            debate_orchestrator.DebateSessionState(
                "ambiguity", "medium", 2, False,
                stalemate_report=debate_orchestrator.build_stalemate_report("plan", "critic"),
            ),
            debate_orchestrator.DebateSessionState(
                "ambiguity", "medium", 1, False,
                rounds=(debate_orchestrator.DebateRoundRecord(1, "plan", "critic"),),
            ),
        )

        for state in terminal_states:
            with self.subTest(state=state):
                self.assertIs(debate_orchestrator.advance_debate_state(state, record), state)


class ProductionOrchestrationTests(unittest.TestCase):
    def test_roster_resolution_prefers_distinct_reachable_families(self) -> None:
        resolution = debate_orchestrator.resolve_roster(
            "pair", is_family_reachable=lambda family: family in {"claude", "codex-gpt"}
        )
        self.assertEqual(resolution.model_for("planner"), "Claude Opus 5 (Thinking)")
        self.assertEqual(resolution.model_for("critic_a"), "Codex 5.6 Sol")
        self.assertFalse(resolution.degraded_independence)

    def test_canary_execution_returns_a_measurement(self) -> None:
        fixture = debate_orchestrator.CANARY_FIXTURES[0]
        response = f'QUOTE: "{fixture.plan_text.splitlines()[0]}"\\n1. flaw found\\nVERDICT: REVISE'
        with tempfile.TemporaryDirectory() as tmp:
            result = debate_orchestrator.run_canary_dialogue(
                "unused", lambda *_args: response, root_dir=Path(tmp), canary_fixture=fixture
            )
        self.assertEqual(result.outcome, "canary")
        self.assertEqual(result.canary_result, "catch")

    def test_budget_degradation_alert_is_emitted_to_stderr(self) -> None:
        def invoker(_model: str, _effort: str, prompt: str) -> str:
            if "You are the Planner" in prompt:
                return "Proposed plan"
            return 'QUOTE: "Proposed plan"\nVERDICT: APPROVE'

        stderr = io.StringIO()
        cap = debate_orchestrator._load_dialogue_budget_config(
            debate_orchestrator._CONFIG_PATH
        )
        with tempfile.TemporaryDirectory() as tmp, redirect_stderr(stderr):
            result = debate_orchestrator.run_advisory_consultation_debate(
                "Plan the implementation",
                invoker,
                root_dir=Path(tmp),
                session_spend_so_far=cap,
            )

        self.assertEqual(result.degradation_rung, 1)
        assert result.executive_report is not None
        self.assertEqual(stderr.getvalue(), result.executive_report.budget_alert)

    def test_consecutive_default_path_worker_failures_alert_to_errors_md(self) -> None:
        """debate_orchestrator's default worker path routes isolated process
        execution and failure alerting through its re-exported DebateTransport
        / RecurringFailureNotifier (see `run_advisory_consultation_debate`'s
        `invoke_worker is None` branch), rather than a hand-rolled notifier
        closure. This exercises that wiring directly through the same
        symbols the orchestrator itself instantiates.
        """

        def failing_runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="worker unreachable")

        with tempfile.TemporaryDirectory() as tmp:
            root_dir = Path(tmp)
            transport = debate_orchestrator.DebateTransport(
                runner=failing_runner,
                notifier=debate_orchestrator.RecurringFailureNotifier(
                    threshold=debate_orchestrator.ESCALATION_FAILURE_THRESHOLD
                ),
                root_dir=root_dir,
            )
            errors_path = root_dir / "ERRORS.md"

            with self.assertRaises(RuntimeError):
                transport.invoke_worker("Codex 5.6 Sol", "high", "prompt one")
            self.assertFalse(errors_path.exists())

            with self.assertRaises(RuntimeError):
                transport.invoke_worker("Codex 5.6 Sol", "high", "prompt two")

            self.assertTrue(errors_path.exists())
            contents = errors_path.read_text(encoding="utf-8")
            self.assertIn("## Recurring worker failure", contents)
            self.assertIn("Model: `Codex 5.6 Sol`", contents)
            self.assertIn("Consecutive failures: 2", contents)

    def test_default_path_invoke_worker_none_alerts_through_run_advisory_consultation_debate(
        self,
    ) -> None:
        """`run_advisory_consultation_debate`'s own `invoke_worker is None`
        default path (not `DebateTransport` invoked directly, as
        `test_consecutive_default_path_worker_failures_alert_to_errors_md`
        above does) must still wire through the shared transport/notifier
        pair, so a real production outage escalates to `ERRORS.md` from the
        orchestrator entry point production callers actually use. The
        escalation threshold is patched to 1 here: every `invoke_worker`
        failure inside `run_advisory_consultation_debate` returns
        immediately (fail-closed), so a single call can never produce two
        failures for the same model and reach the real threshold of 2.
        """

        def failing_invoke_worker(model: str, effort: str, prompt: str, **_kwargs: object) -> str:
            raise RuntimeError("simulated production_invoker failure")

        with tempfile.TemporaryDirectory() as tmp:
            root_dir = Path(tmp)
            errors_path = root_dir / "ERRORS.md"
            with patch.object(
                debate_orchestrator._current_production_invoker(), "invoke_worker", failing_invoke_worker
            ), patch.object(debate_orchestrator, "ESCALATION_FAILURE_THRESHOLD", 1):
                result = debate_orchestrator.run_advisory_consultation_debate(
                    "Plan the implementation", invoke_worker=None, root_dir=root_dir
                )

            self.assertEqual(result.outcome, "worker_error")
            self.assertTrue(errors_path.exists())
            contents = errors_path.read_text(encoding="utf-8")
            self.assertIn("## Recurring worker failure", contents)
            self.assertIn("Consecutive failures: 1", contents)

    def test_run_advisory_consultation_debate_calls_through_resolve_topology(self) -> None:
        """Ticket 43.1: `run_advisory_consultation_debate` must resolve its
        `panel_mode` local through `resolve_topology` (re-exported from
        `debate_state_machine` as `_resolve_topology`) rather than
        duplicating `is_panel_topology`'s boolean inline, so
        `resolve_topology` has a real production caller."""

        def invoker(_model: str, _effort: str, prompt: str) -> str:
            if "You are the Planner" in prompt:
                return "Proposed plan"
            return 'QUOTE: "Proposed plan"\nVERDICT: APPROVE'

        real_resolve_topology = debate_orchestrator._resolve_topology
        calls: list[tuple[str, str]] = []

        def spy(occasion: str, complexity: str) -> str:
            calls.append((occasion, complexity))
            return real_resolve_topology(occasion, complexity)

        with tempfile.TemporaryDirectory() as tmp, patch.object(
            debate_orchestrator, "_resolve_topology", spy
        ):
            result = debate_orchestrator.run_advisory_consultation_debate(
                "Plan the implementation", invoker, root_dir=Path(tmp), complexity="medium"
            )

        self.assertEqual(calls, [("ambiguity", "medium")])
        self.assertEqual(result.outcome, "consensus")
        self.assertEqual(result.topology, "pair")


class SecurityVetoAndManifestTests(unittest.TestCase):
    SECRET = b"ticket-03-test-secret"

    @staticmethod
    def _write_secret(root: Path, secret: bytes = SECRET) -> None:
        key_path = root / ".ralph" / "cache" / "calibration.key"
        key_path.parent.mkdir(parents=True, exist_ok=True)
        key_path.write_bytes(secret)

    def _assert_valid_manifest(self, path: str, expected_status: str) -> dict[str, Any]:
        manifest = json.loads(Path(path).read_text(encoding="utf-8"))
        signature = manifest.pop("council_hmac")
        canonical = json.dumps(
            manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        self.assertEqual(signature, hmac.new(self.SECRET, canonical, hashlib.sha256).hexdigest())
        self.assertEqual(manifest["metadata"]["status"], expected_status)
        return manifest

    def test_manifest_filename_sanitizes_run_id_without_changing_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_secret(root)

            manifest_path = Path(
                debate_orchestrator.write_council_manifest(
                    "UNANIMOUS", "../unsafe/run:id", root
                )
            )

            self.assertEqual(
                manifest_path,
                root / ".ralph" / "council-manifest-___unsafe_run_id.json",
            )
            manifest = self._assert_valid_manifest(str(manifest_path), "UNANIMOUS")
            self.assertEqual(manifest["metadata"]["run_id"], "../unsafe/run:id")

    @staticmethod
    def _review_response(
        plan: str,
        *,
        vote: str = "approve",
        verdict: str = "APPROVE",
        findings: list[dict[str, object]] | None = None,
        confidence: float = 1.0,
    ) -> str:
        payload = {
            "vote": vote,
            "confidence": confidence,
            "candidate_hash": hashlib.sha256(plan.encode("utf-8")).hexdigest(),
            "findings": findings or [],
        }
        return f'{json.dumps(payload)}\nQUOTE: "{plan}"\nVERDICT: {verdict}'

    def test_dyad_security_veto_halts_without_manifest(self) -> None:
        calls: list[str] = []

        def invoker(_model: str, _effort: str, prompt: str) -> str:
            calls.append(prompt)
            if "You are the Planner" in prompt:
                return "Proposed plan"
            return self._review_response(
                "Proposed plan",
                vote="block",
                verdict="REVISE",
                findings=[{"id": "SEC-DYAD", "severity": "HIGH", "confidence": 0.95}],
            )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "implementation_plan.md").write_text("stale", encoding="utf-8")
            result = debate_orchestrator.run_advisory_consultation_debate(
                "Plan safely", invoker, root_dir=root
            )

            self.assertEqual(result.outcome, "security_halt")
            self.assertEqual(len(calls), 2)
            self.assertIsNone(result.manifest_path)
            assert result.security_veto is not None
            self.assertEqual(result.security_veto.finding["id"], "SEC-DYAD")
            self.assertFalse((root / "implementation_plan.md").exists())
            self.assertEqual(list((root / ".ralph").glob("council-manifest-*.json")), [])

    def test_panel_security_veto_halts_and_signs_manifest(self) -> None:
        calls: list[str] = []

        def invoker(model: str, _effort: str, prompt: str) -> str:
            calls.append(model)
            if "You are the Planner" in prompt:
                return "Proposed plan"
            if "Gemini" in model:
                return self._review_response(
                    "Proposed plan",
                    vote="block",
                    verdict="REVISE",
                    findings=[{"id": "SEC-PANEL", "severity": "critical", "confidence": 0.9}],
                )
            return self._review_response("Proposed plan")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_secret(root)
            result = debate_orchestrator.run_advisory_consultation_debate(
                "Review architecture",
                invoker,
                root_dir=root,
                occasion="plan-review",
                complexity="complex",
            )

            self.assertEqual(result.outcome, "security_halt")
            self.assertEqual(len(calls), 3)
            assert result.manifest_path is not None
            assert result.security_veto is not None
            self.assertEqual(result.security_veto.provider, "gemini")
            manifest = self._assert_valid_manifest(result.manifest_path, "SECURITY_HALT")
            self.assertEqual(manifest["security_veto"]["finding"]["id"], "SEC-PANEL")

    def test_panel_consensus_manifests_record_genuine_status_and_are_signed(self) -> None:
        cases = (
            ("UNANIMOUS", None, "consensus", "UNANIMOUS"),
            (
                "QUALIFIED",
                {
                    "consensus_policy": [
                        "UNANIMOUS", "QUALIFIED", "MATERIAL_DISAGREEMENT", "INCOMPLETE", "UNRESOLVED"
                    ],
                    "weighting": {
                        "initial_weights": {"codex": 0.8, "gemini": 0.2},
                        "quorum_threshold": 0.7,
                    },
                },
                "consensus",
                "QUALIFIED",
            ),
            (
                "UNRESOLVED",
                {
                    "consensus_policy": [
                        "UNANIMOUS", "QUALIFIED", "MATERIAL_DISAGREEMENT", "INCOMPLETE", "UNRESOLVED"
                    ],
                    "weighting": {
                        "initial_weights": {"codex": 0.8, "gemini": 0.2},
                        "quorum_threshold": 0.7,
                    },
                },
                "stalemate",
                "STALEMATE",
            ),
        )
        for panel_status, policy, expected_outcome, manifest_status in cases:
            with self.subTest(status=panel_status), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                self._write_secret(root)

                def invoker(
                    model: str,
                    _effort: str,
                    prompt: str,
                    panel_status: str = panel_status,
                ) -> str:
                    if "You are the Planner" in prompt:
                        return "Proposed plan"
                    if panel_status == "UNRESOLVED":
                        return self._review_response(
                            "Proposed plan", vote="revise", verdict="APPROVE", confidence=0.0
                        )
                    if panel_status == "QUALIFIED" and "Gemini" in model:
                        return self._review_response(
                            "Proposed plan", vote="revise", verdict="REVISE", confidence=0.0
                        )
                    return self._review_response("Proposed plan")

                result = debate_orchestrator.run_advisory_consultation_debate(
                    "Review architecture",
                    invoker,
                    root_dir=root,
                    occasion="plan-review",
                    complexity="complex",
                    consultation_policy=policy,
                )

                self.assertEqual(result.outcome, expected_outcome)
                assert result.manifest_path is not None
                self._assert_valid_manifest(result.manifest_path, manifest_status)

    def test_panel_weighted_quorum_returns_qualified_consensus(self) -> None:
        policy = {
            "consensus_policy": [
                "UNANIMOUS",
                "QUALIFIED",
                "MATERIAL_DISAGREEMENT",
                "INCOMPLETE",
                "UNRESOLVED",
            ],
            "weighting": {
                "initial_weights": {"codex": 0.8, "gemini": 0.2},
                "quorum_threshold": 0.7,
            },
        }

        def invoker(model: str, _effort: str, prompt: str) -> str:
            if "You are the Planner" in prompt:
                return "Proposed plan"
            if "Gemini" in model:
                return self._review_response(
                    "Proposed plan", vote="revise", verdict="REVISE", confidence=0.0
                )
            return self._review_response("Proposed plan")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_secret(root)
            result = debate_orchestrator.run_advisory_consultation_debate(
                "Review architecture",
                invoker,
                root_dir=root,
                occasion="plan-review",
                complexity="complex",
                consultation_policy=policy,
            )

            self.assertEqual(result.outcome, "consensus")
            assert result.manifest_path is not None
            self._assert_valid_manifest(result.manifest_path, "QUALIFIED")

    def test_panel_stalemate_manifest_is_signed(self) -> None:
        def invoker(_model: str, _effort: str, prompt: str) -> str:
            if "You are the Planner" in prompt:
                return "Proposed plan"
            return self._review_response(
                "Proposed plan", vote="revise", verdict="REVISE", confidence=0.0
            )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_secret(root)
            result = debate_orchestrator.run_advisory_consultation_debate(
                "Review architecture",
                invoker,
                root_dir=root,
                occasion="plan-review",
                complexity="complex",
                max_rounds=1,
            )
            self.assertEqual(result.outcome, "stalemate")
            assert result.manifest_path is not None
            self._assert_valid_manifest(result.manifest_path, "STALEMATE")

    def test_panel_worker_error_and_malformed_verdict_manifests_are_signed(self) -> None:
        cases = (("worker_error", "WORKER_ERROR"), ("malformed", "MALFORMED_VERDICT"))
        for failure, manifest_status in cases:
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                self._write_secret(root)

                def invoker(
                    model: str, _effort: str, prompt: str, failure: str = failure
                ) -> str:
                    if "You are the Planner" in prompt:
                        return "Proposed plan"
                    if failure == "worker_error" and "Codex" in model:
                        raise RuntimeError("critic unavailable")
                    if failure == "malformed" and "Codex" in model:
                        return "response without a verdict"
                    return self._review_response("Proposed plan")

                result = debate_orchestrator.run_advisory_consultation_debate(
                    "Review architecture",
                    invoker,
                    root_dir=root,
                    occasion="plan-review",
                    complexity="complex",
                )

                expected_outcome = (
                    "worker_error" if failure == "worker_error" else "unparseable_verdict"
                )
                self.assertEqual(result.outcome, expected_outcome)
                assert result.manifest_path is not None
                self._assert_valid_manifest(result.manifest_path, manifest_status)

    def test_canary_security_finding_runs_veto_handler(self) -> None:
        fixture = debate_orchestrator.CANARY_FIXTURES[0]
        finding = {"id": "SEC-CANARY", "severity": "high", "confidence": 0.95}

        def invoker(_model: str, _effort: str, _prompt: str) -> str:
            return self._review_response(
                fixture.plan_text,
                vote="block",
                verdict="REVISE",
                findings=[finding],
            )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan_path = root / "implementation_plan.md"
            plan_path.write_text("current plan", encoding="utf-8")
            result = debate_orchestrator.run_advisory_consultation_debate(
                "Run canary",
                invoker,
                root_dir=root,
                is_canary=True,
                canary_fixture=fixture,
            )

            self.assertEqual(result.outcome, "security_halt")
            assert result.security_veto is not None
            self.assertEqual(result.security_veto.finding["id"], "SEC-CANARY")
            self.assertEqual(plan_path.read_text(encoding="utf-8"), "current plan")

    def test_normal_dyad_outcomes_do_not_write_manifests(self) -> None:
        for verdict, expected_outcome in (("APPROVE", "consensus"), ("REVISE", "stalemate")):
            with self.subTest(outcome=expected_outcome), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)

                def invoker(
                    _model: str,
                    _effort: str,
                    prompt: str,
                    verdict: str = verdict,
                ) -> str:
                    if "You are the Planner" in prompt:
                        return "Proposed plan"
                    return self._review_response(
                        "Proposed plan", vote=verdict.casefold(), verdict=verdict
                    )

                result = debate_orchestrator.run_advisory_consultation_debate(
                    "Plan safely", invoker, root_dir=root, max_rounds=1
                )
                self.assertEqual(result.outcome, expected_outcome)
                self.assertIsNone(result.manifest_path)
                self.assertEqual(list((root / ".ralph").glob("council-manifest-*.json")), [])

    def test_missing_panel_secret_fails_closed(self) -> None:
        def invoker(_model: str, _effort: str, prompt: str) -> str:
            if "You are the Planner" in prompt:
                return "Proposed plan"
            return self._review_response("Proposed plan")

        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {"AGY_CALIBRATION_SECRET": "", "COUNCIL_REVIEW_SECRET": ""},
        ):
            root = Path(tmp)
            with self.assertRaisesRegex(RuntimeError, "secret resolution failed"):
                debate_orchestrator.run_advisory_consultation_debate(
                    "Review architecture",
                    invoker,
                    root_dir=root,
                    occasion="plan-review",
                    complexity="complex",
                )
            self.assertFalse((root / "implementation_plan.md").exists())
            self.assertEqual(list((root / ".ralph").glob("council-manifest-*.json")), [])

    def test_facade_and_orchestrator_signatures_match(self) -> None:
        import inspect

        for symbol in (
            "run_advisory_consultation_debate",
            "run_debate_loop",
            "run_canary_dialogue",
            "run_post_mortem_loop",
            "dispatch_post_mortem_consultation",
        ):
            facade_fn = getattr(advisory_consultation, symbol)
            orch_fn = getattr(debate_orchestrator, symbol)
            self.assertEqual(
                inspect.signature(facade_fn),
                inspect.signature(orch_fn),
                f"Signature mismatch on {symbol}",
            )


class _FakePerspectiveAdapter:
    """Minimal stand-in for `provider_adapters.FakeReviewerAdapter`, kept
    local so this module's tests stay self-contained rather than reaching
    into `council-review/scripts` for a fixture."""

    def __init__(self, provider_id: str, fixed_responses: list[dict[str, Any]]) -> None:
        self.provider_id = provider_id
        self.fixed_responses = fixed_responses
        self.call_count = 0

    async def review(self, envelope: str, round_spec: int, deadline: int) -> dict[str, Any]:
        if self.call_count < len(self.fixed_responses):
            resp = self.fixed_responses[self.call_count]
            self.call_count += 1
            return resp
        return {"provider": self.provider_id, "vote": "abstain", "confidence": 0.0}


def _perspective_vote(perspective: str, vote: str, candidate_hash: str, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "provider": perspective,
        "perspective": perspective,
        "vote": vote,
        "candidate_hash": candidate_hash,
    }
    payload.update(extra)
    return payload


class _TamperingPerspectiveAdapter(_FakePerspectiveAdapter):
    """Like `_FakePerspectiveAdapter`, but overwrites `subject_path`'s
    on-disk content the first time `review()` is awaited — simulating a
    source file that changes underneath an in-flight review, so the
    pre-round-1/post-round-1 hash comparison must trip."""

    def __init__(
        self, provider_id: str, fixed_responses: list[dict[str, Any]], subject_path: str
    ) -> None:
        super().__init__(provider_id, fixed_responses)
        self._subject_path = subject_path
        self._tampered = False

    async def review(self, envelope: str, round_spec: int, deadline: int) -> dict[str, Any]:
        if not self._tampered:
            self._tampered = True
            with open(self._subject_path, "w", encoding="utf-8") as stream:  # noqa: ASYNC230 - tiny local test fixture write, not production I/O
                stream.write("tampered content")
        return await super().review(envelope, round_spec, deadline)


class _FakeProviderAdaptersModule:
    """Stand-in for the `provider_adapters` module `_load_provider_adapters`
    returns — exposes just the `build_adapter` seam `_route_perspective_stalemate`
    calls, so a test can hand back a fixed adjudicator adapter regardless of
    which adjudicator config is passed in."""

    def __init__(self, adapter: Any) -> None:
        self._adapter = adapter

    def build_adapter(self, config: dict[str, Any]) -> Any:
        return self._adapter


# `ReviewCouncil`'s by-perspective fast path (spec 0012 / workflow-v2 ticket
# 07, ADR 0012's 1-shot round), its unilateral security veto, and its
# stalemate escalation. `skills/council-review/tests/test_council_review.py`
# (`CouncilPerspectiveFastPathTests`) covers the same flow through the
# `council_review` facade with a wider matrix of adjudicator scenarios; this
# suite exercises `ReviewCouncil` directly, where it is actually defined.
class ReviewCouncilByPerspectiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace_root = self.temp_dir.name
        cal_dir = os.path.join(self.workspace_root, ".ralph", "cache")
        os.makedirs(cal_dir, exist_ok=True)
        with open(os.path.join(cal_dir, "calibration.key"), "w") as f:
            f.write("test_secret_key_12345")
        self.objective = "Add a caching layer"
        self.candidate_hash = hashlib.sha256(self.objective.encode("utf-8")).hexdigest()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _adapters(
        self, votes: dict[str, str], overrides: dict[str, dict[str, Any]] | None = None
    ) -> list[_FakePerspectiveAdapter]:
        overrides = overrides or {}
        return [
            _FakePerspectiveAdapter(
                perspective,
                [_perspective_vote(perspective, vote, self.candidate_hash, **overrides.get(perspective, {}))],
            )
            for perspective, vote in votes.items()
        ]

    def test_one_shot_fast_path_approves_in_round_1(self) -> None:
        council = debate_orchestrator.ReviewCouncil()
        req = debate_orchestrator.ReviewRequest(
            objective=self.objective, workspace_root=self.workspace_root, by_perspective=True
        )
        adapters = self._adapters({
            "reviewer_architecture": "approved",
            "reviewer_risk": "approved",
            "reviewer_maintainability": "approved",
            "reviewer_security": "approved",
        })

        outcome = asyncio.run(council.review(req, custom_adapters=adapters))

        self.assertIsInstance(outcome, debate_orchestrator.ReviewOutcome)
        self.assertEqual(outcome.status, "UNANIMOUS")
        # 1-shot: every perspective reviewer ran exactly once.
        self.assertEqual([a.call_count for a in adapters], [1, 1, 1, 1])

    def test_unilateral_security_veto_with_dict_finding_halts_review(self) -> None:
        council = debate_orchestrator.ReviewCouncil()
        req = debate_orchestrator.ReviewRequest(
            objective=self.objective, workspace_root=self.workspace_root, by_perspective=True
        )
        adapters = self._adapters(
            {
                "reviewer_architecture": "approved",
                "reviewer_risk": "approved",
                "reviewer_maintainability": "approved",
                "reviewer_security": "blocked",
            },
            overrides={
                "reviewer_security": {
                    "findings": [{"id": "SEC-1", "severity": "critical", "claim": "SQLi", "confidence": 0.95}]
                }
            },
        )

        outcome = asyncio.run(council.review(req, custom_adapters=adapters))

        self.assertEqual(outcome.status, "SECURITY_HALT")
        self.assertEqual(outcome.unresolved_blockers, 1)

    def test_round_1_quorum_failure_escalates_to_stalemate(self) -> None:
        council = debate_orchestrator.ReviewCouncil()
        req = debate_orchestrator.ReviewRequest(
            objective=self.objective, workspace_root=self.workspace_root, by_perspective=True
        )
        adapters = self._adapters({
            "reviewer_architecture": "revise",
            "reviewer_risk": "revise",
            "reviewer_maintainability": "revise",
            "reviewer_security": "revise",
        })

        outcome = asyncio.run(council.review(req, custom_adapters=adapters))

        self.assertEqual(outcome.status, "UNRESOLVED")
        self.assertEqual(outcome.unresolved_blockers, 1)
        assert outcome.stalemate_report is not None
        self.assertEqual(outcome.stalemate_report.planner_position, self.objective)
        # Escalation happens straight off round 1 — no second round is polled.
        self.assertEqual([a.call_count for a in adapters], [1, 1, 1, 1])

    def test_perspective_fast_path_tampered_source_fails_closed_to_unresolved(self) -> None:
        subject_path = os.path.join(self.workspace_root, "subject.txt")
        with open(subject_path, "w", encoding="utf-8") as stream:
            stream.write("original content")
        council = debate_orchestrator.ReviewCouncil()
        req = debate_orchestrator.ReviewRequest(
            objective=self.objective,
            workspace_root=self.workspace_root,
            subject=subject_path,
            by_perspective=True,
        )
        approvals = {
            "reviewer_architecture": "approved",
            "reviewer_risk": "approved",
            "reviewer_maintainability": "approved",
            "reviewer_security": "approved",
        }
        adapters = [
            _TamperingPerspectiveAdapter(
                perspective,
                [_perspective_vote(perspective, vote, self.candidate_hash)],
                subject_path,
            )
            for perspective, vote in approvals.items()
        ]

        outcome = asyncio.run(council.review(req, custom_adapters=adapters))

        self.assertEqual(outcome.status, "UNRESOLVED")
        self.assertTrue(outcome.source_changed)
        self.assertEqual(outcome.unresolved_blockers, 1)

    def test_perspective_adjudicator_tampered_source_fails_closed_to_unresolved(self) -> None:
        subject_path = os.path.join(self.workspace_root, "subject.txt")
        with open(subject_path, "w", encoding="utf-8") as stream:
            stream.write("original content")
        council = debate_orchestrator.ReviewCouncil()
        req = debate_orchestrator.ReviewRequest(
            objective=self.objective,
            workspace_root=self.workspace_root,
            subject=subject_path,
            by_perspective=True,
        )
        revises = {
            "reviewer_architecture": "revise",
            "reviewer_risk": "revise",
            "reviewer_maintainability": "revise",
            "reviewer_security": "revise",
        }
        adapters = [
            _TamperingPerspectiveAdapter(
                perspective,
                [_perspective_vote(perspective, vote, self.candidate_hash)],
                subject_path,
            )
            for perspective, vote in revises.items()
        ]
        approving_adjudicator = _FakePerspectiveAdapter(
            "lm-studio", [{"provider": "lm-studio", "vote": "approve", "confidence": 1.0}]
        )

        with patch.object(
            debate_orchestrator,
            "_load_provider_adapters",
            return_value=_FakeProviderAdaptersModule(approving_adjudicator),
        ):
            outcome = asyncio.run(council.review(req, custom_adapters=adapters))

        self.assertEqual(outcome.status, "UNRESOLVED")
        self.assertTrue(outcome.source_changed)
        self.assertEqual(outcome.unresolved_blockers, 1)

    def test_fast_path_disabled_escalates_to_adjudicator_despite_quorum(self) -> None:
        base = json.loads(debate_orchestrator.ROUTING_CONFIG_PATH.read_text())
        base.setdefault("consultation_policy", {})["council_policy"] = {"fast_path_enabled": False}
        with tempfile.TemporaryDirectory() as config_dir:
            config_path = Path(config_dir) / "routing-config.json"
            config_path.write_text(json.dumps(base), encoding="utf-8")
            council = debate_orchestrator.ReviewCouncil(config_path)

        req = debate_orchestrator.ReviewRequest(
            objective=self.objective, workspace_root=self.workspace_root, by_perspective=True
        )
        adapters = self._adapters({
            "reviewer_architecture": "approved",
            "reviewer_risk": "approved",
            "reviewer_maintainability": "approved",
            "reviewer_security": "approved",
        })

        outcome = asyncio.run(council.review(req, custom_adapters=adapters))

        # Round 1 had quorum, but with fast_path disabled the panel must
        # still escalate to the Adjudicator/HITL route rather than
        # finalizing directly off round 1 — the stub lm-studio adjudicator
        # abstains, so this fails closed to UNRESOLVED with a stalemate
        # report attached (only the stalemate route ever populates
        # `stalemate_report`; a direct fast-path finalize never does).
        self.assertEqual(outcome.status, "UNRESOLVED")
        assert outcome.stalemate_report is not None
        self.assertEqual([a.call_count for a in adapters], [1, 1, 1, 1])


if __name__ == "__main__":
    unittest.main()
