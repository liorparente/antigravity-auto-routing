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
import re
import sys
import tempfile
import unittest
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parent))

if __package__:
    from . import learned_state, learner_worker, risk_tiered_application
    from .learning_journal import (
        ComplianceRecord,
        DialogueQualityRecord,
        DialogueRound,
        OutcomeRecord,
        ReplayBenchmarkRecord,
        TaskLabel,
        WorkerExecutionRecord,
        append_journal_record,
    )
else:
    import learned_state  # type: ignore[no-redef]
    import learner_worker  # type: ignore[no-redef]
    import risk_tiered_application  # type: ignore[no-redef]
    from learning_journal import (  # type: ignore[no-redef]
        ComplianceRecord,
        DialogueQualityRecord,
        DialogueRound,
        OutcomeRecord,
        ReplayBenchmarkRecord,
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


def _seed_compliance(
    root: Path,
    *,
    session_id: str,
    run_id: str | None = None,
    violation_count: int = 0,
    declaration_drift_count: int = 0,
    issue_codes: tuple[str, ...] = (),
    timestamp: str = _IN_WINDOW_TS,
) -> None:
    record = ComplianceRecord(
        session_id=session_id,
        total_writes=0,
        code_writes=0,
        routing_declarations=0,
        worker_calls=0,
        violation_count=violation_count,
        declaration_drift_count=declaration_drift_count,
        calibration_markers=0,
        code_write_count=0,
        issue_codes=issue_codes,
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


def _seed_replay_benchmark(
    root: Path,
    *,
    task_set: str = "bench-v1",
    run_id: str | None = None,
    score: float = 0.9,
    timestamp: str = _IN_WINDOW_TS,
) -> None:
    record = ReplayBenchmarkRecord(
        task_set=task_set,
        success=True,
        score=score,
        run_id=run_id,
        timestamp=timestamp,
    )
    error = append_journal_record(record, root_dir=root)
    assert error is None, error


def _imported_names(source: str) -> set[str]:
    """Every module and symbol name `source` imports, collected from every
    `ast.Import` and `ast.ImportFrom` node. `from . import x` puts `x` in
    `alias.name` with `node.module` `None`; `from .x import y` puts `x` in
    `node.module` and `y` in `alias.name` — so both must be collected or a
    relative import slips past.
    """
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module)
            names.update(alias.name for alias in node.names)
    return names


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


@dataclass(frozen=True)
class _LightRenderCapture:
    """What `_render_session_light_prompt` was actually called with, plus the
    pass's own result. Per docs/specs/0004-learning-loop.md:168-170, tests
    assert on the selected journal records and call arguments a render
    function received — never on the wording of the prompt it produced.
    """

    result: learner_worker.SessionEndResult
    journal: learner_worker.learning_journal.JournalRead
    now: datetime


def _run_light_capturing_render_args(
    worker: _RecordingWorker,
    root: Path,
    *,
    now: datetime = _NOW,
    session_id: str | None = None,
    run_id: str | None = None,
) -> _LightRenderCapture:
    """Run the light pass while spying on `_render_session_light_prompt`,
    capturing the `journal: JournalRead` and `now` it was actually given.
    """
    captured: dict[str, Any] = {}
    original = learner_worker._render_session_light_prompt

    def _capture(journal: learner_worker.learning_journal.JournalRead, **kwargs: Any) -> str:
        captured["journal"] = journal
        captured["now"] = kwargs["now"]
        return original(journal, **kwargs)

    with patch.object(learner_worker, "_render_session_light_prompt", side_effect=_capture):
        result = learner_worker.run_session_end_light(
            worker, root_dir=root, now=now, session_id=session_id, run_id=run_id
        )
    return _LightRenderCapture(result=result, journal=captured["journal"], now=captured["now"])


@dataclass(frozen=True)
class _WeeklyRenderCapture:
    """What `_render_weekly_deep_prompt` was actually called with, plus the
    deep pass's own result. Same rationale as `_LightRenderCapture` above.
    """

    result: learner_worker.WeeklyDeepResult
    comparison: learner_worker.learning_scoreboard.ScoreboardComparison
    journal: learner_worker.learning_journal.JournalRead
    now: datetime
    window_days: int


def _run_weekly_capturing_render_args(
    worker: _RecordingWorker,
    root: Path,
    runner: Callable[[], float],
    *,
    now: datetime = _NOW,
    window_days: int = learner_worker.DEFAULT_WINDOW_DAYS,
    run_id: str | None = None,
) -> _WeeklyRenderCapture:
    """Run the deep pass while spying on `_render_weekly_deep_prompt`,
    capturing the scoreboard `comparison`, the `journal: JournalRead`, and
    the `now`/`window_days` it was actually given.
    """
    captured: dict[str, Any] = {}
    original = learner_worker._render_weekly_deep_prompt

    def _capture(
        comparison: learner_worker.learning_scoreboard.ScoreboardComparison,
        journal: learner_worker.learning_journal.JournalRead,
        **kwargs: Any,
    ) -> str:
        captured["comparison"] = comparison
        captured["journal"] = journal
        captured["now"] = kwargs["now"]
        captured["window_days"] = kwargs["window_days"]
        return original(comparison, journal, **kwargs)

    with patch.object(learner_worker, "_render_weekly_deep_prompt", side_effect=_capture):
        result = learner_worker.run_weekly_deep(
            worker, root_dir=root, now=now, runner=runner, window_days=window_days, run_id=run_id
        )
    return _WeeklyRenderCapture(
        result=result,
        comparison=captured["comparison"],
        journal=captured["journal"],
        now=captured["now"],
        window_days=captured["window_days"],
    )


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
            self.assertEqual(current.get("memory"), "- Lesson: test seams directly.")
            self.assertEqual(len(worker.calls), 1)

    def test_light_pass_formats_multiline_lesson_with_indented_continuation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            multiline_lesson = "First line of lesson\ncontinued on a second line"
            worker = _RecordingWorker(_json_reply({"memory_lessons": [multiline_lesson]}))

            result = learner_worker.run_session_end_light(worker, root_dir=root, now=_NOW)

            self.assertEqual(result.lessons, (multiline_lesson,))
            current = learned_state.read_current(root)
            self.assertEqual(
                current.get("memory"),
                "- First line of lesson\n  continued on a second line",
            )
            # Round-trips as one entry, not split by `_parse_memory_document`'s
            # bulleted grammar into a malformed mixture or two entries.
            entries = risk_tiered_application._parse_memory_document(current.get("memory", ""))
            self.assertEqual(entries, (multiline_lesson,))

    def test_light_pass_consolidates_multiple_lessons_into_one_document(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_worker_execution(root, task_id="task-1")
            worker = _RecordingWorker(
                _json_reply({"memory_lessons": ["Lesson A", "Lesson B"]})
            )

            result = learner_worker.run_session_end_light(worker, root_dir=root, now=_NOW)

            self.assertEqual(result.lessons, ("Lesson A", "Lesson B"))
            self.assertEqual(len(result.outcomes), 1)
            current = learned_state.read_current(root)
            self.assertIn("Lesson A", current.get("memory", ""))
            self.assertIn("Lesson B", current.get("memory", ""))

    def test_across_two_calls_accumulates_lessons(self) -> None:
        """Cross-run accumulation (ADR 0010, Ticket 33): a second session's
        light pass merges its lessons with the first session's rather than
        replacing them — the gap this ticket closes.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first_worker = _RecordingWorker(_json_reply({"memory_lessons": ["Session 1 lesson"]}))
            learner_worker.run_session_end_light(
                first_worker, root_dir=root, now=_NOW, session_id="session-1"
            )

            second_worker = _RecordingWorker(_json_reply({"memory_lessons": ["Session 2 lesson"]}))
            result = learner_worker.run_session_end_light(
                second_worker, root_dir=root, now=_NOW, session_id="session-2"
            )

            self.assertEqual(result.outcomes[0].status, "applied")
            current = learned_state.read_current(root)
            self.assertEqual(current.get("memory"), "- Session 1 lesson\n- Session 2 lesson")
            self.assertEqual(len(learned_state.read_history(root)), 2)

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

            captured = _run_light_capturing_render_args(worker, root)

            self.assertEqual(captured.result.lessons, ())
            self.assertEqual(captured.result.outcomes, ())
            self.assertEqual(len(worker.calls), 1)
            self.assertEqual(captured.journal.worker_executions, ())

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
            _seed_compliance(root, session_id="session-xyz", run_id="run-9")
            worker = _RecordingWorker(_json_reply({"memory_lessons": []}))

            captured = _run_light_capturing_render_args(
                worker, root, session_id="session-xyz", run_id="run-9"
            )

            self.assertEqual(len(worker.calls), 1)
            model, effort, _prompt = worker.calls[0]
            self.assertEqual(model, learner_worker._SESSION_LIGHT_MODEL)
            self.assertEqual(effort, learner_worker._SESSION_LIGHT_EFFORT)
            journal = captured.journal
            self.assertEqual([r.task.task_id for r in journal.worker_executions], ["task-42"])
            self.assertEqual([r.task.task_id for r in journal.outcomes], ["task-42"])
            self.assertEqual([c.session_id for c in journal.compliance], ["session-xyz"])

    def test_prompt_includes_dialogue_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_dialogue(root, task_id="task-dialogue")
            worker = _RecordingWorker(_json_reply({"memory_lessons": []}))

            captured = _run_light_capturing_render_args(worker, root)

            journal = captured.journal
            self.assertEqual(len(journal.dialogues), 1)
            self.assertEqual(journal.dialogues[0].task.task_id, "task-dialogue")
            self.assertEqual(journal.dialogues[0].occasion, "ambiguity")

    def test_prompt_reduces_duplicate_outcomes_positionally(self) -> None:
        """A task re-tested within the same session writes a second
        `OutcomeRecord` under the same `(task_id, ground_truth)` pair. The
        rendered prompt must count and list the reduced, authoritative
        records — one per pair, last verdict wins — not the raw journal rows.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_outcome(root, task_id="task-dup", timestamp="2026-08-15T10:00:00Z")
            _seed_outcome(
                root,
                task_id="task-dup",
                timestamp="2026-08-15T11:00:00Z",
            )
            error = append_journal_record(
                OutcomeRecord(
                    task=TaskLabel.for_task("task-dup"),
                    ground_truth="tests",
                    verdict="fail",
                    timestamp="2026-08-15T11:30:00Z",
                ),
                root_dir=root,
            )
            assert error is None, error
            _seed_outcome(root, task_id="task-other", timestamp="2026-08-15T09:00:00Z")
            worker = _RecordingWorker(_json_reply({"memory_lessons": []}))

            _run_light_capturing_render_args(worker, root)

            self.assertEqual(len(worker.calls), 1)
            _model, _effort, prompt = worker.calls[0]
            self.assertIn("outcomes: 2", prompt)
            self.assertIn("task=task-dup ground_truth=tests verdict=fail", prompt)
            self.assertNotIn("task=task-dup ground_truth=tests verdict=pass", prompt)
            self.assertIn("task=task-other ground_truth=tests verdict=pass", prompt)

    def test_run_id_filter_excludes_other_runs_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_worker_execution(root, task_id="task-mine", run_id="run-mine")
            _seed_worker_execution(root, task_id="task-other", run_id="run-other")
            worker = _RecordingWorker(_json_reply({"memory_lessons": []}))

            captured = _run_light_capturing_render_args(worker, root, run_id="run-mine")

            self.assertEqual(
                [r.task.task_id for r in captured.journal.worker_executions], ["task-mine"]
            )

    def test_session_id_alone_narrows_compliance_only(self) -> None:
        """`session_id` without `run_id` narrows only `compliance` — the one
        family that actually carries `session_id`. It must not be joined
        through compliance's `run_id`s to filter the other four families;
        those have no session concept of their own, so both sessions' worker
        evidence still appears when only `session_id` is given.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_compliance(
                root, session_id="session-mine", run_id="run-mine", violation_count=2,
                declaration_drift_count=1, issue_codes=("RT-01",),
            )
            _seed_compliance(root, session_id="session-other", run_id="run-other")
            _seed_worker_execution(root, task_id="task-mine", run_id="run-mine")
            _seed_worker_execution(root, task_id="task-other", run_id="run-other")
            _seed_outcome(root, task_id="task-mine", run_id="run-mine")
            _seed_outcome(root, task_id="task-other", run_id="run-other")
            worker = _RecordingWorker(_json_reply({"memory_lessons": []}))

            captured = _run_light_capturing_render_args(
                worker, root, session_id="session-mine"
            )

            journal = captured.journal
            # Not narrowed: session_id has no join partner on these families.
            self.assertEqual(
                {r.task.task_id for r in journal.worker_executions}, {"task-mine", "task-other"}
            )
            self.assertEqual(
                {r.task.task_id for r in journal.outcomes}, {"task-mine", "task-other"}
            )
            # Narrowed: compliance carries session_id directly.
            self.assertEqual(len(journal.compliance), 1)
            comp = journal.compliance[0]
            self.assertEqual(comp.session_id, "session-mine")
            self.assertEqual(comp.violation_count, 2)
            self.assertEqual(comp.declaration_drift_count, 1)
            self.assertEqual(comp.issue_codes, ("RT-01",))

    def test_run_id_narrows_all_five_families_independently_of_session_id(self) -> None:
        """`run_id` given alongside `session_id` narrows every family that
        carries `run_id` directly by that field — including `compliance`,
        which then reflects both filters since it carries both identities.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_compliance(root, session_id="session-mine", run_id="run-mine")
            _seed_compliance(root, session_id="session-mine", run_id="run-other-in-session")
            _seed_worker_execution(root, task_id="task-mine", run_id="run-mine")
            _seed_worker_execution(root, task_id="task-other", run_id="run-other-in-session")
            _seed_outcome(root, task_id="task-mine", run_id="run-mine")
            worker = _RecordingWorker(_json_reply({"memory_lessons": []}))

            captured = _run_light_capturing_render_args(
                worker, root, session_id="session-mine", run_id="run-mine"
            )

            journal = captured.journal
            self.assertEqual([r.task.task_id for r in journal.worker_executions], ["task-mine"])
            self.assertEqual(len(journal.compliance), 1)
            self.assertEqual(journal.compliance[0].run_id, "run-mine")

    def test_session_id_with_mismatched_run_id_yields_no_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_compliance(root, session_id="session-mine", run_id="run-mine")
            _seed_worker_execution(root, task_id="task-mine", run_id="run-mine")
            worker = _RecordingWorker(_json_reply({"memory_lessons": []}))

            captured = _run_light_capturing_render_args(
                worker,
                root,
                session_id="session-mine",
                run_id="run-not-in-session",
            )

            self.assertEqual(captured.journal.worker_executions, ())
            self.assertEqual(captured.journal.compliance, ())


class LocalValidatorTests(unittest.TestCase):
    """`learner_worker._validate_trials`/`_validate_score_threshold` are
    local mirrors of `acceptance_gate`'s private validators of the same
    name — never calls across the module boundary into `acceptance_gate`'s
    own private functions. Exercised directly here, not only through
    `_load_acceptance_gate_config`.
    """

    def test_validate_trials_rejects_bool(self) -> None:
        with self.assertRaises(ValueError):
            learner_worker._validate_trials(True, "trials")

    def test_validate_trials_rejects_non_positive(self) -> None:
        with self.assertRaises(ValueError):
            learner_worker._validate_trials(0, "trials")

    def test_validate_trials_accepts_positive_int(self) -> None:
        learner_worker._validate_trials(5, "trials")

    def test_validate_score_threshold_rejects_bool(self) -> None:
        with self.assertRaises(ValueError):
            learner_worker._validate_score_threshold(True, "score_threshold")

    def test_validate_score_threshold_rejects_non_finite(self) -> None:
        with self.assertRaises(ValueError):
            learner_worker._validate_score_threshold(float("nan"), "score_threshold")
        with self.assertRaises(ValueError):
            learner_worker._validate_score_threshold(float("inf"), "score_threshold")

    def test_validate_score_threshold_accepts_finite_number(self) -> None:
        learner_worker._validate_score_threshold(0.8, "score_threshold")


class FormatLessonEntryTests(unittest.TestCase):
    def test_single_line_lesson_gets_leading_dash(self) -> None:
        self.assertEqual(learner_worker._format_lesson_entry("Lesson A"), "- Lesson A")

    def test_multiline_lesson_indents_continuation_lines(self) -> None:
        self.assertEqual(
            learner_worker._format_lesson_entry("First line\nSecond line\nThird line"),
            "- First line\n  Second line\n  Third line",
        )

    def test_formatted_entry_round_trips_through_parse_memory_document(self) -> None:
        lesson = "First line\nSecond line"
        formatted = learner_worker._format_lesson_entry(lesson)
        entries = risk_tiered_application._parse_memory_document(formatted)
        self.assertEqual(entries, (lesson,))


class StringTupleTests(unittest.TestCase):
    def test_string_tuple_filters_non_string_elements(self) -> None:
        result = learner_worker._string_tuple(
            ["Lesson 1", 123, None, "Lesson 2", "   "]
        )
        self.assertEqual(result, ("Lesson 1", "Lesson 2"))


class PrefixCutJournalTests(unittest.TestCase):
    def test_prefix_cut_excludes_future_stamped_records(self) -> None:
        """`_prefix_cut_journal` must drop every record stamped after `now` —
        `run_weekly_deep`/`run_session_end_light` both trust this to keep a
        future-dated record (e.g. clock skew on the writer) from ever
        reaching a prompt or a scoreboard.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            future_ts = "2026-08-20T00:00:00Z"  # after _NOW (2026-08-15T12:00:00Z)
            _seed_worker_execution(root, task_id="task-future", timestamp=future_ts)
            _seed_worker_execution(root, task_id="task-past", timestamp=_IN_WINDOW_TS)

            journal = learner_worker._prefix_cut_journal(root, now=_NOW)

            self.assertEqual(
                [r.task.task_id for r in journal.worker_executions], ["task-past"]
            )


class WeeklyDeepTests(unittest.TestCase):
    def test_loads_trials_and_score_threshold_from_routing_config(self) -> None:
        config_path = Path(__file__).with_name("routing-config.json")
        config_data = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertIn("acceptance_gate", config_data)

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

    def test_load_acceptance_gate_config_rejects_bool_trials(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            custom_config = Path(tmp) / "routing-config.json"
            custom_config.write_text(
                json.dumps({"acceptance_gate": {"trials": True, "score_threshold": 0.5}}),
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                learner_worker._load_acceptance_gate_config(custom_config)

    def test_load_acceptance_gate_config_rejects_non_finite_score_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            custom_config = Path(tmp) / "routing-config.json"
            custom_config.write_text(
                json.dumps({"acceptance_gate": {"trials": 3, "score_threshold": float("inf")}}),
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                learner_worker._load_acceptance_gate_config(custom_config)

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

    def test_weekly_run_id_reaches_routing_update_gate_trials(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worker = _RecordingWorker(
                _json_reply({"routing_table_update": '{"version": "v2", "routes": []}'})
            )
            runner, _ = _counting_runner(0.95)

            result = learner_worker.run_weekly_deep(
                worker, root_dir=root, now=_NOW, runner=runner, run_id="weekly-run-32"
            )

            gate_decision = result.routing_outcomes[0].gate_decision
            self.assertIsNotNone(gate_decision)
            assert gate_decision is not None  # narrows for mypy; asserted above
            self.assertEqual(len(gate_decision.trial_records), 5)  # trials from routing-config.json
            self.assertTrue(
                all(record.run_id == "weekly-run-32" for record in gate_decision.trial_records)
            )

    def test_weekly_routing_update_defaults_gate_trial_run_id_to_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worker = _RecordingWorker(
                _json_reply({"routing_table_update": '{"version": "v2", "routes": []}'})
            )
            runner, _ = _counting_runner(0.95)

            result = learner_worker.run_weekly_deep(worker, root_dir=root, now=_NOW, runner=runner)

            gate_decision = result.routing_outcomes[0].gate_decision
            self.assertIsNotNone(gate_decision)
            assert gate_decision is not None  # narrows for mypy; asserted above
            self.assertEqual(len(gate_decision.trial_records), 5)  # trials from routing-config.json
            self.assertTrue(all(record.run_id is None for record in gate_decision.trial_records))

    def test_weekly_deep_rejects_invalid_run_id_before_worker_or_runner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worker = _RecordingWorker(
                _json_reply({"routing_table_update": '{"version": "v2", "routes": []}'})
            )
            runner, calls = _counting_runner(0.95)

            with self.assertRaises(ValueError):
                learner_worker.run_weekly_deep(
                    worker,
                    root_dir=root,
                    now=_NOW,
                    runner=runner,
                    run_id="invalid run id",
                )

            self.assertEqual(len(worker.calls), 0)
            self.assertEqual(calls, [])

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

            # Multiple lessons from one run are consolidated into a single
            # `apply_memory_lesson` call, never one call per lesson — a
            # second call would silently overwrite the first's adoption.
            self.assertEqual(len(result.memory_outcomes), 1)
            self.assertEqual(result.memory_outcomes[0].status, "applied")
            self.assertEqual(len(learned_state.read_history(root)), 1)
            current = learned_state.read_current(root)
            self.assertIn("Lesson A", current.get("memory", ""))
            self.assertIn("Lesson B", current.get("memory", ""))

    def test_weekly_deep_formats_multiline_lesson_with_indented_continuation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            multiline_lesson = "Weekly first line\ncontinued weekly line"
            worker = _RecordingWorker(_json_reply({"memory_lessons": [multiline_lesson]}))
            runner, _ = _counting_runner(0.95)

            result = learner_worker.run_weekly_deep(
                worker, root_dir=root, now=_NOW, runner=runner
            )

            self.assertEqual(result.memory_outcomes[0].status, "applied")
            current = learned_state.read_current(root)
            self.assertEqual(
                current.get("memory"),
                "- Weekly first line\n  continued weekly line",
            )
            entries = risk_tiered_application._parse_memory_document(current.get("memory", ""))
            self.assertEqual(entries, (multiline_lesson,))

    def test_run_weekly_deep_across_two_runs_accumulates_memory_lessons_with_prior_light_pass(
        self,
    ) -> None:
        """Accumulation is cross-cadence, not merely cross-run of the same
        cadence: a prior session-end light pass's lesson survives a later
        weekly deep run's own lesson, merged rather than replaced (ADR
        0010, Ticket 33).
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            light_worker = _RecordingWorker(
                _json_reply({"memory_lessons": ["Session lesson"]})
            )
            learner_worker.run_session_end_light(light_worker, root_dir=root, now=_NOW)

            deep_worker = _RecordingWorker(_json_reply({"memory_lessons": ["Weekly lesson"]}))
            runner, _ = _counting_runner(0.95)
            result = learner_worker.run_weekly_deep(
                deep_worker, root_dir=root, now=_NOW, runner=runner
            )

            self.assertEqual(result.memory_outcomes[0].status, "applied")
            current = learned_state.read_current(root)
            self.assertEqual(current.get("memory"), "- Session lesson\n- Weekly lesson")
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

    def test_weekly_report_lists_no_adopted_changes_when_nothing_applied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worker = _RecordingWorker(_json_reply({}))
            runner, _ = _counting_runner(0.95)

            result = learner_worker.run_weekly_deep(
                worker, root_dir=root, now=_NOW, runner=runner
            )

            content = result.report_path.read_text(encoding="utf-8")
            adopted_section = content.split("## Changes adopted this week", 1)[1].split(
                "## Changes reverted this week", 1
            )[0]
            self.assertIn("None this week.", adopted_section)
            self.assertNotIn("Not yet wired", adopted_section)

    def test_weekly_report_lists_adopted_changes_when_proposals_applied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worker = _RecordingWorker(
                _json_reply(
                    {
                        "routing_table_update": '{"version": "v2", "routes": []}',
                        "memory_lessons": ["Lesson A"],
                    }
                )
            )
            runner, _ = _counting_runner(0.95)

            result = learner_worker.run_weekly_deep(
                worker, root_dir=root, now=_NOW, runner=runner
            )

            self.assertEqual(result.routing_outcomes[0].status, "applied")
            self.assertEqual(result.memory_outcomes[0].status, "applied")
            routing_change_id = result.routing_outcomes[0].change_id
            memory_change_id = result.memory_outcomes[0].change_id
            self.assertIsNotNone(routing_change_id)
            self.assertIsNotNone(memory_change_id)

            content = result.report_path.read_text(encoding="utf-8")
            self.assertIn(f"routing_table: change_id={routing_change_id}", content)
            self.assertIn(f"memory: change_id={memory_change_id}", content)

    def test_weekly_report_omits_no_op_change_from_adopted_section(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runner, _ = _counting_runner(0.95)
            reply = _json_reply({"memory_lessons": ["Lesson A"]})

            learner_worker.run_weekly_deep(
                _RecordingWorker(reply), root_dir=root, now=_NOW, runner=runner
            )
            second_result = learner_worker.run_weekly_deep(
                _RecordingWorker(reply), root_dir=root, now=_NOW, runner=runner
            )

            # Second run proposes the same lesson content, so
            # `_adopt_with_idempotency` reports it as `no_op` rather than
            # `applied` — Finding 2's fix gates the adopted-changes list on
            # `status == "applied"`, not the `applied` flag, which is also
            # `True` for a `no_op`.
            self.assertEqual(second_result.memory_outcomes[0].status, "no_op")

            content = second_result.report_path.read_text(encoding="utf-8")
            adopted_section = content.split("## Changes adopted this week", 1)[1].split(
                "## Changes reverted this week", 1
            )[0]
            self.assertIn("None this week.", adopted_section)
            self.assertNotIn("memory: change_id=", adopted_section)

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

    def test_window_cuts_compliance_and_replay_benchmarks_consistently(self) -> None:
        """All five journal families are window-cut the same way — including
        `compliance` and `replay_benchmarks`, which previously passed through
        unfiltered while the other three were windowed.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_compliance(root, session_id="s-in-window", timestamp=_IN_WINDOW_TS)
            _seed_compliance(
                root, session_id="s-out-of-window", timestamp="2026-07-01T00:00:00Z"
            )
            _seed_replay_benchmark(root, task_set="bench-in", timestamp=_IN_WINDOW_TS)
            _seed_replay_benchmark(
                root, task_set="bench-out", timestamp="2026-07-01T00:00:00Z"
            )
            worker = _RecordingWorker(_json_reply({}))
            runner, _ = _counting_runner(0.95)

            captured = _run_weekly_capturing_render_args(worker, root, runner)

            windowed = captured.journal
            self.assertEqual(len(windowed.compliance), 1)
            self.assertEqual(windowed.compliance[0].session_id, "s-in-window")
            self.assertEqual(len(windowed.replay_benchmarks), 1)
            self.assertEqual(windowed.replay_benchmarks[0].task_set, "bench-in")

    def test_prompt_reduces_duplicate_outcomes_positionally(self) -> None:
        """Same reduction contract as the light pass's own
        `test_prompt_reduces_duplicate_outcomes_positionally`, applied to the
        windowed outcomes the weekly deep prompt renders.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_outcome(root, task_id="task-dup", timestamp=_IN_WINDOW_TS)
            error = append_journal_record(
                OutcomeRecord(
                    task=TaskLabel.for_task("task-dup"),
                    ground_truth="tests",
                    verdict="fail",
                    timestamp="2026-08-11T09:00:00Z",
                ),
                root_dir=root,
            )
            assert error is None, error
            _seed_outcome(root, task_id="task-other", timestamp="2026-08-12T09:00:00Z")
            worker = _RecordingWorker(_json_reply({}))
            runner, _ = _counting_runner(0.95)

            _run_weekly_capturing_render_args(worker, root, runner)

            self.assertEqual(len(worker.calls), 1)
            _model, _effort, prompt = worker.calls[0]
            self.assertIn("outcomes this window: 2", prompt)
            self.assertIn("task=task-dup ground_truth=tests verdict=fail", prompt)
            self.assertNotIn("task=task-dup ground_truth=tests verdict=pass", prompt)
            self.assertIn("task=task-other ground_truth=tests verdict=pass", prompt)

    def test_weekly_deep_forwards_config_trials_and_threshold_to_the_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            custom_config = root / "routing-config.json"
            custom_config.write_text(
                json.dumps({"acceptance_gate": {"trials": 3, "score_threshold": 0.9}}),
                encoding="utf-8",
            )
            worker = _RecordingWorker(
                _json_reply({"routing_table_update": '{"version": "v2", "routes": []}'})
            )
            runner, calls = _counting_runner(0.85)  # clears 0.8 default, misses the config's 0.9

            result = learner_worker.run_weekly_deep(
                worker, root_dir=root, now=_NOW, runner=runner, config_path=custom_config
            )

            self.assertEqual(len(calls), 3)  # 3, not DEFAULT_TRIAL_COUNT's 5
            self.assertEqual(result.routing_outcomes[0].status, "rejected")  # 0.9, not 0.8

    def test_weekly_deep_baseline_uses_window_start(self) -> None:
        """`baseline_board` is computed as if `now` were `window_start`, so it
        reflects the *prior* window's activity while `current_board` reflects
        the recent one — mutating `run_weekly_deep` to pass `now` (instead of
        `window_start`) for the baseline call would collapse the two into
        identical scoreboards without any assertion here catching it unless
        the two windows are seeded with genuinely different evidence.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Baseline window: (2026-08-01T12:00:00Z, 2026-08-08T12:00:00Z].
            _seed_replay_benchmark(
                root, task_set="bench-baseline", score=0.2, timestamp="2026-08-03T00:00:00Z"
            )
            # Current window: (2026-08-08T12:00:00Z, 2026-08-15T12:00:00Z] — _IN_WINDOW_TS.
            _seed_replay_benchmark(
                root, task_set="bench-current", score=0.9, timestamp=_IN_WINDOW_TS
            )
            worker = _RecordingWorker(_json_reply({}))
            runner, _ = _counting_runner(0.95)

            captured = _run_weekly_capturing_render_args(worker, root, runner)

            changes_by_name = {c.name: c for c in captured.comparison.changes}
            benchmark_change = changes_by_name["mean_benchmark_score"]
            baseline_metric = benchmark_change.baseline
            current_metric = benchmark_change.current
            self.assertIsInstance(baseline_metric, learner_worker.learning_scoreboard.MetricValue)
            self.assertIsInstance(current_metric, learner_worker.learning_scoreboard.MetricValue)
            assert isinstance(baseline_metric, learner_worker.learning_scoreboard.MetricValue)
            assert isinstance(current_metric, learner_worker.learning_scoreboard.MetricValue)
            self.assertAlmostEqual(baseline_metric.value, 0.2)
            self.assertAlmostEqual(current_metric.value, 0.9)

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

    def test_weekly_deep_with_run_id_seeds_change_id_without_narrowing_windowed_journal(
        self,
    ) -> None:
        """`run_id` seeds this run's own `change_id`/`proposal_id` derivation,
        but it must not narrow which journal evidence the retrospective
        reads — the batch retrospective's whole point is to read every
        task's evidence for the window, not one run's (see `run_weekly_deep`'s
        docstring and the gate-forwarding tests).
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_worker_execution(root, task_id="task-other", run_id="run-other-xyz")
            worker = _RecordingWorker(
                _json_reply({"memory_lessons": ["Lesson A"]})
            )
            runner, _ = _counting_runner(0.95)

            captured = _run_weekly_capturing_render_args(
                worker, root, runner, run_id="run-weekly-abc"
            )

            change_id = captured.result.memory_outcomes[0].change_id
            self.assertIsNotNone(change_id)
            assert change_id is not None  # narrows for mypy; asserted above
            self.assertIn("run-weekly-abc", change_id)
            self.assertIn(
                "run-other-xyz",
                [r.run_id for r in captured.journal.worker_executions],
            )


class WeeklyDeepRevertTests(unittest.TestCase):
    """Ticket 21 slice 2: `run_weekly_deep` calls
    `risk_tiered_application.revert_attributable_regression` unconditionally
    after computing the scoreboard comparison, reports the outcome as its
    own weekly-report section, and refuses to let this same run's own
    proposal readopt whatever content the revert just undid.
    """

    # Baseline window (2026-08-01T12:00:00Z, 2026-08-08T12:00:00Z].
    _BASELINE_TS = "2026-08-03T00:00:00Z"

    def test_attributable_regression_reverts_and_reports_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            risk_tiered_application.apply_memory_lesson(
                "- Lesson A",
                root_dir=root,
                now=datetime(2026, 8, 1, 0, 0, 0, tzinfo=timezone.utc),
                change_id="adopt-1",
            )
            risk_tiered_application.apply_memory_lesson(
                "- Lesson B",
                root_dir=root,
                now=datetime(2026, 8, 12, 0, 0, 0, tzinfo=timezone.utc),
                change_id="adopt-2",
            )
            _seed_replay_benchmark(root, task_set="bench", score=0.9, timestamp=self._BASELINE_TS)
            _seed_replay_benchmark(root, task_set="bench", score=0.1, timestamp=_IN_WINDOW_TS)
            worker = _RecordingWorker(_json_reply({}))
            runner, _ = _counting_runner(0.95)

            result = learner_worker.run_weekly_deep(
                worker, root_dir=root, now=_NOW, runner=runner
            )

            self.assertEqual(result.revert_outcome.status, "reverted")
            self.assertEqual(
                result.revert_outcome.regressed_metrics, ("mean_benchmark_score",)
            )
            self.assertEqual(result.revert_outcome.reverted_change_id, "adopt-2")

            # The reverted state matches the pre-adoption version exactly.
            current = learned_state.read_current(root)
            self.assertEqual(current.get("memory"), "- Lesson A")

            content = result.report_path.read_text(encoding="utf-8")
            reverted_section = content.split("## Changes reverted this week", 1)[1].split(
                "## Budget degradations", 1
            )[0]
            self.assertIn("memory: change_id=adopt-2", reverted_section)
            self.assertIn("mean_benchmark_score", reverted_section)

    def test_unattributable_regression_reverts_nothing_and_reports_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # The only live adoption is well outside the trailing window, so
            # the regression cannot be attributed to it.
            risk_tiered_application.apply_memory_lesson(
                "- Lesson A",
                root_dir=root,
                now=datetime(2026, 7, 1, 0, 0, 0, tzinfo=timezone.utc),
                change_id="adopt-old",
            )
            _seed_replay_benchmark(root, task_set="bench", score=0.9, timestamp=self._BASELINE_TS)
            _seed_replay_benchmark(root, task_set="bench", score=0.1, timestamp=_IN_WINDOW_TS)
            worker = _RecordingWorker(_json_reply({}))
            runner, _ = _counting_runner(0.95)

            result = learner_worker.run_weekly_deep(
                worker, root_dir=root, now=_NOW, runner=runner
            )

            self.assertEqual(result.revert_outcome.status, "unattributable")
            self.assertEqual(
                result.revert_outcome.regressed_metrics, ("mean_benchmark_score",)
            )
            self.assertIsNone(result.revert_outcome.reverted_change_id)

            # Nothing was rolled back: history is unchanged.
            self.assertEqual(len(learned_state.read_history(root)), 1)

            content = result.report_path.read_text(encoding="utf-8")
            reverted_section = content.split("## Changes reverted this week", 1)[1].split(
                "## Budget degradations", 1
            )[0]
            self.assertIn("none:", reverted_section)
            self.assertIn("mean_benchmark_score", reverted_section)

    def test_no_regression_reports_empty_reverted_section(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worker = _RecordingWorker(_json_reply({}))
            runner, _ = _counting_runner(0.95)

            result = learner_worker.run_weekly_deep(
                worker, root_dir=root, now=_NOW, runner=runner
            )

            self.assertEqual(result.revert_outcome.status, "no_regression")
            self.assertEqual(result.revert_outcome.regressed_metrics, ())

            content = result.report_path.read_text(encoding="utf-8")
            reverted_section = content.split("## Changes reverted this week", 1)[1].split(
                "## Budget degradations", 1
            )[0]
            self.assertIn("None this week.", reverted_section)

    def test_reverted_content_is_not_readopted_by_the_same_run(self) -> None:
        """The anti-flapping guard: a proposal identical to the content this
        run's own revert just undid must not be re-applied — a `rejected`
        `TierOutcome` is returned instead, and the store is left exactly as
        the revert left it.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            risk_tiered_application.apply_memory_lesson(
                "- Lesson A",
                root_dir=root,
                now=datetime(2026, 8, 1, 0, 0, 0, tzinfo=timezone.utc),
                change_id="adopt-1",
            )
            risk_tiered_application.apply_memory_lesson(
                "- Lesson B",
                root_dir=root,
                now=datetime(2026, 8, 12, 0, 0, 0, tzinfo=timezone.utc),
                change_id="adopt-2",
            )
            _seed_replay_benchmark(root, task_set="bench", score=0.9, timestamp=self._BASELINE_TS)
            _seed_replay_benchmark(root, task_set="bench", score=0.1, timestamp=_IN_WINDOW_TS)
            # The worker proposes only "Lesson B" — its raw consolidated
            # content is "- Lesson B", which is *not* byte-for-byte what
            # `adopt-2` put in the store (that was the merged "- Lesson A\n-
            # Lesson B"). The guard must catch this anyway: `apply_memory_
            # lesson` merges "- Lesson B" back into post-revert "- Lesson A"
            # and reconstructs exactly the just-reverted candidate, which is
            # what `reject_if_candidate_digest` actually compares against —
            # see `test_memory_anti_flapping_guard_rejects_via_reject_if_
            # candidate_digest` below for the same property in isolation.
            worker = _RecordingWorker(_json_reply({"memory_lessons": ["Lesson B"]}))
            runner, _ = _counting_runner(0.95)

            result = learner_worker.run_weekly_deep(
                worker, root_dir=root, now=_NOW, runner=runner
            )

            self.assertEqual(result.revert_outcome.status, "reverted")
            self.assertEqual(len(result.memory_outcomes), 1)
            self.assertEqual(result.memory_outcomes[0].status, "rejected")
            self.assertFalse(result.memory_outcomes[0].applied)
            self.assertIsNotNone(result.memory_outcomes[0].reason)
            assert result.memory_outcomes[0].reason is not None  # narrows for mypy
            self.assertIn("anti-flapping", result.memory_outcomes[0].reason)

            # The store still holds exactly what the revert restored — the
            # rejected re-proposal never touched it.
            current = learned_state.read_current(root)
            self.assertEqual(current.get("memory"), "- Lesson A")
            self.assertEqual(len(learned_state.read_history(root)), 3)  # 2 adopts + 1 rollback

            content = result.report_path.read_text(encoding="utf-8")
            adopted_section = content.split("## Changes adopted this week", 1)[1].split(
                "## Changes reverted this week", 1
            )[0]
            self.assertNotIn("memory: change_id=", adopted_section)

    def test_different_content_still_applies_after_an_unrelated_revert(self) -> None:
        """The anti-flapping guard is content-specific, not a blanket freeze
        on the reverted document: a genuinely different proposal for the
        same document this run just reverted still applies normally.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            risk_tiered_application.apply_memory_lesson(
                "- Lesson A",
                root_dir=root,
                now=datetime(2026, 8, 1, 0, 0, 0, tzinfo=timezone.utc),
                change_id="adopt-1",
            )
            risk_tiered_application.apply_memory_lesson(
                "- Lesson B",
                root_dir=root,
                now=datetime(2026, 8, 12, 0, 0, 0, tzinfo=timezone.utc),
                change_id="adopt-2",
            )
            _seed_replay_benchmark(root, task_set="bench", score=0.9, timestamp=self._BASELINE_TS)
            _seed_replay_benchmark(root, task_set="bench", score=0.1, timestamp=_IN_WINDOW_TS)
            worker = _RecordingWorker(_json_reply({"memory_lessons": ["Lesson C"]}))
            runner, _ = _counting_runner(0.95)

            result = learner_worker.run_weekly_deep(
                worker, root_dir=root, now=_NOW, runner=runner
            )

            self.assertEqual(result.revert_outcome.status, "reverted")
            self.assertEqual(result.memory_outcomes[0].status, "applied")
            # Accumulated, not a wholesale replacement: post-revert memory
            # is "- Lesson A", and this run's genuinely different "Lesson C"
            # merges into it rather than overwriting it.
            current = learned_state.read_current(root)
            self.assertEqual(current.get("memory"), "- Lesson A\n- Lesson C")

    def test_memory_anti_flapping_guard_rejects_via_reject_if_candidate_digest(self) -> None:
        """Written to fail against a naive raw-content digest comparison,
        proving the guard exercises the actual *merged* candidate rather
        than the caller's raw `content` argument (ADR 0010). Existing
        memory already holds `"- Lesson A"`; a caller proposing only
        `"Lesson B"` has raw content `"- Lesson B"`, whose digest never
        equals `digest("- Lesson A\\n- Lesson B")` — the actual
        just-reverted content. Only a comparison against the merged result
        (what `apply_memory_lesson` is actually about to adopt) catches it.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            risk_tiered_application.apply_memory_lesson(
                "Lesson A", root_dir=root, now=_NOW, change_id="adopt-1"
            )
            reverted_before_digest = risk_tiered_application._digest("- Lesson A\n- Lesson B")
            naive_raw_digest = risk_tiered_application._digest("- Lesson B")
            self.assertNotEqual(
                naive_raw_digest,
                reverted_before_digest,
                "the scenario only proves the point if a raw comparison would have missed it",
            )

            outcome = risk_tiered_application.apply_memory_lesson(
                "Lesson B",
                root_dir=root,
                now=datetime(2026, 8, 15, 13, 0, 0, tzinfo=timezone.utc),
                reject_if_candidate_digest=reverted_before_digest,
            )

            self.assertEqual(outcome.status, "rejected")
            self.assertFalse(outcome.applied)
            self.assertIn("anti-flapping", outcome.reason or "")
            current = learned_state.read_current(root)
            self.assertEqual(current.get("memory"), "- Lesson A")


_PROPOSAL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class ChangeIdTests(unittest.TestCase):
    def test_change_id_sanitization_produces_valid_proposal_id(self) -> None:
        """A seed carrying characters `learned_state`/`risk_tiered_application`'s
        own validators would reject (`/`, `#`, `@`) must still yield a
        `change_id` matching their shared pattern
        (`^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$`) — `_change_id` sanitizes
        rather than trusting the seed.
        """
        change_id = learner_worker._change_id(
            "learner-light", seed="invalid/chars#with@symbols", index=0
        )
        self.assertRegex(change_id, _PROPOSAL_ID_RE)

    def test_long_seed_never_truncates_the_trailing_index(self) -> None:
        long_seed = "s" * 150
        first = learner_worker._change_id("learner-deep-memory", seed=long_seed, index=0)
        second = learner_worker._change_id("learner-deep-memory", seed=long_seed, index=1)

        self.assertLessEqual(len(first), 128)
        self.assertLessEqual(len(second), 128)
        self.assertTrue(first.endswith("-0"))
        self.assertTrue(second.endswith("-1"))
        self.assertNotEqual(first, second)


class ExtractJsonObjectTests(unittest.TestCase):
    def test_extracts_unfenced_json(self) -> None:
        payload = learner_worker._extract_json_object('{"memory_lessons": ["a"]}')
        self.assertEqual(payload, {"memory_lessons": ["a"]})

    def test_extracts_json_substring_with_surrounding_prose(self) -> None:
        response = 'Sure thing! {"memory_lessons": ["a"]} Let me know if you need more.'
        payload = learner_worker._extract_json_object(response)
        self.assertEqual(payload, {"memory_lessons": ["a"]})

    def test_extract_json_object_with_code_fence_and_surrounding_braces(self) -> None:
        """When a worker produces a markdown response with a fenced JSON code
        block, stray braces in the surrounding prose must not be what gets
        returned. Without the fence branch, the candidate spanning from the
        first `{` to the last `}` would span non-JSON prose across the code
        block and fail to parse as valid JSON, returning `None` rather than
        the fenced payload.
        """
        response = (
            "Here's a random aside about {curly braces} in prose, "
            "not JSON at all.\n"
            "```json\n"
            '{"memory_lessons": ["from the fence"]}\n'
            "```\n"
            "Hope that helps!"
        )
        payload = learner_worker._extract_json_object(response)
        self.assertEqual(payload, {"memory_lessons": ["from the fence"]})


class TestSeamAndSeparationTests(unittest.TestCase):
    """Prompt-structure assertions and proposer/approver separation."""

    def test_invoke_worker_called_with_expected_model_and_effort_light(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worker = _RecordingWorker(_json_reply({"memory_lessons": []}))
            captured = _run_light_capturing_render_args(worker, root)

            self.assertEqual(len(worker.calls), 1)
            model, effort, _prompt = worker.calls[0]
            self.assertEqual(model, learner_worker._SESSION_LIGHT_MODEL)
            self.assertEqual(effort, learner_worker._SESSION_LIGHT_EFFORT)
            self.assertEqual(captured.now, _NOW)

    def test_invoke_worker_called_with_expected_prompt_structure_deep(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_worker_execution(root, task_id="task-1")
            worker = _RecordingWorker(_json_reply({}))
            runner, _ = _counting_runner(0.95)
            captured = _run_weekly_capturing_render_args(worker, root, runner)

            self.assertEqual(len(worker.calls), 1)
            model, effort, _prompt = worker.calls[0]
            self.assertEqual(model, learner_worker._WEEKLY_DEEP_MODEL)
            self.assertEqual(effort, learner_worker._WEEKLY_DEEP_EFFORT)
            self.assertEqual(captured.now, _NOW)
            self.assertEqual(captured.window_days, 7)
            self.assertIn(
                "violations_per_session",
                [change.name for change in captured.comparison.changes],
            )
            self.assertEqual(
                [r.task.task_id for r in captured.journal.worker_executions], ["task-1"]
            )

    def test_module_never_imports_learned_state(self) -> None:
        """The learner proposes; it never adopts. Enforced structurally: this
        module cannot call `learned_state.adopt` if it never imports
        `learned_state` at all — every mutation must instead flow through
        `risk_tiered_application`'s tiering.
        """
        source = Path(learner_worker.__file__).read_text(encoding="utf-8")
        imported_names = _imported_names(source)

        self.assertNotIn("learned_state", imported_names)
        self.assertNotIn("adopt(", source)

    def test_the_learned_state_import_guard_catches_relative_and_absolute_imports(self) -> None:
        """Regression for the AST walk above: it must flag `learned_state`
        whether it arrives via a plain `import`, a relative `from . import`,
        a relative `from .learned_state import`, or an absolute
        `from learned_state import` — the walk collects both `node.module`
        and each `alias.name` from every `ast.ImportFrom` node.
        """

        self.assertIn("learned_state", _imported_names("import learned_state"))
        self.assertIn("learned_state", _imported_names("from . import learned_state"))
        self.assertIn(
            "learned_state", _imported_names("from .learned_state import adopt")
        )
        self.assertIn(
            "learned_state", _imported_names("from learned_state import adopt")
        )

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
