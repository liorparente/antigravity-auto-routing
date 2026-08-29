#!/bin/bash
# install.sh — atomically installs the Auto Routing & Collaboration Protocol.
# Usage: ./install.sh [target_project_dir]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="$SCRIPT_DIR/skills/worker-routing"
COUNCIL_SRC_DIR="$SCRIPT_DIR/skills/council-review"
LEARN_SESSION_SRC_DIR="$SCRIPT_DIR/skills/learn-session"
PROTOCOL_SRC="$SRC_DIR/protocol.md"
TARGET_PROJECT_DIR="${1:-.}"

if [ ! -d "$TARGET_PROJECT_DIR" ] \
    || [ ! -r "$PROTOCOL_SRC" ] \
    || [ ! -d "$COUNCIL_SRC_DIR" ] \
    || [ ! -d "$LEARN_SESSION_SRC_DIR" ]; then
    echo "❌ Target project, protocol source, council-review source, or learn-session source is unavailable." \
        >&2
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
# Non-Python artifacts propagated to an installed harness. Python production
# modules are discovered below, so extracting a future module cannot leave an
# installed harness missing an import solely because this array was not edited.
MANAGED_FILES=(
    SKILL.md REFERENCE.md routing-audit.sh protocol.md
)
PYTHON_MODULE_MANIFEST=".auto-routing-python-modules"
PYTHON_MODULE_MANIFEST_HEADER="auto-routing-python-modules-v1"
PYTHON_MODULES=()

valid_python_module_name() {
    local module_name="$1" module_stem
    case "$module_name" in
        *.py) module_stem="${module_name%.py}" ;;
        *) return 1 ;;
    esac
    case "$module_stem" in
        ""|test_*|.*|*.*|*[!A-Za-z0-9_]*) return 1 ;;
        [A-Za-z_]*) return 0 ;;
        *) return 1 ;;
    esac
}

sha256_file() {
    python3 - "$1" <<'PYEOF'
import hashlib
import sys

with open(sys.argv[1], "rb") as stream:
    print(hashlib.sha256(stream.read()).hexdigest())
PYEOF
}

# Populate PARSED_PYTHON_MODULES with validated "digest|name" records. The
# versioned header distinguishes an installer manifest from a user-file/path
# collision, while the digest prevents a corrupted record from claiming an
# unrelated target-side Python file by basename alone.
read_python_module_manifest() {
    local manifest="$1" header digest module_name extra record existing
    PARSED_PYTHON_MODULES=()
    [ -f "$manifest" ] && [ ! -L "$manifest" ] || return 1
    {
        IFS= read -r header || return 1
        [ "$header" = "$PYTHON_MODULE_MANIFEST_HEADER" ] || return 1
        while IFS=$'\t' read -r digest module_name extra \
            || [ -n "$digest$module_name$extra" ]; do
            [ -z "$extra" ] || return 1
            [ "${#digest}" -eq 64 ] || return 1
            case "$digest" in
                *[!0-9a-f]*) return 1 ;;
            esac
            valid_python_module_name "$module_name" || return 1
            if [ "${#PARSED_PYTHON_MODULES[@]}" -gt 0 ]; then
                for record in "${PARSED_PYTHON_MODULES[@]}"; do
                    existing="${record#*|}"
                    [ "$existing" != "$module_name" ] || return 1
                done
            fi
            PARSED_PYTHON_MODULES+=("$digest|$module_name")
        done
        [ "${#PARSED_PYTHON_MODULES[@]}" -gt 0 ] || return 1
    } < "$manifest"
}

