#!/usr/bin/env python3
"""LearningJournal: the content-free signal stream the learning loop reads.

A dedicated, append-only JSONL stream beside the audited routing telemetry,
carrying five record families — worker execution, ground-truth outcomes,
dialogue quality, protocol compliance, and replay-benchmark trials. It exists
so the orchestrator can be judged against what actually happened rather than
against what it declared it would do.

**This is not the routing telemetry stream, and must never become it.**
``.ralph/routing_telemetry.jsonl`` has two writers with a frozen, audited
record contract (see `AdvisoryTelemetryRecord` and
`agent_council.log_routing_telemetry`), including the deliberate `kind`
asymmetry an auditor reads as "council decision". Extending that stream to
carry learning signals would put an evolving schema inside an audited one.
So this module writes its own file, and records correlate across the two
streams on **TaskIdentity** (`task_id`) instead — that join is the whole
reason `task_id` here is validated against the exact pattern
`agent_council.TASK_ID_RE` accepts, and against nothing else (see
`TASK_ID_RE` and `_validate_carried_identifier`).

**Two identifier vocabularies, and a record is keyed on both.**
`task_id` names *what* was worked on and is deliberately stable across
repeats of the same task (`advisory_consultation._default_task_id` digests
the task text). `run_id` names *which attempt* a record belongs to, and is
fresh per execution. Without the second, two runs of one task collapse into
one identity: their costs sum as if one run, and an outcome grading the
second joins the first as well — rework, the very thing the spec's
efficiency metric asks for, becomes unobservable. Every family carries
`run_id` optionally; see `_validate_run_id` for what a consumer may and may
not conclude from its absence.

**Content-freedom is structural, not editorial.** There is no free-text field
anywhere in this module, and no field a caller can fill with free text.
Every field is a number, a boolean, a value from an enumerated vocabulary, a
`TaskLabel`, or a string that must satisfy a pattern; `kind` is not a field
at all but a per-family class constant (`KIND`), so no caller can name a
record family after the task it describes. All of it is enforced at
*runtime*, because `Literal`, `bool`, and `int` annotations are erased before
any value from a parsed log or a worker response reaches a record — an
annotation alone documents the intent without enforcing it, which is exactly
how a "typed" field becomes a leak path. A record carrying a task
description, a prompt, a file path, or a matched secret value raises
`ValueError` at construction — a reviewer who forgets the rule cannot write
one anyway. Where learning genuinely needs content, the learner reads the
existing content-bearing surfaces (transcripts, signed decision records)
locally under their own rules; the journal never becomes a second one.

The `kind` values are snake_case (`worker_execution`, `dialogue_quality`)
while the spec's prose names those families "worker-execution" and
"dialogue-quality"; the wire form deliberately follows the repo's existing
precedent — `AdvisoryTelemetryRecord`'s `kind="advisory_consultation"` — not
the prose, so a reader parsing both streams sees one convention.

Two deliberately different failure modes live here:

- A malformed record **raises**. It is a programming error at the call site
  — the value being journaled was never fit to journal — and it must be loud,
  in exactly the spirit of `run_advisory_consultation_debate`'s `max_rounds`
  check.
- A failed **write** returns an error string and never raises, matching
  `advisory_consultation._write_telemetry_record`. A broken disk must never
  take down the operation the journal was merely observing.

**What "loud" means for a writer that is observing something else.** Two of
this module's three writers do not exist to journal: they exist to run a
worker (`production_invoker.make_journaled_invoke_worker`) or to audit a
session (`routing_check._persist_compliance_record`), and journal as a side
effect. For them, raising on a malformed record would break the very
operation the journal is not permitted to affect — so they catch it and
**report** it instead, through a returned message or an injected sink. That
is a different mechanism from raising, not a weaker rule: the requirement is
that a call-site bug is never silent, and silence — an `except Exception:
pass` that discards even `append_journal_record`'s returned message — is the
one handling no writer here may choose. A writer that a caller invokes
*directly* to record something (`learning_outcomes`) has no such conflict
and lets the `ValueError` propagate, which is this contract's plain form.

Callers inject `root_dir` and every path is derived from it, so the journal
is fully exercisable offline against a temporary directory. That injected
root is this module's only seam; the spec's other two (the worker callable
and the benchmark runner) belong to later modules and must not sprout here.
"""
from __future__ import annotations

import dataclasses
import fcntl
import json
import math
import re
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar, Literal, get_args, get_type_hints

# The journal lives beside `routing_telemetry.jsonl` under the same `.ralph`
# directory — close enough for an operator to find both, separate enough that
# the audited contract stays frozen while this schema evolves. Never merge the
# two files: see this module's docstring.
JOURNAL_RELATIVE_PATH = Path(".ralph") / "learning_journal.jsonl"

# Mirrors `agent_council.TASK_ID_RE` character-for-character rather than
# importing it: importing `agent_council` drags in `urllib.request` and
# `asyncio`, and these files are loaded by path rather than as a package, so
# the import would need a `sys.path` hack — the same reasoning
# `advisory_consultation` documents on its `SENSITIVITY_MARKERS` and
# `_append_jsonl_locked`. `test_routing.py` asserts the two patterns are
# identical, so they cannot drift.
#
# Matching that pattern exactly is not cosmetic: every `task_id` the council
# accepts must be journal-writable, or the cross-stream join silently loses
# records whose id this module rejected. Widening it is equally forbidden —
# the pattern is also what makes prose structurally unwritable here, since a
# task description contains spaces and a file path contains slashes, and
# neither can match.
#
# "Exactly" also means *no additional gate on a task_id* — see
# `_validate_carried_identifier`. This pattern is the whole of the contract
# for that field, because it is the whole of the contract
# `agent_council._task_id` applies before writing the same id to
# `.ralph/routing_telemetry.jsonl`. The same pattern, and the same "no
# additional gate", governs every other identifier this module *receives*
# rather than composes — `session_id`, `run_id`.
TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")

# The wire timestamp format `agent_council.log_routing_telemetry` and
# `advisory_consultation._build_telemetry_record` both emit. Pinned as a
# pattern so `timestamp` is a constrained field like every other string here,
# and so a journal record and a telemetry record for the same moment sort and
# compare identically.
TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

# The audit's issue codes (`DEC-01`, `LOG-01`, `WARN-02`, ...). Deliberately a
# shape, not an enumeration of today's codes: `routing_check.py` owns the code
# list and adds to it, and a journal that rejected a newly-added code would
# drop exactly the violations most worth trending. The shape is still narrow
# enough that no message text, path, or secret can pass as a code.
ISSUE_CODE_RE = re.compile(r"^[A-Z]{2,6}-\d{2}$")

# Splits an identifier — and a sensitivity marker — into comparable tokens.
# See `_identifier_sensitivity_marker` for why the comparison is by token
# rather than by substring.
_TOKEN_SEPARATOR_RE = re.compile(r"[^A-Za-z0-9]+")

# Mirrors `advisory_consultation.SENSITIVITY_MARKERS`, which itself mirrors
# `agent_council.SENSITIVE_PATTERNS`, for the same by-path-not-by-package
# reason given on `TASK_ID_RE`. `test_routing.py` asserts this tuple is a
# superset of both, so the three cannot silently diverge.
#
# Here the markers guard identifiers rather than task text, and they are
# matched on token boundaries — see `_identifier_sensitivity_marker`, and do
# not "simplify" it back into the plain substring scan the other two modules
# use. That scan is correct for prose and catastrophic here: `"task-1"`
# contains `"sk-"`, so a substring check rejects `task-<digest>` — the exact
# id format `agent_council._task_id` generates — and every record whose
# identifier merely resembled a credential would be silently refused.
#
# Which fields face this gate is itself a decision, and the line is
# *composed descriptor* vs *carried identifier*, not "string that looks like
# an identifier" (see `_validate_carried_identifier` for the other half).
# Only `model_id` and `model_family` face it: a caller builds those two out of
# its own vocabulary, so the check stays knowingly over-broad there, since a
# caller renames a descriptor in seconds while a credential written into a
# stream a learner later mines cannot be un-leaked. `task_id`, `session_id`,
# and `run_id` do not face it. Each names a thing that already exists and was
# already named elsewhere — a task the audited telemetry stream has accepted,
# a conversation directory, an execution — so refusing one here un-names
# nothing and only drops the record.
#
# That distinction was learned the expensive way twice. `task_id` was gated by
# reflex and fixed; `session_id` sat behind the same gate unnoticed until a
# second review, which is why the rule is now stated as a rule about *where a
# string came from* rather than a list of field names to check against.
SENSITIVITY_MARKERS = (
    "AGY_CALIBRATION_SECRET",
    "api_key",
    "sk-",
    "bearer ",
    "BEGIN PRIVATE KEY",
    "password",
    "secret",
    "[SENSITIVE]",
)

# The coarse task-type vocabulary permitted on a normal task. Enumerated, not
# free text, and that is the entire defense: "bugfix" tells a learner which
# work classes route badly, while "fix the login 500 for the ACME account"
# would put the task itself in the journal. A caller needing a class not
# listed here adds it to this tuple in a reviewed change — which is precisely
# the human checkpoint an open string field would skip.
TaskType = Literal[
    "bugfix",
    "refactor",
    "feature",
    "docs",
    "test",
    "chore",
    "research",
    "review",
]
TASK_TYPE_TAGS: frozenset[str] = frozenset(get_args(TaskType))

# Mirrors `agent_council.VALID_EFFORTS`; pinned against it by `test_routing.py`.
EffortLevel = Literal["low", "medium", "high", "ultra"]
VALID_EFFORTS: frozenset[str] = frozenset(get_args(EffortLevel))

# Which ground truth an outcome record grades. Kept as four distinct kinds
# rather than one "result" field because they become known at different times,
# from different graders, and a learner that cannot tell a failing test from a
# rejected plan cannot tell a bad worker from a bad plan.
#
# Named `GroundTruth`, not `OutcomeSignal`, because CONTEXT.md — the glossary
# this codebase is driven by, and the authority when the two disagree —
# already spends "signal family" on the journal's five *record* families
# (worker execution, ground-truth outcomes, dialogue quality, protocol
# compliance, replay-benchmark trials). One word at two granularities in a
# glossary-driven codebase is a reader's trap: "signal" would mean the outcome
# family in CONTEXT.md and a subdivision *inside* that one family here. The
# prose in this module already called these four "ground truth" throughout;
# the type now agrees with it.
GroundTruth = Literal["tests", "review", "plan", "stalemate_resolution"]

