#!/usr/bin/env python3
"""Unit tests for `learning_journal.read_journal` (spec 0004 ticket 16, stage 1).

Covers Slices 1-3 of the ticket's TDD plan: the reader exists and survives a
missing/empty journal, the round trip through the real writers is lossless,
and the reader tolerates a damaged or forward-shifted stream rather than
raising. Slice 3's test 16 (`compute_scoreboard`/`read_scoreboard` does not
raise over such a line) is stage 2's — `learning_scoreboard.py` does not
exist yet — and is deliberately not written here.
"""
from __future__ import annotations

import ast
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest import mock

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parent))

if __package__:
    from . import learning_journal, learning_outcomes, learning_scoreboard
else:
    import learning_journal  # type: ignore[no-redef]
    import learning_outcomes  # type: ignore[no-redef]
    import learning_scoreboard  # type: ignore[no-redef]

LEARNING_SCOREBOARD_PATH = Path(__file__).with_name("learning_scoreboard.py")

# A shared, timezone-aware `now` for every stage-2 test below — never used to
# derive a live clock reading, only as a fixed injected value.
_NOW = datetime(2026, 1, 8, tzinfo=timezone.utc)


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


def _append_raw_bytes(root: Path, data: bytes) -> None:
    """Append raw bytes to the journal — for a line that is not valid UTF-8."""
    path = learning_journal.journal_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "ab") as stream:
        stream.write(data)


# Hand-built wire mappings, one per family, matching exactly what each
# family's real `to_mapping()` emits for a minimal record — every key a real
# writer always includes, and none of the ones it includes only when set
# (`run_id`, `task_type`, `session_last_activity`). `score` is optional by
# type too, but a successful trial always carries one, so the
# replay-benchmark fixture below does include it. Used as a base a test
# mutates (deletes one key, or sets an optional one) rather than constructing
# a real record and re-deriving its wire form, since these tests are about
# the wire contract itself, not about round-tripping a Python object.
def _valid_worker_execution_wire(task_id: str, *, timestamp: str) -> dict[str, Any]:
    return {
        "kind": "worker_execution",
        "task_id": task_id,
        "sensitivity_halted": False,
        "duration_ms": 500,
        "cost_estimate_usd": 0.05,
        "success": True,
        "retry_count": 0,
        "effort": "medium",
        "model_id": "claude-sonnet-5",
        "model_family": "claude",
        "timestamp": timestamp,
    }


def _valid_outcome_wire(task_id: str, *, timestamp: str) -> dict[str, Any]:
    return {
        "kind": "outcome",
        "task_id": task_id,
        "sensitivity_halted": False,
        "ground_truth": "tests",
        "verdict": "pass",
        "timestamp": timestamp,
    }


def _valid_dialogue_quality_wire(task_id: str, *, timestamp: str) -> dict[str, Any]:
    # `rounds_run` must agree with `len(rounds)` — it is now a required,
    # cross-validated wire key (learning_journal.py `_rehydrate_dialogue_quality`).
    # A test that overrides `rounds` on the dict this returns must also
    # override `rounds_run` to match, or the line becomes unreadable for a
    # reason unrelated to what that test means to exercise.
    return {
        "kind": "dialogue_quality",
        "task_id": task_id,
        "sensitivity_halted": False,
        "occasion": "ambiguity",
        "topology": "pair",
        "rounds": [{"verdict": "approved", "engagement_count": 1}],
        "rounds_run": 1,
        "canaries_planted": 0,
        "canaries_caught": 0,
        "degraded": False,
        "independent": True,
        "timestamp": timestamp,
    }


def _valid_replay_benchmark_wire(task_set: str, *, timestamp: str) -> dict[str, Any]:
    return {
        "kind": "replay_benchmark",
        "task_set": task_set,
        "success": True,
        "score": 0.75,
        "timestamp": timestamp,
    }


def _valid_compliance_wire(session_id: str, *, timestamp: str) -> dict[str, Any]:
    return {
        "kind": "compliance",
        "session_id": session_id,
        "total_writes": 1,
        "code_writes": 0,
        "routing_declarations": 1,
        "worker_calls": 0,
        "violation_count": 0,
        "declaration_drift_count": 0,
        "calibration_markers": 0,
        "code_write_count": 0,
        "issue_codes": [],
        "timestamp": timestamp,
    }


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
        self.assertEqual(read.replay_benchmarks, ())
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
        self.assertEqual(read.replay_benchmarks, ())
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

            replay_benchmark_record = learning_journal.ReplayBenchmarkRecord(
                task_set="bench-v1",
                success=True,
                score=0.9,
                run_id="run-benchmark-1",
                timestamp="2026-01-01T00:00:00Z",
            )
            self.assertIsNone(
                learning_journal.append_journal_record(replay_benchmark_record, root_dir=root)
            )

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
        self.assertEqual(len(read.replay_benchmarks), 1)
        self.assertEqual(read.unreadable_lines, 0)
        self.assertEqual(read.unknown_kind_lines, 0)

        self.assertEqual(read.worker_executions[0], worker_record)
        self.assertEqual(read.outcomes[0], outcome_record)
        self.assertEqual(read.dialogues[0], dialogue_record)
        self.assertEqual(read.compliance[0], compliance_record)
        self.assertEqual(read.replay_benchmarks[0], replay_benchmark_record)

    def test_an_absent_run_id_reads_back_as_none(self) -> None:
        # Narrowed to what this test can actually observe: an absent
        # `run_id` reads back as `None`. It used to also assert the two
        # records' task ids were unequal, which the two distinct input task
        # ids satisfy on their own — that assertion would stay green even if
        # "absent run_id is never a shared run" were deleted. That
        # non-grouping behaviour belongs to the stage-2 reducer test, where
        # two records can actually share one task_id.
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

        # The complete value, not just year/tzinfo/utcoffset — asserting
        # only those three stays green even if month/day/time parsing were
        # deleted.
        self.assertEqual(parsed, datetime(2026, 3, 5, 12, 30, 0, tzinfo=timezone.utc))

    def test_parse_wire_timestamp_raises_on_a_calendar_invalid_value(self) -> None:
        with self.assertRaises(ValueError):
            learning_journal.parse_wire_timestamp("2026-99-99T99:99:99Z")


class ConstructionTimeCalendarValidationTests(unittest.TestCase):
    """`_validate_timestamp` now checks calendar validity, not just shape.

    Regression coverage for the fix: every one of the five record types
    calls `_validate_timestamp` from `__post_init__`, so a calendar-invalid
    timestamp like `"2026-99-99T99:99:99Z"` — which matches `TIMESTAMP_RE`
    but names no real instant — must now be rejected by the constructor
    itself, never only by `read_journal` three modules downstream. Each test
    here calls the constructor directly (never `read_journal`) so a
    regression that moved the check back out of `_validate_timestamp` would
    fail these tests even though the reader's own tolerance tests
    (`ReadJournalToleranceTests`) would stay green.
    """

    BAD_TIMESTAMP = "2026-99-99T99:99:99Z"

    def test_worker_execution_record_rejects_calendar_invalid_timestamp(self) -> None:
        with self.assertRaises(ValueError):
            learning_journal.WorkerExecutionRecord(
                task=learning_journal.TaskLabel.for_task("task-bad-ts-worker"),
                duration_ms=1,
                cost_estimate_usd=0.0,
                success=True,
                retry_count=0,
                effort="low",
                model_id="claude-sonnet-5",
                model_family="claude",
                timestamp=self.BAD_TIMESTAMP,
            )

    def test_outcome_record_rejects_calendar_invalid_timestamp(self) -> None:
        with self.assertRaises(ValueError):
            learning_journal.OutcomeRecord(
                task=learning_journal.TaskLabel.for_task("task-bad-ts-outcome"),
                ground_truth="tests",
                verdict="pass",
                timestamp=self.BAD_TIMESTAMP,
            )

    def test_dialogue_quality_record_rejects_calendar_invalid_timestamp(self) -> None:
        with self.assertRaises(ValueError):
            learning_journal.DialogueQualityRecord(
                task=learning_journal.TaskLabel.for_task("task-bad-ts-dialogue"),
                occasion="ambiguity",
                topology="pair",
                rounds=(learning_journal.DialogueRound(verdict="approved", engagement_count=1),),
                timestamp=self.BAD_TIMESTAMP,
            )

    def test_replay_benchmark_record_rejects_calendar_invalid_timestamp(self) -> None:
        """Ticket 26's fifth family, which this class predates. Without it the
        docstring's "every one of the five record types" was an enumeration
        no assertion backed — and `acceptance_gate.py` stamps every trial from
        an injected `now`, so a caller handing it a malformed instant is the
        one path that reaches this constructor with a bad timestamp.
        """
        with self.assertRaises(ValueError):
            learning_journal.ReplayBenchmarkRecord(
                task_set="bench-v1",
                success=True,
                score=0.82,
                timestamp=self.BAD_TIMESTAMP,
            )

    def test_compliance_record_rejects_calendar_invalid_timestamp(self) -> None:
        with self.assertRaises(ValueError):
            learning_journal.ComplianceRecord(
                session_id="session-bad-ts-compliance",
                total_writes=1,
                code_writes=0,
                routing_declarations=1,
                worker_calls=0,
                violation_count=0,
                declaration_drift_count=0,
                calibration_markers=0,
                code_write_count=0,
                timestamp=self.BAD_TIMESTAMP,
            )

    def test_compliance_record_rejects_calendar_invalid_session_last_activity(self) -> None:
        with self.assertRaises(ValueError):
            learning_journal.ComplianceRecord(
                session_id="session-bad-activity-compliance",
                total_writes=1,
                code_writes=0,
                routing_declarations=1,
                worker_calls=0,
                violation_count=0,
                declaration_drift_count=0,
                calibration_markers=0,
                code_write_count=0,
                session_last_activity=self.BAD_TIMESTAMP,
                timestamp="2026-01-01T00:00:00Z",
            )

    def test_a_calendar_invalid_timestamp_cannot_be_written_before_it_can_vanish_on_read(
        self,
    ) -> None:
        # The write-read symmetry the finding was about: constructing a
        # record with a calendar-invalid timestamp must fail *before*
        # `append_journal_record` is ever reached, so the round trip can no
        # longer produce a record that was accepted at construction and then
        # silently discarded by `read_journal` on the way back out.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            with mock.patch.object(
                learning_journal, "append_journal_record"
            ) as mock_append:
                with self.assertRaises(ValueError):
                    learning_journal.WorkerExecutionRecord(
                        task=learning_journal.TaskLabel.for_task("task-never-written"),
                        duration_ms=1,
                        cost_estimate_usd=0.0,
                        success=True,
                        retry_count=0,
                        effort="low",
                        model_id="claude-sonnet-5",
                        model_family="claude",
                        timestamp=self.BAD_TIMESTAMP,
                    )
                mock_append.assert_not_called()

            # Nothing reached the journal file at all.
            read = learning_journal.read_journal(root)

        self.assertEqual(read.worker_executions, ())
        self.assertEqual(read.unreadable_lines, 0)


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

    def test_a_line_that_is_not_valid_utf8_is_skipped_and_its_neighbours_survive(self) -> None:
        # A byte sequence that is not valid UTF-8 mid-file is the same
        # artifact as a torn JSON line — a crash or full disk mid-write —
        # so it is per-line tolerance, not a whole-file failure.
        #
        # The bad byte is inserted *inside* an otherwise-valid record's
        # `future_field` string — a key `_filtered_fields` discards once the
        # line is decoded — rather than inside `task_id`. `b"\xff\xfe not
        # valid utf-8\n"` used to be the payload here, but that text is
        # independently invalid JSON with or without the bad bytes, so the
        # test could not tell strict UTF-8 decoding apart from a decode that
        # silently drops bad bytes (`errors="ignore"`) and then fails to
        # parse for an unrelated reason — it would stay green either way.
        # A later revision moved the byte into `task_id` to fix exactly
        # that, but that still could not isolate the claim: had decoding
        # been loosened to `errors="replace"`, the resulting garbled
        # `task_id` would fail *its own* validator
        # (`_validate_carried_identifier`), and the line would still read as
        # unreadable — for a field-validation reason unrelated to decoding
        # strictness. `future_field` closes that gap too: nothing validates
        # it (`_filtered_fields` drops it outright, whatever it contains),
        # so under `errors="replace"` this line would parse as valid JSON
        # and rehydrate into a real record. Only strict UTF-8 decoding
        # rejects it. Removing the one inserted byte reconstructs `line`
        # exactly, so a decoder using `errors="ignore"` would parse this
        # into a real record instead of skipping it — isolating the
        # decoding behaviour this test exists to check.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = _worker_record(
                "task-good-before-bad-bytes", timestamp="2026-01-01T00:00:00Z"
            )
            self.assertIsNone(learning_journal.append_journal_record(first, root_dir=root))

            wire = _valid_worker_execution_wire(
                "task-otherwise-valid-record", timestamp="2026-01-01T00:00:01Z"
            )
            wire["future_field"] = "unknown-field-marker"
            line = json.dumps(wire)
            encoded = line.encode("utf-8")
            marker = b"unknown-field-marker"
            insert_at = encoded.index(marker) + 4
            # 0xFF is never a valid byte anywhere in a UTF-8 sequence
            # (leading or continuation), so this is guaranteed to raise
            # `UnicodeDecodeError` under strict decoding.
            corrupted = encoded[:insert_at] + b"\xff" + encoded[insert_at:]
            self.assertEqual(corrupted.decode("utf-8", errors="ignore"), line)
            _append_raw_bytes(root, corrupted + b"\n")

            second = _worker_record(
                "task-good-after-bad-bytes", timestamp="2026-01-01T00:00:02Z"
            )
            self.assertIsNone(learning_journal.append_journal_record(second, root_dir=root))

            read = learning_journal.read_journal(root)

        self.assertEqual(
            {record.task.task_id for record in read.worker_executions},
            {"task-good-before-bad-bytes", "task-good-after-bad-bytes"},
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

    def test_a_non_finite_constant_inside_a_filtered_field_makes_the_line_unreadable(
        self,
    ) -> None:
        # F3: `json.loads` accepts the non-standard `NaN`/`Infinity`/
        # `-Infinity` tokens by default, anywhere in the line — including
        # inside a field this reader does not know about and would
        # otherwise silently drop via `_filtered_fields`. That must not let
        # the line through: the wire format's own contract
        # (`_append_jsonl_locked`) never emits these tokens for anything a
        # real writer's validators accept, so a line carrying one anywhere
        # is not valid JSON per that contract and must count as unreadable
        # exactly like malformed JSON does.
        #
        # Deliberately placed inside `future_field` — a key
        # `_filtered_fields` discards once the line is decoded — rather than
        # inside a validated field like `cost_estimate_usd`: a non-finite
        # value in a validated field already fails today via that field's
        # own validator (`_validate_amount`), so that case is not evidence
        # that non-finite tokens are rejected during parsing itself. Only a
        # filtered/unknown field isolates that claim.
        for constant, raw_token in (
            (float("nan"), "NaN"),
            (float("inf"), "Infinity"),
            (float("-inf"), "-Infinity"),
        ):
            with self.subTest(raw_token=raw_token):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    neighbour = _worker_record(
                        f"task-non-finite-{raw_token}-neighbour",
                        timestamp="2026-01-01T00:00:00Z",
                    )
                    self.assertIsNone(
                        learning_journal.append_journal_record(neighbour, root_dir=root)
                    )
                    wire = _valid_worker_execution_wire(
                        f"task-non-finite-{raw_token}", timestamp="2026-01-01T00:00:01Z"
                    )
                    wire["future_field"] = constant
                    line = json.dumps(wire)
                    # `json.dumps` writes the bare, unquoted token for a
                    # non-finite float by default — confirming the payload
                    # actually contains the non-standard constant this test
                    # means to exercise, not a quoted string that merely
                    # looks like one.
                    self.assertIn(raw_token, line)
                    _append_raw_line(root, line)

                    read = learning_journal.read_journal(root)

                self.assertEqual(len(read.worker_executions), 1)
                self.assertEqual(
                    read.worker_executions[0].task.task_id,
                    f"task-non-finite-{raw_token}-neighbour",
                )
                self.assertEqual(read.unreadable_lines, 1)

    def test_an_unrecognised_key_nested_inside_a_round_still_reads(self) -> None:
        # `_filtered_fields` must apply *inside* each round, not just at the
        # dialogue's top level — otherwise one future field nested in one
        # round makes the entire dialogue record unreadable.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wire = {
                "kind": "dialogue_quality",
                "task_id": "task-future-round-field",
                "sensitivity_halted": False,
                "occasion": "ambiguity",
                "topology": "pair",
                "rounds": [
                    {
                        "verdict": "approved",
                        "engagement_count": 2,
                        "future_round_field": "from a ticket that does not exist yet",
                    }
                ],
                "rounds_run": 1,
                "canaries_planted": 0,
                "canaries_caught": 0,
                "degraded": False,
                "independent": True,
                "timestamp": "2026-01-01T00:00:00Z",
            }
            _append_raw_line(root, json.dumps(wire))

            read = learning_journal.read_journal(root)

        self.assertEqual(len(read.dialogues), 1)
        self.assertEqual(read.dialogues[0].task.task_id, "task-future-round-field")
        self.assertEqual(
            read.dialogues[0].rounds,
            (learning_journal.DialogueRound(verdict="approved", engagement_count=2),),
        )
        self.assertEqual(read.unreadable_lines, 0)
        self.assertEqual(read.unknown_kind_lines, 0)

    def test_a_round_missing_a_required_key_reaches_argument_binding_as_a_type_error(
        self,
    ) -> None:
        # F3: `_check_required_keys` only guards the top-level wire keys a
        # rehydrator reads before construction — nothing plays that role
        # for the objects nested inside `rounds`, so a round missing
        # `verdict` or `engagement_count` reaches `DialogueRound(**...)`
        # itself and fails there with a `TypeError` for a missing required
        # positional argument, not a `ValueError` from any validator. This
        # is `read_journal`'s documented `except (ValueError, TypeError)`
        # actually being exercised by a `TypeError` rather than merely
        # promised: every other malformed-input test in this file reaches a
        # `_check_required_keys`/validator `ValueError` first, so without a
        # case like this the `TypeError` branch could be deleted and every
        # test here would stay green.
        cases = (
            ("verdict", {"engagement_count": 2}),
            ("engagement_count", {"verdict": "approved"}),
        )
        for missing_key, round_wire in cases:
            with self.subTest(missing_key=missing_key):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    neighbour = learning_journal.DialogueQualityRecord(
                        task=learning_journal.TaskLabel.for_task(
                            f"task-round-missing-{missing_key}-neighbour"
                        ),
                        occasion="ambiguity",
                        topology="pair",
                        rounds=(
                            learning_journal.DialogueRound(
                                verdict="approved", engagement_count=1
                            ),
                        ),
                        timestamp="2026-01-01T00:00:00Z",
                    )
                    self.assertIsNone(
                        learning_journal.append_journal_record(neighbour, root_dir=root)
                    )
                    wire = _valid_dialogue_quality_wire(
                        f"task-round-missing-{missing_key}",
                        timestamp="2026-01-01T00:00:01Z",
                    )
                    wire["rounds"] = [round_wire]
                    wire["rounds_run"] = 1
                    _append_raw_line(root, json.dumps(wire))

                    read = learning_journal.read_journal(root)

                self.assertEqual(len(read.dialogues), 1)
                self.assertEqual(
                    read.dialogues[0].task.task_id,
                    f"task-round-missing-{missing_key}-neighbour",
                )
                self.assertEqual(read.unreadable_lines, 1)

    # F4: the exhaustive, per-key, all-four-families sweep this single-family
    # test used to be lives in `ReadJournalRequiredWireKeyTests` now, as
    # `test_every_required_key_of_every_task_bearing_family_makes_its_line_unreadable`
    # and `test_every_required_key_of_compliance_makes_its_line_unreadable` —
    # one table covering every family rather than a single-family test that
    # made every other family's required-key enforcement untested by
    # omission. The "why `sensitivity_halted`/`timestamp` count as required
    # despite carrying a constructor default" reasoning this comment used to
    # carry moved there with it.

    def test_records_are_returned_in_file_order_not_sorted_by_id_or_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Insertion order (b, c, a) deliberately conflicts with both a
            # sort by task_id (a, b, c) and a sort by timestamp (c, a, b),
            # so an implementation that secretly sorted by either would fail
            # this test rather than passing it by accident.
            records = [
                _worker_record("task-order-b", timestamp="2026-01-01T00:00:03Z"),
                _worker_record("task-order-c", timestamp="2026-01-01T00:00:01Z"),
                _worker_record("task-order-a", timestamp="2026-01-01T00:00:02Z"),
            ]
            for record in records:
                self.assertIsNone(learning_journal.append_journal_record(record, root_dir=root))

            read = learning_journal.read_journal(root)

        self.assertEqual(
            [record.task.task_id for record in read.worker_executions],
            ["task-order-b", "task-order-c", "task-order-a"],
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
                # `issue_codes` is required on the wire (it is not typed
                # `X | None`) and is included here so this line fails for
                # exactly the reason this test names — a bad
                # `session_last_activity` — rather than incidentally
                # failing the required-key check first for an unrelated,
                # unasserted reason.
                "issue_codes": [],
                "timestamp": "2026-01-01T00:00:00Z",
                "session_last_activity": "2026-99-99T99:99:99Z",
            }
            _append_raw_line(root, json.dumps(wire))

            read = learning_journal.read_journal(root)

        self.assertEqual(len(read.compliance), 0)
        self.assertEqual(read.unreadable_lines, 1)


