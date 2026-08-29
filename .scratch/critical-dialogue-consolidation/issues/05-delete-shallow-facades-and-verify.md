# 05 — Contract: Delete Shallow Facades and Verify Total Cleanliness

**What to build:** Delete `skills/worker-routing/advisory_consultation.py`, `skills/council-review/scripts/council_review.py`, and `skills/worker-routing/debate_orchestrator.py`. Verify clean linting (`ruff check`), strict static typing (`mypy`), and complete test suite execution (`python3 -m unittest test_suite.py`).

**Blocked by:** 04 — Migrate: Update Test Suites to Target CriticalDialogue

**Status:** ready-for-agent

- [ ] Delete `skills/worker-routing/advisory_consultation.py`
- [ ] Delete `skills/council-review/scripts/council_review.py`
- [ ] Delete legacy `skills/worker-routing/debate_orchestrator.py`
- [ ] Run `ruff check .` with zero lint errors
- [ ] Run `mypy skills/worker-routing` with zero type errors
- [ ] Run `python3 -m unittest test_suite.py` with 100% pass rate
