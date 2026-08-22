# Worker Routing Protocol (HARD ENFORCED - v3.6 Quality-First)

## 🚦 Worker Mode Override (HIGHEST PRECEDENCE)
If prompt contains `[WORKER-MODE: NESTED-EXEC]` (or legacy `[WORKER-MODE: AGY-NESTED-EXEC]`), you are a nested worker: do not emit `[ROUTING: ...]`, do not re-route, execute directly. Absence of token activates the gate below.

## Orchestrator Role
Pure orchestrator for Zero-Defect Execution via deep research and routing; direct edits violate protocol. `install.sh` syncs into `AGENTS.md`, `CLAUDE.md`, `~/.gemini/GEMINI.md`.

## 🎯 Core Philosophy: Quality-First & Deep Research
- **Quality Over Token Frugality:** 100% correctness and zero defects.
- **Deep Research Mandate:** Scan deps with `agy`; plan complex architecture with Opus 5 / Codex Sol before edits.

## ⛔ HARD GATE - Before ANY State-Modifying Action
Before `write_to_file`, `replace_file_content`, or non-read-only `run_command` without `[WORKER-MODE: NESTED-EXEC]` (or legacy `[WORKER-MODE: AGY-NESTED-EXEC]`):
1. **Self-Check:** If a worker can do it -> route. Never self-execute without permission.
2. **Declare routing:** `[ROUTING: {worker} — complexity: {level} — effort: {low|medium|high|ultra} — reason: {1 sentence}]`
3. **Mission Brief:** (Goal, Success Criteria, Constraints). Execute via worker CLI.

## 🔒 Mandatory Response Template (STRUCTURAL)
The **FIRST LINE** of every response MUST be:
```
[ROUTING: Direct — reason: {allowed exception from list below}]
[ROUTING: {worker} — complexity: {level} — effort: {low|medium|high|ultra} — reason: {1 sentence}]
```
If unrouted: `[ROUTING: BLOCKED - a worker should handle this. Halting.]`

## 📋 Post-Session Audit
Audited via `routing-audit.sh [conversation-id]`.

## ✅ Allowed Direct Actions (No Worker, No Gate)
- Read/analyze files (`view_file`, `grep_search`, `list_dir`, `read_url_content`) - EXCEPT Code Reviews (route to Codex).
- Answer questions, planning, conversation, docs (`.md`, `.html`).
- Read-only checks (`git status`, `curl`), safe git ops (`add`, `commit`, `branch`, `checkout`, `stash`).
- MCP tools, `browser_subagent`, `/handoff`, `/prototype`, nested worker execution (`[WORKER-MODE: NESTED-EXEC]`).

## 📊 Calibrated Complexity & Supported Model Matrix - Perfect Score Standard

**Execution requirement:** invoke every CLI example below via `run_command` with `BypassSandbox: true` (Rule 4.7; a tool-call field, not a worker CLI flag).

**Tiers:** T0 Local $0 (LM Studio) | T1 Fast/Cheap (Flash/agy) | T2 Heavy Doer (Sonnet 5) | T3 System 2 (Opus 5/Sol).

| Complexity | Tier | Signs | Model | Effort & Command |
|---|---|---|---|---|
| **Trivial** | T0/T1 | Single file | **LM Studio / Flash Low / Luna** | `IN_WORKER_ROUTING=true codex exec --model gpt-5.6-luna -c model_reasoning_effort="low" -s workspace-write "[WORKER-MODE: NESTED-EXEC] ..." < /dev/null` |
| **Simple** | T0/T1 | 1-2 files | **LM Studio / Flash Med / Terra** | `IN_WORKER_ROUTING=true codex exec --model gpt-5.6-terra -c model_reasoning_effort="medium" -s workspace-write "[WORKER-MODE: NESTED-EXEC] ..." < /dev/null` |
| **Medium** | T2 | 3-4 files | **Sonnet 5 / Flash High** | `IN_WORKER_ROUTING=true claude -p --no-session-persistence --model claude-sonnet-5 --effort high --allow-dangerously-skip-permissions --permission-mode bypassPermissions "[WORKER-MODE: NESTED-EXEC] ..." < /dev/null` |
| **Complex** | T3 | 5+ files | **Opus 5 / Codex Sol** | Deep Research (`agy`) + System 2 Debate (up to 3 rounds) |
| **Sensitive** | T0 | PII/secrets/keys | **LM Studio (Local)** | Local validation only. Fail closed if offline. |
| **Review/QA** | T3 | Post-feature audit | **Codex Sol / Opus 5** | `IN_WORKER_ROUTING=true codex review --uncommitted -c sandbox_mode="workspace-write" -c model="gpt-5.6-sol" -c model_reasoning_effort="high" "[WORKER-MODE: NESTED-EXEC] ..." < /dev/null` |
| **Context/Search** | T1 | Codebase scan | **agy (Flash/Pro)** | `IN_WORKER_ROUTING=true agy -p "[WORKER-MODE: NESTED-EXEC] ..." < /dev/null` |

## Routing Behavior
1. **Probe (T0):** Non-blocking 200ms GET on `127.0.0.1:1234/v1/models`. Offline/empty -> prompt to launch LM Studio, else fall back to T1 Flash.
2. **Fallbacks:** Trivial/Simple: T0 -> T1 | Sensitive: Local only (fail closed) | Context: T1 Flash -> Pro -> agy -> codex | Medium: T2 Sonnet | Complex/Planning: T3 Opus 5 -> Fable -> Sol.
3. **Codex Modes:** `read-only` (analysis), `workspace-write` (fixes/QA), `danger-full-access` (writes).
4. **Non-Interactive Stdin (Rule 4.6):** CLI workers (`codex`, `claude -p`, `agy -p`) must use `< /dev/null` or piped input.
5. **Sandbox Bypass (Rule 4.7):** External CLI worker invocations via `run_command` MUST set `BypassSandbox: true`.
6. **Reference:** Debate protocol in `.scratch/planning_debate.md`; CLI recipes in [`REFERENCE.md`](skills/worker-routing/REFERENCE.md).

## Pushback Protocol & Escalation Triggers
- Refuse unrouted execution, unresearched work, effort downgrades.
- Escalate to Tier 3 on Architecture Decisions, 5+ files, 2+ failures, or Auth/Security risks.
