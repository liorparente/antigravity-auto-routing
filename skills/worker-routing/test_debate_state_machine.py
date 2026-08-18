"""Offline tests for the pure debate-state reducer."""
from __future__ import annotations

import importlib.util
import sys
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(f"{name}.py"))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


machine = _load("debate_state_machine")


class DebateStateMachineTests(unittest.TestCase):
    def test_panel_topology_and_reports(self) -> None:
        self.assertTrue(machine.is_panel_topology("plan-review", " Complex "))
        self.assertTrue(machine.is_panel_topology("code-review", "complex"))
        self.assertFalse(machine.is_panel_topology("ambiguity", "complex"))
        self.assertFalse(machine.is_panel_topology("plan-review", "medium"))
        pair = machine.build_stalemate_report("planner", "critic")
        panel = machine.build_stalemate_report("planner", "critic a", "critic b")
        self.assertEqual(pair.options[1].label, "Approve Critic Architecture")
        self.assertIsNone(pair.critic_b_position)
        self.assertEqual(panel.critic_b_position, "critic b")
        self.assertEqual(panel.options[1].description, "Critic A:\ncritic a\n\nCritic B:\ncritic b")

    def test_pair_and_panel_verdicts_fail_closed(self) -> None:
        self.assertEqual(machine.evaluate_round_verdicts(" approved "), (True, None))
        self.assertEqual(machine.evaluate_round_verdicts("ReViSe"), (False, None))
        self.assertEqual(machine.evaluate_round_verdicts("yes"), (False, "unparseable verdict: yes"))
        self.assertEqual(machine.evaluate_round_verdicts("APPROVE", "approve", is_panel=True), (True, None))
        self.assertEqual(machine.evaluate_round_verdicts("APPROVE", "revise", is_panel=True), (False, None))
        self.assertEqual(machine.evaluate_round_verdicts("APPROVE", None, is_panel=True), (False, "unparseable verdict: critic_a=APPROVE, critic_b=None"))

    def test_quorum_policies_and_malformed_votes(self) -> None:
        votes = tuple(machine.CriticResponse(str(index), "", verdict) for index, verdict in enumerate(("approve", "approve", "revise")))
        self.assertEqual(machine.evaluate_quorum(votes, "unanimous"), (False, None))
        self.assertEqual(machine.evaluate_quorum(votes, "majority"), (True, None))
        self.assertEqual(machine.evaluate_quorum(votes, "qualified"), (True, None))
        self.assertEqual(machine.evaluate_quorum(votes[:2] + (machine.CriticResponse("bad", "", "maybe"),)), (False, "unparseable verdict: bad=maybe"))

    def test_session_transitions_and_terminal_preservation(self) -> None:
        initial = machine.DebateSessionState("ambiguity", "medium", 3, False)
        approved = machine.advance_debate_state(initial, machine.DebateRoundRecord(1, "plan one", "ok", critic_a_verdict="APPROVE"))
        self.assertTrue(approved.consensus_reached)
        self.assertEqual(approved.final_plan, "plan one")
        revised = machine.advance_debate_state(initial, machine.DebateRoundRecord(1, "plan one", "revise", critic_a_verdict="REVISE"))
        second = machine.advance_debate_state(revised, machine.DebateRoundRecord(2, "plan two", "ok", critic_a_verdict="APPROVED"))
        self.assertTrue(second.consensus_reached)
        third = machine.advance_debate_state(machine.advance_debate_state(revised, machine.DebateRoundRecord(2, "plan two", "revise", critic_a_verdict="REVISE")), machine.DebateRoundRecord(3, "plan three", "revise", critic_a_verdict="REVISE"))
        self.assertIsNotNone(third.stalemate_report)
        self.assertIs(machine.advance_debate_state(approved, machine.DebateRoundRecord(2, "ignored", "", critic_a_verdict="APPROVE")), approved)
        with self.assertRaises(FrozenInstanceError):
            initial.rounds = ()

    def test_general_reducer_uses_quorum_and_preserves_terminal_state(self) -> None:
        state = machine.DebateState("plan-review", "task", "task-1", 0, 3, (), (), "in_progress")
        revise = machine.RoundTurnResult(1, "plan one", (machine.CriticResponse("a", "revise", "REVISE"),))
        pending = machine.advance_debate_state(state, revise)
        accepted = machine.advance_debate_state(pending, machine.RoundTurnResult(2, "plan two", (machine.CriticResponse("a", "ok", "approve"),)))
        self.assertEqual(pending.status, "in_progress")
        self.assertEqual(accepted.status, "consensus")
        self.assertEqual(accepted.final_plan, "plan two")
        self.assertIs(machine.advance_debate_state(accepted, revise), accepted)

    def test_general_reducer_escalates_after_third_round(self) -> None:
        state = machine.DebateState("code-review", "task", "task-1", 0, 3, (), (), "in_progress")
        for index in range(1, 4):
            state = machine.advance_debate_state(state, machine.RoundTurnResult(index, f"plan {index}", (machine.CriticResponse("a", "revise", "revise"), machine.CriticResponse("b", "revise", "revise"))))
        self.assertEqual(state.status, "stalemate")
        self.assertEqual(state.stalemate_report.critic_b_position, "revise")


if __name__ == "__main__":
    unittest.main()
