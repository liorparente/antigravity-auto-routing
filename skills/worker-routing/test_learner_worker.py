#!/usr/bin/env python3
"""Unit tests for LearnerWorker (spec 0004 ticket 22).

100% offline and deterministic: `invoke_worker` and `runner` are always
injected fakes, `now` is always a fixed timezone-aware value, and every test
runs against a fresh `tempfile.TemporaryDirectory()` — no network, no live
clock, no shared state between tests.
"""
from __future__ import annotations

import ast
import json
import tempfile
import unittest
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import learned_state
import learner_worker
import risk_tiered_application
from learning_journal import (
    DialogueQualityRecord,
    DialogueRound,
    OutcomeRecord,
    TaskLabel,
    WorkerExecutionRecord,
    append_journal_record,
)

# A fixed, timezone-aware `now`. The default 7-day weekly window is
# (2026-08-08T12:00:00Z, 2026-08-15T12:00:00Z].
_NOW = datetime(2026, 8, 15, 12, 0, 0, tzinfo=timezone.utc)
_IN_WINDOW_TS = "2026-08-10T09:00:00Z"


def _seed_worker_execution(
    root: Path, *, task_id: str, run_id: str | None = None, timestamp: str = _IN_WINDOW_TS
) -> None:
    record = WorkerExecutionRecord(
        task=TaskLabel.for_task(task_id),
        duration_ms=1200,
        cost_estimate_usd=0.05,
        success=True,
        retry_count=0,
        effort="medium",
        model_id="claude-sonnet-5",
        model_family="claude",
        run_id=run_id,
        timestamp=timestamp,
    )
    error = append_journal_record(record, root_dir=root)
    assert error is None, error


def _seed_outcome(
    root: Path, *, task_id: str, run_id: str | None = None, timestamp: str = _IN_WINDOW_TS
) -> None:
    record = OutcomeRecord(
        task=TaskLabel.for_task(task_id),
        ground_truth="tests",
        verdict="pass",
        run_id=run_id,
        timestamp=timestamp,
    )
    error = append_journal_record(record, root_dir=root)
    assert error is None, error


def _seed_dialogue(root: Path, *, task_id: str, timestamp: str = _IN_WINDOW_TS) -> None:
    record = DialogueQualityRecord(
        task=TaskLabel.for_task(task_id),
        occasion="ambiguity",
        topology="pair",
        rounds=(DialogueRound(verdict="approved", engagement_count=2),),
        timestamp=timestamp,
    )
    error = append_journal_record(record, root_dir=root)
    assert error is None, error


def _json_reply(payload: dict) -> str:
    """A worker response shaped like a real one: prose, then a fenced JSON block."""
    return f"Here is my analysis.\n```json\n{json.dumps(payload)}\n```\nDone."


class _RecordingWorker:
    """A fake `InvokeWorker` that records every call and returns a fixed reply."""

    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[tuple[str, str, str]] = []

    def __call__(self, model: str, effort: str, prompt: str) -> str:
        self.calls.append((model, effort, prompt))
        return self.response


def _counting_runner(score: float) -> tuple[Callable[[], float], list[None]]:
    calls: list[None] = []

    def runner() -> float:
        calls.append(None)
        return score

    return runner, calls


