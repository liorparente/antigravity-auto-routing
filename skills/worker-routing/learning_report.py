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

**Two doors and a path — for the report.** `report_path` computes where a
report belongs; `render_weekly_report` is the pure contract (journal in,
Markdown out, no clock, no disk); `write_weekly_report` is the three-line
convenience door that reads the journal, renders, and writes atomically. See
implementation_plan.md Section 2.

**A third, unrelated door: the local dashboard server.** Ticket 51 (Spec
0013) added `create_dashboard_server`/`serve_dashboard` and
`DEFAULT_SERVE_PORT` — the `--serve` CLI mode that answers `GET /`,
`POST /api/config`, and `GET /api/model-capabilities`. It shares this module
only because the ticket named `learning_report.py` as where `--serve`
belongs, not because it renders a report: it reads no journal and writes no
Markdown, and `main` treats it as a fully separate mode that returns before
any of the report-writing code below runs. `GET /` serves a report `--html`
wrote earlier rather than rendering one, so this mode stays clock-free like
the rest of the module.

**This module owns no clock.** `now` is always injected on all three public
entry points, and there is no `datetime.now`, `datetime.utcnow`, `time.time`,
or `time.gmtime` anywhere below — matching `learning_scoreboard.py`'s own
guarantee, and enforced by the same AST guard test.

