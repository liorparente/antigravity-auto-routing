#!/usr/bin/env python3
"""Backward-compatible, ultra-slim facade for CriticalDialogue."""
from __future__ import annotations

import threading
from pathlib import Path

if __package__:
    from . import debate_orchestrator as _debate_orchestrator
    from . import debate_state_machine as _debate_state_machine
    from . import debate_transport as _debate_transport
    from . import dialogue_contracts as _dialogue_contracts
    from . import dialogue_degradation as _dialogue_degradation
    from . import dialogue_transcript as _dialogue_transcript
    from . import executive_dialogue_report as _executive_dialogue_report
    from . import prompt_assembler as _prompt_assembler
    from . import sensitivity_redactor as _sensitivity_redactor
else:
    import debate_orchestrator as _debate_orchestrator  # type: ignore[no-redef]
    import debate_state_machine as _debate_state_machine  # type: ignore[no-redef]
    import debate_transport as _debate_transport  # type: ignore[no-redef]
    import dialogue_contracts as _dialogue_contracts  # type: ignore[no-redef]
    import dialogue_degradation as _dialogue_degradation  # type: ignore[no-redef]
    import dialogue_transcript as _dialogue_transcript  # type: ignore[no-redef]
    import executive_dialogue_report as _executive_dialogue_report  # type: ignore[no-redef]
    import prompt_assembler as _prompt_assembler  # type: ignore[no-redef]
    import sensitivity_redactor as _sensitivity_redactor  # type: ignore[no-redef]

_CONFIG_PATH = Path(__file__).with_name("routing-config.json")

# Symbols owned by debate_orchestrator itself: roster resolution, canary
# fixtures, the AdvisoryDebateResult/Round contracts, and every run_*/needs_*
# entry point this facade exists to expose.
AdvisoryDebateResult = _debate_orchestrator.AdvisoryDebateResult
AdvisoryDebateRound = _debate_orchestrator.AdvisoryDebateRound
CANARY_FIXTURES = _debate_orchestrator.CANARY_FIXTURES
CanaryFixture = _debate_orchestrator.CanaryFixture
CanaryResult = _debate_orchestrator.CanaryResult
DEFAULT_CANARY_DIALOGUES_PER_CANARY = _debate_orchestrator.DEFAULT_CANARY_DIALOGUES_PER_CANARY
DEFAULT_CANARY_SECONDS_BETWEEN_CANARIES = _debate_orchestrator.DEFAULT_CANARY_SECONDS_BETWEEN_CANARIES
DEFAULT_CODE_REVIEW_DIFF_LINE_THRESHOLD = _debate_orchestrator.DEFAULT_CODE_REVIEW_DIFF_LINE_THRESHOLD
DEFAULT_ROSTER_FALLBACK_CHAINS = _debate_orchestrator.DEFAULT_ROSTER_FALLBACK_CHAINS
DEFAULT_SECURITY_SENSITIVE_PATH_PATTERNS = _debate_orchestrator.DEFAULT_SECURITY_SENSITIVE_PATH_PATTERNS
ESCALATION_FAILURE_THRESHOLD = _debate_orchestrator.ESCALATION_FAILURE_THRESHOLD
InvokeWorker = _debate_orchestrator.InvokeWorker
IsFamilyReachable = _debate_orchestrator.IsFamilyReachable
MAX_DEBATE_ROUNDS = _debate_orchestrator.MAX_DEBATE_ROUNDS
PrivacyMode = _debate_orchestrator.PrivacyMode
ReviewCouncil = _debate_orchestrator.ReviewCouncil
ReviewOutcome = _debate_orchestrator.ReviewOutcome
ReviewRequest = _debate_orchestrator.ReviewRequest
RosterAssignment = _debate_orchestrator.RosterAssignment
RosterResolution = _debate_orchestrator.RosterResolution
RosterResolutionError = _debate_orchestrator.RosterResolutionError
RosterRole = _debate_orchestrator.RosterRole
RosterTopology = _debate_orchestrator.RosterTopology
WORKER_MODE_TOKEN = _debate_orchestrator.WORKER_MODE_TOKEN
LEGACY_WORKER_MODE_TOKEN = _debate_orchestrator.LEGACY_WORKER_MODE_TOKEN
_CLOUD_FAMILY_SUBSTRINGS = _debate_orchestrator._CLOUD_FAMILY_SUBSTRINGS
_build_stalemate_report = _debate_orchestrator._build_stalemate_report
_resolve_task_id = _debate_orchestrator._resolve_task_id
classify_model_family = _debate_orchestrator.classify_model_family
is_canary_dialogue = _debate_orchestrator.is_canary_dialogue
is_local_family = _debate_orchestrator.is_local_family
needs_advisory_consultation = _debate_orchestrator.needs_advisory_consultation
needs_code_review_consultation = _debate_orchestrator.needs_code_review_consultation
needs_plan_review_consultation = _debate_orchestrator.needs_plan_review_consultation
needs_post_mortem_consultation = _debate_orchestrator.needs_post_mortem_consultation
resolve_roster = _debate_orchestrator.resolve_roster
run_advisory_consultation_debate = _debate_orchestrator.run_advisory_consultation_debate
run_canary_dialogue = _debate_orchestrator.run_canary_dialogue
run_critical_dialogue = _debate_orchestrator.run_critical_dialogue
run_debate_loop = _debate_orchestrator.run_debate_loop
run_post_mortem_loop = _debate_orchestrator.run_post_mortem_loop

