---
name: Auto Routing Protocol
description: "Antigravity = expensive orchestrator. Its ONLY job: assess task complexity and route to the cheapest capable worker. It never writes code or runs tasks itself. Every execution — including QA and review — is delegated to a worker model. Tokens saved = cost saved."
---

# Auto Routing & Collaboration Protocol v3.3

This protocol defines the multi-model agent hierarchy and collaborative workflows. Antigravity acts as a **pure orchestrator**, delegating all context gathering, planning, execution, and verification steps to specialized models to optimize performance, cost, and speed.

The hard-enforced gate, response template, and complexity/routing matrix live in [`protocol.md`](protocol.md) — the single source of truth also injected verbatim into `AGENTS.md`, `CLAUDE.md`, and `GEMINI.md`. This file covers everything protocol.md doesn't: the full agent mesh, the task lifecycle, and CLI command syntax for each worker.

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

> **Sensitive-tier exception:** the fallback chain in [`protocol.md`](protocol.md) does not apply to Sensitive-tier tasks — if LM Studio (local) is unavailable, the task must fail closed immediately rather than escalating to any other worker.

---

## 🔄 Task Lifecycle & Collaboration Pipeline

For every non-trivial task, the Orchestrator must run the following sequential lifecycle:

```
[Context Specialist (agy)] ➔ [Planner (Fable 5)] ➔ [Critic (Codex Sol)] ➔ [Orchestration (task.md)] ➔ [Execution (Sonnet/Terra/Local)] ➔ [QA (Codex Sol)]
```

### Phase 1: Context Distillation
Before planning begins, the Orchestrator invokes `agy` (Gemini 3.5 Flash) to gather relevant codebase parts.
* **Goal:** Avoid polluting the Planner's context with raw files.
* **Command:** `IN_WORKER_ROUTING=true script -q /dev/null agy -p "Scan the codebase and locate all references to {TOPIC}. Output a distilled context summary."`

### Phase 2: Planner-Critic Consensus Loop (System 2 Planning)
For all Medium and Complex tasks, planning must undergo peer review before execution:
1. **Drafting:** The **Planner** (Claude Fable 5 / Opus 4.8) writes a proposed implementation plan to `.claude/plan_draft.md`.
2. **Autonomous Debate Loop:** The **Critic** (Codex 5.6 Sol) reviews the draft plan, and the **Planner** updates the plan to resolve objections. This loop runs autonomously for up to 3 rounds, logging the entire discussion to `.scratch/planning_debate.md`.
   * **Command (Critic Review):** `cat .claude/plan_draft.md | IN_WORKER_ROUTING=true codex exec --model gpt-5.6-sol -c model_reasoning_effort="medium" "Review this plan. Check for edge cases, performance bottlenecks, and architectural violations."`
3. **Plan Delivery & Consensus Audit:**
   * If the Critic approves the plan, the final resolved plan is saved to `implementation_plan.md` for user approval.
   * If the 3-round limit is reached without Critic approval, the plan is written to `implementation_plan.md` but clearly demarcated as a **Disputed Plan (No Consensus)**. The orchestrator must halt and request manual user intervention.

### Phase 3: Task Decomposition
Upon user approval, the Orchestrator initializes `task.md` with structured sub-tasks.
* **Flow State Hygiene:** Run `/clear` to clear current context before starting a new feature, ensuring fresh execution free of historical memory noise.

### Phase 4: Dynamic Execution (System 1 vs System 2 Routing)
The Orchestrator processes the `task.md` checklist, routing individual sub-tasks dynamically:
* **Trivial/Boilerplate Steps:** Route to **Codex 5.6 Luna** or local **LM Studio** models (if already loaded) directly.
* **Complex Logic/Multi-File Steps:** Route to **Claude Sonnet 5** using alternating instruction-execution (`Hi-CoT`) blocks.

### Phase 5: Verification & QA
* The **Doer** runs local unit tests.
* The Orchestrator invokes **Codex 5.6 Sol** for a final audit of the diff:
  * **Command:** `IN_WORKER_ROUTING=true codex review --uncommitted -s workspace-write -c model="gpt-5.6-sol" -c model_reasoning_effort="medium"`

---

## 📊 Difficulty-Aware Routing Matrix

The authoritative Trivial → Sensitive routing matrix — which worker handles which complexity tier — lives in [`protocol.md`](protocol.md), the single source of truth that is also enforced live via `AGENTS.md`/`CLAUDE.md`/`GEMINI.md`. Edit it there; this file and `README.md` only reference it now, so the copies can't drift out of sync the way this table and `protocol.md`'s used to.

---

## 💻 CLI Command Reference

See the full CLI usage templates, REST API curls, and verification commands in the [REFERENCE.md](REFERENCE.md) documentation.

---

## ⛔ Enforcement & Anti-Patterns

1. **No Self-Execution:** Antigravity must never edit code files (`.ts`, `.tsx`, `.js`, `.css`) or run compilation/build commands directly in its own workspace. Direct execution is a protocol violation.
2. **Positional Risk-Aware QA:** Verify the code using Codex review at the end of the feature cycle. If a bug fails to resolve after 2 fix attempts, escalate to Codex Sol with full repro logs.
3. **No Dangling Local Models:** Always unload LM Studio models immediately after inference. Leaving 30B+ models loaded will exhaust system memory.
4. **Flow State Context Cleaning:** Always run `/clear` when transitioning between roadmap features.
5. **No Routing Overhead Exemptions:** If a task can be performed by a worker model, it must be routed.

---
*Auto Routing & Collaboration Protocol v3.3 - 2026-07-10*
*Orchestrator = Dispatcher. Workers = Execution. Content = Distilled.*
