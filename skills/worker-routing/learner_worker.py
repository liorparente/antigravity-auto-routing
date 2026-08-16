#!/usr/bin/env python3
"""LearnerWorker: two cadences that turn journal evidence into proposals.

Spec 0004 ticket 22. The component that turns records into changed
behavior — and it is a **worker**, invoked through the standard `(model,
effort, prompt) -> str` seam, never the orchestrator. The proposer and the
approver must always be separate parties; an orchestrator that distills its
own session is both. Every call this module makes into
`risk_tiered_application` is a *proposal*: Tier 1 auto-applies, Tier 2 must
clear the acceptance gate, Tier 3 is held pending human approval, and Tier 4
(the protocol) is unreachable via the closed `LearnedDocument` vocabulary
(`{"memory", "routing_table", "briefs"}`) — it has no member for protocol
files, so no `risk_tiered_application` call can ever target it. Separately,
`learner_worker` itself only ever emits proposals through
`risk_tiered_application` — see `risk_tiered_application.py` for what each
tier means. `test_learner_worker.py`'s
`TestSeamAndSeparationTests.test_module_never_imports_learned_state` asserts
this module's own source never imports `learned_state` at all, so a future
edit that reached around the tiering to call its adoption entry point
directly would fail a test rather than ship quietly.

Two cadences:

- **Session end, light** (`run_session_end_light`). Distills one session's
  journal entries into institutional-memory lessons — extending the
  learn-session flow to mine the content-free journal rather than only chat
  history.
- **Weekly, deep** (`run_weekly_deep`). Computes the scoreboard, runs a batch
  retrospective over the week's tasks, and proposes routing-table updates,
  brief diffs, and memory lessons from the evidence. This is a single
  one-shot `invoke_worker` call, not an `advisory_consultation` dialogue —
  see issue 31 for the open question of whether it should become one.

**This module owns no clock.** `now` is always injected by both cadences'
callers, matching every sibling in this family
(`learning_journal`/`learning_scoreboard`/`learning_report`/
`risk_tiered_application`); cadence itself comes from the existing
scheduler, never from a `datetime.now()` call here.

**This ticket owns the acceptance gate's configuration.** Ticket 18 asks for
a benchmark run "several times — the count is configuration", and
`acceptance_gate.evaluate_proposal` provides that as `trials` /
`score_threshold` keyword arguments over `DEFAULT_TRIAL_COUNT` /
`DEFAULT_SCORE_THRESHOLD`. The gate already has a caller —
`risk_tiered_application.apply_routing_table_update`, which re-exposes those
same two keyword arguments over the same two defaults — but nothing supplied
it config-sourced values before this ticket, deliberately: the gate itself
is a leaf, and a config read inside it would be a second source for a value
its caller already passes down. `_load_acceptance_gate_config` reads
`routing-config.json`'s `acceptance_gate` section the same way
`advisory_consultation._load_dialogue_budget_config` reads
`dialogue_budget.session_dialogue_cap`, and `run_weekly_deep` is the first
caller of `apply_routing_table_update` to hand it those config-loaded
`trials`/`score_threshold` rather than leaving them at their defaults.

**The journal carries no prose (`learning_journal.py`'s own content-freedom
guarantee), so neither does a prompt built from it.** Every prompt this
module renders cites only structured fields already present on a journal
record — task ids, model ids, verdicts, counts, scores — never free text.
The worker's own response is where prose enters, and that response is
parsed defensively: `_extract_json_object` never raises on a malformed or
off-topic reply, because an untrusted worker response failing to parse is
expected input here, not a caller bug.
"""
from __future__ import annotations

import json
import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import acceptance_gate
import learning_journal
import learning_report
import learning_scoreboard
import risk_tiered_application
from risk_tiered_application import TierOutcome

