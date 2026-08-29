# 04 — Migrate: Update Test Suites to Target CriticalDialogue

**What to build:** Update `test_debate_orchestrator.py`, `test_council_review.py`, and related test files to test directly against `critical_dialogue.py`, ensuring all 1,700+ tests pass against the new seam.

**Blocked by:** 03 — Migrate: Re-bind Root Package Exports and Internal Callers

**Status:** ready-for-agent

- [ ] Update import paths in `skills/worker-routing/test_debate_orchestrator.py` (or rename to `test_critical_dialogue.py`)
- [ ] Update import paths in `skills/council-review/tests/test_council_review.py`
- [ ] Ensure all 1,700+ unit and integration tests execute and pass with 100% success rate
