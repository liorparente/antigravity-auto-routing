#!/usr/bin/env python3
"""Unit tests for `acceptance_gate` (spec 0004 ticket 18).

Modules are loaded by path with `importlib.util.spec_from_file_location`,
the pattern `test_learning_report.py` already uses: these files are not a
package, and `acceptance_gate.py`'s bare `import learning_journal` /
`import learning_scoreboard` only resolve because those two names are
registered in `sys.modules` first.
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

MODULE_PATH = Path(__file__).with_name("learning_journal.py")
LEARNING_SCOREBOARD_PATH = Path(__file__).with_name("learning_scoreboard.py")
ACCEPTANCE_GATE_PATH = Path(__file__).with_name("acceptance_gate.py")

learning_journal_spec = importlib.util.spec_from_file_location("learning_journal", MODULE_PATH)
assert learning_journal_spec is not None and learning_journal_spec.loader is not None
learning_journal = importlib.util.module_from_spec(learning_journal_spec)
sys.modules["learning_journal"] = learning_journal
learning_journal_spec.loader.exec_module(learning_journal)

learning_scoreboard_spec = importlib.util.spec_from_file_location(
    "learning_scoreboard", LEARNING_SCOREBOARD_PATH
)
assert learning_scoreboard_spec is not None and learning_scoreboard_spec.loader is not None
learning_scoreboard = importlib.util.module_from_spec(learning_scoreboard_spec)
sys.modules["learning_scoreboard"] = learning_scoreboard
learning_scoreboard_spec.loader.exec_module(learning_scoreboard)

acceptance_gate_spec = importlib.util.spec_from_file_location(
    "acceptance_gate", ACCEPTANCE_GATE_PATH
)
assert acceptance_gate_spec is not None and acceptance_gate_spec.loader is not None
acceptance_gate = importlib.util.module_from_spec(acceptance_gate_spec)
sys.modules["acceptance_gate"] = acceptance_gate
acceptance_gate_spec.loader.exec_module(acceptance_gate)

# A shared, timezone-aware `now` for every test below — never used to derive
# a live clock reading, only as a fixed injected value.
_NOW = datetime(2026, 1, 8, tzinfo=timezone.utc)


def _scripted_runner(scores: list[float]) -> Callable[[], float]:
    """A runner that returns each of `scores` in order, one per call."""
    remaining = iter(scores)
    return lambda: next(remaining)


class AcceptanceTests(unittest.TestCase):
    def test_every_trial_meeting_threshold_with_no_regression_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decision = acceptance_gate.evaluate_proposal(
                _scripted_runner([0.9, 0.85, 0.95]),
                task_set="bench-v1",
                root_dir=root,
                now=_NOW,
                trials=3,
                score_threshold=0.8,
            )

        self.assertTrue(decision.threshold_met)
        self.assertFalse(decision.comparison.has_regression)
        self.assertTrue(decision.accepted)
        self.assertEqual(len(decision.trial_records), 3)
        self.assertTrue(all(t.success for t in decision.trial_records))

    def test_a_trial_scoring_exactly_the_threshold_counts_as_meeting_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decision = acceptance_gate.evaluate_proposal(
                _scripted_runner([0.8, 0.8]),
                task_set="bench-v1",
                root_dir=root,
                now=_NOW,
                trials=2,
                score_threshold=0.8,
            )

        self.assertTrue(decision.accepted)


class SingleLuckyRunRejectionTests(unittest.TestCase):
    def test_one_winning_trial_among_losing_ones_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decision = acceptance_gate.evaluate_proposal(
                _scripted_runner([0.95, 0.2, 0.3]),
                task_set="bench-v1",
                root_dir=root,
                now=_NOW,
                trials=3,
                score_threshold=0.8,
            )

        self.assertFalse(decision.threshold_met)
        self.assertFalse(decision.accepted)
        # Not a mean-based rejection either: the mean of (0.95, 0.2, 0.3) is
        # well below 0.8 too, so this alone would not distinguish "every
        # trial must clear the bar" from "the average must clear it". The
        # test below does.
        self.assertEqual([t.score for t in decision.trial_records], [0.95, 0.2, 0.3])

    def test_a_mean_above_threshold_does_not_rescue_a_single_losing_trial(self) -> None:
        """The mean of (0.95, 0.7) is 0.825 — above an 0.8 threshold — but
        the second trial alone is below it. If acceptance were mean-based
        this would accept; the per-trial rule rejects it."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decision = acceptance_gate.evaluate_proposal(
                _scripted_runner([0.95, 0.7]),
                task_set="bench-v1",
                root_dir=root,
                now=_NOW,
                trials=2,
                score_threshold=0.8,
            )

        self.assertFalse(decision.threshold_met)
        self.assertFalse(decision.accepted)


