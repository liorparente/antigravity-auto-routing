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
# SHA-256 identities of every source-controlled
# skills/council-review/scripts/council_review.py blob reachable from Git
# history when the facade was retired (16 path-touching commits, 14 unique
# contents). These digests identify an exact retired artifact only when a
# signed prior manifest also proves that both worker modules retired with the
# facade belonged to the same pre-ticket installation.
LEGACY_COUNCIL_REVIEW_FACADE_DIGESTS=(
    027da87617c84de20f7033b6c4e191093db3efda9814063f630c96757cb86630
    2b7780b2a47325194e43c1b1a69adeb99db7df430597c6eacdd05f96806dfb2b
    307c3b1b6747f5977b97041d47c6f2f64d975d17ed0a11f3c22ad50b8b16b7dc
    43d3f97af0122d774f2ed1d2f359cbc5aac7f73e50f50374f6921b8020c8cc3d
    47c761b9dd228580ff9148ca50df32e874719fdcdac3ac6767a00a9378c58618
    5de22e3a7fb1584c7d59db458365ed521e8f9c95b9a094537b9608990daa352a
    9b2ff8bbb3fb14d67bb3551675326def5c6fbb73641e2184ea93a2649bc9c26b
    a69994ee6743285b12e635e474fa7fd9c09dfc4e2dce7e2e27b900487e09bdeb
    a722b850baeb4421b4a982e09bbf3ff5e2d9420fb031cab564b33f4dde2bc380
    a9d8937040dc85a2a2d0fddcf6e77a5aaf6adcf01bfc9e0db217e8398e12136f
    b21d3320d8550e0a5747e998897a6dbe882eb259bf7dc712f483be698e107b8b
    c55f402700a607afc0366de2ce39956fe2aaf04e2fa8a93e8112fe810fc65791
    e698442453ffaaf54dbd90879f7085b500791d9802daf514a2a6c8a206ea583f
    ebdecc52f7624b33928b32536f2fea191a4c1d90e6a3502f2f7e6563433adc8f
)
PYTHON_MODULE_MANIFEST=".auto-routing-python-modules"
PYTHON_MODULE_MANIFEST_HEADER="auto-routing-python-modules-v1"
PYTHON_MODULE_RECEIPT_NAME=".auto-routing-python-modules.receipt"
PYTHON_MODULE_RECEIPT_HEADER="auto-routing-python-modules-receipt-v2-hmac-sha256"
INSTALLER_STATE_DIR="${AUTO_ROUTING_STATE_DIR:-$HOME/.local/state/auto-routing}"
PYTHON_MODULE_KEY="$INSTALLER_STATE_DIR/python-module-manifest.key"
PROJECT_RECEIPT_ID="$(python3 - "$TARGET_PROJECT_DIR" <<'PYEOF'
import hashlib
import os
import sys

print(hashlib.sha256(os.fsencode(sys.argv[1])).hexdigest())
PYEOF
)"
PYTHON_MODULE_RECEIPT="$INSTALLER_STATE_DIR/projects/$PROJECT_RECEIPT_ID/$PYTHON_MODULE_RECEIPT_NAME"
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
# collision. Record digests protect modified files; the independent receipt
# validated below is what authorizes the manifest as installer provenance.
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

