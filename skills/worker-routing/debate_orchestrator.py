"""Pure state-machine primitives for AdvisoryConsultation debates."""
from __future__ import annotations

from dataclasses import dataclass, field

from dialogue_contracts import AdvisoryResolutionOption, AdvisoryStalemateReport, Occasion


PANEL_TOPOLOGY_OCCASIONS: tuple[Occasion, ...] = ("plan-review", "code-review")
_PANEL_TOPOLOGY_OCCASIONS = PANEL_TOPOLOGY_OCCASIONS


def is_panel_topology(occasion: Occasion, complexity: str) -> bool:
    """Return whether this occasion and complexity use two Critics."""
    normalized = complexity.lower().strip()
    return occasion in PANEL_TOPOLOGY_OCCASIONS and normalized == "complex"


_is_panel_topology = is_panel_topology


def build_stalemate_report(
    planner_position: str,
    critic_position: str,
    critic_b_position: str | None = None,
) -> AdvisoryStalemateReport:
    """Build pair- or panel-topology human-resolution options."""
    if critic_b_position is None:
        return AdvisoryStalemateReport(
            planner_position=planner_position,
            critic_position=critic_position,
            options=(
                AdvisoryResolutionOption(1, "Approve Planner Architecture", planner_position),
                AdvisoryResolutionOption(2, "Approve Critic Architecture", critic_position),
                AdvisoryResolutionOption(
                    3, "Escalate to Human Decision", "Halt execution and request user review"
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
                3, "Escalate to Human Decision", "Halt execution and request user review"
            ),
        ),
    )


_build_stalemate_report = build_stalemate_report


def evaluate_round_verdicts(
    critic_a_verdict: str | None,
    critic_b_verdict: str | None = None,
    *,
    is_panel: bool = False,
) -> tuple[bool, str | None]:
    """Return consensus and any fail-closed malformed-verdict error."""
    if not is_panel:
        return (
            critic_a_verdict == "APPROVE",
            None
            if critic_a_verdict in ("APPROVE", "REVISE")
            else f"unparseable verdict: {critic_a_verdict}",
        )
    return (
        critic_a_verdict == "APPROVE" and critic_b_verdict == "APPROVE",
        None
        if (
            critic_a_verdict in ("APPROVE", "REVISE")
            and critic_b_verdict in ("APPROVE", "REVISE")
        )
        else f"unparseable verdict: critic_a={critic_a_verdict}, critic_b={critic_b_verdict}",
    )


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


@dataclass
class DebateSessionState:
    occasion: Occasion
    complexity: str
    max_rounds: int
    is_panel: bool
    rounds: list[DebateRoundRecord] = field(default_factory=list)
    consensus_reached: bool = False
    final_plan: str | None = None
    stalemate_report: AdvisoryStalemateReport | None = None
    error: str | None = None