# The seam every worker invocation in this repository shares: `(model,
# effort, prompt) -> str`. Declared locally rather than imported from
# `advisory_consultation` — that module pulls in `asyncio` and
# `urllib.request` for reasons that have nothing to do with this one type
# alias, and these files are loaded by path rather than as a package (see
# `learning_journal.py`'s own note on `TASK_ID_RE` for why a cross-module
# import is avoided for a value this cheap to restate).
InvokeWorker = Callable[[str, str, str], str]

_CONFIG_PATH = Path(__file__).resolve().parent / "routing-config.json"

# Re-exported from `learning_scoreboard` — one source, never a second
# literal, matching `learning_report.DEFAULT_WINDOW_DAYS`'s own re-export.
DEFAULT_WINDOW_DAYS: int = learning_scoreboard.DEFAULT_WINDOW_DAYS

# The routed model/effort for each cadence, chosen from `routing-config.json`'s
# own tiers: the light pass is a per-session summarization job — the
# `light_doer` tier's first listed alternative — while the deep weekly run
# is a batch retrospective plus a Tier-2/Tier-3 proposal, closer to the
# `heavy_doer` tier's "new feature, refactoring" weight than a light one.
_SESSION_LIGHT_MODEL = "Codex 5.6 Terra"
_SESSION_LIGHT_EFFORT = "medium"
_WEEKLY_DEEP_MODEL = "Claude Sonnet 5 (Thinking)"
_WEEKLY_DEEP_EFFORT = "high"

# The exact character class `learned_state._CHANGE_ID_RE` and
# `risk_tiered_application._PROPOSAL_ID_RE` both accept
# (`^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$`). `_change_id` below sanitizes a
# caller-supplied `session_id`/`run_id` against this before using it as a
# seed, so a seed containing a character either validator would reject can
# never reach them.
_IDENTIFIER_UNSAFE_RE = re.compile(r"[^A-Za-z0-9_.-]+")

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def _require_aware_now(now: datetime) -> None:
    """Refuse a naive `now` before any worker is invoked.

    Matches every sibling module's guard of the same name
    (`risk_tiered_application._require_aware_now`,
    `learning_scoreboard._require_aware_now`,
    `learning_report._require_aware_now`): a naive value is silently local
    time, and accepting one here would shift a session's journal window by
    the caller's UTC offset with nothing downstream able to detect it.
    """
    if now.tzinfo is None or now.tzinfo.utcoffset(now) is None:
        raise ValueError("now must be a timezone-aware datetime, got a naive value")


def _wire_timestamp(now: datetime) -> str:
    """The wire timestamp shape every sibling module emits, for a prompt's
    human-readable `as_of` line."""
    return now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _compact_timestamp(now: datetime) -> str:
    """A colon-free rendering of `now`, safe to embed in a `change_id`/
    `proposal_id` seed — `_wire_timestamp`'s colons are not in
    `_IDENTIFIER_UNSAFE_RE`'s allowed set."""
    return now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _change_id(prefix: str, *, seed: str, index: int) -> str:
    """A `change_id`/`proposal_id` shaped for `learned_state` and
    `risk_tiered_application`'s validators, built from a caller-supplied or
    derived `seed` (a `session_id`, `run_id`, or `_compact_timestamp`) plus
    `index`, which identifies the proposal's position within the run or
    session that produced it. Every call site today consolidates its inputs
    (e.g. all of a run's memory lessons) into a single proposal and always
    passes `index=0`; the index exists for a future or alternate caller that
    emits multiple distinct proposals from one run and needs each to get its
    own identifier rather than collide on `index=0`.

    Sanitizes rather than trusts the seed: `session_id` and `run_id` are
    validated elsewhere against `learning_journal.TASK_ID_RE` before this
    module ever sees them, but `_compact_timestamp` and any future seed
    source are not, so every seed is passed through the same filter.

    The seed is truncated *before* formatting, not the final string after —
    a trailing `[:128]` on the assembled string can chop off the `-{index}`
    suffix itself when `prefix` and `seed` are already long, which collides
    two different indices into the same identifier. Reserving room for
    `prefix`, both separators, and `index` up front guarantees `-{index}`
    always survives intact.
    """
    safe_seed = _IDENTIFIER_UNSAFE_RE.sub("-", seed).strip("-") or "run"
    max_seed_len = 128 - len(prefix) - len(str(index)) - 2
    truncated_seed = safe_seed[: max(1, max_seed_len)].rstrip("-")
    return f"{prefix}-{truncated_seed}-{index}"