# Verdict contracts and the pure VerdictContract parser.
AdvisoryOutcome = _dialogue_contracts.AdvisoryOutcome
AdvisoryResolutionOption = _dialogue_contracts.AdvisoryResolutionOption
AdvisoryRoundVerdict = _dialogue_contracts.AdvisoryRoundVerdict
AdvisoryStalemateReport = _dialogue_contracts.AdvisoryStalemateReport
CRITIC_VERDICT_APPROVE = _dialogue_contracts.CRITIC_VERDICT_APPROVE
CRITIC_VERDICT_REVISE = _dialogue_contracts.CRITIC_VERDICT_REVISE
CriticVerdict = _dialogue_contracts.CriticVerdict
Occasion = _dialogue_contracts.Occasion
VerdictContractResult = _dialogue_contracts.VerdictContractResult
_parse_critic_verdict = _dialogue_contracts._parse_critic_verdict
extract_objections = _dialogue_contracts.extract_objections
extract_quotes = _dialogue_contracts.extract_quotes
parse_verdict_contract = _dialogue_contracts.parse_verdict_contract
verify_quotes = _dialogue_contracts.verify_quotes

# Pure debate state machine.
ConsensusTable = _debate_state_machine.ConsensusTable
CriticResponse = _debate_state_machine.CriticResponse
DebateRoundRecord = _debate_state_machine.DebateRoundRecord
DebateSessionState = _debate_state_machine.DebateSessionState
DebateState = _debate_state_machine.DebateState
PANEL_TOPOLOGY_OCCASIONS = _debate_state_machine.PANEL_TOPOLOGY_OCCASIONS
RoundTurnResult = _debate_state_machine.RoundTurnResult
SecurityVeto = _debate_state_machine.SecurityVeto
SecurityVetoHandler = _debate_state_machine.SecurityVetoHandler
advance_debate_state = _debate_state_machine.advance_debate_state
build_stalemate_report = _debate_state_machine.build_stalemate_report
evaluate_quorum = _debate_state_machine.evaluate_quorum
evaluate_round_verdicts = _debate_state_machine.evaluate_round_verdicts
is_panel_topology = _debate_state_machine.is_panel_topology

# Isolated worker transport.
DebateTransport = _debate_transport.DebateTransport
RecurringFailureNotifier = _debate_transport.RecurringFailureNotifier

# Dialogue budget degradation ladder.
BUDGET_DEGRADATION_MARKER = _dialogue_degradation.BUDGET_DEGRADATION_MARKER
DEFAULT_SESSION_DIALOGUE_CAP = _dialogue_degradation.DEFAULT_SESSION_DIALOGUE_CAP
DegradationLadderState = _dialogue_degradation.DegradationLadderState
DegradationRung = _dialogue_degradation.DegradationRung
_DEFAULT_DEGRADED_ROSTER_MODEL = _dialogue_degradation._DEFAULT_DEGRADED_ROSTER_MODEL
_DEGRADED_EFFORT = _dialogue_degradation._DEGRADED_EFFORT
_load_degraded_roster_model = _dialogue_degradation._load_degraded_roster_model
_load_dialogue_budget_config = _dialogue_degradation._load_dialogue_budget_config
resolve_degradation_rung = _dialogue_degradation.resolve_degradation_rung

# Transcript, telemetry, and LearningJournal persistence.
AdvisoryTelemetryRecord = _dialogue_transcript.AdvisoryTelemetryRecord
CANARY_MARKER = _dialogue_transcript.CANARY_MARKER
ConsultationTranscript = _dialogue_transcript.ConsultationTranscript
DEGRADED_INDEPENDENCE_MARKER = _dialogue_transcript.DEGRADED_INDEPENDENCE_MARKER
_append_jsonl_locked = _dialogue_transcript._append_jsonl_locked
_atomic_text_write = _dialogue_transcript._atomic_text_write
_build_telemetry_record = _dialogue_transcript._build_telemetry_record
_default_task_id = _dialogue_transcript._default_task_id
_reduce_dialogue_round = _dialogue_transcript._reduce_dialogue_round
_write_dialogue_quality_record = _dialogue_transcript._write_dialogue_quality_record
_write_plan_outcome_record = _dialogue_transcript._write_plan_outcome_record
_write_telemetry_record = _dialogue_transcript._write_telemetry_record
format_transcript_markdown = _dialogue_transcript.format_transcript_markdown
render_consultation_transcript = _dialogue_transcript.render_consultation_transcript
render_sensitivity_halt_transcript = _dialogue_transcript.render_sensitivity_halt_transcript

# Pure executive-facing formatting.
ExecutiveDialogueReport = _executive_dialogue_report.ExecutiveDialogueReport
format_budget_degradation_alert = _executive_dialogue_report.format_budget_degradation_alert
render_executive_summary = _executive_dialogue_report.render_executive_summary

# Prompt assembly.
MISSION_COPY = _prompt_assembler.MISSION_COPY
MissionCopy = _prompt_assembler.MissionCopy
build_adjudicator_prompt = _prompt_assembler.build_adjudicator_prompt
build_canary_prompt = _prompt_assembler.build_canary_prompt
build_critic_prompt = _prompt_assembler.build_critic_prompt
build_planner_prompt = _prompt_assembler.build_planner_prompt
build_stalemate_prompt = _prompt_assembler.build_stalemate_prompt
combine_panel_critic_feedback = _prompt_assembler.combine_panel_critic_feedback

# Sensitivity scanning and redaction.
SENSITIVITY_MARKERS = _sensitivity_redactor.SENSITIVITY_MARKERS
TaskIdentity = _sensitivity_redactor.TaskIdentity
derive_safe_task_identity = _sensitivity_redactor.derive_safe_task_identity
detect_sensitivity_marker = _sensitivity_redactor.detect_sensitivity_marker
scan_sensitivity_markers = _sensitivity_redactor.scan_sensitivity_markers


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


__all__ = [
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
    "LEGACY_WORKER_MODE_TOKEN",
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
]
