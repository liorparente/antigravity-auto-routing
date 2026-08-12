#!/usr/bin/env python3
"""Unit and integration tests for routing_check.py, routing-audit.sh, and
the install.sh / uninstall.sh protocol.md single-sourcing.

Run with:
    python3 -m unittest skills/worker-routing/test_routing.py -v
or, from this directory:
    python3 test_routing.py
"""
from __future__ import annotations

import dataclasses
import hashlib
import hmac
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from typing import TYPE_CHECKING
from unittest import mock

if TYPE_CHECKING:
    from collections.abc import Iterator

    # For type annotations only — at runtime `advisory_consultation` is the
    # dynamically loaded module object below, whose attributes mypy cannot
    # resolve inside annotations.
    from advisory_consultation import CanaryFixture, IsFamilyReachable

SKILL_DIR = Path(__file__).resolve().parent
REPO_ROOT = SKILL_DIR.parent.parent
FIXTURES_DIR = SKILL_DIR / "tests" / "fixtures"
ROUTING_CHECK = SKILL_DIR / "routing_check.py"
ROUTING_AUDIT = SKILL_DIR / "routing-audit.sh"
SKILL_MD = SKILL_DIR / "SKILL.md"
PROTOCOL_MD = SKILL_DIR / "protocol.md"
INSTALL_SH = REPO_ROOT / "install.sh"
UNINSTALL_SH = REPO_ROOT / "uninstall.sh"

# Same versionless sentinel markers install.sh/uninstall.sh write/look for.
PROTOCOL_START = "# === ANTIGRAVITY WORKER ROUTING PROTOCOL START ==="
PROTOCOL_END = "# === ANTIGRAVITY WORKER ROUTING PROTOCOL END ==="

spec = importlib.util.spec_from_file_location("routing_check", ROUTING_CHECK)
assert spec is not None and spec.loader is not None
routing_check = importlib.util.module_from_spec(spec)
sys.modules["routing_check"] = routing_check
spec.loader.exec_module(routing_check)

agent_council_spec = importlib.util.spec_from_file_location(
    "agent_council", SKILL_DIR / "agent_council.py"
)
assert agent_council_spec is not None and agent_council_spec.loader is not None
agent_council = importlib.util.module_from_spec(agent_council_spec)
sys.modules["agent_council"] = agent_council
agent_council_spec.loader.exec_module(agent_council)

advisory_consultation_spec = importlib.util.spec_from_file_location(
    "advisory_consultation", SKILL_DIR / "advisory_consultation.py"
)
assert advisory_consultation_spec is not None and advisory_consultation_spec.loader is not None
advisory_consultation = importlib.util.module_from_spec(advisory_consultation_spec)
sys.modules["advisory_consultation"] = advisory_consultation
advisory_consultation_spec.loader.exec_module(advisory_consultation)


def run_check(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ROUTING_CHECK), *args],
        capture_output=True,
        check=False,
        text=True,
    )


def assert_metrics(
    test_case: unittest.TestCase,
    stdout: str,
    total_writes: int,
    code_writes: int,
    routing_declarations: int,
    worker_calls: int,
    violations: int,
) -> None:
    test_case.assertIn(f"{'Total file write tool calls:':<33} {total_writes}", stdout)
    test_case.assertIn(f"{'Writes to source code files:':<33} {code_writes}", stdout)
    test_case.assertIn(f"{'ROUTING declarations found:':<33} {routing_declarations}", stdout)
    test_case.assertIn(f"{'Worker CLI calls found:':<33} {worker_calls}", stdout)
    test_case.assertIn(f"{'Unrouted code edit violations:':<33} {violations}", stdout)


