#!/usr/bin/env python3
"""AdvisoryConsultation: the Planner-Critic advisory debate loop.

This is a distinct capability from :mod:`agent_council`'s deterministic
three-tier round plan. `AgentCouncil` has no model or network dependency and
its output is cached and HMAC-signed; a real, model-based Planner-Critic loop
must never be dropped into that module, or it would silently destroy the
determinism its cache and signature depend on. This module is where that
loop belongs instead.

Callers may inject an ``invoke_worker`` callable: ``(model, effort, prompt)
-> text``. When omitted, the production invoker is imported lazily, so the
loop remains exercisable offline with a fake.
"""
from __future__ import annotations

import dataclasses
import fcntl
import hashlib
import json
import os
import re
import secrets
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

MAX_DEBATE_ROUNDS = 3

# Mirrors `agent_council.ESCALATION_FAILURE_THRESHOLD` rather than importing
# it — same reasoning as `SENSITIVITY_MARKERS` below (importing
# `agent_council` would pull `urllib.request`/`asyncio` into this module,
# and both files are loaded by path, not as a package). This is the
# protocol's 2-failure escalation rule: `agent_council.escalate_routing_effort`
# treats `attempts >= ESCALATION_FAILURE_THRESHOLD` as "escalate", and
# `needs_post_mortem_consultation` below fires its own escalation trigger at
# the same threshold, because it exists specifically to track that same
# protocol rule. `test_escalation_failure_threshold_matches_agent_council_constant`
# in `test_routing.py` keeps the two values from silently drifting apart.
ESCALATION_FAILURE_THRESHOLD = 2

WORKER_MODE_TOKEN = "[WORKER-MODE: AGY-NESTED-EXEC]"

CRITIC_VERDICT_APPROVE = "VERDICT: APPROVE"
CRITIC_VERDICT_REVISE = "VERDICT: REVISE"

InvokeWorker = Callable[[str, str, str], str]

# Discriminates how a consultation ended. `consensus_reached` on the result
# stays consistent with this: True only when outcome == "consensus". The
# other four are all "no consensus", distinguished for the caller because a
# stalemate, a malformed verdict, an unreachable worker, and a sensitivity
# halt each demand a different human response. "sensitivity_halt" is kept
# distinct from "stalemate" and "worker_error" rather than folded into
# either: it is a pre-flight refusal on the task text, so no worker was ever
# contacted — it is neither a disagreement (stalemate) nor a failure to
# reach one (worker_error).
AdvisoryOutcome = Literal[
    "consensus",
    "stalemate",
    "unparseable_verdict",
    "worker_error",
    "sensitivity_halt",
]

# The Critic's verdict line, once read, is one of these three states.
# "unparseable" is deliberately not folded into "revise": a malformed
# response must halt the consultation, not be fed back to the Planner as if
# it were a reasoned objection. See `VerdictContractResult` below for the
# richer shape `_parse_critic_verdict` actually returns (spec 0003 ticket
# 02): this three-way type is still exactly what a caller branches on, just
# no longer the parser's entire return value.
CriticVerdict = Literal["approved", "revise", "unparseable"]

# Spec 0003 (CriticalDialogue) ticket 02: the VerdictContract's textual
# shape. Nothing in the spec pins the literal syntax below -- that choice
# belongs to this ticket, and it is load-bearing for every later ticket that
# reads a Critic response (panel topology, canaries, telemetry), so it is
# documented once, here, rather than re-derived from `_parse_critic_verdict`
# each time.
#
# A Critic response must contain, in order:
#
#   1. Free-text rationale -- any prose, any number of lines, no marker.
#   2. Zero or more "engagement units", each occupying exactly one line,
#      interleaved with rationale text in any order, but all of them before
#      the verdict line:
#        - A QUOTE line: stripped text starting, case-insensitively, with
#          "QUOTE:", e.g. `QUOTE: "text copied verbatim from the artifact"`.
#          One leading and one trailing '"' are stripped from the remainder
#          if present (the quote marks are a convention for the Critic to
#          follow, not something the parser requires). What remains is
#          checked for verbatim (byte-for-byte) containment in the artifact
#          text `_parse_critic_verdict` is given -- the Planner's plan on
#          today's occasion, a diff or a lesson on later ones. A quote that
#          fails this check is silently dropped: it does not count toward
#          `verified_quote_count`, and it does NOT by itself make the
#          response unparseable -- see `_parse_critic_verdict`.
#        - An OBJECTION line: stripped text matching `^\d+\.\s+\S`, e.g.
#          `1. The rollback plan omits a database migration step.` Numbers
#          need not be sequential or unique; every matching line counts as
#          one objection.
#   3. The verdict line, LAST: the final non-empty line of the entire
#      response (reversed from spec 0001's "first line" contract, to make
#      room for rationale and engagement units ahead of it). Its own shape
#      is unchanged from spec 0001: an exact "VERDICT: APPROVE"
#      (case-insensitive, no other content on that line) or a
#      "VERDICT: REVISE"-prefixed line, tolerantly matched exactly as
#      `_is_tolerant_revise` always has.
#
# An APPROVE verdict line is only ever read as "approved" when
# `verified_quote_count >= 1` for that response -- `objection_count` is
# tallied alongside it but never substitutes for a verified quote. This is
# deliberately asymmetric, not `verified_quote_count + objection_count >=
# 1`: a quote is mechanically checked, byte-for-byte, against the artifact
# text, so it is evidence the Critic actually engaged with something real;
# an objection is free text with no verification at all, so any number of
# objections backed by zero verified quotes is exactly as untrustworthy as
# zero engagement -- a Critic could fabricate ten numbered objections about
# a plan it never read. A bare or fabricated APPROVE (zero verified quotes,
# regardless of objection_count) parses as "unparseable" instead (see
# `_parse_critic_verdict`). REVISE carries no such requirement: it is read
# exactly as tolerantly, and exactly as unconditionally, as spec 0001 always
# read it.
_QUOTE_LINE_PATTERN = re.compile(r"(?i)^QUOTE:\s*(.*)$")
_OBJECTION_LINE_PATTERN = re.compile(r"^\d+\.\s+\S")

# Spec 0003 (CriticalDialogue) ticket 01: the occasion a consultation runs
# under. "ambiguity" is the sole occasion spec 0001 shipped — every default
# below resolves to it, so an existing call site that never mentions
# `occasion` keeps behaving exactly as it did before this type existed. The
# other three are the seam this ticket builds: `_MISSION_COPY` backs each
# with prompt content, but wiring their real trigger predicates (spec 0003's
# ticket 03) and blocking stance (ticket 04) is deliberately not done here.
Occasion = Literal["ambiguity", "plan-review", "code-review", "post-mortem"]

