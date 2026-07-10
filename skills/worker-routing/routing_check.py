#!/usr/bin/env python3
"""
routing_check.py — the routing audit engine.

Modes:
  --strict               Treat warnings as failures too (exit 1 instead of
                          0 when a 🟡 WARNING is emitted but no 🔴 VIOLATION
                          is found).
  <log_file>              Full audit: parses the log, computes every routing
                          metric strictly within each conversation step's own
                          boundaries, prints a human-readable report to
                          stdout, and exits with the audit verdict.
                          Per-violation detail lines go to stderr.

Log formats:
  *.txt / anything else   Plain text, split on `Step \\d+:` markers — unless
                           the stripped content starts with `{`, in which
                           case it's treated as JSON Lines regardless of
                           extension (Antigravity's own `overview.txt` logs
                           are written this way).
  *.jsonl                 JSON Lines: one step object per line.

  Each JSON Lines step object looks like:
    {"routing": "[ROUTING: Direct — reason: ...]",
     "tool_calls": [{"tool": "run_command", "command_line": "..."},
                    {"tool": "replace_file_content", "target_file": "..."}]}

  Antigravity's own conversation logs use a slightly different shape: the
  tool name is under `name` instead of `tool`, arguments are nested under
  `args` as `TargetFile`/`CommandLine` (sometimes wrapped in literal double
  quotes), and the `[ROUTING: ...]` declaration is embedded in a separate
  step's free-form `content` rather than a dedicated `routing` key.
  `_step_from_dict` accepts both shapes.

Worker-CLI detection only ever looks at the `CommandLine`/`command_line`
value of an actual `run_command` tool call — never at surrounding prose —
so a conversational mention of a worker's name can't be mistaken for routing.
`is_worker_invocation` strips leading environment variable assignments
(e.g. `IN_WORKER_ROUTING=true`) and known wrappers (`script -q /dev/null`,
`bash -c`) before checking whether the command *starts with* a configured
worker pattern — a substring mention mid-command (e.g. `echo codex exec`)
does not count.

Commands that are not worker invocations are checked against
`safe_commands` in routing-config.json via `is_command_safe`. A command
that is neither a worker invocation nor a recognized safe command is an
`unrouted_mutation` and flags its step as a violation even if it wrote no
code files directly (e.g. a redirect, backtick, or `$()` substitution that
could mutate state outside the tracked write tools).

Code-file detection uses `Path(filename).suffix` for an exact extension
match, so `.html` can't be mistaken for `.h`, `package.json` for `.js`, or
`.pyc` for `.py`.

A step that writes a source code file with zero worker CLI calls of its own
is a violation — regardless of what (if anything) its `[ROUTING:]` label
says. A `[ROUTING: heavy_doer ...]` label doesn't excuse an unrouted write
any more than `[ROUTING: Direct ...]` does; only an actual worker `run_command`
call in that same step does.

Exit codes:
  0   Audit ran, no violations (and, in --strict mode, no warnings either).
  1   Audit ran, violations found (or, in --strict mode, warnings found).
  2   The audit itself could not run — missing/unreadable log file, an
      empty log, a log that failed to parse or yielded no steps, a
      routing-config.json that failed to load, or a raw-text cross-check
      that suggests the parser is out of sync with the log format. Fails
      closed rather than silently treating an unreadable/unparseable log
      as clean.
"""
from __future__ import annotations

import json
import re
import sys
import traceback
from collections import Counter
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = SCRIPT_DIR / "routing-config.json"

DEFAULT_CODE_EXTENSIONS = ["ts", "tsx", "css", "js", "jsx"]

WRITE_TOOLS = {"write_to_file", "replace_file_content", "multi_replace_file_content"}

STEP_HEADER_RE = re.compile(r"^Step\s+\d+\s*:", re.MULTILINE)
ROUTING_RE = re.compile(r"\[ROUTING:[^\]\n]*\]")
TOOL_CALL_RE = re.compile(r"Tool call:\s*(\w+)\(")

