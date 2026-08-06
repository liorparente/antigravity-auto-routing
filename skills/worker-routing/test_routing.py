#!/usr/bin/env python3
"""Unit and integration tests for routing_check.py, routing-audit.sh, and
the install.sh / uninstall.sh protocol.md single-sourcing.

Run with:
    python3 -m unittest skills/worker-routing/test_routing.py -v
or, from this directory:
    python3 test_routing.py
"""
from __future__ import annotations

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
        metrics = routing_check.compute_metrics([step], ["py"], ["claude -p"], safe_patterns)
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
        metrics = routing_check.compute_metrics([step], ["py"], [], safe_patterns)
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
            'IN_WORKER_ROUTING=true codex exec --model gpt-5.6-luna '
            '-c model_reasoning_effort="low" -s workspace-write "..." < /dev/null',
            'IN_WORKER_ROUTING=true codex exec --model gpt-5.6-terra '
            '-c model_reasoning_effort="medium" -s workspace-write "..." < /dev/null',
            'IN_WORKER_ROUTING=true claude -p --model claude-sonnet-5 '
            '-c model_reasoning_effort="high" --allow-dangerously-skip-permissions '
            '"..." < /dev/null',
            "IN_WORKER_ROUTING=true codex review --uncommitted -s workspace-write "
            '-c model="gpt-5.6-sol" -c model_reasoning_effort="high" < /dev/null',
            'IN_WORKER_ROUTING=true agy -p "..." < /dev/null',
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

    def test_cmd01_chained_command_with_unsafe_tail_flagged(self) -> None:
        step = routing_check.Step(1, "[ROUTING: codex — complexity: simple — effort: low — reason: test]")
        step.commands.append("IN_WORKER_ROUTING=true codex exec --model gpt-5.6-luna -c model_reasoning_effort=\"low\" && touch x.py")
        metrics = routing_check.compute_metrics([step], self.code_extensions, self.worker_patterns, self.safe_patterns)
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
            [step], self.code_extensions, self.worker_patterns, self.safe_patterns
        )
        self.assertEqual(len(metrics["violations"]), 1)

    def test_dec01_model_declaration_drift_detected(self) -> None:
        step = routing_check.Step(1, "[ROUTING: Codex Sol — complexity: complex — effort: high — reason: test]")
        step.commands.append("IN_WORKER_ROUTING=true codex exec --model gpt-5.6-luna -c model_reasoning_effort=\"low\"")
        metrics = routing_check.compute_metrics([step], self.code_extensions, self.worker_patterns, self.safe_patterns)
        self.assertEqual(len(metrics["violations"]), 1)

    def test_dec02_effort_declaration_drift_detected(self) -> None:
        step = routing_check.Step(1, "[ROUTING: codex — complexity: complex — effort: high — reason: test]")
        step.commands.append("IN_WORKER_ROUTING=true codex exec --model gpt-5.6-sol -c model_reasoning_effort=\"low\"")
        metrics = routing_check.compute_metrics([step], self.code_extensions, self.worker_patterns, self.safe_patterns)
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
            'claude -p --model claude-sonnet-5 -c model_reasoning_effort="high" '
            '--allow-dangerously-skip-permissions "implement feature" < /dev/null'
        )
        metrics = routing_check.compute_metrics(
            [agy, review, claude], self.code_extensions, self.worker_patterns, self.safe_patterns
        )
        self.assertEqual(metrics["violations"], [])

    def test_dec04_claude_v5_opus_and_sonnet_routing_steps_valid(self) -> None:
        opus_step = routing_check.Step(
            1, "[ROUTING: Claude Opus 5 — complexity: complex — effort: high — reason: plan architectural changes]"
        )
        opus_step.commands.append(
            'claude -p --model claude-opus-5 -c model_reasoning_effort="high" "plan feature" < /dev/null'
        )
        sonnet_step = routing_check.Step(
            2, "[ROUTING: Claude Sonnet 5 — complexity: medium — effort: high — reason: execute implementation]"
        )
        sonnet_step.commands.append(
            'claude -p --model claude-sonnet-5 -c model_reasoning_effort="high" "implement feature" < /dev/null'
        )
        metrics = routing_check.compute_metrics(
            [opus_step, sonnet_step], self.code_extensions, self.worker_patterns, self.safe_patterns
        )
        self.assertEqual(metrics["violations"], [])

    def test_dec05_retired_claude_v4_6_model_declarations_rejected(self) -> None:
        step = routing_check.Step(
            1, "[ROUTING: Claude Sonnet 4.6 — complexity: medium — effort: high — reason: legacy model test]"
        )
        step.commands.append(
            'claude -p --model claude-sonnet-5 -c model_reasoning_effort="high" "implement feature" < /dev/null'
        )
        metrics = routing_check.compute_metrics(
            [step], self.code_extensions, self.worker_patterns, self.safe_patterns
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
        metrics = routing_check.compute_metrics([step], self.code_extensions, self.worker_patterns, self.safe_patterns)
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
        return routing_check.compute_metrics(
            [step],
            self.code_extensions,
            self.worker_patterns,
            self.safe_patterns,
            root_dir=root_dir,
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


if __name__ == "__main__":
    unittest.main()