OutcomeVerdict = Literal[
    "pass",
    "fail",
    "approved",
    "rejected",
    "accepted",
    "planner",
    "critic",
    "human",
]

# Which verdicts each ground truth may carry. A flat verdict vocabulary would
# let `("tests", "planner")` be constructed — a record that reads as if a test
# run picked a stalemate winner. Pairing the vocabularies to their ground
# truths is what makes that unconstructible.
#
# The `stalemate_resolution` verdicts are exactly
# `advisory_consultation._build_stalemate_report`'s three options, in order:
# approve the Planner's architecture, approve the Critic's, escalate to a
# human. Keep them aligned; a fourth option there needs a fourth verdict here.
OUTCOME_VERDICTS: Mapping[GroundTruth, frozenset[str]] = {
    "tests": frozenset({"pass", "fail"}),
    "review": frozenset({"approved", "rejected"}),
    "plan": frozenset({"accepted", "rejected"}),
    "stalemate_resolution": frozenset({"planner", "critic", "human"}),
}

# Spec 0003's four dialogue occasions. Schema only in this ticket — spec
# 0003's machinery is what will write these records.
#
# **Must stay byte-identical to `advisory_consultation.Occasion`, hyphens and
# all.** These are two separately-declared `Literal` aliases in two different
# files describing what is supposed to be one vocabulary; nothing in the type
# system ties them together, so nothing stops them from drifting apart. They
# already did once — this alias used to spell three of the four values with
# underscores while the shipped `Occasion` uses hyphens — and the drift was
# invisible until a future writer did
# `DialogueQualityRecord(occasion=telemetry_record.occasion, ...)` and hit a
# runtime `ValueError` from `_validate_choice` against `DIALOGUE_OCCASIONS`.
# `test_cross_spec_vocabularies_agree` below pins the two aliases equal so
# that failure mode reappears as a test failure instead of a production
# surprise.
DialogueOccasion = Literal["ambiguity", "plan-review", "code-review", "post-mortem"]
DIALOGUE_OCCASIONS: frozenset[str] = frozenset(get_args(DialogueOccasion))

# "pair" is the default cross-family Planner-Critic exchange; "panel" is the
# Complex-task topology of one Planner and two Critics from two other model
# families.
#
# Same cross-file agreement risk as `DialogueOccasion` above, against
# `advisory_consultation.RosterTopology`: currently identical
# (`Literal["pair", "panel"]` on both sides), and just as unguarded by the
# type system if one side ever grows a third topology.
# `test_cross_spec_vocabularies_agree` pins this pair too.
DialogueTopology = Literal["pair", "panel"]
DIALOGUE_TOPOLOGIES: frozenset[str] = frozenset(get_args(DialogueTopology))

# One resolved verdict per round. Mirrors `advisory_consultation.CriticVerdict`
# — including "unparseable" staying distinct from "revise", since a malformed
# response is a broken Critic, not a reasoned objection, and a learner that
# conflated the two would read parser breakage as healthy disagreement.
RoundVerdict = Literal["approved", "revise", "unparseable"]
ROUND_VERDICTS: frozenset[str] = frozenset(get_args(RoundVerdict))


