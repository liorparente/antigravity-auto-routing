# Auto Routing & Collaboration Protocol Upgrade Investigation

## 🚀 BLUF (Brief Line of Upfront Findings)
This investigation checked the worker-routing codebase (`install.sh`, `uninstall.sh`, `test_routing.py`, and configurations) against `~/.gemini/antigravity/knowledge/global-memory.md`. We identified two missing/outdated target directories (`.agent/skills/` and `.claude/rules/`), an outdated CLI tool reference (`gemini -p`), an outdated Claude Code CLI flag (`--dangerously-skip-permissions`), and a missing safe command pattern (`lsof`).

---

## 📂 Target Directories & Rules Files Analysis

### Comparison Matrix
The table below contrasts the directories supported in the current installation script with the unified guidelines in `global-memory.md`.

| Target Path | Supported in `install.sh` | Supported in `uninstall.sh` | Supported in `test_routing.py` | Role / Purpose (from Global Memory) | Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `~/.gemini/config/skills/worker-routing/` | Yes (L23) | Yes (L27) | Yes (L434) | Global Antigravity GUI skill directory | Active |
| `~/.codex/skills/worker-routing/` | Yes (L24) | Yes (L28) | Yes (L435) | Global Codex skill directory | Active |
| `.agents/skills/worker-routing/` | Yes (L25) | No (intentional) | Yes (L436) | Project-local skill folder for Antigravity GUI | Active |
| `.codex/skills/worker-routing/` | Yes (L26) | Yes (L29) | Yes (L437) | Project-local skill folder for Codex | Active |
| **`.agent/skills/`** | **No** | **No** | **No** | Project-local skill folder for **Antigravity CLI** | 🔴 **Missing** |
| **`.claude/rules/`** | **No** | **No** | **No** | Project-local rules directory for **Claude Code** | 🔴 **Missing** |

### Detailed Gaps

1. **`.agent/skills/` (Antigravity CLI local skills)**
   - **Source Requirement**: `global-memory.md` (L14) specifies `.agent/skills/` (singular) for Antigravity CLI, alongside `.agents/skills/` (plural) for Antigravity GUI.
   - **Current Codebase**: `install.sh` (L22-27), `uninstall.sh` (L26-30), and `test_routing.py` (L433-438) only reference `.agents/skills/worker-routing/`.
   - **Impact**: When the protocol is installed, local skill copies are not distributed to `.agent/skills/`, preventing CLI-based Antigravity runs from auto-discovering the `worker-routing` skill.

2. **`.claude/rules/` (Claude Code rules)**
   - **Source Requirement**: `global-memory.md` (L14) specifies `.claude/rules/` for Claude Code rule distribution.
   - **Current Codebase**: Totally unreferenced in `install.sh`, `uninstall.sh`, and `test_routing.py`.
   - **Impact**: Claude Code will not natively load the Worker Routing Protocol guidelines on startup, making it harder to enforce rules in Claude Code sessions.

---

## 💻 CLI Configurations, Models, & Safe Command Patterns

We audited the model configurations, patterns, and safety constraints in `routing-config.json` against global memory conventions and macOS requirements.

### 1. Deprecated `gemini -p` CLI Tool
- **Source**: `global-memory.md` (L68) explicitly states that the `gemini` CLI tool is deprecated and discontinued as of June 18, 2026. The recommended replacement is `agy` v1.0.0.
- **Current Configuration**: `routing-config.json` (L4-5) lists `gemini -p` under `context_specialist` patterns: `"patterns": ["agy", "gemini -p"]`.
- **Action**: Remove `gemini -p` from `routing-config.json` to prevent parsing of deprecated commands.

### 2. Outdated Claude Code CLI Flags
- **Source**: `global-memory.md` (L74) states that in Claude Code v2.x, the command flag was renamed from `--dangerously-skip-permissions` to `--allow-dangerously-skip-permissions`. Using the old flag causes a silent exit/hang.
- **Current Codebase**:
  - `skills/worker-routing/protocol.md` (L58) references: `claude -p --dangerously-skip-permissions`.
  - `skills/worker-routing/REFERENCE.md` (L26, L29) references: `--dangerously-skip-permissions`.
  - `skills/worker-routing/SKILL.md` (L48) references: `--dangerously-skip-permissions`.
- **Action**: Update all occurrences of `--dangerously-skip-permissions` to `--allow-dangerously-skip-permissions`.

### 3. Missing Safe Command Pattern: `lsof`
- **Source**: Custom Agent Rules (macOS Browser Automation Rule) require running `lsof -i :9222` to check if Chrome's debugging port is active.
- **Current Configuration**: `routing-config.json`'s `safe_commands` (L31-44) contains patterns for `ls`, `cat`, `grep`, `rg`, `git`, `curl`, `jq`, `which`, `echo`, `pwd`, `find`, and `unittest`, but does **not** allow `lsof`.
- **Impact**: If a developer or agent runs `lsof -i :9222`, `routing_check.py` will flag it as an **unrouted mutation violation** (unrouted command line that is not a worker call and is not safe).
- **Action**: Append `lsof` to `safe_commands` in `routing-config.json`.

---

## 🛠️ Required Codebase Changes

### 1. `install.sh`
- Add `.agent/skills/worker-routing` to `TARGET_DIRS` (L22-27).
- Add `.claude/rules/` support. Since `.claude/rules/` takes plain rules documents instead of a full skill folder structure, copy `protocol.md` to `$TARGET_PROJECT_DIR/.claude/rules/worker-routing.md` during installation.

### 2. `uninstall.sh`
- Clean up `$TARGET_PROJECT_DIR/.agent/skills/worker-routing` and `$TARGET_PROJECT_DIR/.claude/rules/worker-routing.md`.

### 3. `test_routing.py`
- Add unit/integration tests verifying that files are correctly copied to and removed from `.agent/skills/worker-routing` and `.claude/rules/worker-routing.md`.

### 4. `routing-config.json`
- Remove `"gemini -p"` from `context_specialist` patterns.
- Add `^\\s*lsof(?:\\s+-[a-zA-Z0-9:]+)?(?:\\s+[^>|&;]+)?\\s*$` to `safe_commands`.
