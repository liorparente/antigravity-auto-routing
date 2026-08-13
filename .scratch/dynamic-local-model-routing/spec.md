# Specification: Dynamic Local Model Routing & Judicial Advisory Protocol

## Problem Statement

Users need an intelligent, privacy-safe orchestrator that dynamically offloads tasks to local models (e.g., LM Studio) when appropriate, while ensuring zero data leakage of sensitive credentials and maintaining high solution quality for complex tasks through multi-model judicial consultation.

## Solution

A multi-tiered decision engine integrated into the Worker Routing Protocol (`AGENTS.md` and `SKILL.md`) that evaluates task complexity and sensitivity, enforces a mandatory human approval gate for suspicious code or keys, runs up to 3 rounds of bi-/tri-lateral judicial model debate when complexity is ambiguous, logs complete execution telemetry, and seamlessly executes fallback chains on local model unavailability.

## User Stories

1. As a developer, I want my sensitive credentials, security keys, and private data to be automatically detected, so that no confidential information is uploaded to cloud models without my explicit approval.
2. As a system architect, I want simple and routine tasks (formatting, single-file edits, log parsing) to be automatically routed to local models like LM Studio, so that I optimize cloud token costs and latency.
3. As a senior engineer, I want ambiguous tasks to trigger a structured 2-to-3-way debate loop between Planner and Critic models (Claude Code and Codex), so that edge cases and architectural trade-offs are thoroughly stress-tested before code generation.
4. As a DevOps engineer, I want local model failures or offline events to automatically log a structured technical bug report and seamlessly continue execution via the cloud fallback chain, so that my workflows are never blocked.
5. As an auditor, I want a complete telemetry log recording Task ID, complexity tier, routing rationale, latency, and execution outcome for every routed action, so that I have full historical visibility over model routing decisions.

## Implementation Decisions

* **Module Updates:** Modify `AGENTS.md`, `.agent/skills/worker-routing/SKILL.md`, and routing validation scripts.
* **Dual-Parameter Routing Engine:** Implement routing logic combining evaluated task complexity (Trivial, Simple, Medium, Complex) and privacy sensitivity.
* **Proactive Security Gate:** Intercept any prompt or file containing API keys, private certificates, or user data flags, prompting the user for approval prior to dispatch.
* **Judicial Advisory Protocol:** Define a structured 3-round max Planner-Critic debate loop when model selection complexity is borderline or ambiguous.
* **Fallback & Telemetry:** Maintain `.ralph/cache/` or `.scratch/` structured JSON/text execution logs for all model routing events.

## Testing Decisions

* **Test Seam:** High-level policy evaluation seam in `agent_council.py` and routing validation scripts.
* **Behavior Verification:** Validate that sensitive tasks strictly trigger the human approval prompt, simple tasks route to local models when reachable, and offline local endpoints trigger structured log generation before falling back to cloud workers.
* **Prior Art:** Existing policy evaluation tests in `test_agent_council.py` and sentinel integrity tests in `install.sh`.

## Out of Scope

* Building a custom local LLM inference engine (LM Studio / Ollama will be consumed via standard HTTP endpoints).
* Automatic key rotation or cloud secret management.

## Further Notes

* Derived from ADR 0004 (`docs/adr/0004-dynamic-local-model-routing-and-consultation-policy.md`).
