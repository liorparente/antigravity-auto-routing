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

import json
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Literal

__all__ = [
    "CRITIC_VERDICT_APPROVE",
    "CRITIC_VERDICT_BLOCK",
    "CRITIC_VERDICT_REVISE",
    "FINDING_SEVERITIES",
    "REVIEWER_PERSPECTIVES",
    "AdvisoryOutcome",
    "AdvisoryResolutionOption",
    "AdvisoryRoundVerdict",
    "AdvisoryStalemateReport",
    "CriticVerdict",
    "FindingSeverity",
    "Occasion",
    "PerspectiveReviewResult",
    "PerspectiveVerdict",
    "ReviewerPerspective",
    "StructuredFinding",
    "VerdictContractResult",
    "extract_objections",
    "extract_perspective_tag",
    "extract_quotes",
    "extract_structured_findings",
    "parse_perspective_review",
    "parse_verdict_contract",
    "verify_quotes",
]

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
CRITIC_VERDICT_BLOCK = "VERDICT: BLOCK"

# Spec 0012 / workflow-v2 ticket 06: the four analytical perspectives a
# Council reviewer is assigned, in place of a vendor brand identity. See
# `docs/adr/0012-workflow-v2-architecture-contracts.md` for the rationale.
ReviewerPerspective = Literal[
    "reviewer_architecture", "reviewer_risk", "reviewer_maintainability", "reviewer_security"
]
REVIEWER_PERSPECTIVES: tuple[ReviewerPerspective, ...] = (
    "reviewer_architecture",
    "reviewer_risk",
    "reviewer_maintainability",
    "reviewer_security",
)

FindingSeverity = Literal["critical", "high", "medium", "low"]
FINDING_SEVERITIES: tuple[FindingSeverity, ...] = ("critical", "high", "medium", "low")

# Derived from `REVIEWER_PERSPECTIVES`, not hand-coded, so a fifth
# perspective added there automatically gains both its canonical name and
# its short alias here (`"reviewer_risk"` -> `"risk"`) with no second edit.
_PERSPECTIVE_ALIASES: dict[str, ReviewerPerspective] = {
    alias: perspective
    for perspective in REVIEWER_PERSPECTIVES
    for alias in (perspective, perspective.removeprefix("reviewer_"))
}

# The Critic's verdict line, once read, is one of these three states.
# "unparseable" is deliberately not folded into "revise": a malformed
# response must halt the consultation, not be fed back to the Planner as if
# it were a reasoned objection. See `VerdictContractResult` below for the
# richer shape `_parse_critic_verdict` actually returns (spec 0003 ticket
# 02): this three-way type is still exactly what a caller branches on, just
# no longer the parser's entire return value.
#
# This exact three-way vocabulary is pinned against `learning_journal`'s
# `RoundVerdict` (see test_routing.py's cross-spec vocabulary pin) — do not
# add a fourth value here. `PerspectiveVerdict` below is the extended,
# four-way vocabulary for a perspective reviewer's unilateral BLOCK veto
# (spec 0012 / ticket 06); it is a distinct type precisely so this one stays
# pinned.
CriticVerdict = Literal["approved", "revise", "unparseable"]

# Spec 0012 / ticket 06: a perspective reviewer's verdict, extending
# `CriticVerdict` with "blocked" for `CRITIC_VERDICT_BLOCK` — a unilateral
# security veto that must halt the pipeline regardless of any other
# reviewer's vote. See `parse_perspective_review` and
# `PerspectiveReviewResult`.
PerspectiveVerdict = Literal["approved", "revise", "unparseable", "blocked"]

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


@dataclass(frozen=True)
class StructuredFinding:
    """One machine-parseable finding extracted from a perspective reviewer's
    response — either a `FINDING: [id=... severity=... category=...] <claim>`
    tagged line or an entry in a fenced ```json findings array (see
    `extract_structured_findings`).

    `perspective` records which of the four `ReviewerPerspective` values
    surfaced this finding — the reviewer's own self-declared tag when
    present, else whatever `default_perspective` the caller supplied — so a
    caller aggregating findings across a Council panel can still tell them
    apart after they are flattened into one collection.
    """

    id: str
    severity: FindingSeverity
    claim: str
    category: str = "general"
    evidence_references: tuple[str, ...] = ()
    recommendation: str = ""
    rationale: str = ""
    confidence: float = 1.0
    perspective: ReviewerPerspective | None = None