def _extract_json_object(response: str) -> dict[str, object] | None:
    """Best-effort extraction of one JSON object from a worker's free-text
    response. Never raises.

    Tries, in order: the contents of a fenced ```json (or bare ```) code
    block; the whole response parsed as JSON; the substring from the
    response's first `{` to its last `}`. The first candidate that parses to
    a JSON object wins. Returns `None` when nothing parses — a worker that
    refuses, apologizes, or replies with prose is expected input here, not a
    bug in the caller, so every cadence below must treat `None` as "no
    proposal this run" rather than crash.
    """
    candidates: list[str] = []
    fence_match = _JSON_FENCE_RE.search(response)
    if fence_match is not None:
        candidates.append(fence_match.group(1).strip())
    candidates.append(response.strip())
    start = response.find("{")
    end = response.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidates.append(response[start : end + 1])

    for candidate in candidates:
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _string_tuple(value: object) -> tuple[str, ...]:
    """Coerce a parsed JSON value into a tuple of non-empty, stripped
    strings. Never raises: a worker response's shape is untrusted input, so
    a malformed `memory_lessons` entry is dropped rather than crashing the
    whole pass.
    """
    if not isinstance(value, list):
        return ()
    return tuple(entry.strip() for entry in value if isinstance(entry, str) and entry.strip())


def _optional_string(value: object) -> str | None:
    """`value` as a stripped, non-empty string, or `None` — for a worker
    response field that is either absent, the wrong type, or blank, all of
    which mean "the worker proposed nothing here."
    """
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _validate_trials(value: object, field_name: str = "trials") -> None:
    """A strictly positive, non-`bool` `int` — the shape of a trial count.

    Mirrors `acceptance_gate._validate_trials` rather than importing it: a
    private name is never imported across these modules (see `InvokeWorker`'s
    docstring above on why this file restates rather than reaches into a
    sibling's internals). `bool` is an `int` subclass, so `isinstance` alone
    would admit `trials=True`.
    """
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(  # noqa: TRY004 - one rejection contract; see learning_journal.py
            f"{field_name} must be an integer, got {type(value).__name__}"
        )
    if value <= 0:
        raise ValueError(f"{field_name} must be > 0, got {value!r}")


def _validate_score_threshold(value: object, field_name: str = "score_threshold") -> None:
    """A finite, non-`bool` number — the shape of a bar every trial must
    clear. Mirrors `acceptance_gate._validate_score_threshold`; see
    `_validate_trials` above for why this is restated rather than imported.
    """
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(  # noqa: TRY004 - one rejection contract; see learning_journal.py
            f"{field_name} must be a number, got {type(value).__name__}"
        )
    if not math.isfinite(value):
        raise ValueError(f"{field_name} must be finite, got {value!r}")