class ReadJournalRequiredWireKeyTests(unittest.TestCase):
    """A wire key is required unless its field's own type admits `None`.

    `_wire_form` (and `TaskLabel.to_mapping`) omit a key *iff* its value is
    `None` — never because the value happens to equal a constructor default.
    So the rehydrators must not treat a missing key as "use the default" the
    way a constructor would: a missing `timestamp` used to be silently
    stamped with the *read* time via `default_factory`, and a missing
    `rounds` used to fall back to `()` via the rehydrator's own
    `mapping.get("rounds", ())` — both indistinguishable from a real record
    and both corrupting every trend built on them. Exactly four fields
    anywhere in this module are typed `X | None` — `run_id`, `task_type`,
    `session_last_activity`, and ticket 26's `score` — and those four are the
    only ones a real line may honestly omit.

    `dialogue_quality`'s `rounds_run` is required by the same "always
    emitted, so always required" rule, but by a different mechanism: it is a
    `@property`, not a field, so nothing in its type annotation could mark it
    optional in the first place — see `_DIALOGUE_QUALITY_REQUIRED_KEYS` in
    `learning_journal.py`. It is also the one required key that is not just
    checked for presence but cross-validated against another key
    (`len(rounds)`); see the mismatch test below.
    """

    def test_a_line_missing_timestamp_is_unreadable_for_every_family(self) -> None:
        cases = (
            ("worker_execution", _valid_worker_execution_wire),
            ("outcome", _valid_outcome_wire),
            ("dialogue_quality", _valid_dialogue_quality_wire),
        )
        for kind, wire_factory in cases:
            with self.subTest(kind=kind):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    # A valid neighbour of the *same* family that must
                    # still be read — proof the damaged line is skipped
                    # rather than taking the whole family down with it.
                    neighbour = _worker_record(
                        f"task-{kind}-timestamp-neighbour", timestamp="2026-01-01T00:00:00Z"
                    )
                    self.assertIsNone(
                        learning_journal.append_journal_record(neighbour, root_dir=root)
                    )
                    wire = wire_factory(
                        f"task-{kind}-missing-timestamp", timestamp="2026-01-01T00:00:01Z"
                    )
                    del wire["timestamp"]
                    _append_raw_line(root, json.dumps(wire))

                    read = learning_journal.read_journal(root)

                self.assertEqual(len(read.worker_executions), 1)
                self.assertEqual(len(read.outcomes), 0)
                self.assertEqual(len(read.dialogues), 0)
                self.assertEqual(read.unreadable_lines, 1)

    def test_a_compliance_line_missing_timestamp_is_unreadable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            neighbour = _worker_record(
                "task-compliance-timestamp-neighbour", timestamp="2026-01-01T00:00:00Z"
            )
            self.assertIsNone(learning_journal.append_journal_record(neighbour, root_dir=root))
            wire = _valid_compliance_wire(
                "session-missing-timestamp", timestamp="2026-01-01T00:00:01Z"
            )
            del wire["timestamp"]
            _append_raw_line(root, json.dumps(wire))

            read = learning_journal.read_journal(root)

        self.assertEqual(len(read.worker_executions), 1)
        self.assertEqual(len(read.compliance), 0)
        self.assertEqual(read.unreadable_lines, 1)

    def test_a_dialogue_line_missing_rounds_is_unreadable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            neighbour = learning_journal.DialogueQualityRecord(
                task=learning_journal.TaskLabel.for_task("task-rounds-neighbour"),
                occasion="ambiguity",
                topology="pair",
                rounds=(learning_journal.DialogueRound(verdict="approved", engagement_count=1),),
                timestamp="2026-01-01T00:00:00Z",
            )
            self.assertIsNone(learning_journal.append_journal_record(neighbour, root_dir=root))
            wire = _valid_dialogue_quality_wire(
                "task-missing-rounds", timestamp="2026-01-01T00:00:01Z"
            )
            del wire["rounds"]
            _append_raw_line(root, json.dumps(wire))

            read = learning_journal.read_journal(root)

        # A missing `rounds` key must not read as the legitimate zero-round
        # state (a budget-skipped run) that a real writer's `rounds: []`
        # describes — those are two different facts, and only presence with
        # an empty list may claim the second one. See the positive control
        # below.
        self.assertEqual(len(read.dialogues), 1)
        self.assertEqual(read.dialogues[0].task.task_id, "task-rounds-neighbour")
        self.assertEqual(read.unreadable_lines, 1)

    def test_a_dialogue_line_with_an_explicit_empty_rounds_list_still_reads_as_zero_rounds(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wire = _valid_dialogue_quality_wire("task-zero-rounds", timestamp="2026-01-01T00:00:00Z")
            wire["rounds"] = []
            wire["rounds_run"] = 0
            _append_raw_line(root, json.dumps(wire))

            read = learning_journal.read_journal(root)

        self.assertEqual(len(read.dialogues), 1)
        self.assertEqual(read.dialogues[0].rounds, ())
        self.assertEqual(read.unreadable_lines, 0)

    def test_a_line_missing_only_run_id_task_type_or_session_last_activity_still_reads(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worker_wire = _valid_worker_execution_wire(
                "task-no-run-id-or-type", timestamp="2026-01-01T00:00:00Z"
            )
            # A real writer never includes these two when they are unset in
            # the first place (`TaskLabel.to_mapping`, `_wire_form`) — this
            # confirms the base fixture already matches that, so the
            # assertions below exercise real absence, not an accidental
            # `null`.
            self.assertNotIn("run_id", worker_wire)
            self.assertNotIn("task_type", worker_wire)
            _append_raw_line(root, json.dumps(worker_wire))

            compliance_wire = _valid_compliance_wire(
                "session-no-last-activity", timestamp="2026-01-01T00:00:01Z"
            )
            self.assertNotIn("session_last_activity", compliance_wire)
            _append_raw_line(root, json.dumps(compliance_wire))

            # `run_id` is optional on every task-bearing family, not just
            # `worker_execution` — the deletion sweep below proves each
            # family's `run_id` is *required when the writer sets it*, and
            # this is that check's positive counterpart for the other two
            # families: absent is fine everywhere it is legitimately absent.
            outcome_wire = _valid_outcome_wire(
                "task-outcome-no-run-id", timestamp="2026-01-01T00:00:02Z"
            )
            self.assertNotIn("run_id", outcome_wire)
            _append_raw_line(root, json.dumps(outcome_wire))

            dialogue_wire = _valid_dialogue_quality_wire(
                "task-dialogue-no-run-id", timestamp="2026-01-01T00:00:03Z"
            )
            self.assertNotIn("run_id", dialogue_wire)
            _append_raw_line(root, json.dumps(dialogue_wire))

            read = learning_journal.read_journal(root)

        self.assertEqual(read.unreadable_lines, 0)
        self.assertEqual(len(read.worker_executions), 1)
        self.assertIsNone(read.worker_executions[0].run_id)
        self.assertIsNone(read.worker_executions[0].task.task_type)
        self.assertEqual(len(read.compliance), 1)
        self.assertIsNone(read.compliance[0].run_id)
        self.assertIsNone(read.compliance[0].session_last_activity)
        self.assertEqual(len(read.outcomes), 1)
        self.assertIsNone(read.outcomes[0].run_id)
        self.assertEqual(len(read.dialogues), 1)
        self.assertIsNone(read.dialogues[0].run_id)

    def test_a_dialogue_lines_rounds_run_contradicting_len_rounds_is_unreadable(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            neighbour = learning_journal.DialogueQualityRecord(
                task=learning_journal.TaskLabel.for_task("task-rounds-run-mismatch-neighbour"),
                occasion="ambiguity",
                topology="pair",
                rounds=(learning_journal.DialogueRound(verdict="approved", engagement_count=1),),
                timestamp="2026-01-01T00:00:00Z",
            )
            self.assertIsNone(learning_journal.append_journal_record(neighbour, root_dir=root))
            wire = _valid_dialogue_quality_wire(
                "task-rounds-run-mismatch", timestamp="2026-01-01T00:00:01Z"
            )
            # One round on the wire, but `rounds_run` lies about it — the
            # exact contradiction `to_mapping`'s inverse must never accept,
            # since `rounds_run` is derived from `rounds` and must always
            # agree with it (see `DialogueQualityRecord.rounds_run` and
            # `_rehydrate_dialogue_quality` in learning_journal.py).
            wire["rounds_run"] = 5
            _append_raw_line(root, json.dumps(wire))

            read = learning_journal.read_journal(root)

        self.assertEqual(len(read.dialogues), 1)
        self.assertEqual(
            read.dialogues[0].task.task_id, "task-rounds-run-mismatch-neighbour"
        )
        self.assertEqual(read.unreadable_lines, 1)

    def test_a_bool_rounds_run_is_unreadable_even_though_it_equals_len_rounds(self) -> None:
        # F2: `True == 1` in Python, so a bare `rounds_run_raw != len(rounds)`
        # check lets `rounds_run=true` slip through as if it correctly named
        # one round. `rounds_run` must be type-validated (rejecting `bool`,
        # exactly as every other count field in this module already does via
        # `_validate_count`) before the equality comparison ever runs.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            neighbour = learning_journal.DialogueQualityRecord(
                task=learning_journal.TaskLabel.for_task("task-rounds-run-bool-neighbour"),
                occasion="ambiguity",
                topology="pair",
                rounds=(learning_journal.DialogueRound(verdict="approved", engagement_count=1),),
                timestamp="2026-01-01T00:00:00Z",
            )
            self.assertIsNone(learning_journal.append_journal_record(neighbour, root_dir=root))
            wire = _valid_dialogue_quality_wire(
                "task-rounds-run-bool", timestamp="2026-01-01T00:00:01Z"
            )
            # One round on the wire; `rounds_run=True` would pass a bare
            # `!= len(rounds)` check since `True == 1`.
            wire["rounds_run"] = True
            _append_raw_line(root, json.dumps(wire))

            read = learning_journal.read_journal(root)

        self.assertEqual(len(read.dialogues), 1)
        self.assertEqual(read.dialogues[0].task.task_id, "task-rounds-run-bool-neighbour")
        self.assertEqual(read.unreadable_lines, 1)

    def test_a_float_rounds_run_is_unreadable_even_though_it_equals_len_rounds(self) -> None:
        # F2: same hole, `1.0 == 1` in Python. `rounds_run` must be rejected
        # as a non-int before the equality comparison, not accepted because
        # it happens to compare equal to the real round count.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            neighbour = learning_journal.DialogueQualityRecord(
                task=learning_journal.TaskLabel.for_task("task-rounds-run-float-neighbour"),
                occasion="ambiguity",
                topology="pair",
                rounds=(learning_journal.DialogueRound(verdict="approved", engagement_count=1),),
                timestamp="2026-01-01T00:00:00Z",
            )
            self.assertIsNone(learning_journal.append_journal_record(neighbour, root_dir=root))
            wire = _valid_dialogue_quality_wire(
                "task-rounds-run-float", timestamp="2026-01-01T00:00:01Z"
            )
            wire["rounds_run"] = 1.0
            _append_raw_line(root, json.dumps(wire))

            read = learning_journal.read_journal(root)

        self.assertEqual(len(read.dialogues), 1)
        self.assertEqual(read.dialogues[0].task.task_id, "task-rounds-run-float-neighbour")
        self.assertEqual(read.unreadable_lines, 1)

    def test_every_required_key_of_every_task_bearing_family_makes_its_line_unreadable(
        self,
    ) -> None:
        # F4: `test_a_known_kind_missing_a_required_key_is_unreadable_not_a_crash`
        # used to run this sweep for `worker_execution` alone. Every other
        # family's required-key enforcement was therefore reachable only by
        # accident — dropping the check for `outcome.verdict` or
        # `dialogue_quality.topology` would have stayed green. One table,
        # every task-bearing family, every key `to_mapping` always emits.
        # `compliance` is not task-bearing (no `TaskLabel`, so no
        # `task_id`/`sensitivity_halted` to union in) and gets its own test
        # below rather than being forced into this table's shape.
        families = (
            (
                "worker_execution",
                _valid_worker_execution_wire,
                (
                    "task_id",
                    "sensitivity_halted",
                    "duration_ms",
                    "cost_estimate_usd",
                    "success",
                    "retry_count",
                    "effort",
                    "model_id",
                    "model_family",
                    "timestamp",
                ),
            ),
            (
                "outcome",
                _valid_outcome_wire,
                ("task_id", "sensitivity_halted", "ground_truth", "verdict", "timestamp"),
            ),
            (
                "dialogue_quality",
                _valid_dialogue_quality_wire,
                (
                    "task_id",
                    "sensitivity_halted",
                    "occasion",
                    "topology",
                    "rounds",
                    "rounds_run",
                    "canaries_planted",
                    "canaries_caught",
                    "degraded",
                    "independent",
                    "timestamp",
                ),
            ),
        )
        for kind, wire_factory, required_keys in families:
            for missing_key in required_keys:
                with self.subTest(kind=kind, missing_key=missing_key):
                    with tempfile.TemporaryDirectory() as tmp:
                        root = Path(tmp)
                        # A neighbour of the *same* family, proving the
                        # damaged line is skipped rather than taking the
                        # whole family down with it.
                        neighbour_wire = wire_factory(
                            f"task-{kind}-{missing_key}-neighbour",
                            timestamp="2026-01-01T00:00:00Z",
                        )
                        _append_raw_line(root, json.dumps(neighbour_wire))
                        wire = wire_factory(
                            f"task-{kind}-{missing_key}-missing",
                            timestamp="2026-01-01T00:00:01Z",
                        )
                        del wire[missing_key]
                        _append_raw_line(root, json.dumps(wire))

                        read = learning_journal.read_journal(root)

                    counts = {
                        "worker_execution": len(read.worker_executions),
                        "outcome": len(read.outcomes),
                        "dialogue_quality": len(read.dialogues),
                    }
                    self.assertEqual(counts[kind], 1)
                    self.assertEqual(read.unreadable_lines, 1)

    def test_every_required_key_of_compliance_makes_its_line_unreadable(self) -> None:
        required_keys = (
            "session_id",
            "total_writes",
            "code_writes",
            "routing_declarations",
            "worker_calls",
            "violation_count",
            "declaration_drift_count",
            "calibration_markers",
            "code_write_count",
            "issue_codes",
            "timestamp",
        )
        for missing_key in required_keys:
            with self.subTest(missing_key=missing_key):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    neighbour_wire = _valid_compliance_wire(
                        f"session-{missing_key}-neighbour", timestamp="2026-01-01T00:00:00Z"
                    )
                    _append_raw_line(root, json.dumps(neighbour_wire))
                    wire = _valid_compliance_wire(
                        f"session-{missing_key}-missing", timestamp="2026-01-01T00:00:01Z"
                    )
                    del wire[missing_key]
                    _append_raw_line(root, json.dumps(wire))

                    read = learning_journal.read_journal(root)

                self.assertEqual(len(read.compliance), 1)
                self.assertEqual(read.unreadable_lines, 1)

    def test_every_required_key_of_replay_benchmark_makes_its_line_unreadable(self) -> None:
        """`score` is deliberately absent from this list: it is one of the
        four fields typed `X | None` (`_optional_wire_fields`), so a missing
        `score` is not a required-key violation at all. A successful trial
        missing its score still becomes unreadable, but through
        `ReplayBenchmarkRecord.__post_init__`'s own success/score agreement
        check — see the dedicated test below."""
        required_keys = ("task_set", "success", "timestamp")
        for missing_key in required_keys:
            with self.subTest(missing_key=missing_key):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    neighbour_wire = _valid_replay_benchmark_wire(
                        f"bench-{missing_key}-neighbour", timestamp="2026-01-01T00:00:00Z"
                    )
                    _append_raw_line(root, json.dumps(neighbour_wire))
                    wire = _valid_replay_benchmark_wire(
                        f"bench-{missing_key}-missing", timestamp="2026-01-01T00:00:01Z"
                    )
                    del wire[missing_key]
                    _append_raw_line(root, json.dumps(wire))

                    read = learning_journal.read_journal(root)

                self.assertEqual(len(read.replay_benchmarks), 1)
                self.assertEqual(read.unreadable_lines, 1)

    def test_a_successful_replay_benchmark_line_missing_its_score_is_unreadable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            neighbour_wire = _valid_replay_benchmark_wire(
                "bench-score-neighbour", timestamp="2026-01-01T00:00:00Z"
            )
            _append_raw_line(root, json.dumps(neighbour_wire))
            wire = _valid_replay_benchmark_wire(
                "bench-score-missing", timestamp="2026-01-01T00:00:01Z"
            )
            del wire["score"]
            _append_raw_line(root, json.dumps(wire))

            read = learning_journal.read_journal(root)

        self.assertEqual(len(read.replay_benchmarks), 1)
        self.assertEqual(read.replay_benchmarks[0].task_set, "bench-score-neighbour")
        self.assertEqual(read.unreadable_lines, 1)

    def test_a_failed_replay_benchmark_line_with_no_score_at_all_reads_back(self) -> None:
        """The positive control for the test above: a failed trial's wire
        form never carries `score` at all, and that must read back as a
        genuine, complete record — not as damage."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record = learning_journal.ReplayBenchmarkRecord(
                task_set="bench-v1", success=False, timestamp="2026-01-01T00:00:00Z"
            )
            self.assertIsNone(learning_journal.append_journal_record(record, root_dir=root))

            read = learning_journal.read_journal(root)

        self.assertEqual(len(read.replay_benchmarks), 1)
        self.assertEqual(read.replay_benchmarks[0], record)
        self.assertIsNone(read.replay_benchmarks[0].score)
        self.assertEqual(read.unreadable_lines, 0)


class ReadJournalKindTypeTests(unittest.TestCase):
    """A non-string `kind` must not reach the dispatch lookup.

    It must count as damage (`unreadable_lines`), never as a family this
    reader predates (`unknown_kind_lines`) — a `list` there raises
    `TypeError: unhashable type` before any tolerance applies, and
    `None`/a number are real values a damaged record can carry, not a real
    family name.
    """

    def _assert_bad_kind_is_unreadable_not_unknown(self, bad_kind: Any) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            neighbour = _worker_record(
                "task-good-kind-neighbour", timestamp="2026-01-01T00:00:00Z"
            )
            self.assertIsNone(learning_journal.append_journal_record(neighbour, root_dir=root))
            _append_raw_line(root, json.dumps({"kind": bad_kind, "task_id": "task-bad-kind"}))

            read = learning_journal.read_journal(root)

        self.assertEqual(len(read.worker_executions), 1)
        self.assertEqual(read.worker_executions[0].task.task_id, "task-good-kind-neighbour")
        self.assertEqual(read.unreadable_lines, 1)
        self.assertEqual(read.unknown_kind_lines, 0)

    def test_a_list_kind_is_unreadable_not_a_crash(self) -> None:
        self._assert_bad_kind_is_unreadable_not_unknown([])

    def test_a_dict_kind_is_unreadable_not_a_crash(self) -> None:
        self._assert_bad_kind_is_unreadable_not_unknown({})

    def test_a_numeric_kind_is_unreadable_not_unknown(self) -> None:
        self._assert_bad_kind_is_unreadable_not_unknown(1)

    def test_a_null_kind_is_unreadable_not_unknown(self) -> None:
        self._assert_bad_kind_is_unreadable_not_unknown(None)


class ReadJournalWholeFileFailureTests(unittest.TestCase):
    """`open`/`flock` failures propagate; the shared lock is actually taken."""

    def test_a_whole_file_open_failure_propagates_rather_than_reading_as_empty(self) -> None:
        # This is F1's "inaccessible path propagates" case: the journal
        # exists and has real content, `open()` fails with `PermissionError`
        # (not `FileNotFoundError`), so the "missing journal reads as empty"
        # branch must not apply. Before the fix this path went through
        # `Path.exists()` first, which can itself observe a permission
        # failure and report `False` — masking exactly this error as an
        # empty journal. There is no separate `exists()` call left to do
        # that, so this failure has only one place left to be swallowed:
        # nowhere.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record = _worker_record("task-open-failure", timestamp="2026-01-01T00:00:00Z")
            self.assertIsNone(learning_journal.append_journal_record(record, root_dir=root))

            with (
                mock.patch("builtins.open", side_effect=PermissionError("simulated failure")),
                self.assertRaises(PermissionError),
            ):
                learning_journal.read_journal(root)

    def test_open_raising_file_not_found_reads_as_empty_even_though_the_file_exists(
        self,
    ) -> None:
        # F1's deletion-race case: the journal genuinely exists on disk (a
        # real record was appended to it), but `open()` itself is made to
        # raise `FileNotFoundError` — simulating the file vanishing between
        # any check and the open call. `read_journal` no longer performs a
        # separate `path.exists()` check at all, so there is no earlier call
        # whose answer this could contradict; only `open()`'s own exception
        # decides "missing", and this is that exception.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record = _worker_record("task-exists-race", timestamp="2026-01-01T00:00:00Z")
            self.assertIsNone(learning_journal.append_journal_record(record, root_dir=root))

            with mock.patch("builtins.open", side_effect=FileNotFoundError()):
                read = learning_journal.read_journal(root)

        self.assertEqual(read.worker_executions, ())
        self.assertEqual(read.unreadable_lines, 0)

    def test_a_flock_failure_propagates_rather_than_reading_as_empty(self) -> None:
        # Only `open` was ever patched to fail before this test. A reader
        # that swallowed an `OSError` from `fcntl.flock` and fell through to
        # an empty read would stay green against that gap — this exercises
        # the lock call itself failing against a journal that is not empty,
        # so a false "no data" read is distinguishable from a real one.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record = _worker_record("task-flock-failure", timestamp="2026-01-01T00:00:00Z")
            self.assertIsNone(learning_journal.append_journal_record(record, root_dir=root))

            sentinel = OSError("simulated flock failure")
            real_flock = learning_journal.fcntl.flock

            def _failing_flock(fd: int, operation: int) -> None:
                if operation == learning_journal.fcntl.LOCK_SH:
                    raise sentinel
                real_flock(fd, operation)

            with (
                mock.patch.object(learning_journal.fcntl, "flock", side_effect=_failing_flock),
                self.assertRaises(OSError) as ctx,
            ):
                learning_journal.read_journal(root)

        # The exact error, not merely "some `OSError`" — proof the
        # propagation reaches the caller unmodified rather than being
        # caught and re-raised as a different, coincidentally-also-OSError
        # failure.
        self.assertIs(ctx.exception, sentinel)

    def test_a_readlines_failure_propagates_rather_than_reading_as_empty(self) -> None:
        # F6: a mid-read failure (a disk error surfacing while `readlines()`
        # is still consuming the file, distinct from `open()` or `flock`
        # failing before any bytes are read) must not be swallowed into an
        # empty journal either. Wraps the real file object returned by
        # `open()`, exactly as `test_read_journal_holds_the_lock_across_the_read_not_just_around_it`
        # already does to observe `readlines()`, but replaces the call with
        # one that raises instead of merely recording that it ran.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record = _worker_record("task-readlines-failure", timestamp="2026-01-01T00:00:00Z")
            self.assertIsNone(learning_journal.append_journal_record(record, root_dir=root))

            sentinel = OSError("simulated readlines failure")
            real_open = open
            journal_file = learning_journal.journal_path(root)

            def _open_with_failing_readlines(
                file: Any, mode: str = "r", *args: Any, **kwargs: Any
            ) -> Any:
                opened = real_open(file, mode, *args, **kwargs)
                if mode == "rb" and Path(str(file)) == journal_file:

                    def _failing_readlines(hint: int = -1) -> list[bytes]:
                        raise sentinel

                    opened.readlines = _failing_readlines  # type: ignore[method-assign]
                return opened

            with (
                mock.patch("builtins.open", side_effect=_open_with_failing_readlines),
                self.assertRaises(OSError) as ctx,
            ):
                learning_journal.read_journal(root)

        # The exact error, for the same reason the flock test above checks
        # `assertIs` rather than merely `assertRaises(OSError)`: proof this
        # is unmodified propagation, not a caught-and-rethrown coincidence.
        self.assertIs(ctx.exception, sentinel)

    def test_a_file_not_found_error_from_readlines_propagates_rather_than_reading_as_empty(
        self,
    ) -> None:
        # F1: the "missing journal reads as empty" branch must catch
        # `FileNotFoundError` only from `open()` itself, not from anything
        # inside the `with` block. A `FileNotFoundError` raised by
        # `readlines()` — the file was present when `open()` succeeded and
        # vanished before it was read, e.g. deleted mid-read or an
        # NFS unlink-on-open pattern — is a whole-file failure like any
        # other, not "the file was absent at open", and must propagate
        # exactly like the generic `OSError` case above rather than being
        # silently read as an empty journal.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record = _worker_record(
                "task-readlines-file-not-found", timestamp="2026-01-01T00:00:00Z"
            )
            self.assertIsNone(learning_journal.append_journal_record(record, root_dir=root))

            sentinel = FileNotFoundError("simulated deletion mid-read")
            real_open = open
            journal_file = learning_journal.journal_path(root)

            def _open_with_vanishing_readlines(
                file: Any, mode: str = "r", *args: Any, **kwargs: Any
            ) -> Any:
                opened = real_open(file, mode, *args, **kwargs)
                if mode == "rb" and Path(str(file)) == journal_file:

                    def _vanishing_readlines(hint: int = -1) -> list[bytes]:
                        raise sentinel

                    opened.readlines = _vanishing_readlines  # type: ignore[method-assign]
                return opened

            with (
                mock.patch("builtins.open", side_effect=_open_with_vanishing_readlines),
                self.assertRaises(FileNotFoundError) as ctx,
            ):
                learning_journal.read_journal(root)

        # The exact error, not a fresh empty read — proof the file-absent-at-
        # open branch does not also catch this.
        self.assertIs(ctx.exception, sentinel)

    def test_read_journal_takes_a_shared_lock_before_reading_and_releases_it_after(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record = _worker_record("task-lock", timestamp="2026-01-01T00:00:00Z")
            self.assertIsNone(learning_journal.append_journal_record(record, root_dir=root))

            operations: list[int] = []
            real_flock = learning_journal.fcntl.flock

            def _tracking_flock(fd: int, operation: int) -> None:
                operations.append(operation)
                real_flock(fd, operation)

            with mock.patch.object(learning_journal.fcntl, "flock", side_effect=_tracking_flock):
                read = learning_journal.read_journal(root)

        # Deleting both `flock` calls leaves this list empty; a reader that
        # dropped only the release would leave it with one entry. Either
        # failure mode is caught by asserting the exact pair, in order.
        self.assertEqual(
            operations,
            [learning_journal.fcntl.LOCK_SH, learning_journal.fcntl.LOCK_UN],
        )
        self.assertEqual(len(read.worker_executions), 1)

    def test_read_journal_holds_the_lock_across_the_read_not_just_around_it(self) -> None:
        # The previous version of this test asserted only
        # `[LOCK_SH, LOCK_UN]` — the two lock calls in order relative to each
        # other. That stays green even if `LOCK_UN` moved to immediately
        # before `readlines()`, which breaks the actual contract: the lock
        # must be held *during* the read, not merely requested and released
        # somewhere around it. Folding `readlines()` into the same timeline
        # is what makes that reachable: the assertion below fails if the
        # unlock is reordered ahead of the read, not just if a lock call is
        # dropped entirely.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record = _worker_record("task-lock-ordering", timestamp="2026-01-01T00:00:00Z")
            self.assertIsNone(learning_journal.append_journal_record(record, root_dir=root))

            events: list[object] = []
            real_flock = learning_journal.fcntl.flock
            real_open = open
            journal_file = learning_journal.journal_path(root)

            def _tracking_flock(fd: int, operation: int) -> None:
                events.append(operation)
                real_flock(fd, operation)

            def _open_and_track(file: Any, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
                opened = real_open(file, mode, *args, **kwargs)
                if mode == "rb" and Path(str(file)) == journal_file:
                    real_readlines = opened.readlines

                    def _tracking_readlines(hint: int = -1) -> list[bytes]:
                        events.append("readlines")
                        return real_readlines(hint)

                    opened.readlines = _tracking_readlines  # type: ignore[method-assign]
                return opened

            with (
                mock.patch.object(learning_journal.fcntl, "flock", side_effect=_tracking_flock),
                mock.patch("builtins.open", side_effect=_open_and_track),
            ):
                read = learning_journal.read_journal(root)

        self.assertEqual(
            events,
            [
                learning_journal.fcntl.LOCK_SH,
                "readlines",
                learning_journal.fcntl.LOCK_UN,
            ],
        )
        self.assertEqual(len(read.worker_executions), 1)


class ReadJournalBlankLineTests(unittest.TestCase):
    """DECIDE D1 (worker-routing.md Spec 0004 review, iteration 3): a blank
    or whitespace-only line is skipped without incrementing
    `unreadable_lines` rather than counted as damage. See `read_journal`'s
    docstring for the full reasoning; this is that decision's test.
    """

    def test_a_blank_line_between_two_records_is_skipped_without_counting_as_unreadable(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = _worker_record("task-before-blank", timestamp="2026-01-01T00:00:00Z")
            self.assertIsNone(learning_journal.append_journal_record(first, root_dir=root))
            _append_raw_line(root, "")
            _append_raw_line(root, "   ")
            second = _worker_record("task-after-blank", timestamp="2026-01-01T00:00:01Z")
            self.assertIsNone(learning_journal.append_journal_record(second, root_dir=root))

            read = learning_journal.read_journal(root)

        self.assertEqual(
            {record.task.task_id for record in read.worker_executions},
            {"task-before-blank", "task-after-blank"},
        )
        self.assertEqual(read.unreadable_lines, 0)

    def test_an_all_blank_journal_reads_as_empty_not_damaged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _append_raw_line(root, "")
            _append_raw_line(root, "   ")

            read = learning_journal.read_journal(root)

        self.assertEqual(read.worker_executions, ())
        self.assertEqual(read.unreadable_lines, 0)


class ReadJournalExplicitNullOptionalFieldTests(unittest.TestCase):
    """DECIDE D2 (worker-routing.md Spec 0004 review, iteration 3): an
    explicit wire `null` on an optional field (`run_id`, `task_type`,
    `session_last_activity`, and ticket 26's `score`) is accepted as absence
    rather than rejected as damage. See `read_journal`'s docstring for the
    full reasoning; this is that decision's test.

    `score` is the one where accepting absence does not end the matter: a
    `success: true` line whose `score` is an explicit `null` reads back as
    absence here and is then refused by `ReplayBenchmarkRecord.__post_init__`'s
    success/score agreement check, which is a different rule and a different
    test (`test_a_successful_replay_benchmark_line_missing_its_score_is_unreadable`
    — not the required-key sweep beside it, which deliberately excludes
    `score` for exactly this reason).
    """

    def test_an_explicit_null_run_id_reads_back_as_none_not_as_damage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wire = _valid_worker_execution_wire(
                "task-explicit-null-run-id", timestamp="2026-01-01T00:00:00Z"
            )
            wire["run_id"] = None
            _append_raw_line(root, json.dumps(wire))

            read = learning_journal.read_journal(root)

        self.assertEqual(len(read.worker_executions), 1)
        self.assertIsNone(read.worker_executions[0].run_id)
        self.assertEqual(read.unreadable_lines, 0)

    def test_an_explicit_null_task_type_reads_back_as_untagged_not_as_damage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wire = _valid_worker_execution_wire(
                "task-explicit-null-task-type", timestamp="2026-01-01T00:00:00Z"
            )
            wire["task_type"] = None
            _append_raw_line(root, json.dumps(wire))

            read = learning_journal.read_journal(root)

        self.assertEqual(len(read.worker_executions), 1)
        self.assertIsNone(read.worker_executions[0].task.task_type)
        self.assertEqual(read.unreadable_lines, 0)

    def test_an_explicit_null_session_last_activity_reads_back_as_none_not_as_damage(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wire = _valid_compliance_wire(
                "session-explicit-null-last-activity", timestamp="2026-01-01T00:00:00Z"
            )
            wire["session_last_activity"] = None
            _append_raw_line(root, json.dumps(wire))

            read = learning_journal.read_journal(root)

        self.assertEqual(len(read.compliance), 1)
        self.assertIsNone(read.compliance[0].session_last_activity)
        self.assertEqual(read.unreadable_lines, 0)


def _worker_execution_record(
    task_id: str, *, timestamp: str, cost: float = 0.0, run_id: str | None = None
) -> Any:
    """A `WorkerExecutionRecord` with a controllable cost and `run_id` — for
    the rework and cost-per-completed-task metric tests, where `_worker_record`
    above's fixed `cost=0.0`/`run_id=None` is not enough.
    """
    return learning_journal.WorkerExecutionRecord(
        task=learning_journal.TaskLabel.for_task(task_id),
        duration_ms=1,
        cost_estimate_usd=cost,
        success=True,
        retry_count=0,
        effort="low",
        model_id="claude-sonnet-5",
        model_family="claude",
        run_id=run_id,
        timestamp=timestamp,
    )


def _outcome_record(
    task_id: str,
    *,
    ground_truth: str,
    verdict: str,
    timestamp: str,
    run_id: str | None = None,
) -> Any:
    return learning_journal.OutcomeRecord(
        task=learning_journal.TaskLabel.for_task(task_id),
        ground_truth=ground_truth,  # type: ignore[arg-type]
        verdict=verdict,  # type: ignore[arg-type]
        run_id=run_id,
        timestamp=timestamp,
    )


def _dialogue_record(
    task_id: str,
    *,
    rounds: tuple[Any, ...],
    timestamp: str,
    canaries_planted: int = 0,
    canaries_caught: int = 0,
) -> Any:
    return learning_journal.DialogueQualityRecord(
        task=learning_journal.TaskLabel.for_task(task_id),
        occasion="ambiguity",
        topology="pair",
        rounds=rounds,
        canaries_planted=canaries_planted,
        canaries_caught=canaries_caught,
        timestamp=timestamp,
    )


def _compliance_record(
    session_id: str,
    *,
    violation_count: int,
    timestamp: str,
    session_last_activity: str | None,
    run_id: str | None = None,
) -> Any:
    return learning_journal.ComplianceRecord(
        session_id=session_id,
        total_writes=1,
        code_writes=0,
        routing_declarations=1,
        worker_calls=0,
        violation_count=violation_count,
        declaration_drift_count=0,
        calibration_markers=0,
        code_write_count=0,
        run_id=run_id,
        session_last_activity=session_last_activity,
        timestamp=timestamp,
    )


def _no_data_family_kwargs() -> dict[str, Any]:
    """Every family field as `MetricNoData`, keyed by field name.

    A base a duplicate-name/NaN test overrides one entry of, so each test
    states only the field it means to corrupt rather than re-deriving all
    eight metric names and directions.
    """
    no_data = learning_scoreboard.MetricNoData
    return {
        "discipline": learning_scoreboard.DisciplineMetrics(
            violations_per_session=no_data(
                name="violations_per_session", direction="lower_is_better"
            ),
        ),
        "critique_authenticity": learning_scoreboard.CritiqueAuthenticityMetrics(
            canary_catch_rate=no_data(name="canary_catch_rate", direction="higher_is_better"),
            mean_engagement_count=no_data(
                name="mean_engagement_count", direction="higher_is_better"
            ),
        ),
        "efficiency": learning_scoreboard.EfficiencyMetrics(
            escalation_rate=no_data(name="escalation_rate", direction="lower_is_better"),
            dialogue_non_consensus_rate=no_data(
                name="dialogue_non_consensus_rate", direction="lower_is_better"
            ),
            mean_rework_per_task=no_data(
                name="mean_rework_per_task", direction="lower_is_better"
            ),
            cost_per_completed_task_usd=no_data(
                name="cost_per_completed_task_usd", direction="lower_is_better"
            ),
        ),
        "replay_benchmark": learning_scoreboard.ReplayBenchmarkMetrics(
            mean_benchmark_score=no_data(
                name="mean_benchmark_score", direction="higher_is_better"
            ),
        ),
        "window_days": learning_scoreboard.DEFAULT_WINDOW_DAYS,
        "window_end": _NOW,
        "unreadable_lines": 0,
        "unknown_kind_lines": 0,
    }


class MetricTypeTests(unittest.TestCase):
    """Slice 4 — the metric type's two locks (implementation_plan.md Section 4)."""

    def test_metric_no_data_has_no_value_attribute(self) -> None:
        metric = learning_scoreboard.MetricNoData(
            name="violations_per_session", direction="lower_is_better"
        )
        with self.assertRaises(AttributeError):
            _ = metric.value  # type: ignore[attr-defined]

    def test_a_metric_value_with_an_empty_sample_is_unconstructible(self) -> None:
        with self.assertRaises(ValueError):
            learning_scoreboard.MetricValue(
                name="violations_per_session",
                direction="lower_is_better",
                value=0.0,
                sample_size=0,
            )

    def test_a_metric_direction_outside_the_vocabulary_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            learning_scoreboard.MetricNoData(
                name="violations_per_session",
                direction="sideways",  # type: ignore[arg-type]
            )

    def test_a_non_finite_metric_value_is_unconstructible(self) -> None:
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                learning_scoreboard.MetricValue(
                    name="cost_per_completed_task_usd",
                    direction="lower_is_better",
                    value=value,
                    sample_size=1,
                )

    def test_a_nan_metric_can_never_reach_a_comparison_as_an_improvement(self) -> None:
        # Under IEEE-754 semantics, every NaN comparison evaluates False,
        # which would otherwise falsely classify NaN as `improved` under
        # `lower_is_better` (False == False) or `regressed` under
        # `higher_is_better` (False == True). The public API prevents
        # construction via `MetricValue.__post_init__`, and ticket 28 added
        # an independent defense-in-depth in `_classify_change` returning
        # `indeterminate` if either side carries NaN.
        for direction in ("lower_is_better", "higher_is_better"):
            for baseline_nan, current_nan in (
                (False, True),
                (True, False),
                (True, True),
            ):
                with self.subTest(
                    direction=direction,
                    baseline_nan=baseline_nan,
                    current_nan=current_nan,
                ):
                    baseline = learning_scoreboard.MetricValue(
                        name="violations_per_session"
                        if direction == "lower_is_better"
                        else "mean_benchmark_score",
                        direction=direction,  # type: ignore[arg-type]
                        value=5.0,
                        sample_size=1,
                    )
                    current = learning_scoreboard.MetricValue(
                        name="violations_per_session"
                        if direction == "lower_is_better"
                        else "mean_benchmark_score",
                        direction=direction,  # type: ignore[arg-type]
                        value=5.0,
                        sample_size=1,
                    )
                    if baseline_nan:
                        object.__setattr__(baseline, "value", float("nan"))
                    if current_nan:
                        object.__setattr__(current, "value", float("nan"))
                    self.assertEqual(
                        learning_scoreboard._classify_change(baseline, current),
                        "indeterminate",
                    )

    def test_a_boolean_is_neither_a_metric_value_nor_a_sample_size(self) -> None:
        with self.subTest(field="value"), self.assertRaises(ValueError):
            learning_scoreboard.MetricValue(
                name="mean_rework_per_task",
                direction="lower_is_better",
                value=True,  # type: ignore[arg-type]
                sample_size=1,
            )
        with self.subTest(field="sample_size"), self.assertRaises(ValueError):
            learning_scoreboard.MetricValue(
                name="mean_rework_per_task",
                direction="lower_is_better",
                value=1.0,
                sample_size=True,  # type: ignore[arg-type]
            )


def _resolve_dotted_attribute_path(node: ast.Attribute) -> tuple[str, ...] | None:
    """Resolve a (possibly nested) attribute chain to its full dotted path.

    Walks `.value` until it hits a non-`Attribute` node. Returns `None`
    unless that node is a bare `ast.Name` — a chain rooted in a call result
    or a subscript (`foo().bar`, `x[0].bar`) is not a module-attribute
    access and is out of scope for the clock-call check below.
    """
    parts = [node.attr]
    current = node.value
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return None
    parts.append(current.id)
    return tuple(reversed(parts))


_CLOCK_MODULE_ROOTS = {"datetime", "time"}
_CLOCK_FINAL_ATTRS = {
    "now",
    "utcnow",
    "time",
    "gmtime",
    "monotonic",
    "monotonic_ns",
    "perf_counter",
    "perf_counter_ns",
    "time_ns",
}


def _find_forbidden_clock_reads(tree: ast.AST) -> list[tuple[str, ...]]:
    """Find attribute accesses whose dotted path reads a wall/monotonic clock.

    Matches on the resolved path's root name (`datetime`/`time`) and final
    attribute (a clock-reading method) — covers both a single-dot access
    (`datetime.now()`) and a nested one (`datetime.datetime.now()`), since
    the path is resolved by walking the full `.value` chain rather than
    matching one `Attribute` node in isolation. Matching on the *root* name
    (not "any attribute access ending in `.now`") keeps an unrelated
    `.now()` on some other object from false-positiving.

    Matches every `ast.Attribute` node, not only ones that are a `Call`'s
    `.func` — a bare reference (`x = datetime.now`) is how a clock read gets
    deferred past a call-only check (e.g. stored as
    `dataclasses.field(default_factory=datetime.now)` and invoked later
    through a name this function cannot trace), so it is flagged too. A
    call's `.func` is itself an `ast.Attribute` node, so calls are still
    caught.

    Does NOT cover: an aliased import (`import time as t; t.time()`) — the
    bare root name is the alias `t`, not `time`, and this function does not
    track import bindings; or dynamic access (`getattr(datetime, "now")`).
    """
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        path = _resolve_dotted_attribute_path(node)
        if path is None:
            continue
        if path[0] in _CLOCK_MODULE_ROOTS and path[-1] in _CLOCK_FINAL_ATTRS:
            found.append(path)
    return found


class NoClockTests(unittest.TestCase):
    def test_the_scoreboard_module_reads_no_clock(self) -> None:
        tree = ast.parse(LEARNING_SCOREBOARD_PATH.read_text(encoding="utf-8"))

        self.assertEqual(_find_forbidden_clock_reads(tree), [])

    def test_the_no_clock_guard_catches_a_nested_datetime_module_now_call(self) -> None:
        # The gap a single-dot-only check misses: the outer `Attribute`'s
        # `.value` is itself an `Attribute` (`datetime.datetime`), not a
        # bare `Name`, so a check that only matched `Name.attr` skipped this
        # node entirely.
        tree = ast.parse("import datetime\ndatetime.datetime.now()\n")

        self.assertEqual(_find_forbidden_clock_reads(tree), [("datetime", "datetime", "now")])

    def test_the_no_clock_guard_catches_time_monotonic(self) -> None:
        # The gap a forbidden set of only now/utcnow/time/gmtime misses:
        # `time.monotonic()` is a bare-`Name` attribute access, so it would
        # have matched the old check's shape — it just wasn't in the old
        # check's forbidden set.
        tree = ast.parse("import time\ntime.monotonic()\n")

        self.assertEqual(_find_forbidden_clock_reads(tree), [("time", "monotonic")])

    def test_the_no_clock_guard_catches_an_uncalled_reference(self) -> None:
        # A check gated on `isinstance(node, ast.Call)` skips a bare
        # reference entirely, so a clock read deferred through one (e.g.
        # `dataclasses.field(default_factory=datetime.now)`) goes uncaught.
        tree = ast.parse("x = datetime.now\n")

        self.assertEqual(_find_forbidden_clock_reads(tree), [("datetime", "now")])

    def test_the_no_clock_guard_does_not_flag_an_unrelated_now_method(self) -> None:
        # Root name is `journal`, not `datetime`/`time` — matching on the
        # root keeps this from false-positiving on an unrelated `.now()`.
        tree = ast.parse("journal.now()\n")

        self.assertEqual(_find_forbidden_clock_reads(tree), [])

    def test_the_no_clock_guard_does_not_flag_an_unrelated_final_attribute(self) -> None:
        # Root matches (`datetime`) but the final attribute (`today`) is
        # not in the forbidden set.
        tree = ast.parse("import datetime\ndatetime.date.today()\n")

        self.assertEqual(_find_forbidden_clock_reads(tree), [])

    def test_the_no_clock_guard_does_not_flag_an_aliased_import(self) -> None:
        # Documented gap: the bare root name is the alias `t`, not `time`,
        # and this function does not track import bindings.
        tree = ast.parse("import time as t\nt.time()\n")

        self.assertEqual(_find_forbidden_clock_reads(tree), [])


class NowGuardTests(unittest.TestCase):
    def test_a_naive_now_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            journal = learning_journal.read_journal(root)

        with self.assertRaises(ValueError):
            # DTZ001: the naive value is the point of this test.
            learning_scoreboard.compute_scoreboard(journal, now=datetime(2026, 1, 8))  # noqa: DTZ001


class ScoreboardSkeletonTests(unittest.TestCase):
    """Slice 5 — the empty board and determinism (criteria 2 and 4)."""

    def test_an_empty_journal_produces_a_scoreboard_whose_every_metric_is_no_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            journal = learning_journal.read_journal(root)

        board = learning_scoreboard.compute_scoreboard(journal, now=_NOW)

        # Size asserted before type, so an empty `metrics` tuple could not
        # pass by vacuous truth. Eight: 1 discipline + 2 critique
        # authenticity + 4 efficiency + 1 replay benchmark.
        self.assertEqual(len(board.metrics), 8)
        for metric in board.metrics:
            self.assertIsInstance(metric, learning_scoreboard.MetricNoData)

    def test_the_same_journal_and_the_same_now_produce_equal_scoreboards(self) -> None:
        # Populated, not empty: on an empty journal this would be satisfiable
        # without a single metric having been computed.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record = _worker_record("task-determinism", timestamp="2026-01-01T00:00:00Z")
            self.assertIsNone(learning_journal.append_journal_record(record, root_dir=root))
            journal = learning_journal.read_journal(root)

        self.assertEqual(len(journal.worker_executions), 1)

        first = learning_scoreboard.compute_scoreboard(journal, now=_NOW)
        second = learning_scoreboard.compute_scoreboard(journal, now=_NOW)

        self.assertEqual(first, second)

    def test_duplicate_metric_names_are_unconstructible(self) -> None:
        kwargs = _no_data_family_kwargs()
        kwargs["discipline"] = learning_scoreboard.DisciplineMetrics(
            violations_per_session=learning_scoreboard.MetricNoData(
                # Collides with critique_authenticity.canary_catch_rate below.
                name="canary_catch_rate",
                direction="lower_is_better",
            ),
        )
        with self.assertRaises(ValueError):
            learning_scoreboard.Scoreboard(**kwargs)


class WindowDaysGuardTests(unittest.TestCase):
    """`window_days` must be a strictly positive, non-`bool` int.

    Not in implementation_plan.md's numbered test list — required directly
    by the mission brief for this stage, since an unvalidated `window_days`
    makes the window run backwards or vanish silently.
    """

    def _journal(self) -> Any:
        with tempfile.TemporaryDirectory() as tmp:
            return learning_journal.read_journal(Path(tmp))

    def test_a_negative_window_days_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            learning_scoreboard.compute_scoreboard(self._journal(), now=_NOW, window_days=-5)

    def test_a_zero_window_days_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            learning_scoreboard.compute_scoreboard(self._journal(), now=_NOW, window_days=0)

    def test_a_boolean_window_days_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            learning_scoreboard.compute_scoreboard(
                self._journal(), now=_NOW, window_days=True  # type: ignore[arg-type]
            )


class DisciplineMetricTests(unittest.TestCase):
    """Slice 6 — discipline (implementation_plan.md Section 3.1)."""

    def test_two_audits_of_one_session_count_as_one_session_and_the_later_verdict_wins(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = _compliance_record(
                "session-repeat",
                violation_count=5,
                timestamp="2026-01-03T00:00:00Z",
                session_last_activity="2026-01-03T00:00:00Z",
                run_id="audit-1",
            )
            second = _compliance_record(
                "session-repeat",
                violation_count=1,
                timestamp="2026-01-06T00:00:00Z",
                session_last_activity="2026-01-06T00:00:00Z",
                run_id="audit-2",
            )
            self.assertIsNone(learning_journal.append_journal_record(first, root_dir=root))
            self.assertIsNone(learning_journal.append_journal_record(second, root_dir=root))

            journal = learning_journal.read_journal(root)

        board = learning_scoreboard.compute_scoreboard(journal, now=_NOW)
        metric = board.discipline.violations_per_session
        self.assertIsInstance(metric, learning_scoreboard.MetricValue)
        assert isinstance(metric, learning_scoreboard.MetricValue)
        self.assertEqual(metric.value, 1.0)
        self.assertEqual(metric.sample_size, 1)

    def test_a_compliance_record_without_session_last_activity_is_skipped_not_dated_by_its_audit_time(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            undatable = _compliance_record(
                "session-undatable",
                violation_count=100,
                timestamp="2026-01-05T00:00:00Z",
                session_last_activity=None,
            )
            datable = _compliance_record(
                "session-datable",
                violation_count=3,
                timestamp="2026-01-05T00:00:00Z",
                session_last_activity="2026-01-05T00:00:00Z",
            )
            self.assertIsNone(learning_journal.append_journal_record(undatable, root_dir=root))
            self.assertIsNone(learning_journal.append_journal_record(datable, root_dir=root))

            journal = learning_journal.read_journal(root)

        board = learning_scoreboard.compute_scoreboard(journal, now=_NOW)
        metric = board.discipline.violations_per_session
        assert isinstance(metric, learning_scoreboard.MetricValue)
        self.assertEqual(metric.value, 3.0)
        self.assertEqual(metric.sample_size, 1)

    def test_violations_per_session_is_the_mean_over_reduced_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for session_id, violations in (
                ("session-a", 0),
                ("session-b", 2),
                ("session-c", 4),
            ):
                record = _compliance_record(
                    session_id,
                    violation_count=violations,
                    timestamp="2026-01-05T00:00:00Z",
                    session_last_activity="2026-01-05T00:00:00Z",
                )
                self.assertIsNone(learning_journal.append_journal_record(record, root_dir=root))

            journal = learning_journal.read_journal(root)

        board = learning_scoreboard.compute_scoreboard(journal, now=_NOW)
        metric = board.discipline.violations_per_session
        assert isinstance(metric, learning_scoreboard.MetricValue)
        self.assertEqual(metric.value, 2.0)
        self.assertEqual(metric.sample_size, 3)

    def test_discipline_is_no_data_with_no_datable_compliance_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record = _compliance_record(
                "session-undatable-only",
                violation_count=1,
                timestamp="2026-01-05T00:00:00Z",
                session_last_activity=None,
            )
            self.assertIsNone(learning_journal.append_journal_record(record, root_dir=root))

            journal = learning_journal.read_journal(root)

        board = learning_scoreboard.compute_scoreboard(journal, now=_NOW)
        self.assertIsInstance(
            board.discipline.violations_per_session, learning_scoreboard.MetricNoData
        )


class CritiqueAuthenticityMetricTests(unittest.TestCase):
    """Slice 7 — critique authenticity (implementation_plan.md Section 3.6)."""

    def test_a_canary_probe_does_not_enter_the_engagement_mean(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            probe = _dialogue_record(
                "task-probe",
                rounds=(learning_journal.DialogueRound(verdict="approved", engagement_count=99),),
                timestamp="2026-01-05T00:00:00Z",
                canaries_planted=1,
                canaries_caught=1,
            )
            ordinary = _dialogue_record(
                "task-ordinary",
                rounds=(learning_journal.DialogueRound(verdict="approved", engagement_count=5),),
                timestamp="2026-01-05T00:00:00Z",
            )
            self.assertIsNone(learning_journal.append_journal_record(probe, root_dir=root))
            self.assertIsNone(learning_journal.append_journal_record(ordinary, root_dir=root))

            journal = learning_journal.read_journal(root)

        board = learning_scoreboard.compute_scoreboard(journal, now=_NOW)
        metric = board.critique_authenticity.mean_engagement_count
        assert isinstance(metric, learning_scoreboard.MetricValue)
        self.assertEqual(metric.value, 5.0)
        self.assertEqual(metric.sample_size, 1)

    def test_the_catch_rate_is_computed_over_probes_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            probe_a = _dialogue_record(
                "task-probe-a",
                rounds=(learning_journal.DialogueRound(verdict="approved", engagement_count=1),),
                timestamp="2026-01-05T00:00:00Z",
                canaries_planted=2,
                canaries_caught=1,
            )
            probe_b = _dialogue_record(
                "task-probe-b",
                rounds=(learning_journal.DialogueRound(verdict="approved", engagement_count=1),),
                timestamp="2026-01-05T00:00:00Z",
                canaries_planted=3,
                canaries_caught=3,
            )
            ordinary = _dialogue_record(
                "task-ordinary",
                rounds=(learning_journal.DialogueRound(verdict="approved", engagement_count=50),),
                timestamp="2026-01-05T00:00:00Z",
            )
            for record in (probe_a, probe_b, ordinary):
                self.assertIsNone(learning_journal.append_journal_record(record, root_dir=root))

            journal = learning_journal.read_journal(root)

        board = learning_scoreboard.compute_scoreboard(journal, now=_NOW)
        metric = board.critique_authenticity.canary_catch_rate
        assert isinstance(metric, learning_scoreboard.MetricValue)
        self.assertEqual(metric.value, 0.8)
        self.assertEqual(metric.sample_size, 5)

    def test_the_catch_rate_is_no_data_when_no_probe_was_planted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ordinary = _dialogue_record(
                "task-ordinary-only",
                rounds=(learning_journal.DialogueRound(verdict="approved", engagement_count=1),),
                timestamp="2026-01-05T00:00:00Z",
            )
            self.assertIsNone(learning_journal.append_journal_record(ordinary, root_dir=root))

            journal = learning_journal.read_journal(root)

        board = learning_scoreboard.compute_scoreboard(journal, now=_NOW)
        self.assertIsInstance(
            board.critique_authenticity.canary_catch_rate, learning_scoreboard.MetricNoData
        )

    def test_a_zero_round_dialogue_does_not_read_as_zero_engagement(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            zero_round = _dialogue_record(
                "task-zero-round", rounds=(), timestamp="2026-01-05T00:00:00Z"
            )
            engaged = _dialogue_record(
                "task-engaged",
                rounds=(learning_journal.DialogueRound(verdict="approved", engagement_count=10),),
                timestamp="2026-01-05T00:00:00Z",
            )
            self.assertIsNone(learning_journal.append_journal_record(zero_round, root_dir=root))
            self.assertIsNone(learning_journal.append_journal_record(engaged, root_dir=root))

            journal = learning_journal.read_journal(root)

        board = learning_scoreboard.compute_scoreboard(journal, now=_NOW)
        metric = board.critique_authenticity.mean_engagement_count
        assert isinstance(metric, learning_scoreboard.MetricValue)
        self.assertEqual(metric.value, 10.0)
        self.assertEqual(metric.sample_size, 1)

    def test_an_unparseable_round_is_counted_in_the_engagement_mean(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record = _dialogue_record(
                "task-unparseable",
                rounds=(learning_journal.DialogueRound(verdict="unparseable", engagement_count=0),),
                timestamp="2026-01-05T00:00:00Z",
            )
            self.assertIsNone(learning_journal.append_journal_record(record, root_dir=root))

            journal = learning_journal.read_journal(root)

        board = learning_scoreboard.compute_scoreboard(journal, now=_NOW)
        metric = board.critique_authenticity.mean_engagement_count
        assert isinstance(metric, learning_scoreboard.MetricValue)
        self.assertEqual(metric.value, 0.0)
        self.assertEqual(metric.sample_size, 1)


class ReworkMetricTests(unittest.TestCase):
    """Slice 8 — efficiency: rework (implementation_plan.md Section 3.4)."""

    def test_rework_is_every_run_after_the_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            records = (
                _worker_execution_record(
                    "task-rework-basic", timestamp="2026-01-02T00:00:00Z", run_id="run-a"
                ),
                _worker_execution_record(
                    "task-rework-basic", timestamp="2026-01-04T00:00:00Z", run_id="run-b"
                ),
                _worker_execution_record(
                    "task-rework-basic", timestamp="2026-01-05T00:00:00Z", run_id="run-b"
                ),
            )
            for record in records:
                self.assertIsNone(learning_journal.append_journal_record(record, root_dir=root))

            journal = learning_journal.read_journal(root)

        board = learning_scoreboard.compute_scoreboard(journal, now=_NOW)
        metric = board.efficiency.mean_rework_per_task
        assert isinstance(metric, learning_scoreboard.MetricValue)
        self.assertEqual(metric.value, 1.0)
        self.assertEqual(metric.sample_size, 1)

    def test_rework_is_not_retry_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = _worker_execution_record(
                "task-not-retry", timestamp="2026-01-02T00:00:00Z", run_id="run-a"
            )
            second = _worker_execution_record(
                "task-not-retry", timestamp="2026-01-05T00:00:00Z", run_id="run-b"
            )
            self.assertEqual(first.retry_count, 0)
            self.assertEqual(second.retry_count, 0)
            self.assertIsNone(learning_journal.append_journal_record(first, root_dir=root))
            self.assertIsNone(learning_journal.append_journal_record(second, root_dir=root))

            journal = learning_journal.read_journal(root)

        board = learning_scoreboard.compute_scoreboard(journal, now=_NOW)
        metric = board.efficiency.mean_rework_per_task
        assert isinstance(metric, learning_scoreboard.MetricValue)
        self.assertEqual(metric.value, 1.0)
        self.assertEqual(metric.sample_size, 1)

    def test_a_task_whose_executions_carry_no_run_id_is_excluded_not_counted_as_zero_rework(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ordinary = (
                _worker_execution_record(
                    "task-ordinary-rework", timestamp="2026-01-02T00:00:00Z", run_id="run-a"
                ),
                _worker_execution_record(
                    "task-ordinary-rework", timestamp="2026-01-05T00:00:00Z", run_id="run-b"
                ),
            )
            no_run_id = (
                _worker_execution_record(
                    "task-no-run-id", timestamp="2026-01-03T00:00:00Z", run_id=None
                ),
                _worker_execution_record(
                    "task-no-run-id", timestamp="2026-01-05T00:00:00Z", run_id=None
                ),
            )
            for record in (*ordinary, *no_run_id):
                self.assertIsNone(learning_journal.append_journal_record(record, root_dir=root))

            journal = learning_journal.read_journal(root)

        board = learning_scoreboard.compute_scoreboard(journal, now=_NOW)
        metric = board.efficiency.mean_rework_per_task
        assert isinstance(metric, learning_scoreboard.MetricValue)
        # The wrong implementation reports 0.5 over sample_size == 2; both
        # assertions together are what catches it and say why.
        self.assertEqual(metric.value, 1.0)
        self.assertEqual(metric.sample_size, 1)

    def test_a_run_whose_only_prior_run_predates_the_window_is_still_rework(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            earlier = _worker_execution_record(
                "task-straddle-prior", timestamp="2025-12-15T00:00:00Z", run_id="run-old"
            )
            later = _worker_execution_record(
                "task-straddle-prior", timestamp="2026-01-04T00:00:00Z", run_id="run-new"
            )
            self.assertIsNone(learning_journal.append_journal_record(earlier, root_dir=root))
            self.assertIsNone(learning_journal.append_journal_record(later, root_dir=root))

            journal = learning_journal.read_journal(root)

        board = learning_scoreboard.compute_scoreboard(journal, now=_NOW)
        metric = board.efficiency.mean_rework_per_task
        assert isinstance(metric, learning_scoreboard.MetricValue)
        self.assertEqual(metric.value, 1.0)
        self.assertEqual(metric.sample_size, 1)

    def test_a_straddling_repeat_is_rework_in_the_later_window_and_not_in_the_earlier_one(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first_run = _worker_execution_record(
                "task-straddle", timestamp="2025-12-28T00:00:00Z", run_id="run-first"
            )
            second_run = _worker_execution_record(
                "task-straddle", timestamp="2026-01-05T00:00:00Z", run_id="run-second"
            )
            self.assertIsNone(learning_journal.append_journal_record(first_run, root_dir=root))
            self.assertIsNone(learning_journal.append_journal_record(second_run, root_dir=root))

            journal = learning_journal.read_journal(root)

        early_now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        later_now = _NOW

        early_board = learning_scoreboard.compute_scoreboard(journal, now=early_now)
        later_board = learning_scoreboard.compute_scoreboard(journal, now=later_now)

        early_metric = early_board.efficiency.mean_rework_per_task
        assert isinstance(early_metric, learning_scoreboard.MetricValue)
        self.assertEqual(early_metric.value, 0.0)
        self.assertEqual(early_metric.sample_size, 1)

        later_metric = later_board.efficiency.mean_rework_per_task
        assert isinstance(later_metric, learning_scoreboard.MetricValue)
        self.assertEqual(later_metric.value, 1.0)
        self.assertEqual(later_metric.sample_size, 1)


class CompletionAndCostMetricTests(unittest.TestCase):
    """Slice 9 — efficiency: completion and cost (implementation_plan.md Section 3.4).

    Every assertion here reaches `efficiency.cost_per_completed_task_usd`
    through the public interface — value, sample_size, or MetricNoData.
    """

    def test_a_plan_accepted_record_alone_does_not_complete_a_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outcome = _outcome_record(
                "task-plan-only",
                ground_truth="plan",
                verdict="accepted",
                timestamp="2026-01-05T00:00:00Z",
            )
            execution = _worker_execution_record(
                "task-plan-only", timestamp="2026-01-05T00:00:00Z", cost=1.0
            )
            self.assertIsNone(learning_journal.append_journal_record(outcome, root_dir=root))
            self.assertIsNone(learning_journal.append_journal_record(execution, root_dir=root))

            journal = learning_journal.read_journal(root)

        board = learning_scoreboard.compute_scoreboard(journal, now=_NOW)
        self.assertIsInstance(
            board.efficiency.cost_per_completed_task_usd, learning_scoreboard.MetricNoData
        )

    def test_a_failing_test_record_completes_a_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outcome = _outcome_record(
                "task-failing-tests",
                ground_truth="tests",
                verdict="fail",
                timestamp="2026-01-05T00:00:00Z",
            )
            execution = _worker_execution_record(
                "task-failing-tests", timestamp="2026-01-05T00:00:00Z", cost=2.5
            )
            self.assertIsNone(learning_journal.append_journal_record(outcome, root_dir=root))
            self.assertIsNone(learning_journal.append_journal_record(execution, root_dir=root))

            journal = learning_journal.read_journal(root)

        board = learning_scoreboard.compute_scoreboard(journal, now=_NOW)
        metric = board.efficiency.cost_per_completed_task_usd
        assert isinstance(metric, learning_scoreboard.MetricValue)
        self.assertEqual(metric.value, 2.5)
        self.assertEqual(metric.sample_size, 1)

    def test_a_review_verdict_completes_a_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outcome = _outcome_record(
                "task-review",
                ground_truth="review",
                verdict="approved",
                timestamp="2026-01-05T00:00:00Z",
            )
            execution = _worker_execution_record(
                "task-review", timestamp="2026-01-05T00:00:00Z", cost=3.0
            )
            self.assertIsNone(learning_journal.append_journal_record(outcome, root_dir=root))
            self.assertIsNone(learning_journal.append_journal_record(execution, root_dir=root))

            journal = learning_journal.read_journal(root)

        board = learning_scoreboard.compute_scoreboard(journal, now=_NOW)
        metric = board.efficiency.cost_per_completed_task_usd
        assert isinstance(metric, learning_scoreboard.MetricValue)
        self.assertEqual(metric.value, 3.0)
        self.assertEqual(metric.sample_size, 1)

    def test_a_stalemate_resolution_alone_does_not_complete_a_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outcome = _outcome_record(
                "task-stalemate-only",
                ground_truth="stalemate_resolution",
                verdict="human",
                timestamp="2026-01-05T00:00:00Z",
            )
            execution = _worker_execution_record(
                "task-stalemate-only", timestamp="2026-01-05T00:00:00Z", cost=4.0
            )
            self.assertIsNone(learning_journal.append_journal_record(outcome, root_dir=root))
            self.assertIsNone(learning_journal.append_journal_record(execution, root_dir=root))

            journal = learning_journal.read_journal(root)

        board = learning_scoreboard.compute_scoreboard(journal, now=_NOW)
        self.assertIsInstance(
            board.efficiency.cost_per_completed_task_usd, learning_scoreboard.MetricNoData
        )

    def test_a_task_with_no_outcome_record_is_not_completed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            execution = _worker_execution_record(
                "task-no-outcome", timestamp="2026-01-05T00:00:00Z", cost=9.0
            )
            self.assertIsNone(learning_journal.append_journal_record(execution, root_dir=root))

            journal = learning_journal.read_journal(root)

        board = learning_scoreboard.compute_scoreboard(journal, now=_NOW)
        self.assertIsInstance(
            board.efficiency.cost_per_completed_task_usd, learning_scoreboard.MetricNoData
        )

    def test_cost_sums_across_every_run_of_a_completed_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outcome = _outcome_record(
                "task-cost-sum",
                ground_truth="tests",
                verdict="pass",
                timestamp="2026-01-05T00:00:00Z",
            )
            run_x = _worker_execution_record(
                "task-cost-sum", timestamp="2026-01-03T00:00:00Z", cost=1.0, run_id="run-x"
            )
            run_y = _worker_execution_record(
                "task-cost-sum", timestamp="2026-01-04T00:00:00Z", cost=2.0, run_id="run-y"
            )
            for record in (outcome, run_x, run_y):
                self.assertIsNone(learning_journal.append_journal_record(record, root_dir=root))

            journal = learning_journal.read_journal(root)

        board = learning_scoreboard.compute_scoreboard(journal, now=_NOW)
        metric = board.efficiency.cost_per_completed_task_usd
        assert isinstance(metric, learning_scoreboard.MetricValue)
        self.assertEqual(metric.value, 3.0)
        self.assertEqual(metric.sample_size, 1)

    def test_cost_includes_an_execution_older_than_the_window_when_completion_falls_inside_it(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outcome = _outcome_record(
                "task-old-execution",
                ground_truth="tests",
                verdict="pass",
                timestamp="2026-01-05T00:00:00Z",
            )
            older = _worker_execution_record(
                "task-old-execution", timestamp="2025-12-01T00:00:00Z", cost=1.0, run_id="run-old"
            )
            windowed = _worker_execution_record(
                "task-old-execution", timestamp="2026-01-06T00:00:00Z", cost=2.0, run_id="run-new"
            )
            for record in (outcome, older, windowed):
                self.assertIsNone(learning_journal.append_journal_record(record, root_dir=root))

            journal = learning_journal.read_journal(root)

        board = learning_scoreboard.compute_scoreboard(journal, now=_NOW)
        metric = board.efficiency.cost_per_completed_task_usd
        assert isinstance(metric, learning_scoreboard.MetricValue)
        self.assertEqual(metric.value, 3.0)
        self.assertEqual(metric.sample_size, 1)

    def test_cost_excludes_an_execution_stamped_after_now_so_a_historical_board_is_reproducible(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outcome = _outcome_record(
                "task-future-execution",
                ground_truth="tests",
                verdict="pass",
                timestamp="2026-01-05T00:00:00Z",
            )
            earlier = _worker_execution_record(
                "task-future-execution",
                timestamp="2026-01-03T00:00:00Z",
                cost=1.00,
                run_id="run-earlier",
            )
            future = _worker_execution_record(
                "task-future-execution",
                timestamp="2026-01-09T00:00:00Z",
                cost=10.00,
                run_id="run-future",
            )
            for record in (outcome, earlier, future):
                self.assertIsNone(learning_journal.append_journal_record(record, root_dir=root))

            journal = learning_journal.read_journal(root)

        board = learning_scoreboard.compute_scoreboard(journal, now=_NOW)
        metric = board.efficiency.cost_per_completed_task_usd
        assert isinstance(metric, learning_scoreboard.MetricValue)
        self.assertEqual(metric.value, 1.00)
        self.assertEqual(metric.sample_size, 1)

    def test_recomputing_an_old_board_from_a_longer_journal_gives_the_same_answer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outcome = _outcome_record(
                "task-repro",
                ground_truth="tests",
                verdict="pass",
                timestamp="2026-01-05T00:00:00Z",
            )
            earlier = _worker_execution_record(
                "task-repro", timestamp="2026-01-03T00:00:00Z", cost=1.0, run_id="run-earlier"
            )
            self.assertIsNone(learning_journal.append_journal_record(outcome, root_dir=root))
            self.assertIsNone(learning_journal.append_journal_record(earlier, root_dir=root))

            journal_before = learning_journal.read_journal(root)
            board_before = learning_scoreboard.compute_scoreboard(journal_before, now=_NOW)

            later_run = _worker_execution_record(
                "task-repro", timestamp="2026-02-01T00:00:00Z", cost=99.0, run_id="run-later"
            )
            self.assertIsNone(learning_journal.append_journal_record(later_run, root_dir=root))

            journal_after = learning_journal.read_journal(root)
            board_after = learning_scoreboard.compute_scoreboard(journal_after, now=_NOW)

        self.assertEqual(board_before, board_after)

    def test_a_completed_task_with_no_worker_execution_is_excluded_not_costed_at_zero(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            uncosted_outcome = _outcome_record(
                "task-no-execution",
                ground_truth="tests",
                verdict="pass",
                timestamp="2026-01-05T00:00:00Z",
            )
            costed_outcome = _outcome_record(
                "task-with-execution",
                ground_truth="tests",
                verdict="pass",
                timestamp="2026-01-05T00:00:00Z",
            )
            costed_execution = _worker_execution_record(
                "task-with-execution", timestamp="2026-01-05T00:00:00Z", cost=5.0
            )
            for record in (uncosted_outcome, costed_outcome, costed_execution):
                self.assertIsNone(learning_journal.append_journal_record(record, root_dir=root))

            journal = learning_journal.read_journal(root)

        board = learning_scoreboard.compute_scoreboard(journal, now=_NOW)
        metric = board.efficiency.cost_per_completed_task_usd
        assert isinstance(metric, learning_scoreboard.MetricValue)
        self.assertEqual(metric.value, 5.0)
        self.assertEqual(metric.sample_size, 1)


class DialogueNonConsensusRateTests(unittest.TestCase):
    """Slice 10 — efficiency: dialogue non-consensus rate and the escalation
    rate that has no source (implementation_plan.md Section 3.2).
    """

    def test_dialogue_non_consensus_rate_is_the_non_consensus_share_of_debated_dialogues(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            consensus = _dialogue_record(
                "task-consensus",
                rounds=(learning_journal.DialogueRound(verdict="approved", engagement_count=1),),
                timestamp="2026-01-05T00:00:00Z",
            )
            non_consensus = _dialogue_record(
                "task-non-consensus",
                rounds=(learning_journal.DialogueRound(verdict="revise", engagement_count=1),),
                timestamp="2026-01-05T00:00:00Z",
            )
            self.assertIsNone(learning_journal.append_journal_record(consensus, root_dir=root))
            self.assertIsNone(
                learning_journal.append_journal_record(non_consensus, root_dir=root)
            )

            journal = learning_journal.read_journal(root)

        board = learning_scoreboard.compute_scoreboard(journal, now=_NOW)
        metric = board.efficiency.dialogue_non_consensus_rate
        assert isinstance(metric, learning_scoreboard.MetricValue)
        self.assertEqual(metric.value, 0.5)
        self.assertEqual(metric.sample_size, 2)

    def test_a_dialogue_that_approves_after_revising_is_not_non_consensus(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record = _dialogue_record(
                "task-revise-then-approve",
                rounds=(
                    learning_journal.DialogueRound(verdict="revise", engagement_count=1),
                    learning_journal.DialogueRound(verdict="approved", engagement_count=2),
                ),
                timestamp="2026-01-05T00:00:00Z",
            )
            self.assertIsNone(learning_journal.append_journal_record(record, root_dir=root))

            journal = learning_journal.read_journal(root)

        board = learning_scoreboard.compute_scoreboard(journal, now=_NOW)
        metric = board.efficiency.dialogue_non_consensus_rate
        assert isinstance(metric, learning_scoreboard.MetricValue)
        self.assertEqual(metric.value, 0.0)
        self.assertEqual(metric.sample_size, 1)

    def test_a_canary_probe_is_in_neither_half_of_the_non_consensus_rate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            probe = _dialogue_record(
                "task-probe-non-consensus-shaped",
                rounds=(learning_journal.DialogueRound(verdict="revise", engagement_count=1),),
                timestamp="2026-01-05T00:00:00Z",
                canaries_planted=1,
                canaries_caught=1,
            )
            ordinary = _dialogue_record(
                "task-ordinary-consensus",
                rounds=(learning_journal.DialogueRound(verdict="approved", engagement_count=1),),
                timestamp="2026-01-05T00:00:00Z",
            )
            self.assertIsNone(learning_journal.append_journal_record(probe, root_dir=root))
            self.assertIsNone(learning_journal.append_journal_record(ordinary, root_dir=root))

            journal = learning_journal.read_journal(root)

        board = learning_scoreboard.compute_scoreboard(journal, now=_NOW)
        metric = board.efficiency.dialogue_non_consensus_rate
        assert isinstance(metric, learning_scoreboard.MetricValue)
        self.assertEqual(metric.value, 0.0)
        self.assertEqual(metric.sample_size, 1)

    def test_a_zero_round_dialogue_is_in_neither_half_of_the_non_consensus_rate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            zero_round = _dialogue_record(
                "task-zero-round-non-consensus", rounds=(), timestamp="2026-01-05T00:00:00Z"
            )
            ordinary = _dialogue_record(
                "task-ordinary-consensus-2",
                rounds=(learning_journal.DialogueRound(verdict="approved", engagement_count=1),),
                timestamp="2026-01-05T00:00:00Z",
            )
            self.assertIsNone(learning_journal.append_journal_record(zero_round, root_dir=root))
            self.assertIsNone(learning_journal.append_journal_record(ordinary, root_dir=root))

            journal = learning_journal.read_journal(root)

        board = learning_scoreboard.compute_scoreboard(journal, now=_NOW)
        metric = board.efficiency.dialogue_non_consensus_rate
        assert isinstance(metric, learning_scoreboard.MetricValue)
        self.assertEqual(metric.value, 0.0)
        self.assertEqual(metric.sample_size, 1)

    def test_the_non_consensus_rate_is_no_data_when_no_dialogue_debated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            journal = learning_journal.read_journal(root)

        board = learning_scoreboard.compute_scoreboard(journal, now=_NOW)
        self.assertIsInstance(
            board.efficiency.dialogue_non_consensus_rate, learning_scoreboard.MetricNoData
        )

    def test_escalation_rate_is_no_data_even_over_a_journal_full_of_non_consensus_dialogues(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            consensus = _dialogue_record(
                "task-consensus-53a",
                rounds=(learning_journal.DialogueRound(verdict="approved", engagement_count=1),),
                timestamp="2026-01-05T00:00:00Z",
            )
            non_consensus = _dialogue_record(
                "task-non-consensus-53a",
                rounds=(learning_journal.DialogueRound(verdict="revise", engagement_count=1),),
                timestamp="2026-01-05T00:00:00Z",
            )
            self.assertIsNone(learning_journal.append_journal_record(consensus, root_dir=root))
            self.assertIsNone(
                learning_journal.append_journal_record(non_consensus, root_dir=root)
            )

            journal = learning_journal.read_journal(root)

        board = learning_scoreboard.compute_scoreboard(journal, now=_NOW)
        self.assertIsInstance(board.efficiency.escalation_rate, learning_scoreboard.MetricNoData)
        self.assertIsInstance(
            board.efficiency.dialogue_non_consensus_rate, learning_scoreboard.MetricValue
        )
        self.assertNotEqual(
            board.efficiency.escalation_rate.name,
            board.efficiency.dialogue_non_consensus_rate.name,
        )


class WindowTests(unittest.TestCase):
    """Slice 11 — the window (implementation_plan.md Sections 3.5, 3.4)."""

    def test_a_record_older_than_the_window_is_excluded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record = _dialogue_record(
                "task-older-than-window",
                rounds=(learning_journal.DialogueRound(verdict="approved", engagement_count=1),),
                timestamp="2025-12-01T00:00:00Z",
            )
            self.assertIsNone(learning_journal.append_journal_record(record, root_dir=root))

            journal = learning_journal.read_journal(root)

        board = learning_scoreboard.compute_scoreboard(journal, now=_NOW)
        self.assertIsInstance(
            board.critique_authenticity.mean_engagement_count, learning_scoreboard.MetricNoData
        )

    def test_a_record_stamped_after_now_is_excluded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record = _dialogue_record(
                "task-after-now",
                rounds=(learning_journal.DialogueRound(verdict="approved", engagement_count=1),),
                timestamp="2026-01-09T00:00:00Z",
            )
            self.assertIsNone(learning_journal.append_journal_record(record, root_dir=root))

            journal = learning_journal.read_journal(root)

        board = learning_scoreboard.compute_scoreboard(journal, now=_NOW)
        self.assertIsInstance(
            board.critique_authenticity.mean_engagement_count, learning_scoreboard.MetricNoData
        )

    def test_the_window_is_half_open_so_two_adjacent_windows_do_not_double_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            boundary = _dialogue_record(
                "task-boundary",
                rounds=(learning_journal.DialogueRound(verdict="approved", engagement_count=7),),
                timestamp="2026-01-01T00:00:00Z",
            )
            self.assertIsNone(learning_journal.append_journal_record(boundary, root_dir=root))

            journal = learning_journal.read_journal(root)

        early_now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        late_now = _NOW

        early_board = learning_scoreboard.compute_scoreboard(journal, now=early_now)
        late_board = learning_scoreboard.compute_scoreboard(journal, now=late_now)

        early_metric = early_board.critique_authenticity.mean_engagement_count
        assert isinstance(early_metric, learning_scoreboard.MetricValue)
        self.assertEqual(early_metric.value, 7.0)
        self.assertEqual(early_metric.sample_size, 1)

        self.assertIsInstance(
            late_board.critique_authenticity.mean_engagement_count,
            learning_scoreboard.MetricNoData,
        )

    def test_a_non_default_window_days_is_honoured_and_recorded_on_the_board(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record = _dialogue_record(
                "task-window-span",
                rounds=(learning_journal.DialogueRound(verdict="approved", engagement_count=9),),
                timestamp="2026-01-03T00:00:00Z",
            )
            self.assertIsNone(learning_journal.append_journal_record(record, root_dir=root))

            journal = learning_journal.read_journal(root)

        narrow_board = learning_scoreboard.compute_scoreboard(journal, now=_NOW, window_days=3)
        wide_board = learning_scoreboard.compute_scoreboard(journal, now=_NOW, window_days=7)

        self.assertEqual(narrow_board.window_days, 3)
        self.assertIsInstance(
            narrow_board.critique_authenticity.mean_engagement_count,
            learning_scoreboard.MetricNoData,
        )

        self.assertEqual(wide_board.window_days, 7)
        wide_metric = wide_board.critique_authenticity.mean_engagement_count
        assert isinstance(wide_metric, learning_scoreboard.MetricValue)
        self.assertEqual(wide_metric.value, 9.0)
        self.assertEqual(wide_metric.sample_size, 1)

    def test_rework_counts_only_windowed_runs_while_cost_sums_the_whole_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_a = _worker_execution_record(
                "task-asymmetry", timestamp="2025-12-18T00:00:00Z", cost=1.0, run_id="run-a"
            )
            run_b = _worker_execution_record(
                "task-asymmetry", timestamp="2025-12-25T00:00:00Z", cost=2.0, run_id="run-b"
            )
            run_c = _worker_execution_record(
                "task-asymmetry", timestamp="2026-01-05T00:00:00Z", cost=4.0, run_id="run-c"
            )
            outcome = _outcome_record(
                "task-asymmetry",
                ground_truth="tests",
                verdict="pass",
                timestamp="2026-01-06T00:00:00Z",
            )
            for record in (run_a, run_b, run_c, outcome):
                self.assertIsNone(learning_journal.append_journal_record(record, root_dir=root))

            journal = learning_journal.read_journal(root)

        board = learning_scoreboard.compute_scoreboard(journal, now=_NOW)

        rework = board.efficiency.mean_rework_per_task
        assert isinstance(rework, learning_scoreboard.MetricValue)
        self.assertEqual(rework.value, 1.0)
        self.assertEqual(rework.sample_size, 1)

        cost = board.efficiency.cost_per_completed_task_usd
        assert isinstance(cost, learning_scoreboard.MetricValue)
        self.assertEqual(cost.value, 7.0)
        self.assertEqual(cost.sample_size, 1)

    def test_the_prefix_cut_applies_to_every_family_not_only_to_the_windowed_metrics(
        self,
    ) -> None:
        def _write_baseline(root: Path) -> None:
            baseline = _dialogue_record(
                "task-baseline",
                rounds=(learning_journal.DialogueRound(verdict="approved", engagement_count=3),),
                timestamp="2026-01-05T00:00:00Z",
            )
            assert learning_journal.append_journal_record(baseline, root_dir=root) is None

        with tempfile.TemporaryDirectory() as tmp_a:
            root_a = Path(tmp_a)
            _write_baseline(root_a)
            journal_a = learning_journal.read_journal(root_a)

        with tempfile.TemporaryDirectory() as tmp_b:
            root_b = Path(tmp_b)
            _write_baseline(root_b)

            future = "2026-01-09T00:00:00Z"
            future_worker = _worker_execution_record(
                "task-future-worker", timestamp=future, cost=1.0, run_id="run-future"
            )
            future_outcome = _outcome_record(
                "task-future-outcome", ground_truth="tests", verdict="pass", timestamp=future
            )
            future_dialogue = _dialogue_record(
                "task-future-dialogue",
                rounds=(learning_journal.DialogueRound(verdict="approved", engagement_count=1),),
                timestamp=future,
            )
            future_compliance = _compliance_record(
                "session-future",
                violation_count=1,
                timestamp=future,
                session_last_activity=future,
            )
            for record in (
                future_worker,
                future_outcome,
                future_dialogue,
                future_compliance,
            ):
                self.assertIsNone(learning_journal.append_journal_record(record, root_dir=root_b))

            journal_b = learning_journal.read_journal(root_b)

        board_a = learning_scoreboard.compute_scoreboard(journal_a, now=_NOW)
        board_b = learning_scoreboard.compute_scoreboard(journal_b, now=_NOW)

        self.assertEqual(board_a, board_b)


def _comparison_board(
    *,
    window_days: int = learning_scoreboard.DEFAULT_WINDOW_DAYS,
    **family_overrides: Any,
) -> Any:
    """A `Scoreboard` built directly through its public constructors, every
    metric `MetricNoData` unless a family is overridden — for Slice 12's
    comparison tests, which exercise `compare_scoreboards` against
    directly-built boards rather than journal-derived ones. Reuses
    `_no_data_family_kwargs`'s all-no-data base exactly as
    `test_duplicate_metric_names_are_unconstructible` already does.
    """
    kwargs = _no_data_family_kwargs()
    kwargs["window_days"] = window_days
    kwargs.update(family_overrides)
    return learning_scoreboard.Scoreboard(**kwargs)


class ScoreboardComparisonTests(unittest.TestCase):
    """Slice 12 — the comparison (implementation_plan.md Section 5.2, 5.3)."""

    def test_a_rising_violation_rate_is_a_regression_not_an_improvement(self) -> None:
        # The test this ticket exists to make impossible to fail silently: a
        # bare `current.value > baseline.value` reads a rising lower-is-
        # better metric as `improved`.
        baseline = _comparison_board(
            discipline=learning_scoreboard.DisciplineMetrics(
                violations_per_session=learning_scoreboard.MetricValue(
                    name="violations_per_session",
                    direction="lower_is_better",
                    value=1.0,
                    sample_size=3,
                ),
            ),
        )
        current = _comparison_board(
            discipline=learning_scoreboard.DisciplineMetrics(
                violations_per_session=learning_scoreboard.MetricValue(
                    name="violations_per_session",
                    direction="lower_is_better",
                    value=2.0,
                    sample_size=3,
                ),
            ),
        )

        comparison = learning_scoreboard.compare_scoreboards(baseline, current)

        self.assertIn("violations_per_session", comparison.regressed)
        self.assertNotIn("violations_per_session", comparison.improved)

    def test_a_falling_violation_rate_is_an_improvement(self) -> None:
        baseline = _comparison_board(
            discipline=learning_scoreboard.DisciplineMetrics(
                violations_per_session=learning_scoreboard.MetricValue(
                    name="violations_per_session",
                    direction="lower_is_better",
                    value=2.0,
                    sample_size=3,
                ),
            ),
        )
        current = _comparison_board(
            discipline=learning_scoreboard.DisciplineMetrics(
                violations_per_session=learning_scoreboard.MetricValue(
                    name="violations_per_session",
                    direction="lower_is_better",
                    value=1.0,
                    sample_size=3,
                ),
            ),
        )

        comparison = learning_scoreboard.compare_scoreboards(baseline, current)

        self.assertIn("violations_per_session", comparison.improved)
        self.assertNotIn("violations_per_session", comparison.regressed)

    def test_a_rising_canary_catch_rate_is_an_improvement(self) -> None:
        # The opposite direction from test 59/60: a `>`-only implementation
        # cannot pass both this test and test 59, since it would read every
        # rise as an improvement regardless of direction.
        baseline = _comparison_board(
            critique_authenticity=learning_scoreboard.CritiqueAuthenticityMetrics(
                canary_catch_rate=learning_scoreboard.MetricValue(
                    name="canary_catch_rate",
                    direction="higher_is_better",
                    value=0.5,
                    sample_size=4,
                ),
                mean_engagement_count=learning_scoreboard.MetricNoData(
                    name="mean_engagement_count", direction="higher_is_better"
                ),
            ),
        )
        current = _comparison_board(
            critique_authenticity=learning_scoreboard.CritiqueAuthenticityMetrics(
                canary_catch_rate=learning_scoreboard.MetricValue(
                    name="canary_catch_rate",
                    direction="higher_is_better",
                    value=0.8,
                    sample_size=4,
                ),
                mean_engagement_count=learning_scoreboard.MetricNoData(
                    name="mean_engagement_count", direction="higher_is_better"
                ),
            ),
        )

        comparison = learning_scoreboard.compare_scoreboards(baseline, current)

        self.assertIn("canary_catch_rate", comparison.improved)
        self.assertNotIn("canary_catch_rate", comparison.regressed)

    def test_an_unchanged_metric_holds(self) -> None:
        baseline = _comparison_board(
            discipline=learning_scoreboard.DisciplineMetrics(
                violations_per_session=learning_scoreboard.MetricValue(
                    name="violations_per_session",
                    direction="lower_is_better",
                    value=1.0,
                    sample_size=3,
                ),
            ),
        )
        current = _comparison_board(
            discipline=learning_scoreboard.DisciplineMetrics(
                violations_per_session=learning_scoreboard.MetricValue(
                    name="violations_per_session",
                    direction="lower_is_better",
                    value=1.0,
                    sample_size=9,
                ),
            ),
        )

        comparison = learning_scoreboard.compare_scoreboards(baseline, current)

        self.assertIn("violations_per_session", comparison.held)

    def test_no_data_on_either_side_is_indeterminate(self) -> None:
        measured = learning_scoreboard.MetricValue(
            name="violations_per_session", direction="lower_is_better", value=1.0, sample_size=1
        )
        no_data = learning_scoreboard.MetricNoData(
            name="violations_per_session", direction="lower_is_better"
        )
        cases: dict[str, tuple[learning_scoreboard.Metric, learning_scoreboard.Metric]] = {
            "baseline_only_no_data": (no_data, measured),
            "current_only_no_data": (measured, no_data),
            "neither_has_data": (no_data, no_data),
        }
        for label, (baseline_metric, current_metric) in cases.items():
            with self.subTest(case=label):
                baseline = _comparison_board(
                    discipline=learning_scoreboard.DisciplineMetrics(
                        violations_per_session=baseline_metric
                    ),
                )
                current = _comparison_board(
                    discipline=learning_scoreboard.DisciplineMetrics(
                        violations_per_session=current_metric
                    ),
                )

                comparison = learning_scoreboard.compare_scoreboards(baseline, current)

                self.assertIn("violations_per_session", comparison.indeterminate)
                self.assertNotIn("violations_per_session", comparison.improved)
                self.assertNotIn("violations_per_session", comparison.regressed)

    def test_has_regression_is_true_when_exactly_one_metric_regressed(self) -> None:
        baseline = _comparison_board(
            discipline=learning_scoreboard.DisciplineMetrics(
                violations_per_session=learning_scoreboard.MetricValue(
                    name="violations_per_session",
                    direction="lower_is_better",
                    value=1.0,
                    sample_size=3,
                ),
            ),
        )
        current = _comparison_board(
            discipline=learning_scoreboard.DisciplineMetrics(
                violations_per_session=learning_scoreboard.MetricValue(
                    name="violations_per_session",
                    direction="lower_is_better",
                    value=2.0,
                    sample_size=3,
                ),
            ),
        )

        comparison = learning_scoreboard.compare_scoreboards(baseline, current)

        self.assertTrue(comparison.has_regression)
        self.assertEqual(comparison.regressed, ("violations_per_session",))

    def test_every_metric_appears_in_exactly_one_of_the_four_buckets(self) -> None:
        # One metric each of regressed, improved, held; the remaining five
        # stay all-no-data (indeterminate). Bucket sizes must sum to the
        # board's full metric count, not merely each be non-empty, so no
        # metric can be silently dropped from the comparison.
        baseline = _comparison_board(
            discipline=learning_scoreboard.DisciplineMetrics(
                violations_per_session=learning_scoreboard.MetricValue(
                    name="violations_per_session",
                    direction="lower_is_better",
                    value=1.0,
                    sample_size=1,
                ),
            ),
            critique_authenticity=learning_scoreboard.CritiqueAuthenticityMetrics(
                canary_catch_rate=learning_scoreboard.MetricValue(
                    name="canary_catch_rate",
                    direction="higher_is_better",
                    value=0.5,
                    sample_size=1,
                ),
                mean_engagement_count=learning_scoreboard.MetricValue(
                    name="mean_engagement_count",
                    direction="higher_is_better",
                    value=3.0,
                    sample_size=1,
                ),
            ),
        )
        current = _comparison_board(
            discipline=learning_scoreboard.DisciplineMetrics(
                # Regresses: lower_is_better, value rose.
                violations_per_session=learning_scoreboard.MetricValue(
                    name="violations_per_session",
                    direction="lower_is_better",
                    value=2.0,
                    sample_size=1,
                ),
            ),
            critique_authenticity=learning_scoreboard.CritiqueAuthenticityMetrics(
                # Improves: higher_is_better, value rose.
                canary_catch_rate=learning_scoreboard.MetricValue(
                    name="canary_catch_rate",
                    direction="higher_is_better",
                    value=0.8,
                    sample_size=1,
                ),
                # Holds: unchanged value.
                mean_engagement_count=learning_scoreboard.MetricValue(
                    name="mean_engagement_count",
                    direction="higher_is_better",
                    value=3.0,
                    sample_size=1,
                ),
            ),
        )

        comparison = learning_scoreboard.compare_scoreboards(baseline, current)

        self.assertEqual(len(baseline.metrics), 8)
        bucket_total = (
            len(comparison.improved)
            + len(comparison.held)
            + len(comparison.regressed)
            + len(comparison.indeterminate)
        )
        self.assertEqual(bucket_total, len(baseline.metrics))
        self.assertEqual(comparison.regressed, ("violations_per_session",))
        self.assertEqual(comparison.improved, ("canary_catch_rate",))
        self.assertEqual(comparison.held, ("mean_engagement_count",))
        self.assertEqual(len(comparison.indeterminate), 5)

    def test_comparing_boards_of_different_windows_raises(self) -> None:
        baseline = _comparison_board(window_days=7)
        current = _comparison_board(window_days=30)

        with self.assertRaises(ValueError):
            learning_scoreboard.compare_scoreboards(baseline, current)

    def test_comparing_boards_whose_metric_names_differ_raises(self) -> None:
        baseline = _comparison_board()
        current = _comparison_board(
            discipline=learning_scoreboard.DisciplineMetrics(
                violations_per_session=learning_scoreboard.MetricNoData(
                    # Not `violations_per_session` — the name set now differs
                    # from `baseline`'s, without touching direction.
                    name="extra_metric_name",
                    direction="lower_is_better",
                ),
            ),
        )

        with self.assertRaises(ValueError):
            learning_scoreboard.compare_scoreboards(baseline, current)

    def test_comparing_boards_whose_metric_directions_differ_raises(self) -> None:
        # Objection 9: the name-set guard alone lets this through, because
        # both boards carry the same name set — only `violations_per_session`'s
        # `direction` differs. Without this test, an implementation could
        # ship only the name-set half of Section 5.3's guard and stay green.
        baseline = _comparison_board()
        current = _comparison_board(
            discipline=learning_scoreboard.DisciplineMetrics(
                violations_per_session=learning_scoreboard.MetricNoData(
                    name="violations_per_session",
                    direction="higher_is_better",
                ),
            ),
        )

        with self.assertRaises(ValueError):
            learning_scoreboard.compare_scoreboards(baseline, current)


def _write_fully_populated_journal(root: Path) -> None:
    """One record feeding each of Slice 13's "fed" families, reused across
    tests 69-71: discipline (a compliance record), critique authenticity (an
    ordinary dialogue), and efficiency's `dialogue_non_consensus_rate`,
    `mean_rework_per_task` (two runs of one task), and
    `cost_per_completed_task_usd` (a `tests` outcome for a second task).
    `escalation_rate` has no producer here or anywhere — see
    implementation_plan.md Section 3.2.1 — and stays `MetricNoData` by
    construction, not because this fixture forgot it. `mean_benchmark_score`
    does have a producer since ticket 26 (`acceptance_gate.py`); this fixture
    simply carries no `ReplayBenchmarkRecord`, so it stays `MetricNoData` too
    — by omission, not by construction. See `ReplayBenchmarkFamilyTests`
    below for the fed case.
    """
    records: tuple[Any, ...] = (
        _compliance_record(
            "session-e2e",
            violation_count=2,
            timestamp="2026-01-05T00:00:00Z",
            session_last_activity="2026-01-05T00:00:00Z",
        ),
        _dialogue_record(
            "task-dialogue-e2e",
            rounds=(learning_journal.DialogueRound(verdict="approved", engagement_count=3),),
            timestamp="2026-01-05T00:00:00Z",
        ),
        _worker_execution_record(
            "task-rework-e2e", timestamp="2026-01-04T00:00:00Z", cost=1.0, run_id="run-1"
        ),
        _worker_execution_record(
            "task-rework-e2e", timestamp="2026-01-05T00:00:00Z", cost=1.0, run_id="run-2"
        ),
        _worker_execution_record(
            "task-cost-e2e", timestamp="2026-01-04T00:00:00Z", cost=3.0, run_id="run-cost"
        ),
        _outcome_record(
            "task-cost-e2e",
            ground_truth="tests",
            verdict="pass",
            timestamp="2026-01-05T00:00:00Z",
            run_id="run-cost",
        ),
    )
    for record in records:
        assert learning_journal.append_journal_record(record, root_dir=root) is None


class EndToEndTests(unittest.TestCase):
    """Slice 13 — the replay benchmark and the end-to-end path."""

    def test_the_replay_benchmark_family_is_no_data_when_unfed(self) -> None:
        """Not "until ticket 26" any more: ticket 26 gave this family a real
        producer (`acceptance_gate.py`). It is still `MetricNoData` here
        because `_write_fully_populated_journal` carries no
        `ReplayBenchmarkRecord` at all — the same reason
        `violations_per_session` would read `MetricNoData` from a journal
        with no `ComplianceRecord`, not a special case."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_fully_populated_journal(root)
            journal = learning_journal.read_journal(root)

        board = learning_scoreboard.compute_scoreboard(journal, now=_NOW)

        self.assertIsInstance(
            board.replay_benchmark.mean_benchmark_score, learning_scoreboard.MetricNoData
        )

    def test_read_scoreboard_reaches_the_same_answer_as_read_then_compute(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_fully_populated_journal(root)

            via_read_scoreboard = learning_scoreboard.read_scoreboard(root, now=_NOW)

            journal = learning_journal.read_journal(root)
        via_read_then_compute = learning_scoreboard.compute_scoreboard(journal, now=_NOW)

        self.assertEqual(via_read_scoreboard, via_read_then_compute)

    def test_a_populated_journal_produces_at_least_one_measured_metric_in_each_fed_family(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_fully_populated_journal(root)
            journal = learning_journal.read_journal(root)

        board = learning_scoreboard.compute_scoreboard(journal, now=_NOW)

        self.assertIsInstance(
            board.discipline.violations_per_session, learning_scoreboard.MetricValue
        )
        self.assertIsInstance(
            board.critique_authenticity.mean_engagement_count, learning_scoreboard.MetricValue
        )
        self.assertIsInstance(
            board.efficiency.dialogue_non_consensus_rate, learning_scoreboard.MetricValue
        )
        self.assertIsInstance(
            board.efficiency.mean_rework_per_task, learning_scoreboard.MetricValue
        )
        self.assertIsInstance(
            board.efficiency.cost_per_completed_task_usd, learning_scoreboard.MetricValue
        )
        # `escalation_rate` is excluded from "fed" by construction (no
        # producer exists anywhere); `mean_benchmark_score` is excluded by
        # this fixture's own omission (it carries no `ReplayBenchmarkRecord`)
        # — see `_write_fully_populated_journal`'s docstring and
        # `ReplayBenchmarkFamilyTests` below for the fed case.
        self.assertIsInstance(
            board.efficiency.escalation_rate, learning_scoreboard.MetricNoData
        )
        self.assertIsInstance(
            board.replay_benchmark.mean_benchmark_score, learning_scoreboard.MetricNoData
        )


class ReplayBenchmarkFamilyTests(unittest.TestCase):
    """Ticket 26 — `mean_benchmark_score` becomes real arithmetic once the
    replay-benchmark family is actually fed."""

    def test_a_fed_family_produces_a_real_mean_over_successful_trials(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for score, timestamp in (
                (0.9, "2026-01-04T00:00:00Z"),
                (0.7, "2026-01-05T00:00:00Z"),
            ):
                record = learning_journal.ReplayBenchmarkRecord(
                    task_set="bench-v1", success=True, score=score, timestamp=timestamp
                )
                self.assertIsNone(
                    learning_journal.append_journal_record(record, root_dir=root)
                )
            # A failed trial is real evidence, kept in the journal, and
            # deliberately excluded from the mean it has no score to feed —
            # not silently dropped, just not averaged in.
            failed = learning_journal.ReplayBenchmarkRecord(
                task_set="bench-v1", success=False, timestamp="2026-01-05T12:00:00Z"
            )
            self.assertIsNone(learning_journal.append_journal_record(failed, root_dir=root))

            journal = learning_journal.read_journal(root)

        board = learning_scoreboard.compute_scoreboard(journal, now=_NOW)

        metric = board.replay_benchmark.mean_benchmark_score
        self.assertIsInstance(metric, learning_scoreboard.MetricValue)
        assert isinstance(metric, learning_scoreboard.MetricValue)
        self.assertAlmostEqual(metric.value, 0.8)
        self.assertEqual(metric.sample_size, 2)
        self.assertEqual(metric.direction, "higher_is_better")

    def test_a_trial_outside_the_window_does_not_feed_the_trend(self) -> None:
        """The default 7-day window: `_NOW` is 2026-01-08, so a trial from
        2025-12-01 predates `window_start` (2026-01-01) and must not move the
        mean — the same windowing rule every other metric in this file
        already honors."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            in_window = learning_journal.ReplayBenchmarkRecord(
                task_set="bench-v1", success=True, score=0.5, timestamp="2026-01-05T00:00:00Z"
            )
            out_of_window = learning_journal.ReplayBenchmarkRecord(
                task_set="bench-v1", success=True, score=1.0, timestamp="2025-12-01T00:00:00Z"
            )
            self.assertIsNone(
                learning_journal.append_journal_record(in_window, root_dir=root)
            )
            self.assertIsNone(
                learning_journal.append_journal_record(out_of_window, root_dir=root)
            )

            journal = learning_journal.read_journal(root)

        board = learning_scoreboard.compute_scoreboard(journal, now=_NOW)

        metric = board.replay_benchmark.mean_benchmark_score
        self.assertIsInstance(metric, learning_scoreboard.MetricValue)
        assert isinstance(metric, learning_scoreboard.MetricValue)
        self.assertEqual(metric.value, 0.5)
        self.assertEqual(metric.sample_size, 1)


class ReplayBenchmarkMetricsTests(unittest.TestCase):
    """Ticket 29 — `mean_benchmark_score` blends incomparable task sets."""

    def _seed_benchmarks(self, root: Path) -> None:
        v1_record = learning_journal.ReplayBenchmarkRecord(
            task_set="bench-v1", success=True, score=0.2, timestamp="2026-01-05T00:00:00Z"
        )
        v2_record = learning_journal.ReplayBenchmarkRecord(
            task_set="bench-v2", success=True, score=0.9, timestamp="2026-01-06T00:00:00Z"
        )
        learning_journal.append_journal_record(v1_record, root_dir=root)
        learning_journal.append_journal_record(v2_record, root_dir=root)

    def test_multiple_task_sets_in_window_are_not_blended_default_latest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._seed_benchmarks(root)

            journal = learning_journal.read_journal(root)

        board = learning_scoreboard.compute_scoreboard(journal, now=_NOW)
        metric = board.replay_benchmark.mean_benchmark_score
        self.assertIsInstance(metric, learning_scoreboard.MetricValue)
        assert isinstance(metric, learning_scoreboard.MetricValue)
        self.assertEqual(metric.value, 0.9)
        self.assertEqual(metric.sample_size, 1)

    def test_explicit_task_set_filters_to_target_task_set(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._seed_benchmarks(root)

            journal = learning_journal.read_journal(root)

        # Explicitly ask for bench-v1
        board_v1 = learning_scoreboard.compute_scoreboard(journal, now=_NOW, task_set="bench-v1")
        metric_v1 = board_v1.replay_benchmark.mean_benchmark_score
        self.assertIsInstance(metric_v1, learning_scoreboard.MetricValue)
        assert isinstance(metric_v1, learning_scoreboard.MetricValue)
        self.assertEqual(metric_v1.value, 0.2)
        self.assertEqual(metric_v1.sample_size, 1)

        # Explicitly ask for bench-v2
        board_v2 = learning_scoreboard.compute_scoreboard(journal, now=_NOW, task_set="bench-v2")
        metric_v2 = board_v2.replay_benchmark.mean_benchmark_score
        self.assertIsInstance(metric_v2, learning_scoreboard.MetricValue)
        assert isinstance(metric_v2, learning_scoreboard.MetricValue)
        self.assertEqual(metric_v2.value, 0.9)
        self.assertEqual(metric_v2.sample_size, 1)

    def test_explicit_task_set_with_no_records_returns_no_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            v1_record = learning_journal.ReplayBenchmarkRecord(
                task_set="bench-v1", success=True, score=0.2, timestamp="2026-01-05T00:00:00Z"
            )
            learning_journal.append_journal_record(v1_record, root_dir=root)
            journal = learning_journal.read_journal(root)

        board = learning_scoreboard.compute_scoreboard(journal, now=_NOW, task_set="bench-v3")
        metric = board.replay_benchmark.mean_benchmark_score
        self.assertIsInstance(metric, learning_scoreboard.MetricNoData)

    def test_task_set_validation_on_compute_and_read_scoreboard(self) -> None:
        invalid_task_sets: list[Any] = ["", 123, True, "contains-secret-word"]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            journal = learning_journal.read_journal(root)

            for invalid in invalid_task_sets:
                with self.assertRaises(ValueError):
                    learning_scoreboard.compute_scoreboard(journal, now=_NOW, task_set=invalid)
                with self.assertRaises(ValueError):
                    learning_scoreboard.read_scoreboard(root, now=_NOW, task_set=invalid)

    def test_blended_mean_would_regress_but_unblended_does_not(self) -> None:
        # Scenario from ticket 29:
        # If task sets were blended, the current scoreboard contains both bench-v1 (1.0) and bench-v2 (0.85).
        # With unblended scoreboards, baseline on bench-v2 is MetricNoData and current on bench-v2 is 0.85,
        # which is compared to MetricNoData and yields indeterminate change (no regression).
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Baseline contains bench-v1 record with score 1.0
            v1_record = learning_journal.ReplayBenchmarkRecord(
                task_set="bench-v1", success=True, score=1.0, timestamp="2026-01-05T00:00:00Z"
            )
            learning_journal.append_journal_record(v1_record, root_dir=root)

            # Read baseline on bench-v2
            baseline = learning_scoreboard.read_scoreboard(root, now=_NOW, task_set="bench-v2")
            self.assertIsInstance(baseline.replay_benchmark.mean_benchmark_score, learning_scoreboard.MetricNoData)

            # Candidate runs on bench-v2 and scores 0.85
            v2_record = learning_journal.ReplayBenchmarkRecord(
                task_set="bench-v2", success=True, score=0.85, timestamp="2026-01-06T00:00:00Z"
            )
            learning_journal.append_journal_record(v2_record, root_dir=root)

            # Read current on bench-v2
            current = learning_scoreboard.read_scoreboard(root, now=_NOW, task_set="bench-v2")
            current_score = current.replay_benchmark.mean_benchmark_score
            assert isinstance(current_score, learning_scoreboard.MetricValue)
            self.assertEqual(current_score.value, 0.85)

            # Compare them: regression should not be detected since baseline was MetricNoData
            comp = learning_scoreboard.compare_scoreboards(baseline, current)
            self.assertFalse(comp.has_regression)
            self.assertIn("mean_benchmark_score", comp.indeterminate)


if __name__ == "__main__":
    unittest.main()
