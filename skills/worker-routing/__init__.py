"""Canonical public API for the worker-routing package.

The implementation predates package installation. This package boundary
exposes its stable public surface through package-relative imports.
"""
from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

_PKG_DIR = str(_Path(__file__).parent.resolve())
if _PKG_DIR not in _sys.path:
    _sys.path.insert(0, _PKG_DIR)

from . import acceptance_gate as _gate
from . import advisory_consultation as _advisory
from . import agent_council as _council
from . import dialogue_contracts as _dialogue_contracts
from . import dialogue_degradation as _dialogue_degradation
from . import learned_state as _learned_state
from . import learning_journal as _journal
from . import learning_outcomes as _outcomes
from . import production_invoker as _invoker
from . import routing_check as _routing_check

# Advisory and critical dialogue.
run_critical_dialogue = _advisory.run_critical_dialogue
run_advisory_consultation_debate = _advisory.run_advisory_consultation_debate
run_debate_loop = _advisory.run_debate_loop
run_canary_dialogue = _advisory.run_canary_dialogue
run_post_mortem_loop = _advisory.run_post_mortem_loop
dispatch_post_mortem_consultation = _advisory.dispatch_post_mortem_consultation
ReviewCouncil = _advisory.ReviewCouncil
ReviewRequest = _advisory.ReviewRequest
ReviewOutcome = _advisory.ReviewOutcome
SecurityVeto = _advisory.SecurityVeto
SecurityVetoHandler = _advisory.SecurityVetoHandler
ConsensusTable = _advisory.ConsensusTable
PrivacyMode = _advisory.PrivacyMode
DebateTransport = _advisory.DebateTransport
RecurringFailureNotifier = _advisory.RecurringFailureNotifier

# Dialogue contracts.
AdvisoryDebateResult = _advisory.AdvisoryDebateResult
AdvisoryDebateRound = _advisory.AdvisoryDebateRound
AdvisoryOutcome = _dialogue_contracts.AdvisoryOutcome
AdvisoryResolutionOption = _dialogue_contracts.AdvisoryResolutionOption
AdvisoryRoundVerdict = _dialogue_contracts.AdvisoryRoundVerdict
AdvisoryStalemateReport = _dialogue_contracts.AdvisoryStalemateReport
AdvisoryTelemetryRecord = _advisory.AdvisoryTelemetryRecord
CanaryFixture = _advisory.CanaryFixture
CanaryResult = _advisory.CanaryResult
ConsultationTranscript = _advisory.ConsultationTranscript
CriticResponse = _advisory.CriticResponse
CriticVerdict = _dialogue_contracts.CriticVerdict
DebateRoundRecord = _advisory.DebateRoundRecord
DebateSessionState = _advisory.DebateSessionState
DebateState = _advisory.DebateState
DegradationLadderState = _advisory.DegradationLadderState
DegradationRung = _dialogue_degradation.DegradationRung
ExecutiveDialogueReport = _advisory.ExecutiveDialogueReport
InvokeWorker = _advisory.InvokeWorker
IsFamilyReachable = _advisory.IsFamilyReachable
MissionCopy = _advisory.MissionCopy
Occasion = _dialogue_contracts.Occasion
RosterAssignment = _advisory.RosterAssignment
RosterResolution = _advisory.RosterResolution
RosterResolutionError = _advisory.RosterResolutionError
RosterRole = _advisory.RosterRole
RosterTopology = _advisory.RosterTopology
RoundTurnResult = _advisory.RoundTurnResult
TaskIdentity = _advisory.TaskIdentity
VerdictContractResult = _dialogue_contracts.VerdictContractResult


LearningJournal = _journal
TaskLabel = _journal.TaskLabel
WorkerExecutionRecord = _journal.WorkerExecutionRecord
OutcomeRecord = _journal.OutcomeRecord
DialogueRound = _journal.DialogueRound
DialogueQualityRecord = _journal.DialogueQualityRecord
ComplianceRecord = _journal.ComplianceRecord
ReplayBenchmarkRecord = _journal.ReplayBenchmarkRecord
JournalRead = _journal.JournalRead
read_journal = _journal.read_journal
append_journal_record = _journal.append_journal_record
extract_issue_codes = _journal.extract_issue_codes
journal_path = _journal.journal_path

