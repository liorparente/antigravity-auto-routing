# 04 — Streamline Consultation Facade and Remove Legacy Dead Code

**What to build:** Slim down `advisory_consultation.py` into a thin (<300 line) facade delegating to the new specialized modules, removing unused dead legacy code while maintaining 100% backward compatibility for all public symbols.

**Blocked by:** 01 — Extract Pure Prompt Assembler and Sensitivity Redactor, 02 — Extract Debate Orchestrator State Machine, 03 — Implement Executive Dialogue Reporting and Budget Alerts

**Status:** complete

- [x] `advisory_consultation.py` reduced and streamlined into modular delegations.
- [x] All public exports (`run_advisory_consultation_debate`, types, constants) preserved identically.
- [x] Dead legacy experiment code and redundant branches removed.
- [x] All 976 unit & integration tests pass (972 passing, 4 expected offline LM Studio) with zero regressions.

