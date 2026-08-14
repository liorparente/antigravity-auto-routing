#!/usr/bin/env python3
"""Unit tests for `learning_report` (spec 0004 ticket 17).

Modules are loaded by path with `importlib.util.spec_from_file_location`,
the pattern `test_learning_scoreboard.py` and `test_production_invoker.py`
already use: these files are not a package, and `learning_report.py`'s bare
`import learning_journal` / `import learning_scoreboard` only resolve
because those two names are registered in `sys.modules` first.

`_find_forbidden_clock_reads` is imported by name from
`test_learning_scoreboard` — and nothing else from that module — per
implementation_plan.md Section 9 slice 1's round-2 correction: that one
function is pure (`ast.AST` in, a plain list of tuples out) and touches
neither `learning_journal` nor `learning_scoreboard`, so it is safe to import
even though importing `test_learning_scoreboard` re-registers those two
names in `sys.modules` as a side effect. This file's own module loads below
re-register both names again afterward, so every fixture and assertion here
still narrows against the module objects this file itself loaded.
"""
from __future__ import annotations

import ast
import importlib.util
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest import mock

from test_learning_scoreboard import _find_forbidden_clock_reads

MODULE_PATH = Path(__file__).with_name("learning_journal.py")
LEARNING_SCOREBOARD_PATH = Path(__file__).with_name("learning_scoreboard.py")
LEARNING_REPORT_PATH = Path(__file__).with_name("learning_report.py")

learning_journal_spec = importlib.util.spec_from_file_location("learning_journal", MODULE_PATH)
assert learning_journal_spec is not None and learning_journal_spec.loader is not None
learning_journal = importlib.util.module_from_spec(learning_journal_spec)
sys.modules["learning_journal"] = learning_journal
learning_journal_spec.loader.exec_module(learning_journal)

# `learning_scoreboard` does a bare `import learning_journal` at module top
# level, which only resolves because `learning_journal` is already
# registered in `sys.modules` above.
learning_scoreboard_spec = importlib.util.spec_from_file_location(
    "learning_scoreboard", LEARNING_SCOREBOARD_PATH
)
assert learning_scoreboard_spec is not None and learning_scoreboard_spec.loader is not None
learning_scoreboard = importlib.util.module_from_spec(learning_scoreboard_spec)
sys.modules["learning_scoreboard"] = learning_scoreboard
learning_scoreboard_spec.loader.exec_module(learning_scoreboard)

# Same reasoning: `learning_report` does bare `import learning_journal` and
# `import learning_scoreboard`, resolved via the two registrations above.
learning_report_spec = importlib.util.spec_from_file_location(
    "learning_report", LEARNING_REPORT_PATH
)
assert learning_report_spec is not None and learning_report_spec.loader is not None
learning_report = importlib.util.module_from_spec(learning_report_spec)
sys.modules["learning_report"] = learning_report
learning_report_spec.loader.exec_module(learning_report)

# A shared, timezone-aware `now` for every test below — never used to derive
# a live clock reading, only as a fixed injected value. Window bounds for the
# default 7-day window: current window is (2026-01-01, 2026-01-08], baseline
# window is (2025-12-25, 2026-01-01].
_NOW = datetime(2026, 1, 8, tzinfo=timezone.utc)

_ALL_METRIC_NAMES = (
    "violations_per_session",
    "canary_catch_rate",
    "mean_engagement_count",
    "escalation_rate",
    "dialogue_non_consensus_rate",
    "mean_rework_per_task",
    "cost_per_completed_task_usd",
    "mean_benchmark_score",
)

# `escalation_rate` has no producer anywhere and is permanently no-data.
# `mean_benchmark_score` has had a producer since ticket 26
# (`acceptance_gate.py`'s `ReplayBenchmarkRecord`s) — it reads no-data below
# only because this suite's fixtures carry none, not because it is permanent.
_PERMANENTLY_NO_DATA_METRICS = ("escalation_rate",)


def _line_for_metric(report: str, name: str) -> str:
    """Locate a metric's line by name, never by a loose substring search —
    the repo's dominant defect is a "the word appears somewhere" assertion
    that never actually checked the specific line it names.
    """
    for line in report.splitlines():
        if line.startswith(f"- {name} ("):
            return line
    raise AssertionError(f"no metric line found for {name!r} in report:\n{report}")


