"""Hermetic unit tests for pure debate orchestration state."""
from __future__ import annotations

import importlib.util
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from dataclasses import FrozenInstanceError
from pathlib import Path


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(f"{name}.py"))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


dialogue_contracts = _load("dialogue_contracts")
debate_orchestrator = _load("debate_orchestrator")


class PanelTopologyTests(unittest.TestCase):
    def test_only_complex_reviews_use_the_panel(self) -> None:
        for occasion in ("plan-review", "code-review"):
            self.assertTrue(debate_orchestrator.is_panel_topology(occasion, " Complex "))
        for occasion in ("ambiguity", "post-mortem"):
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
        self.assertEqual(stderr.getvalue(), result.executive_report.budget_alert)

    def test_facade_and_orchestrator_signatures_match(self) -> None:
        import inspect

        advisory_consultation = _load("advisory_consultation")

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


if __name__ == "__main__":
    unittest.main()
