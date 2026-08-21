# 05 — Learning Journal & State Store Sibling Loader Elimination

**GitHub Issue:** [#14](https://github.com/liorparente/antigravity-auto-routing/issues/14)
**What to build:** Eradicate bare/dynamic sibling imports from the learning-journal and state-store
subsystem, replacing them with the standard hybrid package/standalone import header
(`if __package__: from . import ... else: import ...  # type: ignore[no-redef]`). `learning_journal.py`
and `learned_state.py` are true leaves with no cross-module imports of their own (by design — see
their module docstrings) and needed no change; the six modules that do import siblings were
converted: `learning_outcomes.py`, `learning_scoreboard.py`, `learning_report.py`,
`learner_worker.py`, `risk_tiered_application.py`, and `acceptance_gate.py`.

**Blocked by:** [04 — Debate Orchestrator & Advisory Consultation Facade Cleanup (#13)](https://github.com/liorparente/antigravity-auto-routing/issues/13)

**Status:** complete

- [x] Convert `learning_outcomes.py`, `learning_scoreboard.py`, `learning_report.py`,
      `learner_worker.py`, `risk_tiered_application.py`, and `acceptance_gate.py` to the standard
      hybrid package/standalone import header.
- [x] Eliminate `importlib.util.spec_from_file_location` loader hacks from
      `test_learned_state.py`, `test_learning_scoreboard.py`, `test_learning_report.py`,
      `test_acceptance_gate.py`, and `test_risk_tiered_application.py` / `test_learner_worker.py`
      (which already used bare imports), replacing them with the standard test header
      (`if __package__ is None or __package__ == "": sys.path.insert(...)`) plus hybrid imports.
      `test_learner_worker.py`'s `TestSeamAndSeparationTests.test_module_never_imports_learned_state`
      AST assertion is preserved and strengthened via shared `_imported_module_names` helper to cover relative imports.
- [x] Verify `test_learned_state.py`, `test_learner_worker.py`, `test_learning_report.py`,
      `test_learning_scoreboard.py`, `test_risk_tiered_application.py`, and `test_acceptance_gate.py`
      pass cleanly (416 tests), plus the full `test_suite` (1080 tests, 1 pre-existing skip).
- [x] `ruff check` and `mypy --config-file pyproject.toml` (the full `PYTHON_MODULES` list from CI)
      are clean of any error introduced by this ticket — mypy now actually type-checks these test
      files instead of treating dynamically loaded modules as `Any`, which surfaced and fixed a
      handful of previously-invisible test-fixture type mismatches. Nine pre-existing mypy errors in
      `test_dialogue_degradation.py`, `test_dialogue_contracts.py`, `test_sensitivity_redactor.py`,
      `test_prompt_assembler.py`, and `test_executive_dialogue_report.py` predate this ticket and are
      out of scope (Tickets 02–04).
