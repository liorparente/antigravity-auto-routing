"""Pure prompt construction for the CriticalDialogue engine.

This module deliberately has no filesystem, subprocess, or network imports.
It owns only the stable text contract presented to Planner and Critic workers.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Occasion = Literal["ambiguity", "plan-review", "code-review", "post-mortem"]

WORKER_MODE_TOKEN = "[WORKER-MODE: AGY-NESTED-EXEC]"
CRITIC_VERDICT_APPROVE = "VERDICT: APPROVE"
CRITIC_VERDICT_REVISE = "VERDICT: REVISE"


@dataclass(frozen=True)
class MissionCopy:
    """Occasion-specific role framing used by Planner and Critic prompts."""

    planner_intro: str
    planner_revision_intro: str
    artifact_label: str
    critic_intro: str


# Compatibility name retained for callers that previously reached into the
# advisory facade's private prompt vocabulary.
_MissionCopy = MissionCopy

MISSION_COPY: dict[Occasion, MissionCopy] = {
    "ambiguity": MissionCopy(
        "You are the Planner in an AdvisoryConsultation. Propose a concise, concrete implementation plan for the task below.",
        "You are the Planner in an AdvisoryConsultation. The Critic did not approve your previous plan. Revise your plan to address the Critic's objection below.",
        "plan",
        "You are the Critic in an AdvisoryConsultation. Judge the Planner's plan below on its merits.",
    ),
    "plan-review": MissionCopy(
        "You are the Planner in a CriticalDialogue plan review. Propose a concise, concrete implementation plan for the task below.",
        "You are the Planner in a CriticalDialogue plan review. The Critic did not approve your previous plan. Revise your plan to address the Critic's objection below.",
        "plan",
        "You are the Critic in a CriticalDialogue plan review. Judge the Planner's plan below on its merits.",
    ),
    "code-review": MissionCopy(
        "You are the Planner in a CriticalDialogue code review, defending the diff under review. Propose a concise, concrete rationale for the diff below.",
        "You are the Planner in a CriticalDialogue code review. The Critic did not approve your previous defense of the diff. Revise it to address the Critic's objection below.",
        "diff defense",
        "You are the Critic in a CriticalDialogue code review. Judge the diff below on its merits.",
    ),
    "post-mortem": MissionCopy(
        "You are the Planner in a CriticalDialogue post-mortem. Propose a concise, concrete lesson to record for the failure below.",
        "You are the Planner in a CriticalDialogue post-mortem. The Critic did not approve your previous lesson. Revise it to address the Critic's objection below.",
        "lesson",
        "You are the Critic in a CriticalDialogue post-mortem. Judge the lesson below on its merits.",
    ),
}
_MISSION_COPY = MISSION_COPY


def build_planner_prompt(task_description: str, *, occasion: Occasion = "ambiguity", previous_plan: str | None = None, critic_feedback: str | None = None) -> str:
    """Build a Planner's initial or revision prompt without interpreting input."""
    mission = MISSION_COPY[occasion]
    if previous_plan is None or critic_feedback is None:
        return f"{WORKER_MODE_TOKEN}\n{mission.planner_intro}\n\nTask: {task_description}"
    return (
        f"{WORKER_MODE_TOKEN}\n{mission.planner_revision_intro}\n\n"
        f"Task: {task_description}\n\nYour previous {mission.artifact_label}:\n"
        f"{previous_plan}\n\nCritic's response:\n{critic_feedback}"
    )


def build_critic_prompt(task_description: str, planner_plan: str, *, occasion: Occasion = "ambiguity", approve_verdict: str = CRITIC_VERDICT_APPROVE, revise_verdict: str = CRITIC_VERDICT_REVISE) -> str:
    """Build the strict VerdictContract prompt used for a Critic review."""
    mission = MISSION_COPY[occasion]
    return (
        f"{WORKER_MODE_TOKEN}\n{mission.critic_intro}\n\n"
        "Write your rationale first. Before you verdict, show your engagement with it: quote the exact passages you are judging, one per line, as QUOTE: \"<verbatim text copied from what you were given>\", and list any concrete objections as a numbered list, one per line, like \"1. <objection>\". End your response with exactly one verdict line, LAST: either "
        f"\"{approve_verdict}\" if it is sound as written, or \"{revise_verdict}\" if it is not. An APPROVE backed by zero verified quotes will be treated as invalid, even if it lists objections.\n\n"
        f"Task: {task_description}\n\nPlanner's plan:\n{planner_plan}"
    )


def build_adjudicator_prompt(task_description: str, planner_position: str, critic_position: str) -> str:
    """Build a neutral human-escalation prompt for irreconcilable positions."""
    return (
        f"{WORKER_MODE_TOKEN}\nYou are the Adjudicator in an AdvisoryConsultation. "
        "Compare the Planner and Critic positions, identify the decisive trade-off, and recommend a safe next action.\n\n"
        f"Task: {task_description}\n\nPlanner position:\n{planner_position}\n\nCritic position:\n{critic_position}"
    )


def build_stalemate_prompt(task_description: str, planner_position: str, critic_position: str) -> str:
    """Build the legacy-compatible escalation prompt for a stalemate."""
    return build_adjudicator_prompt(task_description, planner_position, critic_position)


_build_planner_prompt = build_planner_prompt
_build_critic_prompt = build_critic_prompt
_build_adjudicator_prompt = build_adjudicator_prompt
_build_stalemate_prompt = build_stalemate_prompt
