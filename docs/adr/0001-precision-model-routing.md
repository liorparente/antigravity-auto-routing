# ADR 0001: Precision Model Routing & Consensus Protocol

## Status
Approved

## Context
Antigravity acts as a pure orchestrator. Previously, Codex CLI invocations did not specify `--model` and `-c model_reasoning_effort` flags, leading the CLI to fall back to global default configurations in `~/.codex/config.toml` (which default to `gpt-5.6-sol` and `ultra` reasoning effort). This resulted in high costs and token usage for simple or trivial tasks.

Additionally, the fallback chain across tools (agy, codex, claude, local models) and the consensus planning loop (Planner-Critic) were not explicitly codified.

## Decision
We establish a precision routing protocol, a structured fallback chain, and an autonomous multi-round consensus mechanism to optimize costs, latency, and capability.

### 1. Complexity-Based Overrides
- **Trivial tasks** (comments, renaming variables, formatting): Codex Luna (`gpt-5.6-luna`) with `low` reasoning effort.
- **Simple tasks** (basic logic, helper functions, unit tests): Codex Terra (`gpt-5.6-terra`) with `low` reasoning effort.
  - *Optimization*: If a local model (Qwen 30B/Gemma) is already loaded in LM Studio, it should be prioritized for simple tasks before paid APIs.
- **Review/QA and critique**: Codex Sol (`gpt-5.6-sol`) with `medium` reasoning effort.
- **Complex/Planning**: Claude Fable 5 / Opus 4.8 for planning, Codex Sol for critique, Claude Sonnet 5 for execution.

### 2. Fallback Chain
- **Sensitive**: Local models only (Gemma -> Qwen) -> fail closed.
- **Context Scan**: agy Flash -> agy Pro -> codex read-only.
- **Execution (Trivial/Simple)**: codex Luna/Terra -> codex Sol (low) -> Claude Sonnet -> Local models.
- **Complex/Planning**: Claude Fable/Opus -> codex Sol (medium/high) -> manual.

### 3. Autopilot Multi-Round Consensus
- For complex/planning tasks, a multi-round debate loop runs autonomously (up to 3 rounds) between the Planner (Claude) and the Critic (Codex Sol).
- The discussion logs are written to `.scratch/planning_debate.md` for inspection.
- The final approved plan is written to `implementation_plan.md` and presented to the user for final approval.

## Consequences
- Lower OpenAI API execution costs due to Luna/Terra and low effort overrides.
- Higher planning quality through autonomous multi-round consensus reviews.
- Strict data compliance by failing closed on local sensitive tasks.
