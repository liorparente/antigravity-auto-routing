# 02 — Extract Degradation Policy & Budget Ladder (Slice 2)

**What to build:** Extract degradation rung resolution (`resolve_degradation_rung`), dialogue cap state calculations, and session budget tracking into a pure, side-effect-free module (`dialogue_degradation.py`). Re-export from `advisory_consultation.py`. Add pure unit tests covering all degradation rungs (0 to 3) without mocking worker subprocesses.

**Blocked by:** 01 — Extract Dialogue Contracts & Verdict Parser (Slice 1).

**Status:** completed

- [x] Extract `resolve_degradation_rung`, `DegradationLadderState`, and budget math constants into `dialogue_degradation.py`.
- [x] Ensure functions are completely pure (deterministic, zero file I/O or subprocesses).
- [x] Re-export all budget and degradation symbols in `advisory_consultation.py`.
- [x] Add direct unit tests for all rungs (rung 0 full, rung 1 reduced, rung 2 degraded independence, rung 3 budget skipped).
- [x] Verify all existing tests in `test_routing.py` pass without regression.
