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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="$SCRIPT_DIR/skills/worker-routing"
COUNCIL_SRC_DIR="$SCRIPT_DIR/skills/council-review"

TARGET_PROJECT_DIR="${1:-.}"
if [ ! -d "$TARGET_PROJECT_DIR" ]; then
    echo "❌ Target project directory does not exist: $TARGET_PROJECT_DIR"
    exit 1
fi
TARGET_PROJECT_DIR="$(cd "$TARGET_PROJECT_DIR" && pwd)"

# Mirrors install.sh's TARGET_DIRS exactly — all 5 targets install.sh writes
# to are torn down here, including the project-local
# "$TARGET_PROJECT_DIR/.agents/skills/worker-routing" and
# "$TARGET_PROJECT_DIR/.agent/skills/worker-routing" convention directories,
# left out of this list before install.sh started propagating learned state
# into them (spec 0004 ticket 23/34). ".agents/" and ".agent/" are shared
# convention directories other tools may also populate, so this script never
# removes them outright — it only deletes the specific files it knows it
# installed (INSTALLED_FILES, learned-state/) and then removes
# "skills/worker-routing" and its "skills" parent with `rmdir`, which no-ops
# whenever anything else — worker-routing leftovers or another tool's files —
# still lives there. That parent-directory reclaim is scoped by index (see
# PROJECT_LOCAL_INDICES below) to the project-local targets (.agents/,
# .agent/, .codex/); it never ascends past the two home-directory targets
# ("$HOME/.gemini/config/skills/worker-routing",
# "$HOME/.codex/skills/worker-routing") into "$HOME/.gemini/config" or
# "$HOME/.codex", since install.sh did not create those solely to hold this
# skill — even when TARGET_PROJECT_DIR happens to equal $HOME, where a
# path-prefix check would otherwise treat the home targets as project-local
# too. Non-worker-routing content in ".agents/" or ".agent/" is therefore
# always preserved.
TARGET_DIRS=(
    "$HOME/.gemini/config/skills/worker-routing"
    "$HOME/.codex/skills/worker-routing"
    "$TARGET_PROJECT_DIR/.agents/skills/worker-routing"
    "$TARGET_PROJECT_DIR/.agent/skills/worker-routing"
    "$TARGET_PROJECT_DIR/.codex/skills/worker-routing"
)
# Indices into TARGET_DIRS that are project-local convention directories
# eligible for parent-directory reclamation. Indices 0 and 1 are the
# home-directory targets and must never ascend past their own skill
# directory, regardless of what TARGET_PROJECT_DIR resolves to.
PROJECT_LOCAL_INDICES=(2 3 4)
GEMINI_MD="$HOME/.gemini/GEMINI.md"
AGENTS_MD="$TARGET_PROJECT_DIR/AGENTS.md"
CLAUDE_MD="$TARGET_PROJECT_DIR/CLAUDE.md"

# Mirrors install.sh's MANAGED_FILES, plus the routing-config.json install.sh
# writes separately (it installs only the default and preserves a customized
# one, but it did put the file there, so uninstall removes it). Kept as an
# array rather than one long `rm -f` line so a module added to install.sh and
# forgotten here is visible; `test_routing.py`'s `ManagedFileClosureTests`
# additionally asserts this list covers every managed file, so the drift that
# left `learning_journal.py` behind cannot recur silently in either script.
INSTALLED_FILES=(
    SKILL.md REFERENCE.md routing-audit.sh __init__.py routing_check.py
    agent_council.py dialogue_contracts.py dialogue_degradation.py
    executive_dialogue_report.py dialogue_transcript.py prompt_assembler.py regenerate_institutional_memory.py
    sensitivity_redactor.py debate_orchestrator.py debate_state_machine.py
    consultation_policy.py debate_transport.py advisory_consultation.py
    production_invoker.py learning_journal.py learning_outcomes.py
    learning_scoreboard.py learning_report.py learning_report_html.py acceptance_gate.py
    learned_state.py risk_tiered_application.py learner_worker.py protocol.md
    routing-config.json routing_config.py probe_models.py
)

