# 04 — Generalize Worker Mode Token & Harness-Neutral Invocations

**What to build:** Update prompt_assembler.py and protocol.md to standardize on [WORKER-MODE: NESTED-EXEC] while maintaining backward-compatibility for [WORKER-MODE: AGY-NESTED-EXEC], removing Antigravity-specific CLI assumptions.

**Blocked by:** 03 — Dynamic Role Resolution & Preference Fallback in production_invoker.py

**Status:** ready-for-agent

- [ ] Update WORKER_MODE_TOKEN in skills/worker-routing/prompt_assembler.py to [WORKER-MODE: NESTED-EXEC].
- [ ] Add backward-compatibility check in production_invoker.py and routing_check.py recognizing both NESTED-EXEC and AGY-NESTED-EXEC.
- [ ] Update skills/worker-routing/protocol.md to use harness-neutral language and examples.
- [ ] Verify all prompt assembly unit tests pass in skills/worker-routing/test_prompt_assembler.py.
