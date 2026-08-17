# 03 — Refactor Council Reviewer Adapters to Thin Facades

**What to build:** Refactor `provider_adapters.py` in `.agent/skills/council-review/scripts/` to be a thin facade delegating directly to `production_invoker.py`, eliminating duplicate subprocess execution logic, duplicate CLI argument construction, and separate regex extraction routines while keeping all public adapter classes (`ClaudeAdapter`, `CodexAdapter`, `AgyAdapter`, `LMStudioAdapter`, `FakeReviewerAdapter`, `build_adapter`) fully functional and API-compatible.

**Blocked by:** 02 — Async Worker Invocation Engine & Timeout Process Reaping

**Status:** ready-for-agent

- [ ] Refactor `CLIReviewerAdapter.review()` to delegate subprocess execution to `production_invoker.invoke_worker_async`.
- [ ] Remove duplicate subprocess spawning, duplicate argument lists, and redundant regex parsing from `provider_adapters.py`.
- [ ] Verify that all 7 Council Review unit tests in `test_council_review.py` pass cleanly without errors.
