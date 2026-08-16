# Implementation Plan — Ticket 31: Batch Retrospective Architecture Resolution (ADR 0009)

Settled the open design decision for `run_weekly_deep`'s batch retrospective, establishing that it operates as a **one-shot synthesis** (`invoke_worker`) rather than a multi-round advisory dialogue.

## User Review Required

- **Architectural Decision:** ADR 0009 formalizes that the weekly batch retrospective in `learner_worker.run_weekly_deep` remains a one-shot worker prompt (`invoke_worker`) producing structured JSON proposals (`routing_table_update`, `brief_update`, `memory_lessons`, `retrospective_summary`).
- **Safety Boundaries:**
  - **Tier 2 (Routing Table)**: Evaluated pre-adoption by Acceptance Gate (`acceptance_gate.evaluate_proposal`, ADR 0008) via config-sourced benchmark trials (`trials=5`, `score_threshold=0.8` in `routing-config.json`), zero regression on concurrent live metrics, and fail-closed journaling.
  - **Tier 3 (Briefs)**: Staged as pending proposals requiring explicit human review and approval.
  - **Tier 1 (Memory Lessons)**: Auto-applied directly for low-stakes institutional memory, guarded by intra-run consolidation, anti-flapping checks, and post-adoption anti-ratchet rollback (`revert_attributable_regression`).

## Codebase Design & Deep Module Principles

- **Interface Preservation:** Preserves the lean, decoupled `InvokeWorker = Callable[[str, str, str], str]` seam in `learner_worker.py`.
- **Fail-Closed & Autonomy:** Eliminates stalemate blocks on unattended background scheduler runs.
- **Spec & Backlog Consistency:** Fully synchronizes ADR 0009, Spec 0004, `learner_worker.py` docstrings, and backlog issues 22 and 31.

## Proposed Changes

### Architecture Decision Records
- **[NEW] `docs/adr/0009-batch-retrospective-one-shot-synthesis.md`**: Formulated ADR 0009.

### Learner Worker Module
- **[MODIFY] `skills/worker-routing/learner_worker.py`**: Updated module docstring and `run_weekly_deep` docstring to reflect one-shot synthesis and exact tiering boundaries.

### Specifications & Backlog
- **[MODIFY] `docs/specs/0004-learning-loop.md`**: Updated User Story 10, Implementation Decisions, and test cases (line 186) to align with ADR 0008 and ADR 0009.
- **[MODIFY] `.scratch/routing-backlog/issues/22-learner-worker.md`**: Updated terminology to "synthesis" and referenced ADR 0009.
- **[MODIFY] `.scratch/routing-backlog/issues/31-batch-retrospective-dialogue-occasion.md`**: Marked `Status: complete` with all criteria satisfied.

## Verification Plan

### Automated Tests
- `python3 -m py_compile skills/worker-routing/*.py` — 0 errors.
- `shellcheck install.sh uninstall.sh skills/worker-routing/routing-audit.sh` — 0 warnings.
- All 8 offline test suites (867 tests) passing:
  - `skills/worker-routing/test_learner_worker.py` (56 tests)
  - `skills/worker-routing/test_acceptance_gate.py` (24 tests)
  - `skills/worker-routing/test_learning_scoreboard.py` (131 tests)
  - `skills/worker-routing/test_learning_report.py` (44 tests)
  - `skills/worker-routing/test_learned_state.py` (93 tests)
  - `skills/worker-routing/test_risk_tiered_application.py` (23 tests)
  - `skills/worker-routing/test_production_invoker.py` (30 tests)
  - `skills/worker-routing/test_routing.py` (466 tests)
- Multi-harness synchronization: `./install.sh .`