def _utc_timestamp() -> str:
    """The wire timestamp, in the format both telemetry writers already emit."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _tokenize(value: str) -> tuple[str, ...]:
    """Split an identifier into its lowercase alphanumeric words."""
    return tuple(part for part in _TOKEN_SEPARATOR_RE.split(value.lower()) if part)


def _identifier_sensitivity_marker(value: str) -> str | None:
    """Return the `SENSITIVITY_MARKERS` entry `value` embeds, or None.

    **Token-boundary matching, not the substring scan its counterparts in
    `advisory_consultation` and `agent_council` use — deliberately.** Those
    two scan free-form task text, where a substring hit is the right call.
    This one scans identifiers, where it is actively wrong: `"task-1"`
    contains `"sk-"`, so a substring check condemns an ordinary identifier —
    including `task-<digest>`, the form `agent_council._task_id` generates —
    on the strength of two letters that happen to line up. A refused value
    raises rather than writes, so every such false positive is a record the
    learning loop never gets.

    So both sides are tokenized on non-alphanumeric boundaries and a marker
    matches only as a contiguous run of whole tokens: `"api_key"` is caught in
    `"model-api_key-rotation"` (tokens `api`, `key`, adjacent and in order)
    and `"sk-"` is caught in `"sk-live-9f3c"` (token `sk`), while `"task-1"`
    passes because its tokens are `task` and `1`.

    Which fields this gate runs on is a separate question with its own
    answer: the descriptors a caller composes, never an identifier it carried
    in — see `_validate_identifier` and `_validate_carried_identifier`.

    Returns the marker constant itself, never the value it matched against.
    `_validate_identifier` puts the return value in an exception message, and
    echoing the offending string back would leak the very credential the check
    exists to stop — the same rule the other two modules document.
    """
    tokens = _tokenize(value)
    for marker in SENSITIVITY_MARKERS:
        needle = _tokenize(marker)
        if not needle:
            continue
        for start in range(len(tokens) - len(needle) + 1):
            if tokens[start : start + len(needle)] == needle:
                return marker
    return None


# --- the validators every field of every record passes through ---
#
# Each one takes `object`, not the field's annotated type, and that is the
# point rather than an oversight. The annotations are erased at runtime, and
# the values reaching these records come from parsed logs, worker responses,
# and audit reports that no type checker ever saw; a validator typed `str` or
# `int` would be checking a promise nobody kept. Taking `object` also keeps
# the narrowing honest — the rejection branch is genuinely reachable, so
# neither mypy nor a reader is invited to believe the check is dead.
#
# Every one of them raises `ValueError` rather than returning an error string,
# unlike the write path at the bottom of this module. A record built from
# unjournalable values is a bug at the call site, not an environmental
# failure, and a silent skip would leave the learner reasoning from a stream
# with holes in it.
#
# `ValueError` even for a wrong *type*, which is why each type check below
# carries a TRY004 suppression. One exception type is the contract: "this value
# is not journalable" is one condition, whether the value is the wrong type
# or the right type carrying the wrong content, and a caller should not have
# to catch two exceptions to mean one thing. `TypeError` is also what a
# misspelled keyword argument already raises, so splitting the contract would
# leave the tests that assert a field rejects task text unable to tell a
# rejected value from a typo'd field name.


def _require_str(value: object, field_name: str) -> str:
    """Reject a non-string before any pattern check can crash on it."""
    if not isinstance(value, str):
        raise ValueError(  # noqa: TRY004 - one rejection contract; see the note above
            f"{field_name} must be a string, got {type(value).__name__}"
        )
    return value


def _validate_carried_identifier(value: object, field_name: str) -> None:
    """Shape gate and nothing else — for an identifier this module *receives*.

    The validator for `task_id`, `session_id`, and `run_id`: three names for
    things that already existed and were already named somewhere else. A
    `task_id` was accepted by `agent_council._task_id` (whose only check is
    this same pattern) and written to the audited
    `.ralph/routing_telemetry.jsonl` stream before this module saw it. A
    `session_id` is a conversation's directory name, resolved by
    `routing-audit.sh` from `$HOME/.gemini/antigravity/brain` or handed to it
    as an argument. A `run_id` names one execution that has already happened.

    **Deliberately not `_validate_identifier`: no sensitivity-marker gate.**
    None of the three is caller prose that this module gets to adjudicate, and
    re-adjudicating a name cannot un-name the thing. All refusing one can do
    is drop the journal record — for exactly the tasks and sessions whose
    names touch security vocabulary (`secret-rotation`, `api-key-migration`,
    a conversation about rotating a credential), which are the ones whose
    routing quality and protocol discipline are most worth learning from. The
    record is the only casualty, and it is a permanent one: nothing re-audits
    a session whose verdict was refused.

    The invariant, pinned by a test that drives the council's own id
    validation rather than a literal: any task id that can appear in the
    telemetry stream is writable here.

    The shape gate stays, because it is the council's own contract: prose has
    spaces and paths have slashes, so a task description still cannot be
    smuggled in through any of these fields. A caller holding an identifier
    that cannot satisfy it owns deriving one that can — see
    `routing_check._journalable_session_id`, which digests rather than drops.
    The marker gate remains on the descriptors a caller composes here; see
    `_validate_identifier`.
    """
    text = _require_str(value, field_name)
    if not TASK_ID_RE.fullmatch(text):
        raise ValueError(
            f"{field_name} must match {TASK_ID_RE.pattern} — the journal carries "
            "identifiers only, never task text, prompt text, or paths"
        )


def _validate_run_id(value: object, field_name: str) -> None:
    """Validate an optional `run_id`, whose absence is a statement of its own.

    `None` means "this record names no particular run", not "run 0" and not
    "unknown run". A consumer counting rework (distinct runs per `task_id`)
    must therefore treat records without a `run_id` as uncountable rather
    than as one shared run — lumping them together would report a task
    retried five times as retried once. Every writer in this repository
    supplies one; the field is optional so that a future writer with no
    honest run identity says so instead of inventing one.
    """
    if value is None:
        return
    _validate_carried_identifier(value, field_name)


def _validate_identifier(value: object, field_name: str) -> None:
    """Reject any string that is not a bare, secret-free identifier.

    The gate for the identifiers a caller *composes* for a record — `model_id`
    and `model_family` — as opposed to the ones it carries over from
    somewhere that already named them (`_validate_carried_identifier`). Two
    checks, and both matter:

    - `TASK_ID_RE`: no spaces, no slashes, no punctuation beyond `_.-`. A task
      description, a prompt, a log excerpt, and a file path all fail on shape
      alone, before anything has to reason about their meaning.
    - `_identifier_sensitivity_marker`: an identifier that *is* shaped like an
      identifier but embeds a credential (`sk-live-...`) passes the first gate
      and must not pass at all.

    The message names the field and the matched marker constant — never the
    rejected value.
    """
    text = _require_str(value, field_name)
    marker = _identifier_sensitivity_marker(text)
    if marker is not None:
        raise ValueError(
            f"{field_name} matched sensitivity marker '{marker}' and may not be "
            "journaled (the rejected value is deliberately not repeated here)"
        )
    if not TASK_ID_RE.fullmatch(text):
        raise ValueError(
            f"{field_name} must match {TASK_ID_RE.pattern} — the journal carries "
            "identifiers only, never task text, prompt text, or paths"
        )


def _echoable(value: object) -> str:
    """Render `value` for an error message, or redact it if it isn't safe to show.

    A rejection message is the one place a rejected value could still escape
    this module — and the values most likely to be rejected are exactly the
    ones that must not escape, since a caller who passed a task description
    where an effort level belonged would otherwise see it echoed into a log.
    Naming a bad enum value is genuinely useful ("turbo" is one keystroke from
    "ultra"), so rather than redacting unconditionally, the value is shown only
    if it would have been journalable in its own right: short, identifier-
    shaped, and marker-free. Everything else becomes a placeholder, and the
    field name plus the allowed vocabulary still explain the failure.

    A number is always shown — it cannot carry prose — and anything else is
    named by its type only. An arbitrary object's `repr` is not safe to echo:
    the likeliest wrong object to pass here is one holding the task itself.
    """
    if isinstance(value, (bool, int, float)):
        return repr(value)
    if not isinstance(value, str):
        return f"<redacted: {type(value).__name__}>"
    if (
        len(value) <= 32
        and TASK_ID_RE.fullmatch(value)
        and _identifier_sensitivity_marker(value) is None
    ):
        return repr(value)
    return "<redacted: not an identifier>"


def _validate_timestamp(value: object, field_name: str = "timestamp") -> None:
    """Every wire timestamp, whichever field carries it.

    `field_name` is a parameter because more than one field is a timestamp
    now (`ComplianceRecord.session_last_activity`), and a rejection naming
    the wrong field sends a reader to the wrong line.

    Two checks, not one: shape (`TIMESTAMP_RE`) and calendar validity
    (`strptime`). `"2026-99-99T99:99:99Z"` matches the regex — it has the
    right digit counts in the right places — but names no real instant, and
    `strptime` is what catches that a shape check cannot. Both live here,
    at construction, rather than the calendar check living only in
    `parse_wire_timestamp`, because of this module's own rule, stated at the
    top of the file: "A malformed record **raises**. It is a programming
    error at the call site — the value being journaled was never fit to
    journal — and it must be loud." A record built from a
    calendar-invalid timestamp was never fit to journal either, and every one
    of the five record types below calls this function from `__post_init__`
    — so a calendar check that lived only downstream, in
    `parse_wire_timestamp`, would let `WorkerExecutionRecord`,
    `OutcomeRecord`, `DialogueQualityRecord`, `ComplianceRecord`, and
    `ReplayBenchmarkRecord` all construct successfully from a value that
    `read_journal` — this module's own reader — later discards as unreadable.
    A record accepted at construction must not vanish on read.
    """
    text = _require_str(value, field_name)
    if not TIMESTAMP_RE.fullmatch(text):
        raise ValueError(
            f"{field_name} must match {TIMESTAMP_RE.pattern}, got {_echoable(text)}"
        )
    try:
        # DTZ007: the wire format is always UTC ("Z"); parsed value discarded
        # here — this function only validates, `parse_wire_timestamp` returns
        # the parsed `datetime`.
        datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ")  # noqa: DTZ007
    except ValueError as exc:
        raise ValueError(
            f"{field_name} matches {TIMESTAMP_RE.pattern} but names no real "
            f"instant: {_echoable(text)}"
        ) from exc


def parse_wire_timestamp(value: str) -> datetime:
    """Parse a wire timestamp into a timezone-aware UTC `datetime`.

    The wire format is defined once, here (`TIMESTAMP_RE`, `_utc_timestamp`);
    defining it again inside a reader would be the exact drift this function
    exists to prevent.

    Raises `ValueError` on two distinct shapes of bad input: one
    `TIMESTAMP_RE` itself refuses, and one it accepts but the calendar does
    not — `"2026-99-99T99:99:99Z"` matches the regex and `strptime` refuses
    it. Both checks now live in `_validate_timestamp`, which this function
    calls before parsing; see that function for why. It raises rather than
    returning `None` because its two callers want opposite things:
    `read_journal` catches it and counts the line (see that function), while
    a caller reached any other way is holding a value it had no right to
    hold, and `None` would make that failure silent.
    """
    _validate_timestamp(value, "timestamp")
    # DTZ007: the wire format is always UTC ("Z"); made aware below.
    # `_validate_timestamp` above already proved this parses cleanly.
    parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")  # noqa: DTZ007
    return parsed.replace(tzinfo=timezone.utc)


def _validate_choice(value: object, allowed: frozenset[str], field_name: str) -> None:
    """Enforce an enumerated vocabulary at runtime.

    `Literal` annotations are erased at runtime, and the values reaching these
    records come from parsed logs and worker responses that no type checker
    ever saw. Without this, `Literal` would be documentation.
    """
    if not isinstance(value, str) or value not in allowed:
        raise ValueError(
            f"{field_name} must be one of {sorted(allowed)}, got {_echoable(value)}"
        )


def _validate_flag(value: object, field_name: str) -> None:
    """Enforce that a boolean field holds an actual boolean.

    `bool` is erased exactly as `Literal` is, so without this a flag is a
    free string field wearing a type annotation — `success="task text leaks
    here"` would construct, serialize, and be read back as a truthy value by
    every consumer of the stream. Nothing but `True` or `False` is a
    meaningful answer to any question this module asks, so nothing else is
    accepted, including the `0`/`1` a JSON reader might have produced.
    """
    if not isinstance(value, bool):
        raise ValueError(  # noqa: TRY004 - one rejection contract; see the note above
            f"{field_name} must be True or False, got {_echoable(value)}"
        )


def _validate_count(value: object, field_name: str) -> None:
    """A non-negative integer, and nothing that merely behaves like one.

    `bool` is a subclass of `int`, so `isinstance` alone would let
    `retry_count=True` through; a count of `True` is a nonsense number that a
    metrics reader would happily average.
    """
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(  # noqa: TRY004 - one rejection contract; see the note above
            f"{field_name} must be an integer, got {_echoable(value)}"
        )
    if value < 0:
        raise ValueError(f"{field_name} must be >= 0, got {value!r}")


def _validate_amount(value: object, field_name: str) -> None:
    """A non-negative, finite number — the shape of a cost.

    Finiteness is not fussiness: `json.dumps` writes `NaN` and `Infinity`,
    which are not JSON, so a single non-finite amount makes the line
    unparseable for the strict reader on the other end of this stream.
    """
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(  # noqa: TRY004 - one rejection contract; see the note above
            f"{field_name} must be a number, got {_echoable(value)}"
        )
    if not math.isfinite(value):
        raise ValueError(
            f"{field_name} must be finite (a non-finite value is not JSON), "
            f"got {value!r}"
        )
    if value < 0:
        raise ValueError(f"{field_name} must be >= 0, got {value!r}")


def _validate_tuple(value: object, field_name: str) -> tuple[object, ...]:
    """Reject anything but a tuple, a `str` most of all.

    A string is iterable, so a per-item loop over `issue_codes="DEC-01 and
    then some prose"` would inspect characters rather than reject the value.
    Requiring the container type first is what makes that unreachable.
    """
    if not isinstance(value, tuple):
        raise ValueError(  # noqa: TRY004 - one rejection contract; see the note above
            f"{field_name} must be a tuple, got {type(value).__name__}"
        )
    return value


@dataclass(frozen=True)
class TaskLabel:
    """The TaskIdentity a journal record hangs on, plus its optional coarse tag.

    A dedicated type rather than two loose fields on each record, because the
    one rule that must never be violated is a rule *about the pair*: a
    sensitivity-halted task carries no tag of any kind. Enforcing that in one
    place beats re-enforcing it in three record types.

    The rule has two independent locks:

    1. `for_halted_task` takes no tag parameter, so there is no argument
       through which a caller could supply one.
    2. `__post_init__` raises if `sensitivity_halted` and `task_type` are both
       set, so bypassing the constructors — direct construction, or a
       `dataclasses.replace` that adds a tag to a halted label — fails too.

    Why the rule exists: a tag is derived from the task text, and the standing
    boundary around a halt (see `advisory_consultation._resolve_task_id` and
    `_render_sensitivity_halt_transcript`) is that *nothing* derived from
    halted task text surfaces anywhere. "bugfix" looks harmless in isolation,
    but a halted task's tag plus its timestamp is a confirmation oracle over
    guessable work, and the journal must not become the first exception to a
    rule the rest of the system keeps.

    `task_id` for a halted task must be the identity the halt already
    resolved — a random one, never a digest of the task text. This module
    cannot verify that (an opaque id is opaque to it too), so the caller owns
    it; passing the halt's own `task_id` is also what keeps this record
    joinable to the halt's telemetry record.

    `task_id` is checked against `agent_council`'s contract and nothing
    further — see `_validate_carried_identifier` for why the journal must not
    add a gate of its own to an id the audited stream has already accepted.
    """

    task_id: str
    task_type: TaskType | None = None
    sensitivity_halted: bool = False

    def __post_init__(self) -> None:
        _validate_carried_identifier(self.task_id, "task_id")
        _validate_flag(self.sensitivity_halted, "sensitivity_halted")
        if self.sensitivity_halted and self.task_type is not None:
            raise ValueError(
                "a sensitivity-halted task carries no task_type tag: a tag is "
                "derived from task text, and nothing derived from halted task "
                "text may be journaled"
            )
        if self.task_type is not None:
            _validate_choice(self.task_type, TASK_TYPE_TAGS, "task_type")

    @classmethod
    def for_task(cls, task_id: str, *, task_type: TaskType | None = None) -> TaskLabel:
        """Label a normal task, optionally with a coarse type tag."""
        return cls(task_id=task_id, task_type=task_type)

    @classmethod
    def for_halted_task(cls, task_id: str) -> TaskLabel:
        """Label a sensitivity-halted task. Deliberately has no tag parameter.

        This signature is lock 1 of the two described on the class: the
        absence of a tag argument is what makes "a halted task carries no tag"
        unexpressible rather than merely discouraged.

        **It has no production caller today, and that is a fact about the
        record families, not an omission to be fixed by wiring one.** A
        sensitivity halt returns from `run_advisory_consultation_debate`
        before any worker is contacted, so there is no invocation for a
        `WorkerExecutionRecord` to describe; it produces no ground truth, so
        there is no `OutcomeRecord`; it runs no round, so there is no
        `DialogueQualityRecord`; `ComplianceRecord` is session-scoped and
        carries no `TaskLabel` at all; and `ReplayBenchmarkRecord` grades the
        evaluator's own fixed task set, never a development task, so it
        carries no `TaskLabel` either — permanently, not just until some
        future ticket. None of the five families this module defines is
        reachable on a halt, so no caller can exist yet without first
        fabricating a record about work that never ran — which is exactly
        what the halt boundary forbids.

        The first caller therefore arrives with the first family that *is*
        reachable on a halt, and it must be built by whichever ticket adds
        that family, not retrofitted here. Until then this constructor's job
        is lock 1: it exists so that the rule holds for that future writer
        the day it appears, rather than depending on its author remembering
        it. `test_routing.py` exercises it directly for the same reason.
        """
        return cls(task_id=task_id, sensitivity_halted=True)

    def to_mapping(self) -> dict[str, object]:
        """Flatten onto the record's top level, omitting an absent tag entirely.

        Flat rather than nested so both streams' records are joined by reading
        one `task_id` key at the top level, with no per-family unwrapping.

        An untagged label emits **no** `task_type` key at all rather than
        `"task_type": null`. `null` would still be a per-record statement
        about a halted task's tag; absence is the stronger claim, and the
        weaker one is not worth the schema uniformity. `sensitivity_halted`
        is always emitted, so an auditor can still tell "halted, therefore
        untaggable" from "normal task the caller chose not to tag" — a
        distinction the trendline needs and one that reveals nothing the
        halt's own telemetry record does not already state.
        """
        mapping: dict[str, object] = {
            "task_id": self.task_id,
            "sensitivity_halted": self.sensitivity_halted,
        }
        if self.task_type is not None:
            mapping["task_type"] = self.task_type
        return mapping


def _validate_task_label(value: object, field_name: str) -> None:
    """Reject anything but a real `TaskLabel` in a record's `task` slot.

    Without this the strictest field in the module is also the easiest to
    bypass: `task` is annotated `TaskLabel`, so nothing at runtime stopped
    `WorkerExecutionRecord(task="fix the login 500 for ACME", ...)` from
    constructing — every validation `TaskLabel.__post_init__` performs is
    skipped by simply not building one.
    """
    if not isinstance(value, TaskLabel):
        raise ValueError(  # noqa: TRY004 - one rejection contract; see the note above
            f"{field_name} must be a TaskLabel (built by TaskLabel.for_task or "
            f"TaskLabel.for_halted_task), got {type(value).__name__}"
        )


def _wire_form(record: Any) -> dict[str, object]:
    """Render any record to its wire mapping, stamped with its family.

    `dataclasses.asdict` rather than a hand-enumerated dict per family, for
    the reason `AdvisoryTelemetryRecord.to_mapping` gives: a hand-written
    field list is duplication that drifts unless a test pins it.

    `kind` is added from the class constant rather than read from a field,
    which is what makes the family discriminator uncounterfeitable: a caller
    has no constructor parameter, no `dataclasses.replace` keyword, and (the
    dataclass being frozen) no assignment through which to name a record
    family after the task it describes. Field order is irrelevant;
    `_append_jsonl_locked` writes with `sort_keys=True`.

    An absent optional field emits **no key** rather than `null`, following
    `TaskLabel.to_mapping`'s reasoning: `"run_id": null` is a per-record
    assertion about a run, while absence is the weaker and truer claim that
    this record names none. It also keeps a reducer's test the plain
    `"run_id" in record`.
    """
    mapping: dict[str, object] = dataclasses.asdict(record)
    mapping["kind"] = record.KIND
    return {key: value for key, value in mapping.items() if value is not None}


def _flatten(record: Any, task: TaskLabel) -> dict[str, object]:
    """Render a task-bearing record to its wire form.

    The nested `task` field is replaced by its flattened keys — see
    `TaskLabel.to_mapping`.
    """
    mapping = _wire_form(record)
    mapping.pop("task")
    mapping.update(task.to_mapping())
    return mapping


@dataclass(frozen=True)
class WorkerExecutionRecord:
    """One worker invocation: what it cost, how it went, and who ran it.

    Today's biggest blind spot — `production_invoker` records nothing — so
    this is the record the routing table will eventually be judged against.

    `model_id` is a routing key, not a display name: "claude-opus-5", never
    "Claude Opus 5 (Thinking)". `_validate_identifier` enforces that by
    refusing spaces and parentheses, and the strictness is deliberate. A
    display string is a rendering concern that varies by harness; a learner
    grouping invocations by it would split one model into several. Callers
    holding a display name map it to its routing key before journaling.

    `cost_estimate_usd` is named an estimate because that is what every
    caller will actually have. Do not rename it to `cost_usd` later without a
    real billing source behind it — the honesty of that name is what stops
    the weekly report from presenting a guess as an invoice.

    **`run_id` is what makes rework countable, and it is not `retry_count`.**
    The spec asks efficiency for "escalation rate, rework counts, cost per
    completed task". `task_id` alone cannot answer any of the three across
    repeats: it is a stable digest of the task text
    (`advisory_consultation._default_task_id`), deliberately identical for
    two consultations of the same task, so their invocations pile into one
    identity — cost sums as though one run happened, and the second run's
    rework reads as the first run's. `run_id` separates them:

    - rework on a task = distinct `run_id`s carrying that `task_id`, minus one
    - cost per completed task = sum over all of them (unchanged, and now
      knowingly a sum over runs rather than an accident)
    - cost per run = sum within one `run_id`

    `retry_count` answers a different question and honestly stays `0`:
    `invoke_worker` retries nothing, so no invocation here is ever attempt
    two *of itself*. A second consultation of the same task is not a retry of
    the first — different prompts, different rounds, its own invocations —
    which is why it needed a new field rather than a repurposed one.
    """

    KIND: ClassVar[str] = "worker_execution"

    task: TaskLabel
    duration_ms: int
    cost_estimate_usd: float
    success: bool
    retry_count: int
    effort: EffortLevel
    model_id: str
    model_family: str
    run_id: str | None = None
    timestamp: str = field(default_factory=_utc_timestamp)

    def __post_init__(self) -> None:
        _validate_task_label(self.task, "task")
        _validate_run_id(self.run_id, "run_id")
        _validate_count(self.duration_ms, "duration_ms")
        _validate_amount(self.cost_estimate_usd, "cost_estimate_usd")
        _validate_flag(self.success, "success")
        _validate_count(self.retry_count, "retry_count")
        _validate_choice(self.effort, VALID_EFFORTS, "effort")
        _validate_identifier(self.model_id, "model_id")
        _validate_identifier(self.model_family, "model_family")
        _validate_timestamp(self.timestamp)

    def to_mapping(self) -> dict[str, object]:
        return _flatten(self, self.task)


@dataclass(frozen=True)
class OutcomeRecord:
    """One ground truth, joined to the decision it grades.

    The `task_id` on this record is deliberately the identity of the *earlier
    decision* — the routing call, the consultation, the plan — not a fresh
    identity for the grading event. That reuse is the entire mechanism by
    which "what we decided" can be checked against "were we right": a
    scoreboard reads one task_id and finds the decision in the telemetry
    stream and its result here.

    `ground_truth` and `verdict` are validated as a pair against
    `OUTCOME_VERDICTS`, so a verdict belonging to another ground truth cannot
    be attached.

    **`run_id` narrows what this record grades, and its absence widens it.**
    With one, the record grades that run of the task; without one, it grades
    the task as a whole. Both are legitimate: a test runner reading its own
    exit code usually knows which run it just graded, while a reviewer
    approving "the ACME fix" may only mean the task. What is illegitimate is
    inventing one — a fabricated `run_id` attaches a real verdict to an
    arbitrary run, which is worse than attaching it to the task, so
    `learning_outcomes` leaves it out rather than guessing (see that module).
    """

    KIND: ClassVar[str] = "outcome"

    task: TaskLabel
    ground_truth: GroundTruth
    verdict: OutcomeVerdict
    run_id: str | None = None
    timestamp: str = field(default_factory=_utc_timestamp)

    def __post_init__(self) -> None:
        _validate_task_label(self.task, "task")
        _validate_run_id(self.run_id, "run_id")
        _validate_choice(self.ground_truth, frozenset(OUTCOME_VERDICTS), "ground_truth")
        _validate_choice(
            self.verdict,
            OUTCOME_VERDICTS[self.ground_truth],
            f"verdict for ground_truth '{self.ground_truth}'",
        )
        _validate_timestamp(self.timestamp)

    def to_mapping(self) -> dict[str, object]:
        return _flatten(self, self.task)


@dataclass(frozen=True)
class DialogueRound:
    """One round of a CriticalDialogue: its verdict and how much it engaged.

    A type per concept, following `AdvisoryDebateRound` in
    `advisory_consultation`, and replacing the pair of synchronized tuples
    (`round_verdicts`, `engagement_counts`) this record used to carry. Those
    needed an equal-length check to stay meaningful, which is the tell: a
    check that has to be written can be forgotten, mis-ordered, or defeated by
    a later field appended to only one of the two tuples. Round three's
    verdict and round three's engagement count are one fact about round three,
    so they are one value, and a mismatch stops being expressible.

    One verdict per round, not per critic. A `panel` round resolves to
    "approved" only when every Critic approved (CriticalDialogue's rule that
    consensus needs explicit approval from both), and to the objection
    otherwise. Per-critic detail stays in the transcript, which is
    content-bearing and governed by its own rules; the journal counts.

    `engagement_count` is the round's **verified-quote count, reduced across its
    Critics by `min`** — never a sum with objections, and never `sum` or `max`
    across Critics. The VerdictContract measure that makes rubber-stamping
    visible: an approval carrying zero of these does not parse as an approval at
    all. Trending it is how the loop notices a Critic going quiet. A count, never
    the objections themselves: the units are quoted from the reviewed artifact,
    so carrying them would carry the artifact.

    **Quotes, not objections.** `advisory_consultation` counts two things per
    Critic — verified quotes, each checked byte-for-byte against the reviewed
    artifact, and numbered objections, which are unverified free text. That
    module already refuses to let the second substitute for the first: an
    APPROVE is honored only when `verified_quote_count >= 1`, explicitly not
    when `verified_quote_count + objection_count >= 1`, because a Critic can
    fabricate ten numbered objections about a plan it never read. Summing them
    here would contradict that rule and the first paragraph above it — a
    fabricated approval with three objections and no quotes would journal
    `engagement_count=3` for a response the parser classified `unparseable` for
    carrying no engagement at all. Objections are not discarded from the system;
    `advisory_consultation.AdvisoryTelemetryRecord.round_verdicts` keeps both
    integers per Critic. They are simply not what this field counts.

    **`min` across a panel's Critics, never `sum` or `max`.** With Critic A at
    five verified quotes and Critic B at zero, `sum` and `max` both report five —
    indistinguishable from a pair round whose sole Critic verified five, so one
    engaged Critic masks a silent one, which is the exact failure this metric
    exists to expose. `min` reports zero: a panel round is only as engaged as its
    least engaged Critic. This is the same reduction the verdict paragraph above
    already uses ("approved" only when every Critic approved), applied to the
    count instead of the label, so the two can never disagree about whether a
    round was engaged. A pair round — and a canary's single-Critic probe —
    reduces over one value and is unaffected.

    The direction of the error is deliberate. Verified quotes are the only
    mechanically checked evidence available, so this count reads low for a Critic
    that objected at length without quoting anything. That is the safe direction:
    it can prompt a look at a Critic that was in fact working, and it can never
    certify a rubber-stamping one as engaged.
    """

    verdict: RoundVerdict
    engagement_count: int

    def __post_init__(self) -> None:
        _validate_choice(self.verdict, ROUND_VERDICTS, "verdict")
        _validate_count(self.engagement_count, "engagement_count")


def _validate_rounds(value: object, field_name: str) -> None:
    """Reject anything but a tuple of real `DialogueRound`s.

    The same hole `_validate_task_label` closes: a two-key dict or a bare
    string would otherwise sail past an annotation that only a type checker
    reads, taking every check `DialogueRound.__post_init__` performs with it.
    """
    for index, item in enumerate(_validate_tuple(value, field_name)):
        if not isinstance(item, DialogueRound):
            raise ValueError(  # noqa: TRY004 - one rejection contract; see the note above
                f"{field_name}[{index}] must be a DialogueRound, got "
                f"{type(item).__name__}"
            )


@dataclass(frozen=True)
class DialogueQualityRecord:
    """How a CriticalDialogue behaved.

    The writer is `advisory_consultation._write_dialogue_quality_record`, one
    record per dialogue, appended at that module's `_result` choke point —
    the same single exit every `AdvisoryTelemetryRecord` already passes
    through. Ownership returned to spec 0004 on 2026-08-13 (spec 0004 ticket
    24): this module owns the contract, `advisory_consultation` owns the one
    caller that fills it in from a real dialogue's result.

    `rounds` is a tuple of `DialogueRound` — one value per round, not parallel
    arrays; see that class for why. It serializes as a list of objects
    (`[{"verdict": ..., "engagement_count": ...}, ...]`), which is the shape a
    metrics reader can index by round without zipping two lists together and
    trusting they are the same length.

    `rounds_run` is a derived property, not a field, for the same reason
    `AdvisoryDebateResult.consensus_reached` is: a record must not be able to
    claim a round count its own round sequence does not back. The count is
    written to the wire form by `to_mapping`, so a reader still sees it —
    and because `to_mapping` writes it unconditionally, `read_journal`
    treats it as required on the wire and rejects a line whose `rounds_run`
    contradicts its own `rounds` list, rather than trusting or silently
    recomputing a value the writer never actually promised to get right on
    the read side (see `_rehydrate_dialogue_quality`). The property itself
    stays the single source of truth for any in-process `DialogueQualityRecord`
    — only a value that arrived over the wire can disagree with it at all.

    Both flags are named for their healthy state being `True`/`False`
    respectively, because a flag whose polarity a reader has to guess is a
    trap in a metrics stream: `degraded` is True when the dialogue ran cheaper
    or shorter than its occasion called for (a budget degradation), and
    `independent` is True when the participants genuinely came from different
    model families.

    **Telling a canary probe's record from a real dialogue's — and why there is
    no `outcome` field.** `canaries_planted` already answers it. The writer sets
    it to 1 exactly when a Critic was actually shown a seeded-flaw fixture and
    returned a verdict, and to 0 for every ordinary dialogue, so
    `canaries_planted == 0` *is* the filter for ordinary dialogue activity and
    `canaries_planted >= 1` marks a probe whose single round's verdict was
    forced by a fixture. An aggregation that skips that filter blends a probe's
    engagement count into real-mission statistics — the identical silent-blend
    `advisory_consultation.AdvisoryTelemetryRecord` warns about for
    `round_verdicts`. A second discriminator field would be a second thing to
    keep in step with the first; this record already carries the answer.
    """

    KIND: ClassVar[str] = "dialogue_quality"

    task: TaskLabel
    occasion: DialogueOccasion
    topology: DialogueTopology
    rounds: tuple[DialogueRound, ...]
    canaries_planted: int = 0
    canaries_caught: int = 0
    degraded: bool = False
    independent: bool = True
    run_id: str | None = None
    timestamp: str = field(default_factory=_utc_timestamp)

    @property
    def rounds_run(self) -> int:
        """Derived from `rounds`, never independently settable."""
        return len(self.rounds)

    def __post_init__(self) -> None:
        _validate_task_label(self.task, "task")
        _validate_run_id(self.run_id, "run_id")
        _validate_choice(self.occasion, DIALOGUE_OCCASIONS, "occasion")
        _validate_choice(self.topology, DIALOGUE_TOPOLOGIES, "topology")
        _validate_rounds(self.rounds, "rounds")
        _validate_count(self.canaries_planted, "canaries_planted")
        _validate_count(self.canaries_caught, "canaries_caught")
        _validate_flag(self.degraded, "degraded")
        _validate_flag(self.independent, "independent")
        _validate_timestamp(self.timestamp)
        if self.canaries_caught > self.canaries_planted:
            raise ValueError(
                "canaries_caught cannot exceed canaries_planted "
                f"({self.canaries_caught} > {self.canaries_planted})"
            )

    def to_mapping(self) -> dict[str, object]:
        mapping = _flatten(self, self.task)
        # `rounds_run` is a property, so `dataclasses.asdict` cannot see it.
        # Added explicitly because a reader of the stream needs the count
        # without recomputing it, and pinned by a schema test. Always added,
        # never conditionally: `_optional_wire_fields`/`_required_wire_keys`
        # cannot see a property either, so `read_journal` treats this key as
        # required by hand (`_DIALOGUE_QUALITY_REQUIRED_KEYS`) — that
        # requirement is only honest because this line has no branch that
        # could omit it.
        mapping["rounds_run"] = self.rounds_run
        return mapping


@dataclass(frozen=True)
class ComplianceRecord:
    """One post-session audit verdict, persisted instead of printed and lost.

    Session-scoped, so it carries a `session_id` and no `TaskLabel`: the audit
    grades a whole conversation log, not one task, and inventing a task
    identity for it would fabricate a join that does not exist.

    **What of `AuditReport` is kept, and what is deliberately dropped.**
    `routing_check.AuditReport` computes nine fields. The counts —
    `total_writes`, `code_writes`, `routing_declarations`, `worker_calls`,
    `calibration_markers` — are carried verbatim; they are pure numbers and
    they are the discipline trendline. `violations`, `declaration_drift`, and
    `code_write_files` are reduced to counts, and `violation_details` to its
    issue codes via `extract_issue_codes`.

    Dropping the detail is a decision, not an oversight. `violation_details`
    carries audit messages built from log excerpts, and `code_write_files`
    carries repository paths; both describe what a session was *working on*,
    which is precisely the task content this stream refuses to hold. A
    violation's code answers the question the scoreboard asks ("which rule,
    how often, trending which way"); its message answers "what happened in
    that step", which the audit's own stdout and the session log still answer
    for anyone with access to them. A future maintainer wanting richer
    compliance analytics should add more *codes* or more *counts*, never the
    messages or the paths.

    **One record per audit RUN, not per session — read this before reducing
    them.** Nothing stops a session being audited more than once:
    `routing-audit.sh` with no argument audits the most recent conversation,
    so a plain run followed by a `--strict` run, or a mid-session check
    followed by an end-of-session one, appends two records under one
    `session_id`. That is deliberate — a re-audit is a real event, and
    discarding it would lose the fact that a verdict changed — but it means a
    consumer asking a *per-session* question (spec 0004 ticket 16's "protocol
    violation rate per session" is exactly one) must reduce first, or it
    counts that session as many.

    The reduction contract, in full:

    - Group by `session_id`. Within a group, **the last record wins**: the
      journal is append-only, so file order is audit order and the final
      record for a session is its most recent verdict. `timestamp` agrees but
      is second-resolution and can tie; file order cannot.
    - `run_id` tells two audits apart from one audit written twice. Same
      `session_id`, different `run_id` — two audits, one session, verdict =
      the later. Same `session_id` *and* same `run_id` — one audit whose line
      got duplicated; dedupe it rather than reading a re-audit into it.
    - `timestamp` is when the **audit ran**, never when the session happened.
      Auditing a backlog of ten conversations in one afternoon stamps all ten
      minutes apart, so a discipline trendline plotted against `timestamp`
      collapses into a single point that describes the operator's afternoon
      rather than any session. Plot against `session_last_activity`, and skip
      a record that has none rather than substituting `timestamp` for it.
    """

    KIND: ClassVar[str] = "compliance"

    session_id: str
    total_writes: int
    code_writes: int
    routing_declarations: int
    worker_calls: int
    violation_count: int
    declaration_drift_count: int
    calibration_markers: int
    code_write_count: int
    issue_codes: tuple[str, ...] = ()
    run_id: str | None = None
    # When the audited session demonstrably last had activity, in wire form.
    # `routing_check` derives it from the audited log's mtime — the only
    # observable this stream can reach that is about the session rather than
    # about the audit of it. Optional rather than assumed because of two
    # honest limits: a log that was copied, restored, or `touch`ed carries
    # that moment instead, and a log that cannot be stat'd yields nothing at
    # all, which is recorded as nothing rather than as a guess.
    session_last_activity: str | None = None
    timestamp: str = field(default_factory=_utc_timestamp)

    def __post_init__(self) -> None:
        # `_validate_carried_identifier`, never `_validate_identifier`: a
        # session id is a conversation's directory name, not a descriptor
        # this module's caller composed — so a conversation named
        # `secret-rotation` must be able to have its verdict recorded. See
        # that validator for the full argument.
        _validate_carried_identifier(self.session_id, "session_id")
        _validate_run_id(self.run_id, "run_id")
        if self.session_last_activity is not None:
            _validate_timestamp(self.session_last_activity, "session_last_activity")
        for name in (
            "total_writes",
            "code_writes",
            "routing_declarations",
            "worker_calls",
            "violation_count",
            "declaration_drift_count",
            "calibration_markers",
            "code_write_count",
        ):
            _validate_count(getattr(self, name), name)
        for index, code in enumerate(_validate_tuple(self.issue_codes, "issue_codes")):
            if not isinstance(code, str) or not ISSUE_CODE_RE.fullmatch(code):
                raise ValueError(
                    f"issue_codes[{index}] must match {ISSUE_CODE_RE.pattern} — the "
                    "journal keeps audit codes, never audit messages"
                )
        _validate_timestamp(self.timestamp)

    def to_mapping(self) -> dict[str, object]:
        """Session-scoped, so there is no `TaskLabel` to flatten."""
        return _wire_form(self)


@dataclass(frozen=True)
class ReplayBenchmarkRecord:
    """One trial of the fixed replay benchmark: its score, or that it failed.

    Ticket 26's fifth family, added because the outcome family cannot hold a
    score by design — `OUTCOME_VERDICTS` pairs every `GroundTruth` to a closed
    vocabulary of categorical verdicts precisely so `("tests", "planner")` is
    unconstructible, and a number has nowhere to sit in that shape. Extending
    `WorkerExecutionRecord` or `DialogueQualityRecord` instead would have bent
    an unrelated family's schema around a value neither one is about. A fifth
    family costs updating the "how many families" count in three places
    (this module's own docstring, `CONTEXT.md`, `docs/specs/0004-learning-loop.md`)
    and is otherwise the shape ticket 26 itself calls the obvious one.

    **No `TaskLabel`.** The replay benchmark scores a fixed, versioned task
    set the evaluator owns — not a development task, the way three of this
    module's other four families are (`ComplianceRecord` is the other
    exception: session-scoped, and carries no `TaskLabel` either) — so there
    is no `TaskIdentity` to correlate against and no sensitivity-halt
    boundary to enforce: the benchmark set is never a user's task text.
    `task_set` is a caller-composed identifier (a benchmark version name,
    e.g. `"bench-v1"`), so it faces
    `_validate_identifier` — shape gate plus the sensitivity-marker gate — the
    same treatment `model_id` and `model_family` get, not
    `_validate_carried_identifier`.

    **`score` is `None` on a failed trial, never `0.0`.** A runner failure
    (`acceptance_gate.py`'s injected callable raising mid-trial) produced no
    score at all; recording `0.0` would indistinguishably mean "the runner
    crashed" and "the runner ran and scored zero", and the acceptance gate's
    "fails closed" rule depends on telling those apart. `success` and `score`
    are validated together in `__post_init__` so the two can never disagree:
    a successful trial must carry a score, a failed one must not.
    """

    KIND: ClassVar[str] = "replay_benchmark"

    task_set: str
    success: bool
    score: float | None = None
    run_id: str | None = None
    timestamp: str = field(default_factory=_utc_timestamp)

    def __post_init__(self) -> None:
        _validate_identifier(self.task_set, "task_set")
        _validate_flag(self.success, "success")
        _validate_run_id(self.run_id, "run_id")
        if self.success:
            if self.score is None:
                raise ValueError("a successful trial must carry a score")
            _validate_amount(self.score, "score")
        elif self.score is not None:
            raise ValueError("a failed trial carries no score")
        _validate_timestamp(self.timestamp)

    def to_mapping(self) -> dict[str, object]:
        """No `TaskLabel` to flatten — same shape as `ComplianceRecord.to_mapping`."""
        return _wire_form(self)


# The closed set of things that may be written to the journal. A `Protocol`
# with a `to_mapping` method would have been the flexible choice and is
# exactly wrong here: any dict-shaped object could then satisfy it, and "no
# bare mappings as contracts" is the property this stream depends on. A caller
# who wants a sixth record family adds it here, in a reviewed change, with its
# own validated schema.
JournalRecord = (
    WorkerExecutionRecord
    | OutcomeRecord
    | DialogueQualityRecord
    | ComplianceRecord
    | ReplayBenchmarkRecord
)


def journal_path(root_dir: Path) -> Path:
    """The journal file beneath an injected `root_dir`.

    Always distinct from `root_dir / ".ralph" / "routing_telemetry.jsonl"`.
    A test pins that inequality, because a refactor that "tidied" the two
    together would silently start writing an evolving schema into an audited
    stream.
    """
    return root_dir / JOURNAL_RELATIVE_PATH


def _append_jsonl_locked(path: Path, record: dict[str, object]) -> None:
    """Append one JSON record to `path` under an exclusive advisory lock.

    Duplicates `advisory_consultation._append_jsonl_locked`, which itself
    duplicates `agent_council.append_jsonl_locked`, and for the same reason
    both of them document: these files are loaded by path rather than as a
    package, so importing either would need a `sys.path` hack, and importing
    `agent_council` additionally drags in `urllib.request` and `asyncio`.
    `test_routing.py` asserts this writer produces byte-identical output to
    `agent_council.append_jsonl_locked` for the same record, so the encoding
    (`sort_keys`, the trailing newline) cannot drift apart from the stream an
    auditor already knows how to read. As with the other two copies, that test
    says nothing about the lock semantics themselves — a byte comparison
    cannot observe `fcntl.flock`.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, sort_keys=True) + "\n"
    with open(path, "a", encoding="utf-8") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            stream.write(line)
            stream.flush()
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def append_journal_record(record: JournalRecord, *, root_dir: Path) -> str | None:
    """Append one record to the journal. Failure is reported, never raised.

    Returns `None` on success and an error message on failure, matching
    `advisory_consultation._write_telemetry_record` exactly. The journal
    observes work it must never be able to break: an unwritable `.ralph`
    directory has to degrade the learning loop, not fail the worker
    invocation, test run, or audit that was merely being recorded. Callers
    fold the returned message into whatever they already report, the way the
    consultation folds telemetry failures into its result's `error`.

    Note the deliberate split with record construction, which *raises*: a bad
    value is a call-site bug and must be loud, a bad disk is the environment
    and must not be. See this module's docstring.
    """
    path = journal_path(root_dir)
    try:
        _append_jsonl_locked(path, record.to_mapping())
    except OSError as exc:
        return f"failed to write learning journal record at {path}: {exc}"
    return None


def extract_issue_codes(messages: Iterable[str]) -> tuple[str, ...]:
    """Reduce audit messages to their distinct issue codes, sorted.

    The documented boundary where content becomes a statistic. A caller holds
    the audit's issue messages — built from log excerpts — and needs the
    *codes* for `ComplianceRecord`. Passing the messages through this function
    is what guarantees only codes come out: it matches a leading token against
    `ISSUE_CODE_RE` and discards everything else, including any message that
    carries no code at all.

    **Both message shapes the audit actually produces are accepted, and that
    is part of the contract rather than an accident.** `routing_check`
    builds some issues bare (`"DEC-04 missing --model ..."`, straight from
    `_analyze_step`) and others prefixed with their step
    (`"Step 7: DEC-03 invalid routing declaration"`, from
    `RoutingAuditEngine._structural_issues`). So the code is looked for at
    the front of the message first, and only then after a leading `"Step N:
    "`-style prefix. Checking past the prefix is what a message like
    `"LOG-01 unknown write tool: apply_unreviewed_patch"` needs — its own
    embedded colon would otherwise hide the code — and checking the front
    first is what keeps a caller from having to synthesize a prefix it does
    not have just to satisfy this function's parsing. `_persist_compliance_record`
    used to do exactly that, which made a string format an unwritten contract
    between two modules; neither shape is privileged now.

    Multiplicity is deliberately dropped. `ComplianceRecord.violation_count`
    already carries volume; this carries *which rules* were broken, and a set
    is the honest shape for that. Repeat counts per code would also make the
    record's size track how noisy a single session's log was — a weak but
    real signal about session content, for no analytical gain the count does
    not already provide.
    """
    codes = set()
    for message in messages:
        for candidate in (message, message.split(":", 1)[-1]):
            head = candidate.strip().split(" ", 1)[0]
            if ISSUE_CODE_RE.fullmatch(head):
                codes.add(head)
                break
    return tuple(sorted(codes))


# --- the reader: the inverse of `to_mapping`, across all five families ---
#
# Lives here rather than in a consumer module because the schema knowledge
# it needs — `TaskLabel`'s un-flattening, `DialogueRound`'s reconstruction,
# which keys each family declares — already lives here, and duplicating it
# into a second file is exactly the drift ticket 12's ownership principle
# exists to prevent. See the design decision recorded in
# `implementation_plan.md` Section 1 for the full argument.


def _reject_non_finite_json_constant(constant: str) -> float:
    """`parse_constant` hook: refuse `NaN`/`Infinity`/`-Infinity` outright.

    `json.loads` accepts these three non-standard tokens by default, anywhere
    in a line — including inside a field this reader does not yet know about
    and would otherwise silently drop via `_filtered_fields`. A value that
    can only be discarded this way still made the line parse as valid JSON,
    which it is not per the wire format's own contract (`_append_jsonl_locked`
    writes with `json.dumps`, which never emits these tokens for a value this
    module's own validators would have accepted — see `_validate_amount`).
    Raising here makes such a line unreadable exactly like any other invalid
    JSON, instead of only when the token happens to land in a validated
    field.
    """
    raise ValueError(f"non-finite JSON constant {constant!r} is not valid JSON")


def _filtered_fields(cls: type, mapping: Mapping[str, Any]) -> dict[str, Any]:
    """Restrict `mapping` to `cls`'s declared dataclass field names.

    The forward-compatibility lock: a field this reader predates (ticket 27's
    discriminator, added to an existing family) is dropped rather than
    reaching the constructor as an unexpected keyword argument, which would
    otherwise turn `TypeError` into a lost record for every line carrying it.
    A new record *family* is a different case entirely: for any reader that
    predates it, it is handled upstream of this function by the
    `unknown_kind_lines` path in `read_journal` — its records never reach a
    rehydrator, so they never reach `_filtered_fields` either. Ticket 26's
    `ReplayBenchmarkRecord` took exactly that path in every reader before this
    one; once a family has an entry in `_REHYDRATE_BY_KIND`, its records flow
    through this function like any other family's. `dataclasses.fields` never
    sees a `ClassVar` (`KIND`) or a `@property`
    (`DialogueQualityRecord.rounds_run`), so both are filtered out here for
    free rather than needing a special case.
    """
    allowed = {declared.name for declared in dataclasses.fields(cls)}
    return {key: value for key, value in mapping.items() if key in allowed}


def _pop_task_label(mapping: dict[str, Any]) -> TaskLabel:
    """Un-flatten a task-bearing record's three top-level keys back into a `TaskLabel`.

    Mirrors `TaskLabel.to_mapping`. A missing `task_type` key and an explicit
    `{"task_type": null}` both pop to `None` (untagged) here — `dict.pop`
    with a default cannot tell "absent" from "present but null" apart, and
    this reader does not try to: see `read_journal`'s docstring for why a
    wire `null` on an optional field is accepted as absence rather than
    rejected as damage. `TaskLabel.__post_init__` re-runs both of its locks
    on the rebuilt object, so a corrupt line claiming both
    `sensitivity_halted` and a `task_type` is rejected on read exactly as it
    would be on write.
    """
    return TaskLabel(
        task_id=mapping.pop("task_id", None),
        task_type=mapping.pop("task_type", None),
        sensitivity_halted=mapping.pop("sensitivity_halted", False),
    )


def _optional_wire_fields(cls: type) -> frozenset[str]:
    """Field names whose annotation admits `None` — the only ones a wire
    record may legitimately omit.

    Mirrors the write side exactly: `_wire_form` (and `TaskLabel.to_mapping`)
    drop a field from the wire *iff its value is `None`*, never because a
    dataclass default happened to apply. So a field can be honestly absent
    from a real record only if its type allows `None` in the first place —
    everything else is written every time, whatever value it holds,
    including a falsy or default one (`degraded=False`, `issue_codes=()`).
    Derived from resolved type hints, not a hardcoded field list, so a future
    field defaults to *required on the wire* unless its own annotation opts
    it out — the same way a future field defaults to *dropped* by
    `_filtered_fields` unless it is declared on the dataclass.
    """
    hints = get_type_hints(cls)
    return frozenset(
        declared.name
        for declared in dataclasses.fields(cls)
        if type(None) in get_args(hints.get(declared.name))
    )


def _required_wire_keys(cls: type, *, exclude: frozenset[str] = frozenset()) -> frozenset[str]:
    """Every wire key `cls` always writes, per `_optional_wire_fields`.

    `exclude` drops fields that never appear under their own name on the
    wire: `task` is flattened into `TaskLabel`'s own keys (see `_flatten`),
    and a `@property` such as `DialogueQualityRecord.rounds_run` is not a
    `dataclasses.field` at all, so it is already absent from
    `dataclasses.fields(cls)` without needing to be named here.
    """
    optional = _optional_wire_fields(cls)
    return frozenset(
        declared.name for declared in dataclasses.fields(cls) if declared.name not in optional
    ) - exclude


# One rule, verified against every family's `to_mapping` rather than a
# hand-enumerated field list: a wire key is required unless its field's own
# type admits `None`. Computed once, after every record class is declared, so
# a FIELD added later to an already-registered family is picked up here with
# no line added — but a new record FAMILY is not: it still needs its own
# required-key constant below, its own `_rehydrate_*` function, and its own
# `_REHYDRATE_BY_KIND` entry, none of which follow from merely declaring the
# dataclass. `TaskLabel`'s own required keys (`task_id`, `sensitivity_halted`
# — `task_type` is the one field on that class typed `X | None`) are unioned
# into each task-bearing family's set, because those two names are what
# `task` flattens onto the wire; `task` itself is excluded from the record's
# own set since it never appears under that name. `rounds_run` is the one
# exception to "verified against `to_mapping`" itself: it is a `@property`,
# not a `dataclasses.field`, so `_required_wire_keys` cannot discover it, and
# it is unioned in by hand below where `_DIALOGUE_QUALITY_REQUIRED_KEYS` is
# built.
_TASK_LABEL_REQUIRED_KEYS = _required_wire_keys(TaskLabel)
_WORKER_EXECUTION_REQUIRED_KEYS = (
    _required_wire_keys(WorkerExecutionRecord, exclude=frozenset({"task"}))
    | _TASK_LABEL_REQUIRED_KEYS
)
_OUTCOME_REQUIRED_KEYS = (
    _required_wire_keys(OutcomeRecord, exclude=frozenset({"task"})) | _TASK_LABEL_REQUIRED_KEYS
)
_DIALOGUE_QUALITY_REQUIRED_KEYS = (
    _required_wire_keys(DialogueQualityRecord, exclude=frozenset({"task"}))
    | _TASK_LABEL_REQUIRED_KEYS
    | frozenset({"rounds_run"})
)
_COMPLIANCE_REQUIRED_KEYS = _required_wire_keys(ComplianceRecord)
_REPLAY_BENCHMARK_REQUIRED_KEYS = _required_wire_keys(ReplayBenchmarkRecord)


def _check_required_keys(mapping: Mapping[str, Any], required: frozenset[str], kind: str) -> None:
    """Raise if `mapping` — the raw wire object, before any un-flattening —
    lacks a key that `to_mapping` always writes for `kind`.

    Runs before any `.pop`/`.get` in a rehydrate function touches `mapping`,
    so a default reached later in that function (a dataclass
    `default_factory`, or a rehydrator's own `.get(key, fallback)`) can never
    stand in for a key the wire form never actually omitted. That is the
    entire bug this closes: a constructor default is a write-side
    convenience for a caller who has no opinion on a field, not a statement
    that the field may be silently reconstructed from nothing on the read
    side.
    """
    missing = required - mapping.keys()
    if missing:
        raise ValueError(f"{kind} record missing required wire key(s): {sorted(missing)}")


def _rehydrate_worker_execution(mapping: dict[str, Any]) -> WorkerExecutionRecord:
    _check_required_keys(mapping, _WORKER_EXECUTION_REQUIRED_KEYS, WorkerExecutionRecord.KIND)
    mapping["task"] = _pop_task_label(mapping)
    return WorkerExecutionRecord(**_filtered_fields(WorkerExecutionRecord, mapping))


def _rehydrate_outcome(mapping: dict[str, Any]) -> OutcomeRecord:
    _check_required_keys(mapping, _OUTCOME_REQUIRED_KEYS, OutcomeRecord.KIND)
    mapping["task"] = _pop_task_label(mapping)
    return OutcomeRecord(**_filtered_fields(OutcomeRecord, mapping))


def _rehydrate_dialogue_quality(mapping: dict[str, Any]) -> DialogueQualityRecord:
    _check_required_keys(
        mapping, _DIALOGUE_QUALITY_REQUIRED_KEYS, DialogueQualityRecord.KIND
    )
    mapping["task"] = _pop_task_label(mapping)
    # `_check_required_keys` above already guarantees "rounds" is present —
    # unlike the removed `mapping.get("rounds", ())`, nothing here may
    # substitute a fallback for an absent key.
    rounds_raw = mapping["rounds"]
    if not isinstance(rounds_raw, (list, tuple)):
        raise ValueError(  # noqa: TRY004 - one rejection contract; see the note above
            f"rounds must be a list on the wire, got {type(rounds_raw).__name__}"
        )
    rounds: list[DialogueRound] = []
    for entry in rounds_raw:
        if not isinstance(entry, dict):
            raise ValueError(  # noqa: TRY004 - one rejection contract; see the note above
                f"each round must be an object on the wire, got {type(entry).__name__}"
            )
        # `_filtered_fields`, not a bare `DialogueRound(**entry)`: a nested
        # round from a schema this reader predates (a future per-round
        # field) must be forward-compatibility-filtered exactly like the
        # top-level record is, or one unknown key inside one round makes the
        # entire dialogue unreadable rather than just that round's addition.
        rounds.append(DialogueRound(**_filtered_fields(DialogueRound, entry)))
    mapping["rounds"] = tuple(rounds)
    # `rounds_run` is on the wire — `to_mapping` always writes it, so by the
    # rule the rest of this reader applies (an always-emitted key is
    # required) it is in `_DIALOGUE_QUALITY_REQUIRED_KEYS` and
    # `_check_required_keys` above already guarantees its presence. Popped
    # and checked here, rather than left for `_filtered_fields` to silently
    # drop: it is a derived property, not a `dataclasses.field`, so nothing
    # would otherwise notice a value that contradicts the round sequence it
    # is supposed to describe — exactly the gap that let a line whose
    # `rounds_run` disagreed with `len(rounds)` through before this check.
    rounds_run_raw = mapping.pop("rounds_run")
    _validate_count(rounds_run_raw, "rounds_run")
    if rounds_run_raw != len(rounds):
        raise ValueError(
            f"rounds_run ({rounds_run_raw!r}) does not match len(rounds) "
            f"({len(rounds)}) — the wire record is internally inconsistent"
        )
    return DialogueQualityRecord(**_filtered_fields(DialogueQualityRecord, mapping))


def _rehydrate_compliance(mapping: dict[str, Any]) -> ComplianceRecord:
    _check_required_keys(mapping, _COMPLIANCE_REQUIRED_KEYS, ComplianceRecord.KIND)
    issue_codes_raw = mapping.get("issue_codes")
    if issue_codes_raw is not None:
        if not isinstance(issue_codes_raw, (list, tuple)):
            raise ValueError(
                f"issue_codes must be a list on the wire, got {type(issue_codes_raw).__name__}"
            )
        mapping["issue_codes"] = tuple(issue_codes_raw)
    return ComplianceRecord(**_filtered_fields(ComplianceRecord, mapping))


def _rehydrate_replay_benchmark(mapping: dict[str, Any]) -> ReplayBenchmarkRecord:
    _check_required_keys(
        mapping, _REPLAY_BENCHMARK_REQUIRED_KEYS, ReplayBenchmarkRecord.KIND
    )
    return ReplayBenchmarkRecord(**_filtered_fields(ReplayBenchmarkRecord, mapping))


# Every known `kind` value, mapped to the function that rebuilds its record.
# A `kind` absent from this mapping is not this reader's schema problem — see
# `read_journal`'s `unknown_kind_lines` handling — so this is deliberately
# not the same thing as "every value `JournalRecord` can hold".
_REHYDRATE_BY_KIND: dict[str, Callable[[dict[str, Any]], Any]] = {
    WorkerExecutionRecord.KIND: _rehydrate_worker_execution,
    OutcomeRecord.KIND: _rehydrate_outcome,
    DialogueQualityRecord.KIND: _rehydrate_dialogue_quality,
    ComplianceRecord.KIND: _rehydrate_compliance,
    ReplayBenchmarkRecord.KIND: _rehydrate_replay_benchmark,
}


@dataclass(frozen=True)
class JournalRead:
    """The result of reading the journal: every record, grouped by family.

    **File order is preserved within each family, and never sorted.** This
    is load-bearing, not stylistic: a consumer's "last record wins"
    reduction (`ComplianceRecord`'s own docstring; the same convention for a
    task's `plan` outcomes) derives its meaning from file order in an
    append-only stream, because `ComplianceRecord.timestamp` is
    second-resolution and can tie. A reader that sorted by timestamp would
    silently pick the wrong verdict on every same-second pair.

    **Every record here has parseable timestamps.** `read_journal` calls
    `parse_wire_timestamp` on every family's `timestamp` (and on
    `ComplianceRecord.session_last_activity` when present) before a record
    is admitted, so a consumer may parse them freely — this is the guarantee
    that keeps that arithmetic branch-free. See `read_journal`.
    """

    worker_executions: tuple[WorkerExecutionRecord, ...] = ()
    outcomes: tuple[OutcomeRecord, ...] = ()
    dialogues: tuple[DialogueQualityRecord, ...] = ()
    compliance: tuple[ComplianceRecord, ...] = ()
    replay_benchmarks: tuple[ReplayBenchmarkRecord, ...] = ()
    unreadable_lines: int = 0
    unknown_kind_lines: int = 0


def read_journal(root_dir: Path) -> JournalRead:
    """Read every record in the journal beneath `root_dir`, tolerating damage.

    **A missing journal file reads as an empty `JournalRead`, not an
    error.** This is what makes "an empty journal produces a scoreboard
    rather than an error" structural rather than a special case bolted onto
    a consumer.

    **Detected by catching `FileNotFoundError` from `open()` itself, never by
    a `Path.exists()` pre-check.** An earlier version of this function called
    `path.exists()` before opening the file, and that was the exact back door
    the whole-file-failure guarantee below exists to close: `exists()`
    reports `False` for at least some `OSError`s raised while stat-ing the
    path (a permission error on the path or a parent directory among them),
    which is indistinguishable from a genuinely absent file — the silent
    "no data" the module docstring forbids, reappearing through a check that
    looks like a harmless guard clause. A separate pre-check is also a race
    on its own terms, independent of what it swallows: a file present at the
    `exists()` call and deleted before the `open()` call two lines later
    would still need `open()`'s own `FileNotFoundError` handled regardless of
    what `exists()` had said moments earlier. Asking `open()` once and
    reacting to what it actually raises removes both problems at once —
    there is no longer a window in which the two calls can disagree, because
    there is only one call.

    **A line is malformed** — skipped and counted in `unreadable_lines`,
    never raised — when: it is not valid JSON; it parses to something other
    than a JSON object; it has no `kind` key; re-hydration raises (a missing
    required key, or a value a record's own validators reject, both of which
    surface as `ValueError` or `TypeError` — argument binding for a missing
    keyword raises the latter, not a validator, so both are caught); or a
    timestamp that satisfies `TIMESTAMP_RE` names no real instant (parsed and
    discarded here, inside the same try that builds the record, so a
    calendar-invalid timestamp is counted unreadable rather than raising
    three modules downstream). A torn final line — the ordinary artifact of a
    crash or full disk mid-write — is exactly this case, not an exotic one.

    **A blank or whitespace-only line is not one of the above, and is
    skipped without incrementing `unreadable_lines` — DECIDE, resolved
    toward "skip".** Section 1.3 calls a line malformed when it is not valid
    JSON, and an empty string is not valid JSON, so this could have gone
    either way. It is skipped rather than counted because a blank line
    carries no record and loses no evidence: unlike a torn JSON line, there
    was never a record here to fail to read. Counting it would also make
    `unreadable_lines` sensitive to a fact about the filesystem rather than
    about the journal's health — a file hand-edited to add a separating
    blank line would otherwise report damage that is not there.

    **An explicit wire `null` on an optional field (`run_id`, `task_type`,
    `session_last_activity`) is accepted as absence, not rejected as
    damage — DECIDE, resolved toward "accept".** `_wire_form` and
    `TaskLabel.to_mapping` never emit `null` — an absent optional field is
    dropped from the mapping entirely — so by the rule the rest of this
    reader applies elsewhere ("a shape this module's own writer would never
    produce is damage", the reasoning behind the `rounds_run` consistency
    check and every required-key check above) a present `null` looks like it
    should fail the same way. It is let through instead, because unlike
    those other cases the two readings of `null` are not merely similar but
    identical the moment they cross this function's return boundary: every
    optional field here is typed `X | None`, so a rehydrated record holds
    exactly `None` whether the wire said nothing or said `null` — nothing
    downstream of `read_journal` ever sees the raw mapping to tell the two
    apart, only the typed `JournalRead` dataclasses this function returns.
    Rejecting `null` would add a check whose only effect is guarding a
    distinction no code in this repository can observe. If a future consumer
    is ever handed the raw wire mapping instead of a rehydrated record, this
    decision needs revisiting before that ships, not after.

    **A record of an unknown `kind`** — a family this reader predates — is
    skipped and counted separately, in `unknown_kind_lines`. Two different
    facts deserve two different counters: `unreadable_lines` means *this
    stream is damaged*; `unknown_kind_lines` means *this reader is older than
    this stream*. Folding them into one number destroys the only signal that
    distinguishes an operator's two different next steps (look at the disk,
    or look at the deployment).

    **File order is preserved; the read never sorts.** See `JournalRead`.

    **A shared advisory lock** (`fcntl.LOCK_SH`) is held for the read,
    mirroring `_append_jsonl_locked`'s `LOCK_EX`. This makes a *concurrent*
    torn line impossible rather than merely tolerated; the tolerance above
    remains for lines already on disk from a past crash — both, not either.

    **A whole-file failure — `open` or `flock` raising — propagates, and
    that is a decision, not an oversight.** A missing file is not a failure
    (see above, and note that "missing" means `FileNotFoundError` and
    nothing broader); a file that exists but cannot be opened (a
    `PermissionError` — the exact failure a `Path.exists()` pre-check risked
    swallowing, see above) or locked is a different fact than a damaged
    line: the caller asked to
    look at the journal and this call could not attempt to, rather than
    attempting and finding damage. Returning an empty `JournalRead` for that
    would be silent in exactly the sense the module docstring forbids — an
    empty read is indistinguishable from an honest empty journal, so a
    scoreboard built on it would report "no data" when the truth is "could
    not look". That module docstring also gives the rule for *which* of its
    two failure modes applies here: a writer invoked as a side effect of
    some other operation catches and reports, but "a writer... invoked
    *directly* to record something... lets the... error propagate, which is
    this contract's plain form." `read_journal` has **no production caller
    today** — `learning_outcomes.py` never calls it; grep confirms it — so
    there is nothing yet for it to be a side effect of. Its first caller will
    be stage 2's scoreboard, and reading the journal is that caller's
    *primary operation*, not a side effect of something else it must not
    disturb — the same distinction that lets a reader propagate where a
    writer, whose primary operation is something else entirely, must not.
    `read_journal` therefore takes the writer family's "invoked directly"
    form rather than growing a second, swallow-and-report path this module
    reserves for side-effecting writers.

    **`UnicodeDecodeError` is deliberately carved out of that propagation
    and handled per line instead.** A byte sequence that is not valid UTF-8
    partway through the file is not "the journal could not be looked at" —
    it is the identical shape as a torn final JSON line: the ordinary
    artifact of a crash or full disk mid-write, this time tearing a
    multi-byte UTF-8 sequence instead of a JSON token. Section 1.3's
    per-line tolerance already exists for exactly that artifact, so the file
    is read as bytes and decoded one line at a time — a bad line is counted
    in `unreadable_lines` and its neighbours still read — rather than
    decoding the whole file at once and letting one torn line anywhere in it
    take down every record after it.
    """
    path = journal_path(root_dir)
    try:
        # SIM115 wants `open()` directly in a `with` — but the `except`
        # below must catch only `open()`'s own `FileNotFoundError`, never
        # one raised later by `flock`/`readlines` inside the block (see this
        # function's docstring on the whole-file-failure guarantee), and a
        # single `with open(...) as stream:` cannot narrow its own `except`
        # to just the open call. The two-step form is what keeps that scope
        # narrow; `stream` is still closed via the `with` below.
        stream = open(path, "rb")  # noqa: SIM115
    except FileNotFoundError:
        return JournalRead()
    with stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_SH)
        try:
            raw_lines = stream.readlines()
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    worker_executions: list[WorkerExecutionRecord] = []
    outcomes: list[OutcomeRecord] = []
    dialogues: list[DialogueQualityRecord] = []
    compliance: list[ComplianceRecord] = []
    replay_benchmarks: list[ReplayBenchmarkRecord] = []
    unreadable_lines = 0
    unknown_kind_lines = 0

    for raw_line in raw_lines:
        try:
            text = raw_line.decode("utf-8").strip()
        except UnicodeDecodeError:
            unreadable_lines += 1
            continue
        if not text:
            continue

        try:
            payload = json.loads(text, parse_constant=_reject_non_finite_json_constant)
        except ValueError:
            # `json.JSONDecodeError` (malformed JSON) and the plain
            # `ValueError` `_reject_non_finite_json_constant` raises for a
            # `NaN`/`Infinity`/`-Infinity` token are both "this line is not
            # valid JSON" — `JSONDecodeError` is itself a `ValueError`
            # subclass, so one except clause covers both without treating
            # them as different failure modes.
            unreadable_lines += 1
            continue

        if not isinstance(payload, dict) or "kind" not in payload:
            unreadable_lines += 1
            continue

        kind = payload["kind"]
        if not isinstance(kind, str):
            # A non-string `kind` (a list, a number, `null`) is not "a family
            # this reader predates" — every real family name is a str — so it
            # belongs in `unreadable_lines`, not `unknown_kind_lines`. It also
            # cannot reach `_REHYDRATE_BY_KIND.get` at all: a `list` there
            # raises `TypeError: unhashable type`, which would break this
            # function's own per-line tolerance contract before the tolerant
            # `try` below even starts.
            unreadable_lines += 1
            continue

        rehydrate = _REHYDRATE_BY_KIND.get(kind)
        if rehydrate is None:
            unknown_kind_lines += 1
            continue

        try:
            record = rehydrate(dict(payload))
            parse_wire_timestamp(record.timestamp)
            if (
                isinstance(record, ComplianceRecord)
                and record.session_last_activity is not None
            ):
                parse_wire_timestamp(record.session_last_activity)
        except (ValueError, TypeError):
            unreadable_lines += 1
            continue

        if isinstance(record, WorkerExecutionRecord):
            worker_executions.append(record)
        elif isinstance(record, OutcomeRecord):
            outcomes.append(record)
        elif isinstance(record, DialogueQualityRecord):
            dialogues.append(record)
        elif isinstance(record, ComplianceRecord):
            compliance.append(record)
        else:
            replay_benchmarks.append(record)

    return JournalRead(
        worker_executions=tuple(worker_executions),
        outcomes=tuple(outcomes),
        dialogues=tuple(dialogues),
        compliance=tuple(compliance),
        replay_benchmarks=tuple(replay_benchmarks),
        unreadable_lines=unreadable_lines,
        unknown_kind_lines=unknown_kind_lines,
    )
