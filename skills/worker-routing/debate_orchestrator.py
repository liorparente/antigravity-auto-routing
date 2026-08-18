"""Pure state-machine primitives for AdvisoryConsultation debates."""
from __future__ import annotations

from dataclasses import dataclass, field
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any


def _load_sibling(name: str) -> Any:
    """Load a sibling module when this module was imported directly by path."""
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
Occasion = _dialogue_contracts.Occasion
AdvisoryResolutionOption = _dialogue_contracts.AdvisoryResolutionOption
AdvisoryStalemateReport = _dialogue_contracts.AdvisoryStalemateReport
VerdictContractResult = _dialogue_contracts.VerdictContractResult


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
    def normalize(verdict: str | None) -> str | None:
        if not isinstance(verdict, str):
            return None
        normalized = verdict.strip().casefold()
        if normalized in ("approve", "approved"):
            return "APPROVE"
        if normalized == "revise":
            return "REVISE"
        return None

    normalized_a = normalize(critic_a_verdict)
    normalized_b = normalize(critic_b_verdict)
    if not is_panel:
        return (
            normalized_a == "APPROVE",
            None
            if normalized_a is not None
            else f"unparseable verdict: {critic_a_verdict}",
        )
    return (
        normalized_a == "APPROVE" and normalized_b == "APPROVE",
        None
        if (
            normalized_a is not None
            and normalized_b is not None
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


# Phase-2 compatibility bridge -------------------------------------------------
#
# The historical implementation still owns the long-running production loop.
# Keep this module as the canonical import surface for orchestration helpers,
# and resolve the legacy implementation only when an execution API is called.
# This avoids an import cycle while direct-path imports remain supported.


@dataclass(frozen=True)
class CodeReviewRiskConfig:
    """Configuration inputs used by the code-review consultation trigger."""

    diff_line_threshold: int
    security_sensitive_path_patterns: tuple[str, ...]


@dataclass(frozen=True)
class PostMortemTriggerConfig:
    """Configuration inputs used by the post-mortem consultation trigger."""

    failure_threshold: int
    trigger_patterns: tuple[str, ...]


DEFAULT_POST_MORTEM_TRIGGER_FAILURE_THRESHOLD = 2
DEFAULT_POST_MORTEM_TRIGGER_PATTERNS: tuple[str, ...] = ("failure", "escalation", "stalemate")


def _load_post_mortem_trigger_config(
    config_path: Path,
) -> PostMortemTriggerConfig:
    """Load optional post-mortem trigger policy with conservative defaults."""
    with open(config_path, "r", encoding="utf-8") as stream:
        section = json.load(stream).get("critical_dialogue", {})
    return PostMortemTriggerConfig(
        int(section.get(
            "post_mortem_trigger_failure_threshold",
            DEFAULT_POST_MORTEM_TRIGGER_FAILURE_THRESHOLD,
        )),
        tuple(section.get(
            "post_mortem_trigger_patterns", DEFAULT_POST_MORTEM_TRIGGER_PATTERNS
        )),
    )


def _legacy_orchestrator() -> Any:
    """Load the compatibility implementation only after this module is ready."""
    return _load_sibling("advisory_consultation")


def run_debate_loop(*args: Any, **kwargs: Any) -> Any:
    """Execute the legacy-compatible Planner/Critic debate state machine."""
    return _legacy_orchestrator().run_advisory_consultation_debate(*args, **kwargs)


def run_canary_dialogue(*args: Any, **kwargs: Any) -> Any:
    """Execute exactly one seeded-flaw Critic probe through the debate loop."""
    kwargs["is_canary"] = True
    return run_debate_loop(*args, **kwargs)


def run_post_mortem_loop(*args: Any, **kwargs: Any) -> Any:
    """Execute a post-mortem dialogue using the ordinary state machine."""
    kwargs.setdefault("occasion", "post-mortem")
    return run_debate_loop(*args, **kwargs)


_LEGACY_EXPORTS = frozenset({
    "classify_model_family", "is_local_family", "resolve_roster",
    "RosterAssignment", "RosterResolution", "RosterResolutionError",
    "DEFAULT_ROSTER_FALLBACK_CHAINS", "_load_roster_fallback_chains",
    "needs_code_review_consultation", "needs_post_mortem_consultation",
    "_load_code_review_risk_config",
    "DEFAULT_CODE_REVIEW_DIFF_LINE_THRESHOLD", "DEFAULT_SECURITY_SENSITIVE_PATH_PATTERNS",
    "CanaryFixture", "CanaryResult", "CANARY_FIXTURES", "is_canary_dialogue",
})


def __getattr__(name: str) -> Any:
    """Preserve direct imports while the production loop is being extracted."""
    if name in _LEGACY_EXPORTS:
        return getattr(_legacy_orchestrator(), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