# All source-side, non-test Python modules are installer-managed. Discovering
# only from SRC_DIR (rather than an installed target) keeps installation
# deterministic and gives uninstall an equally surgical ownership boundary.
for python_module in "$SRC_DIR"/*.py; do
    [ -f "$python_module" ] || continue
    python_module_name="$(basename "$python_module")"
    [[ "$python_module_name" == test_* ]] && continue
    if ! valid_python_module_name "$python_module_name"; then
        echo "❌ Unsafe Python module filename: $python_module_name" >&2
        exit 1
    fi
    MANAGED_FILES+=("$python_module_name")
    PYTHON_MODULES+=("$python_module_name")
done

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

SNAPSHOT_COUNT=0
SNAPSHOT_SEEN=""

snapshot_file() {
    local target="$1" number
    case "$SNAPSHOT_SEEN" in
        *"|$target|"*) return ;;
    esac
    SNAPSHOT_SEEN="${SNAPSHOT_SEEN:-|}$target|"
    number=$SNAPSHOT_COUNT
    SNAPSHOT_COUNT=$((SNAPSHOT_COUNT + 1))
    if [ -e "$target" ]; then
        cp -p "$target" "$TRANSACTION_DIR/$number"
        echo "$number|present|$target" >> "$TRANSACTION_DIR/entries"
    else
        echo "$number|absent|$target" >> "$TRANSACTION_DIR/entries"
    fi
}

atomic_copy() {
    local source="$1" target="$2" temporary
    snapshot_file "$target"
    mkdir -p "$(dirname "$target")"
    temporary="${target}.auto-routing-tmp.$$"
    cp "$source" "$temporary"
    mv -f "$temporary" "$target"
}

merge_consultation_policy() {
    local source="$1" target="$2" merged
    if [ ! -f "$target" ]; then
        atomic_copy "$source" "$target"
        return 0
    fi
    if grep -q '"consultation_policy"' "$target" 2>/dev/null; then
        return 0
    fi
    merged="$STAGING_DIR/routing-config-merged-$routing_config_index.json"
    routing_config_index=$((routing_config_index + 1))
    python3 - "$source" "$target" "$merged" <<'PYEOF'
import json
import shutil
import sys

source_path, target_path, output_path = sys.argv[1:]
try:
    with open(source_path, "r", encoding="utf-8") as stream:
        source = json.load(stream)
    with open(target_path, "r", encoding="utf-8") as stream:
        target = json.load(stream)
except (OSError, json.JSONDecodeError):
    shutil.copyfile(target_path, output_path)
else:
    if (
        isinstance(source, dict)
        and isinstance(target, dict)
        and "consultation_policy" not in target
        and "consultation_policy" in source
    ):
        target["consultation_policy"] = source["consultation_policy"]
        with open(output_path, "w", encoding="utf-8") as stream:
            json.dump(target, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
    else:
        shutil.copyfile(target_path, output_path)
PYEOF
    if ! cmp -s "$target" "$merged"; then
        atomic_copy "$merged" "$target"
    fi
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
    if [ -s "$output" ]; then
        awk '
            { lines[NR] = $0 }
            END {
                while (NR > 0 && lines[NR] ~ /^[[:space:]]*$/) { NR-- }
                for (i = 1; i <= NR; i++) { print lines[i] }
            }
        ' "$output" > "$output.trimmed" && mv -f "$output.trimmed" "$output"
    fi
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
# Keep an installed ownership record for the dynamically discovered modules.
# This lets a later uninstall remove a module that was present at install time
# but has since been deleted from this checkout, without globbing target files.
{
    printf '%s\n' "$PYTHON_MODULE_MANIFEST_HEADER"
    for python_module_name in "${PYTHON_MODULES[@]}"; do
        printf '%s\t%s\n' \
            "$(sha256_file "$STAGING_DIR/files/$python_module_name")" \
            "$python_module_name"
    done
} > "$STAGING_DIR/python-module-manifest"

# Stage the complete Council Review skill alongside worker-routing. Ignore
# interpreter caches, which are runtime artifacts rather than installable
# skill content. Relative paths are retained so scripts, references, tests,
# and agent metadata reach the same sibling skill directory in every harness.
mkdir -p "$STAGING_DIR/council-review"
while IFS= read -r -d '' council_file; do
    relative="${council_file#"$COUNCIL_SRC_DIR/"}"
    mkdir -p "$STAGING_DIR/council-review/$(dirname "$relative")"
    cp "$council_file" "$STAGING_DIR/council-review/$relative"
done < <(
    find "$COUNCIL_SRC_DIR" -type f \
        ! -path '*/__pycache__/*' \
        ! -name '*.pyc' \
        -print0 | LC_ALL=C sort -z
)

# Stage the complete learn-session skill with the same cache exclusions as
# council-review, so every harness receives the canonical learning workflow.
mkdir -p "$STAGING_DIR/learn-session"
while IFS= read -r -d '' learn_session_file; do
    relative="${learn_session_file#"$LEARN_SESSION_SRC_DIR/"}"
    mkdir -p "$STAGING_DIR/learn-session/$(dirname "$relative")"
    cp "$learn_session_file" "$STAGING_DIR/learn-session/$relative"
done < <(
    find "$LEARN_SESSION_SRC_DIR" -type f \
        ! -path '*/__pycache__/*' \
        ! -name '*.pyc' \
        -print0 | LC_ALL=C sort -z
)

# Adopted learned state (spec 0004 ticket 23) joins the same atomic
# staging/sync mechanism as every other managed artifact rather than getting
# a second, parallel one. `learned_state.py`'s `root_dir` for this repo's own
# store is `SCRIPT_DIR` (the directory this script itself lives in), since
# `learned-state/` is a git-tracked sibling of `install.sh`, not part of
# `SRC_DIR`. Resolving "what is currently adopted" is Python's job
# (`learned_state.current_version_dir`); this script's only job is bridging
# that answer into bash.
LEARNED_STATE_RELATIVE="learned-state"
LEARNED_STATE_SRC="$SCRIPT_DIR/$LEARNED_STATE_RELATIVE"
STAGED_LEARNED_STATE=0

if [ -d "$LEARNED_STATE_SRC" ]; then
    CURRENT_VERSION_DIR=""
    if ! CURRENT_VERSION_DIR="$(python3 - "$SRC_DIR" "$SCRIPT_DIR" <<'PYEOF'
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
import learned_state

try:
    resolved = learned_state.current_version_dir(root_dir=Path(sys.argv[2]))
    if resolved is not None:
        # Validate that the current version snapshot directory and documents are intact
        learned_state.read_current(root_dir=Path(sys.argv[2]))
except ValueError as exc:
    print(f"learned-state is damaged: {exc}", file=sys.stderr)
    sys.exit(1)

print(str(resolved) if resolved is not None else "")
PYEOF
)"; then
        # A damaged store (e.g. a corrupted history.jsonl) must fail preflight
        # cleanly, before any target has been touched — same contract as the
        # marker-balance check below.
        echo "❌ Preflight failed: adopted learned state could not be resolved." >&2
        exit 1
    fi

    if [ -n "$CURRENT_VERSION_DIR" ]; then
        # A resolvable but un-adopted store (`current_version_dir` returned
        # `None`, printed here as an empty line) proceeds cleanly without
        # staging anything — there is nothing yet to propagate.
        STAGED_LEARNED_STATE=1
        mkdir -p "$STAGING_DIR/learned-state/versions"
        cp "$LEARNED_STATE_SRC/history.jsonl" "$STAGING_DIR/learned-state/history.jsonl"
        cp -R "$LEARNED_STATE_SRC/versions/." "$STAGING_DIR/learned-state/versions/"
    fi
fi

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

# Validate every existing ownership manifest before the first target write.
# A malformed file, symlink, or directory at the reserved path is ambiguous:
# fail closed instead of overwriting user content or trusting attacker-chosen
# removal entries. Normalized records are staged by target index for the
# transactional stale-module cleanup below.
mkdir -p "$STAGING_DIR/previous-python-modules"
for index in "${!TARGET_DIRS[@]}"; do
    manifest_path="${TARGET_DIRS[$index]}/$PYTHON_MODULE_MANIFEST"
    if [ -e "$manifest_path" ] || [ -L "$manifest_path" ]; then
        if ! read_python_module_manifest "$manifest_path"; then
            echo "❌ Invalid or colliding Python ownership manifest: $manifest_path" >&2
            exit 1
        fi
        printf '%s\n' "${PARSED_PYTHON_MODULES[@]}" \
            > "$STAGING_DIR/previous-python-modules/$index"
    fi
done

# Test-only fault injection verifies that preflight has no side effects.
if [ "${AUTO_ROUTING_FAIL_AFTER_STAGE:-0}" = "1" ]; then
    echo "🧪 Test hook AUTO_ROUTING_FAIL_AFTER_STAGE=1 triggered — aborting install before target mutation." >&2
    exit 1
fi

write_count=0
routing_config_index=0
# One helper shared by every managed write below (MANAGED_FILES and, when
# staged, learned state) so both count against the same
# AUTO_ROUTING_FAIL_AFTER_WRITES fault-injection hook and roll back through
# the same atomic_copy/snapshot_file transaction.
copy_managed() {
    local source="$1" target="$2"
    atomic_copy "$source" "$target"
    write_count=$((write_count + 1))
    if [ "${AUTO_ROUTING_FAIL_AFTER_WRITES:-0}" = "$write_count" ]; then
        echo "🧪 Test hook AUTO_ROUTING_FAIL_AFTER_WRITES triggered." >&2
        exit 1
    fi
}

for index in "${!TARGET_DIRS[@]}"; do
    target_dir="${TARGET_DIRS[$index]}"
    previous_manifest="$STAGING_DIR/previous-python-modules/$index"
    if [ -f "$previous_manifest" ]; then
        while IFS='|' read -r previous_digest previous_module_name \
            || [ -n "$previous_digest$previous_module_name" ]; do
            module_is_current=false
            for python_module_name in "${PYTHON_MODULES[@]}"; do
                if [ "$python_module_name" = "$previous_module_name" ]; then
                    module_is_current=true
                    break
                fi
            done
            [ "$module_is_current" = false ] || continue
            stale_target="$target_dir/$previous_module_name"
            if [ -f "$stale_target" ] && [ ! -L "$stale_target" ]; then
                if [ "$(sha256_file "$stale_target")" = "$previous_digest" ]; then
                    snapshot_file "$stale_target"
                    rm -f "$stale_target"
                else
                    echo "⚠️  Preserving modified stale module: $stale_target" >&2
                fi
            elif [ -e "$stale_target" ] || [ -L "$stale_target" ]; then
                echo "⚠️  Preserving non-regular stale module path: $stale_target" >&2
            fi
        done < "$previous_manifest"
    fi
    for file in "${MANAGED_FILES[@]}"; do
        copy_managed "$STAGING_DIR/files/$file" "$target_dir/$file"
    done
    copy_managed \
        "$STAGING_DIR/python-module-manifest" \
        "$target_dir/$PYTHON_MODULE_MANIFEST"
    # Preserve customized routing configuration; install only the default.
    if [ ! -f "$target_dir/routing-config.json" ]; then
        atomic_copy "$SRC_DIR/routing-config.json" "$target_dir/routing-config.json"
    else
        merge_consultation_policy \
            "$SRC_DIR/routing-config.json" \
            "$target_dir/routing-config.json"
    fi
    chmod +x "$target_dir/routing-audit.sh" "$target_dir/agent_council.py"

    if [ "$STAGED_LEARNED_STATE" -eq 1 ]; then
        copy_managed \
            "$STAGING_DIR/learned-state/history.jsonl" \
            "$target_dir/$LEARNED_STATE_RELATIVE/history.jsonl"
        while IFS= read -r -d '' version_file; do
            relative="${version_file#"$STAGING_DIR/learned-state/versions/"}"
            copy_managed "$version_file" "$target_dir/$LEARNED_STATE_RELATIVE/versions/$relative"
        done < <(find "$STAGING_DIR/learned-state/versions" -type f -print0 | LC_ALL=C sort -z)
    fi

    council_target_dir="$(dirname "$target_dir")/council-review"
    while IFS= read -r -d '' council_file; do
        relative="${council_file#"$STAGING_DIR/council-review/"}"
        copy_managed "$council_file" "$council_target_dir/$relative"
    done < <(
        find "$STAGING_DIR/council-review" -type f -print0 | LC_ALL=C sort -z
    )

    learn_target_dir="$(dirname "$target_dir")/learn-session"
    while IFS= read -r -d '' learn_session_file; do
        relative="${learn_session_file#"$STAGING_DIR/learn-session/"}"
        copy_managed "$learn_session_file" "$learn_target_dir/$relative"
    done < <(
        find "$STAGING_DIR/learn-session" -type f -print0 | LC_ALL=C sort -z
    )

    # council-policy.json was superseded by worker-routing/routing-config.json.
    # Remove both historic placements transactionally so a failed install can
    # restore an existing legacy policy exactly as it was.
    for legacy_policy in \
        "$council_target_dir/council-policy.json" \
        "$council_target_dir/references/council-policy.json"; do
        if [ -e "$legacy_policy" ]; then
            snapshot_file "$legacy_policy"
            rm -f "$legacy_policy"
        fi
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
