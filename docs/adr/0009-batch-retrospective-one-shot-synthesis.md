# ADR 0009: Batch Retrospective Architecture as One-Shot Synthesis

## Status
Accepted (2026-08-16)

## Context
Ticket 22 implemented the LearnerWorker with two learning cadences: a light session-end pass (`run_session_end_light`) and a deep weekly run (`run_weekly_deep`). In the original specification (Spec 0004) and early wayfinder notes, the weekly retrospective over past tasks was colloquially referred to as a "batch retrospective dialogue". However, the implementation in `learner_worker.py` executes a single one-shot `invoke_worker` prompt that returns structured JSON proposals (`routing_table_update`, `brief_update`, `memory_lessons`, `retrospective_summary`), which are then passed downstream to `risk_tiered_application`.

Ticket 31 surfaced the design question: should `run_weekly_deep` remain a one-shot worker invocation, or should it be converted into a multi-round `advisory_consultation` dialogue under a `post-mortem` occasion?

## Decision
1. **Retain One-Shot Synthesis for Weekly Batch Retrospective**:
   - The weekly deep batch retrospective in `learner_worker.run_weekly_deep` remains a single one-shot `invoke_worker` call that synthesizes weekly journal evidence into structured JSON proposals.
   - It is explicitly not a multi-round `advisory_consultation` debate.

2. **Downstream Risk Tiering and Anti-Ratchet as the Safety Authority**:
   - The output of `run_weekly_deep` is never adopted unconditionally:
     - **Tier 2 (Routing Tables)**: Must clear the Acceptance Gate (`acceptance_gate.evaluate_proposal`, ADR 0008), which enforces multi-trial benchmark verification (`trials` count and `score_threshold`), zero regression on concurrent live metrics, and fail-closed journal persistence.
     - **Tier 3 (Briefs)**: Held as pending proposals requiring explicit human review and approval before adoption.
     - **Tier 1 (Memory Lessons)**: Protected by intra-run consolidation, anti-flapping guards (refusing re-adoption of content reverted in the same run), and the post-adoption anti-ratchet auto-revert (`revert_attributable_regression`).
   - The multi-tiered application architecture is the actual security and quality boundary, making a multi-round dialogue at proposal time redundant.

3. **Prevention of Autonomous Scheduler Deadlocks**:
   - `advisory_consultation` can terminate in stalemates (`AdvisoryStalemateReport`), which require interactive human adjudication to select a resolution option. Because `run_weekly_deep` is an autonomous background scheduler/cron job, blocking on human input during execution is an anti-pattern.
   - Preserving a pure, decoupled `InvokeWorker` seam (`(model, effort, prompt) -> str`) maintains 100% deterministic, offline testability without coupling the learner worker to the interactive dialogue state machine.

4. **Terminology Synchronization**:
   - All references across specifications, docstrings, and backlog tickets are synchronized to describe the weekly run as a "batch retrospective synthesis" or "one-shot batch retrospective", deprecating "dialogue" to match reality.

## Consequences
- **Positive**:
  - Eliminates risk of automated scheduler deadlocks due to debate stalemates.
  - Avoids token waste and latency overhead on weekly batch jobs.
  - Maintains clean separation of concerns and deterministic testability.
  - Fully aligns documentation and code contracts.
- **Negative / Trade-offs**:
  - The retrospective summary and initial proposals are synthesized by a single worker model rather than debated by a Planner-Critic pair. This is mitigated by the Acceptance Gate, human approval gates, and automatic regression rollback.
