import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
import sys

SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent / "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from council_review import (
    ReviewCouncil,
    ReviewRequest,
    ReviewOutcome,
    PrivacyMode,
    ConsensusTable,
    SecurityVetoHandler,
    SecurityVeto,
)
from provider_adapters import (
    FakeReviewerAdapter,
    ClaudeAdapter,
    CodexAdapter,
    AgyAdapter,
    LMStudioAdapter,
)

POLICY_PATH = str(Path(__file__).resolve().parent.parent / "references" / "council-policy.json")


class CouncilReviewTDDTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace_root = self.temp_dir.name
        self.cal_dir = os.path.join(self.workspace_root, ".ralph", "cache")
        os.makedirs(self.cal_dir, exist_ok=True)
        self.cal_key = os.path.join(self.cal_dir, "calibration.key")
        with open(self.cal_key, "w") as f:
            f.write("test_secret_key_12345")

    def tearDown(self):
        self.temp_dir.cleanup()

    # Slice 1: Security Veto execution
    def test_unilateral_security_veto_halts_review(self):
        veto_handler = SecurityVetoHandler(
            veto_severities=["critical", "high"],
            security_threshold=0.80,
            enabled=True,
        )
        votes = [
            {"provider": "claude", "vote": "approve", "confidence": 1.0},
            {"provider": "codex", "vote": "block", "confidence": "0.95", "findings": [
                {"id": "SEC-01", "severity": "critical", "claim": "SQL injection in auth", "confidence": "0.90"}
            ]},
            {"provider": "gemini", "vote": "approve", "confidence": 0.8},
        ]
        veto = veto_handler.check(votes)
        self.assertIsNotNone(veto)
        self.assertEqual(veto.provider, "codex")
        self.assertEqual(veto.finding["id"], "SEC-01")

    # Slice 2: Dynamic Weights Bounds and Unknown Provider Filtering
    def test_dynamic_weights_clamping_and_filtering(self):
        council = ReviewCouncil(POLICY_PATH)
        weights_file = os.path.join(self.workspace_root, ".ralph", "council_weights.json")
        os.makedirs(os.path.dirname(weights_file), exist_ok=True)
        with open(weights_file, "w") as f:
            json.dump({
                "claude": 10.0,         # Out of bounds (> 0.65)
                "gemini": 0.01,         # Out of bounds (< 0.05)
                "malicious_model": 5.0, # Unknown provider, must be ignored
            }, f)

        weights = council._load_weights(self.workspace_root)
        self.assertNotIn("malicious_model", weights)
        self.assertLessEqual(weights["claude"], 0.65)
        self.assertGreaterEqual(weights["gemini"], 0.05)
        self.assertEqual(weights["codex"], 0.40)  # Default preserved

    # Slice 3: CLI Adapters non-interactive tokens and flags
    def test_cli_adapters_arguments_and_worker_tokens(self):
        claude = ClaudeAdapter("claude-opus-5", "high")
        codex = CodexAdapter("gpt-5.6-sol", "high")
        agy = AgyAdapter("gemini-3.1-pro", "high")

        claude_args = claude._get_args("Review this plan")
        codex_args = codex._get_args("Review this plan")
        agy_args = agy._get_args("Review this plan")

        self.assertIn("--allow-dangerously-skip-permissions", claude_args)
        self.assertIn("bypassPermissions", claude_args)
        self.assertIn("--no-session-persistence", claude_args)
        self.assertIn("-s", codex_args)
        self.assertIn("workspace-write", codex_args)
        self.assertIn("[WORKER-MODE: AGY-NESTED-EXEC]", " ".join(claude_args))
        self.assertIn("[WORKER-MODE: AGY-NESTED-EXEC]", " ".join(codex_args))
        self.assertIn("[WORKER-MODE: AGY-NESTED-EXEC]", " ".join(agy_args))

    # Slice 4: Privacy Mode Local-Only Enforcement
    def test_local_only_privacy_mode_enforcement(self):
        council = ReviewCouncil(POLICY_PATH)
        req = ReviewRequest(
            objective="Sensitive task review",
            workspace_root=self.workspace_root,
            privacy_mode=PrivacyMode.LOCAL_ONLY,
        )
        adapters = council._resolve_adapters(req)
        # Must only return local adjudicator, zero cloud providers
        self.assertEqual(len(adapters), 1)
        self.assertEqual(adapters[0].provider_id, "lm-studio")

    # Slice 5: Secret Key Resolution
    def test_hmac_secret_resolution_fallback(self):
        council = ReviewCouncil(POLICY_PATH)
        # Clear env var if set
        old_env = os.environ.pop("COUNCIL_REVIEW_SECRET", None)
        try:
            secret = council._resolve_secret(self.workspace_root)
            self.assertEqual(secret, b"test_secret_key_12345")
        finally:
            if old_env:
                os.environ["COUNCIL_REVIEW_SECRET"] = old_env

    # Slice 6: End-to-End Async Review with Custom Adapters (Unanimous Approval)
    def test_async_review_unanimous_approval(self):
        council = ReviewCouncil(POLICY_PATH)
        req = ReviewRequest(
            objective="Feature implementation plan",
            workspace_root=self.workspace_root,
        )
        adapters = [
            FakeReviewerAdapter("claude", [{"provider": "claude", "vote": "approve", "confidence": 1.0, "candidate_hash": "synth1"}] * 3),
            FakeReviewerAdapter("codex", [{"provider": "codex", "vote": "approve", "confidence": 1.0, "candidate_hash": "synth1"}] * 3),
            FakeReviewerAdapter("gemini", [{"provider": "gemini", "vote": "approve", "confidence": 0.9, "candidate_hash": "synth1"}] * 3),
        ]
        outcome = asyncio.run(council.review(req, custom_adapters=adapters))
        self.assertEqual(outcome.status, "UNANIMOUS")
        self.assertTrue(os.path.isfile(outcome.manifest_path))

    # Slice 7: End-to-End Async Review with Security Veto Halt
    def test_async_review_security_veto_halt(self):
        council = ReviewCouncil(POLICY_PATH)
        req = ReviewRequest(
            objective="Feature implementation with vulnerability",
            workspace_root=self.workspace_root,
        )
        adapters = [
            FakeReviewerAdapter("claude", [{"provider": "claude", "vote": "approve", "confidence": 1.0}] * 3),
            FakeReviewerAdapter("codex", [{
                "provider": "codex",
                "vote": "block",
                "confidence": 1.0,
                "findings": [{"id": "CWE-89", "severity": "critical", "confidence": 1.0, "claim": "SQLi in query"}]
            }] * 3),
            FakeReviewerAdapter("gemini", [{"provider": "gemini", "vote": "approve", "confidence": 1.0}] * 3),
        ]
        outcome = asyncio.run(council.review(req, custom_adapters=adapters))
        self.assertEqual(outcome.status, "SECURITY_HALT")
        self.assertEqual(outcome.unresolved_blockers, 1)


if __name__ == "__main__":
    unittest.main()
