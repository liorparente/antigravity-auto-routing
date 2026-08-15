#!/usr/bin/env python3
"""LearningReport: the weekly Markdown record of the learning loop.

Named for its siblings `learning_journal.py` (the record contract and its
reader), `learning_outcomes.py` (the hand-recorded ground truths), and
`learning_scoreboard.py` (the eight-metric snapshot). This module turns two
`learning_scoreboard.Scoreboard`s and one week's journal into the canonical
Markdown report a human reads in one sitting. See `implementation_plan.md`
(spec 0004 ticket 17) for the full design record; this docstring states only
what a caller of this module needs to know.

**A renderer, not a computer.** Every number in the report comes from
`learning_scoreboard.compute_scoreboard` and `compare_scoreboards`; this
module never re-derives a metric's value or its improved/held/regressed
classification from raw numbers — doing so would duplicate the one place
that direction-aware comparison lives. See implementation_plan.md Section 0.

**The baseline is recomputed, never persisted, never parsed.** "Direction
since the previous report" is answered by computing a second board at
`now - window_days`, over the same journal — never by reading a stored board
or a previous report file. See implementation_plan.md Section 3.

**Two doors and a path — nothing else public.** `report_path` computes where
a report belongs; `render_weekly_report` is the pure contract (journal in,
Markdown out, no clock, no disk); `write_weekly_report` is the three-line
convenience door that reads the journal, renders, and writes atomically. See
implementation_plan.md Section 2.

**This module owns no clock.** `now` is always injected on all three public
entry points, and there is no `datetime.now`, `datetime.utcnow`, `time.time`,
or `time.gmtime` anywhere below — matching `learning_scoreboard.py`'s own
guarantee, and enforced by the same AST guard test.

**`write_weekly_report` overwrites an existing same-name file by design.** A
second call on the same UTC date supersedes the first — the journal grows
between runs, so a later call's report is a more complete one, not a
duplicate of the same output. The write itself is atomic and durable,
mirroring `advisory_consultation._atomic_text_write` and
`agent_council._atomic_json_write`'s shape (not imported — the helper is
private, and importing across those modules would point the dependency
arrow backwards across `learning_report -> learning_scoreboard ->
learning_journal`). See implementation_plan.md Section 2.
"""
from __future__ import annotations

import dataclasses
import os
import tempfile
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TypeVar

import learning_journal
import learning_scoreboard

# Re-exported from `learning_scoreboard` — one source, never a second
# literal. See implementation_plan.md Section 2.
DEFAULT_WINDOW_DAYS: int = learning_scoreboard.DEFAULT_WINDOW_DAYS

REPORTS_RELATIVE_DIR = Path(".ralph") / "reports"

# Family attribute name on `Scoreboard`, paired with its section heading —
# both in `Scoreboard.metrics`' own family order. See implementation_plan.md
# Section 6's "family-section mechanism, made explicit".
_FAMILY_HEADINGS: tuple[tuple[str, str], ...] = (
    ("discipline", "Discipline"),
    ("critique_authenticity", "Critique authenticity"),
    ("efficiency", "Efficiency"),
    ("replay_benchmark", "Replay benchmark"),
)

_DIRECTION_WORDS: dict[str, str] = {
    "lower_is_better": "lower is better",
    "higher_is_better": "higher is better",
}


def _require_aware_now(now: datetime) -> None:
    """Refuse a naive `now`.

    Mirrors `learning_scoreboard.py`'s own function of the same name rather
    than importing it — that function is private, and importing a private
    name across modules is the one pattern this codebase never uses (see
    `_atomic_text_write` below). `datetime.utcnow()` and `datetime.now()`
    both return naive values, and the second is *local* time — accepting
    either would silently shift every window by the caller's UTC offset,
    producing a report that is wrong in a way nothing downstream could
    detect.
    """
    if now.tzinfo is None or now.tzinfo.utcoffset(now) is None:
        raise ValueError("now must be a timezone-aware datetime, got a naive value")


