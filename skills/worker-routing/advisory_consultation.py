#!/usr/bin/env python3
"""Backward-compatible, ultra-slim facade for CriticalDialogue."""
from __future__ import annotations

import importlib.util
import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from debate_orchestrator import InvokeWorker, IsFamilyReachable


def _load_sibling(name: str) -> Any:
    if __package__:
        pkg_mod = sys.modules.get(f"{__package__}.{name}")
        if pkg_mod is not None:
            return pkg_mod
        return __import__(f"{__package__}.{name}", fromlist=[name])
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


# Keep the historic flat API while the implementation lives in focused modules.
_debate_orchestrator = _load_sibling("debate_orchestrator")
_prompt_assembler = _load_sibling("prompt_assembler")
_sensitivity_redactor = _load_sibling("sensitivity_redactor")
_executive_dialogue_report = _load_sibling("executive_dialogue_report")
_dialogue_degradation = _load_sibling("dialogue_degradation")
_dialogue_contracts = _load_sibling("dialogue_contracts")
_dialogue_transcript = _load_sibling("dialogue_transcript")
_debate_state_machine = _load_sibling("debate_state_machine")
_debate_transport = _load_sibling("debate_transport")

DebateTransport = _debate_transport.DebateTransport
RecurringFailureNotifier = _debate_transport.RecurringFailureNotifier

_modules = (
    _debate_orchestrator, _prompt_assembler, _sensitivity_redactor,
    _executive_dialogue_report, _dialogue_degradation, _dialogue_contracts,
    _dialogue_transcript, _debate_state_machine, _debate_transport,
)

_CONFIG_PATH = Path(__file__).with_name("routing-config.json")
MAX_DEBATE_ROUNDS = _debate_orchestrator.MAX_DEBATE_ROUNDS

# Bind the execution entry points explicitly for static callers and type checkers.
run_advisory_consultation_debate = _debate_orchestrator.run_advisory_consultation_debate
run_critical_dialogue = _debate_orchestrator.run_critical_dialogue
run_debate_loop = _debate_orchestrator.run_debate_loop
run_canary_dialogue = _debate_orchestrator.run_canary_dialogue
run_post_mortem_loop = _debate_orchestrator.run_post_mortem_loop


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
    reachability_check: IsFamilyReachable | None = None,
    roster_config_path: Path = _CONFIG_PATH,
    session_spend_so_far: int = 0,
    budget_config_path: Path = _CONFIG_PATH,
) -> threading.Thread:
    """Compatibility wrapper preserving patches to the historic facade API."""
    _debate_orchestrator.run_advisory_consultation_debate = run_advisory_consultation_debate
    return _debate_orchestrator.dispatch_post_mortem_consultation(
        task_description,
        invoke_worker,
        root_dir=root_dir,
        max_rounds=max_rounds,
        planner_model=planner_model,
        critic_model=critic_model,
        planner_effort=planner_effort,
        critic_effort=critic_effort,
        task_id=task_id,
        reachability_check=reachability_check,
        roster_config_path=roster_config_path,
        session_spend_so_far=session_spend_so_far,
        budget_config_path=budget_config_path,
    )

def __getattr__(name: str) -> Any:
    for module in _modules:
        try:
            return getattr(module, name)
        except AttributeError:
            pass
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

def __dir__() -> list[str]:
    return sorted(set(globals()) | {key for module in _modules for key in vars(module)})


# A deliberately explicit star-export surface.  The facade resolves these
# names lazily through `__getattr__`, preserving direct imports while making
# `from advisory_consultation import *` expose the complete supported API.
__all__ = (  # noqa: F822
    "BUDGET_DEGRADATION_MARKER",
    "CANARY_FIXTURES",
    "CANARY_MARKER",
    "CRITIC_VERDICT_APPROVE",
    "CRITIC_VERDICT_REVISE",
    "DEFAULT_CANARY_DIALOGUES_PER_CANARY",
    "DEFAULT_CANARY_SECONDS_BETWEEN_CANARIES",
    "DEFAULT_CODE_REVIEW_DIFF_LINE_THRESHOLD",
    "DEFAULT_ROSTER_FALLBACK_CHAINS",
    "DEFAULT_SECURITY_SENSITIVE_PATH_PATTERNS",
    "DEFAULT_SESSION_DIALOGUE_CAP",
    "DEGRADED_INDEPENDENCE_MARKER",
    "ESCALATION_FAILURE_THRESHOLD",
    "MAX_DEBATE_ROUNDS",
    "MISSION_COPY",
    "PANEL_TOPOLOGY_OCCASIONS",
    "SENSITIVITY_MARKERS",
    "WORKER_MODE_TOKEN",
    "AdvisoryDebateResult",
    "AdvisoryDebateRound",
    "AdvisoryOutcome",
    "AdvisoryResolutionOption",
    "AdvisoryRoundVerdict",
    "AdvisoryStalemateReport",
    "AdvisoryTelemetryRecord",
    "CanaryFixture",
    "CanaryResult",
    "ConsultationTranscript",
    "CriticResponse",
    "CriticVerdict",
    "DebateRoundRecord",
    "DebateSessionState",
    "DebateState",
    "DebateTransport",
    "DegradationLadderState",
    "DegradationRung",
    "ExecutiveDialogueReport",
    "InvokeWorker",
    "IsFamilyReachable",
    "MissionCopy",
    "Occasion",
    "RecurringFailureNotifier",
    "RosterAssignment",
    "RosterResolution",
    "RosterResolutionError",
    "RosterRole",
    "RosterTopology",
    "RoundTurnResult",
    "TaskIdentity",
    "VerdictContractResult",
    "advance_debate_state",
    "build_adjudicator_prompt",
    "build_canary_prompt",
    "build_critic_prompt",
    "build_planner_prompt",
    "build_stalemate_prompt",
    "build_stalemate_report",
    "classify_model_family",
    "combine_panel_critic_feedback",
    "derive_safe_task_identity",
    "detect_sensitivity_marker",
    "dispatch_post_mortem_consultation",
    "evaluate_quorum",
    "evaluate_round_verdicts",
    "extract_objections",
    "extract_quotes",
    "format_budget_degradation_alert",
    "format_transcript_markdown",
    "is_canary_dialogue",
    "is_local_family",
    "is_panel_topology",
    "needs_advisory_consultation",
    "needs_code_review_consultation",
    "needs_plan_review_consultation",
    "needs_post_mortem_consultation",
    "parse_verdict_contract",
    "render_consultation_transcript",
    "render_executive_summary",
    "render_sensitivity_halt_transcript",
    "resolve_degradation_rung",
    "resolve_roster",
    "run_advisory_consultation_debate",
    "run_canary_dialogue",
    "run_critical_dialogue",
    "run_debate_loop",
    "run_post_mortem_loop",
    "scan_sensitivity_markers",
    "verify_quotes",
)