# SECURITY INVARIANT: this function and the three helpers above are duplicated
# in uninstall.sh so each script remains standalone. ManagedFileClosureTests
# compares their bodies and contract constants byte-for-byte to prevent drift.
validate_python_module_receipt() {
    local receipt="$1" key="$2" project="$3" output="$4"
    [ -f "$receipt" ] && [ ! -L "$receipt" ] || return 1
    [ -f "$key" ] && [ ! -L "$key" ] || return 1
    python3 - "$receipt" "$key" "$project" "$output" \
        "$PYTHON_MODULE_RECEIPT_HEADER" <<'PYEOF'
import hashlib
import hmac
import os
import sys

receipt_path, key_path, project, output_path, expected_header = sys.argv[1:]
try:
    with open(key_path, "r", encoding="ascii") as stream:
        key_hex = stream.read().strip()
    if len(key_hex) != 64 or any(char not in "0123456789abcdef" for char in key_hex):
        raise ValueError("invalid key")
    with open(receipt_path, "rb") as stream:
        header = stream.readline().rstrip(b"\n")
        signature = stream.readline().rstrip(b"\n")
        manifest = stream.read()
    if header != expected_header.encode("ascii"):
        raise ValueError("invalid header")
    if len(signature) != 64 or any(byte not in b"0123456789abcdef" for byte in signature):
        raise ValueError("invalid signature")
    payload = os.fsencode(project) + b"\0" + manifest
    expected = hmac.new(bytes.fromhex(key_hex), payload, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature.decode("ascii"), expected):
        raise ValueError("signature mismatch")
    with open(output_path, "wb") as stream:
        stream.write(manifest)
except (OSError, UnicodeError, ValueError):
    sys.exit(1)
PYEOF
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
touch "$TRANSACTION_DIR/writes"
touch "$TRANSACTION_DIR/directories"
touch "$TRANSACTION_DIR/quarantines"
touch "$TRANSACTION_DIR/recoveries"
COMMITTED=0
RECOVERY_RECORD_FAILED=0

record_recovery() {
    local quarantine="$1"
    if ! grep -Fqx -- "$quarantine" "$TRANSACTION_DIR/recoveries"; then
        if ! printf '%s\n' "$quarantine" >> "$TRANSACTION_DIR/recoveries"; then
            RECOVERY_RECORD_FAILED=1
            return 1
        fi
    fi
}

rollback_transaction_entry() {
    local snapshot="$1" state="$2" target="$3"
    local expected_device="$4" expected_inode="$5" number="$6"
    python3 - rollback \
        "$snapshot" "$state" "$target" "$expected_device" "$expected_inode" \
        "$TRANSACTION_DIR/recoveries" "$number" <<'PYEOF'
import ctypes
import errno
import os
import shutil
import sys
import tempfile

(
    snapshot,
    state,
    target,
    expected_device,
    expected_inode,
    recovery_log,
    number,
) = sys.argv[2:]
expected_identity = (int(expected_device), int(expected_inode))


def exists(path: str) -> bool:
    return os.path.lexists(path)


def record(path: str) -> None:
    try:
        with open(recovery_log, "a", encoding="utf-8") as stream:
            stream.write(path + "\n")
    except OSError:
        print(f"❌ Could not index retained recovery path: {path}", file=sys.stderr)


def retain(message: str, *paths: str) -> None:
    print(message, file=sys.stderr)
    for path in paths:
        if exists(path):
            record(path)
            print(f"   Retained recovery: {path}", file=sys.stderr)


def rename_no_replace(source: str, destination: str) -> None:
    """Atomically move source only when destination is still absent."""
    libc = ctypes.CDLL(None, use_errno=True)
    source_bytes = os.fsencode(source)
    destination_bytes = os.fsencode(destination)
    if sys.platform == "darwin" and hasattr(libc, "renamex_np"):
        rename_exclusive = 0x00000004
        result = libc.renamex_np(source_bytes, destination_bytes, rename_exclusive)
    elif sys.platform.startswith("linux") and hasattr(libc, "renameat2"):
        at_fdcwd = -100
        rename_noreplace = 1
        result = libc.renameat2(
            at_fdcwd,
            source_bytes,
            at_fdcwd,
            destination_bytes,
            rename_noreplace,
        )
    else:
        raise OSError(errno.ENOTSUP, "atomic no-replace rename is unavailable")
    if result != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number), destination)


parent = os.path.dirname(target) or "."
try:
    recovery_dir = tempfile.mkdtemp(
        prefix=f".auto-routing-recovery.{os.getpid()}.{number}.", dir=parent
    )
except OSError as error:
    retain(
        f"❌ Could not create a private recovery directory for {target}: {error}",
        snapshot,
    )
    sys.exit(4)

# Record the private directory before moving anything. If this process is
# interrupted after the rename, cleanup can still name the discoverable path.
record(recovery_dir)
held = os.path.join(recovery_dir, "current")
try:
    os.rename(target, held)
except FileNotFoundError:
    retain(
        f"❌ Transaction-written object disappeared from {target}; "
        "preserved the original snapshot for manual recovery.",
        snapshot,
    )
    try:
        os.rmdir(recovery_dir)
    except OSError:
        pass
    sys.exit(4)
except OSError as error:
    retain(
        f"❌ Could not safely isolate the current object at {target}: {error}",
        snapshot,
        recovery_dir,
    )
    sys.exit(4)

