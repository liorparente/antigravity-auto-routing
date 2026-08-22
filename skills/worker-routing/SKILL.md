---
name: Auto Routing Protocol
description: "Antigravity = pure orchestrator for Maximum Quality & Zero-Defect Execution ('Perfect Score Standard'). Every mission begins with deep research (agy) and deep thinking (Claude/Codex Sol), calibrating worker reasoning effort (low/medium/high/ultra) to guarantee top-tier performance. Use when orchestrating complex multi-agent tasks, performing worker routing calibration, running Agent Council reviews, or managing model effort tiers."
---

# Auto Routing & Collaboration Protocol v3.6 (Quality-First Standard)

This protocol defines the multi-model agent hierarchy and collaborative workflows. Antigravity acts as a **pure orchestrator**, delegating all context gathering, planning, execution, and verification steps to specialized models to optimize accuracy, structural soundness, and performance score.

The hard-enforced gate, response template, and quality/effort matrix live in [`protocol.md`](protocol.md) — the single source of truth also injected verbatim into `AGENTS.md`, `CLAUDE.md`, and `GEMINI.md`.

---

## 👥 The Agent Mesh & Roles

| Role | Primary Model | CLI / Interface | Operational Purpose |
| :--- | :--- | :--- | :--- |
| **Orchestrator** | Claude Code / Codex | Active Workspace CLI | Parses user requests, decomposes tasks into `task.md`, and orchestrates the worker pipeline. **Strictly prohibited from self-executing code/commands.** |
| **Deep Context Specialist** | `agy` (**Gemini 3.6 Flash** / **Gemini 3.1 Pro**) | `agy -p` (PTY wrapped) | Performs deep semantic code searches, parses massive repositories, maps dependencies, and generates distilled context briefs (1,000–2,000 tokens). |
| **Planner / Deep Thinker** | **Claude Opus 5 (Thinking)** / Claude Fable 5 | `claude -p --no-session-persistence --model` | Receives distilled context, performs deep reasoning, designs architectural specs, and writes implementation plans (ICoT). |
| **Critic / Peer Reviewer** | Codex 5.6 Sol / **GPT-OSS 120B (Medium)** | `codex exec` | Peer-reviews Planner drafts with calibrated reasoning effort (`medium`/`high`/`ultra`), flags edge cases, verifies logic consistency, and approves final plans. |
| **Heavy Doer** | **Claude Sonnet 5 (Thinking)** / Sonnet 5 | `claude -p --no-session-persistence` | Executes complex, multi-file code modifications, refactorings, and logic implementation. |
| **Light Doer** | Codex 5.6 Terra / Luna / **Gemini 3.6 Flash (Low)** | `codex exec` / `agy` | Implements simple steps, boilerplate code, formats files, and writes unit tests with calibrated effort. |
| **Local / Sensitive Doer** | LM Studio (Qwen 30B / Gemma 4 E4B) | Local API (`127.0.0.1:1234`) | Executes tasks involving PII, credentials, or proprietary logic. Performs deep local validation. |
| **QA / Auditor** | Codex 5.6 Sol / **Claude Opus 5 (Thinking)** | `codex review` | Audits the final workspace state and uncommitted changes (`codex review --uncommitted` with `high` effort). |

---

## 🔄 Task Lifecycle & Collaboration Pipeline (Quality-First)

For every non-trivial task, the Orchestrator runs the following sequential pipeline:

```
[Deep Research (agy - Gemini 3.6 Flash/3.1 Pro)] ➔ [Planner (Claude Opus 5 Thinking)] ➔ [Critic (Codex Sol / GPT-OSS 120B)] ➔ [Orchestration (task.md)] ➔ [Execution (Sonnet 5 Thinking / Terra / Local)] ➔ [Zero-Defect QA (Codex Sol)]
```