# Mirrors `agent_council.SENSITIVE_PATTERNS` rather than importing it:
# importing `agent_council` would pull `urllib.request`, `asyncio`, and
# `fcntl` into a module whose docstring promises no HTTP client and full
# offline exercisability, and these files are loaded by path rather than as
# a package, so the import would need a `sys.path` hack. `test_routing.py`
# already loads both modules and asserts this tuple is a superset of
# `agent_council.SENSITIVE_PATTERNS`, so the duplication cannot silently
# drift apart. Same precedent this module already set for `MAX_DEBATE_ROUNDS`.
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

# Spec 0003 (CriticalDialogue) ticket 03: the code-review occasion's risk
# signals — an oversized diff or a security-sensitive changed path — must be
# "read from config, not hardcoded literals in the Python source" per the
# ticket's acceptance criteria. `_CONFIG_PATH` is this module's own
# `routing-config.json`, mirroring `routing_check.CONFIG_PATH`'s
# `SCRIPT_DIR / "routing-config.json"` pattern; it is not imported from
# `routing_check` for the same reason `SENSITIVITY_MARKERS` above duplicates
# rather than imports `agent_council.SENSITIVE_PATTERNS` — these files are
# loaded by path, not as a package. `needs_code_review_consultation` accepts
# `config_path` as a keyword argument specifically so a caller (a test, or a
# future ticket) can point it at a different file and observe the trigger's
# answer change, which is the only way to prove a value is genuinely read
# from config rather than merely referenced by key.
#
# The two `DEFAULT_*` constants below are a fallback for a config file that
# is missing the `critical_dialogue` section (or one of its two keys) —
# mirroring `routing_check.load_code_extensions`'s identical
# `config.get("code_extensions", DEFAULT_CODE_EXTENSIONS)` pattern — never
# the value actually used when `routing-config.json` supplies its own
# `critical_dialogue` section, which it does as of this ticket.
_CONFIG_PATH = Path(__file__).resolve().parent / "routing-config.json"
DEFAULT_CODE_REVIEW_DIFF_LINE_THRESHOLD = 300
DEFAULT_SECURITY_SENSITIVE_PATH_PATTERNS: tuple[str, ...] = (
    "auth",
    "credential",
    "secret",
    ".env",
    "password",
    "token",
    "/keys/",
    "security",
    "iam",
    "acl",
    "permission",
)


@dataclass(frozen=True)
class AdvisoryDebateRound:
    """One Planner/Critic exchange: the proposal offered and the verdict it drew."""

    planner_proposal: str
    critic_response: str


@dataclass(frozen=True)
class AdvisoryResolutionOption:
    """One way a human can resolve a stalemate."""

    id: int
    label: str
    description: str


@dataclass(frozen=True)
class AdvisoryStalemateReport:
    """Both final positions of an unresolved consultation, plus the human's options.

    Carries no winner: the consultation does not pick one, so this structure
    has no field capable of holding one.
    """

    planner_position: str
    critic_position: str
    options: tuple[AdvisoryResolutionOption, AdvisoryResolutionOption, AdvisoryResolutionOption]


@dataclass(frozen=True)
class AdvisoryDebateResult:
    """`occasion` records which of the four `Occasion` values this
    consultation ran under (spec 0003 ticket 01). Defaulted to "ambiguity",
    spec 0001's sole occasion, so every pre-existing construction of this
    dataclass — in this module and in tests — that never mentions occasion
    keeps meaning exactly what it meant before this field existed.

    `occasion` is appended after `error`, last in field order, rather than
    inserted next to `outcome` where it reads more naturally: per
    institutional memory's 2026-08-06 backward-compatibility gotcha, a new
    dataclass field must either be keyword-only or preserve the original
    field order, because every field here is a plain (not keyword-only)
    dataclass attribute and a positional construction beyond `outcome` — none
    exists in this repo today, but nothing forbids one tomorrow — would
    otherwise silently bind into the wrong field instead of failing loudly.
    Appending preserves the original order exactly; only a genuinely new
    positional argument could ever reach `occasion`.
    """

    rounds_run: int
    final_plan: str
    outcome: AdvisoryOutcome
    planner_model: str = "Claude Opus 5 (Thinking)"
    critic_model: str = "Codex 5.6 Sol"
    rounds: tuple[AdvisoryDebateRound, ...] = ()
    stalemate: AdvisoryStalemateReport | None = None
    error: str | None = None
    occasion: Occasion = "ambiguity"

    @property
    def consensus_reached(self) -> bool:
        """True only when `outcome == "consensus"` — never independently settable.

        Frozen and derived so a result can never be constructed or mutated
        into claiming consensus its outcome does not back.
        """
        return self.outcome == "consensus"


def needs_advisory_consultation(complexity: str, confidence: float = 1.0) -> bool:
    """Determine whether task requires an advisory Planner-Critic debate loop."""
    normalized = complexity.lower().strip()
    if normalized == "ambiguous":
        return True
    return confidence < 0.7


# Spec 0003 (CriticalDialogue) ticket 03: the three sibling trigger
# predicates for the occasions `needs_advisory_consultation` above does not
# cover. Each is deliberately its own function rather than a branch bolted
# onto `needs_advisory_consultation` — that function is spec 0001's single
# predicate for the "ambiguity" occasion alone, and ticket 03's acceptance
# criteria require it to stay completely untouched (see
# `AdvisoryAmbiguityTriggerUnchangedTests` in the test suite for the
# characterization coverage that pins this).
#
# All three are pure predicates over caller-supplied signals: none of them
# invoke a worker, write an artifact, or otherwise reach outside the
# process. Deciding *whether* a dialogue should run is this ticket's job;
# deciding whether that dialogue blocks the caller (ticket 04) and what
# topology it runs under (ticket 05) are later tickets' work, wired at the
# call site, not inside these predicates.


def needs_plan_review_consultation(complexity: str) -> bool:
    """Plan-review occasion trigger: fires for Medium and Complex complexity,
    never for Simple or Trivial (spec 0003's Triggers paragraph: "Plan
    review: complexity >= Medium").

    Normalizes with the same `.lower().strip()` `needs_advisory_consultation`
    uses, because both predicates read the same caller-supplied complexity
    string — that is convergent reuse of a normalization idiom, not a
    dependency between the two functions.
    """
    normalized = complexity.lower().strip()
    return normalized in ("medium", "complex")


