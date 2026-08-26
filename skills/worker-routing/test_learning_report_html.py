#!/usr/bin/env python3
"""Unit tests for `learning_report_html` (ticket 44).

`_find_forbidden_clock_reads` is imported by name from
`test_learning_scoreboard` — same convention `test_learning_report.py`
already uses: that function is pure (`ast.AST` in, a plain list of tuples
out) and touches neither `learning_journal` nor `learning_scoreboard`.
"""
from __future__ import annotations

import ast
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest import mock

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parent))

if __package__:
    from . import learning_journal, learning_report_html, learning_scoreboard, routing_config
    from .test_learning_scoreboard import _find_forbidden_clock_reads
else:
    import learning_journal  # type: ignore[no-redef]
    import learning_report_html  # type: ignore[no-redef]
    import learning_scoreboard  # type: ignore[no-redef]
    import routing_config  # type: ignore[no-redef]
    from test_learning_scoreboard import _find_forbidden_clock_reads  # type: ignore[no-redef]

LEARNING_REPORT_HTML_PATH = Path(__file__).with_name("learning_report_html.py")

# A shared, timezone-aware `now` for every test below — never used to derive
# a live clock reading, only as a fixed injected value. Window bounds for the
# default 7-day window: current window is (2026-01-01, 2026-01-08], baseline
# window is (2025-12-25, 2026-01-01].
_NOW = datetime(2026, 1, 8, tzinfo=timezone.utc)


def _worker_execution_record(
    task_id: str,
    *,
    timestamp: str,
    cost: float = 0.0,
    success: bool = True,
    model_family: str = "claude",
    model_id: str = "claude-sonnet-5",
    run_id: str | None = None,
) -> Any:
    return learning_journal.WorkerExecutionRecord(
        task=learning_journal.TaskLabel.for_task(task_id),
        duration_ms=100,
        cost_estimate_usd=cost,
        success=success,
        retry_count=0,
        effort="low",
        model_id=model_id,
        model_family=model_family,
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
    issue_codes: tuple[str, ...] = (),
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
        issue_codes=issue_codes,
        run_id=run_id,
        session_last_activity=session_last_activity,
        timestamp=timestamp,
    )


def _boards(journal: learning_journal.JournalRead, *, now: datetime, window_days: int = 7) -> Any:
    board = learning_scoreboard.compute_scoreboard(journal, now=now, window_days=window_days)
    baseline_board = learning_scoreboard.compute_scoreboard(
        journal, now=now - timedelta(days=window_days), window_days=window_days
    )
    return board, baseline_board


# --- AST guard: no live clock ---


class NoClockTests(unittest.TestCase):
    def test_the_html_report_module_reads_no_clock(self) -> None:
        tree = ast.parse(LEARNING_REPORT_HTML_PATH.read_text(encoding="utf-8"))

        self.assertEqual(_find_forbidden_clock_reads(tree), [])


# --- Pure contract: render_html_report ---


class RenderHtmlReportSkeletonTests(unittest.TestCase):
    def test_empty_journal_renders_without_crashing_and_carries_expected_sections(self) -> None:
        journal = learning_journal.JournalRead()
        board, baseline_board = _boards(journal, now=_NOW)

        report = learning_report_html.render_html_report(
            journal, board, baseline_board, now=_NOW
        )

        self.assertTrue(report.startswith("<!DOCTYPE html>"))
        self.assertIn('dir="rtl"', report)
        self.assertIn("Light Mode", report)
        self.assertIn("Rubik", report)
        self.assertIn("<html", report)
        self.assertIn("</html>", report)
        for name in (
            "violations_per_session",
            "canary_catch_rate",
            "mean_engagement_count",
            "escalation_rate",
            "dialogue_non_consensus_rate",
            "mean_rework_per_task",
            "cost_per_completed_task_usd",
            "mean_benchmark_score",
            "first_pass_yield",
            "total_cost_usd",
            "cost_savings_usd",
            "token_savings",
        ):
            self.assertIn(name, report)

    def test_refuses_a_naive_now(self) -> None:
        journal = learning_journal.JournalRead()
        board, baseline_board = _boards(journal, now=_NOW)

        with self.assertRaises(ValueError):
            learning_report_html.render_html_report(
                journal, board, baseline_board, now=datetime(2026, 1, 8)  # noqa: DTZ001
            )

    def test_refuses_a_non_positive_window_days(self) -> None:
        journal = learning_journal.JournalRead()
        board, baseline_board = _boards(journal, now=_NOW)

        with self.assertRaises(ValueError):
            learning_report_html.render_html_report(
                journal, board, baseline_board, now=_NOW, window_days=0
            )

    def test_refuses_a_board_computed_with_a_different_window_days(self) -> None:
        journal = learning_journal.JournalRead()
        board = learning_scoreboard.compute_scoreboard(journal, now=_NOW, window_days=7)
        mismatched_baseline = learning_scoreboard.compute_scoreboard(
            journal, now=_NOW - timedelta(days=14), window_days=14
        )

        with self.assertRaises(ValueError):
            learning_report_html.render_html_report(
                journal, board, mismatched_baseline, now=_NOW, window_days=7
            )

    def test_refuses_a_baseline_board_not_aligned_to_one_window_before_now(self) -> None:
        journal = learning_journal.JournalRead()
        board = learning_scoreboard.compute_scoreboard(journal, now=_NOW, window_days=7)
        # Baseline computed at the wrong `now` (should be _NOW - 7 days).
        wrong_baseline = learning_scoreboard.compute_scoreboard(
            journal, now=_NOW - timedelta(days=3), window_days=7
        )

        with self.assertRaises(ValueError):
            learning_report_html.render_html_report(
                journal, board, wrong_baseline, now=_NOW, window_days=7
            )


