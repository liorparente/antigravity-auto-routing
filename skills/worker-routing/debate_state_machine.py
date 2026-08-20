"""Pure, immutable state transitions for Planner/Critic debates.

This module deliberately has no transport or persistence dependencies.  Its
only sibling dependency is the dialogue contract that owns shared wire types.
"""
from __future__ import annotations

import importlib.util
import math
import sys
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, overload
from types import MappingProxyType

if TYPE_CHECKING:
    from dialogue_contracts import (
        AdvisoryResolutionOption,
        AdvisoryStalemateReport,
        Occasion,
    )


def _load_sibling(name: str) -> Any:
    try:
        return __import__(name)
    except ModuleNotFoundError as exc:
        if exc.name != name:
            raise
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(f"{name}.py"))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_dialogue_contracts = _load_sibling("dialogue_contracts")

if not TYPE_CHECKING:
    Occasion = _dialogue_contracts.Occasion
    AdvisoryResolutionOption = _dialogue_contracts.AdvisoryResolutionOption
    AdvisoryStalemateReport = _dialogue_contracts.AdvisoryStalemateReport

PANEL_TOPOLOGY_OCCASIONS: tuple[Occasion, ...] = ("plan-review", "code-review")

DEFAULT_VOTE_CONFIDENCE: dict[str, float] = {
    "approve": 1.0,
    "revise": -0.3,
    "block": -1.0,
    "abstain": 0.0,
}
NEGATIVE_LOSS_MULTIPLIER = 1.5
VALID_CONSENSUS_OUTCOMES = frozenset({
    "UNANIMOUS", "QUALIFIED", "MATERIAL_DISAGREEMENT", "INCOMPLETE", "UNRESOLVED",
})


def is_panel_topology(occasion: Occasion, complexity: str) -> bool:
    """Return whether an occasion uses two independent Critics."""
    return occasion in PANEL_TOPOLOGY_OCCASIONS and complexity.lower().strip() == "complex"


def build_stalemate_report(
    planner_position: str,
    critic_position: str,
    critic_b_position: str | None = None,
) -> AdvisoryStalemateReport:
    """Build the stable pair- or panel-topology human-resolution report."""
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
    if normalized in ("approve", "approved"):
        return "APPROVE"
    if normalized == "revise":
        return "REVISE"
    if normalized == "block":
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
    findings: tuple[dict[str, Any], ...] = ()

    def __post_init__(self) -> None:
        """Recursively detach the immutable response from caller-owned findings."""
        object.__setattr__(self, "findings", tuple(_deep_freeze(finding) for finding in self.findings))


class SecurityVeto(Exception):
    """A unilateral security finding that halts a consultation."""

    def __init__(self, provider: str, finding: dict[str, Any]) -> None:
        self.provider = provider
        self.finding = finding
        claim = finding.get("claim", finding.get("id", "unspecified"))
        super().__init__(f"Security veto by {provider}: {claim}")


