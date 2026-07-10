#!/usr/bin/env python3
"""
routing_check.py — the routing audit engine.

Modes:
  --regex              Build a regex alternation from all worker patterns in
                        routing-config.json and print it to stdout.
  --extensions-regex    Print a regex alternation of the configured
                        `code_extensions` to stdout.
  <log_file>            Full audit: parses the log (plain text, JSON, or
                        JSON Lines — detected from the file extension),
                        computes every routing metric strictly within each
                        conversation step's own boundaries, prints a
                        human-readable report to stdout, and exits with the
                        audit verdict. Per-violation detail lines go to
                        stderr.

Log formats:
  *.txt / anything else   Plain text, split on `Step \\d+:` markers.
  *.json                  A single JSON document: either a top-level array
                           of step objects, or an object with a `steps` array.
  *.jsonl                 JSON Lines: one step object per line.

  Each step object looks like:
    {"routing": "[ROUTING: Direct — reason: ...]",
     "tool_calls": [{"tool": "run_command", "command_line": "..."},
                    {"tool": "replace_file_content", "target_file": "..."}]}

Worker-CLI detection only ever looks at the `CommandLine`/`command_line`
value of an actual `run_command` tool call — never at surrounding prose —
so a conversational mention of a worker's name can't be mistaken for routing.
Code-file detection uses `Path(filename).suffix` for an exact extension
match, so `.html` can't be mistaken for `.h`, `package.json` for `.js`, or
`.pyc` for `.py`.

Exit codes:
  0   Audit ran, no violations.
  1   Audit ran, violations found.
  2   The audit itself could not run — missing/unreadable log file, a log
      that failed to parse, or a routing-config.json that failed to load.
      Fails closed rather than silently treating an unreadable log as clean.
"""
import json
import re
import sys
import traceback
from collections import Counter
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = SCRIPT_DIR / "routing-config.json"

DEFAULT_CODE_EXTENSIONS = ["ts", "tsx", "css", "js", "jsx"]

WRITE_TOOLS = {"write_to_file", "replace_file_content", "multi_replace_file_content"}

STEP_HEADER_RE = re.compile(r"^Step\s+\d+\s*:", re.MULTILINE)
ROUTING_RE = re.compile(r"\[ROUTING:[^\]\n]*\]")
DIRECT_ROUTING_RE = re.compile(r"\[ROUTING:\s*Direct\b", re.IGNORECASE)
TOOL_CALL_RE = re.compile(r"Tool call:\s*(\w+)\(")


def _kv_pattern(key):
    return re.compile(r'"' + key + r'"\s*:\s*"((?:[^"\\]|\\.)*)"')


TARGET_FILE_RE = _kv_pattern("TargetFile")
COMMAND_LINE_RE = _kv_pattern("CommandLine")


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def load_patterns(config):
    patterns = []
    for key, role in config.items():
        if key == "code_extensions" or not isinstance(role, dict):
            continue
        patterns.extend(role.get("patterns", []))
    return patterns


def load_code_extensions(config):
    return config.get("code_extensions", DEFAULT_CODE_EXTENSIONS)


def print_regex(config):
    patterns = load_patterns(config)
    escaped = [re.escape(p) for p in patterns]
    print(f"\\b({'|'.join(escaped)})\\b")


def print_extensions_regex(config):
    extensions = load_code_extensions(config)
    escaped = [re.escape(e) for e in extensions]
    print(f"({'|'.join(escaped)})")


class Step:
    """One logical unit of a conversation log: an optional [ROUTING: ...]
    declaration plus the tool calls made while that declaration was active.
    Metrics are computed strictly within a single Step — content from a
    neighboring step never bleeds in."""

    __slots__ = ("index", "routing", "writes", "commands")

    def __init__(self, index, routing=None):
        self.index = index
        self.routing = routing
        self.writes = []  # TargetFile strings from write-tool calls
        self.commands = []  # CommandLine strings from run_command calls

    @property
    def is_direct(self):
        return bool(self.routing) and bool(DIRECT_ROUTING_RE.search(self.routing))


def _parse_text_steps(text):
    """Plain-text logs: split on `Step \\d+:` markers. Each chunk is scanned
    only for its own [ROUTING:] declaration and its own tool calls."""
    headers = list(STEP_HEADER_RE.finditer(text))
    steps = []

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


def _step_from_dict(index, data):
    step = Step(index, data.get("routing"))
    for call in data.get("tool_calls") or []:
        tool_name = call.get("tool")
        if tool_name in WRITE_TOOLS and call.get("target_file"):
            step.writes.append(call["target_file"])
        elif tool_name == "run_command" and call.get("command_line"):
            step.commands.append(call["command_line"])
    return step


def _parse_json_steps(text):
    """JSON logs: a single document that is either a top-level array of step
    objects, or an object with a `steps` array."""
    data = json.loads(text) if text.strip() else {}
    if isinstance(data, dict):
        raw_steps = data.get("steps", [])
    elif isinstance(data, list):
        raw_steps = data
    else:
        raw_steps = []
    return [_step_from_dict(i + 1, s) for i, s in enumerate(raw_steps)]


