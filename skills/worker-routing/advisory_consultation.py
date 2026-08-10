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
from typing import Literal

MAX_DEBATE_ROUNDS = 3

WORKER_MODE_TOKEN = "[WORKER-MODE: AGY-NESTED-EXEC]"

CRITIC_VERDICT_APPROVE = "VERDICT: APPROVE"
CRITIC_VERDICT_REVISE = "VERDICT: REVISE"

InvokeWorker = Callable[[str, str, str], str]

# Discriminates how a consultation ended. `consensus_reached` on the result
# stays consistent with this: True only when outcome == "consensus". The
# other three are all "no consensus", distinguished for the caller because
# a stalemate, a malformed verdict, and an unreachable worker each demand a
# different human response.
AdvisoryOutcome = Literal[
    "consensus", "stalemate", "unparseable_verdict", "worker_error"
]

# The Critic's verdict line, once read, is one of these three states.
# "unparseable" is deliberately not folded into "revise": a malformed
# response must halt the consultation, not be fed back to the Planner as if
# it were a reasoned objection.
CriticVerdict = Literal["approved", "revise", "unparseable"]


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
    rounds_run: int
    final_plan: str
    outcome: AdvisoryOutcome
    planner_model: str = "Claude Opus 5 (Thinking)"
    critic_model: str = "Codex 5.6 Sol"
    rounds: tuple[AdvisoryDebateRound, ...] = ()
    stalemate: AdvisoryStalemateReport | None = None
    error: str | None = None

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


def _parse_critic_verdict(critic_response: str) -> CriticVerdict:
    """Parse only the first non-empty line; anything else is unparseable.

    Absence of rejection is not agreement: only an exact "VERDICT: APPROVE"
    counts as approval, only an exact "VERDICT: REVISE" counts as a
    parseable rejection that keeps the loop going, and everything else
    (empty, prose-only, near-miss) fails closed as "unparseable" rather than
    being silently treated as either.
    """
    for line in critic_response.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        upper = stripped.upper()
        if upper == CRITIC_VERDICT_APPROVE:
            return "approved"
        if upper == CRITIC_VERDICT_REVISE:
            return "revise"
        return "unparseable"
    return "unparseable"


def _remove_stale_plan_artifact(plan_path: Path) -> str | None:
    """Ensure no plan artifact survives a non-consensus exit.

    Only this one path is touched — the module owns nothing else under
    `root_dir`. Cleanup failure (e.g. `plan_path` is a directory, or its
    parent is unwritable) must not raise out of the consultation and must
    not replace whatever error actually caused the non-consensus exit — it
    is reported back to the caller instead, who folds it into the result.
    """
    try:
        plan_path.unlink(missing_ok=True)
    except OSError as exc:
        return f"failed to remove stale plan artifact at {plan_path}: {exc}"
    return None


def _combine_errors(primary: str, cleanup_error: str | None) -> str:
    """Preserve the original failure while still surfacing a cleanup problem."""
    if cleanup_error is None:
        return primary
    return f"{primary}; {cleanup_error}"


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
    exchange repeats up to ``max_rounds`` times.

    Three ways this can end without consensus, and each fails closed the
    same way — no plan artifact, no winner picked, the failure visible on
    the result:

    - Stalemate: every round runs and none is approved. The result carries
      both final positions and three resolution options.
    - Unparseable verdict: a Critic response has no readable verdict line.
      This ends the consultation immediately rather than being silently fed
      back to the Planner as if it were a reasoned rejection.
    - Worker error: ``invoke_worker`` raises. The exception is caught (never
      ``BaseException``, so Ctrl-C still propagates) and its message is
      carried on the result.

    A pre-existing ``implementation_plan.md`` under ``root_dir`` from an
    earlier run is removed on every one of these three exits, so the
    artifact on disk is never staler than the result describing it.

    Raises ``ValueError`` if ``max_rounds`` is not at least 1: that is a
    programming error at the call site, not a genuine Planner-Critic
    disagreement, and must not be reported back as a fabricated stalemate.
    """
    if max_rounds < 1:
        raise ValueError(f"max_rounds must be >= 1, got {max_rounds}")

    rounds: list[AdvisoryDebateRound] = []
    previous_plan: str | None = None
    previous_critique: str | None = None
    plan_path = root_dir / "implementation_plan.md"

    def _result(
        outcome: AdvisoryOutcome,
        *,
        final_plan: str = "",
        stalemate: AdvisoryStalemateReport | None = None,
        error: str | None = None,
    ) -> AdvisoryDebateResult:
        return AdvisoryDebateResult(
            rounds_run=len(rounds),
            final_plan=final_plan,
            outcome=outcome,
            planner_model=planner_model,
            critic_model=critic_model,
            rounds=tuple(rounds),
            stalemate=stalemate,
            error=error,
        )

    for _round_number in range(1, max_rounds + 1):
        planner_prompt = _build_planner_prompt(
            task_description,
            previous_plan=previous_plan,
            critic_feedback=previous_critique,
        )
        try:
            planner_plan = invoke_worker(planner_model, planner_effort, planner_prompt)
        except Exception as exc:  # noqa: BLE001 - a worker failure must fail closed.
            cleanup_error = _remove_stale_plan_artifact(plan_path)
            return _result("worker_error", error=_combine_errors(str(exc), cleanup_error))

        critic_prompt = _build_critic_prompt(task_description, planner_plan)
        try:
            critic_response = invoke_worker(critic_model, critic_effort, critic_prompt)
        except Exception as exc:  # noqa: BLE001 - a worker failure must fail closed.
            cleanup_error = _remove_stale_plan_artifact(plan_path)
            return _result("worker_error", error=_combine_errors(str(exc), cleanup_error))

        rounds.append(AdvisoryDebateRound(planner_plan, critic_response))
        verdict = _parse_critic_verdict(critic_response)

        if verdict == "approved":
            _atomic_text_write(plan_path, planner_plan)
            return _result("consensus", final_plan=planner_plan)

        if verdict == "unparseable":
            cleanup_error = _remove_stale_plan_artifact(plan_path)
            return _result("unparseable_verdict", error=cleanup_error)

        previous_plan = planner_plan
        previous_critique = critic_response

    cleanup_error = _remove_stale_plan_artifact(plan_path)
    stalemate = _build_stalemate_report(previous_plan or "", previous_critique or "")
    return _result("stalemate", stalemate=stalemate, error=cleanup_error)
