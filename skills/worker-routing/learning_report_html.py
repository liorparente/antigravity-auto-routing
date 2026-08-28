#!/usr/bin/env python3
"""LearningReportHtml: a standalone HTML dashboard over the learning loop.

Named for its siblings `learning_journal.py` (the record contract and its
reader), `learning_scoreboard.py` (the eight-metric snapshot), and
`learning_report.py` (the weekly Markdown record this module sits beside —
never replaces). Where `learning_report.py` renders one week's numbers for a
human reading top to bottom, this module renders the same numbers — plus a
handful of derived ones a Markdown table cannot show well — as a single,
self-contained, double-clickable `.html` file: KPI cards, a per-model-family
table, and the week's compliance and degradation events.

**A renderer, not a computer, same as `learning_report.py`.** Every one of
the eight canonical metrics comes from `learning_scoreboard.compute_scoreboard`
and `compare_scoreboards`; this module never re-derives one of those eight or
their improved/held/regressed classification. It does compute four *new*
metrics `learning_scoreboard.Scoreboard` does not carry
(`first_pass_yield`, `total_cost_usd`, `cost_savings_usd`, and the
permanently-absent `token_savings`) — see `_derived_metric_changes` — because
the ticket asking for this dashboard asks for numbers the scoreboard does not
compute. Duplicating small pieces of `learning_scoreboard.py`'s private
windowing logic to get there (`_in_window`, `_windowed`, the rework-per-task
reconstruction) is deliberate, not an oversight: mirrors
`learning_report.py`'s own `_in_window`/`_windowed` duplication, for the same
reason stated there — importing a private name across modules is the one
pattern this codebase never uses.

**`board` and `baseline_board` are parameters, not something this module
computes.** `render_html_report` takes both already built, exactly the way
`render_weekly_report` builds them internally from one journal — the caller
(today, `write_html_report` below; conceivably a future caller sharing one
journal read across the Markdown and HTML renderers) owns computing them
once. This keeps the pure contract door clock-free and disk-free like every
other one in this family, and keeps a `journal`/`board`/`baseline_board`
mismatch (different `window_days`, a `baseline_board` not aligned one window
behind `now`) a loud `ValueError` here rather than a silently wrong table
somewhere downstream.

**No token metric is fabricated.** `WorkerExecutionRecord` carries a cost
estimate and a duration, never a token count — no producer in this codebase
journals one. `token_savings` therefore renders as `MetricNoData`
unconditionally and permanently, exactly the shape
`learning_scoreboard.EfficiencyMetrics.escalation_rate` already uses for the
same reason: the honest answer to "how many tokens were saved" is "we do not
have that number", not a guess dressed as one.

**"Savings vs baseline" is a measured comparison, never a stipulated Tier-3
rate.** The research spec's example — a hypothetical single-model-tier cost
— has no producer in this codebase either: no config anywhere fixes a Tier-3
USD-per-task rate. `cost_savings_usd` instead asks a question every number
behind it can answer honestly: "if this window's completed tasks had cost
what a completed task cost in the *previous* window, what would the total
have been, versus what it actually was?" That is `baseline_board`'s own
`cost_per_completed_task_usd` (real, measured) times the current window's
completed-task count, minus the current window's actual total — the same
current-vs-baseline comparison every other metric in this report already
makes, applied to a total instead of a mean.

**This module owns no clock.** `now` is always injected, and there is no
`datetime.now`, `datetime.utcnow`, `time.time`, or `time.gmtime` anywhere
below — matching every sibling in this family, enforced by the same AST
guard test.

**Every dynamic string is escaped, even though none can carry HTML
metacharacters today.** Every string field this module renders already
passed `learning_journal`'s own validators — `TASK_ID_RE`, `ISSUE_CODE_RE`,
or a closed `Literal` vocabulary — none of which admit `<`, `>`, `&`, or
`"`. `_escape` runs anyway, on the same "never trust a string at the render
boundary" principle the rest of this codebase applies at its own
untrusted-input boundaries: a future field or a future caller must not be
able to reach this template with an unescaped value.

**The Role & Model Configuration Matrix (Spec 0013, tickets 47–49) is a
second tab, not a second document.** `render_html_report` grows two
optional parameters — `role_matrix`
(`routing_config.get_role_matrix_view_data`'s output) and
`model_capabilities` (`routing_config.build_model_capabilities_registry`'s)
— and renders them as a Bento Grid of role cards behind a second tab
alongside the existing metrics tab. Passing either as `None` (the default)
renders an empty grid rather than reading `routing-config.json` itself:
`render_html_report` stays clock-free and disk-free exactly as documented
above, and `write_html_report` is the caller that loads both, mirroring how
it already owns computing `board`/`baseline_board` from the journal it
reads.

**Ticket 49's dirty-state pill has no server-side half.** Unlike the role
matrix itself, `currentRoles`/`savedSnapshot`/`undoHistory` and the
floating action pill they drive exist only in `_SCRIPT` and
`_ACTION_PILL_HTML` — there is no Python-side render of "what is currently
dirty" the way `_resolve_effort_state` mirrors `resolveEffortState`,
because dirtiness is inherently a property of in-page edits a
scripting-disabled load can never produce. `_ACTION_PILL_HTML` is static
markup with no dynamic value, so it needs no `_escape` call anywhere it
appears.

**The document ships exactly two script tags, and only one of them is
executable.** Ticket 47's tab bar and "primary roles / all roles" toggle
are still pure CSS (checked-radio sibling selectors), but ticket 48's
reactive model/effort binding genuinely needs JavaScript, so the standing
"no inline script" invariant that held through ticket 47 is now retired.
What replaces it is narrower and stronger, pinned by `ScriptInjectionTests`:
the only tags are one `application/json` island carrying every dynamic
value *the script reads*, and one executable block that is a *static
literal* — no dynamic value is ever interpolated into source the browser
compiles, so the escaping burden collapses to the JSON island alone, where
three characters (`<`, `>`, `&`) are `\\uXXXX`-escaped so nothing inside
can close the block. (Everything else dynamic on the page is ordinary
server-rendered markup, escaped by `_escape` as it always was.)

**The auto-snap rule is written twice, and pinned to stay one rule.**
`_resolve_effort_state` (Python, for the initial server-side render, so a
document opened with scripting disabled still never shows an effort its
model rejects) and `resolveEffortState` (JavaScript, for the reactive case)
are the same Spec 0013 §3 decision in two languages. `JsEffortSnapParityTests`
runs one shared table of cases through both and asserts they agree, and the
JavaScript's behavior is tested by executing it under node against a stubbed
DOM built from this module's own rendered output — never by asserting over
the script's source text.
"""
from __future__ import annotations

import html
import json
import math
import os
import tempfile
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, NamedTuple

if __package__:
    from . import learning_journal, learning_report, learning_scoreboard, routing_config
else:
    import learning_journal  # type: ignore[no-redef]
    import learning_report  # type: ignore[no-redef]
    import learning_scoreboard  # type: ignore[no-redef]
    import routing_config  # type: ignore[no-redef]

# Re-exported from `learning_scoreboard` — one source, never a second
# literal, matching `learning_report.DEFAULT_WINDOW_DAYS`'s own re-export.
DEFAULT_WINDOW_DAYS: int = learning_scoreboard.DEFAULT_WINDOW_DAYS

# The HTML report lives beside the Markdown one, in the same directory —
# `learning_report.REPORTS_RELATIVE_DIR` is a genuine public constant (no
# leading underscore), so re-reading it here is not the private-import this
# module's docstring otherwise forbids.
REPORTS_RELATIVE_DIR = learning_report.REPORTS_RELATIVE_DIR

_DIRECTION_WORDS: dict[str, str] = {
    "lower_is_better": "lower is better",
    "higher_is_better": "higher is better",
}

_STATUS_LABELS: dict[str, str] = {
    "improved": "Improved",
    "held": "Held",
    "regressed": "Regressed",
    "indeterminate": "No trend",
}

_KPI_DISPLAY_NAMES: dict[str, str] = {
    "violations_per_session": "Violations / Session",
    "canary_catch_rate": "Canary Catch Rate",
    "mean_engagement_count": "Mean Engagement",
    "escalation_rate": "Escalation Rate",
    "dialogue_non_consensus_rate": "Dialogue Non-Consensus Rate",
    "mean_rework_per_task": "Mean Rework / Task",
    "cost_per_completed_task_usd": "Cost / Completed Task (USD)",
    "mean_benchmark_score": "Mean Benchmark Score",
    "first_pass_yield": "First-Pass Yield (FPY)",
    "total_cost_usd": "Total Windowed Cost (USD)",
    "cost_savings_usd": "Cost Savings vs Baseline (USD)",
    "token_savings": "Token Savings",
}


def _require_aware_now(now: datetime) -> None:
    """Refuse a naive `now`. Mirrors `learning_report._require_aware_now`;
    duplicated rather than imported for the reason stated there and in this
    module's own docstring.
    """
    if now.tzinfo is None or now.tzinfo.utcoffset(now) is None:
        raise ValueError("now must be a timezone-aware datetime, got a naive value")


def _validate_window_days(value: object, field_name: str = "window_days") -> None:
    """A strictly positive, non-`bool` `int`. Mirrors
    `learning_report._validate_window_days`; duplicated for the same reason.
    """
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(  # noqa: TRY004 - one rejection contract; see learning_journal.py
            f"{field_name} must be an integer, got {type(value).__name__}"
        )
    if value <= 0:
        raise ValueError(f"{field_name} must be > 0, got {value!r}")


def _utc_format(value: datetime) -> str:
    """Render an aware `datetime` in the exact wire shape
    `learning_journal._utc_timestamp` writes. Mirrors `learning_report._utc_format`.
    """
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _in_window(ts: datetime, *, window_start: datetime, now: datetime) -> bool:
    """Half-open on the left, closed on the right — the same convention
    `learning_scoreboard._in_window` and `learning_report._in_window` use.
    """
    return window_start < ts <= now


def _windowed(records: tuple[Any, ...], *, window_start: datetime, now: datetime) -> tuple[Any, ...]:
    """Every record whose `timestamp` falls inside the trailing window.
    Mirrors `learning_scoreboard._windowed`.
    """
    return tuple(
        record
        for record in records
        if _in_window(
            learning_journal.parse_wire_timestamp(record.timestamp),
            window_start=window_start,
            now=now,
        )
    )


def _prefix_cut(records: tuple[Any, ...], *, now: datetime) -> tuple[Any, ...]:
    """The `<= now` prefix. Mirrors `learning_scoreboard._prefix_cut`."""
    return tuple(
        record
        for record in records
        if learning_journal.parse_wire_timestamp(record.timestamp) <= now
    )


def _format_value(value: float) -> str:
    """The one place a number becomes a string in this module — matches
    `learning_report._format_value`'s precision exactly, so the same metric
    never reads differently between the Markdown report and this one.
    """
    return f"{value:.4g}"


def _metric_value_repr(metric: Any) -> str:
    if isinstance(metric, learning_scoreboard.MetricNoData):
        return "no data"
    return f"{_format_value(metric.value)} (n={metric.sample_size})"


def _escape(value: object) -> str:
    """Escape any dynamic value before it reaches the HTML template. See this
    module's docstring for why this runs unconditionally on every value.
    """
    return html.escape(str(value), quote=True)


def _classify(baseline: Any, current: Any) -> learning_scoreboard.ChangeStatus:
    """Mirrors `learning_scoreboard._classify_change`'s status logic, for the
    derived metrics this module computes and that Scoreboard does not carry.
    Duplicated rather than imported — that function is private.
    """
    if isinstance(baseline, learning_scoreboard.MetricNoData) or isinstance(
        current, learning_scoreboard.MetricNoData
    ):
        return "indeterminate"
    if math.isnan(baseline.value) or math.isnan(current.value):
        return "indeterminate"
    if current.value == baseline.value:
        return "held"
    moved_up = current.value > baseline.value
    improved = moved_up == (baseline.direction == "higher_is_better")
    return "improved" if improved else "regressed"


# --- derived metric: first_pass_yield ---