def _load_code_review_risk_config(config_path: Path) -> tuple[int, tuple[str, ...]]:
    """Read the code-review occasion's two risk-signal settings from
    `config_path`'s `critical_dialogue` section, falling back to this
    module's `DEFAULT_*` constants for whichever key (or the whole section)
    is absent — see the comment above `_CONFIG_PATH` for why a fallback
    exists at all and why it is never what production actually uses.

    Raises whatever `open`/`json.load` raises for a missing or malformed
    file: mirrors `routing_check.load_config`'s no-try/except contract
    rather than silently swallowing a bad `config_path`, which would hide a
    real caller mistake (production always calls this with the default
    `_CONFIG_PATH`, which is checked into the repo).
    """
    with open(config_path, "r", encoding="utf-8") as stream:
        config = json.load(stream)
    section = config.get("critical_dialogue", {})
    threshold = section.get(
        "code_review_diff_line_threshold", DEFAULT_CODE_REVIEW_DIFF_LINE_THRESHOLD
    )
    patterns = section.get(
        "security_sensitive_path_patterns",
        list(DEFAULT_SECURITY_SENSITIVE_PATH_PATTERNS),
    )
    return int(threshold), tuple(patterns)


def needs_code_review_consultation(
    complexity: str,
    *,
    tests_failing: bool = False,
    diff_line_count: int = 0,
    changed_paths: Sequence[str] = (),
    config_path: Path = _CONFIG_PATH,
) -> bool:
    """Code-review occasion trigger (spec 0003's Triggers paragraph: "Code
    review: complexity >= Medium always, plus risk signals at any tier").

    Medium/Complex complexity fires unconditionally, exactly like
    `needs_plan_review_consultation` — checked first so a Medium+ task never
    pays for a config read it does not need. Below that, three independent
    risk signals each fire it on their own at any complexity tier,
    including Trivial: failing tests, a diff whose `diff_line_count`
    *exceeds* (strictly greater than, not equal to) the configured
    threshold, or any `changed_paths` entry matching a configured
    security-sensitive pattern (case-insensitive substring match, the same
    style `_detect_sensitivity_marker` above already uses for
    `SENSITIVITY_MARKERS`). The threshold and patterns are read from
    `config_path` via `_load_code_review_risk_config` on every call rather
    than cached at import time, so a caller (or a test) pointing this at a
    different file always observes that file's current values.
    """
    normalized = complexity.lower().strip()
    if normalized in ("medium", "complex"):
        return True
    if tests_failing:
        return True
    threshold, patterns = _load_code_review_risk_config(config_path)
    if diff_line_count > threshold:
        return True
    lowered_patterns = [pattern.lower() for pattern in patterns]
    for changed_path in changed_paths:
        lowered_path = changed_path.lower()
        if any(pattern in lowered_path for pattern in lowered_patterns):
            return True
    return False


def needs_post_mortem_consultation(
    *,
    occasion: Occasion | None = None,
    failed: bool = False,
    consecutive_failures: int = 0,
    stalemate_occurred: bool = False,
) -> bool:
    """Post-mortem occasion trigger (spec 0003's Triggers paragraph:
    "Post-mortem: every failure, escalation (the protocol's 2-failure
    rule), and stalemate").

    `occasion` identifies which occasion's outcome is being evaluated for a
    possible post-mortem — the occasion of the task or dialogue that
    failed, escalated, or stalemated. When `occasion == "post-mortem"`,
    this function always returns `False`, regardless of the other three
    arguments: ticket 03's acceptance criteria state the recursion-guard
    rule generally ("a post-mortem's own outcome must NOT recursively
    trigger another post-mortem") before giving a stalemate as the concrete
    example ("if a post-mortem dialogue itself stalemates, that must not
    spawn a further post-mortem"), so the guard here covers every signal a
    post-mortem's own outcome could carry, not only `stalemate_occurred`.
    Every other occasion (or `None`, when the caller has no occasion to
    report — e.g. a plain task-execution failure that never reached a
    dialogue at all) is eligible for all three triggers below.

    `consecutive_failures >= ESCALATION_FAILURE_THRESHOLD` tracks the same
    protocol rule `agent_council.escalate_routing_effort` already encodes as
    `attempts >= ESCALATION_FAILURE_THRESHOLD` (its own docstring: "Escalate
    reasoning effort and model tier after 2+ failed worker attempts")
    rather than inventing a new threshold or a new piece of session state.
    The two constants are separately defined — see the comment above
    `ESCALATION_FAILURE_THRESHOLD` for why this module cannot simply import
    `agent_council`'s — and kept in sync by a test rather than a shared
    import.
    """
    if occasion == "post-mortem":
        return False
    if failed:
        return True
    if consecutive_failures >= ESCALATION_FAILURE_THRESHOLD:
        return True
    if stalemate_occurred:
        return True
    return False


def _atomic_text_write(path: Path, content: str) -> None:
    """Write text without exposing a partially-written plan artifact."""
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


@dataclass(frozen=True)
class _MissionCopy:
    """The occasion-specific framing sentences `_build_planner_prompt` and
    `_build_critic_prompt` select between (spec 0003 ticket 01).

    One instance per `Occasion` value, held in `_MISSION_COPY` — an
    exhaustive mapping, not a partial one with a fallback, so selecting an
    occasion this module doesn't know about fails loudly (a `KeyError` from
    the lookup) rather than silently borrowing another occasion's mission.
    `ambiguity`'s two sentences and `artifact_label` are spec 0001's
    hardcoded prompt text verbatim: selecting it must reproduce
    byte-identical prompts to before this module became occasion-aware.
    The other three occasions' copy is deliberately minimal — proving the
    routing seam works is this ticket's job; writing each occasion's real
    mission content belongs to the ticket that wires its triggers.
    """

    planner_intro: str
    planner_revision_intro: str
    artifact_label: str
    critic_intro: str


_MISSION_COPY: dict[Occasion, _MissionCopy] = {
    "ambiguity": _MissionCopy(
        planner_intro=(
            "You are the Planner in an AdvisoryConsultation. Propose a "
            "concise, concrete implementation plan for the task below."
        ),
        planner_revision_intro=(
            "You are the Planner in an AdvisoryConsultation. The Critic did "
            "not approve your previous plan. Revise your plan to address "
            "the Critic's objection below."
        ),
        artifact_label="plan",
        critic_intro=(
            "You are the Critic in an AdvisoryConsultation. Judge the "
            "Planner's plan below on its merits."
        ),
    ),
    "plan-review": _MissionCopy(
        planner_intro=(
            "You are the Planner in a CriticalDialogue plan review. "
            "Propose a concise, concrete implementation plan for the task "
            "below."
        ),
        planner_revision_intro=(
            "You are the Planner in a CriticalDialogue plan review. The "
            "Critic did not approve your previous plan. Revise your plan "
            "to address the Critic's objection below."
        ),
        artifact_label="plan",
        critic_intro=(
            "You are the Critic in a CriticalDialogue plan review. Judge "
            "the Planner's plan below on its merits."
        ),
    ),
    "code-review": _MissionCopy(
        planner_intro=(
            "You are the Planner in a CriticalDialogue code review, "
            "defending the diff under review. Propose a concise, concrete "
            "rationale for the diff below."
        ),
        planner_revision_intro=(
            "You are the Planner in a CriticalDialogue code review. The "
            "Critic did not approve your previous defense of the diff. "
            "Revise it to address the Critic's objection below."
        ),
        artifact_label="diff defense",
        critic_intro=(
            "You are the Critic in a CriticalDialogue code review. Judge "
            "the diff below on its merits."
        ),
    ),
    "post-mortem": _MissionCopy(
        planner_intro=(
            "You are the Planner in a CriticalDialogue post-mortem. "
            "Propose a concise, concrete lesson to record for the failure "
            "below."
        ),
        planner_revision_intro=(
            "You are the Planner in a CriticalDialogue post-mortem. The "
            "Critic did not approve your previous lesson. Revise it to "
            "address the Critic's objection below."
        ),
        artifact_label="lesson",
        critic_intro=(
            "You are the Critic in a CriticalDialogue post-mortem. Judge "
            "the lesson below on its merits."
        ),
    ),
}


