#!/usr/bin/env python3
"""
routing_check.py — dynamic worker-pattern support for the routing audit.

Two modes:
  --regex        Build a regex alternation from all worker patterns in
                  routing-config.json and print it to stdout (for the
                  bash audit script to grep with).
  <log_file>      Detect [ROUTING: Direct] steps followed by a source code
                  edit that doesn't reference any known worker CLI pattern.
                  Prints violation count to stdout, details to stderr.
"""
import sys
import re
import json
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = SCRIPT_DIR / "routing-config.json"


def load_patterns():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = json.load(f)
    patterns = []
    for role in config.values():
        patterns.extend(role.get("patterns", []))
    return patterns


def print_regex():
    patterns = load_patterns()
    escaped = [re.escape(p) for p in patterns]
    print(f"({'|'.join(escaped)})")


def check_log(log_file):
    patterns = load_patterns()
    worker_pattern = re.compile("|".join(re.escape(p) for p in patterns))

    try:
        with open(log_file, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        print(0)
        return

    code_ext = re.compile(r"TargetFile[^,]*\.(ts|tsx|css|js|jsx)")
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
                exts = [m if isinstance(m, str) else m[0] for m in files_found]
                print(f"  ⚠️  Step {i+1}: [ROUTING: Direct] → code edit detected ({exts})", file=sys.stderr)

    print(violations)


def main():
    if len(sys.argv) < 2:
        print(0)
        return

    if sys.argv[1] == "--regex":
        print_regex()
        return

    check_log(sys.argv[1])


if __name__ == "__main__":
    main()
