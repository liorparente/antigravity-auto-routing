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


if __name__ == "__main__":
    unittest.main()
