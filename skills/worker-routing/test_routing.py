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
from collections.abc import Iterator, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, get_args
from unittest import mock

_COUNCIL_SECRET_PATCHER: object | None = None


def setUpModule() -> None:
    """Give legacy panel tests the signing secret CouncilPanel now requires."""
    global _COUNCIL_SECRET_PATCHER
    _COUNCIL_SECRET_PATCHER = mock.patch.object(
        advisory_consultation._debate_orchestrator,
        "resolve_hmac_secret",
        return_value=b"worker-routing-test-secret",
    )
    _COUNCIL_SECRET_PATCHER.start()  # type: ignore[attr-defined]


def tearDownModule() -> None:
    if _COUNCIL_SECRET_PATCHER is not None:
        _COUNCIL_SECRET_PATCHER.stop()  # type: ignore[attr-defined]

if TYPE_CHECKING:
    from collections.abc import Iterator

    # For type annotations only — at runtime `advisory_consultation` is the
    # dynamically loaded module object below, whose attributes mypy cannot
    # resolve inside annotations.
    from advisory_consultation import CanaryFixture, IsFamilyReachable
    from learning_journal import OutcomeRecord

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

learned_state_spec = importlib.util.spec_from_file_location(
    "learned_state", SKILL_DIR / "learned_state.py"
)
assert learned_state_spec is not None and learned_state_spec.loader is not None
learned_state = importlib.util.module_from_spec(learned_state_spec)
sys.modules["learned_state"] = learned_state
learned_state_spec.loader.exec_module(learned_state)


def _bash_array(script: Path, name: str) -> list[str]:
    """Parse a `NAME=(a b c)` bash array literal out of `script`'s source.

    Shared by `LearnedStatePropagationTests` and `ManagedFileClosureTests` —
    both need `install.sh`'s `MANAGED_FILES` without executing the script.
    """
    text = script.read_text(encoding="utf-8")
    match = re.search(rf"^{name}=\(([^)]*)\)", text, re.MULTILINE)
    assert match is not None, f"{name} not found in {script}"
    return match.group(1).split()


