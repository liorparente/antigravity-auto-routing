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
import unittest
from pathlib import Path
from unittest import mock

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
        env["LEARNING_JOURNAL_ROOT"] = str(self.home_dir)
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
                ["Planner's proposed plan.", "VERDICT: APPROVE\nLooks solid."]
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
                    "VERDICT: REVISE\nNeeds more detail.",
                    "Planner's revised plan.",
                    "VERDICT: REVISE\nStill missing detail.",
                    "Planner's third plan.",
                    "VERDICT: REVISE\nNot convinced.",
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
                ["Planner's proposed plan.", "VERDICT: APPROVE\nLooks solid."]
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
                ["Planner's proposed plan.", "VERDICT: APPROVE\nLooks solid."]
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
                ["Planner's proposed plan.", "VERDICT: APPROVE\nLooks solid."]
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
                    "VERDICT: REVISE\nMissing a rollback strategy.",
                    "Planner's revised plan.",
                    "VERDICT: APPROVE\nRollback strategy addressed.",
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
                    "VERDICT: REVISE\nNeeds more detail.",
                    "Planner's second plan.",
                    "VERDICT: REVISE\nStill thin.",
                    "Planner's third plan.",
                    "VERDICT: APPROVE\nThis works.",
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
                    "VERDICT: REVISE\nNeeds more detail.",
                    "Planner's second plan.",
                    "VERDICT: APPROVE\nGood now.",
                ]
            )
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite", invoker, root_dir=root
            )

        self.assertEqual(len(result.rounds), 2)
        self.assertEqual(result.rounds[0].planner_proposal, "Planner's first plan.")
        self.assertEqual(
            result.rounds[0].critic_response, "VERDICT: REVISE\nNeeds more detail."
        )
        self.assertEqual(result.rounds[1].planner_proposal, "Planner's second plan.")
        self.assertEqual(
            result.rounds[1].critic_response, "VERDICT: APPROVE\nGood now."
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
                        responses.append(f"VERDICT: REVISE\nStill not good enough #{i + 1}.")
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
                    "VERDICT: REVISE\nNeeds more detail.",
                    "Planner's second plan.",
                    "VERDICT: REVISE\nStill thin.",
                    "Planner's third plan.",
                    "VERDICT: APPROVE\nThis works.",
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
                    "VERDICT: REVISE\nNeeds more detail.",
                    "Planner's second plan.",
                    "VERDICT: REVISE\nStill thin.",
                    "Planner's third plan.",
                    "VERDICT: REVISE\nNot convinced.",
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
                    "VERDICT: REVISE\nNeeds more detail.",
                    "Planner's final plan.",
                    "VERDICT: REVISE\nStill not convinced.",
                ]
            )
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite", invoker, root_dir=root, max_rounds=2
            )

        self.assertIsNotNone(result.stalemate)
        assert result.stalemate is not None
        self.assertEqual(result.stalemate.planner_position, "Planner's final plan.")
        self.assertEqual(
            result.stalemate.critic_position, "VERDICT: REVISE\nStill not convinced."
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
                ["Planner's plan.", "VERDICT: REVISE\nNot convinced."]
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
                    "VERDICT: REVISE\nNeeds more detail.",
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
                ["Planner's plan.", "VERDICT: REVISE\nNot convinced."]
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
                            "VERDICT: APPROVE\nGood now.",
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
                            "VERDICT: APPROVE\nGood now.",
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
                ["Planner's proposed plan.", "VERDICT: APPROVE\nLooks solid."]
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
                ["Planner's proposed plan.", "VERDICT: APPROVE\nLooks solid."]
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
                    "VERDICT: APPROVE\nLooks solid.",
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
                ["Planner's proposed plan.", "VERDICT: APPROVE\nLooks solid."]
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
        self.assertIn("VERDICT: APPROVE\nLooks solid.", transcript)

    def test_stalemate_writes_transcript_with_every_round_in_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            invoker = _RecordingInvoker(
                [
                    "Planner's first plan.",
                    "VERDICT: REVISE\nNeeds more detail.",
                    "Planner's second plan.",
                    "VERDICT: REVISE\nStill thin.",
                    "Planner's third plan.",
                    "VERDICT: REVISE\nNot convinced.",
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
                    "VERDICT: REVISE\nNeeds more detail.",
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
                    "VERDICT: REVISE\nNeeds more detail.",
                    "Planner's revised plan.",
                    "VERDICT: APPROVE\nGood now.",
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
                ["Planner's plan.", "VERDICT: APPROVE\nLooks solid."]
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
                    ["Planner's plan.", "VERDICT: APPROVE\nLooks solid."]
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
                ["First run's plan.", "VERDICT: APPROVE\nFirst run approved."]
            )
            advisory_consultation.run_advisory_consultation_debate(
                "Plan the first task", first_invoker, root_dir=root
            )
            second_invoker = _RecordingInvoker(
                ["Second run's plan.", "VERDICT: APPROVE\nSecond run approved."]
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
                ["Planner's plan.", "VERDICT: APPROVE\nLooks solid."]
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
                ["Planner's plan.", "VERDICT: APPROVE\nLooks solid."]
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
                ["Planner's plan.", "VERDICT: APPROVE\nLooks solid."]
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
                ["Planner's plan.", "VERDICT: APPROVE\nLooks solid."]
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
            invoker = _RecordingInvoker(["Plan.", "VERDICT: APPROVE\nGood."])
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


# Spec 0004 ticket 12 — the LearningJournal. Loaded here rather than beside
# the loaders at the top of this file so the ticket's additions are one
# contiguous, append-only block: this file is edited concurrently on other
# branches, and an insertion at the top is a merge conflict for everyone.
learning_journal_spec = importlib.util.spec_from_file_location(
    "learning_journal", SKILL_DIR / "learning_journal.py"
)
assert learning_journal_spec is not None and learning_journal_spec.loader is not None
learning_journal = importlib.util.module_from_spec(learning_journal_spec)
sys.modules["learning_journal"] = learning_journal
learning_journal_spec.loader.exec_module(learning_journal)


class LearningJournalTests(unittest.TestCase):
    """The journal records what happened without ever recording what it was about.

    Two properties carry this ticket, and both are asserted here as
    observable facts rather than trusted as conventions: every record lands
    in a stream separate from the audited routing telemetry yet joinable to
    it on TaskIdentity, and a record carrying task text, a path, or a matched
    secret cannot be constructed at all.
    """

    JOURNAL_RELATIVE_PATH = Path(".ralph") / "learning_journal.jsonl"
    TELEMETRY_RELATIVE_PATH = Path(".ralph") / "routing_telemetry.jsonl"

    FIXED_TIMESTAMP = "2026-08-12T09:30:00Z"

    def _worker_execution_record(self, **overrides: object) -> object:
        fields: dict[str, object] = {
            "task": learning_journal.TaskLabel.for_task("task-1", task_type="bugfix"),
            "duration_ms": 4200,
            "cost_estimate_usd": 0.0125,
            "success": True,
            "retry_count": 1,
            "effort": "high",
            "model_id": "claude-opus-5",
            "model_family": "claude",
            "timestamp": self.FIXED_TIMESTAMP,
        }
        fields.update(overrides)
        return learning_journal.WorkerExecutionRecord(**fields)  # type: ignore[arg-type]

    def _outcome_record(self, **overrides: object) -> object:
        fields: dict[str, object] = {
            "task": learning_journal.TaskLabel.for_task("graded-decision-1"),
            "signal": "tests",
            "verdict": "pass",
            "timestamp": self.FIXED_TIMESTAMP,
        }
        fields.update(overrides)
        return learning_journal.OutcomeRecord(**fields)  # type: ignore[arg-type]

    def _dialogue_quality_record(self, **overrides: object) -> object:
        fields: dict[str, object] = {
            "task": learning_journal.TaskLabel.for_task("task-1"),
            "occasion": "plan_review",
            "topology": "panel",
            "rounds": (
                learning_journal.DialogueRound("revise", 4),
                learning_journal.DialogueRound("approved", 2),
            ),
            "canaries_planted": 2,
            "canaries_caught": 1,
            "degraded": False,
            "independent": True,
            "timestamp": self.FIXED_TIMESTAMP,
        }
        fields.update(overrides)
        return learning_journal.DialogueQualityRecord(**fields)  # type: ignore[arg-type]

    def _task_label(self, **overrides: object) -> object:
        """A `TaskLabel` built through its raw constructor.

        The classmethods are the production path; this one reaches fields
        (`sensitivity_halted`) that they deliberately do not expose, which is
        what a field-by-field attack has to do.
        """
        fields: dict[str, object] = {"task_id": "task-1"}
        fields.update(overrides)
        return learning_journal.TaskLabel(**fields)  # type: ignore[arg-type]

    def _compliance_record(self, **overrides: object) -> object:
        fields: dict[str, object] = {
            "session_id": "session-2026-08-12",
            "total_writes": 12,
            "code_writes": 5,
            "routing_declarations": 9,
            "worker_calls": 7,
            "violation_count": 2,
            "declaration_drift_count": 1,
            "calibration_markers": 3,
            "code_write_count": 5,
            "issue_codes": ("DEC-01", "LOG-01"),
            "timestamp": self.FIXED_TIMESTAMP,
        }
        fields.update(overrides)
        return learning_journal.ComplianceRecord(**fields)  # type: ignore[arg-type]

    def _write_and_read(self, records: list[object]) -> list[dict]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for record in records:
                self.assertIsNone(
                    learning_journal.append_journal_record(record, root_dir=root)
                )
            return _read_jsonl(root / self.JOURNAL_RELATIVE_PATH)

    # --- one test per record family: the schema each one lands with ---

    def test_worker_execution_record_lands_with_its_schema(self) -> None:
        record = self._write_and_read([self._worker_execution_record()])[0]

        self.assertEqual(
            set(record),
            {
                "kind",
                "task_id",
                "task_type",
                "sensitivity_halted",
                "duration_ms",
                "cost_estimate_usd",
                "success",
                "retry_count",
                "effort",
                "model_id",
                "model_family",
                "timestamp",
            },
        )
        self.assertEqual(record["kind"], "worker_execution")
        self.assertEqual(record["task_id"], "task-1")
        self.assertEqual(record["task_type"], "bugfix")
        self.assertFalse(record["sensitivity_halted"])
        self.assertEqual(record["duration_ms"], 4200)
        self.assertEqual(record["cost_estimate_usd"], 0.0125)
        self.assertTrue(record["success"])
        self.assertEqual(record["retry_count"], 1)
        self.assertEqual(record["effort"], "high")
        self.assertEqual(record["model_id"], "claude-opus-5")
        self.assertEqual(record["model_family"], "claude")
        self.assertEqual(record["timestamp"], self.FIXED_TIMESTAMP)

    def test_outcome_record_lands_with_its_schema_for_every_ground_truth(self) -> None:
        """All four truths the spec names — tests, review, plan acceptance, and
        the human's stalemate choice — are the same family, discriminated by
        `signal`, each carrying the identity of the decision it grades."""
        cases = [
            ("tests", "fail"),
            ("review", "approved"),
            ("plan", "rejected"),
            ("stalemate_resolution", "human"),
        ]
        for signal, verdict in cases:
            with self.subTest(signal=signal):
                record = self._write_and_read(
                    [
                        learning_journal.OutcomeRecord(
                            task=learning_journal.TaskLabel.for_task("graded-decision-1"),
                            signal=signal,  # type: ignore[arg-type]
                            verdict=verdict,  # type: ignore[arg-type]
                            timestamp=self.FIXED_TIMESTAMP,
                        )
                    ]
                )[0]

                self.assertEqual(
                    set(record),
                    {
                        "kind",
                        "task_id",
                        "sensitivity_halted",
                        "signal",
                        "verdict",
                        "timestamp",
                    },
                )
                self.assertEqual(record["kind"], "outcome")
                self.assertEqual(record["task_id"], "graded-decision-1")
                self.assertEqual(record["signal"], signal)
                self.assertEqual(record["verdict"], verdict)

    def test_a_verdict_from_another_signals_vocabulary_is_rejected(self) -> None:
        """A flat verdict vocabulary would let a test run "pick the Planner"."""
        for signal, verdict in (
            ("tests", "planner"),
            ("review", "pass"),
            ("plan", "approved"),
            ("stalemate_resolution", "fail"),
        ):
            with self.subTest(signal=signal, verdict=verdict), self.assertRaises(
                ValueError
            ):
                learning_journal.OutcomeRecord(
                    task=learning_journal.TaskLabel.for_task("task-1"),
                    signal=signal,  # type: ignore[arg-type]
                    verdict=verdict,  # type: ignore[arg-type]
                )

    def test_dialogue_quality_record_lands_with_its_schema(self) -> None:
        record = self._write_and_read([self._dialogue_quality_record()])[0]

        self.assertEqual(
            set(record),
            {
                "kind",
                "task_id",
                "sensitivity_halted",
                "occasion",
                "topology",
                "rounds_run",
                "rounds",
                "canaries_planted",
                "canaries_caught",
                "degraded",
                "independent",
                "timestamp",
            },
        )
        self.assertEqual(record["kind"], "dialogue_quality")
        self.assertEqual(record["occasion"], "plan_review")
        self.assertEqual(record["topology"], "panel")
        self.assertEqual(record["rounds_run"], 2)
        self.assertEqual(
            record["rounds"],
            [
                {"verdict": "revise", "engagement_count": 4},
                {"verdict": "approved", "engagement_count": 2},
            ],
        )
        self.assertEqual(record["canaries_planted"], 2)
        self.assertEqual(record["canaries_caught"], 1)
        self.assertFalse(record["degraded"])
        self.assertTrue(record["independent"])

    def test_compliance_record_lands_with_its_schema(self) -> None:
        record = self._write_and_read([self._compliance_record()])[0]

        self.assertEqual(
            set(record),
            {
                "kind",
                "session_id",
                "total_writes",
                "code_writes",
                "routing_declarations",
                "worker_calls",
                "violation_count",
                "declaration_drift_count",
                "calibration_markers",
                "code_write_count",
                "issue_codes",
                "timestamp",
            },
        )
        self.assertEqual(record["kind"], "compliance")
        self.assertEqual(record["session_id"], "session-2026-08-12")
        self.assertEqual(record["violation_count"], 2)
        self.assertEqual(record["issue_codes"], ["DEC-01", "LOG-01"])
        self.assertNotIn(
            "task_id",
            record,
            "the audit grades a session, not a task: inventing a task identity "
            "for it would fabricate a join that does not exist",
        )

    def test_all_four_families_are_distinguishable_by_kind(self) -> None:
        records = self._write_and_read(
            [
                self._worker_execution_record(),
                learning_journal.OutcomeRecord(
                    task=learning_journal.TaskLabel.for_task("task-1"),
                    signal="tests",
                    verdict="pass",
                ),
                self._dialogue_quality_record(),
                self._compliance_record(),
            ]
        )

        self.assertEqual(
            [record["kind"] for record in records],
            ["worker_execution", "outcome", "dialogue_quality", "compliance"],
        )

    # --- the stream itself ---

    def test_journal_stream_is_separate_from_the_audited_telemetry_stream(self) -> None:
        """Ticket 12's first constraint: the audited record contract stays
        frozen, which it cannot do if the learning schema shares its file."""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            self.assertNotEqual(
                learning_journal.journal_path(root),
                root / self.TELEMETRY_RELATIVE_PATH,
            )

            invoker = _RecordingInvoker(["Plan.", "VERDICT: APPROVE\nGood."])
            advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite", invoker, root_dir=root, task_id="shared-task"
            )
            telemetry_before = (root / self.TELEMETRY_RELATIVE_PATH).read_bytes()

            learning_journal.append_journal_record(
                self._worker_execution_record(
                    task=learning_journal.TaskLabel.for_task("shared-task")
                ),
                root_dir=root,
            )

            self.assertEqual(
                (root / self.TELEMETRY_RELATIVE_PATH).read_bytes(),
                telemetry_before,
                "writing the journal must not touch the audited stream",
            )
            self.assertEqual(len(_read_jsonl(learning_journal.journal_path(root))), 1)

    def test_records_are_appended_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for index in range(3):
                learning_journal.append_journal_record(
                    self._worker_execution_record(
                        task=learning_journal.TaskLabel.for_task(f"task-{index}")
                    ),
                    root_dir=root,
                )
            records = _read_jsonl(learning_journal.journal_path(root))

        self.assertEqual(
            [record["task_id"] for record in records], ["task-0", "task-1", "task-2"]
        )

    def test_journal_lands_under_the_injected_root_and_the_real_repo_is_untouched(
        self,
    ) -> None:
        repo_journal = REPO_ROOT / self.JOURNAL_RELATIVE_PATH
        before = (
            (repo_journal.read_bytes(), repo_journal.stat().st_mtime)
            if repo_journal.exists()
            else None
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            learning_journal.append_journal_record(
                self._compliance_record(), root_dir=root
            )
            self.assertTrue(learning_journal.journal_path(root).exists())

        after = (
            (repo_journal.read_bytes(), repo_journal.stat().st_mtime)
            if repo_journal.exists()
            else None
        )
        self.assertEqual(before, after)

    def test_write_failure_is_reported_to_the_caller_and_never_raised(self) -> None:
        """The journal observes work it must never be able to break: an
        unwritable `.ralph` degrades the learning loop, it does not fail the
        invocation being recorded. Same contract as
        `advisory_consultation._write_telemetry_record`."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".ralph").write_text("not a directory")

            error = learning_journal.append_journal_record(
                self._worker_execution_record(), root_dir=root
            )

            self.assertIsNotNone(error)
            assert error is not None
            self.assertIn("learning journal", error.lower())
            self.assertIn(str(learning_journal.journal_path(root)), error)
            self.assertFalse(learning_journal.journal_path(root).is_file())

    def test_journal_and_routing_telemetry_join_on_task_identity(self) -> None:
        """User story 2: "what we decided" checked against "were we right".
        The consultation's decision lands in the audited stream and its
        execution and result land in the journal; one `task_id` reads both."""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            invoker = _RecordingInvoker(["Plan.", "VERDICT: APPROVE\nGood."])
            advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite",
                invoker,
                root_dir=root,
                task_id="join-task-1",
            )
            learning_journal.append_journal_record(
                self._worker_execution_record(
                    task=learning_journal.TaskLabel.for_task(
                        "join-task-1", task_type="refactor"
                    )
                ),
                root_dir=root,
            )
            learning_journal.append_journal_record(
                learning_journal.OutcomeRecord(
                    task=learning_journal.TaskLabel.for_task("join-task-1"),
                    signal="tests",
                    verdict="pass",
                ),
                root_dir=root,
            )
            telemetry = _read_jsonl(root / self.TELEMETRY_RELATIVE_PATH)
            journal = _read_jsonl(learning_journal.journal_path(root))

        decisions = [r for r in telemetry if r["task_id"] == "join-task-1"]
        graded = [r for r in journal if r["task_id"] == "join-task-1"]

        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0]["outcome"], "consensus")
        self.assertEqual(
            {record["kind"] for record in graded}, {"worker_execution", "outcome"}
        )
        self.assertEqual(
            next(r for r in graded if r["kind"] == "outcome")["verdict"], "pass"
        )

    # --- content-freedom, enforced by construction ---

    def test_task_text_and_prompt_text_are_rejected_by_construction(self) -> None:
        """The shape gate: prose has spaces, paths have slashes, and neither
        can match the identifier pattern. No regex scan of a free-text field
        is involved, because there is no free-text field."""
        unjournalable = (
            "Plan the auth rewrite for the ACME account",
            "[WORKER-MODE: AGY-NESTED-EXEC]\nYou are the Planner",
            "src/routing/handler.py",
            "fix the login 500",
            "",
        )
        for value in unjournalable:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    learning_journal.TaskLabel.for_task(value)
                with self.assertRaises(ValueError):
                    self._worker_execution_record(model_id=value)
                with self.assertRaises(ValueError):
                    self._compliance_record(session_id=value)

    def test_a_matched_secret_value_is_rejected_in_every_descriptor_field(self) -> None:
        """The second gate: `sk-live-...` is shaped exactly like a valid
        identifier, so shape alone would pass it. The marker check is what
        does not — and the rejection may name the marker constant it matched
        (as `advisory_consultation` does) but never the secret around it.

        The gate covers the identifiers a caller *composes* for a record.
        `task_id` is deliberately not among them: it arrives already accepted
        by `agent_council` and already written to the audited telemetry
        stream, so refusing it here cannot un-write it and can only break the
        cross-stream join — see `_validate_task_id` and
        `test_every_task_id_the_council_accepts_is_journal_writable`.
        """
        secrets_that_look_like_ids = (
            ("sk-live-9f3c1d7b", "9f3c1d7b"),
            ("model-api_key-9f3c1d", "9f3c1d"),
            ("session-password-hunter2", "hunter2"),
            ("AGY_CALIBRATION_SECRET-a1b2", "a1b2"),
        )
        for value, secret_material in secrets_that_look_like_ids:
            with self.subTest(value=value):
                for build in (
                    lambda v=value: self._worker_execution_record(model_id=v),
                    lambda v=value: self._worker_execution_record(model_family=v),
                    lambda v=value: self._compliance_record(session_id=v),
                ):
                    with self.assertRaises(ValueError) as caught:
                        build()
                    self.assertNotIn(
                        secret_material,
                        str(caught.exception),
                        "the rejection must not repeat the secret it rejected",
                    )

    def test_the_marker_gate_still_accepts_the_councils_own_task_id_format(self) -> None:
        """Regression guard for the bug that a substring marker scan causes:
        "task-1" contains "sk-", so a substring check refuses `task-<digest>`
        — exactly what `agent_council._task_id` generates when no id is
        supplied — and every unnamed task falls out of the journal. See
        `learning_journal._identifier_sensitivity_marker`."""
        council_default = agent_council.AgentCouncil._task_id(
            "Refactor routing checks", "simple", "medium", None
        )
        self.assertTrue(council_default.startswith("task-"))

        for identifier in (council_default, "task-1", "task-42", "risk-review-1"):
            with self.subTest(identifier=identifier):
                self.assertEqual(
                    learning_journal.TaskLabel.for_task(identifier).task_id, identifier
                )

    def test_a_rejection_message_never_echoes_content_back(self) -> None:
        """The last way out: an error path. A caller who puts task text in an
        enumerated field must not see it reflected into whatever log catches
        the exception — while a plain typo stays diagnosable."""
        with self.assertRaises(ValueError) as caught:
            self._worker_execution_record(
                effort="rewrite the auth flow for the ACME account"
            )
        self.assertNotIn("ACME", str(caught.exception))
        self.assertIn("redacted", str(caught.exception))

        with self.assertRaises(ValueError) as typo:
            self._worker_execution_record(effort="turbo")
        self.assertIn(
            "turbo",
            str(typo.exception),
            "an identifier-shaped, marker-free value stays visible: 'turbo' is "
            "one keystroke from 'ultra' and redacting it helps nobody",
        )

    def test_a_task_type_tag_must_come_from_the_enumerated_vocabulary(self) -> None:
        """A coarse tag is permitted; a description is not, and the difference
        is enforced by the vocabulary rather than by a reviewer's judgement."""
        self.assertIn("bugfix", learning_journal.TASK_TYPE_TAGS)
        self.assertIn("refactor", learning_journal.TASK_TYPE_TAGS)

        for tag in ("login-500-for-acme", "bugfix in the auth module", "BUGFIX"):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                learning_journal.TaskLabel.for_task("task-1", task_type=tag)  # type: ignore[arg-type]

    def test_audit_messages_and_file_paths_never_reach_a_compliance_record(self) -> None:
        """The compliance family's deliberate loss: codes are kept, the
        messages and paths that carry session content are not."""
        for code in (
            "DEC-01 unrouted code edit in src/billing/charge.py",
            "src/billing/charge.py",
            "unrouted code edit",
        ):
            with self.subTest(code=code), self.assertRaises(ValueError):
                self._compliance_record(issue_codes=(code,))

    def test_extract_issue_codes_keeps_the_codes_and_drops_the_message_text(
        self,
    ) -> None:
        messages = [
            "Step 3: DEC-01 unrouted code edit in src/billing/charge.py",
            "Step 3: LOG-01 unknown write tool apply_unreviewed_patch",
            "Step 7: DEC-01 unrouted code edit in src/billing/refund.py",
            "no code in this message at all",
        ]

        codes = learning_journal.extract_issue_codes(messages)

        self.assertEqual(codes, ("DEC-01", "LOG-01"))
        joined = " ".join(codes)
        for leaked in ("charge.py", "refund.py", "apply_unreviewed_patch", "unrouted"):
            self.assertNotIn(leaked, joined)

    # --- the halted-task rule ---

    def test_the_halted_label_constructor_has_no_tag_parameter_at_all(self) -> None:
        """Lock 1: a tag is not merely rejected on a halted task, there is no
        argument through which one could be offered."""
        import inspect

        parameters = inspect.signature(
            learning_journal.TaskLabel.for_halted_task
        ).parameters

        self.assertEqual(set(parameters), {"task_id"})
        self.assertIn(
            "task_type",
            inspect.signature(learning_journal.TaskLabel.for_task).parameters,
            "the normal-task constructor is the one that may take a tag",
        )

    def test_a_halted_label_carrying_a_tag_cannot_be_constructed_at_all(self) -> None:
        """Lock 2: bypassing the constructors does not bypass the rule."""
        with self.assertRaises(ValueError):
            learning_journal.TaskLabel(
                task_id="halt-1", task_type="bugfix", sensitivity_halted=True
            )

        halted = learning_journal.TaskLabel.for_halted_task("halt-1")
        with self.assertRaises(ValueError):
            dataclasses.replace(halted, task_type="bugfix")

    def test_a_halted_task_record_carries_no_task_type_key_whatsoever(self) -> None:
        """Absence, not `"task_type": null` — see `TaskLabel.to_mapping`. The
        halt flag still lands, so an auditor can tell "halted, therefore
        untaggable" from "untagged by choice"."""
        halted, normal = self._write_and_read(
            [
                self._worker_execution_record(
                    task=learning_journal.TaskLabel.for_halted_task("a1b2c3d4e5f6")
                ),
                self._worker_execution_record(
                    task=learning_journal.TaskLabel.for_task("task-1")
                ),
            ]
        )

        self.assertNotIn("task_type", halted)
        self.assertTrue(halted["sensitivity_halted"])
        self.assertEqual(halted["task_id"], "a1b2c3d4e5f6")
        self.assertNotIn("task_type", normal)
        self.assertFalse(normal["sensitivity_halted"])

    # --- structural invariants and drift guards ---

    def test_rounds_run_is_derived_from_the_round_sequence(self) -> None:
        """Mirrors `AdvisoryDebateResult.consensus_reached`: a record cannot
        claim a round count its own round sequence does not back."""
        record = self._dialogue_quality_record(
            rounds=(
                learning_journal.DialogueRound("revise", 3),
                learning_journal.DialogueRound("revise", 2),
                learning_journal.DialogueRound("unparseable", 0),
            )
        )

        self.assertEqual(record.rounds_run, 3)  # type: ignore[attr-defined]
        self.assertNotIn(
            "rounds_run",
            {f.name for f in dataclasses.fields(record)},  # type: ignore[arg-type]
            "rounds_run must stay derived, never an independently settable field",
        )

    def test_impossible_records_are_rejected_by_construction(self) -> None:
        with self.assertRaises(ValueError):
            self._worker_execution_record(duration_ms=-1)
        with self.assertRaises(ValueError):
            self._worker_execution_record(retry_count=-1)
        with self.assertRaises(ValueError):
            self._worker_execution_record(cost_estimate_usd=-0.01)
        with self.assertRaises(ValueError):
            self._worker_execution_record(effort="turbo")
        with self.assertRaises(ValueError):
            self._worker_execution_record(timestamp="12 August 2026")
        with self.assertRaises(ValueError):
            self._dialogue_quality_record(occasion="chat")
        with self.assertRaises(ValueError):
            self._dialogue_quality_record(topology="solo")
        with self.assertRaises(ValueError):
            self._dialogue_quality_record(
                rounds=(learning_journal.DialogueRound("approved", 1), "shrug")
            )
        with self.assertRaises(ValueError):
            learning_journal.DialogueRound("shrug", 1)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            learning_journal.DialogueRound("approved", -1)
        with self.assertRaises(ValueError):
            self._dialogue_quality_record(canaries_planted=1, canaries_caught=2)
        with self.assertRaises(ValueError):
            self._compliance_record(violation_count=-1)

    def test_task_id_pattern_matches_agent_council_exactly(self) -> None:
        """Drift guard for the deliberate duplication (see `TASK_ID_RE`). A
        narrower pattern here silently drops council task_ids from the
        journal and breaks the cross-stream join; a wider one lets prose in."""
        self.assertEqual(
            learning_journal.TASK_ID_RE.pattern, agent_council.TASK_ID_RE.pattern
        )

    def test_effort_vocabulary_matches_agent_council(self) -> None:
        self.assertEqual(learning_journal.VALID_EFFORTS, agent_council.VALID_EFFORTS)

    def test_sensitivity_markers_are_a_superset_of_agent_council_patterns(self) -> None:
        """Same drift guard the advisory module carries, for the same reason:
        neither module imports `agent_council`, so only a test keeps the three
        marker lists from diverging."""
        journal_markers = {marker.lower() for marker in learning_journal.SENSITIVITY_MARKERS}
        council_patterns = {pattern.lower() for pattern in agent_council.SENSITIVE_PATTERNS}
        advisory_markers = {
            marker.lower() for marker in advisory_consultation.SENSITIVITY_MARKERS
        }
        self.assertTrue(council_patterns.issubset(journal_markers))
        self.assertTrue(advisory_markers.issubset(journal_markers))

    def test_append_jsonl_locked_matches_agent_council_byte_for_byte(self) -> None:
        """The journal is a second reader's problem as much as a writer's: an
        auditor already parses `sort_keys`-ordered, newline-terminated JSONL,
        and this stream must encode identically. Proves nothing about the
        lock semantics — a byte comparison cannot observe `fcntl.flock`."""
        record = {"b": 2, "a": 1, "kind": "worker_execution"}
        with tempfile.TemporaryDirectory() as tmp:
            journal_written = Path(tmp) / "journal.jsonl"
            council_written = Path(tmp) / "council.jsonl"
            learning_journal._append_jsonl_locked(journal_written, record)
            agent_council.append_jsonl_locked(council_written, record)
            self.assertEqual(
                journal_written.read_bytes(), council_written.read_bytes()
            )

    # --- content-freedom, field by field ---
    #
    # The tests above attack the fields whose validation is visible in the
    # signature — the enumerated ones and the identifier-shaped ones. These
    # attack the rest: `kind`, the booleans, the numbers, and `task` itself.
    # A field annotated `bool` or `Literal` is not a validated field, because
    # those annotations are gone by the time a value from a parsed log
    # arrives; "content-freedom is structural" is a claim about *every* field
    # or it is not structural at all.

    def test_no_field_of_any_record_accepts_free_text(self) -> None:
        """The general form of the rule, driven off `dataclasses.fields` so
        that a field added to any record later is attacked the day it is
        added rather than the day someone remembers to extend a list here."""
        task_text = "Prompt: reset the ACME account password"
        builders = (
            self._task_label,
            self._worker_execution_record,
            self._outcome_record,
            self._dialogue_quality_record,
            self._compliance_record,
        )
        for build in builders:
            record = build()
            field_names = [
                f.name for f in dataclasses.fields(record)  # type: ignore[arg-type]
            ]
            self.assertTrue(field_names)
            for name in field_names:
                with self.subTest(record=type(record).__name__, field=name):
                    with self.assertRaises(ValueError) as caught:
                        build(**{name: task_text})
                    self.assertNotIn(
                        "ACME",
                        str(caught.exception),
                        "a rejection must not echo the text it rejected",
                    )

    def test_kind_is_a_family_constant_no_caller_can_set(self) -> None:
        """`kind` names the record family, so it is not caller input at all.
        As a field with a default it was a free string field like any other:
        `kind="Prompt: reset the ACME password"` constructed and serialized.
        Now it is a class constant with no constructor parameter behind it —
        the lock `TaskLabel.for_halted_task` uses for its absent tag
        argument, applied to a field that had no business existing."""
        families = (
            ("worker_execution", self._worker_execution_record),
            ("outcome", self._outcome_record),
            ("dialogue_quality", self._dialogue_quality_record),
            ("compliance", self._compliance_record),
        )
        for expected_kind, build in families:
            with self.subTest(kind=expected_kind):
                record = build()

                self.assertNotIn(
                    "kind",
                    {f.name for f in dataclasses.fields(record)},  # type: ignore[arg-type]
                    "kind must not be a field: a field is settable",
                )
                self.assertEqual(record.KIND, expected_kind)  # type: ignore[attr-defined]
                self.assertEqual(
                    self._write_and_read([record])[0]["kind"],
                    expected_kind,
                    "the wire form still carries the discriminator a reader joins on",
                )
                with self.assertRaises(TypeError):
                    build(kind="Prompt: reset the ACME account password")
                with self.assertRaises(TypeError):
                    dataclasses.replace(  # type: ignore[type-var]
                        record, kind="Prompt: reset the ACME account password"
                    )

    def test_boolean_fields_hold_booleans_and_nothing_else(self) -> None:
        """`success="task text leaks here"` used to construct, serialize, and
        read back as a truthy value — a free string field wearing a `bool`
        annotation. `1` and `0` are refused too: they are what a sloppy JSON
        round-trip produces, and a count is not an answer to "did it work"."""
        attacks = (
            (self._worker_execution_record, "success"),
            (self._dialogue_quality_record, "degraded"),
            (self._dialogue_quality_record, "independent"),
            (self._task_label, "sensitivity_halted"),
        )
        for build, name in attacks:
            for value in ("task text leaks here", 1, 0, None, "true"):
                with self.subTest(field=name, value=value):
                    with self.assertRaises(ValueError) as caught:
                        build(**{name: value})
                    self.assertNotIn("leaks", str(caught.exception))

        written = self._write_and_read(
            [self._worker_execution_record(success=False)]
        )[0]
        self.assertIs(written["success"], False)

    def test_numeric_fields_reject_non_numbers_and_non_finite_amounts(self) -> None:
        """A count field takes an integer, not a duration written out in
        words. `NaN` and `Infinity` are refused on top of that because
        `json.dumps` emits them literally and neither is JSON: one non-finite
        cost would make that line unparseable for the reader this stream
        exists to feed."""
        for value in ("4200 ms on the ACME login", None, True, 4.2):
            with self.subTest(field="duration_ms", value=value), self.assertRaises(
                ValueError
            ):
                self._worker_execution_record(duration_ms=value)
        for value in ("about a dollar", None, True, float("nan"), float("inf")):
            with self.subTest(
                field="cost_estimate_usd", value=value
            ), self.assertRaises(ValueError):
                self._worker_execution_record(cost_estimate_usd=value)
        with self.assertRaises(ValueError):
            self._compliance_record(total_writes="12 writes to src/billing")
        with self.assertRaises(ValueError):
            self._dialogue_quality_record(canaries_planted=True)
        with self.assertRaises(ValueError):
            learning_journal.DialogueRound("approved", "four objections")

        priced = self._write_and_read(
            [self._worker_execution_record(cost_estimate_usd=1)]
        )[0]
        self.assertEqual(priced["cost_estimate_usd"], 1)

    def test_a_record_carries_a_real_task_label_or_none_at_all(self) -> None:
        """The widest hole of the set: `task: TaskLabel` is an annotation, so
        every check `TaskLabel.__post_init__` performs was skippable by simply
        not building one — `task="fix the login 500 for the ACME account"`
        constructed, and only failed later, at serialization."""
        for build in (
            self._worker_execution_record,
            self._outcome_record,
            self._dialogue_quality_record,
        ):
            for value in (
                "fix the login 500 for the ACME account",
                {"task_id": "task-1"},
                None,
            ):
                with self.subTest(
                    record=type(build()).__name__, value=value
                ), self.assertRaises(ValueError):
                    build(task=value)

    # --- the task-id contract: what the council accepts, the journal writes ---

    def test_every_task_id_the_council_accepts_is_journal_writable(self) -> None:
        """The cross-stream join's precondition, pinned against the council's
        own validator rather than a hand-written list, so the two cannot
        drift apart.

        `secret-rotation` is the case that regressed: the marker gate applied
        to `task_id` refused it, while `agent_council` accepts it and writes
        it to `.ralph/routing_telemetry.jsonl`. The journal then held no
        record for that task at all — the join failing silently for exactly
        the tasks whose names touch security vocabulary. A `task_id` is an
        identifier the system already accepted, not task text, and the
        journal is not the place to re-adjudicate it."""
        council_default = agent_council.AgentCouncil._task_id(
            "Refactor routing checks", "simple", "medium", None
        )
        candidates = (
            "secret-rotation",
            "api_key-migration",
            "password-reset-flow",
            "sk-live-rotation",
            "AGY_CALIBRATION_SECRET-rotation",
            "task-1",
            council_default,
            "Plan the auth rewrite",
            "src/routing/handler.py",
            "",
        )
        for candidate in candidates:
            with self.subTest(task_id=candidate):
                try:
                    accepted: str | None = agent_council.AgentCouncil._task_id(
                        "any task", "simple", "medium", candidate
                    )
                except ValueError:
                    accepted = None

                if accepted is None:
                    with self.assertRaises(ValueError):
                        learning_journal.TaskLabel.for_task(candidate)
                    continue
                self.assertEqual(
                    learning_journal.TaskLabel.for_task(accepted).task_id, accepted
                )
                self.assertEqual(
                    learning_journal.TaskLabel.for_halted_task(accepted).task_id,
                    accepted,
                )

    def test_a_task_id_from_the_telemetry_stream_joins_the_journal(self) -> None:
        """The invariant in its literal form: an id that reached
        `.ralph/routing_telemetry.jsonl` is journalable. Driven through the
        council's own writer, so the id under test is one the audited stream
        genuinely produced rather than one this test asserted was valid."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            telemetry_file = root / self.TELEMETRY_RELATIVE_PATH
            agent_council.log_routing_telemetry(
                "secret-rotation",
                "simple",
                "codex",
                "routine rotation work",
                log_file=telemetry_file,
            )
            written_id = _read_jsonl(telemetry_file)[0]["task_id"]

            error = learning_journal.append_journal_record(
                self._worker_execution_record(
                    task=learning_journal.TaskLabel.for_task(written_id)
                ),
                root_dir=root,
            )
            journal = _read_jsonl(learning_journal.journal_path(root))

        self.assertIsNone(error)
        self.assertEqual(written_id, "secret-rotation")
        self.assertEqual([record["task_id"] for record in journal], [written_id])

    def test_the_marker_gate_matches_whole_tokens_never_substrings(self) -> None:
        """The gate that remains, on the identifiers a caller composes. Its
        token-boundary rule is the guard from the same past bug as
        `test_the_marker_gate_still_accepts_the_councils_own_task_id_format`,
        asserted here on the gate itself so it stays covered even though
        `task_id` no longer passes through it."""
        marker_of = learning_journal._identifier_sensitivity_marker

        self.assertIsNone(marker_of("task-1"))
        self.assertIsNone(marker_of("risk-review-1"))
        self.assertEqual(marker_of("sk-live-9f3c1d7b"), "sk-")
        self.assertEqual(marker_of("model-api_key-1"), "api_key")

        with self.assertRaises(ValueError):
            self._worker_execution_record(model_id="sk-live-9f3c1d7b")
        journaled = self._write_and_read(
            [self._worker_execution_record(model_id="task-1")]
        )[0]
        self.assertEqual(
            journaled["model_id"],
            "task-1",
            "an identifier that merely contains a marker's letters is not a "
            "credential, and refusing it would drop honest records",
        )

    # --- one type per round, not two arrays ---

    def test_a_round_pairs_its_verdict_with_its_own_engagement_count(self) -> None:
        """`round_verdicts` and `engagement_counts` were two synchronized
        tuples kept honest by a manual equal-length check. A `DialogueRound`
        per round — the shape `AdvisoryDebateRound` already sets in this repo
        — makes a mismatch unexpressible, so the check has nothing left to
        guard and is gone."""
        record = self._dialogue_quality_record(
            rounds=(
                learning_journal.DialogueRound("revise", 3),
                learning_journal.DialogueRound("approved", 1),
            )
        )
        field_names = {
            f.name for f in dataclasses.fields(record)  # type: ignore[arg-type]
        }

        self.assertIn("rounds", field_names)
        self.assertNotIn("round_verdicts", field_names)
        self.assertNotIn("engagement_counts", field_names)
        self.assertEqual(record.rounds[1].verdict, "approved")  # type: ignore[attr-defined]
        self.assertEqual(record.rounds[1].engagement_count, 1)  # type: ignore[attr-defined]

        for impossible in (
            ({"verdict": "approved", "engagement_count": 1},),
            ("approved",),
            (learning_journal.DialogueRound("approved", 1), None),
        ):
            with self.subTest(rounds=impossible), self.assertRaises(ValueError):
                self._dialogue_quality_record(rounds=impossible)
        with self.assertRaises(ValueError):
            self._dialogue_quality_record(rounds="approved")

    def test_rounds_serialize_as_one_object_per_round(self) -> None:
        """The wire form a metrics reader gets: a round is one object, so
        round three's engagement count is read from round three rather than
        by indexing a second array and trusting it lines up."""
        record = self._write_and_read(
            [
                self._dialogue_quality_record(
                    rounds=(
                        learning_journal.DialogueRound("revise", 4),
                        learning_journal.DialogueRound("unparseable", 0),
                        learning_journal.DialogueRound("approved", 2),
                    )
                )
            ]
        )[0]

        self.assertEqual(
            record["rounds"],
            [
                {"verdict": "revise", "engagement_count": 4},
                {"verdict": "unparseable", "engagement_count": 0},
                {"verdict": "approved", "engagement_count": 2},
            ],
        )
        self.assertEqual(record["rounds_run"], 3)

    # --- the CI contract ---

    @staticmethod
    def _workflow_list(workflow: str, name: str) -> list[str]:
        """The paths under one `NAME: >-` block scalar in the workflow env."""
        lines = workflow.splitlines()
        start = next(
            index
            for index, line in enumerate(lines)
            if line.strip().startswith(f"{name}:")
        )
        listed = []
        for line in lines[start + 1 :]:
            stripped = line.strip()
            if not stripped.startswith("skills/"):
                break
            listed.append(stripped)
        return listed

    def test_ci_lints_and_type_checks_one_single_sourced_module_list(self) -> None:
        """The ruff and mypy steps carried identical hand-maintained module
        lists, so every new module had to be added twice — and a module added
        to one list only is checked by one tool only, silently. One list, and
        every path in it names a file that exists."""
        workflow = (REPO_ROOT / ".github" / "workflows" / "test.yml").read_text(
            encoding="utf-8"
        )
        listed = self._workflow_list(workflow, "PYTHON_MODULES")

        self.assertIn("skills/worker-routing/learning_journal.py", listed)
        self.assertEqual(
            sorted(listed),
            sorted(set(listed)),
            "a module named twice means the two steps carry their own copies again",
        )
        for module in listed:
            with self.subTest(module=module):
                self.assertTrue(
                    (REPO_ROOT / module).is_file(),
                    f"{module} is checked by CI but does not exist",
                )

        commands = [
            line.split("run:", 1)[1].strip()
            for line in workflow.splitlines()
            if line.strip().startswith("run:")
        ]
        checks = [c for c in commands if c.startswith(("ruff ", "mypy "))]
        self.assertEqual(len(checks), 2, "one ruff step and one mypy step")
        for command in checks:
            with self.subTest(command=command):
                self.assertIn("$PYTHON_MODULES", command)
                self.assertNotIn(
                    ".py",
                    command,
                    "a check step naming a module directly has stopped sharing "
                    "the single-sourced list",
                )

    def test_ci_runs_every_test_file_it_checks(self) -> None:
        """Being linted and type-checked is not being run.

        `test_production_invoker.py` was in `PYTHON_MODULES` from the day it
        was written, so ruff and mypy both saw it and CI stayed green — while
        not one of its tests ever executed, because the only test-running step
        named `test_routing.py` directly. This asserts the property that gap
        violated: every `test_*.py` the workflow checks is also a file the
        workflow executes, and the executing step reads its list from the
        environment rather than naming a file inline.
        """
        workflow = (REPO_ROOT / ".github" / "workflows" / "test.yml").read_text(
            encoding="utf-8"
        )
        checked = self._workflow_list(workflow, "PYTHON_MODULES")
        executed = self._workflow_list(workflow, "PYTHON_TESTS")

        checked_tests = {
            path for path in checked if Path(path).name.startswith("test_")
        }
        self.assertTrue(checked_tests, "no test files are checked at all")
        self.assertEqual(
            checked_tests,
            set(executed),
            "a test file CI checks but never runs is a suite that cannot fail",
        )
        for path in executed:
            with self.subTest(path=path):
                self.assertTrue(
                    (REPO_ROOT / path).is_file(),
                    f"{path} is run by CI but does not exist",
                )

        self.assertIn(
            "$PYTHON_TESTS",
            workflow,
            "the test-running step no longer reads the single-sourced list",
        )


production_invoker_spec = importlib.util.spec_from_file_location(
    "production_invoker", SKILL_DIR / "production_invoker.py"
)
assert production_invoker_spec is not None and production_invoker_spec.loader is not None
production_invoker = importlib.util.module_from_spec(production_invoker_spec)
sys.modules["production_invoker"] = production_invoker
production_invoker_spec.loader.exec_module(production_invoker)


class WorkerExecutionJournalingTests(unittest.TestCase):
    """Ticket 13: every worker invocation the production path makes leaves a
    `WorkerExecutionRecord` behind, correlated to its consultation by
    TaskIdentity — and never for a test's own injected fake, which the
    consultation's `(model, effort, prompt) -> str` seam still accepts
    unchanged. Success/failure field coverage (non-zero exit, timeout,
    journal-write failure) lives in `test_production_invoker.py`'s
    `JournaledInvokeWorkerTests`, at the level the factory itself is
    exercised; these tests cover the seam this ticket adds on top of it —
    the consultation's own integration with `make_journaled_invoke_worker`.
    """

    JOURNAL_RELATIVE_PATH = Path(".ralph") / "learning_journal.jsonl"
    TELEMETRY_RELATIVE_PATH = Path(".ralph") / "routing_telemetry.jsonl"

    def test_injecting_a_plain_fake_writes_nothing_to_the_journal(self) -> None:
        """The consultation's seam is unchanged: a caller-supplied fake
        `invoke_worker` bypasses instrumentation entirely, exactly as it did
        before this ticket."""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            invoker = _RecordingInvoker(
                ["Planner's proposed plan.", "VERDICT: APPROVE\nLooks solid."]
            )
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite", invoker, root_dir=root
            )

        self.assertTrue(result.consensus_reached)
        self.assertFalse((root / self.JOURNAL_RELATIVE_PATH).exists())

    def test_journaled_invocations_correlate_to_the_consultation_by_task_id(self) -> None:
        """Wrapping a caller-supplied callable through
        `make_journaled_invoke_worker` and handing the result to the
        consultation as `invoke_worker` — the shape the production default
        takes internally — must leave the journal's records and the
        telemetry stream's record joinable on the same `task_id`, with the
        per-call model identity resolved correctly for two different
        provider families."""
        runner = mock.Mock(
            side_effect=[
                subprocess.CompletedProcess([], 0, "Planner's proposed plan.", ""),
                subprocess.CompletedProcess([], 0, "VERDICT: APPROVE\nLooks solid.", ""),
            ]
        )
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            journaled_invoker = production_invoker.make_journaled_invoke_worker(
                learning_journal.TaskLabel.for_task("task-correlated-1", task_type="feature"),
                root_dir=root,
                runner=runner,
            )
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite",
                journaled_invoker,
                root_dir=root,
                planner_model="Claude Opus 5 (Thinking)",
                critic_model="Codex 5.6 Sol",
                task_id="task-correlated-1",
            )
            journal_records = _read_jsonl(root / self.JOURNAL_RELATIVE_PATH)
            telemetry_records = _read_jsonl(root / self.TELEMETRY_RELATIVE_PATH)

        self.assertTrue(result.consensus_reached)

        self.assertEqual(len(journal_records), 2)
        for record in journal_records:
            self.assertEqual(record["task_id"], "task-correlated-1")
            self.assertEqual(record["kind"], "worker_execution")
            self.assertTrue(record["success"])
        self.assertEqual(journal_records[0]["model_id"], "claude-opus-5")
        self.assertEqual(journal_records[0]["model_family"], "claude")
        self.assertEqual(journal_records[1]["model_id"], "gpt-5.6-sol")
        self.assertEqual(journal_records[1]["model_family"], "codex")

        self.assertEqual(len(telemetry_records), 1)
        self.assertEqual(telemetry_records[0]["task_id"], "task-correlated-1")

    def test_production_default_path_reaches_the_real_journaling_factory(self) -> None:
        """`run_advisory_consultation_debate` with no `invoke_worker`
        supplied reaches `production_invoker.make_journaled_invoke_worker`
        through its own lazy import — the real factory, not a test double.
        `production_invoker.invoke_worker` itself is patched so no real
        worker CLI is ever launched; everything below that (task resolution,
        the factory, the journal write) runs for real."""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ), mock.patch.object(
            production_invoker,
            "invoke_worker",
            side_effect=["Planner's proposed plan.", "VERDICT: APPROVE\nLooks solid."],
        ):
            root = Path(tmp)
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite",
                root_dir=root,
                planner_model="Claude Opus 5 (Thinking)",
                critic_model="Codex 5.6 Sol",
                task_id="task-default-path-1",
            )
            journal_records = _read_jsonl(root / self.JOURNAL_RELATIVE_PATH)

        self.assertTrue(result.consensus_reached)
        self.assertEqual(len(journal_records), 2)
        for record in journal_records:
            self.assertEqual(record["task_id"], "task-default-path-1")
            self.assertEqual(record["kind"], "worker_execution")
            self.assertTrue(record["success"])


learning_outcomes_spec = importlib.util.spec_from_file_location(
    "learning_outcomes", SKILL_DIR / "learning_outcomes.py"
)
assert learning_outcomes_spec is not None and learning_outcomes_spec.loader is not None
learning_outcomes = importlib.util.module_from_spec(learning_outcomes_spec)
sys.modules["learning_outcomes"] = learning_outcomes
learning_outcomes_spec.loader.exec_module(learning_outcomes)


class OutcomeRecordingTests(unittest.TestCase):
    """Ticket 14: ground truth gets joined to the decision that produced it.

    `learning_journal.OutcomeRecord` already carries the schema and the
    (signal, verdict) pairing; these tests exercise the public surface a
    caller far from that schema actually calls — `learning_outcomes`'s four
    `record_*` functions — and the one path (the stalemate resolution) that
    is wired into the real `advisory_consultation` flow rather than tested
    against a hand-built stand-in.
    """

    JOURNAL_RELATIVE_PATH = Path(".ralph") / "learning_journal.jsonl"

    def test_record_test_result_writes_pass_and_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            error_pass = learning_outcomes.record_test_result(
                "task-tests-1", passed=True, root_dir=root
            )
            error_fail = learning_outcomes.record_test_result(
                "task-tests-2", passed=False, root_dir=root
            )
            records = _read_jsonl(root / self.JOURNAL_RELATIVE_PATH)

        self.assertIsNone(error_pass)
        self.assertIsNone(error_fail)
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["kind"], "outcome")
        self.assertEqual(records[0]["task_id"], "task-tests-1")
        self.assertEqual(records[0]["signal"], "tests")
        self.assertEqual(records[0]["verdict"], "pass")
        self.assertEqual(records[1]["task_id"], "task-tests-2")
        self.assertEqual(records[1]["verdict"], "fail")

    def test_record_review_verdict_writes_approved_and_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            learning_outcomes.record_review_verdict(
                "task-review-1", approved=True, root_dir=root
            )
            learning_outcomes.record_review_verdict(
                "task-review-2", approved=False, root_dir=root
            )
            records = _read_jsonl(root / self.JOURNAL_RELATIVE_PATH)

        self.assertEqual(records[0]["signal"], "review")
        self.assertEqual(records[0]["verdict"], "approved")
        self.assertEqual(records[1]["verdict"], "rejected")

    def test_record_plan_outcome_writes_accepted_and_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            learning_outcomes.record_plan_outcome(
                "task-plan-1", accepted=True, root_dir=root
            )
            learning_outcomes.record_plan_outcome(
                "task-plan-2", accepted=False, root_dir=root
            )
            records = _read_jsonl(root / self.JOURNAL_RELATIVE_PATH)

        self.assertEqual(records[0]["signal"], "plan")
        self.assertEqual(records[0]["verdict"], "accepted")
        self.assertEqual(records[1]["verdict"], "rejected")

    def _run_to_stalemate(self, root: Path):
        invoker = _RecordingInvoker(
            [
                "Planner's first plan.",
                "VERDICT: REVISE\nNeeds more detail.",
                "Planner's second plan.",
                "VERDICT: REVISE\nStill thin.",
                "Planner's third plan.",
                "VERDICT: REVISE\nNot convinced.",
            ]
        )
        return advisory_consultation.run_advisory_consultation_debate(
            "Plan the auth rewrite", invoker, root_dir=root, task_id="task-stalemate-1"
        )

    def test_record_stalemate_resolution_is_wired_to_a_real_stalemate_report(self) -> None:
        """Drives an actual stalemate through `run_advisory_consultation_debate`
        rather than a hand-built `AdvisoryStalemateReport`, then records the
        human's pick of the Critic's option — proving the function is wired
        to the real consultation path, not a stand-in for one."""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            result = self._run_to_stalemate(root)
            assert result.stalemate is not None
            critic_option = next(
                option
                for option in result.stalemate.options
                if option.label == "Approve Critic Architecture"
            )

            error = learning_outcomes.record_stalemate_resolution(
                "task-stalemate-1", result.stalemate, critic_option, root_dir=root
            )
            records = _read_jsonl(root / self.JOURNAL_RELATIVE_PATH)

        self.assertIsNone(error)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["kind"], "outcome")
        self.assertEqual(records[0]["task_id"], "task-stalemate-1")
        self.assertEqual(records[0]["signal"], "stalemate_resolution")
        self.assertEqual(records[0]["verdict"], "critic")

    def test_record_stalemate_resolution_maps_all_three_options(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            result = self._run_to_stalemate(root)
            assert result.stalemate is not None

            for expected_verdict, option in zip(
                ("planner", "critic", "human"), result.stalemate.options
            ):
                learning_outcomes.record_stalemate_resolution(
                    "task-stalemate-1", result.stalemate, option, root_dir=root
                )
            records = _read_jsonl(root / self.JOURNAL_RELATIVE_PATH)

        self.assertEqual([record["verdict"] for record in records], ["planner", "critic", "human"])

    def test_record_stalemate_resolution_rejects_an_option_from_a_different_report(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            result = self._run_to_stalemate(root)
            assert result.stalemate is not None
            foreign_option = advisory_consultation.AdvisoryResolutionOption(
                1, "Approve Planner Architecture", "a hand-built, non-matching position"
            )

            with self.assertRaises(ValueError):
                learning_outcomes.record_stalemate_resolution(
                    "task-stalemate-1", result.stalemate, foreign_option, root_dir=root
                )

            self.assertFalse((root / self.JOURNAL_RELATIVE_PATH).exists())

    def test_missing_task_id_is_refused_rather_than_writing_an_orphan_record(self) -> None:
        """"Unknown task" handling: this module never fabricates a `task_id`
        for an outcome. An empty one is refused loudly, and no orphan record
        reaches the journal."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            with self.assertRaises(ValueError):
                learning_outcomes.record_test_result("", passed=True, root_dir=root)

            self.assertFalse((root / self.JOURNAL_RELATIVE_PATH).exists())

    def test_task_description_shaped_task_id_is_refused(self) -> None:
        """Content-freedom holds at this surface too: a caller cannot smuggle
        task text in through `task_id` — the shape gate `TaskLabel.for_task`
        already enforces rejects it before anything is written."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            with self.assertRaises(ValueError):
                learning_outcomes.record_review_verdict(
                    "fix the login 500 for the ACME account", approved=True, root_dir=root
                )

            self.assertFalse((root / self.JOURNAL_RELATIVE_PATH).exists())

    def test_write_failure_is_reported_to_the_caller_and_never_raised(self) -> None:
        """Matches ticket 13's contract: a broken `.ralph` degrades the
        learning loop, it never breaks the caller recording the outcome."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".ralph").write_text("not a directory")

            error = learning_outcomes.record_plan_outcome(
                "task-plan-write-failure", accepted=True, root_dir=root
            )

            self.assertIsNotNone(error)
            assert error is not None
            self.assertIn("learning journal", error.lower())

    def test_outcome_joins_to_its_decision_by_task_id(self) -> None:
        """User story 2: a decision recorded via the worker-execution family
        and its later outcome share one `task_id` a scoreboard reads together."""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            journaled_invoker = production_invoker.make_journaled_invoke_worker(
                learning_journal.TaskLabel.for_task("task-join-1", task_type="feature"),
                root_dir=root,
                runner=mock.Mock(
                    return_value=subprocess.CompletedProcess([], 0, "worker output", "")
                ),
            )
            journaled_invoker("claude-sonnet-5", "high", "do the thing")

            learning_outcomes.record_test_result("task-join-1", passed=True, root_dir=root)

            records = _read_jsonl(root / self.JOURNAL_RELATIVE_PATH)

        kinds_by_task = {record["kind"]: record for record in records}
        self.assertEqual(kinds_by_task["worker_execution"]["task_id"], "task-join-1")
        self.assertEqual(kinds_by_task["outcome"]["task_id"], "task-join-1")
        self.assertEqual(kinds_by_task["outcome"]["signal"], "tests")
        self.assertEqual(kinds_by_task["outcome"]["verdict"], "pass")


# Ticket 15 — the post-session audit verdict, persisted instead of printed and
# lost. Appended here, at the end of the file, per this ticket's instructions:
# this file is edited concurrently on other branches, so an insertion
# anywhere but the end is a merge conflict for everyone.
class PersistComplianceRecordTests(unittest.TestCase):
    """Unit-level coverage of `routing_check._persist_compliance_record`.

    Exercised directly against hand-built `AuditReport`s rather than through
    a parsed log fixture: no fixture on disk today drives a full
    `[ROUTING: worker — complexity: ... — effort: ...]` declaration through a
    DEC-01 drift, and hand-authoring one as raw log text is exactly the kind
    of escaping-fragile fixture the rest of this file avoids by constructing
    `Step`/`AuditReport` objects directly (see `RoutingAuditEngineTests`).
    What matters here — that a real, non-empty set of audit issues reduces to
    the right `issue_codes` — does not need a parser in the loop at all.

    `_report` builds the real frozen dataclass, not a dict shaped like one:
    the whole point of the signature this function now takes is that a caller
    cannot hand it a mapping with a mistyped key and have it look correct
    until runtime.
    """

    JOURNAL_RELATIVE_PATH = Path(".ralph") / "learning_journal.jsonl"

    # No return annotation: `routing_check` is loaded by path, so mypy sees a
    # bare `ModuleType` and cannot resolve `routing_check.AuditReport` as a
    # name. Every other test in this file that touches these modules relies on
    # the same inference.
    @staticmethod
    def _report(**overrides: object):
        base: dict = {
            "total_writes": 2,
            "code_writes": 1,
            "routing_declarations": 1,
            "worker_calls": 1,
            "code_write_files": ["src/app.py"],
            "violations": [],
            "declaration_drift": [],
            "violation_details": [],
            "calibration_markers": 0,
            "exit_code": 0,
        }
        base.update(overrides)
        return routing_check.AuditReport(**base)

    def test_clean_metrics_persist_a_clean_verdict_not_nothing(self) -> None:
        """User story 4 / the trendline-has-no-silent-gaps criterion: a
        session with zero violations still writes a record, with an empty
        `issue_codes` tuple rather than no record at all."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            error = routing_check._persist_compliance_record(
                self._report(), session_id="sess-clean", root_dir=root
            )

            self.assertIsNone(error)
            records = _read_jsonl(root / self.JOURNAL_RELATIVE_PATH)
            self.assertEqual(len(records), 1)
            record = records[0]
            self.assertEqual(record["kind"], "compliance")
            self.assertEqual(record["session_id"], "sess-clean")
            self.assertEqual(record["violation_count"], 0)
            self.assertEqual(record["issue_codes"], [])
            self.assertEqual(record["total_writes"], 2)
            self.assertEqual(record["code_writes"], 1)
            self.assertEqual(record["routing_declarations"], 1)
            self.assertEqual(record["worker_calls"], 1)
            self.assertEqual(record["declaration_drift_count"], 0)
            self.assertEqual(record["code_write_count"], 1)

    def test_violating_metrics_persist_violation_count_and_issue_codes(self) -> None:
        """Codes survive both message shapes the audit produces — a bare
        `"DEC-01 ..."` and a LOG-01 message with a colon of its own — with no
        caller-synthesized `"Step N: "` prefix in between, and the message
        text itself still never reaches the record."""
        report = self._report(
            violations=[(1, ["src/app.py"]), (2, [])],
            declaration_drift=[
                (1, ["DEC-01 declaration worker/model drift"]),
                (2, ["LOG-01 unknown write tool: apply_unreviewed_patch"]),
            ],
            violation_details=[
                (1, ["DEC-01 declaration worker/model drift"]),
                (2, ["LOG-01 unknown write tool: apply_unreviewed_patch"]),
            ],
            exit_code=1,
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            error = routing_check._persist_compliance_record(
                report, session_id="sess-violating", root_dir=root
            )

            self.assertIsNone(error)
            record = _read_jsonl(root / self.JOURNAL_RELATIVE_PATH)[0]
            self.assertEqual(record["violation_count"], 2)
            self.assertEqual(sorted(record["issue_codes"]), ["DEC-01", "LOG-01"])
            for leaked in ("apply_unreviewed_patch", "app.py"):
                self.assertNotIn(leaked, json.dumps(record))

    def test_drift_on_a_non_violating_step_still_reaches_the_record(self) -> None:
        """The trendline's whole subject is discipline drift, and a DEC-01 on
        a step that did not also trip a violation is drift. Sourcing the codes
        from `violation_details` dropped exactly those, so a session could
        carry declaration drift and persist `issue_codes=()`."""
        report = self._report(
            violations=[],
            declaration_drift=[(3, ["DEC-02 declaration effort drift"])],
            violation_details=[],
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            error = routing_check._persist_compliance_record(
                report, session_id="sess-drift-only", root_dir=root
            )

            self.assertIsNone(error)
            record = _read_jsonl(root / self.JOURNAL_RELATIVE_PATH)[0]
            self.assertEqual(record["violation_count"], 0)
            self.assertEqual(record["issue_codes"], ["DEC-02"])
            self.assertEqual(record["declaration_drift_count"], 1)

    def test_warning_codes_reach_the_record(self) -> None:
        """A `--strict` run that fails on warnings alone used to persist
        `violation_count=0, issue_codes=()` — a record no reader could tell
        apart from a genuinely clean session."""
        report = self._report(warning_codes=("WARN-01",), exit_code=1)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            error = routing_check._persist_compliance_record(
                report, session_id="sess-warned", root_dir=root
            )

            self.assertIsNone(error)
            record = _read_jsonl(root / self.JOURNAL_RELATIVE_PATH)[0]
            self.assertEqual(record["violation_count"], 0)
            self.assertEqual(record["issue_codes"], ["WARN-01"])

    def test_no_session_id_persists_nothing(self) -> None:
        """The documented handling of 'no id available': an explicit skip,
        never a fabricated placeholder id."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            error = routing_check._persist_compliance_record(
                self._report(), session_id=None, root_dir=root
            )

            self.assertIsNone(error)
            self.assertFalse((root / self.JOURNAL_RELATIVE_PATH).exists())

    def test_malformed_session_id_is_reported_not_raised(self) -> None:
        """A `session_id` that fails `ComplianceRecord`'s own validation is a
        call-site bug by `learning_journal`'s own contract, but from
        `run_audit`'s point of view it must degrade exactly like a broken
        disk: reported back as a string, never an exception the audit run
        has to survive."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            error = routing_check._persist_compliance_record(
                self._report(),
                session_id="not a valid identifier",
                root_dir=root,
            )

            self.assertIsNotNone(error)
            assert error is not None
            self.assertIn("compliance record", error.lower())
            self.assertFalse((root / self.JOURNAL_RELATIVE_PATH).exists())

    def test_write_failure_is_reported_not_raised(self) -> None:
        """Matches tickets 13 and 14's contract for a broken `.ralph`."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".ralph").write_text("not a directory")

            error = routing_check._persist_compliance_record(
                self._report(), session_id="sess-write-failure", root_dir=root
            )

            self.assertIsNotNone(error)
            assert error is not None
            self.assertIn("learning journal", error.lower())


class RoutingCheckCliCompliancePersistenceTests(unittest.TestCase):
    """CLI-boundary coverage: `routing_check.py --session-id ID <log_file>`.

    Every subprocess here passes `--root-dir` pointing at a throwaway
    temporary directory — `root_dir` has no implicit default (see
    `_persist_compliance_record`), so this is what keeps persistence off the
    real repository, exactly as `RoutingAuditIntegrationTests` does via
    `LEARNING_JOURNAL_ROOT` instead (that class never inspects journal
    content, so an env var suffices there).
    """

    JOURNAL_RELATIVE_PATH = Path(".ralph") / "learning_journal.jsonl"

    def _run(self, cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(ROUTING_CHECK), "--root-dir", str(cwd), *args],
            capture_output=True,
            check=False,
            text=True,
            cwd=str(cwd),
        )

    def test_clean_log_with_session_id_appends_one_clean_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = self._run(
                root, "--session-id", "sess-cli-clean", str(FIXTURES_DIR / "clean_log.txt")
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            records = _read_jsonl(root / self.JOURNAL_RELATIVE_PATH)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["session_id"], "sess-cli-clean")
            self.assertEqual(records[0]["violation_count"], 0)

    def test_violating_log_with_session_id_appends_one_record_with_violations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = self._run(
                root,
                "--session-id",
                "sess-cli-violating",
                str(FIXTURES_DIR / "unrouted_mutation_log.txt"),
            )

            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            records = _read_jsonl(root / self.JOURNAL_RELATIVE_PATH)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["session_id"], "sess-cli-violating")
            self.assertEqual(records[0]["violation_count"], 2)

    def test_no_session_id_never_creates_the_ralph_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = self._run(root, str(FIXTURES_DIR / "clean_log.txt"))

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertFalse((root / ".ralph").exists())

    def test_no_root_dir_never_writes_beneath_cwd_even_with_a_session_id(self) -> None:
        """Regression guard for the original defect: omitting `--root-dir`
        must skip persistence, never fall back to writing beneath whatever
        directory the process happens to be running in — even though a
        `--session-id` was given and would otherwise trigger a write."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROUTING_CHECK),
                    "--session-id",
                    "sess-no-root-dir",
                    str(FIXTURES_DIR / "clean_log.txt"),
                ],
                capture_output=True,
                check=False,
                text=True,
                cwd=str(root),
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertFalse((root / ".ralph").exists())

    def test_stdout_and_exit_code_are_identical_with_and_without_session_id(self) -> None:
        """The hard constraint: gaining a persistence destination must not
        change the audit's existing stdout/exit-code contract at all."""
        with tempfile.TemporaryDirectory() as tmp_a, tempfile.TemporaryDirectory() as tmp_b:
            without_session = self._run(Path(tmp_a), str(FIXTURES_DIR / "clean_log.txt"))
            with_session = self._run(
                Path(tmp_b), "--session-id", "sess-parity", str(FIXTURES_DIR / "clean_log.txt")
            )

            self.assertEqual(without_session.returncode, with_session.returncode)
            self.assertEqual(without_session.stdout, with_session.stdout)

    def test_journal_write_failure_never_changes_exit_code_or_stdout(self) -> None:
        """Pins the exit-code/stdout contract across persistence succeeding
        (`healthy_root`) and failing (`blocked_root`, whose `.ralph` is a
        plain file) for the same log — the write failure is reported to
        stderr (matching tickets 13/14) but never touches stdout or the
        returncode."""
        with tempfile.TemporaryDirectory() as healthy_root, tempfile.TemporaryDirectory() as blocked_root:
            (Path(blocked_root) / ".ralph").write_text("not a directory")

            healthy = self._run(
                Path(healthy_root),
                "--session-id",
                "sess-healthy",
                str(FIXTURES_DIR / "clean_log.txt"),
            )
            blocked = self._run(
                Path(blocked_root),
                "--session-id",
                "sess-blocked",
                str(FIXTURES_DIR / "clean_log.txt"),
            )

            self.assertEqual(healthy.returncode, blocked.returncode)
            self.assertEqual(healthy.stdout, blocked.stdout)
            self.assertTrue(
                (Path(healthy_root) / self.JOURNAL_RELATIVE_PATH).exists()
            )
            self.assertIn("failed to write learning journal record", blocked.stderr)

    def test_session_id_missing_a_value_fails_closed_with_usage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = self._run(
                Path(tmp), str(FIXTURES_DIR / "clean_log.txt"), "--session-id"
            )
            self.assertEqual(result.returncode, 2)


