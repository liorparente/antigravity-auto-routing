# Spec 0011 — Zero-Latency Boot, Inverted Routing Hierarchy & Active Automated Learning

* **Status:** ready-for-agent
* **Date:** 2026-08-22
* **Related:** Spec 0004 (Learning Loop), Spec 0005 (Unified Worker Invocation), Spec 0009 (Unified Consultation Engine), Spec 0010 (Standard Python Package), ADR 0001, ADR 0004, ADR 0008, ADR 0010
* **Glossary:** **AllowedDirectAction**, **HardGate**, **WorkerModeOverride**, **InvertedRoutingPyramid**, **ActiveMemoryRetrieval**, **LearningJournal**, **LearnedState** (`CONTEXT.md`)

---

## Problem Statement

The `auto-routing` ecosystem has grown through ten successive specifications into an enterprise-grade multi-agent governance platform. However, this growth has accumulated severe structural overhead that degrades developer experience, inflates cloud API costs, and isolates the self-improving learning loop:

1. **Severe Boot Latency & Protocol Bloat:**
   - Injected protocol instructions in `AGENTS.md`, `CLAUDE.md`, and `~/.gemini/GEMINI.md` have ballooned past 22KB (>6,000 tokens), consuming context window space and slowing session initialization.
   - Synchronous preflight checks and file lock contention (`fcntl.flock`) in `install.sh` and startup hooks cause execution timeouts during rapid subagent spawning.
   - Child process execution of Antigravity CLI (`agy`) risks indefinite TTY/IPC hanging in terminal and sandboxed environments when stdin is unhandled or loopback sockets are isolated.

2. **Cost-Inverted Routing Pyramid:**
   - The routing matrix disproportionately delegates trivial, single-file, and context-gathering tasks to high-cost cloud models (`Claude Sonnet 5 Thinking`, `Codex Sol`), rapidly burning API credits.
   - Free local models running via `LM Studio` (`127.0.0.1:1234/v1`) and low-cost fast models (`agy` with Gemini 3.6/3.7 Flash) remain underutilized due to static routing configurations and absent dynamic capability probing.

3. **Passive, Disconnected Learning Mechanism:**
   - `knowledge/institutional-memory.md` contains 103 items (~90KB), making full-context injection impossible without degrading attention and token budget.
   - The ground-truth recording mechanisms (`learning_outcomes.py`, `learning_journal.py`) operate without automated runtime hooks, requiring manual invocation and causing learning journal drift.

---

## Solution

Modernize the `auto-routing` architecture across three unified pillars:

1. **Pillar 1: Zero-Latency Infrastructure & Lean Protocol:**
   - Slim injected protocol files down from 22KB to a compact core sentinel (~4KB) preserving the **Hard Gate** and **Worker Mode Override**, while offloading extensive manuals, edge cases, and CLI recipes to pointer-based references (`SKILL.md` and `REFERENCE.md`).
   - Replace blocking file-lock preflights in `install.sh` with non-blocking, lazy state validation.
   - Harden CLI worker execution with strict non-interactive stdin guards (`< /dev/null`, piped input) and mandatory `BypassSandbox: true` annotations.

2. **Pillar 2: Inverted Routing Pyramid (Local & Flash First):**
   - Establish **Tier 0** (LM Studio / Local $0) and **Tier 1** (`agy` Gemini Flash) as strict defaults for Trivial, Simple, Boilerplate, and Context/Search tasks.
   - Implement an active capability probe against `http://127.0.0.1:1234/v1/models` that identifies loaded local models and gracefully warns/prompts the user if LM Studio is offline.
   - Restrict **Tier 2/3** (Claude Sonnet/Opus Thinking, Codex Sol) strictly to high-impact triggers: 5+ file changes, architectural/DB schema modifications, initial planning (`/plan`), stubborn bugs (2+ failed attempts), and security-sensitive boundaries.

3. **Pillar 3: Active Context Scoping & Automated Closed-Loop Learning:**
   - Distill the 103 existing institutional memory items into **20 Core Golden Rules**, archiving raw historical logs to `knowledge/archive/institutional-memory-legacy.md`.
   - Implement a lightweight hybrid retrieval engine (topic tags + keyword relevance) that dynamically injects only the top 3–5 most relevant rules per task context.
   - Wire automated lifecycle hooks: a post-test execution hook (recording test Pass/Fail in TDD) and a post-review hook (recording code review verdicts), closing the feedback loop into dynamic router calibration without human overhead.

---

## User Stories