def _build_planner_prompt(
    task_description: str,
    *,
    occasion: Occasion = "ambiguity",
    previous_plan: str | None = None,
    critic_feedback: str | None = None,
) -> str:
    mission = _MISSION_COPY[occasion]
    if previous_plan is None or critic_feedback is None:
        return (
            f"{WORKER_MODE_TOKEN}\n"
            f"{mission.planner_intro}\n\n"
            f"Task: {task_description}"
        )
    return (
        f"{WORKER_MODE_TOKEN}\n"
        f"{mission.planner_revision_intro}\n\n"
        f"Task: {task_description}\n\n"
        f"Your previous {mission.artifact_label}:\n{previous_plan}\n\n"
        f"Critic's response:\n{critic_feedback}"
    )


def _build_critic_prompt(
    task_description: str, planner_plan: str, *, occasion: Occasion = "ambiguity"
) -> str:
    # spec 0003 ticket 02's VerdictContract, reversed from spec 0001: the
    # verdict line now comes LAST, after rationale and engagement units, not
    # first. See the textual-contract comment above `_QUOTE_LINE_PATTERN`
    # for the exact QUOTE:/N. line shapes `_parse_critic_verdict` reads —
    # this prompt and that parser must keep asking for and reading the same
    # shape, since later tickets (panel topology, canaries, telemetry) build
    # on this one.
    #
    # The prompt still asks for an exact verdict line: the tolerance added in
    # `_is_tolerant_revise` is a parser-side safety net for what real models
    # actually emit, not a relaxation of the contract we ask for. Asking for
    # exactness and parsing with tolerance are not in tension — the ask stays
    # strict so most responses need no tolerance at all.
    #
    # The engagement-unit and verdict-line instructions, and the closing
    # "Planner's plan:" label, stay fixed across every occasion, unlike the
    # intro above: they are the VerdictContract's territory, and ticket 02
    # needs one shared shape to extend across all four occasions rather than
    # four independently-drifting copies.
    #
    # "at least one verified quote" (not "quotes or objections"): only a
    # quote is mechanically checked against the plan text, so only a quote
    # is proof the Critic engaged with something real. Objections are
    # encouraged and still counted, but — per `_parse_critic_verdict` — they
    # can never substitute for a quote in the approval decision, so the
    # prompt must not imply they can.
    mission = _MISSION_COPY[occasion]
    return (
        f"{WORKER_MODE_TOKEN}\n"
        f"{mission.critic_intro}\n\n"
        "Write your rationale first. Before you verdict, show your "
        "engagement with it: quote the exact passages you are judging, one "
        "per line, as QUOTE: \"<verbatim text copied from what you were "
        "given>\", and list any concrete objections as a numbered list, one "
        "per line, like \"1. <objection>\". End your response with exactly "
        f"one verdict line, LAST: either \"{CRITIC_VERDICT_APPROVE}\" if it "
        "is sound as written, or \"VERDICT: REVISE\" if it is not. An "
        "APPROVE backed by zero verified quotes will be treated as invalid, "
        "even if it lists objections.\n\n"
        f"Task: {task_description}\n\n"
        f"Planner's plan:\n{planner_plan}"
    )


def _is_tolerant_revise(upper_line: str) -> bool:
    """True when `upper_line` opens with "VERDICT: REVISE" followed by
    end-of-line or a non-alphanumeric separator.

    Deliberately not a bare `str.startswith`: that would also match
    "VERDICT: REVISED PLAN ATTACHED" and "VERDICT: REVISEMENT", neither of
    which is the Critic asking for a revision round. Requiring the character
    right after the prefix to be either absent or non-alphanumeric is what
    tells "REVISE" apart from a word that merely starts with it.

    This tolerance has no APPROVE counterpart, and that asymmetry is
    intentional: an unparseable response must fail closed, and folding a
    near-miss APPROVE into approval would risk reporting a consensus nobody
    granted. A near-miss REVISE carries no such risk — at worst it continues
    the revision loop, which is what the Critic asked for anyway. See
    `_parse_critic_verdict`.
    """
    if not upper_line.startswith(CRITIC_VERDICT_REVISE):
        return False
    remainder = upper_line[len(CRITIC_VERDICT_REVISE) :]
    if not remainder:
        return True
    return not remainder[0].isalnum()


@dataclass(frozen=True)
class VerdictContractResult:
    """The result of parsing a Critic response under the VerdictContract
    (spec 0003 ticket 02).

    `verdict` is the same three-way `CriticVerdict` spec 0001 shipped —
    every existing call site's approved/unparseable/revise branching keeps
    meaning exactly what it always meant, so this is additive, not a
    replacement type. `verified_quote_count` and `objection_count` are new:
    the engagement-unit tally that justifies (or fails to justify) an
    "approved" verdict, carried on the result so a caller — or a later
    ticket's telemetry (ticket 10) — can observe not just whether the
    Critic approved, but whether it earned that approval.

    An "approved" verdict is only ever returned when
    `verified_quote_count >= 1`; see `_parse_critic_verdict` for the exact
    rule. This is deliberately NOT symmetric with `objection_count`: a quote
    is mechanically checked against the artifact text (see
    `_count_engagement_units`), so it is evidence the Critic actually read
    something real; an objection is free text with no verification at all,
    so any number of objections with zero verified quotes is exactly as
    trustworthy as zero engagement — a Critic could fabricate ten numbered
    objections about a plan it never opened. `objection_count` is still
    carried on every result (including "unparseable" ones) as a genuine
    engagement signal for a caller or telemetry to read, it just cannot by
    itself unlock "approved". A bare or fabricated approval instead comes
    back as `verdict="unparseable"`, carrying whatever counts were actually
    found — the same fail-closed state a genuinely malformed response gets,
    on purpose: an approval that cannot be trusted is exactly as
    untrustworthy as one that cannot be read at all, so this module gives
    both the same verdict rather than inventing a fourth state every call
    site would then have to learn to distinguish.
    """

    verdict: CriticVerdict
    verified_quote_count: int
    objection_count: int


