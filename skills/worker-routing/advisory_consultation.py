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
import threading
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

# Spec 0003 (CriticalDialogue) ticket 07: "Family, not model, is the
# independence unit" (spec's Implementation Decisions paragraph of the same
# name). A family is a provider lineage — the Claude family, the Codex/GPT
# family, the Gemini family, and each local model lineage counts as its own
# family — not an individual model name. `classify_model_family` is the pure
# function every later piece of this ticket's infrastructure is built on:
# the roster resolver below calls it to decide whether two roles' assigned
# models are independent, and a caller wiring telemetry (ticket 10) or a
# canary fixture (ticket 08) can call it directly on any model name without
# reaching into this module's other machinery.
#
# The four cloud lineages are recognized by a small, fixed vocabulary of
# provider-name substrings, not by an exhaustive model list pulled from
# `routing-config.json`'s `supported_models` — deliberately, per this
# ticket's own brief ("don't hardcode an exhaustive model list ... but use
# your judgment"). Provider lineage is a property of the name itself: every
# Claude model's name contains "claude", and that stays true for models this
# file has never seen; `supported_models` is merely today's roster snapshot
# and would need an edit for every new tier that ships, which would make
# family classification silently stale the moment a new model landed in
# config but not in this list. "codex" and "gpt" both resolve to one
# "codex-gpt" family — spelled out explicitly in this ticket's own
# instructions — because `routing-config.json`'s `critic` role block already
# lists "Codex 5.6 Sol" and "GPT-OSS 120B (Medium)" as interchangeable
# alternatives within one role, i.e. this repo's own config already treats
# them as the same lineage for routing purposes.
#
# Anything matching none of the four substrings is treated as its own local
# lineage — the spec's "each local model lineage counts as its own family" —
# derived from the model's own name rather than looked up in a table this
# module would otherwise need to keep in sync with every local model ever
# loaded into LM Studio. The leading run of alphabetic characters in the
# name, case-folded, is that lineage's identifier: "Gemma 4 E4B" and a
# future "Gemma 2 9B" both become "gemma" (two checkpoints of one base model
# are one lineage), while "Qwen3-Coder-Next" becomes "qwen" — a genuinely
# different local model gets a genuinely different family. This is a
# heuristic, not a registry lookup, on purpose: it needs no maintenance as
# new local checkpoints are loaded, which a hardcoded local-model list would.
_CLOUD_FAMILY_SUBSTRINGS: tuple[tuple[str, str], ...] = (
    ("claude", "claude"),
    ("gemini", "gemini"),
    ("codex", "codex-gpt"),
    ("gpt", "codex-gpt"),
)

_LOCAL_LINEAGE_PATTERN = re.compile(r"[a-zA-Z]+")


def classify_model_family(model_name: str) -> str:
    """Return the provider-lineage family `model_name` belongs to.

    Pure and total: every input string maps to some family string, and this
    never raises. See the module comment above `_CLOUD_FAMILY_SUBSTRINGS`
    for the exact rule and why the cloud vocabulary is a small fixed set
    rather than a model list, and why a local model's family is derived
    from its own name rather than looked up. Matching is case-insensitive
    and substring-based (`"claude" in lowered`), same style
    `_detect_sensitivity_marker` already uses for `SENSITIVITY_MARKERS`.

    The local-lineage fallback uses `_LOCAL_LINEAGE_PATTERN.match`, which
    Python anchors at index 0, not `.search` — deliberately: this function's
    own contract is "the LEADING run of alphabetic characters", and `.match`
    is what actually enforces that anchor. `.search` would instead find the
    first alphabetic run anywhere in the string, which for a digit-led local
    name (e.g. a hypothetical "70B-Instruct" or "4B-Mixtral") would pick out
    whatever short fragment happens to occur first ("b", "instruct") — an
    unstable, coincidental identifier two genuinely unrelated local models
    could easily collide into, silently defeating the independence guarantee
    `resolve_roster` exists to enforce. A `model_name` with no leading
    alphabetic run at all (digit-led, or empty) instead falls through to the
    final fallback below: the full lowercased, stripped name is used as its
    own family identifier, or `"unknown"` if that is also empty. That
    fallback can never falsely equate two different digit-led names (each
    gets its own family, keyed off its own full text), and never leaves a
    "different family" check silently satisfied by an empty-string family
    either.
    """
    lowered = model_name.lower()
    for substring, family in _CLOUD_FAMILY_SUBSTRINGS:
        if substring in lowered:
            return family
    match = _LOCAL_LINEAGE_PATTERN.match(model_name)
    if match:
        return match.group(0).lower()
    return lowered.strip() or "unknown"


# Spec 0003 (CriticalDialogue) ticket 07: the two topologies a roster can be
# resolved for. "critic_a" doubles as pair mode's sole critic role — the
# same "`critic_position` means Critic A in panel mode" convention
# `AdvisoryStalemateReport`'s docstring already established (pair mode
# simply never resolves a `critic_b`), rather than inventing a third,
# pair-only role name that would need its own fallback chain in config for
# no semantic reason: a pair's one Critic and a panel's Critic A play
# exactly the same role.
RosterTopology = Literal["pair", "panel"]
RosterRole = Literal["planner", "critic_a", "critic_b"]

_PAIR_ROSTER_ROLES: tuple[RosterRole, ...] = ("planner", "critic_a")
_PANEL_ROSTER_ROLES: tuple[RosterRole, ...] = ("planner", "critic_a", "critic_b")

# The independence unit's own callable seam: `(family) -> bool`, injected by
# the caller exactly like `InvokeWorker` above — this is what lets
# `resolve_roster` be exercised offline with a scripted fake instead of a
# real reachability probe (a curl to LM Studio, a provider health check),
# per this ticket's own instruction and the precedent every other seam in
# this module already sets.
IsFamilyReachable = Callable[[str], bool]


class RosterResolutionError(RuntimeError):
    """Raised when `resolve_roster` finds no reachable family for a role at all.

    Distinct from degraded independence: degradation means a family had to
    be reused across roles, and `resolve_roster` still returns a complete,
    usable result for that case — see `RosterResolution.degraded_independence`.
    This error means a role's entire fallback chain, every family it names,
    reported unreachable, so no assignment exists for that role at all. A
    caller integrating this into `run_advisory_consultation_debate` treats
    it the same as any other pre-flight failure to reach a worker: fail
    closed, never fabricate a roster to paper over it.
    """