### Pillar 1: Lean Infrastructure & Zero-Latency Boot
1. As an orchestrator starting a new session, I want `AGENTS.md` and `GEMINI.md` to load in under 50ms with a lean ~4KB footprint, so that context windows remain focused on task requirements.
2. As an orchestrator enforcing governance, I want the core protocol sentinel to strictly retain the `[ROUTING:]` first-line format, Hard Gate, and Worker Mode Override, so that unrouted code modifications remain structurally impossible.
3. As a developer installing or syncing skills via `install.sh`, I want lock-free, atomic staging across harnesses (`~/.gemini/`, `~/.codex/`, `.agents/`) without hitting `fcntl.flock` deadlocks or timeout errors.
4. As a nested worker spawned via `agy -p`, I want child processes to execute with stdin detached (`< /dev/null`) and loopback IPC preserved, so that the CLI never hangs indefinitely waiting for terminal input.
5. As a maintainer exploring rules, I want detailed worker CLI flags, sandbox nuances, and examples indexed in `REFERENCE.md`, so that deep documentation is accessible on demand without bloating the global prompt.

### Pillar 2: Local & Flash First Routing
6. As a project owner, I want simple single-file edits and unit test boilerplate to route automatically to local LM Studio models ($0 cost), so that cloud API expenses are minimized.
7. As an orchestrator checking local model availability, I want a sub-millisecond probe to `http://127.0.0.1:1234/v1/models`, so that available local model capabilities (e.g. Gemma, Qwen) are discovered dynamically.
8. As a user working with LM Studio offline, I want the orchestrator to surface a clear prompt asking whether to start LM Studio or proceed with Gemini Flash, so that I retain full control over local vs. cloud routing.
9. As an orchestrator performing codebase searches, log parsing, or dependency mapping, I want context-gathering tasks to route directly to `agy` with Gemini 3.6/3.7 Flash, so that research is fast and inexpensive.
10. As a developer executing a 6-file architectural refactor or DB migration, I want the router to escalate to Claude Sonnet/Opus Thinking and Codex Sol, ensuring high-reasoning System 2 oversight for complex changes.
11. As a security auditor, I want any task touching credentials, auth tokens, or private keys to force-route to LM Studio or fail closed, preventing sensitive data exposure.

### Pillar 3: Active Context & Automated Learning
12. As an agent assembling a task prompt, I want the prompt assembler to inject only the top 3–5 relevant rules from the 20 Golden Rules based on task keywords and file tags, preventing context saturation.
13. As an orchestrator completing a TDD cycle, I want the test runner to automatically invoke `learning_outcomes.record_test_result` upon test completion, recording an honest boolean pass/fail without manual intervention.
14. As a reviewer approving an implementation, I want the review process to automatically invoke `learning_outcomes.record_review_verdict`, capturing reviewer sign-off directly into the learning journal.
15. As a dynamic router evaluating model fallbacks, I want calibration scores and scoreboard metrics to update dynamically from verified outcomes, continuously optimizing model tier selections.
16. As a maintainer inspecting historical lessons, I want legacy memories safely preserved in `knowledge/archive/`, maintaining full institutional traceability without cluttering active runtime files.

---

## Implementation Decisions

### 1. Protocol Architecture & Sentinel Decoupling
- The generated protocol block in `protocol.md` is refactored into a high-density, lean core format (~4KB).
- The core block retains:
  - Worker Mode Override token check (`[WORKER-MODE: AGY-NESTED-EXEC]`).
  - Orchestrator Hard Gate and mandatory `[ROUTING:]` first-line grammar.
  - Inverted 4-tier model complexity matrix summary.
  - Direct action whitelist and escalation triggers.
- Detailed CLI command syntax, fallback recipes, and learning recording guidelines are moved to `skills/worker-routing/REFERENCE.md` and referenced via clear pointers.
- `install.sh` is streamlined to synchronize the lean block across `AGENTS.md`, `CLAUDE.md`, and `~/.gemini/GEMINI.md` with zero-lock hash verification.

### 2. Active Model Prober & Universal Calibrated Matrix (`production_invoker.py` & `routing_check.py`)
- Introduce `probe_local_model_availability(endpoint: str = "http://127.0.0.1:1234/v1") -> Optional[LocalModelCapabilities]` in `production_invoker.py`.
- The prober executes a non-blocking HTTP GET with a 200ms socket timeout against `/models`.
- If an active model is found, its model ID and parameter class are parsed to match task complexity.
- If the endpoint is unreachable, the orchestrator triggers an explicit user decision prompt:
  `LM Studio is offline. [1] Launch local model [2] Fallback to Gemini Flash (Cloud)`
- **Universal Calibrated Provider & Effort Routing Matrix:**
  - **Claude Provider:** Dynamic model selection (`claude-sonnet-5`, `claude-opus-5`, `claude-3-7-sonnet`) with fine-grained thinking/effort modes (`--effort low | medium | high | ultra` / `ultracode` for deep structural implementation).
  - **Codex Provider:** Dynamic model selection (`gpt-5.6-luna`, `gpt-5.6-terra`, `gpt-5.6-sol`) with calibrated reasoning effort (`-c model_reasoning_effort="low"|"medium"|"high"|"ultra"`).
  - **Antigravity CLI (`agy`) Provider:** Dynamic model selection (`gemini-3.7-flash`, `gemini-3.6-flash`, `gemini-3.1-pro`) with thinking parameters (`low`, `medium`, `high`).
  - **LM Studio Provider:** Active discovery of loaded local models (Gemma 4 E4B, Qwen 2.5/3, DeepSeek-R1-Distill) via `http://127.0.0.1:1234/v1`.
