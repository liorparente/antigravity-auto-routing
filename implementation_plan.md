# Implementation Plan — Ticket 33: Atomic, Bounded Accumulation of Learned Memory Lessons (ADR 0010)

Settled the open architecture and implementation for cross-run memory lesson accumulation, establishing that `risk_tiered_application.apply_memory_lesson` owns accumulation, deduplication, FIFO bounding, and atomic CAS retries under ADR 0010.

## User Review Required

- **Architectural Decision:** ADR 0010 formalizes that `risk_tiered_application.apply_memory_lesson` owns cross-run accumulation.
- **Safety & Concurrency Boundaries:**
  - **CAS Precondition:** `learned_state.adopt` gained `expected_current: Mapping[LearnedDocument, str | None] | None` verified inside `_exclusive_store_lock`.
  - **Atomic Retry Loop:** `apply_memory_lesson` executes a bounded optimistic CAS retry loop (`_MAX_MERGE_RETRIES = 8`).
  - **Round-Trip Grammar:** Canonical entries start with `"- "`; multiline continuations use 2-space indentation. Legacy unbulleted documents preserve as single entries; malformed mixtures fail closed.
  - **Deduplication & Bounding:** Exact case-sensitive deduplication. FIFO capacity bound `DEFAULT_MAX_MEMORY_LESSONS = 200`.
  - **Anti-Flapping:** Atomic validation via `reject_if_candidate_digest` matching the actual merged candidate document.

## Codebase Design & Deep Module Principles

- **Public Interface:** The public interface of `risk_tiered_application` (`apply_memory_lesson`, `DEFAULT_MAX_MEMORY_LESSONS`) remains narrow and declarative.
- **Module Depth:** High depth-to-surface ratio. Behind `apply_memory_lesson`, the module encapsulates round-trip grammar parsing, universal newline normalization, deduplication, FIFO bounding, atomic CAS retry transaction, and anti-flapping digest validation.
- **Leverage:** Both cadences (`run_session_end_light`, `run_weekly_deep`) and manual callers gain automatic cross-run accumulation without implementing merge logic.
- **Locality:** All parsing and formatting logic concentrates in `risk_tiered_application.py`. `learned_state.py` remains strictly content-agnostic.
- **Test Seams:** Injected `root_dir` (filesystem isolation) and explicit `now` (temporal determinism) allow all unit and integration tests to run 100% offline.

## Implemented Changes

### Architecture Decision Records
- **[NEW] `docs/adr/0010-atomic-bounded-memory-lesson-accumulation.md`**: Formulated ADR 0010.

### Learned State Module
- **[MODIFY] `skills/worker-routing/learned_state.py`**: Added `expected_current` CAS precondition to `adopt`.

### Risk-Tiered Application Module
- **[MODIFY] `skills/worker-routing/risk_tiered_application.py`**: Implemented `DEFAULT_MAX_MEMORY_LESSONS = 200`, grammar parsing, deduplication, FIFO bounding, and atomic CAS retry in `apply_memory_lesson`.

### Learner Worker Module
- **[MODIFY] `skills/worker-routing/learner_worker.py`**: Added multiline continuation formatting (`_format_lesson_entry`) and wired `reject_if_candidate_digest` in `run_weekly_deep`.

### Backlog & Specs
- **[MODIFY] `docs/specs/0004-learning-loop.md`**: Updated User Story 13 and risk-tiered application descriptions.
- **[MODIFY] `.scratch/routing-backlog/issues/33-accumulate-memory-lessons-across-runs.md`**: Marked `Status: complete`.

## Verification Plan

### Automated Tests
- `python3 -m py_compile skills/worker-routing/*.py` — 0 errors.
- `shellcheck install.sh uninstall.sh skills/worker-routing/routing-audit.sh` — 0 warnings.
- `ruff check skills/worker-routing/` — 0 errors.
- `mypy skills/worker-routing/` — 0 errors.
- All 8 offline test suites (911 tests) passing.
- Multi-harness synchronization: `./install.sh .`
