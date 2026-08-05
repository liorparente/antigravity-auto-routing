#!/bin/bash
# install.sh — atomically installs the Auto Routing & Collaboration Protocol.
# Usage: ./install.sh [target_project_dir]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WR_SRC_DIR="$SCRIPT_DIR/skills/worker-routing"
CR_SRC_DIR="$SCRIPT_DIR/skills/council-review"
ME_SRC_DIR="$SCRIPT_DIR/skills/model-evaluator"
PROTOCOL_SRC="$WR_SRC_DIR/protocol.md"
TARGET_PROJECT_DIR="${1:-.}"

if [ ! -d "$TARGET_PROJECT_DIR" ] || [ ! -r "$PROTOCOL_SRC" ]; then
    echo "❌ Target project or protocol source is unavailable." >&2
    exit 1
fi
TARGET_PROJECT_DIR="$(cd "$TARGET_PROJECT_DIR" && pwd)"

WR_TARGET_DIRS=(
    "$HOME/.gemini/config/skills/worker-routing"
    "$HOME/.codex/skills/worker-routing"
    "$TARGET_PROJECT_DIR/.agents/skills/worker-routing"
    "$TARGET_PROJECT_DIR/.agent/skills/worker-routing"
    "$TARGET_PROJECT_DIR/.codex/skills/worker-routing"
)
CR_TARGET_DIRS=(
    "$HOME/.gemini/config/skills/council-review"
    "$HOME/.codex/skills/council-review"
    "$TARGET_PROJECT_DIR/.agents/skills/council-review"
    "$TARGET_PROJECT_DIR/.agent/skills/council-review"
    "$TARGET_PROJECT_DIR/.codex/skills/council-review"
)
ME_TARGET_DIRS=(
    "$HOME/.gemini/config/skills/model-evaluator"
    "$HOME/.codex/skills/model-evaluator"
    "$TARGET_PROJECT_DIR/.agents/skills/model-evaluator"
    "$TARGET_PROJECT_DIR/.agent/skills/model-evaluator"
    "$TARGET_PROJECT_DIR/.codex/skills/model-evaluator"
)
AGENTS_MD="$TARGET_PROJECT_DIR/AGENTS.md"
CLAUDE_MD="$TARGET_PROJECT_DIR/CLAUDE.md"
GEMINI_MD="$HOME/.gemini/GEMINI.md"
CLAUDE_RULE="$TARGET_PROJECT_DIR/.claude/rules/worker-routing.md"
PROTOCOL_START="# === ANTIGRAVITY WORKER ROUTING PROTOCOL START ==="
PROTOCOL_END="# === ANTIGRAVITY WORKER ROUTING PROTOCOL END ==="
LEGACY_MARKER="## Worker Routing Protocol (HARD ENFORCED — v3.0)"
WR_MANAGED_FILES=(SKILL.md REFERENCE.md routing-audit.sh routing_check.py agent_council.py protocol.md)
CR_MANAGED_FILES=(SKILL.md agents/openai.yaml scripts/council_review.py scripts/provider_adapters.py references/council-manifest-v1.schema.json references/council-policy.json references/domain-decisions.md references/member-review.schema.json)
ME_MANAGED_FILES=(SKILL.md scripts/evaluator.py scripts/storage.py scripts/router_config_generator.py benchmarks/low.yaml)

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
mkdir -p "$STAGING_DIR/wr_files"
mkdir -p "$STAGING_DIR/cr_files"
mkdir -p "$STAGING_DIR/me_files"
for file in "${WR_MANAGED_FILES[@]}"; do
    [ -r "$WR_SRC_DIR/$file" ] || { echo "❌ Missing required source: $file" >&2; exit 1; }
    mkdir -p "$(dirname "$STAGING_DIR/wr_files/$file")"
    cp "$WR_SRC_DIR/$file" "$STAGING_DIR/wr_files/$file"
done
for file in "${CR_MANAGED_FILES[@]}"; do
    [ -r "$CR_SRC_DIR/$file" ] || { echo "❌ Missing required source: $file" >&2; exit 1; }
    mkdir -p "$(dirname "$STAGING_DIR/cr_files/$file")"
    cp "$CR_SRC_DIR/$file" "$STAGING_DIR/cr_files/$file"
done
for file in "${ME_MANAGED_FILES[@]}"; do
    [ -r "$ME_SRC_DIR/$file" ] || { echo "❌ Missing required source: $file" >&2; exit 1; }
    mkdir -p "$(dirname "$STAGING_DIR/me_files/$file")"
    cp "$ME_SRC_DIR/$file" "$STAGING_DIR/me_files/$file"
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
for target_dir in "${WR_TARGET_DIRS[@]}"; do
    for file in "${WR_MANAGED_FILES[@]}"; do
        atomic_copy "$STAGING_DIR/wr_files/$file" "$target_dir/$file"
        write_count=$((write_count + 1))
        if [ "${AUTO_ROUTING_FAIL_AFTER_WRITES:-0}" = "$write_count" ]; then
            echo "🧪 Test hook AUTO_ROUTING_FAIL_AFTER_WRITES triggered." >&2
            exit 1
        fi
    done
    # Preserve customized routing configuration; install only the default, or migrate existing
    if [ ! -f "$target_dir/routing-config.json" ]; then
        atomic_copy "$WR_SRC_DIR/routing-config.json" "$target_dir/routing-config.json"
    else
        # JSON Migration for council_runner audit entry
        tmp_config="$STAGING_DIR/routing-config-$$.json"
        if python3 -c '
import json, sys
try:
    with open(sys.argv[1], "r") as f:
        data = json.load(f)
    if "audit_patterns" not in data:
        data["audit_patterns"] = {}
    if "council_runner" not in data["audit_patterns"]:
        data["audit_patterns"]["council_runner"] = {"command": "council-review", "allow_sandbox": True}
    with open(sys.argv[2], "w") as f:
        json.dump(data, f, indent=2)
    sys.exit(0)
except Exception as e:
    sys.exit(1)
' "$target_dir/routing-config.json" "$tmp_config"; then
            atomic_copy "$tmp_config" "$target_dir/routing-config.json"
        fi
    fi
    chmod +x "$target_dir/routing-audit.sh" "$target_dir/agent_council.py"
done

for target_dir in "${CR_TARGET_DIRS[@]}"; do
    for file in "${CR_MANAGED_FILES[@]}"; do
        atomic_copy "$STAGING_DIR/cr_files/$file" "$target_dir/$file"
    done
done

for target_dir in "${ME_TARGET_DIRS[@]}"; do
    for file in "${ME_MANAGED_FILES[@]}"; do
        atomic_copy "$STAGING_DIR/me_files/$file" "$target_dir/$file"
    done
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
