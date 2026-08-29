"""Direct contract tests for the consolidated CriticalDialogue boundary."""
from __future__ import annotations

import asyncio
import hashlib
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

if __package__:
    from . import critical_dialogue
else:
    import critical_dialogue  # type: ignore[no-redef]


class _Adapter:
    def __init__(self, provider_id: str, responses: list[dict[str, Any]]) -> None:
        self.provider_id = provider_id
        self._responses = responses
        self.calls = 0

    async def review(self, _envelope: str, _round_spec: int, _deadline: int) -> dict[str, Any]:
        response = self._responses[min(self.calls, len(self._responses) - 1)]
        self.calls += 1
        return response


class CriticalDialogueTests(unittest.TestCase):
    def _approve(self, _model: str, _effort: str, prompt: str) -> str:
        if "You are the Planner" in prompt:
            return "Proposed plan"
        return 'QUOTE: "Proposed plan"\nVERDICT: APPROVE'

    def test_run_critical_dialogue_handles_blocking_occasions_and_approval(self) -> None:
        for occasion in ("ambiguity", "plan-review", "code-review", "post-mortem"):
            with self.subTest(occasion=occasion), tempfile.TemporaryDirectory() as tmp:
                result = critical_dialogue.run_critical_dialogue(
                    "Review the implementation", self._approve, root_dir=Path(tmp), occasion=occasion
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
            _Adapter(provider, [{"provider": provider, "vote": "approve", "confidence": 1.0, "candidate_hash": self.candidate_hash}] * 3)
            for provider in ("claude", "codex", "gemini")
        ]
        outcome = asyncio.run(council.review(self._request(), custom_adapters=approvals))
        self.assertEqual(outcome.status, "UNANIMOUS")

        vetoes = [
            _Adapter("claude", [{"provider": "claude", "vote": "approve", "confidence": 1.0}]),
            _Adapter("codex", [{"provider": "codex", "vote": "block", "findings": [{"severity": "critical", "confidence": 1.0}]}]),
        ]
        outcome = asyncio.run(council.review(self._request(), custom_adapters=vetoes))
        self.assertEqual(outcome.status, "SECURITY_HALT")

    def test_review_council_local_and_perspective_modes(self) -> None:
        council = critical_dialogue.ReviewCouncil()
        self.assertEqual(council._resolve_adapters(self._request(local=True))[0].provider_id, "lm-studio")
        perspectives = [
            _Adapter(
                name,
                [{"provider": name, "perspective": name, "vote": "approved", "candidate_hash": self.candidate_hash}],
            )
            for name in ("reviewer_architecture", "reviewer_risk", "reviewer_maintainability", "reviewer_security")
        ]
        outcome = asyncio.run(council.review(self._request(perspective=True), custom_adapters=perspectives))
        self.assertEqual(outcome.status, "UNANIMOUS")

    def test_request_council_review_runs_the_async_council_to_completion(self) -> None:
        expected = critical_dialogue.ReviewOutcome(status="UNANIMOUS", run_id="test-run")

        class _Council:
            def __init__(self, *, policy_path: str | Path) -> None:
                self.policy_path = policy_path

            async def review(self, _request: Any) -> Any:
                return expected

        with patch.object(critical_dialogue, "ReviewCouncil", _Council):
            outcome = critical_dialogue.request_council_review(self._request())
        self.assertIs(outcome, expected)


if __name__ == "__main__":
    unittest.main()
