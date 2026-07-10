#!/bin/bash
# uninstall.sh — removes the Auto Routing & Collaboration Protocol
# Reverses everything install.sh does: deletes the installed skill files
# (removing the containing directory only if that leaves it empty), strips
# the protocol block back out of AGENTS.md, CLAUDE.md, and GEMINI.md
# (preserving any other custom content in each), and deletes AGENTS.md/
# CLAUDE.md entirely if nothing but the block was ever there.
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

# Note: intentionally excludes "$TARGET_PROJECT_DIR/.agents/skills/worker-routing".
# install.sh writes there too, but .agents/ is a shared convention directory
# other tools may also populate — uninstall.sh leaves it alone so it never
# has to guess whether it's safe to touch.
TARGET_DIRS=(
    "$HOME/.gemini/config/skills/worker-routing"
    "$HOME/.codex/skills/worker-routing"
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

# 1. Remove only the specific files install.sh copied into each skill
#    directory, then remove the directory itself if that leaves it empty.
#    Any other content a user placed there is left untouched.
for target_dir in "${TARGET_DIRS[@]}"; do
    if [ -d "$target_dir" ]; then
        rm -f "$target_dir/SKILL.md" "$target_dir/routing-audit.sh" "$target_dir/routing_check.py" "$target_dir/routing-config.json" "$target_dir/protocol.md"
        rmdir "$target_dir" 2>/dev/null || true
        if [ -d "$target_dir" ]; then
            echo "✅ Removed skill files from $target_dir (other content preserved)"
        else
            echo "✅ Removed $target_dir"
        fi
    else
        echo "⏭️  $target_dir not found — skipping."
    fi
done

# Strip the protocol block out of a file in place (versionless or legacy
# marker), leaving any other custom content untouched.
strip_protocol_block() {
    local target_file="$1"

    if grep -qF "$PROTOCOL_START" "$target_file" 2>/dev/null; then
        awk -v start="$PROTOCOL_START" -v end="$PROTOCOL_END" '
            $0 == start { skip=1; next }
            skip && $0 == end { skip=0; next }
            !skip { print }
        ' "$target_file" > "$target_file.tmp"
        mv "$target_file.tmp" "$target_file"
    elif grep -qF "$LEGACY_MARKER" "$target_file" 2>/dev/null; then
        awk -v marker="$LEGACY_MARKER" '
            $0 == marker { exit }
            { print }
        ' "$target_file" > "$target_file.tmp"
        mv "$target_file.tmp" "$target_file"
    fi

    # Trim trailing blank lines left behind after stripping.
    while [ -s "$target_file" ] && [ -z "$(tail -n 1 "$target_file")" ]; do
        sed -i.tmp '$d' "$target_file"
        rm -f "$target_file.tmp"
    done
}

# 2. Strip the protocol block out of AGENTS.md / CLAUDE.md in place,
#    preserving any other custom content. If nothing but the block (and
#    surrounding blank lines) was ever there, the file was purely
#    generated, so remove it entirely.
remove_protocol_doc() {
    local target_file="$1"

    if [ ! -f "$target_file" ]; then
        echo "⏭️  $target_file not found — skipping."
        return
    fi

    if ! grep -qF -e "$PROTOCOL_START" -e "$LEGACY_MARKER" "$target_file" 2>/dev/null; then
        echo "⏭️  No Worker Routing Protocol block found in $target_file — skipping."
        return
    fi

    strip_protocol_block "$target_file"

    if [ ! -s "$target_file" ]; then
        rm -f "$target_file"
        echo "✅ Removed $target_file (no custom content remained)"
    else
        echo "✅ Removed Worker Routing Protocol block from $target_file (custom content preserved)"
    fi
}

remove_protocol_doc "$AGENTS_MD"
remove_protocol_doc "$CLAUDE_MD"

# 3. Strip the protocol block out of GEMINI.md, if present. GEMINI.md is
#    Antigravity's global instruction file, so it is never deleted outright
#    — only the block is removed, everything else is left untouched.
if [ -f "$GEMINI_MD" ] && grep -qF -e "$PROTOCOL_START" -e "$LEGACY_MARKER" "$GEMINI_MD" 2>/dev/null; then
    if [ ! -f "$GEMINI_MD.bak" ]; then
        cp "$GEMINI_MD" "$GEMINI_MD.bak"
        echo "🗄️  Backed up $GEMINI_MD to $GEMINI_MD.bak"
    fi

    strip_protocol_block "$GEMINI_MD"

    echo "✅ Removed Worker Routing Protocol block from $GEMINI_MD"
else
    echo "⏭️  No Worker Routing Protocol block found in $GEMINI_MD — skipping."
fi

echo "---"
echo "🎉 Uninstall complete."
