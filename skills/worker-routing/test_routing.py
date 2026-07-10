#!/usr/bin/env python3
"""Unit and integration tests for routing_check.py, routing-audit.sh, and
the install.sh / uninstall.sh protocol.md single-sourcing.

Run with:
    python3 -m unittest skills/worker-routing/test_routing.py -v
or, from this directory:
    python3 test_routing.py
"""
from __future__ import annotations

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
REPO_ROOT = SKILL_DIR.parent.parent
FIXTURES_DIR = SKILL_DIR / "tests" / "fixtures"
ROUTING_CHECK = SKILL_DIR / "routing_check.py"
ROUTING_AUDIT = SKILL_DIR / "routing-audit.sh"
PROTOCOL_MD = SKILL_DIR / "protocol.md"
INSTALL_SH = REPO_ROOT / "install.sh"
UNINSTALL_SH = REPO_ROOT / "uninstall.sh"

# Same versionless sentinel markers install.sh/uninstall.sh write/look for.
PROTOCOL_START = "# === ANTIGRAVITY WORKER ROUTING PROTOCOL START ==="
PROTOCOL_END = "# === ANTIGRAVITY WORKER ROUTING PROTOCOL END ==="

spec = importlib.util.spec_from_file_location("routing_check", ROUTING_CHECK)
assert spec is not None and spec.loader is not None
routing_check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(routing_check)


def run_check(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ROUTING_CHECK), *args],
        capture_output=True,
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
        self.brain_dir = Path.home() / ".gemini" / "antigravity" / "brain"
        self.conv_id = f"routing-audit-test-{os.getpid()}"
        self.conv_dir = self.brain_dir / self.conv_id
        self.log_dir = self.conv_dir / ".system_generated" / "logs"
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.conv_dir, ignore_errors=True)

    def _run_audit(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(ROUTING_AUDIT), *args, self.conv_id],
            capture_output=True,
            text=True,
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


class ProtocolSyncTests(unittest.TestCase):
    """Ensures install.sh/uninstall.sh single-source AGENTS.md and CLAUDE.md
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
            text=True,
            env=env,
        )

    def test_install_sh_injects_protocol_block_into_agents_and_claude(self) -> None:
        with tempfile.TemporaryDirectory() as fake_home, tempfile.TemporaryDirectory() as target_dir:
            result = self._run(INSTALL_SH, target_dir, home=fake_home)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            protocol_text = PROTOCOL_MD.read_text()
            for name in ("AGENTS.md", "CLAUDE.md"):
                doc = Path(target_dir) / name
                self.assertTrue(doc.exists())
                text = doc.read_text()
                self.assertIn(PROTOCOL_START, text)
                self.assertIn(PROTOCOL_END, text)
                self.assertIn(protocol_text, text)

    def test_install_sh_copies_protocol_md_to_skill_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as fake_home, tempfile.TemporaryDirectory() as target_dir:
            result = self._run(INSTALL_SH, target_dir, home=fake_home)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            protocol_text = PROTOCOL_MD.read_text()
            for installed_dir in (
                Path(fake_home) / ".gemini" / "config" / "skills" / "worker-routing",
                Path(fake_home) / ".codex" / "skills" / "worker-routing",
                Path(target_dir) / ".agents" / "skills" / "worker-routing",
                Path(target_dir) / ".codex" / "skills" / "worker-routing",
            ):
                installed_protocol = installed_dir / "protocol.md"
                self.assertTrue(installed_protocol.exists(), installed_protocol)
                self.assertEqual(installed_protocol.read_text(), protocol_text)

    def test_install_sh_leaves_unbalanced_markers_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as fake_home, tempfile.TemporaryDirectory() as target_dir:
            agents_md = Path(target_dir) / "AGENTS.md"
            original = f"pre-existing\n{PROTOCOL_START}\nsome content but no end marker\n"
            agents_md.write_text(original)

            result = self._run(INSTALL_SH, target_dir, home=fake_home)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(agents_md.read_text(), original)
            self.assertIn(PROTOCOL_START, result.stderr)
            self.assertIn("no matching", result.stderr)

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


if __name__ == "__main__":
    unittest.main()
