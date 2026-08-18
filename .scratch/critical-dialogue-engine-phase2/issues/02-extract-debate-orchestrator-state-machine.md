# 02 — Extract Debate Orchestrator State Machine

**What to build:** Extract the multi-round debate loop, multi-critic panel consensus checks, engagement validation, and stalemate escalation logic into a dedicated module (`debate_orchestrator.py`).

**Blocked by:** 01 — Extract Pure Prompt Assembler and Sensitivity Redactor

**Status:** complete

- [x] `debate_orchestrator.py` provides an isolated state machine for managing round transitions and consensus evaluation.
- [x] Supports 1-pair debate and 3-critic panel quorums with fail-closed stalemates.
- [x] Unit tests added in `test_debate_orchestrator.py`.
- [x] Full existing test suite (971 tests) passes without regression.

