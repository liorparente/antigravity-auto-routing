#!/usr/bin/env python3
"""
routing_check.py — detects [ROUTING: Direct] steps followed by source code edits.
Called by routing-audit.sh. Prints violation count to stdout, details to stderr.
"""
import sys
import re

if len(sys.argv) < 2:
    print(0)
    sys.exit(0)

log_file = sys.argv[1]

try:
    with open(log_file, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
except OSError:
    print(0)
    sys.exit(0)

code_ext = re.compile(r"TargetFile[^,]*\.(ts|tsx|css|js|jsx)")
direct_pattern = re.compile(r"ROUTING:\s*Direct", re.IGNORECASE)

violations = 0

for i, line in enumerate(lines):
    if direct_pattern.search(line):
        # Check the current line and the next 2 lines for a code edit
        window = "".join(lines[i : i + 3])
        if code_ext.search(window):
            violations += 1
            # Extract the filenames for the detail message
            files_found = code_ext.findall(window)
            exts = [m if isinstance(m, str) else m[0] for m in files_found]
            print(f"  ⚠️  Step {i+1}: [ROUTING: Direct] → code edit detected ({exts})", file=sys.stderr)

print(violations)