def _load_acceptance_gate_config(config_path: Path) -> tuple[int, float]:
    """Read `trials`/`score_threshold` from `config_path`'s `acceptance_gate`
    section, falling back to `acceptance_gate.DEFAULT_TRIAL_COUNT` /
    `DEFAULT_SCORE_THRESHOLD` when the section (or either key) is absent —
    the same pattern, including the no-try/except contract, as
    `advisory_consultation._load_dialogue_budget_config`: production always
    calls this with the default `_CONFIG_PATH`, which is checked into the
    repo, so a missing or malformed `config_path` is a genuine caller
    mistake left to raise loudly rather than be swallowed.

    Validated with this module's own `_validate_trials`/
    `_validate_score_threshold` before any coercion — a raw `True` for
    `trials` or a raw `float` would otherwise survive `int()`/`float()`
    silently (`bool` is an `int` subclass, and `int(1.9)` truncates rather
    than rejects), which is exactly the class of malformed config this
    module's own docstring on `_IDENTIFIER_UNSAFE_RE`-adjacent validation
    elsewhere refuses to let through unchecked.
    """
    with open(config_path, "r", encoding="utf-8") as stream:
        config = json.load(stream)
    section = config.get("acceptance_gate", {})
    raw_trials = section.get("trials", acceptance_gate.DEFAULT_TRIAL_COUNT)
    raw_threshold = section.get("score_threshold", acceptance_gate.DEFAULT_SCORE_THRESHOLD)
    _validate_trials(raw_trials, "trials")
    _validate_score_threshold(raw_threshold, "score_threshold")
    return raw_trials, float(raw_threshold)


def _in_window(ts: datetime, *, window_start: datetime, now: datetime) -> bool:
    """Half-open on the left, closed on the right — the same convention
    `learning_scoreboard._in_window` and `learning_report._in_window` use, so
    this module's prompts describe exactly the span those two already
    compute metrics and reports over.
    """
    return window_start < ts <= now


def _prefix_cut(records: tuple[Any, ...], *, now: datetime) -> tuple[Any, ...]:
    """The `<= now` prefix of one journal family — mirrors
    `learning_scoreboard._prefix_cut`'s shape (`Any`-typed for the same
    reason: one implementation shared across five otherwise-unrelated record
    types) without importing a private name across the module boundary.
    """
    return tuple(
        record
        for record in records
        if learning_journal.parse_wire_timestamp(record.timestamp) <= now
    )


