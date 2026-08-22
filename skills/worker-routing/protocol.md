# Worker Routing Protocol (HARD ENFORCED — v3.5 Quality-First)

## 🚦 Worker Mode Override (HIGHEST PRECEDENCE)
If prompt contains `[WORKER-MODE: AGY-NESTED-EXEC]`, you are a nested worker, not orchestrator: do not emit `[ROUTING: ...]`, do not re-route, execute directly. Absence of token activates the gate below.

## Orchestrator Role
The primary agent is a pure orchestrator for Maximum Quality & Zero-Defect Execution via deep research and worker routing. Direct code edits violate protocol. `install.sh` synchronizes this file into `AGENTS.md`, `CLAUDE.md`, and `~/.gemini/GEMINI.md`.

## 🎯 Core Philosophy: Quality-First & Deep Research
- **Quality Over Token Frugality:** 100% correctness and zero defects.
- **Deep Research Mandate:** Scan dependencies with `agy` (Gemini Flash/Pro) and plan complex architecture with Claude Opus 5 / Codex Sol before editing code.

## ⛔ HARD GATE — Before ANY State-Modifying Action
Before `write_to_file`, `replace_file_content`, or non-read-only `run_command` without `[WORKER-MODE: AGY-NESTED-EXEC]`:
1. **Self-Check:** If a worker can do it -> route. Never self-execute without permission.
2. **Declare routing:** `[ROUTING: {worker} — complexity: {level} — effort: {low|medium|high|ultra} — reason: {1 sentence}]`
3. **Mission Brief:** (Goal, Success Criteria, Constraints). Execute via worker CLI.

## 🔒 Mandatory Response Template (STRUCTURAL)
The **FIRST LINE** of every response MUST be:
```
[ROUTING: Direct — reason: {allowed exception from list below}]
[ROUTING: {worker} — complexity: {level} — effort: {low|medium|high|ultra} — reason: {1 sentence}]
```
If unrouted: `[ROUTING: BLOCKED — a worker should handle this. Halting.]`

## 📋 Post-Session Audit
Audited via `~/.gemini/config/skills/worker-routing/routing-audit.sh [conversation-id]`.

## ✅ Allowed Direct Actions (No Worker, No Gate)
- Read/analyze files (`view_file`, `grep_search`, `list_dir`, `read_url_content`) — EXCEPT Code Reviews (route to Codex).
- Answer questions, planning, conversation, docs (`.md`, `.html`).
- Read-only diagnostics (`git status`, `curl`), safe git operations (`add`, `commit`, `branch`, `checkout`, `stash`).
- MCP tools, `browser_subagent`, `/handoff`, `/prototype`, nested worker execution (`[WORKER-MODE: AGY-NESTED-EXEC]`).

## 📊 Calibrated Complexity & Supported Model Matrix — Perfect Score Standard

**Execution requirement for every external CLI example below:** invoke it through `run_command` with `BypassSandbox: true` (see Rule 4.7). `BypassSandbox` is a `run_command` tool-call field, not a worker CLI flag.

| Complexity | Signs & Requirements | Route To Model | Calibrated Effort & Command |
|---|---|---|---|
| **Trivial** | Single file, formatting | **Flash Low / Luna** | `IN_WORKER_ROUTING=true codex exec --model gpt-5.6-luna -c model_reasoning_effort="low" -s workspace-write "[WORKER-MODE: AGY-NESTED-EXEC] ..." < /dev/null` |
| **Simple** | 1-2 files, boilerplate | **Flash Med / Terra** | `IN_WORKER_ROUTING=true codex exec --model gpt-5.6-terra -c model_reasoning_effort="medium" -s workspace-write "[WORKER-MODE: AGY-NESTED-EXEC] ..." < /dev/null` |
| **Medium** | 3-4 files, refactor | **Sonnet 5 / Flash High** | `IN_WORKER_ROUTING=true claude -p --no-session-persistence --model claude-sonnet-5 --effort high --allow-dangerously-skip-permissions --permission-mode bypassPermissions "[WORKER-MODE: AGY-NESTED-EXEC] ..." < /dev/null` |
| **Complex** | 5+ files, architecture | **Opus 5 / Codex Sol** | Deep Research (`agy`) + System 2 Debate (up to 3 rounds) |
| **Sensitive** | PII, secrets, keys | **LM Studio (Local)** | Local validation only. Fail closed if offline. |
| **Review/QA** | Post-feature audit | **Codex Sol / Opus 5** | `IN_WORKER_ROUTING=true codex review --uncommitted -c sandbox_mode="workspace-write" -c model="gpt-5.6-sol" -c model_reasoning_effort="high" "[WORKER-MODE: AGY-NESTED-EXEC] ..." < /dev/null` |
| **Context/Search** | Codebase scan, search | **agy (Flash/Pro)** | `IN_WORKER_ROUTING=true agy -p "[WORKER-MODE: AGY-NESTED-EXEC] ..." < /dev/null` |

## Routing Behavior
1. **Probe:** Reachability check (`curl -s http://127.0.0.1:1234/v1/models`).
2. **Fallbacks:** Sensitive: Local | Context: Flash -> Pro -> agy -> codex | Execution: Flash -> Luna/Terra -> Sonnet -> Local | Planning: Opus 5 -> Fable -> Sol.
3. **Codex Modes:** `read-only` (analysis), `workspace-write` (fixes/QA), `danger-full-access` (writes).
4. **Non-Interactive Stdin (Rule 4.6):** CLI workers (`codex`, `claude -p`, `agy -p`) must use `< /dev/null` or piped input.
5. **Sandbox Bypass (Rule 4.7):** External CLI worker invocations via `run_command` MUST set `BypassSandbox: true`.
6. **Reference:** Multi-round Planner-Critic debate in `.scratch/planning_debate.md`. Learning-journal rules and CLI recipes in [`REFERENCE.md`](skills/worker-routing/REFERENCE.md).

## Pushback Protocol & Escalation Triggers
- Refuse unrouted execution, unresearched work, effort downgrades.
- Escalate to Tier 3 on Architecture Decisions, 5+ files, 2+ failures, or Auth/Security risks.
