# 39 — Slim Facade Integration & 100% Test Compatibility (`debate_orchestrator.py`)

* GitHub Issue: [#8](https://github.com/liorparente/antigravity-auto-routing/issues/8)
* Spec: [docs/specs/0008-debate-engine-modular-decomposition.md](file:///Users/liorparente/Projects/auto-routing/docs/specs/0008-debate-engine-modular-decomposition.md)

**Blocked by:** 36 — Pure Debate State Machine Reducer, 37 — Isolated Worker Process Transport, 38 — Deepen Leaf Modules: Contract & Transcript Extraction

**Status:** ready-for-agent

- [ ] Wire `debate_orchestrator.py` to delegate to `debate_state_machine.py` and `debate_transport.py`.
- [ ] Re-export all legacy symbols, constants, and entry points (`run_advisory_consultation_debate`, `run_critical_dialogue`).
- [ ] Verify `python3 -m unittest discover -s skills/worker-routing` passes all 986+ tests with 0 errors.
- [ ] Run `ruff check skills/worker-routing/` and `mypy skills/worker-routing/` with 0 warnings.
