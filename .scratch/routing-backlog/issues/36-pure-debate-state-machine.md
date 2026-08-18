# 36 — Pure Debate State Machine Reducer (`debate_state_machine.py`)

* GitHub Issue: [#5](https://github.com/liorparente/antigravity-auto-routing/issues/5)
* Spec: [docs/specs/0008-debate-engine-modular-decomposition.md](file:///Users/liorparente/Projects/auto-routing/docs/specs/0008-debate-engine-modular-decomposition.md)

**Blocked by:** None — can start immediately.

**Status:** completed

- [x] Define `DebateState` and `RoundTurnResult` as immutable `@dataclass(frozen=True)` models.
- [x] Implement `advance_debate_state(current_state: DebateState, round_feedback: RoundFeedback, quorum_policy: QuorumPolicy) -> DebateState` as a pure reducer.
- [x] Support both 1-pair debates and 3-critic panels with unanimous vs qualified quorum evaluation.
- [x] Generate structured `AdvisoryStalemateReport` upon round exhaustion without consensus.
- [x] 100% offline unit tests in `skills/worker-routing/test_debate_state_machine.py` covering all state transitions in <10ms.