# Spec 0003 (CriticalDialogue) ticket 07: each role's ordered fallback chain
# of candidate models, config-driven (see `_load_roster_fallback_chains`)
# rather than hardcoded here — mirroring the `critical_dialogue` section
# ticket 03 added to `routing-config.json`, and for the identical reason: a
# config read is the only way a test (or a future operator) can point this
# module at a different roster and observe the resolver's answer change,
# which is the only way to prove a value is genuinely read from config
# rather than merely referenced by key. These `DEFAULT_*` chains are the
# fallback for a config file missing the `roster_topology` section (or one
# of its role keys) — mirroring `DEFAULT_CODE_REVIEW_DIFF_LINE_THRESHOLD`'s
# identical role above — never what production actually uses, since
# `routing-config.json` supplies its own section as of this ticket.
#
# Every chain's first entry is exactly the parameter default
# `run_advisory_consultation_debate` already shipped before this ticket
# existed (`planner_model="Claude Opus 5 (Thinking)"`,
# `critic_a_model="Codex 5.6 Sol"`, `critic_b_model="Gemini 3.6 Flash"`), so
# a fully-reachable environment resolves to exactly the roster those
# hardcoded defaults already produced — this ticket adds a resolution
# *path*, it does not change what "everything is up" already looked like.
# Each chain's later entries walk through the protocol's documented
# fallback ordering (`protocol.md` section 3.5, "Complex/Planning: Claude
# Opus 5 (Thinking) -> Claude Fable/Opus 4.8 -> codex Sol") before ending on
# a local model, so "local families qualify" (the spec's own phrase) is a
# real, reachable last resort in every chain, not merely a claim.
DEFAULT_ROSTER_FALLBACK_CHAINS: dict[str, tuple[str, ...]] = {
    "planner": (
        "Claude Opus 5 (Thinking)",
        "Claude Fable 5",
        "Codex 5.6 Sol",
        "Gemini 3.6 Flash (High)",
        "Gemma 4 E4B",
    ),
    "critic_a": (
        "Codex 5.6 Sol",
        "GPT-OSS 120B (Medium)",
        "Gemini 3.6 Flash (High)",
        "Claude Opus 5 (Thinking)",
        "Qwen3-Coder-Next",
    ),
    "critic_b": (
        "Gemini 3.6 Flash",
        "Gemini 3.1 Pro (High)",
        "Codex 5.6 Sol",
        "Claude Opus 5 (Thinking)",
        "Gemma 4 E4B",
    ),
}


def _load_roster_fallback_chains(config_path: Path) -> dict[str, tuple[str, ...]]:
    """Read each role's fallback chain from `config_path`'s `roster_topology`
    section, falling back to `DEFAULT_ROSTER_FALLBACK_CHAINS` per role (or
    wholesale when the section itself is absent) — same pattern
    `_load_code_review_risk_config` above uses for `critical_dialogue`,
    including its no-try/except contract: a missing or malformed
    `config_path` raises whatever `open`/`json.load` raises rather than
    being silently swallowed, because production always calls this with the
    default `_CONFIG_PATH`, which is checked into the repo.
    """
    with open(config_path, "r", encoding="utf-8") as stream:
        config = json.load(stream)
    section = config.get("roster_topology", {})
    configured_chains = section.get("role_fallback_chains", {})
    resolved: dict[str, tuple[str, ...]] = {}
    for role, default_chain in DEFAULT_ROSTER_FALLBACK_CHAINS.items():
        resolved[role] = tuple(configured_chains.get(role, default_chain))
    return resolved


@dataclass(frozen=True)
class RosterAssignment:
    """One role's resolved model, and the family `classify_model_family`
    placed it in."""

    role: RosterRole
    model: str
    family: str


@dataclass(frozen=True)
class RosterResolution:
    """The result of resolving one topology's roster against live reachability.

    `degraded_independence` is this ticket's own exposed signal: True
    whenever any two roles in `assignments` ended up sharing a family,
    because `resolve_roster` could not find enough distinct reachable
    families to give every role its own. See `resolve_roster`'s docstring
    for the exact "single family remains" reading this field implements —
    this dataclass is deliberately where a caller (including ticket 10's
    telemetry-extension work) reads whether degradation occurred, per this
    ticket's own instruction to expose it here even where wiring it further
    would overstep this ticket's scope.
    """

    topology: RosterTopology
    assignments: tuple[RosterAssignment, ...]
    degraded_independence: bool

    def model_for(self, role: RosterRole) -> str:
        """The resolved model for `role`.

        Raises `KeyError` if `role` was never part of this resolution's
        topology (e.g. asking a pair-mode result for `"critic_b"`) —
        deliberately loud rather than returning an empty string, since that
        is always a call-site bug, not a legitimate "no such role" state.
        """
        for assignment in self.assignments:
            if assignment.role == role:
                return assignment.model
        raise KeyError(f"role {role!r} is not part of this {self.topology} roster")


def resolve_roster(
    topology: RosterTopology,
    *,
    is_family_reachable: IsFamilyReachable,
    config_path: Path = _CONFIG_PATH,
) -> RosterResolution:
    """Assign a model to each role `topology` requires, preferring a
    distinct family per role and degrading to family reuse only when the
    reachable families genuinely run out.

    Roles are resolved in a fixed order — `planner`, then `critic_a`, then
    (panel only) `critic_b` — each walking its own config-driven fallback
    chain (`_load_roster_fallback_chains`) and classifying every candidate
    with `classify_model_family`. For each role, two passes:

    1. Prefer the first candidate whose family both `is_family_reachable`
       and is not already claimed by an earlier role in this resolution.
       This is "substitute the next family in the fallback chain" (the
       ticket's own phrase): a role whose *preferred* family is unreachable
       does not immediately degrade, it just keeps walking its own chain,
       exactly the same as any other unreachable candidate.
    2. Only if no candidate offers a fresh, reachable family does resolution
       fall back to the first candidate that is merely reachable (even
       though its family is already used elsewhere in this roster) — and
       only this second pass sets `degraded_independence`.

    A role whose chain offers no reachable family at all, in either pass,
    raises `RosterResolutionError` — resolution never fabricates an
    assignment nobody can actually reach.

    **Reading "a single family remains" (spec 0003's "Degraded
    independence, never silence" paragraph).** The spec's literal words —
    "Only when a single family remains does the dialogue run same-family" —
    admit two readings: (a) exactly one family is reachable across the
    entire environment, or (b) resolution has been forced to reuse a family
    already claimed within *this* roster, however many families remain
    reachable elsewhere. This function implements reading (b), for two
    reasons. First, the spec's own next clause is about causation, not a
    global census: "and then it carries an explicit degraded-independence
    marker" — the marker exists to flag *this dialogue's* weakened
    independence, and a dialogue's independence is exactly as weakened by
    "2 roles, 1 shared family, while a third unrelated family sits
    reachable but unused by this topology" as by "literally 1 family total
    reachable" — reading (a) would leave the former case silently
    unflagged, which contradicts the spec's own repeated "Never silently"
    refrain (Problem Statement, Implementation Decisions, and User Story 14
    all restate it). Second, reading (b) is strictly the more conservative
    of the two — it flags a superset of what reading (a) would — so it
    never under-reports a compromised roster, only ever over-reports
    relative to the narrower literal reading, which is the safe direction
    to err in for an audit signal. Concretely: a panel needing 3 families
    with only 2 reachable is degraded under this reading (one family
    serves two roles) even though "a single family" was never literally
    true — this is the ticket's own suggested resolution for that exact
    ambiguity, spelled out here rather than left implicit.

    Raises `ValueError` for a `topology` that is not `"pair"` or `"panel"`
    — a call-site programming error, not a roster outcome.
    """
    if topology == "pair":
        roles: tuple[RosterRole, ...] = _PAIR_ROSTER_ROLES
    elif topology == "panel":
        roles = _PANEL_ROSTER_ROLES
    else:
        raise ValueError(f"unknown roster topology {topology!r}; expected 'pair' or 'panel'")

    chains = _load_roster_fallback_chains(config_path)
    used_families: set[str] = set()
    assignments: list[RosterAssignment] = []
    degraded = False

    for role in roles:
        chain = chains[role]
        chosen: tuple[str, str] | None = None

        for model in chain:
            family = classify_model_family(model)
            if is_family_reachable(family) and family not in used_families:
                chosen = (model, family)
                break

        if chosen is None:
            for model in chain:
                family = classify_model_family(model)
                if is_family_reachable(family):
                    chosen = (model, family)
                    degraded = True
                    break

        if chosen is None:
            raise RosterResolutionError(
                f"no reachable family for role {role!r}: every family in its "
                f"fallback chain {list(chain)!r} reported unreachable"
            )

        assignments.append(RosterAssignment(role=role, model=chosen[0], family=chosen[1]))
        used_families.add(chosen[1])

    return RosterResolution(
        topology=topology,
        assignments=tuple(assignments),
        degraded_independence=degraded,
    )


