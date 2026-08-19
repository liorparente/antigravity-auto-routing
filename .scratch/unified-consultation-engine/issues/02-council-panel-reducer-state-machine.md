# 02 — Council Panel Reducer & Multi-Critic Weighted Quorum in `debate_state_machine.py`

**What to build:** Extend the pure reducer state machine (`advance_debate_state`) to support the `CouncilPanel` topology — receiving multiple critic votes per round, computing weighted consensus scores against the quorum threshold, and transitioning debate status deterministically with zero I/O and zero subprocess dependencies.

**Blocked by:** 01 — Centralize Consultation Policy in `routing-config.json`

**Status:** done

- [x] Add `CouncilPanel` support in `debate_state_machine.py`.
- [x] Implement pure weighted consensus calculation and quorum threshold comparison.
- [x] Add 100% offline unit tests in `skills/worker-routing/test_debate_state_machine.py`.