def _section_body(report: str, heading: str) -> str:
    """Every line between `heading` and the next `#`-headed line, with the
    blank-line section separator trimmed off the end.
    """
    lines = report.splitlines()
    start = lines.index(heading) + 1
    end = start
    while end < len(lines) and not lines[end].startswith("#"):
        end += 1
    body_lines = lines[start:end]
    while body_lines and body_lines[-1] == "":
        body_lines.pop()
    return "\n".join(body_lines)


def _worker_execution_record(
    task_id: str, *, timestamp: str, cost: float = 0.0, run_id: str | None = None
) -> Any:
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
    degraded: bool = False,
    occasion: str = "ambiguity",
    topology: str = "pair",
) -> Any:
    return learning_journal.DialogueQualityRecord(
        task=learning_journal.TaskLabel.for_task(task_id),
        occasion=occasion,  # type: ignore[arg-type]
        topology=topology,  # type: ignore[arg-type]
        rounds=rounds,
        canaries_planted=canaries_planted,
        canaries_caught=canaries_caught,
        degraded=degraded,
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


# --- Slice 1: path and skeleton ---


class ReportPathTests(unittest.TestCase):
    def test_report_path_is_beneath_root_in_ralph_reports_named_by_utc_date(self) -> None:
        root = Path("/fake/root")
        now = datetime(2026, 1, 8, 12, 0, 0, tzinfo=timezone.utc)

        path = learning_report.report_path(root, now=now)

        self.assertEqual(path, root / ".ralph" / "reports" / "weekly-report-2026-01-08.md")

    def test_report_path_names_the_file_by_the_utc_date_not_a_local_one(self) -> None:
        root = Path("/fake/root")
        # 2026-01-09T02:00:00+09:00 is 2026-01-08T17:00:00Z.
        now = datetime(2026, 1, 9, 2, 0, 0, tzinfo=timezone(timedelta(hours=9)))

        path = learning_report.report_path(root, now=now)

        self.assertEqual(path.name, "weekly-report-2026-01-08.md")

    def test_report_path_refuses_a_naive_now(self) -> None:
        root = Path("/fake/root")

        with self.assertRaises(ValueError):
            learning_report.report_path(root, now=datetime(2026, 1, 8))  # noqa: DTZ001


class NoClockTests(unittest.TestCase):
    def test_the_report_module_reads_no_clock(self) -> None:
        tree = ast.parse(LEARNING_REPORT_PATH.read_text(encoding="utf-8"))

        self.assertEqual(_find_forbidden_clock_reads(tree), [])


# --- Slice 2: empty journal ---


class EmptyJournalTests(unittest.TestCase):
    def test_empty_journal_renders_full_structure_with_quiet_notice(self) -> None:
        journal = learning_journal.JournalRead()

        report = learning_report.render_weekly_report(journal, now=_NOW)

        self.assertIn("# Weekly Learning Report", report)
        for heading in (
            "## Discipline",
            "## Critique authenticity",
            "## Efficiency",
            "## Replay benchmark",
        ):
            self.assertIn(heading, report)
        self.assertIn("**No activity dated in this window.**", report)
        for name in _ALL_METRIC_NAMES:
            line = _line_for_metric(report, name)
            self.assertIn("no data — indeterminate", line)
            self.assertIn("was no data", line)
        self.assertIn(
            "## Changes adopted this week\nNot yet wired: no producer reports this section.",
            report,
        )
        self.assertIn(
            "## Changes reverted this week\nNot yet wired: no producer reports this section.",
            report,
        )
        self.assertIn("## Budget degradations\nNone this week.", report)
        self.assertIn(
            "## Journal health\nWhole file: 0 unreadable line(s), 0 unknown-kind line(s).",
            report,
        )


# --- Slice 3: populated week, per-metric ---


class PopulatedWeekMetricTests(unittest.TestCase):
    """Six metrics get a real current value, a real baseline value, and a
    non-indeterminate status; two stay permanently no-data. Every fixture
    plants records in both the current window (2026-01-01, 2026-01-08] and
    the baseline window (2025-12-25, 2026-01-01] so both halves of each
    line's assertion (current AND baseline) are exercised, per
    implementation_plan.md Section 9 slice 3's round-2 gap fix.
    """

    report: str

    @classmethod
    def setUpClass(cls) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            records: tuple[Any, ...] = (
                # violations_per_session: discipline.
                _compliance_record(
                    "session-viol-cur",
                    violation_count=1,
                    timestamp="2026-01-05T00:00:00Z",
                    session_last_activity="2026-01-05T00:00:00Z",
                ),
                _compliance_record(
                    "session-viol-base",
                    violation_count=4,
                    timestamp="2025-12-28T00:00:00Z",
                    session_last_activity="2025-12-28T00:00:00Z",
                ),
                # mean_engagement_count: ordinary dialogue, critique authenticity.
                _dialogue_record(
                    "task-engage-cur",
                    rounds=(learning_journal.DialogueRound(verdict="approved", engagement_count=4),),
                    timestamp="2026-01-05T00:00:00Z",
                ),
                _dialogue_record(
                    "task-engage-base",
                    rounds=(learning_journal.DialogueRound(verdict="approved", engagement_count=2),),
                    timestamp="2025-12-28T00:00:00Z",
                ),
                # canary_catch_rate: a planted canary probe in both windows.
                _dialogue_record(
                    "task-canary-cur",
                    rounds=(learning_journal.DialogueRound(verdict="approved", engagement_count=1),),
                    timestamp="2026-01-06T00:00:00Z",
                    canaries_planted=1,
                    canaries_caught=1,
                ),
                _dialogue_record(
                    "task-canary-base",
                    rounds=(learning_journal.DialogueRound(verdict="approved", engagement_count=1),),
                    timestamp="2025-12-29T00:00:00Z",
                    canaries_planted=1,
                    canaries_caught=0,
                ),
                # dialogue_non_consensus_rate: an ordinary non-approved dialogue.
                _dialogue_record(
                    "task-nonconsensus-cur",
                    rounds=(learning_journal.DialogueRound(verdict="revise", engagement_count=2),),
                    timestamp="2026-01-06T12:00:00Z",
                ),
                _dialogue_record(
                    "task-nonconsensus-base",
                    rounds=(learning_journal.DialogueRound(verdict="revise", engagement_count=1),),
                    timestamp="2025-12-29T12:00:00Z",
                ),
                # mean_rework_per_task: two runs of one task in each window.
                _worker_execution_record(
                    "task-rework-cur", timestamp="2026-01-02T00:00:00Z", cost=1.0, run_id="run-1"
                ),
                _worker_execution_record(
                    "task-rework-cur", timestamp="2026-01-03T00:00:00Z", cost=1.0, run_id="run-2"
                ),
                _worker_execution_record(
                    "task-rework-base",
                    timestamp="2025-12-26T00:00:00Z",
                    cost=1.0,
                    run_id="run-1",
                ),
                _worker_execution_record(
                    "task-rework-base",
                    timestamp="2025-12-27T00:00:00Z",
                    cost=1.0,
                    run_id="run-2",
                ),
                _worker_execution_record(
                    "task-rework-base",
                    timestamp="2025-12-28T00:00:00Z",
                    cost=1.0,
                    run_id="run-3",
                ),
                # cost_per_completed_task_usd: one completed task per window.
                _worker_execution_record(
                    "task-cost-cur", timestamp="2026-01-03T00:00:00Z", cost=2.0, run_id="run-cost-cur"
                ),
                _outcome_record(
                    "task-cost-cur",
                    ground_truth="tests",
                    verdict="pass",
                    timestamp="2026-01-04T00:00:00Z",
                    run_id="run-cost-cur",
                ),
                _worker_execution_record(
                    "task-cost-base",
                    timestamp="2025-12-27T00:00:00Z",
                    cost=5.0,
                    run_id="run-cost-base",
                ),
                _outcome_record(
                    "task-cost-base",
                    ground_truth="review",
                    verdict="approved",
                    timestamp="2025-12-28T00:00:00Z",
                    run_id="run-cost-base",
                ),
            )
            for record in records:
                assert learning_journal.append_journal_record(record, root_dir=root) is None
            journal = learning_journal.read_journal(root)

        cls.report = learning_report.render_weekly_report(journal, now=_NOW)

    def test_violations_per_session_has_a_current_and_baseline_value(self) -> None:
        line = _line_for_metric(self.report, "violations_per_session")

        self.assertEqual(
            line,
            "- violations_per_session (lower is better): 1 (n=1) — improved (was 4, n=1)",
        )

    def test_mean_engagement_count_has_a_current_and_baseline_value(self) -> None:
        # Population is every ordinary (canaries_planted=0) windowed dialogue,
        # not only the one this test names — `task-nonconsensus-cur`/`-base`
        # (planted for dialogue_non_consensus_rate below) are ordinary
        # dialogues too, so their rounds' engagement counts are also in this
        # metric's mean: current (4, 2) -> 3.0, n=2; baseline (2, 1) -> 1.5, n=2.
        line = _line_for_metric(self.report, "mean_engagement_count")

        self.assertEqual(
            line,
            "- mean_engagement_count (higher is better): 3 (n=2) — improved (was 1.5, n=2)",
        )

    def test_canary_catch_rate_has_a_current_and_baseline_value(self) -> None:
        line = _line_for_metric(self.report, "canary_catch_rate")

        self.assertEqual(
            line,
            "- canary_catch_rate (higher is better): 1 (n=1) — improved (was 0, n=1)",
        )

    def test_dialogue_non_consensus_rate_has_a_current_and_baseline_value(self) -> None:
        line = _line_for_metric(self.report, "dialogue_non_consensus_rate")

        self.assertEqual(
            line,
            "- dialogue_non_consensus_rate (lower is better): 0.5 (n=2) — held (was 0.5, n=2)",
        )

    def test_mean_rework_per_task_has_a_current_and_baseline_value(self) -> None:
        # Population is every task with a run starting in the window, not
        # only `task-rework-*` — `task-cost-cur`/`-base` (planted for
        # cost_per_completed_task_usd below) each have exactly one run
        # starting in their window too, contributing a rework count of 0:
        # current (1, 0) -> 0.5, n=2; baseline (2, 0) -> 1.0, n=2.
        line = _line_for_metric(self.report, "mean_rework_per_task")

        self.assertEqual(
            line,
            "- mean_rework_per_task (lower is better): 0.5 (n=2) — improved (was 1, n=2)",
        )

    def test_cost_per_completed_task_usd_has_a_current_and_baseline_value(self) -> None:
        line = _line_for_metric(self.report, "cost_per_completed_task_usd")

        self.assertEqual(
            line,
            "- cost_per_completed_task_usd (lower is better): 2 (n=1) — improved (was 5, n=1)",
        )

    def test_escalation_rate_stays_permanently_no_data(self) -> None:
        line = _line_for_metric(self.report, "escalation_rate")

        self.assertEqual(
            line, "- escalation_rate (lower is better): no data — indeterminate (was no data)"
        )

    def test_mean_benchmark_score_is_no_data_when_this_fixtures_journal_carries_none(
        self,
    ) -> None:
        """Not permanent: ticket 26 gave this family a producer
        (`acceptance_gate.py`). It reads no-data here only because this
        class's fixture journal carries no `ReplayBenchmarkRecord` — see
        `test_learning_scoreboard.ReplayBenchmarkFamilyTests` for the
        real-arithmetic case, which this renderer-only suite does not
        duplicate."""
        line = _line_for_metric(self.report, "mean_benchmark_score")

        self.assertEqual(
            line,
            "- mean_benchmark_score (higher is better): no data — indeterminate (was no data)",
        )


# --- Slice 4: direction words, including the inversion case ---


class DirectionAndFamilyJoinTests(unittest.TestCase):
    report: str

    @classmethod
    def setUpClass(cls) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            records: tuple[Any, ...] = (
                # lower_is_better metric rising -> regressed.
                _compliance_record(
                    "session-regress-base",
                    violation_count=1,
                    timestamp="2025-12-28T00:00:00Z",
                    session_last_activity="2025-12-28T00:00:00Z",
                ),
                _compliance_record(
                    "session-regress-cur",
                    violation_count=5,
                    timestamp="2026-01-05T00:00:00Z",
                    session_last_activity="2026-01-05T00:00:00Z",
                ),
                # higher_is_better metric rising -> improved.
                _dialogue_record(
                    "task-improve-base",
                    rounds=(learning_journal.DialogueRound(verdict="approved", engagement_count=1),),
                    timestamp="2025-12-28T00:00:00Z",
                ),
                _dialogue_record(
                    "task-improve-cur",
                    rounds=(learning_journal.DialogueRound(verdict="approved", engagement_count=5),),
                    timestamp="2026-01-05T00:00:00Z",
                ),
                # first-window case: current has a value, baseline has none.
                _worker_execution_record(
                    "task-first-window",
                    timestamp="2026-01-03T00:00:00Z",
                    cost=3.0,
                    run_id="run-first",
                ),
                _outcome_record(
                    "task-first-window",
                    ground_truth="tests",
                    verdict="pass",
                    timestamp="2026-01-04T00:00:00Z",
                    run_id="run-first",
                ),
            )
            for record in records:
                assert learning_journal.append_journal_record(record, root_dir=root) is None
            journal = learning_journal.read_journal(root)

        cls.report = learning_report.render_weekly_report(journal, now=_NOW)

    def test_a_rising_lower_is_better_metric_reads_regressed(self) -> None:
        line = _line_for_metric(self.report, "violations_per_session")

        self.assertIn("— regressed (", line)

    def test_no_other_metric_reads_regressed(self) -> None:
        for name in _ALL_METRIC_NAMES:
            if name == "violations_per_session":
                continue
            line = _line_for_metric(self.report, name)
            self.assertNotIn("regressed", line)

    def test_a_rising_higher_is_better_metric_reads_improved(self) -> None:
        line = _line_for_metric(self.report, "mean_engagement_count")

        self.assertIn("— improved (", line)

    def test_a_current_value_with_an_empty_baseline_reads_indeterminate(self) -> None:
        line = _line_for_metric(self.report, "cost_per_completed_task_usd")

        self.assertEqual(
            line,
            "- cost_per_completed_task_usd (lower is better): 3 (n=1) — indeterminate (was no data)",
        )

    def test_every_metric_appears_under_its_documented_family_heading(self) -> None:
        discipline = _section_body(self.report, "## Discipline")
        critique = _section_body(self.report, "## Critique authenticity")
        efficiency = _section_body(self.report, "## Efficiency")
        replay = _section_body(self.report, "## Replay benchmark")

        self.assertIn("violations_per_session", discipline)
        self.assertIn("canary_catch_rate", critique)
        self.assertIn("mean_engagement_count", critique)
        self.assertIn("escalation_rate", efficiency)
        self.assertIn("dialogue_non_consensus_rate", efficiency)
        self.assertIn("mean_rework_per_task", efficiency)
        self.assertIn("cost_per_completed_task_usd", efficiency)
        self.assertIn("mean_benchmark_score", replay)
        # And not cross-planted into an undocumented family's section.
        self.assertNotIn("mean_rework_per_task", discipline)
        self.assertNotIn("violations_per_session", efficiency)


# --- Slice 5: adopted/reverted ---


class AdoptedRevertedTests(unittest.TestCase):
    def test_entries_render_one_bullet_per_entry_in_order(self) -> None:
        journal = learning_journal.JournalRead()

        report = learning_report.render_weekly_report(
            journal, now=_NOW, adopted=("first change", "second change")
        )

        body = _section_body(report, "## Changes adopted this week")
        self.assertEqual(body, "- first change\n- second change")

    def test_an_empty_tuple_renders_none_this_week(self) -> None:
        journal = learning_journal.JournalRead()

        report = learning_report.render_weekly_report(journal, now=_NOW, reverted=())

        body = _section_body(report, "## Changes reverted this week")
        self.assertEqual(body, "None this week.")

    def test_none_renders_not_yet_wired(self) -> None:
        journal = learning_journal.JournalRead()

        report = learning_report.render_weekly_report(journal, now=_NOW, adopted=None)

        body = _section_body(report, "## Changes adopted this week")
        self.assertEqual(body, "Not yet wired: no producer reports this section.")

    def test_an_entry_with_an_embedded_newline_raises(self) -> None:
        journal = learning_journal.JournalRead()

        with self.assertRaises(ValueError):
            learning_report.render_weekly_report(journal, now=_NOW, adopted=("line one\nline two",))

    def test_an_entry_with_an_embedded_carriage_return_raises(self) -> None:
        journal = learning_journal.JournalRead()

        with self.assertRaises(ValueError):
            learning_report.render_weekly_report(journal, now=_NOW, reverted=("bad\rentry",))

    def test_an_empty_entry_raises(self) -> None:
        journal = learning_journal.JournalRead()

        with self.assertRaises(ValueError):
            learning_report.render_weekly_report(journal, now=_NOW, adopted=("",))

    def test_a_whitespace_only_entry_raises(self) -> None:
        journal = learning_journal.JournalRead()

        with self.assertRaises(ValueError):
            learning_report.render_weekly_report(journal, now=_NOW, adopted=("   ",))

    def test_a_bare_string_container_raises_without_rendering_bullets(self) -> None:
        journal = learning_journal.JournalRead()

        with self.assertRaises(ValueError):
            learning_report.render_weekly_report(
                journal, now=_NOW, adopted="adopted the routing-table update"
            )


# --- Slice 6: degradations ---


class DegradationTests(unittest.TestCase):
    def test_a_windowed_degraded_record_renders_one_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record = _dialogue_record(
                "task-degraded",
                rounds=(learning_journal.DialogueRound(verdict="approved", engagement_count=1),),
                timestamp="2026-01-05T10:00:00Z",
                degraded=True,
                occasion="plan-review",
                topology="pair",
            )
            assert learning_journal.append_journal_record(record, root_dir=root) is None
            journal = learning_journal.read_journal(root)

        report = learning_report.render_weekly_report(journal, now=_NOW)

        body = _section_body(report, "## Budget degradations")
        self.assertEqual(
            body,
            "- 2026-01-05T10:00:00Z — plan-review (pair) — task task-degraded — 1 round(s)",
        )

    def test_a_degraded_record_before_the_window_is_absent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record = _dialogue_record(
                "task-degraded-old",
                rounds=(learning_journal.DialogueRound(verdict="approved", engagement_count=1),),
                timestamp="2025-12-01T00:00:00Z",
                degraded=True,
            )
            assert learning_journal.append_journal_record(record, root_dir=root) is None
            journal = learning_journal.read_journal(root)

        report = learning_report.render_weekly_report(journal, now=_NOW)

        body = _section_body(report, "## Budget degradations")
        self.assertEqual(body, "None this week.")

    def test_a_degraded_canary_probe_is_listed_with_its_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record = _dialogue_record(
                "task-degraded-canary",
                rounds=(learning_journal.DialogueRound(verdict="approved", engagement_count=1),),
                timestamp="2026-01-05T10:00:00Z",
                degraded=True,
                canaries_planted=1,
                canaries_caught=1,
            )
            assert learning_journal.append_journal_record(record, root_dir=root) is None
            journal = learning_journal.read_journal(root)

        report = learning_report.render_weekly_report(journal, now=_NOW)

        body = _section_body(report, "## Budget degradations")
        self.assertTrue(body.endswith("— canary probe"))
        self.assertIn("task task-degraded-canary", body)

    def test_a_non_degraded_record_never_appears(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record = _dialogue_record(
                "task-not-degraded",
                rounds=(learning_journal.DialogueRound(verdict="approved", engagement_count=1),),
                timestamp="2026-01-05T10:00:00Z",
                degraded=False,
            )
            assert learning_journal.append_journal_record(record, root_dir=root) is None
            journal = learning_journal.read_journal(root)

        report = learning_report.render_weekly_report(journal, now=_NOW)

        body = _section_body(report, "## Budget degradations")
        self.assertEqual(body, "None this week.")
        self.assertNotIn("task-not-degraded", report)


# --- Slice 7: quiet-week boundary ---


class QuietWeekBoundaryTests(unittest.TestCase):
    def test_records_only_before_the_window_still_show_the_notice_and_a_baseline_value(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record = _compliance_record(
                "session-history",
                violation_count=3,
                timestamp="2025-12-28T00:00:00Z",
                session_last_activity="2025-12-28T00:00:00Z",
            )
            assert learning_journal.append_journal_record(record, root_dir=root) is None
            journal = learning_journal.read_journal(root)

        report = learning_report.render_weekly_report(journal, now=_NOW)

        self.assertIn("**No activity dated in this window.**", report)
        line = _line_for_metric(report, "violations_per_session")
        self.assertEqual(
            line,
            "- violations_per_session (lower is better): no data — indeterminate (was 3, n=1)",
        )

    def test_a_single_in_window_record_in_any_family_suppresses_the_notice(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record = _compliance_record(
                "session-current",
                violation_count=1,
                timestamp="2026-01-05T00:00:00Z",
                session_last_activity="2026-01-05T00:00:00Z",
            )
            assert learning_journal.append_journal_record(record, root_dir=root) is None
            journal = learning_journal.read_journal(root)

        report = learning_report.render_weekly_report(journal, now=_NOW)

        self.assertNotIn("**No activity dated in this window.**", report)

    def test_a_compliance_record_with_no_session_last_activity_does_not_suppress_the_notice(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record = _compliance_record(
                "session-no-activity",
                violation_count=1,
                timestamp="2026-01-05T00:00:00Z",
                session_last_activity=None,
            )
            assert learning_journal.append_journal_record(record, root_dir=root) is None
            journal = learning_journal.read_journal(root)

        report = learning_report.render_weekly_report(journal, now=_NOW)

        self.assertIn("**No activity dated in this window.**", report)


# --- Slice 7b: window-line UTC normalization ---


class WindowLineUtcNormalizationTests(unittest.TestCase):
    def test_the_window_line_renders_both_bounds_in_utc_even_for_a_non_utc_now(self) -> None:
        journal = learning_journal.JournalRead()
        now = datetime(2026, 1, 8, 9, 0, 0, tzinfo=timezone(timedelta(hours=9)))
        # 2026-01-08T09:00:00+09:00 is 2026-01-08T00:00:00Z.

        report = learning_report.render_weekly_report(journal, now=now)

        lines = report.splitlines()
        window_line = next(line for line in lines if line.startswith("Window: "))
        self.assertEqual(
            window_line, "Window: 2026-01-01T00:00:00Z → 2026-01-08T00:00:00Z (7 days)"
        )


# --- Slice 8: the write door ---


class WriteWeeklyReportTests(unittest.TestCase):
    def test_write_creates_parent_dirs_writes_at_report_path_and_matches_render(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            path = learning_report.write_weekly_report(root, now=_NOW)

            self.assertEqual(path, learning_report.report_path(root, now=_NOW))
            self.assertTrue(path.exists())
            journal = learning_journal.read_journal(root)
            expected = learning_report.render_weekly_report(journal, now=_NOW)
            self.assertEqual(path.read_text(encoding="utf-8"), expected)

    def test_a_second_call_the_same_utc_day_supersedes_the_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            first_path = learning_report.write_weekly_report(root, now=_NOW, reverted=("first",))
            second_path = learning_report.write_weekly_report(
                root, now=_NOW, reverted=("first", "second")
            )

            self.assertEqual(first_path, second_path)
            journal = learning_journal.read_journal(root)
            expected_second = learning_report.render_weekly_report(
                journal, now=_NOW, reverted=("first", "second")
            )
            self.assertEqual(second_path.read_text(encoding="utf-8"), expected_second)

    def test_a_successful_write_leaves_no_stray_temp_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            path = learning_report.write_weekly_report(root, now=_NOW)

            siblings = list(path.parent.iterdir())
            self.assertEqual(siblings, [path])

    def test_a_failure_between_temp_creation_and_replace_leaves_the_prior_report_untouched(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first_path = learning_report.write_weekly_report(root, now=_NOW, reverted=("first",))
            original_bytes = first_path.read_bytes()

            with (
                mock.patch("learning_report.os.replace", side_effect=OSError("boom")),
                self.assertRaises(OSError),
            ):
                learning_report.write_weekly_report(root, now=_NOW, reverted=("first", "second"))

            self.assertEqual(first_path.read_bytes(), original_bytes)
            siblings = list(first_path.parent.iterdir())
            self.assertEqual(siblings, [first_path])

    def test_write_refuses_a_naive_now_before_any_disk_access(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            with self.assertRaises(ValueError):
                learning_report.write_weekly_report(root, now=datetime(2026, 1, 8))  # noqa: DTZ001

            self.assertFalse((root / ".ralph").exists())

    def test_render_refuses_a_naive_now(self) -> None:
        journal = learning_journal.JournalRead()

        with self.assertRaises(ValueError):
            learning_report.render_weekly_report(journal, now=datetime(2026, 1, 8))  # noqa: DTZ001

    def test_render_refuses_a_non_positive_window_days(self) -> None:
        journal = learning_journal.JournalRead()

        with self.assertRaises(ValueError):
            learning_report.render_weekly_report(journal, now=_NOW, window_days=0)

    def test_render_refuses_a_bool_window_days(self) -> None:
        journal = learning_journal.JournalRead()

        with self.assertRaises(ValueError):
            learning_report.render_weekly_report(journal, now=_NOW, window_days=True)

    def test_write_refuses_a_non_positive_window_days(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            with self.assertRaises(ValueError):
                learning_report.write_weekly_report(root, now=_NOW, window_days=-1)


if __name__ == "__main__":
    unittest.main()