# Spec 0003 (CriticalDialogue) ticket 07: the literal substring
# `_render_consultation_transcript` writes into a degraded-independence
# transcript, and what a test greps for. Named and exported so a caller
# never needs to hand-copy the marker text to check for it — see the
# acceptance criterion that a test asserting on transcript content must
# find this, not just the structured record.
DEGRADED_INDEPENDENCE_MARKER = "DEGRADED INDEPENDENCE"

# Spec 0003 (CriticalDialogue) ticket 04: named once and reused by both
# `run_advisory_consultation_debate` and the crash-recovery path inside
# `dispatch_post_mortem_consultation`'s thread target, rather than left as
# two independently-typed literals. Before ticket 04 there was only ever one
# writer of these two paths, so one inline literal each was fine; a second
# writer (the crash-recovery path, which must reach the exact same files a
# normal run would have, "exactly as if it had blocked") makes the
# duplication a real drift risk instead of a hypothetical one — a future
# edit to one inline literal and not the other would make the crash path
# write to a location nothing ever reads.
_TRANSCRIPT_RELATIVE_PATH = Path(".scratch") / "planning_debate.md"
_TELEMETRY_RELATIVE_PATH = Path(".ralph") / "routing_telemetry.jsonl"


@dataclass(frozen=True)
class AdvisoryDebateRound:
    """One Planner/Critic exchange: the proposal offered and the verdict(s) it drew.

    `critic_response` is unrenamed and means exactly what it always meant:
    the sole Critic's response in pair mode, and — spec 0003 ticket 05 —
    Critic A's response in panel mode. Every pre-existing pair-mode
    construction and read site, in this module and in tests, keeps meaning
    exactly what it meant before panel mode existed.

    `critic_b_response` is new (ticket 05) and appended last with a `None`
    default so this stays additive, not a reshape: `None` on every pair-mode
    round, populated only for a panel-mode round, where a second,
    independently invoked Critic reviews the same Planner proposal that
    Critic A saw. A caller distinguishes a pair round from a panel round by
    `critic_b_response is None` — exactly what `_render_consultation_transcript`
    does below. Ticket 06 owns giving a *stalemate report* three properly
    labeled voices; this dataclass only needs to carry the second response
    at all, which is as far as this ticket's scope goes.
    """

    planner_proposal: str
    critic_response: str
    critic_b_response: str | None = None


@dataclass(frozen=True)
class AdvisoryResolutionOption:
    """One way a human can resolve a stalemate."""

    id: int
    label: str
    description: str