def _split_off_verdict_line(critic_response: str) -> tuple[str | None, list[str]]:
    """Split `critic_response` into its verdict line and everything before it.

    The verdict line is the LAST non-empty line (spec 0003 ticket 02 moved
    it there from spec 0001's first line, to make room for rationale and
    engagement units ahead of it). Everything before that line — the
    "body" — is where `_count_engagement_units` looks for quotes and
    objections; trailing blank lines after the verdict line are simply
    skipped, not treated as a second body. Returns `(None, [])` when the
    response has no non-empty line at all.
    """
    lines = critic_response.splitlines()
    for index in range(len(lines) - 1, -1, -1):
        if lines[index].strip():
            return lines[index].strip(), lines[:index]
    return None, []


def _count_engagement_units(body_lines: list[str], artifact_text: str) -> tuple[int, int]:
    """Count verified quotes and numbered objections among `body_lines`.

    Returns `(verified_quote_count, objection_count)`. See the
    VerdictContract comment above `_QUOTE_LINE_PATTERN` for the exact line
    shapes recognised. A quote line whose text does not verify against
    `artifact_text` is dropped silently — it contributes to neither count —
    rather than being counted as a malformed line of any kind.
    """
    verified_quotes = 0
    objections = 0
    for line in body_lines:
        stripped = line.strip()
        quote_match = _QUOTE_LINE_PATTERN.match(stripped)
        if quote_match:
            candidate = quote_match.group(1).strip()
            if len(candidate) >= 2 and candidate[0] == '"' and candidate[-1] == '"':
                candidate = candidate[1:-1]
            if candidate and candidate in artifact_text:
                verified_quotes += 1
            continue
        if _OBJECTION_LINE_PATTERN.match(stripped):
            objections += 1
    return verified_quotes, objections


def _parse_critic_verdict(
    critic_response: str, artifact_text: str
) -> VerdictContractResult:
    """Parse `critic_response` under the VerdictContract, verifying quotes
    against `artifact_text` — the reviewed artifact (the Planner's plan on
    today's sole wired occasion; a diff or a lesson on later ones).

    Absence of rejection is still not agreement (spec 0001's rule,
    unchanged, now extended to bare/fabricated approval per spec 0003):
    only an exact "VERDICT: APPROVE" last line counts as an approval
    attempt at all — no prefix matching, no punctuation or trailing-text
    tolerance, ever, because a wrongly-inferred approval would report a
    consensus nobody granted — and even then only when it is backed by at
    least one *verified* quote (`verified_quote_count >= 1`). Objections do
    NOT substitute for a quote here, on purpose: spec 0003's VerdictContract
    paragraph is one-directional — "zero objections is valid only alongside
    verified quotes" — never the mirror "zero quotes is valid alongside
    objections". Quotes are the only engagement unit this function checks
    against reality (`_count_engagement_units` verifies each one
    byte-for-byte against `artifact_text`); a numbered objection is free
    text a Critic can fabricate without having read anything, so it cannot
    by itself earn an approval — it can only ride along with at least one
    quote that already did. A bare or fabricated approval (zero verified
    quotes, regardless of objection_count) parses as "unparseable", the
    same fail-closed state a response with no readable verdict line at all
    gets — see `VerdictContractResult`.

    "VERDICT: REVISE" is still read tolerantly instead (see
    `_is_tolerant_revise`), and carries no engagement-unit requirement: a
    rejection that keeps the loop going risks nothing a bare approval does,
    exactly the asymmetry spec 0001 already established.
    """
    verdict_line, body_lines = _split_off_verdict_line(critic_response)
    if verdict_line is None:
        return VerdictContractResult("unparseable", 0, 0)

    verified_quotes, objections = _count_engagement_units(body_lines, artifact_text)
    upper = verdict_line.upper()

    if upper == CRITIC_VERDICT_APPROVE:
        # Quotes only: `objections` is never part of this gate. A quote is
        # mechanically verified against `artifact_text` above; an objection
        # is unverified free text, so it cannot substitute for one. See the
        # docstring above and on `VerdictContractResult` for why this is
        # deliberately asymmetric rather than `verified_quotes + objections`.
        if verified_quotes == 0:
            return VerdictContractResult("unparseable", verified_quotes, objections)
        return VerdictContractResult("approved", verified_quotes, objections)

    if _is_tolerant_revise(upper):
        return VerdictContractResult("revise", verified_quotes, objections)

    return VerdictContractResult("unparseable", verified_quotes, objections)


def _detect_sensitivity_marker(text: str) -> str | None:
    """Return the first `SENSITIVITY_MARKERS` entry found in `text`, or None.

    Returns the marker constant itself, never the surrounding text it
    matched against — the caller reports this back as the reason a
    consultation halted, so the marker name alone must be able to explain
    the halt without ever repeating the task text or the secret value that
    tripped it.
    """
    lowered = text.lower()
    for marker in SENSITIVITY_MARKERS:
        if marker.lower() in lowered:
            return marker
    return None


def _remove_stale_plan_artifact(plan_path: Path) -> str | None:
    """Ensure no plan artifact survives a non-consensus exit.

    Only this one path is touched — the module owns nothing else under
    `root_dir`. Cleanup failure (e.g. `plan_path` is a directory, or its
    parent is unwritable) must not raise out of the consultation and must
    not replace whatever error actually caused the non-consensus exit — it
    is reported back to the caller instead, who folds it into the result.

    The marker-only redaction boundary covers task text: nothing derived
    from `task_description` reaches a reason beyond the matched marker
    constant. A cleanup suffix may instead name `plan_path`, which comes
    from caller-injected `root_dir` (the repository root in production),
    never from the task. Suppressing that suffix was deliberately rejected:
    its OSError text tells the operator why a stale plan survived the halt,
    and already contains the path, so redaction would discard the diagnostic
    rather than make it shorter.
    """
    try:
        plan_path.unlink(missing_ok=True)
    except OSError as exc:
        return f"failed to remove stale plan artifact at {plan_path}: {exc}"
    return None