class RoutingCheckUnitTests(unittest.TestCase):
    """Exercises routing_check.py's helper functions directly."""

    def setUp(self) -> None:
        self.config = routing_check.load_config()

    def test_load_patterns_includes_known_workers(self) -> None:
        patterns = routing_check.load_patterns(self.config)
        self.assertIn("codex exec", patterns)
        self.assertIn("codex review", patterns)
        self.assertIn("claude -p", patterns)
        self.assertNotIn("py", patterns)  # code_extensions must not leak in
        self.assertNotIn("safe_commands", patterns)  # safe_commands must not leak in
        self.assertNotIn("gemini -p", patterns)  # gemini -p must be removed
        # The "orchestrator" role (bare "claude -p" / "codex" patterns) was
        # removed so those bare invocations no longer register as worker
        # calls on their own.
        self.assertNotIn("orchestrator", self.config)

    def test_load_code_extensions_matches_config(self) -> None:
        extensions = routing_check.load_code_extensions(self.config)
        self.assertIn("py", extensions)
        self.assertIn("sh", extensions)

    def test_security_context_is_immutable_and_compute_metrics_accepts_it(self) -> None:
        context = routing_check.SecurityContext.create(secret=b"test-secret")
        with self.assertRaises(AttributeError):
            context.secret = b"replacement-secret"  # type: ignore[misc]

    def test_worker_pattern_ignores_substrings(self) -> None:
        patterns = routing_check.load_patterns(self.config)
        worker_pattern = re.compile(r"\b(?:" + "|".join(re.escape(p) for p in patterns) + r")\b")
        self.assertIsNone(worker_pattern.search("recodexing and codexes are not real words"))
        self.assertIsNone(worker_pattern.search("agynostic and geministic are also fake words"))

    def test_worker_pattern_matches_whole_word_mention(self) -> None:
        patterns = routing_check.load_patterns(self.config)
        worker_pattern = re.compile(r"\b(?:" + "|".join(re.escape(p) for p in patterns) + r")\b")
        self.assertIsNotNone(worker_pattern.search("I ran `codex exec \"fix bug\"` earlier"))

    def test_check_log_missing_file_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "does-not-exist.txt"
            result = run_check(str(missing))
            self.assertEqual(result.returncode, 2)
            self.assertIn("No log found", result.stdout)

    def test_empty_log_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            empty_log = Path(tmp) / "empty.txt"
            empty_log.write_text("")
            result = run_check(str(empty_log))
            self.assertEqual(result.returncode, 2)

            whitespace_only_log = Path(tmp) / "whitespace.txt"
            whitespace_only_log.write_text("   \n\n\t\n")
            result = run_check(str(whitespace_only_log))
            self.assertEqual(result.returncode, 2)

    def test_parser_out_of_sync_fails_closed(self) -> None:
        # If the raw log text mentions a write tool or a [ROUTING:] label
        # but the parser recovered zero of the corresponding metric, the
        # parser is out of sync with the log format — fail closed instead
        # of silently reporting a clean audit.
        with tempfile.TemporaryDirectory() as tmp:
            mismatched = Path(tmp) / "mismatched.txt"
            mismatched.write_text(
                "Step 1: [ROUTING: Direct — reason: mention only]\n"
                "I intend to call write_to_file eventually but never issue "
                "the actual tool call in the expected format.\n"
            )
            result = run_check(str(mismatched))
            self.assertEqual(result.returncode, 2)
            self.assertIn("Parser out of sync", result.stdout)

    def test_no_args_fails_closed_with_usage(self) -> None:
        result = run_check()
        self.assertEqual(result.returncode, 2)
        self.assertIn("Usage:", result.stderr)

    def test_missing_config_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            script_copy = Path(tmp) / "routing_check.py"
            shutil.copy(ROUTING_CHECK, script_copy)  # no routing-config.json alongside it
            result = subprocess.run(
                [sys.executable, str(script_copy), str(FIXTURES_DIR / "clean_log.txt")],
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertEqual(result.returncode, 2)

    def test_non_direct_label_still_violates(self) -> None:
        # An unrouted code write is a violation regardless of the step's
        # [ROUTING:] label — a non-"Direct" label (e.g. a worker was
        # declared but never actually invoked in this step) must be flagged
        # exactly the same as [ROUTING: Direct].
        step = routing_check.Step(
            1, "[ROUTING: heavy_doer — complexity: Medium — reason: implement feature]"
        )
        step.writes.append("src/feature.py")
        safe_patterns = routing_check.load_safe_patterns(self.config)
        metrics = routing_check.compute_metrics(
            [step], ["py"], ["claude -p"], safe_patterns,
            security_ctx=routing_check.SecurityContext.create(),
        )
        self.assertEqual(metrics["violations"], [(1, ["src/feature.py"])])

    def test_safe_commands_allowlist_matches_expected(self) -> None:
        # Every safe_commands pattern in routing-config.json must actually
        # allow the kind of read-only diagnostic command it was written for
        # — none of these should ever surface as an unrouted_mutation.
        safe_patterns = routing_check.load_safe_patterns(self.config)
        step = routing_check.Step(1, "[ROUTING: Direct — reason: read-only diagnostics]")
        step.commands = [
            "ls -la",
            "cat README.md",
            "grep -rn TODO src/",
            "rg TODO src/",
            "git status",
            "git log --oneline -5",
            "curl -s http://127.0.0.1:1234/api/v0/models",
            "jq '.version' package.json",
            "which python3",
            "echo hello",
            "pwd",
            "find . -name '*.py'",
            "python3 -m unittest skills/worker-routing/test_routing.py -v",
            "lsof -i :9222",
            " lsof -i ",
        ]
        metrics = routing_check.compute_metrics(
            [step], ["py"], [], safe_patterns,
            security_ctx=routing_check.SecurityContext.create(),
        )
        self.assertEqual(metrics["violations"], [])

    def test_unrouted_mutation_fails_strict_and_warns(self) -> None:
        # A command that is neither a worker invocation nor a recognized
        # safe command (e.g. a jq/echo shell redirect that mutates state
        # directly) must be flagged as an unrouted mutation violation, in
        # both plain and --strict modes — violations always fail, they are
        # never downgraded to a mere warning.
        result = run_check(str(FIXTURES_DIR / "unrouted_mutation_log.txt"))
        self.assertEqual(result.returncode, 1)
        assert_metrics(self, result.stdout, total_writes=0, code_writes=0,
                       routing_declarations=2, worker_calls=0, violations=2)
        self.assertIn("VIOLATION", result.stdout)
        self.assertIn("Step 1: unrouted code edit detected", result.stderr)
        self.assertIn("Step 2: unrouted code edit detected", result.stderr)

        strict_result = run_check("--strict", str(FIXTURES_DIR / "unrouted_mutation_log.txt"))
        self.assertEqual(strict_result.returncode, 1)
        self.assertIn("VIOLATION", strict_result.stdout)

    def test_substring_matching_does_not_count_as_delegation(self) -> None:
        # is_worker_invocation must check that the command *starts with* a
        # worker pattern after stripping env assignments/wrappers — a
        # worker's name mentioned mid-command (e.g. inside an echo) is not
        # an actual delegation.
        patterns = routing_check.load_patterns(self.config)
        self.assertFalse(routing_check.is_worker_invocation("echo codex exec", patterns))
        self.assertFalse(routing_check.is_worker_invocation("echo claude -p", patterns))
        self.assertTrue(routing_check.is_worker_invocation('codex exec "fix bug"', patterns))
        self.assertTrue(
            routing_check.is_worker_invocation(
                'IN_WORKER_ROUTING=true script -q /dev/null codex exec "fix bug"', patterns
            )
        )

    def test_step_from_dict_reads_antigravity_shape(self) -> None:
        # Antigravity's own conversation logs nest tool name under `name`,
        # arguments under `args.TargetFile`/`args.CommandLine` (sometimes
        # wrapped in literal double quotes), and carry no dedicated
        # `routing` key — the [ROUTING:] declaration must be recovered from
        # a sibling step's free-form `content` field instead.
        step = routing_check._step_from_dict(
            1,
            {
                "content": "[ROUTING: Direct — reason: quick fix]\n\nDone.",
                "tool_calls": [
                    {"name": "replace_file_content", "args": {"TargetFile": '"src/app.py"'}},
                    {"name": "run_command", "args": {"CommandLine": '"codex exec fix-bug"'}},
                ],
            },
        )
        self.assertEqual(step.routing, "[ROUTING: Direct — reason: quick fix]")
        self.assertEqual(step.writes, ["src/app.py"])
        self.assertEqual(step.commands, ["codex exec fix-bug"])


class RoutingCheckFixtureTests(unittest.TestCase):
    """Runs routing_check.py against the fixture logs in tests/fixtures/,
    covering plain-text and JSON Lines formats."""

    def test_clean_log_has_no_violations(self) -> None:
        result = run_check(str(FIXTURES_DIR / "clean_log.txt"))
        self.assertEqual(result.returncode, 0)
        assert_metrics(self, result.stdout, total_writes=2, code_writes=1,
                       routing_declarations=3, worker_calls=1, violations=0)
        self.assertIn("No violations detected", result.stdout)
        self.assertEqual(result.stderr.strip(), "")

    def test_direct_then_code_log_flags_one_violation(self) -> None:
        result = run_check(str(FIXTURES_DIR / "direct_then_code_log.txt"))
        self.assertEqual(result.returncode, 1)
        assert_metrics(self, result.stdout, total_writes=2, code_writes=2,
                       routing_declarations=2, worker_calls=1, violations=1)
        self.assertIn("src/utils.py", result.stderr)

    def test_prose_boundary_log_flags_both_direct_steps(self) -> None:
        # Block 1: [ROUTING: Direct] + code edit, surrounded only by prose that
        # contains substrings of worker names (recodexing, codexes, agynostic,
        # geministic) — these must NOT be mistaken for a worker call.
        # Block 2: [ROUTING: Direct] + code edit, where the prose *mentions* a
        # worker invocation (`codex exec ...`) but there is no actual
        # `run_command` tool call — a prose mention must NOT count as routing,
        # so this must be flagged too.
        result = run_check(str(FIXTURES_DIR / "prose_boundary_log.txt"))
        self.assertEqual(result.returncode, 1)
        assert_metrics(self, result.stdout, total_writes=2, code_writes=2,
                       routing_declarations=2, worker_calls=0, violations=2)
        self.assertIn("src/module.py", result.stderr)
        self.assertIn("src/patched.py", result.stderr)

    def test_step_boundary_log_does_not_leak_across_steps(self) -> None:
        # Step 1 has a code edit and no worker call of its own. Step 2's
        # routing declaration and its real `codex exec` tool call happen to
        # sit on the same line, which used to fall inside the old 3-line
        # lookahead window from step 1 — incorrectly clearing step 1's
        # violation. Step-scoped parsing must still flag step 1.
        result = run_check(str(FIXTURES_DIR / "step_boundary_log.txt"))
        self.assertEqual(result.returncode, 1)
        assert_metrics(self, result.stdout, total_writes=1, code_writes=1,
                       routing_declarations=2, worker_calls=1, violations=1)
        self.assertIn("src/leaky.py", result.stderr)

    def test_extension_edge_cases_are_not_false_positives(self) -> None:
        # index.html must not match `.h`, package.json must not match `.js`,
        # and build/cache.pyc must not match `.py` — exact-suffix matching
        # must exclude all three from "code writes" and violations.
        result = run_check(str(FIXTURES_DIR / "extension_edge_cases_log.txt"))
        self.assertEqual(result.returncode, 0)
        assert_metrics(self, result.stdout, total_writes=3, code_writes=0,
                       routing_declarations=3, worker_calls=0, violations=0)
        self.assertIn("No violations detected", result.stdout)
        self.assertEqual(result.stderr.strip(), "")

    def test_jsonl_log_format_is_parsed(self) -> None:
        result = run_check(str(FIXTURES_DIR / "direct_then_code_log.jsonl"))
        self.assertEqual(result.returncode, 1)
        assert_metrics(self, result.stdout, total_writes=2, code_writes=2,
                       routing_declarations=2, worker_calls=1, violations=1)
        self.assertIn("src/utils.py", result.stderr)

    def test_real_overview_log_flags_unrouted_shell_mutations(self) -> None:
        # Antigravity's actual overview.txt shape: JSON Lines wearing a
        # .txt extension, tool calls nested under `name`/`args`, and
        # [ROUTING:] declarations embedded in a separate step's `content`.
        # None of its writes touch a code_extensions file, but two of its
        # run_command calls mutate config files via shell redirection
        # (`jq ... > tmp && mv tmp file`) without going through a worker —
        # exactly what is_command_safe/unrouted_mutation detection exists
        # to catch, so both must be flagged.
        result = run_check(str(FIXTURES_DIR / "real_overview_log.txt"))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        assert_metrics(self, result.stdout, total_writes=1, code_writes=0,
                       routing_declarations=3, worker_calls=0, violations=2)
        self.assertIn("VIOLATION", result.stdout)

    def test_warning_only_log_warns_without_violation(self) -> None:
        result = run_check(str(FIXTURES_DIR / "warning_only_log.txt"))
        self.assertEqual(result.returncode, 0)
        assert_metrics(self, result.stdout, total_writes=2, code_writes=2,
                       routing_declarations=1, worker_calls=1, violations=0)
        self.assertIn("WARNING", result.stdout)

    def test_strict_mode_fails_on_warnings(self) -> None:
        result = run_check("--strict", str(FIXTURES_DIR / "warning_only_log.txt"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("WARNING", result.stdout)

    def test_strict_mode_does_not_fail_clean_log(self) -> None:
        result = run_check("--strict", str(FIXTURES_DIR / "clean_log.txt"))
        self.assertEqual(result.returncode, 0)


class RoutingAuditIntegrationTests(unittest.TestCase):
    """Exercises routing-audit.sh end to end against a throwaway brain/ conversation dir."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.home_dir = Path(self.tmp_dir.name)
        self.brain_dir = self.home_dir / ".gemini" / "antigravity" / "brain"
        self.conv_id = f"routing-audit-test-{os.getpid()}"
        self.conv_dir = self.brain_dir / self.conv_id
        self.log_dir = self.conv_dir / ".system_generated" / "logs"
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def _run_audit(self, *args: str) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ)
        env["HOME"] = str(self.home_dir)
        return subprocess.run(
            ["bash", str(ROUTING_AUDIT), *args, self.conv_id],
            capture_output=True,
            check=False,
            text=True,
            env=env,
        )

    def test_clean_log_exits_zero(self) -> None:
        shutil.copy(FIXTURES_DIR / "clean_log.txt", self.log_dir / "overview.txt")
        result = self._run_audit()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("No violations detected", result.stdout)

    def test_direct_then_code_log_exits_nonzero(self) -> None:
        shutil.copy(FIXTURES_DIR / "direct_then_code_log.txt", self.log_dir / "overview.txt")
        result = self._run_audit()
        self.assertEqual(result.returncode, 1)
        self.assertIn("VIOLATION", result.stdout)
        self.assertIn(f"{'Unrouted code edit violations:':<33} 1", result.stdout)

    def test_prose_boundary_log_exits_nonzero_with_two_violations(self) -> None:
        shutil.copy(FIXTURES_DIR / "prose_boundary_log.txt", self.log_dir / "overview.txt")
        result = self._run_audit()
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"{'Unrouted code edit violations:':<33} 2", result.stdout)

    def test_missing_log_fails_closed_with_exit_2(self) -> None:
        shutil.rmtree(self.conv_dir, ignore_errors=True)
        result = self._run_audit()
        self.assertEqual(result.returncode, 2)
        self.assertIn("No log found", result.stdout)

    def test_transcript_jsonl_is_auto_detected_when_overview_missing(self) -> None:
        shutil.copy(FIXTURES_DIR / "direct_then_code_log.jsonl", self.log_dir / "transcript.jsonl")
        result = self._run_audit()
        self.assertEqual(result.returncode, 1)
        self.assertIn("transcript.jsonl", result.stdout)
        self.assertIn(f"{'Unrouted code edit violations:':<33} 1", result.stdout)

    def test_strict_flag_is_relayed_and_fails_on_warning(self) -> None:
        shutil.copy(FIXTURES_DIR / "warning_only_log.txt", self.log_dir / "overview.txt")
        result = self._run_audit("--strict")
        self.assertEqual(result.returncode, 1)
        self.assertIn("WARNING", result.stdout)


class ProtocolDocumentationTests(unittest.TestCase):
    """Locks the centralized Rule 4.7 guidance and its CLI examples."""

    def test_skill_centralizes_phase_bypass_guidance(self) -> None:
        skill_text = SKILL_MD.read_text()
        lifecycle_section = skill_text.split(
            "## 🔄 Task Lifecycle & Collaboration Pipeline", 1
        )[1].split("## 📊 Calibrated Effort Matrix", 1)[0]
        lifecycle_preamble, phase_bodies = lifecycle_section.split("### Phase 0:", 1)

        self.assertEqual(lifecycle_preamble.count("Rule 4.7"), 1)
        self.assertIn(
            "[Rule 4.7 in `protocol.md`](protocol.md#routing-behavior)",
            lifecycle_preamble,
        )
        self.assertNotIn("Rule 4.7", phase_bodies)
        self.assertNotIn("BypassSandbox", phase_bodies)

    def test_protocol_matrix_scopes_bypass_to_run_command(self) -> None:
        protocol_text = PROTOCOL_MD.read_text()
        matrix_section = protocol_text.split(
            "## 📊 Calibrated Complexity & Supported Model Matrix", 1
        )[1].split("## Routing Behavior", 1)[0]
        matrix_intro, matrix_table = matrix_section.split("| Complexity |", 1)
        matrix_table = "| Complexity |" + matrix_table
        command_markers = ("codex exec", "codex review", "claude -p", "agy -p")
        command_examples = [
            code_span
            for code_span in re.findall(r"`([^`\n]+)`", matrix_table)
            if any(marker in code_span for marker in command_markers)
        ]
        expected_commands = [
            ('IN_WORKER_ROUTING=true codex exec --model gpt-5.6-luna '
             '-c model_reasoning_effort="low" -s workspace-write "[WORKER-MODE: AGY-NESTED-EXEC] ..." < /dev/null'),
            ('IN_WORKER_ROUTING=true codex exec --model gpt-5.6-terra '
             '-c model_reasoning_effort="medium" -s workspace-write "[WORKER-MODE: AGY-NESTED-EXEC] ..." < /dev/null'),
            ('IN_WORKER_ROUTING=true claude -p --no-session-persistence --model claude-sonnet-5 '
             '--effort high --allow-dangerously-skip-permissions --permission-mode bypassPermissions '
             '"[WORKER-MODE: AGY-NESTED-EXEC] ..." < /dev/null'),
            ("IN_WORKER_ROUTING=true codex review --uncommitted -c sandbox_mode=\"workspace-write\" "
             '-c model="gpt-5.6-sol" -c model_reasoning_effort="high" "[WORKER-MODE: AGY-NESTED-EXEC] ..." < /dev/null'),
            'IN_WORKER_ROUTING=true agy -p "[WORKER-MODE: AGY-NESTED-EXEC] ..." < /dev/null',
        ]

        self.assertIn("**Execution requirement", matrix_intro)
        for requirement in (
            "run_command",
            "BypassSandbox: true",
            "Rule 4.7",
            "tool-call field",
            "not a worker CLI flag",
        ):
            with self.subTest(requirement=requirement):
                self.assertIn(requirement, matrix_intro)

        self.assertEqual(command_examples, expected_commands)
        for command in command_examples:
            with self.subTest(command=command):
                self.assertNotIn("BypassSandbox", command)
                self.assertNotRegex(command, r"(?i)--\S*bypass\S*sandbox")


class ProtocolSyncTests(unittest.TestCase):
    """Ensures install.sh/uninstall.sh single-source AGENTS.md, CLAUDE.md,
    and GEMINI.md
    from skills/worker-routing/protocol.md by injecting it between sentinel
    markers, preserving any other custom content already in those files.
    Sandboxed under a fake $HOME so the tests never touch the real
    ~/.gemini or ~/.codex."""

    def _run(self, script: Path, *args: str, home: str) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ)
        env["HOME"] = str(home)
        return subprocess.run(
            ["bash", str(script), *args],
            capture_output=True,
            check=False,
            text=True,
            env=env,
        )

    def test_install_sh_injects_exact_protocol_block_into_all_docs(self) -> None:
        with tempfile.TemporaryDirectory() as fake_home, tempfile.TemporaryDirectory() as target_dir:
            result = self._run(INSTALL_SH, target_dir, home=fake_home)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            protocol_text = PROTOCOL_MD.read_text()
            expected_block = f"\n\n{protocol_text}\n"
            docs = (
                Path(target_dir) / "AGENTS.md",
                Path(target_dir) / "CLAUDE.md",
                Path(fake_home) / ".gemini" / "GEMINI.md",
            )
            for doc in docs:
                with self.subTest(doc=doc):
                    self.assertTrue(doc.exists())
                    text = doc.read_text()
                    self.assertEqual(text.count(PROTOCOL_START), 1)
                    self.assertEqual(text.count(PROTOCOL_END), 1)
                    block = text.split(PROTOCOL_START, 1)[1].split(PROTOCOL_END, 1)[0]
                    self.assertEqual(block, expected_block)

    def test_install_sh_copies_protocol_md_to_skill_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as fake_home, tempfile.TemporaryDirectory() as target_dir:
            result = self._run(INSTALL_SH, target_dir, home=fake_home)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            protocol_text = PROTOCOL_MD.read_text()
            for installed_dir in (
                Path(fake_home) / ".gemini" / "config" / "skills" / "worker-routing",
                Path(fake_home) / ".codex" / "skills" / "worker-routing",
                Path(target_dir) / ".agents" / "skills" / "worker-routing",
                Path(target_dir) / ".agent" / "skills" / "worker-routing",
                Path(target_dir) / ".codex" / "skills" / "worker-routing",
            ):
                installed_protocol = installed_dir / "protocol.md"
                self.assertTrue(installed_protocol.exists(), installed_protocol)
                self.assertEqual(installed_protocol.read_text(), protocol_text)

    def test_install_sh_copies_protocol_to_claude_rules(self) -> None:
        with tempfile.TemporaryDirectory() as fake_home, tempfile.TemporaryDirectory() as target_dir:
            result = self._run(INSTALL_SH, target_dir, home=fake_home)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            claude_rule = Path(target_dir) / ".claude" / "rules" / "worker-routing.md"
            self.assertTrue(claude_rule.exists())
            self.assertEqual(claude_rule.read_text(), PROTOCOL_MD.read_text())

    def test_install_sh_aborts_on_unbalanced_markers_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as fake_home, tempfile.TemporaryDirectory() as target_dir:
            agents_md = Path(target_dir) / "AGENTS.md"
            original = f"pre-existing\n{PROTOCOL_START}\nsome content but no end marker\n"
            agents_md.write_text(original)

            result = self._run(INSTALL_SH, target_dir, home=fake_home)
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(agents_md.read_text(), original)
            self.assertIn(PROTOCOL_START, result.stderr)
            self.assertIn("no matching", result.stderr)
            self.assertFalse(
                (Path(target_dir) / ".agents" / "skills" / "worker-routing" / "protocol.md").exists()
            )

    def test_install_sh_preserves_custom_content_in_agents_and_claude(self) -> None:
        with tempfile.TemporaryDirectory() as fake_home, tempfile.TemporaryDirectory() as target_dir:
            agents_md = Path(target_dir) / "AGENTS.md"
            claude_md = Path(target_dir) / "CLAUDE.md"
            agents_md.write_text("# My AGENTS notes\nDo not touch this custom section.\n")
            claude_md.write_text("# My CLAUDE notes\nDo not touch this custom section either.\n")

            self._run(INSTALL_SH, target_dir, home=fake_home)

            self.assertIn("Do not touch this custom section.", agents_md.read_text())
            self.assertIn("Do not touch this custom section either.", claude_md.read_text())
            self.assertIn(PROTOCOL_START, agents_md.read_text())
            self.assertIn(PROTOCOL_START, claude_md.read_text())

            # Re-running install must not duplicate the block or drop custom content.
            self._run(INSTALL_SH, target_dir, home=fake_home)
            agents_text = agents_md.read_text()
            self.assertEqual(agents_text.count(PROTOCOL_START), 1)
            self.assertIn("Do not touch this custom section.", agents_text)

    def test_install_sh_backs_up_pre_existing_docs_only_once(self) -> None:
        with tempfile.TemporaryDirectory() as fake_home, tempfile.TemporaryDirectory() as target_dir:
            agents_md = Path(target_dir) / "AGENTS.md"
            agents_md.write_text("pre-existing custom instructions\n")

            self._run(INSTALL_SH, target_dir, home=fake_home)
            backup = Path(target_dir) / "AGENTS.md.bak"
            self.assertTrue(backup.exists())
            self.assertEqual(backup.read_text(), "pre-existing custom instructions\n")

            # A second install must not clobber the original backup.
            agents_md.write_text("mutated between installs\n" + agents_md.read_text())
            self._run(INSTALL_SH, target_dir, home=fake_home)
            self.assertEqual(backup.read_text(), "pre-existing custom instructions\n")

    def test_uninstall_sh_removes_generated_docs(self) -> None:
        with tempfile.TemporaryDirectory() as fake_home, tempfile.TemporaryDirectory() as target_dir:
            self._run(INSTALL_SH, target_dir, home=fake_home)
            result = self._run(UNINSTALL_SH, target_dir, home=fake_home)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            self.assertFalse((Path(target_dir) / "AGENTS.md").exists())
            self.assertFalse((Path(target_dir) / "CLAUDE.md").exists())
            self.assertFalse((Path(target_dir) / ".codex" / "skills" / "worker-routing").exists())
            self.assertFalse((Path(fake_home) / ".codex" / "skills" / "worker-routing").exists())
            self.assertFalse((Path(fake_home) / ".gemini" / "config" / "skills" / "worker-routing").exists())
            self.assertFalse((Path(target_dir) / ".claude" / "rules" / "worker-routing.md").exists())
            self.assertFalse((Path(target_dir) / ".claude").exists())

    def test_uninstall_sh_does_not_touch_local_agents_dir(self) -> None:
        # uninstall.sh's TARGET_DIRS intentionally excludes the project-local
        # .agents/ directory (unlike install.sh's) — see uninstall.sh for
        # rationale. Its installed skill files are left in place.
        with tempfile.TemporaryDirectory() as fake_home, tempfile.TemporaryDirectory() as target_dir:
            self._run(INSTALL_SH, target_dir, home=fake_home)
            agents_skill_dir = Path(target_dir) / ".agents" / "skills" / "worker-routing"
            self.assertTrue((agents_skill_dir / "protocol.md").exists())

            result = self._run(UNINSTALL_SH, target_dir, home=fake_home)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            self.assertTrue((agents_skill_dir / "protocol.md").exists())

    def test_uninstall_sh_does_not_touch_local_agent_dir(self) -> None:
        # uninstall.sh's TARGET_DIRS intentionally excludes the project-local
        # .agent/ directory (unlike install.sh's). Its installed skill files are left in place.
        with tempfile.TemporaryDirectory() as fake_home, tempfile.TemporaryDirectory() as target_dir:
            self._run(INSTALL_SH, target_dir, home=fake_home)
            agent_skill_dir = Path(target_dir) / ".agent" / "skills" / "worker-routing"
            self.assertTrue((agent_skill_dir / "protocol.md").exists())

            result = self._run(UNINSTALL_SH, target_dir, home=fake_home)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            self.assertTrue((agent_skill_dir / "protocol.md").exists())

    def test_uninstall_sh_removes_protocol_md_but_preserves_other_content(self) -> None:
        with tempfile.TemporaryDirectory() as fake_home, tempfile.TemporaryDirectory() as target_dir:
            self._run(INSTALL_SH, target_dir, home=fake_home)
            codex_skill_dir = Path(target_dir) / ".codex" / "skills" / "worker-routing"
            self.assertTrue((codex_skill_dir / "protocol.md").exists())

            (codex_skill_dir / "my-custom-notes.txt").write_text("keep me\n")

            result = self._run(UNINSTALL_SH, target_dir, home=fake_home)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            self.assertFalse((codex_skill_dir / "protocol.md").exists())
            self.assertFalse((codex_skill_dir / "SKILL.md").exists())
            # The directory itself survives because it still holds
            # non-installer content — rmdir only succeeds on an empty dir.
            self.assertTrue(codex_skill_dir.exists())
            self.assertEqual((codex_skill_dir / "my-custom-notes.txt").read_text(), "keep me\n")

    def test_uninstall_sh_strips_block_but_preserves_custom_content(self) -> None:
        with tempfile.TemporaryDirectory() as fake_home, tempfile.TemporaryDirectory() as target_dir:
            agents_md = Path(target_dir) / "AGENTS.md"
            claude_md = Path(target_dir) / "CLAUDE.md"
            agents_md.write_text("# My custom notes\nKeep me around.\n")
            claude_md.write_text("# My other notes\nKeep me too.\n")

            self._run(INSTALL_SH, target_dir, home=fake_home)
            self.assertIn(PROTOCOL_START, agents_md.read_text())
            self.assertIn(PROTOCOL_START, claude_md.read_text())

            result = self._run(UNINSTALL_SH, target_dir, home=fake_home)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            self.assertTrue(agents_md.exists())
            self.assertTrue(claude_md.exists())
            agents_text = agents_md.read_text()
            claude_text = claude_md.read_text()
            self.assertIn("Keep me around.", agents_text)
            self.assertIn("Keep me too.", claude_text)
            self.assertNotIn(PROTOCOL_START, agents_text)
            self.assertNotIn(PROTOCOL_START, claude_text)



class GoldStandardV6NegativeTests(unittest.TestCase):
    """Matrix tests for Gold Standard v6 (CMD-01..CMD-05, DEC-01..DEC-04, LOG-01, INS-01)."""

    def setUp(self) -> None:
        self.config = routing_check.load_config()
        self.worker_patterns = routing_check.load_patterns(self.config)
        self.safe_patterns = routing_check.load_safe_patterns(self.config)
        self.code_extensions = routing_check.load_code_extensions(self.config)
        self.security_ctx = routing_check.SecurityContext.create()

    def test_cmd01_chained_command_with_unsafe_tail_flagged(self) -> None:
        step = routing_check.Step(1, "[ROUTING: codex — complexity: simple — effort: low — reason: test]")
        step.commands.append("IN_WORKER_ROUTING=true codex exec --model gpt-5.6-luna -c model_reasoning_effort=\"low\" && touch x.py")
        metrics = routing_check.compute_metrics(
            [step],
            self.code_extensions,
            self.worker_patterns,
            self.safe_patterns,
            self.security_ctx,
        )
        self.assertEqual(len(metrics["violations"]), 1)

    def test_cmd02_subshell_substitution_rejected(self) -> None:
        safe = routing_check.is_command_safe("echo $(id)", self.safe_patterns)
        self.assertFalse(safe)

    def test_cmd03_exact_token_boundary_enforced(self) -> None:
        is_worker = routing_check.is_worker_invocation("codex execfoo --test", self.worker_patterns)
        self.assertFalse(is_worker)

    def test_cmd04_nested_bash_c_unsafe_tail_flagged(self) -> None:
        step = routing_check.Step(1, "[ROUTING: codex — complexity: simple — effort: low — reason: test]")
        step.commands.append(
            "bash -c 'codex exec --model gpt-5.6-luna -c model_reasoning_effort=low && touch x.py'"
        )
        metrics = routing_check.compute_metrics(
            [step],
            self.code_extensions,
            self.worker_patterns,
            self.safe_patterns,
            self.security_ctx,
        )
        self.assertEqual(len(metrics["violations"]), 1)

    def test_dec01_model_declaration_drift_detected(self) -> None:
        step = routing_check.Step(1, "[ROUTING: Codex Sol — complexity: complex — effort: high — reason: test]")
        step.commands.append("IN_WORKER_ROUTING=true codex exec --model gpt-5.6-luna -c model_reasoning_effort=\"low\"")
        metrics = routing_check.compute_metrics(
            [step], self.code_extensions, self.worker_patterns, self.safe_patterns, self.security_ctx
        )
        self.assertEqual(len(metrics["violations"]), 1)

    def test_dec02_effort_declaration_drift_detected(self) -> None:
        step = routing_check.Step(1, "[ROUTING: codex — complexity: complex — effort: high — reason: test]")
        step.commands.append("IN_WORKER_ROUTING=true codex exec --model gpt-5.6-sol -c model_reasoning_effort=\"low\"")
        metrics = routing_check.compute_metrics(
            [step], self.code_extensions, self.worker_patterns, self.safe_patterns, self.security_ctx
        )
        self.assertEqual(len(metrics["violations"]), 1)

    def test_dec03_approved_agy_codex_review_and_calibrated_claude_are_valid(self) -> None:
        agy = routing_check.Step(
            1, "[ROUTING: Gemini 3.6 Flash — complexity: medium — effort: high — reason: research]"
        )
        agy.commands.append('agy -p "scan repository"')
        review = routing_check.Step(
            2, "[ROUTING: Codex Sol — complexity: complex — effort: high — reason: audit]"
        )
        review.commands.append("codex review --uncommitted")
        claude = routing_check.Step(
            3, "[ROUTING: Claude Sonnet 5 — complexity: medium — effort: high — reason: implement]"
        )
        claude.commands.append(
            'claude -p --no-session-persistence --model claude-sonnet-5 -c model_reasoning_effort="high" '
            '--allow-dangerously-skip-permissions "implement feature" < /dev/null'
        )
        metrics = routing_check.compute_metrics(
            [agy, review, claude],
            self.code_extensions,
            self.worker_patterns,
            self.safe_patterns,
            self.security_ctx,
        )
        self.assertEqual(metrics["violations"], [])

    def test_dec04_claude_v5_opus_and_sonnet_routing_steps_valid(self) -> None:
        opus_step = routing_check.Step(
            1, "[ROUTING: Claude Opus 5 — complexity: complex — effort: high — reason: plan architectural changes]"
        )
        opus_step.commands.append(
            'claude -p --no-session-persistence --model claude-opus-5 -c model_reasoning_effort="high" "plan feature" < /dev/null'
        )
        sonnet_step = routing_check.Step(
            2, "[ROUTING: Claude Sonnet 5 — complexity: medium — effort: high — reason: execute implementation]"
        )
        sonnet_step.commands.append(
            'claude -p --no-session-persistence --model claude-sonnet-5 -c model_reasoning_effort="high" "implement feature" < /dev/null'
        )
        metrics = routing_check.compute_metrics(
            [opus_step, sonnet_step],
            self.code_extensions,
            self.worker_patterns,
            self.safe_patterns,
            self.security_ctx,
        )
        self.assertEqual(metrics["violations"], [])

    def test_dec05_retired_claude_v4_6_model_declarations_rejected(self) -> None:
        step = routing_check.Step(
            1, "[ROUTING: Claude Sonnet 4.6 — complexity: medium — effort: high — reason: legacy model test]"
        )
        step.commands.append(
            'claude -p --no-session-persistence --model claude-sonnet-5 -c model_reasoning_effort="high" "implement feature" < /dev/null'
        )
        metrics = routing_check.compute_metrics(
            [step],
            self.code_extensions,
            self.worker_patterns,
            self.safe_patterns,
            self.security_ctx,
        )
        self.assertEqual(len(metrics["violations"]), 1)

    def test_agent_council_manifest_generation(self) -> None:
        agent_council_path = SKILL_DIR / "agent_council.py"
        spec = importlib.util.spec_from_file_location("agent_council", agent_council_path)
        assert spec is not None and spec.loader is not None
        agent_council_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(agent_council_mod)
        AgentCouncil = agent_council_mod.AgentCouncil

        with tempfile.TemporaryDirectory() as tmp:
            council = AgentCouncil(root_dir=Path(tmp))
            manifest = council.run(task="Refactor auth system", complexity="complex", effort="high", task_id="test-task-1")
            self.assertEqual(manifest["task_id"], "test-task-1")
            self.assertEqual(manifest["complexity"], "complex")
            self.assertEqual(manifest["effort"], "high")
            self.assertEqual(manifest.get("decision") or manifest.get("status"), "APPROVED")
            self.assertTrue(len(manifest["signature"]) > 0)
    def test_log01_unknown_write_tool_fails_closed(self) -> None:
        step = routing_check.Step(1, "[ROUTING: Direct — reason: test]")
        step.unknown_write_tools.append("apply_unreviewed_patch")
        metrics = routing_check.compute_metrics(
            [step], self.code_extensions, self.worker_patterns, self.safe_patterns, self.security_ctx
        )
        self.assertEqual(len(metrics["violations"]), 1)
        self.assertIn("LOG-01", metrics["violation_details"][0][1][0])

    def test_ins01_atomic_install_with_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as fake_home, tempfile.TemporaryDirectory() as target_dir:
            agents_md = Path(target_dir) / "AGENTS.md"
            agents_md.write_text("# Original AGENTS.md content\n")

            env = dict(os.environ)
            env["HOME"] = fake_home
            env["AUTO_ROUTING_FAIL_AFTER_WRITES"] = "3"

            res = subprocess.run(
                [str(INSTALL_SH), target_dir],
                capture_output=True,
                check=False,
                text=True,
                env=env,
            )
            self.assertNotEqual(res.returncode, 0)
            self.assertIn("AUTO_ROUTING_FAIL_AFTER_WRITES", res.stderr)
            self.assertEqual(agents_md.read_text(), "# Original AGENTS.md content\n")
            self.assertFalse(
                (Path(fake_home) / ".gemini" / "config" / "skills" / "worker-routing" / "SKILL.md").exists()
            )


class CalibrationSignatureTests(unittest.TestCase):
    """DEC-05 verification uses same-step evidence and never creates secrets."""

    def setUp(self) -> None:
        self.config = routing_check.load_config()
        self.worker_patterns = routing_check.load_patterns(self.config)
        self.safe_patterns = routing_check.load_safe_patterns(self.config)
        self.code_extensions = routing_check.load_code_extensions(self.config)

    @staticmethod
    def _manifest(secret: bytes, **overrides: str) -> dict[str, str]:
        manifest = {
            "task_id": "task-1",
            "task": "Refactor routing checks",
            "complexity": "medium",
            "effort": "high",
            "decision": "APPROVED",
            "nonce": "0123456789abcdef0123456789abcdef",
        }
        manifest.update(overrides)
        payload = json.dumps(
            manifest, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        manifest["signature"] = hmac.new(secret, payload, hashlib.sha256).hexdigest()
        return manifest

    def _metrics(
        self, step: object, root_dir: Path | None = None
    ) -> dict[str, object]:
        security_ctx = routing_check.SecurityContext.create(root_dir=root_dir)
        return routing_check.compute_metrics(
            [step],
            self.code_extensions,
            self.worker_patterns,
            self.safe_patterns,
            security_ctx=security_ctx,
        )

    def test_valid_header_and_six_field_manifest_pass(self) -> None:
        secret = b"isolated-test-secret"
        manifest = self._manifest(secret)
        step = routing_check.Step(1)
        step.calibration_headers.append(manifest["signature"])
        step.calibration_manifests.append(manifest)
        with mock.patch.dict(
            os.environ, {"AGY_CALIBRATION_SECRET": secret.decode()}, clear=True
        ):
            metrics = self._metrics(step)
        self.assertEqual(metrics["violations"], [])

    def test_tampered_manifest_records_exactly_one_dec05(self) -> None:
        secret = b"isolated-test-secret"
        manifest = self._manifest(secret)
        manifest["task"] = "Tampered task"
        step = routing_check.Step(1)
        step.calibration_headers.extend([manifest["signature"], "not-an-hmac"])
        step.calibration_manifests.append(manifest)
        with mock.patch.dict(
            os.environ, {"AGY_CALIBRATION_SECRET": secret.decode()}, clear=True
        ):
            metrics = self._metrics(step)
        violation_details = metrics["violation_details"]
        assert isinstance(violation_details, list)
        details = violation_details[0][1]
        assert isinstance(details, list)
        self.assertEqual(details.count(routing_check.CALIBRATION_VIOLATION), 1)

    def test_header_without_secret_or_evidence_fails_without_creating_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            step = routing_check.Step(1)
            step.calibration_headers.append("a" * 64)
            with mock.patch.dict(os.environ, {}, clear=True):
                metrics = self._metrics(step, root)
            violation_details = metrics["violation_details"]
            assert isinstance(violation_details, list)
            details = violation_details[0][1]
            assert isinstance(details, list)
            self.assertIn(
                routing_check.CALIBRATION_VIOLATION,
                details,
            )
            self.assertFalse(
                (root / ".ralph" / "cache" / "calibration.key").exists()
            )

    def test_signature_only_manifest_cannot_bypass_six_field_verification(self) -> None:
        steps = routing_check.parse_steps(
            "overview.txt", json.dumps({"signature": "a" * 64}) + "\n"
        )
        with mock.patch.dict(
            os.environ, {"AGY_CALIBRATION_SECRET": "isolated-test-secret"}, clear=True
        ):
            metrics = routing_check.compute_metrics(
                steps,
                self.code_extensions,
                self.worker_patterns,
                self.safe_patterns,
                security_ctx=routing_check.SecurityContext.create(),
            )
        self.assertEqual(metrics["calibration_markers"], 1)
        self.assertEqual(
            metrics["violation_details"][0][1],
            [routing_check.CALIBRATION_VIOLATION],
        )

    def test_project_key_is_used_when_environment_secret_is_absent(self) -> None:
        secret = b"project-local-secret"
        manifest = self._manifest(secret)
        step = routing_check.Step(1)
        step.calibration_manifests.append(manifest)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            key_file = root / ".ralph" / "cache" / "calibration.key"
            key_file.parent.mkdir(parents=True)
            key_file.write_bytes(secret)
            with mock.patch.dict(os.environ, {}, clear=True):
                metrics = self._metrics(step, root)
        self.assertEqual(metrics["violations"], [])

    def test_jsonl_parser_pairs_header_with_embedded_manifest(self) -> None:
        secret = b"parser-test-secret"
        manifest = self._manifest(secret)
        content = (
            f"[CALIBRATION: {manifest['signature']}]\n"
            + json.dumps(manifest, sort_keys=True)
        )
        steps = routing_check.parse_steps(
            "overview.txt", json.dumps({"content": content}) + "\n"
        )
        with mock.patch.dict(
            os.environ, {"AGY_CALIBRATION_SECRET": secret.decode()}, clear=True
        ):
            metrics = routing_check.compute_metrics(
                steps,
                self.code_extensions,
                self.worker_patterns,
                self.safe_patterns,
                security_ctx=routing_check.SecurityContext.create(),
            )
        self.assertEqual(metrics["violations"], [])
        self.assertEqual(len(steps[0].calibration_headers), 1)
        self.assertGreaterEqual(len(steps[0].calibration_manifests), 1)

    def test_agent_council_signature_matches_routing_check_contract(self) -> None:
        secret = "cross-module-secret"
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {"AGY_CALIBRATION_SECRET": secret}, clear=True
        ):
            manifest = agent_council.AgentCouncil(Path(tmp)).run(
                "Refactor routing checks", "medium", "high", "contract-task"
            )
            step = routing_check.Step(1)
            step.calibration_headers.append(manifest["signature"])
            step.calibration_manifests.append(manifest)
            metrics = self._metrics(step, Path(tmp))
        self.assertEqual(metrics["violations"], [])


class TransactionalWorkerCallTests(unittest.TestCase):
    def setUp(self) -> None:
        config = routing_check.load_config()
        self.worker_patterns = routing_check.load_patterns(config)
        self.safe_patterns = routing_check.load_safe_patterns(config)
        self.code_extensions = routing_check.load_code_extensions(config)
        self.security_ctx = routing_check.SecurityContext.create()

    def _metrics(self, *commands: str) -> dict[str, object]:
        step = routing_check.Step(
            1,
            "[ROUTING: codex — complexity: simple — effort: low — reason: test]",
        )
        step.commands.extend(commands)
        return routing_check.compute_metrics(
            [step],
            self.code_extensions,
            self.worker_patterns,
            self.safe_patterns,
            self.security_ctx,
        )

    def test_worker_segment_in_command_with_unsafe_tail_is_not_counted(self) -> None:
        metrics = self._metrics(
            "IN_WORKER_ROUTING=true codex exec --model gpt-5.6-luna "
            "-c model_reasoning_effort=low && touch x.py"
        )
        self.assertEqual(metrics["worker_calls"], 0)
        violations = metrics["violations"]
        assert isinstance(violations, list)
        self.assertEqual(len(violations), 1)

    def test_nested_shell_worker_with_unsafe_tail_is_not_counted(self) -> None:
        metrics = self._metrics(
            "bash -c 'codex exec --model gpt-5.6-luna "
            "-c model_reasoning_effort=low && touch x.py'"
        )
        self.assertEqual(metrics["worker_calls"], 0)
        violations = metrics["violations"]
        assert isinstance(violations, list)
        self.assertEqual(len(violations), 1)

    def test_newline_or_background_tail_is_not_counted(self) -> None:
        worker = (
            "codex exec --model gpt-5.6-luna "
            "-c model_reasoning_effort=low"
        )
        for separator in ("\n", " & "):
            with self.subTest(separator=repr(separator)):
                metrics = self._metrics(f"{worker}{separator}touch x.py")
                self.assertEqual(metrics["worker_calls"], 0)
                violations = metrics["violations"]
                assert isinstance(violations, list)
                self.assertEqual(len(violations), 1)

    def test_suppression_is_per_original_command(self) -> None:
        metrics = self._metrics(
            "codex exec --model gpt-5.6-luna "
            "-c model_reasoning_effort=low && touch x.py",
            "codex exec --model gpt-5.6-luna "
            "-c model_reasoning_effort=low && git status",
        )
        self.assertEqual(metrics["worker_calls"], 1)
        violations = metrics["violations"]
        assert isinstance(violations, list)
        self.assertEqual(len(violations), 1)


class AgentCouncilDebateTests(unittest.TestCase):
    def test_safe_task_reaches_consensus_after_two_distinct_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            manifest = agent_council.AgentCouncil(Path(tmp)).run(
                "Refactor routing checks", "complex", "high", "safe-task"
            )
        self.assertEqual(manifest["decision"], "APPROVED")
        self.assertEqual(
            [item["focus"] for item in manifest["debate_rounds"]],
            ["safety", "constraints"],
        )
        self.assertEqual(manifest["consensus_status"], "CONSENSUS_REACHED")
        self.assertEqual(manifest["consensus_round"], 2)

    def test_safety_constraint_disagreement_uses_third_round(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            manifest = agent_council.AgentCouncil(Path(tmp)).run(
                "Refactor; touch x.py", "medium", "high", "unsafe-task"
            )
        self.assertEqual(manifest["decision"], "REJECTED_LEXICAL")
        self.assertEqual(len(manifest["debate_rounds"]), 3)
        self.assertEqual(manifest["debate_rounds"][-1]["focus"], "adjudication")
        self.assertEqual(manifest["consensus_round"], 3)
        self.assertLessEqual(
            len(manifest["debate_rounds"]), agent_council.MAX_DEBATE_ROUNDS
        )

    def test_v1_or_tampered_cache_is_regenerated_as_strict_v2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            council = agent_council.AgentCouncil(root)
            council.cache_file.parent.mkdir(parents=True)
            council.cache_file.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "entries": {
                            "untrusted": {
                                "created_at": 1,
                                "manifest": {"signature": "0" * 64},
                            }
                        },
                    }
                )
            )
            first = council.run(
                "Refactor routing checks", "medium", "high", "cache-task"
            )
            cache = json.loads(council.cache_file.read_text())
            self.assertEqual(cache["version"], 2)

            entry = next(iter(cache["entries"].values()))
            entry["manifest"]["signature"] = "0" * 64
            council.cache_file.write_text(json.dumps(cache))
            second = council.run(
                "Refactor routing checks", "medium", "high", "cache-task"
            )
            rewritten = json.loads(council.cache_file.read_text())
            rewritten_entry = next(iter(rewritten["entries"].values()))
            rewritten_entry["manifest"]["debate_rounds"][0]["vote"] = "REJECT"
            council.cache_file.write_text(json.dumps(rewritten))
            third = council.run(
                "Refactor routing checks", "medium", "high", "cache-task"
            )
            debate_rewritten = json.loads(council.cache_file.read_text())
        self.assertEqual(first, second)
        self.assertEqual(first, third)
        self.assertNotEqual(
            next(iter(rewritten["entries"].values()))["manifest"]["signature"],
            "0" * 64,
        )
        self.assertEqual(
            next(iter(debate_rewritten["entries"].values()))["manifest"][
                "debate_rounds"
            ][0]["vote"],
            "APPROVE",
        )

    def test_environment_secret_precedes_workspace_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            key = root / ".ralph" / "cache" / "calibration.key"
            key.parent.mkdir(parents=True)
            key.write_bytes(b"workspace")
            with mock.patch.dict(
                os.environ, {"AGY_CALIBRATION_SECRET": "environment"}, clear=True
            ):
                self.assertEqual(
                    agent_council.get_calibration_secret(root), b"environment"
                )


class AgentCouncilSafetyDefectTests(unittest.TestCase):
    """Regression coverage for reviewed routing-safety defects in agent_council.py."""

    def test_sensitive_task_routes_to_local_model_when_available(self) -> None:
        """A sensitive task must stay on the local model, never be routed away.

        Prior to the fix, `is_sensitive=True` short-circuited to False —
        exactly inverted from the privacy rule ("Sensitive ... LM Studio
        ALWAYS (local model)").
        """
        self.assertEqual(
            agent_council.should_route_to_local_model(
                "complex", is_sensitive=True, is_local_available=True
            ),
            "route_local",
        )

    def test_non_sensitive_trivial_task_still_routes_local_when_available(self) -> None:
        self.assertEqual(
            agent_council.should_route_to_local_model(
                "trivial", is_sensitive=False, is_local_available=True
            ),
            "route_local",
        )

    def test_sensitive_task_not_routed_local_when_unavailable(self) -> None:
        self.assertEqual(
            agent_council.should_route_to_local_model(
                "complex", is_sensitive=True, is_local_available=False
            ),
            "halt",
        )

    def test_cloud_route_is_selected_without_local_model(self) -> None:
        self.assertEqual(
            agent_council.should_route_to_local_model(
                "simple", is_sensitive=False, is_local_available=False
            ),
            "route_cloud",
        )

    def test_evaluate_sensitivity_distinguishes_credentials(self) -> None:
        self.assertEqual(
            agent_council.evaluate_sensitivity("[SENSITIVE] customer data"),
            (True, False),
        )
        self.assertEqual(
            agent_council.evaluate_sensitivity("rotate bearer token"),
            (True, True),
        )

    def test_run_uses_local_route_and_records_telemetry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            agent_council, "check_local_model_endpoint", return_value=True
        ):
            council = agent_council.AgentCouncil(Path(tmp))
            council.run("Refactor routing checks", "simple", task_id="local-route")
            telemetry = json.loads(council.telemetry_file.read_text().strip())
        self.assertEqual(telemetry["chosen_worker"], "route_local")
        self.assertEqual(telemetry["task_id"], "local-route")

    def test_run_fails_closed_for_sensitive_task_without_local_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            agent_council, "check_local_model_endpoint", return_value=False
        ):
            council = agent_council.AgentCouncil(Path(tmp))
            with self.assertRaises(agent_council.SensitiveTaskFallbackBlocked):
                council.run("rotate api_key", "simple", task_id="sensitive-halt")
            telemetry = json.loads(council.telemetry_file.read_text().strip())
            error_log = (council.root_dir / ".ralph" / "errors.log").read_text()
        self.assertEqual(telemetry["chosen_worker"], "halt")
        self.assertIn("Task ID: sensitive-halt", error_log)
        self.assertIn("Primary Worker: local_model", error_log)
        self.assertIn("local inference endpoint unavailable", error_log)
        self.assertIn("FAIL-CLOSED", error_log)

    def test_run_escalates_retry_effort_through_council_caller_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            agent_council, "check_local_model_endpoint", return_value=False
        ), mock.patch.object(
            agent_council,
            "escalate_routing_effort",
            return_value=("high", "claude_opus_5"),
        ) as escalate:
            manifest = agent_council.AgentCouncil(Path(tmp)).run(
                "Refactor routing checks",
                "simple",
                task_id="retry-escalation",
                attempts=2,
            )

        escalate.assert_called_once_with("simple", "medium", 2)
        self.assertEqual(manifest["effort"], "high")

    def test_sensitive_task_local_failure_never_falls_back_to_cloud(self) -> None:
        """Rule 3.5: sensitive tasks fail closed instead of escalating to a cloud worker."""
        with self.assertRaises(agent_council.SensitiveTaskFallbackBlocked):
            agent_council.record_local_model_failure(
                "task-1", "lmstudio", "connection refused", is_sensitive=True
            )

    def test_non_sensitive_task_still_falls_back_to_cloud_worker(self) -> None:
        self.assertEqual(
            agent_council.record_local_model_failure(
                "task-1", "lmstudio", "timeout", is_sensitive=False
            ),
            "codex_terra",
        )


class _RecordingInvoker:
    """A fake `invoke_worker` callable: scripted responses, recorded calls.

    A scripted entry that is an `Exception` instance is raised instead of
    returned, so a test can script a worker failure at a specific call.
    """

    def __init__(self, responses: list[str | Exception]) -> None:
        self.responses: list[str | Exception] = list(responses)
        self.calls: list[tuple[str, str, str]] = []

    def __call__(self, model: str, effort: str, prompt: str) -> str:
        self.calls.append((model, effort, prompt))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class _RoleKeyedInvoker:
    """A fake `invoke_worker` for panel-mode tests: scripted per role, per round.

    Spec 0003 ticket 05's role-distinguishing mechanism is the `model`
    argument each panel role is invoked with — the Planner, Critic A, and
    Critic B each get their own `model` value at the call site (see
    `run_advisory_consultation_debate`'s `planner_model`/`critic_a_model`/
    `critic_b_model` parameters) — so this fake keys its script by that same
    `model` string. `responses` maps a `model` value to the ordered queue of
    responses that role receives, one per round: the first call with a given
    `model` pops that role's round-1 entry, the second call pops round 2,
    and so on, which is what makes "Planner / Critic A / Critic B each get
    independently scripted responses" (spec 0003's Testing Decisions,
    "the fake is keyed by role and round") true of this fake specifically.
    An unscripted call for a `model` whose queue is empty raises
    `AssertionError` immediately rather than a confusing `IndexError`, so a
    test with too few scripted rounds fails at the exact call it under-
    scripted.
    """

    def __init__(self, responses: dict[str, list[str | Exception]]) -> None:
        self.responses: dict[str, list[str | Exception]] = {
            model: list(queue) for model, queue in responses.items()
        }
        self.calls: list[tuple[str, str, str]] = []

    def __call__(self, model: str, effort: str, prompt: str) -> str:
        self.calls.append((model, effort, prompt))
        queue = self.responses.get(model)
        if not queue:
            raise AssertionError(
                f"_RoleKeyedInvoker: no scripted response left for model {model!r} "
                f"(call {len(self.calls)}); scripted models were "
                f"{sorted(self.responses)!r}"
            )
        response = queue.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _approve(artifact_text: str, note: str = "Looks solid.") -> str:
    """Build a scripted Critic response that satisfies the VerdictContract's
    APPROVE path (spec 0003 ticket 02): rationale, then one verified quote —
    the entire `artifact_text` (always trivially verbatim-contained in
    itself), stated as `QUOTE: "<artifact_text>"` — then the verdict line
    last. `artifact_text` must be exactly the Planner's plan text the
    surrounding test scripts for that same round, since
    `_parse_critic_verdict` verifies the quote against it.

    Used throughout this file wherever a scripted Critic response must
    actually reach `outcome == "consensus"`. Tests that probe the
    VerdictContract's parsing rules themselves — the engagement-unit
    counting, quote verification, and bare-approval rejection — live in
    `VerdictContractParserTests` and build their response text by hand,
    per spec 0003's pinned exception for VerdictContract parse behavior.
    """
    return f'{note}\nQUOTE: "{artifact_text}"\nVERDICT: APPROVE'


def _revise(note: str) -> str:
    """Build a scripted Critic response that satisfies the VerdictContract's
    REVISE path. REVISE carries no engagement-unit requirement (spec 0003
    ticket 02) — only APPROVE does — so the rationale/objection text alone,
    with the verdict line last, is sufficient.
    """
    return f"{note}\nVERDICT: REVISE"


def _approve_fixture(
    fixture: CanaryFixture, note: str = "Looks solid."
) -> str:
    """Build a scripted Critic response that approves `fixture` under the
    VerdictContract, for canary tests (spec 0003 ticket 08).

    Unlike `_approve`, this quotes only `fixture.plan_text`'s first line
    rather than the whole fixture text. `_approve`'s single-line
    `QUOTE: "<artifact_text>"` construction relies on `artifact_text`
    containing no newline of its own — true for every other artifact_text
    in this file ("Planner's proposed plan.", etc.) but not for
    `CANARY_FIXTURES`, whose entries are deliberately multi-line, realistic
    plans. `_parse_critic_verdict` reads a QUOTE line per physical line
    (`critic_response.splitlines()`), so embedding a real newline inside
    the quoted text splits it across several lines, none of which end with
    the closing `"` on the same line as the opening one — the quote never
    verifies. A fixture's first line is still guaranteed to be a verbatim
    substring of the whole `plan_text`, which is all the VerdictContract's
    quote verification actually requires for `verified_quote_count >= 1`.
    """
    quotable_line = fixture.plan_text.splitlines()[0]
    return f'{note}\nQUOTE: "{quotable_line}"\nVERDICT: APPROVE'


def _reachable(*families: str) -> IsFamilyReachable:
    """Build a scripted `is_family_reachable` fake for `resolve_roster` and
    `run_advisory_consultation_debate`'s `reachability_check` parameter
    (spec 0003 ticket 07): reachable for exactly the named families,
    unreachable for everything else. This is that seam's offline fake, the
    same role `_RecordingInvoker`/`_RoleKeyedInvoker` play for
    `invoke_worker` — no real network or local-endpoint probe, ever, per
    this ticket's own testability requirement ("this must be testable
    offline with a fake, exactly like `invoke_worker` is injected today").
    `_reachable()` with no arguments is the "nothing is up" fake used to
    exercise `RosterResolutionError`.
    """
    allowed = set(families)
    return lambda family: family in allowed


class VerdictContractParserTests(unittest.TestCase):
    """Spec 0003 (CriticalDialogue) ticket 02: `_parse_critic_verdict` under
    the VerdictContract. This is the one class in this file permitted to
    assert on the exact textual shape of a Critic response — the QUOTE:/N.
    line syntax and the verdict line — per the spec's Testing Decisions
    pinned exception for VerdictContract parse behavior. Every other test in
    this file that needs a scripted APPROVE/REVISE response should go
    through `_approve`/`_revise` instead of hand-rolling the contract text,
    so those tests stay agnostic to exactly this syntax.

    Calls `advisory_consultation._parse_critic_verdict` directly rather than
    through the full debate loop: the loop is exercised end-to-end elsewhere
    (`AdvisoryConsultationTests`), and driving every one of ticket 02's
    acceptance criteria through a full Planner/Critic round trip would
    duplicate this file's slowest fixture for no additional coverage of the
    parser itself.
    """

    def test_rationale_quotes_and_objections_before_approve_parses_as_approved_with_counts(
        self,
    ) -> None:
        artifact_text = (
            "The system shall retry failed writes up to three times before "
            "surfacing an error to the caller."
        )
        critic_response = (
            "This is a reasonable first pass at the retry behaviour.\n"
            'QUOTE: "retry failed writes up to three times"\n'
            "1. The backoff strategy between retries is unspecified.\n"
            "2. Nothing says whether the operation must be idempotent.\n"
            "VERDICT: APPROVE"
        )

        result = advisory_consultation._parse_critic_verdict(
            critic_response, artifact_text
        )

        self.assertEqual(result.verdict, "approved")
        self.assertEqual(result.verified_quote_count, 1)
        self.assertEqual(result.objection_count, 2)

    def test_bare_approve_with_zero_engagement_units_parses_as_not_approved(
        self,
    ) -> None:
        result = advisory_consultation._parse_critic_verdict(
            "VERDICT: APPROVE", "The reviewed artifact text."
        )

        self.assertNotEqual(result.verdict, "approved")
        self.assertEqual(result.verified_quote_count, 0)
        self.assertEqual(result.objection_count, 0)

    def test_quote_absent_from_artifact_does_not_count_and_leaves_response_not_approved(
        self,
    ) -> None:
        artifact_text = "The system shall retry failed writes up to three times."
        critic_response = (
            "Looks fine to me.\n"
            'QUOTE: "this exact text never appears in the artifact"\n'
            "VERDICT: APPROVE"
        )

        result = advisory_consultation._parse_critic_verdict(
            critic_response, artifact_text
        )

        self.assertNotEqual(result.verdict, "approved")
        self.assertEqual(result.verified_quote_count, 0)
        self.assertEqual(result.objection_count, 0)

    def test_mixed_valid_and_invalid_quotes_only_the_verbatim_one_counts(
        self,
    ) -> None:
        artifact_text = "Ship the feature behind a flag."
        critic_response = (
            "Partial engagement.\n"
            'QUOTE: "Ship the feature behind a flag."\n'
            'QUOTE: "this text is fabricated and not in the artifact"\n'
            "VERDICT: APPROVE"
        )

        result = advisory_consultation._parse_critic_verdict(
            critic_response, artifact_text
        )

        self.assertEqual(result.verdict, "approved")
        self.assertEqual(result.verified_quote_count, 1)
        self.assertEqual(result.objection_count, 0)

    def test_zero_objections_is_valid_when_at_least_one_quote_verifies(self) -> None:
        artifact_text = "Ship the feature behind a flag."
        critic_response = (
            "Solid reasoning, nothing to add.\n"
            'QUOTE: "Ship the feature behind a flag."\n'
            "VERDICT: APPROVE"
        )

        result = advisory_consultation._parse_critic_verdict(
            critic_response, artifact_text
        )

        self.assertEqual(result.verdict, "approved")
        self.assertEqual(result.verified_quote_count, 1)
        self.assertEqual(result.objection_count, 0)

    def test_zero_quotes_and_zero_objections_parses_as_not_approved_even_with_rationale(
        self,
    ) -> None:
        artifact_text = "Ship the feature behind a flag."
        critic_response = (
            "I read this carefully and it seems fine overall, no notes.\n"
            "VERDICT: APPROVE"
        )

        result = advisory_consultation._parse_critic_verdict(
            critic_response, artifact_text
        )

        self.assertNotEqual(result.verdict, "approved")
        self.assertEqual(result.verified_quote_count, 0)
        self.assertEqual(result.objection_count, 0)

    def test_objections_alone_with_no_verified_quotes_do_not_earn_approval(self) -> None:
        """The asymmetric half of the rule: spec 0003's VerdictContract
        paragraph only licenses "zero objections is fine alongside a
        verified quote" — never the mirror "zero quotes is fine alongside
        objections". Objections are unverified free text a Critic could
        fabricate without reading anything; only a quote is mechanically
        checked against the artifact, so only a quote may unlock approval.
        Several numbered objections, with zero verified quotes, must still
        parse as not-approved.
        """
        artifact_text = "Ship the feature behind a flag."
        critic_response = (
            "No verbatim passage is worth quoting, but I have concerns.\n"
            "1. The flag's default state is not specified.\n"
            "2. Nothing says who owns the flag once it is removed.\n"
            "VERDICT: APPROVE"
        )

        result = advisory_consultation._parse_critic_verdict(
            critic_response, artifact_text
        )

        self.assertNotEqual(result.verdict, "approved")
        self.assertEqual(result.verified_quote_count, 0)
        self.assertEqual(result.objection_count, 2)

    def test_unparseable_response_with_no_recognizable_verdict_line_is_not_approved(
        self,
    ) -> None:
        for critic_response in (
            "",
            "   \n\n   ",
            "This plan looks fine to me, no verdict line at all.",
        ):
            with self.subTest(critic_response=repr(critic_response)):
                result = advisory_consultation._parse_critic_verdict(
                    critic_response, "The reviewed artifact text."
                )

                self.assertEqual(result.verdict, "unparseable")
                self.assertNotEqual(result.verdict, "approved")

    def test_revise_still_works_with_rationale_and_objections_before_it(self) -> None:
        artifact_text = "Ship the feature behind a flag."
        critic_response = (
            "This needs another pass before it is ready.\n"
            "1. The rollback plan is missing.\n"
            "2. No mention of the flag's default state.\n"
            "VERDICT: REVISE"
        )

        result = advisory_consultation._parse_critic_verdict(
            critic_response, artifact_text
        )

        self.assertEqual(result.verdict, "revise")
        self.assertEqual(result.objection_count, 2)

    def test_result_is_a_verdict_contract_result_with_all_three_fields(self) -> None:
        result = advisory_consultation._parse_critic_verdict(
            "VERDICT: APPROVE", "irrelevant artifact text"
        )

        self.assertIsInstance(result, advisory_consultation.VerdictContractResult)
        self.assertEqual(
            dataclasses.fields(advisory_consultation.VerdictContractResult).__len__(),
            3,
        )


class AdvisoryConsultationTests(unittest.TestCase):
    """The Planner-Critic loop must never report fake consensus.

    The prior stub returned `consensus_reached=True` from an f-string with no
    model consulted — a lying success stub is more dangerous than a missing
    feature. These tests drive the real single-round loop through the one
    seam it exposes: the injected `invoke_worker` callable.
    """

    def test_consensus_on_round_one_writes_plan_and_reports_models_used(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            invoker = _RecordingInvoker(
                ["Planner's proposed plan.", _approve("Planner's proposed plan.")]
            )
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite",
                invoker,
                root_dir=root,
                planner_model="Test Planner",
                critic_model="Test Critic",
            )
            plan_path = root / "implementation_plan.md"
            self.assertTrue(plan_path.exists())
            self.assertEqual(plan_path.read_text(), "Planner's proposed plan.")

        self.assertEqual(len(invoker.calls), 2)
        self.assertEqual(result.rounds_run, 1)
        self.assertTrue(result.consensus_reached)
        self.assertEqual(result.final_plan, "Planner's proposed plan.")
        self.assertEqual(result.planner_model, "Test Planner")
        self.assertEqual(result.critic_model, "Test Critic")

    def test_non_approving_critic_yields_no_consensus_and_no_plan_file(self) -> None:
        """A Critic that withholds approval every round exhausts max_rounds honestly."""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            invoker = _RecordingInvoker(
                [
                    "Planner's proposed plan.",
                    _revise("Needs more detail."),
                    "Planner's revised plan.",
                    _revise("Still missing detail."),
                    "Planner's third plan.",
                    _revise("Not convinced."),
                ]
            )
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite", invoker, root_dir=root
            )
            self.assertFalse((root / "implementation_plan.md").exists())

        self.assertFalse(result.consensus_reached)
        self.assertEqual(result.rounds_run, 3)
        self.assertEqual(result.final_plan, "")
        self.assertEqual(len(invoker.calls), 6)

    def test_unparseable_critic_verdict_is_never_reported_as_consensus(self) -> None:
        """Verdict parsing is the subject here, not round count, so pin max_rounds=1."""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            for critic_response in (
                "",
                "\n\n",
                "This plan looks great, I fully approve of it.",
                "APPROVE",
            ):
                with self.subTest(critic_response=repr(critic_response)):
                    invoker = _RecordingInvoker(
                        ["Planner's proposed plan.", critic_response]
                    )
                    result = advisory_consultation.run_advisory_consultation_debate(
                        "Plan the auth rewrite", invoker, root_dir=root, max_rounds=1
                    )
                    self.assertFalse(result.consensus_reached)
                    self.assertFalse((root / "implementation_plan.md").exists())

    def test_artifacts_land_under_injected_root_and_real_repo_is_untouched(self) -> None:
        repo_plan = REPO_ROOT / "implementation_plan.md"
        repo_transcript = REPO_ROOT / ".scratch" / "planning_debate.md"
        repo_telemetry = REPO_ROOT / ".ralph" / "routing_telemetry.jsonl"
        before = {
            path: (path.read_text(), path.stat().st_mtime) if path.exists() else None
            for path in (repo_plan, repo_transcript, repo_telemetry)
        }

        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            invoker = _RecordingInvoker(
                ["Planner's proposed plan.", _approve("Planner's proposed plan.")]
            )
            advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite", invoker, root_dir=root
            )
            self.assertTrue((root / "implementation_plan.md").exists())
            self.assertTrue((root / ".scratch" / "planning_debate.md").exists())
            self.assertTrue((root / ".ralph" / "routing_telemetry.jsonl").exists())

        after = {
            path: (path.read_text(), path.stat().st_mtime) if path.exists() else None
            for path in (repo_plan, repo_transcript, repo_telemetry)
        }
        self.assertEqual(before, after)

    def test_ralph_directory_contains_nothing_but_the_telemetry_file(self) -> None:
        """Ticket 06: telemetry now lands at `.ralph/routing_telemetry.jsonl`, and
        that is the ONLY thing a consultation ever creates under `.ralph` —
        asserted as an allowlist of the whole directory listing, not a
        denylist of the two names (`decisions`, `cache`) ticket 06 happened
        to predict a collision with. A denylist stops catching new writes
        the moment anything else starts touching the directory — it would
        not have caught `agent_council.py`'s own `.ralph/errors.log`, which
        this whole-directory allowlist does."""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            invoker = _RecordingInvoker(
                ["Planner's proposed plan.", _approve("Planner's proposed plan.")]
            )
            advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite", invoker, root_dir=root
            )
            self.assertEqual(
                sorted(p.name for p in (root / ".ralph").iterdir()),
                ["routing_telemetry.jsonl"],
            )

    def test_both_prompts_carry_worker_mode_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            invoker = _RecordingInvoker(
                ["Planner's proposed plan.", _approve("Planner's proposed plan.")]
            )
            advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite", invoker, root_dir=root
            )

        self.assertEqual(len(invoker.calls), 2)
        for _model, _effort, prompt in invoker.calls:
            self.assertIn("[WORKER-MODE: AGY-NESTED-EXEC]", prompt)

    def test_rejection_sends_the_critics_objection_back_to_the_planner(self) -> None:
        """A rejection must drive a second Planner call that actually holds the critique."""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            invoker = _RecordingInvoker(
                [
                    "Planner's first plan.",
                    _revise("Missing a rollback strategy."),
                    "Planner's revised plan.",
                    _approve("Planner's revised plan.", "Rollback strategy addressed."),
                ]
            )
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite", invoker, root_dir=root
            )

        self.assertEqual(len(invoker.calls), 4)
        second_planner_prompt = invoker.calls[2][2]
        self.assertIn("Missing a rollback strategy.", second_planner_prompt)
        self.assertIn("Planner's first plan.", second_planner_prompt)
        self.assertTrue(result.consensus_reached)
        self.assertEqual(result.rounds_run, 2)
        self.assertEqual(result.final_plan, "Planner's revised plan.")

    def test_consensus_on_third_exchange_reports_three_rounds_and_writes_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            invoker = _RecordingInvoker(
                [
                    "Planner's first plan.",
                    _revise("Needs more detail."),
                    "Planner's second plan.",
                    _revise("Still thin."),
                    "Planner's third plan.",
                    _approve("Planner's third plan.", "This works."),
                ]
            )
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite", invoker, root_dir=root
            )
            plan_path = root / "implementation_plan.md"
            self.assertTrue(plan_path.exists())
            self.assertEqual(plan_path.read_text(), "Planner's third plan.")

        self.assertEqual(len(invoker.calls), 6)
        self.assertTrue(result.consensus_reached)
        self.assertEqual(result.rounds_run, 3)
        self.assertEqual(result.final_plan, "Planner's third plan.")

    def test_result_retains_each_rounds_exchange_in_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            invoker = _RecordingInvoker(
                [
                    "Planner's first plan.",
                    _revise("Needs more detail."),
                    "Planner's second plan.",
                    _approve("Planner's second plan.", "Good now."),
                ]
            )
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite", invoker, root_dir=root
            )

        self.assertEqual(len(result.rounds), 2)
        self.assertEqual(result.rounds[0].planner_proposal, "Planner's first plan.")
        self.assertEqual(
            result.rounds[0].critic_response, _revise("Needs more detail.")
        )
        self.assertEqual(result.rounds[1].planner_proposal, "Planner's second plan.")
        self.assertEqual(
            result.rounds[1].critic_response,
            _approve("Planner's second plan.", "Good now."),
        )

    def test_critic_that_never_approves_produces_exactly_two_times_max_rounds_calls(
        self,
    ) -> None:
        """Criterion 4, strict: no path may over- or under-run the configured cap."""
        for max_rounds in (2, 4):
            with self.subTest(max_rounds=max_rounds):
                with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
                    os.environ, {}, clear=True
                ):
                    root = Path(tmp)
                    responses: list[str | Exception] = []
                    for i in range(max_rounds):
                        responses.append(f"Planner's plan #{i + 1}.")
                        responses.append(_revise(f"Still not good enough #{i + 1}."))
                    invoker = _RecordingInvoker(responses)
                    result = advisory_consultation.run_advisory_consultation_debate(
                        "Plan the auth rewrite",
                        invoker,
                        root_dir=root,
                        max_rounds=max_rounds,
                    )
                    self.assertFalse((root / "implementation_plan.md").exists())

                self.assertEqual(len(invoker.calls), 2 * max_rounds)
                self.assertEqual(result.rounds_run, max_rounds)
                self.assertFalse(result.consensus_reached)
                self.assertEqual(result.final_plan, "")

    def test_worker_mode_token_present_in_every_round_of_multi_round_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            invoker = _RecordingInvoker(
                [
                    "Planner's first plan.",
                    _revise("Needs more detail."),
                    "Planner's second plan.",
                    _revise("Still thin."),
                    "Planner's third plan.",
                    _approve("Planner's third plan.", "This works."),
                ]
            )
            advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite", invoker, root_dir=root
            )

        self.assertEqual(len(invoker.calls), 6)
        for _model, _effort, prompt in invoker.calls:
            self.assertIn("[WORKER-MODE: AGY-NESTED-EXEC]", prompt)

    def test_stalemate_after_round_cap_reports_no_consensus_and_no_winner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            invoker = _RecordingInvoker(
                [
                    "Planner's first plan.",
                    _revise("Needs more detail."),
                    "Planner's second plan.",
                    _revise("Still thin."),
                    "Planner's third plan.",
                    _revise("Not convinced."),
                ]
            )
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite", invoker, root_dir=root
            )
            self.assertFalse((root / "implementation_plan.md").exists())

        self.assertEqual(result.outcome, "stalemate")
        self.assertFalse(result.consensus_reached)
        self.assertEqual(result.rounds_run, 3)
        self.assertEqual(result.final_plan, "")

    def test_stalemate_carries_both_final_positions_and_three_resolution_options(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            invoker = _RecordingInvoker(
                [
                    "Planner's first plan.",
                    _revise("Needs more detail."),
                    "Planner's final plan.",
                    _revise("Still not convinced."),
                ]
            )
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite", invoker, root_dir=root, max_rounds=2
            )

        self.assertIsNotNone(result.stalemate)
        assert result.stalemate is not None
        self.assertEqual(result.stalemate.planner_position, "Planner's final plan.")
        self.assertEqual(
            result.stalemate.critic_position, _revise("Still not convinced.")
        )
        self.assertEqual(len(result.stalemate.options), 3)
        labels = {option.label for option in result.stalemate.options}
        self.assertEqual(
            labels,
            {
                "Approve Planner Architecture",
                "Approve Critic Architecture",
                "Escalate to Human Decision",
            },
        )

    def test_pre_existing_plan_file_is_removed_on_every_non_consensus_exit(self) -> None:
        """The defect carried out of ticket 02: a stale plan must not survive."""
        scenarios: dict[str, _RecordingInvoker] = {
            "stalemate": _RecordingInvoker(
                ["Planner's plan.", _revise("Not convinced.")]
            ),
            "unparseable": _RecordingInvoker(
                ["Planner's plan.", "This plan looks fine to me."]
            ),
            "worker_error": _RecordingInvoker([RuntimeError("worker unreachable")]),
        }
        for name, invoker in scenarios.items():
            with (
                self.subTest(scenario=name),
                tempfile.TemporaryDirectory() as tmp,
                mock.patch.dict(os.environ, {}, clear=True),
            ):
                root = Path(tmp)
                plan_path = root / "implementation_plan.md"
                plan_path.write_text("stale plan from an earlier run")

                result = advisory_consultation.run_advisory_consultation_debate(
                    "Plan the auth rewrite", invoker, root_dir=root, max_rounds=1
                )

                self.assertFalse(plan_path.exists())
                self.assertFalse(result.consensus_reached)

    def test_unparseable_verdict_halts_without_a_second_planner_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            invoker = _RecordingInvoker(
                ["Planner's plan.", "This plan looks fine to me."]
            )
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite", invoker, root_dir=root, max_rounds=3
            )

        self.assertEqual(result.outcome, "unparseable_verdict")
        self.assertFalse(result.consensus_reached)
        self.assertEqual(len(invoker.calls), 2)
        self.assertEqual(len(result.rounds), 1)
        self.assertEqual(
            result.rounds[0].critic_response, "This plan looks fine to me."
        )

    def test_raising_worker_halts_with_no_plan_and_visible_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            invoker = _RecordingInvoker([RuntimeError("worker unreachable")])
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite", invoker, root_dir=root
            )
            self.assertFalse((root / "implementation_plan.md").exists())

        self.assertEqual(result.outcome, "worker_error")
        self.assertFalse(result.consensus_reached)
        self.assertEqual(result.final_plan, "")
        self.assertIsNotNone(result.error)
        assert result.error is not None
        self.assertIn("worker unreachable", result.error)

    def test_raising_worker_after_a_completed_round_keeps_that_rounds_history(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            invoker = _RecordingInvoker(
                [
                    "Planner's first plan.",
                    _revise("Needs more detail."),
                    RuntimeError("worker unreachable"),
                ]
            )
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite", invoker, root_dir=root
            )

        self.assertEqual(result.outcome, "worker_error")
        self.assertEqual(result.rounds_run, 1)
        self.assertEqual(len(result.rounds), 1)
        self.assertEqual(result.rounds[0].planner_proposal, "Planner's first plan.")

    def test_neither_failure_path_reports_consensus(self) -> None:
        """Criterion 6: no failure path may report a consensus that was not granted."""
        scenarios: dict[str, _RecordingInvoker] = {
            "stalemate": _RecordingInvoker(
                ["Planner's plan.", _revise("Not convinced.")]
            ),
            "worker_error": _RecordingInvoker([RuntimeError("worker unreachable")]),
            "unparseable": _RecordingInvoker(
                ["Planner's plan.", "garbled response"]
            ),
        }
        for name, invoker in scenarios.items():
            with self.subTest(scenario=name):
                with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
                    os.environ, {}, clear=True
                ):
                    root = Path(tmp)
                    result = advisory_consultation.run_advisory_consultation_debate(
                        "Plan the auth rewrite", invoker, root_dir=root, max_rounds=1
                    )
                self.assertFalse(result.consensus_reached)
                self.assertNotEqual(result.outcome, "consensus")

    def test_non_positive_max_rounds_raises_value_error_without_invoking_worker(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            for bad_max_rounds in (0, -1):
                with self.subTest(max_rounds=bad_max_rounds):
                    invoker = _RecordingInvoker([])
                    with self.assertRaises(ValueError):
                        advisory_consultation.run_advisory_consultation_debate(
                            "Plan the auth rewrite",
                            invoker,
                            root_dir=root,
                            max_rounds=bad_max_rounds,
                        )
                    self.assertEqual(invoker.calls, [])

    def test_stale_plan_cleanup_failure_preserves_worker_error_message(self) -> None:
        """A cleanup failure must not mask the worker exception that caused it."""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            (root / "implementation_plan.md").mkdir()
            invoker = _RecordingInvoker([RuntimeError("worker unreachable")])
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite", invoker, root_dir=root
            )

        self.assertEqual(result.outcome, "worker_error")
        self.assertFalse(result.consensus_reached)
        self.assertIsNotNone(result.error)
        assert result.error is not None
        self.assertIn("worker unreachable", result.error)
        self.assertIn("implementation_plan.md", result.error)

    def test_stale_plan_cleanup_failure_on_unparseable_verdict_does_not_raise(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            (root / "implementation_plan.md").mkdir()
            invoker = _RecordingInvoker(["Planner's plan.", "garbled response"])
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite", invoker, root_dir=root, max_rounds=1
            )

        self.assertEqual(result.outcome, "unparseable_verdict")
        self.assertFalse(result.consensus_reached)
        self.assertIsNotNone(result.error)
        assert result.error is not None
        self.assertIn("implementation_plan.md", result.error)

    def test_tolerant_revise_forms_drive_a_real_revision_round(self) -> None:
        """A Critic that writes REVISE loosely must still get a second Planner call."""
        tolerated_revise_responses = (
            "VERDICT: REVISE",
            "VERDICT: REVISE.",
            "VERDICT: REVISE:",
            "VERDICT: REVISE - needs a rollback strategy",
            "VERDICT: REVISE — needs a rollback strategy",
            "verdict: revise (missing error handling)",
        )
        for critic_response in tolerated_revise_responses:
            with self.subTest(critic_response=critic_response):
                with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
                    os.environ, {}, clear=True
                ):
                    root = Path(tmp)
                    invoker = _RecordingInvoker(
                        [
                            "Planner's first plan.",
                            critic_response,
                            "Planner's revised plan.",
                            _approve("Planner's revised plan.", "Good now."),
                        ]
                    )
                    result = advisory_consultation.run_advisory_consultation_debate(
                        "Plan the auth rewrite", invoker, root_dir=root
                    )

                self.assertEqual(len(invoker.calls), 4)
                second_planner_prompt = invoker.calls[2][2]
                self.assertIn(critic_response, second_planner_prompt)
                self.assertTrue(result.consensus_reached)
                self.assertEqual(result.rounds_run, 2)

    def test_revise_near_misses_still_halt_as_unparseable(self) -> None:
        rejected_responses = (
            "VERDICT: REVISED PLAN ATTACHED",
            "VERDICT: REVISEMENT",
            "Looks like it needs revision",
            "   \n\n   ",
        )
        for critic_response in rejected_responses:
            with self.subTest(critic_response=repr(critic_response)):
                with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
                    os.environ, {}, clear=True
                ):
                    root = Path(tmp)
                    invoker = _RecordingInvoker(
                        ["Planner's proposed plan.", critic_response]
                    )
                    result = advisory_consultation.run_advisory_consultation_debate(
                        "Plan the auth rewrite", invoker, root_dir=root, max_rounds=3
                    )

                self.assertEqual(result.outcome, "unparseable_verdict")
                self.assertEqual(len(invoker.calls), 2)

    def test_near_miss_approve_and_revise_are_not_treated_symmetrically(self) -> None:
        """Pins the asymmetry: near-miss APPROVE never reaches consensus, but the
        matching near-miss REVISE forms still drive a revision round. A future
        refactor that "tidies up" the two branches into symmetry must fail this.
        """
        near_miss_approvals = (
            "VERDICT: APPROVE.",
            "VERDICT: APPROVE - looks good",
            "VERDICT: APPROVED",
        )
        for critic_response in near_miss_approvals:
            with self.subTest(critic_response=critic_response):
                with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
                    os.environ, {}, clear=True
                ):
                    root = Path(tmp)
                    invoker = _RecordingInvoker(
                        ["Planner's proposed plan.", critic_response]
                    )
                    result = advisory_consultation.run_advisory_consultation_debate(
                        "Plan the auth rewrite", invoker, root_dir=root, max_rounds=1
                    )
                    self.assertFalse((root / "implementation_plan.md").exists())

                self.assertFalse(result.consensus_reached)
                self.assertEqual(result.outcome, "unparseable_verdict")

        near_miss_revises = (
            "VERDICT: REVISE.",
            "VERDICT: REVISE - looks good",
        )
        for critic_response in near_miss_revises:
            with self.subTest(critic_response=critic_response):
                with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
                    os.environ, {}, clear=True
                ):
                    root = Path(tmp)
                    invoker = _RecordingInvoker(
                        [
                            "Planner's first plan.",
                            critic_response,
                            "Planner's revised plan.",
                            _approve("Planner's revised plan.", "Good now."),
                        ]
                    )
                    result = advisory_consultation.run_advisory_consultation_debate(
                        "Plan the auth rewrite", invoker, root_dir=root
                    )

                self.assertEqual(len(invoker.calls), 4)
                self.assertTrue(result.consensus_reached)

    def test_consensus_reached_is_derived_from_outcome_and_cannot_be_mutated(
        self,
    ) -> None:
        consensus_result = advisory_consultation.AdvisoryDebateResult(
            rounds_run=1, final_plan="A plan.", outcome="consensus"
        )
        stalemate_result = advisory_consultation.AdvisoryDebateResult(
            rounds_run=1, final_plan="", outcome="stalemate"
        )
        unparseable_result = advisory_consultation.AdvisoryDebateResult(
            rounds_run=1, final_plan="", outcome="unparseable_verdict"
        )
        worker_error_result = advisory_consultation.AdvisoryDebateResult(
            rounds_run=0, final_plan="", outcome="worker_error"
        )
        self.assertTrue(consensus_result.consensus_reached)
        self.assertFalse(stalemate_result.consensus_reached)
        self.assertFalse(unparseable_result.consensus_reached)
        self.assertFalse(worker_error_result.consensus_reached)

        with self.assertRaises(dataclasses.FrozenInstanceError):
            worker_error_result.consensus_reached = True  # type: ignore[misc]
        with self.assertRaises(dataclasses.FrozenInstanceError):
            worker_error_result.outcome = "consensus"  # type: ignore[misc]
        self.assertFalse(worker_error_result.consensus_reached)


class AdvisoryPanelTopologyTests(unittest.TestCase):
    """Spec 0003 (CriticalDialogue) ticket 05: Complex-tier plan-review and
    code-review occasions run a panel — one Planner, two independently
    invoked Critics — instead of the pair topology `AdvisoryConsultationTests`
    above exercises. Every test here drives the loop through the same public
    `run_advisory_consultation_debate` seam those tests use, with the new
    `complexity` keyword argument and the new `critic_a_model`/`critic_b_model`
    role slots. `_RoleKeyedInvoker` (defined above `_approve`) is what lets a
    single scripted run address the Planner, Critic A, and Critic B
    independently, per round — spec 0003's Testing Decisions: "the fake is
    keyed by role and round."
    """

    def test_complex_plan_review_both_critics_approve_round_one_reaches_consensus(
        self,
    ) -> None:
        """Criterion 1 and 2: Complex-tier plan-review invokes three workers,
        each independently addressable, and both Critics approving in round
        one is consensus."""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            plan = "Planner's proposed plan."
            invoker = _RoleKeyedInvoker(
                {
                    "Test Planner": [plan],
                    "Test Critic A": [_approve(plan, "Critic A: solid.")],
                    "Test Critic B": [_approve(plan, "Critic B: solid.")],
                }
            )
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite",
                invoker,
                root_dir=root,
                occasion="plan-review",
                complexity="complex",
                planner_model="Test Planner",
                critic_a_model="Test Critic A",
                critic_b_model="Test Critic B",
            )
            plan_path = root / "implementation_plan.md"
            self.assertTrue(plan_path.exists())
            self.assertEqual(plan_path.read_text(), plan)

        self.assertTrue(result.consensus_reached)
        self.assertEqual(result.rounds_run, 1)
        self.assertEqual(result.final_plan, plan)
        self.assertEqual(len(invoker.calls), 3)
        called_models = {model for model, _effort, _prompt in invoker.calls}
        self.assertEqual(
            called_models, {"Test Planner", "Test Critic A", "Test Critic B"}
        )

    def test_complex_code_review_also_runs_the_panel(self) -> None:
        """The panel topology is not plan-review-only: code-review at Complex
        tier is named in the same acceptance criterion."""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            plan = "Diff defense."
            invoker = _RoleKeyedInvoker(
                {
                    "Test Planner": [plan],
                    "Test Critic A": [_approve(plan)],
                    "Test Critic B": [_approve(plan)],
                }
            )
            result = advisory_consultation.run_advisory_consultation_debate(
                "Review the diff",
                invoker,
                root_dir=root,
                occasion="code-review",
                complexity="complex",
                planner_model="Test Planner",
                critic_a_model="Test Critic A",
                critic_b_model="Test Critic B",
            )

        self.assertTrue(result.consensus_reached)
        self.assertEqual(len(invoker.calls), 3)

    def test_split_verdict_is_not_consensus_and_triggers_a_second_round_with_both_critics_reinvoked(
        self,
    ) -> None:
        """Criterion 3: one Critic approving and the other objecting is not
        consensus; both Critics must be re-invoked in the following round,
        not just the one that objected."""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            first_plan = "Planner's first plan."
            second_plan = "Planner's revised plan."
            invoker = _RoleKeyedInvoker(
                {
                    "Test Planner": [first_plan, second_plan],
                    "Test Critic A": [
                        _approve(first_plan, "A: fine as-is."),
                        _approve(second_plan, "A: still fine."),
                    ],
                    "Test Critic B": [
                        _revise("B: needs a rollback plan."),
                        _approve(second_plan, "B: rollback addressed."),
                    ],
                }
            )
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite",
                invoker,
                root_dir=root,
                occasion="plan-review",
                complexity="complex",
                planner_model="Test Planner",
                critic_a_model="Test Critic A",
                critic_b_model="Test Critic B",
            )

        self.assertTrue(result.consensus_reached)
        self.assertEqual(result.rounds_run, 2)
        self.assertEqual(len(invoker.calls), 6)
        second_round_models = [model for model, _e, _p in invoker.calls[3:6]]
        self.assertEqual(
            second_round_models, ["Test Planner", "Test Critic A", "Test Critic B"]
        )
        second_planner_prompt = invoker.calls[3][2]
        self.assertIn("B: needs a rollback plan.", second_planner_prompt)

    def test_both_critics_reject_every_round_produces_stalemate_at_the_cap(
        self,
    ) -> None:
        """The other 'other combination': both Critics objecting every round
        must exhaust exactly `MAX_DEBATE_ROUNDS` rounds and end in a
        stalemate, never a fabricated consensus."""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            invoker = _RoleKeyedInvoker(
                {
                    "Test Planner": [f"Planner's plan #{i}." for i in range(1, 4)],
                    "Test Critic A": [
                        _revise(f"A: not yet #{i}.") for i in range(1, 4)
                    ],
                    "Test Critic B": [
                        _revise(f"B: not yet #{i}.") for i in range(1, 4)
                    ],
                }
            )
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite",
                invoker,
                root_dir=root,
                occasion="plan-review",
                complexity="complex",
                planner_model="Test Planner",
                critic_a_model="Test Critic A",
                critic_b_model="Test Critic B",
            )
            self.assertFalse((root / "implementation_plan.md").exists())

        self.assertEqual(result.outcome, "stalemate")
        self.assertFalse(result.consensus_reached)
        self.assertEqual(result.rounds_run, advisory_consultation.MAX_DEBATE_ROUNDS)
        self.assertEqual(result.rounds_run, 3)
        self.assertEqual(len(invoker.calls), 9)
        self.assertIsNotNone(result.stalemate)

    def test_panel_round_cap_is_exactly_three_never_four(self) -> None:
        """Criterion 5, strict: `MAX_DEBATE_ROUNDS` must not change for panel
        mode. Exactly 3 rounds' worth of responses (9 calls) are scripted
        per role; if the loop ever over-ran to a fourth round,
        `_RoleKeyedInvoker` would raise `AssertionError` on the exhausted
        queue rather than silently returning a stale response, so a 4-round
        run could never reach this test's assertions at all.
        """
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            invoker = _RoleKeyedInvoker(
                {
                    "Test Planner": [f"Plan #{i}." for i in range(1, 4)],
                    "Test Critic A": [f"A objects #{i}.\nVERDICT: REVISE" for i in range(1, 4)],
                    "Test Critic B": [f"B objects #{i}.\nVERDICT: REVISE" for i in range(1, 4)],
                }
            )
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite",
                invoker,
                root_dir=root,
                occasion="code-review",
                complexity="complex",
                planner_model="Test Planner",
                critic_a_model="Test Critic A",
                critic_b_model="Test Critic B",
            )

        self.assertEqual(result.rounds_run, 3)
        self.assertEqual(len(invoker.calls), 9)
        self.assertEqual(advisory_consultation.MAX_DEBATE_ROUNDS, 3)

    def test_non_complex_plan_review_stays_pair_mode_with_exactly_two_workers(
        self,
    ) -> None:
        """Criterion 4 (regression): plan-review/code-review at any
        complexity below Complex must keep invoking exactly two workers,
        completely unchanged from before this ticket."""
        for complexity in ("trivial", "simple", "medium"):
            with self.subTest(complexity=complexity):
                with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
                    os.environ, {}, clear=True
                ):
                    root = Path(tmp)
                    plan = "Planner's plan."
                    invoker = _RecordingInvoker([plan, _approve(plan)])
                    result = advisory_consultation.run_advisory_consultation_debate(
                        "Plan the auth rewrite",
                        invoker,
                        root_dir=root,
                        occasion="plan-review",
                        complexity=complexity,
                    )
                self.assertEqual(len(invoker.calls), 2)
                self.assertTrue(result.consensus_reached)

    def test_complex_ambiguity_and_post_mortem_stay_pair_mode_with_exactly_two_workers(
        self,
    ) -> None:
        """Criterion 4 (regression), the other half: Complex-tier occasions
        outside plan-review/code-review keep the pair topology completely
        unchanged — a panel is not simply 'whatever runs at Complex
        complexity.'"""
        for occasion in ("ambiguity", "post-mortem"):
            with self.subTest(occasion=occasion):
                with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
                    os.environ, {}, clear=True
                ):
                    root = Path(tmp)
                    plan = "Planner's plan."
                    invoker = _RecordingInvoker([plan, _approve(plan)])
                    result = advisory_consultation.run_advisory_consultation_debate(
                        "Plan the auth rewrite",
                        invoker,
                        root_dir=root,
                        occasion=occasion,
                        complexity="complex",
                    )
                self.assertEqual(len(invoker.calls), 2)
                self.assertTrue(result.consensus_reached)

    def test_default_complexity_never_selects_the_panel(self) -> None:
        """A call site that never mentions `complexity` (every pre-ticket-05
        call site, including every `AdvisoryConsultationTests` case above)
        must keep behaving exactly as before this parameter existed: pair
        mode, two workers."""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            plan = "Planner's plan."
            invoker = _RecordingInvoker([plan, _approve(plan)])
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite",
                invoker,
                root_dir=root,
                occasion="plan-review",
            )
        self.assertEqual(len(invoker.calls), 2)
        self.assertTrue(result.consensus_reached)

    def test_panel_round_stores_both_critics_responses(self) -> None:
        """`AdvisoryDebateRound.critic_b_response` (ticket 05) carries Critic
        B's response; `critic_response` still carries Critic A's, unrenamed."""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            plan = "Planner's plan."
            a_response = _approve(plan, "Critic A note.")
            b_response = _approve(plan, "Critic B note.")
            invoker = _RoleKeyedInvoker(
                {
                    "Test Planner": [plan],
                    "Test Critic A": [a_response],
                    "Test Critic B": [b_response],
                }
            )
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite",
                invoker,
                root_dir=root,
                occasion="plan-review",
                complexity="complex",
                planner_model="Test Planner",
                critic_a_model="Test Critic A",
                critic_b_model="Test Critic B",
            )

        self.assertEqual(len(result.rounds), 1)
        self.assertEqual(result.rounds[0].critic_response, a_response)
        self.assertEqual(result.rounds[0].critic_b_response, b_response)

    def test_pair_mode_round_leaves_critic_b_response_none(self) -> None:
        """The additive-field guarantee: a pair-mode round's new field is
        `None`, never populated by anything pair mode does."""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            plan = "Planner's plan."
            invoker = _RecordingInvoker([plan, _approve(plan)])
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite", invoker, root_dir=root
            )

        self.assertEqual(len(result.rounds), 1)
        self.assertIsNone(result.rounds[0].critic_b_response)

    def test_unparseable_verdict_from_either_critic_halts_the_panel_immediately(
        self,
    ) -> None:
        """A malformed response from either Critic must halt the panel the
        same way a single malformed response halts pair mode — never folded
        into 'the panel asked for a revision' as if it were a reasoned
        objection from whichever Critic actually engaged."""
        plan = "Planner's plan."
        scenarios: dict[str, dict[str, list[str | Exception]]] = {
            "critic_a_unparseable": {
                "Test Planner": [plan],
                "Test Critic A": ["no verdict line here"],
                "Test Critic B": [_approve(plan)],
            },
            "critic_b_unparseable": {
                "Test Planner": [plan],
                "Test Critic A": [_approve(plan)],
                "Test Critic B": ["no verdict line here"],
            },
            "both_unparseable": {
                "Test Planner": [plan],
                "Test Critic A": ["garbled"],
                "Test Critic B": ["also garbled"],
            },
        }
        for name, responses in scenarios.items():
            with self.subTest(scenario=name):
                with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
                    os.environ, {}, clear=True
                ):
                    root = Path(tmp)
                    invoker = _RoleKeyedInvoker(responses)
                    result = advisory_consultation.run_advisory_consultation_debate(
                        "Plan the auth rewrite",
                        invoker,
                        root_dir=root,
                        occasion="plan-review",
                        complexity="complex",
                        planner_model="Test Planner",
                        critic_a_model="Test Critic A",
                        critic_b_model="Test Critic B",
                    )
                    self.assertFalse((root / "implementation_plan.md").exists())
                self.assertEqual(result.outcome, "unparseable_verdict")
                self.assertEqual(len(invoker.calls), 3)

    def test_panel_worker_mode_token_present_in_every_role_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            plan = "Planner's plan."
            invoker = _RoleKeyedInvoker(
                {
                    "Test Planner": [plan],
                    "Test Critic A": [_approve(plan)],
                    "Test Critic B": [_approve(plan)],
                }
            )
            advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite",
                invoker,
                root_dir=root,
                occasion="plan-review",
                complexity="complex",
                planner_model="Test Planner",
                critic_a_model="Test Critic A",
                critic_b_model="Test Critic B",
            )

        self.assertEqual(len(invoker.calls), 3)
        for _model, _effort, prompt in invoker.calls:
            self.assertIn("[WORKER-MODE: AGY-NESTED-EXEC]", prompt)

    def test_transcript_renders_both_critics_for_a_panel_round(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            plan = "Planner's plan."
            invoker = _RoleKeyedInvoker(
                {
                    "Test Planner": [plan],
                    "Test Critic A": [_approve(plan, "Critic A verbatim note.")],
                    "Test Critic B": [_approve(plan, "Critic B verbatim note.")],
                }
            )
            advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite",
                invoker,
                root_dir=root,
                occasion="plan-review",
                complexity="complex",
                planner_model="Test Planner",
                critic_a_model="Test Critic A",
                critic_b_model="Test Critic B",
            )
            transcript = (root / ".scratch" / "planning_debate.md").read_text()

        self.assertIn("Critic A verbatim note.", transcript)
        self.assertIn("Critic B verbatim note.", transcript)
        self.assertIn("### Critic A", transcript)
        self.assertIn("### Critic B", transcript)

    def test_pair_mode_transcript_header_is_byte_identical_to_before_panel_mode(
        self,
    ) -> None:
        """Pins `_render_consultation_transcript`'s pair-mode output: adding
        the panel-mode branch must not change a single byte of what a
        pair-mode transcript renders."""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            plan = "Planner's plan."
            invoker = _RecordingInvoker([plan, _approve(plan)])
            advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite",
                invoker,
                root_dir=root,
                planner_model="Test Planner",
                critic_model="Test Critic",
            )
            transcript = (root / ".scratch" / "planning_debate.md").read_text()

        self.assertIn("### Critic (Test Critic)", transcript)
        self.assertNotIn("Critic A", transcript)
        self.assertNotIn("Critic B", transcript)