held_stat = os.lstat(held)
held_identity = (held_stat.st_dev, held_stat.st_ino)
if held_identity != expected_identity:
    # The pathname no longer names the inode installed by this transaction.
    # An exclusive rename restores the moved replacement only if the name
    # remains absent. Otherwise it stays in the private recovery directory;
    # neither version is overwritten and no link/unlink race is introduced.
    try:
        rename_no_replace(held, target)
    except OSError:
        pass
    else:
        try:
            os.rmdir(recovery_dir)
        except OSError:
            pass
    retain(
        f"❌ Concurrent replacement detected at {target}; rollback did not "
        "overwrite it and preserved the original version.",
        target,
        snapshot,
        held,
        recovery_dir,
    )
    sys.exit(4)

if state == "absent":
    try:
        os.unlink(held)
        os.rmdir(recovery_dir)
    except OSError as error:
        retain(
            f"❌ Could not remove the isolated transaction object for {target}: {error}",
            held,
            recovery_dir,
        )
        sys.exit(4)
    if exists(target):
        retain(
            f"❌ Concurrent replacement detected at {target}; preserved it "
            "while removing the exact transaction-written object.",
            target,
        )
        sys.exit(4)
    sys.exit(0)

# Copy the original snapshot into the target's filesystem, then publish it
# with one atomic exclusive rename. If another process recreates target at any
# point before that syscall, both that path and these recovery bytes stay.
restored = os.path.join(recovery_dir, "original")
try:
    shutil.copy2(snapshot, restored, follow_symlinks=False)
    rename_no_replace(restored, target)
except OSError as error:
    retain(
        f"❌ Could not restore the original snapshot to {target} without "
        f"clobbering a concurrent path: {error}",
        snapshot,
        restored,
        held,
        recovery_dir,
    )
    sys.exit(4)

try:
    os.unlink(held)
    os.rmdir(recovery_dir)
except OSError as error:
    retain(
        f"❌ Restored {target}, but could not finish recovery cleanup: {error}",
        snapshot,
        restored,
        held,
        recovery_dir,
    )
    sys.exit(4)
sys.exit(0)
PYEOF
}

rollback() {
    local rollback_error=false partial_recovery=false restore_status
    local expected_device expected_inode write_number write_device write_inode write_target
    [ -s "$TRANSACTION_DIR/entries" ] || [ -s "$TRANSACTION_DIR/directories" ] \
        || [ -s "$TRANSACTION_DIR/quarantines" ] || return 0
    echo "↩️  Rolling back incomplete installation..." >&2
    while IFS='|' read -r number state target; do
        [ -n "$target" ] || continue
        expected_device=""
        expected_inode=""
        while IFS='|' read -r write_number write_device write_inode write_target; do
            if [ "$write_number" = "$number" ] && [ "$write_target" = "$target" ]; then
                expected_device="$write_device"
                expected_inode="$write_inode"
            fi
        done < "$TRANSACTION_DIR/writes"
        if [ -z "$expected_device" ] || [ -z "$expected_inode" ]; then
            # The snapshot was recorded but no atomic replacement completed.
            # The original path is still authoritative and must not be touched.
            continue
        fi
        restore_status=0
        rollback_transaction_entry \
            "$TRANSACTION_DIR/$number" "$state" "$target" \
            "$expected_device" "$expected_inode" "$number" \
            || restore_status=$?
        if [ "$restore_status" -ne 0 ]; then
            partial_recovery=true
        fi
    done < "$TRANSACTION_DIR/entries"
    while IFS='|' read -r target quarantine; do
        if [ -z "$target" ] || [ ! -e "$quarantine" ]; then
            continue
        fi
        if grep -Fqx -- "$quarantine" "$TRANSACTION_DIR/recoveries"; then
            partial_recovery=true
            continue
        fi
        restore_status=0
        restore_quarantined_managed "$quarantine" "$target" \
            || restore_status=$?
        if [ "$restore_status" -ne 0 ]; then
            [ "$RECOVERY_RECORD_FAILED" -eq 0 ] || rollback_error=true
            partial_recovery=true
        fi
    done < "$TRANSACTION_DIR/quarantines"
    # Retry globally until no directory can be removed. This handles shared
    # ancestors recorded before later sibling directories were created.
    while :; do
        removed_directory=false
        while IFS= read -r directory; do
            [ -n "$directory" ] || continue
            if rmdir "$directory" 2>/dev/null; then
                removed_directory=true
            fi
        done < "$TRANSACTION_DIR/directories"
        [ "$removed_directory" = true ] || break
    done
    [ "$rollback_error" = false ] || return 2
    [ "$partial_recovery" = false ] || return 1
    return 0
}

