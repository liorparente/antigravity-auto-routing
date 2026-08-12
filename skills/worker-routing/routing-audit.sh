#!/bin/bash
# Routing Protocol Audit Script v3.2
# Thin wrapper: locates the conversation log and delegates all parsing and
# metric computation to routing_check.py, then relays its exit code as-is.
#
# Usage: ./routing-audit.sh [--strict] [conversation-id]
# If no ID given, scans the most recent conversation.
#   --strict   Relayed to routing_check.py: also fail (exit 1) on warnings,
#              not just violations.
#
# The resolved conversation id (given or scanned) is also passed to
# routing_check.py as --session-id, so it can persist the verdict as a
# ComplianceRecord (spec 0004 ticket 15) keyed on the same id this script
# already resolved — never a synthetic one. The journal destination for
# that record is passed alongside it as --root-dir: it defaults to this
# script's own repository (a fixed walk up from SCRIPT_DIR, since this
# script always lives at skills/worker-routing/ inside the repo it audits),
# and can be redirected independently by setting LEARNING_JOURNAL_ROOT.
# routing_check.py never invents a destination of its own — see its
# --root-dir docs. This must stay the repository, never $HOME: every other
# writer in this loop (learning_journal's own callers, AgentCouncil's
# routing_telemetry.jsonl) is repo-scoped, and a ComplianceRecord that
# landed anywhere else would split the journal in two — one file the
# ticket-16 scoreboard reads, one it doesn't.
#
# Exit codes (relayed directly from routing_check.py):
#   0   Audit ran, no violations (and, with --strict, no warnings).
#   1   Audit ran, violations found (or, with --strict, warnings found).
#   2   The audit itself could not run — no conversations found, no log
#       file found for the conversation, or routing_check.py failed to
#       load/parse its config or the log. Fails closed rather than
#       silently treating the log as clean.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
BRAIN_DIR="$HOME/.gemini/antigravity/brain"
JOURNAL_ROOT="${LEARNING_JOURNAL_ROOT:-$REPO_ROOT}"
PY_CHECK="$SCRIPT_DIR/routing_check.py"

STRICT_FLAG=""
CONV_ID=""
for arg in "$@"; do
    if [ "$arg" = "--strict" ]; then
        STRICT_FLAG="--strict"
    else
        CONV_ID="$arg"
    fi
done

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

PY_ARGS=()
if [ -n "$STRICT_FLAG" ]; then
    PY_ARGS+=(--strict)
fi
PY_ARGS+=(--session-id "$CONV_ID" --root-dir "$JOURNAL_ROOT" "$LOG_FILE")

set +e
python3 "$PY_CHECK" "${PY_ARGS[@]}"
STATUS=$?
set -e

exit "$STATUS"
