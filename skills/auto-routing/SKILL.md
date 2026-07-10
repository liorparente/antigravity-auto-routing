---
name: Auto Routing Protocol
description: "Antigravity = expensive orchestrator. Its ONLY job: assess task complexity and route to the cheapest capable worker. It never writes code or runs tasks itself. Every execution — including QA and review — is delegated to a worker model. Tokens saved = cost saved."
---

# Auto Routing & Collaboration Protocol v3.0

This protocol defines the multi-model agent hierarchy, routing matrix, and collaborative workflows. Antigravity acts as a **pure orchestrator**, delegating all context gathering, planning, execution, and verification steps to specialized models to optimize performance, cost, and speed.

---

## 👥 The Agent Mesh & Roles

| Role | Primary Model | CLI / Interface | Operational Purpose |
| :--- | :--- | :--- | :--- |
| **Orchestrator** | Claude Code / Codex | Active Workspace CLI | Parses user requests, decomposes tasks into `task.md`, and orchestrates the worker pipeline. **Strictly prohibited from self-executing code/commands.** |
| **Context Specialist** | `agy` (Gemini 3.5 Flash) | `agy -p` (PTY wrapped) | Performs semantic code searches, parses massive repositories, and generates distilled context briefs (1,000–2,000 tokens) to keep planners` context clean. |
| **Planner / Thinker** | Claude Fable 5 / Opus 4.8 | `claude -p --model <model>` | Receives distilled context, designs architectural specs, and writes implementation plans (ICoT). |
| **Critic / Peer Reviewer** | Codex 5.6 Sol | `codex exec` | Peer-reviews Planner drafts, flags edge cases, verifies logic consistency, and approves final plans. |
| **Heavy Doer** | Claude Sonnet 5 | `claude -p` | Executes complex, multi-file code modifications, refactorings, and logic implementation. |
| **Light Doer** | Codex 5.6 Terra / Luna | `codex exec` | Implements simple steps, boilerplate code, formats files, and writes unit tests. |
| **Local / Sensitive Doer** | LM Studio (Qwen 30B / Gemma) | Local API (`127.0.0.1:1234`) | Executes tasks involving PII, credentials, or proprietary logic. Also acts as an offline fallback. |
| **QA / Auditor** | Codex 5.6 Sol | `codex review` | Audits the final workspace state and uncommitted changes (`codex review --uncommitted`). |

---

## 🔄 Task Lifecycle & Collaboration Pipeline

For every non-trivial task, the Orchestrator must run the following sequential lifecycle:

```
[Context Specialist (agy)] ➔ [Planner (Fable 5)] ➔ [Critic (Codex Sol)] ➔ [Orchestration (task.md)] ➔ [Execution (Sonnet/Terra/Local)] ➔ [QA (Codex Sol)]
```

### Phase 1: Context Distillation
Before planning begins, the Orchestrator invokes `agy` (Gemini 3.5 Flash) to gather relevant codebase parts.
* **Goal:** Avoid polluting the Planner's context with raw files.
* **Command:** `script -q /dev/null agy -p "Scan the codebase and locate all references to {TOPIC}. Output a distilled context summary."`

### Phase 2: Planner-Critic Consensus Loop (System 2 Planning)
For all Medium and Complex tasks, planning must undergo peer review before execution:
1. **Drafting:** The **Planner** (Claude Fable 5 / Opus 4.8) writes a proposed implementation plan to `.claude/plan_draft.md`.
2. **Review:** The **Critic** (Codex 5.6 Sol) reviews the draft plan.
   * **Command:** `cat .claude/plan_draft.md | codex exec "Review this plan. Check for edge cases, performance bottlenecks, and architectural violations."`
3. **Refinement:** The Planner integrates the Critic's feedback, producing the final `implementation_plan.md` for user approval.

### Phase 3: Task Decomposition
Upon user approval, the Orchestrator initializes `task.md` with structured sub-tasks.
* **Flow State Hygiene:** Run `/clear` to clear current context before starting a new feature, ensuring fresh execution free of historical memory noise.

### Phase 4: Dynamic Execution (System 1 vs System 2 Routing)
The Orchestrator processes the `task.md` checklist, routing individual sub-tasks dynamically:
* **Trivial/Boilerplate Steps:** Route to **Codex 5.6 Luna** or local **LM Studio** models directly.
* **Complex Logic/Multi-File Steps:** Route to **Claude Sonnet 5** using alternating instruction-execution (`Hi-CoT`) blocks.

### Phase 5: Verification & QA
* The **Doer** runs local unit tests.
* The Orchestrator invokes **Codex 5.6 Sol** for a final audit of the diff:
  * **Command:** `codex review --uncommitted -s workspace-write -c model_reasoning_effort="medium"`

---

## 📊 Difficulty-Aware Routing Matrix

| Task Complexity | Signs | Assigned Worker | Execution Strategy |
| :--- | :--- | :--- | :--- |
| **Trivial** | Single-file edits, comments, formatting, quick Q&A. | **Codex 5.6 Luna** or local **Gemma 4 E4B** | **Direct Generation:** Skip planning/CoT to prevent overthinking and save tokens. |
| **Simple** | Boilerplate creation, unit tests, simple logic (1-2 files). | **Codex 5.6 Terra** or local **Qwen3 Coder 30B** | **System 1 Few-Shot:** Generate directly with minimal instructions. |
| **Medium** | New features, refactoring 3–4 files, API integration. | **Planner:** Claude Sonnet 5<br>**Executor:** Claude Sonnet 5 | **ICoT / Thinker-Executor:** Define specs and ideas first, then implement. |
| **Complex** | Architectural shifts, refactoring 5+ files, core algorithm design. | **Planner:** Claude Fable 5 / Opus 4.8<br>**Critic:** Codex 5.6 Sol<br>**Executor:** Claude Sonnet 5 | **Consensus Loop + Hi-CoT:** Alternating plan-execution cycles to avoid drift. |
| **Sensitive** | PII, credentials, database connection strings, security logic. | **LM Studio** (Local Models only) | **Local Inference Flow:** Enforce zero-leakage offline boundaries. |

---

## 💻 CLI Command Reference

### 1. Antigravity CLI (agy) - Gemini 3.5 Flash
*Always wrap with `script -q /dev/null` to allocate a PTY and prevent CLI hangs.*
```bash
# Codebase scanning & context distillation
script -q /dev/null agy -p "Summarize how authentication is handled across the repository" --output-format markdown

