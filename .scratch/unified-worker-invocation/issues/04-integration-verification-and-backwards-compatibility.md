# 04 — Backward Compatibility & End-to-End Integration Verification

**What to build:** Verify complete backwards compatibility across all existing synchronous worker callers (`invoke_worker`, `make_journaled_invoke_worker`), verify that `advisory_consultation.py` debate loops continue to operate seamlessly, and ensure that the entire repository test suite passes with zero regressions.

**Blocked by:** 03 — Refactor Council Reviewer Adapters to Thin Facades

**Status:** completed

- [x] Verify `invoke_worker` and `make_journaled_invoke_worker` retain 100% signature and behavior compatibility.
- [x] Run full test suites: `test_production_invoker.py`, `test_council_review.py`, and `test_routing.py`.
- [x] Ensure all unit tests pass with zero failures and zero new warnings.
