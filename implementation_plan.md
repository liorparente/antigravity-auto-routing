# Implementation Plan — Ticket 31: Batch Retrospective Architecture Resolution (ADR 0009)

Settled the open design decision for `run_weekly_deep`'s batch retrospective, establishing that it operates as a **one-shot synthesis** (`invoke_worker`) rather than a multi-round advisory dialogue.

## User Review Required

- **Architectural Decision:** ADR 0009 formalizes that the weekly batch retrospective in `learner_worker.run_weekly_deep` remains a one-shot worker prompt (`invoke_worker`) producing actionable JSON proposals (`routing_table_update`, `brief_update`, `memory_lessons`) and an informational `retrospective_summary`.
- **Safety Boundaries:**
  - **Tier 2 (Routing Table)**: Evaluated pre-adoption by Acceptance Gate (`acceptance_gate.evaluate_proposal`, ADR 0008) via config-sourced benchmark trials (`trials=5`, `score_threshold=0.8` in `routing-config.json`), zero regression on concurrent live metrics, and fail-closed journaling.
  - **Tier 3 (Briefs)**: Staged as pending proposals requiring explicit human review and approval.
  - **Tier 1 (Memory Lessons)**: Auto-applied directly for low-stakes institutional memory, guarded by intra-run consolidation, anti-flapping checks, and post-adoption anti-ratchet rollback (`revert_attributable_regression`).

## Codebase Design & Deep Module Principles

- **Public Interface:** The public interface of `learner_worker` (`run_weekly_deep`, `run_session_end_light`, `DEFAULT_WINDOW_DAYS`, `SessionEndResult`, `WeeklyDeepResult`) is intentionally narrow and declarative. It exposes only high-level cadence entry points and typed result objects.
- **Module Depth:** High depth-to-surface ratio. Behind the simple `run_weekly_deep` entry point, the module encapsulates complex logic: timezone validation, prefix cutting, multi-family window filtering, baseline scoreboard computation, attributable regression rollback, structured JSON extraction, anti-flapping hash digest checks, and weekly markdown report generation.
- **Leverage:** Maximum architectural leverage. By standardizing on the injected `InvokeWorker = Callable[[str, str, str], str]` seam, `learner_worker` avoids dragging in the heavy multi-party state machine of `advisory_consultation.py` (which requires interactive human stalemate resolution and async/urllib dependencies).
- **Locality:** All prompt template construction (`_render_weekly_deep_prompt`), defensive parsing (`_extract_json_object`), and tier-based dispatching remain localized within `learner_worker.py`. Callers pass dependencies and receive structured outcomes without needing internal knowledge of prompt schemas or parser fallbacks.
- **Test Seams:** Comprehensive test seams. Injected `InvokeWorker`, injected `runner` (benchmark scoring), injected `root_dir` (filesystem isolation), and explicit `now` (temporal determinism) allow all 56 unit tests in `test_learner_worker.py` (and 867 total repo tests) to run 100% offline, deterministically, and sub-second.

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
