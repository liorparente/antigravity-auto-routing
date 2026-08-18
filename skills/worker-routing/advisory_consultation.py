#!/usr/bin/env python3
"""Backward-compatible, ultra-slim facade for CriticalDialogue."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any


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


# Keep the historic flat API while the implementation lives in focused modules.
_debate_orchestrator = _load_sibling("debate_orchestrator")
_prompt_assembler = _load_sibling("prompt_assembler")
_sensitivity_redactor = _load_sibling("sensitivity_redactor")
_executive_dialogue_report = _load_sibling("executive_dialogue_report")
_dialogue_degradation = _load_sibling("dialogue_degradation")
_dialogue_contracts = _load_sibling("dialogue_contracts")
_dialogue_transcript = _load_sibling("dialogue_transcript")

_modules = (
    _debate_orchestrator, _prompt_assembler, _sensitivity_redactor,
    _executive_dialogue_report, _dialogue_degradation, _dialogue_contracts,
    _dialogue_transcript,
)

# Bind the execution entry points explicitly for static callers and type checkers.
run_advisory_consultation_debate = _debate_orchestrator.run_advisory_consultation_debate
run_debate_loop = _debate_orchestrator.run_debate_loop
run_canary_dialogue = _debate_orchestrator.run_canary_dialogue
run_post_mortem_loop = _debate_orchestrator.run_post_mortem_loop
def dispatch_post_mortem_consultation(*args: Any, **kwargs: Any) -> Any:
    """Compatibility wrapper preserving patches to the historic facade API."""
    _debate_orchestrator.run_advisory_consultation_debate = run_advisory_consultation_debate
    return _debate_orchestrator.dispatch_post_mortem_consultation(*args, **kwargs)

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
__all__ = (
    "Occasion", "AdvisoryOutcome", "AdvisoryRoundVerdict",
    "AdvisoryResolutionOption", "AdvisoryStalemateReport", "AdvisoryDebateRound",
    "AdvisoryDebateResult", "AdvisoryTelemetryRecord", "ConsultationTranscript",
    "CanaryFixture", "CanaryResult", "TaskIdentity", "MissionCopy",
    "VerdictContractResult", "CriticVerdict", "DegradationLadderState",
    "DegradationRung", "ExecutiveDialogueReport", "RosterAssignment",
    "RosterResolution", "RosterResolutionError", "RosterTopology", "RosterRole",
    "InvokeWorker", "IsFamilyReachable", "DebateRoundRecord", "DebateSessionState",
    "CRITIC_VERDICT_APPROVE", "CRITIC_VERDICT_REVISE", "WORKER_MODE_TOKEN",
    "MAX_DEBATE_ROUNDS", "ESCALATION_FAILURE_THRESHOLD", "SENSITIVITY_MARKERS",
    "BUDGET_DEGRADATION_MARKER", "DEGRADED_INDEPENDENCE_MARKER", "CANARY_MARKER",
    "DEFAULT_SESSION_DIALOGUE_CAP", "DEFAULT_CODE_REVIEW_DIFF_LINE_THRESHOLD",
    "DEFAULT_SECURITY_SENSITIVE_PATH_PATTERNS", "DEFAULT_ROSTER_FALLBACK_CHAINS",
    "DEFAULT_CANARY_DIALOGUES_PER_CANARY",
    "DEFAULT_CANARY_SECONDS_BETWEEN_CANARIES", "CANARY_FIXTURES", "MISSION_COPY",
    "PANEL_TOPOLOGY_OCCASIONS",
    "build_planner_prompt", "build_critic_prompt", "build_canary_prompt",
    "build_adjudicator_prompt", "build_stalemate_prompt", "combine_panel_critic_feedback",
    "format_budget_degradation_alert", "render_executive_summary",
    "scan_sensitivity_markers", "derive_safe_task_identity", "detect_sensitivity_marker",
    "classify_model_family", "is_local_family", "resolve_degradation_rung",
    "resolve_roster", "is_canary_dialogue", "is_panel_topology",
    "build_stalemate_report", "evaluate_round_verdicts", "advance_debate_state",
    "needs_advisory_consultation", "needs_plan_review_consultation",
    "needs_code_review_consultation", "needs_post_mortem_consultation",
    "run_advisory_consultation_debate", "run_debate_loop", "run_canary_dialogue",
    "run_post_mortem_loop", "dispatch_post_mortem_consultation",
)