def _window_cut(records: tuple[Any, ...], *, window_start: datetime, now: datetime) -> tuple[Any, ...]:
    """The `(window_start, now]` window of one journal family — mirrors
    `_prefix_cut`'s shape (`Any`-typed for the same "one implementation
    shared across otherwise-unrelated record types" reason) so
    `run_weekly_deep` doesn't repeat the same three-line filter once per
    family.
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


def _prefix_cut_journal(root_dir: Path, *, now: datetime) -> learning_journal.JournalRead:
    """Read the journal beneath `root_dir` and cut every family to its
    `<= now` prefix — Rule 1 of `learning_scoreboard.py`'s own contract,
    restated here because this module reads records directly (for prompt
    text) rather than only through `compute_scoreboard`.
    """
    journal = learning_journal.read_journal(root_dir)
    return learning_journal.JournalRead(
        worker_executions=_prefix_cut(journal.worker_executions, now=now),
        outcomes=_prefix_cut(journal.outcomes, now=now),
        dialogues=_prefix_cut(journal.dialogues, now=now),
        compliance=_prefix_cut(journal.compliance, now=now),
        replay_benchmarks=_prefix_cut(journal.replay_benchmarks, now=now),
        unreadable_lines=journal.unreadable_lines,
        unknown_kind_lines=journal.unknown_kind_lines,
    )


def _session_journal(
    root_dir: Path,
    *,
    now: datetime,
    session_id: str | None,
    run_id: str | None,
) -> learning_journal.JournalRead:
    """The `<= now` journal prefix, optionally narrowed to one session and/or
    one run.

    `session_id` and `run_id` each narrow *independently*, by the field they
    actually carry — never joined or intersected across the two. `session_id`
    is the only carried identity `compliance`
    (`learning_journal.ComplianceRecord.session_id`) has, so an explicit
    `session_id` narrows *only* that family, directly by its `session_id`
    field. `run_id`, when given, narrows all five families
    (`worker_executions`, `outcomes`, `dialogues`, `compliance`,
    `replay_benchmarks`) directly by their own `run_id` field — `compliance`
    is narrowed by both filters when both are given, since it is the one
    family that carries both identities.

    Deliberately not a join: `worker_executions`/`outcomes`/`dialogues`/
    `replay_benchmarks` have no `session_id` field, and deriving one for them
    by looking up the `run_id`s that happen to appear on a session's
    `compliance` records would treat `run_id` as if it were a single global
    namespace shared across every audit surface. It is not — a `run_id`
    collision across disjoint producers would silently pull in another
    session's worker records. A caller that wants a session's worker/outcome/
    dialogue evidence narrowed too must pass the matching `run_id` alongside
    `session_id`; this function does not infer it.
    """
    journal = _prefix_cut_journal(root_dir, now=now)

    worker_executions = journal.worker_executions
    outcomes = journal.outcomes
    dialogues = journal.dialogues
    compliance = journal.compliance
    replay_benchmarks = journal.replay_benchmarks

    if session_id is not None:
        compliance = tuple(r for r in compliance if r.session_id == session_id)
    if run_id is not None:
        worker_executions = tuple(r for r in worker_executions if r.run_id == run_id)
        outcomes = tuple(r for r in outcomes if r.run_id == run_id)
        dialogues = tuple(r for r in dialogues if r.run_id == run_id)
        compliance = tuple(r for r in compliance if r.run_id == run_id)
        replay_benchmarks = tuple(r for r in replay_benchmarks if r.run_id == run_id)

    return learning_journal.JournalRead(
        worker_executions=worker_executions,
        outcomes=outcomes,
        dialogues=dialogues,
        compliance=compliance,
        replay_benchmarks=replay_benchmarks,
        unreadable_lines=journal.unreadable_lines,
        unknown_kind_lines=journal.unknown_kind_lines,
    )


def _render_session_light_prompt(
    journal: learning_journal.JournalRead,
    *,
    now: datetime,
    session_id: str | None,
    run_id: str | None,
) -> str:
    """Build the light pass's prompt from structured journal fields only —
    see the module docstring's content-freedom note.
    """
    lines = [
        "You are the LearnerWorker's session-end light pass.",
        "Distill the session evidence below into institutional-memory lessons.",
        "",
        f"as_of: {_wire_timestamp(now)}",
    ]
    if session_id is not None:
        lines.append(f"session_id: {session_id}")
    if run_id is not None:
        lines.append(f"run_id: {run_id}")
    lines.append("")

    lines.append(f"worker_executions: {len(journal.worker_executions)}")
    for record in journal.worker_executions:
        lines.append(
            f"- task={record.task.task_id} model={record.model_id} effort={record.effort} "
            f"success={record.success} duration_ms={record.duration_ms} "
            f"cost_usd={record.cost_estimate_usd} retry_count={record.retry_count}"
        )
    lines.append("")

    lines.append(f"outcomes: {len(journal.outcomes)}")
    for outcome_record in journal.outcomes:
        lines.append(
            f"- task={outcome_record.task.task_id} ground_truth={outcome_record.ground_truth} "
            f"verdict={outcome_record.verdict}"
        )
    lines.append("")

    lines.append(f"dialogues: {len(journal.dialogues)}")
    for dialogue in journal.dialogues:
        lines.append(
            f"- task={dialogue.task.task_id} occasion={dialogue.occasion} "
            f"topology={dialogue.topology} rounds={dialogue.rounds_run} "
            f"degraded={dialogue.degraded}"
        )
    lines.append("")

    lines.append(f"compliance: {len(journal.compliance)}")
    for comp in journal.compliance:
        lines.append(
            f"- session={comp.session_id} violations={comp.violation_count} "
            f"drift={comp.declaration_drift_count} codes={comp.issue_codes}"
        )
    lines.append("")

    lines.append(
        "Respond with a fenced ```json code block containing exactly one object: "
        '{"memory_lessons": ["<lesson 1>", "<lesson 2>", ...]}. Each lesson must be a '
        "short, generalizable institutional-memory statement — never a verbatim task "
        "description, prompt, or file path. Return an empty list if nothing is worth "
        "keeping."
    )
    return "\n".join(lines)


@dataclass(frozen=True)
class SessionEndResult:
    """The light pass's outcome: what it proposed, what happened to each
    proposal, and the worker's raw reply for audit."""

    lessons: tuple[str, ...]
    outcomes: tuple[TierOutcome, ...]
    raw_response: str