@dataclass(frozen=True)
class PerspectiveReviewResult:
    """A perspective reviewer's parsed verdict, engagement units, and
    structured findings (see `parse_perspective_review`).

    Deliberately not a subclass or replacement of `VerdictContractResult`:
    that type's contract is frozen for `parse_verdict_contract`'s existing
    callers. This is the richer, additive shape for spec 0012's Council
    perspectives — same verdict/quote/objection accounting, plus the
    `findings` a perspective reviewer tagged, its own declared
    `perspective`, and the verbatim `raw_vote` line for callers that need
    the untouched text (e.g. telemetry).
    """

    verdict: PerspectiveVerdict
    verified_quote_count: int
    objection_count: int
    findings: tuple[StructuredFinding, ...] = ()
    perspective: ReviewerPerspective | None = None
    raw_vote: str | None = None


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


# Spec 0012 / ticket 06: a perspective reviewer self-declares which of the
# four `ReviewerPerspective` values it is reviewing under, either as a
# bracketed `[PERSPECTIVE: reviewer_security]` tag (matched anywhere in the
# response, so it can sit alongside prose or a finding line), a standalone
# `PERSPECTIVE: reviewer_security` / `perspective: security` line (matched
# case-insensitively so both the constant and the short alias spelling
# work), or a `"perspective": "reviewer_security"` field inside a fenced
# ```json block, read from the actually-parsed JSON object rather than a
# regex guess. A line that opens a QUOTE engagement unit is excluded from
# every one of these lookups first — a Critic quoting the artifact
# verbatim could otherwise have a stray `[PERSPECTIVE: ...]`-shaped
# substring inside the quoted text misread as its own self-declaration.
_PERSPECTIVE_BRACKET_PATTERN = re.compile(r"\[PERSPECTIVE:\s*([A-Za-z_]+)\]", re.IGNORECASE)
_PERSPECTIVE_LINE_PATTERN = re.compile(r"(?im)^\s*PERSPECTIVE:\s*([A-Za-z_]+)\s*$")