cleanup() {
    local status="$1" rollback_status=0 recovery target quarantine
    local number state snapshot retained_transaction=false
    if [ "$status" -ne 0 ] && [ "$COMMITTED" -ne 1 ]; then
        rollback || rollback_status=$?
        if [ "$rollback_status" -eq 0 ]; then
            echo "❌ Installation failed; original files were restored." >&2
        elif [ "$rollback_status" -eq 1 ]; then
            echo "❌ Installation failed with partial recovery; some originals could not be restored automatically." >&2
        else
            echo "❌ Installation failed; rollback encountered errors and recovery may be incomplete." >&2
        fi
        if [ "$rollback_status" -ne 0 ]; then
            retained_transaction=true
            while IFS= read -r recovery; do
                [ -n "$recovery" ] || continue
                if [ -e "$recovery" ] || [ -L "$recovery" ]; then
                    echo "   Retained recovery: $recovery" >&2
                fi
            done < "$TRANSACTION_DIR/recoveries"
            while IFS='|' read -r number state target; do
                [ "$state" = "present" ] || continue
                snapshot="$TRANSACTION_DIR/$number"
                [ -e "$snapshot" ] || continue
                echo "   Retained original snapshot: $snapshot" >&2
            done < "$TRANSACTION_DIR/entries"
            while IFS='|' read -r target quarantine; do
                [ -e "$quarantine" ] || [ -L "$quarantine" ] || continue
                echo "   Retained quarantined original: $quarantine" >&2
            done < "$TRANSACTION_DIR/quarantines"
            echo "   Retained transaction directory: $TRANSACTION_DIR" >&2
        fi
    fi
    rm -rf "$STAGING_DIR"
    if [ "$retained_transaction" = true ]; then
        return
    fi
    while IFS='|' read -r target quarantine; do
        [ -n "$target" ] || continue
        if grep -Fqx -- "$quarantine" "$TRANSACTION_DIR/recoveries"; then
            continue
        fi
        [ "$RECOVERY_RECORD_FAILED" -eq 0 ] || continue
        rm -f "$quarantine"
    done < "$TRANSACTION_DIR/quarantines"
    rm -rf "$TRANSACTION_DIR"
}
trap 'cleanup "$?"' EXIT

# The HMAC key lives in installer state, outside every writable target
# project. It authenticates provenance against project-local joint forgery.
# A same-account attacker who can read this 0600 key is outside that boundary.
STAGED_PYTHON_MODULE_KEY="$STAGING_DIR/python-module-manifest.key"
INSTALL_PYTHON_MODULE_KEY=0
if [ -e "$PYTHON_MODULE_KEY" ] || [ -L "$PYTHON_MODULE_KEY" ]; then
    if [ ! -f "$PYTHON_MODULE_KEY" ] || [ -L "$PYTHON_MODULE_KEY" ] \
        || ! key_value="$(tr -d '\n' < "$PYTHON_MODULE_KEY")" \
        || [ "${#key_value}" -ne 64 ]; then
        echo "❌ Invalid installer provenance key: $PYTHON_MODULE_KEY" >&2
        exit 1
    fi
    case "$key_value" in
        *[!0-9a-f]*)
            echo "❌ Invalid installer provenance key: $PYTHON_MODULE_KEY" >&2
            exit 1
            ;;
    esac
    cp "$PYTHON_MODULE_KEY" "$STAGED_PYTHON_MODULE_KEY"
else
    python3 - "$STAGED_PYTHON_MODULE_KEY" <<'PYEOF'
import secrets
import sys

with open(sys.argv[1], "w", encoding="ascii") as stream:
    stream.write(secrets.token_hex(32) + "\n")
PYEOF
    chmod 600 "$STAGED_PYTHON_MODULE_KEY"
    INSTALL_PYTHON_MODULE_KEY=1
fi

SNAPSHOT_COUNT=0
SNAPSHOT_SEEN=""
DIRECTORY_SNAPSHOT_SEEN=""

