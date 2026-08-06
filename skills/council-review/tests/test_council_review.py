import unittest
import os
import json
import tempfile
import asyncio
from pathlib import Path

# Assume skills/council-review/scripts/council_review.py
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'scripts')))
from council_review import ReviewRequest, ReviewCouncil, PrivacyMode

class TestCouncilReview(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.policy_path = os.path.join(self.temp_dir.name, "council-policy.json")
        with open(self.policy_path, "w") as f:
            json.dump({"consensus_policy": ["UNANIMOUS", "QUALIFIED", "MATERIAL_DISAGREEMENT", "INCOMPLETE", "UNRESOLVED"]}, f)
            
        os.environ["COUNCIL_REVIEW_SECRET"] = "test-secret-for-unit-tests"

    def tearDown(self):
        self.temp_dir.cleanup()
        if "COUNCIL_REVIEW_SECRET" in os.environ:
            del os.environ["COUNCIL_REVIEW_SECRET"]

    def test_review_request_validation(self):
        with self.assertRaises(ValueError):
            ReviewRequest(objective="Test", workspace_root="", privacy_mode="invalid")
        
        req = ReviewRequest(objective="Fix bugs", workspace_root="/tmp", privacy_mode=PrivacyMode.LOCAL_ONLY)
        self.assertEqual(req.privacy_mode, "local-only")

    def test_council_initialization(self):
        council = ReviewCouncil(self.policy_path)
        self.assertIsNotNone(council.policy)

    def test_finding_ledger(self):
        from council_review import FindingLedger
        ledger = FindingLedger()
        ledger.add_finding({"id": "f1", "severity": "critical", "claim": "SQL injection", "resolved": False})
        ledger.add_finding({"id": "f2", "severity": "low", "claim": "Typo", "resolved": False})
        self.assertEqual(ledger.get_unresolved_blockers(), 1)
        ledger.add_finding({"id": "f1", "severity": "critical", "claim": "SQL injection", "resolved": True})
        self.assertEqual(ledger.get_unresolved_blockers(), 0)

    def test_consensus_table(self):
        from council_review import ConsensusTable
        table = ConsensusTable(policy=["UNANIMOUS", "MATERIAL_DISAGREEMENT", "INCOMPLETE"])
        
        # Missing provider -> INCOMPLETE
        self.assertEqual(table.evaluate([
            {"provider": "claude", "vote": "approve"},
            {"provider": "codex", "vote": "approve"}
        ]), "INCOMPLETE")

        # Unanimous
        self.assertEqual(table.evaluate([
            {"provider": "claude", "vote": "approve", "candidate_hash": "abc"},
            {"provider": "codex", "vote": "approve", "candidate_hash": "abc"},
            {"provider": "agy", "vote": "approve", "candidate_hash": "abc"}
        ]), "UNANIMOUS")

        # Material Disagreement (different candidate hash)
        self.assertEqual(table.evaluate([
            {"provider": "claude", "vote": "approve", "candidate_hash": "abc"},
            {"provider": "codex", "vote": "approve", "candidate_hash": "abc"},
            {"provider": "agy", "vote": "approve", "candidate_hash": "def"}
        ]), "MATERIAL_DISAGREEMENT")

    def test_staleness_detection(self):
        subject_file = os.path.join(self.temp_dir.name, "subject.txt")
        with open(subject_file, "w") as f:
            f.write("initial")
            
        req = ReviewRequest(objective="Test", workspace_root=self.temp_dir.name, subject=subject_file, privacy_mode=PrivacyMode.LOCAL_ONLY)
        council = ReviewCouncil(self.policy_path)
        
        # Test 1: No change
        outcome = asyncio.run(council.review(req))
        self.assertFalse(outcome.source_changed)
        # Verify HMACS via public object property
        self.assertIsNotNone(outcome.manifest_path)
        
        # Test 2: Source changed during review
        # Instead of mocking internal hashing functions, we mock the execute_round 
        # to block briefly, and change the file during execution to test staleness safely.
        async def mock_execute_round(*args, **kwargs):
            # Change source mid-review
            with open(subject_file, "w") as f:
                f.write("changed")
            return [{"provider": "claude", "vote": "approve", "candidate_hash": "synth1"},
                    {"provider": "codex", "vote": "approve", "candidate_hash": "synth1"},
                    {"provider": "agy", "vote": "approve", "candidate_hash": "synth1"}]
            
        # Temporarily patch internal executor just for timing simulation (safe TDD boundary usage for asyncio interleaving)
        import unittest.mock
        with unittest.mock.patch.object(council, "_execute_round", new=mock_execute_round):
            outcome = asyncio.run(council.review(req))
            self.assertTrue(outcome.source_changed)
            self.assertEqual(outcome.status, "UNRESOLVED")

    def test_provider_adapters(self):
        from provider_adapters import ClaudeAdapter, CodexAdapter, AgyAdapter, LMStudioAdapter, FakeReviewerAdapter
        
        claude = ClaudeAdapter("claude-sonnet-5", "high")
        self.assertEqual(claude.model, "claude-sonnet-5")
        
        codex = CodexAdapter("gpt-5.6-sol", "high")
        self.assertEqual(codex.executable, "codex")
        
        agy = AgyAdapter("gemini-3.6-flash", "high")
        self.assertEqual(agy.provider_id, "agy")
        
        lm = LMStudioAdapter("qwen3-coder-30b", "high")
        self.assertEqual(lm.provider_id, "lm-studio")
        
        fake = FakeReviewerAdapter("fake", [{"provider": "fake", "vote": "approve"}])
        res = asyncio.run(fake.review("env", 1, 10))
        self.assertEqual(res["vote"], "approve")

if __name__ == '__main__':
    unittest.main()