class AdvisoryPanelStalemateReportTests(unittest.TestCase):
    """Spec 0003 (CriticalDialogue) ticket 06: a panel stalemate report must
    carry three distinct final positions — the Planner's, Critic A's, and
    Critic B's — rather than the single folded/combined string
    `_combine_panel_critic_feedback` produced through ticket 05. Every test
    here drives the loop through the same public
    `run_advisory_consultation_debate` seam `AdvisoryPanelTopologyTests`
    uses, with scripted Critic A/Critic B responses that are textually
    different so a folded string and three separated fields are
    distinguishable by inspection.
    """

    def test_split_verdict_at_cap_stalemate_carries_planner_and_both_critics_distinct_positions(
        self,
    ) -> None:
        """Criterion 1: a split verdict (one Critic approves, one objects)
        every round through the cap produces a stalemate report carrying the
        Planner's final plan and both Critics' final positions, verbatim and
        un-concatenated."""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            plans = [f"Planner's plan #{i}." for i in range(1, 4)]
            invoker = _RoleKeyedInvoker(
                {
                    # `list(plans)` re-types the entry as this dict literal's
                    # `list[str | Exception]` value type without widening
                    # `plans` itself, which the surrounding test keeps as
                    # `list[str]`.
                    "Test Planner": list(plans),
                    "Test Critic A": [
                        _approve(plan, f"A approves plan #{i}.")
                        for i, plan in enumerate(plans, start=1)
                    ],
                    "Test Critic B": [
                        _revise(f"B still objects, round #{i}.") for i in range(1, 4)
                    ],
                }
            )
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite",
                invoker,
                root_dir=root,
                occasion="plan-review",
                complexity="complex",
                planner_model="Test Planner",
                critic_a_model="Test Critic A",
                critic_b_model="Test Critic B",
            )

        self.assertEqual(result.outcome, "stalemate")
        self.assertIsNotNone(result.stalemate)
        assert result.stalemate is not None
        self.assertEqual(result.stalemate.planner_position, plans[-1])
        self.assertIn("A approves plan #3.", result.stalemate.critic_position)
        self.assertIsNotNone(result.stalemate.critic_b_position)
        assert result.stalemate.critic_b_position is not None
        self.assertIn(
            "B still objects, round #3.", result.stalemate.critic_b_position
        )
        # Not folded: Critic A's text never leaks into Critic B's field or
        # vice versa, unlike the ticket-05 `_combine_panel_critic_feedback`
        # string this report replaces.
        self.assertNotIn("B still objects", result.stalemate.critic_position)
        self.assertNotIn("A approves", result.stalemate.critic_b_position)

    def test_both_critics_reject_at_cap_stalemate_carries_planner_and_both_critics_distinct_positions(
        self,
    ) -> None:
        """Criterion 2: both Critics rejecting every round through the cap
        also produces a stalemate report with all three positions present
        and distinguishable."""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            plans = [f"Planner's plan #{i}." for i in range(1, 4)]
            invoker = _RoleKeyedInvoker(
                {
                    # `list(plans)` re-types the entry as this dict literal's
                    # `list[str | Exception]` value type without widening
                    # `plans` itself, which the surrounding test keeps as
                    # `list[str]`.
                    "Test Planner": list(plans),
                    "Test Critic A": [
                        _revise(f"A objects, round #{i}.") for i in range(1, 4)
                    ],
                    "Test Critic B": [
                        _revise(f"B objects differently, round #{i}.")
                        for i in range(1, 4)
                    ],
                }
            )
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite",
                invoker,
                root_dir=root,
                occasion="code-review",
                complexity="complex",
                planner_model="Test Planner",
                critic_a_model="Test Critic A",
                critic_b_model="Test Critic B",
            )

        self.assertEqual(result.outcome, "stalemate")
        self.assertIsNotNone(result.stalemate)
        assert result.stalemate is not None
        self.assertEqual(result.stalemate.planner_position, plans[-1])
        self.assertIn("A objects, round #3.", result.stalemate.critic_position)
        assert result.stalemate.critic_b_position is not None
        self.assertIn(
            "B objects differently, round #3.", result.stalemate.critic_b_position
        )
        self.assertNotIn("B objects differently", result.stalemate.critic_position)
        self.assertNotIn("A objects,", result.stalemate.critic_b_position)

    def test_panel_stalemate_resolution_options_remain_three_and_never_pick_a_winner(
        self,
    ) -> None:
        """Criterion 3: the options stay exactly approve-Planner /
        approve-Critic(s) / escalate-to-human in shape, and the report
        carries no field capable of naming a winner."""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            plans = [f"Planner's plan #{i}." for i in range(1, 4)]
            invoker = _RoleKeyedInvoker(
                {
                    # `list(plans)` re-types the entry as this dict literal's
                    # `list[str | Exception]` value type without widening
                    # `plans` itself, which the surrounding test keeps as
                    # `list[str]`.
                    "Test Planner": list(plans),
                    "Test Critic A": [
                        _revise(f"A objects, round #{i}.") for i in range(1, 4)
                    ],
                    "Test Critic B": [
                        _revise(f"B objects, round #{i}.") for i in range(1, 4)
                    ],
                }
            )
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite",
                invoker,
                root_dir=root,
                occasion="plan-review",
                complexity="complex",
                planner_model="Test Planner",
                critic_a_model="Test Critic A",
                critic_b_model="Test Critic B",
            )

        assert result.stalemate is not None
        self.assertEqual(len(result.stalemate.options), 3)
        self.assertEqual(
            [option.id for option in result.stalemate.options], [1, 2, 3]
        )
        field_names = {field.name for field in dataclasses.fields(result.stalemate)}
        self.assertEqual(
            field_names,
            {"planner_position", "critic_position", "options", "critic_b_position"},
        )
        option_field_names = {
            field.name for field in dataclasses.fields(result.stalemate.options[0])
        }
        self.assertEqual(option_field_names, {"id", "label", "description"})

    def test_unparseable_critic_in_panel_never_produces_a_stalemate_report(
        self,
    ) -> None:
        """An unparseable verdict from either Critic halts the panel
        immediately (ticket 05's behavior) rather than going through
        `_build_stalemate_report` at all — confirmed unaffected by this
        ticket's changes."""
        plan = "Planner's plan."
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            invoker = _RoleKeyedInvoker(
                {
                    "Test Planner": [plan],
                    "Test Critic A": ["no verdict line here"],
                    "Test Critic B": [_approve(plan)],
                }
            )
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite",
                invoker,
                root_dir=root,
                occasion="plan-review",
                complexity="complex",
                planner_model="Test Planner",
                critic_a_model="Test Critic A",
                critic_b_model="Test Critic B",
            )

        self.assertEqual(result.outcome, "unparseable_verdict")
        self.assertIsNone(result.stalemate)

    def test_pair_mode_stalemate_report_leaves_critic_b_position_none(self) -> None:
        """The additive-field guarantee, mirrored from
        `AdvisoryDebateRound.critic_b_response`: a pair-mode stalemate's new
        field is `None`, never populated by anything pair mode does. Pair
        mode's own `test_stalemate_carries_both_final_positions_and_three_resolution_options`
        (in `AdvisoryConsultationTests`) already pins the two-voice shape
        byte-for-byte and is left completely unmodified by this ticket; this
        test adds only the one new-field assertion that test predates."""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            invoker = _RecordingInvoker(
                [
                    "Planner's first plan.",
                    _revise("Needs more detail."),
                    "Planner's final plan.",
                    _revise("Still not convinced."),
                ]
            )
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite", invoker, root_dir=root, max_rounds=2
            )

        assert result.stalemate is not None
        self.assertIsNone(result.stalemate.critic_b_position)