class RoutingAuditShWiresConversationIdToComplianceRecordTests(unittest.TestCase):
    """End-to-end: `routing-audit.sh` resolves a conversation id and this
    ticket's job is to thread it through as `--session-id`, not invent a
    fresh one. Mirrors `RoutingAuditIntegrationTests`' `$HOME` sandboxing,
    plus a distinct `LEARNING_JOURNAL_ROOT` override — kept separate from
    `home_dir` on purpose, so this also pins that the override actually
    redirects the journal rather than merely coinciding with `$HOME`."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.home_dir = Path(self.tmp_dir.name) / "home"
        self.root_dir = Path(self.tmp_dir.name) / "root"
        self.home_dir.mkdir()
        self.root_dir.mkdir()
        self.brain_dir = self.home_dir / ".gemini" / "antigravity" / "brain"
        self.conv_id = f"routing-audit-compliance-test-{os.getpid()}"
        self.conv_dir = self.brain_dir / self.conv_id
        self.log_dir = self.conv_dir / ".system_generated" / "logs"
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def _run_audit(self, *args: str) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ)
        env["HOME"] = str(self.home_dir)
        env["LEARNING_JOURNAL_ROOT"] = str(self.root_dir)
        return subprocess.run(
            ["bash", str(ROUTING_AUDIT), *args, self.conv_id],
            capture_output=True,
            check=False,
            text=True,
            env=env,
        )

    def test_wrapper_persists_a_record_keyed_on_the_real_conversation_id(self) -> None:
        shutil.copy(FIXTURES_DIR / "clean_log.txt", self.log_dir / "overview.txt")

        result = self._run_audit()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        records = _read_jsonl(self.root_dir / ".ralph" / "learning_journal.jsonl")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["session_id"], self.conv_id)

    def test_wrapper_persists_on_violation_too(self) -> None:
        shutil.copy(FIXTURES_DIR / "direct_then_code_log.txt", self.log_dir / "overview.txt")

        result = self._run_audit()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        records = _read_jsonl(self.root_dir / ".ralph" / "learning_journal.jsonl")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["session_id"], self.conv_id)
        self.assertGreater(records[0]["violation_count"], 0)


class JournalUnificationTests(unittest.TestCase):
    """Pins the invariant `routing-audit.sh`'s `JOURNAL_ROOT` default was
    fixed to uphold: every record family in this loop writes to *one*
    journal, beside the routing telemetry, inside the repository — never a
    second file only one writer knows about.

    Deliberately does not assert a hardcoded path string: a compliance
    writer and a differently-invented location could both contain
    `learning_journal.jsonl` and still be two separate files. This writes a
    `ComplianceRecord` and an `OutcomeRecord` — genuinely different families,
    reached through their real production entry points — to the same
    injected root and asserts they land in the same single file on disk. A
    future record family (ticket 17, 19, 21, ...) that resolves its own
    destination instead of going through `learning_journal.journal_path`
    would fail this test by producing a second file.
    """

    def test_compliance_and_outcome_records_share_one_journal_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            compliance_error = routing_check._persist_compliance_record(
                routing_check.AuditReport(
                    total_writes=2,
                    code_writes=1,
                    routing_declarations=1,
                    worker_calls=1,
                    violations=[],
                    declaration_drift=[],
                    violation_details=[],
                    calibration_markers=0,
                    code_write_files=["src/app.py"],
                    exit_code=0,
                ),
                session_id="sess-unification",
                root_dir=root,
            )
            outcome_error = learning_outcomes.record_test_result(
                "task-unification-1", passed=True, root_dir=root
            )

            journal_files = sorted(root.rglob("*.jsonl"))
            self.assertEqual(
                len(journal_files),
                1,
                f"expected one shared journal file, found {journal_files}",
            )
            records = _read_jsonl(journal_files[0])

        self.assertIsNone(compliance_error)
        self.assertIsNone(outcome_error)
        self.assertEqual(len(records), 2)
        self.assertEqual({record["kind"] for record in records}, {"compliance", "outcome"})

    def test_an_installed_audit_writes_into_the_audited_repositorys_journal(self) -> None:
        """The same invariant, under the layout that actually broke it.

        The test above injects `root_dir` directly, so it holds no matter
        where `routing-audit.sh` thinks the repository is — which is why it
        passed while every installed copy of the script was resolving
        `$HOME/.gemini/config` as the journal root and splitting the stream in
        two. This one installs for real, runs the *installed* audit from
        inside a separate audited repository with no `LEARNING_JOURNAL_ROOT`
        override, and asserts the compliance record lands in that
        repository's journal — the same file an `OutcomeRecord` written by
        the loop's other production entry point lands in — and that nothing
        at all is written under the install prefix.
        """
        with _InstalledHarness() as harness:
            audit = harness.run_installed_audit(cwd=harness.project)
            self.assertEqual(audit.returncode, 0, audit.stdout + audit.stderr)

            outcome_error = learning_outcomes.record_test_result(
                "task-installed-unification", passed=True, root_dir=harness.project
            )

            journal_files = sorted(harness.project.rglob("*.jsonl"))
            stray = [
                path
                for path in harness.home.rglob("*.jsonl")
                if "antigravity" not in path.parts
            ]
            self.assertEqual(
                stray,
                [],
                f"the install prefix must hold no journal at all, found {stray}",
            )
            self.assertEqual(
                [path.name for path in journal_files],
                ["learning_journal.jsonl"],
                f"expected one shared journal in the audited repo, found {journal_files}",
            )
            records = _read_jsonl(journal_files[0])

        self.assertIsNone(outcome_error)
        self.assertEqual({record["kind"] for record in records}, {"compliance", "outcome"})
        compliance = next(
            record for record in records if record["kind"] == "compliance"
        )
        self.assertEqual(compliance["session_id"], harness.conv_id)


class _InstalledHarness:
    """A real `install.sh` run into a throwaway `$HOME` plus a separate
    audited git repository — the two-directory shape no dev-checkout test has.

    Every defect this class exists to catch shares one cause: in a checkout,
    the skill directory, the repository being audited, and the process's
    working directory are all the same tree, so a module resolved from the
    wrong one of the three still resolves. Installing separates them.
    """

    def __init__(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name).resolve()
        self.home = base / "home"
        self.project = base / "project"
        self.home.mkdir()
        self.project.mkdir()
        self.installed_dir = (
            self.home / ".gemini" / "config" / "skills" / "worker-routing"
        )
        self.conv_id = f"installed-harness-{os.getpid()}"

    def __enter__(self) -> _InstalledHarness:  # noqa: PYI034 - `typing.Self` would need a new import at the top of this file, which every concurrent branch would then conflict on; this helper is private and never subclassed.
        subprocess.run(
            ["git", "init", "-q"], cwd=self.project, check=True, capture_output=True
        )
        install = subprocess.run(
            ["bash", str(INSTALL_SH), str(self.project)],
            capture_output=True,
            check=False,
            text=True,
            env=self.env(),
        )
        assert install.returncode == 0, install.stdout + install.stderr

        log_dir = (
            self.home
            / ".gemini"
            / "antigravity"
            / "brain"
            / self.conv_id
            / ".system_generated"
            / "logs"
        )
        log_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(FIXTURES_DIR / "clean_log.txt", log_dir / "overview.txt")
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._tmp.cleanup()

    def env(self, **overrides: str) -> dict[str, str]:
        """The child environment: a sandboxed `$HOME`, and deliberately no
        `LEARNING_JOURNAL_ROOT` — resolving the journal root without one is
        the whole subject."""
        env = dict(os.environ)
        env["HOME"] = str(self.home)
        env.pop("LEARNING_JOURNAL_ROOT", None)
        env.update(overrides)
        return env

    def run_installed_audit(
        self, *args: str, cwd: Path, **env_overrides: str
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(self.installed_dir / "routing-audit.sh"), *args, self.conv_id],
            capture_output=True,
            check=False,
            text=True,
            cwd=cwd,
            env=self.env(**env_overrides),
        )

    def run_installed_python(
        self, program: str, *args: str
    ) -> subprocess.CompletedProcess[str]:
        """Run `program` with the installed skill directory as `sys.path[0]`.

        `-c` rather than a script file so `sys.path[0]` is the working
        directory — which is what makes this an honest test of the installed
        copy: the modules it imports are the installed ones, not this
        checkout's.
        """
        return subprocess.run(
            [sys.executable, "-c", program, *args],
            capture_output=True,
            check=False,
            text=True,
            cwd=self.installed_dir,
            env=self.env(),
        )


class ManagedFileClosureTests(unittest.TestCase):
    """`install.sh`'s `MANAGED_FILES` must be closed under sibling imports.

    A managed module importing an unmanaged sibling is the one defect class
    that is invisible in a checkout by construction: every module sits in one
    directory there, so every import resolves regardless of what the
    installer copies. On an installed harness the same import raises
    `ModuleNotFoundError` — and `advisory_consultation` catches exactly that
    when it lazily imports `production_invoker`, so the symptom was not a
    crash but *every* production consultation returning `worker_error` before
    a worker ever ran.

    `learning_journal.py` and `learning_outcomes.py` were both missing from
    the list. Adding them fixes today; this test is what makes the next
    module's omission a failing test instead of a silently broken install,
    which matters more than either individual fix.
    """

    @staticmethod
    def _bash_array(script: Path, name: str) -> list[str]:
        text = script.read_text(encoding="utf-8")
        match = re.search(rf"^{name}=\(([^)]*)\)", text, re.MULTILINE)
        assert match is not None, f"{name} not found in {script}"
        return match.group(1).split()

    def _managed(self) -> list[str]:
        return self._bash_array(INSTALL_SH, "MANAGED_FILES")

    @staticmethod
    def _sibling_imports(module_path: Path) -> set[str]:
        """Every top-level module name `module_path` imports that is a sibling
        `.py` file in the skill directory.

        Read from the AST rather than by importing, so a module is inspected
        without being executed, and so imports inside functions
        (`routing_check._persist_compliance_record`'s deliberately local
        `import learning_journal`) and under `if TYPE_CHECKING`
        (`learning_outcomes`' `advisory_consultation`) are found too — a
        lazily-imported sibling is exactly as absent on an installed harness
        as an eagerly-imported one.
        """
        import ast

        siblings = {path.stem for path in SKILL_DIR.glob("*.py")}
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module] if node.module and node.level == 0 else []
            else:
                continue
            imported.update(
                name.split(".")[0]
                for name in names
                if name.split(".")[0] in siblings
            )
        return imported

    def test_every_module_a_managed_file_imports_is_itself_managed(self) -> None:
        managed = self._managed()
        managed_modules = {name for name in managed if name.endswith(".py")}
        self.assertTrue(managed_modules, "no Python modules are managed at all")

        for name in sorted(managed_modules):
            module_path = SKILL_DIR / name
            with self.subTest(module=name):
                self.assertTrue(module_path.is_file(), f"{name} is managed but absent")
                for imported in sorted(self._sibling_imports(module_path)):
                    self.assertIn(
                        f"{imported}.py",
                        managed_modules,
                        f"{name} imports the sibling module {imported!r}, which "
                        f"install.sh does not propagate — on an installed "
                        f"harness that import raises ModuleNotFoundError",
                    )

    def test_uninstall_removes_every_file_install_manages(self) -> None:
        """The mirror image, and the same drift in the other direction: a
        module added to `MANAGED_FILES` and forgotten in `uninstall.sh` is
        left behind on every uninstall, where a stale copy goes on being
        imported by whatever else remains."""
        removed = set(self._bash_array(UNINSTALL_SH, "INSTALLED_FILES"))

        for name in self._managed():
            with self.subTest(managed_file=name):
                self.assertIn(
                    name,
                    removed,
                    f"install.sh installs {name} but uninstall.sh never removes it",
                )

    def test_an_installed_harness_can_import_the_production_path(self) -> None:
        """The failure itself, reproduced end to end.

        Before `MANAGED_FILES` was fixed this exited non-zero with
        `ModuleNotFoundError: No module named 'learning_journal'`, raised from
        `production_invoker`'s module-level import — the import
        `advisory_consultation` performs the moment a caller does not inject
        its own worker.
        """
        with _InstalledHarness() as harness:
            result = harness.run_installed_python(
                "import advisory_consultation, learning_outcomes, "
                "production_invoker, routing_check\n"
                "print('imports ok')"
            )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("imports ok", result.stdout)

    def test_an_installed_consultation_runs_and_journals(self) -> None:
        """One step past importing: the production path a real consultation
        takes, from an installed copy, ending in a journal record.

        `production_invoker.invoke_worker` is replaced inside the child so no
        worker CLI is launched; everything else — the lazy import, the
        journaling factory, the record, the write — is the installed code
        running for real.
        """
        program = (
            "import sys\n"
            "from pathlib import Path\n"
            "import advisory_consultation, production_invoker\n"
            "replies = iter(['Planner plan.', 'VERDICT: APPROVE\\nLooks solid.'])\n"
            "production_invoker.invoke_worker = (\n"
            "    lambda model, effort, prompt, **kwargs: next(replies)\n"
            ")\n"
            "result = advisory_consultation.run_advisory_consultation_debate(\n"
            "    'Plan the auth rewrite', root_dir=Path(sys.argv[1]),\n"
            "    task_id='installed-consultation-1',\n"
            ")\n"
            "print(result.outcome)\n"
            "print(result.error)\n"
        )
        with _InstalledHarness() as harness:
            result = harness.run_installed_python(program, str(harness.project))
            journal = harness.project / ".ralph" / "learning_journal.jsonl"
            records = _read_jsonl(journal) if journal.exists() else []

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.splitlines()[0], "consensus")
        self.assertEqual(result.stdout.splitlines()[1], "None")
        self.assertEqual(len(records), 2)
        for record in records:
            self.assertEqual(record["kind"], "worker_execution")
            self.assertEqual(record["task_id"], "installed-consultation-1")
            self.assertTrue(record["success"])


class InstalledAuditJournalRootTests(unittest.TestCase):
    """Where an installed `routing-audit.sh` gets its journal root.

    Not from `SCRIPT_DIR`: `install.sh` copies the script to five
    directories, two under `$HOME`, so a fixed walk up from it resolves to
    the repository only in a dev checkout. The root comes from the repository
    the audit is *run in* — the same tree `.ralph/routing_telemetry.jsonl`
    already lives in — with `LEARNING_JOURNAL_ROOT` as the explicit override
    and no fallback beyond those two.
    """

    def test_installed_audit_journals_into_the_repository_it_is_run_in(self) -> None:
        with _InstalledHarness() as harness:
            result = harness.run_installed_audit(cwd=harness.project)
            journal = harness.project / ".ralph" / "learning_journal.jsonl"
            records = _read_jsonl(journal) if journal.exists() else []
            install_prefix_journal = (
                harness.home / ".gemini" / "config" / ".ralph" / "learning_journal.jsonl"
            )
            leaked = install_prefix_journal.exists()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(leaked, "the journal followed the script instead of the repo")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["kind"], "compliance")
        self.assertEqual(records[0]["session_id"], harness.conv_id)

    def test_learning_journal_root_still_overrides_the_repository(self) -> None:
        """The override is what makes a non-git project, or a deliberately
        separate journal location, expressible at all — so it must keep
        winning over the resolved repository, not merely fill in for it."""
        with _InstalledHarness() as harness:
            elsewhere = harness.project.parent / "elsewhere"
            elsewhere.mkdir()

            result = harness.run_installed_audit(
                cwd=harness.project, LEARNING_JOURNAL_ROOT=str(elsewhere)
            )
            redirected = _read_jsonl(elsewhere / ".ralph" / "learning_journal.jsonl")
            in_repo = (harness.project / ".ralph" / "learning_journal.jsonl").exists()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(in_repo)
        self.assertEqual(len(redirected), 1)
        self.assertEqual(redirected[0]["session_id"], harness.conv_id)

    def test_no_resolvable_root_skips_persistence_without_inventing_one(self) -> None:
        """Run from outside any repository and with no override, there is no
        destination — and "no destination" means nothing is written, not that
        the process's working directory is promoted into one. The audit still
        runs, still prints, and still relays its own exit code."""
        with _InstalledHarness() as harness:
            outside = harness.project.parent / "not-a-repo"
            outside.mkdir()

            result = harness.run_installed_audit(cwd=outside)
            stray = sorted(harness.home.rglob("learning_journal.jsonl"))
            stray += sorted(outside.rglob("learning_journal.jsonl"))
            stray += sorted(harness.project.rglob("learning_journal.jsonl"))

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("No violations detected", result.stdout)
        self.assertEqual(stray, [], f"a destination was invented: {stray}")


class ConsultationSurvivesJournalWiringFailureTests(unittest.TestCase):
    """Ticket 13's rule, applied to the setup step and not only the write.

    `_resolve_task_id` returns a caller-supplied `task_id` verbatim and
    unvalidated, while `TaskLabel.for_task` rejects anything off
    `TASK_ID_RE`. Both used to sit inside one `try` whose `except` returned
    `worker_error`, so a caller-supplied id with a space in it failed the
    entire consultation before a worker was contacted — instrumentation
    aborting the thing it exists to measure.
    """

    JOURNAL_RELATIVE_PATH = Path(".ralph") / "learning_journal.jsonl"

    # Unannotated return for the reason `PersistComplianceRecordTests._report`
    # gives: `advisory_consultation.AdvisoryDebateResult` is not a name mypy
    # can resolve through a path-loaded module.
    def _run(self, task_id: str, root: Path):
        with mock.patch.object(
            production_invoker,
            "invoke_worker",
            side_effect=["Planner's proposed plan.", "VERDICT: APPROVE\nLooks solid."],
        ):
            return advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite", root_dir=root, task_id=task_id
            )

    def test_a_task_id_the_journal_rejects_does_not_fail_the_consultation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = self._run("not a valid task id", root)
            journal_written = (root / self.JOURNAL_RELATIVE_PATH).exists()

        self.assertEqual(result.outcome, "consensus")
        self.assertTrue(result.consensus_reached)
        self.assertEqual(result.final_plan, "Planner's proposed plan.")
        self.assertFalse(journal_written, "an unjournalable id journaled anyway")

    def test_the_wiring_failure_is_reported_on_the_result_not_swallowed(self) -> None:
        """Degrading silently would be its own defect: the run is genuinely
        unmeasured, and `_fold_error` is this module's named mechanism for
        saying so without displacing the real outcome."""
        with tempfile.TemporaryDirectory() as tmp:
            result = self._run("not a valid task id", Path(tmp))

        assert result.error is not None
        self.assertIn("journaling disabled", result.error)
        self.assertEqual(result.outcome, "consensus")

    def test_a_valid_task_id_still_journals_normally(self) -> None:
        """The guard must not have turned journaling into a no-op."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = self._run("task-still-journaled-1", root)
            records = _read_jsonl(root / self.JOURNAL_RELATIVE_PATH)

        self.assertIsNone(result.error)
        self.assertEqual(len(records), 2)
        for record in records:
            self.assertEqual(record["task_id"], "task-still-journaled-1")
            self.assertEqual(record["kind"], "worker_execution")


if __name__ == "__main__":
    unittest.main()