snapshot_created_directories() {
    local directory="$1"
    while [ ! -d "$directory" ]; do
        [ ! -e "$directory" ] && [ ! -L "$directory" ] || return 1
        case "$DIRECTORY_SNAPSHOT_SEEN" in
            *"|$directory|"*) ;;
            *)
                DIRECTORY_SNAPSHOT_SEEN="${DIRECTORY_SNAPSHOT_SEEN:-|}$directory|"
                # Record leaf-to-root: rollback removes children before parents.
                printf '%s\n' "$directory" >> "$TRANSACTION_DIR/directories"
                ;;
        esac
        [ "$directory" != "/" ] || return 1
        directory="$(dirname "$directory")"
    done
}

snapshot_file() {
    local target="$1" number
    case "$SNAPSHOT_SEEN" in
        *"|$target|"*)
            while IFS='|' read -r number _ snapshot_target; do
                if [ "$snapshot_target" = "$target" ]; then
                    LAST_SNAPSHOT_NUMBER="$number"
                    return
                fi
            done < "$TRANSACTION_DIR/entries"
            return 1
            ;;
    esac
    SNAPSHOT_SEEN="${SNAPSHOT_SEEN:-|}$target|"
    number=$SNAPSHOT_COUNT
    LAST_SNAPSHOT_NUMBER="$number"
    SNAPSHOT_COUNT=$((SNAPSHOT_COUNT + 1))
    if [ -e "$target" ]; then
        cp -p "$target" "$TRANSACTION_DIR/$number"
        echo "$number|present|$target" >> "$TRANSACTION_DIR/entries"
    else
        echo "$number|absent|$target" >> "$TRANSACTION_DIR/entries"
    fi
}

atomic_copy() {
    local source="$1" target="$2" temporary identity device inode
    snapshot_file "$target"
    snapshot_created_directories "$(dirname "$target")"
    mkdir -p "$(dirname "$target")"
    temporary="$(mktemp "${target}.auto-routing-tmp.XXXXXX")"
    cp "$source" "$temporary"
    identity="$(python3 - "$temporary" <<'PYEOF'
import os
import sys

stat_result = os.lstat(sys.argv[1])
print(f"{stat_result.st_dev}|{stat_result.st_ino}")
PYEOF
    )"
    device="${identity%%|*}"
    inode="${identity#*|}"
    # The identity is captured from the temporary inode before rename and
    # persisted as write intent before publication. A failure after rename can
    # therefore identify and roll back the published inode. Repeated writes to
    # one target append newer identities; rollback uses the last.
    printf '%s|%s|%s|%s\n' \
        "$LAST_SNAPSHOT_NUMBER" "$device" "$inode" "$target" \
        >> "$TRANSACTION_DIR/writes"
    mv -f "$temporary" "$target"
    record_mutation "copy:$target"
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
python3 - \
    "$STAGED_PYTHON_MODULE_KEY" \
    "$TARGET_PROJECT_DIR" \
    "$STAGING_DIR/python-module-manifest" \
    "$STAGING_DIR/python-module-receipt" \
    "$PYTHON_MODULE_RECEIPT_HEADER" <<'PYEOF'
import hashlib
import hmac
import os
import sys

key_path, project, manifest_path, receipt_path, header = sys.argv[1:]
with open(key_path, "r", encoding="ascii") as stream:
    key = bytes.fromhex(stream.read().strip())
with open(manifest_path, "rb") as stream:
    manifest = stream.read()
signature = hmac.new(
    key, os.fsencode(project) + b"\0" + manifest, hashlib.sha256
).hexdigest()
with open(receipt_path, "wb") as stream:
    stream.write(header.encode("ascii") + b"\n")
    stream.write(signature.encode("ascii") + b"\n")
    stream.write(manifest)
PYEOF

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

