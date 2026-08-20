"""Compatibility facade for the unified council-review implementation."""
from __future__ import annotations

import sys
from pathlib import Path

WORKER_ROUTING_DIR = str(Path(__file__).resolve().parents[2] / "worker-routing")
if WORKER_ROUTING_DIR not in sys.path:
    sys.path.insert(0, WORKER_ROUTING_DIR)

from debate_orchestrator import (
    DEFAULT_CONSULTATION_POLICY,
    ROUTING_CONFIG_PATH,
    ConsensusTable,
    PrivacyMode,
    ReviewCouncil,
    ReviewOutcome,
    ReviewRequest,
    SecurityVeto,
    SecurityVetoHandler,
    load_consultation_policy,
    resolve_hmac_secret,
    write_council_manifest,
)

__all__ = [
    "DEFAULT_CONSULTATION_POLICY", "ROUTING_CONFIG_PATH", "ConsensusTable", "PrivacyMode", "ReviewCouncil", "ReviewOutcome",
    "ReviewRequest", "SecurityVeto", "SecurityVetoHandler", "load_consultation_policy", "resolve_hmac_secret", "write_council_manifest"]