def run_session_end_light(
    invoke_worker: InvokeWorker,
    *,
    root_dir: Path,
    now: datetime,
    session_id: str | None = None,
    run_id: str | None = None,
    model: str = _SESSION_LIGHT_MODEL,
    effort: str = _SESSION_LIGHT_EFFORT,
) -> SessionEndResult:
    """Distill one session's journal entries into memory lessons and
    auto-apply them (Tier 1).

    Always invokes the worker, even over an empty journal — the worker, not
    this module, decides whether a quiet session yields a lesson; that
    decision must be observable through `invoke_worker`
    (spec 0004 ticket 22's "every learner run is observable as a worker
    invocation"), not short-circuited here. A response this module cannot
    parse into JSON, or whose `memory_lessons` field is malformed, yields no
    lessons rather than raising — see `_extract_json_object` and
    `_string_tuple`.

    Every lesson from one run is folded into a single `apply_memory_lesson`
    call, never one call per lesson: `learned_state.adopt` replaces the
    current memory version wholesale, so applying lessons one at a time
    would have each call overwrite the previous lesson rather than add to
    it, leaving only the last lesson of a multi-lesson run actually adopted.

    This consolidation is intra-run only: it merges the several lessons *one*
    call to this function proposes into one `apply_memory_lesson` call. It
    does not accumulate lessons *across* separate runs — each call to this
    function still replaces `learned_state`'s current memory version
    wholesale via the tier it goes through, the same way a second run's
    lessons would replace a first run's rather than merge with them. Whether
    and how to accumulate memory lessons across multiple sessions/runs is an
    open design question, not yet addressed here — see issue 33.
    """
    _require_aware_now(now)
    journal = _session_journal(root_dir, now=now, session_id=session_id, run_id=run_id)
    prompt = _render_session_light_prompt(
        journal, now=now, session_id=session_id, run_id=run_id
    )
    raw_response = invoke_worker(model, effort, prompt)

    payload = _extract_json_object(raw_response)
    lessons = _string_tuple(payload.get("memory_lessons")) if payload is not None else ()

    seed = session_id or run_id or _compact_timestamp(now)
    outcomes: tuple[TierOutcome, ...] = ()
    if lessons:
        consolidated = "\n".join(f"- {lesson}" for lesson in lessons)
        outcome = risk_tiered_application.apply_memory_lesson(
            consolidated,
            root_dir=root_dir,
            now=now,
            change_id=_change_id("learner-light", seed=seed, index=0),
        )
        outcomes = (outcome,)
    return SessionEndResult(lessons=lessons, outcomes=outcomes, raw_response=raw_response)


def _metric_repr(metric: learning_scoreboard.Metric) -> str:
    """One metric rendered for a prompt — mirrors
    `learning_report._value_repr`'s shape without importing a private name
    across the module boundary (this module's own convention; see
    `InvokeWorker`'s docstring above)."""
    if isinstance(metric, learning_scoreboard.MetricNoData):
        return "no data"
    return f"{metric.value:.4g} (n={metric.sample_size})"