# Authenticate one canonical previous manifest before the first target write.
# The receipt embeds and signs that canonical manifest, so a missing target or
# target-local manifest can be healed without weakening collision checks.
# Pre-receipt v1 manifests are inherently unauthenticated; migration therefore
# requires explicit operator authority and agreement among every present copy.
mkdir -p "$STAGING_DIR/previous-python-modules"
AUTHENTICATED_PREVIOUS_MANIFEST="$STAGING_DIR/authenticated-previous-manifest"
PREVIOUS_MANIFEST_AUTHORIZED=0
PREVIOUS_MANIFEST_RECEIPT_AUTHENTICATED=0
if [ -e "$PYTHON_MODULE_RECEIPT" ] || [ -L "$PYTHON_MODULE_RECEIPT" ]; then
    if ! validate_python_module_receipt \
        "$PYTHON_MODULE_RECEIPT" \
        "$PYTHON_MODULE_KEY" \
        "$TARGET_PROJECT_DIR" \
        "$AUTHENTICATED_PREVIOUS_MANIFEST"; then
        echo "❌ Invalid Python ownership receipt: $PYTHON_MODULE_RECEIPT" >&2
        exit 1
    fi
    if ! read_python_module_manifest "$AUTHENTICATED_PREVIOUS_MANIFEST"; then
        echo "❌ Invalid authenticated Python ownership manifest." >&2
        exit 1
    fi
    PREVIOUS_MANIFEST_AUTHORIZED=1
    PREVIOUS_MANIFEST_RECEIPT_AUTHENTICATED=1
else
    first_legacy_manifest=""
    for target_dir in "${TARGET_DIRS[@]}"; do
        manifest_path="$target_dir/$PYTHON_MODULE_MANIFEST"
        if [ -e "$manifest_path" ] || [ -L "$manifest_path" ]; then
            if ! read_python_module_manifest "$manifest_path"; then
                echo "❌ Invalid or colliding Python ownership manifest: $manifest_path" >&2
                exit 1
            fi
            if [ -z "$first_legacy_manifest" ]; then
                first_legacy_manifest="$manifest_path"
            elif ! cmp -s "$first_legacy_manifest" "$manifest_path"; then
                echo "❌ Conflicting pre-receipt Python ownership manifests." >&2
                exit 1
            fi
        fi
    done
    if [ -n "$first_legacy_manifest" ]; then
        if [ "${AUTO_ROUTING_MIGRATE_PRE_RECEIPT:-0}" != "1" ]; then
            echo "❌ Pre-receipt Python ownership requires explicit migration: set AUTO_ROUTING_MIGRATE_PRE_RECEIPT=1 after reviewing the manifests." >&2
            exit 1
        fi
        cp "$first_legacy_manifest" "$AUTHENTICATED_PREVIOUS_MANIFEST"
        PREVIOUS_MANIFEST_AUTHORIZED=1
    fi
fi

if [ "$PREVIOUS_MANIFEST_AUTHORIZED" -eq 1 ]; then
    read_python_module_manifest "$AUTHENTICATED_PREVIOUS_MANIFEST"
    printf '%s\n' "${PARSED_PYTHON_MODULES[@]}" \
        > "$STAGING_DIR/authorized-previous-python-modules"
fi

for index in "${!TARGET_DIRS[@]}"; do
    manifest_path="${TARGET_DIRS[$index]}/$PYTHON_MODULE_MANIFEST"
    if [ -e "$manifest_path" ] || [ -L "$manifest_path" ]; then
        if ! read_python_module_manifest "$manifest_path"; then
            echo "❌ Invalid or colliding Python ownership manifest: $manifest_path" >&2
            exit 1
        fi
        if [ "$PREVIOUS_MANIFEST_AUTHORIZED" -eq 1 ] \
            && ! cmp -s "$manifest_path" "$AUTHENTICATED_PREVIOUS_MANIFEST"; then
            echo "❌ Python ownership manifest does not match authenticated provenance: $manifest_path" >&2
            exit 1
        fi
    fi
    if [ "$PREVIOUS_MANIFEST_AUTHORIZED" -eq 1 ]; then
        cp "$STAGING_DIR/authorized-previous-python-modules" \
            "$STAGING_DIR/previous-python-modules/$index"
    fi

    # Refuse to overwrite a current source basename unless a validated
    # receipt (or explicit migration authority) proves prior ownership.
    # Byte-identical content is deliberately insufficient provenance.
    for python_module_name in "${PYTHON_MODULES[@]}"; do
        installed_path="${TARGET_DIRS[$index]}/$python_module_name"
        [ -e "$installed_path" ] || [ -L "$installed_path" ] || continue
        if [ ! -f "$installed_path" ] || [ -L "$installed_path" ]; then
            echo "❌ Unowned Python module collision: $installed_path" >&2
            exit 1
        fi
        module_is_owned=false
        if [ -f "$STAGING_DIR/previous-python-modules/$index" ]; then
            while IFS='|' read -r previous_digest previous_module_name; do
                if [ "$previous_module_name" = "$python_module_name" ]; then
                    module_is_owned=true
                    break
                fi
            done < "$STAGING_DIR/previous-python-modules/$index"
        fi
        if [ "$module_is_owned" = false ]; then
            echo "❌ Unowned Python module collision: $installed_path" >&2
            exit 1
        fi
    done
