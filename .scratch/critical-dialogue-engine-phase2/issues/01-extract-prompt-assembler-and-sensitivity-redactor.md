# 01 — Extract Pure Prompt Assembler and Sensitivity Redactor

**What to build:** Extract prompt construction, role envelopes, canary injection markers, and sensitivity pattern scanning into dedicated, pure modules (`prompt_assembler.py` and `sensitivity_redactor.py`) with zero process or filesystem I/O dependencies.

**Blocked by:** None — can start immediately.

**Status:** complete

- [x] `prompt_assembler.py` implements pure functions for planner, critic, adjudicator, and stalemate prompts.
- [x] `sensitivity_redactor.py` isolates sensitivity marker matching and random `TaskIdentity` generation for halted runs.
- [x] Comprehensive unit tests added in `test_prompt_assembler.py` and `test_sensitivity_redactor.py`.
- [x] Full existing test suite (966 tests) passes without regression.