class ScoreboardRegressionRejectionTests(unittest.TestCase):
    def test_a_regressed_metric_rejects_even_with_an_excellent_score(self) -> None:
        """Simulates real production activity landing *while* the gate's
        trials run: the runner itself — a full closure, not a bare number —
        appends a second `ComplianceRecord` for the baseline's own session,
        carrying a much worse `violation_count`. `ComplianceRecord`'s own
        reduction rule (last record for a `session_id` wins) means the
        `current` scoreboard, read after the trial loop, sees the worse
        number while `baseline`, read before it, does not."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertIsNone(
                learning_journal.append_journal_record(
                    learning_journal.ComplianceRecord(
                        session_id="session-concurrent",
                        total_writes=1,
                        code_writes=0,
                        routing_declarations=1,
                        worker_calls=0,
                        violation_count=0,
                        declaration_drift_count=0,
                        calibration_markers=0,
                        code_write_count=0,
                        timestamp="2026-01-05T00:00:00Z",
                        session_last_activity="2026-01-05T00:00:00Z",
                    ),
                    root_dir=root,
                )
            )

            def runner() -> float:
                error = learning_journal.append_journal_record(
                    learning_journal.ComplianceRecord(
                        session_id="session-concurrent",
                        total_writes=1,
                        code_writes=0,
                        routing_declarations=1,
                        worker_calls=0,
                        violation_count=9,
                        declaration_drift_count=0,
                        calibration_markers=0,
                        code_write_count=0,
                        timestamp="2026-01-06T00:00:00Z",
                        session_last_activity="2026-01-06T00:00:00Z",
                    ),
                    root_dir=root,
                )
                assert error is None
                return 0.99

            decision = acceptance_gate.evaluate_proposal(
                runner,
                task_set="bench-v1",
                root_dir=root,
                now=_NOW,
                trials=3,
                score_threshold=0.8,
            )

        self.assertTrue(decision.threshold_met, "the benchmark score itself is excellent")
        self.assertEqual(
            decision.comparison.regressed,
            ("violations_per_session",),
            "the concurrent compliance record is what regressed, not the benchmark",
        )
        self.assertFalse(
            decision.accepted,
            "a regressed scoreboard metric rejects even when the score is excellent",
        )

    def test_a_batch_that_drags_down_its_own_benchmark_trend_regresses_too(self) -> None:
        """A candidate probe's benchmark trend remains telemetry, not a
        second admission bar. Prior history in the window scored high (0.98,
        0.97); every one of this batch's three trials individually clears the
        0.8 threshold at 0.81, while blending them into the window regresses
        `mean_benchmark_score`. ADR 0008 assigns anti-ratchet enforcement to
        Ticket 21's post-adoption auto-revert, so this benchmark-only
        regression does not reject the un-adopted candidate."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for score, timestamp in ((0.98, "2026-01-02T00:00:00Z"), (0.97, "2026-01-03T00:00:00Z")):
                record = learning_journal.ReplayBenchmarkRecord(
                    task_set="bench-v1", success=True, score=score, timestamp=timestamp
                )
                self.assertIsNone(
                    learning_journal.append_journal_record(record, root_dir=root)
                )

            decision = acceptance_gate.evaluate_proposal(
                _scripted_runner([0.81, 0.81, 0.81]),
                task_set="bench-v1",
                root_dir=root,
                now=_NOW,
                trials=3,
                score_threshold=0.8,
            )

        self.assertTrue(decision.threshold_met, "every trial individually cleared 0.8")
        self.assertEqual(decision.comparison.regressed, ("mean_benchmark_score",))
        self.assertTrue(decision.accepted)


