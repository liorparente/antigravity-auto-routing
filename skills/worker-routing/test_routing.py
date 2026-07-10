#!/usr/bin/env python3
"""Unit and integration tests for routing_check.py and routing-audit.sh.

Run with:
    python3 -m unittest skills/worker-routing/test_routing.py -v
or, from this directory:
    python3 test_routing.py
"""
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent
FIXTURES_DIR = SKILL_DIR / "tests" / "fixtures"
ROUTING_CHECK = SKILL_DIR / "routing_check.py"
ROUTING_AUDIT = SKILL_DIR / "routing-audit.sh"

spec = importlib.util.spec_from_file_location("routing_check", ROUTING_CHECK)
routing_check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(routing_check)


def run_check(*args):
    return subprocess.run(
        [sys.executable, str(ROUTING_CHECK), *args],
        capture_output=True,
        text=True,
    )


class RoutingCheckUnitTests(unittest.TestCase):
    """Exercises routing_check.py's helper functions directly."""

    def setUp(self):
        self.config = routing_check.load_config()

    def test_load_patterns_includes_known_workers(self):
        patterns = routing_check.load_patterns(self.config)
        self.assertIn("codex", patterns)
        self.assertIn("claude -p", patterns)
        self.assertNotIn("py", patterns)  # code_extensions must not leak in

    def test_load_code_extensions_matches_config(self):
        extensions = routing_check.load_code_extensions(self.config)
        self.assertIn("py", extensions)
        self.assertIn("sh", extensions)

    def test_worker_pattern_ignores_substrings(self):
        patterns = routing_check.load_patterns(self.config)
        worker_pattern = re.compile(r"\b(?:" + "|".join(re.escape(p) for p in patterns) + r")\b")
        self.assertIsNone(worker_pattern.search("recodexing and codexes are not real words"))
        self.assertIsNone(worker_pattern.search("agynostic and geministic are also fake words"))

    def test_worker_pattern_matches_whole_word_mention(self):
        patterns = routing_check.load_patterns(self.config)
        worker_pattern = re.compile(r"\b(?:" + "|".join(re.escape(p) for p in patterns) + r")\b")
        self.assertIsNotNone(worker_pattern.search("I ran `codex exec \"fix bug\"` earlier"))

    def test_check_log_missing_file_prints_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "does-not-exist.txt"
            result = run_check(str(missing))
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout.strip(), "0")

    def test_no_args_prints_zero_and_exits_clean(self):
        result = run_check()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "0")

    def test_regex_flag_prints_worker_alternation(self):
        result = run_check("--regex")
        self.assertEqual(result.returncode, 0)
        pattern = result.stdout.strip()
        self.assertTrue(re.search(pattern, "ran codex exec here"))
        self.assertFalse(re.search(pattern, "recodexing is not a match"))

    def test_extensions_regex_flag_prints_extension_alternation(self):
        result = run_check("--extensions-regex")
        self.assertEqual(result.returncode, 0)
        pattern = result.stdout.strip()
        self.assertTrue(re.search(pattern, "src/app.py"))
        self.assertFalse(re.search(pattern, "docs/notes.md"))

    def test_missing_config_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            script_copy = Path(tmp) / "routing_check.py"
            shutil.copy(ROUTING_CHECK, script_copy)  # no routing-config.json alongside it
            result = subprocess.run(
                [sys.executable, str(script_copy), str(FIXTURES_DIR / "clean_log.txt")],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 2)


class RoutingCheckFixtureTests(unittest.TestCase):
    """Runs routing_check.py against the fixture logs in tests/fixtures/."""

    def test_clean_log_has_no_violations(self):
        result = run_check(str(FIXTURES_DIR / "clean_log.txt"))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "0")
        self.assertEqual(result.stderr.strip(), "")

    def test_direct_then_code_log_flags_one_violation(self):
        result = run_check(str(FIXTURES_DIR / "direct_then_code_log.txt"))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "1")
        self.assertIn("src/utils.py", result.stderr)

    def test_prose_boundary_log_flags_only_the_real_violation(self):
        # Block 1: [ROUTING: Direct] + code edit, surrounded only by prose that
        # contains substrings of worker names (recodexing, codexes, agynostic,
        # geministic) — these must NOT be mistaken for a worker call.
        # Block 2: [ROUTING: Direct] + code edit, but the prose genuinely
        # mentions a worker invocation (`codex exec ...`) — this must count
        # as routed and NOT be flagged.
        result = run_check(str(FIXTURES_DIR / "prose_boundary_log.txt"))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "1")
        self.assertIn("src/module.py", result.stderr)
        self.assertNotIn("src/patched.py", result.stderr)


class RoutingAuditIntegrationTests(unittest.TestCase):
    """Exercises routing-audit.sh end to end against a throwaway brain/ conversation dir."""

    def setUp(self):
        self.brain_dir = Path.home() / ".gemini" / "antigravity" / "brain"
        self.conv_id = f"routing-audit-test-{os.getpid()}"
        self.conv_dir = self.brain_dir / self.conv_id
        self.log_dir = self.conv_dir / ".system_generated" / "logs"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.log_dir / "overview.txt"

    def tearDown(self):
        shutil.rmtree(self.conv_dir, ignore_errors=True)

    def _run_audit(self):
        return subprocess.run(
            ["bash", str(ROUTING_AUDIT), self.conv_id],
            capture_output=True,
            text=True,
        )

    def test_clean_log_exits_zero(self):
        shutil.copy(FIXTURES_DIR / "clean_log.txt", self.log_path)
        result = self._run_audit()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("No violations detected", result.stdout)

    def test_direct_then_code_log_exits_nonzero(self):
        shutil.copy(FIXTURES_DIR / "direct_then_code_log.txt", self.log_path)
        result = self._run_audit()
        self.assertEqual(result.returncode, 1)
        self.assertIn("VIOLATION", result.stdout)
        self.assertIn("[Direct] → code edit violations: 1", result.stdout)

    def test_prose_boundary_log_exits_nonzero_with_one_violation(self):
        shutil.copy(FIXTURES_DIR / "prose_boundary_log.txt", self.log_path)
        result = self._run_audit()
        self.assertEqual(result.returncode, 1)
        self.assertIn("[Direct] → code edit violations: 1", result.stdout)

    def test_missing_log_fails_closed(self):
        shutil.rmtree(self.conv_dir, ignore_errors=True)
        result = self._run_audit()
        self.assertEqual(result.returncode, 1)
        self.assertIn("No log found", result.stdout)


if __name__ == "__main__":
    unittest.main()
