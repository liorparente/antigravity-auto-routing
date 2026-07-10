#!/bin/bash
# install.sh — installs the Auto Routing & Collaboration Protocol
# Idempotent: safe to run multiple times.
#
# Usage: ./install.sh [target_project_dir]
#   target_project_dir   Project to install the local skill copies and the
#                         generated AGENTS.md/CLAUDE.md into. Defaults to the
#                         current directory.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="$SCRIPT_DIR/skills/worker-routing"
PROTOCOL_SRC="$SRC_DIR/protocol.md"

TARGET_PROJECT_DIR="${1:-.}"
if [ ! -d "$TARGET_PROJECT_DIR" ]; then
    echo "❌ Target project directory does not exist: $TARGET_PROJECT_DIR"
    exit 1
fi
TARGET_PROJECT_DIR="$(cd "$TARGET_PROJECT_DIR" && pwd)"

TARGET_DIRS=(
    "$HOME/.gemini/config/skills/worker-routing"
    "$HOME/.codex/skills/worker-routing"
    "$TARGET_PROJECT_DIR/.agents/skills/worker-routing"
    "$TARGET_PROJECT_DIR/.codex/skills/worker-routing"
)
GEMINI_MD="$HOME/.gemini/GEMINI.md"
AGENTS_MD="$TARGET_PROJECT_DIR/AGENTS.md"
CLAUDE_MD="$TARGET_PROJECT_DIR/CLAUDE.md"

# Versionless sentinel markers — the protocol content between them can change
# across releases without ever needing a new marker string, so re-running the
# installer is always a clean "replace everything between these two lines".
PROTOCOL_START="# === ANTIGRAVITY WORKER ROUTING PROTOCOL START ==="
PROTOCOL_END="# === ANTIGRAVITY WORKER ROUTING PROTOCOL END ==="

# Legacy marker used before versionless markers existed (v3.0). That block was
# always appended as the final section of GEMINI.md with nothing after it, so
# "from this heading to end of file" reliably captures the whole thing.
LEGACY_MARKER="## Worker Routing Protocol (HARD ENFORCED — v3.0)"

echo "🚀 Installing Auto Routing & Collaboration Protocol"
echo "   Target project: $TARGET_PROJECT_DIR"
echo "---"

install_skill_files() {
    local target_dir="$1"

    if [ ! -d "$target_dir" ]; then
        echo "📁 Creating $target_dir"
        mkdir -p "$target_dir"
    else
        echo "📁 Target directory already exists: $target_dir"
    fi

    echo "📄 Copying skill files to $target_dir..."
    cp "$SRC_DIR/SKILL.md" "$target_dir/SKILL.md"
    cp "$SRC_DIR/routing-audit.sh" "$target_dir/routing-audit.sh"
    cp "$SRC_DIR/routing_check.py" "$target_dir/routing_check.py"
    cp "$SRC_DIR/protocol.md" "$target_dir/protocol.md"
    chmod +x "$target_dir/routing-audit.sh"
    echo "✅ Copied SKILL.md, routing-audit.sh, routing_check.py, protocol.md"

    if [ -f "$target_dir/routing-config.json" ]; then
        echo "⏭️  routing-config.json already exists in $target_dir — skipping copy to preserve customizations."
    else
        cp "$SRC_DIR/routing-config.json" "$target_dir/routing-config.json"
        echo "✅ Copied routing-config.json"
    fi
}

# Remove any existing protocol block from a doc, whether it was written with
# the new versionless markers or the legacy v3.0 heading. Leaves the rest of
# the file untouched. Returns non-zero (and leaves the file completely
# untouched) if PROTOCOL_START is present without a matching PROTOCOL_END —
# stripping a half-written block would silently discard everything after it.
strip_existing_block() {
    local file="$1"

    if grep -qF "$PROTOCOL_START" "$file" 2>/dev/null; then
        if ! grep -qF "$PROTOCOL_END" "$file" 2>/dev/null; then
            echo "⚠️  $file has $PROTOCOL_START but no matching $PROTOCOL_END — leaving it untouched." >&2
            return 1
        fi
        awk -v start="$PROTOCOL_START" -v end="$PROTOCOL_END" '
            $0 == start { skip=1; next }
            skip && $0 == end { skip=0; next }
            !skip { print }
        ' "$file" > "$file.tmp"
        mv "$file.tmp" "$file"
    elif grep -qF "$LEGACY_MARKER" "$file" 2>/dev/null; then
        awk -v marker="$LEGACY_MARKER" '
            $0 == marker { exit }
            { print }
        ' "$file" > "$file.tmp"
        mv "$file.tmp" "$file"
    fi
}

# Back up a file to "$file.bak" the first time we touch it. Once a backup
# exists we never overwrite it, so it always holds the user's pre-install
# original rather than a snapshot from a previous re-run. No-op for files
# that don't exist yet — there's nothing to back up.
backup_once() {
    local file="$1"
    if [ -f "$file" ] && [ ! -f "$file.bak" ]; then
        cp "$file" "$file.bak"
        echo "🗄️  Backed up $file to $file.bak"
    fi
}

# Inject/refresh the Worker Routing Protocol block between the sentinel
# markers in a doc, preserving any other custom content already in the
# file. Safe to re-run: a pre-existing block (versionless or legacy) is
# replaced in place rather than duplicated.
sync_protocol_doc() {
    local target_file="$1"

    mkdir -p "$(dirname "$target_file")"
    backup_once "$target_file"
    touch "$target_file"

    if grep -qF "$LEGACY_MARKER" "$target_file" 2>/dev/null; then
        echo "🔄 Legacy v3.0 protocol block detected in $(basename "$target_file") — upgrading to versionless markers."
    fi

    if ! strip_existing_block "$target_file"; then
        echo "⏭️  Skipping $target_file — resolve the unbalanced markers, then re-run install.sh." >&2
        return
    fi

    # Trim trailing blank lines left over after stripping so re-running the
    # installer doesn't accumulate blank lines before the re-injected block.
    while [ -s "$target_file" ] && [ -z "$(tail -n 1 "$target_file")" ]; do
        sed -i.tmp '$d' "$target_file"
        rm -f "$target_file.tmp"
    done

    echo "📝 Writing Worker Routing Protocol to $target_file"
    {
        echo ""
        echo "$PROTOCOL_START"
        echo ""
        cat "$PROTOCOL_SRC"
        echo ""
        echo "$PROTOCOL_END"
    } >> "$target_file"
    echo "✅ Synced $(basename "$target_file") from protocol.md"
}

# 1. Copy skill files to all supported global and local agent targets.
for target_dir in "${TARGET_DIRS[@]}"; do
    install_skill_files "$target_dir"
done

# 2. Inject/refresh the Worker Routing Protocol block in AGENTS.md and
#    CLAUDE.md at the project root, and in GEMINI.md (Antigravity's global
#    instruction file) — preserving any other custom content already there.
sync_protocol_doc "$AGENTS_MD"
sync_protocol_doc "$CLAUDE_MD"
sync_protocol_doc "$GEMINI_MD"

echo "---"
echo "🎉 Installation complete."
echo "   Skill files installed to:"
for target_dir in "${TARGET_DIRS[@]}"; do
    echo "     - $target_dir"
done
echo "   Project docs:  $AGENTS_MD, $CLAUDE_MD"
echo "   Protocol doc:  $GEMINI_MD (backup: $GEMINI_MD.bak)"
echo "   Run audits with: ${TARGET_DIRS[0]}/routing-audit.sh [conversation-id]"
