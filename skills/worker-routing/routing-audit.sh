#!/bin/bash
# Routing Protocol Audit Script v3.1
# Thin wrapper: locates the conversation log and delegates all parsing and
# metric computation to routing_check.py, then relays its exit code as-is.
#
# Usage: ./routing-audit.sh [conversation-id]
# If no ID given, scans the most recent conversation.
#
# Exit codes (relayed directly from routing_check.py):
#   0   Audit ran, no violations.
#   1   Audit ran, violations found.
#   2   The audit itself could not run — no conversations found, no log
#       file found for the conversation, or routing_check.py failed to
#       load/parse its config or the log. Fails closed rather than
#       silently treating the log as clean.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BRAIN_DIR="$HOME/.gemini/antigravity/brain"
PY_CHECK="$SCRIPT_DIR/routing_check.py"

CONV_ID="${1:-}"
if [ -z "$CONV_ID" ]; then
    # shellcheck disable=SC2012 # conversation IDs are simple directory names
    CONV_ID=$(ls -t "$BRAIN_DIR" 2>/dev/null | head -1) || true
fi

if [ -z "$CONV_ID" ]; then
    echo "❌ No conversations found under $BRAIN_DIR"
    exit 2
fi

LOG_DIR="$BRAIN_DIR/$CONV_ID/.system_generated/logs"

# Auto-detect which log format this conversation produced.
LOG_FILE=""
if [ -f "$LOG_DIR/overview.txt" ]; then
    LOG_FILE="$LOG_DIR/overview.txt"
elif [ -f "$LOG_DIR/transcript.jsonl" ]; then
    LOG_FILE="$LOG_DIR/transcript.jsonl"
fi

if [ -z "$LOG_FILE" ]; then
    echo "❌ No log found for conversation: $CONV_ID (looked for overview.txt, transcript.jsonl in $LOG_DIR)"
    exit 2
fi

echo "🔍 Auditing conversation: $CONV_ID"
echo "   Log file: $LOG_FILE"
echo "---"

set +e
python3 "$PY_CHECK" "$LOG_FILE"
STATUS=$?
set -e

exit "$STATUS"