def _first_ever_run_id(runs: dict[str, datetime]) -> str:
    """Mirrors `learning_scoreboard._first_ever_run_id`."""
    return min(runs.items(), key=lambda item: (item[1], item[0]))[0]


def _task_run_starts(worker_executions: tuple[Any, ...]) -> dict[str, dict[str, datetime]]:
    """`task_id -> {run_id: earliest timestamp among that run's executions}`.
    Mirrors `learning_scoreboard._task_run_starts` — a record with no
    `run_id` contributes to no task's runs at all, for the same reason
    documented there.
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


def _windowed_rework_counts(
    worker_executions: tuple[Any, ...], *, window_start: datetime, now: datetime
) -> list[int]:
    """Per-task rework count for every task with a run starting in the
    window — the same population `learning_scoreboard._mean_rework_per_task`
    reduces to a mean, exposed here as the raw per-task list so
    `_first_pass_yield_metric` can count the zeros directly.
    """
    task_runs = _task_run_starts(worker_executions)
    counts: list[int] = []
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
            counts.append(rework_count)
    return counts


def _first_pass_yield_metric(
    worker_executions: tuple[Any, ...], *, window_start: datetime, now: datetime
) -> Any:
    """The fraction of windowed tasks whose rework count is zero — `1.0`
    means every task in the window shipped on its first run.
    """
    counts = _windowed_rework_counts(worker_executions, window_start=window_start, now=now)
    if not counts:
        return learning_scoreboard.MetricNoData(
            name="first_pass_yield", direction="higher_is_better"
        )
    zero_rework = sum(1 for count in counts if count == 0)
    return learning_scoreboard.MetricValue(
        name="first_pass_yield",
        direction="higher_is_better",
        value=zero_rework / len(counts),
        sample_size=len(counts),
    )


# --- derived metric: total_cost_usd ---


def _total_cost_metric(
    worker_executions: tuple[Any, ...], *, window_start: datetime, now: datetime
) -> Any:
    """The sum of `cost_estimate_usd` over every windowed execution,
    completed or not — unlike `cost_per_completed_task_usd`, this counts
    every dollar spent in the window, including on tasks still in flight.
    """
    windowed = _windowed(worker_executions, window_start=window_start, now=now)
    if not windowed:
        return learning_scoreboard.MetricNoData(name="total_cost_usd", direction="lower_is_better")
    total = sum(record.cost_estimate_usd for record in windowed)
    return learning_scoreboard.MetricValue(
        name="total_cost_usd",
        direction="lower_is_better",
        value=total,
        sample_size=len(windowed),
    )


# --- derived metric: cost_savings_usd ---


def _cost_savings_metric(
    board: learning_scoreboard.Scoreboard, baseline_board: learning_scoreboard.Scoreboard
) -> Any:
    """See this module's docstring: a measured current-vs-baseline
    comparison, never a stipulated external rate. `MetricNoData` unless both
    boards have a real `cost_per_completed_task_usd` — a completed task in
    one window with none in the other cannot honestly be compared.
    """
    current_cost = board.efficiency.cost_per_completed_task_usd
    baseline_cost = baseline_board.efficiency.cost_per_completed_task_usd
    if isinstance(current_cost, learning_scoreboard.MetricNoData) or isinstance(
        baseline_cost, learning_scoreboard.MetricNoData
    ):
        return learning_scoreboard.MetricNoData(
            name="cost_savings_usd", direction="higher_is_better"
        )
    hypothetical_total = baseline_cost.value * current_cost.sample_size
    actual_total = current_cost.value * current_cost.sample_size
    return learning_scoreboard.MetricValue(
        name="cost_savings_usd",
        direction="higher_is_better",
        value=hypothetical_total - actual_total,
        sample_size=current_cost.sample_size,
    )


def _derived_metric_changes(
    journal: learning_journal.JournalRead,
    board: learning_scoreboard.Scoreboard,
    baseline_board: learning_scoreboard.Scoreboard,
    *,
    window_start: datetime,
    baseline_window_start: datetime,
    now: datetime,
) -> tuple[learning_scoreboard.MetricChange, ...]:
    """The four metrics this dashboard shows that `Scoreboard` does not
    carry: `first_pass_yield`, `total_cost_usd`, `cost_savings_usd`, and the
    permanently-absent `token_savings` (see this module's docstring).
    """
    current_executions = _prefix_cut(journal.worker_executions, now=now)
    baseline_executions = _prefix_cut(journal.worker_executions, now=window_start)

    current_fpy = _first_pass_yield_metric(
        current_executions, window_start=window_start, now=now
    )
    baseline_fpy = _first_pass_yield_metric(
        baseline_executions, window_start=baseline_window_start, now=window_start
    )

    current_total_cost = _total_cost_metric(
        current_executions, window_start=window_start, now=now
    )
    baseline_total_cost = _total_cost_metric(
        baseline_executions, window_start=baseline_window_start, now=window_start
    )

    current_savings = _cost_savings_metric(board, baseline_board)
    # The baseline period has no "savings vs its own baseline" in this
    # report — there is no board one window further back — so the baseline
    # side of this one metric is always `MetricNoData`, reading as
    # `indeterminate` via `_classify` like every other first-window metric.
    baseline_savings = learning_scoreboard.MetricNoData(
        name="cost_savings_usd", direction="higher_is_better"
    )

    token_savings = learning_scoreboard.MetricNoData(
        name="token_savings", direction="higher_is_better"
    )

    return (
        learning_scoreboard.MetricChange(
            name="first_pass_yield",
            direction="higher_is_better",
            status=_classify(baseline_fpy, current_fpy),
            baseline=baseline_fpy,
            current=current_fpy,
        ),
        learning_scoreboard.MetricChange(
            name="total_cost_usd",
            direction="lower_is_better",
            status=_classify(baseline_total_cost, current_total_cost),
            baseline=baseline_total_cost,
            current=current_total_cost,
        ),
        learning_scoreboard.MetricChange(
            name="cost_savings_usd",
            direction="higher_is_better",
            status=_classify(baseline_savings, current_savings),
            baseline=baseline_savings,
            current=current_savings,
        ),
        learning_scoreboard.MetricChange(
            name="token_savings",
            direction="higher_is_better",
            status=_classify(token_savings, token_savings),
            baseline=token_savings,
            current=token_savings,
        ),
    )


# --- KPI cards ---


def _kpi_card_html(change: learning_scoreboard.MetricChange) -> str:
    display_name = _KPI_DISPLAY_NAMES.get(change.name, change.name)
    direction_words = _DIRECTION_WORDS[change.direction]
    status_label = _STATUS_LABELS[change.status]
    value_str = _metric_value_repr(change.current)
    baseline_str = _metric_value_repr(change.baseline)
    return f"""
      <div class="kpi-card status-{_escape(change.status)}">
        <div class="kpi-name">{_escape(display_name)}</div>
        <div class="kpi-direction">{_escape(direction_words)}</div>
        <div class="kpi-value" dir="ltr">{_escape(value_str)}</div>
        <div class="kpi-baseline">was <span dir="ltr">{_escape(baseline_str)}</span></div>
        <div class="kpi-status">{_escape(status_label)}</div>
        <div class="kpi-metric-id" dir="ltr">{_escape(change.name)}</div>
      </div>"""


def _kpi_section_html(changes: tuple[learning_scoreboard.MetricChange, ...]) -> str:
    return "".join(_kpi_card_html(change) for change in changes)


# --- model family table ---


def _model_family_rows(
    worker_executions: tuple[Any, ...], *, window_start: datetime, now: datetime
) -> tuple[dict[str, Any], ...]:
    windowed = _windowed(worker_executions, window_start=window_start, now=now)
    by_family: dict[str, list[Any]] = {}
    for record in windowed:
        by_family.setdefault(record.model_family, []).append(record)

    rows = []
    for family in sorted(by_family):
        records = by_family[family]
        execution_count = len(records)
        success_count = sum(1 for record in records if record.success)
        tasks = {record.task.task_id for record in records}
        reworked_tasks = sum(
            1
            for task_id in tasks
            if len(
                {
                    record.run_id
                    for record in records
                    if record.task.task_id == task_id and record.run_id is not None
                }
            )
            > 1
        )
        rows.append(
            {
                "model_family": family,
                "execution_count": execution_count,
                "distinct_task_count": len(tasks),
                "total_cost_usd": sum(record.cost_estimate_usd for record in records),
                "success_rate": success_count / execution_count,
                "rework_rate": reworked_tasks / len(tasks),
                "mean_duration_ms": sum(record.duration_ms for record in records)
                / execution_count,
            }
        )
    return tuple(rows)


def _model_family_table_html(rows: tuple[dict[str, Any], ...]) -> str:
    if not rows:
        return '<p class="empty-state">No worker executions in this window.</p>'
    body_rows = []
    for row in rows:
        body_rows.append(
            f"""
        <tr>
          <td dir="ltr">{_escape(row["model_family"])}</td>
          <td dir="ltr">{row["execution_count"]}</td>
          <td dir="ltr">{row["distinct_task_count"]}</td>
          <td dir="ltr">{_escape(_format_value(row["total_cost_usd"]))}</td>
          <td dir="ltr">{row["success_rate"] * 100:.1f}%</td>
          <td dir="ltr">{row["rework_rate"] * 100:.1f}%</td>
          <td dir="ltr">{_escape(_format_value(row["mean_duration_ms"]))}</td>
        </tr>"""
        )
    return f"""
    <table class="data-table">
      <thead>
        <tr>
          <th>Model Family</th>
          <th>Executions</th>
          <th>Distinct Tasks</th>
          <th>Total Cost (USD)</th>
          <th>Success Rate</th>
          <th>Rework Rate</th>
          <th>Mean Duration (ms)</th>
        </tr>
      </thead>
      <tbody>{"".join(body_rows)}
      </tbody>
    </table>"""


# --- compliance & degradation ---


def _reduce_compliance(
    compliance: tuple[Any, ...], *, window_start: datetime, now: datetime
) -> dict[str, Any]:
    """Mirrors `learning_scoreboard._reduce_compliance`: one verdict per
    `session_id`, filtered by `session_last_activity`, last record in file
    order wins.
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


def _compliance_section_html(
    compliance: tuple[Any, ...], *, window_start: datetime, now: datetime
) -> str:
    reduced = _reduce_compliance(compliance, window_start=window_start, now=now)
    if not reduced:
        return '<p class="empty-state">No compliance audits in this window.</p>'
    rows = []
    for record in reduced.values():
        codes = ", ".join(record.issue_codes) if record.issue_codes else "—"
        rows.append(
            f"""
        <tr>
          <td dir="ltr">{_escape(record.session_id)}</td>
          <td dir="ltr">{record.violation_count}</td>
          <td dir="ltr">{_escape(codes)}</td>
          <td dir="ltr">{_escape(record.session_last_activity)}</td>
        </tr>"""
        )
    return f"""
    <table class="data-table">
      <thead>
        <tr>
          <th>Session</th>
          <th>Violations</th>
          <th>Issue Codes</th>
          <th>Last Activity (UTC)</th>
        </tr>
      </thead>
      <tbody>{"".join(rows)}
      </tbody>
    </table>"""


def _degradation_section_html(
    dialogues: tuple[Any, ...], *, window_start: datetime, now: datetime
) -> str:
    lines = []
    for record in dialogues:
        ts = learning_journal.parse_wire_timestamp(record.timestamp)
        if not _in_window(ts, window_start=window_start, now=now):
            continue
        if not record.degraded:
            continue
        marker = " — canary probe" if record.canaries_planted >= 1 else ""
        lines.append(
            f"""
        <li>
          <span dir="ltr">{_escape(record.timestamp)}</span> —
          {_escape(record.occasion)} ({_escape(record.topology)}) —
          task <span dir="ltr">{_escape(record.task.task_id)}</span> —
          {record.rounds_run} round(s){_escape(marker)}
        </li>"""
        )
    if not lines:
        return '<p class="empty-state">No budget degradations in this window.</p>'
    return f'<ul class="event-list">{"".join(lines)}</ul>'


def _consensus_section_html(
    dialogues: tuple[Any, ...], *, window_start: datetime, now: datetime
) -> str:
    """Summarize debated dialogues by final consensus verdict and rounds."""
    debated = tuple(
        record
        for record in _windowed(dialogues, window_start=window_start, now=now)
        if record.rounds
    )
    if not debated:
        return '<p class="empty-state">No debates in this window.</p>'
    consensus_count = sum(1 for record in debated if record.rounds[-1].verdict == "approved")
    total_rounds = sum(record.rounds_run for record in debated)
    return f"""
    <div class="consensus-summary">
      <div><strong dir="ltr">{consensus_count / len(debated) * 100:.1f}%</strong> consensus rate</div>
      <div><strong dir="ltr">{len(debated) - consensus_count}</strong> non-consensus debate(s)</div>
      <div><strong dir="ltr">{total_rounds / len(debated):.1f}</strong> mean rounds / debate</div>
    </div>"""


# --- role matrix (ticket 47) ---

# Declared JSON order of `routing-config.json`'s `roles` map, restated here
# only for display ordering — `get_role_matrix_view_data` already preserves
# `config.roles`' own insertion order, so this exists purely so a future
# role added out of that order still renders in a stable, reviewed spot
# rather than wherever dict iteration happens to put it. Any role id not
# listed here (a future addition) still renders — see `_ordered_role_ids`.
_ROLE_DISPLAY_ORDER: tuple[str, ...] = (
    "planner",
    "builder_heavy",
    "builder_light",
    "reviewer_architecture",
    "reviewer_risk",
    "reviewer_maintainability",
    "reviewer_security",
    "adjudicator",
    "sensitive_executor",
)

# The five roles user story 2 names as the Bento Grid's "primary" set
# (Planner, Heavy Builder, Light Builder, Critic, Adjudicator) — every role
# in `_ROLE_DISPLAY_ORDER` *not* in this set is exactly the "security,
# architecture, maintainability reviewers, and sensitive executor" group
# the spec's `all` mode adds (Implementation Decisions §2). `reviewer_risk`
# is the "Critic" of the two user-facing names — see `_ROLE_DISPLAY_NAMES`.
_PRIMARY_ROLE_IDS: frozenset[str] = frozenset(
    {"planner", "builder_heavy", "builder_light", "reviewer_risk", "adjudicator"}
)

_ROLE_DISPLAY_NAMES: dict[str, str] = {
    "planner": "Planner — מתכנן",
    "builder_heavy": "Heavy Builder — בנאי כבד",
    "builder_light": "Light Builder — בנאי קל",
    "reviewer_architecture": "Architecture Reviewer — מבקר ארכיטקטורה",
    "reviewer_risk": "Critic — מבקר",
    "reviewer_maintainability": "Maintainability Reviewer — מבקר תחזוקתיות",
    "reviewer_security": "Security Reviewer — מבקר אבטחה",
    "adjudicator": "Adjudicator — פוסק",
    "sensitive_executor": "Sensitive Executor — מבצע רגיש",
}

# The Bento Grid's colored accent sidebar — one hex per role. Only the
# planner's blue (`#2563eb`) is the spec's own named color (Implementation
# Decisions §2: "#2563eb interactive blues"); §2 otherwise only names the
# theme's shared surfaces (`#0f172a` headers, slate-50 canvas) and never
# defines a role-by-function scheme, so every other hex below is this
# module's own extension of that theme, not a spec-cited value: green for
# building, purple for the three review roles, amber for the adjudicator,
# red for `reviewer_security` and `sensitive_executor` — an editorial
# choice, not one mechanically derived from `routing-config.json`: there is
# no `sensitivity_gate` key or other single flag that picks out exactly
# these two and no others (`sensitive_executor`'s own
# `capability_requirements.local_only` is `True`, but so is
# `adjudicator`'s, and `adjudicator` is colored amber above, not red — so
# `local_only` cannot be the rule actually being followed here; treat this
# mapping as a curated visual choice, not a derived one). A role id absent
# here falls back to `--neutral` in `_role_accent_color`, so a future role
# never renders with a broken style.
_ROLE_ACCENT_COLORS: dict[str, str] = {
    "planner": "#2563eb",
    "builder_heavy": "#059669",
    "builder_light": "#10b981",
    "reviewer_architecture": "#7c3aed",
    "reviewer_risk": "#7c3aed",
    "reviewer_maintainability": "#7c3aed",
    "reviewer_security": "#dc2626",
    "adjudicator": "#d97706",
    "sensitive_executor": "#dc2626",
}

# Reasoning-effort badge colors. Four of the six rungs are exactly what user
# story 7 specifies: "Green for Low, Blue for Medium, Purple for High, Amber
# for Ultra". `xhigh` and `max` are this module's own extension of that
# scale, not spec-named values — but they are not optional: the closed
# vocabulary is `routing_config._EFFORT_RANK`, which has *six* rungs, and
# both of the unnamed two are genuinely selectable. Counted off
# `build_model_capabilities_registry()`'s 28 entries: `xhigh` is offered by
# 10 of them (every `codex_cli` model, plus the three Claude 5-family
# models) and `max` by 7 (those same three, plus `claude-sonnet-4-6` under
# `claude_code_cli`, plus the three `gpt-5.6-*` Codex models). Leaving them
# out — as this table did through ticket 47, whose comment here claimed "a
# rung absent here (there is none today)" while both of these were in fact
# absent — rendered two valid, expensive rungs in the same neutral grey the
# badge otherwise reserves for "unrecognized", the opposite of the instant
# cost signal user story 7 asks for. They continue the ramp between purple
# `high` and amber `ultra` as fuchsia and rose.
#
# Every hue user story 7 names is kept — green, blue, purple, amber — but
# three shades are darker than the first draft of this table, because a
# badge paints its text in the rung's color over that same color at 10%
# (`background:{color}1a`), and two of the drafted shades were not legible
# against their own tint: `ultra`'s #d97706 scored 2.86 where WCAG AA wants
# 4.5 for text this size, and `max`'s #b45309 scored 4.35. `xhigh` moved for
# a different reason — its drafted #6d28d9 sat ΔE 6.2 from `high`'s #7c3aed,
# far below the ~10 at which two colors stop being tellable apart, so the
# two most expensive Claude/Codex rungs were rendering as the same purple.
# A spec review running concurrently, with no sight of these measurements,
# independently called that same pair a weak spot in differentiation.
#
# `medium` is the one shade whose move costs something: #2563eb is one of
# only two hexes Implementation Decisions §2 names at all ("#0f172a primary
# headers, #2563eb interactive blues"), and it survives as exactly what §2
# calls it — it is still the planner's accent in `_ROLE_ACCENT_COLORS` and
# the theme's interactive blue, neither of which this touches. It is
# only the *badge* that uses the darker #1d4ed8, because at 4.49 the
# spec-named hex lands a hair under AA against its own tint, and legibility
# of a value an operator reads decides the shade here.
#
# Two tests pin all of this:
# `test_every_effort_rung_in_the_closed_vocabulary_has_its_own_badge_color`
# keeps the table matched to `_EFFORT_RANK`, and
# `test_badge_colors_are_legible_and_mutually_distinguishable` keeps every
# rung at AA against its own tint and every pair apart.
_EFFORT_BADGE_COLORS: dict[str, str] = {
    "low": "#1a7a4c",
    "medium": "#1d4ed8",
    "high": "#7c3aed",
    "xhigh": "#a21caf",
    "max": "#9f1239",
    "ultra": "#92400e",
}


_NEUTRAL_COLOR = "#8a8377"


def _role_accent_color(role_id: str) -> str:
    return _ROLE_ACCENT_COLORS.get(role_id, _NEUTRAL_COLOR)


# --- reactive model/effort binding (ticket 48) ---


class EffortState(NamedTuple):
    """Which reasoning effort a role should display for a given model, and
    why. `status` separates the two ways an effort dropdown can be empty,
    which the dashboard must not conflate:

    * `"ok"` — the model has a ladder; `effort` is a rung on it and `efforts`
      is the full ladder to offer.
    * `"none"` — the model genuinely has no reasoning-effort parameter
      (`supported_efforts=()`: `claude-3-7-sonnet` predates the ladder, and
      LM Studio is reached over an HTTP API that exposes no such parameter).
      There is nothing to choose, so `effort` is `None`.
    * `"unknown"` — no audited capability for this `(provider, model)` pair
      at all: the live-catalog drift `RoleModelBinding.capability=None`
      already carries. `effort` is whatever `routing-config.json` configures,
      passed through untouched — this view exists partly so an operator can
      *see* that drift, so it must not overwrite the configured value on the
      strength of not recognizing the model.
    """

    status: str
    effort: str | None
    efforts: tuple[str, ...]


def _resolve_effort_state(capability: Any | None, current_effort: str) -> EffortState:
    """Spec 0013 §3's auto-snap rule, as one pure function: keep
    `current_effort` when the model supports it, otherwise snap to the
    model's own `default_effort`, otherwise to the first rung it offers
    (the lowest, for every ladder in the audited catalog today — each is
    written in ascending order — though nothing enforces that ordering, so
    this describes the position taken, not a guaranteed ranking).

    Both consumers of this rule go through here. The initial server-side
    render calls it directly, so a document opened with scripting disabled
    still never displays an effort its bound model cannot accept; the
    embedded JavaScript re-implements exactly this for the reactive case,
    and `JsEffortSnapParityTests` runs one shared table of cases through
    both and asserts they agree, so the two cannot drift apart silently.

    The final fallback is `efforts[0]` rather than a raise: `default_effort`
    is legitimately `None` for a model whose provider publishes no per-model
    default (`agy models` does not, so every `antigravity_cli` entry with a
    multi-rung ladder lands here), and an unsupported configured effort is
    exactly the misconfiguration this dashboard exists to surface and let an
    operator correct — not one it should refuse to render.
    """
    if capability is None:
        return EffortState("unknown", current_effort, ())
    supported = tuple(capability.supported_efforts)
    if not supported:
        return EffortState("none", None, ())
    if current_effort in supported:
        return EffortState("ok", current_effort, supported)
    if capability.default_effort in supported:
        return EffortState("ok", capability.default_effort, supported)
    return EffortState("ok", supported[0], supported)


def _model_key(provider: str, model_id: str) -> str:
    """The `(provider, model_id)` capability-registry key, flattened for use
    as an `<option>` value and a JSON object key.

    Flattened rather than keyed on `model_id` alone because bare-model-id
    keying is precisely finding F7 that ticket 46's registry exists to fix,
    and the collision it warns about is live, not hypothetical:
    `claude-sonnet-4-6` is in the registry twice, with a different ladder
    each time (`antigravity_cli` offers low/medium/high; `claude_code_cli`
    adds `max` — `probe_models._CROSS_PROVIDER_EFFORT_LADDERS`). Keyed by
    model id, one of those two entries would silently overwrite the other,
    and whichever lost would be rendered with the winner's ladder — either
    offering `max` to `agy`, which rejects it, or hiding it from the
    `claude` path, which accepts it.
    """
    return f"{provider}::{model_id}"


def _effort_badge_color(effort: str | None) -> str:
    return _EFFORT_BADGE_COLORS.get(effort or "", _NEUTRAL_COLOR)


def _option_html(value: str, label: str, *, selected: bool) -> str:
    marker = " selected" if selected else ""
    return f'<option value="{_escape(value)}"{marker}>{_escape(label)}</option>'


def _model_option_label(key: str) -> str:
    return key.replace("::", " · ")


def _model_options_html(capabilities: Mapping[tuple[str, str], Any], selected_key: str) -> str:
    """Every audited `(provider, model)` pair as an option, plus the role's
    currently bound pair when the registry has never seen it — a drift
    binding must still be displayable as the selected value rather than
    silently reading as some other model.
    """
    options = []
    keys = sorted(_model_key(provider, model_id) for provider, model_id in capabilities)
    if selected_key not in keys:
        options.append(
            _option_html(selected_key, f"{_model_option_label(selected_key)} (drift)", selected=True)
        )
    for key in keys:
        options.append(_option_html(key, _model_option_label(key), selected=key == selected_key))
    return "".join(options)


def _effort_options_html(state: EffortState) -> str:
    """Mirrors the script's `effortOptionPairs`. A non-`ok` state still gets
    exactly one option so the control is never an empty box: the configured
    effort for `unknown` (drift — the value is real, only unvalidatable),
    and a bare `none` label for a model with no ladder at all.
    """
    if state.status == "ok":
        return "".join(
            _option_html(effort, effort, selected=effort == state.effort)
            for effort in state.efforts
        )
    # Falsy rather than `is None`: a `"none"` state carries `None`, but an
    # `unknown` state reached *from* a `"none"` one carries the empty string
    # it inherited from that empty select, and an option labelled with the
    # empty string renders as a blank row that reads as a broken control.
    if not state.effort:
        return _option_html("", "none", selected=True)
    return _option_html(state.effort, state.effort, selected=True)


def _role_controls_html(
    role_id: str, binding: Any, capabilities: Mapping[tuple[str, str], Any]
) -> str:
    """The role's active model and effort, as the two bound dropdowns and the
    color-coded badge ticket 48 makes reactive.

    Only the *first* preferred provider gets controls: that is the role's
    active binding, and the rest of `entry.bindings` is its fallback chain,
    which stays the read-only pill list ticket 47 renders. Configuring the
    chain itself is user story 9, not this ticket.
    """
    state = _resolve_effort_state(binding.capability, binding.reasoning_effort)
    selected_key = _model_key(binding.adapter, binding.model_id)
    color = _effort_badge_color(state.effort if state.status == "ok" else None)
    # A non-`ok` badge reads as its status word — "none" or "unknown" — which
    # is why `EffortState.status` uses exactly the two strings the badge
    # should show. Mirrors the script's `paintBadge`, which does the same.
    badge_text = state.effort if state.status == "ok" else state.status
    disabled = "" if state.status == "ok" else " disabled"
    return f"""
        <div class="role-card-controls">
          <label class="control-label">מודל
            <select class="model-select" data-role-id="{_escape(role_id)}" dir="ltr">{
                _model_options_html(capabilities, selected_key)
            }</select>
          </label>
          <label class="control-label">רמת חשיבה
            <select class="effort-select" data-role-id="{_escape(role_id)}"{disabled} dir="ltr">{
                _effort_options_html(state)
            }</select>
          </label>
          <span class="effort-badge" data-role-id="{_escape(role_id)}" dir="ltr" style="background:{
            _escape(color)
          }1a;color:{_escape(color)};">{_escape(badge_text)}</span>
        </div>"""


def _ordered_role_ids(entries: Mapping[str, Any]) -> tuple[str, ...]:
    """`_ROLE_DISPLAY_ORDER`'s roles that are actually present in `entries`,
    followed by any present role id `_ROLE_DISPLAY_ORDER` does not name (an
    addition to `routing-config.json` this module has not been taught yet)
    in sorted order — every entry renders, never silently dropped for want
    of a display-order slot.
    """
    known = tuple(role_id for role_id in _ROLE_DISPLAY_ORDER if role_id in entries)
    unknown = tuple(sorted(role_id for role_id in entries if role_id not in _ROLE_DISPLAY_ORDER))
    return known + unknown


def _pill_html(label: str, value: str, *, color: str | None = None) -> str:
    style = f' style="background:{_escape(color)}1a;color:{_escape(color)};"' if color else ""
    return (
        f'<span class="capability-pill"{style}>'
        f"{_escape(label)}: {_escape(value)}</span>"
    )


def _capability_requirements_pills(requirements: Any) -> str:
    pills = [
        _pill_html("Reasoning Tier", requirements.reasoning_tier),
        _pill_html("Tool Access", requirements.tool_access),
        _pill_html("Min Context", str(requirements.min_context)),
    ]
    if requirements.local_only:
        pills.append(_pill_html("Local Only", "yes", color="#b3261e"))
    return "".join(pills)


def _binding_html(binding: Any) -> str:
    capability = binding.capability
    effort_color = _EFFORT_BADGE_COLORS.get(binding.reasoning_effort, "#8a8377")
    pills = [
        _pill_html("Provider", binding.provider_id),
        _pill_html("Model", binding.model_id),
        _pill_html("Effort", binding.reasoning_effort, color=effort_color),
    ]
    if capability is None:
        pills.append(_pill_html("Capability", "unknown (drift)", color="#b3261e"))
    else:
        pills.append(_pill_html("Tier", capability.tier))
        pills.append(
            _pill_html(
                "Supported Efforts",
                ", ".join(capability.supported_efforts) if capability.supported_efforts else "none",
            )
        )
        pills.append(
            _pill_html("Context", str(capability.context) if capability.context is not None else "unknown")
        )
    return f"""
        <div class="role-binding">
          <div class="role-binding-pills">{"".join(pills)}</div>
        </div>"""


def _role_card_html(entry: Any, capabilities: Mapping[tuple[str, str], Any]) -> str:
    display_name = _ROLE_DISPLAY_NAMES.get(entry.role_id, entry.role_id)
    accent = _role_accent_color(entry.role_id)
    bindings_html = "".join(_binding_html(binding) for binding in entry.bindings)
    if not bindings_html:
        bindings_html = '<p class="empty-state">No preferred providers configured.</p>'
    controls_html = (
        _role_controls_html(entry.role_id, entry.bindings[0], capabilities)
        if entry.bindings
        else ""
    )
    return f"""
      <div class="role-card" data-role-id="{_escape(entry.role_id)}" style="border-inline-start-color:{_escape(accent)};">
        <div class="role-card-header">
          <div class="role-name">{_escape(display_name)}</div>
          <div class="role-id" dir="ltr">{_escape(entry.role_id)}</div>
        </div>
        <div class="role-card-requirements">{_capability_requirements_pills(entry.capability_requirements)}</div>{controls_html}
        <div class="role-card-bindings">{bindings_html}</div>
      </div>"""


def _role_matrix_grid_html(
    entries: Mapping[str, Any],
    role_ids: tuple[str, ...],
    capabilities: Mapping[tuple[str, str], Any],
) -> str:
    cards = "".join(_role_card_html(entries[role_id], capabilities) for role_id in role_ids)
    if not cards:
        return '<p class="empty-state">No roles configured.</p>'
    return cards


def _role_matrix_section_html(
    role_matrix: Mapping[str, Any], capabilities: Mapping[tuple[str, str], Any]
) -> str:
    """The Bento Grid plus its CSS-only "primary roles / all roles"
    segmented toggle. An empty `role_matrix` (the `render_html_report`
    default) renders the same empty state both grids fall back to, rather
    than a blank tab.

    A role in `_PRIMARY_ROLE_IDS` renders *twice* — once in each grid —
    since the advanced grid is the full list, not the complement of the
    primary one. So a role id is not unique in the document, and the
    controls are addressed by `data-role-id`, never by `id`.
    `onModelSelect` updates *every* card carrying the id for that reason:
    change a model in the primary grid and the advanced grid's copy of the
    same role must not still show the old one after the toggle flips.
    """
    ordered = _ordered_role_ids(role_matrix)
    primary_ids = tuple(role_id for role_id in ordered if role_id in _PRIMARY_ROLE_IDS)
    return f"""
    <input type="radio" id="role-mode-simple" name="role-mode" class="sr-only-toggle" checked>
    <input type="radio" id="role-mode-all" name="role-mode" class="sr-only-toggle">
    <div class="role-matrix-toolbar">
      <button type="button" class="pill-btn" id="refresh-models">🔄 רענן מודלים חיים</button>
      <span class="refresh-status" id="refresh-status" aria-live="polite"></span>
    </div>
    <div class="segmented-toggle">
      <label for="role-mode-simple">תפקידי מפתח (ראשי)</label>
      <label for="role-mode-all">פירוט מלא (מתקדם)</label>
    </div>
    <div class="role-grid" id="role-grid-simple">{
        _role_matrix_grid_html(role_matrix, primary_ids, capabilities)
    }
    </div>
    <div class="role-grid" id="role-grid-all">{
        _role_matrix_grid_html(role_matrix, ordered, capabilities)
    }
    </div>"""


# --- embedded capability payload and reactive script (ticket 48) ---

# `json.dumps` escapes `"` and `\\`, which is enough for a JSON *string* but
# not for JSON embedded in HTML: `</script>` inside any value would close the
# block early and the rest would be parsed as markup. Escaping these three
# characters to their `\\uXXXX` forms — still valid JSON, decoding back to the
# identical string — removes every character an HTML tokenizer reacts to, so
# no value can end the block or open a new element. Escaping `<` alone would
# be enough to stop `</script`, which is the only sequence that actually
# terminates the block; `>` and `&` go with it so the payload carries no
# character a tokenizer treats specially at all, rather than relying on that
# one rule staying the only one that matters.
_JSON_HTML_ESCAPES: dict[str, str] = {"<": "\\u003c", ">": "\\u003e", "&": "\\u0026"}


def _dashboard_config_json(
    capabilities: Mapping[tuple[str, str], Any],
    *,
    original_config: Mapping[str, Any] | None = None,
) -> str:
    """Everything the embedded script needs, and nothing it does not, as one
    JSON island.

    One block rather than several because each additional `<script>` is
    another tag `ScriptInjectionTests` has to bless, and because the effort
    palette travelling as data here — rather than as a JavaScript literal —
    is what keeps `_EFFORT_BADGE_COLORS` the single source of those colors
    for the server-rendered badge and the reactive one alike.

    Only the two fields the script reads are serialized. Of
    `ModelCapability`'s seven, `provider` and `model_id` are already the
    key, and `tier`, `context`, and `local_only` are deliberately omitted:
    the script reads none of them, and carrying them made this island 48%
    larger — 4677 bytes against 3165 over the live registry, so dropping
    them cut a third off it — with nothing to show for it: dead payload a
    reader cannot tell apart from data the page depends on. Two of the three are not even lost from the page:
    `_binding_html` renders `tier` and `context` server-side as pills.
    (`local_only` is genuinely not shown for a capability — the "Local
    Only" pill on a card reports the *role's*
    `capability_requirements.local_only`, a different field about a
    different thing.) Spec §1 does define the registry schema with all
    five, and names a `GET /api/model-capabilities` that would serve them,
    but the spec's own Testing Decisions group that endpoint with
    `--serve` — the local-server work, not this renderer — so it can
    serialize the whole record once something consumes it.

    `original_config` is `routing_config.RoutingConfig.to_dict()` as loaded
    for this render — the full `roles`/`providers` shape `POST /api/config`
    validates, not the reduced `{model, effort}` preview the role cards
    edit. `saveChanges` (ticket 51/52 follow-up, spec 0013 US14) reconciles
    the two client-side: it clones this untouched original, then only
    repoints each edited role's primary `preferred_providers` entry, so a
    save never has to fabricate the sections this page has no UI for
    (`council_policy`, `roster_topology`, and so on). Defaults to `{}` for
    the same reason `capabilities` defaults to empty — a document rendered
    without a config still renders, it just cannot reconcile a save.
    """
    payload = {
        "capabilities": {
            _model_key(provider, model_id): {
                "supportedEfforts": list(capability.supported_efforts),
                "defaultEffort": capability.default_effort,
            }
            for (provider, model_id), capability in capabilities.items()
        },
        "effortColors": dict(_EFFORT_BADGE_COLORS),
        "neutralColor": _NEUTRAL_COLOR,
        "originalConfig": dict(original_config) if original_config is not None else {},
    }
    # `sort_keys` alone makes the output deterministic — sorting the input
    # too would be a second mechanism for one guarantee.
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    for character, replacement in _JSON_HTML_ESCAPES.items():
        encoded = encoded.replace(character, replacement)
    return encoded


# The document's only executable block, and a literal one: every dynamic
# value reaches it through the `application/json` block above, as data it
# parses rather than as source the browser compiles.
# `test_the_embedded_script_interpolates_no_dynamic_value` pins that.
#
# `resolveEffortState` mirrors `_resolve_effort_state` rung for rung, and
# `JsEffortSnapParityTests` runs one shared table through both to keep them
# from drifting. `onModelSelect(roleId, newModel)` is ticket 48's named
# seam: it rebuilds the effort options from the newly selected model's
# ladder, snaps the effort, and repaints the badge — on every card carrying
# that role id, since a primary role has a card in each of the two grids.
#
# Ticket 49 adds the client-side state machine `currentRoles` /
# `savedSnapshot` / `undoHistory` names ask for, plus the floating action
# pill it drives. `paintRoleValue` is the DOM-painting half of
# `onModelSelect` pulled out into its own seam (`commitRoleEdit` is the
# other half both `onModelSelect` and `onEffortSelect` share), because
# `undoChange` and `resetDefaults` need to repaint a role from an arbitrary
# past `{model, effort}` pair the exact same way a fresh selection does —
# including re-resolving the effort ladder, since the model a role is
# reverted to is not necessarily the one currently on screen.
#
# `SYSTEM_DEFAULTS` and `CLIENT_STATE.savedSnapshot` look like the same
# thing at load — both are `cloneRoleState(buildRoleState())` at that
# instant — but they answer different questions and only one of them ever
# changes again. `savedSnapshot` is the dirty-check baseline `saveChanges`
# is explicitly allowed to move. `SYSTEM_DEFAULTS` is the page's own
# rendered values, captured once and never reassigned: nothing in this
# codebase's config model represents a role's assignment as independent
# from `routing-config.json` itself, so the only "system preset" this page
# can ever know about is the state it was rendered from. Spec 0013 US12
# asks to "restore factory routing presets at any time" — "at any time"
# is why `resetDefaults` reads `SYSTEM_DEFAULTS`, not `savedSnapshot`: after
# even one `saveChanges`, the two have diverged, and reading the mutable one
# would make "Reset to Default" only reach back as far as the last save,
# not to what actually shipped. `undoHistory` is a LIFO stack of one
# `{roleId, previous}` entry per edit that actually changed something (a
# re-selection of the already-current value pushes nothing), so `undoChange`
# always reverts the
# single most recent edit, to whichever role made it.
_SCRIPT = r"""
var DASHBOARD_CONFIG = JSON.parse(
  document.getElementById("dashboard-config").textContent
);
var MODEL_CAPABILITIES = DASHBOARD_CONFIG.capabilities;
var EFFORT_COLORS = DASHBOARD_CONFIG.effortColors;
var NEUTRAL_COLOR = DASHBOARD_CONFIG.neutralColor;
var ORIGINAL_CONFIG = DASHBOARD_CONFIG.originalConfig;

function resolveEffortState(modelKey, currentEffort) {
  var capability = MODEL_CAPABILITIES[modelKey];
  if (!capability) {
    return { status: "unknown", effort: currentEffort, efforts: [] };
  }
  var supported = capability.supportedEfforts;
  if (supported.length === 0) {
    return { status: "none", effort: null, efforts: [] };
  }
  if (supported.indexOf(currentEffort) !== -1) {
    return { status: "ok", effort: currentEffort, efforts: supported };
  }
  if (supported.indexOf(capability.defaultEffort) !== -1) {
    return { status: "ok", effort: capability.defaultEffort, efforts: supported };
  }
  return { status: "ok", effort: supported[0], efforts: supported };
}

function effortOptionPairs(state) {
  if (state.status === "ok") {
    return state.efforts.map(function (effort) {
      return [effort, effort];
    });
  }
  if (!state.effort) {
    return [["", "none"]];
  }
  return [[state.effort, state.effort]];
}

function setEffortOptions(select, state) {
  while (select.firstChild) {
    select.removeChild(select.firstChild);
  }
  var pairs = effortOptionPairs(state);
  for (var i = 0; i < pairs.length; i++) {
    var option = document.createElement("option");
    option.value = pairs[i][0];
    option.textContent = pairs[i][1];
    select.appendChild(option);
  }
  select.value = state.status === "ok" ? state.effort : pairs[0][0];
  select.disabled = state.status !== "ok";
}

function paintBadge(badge, state) {
  var color = state.status === "ok" && EFFORT_COLORS[state.effort]
    ? EFFORT_COLORS[state.effort]
    : NEUTRAL_COLOR;
  badge.textContent = state.status === "ok" ? state.effort : state.status;
  badge.style.background = color + "1a";
  badge.style.color = color;
}

function roleCards(roleId) {
  var matching = [];
  var cards = document.querySelectorAll(".role-card");
  for (var i = 0; i < cards.length; i++) {
    if (cards[i].getAttribute("data-role-id") === roleId) {
      matching.push(cards[i]);
    }
  }
  return matching;
}

function paintRoleValue(roleId, assignment) {
  var cards = roleCards(roleId);
  if (!cards.length) {
    return null;
  }
  var state = resolveEffortState(assignment.model, assignment.effort);
  for (var i = 0; i < cards.length; i++) {
    var card = cards[i];
    card.querySelector(".model-select").value = assignment.model;
    setEffortOptions(card.querySelector(".effort-select"), state);
    paintBadge(card.querySelector(".effort-badge"), state);
  }
  return state;
}

// Shared by `onModelSelect` and `onEffortSelect`, which differ only in
// which half of the pair the user just changed: paint the role from the
// candidate `{model, effort}` (snapping effort through `resolveEffortState`
// exactly as a fresh model pick would), then record whatever the paint
// actually landed on — not the raw candidate — as this edit's new value,
// since an unsupported effort snaps to something other than what was asked
// for. Only ever called with a `roleId` its caller already confirmed has
// cards, so `paintRoleValue` here never returns its "no such role" `null`.
function commitRoleEdit(roleId, model, effort) {
  var state = paintRoleValue(roleId, { model: model, effort: effort });
  recordStateChange(roleId, {
    model: model,
    effort: state.status === "ok" ? state.effort : (state.effort || "")
  });
  return state;
}

function onModelSelect(roleId, newModel) {
  var cards = roleCards(roleId);
  if (!cards.length) {
    return null;
  }
  var currentEffort = cards[0].querySelector(".effort-select").value;
  return commitRoleEdit(roleId, newModel, currentEffort);
}

function onEffortSelect(roleId, newEffort) {
  var cards = roleCards(roleId);
  if (!cards.length) {
    return { status: "ok", effort: newEffort, efforts: [] };
  }
  var currentModel = cards[0].querySelector(".model-select").value;
  return commitRoleEdit(roleId, currentModel, newEffort);
}

function bindSelect(select, roleId, handler) {
  if (!select) {
    return;
  }
  select.addEventListener("change", function () {
    handler(roleId, select.value);
  });
}

function bindRoleControls() {
  var cards = document.querySelectorAll(".role-card");
  for (var i = 0; i < cards.length; i++) {
    var card = cards[i];
    var roleId = card.getAttribute("data-role-id");
    bindSelect(card.querySelector(".model-select"), roleId, onModelSelect);
    bindSelect(card.querySelector(".effort-select"), roleId, onEffortSelect);
  }
}

// --- client state machine & floating action pill (ticket 49) ---

function cloneValue(assignment) {
  return assignment ? { model: assignment.model, effort: assignment.effort } : null;
}

function cloneRoleState(state) {
  var clone = {};
  for (var roleId in state) {
    if (Object.prototype.hasOwnProperty.call(state, roleId)) {
      clone[roleId] = cloneValue(state[roleId]);
    }
  }
  return clone;
}

function buildRoleState() {
  var state = {};
  var cards = document.querySelectorAll(".role-card");
  for (var i = 0; i < cards.length; i++) {
    var roleId = cards[i].getAttribute("data-role-id");
    if (Object.prototype.hasOwnProperty.call(state, roleId)) {
      continue;
    }
    var modelSelect = cards[i].querySelector(".model-select");
    var effortSelect = cards[i].querySelector(".effort-select");
    if (!modelSelect || !effortSelect) {
      continue;
    }
    state[roleId] = { model: modelSelect.value, effort: effortSelect.value };
  }
  return state;
}

function roleIsDirty(roleId) {
  var current = CLIENT_STATE.currentRoles[roleId];
  var saved = CLIENT_STATE.savedSnapshot[roleId];
  if (!current || !saved) {
    return false;
  }
  return current.model !== saved.model || current.effort !== saved.effort;
}

function dirtyRoleCount() {
  var count = 0;
  for (var roleId in CLIENT_STATE.currentRoles) {
    if (
      Object.prototype.hasOwnProperty.call(CLIENT_STATE.currentRoles, roleId) &&
      roleIsDirty(roleId)
    ) {
      count += 1;
    }
  }
  return count;
}

function recordStateChange(roleId, nextAssignment) {
  var previous = cloneValue(CLIENT_STATE.currentRoles[roleId]);
  CLIENT_STATE.currentRoles[roleId] = cloneValue(nextAssignment);
  if (
    previous &&
    (previous.model !== nextAssignment.model || previous.effort !== nextAssignment.effort)
  ) {
    CLIENT_STATE.undoHistory.push({ roleId: roleId, previous: previous });
  }
  refreshStateViews();
}

function updateActionPill() {
  var pill = document.getElementById("action-pill");
  if (!pill) {
    return;
  }
  var count = dirtyRoleCount();
  var label = document.getElementById("action-pill-label");
  if (label) {
    if (count === 0) {
      label.textContent = "אין שינויים לא שמורים";
    } else if (count === 1) {
      label.textContent = "שינוי אחד לא שמור";
    } else {
      label.textContent = count + " שינויים לא שמורים";
    }
  }
  if (count > 0) {
    pill.classList.add("is-visible");
  } else {
    pill.classList.remove("is-visible");
  }
}

function showToast(message, kind) {
  var container = document.getElementById("toast-container");
  if (!container) {
    return null;
  }
  var toast = document.createElement("div");
  toast.className = "toast toast-" + (kind || "info");
  toast.textContent = message;
  container.appendChild(toast);
  var timer = setTimeout(function () {
    container.removeChild(toast);
  }, TOAST_DURATION_MS);
  // `setTimeout` in Node returns a `Timeout` with `.unref()`, which lets the
  // process exit immediately instead of waiting out the toast's lifetime; a
  // browser's numeric return has no such method, and probing for it first
  // keeps this line a no-op there instead of a `TypeError`.
  if (timer && typeof timer.unref === "function") {
    timer.unref();
  }
  return toast;
}

function undoChange() {
  if (!CLIENT_STATE.undoHistory.length) {
    return false;
  }
  var entry = CLIENT_STATE.undoHistory.pop();
  paintRoleValue(entry.roleId, entry.previous);
  CLIENT_STATE.currentRoles[entry.roleId] = cloneValue(entry.previous);
  refreshStateViews();
  showToast("הפעולה האחרונה בוטלה", "info");
  return true;
}

function isRoleStateAtSystemDefaults() {
  for (var roleId in SYSTEM_DEFAULTS) {
    if (Object.prototype.hasOwnProperty.call(SYSTEM_DEFAULTS, roleId)) {
      var current = CLIENT_STATE.currentRoles[roleId];
      var reference = SYSTEM_DEFAULTS[roleId];
      if (!current || current.model !== reference.model || current.effort !== reference.effort) {
        return false;
      }
    }
  }
  return true;
}

// Restores `SYSTEM_DEFAULTS` — the values this page was rendered with, an
// immutable snapshot `saveChanges` never touches — never `savedSnapshot`,
// which does move on every save. "Restore factory routing presets at any
// time" (spec 0013 US12) means always, including after a save already
// moved the dirty-check baseline away from what shipped in
// `routing-config.json`; resetting `savedSnapshot` here too is what makes a
// post-reset edit register as dirty again against the same factory values,
// instead of against whatever was most recently saved.
function resetDefaults() {
  if (isRoleStateAtSystemDefaults()) {
    return false;
  }
  if (!confirm("לאפס את כל השינויים ולחזור לברירת המחדל של המערכת?")) {
    return false;
  }
  for (var roleId in SYSTEM_DEFAULTS) {
    if (Object.prototype.hasOwnProperty.call(SYSTEM_DEFAULTS, roleId)) {
      paintRoleValue(roleId, SYSTEM_DEFAULTS[roleId]);
      CLIENT_STATE.currentRoles[roleId] = cloneValue(SYSTEM_DEFAULTS[roleId]);
    }
  }
  CLIENT_STATE.savedSnapshot = cloneRoleState(SYSTEM_DEFAULTS);
  CLIENT_STATE.undoHistory = [];
  refreshStateViews();
  showToast("התפקידים אופסו לברירת המחדל", "success");
  return true;
}

// "Server mode" per spec 0013 Decision 4: the page was fetched over
// `http(s)://` (a `--serve` origin) rather than opened straight from disk
// over `file://`. `location` does not exist at all outside a browser (this
// script also runs headless under node in tests), so the guard checks for
// the global before reading off it.
function isServerMode() {
  return typeof location !== "undefined" && /^https?:$/.test(location.protocol);
}

function commitSaveSnapshot() {
  CLIENT_STATE.savedSnapshot = cloneRoleState(CLIENT_STATE.currentRoles);
  CLIENT_STATE.undoHistory = [];
  refreshStateViews();
}

// Reuses an existing `providers` entry only when its adapter/model/effort
// already match exactly; otherwise mints a `{roleId}__custom` entry that
// belongs to this role alone. Never mutates an existing entry in place —
// `providers` are heavily shared in the shipped config (`codex_sol` alone
// backs four roles), so overwriting one to reflect a single role's edit
// would silently repoint every other role sharing it.
function findOrCreateProviderId(providers, roleId, adapter, modelId, effort) {
  // `default_reasoning_effort` must be a non-empty string (routing_config's
  // own schema), but a role bound to a no-ladder model (e.g. LM Studio)
  // carries effort `""` client-side — the same placeholder the shipped
  // config already uses for its own no-ladder providers.
  var resolvedEffort = effort || "medium";
  for (var providerId in providers) {
    if (!Object.prototype.hasOwnProperty.call(providers, providerId)) {
      continue;
    }
    var provider = providers[providerId];
    if (
      provider.adapter === adapter &&
      provider.model === modelId &&
      provider.default_reasoning_effort === resolvedEffort
    ) {
      return providerId;
    }
  }
  var newProviderId = roleId + "__custom";
  providers[newProviderId] = {
    adapter: adapter,
    model: modelId,
    default_reasoning_effort: resolvedEffort
  };
  return newProviderId;
}

// Reconciles the reduced `{model, effort}` role state this page edits back
// into the full `RoutingConfig` shape `POST /api/config` validates (spec
// 0013 Decision 4's "Serialization Contract"). Only the edited role's
// *primary* `preferred_providers` entry is ever replaced — the rest of its
// fallback chain, every other role, and every section this page has no UI
// for (`council_policy`, `roster_topology`, ...) is carried through from
// `ORIGINAL_CONFIG` untouched.
function buildFullConfigPayload() {
  var config = JSON.parse(JSON.stringify(ORIGINAL_CONFIG));
  config.providers = config.providers || {};
  config.roles = config.roles || {};
  for (var roleId in CLIENT_STATE.currentRoles) {
    if (!Object.prototype.hasOwnProperty.call(CLIENT_STATE.currentRoles, roleId)) {
      continue;
    }
    var assignment = CLIENT_STATE.currentRoles[roleId];
    var role = config.roles[roleId];
    if (!assignment || !assignment.model || !role) {
      continue;
    }
    var separatorIndex = assignment.model.indexOf("::");
    if (separatorIndex === -1) {
      continue;
    }
    var adapter = assignment.model.slice(0, separatorIndex);
    var modelId = assignment.model.slice(separatorIndex + 2);
    var providerId = findOrCreateProviderId(
      config.providers, roleId, adapter, modelId, assignment.effort
    );
    var chain = (role.preferred_providers || []).slice();
    if (chain.length) {
      chain[0] = providerId;
    } else {
      chain = [providerId];
    }
    role.preferred_providers = chain;
  }
  return config;
}

function saveChanges() {
  if (!dirtyRoleCount()) {
    return false;
  }
  if (!isServerMode()) {
    commitSaveSnapshot();
    showToast("השינויים נשמרו", "success");
    return true;
  }
  if (typeof fetch === "undefined") {
    showToast("השמירה לשרת אינה נתמכת בדפדפן זה", "error");
    return false;
  }
  fetch("/api/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(buildFullConfigPayload())
  }).then(
    function (response) {
      if (response && response.ok) {
        commitSaveSnapshot();
        showToast("השינויים נשמרו בדיסק", "success");
      } else {
        showToast("השמירה נכשלה (השרת דחה את הבקשה)", "error");
      }
    },
    function () {
      showToast("השמירה נכשלה — האם השרת המקומי פעיל?", "error");
    }
  );
  return true;
}

function bindActionPill() {
  var undoButton = document.getElementById("action-undo");
  var resetButton = document.getElementById("action-reset");
  var saveButton = document.getElementById("action-save");
  if (undoButton) {
    undoButton.addEventListener("click", undoChange);
  }
  if (resetButton) {
    resetButton.addEventListener("click", resetDefaults);
  }
  if (saveButton) {
    saveButton.addEventListener("click", saveChanges);
  }
}

// --- live JSON drawer & clipboard export (ticket 50) ---

// The drawer previews `CLIENT_STATE.currentRoles` — every role's pending
// model/effort selection, live, before Save — not a full reconstruction of
// `routing-config.json`. A role's dropdown offers any audited
// `{adapter}::{model_id}` pair (ticket 48, user story 4), which carries no
// guaranteed id in the file's own `providers` section until a save
// actually resolves one; synthesizing a `RoleConfig`/`ProviderConfig` shape
// here, client-side, would mean either fabricating a provider id or reading
// it off the role card's own binding pill, which ticket 48 never repaints
// and so goes stale the instant a role's model changes. `buildConfigPreview`
// sticks to what this page knows for certain: the pending `{model, effort}`
// per role.
function buildConfigPreview() {
  return { roles: cloneRoleState(CLIENT_STATE.currentRoles) };
}

function configPreviewJson() {
  return JSON.stringify(buildConfigPreview(), null, 2);
}

// Escapes text for safe insertion as HTML *markup* rather than as a text
// node — needed the moment `updateConfigDrawer` stops writing
// `textContent` (which never interprets its argument) and starts building
// highlighted `innerHTML` instead. `syntaxHighlightJson` below wraps
// substrings of this escaped text in `<span>` tags, so every `&`/`<`/`>`
// the original JSON could carry must already be neutralized before that
// wrapping happens, the same concern `ScriptInjectionTests` holds every
// other interpolation in this document to.
function escapeHtmlText(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

// A minimal hand-rolled JSON syntax highlighter for the drawer's preview —
// no runtime dependency pulled in for it, matching this module's own
// "zero build step, zero external runtime dependencies" rule
// (Implementation Decisions §2), the same way this repo's own WCAG/ΔE
// color math is written out by hand rather than imported. Runs over
// `json` only after `escapeHtmlText` has already neutralized it, so the
// token regex below only ever recognizes shapes within already-safe text
// and never reintroduces a raw `<`/`>`/`&`. A matched string is a key
// exactly when it is immediately followed by `:` (`JSON.stringify`'s own
// two-space indent never puts whitespace before that colon, but `\s*`
// tolerates it if that ever changes).
function syntaxHighlightJson(json) {
  var tokenPattern =
    /("(\\u[a-fA-F0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\btrue\b|\bfalse\b|\bnull\b|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)/g;
  return escapeHtmlText(json).replace(tokenPattern, function (match) {
    var cssClass = "json-number";
    if (match.charAt(0) === '"') {
      cssClass = /:$/.test(match) ? "json-key" : "json-string";
    } else if (match === "true" || match === "false") {
      cssClass = "json-boolean";
    } else if (match === "null") {
      cssClass = "json-null";
    }
    return '<span class="' + cssClass + '">' + match + "</span>";
  });
}

function updateConfigDrawer() {
  var output = document.getElementById("config-drawer-json");
  if (!output) {
    return;
  }
  output.innerHTML = syntaxHighlightJson(configPreviewJson());
}

function toggleConfigDrawer() {
  var drawer = document.getElementById("config-drawer");
  if (!drawer) {
    return;
  }
  drawer.classList.toggle("is-open");
}

// `navigator.clipboard` is undefined outside a secure context — including
// a dashboard opened straight from disk over `file://`, the zero-friction
// default Implementation Decisions §4 names for this page — so this checks
// for it rather than letting a missing global throw a `TypeError` out of a
// click handler.
function copyConfigToClipboard() {
  if (
    typeof navigator === "undefined" ||
    !navigator.clipboard ||
    !navigator.clipboard.writeText
  ) {
    showToast("ההעתקה ללוח אינה נתמכת בדפדפן זה", "error");
    return false;
  }
  navigator.clipboard.writeText(configPreviewJson()).then(
    function () {
      showToast("הקונפיגורציה הועתקה ללוח", "success");
    },
    function () {
      showToast("העתקת הקונפיגורציה נכשלה", "error");
    }
  );
  return true;
}

function bindConfigDrawer() {
  var toggleButton = document.getElementById("config-drawer-toggle");
  var copyButton = document.getElementById("config-drawer-copy");
  if (toggleButton) {
    toggleButton.addEventListener("click", toggleConfigDrawer);
  }
  if (copyButton) {
    copyButton.addEventListener("click", copyConfigToClipboard);
  }
}

// --- live model refresh (ticket 52 follow-up, spec 0013 US8) ---

// Merges a `GET /api/model-capabilities` response into `MODEL_CAPABILITIES`
// and appends an `<option>` for every key this page did not already offer —
// "so that newly loaded local LLMs appear immediately in the interface"
// without a reload. Reuses `select.children`, not `.options`, the same way
// `setEffortOptions` already does, so the two stay consistent with each
// other and with the test harness's DOM stub. Returns the newly discovered
// keys so the caller can report how many were found.
function addModelOption(key) {
  var label = key.replace("::", " · ");
  var selects = document.querySelectorAll(".model-select");
  for (var i = 0; i < selects.length; i++) {
    var select = selects[i];
    var alreadyPresent = false;
    for (var j = 0; j < select.children.length; j++) {
      if (select.children[j].value === key) {
        alreadyPresent = true;
        break;
      }
    }
    if (!alreadyPresent) {
      var option = document.createElement("option");
      option.value = key;
      option.textContent = label;
      select.appendChild(option);
    }
  }
}

function applyCapabilitySnapshot(capabilities) {
  var addedKeys = [];
  for (var key in capabilities) {
    if (!Object.prototype.hasOwnProperty.call(capabilities, key)) {
      continue;
    }
    var entry = capabilities[key];
    if (!Object.prototype.hasOwnProperty.call(MODEL_CAPABILITIES, key)) {
      addedKeys.push(key);
      addModelOption(key);
    }
    MODEL_CAPABILITIES[key] = {
      supportedEfforts: entry.supportedEfforts,
      defaultEffort: entry.defaultEffort
    };
  }
  return addedKeys;
}

// Repaints every role from its own current `{model, effort}` — not just the
// dirty ones — so a refreshed ladder (a model whose `supportedEfforts` just
// changed) is reflected even for a role nobody has touched yet this session.
function repaintEveryRoleFromState() {
  for (var roleId in CLIENT_STATE.currentRoles) {
    if (
      Object.prototype.hasOwnProperty.call(CLIENT_STATE.currentRoles, roleId) &&
      CLIENT_STATE.currentRoles[roleId]
    ) {
      paintRoleValue(roleId, CLIENT_STATE.currentRoles[roleId]);
    }
  }
}

function setRefreshStatus(message) {
  var status = document.getElementById("refresh-status");
  if (status) {
    status.textContent = message;
  }
}

function refreshLiveModels() {
  if (typeof fetch === "undefined") {
    showToast("רענון מודלים חי אינו נתמך בדפדפן זה", "error");
    return false;
  }
  setRefreshStatus("בודק מודלים זמינים…");
  fetch("/api/model-capabilities").then(
    function (response) {
      if (!response || !response.ok || typeof response.json !== "function") {
        setRefreshStatus("");
        showToast("רענון המודלים נכשל — האם השרת המקומי פעיל?", "error");
        return;
      }
      response.json().then(function (data) {
        var added = applyCapabilitySnapshot((data && data.capabilities) || {});
        repaintEveryRoleFromState();
        var message = added.length
          ? "נמצאו " + added.length + " מודלים חדשים"
          : "אין מודלים חדשים";
        setRefreshStatus(message);
        showToast(message, "success");
      });
    },
    function () {
      setRefreshStatus("");
      showToast("רענון המודלים נכשל — האם השרת המקומי פעיל?", "error");
    }
  );
  return true;
}

function bindRefreshButton() {
  var button = document.getElementById("refresh-models");
  if (button) {
    button.addEventListener("click", refreshLiveModels);
  }
}

// Every mutation to `CLIENT_STATE` has exactly two views that must repaint
// from it — the action pill's dirty count and the drawer's JSON preview —
// so this is the one seam `recordStateChange`/`undoChange`/
// `resetDefaults`/`saveChanges` (and the initial render, below) call,
// mirroring why `commitRoleEdit` exists: a shape shared by more than one
// call site belongs in one place, not repeated at each of them.
function refreshStateViews() {
  updateActionPill();
  updateConfigDrawer();
}

var TOAST_DURATION_MS = 3200;

// The page's own rendered values — the only "system defaults" this
// document can know about (see `resetDefaults`) — captured once and never
// reassigned afterward, unlike `CLIENT_STATE.savedSnapshot`.
var SYSTEM_DEFAULTS = cloneRoleState(buildRoleState());

var CLIENT_STATE = {
  currentRoles: buildRoleState(),
  savedSnapshot: cloneRoleState(SYSTEM_DEFAULTS),
  undoHistory: []
};

bindRoleControls();
bindActionPill();
bindConfigDrawer();
bindRefreshButton();
refreshStateViews();
"""


# --- document assembly ---

_CSS = """
:root {
  --bg: #faf6ef;
  --panel: #ffffff;
  --ink: #2b2a28;
  --muted: #6f6a61;
  --slate: #4a5568;
  --border: #e6ddd0;
  --good: #1a7a4c;
  --bad: #b3261e;
  --neutral: #8a8377;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--ink);
  font-family: 'Rubik', 'Heebo', system-ui, -apple-system, sans-serif;
  line-height: 1.5;
}
header {
  padding: 1.5rem 2rem;
  border-bottom: 1px solid var(--border);
  background: var(--panel);
}
header h1 { margin: 0 0 0.25rem 0; font-size: 1.5rem; }
header .subtitle { color: var(--muted); font-size: 0.9rem; }
main { padding: 1.5rem 2rem; max-width: 1200px; margin: 0 auto; }
section { margin-bottom: 2rem; }
section h2 {
  font-size: 1.1rem;
  border-bottom: 2px solid var(--border);
  padding-bottom: 0.4rem;
  margin-bottom: 1rem;
}
.kpi-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 1rem;
}
.kpi-card {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 1rem;
}
.kpi-name { font-weight: 600; font-size: 0.95rem; }
.kpi-direction { color: var(--muted); font-size: 0.75rem; margin-bottom: 0.5rem; }
.kpi-value { font-size: 1.6rem; font-weight: 700; }
.kpi-baseline { color: var(--muted); font-size: 0.8rem; }
.kpi-metric-id { color: var(--neutral); font-size: 0.7rem; margin-top: 0.35rem; }
.kpi-status {
  display: inline-block;
  margin-top: 0.5rem;
  font-size: 0.75rem;
  font-weight: 600;
  padding: 0.1rem 0.5rem;
  border-radius: 999px;
}
.status-improved .kpi-status { background: #e3f3ea; color: var(--good); }
.status-regressed .kpi-status { background: #fbe7e5; color: var(--bad); }
.status-held .kpi-status, .status-indeterminate .kpi-status {
  background: #eeece6; color: var(--neutral);
}
.data-table { width: 100%; border-collapse: collapse; background: var(--panel); }
.data-table th, .data-table td {
  border-bottom: 1px solid var(--border);
  padding: 0.5rem 0.75rem;
  text-align: start;
  font-size: 0.9rem;
}
.data-table th { color: var(--slate); font-weight: 600; }
.event-list { list-style: none; margin: 0; padding: 0; }
.event-list li {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 0.5rem 0.75rem;
  margin-bottom: 0.5rem;
  font-size: 0.9rem;
}
.empty-state { color: var(--muted); font-style: italic; }
.consensus-summary { display: flex; flex-wrap: wrap; gap: 1rem; }
.consensus-summary div { background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: .75rem 1rem; }
footer { padding: 1rem 2rem; color: var(--muted); font-size: 0.8rem; }

/* Two-tab navigation & role-mode toggle (ticket 47) — CSS-only, driven by
   checked radio inputs and general-sibling selectors. This document ships
   no script tags at all; see this module's docstring for why. */
.sr-only-toggle { position: absolute; opacity: 0; width: 1px; height: 1px; pointer-events: none; }
.tab-bar { display: flex; gap: 0.5rem; margin-top: 0.75rem; }
.tab-bar label {
  cursor: pointer;
  padding: 0.4rem 0.9rem;
  border-radius: 999px;
  border: 1px solid var(--border);
  background: var(--panel);
  font-size: 0.85rem;
  font-weight: 600;
  color: var(--muted);
}
#tab-metrics:checked ~ header label[for="tab-metrics"],
#tab-roles:checked ~ header label[for="tab-roles"] {
  background: var(--slate);
  color: #fff;
  border-color: var(--slate);
}
#tab-content-metrics, #tab-content-roles { display: none; }
#tab-metrics:checked ~ #tab-content-metrics { display: block; }
#tab-roles:checked ~ #tab-content-roles { display: block; }

.role-matrix-main { padding: 1.5rem 2rem; max-width: 1200px; margin: 0 auto; }
.role-matrix-toolbar {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  margin-bottom: 1rem;
}
.refresh-status { font-size: 0.8rem; color: var(--muted); }
.segmented-toggle { display: flex; gap: 0.5rem; margin-bottom: 1rem; }
.segmented-toggle label {
  cursor: pointer;
  padding: 0.35rem 0.85rem;
  border-radius: 999px;
  border: 1px solid var(--border);
  background: var(--panel);
  font-size: 0.85rem;
  font-weight: 600;
  color: var(--muted);
}
#role-mode-simple:checked ~ .segmented-toggle label[for="role-mode-simple"],
#role-mode-all:checked ~ .segmented-toggle label[for="role-mode-all"] {
  background: var(--slate);
  color: #fff;
  border-color: var(--slate);
}
.role-grid { display: none; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 1rem; }
#role-mode-simple:checked ~ #role-grid-simple { display: grid; }
#role-mode-all:checked ~ #role-grid-all { display: grid; }
.role-card {
  background: var(--panel);
  border: 1px solid var(--border);
  border-inline-start: 4px solid var(--neutral);
  border-radius: 10px;
  padding: 1rem;
}
.role-card-header { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 0.5rem; }
.role-name { font-weight: 700; font-size: 1rem; }
.role-id { color: var(--neutral); font-size: 0.75rem; }
.role-card-requirements, .role-binding-pills { display: flex; flex-wrap: wrap; gap: 0.35rem; margin-bottom: 0.5rem; }
.role-binding { border-top: 1px dashed var(--border); padding-top: 0.5rem; margin-top: 0.5rem; }
.capability-pill {
  display: inline-block;
  background: #eeece6;
  color: var(--ink);
  font-size: 0.7rem;
  font-weight: 600;
  padding: 0.15rem 0.5rem;
  border-radius: 999px;
  direction: ltr;
}

/* Reactive model & effort controls (ticket 48). */
.role-card-controls {
  display: flex;
  flex-wrap: wrap;
  align-items: end;
  gap: 0.5rem;
  margin-bottom: 0.5rem;
}
.control-label {
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
  font-size: 0.7rem;
  font-weight: 600;
  color: var(--muted);
  flex: 1 1 8rem;
}
.control-label select {
  font-family: inherit;
  font-size: 0.75rem;
  padding: 0.25rem 0.4rem;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--panel);
  color: var(--ink);
  max-width: 100%;
}
.control-label select:disabled { color: var(--neutral); background: #f4f1ea; }
.effort-badge {
  font-size: 0.7rem;
  font-weight: 700;
  padding: 0.25rem 0.6rem;
  border-radius: 999px;
  background: #eeece6;
  color: var(--neutral);
}

/* Floating action pill & toast notifications (ticket 49). Both live inside
   `#tab-content-roles` (see `render_html_report`) rather than at the
   document's top level: that div is exactly what the tab-bar's CSS-only
   toggle already hides while the metrics tab is active, which keeps a
   dirty-state pill from ever floating over unrelated content on the other
   tab — no extra rule needed here to replicate that. */
.action-pill {
  position: fixed;
  left: 50%;
  bottom: 1.5rem;
  transform: translate(-50%, 150%);
  opacity: 0;
  pointer-events: none;
  transition: transform 0.25s ease, opacity 0.25s ease;
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.6rem 1rem;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.72);
  border: 1px solid rgba(230, 221, 208, 0.85);
  box-shadow: 0 12px 32px rgba(15, 23, 42, 0.18);
  backdrop-filter: blur(14px);
  -webkit-backdrop-filter: blur(14px);
  z-index: 40;
}
.action-pill.is-visible {
  transform: translate(-50%, 0);
  opacity: 1;
  pointer-events: auto;
}
.action-pill-dot {
  width: 0.55rem;
  height: 0.55rem;
  border-radius: 50%;
  background: var(--bad);
  flex: none;
  animation: action-pill-pulse 1.6s ease-in-out infinite;
}
@keyframes action-pill-pulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(179, 38, 30, 0.45); }
  50% { box-shadow: 0 0 0 6px rgba(179, 38, 30, 0); }
}
.action-pill-label { font-size: 0.85rem; font-weight: 600; white-space: nowrap; }
.action-pill-buttons { display: flex; gap: 0.4rem; }
.pill-btn {
  font-family: inherit;
  font-size: 0.8rem;
  font-weight: 600;
  padding: 0.35rem 0.85rem;
  border-radius: 999px;
  border: 1px solid var(--border);
  background: var(--panel);
  color: var(--ink);
  cursor: pointer;
}
.pill-btn-primary { background: var(--slate); color: #fff; border-color: var(--slate); }
.pill-btn-danger { color: var(--bad); border-color: var(--bad); }

.toast-stack {
  position: fixed;
  left: 50%;
  bottom: 5rem;
  transform: translateX(-50%);
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
  z-index: 50;
  pointer-events: none;
}
.toast {
  padding: 0.5rem 0.9rem;
  border-radius: 8px;
  font-size: 0.8rem;
  font-weight: 600;
  color: #fff;
  background: var(--slate);
  box-shadow: 0 8px 20px rgba(15, 23, 42, 0.2);
  animation: toast-in 0.2s ease;
}
.toast-success { background: var(--good); }
.toast-info { background: var(--slate); }
.toast-error { background: var(--bad); }
@keyframes toast-in {
  from { opacity: 0; transform: translateY(6px); }
  to { opacity: 1; transform: translateY(0); }
}

/* Live JSON drawer & clipboard export (ticket 50). Lives inside
   `#tab-content-roles` for the same reason the action pill does — see the
   comment above `.action-pill`. */
.config-drawer {
  position: fixed;
  left: 0;
  right: 0;
  bottom: 0;
  background: var(--panel);
  border-top: 1px solid var(--border);
  box-shadow: 0 -4px 16px rgba(15, 23, 42, 0.08);
  z-index: 30;
}
.config-drawer-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  padding: 0.5rem 1rem;
}
.config-drawer-toggle {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  font-family: inherit;
  font-size: 0.8rem;
  font-weight: 600;
  color: var(--ink);
  background: none;
  border: none;
  cursor: pointer;
  padding: 0.2rem 0;
}
.config-drawer-caret {
  display: inline-block;
  transition: transform 0.2s ease;
}
.config-drawer.is-open .config-drawer-caret { transform: rotate(90deg); }
.config-drawer-body {
  max-height: 0;
  overflow: hidden;
  transition: max-height 0.2s ease;
  border-top: 1px solid transparent;
}
.config-drawer.is-open .config-drawer-body {
  max-height: 40vh;
  overflow: auto;
  border-top-color: var(--border);
}
.config-drawer-json {
  margin: 0;
  padding: 0.85rem 1rem;
  font-family: 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.78rem;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--ink);
}
/* Token colors `syntaxHighlightJson` wraps the preview's spans in. A
   key's own trailing colon is part of its matched token (`tokenPattern`'s
   optional trailing-colon group), so it renders in `.json-key`'s color
   too, not `--ink` — only braces, commas, and whitespace are never
   wrapped and so stay `.config-drawer-json`'s own `--ink`. */
.config-drawer-json .json-key { color: #1d4ed8; font-weight: 600; }
.config-drawer-json .json-string { color: var(--good); }
.config-drawer-json .json-number { color: #b45309; }
.config-drawer-json .json-boolean { color: #7c3aed; }
.config-drawer-json .json-null { color: var(--neutral); font-style: italic; }
"""

# The floating action pill and its toast stack (ticket 49) — static markup,
# no dynamic value, so unlike every `*_html` function above this needs no
# `_escape` call anywhere. Its three buttons are `bindActionPill`'s named
# ids; the label starts on the "nothing is dirty" copy `updateActionPill`
# would otherwise have to paint on first load.
_ACTION_PILL_HTML = """
    <div class="action-pill" id="action-pill">
      <span class="action-pill-dot" aria-hidden="true"></span>
      <span class="action-pill-label" id="action-pill-label">אין שינויים לא שמורים</span>
      <div class="action-pill-buttons">
        <button type="button" class="pill-btn" id="action-undo">↩ בטל פעולה</button>
        <button type="button" class="pill-btn pill-btn-danger" id="action-reset">אפס לברירת מחדל</button>
        <button type="button" class="pill-btn pill-btn-primary" id="action-save">שמור שינויים</button>
      </div>
    </div>
    <div class="toast-stack" id="toast-container" aria-live="polite"></div>"""

# The collapsible live JSON drawer (ticket 50) — static markup, no dynamic
# value, so like `_ACTION_PILL_HTML` this needs no `_escape` call. Starts
# collapsed (`config-drawer` has no `is-open` class); `config-drawer-json`
# is empty until `updateConfigDrawer()` fills it on load. The toggle and
# copy buttons are siblings, not nested, so a real browser's click on the
# copy button never bubbles into the toggle's own listener.
_CONFIG_DRAWER_HTML = """
    <div class="config-drawer" id="config-drawer">
      <div class="config-drawer-header">
        <button type="button" class="config-drawer-toggle" id="config-drawer-toggle">
          <span class="config-drawer-caret" aria-hidden="true">▸</span>
          <span>routing-config.json</span>
        </button>
        <button type="button" class="pill-btn" id="config-drawer-copy">📋 העתק קונפיגורציה</button>
      </div>
      <div class="config-drawer-body">
        <pre class="config-drawer-json" id="config-drawer-json" dir="ltr"></pre>
      </div>
    </div>"""


def render_html_report(
    journal: learning_journal.JournalRead,
    board: learning_scoreboard.Scoreboard,
    baseline_board: learning_scoreboard.Scoreboard,
    *,
    now: datetime,
    window_days: int = DEFAULT_WINDOW_DAYS,
    role_matrix: Mapping[str, Any] | None = None,
    model_capabilities: Mapping[tuple[str, str], Any] | None = None,
    routing_config_dict: Mapping[str, Any] | None = None,
) -> str:
    """The pure contract door: a journal and its two already-computed
    boards in, a standalone HTML document out. Reads no clock, touches no
    disk.

    `board` must be the scoreboard computed at `now`, and `baseline_board`
    the one computed at `now - window_days` — both with the same
    `window_days` passed here — mirroring exactly what
    `render_weekly_report` builds internally from one journal. A mismatch on
    any of the three is a loud `ValueError`, not a silently wrong table.

    `role_matrix` is `routing_config.get_role_matrix_view_data`'s output —
    a `Mapping[str, RoleMatrixEntry]` — rendered as the second tab's Bento
    Grid (ticket 47). Defaults to an empty mapping (an empty grid, not a
    crash) rather than this function computing it itself: see this
    module's docstring for why loading `routing-config.json` belongs to
    `write_html_report`, not here.

    `model_capabilities` is `routing_config.build_model_capabilities_registry()`'s
    output, populating the model dropdowns and the reactive script's effort
    ladders (ticket 48). It is a separate parameter rather than something
    derived from `role_matrix` because the two answer different questions:
    `role_matrix` carries only the capabilities of models a role is
    *already* bound to, while the dropdown must offer every model an
    operator could switch *to* (user story 4). Defaults to empty for the
    same reason `role_matrix` does — a document whose dropdowns offer only
    the current binding, never a crash and never a disk read from this
    door.

    `routing_config_dict` is `routing_config.RoutingConfig.to_dict()` —
    embedded verbatim (see `_dashboard_config_json`) so the reactive
    `saveChanges` can reconcile an edited role's `{model, effort}` back
    into a full config payload for `POST /api/config` (spec 0013 US14).
    Defaults to empty for the same reason the other two do; a document
    rendered without one still renders, `saveChanges` just has nothing to
    reconcile onto in server mode.
    """
    _require_aware_now(now)
    _validate_window_days(window_days)
    if board.window_days != window_days:
        raise ValueError(
            f"board.window_days ({board.window_days!r}) must equal window_days ({window_days!r})"
        )
    if baseline_board.window_days != window_days:
        raise ValueError(
            "baseline_board.window_days "
            f"({baseline_board.window_days!r}) must equal window_days ({window_days!r})"
        )

    window_start = now - timedelta(days=window_days)
    if baseline_board.window_end != window_start:
        raise ValueError(
            "baseline_board must be computed at now - window_days "
            f"({window_start!r}), got window_end={baseline_board.window_end!r}"
        )
    baseline_window_start = window_start - timedelta(days=window_days)

    comparison = learning_scoreboard.compare_scoreboards(baseline_board, board)
    derived_changes = _derived_metric_changes(
        journal,
        board,
        baseline_board,
        window_start=window_start,
        baseline_window_start=baseline_window_start,
        now=now,
    )
    kpi_html = _kpi_section_html(comparison.changes + derived_changes)

    current_executions = _prefix_cut(journal.worker_executions, now=now)
    family_rows = _model_family_rows(current_executions, window_start=window_start, now=now)
    model_family_html = _model_family_table_html(family_rows)

    current_compliance = _prefix_cut(journal.compliance, now=now)
    compliance_html = _compliance_section_html(
        current_compliance, window_start=window_start, now=now
    )

    current_dialogues = _prefix_cut(journal.dialogues, now=now)
    degradation_html = _degradation_section_html(
        current_dialogues, window_start=window_start, now=now
    )
    consensus_html = _consensus_section_html(
        current_dialogues, window_start=window_start, now=now
    )

    window_line = f"{_utc_format(window_start)} → {_utc_format(now)} ({window_days} days)"
    journal_health = (
        f"{journal.unreadable_lines} unreadable line(s), "
        f"{journal.unknown_kind_lines} unknown-kind line(s)."
    )
    capabilities = model_capabilities if model_capabilities is not None else {}
    role_matrix_html = _role_matrix_section_html(
        role_matrix if role_matrix is not None else {}, capabilities
    )
    dashboard_config_json = _dashboard_config_json(
        capabilities, original_config=routing_config_dict
    )

    return f"""<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_escape("Learning Dashboard — לוח בקרה למידה")}</title>
<style>{_CSS}</style>
</head>
<body>
<input type="radio" id="tab-metrics" name="dashboard-tab" class="sr-only-toggle" checked>
<input type="radio" id="tab-roles" name="dashboard-tab" class="sr-only-toggle">
<header>
  <h1>Learning Dashboard <span style="color: var(--muted);">לוח בקרה למידה</span></h1>
  <div class="subtitle" dir="ltr">Light Mode · Window: {_escape(window_line)}</div>
  <div class="tab-bar">
    <label for="tab-metrics">מדדי ביצוע ולמידה</label>
    <label for="tab-roles">הגדרת תפקידים ומודלים</label>
  </div>
</header>
<div id="tab-content-metrics">
<main>
  <section>
    <h2>Key Performance Indicators — מדדים מרכזיים</h2>
    <div class="kpi-grid">{kpi_html}
    </div>
  </section>
  <section>
    <h2>Model Family Performance — ביצועי משפחות מודלים</h2>
    {model_family_html}
  </section>
  <section>
    <h2>Compliance Audits — ביקורות ציות</h2>
    {compliance_html}
  </section>
  <section>
    <h2>Consensus &amp; Debate — קונצנזוס ודיונים</h2>
    {consensus_html}
  </section>
  <section>
    <h2>Budget Degradations — אירועי הידרדרות תקציב</h2>
    {degradation_html}
  </section>
</main>
</div>
<div id="tab-content-roles">
<main class="role-matrix-main">
  <section>
    <h2>Role &amp; Model Configuration Matrix — מטריצת תפקידים ומודלים</h2>
    {role_matrix_html}
  </section>
</main>{_ACTION_PILL_HTML}{_CONFIG_DRAWER_HTML}
</div>
<footer dir="ltr">Journal health: {_escape(journal_health)}</footer>
<script type="application/json" id="dashboard-config">{dashboard_config_json}</script>
<script>{_SCRIPT}</script>
</body>
</html>
"""


def html_report_path(root_dir: Path, *, now: datetime) -> Path:
    """`root_dir / .ralph / reports / weekly-report-<UTC date of now>.html` —
    the HTML sibling of `learning_report.report_path`, in the same
    directory, named the same way.
    """
    _require_aware_now(now)
    date = now.astimezone(timezone.utc).date().isoformat()
    return root_dir / REPORTS_RELATIVE_DIR / f"weekly-report-{date}.html"


def _atomic_text_write(path: Path, content: str) -> None:
    """Write text without exposing a partially-written report. Mirrors
    `learning_report._atomic_text_write` exactly, duplicated for the same
    reason stated there — the helper is private to that module.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def write_html_report(
    journal_path: Path,
    output_path: Path | None = None,
    *,
    now: datetime,
    window_days: int = DEFAULT_WINDOW_DAYS,
) -> Path:
    """Read, render, and atomically write an HTML report.

    ``journal_path`` accepts the literal
    ``.ralph/learning_journal.jsonl`` path requested by the dashboard API;
    the report writer derives its root from that conventional location. For
    parity with ``write_weekly_report``, callers may also pass the project
    root directly. ``output_path`` defaults to ``html_report_path``.

    The role matrix rendered on the second tab always comes from this
    package's own ``routing-config.json`` (``routing_config.
    load_routing_config()``'s default path) — unrelated to ``root_dir``,
    the same way ``routing_config.ROUTING_CONFIG_PATH`` is a fixed sibling
    file every other consumer in this package reads regardless of which
    project root it is otherwise operating on.
    """
    _require_aware_now(now)
    _validate_window_days(window_days)
    root_dir = (
        journal_path.parent.parent
        if journal_path.name == learning_journal.JOURNAL_RELATIVE_PATH.name
        and journal_path.parent.name == learning_journal.JOURNAL_RELATIVE_PATH.parent.name
        else journal_path
    )
    journal = learning_journal.read_journal(root_dir)
    board = learning_scoreboard.compute_scoreboard(journal, now=now, window_days=window_days)
    baseline_board = learning_scoreboard.compute_scoreboard(
        journal, now=now - timedelta(days=window_days), window_days=window_days
    )
    capabilities = routing_config.build_model_capabilities_registry()
    loaded_config = routing_config.load_routing_config()
    role_matrix = routing_config.get_role_matrix_view_data(
        loaded_config, capabilities=capabilities
    )
    content = render_html_report(
        journal,
        board,
        baseline_board,
        now=now,
        window_days=window_days,
        role_matrix=role_matrix,
        model_capabilities=capabilities,
        routing_config_dict=loaded_config.to_dict(),
    )
    path = output_path if output_path is not None else html_report_path(root_dir, now=now)
    _atomic_text_write(path, content)
    return path
