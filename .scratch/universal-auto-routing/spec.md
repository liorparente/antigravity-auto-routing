# Spec: Universal Auto-Routing Protocol & Multi-Harness Execution

## Problem Statement

Developers using multiple AI coding tools (OpenAI Codex CLI, Anthropic Claude Code, and Google Antigravity IDE) currently experience inconsistent execution quality, unconstrained self-execution by orchestrator agents, unpredictable fallback behavior during offline/rate-limit events, and unauthorized scope creep. There is no unified, multi-harness protocol that guarantees zero defects (**Perfect Score Standard**) through calibrated worker routing, autonomous Planner-Critic debate, dynamic effort escalation, and automated post-task spec auditing across all three environments.

## Solution

A universal, cross-environment Auto-Routing framework that transforms Codex CLI, Claude Code, and Antigravity IDE into a coordinated multi-agent routing network. The primary agent acts strictly as a **Pure Orchestrator**, delegating tasks based on a dynamic effort matrix (`routing_config.json`), conducting autonomous Planner-Critic debate loops for complex tasks, escalating reasoning effort and model tiers upon repeated failures, gracefully failing back to local LM Studio models during network loss, and enforcing post-task Spec vs Diff auditing with automated issue tracking.

## User Stories

1. As a developer using any supported CLI (Claude Code, Codex, or Antigravity), I want the Orchestrator agent to automatically delegate code implementation to specialized worker models rather than self-executing, so that I maintain strict quality isolation and architectural standards.
2. As an Architect agent, I want full flexibility to select any active model and reasoning effort tier (`low`, `medium`, `high`, `ultra`) across providers, so that the right tool and effort are applied to each unique task.
3. As a developer, I want the system to automatically escalate reasoning effort and/or upgrade the model tier when a worker encounters repeated test failures (2+ failed attempts), so that root causes are resolved rather than blindly retrying broken approaches.
4. As a developer working offline or with sensitive credentials, I want the system to route tasks to local LM Studio models first and gracefully fail back to alternative local models (like Gemma 4) without silently sending data to public cloud APIs, so that my data privacy is guaranteed.
5. As a developer facing an offline model failure, I want to receive an actionable, clear error message with interactive recovery options, so that I can resolve the issue immediately without session disruption.
6. As a developer designing a complex multi-file feature, I want an autonomous Planner-Critic debate loop to stress-test the architectural plan before implementation begins, so that design flaws are identified before code is written.
7. As a developer reviewing a stalemated debate between models, I want to see an interactive visual comparison matrix showing trade-offs, pros, and cons with one-click decision options, so that I can easily make the final architectural call.
8. As a project owner, I want an automated audit engine to compare completed git diffs against the original task specification, so that unauthorized scope creep is flagged immediately.
9. As a project owner, I want unapproved code modifications to automatically generate local tracking issues in `.scratch/issues/`, so that technical debt is tracked without cluttering the repository.
10. As a developer configuring model preferences, I want a periodic background evaluation job (`/schedule`) to benchmark free local models against paid cloud APIs, so that my `routing_config.json` matrix remains optimal over time.
11. As a developer setting up a workspace, I want a single synchronization script (`install.sh`) to align `AGENTS.md`, `CLAUDE.md`, and `~/.gemini/GEMINI.md`, so that all three environments enforce identical routing rules.

## Implementation Decisions

- **Pure Orchestrator Guard:** All three environment configurations (`AGENTS.md`, `CLAUDE.md`, `~/.gemini/GEMINI.md`) strictly enforce the `[ROUTING:]` template and prohibit direct state-modifying file edits when `IN_WORKER_ROUTING` is not set.
- **Dynamic Model & Effort Matrix (`routing_config.json`):** Centralized configuration supporting full model flexibility and reasoning effort tiers (`low`, `medium`, `high`, `ultra`) across Claude Code, Codex, Gemini, and LM Studio.
- **Autonomous Escalation Engine:** Logic that monitors worker attempt counters. Upon 2 consecutive failures on the same task, the engine automatically escalates reasoning effort to `high`/`ultra` and/or upgrades the model tier before issuing a retry.
- **Unified Permission Sync (`install.sh`):** A canonical shell script propagates sentinel-wrapped routing rules and sandbox permissions atomically across all harness manifests (`AGENTS.md`, `CLAUDE.md`, `GEMINI.md`).
- **Fail-Safe & Recovery Controller:** Encapsulated routing fallback logic that attempts local alternative models before halting and emitting interactive prompt schemas.
- **Planner-Critic Debate Engine:** Multi-round autonomous debate protocol capped at 3 rounds. Generates visual comparison tables and interactive decision modals upon stalemate.
- **Spec vs Diff Audit Engine:** Post-execution verification module that parses `git diff`, compares modified files against the mission brief, alerts on unauthorized scope creep, and creates `.scratch/issues/` markdown tickets.

## Testing Decisions

- **Seam Selection:** Testing occurs at the public interface seam of `RoutingAuditEngine` and `AgentCouncil` in `skills/worker-routing/test_routing.py`. This single high-level seam validates manifest signature verification, effort escalation logic, fallback chains, DEC-01 protocol compliance, and spec audit detection.
- **Behavior-Only Verification:** Tests verify external behavior (exit codes, audit issue generation, effort escalation state transitions, manifest signature validity) rather than private implementation details.
- **Prior Art:** Extends existing unittest suite in `skills/worker-routing/test_routing.py` (currently 92 passing tests).

## Out of Scope

- Building a standalone GUI desktop app.
- Automatic committing or pushing of git branches without explicit user approval.

## Further Notes

- Architectural decisions recorded in `docs/adr/0005-universal-auto-routing-architecture-and-pillars.md`.
- Ready to publish local tracer bullet tickets under `.scratch/universal-auto-routing/issues/`.