# Non-role keys that may appear at the top level of routing-config.json
# alongside the worker-role dicts.
NON_ROLE_CONFIG_KEYS = {"code_extensions", "safe_commands", "orchestrator"}

ENV_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=\S+\s+")
SCRIPT_WRAPPER_RE = re.compile(r"^script\s+(?:-\S+\s+)*\S+\s+")
BASH_C_WRAPPER_RE = re.compile(r"^bash\s+-c\s+")

LOGICAL_SPLIT_RE = re.compile(r"\|\||&&|\||;")
UNSAFE_SUBSTRINGS = (">", "`", "$(")


def _kv_pattern(key: str) -> re.Pattern[str]:
    return re.compile(r'"' + key + r'"\s*:\s*"((?:[^"\\]|\\.)*)"')


TARGET_FILE_RE = _kv_pattern("TargetFile")
COMMAND_LINE_RE = _kv_pattern("CommandLine")


def load_config() -> dict[str, Any]:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)  # type: ignore[no-any-return]


def load_patterns(config: dict[str, Any]) -> list[str]:
    patterns: list[str] = []
    for key, role in config.items():
        if key in NON_ROLE_CONFIG_KEYS or not isinstance(role, dict):
            continue
        patterns.extend(role.get("patterns", []))
    return patterns


def load_code_extensions(config: dict[str, Any]) -> list[str]:
    return config.get("code_extensions", DEFAULT_CODE_EXTENSIONS)  # type: ignore[no-any-return]


def load_safe_patterns(config: dict[str, Any]) -> list[re.Pattern[str]]:
    return [re.compile(p) for p in config.get("safe_commands", [])]


def _strip_command_wrappers(command: str) -> str:
    """Strip leading environment variable assignments and known wrapper
    commands (`script -q /dev/null ...`, `bash -c ...`) so the underlying
    invocation is what gets matched against worker patterns."""
    stripped = command.strip()
    while True:
        without_env = ENV_ASSIGNMENT_RE.sub("", stripped)
        if without_env != stripped:
            stripped = without_env
            continue

        script_match = SCRIPT_WRAPPER_RE.match(stripped)
        if script_match:
            stripped = stripped[script_match.end():]
            continue

        bash_c_match = BASH_C_WRAPPER_RE.match(stripped)
        if bash_c_match:
            stripped = stripped[bash_c_match.end():].strip()
            if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in "\"'":
                stripped = stripped[1:-1]
            continue

        break

    return stripped.strip()


def is_worker_invocation(command: str, worker_patterns: list[str]) -> bool:
    """True if `command`, once stripped of leading env assignments and
    known wrappers, actually starts with a configured worker pattern — a
    substring mention elsewhere in the command (e.g. `echo codex exec`)
    does not count as delegation."""
    stripped = _strip_command_wrappers(command)
    return any(stripped.startswith(pattern) for pattern in worker_patterns)


def is_command_safe(command: str, safe_patterns: list[re.Pattern[str]]) -> bool:
    """True if `command` contains no redirect/substitution shell metacharacters
    and every `||`/`&&`/`|`/`;`-separated part matches a configured safe
    pattern."""
    if any(token in command for token in UNSAFE_SUBSTRINGS):
        return False

    parts = LOGICAL_SPLIT_RE.split(command)
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if not any(pattern.match(part) for pattern in safe_patterns):
            return False
    return True


class Step:
    """One logical unit of a conversation log: an optional [ROUTING: ...]
    declaration plus the tool calls made while that declaration was active.
    Metrics are computed strictly within a single Step — content from a
    neighboring step never bleeds in."""

    __slots__ = ("index", "routing", "writes", "commands")

    index: int
    routing: str | None
    writes: list[str]
    commands: list[str]

    def __init__(self, index: int, routing: str | None = None) -> None:
        self.index = index
        self.routing = routing
        self.writes = []  # TargetFile strings from write-tool calls
        self.commands = []  # CommandLine strings from run_command calls