class RunnerFailureFailsClosedTests(unittest.TestCase):
    def test_a_runner_that_always_raises_rejects_every_trial(self) -> None:
        def flaky() -> float:
            raise RuntimeError("evaluator crashed")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decision = acceptance_gate.evaluate_proposal(
                flaky, task_set="bench-v1", root_dir=root, now=_NOW, trials=3
            )

        self.assertFalse(decision.threshold_met)
        self.assertFalse(decision.accepted)
        self.assertEqual(len(decision.trial_records), 3)
        self.assertTrue(all(not t.success for t in decision.trial_records))
        self.assertTrue(all(t.score is None for t in decision.trial_records))

    def test_a_runner_returning_an_unjournalable_score_fails_closed_not_uncaught(self) -> None:
        """A runner that *returns* instead of raising — NaN, negative, the
        wrong type — is exactly as failed a trial as one that raised. Before
        this was fixed, `ReplayBenchmarkRecord`'s own validation raised
        *inside* the trial loop's success branch, uncaught, crashing the
        whole batch instead of failing that one trial closed."""
        for bad_score in (float("nan"), float("-inf"), -1.0, "not a number"):
            with self.subTest(bad_score=bad_score):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    decision = acceptance_gate.evaluate_proposal(
                        lambda score=bad_score: score,
                        task_set="bench-v1",
                        root_dir=root,
                        now=_NOW,
                        trials=2,
                    )

                self.assertFalse(decision.threshold_met)
                self.assertFalse(decision.accepted)
                self.assertTrue(all(not t.success for t in decision.trial_records))
                self.assertTrue(all(t.score is None for t in decision.trial_records))

    def test_a_runner_that_fails_only_once_still_rejects_the_whole_batch(self) -> None:
        """Two excellent trials and one crash: the crash alone is enough to
        reject, and every trial — the two good ones included — still runs
        and reaches the journal rather than the batch stopping early."""
        calls = {"count": 0}

        def flaky_once() -> float:
            calls["count"] += 1
            if calls["count"] == 2:
                raise RuntimeError("evaluator crashed")
            return 0.99

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decision = acceptance_gate.evaluate_proposal(
                flaky_once, task_set="bench-v1", root_dir=root, now=_NOW, trials=3
            )

        self.assertEqual(calls["count"], 3, "every trial is attempted, not just up to the failure")
        self.assertFalse(decision.threshold_met)
        self.assertFalse(decision.accepted)
        self.assertEqual(
            [t.success for t in decision.trial_records], [True, False, True]
        )


