"""Pure, immutable state transitions for Planner/Critic debates.

This module deliberately has no transport or persistence dependencies.  Its
only sibling dependency is the dialogue contract that owns shared wire types.
"""
from __future__ import annotations

import importlib.util
import sys
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, overload

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
    if normalized == "abstain":
        return "ABSTAIN"
    return None


@dataclass(frozen=True)
class CriticResponse:
    critic_id: str
    response: str
    verdict: str | None = None
    confidence: float = 1.0


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


def _advance_general_state(state: DebateState, turn: RoundTurnResult, quorum_policy: str) -> DebateState:
    if state.status != "in_progress":
        return state
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
def advance_debate_state(state: DebateSessionState, record: DebateRoundRecord, quorum_policy: str = "unanimous") -> DebateSessionState: ...


@overload
def advance_debate_state(state: DebateState, record: RoundTurnResult, quorum_policy: str = "unanimous") -> DebateState: ...


def advance_debate_state(
    state: DebateSessionState | DebateState,
    record: DebateRoundRecord | RoundTurnResult,
    quorum_policy: str = "unanimous",
) -> DebateSessionState | DebateState:
    """Advance either supported immutable debate state without side effects."""
    if isinstance(state, DebateSessionState) and isinstance(record, DebateRoundRecord):
        return _advance_session_state(state, record)
    if isinstance(state, DebateState) and isinstance(record, RoundTurnResult):
        return _advance_general_state(state, record, quorum_policy)
    raise TypeError("state and record must use the same debate state-machine API")
