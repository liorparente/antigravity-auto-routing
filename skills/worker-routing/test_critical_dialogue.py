"""Direct contract tests for the consolidated CriticalDialogue boundary."""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from typing import Any
from unittest.mock import patch

if __package__:
    from . import critical_dialogue
else:
    import critical_dialogue  # type: ignore[no-redef]


def _make_vote_payload(
    provider: str,
    vote: str,
    *,
    candidate_hash: str | None = None,
    confidence: float = 1.0,
    **extra: Any,
) -> dict[str, Any]:
    """Build a concise, configurable reviewer vote payload for council tests."""
    payload: dict[str, Any] = {"provider": provider, "vote": vote, "confidence": confidence}
    if candidate_hash is not None:
        payload["candidate_hash"] = candidate_hash
    payload.update(extra)
    return payload


class _MockReviewerAdapter:
    def __init__(self, provider_id: str, responses: list[dict[str, Any]]) -> None:
        self.provider_id = provider_id
        self._responses = responses
        self.calls = 0

    async def review(self, _envelope: str, _round_spec: int, _deadline: int) -> dict[str, Any]:
        response = self._responses[min(self.calls, len(self._responses) - 1)]
        self.calls += 1
        return response


def _mock_approve(_model: str, _effort: str, prompt: str) -> str:
    if "You are the Planner" in prompt:
        return "Proposed plan"
    return 'QUOTE: "Proposed plan"\nVERDICT: APPROVE'


class _StubCouncil:
    outcome: Any

    def __init__(self, *, policy_path: str | Path) -> None:
        self.policy_path = policy_path

    async def review(self, _request: Any) -> Any:
        return self.outcome


class CriticalDialogueTests(unittest.TestCase):
    def test_run_critical_dialogue_handles_blocking_occasions_and_approval(self) -> None:
        for occasion in ("ambiguity", "plan-review", "code-review", "post-mortem"):
            with self.subTest(occasion=occasion), tempfile.TemporaryDirectory() as tmp:
                result = critical_dialogue.run_critical_dialogue(
                    "Review the implementation", _mock_approve, root_dir=Path(tmp), occasion=occasion
                )
                self.assertEqual(result.outcome, "consensus")
                self.assertTrue(result.consensus_reached)

    def test_run_critical_dialogue_continues_rounds_then_reports_stalemate(self) -> None:
        def revise(_model: str, _effort: str, prompt: str) -> str:
            if "You are the Planner" in prompt:
                return "Proposed plan"
            return 'QUOTE: "Proposed plan"\n1. Missing validation.\nVERDICT: REVISE'

        with tempfile.TemporaryDirectory() as tmp:
            result = critical_dialogue.run_critical_dialogue(
                "Resolve ambiguity", revise, root_dir=Path(tmp), max_rounds=2
            )
        self.assertEqual(result.outcome, "stalemate")
        self.assertEqual(result.rounds_run, 2)

    def test_run_critical_dialogue_halts_on_sensitive_marker_when_unreachable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = critical_dialogue.run_critical_dialogue(
                "Rotate the BEGIN PRIVATE KEY credential",
                _mock_approve,
                root_dir=Path(tmp),
                reachability_check=lambda _family: False,
            )

        self.assertEqual(result.outcome, "sensitivity_halt")

    def test_run_canary_dialogue_returns_a_seeded_flaw_measurement(self) -> None:
        fixture = critical_dialogue.CANARY_FIXTURES[0]
        response = f'QUOTE: "{fixture.plan_text.splitlines()[0]}"\nVERDICT: REVISE\nRace condition.'
        with tempfile.TemporaryDirectory() as tmp:
            result = critical_dialogue.run_canary_dialogue(
                "unused", lambda *_args: response, root_dir=Path(tmp), canary_fixture=fixture
            )
        self.assertEqual((result.outcome, result.canary_result), ("canary", "catch"))

    def test_decision_functions_and_roster_resolution(self) -> None:
        self.assertTrue(critical_dialogue.needs_advisory_consultation("ambiguous"))
        self.assertTrue(critical_dialogue.needs_plan_review_consultation("medium"))
        self.assertTrue(critical_dialogue.needs_code_review_consultation("simple", tests_failing=True))
        self.assertTrue(critical_dialogue.needs_post_mortem_consultation(failed=True))
        self.assertFalse(critical_dialogue.needs_post_mortem_consultation(occasion="post-mortem", failed=True))
        roster = critical_dialogue.resolve_roster(
            "pair", is_family_reachable=lambda family: family in {"claude", "codex-gpt"}
        )
        self.assertFalse(roster.degraded_independence)


class CouncilReviewBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = self.temp_dir.name
        key_dir = Path(self.workspace) / ".ralph" / "cache"
        key_dir.mkdir(parents=True)
        (key_dir / "calibration.key").write_text("test-secret", encoding="utf-8")
        self.objective = "Review this design"
        self.candidate_hash = hashlib.sha256(self.objective.encode()).hexdigest()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _request(self, *, perspective: bool = False, local: bool = False) -> Any:
        return critical_dialogue.ReviewRequest(
            objective=self.objective,
            workspace_root=self.workspace,
            by_perspective=perspective,
            privacy_mode=critical_dialogue.PrivacyMode.LOCAL_ONLY if local else critical_dialogue.PrivacyMode.AUTO,
        )

    def test_review_council_unanimous_and_security_veto(self) -> None:
        council = critical_dialogue.ReviewCouncil()
        approvals = [
            _MockReviewerAdapter(
                provider, [_make_vote_payload(provider, "approve", candidate_hash=self.candidate_hash)] * 3
            )
            for provider in ("claude", "codex", "gemini")
        ]
        outcome = asyncio.run(council.review(self._request(), custom_adapters=approvals))
        self.assertEqual(outcome.status, "UNANIMOUS")

        vetoes = [
            _MockReviewerAdapter("claude", [_make_vote_payload("claude", "approve")]),
            _MockReviewerAdapter(
                "codex",
                [_make_vote_payload(
                    "codex", "block", findings=[{"severity": "critical", "confidence": 1.0}]
                )],
            ),
        ]
        outcome = asyncio.run(council.review(self._request(), custom_adapters=vetoes))
        self.assertEqual(outcome.status, "SECURITY_HALT")

    def test_review_council_weighted_quorum_majority_approval(self) -> None:
        council = critical_dialogue.ReviewCouncil()
        votes = [
            _MockReviewerAdapter(
                provider,
                [
                    _make_vote_payload(provider, "approve"),
                    _make_vote_payload(provider, vote, candidate_hash=self.candidate_hash),
                ],
            )
            for provider, vote in (("claude", "approve"), ("codex", "approve"), ("gemini", "revise"))
        ]

        outcome = asyncio.run(council.review(self._request(), custom_adapters=votes))

        self.assertEqual(outcome.status, "QUALIFIED")

    def test_review_council_local_and_perspective_modes(self) -> None:
        council = critical_dialogue.ReviewCouncil()
        self.assertEqual(council._resolve_adapters(self._request(local=True))[0].provider_id, "lm-studio")
        perspectives = [
            _MockReviewerAdapter(
                name,
                [
                    _make_vote_payload(
                        name, "approved", candidate_hash=self.candidate_hash, perspective=name
                    )
                ],
            )
            for name in ("reviewer_architecture", "reviewer_risk", "reviewer_maintainability", "reviewer_security")
        ]
        outcome = asyncio.run(council.review(self._request(perspective=True), custom_adapters=perspectives))
        self.assertEqual(outcome.status, "UNANIMOUS")

    def test_request_council_review_runs_the_async_council_to_completion(self) -> None:
        expected = critical_dialogue.ReviewOutcome(status="UNANIMOUS", run_id="test-run")

        with (
            patch.object(critical_dialogue, "ReviewCouncil", _StubCouncil),
            patch.object(_StubCouncil, "outcome", expected, create=True),
        ):
            outcome = critical_dialogue.request_council_review(self._request())
        self.assertIs(outcome, expected)

    def test_request_council_review_completes_inside_a_running_event_loop(self) -> None:
        expected = critical_dialogue.ReviewOutcome(status="UNANIMOUS", run_id="loop-run")

        async def run_in_loop() -> Any:
            return critical_dialogue.request_council_review(self._request())

        with (
            patch.object(critical_dialogue, "ReviewCouncil", _StubCouncil),
            patch.object(_StubCouncil, "outcome", expected, create=True),
        ):
            outcome = asyncio.run(run_in_loop())
        self.assertIs(outcome, expected)


class CriticalDialoguePersistenceTests(unittest.TestCase):
    def test_run_critical_dialogue_applies_budget_degradation_ladder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            budget_path = root / "routing-config.json"
            budget_path.write_text(
                json.dumps({"dialogue_budget": {"session_dialogue_cap": 1}}), encoding="utf-8"
            )
            calls: list[tuple[str, str]] = []

            def invoke(model: str, effort: str, prompt: str) -> str:
                calls.append((model, effort))
                return _mock_approve(model, effort, prompt)

            with redirect_stderr(io.StringIO()):
                reduced_rounds = critical_dialogue.run_critical_dialogue(
                    "Review the implementation", invoke, root_dir=root,
                    session_spend_so_far=1, budget_config_path=budget_path,
                )
                cheaper_roster = critical_dialogue.run_critical_dialogue(
                    "Review the implementation", invoke, root_dir=root,
                    session_spend_so_far=2, budget_config_path=budget_path,
                )
                skipped = critical_dialogue.run_critical_dialogue(
                    "Review the implementation", invoke, root_dir=root,
                    session_spend_so_far=3, budget_config_path=budget_path,
                )

        self.assertEqual(reduced_rounds.degradation_rung, 1)
        self.assertEqual(reduced_rounds.rounds_run, 1)
        self.assertEqual(cheaper_roster.degradation_rung, 2)
        self.assertEqual(
            (cheaper_roster.planner_model, cheaper_roster.critic_model),
            ("Codex 5.6 Terra", "Codex 5.6 Terra"),
        )
        self.assertEqual(skipped.outcome, "budget_skipped")
        self.assertEqual(skipped.degradation_rung, 3)
        self.assertEqual(len(calls), 4)
        self.assertEqual(calls[2:], [("Codex 5.6 Terra", "low")] * 2)

    def test_run_critical_dialogue_emits_transcript_and_telemetry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = critical_dialogue.run_critical_dialogue(
                "Review the implementation", _mock_approve, root_dir=root
            )
            transcript_path = root / ".scratch" / "planning_debate.md"
            telemetry_path = root / ".ralph" / "routing_telemetry.jsonl"
            self.assertTrue(transcript_path.is_file())
            self.assertTrue(telemetry_path.is_file())
            transcript = transcript_path.read_text(encoding="utf-8")
            telemetry = json.loads(telemetry_path.read_text(encoding="utf-8").splitlines()[-1])

        self.assertIn("**Outcome:** consensus", transcript)
        self.assertEqual(telemetry["outcome"], result.outcome)
        self.assertEqual(telemetry["rounds_run"], result.rounds_run)


if __name__ == "__main__":
    unittest.main()