class SecurityVetoHandler:
    """Detect configured high-confidence security findings in critic votes."""

    def __init__(
        self,
        veto_severities: list[str] | tuple[str, ...] | set[str] | None = None,
        security_threshold: float = 0.80,
        enabled: bool = True,
    ) -> None:
        severities = veto_severities if veto_severities is not None else {"critical", "high"}
        self.veto_severities = {
            str(severity).strip().casefold() for severity in severities
        }
        self.security_threshold = security_threshold
        self.enabled = enabled

    @staticmethod
    def _field(
        vote: dict[str, Any] | CriticResponse, name: str, default: Any = None
    ) -> Any:
        return vote.get(name, default) if isinstance(vote, dict) else getattr(vote, name, default)

    @staticmethod
    def _confidence(finding: dict[str, Any]) -> float:
        raw_confidence = finding.get("confidence", 1.0)
        if isinstance(raw_confidence, bool):
            return 1.0
        try:
            confidence = float(raw_confidence)
        except (TypeError, ValueError):
            return 1.0
        if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
            return 1.0
        return confidence

    def check(
        self, votes: Sequence[dict[str, Any] | CriticResponse]
    ) -> SecurityVeto | None:
        """Return the first configured veto, treating malformed confidence as certain."""
        if not self.enabled:
            return None
        for vote in votes:
            findings = self._field(vote, "findings", ())
            if not isinstance(findings, (list, tuple)):
                continue
            for finding in findings:
                if not isinstance(finding, dict) and not isinstance(finding, MappingProxyType):
                    continue
                severity = str(finding.get("severity", "")).strip().casefold()
                if severity not in self.veto_severities:
                    continue
                if self._confidence(finding) >= self.security_threshold:
                    provider = str(
                        self._field(
                            vote,
                            "provider",
                            self._field(vote, "critic_id", "unknown"),
                        )
                        or "unknown"
                    )
                    return SecurityVeto(provider, dict(finding))
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

    @staticmethod
    def _field(vote: dict[str, Any] | CriticResponse, name: str, default: Any = None) -> Any:
        return vote.get(name, default) if isinstance(vote, dict) else getattr(vote, name, default)

    def _confidence(self, vote: dict[str, Any] | CriticResponse) -> float:
        verdict = self._field(vote, "vote", self._field(vote, "verdict", ""))
        if _normalize_verdict(verdict) is None:
            return 0.0
        confidence = self._field(vote, "confidence")
        if confidence is None:
            confidence = DEFAULT_VOTE_CONFIDENCE.get(str(verdict).strip().casefold(), 0.0)
        try:
            value = float(confidence)
        except (TypeError, ValueError):
            value = 0.0
        if not math.isfinite(value):
            return 0.0
        return max(-1.0, min(1.0, value))

    def _identity(self, vote: dict[str, Any] | CriticResponse) -> str:
        return str(self._field(vote, "provider", self._field(vote, "critic_id", "")) or "")

    def _invalid_voters(self, votes: Sequence[dict[str, Any] | CriticResponse]) -> str:
        invalid = []
        for vote in votes:
            verdict = self._field(vote, "vote", self._field(vote, "verdict", ""))
            if _normalize_verdict(verdict) is None:
                invalid.append(f"{self._identity(vote)}={verdict}")
        return ", ".join(invalid)

    def _enforce_policy(self, outcome: str) -> str:
        if not self._policy_is_valid:
            return "INCOMPLETE"
        if self.policy and outcome not in self.policy:
            return "INCOMPLETE"
        return outcome

    def weighted_score(self, votes: Sequence[dict[str, Any] | CriticResponse]) -> float:
        if not votes:
            return 0.0
        raw_weights = [self.weights.get(self._identity(vote), 0.0) for vote in votes]
        total_weight = sum(raw_weights)
        effective_weights = raw_weights if total_weight > 0 else [1.0] * len(votes)
        total_weight = total_weight if total_weight > 0 else float(len(votes))
        score = 0.0
        for vote, weight in zip(votes, effective_weights):
            confidence = self._confidence(vote)
            if confidence < 0:
                confidence *= NEGATIVE_LOSS_MULTIPLIER
            score += weight * confidence
        return score / total_weight

    def evaluate(
        self,
        votes: Sequence[dict[str, Any] | CriticResponse],
        expected_hash: str | None = None,
        require_candidate_hashes: bool = False,
    ) -> str:
        if not votes or any(not self._identity(vote) for vote in votes) or self._invalid_voters(votes):
            return self._enforce_policy("INCOMPLETE")
        hashes = [self._field(vote, "candidate_hash") for vote in votes]
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
        vote_names = [str(self._field(vote, "vote", self._field(vote, "verdict", ""))).strip().casefold() for vote in votes]
        return self._enforce_policy("UNANIMOUS" if all(vote in {"approve", "approved"} for vote in vote_names) else "QUALIFIED")


def evaluate_weighted_quorum(
    responses: Sequence[CriticResponse],
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
    invalid_voters = table._invalid_voters(responses)
    if invalid_voters:
        return False, "INCOMPLETE", 0.0, f"unparseable verdict: {invalid_voters}"
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
    if state.consensus_reached or state.error is not None or state.stalemate_report is not None or len(state.rounds) >= state.max_rounds:
        return state
    consensus, error = evaluate_round_verdicts(
        record.critic_a_verdict, record.critic_b_verdict, is_panel=state.is_panel
    )
    normalized_record = replace(record, is_consensus=consensus, error=error)
    rounds = (*state.rounds, normalized_record)
    if consensus:
        return replace(state, rounds=rounds, consensus_reached=True, final_plan=record.planner_plan, stalemate_report=None, error=error)
    if error:
        return replace(state, rounds=rounds, consensus_reached=False, final_plan=None, stalemate_report=None, error=error)
    if len(rounds) >= state.max_rounds:
        return replace(state, rounds=rounds, consensus_reached=False, final_plan=None, stalemate_report=build_stalemate_report(record.planner_plan, record.critic_a_response, record.critic_b_response if state.is_panel else None), error=None)
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
        return replace(state, round_number=turn.round_index, planner_proposals=proposals, critic_responses=responses, status="error", error=error)
    if consensus:
        return replace(state, round_number=turn.round_index, planner_proposals=proposals, critic_responses=responses, status="consensus", final_plan=recorded_turn.planner_proposal, stalemate_report=None, error=None)
    if len(proposals) >= state.max_rounds:
        critic_a = turn.critic_responses[0].response if turn.critic_responses else ""
        critic_b = turn.critic_responses[1].response if len(turn.critic_responses) > 1 else None
        return replace(state, round_number=turn.round_index, planner_proposals=proposals, critic_responses=responses, status="stalemate", final_plan=None, stalemate_report=build_stalemate_report(turn.planner_proposal, critic_a, critic_b), error=None)
    return replace(state, round_number=turn.round_index, planner_proposals=proposals, critic_responses=responses)


@overload
def advance_debate_state(state: DebateSessionState, record: DebateRoundRecord, quorum_policy: str = "unanimous", *, weights: dict[str, float] | None = None, quorum_threshold: float = 0.60, require_candidate_hashes: bool = False, expected_hash: str | None = None) -> DebateSessionState: ...


@overload
def advance_debate_state(state: DebateState, record: RoundTurnResult, quorum_policy: str = "unanimous", *, weights: dict[str, float] | None = None, quorum_threshold: float = 0.60, require_candidate_hashes: bool = False, expected_hash: str | None = None) -> DebateState: ...


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