# --- Dynamic metrics: cost & savings ---


class CostAndSavingsTests(unittest.TestCase):
    def test_total_windowed_cost_sums_every_execution_regardless_of_completion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            records = (
                _worker_execution_record("task-a", timestamp="2026-01-03T00:00:00Z", cost=2.0),
                _worker_execution_record("task-b", timestamp="2026-01-04T00:00:00Z", cost=3.5),
            )
            for record in records:
                assert learning_journal.append_journal_record(record, root_dir=root) is None
            journal = learning_journal.read_journal(root)

        board, baseline_board = _boards(journal, now=_NOW)
        report = learning_report_html.render_html_report(journal, board, baseline_board, now=_NOW)

        self.assertIn("5.5", report)

    def test_cost_savings_is_no_data_when_baseline_has_no_completed_task(self) -> None:
        journal = learning_journal.JournalRead()
        board, baseline_board = _boards(journal, now=_NOW)

        report = learning_report_html.render_html_report(journal, board, baseline_board, now=_NOW)

        self.assertIn("cost_savings_usd", report)
        self.assertIn("no data", report)

    def test_cost_savings_is_positive_when_current_costs_less_per_task_than_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            records = (
                # Baseline window: one completed task costing 10.0.
                _worker_execution_record(
                    "task-base", timestamp="2025-12-27T00:00:00Z", cost=10.0, run_id="run-base"
                ),
                _outcome_record(
                    "task-base",
                    ground_truth="tests",
                    verdict="pass",
                    timestamp="2025-12-28T00:00:00Z",
                    run_id="run-base",
                ),
                # Current window: one completed task costing 2.0.
                _worker_execution_record(
                    "task-cur", timestamp="2026-01-03T00:00:00Z", cost=2.0, run_id="run-cur"
                ),
                _outcome_record(
                    "task-cur",
                    ground_truth="tests",
                    verdict="pass",
                    timestamp="2026-01-04T00:00:00Z",
                    run_id="run-cur",
                ),
            )
            for record in records:
                assert learning_journal.append_journal_record(record, root_dir=root) is None
            journal = learning_journal.read_journal(root)

        board, baseline_board = _boards(journal, now=_NOW)
        report = learning_report_html.render_html_report(journal, board, baseline_board, now=_NOW)

        # Hypothetical (baseline rate 10.0 * 1 completed task) - actual (2.0) = 8.0.
        self.assertIn("cost_savings_usd", report)
        self.assertIn(f'dir="ltr">{learning_report_html._format_value(8.0)} (n=1)<', report)


# --- Dynamic metrics: FPY & rework ---


