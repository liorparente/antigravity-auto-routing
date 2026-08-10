#!/usr/bin/env python3
"""AdvisoryConsultation: the Planner-Critic advisory debate loop.

This is a distinct capability from :mod:`agent_council`'s deterministic
three-tier round plan. `AgentCouncil` has no model or network dependency and
its output is cached and HMAC-signed; a real, model-based Planner-Critic loop
must never be dropped into that module, or it would silently destroy the
determinism its cache and signature depend on. This module is where that
loop belongs instead.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

MAX_DEBATE_ROUNDS = 3


@dataclass
class AdvisoryDebateResult:
    rounds_run: int
    consensus_reached: bool
    final_plan: str
    planner_model: str = "Claude Opus 5 (Thinking)"
    critic_model: str = "Codex 5.6 Sol"


def needs_advisory_consultation(complexity: str, confidence: float = 1.0) -> bool:
    """Determine whether task requires an advisory Planner-Critic debate loop."""
    normalized = complexity.lower().strip()
    if normalized == "ambiguous":
        return True
    return confidence < 0.7


def run_advisory_consultation_debate(
    task_description: str,
    max_rounds: int = MAX_DEBATE_ROUNDS,
) -> AdvisoryDebateResult:
    """Execute up to max_rounds of Planner-Critic debate loop for complex/ambiguous tasks.

    Not implemented. The Planner-Critic advisory consultation loop (ADR 0005
    Pillar 3 / protocol.md Rule 6) requires actually invoking Planner and
    Critic models; no such loop exists yet. A stub that reported fake
    consensus was worse than no feature at all, so this fails loudly instead.
    """
    raise NotImplementedError(
        "run_advisory_consultation_debate is not implemented: no Planner or "
        "Critic model was consulted for "
        f"{task_description!r}. Callers must not treat this as a reached "
        "consensus — the real Planner-Critic debate loop is separately "
        "scheduled work."
    )


def generate_debate_stalemate_report(
    planner_plan: str,
    critic_plan: str,
    rounds_run: int = MAX_DEBATE_ROUNDS,
) -> dict[str, Any]:
    """Generate a structured visual comparison matrix and options when Planner-Critic debate stalemates."""
    return {
        "title": f"STALEMATE: Planner-Critic Debate Unresolved after {rounds_run} Rounds",
        "rounds": rounds_run,
        "planner_summary": planner_plan,
        "critic_summary": critic_plan,
        "options": [
            {"id": 1, "label": "Approve Planner Architecture", "description": planner_plan},
            {"id": 2, "label": "Approve Critic Architecture", "description": critic_plan},
            {"id": 3, "label": "Escalate to Human Decision", "description": "Halt execution and request user review"},
        ],
    }
