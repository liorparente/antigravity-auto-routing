"""Pure, immutable state transitions for Planner/Critic debates.

This module deliberately has no transport or persistence dependencies.  Its
only sibling dependency is the dialogue contract that owns shared wire types.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from types import MappingProxyType
from typing import Any, Literal, overload

if __package__:
    from .dialogue_contracts import (
        AdvisoryResolutionOption,
        AdvisoryStalemateReport,
        Occasion,
        PerspectiveReviewResult,
        StructuredFinding,
    )
else:
    from dialogue_contracts import (  # type: ignore[no-redef]
        AdvisoryResolutionOption,
        AdvisoryStalemateReport,
        Occasion,
        PerspectiveReviewResult,
        StructuredFinding,
    )

__all__ = [
    "CONSULTATION_TOPOLOGIES",
    "DEFAULT_VOTE_CONFIDENCE",
    "PANEL_TOPOLOGY_OCCASIONS",
    "VALID_CONSENSUS_OUTCOMES",
    "AdvisoryResolutionOption",
    "AdvisoryStalemateReport",
    "ConsensusTable",
    "ConsultationTopology",
    "CriticResponse",
    "DebateRoundRecord",
    "DebateSessionState",
    "DebateState",
    "Occasion",
    "RoundTurnResult",
    "SecurityVeto",
    "SecurityVetoHandler",
    "VoteInput",
    "advance_debate_state",
    "build_stalemate_report",
    "evaluate_quorum",
    "evaluate_round_verdicts",
    "evaluate_weighted_quorum",
    "is_panel_topology",
    "resolve_topology",
]

PANEL_TOPOLOGY_OCCASIONS: tuple[Occasion, ...] = ("plan-review", "code-review")

# Ticket 43.1: the two supported consultation shapes named as a closed
# vocabulary, rather than left implicit in `is_panel_topology`'s boolean —
# "dyad" is the 1-on-1 Planner/Critic pair (spec 0009's Medium-complexity
# path), "council_panel" is the weighted N-reviewer quorum `ConsensusTable`
# already evaluates (spec 0009/0012's Complex-complexity path).
ConsultationTopology = Literal["dyad", "council_panel"]
CONSULTATION_TOPOLOGIES: tuple[ConsultationTopology, ...] = ("dyad", "council_panel")

DEFAULT_VOTE_CONFIDENCE: dict[str, float] = {
    "approve": 1.0,
    "approved": 1.0,
    "unanimous": 1.0,
    "revise": -0.3,
    "block": -1.0,
    "blocked": -1.0,
    "abstain": 0.0,
}
VALID_CONSENSUS_OUTCOMES = frozenset({
    "UNANIMOUS", "QUALIFIED", "MATERIAL_DISAGREEMENT", "INCOMPLETE", "UNRESOLVED",
})


def is_panel_topology(occasion: Occasion, complexity: str) -> bool:
    """Return whether an occasion uses two independent Critics."""
    return occasion in PANEL_TOPOLOGY_OCCASIONS and complexity.lower().strip() == "complex"


def resolve_topology(occasion: Occasion, complexity: str) -> ConsultationTopology:
    """Resolve which `ConsultationTopology` an occasion/complexity pair uses.

    A thin, named wrapper over `is_panel_topology`'s existing boolean: a
    caller building a mission brief or a facade delegator wants the
    topology's name, not a bit it has to re-interpret.
    """
    return "council_panel" if is_panel_topology(occasion, complexity) else "dyad"


def build_stalemate_report(
    planner_position: str,
    critic_position: str | None = None,
    critic_b_position: str | None = None,
    *,
    perspective_positions: Mapping[str, str] | None = None,
) -> AdvisoryStalemateReport:
    """Build the stable pair-, panel-, or Council-perspective human-resolution report.

    Pair and panel topology (`critic_position`/`critic_b_position`) are
    unchanged from spec 0003. `perspective_positions` is spec 0012 ticket
    07's addition for a Council review's N-way perspective panel: a mapping
    of each `ReviewerPerspective` to its formatted verdict/finding summary,
    joined into one `critic_position` block (`"<perspective>:\\n<text>"` per
    entry) rather than forcing an N-way panel into the two-critic pair/panel
    shape. When supplied, it takes precedence over `critic_position`/
    `critic_b_position`, which are ignored.
    """
    if perspective_positions:
        formatted_critic_position = "\n\n".join(
            f"{perspective}:\n{position}" for perspective, position in perspective_positions.items()
        )
        return AdvisoryStalemateReport(
            planner_position=planner_position,
            critic_position=formatted_critic_position,
            options=(
                AdvisoryResolutionOption(1, "Approve Planner Architecture", planner_position),
                AdvisoryResolutionOption(2, "Approve Council Perspectives", formatted_critic_position),
                AdvisoryResolutionOption(3, "Escalate to Human Decision", "Halt execution and request user review"),
            ),
        )
    if critic_position is None:
        raise ValueError("critic_position is required unless perspective_positions is supplied")
    if critic_b_position is None:
        return AdvisoryStalemateReport(
            planner_position=planner_position,
            critic_position=critic_position,
            options=(
                AdvisoryResolutionOption(1, "Approve Planner Architecture", planner_position),
                AdvisoryResolutionOption(2, "Approve Critic Architecture", critic_position),
                AdvisoryResolutionOption(3, "Escalate to Human Decision", "Halt execution and request user review"),
            ),
        )
    combined = f"Critic A:\n{critic_position}\n\nCritic B:\n{critic_b_position}"
    return AdvisoryStalemateReport(
        planner_position=planner_position,
        critic_position=critic_position,
        critic_b_position=critic_b_position,
        options=(
            AdvisoryResolutionOption(1, "Approve Planner Architecture", planner_position),
            AdvisoryResolutionOption(2, "Approve Critics' Architecture", combined),
            AdvisoryResolutionOption(3, "Escalate to Human Decision", "Halt execution and request user review"),
        ),
    )


def _normalize_verdict(verdict: str | None) -> str | None:
    if not isinstance(verdict, str):
        return None
    normalized = verdict.strip().casefold()
    if normalized in ("approve", "approved", "unanimous"):
        return "APPROVE"
    if normalized == "revise":
        return "REVISE"
    if normalized in ("block", "blocked"):
        return "BLOCK"
    if normalized == "abstain":
        return "ABSTAIN"
    return None


def _deep_freeze(value: Any) -> Any:
    """Recursively detach findings from mutable caller-owned values."""
    if isinstance(value, dict):
        return MappingProxyType({key: _deep_freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_deep_freeze(item) for item in value)
    if isinstance(value, set):
        return frozenset(_deep_freeze(item) for item in value)
    return value


@dataclass(frozen=True)
class CriticResponse:
    critic_id: str
    response: str
    verdict: str | None = None
    confidence: float | None = None
    candidate_hash: str | None = None
    findings: tuple[Mapping[str, Any], ...] = ()

    def __post_init__(self) -> None:
        """Recursively detach the immutable response from caller-owned findings."""
        object.__setattr__(self, "findings", tuple(_deep_freeze(finding) for finding in self.findings))


VoteInput = dict[str, Any] | CriticResponse | PerspectiveReviewResult


def _field(vote: VoteInput, name: str, default: Any = None) -> Any:
    """Read a field from any supported critic-vote representation."""
    return vote.get(name, default) if isinstance(vote, dict) else getattr(vote, name, default)


class SecurityVeto(Exception):
    """A unilateral security finding that halts a consultation."""

    def __init__(self, provider: str, finding: dict[str, Any]) -> None:
        self.provider = provider
        self.finding = finding
        claim = finding.get("claim", finding.get("id", "unspecified"))
        super().__init__(f"Security veto by {provider}: {claim}")


class SecurityVetoHandler:
    """Detect configured high-confidence security findings in critic votes."""

    DEFAULT_SECURITY_THRESHOLD = 0.80
    DEFAULT_VETO_SEVERITIES = frozenset({"critical", "high"})

    def __init__(
        self,
        veto_severities: list[str] | tuple[str, ...] | set[str] | None = None,
        security_threshold: float = 0.80,
        enabled: bool = True,
    ) -> None:
        if (
            not isinstance(veto_severities, (list, tuple, set, frozenset))
            or not veto_severities
            or not all(
                isinstance(severity, str) and bool(severity.strip())
                for severity in veto_severities
            )
        ):
            self.veto_severities = set(self.DEFAULT_VETO_SEVERITIES)
        else:
            self.veto_severities = {
                severity.strip().casefold() for severity in veto_severities
            }
        if (
            isinstance(security_threshold, bool)
            or not isinstance(security_threshold, (int, float))
            or not math.isfinite(float(security_threshold))
            or not 0.0 <= float(security_threshold) <= 1.0
        ):
            self.security_threshold = self.DEFAULT_SECURITY_THRESHOLD
        else:
            self.security_threshold = float(security_threshold)
        self.enabled = enabled

    @staticmethod
    def _is_finding(value: Any) -> bool:
        return isinstance(value, (Mapping, StructuredFinding))

    @staticmethod
    def _finding_field(
        finding: Mapping[str, Any] | StructuredFinding, name: str, default: Any = None
    ) -> Any:
        """Read one field from either supported finding representation — a
        Mapping (the wire shape) or a `StructuredFinding` dataclass instance
        (spec 0012's parsed perspective-reviewer shape)."""
        return finding.get(name, default) if isinstance(finding, Mapping) else getattr(finding, name, default)

    @staticmethod
    def _finding_to_dict(finding: Mapping[str, Any] | StructuredFinding) -> dict[str, Any]:
        return asdict(finding) if isinstance(finding, StructuredFinding) else dict(finding)

    def _confidence(self, finding: Mapping[str, Any] | StructuredFinding) -> float:
        raw_confidence = self._finding_field(finding, "confidence", 1.0)
        if isinstance(raw_confidence, bool):
            return 1.0
        try:
            confidence = float(raw_confidence)
        except (TypeError, ValueError):
            return 1.0
        if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
            return 1.0
        return confidence

    @staticmethod
    def _default_block_finding() -> dict[str, Any]:
        return {"claim": "unilateral security block verdict", "severity": "critical"}

    def check(self, votes: Sequence[VoteInput]) -> SecurityVeto | None:
        """Return the first configured veto, treating malformed confidence as certain.

        Two independent triggers, checked per vote in order:

        Trigger 1 — domain-agnostic finding severity. Any vote, from any
        provider, carrying a finding (a Mapping or a `StructuredFinding`,
        checked by shape via `_is_finding`/`_finding_field` so both
        representations are recognized interchangeably) whose severity is
        configured (`veto_severities`, default critical/high) and whose
        confidence meets `security_threshold` vetoes — regardless of that
        vote's own verdict (APPROVE/REVISE/BLOCK all trigger).

        Trigger 2 — unilateral `reviewer_security` block. An explicit
        `"BLOCK"` verdict (see `_normalize_verdict`) from the perspective
        reviewer named `reviewer_security` vetoes unconditionally, using its
        first attached finding if present or a synthesized default finding
        otherwise (spec 0012's unilateral security veto — this is the one
        case the handler fabricates a finding, since a bare `BLOCK` carries
        none to report). A `"BLOCK"` verdict from any other provider does
        NOT veto by itself — that is a plain disapproval, not a security
        signal; it can still veto, but only through Trigger 1.
        """
        if not self.enabled:
            return None
        for vote in votes:
            findings = _field(vote, "findings", ())
            if not isinstance(findings, (list, tuple)):
                findings = ()
            provider = str(
                _field(
                    vote,
                    "perspective",
                    _field(vote, "provider", _field(vote, "critic_id", "unknown")),
                )
                or "unknown"
            )

            for finding in findings:
                if not self._is_finding(finding):
                    continue
                severity = str(self._finding_field(finding, "severity", "")).strip().casefold()
                if severity not in self.veto_severities:
                    continue
                if self._confidence(finding) >= self.security_threshold:
                    return SecurityVeto(provider, self._finding_to_dict(finding))

            verdict_value = _field(vote, "vote", _field(vote, "verdict", None))
            if _normalize_verdict(verdict_value) != "BLOCK":
                continue
            if provider == "reviewer_security":
                block_finding = next((f for f in findings if self._is_finding(f)), None)
                finding_dict = (
                    self._finding_to_dict(block_finding)
                    if block_finding is not None
                    else self._default_block_finding()
                )
                return SecurityVeto(provider, finding_dict)
        return None


class ConsensusTable:
    """Pure weighted reducer for a council's structured critic votes."""

    def __init__(
        self,
        policy: list[str] | tuple[str, ...] | None = None,
        weights: dict[str, float] | None = None,
        quorum_threshold: float = 0.60,
    ) -> None:
        self.policy = tuple(policy or ())
        self._policy_is_valid = all(
            isinstance(outcome, str) and outcome in VALID_CONSENSUS_OUTCOMES
            for outcome in self.policy
        )
        # Ignore malformed weights rather than letting them corrupt the reducer.
        clean_weights: dict[str, float] = {}
        for provider, raw_weight in (weights or {}).items():
            if isinstance(raw_weight, bool):
                continue
            try:
                weight = float(raw_weight)
            except (TypeError, ValueError):
                continue
            if math.isfinite(weight) and weight >= 0.0:
                clean_weights[provider] = weight
        self.weights = MappingProxyType(clean_weights)
        if isinstance(quorum_threshold, bool):
            threshold = 0.60
        else:
            try:
                threshold = float(quorum_threshold)
            except (TypeError, ValueError):
                threshold = 0.60
        self.quorum_threshold = threshold if math.isfinite(threshold) and 0.0 <= threshold <= 1.0 else 0.60

    def _confidence(self, vote: VoteInput) -> float:
        verdict = _field(vote, "vote", _field(vote, "verdict", ""))
        if _normalize_verdict(verdict) is None:
            return 0.0
        confidence = _field(vote, "confidence")
        if confidence is None:
            confidence = DEFAULT_VOTE_CONFIDENCE.get(str(verdict).strip().casefold(), 0.0)
        try:
            value = float(confidence)
        except (TypeError, ValueError):
            value = 0.0
        if not math.isfinite(value):
            return 0.0
        return max(-1.0, min(1.0, value))

    def _identity(self, vote: VoteInput) -> str:
        """A vote's weight-lookup key: its Council `perspective` (spec 0012
        ticket 07) when present, else its legacy `provider`/`critic_id`."""
        return str(
            _field(vote, "perspective", _field(vote, "provider", _field(vote, "critic_id", "")))
            or ""
        )

    def invalid_voters(self, votes: Sequence[VoteInput]) -> tuple[str, ...]:
        invalid = []
        for vote in votes:
            verdict = _field(vote, "vote", _field(vote, "verdict", ""))
            if _normalize_verdict(verdict) is None:
                invalid.append(f"{self._identity(vote)}={verdict}")
        return tuple(invalid)

    def _enforce_policy(self, outcome: str) -> str:
        if not self._policy_is_valid:
            return "INCOMPLETE"
        if self.policy and outcome not in self.policy:
            return "INCOMPLETE"
        return outcome

    def weighted_score(self, votes: Sequence[VoteInput]) -> float:
        if not votes:
            return 0.0
        raw_weights = [self.weights.get(self._identity(vote), 0.0) for vote in votes]
        total_weight = sum(raw_weights)
        effective_weights = raw_weights if total_weight > 0 else [1.0] * len(votes)
        total_weight = total_weight if total_weight > 0 else float(len(votes))
        score = 0.0
        for vote, weight in zip(votes, effective_weights):
            verdict = _field(vote, "vote", _field(vote, "verdict", ""))
            if _normalize_verdict(verdict) == "APPROVE":
                score += weight * max(0.0, self._confidence(vote))
        return score / total_weight

    def evaluate(
        self,
        votes: Sequence[VoteInput],
        expected_hash: str | None = None,
        require_candidate_hashes: bool = False,
    ) -> str:
        if not votes or any(not self._identity(vote) for vote in votes) or self.invalid_voters(votes):
            return self._enforce_policy("INCOMPLETE")
        hashes = [_field(vote, "candidate_hash") for vote in votes]
        ratification_required = (
            expected_hash is not None
            or require_candidate_hashes
            or any(candidate_hash is not None for candidate_hash in hashes)
        )
        if ratification_required:
            if any(not isinstance(candidate_hash, str) or not candidate_hash for candidate_hash in hashes):
                return self._enforce_policy("MATERIAL_DISAGREEMENT")
            if expected_hash is not None:
                if any(candidate_hash != expected_hash for candidate_hash in hashes):
                    return self._enforce_policy("MATERIAL_DISAGREEMENT")
            elif len(set(hashes)) != 1:
                return self._enforce_policy("MATERIAL_DISAGREEMENT")
        score = self.weighted_score(votes)
        if score < self.quorum_threshold:
            return self._enforce_policy("UNRESOLVED")
        vote_names = [
            str(_field(vote, "vote", _field(vote, "verdict", ""))).strip().casefold()
            for vote in votes
        ]
        is_unanimous = all(_normalize_verdict(vote) == "APPROVE" for vote in vote_names)
        return self._enforce_policy("UNANIMOUS" if is_unanimous else "QUALIFIED")


def evaluate_weighted_quorum(
    responses: Sequence[VoteInput],
    weights: dict[str, float] | None = None,
    quorum_threshold: float = 0.60,
    require_candidate_hashes: bool = False,
    expected_hash: str | None = None,
) -> tuple[bool, str, float, str | None]:
    """Evaluate a critic panel, returning consensus, outcome, score, and error."""
    table = ConsensusTable(weights=weights, quorum_threshold=quorum_threshold)
    outcome = table.evaluate(
        responses,
        expected_hash=expected_hash,
        require_candidate_hashes=require_candidate_hashes,
    )
    invalid_voters = table.invalid_voters(responses)
    if invalid_voters:
        return False, "INCOMPLETE", 0.0, f"unparseable verdict: {', '.join(invalid_voters)}"
    score = table.weighted_score(responses)
    consensus = outcome in {"UNANIMOUS", "QUALIFIED"}
    error = "material disagreement in candidate hashes" if outcome == "MATERIAL_DISAGREEMENT" else None
    return consensus, outcome, score, error


def evaluate_quorum(
    responses: Sequence[CriticResponse], policy: str = "unanimous"
) -> tuple[bool, str | None]:
    """Evaluate valid Critic votes, failing closed for malformed input.

    ``unanimous`` requires every Critic to approve, ``majority`` requires more
    than half, and ``qualified`` requires at least two thirds (rounded up).
    """
    normalized_policy = policy.strip().casefold() if isinstance(policy, str) else ""
    if normalized_policy not in {"unanimous", "majority", "qualified"}:
        return False, f"unknown quorum policy: {policy}"
    if not responses:
        return False, "unparseable verdict: no critic responses"
    invalid = [response for response in responses if _normalize_verdict(response.verdict) is None]
    if invalid:
        labels = ", ".join(f"{response.critic_id}={response.verdict}" for response in invalid)
        return False, f"unparseable verdict: {labels}"
    approvals = sum(_normalize_verdict(response.verdict) == "APPROVE" for response in responses)
    count = len(responses)
    if normalized_policy == "unanimous":
        required = count
    elif normalized_policy == "majority":
        required = count // 2 + 1
    else:
        required = (2 * count + 2) // 3
    return approvals >= required, None


def evaluate_round_verdicts(
    critic_a_verdict: str | None,
    critic_b_verdict: str | None = None,
    *,
    is_panel: bool = False,
    quorum_policy: str = "unanimous",
) -> tuple[bool, str | None]:
    """Evaluate legacy pair/panel verdict fields through the quorum reducer."""
    if not is_panel:
        normalized = _normalize_verdict(critic_a_verdict)
        return (
            normalized == "APPROVE",
            None if normalized is not None else f"unparseable verdict: {critic_a_verdict}",
        )
    responses = (
        CriticResponse("critic_a", "", critic_a_verdict),
        CriticResponse("critic_b", "", critic_b_verdict),
    )
    consensus, error = evaluate_quorum(responses, quorum_policy)
    if error and error.startswith("unparseable verdict:"):
        return False, f"unparseable verdict: critic_a={critic_a_verdict}, critic_b={critic_b_verdict}"
    return consensus, error


@dataclass(frozen=True)
class DebateRoundRecord:
    round_index: int
    planner_plan: str
    critic_a_response: str
    critic_b_response: str | None = None
    critic_a_verdict: str | None = None
    critic_b_verdict: str | None = None
    is_consensus: bool = False
    error: str | None = None


@dataclass(frozen=True)
class DebateSessionState:
    occasion: Occasion
    complexity: str
    max_rounds: int
    is_panel: bool
    rounds: tuple[DebateRoundRecord, ...] = ()
    consensus_reached: bool = False
    final_plan: str | None = None
    stalemate_report: AdvisoryStalemateReport | None = None
    error: str | None = None


@dataclass(frozen=True)
class RoundTurnResult:
    round_index: int
    planner_proposal: str
    critic_responses: tuple[CriticResponse, ...]
    is_consensus: bool = False
    error: str | None = None


@dataclass(frozen=True)
class DebateState:
    occasion: Occasion
    task_description: str
    task_id: str
    round_number: int
    max_rounds: int
    planner_proposals: tuple[str, ...]
    critic_responses: tuple[tuple[CriticResponse, ...], ...]
    status: str
    final_plan: str | None = None
    stalemate_report: AdvisoryStalemateReport | None = None
    error: str | None = None


def _advance_session_state(state: DebateSessionState, record: DebateRoundRecord) -> DebateSessionState:
    if (
        state.consensus_reached
        or state.error is not None
        or state.stalemate_report is not None
        or len(state.rounds) >= state.max_rounds
    ):
        return state
    consensus, error = evaluate_round_verdicts(
        record.critic_a_verdict, record.critic_b_verdict, is_panel=state.is_panel
    )
    normalized_record = replace(record, is_consensus=consensus, error=error)
    rounds = (*state.rounds, normalized_record)
    if consensus:
        return replace(
            state,
            rounds=rounds,
            consensus_reached=True,
            final_plan=record.planner_plan,
            stalemate_report=None,
            error=error,
        )
    if error:
        return replace(
            state,
            rounds=rounds,
            consensus_reached=False,
            final_plan=None,
            stalemate_report=None,
            error=error,
        )
    if len(rounds) >= state.max_rounds:
        report = build_stalemate_report(
            record.planner_plan,
            record.critic_a_response,
            record.critic_b_response if state.is_panel else None,
        )
        return replace(
            state,
            rounds=rounds,
            consensus_reached=False,
            final_plan=None,
            stalemate_report=report,
            error=None,
        )
    return replace(state, rounds=rounds)


def _advance_general_state(
    state: DebateState,
    turn: RoundTurnResult,
    quorum_policy: str,
    weights: dict[str, float] | None = None,
    quorum_threshold: float = 0.60,
    *,
    require_candidate_hashes: bool = False,
    expected_hash: str | None = None,
) -> DebateState:
    if state.status != "in_progress":
        return state
    if quorum_policy.strip().casefold() == "weighted":
        consensus, _outcome, _score, error = evaluate_weighted_quorum(
            turn.critic_responses,
            weights,
            quorum_threshold,
            require_candidate_hashes=require_candidate_hashes,
            expected_hash=expected_hash,
        )
    else:
        consensus, error = evaluate_quorum(turn.critic_responses, quorum_policy)
    recorded_turn = replace(turn, is_consensus=consensus, error=error)
    proposals = (*state.planner_proposals, turn.planner_proposal)
    responses = (*state.critic_responses, turn.critic_responses)
    if error:
        return replace(
            state,
            round_number=turn.round_index,
            planner_proposals=proposals,
            critic_responses=responses,
            status="error",
            error=error,
        )
    if consensus:
        return replace(
            state,
            round_number=turn.round_index,
            planner_proposals=proposals,
            critic_responses=responses,
            status="consensus",
            final_plan=recorded_turn.planner_proposal,
            stalemate_report=None,
            error=None,
        )
    if len(proposals) >= state.max_rounds:
        critic_a = turn.critic_responses[0].response if turn.critic_responses else ""
        critic_b = turn.critic_responses[1].response if len(turn.critic_responses) > 1 else None
        report = build_stalemate_report(turn.planner_proposal, critic_a, critic_b)
        return replace(
            state,
            round_number=turn.round_index,
            planner_proposals=proposals,
            critic_responses=responses,
            status="stalemate",
            final_plan=None,
            stalemate_report=report,
            error=None,
        )
    return replace(state, round_number=turn.round_index, planner_proposals=proposals, critic_responses=responses)


@overload
def advance_debate_state(
    state: DebateSessionState,
    record: DebateRoundRecord,
    quorum_policy: str = "unanimous",
    *,
    weights: dict[str, float] | None = None,
    quorum_threshold: float = 0.60,
    require_candidate_hashes: bool = False,
    expected_hash: str | None = None,
) -> DebateSessionState: ...


@overload
def advance_debate_state(
    state: DebateState,
    record: RoundTurnResult,
    quorum_policy: str = "unanimous",
    *,
    weights: dict[str, float] | None = None,
    quorum_threshold: float = 0.60,
    require_candidate_hashes: bool = False,
    expected_hash: str | None = None,
) -> DebateState: ...


def advance_debate_state(
    state: DebateSessionState | DebateState,
    record: DebateRoundRecord | RoundTurnResult,
    quorum_policy: str = "unanimous",
    *,
    weights: dict[str, float] | None = None,
    quorum_threshold: float = 0.60,
    require_candidate_hashes: bool = False,
    expected_hash: str | None = None,
) -> DebateSessionState | DebateState:
    """Advance either supported immutable debate state without side effects."""
    if isinstance(state, DebateSessionState) and isinstance(record, DebateRoundRecord):
        return _advance_session_state(state, record)
    if isinstance(state, DebateState) and isinstance(record, RoundTurnResult):
        return _advance_general_state(
            state,
            record,
            quorum_policy,
            weights,
            quorum_threshold,
            require_candidate_hashes=require_candidate_hashes,
            expected_hash=expected_hash,
        )
    raise TypeError("state and record must use the same debate state-machine API")
