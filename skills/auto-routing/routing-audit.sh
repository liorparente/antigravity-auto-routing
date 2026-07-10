#!/bin/bash
# Routing Protocol Audit Script v2.1
# Scans conversation logs for routing protocol violations.
#
# Checks:
#   1. Source code edits with 0 worker calls (always a violation)
#   2. [ROUTING: Direct] declaration in/before a step that edits source code
#
# Usage: ./routing-audit.sh [conversation-id]
# If no ID given, scans the most recent conversation.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BRAIN_DIR="$HOME/.gemini/antigravity/brain"

if [ -n "$1" ]; then
    CONV_ID="$1"
else
    CONV_ID=$(ls -t "$BRAIN_DIR" 2>/dev/null | head -1)
fi

LOG_FILE="$BRAIN_DIR/$CONV_ID/.system_generated/logs/overview.txt"

if [ ! -f "$LOG_FILE" ]; then
    echo "❌ No log found for conversation: $CONV_ID"
    exit 1
fi

echo "🔍 Auditing conversation: $CONV_ID"
echo "---"

# --- Metric 1: Total file-write tool calls ---
DIRECT_WRITES=$(grep -o '"write_to_file"\|"replace_file_content"\|"multi_replace_file_content"' "$LOG_FILE" 2>/dev/null | wc -l | xargs)
DIRECT_WRITES=${DIRECT_WRITES:-0}

# --- Metric 2: Writes targeting source code files ---
CODE_WRITES=$(grep -oE 'TargetFile[^,]*\.(ts|tsx|css|js|jsx)' "$LOG_FILE" 2>/dev/null | wc -l | xargs)
CODE_WRITES=${CODE_WRITES:-0}

# --- Metric 3: ROUTING declarations ---
ROUTING_DECLARATIONS=$(grep -c '\[ROUTING:' "$LOG_FILE" 2>/dev/null | head -1 | xargs)
ROUTING_DECLARATIONS=${ROUTING_DECLARATIONS:-0}

# --- Metric 4: Worker CLI calls ---
WORKER_CALLS=$(grep -cE 'claude -p|codex |gemini -p|agy |127\.0\.0\.1:1234/v1/chat' "$LOG_FILE" 2>/dev/null | head -1 | xargs)
WORKER_CALLS=${WORKER_CALLS:-0}

# --- Metric 5: [ROUTING: Direct] followed by code edit (new check) ---
DIRECT_THEN_CODE_DETAILS=$(python3 "$SCRIPT_DIR/routing_check.py" "$LOG_FILE" 2>&1 1>/dev/null)
DIRECT_THEN_CODE=$(python3 "$SCRIPT_DIR/routing_check.py" "$LOG_FILE" 2>/dev/null)
DIRECT_THEN_CODE=${DIRECT_THEN_CODE:-0}

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
grep -oE 'TargetFile[^,]*\.(ts|tsx|css|js|jsx)' "$LOG_FILE" 2>/dev/null | sed 's/.*\///;s/\\"//' | sort | uniq -c | sort -rn
