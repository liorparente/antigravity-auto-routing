# 05 — Learning Journal & State Store Sibling Loader Elimination

**GitHub Issue:** [#14](https://github.com/liorparente/antigravity-auto-routing/issues/14)
**What to build:** Eradicate `_load_sibling` from `learning_journal.py`, `learned_state.py`, `learning_outcomes.py`, `learning_scoreboard.py`, `learning_report.py`, `learner_worker.py`, and `risk_tiered_application.py`.

**Blocked by:** [04 — Debate Orchestrator & Advisory Consultation Facade Cleanup (#13)](https://github.com/liorparente/antigravity-auto-routing/issues/13)

**Status:** ready-for-agent

- [ ] Replace `_load_sibling` in all 7 learning subsystem modules with clean relative imports.
- [ ] Verify `test_learned_state.py`, `test_learner_worker.py`, `test_learning_report.py`, `test_learning_scoreboard.py`, and `test_risk_tiered_application.py` pass cleanly.
