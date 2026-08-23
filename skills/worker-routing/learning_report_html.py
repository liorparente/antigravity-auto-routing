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
"""
from __future__ import annotations

import html
import math
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

if __package__:
    from . import learning_journal, learning_report, learning_scoreboard
else:
    import learning_journal  # type: ignore[no-redef]
    import learning_report  # type: ignore[no-redef]
    import learning_scoreboard  # type: ignore[no-redef]

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
"""


def render_html_report(
    journal: learning_journal.JournalRead,
    board: learning_scoreboard.Scoreboard,
    baseline_board: learning_scoreboard.Scoreboard,
    *,
    now: datetime,
    window_days: int = DEFAULT_WINDOW_DAYS,
) -> str:
    """The pure contract door: a journal and its two already-computed
    boards in, a standalone HTML document out. Reads no clock, touches no
    disk.

    `board` must be the scoreboard computed at `now`, and `baseline_board`
    the one computed at `now - window_days` — both with the same
    `window_days` passed here — mirroring exactly what
    `render_weekly_report` builds internally from one journal. A mismatch on
    any of the three is a loud `ValueError`, not a silently wrong table.
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

    return f"""<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_escape("Learning Dashboard — לוח בקרה למידה")}</title>
<style>{_CSS}</style>
</head>
<body>
<header>
  <h1>Learning Dashboard <span style="color: var(--muted);">לוח בקרה למידה</span></h1>
  <div class="subtitle" dir="ltr">Light Mode · Window: {_escape(window_line)}</div>
</header>
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
<footer dir="ltr">Journal health: {_escape(journal_health)}</footer>
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
    content = render_html_report(
        journal, board, baseline_board, now=now, window_days=window_days
    )
    path = output_path if output_path is not None else html_report_path(root_dir, now=now)
    _atomic_text_write(path, content)
    return path