def _validate_window_days(value: object, field_name: str = "window_days") -> None:
    """A strictly positive, non-`bool` `int` — the shape of a day count.

    Mirrors `learning_scoreboard.py`'s own function of the same name rather
    than importing it, for the same reason as `_require_aware_now` above.
    `bool` is an `int` subclass, so `isinstance` alone would admit
    `window_days=True`. Left unvalidated, a negative or zero `window_days`
    makes the window run backwards or vanish — silently, since a report
    carries no evidence of what it summed, so the wrong answer looks exactly
    like the right one.
    """
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(  # noqa: TRY004 - one rejection contract; see learning_journal.py
            f"{field_name} must be an integer, got {type(value).__name__}"
        )
    if value <= 0:
        raise ValueError(f"{field_name} must be > 0, got {value!r}")


def _utc_format(value: datetime) -> str:
    """Render an aware `datetime` in the exact wire shape
    `learning_journal._utc_timestamp` writes — so a window bound and a
    degradation timestamp are byte-comparable. See implementation_plan.md
    Section 2's round-2 objection N2.
    """
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _format_value(value: float) -> str:
    """The one place a metric's number becomes a string, so precision can
    never drift between sections. See implementation_plan.md Section 6.
    """
    return f"{value:.4g}"


def _metric_repr(metric: learning_scoreboard.Metric, *, sample_size_format: str) -> str:
    """Shared by `_value_repr` and `_baseline_repr`, which differ only in how
    the sample size is punctuated around the value — `sample_size_format` is
    a template with `{value}` and `{n}` placeholders.
    """
    if isinstance(metric, learning_scoreboard.MetricNoData):
        return "no data"
    return sample_size_format.format(value=_format_value(metric.value), n=metric.sample_size)


def _value_repr(metric: learning_scoreboard.Metric) -> str:
    return _metric_repr(metric, sample_size_format="{value} (n={n})")


def _baseline_repr(metric: learning_scoreboard.Metric) -> str:
    return _metric_repr(metric, sample_size_format="{value}, n={n}")


def _metric_line(change: learning_scoreboard.MetricChange) -> str:
    direction_words = _DIRECTION_WORDS[change.direction]
    return (
        f"- {change.name} ({direction_words}): {_value_repr(change.current)} — "
        f"{change.status} (was {_baseline_repr(change.baseline)})"
    )


def _family_section_lines(
    board: learning_scoreboard.Scoreboard, comparison: learning_scoreboard.ScoreboardComparison
) -> list[str]:
    """One section per family, in `Scoreboard.metrics`'s own family-then-field
    order. A metric's family is recovered by walking `dataclasses.fields()`
    on the family dataclass instance itself — not a positional slice of the
    flat `Scoreboard.metrics` tuple and not a hardcoded name-to-family table
    — and looked up in `comparison.changes` by a plain `dict[name]`, never
    `.get`. A family/comparison mismatch therefore raises `KeyError` loudly
    at render time rather than silently omitting a metric line. See
    implementation_plan.md Section 6's "family-section mechanism, made
    explicit" and its round-2 objection N1.
    """
    changes_by_name = {change.name: change for change in comparison.changes}
    lines: list[str] = []
    for attr_name, heading in _FAMILY_HEADINGS:
        lines.append(f"## {heading}")
        family_instance = getattr(board, attr_name)
        for metric_field in dataclasses.fields(family_instance):
            change = changes_by_name[metric_field.name]
            lines.append(_metric_line(change))
        lines.append("")
    return lines


def _validate_entries(value: object, field_name: str) -> tuple[str, ...] | None:
    """`None` or a tuple of single-line, non-empty strings — never a bare
    `str`, which is iterable and would otherwise pass a per-entry check
    character by character. Container-first, then per-entry, mirroring
    `learning_journal._validate_tuple`'s own trap. See
    implementation_plan.md Section 4.
    """
    if value is None:
        return None
    if not isinstance(value, tuple):
        raise ValueError(  # noqa: TRY004 - one rejection contract; see learning_journal.py
            f"{field_name} must be None or a tuple, got {type(value).__name__}"
        )
    for index, entry in enumerate(value):
        if not isinstance(entry, str):
            raise ValueError(  # noqa: TRY004 - one rejection contract; see learning_journal.py
                f"{field_name}[{index}] must be a string, got {type(entry).__name__}"
            )
        if not entry.strip():
            raise ValueError(f"{field_name}[{index}] must not be empty or whitespace-only")
        if "\n" in entry or "\r" in entry:
            raise ValueError(f"{field_name}[{index}] must not contain a newline")
    return value