@dataclass(frozen=True)
class AdvisoryStalemateReport:
    """Every final position of an unresolved consultation, plus the human's options.

    Carries no winner: the consultation does not pick one, so this structure
    has no field capable of holding one.

    Two voices in pair mode (spec 0001, unchanged): `planner_position` and
    `critic_position` are the Planner's and the sole Critic's last
    positions, and `critic_b_position` stays `None` — see the additive-field
    note below.

    Three voices in panel mode (spec 0003 ticket 06): `critic_b_position` is
    populated with Critic B's own final position, kept completely separate
    from `critic_position`, which in panel mode carries Critic A's final
    position — the same "`critic_position` means Critic A in panel mode"
    convention `AdvisoryDebateRound.critic_response` already established.
    Never a folded/concatenated string of both Critics (that was ticket 05's
    deliberately-temporary `_combine_panel_critic_feedback`, which this
    report replaces for stalemate purposes): a human resolving a panel
    stalemate reads each Critic's actual final words, not a summary this
    module wrote of them.

    `critic_b_position` is appended last with a `None` default so this stays
    additive, not a reshape: every pre-ticket-06 construction of this
    dataclass — in this module and in tests — that never mentions
    `critic_b_position` keeps meaning exactly what it meant before this field
    existed, and `_build_stalemate_report`'s existing two-argument pair-mode
    call site needs no change at all.
    """

    planner_position: str
    critic_position: str
    options: tuple[AdvisoryResolutionOption, AdvisoryResolutionOption, AdvisoryResolutionOption]
    critic_b_position: str | None = None


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

    `degraded_independence` (spec 0003 ticket 07) is appended after
    `occasion` for the identical reason and by the identical rule: every
    pre-ticket-07 construction of this dataclass — in this module and in
    tests — that never mentions it keeps meaning exactly what it meant
    before this field existed, defaulting to `False`. True only when
    `run_advisory_consultation_debate` resolved its roster via an injected
    `reachability_check` (see that function's docstring) and
    `resolve_roster` reported `RosterResolution.degraded_independence` —
    i.e. two or more roles in this consultation had to share a model
    family. A run that never opts into roster resolution at all — every
    call site in this repo today — always carries `False` here, never
    silently `True`: this field is deliberately not inferred from
    `planner_model == critic_model` after the fact, because two roles
    sharing a literal model name by caller coincidence (an explicit
    `critic_model="Test Critic"` in a test, say) is not the same claim as
    "the roster resolver was forced to reuse a family."
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
    degraded_independence: bool = False

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
    ]
    # Spec 0003 (CriticalDialogue) ticket 07: only present when
    # `result.degraded_independence` is True — never an always-rendered
    # "Degraded independence: False" line — so a normal run's transcript
    # never contains `DEGRADED_INDEPENDENCE_MARKER` at all, which is the
    # literal acceptance criterion ("A normal ... run never carries the
    # marker") a test can grep for with `assertNotIn`, not merely a falsy
    # field a reader has to notice.
    if result.degraded_independence:
        lines.extend(
            [
                (
                    f"**{DEGRADED_INDEPENDENCE_MARKER}:** This dialogue could "
                    "not achieve full cross-family independence — the roster "
                    "resolver was forced to assign the same model family to "
                    "more than one role. Treat this consultation's outcome "
                    "with reduced confidence."
                ),
                "",
            ]
        )
    lines.extend(
        [
            "## Task",
            "",
            task_description,
            "",
        ]
    )
    if not rounds:
        lines.append("_No rounds were run._")
    for index, round_ in enumerate(rounds, start=1):
        # `critic_b_response is not None` is exactly `AdvisoryDebateRound`'s
        # own pair-vs-panel discriminant (see its docstring): a pair-mode
        # round leaves this branch untaken, so its rendered output — the
        # `### Critic (...)` header and nothing after the sole response — is
        # byte-identical to every transcript this function rendered before
        # panel mode existed (spec 0003 ticket 05). A panel-mode round gets a
        # second, plainly-labeled section for Critic B; giving Critic B's
        # header a model name to match Critic A's requires a field this
        # ticket deliberately does not add to `AdvisoryDebateResult` — see
        # the ticket's report for why that is ticket 10's job, not this one's.
        is_panel_round = round_.critic_b_response is not None
        critic_a_header = (
            f"### Critic A ({result.critic_model})"
            if is_panel_round
            else f"### Critic ({result.critic_model})"
        )
        lines.extend(
            [
                f"## Round {index}",
                "",
                f"### Planner ({result.planner_model})",
                "",
                round_.planner_proposal,
                "",
                critic_a_header,
                "",
                round_.critic_response,
                "",
            ]
        )
        if round_.critic_b_response is not None:
            lines.extend(
                [
                    "### Critic B",
                    "",
                    round_.critic_b_response,
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

    `degraded_independence` (spec 0003 ticket 07) is appended after `kind`,
    last in field order — the same append-only rule `AdvisoryDebateResult`
    documents on its own `degraded_independence` field, which is this
    field's sole source: `_build_telemetry_record` copies
    `result.degraded_independence` verbatim, never re-derives it. This is
    deliberately a minimal, additive field rather than an attempt to
    anticipate ticket 10's full telemetry-extension scope (occasion,
    topology, per-round verdict sequence, engagement-unit counts, canary
    flag/result, and a general degradation-flags field for the budget
    ladder ticket 09 adds) — this ticket's acceptance criteria require only
    that the degraded-independence marker itself reach the telemetry
    record, not that this record anticipate every later ticket's shape.
    Ticket 10 extends this record further; this field is what it has to
    build on for this one signal.
    """

    timestamp: str
    task_id: str
    rounds_run: int
    outcome: AdvisoryOutcome
    planner_model: str
    critic_model: str
    kind: str = "advisory_consultation"
    degraded_independence: bool = False

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
        degraded_independence=result.degraded_independence,
    )


def _write_telemetry_record(path: Path, record: AdvisoryTelemetryRecord) -> str | None:
    """Render `record` to its wire form and write it. Failure is reported, never raised."""
    try:
        _append_jsonl_locked(path, record.to_mapping())
    except OSError as exc:
        return f"failed to write consultation telemetry at {path}: {exc}"
    return None


def _build_stalemate_report(
    planner_position: str,
    critic_position: str,
    critic_b_position: str | None = None,
) -> AdvisoryStalemateReport:
    """Build the report a caller receives when a consultation exhausts its
    rounds without consensus.

    `critic_b_position` is `None` for pair mode (every pre-ticket-06 call
    site) and this function's pair-mode branch is byte-for-byte what it was
    before this parameter existed — same two `AdvisoryResolutionOption`
    labels, same `critic_position`-only content, same field values. Spec
    0003 ticket 06: a caller now passing a non-`None` `critic_b_position`
    (a panel stalemate) instead takes the three-voice branch, which reports
    `critic_position` (Critic A's position) and `critic_b_position` (Critic
    B's position) as their own separate `AdvisoryStalemateReport` fields —
    never folded into one string — and rewords the second option's label
    from singular "Critic" to plural "Critics'" so it reads correctly for
    two reviewers. That option's `description` is the one place this
    function still joins the two Critics' text: `AdvisoryResolutionOption`
    has a single `description` field, so representing "approve the Critics'
    architecture" as one string necessarily needs both voices in it; this is
    a convenience summary for that one option only; it never replaces or
    substitutes for the report's own `critic_position`/`critic_b_position`
    fields, which stay separate and verbatim. Neither branch ever computes
    or reports a winner — see `AdvisoryStalemateReport`'s docstring.
    """
    if critic_b_position is None:
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

    combined_critics_description = (
        f"Critic A:\n{critic_position}\n\nCritic B:\n{critic_b_position}"
    )
    return AdvisoryStalemateReport(
        planner_position=planner_position,
        critic_position=critic_position,
        critic_b_position=critic_b_position,
        options=(
            AdvisoryResolutionOption(1, "Approve Planner Architecture", planner_position),
            AdvisoryResolutionOption(
                2, "Approve Critics' Architecture", combined_critics_description
            ),
            AdvisoryResolutionOption(
                3,
                "Escalate to Human Decision",
                "Halt execution and request user review",
            ),
        ),
    )


# Spec 0003 (CriticalDialogue) ticket 05: which occasion/complexity
# combinations run the panel topology (one Planner, two Critics) instead of
# the pair topology (one Planner, one Critic) spec 0001 shipped. Only these
# two occasions, and only at Complex tier: the spec's "Tiered topology"
# paragraph names plan-review and code-review specifically ("for Complex
# tasks, a panel of one Planner and two Critics from two families other than
# the Planner's"), and the ticket's own acceptance criteria are explicit that
# ambiguity and post-mortem keep the pair topology "unchanged" even at
# Complex tier — a panel is not simply "whatever runs at Complex complexity."
_PANEL_TOPOLOGY_OCCASIONS: tuple[Occasion, ...] = ("plan-review", "code-review")


def _is_panel_topology(occasion: Occasion, complexity: str) -> bool:
    """True when `occasion`/`complexity` select the panel topology.

    Normalizes `complexity` with the same `.lower().strip()` idiom every
    other complexity-reading function in this module already uses
    (`needs_advisory_consultation`, `needs_plan_review_consultation`,
    `needs_code_review_consultation`) — convergent reuse of that
    normalization, not a dependency on any of them. Deliberately permissive
    like those three, not validated like `occasion`/`max_rounds` below: a
    `complexity` value this function doesn't recognize as `"complex"` simply
    keeps the pair topology, exactly as `needs_plan_review_consultation`
    treats any complexity outside `("medium", "complex")` as "does not fire"
    rather than raising. `VALID_COMPLEXITIES` in `agent_council.py` is this
    repo's complexity vocabulary (`trivial`/`simple`/`medium`/`complex`);
    this function is not the place that enforces membership in it — the
    caller's own complexity-classification step already did, before this
    function is ever reached.
    """
    normalized = complexity.lower().strip()
    return occasion in _PANEL_TOPOLOGY_OCCASIONS and normalized == "complex"


def _combine_panel_critic_feedback(critic_a_response: str, critic_b_response: str) -> str:
    """Fold both Critics' responses into the single feedback string
    `_build_planner_prompt`'s `critic_feedback` parameter expects, for the
    Planner's next revision round.

    `_build_planner_prompt` is one-critic shaped, and ticket 05 reuses it
    completely unmodified rather than forking a panel-only variant (see the
    ticket's own instructions: extend the round loop, don't write a parallel
    one). This is the seam that makes that reuse possible: collapse two
    responses into the one string the existing single-critic-shaped
    machinery already knows how to hold as revision context for the Planner.
    It is deliberately not fancier than a labeled concatenation — nothing
    about *this* feedback string needs the two voices kept separate, since
    the Planner is meant to read and address both at once.

    Ticket 05 also fed this same folded string into `_build_stalemate_report`
    as a stand-in `critic_position` when a panel exhausted its rounds without
    consensus; ticket 06 replaced that specific use with a proper three-voice
    report (`_build_stalemate_report`'s `critic_b_position` parameter) that
    keeps Critic A's and Critic B's final positions separate instead of
    folding them — see `AdvisoryStalemateReport`'s docstring for why. This
    function's only remaining caller is the per-round Planner-feedback path
    below.
    """
    return f"Critic A:\n{critic_a_response}\n\nCritic B:\n{critic_b_response}"


def run_advisory_consultation_debate(
    task_description: str,
    invoke_worker: InvokeWorker | None = None,
    *,
    root_dir: Path,
    occasion: Occasion = "ambiguity",
    complexity: str = "medium",
    max_rounds: int = MAX_DEBATE_ROUNDS,
    planner_model: str = "Claude Opus 5 (Thinking)",
    critic_model: str = "Codex 5.6 Sol",
    planner_effort: str = "high",
    critic_effort: str = "high",
    critic_a_model: str = "Codex 5.6 Sol",
    critic_a_effort: str = "high",
    critic_b_model: str = "Gemini 3.6 Flash",
    critic_b_effort: str = "high",
    task_id: str | None = None,
    reachability_check: IsFamilyReachable | None = None,
    roster_config_path: Path = _CONFIG_PATH,
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
    to replace the primary outcome. The same is true of the learning
    journal: if it cannot be wired to this run's worker calls — a caller
    ``task_id`` the journal's own validation rejects, say — the consultation
    runs un-instrumented and says so in ``error``, rather than failing as
    ``worker_error`` before a worker is contacted.

    ``occasion`` (spec 0003 ticket 01) selects which mission the Planner and
    Critic prompts carry — see ``_MISSION_COPY``. It defaults to
    ``"ambiguity"``, spec 0001's sole occasion, so a call site that never
    mentions it behaves exactly as before this parameter existed: same
    prompts, same outcomes, same call counts. It is recorded on the returned
    ``AdvisoryDebateResult`` so a caller can observe which occasion ran.
    Wiring the other three occasions' real trigger predicates and blocking
    stance is later tickets' work, not this function's.

    ``complexity`` (spec 0003 ticket 05) selects, together with ``occasion``,
    which topology this round loop runs: the pair topology spec 0001 shipped
    (one Planner, one Critic — ``critic_model``/``critic_effort``), or the
    panel topology (one Planner, two independently invoked Critics —
    ``critic_a_model``/``critic_a_effort`` and ``critic_b_model``/
    ``critic_b_effort``) ticket 05 adds. The panel topology is selected only
    when ``occasion`` is ``"plan-review"`` or ``"code-review"`` *and*
    ``complexity`` normalizes to ``"complex"`` (see ``_is_panel_topology``);
    every other combination — including ``"complex"`` ambiguity or
    post-mortem — keeps the pair topology completely unchanged, and a call
    site that never mentions ``complexity`` defaults to ``"medium"``, which
    never selects a panel, so every pre-ticket-05 call site behaves exactly
    as before this parameter existed.

    In a panel round both Critics review the exact same Planner proposal
    (the same ``critic_prompt`` text ``_build_critic_prompt`` already builds
    for pair mode, addressed to each Critic's own ``model``/``effort`` at
    the ``invoke_worker`` call site — the prompt text itself does not differ
    between Critic A and Critic B), and each response is parsed through the
    same ``_parse_critic_verdict`` (ticket 02) against that proposal.
    Consensus requires *both* Critics' verdict to be ``"approved"`` in the
    same round; an unparseable verdict from either Critic ends the
    consultation immediately, exactly as a single unparseable verdict does
    in pair mode; any other combination (a split verdict, or both Critics
    objecting) folds both responses into one feedback string
    (``_combine_panel_critic_feedback``) for the Planner's next revision
    prompt and starts another round, up to the same ``max_rounds`` cap pair
    mode obeys — panel mode adds no rounds of its own. A panel that never
    reaches consensus by the cap produces a stalemate report built by the
    same ``_build_stalemate_report`` pair mode uses, called with both
    Critics' own last responses kept separate (spec 0003 ticket 06): the
    report's ``critic_position`` carries Critic A's final position and its
    ``critic_b_position`` carries Critic B's, never a folded combination of
    the two — see ``AdvisoryStalemateReport``'s docstring.

    ``reachability_check`` (spec 0003 ticket 07) is the opt-in seam for
    family-aware roster resolution: ``None`` by default, which leaves every
    pre-ticket-07 call site's behaviour completely unchanged — the explicit
    ``planner_model``/``critic_model``/``critic_a_model``/``critic_b_model``
    arguments (or their existing string defaults) are used exactly as they
    always were, and ``degraded_independence`` on the result stays ``False``.
    Supplying a callable opts in: it is passed to ``resolve_roster`` (with
    the topology this call already selected via ``occasion``/``complexity``)
    as the injected ``is_family_reachable`` check, and the roster it returns
    *replaces* ``planner_model``/``critic_a_model``/``critic_model`` (kept
    equal to the resolved ``critic_a_model`` in both topologies, matching
    ``result_critic_model``'s existing pair/panel convention above) and, in
    panel mode, ``critic_b_model`` — whatever those parameters were
    explicitly passed as is then ignored for this call. This is a
    deliberate all-or-nothing seam, not a per-parameter merge: mixing "some
    roles explicit, some resolved" would need a way to tell "caller passed
    this explicitly" apart from "caller left this at its default string",
    which plain string parameters cannot express. A caller wanting some
    roles pinned and others resolved should call ``resolve_roster`` itself
    and pass its own choice of explicit model arguments — that public
    function is exactly the alternative integration point for that case,
    already exposed as its own callable rather than folded into this one.
    ``roster_config_path`` threads through to ``resolve_roster`` unchanged,
    defaulting to this module's own ``routing-config.json``, exactly like
    ``needs_code_review_consultation``'s ``config_path`` parameter. A
    ``RosterResolutionError`` (no reachable family at all for some role) is
    caught and reported as a ``worker_error`` outcome, the same fail-closed
    treatment every other pre-flight failure to reach a worker already gets
    in this function.

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

    panel_mode = _is_panel_topology(occasion, complexity)
    # The `critic_model` the result/transcript/telemetry actually report.
    # Pair mode reports the `critic_model` parameter exactly as it always
    # has (every pre-ticket-05 assertion on `result.critic_model` stays
    # valid unmodified); panel mode reports Critic A's model instead, since
    # `critic_model` itself is never invoked in that mode and would
    # otherwise report a model this run never contacted. Recording both
    # Critics' models on the result is ticket 10's telemetry-extension job,
    # not this one's — see this ticket's report for why that line is drawn
    # here.
    result_critic_model = critic_a_model if panel_mode else critic_model
    # Spec 0003 (CriticalDialogue) ticket 07: set (if at all) only by the
    # roster-resolution block below, which runs after the sensitivity gate
    # and before this closure is ever invoked — see that block's own
    # comment for why. Declared here, before `_result` is defined, purely
    # so the sensitivity-halt branch (which calls `_result` before roster
    # resolution ever runs) reads a real `False` rather than raising
    # `UnboundLocalError`: a halted task never reaches roster resolution at
    # all, and correctly reports no degradation for a dialogue that never
    # ran.
    roster_degraded_independence = False

    rounds: list[AdvisoryDebateRound] = []
    previous_plan: str | None = None
    previous_critique: str | None = None
    # Panel mode only (spec 0003 ticket 06): each Critic's own last response,
    # tracked separately from `previous_critique` above — which stays the
    # *folded* string `_combine_panel_critic_feedback` builds for the
    # Planner's next revision prompt, unchanged from ticket 05. These two
    # carry the same information but serve different consumers: the folded
    # string is what the Planner reads mid-loop, and these two separated
    # strings are what a panel stalemate report reads at the cap, keeping
    # Critic A's and Critic B's final positions distinct rather than
    # re-parsing them back out of the fold. Both stay `None` in pair mode,
    # where they are never read.
    previous_critic_a_response: str | None = None
    previous_critic_b_response: str | None = None
    plan_path = root_dir / "implementation_plan.md"
    transcript_path = root_dir / _TRANSCRIPT_RELATIVE_PATH
    telemetry_path = root_dir / _TELEMETRY_RELATIVE_PATH

    # Set if the learning journal could not be wired to this run's worker
    # calls (see the `invoke_worker is None` branch below). Folded into
    # whatever outcome this run actually reaches, exactly like a transcript-
    # or telemetry-write failure: a secondary failure is reported, never
    # allowed to become the primary outcome.
    journal_wiring_error: str | None = None

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
            critic_model=result_critic_model,
            rounds=tuple(rounds),
            stalemate=stalemate,
            error=error,
            degraded_independence=roster_degraded_independence,
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
        folded_error = _fold_error(folded_error, journal_wiring_error)

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

    # Spec 0003 (CriticalDialogue) ticket 07: opt-in roster resolution.
    # Placed after the sensitivity gate (a halted task never needs a
    # roster) and before any worker is contacted (a resolved roster must be
    # in place before the first `invoke_worker` call uses it). Reassigning
    # `planner_model`/`critic_model`/`critic_a_model`/`critic_b_model` and
    # `result_critic_model` here, as plain enclosing-scope locals, is
    # sufficient for `_result` to see the resolved values: `_result` is a
    # closure that reads them at call time, not at its definition point
    # above, and its only call so far (`sensitivity_halt`) has already
    # returned by the time control reaches here.
    if reachability_check is not None:
        roster_topology: RosterTopology = "panel" if panel_mode else "pair"
        try:
            roster = resolve_roster(
                roster_topology,
                is_family_reachable=reachability_check,
                config_path=roster_config_path,
            )
        except RosterResolutionError as exc:
            cleanup_error = _remove_stale_plan_artifact(plan_path)
            return _result("worker_error", error=_fold_error(str(exc), cleanup_error))

        planner_model = roster.model_for("planner")
        critic_a_model = roster.model_for("critic_a")
        critic_model = critic_a_model
        if panel_mode:
            critic_b_model = roster.model_for("critic_b")
        result_critic_model = critic_a_model if panel_mode else critic_model
        roster_degraded_independence = roster.degraded_independence

    if invoke_worker is None:
        # Two failures with opposite handling, so two separate blocks. Without
        # a worker callable there is no consultation to run at all, and this
        # fails closed. Without journaling there is still a consultation — the
        # instrumentation is what degrades, and a run that would otherwise
        # have succeeded must not be reported as `worker_error` because the
        # journal could not be set up. Folding these two together is how a
        # caller-supplied `task_id` that `TaskLabel.for_task` rejects (a
        # space in it, say) used to fail every production consultation before
        # a worker was ever contacted.
        try:
            import production_invoker
        except Exception as exc:  # noqa: BLE001 - no worker callable at all fails closed.
            cleanup_error = _remove_stale_plan_artifact(plan_path)
            return _result("worker_error", error=_fold_error(str(exc), cleanup_error))

        invoke_worker = production_invoker.invoke_worker
        try:
            # Any non-`sensitivity_halt` outcome resolves the same task_id
            # (see `_resolve_task_id`); the sensitivity gate above already
            # ruled that outcome out for this call, so resolving here is
            # exactly the id `_result` will resolve again for whichever
            # outcome this run actually reaches — the journal record and
            # this run's telemetry record stay correlated by TaskIdentity.
            journaled_task_id = _resolve_task_id(task_description, task_id, "consensus")
            invoke_worker = production_invoker.make_journaled_invoke_worker(
                journaled_task_id, root_dir=root_dir
            )
        except Exception as exc:  # noqa: BLE001 - instrumentation never aborts what it observes.
            journal_wiring_error = (
                f"worker-execution journaling disabled for this run: {exc}"
            )

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

        # `critic_prompt` is built once and reused verbatim for every Critic
        # in the round — one call in pair mode, two independent
        # `invoke_worker` calls (Critic A, Critic B) in panel mode — never
        # rebuilt per-Critic. Spec 0003 ticket 05: a panel Critic is
        # addressed as a distinct role by the `model` argument it is invoked
        # with, not by different prompt text, so `_build_critic_prompt`
        # needs no panel-awareness of its own.
        critic_prompt = _build_critic_prompt(
            task_description, planner_plan, occasion=occasion
        )

        if panel_mode:
            try:
                critic_a_response = invoke_worker(
                    critic_a_model, critic_a_effort, critic_prompt
                )
            except Exception as exc:  # noqa: BLE001 - a worker failure must fail closed.
                cleanup_error = _remove_stale_plan_artifact(plan_path)
                return _result("worker_error", error=_fold_error(str(exc), cleanup_error))
            try:
                critic_b_response = invoke_worker(
                    critic_b_model, critic_b_effort, critic_prompt
                )
            except Exception as exc:  # noqa: BLE001 - a worker failure must fail closed.
                cleanup_error = _remove_stale_plan_artifact(plan_path)
                return _result("worker_error", error=_fold_error(str(exc), cleanup_error))

            rounds.append(
                AdvisoryDebateRound(planner_plan, critic_a_response, critic_b_response)
            )
            # Same VerdictContract parser (ticket 02), applied independently
            # to each Critic's response, both checked against the same
            # reviewed artifact `planner_plan` — never against each other.
            verdict_a = _parse_critic_verdict(critic_a_response, planner_plan)
            verdict_b = _parse_critic_verdict(critic_b_response, planner_plan)

            # An unparseable verdict from either Critic ends the panel
            # immediately, exactly as pair mode's single unparseable verdict
            # does: a malformed response must halt, never be folded into
            # "the panel asked for a revision" as if it were a reasoned
            # objection from whichever Critic actually engaged.
            if verdict_a.verdict == "unparseable" or verdict_b.verdict == "unparseable":
                cleanup_error = _remove_stale_plan_artifact(plan_path)
                return _result("unparseable_verdict", error=cleanup_error)

            if verdict_a.verdict == "approved" and verdict_b.verdict == "approved":
                panel_write_error: str | None = None
                try:
                    _atomic_text_write(plan_path, planner_plan)
                except OSError as exc:
                    panel_write_error = (
                        f"failed to write plan artifact at {plan_path}: {exc}"
                    )
                return _result(
                    "consensus", final_plan=planner_plan, error=panel_write_error
                )

            # Any other combination — one approves and one objects, or both
            # object — is not consensus and starts another round.
            previous_plan = planner_plan
            # The folded string `_build_planner_prompt` expects for the
            # Planner's next revision prompt — completely unmodified for
            # panel mode, ticket 05's original reuse.
            previous_critique = _combine_panel_critic_feedback(
                critic_a_response, critic_b_response
            )
            # Each Critic's own last response, kept separate (ticket 06) so
            # a stalemate report at the cap can carry three distinct voices
            # instead of re-deriving them from the fold above.
            previous_critic_a_response = critic_a_response
            previous_critic_b_response = critic_b_response
            continue

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
    # Spec 0003 ticket 06: a panel stalemate gets a three-voice report —
    # Critic A's and Critic B's last responses stay separate fields, never
    # folded into one string — via `_build_stalemate_report`'s
    # `critic_b_position` parameter. Pair mode's call below is completely
    # unchanged: two positional arguments, `critic_b_position` left at its
    # `None` default, byte-for-byte the same report shape spec 0001 shipped.
    if panel_mode:
        stalemate = _build_stalemate_report(
            previous_plan or "",
            previous_critic_a_response or "",
            previous_critic_b_response or "",
        )
    else:
        stalemate = _build_stalemate_report(previous_plan or "", previous_critique or "")
    return _result("stalemate", stalemate=stalemate, error=cleanup_error)


def _run_dispatched_post_mortem(
    task_description: str,
    invoke_worker: InvokeWorker | None,
    *,
    root_dir: Path,
    max_rounds: int,
    planner_model: str,
    critic_model: str,
    planner_effort: str,
    critic_effort: str,
    task_id: str | None,
) -> None:
    """`dispatch_post_mortem_consultation`'s actual thread target.

    `run_advisory_consultation_debate` already fails closed for every
    documented outcome (a worker error, a stalemate, an unparseable verdict,
    a sensitivity halt): each one reaches the function's own `_result`
    choke point, which writes the transcript and telemetry record before
    returning. Calling it from a background thread does not weaken any of
    that — those writes happen exactly the same way regardless of which
    thread is running the code.

    What a background thread changes is the *unhandled* case: an exception
    that is not one of those documented outcomes — a genuine bug, in this
    function or a future change to it, of a kind `run_advisory_consultation_debate`
    never anticipated catching. Raised from a synchronous call, that
    exception propagates to the caller: loud, ugly, but visible — the
    caller's process sees it. Raised inside a plain `threading.Thread`
    target, it instead hits Python's default unhandled-exception-in-thread
    behavior: printed once to stderr by `threading.excepthook` and then
    gone, with no transcript, no telemetry record, and nothing the
    dispatching caller can observe — a silent hole in exactly the kind of
    record-keeping this whole ticket exists to guarantee. This wrapper's
    only job is closing that hole: catch anything unexpected and record it
    through the same transcript/telemetry primitives
    `run_advisory_consultation_debate` itself uses (`_render_consultation_transcript`,
    `_write_transcript`, `_build_telemetry_record`, `_write_telemetry_record`,
    `_resolve_task_id` — same functions, same `_TRANSCRIPT_RELATIVE_PATH`/
    `_TELEMETRY_RELATIVE_PATH` paths, not a parallel implementation of any
    of them) rather than reinventing an error-reporting path for this one
    caller.

    The synthesized `AdvisoryDebateResult` this builds on an unexpected
    exception reuses the existing `"worker_error"` outcome rather than
    inventing a sixth `AdvisoryOutcome` value: `AdvisoryOutcome` is a closed
    `Literal`, and every caller that branches on it today was written
    against exactly five values, none of them "the dispatch mechanism
    itself broke." `"worker_error"` is the closest existing meaning — "the
    consultation could not be trusted to have run correctly" — and reusing
    it keeps this a minimal recovery net, not new type-level surface area,
    exactly as this ticket is scoped to be. `rounds_run=0` and
    `final_plan=""` are honest here: unlike a genuine `worker_error` from
    inside the round loop, this exception could have occurred before a
    single round ran (e.g. while resolving the task id), so claiming any
    rounds happened would overstate what is actually known. The `error`
    field carries the exception's `str()`, prefixed to make this failure
    mode distinguishable in a transcript from an ordinary `invoke_worker`
    failure — never the exception object itself, so this function never
    needs to reason about whether some future exception type's `__repr__`
    could leak more than a plain message would (`except Exception`, not
    `BaseException`, so `SystemExit`/`KeyboardInterrupt` still propagate —
    the module's existing convention everywhere else it catches worker
    failures).

    One deliberate departure from `_render_consultation_transcript`'s normal
    output: that renderer never includes `result.error` in the transcript
    text, for any outcome, anywhere in this module — a synchronous caller
    already has `result.error` on the object it was handed back, so writing
    it to disk too would be redundant, and every existing test that reads a
    `worker_error` transcript confirms only the outcome line is expected
    there. This function has no such synchronous caller to fall back on —
    dispatch discards the eventual `AdvisoryDebateResult` on purpose, so if
    the crash detail is not written down here, it is gone the moment this
    thread ends, sitting nowhere any operator could ever read it. So this
    one crash-only transcript gets an appended section carrying
    `crash_result.error` that `_render_consultation_transcript`'s own output
    for every other outcome and every other call site still deliberately
    omits.
    """
    try:
        run_advisory_consultation_debate(
            task_description,
            invoke_worker,
            root_dir=root_dir,
            occasion="post-mortem",
            max_rounds=max_rounds,
            planner_model=planner_model,
            critic_model=critic_model,
            planner_effort=planner_effort,
            critic_effort=critic_effort,
            task_id=task_id,
        )
    except Exception as exc:  # noqa: BLE001 - last-resort net for a dispatched thread; see docstring above.
        crash_result = AdvisoryDebateResult(
            rounds_run=0,
            final_plan="",
            outcome="worker_error",
            occasion="post-mortem",
            planner_model=planner_model,
            critic_model=critic_model,
            error=f"dispatch_post_mortem_consultation: unexpected exception: {exc}",
        )
        resolved_task_id = _resolve_task_id(task_description, task_id, "worker_error")
        transcript = _render_consultation_transcript(task_description, crash_result)
        transcript += (
            "\n\n## Dispatch failure\n\n"
            "This post-mortem's own dispatch mechanism raised an exception "
            "outside `run_advisory_consultation_debate`'s documented failure "
            f"paths:\n\n{crash_result.error}\n"
        )
        _write_transcript(root_dir / _TRANSCRIPT_RELATIVE_PATH, transcript)
        record = _build_telemetry_record(crash_result, task_id=resolved_task_id)
        _write_telemetry_record(root_dir / _TELEMETRY_RELATIVE_PATH, record)


# Spec 0003 (CriticalDialogue) ticket 04: blocking stance per occasion. Plan
# review and code review need no new code at all: calling
# `run_advisory_consultation_debate` synchronously already blocks the
# caller, because it is an ordinary Python function call — the caller's next
# line simply does not execute until the round loop above returns. That is
# spec 0003's "Plan-review and code-review dialogues gate progress"
# (Implementation Decisions, "Blocking stance") in its entirety for those two
# occasions: nothing to build, only to characterize with a test, so a future
# change that accidentally makes either occasion non-blocking (e.g. by
# routing it through the dispatcher below by mistake) gets caught. See
# `AdvisoryBlockingStanceTests` in `test_routing.py`.
#
# Post-mortem needs the opposite stance: "Post-mortems run in the background
# and never block; their occurrence and outcome are still recorded." Two
# facts about this module made the mechanism choice easy. First, there is no
# threading or asyncio precedent anywhere in this file or its siblings to
# preserve or work around: `agent_council.py` uses `asyncio` internally, but
# for something unrelated to this loop, and nothing in either module has
# ever spawned a thread before this ticket. Second, there is today no
# *production* caller of `run_advisory_consultation_debate` at all — every
# call site is a test, confirmed by grep across the repo
# (`test_routing.py` and `test_production_invoker.py`, both under
# `skills/worker-routing/`) — so there is no existing production call-site
# contract this dispatcher needs to slot into, only a future one to make
# possible. Given both, `threading.Thread` is the smallest correct
# mechanism: stdlib, no new dependency, and its target (`_run_dispatched_post_mortem`,
# below) calls `run_advisory_consultation_debate` completely unmodified, so
# every one of that function's existing side effects (the transcript, the
# telemetry record, and — on consensus — the plan artifact) fires exactly as
# documented, just from a background thread instead of the caller's own; the
# wrapper adds only a last-resort exception net around that call (see
# `_run_dispatched_post_mortem`'s own docstring), not any change to the
# call itself. Nothing here is a job queue, a retry policy, or a supervisor:
# one dispatch is one thread, and `dispatch_post_mortem_consultation`'s only
# job is starting it and handing the caller its handle.
#
# That thread is started non-daemon (`daemon=False`, the `threading.Thread`
# default — spelled out explicitly below rather than left implicit, because
# getting this one flag wrong would quietly break the ticket's own
# guarantee). A daemon thread is killed without any cleanup the instant the
# interpreter has no non-daemon threads left to run — which, for a
# short-lived CLI or mission runner, could easily be moments after
# `dispatch_post_mortem_consultation` returns. A blocking call can *never*
# have that failure mode: the process is physically inside the call and
# cannot exit before the write happens. A daemon dispatch thread could —
# the process exits, the debate is killed mid-round, and the ticket's
# promise ("A post-mortem's eventual record ... is still written and
# discoverable after the fact, exactly as if it had blocked") silently does
# not hold. `daemon=False` instead makes the interpreter wait for this
# thread before it can exit at all, which is what actually backs "the mission
# path returns immediately, but the record still lands eventually" rather
# than merely "the record lands eventually, unless the process happens to
# exit first." This is not a new hang risk introduced by this ticket: each
# `invoke_worker` call already carries its own bounded timeout
# (`production_invoker.DEFAULT_TIMEOUT_SECONDS`, inherited from spec 0001's
# per-round time limit), and `MAX_DEBATE_ROUNDS` bounds how many times that
# timeout can be hit — so a non-daemon dispatch thread is bounded by the
# same ceiling a synchronous call already was, not an unbounded one.
def dispatch_post_mortem_consultation(
    task_description: str,
    invoke_worker: InvokeWorker | None = None,
    *,
    root_dir: Path,
    max_rounds: int = MAX_DEBATE_ROUNDS,
    planner_model: str = "Claude Opus 5 (Thinking)",
    critic_model: str = "Codex 5.6 Sol",
    planner_effort: str = "high",
    critic_effort: str = "high",
    task_id: str | None = None,
) -> threading.Thread:
    """Dispatch a post-mortem CriticalDialogue on a background thread and return immediately.

    Mirrors `run_advisory_consultation_debate`'s parameter set exactly,
    minus `occasion`: this function exists specifically to dispatch the
    post-mortem occasion (its name says so), so `occasion` is hardcoded to
    `"post-mortem"` in the call below rather than exposed as a knob — a
    caller wanting a background dispatch of a different occasion is out of
    this ticket's scope (spec 0003 only specifies post-mortem as
    non-blocking) and would need its own function, not a parameter bolted
    onto this one that could be misused to silently make a supposedly-
    blocking occasion non-blocking.

    Delegates to `run_advisory_consultation_debate` as the background
    thread's target — via `_run_dispatched_post_mortem`, a thin wrapper
    described below, but the call inside it is unmodified — so every
    documented side effect (the transcript at
    `root_dir / ".scratch" / "planning_debate.md"` and the telemetry record
    at `root_dir / ".ralph" / "routing_telemetry.jsonl"`) still happens,
    just after this function has already returned control to its caller.
    The `AdvisoryDebateResult` that call eventually produces is not captured
    or exposed here: nobody is waiting for it — that is the entire point of
    "does not block" — and its outcome is fully recoverable from the
    transcript and telemetry it writes once the thread completes.

    `max_rounds` is validated synchronously, before the thread is even
    started, exactly the same check `run_advisory_consultation_debate`
    itself makes (raising `ValueError` for `< 1`). This is deliberate, not
    redundant: a bad `max_rounds` is a call-site programming error, and if
    it were left to surface only inside the background thread, it would
    become an unhandled thread exception — printed to stderr by Python's
    default thread excepthook, never raised back to the caller, and never
    written to a transcript or telemetry record, since the function would
    have failed before reaching its own choke point. That would hide exactly
    the kind of mistake this module raises loudly for everywhere else. Every
    other non-consensus outcome (a worker error, a stalemate, an
    unparseable verdict, a sensitivity halt) is a legitimate dialogue
    outcome rather than a programming error, so each is left to happen
    inside the thread and be recorded by `run_advisory_consultation_debate`'s
    own choke point exactly as it would be for a synchronous call.

    Returns the already-started `threading.Thread` (`daemon=False` — see the
    module comment above `dispatch_post_mortem_consultation` for why a
    daemon thread here would risk the record never being written at all). A
    caller does not need this handle to get the non-blocking guarantee; it
    is returned so a test — or a caller that genuinely wants to wait, e.g.
    at orderly shutdown — can `.join()` it deterministically instead of
    polling the filesystem or sleeping.
    """
    if max_rounds < 1:
        raise ValueError(f"max_rounds must be >= 1, got {max_rounds}")

    thread = threading.Thread(
        target=_run_dispatched_post_mortem,
        args=(task_description, invoke_worker),
        kwargs={
            "root_dir": root_dir,
            "max_rounds": max_rounds,
            "planner_model": planner_model,
            "critic_model": critic_model,
            "planner_effort": planner_effort,
            "critic_effort": critic_effort,
            "task_id": task_id,
        },
        name="advisory-post-mortem-dispatch",
        daemon=False,
    )
    thread.start()
    return thread
