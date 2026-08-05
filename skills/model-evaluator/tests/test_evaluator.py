import unittest
from unittest.mock import patch, MagicMock, AsyncMock
import asyncio
from pathlib import Path
import tempfile
import sys
import os

# Add scripts directory to path to import evaluator
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'scripts')))
from evaluator import Evaluator

class TestEvaluator(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.benchmarks_dir = Path(self.temp_dir.name)
        
        # Create a dummy test file
        with open(self.benchmarks_dir / "low.yaml", "w") as f:
            f.write("- id: test1\n  prompt: test\n  expected_format: test\n")
            
    def tearDown(self):
        self.temp_dir.cleanup()

    @patch("evaluator.storage.log_run")
    @patch("evaluator.litellm.acompletion")
    def test_evaluator_public_interface(self, mock_acompletion, mock_log_run):
        # Mock streaming response using simple objects
        class MockDelta:
            content = "test response"
        class MockChoice:
            delta = MockDelta()
        class MockChunk:
            choices = [MockChoice()]
            
        def mock_stream_factory(*args, **kwargs):
            async def mock_stream():
                yield MockChunk()
            return mock_stream()
            
        mock_acompletion.side_effect = mock_stream_factory
        
        # The public interface must be run_suite
        evaluator = Evaluator(benchmarks_dir=str(self.benchmarks_dir))
        
        # Mock the config generation to prevent file writes in test
        with patch("router_config_generator.generate_config"):
            results = evaluator.run_suite(output="dummy.md")
            
        self.assertIsInstance(results, list)
        self.assertGreater(len(results), 0)
        
        # Verify result structure
        res = next((r for r in results if r.model == "gemini/gemini-1.5-flash"), None)
        self.assertIsNotNone(res, "Gemini flash model result not found")
        if not res.success:
            print(f"Result object: {res}")
        self.assertEqual(res.tier, "low")
        self.assertTrue(res.success) # expected format "test" is in "test response"
        self.assertEqual(res.score, 100.0)

if __name__ == '__main__':
    unittest.main()
