---
name: learn-session
description: Scan full conversation history, extract strategic insights, and batch-sync them across institutional memory, CONTEXT.md, ERRORS.md, AGENTS.md, and all installed harnesses. Trigger on /learn-session or /learn session.
---

# 🧠 Institutional Memory Builder - Session Review & Core Sync Mode

**Trigger:** `/learn-session` or `/learn session`

**Goal:** Scan the entire conversation history, extract strategic insights, classify them, and batch-sync them across the appropriate institutional-memory, glossary, failure-log, and workflow-rule stores. In catalog-backed workspaces, the structured rule catalog is the source of truth and the Markdown memory is regenerated from it.

---

## Step 1: 🔭 Full Conversation Scan & Auto-Classification

Scan the entire conversation history from start to current point. Extract up to 7 key insights and classify each:

### 1. Scope (Select exactly one)
- `[global]` — Cross-project rules, MCP tools, agent orchestration conventions -> Target: `~/.gemini/antigravity/knowledge/global-memory.md`
- `[local]` — Workspace-specific gotchas, architecture, patterns, or rules -> Target: Workspace files

### 2. Category (Select exactly one)
- `architecture` — System design, data models, API contracts -> Institutional memory + ADR check (`docs/adr/`)
- `gotcha` — Bugs, traps, footguns -> Institutional memory + `ERRORS.md`
- `domain` — Domain terms, ubiquitous language -> `CONTEXT.md`
- `workflow` / `rule` — Dev process, CI/CD, agent routing rules -> `AGENTS.md` / `PROJECT_RULES.md`
- `pattern` / `preference` — Reusable code patterns or styling conventions -> Institutional memory

### 3. Importance Rating (1-5)
- **5 (Critical)**: High-risk regression or major structural insight.
- **4 (Important)**: Saves significant debugging time or establishes durable pattern.
- **3-1**: Useful to minor preference.

**Completion Criterion:** A numbered list of at most 7 classified insights with target files identified. Continue directly to the batch write; this skill has no approval checkpoint.

---

## Step 2: ✍️ Batch Write

For each insight, apply the appropriate update:

### Institutional Memory
- **`[global]`**: Update `~/.gemini/antigravity/knowledge/global-memory.md`.
- **`[local]` in a catalog-backed workspace**:

  1. Add the new `GoldenRule` entry to `GOLDEN_RULES` in `skills/worker-routing/prompt_assembler.py` (with the next sequential ID, category, keywords, file_patterns, title, and directive).
  2. Run `python3 skills/worker-routing/regenerate_institutional_memory.py` to regenerate `knowledge/institutional-memory.md`.

  Never write directly to `knowledge/institutional-memory.md`; it is generated output.
- **`[local]` outside a catalog-backed workspace**: Update that workspace's institutional-memory store according to its documented ownership model.
- If category is `architecture`, importance is at least 4, and the Pocock 3-Condition Rule passes, offer to generate `docs/adr/NNNN-title.md`.

### Domain Glossary
- For a `domain` insight or a new ubiquitous term, update `CONTEXT.md` inline, following the Glossary-Only rule.

### Failure Log
- For a `gotcha` or non-trivial failure, update `ERRORS.md` with what was attempted, what failed, the signal that proved it, and what worked instead.

### Durable Workflow Rules
- For a `workflow` / `rule` insight or another durable constraint, update `AGENTS.md` or `PROJECT_RULES.md`.

**Completion Criterion:** All classified insights are written to their authoritative targets; any catalog-backed local memory change has regenerated `knowledge/institutional-memory.md`.

---

## Step 3: 🔄 Multi-Harness Sync (`install.sh`)

If `./install.sh` exists and is executable in the workspace root, execute it to synchronize managed skills and protocol markers across the configured harnesses.

**Completion Criterion:** `./install.sh` exits 0, confirming multi-harness alignment.

---

## Step 4: 📡 NotebookLM MCP Sync

For each file updated in Step 2, verify NotebookLM MCP availability and, when available, update the corresponding notebook source with the revised content.

**Completion Criterion:** Updated sources are re-synced, or unavailable NotebookLM MCP is reported without blocking the completed local writes.

---

## Output Summary (BLUF)

Report a concise summary (max 25 lines):
- **Insights Persisted:** Count of insights written.
- **Files Updated:** Clickable links to modified files.
- **Multi-Harness Sync Status:** Result of `./install.sh`.
- **NotebookLM Sync Status:** Re-synced sources or MCP unavailability.