def _parse_text_steps(text: str) -> list[Step]:
    """Plain-text logs: split on `Step \\d+:` markers. Each chunk is scanned
    only for its own [ROUTING:] declaration and its own tool calls."""
    headers = list(STEP_HEADER_RE.finditer(text))
    steps: list[Step] = []

    for i, header in enumerate(headers):
        start = header.start()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        chunk = text[start:end]

        routing_match = ROUTING_RE.search(chunk)
        step = Step(i + 1, routing_match.group(0) if routing_match else None)

        calls = list(TOOL_CALL_RE.finditer(chunk))
        for j, call in enumerate(calls):
            tool_name = call.group(1)
            seg_start = call.end()
            seg_end = calls[j + 1].start() if j + 1 < len(calls) else len(chunk)
            segment = chunk[seg_start:seg_end]

            if tool_name in WRITE_TOOLS:
                m = TARGET_FILE_RE.search(segment)
                if m:
                    step.writes.append(m.group(1))
            elif tool_name == "run_command":
                m = COMMAND_LINE_RE.search(segment)
                if m:
                    step.commands.append(m.group(1))

        steps.append(step)

    return steps


def _dig(data: dict[str, Any], *path: str) -> Any:
    """Walk a chain of dict keys, returning None as soon as one is missing
    or the value along the way isn't a dict."""
    value: Any = data
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _strip_quotes(value: Any) -> Any:
    return value.strip('"') if isinstance(value, str) else value


def _step_from_dict(index: int, data: dict[str, Any]) -> Step:
    routing = data.get("routing")
    if "routing" not in data:
        content = data.get("content")
        if isinstance(content, str):
            match = ROUTING_RE.search(content)
            routing = match.group(0) if match else None

    step = Step(index, routing)
    for call in data.get("tool_calls") or []:
        tool_name = call.get("tool") or call.get("name")
        target_file = _strip_quotes(call.get("target_file") or _dig(call, "args", "TargetFile"))
        command_line = _strip_quotes(call.get("command_line") or _dig(call, "args", "CommandLine"))

        if tool_name in WRITE_TOOLS and target_file:
            step.writes.append(target_file)
        elif tool_name == "run_command" and command_line:
            step.commands.append(command_line)
    return step


def _parse_jsonl_steps(text: str) -> list[Step]:
    """JSON Lines logs: one step object per line."""
    steps: list[Step] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        steps.append(_step_from_dict(len(steps) + 1, json.loads(line)))
    return steps


def parse_steps(log_file: str, text: str) -> list[Step]:
    if text.strip().startswith("{"):
        return _parse_jsonl_steps(text)
    if Path(log_file).suffix.lower() == ".jsonl":
        return _parse_jsonl_steps(text)
    return _parse_text_steps(text)


def compute_metrics(
    steps: list[Step],
    code_extensions: list[str],
    worker_patterns: list[str],
    safe_patterns: list[re.Pattern[str]],
) -> dict[str, Any]:
    code_ext_set = {e.lower().lstrip(".") for e in code_extensions}

    total_writes = 0
    code_writes = 0
    routing_declarations = 0
    worker_calls = 0
    code_write_files: list[str] = []
    violations: list[tuple[int, list[str]]] = []  # (step_index, [files])

    for step in steps:
        if step.routing:
            routing_declarations += 1

        step_worker_calls = 0
        step_has_unrouted_mutation = False
        for command in step.commands:
            if is_worker_invocation(command, worker_patterns):
                worker_calls += 1
                step_worker_calls += 1
            elif not is_command_safe(command, safe_patterns):
                step_has_unrouted_mutation = True

        step_code_writes: list[str] = []
        for target_file in step.writes:
            total_writes += 1
            suffix = Path(target_file).suffix.lower().lstrip(".")
            if suffix in code_ext_set:
                code_writes += 1
                code_write_files.append(target_file)
                step_code_writes.append(target_file)

        if step_has_unrouted_mutation or (step_code_writes and step_worker_calls == 0):
            violations.append((step.index, step_code_writes))

    return {
        "total_writes": total_writes,
        "code_writes": code_writes,
        "routing_declarations": routing_declarations,
        "worker_calls": worker_calls,
        "code_write_files": code_write_files,
        "violations": violations,
    }