def _fold_error(existing: str | None, addition: str | None) -> str | None:
    """Combine a possibly-absent primary error with a possibly-absent addition.

    Used at every point in this module where a secondary failure can occur
    alongside a primary one: the `_result` choke point, where the primary
    error is itself optional depending on which exit path is folding a
    transcript- or telemetry-write failure into it, and the outcome branches
    above it, where the primary error (a halt reason or a caught worker
    exception) is always present but a cleanup failure alongside it may or
    may not be. Same non-negotiable rule everywhere it's called, and the one
    `_remove_stale_plan_artifact` documents too: a secondary I/O failure is
    reported, never allowed to mask or replace the primary outcome.
    """
    if addition is None:
        return existing
    if existing is None:
        return addition
    return f"{existing}; {addition}"


def _default_task_id(task_description: str) -> str:
    """Derive a stable task identity from `task_description` when the caller
    supplied none.

    A truncated SHA-256 hex digest, never the task text itself. This is the
    default for every outcome except `sensitivity_halt` — see
    `_resolve_task_id` for why a halt cannot use it: a digest, however
    non-reversible, is still a confirmation oracle over guessable task text,
    and the redaction boundary around a halt forbids anything derived from
    `task_description` at all.
    """
    return hashlib.sha256(task_description.encode("utf-8")).hexdigest()[:16]


def _resolve_task_id(
    task_description: str, task_id: str | None, outcome: AdvisoryOutcome
) -> str:
    """Resolve the task identity `_result` emits to both artifacts for one outcome.

    A caller-supplied `task_id` always wins, on every outcome: the caller
    chose it, so it carries none of the risk a value this module derived
    would. Absent one, every outcome but `sensitivity_halt` falls back to
    `_default_task_id` — a stable digest of `task_description`, safe to
    reuse across runs of the same task.

    `sensitivity_halt` is the one outcome that must never fall back to that
    digest: the module's redaction boundary (see `_detect_sensitivity_marker`
    and `_render_sensitivity_halt_transcript`) promises nothing derived from
    `task_description` escapes a halt, and a digest over guessable task text
    is exactly the confirmation oracle that promise rules out. Its default is
    instead a random identity, unrelated to the task text, generated fresh
    per halt — an auditor can still count and correlate distinct halts
    against their transcripts by this id, just not recover anything about
    what was halted from it.
    """
    if task_id is not None:
        return task_id
    if outcome == "sensitivity_halt":
        return secrets.token_hex(8)
    return _default_task_id(task_description)


def _render_consultation_transcript(
    task_description: str, result: AdvisoryDebateResult
) -> str:
    """Render the round-by-round transcript for every outcome except a halt.

    Reached for every outcome except `sensitivity_halt` (see
    `_render_sensitivity_halt_transcript` for that redacted counterpart), so
    `task_description` and each round's full Planner/Critic text are fair
    game here — that is the entire point of a transcript. Takes the already-
    built `result` rather than its individual fields: `outcome`, `rounds`,
    `planner_model`, and `critic_model` are its own field set, and threading
    them through as a separate parameter clump would just re-derive what the
    result object already carries.
    """
    rounds = result.rounds
    lines = [
        "# AdvisoryConsultation Transcript",
        "",
        f"**Outcome:** {result.outcome}",
        f"**Planner:** {result.planner_model}",
        f"**Critic:** {result.critic_model}",
        f"**Rounds run:** {len(rounds)}",
        "",
        "## Task",
        "",
        task_description,
        "",
    ]
    if not rounds:
        lines.append("_No rounds were run._")
    for index, round_ in enumerate(rounds, start=1):
        lines.extend(
            [
                f"## Round {index}",
                "",
                f"### Planner ({result.planner_model})",
                "",
                round_.planner_proposal,
                "",
                f"### Critic ({result.critic_model})",
                "",
                round_.critic_response,
                "",
            ]
        )
    return "\n".join(lines)


def _render_sensitivity_halt_transcript(marker: str, task_id: str) -> str:
    """Render the redacted transcript written on a `sensitivity_halt`.

    The redaction boundary documented on `_detect_sensitivity_marker`: nothing
    derived from the task text may appear here, only the matched marker
    constant, the halt's `task_id`, and the fact that human approval is
    required. `marker` is required, not optional: a halt with no marker to
    report is a programming error inside this module (the only call site
    only reaches this function when `_detect_sensitivity_marker` already
    found one), so it must be impossible to construct this transcript with
    nothing to blame the halt on rather than silently rendering an empty
    explanation. `task_id` is included so this transcript and the
    `sensitivity_halt` telemetry record for the same halt can be correlated
    by an auditor — see `_resolve_task_id`.
    """
    return "\n".join(
        [
            "# AdvisoryConsultation Transcript",
            "",
            "**Outcome:** sensitivity_halt",
            f"**Task ID:** {task_id}",
            "",
            (
                f"Task text matched sensitivity marker `{marker}`. Human approval "
                "is required before this task may proceed."
            ),
            "",
            "No Planner or Critic was contacted. No task details are recorded here.",
        ]
    )


def _write_transcript(path: Path, content: str) -> str | None:
    """Write the transcript fresh (never appended) so a stale transcript from
    an earlier run can never survive. Failure is reported, never raised."""
    try:
        _atomic_text_write(path, content)
    except OSError as exc:
        return f"failed to write consultation transcript at {path}: {exc}"
    return None


