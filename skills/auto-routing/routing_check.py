#!/usr/bin/env python3
"""
routing_check.py — dynamic worker-pattern support for the routing audit.

Modes:
  --regex             Build a regex alternation from all worker patterns in
                       routing-config.json and print it to stdout (for the
                       bash audit script to grep with).
  --extensions-regex   Print a regex alternation of the configured
                       `code_extensions` to stdout.
  <log_file>           Detect [ROUTING: Direct] steps followed by a source
                       code edit that doesn't reference any known worker CLI
                       pattern. Prints violation count to stdout, details to
                       stderr.

Exit codes:
  0   Ran successfully (see stdout for the result).
  2   Failed to load/parse routing-config.json — fails closed rather than
      silently treating every log as clean.
"""
import sys
import re
import json
import traceback
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = SCRIPT_DIR / "routing-config.json"

DEFAULT_CODE_EXTENSIONS = ["ts", "tsx", "css", "js", "jsx"]


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


def check_log(config, log_file):
    patterns = load_patterns(config)
    extensions = load_code_extensions(config)
    worker_pattern = re.compile(
        r"\b(?:" + "|".join(re.escape(p) for p in patterns) + r")\b"
    )

    try:
        with open(log_file, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        print(0)
        return

    code_ext = re.compile(
        r"TargetFile[^,]*\.(?:" + "|".join(re.escape(e) for e in extensions) + r")"
    )
    direct_pattern = re.compile(r"ROUTING:\s*Direct", re.IGNORECASE)

    violations = 0

    for i, line in enumerate(lines):
        if direct_pattern.search(line):
            # Check the current line and the next 2 lines for a code edit
            window = "".join(lines[i : i + 3])
            if code_ext.search(window) and not worker_pattern.search(window):
                violations += 1
                # Extract the filenames for the detail message
                files_found = code_ext.findall(window)
                print(f"  ⚠️  Step {i+1}: [ROUTING: Direct] → code edit detected ({files_found})", file=sys.stderr)

    print(violations)


def main():
    try:
        config = load_config()
    except Exception:
        traceback.print_exc(file=sys.stderr)
        sys.exit(2)

    if len(sys.argv) < 2:
        print(0)
        sys.exit(0)

    if sys.argv[1] == "--regex":
        print_regex(config)
        sys.exit(0)

    if sys.argv[1] == "--extensions-regex":
        print_extensions_regex(config)
        sys.exit(0)

    check_log(config, sys.argv[1])
    sys.exit(0)


if __name__ == "__main__":
    main()