def run_audit(config: dict[str, Any], log_file: str, strict: bool = False) -> int:
    worker_patterns = load_patterns(config)
    code_extensions = load_code_extensions(config)
    safe_patterns = load_safe_patterns(config)

    try:
        with open(log_file, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        print(f"❌ No log found: {log_file}")
        return 2

    if not text.strip():
        print(f"❌ Empty log: {log_file}")
        return 2

    try:
        steps = parse_steps(log_file, text)
    except Exception:
        traceback.print_exc(file=sys.stderr)
        print(f"❌ Failed to parse log: {log_file}")
        return 2

    if not steps:
        print(f"❌ No steps parsed from log: {log_file}")
        return 2

    metrics = compute_metrics(steps, code_extensions, worker_patterns, safe_patterns)

    raw_has_writes = any(t in text for t in WRITE_TOOLS)
    raw_has_routing = "[ROUTING:" in text
    if (raw_has_writes and metrics["total_writes"] == 0) or (
        raw_has_routing and metrics["routing_declarations"] == 0
    ):
        print("❌ Parser out of sync with log format.")
        return 2

    violation_count = len(metrics["violations"])

    print("📊 Results:")
    print(f"  {'Total file write tool calls:':<33} {metrics['total_writes']}")
    print(f"  {'Writes to source code files:':<33} {metrics['code_writes']}")
    print(f"  {'ROUTING declarations found:':<33} {metrics['routing_declarations']}")
    print(f"  {'Worker CLI calls found:':<33} {metrics['worker_calls']}")
    print(f"  {'Unrouted code edit violations:':<33} {violation_count}")
    print("")

    violation = False

    if metrics["code_writes"] > 0 and metrics["worker_calls"] == 0:
        print(f"🔴 VIOLATION: {metrics['code_writes']} source code edits with 0 worker calls.")
        print("   Antigravity executed code changes directly without routing.")
        violation = True

    if violation_count > 0:
        print(f"🔴 VIOLATION: Unrouted code edit detected in {violation_count} step(s).")
        print("   Every step that writes a source code file must also contain a worker CLI call,")
        print("   regardless of what its [ROUTING:] label says.")
        for step_index, files in metrics["violations"]:
            print(f"  ⚠️  Step {step_index}: unrouted code edit detected ({files})", file=sys.stderr)
        violation = True

    warning = False
    if metrics["code_writes"] > metrics["worker_calls"] and not violation:
        print(f"🟡 WARNING: More code edits ({metrics['code_writes']}) than worker calls ({metrics['worker_calls']}).")
        print("   Some edits may not have been properly routed.")
        warning = True
    elif metrics["routing_declarations"] == 0 and metrics["total_writes"] > 0 and not violation:
        print(f"🟡 WARNING: No [ROUTING:] declarations found, but {metrics['total_writes']} file writes occurred.")
        warning = True
    elif not violation:
        print("✅ No violations detected.")

    print("")
    print("--- Detailed source code edits ---")
    counts = Counter(Path(f).name for f in metrics["code_write_files"])
    for name, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"{count:>7} {name}")

    if violation:
        return 1
    if strict and warning:
        return 1
    return 0


def main() -> None:
    strict = "--strict" in sys.argv
    if strict:
        sys.argv.remove("--strict")

    try:
        config = load_config()
    except Exception:
        traceback.print_exc(file=sys.stderr)
        sys.exit(2)

    if len(sys.argv) < 2:
        print("Usage: routing_check.py [--strict] <log_file>", file=sys.stderr)
        sys.exit(2)

    sys.exit(run_audit(config, sys.argv[1], strict=strict))


if __name__ == "__main__":
    main()