def _parse_jsonl_steps(text):
    """JSON Lines logs: one step object per line."""
    steps = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        steps.append(_step_from_dict(len(steps) + 1, json.loads(line)))
    return steps


def parse_steps(log_file, text):
    suffix = Path(log_file).suffix.lower()
    if suffix == ".jsonl":
        return _parse_jsonl_steps(text)
    if suffix == ".json":
        return _parse_json_steps(text)
    return _parse_text_steps(text)


def compute_metrics(steps, code_extensions, worker_pattern):
    code_ext_set = {e.lower().lstrip(".") for e in code_extensions}

    total_writes = 0
    code_writes = 0
    routing_declarations = 0
    worker_calls = 0
    code_write_files = []
    violations = []  # list of (step_index, [files])

    for step in steps:
        if step.routing:
            routing_declarations += 1

        step_worker_calls = 0
        for command in step.commands:
            n = len(worker_pattern.findall(command))
            worker_calls += n
            step_worker_calls += n

        step_code_writes = []
        for target_file in step.writes:
            total_writes += 1
            suffix = Path(target_file).suffix.lower().lstrip(".")
            if suffix in code_ext_set:
                code_writes += 1
                code_write_files.append(target_file)
                step_code_writes.append(target_file)

        if step.is_direct and step_code_writes and step_worker_calls == 0:
            violations.append((step.index, step_code_writes))

    return {
        "total_writes": total_writes,
        "code_writes": code_writes,
        "routing_declarations": routing_declarations,
        "worker_calls": worker_calls,
        "code_write_files": code_write_files,
        "violations": violations,
    }


def run_audit(config, log_file):
    patterns = load_patterns(config)
    code_extensions = load_code_extensions(config)
    worker_pattern = re.compile(
        r"\b(?:" + "|".join(re.escape(p) for p in patterns) + r")\b"
    )

    try:
        with open(log_file, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        print(f"❌ No log found: {log_file}")
        return 2

    try:
        steps = parse_steps(log_file, text)
    except Exception:
        traceback.print_exc(file=sys.stderr)
        print(f"❌ Failed to parse log: {log_file}")
        return 2

    metrics = compute_metrics(steps, code_extensions, worker_pattern)
    violation_count = len(metrics["violations"])

    print("📊 Results:")
    print(f"  Total file write tool calls:     {metrics['total_writes']}")
    print(f"  Writes to source code files:     {metrics['code_writes']}")
    print(f"  ROUTING declarations found:      {metrics['routing_declarations']}")
    print(f"  Worker CLI calls found:          {metrics['worker_calls']}")
    print(f"  [Direct] → code edit violations: {violation_count}")
    print("")

    violation = False

    if metrics["code_writes"] > 0 and metrics["worker_calls"] == 0:
        print(f"🔴 VIOLATION: {metrics['code_writes']} source code edits with 0 worker calls.")
        print("   Antigravity executed code changes directly without routing.")
        violation = True

    if violation_count > 0:
        print(f"🔴 VIOLATION: [ROUTING: Direct] preceded a code edit {violation_count} time(s).")
        print("   Direct routing is only allowed for .md edits, read-only ops, MCP calls, and QA.")
        for step_index, files in metrics["violations"]:
            print(f"  ⚠️  Step {step_index}: [ROUTING: Direct] → code edit detected ({files})", file=sys.stderr)
        violation = True

    if metrics["code_writes"] > metrics["worker_calls"] and not violation:
        print(f"🟡 WARNING: More code edits ({metrics['code_writes']}) than worker calls ({metrics['worker_calls']}).")
        print("   Some edits may not have been properly routed.")
    elif metrics["routing_declarations"] == 0 and metrics["total_writes"] > 0 and not violation:
        print(f"🟡 WARNING: No [ROUTING:] declarations found, but {metrics['total_writes']} file writes occurred.")
    elif not violation:
        print("✅ No violations detected.")

    print("")
    print("--- Detailed source code edits ---")
    counts = Counter(Path(f).name for f in metrics["code_write_files"])
    for name, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"{count:>7} {name}")

    return 1 if violation else 0


def main():
    try:
        config = load_config()
    except Exception:
        traceback.print_exc(file=sys.stderr)
        sys.exit(2)

    if len(sys.argv) < 2:
        print("Usage: routing_check.py [--regex | --extensions-regex | <log_file>]", file=sys.stderr)
        sys.exit(2)

    if sys.argv[1] == "--regex":
        print_regex(config)
        sys.exit(0)

    if sys.argv[1] == "--extensions-regex":
        print_extensions_regex(config)
        sys.exit(0)

    sys.exit(run_audit(config, sys.argv[1]))


if __name__ == "__main__":
    main()