class ModelFamilyClassifierTests(unittest.TestCase):
    """Spec 0003 (CriticalDialogue) ticket 07: `classify_model_family` is
    the pure function every later piece of roster-resolution
    infrastructure is built on — "Family, not model, is the independence
    unit" (spec's Implementation Decisions paragraph of the same name)."""

    def test_claude_models_share_one_family(self) -> None:
        self.assertEqual(
            advisory_consultation.classify_model_family("Claude Opus 5 (Thinking)"),
            advisory_consultation.classify_model_family("Claude Fable 5"),
        )

    def test_codex_and_gpt_share_one_family(self) -> None:
        """The ticket's own brief: "gpt"/"codex" substrings both map to the
        Codex/GPT family — routing-config.json's own `critic` role block
        already lists "Codex 5.6 Sol" and "GPT-OSS 120B (Medium)" as
        interchangeable alternatives within one role."""
        self.assertEqual(
            advisory_consultation.classify_model_family("Codex 5.6 Sol"),
            advisory_consultation.classify_model_family("GPT-OSS 120B (Medium)"),
        )

    def test_gemini_models_share_one_family(self) -> None:
        self.assertEqual(
            advisory_consultation.classify_model_family("Gemini 3.6 Flash (High)"),
            advisory_consultation.classify_model_family("Gemini 3.1 Pro (High)"),
        )

    def test_the_four_cloud_families_are_pairwise_distinct(self) -> None:
        families = {
            advisory_consultation.classify_model_family("Claude Opus 5 (Thinking)"),
            advisory_consultation.classify_model_family("Codex 5.6 Sol"),
            advisory_consultation.classify_model_family("Gemini 3.6 Flash (High)"),
        }
        self.assertEqual(len(families), 3)

    def test_each_local_model_lineage_is_its_own_family(self) -> None:
        """The spec's own phrase: "each local model lineage counts as its
        own family" — two different local checkpoints must classify to two
        different families, and neither may collide with a cloud family."""
        gemma = advisory_consultation.classify_model_family("Gemma 4 E4B")
        qwen = advisory_consultation.classify_model_family("Qwen3-Coder-Next")
        self.assertNotEqual(gemma, qwen)
        cloud_families = {
            advisory_consultation.classify_model_family("Claude Opus 5 (Thinking)"),
            advisory_consultation.classify_model_family("Codex 5.6 Sol"),
            advisory_consultation.classify_model_family("Gemini 3.6 Flash (High)"),
        }
        self.assertNotIn(gemma, cloud_families)
        self.assertNotIn(qwen, cloud_families)

    def test_same_local_lineage_different_checkpoint_is_one_family(self) -> None:
        self.assertEqual(
            advisory_consultation.classify_model_family("Gemma 4 E4B"),
            advisory_consultation.classify_model_family("Gemma 2 9B"),
        )

    def test_classification_is_case_insensitive_for_cloud_families(self) -> None:
        self.assertEqual(
            advisory_consultation.classify_model_family("claude opus 5"),
            advisory_consultation.classify_model_family("CLAUDE OPUS 5"),
        )

    def test_digit_leading_local_model_names_do_not_collide_on_a_middle_fragment(
        self,
    ) -> None:
        """Regression for a code-review finding: `_LOCAL_LINEAGE_PATTERN`
        must anchor to the LEADING run of alphabetic characters (`.match`),
        not the first one found anywhere in the string (`.search`). Before
        this was anchored, two unrelated digit-led local model names could
        silently collide into the same family via a coincidental middle
        fragment — e.g. both matching "b" from "70B-Instruct" and
        "4B-Mixtral" — which would have defeated the exact independence
        guarantee `resolve_roster` exists to enforce. Neither fixture here
        starts with a letter, so neither hits the leading-alpha-run branch
        at all; both fall through to the full-lowered-name fallback
        instead, which keeps them distinct without merging on a fragment.
        """
        seventy_b = advisory_consultation.classify_model_family("70B-Instruct")
        four_b = advisory_consultation.classify_model_family("4B-Mixtral")
        self.assertNotEqual(seventy_b, four_b)
        cloud_families = {
            advisory_consultation.classify_model_family("Claude Opus 5 (Thinking)"),
            advisory_consultation.classify_model_family("Codex 5.6 Sol"),
            advisory_consultation.classify_model_family("Gemini 3.6 Flash (High)"),
        }
        self.assertNotIn(seventy_b, cloud_families)
        self.assertNotIn(four_b, cloud_families)

    def test_digit_leading_local_model_name_falls_back_to_full_lowered_name(
        self,
    ) -> None:
        """Locks in the exact fallback value for a name with no leading
        alphabetic run at all: the full lowercased, stripped name — not an
        exception, not `"unknown"` (that is reserved for a genuinely empty
        or all-punctuation name), and not a fragment `.search` would have
        found anywhere else in the string."""
        self.assertEqual(
            advisory_consultation.classify_model_family("70B-Instruct"),
            "70b-instruct",
        )