class EveryTrialReachesTheJournalTests(unittest.TestCase):
    def test_a_failed_trial_is_journaled_as_failed_rather_than_omitted(self) -> None:
        def flaky() -> float:
            raise RuntimeError("boom")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decision = acceptance_gate.evaluate_proposal(
                flaky, task_set="bench-v1", root_dir=root, now=_NOW, trials=2
            )

            journal = learning_journal.read_journal(root)

        self.assertEqual(len(journal.replay_benchmarks), 2)
        self.assertTrue(all(not r.success for r in journal.replay_benchmarks))
        self.assertTrue(all(r.score is None for r in journal.replay_benchmarks))
        self.assertEqual(journal.replay_benchmarks, decision.trial_records)

    def test_every_trial_shares_a_task_set_and_an_explicit_run_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            acceptance_gate.evaluate_proposal(
                _scripted_runner([0.9, 0.9]),
                task_set="bench-v1",
                root_dir=root,
                now=_NOW,
                trials=2,
                run_id="run-eval-1",
            )

            journal = learning_journal.read_journal(root)

        self.assertEqual(len(journal.replay_benchmarks), 2)
        for record in journal.replay_benchmarks:
            self.assertEqual(record.task_set, "bench-v1")
            self.assertEqual(record.run_id, "run-eval-1")

    def test_a_failed_trial_carries_the_same_identity_and_the_injected_now(self) -> None:
        """The `except` arm builds a second `ReplayBenchmarkRecord`, and every
        field it carries has to be passed there too.

        The test above drives a runner that always succeeds, so it never
        reaches that arm — and each of the three fields could be dropped from
        it with the whole suite staying green. `timestamp` is the one that
        matters most: omitting it falls back to `learning_journal`'s live
        clock, so a failed trial is stamped with the wall time instead of the
        injected `now`, lands after it, and is cut from the `current` board by
        `_prefix_cut` — the same disappearance `_wire_timestamp`'s missing UTC
        conversion caused on the success path, on the one path that exists to
        prove a runner failure is never silently absent.
        """
        def always_raises() -> float:
            raise RuntimeError("evaluator crashed")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decision = acceptance_gate.evaluate_proposal(
                always_raises,
                task_set="bench-v1",
                root_dir=root,
                now=_NOW,
                trials=2,
                run_id="run-eval-1",
            )
            journal = learning_journal.read_journal(root)

        self.assertFalse(decision.accepted)
        self.assertEqual(len(journal.replay_benchmarks), 2)
        for record in journal.replay_benchmarks:
            self.assertFalse(record.success)
            self.assertIsNone(record.score)
            self.assertEqual(record.task_set, "bench-v1")
            self.assertEqual(record.run_id, "run-eval-1")
            self.assertEqual(record.timestamp, "2026-01-08T00:00:00Z")


