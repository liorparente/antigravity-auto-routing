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
    PrivacyMode,
    SecurityVetoHandler,
)
from provider_adapters import (
    FakeReviewerAdapter,
    ClaudeAdapter,
    CodexAdapter,
    AgyAdapter,
    CLIReviewerAdapter,
)

POLICY_PATH = str(Path(__file__).resolve().parent.parent / "references" / "council-policy.json")


class CouncilReviewTDDTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace_root = self.temp_dir.name
        self.cal_dir = os.path.join(self.workspace_root, ".ralph", "cache")
        os.makedirs(self.cal_dir, exist_ok=True)
        self.cal_key = os.path.join(self.cal_dir, "calibration.key")
        with open(self.cal_key, "w") as f:
            f.write("test_secret_key_12345")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    # Slice 1: Security Veto execution with uppercase severity support
    def test_unilateral_security_veto_halts_review(self) -> None:
        veto_handler = SecurityVetoHandler(
            veto_severities=["critical", "high"],
            security_threshold=0.80,
            enabled=True,
        )
        votes = [
            {"provider": "claude", "vote": "approve", "confidence": 1.0},
            {"provider": "codex", "vote": "block", "confidence": "0.95", "findings": [
                {"id": "SEC-01", "severity": "CRITICAL", "claim": "SQL injection in auth", "confidence": "0.90"}
            ]},
            {"provider": "gemini", "vote": "approve", "confidence": 0.8},
        ]
        veto = veto_handler.check(votes)
        self.assertIsNotNone(veto)
        self.assertEqual(veto.provider, "codex")
        self.assertEqual(veto.finding["id"], "SEC-01")

    # Slice 2: Dynamic Weights Bounds and Unknown Provider Filtering
    def test_dynamic_weights_clamping_and_filtering(self) -> None:
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
    def test_cli_adapters_arguments_and_worker_tokens(self) -> None:
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
    def test_local_only_privacy_mode_enforcement(self) -> None:
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

        # Verify async review passes with local adapter without zero-weight lockout
        outcome = asyncio.run(council.review(req, custom_adapters=adapters))
        self.assertEqual(outcome.status, "UNANIMOUS")

    # Slice 5: Secret Key Resolution with AGY_CALIBRATION_SECRET & Fallback
    def test_hmac_secret_resolution_fallback(self) -> None:
        council = ReviewCouncil(POLICY_PATH)
        # Clear env vars if set
        old_agy = os.environ.pop("AGY_CALIBRATION_SECRET", None)
        old_council = os.environ.pop("COUNCIL_REVIEW_SECRET", None)
        try:
            # Test file fallback
            secret = council._resolve_secret(self.workspace_root)
            self.assertEqual(secret, b"test_secret_key_12345")

            # Test AGY_CALIBRATION_SECRET env
            os.environ["AGY_CALIBRATION_SECRET"] = "env_secret_999"
            secret_env = council._resolve_secret(self.workspace_root)
            self.assertEqual(secret_env, b"env_secret_999")
        finally:
            if old_agy:
                os.environ["AGY_CALIBRATION_SECRET"] = old_agy
            if old_council:
                os.environ["COUNCIL_REVIEW_SECRET"] = old_council

    # Slice 6: End-to-End Async Review with Custom Adapters (Unanimous Approval)
    def test_async_review_unanimous_approval(self) -> None:
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
    def test_async_review_security_veto_halt(self) -> None:
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
                "findings": [{"id": "CWE-89", "severity": "CRITICAL", "confidence": 1.0, "claim": "SQLi in query"}]
            }] * 3),
            FakeReviewerAdapter("gemini", [{"provider": "gemini", "vote": "approve", "confidence": 1.0}] * 3),
        ]
        outcome = asyncio.run(council.review(req, custom_adapters=adapters))
        self.assertEqual(outcome.status, "SECURITY_HALT")
        self.assertEqual(outcome.unresolved_blockers, 1)


class _FakeAsyncProcess:
    """A minimal stand-in for the process handle `invoke_worker_async` awaits."""

    def __init__(self, *, stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0) -> None:
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr

    async def communicate(self):
        return self._stdout, self._stderr

    def kill(self) -> None:
        pass

    async def wait(self) -> int:
        return self.returncode


def _runner_returning(process: "_FakeAsyncProcess"):
    async def _runner(*args, **kwargs):
        return process

    return _runner


def _runner_raising(exc: Exception):
    async def _runner(*args, **kwargs):
        raise exc

    return _runner


class CLIReviewerAdapterReviewTests(unittest.TestCase):
    # CLIReviewerAdapter.review() delegates to production_invoker.invoke_worker_async
    # via an injected runner, rather than spawning its own subprocess.

    def test_review_success_returns_parsed_payload_with_provider(self) -> None:
        process = _FakeAsyncProcess(stdout=b'{"vote": "approve", "confidence": 0.9}')
        adapter = CodexAdapter("gpt-5.6-sol", "high", runner=_runner_returning(process))

        outcome = asyncio.run(adapter.review("proposal text", 1, 30))

        self.assertEqual(outcome["provider"], "codex")
        self.assertEqual(outcome["vote"], "approve")
        self.assertEqual(outcome["confidence"], 0.9)

    def test_review_nonzero_exit_returns_abstain_with_error(self) -> None:
        process = _FakeAsyncProcess(stdout=b"", stderr=b"boom", returncode=1)
        adapter = ClaudeAdapter("claude-sonnet-5", "high", runner=_runner_returning(process))

        outcome = asyncio.run(adapter.review("proposal text", 1, 30))

        self.assertEqual(outcome["provider"], "claude")
        self.assertEqual(outcome["vote"], "abstain")
        self.assertEqual(outcome["confidence"], 0.0)
        self.assertIn("error", outcome)

    def test_review_spawn_failure_returns_abstain_with_error(self) -> None:
        adapter = AgyAdapter(
            "gemini-3.1-pro", "high", runner=_runner_raising(RuntimeError("no such binary"))
        )

        outcome = asyncio.run(adapter.review("proposal text", 1, 30))

        self.assertEqual(outcome["provider"], "gemini")
        self.assertEqual(outcome["vote"], "abstain")
        self.assertEqual(outcome["confidence"], 0.0)
        self.assertIn("error", outcome)

    def test_parse_output_tags_payload_with_provider_id(self) -> None:
        adapter = CLIReviewerAdapter("codex", "gpt-5.6-sol", "high", "codex")

        payload = adapter._parse_output('{"vote": "revise", "confidence": 0.2}')

        self.assertEqual(payload["provider"], "codex")
        self.assertEqual(payload["vote"], "revise")
        self.assertEqual(payload["confidence"], 0.2)

    def test_parse_output_empty_output_uses_defaults(self) -> None:
        adapter = CLIReviewerAdapter("gemini", "gemini-3.1-pro", "high", "agy")

        payload = adapter._parse_output("")

        self.assertEqual(payload["provider"], "gemini")
        self.assertEqual(payload["vote"], "approve")
        self.assertEqual(payload["confidence"], 1.0)


if __name__ == "__main__":
    unittest.main()
