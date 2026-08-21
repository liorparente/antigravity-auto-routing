"""Unit tests for extracted advisory transcript and telemetry persistence."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parent))

if __package__:
    from . import advisory_consultation, dialogue_contracts, dialogue_transcript
else:
    import advisory_consultation  # type: ignore[no-redef]
    import dialogue_contracts  # type: ignore[no-redef]
    import dialogue_transcript  # type: ignore[no-redef]


class DialogueTranscriptTests(unittest.TestCase):
    """Pin the extraction's redaction, identity, rendering, and write contracts."""

    def _result(self, **overrides: object) -> advisory_consultation.AdvisoryDebateResult:
        values: dict[str, object] = {
            "rounds_run": 1,
            "final_plan": "safe proposal",
            "outcome": "consensus",
            "planner_model": "Planner",
            "critic_model": "Critic",
            "rounds": (advisory_consultation.AdvisoryDebateRound("safe proposal", "VERDICT: APPROVE"),),
        }
        values.update(overrides)
        return advisory_consultation.AdvisoryDebateResult(**values)  # type: ignore[arg-type]

    def test_default_task_id_is_stable_16_hex_sha256(self) -> None:
        task = "Plan a release"
        first = dialogue_transcript._default_task_id(task)
        self.assertEqual(first, dialogue_transcript._default_task_id(task))
        self.assertRegex(first, r"^[0-9a-f]{16}$")

    def test_resolve_task_id_obeys_sensitive_and_canary_boundaries(self) -> None:
        self.assertEqual(
            dialogue_transcript._resolve_task_id("secret task", "caller-id", "sensitivity_halt"),
            "caller-id",
        )
        task = "ordinary task"
        self.assertEqual(
            dialogue_transcript._resolve_task_id(task, None, "consensus"),
            dialogue_transcript._default_task_id(task),
        )
        for outcome in ("sensitivity_halt", "canary"):
            value = dialogue_transcript._resolve_task_id(task, None, outcome)
            self.assertRegex(value, r"^[0-9a-f]{16}$")
            self.assertNotEqual(value, dialogue_transcript._default_task_id(task))

    def test_sensitivity_halt_renderer_accepts_no_task_text(self) -> None:
        secret_task = "deploy api_key=super-secret-value"
        rendered = dialogue_transcript._render_sensitivity_halt_transcript("api_key", "random-id")
        self.assertIn("api_key", rendered)
        self.assertIn("random-id", rendered)
        self.assertNotIn(secret_task, rendered)
        self.assertNotIn("super-secret-value", rendered)
        self.assertNotIn("## Task", rendered)

    def test_public_sensitivity_halt_renderer_matches_compatibility_renderer(self) -> None:
        self.assertEqual(
            dialogue_transcript.render_sensitivity_halt_transcript("api_key", "safe-id"),
            dialogue_transcript._render_sensitivity_halt_transcript("api_key", "safe-id"),
        )

    def test_transcript_marks_degradation_independence_and_canary(self) -> None:
        result = self._result(
            outcome="canary",
            degraded_independence=True,
            canary_result="catch",
            degradation_rung=1,
        )
        fixture = advisory_consultation.CanaryFixture("bad-fixture", "missing rollback", "safe proposal")
        rendered = dialogue_transcript._render_consultation_transcript("task", result, canary_fixture=fixture)
        self.assertIn("**Outcome:** canary", rendered)
        self.assertIn("Planner", rendered)
        self.assertIn("Critic", rendered)
        self.assertIn("**Rounds run:** 1", rendered)
        self.assertIn(dialogue_transcript.DEGRADED_INDEPENDENCE_MARKER, rendered)
        self.assertIn(dialogue_transcript.CANARY_MARKER, rendered)
        self.assertIn(dialogue_transcript.BUDGET_DEGRADATION_MARKER, rendered)

    def test_public_consultation_renderer_matches_compatibility_renderer(self) -> None:
        result = self._result()
        self.assertEqual(
            dialogue_transcript.render_consultation_transcript("task", result),
            dialogue_transcript._render_consultation_transcript("task", result),
        )

    def test_format_transcript_markdown_renders_round_layout_without_orchestrator_types(self) -> None:
        class Round:
            planner_proposal = "proposal"
            critic_response = "VERDICT: REVISE"
            critic_b_response = None

        rendered = dialogue_transcript.format_transcript_markdown(
            "task", [Round()], "Planner", "Critic", "revise",
        )

        self.assertIn("**Outcome:** revise", rendered)
        self.assertIn("### Planner (Planner)", rendered)
        self.assertIn("### Critic (Critic)", rendered)
        self.assertIn("proposal", rendered)

    def test_telemetry_mapping_and_locked_write(self) -> None:
        record = dialogue_transcript.AdvisoryTelemetryRecord(
            timestamp="2026-01-01T00:00:00Z", task_id="task-id", rounds_run=1,
            outcome="consensus", planner_model="Planner", critic_model="Critic",
        )
        self.assertEqual(record.to_mapping()["kind"], "advisory_consultation")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "telemetry.jsonl"
            self.assertIsNone(dialogue_transcript._write_telemetry_record(path, record))
            written = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(written["task_id"], "task-id")
        self.assertEqual(written["outcome"], "consensus")

    def test_reduce_dialogue_round_handles_pair_and_panel_engagement(self) -> None:
        approved = dialogue_contracts.VerdictContractResult("approved", 3, 0)
        revise = dialogue_contracts.VerdictContractResult("revise", 1, 1)
        unparseable = dialogue_contracts.VerdictContractResult("unparseable", 2, 0)
        self.assertEqual(
            dialogue_transcript._reduce_dialogue_round(dialogue_contracts.AdvisoryRoundVerdict(approved)),
            ("approved", 3),
        )
        self.assertEqual(
            dialogue_transcript._reduce_dialogue_round(dialogue_contracts.AdvisoryRoundVerdict(approved, revise)),
            ("revise", 1),
        )
        self.assertEqual(
            dialogue_transcript._reduce_dialogue_round(dialogue_contracts.AdvisoryRoundVerdict(approved, unparseable)),
            ("unparseable", 2),
        )


if __name__ == "__main__":
    unittest.main()