class MultiTrialTrendTests(unittest.TestCase):
    def test_the_batchs_trials_feed_a_real_mean_benchmark_score(self) -> None:
        """The gate's own writes are what the scoreboard's fourth metric
        family reads back — the end-to-end path ticket 26 exists to close."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            acceptance_gate.evaluate_proposal(
                _scripted_runner([0.6, 0.8, 1.0]),
                task_set="bench-v1",
                root_dir=root,
                now=_NOW,
                trials=3,
                score_threshold=0.5,
            )

            board = learning_scoreboard.read_scoreboard(root, now=_NOW)

        metric = board.replay_benchmark.mean_benchmark_score
        self.assertIsInstance(metric, learning_scoreboard.MetricValue)
        assert isinstance(metric, learning_scoreboard.MetricValue)
        self.assertAlmostEqual(metric.value, 0.8)
        self.assertEqual(metric.sample_size, 3)


class InputValidationTests(unittest.TestCase):
    def test_a_naive_now_is_refused(self) -> None:
        """The gate's own `_require_aware_now` fires, not the identical guard
        inside `read_scoreboard` one statement later.

        Asserting `ValueError` alone cannot tell the two apart — the messages
        are byte-identical, so even `assertRaisesRegex` could not — and
        deleting the gate's mirror entirely left this test green. The
        collaborator is stubbed to fail if reached, which is the property the
        mirror actually provides: a naive `now` is refused at the front door,
        before `_wire_timestamp` has silently reinterpreted a local wall
        clock as UTC and stamped every trial with it.
        """
        def _unreachable(*args: object, **kwargs: object) -> object:
            raise AssertionError("read_scoreboard was reached despite a naive now")

        real_read_scoreboard = acceptance_gate.learning_scoreboard.read_scoreboard
        acceptance_gate.learning_scoreboard.read_scoreboard = _unreachable
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                with self.assertRaises(ValueError):
                    acceptance_gate.evaluate_proposal(
                        _scripted_runner([0.9]),
                        task_set="bench-v1",
                        root_dir=root,
                        now=datetime(2026, 1, 8),  # noqa: DTZ001 - the value under test
                    )
        finally:
            acceptance_gate.learning_scoreboard.read_scoreboard = real_read_scoreboard

    def test_a_non_positive_or_boolean_trial_count_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for bad_trials in (0, -1, True):
                with self.subTest(trials=bad_trials), self.assertRaises(ValueError):
                    acceptance_gate.evaluate_proposal(
                        _scripted_runner([0.9]),
                        task_set="bench-v1",
                        root_dir=root,
                        now=_NOW,
                        trials=bad_trials,
                    )

    def test_a_non_finite_score_threshold_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # `True` for the same reason `_validate_trials`' own test carries
            # it: `bool` is an `int` subclass, so without the explicit
            # `isinstance(value, bool)` rejection a threshold of `True`
            # silently means 1.0 — a bar no ordinary trial clears.
            for bad_threshold in (float("nan"), float("inf"), "0.8", True):
                with self.subTest(score_threshold=bad_threshold), self.assertRaises(ValueError):
                    acceptance_gate.evaluate_proposal(
                        _scripted_runner([0.9]),
                        task_set="bench-v1",
                        root_dir=root,
                        now=_NOW,
                        score_threshold=bad_threshold,  # type: ignore[arg-type]
                    )

    def test_a_malformed_task_set_is_refused_before_the_runner_is_ever_called(self) -> None:
        """Validated at wiring time, the same rule
        `production_invoker.make_journaled_invoke_worker` documents for
        `task_id`: a caller finds out its `task_set` is unjournalable before
        paying for even one benchmark trial, not once per trial."""
        calls = {"count": 0}

        def counting_runner() -> float:
            calls["count"] += 1
            return 0.9

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(ValueError):
                acceptance_gate.evaluate_proposal(
                    counting_runner,
                    task_set="bench set with spaces",
                    root_dir=root,
                    now=_NOW,
                    trials=3,
                )

        self.assertEqual(calls["count"], 0)

    def test_a_malformed_run_id_is_refused_before_the_runner_is_ever_called(self) -> None:
        calls = {"count": 0}

        def counting_runner() -> float:
            calls["count"] += 1
            return 0.9

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(ValueError):
                acceptance_gate.evaluate_proposal(
                    counting_runner,
                    task_set="bench-v1",
                    root_dir=root,
                    now=_NOW,
                    trials=3,
                    run_id="run with spaces",
                )

        self.assertEqual(calls["count"], 0)


class WindowDaysTests(unittest.TestCase):
    def test_a_custom_window_reaches_both_boards_and_changes_the_verdict(self) -> None:
        """`window_days` has to reach *both* `read_scoreboard` calls.

        `compare_scoreboards` refuses two boards computed over different
        spans, so dropping the argument from either read turns every
        non-default call into a `ValueError` — and no test passed a
        non-default `window_days` at all, so the whole parameter was
        unexercised and that mutation stayed green.

        Asserting "it did not raise" would be enough to catch that and would
        prove nothing about the window itself, so this drives the span until
        it changes the answer. A clean compliance record ten days old is
        inside a 14-day baseline but outside the default seven-day one. The
        runner appends a concurrent record with five violations. At 14 that
        live-system regression rejects the proposal; at seven the older
        baseline is absent, so there is no regression and the identical
        candidate is accepted.
        """
        older = _NOW - timedelta(days=10)

        def run(window_days: int) -> Any:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                record = learning_journal.ComplianceRecord(
                    session_id="session-window",
                    total_writes=1,
                    code_writes=0,
                    routing_declarations=1,
                    worker_calls=0,
                    violation_count=0,
                    declaration_drift_count=0,
                    calibration_markers=0,
                    code_write_count=0,
                    timestamp=older.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    session_last_activity=older.strftime("%Y-%m-%dT%H:%M:%SZ"),
                )
                self.assertIsNone(
                    learning_journal.append_journal_record(record, root_dir=root)
                )
                def runner() -> float:
                    error = learning_journal.append_journal_record(
                        learning_journal.ComplianceRecord(
                            session_id="session-window",
                            total_writes=1,
                            code_writes=0,
                            routing_declarations=1,
                            worker_calls=0,
                            violation_count=5,
                            declaration_drift_count=0,
                            calibration_markers=0,
                            code_write_count=0,
                            timestamp=_NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
                            session_last_activity=_NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        ),
                        root_dir=root,
                    )
                    assert error is None
                    return 0.81

                return acceptance_gate.evaluate_proposal(
                    runner,
                    task_set="bench-v1",
                    root_dir=root,
                    now=_NOW,
                    trials=2,
                    score_threshold=0.8,
                    window_days=window_days,
                )

        wide = run(14)
        narrow = run(acceptance_gate.DEFAULT_WINDOW_DAYS)

        self.assertTrue(wide.threshold_met, "every trial cleared 0.8 either way")
        self.assertEqual(wide.comparison.regressed, ("violations_per_session",))
        self.assertFalse(wide.accepted, "the ten-day-old clean baseline is inside 14 days")

        self.assertTrue(narrow.threshold_met)
        self.assertEqual(narrow.comparison.regressed, ())
        self.assertTrue(narrow.accepted, "the clean baseline is outside the default 7-day window")


class NonUtcNowTests(unittest.TestCase):
    def test_trials_are_stamped_in_utc_and_land_inside_their_own_window(self) -> None:
        """`_wire_timestamp`'s `.astimezone(timezone.utc)`, which every other
        test leaves a no-op by injecting a `now` that is already UTC.

        The conversion is not cosmetic. `read_scoreboard` cuts the journal to
        the records at or before `now` (`_prefix_cut`), and it compares wire
        timestamps — so a batch stamped with the caller's local wall clock
        instead of its UTC instant reads as *later* than the `now` that
        produced it, for any positive offset, and the gate's own trials are
        cut from the `current` board it just wrote them for. This is the
        failure `acceptance_gate.py`'s docstring names ("a caller testing
        with a fixed, non-current `now` would find its own just-written
        trials excluded from the 'current' scoreboard's `<= now` prefix
        cut"), and until this test nothing asserted it: dropping the
        conversion left every other gate test green, because a vanished batch
        moves no metric and so regresses nothing.
        """
        now = datetime(2026, 1, 8, 10, 0, tzinfo=timezone(timedelta(hours=2)))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decision = acceptance_gate.evaluate_proposal(
                _scripted_runner([0.9, 0.9]),
                task_set="bench-v1",
                root_dir=root,
                now=now,
                trials=2,
            )
            board = learning_scoreboard.read_scoreboard(root, now=now)

        for record in decision.trial_records:
            self.assertEqual(record.timestamp, "2026-01-08T08:00:00Z")
        metric = board.replay_benchmark.mean_benchmark_score
        self.assertIsInstance(metric, learning_scoreboard.MetricValue)
        assert isinstance(metric, learning_scoreboard.MetricValue)
        self.assertEqual(
            metric.sample_size,
            2,
            "the batch's own trials must be inside the window it just wrote them into",
        )


class JournalWriteFailureTests(unittest.TestCase):
    def test_a_journal_write_failure_rejects_a_batch_that_otherwise_passed(self) -> None:
        """Every trial scores 0.9 against a 0.8 bar and no metric regresses,
        so the only thing standing between this batch and acceptance is that
        none of its records reached the disk.

        The three assertions below are what make this fail-closed rather than
        merely false: `threshold_met` is `True` and `has_regression` is
        `False`, which rules out both of the other two rejection causes, so
        `accepted is False` can only have come from `journal_complete`. The
        on-disk check is the point of the rule — an accepted proposal whose
        evidence is an empty journal is indistinguishable, afterwards, from
        one backed by five excellent trials.
        """
        reported: list[str] = []

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # A read-only journal *file*, not `.ralph` replaced by a plain
            # file: `evaluate_proposal` reads the journal too
            # (`read_scoreboard`, for the baseline board), and a `.ralph`
            # that is a file rather than a directory makes `open(..., "rb")`
            # raise `NotADirectoryError` there — before a single trial runs,
            # for a reason unrelated to what this test means to exercise.
            # Read-only permissions let the read through and fail only the
            # `open(path, "a", ...)` append.
            path = learning_journal.journal_path(root)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
            path.chmod(0o444)
            try:
                decision = acceptance_gate.evaluate_proposal(
                    _scripted_runner([0.9, 0.9]),
                    task_set="bench-v1",
                    root_dir=root,
                    now=_NOW,
                    trials=2,
                    report_journal_error=reported.append,
                )
            finally:
                path.chmod(0o644)
            journalled = learning_journal.read_journal(root).replay_benchmarks

        self.assertTrue(decision.threshold_met, "every trial cleared the bar")
        self.assertFalse(
            decision.comparison.has_regression, "no metric moved — nothing was written"
        )
        self.assertFalse(decision.journal_complete)
        self.assertFalse(
            decision.accepted,
            "a gate that could not record why it opened must not open",
        )
        self.assertEqual(journalled, (), "the batch's evidence never reached the disk")
        self.assertEqual(len(decision.trial_records), 2)
        self.assertEqual(len(reported), 2)
        for message in reported:
            self.assertIn("failed to write learning journal record", message)

    def test_a_journalled_batch_still_reports_the_journal_as_complete(self) -> None:
        """The other side of the same switch, and what keeps the assertion
        above from passing for the wrong reason: an identical batch against a
        writable journal accepts, `journal_complete` is `True`, and both
        records are on disk. Without this, `accepted is False` above would
        also be satisfied by a gate that rejected everything.
        """
        reported: list[str] = []

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decision = acceptance_gate.evaluate_proposal(
                _scripted_runner([0.9, 0.9]),
                task_set="bench-v1",
                root_dir=root,
                now=_NOW,
                trials=2,
                report_journal_error=reported.append,
            )
            journalled = learning_journal.read_journal(root).replay_benchmarks

        self.assertTrue(decision.journal_complete)
        self.assertTrue(decision.accepted)
        self.assertEqual(reported, [])
        self.assertEqual(len(journalled), 2)


class TaskSetBumpTests(unittest.TestCase):
    """Ticket 29 — verify evaluate_proposal across task-set version bumps."""

    def test_proposal_evaluation_across_task_set_version_bump_does_not_reject(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Seed the journal with bench-v1 records
            v1_record = learning_journal.ReplayBenchmarkRecord(
                task_set="bench-v1", success=True, score=1.0, timestamp="2026-01-05T00:00:00Z"
            )
            learning_journal.append_journal_record(v1_record, root_dir=root)

            # Evaluate proposal with task_set="bench-v2" and runner returning 0.85
            decision = acceptance_gate.evaluate_proposal(
                _scripted_runner([0.85, 0.85]),
                task_set="bench-v2",
                root_dir=root,
                now=_NOW,
                trials=2,
                score_threshold=0.8,
            )

        self.assertTrue(decision.accepted)
        self.assertFalse(decision.comparison.has_regression)

    def test_subsequent_proposal_on_same_task_set_detects_true_regression(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # First, evaluate a proposal that establishes the bench-v2 baseline of 0.85
            decision1 = acceptance_gate.evaluate_proposal(
                _scripted_runner([0.85, 0.85]),
                task_set="bench-v2",
                root_dir=root,
                now=_NOW - timedelta(hours=1),
                trials=2,
                score_threshold=0.8,
            )
            self.assertTrue(decision1.accepted)

            # Second, run subsequent evaluate_proposal on bench-v2 where runner returns 0.7
            decision2 = acceptance_gate.evaluate_proposal(
                _scripted_runner([0.7, 0.7]),
                task_set="bench-v2",
                root_dir=root,
                now=_NOW,
                trials=2,
                score_threshold=0.8,
            )

        self.assertFalse(decision2.accepted)
        self.assertEqual(decision2.comparison.regressed, ("mean_benchmark_score",))


if __name__ == "__main__":
    unittest.main()
