#!/bin/bash
# install.sh — installs the Auto Routing & Collaboration Protocol
# Idempotent: safe to run multiple times.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="$SCRIPT_DIR/skills/worker-routing"
TARGET_DIRS=(
    "$HOME/.gemini/config/skills/worker-routing"
    "$HOME/.codex/skills/worker-routing"
    "$SCRIPT_DIR/.agents/skills/worker-routing"
    "$SCRIPT_DIR/.agent/skills/worker-routing"
    "$SCRIPT_DIR/.codex/skills/worker-routing"
)
GEMINI_MD="$HOME/.gemini/GEMINI.md"

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
    chmod +x "$target_dir/routing-audit.sh"
    echo "✅ Copied SKILL.md, routing-audit.sh, routing_check.py"

    if [ -f "$target_dir/routing-config.json" ]; then
        echo "⏭️  routing-config.json already exists in $target_dir — skipping copy to preserve customizations."
    else
        cp "$SRC_DIR/routing-config.json" "$target_dir/routing-config.json"
        echo "✅ Copied routing-config.json"
    fi
}

# Remove any existing protocol block from a GEMINI.md-like file, whether it
# was written with the new versionless markers or the legacy v3.0 heading.
# Leaves the rest of the file untouched.
strip_existing_block() {
    local file="$1"

    if grep -qF "$PROTOCOL_START" "$file" 2>/dev/null; then
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

# 1. Copy skill files to all supported global and local agent targets.
for target_dir in "${TARGET_DIRS[@]}"; do
    install_skill_files "$target_dir"
done

# 2. Inject/refresh the Worker Routing Protocol block in GEMINI.md.
mkdir -p "$(dirname "$GEMINI_MD")"
touch "$GEMINI_MD"

cp "$GEMINI_MD" "$GEMINI_MD.bak"
echo "🗄️  Backed up $GEMINI_MD to $GEMINI_MD.bak"

if grep -qF "$LEGACY_MARKER" "$GEMINI_MD" 2>/dev/null; then
    echo "🔄 Legacy v3.0 protocol block detected — upgrading to versionless markers."
fi

strip_existing_block "$GEMINI_MD"

# Trim trailing blank lines left over after stripping so re-running the
# installer doesn't accumulate blank lines before the re-injected block.
while [ -s "$GEMINI_MD" ] && [ -z "$(tail -n 1 "$GEMINI_MD")" ]; do
    sed -i.tmp '$d' "$GEMINI_MD"
    rm -f "$GEMINI_MD.tmp"
done

echo "📝 Writing Worker Routing Protocol to $GEMINI_MD"
{
    echo ""
    echo "$PROTOCOL_START"
    cat << 'PROTOCOL_EOF'

## Worker Routing Protocol (HARD ENFORCED)

Antigravity is a **pure orchestrator**. Its only job: assess complexity → pick worker → collect output.
Self-execution of code/commands is a **protocol violation**, not a fallback option.

### ⛔ HARD GATE — Before ANY State-Modifying Action

Before using `write_to_file`, `replace_file_content`, `multi_replace_file_content`, or `run_command` (non-read-only), if the environment variable `IN_WORKER_ROUTING` is NOT set to `true`, execute this internal check:

1. **Self-Check:** Ask internally — *"Can a worker do this?"*
   - YES → route to worker. Do not proceed with direct execution.
   - NO → state explicitly why no worker is suitable, then ask user for permission.
2. **Declare routing:** `[ROUTING: {worker} — complexity: {level} — reason: {1 sentence}]`
3. **Compose Mission Brief** (required for Medium/Complex tasks):
   - **Goal:** One sentence objective
   - **Success Criteria:** Measurable definition of done
   - **Constraints:** What must NOT be touched
   - **Context:** KI reference or conversation ID
4. **Execute via worker CLI.** Never execute directly without explicit user approval.

### 🔒 Mandatory Response Template (STRUCTURAL — Not Optional)
The **FIRST LINE** of every response MUST be exactly one of:
```
[ROUTING: Direct — reason: {allowed exception from list below}]
[ROUTING: {worker} — complexity: {level} — reason: {1 sentence}]
```
A response that modifies state without a `[ROUTING:]` first line is **structurally invalid**.
If the self-check answer is YES (a worker can do it) but you are about to self-execute anyway — STOP and output:
```
[ROUTING: BLOCKED — a worker should handle this. Halting.]
```
Then ask the user how to proceed.

### 📋 Post-Session Audit
All sessions are auditable via: `~/.gemini/config/skills/worker-routing/routing-audit.sh [conversation-id]`
This script detects source code edits made without worker routing. Violations are flagged automatically.

### ✅ Allowed Direct Actions (No Worker, No Gate)
- Reading/analyzing files (`view_file`, `grep_search`, `list_dir`, `read_url_content`) — **EXCEPT Code Reviews (must route to Codex)**
- Answering questions, planning, conversation
- Creating/editing **documentation & visualization artifacts** (`.md` and `.html` files — not `.ts`, `.tsx`, `.css`, `.js`)
- Read-only diagnostics (`git status`, `git log`, `curl` health checks)
- MCP tool calls (NotebookLM, GA4, GSC, Stitch — these are tools, not code output)
- `browser_subagent` for UI inspection/QA
- `/handoff` output (temp .md file, not committed to repo) and `/prototype` throwaway files (local only)
- Executing when the environment variable `IN_WORKER_ROUTING` is set to `true` (nested worker execution)

### Complexity Matrix — Pick Worker Automatically
| Complexity | Signs | Route To |
|---|---|---|
| **Trivial** | Single file, rename, format, quick Q&A | **Codex 5.6 Luna** or local **Gemma 4 E4B** |
| **Simple** | 1-2 files, boilerplate, simple logic | **Codex 5.6 Terra** or local **Qwen3 Coder 30B** |
| **Medium** | 3-4 files, new feature | **Claude Sonnet 5** (`claude -p --dangerously-skip-permissions`) |
| **Complex** | 5+ files, architectural impact | **Planner:** Claude Fable 5 / Opus 4.8 <br> **Critic:** Codex 5.6 Sol <br> **Executor:** Claude Sonnet 5 |
| **Sensitive** | PII, medical, credentials | **LM Studio** ALWAYS (local only) |
| **Review/QA** | Post-feature audit | **Codex 5.6 Sol** (`codex review --uncommitted -s workspace-write -c model_reasoning_effort="medium"`) |
| **Context/Search** | Codebase scan, log parsing | **Antigravity CLI** (`agy`) with Gemini 3.5 Flash |

### Routing Behavior
1. **Silent availability check:** Before routing, verify the target worker is reachable (e.g., `curl -s http://127.0.0.1:1234/api/v0/models` for LM Studio). Do this silently.
2. **If worker is unreachable:** HALT. Report which worker is down and the fix. Do NOT silently self-execute.
3. **Audit trail:** Every response that involves any action must start with `[ROUTING: {worker} — reason: {why}]` or `[ROUTING: Direct — reason: {allowed exception}]`.
3.5. **Fallback Chain (on worker unavailability):** Local (LM Studio down) → escalate one tier up. API worker fails → try alternate API model. Full fallback order: Gemma E4B → Qwen Coder → Claude Code → agy Flash → agy Pro → manual. Log every fallback to ERRORS.md with reason.
4. **Codex Sandbox Modes:** Always pick the right `-s` flag — wrong mode = blocked writes. `read-only`: pure analysis only. `workspace-write`: applying patches or fixes within the repo (default for Review/QA). `danger-full-access`: unrestricted system writes. Never use `read-only` when Codex needs to write files.
5. **Full reference:** See `~/.gemini/config/skills/worker-routing/SKILL.md` for CLI syntax and edge cases.

### Pushback Protocol (Bidirectional)
Antigravity is authorized — and **required** — to refuse:
- Direct self-execution when a worker is available → "I must route this to {worker}."
- Opus-tier model for trivial tasks → recommend Flash/local downgrade
- Execution without Mission Brief for Complex tasks → request the brief first
- User raw data dump >20 lines without filtering → request a filtered version

### Escalation Triggers (Advisor Strategy)
When operating as a "tier 1/2" model (e.g. Flash or Sonnet) and encountering any of the following triggers, **STOP and recommend a model upgrade**. Do not attempt to force a solution:
1. **Architecture Decisions:** Choosing between competing architectural patterns or generating complex plans (e.g., `/plan`).
2. **Multi-File Refactors:** Code changes impacting 5+ interdependent files.
3. **Ambiguity Loops:** Failing to resolve the same issue after 2 distinct approaches. If stuck, generate a Consultation Request: summarize the problem, what was tried, and what's blocking — then escalate.
4. **Security / Data Risks:** Any operations touching Auth, RLS, production secrets, or potentially destructive actions.

Conversely, when operating as a "tier 3" model (e.g. Opus) and receiving a trivial task (such as drafting a `/note` or summarizing meetings) — **recommend downgrading to a cheaper model** to conserve resources.
PROTOCOL_EOF
    echo "$PROTOCOL_END"
} >> "$GEMINI_MD"
echo "✅ Worker Routing Protocol installed."

echo "---"
echo "🎉 Installation complete."
echo "   Skill files installed to:"
for target_dir in "${TARGET_DIRS[@]}"; do
    echo "     - $target_dir"
done
echo "   Protocol doc:  $GEMINI_MD (backup: $GEMINI_MD.bak)"
echo "   Run audits with: ${TARGET_DIRS[0]}/routing-audit.sh [conversation-id]"