class RosterResolutionTests(unittest.TestCase):
    """Spec 0003 (CriticalDialogue) ticket 07: `resolve_roster` assigns a
    model to each role in a pair or panel topology, preferring a distinct
    family per role and degrading to family reuse only when the reachable
    families genuinely run out. Every test drives the public
    `resolve_roster` seam with a scripted `is_family_reachable` fake
    (`_reachable`, defined above `VerdictContractParserTests`) — no real
    reachability probe, ever.
    """

    def test_pair_roster_spans_two_distinct_families_when_reachable(self) -> None:
        """Criterion 1 (pair half): 2+ reachable families never repeat
        across roles."""
        roster = advisory_consultation.resolve_roster(
            "pair", is_family_reachable=_reachable("claude", "codex-gpt", "gemini")
        )
        families = {a.family for a in roster.assignments}
        self.assertEqual(len(roster.assignments), 2)
        self.assertEqual(len(families), 2)
        self.assertFalse(roster.degraded_independence)

    def test_panel_roster_spans_three_distinct_families_when_reachable(self) -> None:
        """Criterion 1 (panel half): 2+ (here 3) reachable families never
        repeat across roles."""
        roster = advisory_consultation.resolve_roster(
            "panel", is_family_reachable=_reachable("claude", "codex-gpt", "gemini")
        )
        families = {a.family for a in roster.assignments}
        self.assertEqual(len(roster.assignments), 3)
        self.assertEqual(len(families), 3)
        self.assertFalse(roster.degraded_independence)

    def test_unreachable_preferred_family_substitutes_next_in_chain_not_degraded(
        self,
    ) -> None:
        """Criterion 2: `critic_a`'s preferred family (codex-gpt, from
        "Codex 5.6 Sol") is unreachable; the resolver must move to the next
        family in `critic_a`'s own configured fallback chain rather than
        immediately reusing the Planner's family."""
        roster = advisory_consultation.resolve_roster(
            "pair", is_family_reachable=_reachable("claude", "gemini")
        )
        critic_a = next(a for a in roster.assignments if a.role == "critic_a")
        self.assertNotEqual(critic_a.family, "codex-gpt")
        self.assertEqual(critic_a.family, "gemini")
        self.assertFalse(roster.degraded_independence)

    def test_single_reachable_family_forces_reuse_and_flags_degraded_for_pair(
        self,
    ) -> None:
        """Criterion 3 (pair half): the fallback chain exhausted to one
        remaining family forces same-family and sets the marker."""
        roster = advisory_consultation.resolve_roster(
            "pair", is_family_reachable=_reachable("claude")
        )
        families = {a.family for a in roster.assignments}
        self.assertEqual(families, {"claude"})
        self.assertTrue(roster.degraded_independence)

    def test_single_reachable_family_forces_reuse_and_flags_degraded_for_panel(
        self,
    ) -> None:
        """Criterion 3 (panel half): same as above, for a three-role panel."""
        roster = advisory_consultation.resolve_roster(
            "panel", is_family_reachable=_reachable("claude")
        )
        families = {a.family for a in roster.assignments}
        self.assertEqual(families, {"claude"})
        self.assertEqual(len(roster.assignments), 3)
        self.assertTrue(roster.degraded_independence)

    def test_panel_with_two_reachable_families_reuses_one_and_flags_degraded(
        self,
    ) -> None:
        """The ticket's own named ambiguity: a panel needs three distinct
        families for full independence, but only two are reachable. This
        module reads "a single family remains" (spec 0003's Implementation
        Decisions) as "resolution was forced to reuse a family already
        claimed within this roster" — see `resolve_roster`'s docstring for
        the full reasoning — which is true here even though two distinct
        families are reachable overall, so this must still degrade rather
        than silently running a two-out-of-three-independent panel."""
        roster = advisory_consultation.resolve_roster(
            "panel", is_family_reachable=_reachable("claude", "codex-gpt")
        )
        families = [a.family for a in roster.assignments]
        self.assertEqual(len(families), 3)
        self.assertEqual(set(families), {"claude", "codex-gpt"})
        self.assertTrue(roster.degraded_independence)

    def test_normal_run_never_flags_degraded(self) -> None:
        """Criterion 5: a normal (non-degraded) run never carries the marker."""
        for topology in ("pair", "panel"):
            with self.subTest(topology=topology):
                roster = advisory_consultation.resolve_roster(
                    topology,
                    is_family_reachable=_reachable("claude", "codex-gpt", "gemini"),
                )
                self.assertFalse(roster.degraded_independence)

    def test_model_for_raises_key_error_outside_the_topology(self) -> None:
        roster = advisory_consultation.resolve_roster(
            "pair", is_family_reachable=_reachable("claude", "codex-gpt")
        )
        with self.assertRaises(KeyError):
            roster.model_for("critic_b")

    def test_no_reachable_family_at_all_raises_roster_resolution_error(self) -> None:
        with self.assertRaises(advisory_consultation.RosterResolutionError):
            advisory_consultation.resolve_roster("pair", is_family_reachable=_reachable())

    def test_unknown_topology_raises_value_error(self) -> None:
        with self.assertRaises(ValueError):
            advisory_consultation.resolve_roster(
                "trio",  # type: ignore[arg-type]
                is_family_reachable=_reachable("claude"),
            )

    def test_default_chains_match_pre_ticket_07_parameter_defaults(self) -> None:
        """When everything is reachable, resolution reproduces exactly the
        hardcoded defaults `run_advisory_consultation_debate` already
        shipped before this ticket existed — this ticket adds a resolution
        *path*, it does not change what "everything is up" already looked
        like."""
        roster = advisory_consultation.resolve_roster(
            "panel", is_family_reachable=_reachable("claude", "codex-gpt", "gemini")
        )
        self.assertEqual(roster.model_for("planner"), "Claude Opus 5 (Thinking)")
        self.assertEqual(roster.model_for("critic_a"), "Codex 5.6 Sol")
        self.assertEqual(roster.model_for("critic_b"), "Gemini 3.6 Flash")

    def test_fallback_chains_are_read_from_config_not_hardcoded(self) -> None:
        """Drift guard mirroring `test_code_review_threshold_is_read_from_injected_config_not_hardcoded`'s
        own style: pointing `config_path` at a file with a different chain
        must change the resolver's answer, proving the chain is genuinely
        read from config rather than merely referenced by key."""
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "routing-config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "roster_topology": {
                            "role_fallback_chains": {
                                "planner": ["Qwen3-Coder-Next"],
                                "critic_a": ["Gemma 4 E4B"],
                                "critic_b": ["Claude Opus 5 (Thinking)"],
                            }
                        }
                    }
                )
            )
            roster = advisory_consultation.resolve_roster(
                "pair",
                is_family_reachable=_reachable("qwen", "gemma"),
                config_path=config_path,
            )
        planner = next(a for a in roster.assignments if a.role == "planner")
        critic_a = next(a for a in roster.assignments if a.role == "critic_a")
        self.assertEqual(planner.model, "Qwen3-Coder-Next")
        self.assertEqual(critic_a.model, "Gemma 4 E4B")

    def test_missing_roster_topology_section_falls_back_to_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "routing-config.json"
            config_path.write_text(json.dumps({}))
            roster = advisory_consultation.resolve_roster(
                "pair",
                is_family_reachable=_reachable("claude", "codex-gpt"),
                config_path=config_path,
            )
        self.assertEqual(roster.model_for("planner"), "Claude Opus 5 (Thinking)")
        self.assertEqual(roster.model_for("critic_a"), "Codex 5.6 Sol")


class AdvisoryRosterIntegrationTests(unittest.TestCase):
    """Spec 0003 (CriticalDialogue) ticket 07: `reachability_check` wired
    into `run_advisory_consultation_debate` as an opt-in roster-resolution
    seam. Every pre-existing test in this file never mentions this
    parameter, so it always defaults to `None` and continues to invoke
    exactly the explicit/default models it always did — the entire
    pre-existing 204-test suite, `AdvisoryConsultationTests` and
    `AdvisoryPanelTopologyTests` above included, is this ticket's
    regression guard that the opt-in changes nothing when a caller does not
    ask for it.
    """

    def test_reachability_check_none_leaves_default_models_untouched(self) -> None:
        """The opt-in contract's other half, explicit: omitting
        `reachability_check` (its default, `None`) must invoke exactly the
        models the caller passed, never a roster-resolved substitute, and
        `degraded_independence` must stay `False`."""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            plan = "Planner's plan."
            invoker = _RecordingInvoker([plan, _approve(plan)])
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite",
                invoker,
                root_dir=root,
                planner_model="Test Planner",
                critic_model="Test Critic",
            )

        self.assertEqual(result.planner_model, "Test Planner")
        self.assertEqual(result.critic_model, "Test Critic")
        self.assertFalse(result.degraded_independence)
        called_models = {model for model, _e, _p in invoker.calls}
        self.assertEqual(called_models, {"Test Planner", "Test Critic"})

    def test_pair_roster_resolved_end_to_end_with_three_reachable_families(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            plan = "Planner's plan."
            invoker = _RoleKeyedInvoker(
                {
                    "Claude Opus 5 (Thinking)": [plan],
                    "Codex 5.6 Sol": [_approve(plan)],
                }
            )
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite",
                invoker,
                root_dir=root,
                reachability_check=_reachable("claude", "codex-gpt", "gemini"),
            )

        self.assertTrue(result.consensus_reached)
        self.assertEqual(result.planner_model, "Claude Opus 5 (Thinking)")
        self.assertEqual(result.critic_model, "Codex 5.6 Sol")
        self.assertFalse(result.degraded_independence)

    def test_panel_roster_resolved_end_to_end_spans_three_distinct_families(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            plan = "Planner's plan."
            invoker = _RoleKeyedInvoker(
                {
                    "Claude Opus 5 (Thinking)": [plan],
                    "Codex 5.6 Sol": [_approve(plan, "Critic A: solid.")],
                    "Gemini 3.6 Flash": [_approve(plan, "Critic B: solid.")],
                }
            )
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite",
                invoker,
                root_dir=root,
                occasion="plan-review",
                complexity="complex",
                reachability_check=_reachable("claude", "codex-gpt", "gemini"),
            )

        self.assertTrue(result.consensus_reached)
        self.assertEqual(len(invoker.calls), 3)
        called_families = {
            advisory_consultation.classify_model_family(model)
            for model, _e, _p in invoker.calls
        }
        self.assertEqual(len(called_families), 3)
        self.assertFalse(result.degraded_independence)

    def test_degraded_independence_surfaces_in_telemetry_record(self) -> None:
        """Criterion 4 (telemetry half): the marker reaches the structured record."""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            plan = "Planner's plan."
            invoker = _RecordingInvoker([plan, _approve(plan)])
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite",
                invoker,
                root_dir=root,
                reachability_check=_reachable("claude"),
            )
            records = _read_jsonl(root / ".ralph" / "routing_telemetry.jsonl")

        self.assertTrue(result.degraded_independence)
        self.assertEqual(len(records), 1)
        self.assertTrue(records[0]["degraded_independence"])

    def test_degraded_independence_surfaces_in_transcript_text(self) -> None:
        """Criterion 4 (transcript half): a test asserting on transcript
        content finds the marker, not just the structured record."""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            plan = "Planner's plan."
            invoker = _RecordingInvoker([plan, _approve(plan)])
            advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite",
                invoker,
                root_dir=root,
                reachability_check=_reachable("claude"),
            )
            transcript = (root / ".scratch" / "planning_debate.md").read_text()

        self.assertIn(advisory_consultation.DEGRADED_INDEPENDENCE_MARKER, transcript)

    def test_normal_reachable_run_carries_no_degraded_marker_in_either_artifact(
        self,
    ) -> None:
        """Criterion 5, end to end: a normal run through the public entry
        point never carries the marker in either artifact."""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            plan = "Planner's plan."
            invoker = _RoleKeyedInvoker(
                {
                    "Claude Opus 5 (Thinking)": [plan],
                    "Codex 5.6 Sol": [_approve(plan)],
                }
            )
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite",
                invoker,
                root_dir=root,
                reachability_check=_reachable("claude", "codex-gpt", "gemini"),
            )
            transcript = (root / ".scratch" / "planning_debate.md").read_text()
            records = _read_jsonl(root / ".ralph" / "routing_telemetry.jsonl")

        self.assertFalse(result.degraded_independence)
        self.assertNotIn(advisory_consultation.DEGRADED_INDEPENDENCE_MARKER, transcript)
        self.assertFalse(records[0]["degraded_independence"])

    def test_roster_resolution_error_fails_closed_as_worker_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite",
                _RecordingInvoker([]),
                root_dir=root,
                reachability_check=_reachable(),  # nothing reachable at all
            )

        self.assertEqual(result.outcome, "worker_error")
        self.assertFalse(result.consensus_reached)
        self.assertFalse((root / "implementation_plan.md").exists())


class AdvisoryOccasionParameterizationTests(unittest.TestCase):
    """Spec 0003 (CriticalDialogue) ticket 01: the consultation loop becomes
    occasion-aware. `occasion` selects the mission prompt and is carried on
    the result; the pre-existing `ambiguity` occasion (spec 0001) must keep
    behaving exactly as it did before this seam existed — this class is the
    proof, alongside every pre-existing `AdvisoryConsultationTests` case
    passing unmodified with `occasion` never mentioned.
    """

    def test_default_occasion_is_ambiguity_and_is_recorded_on_the_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            invoker = _RecordingInvoker(
                ["Planner's proposed plan.", _approve("Planner's proposed plan.")]
            )
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite", invoker, root_dir=root
            )

        self.assertEqual(result.occasion, "ambiguity")

    def test_each_occasion_is_invocable_and_recorded_on_the_result(self) -> None:
        """Criterion 4: plan-review and code-review (and, since the `Occasion`
        type carries all four values, post-mortem and ambiguity too) must be
        invocable through the public seam without raising, each producing a
        result that names the occasion it ran under."""
        for occasion in ("ambiguity", "plan-review", "code-review", "post-mortem"):
            with self.subTest(occasion=occasion):
                with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
                    os.environ, {}, clear=True
                ):
                    root = Path(tmp)
                    invoker = _RecordingInvoker(
                        ["Planner's proposed plan.", _approve("Planner's proposed plan.")]
                    )
                    result = advisory_consultation.run_advisory_consultation_debate(
                        "Review the change",
                        invoker,
                        root_dir=root,
                        occasion=occasion,
                    )
                self.assertEqual(result.occasion, occasion)
                self.assertEqual(result.outcome, "consensus")
                self.assertEqual(len(invoker.calls), 2)

    def test_unknown_occasion_raises_without_invoking_any_worker(self) -> None:
        """Mirrors the existing `max_rounds` contract: an invalid `occasion`
        is a call-site programming error and must surface as a raise, not
        silently fall back to `ambiguity` or fail deep inside prompt-building
        with a bare `KeyError`."""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            invoker = _RecordingInvoker([])
            with self.assertRaises(ValueError):
                advisory_consultation.run_advisory_consultation_debate(
                    "Plan the auth rewrite",
                    invoker,
                    root_dir=root,
                    occasion="not-a-real-occasion",  # type: ignore[arg-type]
                )
            self.assertEqual(invoker.calls, [])

    def test_mission_prompt_selection_is_a_genuine_function_of_occasion(self) -> None:
        """Prompt *wording* stays unpinned per spec 0003's testing policy, but
        the seam this ticket builds must be provably more than
        occasion-in-same-prompt-out. The four occasions' round-1 Planner
        prompts, and their matching Critic prompts, are compared pairwise for
        inequality only — never for specific content."""
        occasions = ("ambiguity", "plan-review", "code-review", "post-mortem")
        planner_prompts: dict[str, str] = {}
        critic_prompts: dict[str, str] = {}
        for occasion in occasions:
            with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
                os.environ, {}, clear=True
            ):
                root = Path(tmp)
                invoker = _RecordingInvoker(
                    ["Planner's proposed plan.", _approve("Planner's proposed plan.")]
                )
                advisory_consultation.run_advisory_consultation_debate(
                    "Plan the auth rewrite", invoker, root_dir=root, occasion=occasion
                )
            planner_prompts[occasion] = invoker.calls[0][2]
            critic_prompts[occasion] = invoker.calls[1][2]

        self.assertEqual(len(set(planner_prompts.values())), len(occasions))
        self.assertEqual(len(set(critic_prompts.values())), len(occasions))

    def test_occasion_field_defaults_to_ambiguity_on_direct_construction(self) -> None:
        """Mirrors `test_consensus_reached_is_derived_from_outcome_and_cannot_be_mutated`
        above: existing direct `AdvisoryDebateResult(...)` construction sites
        that never mention `occasion` — in this test file and in production
        code — must keep working and must mean "ambiguity", not break on a
        newly-required positional/keyword argument."""
        result = advisory_consultation.AdvisoryDebateResult(
            rounds_run=1, final_plan="A plan.", outcome="consensus"
        )
        self.assertEqual(result.occasion, "ambiguity")


class AdvisoryAmbiguityTriggerUnchangedTests(unittest.TestCase):
    """Spec 0003 ticket 03 requires `needs_advisory_consultation` — the sole
    trigger predicate spec 0001 shipped — to be left completely untouched by
    this ticket's new occasion predicates. No test in the pre-existing suite
    exercised it directly (confirmed by searching this file before ticket 03
    started), so there was no pre-existing coverage to preserve unchanged;
    this class is new characterization coverage that pins today's behavior
    so a future change to this function is forced to notice it broke the
    ambiguity occasion, exactly as if the coverage had always been here.
    """

    def test_ambiguous_complexity_always_needs_consultation(self) -> None:
        self.assertTrue(advisory_consultation.needs_advisory_consultation("ambiguous"))
        self.assertTrue(advisory_consultation.needs_advisory_consultation("Ambiguous", 1.0))

    def test_low_confidence_needs_consultation_regardless_of_complexity(self) -> None:
        self.assertTrue(advisory_consultation.needs_advisory_consultation("trivial", 0.5))
        self.assertTrue(advisory_consultation.needs_advisory_consultation("complex", 0.69))

    def test_high_confidence_non_ambiguous_complexity_does_not_need_consultation(self) -> None:
        self.assertFalse(advisory_consultation.needs_advisory_consultation("medium", 0.9))
        self.assertFalse(advisory_consultation.needs_advisory_consultation("complex"))

    def test_confidence_boundary_is_exclusive_at_0_7(self) -> None:
        self.assertFalse(advisory_consultation.needs_advisory_consultation("simple", 0.7))
        self.assertTrue(advisory_consultation.needs_advisory_consultation("simple", 0.6999))


class AdvisoryTriggerWiringTests(unittest.TestCase):
    """Spec 0003 (CriticalDialogue) ticket 03: the three new occasion trigger
    predicates — `needs_plan_review_consultation`, `needs_code_review_consultation`,
    and `needs_post_mortem_consultation`. Each is a standalone function, not a
    generalization of `needs_advisory_consultation` (see
    `AdvisoryAmbiguityTriggerUnchangedTests` above for proof that one is
    untouched). These are pure predicates over signals a caller already has
    in hand — no worker is invoked, no artifact is written — so every test
    here calls the function directly with no debate loop, no injected root
    directory, and no fake invoker.
    """

    # -- plan-review ---------------------------------------------------

    def test_plan_review_fires_at_medium_and_complex(self) -> None:
        self.assertTrue(advisory_consultation.needs_plan_review_consultation("medium"))
        self.assertTrue(advisory_consultation.needs_plan_review_consultation("complex"))
        # Case/whitespace tolerance mirrors `needs_advisory_consultation`'s
        # own `.lower().strip()` normalization, not a coincidence — both
        # predicates read the same caller-supplied complexity string.
        self.assertTrue(advisory_consultation.needs_plan_review_consultation(" Medium "))
        self.assertTrue(advisory_consultation.needs_plan_review_consultation("COMPLEX"))

    def test_plan_review_does_not_fire_at_simple_or_trivial(self) -> None:
        self.assertFalse(advisory_consultation.needs_plan_review_consultation("simple"))
        self.assertFalse(advisory_consultation.needs_plan_review_consultation("trivial"))

    # -- code-review -----------------------------------------------------

    def test_code_review_fires_at_medium_and_complex_with_zero_risk_signals(self) -> None:
        self.assertTrue(advisory_consultation.needs_code_review_consultation("medium"))
        self.assertTrue(advisory_consultation.needs_code_review_consultation("complex"))

    def test_code_review_does_not_fire_at_trivial_or_simple_with_no_risk_signal(self) -> None:
        self.assertFalse(advisory_consultation.needs_code_review_consultation("trivial"))
        self.assertFalse(advisory_consultation.needs_code_review_consultation("simple"))

    def test_code_review_fires_at_trivial_when_tests_are_failing(self) -> None:
        self.assertTrue(
            advisory_consultation.needs_code_review_consultation(
                "trivial", tests_failing=True
            )
        )

    def test_code_review_fires_at_simple_when_diff_exceeds_configured_threshold(
        self,
    ) -> None:
        # Uses the real routing-config.json threshold (300 lines) as the
        # boundary: one line under does not fire, one line over does. This
        # is deliberately over the *real* config, not an injected one — see
        # the config-injection test below for proof the value is genuinely
        # read from config rather than a Python-side literal that happens to
        # match it today.
        self.assertFalse(
            advisory_consultation.needs_code_review_consultation(
                "simple", diff_line_count=300
            )
        )
        self.assertTrue(
            advisory_consultation.needs_code_review_consultation(
                "simple", diff_line_count=301
            )
        )

    def test_code_review_fires_at_trivial_when_a_changed_path_is_security_sensitive(
        self,
    ) -> None:
        self.assertTrue(
            advisory_consultation.needs_code_review_consultation(
                "trivial", changed_paths=["src/auth/login.py"]
            )
        )

    def test_code_review_does_not_fire_at_trivial_for_an_ordinary_changed_path(
        self,
    ) -> None:
        self.assertFalse(
            advisory_consultation.needs_code_review_consultation(
                "trivial", changed_paths=["src/widgets/button.py"]
            )
        )

    def test_code_review_threshold_is_read_from_injected_config_not_hardcoded(
        self,
    ) -> None:
        """Proves the diff-size threshold is genuinely read from config —
        not merely referenced by key — by injecting two different config
        files and observing the trigger's boolean answer flip for the exact
        same `diff_line_count` input."""
        with tempfile.TemporaryDirectory() as tmp:
            low_threshold_config = Path(tmp) / "low.json"
            low_threshold_config.write_text(
                json.dumps({"critical_dialogue": {"code_review_diff_line_threshold": 5}})
            )
            high_threshold_config = Path(tmp) / "high.json"
            high_threshold_config.write_text(
                json.dumps(
                    {"critical_dialogue": {"code_review_diff_line_threshold": 5000}}
                )
            )

            fires_low = advisory_consultation.needs_code_review_consultation(
                "trivial", diff_line_count=10, config_path=low_threshold_config
            )
            fires_high = advisory_consultation.needs_code_review_consultation(
                "trivial", diff_line_count=10, config_path=high_threshold_config
            )

        self.assertTrue(fires_low)
        self.assertFalse(fires_high)

    def test_code_review_security_paths_are_read_from_injected_config_not_hardcoded(
        self,
    ) -> None:
        """Same proof as the threshold test above, for
        `security_sensitive_path_patterns`: a pattern present only in the
        injected config fires, and a real-config pattern absent from the
        injected config does not."""
        with tempfile.TemporaryDirectory() as tmp:
            custom_config = Path(tmp) / "custom.json"
            custom_config.write_text(
                json.dumps(
                    {
                        "critical_dialogue": {
                            "security_sensitive_path_patterns": ["quux_only_here"]
                        }
                    }
                )
            )

            fires_on_custom_pattern = advisory_consultation.needs_code_review_consultation(
                "trivial",
                changed_paths=["src/quux_only_here/module.py"],
                config_path=custom_config,
            )
            does_not_fire_on_real_config_pattern = (
                advisory_consultation.needs_code_review_consultation(
                    "trivial",
                    changed_paths=["src/auth/login.py"],
                    config_path=custom_config,
                )
            )

        self.assertTrue(fires_on_custom_pattern)
        self.assertFalse(does_not_fire_on_real_config_pattern)

    # -- post-mortem -------------------------------------------------------

    def test_post_mortem_does_not_fire_with_no_signal(self) -> None:
        self.assertFalse(advisory_consultation.needs_post_mortem_consultation())

    def test_post_mortem_fires_on_failure(self) -> None:
        self.assertTrue(advisory_consultation.needs_post_mortem_consultation(failed=True))

    def test_post_mortem_fires_on_two_failure_escalation(self) -> None:
        # Tracks `advisory_consultation.ESCALATION_FAILURE_THRESHOLD`, the
        # same named constant `agent_council.escalate_routing_effort` uses
        # for "2+ failed worker attempts" — see
        # `test_escalation_failure_threshold_matches_agent_council_constant`
        # below for the drift guard between the two modules' copies of it.
        threshold = advisory_consultation.ESCALATION_FAILURE_THRESHOLD
        self.assertFalse(
            advisory_consultation.needs_post_mortem_consultation(
                consecutive_failures=threshold - 1
            )
        )
        self.assertTrue(
            advisory_consultation.needs_post_mortem_consultation(
                consecutive_failures=threshold
            )
        )
        self.assertTrue(
            advisory_consultation.needs_post_mortem_consultation(
                consecutive_failures=threshold + 1
            )
        )

    def test_escalation_failure_threshold_matches_agent_council_constant(self) -> None:
        """Drift guard for the deliberate duplication: `advisory_consultation`
        does not import `agent_council` (see the comment on
        `ESCALATION_FAILURE_THRESHOLD`), so this test is what keeps the two
        modules' copies of the protocol's 2-failure escalation threshold
        from silently diverging — mirrors
        `test_sensitivity_markers_are_a_superset_of_agent_council_patterns`'s
        role for `SENSITIVITY_MARKERS`/`SENSITIVE_PATTERNS`."""
        self.assertEqual(
            advisory_consultation.ESCALATION_FAILURE_THRESHOLD,
            agent_council.ESCALATION_FAILURE_THRESHOLD,
        )

    def test_post_mortem_fires_on_a_stalemate_from_any_non_post_mortem_occasion(
        self,
    ) -> None:
        for occasion in ("ambiguity", "plan-review", "code-review"):
            with self.subTest(occasion=occasion):
                self.assertTrue(
                    advisory_consultation.needs_post_mortem_consultation(
                        occasion=occasion, stalemate_occurred=True
                    )
                )

    def test_post_mortem_stalemate_does_not_recursively_trigger_another_post_mortem(
        self,
    ) -> None:
        self.assertFalse(
            advisory_consultation.needs_post_mortem_consultation(
                occasion="post-mortem", stalemate_occurred=True
            )
        )

    def test_post_mortem_occasion_never_fires_on_any_signal_not_just_stalemate(
        self,
    ) -> None:
        """The recursion guard is a blanket "a post-mortem's own outcome
        must NOT recursively trigger another post-mortem" (spec 0003 ticket
        03), not narrowly scoped to the stalemate example the ticket
        happens to spell out — a post-mortem dialogue that itself failed, or
        that is somehow read as its own second consecutive failure, must not
        chain into a further post-mortem either."""
        self.assertFalse(
            advisory_consultation.needs_post_mortem_consultation(
                occasion="post-mortem", failed=True
            )
        )
        self.assertFalse(
            advisory_consultation.needs_post_mortem_consultation(
                occasion="post-mortem", consecutive_failures=5
            )
        )


class AdvisorySensitivityGateTests(unittest.TestCase):
    """A sensitive task must never reach a cloud Planner or Critic.

    Ticket 05: `run_advisory_consultation_debate` gates on the task text
    before contacting any worker, distinct from the four outcomes that
    already exist because it never even enters the round loop.
    """

    def test_credential_marker_halts_before_any_worker_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            invoker = _RecordingInvoker(
                ["Planner's proposed plan.", _approve("Planner's proposed plan.")]
            )
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite using api_key=sk-abc123 for the test fixture",
                invoker,
                root_dir=root,
            )
            self.assertFalse((root / "implementation_plan.md").exists())

        self.assertEqual(invoker.calls, [])
        self.assertEqual(result.outcome, "sensitivity_halt")
        self.assertFalse(result.consensus_reached)

    def test_explicit_sensitive_marker_halts_before_any_worker_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            invoker = _RecordingInvoker(
                ["Planner's proposed plan.", _approve("Planner's proposed plan.")]
            )
            result = advisory_consultation.run_advisory_consultation_debate(
                "[SENSITIVE] Plan the customer PII migration",
                invoker,
                root_dir=root,
            )

        self.assertEqual(invoker.calls, [])
        self.assertEqual(result.outcome, "sensitivity_halt")
        self.assertFalse(result.consensus_reached)

    def test_sensitivity_halt_is_distinct_from_consensus_and_stalemate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite, password=hunter2",
                _RecordingInvoker([]),
                root_dir=root,
            )

        self.assertEqual(result.outcome, "sensitivity_halt")
        self.assertNotEqual(result.outcome, "consensus")
        self.assertNotEqual(result.outcome, "stalemate")
        self.assertFalse(result.consensus_reached)
        self.assertEqual(result.rounds_run, 0)
        self.assertEqual(result.rounds, ())
        self.assertEqual(result.final_plan, "")
        self.assertIsNone(result.stalemate)

    def test_halt_reason_states_human_approval_required_and_names_the_marker(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            result = advisory_consultation.run_advisory_consultation_debate(
                "Rotate the api_key before the migration",
                _RecordingInvoker([]),
                root_dir=root,
            )

        self.assertIsNotNone(result.error)
        assert result.error is not None
        self.assertIn("human approval", result.error.lower())
        self.assertIn("api_key", result.error)

    def test_halt_reason_never_leaks_task_text_or_matched_secret_value(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            task = "Plan the rollout; api_key=sk-supersecretvalue-do-not-print"
            result = advisory_consultation.run_advisory_consultation_debate(
                task, _RecordingInvoker([]), root_dir=root
            )

        self.assertIsNotNone(result.error)
        assert result.error is not None
        self.assertNotIn("supersecretvalue", result.error)
        self.assertNotIn("sk-supersecretvalue-do-not-print", result.error)
        self.assertNotIn(task, result.error)

    def test_pre_existing_plan_file_is_removed_on_sensitivity_halt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            plan_path = root / "implementation_plan.md"
            plan_path.write_text("stale plan from an earlier run")

            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the rollout, secret=whatever",
                _RecordingInvoker([]),
                root_dir=root,
            )

            self.assertFalse(plan_path.exists())

        self.assertEqual(result.outcome, "sensitivity_halt")

    def test_gate_checks_the_task_not_worker_responses(self) -> None:
        """A non-sensitive task must run the loop normally even if a worker's
        own response happens to contain a sensitive marker — the gate is a
        pre-check on the task text, never a filter on what a worker says."""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            invoker = _RecordingInvoker(
                [
                    "Planner's plan mentions api_key rotation as a step.",
                    _approve("Planner's plan mentions api_key rotation as a step."),
                ]
            )
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite", invoker, root_dir=root
            )

        self.assertEqual(len(invoker.calls), 2)
        self.assertEqual(result.outcome, "consensus")
        self.assertTrue(result.consensus_reached)

    def test_sensitivity_markers_are_a_superset_of_agent_council_patterns(self) -> None:
        """Drift guard for the deliberate duplication: `advisory_consultation`
        does not import `agent_council` (see the comment on
        `SENSITIVITY_MARKERS`), so this test is what keeps the two pattern
        sets from silently diverging."""
        advisory_markers = {marker.lower() for marker in advisory_consultation.SENSITIVITY_MARKERS}
        council_patterns = {pattern.lower() for pattern in agent_council.SENSITIVE_PATTERNS}
        self.assertTrue(council_patterns.issubset(advisory_markers))


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


