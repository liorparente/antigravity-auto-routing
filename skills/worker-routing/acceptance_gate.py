#!/usr/bin/env python3
"""AcceptanceGate: repeated benchmark trials, non-benchmark regression guard.

Named for its siblings `learning_journal.py` (the record contract),
`learning_scoreboard.py` (the eight-metric snapshot), and `learning_report.py`
(the weekly renderer). This module is spec 0004 ticket 18: the gate a
`LearnerWorker`'s proposal must clear before a routing-table update auto-
applies (`docs/specs/0004-learning-loop.md`'s "Risk-tiered application").

**The third and final test seam.** `learning_journal.py`'s own docstring
names its one seam (`root_dir`) and defers the other two — "the worker
callable and the benchmark runner — [that] belong to later modules." The
worker callable is `critical_dialogue.InvokeWorker`/
`production_invoker.make_journaled_invoke_worker`. `evaluate_proposal`'s
`runner` parameter is the last one: a zero-argument callable that returns one
trial's score in production (driving the real evaluator) and a scripted
value in tests. No fourth seam is added here. `report_journal_error` is
injected too, and is deliberately not counted as one: a seam in the ticket's
sense is a place *evidence enters* — swap it and the verdict changes — while
this parameter is where a failure *leaves*, the observability sink
`production_invoker.py` already established for this exact append failure.
Nothing handed to it is ever read back; `journal_complete` alone carries
that fact into the decision.

**The learner never grades its own proposal.** Every score in a `GateDecision`
came from `runner()` and nowhere else — there is no code path in this module,
or in the `ReplayBenchmarkRecord`s it writes, that derives a score from
anything but the injected callable's return value. That is the single most
load-bearing decision in spec 0004 (external signals beat self-assessment;
see `docs/research/self-improvement-prior-art.md`), and it is enforced by
this module simply never containing a second way to produce one.

**The gate separates candidate quality from live-system regression.** Every
candidate probe trial is judged against the absolute `score_threshold`: every
trial must succeed and score at or above that threshold. `baseline` is read
before the trial loop and `current` after it, so the resulting comparison
also captures concurrent system activity during the gate run. A regression in
discipline, critique authenticity, or efficiency rejects the proposal; a
`mean_benchmark_score` regression by itself does not. The candidate probe is
not adopted system state, so blending its scores into a historical probe mean
must not create a second, implicit admission bar. `GateDecision.comparison`
still exposes every movement, including the benchmark mean, for complete
telemetry.

**Acceptance requires every trial to clear the bar, durable evidence, and no
concurrent non-benchmark regression.** A proposal is accepted only when
*every* trial in the batch both succeeded and scored at or above
`score_threshold`, every trial reached the journal, and no non-benchmark
scoreboard metric regressed. A single winning run among losing ones is
rejected directly: if even one trial scores below threshold (or fails
outright), `threshold_met` is `False` — no mean, no majority vote, ever
launders one bad run into an accepted score. The anti-ratchet protection for
`mean_benchmark_score` belongs to Ticket 21's post-adoption auto-revert,
which compares live post-adoption metrics against their pre-adoption baseline
(ADR 0008).

**A runner failure fails the gate closed, not silently.** Every trial is
attempted — even after an earlier one raised — so the batch always produces
`trials` `ReplayBenchmarkRecord`s and the replay-benchmark trend never gets a
silent gap (ticket 26's own requirement). "Failure" covers both shapes a
`runner()` call can take: raising outright, and *returning* a value
`ReplayBenchmarkRecord` itself refuses (non-finite, negative, the wrong
type) — both are caught by the same `try`, around both the call and the
success record's construction, so a bad return value fails one trial closed
exactly like a raised exception does rather than crashing the whole batch
uncaught. Either way the trial is journaled as `success=False`: a failed
trial can never satisfy `threshold_met`, so a runner that cannot run at all
rejects every proposal it is asked to evaluate, which is the fail-closed
behaviour the ticket asks for. Neither the exception nor the rejected value
is re-raised or inspected — a benchmark runner's failure mode is not this
gate's business, only the fact that it failed is.

**A journal write that fails rejects too.** Constructing `trials` records is
not the same as landing them: `append_journal_record` returns its error
rather than raising, so a full disk or an unwritable `.ralph` leaves the
batch's evidence in memory and nowhere else. `current` is then read back
from that same disk and sees none of it, so `compare_scoreboards` finds
nothing moved, `has_regression` is `False`, and — before this rule —
`accepted` came back `True` for a proposal whose entire benchmark evidence
had just evaporated. That is precisely the silent gap ticket 26's "each
trial reaches the journal ... so the trend has no silent gaps" forbids, and
it is undetectable after the fact: an acceptance backed by five excellent
trials and one backed by five vanished ones are indistinguishable once the
process exits, and ticket 21's auto-revert is left with no post-adoption
trend to compare against. A disk that cannot be written to is indeed an
environment failure rather than evidence about the proposal — but a gate
that cannot record why it opened must not open, and that is what fail-closed
means here. The cost is the honest one: an environment failure defers a good
proposal to the next run, loudly, rather than applying an unrecorded one
quietly. `GateDecision.journal_complete` reports which of the two happened,
so a caller never has to infer it from the rejection.

**This module owns no clock.** `now` is always injected, matching
`learning_scoreboard.py` and `learning_report.py`'s own guarantee. Every
`ReplayBenchmarkRecord` this module writes is stamped from `now`, not from
`learning_journal._utc_timestamp`'s live clock — otherwise a caller testing
with a fixed, non-current `now` would find its own just-written trials
excluded from the "current" scoreboard's `<= now` prefix cut.
"""
from __future__ import annotations