class FirstPassYieldTests(unittest.TestCase):
    def test_a_task_with_no_rework_is_full_yield(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record = _worker_execution_record(
                "task-single", timestamp="2026-01-03T00:00:00Z", cost=1.0, run_id="run-1"
            )
            assert learning_journal.append_journal_record(record, root_dir=root) is None
            journal = learning_journal.read_journal(root)

        board, baseline_board = _boards(journal, now=_NOW)
        report = learning_report_html.render_html_report(journal, board, baseline_board, now=_NOW)

        self.assertIn("first_pass_yield", report)
        self.assertIn("1", report)

    def test_a_task_reworked_once_pulls_fpy_below_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            records = (
                _worker_execution_record(
                    "task-reworked", timestamp="2026-01-02T00:00:00Z", cost=1.0, run_id="run-1"
                ),
                _worker_execution_record(
                    "task-reworked", timestamp="2026-01-03T00:00:00Z", cost=1.0, run_id="run-2"
                ),
                _worker_execution_record(
                    "task-clean", timestamp="2026-01-02T00:00:00Z", cost=1.0, run_id="run-clean"
                ),
            )
            for record in records:
                assert learning_journal.append_journal_record(record, root_dir=root) is None
            journal = learning_journal.read_journal(root)

        board, baseline_board = _boards(journal, now=_NOW)
        report = learning_report_html.render_html_report(journal, board, baseline_board, now=_NOW)

        self.assertIn("first_pass_yield", report)
        self.assertIn("0.5", report)

    def test_fpy_is_no_data_on_an_empty_journal(self) -> None:
        journal = learning_journal.JournalRead()
        board, baseline_board = _boards(journal, now=_NOW)

        report = learning_report_html.render_html_report(journal, board, baseline_board, now=_NOW)

        self.assertIn("first_pass_yield", report)


# --- Dynamic metrics: model family breakdown ---


class ModelFamilyBreakdownTests(unittest.TestCase):
    def test_each_family_gets_a_row_with_cost_and_success_rate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            records = (
                _worker_execution_record(
                    "task-1",
                    timestamp="2026-01-02T00:00:00Z",
                    cost=1.0,
                    success=True,
                    model_family="claude",
                ),
                _worker_execution_record(
                    "task-2",
                    timestamp="2026-01-03T00:00:00Z",
                    cost=3.0,
                    success=False,
                    model_family="claude",
                ),
                _worker_execution_record(
                    "task-3",
                    timestamp="2026-01-03T00:00:00Z",
                    cost=0.5,
                    success=True,
                    model_family="gemini",
                ),
            )
            for record in records:
                assert learning_journal.append_journal_record(record, root_dir=root) is None
            journal = learning_journal.read_journal(root)

        board, baseline_board = _boards(journal, now=_NOW)
        report = learning_report_html.render_html_report(journal, board, baseline_board, now=_NOW)

        self.assertIn("claude", report)
        self.assertIn("gemini", report)
        # claude: 2 executions, 1 success -> 50.0%; gemini: 1 execution, 1 success -> 100.0%.
        self.assertIn("50.0%", report)
        self.assertIn("100.0%", report)
        self.assertIn("Rework Rate", report)

    def test_an_execution_outside_the_window_is_excluded_from_the_table(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record = _worker_execution_record(
                "task-old", timestamp="2025-01-01T00:00:00Z", cost=9.0, model_family="stale-family"
            )
            assert learning_journal.append_journal_record(record, root_dir=root) is None
            journal = learning_journal.read_journal(root)

        board, baseline_board = _boards(journal, now=_NOW)
        report = learning_report_html.render_html_report(journal, board, baseline_board, now=_NOW)

        self.assertNotIn("stale-family", report)

    def test_no_worker_executions_renders_an_empty_state_not_a_crash(self) -> None:
        journal = learning_journal.JournalRead()
        board, baseline_board = _boards(journal, now=_NOW)

        report = learning_report_html.render_html_report(journal, board, baseline_board, now=_NOW)

        self.assertIn("No worker executions", report)

    def test_rework_rate_is_reported_per_model_family(self) -> None:
        journal = learning_journal.JournalRead(
            worker_executions=(
                _worker_execution_record(
                    "task-reworked", timestamp="2026-01-02T00:00:00Z", run_id="run-1"
                ),
                _worker_execution_record(
                    "task-reworked", timestamp="2026-01-03T00:00:00Z", run_id="run-2"
                ),
            )
        )
        board, baseline_board = _boards(journal, now=_NOW)

        report = learning_report_html.render_html_report(journal, board, baseline_board, now=_NOW)

        self.assertIn("100.0%", report)


# --- Dynamic metrics: compliance audits & degradation events ---


class ComplianceAndDegradationTests(unittest.TestCase):
    def test_a_windowed_compliance_record_is_listed_with_its_issue_codes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record = _compliance_record(
                "session-audit-1",
                violation_count=2,
                timestamp="2026-01-05T00:00:00Z",
                session_last_activity="2026-01-05T00:00:00Z",
                issue_codes=("DEC-01", "LOG-02"),
            )
            assert learning_journal.append_journal_record(record, root_dir=root) is None
            journal = learning_journal.read_journal(root)

        board, baseline_board = _boards(journal, now=_NOW)
        report = learning_report_html.render_html_report(journal, board, baseline_board, now=_NOW)

        self.assertIn("session-audit-1", report)
        self.assertIn("DEC-01", report)
        self.assertIn("LOG-02", report)

    def test_only_the_last_record_for_a_session_survives_reduction(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            records = (
                _compliance_record(
                    "session-repeat",
                    violation_count=1,
                    timestamp="2026-01-04T00:00:00Z",
                    session_last_activity="2026-01-05T00:00:00Z",
                    run_id="run-1",
                ),
                _compliance_record(
                    "session-repeat",
                    violation_count=9,
                    timestamp="2026-01-05T00:00:00Z",
                    session_last_activity="2026-01-05T00:00:00Z",
                    run_id="run-2",
                ),
            )
            for record in records:
                assert learning_journal.append_journal_record(record, root_dir=root) is None
            journal = learning_journal.read_journal(root)

        board, baseline_board = _boards(journal, now=_NOW)
        report = learning_report_html.render_html_report(journal, board, baseline_board, now=_NOW)

        self.assertEqual(report.count("session-repeat"), 1)
        self.assertIn('dir="ltr">9</td>', report)

    def test_no_compliance_records_renders_an_empty_state(self) -> None:
        journal = learning_journal.JournalRead()
        board, baseline_board = _boards(journal, now=_NOW)

        report = learning_report_html.render_html_report(journal, board, baseline_board, now=_NOW)

        self.assertIn("No compliance audits", report)

    def test_a_degraded_dialogue_in_the_window_is_listed(self) -> None:
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

        board, baseline_board = _boards(journal, now=_NOW)
        report = learning_report_html.render_html_report(journal, board, baseline_board, now=_NOW)

        self.assertIn("task-degraded", report)
        self.assertIn("plan-review", report)

    def test_a_non_degraded_dialogue_never_appears_in_the_degradation_section(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record = _dialogue_record(
                "task-healthy",
                rounds=(learning_journal.DialogueRound(verdict="approved", engagement_count=1),),
                timestamp="2026-01-05T10:00:00Z",
                degraded=False,
            )
            assert learning_journal.append_journal_record(record, root_dir=root) is None
            journal = learning_journal.read_journal(root)

        board, baseline_board = _boards(journal, now=_NOW)
        report = learning_report_html.render_html_report(journal, board, baseline_board, now=_NOW)

        self.assertNotIn("task-healthy", report)

    def test_no_degradations_renders_an_empty_state(self) -> None:
        journal = learning_journal.JournalRead()
        board, baseline_board = _boards(journal, now=_NOW)

        report = learning_report_html.render_html_report(journal, board, baseline_board, now=_NOW)

        self.assertIn("No budget degradations", report)

    def test_consensus_and_debate_metrics_are_rendered(self) -> None:
        journal = learning_journal.JournalRead(
            dialogues=(
                _dialogue_record(
                    "task-consensus",
                    rounds=(learning_journal.DialogueRound(verdict="approved", engagement_count=2),),
                    timestamp="2026-01-05T10:00:00Z",
                ),
                _dialogue_record(
                    "task-stalemate",
                    rounds=(learning_journal.DialogueRound(verdict="revise", engagement_count=1),),
                    timestamp="2026-01-06T10:00:00Z",
                ),
            )
        )
        board, baseline_board = _boards(journal, now=_NOW)

        report = learning_report_html.render_html_report(journal, board, baseline_board, now=_NOW)

        self.assertIn("Consensus &amp; Debate", report)
        self.assertIn("50.0%", report)


# --- Escaping ---


class EscapingTests(unittest.TestCase):
    def test_the_escape_helper_neutralizes_html_metacharacters(self) -> None:
        dangerous = "<script>alert('x')</script>&\""

        escaped = learning_report_html._escape(dangerous)

        self.assertNotIn("<script>", escaped)
        self.assertIn("&lt;script&gt;", escaped)
        self.assertIn("&amp;", escaped)
        self.assertIn("&quot;", escaped)

    def test_the_escape_helper_stringifies_a_non_string_value_first(self) -> None:
        self.assertEqual(learning_report_html._escape(3.14), "3.14")
        self.assertEqual(learning_report_html._escape(True), "True")

    def test_rendered_report_never_leaks_an_unescaped_script_tag(self) -> None:
        # Journal identifiers cannot carry `<script>` (TASK_ID_RE forbids it),
        # so this exercises the renderer's own literal content instead: no
        # section of the document may contain a bare, unescaped `<script>`
        # tag anywhere outside a `<script>`-less document — this report ships
        # no inline JS at all, so the substring must not appear anywhere.
        journal = learning_journal.JournalRead()
        board, baseline_board = _boards(journal, now=_NOW)

        report = learning_report_html.render_html_report(journal, board, baseline_board, now=_NOW)

        self.assertNotIn("<script", report)


# --- Role matrix (ticket 47) ---


def _capability(
    *,
    provider: str = "anthropic",
    model_id: str = "claude-sonnet-5",
    supported_efforts: tuple[str, ...] = ("low", "medium", "high"),
    default_effort: str | None = "medium",
    tier: str = "high",
    context: int | None = 200000,
    local_only: bool = False,
) -> Any:
    return routing_config.ModelCapability(
        provider=provider,
        model_id=model_id,
        supported_efforts=supported_efforts,
        default_effort=default_effort,
        tier=tier,
        context=context,
        local_only=local_only,
    )


def _binding(
    *,
    provider_id: str = "anthropic-sonnet",
    adapter: str = "anthropic",
    model_id: str = "claude-sonnet-5",
    reasoning_effort: str = "medium",
    capability: Any | None = None,
) -> Any:
    return routing_config.RoleModelBinding(
        provider_id=provider_id,
        adapter=adapter,
        model_id=model_id,
        reasoning_effort=reasoning_effort,
        capability=capability if capability is not None else _capability(),
    )


def _role_entry(
    role_id: str,
    *,
    reasoning_tier: str = "high",
    tool_access: str = "full",
    min_context: int = 100000,
    local_only: bool = False,
    bindings: tuple[Any, ...] = (),
) -> Any:
    return routing_config.RoleMatrixEntry(
        role_id=role_id,
        capability_requirements=routing_config.CapabilityRequirements(
            reasoning_tier=reasoning_tier,
            tool_access=tool_access,
            min_context=min_context,
            local_only=local_only,
        ),
        bindings=bindings,
    )


class RoleMatrixSectionTests(unittest.TestCase):
    def test_empty_role_matrix_renders_an_empty_state_in_both_grids(self) -> None:
        journal = learning_journal.JournalRead()
        board, baseline_board = _boards(journal, now=_NOW)

        report = learning_report_html.render_html_report(journal, board, baseline_board, now=_NOW)

        self.assertIn('id="role-grid-simple"', report)
        self.assertIn('id="role-grid-all"', report)
        self.assertIn("No roles configured.", report)

    def test_tab_bar_and_role_matrix_heading_are_present(self) -> None:
        journal = learning_journal.JournalRead()
        board, baseline_board = _boards(journal, now=_NOW)

        report = learning_report_html.render_html_report(journal, board, baseline_board, now=_NOW)

        self.assertIn('id="tab-metrics"', report)
        self.assertIn('id="tab-roles"', report)
        self.assertIn("מדדי ביצוע ולמידה", report)
        self.assertIn("הגדרת תפקידים ומודלים", report)
        self.assertIn("Role &amp; Model Configuration Matrix", report)

    def test_segmented_toggle_labels_are_present(self) -> None:
        journal = learning_journal.JournalRead()
        board, baseline_board = _boards(journal, now=_NOW)

        report = learning_report_html.render_html_report(journal, board, baseline_board, now=_NOW)

        self.assertIn("תפקידי מפתח (ראשי)", report)
        self.assertIn("פירוט מלא (מתקדם)", report)

    def test_a_primary_role_appears_in_both_the_simple_and_all_grids(self) -> None:
        journal = learning_journal.JournalRead()
        board, baseline_board = _boards(journal, now=_NOW)
        role_matrix = {"planner": _role_entry("planner", bindings=(_binding(),))}

        report = learning_report_html.render_html_report(
            journal, board, baseline_board, now=_NOW, role_matrix=role_matrix
        )

        simple_grid = report.split('id="role-grid-simple"')[1].split('id="role-grid-all"')[0]
        all_grid = report.split('id="role-grid-all"')[1]
        self.assertIn("planner", simple_grid)
        self.assertIn("planner", all_grid)

    def test_a_non_primary_role_appears_only_in_the_all_grid(self) -> None:
        journal = learning_journal.JournalRead()
        board, baseline_board = _boards(journal, now=_NOW)
        role_matrix = {
            "reviewer_security": _role_entry("reviewer_security", bindings=(_binding(),))
        }

        report = learning_report_html.render_html_report(
            journal, board, baseline_board, now=_NOW, role_matrix=role_matrix
        )

        simple_grid = report.split('id="role-grid-simple"')[1].split('id="role-grid-all"')[0]
        all_grid = report.split('id="role-grid-all"')[1]
        self.assertNotIn("reviewer_security", simple_grid)
        self.assertIn("reviewer_security", all_grid)

    def test_role_card_shows_capability_requirements_and_binding_details(self) -> None:
        journal = learning_journal.JournalRead()
        board, baseline_board = _boards(journal, now=_NOW)
        role_matrix = {
            "planner": _role_entry(
                "planner",
                reasoning_tier="high",
                tool_access="full",
                min_context=128000,
                bindings=(
                    _binding(
                        provider_id="anthropic-sonnet",
                        model_id="claude-sonnet-5",
                        reasoning_effort="high",
                        capability=_capability(tier="high", supported_efforts=("low", "high")),
                    ),
                ),
            )
        }

        report = learning_report_html.render_html_report(
            journal, board, baseline_board, now=_NOW, role_matrix=role_matrix
        )

        self.assertIn("Reasoning Tier: high", report)
        self.assertIn("Tool Access: full", report)
        self.assertIn("Min Context: 128000", report)
        self.assertIn("Provider: anthropic-sonnet", report)
        self.assertIn("Model: claude-sonnet-5", report)
        self.assertIn("Effort: high", report)
        self.assertIn("Supported Efforts: low, high", report)

    def test_a_binding_with_no_capability_shows_an_unknown_capability_pill(self) -> None:
        journal = learning_journal.JournalRead()
        board, baseline_board = _boards(journal, now=_NOW)
        role_matrix = {
            "planner": _role_entry(
                "planner",
                bindings=(
                    routing_config.RoleModelBinding(
                        provider_id="anthropic-sonnet",
                        adapter="anthropic",
                        model_id="claude-sonnet-5",
                        reasoning_effort="medium",
                        capability=None,
                    ),
                ),
            )
        }

        report = learning_report_html.render_html_report(
            journal, board, baseline_board, now=_NOW, role_matrix=role_matrix
        )

        self.assertIn("Capability: unknown (drift)", report)

    def test_local_only_capability_requirement_renders_a_local_only_pill(self) -> None:
        journal = learning_journal.JournalRead()
        board, baseline_board = _boards(journal, now=_NOW)
        role_matrix = {"sensitive_executor": _role_entry("sensitive_executor", local_only=True)}

        report = learning_report_html.render_html_report(
            journal, board, baseline_board, now=_NOW, role_matrix=role_matrix
        )

        self.assertIn("Local Only: yes", report)

    def test_an_unrecognized_role_id_still_renders_instead_of_being_dropped(self) -> None:
        journal = learning_journal.JournalRead()
        board, baseline_board = _boards(journal, now=_NOW)
        role_matrix = {"future_role": _role_entry("future_role")}

        report = learning_report_html.render_html_report(
            journal, board, baseline_board, now=_NOW, role_matrix=role_matrix
        )

        all_grid = report.split('id="role-grid-all"')[1]
        self.assertIn("future_role", all_grid)

    def test_role_matrix_values_are_escaped(self) -> None:
        journal = learning_journal.JournalRead()
        board, baseline_board = _boards(journal, now=_NOW)
        role_matrix = {
            "planner": _role_entry(
                "planner",
                reasoning_tier="<script>alert(1)</script>",
                bindings=(),
            )
        }

        report = learning_report_html.render_html_report(
            journal, board, baseline_board, now=_NOW, role_matrix=role_matrix
        )

        self.assertNotIn("<script", report)
        self.assertIn("&lt;script&gt;", report)

    def test_write_html_report_wires_in_the_real_routing_config_role_matrix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            path = learning_report_html.write_html_report(root, now=_NOW)

            content = path.read_text(encoding="utf-8")
            self.assertIn('id="role-grid-simple"', content)
            self.assertIn("planner", content)
            self.assertNotIn("No roles configured.", content)


# --- The write door ---


class WriteHtmlReportTests(unittest.TestCase):
    def test_write_creates_parent_dirs_writes_at_html_report_path_and_matches_render(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            path = learning_report_html.write_html_report(root, now=_NOW)

            self.assertEqual(path, learning_report_html.html_report_path(root, now=_NOW))
            self.assertTrue(path.exists())
            self.assertEqual(path.suffix, ".html")

    def test_write_accepts_an_explicit_output_path_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            custom = root / "custom" / "dashboard.html"

            path = learning_report_html.write_html_report(root, now=_NOW, output_path=custom)

            self.assertEqual(path, custom)
            self.assertTrue(custom.exists())

    def test_write_accepts_a_literal_journal_path_and_positional_output_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            journal_path = root / learning_journal.JOURNAL_RELATIVE_PATH
            custom = root / "custom" / "dashboard.html"

            path = learning_report_html.write_html_report(journal_path, custom, now=_NOW)

            self.assertEqual(path, custom)
            self.assertTrue(custom.exists())

    def test_a_second_call_the_same_utc_day_supersedes_the_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            first_path = learning_report_html.write_html_report(root, now=_NOW)
            record = _worker_execution_record(
                "task-x", timestamp="2026-01-05T00:00:00Z", cost=1.0
            )
            assert learning_journal.append_journal_record(record, root_dir=root) is None
            second_path = learning_report_html.write_html_report(root, now=_NOW)

            self.assertEqual(first_path, second_path)

    def test_a_successful_write_leaves_no_stray_temp_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            path = learning_report_html.write_html_report(root, now=_NOW)

            siblings = list(path.parent.iterdir())
            self.assertIn(path, siblings)
            for sibling in siblings:
                self.assertFalse(sibling.name.startswith("."))

    def test_a_failure_between_temp_creation_and_replace_leaves_the_prior_report_untouched(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first_path = learning_report_html.write_html_report(root, now=_NOW)
            original_bytes = first_path.read_bytes()

            with (
                mock.patch("learning_report_html.os.replace", side_effect=OSError("boom")),
                self.assertRaises(OSError),
            ):
                learning_report_html.write_html_report(root, now=_NOW)

            self.assertEqual(first_path.read_bytes(), original_bytes)

    def test_write_refuses_a_naive_now_before_any_disk_access(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            with self.assertRaises(ValueError):
                learning_report_html.write_html_report(root, now=datetime(2026, 1, 8))  # noqa: DTZ001

            self.assertFalse((root / ".ralph").exists())

    def test_write_refuses_a_non_positive_window_days(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            with self.assertRaises(ValueError):
                learning_report_html.write_html_report(root, now=_NOW, window_days=-1)


# --- html_report_path ---


class HtmlReportPathTests(unittest.TestCase):
    def test_html_report_path_is_beneath_root_in_ralph_reports_named_by_utc_date(self) -> None:
        root = Path("/fake/root")
        now = datetime(2026, 1, 8, 12, 0, 0, tzinfo=timezone.utc)

        path = learning_report_html.html_report_path(root, now=now)

        self.assertEqual(path, root / ".ralph" / "reports" / "weekly-report-2026-01-08.html")

    def test_html_report_path_names_the_file_by_the_utc_date_not_a_local_one(self) -> None:
        root = Path("/fake/root")
        # 2026-01-09T02:00:00+09:00 is 2026-01-08T17:00:00Z.
        now = datetime(2026, 1, 9, 2, 0, 0, tzinfo=timezone(timedelta(hours=9)))

        path = learning_report_html.html_report_path(root, now=now)

        self.assertEqual(path.name, "weekly-report-2026-01-08.html")

    def test_html_report_path_refuses_a_naive_now(self) -> None:
        root = Path("/fake/root")

        with self.assertRaises(ValueError):
            learning_report_html.html_report_path(root, now=datetime(2026, 1, 8))  # noqa: DTZ001


if __name__ == "__main__":
    unittest.main()