- **Calibrated Tier Hierarchy:**
  - **Tier 0 (Local $0):** LM Studio for Trivial, Simple Boilerplate, and Sensitive/PII tasks.
  - **Tier 1 (Cloud Fast/Cheap):** `agy` Gemini 3.6/3.7 Flash for Context, Search, and Single-File Execution.
  - **Tier 2 (Cloud Heavy Doer / High-Velocity Coding):** Claude Sonnet 5 (High/Ultra effort / `ultracode`) & Codex 5.6 Terra for 3–4 files, intense refactoring, and feature builds.
  - **Tier 3 (Cloud System 2 / Deep Planning):** Claude Opus 5 (Thinking) & Codex 5.6 Sol (Ultra) for 5+ files, DB/architecture migrations, initial planning (`/plan`), and complex debugging (2+ failures).

### 3. Golden Rules Compaction & Scoped Memory Retrieval (`learned_state.py` & `prompt_assembler.py`)
- Distill `knowledge/institutional-memory.md` into 20 categorized Golden Rules:
  - Architecture & Deep Modules (Rules 1–4)
  - Testing & TDD Seams (Rules 5–8)
  - Subprocess & CLI Process Safety (Rules 9–12)
  - State & File Locking Hygiene (Rules 13–16)
  - Multi-Harness Sync & Governance (Rules 17–20)
- The legacy 103 items are archived to `knowledge/archive/institutional-memory-legacy.md`.
- `prompt_assembler.py` is upgraded with `extract_scoped_memory(task_description: str, target_files: List[str], max_rules: int = 5) -> str`:
  - Matches file extensions and domain tags (e.g. `test_`, `.sh`, `auth`, `router`).
  - Computes keyword intersection against the 20 Golden Rules.
  - Injects strictly top 3–5 matching rules into the worker mission brief.

### 4. Automated Ground-Truth Lifecycle Hooks (`learning_outcomes.py`)
- Enhance `learning_outcomes.py` with automatic execution hooks:
  - `auto_record_test_execution(task_id: str, exit_code: int, root_dir: Path)`: Automatically maps `exit_code == 0` to `passed=True` and appends an `OutcomeRecord(ground_truth="tests")`.
  - `auto_record_review_execution(task_id: str, approved: bool, root_dir: Path)`: Records review outcomes upon PR or `/code-review` approval.
- Ensure all automated outcome writes use atomic, append-only file operations with positional reduction (`(task_id, ground_truth)` last record wins).

---

## Testing Decisions

### 1. Seam Selection & Test Boundary
- **Public Seam 1 (Invoker & Prober):** `production_invoker.invoke_worker` and `probe_local_model_availability`. Tests will verify dynamic model fallback, timeout handling, and user prompt triggers using mock HTTP endpoints.
- **Public Seam 2 (Memory Retrieval):** `prompt_assembler.assemble_prompt` and `extract_scoped_memory`. Tests will verify that arbitrary task inputs receive exactly 3–5 relevant rules without token bloat.
- **Public Seam 3 (Automated Learning Hooks):** `learning_outcomes.auto_record_test_execution` and `learning_outcomes.auto_record_review_execution`. Tests will verify correct ground-truth persistence and positional reduction.
- **Public Seam 4 (Protocol Installation & Staging):** `install.sh` execution. Tests will verify generated file sizes (<5KB), sentinel integrity, and clean multi-harness sync.

### 2. Prior Art & Test Integrity
- Build upon existing test suites: `test_production_invoker.py`, `test_learned_state.py`, `test_prompt_assembler.py`, and `test_routing.py`.
- Enforce strict public interface testing without mocking internal file readers or private helper functions.
- All tests must pass deterministically without network access or unmocked subprocess leaks.

---

## Out of Scope

- Modifying core Planner-Critic debate dynamics in `debate_orchestrator.py` and `agent_council.py` established in Spec 0004/0008.
- Introducing external vector database daemons (e.g. Chroma, Pinecone); retrieval must remain lightweight, local, and zero-dependency.
- Supporting non-standard cloud LLM providers outside currently configured tooling.

---

## Further Notes

- Following approval of Spec 0011, `/to-tickets` will decompose implementation into four focused, test-driven tracer-bullet tickets under `.scratch/auto-routing-reorg/issues/`.
- Final sign-off will culminate in a multi-model Council Review evaluation (Claude, Codex, Gemini) before tagging release v3.6.
