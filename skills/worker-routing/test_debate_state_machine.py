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

    def test_three_critic_panel_quorum_with_abstentions(self) -> None:
        """A 3-critic panel split under each quorum policy, including abstentions.

        An "abstain" verdict must normalize like any other valid vote (never
        joining the `invalid`/"unparseable verdict" set) while never counting
        toward `approvals` either -- a valid non-approval vote, distinct from
        both an approval and a malformed response.
        """
        two_approve_one_abstain = (
            machine.CriticResponse("a", "", "approve"),
            machine.CriticResponse("b", "", "APPROVED"),
            machine.CriticResponse("c", "", " Abstain "),
        )
        self.assertEqual(machine.evaluate_quorum(two_approve_one_abstain, "unanimous"), (False, None))
        self.assertEqual(machine.evaluate_quorum(two_approve_one_abstain, "majority"), (True, None))
        self.assertEqual(machine.evaluate_quorum(two_approve_one_abstain, "qualified"), (True, None))

        one_approve_one_abstain_one_revise = (
            machine.CriticResponse("a", "", "approve"),
            machine.CriticResponse("b", "", "abstain"),
            machine.CriticResponse("c", "", "revise"),
        )
        self.assertEqual(machine.evaluate_quorum(one_approve_one_abstain_one_revise, "unanimous"), (False, None))
        self.assertEqual(machine.evaluate_quorum(one_approve_one_abstain_one_revise, "majority"), (False, None))
        self.assertEqual(machine.evaluate_quorum(one_approve_one_abstain_one_revise, "qualified"), (False, None))

        all_abstain = tuple(machine.CriticResponse(str(index), "", "abstain") for index in range(3))
        self.assertEqual(machine.evaluate_quorum(all_abstain, "unanimous"), (False, None))
        self.assertEqual(machine.evaluate_quorum(all_abstain, "majority"), (False, None))
        self.assertEqual(machine.evaluate_quorum(all_abstain, "qualified"), (False, None))

        # A genuinely malformed vote alongside an abstention still fails
        # closed with the unparseable-verdict error -- abstention is not a
        # blanket excuse that swallows other critics' unreadable votes.
        abstain_and_malformed = (
            machine.CriticResponse("a", "", "abstain"),
            machine.CriticResponse("b", "", "approve"),
            machine.CriticResponse("c", "", "maybe"),
        )
        self.assertEqual(
            machine.evaluate_quorum(abstain_and_malformed, "unanimous"),
            (False, "unparseable verdict: c=maybe"),
        )

    def test_general_reducer_escalates_after_third_round(self) -> None:
        state = machine.DebateState("code-review", "task", "task-1", 0, 3, (), (), "in_progress")
        for index in range(1, 4):
            state = machine.advance_debate_state(state, machine.RoundTurnResult(index, f"plan {index}", (machine.CriticResponse("a", "revise", "revise"), machine.CriticResponse("b", "revise", "revise"))))
        self.assertEqual(state.status, "stalemate")
        self.assertEqual(state.stalemate_report.critic_b_position, "revise")

    def test_consensus_table_scores_weights_defaults_and_negative_loss(self) -> None:
        table = machine.ConsensusTable(weights={"a": 3.0, "b": 1.0})
        votes = (
            {"provider": "a", "vote": "approve", "confidence": 2},
            {"provider": "b", "vote": "revise", "confidence": -2},
        )
        # Confidence clamps to +/-1, and negative confidence carries a 1.5x loss.
        self.assertEqual(table.weighted_score(votes), (3.0 - 1.5) / 4.0)
        self.assertEqual(machine.ConsensusTable()._confidence({"vote": "revise"}), -0.3)
        self.assertEqual(machine.ConsensusTable()._confidence({"vote": "block"}), -1.0)
        self.assertEqual(machine.ConsensusTable()._confidence({"vote": "abstain"}), 0.0)
        self.assertEqual(machine.ConsensusTable()._confidence({"vote": "unknown"}), 0.0)

    def test_consensus_table_evaluates_dicts_and_responses(self) -> None:
        table = machine.ConsensusTable(policy=("a", "b"), quorum_threshold=0.50)
        self.assertEqual(table.evaluate((
            {"provider": "a", "vote": "approve", "candidate_hash": "one"},
            {"provider": "b", "vote": "approve", "candidate_hash": "one"},
        )), "UNANIMOUS")
        self.assertEqual(table.evaluate((
            machine.CriticResponse("a", "", "approve", candidate_hash="one"),
            machine.CriticResponse("b", "", "revise", confidence=0.0, candidate_hash="one"),
        )), "QUALIFIED")
        self.assertEqual(table.evaluate((
            {"provider": "a", "vote": "approve", "candidate_hash": "one"},
            {"provider": "b", "vote": "approve", "candidate_hash": "two"},
        )), "MATERIAL_DISAGREEMENT")
        self.assertEqual(table.evaluate(({"vote": "approve"},)), "INCOMPLETE")
        self.assertEqual(machine.ConsensusTable().evaluate((
            machine.CriticResponse("a", "", "abstain", confidence=0.0),
        )), "UNRESOLVED")

    def test_weighted_quorum_helper(self) -> None:
        approvals = (
            machine.CriticResponse("a", "", "approve", candidate_hash="one"),
            machine.CriticResponse("b", "", "revise", confidence=0.0, candidate_hash="one"),
        )
        self.assertEqual(
            machine.evaluate_weighted_quorum(approvals, {"a": 3, "b": 1}, 0.70),
            (True, "QUALIFIED", 0.75, None),
        )
        disagreement = (
            machine.CriticResponse("a", "", "approve", candidate_hash="one"),
            machine.CriticResponse("b", "", "approve", candidate_hash="two"),
        )
        result = machine.evaluate_weighted_quorum(disagreement)
        self.assertFalse(result[0])
        self.assertEqual(result[1], "MATERIAL_DISAGREEMENT")
        self.assertEqual(result[3], "material disagreement in candidate hashes")

    def test_weighted_state_transitions(self) -> None:
        state = machine.DebateState("plan-review", "task", "task-1", 0, 2, (), (), "in_progress")
        consensus = machine.advance_debate_state(
            state,
            machine.RoundTurnResult(1, "plan", (
                machine.CriticResponse("a", "ok", "approve", candidate_hash="one"),
                machine.CriticResponse("b", "minor", "revise", confidence=0.0, candidate_hash="one"),
            )),
            "weighted", weights={"a": 3, "b": 1}, quorum_threshold=0.70,
        )
        self.assertEqual(consensus.status, "consensus")
        self.assertEqual(consensus.final_plan, "plan")

        error = machine.advance_debate_state(
            state,
            machine.RoundTurnResult(1, "plan", (
                machine.CriticResponse("a", "one", "approve", candidate_hash="one"),
                machine.CriticResponse("b", "two", "approve", candidate_hash="two"),
            )),
            "weighted",
        )
        self.assertEqual(error.status, "error")
        self.assertEqual(error.error, "material disagreement in candidate hashes")

        pending = machine.advance_debate_state(
            state,
            machine.RoundTurnResult(1, "plan one", (machine.CriticResponse("a", "abstain", "abstain", confidence=0.0),)),
            "weighted",
        )
        stalemate = machine.advance_debate_state(
            pending,
            machine.RoundTurnResult(2, "plan two", (machine.CriticResponse("a", "abstain", "abstain", confidence=0.0),)),
            "weighted",
        )
        self.assertEqual(stalemate.status, "stalemate")
        self.assertIsNotNone(stalemate.stalemate_report)


if __name__ == "__main__":
    unittest.main()