LearnedState = _learned_state
DocumentChange = _learned_state.DocumentChange
DocumentDelta = _learned_state.DocumentDelta
VersionEntry = _learned_state.VersionEntry
adopt = _learned_state.adopt
roll_back = _learned_state.roll_back
read_history = _learned_state.read_history
read_current = _learned_state.read_current
current_version_dir = _learned_state.current_version_dir

record_test_result = _outcomes.record_test_result
record_review_verdict = _outcomes.record_review_verdict
record_plan_outcome = _outcomes.record_plan_outcome
record_stalemate_resolution = _outcomes.record_stalemate_resolution

evaluate_proposal_gate = _gate.evaluate_proposal
AcceptanceGateResult = _gate.GateDecision

invoke_worker = _invoker.invoke_worker


def resolve_model_name(model: str) -> str:
    """Return a supported model's canonical CLI identifier, failing closed."""
    resolved = _invoker.MODEL_ALIASES.get(model)
    if resolved is None:
        raise ValueError(f"Unsupported worker model: {model!r}")
    return resolved


resolve_cli_command = _invoker.build_worker_command
classify_model_family = _advisory.classify_model_family


def classify_complexity(complexity: str) -> str:
    """Validate and normalize a routing complexity label."""
    if not isinstance(complexity, str):
        raise TypeError("complexity must be a string")
    normalized = complexity.strip().lower()
    if normalized not in _council.VALID_COMPLEXITIES:
        raise ValueError(f"unsupported complexity: {complexity!r}")
    return normalized


escalate_routing_effort = _council.escalate_routing_effort
run_audit = _routing_check.run_audit

__version__ = "3.5.0"

__all__ = (
    "AcceptanceGateResult", "AdvisoryDebateResult", "AdvisoryDebateRound", "AdvisoryOutcome",
    "AdvisoryResolutionOption", "AdvisoryRoundVerdict", "AdvisoryStalemateReport",
    "AdvisoryTelemetryRecord", "CanaryFixture", "CanaryResult", "ComplianceRecord", "ConsensusTable",
    "ConsultationTranscript", "CriticResponse", "CriticVerdict", "DebateRoundRecord", "DebateSessionState",
    "DebateState", "DebateTransport", "DegradationLadderState", "DegradationRung", "DialogueQualityRecord",
    "DialogueRound", "DocumentChange", "DocumentDelta", "ExecutiveDialogueReport", "InvokeWorker",
    "IsFamilyReachable", "JournalRead", "LearnedState", "LearningJournal", "MissionCopy", "Occasion",
    "OutcomeRecord", "PrivacyMode", "RecurringFailureNotifier", "ReplayBenchmarkRecord", "ReviewCouncil",
    "ReviewOutcome", "ReviewRequest", "RosterAssignment", "RosterResolution", "RosterResolutionError",
    "RosterRole", "RosterTopology", "RoundTurnResult", "SecurityVeto", "SecurityVetoHandler", "TaskIdentity",
    "TaskLabel", "VerdictContractResult", "VersionEntry", "WorkerExecutionRecord", "__version__", "adopt",
    "append_journal_record", "classify_complexity", "classify_model_family", "current_version_dir",
    "dispatch_post_mortem_consultation", "escalate_routing_effort", "evaluate_proposal_gate",
    "extract_issue_codes", "invoke_worker", "journal_path", "read_current", "read_history", "read_journal",
    "record_plan_outcome", "record_review_verdict", "record_stalemate_resolution", "record_test_result",
    "resolve_cli_command", "resolve_model_name", "roll_back", "run_advisory_consultation_debate", "run_audit",
    "run_canary_dialogue", "run_critical_dialogue", "run_debate_loop", "run_post_mortem_loop",
)
