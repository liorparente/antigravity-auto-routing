# ADR 0012: Workflow V2 Architecture Contracts — Role-Capability Decoupling, Orchestrator Neutrality, and Perspective Council

## Status
Accepted (2026-08-22)

## 3-Condition Gate Rationale
1. **Hard to reverse:** Establishes the foundational domain contracts and taxonomies for role resolution, multi-harness synchronization, context layering, and council review across all future skills, invokers, tests, and configuration schemas.
2. **Surprising without context:** Explains why roles are decoupled from concrete providers/models, why Council members evaluate proposals via four analytical perspectives rather than vendor brand names, and why orchestrators operate symmetrically across all AI harnesses without vendor-specific privileges.
3. **Real trade-off:** Adopts dynamic declarative role resolution over hardcoded tool strings to protect against provider churn, CLI deprecations, and model sunsetting, incurring a minimal schema lookup and validation abstraction.

---

## Context
The `auto-routing` ecosystem evolved from Antigravity-specific orchestration scripts into an enterprise-grade multi-agent governance platform spanning three primary developer harnesses: Google Antigravity IDE/CLI (`agy`), Anthropic Claude Code CLI (`claude`), and OpenAI Codex CLI (`codex`).

However, Workflow V1 accumulated several structural couplings:
1. **Model-Centric Coupling:** Routing configurations and invokers bound tasks directly to vendor model strings (e.g., `claude-sonnet-5`, `gpt-5.6-sol`, `gemini-3.7-flash`), requiring broad codebase refactoring whenever foundation models updated.
2. **Vendor-Biased Council Review:** Review prompts in `skills/council-review/` addressed models by brand name ("You are Claude", "You are Codex"), causing brand identity drift and ungrounded behavioral biases instead of rigorous domain analysis.
3. **Asymmetric Orchestrator Assumptions:** Protocol rules and prompt tokens (`[WORKER-MODE: AGY-NESTED-EXEC]`) implicitly assumed Antigravity was the sole root orchestrator, creating friction when sessions originated inside Claude Code CLI or Codex CLI.
4. **Context Window Degradation:** Flat context injection blended global rules, project architecture, task specs, and session chatter, consuming tokens and creating attention noise for workers.

Workflow V2 (Spec 0012) addresses these challenges through formal architectural contracts.

---

## Decision

### 1. Contract 1: Role and Capability Requirements Decoupling
We separate what needs to be done (**Role**) and what technical constraints must be satisfied (**Capability Requirements**) from how inference is executed (**Provider**) and which weights are used (**Model**):

* **Role (`Role`):** An abstract functional responsibility or job-to-be-done (`planner`, `builder_heavy`, `builder_light`, `reviewer_architecture`, `reviewer_risk`, `reviewer_maintainability`, `reviewer_security`, `adjudicator`, `learner`).
* **Capability Requirements (`CapabilityRequirements`):** Declarative constraints required by a role:
  - `reasoning_tier`: Reasoning effort (`low`, `medium`, `high`, `ultra`).
  - `tool_access`: Sandbox isolation level (`read`, `workspace-write`, `danger-full-access`).
  - `min_context_window`: Token context threshold (e.g., 32k, 128k, 200k, 1M).
  - `local_only`: Strict boolean flag requiring offline execution (fail-closed if local provider offline).
* **Provider (`Provider`):** An executable transport adapter or CLI harness (`claude_code_cli`, `codex_cli`, `antigravity_cli`, `litellm_proxy`, `lm_studio_local`).
* **Model (`Model`):** Concrete foundation model weights and configuration identifier.

The `RoleResolver` maps `(Role, CapabilityRequirements)` $\rightarrow$ `(Provider, Model)` dynamically via `routing-config.json` with defined fallback preferences.

### 2. Contract 2: Orchestrator Neutrality and Unified Worker Mode Token
The root session agent in *any* harness (Antigravity, Claude Code, or Codex) is a **Pure Orchestrator** subject to the Hard Gate (forbidden from unrouted state modifications).

* **Unified Worker Mode Token:** Standardize prompt marker on `[WORKER-MODE: NESTED-EXEC]`, while maintaining backward-compatibility parser support for legacy `[WORKER-MODE: AGY-NESTED-EXEC]`.
* **Symmetric Invocations:** All workers spawned by an orchestrator inherit non-interactive stdin (`< /dev/null`), sandbox bypass flags (`BypassSandbox: true`), and explicit role briefs.

### 3. Contract 3: Perspective-Based Council Review & 1-Shot Fast Path
Council review is structured around four domain-specific analytical perspectives rather than vendor brand names:

1. **`reviewer_architecture`:** Evaluates deep module design, simple public interfaces, dependency boundaries, and inversion of control.
2. **`reviewer_risk`:** Evaluates concurrency, race conditions, edge cases, error handling, and fail-closed state machines.
3. **`reviewer_maintainability`:** Evaluates anti-bloat, DRY adherence, surgical edits, testability, and cognitive clarity.
4. **`reviewer_security`:** Evaluates CWE vulnerabilities, input validation, authentication boundaries, credential isolation, and sensitive data leakage.

* **1-Shot Parallel Synthesis Fast Path:** Council members evaluate concurrently in parallel. If weighted quorum ($\ge 0.60$) is achieved with zero critical security vetoes, the review terminates in **1 round** ($\le 45$ seconds).
* **Unilateral Security Veto:** Any single perspective detecting a verified Critical/High security vulnerability immediately halts the pipeline (`SECURITY_HALT`), overriding weighted approval.
* **Escalation:** Material disagreements that fail quorum after round 1 escalate to a local Adjudicator model or Human-in-the-Loop with an `AdvisoryStalemateReport`.

### 4. Contract 4: Four-Tier Context Architecture
To maintain maximum attention focus and prevent token exhaustion, context is segmented into four distinct layers:

1. **Layer 1 (Global):** Cross-project developer preferences, multi-harness synchronization rules, and universal safety gates.
2. **Layer 2 (Project):** Project domain glossary (`CONTEXT.md`), project-specific architectural rules (`PROJECT_RULES.md`), and active ADRs (`docs/adr/`).
3. **Layer 3 (Task):** Active ticket requirements, acceptance criteria, test seams, and referenced input artifacts.
4. **Layer 4 (Session):** Ephemeral transcript logs, scratch debugging notes, and active subagent message buffers.

---

## Consequences

### Positive
- **Provider Agnostic:** New model releases (e.g., GPT-5.7, Claude Sonnet 6, Gemini 4) or local runtimes can be adopted by updating `routing-config.json` without modifying skill code or invokers.
- **Unbiased Review Quality:** Perspective-anchored Council prompts force foundation models to evaluate specific engineering domains rather than generating generic brand-aligned responses.
- **Fast-Path Efficiency:** 1-shot parallel Council evaluations reduce review latency from minutes to $< 45$s for non-contentious changes.
- **Harness Neutrality:** Antigravity, Claude Code, and Codex CLI share identical capabilities, protocols, and safety guarantees.

### Negative / Trade-offs
- **Indirection Layer:** Requires `production_invoker.py` to maintain a dynamic resolution and schema validation step.
- **Prompt Specialization:** Prompt assembly must inject distinct heuristic instructions for each of the four reviewer perspectives.
