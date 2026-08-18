# 37 — Isolated Worker Process Transport (`debate_transport.py`)

* GitHub Issue: [#6](https://github.com/liorparente/antigravity-auto-routing/issues/6)
* Spec: [docs/specs/0008-debate-engine-modular-decomposition.md](file:///Users/liorparente/Projects/auto-routing/docs/specs/0008-debate-engine-modular-decomposition.md)

**Blocked by:** None — can start immediately.

**Status:** ready-for-agent

- [ ] Implement `DebateTransport` with robust subprocess execution, environment isolation, and PTY wrapping.
- [ ] Convert unhandled exceptions and CLI timeouts into graceful `abstain` votes (`confidence=0.0`) so transient failures do not crash the deliberation loop.
- [ ] Implement `RecurringFailureNotifier` logging repeated model errors to `ERRORS.md` and raising prominent alerts.
- [ ] Comprehensive unit tests in `skills/worker-routing/test_debate_transport.py` covering timeout handling and failure alerts.