done

# Test-only fault injection verifies that preflight has no side effects.
if [ "${AUTO_ROUTING_FAIL_AFTER_STAGE:-0}" = "1" ]; then
    echo "🧪 Test hook AUTO_ROUTING_FAIL_AFTER_STAGE=1 triggered — aborting install before target mutation." >&2
    exit 1
fi

write_count=0
routing_config_index=0
record_mutation() {
    local description="${1:-unspecified}"
    write_count=$((write_count + 1))
    if [ "${AUTO_ROUTING_TRACE_MUTATIONS:-0}" = "1" ]; then
        echo "🧭 Mutation $write_count: $description" >&2
    fi
    if [ "${AUTO_ROUTING_FAIL_AFTER_WRITES:-0}" = "$write_count" ]; then
        echo "🧪 Test hook AUTO_ROUTING_FAIL_AFTER_WRITES triggered after $description." >&2
        exit 1
    fi
}

copy_managed() {
    atomic_copy "$1" "$2"
}

quarantine_managed() {
    local target="$1" quarantine
    quarantine="${target}.auto-routing-quarantine.$$"
    if [ -e "$quarantine" ] || [ -L "$quarantine" ]; then
        echo "❌ Quarantine path collision: $quarantine" >&2
        return 3
    fi
    if ! printf '%s|%s\n' "$target" "$quarantine" >> "$TRANSACTION_DIR/quarantines"; then
        echo "❌ Could not record quarantine for managed path: $target" >&2
        return 3
    fi
    if ! mv "$target" "$quarantine"; then
        echo "❌ Could not quarantine managed path: $target" >&2
        return 3
    fi
    LAST_QUARANTINE="$quarantine"
    record_mutation "remove:$target"
}

restore_quarantined_managed() {
    local quarantine="$1" target="$2" restore_status=0 recovery_status=0
    # A hard link creates the
    # target only if its name is still absent; it can never replace a path that
    # another process recreated while the quarantined bytes were being hashed.
    python3 - "$quarantine" "$target" <<'PYEOF' || restore_status=$?
import os
import sys

quarantine, target = sys.argv[1:]
try:
    os.link(quarantine, target, follow_symlinks=False)
except FileExistsError:
    sys.exit(2)
except OSError:
    sys.exit(3)
try:
    os.unlink(quarantine)
except OSError:
    sys.exit(3)
PYEOF
    if [ "$restore_status" -ne 0 ]; then
        record_recovery "$quarantine" || recovery_status=$?
        if [ "$restore_status" -eq 2 ]; then
            echo "❌ Concurrent replacement detected at $target; preserved the quarantined original at $quarantine" >&2
        else
            echo "❌ Could not safely restore $target; preserved the quarantined original at $quarantine" >&2
        fi
        if [ "$recovery_status" -ne 0 ]; then
            echo "❌ Could not index retained recovery path: $quarantine" >&2
        fi
        return 4
    fi
    return 0
}

remove_managed_digest() {
    local target="$1" actual_digest expected_digest quarantine_status=0
    local hash_status=0 restore_status=0
    shift
    quarantine_managed "$target" || quarantine_status=$?
    if [ "$quarantine_status" -ne 0 ]; then
        return 3
    fi
    actual_digest="$(sha256_file "$LAST_QUARANTINE")" || hash_status=$?
    if [ "$hash_status" -ne 0 ]; then
        echo "❌ Could not verify quarantined managed file: $target" >&2
        restore_quarantined_managed "$LAST_QUARANTINE" "$target" \
            || restore_status=$?
        [ "$restore_status" -eq 0 ] || return 4
        return 2
    fi
    for expected_digest in "$@"; do
        [ "$actual_digest" != "$expected_digest" ] || return 0
    done

    restore_quarantined_managed "$LAST_QUARANTINE" "$target" \
        || restore_status=$?
    [ "$restore_status" -eq 0 ] || return 4
    return 1
}

