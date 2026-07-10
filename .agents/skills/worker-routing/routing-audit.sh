#!/bin/bash
# Routing Protocol Audit Script v2.2
# Scans conversation logs for routing protocol violations.
#
# Checks:
#   1. Source code edits with 0 worker calls (always a violation)
#   2. [ROUTING: Direct] declaration in/before a step that edits source code
#
# Usage: ./routing-audit.sh [conversation-id]
# If no ID given, scans the most recent conversation.
#
# Exit codes:
#   0   Audit ran and found no violations.
#   1   Violations detected, or the audit itself could not run (missing log,
#       missing conversation, or routing_check.py failed to load its config —
#       fails closed rather than silently treating the log as clean).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BRAIN_DIR="$HOME/.gemini/antigravity/brain"
PY_CHECK="$SCRIPT_DIR/routing_check.py"

CONV_ID="${1:-}"
if [ -z "$CONV_ID" ]; then
    CONV_ID=$(ls -t "$BRAIN_DIR" 2>/dev/null | head -1) || true
fi

if [ -z "$CONV_ID" ]; then
    echo "❌ No conversations found under $BRAIN_DIR"
    exit 1
fi

LOG_FILE="$BRAIN_DIR/$CONV_ID/.system_generated/logs/overview.txt"

if [ ! -f "$LOG_FILE" ]; then
    echo "❌ No log found for conversation: $CONV_ID"
    exit 1
fi

echo "🔍 Auditing conversation: $CONV_ID"
echo "---"

# Runs routing_check.py with the given args, storing its stdout/stderr in
# PY_OUT/PY_ERR. Fails closed: if routing_check.py exits non-zero (e.g. it
# couldn't load routing-config.json), abort the whole audit instead of
# silently falling back to a stale default.
run_py() {
    local err_file status
    err_file=$(mktemp)
    set +e
    PY_OUT=$(python3 "$PY_CHECK" "$@" 2>"$err_file")
    status=$?
    set -e
    PY_ERR=$(cat "$err_file")
    rm -f "$err_file"
    if [ "$status" -ne 0 ]; then
        echo "❌ routing_check.py $* failed (exit $status) — failing closed." >&2
        [ -n "$PY_ERR" ] && echo "$PY_ERR" >&2
        exit 1
    fi
}

# --- Metric 1: Total file-write tool calls ---
DIRECT_WRITES=$(grep -o '"write_to_file"\|"replace_file_content"\|"multi_replace_file_content"' "$LOG_FILE" 2>/dev/null | wc -l | xargs) || true
DIRECT_WRITES=${DIRECT_WRITES:-0}

# --- Dynamically load code extensions and worker CLI patterns ---
run_py --extensions-regex
EXT_REGEX="$PY_OUT"

run_py --regex
WORKER_REGEX="$PY_OUT"

CODE_EXT_PATTERN="TargetFile[^,]*\\.${EXT_REGEX}"

# --- Metric 2: Writes targeting source code files ---
CODE_WRITES=$(grep -oE "$CODE_EXT_PATTERN" "$LOG_FILE" 2>/dev/null | wc -l | xargs) || true
CODE_WRITES=${CODE_WRITES:-0}

# --- Metric 3: ROUTING declarations ---
ROUTING_DECLARATIONS=$(grep -o '\[ROUTING:' "$LOG_FILE" 2>/dev/null | wc -l | xargs) || true
ROUTING_DECLARATIONS=${ROUTING_DECLARATIONS:-0}

# --- Metric 4: Worker CLI calls ---
WORKER_CALLS=$(grep -oE "$WORKER_REGEX" "$LOG_FILE" 2>/dev/null | wc -l | xargs) || true
WORKER_CALLS=${WORKER_CALLS:-0}

# --- Metric 5: [ROUTING: Direct] followed by code edit ---
run_py "$LOG_FILE"
DIRECT_THEN_CODE="${PY_OUT:-0}"
DIRECT_THEN_CODE_DETAILS="$PY_ERR"

# --- Summary ---
echo "📊 Results:"
echo "  Total file write tool calls:     $DIRECT_WRITES"
echo "  Writes to source code files:     $CODE_WRITES"
echo "  ROUTING declarations found:      $ROUTING_DECLARATIONS"
echo "  Worker CLI calls found:          $WORKER_CALLS"
echo "  [Direct] → code edit violations: $DIRECT_THEN_CODE"
echo ""

# --- Violation checks ---
VIOLATION=false

if [ "$CODE_WRITES" -gt 0 ] && [ "$WORKER_CALLS" -eq 0 ]; then
    echo "🔴 VIOLATION: $CODE_WRITES source code edits with 0 worker calls."
    echo "   Antigravity executed code changes directly without routing."
    VIOLATION=true
fi

if [ "$DIRECT_THEN_CODE" -gt 0 ]; then
    echo "🔴 VIOLATION: [ROUTING: Direct] preceded a code edit $DIRECT_THEN_CODE time(s)."
    echo "   Direct routing is only allowed for .md edits, read-only ops, MCP calls, and QA."
    if [ -n "$DIRECT_THEN_CODE_DETAILS" ]; then
        echo "$DIRECT_THEN_CODE_DETAILS"
    fi
    VIOLATION=true
fi

if [ "$CODE_WRITES" -gt "$WORKER_CALLS" ] && [ "$VIOLATION" = false ]; then
    echo "🟡 WARNING: More code edits ($CODE_WRITES) than worker calls ($WORKER_CALLS)."
    echo "   Some edits may not have been properly routed."
elif [ "$ROUTING_DECLARATIONS" -eq 0 ] && [ "$DIRECT_WRITES" -gt 0 ] && [ "$VIOLATION" = false ]; then
    echo "🟡 WARNING: No [ROUTING:] declarations found, but $DIRECT_WRITES file writes occurred."
elif [ "$VIOLATION" = false ]; then
    echo "✅ No violations detected."
fi

echo ""
echo "--- Detailed source code edits ---"
grep -oE "$CODE_EXT_PATTERN" "$LOG_FILE" 2>/dev/null | sed 's/.*\///;s/\\"//' | sort | uniq -c | sort -rn || true

if [ "$VIOLATION" = true ]; then
    exit 1
fi

exit 0
