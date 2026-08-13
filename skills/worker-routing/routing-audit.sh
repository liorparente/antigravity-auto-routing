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
# already resolved — never a synthetic one.
#
# The journal destination for that record is passed alongside it as
# --root-dir, and it is deliberately NOT derived from where this script
# lives. install.sh copies this file to five directories, two of them under
# $HOME ($HOME/.gemini/config/skills/worker-routing and
# $HOME/.codex/skills/worker-routing); a walk up from SCRIPT_DIR resolves to
# the repository only in a dev checkout and to $HOME/.gemini/config in an
# installed copy, which would put compliance records in a second journal
# while worker-execution and outcome records went on landing in the audited
# repository's .ralph/. One journal beside the routing telemetry is the
# invariant, so the destination comes from the repository the audit is being
# *run in* — the same place every other writer in this loop gets its root,
# and the same place .ralph/routing_telemetry.jsonl already is — with
# LEARNING_JOURNAL_ROOT as the explicit override.
#
# If neither yields a root (a project that is not a git repository, or this
# script invoked from outside one), --root-dir is omitted entirely and
# routing_check.py persists nothing. That is the documented handling of "no
# destination was resolved": the audit still runs and still prints and exits
# exactly as before. routing_check.py never invents a destination of its own
# — see its --root-dir docs — and neither does this script.
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
BRAIN_DIR="$HOME/.gemini/antigravity/brain"
PY_CHECK="$SCRIPT_DIR/routing_check.py"

JOURNAL_ROOT="${LEARNING_JOURNAL_ROOT:-}"
if [ -z "$JOURNAL_ROOT" ]; then
    JOURNAL_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || JOURNAL_ROOT=""
fi

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
PY_ARGS+=(--session-id "$CONV_ID")
if [ -n "$JOURNAL_ROOT" ]; then
    PY_ARGS+=(--root-dir "$JOURNAL_ROOT")
fi
PY_ARGS+=("$LOG_FILE")

set +e
python3 "$PY_CHECK" "${PY_ARGS[@]}"
STATUS=$?
set -e

exit "$STATUS"
