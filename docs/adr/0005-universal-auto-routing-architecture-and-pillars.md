# ADR 0005: Universal Auto-Routing Architecture & The Four Pillars

* **Status:** Approved (Updated with Dynamic Escalation Protocol)
* **Date:** 2026-08-09
* **Context:** Expansion of the auto-routing framework from an Antigravity-only protocol to a universal multi-harness routing system spanning Claude Code, OpenAI Codex CLI, and Google Antigravity IDE.

## Core Mission Statement
To transform any AI coding workspace (including Codex, Claude Code, and Antigravity) into an autonomous, intelligent routing network that guarantees maximum quality and zero code defects (**Perfect Score Standard**) through calibrated task distribution across specialized LLMs.

---

## The Four Pillars

### 1. Universal Pure Orchestration & Permission Safety
* **Pure Orchestrator:** The primary session agent acts strictly as an orchestrator across all three CLI environments (Codex, Claude Code, Antigravity) and is gated from direct code modifications.
* **Multi-Harness Permission Alignment:** Cross-CLI configuration consistency is maintained via a single setup script (`install.sh`) synchronizing `AGENTS.md`, `CLAUDE.md`, and `~/.gemini/GEMINI.md`, backed by a lightweight runtime sanity check at session start.

### 2. Dynamic Routing Matrix, Fail-Safe Policy & Autonomous Escalation
* **Flexible Model & Effort Matrix:** The Orchestrator is not restricted to fixed single models. It dynamically selects the appropriate model and calibrated reasoning effort tier (`low`, `medium`, `high`, `ultra`) across all available providers (Claude Code models/effort tiers, Codex Luna/Terra/Sol, Gemini Flash/Pro, local LM Studio).
* **Autonomous Escalation Protocol:** If a worker encounters repeated test failures or ambiguity loops (2 failed attempts), the protocol strictly forbids blind retries. Instead, it automatically **escalates reasoning effort** (e.g., `medium` -> `high`/`ultra`) and/or **upgrades the model tier** (e.g. Flash/Sonnet -> Opus/Fable 5) to diagnose and fix the root cause efficiently.
* **Fail-Safe Fallback:** On local model failure or network loss, the system attempts fallback to an alternative local model (e.g., Gemma 4). If unresolved, it halts and presents an interactive prompt with actionable recommendations.
* **Periodic Deep Research & Benchmarks:** A scheduled background job (`/schedule`) periodically executes a benchmark suite comparing free local LM Studio models against frontier cloud APIs, proposing optimization updates to `routing_config.json`.

### 3. Autonomous Debate Loop & Interactive Dispute Resolution
* **Planner-Critic Debate:** Complex architectural tasks trigger an autonomous debate loop (up to 3 rounds) between a Planner model and a Critic model.
* **Dispute Adjudication:** If consensus is not reached after 3 rounds, execution halts. The system presents an interactive visual comparison matrix displaying trade-offs, pros, cons, and one-click decision options for human resolution.

### 4. Audit, Scope Creep Control & Hybrid Issue Management
* **Spec vs Diff Audit:** Upon task completion, an automated audit engine verifies that modifications adhere strictly to the task specification.
* **Scope Creep Detection:** Unapproved code changes trigger a warning alert and generate a local tracking issue under `.scratch/issues/`, with optional synchronization to GitHub Issues.