**`write_weekly_report` overwrites an existing same-name file by design.** A
second call on the same UTC date supersedes the first — the journal grows
between runs, so a later call's report is a more complete one, not a
duplicate of the same output. The write itself is atomic and durable,
mirroring `dialogue_transcript._atomic_text_write` and
`agent_council._atomic_json_write`'s shape (not imported — the helper is
private, and importing across those modules would point the dependency
arrow backwards across `learning_report -> learning_scoreboard ->
learning_journal`). See implementation_plan.md Section 2.
"""
from __future__ import annotations

import argparse
import dataclasses
import http.server
import json
import os
import tempfile
from collections.abc import Callable, Iterable, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, TypeVar

if __package__:
    from . import learning_journal, learning_scoreboard, probe_models, routing_config
else:
    import learning_journal  # type: ignore[no-redef]
    import learning_scoreboard  # type: ignore[no-redef]
    import probe_models  # type: ignore[no-redef]
    import routing_config  # type: ignore[no-redef]

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

    Replicates `dialogue_transcript._atomic_text_write`'s and
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


# ---------------------------------------------------------------------------
# Local dashboard server & atomic save API (ticket 51 / Spec 0013)
# ---------------------------------------------------------------------------

DEFAULT_SERVE_PORT = 8080

# `probe_all`'s own docstring: "Pass `list_models=False` for spec 0013's
# launch probe: it keeps every provider local, at the cost of `agy`'s live
# listing." And `CatalogSnapshot.to_dict`'s: "Shaped by `to_dict` into the
# capability payload spec 0013's dashboard reads — but it is a plain value
# object, not an HTTP concern." Ticket 45 built both specifically for this
# endpoint; this is that HTTP concern.
CapabilitySnapshotSource = Callable[[], probe_models.CatalogSnapshot]


def _probe_capability_snapshot() -> probe_models.CatalogSnapshot:
    return probe_models.probe_all(list_models=False)


def _model_key(provider: str, model_id: str) -> str:
    """The flattened ``{provider}::{model_id}`` capability key — the same
    shape `learning_report_html._model_key` renders into the dashboard's
    `<option>` values, kept as a second one-line function rather than an
    import across modules. `_atomic_text_write` is the precedent: a
    one-liner duplicated locally in every module that needs it rather than
    imported from `dialogue_transcript` or `agent_council`. The rule is
    about *one-liners not worth coupling two modules over*, not a blanket
    ban on cross-module private access — `routing_config.py` deliberately
    reads `probe_models._CROSS_PROVIDER_EFFORT_LADDERS` where the shared
    thing is real data with a single owner. See
    `learning_report_html._model_key`'s own docstring for why a flattened
    key, not bare `model_id`, is what a capability consumer needs.
    """
    return f"{provider}::{model_id}"


def _latest_dashboard_path(root_dir: Path) -> Path | None:
    """The most recent ``weekly-report-<date>.html`` under `root_dir`, or
    `None` when none has been generated yet.

    This is how ``GET /`` stays clock-free: report filenames embed an
    ISO-8601 UTC date, which sorts lexicographically, so "the newest one"
    is `max()` over the glob rather than a comparison against a live clock
    this module is forbidden from reading (see the module docstring's
    "This module owns no clock", enforced by the AST guard test). Resolved
    per request rather than bound once at startup, so an operator who runs
    ``--html`` while the server is already up sees the new report on the
    next reload instead of having to restart it.
    """
    reports_dir = root_dir / REPORTS_RELATIVE_DIR
    candidates = sorted(reports_dir.glob("weekly-report-*.html"))
    return candidates[-1] if candidates else None


class _ConfigApiHandler(http.server.BaseHTTPRequestHandler):
    """Three routes: ``GET /``, ``POST /api/config``, and
    ``GET /api/model-capabilities``.

    ``self.server`` is this handler's owning `_ConfigApiServer` — the
    `http.server` machinery re-instantiates a `BaseHTTPRequestHandler` per
    request, so any per-request state (here, the save path and the
    capability-snapshot source) has to live on the shared server object
    instead of on the handler itself.

    **POST /api/config.** The request body is validated exactly as
    `routing_config.parse_routing_config` already validates a config loaded
    from disk — this handler adds no schema knowledge of its own, so a
    payload that would fail `load_routing_config` fails here too, for the
    same reason. The role cards (ticket 48/50) preview a reduced
    ``{roles: {role_id: {model, effort}}}`` shape, not this validator's full
    `RoleConfig`/`ProviderConfig` shape — this handler knows nothing of that
    reconciliation. It is `learning_report_html`'s embedded
    `buildFullConfigPayload` (ticket 52 follow-up, spec 0013 US14) that
    reconstitutes a full config client-side before ever POSTing here: it
    clones the page's own embedded `originalConfig`, repoints only an
    edited role's primary `preferred_providers` entry, and reuses or mints
    a `providers` entry for it — this handler only ever sees, and only
    ever needs to validate, that full shape.

    **GET /api/model-capabilities.** Spec 0013 §1 names this endpoint and
    groups its Testing Decisions with `--serve`. It calls
    `self.server.capability_snapshot()` — `_probe_capability_snapshot` by
    default, `probe_models.probe_all(list_models=False)`, built for exactly
    this (see the module comment above `CapabilitySnapshotSource`) — but does
    not return `CatalogSnapshot.to_dict()` verbatim: that method's `"models"`
    map is keyed by bare `model_id`, deduplicated across providers, which is
    precisely finding F7 (`routing_config.ModelCapability`'s own docstring)
    — the same model id can carry a different effort ladder under two CLI
    adapters (`probe_models._CROSS_PROVIDER_EFFORT_LADDERS` records this for
    real, not hypothetically), and that merge silently drops one. `do_GET`
    instead walks `snapshot.providers` directly, keying each entry
    `{provider_id}::{model_id}` — the same flattened shape
    `learning_report_html._model_key` uses for the dashboard's own
    `<option>` values — and looks each pair up in `routing_config.
    build_model_capabilities_registry()` for its audited `tier` (spec §1's
    Registry Schema names `tier` as part of this data; `ProbedModel` itself
    doesn't carry one). A live-probed pair absent from the audited registry
    — a newly loaded local model the catalog hasn't caught up to yet —
    reports `tier: null` rather than raising, mirroring `RoleModelBinding.
    capability`'s own "``None`` is a known, non-fatal drift state" contract.
    `list_models=False` keeps every provider probe local and fast (a socket
    probe to LM Studio under its own 200ms timeout, a `PATH` lookup and a
    local cache read per CLI provider — no CLI subprocess is ever run), so
    this is the same non-blocking probe spec §1's "Live Capability Probing
    on Launch" describes, not a slow one merely relabeled for this route.
    """

    server: _ConfigApiServer

    def do_POST(self) -> None:
        if not self._require_path("/api/config"):
            return
        content_length = int(self.headers.get("Content-Length") or 0)
        raw_body = self.rfile.read(content_length) if content_length > 0 else b""
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            self._respond_json(400, {"error": f"invalid JSON body: {error}"})
            return
        try:
            config = routing_config.parse_routing_config(payload, fallback_on_missing=True)
        except routing_config.ConfigValidationError as error:
            self._respond_json(400, {"error": str(error)})
            return
        _atomic_text_write(
            self.server.config_path, json.dumps(config.to_dict(), indent=2) + "\n"
        )
        self._respond_json(200, {"status": "ok"})

    def do_GET(self) -> None:
        if self.path == "/":
            self._serve_dashboard_document()
            return
        if not self._require_path("/api/model-capabilities"):
            return
        snapshot = self.server.capability_snapshot()
        registry = routing_config.build_model_capabilities_registry()
        capabilities: dict[str, dict[str, Any]] = {}
        for probe in snapshot.providers:
            for model in probe.models:
                audited = registry.get((probe.provider_id, model.model_id))
                key = _model_key(probe.provider_id, model.model_id)
                capabilities[key] = {
                    "provider": probe.provider_id,
                    "modelId": model.model_id,
                    "supportedEfforts": list(model.supported_efforts),
                    "defaultEffort": model.default_effort,
                    "tier": audited.tier if audited is not None else None,
                    "context": model.context_window,
                    "localOnly": model.local_only,
                    "source": model.source,
                }
        providers = [
            {"providerId": probe.provider_id, "available": probe.available, "error": probe.error}
            for probe in snapshot.providers
        ]
        self._respond_json(200, {"capabilities": capabilities, "providers": providers})

    def _serve_dashboard_document(self) -> None:
        """``GET /`` — the dashboard itself, which is what makes every other
        route on this server reachable.

        Without this route `--serve` bound a socket that answered only its
        two `/api/*` endpoints, so the only way to open the dashboard was
        over ``file://`` — and the page's own `isServerMode()` guard
        (`/^https?:$/.test(location.protocol)`) is false there. That made
        three separate spec 0013 behaviours dead code in practice: US14's
        ``POST /api/config`` save branch, Decision 1's automatic launch
        probe, and US8's "🔄 רענן מודלים חיים" button, whose root-relative
        `fetch("/api/model-capabilities")` resolves to
        `file:///api/model-capabilities` and always fails. Serving the
        document from the same origin as the API is the whole fix.

        The document is read from disk rather than rendered here: rendering
        needs a `now` (`learning_report_html.render_html_report` requires an
        aware instant) and this module owns no clock. `--serve` therefore
        publishes what `--html` already wrote, and says so plainly when
        nothing has been written yet rather than serving a blank page.

        **Known consequence: a save is not reflected in a *reloaded* page.**
        `write_html_report` embeds `load_routing_config()` as it stood when
        `--html` ran, so after a successful `POST /api/config` the file on
        disk is current but this document is not. Within the open page the
        operator sees their change (the client state machine owns it, and
        `commitSaveSnapshot` rebases the dirty baseline), so US14's "changes
        take effect immediately in the active workspace" holds for the
        workspace; it is a browser reload that shows the pre-save matrix
        until `--html` is run again. Re-rendering here would need a clock,
        which is the one thing this module may not have — so the honest fix
        is a regeneration step, not a silent stale read, and it is recorded
        here rather than papered over.
        """
        dashboard = _latest_dashboard_path(self.server.dashboard_root)
        if dashboard is None:
            self._respond_json(
                503,
                {
                    "error": (
                        "no dashboard has been generated yet — run "
                        "`learning_report.py --html --now <ISO-8601>` first"
                    )
                },
            )
            return
        self._respond_bytes(
            200, dashboard.read_bytes(), "text/html; charset=utf-8"
        )

    def _require_path(self, expected: str) -> bool:
        """The 404 guard both routes open with — shared so the "wrong path"
        shape lives in one place rather than twice, verbatim, at the top of
        `do_POST` and `do_GET`.
        """
        if self.path != expected:
            self._respond_json(404, {"error": f"no such endpoint: {self.path}"})
            return False
        return True

    def _respond_json(self, status: int, body: dict[str, Any]) -> None:
        self._respond_bytes(status, json.dumps(body).encode("utf-8"), "application/json")

    def _respond_bytes(self, status: int, data: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: Any) -> None:
        """Silence the default stderr access log — a failed request already
        surfaces its reason to the caller as an HTTP status and JSON body."""


class _ConfigApiServer(http.server.HTTPServer):
    """Binds `_ConfigApiHandler` to an injectable save path and capability
    snapshot source, so a test never touches this package's real
    `routing-config.json` and never depends on which CLIs happen to be
    installed on the machine running the test.
    """

    def __init__(
        self,
        server_address: tuple[str, int],
        config_path: Path,
        capability_snapshot: CapabilitySnapshotSource,
        dashboard_root: Path,
    ) -> None:
        self.config_path = config_path
        self.capability_snapshot = capability_snapshot
        self.dashboard_root = dashboard_root
        super().__init__(server_address, _ConfigApiHandler)


def create_dashboard_server(
    *,
    port: int,
    config_path: Path | None = None,
    capability_snapshot: CapabilitySnapshotSource | None = None,
    dashboard_root: Path | None = None,
) -> _ConfigApiServer:
    """Construct, but do not start, the local dashboard save server.

    Starting is the caller's job via `.serve_forever()` — kept separate so a
    test can bind an OS-assigned port (``port=0``), read the port it was
    actually given off `server.server_address`, issue real HTTP requests
    against it, and shut it down again without ever blocking the test
    process. `config_path` defaults to `routing_config.ROUTING_CONFIG_PATH` —
    a fixed sibling file, unrelated to any `--root-dir` this CLI is otherwise
    given, the same way every other consumer in this package reads and
    writes it (see `learning_report_html.write_html_report`'s own note on
    the same fixed path). `capability_snapshot` defaults to
    `_probe_capability_snapshot`; a test overrides it with a fixed
    `CatalogSnapshot` so `GET /api/model-capabilities` reads the same
    deterministic fixture on every machine, real CLIs installed or not.
    `dashboard_root` is the project root ``GET /`` looks under for the newest
    generated report (`_latest_dashboard_path`); it defaults to the process's
    working directory, matching the CLI's own ``--root-dir`` default, and a
    test points it at a temporary directory.
    """
    resolved_config_path = (
        config_path if config_path is not None else routing_config.ROUTING_CONFIG_PATH
    )
    resolved_capability_snapshot = (
        capability_snapshot if capability_snapshot is not None else _probe_capability_snapshot
    )
    resolved_dashboard_root = dashboard_root if dashboard_root is not None else Path(".")
    return _ConfigApiServer(
        ("127.0.0.1", port),
        resolved_config_path,
        resolved_capability_snapshot,
        resolved_dashboard_root,
    )


def serve_dashboard(*, port: int, dashboard_root: Path) -> None:
    """The CLI door: bind and serve until interrupted.

    No `config_path` parameter, unlike `create_dashboard_server`: nothing
    calls this with an override — no CLI flag selects one, and every test
    that needs a non-default path calls `create_dashboard_server` directly
    to get a server it can bind and stop itself without ever blocking on
    `.serve_forever()`. A prior revision carried the parameter through
    anyway; a Standards review flagged it as dead surface with no caller,
    so it was removed rather than justified with a test that would only
    exist to justify it. `dashboard_root` is different: ``--root-dir`` is a
    real CLI flag that selects it, so it is threaded through rather than
    defaulted here.
    """
    server = create_dashboard_server(port=port, dashboard_root=dashboard_root)
    try:
        server.serve_forever()
    finally:
        server.server_close()


def _parse_injected_now(value: str) -> datetime:
    """Parse an aware ISO-8601 instant supplied by the CLI caller."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"invalid ISO-8601 timestamp: {value!r}") from error
    try:
        _require_aware_now(parsed)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error
    return parsed


def main(argv: Sequence[str] | None = None) -> int:
    """Write Markdown and, with ``--html``, the matching HTML dashboard.

    ``--html`` writes the default dashboard, ``--html PATH`` selects a path,
    and ``--html -`` prints the document. ``--no-markdown`` makes HTML the
    only output. The timestamp is explicit so this module remains clock-free.

    ``--serve [PORT]`` (default port `DEFAULT_SERVE_PORT`) instead starts the
    local dashboard save server and blocks until interrupted — it never
    writes a report and, alone among these flags, needs no ``--now``.
    """
    parser = argparse.ArgumentParser(description="Write learning-loop reports")
    parser.add_argument("--root-dir", type=Path, default=Path("."))
    parser.add_argument("--now", type=_parse_injected_now, default=None)
    parser.add_argument("--window-days", type=int, default=DEFAULT_WINDOW_DAYS)
    parser.add_argument("--html", nargs="?", const="", metavar="PATH")
    parser.add_argument("--no-markdown", action="store_true")
    parser.add_argument(
        "--serve", type=int, nargs="?", const=DEFAULT_SERVE_PORT, metavar="PORT"
    )
    args = parser.parse_args(argv)

    if args.serve is not None:
        serve_dashboard(port=args.serve, dashboard_root=args.root_dir)
        return 0

    if args.now is None:
        parser.error("--now is required")
    if args.no_markdown and args.html is None:
        parser.error("--no-markdown requires --html")

    if not args.no_markdown:
        print(write_weekly_report(args.root_dir, now=args.now, window_days=args.window_days))
    if args.html is not None:
        if __package__:
            from . import learning_report_html
        else:
            import learning_report_html  # type: ignore[no-redef]
        if args.html == "-":
            journal = learning_journal.read_journal(args.root_dir)
            board = learning_scoreboard.compute_scoreboard(
                journal, now=args.now, window_days=args.window_days
            )
            baseline = learning_scoreboard.compute_scoreboard(
                journal,
                now=args.now - timedelta(days=args.window_days),
                window_days=args.window_days,
            )
            print(
                learning_report_html.render_html_report(
                    journal, board, baseline, now=args.now, window_days=args.window_days
                ),
                end="",
            )
        else:
            output_path = Path(args.html) if args.html else None
            print(
                learning_report_html.write_html_report(
                    args.root_dir,
                    now=args.now,
                    window_days=args.window_days,
                    output_path=output_path,
                )
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
