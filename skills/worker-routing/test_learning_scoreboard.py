#!/usr/bin/env python3
"""Unit tests for `learning_journal.read_journal` (spec 0004 ticket 16, stage 1).

Covers Slices 1-3 of the ticket's TDD plan: the reader exists and survives a
missing/empty journal, the round trip through the real writers is lossless,
and the reader tolerates a damaged or forward-shifted stream rather than
raising. Slice 3's test 16 (`compute_scoreboard`/`read_scoreboard` does not
raise over such a line) is stage 2's — `learning_scoreboard.py` does not
exist yet — and is deliberately not written here.

Modules are loaded by path with `importlib.util.spec_from_file_location`,
the pattern `test_production_invoker.py` already uses: these files are not a
package, and `learning_outcomes.py`'s bare `import learning_journal` only
resolves because `learning_journal` is registered in `sys.modules` first.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from typing import Any

MODULE_PATH = Path(__file__).with_name("learning_journal.py")
LEARNING_OUTCOMES_PATH = Path(__file__).with_name("learning_outcomes.py")

learning_journal_spec = importlib.util.spec_from_file_location("learning_journal", MODULE_PATH)
assert learning_journal_spec is not None and learning_journal_spec.loader is not None
learning_journal = importlib.util.module_from_spec(learning_journal_spec)
sys.modules["learning_journal"] = learning_journal
learning_journal_spec.loader.exec_module(learning_journal)

learning_outcomes_spec = importlib.util.spec_from_file_location(
    "learning_outcomes", LEARNING_OUTCOMES_PATH
)
assert learning_outcomes_spec is not None and learning_outcomes_spec.loader is not None
learning_outcomes = importlib.util.module_from_spec(learning_outcomes_spec)
learning_outcomes_spec.loader.exec_module(learning_outcomes)


def _worker_record(task_id: str, *, timestamp: str) -> Any:
    """A minimal, otherwise-valid `WorkerExecutionRecord` for tolerance tests."""
    return learning_journal.WorkerExecutionRecord(
        task=learning_journal.TaskLabel.for_task(task_id),
        duration_ms=1,
        cost_estimate_usd=0.0,
        success=True,
        retry_count=0,
        effort="low",
        model_id="claude-sonnet-5",
        model_family="claude",
        timestamp=timestamp,
    )


def _append_raw_line(root: Path, text: str) -> None:
    """Append one hand-written line to the journal — bypassing every writer.

    For the states a real writer cannot produce: a corrupt line, an unknown
    `kind`, a field from the future, a calendar-invalid timestamp.
    """
    path = learning_journal.journal_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as stream:
        stream.write(text + "\n")


class ReadJournalEmptyTests(unittest.TestCase):
    """Slice 1 — the reader exists and survives nothing."""

    def test_read_journal_of_a_missing_journal_is_an_empty_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            read = learning_journal.read_journal(root)

        self.assertEqual(read.worker_executions, ())
        self.assertEqual(read.outcomes, ())
        self.assertEqual(read.dialogues, ())
        self.assertEqual(read.compliance, ())
        self.assertEqual(read.unreadable_lines, 0)
        self.assertEqual(read.unknown_kind_lines, 0)

    def test_read_journal_of_an_empty_file_is_an_empty_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = learning_journal.journal_path(root)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()

            read = learning_journal.read_journal(root)

        self.assertEqual(read.worker_executions, ())
        self.assertEqual(read.outcomes, ())
        self.assertEqual(read.dialogues, ())
        self.assertEqual(read.compliance, ())
        self.assertEqual(read.unreadable_lines, 0)
        self.assertEqual(read.unknown_kind_lines, 0)


class ReadJournalRoundTripTests(unittest.TestCase):
    """Slice 2 — the round trip is the anti-drift lock.

    One record per family through the real writers, re-hydrated and compared
    for full equality against the object that was written — not just field
    subsets, so a reader that silently dropped or mistranslated one field
    cannot pass.
    """

    def test_every_family_written_by_the_real_writers_reads_back_equal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            worker_record = learning_journal.WorkerExecutionRecord(
                task=learning_journal.TaskLabel.for_task("task-worker", task_type="bugfix"),
                duration_ms=1500,
                cost_estimate_usd=0.42,
                success=True,
                retry_count=1,
                effort="high",
                model_id="claude-sonnet-5",
                model_family="claude",
                run_id="run-worker-1",
                timestamp="2026-01-01T00:00:00Z",
            )
            self.assertIsNone(learning_journal.append_journal_record(worker_record, root_dir=root))

            dialogue_record = learning_journal.DialogueQualityRecord(
                task=learning_journal.TaskLabel.for_task("task-dialogue"),
                occasion="plan-review",
                topology="pair",
                rounds=(
                    learning_journal.DialogueRound(verdict="approved", engagement_count=3),
                    learning_journal.DialogueRound(verdict="revise", engagement_count=1),
                ),
                canaries_planted=0,
                canaries_caught=0,
                degraded=False,
                independent=True,
                run_id="run-dialogue-1",
                timestamp="2026-01-01T00:00:00Z",
            )
            self.assertIsNone(
                learning_journal.append_journal_record(dialogue_record, root_dir=root)
            )

            compliance_record = learning_journal.ComplianceRecord(
                session_id="session-compliance-1",
                total_writes=10,
                code_writes=2,
                routing_declarations=8,
                worker_calls=3,
                violation_count=1,
                declaration_drift_count=0,
                calibration_markers=1,
                code_write_count=2,
                issue_codes=("DEC-01", "LOG-01"),
                run_id="run-compliance-1",
                session_last_activity="2026-01-01T00:00:00Z",
                timestamp="2026-01-01T00:00:00Z",
            )
            self.assertIsNone(
                learning_journal.append_journal_record(compliance_record, root_dir=root)
            )

            # Routed through the real production entry point rather than a
            # hand-built OutcomeRecord, so a real caller is in the path.
            error = learning_outcomes.record_test_result(
                "task-outcome", passed=True, root_dir=root, run_id="run-outcome-1"
            )
            self.assertIsNone(error)

            raw_lines = [
                json.loads(line)
                for line in learning_journal.journal_path(root).read_text().splitlines()
                if line.strip()
            ]
            outcome_wire = next(line for line in raw_lines if line["kind"] == "outcome")
            # `record_test_result` stamps its own timestamp; read the wire
            # form back for the exact value rather than re-deriving one that
            # could race the writer's own clock.
            outcome_record = learning_journal.OutcomeRecord(
                task=learning_journal.TaskLabel.for_task("task-outcome"),
                ground_truth="tests",
                verdict="pass",
                run_id="run-outcome-1",
                timestamp=outcome_wire["timestamp"],
            )

            read = learning_journal.read_journal(root)

        # Sizes asserted before the records themselves: a reader that
        # returned nothing must not be able to pass by the equality checks
        # below simply not running.
        self.assertEqual(len(read.worker_executions), 1)
        self.assertEqual(len(read.outcomes), 1)
        self.assertEqual(len(read.dialogues), 1)
        self.assertEqual(len(read.compliance), 1)
        self.assertEqual(read.unreadable_lines, 0)
        self.assertEqual(read.unknown_kind_lines, 0)

        self.assertEqual(read.worker_executions[0], worker_record)
        self.assertEqual(read.outcomes[0], outcome_record)
        self.assertEqual(read.dialogues[0], dialogue_record)
        self.assertEqual(read.compliance[0], compliance_record)

    def test_an_absent_run_id_reads_back_as_none_never_as_a_shared_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = _worker_record("task-no-run-1", timestamp="2026-01-01T00:00:00Z")
            second = _worker_record("task-no-run-2", timestamp="2026-01-01T00:00:01Z")
            self.assertIsNone(learning_journal.append_journal_record(first, root_dir=root))
            self.assertIsNone(learning_journal.append_journal_record(second, root_dir=root))

            read = learning_journal.read_journal(root)

        self.assertEqual(len(read.worker_executions), 2)
        self.assertIsNone(read.worker_executions[0].run_id)
        self.assertIsNone(read.worker_executions[1].run_id)
        # Two distinct records, not one shared run: an absent run_id must
        # never make the reader collapse or alias them.
        self.assertNotEqual(
            read.worker_executions[0].task.task_id, read.worker_executions[1].task.task_id
        )

    def test_an_untagged_task_label_reads_back_untagged_and_a_tagged_one_tagged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            untagged = _worker_record("task-untagged", timestamp="2026-01-01T00:00:00Z")
            tagged = learning_journal.WorkerExecutionRecord(
                task=learning_journal.TaskLabel.for_task("task-tagged", task_type="bugfix"),
                duration_ms=1,
                cost_estimate_usd=0.0,
                success=True,
                retry_count=0,
                effort="low",
                model_id="claude-sonnet-5",
                model_family="claude",
                timestamp="2026-01-01T00:00:01Z",
            )
            self.assertIsNone(learning_journal.append_journal_record(untagged, root_dir=root))
            self.assertIsNone(learning_journal.append_journal_record(tagged, root_dir=root))

            read = learning_journal.read_journal(root)

        self.assertEqual(len(read.worker_executions), 2)
        self.assertIsNone(read.worker_executions[0].task.task_type)
        self.assertEqual(read.worker_executions[1].task.task_type, "bugfix")

    def test_rounds_read_back_as_dialogue_round_objects_and_rounds_run_does_not_break_construction(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record = learning_journal.DialogueQualityRecord(
                task=learning_journal.TaskLabel.for_task("task-rounds"),
                occasion="ambiguity",
                topology="panel",
                rounds=(
                    learning_journal.DialogueRound(verdict="approved", engagement_count=2),
                    learning_journal.DialogueRound(verdict="approved", engagement_count=4),
                ),
                timestamp="2026-01-01T00:00:00Z",
            )
            self.assertIsNone(learning_journal.append_journal_record(record, root_dir=root))

            read = learning_journal.read_journal(root)

        self.assertEqual(len(read.dialogues), 1)
        rehydrated = read.dialogues[0]
        self.assertEqual(rehydrated.rounds, record.rounds)
        for round_ in rehydrated.rounds:
            self.assertIsInstance(round_, learning_journal.DialogueRound)
        # `rounds_run` is on the wire (to_mapping writes it explicitly) but
        # is a derived property, not a field — construction must not choke
        # on it, and the reader must not lose it either.
        self.assertEqual(rehydrated.rounds_run, 2)


class ParseWireTimestampTests(unittest.TestCase):
    def test_parse_wire_timestamp_returns_an_aware_utc_datetime(self) -> None:
        parsed = learning_journal.parse_wire_timestamp("2026-03-05T12:30:00Z")

        self.assertEqual(parsed.year, 2026)
        self.assertIsNotNone(parsed.tzinfo)
        self.assertEqual(parsed.utcoffset(), timedelta(0))

    def test_parse_wire_timestamp_raises_on_a_calendar_invalid_value(self) -> None:
        with self.assertRaises(ValueError):
            learning_journal.parse_wire_timestamp("2026-99-99T99:99:99Z")


class ReadJournalToleranceTests(unittest.TestCase):
    """Slice 3 — the reader's tolerance for a damaged or forward-shifted stream.

    Test 16 of the plan's Slice 3 (`compute_scoreboard` does not raise over
    such a line) is stage 2's and is not written here: it needs
    `learning_scoreboard.py`, which this stage does not build.
    """

    def test_a_malformed_line_is_skipped_and_its_neighbours_survive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = _worker_record("task-good-1", timestamp="2026-01-01T00:00:00Z")
            second = _worker_record("task-good-2", timestamp="2026-01-01T00:00:01Z")
            self.assertIsNone(learning_journal.append_journal_record(first, root_dir=root))
            _append_raw_line(root, "not json at all {{{")
            self.assertIsNone(learning_journal.append_journal_record(second, root_dir=root))

            read = learning_journal.read_journal(root)

        # Both good records present first, so a reader that dropped
        # everything after the corrupt line cannot pass.
        self.assertEqual(len(read.worker_executions), 2)
        self.assertEqual(
            {record.task.task_id for record in read.worker_executions},
            {"task-good-1", "task-good-2"},
        )
        self.assertEqual(read.unreadable_lines, 1)

    def test_a_line_that_is_valid_json_but_not_an_object_is_unreadable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _append_raw_line(root, json.dumps([1, 2, 3]))

            read = learning_journal.read_journal(root)

        self.assertEqual(len(read.worker_executions), 0)
        self.assertEqual(len(read.outcomes), 0)
        self.assertEqual(len(read.dialogues), 0)
        self.assertEqual(len(read.compliance), 0)
        self.assertEqual(read.unreadable_lines, 1)
        self.assertEqual(read.unknown_kind_lines, 0)

    def test_a_record_of_an_unknown_kind_is_counted_apart_from_a_malformed_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _append_raw_line(
                root, json.dumps({"kind": "benchmark_score", "task_id": "task-future-kind"})
            )

            read = learning_journal.read_journal(root)

        # Two different counters wearing two different names, not one
        # counter wearing two.
        self.assertEqual(read.unknown_kind_lines, 1)
        self.assertEqual(read.unreadable_lines, 0)

    def test_an_unrecognised_key_on_a_known_kind_still_reads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wire = {
                "kind": "worker_execution",
                "task_id": "task-future-field",
                "sensitivity_halted": False,
                "duration_ms": 500,
                "cost_estimate_usd": 0.05,
                "success": True,
                "retry_count": 0,
                "effort": "medium",
                "model_id": "claude-sonnet-5",
                "model_family": "claude",
                "timestamp": "2026-01-01T00:00:00Z",
                "future_field": "from ticket 26 or 27",
            }
            _append_raw_line(root, json.dumps(wire))

            read = learning_journal.read_journal(root)

        self.assertEqual(len(read.worker_executions), 1)
        self.assertEqual(read.worker_executions[0].task.task_id, "task-future-field")
        self.assertEqual(read.unreadable_lines, 0)
        self.assertEqual(read.unknown_kind_lines, 0)

    def test_a_known_kind_missing_a_required_key_is_unreadable_not_a_crash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wire = {
                "kind": "worker_execution",
                "task_id": "task-missing-field",
                "sensitivity_halted": False,
                # duration_ms is missing.
                "cost_estimate_usd": 0.05,
                "success": True,
                "retry_count": 0,
                "effort": "medium",
                "model_id": "claude-sonnet-5",
                "model_family": "claude",
                "timestamp": "2026-01-01T00:00:00Z",
            }
            _append_raw_line(root, json.dumps(wire))

            read = learning_journal.read_journal(root)

        self.assertEqual(len(read.worker_executions), 0)
        self.assertEqual(read.unreadable_lines, 1)

    def test_records_are_returned_in_file_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for index in range(3):
                record = _worker_record(
                    f"task-order-{index}", timestamp=f"2026-01-01T00:00:0{index}Z"
                )
                self.assertIsNone(learning_journal.append_journal_record(record, root_dir=root))

            read = learning_journal.read_journal(root)

        self.assertEqual(
            [record.task.task_id for record in read.worker_executions],
            ["task-order-0", "task-order-1", "task-order-2"],
        )

    def test_a_timestamp_that_passes_the_regex_but_names_no_instant_is_unreadable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            valid = _worker_record("task-valid-neighbour", timestamp="2026-01-01T00:00:00Z")
            self.assertIsNone(learning_journal.append_journal_record(valid, root_dir=root))
            wire = {
                "kind": "worker_execution",
                "task_id": "task-bad-timestamp",
                "sensitivity_halted": False,
                "duration_ms": 1,
                "cost_estimate_usd": 0.0,
                "success": True,
                "retry_count": 0,
                "effort": "low",
                "model_id": "claude-sonnet-5",
                "model_family": "claude",
                "timestamp": "2026-99-99T99:99:99Z",
            }
            _append_raw_line(root, json.dumps(wire))

            read = learning_journal.read_journal(root)

        # The skipped line's neighbour still reads — this is the crash path
        # objection 5 named, exercised without raising.
        self.assertEqual(len(read.worker_executions), 1)
        self.assertEqual(read.worker_executions[0].task.task_id, "task-valid-neighbour")
        self.assertEqual(read.unreadable_lines, 1)

    def test_a_compliance_record_whose_session_last_activity_names_no_instant_is_unreadable(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wire = {
                "kind": "compliance",
                "session_id": "session-bad-activity",
                "total_writes": 1,
                "code_writes": 0,
                "routing_declarations": 1,
                "worker_calls": 0,
                "violation_count": 0,
                "declaration_drift_count": 0,
                "calibration_markers": 0,
                "code_write_count": 0,
                "timestamp": "2026-01-01T00:00:00Z",
                "session_last_activity": "2026-99-99T99:99:99Z",
            }
            _append_raw_line(root, json.dumps(wire))

            read = learning_journal.read_journal(root)

        self.assertEqual(len(read.compliance), 0)
        self.assertEqual(read.unreadable_lines, 1)


if __name__ == "__main__":
    unittest.main()