# A tagged finding line: `FINDING: [id=... severity=... category=...] <claim>`.
# Attribute order and presence are both unconstrained — `_parse_finding_attrs`
# reads whichever `key=value` pairs actually appear inside the brackets.
_FINDING_LINE_PATTERN = re.compile(r"(?im)^\s*FINDING:\s*\[(?P<attrs>[^\]]*)\]\s*(?P<claim>.*)$")
_FINDING_ATTR_PATTERN = re.compile(r"(\w+)\s*=\s*(\"[^\"]*\"|'[^']*'|\S+)")
_JSON_FENCE_PATTERN = re.compile(r"```json\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def _normalize_perspective(raw: str) -> str:
    """Map a perspective alias (`security`) or full name (`reviewer_security`)
    to its canonical `ReviewerPerspective` value; unknown text passes through
    unchanged so a caller can still see what a reviewer actually wrote."""
    return _PERSPECTIVE_ALIASES.get(raw.strip().lower(), raw.strip())


def extract_perspective_tag(text: str) -> ReviewerPerspective | None:
    """Extract a reviewer's self-declared perspective, or `None` if absent
    or unrecognized.

    A `QUOTE:` line — the verbatim engagement unit a Critic copies from the
    reviewed artifact — is excluded from every lookup below, so a tag-shaped
    substring quoted from the artifact itself is never mistaken for the
    reviewer's own self-declaration.

    Tries, in order, over the remaining lines: a bracketed
    `[PERSPECTIVE: ...]` tag anywhere in the text, a standalone
    `PERSPECTIVE: ...` (or `perspective: ...`) line, then the `perspective`
    field of a fenced ```json block, read from the actually-parsed JSON
    object (not a regex guess, so a `"perspective"` substring inside
    unrelated JSON text — e.g. a quoted claim — can't be misread as the
    field). The first match wins; its raw value is normalized through
    `_normalize_perspective` and then validated against
    `REVIEWER_PERSPECTIVES` — an unknown or malformed tag (e.g. a typo'd
    perspective name) is discarded rather than returned as free text, since
    every caller of this function treats the result as a `ReviewerPerspective`
    key, never as display text.
    """
    non_quote_text = "\n".join(
        line for line in text.splitlines() if not _QUOTE_LINE_PATTERN.match(line.strip())
    )

    bracket_match = _PERSPECTIVE_BRACKET_PATTERN.search(non_quote_text)
    if bracket_match:
        return _validate_perspective(_normalize_perspective(bracket_match.group(1)))

    for line in non_quote_text.splitlines():
        line_match = _PERSPECTIVE_LINE_PATTERN.match(line)
        if line_match:
            return _validate_perspective(_normalize_perspective(line_match.group(1)))

    for fence_match in _JSON_FENCE_PATTERN.finditer(non_quote_text):
        try:
            payload = json.loads(fence_match.group(1))
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        raw_perspective = payload.get("perspective")
        if not isinstance(raw_perspective, str):
            continue
        validated = _validate_perspective(_normalize_perspective(raw_perspective))
        if validated is not None:
            return validated

    return None


def _validate_perspective(candidate: str) -> ReviewerPerspective | None:
    """Return `candidate` if it is one of `REVIEWER_PERSPECTIVES`, else `None`."""
    return candidate if candidate in REVIEWER_PERSPECTIVES else None  # type: ignore[return-value]


def _parse_finding_attrs(attrs_text: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for match in _FINDING_ATTR_PATTERN.finditer(attrs_text):
        key = match.group(1).strip().lower()
        value = match.group(2)
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        attrs[key] = value
    return attrs


def _normalize_severity(raw: str | None) -> FindingSeverity:
    normalized = (raw or "medium").strip().lower()
    return normalized if normalized in FINDING_SEVERITIES else "medium"  # type: ignore[return-value]


def _clamp_confidence(raw: object) -> float:
    """Coerce `raw` to a finite float in `[0.0, 1.0]`, defaulting to `1.0`.

    A missing, non-numeric, NaN, or infinite confidence is exactly as
    untrustworthy as an out-of-range one — all fall back to `1.0` rather
    than propagating a value a caller could misread as a genuine low- or
    high-confidence signal.
    """
    try:
        confidence = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 1.0
    if not math.isfinite(confidence):
        return 1.0
    return max(0.0, min(1.0, confidence))


def _coerce_evidence_references(evidence: object) -> tuple[str, ...]:
    """Coerce a JSON finding's `evidence_references`/`evidence` field to a
    tuple of strings, failing closed to `()` for any shape that is not
    actually a list of references.

    A reviewer's JSON is untrusted input: `evidence` can arrive as a list
    (the documented shape), a bare string (one reference), or — from a
    malformed or hallucinated response — an int, a dict, or anything else
    JSON allows. Only the first two shapes are meaningful evidence; every
    other shape is discarded rather than raising or being coerced into
    something misleading (e.g. iterating a dict's keys as if they were
    reference strings).
    """
    if isinstance(evidence, (list, tuple)):
        return tuple(str(item) for item in evidence if item is not None)
    if isinstance(evidence, str) and evidence.strip():
        return (evidence.strip(),)
    return ()


def _resolve_finding_perspective(
    raw_perspective: object, default_perspective: ReviewerPerspective | None
) -> ReviewerPerspective | None:
    """Resolve one finding's `perspective` field to a validated
    `ReviewerPerspective`, falling back to `default_perspective` when the
    field is absent, not a string, or not one of `REVIEWER_PERSPECTIVES` —
    a reviewer's own typo'd or hallucinated perspective name must never
    leak through as free text (see `StructuredFinding.perspective`'s
    tightened type)."""
    if isinstance(raw_perspective, str) and raw_perspective:
        validated = _validate_perspective(_normalize_perspective(raw_perspective))
        if validated is not None:
            return validated
    return default_perspective


def _finding_from_json_obj(
    obj: object, *, default_perspective: ReviewerPerspective | None, fallback_id: str
) -> StructuredFinding | None:
    if not isinstance(obj, dict):
        return None
    claim = str(obj.get("claim") or obj.get("summary") or obj.get("description") or "").strip()
    if not claim:
        return None

    evidence_references = _coerce_evidence_references(
        obj.get("evidence_references") or obj.get("evidence")
    )

    confidence = _clamp_confidence(obj.get("confidence", 1.0))

    perspective = _resolve_finding_perspective(obj.get("perspective"), default_perspective)

    return StructuredFinding(
        id=str(obj.get("id") or fallback_id),
        severity=_normalize_severity(str(obj.get("severity")) if obj.get("severity") else None),
        claim=claim,
        category=str(obj.get("category") or "general"),
        evidence_references=evidence_references,
        recommendation=str(obj.get("recommendation") or ""),
        rationale=str(obj.get("rationale") or ""),
        confidence=confidence,
        perspective=perspective,
    )


def extract_structured_findings(
    text: str, *, default_perspective: ReviewerPerspective | None = None
) -> tuple[StructuredFinding, ...]:
    """Extract every `StructuredFinding` tagged in a reviewer's response.

    Parses two independent shapes and concatenates their results in the
    order encountered — a fenced ```json block containing a `findings`
    array first, then `FINDING: [id=... severity=... category=...] <claim>`
    tagged lines. A finding whose `id` collides with one already extracted
    is never dropped — a critical/high finding must never be silently
    discarded merely because its author reused an id — instead its id is
    disambiguated by appending `-{n}` for the smallest `n >= 1` not already
    claimed by any prior finding (so a second "f1" becomes "f1-1", a third
    becomes "f1-2", and so on). This is checked against every id assigned
    so far, not just a per-base-id counter, so a disambiguated id can never
    collide with a real id a reviewer happened to write literally (e.g. a
    genuine "f1-1" finding followed by two colliding "f1"s). A finding with
    no `severity` attribute defaults to `"medium"`; an out-of-vocabulary
    severity is normalized the same way. Every finding without an explicit
    perspective inherits `default_perspective`.
    """
    findings: list[StructuredFinding] = []
    seen_ids: set[str] = set()

    def _disambiguate_id(finding_id: str) -> str:
        if finding_id not in seen_ids:
            seen_ids.add(finding_id)
            return finding_id
        counter = 1
        candidate_id = f"{finding_id}-{counter}"
        while candidate_id in seen_ids:
            counter += 1
            candidate_id = f"{finding_id}-{counter}"
        seen_ids.add(candidate_id)
        return candidate_id

    for fence_match in _JSON_FENCE_PATTERN.finditer(text):
        try:
            payload = json.loads(fence_match.group(1))
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(payload, dict):
            raw_findings = payload.get("findings")
        elif isinstance(payload, list):
            raw_findings = payload
        else:
            raw_findings = None
        if not isinstance(raw_findings, list):
            continue
        for raw_finding in raw_findings:
            finding = _finding_from_json_obj(
                raw_finding,
                default_perspective=default_perspective,
                fallback_id=f"finding-{len(findings) + 1}",
            )
            if finding is not None:
                findings.append(replace(finding, id=_disambiguate_id(finding.id)))

    for line in text.splitlines():
        line_match = _FINDING_LINE_PATTERN.match(line.strip())
        if not line_match:
            continue
        claim = line_match.group("claim").strip()
        if not claim:
            continue
        attrs = _parse_finding_attrs(line_match.group("attrs"))
        finding_id = _disambiguate_id(attrs.get("id") or f"finding-{len(findings) + 1}")
        perspective = _resolve_finding_perspective(attrs.get("perspective"), default_perspective)
        findings.append(
            StructuredFinding(
                id=finding_id,
                severity=_normalize_severity(attrs.get("severity")),
                claim=claim,
                category=attrs.get("category", "general"),
                perspective=perspective,
            )
        )

    return tuple(findings)


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


def parse_perspective_review(
    critic_response: str,
    artifact_text: str,
    *,
    default_perspective: str | None = None,
) -> PerspectiveReviewResult:
    """Parse a Council perspective reviewer's response (spec 0012 ticket 06).

    Layers three additions on top of `_parse_critic_verdict`'s VerdictContract
    accounting (verbatim quote verification, numbered objections), while
    reusing it unchanged:

    1. A `CRITIC_VERDICT_BLOCK` ("VERDICT: BLOCK") verdict line, read exactly
       as strictly as `CRITIC_VERDICT_APPROVE` — no prefix or punctuation
       tolerance — since a unilateral security veto that halts the whole
       Council pipeline must never be inferred from a near-miss line. Unlike
       APPROVE, BLOCK carries no verified-quote requirement: a reviewer that
       has spotted a critical vulnerability is not made less credible by
       lacking a verbatim quote, and fail-closed already means "do not
       proceed" either way.
    2. The reviewer's `perspective`: when the caller supplies a
       `default_perspective` that validates against `REVIEWER_PERSPECTIVES`
       (through the same alias-normalizing `_validate_perspective` path
       `extract_perspective_tag` uses) — the ground-truth role a Council
       orchestrator actually assigned this reviewer — that assignment
       always wins, even if the reviewer's own response self-declares (or
       misdeclares) a different `[PERSPECTIVE: ...]` tag. An invalid
       `default_perspective` (a typo, or any string outside
       `REVIEWER_PERSPECTIVES`/its aliases) is treated exactly like passing
       none at all: a caller's malformed assignment must never leak through
       as free text on `PerspectiveReviewResult.perspective`, which is
       typed as a `ReviewerPerspective`, not a string. Only when no valid
       `default_perspective` survives validation does this fall back to
       `extract_perspective_tag(critic_response)`, which itself discards any
       tag that is not one of `REVIEWER_PERSPECTIVES`. A reviewer's own
       words are trusted only in the absence of a valid assigned identity,
       never used to override one.
    3. Any `StructuredFinding`s the reviewer tagged, via
       `extract_structured_findings`, each inheriting the resolved
       `perspective` as its own default when a finding does not declare one.

    An unreadable response (no non-empty line at all) still returns
    `verdict="unparseable"` with a zero engagement-unit tally, exactly like
    `_parse_critic_verdict` — the fail-closed baseline is unchanged, it is
    just carried on the richer `PerspectiveReviewResult` shape here.
    """
    validated_default_perspective = (
        _validate_perspective(_normalize_perspective(default_perspective))
        if default_perspective is not None
        else None
    )
    perspective = validated_default_perspective or extract_perspective_tag(critic_response)
    findings = extract_structured_findings(critic_response, default_perspective=perspective)

    verdict_line, body_lines = _split_off_verdict_line(critic_response)
    if verdict_line is None:
        return PerspectiveReviewResult(
            "unparseable", 0, 0, findings=findings, perspective=perspective
        )

    verified_quotes, objections = _count_engagement_units(body_lines, artifact_text)
    upper = verdict_line.upper()

    if upper == CRITIC_VERDICT_BLOCK:
        return PerspectiveReviewResult(
            "blocked",
            verified_quotes,
            objections,
            findings=findings,
            perspective=perspective,
            raw_vote=verdict_line,
        )

    if upper == CRITIC_VERDICT_APPROVE:
        verdict: PerspectiveVerdict = "approved" if verified_quotes >= 1 else "unparseable"
        return PerspectiveReviewResult(
            verdict,
            verified_quotes,
            objections,
            findings=findings,
            perspective=perspective,
            raw_vote=verdict_line,
        )

    if _is_tolerant_revise(upper):
        return PerspectiveReviewResult(
            "revise",
            verified_quotes,
            objections,
            findings=findings,
            perspective=perspective,
            raw_vote=verdict_line,
        )

    return PerspectiveReviewResult(
        "unparseable",
        verified_quotes,
        objections,
        findings=findings,
        perspective=perspective,
        raw_vote=verdict_line,
    )


# Public entry point.  Keep the historic private name for every existing
# caller while giving leaf-module consumers a stable supported API.
parse_verdict_contract = _parse_critic_verdict