class AdvisoryTranscriptAndTelemetryTests(unittest.TestCase):
    """Ticket 06: every exit path writes a human-readable transcript and one
    structured telemetry record, and neither ever leaks task text or a
    matched secret value on a sensitivity halt."""

    TRANSCRIPT_RELATIVE_PATH = Path(".scratch") / "planning_debate.md"
    TELEMETRY_RELATIVE_PATH = Path(".ralph") / "routing_telemetry.jsonl"

    def test_consensus_writes_transcript_with_the_rounds_exchange(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            invoker = _RecordingInvoker(
                ["Planner's proposed plan.", _approve("Planner's proposed plan.")]
            )
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite", invoker, root_dir=root
            )
            transcript = (root / self.TRANSCRIPT_RELATIVE_PATH).read_text()

        self.assertEqual(result.outcome, "consensus")
        self.assertIn("Outcome:** consensus", transcript)
        self.assertIn("Plan the auth rewrite", transcript)
        self.assertIn("Round 1", transcript)
        self.assertIn("Planner's proposed plan.", transcript)
        self.assertIn(_approve("Planner's proposed plan."), transcript)

    def test_stalemate_writes_transcript_with_every_round_in_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            invoker = _RecordingInvoker(
                [
                    "Planner's first plan.",
                    _revise("Needs more detail."),
                    "Planner's second plan.",
                    _revise("Still thin."),
                    "Planner's third plan.",
                    _revise("Not convinced."),
                ]
            )
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite", invoker, root_dir=root
            )
            transcript = (root / self.TRANSCRIPT_RELATIVE_PATH).read_text()

        self.assertEqual(result.outcome, "stalemate")
        self.assertIn("Outcome:** stalemate", transcript)
        first_round = transcript.index("Round 1")
        second_round = transcript.index("Round 2")
        third_round = transcript.index("Round 3")
        self.assertLess(first_round, second_round)
        self.assertLess(second_round, third_round)
        for text in (
            "Planner's first plan.",
            "Needs more detail.",
            "Planner's second plan.",
            "Still thin.",
            "Planner's third plan.",
            "Not convinced.",
        ):
            self.assertIn(text, transcript)

    def test_unparseable_verdict_writes_transcript_with_the_one_round(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            invoker = _RecordingInvoker(
                ["Planner's plan.", "This plan looks fine to me."]
            )
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite", invoker, root_dir=root, max_rounds=3
            )
            transcript = (root / self.TRANSCRIPT_RELATIVE_PATH).read_text()

        self.assertEqual(result.outcome, "unparseable_verdict")
        self.assertIn("Outcome:** unparseable_verdict", transcript)
        self.assertIn("Planner's plan.", transcript)
        self.assertIn("This plan looks fine to me.", transcript)

    def test_worker_error_writes_transcript_including_completed_rounds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            invoker = _RecordingInvoker(
                [
                    "Planner's first plan.",
                    _revise("Needs more detail."),
                    RuntimeError("worker unreachable"),
                ]
            )
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite", invoker, root_dir=root
            )
            transcript = (root / self.TRANSCRIPT_RELATIVE_PATH).read_text()

        self.assertEqual(result.outcome, "worker_error")
        self.assertIn("Outcome:** worker_error", transcript)
        self.assertIn("Planner's first plan.", transcript)
        self.assertIn("Needs more detail.", transcript)

    def test_sensitivity_halt_writes_a_redacted_transcript(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the rollout, password=hunter2",
                _RecordingInvoker([]),
                root_dir=root,
            )
            transcript = (root / self.TRANSCRIPT_RELATIVE_PATH).read_text()

        self.assertEqual(result.outcome, "sensitivity_halt")
        self.assertIn("Outcome:** sensitivity_halt", transcript)
        self.assertIn("password", transcript)
        self.assertIn("human approval", transcript.lower())
        self.assertNotIn("hunter2", transcript)
        self.assertNotIn("Plan the rollout", transcript)

    def test_redaction_boundary_secret_reaches_neither_artifact(self) -> None:
        """The hardest constraint: a sensitivity halt's secret value must
        appear in NEITHER the transcript NOR the telemetry record — and
        the emitted task identity itself must not be a value an auditor (or
        an attacker) could recompute from guessed task text, or the digest
        is a confirmation oracle in all but name."""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            task = "Plan the rollout; api_key=sk-supersecretvalue-do-not-print"
            advisory_consultation.run_advisory_consultation_debate(
                task, _RecordingInvoker([]), root_dir=root
            )
            transcript = (root / self.TRANSCRIPT_RELATIVE_PATH).read_text()
            telemetry_text = (root / self.TELEMETRY_RELATIVE_PATH).read_text()
            records = _read_jsonl(root / self.TELEMETRY_RELATIVE_PATH)

        for leak in ("supersecretvalue", "sk-supersecretvalue-do-not-print", task):
            self.assertNotIn(leak, transcript)
            self.assertNotIn(leak, telemetry_text)

        emitted_task_id = records[0]["task_id"]
        digest_of_task_text = hashlib.sha256(task.encode("utf-8")).hexdigest()[:16]
        self.assertNotEqual(
            emitted_task_id,
            digest_of_task_text,
            "sensitivity_halt task_id must not be derived from task_description",
        )
        self.assertIn(
            emitted_task_id,
            transcript,
            "transcript and telemetry for the same halt must carry the same task_id",
        )

    def test_telemetry_record_carries_task_identity_rounds_outcome_and_models(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            invoker = _RecordingInvoker(
                [
                    "Planner's first plan.",
                    _revise("Needs more detail."),
                    "Planner's revised plan.",
                    _approve("Planner's revised plan.", "Good now."),
                ]
            )
            advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite",
                invoker,
                root_dir=root,
                planner_model="Test Planner",
                critic_model="Test Critic",
                task_id="ticket-06-demo",
            )
            records = _read_jsonl(root / self.TELEMETRY_RELATIVE_PATH)

        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["task_id"], "ticket-06-demo")
        self.assertEqual(record["rounds_run"], 2)
        self.assertEqual(record["outcome"], "consensus")
        self.assertEqual(record["planner_model"], "Test Planner")
        self.assertEqual(record["critic_model"], "Test Critic")
        self.assertIn("timestamp", record)

    def test_exactly_one_telemetry_record_is_emitted_per_consultation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            invoker = _RecordingInvoker(
                ["Planner's plan.", _approve("Planner's plan.")]
            )
            advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite", invoker, root_dir=root
            )
            records = _read_jsonl(root / self.TELEMETRY_RELATIVE_PATH)

        self.assertEqual(len(records), 1)

    def test_default_task_id_is_a_stable_hash_not_the_task_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            for _ in range(2):
                invoker = _RecordingInvoker(
                    ["Planner's plan.", _approve("Planner's plan.")]
                )
                advisory_consultation.run_advisory_consultation_debate(
                    "Plan the auth rewrite", invoker, root_dir=root
                )
            records = _read_jsonl(root / self.TELEMETRY_RELATIVE_PATH)

        expected = hashlib.sha256(b"Plan the auth rewrite").hexdigest()[:16]
        self.assertEqual(len(records), 2)
        for record in records:
            self.assertEqual(record["task_id"], expected)
            self.assertNotIn("Plan the auth rewrite", record["task_id"])

    def test_max_rounds_below_one_raises_before_writing_any_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            with self.assertRaises(ValueError):
                advisory_consultation.run_advisory_consultation_debate(
                    "Plan the auth rewrite",
                    _RecordingInvoker([]),
                    root_dir=root,
                    max_rounds=0,
                )
            self.assertFalse((root / self.TRANSCRIPT_RELATIVE_PATH).exists())
            self.assertFalse((root / self.TELEMETRY_RELATIVE_PATH).exists())

    def test_transcript_is_overwritten_not_appended_across_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            first_invoker = _RecordingInvoker(
                ["First run's plan.", _approve("First run's plan.", "First run approved.")]
            )
            advisory_consultation.run_advisory_consultation_debate(
                "Plan the first task", first_invoker, root_dir=root
            )
            second_invoker = _RecordingInvoker(
                ["Second run's plan.", _approve("Second run's plan.", "Second run approved.")]
            )
            advisory_consultation.run_advisory_consultation_debate(
                "Plan the second task", second_invoker, root_dir=root
            )
            transcript = (root / self.TRANSCRIPT_RELATIVE_PATH).read_text()

        self.assertIn("Second run's plan.", transcript)
        self.assertNotIn("First run's plan.", transcript)
        self.assertNotIn("Plan the first task", transcript)

    def test_transcript_write_failure_does_not_mask_the_primary_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            (root / ".scratch").write_text("not a directory")
            invoker = _RecordingInvoker(
                ["Planner's plan.", _approve("Planner's plan.")]
            )
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite", invoker, root_dir=root
            )
            self.assertTrue((root / "implementation_plan.md").exists())

        self.assertEqual(result.outcome, "consensus")
        self.assertTrue(result.consensus_reached)
        self.assertEqual(result.final_plan, "Planner's plan.")
        self.assertIsNotNone(result.error)
        assert result.error is not None
        self.assertIn("transcript", result.error.lower())

    def test_telemetry_write_failure_does_not_mask_the_primary_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            (root / ".ralph").write_text("not a directory")
            invoker = _RecordingInvoker(
                ["Planner's plan.", _approve("Planner's plan.")]
            )
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite", invoker, root_dir=root
            )
            self.assertTrue((root / "implementation_plan.md").exists())
            self.assertTrue((root / self.TRANSCRIPT_RELATIVE_PATH).exists())

        self.assertEqual(result.outcome, "consensus")
        self.assertTrue(result.consensus_reached)
        self.assertIsNotNone(result.error)
        assert result.error is not None
        self.assertIn("telemetry", result.error.lower())

    def test_consensus_plan_write_failure_does_not_mask_the_primary_outcome(
        self,
    ) -> None:
        """Ticket 06's first acceptance criterion is that a transcript is
        written for all four outcomes including consensus. The plan-artifact
        write is the one unguarded I/O on the consensus path: making
        `implementation_plan.md` a directory beforehand must not let
        `IsADirectoryError` propagate out of the debate function before the
        transcript and telemetry are written. The Critic genuinely approved,
        so the outcome must still be `consensus` and `final_plan` must still
        carry the agreed text — only `error` should report the write
        failure."""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            (root / "implementation_plan.md").mkdir()
            invoker = _RecordingInvoker(
                ["Planner's plan.", _approve("Planner's plan.")]
            )
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite", invoker, root_dir=root
            )
            transcript = (root / self.TRANSCRIPT_RELATIVE_PATH).read_text()
            telemetry_records = _read_jsonl(root / self.TELEMETRY_RELATIVE_PATH)

        self.assertEqual(result.outcome, "consensus")
        self.assertTrue(result.consensus_reached)
        self.assertEqual(result.final_plan, "Planner's plan.")
        self.assertIsNotNone(result.error)
        assert result.error is not None
        self.assertIn("implementation_plan.md", result.error)
        self.assertIn("Outcome:** consensus", transcript)
        self.assertEqual(len(telemetry_records), 1)
        self.assertEqual(telemetry_records[0]["outcome"], "consensus")

    def test_telemetry_and_agent_council_share_the_same_log_file(self) -> None:
        """Drift guard: an auditor reads one stream, not two — the log file
        `AgentCouncil` already writes must be the exact file this module
        writes to, not a lookalike path."""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            invoker = _RecordingInvoker(
                ["Planner's plan.", _approve("Planner's plan.")]
            )
            advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite", invoker, root_dir=root
            )
            council = agent_council.AgentCouncil(root_dir=root)
            self.assertEqual(
                council.telemetry_file,
                (root / self.TELEMETRY_RELATIVE_PATH).resolve(),
            )
            records = _read_jsonl(council.telemetry_file)

        self.assertEqual(len(records), 1)

    def test_append_jsonl_locked_matches_agent_council_byte_for_byte(self) -> None:
        """Drift guard for the deliberate duplication of `_append_jsonl_locked`
        (see its docstring, and the identical precedent this test mirrors:
        `test_sensitivity_markers_are_a_superset_of_agent_council_patterns`).
        Pinning only the log path (as `test_telemetry_and_agent_council_share_the_same_log_file`
        does) leaves the record encoding (`sort_keys`, the trailing newline)
        free to drift apart silently. This asserts the two writers produce
        byte-identical output for the same record, so an encoding change on
        either side fails loudly. It proves nothing about the lock semantics
        (`fcntl.flock`): a byte comparison of the two output files cannot
        observe locking behaviour, only the encoding it produced. Lock-
        semantics drift is NOT covered by this or any other test here."""
        record = {"b": 2, "a": 1, "outcome": "consensus", "kind": "advisory_consultation"}
        with tempfile.TemporaryDirectory() as tmp:
            advisory_path = Path(tmp) / "advisory.jsonl"
            council_path = Path(tmp) / "council.jsonl"
            advisory_consultation._append_jsonl_locked(advisory_path, record)
            agent_council.append_jsonl_locked(council_path, record)
            self.assertEqual(advisory_path.read_bytes(), council_path.read_bytes())

    def test_advisory_record_carries_kind_council_record_does_not(self) -> None:
        """Spec 0001 US 12: both `AgentCouncil` and this module append to the
        same `.ralph/routing_telemetry.jsonl`, so an auditor needs a way to
        tell the two record families apart to reconstruct which decisions
        were model-deliberated. `kind` is that discriminator, and it is
        deliberately one-sided: an advisory record carries it, a council
        record never does (`agent_council.log_routing_telemetry`'s record
        shape is asserted by its own tests and is off-limits here), so the
        absence of `kind` reliably identifies a council decision. This test
        pins that asymmetry as the contract."""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ), mock.patch.object(
            agent_council, "check_local_model_endpoint", return_value=True
        ):
            root = Path(tmp)
            invoker = _RecordingInvoker(["Plan.", _approve("Plan.", "Good.")])
            advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite",
                invoker,
                root_dir=root,
                task_id="advisory-1",
            )
            agent_council.AgentCouncil(root_dir=root).run(
                "Refactor routing checks", "simple", task_id="council-1"
            )
            records = _read_jsonl(root / self.TELEMETRY_RELATIVE_PATH)

        by_task_id = {record["task_id"]: record for record in records}
        self.assertEqual(by_task_id["advisory-1"]["kind"], "advisory_consultation")
        self.assertNotIn("kind", by_task_id["council-1"])


class _BlockingThenScriptedInvoker:
    """A fake `invoke_worker`: the FIRST call sets `called_event` and then
    blocks on `release_event` until the test releases it; every call
    (including that first one, once unblocked) pops its response off
    `responses` exactly like `_RecordingInvoker` does.

    Built for `AdvisoryBlockingStanceTests`'s non-blocking-dispatch proof:
    blocking only the first call is sufficient to prove a whole consultation
    cannot have completed yet (round 1 needs a Planner call before it can
    even reach the Critic), while still letting the rest of the scripted
    exchange run normally to completion once released — no test needs to
    script a block on every call just to prove one debate never finished
    early.
    """

    def __init__(self, responses: list[str | Exception], *, release_event: threading.Event, called_event: threading.Event) -> None:
        self.responses: list[str | Exception] = list(responses)
        self.release_event = release_event
        self.called_event = called_event
        self.calls: list[tuple[str, str, str]] = []

    def __call__(self, model: str, effort: str, prompt: str) -> str:
        self.calls.append((model, effort, prompt))
        if len(self.calls) == 1:
            self.called_event.set()
            self.release_event.wait(timeout=5)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class AdvisoryBlockingStanceTests(unittest.TestCase):
    """Spec 0003 (CriticalDialogue) ticket 04: "Plan-review and code-review
    dialogues gate progress. Post-mortems run in the background and never
    block; their occurrence and outcome are still recorded" (Implementation
    Decisions, "Blocking stance").

    Plan-review and code-review need no production code change — calling
    `run_advisory_consultation_debate` synchronously already blocks the
    caller, because it is an ordinary function call. The first test below is
    a characterization test for that fact, pinned via call ordering so a
    future change cannot silently make either occasion non-blocking without
    a test noticing. The rest of this class exercises the one piece of new
    production code this ticket does add: `dispatch_post_mortem_consultation`,
    the background-thread wrapper that gives the post-mortem occasion its
    non-blocking stance.
    """

    def test_plan_review_and_code_review_block_the_caller_until_the_debate_resolves(
        self,
    ) -> None:
        """Criterion 1 and criterion 4 (call ordering as the observable
        proof): for both occasions that must gate progress, every worker
        call the fake records happens strictly between the call to
        `run_advisory_consultation_debate` and the line after it — the
        caller's next line of code provably does not run until the dialogue
        result is available."""
        for occasion in ("plan-review", "code-review"):
            with self.subTest(occasion=occasion):
                order: list[str] = []
                scripted_responses = iter(["Planner's plan.", _approve("Planner's plan.")])

                def fake_invoke_worker(
                    model: str,
                    effort: str,
                    prompt: str,
                    *,
                    # Bind the loop-scoped fixtures as defaults (B023): each
                    # subTest iteration gets its own fake closed over its own
                    # `order`/`scripted_responses`.
                    _order: list[str] = order,
                    _responses: Iterator[str] = scripted_responses,
                ) -> str:
                    _order.append("invoker_called")
                    return next(_responses)

                with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
                    os.environ, {}, clear=True
                ):
                    root = Path(tmp)
                    order.append("before_call")
                    result = advisory_consultation.run_advisory_consultation_debate(
                        "Review the change",
                        fake_invoke_worker,
                        root_dir=root,
                        occasion=occasion,
                    )
                    order.append("after_call")

                self.assertEqual(
                    order,
                    ["before_call", "invoker_called", "invoker_called", "after_call"],
                    "the caller's own code must not run between the dispatch and the "
                    "dialogue's resolution for a blocking occasion",
                )
                self.assertEqual(result.outcome, "consensus")

    def test_dispatch_post_mortem_consultation_returns_before_the_debate_completes(
        self,
    ) -> None:
        """Criterion 2 and criterion 4: dispatching a post-mortem must hand
        control back to the caller while the underlying debate is still
        provably incomplete — proven here by blocking the fake worker on a
        `threading.Event` the test controls and observing, immediately after
        `dispatch_post_mortem_consultation` returns, that (a) the fake has
        been entered (so the background thread genuinely started running
        the real debate loop, not a no-op) and (b) no transcript exists yet
        (so that debate cannot have reached its own choke point, which is
        the only place either artifact gets written)."""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            release_event = threading.Event()
            called_event = threading.Event()
            invoker = _BlockingThenScriptedInvoker(
                ["Planner's lesson.", _approve("Planner's lesson.")],
                release_event=release_event,
                called_event=called_event,
            )

            thread = advisory_consultation.dispatch_post_mortem_consultation(
                "Post-mortem for the auth rewrite failure",
                invoker,
                root_dir=root,
            )

            self.assertTrue(
                called_event.wait(timeout=5), "background thread never invoked the worker"
            )
            self.assertFalse(
                (root / AdvisoryTranscriptAndTelemetryTests.TRANSCRIPT_RELATIVE_PATH).exists(),
                "transcript must not exist while the debate is still blocked — its "
                "presence here would mean dispatch waited for the debate after all",
            )
            self.assertFalse(
                (root / AdvisoryTranscriptAndTelemetryTests.TELEMETRY_RELATIVE_PATH).exists(),
                "telemetry must not exist while the debate is still blocked",
            )

            release_event.set()
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive(), "background thread leaked past the test")

    def test_dispatch_post_mortem_consultation_writes_transcript_and_telemetry_once_the_thread_completes(
        self,
    ) -> None:
        """Criterion 3: once the background thread finishes, the post-mortem's
        transcript and telemetry are discoverable at exactly the paths a
        synchronous call would have used, with the same content a
        synchronous call would have produced — "exactly as if it had
        blocked" (ticket 04's own wording). Also confirms
        `dispatch_post_mortem_consultation` genuinely runs the post-mortem
        occasion (not e.g. the default "ambiguity") by checking the
        recorded Planner prompt."""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            release_event = threading.Event()
            called_event = threading.Event()
            invoker = _BlockingThenScriptedInvoker(
                ["Planner's lesson.", _approve("Planner's lesson.")],
                release_event=release_event,
                called_event=called_event,
            )

            thread = advisory_consultation.dispatch_post_mortem_consultation(
                "Post-mortem for the auth rewrite failure",
                invoker,
                root_dir=root,
                task_id="post-mortem-ticket-04-demo",
            )
            called_event.wait(timeout=5)
            release_event.set()
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())

            transcript = (
                root / AdvisoryTranscriptAndTelemetryTests.TRANSCRIPT_RELATIVE_PATH
            ).read_text()
            records = _read_jsonl(
                root / AdvisoryTranscriptAndTelemetryTests.TELEMETRY_RELATIVE_PATH
            )

        self.assertIn("Outcome:** consensus", transcript)
        self.assertIn("Planner's lesson.", transcript)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["task_id"], "post-mortem-ticket-04-demo")
        self.assertEqual(records[0]["outcome"], "consensus")
        self.assertIn("post-mortem", invoker.calls[0][2].lower())

    def test_dispatch_post_mortem_consultation_records_unexpected_exceptions_instead_of_dropping_them(
        self,
    ) -> None:
        """Standards-axis hardening: `run_advisory_consultation_debate`
        already fails closed for every documented outcome (worker error,
        stalemate, unparseable verdict, sensitivity halt) by writing a
        transcript and telemetry record before returning. But if it — or a
        future change to it — ever raised something outside those
        documented paths, that exception would propagate through
        `Thread.run()` and hit Python's default unhandled-exception-in-
        thread behavior: printed once to stderr, then gone, with nothing
        written and nothing the dispatching caller can observe. This test
        forces exactly that by patching `run_advisory_consultation_debate`
        itself to raise, then proves `_run_dispatched_post_mortem`'s
        exception net still produces a discoverable transcript and
        telemetry record rather than letting the bug vanish silently."""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ), mock.patch.object(
            advisory_consultation,
            "run_advisory_consultation_debate",
            side_effect=RuntimeError("unexpected bug: sentinel-oops"),
        ):
            root = Path(tmp)

            thread = advisory_consultation.dispatch_post_mortem_consultation(
                "Post-mortem for a mystery failure",
                _RecordingInvoker([]),
                root_dir=root,
                task_id="post-mortem-crash-demo",
            )
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive(), "background thread leaked past the test")

            transcript = (
                root / AdvisoryTranscriptAndTelemetryTests.TRANSCRIPT_RELATIVE_PATH
            ).read_text()
            records = _read_jsonl(
                root / AdvisoryTranscriptAndTelemetryTests.TELEMETRY_RELATIVE_PATH
            )

        self.assertIn("sentinel-oops", transcript)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["task_id"], "post-mortem-crash-demo")
        self.assertEqual(records[0]["outcome"], "worker_error")

    def test_dispatch_post_mortem_consultation_rejects_bad_max_rounds_synchronously(
        self,
    ) -> None:
        """Design decision this ticket resolved: `max_rounds` is validated
        BEFORE the background thread is started, not inside it — a bad
        value is a call-site programming error
        (`run_advisory_consultation_debate` itself raises `ValueError` for
        it), and letting that raise happen only inside a background thread
        would turn it into a silent, uncaught thread exception instead of a
        `ValueError` the caller can actually catch. Proven here by asserting
        no thread is left running and the fake worker is never invoked."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            threads_before = threading.active_count()
            invoker = _RecordingInvoker([])

            with self.assertRaises(ValueError):
                advisory_consultation.dispatch_post_mortem_consultation(
                    "Post-mortem for a stalemate",
                    invoker,
                    root_dir=root,
                    max_rounds=0,
                )

            self.assertEqual(threading.active_count(), threads_before)
            self.assertEqual(invoker.calls, [])

    def test_dispatch_post_mortem_at_full_budget_exhaustion_skips_with_zero_invoker_calls(
        self,
    ) -> None:
        """Spec 0003 ticket 09 reaches the dispatch path too: the post-mortem
        occasion fires on every failure, escalation, and stalemate — exactly
        the sessions most likely to be deep into their dialogue budget — so
        `dispatch_post_mortem_consultation` must expose the same
        `session_spend_so_far` seam the synchronous entry point has, or
        post-mortems become the one unbudgetable occasion. At rung 3 the
        dispatched debate must skip entirely: zero worker contact, and a
        `budget_skipped` telemetry record still discoverable after the
        thread completes (degradation is never silent, even in the
        background)."""
        cap = advisory_consultation.DEFAULT_SESSION_DIALOGUE_CAP
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            invoker = _RecordingInvoker([])

            thread = advisory_consultation.dispatch_post_mortem_consultation(
                "Post-mortem for the auth rewrite failure",
                invoker,
                root_dir=root,
                session_spend_so_far=3 * cap,
            )
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive(), "background thread leaked past the test")

            records = _read_jsonl(
                root / AdvisoryTranscriptAndTelemetryTests.TELEMETRY_RELATIVE_PATH
            )

        self.assertEqual(invoker.calls, [])
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["outcome"], "budget_skipped")
        self.assertEqual(records[0]["degradation_rung"], 3)
        self.assertEqual(records[0]["occasion"], "post-mortem")

    def test_dispatch_post_mortem_at_a_lower_rung_observably_reduces_rounds(
        self,
    ) -> None:
        """The ladder's milder rungs thread through the dispatch path too,
        not only the skip rung: a spend at exactly one cap places the
        dispatched debate at rung 1, whose reduced round cap is observable
        the same way `test_rung_one_reduces_effective_round_cap_observable_via_fewer_invoker_calls`
        proves it for the synchronous path — six responses scripted, only
        one round's two consumed before the stalemate."""
        cap = advisory_consultation.DEFAULT_SESSION_DIALOGUE_CAP
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            invoker = _RecordingInvoker(
                [
                    "Planner's first lesson.",
                    _revise("Needs more detail."),
                    "Planner's second lesson.",
                    _revise("Still thin."),
                    "Planner's third lesson.",
                    _revise("Not convinced."),
                ]
            )

            thread = advisory_consultation.dispatch_post_mortem_consultation(
                "Post-mortem for the auth rewrite failure",
                invoker,
                root_dir=root,
                max_rounds=3,
                session_spend_so_far=cap,
            )
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive(), "background thread leaked past the test")

            records = _read_jsonl(
                root / AdvisoryTranscriptAndTelemetryTests.TELEMETRY_RELATIVE_PATH
            )

        self.assertEqual(len(invoker.calls), 2)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["outcome"], "stalemate")
        self.assertEqual(records[0]["degradation_rung"], 1)
        self.assertEqual(records[0]["rounds_run"], 1)


