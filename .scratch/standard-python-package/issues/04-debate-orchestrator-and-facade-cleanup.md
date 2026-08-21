# 04 — Debate Orchestrator & Advisory Consultation Facade Cleanup

**GitHub Issue:** [#13](https://github.com/liorparente/antigravity-auto-routing/issues/13)
**What to build:** Eradicate `_load_sibling` from `debate_orchestrator.py` and replace the dynamic `_modules` / `__getattr__` loop in `advisory_consultation.py` with static re-exports.

**Blocked by:** [03 — Debate State Machine, Transport & Invoker Loader Elimination (#12)](https://github.com/liorparente/antigravity-auto-routing/issues/12)

**Status:** closed

- [x] Replace all `_load_sibling` calls in `debate_orchestrator.py` with clean relative imports.
- [x] Replace dynamic `_modules` and `__getattr__` in `advisory_consultation.py` with explicit static imports.
- [x] Verify `test_debate_orchestrator.py` and `skills/council-review/tests/test_council_review.py` pass with zero regression.
