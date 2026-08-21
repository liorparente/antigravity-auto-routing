# 03 — Debate State Machine, Transport & Invoker Loader Elimination

**GitHub Issue:** [#12](https://github.com/liorparente/antigravity-auto-routing/issues/12)
**What to build:** Eradicate `_load_sibling` from `debate_state_machine.py`, `debate_transport.py`, `dialogue_transcript.py`, and `production_invoker.py`, fixing the module identity split and ensuring unmocked subprocesses never leak in tests.

**Blocked by:** [02 — Core Contracts & Leaf Modules Sibling Loader Elimination (#11)](https://github.com/liorparente/antigravity-auto-routing/issues/11)

**Status:** completed

- [x] Replace `_load_sibling` in state machine, transport, transcript, and invoker with standard relative imports.
- [x] Verify mock monkeypatching in `test_debate_transport.py` and `test_production_invoker.py` intercepts calls seamlessly without module identity splitting.
- [x] Verify `test_debate_state_machine.py` and `test_dialogue_transcript.py` pass cleanly.