class AgentCouncilSignatureApiTests(unittest.TestCase):
    """Public key-loading and signature APIs fail closed for auditors."""

    @staticmethod
    def _unsigned_manifest() -> dict[str, str]:
        return {
            "task_id": "signature-api-task",
            "task": "Verify shared calibration API",
            "complexity": "medium",
            "effort": "high",
            "decision": "APPROVED",
            "nonce": "0123456789abcdef0123456789abcdef",
        }

    def test_load_secret_is_read_only_by_default_and_can_create_explicitly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            key_file = root / ".ralph" / "cache" / "calibration.key"
            with self.assertRaises(FileNotFoundError):
                agent_council.AgentCouncil.load_secret(root)
            self.assertFalse(key_file.exists())

            generated = agent_council.AgentCouncil.load_secret(root, read_only=False)
            self.assertEqual(key_file.read_bytes(), generated)
            self.assertEqual(key_file.stat().st_mode & 0o777, 0o600)
            self.assertEqual(agent_council.AgentCouncil.load_secret(root), generated)

    def test_load_secret_rejects_symlinked_workspace_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            key_file = root / ".ralph" / "cache" / "calibration.key"
            key_file.parent.mkdir(parents=True)
            target = root / "secret-target"
            target.write_bytes(b"target-secret")
            key_file.symlink_to(target)

            with self.assertRaises(ValueError):
                agent_council.AgentCouncil.load_secret(root)

    def test_load_secret_rejects_oversized_workspace_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            key_file = root / ".ralph" / "cache" / "calibration.key"
            key_file.parent.mkdir(parents=True)
            key_file.write_bytes(b"x" * 4097)

            with self.assertRaises(ValueError):
                agent_council.AgentCouncil.load_secret(root)

    def test_verify_signature_and_validate_manifest_structure(self) -> None:
        secret = b"signature-api-secret"
        manifest = self._unsigned_manifest()
        manifest["signature"] = agent_council.AgentCouncil.generate_signature(
            manifest, secret
        )

        self.assertTrue(agent_council.AgentCouncil.validate_manifest_structure(manifest))
        self.assertTrue(agent_council.AgentCouncil.verify_signature(manifest, secret=secret))

        manifest["task"] = "Tampered task"
        self.assertTrue(agent_council.AgentCouncil.validate_manifest_structure(manifest))
        self.assertFalse(agent_council.AgentCouncil.verify_signature(manifest, secret=secret))

        malformed = dict(manifest)
        malformed.pop("nonce")
        self.assertFalse(agent_council.AgentCouncil.validate_manifest_structure(malformed))


class RoutingAuditEngineTests(unittest.TestCase):
    """Unit tests for the RoutingAuditEngine, LogParserAdapters, and PolicyEvaluator."""

    def setUp(self) -> None:
        self.audit_config = routing_check.AuditConfig(
            worker_patterns=[re.compile(r"^codex exec(?:\s|$)")],
            safe_patterns=[re.compile(r"^git status(?:\s|$)")],
            code_extensions=["py"],
        )

    def test_audit_issue_sorting_prioritizes_severity_then_discovery(self) -> None:
        issues = [
            routing_check.AuditIssue("warning", "early warning", 0),
            routing_check.AuditIssue("error", "later error", 3),
            routing_check.AuditIssue("error", "early error", 1),
            routing_check.AuditIssue("warning", "later warning", 2),
        ]

        self.assertEqual(
            [issue.message for issue in sorted(issues)],
            ["early error", "later error", "early warning", "later warning"],
        )

    def test_engine_audit_accepts_valid_synthetic_worker_step(self) -> None:
        step = routing_check.Step(
            7,
            "[ROUTING: Codex Sol — complexity: simple — effort: high — "
            "reason: implement feature]",
        )
        step.commands.append(
            "codex exec --model gpt-5.6-sol "
            '-c model_reasoning_effort="high" implement'
        )
        step.writes.append("src/feature.py")

        result = routing_check.RoutingAuditEngine(self.audit_config).audit([step])

        self.assertTrue(result.passed)
        self.assertEqual(result.errors, [])
        self.assertEqual(result.warnings, [])
        self.assertEqual(result.worker_calls, 1)
        self.assertTrue(result.has_worker_calls)
        self.assertEqual(result.total_steps, 1)

    def test_engine_audit_reports_synthetic_rule_failures(self) -> None:
        unrouted_write = routing_check.Step(
            10, "[ROUTING: Direct — reason: unauthorized source edit]"
        )
        unrouted_write.writes.append("src/direct.py")

        unsafe_execution = routing_check.Step(
            20, "[ROUTING: Direct — reason: unsafe execution]"
        )
        unsafe_execution.commands.append("python3 mutate.py")

        downgraded_worker = routing_check.Step(
            30,
            "[ROUTING: Codex Sol — complexity: complex — effort: high — "
            "reason: complex change]",
        )
        downgraded_worker.commands.append(
            "codex exec --model gpt-5.6-terra "
            '-c model_reasoning_effort="medium" implement'
        )

        malformed_declaration = routing_check.Step(
            40, "[ROUTING: codex exec without required fields]"
        )

        result = routing_check.RoutingAuditEngine(self.audit_config).audit(
            [
                unrouted_write,
                unsafe_execution,
                downgraded_worker,
                malformed_declaration,
            ]
        )

        self.assertFalse(result.passed)
        self.assertEqual(result.worker_calls, 1)
        self.assertTrue(result.has_worker_calls)
        self.assertEqual(result.total_steps, 4)
        self.assertTrue(
            any("unrouted code edit" in issue.message for issue in result.errors)
        )
        self.assertTrue(
            any("unsafe or unrouted command" in issue.message for issue in result.errors)
        )
        self.assertTrue(any("DEC-01" in issue.message for issue in result.errors))
        self.assertTrue(any("DEC-02" in issue.message for issue in result.errors))
        self.assertTrue(any("DEC-03" in issue.message for issue in result.errors))
        self.assertEqual(
            [issue.discovery_ordinal for issue in result.errors],
            sorted(issue.discovery_ordinal for issue in result.errors),
        )

    def test_engine_audit_clean_log_returns_report(self) -> None:
        engine = routing_check.RoutingAuditEngine()
        clean_log = SKILL_DIR / "tests" / "fixtures" / "clean_log.txt"
        report = engine.audit_log(clean_log)
        self.assertIsInstance(report, routing_check.AuditReport)
        self.assertEqual(report.exit_code, 0)
        self.assertEqual(len(report.violations), 0)

    def test_engine_audit_violation_log_returns_error_report(self) -> None:
        engine = routing_check.RoutingAuditEngine()
        violation_log = SKILL_DIR / "tests" / "fixtures" / "unrouted_mutation_log.txt"
        report = engine.audit_log(violation_log, strict=True)
        self.assertIsInstance(report, routing_check.AuditReport)
        self.assertEqual(report.exit_code, 1)

    def test_parser_adapter_selection(self) -> None:
        engine = routing_check.RoutingAuditEngine()
        text_parser = engine.get_parser("log.txt", "Step 1: text log")
        jsonl_parser = engine.get_parser("log.jsonl", '{"routing": "direct"}')
        self.assertIsInstance(text_parser, routing_check.TextLogParser)
        self.assertIsInstance(jsonl_parser, routing_check.JsonLinesLogParser)

    def test_engine_audit_agrees_with_compute_metrics_on_dec05_and_log01(self) -> None:
        """audit() must surface the same DEC-05/LOG-01 codes compute_metrics does.

        Reproduction: an unknown write tool (LOG-01) plus a malformed
        calibration header (DEC-05, since no secret is configured in the
        isolated root_dir) in the same step. Both codes reach
        `_analyze_step.issues`, but `audit()` historically only re-ran
        `_structural_issues`, silently dropping them.
        """
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            step = routing_check.Step(1, "[ROUTING: Direct — reason: test]")
            step.unknown_write_tools.append("apply_unreviewed_patch")
            step.calibration_headers.append("not-a-valid-hmac-signature")

            metrics = routing_check.compute_metrics(
                [step],
                self.audit_config.code_extensions,
                self.audit_config.worker_patterns,
                self.audit_config.safe_patterns,
                security_ctx=routing_check.SecurityContext.create(root_dir=tmp),
            )
            metrics_codes = {
                message.split()[0]
                for _, issues in metrics["violation_details"]
                for message in issues
            }
            self.assertIn("DEC-05", metrics_codes)
            self.assertIn("LOG-01", metrics_codes)

            engine = routing_check.RoutingAuditEngine(self.audit_config, root_dir=tmp)
            result = engine.audit([step])
            audit_codes = {
                issue.message.split(": ", 1)[1].split()[0] for issue in result.errors
            }

        self.assertEqual(audit_codes, metrics_codes)


class Phase1CharacterizationTests(unittest.TestCase):
    GOLDEN_PATH = SKILL_DIR / "test_outputs" / "characterization_golden.json"

    def setUp(self) -> None:
        if not self.GOLDEN_PATH.exists():
            self.fail("Golden file missing")
        self.golden = json.loads(self.GOLDEN_PATH.read_text())

    def _rebuild_steps(self):
        steps = []
        for fixture in self.golden["step_fixtures"]:
            step = routing_check.Step(fixture["index"], fixture["routing"])
            step.writes.extend(fixture["writes"])
            step.commands.extend(fixture["commands"])
            steps.append(step)
        return steps

    def test_characterize_routing_metrics_exact(self) -> None:
        steps = self._rebuild_steps()
        safe_patterns = [re.compile(p) for p in self.golden["safe_commands"]]
        metrics = routing_check.compute_metrics(
            steps,
            self.golden["code_extensions"],
            self.golden["worker_patterns"],
            safe_patterns,
            security_ctx=routing_check.SecurityContext.create(),
        )
        normalized = json.loads(json.dumps(metrics))
        self.assertEqual(normalized, self.golden["compute_metrics_output"])

    def test_characterize_audit_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "characterization_log.txt"
            log_path.write_text(self.golden["log_text"])
            result = run_check(str(log_path))
        self.assertEqual(result.stdout, self.golden["audit_output"]["stdout"])
        self.assertEqual(result.stderr, self.golden["audit_output"]["stderr"])
        self.assertEqual(result.returncode, self.golden["audit_output"]["returncode"])


class CanaryCadencePredicateTests(unittest.TestCase):
    """Spec 0003 (CriticalDialogue) ticket 08: `is_canary_dialogue` is a pure
    function over an injected dialogue-count/clock pair, config-driven
    (`canary_cadence.dialogues_per_canary` /
    `canary_cadence.seconds_between_canaries`) rather than a hardcoded
    literal — mirroring `needs_code_review_consultation`'s
    `_load_code_review_risk_config` pattern. It fires "whichever comes
    first": either boundary alone is sufficient.
    """

    def test_fires_exactly_at_the_dialogue_count_boundary(self) -> None:
        threshold = advisory_consultation.DEFAULT_CANARY_DIALOGUES_PER_CANARY
        self.assertFalse(
            advisory_consultation.is_canary_dialogue(threshold - 1, 0.0)
        )
        self.assertTrue(advisory_consultation.is_canary_dialogue(threshold, 0.0))

    def test_fires_exactly_at_the_weekly_time_boundary(self) -> None:
        threshold = advisory_consultation.DEFAULT_CANARY_SECONDS_BETWEEN_CANARIES
        self.assertFalse(
            advisory_consultation.is_canary_dialogue(0, threshold - 1)
        )
        self.assertTrue(advisory_consultation.is_canary_dialogue(0, threshold))

    def test_fires_when_both_boundaries_are_met(self) -> None:
        self.assertTrue(
            advisory_consultation.is_canary_dialogue(
                advisory_consultation.DEFAULT_CANARY_DIALOGUES_PER_CANARY,
                advisory_consultation.DEFAULT_CANARY_SECONDS_BETWEEN_CANARIES,
            )
        )

    def test_does_not_fire_when_neither_boundary_is_met(self) -> None:
        self.assertFalse(advisory_consultation.is_canary_dialogue(1, 1.0))
        self.assertFalse(advisory_consultation.is_canary_dialogue(5, 12345.0))

    def test_dialogue_count_threshold_is_read_from_injected_config_not_hardcoded(
        self,
    ) -> None:
        """Same proof style as `test_code_review_threshold_is_read_from_injected_config_not_hardcoded`:
        inject two different configs and observe the boolean answer flip for
        the exact same input, showing the value is genuinely read from
        config rather than merely referenced by key."""
        with tempfile.TemporaryDirectory() as tmp:
            low_config = Path(tmp) / "low.json"
            low_config.write_text(
                json.dumps({"canary_cadence": {"dialogues_per_canary": 3}})
            )
            high_config = Path(tmp) / "high.json"
            high_config.write_text(
                json.dumps({"canary_cadence": {"dialogues_per_canary": 3000}})
            )

            fires_low = advisory_consultation.is_canary_dialogue(
                5, 0.0, config_path=low_config
            )
            fires_high = advisory_consultation.is_canary_dialogue(
                5, 0.0, config_path=high_config
            )

        self.assertTrue(fires_low)
        self.assertFalse(fires_high)

    def test_seconds_threshold_is_read_from_injected_config_not_hardcoded(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            low_config = Path(tmp) / "low.json"
            low_config.write_text(
                json.dumps({"canary_cadence": {"seconds_between_canaries": 10}})
            )
            high_config = Path(tmp) / "high.json"
            high_config.write_text(
                json.dumps({"canary_cadence": {"seconds_between_canaries": 10_000_000}})
            )

            fires_low = advisory_consultation.is_canary_dialogue(
                0, 100.0, config_path=low_config
            )
            fires_high = advisory_consultation.is_canary_dialogue(
                0, 100.0, config_path=high_config
            )

        self.assertTrue(fires_low)
        self.assertFalse(fires_high)


class AdvisorySeededFlawCanaryTests(unittest.TestCase):
    """Spec 0003 (CriticalDialogue) ticket 08: `is_canary`/`canary_fixture`
    on `run_advisory_consultation_debate`. `is_canary` defaults to `False`
    and every pre-existing test in this file never mentions it, so the
    entire pre-existing suite is this ticket's regression guard that the
    opt-in changes nothing when a caller does not ask for it — mirroring
    ticket 07's identical `reachability_check` regression argument.
    """

    def test_canary_shows_the_critic_the_fixture_text_and_never_invokes_the_planner(
        self,
    ) -> None:
        fixture = advisory_consultation.CANARY_FIXTURES[0]
        invoker = _RecordingInvoker([_approve_fixture(fixture)])
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite",
                invoker,
                root_dir=root,
                is_canary=True,
                planner_model="Test Planner",
                critic_model="Test Critic",
            )

        self.assertEqual(len(invoker.calls), 1)
        called_model, _effort, prompt = invoker.calls[0]
        self.assertEqual(called_model, "Test Critic")
        self.assertNotEqual(called_model, "Test Planner")
        self.assertIn(fixture.plan_text, prompt)
        self.assertEqual(result.outcome, "canary")

    def test_canary_approval_is_recorded_as_a_miss(self) -> None:
        fixture = advisory_consultation.CANARY_FIXTURES[0]
        invoker = _RecordingInvoker([_approve_fixture(fixture)])
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite", invoker, root_dir=root, is_canary=True
            )

        self.assertEqual(result.outcome, "canary")
        self.assertEqual(result.canary_result, "miss")
        self.assertFalse(result.consensus_reached)

    def test_canary_objection_is_recorded_as_a_catch(self) -> None:
        invoker = _RecordingInvoker(
            [_revise("Missing lock around the write to the telemetry file.")]
        )
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite", invoker, root_dir=root, is_canary=True
            )

        self.assertEqual(result.outcome, "canary")
        self.assertEqual(result.canary_result, "catch")
        self.assertFalse(result.consensus_reached)

    def test_canary_unparseable_verdict_is_also_recorded_as_a_catch(self) -> None:
        """Per the ticket's own instructions: 'objecting (or any
        not-approved outcome) -> catch' — an unparseable response is not an
        approval, so it counts as a catch, not a third canary state."""
        invoker = _RecordingInvoker(["This plan looks fine, I guess."])
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite", invoker, root_dir=root, is_canary=True
            )

        self.assertEqual(result.outcome, "canary")
        self.assertEqual(result.canary_result, "catch")

    def test_canary_never_writes_implementation_plan_on_miss_or_catch(self) -> None:
        fixture = advisory_consultation.CANARY_FIXTURES[0]
        for critic_response, expected_canary_result in (
            (_approve_fixture(fixture), "miss"),
            (_revise("Objection."), "catch"),
        ):
            with self.subTest(expected_canary_result=expected_canary_result):
                invoker = _RecordingInvoker([critic_response])
                with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
                    os.environ, {}, clear=True
                ):
                    root = Path(tmp)
                    result = advisory_consultation.run_advisory_consultation_debate(
                        "Plan the auth rewrite",
                        invoker,
                        root_dir=root,
                        is_canary=True,
                    )
                    self.assertFalse((root / "implementation_plan.md").exists())
                self.assertEqual(result.canary_result, expected_canary_result)

    def test_canary_does_not_touch_a_pre_existing_real_plan_artifact(self) -> None:
        """Isolation: a canary run must not affect a real mission's
        consultation outcome. Run a real consensus dialogue first (which
        writes `implementation_plan.md`), then run a canary against the
        same root, and confirm the real plan file survives untouched — a
        canary must neither write NOR delete a mission's plan artifact."""
        fixture = advisory_consultation.CANARY_FIXTURES[0]
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            real_plan = "The real mission's agreed plan."
            real_invoker = _RecordingInvoker([real_plan, _approve(real_plan)])
            real_result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite", real_invoker, root_dir=root
            )
            self.assertTrue(real_result.consensus_reached)
            plan_path = root / "implementation_plan.md"
            self.assertEqual(plan_path.read_text(), real_plan)

            canary_invoker = _RecordingInvoker([_approve_fixture(fixture)])
            canary_result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the checkout rewrite",
                canary_invoker,
                root_dir=root,
                is_canary=True,
            )

            self.assertEqual(canary_result.outcome, "canary")
            self.assertEqual(plan_path.read_text(), real_plan)

    def test_canary_worker_error_fails_closed_without_touching_the_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            invoker = _RecordingInvoker([RuntimeError("critic unreachable")])
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite", invoker, root_dir=root, is_canary=True
            )
            self.assertFalse((root / "implementation_plan.md").exists())

        self.assertEqual(result.outcome, "worker_error")
        self.assertIsNone(result.canary_result)

    def test_canary_transcript_is_clearly_marked_and_not_read_as_a_normal_round(
        self,
    ) -> None:
        fixture = advisory_consultation.CANARY_FIXTURES[0]
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            invoker = _RecordingInvoker([_approve_fixture(fixture)])
            advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite", invoker, root_dir=root, is_canary=True
            )
            transcript = (root / ".scratch" / "planning_debate.md").read_text()

        self.assertIn(advisory_consultation.CANARY_MARKER, transcript)
        self.assertIn(fixture.id, transcript)
        self.assertIn("Outcome:** canary", transcript)
        self.assertIn(fixture.plan_text, transcript)

    def test_canary_telemetry_record_carries_the_canary_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            invoker = _RecordingInvoker([_revise("Objection.")])
            advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite", invoker, root_dir=root, is_canary=True
            )
            records = _read_jsonl(root / ".ralph" / "routing_telemetry.jsonl")

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["outcome"], "canary")
        self.assertEqual(records[0]["canary_result"], "catch")

    def test_explicit_canary_fixture_overrides_the_library_default(self) -> None:
        """Proves the fixture shown is genuinely injectable — the caller
        can assert 'the Critic was shown THIS specific known-flawed text'
        rather than merely 'some canary ran'."""
        custom_fixture = advisory_consultation.CanaryFixture(
            id="test-custom-fixture",
            flaw_summary="A deliberately planted test-only flaw.",
            plan_text="Custom fixture plan text unique to this test.",
        )
        invoker = _RecordingInvoker([_approve(custom_fixture.plan_text)])
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite",
                invoker,
                root_dir=root,
                is_canary=True,
                canary_fixture=custom_fixture,
            )
            transcript = (root / ".scratch" / "planning_debate.md").read_text()

        self.assertEqual(result.canary_result, "miss")
        self.assertIn(custom_fixture.plan_text, invoker.calls[0][2])
        self.assertIn("test-custom-fixture", transcript)

    def test_canary_still_respects_the_sensitivity_gate(self) -> None:
        """A canary must not bypass the sensitivity halt: the task text
        (not the fixture) still reaches the Critic prompt, so a sensitive
        task must still halt before any worker is contacted."""
        invoker = _RecordingInvoker([])
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite using api_key=sk-abc123",
                invoker,
                root_dir=root,
                is_canary=True,
            )

        self.assertEqual(result.outcome, "sensitivity_halt")
        self.assertEqual(invoker.calls, [])
        self.assertIsNone(result.canary_result)

    def test_canary_ignores_panel_topology_and_probes_a_single_critic(self) -> None:
        """Design decision: canaries default to the narrower, pair-mode-only
        scope regardless of what topology the occasion/complexity would
        otherwise select — a canary always probes exactly one Critic
        (`critic_model`/`critic_effort`), never both panel Critics. This
        proves that even for an occasion/complexity combination that would
        normally select the panel topology (`plan-review` + `complex`), a
        canary run invokes only `critic_model`, never `critic_b_model`, and
        reports `critic_model` (not `critic_a_model`) on the result."""
        fixture = advisory_consultation.CANARY_FIXTURES[0]
        invoker = _RecordingInvoker([_approve_fixture(fixture)])
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite",
                invoker,
                root_dir=root,
                occasion="plan-review",
                complexity="complex",
                is_canary=True,
                critic_model="Pair Critic",
                critic_a_model="Panel Critic A",
                critic_b_model="Panel Critic B",
            )

        self.assertEqual(len(invoker.calls), 1)
        self.assertEqual(invoker.calls[0][0], "Pair Critic")
        self.assertEqual(result.critic_model, "Pair Critic")
        self.assertEqual(result.outcome, "canary")

    def test_default_fixture_used_when_no_fixture_is_explicitly_supplied(self) -> None:
        invoker = _RecordingInvoker(
            [_approve_fixture(advisory_consultation.CANARY_FIXTURES[0])]
        )
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite", invoker, root_dir=root, is_canary=True
            )

        self.assertEqual(len(invoker.calls), 1)
        self.assertEqual(
            invoker.calls[0][2].count(advisory_consultation.CANARY_FIXTURES[0].plan_text),
            1,
        )

    def test_non_canary_dialogue_is_completely_unaffected_by_default(self) -> None:
        """Regression test: omitting `is_canary` (its default, `False`)
        must behave byte-for-byte as before this ticket — a real consensus
        run still invokes the Planner, writes the plan, and reports
        `canary_result=None`."""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            invoker = _RecordingInvoker(
                ["Planner's proposed plan.", _approve("Planner's proposed plan.")]
            )
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite", invoker, root_dir=root
            )
            self.assertEqual(
                (root / "implementation_plan.md").read_text(), "Planner's proposed plan."
            )

        self.assertEqual(len(invoker.calls), 2)
        self.assertTrue(result.consensus_reached)
        self.assertNotEqual(result.outcome, "canary")
        self.assertIsNone(result.canary_result)

    def test_canary_task_id_never_collides_with_the_real_missions_task_id(
        self,
    ) -> None:
        """A canary keeps the real `task_description` untouched (only the
        Planner's plan is substituted for a fixture), so without a
        dedicated fail-safe its default `task_id` would fall back to the
        same digest-of-task-description a real, non-canary dialogue over
        the identical task resolves to — silently colliding the canary's
        miss/catch into that mission's real telemetry stream for any
        consumer that groups or joins by `task_id` alone (spec 0004's
        future LearningJournal/scoreboard, most notably). Proves the two
        never collide: the real run's `task_id` is exactly the expected
        digest, and the canary's `task_id` is neither that digest nor equal
        to the real run's id."""
        task_description = "Plan the auth rewrite"
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            real_plan = "The real mission's agreed plan."
            real_invoker = _RecordingInvoker([real_plan, _approve(real_plan)])
            advisory_consultation.run_advisory_consultation_debate(
                task_description, real_invoker, root_dir=root
            )

            canary_invoker = _RecordingInvoker([_revise("Objection.")])
            advisory_consultation.run_advisory_consultation_debate(
                task_description, canary_invoker, root_dir=root, is_canary=True
            )

            records = _read_jsonl(root / ".ralph" / "routing_telemetry.jsonl")

        self.assertEqual(len(records), 2)
        real_task_id = records[0]["task_id"]
        canary_task_id = records[1]["task_id"]
        expected_real_digest = advisory_consultation._default_task_id(task_description)
        self.assertEqual(real_task_id, expected_real_digest)
        self.assertNotEqual(canary_task_id, expected_real_digest)
        self.assertNotEqual(canary_task_id, real_task_id)