def _render_weekly_deep_prompt(
    comparison: learning_scoreboard.ScoreboardComparison,
    journal: learning_journal.JournalRead,
    *,
    now: datetime,
    window_days: int,
) -> str:
    """Build the deep run's prompt from the scoreboard comparison plus the
    week's windowed journal records — structured fields only, same
    content-freedom rule as the light pass.
    """
    lines = [
        "You are the LearnerWorker's weekly deep run.",
        (
            "Run a batch retrospective over the week's tasks using the scoreboard and "
            "journal evidence below, then propose updates."
        ),
        "",
        f"as_of: {_wire_timestamp(now)}",
        f"window_days: {window_days}",
        "",
        "scoreboard (name, direction, status, current, baseline):",
    ]
    for change in comparison.changes:
        lines.append(
            f"- {change.name} ({change.direction}): {change.status} — "
            f"current={_metric_repr(change.current)} baseline={_metric_repr(change.baseline)}"
        )
    lines.append("")

    lines.append(f"worker_executions this window: {len(journal.worker_executions)}")
    for record in journal.worker_executions:
        lines.append(
            f"- task={record.task.task_id} run={record.run_id} model={record.model_id} "
            f"effort={record.effort} success={record.success} "
            f"duration_ms={record.duration_ms} cost_usd={record.cost_estimate_usd}"
        )
    lines.append("")

    lines.append(f"outcomes this window: {len(journal.outcomes)}")
    for outcome_record in journal.outcomes:
        lines.append(
            f"- task={outcome_record.task.task_id} ground_truth={outcome_record.ground_truth} "
            f"verdict={outcome_record.verdict}"
        )
    lines.append("")

    lines.append(f"dialogues this window: {len(journal.dialogues)}")
    for dialogue in journal.dialogues:
        last_verdict = dialogue.rounds[-1].verdict if dialogue.rounds else "none"
        lines.append(
            f"- task={dialogue.task.task_id} occasion={dialogue.occasion} "
            f"topology={dialogue.topology} rounds={dialogue.rounds_run} "
            f"last_verdict={last_verdict}"
        )
    lines.append("")

    lines.append(
        "Respond with a fenced ```json code block containing exactly one object with "
        "any of these optional keys: "
        '{"routing_table_update": "<full new routing-config.json content>", '
        '"brief_update": "<full new brief content>", '
        '"memory_lessons": ["<lesson>", ...], '
        '"retrospective_summary": "<one paragraph>"}. '
        "Omit a key entirely rather than proposing a change you are not confident in."
    )
    return "\n".join(lines)


@dataclass(frozen=True)
class WeeklyDeepResult:
    """The deep run's outcome: what happened to each tiered proposal, the
    retrospective summary, the written report's path, and the worker's raw
    reply for audit."""

    report_path: Path
    routing_outcomes: tuple[TierOutcome, ...]
    brief_outcomes: tuple[TierOutcome, ...]
    memory_outcomes: tuple[TierOutcome, ...]
    retrospective_summary: str
    raw_response: str


