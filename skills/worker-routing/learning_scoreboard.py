#!/usr/bin/env python3
"""LearningScoreboard: a snapshot of the learning loop's eight metrics.

Named for its siblings `learning_journal.py` (the record contract and its
reader) and `learning_outcomes.py` (the hand-recorded ground truths). This
module turns a `learning_journal.JournalRead` into a `Scoreboard` — a
snapshot, at one `now`, of eight named metrics grouped into four families
(discipline, critique authenticity, efficiency, replay benchmark). See
`implementation_plan.md` (spec 0004 ticket 16) for the full design record;
this docstring states only what a caller of this module needs to know.

**Stage 2c of the ticket: the scoreboard is functionally complete.** Six of
the eight metrics are real arithmetic; `escalation_rate` and
`mean_benchmark_score` report `MetricNoData` unconditionally, each for its
own permanent reason (Section 3.2.1's missing producer; Section 3.7's
ticket-26 dependency) — every other metric is computed from `JournalRead`,
cut first to the `<= now` prefix (Section 3 rule 1) and, where its subject
calls for it, further reduced to the trailing `window_days` window.
`compute_scoreboard` is a total, pure function over any `JournalRead`,
including an empty one, built on the type contract stage 2a established
(`MetricValue`/`MetricNoData`, the four family dataclasses, the
duplicate-name guard, the no-clock and naive-`now` guards).
`compare_scoreboards` closes the loop: it pairs two boards' metrics by name
and classifies each one's movement, reading `direction` rather than a bare
sign comparison, so a rising violation rate can never read as an
improvement (Section 5).

**No metric can be misread as a genuine zero.** `MetricNoData` carries only
a `name` and a `direction` — there is no `value` attribute through which a
caller could read "no data" as zero, and no flag a caller can forget to
check. See implementation_plan.md Section 4 for the rejected alternatives
(`value: float | None`; `value: float` plus `has_data: bool`) and why each
fails the same way.

**Direction lives on the metric, not in a lookup table.** Both `MetricValue`
and `MetricNoData` carry `direction`, so a metric that exists has a
direction by construction — nothing downstream needs a name-keyed table that
could drift out of step with the metric it describes. See
implementation_plan.md Section 5.1.

**This module owns no clock.** `now` is always injected — by
`compute_scoreboard` and, through it, by `read_scoreboard` — and there is no
`datetime.now`, `datetime.utcnow`, `time.time`, or `time.gmtime` anywhere
below. A test parses this file's AST to keep it that way. A window computed
from an injected `now` is reproducible; one computed from a live clock is
not.

**The scoreboard reads. It never writes.** `read_scoreboard` calls
`learning_journal.read_journal` and nothing else journal-shaped. Ticket 26
owns writing a benchmark score, and no ticket ever writes a scoreboard back
to the journal.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal, get_args

import learning_journal

# The default trailing window, matching ticket 17's weekly report cadence.
# Configurable per call because a later consumer (ticket 18's acceptance
# gate) may want a different span; carried on `Scoreboard` itself so a
# comparison can refuse to compare boards computed with different spans.
DEFAULT_WINDOW_DAYS: int = 7

# The two ground truths that complete a task (implementation_plan.md Section
# 3.4). `plan` is excluded even on `accepted`: `advisory_consultation` writes
# that record automatically the instant a plan-producing dialogue reaches
# consensus, before implementation exists, and admitting it would mint a
# completed task at consensus and average the cost of work that never
# happened. `stalemate_resolution` is excluded too: it records how a
# deadlock was broken, not that the task ended.
_COMPLETING_GROUND_TRUTHS: frozenset[str] = frozenset({"tests", "review"})

# Which way a metric moving is good news. Carried on every `Metric` — see
# this module's docstring and implementation_plan.md Section 5.1 for why
# this is not a name-keyed lookup table instead.
MetricDirection = Literal["higher_is_better", "lower_is_better"]
_METRIC_DIRECTIONS: frozenset[str] = frozenset(get_args(MetricDirection))

# What a metric's movement between two boards means. `held` asserts a value
# stayed the same, so it is never used when either side has no value —
# that is `indeterminate` instead. See implementation_plan.md Section 5.2.
ChangeStatus = Literal["improved", "held", "regressed", "indeterminate"]
_CHANGE_STATUSES: frozenset[str] = frozenset(get_args(ChangeStatus))


def _validate_metric_direction(value: object, field_name: str = "direction") -> None:
    """Enforce the two-member direction vocabulary at runtime.

    `Literal` is erased at runtime, exactly as `learning_journal._validate_choice`'s
    docstring explains for that module's own `Literal` fields — without this
    check, a mistyped direction would construct, compare, and render as if it
    meant something.
    """
    if not isinstance(value, str) or value not in _METRIC_DIRECTIONS:
        raise ValueError(
            f"{field_name} must be one of {sorted(_METRIC_DIRECTIONS)}, got {value!r}"
        )


def _validate_metric_value(value: object, field_name: str = "value") -> None:
    """A real, finite number — never a `bool`, never `NaN`/`inf`/`-inf`.

    Mirrors `learning_journal._validate_amount`'s shape, for a sharper reason
    than that validator's own (JSON has no `NaN`): a non-finite `MetricValue`
    would silently poison a later stage's `compare_scoreboards` — every
    comparison against `NaN` is `False`, so a lower-is-better `NaN` metric
    would read as `improved`. See implementation_plan.md Section 4, objection
    8. No negative-value check: a metric may legitimately be negative in some
    future family, and inventing a floor is not this type's business.
    """
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(  # noqa: TRY004 - one rejection contract; see learning_journal.py
            f"{field_name} must be a number, got {type(value).__name__}"
        )
    if not math.isfinite(value):
        raise ValueError(f"{field_name} must be finite, got {value!r}")


def _validate_change_status(value: object, field_name: str = "status") -> None:
    """Enforce the four-member change-status vocabulary at runtime.

    Same reasoning as `_validate_metric_direction`: `Literal` is erased at
    runtime, so without this check a mistyped status would construct,
    compare, and render as if it meant something.
    """
    if not isinstance(value, str) or value not in _CHANGE_STATUSES:
        raise ValueError(
            f"{field_name} must be one of {sorted(_CHANGE_STATUSES)}, got {value!r}"
        )


def _validate_sample_size(value: object, field_name: str = "sample_size") -> None:
    """A positive, non-`bool` `int` — the shape of an evidence count.

    `learning_journal._validate_count`'s shape (`bool` is an `int` subclass,
    so `isinstance` alone would admit `True`), tightened from `>= 0` to
    `> 0`: a metric with no supporting evidence is not a `MetricValue` with a
    sample size of zero, it is `MetricNoData` — see the family builders in
    `compute_scoreboard`.
    """
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(  # noqa: TRY004 - one rejection contract; see learning_journal.py
            f"{field_name} must be an integer, got {type(value).__name__}"
        )
    if value <= 0:
        raise ValueError(f"{field_name} must be > 0, got {value!r}")


@dataclass(frozen=True)
class MetricValue:
    """One measured metric: a level, its direction, and how much evidence backs it.

    `sample_size` is not decoration — it is what makes a rate over 2
    sessions distinguishable from the same rate over 200, and it is how a
    later stage's exclusions (a task with no worker-execution record, say)
    stay visible instead of becoming a silent truncation.
    """

    name: str
    direction: MetricDirection
    value: float
    sample_size: int

    def __post_init__(self) -> None:
        _validate_metric_direction(self.direction)
        _validate_metric_value(self.value)
        _validate_sample_size(self.sample_size)


@dataclass(frozen=True)
class MetricNoData:
    """A named, directed metric with nothing measured yet.

    Deliberately carries no `value` attribute at all — there is no member
    through which a caller could read a no-data metric as zero. An
    unnarrowed `.value` access on a `Metric` is a mypy error before it is
    ever an `AttributeError` at runtime. See implementation_plan.md Section
    4.
    """

    name: str
    direction: MetricDirection

    def __post_init__(self) -> None:
        _validate_metric_direction(self.direction)


# A metric is one of exactly these two shapes — never a `value: float | None`
# and never a `value` plus a `has_data` flag a caller can forget to check.
# See implementation_plan.md Section 4 for the rejected alternatives.
Metric = MetricValue | MetricNoData


@dataclass(frozen=True)
class DisciplineMetrics:
    """Protocol violation discipline, reduced to one metric.

    One field, not two: implementation_plan.md Section 3.1 declines a
    round-1-considered breadth metric (`sessions_with_violations_rate`)
    because ticket 18's gate treats every metric as a rejection condition,
    and a second, strongly correlated discipline metric would only add a
    false-rejection risk with no acceptance criterion asking for it.
    """

    violations_per_session: Metric


@dataclass(frozen=True)
class CritiqueAuthenticityMetrics:
    """How genuinely engaged a dialogue was, and how reliably a probe was caught."""

    canary_catch_rate: Metric
    mean_engagement_count: Metric


@dataclass(frozen=True)
class EfficiencyMetrics:
    """Escalation, dialogue convergence, rework, and cost.

    Four fields, not three: `escalation_rate` and `dialogue_non_consensus_rate`
    are deliberately distinct metrics under distinct names, never one field
    wearing the other's name. See the `escalation_rate` comment inside
    `compute_scoreboard` and implementation_plan.md Section 3.2 for the two
    directions in which merging them would silently invert.
    """

    escalation_rate: Metric
    dialogue_non_consensus_rate: Metric
    mean_rework_per_task: Metric
    cost_per_completed_task_usd: Metric


@dataclass(frozen=True)
class ReplayBenchmarkMetrics:
    """The one metric ticket 26 will eventually supply."""

    mean_benchmark_score: Metric


def _require_aware_now(now: datetime) -> None:
    """Refuse a naive `now`.

    `datetime.utcnow()` and `datetime.now()` both return naive values, and
    the second is *local* time — accepting either would silently shift every
    window by the caller's UTC offset, producing a board that is wrong in a
    way nothing downstream could detect. See implementation_plan.md Section
    3.5.
    """
    if now.tzinfo is None or now.tzinfo.utcoffset(now) is None:
        raise ValueError("now must be a timezone-aware datetime, got a naive value")


def _validate_window_days(value: object, field_name: str = "window_days") -> None:
    """A strictly positive, non-`bool` `int` — the shape of a day count.

    `bool` is an `int` subclass, the same trap `learning_journal._validate_count`
    guards against, so `isinstance` alone would admit `window_days=True`. Left
    unvalidated, a negative or zero `window_days` makes the window run
    backwards or vanish — silently, since a board carries no evidence of what
    it summed, so the wrong answer looks exactly like the right one.
    """
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(  # noqa: TRY004 - one rejection contract; see learning_journal.py
            f"{field_name} must be an integer, got {type(value).__name__}"
        )
    if value <= 0:
        raise ValueError(f"{field_name} must be > 0, got {value!r}")


def _in_window(ts: datetime, *, window_start: datetime, now: datetime) -> bool:
    """Half-open on the left, closed on the right: `window_start < ts <= now`.

    So two adjacent windows partition without double-counting a boundary
    record — with second-resolution wire timestamps, a boundary tie is
    likely, not exotic. See implementation_plan.md Section 3.5.
    """
    return window_start < ts <= now


def _prefix_cut(records: tuple[Any, ...], *, now: datetime) -> tuple[Any, ...]:
    """The `<= now` prefix — Rule 1 of implementation_plan.md Section 3: no
    metric, and no history a metric consults, ever sees a record whose
    timestamp is after `now`. Applied once, to all four families, right
    after the read, so no per-metric rule can forget it and a board stays
    reproducible from a longer journal later (Round 3 objection R3-3). Every
    record in a `JournalRead` has a parseable `timestamp`
    (`learning_journal.JournalRead`'s own guarantee), so
    `parse_wire_timestamp` here never raises.
    """
    return tuple(
        record
        for record in records
        if learning_journal.parse_wire_timestamp(record.timestamp) <= now
    )


def _ratio(*, name: str, direction: MetricDirection, numerator: int, denominator: int) -> Metric:
    """A `MetricValue` when `denominator` is positive, `MetricNoData` when it
    is zero — the one place a ratio-shaped metric's no-data decision is
    made. See implementation_plan.md Section 4.
    """
    if denominator <= 0:
        return MetricNoData(name=name, direction=direction)
    return MetricValue(
        name=name,
        direction=direction,
        value=numerator / denominator,
        sample_size=denominator,
    )


def _mean(*, name: str, direction: MetricDirection, values: list[float]) -> Metric:
    """A `MetricValue` when `values` is non-empty, `MetricNoData` when it is
    not — the mean-shaped sibling of `_ratio`, and the other of the two
    construction sites Section 4 requires.
    """
    if not values:
        return MetricNoData(name=name, direction=direction)
    return MetricValue(
        name=name,
        direction=direction,
        value=sum(values) / len(values),
        sample_size=len(values),
    )


def _reduce_compliance(
    compliance: tuple[Any, ...], *, window_start: datetime, now: datetime
) -> dict[str, Any]:
    """One verdict per `session_id`, honoring `ComplianceRecord`'s own
    reduction contract (`learning_journal.py`): filter to records whose
    `session_last_activity` falls in the window — a record with none is
    skipped, never dated by its audit-time `timestamp` — then group by
    `session_id` and let the last record in file order win. `compliance` is
    already in file order (`JournalRead` never sorts), so a later dict
    assignment for one `session_id` is exactly "the later record wins."
    Same `session_id` and `run_id` (one audit written twice) needs no
    separate dedupe pass: last-wins already picks one of two identical
    records.
    """
    reduced: dict[str, Any] = {}
    for record in compliance:
        if record.session_last_activity is None:
            continue
        activity = learning_journal.parse_wire_timestamp(record.session_last_activity)
        if not _in_window(activity, window_start=window_start, now=now):
            continue
        reduced[record.session_id] = record
    return reduced


def _discipline_metrics(
    compliance: tuple[Any, ...], *, window_start: datetime, now: datetime
) -> DisciplineMetrics:
    reduced = _reduce_compliance(compliance, window_start=window_start, now=now)
    violations = [float(record.violation_count) for record in reduced.values()]
    return DisciplineMetrics(
        violations_per_session=_mean(
            name="violations_per_session", direction="lower_is_better", values=violations
        )
    )


def _windowed_dialogues(
    dialogues: tuple[Any, ...], *, window_start: datetime, now: datetime
) -> tuple[Any, ...]:
    return tuple(
        record
        for record in dialogues
        if _in_window(
            learning_journal.parse_wire_timestamp(record.timestamp),
            window_start=window_start,
            now=now,
        )
    )


def _critique_authenticity_metrics(
    dialogues: tuple[Any, ...], *, window_start: datetime, now: datetime
) -> CritiqueAuthenticityMetrics:
    """`canaries_planted == 0` is the filter for ordinary dialogue activity
    and `>= 1` marks a probe whose single round's verdict was forced by a
    fixture — `DialogueQualityRecord`'s own canary filter
    (`learning_journal.py`). Blending them is the identical silent-blend
    `AdvisoryTelemetryRecord` warns about.
    """
    windowed = _windowed_dialogues(dialogues, window_start=window_start, now=now)
    probes = [record for record in windowed if record.canaries_planted >= 1]
    ordinary = [record for record in windowed if record.canaries_planted == 0]

    catch_rate = _ratio(
        name="canary_catch_rate",
        direction="higher_is_better",
        numerator=sum(record.canaries_caught for record in probes),
        denominator=sum(record.canaries_planted for record in probes),
    )

    # A zero-round dialogue (a budget-skipped run) contributes no rounds and
    # therefore no values — never a zero engagement count for a dialogue
    # that solicited none. `unparseable` rounds are included: a malformed
    # Critic response is genuinely low engagement, and `RoundVerdict`
    # deliberately keeps it distinct from `revise`.
    engagement_values = [
        float(round_.engagement_count) for record in ordinary for round_ in record.rounds
    ]
    engagement = _mean(
        name="mean_engagement_count", direction="higher_is_better", values=engagement_values
    )

    return CritiqueAuthenticityMetrics(
        canary_catch_rate=catch_rate, mean_engagement_count=engagement
    )


def _dialogue_non_consensus_rate(
    dialogues: tuple[Any, ...], *, window_start: datetime, now: datetime
) -> Metric:
    """Windowed `dialogue_quality` records whose dialogue ended without
    consensus, over windowed records that debated at all —
    `DialogueQualityRecord`'s canary filter (`canaries_planted == 0`, the
    same contract `_critique_authenticity_metrics` honors) plus a non-empty
    `rounds`. A dialogue ended without consensus when its *last* round's
    verdict is not `"approved"` — consensus is the only thing that ends a
    dialogue at an approval, so the final round's verdict is the outcome.
    """
    windowed = _windowed_dialogues(dialogues, window_start=window_start, now=now)
    debated = [record for record in windowed if record.canaries_planted == 0 and record.rounds]
    non_consensus = [record for record in debated if record.rounds[-1].verdict != "approved"]
    return _ratio(
        name="dialogue_non_consensus_rate",
        direction="lower_is_better",
        numerator=len(non_consensus),
        denominator=len(debated),
    )


def _first_ever_run_id(runs: dict[str, datetime]) -> str:
    """The run with the earliest start; ties broken by `run_id` for
    determinism when two runs share a start (second-resolution wire
    timestamps can tie).
    """
    return min(runs.items(), key=lambda item: (item[1], item[0]))[0]


def _task_run_starts(worker_executions: tuple[Any, ...]) -> dict[str, dict[str, datetime]]:
    """`task_id -> {run_id: earliest timestamp among that run's executions}`.

    A record with no `run_id` contributes to no task's runs at all —
    `_validate_run_id`'s "uncountable rather than... one shared run"
    (`learning_journal.py`): lumping run-less executions of one task
    together would report a task retried five times as retried once. A task
    all of whose executions omit `run_id` therefore never appears in the
    returned mapping, which is what excludes it from
    `_mean_rework_per_task` rather than scoring it a confident rework 0
    (implementation_plan.md Section 3.4).
    """
    runs_by_task: dict[str, dict[str, datetime]] = {}
    for record in worker_executions:
        if record.run_id is None:
            continue
        ts = learning_journal.parse_wire_timestamp(record.timestamp)
        task_runs = runs_by_task.setdefault(record.task.task_id, {})
        if record.run_id not in task_runs or ts < task_runs[record.run_id]:
            task_runs[record.run_id] = ts
    return runs_by_task


def _mean_rework_per_task(
    worker_executions: tuple[Any, ...], *, window_start: datetime, now: datetime
) -> Metric:
    """`WorkerExecutionRecord`'s rework definition — "rework on a task =
    distinct run_ids carrying that task_id, minus one" — restated as a
    windowed count: a run counts toward rework when its start falls **in
    the window** and it is **not the task's first-ever run**, with
    "first-ever" decided against the task's whole `<= now` history (not the
    window), so a repeat is counted whether or not its predecessor happens
    to be visible in the same window (implementation_plan.md Section 3.4,
    Round 3 objection 2). Numerator and denominator are keyed on the same
    event — a run *starting* in the window — so a task enters the mean's
    population exactly when it has a run (any run, first-ever or not)
    starting there.
    """
    task_runs = _task_run_starts(worker_executions)
    values: list[float] = []
    for runs in task_runs.values():
        if not runs:
            continue
        first_ever_run_id = _first_ever_run_id(runs)
        started_in_window = False
        rework_count = 0
        for run_id, start in runs.items():
            if not _in_window(start, window_start=window_start, now=now):
                continue
            started_in_window = True
            if run_id != first_ever_run_id:
                rework_count += 1
        if started_in_window:
            values.append(float(rework_count))
    return _mean(name="mean_rework_per_task", direction="lower_is_better", values=values)


def _completed_task_ids(
    outcomes: tuple[Any, ...], *, window_start: datetime, now: datetime
) -> frozenset[str]:
    """Task ids with a windowed outcome whose `ground_truth` is `tests` or
    `review` — see `_COMPLETING_GROUND_TRUTHS` for why those two and not the
    other two.

    No reduction runs over `outcomes` here — an outcome's `run_id` narrows
    what it grades, and the `plan` family has two producers under one
    `task_id`; any metric that reads *verdicts* rather than *membership*
    must settle both before it reads one (`OutcomeRecord.run_id`'s
    docstring, `learning_journal.py`). This function only asks membership,
    which no ordering can change, so it settles nothing that question owns.
    """
    completed: set[str] = set()
    for outcome in outcomes:
        if outcome.ground_truth not in _COMPLETING_GROUND_TRUTHS:
            continue
        ts = learning_journal.parse_wire_timestamp(outcome.timestamp)
        if not _in_window(ts, window_start=window_start, now=now):
            continue
        completed.add(outcome.task.task_id)
    return frozenset(completed)


def _cost_per_completed_task_usd(
    worker_executions: tuple[Any, ...], completed_task_ids: frozenset[str]
) -> Metric:
    """A completed task's cost = sum of `cost_estimate_usd` over *every* one
    of its worker executions, across every run — `WorkerExecutionRecord`'s
    own "cost per completed task = sum over all of them" line
    (`learning_journal.py`) — summed over the whole `<= now` prefix.
    `worker_executions` here is already prefix-cut; the window only decided
    which tasks are in `completed_task_ids`, never which executions of a
    selected task are summed. A completed task with no worker-execution
    record at all is excluded, not costed at zero: `production_invoker`
    journals every invocation, so an unjournaled one is a bug, and costing
    it as free would let that bug read as a cost improvement.
    """
    totals: dict[str, float] = {}
    for record in worker_executions:
        task_id = record.task.task_id
        if task_id not in completed_task_ids:
            continue
        totals[task_id] = totals.get(task_id, 0.0) + record.cost_estimate_usd
    return _mean(
        name="cost_per_completed_task_usd",
        direction="lower_is_better",
        values=list(totals.values()),
    )


def _efficiency_metrics(
    worker_executions: tuple[Any, ...],
    outcomes: tuple[Any, ...],
    dialogues: tuple[Any, ...],
    *,
    window_start: datetime,
    now: datetime,
) -> EfficiencyMetrics:
    completed_task_ids = _completed_task_ids(outcomes, window_start=window_start, now=now)
    return EfficiencyMetrics(
        # No journal writer exists for a 2-failure routing escalation
        # (`agent_council.ESCALATION_FAILURE_THRESHOLD`) — agent_council
        # journals nothing at all — so this metric has no source until one
        # exists. Do not substitute `dialogue_non_consensus_rate`: the two
        # directions in which that substitution silently inverts are in
        # implementation_plan.md Section 3.2 (a one-round `revise` under
        # `max_rounds=1` is non-consensus without being a 2-failure
        # escalation; a post-mortem after a real escalation can reach
        # consensus).
        escalation_rate=MetricNoData(name="escalation_rate", direction="lower_is_better"),
        dialogue_non_consensus_rate=_dialogue_non_consensus_rate(
            dialogues, window_start=window_start, now=now
        ),
        mean_rework_per_task=_mean_rework_per_task(
            worker_executions, window_start=window_start, now=now
        ),
        cost_per_completed_task_usd=_cost_per_completed_task_usd(
            worker_executions, completed_task_ids
        ),
    )


@dataclass(frozen=True)
class Scoreboard:
    """A snapshot, at `window_end`, of every metric this ticket defines.

    Six of the eight metrics are real arithmetic as of stage 2b;
    `escalation_rate` and `mean_benchmark_score` are `MetricNoData`
    unconditionally and permanently (Sections 3.2.1, 3.7). The two skip
    counters are passed straight through from `learning_journal.JournalRead`,
    unchanged — see implementation_plan.md Section 2.4.
    """

    discipline: DisciplineMetrics
    critique_authenticity: CritiqueAuthenticityMetrics
    efficiency: EfficiencyMetrics
    replay_benchmark: ReplayBenchmarkMetrics
    window_days: int
    window_end: datetime
    unreadable_lines: int
    unknown_kind_lines: int

    def __post_init__(self) -> None:
        seen: set[str] = set()
        for metric in self.metrics:
            if metric.name in seen:
                raise ValueError(
                    f"duplicate metric name {metric.name!r} on one Scoreboard — "
                    "a later stage's compare_scoreboards pairs metrics by name, "
                    "and a duplicate would make that pairing silently arbitrary"
                )
            seen.add(metric.name)

    @property
    def metrics(self) -> tuple[Metric, ...]:
        """Every metric, in a fixed order: family order, then field order."""
        return (
            self.discipline.violations_per_session,
            self.critique_authenticity.canary_catch_rate,
            self.critique_authenticity.mean_engagement_count,
            self.efficiency.escalation_rate,
            self.efficiency.dialogue_non_consensus_rate,
            self.efficiency.mean_rework_per_task,
            self.efficiency.cost_per_completed_task_usd,
            self.replay_benchmark.mean_benchmark_score,
        )


@dataclass(frozen=True)
class MetricChange:
    """One metric's movement between two boards — direction-aware, never a
    delta.

    Carries no `delta` field: a renderer wanting a numeric delta narrows
    both `baseline` and `current` to `MetricValue` itself, reusing Section
    4's no-data lock rather than this type reintroducing the same `x or
    0.0` ambiguity a `delta: float | None` field would (implementation_plan.md
    Section 5.2).
    """

    name: str
    direction: MetricDirection
    status: ChangeStatus
    baseline: Metric
    current: Metric

    def __post_init__(self) -> None:
        _validate_metric_direction(self.direction)
        _validate_change_status(self.status)


@dataclass(frozen=True)
class ScoreboardComparison:
    """Every metric's movement between two boards, in `Scoreboard.metrics`'s
    own family-then-field order (implementation_plan.md Section 5.2, 5.3).
    """

    changes: tuple[MetricChange, ...]

    @property
    def improved(self) -> tuple[str, ...]:
        return tuple(change.name for change in self.changes if change.status == "improved")

    @property
    def held(self) -> tuple[str, ...]:
        return tuple(change.name for change in self.changes if change.status == "held")

    @property
    def regressed(self) -> tuple[str, ...]:
        return tuple(change.name for change in self.changes if change.status == "regressed")

    @property
    def indeterminate(self) -> tuple[str, ...]:
        return tuple(
            change.name for change in self.changes if change.status == "indeterminate"
        )

    @property
    def has_regression(self) -> bool:
        """The single boolean ticket 18's gate needs: "no scoreboard metric
        regresses" — computed here rather than re-derived by that ticket.
        """
        return len(self.regressed) > 0


def compute_scoreboard(
    journal: learning_journal.JournalRead,
    *,
    now: datetime,
    window_days: int = DEFAULT_WINDOW_DAYS,
) -> Scoreboard:
    """Compute a `Scoreboard` from an already-read journal. Pure; reads no clock.

    Every family is first cut to the `<= now` prefix (implementation_plan.md
    Section 3, rule 1) — no metric, and no history a metric consults, ever
    sees a record stamped after `now`. Six of the eight metrics are then
    real arithmetic over that prefix, each reduced or windowed per Sections
    3.1-3.6. Two stay `MetricNoData` unconditionally, for their own
    permanent reasons: `escalation_rate` because the 2-failure routing
    escalation it names has no journal writer at all (`agent_council.py`
    calls `append_journal_record` zero times — Section 3.2.1);
    `mean_benchmark_score` because no record family carries a benchmark
    score until ticket 26 (Section 3.7).
    """
    _require_aware_now(now)
    _validate_window_days(window_days)

    worker_executions = _prefix_cut(journal.worker_executions, now=now)
    outcomes = _prefix_cut(journal.outcomes, now=now)
    dialogues = _prefix_cut(journal.dialogues, now=now)
    compliance = _prefix_cut(journal.compliance, now=now)

    window_start = now - timedelta(days=window_days)

    return Scoreboard(
        discipline=_discipline_metrics(compliance, window_start=window_start, now=now),
        critique_authenticity=_critique_authenticity_metrics(
            dialogues, window_start=window_start, now=now
        ),
        efficiency=_efficiency_metrics(
            worker_executions, outcomes, dialogues, window_start=window_start, now=now
        ),
        replay_benchmark=ReplayBenchmarkMetrics(
            # No record family carries a benchmark score until ticket 26.
            mean_benchmark_score=MetricNoData(
                name="mean_benchmark_score", direction="higher_is_better"
            ),
        ),
        window_days=window_days,
        window_end=now,
        unreadable_lines=journal.unreadable_lines,
        unknown_kind_lines=journal.unknown_kind_lines,
    )


def read_scoreboard(
    root_dir: Path, *, now: datetime, window_days: int = DEFAULT_WINDOW_DAYS
) -> Scoreboard:
    """Read the journal beneath `root_dir` and compute a `Scoreboard` from it.

    The three-line convenience door; `compute_scoreboard` is the contract.
    Kept rather than deleted because tickets 17, 18, and 22 would otherwise
    each write these same three lines and import `learning_journal` for
    nothing — implementation_plan.md Section 2.2's "why two entry points".
    """
    journal = learning_journal.read_journal(root_dir)
    return compute_scoreboard(journal, now=now, window_days=window_days)


def _classify_change(baseline: Metric, current: Metric) -> ChangeStatus:
    """`improved` iff `(delta > 0) == (direction == "higher_is_better")` —
    never a bare `>` or `<` alone deciding the status. A bare `>` gets
    `higher_is_better` right and inverts every `lower_is_better` metric, so a
    rising violation rate would read as an improvement — the exact failure
    this ticket exists to make impossible (implementation_plan.md Section
    5.2).

    Either side (or both) being `MetricNoData` is `indeterminate`, not
    `held`: `held` asserts a value stayed the same, and there is no value to
    compare. A family that had no data last week and has data this week
    changed its *coverage*, not its performance — reading the arrival of the
    first canary probe as an improvement, or its disappearance as a
    regression, would be exactly the invented verdict the ticket forbids.
    """
    if isinstance(baseline, MetricNoData) or isinstance(current, MetricNoData):
        return "indeterminate"
    if current.value == baseline.value:
        return "held"
    moved_up = current.value > baseline.value
    improved = moved_up == (baseline.direction == "higher_is_better")
    return "improved" if improved else "regressed"


def compare_scoreboards(baseline: Scoreboard, current: Scoreboard) -> ScoreboardComparison:
    """Pair `baseline`'s and `current`'s metrics by name and classify each
    movement per `_classify_change`. Clock-free: it takes no `now`, it
    compares two already-computed boards.

    Two guards, both `ValueError`, matching `learning_outcomes.
    record_stalemate_resolution`'s refusal to silently map by `id` alone
    (implementation_plan.md Section 5.3):

    - the two boards' `window_days` differ — a 7-day rate and a 30-day rate
      are not comparable;
    - the two boards' metric name sets differ, or the same name carries a
      different `direction` on each side — meaning the two boards came from
      different code versions, and silently skipping the unmatched metric is
      how a regression hides.
    """
    if baseline.window_days != current.window_days:
        raise ValueError(
            "cannot compare scoreboards computed with different window_days: "
            f"baseline={baseline.window_days!r}, current={current.window_days!r}"
        )

    baseline_by_name = {metric.name: metric for metric in baseline.metrics}
    current_by_name = {metric.name: metric for metric in current.metrics}

    if baseline_by_name.keys() != current_by_name.keys():
        raise ValueError(
            "cannot compare scoreboards with different metric name sets: "
            f"baseline only={sorted(baseline_by_name.keys() - current_by_name.keys())}, "
            f"current only={sorted(current_by_name.keys() - baseline_by_name.keys())}"
        )

    mismatched_directions = sorted(
        name
        for name, baseline_metric in baseline_by_name.items()
        if baseline_metric.direction != current_by_name[name].direction
    )
    if mismatched_directions:
        raise ValueError(
            "cannot compare scoreboards where a metric's direction differs between "
            f"boards: {mismatched_directions}"
        )

    changes = tuple(
        MetricChange(
            name=name,
            direction=baseline_metric.direction,
            status=_classify_change(baseline_metric, current_by_name[name]),
            baseline=baseline_metric,
            current=current_by_name[name],
        )
        for name, baseline_metric in baseline_by_name.items()
    )
    return ScoreboardComparison(changes=changes)