# Large file parsing (e.g., PDFs, large logs)
script -q /dev/null agy -p "Extract API schemas from this spec document" -i /path/to/spec.pdf
```

### 2. Claude Code CLI
```bash
# Complex implementation (Fable 5 / Sonnet 5)
claude -p --dangerously-skip-permissions "Implement the user profile component following our design tokens"

# Opus-tier architectural research
claude -p --model claude-opus-4-8 --dangerously-skip-permissions "Draft a migration plan for the database schema"
```

### 3. Codex CLI (v0.125+)
*Always specify `-c model_reasoning_effort="low"` or `"medium"` to prevent ChatGPT timeouts.*
```bash
# Plan critique (Consensus step)
codex exec "Analyze this implementation plan: $(cat .claude/plan_draft.md)"

# Code review (QA step)
codex review --uncommitted -s workspace-write -c model_reasoning_effort="medium"
```

### 4. Local Models (LM Studio API)
*Use the REST API to load and unload models to preserve system RAM.*
```bash
# 1. Load model (qwen/qwen3-coder-30b or gemma-4-e4b-it-mlx)
curl -s -X POST http://127.0.0.1:1234/api/v1/models/load \
  -H "Content-Type: application/json" \
  -d '{"model": "qwen/qwen3-coder-30b"}' > /dev/null

# 2. Run inference
curl -s http://127.0.0.1:1234/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen/qwen3-coder-30b",
    "messages": [{"role": "user", "content": "Write a TypeScript debounce function"}],
    "temperature": 0.2
  }' | jq -r '.choices[0].message.content'

# 3. Unload model (mandatory)
curl -s -X POST http://127.0.0.1:1234/api/v1/models/unload \
  -H "Content-Type: application/json" \
  -d '{"instance_id": "qwen/qwen3-coder-30b"}' > /dev/null
```

---

## ⛔ Enforcement & Anti-Patterns

1. **No Self-Execution:** Antigravity must never edit code files (`.ts`, `.tsx`, `.js`, `.css`) or run compilation/build commands directly in its own workspace. Direct execution is a protocol violation.
2. **Positional Risk-Aware QA:** Verify the code using Codex review at the end of the feature cycle. If a bug fails to resolve after 2 fix attempts, escalate to Codex Sol with full repro logs.
3. **No Dangling Local Models:** Always unload LM Studio models immediately after inference. Leaving 30B+ models loaded will exhaust system memory.
4. **Flow State Context Cleaning:** Always run `/clear` when transitioning between roadmap features.
5. **No Routing Overhead Exemptions:** If a task can be performed by a worker model, it must be routed.

---
*Auto Routing & Collaboration Protocol v3.0 - 2026-07-10*
*Orchestrator = Dispatcher. Workers = Execution. Content = Distilled.*