class SessionEndLightTests(unittest.TestCase):
    def test_generates_lessons_and_applies_tier1_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_worker_execution(root, task_id="task-1")
            worker = _RecordingWorker(_json_reply({"memory_lessons": ["Lesson: test seams directly."]}))

            result = learner_worker.run_session_end_light(
                worker, root_dir=root, now=_NOW, session_id="session-abc", run_id="run-1"
            )

            self.assertEqual(result.lessons, ("Lesson: test seams directly.",))
            self.assertEqual(len(result.outcomes), 1)
            self.assertEqual(result.outcomes[0].document, "memory")
            self.assertEqual(result.outcomes[0].status, "applied")
            self.assertTrue(result.outcomes[0].applied)

            current = learned_state.read_current(root)
            self.assertEqual(current.get("memory"), "Lesson: test seams directly.")
            self.assertEqual(len(worker.calls), 1)

    def test_rejects_naive_now_without_invoking_worker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            naive_now = datetime(2026, 8, 15, 12, 0, 0)  # noqa: DTZ001 - the value under test
            worker = _RecordingWorker(_json_reply({"memory_lessons": []}))

            with self.assertRaises(ValueError):
                learner_worker.run_session_end_light(worker, root_dir=root, now=naive_now)

            self.assertEqual(worker.calls, [])

    def test_empty_journal_still_invokes_worker_and_yields_no_lessons(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worker = _RecordingWorker(_json_reply({"memory_lessons": []}))

            result = learner_worker.run_session_end_light(worker, root_dir=root, now=_NOW)

            self.assertEqual(result.lessons, ())
            self.assertEqual(result.outcomes, ())
            self.assertEqual(len(worker.calls), 1)
            self.assertIn("worker_executions: 0", worker.calls[0][2])

    def test_robust_to_non_json_worker_response(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_worker_execution(root, task_id="task-1")
            worker = _RecordingWorker("I'm sorry, I cannot help with that request.")

            result = learner_worker.run_session_end_light(worker, root_dir=root, now=_NOW)

            self.assertEqual(result.lessons, ())
            self.assertEqual(result.outcomes, ())
            self.assertEqual(result.raw_response, worker.response)

    def test_robust_to_malformed_memory_lessons_field(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worker = _RecordingWorker(_json_reply({"memory_lessons": "not a list"}))

            result = learner_worker.run_session_end_light(worker, root_dir=root, now=_NOW)

            self.assertEqual(result.lessons, ())
            self.assertEqual(result.outcomes, ())

    def test_prompt_carries_session_context_and_record_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_worker_execution(root, task_id="task-42", run_id="run-9")
            _seed_outcome(root, task_id="task-42", run_id="run-9")
            worker = _RecordingWorker(_json_reply({"memory_lessons": []}))

            learner_worker.run_session_end_light(
                worker, root_dir=root, now=_NOW, session_id="session-xyz", run_id="run-9"
            )

            self.assertEqual(len(worker.calls), 1)
            model, effort, prompt = worker.calls[0]
            self.assertEqual(model, learner_worker._SESSION_LIGHT_MODEL)
            self.assertEqual(effort, learner_worker._SESSION_LIGHT_EFFORT)
            self.assertIn("session_id: session-xyz", prompt)
            self.assertIn("run_id: run-9", prompt)
            self.assertIn("task=task-42", prompt)
            self.assertIn("memory_lessons", prompt)

    def test_prompt_includes_dialogue_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_dialogue(root, task_id="task-dialogue")
            worker = _RecordingWorker(_json_reply({"memory_lessons": []}))

            learner_worker.run_session_end_light(worker, root_dir=root, now=_NOW)

            prompt = worker.calls[0][2]
            self.assertIn("dialogues: 1", prompt)
            self.assertIn("task=task-dialogue", prompt)
            self.assertIn("occasion=ambiguity", prompt)

    def test_run_id_filter_excludes_other_runs_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_worker_execution(root, task_id="task-mine", run_id="run-mine")
            _seed_worker_execution(root, task_id="task-other", run_id="run-other")
            worker = _RecordingWorker(_json_reply({"memory_lessons": []}))

            learner_worker.run_session_end_light(worker, root_dir=root, now=_NOW, run_id="run-mine")

            prompt = worker.calls[0][2]
            self.assertIn("task=task-mine", prompt)
            self.assertNotIn("task=task-other", prompt)


class WeeklyDeepTests(unittest.TestCase):
    def test_loads_trials_and_score_threshold_from_routing_config(self) -> None:
        config_path = Path(__file__).with_name("routing-config.json")
        trials, score_threshold = learner_worker._load_acceptance_gate_config(config_path)
        self.assertEqual(trials, 5)
        self.assertEqual(score_threshold, 0.8)

    def test_load_acceptance_gate_config_genuinely_reads_the_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            custom_config = Path(tmp) / "routing-config.json"
            custom_config.write_text(
                json.dumps({"acceptance_gate": {"trials": 3, "score_threshold": 0.55}}),
                encoding="utf-8",
            )
            trials, score_threshold = learner_worker._load_acceptance_gate_config(custom_config)
            self.assertEqual(trials, 3)
            self.assertEqual(score_threshold, 0.55)

    def test_routing_table_update_evaluated_and_applied_through_acceptance_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_worker_execution(root, task_id="task-1", run_id="run-1")
            _seed_outcome(root, task_id="task-1", run_id="run-1")
            worker = _RecordingWorker(
                _json_reply({"routing_table_update": '{"version": "v2", "routes": []}'})
            )
            runner, calls = _counting_runner(0.95)

            result = learner_worker.run_weekly_deep(
                worker, root_dir=root, now=_NOW, runner=runner
            )

            self.assertEqual(len(calls), 5)  # trials from routing-config.json
            self.assertEqual(len(result.routing_outcomes), 1)
            self.assertEqual(result.routing_outcomes[0].document, "routing_table")
            self.assertEqual(result.routing_outcomes[0].status, "applied")
            gate_decision = result.routing_outcomes[0].gate_decision
            self.assertIsNotNone(gate_decision)
            assert gate_decision is not None  # narrows for mypy; asserted above
            self.assertTrue(gate_decision.accepted)
            current = learned_state.read_current(root)
            self.assertEqual(current.get("routing_table"), '{"version": "v2", "routes": []}')

    def test_routing_table_update_rejected_when_score_below_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worker = _RecordingWorker(
                _json_reply({"routing_table_update": '{"version": "v2_bad"}'})
            )
            runner, _ = _counting_runner(0.1)

            result = learner_worker.run_weekly_deep(
                worker, root_dir=root, now=_NOW, runner=runner
            )

            self.assertEqual(result.routing_outcomes[0].status, "rejected")
            self.assertFalse(result.routing_outcomes[0].applied)
            self.assertEqual(learned_state.read_current(root), {})

    def test_brief_proposal_staged_as_pending_not_adopted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worker = _RecordingWorker(
                _json_reply({"brief_update": "# Context Brief v2\nPrefer deep modules."})
            )
            runner, _ = _counting_runner(0.95)

            result = learner_worker.run_weekly_deep(
                worker, root_dir=root, now=_NOW, runner=runner
            )

            self.assertEqual(len(result.brief_outcomes), 1)
            self.assertEqual(result.brief_outcomes[0].document, "briefs")
            self.assertEqual(result.brief_outcomes[0].status, "pending")
            self.assertFalse(result.brief_outcomes[0].applied)

            pending = risk_tiered_application.read_pending_proposals(root)
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0].content, "# Context Brief v2\nPrefer deep modules.")
            self.assertNotIn("briefs", learned_state.read_current(root))

    def test_memory_lessons_applied_as_tier1(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worker = _RecordingWorker(
                _json_reply({"memory_lessons": ["Lesson A", "Lesson B"]})
            )
            runner, _ = _counting_runner(0.95)

            result = learner_worker.run_weekly_deep(
                worker, root_dir=root, now=_NOW, runner=runner
            )

            self.assertEqual(len(result.memory_outcomes), 2)
            self.assertTrue(all(o.status == "applied" for o in result.memory_outcomes))
            # Only the last-adopted memory content is visible via read_current,
            # but the history must show both adoptions.
            self.assertEqual(len(learned_state.read_history(root)), 2)

    def test_writes_weekly_markdown_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_worker_execution(root, task_id="task-1")
            worker = _RecordingWorker(_json_reply({}))
            runner, _ = _counting_runner(0.95)

            result = learner_worker.run_weekly_deep(
                worker, root_dir=root, now=_NOW, runner=runner
            )

            self.assertTrue(result.report_path.exists())
            self.assertEqual(result.report_path.parent, root / ".ralph" / "reports")
            content = result.report_path.read_text(encoding="utf-8")
            self.assertIn("Weekly Learning Report", content)

    def test_retrospective_summary_carried_from_worker_response(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worker = _RecordingWorker(
                _json_reply({"retrospective_summary": "A quiet week overall."})
            )
            runner, _ = _counting_runner(0.95)

            result = learner_worker.run_weekly_deep(
                worker, root_dir=root, now=_NOW, runner=runner
            )

            self.assertEqual(result.retrospective_summary, "A quiet week overall.")

    def test_rejects_naive_now_without_invoking_worker_or_runner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            naive_now = datetime(2026, 8, 15, 12, 0, 0)  # noqa: DTZ001 - the value under test
            worker = _RecordingWorker(_json_reply({}))
            runner, calls = _counting_runner(0.95)

            with self.assertRaises(ValueError):
                learner_worker.run_weekly_deep(worker, root_dir=root, now=naive_now, runner=runner)

            self.assertEqual(worker.calls, [])
            self.assertEqual(calls, [])

    def test_no_proposal_keys_yields_no_tier_outcomes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worker = _RecordingWorker("No proposals this week.")
            runner, calls = _counting_runner(0.95)

            result = learner_worker.run_weekly_deep(
                worker, root_dir=root, now=_NOW, runner=runner
            )

            self.assertEqual(result.routing_outcomes, ())
            self.assertEqual(result.brief_outcomes, ())
            self.assertEqual(result.memory_outcomes, ())
            self.assertEqual(calls, [])  # the gate never ran; no proposal to evaluate


class TestSeamAndSeparationTests(unittest.TestCase):
    """Prompt-structure assertions and proposer/approver separation."""

    def test_invoke_worker_called_with_expected_prompt_structure_light(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worker = _RecordingWorker(_json_reply({"memory_lessons": []}))
            learner_worker.run_session_end_light(worker, root_dir=root, now=_NOW)

            self.assertEqual(len(worker.calls), 1)
            model, effort, prompt = worker.calls[0]
            self.assertEqual(model, learner_worker._SESSION_LIGHT_MODEL)
            self.assertEqual(effort, learner_worker._SESSION_LIGHT_EFFORT)
            self.assertIn("session-end light pass", prompt)
            self.assertIn("as_of: 2026-08-15T12:00:00Z", prompt)
            self.assertIn('"memory_lessons"', prompt)

    def test_invoke_worker_called_with_expected_prompt_structure_deep(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_worker_execution(root, task_id="task-1")
            worker = _RecordingWorker(_json_reply({}))
            runner, _ = _counting_runner(0.95)
            learner_worker.run_weekly_deep(worker, root_dir=root, now=_NOW, runner=runner)

            self.assertEqual(len(worker.calls), 1)
            model, effort, prompt = worker.calls[0]
            self.assertEqual(model, learner_worker._WEEKLY_DEEP_MODEL)
            self.assertEqual(effort, learner_worker._WEEKLY_DEEP_EFFORT)
            self.assertIn("weekly deep run", prompt)
            self.assertIn("window_days: 7", prompt)
            self.assertIn("violations_per_session", prompt)
            self.assertIn('"routing_table_update"', prompt)
            self.assertIn("task=task-1", prompt)

    def test_module_never_imports_learned_state(self) -> None:
        """The learner proposes; it never adopts. Enforced structurally: this
        module cannot call `learned_state.adopt` if it never imports
        `learned_state` at all — every mutation must instead flow through
        `risk_tiered_application`'s tiering.
        """
        source = Path(learner_worker.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported_names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_names.add(node.module)

        self.assertNotIn("learned_state", imported_names)
        self.assertNotIn("adopt(", source)

    def test_all_mutations_flow_through_risk_tiered_application(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worker = _RecordingWorker(
                _json_reply(
                    {
                        "routing_table_update": '{"routes": []}',
                        "brief_update": "# Brief",
                        "memory_lessons": ["Lesson"],
                    }
                )
            )
            runner, _ = _counting_runner(0.95)

            with (
                patch.object(
                    risk_tiered_application,
                    "apply_routing_table_update",
                    wraps=risk_tiered_application.apply_routing_table_update,
                ) as routing_spy,
                patch.object(
                    risk_tiered_application,
                    "submit_brief_proposal",
                    wraps=risk_tiered_application.submit_brief_proposal,
                ) as brief_spy,
                patch.object(
                    risk_tiered_application,
                    "apply_memory_lesson",
                    wraps=risk_tiered_application.apply_memory_lesson,
                ) as memory_spy,
            ):
                learner_worker.run_weekly_deep(worker, root_dir=root, now=_NOW, runner=runner)

            routing_spy.assert_called_once()
            brief_spy.assert_called_once()
            memory_spy.assert_called_once()


if __name__ == "__main__":
    unittest.main()
