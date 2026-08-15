#!/bin/bash
# install.sh — atomically installs the Auto Routing & Collaboration Protocol.
# Usage: ./install.sh [target_project_dir]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="$SCRIPT_DIR/skills/worker-routing"
PROTOCOL_SRC="$SRC_DIR/protocol.md"
TARGET_PROJECT_DIR="${1:-.}"

if [ ! -d "$TARGET_PROJECT_DIR" ] || [ ! -r "$PROTOCOL_SRC" ]; then
    echo "❌ Target project or protocol source is unavailable." >&2
    exit 1
fi
TARGET_PROJECT_DIR="$(cd "$TARGET_PROJECT_DIR" && pwd)"

TARGET_DIRS=(
    "$HOME/.gemini/config/skills/worker-routing"
    "$HOME/.codex/skills/worker-routing"
    "$TARGET_PROJECT_DIR/.agents/skills/worker-routing"
    "$TARGET_PROJECT_DIR/.agent/skills/worker-routing"
    "$TARGET_PROJECT_DIR/.codex/skills/worker-routing"
)
AGENTS_MD="$TARGET_PROJECT_DIR/AGENTS.md"
CLAUDE_MD="$TARGET_PROJECT_DIR/CLAUDE.md"
GEMINI_MD="$HOME/.gemini/GEMINI.md"
CLAUDE_RULE="$TARGET_PROJECT_DIR/.claude/rules/worker-routing.md"
PROTOCOL_START="# === ANTIGRAVITY WORKER ROUTING PROTOCOL START ==="
PROTOCOL_END="# === ANTIGRAVITY WORKER ROUTING PROTOCOL END ==="
LEGACY_MARKER="## Worker Routing Protocol (HARD ENFORCED — v3.0)"
# Every artifact propagated to an installed harness.  A managed module that
# imports an unmanaged sibling is a broken installation, not a lint nit: the
# import fails only on installed copies, never in a dev checkout, so nothing
# short of an installation reproduces it.  `test_routing.py`'s
# `ManagedFileClosureTests` parses this array and asserts the set is closed
# under sibling imports, so the next module a ticket adds cannot be forgotten
# here the way `learning_journal.py` and `learning_outcomes.py` were.
# Closure alone still missed a module nothing imported yet — `learning_report.py`
# and then `acceptance_gate.py`, each shipped a ticket ahead of its caller — so
# the same test now also requires every non-test `.py` in the skill directory to
# appear below, whether or not a sibling already imports it.
MANAGED_FILES=(SKILL.md REFERENCE.md routing-audit.sh routing_check.py agent_council.py advisory_consultation.py production_invoker.py learning_journal.py learning_outcomes.py learning_scoreboard.py learning_report.py acceptance_gate.py learned_state.py protocol.md)

STAGING_DIR="$(mktemp -d "${TMPDIR:-/tmp}/auto-routing-stage.XXXXXX")"
TRANSACTION_DIR="$(mktemp -d "${TMPDIR:-/tmp}/auto-routing-rollback.XXXXXX")"
touch "$TRANSACTION_DIR/entries"
COMMITTED=0

rollback() {
    [ -s "$TRANSACTION_DIR/entries" ] || return 0
    echo "↩️  Rolling back incomplete installation..." >&2
    while IFS='|' read -r number state target; do
        [ -n "$target" ] || continue
        if [ "$state" = "present" ]; then
            mkdir -p "$(dirname "$target")"
            cp -p "$TRANSACTION_DIR/$number" "$target"
        else
            rm -f "$target"
        fi
    done < "$TRANSACTION_DIR/entries"
}

cleanup() {
    status=$?
    if [ "$status" -ne 0 ] && [ "$COMMITTED" -ne 1 ]; then
        rollback || true
        echo "❌ Installation failed; original files were restored." >&2
    fi
    rm -rf "$STAGING_DIR" "$TRANSACTION_DIR"
}
trap cleanup EXIT

snapshot_file() {
    local target="$1" number
    if grep -Fqx "$target" "$TRANSACTION_DIR/paths" 2>/dev/null; then
        return
    fi
    touch "$TRANSACTION_DIR/paths"
    number="$(wc -l < "$TRANSACTION_DIR/entries" | tr -d ' ')"
    if [ -e "$target" ]; then
        cp -p "$target" "$TRANSACTION_DIR/$number"
        echo "$number|present|$target" >> "$TRANSACTION_DIR/entries"
    else
        echo "$number|absent|$target" >> "$TRANSACTION_DIR/entries"
    fi
    echo "$target" >> "$TRANSACTION_DIR/paths"
}

atomic_copy() {
    local source="$1" target="$2" temporary
    snapshot_file "$target"
    mkdir -p "$(dirname "$target")"
    temporary="${target}.auto-routing-tmp.$$"
    cp "$source" "$temporary"
    mv -f "$temporary" "$target"
}

