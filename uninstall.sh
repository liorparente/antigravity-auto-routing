#!/bin/bash
# uninstall.sh — removes the Auto Routing & Collaboration Protocol
# Reverses everything install.sh does: deletes the installed skill
# directories, restores/removes the generated AGENTS.md and CLAUDE.md, and
# strips the protocol block back out of GEMINI.md.
#
# Usage: ./uninstall.sh [target_project_dir]
#   target_project_dir   Project the local skill copies and generated
#                         AGENTS.md/CLAUDE.md were installed into. Defaults
#                         to the current directory.
set -euo pipefail

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
    "$TARGET_PROJECT_DIR/.agent/skills/worker-routing"
    "$TARGET_PROJECT_DIR/.codex/skills/worker-routing"
)
GEMINI_MD="$HOME/.gemini/GEMINI.md"
AGENTS_MD="$TARGET_PROJECT_DIR/AGENTS.md"
CLAUDE_MD="$TARGET_PROJECT_DIR/CLAUDE.md"

# Same versionless sentinel markers install.sh writes/looks for.
PROTOCOL_START="# === ANTIGRAVITY WORKER ROUTING PROTOCOL START ==="
PROTOCOL_END="# === ANTIGRAVITY WORKER ROUTING PROTOCOL END ==="

# Legacy marker from v3.0 installs, before versionless markers existed. That
# block was always appended as the final section of GEMINI.md with nothing
# after it, so "from this heading to end of file" reliably captures it.
LEGACY_MARKER="## Worker Routing Protocol (HARD ENFORCED — v3.0)"

echo "🗑️  Uninstalling Auto Routing & Collaboration Protocol"
echo "   Target project: $TARGET_PROJECT_DIR"
echo "---"

# 1. Remove installed skill directories.
for target_dir in "${TARGET_DIRS[@]}"; do
    if [ -d "$target_dir" ]; then
        rm -rf "$target_dir"
        echo "✅ Removed $target_dir"
    else
        echo "⏭️  $target_dir not found — skipping."
    fi
done

# 2. Clean up the generated AGENTS.md / CLAUDE.md at the project root. If a
#    pre-install backup exists, restore it (undoing the sync in place);
#    otherwise the file was purely generated, so just delete it.
remove_protocol_doc() {
    local target_file="$1"
    if [ -f "$target_file.bak" ]; then
        mv "$target_file.bak" "$target_file"
        echo "✅ Restored $target_file from backup"
    elif [ -f "$target_file" ]; then
        rm -f "$target_file"
        echo "✅ Removed $target_file"
    else
        echo "⏭️  $target_file not found — skipping."
    fi
}

remove_protocol_doc "$AGENTS_MD"
remove_protocol_doc "$CLAUDE_MD"

# 3. Strip the protocol block out of GEMINI.md, if present.
if [ -f "$GEMINI_MD" ] && grep -qF -e "$PROTOCOL_START" -e "$LEGACY_MARKER" "$GEMINI_MD" 2>/dev/null; then
    if [ ! -f "$GEMINI_MD.bak" ]; then
        cp "$GEMINI_MD" "$GEMINI_MD.bak"
        echo "🗄️  Backed up $GEMINI_MD to $GEMINI_MD.bak"
    fi

    if grep -qF "$PROTOCOL_START" "$GEMINI_MD" 2>/dev/null; then
        awk -v start="$PROTOCOL_START" -v end="$PROTOCOL_END" '
            $0 == start { skip=1; next }
            skip && $0 == end { skip=0; next }
            !skip { print }
        ' "$GEMINI_MD" > "$GEMINI_MD.tmp"
        mv "$GEMINI_MD.tmp" "$GEMINI_MD"
    elif grep -qF "$LEGACY_MARKER" "$GEMINI_MD" 2>/dev/null; then
        awk -v marker="$LEGACY_MARKER" '
            $0 == marker { exit }
            { print }
        ' "$GEMINI_MD" > "$GEMINI_MD.tmp"
        mv "$GEMINI_MD.tmp" "$GEMINI_MD"
    fi

    # Trim trailing blank lines left behind after stripping.
    while [ -s "$GEMINI_MD" ] && [ -z "$(tail -n 1 "$GEMINI_MD")" ]; do
        sed -i.tmp '$d' "$GEMINI_MD"
        rm -f "$GEMINI_MD.tmp"
    done

    echo "✅ Removed Worker Routing Protocol block from $GEMINI_MD"
else
    echo "⏭️  No Worker Routing Protocol block found in $GEMINI_MD — skipping."
fi

echo "---"
echo "🎉 Uninstall complete."
