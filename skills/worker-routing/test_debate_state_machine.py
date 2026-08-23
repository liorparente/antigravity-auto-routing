"""Offline tests for the pure debate-state reducer."""
from __future__ import annotations

import math
import sys
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parent))

if __package__:
    from . import debate_state_machine as machine
    from . import dialogue_contracts
else:
    import debate_state_machine as machine  # type: ignore[no-redef]
    import dialogue_contracts  # type: ignore[no-redef]


class DebateStateMachineTests(unittest.TestCase):
    def test_security_veto_supports_dicts_and_critic_responses(self) -> None:
        handler = machine.SecurityVetoHandler()
        dict_veto = handler.check(({
            "provider": "codex",
            "findings": [{"id": "SEC-1", "severity": "CRITICAL", "confidence": 0.9}],
        },))
        assert dict_veto is not None
        response_veto = handler.check((machine.CriticResponse(
            "critic-a",
            "",
            findings=({"id": "SEC-2", "severity": "High", "confidence": 0.8},),
        ),))
        assert response_veto is not None

        self.assertEqual((dict_veto.provider, dict_veto.finding["id"]), ("codex", "SEC-1"))
        self.assertEqual(
            (response_veto.provider, response_veto.finding["id"]),
            ("critic-a", "SEC-2"),
        )

    def test_security_veto_confidence_validation_fails_closed(self) -> None:
        handler = machine.SecurityVetoHandler(["high"], 0.8)
        for confidence in (True, False, math.nan, math.inf, -math.inf, -0.1, 1.1, "bad"):
            with self.subTest(confidence=confidence):
                veto = handler.check(({
                    "provider": "critic",
                    "findings": [{"severity": "HIGH", "confidence": confidence}],
                },))
                self.assertIsNotNone(veto)

    def test_invalid_security_threshold_uses_fail_closed_default(self) -> None:
        for threshold in (
            True,
            False,
            None,
            "0.9",
            math.nan,
            math.inf,
            -math.inf,
            -0.1,
            1.1,
        ):
            with self.subTest(threshold=threshold):
                handler = machine.SecurityVetoHandler(
                    ["high"], security_threshold=threshold  # type: ignore[arg-type]
                )
                self.assertEqual(handler.security_threshold, 0.80)
                self.assertIsNotNone(handler.check(({
                    "provider": "critic",
                    "findings": [{"severity": "high", "confidence": 0.80}],
                },)))

    def test_invalid_veto_severities_use_fail_closed_defaults(self) -> None:
        for severities in (None, "high", [], [""], ["high", 1]):
            with self.subTest(severities=severities):
                handler = machine.SecurityVetoHandler(veto_severities=severities)  # type: ignore[arg-type]
                self.assertEqual(handler.veto_severities, {"critical", "high"})

    def test_security_veto_can_be_disabled_and_uses_default_confidence(self) -> None:
        vote = {"provider": "critic", "findings": [{"severity": "high"}]}
        self.assertIsNotNone(machine.SecurityVetoHandler().check((vote,)))
        self.assertIsNone(machine.SecurityVetoHandler(enabled=False).check((vote,)))

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
        assert isinstance(approved, machine.DebateSessionState)
        self.assertTrue(approved.consensus_reached)
        self.assertEqual(approved.final_plan, "plan one")
        revised = machine.advance_debate_state(initial, machine.DebateRoundRecord(1, "plan one", "revise", critic_a_verdict="REVISE"))
        assert isinstance(revised, machine.DebateSessionState)
        second = machine.advance_debate_state(revised, machine.DebateRoundRecord(2, "plan two", "ok", critic_a_verdict="APPROVED"))
        assert isinstance(second, machine.DebateSessionState)
        self.assertTrue(second.consensus_reached)
        third = machine.advance_debate_state(machine.advance_debate_state(revised, machine.DebateRoundRecord(2, "plan two", "revise", critic_a_verdict="REVISE")), machine.DebateRoundRecord(3, "plan three", "revise", critic_a_verdict="REVISE"))
        assert isinstance(third, machine.DebateSessionState)
        self.assertIsNotNone(third.stalemate_report)
        self.assertIs(machine.advance_debate_state(approved, machine.DebateRoundRecord(2, "ignored", "", critic_a_verdict="APPROVE")), approved)
        with self.assertRaises(FrozenInstanceError):
            initial.rounds = ()  # type: ignore[misc]

    def test_general_reducer_uses_quorum_and_preserves_terminal_state(self) -> None:
        state = machine.DebateState("plan-review", "task", "task-1", 0, 3, (), (), "in_progress")
        revise = machine.RoundTurnResult(1, "plan one", (machine.CriticResponse("a", "revise", "REVISE"),))
        pending = machine.advance_debate_state(state, revise)
        assert isinstance(pending, machine.DebateState)
        accepted = machine.advance_debate_state(pending, machine.RoundTurnResult(2, "plan two", (machine.CriticResponse("a", "ok", "approve"),)))
        assert isinstance(accepted, machine.DebateState)
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
            state = machine.advance_debate_state(
                state,
                machine.RoundTurnResult(
                    index,
                    f"plan {index}",
                    (
                        machine.CriticResponse("a", "revise", "revise"),
                        machine.CriticResponse("b", "revise", "revise"),
                    ),
                ),
            )
        assert isinstance(state, machine.DebateState)
        self.assertEqual(state.status, "stalemate")
        assert state.stalemate_report is not None
        self.assertEqual(state.stalemate_report.critic_b_position, "revise")

    def test_consensus_table_scores_only_positive_approvals(self) -> None:
        table = machine.ConsensusTable(weights={"a": 3.0, "b": 1.0})
        votes = (
            {"provider": "a", "vote": "approve", "confidence": 2},
            {"provider": "b", "vote": "revise", "confidence": -2},
        )
        self.assertEqual(table.weighted_score(votes), 3.0 / 4.0)
        self.assertEqual(table.weighted_score((
            {"provider": "a", "vote": "unanimous", "confidence": 1.0},
            {"provider": "b", "vote": "block", "confidence": 1.0},
        )), 3.0 / 4.0)
        self.assertEqual(table.weighted_score((
            {"provider": "a", "vote": "approve", "confidence": -1.0},
        )), 0.0)
        self.assertEqual(machine.ConsensusTable()._confidence({"vote": "revise"}), -0.3)
        self.assertEqual(machine.ConsensusTable()._confidence({"vote": "block"}), -1.0)
        self.assertEqual(machine.ConsensusTable()._confidence({"vote": "abstain"}), 0.0)
        self.assertEqual(machine.ConsensusTable()._confidence({"vote": "unknown"}), 0.0)

    def test_response_default_confidence_uses_its_verdict_and_rejects_nan(self) -> None:
        response = machine.CriticResponse("a", "", "revise")
        table = machine.ConsensusTable()
        self.assertIsNone(response.confidence)
        self.assertEqual(table._confidence(response), -0.3)
        self.assertEqual(table._confidence({"provider": "a", "vote": "approve", "confidence": math.nan}), 0.0)

    def test_consensus_table_discards_nonfinite_weights_and_invalid_thresholds(self) -> None:
        table = machine.ConsensusTable(
            weights={"valid": 1, "negative": -1, "infinite": math.inf, "nan": math.nan},
            quorum_threshold=math.inf,
        )
        self.assertEqual(dict(table.weights), {"valid": 1.0})
        self.assertEqual(table.quorum_threshold, 0.60)
        self.assertEqual(machine.ConsensusTable(quorum_threshold=-0.1).quorum_threshold, 0.60)
        self.assertEqual(machine.ConsensusTable(quorum_threshold=math.nan).quorum_threshold, 0.60)
        self.assertEqual(machine.ConsensusTable(quorum_threshold=False).quorum_threshold, 0.60)
        self.assertEqual(dict(machine.ConsensusTable(weights={"a": True}).weights), {})

    def test_candidate_hash_ratification_requires_every_voter_to_match(self) -> None:
        table = machine.ConsensusTable()
        votes = (
            {"provider": "a", "vote": "approve", "candidate_hash": "candidate"},
            {"provider": "b", "vote": "approve"},
        )
        self.assertEqual(table.evaluate(votes, expected_hash="candidate"), "MATERIAL_DISAGREEMENT")
        self.assertEqual(table.evaluate(votes), "MATERIAL_DISAGREEMENT")
        matching = tuple(
            {"provider": provider, "vote": "approve", "candidate_hash": "candidate"}
            for provider in ("a", "b")
        )
        self.assertEqual(table.evaluate(matching, expected_hash="candidate"), "UNANIMOUS")

    def test_candidate_hash_requirement_rejects_hashless_votes(self) -> None:
        votes = (
            {"provider": "a", "vote": "approve"},
            {"provider": "b", "vote": "approve"},
        )
        self.assertEqual(
            machine.ConsensusTable().evaluate(votes, require_candidate_hashes=True),
            "MATERIAL_DISAGREEMENT",
        )
        responses = tuple(machine.CriticResponse(vote["provider"], "", "approve") for vote in votes)
        result = machine.evaluate_weighted_quorum(responses, require_candidate_hashes=True)
        self.assertEqual(result[1], "MATERIAL_DISAGREEMENT")
        self.assertEqual(result[3], "material disagreement in candidate hashes")

    def test_critic_response_findings_are_defensively_immutable(self) -> None:
        finding = {"severity": "high"}
        response = machine.CriticResponse("critic", "", findings=(finding,))
        finding["severity"] = "low"
        self.assertEqual(response.findings[0]["severity"], "high")
        with self.assertRaises(TypeError):
            response.findings[0]["severity"] = "critical"  # type: ignore[index]

    def test_critic_response_findings_are_deeply_immutable(self) -> None:
        response = machine.CriticResponse("critic", "", findings=({"nested": {"key": "good"}},))
        with self.assertRaises(TypeError):
            response.findings[0]["nested"]["key"] = "bad"  # type: ignore[index]

    def test_consensus_table_evaluates_dicts_and_responses(self) -> None:
        table = machine.ConsensusTable(policy=("UNANIMOUS", "QUALIFIED", "MATERIAL_DISAGREEMENT", "INCOMPLETE", "UNRESOLVED"), quorum_threshold=0.50)
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

    def test_unparseable_weighted_verdict_fails_closed(self) -> None:
        votes = (machine.CriticResponse("a", "", "maybe", confidence=1.0),)
        table = machine.ConsensusTable()
        self.assertEqual(table.invalid_voters(votes), ("a=maybe",))
        self.assertEqual(table.evaluate(votes), "INCOMPLETE")
        self.assertEqual(
            machine.evaluate_weighted_quorum(votes),
            (False, "INCOMPLETE", 0.0, "unparseable verdict: a=maybe"),
        )

    def test_consensus_policy_rejects_unknown_and_disallowed_outcomes(self) -> None:
        approval = ({"provider": "a", "vote": "approve"},)
        self.assertEqual(machine.ConsensusTable(policy=("UNKNOWN",)).evaluate(approval), "INCOMPLETE")
        self.assertEqual(machine.ConsensusTable(policy=("QUALIFIED",)).evaluate(approval), "INCOMPLETE")

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

    def test_below_weighted_quorum_is_unresolved_and_can_continue(self) -> None:
        votes = (
            machine.CriticResponse("a", "", "approve", confidence=0.5),
            machine.CriticResponse("b", "", "revise", confidence=0.0),
        )
        self.assertEqual(
            machine.evaluate_weighted_quorum(votes, quorum_threshold=0.60),
            (False, "UNRESOLVED", 0.25, None),
        )
        state = machine.DebateState("plan-review", "task", "task-1", 0, 2, (), (), "in_progress")
        pending = machine.advance_debate_state(
            state, machine.RoundTurnResult(1, "plan one", votes), "weighted", quorum_threshold=0.60
        )
        self.assertEqual(pending.status, "in_progress")
        stalemate = machine.advance_debate_state(
            pending, machine.RoundTurnResult(2, "plan two", votes), "weighted", quorum_threshold=0.60
        )
        self.assertEqual(stalemate.status, "stalemate")

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
        assert isinstance(consensus, machine.DebateState)
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
        assert isinstance(error, machine.DebateState)
        self.assertEqual(error.status, "error")
        self.assertEqual(error.error, "material disagreement in candidate hashes")

        pending = machine.advance_debate_state(
            state,
            machine.RoundTurnResult(1, "plan one", (machine.CriticResponse("a", "abstain", "abstain", confidence=0.0),)),
            "weighted",
        )
        assert isinstance(pending, machine.DebateState)
        stalemate = machine.advance_debate_state(
            pending,
            machine.RoundTurnResult(2, "plan two", (machine.CriticResponse("a", "abstain", "abstain", confidence=0.0),)),
            "weighted",
        )
        assert isinstance(stalemate, machine.DebateState)
        self.assertEqual(stalemate.status, "stalemate")
        self.assertIsNotNone(stalemate.stalemate_report)

    def test_weighted_state_requires_candidate_hashes_when_requested(self) -> None:
        state = machine.DebateState("plan-review", "task", "task-1", 0, 2, (), (), "in_progress")
        hashless_approvals = (
            machine.CriticResponse("a", "ok", "approve"),
            machine.CriticResponse("b", "ok", "approve"),
        )
        error = machine.advance_debate_state(
            state,
            machine.RoundTurnResult(1, "plan", hashless_approvals),
            "weighted",
            require_candidate_hashes=True,
        )
        assert isinstance(error, machine.DebateState)
        self.assertEqual(error.status, "error")
        self.assertEqual(error.error, "material disagreement in candidate hashes")

    def test_weighted_state_accepts_matching_expected_candidate_hash(self) -> None:
        state = machine.DebateState("plan-review", "task", "task-1", 0, 2, (), (), "in_progress")
        matching_approvals = (
            machine.CriticResponse("a", "ok", "approve", candidate_hash="candidate"),
            machine.CriticResponse("b", "ok", "approve", candidate_hash="candidate"),
        )
        consensus = machine.advance_debate_state(
            state,
            machine.RoundTurnResult(1, "plan", matching_approvals),
            "weighted",
            require_candidate_hashes=True,
            expected_hash="candidate",
        )
        assert isinstance(consensus, machine.DebateState)
        self.assertEqual(consensus.status, "consensus")

    # --- Spec 0012 / workflow-v2 ticket 07: Council fast path & security veto ---

    def test_normalize_verdict_maps_blocked_to_block(self) -> None:
        self.assertEqual(machine._normalize_verdict("blocked"), "BLOCK")
        self.assertEqual(machine._normalize_verdict("Blocked"), "BLOCK")
        self.assertEqual(machine.DEFAULT_VOTE_CONFIDENCE["blocked"], -1.0)

    def test_security_veto_detects_structured_finding_instances(self) -> None:
        handler = machine.SecurityVetoHandler()
        finding = dialogue_contracts.StructuredFinding(
            id="SEC-9", severity="critical", claim="hardcoded credential", confidence=0.95
        )
        veto = handler.check(({"provider": "reviewer_security", "findings": (finding,)},))
        assert veto is not None
        self.assertEqual((veto.provider, veto.finding["id"]), ("reviewer_security", "SEC-9"))
        self.assertEqual(veto.finding["claim"], "hardcoded credential")

    def test_security_veto_detects_unilateral_block_verdict_without_findings(self) -> None:
        handler = machine.SecurityVetoHandler()
        veto = handler.check(({"perspective": "reviewer_security", "vote": "blocked"},))
        assert veto is not None
        self.assertEqual(veto.provider, "reviewer_security")
        self.assertEqual(veto.finding["severity"], "critical")

    def test_security_veto_block_verdict_uses_first_structured_finding(self) -> None:
        handler = machine.SecurityVetoHandler()
        finding = dialogue_contracts.StructuredFinding(
            id="SEC-2", severity="low", claim="unsafe temp file", confidence=1.0
        )
        # A BLOCK verdict is a unilateral veto regardless of the attached
        # finding's own severity/confidence — those gate the *other*,
        # severity-threshold trigger, not this one.
        veto = handler.check((
            {"perspective": "reviewer_security", "vote": "blocked", "findings": (finding,)},
        ))
        assert veto is not None
        self.assertEqual(veto.finding["id"], "SEC-2")

    def test_security_veto_ignores_block_verdict_when_disabled(self) -> None:
        handler = machine.SecurityVetoHandler(enabled=False)
        self.assertIsNone(handler.check(({"perspective": "reviewer_security", "vote": "blocked"},)))

    def test_legacy_block_vote_without_a_security_finding_does_not_veto(self) -> None:
        """A non-security provider voting "block" (plain disapproval, e.g. an
        architecture Critic rejecting a plan) must not be read as a
        unilateral security veto just because the word matches — only
        `reviewer_security`'s own block, or a genuine attached critical/high
        finding, is a security signal."""
        handler = machine.SecurityVetoHandler()
        self.assertIsNone(handler.check(({"provider": "critic_a", "vote": "block"},)))
        self.assertIsNone(handler.check(({"provider": "planner", "verdict": "blocked"},)))
        self.assertIsNone(
            handler.check(({"perspective": "reviewer_architecture", "vote": "block"},))
        )

    def test_legacy_block_vote_with_a_genuine_high_severity_structured_finding_still_vetoes(self) -> None:
        """The other half: a non-security provider's block is still a
        genuine security signal if it carries its own critical/high
        `StructuredFinding` — the vote's own severity, not a fabricated
        default, is what's reported."""
        handler = machine.SecurityVetoHandler()
        finding = dialogue_contracts.StructuredFinding(
            id="SEC-7", severity="high", claim="unsafe deserialization", confidence=0.5
        )
        veto = handler.check((
            {"provider": "reviewer_architecture", "vote": "block", "findings": (finding,)},
        ))
        assert veto is not None
        self.assertEqual(veto.provider, "reviewer_architecture")
        self.assertEqual(veto.finding["id"], "SEC-7")
        self.assertEqual(veto.finding["severity"], "high")

    def test_consensus_table_identity_prefers_perspective_over_provider(self) -> None:
        table = machine.ConsensusTable(
            weights={"reviewer_security": 0.25, "codex": 0.40},
        )
        self.assertEqual(
            table._identity({"perspective": "reviewer_security", "provider": "codex"}),
            "reviewer_security",
        )
        self.assertEqual(table._identity({"provider": "codex"}), "codex")

    def test_consensus_table_evaluates_perspective_review_results(self) -> None:
        table = machine.ConsensusTable(
            weights={
                "reviewer_architecture": 0.30,
                "reviewer_risk": 0.25,
                "reviewer_maintainability": 0.20,
                "reviewer_security": 0.25,
            },
            quorum_threshold=0.60,
        )
        votes = (
            dialogue_contracts.PerspectiveReviewResult(
                "approved", 1, 0, perspective="reviewer_architecture"
            ),
            dialogue_contracts.PerspectiveReviewResult(
                "approved", 1, 0, perspective="reviewer_risk"
            ),
            dialogue_contracts.PerspectiveReviewResult(
                "approved", 1, 0, perspective="reviewer_maintainability"
            ),
            dialogue_contracts.PerspectiveReviewResult(
                "approved", 1, 0, perspective="reviewer_security"
            ),
        )
        self.assertEqual(table.evaluate(votes), "UNANIMOUS")
        self.assertAlmostEqual(table.weighted_score(votes), 1.0)

    def test_consensus_table_evaluates_perspective_vote_dicts_below_quorum(self) -> None:
        table = machine.ConsensusTable(
            weights={
                "reviewer_architecture": 0.30,
                "reviewer_risk": 0.25,
                "reviewer_maintainability": 0.20,
                "reviewer_security": 0.25,
            },
            quorum_threshold=0.60,
        )
        votes = (
            {"perspective": "reviewer_architecture", "vote": "approved", "confidence": 1.0},
            {"perspective": "reviewer_risk", "vote": "revise", "confidence": 0.0},
            {"perspective": "reviewer_maintainability", "vote": "approved", "confidence": 1.0},
            {"perspective": "reviewer_security", "vote": "revise", "confidence": 0.0},
        )
        self.assertEqual(table.evaluate(votes), "UNRESOLVED")

    def test_build_stalemate_report_formats_perspective_positions(self) -> None:
        report = machine.build_stalemate_report(
            "Add a caching layer",
            perspective_positions={
                "reviewer_architecture": "verdict: approved",
                "reviewer_security": "verdict: revise\nfinding: missing rate limiting",
            },
        )
        self.assertEqual(report.planner_position, "Add a caching layer")
        self.assertIn("reviewer_architecture:\nverdict: approved", report.critic_position)
        self.assertIn("reviewer_security:\nverdict: revise", report.critic_position)
        self.assertEqual(report.options[1].label, "Approve Council Perspectives")

    def test_build_stalemate_report_requires_critic_position_without_perspectives(self) -> None:
        with self.assertRaises(ValueError):
            machine.build_stalemate_report("planner")


if __name__ == "__main__":
    unittest.main()
