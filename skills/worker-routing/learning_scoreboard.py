#!/usr/bin/env python3
"""LearningScoreboard: a snapshot of the learning loop's eight metrics.

Named for its siblings `learning_journal.py` (the record contract and its
reader) and `learning_outcomes.py` (the hand-recorded ground truths). This
module turns a `learning_journal.JournalRead` into a `Scoreboard` — a
snapshot, at one `now`, of eight named metrics grouped into four families
(discipline, critique authenticity, efficiency, replay benchmark). See
`implementation_plan.md` (spec 0004 ticket 16) for the full design record;
this docstring states only what a caller of this module needs to know.

**Stage 2a of the ticket: the all-no-data skeleton.** Every metric this
module constructs today reports `MetricNoData` unconditionally — no metric
arithmetic lives here yet. That is not a stub standing in for the real
thing: `compute_scoreboard` is already a total, pure function over any
`JournalRead`, including an empty one, and the type contract this stage
establishes (`MetricValue`/`MetricNoData`, the four family dataclasses, the
duplicate-name guard, the no-clock and naive-`now` guards) is exactly the
contract later stages compute against. Filling in one metric's arithmetic in
a later stage will not need to touch a type declared here.

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
from datetime import datetime
from pathlib import Path
from typing import Literal, get_args

import learning_journal

# The default trailing window, matching ticket 17's weekly report cadence.
# Configurable per call because a later consumer (ticket 18's acceptance
# gate) may want a different span; carried on `Scoreboard` itself so a
# comparison can refuse to compare boards computed with different spans.
DEFAULT_WINDOW_DAYS: int = 7

# Which way a metric moving is good news. Carried on every `Metric` — see
# this module's docstring and implementation_plan.md Section 5.1 for why
# this is not a name-keyed lookup table instead.
MetricDirection = Literal["higher_is_better", "lower_is_better"]
_METRIC_DIRECTIONS: frozenset[str] = frozenset(get_args(MetricDirection))


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


@dataclass(frozen=True)
class Scoreboard:
    """A snapshot, at `window_end`, of every metric this ticket defines.

    **Stage 2a: every metric is `MetricNoData`.** The four family fields and
    the two skip counters (passed straight through from
    `learning_journal.JournalRead`, unchanged — see implementation_plan.md
    Section 2.4) are the whole of this stage's contract; a later stage fills
    in the arithmetic behind `metrics` without touching this type.
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


def compute_scoreboard(
    journal: learning_journal.JournalRead,
    *,
    now: datetime,
    window_days: int = DEFAULT_WINDOW_DAYS,
) -> Scoreboard:
    """Compute a `Scoreboard` from an already-read journal. Pure; reads no clock.

    **Stage 2a: every metric is `MetricNoData`, unconditionally, whatever
    `journal` holds.** The metric arithmetic (implementation_plan.md
    Sections 3.1-3.7) lands in later stages of this ticket; this stage makes
    the type contract and the empty/no-data path real before any of it is
    built, so that a later stage's arithmetic is added to an already-total
    function rather than growing one.

    Two of the eight metrics stay `MetricNoData` even once later stages
    compute the other six, and for their own, permanent reasons rather than
    this stage's temporary one: `escalation_rate` because the 2-failure
    routing escalation it names has no journal writer at all
    (`agent_council.py` calls `append_journal_record` zero times — Section
    3.2.1); `mean_benchmark_score` because no record family carries a
    benchmark score until ticket 26 (Section 3.7).
    """
    _require_aware_now(now)
    return Scoreboard(
        discipline=DisciplineMetrics(
            violations_per_session=MetricNoData(
                name="violations_per_session", direction="lower_is_better"
            ),
        ),
        critique_authenticity=CritiqueAuthenticityMetrics(
            canary_catch_rate=MetricNoData(
                name="canary_catch_rate", direction="higher_is_better"
            ),
            mean_engagement_count=MetricNoData(
                name="mean_engagement_count", direction="higher_is_better"
            ),
        ),
        efficiency=EfficiencyMetrics(
            # No journal writer exists for a 2-failure routing escalation
            # (`agent_council.ESCALATION_FAILURE_THRESHOLD`) — agent_council
            # journals nothing at all — so this metric has no source until
            # one exists. Do not substitute `dialogue_non_consensus_rate`:
            # implementation_plan.md Section 3.2 states the two directions in
            # which that substitution silently inverts (a one-round `revise`
            # under `max_rounds=1` is non-consensus without being a
            # 2-failure escalation; a post-mortem after a real escalation can
            # reach consensus).
            escalation_rate=MetricNoData(
                name="escalation_rate", direction="lower_is_better"
            ),
            dialogue_non_consensus_rate=MetricNoData(
                name="dialogue_non_consensus_rate", direction="lower_is_better"
            ),
            mean_rework_per_task=MetricNoData(
                name="mean_rework_per_task", direction="lower_is_better"
            ),
            cost_per_completed_task_usd=MetricNoData(
                name="cost_per_completed_task_usd", direction="lower_is_better"
            ),
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
