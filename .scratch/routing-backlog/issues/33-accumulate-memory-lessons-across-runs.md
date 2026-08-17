# 33 — Memory lessons don't accumulate across sessions/runs; each `apply_memory_lesson` call replaces

**Status:** complete

**Resolution:** Formalized as [ADR 0010](file:///Users/liorparente/Projects/auto-routing/docs/adr/0010-atomic-bounded-memory-lesson-accumulation.md) (`docs/adr/0010-atomic-bounded-memory-lesson-accumulation.md`) and Planner-Critic consensus in [`.scratch/planning_debate.md`](file:///Users/liorparente/Projects/auto-routing/.scratch/planning_debate.md):
- `learned_state.adopt` gained a content-agnostic `expected_current` Compare-And-Swap (CAS) precondition checked under the store lock.
- `risk_tiered_application.apply_memory_lesson` owns cross-run accumulation, canonical round-trip parsing (`"- "` prefix, 2-space continuations, legacy fallback, malformed-mixture rejection), exact case-sensitive deduplication, FIFO bounding (`DEFAULT_MAX_MEMORY_LESSONS = 200`), atomic CAS retry loop, and `reject_if_candidate_digest` anti-flapping validation against the actual merged candidate.
- `learner_worker.run_weekly_deep` passes `reject_if_candidate_digest=reverted_before_digests.get("memory")` directly.
- All 911 tests passing across all 8 offline test suites.

---

## Historical Context (Prior to ADR 0010)

**Problem originally filed:** Prior to Ticket 33, `run_session_end_light` and `run_weekly_deep` each folded memory lessons from *one run* into a single `apply_memory_lesson` call, but `apply_memory_lesson` replaced `learned_state`'s memory version wholesale. Subsequent sessions or weekly deep runs overwrote earlier lessons, preventing institutional memory from accumulating over project lifetime.

**Design questions settled by ADR 0010:**
- *Ownership:* Accumulation is owned by `risk_tiered_application.apply_memory_lesson`, keeping `learned_state.py` content-agnostic and `learner_worker.py` decoupled.
- *Bounding & Pruning:* Fixed document-wide `DEFAULT_MAX_MEMORY_LESSONS = 200` with FIFO eviction of oldest entries.
- *Anti-Flapping:* Atomic candidate digest validation via `reject_if_candidate_digest`.

**Origin:** Ticket 22 convergence loop (`3cecc61`) Round 2 review pass, finding P-2.
