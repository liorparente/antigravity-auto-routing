#!/usr/bin/env python3
"""LearningJournal: the content-free signal stream the learning loop reads.

A dedicated, append-only JSONL stream beside the audited routing telemetry,
carrying four record families — worker execution, ground-truth outcomes,
dialogue quality, and protocol compliance. It exists so the orchestrator can
be judged against what actually happened rather than against what it declared
it would do.

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
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Literal, get_args

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
# already spends "signal family" on the journal's four *record* families
# (worker execution, ground-truth outcomes, dialogue quality, protocol
# compliance). One word at two granularities in a glossary-driven codebase is
# a reader's trap: "signal" would mean the outcome family in CONTEXT.md and a
# subdivision *inside* that one family here. The prose in this module already
# called these four "ground truth" throughout; the type now agrees with it.
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
    """
    text = _require_str(value, field_name)
    if not TIMESTAMP_RE.fullmatch(text):
        raise ValueError(
            f"{field_name} must match {TIMESTAMP_RE.pattern}, got {_echoable(text)}"
        )


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
        `DialogueQualityRecord`; and `ComplianceRecord` is session-scoped and
        carries no `TaskLabel` at all. None of the four families this module
        defines is reachable on a halt, so no caller can exist yet without
        first fabricating a record about work that never ran — which is
        exactly what the halt boundary forbids.

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

    `engagement_count` is the number of atomic engagement units in that
    round's critique — the VerdictContract measure that makes rubber-stamping
    visible, since an approval carrying zero of them does not parse as an
    approval at all. Trending it is how the loop notices a Critic going quiet.
    A count, never the objections themselves: the units are quoted from the
    reviewed artifact, so carrying them would carry the artifact.
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
    """How a CriticalDialogue behaved — schema only in this ticket.

    Spec 0003's machinery writes these; this module owns the contract so both
    specs agree on the shape before either has a writer.

    `rounds` is a tuple of `DialogueRound` — one value per round, not parallel
    arrays; see that class for why. It serializes as a list of objects
    (`[{"verdict": ..., "engagement_count": ...}, ...]`), which is the shape a
    metrics reader can index by round without zipping two lists together and
    trusting they are the same length.

    `rounds_run` is a derived property, not a field, for the same reason
    `AdvisoryDebateResult.consensus_reached` is: a record must not be able to
    claim a round count its own round sequence does not back. The count is
    written to the wire form by `to_mapping`, so a reader still sees it.

    Both flags are named for their healthy state being `True`/`False`
    respectively, because a flag whose polarity a reader has to guess is a
    trap in a metrics stream: `degraded` is True when the dialogue ran cheaper
    or shorter than its occasion called for (a budget degradation), and
    `independent` is True when the participants genuinely came from different
    model families.
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
        # without recomputing it, and pinned by a schema test.
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


# The closed set of things that may be written to the journal. A `Protocol`
# with a `to_mapping` method would have been the flexible choice and is
# exactly wrong here: any dict-shaped object could then satisfy it, and "no
# bare mappings as contracts" is the property this stream depends on. A caller
# who wants a fifth record family adds it here, in a reviewed change, with its
# own validated schema.
JournalRecord = (
    WorkerExecutionRecord | OutcomeRecord | DialogueQualityRecord | ComplianceRecord
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