def _entries_section_body(entries: tuple[str, ...] | None) -> str:
    """`None` and `()` render differently, deliberately — see
    implementation_plan.md Section 4.
    """
    if entries is None:
        return "Not yet wired: no producer reports this section."
    if not entries:
        return "None this week."
    return "\n".join(f"- {entry}" for entry in entries)


def _in_window(ts: datetime, *, window_start: datetime, now: datetime) -> bool:
    """Half-open on the left, closed on the right — the same convention
    `learning_scoreboard._in_window` uses, so the report's degradation list
    and quiet-week detection describe the same week the board's windowed
    metrics do. See implementation_plan.md Sections 5, 7.
    """
    return window_start < ts <= now


def _degradation_lines(
    dialogues: tuple[learning_journal.DialogueQualityRecord, ...],
    *,
    window_start: datetime,
    now: datetime,
) -> tuple[str, ...]:
    """Every windowed `degraded=True` record, one line each — canary probes
    included and marked, since a listing (unlike an aggregation) must not
    silently drop a real budget event. See implementation_plan.md Section 5.
    """
    lines = []
    for record in dialogues:
        ts = learning_journal.parse_wire_timestamp(record.timestamp)
        if not _in_window(ts, window_start=window_start, now=now):
            continue
        if not record.degraded:
            continue
        marker = " — canary probe" if record.canaries_planted >= 1 else ""
        lines.append(
            f"- {record.timestamp} — {record.occasion} ({record.topology}) — "
            f"task {record.task.task_id} — {record.rounds_run} round(s){marker}"
        )
    return tuple(lines)


_RecordT = TypeVar("_RecordT")


def _has_window_timestamp(
    records: Iterable[_RecordT],
    *,
    timestamp_of: Callable[[_RecordT], str | None],
    window_start: datetime,
    now: datetime,
) -> bool:
    """True if any record in `records` has a window-relevant timestamp in
    `(window_start, now]`. `timestamp_of` extracts the wire timestamp string
    to check for one record, or returns `None` for a record that carries no
    timestamp at all (windowless, and so never in-window) — the compliance
    family's `session_last_activity`-may-be-`None` case. See
    implementation_plan.md Section 7.
    """
    for record in records:
        wire_timestamp = timestamp_of(record)
        if wire_timestamp is None:
            continue
        ts = learning_journal.parse_wire_timestamp(wire_timestamp)
        if _in_window(ts, window_start=window_start, now=now):
            return True
    return False


def _is_quiet_week(
    journal: learning_journal.JournalRead, *, window_start: datetime, now: datetime
) -> bool:
    """Zero records across all five families with a window-relevant
    timestamp in `(window_start, now]`. Compliance records date by
    `session_last_activity`; a record with none is windowless and cannot
    make a week non-quiet. See implementation_plan.md Section 7.
    """
    families = (
        (journal.worker_executions, lambda record: record.timestamp),
        (journal.outcomes, lambda record: record.timestamp),
        (journal.dialogues, lambda record: record.timestamp),
        (journal.compliance, lambda record: record.session_last_activity),
        (journal.replay_benchmarks, lambda record: record.timestamp),
    )
    for records, timestamp_of in families:
        if _has_window_timestamp(
            records, timestamp_of=timestamp_of, window_start=window_start, now=now
        ):
            return False
    return True