def run_weekly_deep(
    invoke_worker: InvokeWorker,
    *,
    root_dir: Path,
    now: datetime,
    runner: Callable[[], float],
    config_path: Path = _CONFIG_PATH,
    window_days: int = DEFAULT_WINDOW_DAYS,
    run_id: str | None = None,
    model: str = _WEEKLY_DEEP_MODEL,
    effort: str = _WEEKLY_DEEP_EFFORT,
) -> WeeklyDeepResult:
    """Compute the scoreboard, run the batch retrospective, and route every
    proposal the worker returns through its tier — routing-table updates
    through the acceptance gate (Tier 2), brief diffs held pending (Tier 3),
    memory lessons auto-applied (Tier 1) — then write the weekly report.

    `run_id` names this learner run's own identity (used only to seed
    `change_id`/`proposal_id` derivation, for traceability across the
    proposals one run produces); it does not narrow which journal evidence
    the retrospective reads; the batch retrospective's whole point is to
    read every task's evidence for the window, not one run's.

    The acceptance gate's `trials`/`score_threshold` are read from
    `config_path` via `_load_acceptance_gate_config` — this ticket's own
    requirement — rather than left at `acceptance_gate.py`'s module
    defaults.

    See Issue 32 for the open question of forwarding this run's `run_id`
    through `risk_tiered_application.apply_routing_table_update` to the
    `ReplayBenchmarkRecord`s the acceptance gate produces — currently
    unwired.
    """
    _require_aware_now(now)
    trials, score_threshold = _load_acceptance_gate_config(config_path)

    journal = _prefix_cut_journal(root_dir, now=now)
    current_board = learning_scoreboard.compute_scoreboard(
        journal, now=now, window_days=window_days
    )
    window_start = now - timedelta(days=window_days)
    baseline_board = learning_scoreboard.compute_scoreboard(
        journal, now=window_start, window_days=window_days
    )
    comparison = learning_scoreboard.compare_scoreboards(baseline_board, current_board)

    windowed_journal = learning_journal.JournalRead(
        worker_executions=_window_cut(
            journal.worker_executions, window_start=window_start, now=now
        ),
        outcomes=_window_cut(journal.outcomes, window_start=window_start, now=now),
        dialogues=_window_cut(journal.dialogues, window_start=window_start, now=now),
        compliance=_window_cut(journal.compliance, window_start=window_start, now=now),
        replay_benchmarks=_window_cut(
            journal.replay_benchmarks, window_start=window_start, now=now
        ),
        unreadable_lines=journal.unreadable_lines,
        unknown_kind_lines=journal.unknown_kind_lines,
    )

    prompt = _render_weekly_deep_prompt(
        comparison, windowed_journal, now=now, window_days=window_days
    )
    raw_response = invoke_worker(model, effort, prompt)
    payload = _extract_json_object(raw_response) or {}

    retrospective_summary = _optional_string(payload.get("retrospective_summary")) or ""
    seed = run_id or _compact_timestamp(now)

    routing_outcomes: tuple[TierOutcome, ...] = ()
    routing_update = _optional_string(payload.get("routing_table_update"))
    if routing_update is not None:
        routing_outcomes = (
            risk_tiered_application.apply_routing_table_update(
                routing_update,
                root_dir=root_dir,
                now=now,
                runner=runner,
                change_id=_change_id("learner-deep-routing", seed=seed, index=0),
                trials=trials,
                score_threshold=score_threshold,
            ),
        )

    brief_outcomes: tuple[TierOutcome, ...] = ()
    brief_update = _optional_string(payload.get("brief_update"))
    if brief_update is not None:
        brief_outcomes = (
            risk_tiered_application.submit_brief_proposal(
                brief_update,
                root_dir=root_dir,
                now=now,
                proposal_id=_change_id("learner-deep-brief", seed=seed, index=0),
            ),
        )

    lessons = _string_tuple(payload.get("memory_lessons"))
    memory_outcomes: tuple[TierOutcome, ...] = ()
    if lessons:
        consolidated = "\n".join(f"- {lesson}" for lesson in lessons)
        memory_outcomes = (
            risk_tiered_application.apply_memory_lesson(
                consolidated,
                root_dir=root_dir,
                now=now,
                change_id=_change_id("learner-deep-memory", seed=seed, index=0),
            ),
        )

    adopted_entries: list[str] = []
    for ro in routing_outcomes:
        if ro.status == "applied":
            adopted_entries.append(f"routing_table: change_id={ro.change_id or 'none'}")
    for mo in memory_outcomes:
        if mo.status == "applied":
            adopted_entries.append(f"memory: change_id={mo.change_id or 'none'}")

    report_path = learning_report.write_weekly_report(
        root_dir, now=now, window_days=window_days, adopted=tuple(adopted_entries)
    )

    return WeeklyDeepResult(
        report_path=report_path,
        routing_outcomes=routing_outcomes,
        brief_outcomes=brief_outcomes,
        memory_outcomes=memory_outcomes,
        retrospective_summary=retrospective_summary,
        raw_response=raw_response,
    )


__all__ = [
    "DEFAULT_WINDOW_DAYS",
    "InvokeWorker",
    "SessionEndResult",
    "WeeklyDeepResult",
    "run_session_end_light",
    "run_weekly_deep",
]