def _append_jsonl_locked(path: Path, record: dict[str, object]) -> None:
    """Append one JSON record to `path` under an exclusive advisory lock.

    Mirrors `agent_council.append_jsonl_locked` rather than importing it:
    importing `agent_council` would pull `urllib.request`, `asyncio`, and its
    own `fcntl` usage into a module whose docstring promises offline
    exercisability, and these files are loaded by path rather than as a
    package (see the identical precedent on `SENSITIVITY_MARKERS`).
    `test_routing.py` asserts this writes to the same path
    `AgentCouncil.telemetry_file` uses AND that the two writers produce
    byte-identical output for the same record, so neither the log path nor
    the record encoding (`sort_keys`, the trailing newline) can silently
    drift apart from that precedent. The lock semantics (`fcntl.flock`
    itself) are NOT covered by that or any other test — a byte comparison
    of the two output files cannot observe locking behaviour.
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


@dataclass(frozen=True)
class AdvisoryTelemetryRecord:
    """The one structured telemetry record a consultation emits.

    A dataclass, like every other concept in this module, rather than a bare
    mapping — the dataclass is what carries the field contract; `to_mapping`
    is just its JSON-serialisable wire form for `_append_jsonl_locked`.
    Carries only the derived/supplied task identity — never task text or a
    matched secret value — alongside rounds run, outcome, and both model
    names, so an auditor can tell which decisions were genuinely deliberated
    without the log becoming a second place secrets can leak from.

    `kind` is the field that makes Spec 0001 US 12 joinable: both
    `AgentCouncil` and this module append to the same
    `.ralph/routing_telemetry.jsonl` stream, and this is the only field that
    tells the two record families apart. It is deliberately one-sided — a
    council record carries no `kind` at all, rather than a matching
    `"council_decision"` value — because `agent_council.log_routing_telemetry`'s
    record shape is asserted by its own tests and is off-limits for this
    module to change. An auditor reads the absence of `kind` as "council
    decision"; do not "helpfully" normalise that asymmetry away later by
    adding the field to both sides, or the join breaks.
    """

    timestamp: str
    task_id: str
    rounds_run: int
    outcome: AdvisoryOutcome
    planner_model: str
    critic_model: str
    kind: str = "advisory_consultation"

    def to_mapping(self) -> dict[str, object]:
        """The JSON-serialisable wire form `_write_telemetry_record` writes.

        `dataclasses.asdict` rather than a hand-enumerated dict: a
        hand-enumerated field list is exactly the kind of duplication this
        module refuses everywhere else (see `SENSITIVITY_MARKERS` and
        `_append_jsonl_locked`) unless a test pins it against drift — asdict
        removes the duplication instead of needing that guard. Field order
        does not matter: `_append_jsonl_locked` writes with `sort_keys=True`.
        """
        return dataclasses.asdict(self)


def _build_telemetry_record(
    result: AdvisoryDebateResult, *, task_id: str
) -> AdvisoryTelemetryRecord:
    """Build the one telemetry record a consultation emits.

    Takes the already-built `result` rather than its individual fields, for
    the same reason `_render_consultation_transcript` does: `rounds_run`,
    `outcome`, `planner_model`, and `critic_model` are the result's own
    field set. `task_id` is passed separately because it genuinely isn't —
    the result carries no task identity, by design (see `AdvisoryDebateResult`
    and `_resolve_task_id`).
    """
    return AdvisoryTelemetryRecord(
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        task_id=task_id,
        rounds_run=result.rounds_run,
        outcome=result.outcome,
        planner_model=result.planner_model,
        critic_model=result.critic_model,
    )


def _write_telemetry_record(path: Path, record: AdvisoryTelemetryRecord) -> str | None:
    """Render `record` to its wire form and write it. Failure is reported, never raised."""
    try:
        _append_jsonl_locked(path, record.to_mapping())
    except OSError as exc:
        return f"failed to write consultation telemetry at {path}: {exc}"
    return None


def _build_stalemate_report(
    planner_position: str, critic_position: str
) -> AdvisoryStalemateReport:
    return AdvisoryStalemateReport(
        planner_position=planner_position,
        critic_position=critic_position,
        options=(
            AdvisoryResolutionOption(1, "Approve Planner Architecture", planner_position),
            AdvisoryResolutionOption(2, "Approve Critic Architecture", critic_position),
            AdvisoryResolutionOption(
                3,
                "Escalate to Human Decision",
                "Halt execution and request user review",
            ),
        ),
    )


def run_advisory_consultation_debate(
    task_description: str,
    invoke_worker: InvokeWorker | None = None,
    *,
    root_dir: Path,
    occasion: Occasion = "ambiguity",
    max_rounds: int = MAX_DEBATE_ROUNDS,
    planner_model: str = "Claude Opus 5 (Thinking)",
    critic_model: str = "Codex 5.6 Sol",
    planner_effort: str = "high",
    critic_effort: str = "high",
    task_id: str | None = None,
) -> AdvisoryDebateResult:
    """Run the Planner/Critic exchange, revising on objection, and report the outcome.

    Round 1: the Planner proposes a plan from the task description alone,
    and the Critic judges it. If the Critic approves, the agreed plan is
    written to ``root_dir / "implementation_plan.md"`` and consensus is
    reported for that round. A failure writing that file is folded into
    the result's ``error`` field rather than raised: the Critic genuinely
    approved, so the outcome stays ``consensus`` and ``final_plan`` still
    carries the agreed text even though it never reached disk — a caller
    that trusts ``final_plan`` without also checking ``error`` is the one
    place in this module where that trust is not yet backed by the file
    system. Otherwise the Planner is asked again, this time holding its
    previous plan and the Critic's objection, and the exchange repeats up
    to ``max_rounds`` times.

    Four ways this can end without consensus, and each fails closed the
    same way — no plan artifact, no winner picked, the failure visible on
    the result:

    - Sensitivity halt: the task text itself carries a secret, credential,
      or personal-data marker. Checked before any worker is contacted — no
      Planner proposal, no Critic verdict, nothing sensitive ever leaves
      this process. The result names which marker tripped the halt (never
      the surrounding text) and states that human approval is required.
    - Stalemate: every round runs and none is approved. The result carries
      both final positions and three resolution options.
    - Unparseable verdict: a Critic response has no readable verdict line.
      This ends the consultation immediately rather than being silently fed
      back to the Planner as if it were a reasoned rejection.
    - Worker error: ``invoke_worker`` raises. The exception is caught (never
      ``BaseException``, so Ctrl-C still propagates) and its message is
      carried on the result.

    A pre-existing ``implementation_plan.md`` under ``root_dir`` from an
    earlier run is removed on every one of these four exits, so the
    artifact on disk is never staler than the result describing it.

    Every one of the five outcomes — including consensus — writes a fresh,
    human-readable transcript to ``root_dir / ".scratch" / "planning_debate.md"``
    (never appended, so a stale transcript can't survive) and emits exactly
    one structured telemetry record to
    ``root_dir / ".ralph" / "routing_telemetry.jsonl"``. On a
    ``sensitivity_halt`` the transcript carries only the matched marker
    constant, never the task text; every other outcome's transcript carries
    the full task description and each round's Planner/Critic exchange. The
    telemetry record never carries task text or a matched secret value on
    any path — only ``task_id``, rounds run, outcome, and both model names.
    ``task_id`` is the ``task_id`` keyword argument when supplied; otherwise
    it defaults to a truncated SHA-256 digest of ``task_description`` for
    every outcome except ``sensitivity_halt``, and to a random identity,
    unrelated to the task text, for that one outcome (a digest is itself a
    confirmation oracle over guessable task text — see ``_resolve_task_id``).
    On a halt the same resolved id appears on both the transcript and the
    telemetry record, so the two stay correlated for an auditor even though
    neither carries the task text. A failure writing either artifact is
    folded into the result's ``error`` field rather than raised or allowed
    to replace the primary outcome.

    ``occasion`` (spec 0003 ticket 01) selects which mission the Planner and
    Critic prompts carry — see ``_MISSION_COPY``. It defaults to
    ``"ambiguity"``, spec 0001's sole occasion, so a call site that never
    mentions it behaves exactly as before this parameter existed: same
    prompts, same outcomes, same call counts. It is recorded on the returned
    ``AdvisoryDebateResult`` so a caller can observe which occasion ran.
    Wiring the other three occasions' real trigger predicates and blocking
    stance is later tickets' work, not this function's.

    Raises ``ValueError`` if ``max_rounds`` is not at least 1, or if
    ``occasion`` is not one of the four ``Occasion`` values: both are
    programming errors at the call site, not a genuine Planner-Critic
    disagreement, and must not be reported back as a fabricated stalemate.
    Validation deliberately precedes the sensitivity gate, so a sensitive
    task with an invalid `max_rounds` or `occasion` raises rather than
    returning a `sensitivity_halt` result. Neither ordering contacts a
    worker, so both preserve the security boundary; only the caller-facing
    report differs. The exception is the louder halt because it cannot be
    ignored, whereas reordering would give a genuine call-site bug a
    plausible-looking result instead of the error that says the caller's
    code is wrong.
    """
    if max_rounds < 1:
        raise ValueError(f"max_rounds must be >= 1, got {max_rounds}")
    if occasion not in _MISSION_COPY:
        raise ValueError(
            f"unknown occasion {occasion!r}; expected one of {tuple(_MISSION_COPY)}"
        )

    rounds: list[AdvisoryDebateRound] = []
    previous_plan: str | None = None
    previous_critique: str | None = None
    plan_path = root_dir / "implementation_plan.md"
    transcript_path = root_dir / ".scratch" / "planning_debate.md"
    telemetry_path = root_dir / ".ralph" / "routing_telemetry.jsonl"

    def _result(
        outcome: AdvisoryOutcome,
        *,
        final_plan: str = "",
        stalemate: AdvisoryStalemateReport | None = None,
        error: str | None = None,
        sensitivity_marker: str | None = None,
    ) -> AdvisoryDebateResult:
        """The single choke point every return passes through.

        Writing the transcript and telemetry record here — rather than at
        each call site — makes "every exit path gets both artifacts" a
        structural guarantee instead of six remembered writes. Task identity
        is resolved here too, per outcome, rather than once up front: the
        `sensitivity_halt` default (a random id) must never be the same code
        path as every other outcome's default (a digest of the task text) —
        see `_resolve_task_id`. Because this closure runs at most once per
        call to `run_advisory_consultation_debate` (every branch below
        returns immediately through it), resolving per-call here is exactly
        as "once" as resolving up front would have been.
        """
        # Provisional: its `error` is pre-fold (the transcript- and
        # telemetry-write failures below haven't been folded in yet), and it
        # is what the renderers below actually see. The caller instead gets
        # the post-fold `AdvisoryDebateResult` built at the end of this
        # function. This gap is inherent — a write's own error can't be known
        # before the write happens — so a renderer must never read `.error`
        # off this object; neither renderer does today, but nothing enforces
        # that beyond this comment.
        provisional_result = AdvisoryDebateResult(
            rounds_run=len(rounds),
            final_plan=final_plan,
            outcome=outcome,
            occasion=occasion,
            planner_model=planner_model,
            critic_model=critic_model,
            rounds=tuple(rounds),
            stalemate=stalemate,
            error=error,
        )

        resolved_task_id = _resolve_task_id(task_description, task_id, outcome)

        if outcome == "sensitivity_halt":
            if sensitivity_marker is None:
                raise ValueError(
                    "sensitivity_halt outcome requires a sensitivity_marker "
                    "(programming error: this module's only sensitivity_halt "
                    "call site always supplies one)"
                )
            transcript = _render_sensitivity_halt_transcript(
                sensitivity_marker, resolved_task_id
            )
        else:
            transcript = _render_consultation_transcript(task_description, provisional_result)
        folded_error = _fold_error(
            provisional_result.error, _write_transcript(transcript_path, transcript)
        )

        record = _build_telemetry_record(provisional_result, task_id=resolved_task_id)
        folded_error = _fold_error(
            folded_error, _write_telemetry_record(telemetry_path, record)
        )

        return dataclasses.replace(provisional_result, error=folded_error)

    marker = _detect_sensitivity_marker(task_description)
    if marker is not None:
        cleanup_error = _remove_stale_plan_artifact(plan_path)
        reason = (
            f"human approval required: task text matched sensitivity marker '{marker}'"
        )
        return _result(
            "sensitivity_halt",
            error=_fold_error(reason, cleanup_error),
            sensitivity_marker=marker,
        )

    if invoke_worker is None:
        try:
            from production_invoker import invoke_worker as production_invoke_worker
        except Exception as exc:  # noqa: BLE001 - a production worker failure fails closed.
            cleanup_error = _remove_stale_plan_artifact(plan_path)
            return _result("worker_error", error=_fold_error(str(exc), cleanup_error))
        invoke_worker = production_invoke_worker

    for _round_number in range(1, max_rounds + 1):
        planner_prompt = _build_planner_prompt(
            task_description,
            occasion=occasion,
            previous_plan=previous_plan,
            critic_feedback=previous_critique,
        )
        try:
            planner_plan = invoke_worker(planner_model, planner_effort, planner_prompt)
        except Exception as exc:  # noqa: BLE001 - a worker failure must fail closed.
            cleanup_error = _remove_stale_plan_artifact(plan_path)
            return _result("worker_error", error=_fold_error(str(exc), cleanup_error))

        critic_prompt = _build_critic_prompt(
            task_description, planner_plan, occasion=occasion
        )
        try:
            critic_response = invoke_worker(critic_model, critic_effort, critic_prompt)
        except Exception as exc:  # noqa: BLE001 - a worker failure must fail closed.
            cleanup_error = _remove_stale_plan_artifact(plan_path)
            return _result("worker_error", error=_fold_error(str(exc), cleanup_error))

        rounds.append(AdvisoryDebateRound(planner_plan, critic_response))
        # `planner_plan` is the reviewed artifact: the VerdictContract
        # (spec 0003 ticket 02) verifies the Critic's quotes against exactly
        # what the Critic was shown as "Planner's plan" in `critic_prompt`
        # above, never against the task description or anything else.
        verdict_result = _parse_critic_verdict(critic_response, planner_plan)

        if verdict_result.verdict == "approved":
            write_error: str | None = None
            try:
                _atomic_text_write(plan_path, planner_plan)
            except OSError as exc:
                write_error = f"failed to write plan artifact at {plan_path}: {exc}"
            return _result("consensus", final_plan=planner_plan, error=write_error)

        if verdict_result.verdict == "unparseable":
            cleanup_error = _remove_stale_plan_artifact(plan_path)
            return _result("unparseable_verdict", error=cleanup_error)

        previous_plan = planner_plan
        previous_critique = critic_response

    cleanup_error = _remove_stale_plan_artifact(plan_path)
    stalemate = _build_stalemate_report(previous_plan or "", previous_critique or "")
    return _result("stalemate", stalemate=stalemate, error=cleanup_error)