def report_path(root_dir: Path, *, now: datetime) -> Path:
    """`root_dir / .ralph / reports / weekly-report-<UTC date of now>.md`.

    Pure: no disk access. The date is `now`'s UTC date, not its local one —
    an aware `now` in a non-UTC zone must not name the file after a local
    date the journal's UTC timestamps never saw. See implementation_plan.md
    Section 2.
    """
    _require_aware_now(now)
    date = now.astimezone(timezone.utc).date().isoformat()
    return root_dir / REPORTS_RELATIVE_DIR / f"weekly-report-{date}.md"


def render_weekly_report(
    journal: learning_journal.JournalRead,
    *,
    now: datetime,
    window_days: int = DEFAULT_WINDOW_DAYS,
    adopted: tuple[str, ...] | None = None,
    reverted: tuple[str, ...] | None = None,
) -> str:
    """The pure contract door: journal in, Markdown out. Reads no clock,
    touches no disk.
    """
    _require_aware_now(now)
    _validate_window_days(window_days)
    adopted_entries = _validate_entries(adopted, "adopted")
    reverted_entries = _validate_entries(reverted, "reverted")

    current_board = learning_scoreboard.compute_scoreboard(
        journal, now=now, window_days=window_days
    )
    window_start = now - timedelta(days=window_days)
    baseline_board = learning_scoreboard.compute_scoreboard(
        journal, now=window_start, window_days=window_days
    )
    comparison = learning_scoreboard.compare_scoreboards(baseline_board, current_board)

    quiet = _is_quiet_week(journal, window_start=window_start, now=now)

    lines: list[str] = ["# Weekly Learning Report", ""]
    lines.append(
        f"Window: {_utc_format(window_start)} → {_utc_format(now)} ({window_days} days)"
    )
    if quiet:
        lines.append("")
        lines.append("**No activity dated in this window.**")
    lines.append("")

    lines.extend(_family_section_lines(current_board, comparison))

    lines.append("## Changes adopted this week")
    lines.append(_entries_section_body(adopted_entries))
    lines.append("")

    lines.append("## Changes reverted this week")
    lines.append(_entries_section_body(reverted_entries))
    lines.append("")

    lines.append("## Budget degradations")
    degradation_lines = _degradation_lines(
        journal.dialogues, window_start=window_start, now=now
    )
    if degradation_lines:
        lines.extend(degradation_lines)
    else:
        lines.append("None this week.")
    lines.append("")

    lines.append("## Journal health")
    lines.append(
        f"Whole file: {journal.unreadable_lines} unreadable line(s), "
        f"{journal.unknown_kind_lines} unknown-kind line(s)."
    )

    return "\n".join(lines) + "\n"


def _atomic_text_write(path: Path, content: str) -> None:
    """Write text without exposing a partially-written report.

    Replicates `advisory_consultation._atomic_text_write`'s and
    `agent_council._atomic_json_write`'s shape locally rather than importing
    either — the helper is private, and importing across those modules would
    point the dependency arrow backwards across `learning_report ->
    learning_scoreboard -> learning_journal`. `fsync` before `os.replace`
    guards against a crash immediately after the rename leaving a durable
    zero-length file; the `except`/`unlink` guards against a write that fails
    after the temp file exists leaking a stray file into
    `REPORTS_RELATIVE_DIR`. See implementation_plan.md Section 2.
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


def write_weekly_report(
    root_dir: Path,
    *,
    now: datetime,
    window_days: int = DEFAULT_WINDOW_DAYS,
    adopted: tuple[str, ...] | None = None,
    reverted: tuple[str, ...] | None = None,
) -> Path:
    """The convenience door: read the journal beneath `root_dir`, render, and
    write atomically beneath it, returning the path written.

    Overwrites an existing same-UTC-date report by design — a later call is a
    more complete report replacing an earlier one, not a duplicate of the
    same output. See implementation_plan.md Section 2.
    """
    _require_aware_now(now)
    journal = learning_journal.read_journal(root_dir)
    content = render_weekly_report(
        journal, now=now, window_days=window_days, adopted=adopted, reverted=reverted
    )
    path = report_path(root_dir, now=now)
    _atomic_text_write(path, content)
    return path
