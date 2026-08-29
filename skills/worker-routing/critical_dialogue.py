"""Canonical public boundary for CriticalDialogue.

This deep module consolidates the supported Planner/Critic dialogue and
Council-review APIs.  Its implementation is deliberately delegated to the
production debate engine while that engine's private state-machine,
transport, transcript, signing, and degradation seams remain encapsulated.
The explicit bindings below preserve both package and path-based imports.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

if __package__:
    from . import debate_orchestrator as _engine
else:
    import debate_orchestrator as _engine  # type: ignore[no-redef]


# Dialogue execution and lifecycle.
run_critical_dialogue = _engine.run_critical_dialogue
run_advisory_consultation_debate = _engine.run_advisory_consultation_debate
run_debate_loop = _engine.run_debate_loop
run_canary_dialogue = _engine.run_canary_dialogue
run_post_mortem_loop = _engine.run_post_mortem_loop
dispatch_post_mortem_consultation = _engine.dispatch_post_mortem_consultation

# Council review, quorum reduction, and signed-manifest support.
ReviewCouncil = _engine.ReviewCouncil
ReviewRequest = _engine.ReviewRequest
ReviewOutcome = _engine.ReviewOutcome
PrivacyMode = _engine.PrivacyMode
SecurityVeto = _engine.SecurityVeto
SecurityVetoHandler = _engine.SecurityVetoHandler
ConsensusTable = _engine.ConsensusTable
DEFAULT_CONSULTATION_POLICY = _engine.DEFAULT_CONSULTATION_POLICY
load_consultation_policy = _engine.load_consultation_policy
resolve_hmac_secret = _engine.resolve_hmac_secret
write_council_manifest = _engine.write_council_manifest
ROUTING_CONFIG_PATH = _engine.ROUTING_CONFIG_PATH
InvokeWorker = _engine.InvokeWorker
IsFamilyReachable = _engine.IsFamilyReachable
ReviewerAdapterProtocol = _engine.ReviewerAdapterProtocol
WORKER_MODE_TOKEN = _engine.WORKER_MODE_TOKEN
LEGACY_WORKER_MODE_TOKEN = _engine.LEGACY_WORKER_MODE_TOKEN
ESCALATION_FAILURE_THRESHOLD = _engine.ESCALATION_FAILURE_THRESHOLD

# Dialogue contracts, canaries, and roster resolution.
AdvisoryDebateResult = _engine.AdvisoryDebateResult
AdvisoryDebateRound = _engine.AdvisoryDebateRound
CanaryFixture = _engine.CanaryFixture
CanaryResult = _engine.CanaryResult
CANARY_FIXTURES = _engine.CANARY_FIXTURES
RosterAssignment = _engine.RosterAssignment
RosterResolution = _engine.RosterResolution
RosterResolutionError = _engine.RosterResolutionError
RosterRole = _engine.RosterRole
RosterTopology = _engine.RosterTopology
resolve_roster = _engine.resolve_roster
classify_model_family = _engine.classify_model_family
is_local_family = _engine.is_local_family
is_canary_dialogue = _engine.is_canary_dialogue

# Occasion decision predicates.
needs_advisory_consultation = _engine.needs_advisory_consultation
needs_plan_review_consultation = _engine.needs_plan_review_consultation
needs_code_review_consultation = _engine.needs_code_review_consultation
needs_post_mortem_consultation = _engine.needs_post_mortem_consultation


def request_council_review(
    request: ReviewRequest, policy_path: str | Path = ROUTING_CONFIG_PATH
) -> ReviewOutcome:
    """Run a multi-model council review and return the consolidated outcome.

    ``ReviewCouncil.review`` is asynchronous because it executes all
    providers concurrently.  The public request helper is synchronous so a
    normal routing decision can obtain its consolidated outcome directly.
    """
    council = ReviewCouncil(policy_path=policy_path)
    return asyncio.run(council.review(request))


# Retain the narrow internal compatibility seam used by established callers
# without exposing every implementation detail as supported public API.
def __getattr__(name: str) -> Any:
    """Resolve historic engine attributes during the consolidation window."""
    return getattr(_engine, name)


__all__ = (
    "CANARY_FIXTURES",
    "DEFAULT_CONSULTATION_POLICY",
    "ESCALATION_FAILURE_THRESHOLD",
    "LEGACY_WORKER_MODE_TOKEN",
    "ROUTING_CONFIG_PATH",
    "WORKER_MODE_TOKEN",
    "AdvisoryDebateResult",
    "AdvisoryDebateRound",
    "CanaryFixture",
    "CanaryResult",
    "ConsensusTable",
    "InvokeWorker",
    "IsFamilyReachable",
    "PrivacyMode",
    "ReviewCouncil",
    "ReviewOutcome",
    "ReviewRequest",
    "ReviewerAdapterProtocol",
    "RosterAssignment",
    "RosterResolution",
    "RosterResolutionError",
    "RosterRole",
    "RosterTopology",
    "SecurityVeto",
    "SecurityVetoHandler",
    "classify_model_family",
    "dispatch_post_mortem_consultation",
    "is_canary_dialogue",
    "is_local_family",
    "load_consultation_policy",
    "needs_advisory_consultation",
    "needs_code_review_consultation",
    "needs_plan_review_consultation",
    "needs_post_mortem_consultation",
    "request_council_review",
    "resolve_hmac_secret",
    "resolve_roster",
    "run_advisory_consultation_debate",
    "run_canary_dialogue",
    "run_critical_dialogue",
    "run_debate_loop",
    "run_post_mortem_loop",
    "write_council_manifest",
)