class DialogueBudgetLadderTests(unittest.TestCase):
    """Spec 0003 (CriticalDialogue) ticket 09: `resolve_degradation_rung` is
    a pure function over a caller-tracked session-spend counter and the
    configured `dialogue_budget.session_dialogue_cap`, config-driven rather
    than a hardcoded literal -- mirroring `is_canary_dialogue`'s identical
    `_load_canary_cadence_config` pattern. Thresholds fall at multiples of
    the cap: spend under 1x the cap is rung 0, 1x-2x is rung 1, 2x-3x is
    rung 2, 3x and beyond is rung 3 -- see the module comment above
    `DegradationRung` for the full reasoning.
    """

    def test_well_under_the_cap_is_rung_zero(self) -> None:
        cap = advisory_consultation.DEFAULT_SESSION_DIALOGUE_CAP
        self.assertEqual(advisory_consultation.resolve_degradation_rung(0), 0)
        self.assertEqual(advisory_consultation.resolve_degradation_rung(cap // 2), 0)

    def test_fires_rung_one_exactly_at_the_cap_boundary(self) -> None:
        cap = advisory_consultation.DEFAULT_SESSION_DIALOGUE_CAP
        self.assertEqual(advisory_consultation.resolve_degradation_rung(cap - 1), 0)
        self.assertEqual(advisory_consultation.resolve_degradation_rung(cap), 1)

    def test_rung_one_holds_up_to_but_not_including_twice_the_cap(self) -> None:
        cap = advisory_consultation.DEFAULT_SESSION_DIALOGUE_CAP
        self.assertEqual(advisory_consultation.resolve_degradation_rung(2 * cap - 1), 1)
        self.assertEqual(advisory_consultation.resolve_degradation_rung(2 * cap), 2)

    def test_rung_two_holds_up_to_but_not_including_three_times_the_cap(self) -> None:
        cap = advisory_consultation.DEFAULT_SESSION_DIALOGUE_CAP
        self.assertEqual(advisory_consultation.resolve_degradation_rung(3 * cap - 1), 2)
        self.assertEqual(advisory_consultation.resolve_degradation_rung(3 * cap), 3)

    def test_rung_three_holds_for_any_spend_at_or_beyond_three_times_the_cap(self) -> None:
        cap = advisory_consultation.DEFAULT_SESSION_DIALOGUE_CAP
        self.assertEqual(advisory_consultation.resolve_degradation_rung(3 * cap), 3)
        self.assertEqual(advisory_consultation.resolve_degradation_rung(100 * cap), 3)

    def test_negative_spend_is_always_rung_zero(self) -> None:
        """A caller passing a negative spend is under budget by construction --
        `resolve_degradation_rung` never raises for it (see its docstring)."""
        self.assertEqual(advisory_consultation.resolve_degradation_rung(-5), 0)

    def test_cap_is_read_from_injected_config_not_hardcoded(self) -> None:
        """Same proof style as
        `CanaryCadencePredicateTests.test_dialogue_count_threshold_is_read_from_injected_config_not_hardcoded`:
        inject two different configs and observe the rung answer flip for
        the exact same spend, showing the cap is genuinely read from config
        rather than merely referenced by key."""
        with tempfile.TemporaryDirectory() as tmp:
            low_config = Path(tmp) / "low.json"
            low_config.write_text(
                json.dumps({"dialogue_budget": {"session_dialogue_cap": 2}})
            )
            high_config = Path(tmp) / "high.json"
            high_config.write_text(
                json.dumps({"dialogue_budget": {"session_dialogue_cap": 2000}})
            )

            rung_low = advisory_consultation.resolve_degradation_rung(
                5, config_path=low_config
            )
            rung_high = advisory_consultation.resolve_degradation_rung(
                5, config_path=high_config
            )

        self.assertEqual(rung_low, 2)  # 5 is >= 2*2 and < 3*2 -> rung 2
        self.assertEqual(rung_high, 0)

    def test_zero_cap_degenerates_to_always_rung_three(self) -> None:
        """A session with no budget at all has no room for any dialogue --
        see the module comment above `DegradationRung` for why this is the
        correct reading rather than a special case the function must guard."""
        with tempfile.TemporaryDirectory() as tmp:
            zero_config = Path(tmp) / "zero.json"
            zero_config.write_text(
                json.dumps({"dialogue_budget": {"session_dialogue_cap": 0}})
            )
            self.assertEqual(
                advisory_consultation.resolve_degradation_rung(0, config_path=zero_config),
                3,
            )
            self.assertEqual(
                advisory_consultation.resolve_degradation_rung(5, config_path=zero_config),
                3,
            )


class DegradedRosterModelTests(unittest.TestCase):
    """Spec 0003 (CriticalDialogue) ticket 09 (revised): `_load_degraded_roster_model`
    reads rung 2's substitute model from `routing-config.json`'s existing
    `light_doer` role block -- `light_doer.name` lists several
    interchangeable alternatives (e.g. "Codex 5.6 Terra / Luna / Gemini 3.6
    Flash (Low)"), and this function reads the first one, the same
    "first is primary" convention `DEFAULT_ROSTER_FALLBACK_CHAINS` already
    establishes for its own ordered tuples.
    """

    def test_reads_the_first_alternative_from_light_doer_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            config_path.write_text(
                json.dumps({"light_doer": {"name": "Model A / Model B / Model C"}})
            )
            self.assertEqual(
                advisory_consultation._load_degraded_roster_model(config_path),
                "Model A",
            )

    def test_falls_back_to_default_when_light_doer_section_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            config_path.write_text(json.dumps({}))
            self.assertEqual(
                advisory_consultation._load_degraded_roster_model(config_path),
                advisory_consultation._DEFAULT_DEGRADED_ROSTER_MODEL,
            )

    def test_falls_back_to_default_when_the_first_alternative_is_blank(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            config_path.write_text(
                json.dumps({"light_doer": {"name": " / Model B"}})
            )
            self.assertEqual(
                advisory_consultation._load_degraded_roster_model(config_path),
                advisory_consultation._DEFAULT_DEGRADED_ROSTER_MODEL,
            )

    def test_checked_in_config_resolves_to_codex_terra(self) -> None:
        """Pins the checked-in `routing-config.json`'s current
        `light_doer.name` value so a future edit to that block is caught
        here rather than silently changing what rung 2 substitutes."""
        self.assertEqual(
            advisory_consultation._load_degraded_roster_model(
                advisory_consultation._CONFIG_PATH
            ),
            "Codex 5.6 Terra",
        )


class AdvisoryBudgetDegradationTests(unittest.TestCase):
    """Spec 0003 (CriticalDialogue) ticket 09: `session_spend_so_far`/
    `budget_config_path` wired into `run_advisory_consultation_debate`.
    `session_spend_so_far` defaults to `0`, which always resolves to rung 0
    for any positive configured cap, so every pre-existing test in this
    file never mentions this parameter and continues to invoke exactly the
    rounds/effort it always did -- the entire pre-existing suite is this
    ticket's regression guard that the opt-in changes nothing when a caller
    does not ask for it, mirroring ticket 07's and ticket 08's identical
    regression argument for their own opt-in seams.

    The checked-in `routing-config.json` sets `dialogue_budget.session_dialogue_cap`
    to `10`, matching `DEFAULT_SESSION_DIALOGUE_CAP` exactly -- these tests
    read `DEFAULT_SESSION_DIALOGUE_CAP` rather than hardcoding `10`, so they
    stay correct even if that checked-in value and the default are ever
    changed together.
    """

    def test_normal_run_carries_no_degradation_marker_and_reports_rung_zero(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            invoker = _RecordingInvoker(
                ["Planner's plan.", _approve("Planner's plan.")]
            )
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite",
                invoker,
                root_dir=root,
                session_spend_so_far=0,
            )
            transcript = (root / ".scratch" / "planning_debate.md").read_text()
            records = _read_jsonl(root / ".ralph" / "routing_telemetry.jsonl")

        self.assertEqual(result.degradation_rung, 0)
        self.assertTrue(result.consensus_reached)
        self.assertNotIn(advisory_consultation.BUDGET_DEGRADATION_MARKER, transcript)
        self.assertEqual(records[0]["degradation_rung"], 0)

    def test_sensitivity_halt_takes_priority_over_the_budget_ladder(self) -> None:
        """The sensitivity gate 'still precedes everything' (spec 0003's own
        phrase) -- a session deep into budget exhaustion must still halt on
        sensitive task text before the budget ladder is ever consulted, and
        report no degradation for a dialogue that never ran, exactly like
        `AdvisoryRosterIntegrationTests` already proves for roster
        resolution."""
        cap = advisory_consultation.DEFAULT_SESSION_DIALOGUE_CAP
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            invoker = _RecordingInvoker([])
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite using api_key=sk-abc123",
                invoker,
                root_dir=root,
                session_spend_so_far=3 * cap,
            )

        self.assertEqual(result.outcome, "sensitivity_halt")
        self.assertEqual(result.degradation_rung, 0)
        self.assertEqual(invoker.calls, [])

    def test_rung_one_reduces_effective_round_cap_observable_via_fewer_invoker_calls(
        self,
    ) -> None:
        cap = advisory_consultation.DEFAULT_SESSION_DIALOGUE_CAP
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            invoker = _RecordingInvoker(
                [
                    "Planner's first plan.",
                    _revise("Needs more detail."),
                    "Planner's second plan.",
                    _revise("Still thin."),
                    "Planner's third plan.",
                    _revise("Not convinced."),
                ]
            )
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite",
                invoker,
                root_dir=root,
                max_rounds=3,
                session_spend_so_far=cap,
            )
            transcript = (root / ".scratch" / "planning_debate.md").read_text()

        self.assertEqual(result.degradation_rung, 1)
        self.assertEqual(result.rounds_run, 1)
        self.assertLess(result.rounds_run, 3)
        self.assertEqual(len(invoker.calls), 2)
        self.assertEqual(result.outcome, "stalemate")
        self.assertIn(advisory_consultation.BUDGET_DEGRADATION_MARKER, transcript)

    def test_rung_one_reduces_rounds_in_panel_topology_too(self) -> None:
        """The round reduction is a plain `max_rounds` reassignment the
        round loop already reads regardless of topology -- proves it holds
        for the panel loop (spec 0003 ticket 05) as well as the pair loop."""
        cap = advisory_consultation.DEFAULT_SESSION_DIALOGUE_CAP
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            invoker = _RoleKeyedInvoker(
                {
                    "Claude Opus 5 (Thinking)": ["Planner's plan."],
                    "Codex 5.6 Sol": [_revise("Critic A objects.")],
                    "Gemini 3.6 Flash": [_revise("Critic B objects.")],
                }
            )
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite",
                invoker,
                root_dir=root,
                occasion="plan-review",
                complexity="complex",
                max_rounds=3,
                session_spend_so_far=cap,
            )

        self.assertEqual(result.degradation_rung, 1)
        self.assertEqual(result.rounds_run, 1)
        self.assertEqual(len(invoker.calls), 3)
        self.assertEqual(result.outcome, "stalemate")

    def test_rung_two_cheapens_effort_observable_in_invoker_calls(self) -> None:
        cap = advisory_consultation.DEFAULT_SESSION_DIALOGUE_CAP
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            invoker = _RecordingInvoker(["Planner's plan.", _revise("Not convinced.")])
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite",
                invoker,
                root_dir=root,
                max_rounds=3,
                planner_effort="high",
                critic_effort="high",
                session_spend_so_far=2 * cap,
            )

        self.assertEqual(result.degradation_rung, 2)
        # Rungs compound: rung 2 still carries rung 1's reduced round cap.
        self.assertEqual(result.rounds_run, 1)
        self.assertEqual(len(invoker.calls), 2)
        efforts = {effort for _model, effort, _prompt in invoker.calls}
        self.assertEqual(efforts, {advisory_consultation._DEGRADED_EFFORT})
        self.assertNotIn("high", efforts)

    def test_rung_two_changes_both_effort_and_model_sent_to_invoke_worker(
        self,
    ) -> None:
        """Ticket 09 (revised): the ticket's own 'What to build' prose is
        specific -- rung 2 must 'cheapen the roster (e.g. fall back toward
        lighter/local families)', not only lower effort. This proves the
        `model` argument `invoke_worker` actually receives changes too, to
        `_load_degraded_roster_model`'s substitute (drawn from
        `routing-config.json`'s `light_doer` block), never the caller's own
        explicit `planner_model`/`critic_model`."""
        cap = advisory_consultation.DEFAULT_SESSION_DIALOGUE_CAP
        expected_model = advisory_consultation._load_degraded_roster_model(
            advisory_consultation._CONFIG_PATH
        )
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            invoker = _RecordingInvoker(["Planner's plan.", _revise("Not convinced.")])
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite",
                invoker,
                root_dir=root,
                max_rounds=3,
                planner_model="Claude Opus 5 (Thinking)",
                critic_model="Codex 5.6 Sol",
                planner_effort="high",
                critic_effort="high",
                session_spend_so_far=2 * cap,
            )

        self.assertEqual(result.degradation_rung, 2)
        self.assertNotEqual(expected_model, "Claude Opus 5 (Thinking)")
        self.assertNotEqual(expected_model, "Codex 5.6 Sol")
        self.assertEqual(len(invoker.calls), 2)
        for model, effort, _prompt in invoker.calls:
            self.assertEqual(model, expected_model)
            self.assertEqual(effort, advisory_consultation._DEGRADED_EFFORT)
        self.assertEqual(result.planner_model, expected_model)
        self.assertEqual(result.critic_model, expected_model)

    def test_rung_two_model_override_wins_over_an_already_resolved_roster(
        self,
    ) -> None:
        """Rung 2's model substitution is applied AFTER roster resolution
        specifically so it wins even when `reachability_check` also
        resolved a roster for this call -- budget exhaustion is a
        stronger, later-stage override than family independence. Proves
        that every `invoke_worker` call carries the degraded model, not
        whatever `resolve_roster` would otherwise have picked — and that
        the resulting family collapse is reported rather than denied:
        one substituted model in every seat is a single-family roster by
        construction, so `degraded_independence` must read True (spec
        0003 story 14) even though `resolve_roster` itself had three
        distinct reachable families to work with."""
        cap = advisory_consultation.DEFAULT_SESSION_DIALOGUE_CAP
        expected_model = advisory_consultation._load_degraded_roster_model(
            advisory_consultation._CONFIG_PATH
        )
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            invoker = _RecordingInvoker(["Planner's plan.", _revise("Not convinced.")])
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite",
                invoker,
                root_dir=root,
                max_rounds=3,
                reachability_check=_reachable("claude", "codex-gpt", "gemini"),
                session_spend_so_far=2 * cap,
            )

        self.assertEqual(result.degradation_rung, 2)
        called_models = {model for model, _e, _p in invoker.calls}
        self.assertEqual(called_models, {expected_model})
        self.assertTrue(result.degraded_independence)
        self.assertEqual(result.planner_model, expected_model)
        self.assertEqual(result.critic_model, expected_model)

    def test_rung_two_still_reaches_consensus_when_the_critic_approves_round_one(
        self,
    ) -> None:
        """Degradation limits retries, it does not block a genuine consensus
        that arrives on the first (and, at this rung, only) round."""
        cap = advisory_consultation.DEFAULT_SESSION_DIALOGUE_CAP
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            plan = "Planner's plan."
            invoker = _RecordingInvoker([plan, _approve(plan)])
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite",
                invoker,
                root_dir=root,
                session_spend_so_far=2 * cap,
            )
            written_plan = (root / "implementation_plan.md").read_text()

        self.assertEqual(result.degradation_rung, 2)
        self.assertTrue(result.consensus_reached)
        self.assertEqual(written_plan, plan)

    def test_rung_three_skips_the_dialogue_entirely_with_zero_worker_calls(
        self,
    ) -> None:
        cap = advisory_consultation.DEFAULT_SESSION_DIALOGUE_CAP
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            invoker = _RecordingInvoker([])
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite",
                invoker,
                root_dir=root,
                session_spend_so_far=3 * cap,
            )
            transcript = (root / ".scratch" / "planning_debate.md").read_text()
            records = _read_jsonl(root / ".ralph" / "routing_telemetry.jsonl")

        self.assertEqual(invoker.calls, [])
        self.assertEqual(result.outcome, "budget_skipped")
        self.assertEqual(result.degradation_rung, 3)
        self.assertFalse(result.consensus_reached)
        self.assertEqual(result.final_plan, "")
        self.assertFalse((root / "implementation_plan.md").exists())
        self.assertIn(advisory_consultation.BUDGET_DEGRADATION_MARKER, transcript)
        self.assertIn("Outcome:** budget_skipped", transcript)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["outcome"], "budget_skipped")
        self.assertEqual(records[0]["degradation_rung"], 3)

    def test_rung_three_removes_a_pre_existing_stale_plan_artifact(self) -> None:
        cap = advisory_consultation.DEFAULT_SESSION_DIALOGUE_CAP
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            plan_path = root / "implementation_plan.md"
            plan_path.write_text("stale plan from an earlier run")

            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite",
                _RecordingInvoker([]),
                root_dir=root,
                session_spend_so_far=3 * cap,
            )

            self.assertFalse(plan_path.exists())
        self.assertEqual(result.outcome, "budget_skipped")

    def test_rung_three_preempts_an_is_canary_call_with_zero_invoker_calls(
        self,
    ) -> None:
        """Design decision: a fully exhausted budget skips the dialogue
        unconditionally, even for a call that also opted into `is_canary` --
        a canary probe still contacts a real Critic, and rung 3 exists
        specifically to guarantee zero worker contact this call. See this
        ticket's report for why the two seams compose this way rather than
        canaries bypassing the budget ladder."""
        cap = advisory_consultation.DEFAULT_SESSION_DIALOGUE_CAP
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            invoker = _RecordingInvoker([])
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite",
                invoker,
                root_dir=root,
                is_canary=True,
                session_spend_so_far=3 * cap,
            )

        self.assertEqual(result.outcome, "budget_skipped")
        self.assertNotEqual(result.outcome, "canary")
        self.assertIsNone(result.canary_result)
        self.assertEqual(invoker.calls, [])

    def test_budget_config_path_is_genuinely_injected_end_to_end(self) -> None:
        """Same proof style as the roster/canary config-injection tests:
        point `budget_config_path` at a small-cap config and observe the
        end-to-end outcome flip to `budget_skipped` for a spend that the
        checked-in `routing-config.json` (cap 10) would not skip at all."""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            small_cap_config = Path(tmp) / "small_cap.json"
            small_cap_config.write_text(
                json.dumps({"dialogue_budget": {"session_dialogue_cap": 1}})
            )
            invoker = _RecordingInvoker([])
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite",
                invoker,
                root_dir=root,
                session_spend_so_far=3,
                budget_config_path=small_cap_config,
            )

        self.assertEqual(result.outcome, "budget_skipped")
        self.assertEqual(result.degradation_rung, 3)
        self.assertEqual(invoker.calls, [])

    def test_a_caller_walking_session_spend_through_the_ladder_sees_progressively_worse_rungs(
        self,
    ) -> None:
        """Characterizes the caller-tracked pattern the ticket's 'A session
        tracks cumulative dialogue spend against the configured budget'
        criterion describes. This module holds no state of its own (see the
        module comment above `DegradationRung`), so this test plays the
        caller: it increments its own counter across successive calls and
        observes the rung climb the ladder exactly as
        `resolve_degradation_rung` documents, all the way to the rung-3
        skip."""
        cap = advisory_consultation.DEFAULT_SESSION_DIALOGUE_CAP
        spends_and_expected_rungs = [
            (0, 0),
            (cap - 1, 0),
            (cap, 1),
            (2 * cap - 1, 1),
            (2 * cap, 2),
            (3 * cap - 1, 2),
            (3 * cap, 3),
        ]
        observed_rungs = []
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            for session_spend, _expected in spends_and_expected_rungs:
                if session_spend < 3 * cap:
                    plan = "Planner's plan."
                    invoker: _RecordingInvoker = _RecordingInvoker(
                        [plan, _approve(plan)]
                    )
                else:
                    invoker = _RecordingInvoker([])
                result = advisory_consultation.run_advisory_consultation_debate(
                    "Plan the auth rewrite",
                    invoker,
                    root_dir=root,
                    session_spend_so_far=session_spend,
                )
                observed_rungs.append(result.degradation_rung)

        self.assertEqual(
            observed_rungs, [expected for _spend, expected in spends_and_expected_rungs]
        )

    def test_rung_two_reports_degraded_independence_in_result_and_telemetry(
        self,
    ) -> None:
        """Spec 0003 story 14: a same-family fallback must be recorded as
        degraded independence, whatever mechanism caused it. Rung 2
        substitutes one model into every seat — a single-family roster by
        construction, one model reviewing its own plan — so the flag must
        read True on the result and in the telemetry record even when no
        `reachability_check` was supplied at all (mirrors
        `test_degraded_independence_surfaces_in_telemetry_record`, the
        ticket-07 roster-path version of this same assertion)."""
        cap = advisory_consultation.DEFAULT_SESSION_DIALOGUE_CAP
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            plan = "Planner's plan."
            invoker = _RecordingInvoker([plan, _approve(plan)])
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite",
                invoker,
                root_dir=root,
                session_spend_so_far=2 * cap,
            )
            records = _read_jsonl(root / ".ralph" / "routing_telemetry.jsonl")

        self.assertEqual(result.degradation_rung, 2)
        self.assertTrue(result.degraded_independence)
        self.assertEqual(len(records), 1)
        self.assertTrue(records[0]["degraded_independence"])

    def test_rung_two_reports_degraded_independence_in_transcript_text(self) -> None:
        """The transcript half of story 14: a rung-2 dialogue's transcript
        carries the exact same degraded-independence line the roster path
        emits — the flag flows through `_result` into the one rendering
        `DEGRADED_INDEPENDENCE_MARKER` already gates on, never a second,
        rung-2-only rendering (mirrors
        `test_degraded_independence_surfaces_in_transcript_text`)."""
        cap = advisory_consultation.DEFAULT_SESSION_DIALOGUE_CAP
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            plan = "Planner's plan."
            invoker = _RecordingInvoker([plan, _approve(plan)])
            advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite",
                invoker,
                root_dir=root,
                session_spend_so_far=2 * cap,
            )
            transcript = (root / ".scratch" / "planning_debate.md").read_text()

        self.assertIn(advisory_consultation.DEGRADED_INDEPENDENCE_MARKER, transcript)

    def test_rung_two_reports_degraded_independence_in_panel_topology_too(
        self,
    ) -> None:
        """Panel mode collapses three seats — Planner, Critic A, Critic B —
        into the one substituted model at rung 2, so the single-family-by-
        construction argument holds there identically: both Critics are the
        same model as the Planner whose plan they judge, and the flag must
        say so."""
        cap = advisory_consultation.DEFAULT_SESSION_DIALOGUE_CAP
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            plan = "Planner's plan."
            invoker = _RecordingInvoker(
                [
                    plan,
                    _approve(plan, "Critic A: solid."),
                    _approve(plan, "Critic B: solid."),
                ]
            )
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite",
                invoker,
                root_dir=root,
                occasion="plan-review",
                complexity="complex",
                session_spend_so_far=2 * cap,
            )

        self.assertEqual(result.degradation_rung, 2)
        self.assertEqual(result.topology, "panel")
        self.assertEqual(len(invoker.calls), 3)
        self.assertTrue(result.degraded_independence)


class AdvisoryTelemetryExtensionsTests(unittest.TestCase):
    """Spec 0003 (CriticalDialogue) ticket 10: `AdvisoryTelemetryRecord`
    gains occasion, topology, per-round verdict sequence, and per-round
    engagement-unit counts — the fields tickets 01/05 already put on
    `AdvisoryDebateResult` (occasion, topology) or already compute and
    discard mid-loop (`_parse_critic_verdict`'s `VerdictContractResult`
    per round), now retained and threaded through to the telemetry record.
    Every pre-ticket-10 telemetry test in `AdvisoryTranscriptAndTelemetryTests`,
    `AdvisorySeededFlawCanaryTests`, and `AdvisoryBudgetDegradationTests`
    keeps passing unmodified — this class only adds coverage, per the
    append-only convention every prior ticket in this module already set.
    """

    TELEMETRY_RELATIVE_PATH = Path(".ralph") / "routing_telemetry.jsonl"

    def test_pair_mode_telemetry_carries_occasion_and_pair_topology(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            plan = "Planner's plan."
            invoker = _RecordingInvoker([plan, _approve(plan)])
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite",
                invoker,
                root_dir=root,
                occasion="plan-review",
            )
            records = _read_jsonl(root / self.TELEMETRY_RELATIVE_PATH)

        self.assertEqual(result.topology, "pair")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["occasion"], "plan-review")
        self.assertEqual(records[0]["topology"], "pair")

    def test_panel_mode_telemetry_carries_occasion_and_panel_topology(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            plan = "Planner's proposed plan."
            invoker = _RoleKeyedInvoker(
                {
                    "Test Planner": [plan],
                    "Test Critic A": [_approve(plan, "Critic A: solid.")],
                    "Test Critic B": [_approve(plan, "Critic B: solid.")],
                }
            )
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite",
                invoker,
                root_dir=root,
                occasion="plan-review",
                complexity="complex",
                planner_model="Test Planner",
                critic_a_model="Test Critic A",
                critic_b_model="Test Critic B",
            )
            records = _read_jsonl(root / self.TELEMETRY_RELATIVE_PATH)

        self.assertEqual(result.topology, "panel")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["occasion"], "plan-review")
        self.assertEqual(records[0]["topology"], "panel")

    def test_pair_mode_two_round_telemetry_carries_a_two_element_round_sequence(
        self,
    ) -> None:
        """A two-round pair consultation's telemetry carries exactly two
        per-round entries, each holding one verdict plus its own engagement
        counts — never a single flattened tally across both rounds."""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            first_plan = "Planner's first plan."
            second_plan = "Planner's revised plan."
            invoker = _RecordingInvoker(
                [
                    first_plan,
                    _revise("Needs more detail."),
                    second_plan,
                    _approve(second_plan, "Good now."),
                ]
            )
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite", invoker, root_dir=root
            )
            records = _read_jsonl(root / self.TELEMETRY_RELATIVE_PATH)

        self.assertEqual(result.rounds_run, 2)
        self.assertEqual(len(result.round_verdicts), 2)

        round_verdicts = records[0]["round_verdicts"]
        self.assertEqual(len(round_verdicts), 2)

        first_round, second_round = round_verdicts
        self.assertEqual(first_round["critic_a"]["verdict"], "revise")
        self.assertEqual(first_round["critic_a"]["verified_quote_count"], 0)
        self.assertEqual(first_round["critic_a"]["objection_count"], 0)
        self.assertIsNone(first_round["critic_b"])

        self.assertEqual(second_round["critic_a"]["verdict"], "approved")
        self.assertEqual(second_round["critic_a"]["verified_quote_count"], 1)
        self.assertEqual(second_round["critic_a"]["objection_count"], 0)
        self.assertIsNone(second_round["critic_b"])

    def test_panel_mode_round_telemetry_carries_both_critics_distinguishably(
        self,
    ) -> None:
        """A panel round's per-round element holds BOTH Critics' verdicts and
        counts, and the two are distinguishable from one another — not
        folded into one shared tally."""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            first_plan = "Planner's first plan."
            second_plan = "Planner's revised plan."
            invoker = _RoleKeyedInvoker(
                {
                    "Test Planner": [first_plan, second_plan],
                    "Test Critic A": [
                        _approve(first_plan, "A: fine as-is."),
                        _approve(second_plan, "A: still fine."),
                    ],
                    "Test Critic B": [
                        _revise("B: needs a rollback plan."),
                        _approve(second_plan, "B: rollback addressed."),
                    ],
                }
            )
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite",
                invoker,
                root_dir=root,
                occasion="plan-review",
                complexity="complex",
                planner_model="Test Planner",
                critic_a_model="Test Critic A",
                critic_b_model="Test Critic B",
            )
            records = _read_jsonl(root / self.TELEMETRY_RELATIVE_PATH)

        self.assertEqual(result.rounds_run, 2)
        round_verdicts = records[0]["round_verdicts"]
        self.assertEqual(len(round_verdicts), 2)

        first_round, second_round = round_verdicts
        self.assertEqual(first_round["critic_a"]["verdict"], "approved")
        self.assertEqual(first_round["critic_a"]["verified_quote_count"], 1)
        self.assertEqual(first_round["critic_b"]["verdict"], "revise")
        self.assertEqual(first_round["critic_b"]["verified_quote_count"], 0)
        self.assertNotEqual(
            first_round["critic_a"]["verdict"], first_round["critic_b"]["verdict"]
        )

        self.assertEqual(second_round["critic_a"]["verdict"], "approved")
        self.assertEqual(second_round["critic_b"]["verdict"], "approved")

    def test_canary_round_verdicts_carries_the_single_critic_verdict_and_pair_topology(
        self,
    ) -> None:
        """A canary probes exactly one Critic (ticket 08) and never runs a
        real Planner round. Its `round_verdicts` still carries that one
        Critic's verdict+counts (critic_b stays None, same pair-mode shape a
        real pair round uses) and `topology` reports "pair" — never "panel"
        — even under an occasion/complexity combination that would
        otherwise select a panel, because a canary genuinely never invokes a
        second Critic. `canary_result` (ticket 08) remains the authoritative
        miss/catch summary; `round_verdicts` is the same generic verdict
        data every other outcome carries, not a competing signal."""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            invoker = _RecordingInvoker([_revise("Objection.")])
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite",
                invoker,
                root_dir=root,
                occasion="plan-review",
                complexity="complex",
                is_canary=True,
            )
            records = _read_jsonl(root / self.TELEMETRY_RELATIVE_PATH)

        self.assertEqual(result.outcome, "canary")
        self.assertEqual(result.canary_result, "catch")
        self.assertEqual(result.topology, "pair")
        self.assertEqual(len(result.round_verdicts), 1)

        record = records[0]
        self.assertEqual(record["topology"], "pair")
        self.assertEqual(record["canary_result"], "catch")
        self.assertEqual(len(record["round_verdicts"]), 1)
        self.assertEqual(record["round_verdicts"][0]["critic_a"]["verdict"], "revise")
        self.assertIsNone(record["round_verdicts"][0]["critic_b"])

    def test_stalemate_and_worker_error_and_sensitivity_halt_still_carry_topology(
        self,
    ) -> None:
        """Every outcome — not just consensus/canary — carries a topology,
        since `_is_panel_topology` is resolved unconditionally at the top of
        the function, before the sensitivity gate, the budget check, or any
        worker is ever contacted."""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            halt_result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the rollout, password=hunter2",
                _RecordingInvoker([]),
                root_dir=root,
            )

        self.assertEqual(halt_result.outcome, "sensitivity_halt")
        self.assertEqual(halt_result.topology, "pair")
        self.assertEqual(halt_result.round_verdicts, ())

    def test_pre_ticket_10_direct_construction_still_defaults_correctly(self) -> None:
        """Mirrors `test_occasion_field_defaults_to_ambiguity_on_direct_construction`:
        a pre-ticket-10 direct `AdvisoryDebateResult(...)`/`AdvisoryTelemetryRecord(...)`
        construction that never mentions `topology` or `round_verdicts` must
        keep meaning exactly what it meant before those fields existed."""
        result = advisory_consultation.AdvisoryDebateResult(
            rounds_run=1, final_plan="A plan.", outcome="consensus"
        )
        self.assertEqual(result.topology, "pair")
        self.assertEqual(result.round_verdicts, ())

        record = advisory_consultation.AdvisoryTelemetryRecord(
            timestamp="2026-01-01T00:00:00Z",
            task_id="abc123",
            rounds_run=1,
            outcome="consensus",
            planner_model="Test Planner",
            critic_model="Test Critic",
        )
        self.assertEqual(record.occasion, "ambiguity")
        self.assertEqual(record.topology, "pair")
        self.assertEqual(record.round_verdicts, ())

    def test_round_verdicts_carry_no_substring_of_a_distinctive_task_description(
        self,
    ) -> None:
        """Redaction test (ticket 10's own acceptance criterion): the
        per-round telemetry data is verdicts and engagement-unit counts
        only — never plan/critique prose, and never the task text or a
        substring of it. A distinctive task description makes an accidental
        leak (of the task text, or of the Planner/Critic prose that
        discusses it) trivially detectable rather than coincidentally
        matching short common words."""
        distinctive_task = (
            "Plan the ZEBRA-QUASAR-77 migration for the northwind-prod cluster"
        )
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            first_plan = "First plan mentioning ZEBRA-QUASAR-77 explicitly."
            second_plan = "Second plan, still about ZEBRA-QUASAR-77."
            invoker = _RecordingInvoker(
                [
                    first_plan,
                    _revise("ZEBRA-QUASAR-77 needs a rollback section."),
                    second_plan,
                    _approve(second_plan, "ZEBRA-QUASAR-77 rollback looks good now."),
                ]
            )
            advisory_consultation.run_advisory_consultation_debate(
                distinctive_task, invoker, root_dir=root
            )
            telemetry_text = (root / self.TELEMETRY_RELATIVE_PATH).read_text()
            records = _read_jsonl(root / self.TELEMETRY_RELATIVE_PATH)

        round_verdicts_json = json.dumps(records[0]["round_verdicts"])
        for leak in (distinctive_task, "ZEBRA-QUASAR-77", "rollback"):
            self.assertNotIn(leak, round_verdicts_json)
        # And the belt-and-suspenders whole-record check every other
        # redaction test in this file uses (see
        # `test_redaction_boundary_secret_reaches_neither_artifact`).
        self.assertNotIn(distinctive_task, telemetry_text)
        self.assertNotIn("ZEBRA-QUASAR-77", telemetry_text)

    def test_existing_spec_0001_telemetry_fields_are_unchanged(self) -> None:
        """Acceptance criterion: existing spec-0001 telemetry fields and
        their tests are unchanged. Same assertions as
        `test_telemetry_record_carries_task_identity_rounds_outcome_and_models`,
        re-run here as a ticket-10 characterization pin."""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            invoker = _RecordingInvoker(
                [
                    "Planner's first plan.",
                    _revise("Needs more detail."),
                    "Planner's revised plan.",
                    _approve("Planner's revised plan.", "Good now."),
                ]
            )
            advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite",
                invoker,
                root_dir=root,
                planner_model="Test Planner",
                critic_model="Test Critic",
                task_id="ticket-10-demo",
            )
            records = _read_jsonl(root / self.TELEMETRY_RELATIVE_PATH)

        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["task_id"], "ticket-10-demo")
        self.assertEqual(record["rounds_run"], 2)
        self.assertEqual(record["outcome"], "consensus")
        self.assertEqual(record["planner_model"], "Test Planner")
        self.assertEqual(record["critic_model"], "Test Critic")
        self.assertIn("timestamp", record)


if __name__ == "__main__":
    unittest.main()