def _target_dirs(script: Path, *, home: str, target_project_dir: str) -> tuple[Path, ...]:
    """Resolve `script`'s `TARGET_DIRS` bash array into concrete paths, the
    same way the shell would substitute `$HOME` and `$TARGET_PROJECT_DIR`.

    Reading `TARGET_DIRS` from the script itself — rather than hardcoding the
    five resolved paths in tests — means a target added to (or dropped from)
    install.sh's array is exercised here without the test file drifting out
    of sync with it.
    """
    return tuple(
        Path(
            raw.strip('"')
            .replace("$HOME", home)
            .replace("$TARGET_PROJECT_DIR", target_project_dir)
        )
        for raw in _bash_array(script, "TARGET_DIRS")
    )


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

    def test_consultation_policy_is_not_a_worker_role(self) -> None:
        self.assertIn("consultation_policy", routing_check.NON_ROLE_CONFIG_KEYS)
        patterns = routing_check.load_patterns(
            {
                "light_doer": {"patterns": ["expected-worker"]},
                "consultation_policy": {"patterns": ["must-not-be-a-worker"]},
            }
        )
        self.assertEqual(patterns, ["expected-worker"])

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

    def test_routing_check_resolves_transparently_in_standalone_and_package_mode(self) -> None:
        res_direct = subprocess.run(
            [sys.executable, str(ROUTING_CHECK), str(FIXTURES_DIR / "clean_log.txt")],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(res_direct.returncode, 0, res_direct.stdout + res_direct.stderr)
        self.assertIn("No violations detected", res_direct.stdout)


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

    def test_install_sh_copies_init_py_to_skill_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as fake_home, tempfile.TemporaryDirectory() as target_dir:
            result = self._run(INSTALL_SH, target_dir, home=fake_home)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            init_text = (SKILL_DIR / "__init__.py").read_text()
            for installed_dir in (
                Path(fake_home) / ".gemini" / "config" / "skills" / "worker-routing",
                Path(fake_home) / ".codex" / "skills" / "worker-routing",
                Path(target_dir) / ".agents" / "skills" / "worker-routing",
                Path(target_dir) / ".agent" / "skills" / "worker-routing",
                Path(target_dir) / ".codex" / "skills" / "worker-routing",
            ):
                installed_init = installed_dir / "__init__.py"
                self.assertTrue(installed_init.exists(), str(installed_init))
                self.assertEqual(installed_init.read_text(), init_text)

    def test_install_sh_synchronizes_council_review_and_removes_legacy_policy(
        self,
    ) -> None:
        with (
            tempfile.TemporaryDirectory() as fake_home,
            tempfile.TemporaryDirectory() as target_dir,
        ):
            worker_targets = _target_dirs(
                INSTALL_SH,
                home=fake_home,
                target_project_dir=target_dir,
            )
            council_targets = tuple(
                target.parent / "council-review" for target in worker_targets
            )
            for council_target in council_targets:
                legacy_policy = council_target / "references" / "council-policy.json"
                legacy_policy.parent.mkdir(parents=True, exist_ok=True)
                legacy_policy.write_text("{}\n", encoding="utf-8")

            result = self._run(INSTALL_SH, target_dir, home=fake_home)

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            for council_target in council_targets:
                with self.subTest(council_target=council_target):
                    self.assertEqual(
                        (council_target / "SKILL.md").read_text(encoding="utf-8"),
                        (
                            REPO_ROOT / "skills" / "council-review" / "SKILL.md"
                        ).read_text(encoding="utf-8"),
                    )
                    self.assertTrue(
                        (council_target / "scripts" / "council_review.py").exists()
                    )
                    self.assertFalse(
                        (council_target / "references" / "council-policy.json").exists()
                    )

    def test_install_sh_merges_missing_consultation_policy_into_custom_config(self) -> None:
        with tempfile.TemporaryDirectory() as fake_home, tempfile.TemporaryDirectory() as target_dir:
            missing_policy_dir = (
                Path(target_dir) / ".agents" / "skills" / "worker-routing"
            )
            existing_policy_dir = (
                Path(target_dir) / ".codex" / "skills" / "worker-routing"
            )
            missing_policy_dir.mkdir(parents=True)
            existing_policy_dir.mkdir(parents=True)
            (missing_policy_dir / "routing-config.json").write_text(
                json.dumps({"custom": {"preserved": True}}), encoding="utf-8"
            )
            existing_policy = {"custom_policy": True}
            (existing_policy_dir / "routing-config.json").write_text(
                json.dumps(
                    {
                        "custom": {"preserved": True},
                        "consultation_policy": existing_policy,
                    }
                ),
                encoding="utf-8",
            )

            result = self._run(INSTALL_SH, target_dir, home=fake_home)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            source_config = json.loads((SKILL_DIR / "routing-config.json").read_text())
            merged = json.loads(
                (missing_policy_dir / "routing-config.json").read_text(encoding="utf-8")
            )
            preserved = json.loads(
                (existing_policy_dir / "routing-config.json").read_text(encoding="utf-8")
            )
            self.assertEqual(merged["custom"], {"preserved": True})
            self.assertEqual(
                merged["consultation_policy"], source_config["consultation_policy"]
            )
            self.assertEqual(preserved["consultation_policy"], existing_policy)

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
            # .agents/ and .agent/ held nothing but the installed skill, so
            # they are reclaimed entirely once empty — same as .codex/.
            self.assertFalse((Path(target_dir) / ".agents" / "skills" / "worker-routing").exists())
            self.assertFalse((Path(target_dir) / ".agents").exists())
            self.assertFalse((Path(target_dir) / ".agent" / "skills" / "worker-routing").exists())
            self.assertFalse((Path(target_dir) / ".agent").exists())

    def test_uninstall_sh_removes_local_agents_dir_skill_files(self) -> None:
        # uninstall.sh's TARGET_DIRS now covers the project-local .agents/
        # directory too (spec 0004 ticket 34), matching install.sh's parity.
        # The now-empty "skills/worker-routing" and "skills" convention
        # directories are reclaimed along with it.
        with tempfile.TemporaryDirectory() as fake_home, tempfile.TemporaryDirectory() as target_dir:
            self._run(INSTALL_SH, target_dir, home=fake_home)
            agents_skill_dir = Path(target_dir) / ".agents" / "skills" / "worker-routing"
            self.assertTrue((agents_skill_dir / "protocol.md").exists())

            result = self._run(UNINSTALL_SH, target_dir, home=fake_home)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            self.assertFalse(agents_skill_dir.exists())
            self.assertFalse((Path(target_dir) / ".agents" / "skills").exists())

    def test_uninstall_sh_removes_local_agent_dir_skill_files(self) -> None:
        # Same parity fix for the singular ".agent/" convention directory.
        with tempfile.TemporaryDirectory() as fake_home, tempfile.TemporaryDirectory() as target_dir:
            self._run(INSTALL_SH, target_dir, home=fake_home)
            agent_skill_dir = Path(target_dir) / ".agent" / "skills" / "worker-routing"
            self.assertTrue((agent_skill_dir / "protocol.md").exists())

            result = self._run(UNINSTALL_SH, target_dir, home=fake_home)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            self.assertFalse(agent_skill_dir.exists())
            self.assertFalse((Path(target_dir) / ".agent" / "skills").exists())

    def test_uninstall_sh_preserves_other_content_in_local_agents_dir(self) -> None:
        # .agents/ is a shared convention directory other tools may also
        # populate. Uninstall must remove only what it installed, never
        # another tool's files or the directories holding them.
        with tempfile.TemporaryDirectory() as fake_home, tempfile.TemporaryDirectory() as target_dir:
            self._run(INSTALL_SH, target_dir, home=fake_home)
            other_skill_dir = Path(target_dir) / ".agents" / "skills" / "other-skill"
            other_skill_dir.mkdir(parents=True)
            (other_skill_dir / "notes.md").write_text("keep me\n")

            result = self._run(UNINSTALL_SH, target_dir, home=fake_home)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            self.assertFalse(
                (Path(target_dir) / ".agents" / "skills" / "worker-routing").exists()
            )
            self.assertEqual((other_skill_dir / "notes.md").read_text(), "keep me\n")
            self.assertTrue((Path(target_dir) / ".agents" / "skills").exists())
            self.assertTrue((Path(target_dir) / ".agents").exists())

    def test_uninstall_sh_preserves_other_content_in_local_agent_dir(self) -> None:
        # Same preservation guarantee for the singular ".agent/" directory,
        # exercised with a file directly under ".agent/" rather than nested
        # under "skills/".
        with tempfile.TemporaryDirectory() as fake_home, tempfile.TemporaryDirectory() as target_dir:
            self._run(INSTALL_SH, target_dir, home=fake_home)
            agent_dir = Path(target_dir) / ".agent"
            (agent_dir / "other-file").write_text("keep me too\n")

            result = self._run(UNINSTALL_SH, target_dir, home=fake_home)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            self.assertFalse((agent_dir / "skills" / "worker-routing").exists())
            self.assertEqual((agent_dir / "other-file").read_text(), "keep me too\n")
            self.assertTrue(agent_dir.exists())

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

    def test_target_dirs_parity_between_install_and_uninstall_sh(self) -> None:
        # A target install.sh writes to but uninstall.sh's TARGET_DIRS omits
        # (or vice versa) either strands learned state and skill files
        # forever, or makes uninstall touch a directory install.sh never
        # created. Ticket 34 fixed one such drift (.agents/, .agent/); this
        # closure test keeps future drift from recurring silently.
        self.assertEqual(
            _bash_array(INSTALL_SH, "TARGET_DIRS"),
            _bash_array(UNINSTALL_SH, "TARGET_DIRS"),
        )

    def test_project_local_indices_closure_over_target_dirs(self) -> None:
        # PROJECT_LOCAL_INDICES is a hardcoded index list into TARGET_DIRS
        # with nothing tying the two together — a future reorder/add/remove
        # in either array could silently desync them, letting the
        # parent-directory reclaim ascend past a home-directory target or
        # skip a project-local one. Derive both arrays from uninstall.sh's
        # own source (rather than hardcoding them again here) and check
        # every index against the raw $TARGET_PROJECT_DIR / $HOME prefix, so
        # future drift fails this test instead of silently misbehaving.
        target_dirs = _bash_array(UNINSTALL_SH, "TARGET_DIRS")
        project_local_indices = {
            int(raw) for raw in _bash_array(UNINSTALL_SH, "PROJECT_LOCAL_INDICES")
        }

        for index in project_local_indices:
            self.assertGreaterEqual(index, 0)
            self.assertLess(index, len(target_dirs), f"index {index} is out of range for TARGET_DIRS")

        for index, raw_target in enumerate(target_dirs):
            if index in project_local_indices:
                self.assertTrue(
                    raw_target.startswith('"$TARGET_PROJECT_DIR/'),
                    f"TARGET_DIRS[{index}] = {raw_target} is in PROJECT_LOCAL_INDICES "
                    "but is not rooted at $TARGET_PROJECT_DIR",
                )
            else:
                self.assertFalse(
                    raw_target.startswith('"$TARGET_PROJECT_DIR/'),
                    f"TARGET_DIRS[{index}] = {raw_target} is rooted at $TARGET_PROJECT_DIR "
                    "but missing from PROJECT_LOCAL_INDICES",
                )

    def test_uninstall_sh_does_not_ascend_past_home_directory_skill_dirs(self) -> None:
        # Parent-directory reclaim is scoped to project-local targets only.
        # "$HOME/.gemini/config" and "$HOME/.codex" are not directories
        # install.sh created solely to hold this skill, so uninstall must
        # never remove them even once their "skills/worker-routing" child is
        # gone.
        with tempfile.TemporaryDirectory() as fake_home, tempfile.TemporaryDirectory() as target_dir:
            self._run(INSTALL_SH, target_dir, home=fake_home)
            result = self._run(UNINSTALL_SH, target_dir, home=fake_home)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            self.assertFalse(
                (Path(fake_home) / ".gemini" / "config" / "skills" / "worker-routing").exists()
            )
            self.assertFalse((Path(fake_home) / ".codex" / "skills" / "worker-routing").exists())
            self.assertTrue((Path(fake_home) / ".gemini" / "config" / "skills").exists())
            self.assertTrue((Path(fake_home) / ".gemini" / "config").exists())
            self.assertTrue((Path(fake_home) / ".codex" / "skills").exists())
            self.assertTrue((Path(fake_home) / ".codex").exists())

    def test_uninstall_sh_reports_reclaimed_parent_convention_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as fake_home, tempfile.TemporaryDirectory() as target_dir:
            self._run(INSTALL_SH, target_dir, home=fake_home)
            result = self._run(UNINSTALL_SH, target_dir, home=fake_home)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            # Anchored on the exact full line rather than a bare path
            # substring: ".agents" is itself a substring of ".agents/skills",
            # so a substring-only assertion would pass even if the "skills"
            # line were missing (or vice versa) as long as either mention of
            # the path happened to appear anywhere in stdout.
            for path in (
                Path(target_dir) / ".agents" / "skills",
                Path(target_dir) / ".agents",
                Path(target_dir) / ".agent" / "skills",
                Path(target_dir) / ".agent",
            ):
                self.assertIn(f"✅ Removed empty {path}\n", result.stdout)

    def test_uninstall_sh_does_not_ascend_past_home_when_target_project_dir_is_home(
        self,
    ) -> None:
        # Regression for the path-prefix check this replaced: when
        # TARGET_PROJECT_DIR resolves to exactly $HOME, "$target_dir starts
        # with $TARGET_PROJECT_DIR" is true for the home-directory targets
        # too, so a prefix check would incorrectly treat
        # "$HOME/.gemini/config/skills/worker-routing" and
        # "$HOME/.codex/skills/worker-routing" as project-local and ascend
        # into "$HOME/.gemini/config" and "$HOME/.codex". Scoping by index
        # instead must keep them intact regardless of where
        # TARGET_PROJECT_DIR points.
        with tempfile.TemporaryDirectory() as fake_home:
            self._run(INSTALL_SH, fake_home, home=fake_home)
            result = self._run(UNINSTALL_SH, fake_home, home=fake_home)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            self.assertFalse(
                (Path(fake_home) / ".gemini" / "config" / "skills" / "worker-routing").exists()
            )
            self.assertFalse((Path(fake_home) / ".codex" / "skills" / "worker-routing").exists())
            self.assertTrue((Path(fake_home) / ".gemini" / "config").exists())
            self.assertTrue((Path(fake_home) / ".codex").exists())


class LearnedStatePropagationTests(unittest.TestCase):
    """Spec 0004 ticket 23: adopted learned state propagates across harnesses
    through `install.sh`'s existing atomic staging/sync mechanism — it does
    not get a second, parallel one.

    `install.sh` resolves `learned_state.current_version_dir` against
    `SCRIPT_DIR` (the directory `install.sh` itself lives in), so a test
    that adopted state directly into this checkout's own `learned-state/`
    would mutate the real, git-tracked, currently-empty store. Every test
    here instead builds an isolated *source* tree — a scratch copy of
    `install.sh` plus exactly the files it reads from `SRC_DIR` — and adopts
    into that copy's own `SCRIPT_DIR` instead.
    """

    def _isolated_source_tree(self) -> Path:
        """A scratch `install.sh` plus a minimal `skills/worker-routing`
        holding only `MANAGED_FILES` and `routing-config.json` — what
        `install.sh` actually reads from `SRC_DIR` — not a full copy of this
        skill directory's caches and other test files."""
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        source_root = Path(tmp)
        shutil.copy(INSTALL_SH, source_root / "install.sh")
        worker_routing_dir = source_root / "skills" / "worker-routing"
        worker_routing_dir.mkdir(parents=True)
        for name in [*_bash_array(INSTALL_SH, "MANAGED_FILES"), "routing-config.json"]:
            shutil.copy(SKILL_DIR / name, worker_routing_dir / name)
        shutil.copytree(
            REPO_ROOT / "skills" / "council-review",
            source_root / "skills" / "council-review",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        return source_root

    def _adopt(self, source_root: Path, **document_contents: str) -> None:
        learned_state.adopt(
            [
                learned_state.DocumentChange(document=document, content=content)  # type: ignore[arg-type]
                for document, content in document_contents.items()
            ],
            root_dir=source_root,
            now=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )

    def _run_install(
        self, source_root: Path, target_dir: str, *, home: str, **env_overrides: str
    ) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ)
        env["HOME"] = str(home)
        env.update(env_overrides)
        return subprocess.run(
            ["bash", str(source_root / "install.sh"), target_dir],
            capture_output=True,
            check=False,
            text=True,
            env=env,
        )

    def _installed_dirs(self, fake_home: str, target_dir: str) -> tuple[Path, ...]:
        return _target_dirs(INSTALL_SH, home=fake_home, target_project_dir=target_dir)

    def test_a_successful_install_propagates_adopted_learned_state_to_every_harness(
        self,
    ) -> None:
        source_root = self._isolated_source_tree()
        self._adopt(source_root, memory="memory v1", briefs="briefs v1")

        with tempfile.TemporaryDirectory() as fake_home, tempfile.TemporaryDirectory() as target_dir:
            result = self._run_install(source_root, target_dir, home=fake_home)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            for installed_dir in self._installed_dirs(fake_home, target_dir):
                with self.subTest(installed_dir=installed_dir):
                    history = installed_dir / "learned-state" / "history.jsonl"
                    version_dir = installed_dir / "learned-state" / "versions" / "v0001"
                    self.assertTrue(history.exists())
                    self.assertEqual((version_dir / "memory").read_text(), "memory v1")
                    self.assertEqual((version_dir / "briefs").read_text(), "briefs v1")

                    self.assertEqual(
                        learned_state.read_current(root_dir=installed_dir),
                        {"memory": "memory v1", "briefs": "briefs v1"},
                    )
                    self.assertEqual(
                        learned_state.current_version_dir(root_dir=installed_dir),
                        version_dir,
                    )

    def test_install_without_any_adopted_learned_state_installs_cleanly(self) -> None:
        source_root = self._isolated_source_tree()
        self.assertFalse((source_root / "learned-state").exists())

        with tempfile.TemporaryDirectory() as fake_home, tempfile.TemporaryDirectory() as target_dir:
            result = self._run_install(source_root, target_dir, home=fake_home)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            for installed_dir in self._installed_dirs(fake_home, target_dir):
                with self.subTest(installed_dir=installed_dir):
                    self.assertTrue((installed_dir / "SKILL.md").exists())
                    self.assertTrue((installed_dir / "protocol.md").exists())
                    self.assertFalse((installed_dir / "learned-state").exists())
                    self.assertIsNone(learned_state.current_version_dir(root_dir=installed_dir))

    def test_install_with_existing_but_unadopted_learned_state_directory_installs_cleanly(
        self,
    ) -> None:
        # Exercises the unadopted-store preflight branch on an orphaned snapshot
        # (versions/v0001 present but no history.jsonl line yet — Decision 2).
        source_root = self._isolated_source_tree()
        orphan_version = source_root / "learned-state" / "versions" / "v0001"
        orphan_version.mkdir(parents=True)
        (orphan_version / "memory").write_text("orphan v1")

        with tempfile.TemporaryDirectory() as fake_home, tempfile.TemporaryDirectory() as target_dir:
            result = self._run_install(source_root, target_dir, home=fake_home)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            for installed_dir in self._installed_dirs(fake_home, target_dir):
                with self.subTest(installed_dir=installed_dir):
                    self.assertTrue((installed_dir / "SKILL.md").exists())
                    self.assertTrue((installed_dir / "protocol.md").exists())
                    self.assertFalse((installed_dir / "learned-state").exists())
                    self.assertIsNone(
                        learned_state.current_version_dir(root_dir=installed_dir)
                    )

    def test_a_missing_snapshot_directory_aborts_preflight_without_mutating_any_target(
        self,
    ) -> None:
        # history.jsonl names version 1, but its snapshot directory is missing.
        source_root = self._isolated_source_tree()
        self._adopt(source_root, memory="memory v1")
        shutil.rmtree(source_root / "learned-state" / "versions" / "v0001")

        with tempfile.TemporaryDirectory() as fake_home, tempfile.TemporaryDirectory() as target_dir:
            result = self._run_install(source_root, target_dir, home=fake_home)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("learned-state", result.stdout + result.stderr)

            for installed_dir in self._installed_dirs(fake_home, target_dir):
                with self.subTest(installed_dir=installed_dir):
                    self.assertFalse(installed_dir.exists())
            self.assertFalse((Path(target_dir) / "AGENTS.md").exists())
            self.assertFalse((Path(target_dir) / "CLAUDE.md").exists())

    def test_uninstall_sh_removes_installed_learned_state_from_target_dirs(
        self,
    ) -> None:
        source_root = self._isolated_source_tree()
        self._adopt(source_root, memory="memory v1", briefs="briefs v1")

        with tempfile.TemporaryDirectory() as fake_home, tempfile.TemporaryDirectory() as target_dir:
            result = self._run_install(source_root, target_dir, home=fake_home)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            for installed_dir in self._installed_dirs(fake_home, target_dir):
                self.assertTrue((installed_dir / "learned-state").exists())

            env = dict(os.environ)
            env["HOME"] = str(fake_home)
            uninst_res = subprocess.run(
                ["bash", str(UNINSTALL_SH), target_dir],
                capture_output=True,
                check=False,
                text=True,
                env=env,
            )
            self.assertEqual(uninst_res.returncode, 0, uninst_res.stdout + uninst_res.stderr)

            for installed_dir in self._installed_dirs(fake_home, target_dir):
                with self.subTest(installed_dir=installed_dir):
                    self.assertFalse(installed_dir.exists())
                    self.assertFalse((installed_dir / "learned-state").exists())

    def test_uninstall_sh_removes_learned_state_but_preserves_custom_file_in_surviving_dir(
        self,
    ) -> None:
        # A skill directory that survives uninstall (because it still holds
        # non-installer content) must lose its learned-state just like one
        # that gets removed outright — learned-state removal is not
        # conditional on the directory itself disappearing.
        source_root = self._isolated_source_tree()
        self._adopt(source_root, memory="memory v1")

        with tempfile.TemporaryDirectory() as fake_home, tempfile.TemporaryDirectory() as target_dir:
            result = self._run_install(source_root, target_dir, home=fake_home)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            codex_skill_dir = Path(target_dir) / ".codex" / "skills" / "worker-routing"
            self.assertTrue((codex_skill_dir / "learned-state").exists())
            (codex_skill_dir / "my-custom-notes.txt").write_text("keep me\n")

            env = dict(os.environ)
            env["HOME"] = str(fake_home)
            uninst_res = subprocess.run(
                ["bash", str(UNINSTALL_SH), target_dir],
                capture_output=True,
                check=False,
                text=True,
                env=env,
            )
            self.assertEqual(uninst_res.returncode, 0, uninst_res.stdout + uninst_res.stderr)

            self.assertTrue(codex_skill_dir.exists())
            self.assertEqual((codex_skill_dir / "my-custom-notes.txt").read_text(), "keep me\n")
            self.assertFalse((codex_skill_dir / "learned-state").exists())

    def test_a_failure_mid_learned_state_sync_rolls_back_every_learned_state_write(
        self,
    ) -> None:
        source_root = self._isolated_source_tree()
        self._adopt(source_root, memory="memory v1", briefs="briefs v1")

        # MANAGED_FILES writes land first for each target directory; the two
        # learned-state writes ("history.jsonl", then "briefs" — the
        # alphabetically-first adopted document) follow immediately after.
        # Failing on the second of those proves both roll back together,
        # and that a later target directory in the loop is never reached.
        managed_files_count = len(_bash_array(INSTALL_SH, "MANAGED_FILES"))
        fail_after = managed_files_count + 2

        with tempfile.TemporaryDirectory() as fake_home, tempfile.TemporaryDirectory() as target_dir:
            result = self._run_install(
                source_root,
                target_dir,
                home=fake_home,
                AUTO_ROUTING_FAIL_AFTER_WRITES=str(fail_after),
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("AUTO_ROUTING_FAIL_AFTER_WRITES", result.stderr)

            first_installed_dir = Path(fake_home) / ".gemini" / "config" / "skills" / "worker-routing"
            self.assertFalse((first_installed_dir / "learned-state" / "history.jsonl").exists())
            self.assertFalse(
                (first_installed_dir / "learned-state" / "versions" / "v0001" / "briefs").exists()
            )
            # Rollback restores absence, not just the two learned-state
            # files: MANAGED_FILES writes that preceded them in this same
            # target directory are gone too.
            self.assertFalse((first_installed_dir / "SKILL.md").exists())

            second_installed_dir = Path(fake_home) / ".codex" / "skills" / "worker-routing"
            self.assertFalse(second_installed_dir.exists())

    def test_a_corrupted_history_journal_aborts_preflight_without_mutating_any_target(
        self,
    ) -> None:
        source_root = self._isolated_source_tree()
        learned_state_dir = source_root / "learned-state"
        (learned_state_dir / "versions").mkdir(parents=True)
        (learned_state_dir / "history.jsonl").write_text("not valid json\n")

        with tempfile.TemporaryDirectory() as fake_home, tempfile.TemporaryDirectory() as target_dir:
            result = self._run_install(source_root, target_dir, home=fake_home)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("learned-state", result.stdout + result.stderr)

            for installed_dir in self._installed_dirs(fake_home, target_dir):
                with self.subTest(installed_dir=installed_dir):
                    self.assertFalse(installed_dir.exists())
            self.assertFalse((Path(target_dir) / "AGENTS.md").exists())
            self.assertFalse((Path(target_dir) / "CLAUDE.md").exists())


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

    def __init__(self, responses: Mapping[str, Sequence[str | Exception]]) -> None:
        # `Mapping[str, Sequence[...]]` rather than `dict[str, list[...]]`:
        # every call site below builds its per-role queues as plain
        # `list[str]`, and `list` is invariant, so a `list[str | Exception]`
        # parameter rejects them. The queues are copied into this fake's own
        # mutable lists on the next line anyway, so the wider read-only type
        # is what this constructor actually needs.
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
    candidate_hash = hashlib.sha256(artifact_text.encode("utf-8")).hexdigest()
    payload = json.dumps({"candidate_hash": candidate_hash})
    return f'{payload}\n{note}\nQUOTE: "{artifact_text}"\nVERDICT: APPROVE'


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
    candidate_hash = hashlib.sha256(fixture.plan_text.encode("utf-8")).hexdigest()
    payload = json.dumps({"candidate_hash": candidate_hash})
    return f'{payload}\n{note}\nQUOTE: "{quotable_line}"\nVERDICT: APPROVE'


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

    The return type spells out `Callable[[str], bool]` — the definition of
    `advisory_consultation.IsFamilyReachable` — instead of naming that alias:
    this file loads `advisory_consultation` through `importlib` at runtime, so
    a type checker cannot resolve an attribute of it in an annotation.
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

    def test_ralph_directory_contains_nothing_but_the_telemetry_and_journal_files(
        self,
    ) -> None:
        """Ticket 06: telemetry lands at `.ralph/routing_telemetry.jsonl`.
        Spec 0004 ticket 24 adds the second and (as of this ticket) last
        thing a consultation creates under `.ralph`:
        `.ralph/learning_journal.jsonl`, the dialogue-quality record every
        non-halted outcome appends — asserted as an allowlist of the whole
        directory listing, not a denylist of specific names, for the same
        reason ticket 06's version of this test gives: a denylist stops
        catching new writes the moment anything else starts touching the
        directory."""
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
                ["learning_journal.jsonl", "routing_telemetry.jsonl"],
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
        qwen = advisory_consultation.classify_model_family("Qwen3.8-27B-MLX-6bit")
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
                                "planner": ["Qwen3.8-27B-MLX-6bit"],
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
        self.assertEqual(planner.model, "Qwen3.8-27B-MLX-6bit")
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

# Every test below reaches the journal through `learning_journal.journal_path`
# (and `JOURNAL_RELATIVE_PATH` where only the file *name* is wanted), never
# through a locally re-declared `Path(".ralph") / "learning_journal.jsonl"`.
# Six test classes carried their own copy of that constant, plus inline
# `root / ".ralph" / ...` spellings: a test that hardcodes the location it is
# meant to verify cannot catch that location changing — production would move
# and every copy would go on asserting the old place, agreeing with nothing.
# `journal_path` is public and is the resolver production itself uses, so
# calling it is both the shorter spelling and the only one that fails when it
# should.


def _countable_runs(records: list[dict], task_id: str) -> set[str]:
    """The reduction `WorkerExecutionRecord` documents, written out once.

    Rework on a task is the distinct run identities carrying its `task_id`,
    minus one. A record that names no run is *uncountable* — skipped here
    rather than folded into a shared bucket, because folding would report a
    task reworked four times as reworked once, which is the failure the
    optional field exists to make expressible in the first place.
    """
    return {
        record["run_id"]
        for record in records
        if record.get("task_id") == task_id and "run_id" in record
    }


class LearningJournalTests(unittest.TestCase):
    """The journal records what happened without ever recording what it was about.

    Two properties carry this ticket, and both are asserted here as
    observable facts rather than trusted as conventions: every record lands
    in a stream separate from the audited routing telemetry yet joinable to
    it on TaskIdentity, and a record carrying task text, a path, or a matched
    secret cannot be constructed at all.
    """

    TELEMETRY_RELATIVE_PATH = Path(".ralph") / "routing_telemetry.jsonl"

    FIXED_TIMESTAMP = "2026-08-12T09:30:00Z"

    # Checked (linted/type-checked) but deliberately never executed by CI —
    # read by test_ci_runs_every_test_file_it_checks below. Sized to exactly
    # one file today: test_lmstudio.py's own module docstring says "CI lints
    # and type-checks this file, but never executes it," because it makes
    # real `urlopen` calls to a live LM Studio server at 127.0.0.1:1234 with
    # a 120-second timeout — no CI runner has that server, so running it in
    # the test step would hang or fail on every run, not sometimes. That is
    # a structural, already-documented exclusion, unlike the accidental one
    # this whole test exists to catch — test_production_invoker.py, whose 16
    # tests were linted and type-checked but silently never executed. Any
    # other test file missing from PYTHON_TESTS still fails the assertion
    # below; this exempts exactly this one file, by name, not the shape of
    # the check.
    _CHECKED_BUT_NOT_EXECUTED_BY_DESIGN = frozenset(
        {
            "skills/worker-routing/test_lmstudio.py",
            "test_suite.py",
        }
    )

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
            "ground_truth": "tests",
            "verdict": "pass",
            "timestamp": self.FIXED_TIMESTAMP,
        }
        fields.update(overrides)
        return learning_journal.OutcomeRecord(**fields)  # type: ignore[arg-type]

    def _dialogue_quality_record(self, **overrides: object) -> object:
        fields: dict[str, object] = {
            "task": learning_journal.TaskLabel.for_task("task-1"),
            "occasion": "plan-review",
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

    def _replay_benchmark_record(self, **overrides: object) -> object:
        fields: dict[str, object] = {
            "task_set": "bench-v1",
            "success": True,
            "score": 0.82,
            "timestamp": self.FIXED_TIMESTAMP,
        }
        fields.update(overrides)
        return learning_journal.ReplayBenchmarkRecord(**fields)  # type: ignore[arg-type]

    def _write_and_read(self, records: list[object]) -> list[dict]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for record in records:
                self.assertIsNone(
                    learning_journal.append_journal_record(record, root_dir=root)
                )
            return _read_jsonl(learning_journal.journal_path(root))

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
        `ground_truth`, each carrying the identity of the decision it grades.

        The field is not called `signal`: CONTEXT.md spends "signal family" on
        the journal's five *record* families, and a subdivision inside one of
        them cannot wear the same word at a finer granularity without turning
        the glossary into a trap. See `test_signal_names_exactly_one_granularity`.
        """
        cases = [
            ("tests", "fail"),
            ("review", "approved"),
            ("plan", "rejected"),
            ("stalemate_resolution", "human"),
        ]
        for ground_truth, verdict in cases:
            with self.subTest(ground_truth=ground_truth):
                record = self._write_and_read(
                    [
                        learning_journal.OutcomeRecord(
                            task=learning_journal.TaskLabel.for_task("graded-decision-1"),
                            ground_truth=ground_truth,  # type: ignore[arg-type]
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
                        "ground_truth",
                        "verdict",
                        "timestamp",
                    },
                )
                self.assertEqual(record["kind"], "outcome")
                self.assertEqual(record["task_id"], "graded-decision-1")
                self.assertEqual(record["ground_truth"], ground_truth)
                self.assertEqual(record["verdict"], verdict)

    def test_a_verdict_from_another_ground_truths_vocabulary_is_rejected(self) -> None:
        """A flat verdict vocabulary would let a test run "pick the Planner"."""
        for ground_truth, verdict in (
            ("tests", "planner"),
            ("review", "pass"),
            ("plan", "approved"),
            ("stalemate_resolution", "fail"),
        ):
            with self.subTest(ground_truth=ground_truth, verdict=verdict), self.assertRaises(
                ValueError
            ):
                learning_journal.OutcomeRecord(
                    task=learning_journal.TaskLabel.for_task("task-1"),
                    ground_truth=ground_truth,  # type: ignore[arg-type]
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
        self.assertEqual(record["occasion"], "plan-review")
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

    def test_replay_benchmark_record_lands_with_its_schema(self) -> None:
        """A successful trial carries a score; a failed one carries none at
        all — the same "absence is the honest claim" rule every optional
        field in this module follows, applied to a field a trial can
        genuinely have nothing to say about."""
        succeeded = self._write_and_read([self._replay_benchmark_record()])[0]

        self.assertEqual(
            set(succeeded),
            {"kind", "task_set", "success", "score", "timestamp"},
        )
        self.assertEqual(succeeded["kind"], "replay_benchmark")
        self.assertEqual(succeeded["task_set"], "bench-v1")
        self.assertTrue(succeeded["success"])
        self.assertEqual(succeeded["score"], 0.82)
        self.assertNotIn(
            "task_id",
            succeeded,
            "the replay benchmark grades the evaluator's fixed task set, "
            "never a development task: there is no TaskIdentity to carry",
        )

        failed = self._write_and_read(
            [self._replay_benchmark_record(success=False, score=None)]
        )[0]
        self.assertEqual(set(failed), {"kind", "task_set", "success", "timestamp"})
        self.assertFalse(failed["success"])
        self.assertNotIn(
            "score",
            failed,
            "a failed trial has no score to report — not zero, absent",
        )

    def test_a_replay_benchmark_records_success_and_score_must_agree(self) -> None:
        with self.assertRaises(ValueError):
            self._replay_benchmark_record(success=True, score=None)
        with self.assertRaises(ValueError):
            self._replay_benchmark_record(success=False, score=0.5)

    def test_all_five_families_are_distinguishable_by_kind(self) -> None:
        records = self._write_and_read(
            [
                self._worker_execution_record(),
                learning_journal.OutcomeRecord(
                    task=learning_journal.TaskLabel.for_task("task-1"),
                    ground_truth="tests",
                    verdict="pass",
                ),
                self._dialogue_quality_record(),
                self._compliance_record(),
                self._replay_benchmark_record(),
            ]
        )

        self.assertEqual(
            [record["kind"] for record in records],
            [
                "worker_execution",
                "outcome",
                "dialogue_quality",
                "compliance",
                "replay_benchmark",
            ],
        )

    # --- RunIdentity: which attempt, as distinct from which task ---

    def test_every_family_can_carry_a_run_id_and_none_has_to(self) -> None:
        """`run_id` is optional on all five families and never invented. A
        writer that has an honest run identity supplies it; one that does not
        omits it, which is a different and weaker claim than any value."""
        named = self._write_and_read(
            [
                self._worker_execution_record(run_id="run-a1b2c3d4"),
                self._outcome_record(run_id="run-a1b2c3d4"),
                self._dialogue_quality_record(run_id="run-a1b2c3d4"),
                self._compliance_record(run_id="run-a1b2c3d4"),
                self._replay_benchmark_record(run_id="run-a1b2c3d4"),
            ]
        )
        anonymous = self._write_and_read(
            [
                self._worker_execution_record(),
                self._outcome_record(),
                self._dialogue_quality_record(),
                self._compliance_record(),
                self._replay_benchmark_record(),
            ]
        )

        for record in named:
            with self.subTest(kind=record["kind"]):
                self.assertEqual(record["run_id"], "run-a1b2c3d4")
        for record in anonymous:
            with self.subTest(kind=record["kind"]):
                self.assertNotIn(
                    "run_id",
                    record,
                    'absence, never "run_id": null — a null is still a '
                    "per-record assertion about a run",
                )

    def test_records_naming_no_run_are_uncountable_never_one_shared_run(self) -> None:
        """The consumer-side half of that contract, and the reason the field
        is `None`-able rather than defaulted: two records that name no run are
        two records about an unknown number of runs, not evidence of one. A
        reducer that folded them together would report a task reworked four
        times as reworked once."""
        records = self._write_and_read(
            [
                self._worker_execution_record(
                    task=learning_journal.TaskLabel.for_task("task-anonymous")
                ),
                self._worker_execution_record(
                    task=learning_journal.TaskLabel.for_task("task-anonymous")
                ),
                self._worker_execution_record(
                    task=learning_journal.TaskLabel.for_task("task-named"),
                    run_id="run-first",
                ),
                self._worker_execution_record(
                    task=learning_journal.TaskLabel.for_task("task-named"),
                    run_id="run-second",
                ),
            ]
        )

        self.assertEqual(_countable_runs(records, "task-anonymous"), set())
        self.assertEqual(
            _countable_runs(records, "task-named"), {"run-first", "run-second"}
        )
        self.assertEqual(
            len(_countable_runs(records, "task-named")) - 1,
            1,
            "rework on a task is its distinct run identities minus one",
        )

    def test_a_run_id_faces_the_shape_gate_and_no_marker_gate(self) -> None:
        """Same contract as `task_id` and `session_id`, for the same reason:
        an execution this module is told about was named elsewhere, and
        refusing the name here un-names nothing."""
        for value in ("run one", "runs/17", "", 17, ["run-1"]):
            with self.subTest(run_id=value), self.assertRaises(ValueError):
                self._worker_execution_record(run_id=value)

        self.assertEqual(
            self._write_and_read(
                [self._worker_execution_record(run_id="secret-rotation-run-1")]
            )[0]["run_id"],
            "secret-rotation-run-1",
        )

    def test_signal_names_exactly_one_granularity(self) -> None:
        """One word, one granularity — the whole of item 9.

        CONTEXT.md is the glossary this codebase is driven by, and it spends
        "signal family" on the journal's five *record* families. A field named
        `signal` discriminating the four ground truths *inside* one of those
        families would make the same word mean a family in the glossary and a
        subdivision of one family in the code — a reader's trap, and the kind
        a glossary-driven codebase cannot afford.
        """
        for build in (
            self._worker_execution_record,
            self._outcome_record,
            self._dialogue_quality_record,
            self._compliance_record,
            self._replay_benchmark_record,
        ):
            record = build()
            with self.subTest(record=type(record).__name__):
                self.assertNotIn(
                    "signal",
                    {f.name for f in dataclasses.fields(record)},  # type: ignore[arg-type]
                )
                self.assertNotIn("signal", self._write_and_read([record])[0])

        self.assertTrue(hasattr(learning_journal, "GroundTruth"))
        self.assertFalse(
            hasattr(learning_journal, "OutcomeSignal"),
            "the four truths inside one record family must not wear the word "
            "CONTEXT.md spends on the five families themselves",
        )
        self.assertEqual(
            set(learning_journal.OUTCOME_VERDICTS),
            {"tests", "review", "plan", "stalemate_resolution"},
        )

        entry = (REPO_ROOT / "CONTEXT.md").read_text(encoding="utf-8")
        entry = entry.split("### LearningJournal", 1)[1].split("\n### ", 1)[0]
        self.assertIn("five signal families", entry)
        self.assertIn(
            "nothing finer",
            entry,
            "the glossary must pin the granularity, not merely use the word",
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

            invoker = _RecordingInvoker(["Plan.", _approve("Plan.", "Good.")])
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
            # 3, not 1: the consultation itself already appended one
            # `dialogue_quality` record (spec 0004 ticket 24) and one
            # `outcome` record grading its own plan (spec 0004 ticket 25 —
            # this run reached consensus) before this test appends its own
            # `worker_execution` record by hand.
            self.assertEqual(len(_read_jsonl(learning_journal.journal_path(root))), 3)

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
        repo_journal = learning_journal.journal_path(REPO_ROOT)
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
            learning_journal.journal_path(root).parent.write_text("not a directory")

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
            invoker = _RecordingInvoker(["Plan.", _approve("Plan.", "Good.")])
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
                    ground_truth="tests",
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
        # `dialogue_quality` joins the same task_id too (spec 0004 ticket 24):
        # the consultation itself already wrote one at its `_result` choke
        # point, so the three-way join is worker_execution + outcome +
        # dialogue_quality, not just the first two.
        self.assertEqual(
            {record["kind"] for record in graded},
            {"worker_execution", "outcome", "dialogue_quality"},
        )
        # Two `outcome` records now share this task_id: the consultation's
        # own `plan` verdict (spec 0004 ticket 25 — this run reached
        # consensus) and the `tests` verdict this test appends by hand.
        # Disambiguate by `ground_truth` rather than taking the first
        # `outcome` record, or this assertion would silently start reading
        # the wrong one the moment a producer's write order changes.
        outcome_records = [r for r in graded if r["kind"] == "outcome"]
        self.assertEqual(
            {r["ground_truth"] for r in outcome_records}, {"plan", "tests"}
        )
        self.assertEqual(
            next(r for r in outcome_records if r["ground_truth"] == "tests")[
                "verdict"
            ],
            "pass",
        )
        self.assertEqual(
            next(r for r in outcome_records if r["ground_truth"] == "plan")[
                "verdict"
            ],
            "accepted",
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

        The gate covers exactly the identifiers a caller *composes* for a
        record — `model_id`, `model_family`, and ticket 26's `task_set`, all
        built out of the caller's own vocabulary. The identifiers a caller
        merely *carries in* (`task_id`,
        `session_id`, `run_id`) are deliberately not among them: each names a
        thing that already exists and was already named elsewhere, so refusing
        one here cannot un-name it and can only drop the record. See
        `_validate_carried_identifier`,
        `test_every_task_id_the_council_accepts_is_journal_writable`, and
        `test_a_session_id_touching_security_vocabulary_is_recorded_not_refused`.
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
                    lambda v=value: self._replay_benchmark_record(task_set=v),
                ):
                    with self.assertRaises(ValueError) as caught:
                        build()
                    self.assertNotIn(
                        secret_material,
                        str(caught.exception),
                        "the rejection must not repeat the secret it rejected",
                    )

    def test_the_marker_gate_runs_on_composed_descriptors_and_nothing_else(self) -> None:
        """The line the gate is drawn on, stated as a property of the two
        validators rather than of a field list.

        `model_id`, `model_family` and `task_set` are composed here, so they
        face `_validate_identifier` (shape *and* markers). `task_id`,
        `session_id` and `run_id` are carried in, so they face
        `_validate_carried_identifier` (shape only). Stating it this way is
        the point: `session_id` sat behind the marker gate unnoticed for a
        whole ticket because the rule was remembered as a list of field names
        instead of as a rule about where a string came from — and `task_set`
        arrived with ticket 26 on the composed side of that rule, where the
        field list would again have missed it.
        """
        marked = "sk-live-9f3c1d7b"

        for descriptor in ("model_id", "model_family"):
            with self.subTest(composed=descriptor), self.assertRaises(ValueError):
                self._worker_execution_record(**{descriptor: marked})
        with self.subTest(composed="task_set"), self.assertRaises(ValueError):
            self._replay_benchmark_record(task_set=marked)

        self.assertEqual(learning_journal.TaskLabel.for_task(marked).task_id, marked)
        carried = (
            ("compliance.session_id", "session_id",
             lambda: self._compliance_record(session_id=marked)),
            ("compliance.run_id", "run_id",
             lambda: self._compliance_record(run_id=marked)),
            ("worker_execution.run_id", "run_id",
             lambda: self._worker_execution_record(run_id=marked)),
            ("outcome.run_id", "run_id",
             lambda: self._outcome_record(run_id=marked)),
            ("dialogue_quality.run_id", "run_id",
             lambda: self._dialogue_quality_record(run_id=marked)),
            ("replay_benchmark.run_id", "run_id",
             lambda: self._replay_benchmark_record(run_id=marked)),
        )
        for label, field_name, build in carried:
            with self.subTest(carried=label):
                written = self._write_and_read([build()])[0]
                self.assertEqual(written[field_name], marked)

    def test_a_session_id_touching_security_vocabulary_is_recorded_not_refused(
        self,
    ) -> None:
        """The regression this half of the fix exists for, in the shape an
        operator meets it.

        A conversation named `secret-rotation` used to have its audit verdict
        refused by the marker gate and dropped — and nothing re-audits a
        session, so that verdict was gone permanently, for exactly the
        sessions whose protocol discipline is most worth learning from. The id
        is a directory name the audit was handed, not prose this module gets
        to adjudicate.
        """
        recorded = self._write_and_read(
            [
                self._compliance_record(session_id=name)
                for name in (
                    "secret-rotation",
                    "api_key-migration",
                    "password-reset-flow",
                    "AGY_CALIBRATION_SECRET-rotation",
                )
            ]
        )

        self.assertEqual(
            [record["session_id"] for record in recorded],
            [
                "secret-rotation",
                "api_key-migration",
                "password-reset-flow",
                "AGY_CALIBRATION_SECRET-rotation",
            ],
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

    def test_cross_spec_vocabularies_agree(self) -> None:
        """Drift guard for the incident this fix addresses: `DialogueOccasion`
        (spec 0004, schema-only) and `advisory_consultation.Occasion` (spec
        0003, shipped) are two separately-declared `Literal` aliases meant to
        describe one vocabulary, but nothing in the type system keeps them in
        sync. They drifted — this alias spelled three of its four values with
        underscores (`plan_review`, `code_review`, `post_mortem`) while the
        shipped `Occasion` uses hyphens — and nothing caught it, because no
        test in this file ever constructed a `DialogueQualityRecord` from a
        real `Occasion` value; `_dialogue_quality_record` always hand-supplied
        its own occasion string in isolation. Left unfixed, spec 0004's future
        writer doing `DialogueQualityRecord(occasion=telemetry_record.occasion,
        ...)` would raise `ValueError` inside `_validate_choice` the first
        time it ran against real production data. Same risk, smaller
        surface, for `DialogueTopology` against `RosterTopology`: identical
        today (`Literal["pair", "panel"]` both sides) but just as unpinned."""
        self.assertEqual(
            set(get_args(advisory_consultation.Occasion)),
            set(get_args(learning_journal.DialogueOccasion)),
        )
        self.assertEqual(
            set(get_args(advisory_consultation.RosterTopology)),
            set(get_args(learning_journal.DialogueTopology)),
        )

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
            self._replay_benchmark_record,
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
            ("replay_benchmark", self._replay_benchmark_record),
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
            (self._replay_benchmark_record, "success"),
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
        for value in ("a great score", None, True, float("nan"), float("inf")):
            with self.subTest(field="score", value=value), self.assertRaises(
                ValueError
            ):
                self._replay_benchmark_record(score=value)

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
        """The paths under one  block scalar in the workflow env."""
        lines = workflow.splitlines()
        start = next(
            index
            for index, line in enumerate(lines)
            if line.strip().startswith(f"{name}:")
        )
        listed = []
        for line in lines[start + 1 :]:
            stripped = line.strip()
            if not stripped or stripped.startswith(("-", "#", "steps:", "jobs:")) or ":" in stripped:
                break
            listed.append(stripped)
        return listed

    def test_ci_lints_and_type_checks_one_single_sourced_module_list(self) -> None:
        """Ruff and mypy share one module list and every entry exists."""
        workflow = (REPO_ROOT / ".github" / "workflows" / "test.yml").read_text(
            encoding="utf-8"
        )
        listed = self._workflow_list(workflow, "PYTHON_MODULES")

        self.assertIn("skills/worker-routing/learning_journal.py", listed)
        self.assertEqual(sorted(listed), sorted(set(listed)))
        for module in listed:
            with self.subTest(module=module):
                self.assertTrue((REPO_ROOT / module).is_file())

        self.assertIn("ruff check $PYTHON_MODULES", workflow)
        self.assertIn("mypy --config-file pyproject.toml $TARGETS", workflow)
        self.assertIn(
            "TARGETS=$(echo \"$PYTHON_MODULES\" | sed 's|skills/worker-routing/|worker_routing/|g')",
            workflow,
        )

    def test_ci_checks_every_python_file_in_the_skill_directory(self) -> None:
        """The other direction, and the one that actually goes missing.

        The test above asserts every *listed* path exists; nothing asserted
        every existing path is listed, so a module omitted here is simply
        never linted or type-checked — silently, since the steps still pass.
        Removing `acceptance_gate.py` from `PYTHON_MODULES` left the whole
        suite green.

        This is the same omission `test_every_production_module_in_the_skill_
        directory_is_managed` closes for `install.sh`, and it went unnoticed
        for the same reason: adding a module means adding it to four lists
        (`MANAGED_FILES`, `INSTALLED_FILES`, `PYTHON_MODULES`, and — for a
        test file — `PYTHON_TESTS`), and until now only three of the four
        were guarded in this direction. Test files are included: CI lints and
        type-checks them too, and `PYTHON_MODULES` already names every one.
        """
        workflow = (REPO_ROOT / ".github" / "workflows" / "test.yml").read_text(
            encoding="utf-8"
        )
        listed = set(self._workflow_list(workflow, "PYTHON_MODULES"))
        present = {
            f"skills/worker-routing/{path.name}" for path in SKILL_DIR.glob("*.py")
        }
        self.assertTrue(present, "no Python files found to check")

        for module in sorted(present):
            with self.subTest(module=module):
                self.assertIn(
                    module,
                    listed,
                    f"{module} exists but CI's PYTHON_MODULES does not name it — "
                    f"ruff and mypy never see it, and both steps still pass",
                )

    def test_ci_runs_every_test_file_it_checks(self) -> None:
        """Being linted and type-checked is not being run.

        `test_production_invoker.py` was in `PYTHON_MODULES` from the day it
        was written, so ruff and mypy both saw it and CI stayed green — while
        not one of its tests ever executed, because the only test-running step
        named `test_routing.py` directly. This asserts the property that gap
        violated: every `test_*.py` the workflow checks is also a file the
        workflow executes, and the executing step reads its list from the
        environment rather than naming a file inline — with exactly one named,
        documented exception (`_CHECKED_BUT_NOT_EXECUTED_BY_DESIGN` above),
        for a file CI cannot run rather than merely forgot to.
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
            checked_tests - self._CHECKED_BUT_NOT_EXECUTED_BY_DESIGN,
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


if "production_invoker" in sys.modules:
    production_invoker = sys.modules["production_invoker"]
else:
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

    TELEMETRY_RELATIVE_PATH = Path(".ralph") / "routing_telemetry.jsonl"

    def test_injecting_a_plain_fake_writes_nothing_to_the_worker_execution_journal(
        self,
    ) -> None:
        """The consultation's worker-execution seam is unchanged: a
        caller-supplied fake `invoke_worker` bypasses that instrumentation
        entirely, exactly as it did before this ticket. The dialogue-quality
        writer (spec 0004 ticket 24) and the plan-outcome writer (ticket 25)
        are both separate, always-on writes at the `_result` choke point
        that do not depend on which `invoke_worker` the caller supplied, so
        the journal file itself now exists — holding one `dialogue_quality`
        record, one `outcome` record (this run reached consensus), and zero
        `worker_execution` records."""
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
            records = _read_jsonl(learning_journal.journal_path(root))

        self.assertTrue(result.consensus_reached)
        self.assertEqual(
            [record["kind"] for record in records], ["dialogue_quality", "outcome"]
        )

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
                subprocess.CompletedProcess(
                    [], 0, _approve("Planner's proposed plan."), ""
                ),
            ]
        )
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            journaled_invoker = production_invoker.make_journaled_invoke_worker(
                "task-correlated-1",
                root_dir=root,
                task_type="feature",
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
            journal_records = _read_jsonl(learning_journal.journal_path(root))
            telemetry_records = _read_jsonl(root / self.TELEMETRY_RELATIVE_PATH)

        self.assertTrue(result.consensus_reached)

        # 4: two `worker_execution` records, one `dialogue_quality` record
        # (ticket 24), and one `outcome` record grading this consultation's
        # own plan — accepted, since this run reached consensus (ticket 25).
        self.assertEqual(len(journal_records), 4)
        worker_records = [r for r in journal_records if r["kind"] == "worker_execution"]
        self.assertEqual(len(worker_records), 2)
        for record in worker_records:
            self.assertEqual(record["task_id"], "task-correlated-1")
            self.assertTrue(record["success"])
        self.assertEqual(worker_records[0]["model_id"], "claude-opus-5")
        self.assertEqual(worker_records[0]["model_family"], "claude")
        self.assertEqual(worker_records[1]["model_id"], "gpt-5.6-sol")
        self.assertEqual(worker_records[1]["model_family"], "codex")

        dialogue_records = [r for r in journal_records if r["kind"] == "dialogue_quality"]
        self.assertEqual(len(dialogue_records), 1)
        self.assertEqual(dialogue_records[0]["task_id"], "task-correlated-1")

        outcome_records = [r for r in journal_records if r["kind"] == "outcome"]
        self.assertEqual(len(outcome_records), 1)
        self.assertEqual(outcome_records[0]["task_id"], "task-correlated-1")
        self.assertEqual(outcome_records[0]["ground_truth"], "plan")
        self.assertEqual(outcome_records[0]["verdict"], "accepted")

        self.assertEqual(len(telemetry_records), 1)
        self.assertEqual(telemetry_records[0]["task_id"], "task-correlated-1")

    def test_two_runs_of_one_task_stay_distinct_while_the_task_join_still_works(
        self,
    ) -> None:
        """The measurement `task_id` alone cannot make.

        `task_id` is deliberately stable across repeats — absent a
        caller-supplied id it is a digest of the task text — so two
        consultations of one task pile their invocations into a single
        identity: cost sums as though one run happened, and the second run's
        rework reads as the first's. One factory is built per consultation, so
        one factory's records are one run's records; two factories over the
        same task must therefore carry one `task_id` and two `run_id`s, with
        the TaskIdentity join across the two streams untouched.
        """
        def _runner() -> mock.Mock:
            return mock.Mock(
                side_effect=[
                    subprocess.CompletedProcess([], 0, "Planner's proposed plan.", ""),
                    subprocess.CompletedProcess(
                        [], 0, _approve("Planner's proposed plan."), ""
                    ),
                ]
            )

        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            for _attempt in range(2):
                advisory_consultation.run_advisory_consultation_debate(
                    "Plan the auth rewrite",
                    production_invoker.make_journaled_invoke_worker(
                        "task-reworked-1", root_dir=root, runner=_runner()
                    ),
                    root_dir=root,
                    task_id="task-reworked-1",
                )
            journal_records = _read_jsonl(learning_journal.journal_path(root))
            telemetry_records = _read_jsonl(root / self.TELEMETRY_RELATIVE_PATH)

        # 8: each of the two runs writes 2 `worker_execution` records, 1
        # `dialogue_quality` record (ticket 24), and 1 `outcome` record
        # grading its own plan (ticket 25) — 4 per run, 2 runs.
        self.assertEqual(len(journal_records), 8)
        self.assertEqual(
            {record["task_id"] for record in journal_records}, {"task-reworked-1"}
        )
        runs = _countable_runs(journal_records, "task-reworked-1")
        self.assertEqual(len(runs), 2, "two consultations of one task are two runs")
        self.assertEqual(
            len(runs) - 1, 1, "which is one rework — the number task_id alone hides"
        )
        worker_records = [r for r in journal_records if r["kind"] == "worker_execution"]
        self.assertEqual(
            [
                len([r for r in worker_records if r["run_id"] == run])
                for run in sorted(runs)
            ],
            [2, 2],
            "each run's own invocations stay attributable to it, so cost per "
            "run is answerable and not only cost per task",
        )
        self.assertEqual(
            {record["task_id"] for record in telemetry_records},
            {"task-reworked-1"},
            "the cross-stream join is on TaskIdentity and must be unaffected",
        )

    def test_every_invocation_of_one_run_carries_that_runs_identity(self) -> None:
        """The other half: a run identity is per *factory*, not per call, or
        the two invocations of one consultation would read as two runs."""
        runner = mock.Mock(
            return_value=subprocess.CompletedProcess([], 0, "worker output", "")
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            journaled = production_invoker.make_journaled_invoke_worker(
                "task-one-run", root_dir=root, runner=runner
            )
            journaled("claude-opus-5", "high", "Plan")
            journaled("gpt-5.6-sol", "medium", "Review")
            records = _read_jsonl(learning_journal.journal_path(root))

        self.assertEqual(len(records), 2)
        self.assertEqual(len(_countable_runs(records, "task-one-run")), 1)

    def test_a_caller_with_a_run_identity_supplies_it_rather_than_getting_a_fresh_one(
        self,
    ) -> None:
        """The generated identity is a default, not a policy: an orchestrator
        that already correlates a dialogue, its invocations and its outcome
        under one run passes that id in and the record carries it verbatim."""
        runner = mock.Mock(
            return_value=subprocess.CompletedProcess([], 0, "worker output", "")
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            production_invoker.make_journaled_invoke_worker(
                "task-supplied-run", root_dir=root, run_id="run-supplied-1", runner=runner
            )("claude-opus-5", "high", "Plan")
            record = _read_jsonl(learning_journal.journal_path(root))[0]

        self.assertEqual(record["run_id"], "run-supplied-1")

    def test_an_unjournalable_task_id_is_refused_at_wiring_time(self) -> None:
        """The factory takes an id, so the id is validated where the label is
        built — once, before any worker runs — rather than once per
        invocation afterwards. `advisory_consultation` already wraps this call
        in the try that degrades to "journaling disabled for this run", which
        is why raising here costs the run its instrumentation and nothing
        else (see `ConsultationSurvivesJournalWiringFailureTests`)."""
        runner = mock.Mock(
            return_value=subprocess.CompletedProcess([], 0, "worker output", "")
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            with self.assertRaises(ValueError):
                production_invoker.make_journaled_invoke_worker(
                    "not a valid task id", root_dir=root, runner=runner
                )

            runner.assert_not_called()
            self.assertFalse(learning_journal.journal_path(root).exists())

    def test_the_coarse_task_type_tag_is_reachable_from_the_factory(self) -> None:
        """`task_type` rides along with the id so the tag stays settable from
        production rather than becoming a field only a test can reach — and
        stays absent when no tag is offered."""
        runner = mock.Mock(
            return_value=subprocess.CompletedProcess([], 0, "worker output", "")
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            production_invoker.make_journaled_invoke_worker(
                "task-tagged", root_dir=root, task_type="bugfix", runner=runner
            )("claude-opus-5", "high", "Plan")
            production_invoker.make_journaled_invoke_worker(
                "task-untagged", root_dir=root, runner=runner
            )("claude-opus-5", "high", "Plan")
            tagged, untagged = _read_jsonl(learning_journal.journal_path(root))

        self.assertEqual(tagged["task_type"], "bugfix")
        self.assertNotIn("task_type", untagged)

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
            side_effect=[
                "Planner's proposed plan.",
                _approve("Planner's proposed plan."),
            ],
        ):
            root = Path(tmp)
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite",
                root_dir=root,
                planner_model="Claude Opus 5 (Thinking)",
                critic_model="Codex 5.6 Sol",
                task_id="task-default-path-1",
            )
            journal_records = _read_jsonl(learning_journal.journal_path(root))

        self.assertTrue(result.consensus_reached)
        # 4: 2 `worker_execution` records, 1 `dialogue_quality` record
        # (ticket 24), 1 `outcome` record grading this consultation's own
        # plan (ticket 25).
        self.assertEqual(len(journal_records), 4)
        worker_records = [r for r in journal_records if r["kind"] == "worker_execution"]
        self.assertEqual(len(worker_records), 2)
        for record in worker_records:
            self.assertEqual(record["task_id"], "task-default-path-1")
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
    (ground_truth, verdict) pairing; these tests exercise the public surface a
    caller far from that schema actually calls — `learning_outcomes`'s four
    `record_*` functions — and the one path (the stalemate resolution) that
    is wired into the real `advisory_consultation` flow rather than tested
    against a hand-built stand-in.
    """

    def test_record_test_result_writes_pass_and_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            error_pass = learning_outcomes.record_test_result(
                "task-tests-1", passed=True, root_dir=root
            )
            error_fail = learning_outcomes.record_test_result(
                "task-tests-2", passed=False, root_dir=root
            )
            records = _read_jsonl(learning_journal.journal_path(root))

        self.assertIsNone(error_pass)
        self.assertIsNone(error_fail)
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["kind"], "outcome")
        self.assertEqual(records[0]["task_id"], "task-tests-1")
        self.assertEqual(records[0]["ground_truth"], "tests")
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
            records = _read_jsonl(learning_journal.journal_path(root))

        self.assertEqual(records[0]["ground_truth"], "review")
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
            records = _read_jsonl(learning_journal.journal_path(root))

        self.assertEqual(records[0]["ground_truth"], "plan")
        self.assertEqual(records[0]["verdict"], "accepted")
        self.assertEqual(records[1]["verdict"], "rejected")

    def _run_to_stalemate(self, root: Path):
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
            records = _read_jsonl(learning_journal.journal_path(root))

        self.assertIsNone(error)
        # 2: `_run_to_stalemate` already appended one `dialogue_quality`
        # record (ticket 24). It writes no `plan` outcome record — a
        # stalemate is exactly the case a human resolves afterward (fix pass
        # 2 of ticket 25), and this call's own `stalemate_resolution` record
        # is that resolution — so the only records here are that
        # `dialogue_quality` record and the `stalemate_resolution` record
        # this call appends.
        self.assertEqual(len(records), 2)
        outcome_record = next(
            r
            for r in records
            if r["kind"] == "outcome" and r["ground_truth"] == "stalemate_resolution"
        )
        self.assertEqual(outcome_record["task_id"], "task-stalemate-1")
        self.assertEqual(outcome_record["verdict"], "critic")

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
            records = _read_jsonl(learning_journal.journal_path(root))

        # `_run_to_stalemate` already appended one `dialogue_quality` record
        # (spec 0004 ticket 24) before the loop above wrote its three
        # `stalemate_resolution` records — it writes no `plan` outcome
        # record of its own (fix pass 2 of ticket 25: a stalemate is silent,
        # left for a human to resolve) — but filter to the ground truth this
        # test is actually about anyway, not just the `outcome` kind, so a
        # future record kind sharing the `outcome` kind cannot throw off the
        # ordered comparison below.
        outcome_records = [
            r
            for r in records
            if r["kind"] == "outcome" and r["ground_truth"] == "stalemate_resolution"
        ]
        self.assertEqual(
            [record["verdict"] for record in outcome_records],
            ["planner", "critic", "human"],
        )

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

            # The journal file itself already holds one record —
            # `_run_to_stalemate` wrote one `dialogue_quality` record
            # (spec 0004 ticket 24) and no `plan` outcome record of its own
            # (fix pass 2 of ticket 25: a stalemate is silent, left for a
            # human to resolve) — before the rejected call above ever ran.
            # What must stay true is that the rejection left no
            # *additional*, orphan `outcome` record behind.
            records = _read_jsonl(learning_journal.journal_path(root))
            self.assertEqual([r["kind"] for r in records], ["dialogue_quality"])

    def test_missing_task_id_is_refused_rather_than_writing_an_orphan_record(self) -> None:
        """"Unknown task" handling: this module never fabricates a `task_id`
        for an outcome. An empty one is refused loudly, and no orphan record
        reaches the journal."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            with self.assertRaises(ValueError):
                learning_outcomes.record_test_result("", passed=True, root_dir=root)

            self.assertFalse(learning_journal.journal_path(root).exists())

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

            self.assertFalse(learning_journal.journal_path(root).exists())

    def test_a_run_id_is_passed_through_when_given_and_never_invented(self) -> None:
        """An outcome that names a run grades that run; one that does not
        grades the task as a whole. Both are honest answers a caller may have,
        and every one of these functions can express either.

        What is not honest is a fabricated run identity — it would attach a
        real verdict to an arbitrary attempt, which is strictly worse than
        attaching it to the task — so the default is omission, not a
        generated id.
        """
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            result = self._run_to_stalemate(root)
            assert result.stalemate is not None

            learning_outcomes.record_test_result(
                "task-run-1", passed=True, root_dir=root, run_id="run-graded-1"
            )
            learning_outcomes.record_review_verdict(
                "task-run-1", approved=True, root_dir=root, run_id="run-graded-1"
            )
            learning_outcomes.record_plan_outcome(
                "task-run-1", accepted=True, root_dir=root, run_id="run-graded-1"
            )
            learning_outcomes.record_stalemate_resolution(
                "task-run-1",
                result.stalemate,
                result.stalemate.options[0],
                root_dir=root,
                run_id="run-graded-1",
            )
            learning_outcomes.record_test_result(
                "task-run-2", passed=True, root_dir=root
            )
            outcomes = [
                record
                for record in _read_jsonl(learning_journal.journal_path(root))
                if record["kind"] == "outcome"
            ]

        named = [record for record in outcomes if record["task_id"] == "task-run-1"]
        self.assertEqual(len(named), 4)
        self.assertEqual({record["run_id"] for record in named}, {"run-graded-1"})
        self.assertEqual(
            {record["ground_truth"] for record in named},
            {"tests", "review", "plan", "stalemate_resolution"},
            "every ground truth can name the run it graded, not just some",
        )

        anonymous = next(
            record for record in outcomes if record["task_id"] == "task-run-2"
        )
        self.assertNotIn(
            "run_id",
            anonymous,
            "a caller that named no run must not have one invented for it",
        )

    def test_write_failure_is_reported_to_the_caller_and_never_raised(self) -> None:
        """Matches ticket 13's contract: a broken `.ralph` degrades the
        learning loop, it never breaks the caller recording the outcome."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            learning_journal.journal_path(root).parent.write_text("not a directory")

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
                "task-join-1",
                root_dir=root,
                task_type="feature",
                runner=mock.Mock(
                    return_value=subprocess.CompletedProcess([], 0, "worker output", "")
                ),
            )
            journaled_invoker("claude-sonnet-5", "high", "do the thing")

            learning_outcomes.record_test_result("task-join-1", passed=True, root_dir=root)

            records = _read_jsonl(learning_journal.journal_path(root))

        kinds_by_task = {record["kind"]: record for record in records}
        self.assertEqual(kinds_by_task["worker_execution"]["task_id"], "task-join-1")
        self.assertEqual(kinds_by_task["outcome"]["task_id"], "task-join-1")
        self.assertEqual(kinds_by_task["outcome"]["ground_truth"], "tests")
        self.assertEqual(kinds_by_task["outcome"]["verdict"], "pass")


# Ticket 15 — the post-session audit verdict, persisted instead of printed and
# lost. Appended here, at the end of the file, per this ticket's instructions:
# this file is edited concurrently on other branches, so an insertion
# anywhere but the end is a merge conflict for everyone.
#
# No return annotation: `routing_check` is loaded by path, so mypy sees a bare
# `ModuleType` and cannot resolve `routing_check.AuditReport` as a name. Every
# other test in this file that touches these modules relies on the same
# inference.
#
# Module-level rather than a `PersistComplianceRecordTests` method because
# `JournalUnificationTests` needs the same report and used to hand-copy all ten
# fields — a second copy that drifts the moment `AuditReport` gains a field,
# and one whose numbers no longer mean anything in particular next to the
# first. One clean report, overridden per test.
def _audit_report(**overrides: object):
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

    `_audit_report` builds the real frozen dataclass, not a dict shaped like one:
    the whole point of the signature this function now takes is that a caller
    cannot hand it a mapping with a mistyped key and have it look correct
    until runtime.
    """

    def test_clean_metrics_persist_a_clean_verdict_not_nothing(self) -> None:
        """User story 4 / the trendline-has-no-silent-gaps criterion: a
        session with zero violations still writes a record, with an empty
        `issue_codes` tuple rather than no record at all."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            error = routing_check._persist_compliance_record(
                _audit_report(), session_id="sess-clean", root_dir=root
            )

            self.assertIsNone(error)
            records = _read_jsonl(learning_journal.journal_path(root))
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
        report = _audit_report(
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
            record = _read_jsonl(learning_journal.journal_path(root))[0]
            self.assertEqual(record["violation_count"], 2)
            self.assertEqual(sorted(record["issue_codes"]), ["DEC-01", "LOG-01"])
            for leaked in ("apply_unreviewed_patch", "app.py"):
                self.assertNotIn(leaked, json.dumps(record))

    def test_drift_on_a_non_violating_step_still_reaches_the_record(self) -> None:
        """The trendline's whole subject is discipline drift, and a DEC-01 on
        a step that did not also trip a violation is drift. Sourcing the codes
        from `violation_details` dropped exactly those, so a session could
        carry declaration drift and persist `issue_codes=()`."""
        report = _audit_report(
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
            record = _read_jsonl(learning_journal.journal_path(root))[0]
            self.assertEqual(record["violation_count"], 0)
            self.assertEqual(record["issue_codes"], ["DEC-02"])
            self.assertEqual(record["declaration_drift_count"], 1)

    def test_warning_codes_reach_the_record(self) -> None:
        """A `--strict` run that fails on warnings alone used to persist
        `violation_count=0, issue_codes=()` — a record no reader could tell
        apart from a genuinely clean session."""
        report = _audit_report(warning_codes=("WARN-01",), exit_code=1)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            error = routing_check._persist_compliance_record(
                report, session_id="sess-warned", root_dir=root
            )

            self.assertIsNone(error)
            record = _read_jsonl(learning_journal.journal_path(root))[0]
            self.assertEqual(record["violation_count"], 0)
            self.assertEqual(record["issue_codes"], ["WARN-01"])

    def test_no_session_id_persists_nothing(self) -> None:
        """The documented handling of 'no id available': an explicit skip,
        never a fabricated placeholder id."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            error = routing_check._persist_compliance_record(
                _audit_report(), session_id=None, root_dir=root
            )

            self.assertIsNone(error)
            self.assertFalse(learning_journal.journal_path(root).exists())

    def test_a_session_id_the_journal_cannot_hold_is_digested_never_dropped(self) -> None:
        """A conversation id the identifier pattern cannot hold verbatim —
        `fix login 500`, the ordinary shape of a conversation name — is
        recorded under a digest of itself rather than refused.

        Dropping was the worst available option: nothing re-audits a session,
        so a refused record is a verdict lost permanently, and the trendline
        loses it with no gap where it had been. The digest is one-way, so a
        conversation named after something sensitive still contributes a
        verdict without contributing its name.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            error = routing_check._persist_compliance_record(
                _audit_report(), session_id="fix login 500", root_dir=root
            )
            again = routing_check._persist_compliance_record(
                _audit_report(), session_id="fix login 500", root_dir=root
            )
            records = _read_jsonl(learning_journal.journal_path(root))

        self.assertIsNone(error)
        self.assertIsNone(again)
        self.assertEqual(len(records), 2)
        derived = records[0]["session_id"]
        self.assertNotEqual(derived, "fix login 500")
        self.assertTrue(learning_journal.TASK_ID_RE.fullmatch(derived))
        self.assertNotIn("login", derived, "a digest, never the name it came from")
        self.assertEqual(
            records[1]["session_id"],
            derived,
            "two audits of one conversation must stay one session, or the "
            "per-session reduction ComplianceRecord documents cannot work",
        )

    def test_a_record_that_cannot_be_built_is_reported_never_raised(self) -> None:
        """A record `ComplianceRecord` refuses is a call-site bug by
        `learning_journal`'s own contract, but from `run_audit`'s point of
        view it must degrade exactly like a broken disk: reported back as a
        string, never an exception the audit run has to survive.

        Reached here through a metric no `ComplianceRecord` can hold rather
        than through a malformed `session_id`, which no longer fails at all —
        see `test_a_session_id_the_journal_cannot_hold_is_digested_never_dropped`.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            error = routing_check._persist_compliance_record(
                _audit_report(total_writes=-1),
                session_id="sess-impossible-metric",
                root_dir=root,
            )

            self.assertIsNotNone(error)
            assert error is not None
            self.assertIn("compliance record", error.lower())
            self.assertFalse(learning_journal.journal_path(root).exists())

    def test_two_audits_of_one_session_are_distinguishable_from_two_sessions(self) -> None:
        """Item 2's property, and ticket 16's precondition: `routing-audit.sh`
        with no argument audits the most recent conversation, so a plain run
        followed by a `--strict` one appends two records under one session id.
        That is a re-audit, not a second session — and a consumer must be able
        to tell it from one audit whose line got duplicated, which is the job
        `run_id` does here.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            routing_check._persist_compliance_record(
                _audit_report(), session_id="sess-reaudited", root_dir=root
            )
            routing_check._persist_compliance_record(
                _audit_report(violations=[(1, ["src/app.py"])], exit_code=1),
                session_id="sess-reaudited",
                root_dir=root,
            )
            routing_check._persist_compliance_record(
                _audit_report(), session_id="sess-other", root_dir=root
            )
            records = _read_jsonl(learning_journal.journal_path(root))

        self.assertEqual(len(records), 3)
        reaudited = [r for r in records if r["session_id"] == "sess-reaudited"]
        self.assertEqual(len(reaudited), 2)
        self.assertNotEqual(
            reaudited[0]["run_id"],
            reaudited[1]["run_id"],
            "two audits of one session that share a run_id are indistinguishable "
            "from one audit written twice",
        )
        # The documented reduction: group by session, last record wins.
        self.assertEqual(len({r["session_id"] for r in records}), 2)
        self.assertEqual(reaudited[-1]["violation_count"], 1)

    def test_session_last_activity_is_recorded_when_the_caller_knows_it(self) -> None:
        """`timestamp` is when the audit ran, never when the session happened
        — auditing a backlog in one afternoon stamps every record minutes
        apart — so the record carries the session's own last-activity moment
        separately, and omits it rather than substituting when it has none."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            routing_check._persist_compliance_record(
                _audit_report(),
                session_id="sess-with-activity",
                root_dir=root,
                session_last_activity="2026-08-01T09:30:00Z",
            )
            routing_check._persist_compliance_record(
                _audit_report(), session_id="sess-without-activity", root_dir=root
            )
            with_activity, without_activity = _read_jsonl(
                learning_journal.journal_path(root)
            )

        self.assertEqual(with_activity["session_last_activity"], "2026-08-01T09:30:00Z")
        self.assertNotIn(
            "session_last_activity",
            without_activity,
            "absent, never a substituted `timestamp`: a missing point is one "
            "the trendline skips, a wrong one is a point it plots wrongly",
        )

    def test_session_last_activity_comes_from_the_audited_logs_own_mtime(self) -> None:
        """The derivation `run_audit` actually uses, driven against a real
        file rather than a hand-passed string."""
        # Locally imported for the reason `_InstalledHarness.__enter__` gives
        # about `typing.Self`: a new import at the top of this file is a merge
        # conflict for every concurrent branch.
        import time

        with tempfile.TemporaryDirectory() as tmp:
            log_file = Path(tmp) / "overview.txt"
            log_file.write_text("Step 1: nothing")
            os.utime(log_file, (1_754_000_000, 1_754_000_000))

            derived = routing_check._session_last_activity(log_file)
            missing = routing_check._session_last_activity(Path(tmp) / "absent.txt")

        self.assertIsNotNone(derived)
        assert derived is not None
        self.assertTrue(learning_journal.TIMESTAMP_RE.fullmatch(derived))
        self.assertEqual(
            derived, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(1_754_000_000))
        )
        self.assertIsNone(missing, "an unstattable log yields nothing, not a guess")

    def test_write_failure_is_reported_not_raised(self) -> None:
        """Matches tickets 13 and 14's contract for a broken `.ralph`."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            learning_journal.journal_path(root).parent.write_text("not a directory")

            error = routing_check._persist_compliance_record(
                _audit_report(), session_id="sess-write-failure", root_dir=root
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
            records = _read_jsonl(learning_journal.journal_path(root))
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
            records = _read_jsonl(learning_journal.journal_path(root))
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["session_id"], "sess-cli-violating")
            self.assertEqual(records[0]["violation_count"], 2)

    def test_a_session_named_after_a_secret_is_recorded_at_the_cli_boundary(self) -> None:
        """Item 1 end to end, through the surface an operator actually uses.

        `routing-audit.sh` hands this script a conversation *directory name*.
        The journal used to run its sensitivity-marker gate over that name, so
        auditing a conversation called `secret-rotation` printed a verdict and
        then silently persisted nothing — losing exactly the sessions whose
        protocol discipline is most worth a trendline, and losing them
        permanently, since nothing re-audits a session.
        """
        for session_id in ("secret-rotation", "api_key-migration", "sk-live-rotation"):
            with self.subTest(session_id=session_id), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                result = self._run(
                    root,
                    "--session-id",
                    session_id,
                    str(FIXTURES_DIR / "clean_log.txt"),
                )

                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                records = _read_jsonl(learning_journal.journal_path(root))
                self.assertEqual(len(records), 1)
                self.assertEqual(
                    records[0]["session_id"],
                    session_id,
                    "recorded verbatim: the id is a directory name the audit "
                    "was handed, not prose the journal adjudicates",
                )
                self.assertEqual(result.stderr, "", "and with nothing to warn about")

    def test_no_session_id_never_creates_the_ralph_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = self._run(root, str(FIXTURES_DIR / "clean_log.txt"))

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertFalse(learning_journal.journal_path(root).parent.exists())

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
            self.assertFalse(learning_journal.journal_path(root).parent.exists())

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
            learning_journal.journal_path(Path(blocked_root)).parent.write_text(
                "not a directory"
            )

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
                learning_journal.journal_path(Path(healthy_root)).exists()
            )
            self.assertIn("failed to write learning journal record", blocked.stderr)

    def test_session_id_missing_a_value_fails_closed_with_usage(self) -> None:
        """Fails closed *and* says why. Asserting only the exit code let this
        test pass for any reason a run might exit 2 — a config load failure, a
        traceback, a missing log file — while the message the code actually
        prints went uncovered."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = self._run(
                root, str(FIXTURES_DIR / "clean_log.txt"), "--session-id"
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("--session-id requires a value", result.stderr)
            self.assertEqual(result.stdout, "", "a failed-closed run audits nothing")
            self.assertFalse(learning_journal.journal_path(root).parent.exists())

    def test_root_dir_missing_a_value_fails_closed_with_usage(self) -> None:
        """`--root-dir`'s missing-value branch is identical to
        `--session-id`'s and had no test at all: `_run` always injects the
        flag with a value, so nothing in this class could ever reach it. Run
        as a bare subprocess for that reason — `_run` would supply a first
        `--root-dir` and `sys.argv.index` would find that one instead.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROUTING_CHECK),
                    "--session-id",
                    "sess-root-dir-no-value",
                    str(FIXTURES_DIR / "clean_log.txt"),
                    "--root-dir",
                ],
                capture_output=True,
                check=False,
                text=True,
                cwd=str(root),
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("--root-dir requires a value", result.stderr)
            self.assertEqual(result.stdout, "", "a failed-closed run audits nothing")
            self.assertFalse(learning_journal.journal_path(root).parent.exists())


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
        records = _read_jsonl(learning_journal.journal_path(self.root_dir))
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["session_id"], self.conv_id)

    def test_wrapper_persists_on_violation_too(self) -> None:
        shutil.copy(FIXTURES_DIR / "direct_then_code_log.txt", self.log_dir / "overview.txt")

        result = self._run_audit()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        records = _read_jsonl(learning_journal.journal_path(self.root_dir))
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

    def test_one_journal_destination_type_across_every_surface(self) -> None:
        """The same unification, in the type system rather than on disk.

        `root_dir` names one concept — where the journal lives — and carried
        three types across six functions: `Path` inside `learning_journal`,
        `str | Path | None` at `routing_check`'s two, and a `Path(root_dir)`
        re-coercion in between. Every such coercion is a place a caller has to
        re-decide what it is holding, and a re-decision is where a `str` that
        should have been a `Path` survives to the next function. `argv` is the
        one honest boundary where a destination is text; `main` converts there
        and nothing inward of it re-decides.

        Annotations are read as strings because every module here uses
        `from __future__ import annotations` — which is also why a mistyped
        annotation cannot fail at import and needs a test to catch it.

        `SecurityContext` / `get_calibration_secret` keep their own
        pre-existing `str | Path | None`: that `root_dir` is a *repository*
        root for a calibration key, not a journal destination, and it is not
        this loop's to change.
        """
        import inspect

        surfaces = (
            learning_journal.append_journal_record,
            learning_journal.journal_path,
            learning_outcomes.record_test_result,
            learning_outcomes.record_review_verdict,
            learning_outcomes.record_plan_outcome,
            learning_outcomes.record_stalemate_resolution,
            production_invoker.make_journaled_invoke_worker,
            routing_check._persist_compliance_record,
            routing_check.run_audit,
        )
        for surface in surfaces:
            with self.subTest(surface=surface.__qualname__):
                annotation = inspect.signature(surface).parameters["root_dir"].annotation
                self.assertIn(
                    annotation,
                    {"Path", "Path | None"},
                    f"{surface.__qualname__} re-decides what a journal "
                    f"destination is: {annotation}",
                )

    def test_compliance_and_outcome_records_share_one_journal_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            compliance_error = routing_check._persist_compliance_record(
                _audit_report(), session_id="sess-unification", root_dir=root
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


class CriticalDialogueFacadeCompatibilityTests(unittest.TestCase):
    """The historic facade exposes the full production entry-point surface."""

    def test_run_critical_dialogue_is_exported_with_the_production_signature(self) -> None:
        import importlib
        import inspect

        debate_orchestrator = importlib.import_module("debate_orchestrator")
        self.assertIn("run_critical_dialogue", advisory_consultation.__all__)
        for module in (advisory_consultation, debate_orchestrator):
            with self.subTest(module=module.__name__):
                self.assertEqual(
                    inspect.signature(module.run_critical_dialogue),
                    inspect.signature(module.run_advisory_consultation_debate),
                )


class WorkerRoutingPackageContractTests(unittest.TestCase):
    """The installable package facade exposes its canonical public contract."""

    def test_package_import_exports_version_symbols_and_core_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            package_root = Path(temporary_dir)
            os.symlink(SKILL_DIR, package_root / "worker_routing")
            sys.path.insert(0, str(package_root))
            try:
                package = importlib.import_module("worker_routing")
                self.assertEqual(package.__version__, "3.5.0")
                self.assertEqual(tuple(sorted(package.__all__)), package.__all__)

                self.assertIs(
                    package.LearningJournal,
                    importlib.import_module("worker_routing.learning_journal"),
                )
                self.assertIs(
                    package.LearnedState,
                    importlib.import_module("worker_routing.learned_state"),
                )
                self.assertIs(
                    package.ReviewCouncil,
                    importlib.import_module("worker_routing.advisory_consultation").ReviewCouncil,
                )
                self.assertIs(
                    package.run_critical_dialogue,
                    importlib.import_module(
                        "worker_routing.advisory_consultation"
                    ).run_critical_dialogue,
                )
                self.assertEqual(
                    package.resolve_model_name("Codex 5.6 Sol"),
                    "gpt-5.6-sol",
                )
                self.assertEqual(package.classify_complexity(" SIMPLE "), "simple")
            finally:
                sys.path.remove(str(package_root))
                for name in list(sys.modules):
                    if name == "worker_routing" or name.startswith("worker_routing."):
                        del sys.modules[name]


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
                names = []
                if node.module:
                    names.append(node.module)
                names.extend(alias.name for alias in node.names)
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

    def test_every_production_module_in_the_skill_directory_is_managed(self) -> None:
        """Closure under imports is not enough on its own: it can only see a
        module something already imports.

        A *leaf* module — one shipped before the ticket that calls it — has no
        managed importer, so the test above walks straight past it and the
        installer omits it silently. That is exactly how `acceptance_gate.py`
        (ticket 18) shipped unmanaged: its eventual caller is ticket 22's
        learner-worker, still unwritten, so nothing in the directory imported
        it yet. `learning_report.py` had reached the same gap one ticket
        earlier for the same reason.

        The invariant that actually holds is simpler than closure and does not
        depend on who imports whom: every non-test module here is production
        code, and production code that the installer does not copy does not
        exist on an installed harness. A module deliberately kept out of an
        install would have to be excluded here explicitly — which is the
        point, since writing that exclusion is a decision, and forgetting a
        line in a bash array is not.
        """
        managed = set(self._managed())
        present = {
            path.name
            for path in SKILL_DIR.glob("*.py")
            if not path.name.startswith("test_")
        }
        self.assertTrue(present, "no production modules found to check")

        for name in sorted(present):
            with self.subTest(module=name):
                self.assertIn(
                    name,
                    managed,
                    f"{name} is production code in {SKILL_DIR.name}/ but "
                    f"install.sh's MANAGED_FILES does not propagate it — it "
                    f"will be absent from every installed harness",
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
        # The Critic reply is built by `_approve` here in the parent and
        # `repr`'d into the child's source, rather than written out as a
        # literal inside it: the VerdictContract's exact text stays
        # single-sourced from the helper every other test uses, so a future
        # change to the contract cannot leave this one child program behind
        # scripting a shape the installed parser no longer accepts.
        plan = "Planner plan."
        program = (
            "import sys\n"
            "from pathlib import Path\n"
            "import advisory_consultation, production_invoker\n"
            f"replies = iter([{plan!r}, {_approve(plan)!r}])\n"
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
            journal = learning_journal.journal_path(harness.project)
            records = _read_jsonl(journal) if journal.exists() else []

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.splitlines()[0], "consensus")
        self.assertEqual(result.stdout.splitlines()[1], "None")
        # 4: 2 `worker_execution` records, 1 `dialogue_quality` record
        # (ticket 24), 1 `outcome` record grading this consultation's own
        # plan (ticket 25).
        self.assertEqual(len(records), 4)
        worker_records = [r for r in records if r["kind"] == "worker_execution"]
        self.assertEqual(len(worker_records), 2)
        for record in worker_records:
            self.assertEqual(record["task_id"], "installed-consultation-1")
            self.assertTrue(record["success"])
        dialogue_records = [r for r in records if r["kind"] == "dialogue_quality"]
        self.assertEqual(len(dialogue_records), 1)
        self.assertEqual(dialogue_records[0]["task_id"], "installed-consultation-1")
        outcome_records = [r for r in records if r["kind"] == "outcome"]
        self.assertEqual(len(outcome_records), 1)
        self.assertEqual(outcome_records[0]["task_id"], "installed-consultation-1")
        self.assertEqual(outcome_records[0]["ground_truth"], "plan")
        self.assertEqual(outcome_records[0]["verdict"], "accepted")


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
            journal = learning_journal.journal_path(harness.project)
            records = _read_jsonl(journal) if journal.exists() else []
            install_prefix_journal = (
                learning_journal.journal_path(harness.home / ".gemini" / "config")
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
            redirected = _read_jsonl(learning_journal.journal_path(elsewhere))
            in_repo = learning_journal.journal_path(harness.project).exists()

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
            stray = sorted(harness.home.rglob(learning_journal.JOURNAL_RELATIVE_PATH.name))
            stray += sorted(outside.rglob(learning_journal.JOURNAL_RELATIVE_PATH.name))
            stray += sorted(harness.project.rglob(learning_journal.JOURNAL_RELATIVE_PATH.name))

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

    # Unannotated return for the reason `_audit_report`
    # gives: `advisory_consultation.AdvisoryDebateResult` is not a name mypy
    # can resolve through a path-loaded module.
    def _run(self, task_id: str, root: Path):
        with mock.patch.object(
            production_invoker,
            "invoke_worker",
            side_effect=[
                "Planner's proposed plan.",
                _approve("Planner's proposed plan."),
            ],
        ):
            return advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite", root_dir=root, task_id=task_id
            )

    def test_a_task_id_the_journal_rejects_does_not_fail_the_consultation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = self._run("not a valid task id", root)
            journal_written = learning_journal.journal_path(root).exists()

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
            records = _read_jsonl(learning_journal.journal_path(root))

        self.assertIsNone(result.error)
        # 4: 2 `worker_execution` records, 1 `dialogue_quality` record
        # (ticket 24), 1 `outcome` record grading this consultation's own
        # plan (ticket 25).
        self.assertEqual(len(records), 4)
        for record in records:
            self.assertEqual(record["task_id"], "task-still-journaled-1")
        worker_records = [r for r in records if r["kind"] == "worker_execution"]
        self.assertEqual(len(worker_records), 2)
        dialogue_records = [r for r in records if r["kind"] == "dialogue_quality"]
        self.assertEqual(len(dialogue_records), 1)


class JournaledRunIdWiringFailureTests(unittest.TestCase):
    """`run_id` faces the same fail-fast-at-wiring-time rule `task_id` faces
    in `JournaledInvokeWorkerTests.test_an_unjournalable_task_id_is_refused_at_wiring_time`
    (`test_production_invoker.py`). A caller-supplied `run_id` used to be
    accepted at wiring time and only checked once per invocation, when a
    `WorkerExecutionRecord` was built from it — every record for the run
    then silently failed to write, one stderr line per call, in a stream
    easy to lose across a long run. Both identifiers are now checked at the
    same moment, before any worker runs.
    """

    def test_an_unjournalable_run_id_is_refused_at_wiring_time(self) -> None:
        runner = mock.Mock(
            return_value=subprocess.CompletedProcess([], 0, "worker output", "")
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            with self.assertRaises(ValueError):
                production_invoker.make_journaled_invoke_worker(
                    "task-1", root_dir=root, run_id="not a valid run id", runner=runner
                )

            runner.assert_not_called()
            self.assertFalse(learning_journal.journal_path(root).exists())


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
    than a hardcoded literal — mirroring `is_canary_dialogue`'s identical
    `_load_canary_cadence_config` pattern. Thresholds fall at multiples of
    the cap: spend under 1x the cap is rung 0, 1x-2x is rung 1, 2x-3x is
    rung 2, 3x and beyond is rung 3 — see the module comment above
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
        """A caller passing a negative spend is under budget by construction —
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
        """A session with no budget at all has no room for any dialogue —
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
    `light_doer` role block — `light_doer.name` lists several
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
    rounds/effort it always did — the entire pre-existing suite is this
    ticket's regression guard that the opt-in changes nothing when a caller
    does not ask for it, mirroring ticket 07's and ticket 08's identical
    regression argument for their own opt-in seams.

    The checked-in `routing-config.json` sets `dialogue_budget.session_dialogue_cap`
    to `10`, matching `DEFAULT_SESSION_DIALOGUE_CAP` exactly — these tests
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
        phrase) — a session deep into budget exhaustion must still halt on
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
        round loop already reads regardless of topology — proves it holds
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
        specific — rung 2 must 'cheapen the roster (e.g. fall back toward
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
        resolved a roster for this call — budget exhaustion is a
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
        unconditionally, even for a call that also opted into `is_canary` —
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

    def test_rung_two_canary_reports_degraded_independence_by_design(self) -> None:
        """Pins the canary × rung-2 combination as designed behavior, not an
        accident: an `is_canary=True` run at rung 2 reports
        `degraded_independence=True` even though a canary invokes only the
        Critic role. The flag states the effective roster's family collapse
        — rung 2 substituted the one cheap model into every seat, and the
        one seat this probe actually used got that degraded model — so on a
        canary record it carries exactly the signal a canary auditor needs:
        this probe measured the degraded cheap Critic, not the production
        Critic. And it can never distort mission-level statistics, because
        canary records are mandatorily filtered out of mission aggregation
        (`outcome != "canary"`, per `AdvisoryTelemetryRecord`'s own
        WARNING) — the flag rides only where the canary auditor looks."""
        cap = advisory_consultation.DEFAULT_SESSION_DIALOGUE_CAP
        fixture = advisory_consultation.CANARY_FIXTURES[0]
        degraded_model = advisory_consultation._load_degraded_roster_model(
            advisory_consultation._CONFIG_PATH
        )
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            invoker = _RecordingInvoker([_approve_fixture(fixture)])
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite",
                invoker,
                root_dir=root,
                is_canary=True,
                canary_fixture=fixture,
                session_spend_so_far=2 * cap,
            )
            records = _read_jsonl(root / ".ralph" / "routing_telemetry.jsonl")

        self.assertEqual(result.outcome, "canary")
        self.assertEqual(result.degradation_rung, 2)
        self.assertTrue(result.degraded_independence)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["outcome"], "canary")
        self.assertTrue(records[0]["degraded_independence"])
        self.assertEqual(len(invoker.calls), 1)
        self.assertEqual(invoker.calls[0][0], degraded_model)

    def test_rung_three_canary_leaves_a_real_missions_plan_artifact_untouched(
        self,
    ) -> None:
        """The artifact half of the canary × rung-3 composition. The
        preemption half is already pinned above
        (`test_rung_three_preempts_an_is_canary_call_with_zero_invoker_calls`):
        a fully exhausted budget skips even a canary probe, with zero worker
        calls. But the stale-plan cleanup a real mission's rung-3 exit
        performs must NOT run for a preempted canary — the module's canary
        invariant says a canary never creates nor deletes
        `implementation_plan.md`, and the plan sitting under `root_dir` here
        is a REAL result's artifact, still accurately described by that real
        result. A scheduled probe that happens to arrive while the session
        is exhausted has no business destroying it."""
        cap = advisory_consultation.DEFAULT_SESSION_DIALOGUE_CAP
        fixture = advisory_consultation.CANARY_FIXTURES[0]
        real_plan = "Real mission's consensus plan — still current.\n"
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            plan_path = root / "implementation_plan.md"
            plan_path.write_text(real_plan)

            invoker = _RecordingInvoker([])
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite",
                invoker,
                root_dir=root,
                is_canary=True,
                canary_fixture=fixture,
                session_spend_so_far=3 * cap,
            )

            self.assertTrue(plan_path.exists())
            self.assertEqual(plan_path.read_bytes(), real_plan.encode("utf-8"))

        self.assertEqual(result.outcome, "budget_skipped")
        self.assertEqual(invoker.calls, [])


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


class IsLocalFamilyTests(unittest.TestCase):
    """Spec 0003 (CriticalDialogue) ticket 11: `is_local_family`, the pure
    predicate the sensitive-task roster wrapper (see
    `SensitiveTaskLocalOnlyDialogueTests` below) is built from. Tested here
    on its own, standalone, the same way `ModelFamilyClassifierTests` above
    tests `classify_model_family` on its own — a pure function earns its
    own focused test class before it earns an integration test through the
    full debate loop.
    """

    def test_every_cloud_family_reads_as_not_local(self) -> None:
        """`is_local_family` is documented as derived from
        `_CLOUD_FAMILY_SUBSTRINGS` rather than a second, hand-written list
        of cloud names — this asserts against that constant directly (not
        a copy-pasted `{"claude", "gemini", "codex-gpt"}` literal) so the
        test itself cannot silently drift from the source it is meant to
        guard, and separately pins today's known cloud vocabulary so a
        change to that vocabulary is visible here too."""
        cloud_families = {
            family
            for _substring, family in advisory_consultation._CLOUD_FAMILY_SUBSTRINGS
        }
        self.assertEqual(cloud_families, {"claude", "gemini", "codex-gpt"})
        for family in cloud_families:
            with self.subTest(family=family):
                self.assertFalse(advisory_consultation.is_local_family(family))

    def test_local_lineages_read_as_local(self) -> None:
        for family in ("gemma", "qwen", "llama", "mistral", "unknown"):
            with self.subTest(family=family):
                self.assertTrue(advisory_consultation.is_local_family(family))

    def test_agrees_with_classify_model_family_for_every_default_roster_model(
        self,
    ) -> None:
        """Drift guard spanning both functions at once: for every model
        name this module's own `DEFAULT_ROSTER_FALLBACK_CHAINS` actually
        names, `is_local_family(classify_model_family(model))` must agree
        with which of those models are genuinely local — proving the two
        functions stay consistent with each other in practice, not merely
        individually correct in isolation."""
        cloud_models = (
            "Claude Opus 5 (Thinking)",
            "Claude Fable 5",
            "Codex 5.6 Sol",
            "GPT-OSS 120B (Medium)",
            "Gemini 3.6 Flash (High)",
            "Gemini 3.1 Pro (High)",
            "Gemini 3.6 Flash",
        )
        local_models = ("Gemma 4 E4B", "Qwen3.8-27B-MLX-6bit")
        for model in cloud_models:
            with self.subTest(model=model):
                self.assertFalse(
                    advisory_consultation.is_local_family(
                        advisory_consultation.classify_model_family(model)
                    )
                )
        for model in local_models:
            with self.subTest(model=model):
                self.assertTrue(
                    advisory_consultation.is_local_family(
                        advisory_consultation.classify_model_family(model)
                    )
                )


class SensitiveTaskLocalOnlyDialogueTests(unittest.TestCase):
    """Spec 0003 (CriticalDialogue) ticket 11: the sensitivity gate
    (`_detect_sensitivity_marker` and its fail-closed behaviour) now
    precedes every occasion, not only ambiguity — a sensitive task may hold
    a dialogue only between local models from two local families, and when
    the local runtime is unavailable the consultation still fails closed
    and escalates to the human, exactly spec 0001's original behaviour
    (`AdvisorySensitivityGateTests`, untouched above), now proven true for
    plan-review, code-review, and post-mortem as well.

    `_ALL_FAMILIES` deliberately scripts every cloud family
    (`claude`/`codex-gpt`/`gemini`) as reachable ALONGSIDE both local
    families this repo's own default roster fallback chains actually offer
    (`gemma`, `qwen` — see `DEFAULT_ROSTER_FALLBACK_CHAINS` in
    advisory_consultation.py). Scripting the cloud families as reachable
    too, rather than simply omitting them, is what actually proves the
    local-only gate is doing the filtering: a test where nothing cloud was
    ever "up" in the first place would pass even if the gate did nothing at
    all. `_CLOUD_ONLY` is the mirror-image fake for the "local runtime is
    unavailable" tests: everything cloud is reachable, nothing local is,
    which is the scenario a sensitive task must fail closed against.
    """

    _ALL_FAMILIES: tuple[str, ...] = ("claude", "codex-gpt", "gemini", "gemma", "qwen")
    _CLOUD_ONLY: tuple[str, ...] = ("claude", "codex-gpt", "gemini")

    _SENSITIVE_TASK = "Rotate the api_key before the review lands"

    @staticmethod
    def _complexity_for(occasion: str) -> str:
        """Panel topology (spec 0003 ticket 05) exists only for
        plan-review/code-review at `complexity="complex"` (see
        `_is_panel_topology`); ambiguity and post-mortem stay pair-mode
        regardless of complexity. Same per-occasion choice
        `AdvisoryOccasionParameterizationTests` already makes for its own
        cross-occasion loop above."""
        return "complex" if occasion in ("plan-review", "code-review") else "medium"

    def test_criterion_1_zero_cloud_family_calls_across_all_four_occasions(self) -> None:
        """Acceptance criterion 1: a sensitive task's dialogue on every
        occasion invokes zero cloud-family workers — verified by
        classifying what the recording fake actually recorded
        (`classify_model_family` on the real `model` argument), never by
        string-matching model names."""
        for occasion in ("ambiguity", "plan-review", "code-review", "post-mortem"):
            with self.subTest(occasion=occasion):
                complexity = self._complexity_for(occasion)
                with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
                    os.environ, {}, clear=True
                ):
                    root = Path(tmp)
                    plan = "Planner's plan for the sensitive task."
                    responses: list[str | Exception]
                    if complexity == "complex":
                        responses = [
                            plan,
                            _approve(plan, "Critic A: solid."),
                            _approve(plan, "Critic B: solid."),
                        ]
                    else:
                        responses = [plan, _approve(plan)]
                    invoker = _RecordingInvoker(responses)
                    result = advisory_consultation.run_advisory_consultation_debate(
                        self._SENSITIVE_TASK,
                        invoker,
                        root_dir=root,
                        occasion=occasion,
                        complexity=complexity,
                        reachability_check=_reachable(*self._ALL_FAMILIES),
                    )

                self.assertTrue(result.consensus_reached)
                self.assertTrue(invoker.calls)
                called_families = {
                    advisory_consultation.classify_model_family(model)
                    for model, _effort, _prompt in invoker.calls
                }
                for family in called_families:
                    self.assertTrue(
                        advisory_consultation.is_local_family(family),
                        f"occasion {occasion!r} invoked cloud family {family!r}",
                    )

    def test_criterion_2_local_runtime_unavailable_fails_closed_across_all_four_occasions(
        self,
    ) -> None:
        """Acceptance criterion 2: with `reachability_check` supplied but
        reporting only cloud families up, a sensitive task fails closed and
        escalates — `sensitivity_halt`, zero worker calls, no
        `implementation_plan.md` — matching spec 0001's existing
        ambiguity-occasion behaviour, for all four occasions."""
        for occasion in ("ambiguity", "plan-review", "code-review", "post-mortem"):
            with self.subTest(occasion=occasion):
                complexity = self._complexity_for(occasion)
                with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
                    os.environ, {}, clear=True
                ):
                    root = Path(tmp)
                    invoker = _RecordingInvoker([])
                    result = advisory_consultation.run_advisory_consultation_debate(
                        self._SENSITIVE_TASK,
                        invoker,
                        root_dir=root,
                        occasion=occasion,
                        complexity=complexity,
                        reachability_check=_reachable(*self._CLOUD_ONLY),
                    )
                    plan_exists = (root / "implementation_plan.md").exists()

                self.assertEqual(result.outcome, "sensitivity_halt")
                self.assertEqual(invoker.calls, [])
                self.assertFalse(plan_exists)

    def test_criterion_3_sensitive_panel_spans_two_distinct_local_families(self) -> None:
        """Acceptance criterion 3: a sensitive panel (Complex tier) must
        not collapse to one local family serving all three roles when a
        second local family is actually reachable. This repo's own default
        `roster_topology.role_fallback_chains` offer exactly two distinct
        local families across all three roles (`gemma` for planner and
        critic_b, `qwen` for critic_a — see `DEFAULT_ROSTER_FALLBACK_CHAINS`
        in advisory_consultation.py), so "two distinct, not one" is the
        strongest claim provable against the real production config without
        hand-rolling a custom one for this test.

        Because that real chain resolves planner=gemma, critic_a=qwen,
        critic_b=gemma, `critic_b`'s own preferred family (`gemma`) is
        already claimed by `planner` by the time `resolve_roster` reaches
        it, so it can only be assigned via `resolve_roster`'s second,
        degraded pass — `result.degraded_independence` must therefore read
        `True` here. Asserting that, not just the family count above, is
        what proves the flag itself stays honest for this exact roster
        rather than merely going unexamined by this test."""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            plan = "Planner's plan for the sensitive task."
            invoker = _RecordingInvoker(
                [
                    plan,
                    _approve(plan, "Critic A: solid."),
                    _approve(plan, "Critic B: solid."),
                ]
            )
            result = advisory_consultation.run_advisory_consultation_debate(
                self._SENSITIVE_TASK,
                invoker,
                root_dir=root,
                occasion="plan-review",
                complexity="complex",
                reachability_check=_reachable(*self._ALL_FAMILIES),
            )

        self.assertTrue(result.consensus_reached)
        self.assertEqual(len(invoker.calls), 3)
        called_families = [
            advisory_consultation.classify_model_family(model)
            for model, _effort, _prompt in invoker.calls
        ]
        distinct_families = set(called_families)
        self.assertGreaterEqual(
            len(distinct_families),
            2,
            f"panel reused one local family for all three roles: {called_families!r}",
        )
        for family in distinct_families:
            self.assertTrue(advisory_consultation.is_local_family(family))
        self.assertTrue(
            result.degraded_independence,
            "planner and critic_b both resolve to gemma against the real "
            "routing-config.json chains, so this roster is degraded and "
            "must say so",
        )

    def test_criterion_4_rung_two_drops_effort_but_never_the_local_roster(self) -> None:
        """Regression guard for the rung-2 carve-out (design point 4 of
        this ticket): budget exhaustion may still cheapen a sensitive
        dialogue's effort, but must never launder it onto
        `_load_degraded_roster_model`'s substitute, which resolves to a
        CLOUD model (`light_doer.name` in routing-config.json)."""
        cap = advisory_consultation.DEFAULT_SESSION_DIALOGUE_CAP
        cloud_light_doer_model = advisory_consultation._load_degraded_roster_model(
            advisory_consultation._CONFIG_PATH
        )
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            invoker = _RecordingInvoker(["Planner's plan.", _revise("Not convinced.")])
            result = advisory_consultation.run_advisory_consultation_debate(
                self._SENSITIVE_TASK,
                invoker,
                root_dir=root,
                max_rounds=3,
                reachability_check=_reachable(*self._ALL_FAMILIES),
                session_spend_so_far=2 * cap,
            )

        self.assertEqual(result.degradation_rung, 2)
        # Rungs compound: rung 2 still carries rung 1's reduced round cap.
        self.assertEqual(result.rounds_run, 1)
        self.assertEqual(len(invoker.calls), 2)
        efforts = {effort for _model, effort, _prompt in invoker.calls}
        self.assertEqual(efforts, {advisory_consultation._DEGRADED_EFFORT})
        called_models = {model for model, _effort, _prompt in invoker.calls}
        self.assertNotIn(cloud_light_doer_model, called_models)
        for model in called_models:
            family = advisory_consultation.classify_model_family(model)
            self.assertTrue(
                advisory_consultation.is_local_family(family),
                f"rung 2 invoked non-local model {model!r} (family {family!r}) "
                "on a sensitive task",
            )

    def test_criterion_5_no_reachability_check_still_halts_closed_pinned_regression(
        self,
    ) -> None:
        """Pins spec 0001's original, unconditional halt
        (`AdvisorySensitivityGateTests` above) as this ticket's own
        regression guard, so a future edit cannot quietly change it:
        omitting `reachability_check` gives this module no way to
        establish a local runtime exists at all, so "fail closed" remains
        the only honest answer — completely unaffected by this ticket's new
        local-only roster path, which only ever activates once a
        `reachability_check` seam is actually supplied."""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            invoker = _RecordingInvoker(
                ["Planner's plan.", _approve("Planner's plan.")]
            )
            result = advisory_consultation.run_advisory_consultation_debate(
                self._SENSITIVE_TASK, invoker, root_dir=root
            )

        self.assertEqual(result.outcome, "sensitivity_halt")
        self.assertEqual(invoker.calls, [])

    def test_criterion_6_roster_exhaustion_halt_never_leaks_task_text_or_secret(
        self,
    ) -> None:
        """The redaction boundary `_detect_sensitivity_marker`/
        `_render_sensitivity_halt_transcript` establish for the
        `reachability_check is None` halt path must hold just as tightly
        for this ticket's new halt path — a `RosterResolutionError` caught
        because no local family was reachable — since both converge on the
        identical `sensitivity_halt` outcome and the identical redacted
        transcript renderer."""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            task = "Plan the rollout; api_key=sk-supersecretvalue-do-not-print"
            result = advisory_consultation.run_advisory_consultation_debate(
                task,
                _RecordingInvoker([]),
                root_dir=root,
                reachability_check=_reachable(*self._CLOUD_ONLY),
            )
            transcript = (root / ".scratch" / "planning_debate.md").read_text()

        self.assertEqual(result.outcome, "sensitivity_halt")
        self.assertIsNotNone(result.error)
        assert result.error is not None
        self.assertNotIn("supersecretvalue", result.error)
        self.assertNotIn(task, result.error)
        self.assertNotIn("supersecretvalue", transcript)
        self.assertNotIn(task, transcript)


class SensitiveTaskDispatchPathTests(unittest.TestCase):
    """Spec 0003 (CriticalDialogue) ticket 11, closing a review finding
    against `SensitiveTaskLocalOnlyDialogueTests` above:
    `test_criterion_1_zero_cloud_family_calls_across_all_four_occasions`
    proves the local-only gate inside `run_advisory_consultation_debate`
    itself, including for `occasion="post-mortem"` — but it gets there by
    calling that function directly with its own `reachability_check`. It
    never goes through `dispatch_post_mortem_consultation`, which is the
    actual production entry point for the post-mortem occasion.

    Before this ticket, `dispatch_post_mortem_consultation` exposed no
    `reachability_check`/`roster_config_path` parameters at all, so every
    dispatched post-mortem always called `run_advisory_consultation_debate`
    with `reachability_check=None` — which always hits that function's
    unconditional `marker is not None and reachability_check is None` halt
    for a sensitive task, regardless of whether a local runtime was
    actually up. A sensitive task dispatched for post-mortem could
    therefore never hold the local-only dialogue user story 19 requires; it
    could only ever escalate. `SensitiveTaskLocalOnlyDialogueTests`'s own
    criterion-1 test could not have caught that, because it never calls the
    dispatch function at all. These tests exercise the real
    `dispatch_post_mortem_consultation`, with its real background thread
    and the real default `routing-config.json`, closing that gap
    specifically.
    """

    _SENSITIVE_TASK = "Rotate the api_key before the review lands"
    _ALL_FAMILIES: tuple[str, ...] = ("claude", "codex-gpt", "gemini", "gemma", "qwen")
    _CLOUD_ONLY: tuple[str, ...] = ("claude", "codex-gpt", "gemini")

    def test_dispatched_sensitive_post_mortem_holds_a_local_only_dialogue(self) -> None:
        """A sensitive task dispatched through the real
        `dispatch_post_mortem_consultation`, with every family (cloud and
        local) reachable, actually runs the dialogue to consensus, and
        every call the recording fake observed belongs to a local family —
        verified by classifying the real recorded `model` argument via
        `classify_model_family`/`is_local_family`, never by string-matching
        model names. Scripting every cloud family as reachable too, rather
        than omitting them, is what actually proves the dispatch path's
        gate is filtering: a test where nothing cloud was ever "up" in the
        first place would pass even if the gate did nothing at all (same
        reasoning `SensitiveTaskLocalOnlyDialogueTests` documents for its
        own `_ALL_FAMILIES`). The thread is joined before any assertion
        runs, the same way every other dispatch test in
        `AdvisoryBlockingStanceTests` synchronizes with the background
        thread rather than racing it."""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            plan = "Planner's post-mortem lesson for the sensitive task."
            invoker = _RecordingInvoker([plan, _approve(plan)])

            thread = advisory_consultation.dispatch_post_mortem_consultation(
                self._SENSITIVE_TASK,
                invoker,
                root_dir=root,
                reachability_check=_reachable(*self._ALL_FAMILIES),
            )
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive(), "background thread leaked past the test")

            records = _read_jsonl(
                root / AdvisoryTranscriptAndTelemetryTests.TELEMETRY_RELATIVE_PATH
            )

        self.assertTrue(invoker.calls)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["outcome"], "consensus")
        called_families = {
            advisory_consultation.classify_model_family(model)
            for model, _effort, _prompt in invoker.calls
        }
        for family in called_families:
            self.assertTrue(
                advisory_consultation.is_local_family(family),
                f"dispatched sensitive post-mortem invoked cloud family {family!r}",
            )

    def test_dispatched_sensitive_post_mortem_fails_closed_with_no_local_family_up(
        self,
    ) -> None:
        """The mirror case: `reachability_check` is supplied, but only
        cloud families report reachable. The dispatched debate must fail
        closed as `sensitivity_halt` with zero worker calls and no
        `implementation_plan.md` — the dispatch path offering a local-only
        roster does not mean it may ever launder a sensitive task onto a
        cloud worker merely because the caller's own probe says cloud is
        up; "no local family reachable" must escalate exactly the way
        `SensitiveTaskLocalOnlyDialogueTests.
        test_criterion_2_local_runtime_unavailable_fails_closed_across_all_four_occasions`
        already proves for the synchronous path."""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            invoker = _RecordingInvoker([])

            thread = advisory_consultation.dispatch_post_mortem_consultation(
                self._SENSITIVE_TASK,
                invoker,
                root_dir=root,
                reachability_check=_reachable(*self._CLOUD_ONLY),
            )
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive(), "background thread leaked past the test")

            records = _read_jsonl(
                root / AdvisoryTranscriptAndTelemetryTests.TELEMETRY_RELATIVE_PATH
            )
            plan_exists = (root / "implementation_plan.md").exists()

        self.assertEqual(invoker.calls, [])
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["outcome"], "sensitivity_halt")
        self.assertFalse(plan_exists)

    def test_dispatched_sensitive_post_mortem_with_no_reachability_check_still_halts(
        self,
    ) -> None:
        """Pinned regression for the dispatch path specifically, mirroring
        `SensitiveTaskLocalOnlyDialogueTests.
        test_criterion_5_no_reachability_check_still_halts_closed_pinned_regression`:
        omitting `reachability_check` — still this parameter's default on
        `dispatch_post_mortem_consultation` — must halt closed exactly as
        it always did before this ticket added the parameter at all. Adding
        the seam is opt-in surface, never a change to the old default
        behaviour: a caller that upgrades to a newer signature without
        passing the new keyword must observe byte-for-byte the same
        fail-closed halt it always got."""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            invoker = _RecordingInvoker([])

            thread = advisory_consultation.dispatch_post_mortem_consultation(
                self._SENSITIVE_TASK,
                invoker,
                root_dir=root,
            )
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive(), "background thread leaked past the test")

            records = _read_jsonl(
                root / AdvisoryTranscriptAndTelemetryTests.TELEMETRY_RELATIVE_PATH
            )

        self.assertEqual(invoker.calls, [])
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["outcome"], "sensitivity_halt")


# Spec 0004 ticket 24 — the fourth journal family's writer. Appended here, at
# the end of the file, per this file's own convention (see the comment above
# `JournalUnificationTests`): this file is edited concurrently on other
# branches, so an insertion anywhere but the end is a merge conflict for
# everyone.
class DialogueQualityRecordWriterTests(unittest.TestCase):
    """`advisory_consultation._write_dialogue_quality_record` /
    `_reduce_dialogue_round`: one `learning_journal.DialogueQualityRecord`
    per dialogue, written at the `_result` choke point, reduced from the
    `AdvisoryRoundVerdict`s a consultation already computes and discards
    nowhere else. Organized 1:1 against
    `.scratch/routing-backlog/issues/24-dialogue-quality-records.md`'s eight
    acceptance criteria, plus a handful of tests (`test_a_*`, prefixed to read
    near their AC siblings) pinning decisions the implementation plan made
    that no acceptance criterion states directly.
    """

    # --- AC1: one record per dialogue, never one per round ---

    def test_a_two_round_dialogue_writes_exactly_one_record(self) -> None:
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
                "Plan the auth rewrite",
                invoker,
                root_dir=root,
                task_id="dq-two-round-1",
            )
            records = _read_jsonl(learning_journal.journal_path(root))

        self.assertTrue(result.consensus_reached)
        dialogue_records = [r for r in records if r["kind"] == "dialogue_quality"]
        self.assertEqual(len(dialogue_records), 1)
        self.assertEqual(dialogue_records[0]["rounds_run"], 2)
        self.assertEqual(len(dialogue_records[0]["rounds"]), 2)

    def test_a_three_round_stalemate_still_writes_exactly_one_record(self) -> None:
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
                task_id="dq-stalemate-1",
            )
            records = _read_jsonl(learning_journal.journal_path(root))

        self.assertEqual(result.outcome, "stalemate")
        dialogue_records = [r for r in records if r["kind"] == "dialogue_quality"]
        self.assertEqual(len(dialogue_records), 1)
        self.assertEqual(dialogue_records[0]["rounds_run"], 3)

    # --- AC2: carries occasion, topology, rounds, canaries, both flags ---

    def test_the_record_carries_occasion_topology_rounds_canaries_and_both_flags(
        self,
    ) -> None:
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
            advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite",
                invoker,
                root_dir=root,
                occasion="plan-review",
                complexity="complex",
                planner_model="Test Planner",
                critic_a_model="Test Critic A",
                critic_b_model="Test Critic B",
                task_id="dq-fields-1",
            )
            record = next(
                r
                for r in _read_jsonl(learning_journal.journal_path(root))
                if r["kind"] == "dialogue_quality"
            )

        self.assertEqual(record["occasion"], "plan-review")
        self.assertEqual(record["topology"], "panel")
        self.assertEqual(record["rounds"], [{"verdict": "approved", "engagement_count": 1}])
        self.assertIn("canaries_planted", record)
        self.assertIn("canaries_caught", record)
        self.assertIn("degraded", record)
        self.assertIn("independent", record)
        self.assertIn("rounds_run", record)

    def test_flag_polarity_is_not_inverted(self) -> None:
        """The one place a copy-don't-derive bug would be silent: the journal
        names both flags for their healthy state (`degraded`/`independent`),
        while the result names one for its unhealthy state
        (`degraded_independence`). A rung-2 run must read `degraded=True`,
        `independent=False`; a rung-0 run must read the mirror image."""
        cap = advisory_consultation.DEFAULT_SESSION_DIALOGUE_CAP
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            invoker = _RecordingInvoker(["Planner's plan.", _revise("Not convinced.")])
            advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite",
                invoker,
                root_dir=root,
                max_rounds=3,
                session_spend_so_far=2 * cap,
                task_id="dq-rung2-1",
            )
            degraded_record = next(
                r
                for r in _read_jsonl(learning_journal.journal_path(root))
                if r["kind"] == "dialogue_quality"
            )

        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            invoker = _RecordingInvoker(["Planner's plan.", _approve("Planner's plan.")])
            advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite",
                invoker,
                root_dir=root,
                session_spend_so_far=0,
                task_id="dq-rung0-1",
            )
            healthy_record = next(
                r
                for r in _read_jsonl(learning_journal.journal_path(root))
                if r["kind"] == "dialogue_quality"
            )

        self.assertTrue(degraded_record["degraded"])
        self.assertFalse(degraded_record["independent"])
        self.assertFalse(healthy_record["degraded"])
        self.assertTrue(healthy_record["independent"])

    # --- AC3: reduction rule, stated once, never recomputed from text ---

    def test_reduce_dialogue_round_counts_verified_quotes_not_objections(self) -> None:
        entry = advisory_consultation.AdvisoryRoundVerdict(
            critic_a=advisory_consultation.VerdictContractResult("revise", 0, 3)
        )
        self.assertEqual(
            advisory_consultation._reduce_dialogue_round(entry), ("revise", 0)
        )

    def test_reduce_dialogue_round_takes_the_minimum_across_a_panel(self) -> None:
        """The ticket's hard constraint: one engaged Critic must never mask a
        silent one. Critic A verified five quotes, Critic B verified zero —
        the round's count must be zero, never five (`sum`/`max` would both
        report five, indistinguishable from a pair round's sole Critic
        having verified five)."""
        entry = advisory_consultation.AdvisoryRoundVerdict(
            critic_a=advisory_consultation.VerdictContractResult("approved", 5, 0),
            critic_b=advisory_consultation.VerdictContractResult("approved", 0, 0),
        )
        _verdict, count = advisory_consultation._reduce_dialogue_round(entry)
        self.assertEqual(count, 0)
        self.assertNotEqual(count, 5, "min must govern the count, never sum or max")

    def test_reduce_dialogue_round_verdicts_follow_the_panel_control_flow(self) -> None:
        approved = advisory_consultation.VerdictContractResult("approved", 1, 0)
        revise = advisory_consultation.VerdictContractResult("revise", 0, 1)
        unparseable = advisory_consultation.VerdictContractResult("unparseable", 0, 0)
        cases = (
            (approved, approved, "approved"),
            (approved, revise, "revise"),
            (unparseable, approved, "unparseable"),
            (approved, unparseable, "unparseable"),
        )
        for critic_a, critic_b, expected in cases:
            with self.subTest(critic_a=critic_a.verdict, critic_b=critic_b.verdict):
                entry = advisory_consultation.AdvisoryRoundVerdict(
                    critic_a=critic_a, critic_b=critic_b
                )
                verdict, _count = advisory_consultation._reduce_dialogue_round(entry)
                self.assertEqual(verdict, expected)

    def test_an_engaged_critic_cannot_mask_a_silent_one_end_to_end(self) -> None:
        """The unit test above, proven through the real parser: a panel round
        where Critic A quotes the plan and Critic B raises an objection with
        no quote at all must journal `engagement_count == 0` for that round —
        Critic A's engagement never masks Critic B's silence."""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            plan = "Planner's proposed plan."
            critic_a_response = (
                f'Solid start.\nQUOTE: "{plan}"\n1. Consider a rollback step.\n'
                "VERDICT: REVISE"
            )
            invoker = _RoleKeyedInvoker(
                {
                    "Test Planner": [plan],
                    "Test Critic A": [critic_a_response],
                    "Test Critic B": [_revise("No quotes, just a concern.")],
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
                max_rounds=1,
                task_id="dq-masking-1",
            )
            record = next(
                r
                for r in _read_jsonl(learning_journal.journal_path(root))
                if r["kind"] == "dialogue_quality"
            )

        self.assertEqual(record["rounds"][0]["engagement_count"], 0)

    def test_engagement_counts_are_never_recomputed_from_text(self) -> None:
        """The writer reads the already-parsed `round_verdicts` structures,
        never the raw Critic prose: a rationale line that merely mentions the
        word `QUOTE:` without matching the VerdictContract's line-start
        pattern must not inflate the journaled count."""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            plan = "Planner's proposed plan."
            critic_response = (
                "This response never uses a QUOTE: line, it just discusses "
                "the word QUOTE: in passing.\nVERDICT: REVISE"
            )
            invoker = _RecordingInvoker([plan, critic_response])
            advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite",
                invoker,
                root_dir=root,
                max_rounds=1,
                task_id="dq-no-recompute-1",
            )
            record = next(
                r
                for r in _read_jsonl(learning_journal.journal_path(root))
                if r["kind"] == "dialogue_quality"
            )

        self.assertEqual(record["rounds"][0]["engagement_count"], 0)

    # --- AC4: correlates to its task by TaskIdentity ---

    def test_the_dialogue_record_shares_the_task_id_of_the_runs_worker_records(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ), mock.patch.object(
            production_invoker,
            "invoke_worker",
            side_effect=[
                "Planner's proposed plan.",
                _approve("Planner's proposed plan."),
            ],
        ):
            root = Path(tmp)
            advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite",
                root_dir=root,
                planner_model="Claude Opus 5 (Thinking)",
                critic_model="Codex 5.6 Sol",
                task_id="dq-shared-run-1",
            )
            records = _read_jsonl(learning_journal.journal_path(root))

        self.assertEqual({r["task_id"] for r in records}, {"dq-shared-run-1"})
        worker_records = [r for r in records if r["kind"] == "worker_execution"]
        dialogue_records = [r for r in records if r["kind"] == "dialogue_quality"]
        self.assertEqual(len(worker_records), 2)
        self.assertEqual(len(dialogue_records), 1)
        run_ids = {r["run_id"] for r in worker_records} | {dialogue_records[0]["run_id"]}
        self.assertEqual(len(run_ids), 1, "all three records share one run_id")

    def test_a_caller_supplied_journaled_invoker_leaves_the_dialogue_record_run_free(
        self,
    ) -> None:
        """Pins §2.4's anti-rework-inflation rule: when the caller owns the
        journal wiring, `advisory_consultation` cannot see that factory's run
        identity, so it must not mint a second one — the dialogue record
        carries no `run_id` key at all, and `_countable_runs` still reports
        exactly one run for the task."""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            runner = mock.Mock(
                side_effect=[
                    subprocess.CompletedProcess(
                        [], 0, "Planner's proposed plan.", ""
                    ),
                    subprocess.CompletedProcess(
                        [], 0, _approve("Planner's proposed plan."), ""
                    ),
                ]
            )
            journaled_invoker = production_invoker.make_journaled_invoke_worker(
                "dq-caller-run-1", root_dir=root, runner=runner
            )
            advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite",
                journaled_invoker,
                root_dir=root,
                planner_model="Claude Opus 5 (Thinking)",
                critic_model="Codex 5.6 Sol",
                task_id="dq-caller-run-1",
            )
            records = _read_jsonl(learning_journal.journal_path(root))

        dialogue_record = next(r for r in records if r["kind"] == "dialogue_quality")
        self.assertNotIn("run_id", dialogue_record)
        self.assertEqual(len(_countable_runs(records, "dq-caller-run-1")), 1)

    # --- AC5: a canary probe's record is distinguishable ---

    def test_a_canary_catch_and_miss_are_marked_and_a_real_dialogue_is_not(self) -> None:
        fixture = advisory_consultation.CANARY_FIXTURES[0]
        task_description = "Plan the auth rewrite"

        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            catch_invoker = _RecordingInvoker([_revise("Objection.")])
            advisory_consultation.run_advisory_consultation_debate(
                task_description, catch_invoker, root_dir=root, is_canary=True
            )
            catch_record = next(
                r
                for r in _read_jsonl(learning_journal.journal_path(root))
                if r["kind"] == "dialogue_quality"
            )

        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            miss_invoker = _RecordingInvoker([_approve_fixture(fixture)])
            advisory_consultation.run_advisory_consultation_debate(
                task_description, miss_invoker, root_dir=root, is_canary=True
            )
            miss_record = next(
                r
                for r in _read_jsonl(learning_journal.journal_path(root))
                if r["kind"] == "dialogue_quality"
            )

        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            real_invoker = _RecordingInvoker(["Real plan.", _approve("Real plan.")])
            advisory_consultation.run_advisory_consultation_debate(
                task_description, real_invoker, root_dir=root
            )
            real_record = next(
                r
                for r in _read_jsonl(learning_journal.journal_path(root))
                if r["kind"] == "dialogue_quality"
            )

        self.assertEqual(
            (catch_record["canaries_planted"], catch_record["canaries_caught"]), (1, 1)
        )
        self.assertEqual(
            (miss_record["canaries_planted"], miss_record["canaries_caught"]), (1, 0)
        )
        self.assertEqual(
            (real_record["canaries_planted"], real_record["canaries_caught"]), (0, 0)
        )
        expected_real_digest = advisory_consultation._default_task_id(task_description)
        self.assertEqual(real_record["task_id"], expected_real_digest)
        self.assertNotEqual(catch_record["task_id"], expected_real_digest)

    # --- AC6: content-free ---

    def test_no_substring_of_the_task_plan_or_critique_reaches_the_journal(self) -> None:
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
            journal_text = learning_journal.journal_path(root).read_text()

        for leak in (
            distinctive_task,
            "ZEBRA-QUASAR-77",
            "rollback",
            first_plan,
            second_plan,
        ):
            self.assertNotIn(leak, journal_text)

    def test_the_record_key_set_is_exactly_the_expected_set(self) -> None:
        """A leak added by a future field is a failure, not an invisible
        addition."""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            invoker = _RecordingInvoker(["Plan.", _approve("Plan.")])
            advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite", invoker, root_dir=root, task_id="dq-keyset-1"
            )
            record = next(
                r
                for r in _read_jsonl(learning_journal.journal_path(root))
                if r["kind"] == "dialogue_quality"
            )

        self.assertEqual(
            set(record),
            {
                "kind",
                "task_id",
                "sensitivity_halted",
                "occasion",
                "topology",
                "rounds",
                "canaries_planted",
                "canaries_caught",
                "degraded",
                "independent",
                "rounds_run",
                "timestamp",
            },
        )

    # --- AC7: a sensitivity-halted consultation writes no dialogue record ---

    def test_a_sensitivity_halted_consultation_writes_no_dialogue_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the rollout, password=hunter2", _RecordingInvoker([]), root_dir=root
            )
            telemetry_exists = (root / ".ralph" / "routing_telemetry.jsonl").exists()
            journal_exists = learning_journal.journal_path(root).exists()

        self.assertEqual(result.outcome, "sensitivity_halt")
        self.assertTrue(telemetry_exists)
        self.assertFalse(journal_exists)

    def test_a_halt_from_an_exhausted_local_roster_writes_no_dialogue_record(self) -> None:
        """The second halt exit (`reachability_check` supplied but reporting
        only cloud families up) — the carve-out proven where it is claimed,
        for both of `_result`'s two `sensitivity_halt` call sites."""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            result = advisory_consultation.run_advisory_consultation_debate(
                "Rotate the api_key before the review lands",
                _RecordingInvoker([]),
                root_dir=root,
                reachability_check=_reachable("claude", "codex-gpt", "gemini"),
            )
            journal_exists = learning_journal.journal_path(root).exists()

        self.assertEqual(result.outcome, "sensitivity_halt")
        self.assertFalse(journal_exists)

    # --- AC8: the scoreboard's data half ---

    def test_a_real_consultation_leaves_dialogue_quality_data_in_the_journal(self) -> None:
        """The buildable half of AC8 — the scoreboard itself is ticket 16 and
        does not exist yet."""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            invoker = _RecordingInvoker(["Plan.", _approve("Plan.")])
            advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite", invoker, root_dir=root, task_id="dq-scoreboard-1"
            )
            records = _read_jsonl(learning_journal.journal_path(root))

        dialogue_records = [r for r in records if r["kind"] == "dialogue_quality"]
        self.assertGreaterEqual(len(dialogue_records), 1)
        for record in dialogue_records:
            self.assertTrue(record["rounds"])
            for round_entry in record["rounds"]:
                self.assertIsInstance(round_entry["engagement_count"], int)

    # --- pinned decisions with no direct acceptance criterion ---

    def test_a_budget_skipped_dialogue_writes_a_zero_round_record(self) -> None:
        cap = advisory_consultation.DEFAULT_SESSION_DIALOGUE_CAP
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite",
                _RecordingInvoker([]),
                root_dir=root,
                session_spend_so_far=3 * cap,
                task_id="dq-rung3-1",
            )
            records = _read_jsonl(learning_journal.journal_path(root))

        self.assertEqual(result.outcome, "budget_skipped")
        dialogue_records = [r for r in records if r["kind"] == "dialogue_quality"]
        self.assertEqual(len(dialogue_records), 1)
        self.assertEqual(dialogue_records[0]["rounds"], [])
        self.assertEqual(dialogue_records[0]["rounds_run"], 0)
        self.assertTrue(dialogue_records[0]["degraded"])

    def test_a_worker_error_before_any_round_writes_a_zero_round_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            invoker = _RecordingInvoker([RuntimeError("planner unreachable")])
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite",
                invoker,
                root_dir=root,
                task_id="dq-worker-error-1",
            )
            records = _read_jsonl(learning_journal.journal_path(root))

        self.assertEqual(result.outcome, "worker_error")
        dialogue_records = [r for r in records if r["kind"] == "dialogue_quality"]
        self.assertEqual(len(dialogue_records), 1)
        self.assertEqual(dialogue_records[0]["rounds"], [])

    def test_a_journal_the_writer_cannot_reach_degrades_the_run_and_never_fails_it(
        self,
    ) -> None:
        """Instrumentation never breaks what it observes: an unwritable
        `.ralph` (pre-created as a file, the shape
        `LearningJournalTests.test_write_failure_is_reported_to_the_caller_and_never_raised`
        uses) still lets the dialogue itself reach consensus; only the
        record of it degrades, named in `result.error`."""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            learning_journal.journal_path(root).parent.write_text("not a directory")
            invoker = _RecordingInvoker(["Plan.", _approve("Plan.")])
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite",
                invoker,
                root_dir=root,
                task_id="dq-unwritable-1",
            )

        self.assertEqual(result.outcome, "consensus")
        self.assertEqual(result.final_plan, "Plan.")
        assert result.error is not None
        self.assertIn("learning journal", result.error.lower())

    def test_the_dispatch_crash_net_writes_no_dialogue_quality_record(self) -> None:
        """§2.3: the dispatch thread's own last-resort exception net
        (`_run_dispatched_post_mortem`'s `except`) writes a telemetry record
        but deliberately no dialogue-quality record — its synthesized result
        is a guess about a dialogue whose real state is unknown, and the
        inner `try` already wraps `_result`, so writing one here risks a
        second record for one dialogue."""
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
                task_id="dq-dispatch-crash-1",
            )
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive(), "background thread leaked past the test")
            telemetry_records = _read_jsonl(root / ".ralph" / "routing_telemetry.jsonl")
            journal_exists = learning_journal.journal_path(root).exists()

        self.assertEqual(len(telemetry_records), 1)
        self.assertEqual(telemetry_records[0]["outcome"], "worker_error")
        self.assertFalse(journal_exists)

    def test_critic_verdict_and_round_verdict_vocabularies_agree(self) -> None:
        """The third cross-spec vocabulary pin (`ERRORS.md`, "pin the
        agreement with an explicit equality test the moment the second
        vocabulary is declared"), now load-bearing: `_reduce_dialogue_round`
        returns a bare string that must be a valid `learning_journal.RoundVerdict`
        for `DialogueRound.__post_init__` to accept it."""
        self.assertEqual(
            set(get_args(advisory_consultation.CriticVerdict)),
            set(get_args(learning_journal.RoundVerdict)),
        )

    def test_an_unjournalable_task_id_writes_no_dialogue_record_and_says_so(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ), mock.patch.object(
            production_invoker,
            "invoke_worker",
            side_effect=["Plan.", _approve("Plan.")],
        ):
            root = Path(tmp)
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite", root_dir=root, task_id="not a valid task id"
            )
            journal_exists = learning_journal.journal_path(root).exists()

        self.assertEqual(result.outcome, "consensus")
        self.assertFalse(journal_exists)
        assert result.error is not None
        self.assertIn("journaling disabled", result.error)
        self.assertIn("dialogue-quality record", result.error)


