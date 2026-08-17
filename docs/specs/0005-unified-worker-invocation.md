# Spec 0005 — Unified Worker Invocation: Consolidated Runtime Execution, Async Batches, and Subprocess Lifecycle

* Status: ready-for-agent
* Date: 2026-08-17
* Related: Spec 0001 (Advisory Consultation), Spec 0003 (Critical Dialogue), Spec 0004 (Learning Loop), ADR 0001, ADR 0007
* Glossary: **WorkerInvocation**, **WorkerModeToken**, **LearningJournal** (`CONTEXT.md`)

## Problem Statement

Worker model execution is bifurcated across multiple disparate modules in the repository:
1. **Divergent Subprocess Engines**: One module executes workers synchronously with cost tracking and journal integration, while another independently implements a multi-class hierarchy (`ClaudeAdapter`, `CodexAdapter`, `AgyAdapter`) using asynchronous subprocesses with regex parsing and its own timeout handling.
2. **Configuration & Flag Drift**: Command line arguments, reasoning effort mappings, non-interactive stdin enforcement (`< /dev/null`), and nested execution tokens (`[WORKER-MODE: AGY-NESTED-EXEC]`) are duplicated across files. Any change in CLI flags or provider options must be maintained in multiple locations.
3. **Process Reaping & Resource Leaks**: Subprocess timeouts and cancellation handling vary across callers; synchronous invocation relies on standard library timeouts while async review loops implement custom killing and process reaping, risking zombie processes if not uniformly managed.
4. **Shallow Adapter Interfaces**: High-level review panels and advisory consultations must interact with differing invocation contracts rather than relying on a single, deep execution module.

## Solution

Consolidate all worker and reviewer subprocess execution behind a single, deep **WorkerInvocation** runtime module:
- **Unified Engine**: Support both single-model execution (synchronous or asynchronous) and parallel multi-model batches (for council review panels and critical dialogues) through a single coherent interface.
- **Strict Process Lifecycle & Isolation**: Standardize non-interactive stdin (`DEVNULL`), environment variable forwarding (`IN_WORKER_ROUTING=1`), and guaranteed subprocess termination/reaping on timeout across all execution modes.
- **Rich Execution Result Contract**: Return a comprehensive `WorkerExecutionResult` encapsulating raw text output, parsed findings/votes, wall-clock duration, cost estimation, and execution status.
- **Seamless Backwards Compatibility**: Preserve existing synchronous call seams (`invoke_worker`, `make_journaled_invoke_worker`) while refactoring reviewer adapters into thin facades delegating directly to the unified engine.

## User Stories

1. As an orchestrator conducting an advisory consultation, I want to invoke any supported worker model through a single function call, so that I don't need to know provider-specific CLI flags.
2. As a multi-agent review panel, I want to execute multiple reviewer models in parallel asynchronously, so that review rounds complete within strict deadlines without blocking the event loop.
3. As a system operator, I want all worker executions to terminate cleanly and reap child processes upon timeout, so that zombie processes never consume host CPU or memory.
4. As a learning journal auditor, I want transparent duration measurement and cost estimation recorded automatically for every worker invocation, so that model performance and spend are consistently tracked.
5. As a reviewer adapter, I want structured JSON and heuristic vote/finding extraction built into the execution layer, so that caller modules do not duplicate regex parsing logic.
6. As a developer writing new agent tools, I want invalid model names and uncalibrated reasoning efforts to fail closed up front before spawning processes, so that faulty arguments cannot trigger undefined CLI behavior.
7. As a security auditor, I want all child worker invocations to carry the nested worker token automatically, so that child workers cannot trigger recursive routing loops or bypass security boundaries.
8. As an offline test writer, I want subprocess runners and monotonic clocks to be fully injectable through clean seams, so that test suites run instantaneously without requiring network access or external CLIs.
9. As a council review coordinator, I want to execute local-only adjudicator models through the same unified interface as cloud models, so that privacy-sensitive tasks are routed seamlessly without bespoke plumbing.
10. As a performance engineer, I want worker invocation errors (such as non-zero exit codes or stderr output) to be captured with full diagnostics, so that debugging faulty model responses is immediate and deterministic.

## Implementation Decisions

1. **Deep WorkerInvocation Module Architecture**:
   - The unified runtime resides in the central worker-routing core and exports both synchronous and asynchronous execution interfaces.
   - External reviewer adapter classes in other skills become thin facades that configure model identifiers and invoke the central engine.

2. **Result Object Contract**:
   - Introduce a structured result type representing an execution outcome:
     - `raw_output`: Complete string output captured from stdout.
     - `duration_ms`: Non-negative integer of elapsed wall-clock milliseconds.
     - `cost_estimate_usd`: Derived floating-point estimate based on the normalized model rate table.
     - `success`: Boolean indicating zero exit code and absence of timeout/execution errors.
     - `error`: Optional error diagnostic string if execution failed.
     - `parsed_payload`: Optional dictionary containing structured JSON fields (such as `vote`, `confidence`, `findings`, `candidate_hash`) extracted from output.

3. **Subprocess Management & Reaping**:
   - Subprocesses are spawned with `stdin=subprocess.DEVNULL` (or async equivalent) to prevent terminal blocking.
   - On timeout, the subprocess is explicitly killed and reaped via `proc.kill()` followed by awaiting process termination (`proc.wait()`), preventing zombie processes.
   - Non-zero process exit codes and timeout expirations are wrapped in structured diagnostic exceptions.

4. **Model & Effort Validation**:
   - Models are normalized via a centralized alias mapping against known families (`claude`, `codex`, `agy`/`gemini`, `lm-studio`).
   - Reasoning effort strings are validated against the standard vocabulary (`low`, `medium`, `high`, `ultra`) before spawning any subprocess.

5. **Journal Integration Seam**:
   - Maintain the `make_journaled_invoke_worker` factory pattern, enabling seamless closed-over journaling for task-bound dialogues while allowing raw invocations where standalone execution is required.

## Testing Decisions

- **Behavioral Testing Over Internals**: Tests will verify that given model names and prompts produce valid command lists, correct results, and properly formatted journal records without asserting on private helper implementation details.
- **Injectable Test Seams**: Use injectable runner functions and clock callables to simulate subprocess success, failure, non-zero exits, and timeouts deterministically.
- **Test Modules**:
  - Extend existing worker invocation unit tests to cover async execution, parallel batch execution, and process timeout reaping.
  - Run the complete Council Review test suite to verify that existing review workflows and security veto mechanisms operate unchanged through the unified engine.
- **Prior Art**:
  - `skills/worker-routing/test_production_invoker.py` (offline runner injection and rate table validation).
  - `.agent/skills/council-review/tests/test_council_review.py` (multi-agent async review round testing).

## Out of Scope

- Modifying the external CLI binaries (`claude`, `codex`, `agy`) or their internal command protocols.
- Adding dynamic billing APIs or live provider token counting (cost remains estimated via rate table derivation).
- Changing the high-level debate protocols of `AdvisoryConsultation` or `CouncilReview`.

## Further Notes

- The addition of `WorkerInvocation` to `CONTEXT.md` establishes this module as the canonical execution seam for all present and future agent-routing capabilities.
