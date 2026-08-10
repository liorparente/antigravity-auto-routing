#!/usr/bin/env python3
"""AdvisoryConsultation: the Planner-Critic advisory debate loop.

This is a distinct capability from :mod:`agent_council`'s deterministic
three-tier round plan. `AgentCouncil` has no model or network dependency and
its output is cached and HMAC-signed; a real, model-based Planner-Critic loop
must never be dropped into that module, or it would silently destroy the
determinism its cache and signature depend on. This module is where that
loop belongs instead.

The only path to a worker is the ``invoke_worker`` callable each caller
injects: ``(model, effort, prompt) -> text``. This module never imports
``subprocess``, ``socket``, or any HTTP client, so the whole loop is
exercisable offline with a fake.
"""
from __future__ import annotations

import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAX_DEBATE_ROUNDS = 3

WORKER_MODE_TOKEN = "[WORKER-MODE: AGY-NESTED-EXEC]"

CRITIC_VERDICT_APPROVE = "VERDICT: APPROVE"

InvokeWorker = Callable[[str, str, str], str]


@dataclass(frozen=True)
class AdvisoryDebateRound:
    """One Planner/Critic exchange: the proposal offered and the verdict it drew."""

    planner_proposal: str
    critic_response: str


@dataclass
class AdvisoryDebateResult:
    rounds_run: int
    consensus_reached: bool
    final_plan: str
    planner_model: str = "Claude Opus 5 (Thinking)"
    critic_model: str = "Codex 5.6 Sol"
    rounds: tuple[AdvisoryDebateRound, ...] = ()


def needs_advisory_consultation(complexity: str, confidence: float = 1.0) -> bool:
    """Determine whether task requires an advisory Planner-Critic debate loop."""
    normalized = complexity.lower().strip()
    if normalized == "ambiguous":
        return True
    return confidence < 0.7


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


def _build_planner_prompt(
    task_description: str,
    *,
    previous_plan: str | None = None,
    critic_feedback: str | None = None,
) -> str:
    if previous_plan is None or critic_feedback is None:
        return (
            f"{WORKER_MODE_TOKEN}\n"
            "You are the Planner in an AdvisoryConsultation. Propose a concise, "
            "concrete implementation plan for the task below.\n\n"
            f"Task: {task_description}"
        )
    return (
        f"{WORKER_MODE_TOKEN}\n"
        "You are the Planner in an AdvisoryConsultation. The Critic did not "
        "approve your previous plan. Revise your plan to address the "
        "Critic's objection below.\n\n"
        f"Task: {task_description}\n\n"
        f"Your previous plan:\n{previous_plan}\n\n"
        f"Critic's response:\n{critic_feedback}"
    )


def _build_critic_prompt(task_description: str, planner_plan: str) -> str:
    return (
        f"{WORKER_MODE_TOKEN}\n"
        "You are the Critic in an AdvisoryConsultation. Judge the Planner's "
        "plan below on its merits.\n\n"
        "Open your response with exactly one verdict line, then your "
        f"critique: either \"{CRITIC_VERDICT_APPROVE}\" if the plan is sound "
        "as written, or \"VERDICT: REVISE\" if it is not.\n\n"
        f"Task: {task_description}\n\n"
        f"Planner's plan:\n{planner_plan}"
    )


def _critic_approved(critic_response: str) -> bool:
    """Parse only the first non-empty line; anything else is not approval."""
    for line in critic_response.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped.upper() == CRITIC_VERDICT_APPROVE
    return False


def run_advisory_consultation_debate(
    task_description: str,
    invoke_worker: InvokeWorker,
    *,
    root_dir: Path,
    max_rounds: int = MAX_DEBATE_ROUNDS,
    planner_model: str = "Claude Opus 5 (Thinking)",
    critic_model: str = "Codex 5.6 Sol",
    planner_effort: str = "high",
    critic_effort: str = "high",
) -> AdvisoryDebateResult:
    """Run the Planner/Critic exchange, revising on objection, and report the outcome.

    Round 1: the Planner proposes a plan from the task description alone,
    and the Critic judges it. If the Critic approves, the agreed plan is
    written to ``root_dir / "implementation_plan.md"`` and consensus is
    reported for that round. Otherwise the Planner is asked again, this
    time holding its previous plan and the Critic's objection, and the
    exchange repeats up to ``max_rounds`` times. If no round is approved,
    the honest no-consensus outcome is reported — no plan file, no plan
    text — with ``rounds_run`` reflecting every exchange that actually ran.
    """
    rounds: list[AdvisoryDebateRound] = []
    previous_plan: str | None = None
    previous_critique: str | None = None

    for round_number in range(1, max_rounds + 1):
        planner_prompt = _build_planner_prompt(
            task_description,
            previous_plan=previous_plan,
            critic_feedback=previous_critique,
        )
        planner_plan = invoke_worker(planner_model, planner_effort, planner_prompt)

        critic_prompt = _build_critic_prompt(task_description, planner_plan)
        critic_response = invoke_worker(critic_model, critic_effort, critic_prompt)

        rounds.append(AdvisoryDebateRound(planner_plan, critic_response))

        if _critic_approved(critic_response):
            _atomic_text_write(root_dir / "implementation_plan.md", planner_plan)
            return AdvisoryDebateResult(
                rounds_run=round_number,
                consensus_reached=True,
                final_plan=planner_plan,
                planner_model=planner_model,
                critic_model=critic_model,
                rounds=tuple(rounds),
            )

        previous_plan = planner_plan
        previous_critique = critic_response

    return AdvisoryDebateResult(
        rounds_run=len(rounds),
        consensus_reached=False,
        final_plan="",
        planner_model=planner_model,
        critic_model=critic_model,
        rounds=tuple(rounds),
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