# Spec 0004 ticket 25 — the outcome family's first in-process producer.
# Appended here, at the end of the file, per this file's own convention (see
# the comment above `JournalUnificationTests`): this file is edited
# concurrently on other branches, so an insertion anywhere but the end is a
# merge conflict for everyone.
class PlanOutcomeRecordWriterTests(unittest.TestCase):
    """`advisory_consultation._write_plan_outcome_record`: the `_result`
    choke point's fifth write, grading whether the consultation's own plan
    was accepted.

    Every test here drives a whole run through the public
    `run_advisory_consultation_debate` entry point rather than calling
    `learning_outcomes.record_plan_outcome` directly — ticket 25 exists
    precisely because a component fully tested in isolation (ticket 14) had
    no caller, and a direct test of the writer would repeat that gap instead
    of closing it.
    """

    # --- only a consensus reached on a plan-producing occasion states a
    # plan verdict ---

    def test_a_consensus_run_writes_plan_accepted(self) -> None:
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
                task_id="plan-outcome-consensus-1",
            )
            records = _read_jsonl(learning_journal.journal_path(root))

        self.assertEqual(result.outcome, "consensus")
        outcome_record = next(r for r in records if r["kind"] == "outcome")
        self.assertEqual(outcome_record["task_id"], "plan-outcome-consensus-1")
        self.assertEqual(outcome_record["ground_truth"], "plan")
        self.assertEqual(outcome_record["verdict"], "accepted")

    def test_a_stalemate_run_writes_no_plan_outcome_record(self) -> None:
        """A stalemate is deliberately silent, not a `rejected` verdict: one
        of the three ways a human resolves a stalemate
        (`learning_outcomes.record_stalemate_resolution`, option 1) is to
        approve the Planner's architecture — the exact plan a `rejected`
        record would already have condemned. Writing `rejected` here would
        let this choke point's record contradict the human's own later
        resolution, so it writes nothing and leaves the verdict to whichever
        of the three options the human actually picks."""
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
                task_id="plan-outcome-stalemate-1",
            )
            records = _read_jsonl(learning_journal.journal_path(root))

        self.assertEqual(result.outcome, "stalemate")
        self.assertEqual([r for r in records if r["kind"] == "outcome"], [])

    # --- code-review and post-mortem debate a diff or a lesson, never a
    # plan, so even a consensus under those occasions writes nothing ---

    def test_a_code_review_consensus_writes_no_plan_outcome_record(self) -> None:
        """`code-review` debates a diff, not a plan — `plan=accepted` about
        it would be a fact asserted about an artifact that was never on the
        table."""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            invoker = _RecordingInvoker(
                ["Diff defense.", _approve("Diff defense.")]
            )
            result = advisory_consultation.run_advisory_consultation_debate(
                "Review the diff",
                invoker,
                root_dir=root,
                occasion="code-review",
                task_id="plan-outcome-code-review-consensus-1",
            )
            records = _read_jsonl(learning_journal.journal_path(root))

        self.assertEqual(result.outcome, "consensus")
        self.assertEqual([r for r in records if r["kind"] == "outcome"], [])

    def test_a_post_mortem_consensus_writes_no_plan_outcome_record(self) -> None:
        """`post-mortem` debates a lesson, not a plan — same rule as
        `code-review`, for the same reason."""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            invoker = _RecordingInvoker(
                ["Lesson learned.", _approve("Lesson learned.")]
            )
            result = advisory_consultation.run_advisory_consultation_debate(
                "Write the post-mortem",
                invoker,
                root_dir=root,
                occasion="post-mortem",
                task_id="plan-outcome-post-mortem-consensus-1",
            )
            records = _read_jsonl(learning_journal.journal_path(root))

        self.assertEqual(result.outcome, "consensus")
        self.assertEqual([r for r in records if r["kind"] == "outcome"], [])

    def test_a_plan_review_consensus_writes_plan_accepted(self) -> None:
        """`plan-review` is the second plan-producing occasion alongside the
        default `ambiguity` already covered above — both debate a Planner's
        architecture, so both honestly support `plan=accepted`."""
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
                occasion="plan-review",
                task_id="plan-outcome-plan-review-consensus-1",
            )
            records = _read_jsonl(learning_journal.journal_path(root))

        self.assertEqual(result.outcome, "consensus")
        outcome_record = next(r for r in records if r["kind"] == "outcome")
        self.assertEqual(
            outcome_record["task_id"], "plan-outcome-plan-review-consensus-1"
        )
        self.assertEqual(outcome_record["ground_truth"], "plan")
        self.assertEqual(outcome_record["verdict"], "accepted")

    # --- the other five outcomes never know whether the plan was accepted ---

    def test_worker_error_writes_no_plan_outcome_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            invoker = _RecordingInvoker([RuntimeError("worker unreachable")])
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite",
                invoker,
                root_dir=root,
                task_id="plan-outcome-worker-error-1",
            )
            records = _read_jsonl(learning_journal.journal_path(root))

        self.assertEqual(result.outcome, "worker_error")
        self.assertEqual([r for r in records if r["kind"] == "outcome"], [])

    def test_unparseable_verdict_writes_no_plan_outcome_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            invoker = _RecordingInvoker(
                ["Planner's plan.", "This plan looks fine to me."]
            )
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite",
                invoker,
                root_dir=root,
                task_id="plan-outcome-unparseable-1",
            )
            records = _read_jsonl(learning_journal.journal_path(root))

        self.assertEqual(result.outcome, "unparseable_verdict")
        self.assertEqual([r for r in records if r["kind"] == "outcome"], [])

    def test_budget_skipped_writes_no_plan_outcome_record(self) -> None:
        """`budget_skipped` still writes a `dialogue_quality` record (ticket
        24 excludes only `sensitivity_halt`), so the journal file itself
        exists — the assertion here is that no `outcome` record joins it."""
        cap = advisory_consultation.DEFAULT_SESSION_DIALOGUE_CAP
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite",
                _RecordingInvoker([]),
                root_dir=root,
                task_id="plan-outcome-budget-skipped-1",
                session_spend_so_far=3 * cap,
            )
            records = _read_jsonl(learning_journal.journal_path(root))

        self.assertEqual(result.outcome, "budget_skipped")
        self.assertEqual([r for r in records if r["kind"] == "outcome"], [])

    def test_canary_writes_no_plan_outcome_record(self) -> None:
        invoker = _RecordingInvoker(
            [_revise("Missing lock around the write to the telemetry file.")]
        )
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite",
                invoker,
                root_dir=root,
                task_id="plan-outcome-canary-1",
                is_canary=True,
            )
            records = _read_jsonl(learning_journal.journal_path(root))

        self.assertEqual(result.outcome, "canary")
        self.assertEqual([r for r in records if r["kind"] == "outcome"], [])

    def test_sensitivity_halt_writes_no_outcome_record_of_any_kind(self) -> None:
        """Same carve-out shape as `dialogue_quality` (ticket 24): a halted
        task ran no round, so it carries no `plan` verdict either — ticket
        12's rule holds for every record kind, not only this one."""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the rollout, password=hunter2", _RecordingInvoker([]), root_dir=root
            )
            journal_exists = learning_journal.journal_path(root).exists()

        self.assertEqual(result.outcome, "sensitivity_halt")
        self.assertFalse(journal_exists)

    # --- the record carries the TaskIdentity and RunIdentity the decision itself carries ---

    def test_the_plan_outcome_record_shares_the_runs_worker_execution_run_id(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ), mock.patch.object(
            production_invoker,
            "invoke_worker",
            side_effect=[
                "Planner's proposed plan.",
                _approve("Planner's proposed plan."),
            ],
        ):
            root = Path(tmp)
            advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite",
                root_dir=root,
                planner_model="Claude Opus 5 (Thinking)",
                critic_model="Codex 5.6 Sol",
                task_id="plan-outcome-shared-run-1",
            )
            records = _read_jsonl(learning_journal.journal_path(root))

        worker_records = [r for r in records if r["kind"] == "worker_execution"]
        outcome_record = next(r for r in records if r["kind"] == "outcome")
        # Count first: an empty worker set makes the union one element and
        # silently leaves the correlation assertion untested.
        self.assertEqual(len(worker_records), 2, "one record per worker invocation")
        run_ids = {r["run_id"] for r in worker_records} | {outcome_record["run_id"]}
        self.assertEqual(len(run_ids), 1, "all records share one run_id")

    def test_a_caller_supplied_journaled_invoker_leaves_the_plan_outcome_record_run_free(
        self,
    ) -> None:
        """Same anti-rework-inflation rule ticket 24 already proved for the
        dialogue-quality record: when the caller owns the journal wiring,
        this module cannot see that factory's run identity, so it must not
        mint a second one for the plan-outcome record either."""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            runner = mock.Mock(
                side_effect=[
                    subprocess.CompletedProcess(
                        [], 0, "Planner's proposed plan.", ""
                    ),
                    subprocess.CompletedProcess(
                        [], 0, _approve("Planner's proposed plan."), ""
                    ),
                ]
            )
            journaled_invoker = production_invoker.make_journaled_invoke_worker(
                "plan-outcome-caller-run-1", root_dir=root, runner=runner
            )
            advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite",
                journaled_invoker,
                root_dir=root,
                planner_model="Claude Opus 5 (Thinking)",
                critic_model="Codex 5.6 Sol",
                task_id="plan-outcome-caller-run-1",
            )
            records = _read_jsonl(learning_journal.journal_path(root))

        outcome_record = next(r for r in records if r["kind"] == "outcome")
        self.assertNotIn("run_id", outcome_record)
        self.assertEqual(
            len(_countable_runs(records, "plan-outcome-caller-run-1")), 1
        )

    # --- ticket 27: multiple plan verdicts are formally reduced by file order ---

    @staticmethod
    def _reduce_plan_outcomes_by_position(
        outcomes: tuple[OutcomeRecord, ...],
    ) -> dict[tuple[str, str], OutcomeRecord]:
        """Apply Ticket 27's consumer rule to already-file-ordered records."""
        reduced: dict[tuple[str, str], OutcomeRecord] = {}
        for record in outcomes:
            if record.ground_truth == "plan":
                reduced[(record.task.task_id, record.ground_truth)] = record
        return reduced

    def test_manual_rejection_after_automatic_consensus_wins_positionally(self) -> None:
        """Ticket 25's automatic writer and the documented human writer share
        one task identity. Ticket 27 makes their append order the formal,
        deterministic resolution rather than adding provenance to the schema.
        """
        task_id = "plan-outcome-positional-resolution-1"
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite",
                _RecordingInvoker(["Planner's plan.", _approve("Planner's plan.")]),
                root_dir=root,
                task_id=task_id,
            )
            error = learning_outcomes.record_plan_outcome(
                task_id, accepted=False, root_dir=root
            )
            journal = learning_journal.read_journal(root)

        self.assertEqual(result.outcome, "consensus")
        self.assertIsNone(error)
        plan_outcomes = tuple(
            record for record in journal.outcomes if record.ground_truth == "plan"
        )
        self.assertEqual([record.verdict for record in plan_outcomes], ["accepted", "rejected"])
        reduced = self._reduce_plan_outcomes_by_position(journal.outcomes)
        self.assertEqual(reduced[(task_id, "plan")].verdict, "rejected")

    def test_a_single_plan_record_reduces_to_its_own_verdict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertIsNone(
                learning_outcomes.record_plan_outcome(
                    "plan-outcome-single-accepted-1", accepted=True, root_dir=root
                )
            )
            self.assertIsNone(
                learning_outcomes.record_plan_outcome(
                    "plan-outcome-single-rejected-1", accepted=False, root_dir=root
                )
            )
            reduced = self._reduce_plan_outcomes_by_position(
                learning_journal.read_journal(root).outcomes
            )

        self.assertEqual(
            reduced[("plan-outcome-single-accepted-1", "plan")].verdict, "accepted"
        )
        self.assertEqual(
            reduced[("plan-outcome-single-rejected-1", "plan")].verdict, "rejected"
        )

    # --- a write failure degrades the instrumentation, never the consultation ---

    def test_a_journal_write_failure_is_folded_into_the_result_error_and_never_raised(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            root = Path(tmp)
            learning_journal.journal_path(root).parent.write_text("not a directory")
            invoker = _RecordingInvoker(["Plan.", _approve("Plan.")])
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite",
                invoker,
                root_dir=root,
                task_id="plan-outcome-unwritable-1",
            )

        self.assertEqual(result.outcome, "consensus")
        self.assertEqual(result.final_plan, "Plan.")
        assert result.error is not None
        self.assertIn("learning journal", result.error.lower())

    def test_a_plan_outcome_write_failure_alone_is_folded_and_never_raised(self) -> None:
        """The test above blocks the whole journal directory, so ticket 24's
        `_write_dialogue_quality_record` fails first and its error alone
        satisfies the assertion — it would still pass if this ticket's writer
        dropped its own failure on the floor. This one fails nothing but the
        plan-outcome write, so the asserted error has exactly one possible
        producer. A green test over a path nobody exercises is the defect
        ticket 25 exists to end; the test proving ticket 25 must not be one.
        """
        refusal = "plan-outcome journal write refused by this test"
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {}, clear=True
        ), mock.patch.object(
            learning_outcomes, "record_plan_outcome", return_value=refusal
        ):
            root = Path(tmp)
            invoker = _RecordingInvoker(["Plan.", _approve("Plan.")])
            result = advisory_consultation.run_advisory_consultation_debate(
                "Plan the auth rewrite",
                invoker,
                root_dir=root,
                task_id="plan-outcome-write-refused-1",
            )

        self.assertEqual(result.outcome, "consensus")
        self.assertEqual(result.final_plan, "Plan.")
        assert result.error is not None
        self.assertIn(refusal, result.error)


if __name__ == "__main__":
    unittest.main()