All Phase 0–3 external CLI worker invocations must follow [Rule 4.7 in `protocol.md`](protocol.md#routing-behavior).

### Phase 0: Deep Research & Context Distillation
Before any code or plan is written, the Orchestrator invokes `agy` (Gemini 3.6 Flash / 3.1 Pro) to perform a comprehensive codebase research pass.
* **Goal:** Understand existing contracts, edge cases, dependencies, and side effects.
* **Command:** `IN_WORKER_ROUTING=true script -q /dev/null agy -p "[WORKER-MODE: NESTED-EXEC] Perform deep research on {TOPIC}. Map out all affected files, imports, exported interfaces, and potential breaking changes."`

### Phase 1: Deep Thinking & Planner-Critic Consensus Loop (System 2 Planning)
For all Medium and Complex tasks, planning undergoes deep reasoning and peer review:
1. **Drafting:** The **Planner** (Claude Opus 5 Thinking / Fable 5) designs an interface-first implementation plan.
2. **Autonomous Debate Loop:** The **Critic** (Codex 5.6 Sol / GPT-OSS 120B) reviews the draft plan using `medium`/`high` reasoning effort, flagging missing edge cases or performance flaws. Up to 3 rounds until consensus.
   * **Command:** `cat .claude/plan_draft.md | IN_WORKER_ROUTING=true codex exec --model gpt-5.6-sol -c model_reasoning_effort="high" "[WORKER-MODE: NESTED-EXEC] Perform deep review of this plan. Check for race conditions, type safety, edge cases, and performance." < /dev/null`
3. **Consensus Delivery:** Save final approved plan to `implementation_plan.md` for user approval.

### Phase 2: Task Decomposition & Execution
Upon user approval, the Orchestrator initializes `task.md` with structured sub-tasks:
* Route sub-tasks dynamically using the **Calibrated Complexity & Effort Matrix** in `protocol.md`.
* Choose the appropriate effort level (`low`, `medium`, `high`, `ultra`) to guarantee 100% correctness without compromises.

### Phase 3: Zero-Defect Verification & QA
* The **Doer** runs local unit/integration tests to verify behavior.
* The Orchestrator invokes **Codex 5.6 Sol** with `high` effort for a final audit of the diff:
  * **Command:** `IN_WORKER_ROUTING=true codex review --uncommitted -c sandbox_mode="workspace-write" -c model="gpt-5.6-sol" -c model_reasoning_effort="high" "[WORKER-MODE: NESTED-EXEC] ..." < /dev/null`

---

## 📊 Calibrated Effort Matrix

See [`protocol.md`](protocol.md) for the authoritative Quality-First complexity matrix and effort mappings.

---

## 📓 Learning-Journal Ground-Truth Recording

`advisory_consultation.py` records one fact by itself: `plan=accepted`, and only when a
plan-producing dialogue (`ambiguity` or `plan-review`) reached consensus. That record says the
dialogue's Critic approved the plan — **not** that you did. Every other ground truth in this family
becomes known outside any process this repository controls, so the Orchestrator is the one who
records it: test results, review verdicts, stalemate resolutions, **and a plan you rejected** —
`learning_outcomes.py`'s `record_test_result` / `record_review_verdict` /
`record_stalemate_resolution` / `record_plan_outcome`, each once its fact is known. Accepted plans
record themselves and rejected ones do not, so the hand-recorded half is what keeps the family from
drifting toward "every plan was accepted". See [`protocol.md`](protocol.md#-learning-journal-ground-truth-recording-spec-0004-ticket-25) for exactly which step, which producer, and why.

---

## ⛔ Enforcement & Anti-Patterns

1. **No Rushed Execution:** Never skip Phase 0 (Deep Research) or Phase 1 (Deep Thinking) just to save time or tokens.
2. **No Self-Execution:** Antigravity must never edit code files (`.ts`, `.tsx`, `.js`, `.css`) directly.
3. **Perfect Score Verification:** Always run QA review with Codex 5.6 Sol before declaring a task finished.
4. **Flow State Context Cleaning:** Run `/clear` when transitioning between major feature tasks.

---
*Auto Routing & Collaboration Protocol v3.6 - Quality-First Standard*