import math
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

if __package__:
    from . import learning_journal, learning_scoreboard, routing_config
else:
    import learning_journal  # type: ignore[no-redef]
    import learning_scoreboard  # type: ignore[no-redef]
    import routing_config  # type: ignore[no-redef]

# The trailing window `evaluate_proposal` uses to compare scoreboards before
# and after a trial batch, re-exported from `learning_scoreboard` rather than
# a second literal — the same "one source" reasoning `learning_report.py`
# documents for its own `DEFAULT_WINDOW_DAYS`.
DEFAULT_WINDOW_DAYS: int = learning_scoreboard.DEFAULT_WINDOW_DAYS

# Ticket 42: sourced from `routing_config`'s shared `DEFAULT_ROUTING_CONFIG
# .acceptance_gate` rather than a second hand-maintained literal, so this
# module's defaults and the checked-in `routing-config.json` schema's own
# `acceptance_gate` defaults (`learner_worker._load_acceptance_gate_config`'s
# fallback) can never drift apart.
DEFAULT_TRIAL_COUNT: int = routing_config.DEFAULT_ROUTING_CONFIG.acceptance_gate.trials
DEFAULT_SCORE_THRESHOLD: float = routing_config.DEFAULT_ROUTING_CONFIG.acceptance_gate.score_threshold


def _require_aware_now(now: datetime) -> None:
    """Refuse a naive `now`.

    Mirrors `learning_scoreboard.py`'s own function of the same name rather
    than importing it — a private name is never imported across these
    modules; see that module's docstring on `_require_aware_now`.
    """
    if now.tzinfo is None or now.tzinfo.utcoffset(now) is None:
        raise ValueError("now must be a timezone-aware datetime, got a naive value")