chmod_managed() {
    chmod "$1" "$2"
    record_mutation "chmod:$2"
}

if [ "$INSTALL_PYTHON_MODULE_KEY" -eq 1 ]; then
    copy_managed "$STAGED_PYTHON_MODULE_KEY" "$PYTHON_MODULE_KEY"
    chmod_managed 600 "$PYTHON_MODULE_KEY"
fi

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
                remove_status=0
                remove_managed_digest "$stale_target" "$previous_digest" \
                    || remove_status=$?
                if [ "$remove_status" -eq 1 ]; then
                    echo "⚠️  Preserving modified stale module: $stale_target" >&2
                elif [ "$remove_status" -ne 0 ]; then
                    exit "$remove_status"
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
    chmod_managed +x "$target_dir/routing-audit.sh"
    chmod_managed +x "$target_dir/agent_council.py"

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

    # Migration authority for the retired sibling facade requires one signed
    # pre-ticket manifest that names both worker modules retired alongside it.
    # A current receipt alone is deliberately insufficient: historical bytes
    # placed after a post-ticket install remain user-owned.
    legacy_council_facade="$council_target_dir/scripts/council_review.py"
    if [ -f "$legacy_council_facade" ] && [ ! -L "$legacy_council_facade" ]; then
        legacy_advisory_owned=false
        legacy_debate_owned=false
        if [ "$PREVIOUS_MANIFEST_RECEIPT_AUTHENTICATED" -eq 1 ] \
            && [ -f "$previous_manifest" ]; then
            while IFS='|' read -r previous_digest previous_module_name; do
                case "$previous_module_name" in
                    advisory_consultation.py) legacy_advisory_owned=true ;;
                    debate_orchestrator.py) legacy_debate_owned=true ;;
                esac
            done < "$previous_manifest"
        fi
        if [ "$legacy_advisory_owned" != true ] \
            || [ "$legacy_debate_owned" != true ]; then
            echo "⚠️  Preserving retired council-review facade without pre-ticket migration authority: $legacy_council_facade" >&2
        else
            remove_status=0
            remove_managed_digest \
                "$legacy_council_facade" \
                "${LEGACY_COUNCIL_REVIEW_FACADE_DIGESTS[@]}" \
                || remove_status=$?
            if [ "$remove_status" -eq 1 ]; then
                echo "⚠️  Preserving customized retired council-review facade: $legacy_council_facade" >&2
            elif [ "$remove_status" -ne 0 ]; then
                exit "$remove_status"
            fi
        fi
    elif [ -e "$legacy_council_facade" ] || [ -L "$legacy_council_facade" ]; then
        echo "⚠️  Preserving non-regular retired council-review facade path: $legacy_council_facade" >&2
    fi

    # Remove retired council-review policy artifacts transactionally so
    # upgrades do not strand files that are no longer present in the source
    # skill. A failed install restores each existing artifact exactly as it was.
    for legacy_council_artifact in \
        "$council_target_dir/council-policy.json" \
        "$council_target_dir/references/council-policy.json"; do
        if [ -e "$legacy_council_artifact" ]; then
            quarantine_status=0
            quarantine_managed "$legacy_council_artifact" \
                || quarantine_status=$?
            if [ "$quarantine_status" -ne 0 ]; then
                exit "$quarantine_status"
            fi
        fi
    done
done

# Commit the installation-level provenance only after all five target
# manifests have been synchronized. A target-local manifest on its own can
# never authorize removal; every target must match this independent receipt.
copy_managed "$STAGING_DIR/python-module-receipt" "$PYTHON_MODULE_RECEIPT"

atomic_copy "$STAGING_DIR/claude-rule.md" "$CLAUDE_RULE"
for index in "${!DOCS[@]}"; do
    [ -n "${STAGED_DOCS[$index]}" ] || continue
    backup_once "${DOCS[$index]}"
    atomic_copy "${STAGED_DOCS[$index]}" "${DOCS[$index]}"
done

COMMITTED=1
echo "🎉 Installation complete."
echo "   Synchronized protocol source: $AGENTS_MD, $CLAUDE_MD, $GEMINI_MD"
