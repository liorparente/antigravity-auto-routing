"""Pure VerdictContract parsing for AdvisoryConsultation.

The consultation facade owns worker coordination, persistence, and telemetry;
this module owns only the critic-response contract.  Keeping the parser pure
means quote verification and fail-closed verdict decisions can be tested with
plain strings, without a worker, filesystem, or debate state machine.

The contract deliberately treats approval more strictly than revision.  An
approval needs a verbatim quote verified against the reviewed artifact, while
a revision may be parsed tolerantly because continuing a revision loop cannot
create a false consensus.  This preserves spec 0001's core rule: absence of
rejection is never agreement.
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

Occasion = Literal["ambiguity", "plan-review", "code-review", "post-mortem"]
AdvisoryOutcome = Literal[
    "consensus",
    "stalemate",
    "unparseable_verdict",
    "worker_error",
    "sensitivity_halt",
    "security_halt",
    "canary",
    "budget_skipped",
]


@dataclass(frozen=True)
class AdvisoryResolutionOption:
    """One way a human can resolve a stalled consultation."""

    id: int
    label: str
    description: str


@dataclass(frozen=True)
class AdvisoryStalemateReport:
    """The final positions of an unresolved consultation and resolution options."""

    planner_position: str
    critic_position: str
    options: tuple[AdvisoryResolutionOption, AdvisoryResolutionOption, AdvisoryResolutionOption]
    critic_b_position: str | None = None

CRITIC_VERDICT_APPROVE = "VERDICT: APPROVE"
CRITIC_VERDICT_REVISE = "VERDICT: REVISE"

# The Critic's verdict line, once read, is one of these three states.
# "unparseable" is deliberately not folded into "revise": a malformed
# response must halt the consultation, not be fed back to the Planner as if
# it were a reasoned objection. See `VerdictContractResult` below for the
# richer shape `_parse_critic_verdict` actually returns (spec 0003 ticket
# 02): this three-way type is still exactly what a caller branches on, just
# no longer the parser's entire return value.
CriticVerdict = Literal["approved", "revise", "unparseable"]

# Spec 0003 (CriticalDialogue) ticket 02: the VerdictContract's textual
# shape. Nothing in the spec pins the literal syntax below — that choice
# belongs to this ticket, and it is load-bearing for every later ticket that
# reads a Critic response (panel topology, canaries, telemetry), so it is
# documented once, here, rather than re-derived from `_parse_critic_verdict`
# each time.
#
# A Critic response must contain, in order:
#
#   1. Free-text rationale — any prose, any number of lines, no marker.
#   2. Zero or more "engagement units", each occupying exactly one line,
#      interleaved with rationale text in any order, but all of them before
#      the verdict line:
#        - A QUOTE line: stripped text starting, case-insensitively, with
#          "QUOTE:", e.g. `QUOTE: "text copied verbatim from the artifact"`.
#          One leading and one trailing '"' are stripped from the remainder
#          if present (the quote marks are a convention for the Critic to
#          follow, not something the parser requires). What remains is
#          checked for verbatim (byte-for-byte) containment in the artifact
#          text `_parse_critic_verdict` is given — the Planner's plan on
#          today's occasion, a diff or a lesson on later ones. A quote that
#          fails this check is silently dropped: it does not count toward
#          `verified_quote_count`, and it does NOT by itself make the
#          response unparseable — see `_parse_critic_verdict`.
#        - An OBJECTION line: stripped text matching `^\\d+\\.\\s+\\S`, e.g.
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
# `verified_quote_count >= 1` for that response — `objection_count` is
# tallied alongside it but never substitutes for a verified quote. This is
# deliberately asymmetric, not `verified_quote_count + objection_count >=
# 1`: a quote is mechanically checked, byte-for-byte, against the artifact
# text, so it is evidence the Critic actually engaged with something real;
# an objection is free text with no verification at all, so any number of
# objections backed by zero verified quotes is exactly as untrustworthy as
# zero engagement — a Critic could fabricate ten numbered objections about
# a plan it never read. A bare or fabricated APPROVE (zero verified quotes,
# regardless of objection_count) parses as "unparseable" instead (see
# `_parse_critic_verdict`). REVISE carries no such requirement: it is read
# exactly as tolerantly, and exactly as unconditionally, as spec 0001 always
# read it.
_QUOTE_LINE_PATTERN = re.compile(r"(?i)^QUOTE:\s*(.*)$")
_OBJECTION_LINE_PATTERN = re.compile(r"^\d+\.\s+\S")


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


@dataclass(frozen=True)
class AdvisoryRoundVerdict:
    """One round's parsed Critic verdict(s) plus their engagement-unit
    counts (spec 0003 ticket 10).

    `run_advisory_consultation_debate`'s round loop already computes a
    `VerdictContractResult` per Critic per round via `_parse_critic_verdict`
    — to decide consensus/continue/unparseable — and previously discarded it
    once that decision was made. This dataclass is what retains it instead,
    one instance per round, so the same already-derived verdict+counts data
    can reach `AdvisoryDebateResult.round_verdicts` and, from there,
    `AdvisoryTelemetryRecord.round_verdicts`, for spec 0004's future
    LearningJournal to read.

    `critic_a` is the sole Critic's verdict in pair mode, and Critic A's
    verdict in panel mode — the identical "`critic_a` means the pair's sole
    Critic" convention `RosterRole` and `AdvisoryDebateRound.critic_response`
    already established. `critic_b` stays `None` for every pair-mode round
    (including a canary's single-Critic probe — ticket 08's fixture round
    never invokes a second Critic, regardless of which topology the
    occasion/complexity combination would otherwise select) and is
    populated only for a panel-mode round, where it carries Critic B's own
    independently parsed verdict — never folded together with `critic_a`
    into one shared tally, so a caller can always tell the two Critics'
    engagement apart.

    Deliberately a `VerdictContractResult` field on each side, not a
    hand-copied subset of its three fields: `VerdictContractResult` already
    *is* exactly "a verdict plus its engagement-unit counts" (see its own
    docstring), so wrapping it here — rather than re-declaring
    `verdict`/`verified_quote_count`/`objection_count` a second time — keeps
    this module's one existing representation of that shape as the only
    one, with no risk of the two silently drifting apart.

    Carries no plan or critique text on either side, only what
    `_parse_critic_verdict` already derived and summarized from it — a
    verdict label and two integers. That is what makes this data safe to
    cross the telemetry redaction boundary (see `AdvisoryTelemetryRecord`):
    it is derived-then-summarized data, never the raw Planner/Critic prose
    it was derived from, and never the task description either.
    """

    critic_a: VerdictContractResult
    critic_b: VerdictContractResult | None = None


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


def extract_quotes(body_lines: list[str]) -> list[str]:
    """Extract ``QUOTE:`` engagement text, removing one surrounding quote pair.

    The returned text is deliberately not verified here: callers may inspect
    exactly what a Critic supplied before choosing the artifact to verify it
    against.  Whitespace around the quote payload is formatting, not content.
    """
    quotes: list[str] = []
    for line in body_lines:
        quote_match = _QUOTE_LINE_PATTERN.match(line.strip())
        if quote_match is None:
            continue
        candidate = quote_match.group(1).strip()
        if len(candidate) >= 2 and candidate[0] == '"' and candidate[-1] == '"':
            candidate = candidate[1:-1]
        quotes.append(candidate)
    return quotes


def extract_objections(body_lines: list[str]) -> list[str]:
    """Extract numbered objection lines in their normalized contract form."""
    return [line.strip() for line in body_lines if _OBJECTION_LINE_PATTERN.match(line.strip())]


def verify_quotes(quotes: Sequence[str], artifact_text: str) -> list[str]:
    """Return quotes that occur verbatim in ``artifact_text``.

    Empty payloads are excluded because they are not evidence that the Critic
    engaged with the artifact, despite Python considering an empty string a
    substring of every string.
    """
    return [quote for quote in quotes if quote and quote in artifact_text]


def _count_engagement_units(body_lines: list[str], artifact_text: str) -> tuple[int, int]:
    """Count verified quotes and numbered objections among `body_lines`.

    Returns `(verified_quote_count, objection_count)`. See the
    VerdictContract comment above `_QUOTE_LINE_PATTERN` for the exact line
    shapes recognised. A quote line whose text does not verify against
    `artifact_text` is dropped silently — it contributes to neither count —
    rather than being counted as a malformed line of any kind.
    """
    verified_quotes = verify_quotes(extract_quotes(body_lines), artifact_text)
    objections = extract_objections(body_lines)
    return len(verified_quotes), len(objections)


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


# Public entry point.  Keep the historic private name for every existing
# caller while giving leaf-module consumers a stable supported API.
parse_verdict_contract = _parse_critic_verdict