def _wire_timestamp(now: datetime) -> str:
    """Render `now` in the exact wire shape `learning_journal._utc_timestamp`
    writes. Mirrors `learning_report._utc_format`."""
    return now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _validate_trials(value: object, field_name: str = "trials") -> None:
    """A strictly positive, non-`bool` `int` — the shape of a trial count.

    Mirrors `learning_scoreboard._validate_window_days`'s shape: `bool` is an
    `int` subclass, so `isinstance` alone would admit `trials=True`. Zero or
    negative trials would evaluate a proposal on no evidence at all — the
    exact single-lucky-run failure mode this gate exists to prevent, only
    with the luck removed entirely.
    """
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(  # noqa: TRY004 - one rejection contract; see learning_journal.py
            f"{field_name} must be an integer, got {type(value).__name__}"
        )
    if value <= 0:
        raise ValueError(f"{field_name} must be > 0, got {value!r}")


def _validate_score_threshold(value: object, field_name: str = "score_threshold") -> None:
    """A finite, non-`bool` number — the shape of a bar every trial must clear."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(  # noqa: TRY004 - one rejection contract; see learning_journal.py
            f"{field_name} must be a number, got {type(value).__name__}"
        )
    if not math.isfinite(value):
        raise ValueError(f"{field_name} must be finite, got {value!r}")


def _report_journal_error_to_stderr(message: str) -> None:
    """Default journal-failure sink: one line on stderr, nothing else.

    Mirrors `production_invoker.report_journal_error_to_stderr` rather than
    importing it: that module's own `import subprocess`/`os` chain has
    nothing to do with evaluating a proposal, and this module's dependency
    graph should not grow a link to it just to reuse four lines.
    """
    print(f"⚠️  {message}", file=sys.stderr)


@dataclass(frozen=True)
class GateDecision:
    """One `evaluate_proposal` call's verdict, and the evidence behind it.

    `trial_records` is the actual `ReplayBenchmarkRecord`s this call
    journaled — not a parallel summary type, and not named `trials` like
    `evaluate_proposal`'s own count parameter, which names a different thing
    entirely — so a caller (or a test) can inspect exactly what was written
    without re-deriving it. `threshold_met` is kept as its own field rather
    than folded silently into `accepted`, so a caller asking "did the
    benchmark itself pass, independent of any scoreboard regression" has an
    answer without recomputing it from `trial_records`. `journal_complete` is
    there for the same reason and one further one: of the three causes that
    can reject a proposal it is the only one a caller can *act* on — a
    rejection carrying `threshold_met=True` and `journal_complete=False` says
    the proposal was never really judged, only that the disk refused its
    evidence, and the response is to fix the disk and re-run rather than to
    abandon the proposal. It cannot be recovered from `trial_records`, which
    look identical whether or not the append succeeded.
    """

    accepted: bool
    threshold_met: bool
    journal_complete: bool
    trial_records: tuple[learning_journal.ReplayBenchmarkRecord, ...]
    comparison: learning_scoreboard.ScoreboardComparison


def evaluate_proposal(
    runner: Callable[[], float],
    *,
    task_set: str,
    root_dir: Path,
    now: datetime,
    trials: int = DEFAULT_TRIAL_COUNT,
    score_threshold: float = DEFAULT_SCORE_THRESHOLD,
    window_days: int = DEFAULT_WINDOW_DAYS,
    run_id: str | None = None,
    report_journal_error: Callable[[str], None] = _report_journal_error_to_stderr,
) -> GateDecision:
    """Run `runner` `trials` times, journal each trial, and render a verdict.

    Reads a baseline `Scoreboard` before the first trial, journals one
    `ReplayBenchmarkRecord` per trial (a `runner()` call that raises, or
    returns a value the record itself refuses, becomes `success=False`,
    never a re-raise), reads a `current` `Scoreboard` afterward, and accepts
    only when every trial met `score_threshold`, every trial was journaled,
    and no non-benchmark scoreboard metric regressed. A benchmark-mean
    regression remains visible on `comparison` but is handled by Ticket 21's
    post-adoption anti-ratchet auto-revert (ADR 0008), not by this gate.

    `task_set` and `run_id` are validated up front, against the exact rules
    `ReplayBenchmarkRecord` itself enforces, by constructing one throwaway
    probe record (`success=False`, never journaled) before any trial runs —
    the same "fail at wiring time, not once per invocation" rule
    `production_invoker.make_journaled_invoke_worker` documents for
    `task_id`, applied here without duplicating `learning_journal`'s private
    validators across a module boundary.

    To ensure like-for-like comparison across task-set transitions without false
    regressions, `task_set` is passed to the baseline and current calls to
    `read_scoreboard`. This isolates the scoreboard comparison exclusively to
    the active benchmark version, preventing older, incomparable task set
    records in the trailing window from blending and falsely flagging a
    regression.

    A journal write that fails (a full disk, an unwritable `.ralph`) never
    raises and never silently vanishes: `append_journal_record` already
    returns rather than raises for it, and that returned message is handed to
    `report_journal_error` — one stderr line in production, a collector a
    test can inspect — exactly as `production_invoker.
    make_journaled_invoke_worker` does for the same failure. Unlike there, it
    also clears `journal_complete` and so rejects the proposal: that module
    journals telemetry *about* work whose result it returns regardless, while
    here the record is the evidence and the return value is permission to
    mutate the routing table. See this module's docstring on why a gate that
    cannot record why it opened must not open.
    """
    _require_aware_now(now)
    _validate_trials(trials, "trials")
    _validate_score_threshold(score_threshold, "score_threshold")
    timestamp = _wire_timestamp(now)
    learning_journal.ReplayBenchmarkRecord(
        task_set=task_set, success=False, run_id=run_id, timestamp=timestamp
    )

    baseline = learning_scoreboard.read_scoreboard(
        root_dir, now=now, window_days=window_days, task_set=task_set
    )

    records: list[learning_journal.ReplayBenchmarkRecord] = []
    journal_complete = True
    for _ in range(trials):
        # One `try` around both the call and the success record's
        # construction, not just the call: a runner that returns instead of
        # raising — a non-finite score, a negative one, a plain string — is
        # exactly as failed a trial as one that raised outright, and
        # `ReplayBenchmarkRecord`'s own validation is what catches it.
        # Splitting these into two `try` blocks would let a bad *return
        # value* crash the whole batch uncaught while a bad *exception*
        # failed closed — one failure mode "fails closed", the other takes
        # down every trial after it, for the same underlying fact: this
        # trial produced nothing usable.
        try:
            record = learning_journal.ReplayBenchmarkRecord(
                task_set=task_set,
                success=True,
                score=runner(),
                run_id=run_id,
                timestamp=timestamp,
            )
        except Exception:  # noqa: BLE001 - a runner's own failure mode is not this gate's business
            record = learning_journal.ReplayBenchmarkRecord(
                task_set=task_set, success=False, run_id=run_id, timestamp=timestamp
            )
        error = learning_journal.append_journal_record(record, root_dir=root_dir)
        if error is not None:
            # Every remaining trial still runs and is still attempted: one
            # unwritable record already decides the verdict, but stopping
            # here would also stop the trials that might yet land, and a
            # partially-written batch is more evidence than an abandoned one.
            journal_complete = False
            report_journal_error(error)
        records.append(record)

    current = learning_scoreboard.read_scoreboard(
        root_dir, now=now, window_days=window_days, task_set=task_set
    )
    comparison = learning_scoreboard.compare_scoreboards(baseline, current)

    threshold_met = all(
        record.success and record.score is not None and record.score >= score_threshold
        for record in records
    )
    has_non_benchmark_regression = any(
        metric_name != "mean_benchmark_score" for metric_name in comparison.regressed
    )

    return GateDecision(
        accepted=threshold_met and journal_complete and not has_non_benchmark_regression,
        threshold_met=threshold_met,
        journal_complete=journal_complete,
        trial_records=tuple(records),
        comparison=comparison,
    )


__all__ = [
    "DEFAULT_SCORE_THRESHOLD",
    "DEFAULT_TRIAL_COUNT",
    "DEFAULT_WINDOW_DAYS",
    "GateDecision",
    "evaluate_proposal",
]