# Mirrors install.sh's own dynamic discovery of future production modules —
# every non-test `.py` file in THIS repo's own skills/worker-routing/ source
# directory is a module install.sh would have propagated, whether or not it
# already appears in the static list above. Discovering from this script's
# own source directory (never by globbing the target directory) is what
# keeps removal surgical: TARGET_DIRS's own comment documents ".agents/",
# ".agent/", and ".codex/" as convention directories other tools may also
# populate, so only files this installer is actually responsible for are
# ever removed from them.
if [ -d "$SRC_DIR" ]; then
    for python_module in "$SRC_DIR"/*.py; do
        [ -e "$python_module" ] || continue
        python_module_name="$(basename "$python_module")"
        [[ "$python_module_name" == test_* ]] && continue
        if [[ " ${INSTALLED_FILES[*]} " != *" $python_module_name "* ]]; then
            INSTALLED_FILES+=("$python_module_name")
        fi
    done
fi

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
#    Any other content a user placed there is left untouched. For the
#    project-local targets only (index in PROJECT_LOCAL_INDICES), also try
#    the "skills" parent and its own parent (e.g. ".agents/skills" then
#    ".agents") once the skill directory itself is gone — `rmdir` only
#    succeeds on a truly empty directory, so this reclaims convention
#    directories install.sh created purely to hold worker-routing without
#    ever deleting one that still holds another tool's content. Scoping by
#    index (rather than a "$target_dir" path-prefix check against
#    "$TARGET_PROJECT_DIR") guarantees the two home-directory targets never
#    ascend past their own skill directory even when TARGET_PROJECT_DIR
#    resolves to "$HOME" itself. Each removal is echoed as it happens.
for i in "${!TARGET_DIRS[@]}"; do
    target_dir="${TARGET_DIRS[$i]}"
    council_target_dir="$(dirname "$target_dir")/council-review"
    if [ -d "$council_target_dir" ]; then
        if [ -d "$COUNCIL_SRC_DIR" ]; then
            while IFS= read -r -d '' council_file; do
                relative="${council_file#"$COUNCIL_SRC_DIR/"}"
                rm -f "$council_target_dir/$relative"
            done < <(
                find "$COUNCIL_SRC_DIR" -type f \
                    ! -path '*/__pycache__/*' \
                    ! -name '*.pyc' \
                    -print0 | LC_ALL=C sort -z
            )
        fi
        rm -f \
            "$council_target_dir/council-policy.json" \
            "$council_target_dir/references/council-policy.json"
        while IFS= read -r -d '' council_dir; do
            rmdir "$council_dir" 2>/dev/null || true
        done < <(find "$council_target_dir" -depth -type d -print0)
        if [ -d "$council_target_dir" ]; then
            echo "✅ Removed council-review skill files from $council_target_dir " \
                "(other content preserved)"
        else
            echo "✅ Removed $council_target_dir"
        fi
    fi
    if [ -d "$target_dir" ]; then
        for installed_file in "${INSTALLED_FILES[@]}"; do
            rm -f "$target_dir/$installed_file"
        done
        rm -rf "$target_dir/learned-state"
        rmdir "$target_dir" 2>/dev/null || true
        if [ -d "$target_dir" ]; then
            echo "✅ Removed skill files from $target_dir (other content preserved)"
        else
            echo "✅ Removed $target_dir"
            is_project_local=false
            for project_local_index in "${PROJECT_LOCAL_INDICES[@]}"; do
                if [ "$i" -eq "$project_local_index" ]; then
                    is_project_local=true
                    break
                fi
            done
            if [ "$is_project_local" = true ]; then
                skills_parent="$(dirname "$target_dir")"
                if rmdir "$skills_parent" 2>/dev/null; then
                    echo "✅ Removed empty $skills_parent"
                    convention_parent="$(dirname "$skills_parent")"
                    if rmdir "$convention_parent" 2>/dev/null; then
                        echo "✅ Removed empty $convention_parent"
                    fi
                fi
            fi
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
    if [ -s "$target_file" ]; then
        awk '
            { lines[NR] = $0 }
            END {
                while (NR > 0 && lines[NR] ~ /^[[:space:]]*$/) { NR-- }
                for (i = 1; i <= NR; i++) { print lines[i] }
            }
        ' "$target_file" > "$target_file.tmp" && mv -f "$target_file.tmp" "$target_file"
    fi
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

# 2.5. Remove Claude Code rules file if present.
CLAUDE_RULE_FILE="$TARGET_PROJECT_DIR/.claude/rules/worker-routing.md"
if [ -f "$CLAUDE_RULE_FILE" ]; then
    rm -f "$CLAUDE_RULE_FILE"
    echo "✅ Removed $CLAUDE_RULE_FILE"
    # Clean up empty parent directories
    rmdir "$TARGET_PROJECT_DIR/.claude/rules" 2>/dev/null || true
    rmdir "$TARGET_PROJECT_DIR/.claude" 2>/dev/null || true
fi

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