backup_once() {
    local target="$1"
    if [ -f "$target" ] && [ ! -f "$target.bak" ]; then
        atomic_copy "$target" "$target.bak"
        echo "🗄️  Backed up $target to $target.bak"
    fi
}

# Render a protocol document in staging.  It never mutates the source file,
# which makes marker validation a true preflight operation.
stage_protocol_doc() {
    local source="$1" output="$2"
    mkdir -p "$(dirname "$output")"
    if [ -f "$source" ]; then
        if grep -qF "$PROTOCOL_START" "$source" && ! grep -qF "$PROTOCOL_END" "$source"; then
            echo "⚠️  $source has $PROTOCOL_START but no matching $PROTOCOL_END — leaving it untouched." >&2
            return 2
        fi
        if grep -qF "$PROTOCOL_END" "$source" && ! grep -qF "$PROTOCOL_START" "$source"; then
            echo "❌ $source has an unmatched $PROTOCOL_END." >&2
            return 1
        fi
        if grep -qF "$PROTOCOL_START" "$source"; then
            awk -v start="$PROTOCOL_START" -v end="$PROTOCOL_END" '
                $0 == start { skip=1; next }
                skip && $0 == end { skip=0; next }
                !skip { print }
            ' "$source" > "$output"
        elif grep -qF "$LEGACY_MARKER" "$source"; then
            awk -v marker="$LEGACY_MARKER" '$0 == marker { exit } { print }' "$source" > "$output"
        else
            cp "$source" "$output"
        fi
    else
        : > "$output"
    fi
    # Do not accumulate blank separators on repeated installs.
    while [ -s "$output" ] && [ -z "$(tail -n 1 "$output")" ]; do
        sed -i.tmp '$d' "$output"
        rm -f "$output.tmp"
    done
    {
        echo ""
        echo "$PROTOCOL_START"
        echo ""
        cat "$PROTOCOL_SRC"
        echo ""
        echo "$PROTOCOL_END"
    } >> "$output"
}

echo "🚀 Installing Auto Routing & Collaboration Protocol"
echo "   Target project: $TARGET_PROJECT_DIR"
echo "📦 Preflighting and staging installation..."

# Stage all source-controlled artifacts before touching an installation target.
mkdir -p "$STAGING_DIR/files"
for file in "${MANAGED_FILES[@]}"; do
    [ -r "$SRC_DIR/$file" ] || { echo "❌ Missing required source: $file" >&2; exit 1; }
    cp "$SRC_DIR/$file" "$STAGING_DIR/files/$file"
done

DOCS=("$AGENTS_MD" "$CLAUDE_MD" "$GEMINI_MD")
STAGED_DOCS=()
for index in "${!DOCS[@]}"; do
    staged="$STAGING_DIR/docs/$index"
    stage_status=0
    stage_protocol_doc "${DOCS[$index]}" "$staged" || stage_status=$?
    if [ "$stage_status" -ne 0 ]; then
        # A malformed sentinel block makes single-source synchronization
        # ambiguous.  Abort before the first target mutation.
        exit "$stage_status"
    fi
    STAGED_DOCS[index]="$staged"
done
cp "$PROTOCOL_SRC" "$STAGING_DIR/claude-rule.md"

# Test-only fault injection verifies that preflight has no side effects.
if [ "${AUTO_ROUTING_FAIL_AFTER_STAGE:-0}" = "1" ]; then
    echo "🧪 Test hook AUTO_ROUTING_FAIL_AFTER_STAGE=1 triggered — aborting install before target mutation." >&2
    exit 1
fi

write_count=0
for target_dir in "${TARGET_DIRS[@]}"; do
    for file in "${MANAGED_FILES[@]}"; do
        atomic_copy "$STAGING_DIR/files/$file" "$target_dir/$file"
        write_count=$((write_count + 1))
        if [ "${AUTO_ROUTING_FAIL_AFTER_WRITES:-0}" = "$write_count" ]; then
            echo "🧪 Test hook AUTO_ROUTING_FAIL_AFTER_WRITES triggered." >&2
            exit 1
        fi
    done
    # Preserve customized routing configuration; install only the default.
    if [ ! -f "$target_dir/routing-config.json" ]; then
        atomic_copy "$SRC_DIR/routing-config.json" "$target_dir/routing-config.json"
    fi
    chmod +x "$target_dir/routing-audit.sh" "$target_dir/agent_council.py"
done

atomic_copy "$STAGING_DIR/claude-rule.md" "$CLAUDE_RULE"
for index in "${!DOCS[@]}"; do
    [ -n "${STAGED_DOCS[$index]}" ] || continue
    backup_once "${DOCS[$index]}"
    atomic_copy "${STAGED_DOCS[$index]}" "${DOCS[$index]}"
done

COMMITTED=1
echo "🎉 Installation complete."
echo "   Synchronized protocol source: $AGENTS_MD, $CLAUDE_MD, $GEMINI_MD"
