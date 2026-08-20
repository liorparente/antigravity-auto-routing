# 02 — Core Contracts & Leaf Modules Sibling Loader Elimination

**GitHub Issue:** [#11](https://github.com/liorparente/antigravity-auto-routing/issues/11)
**What to build:** Eradicate `_load_sibling` from leaf contracts and helper modules (`dialogue_contracts.py`, `dialogue_degradation.py`, `prompt_assembler.py`, `sensitivity_redactor.py`, `executive_dialogue_report.py`, `consultation_policy.py`), replacing them with standard relative imports.

**Blocked by:** None — [01 — Hybrid Package Infrastructure & Root Config (#10)](https://github.com/liorparente/antigravity-auto-routing/issues/10) is completed. (Frontier)

**Status:** ready-for-agent

- [ ] Remove `_load_sibling` and `importlib.util` imports from all 6 leaf contract/helper modules.
- [ ] Add clean relative imports (`from .dialogue_contracts import ...`).
- [ ] Verify `test_dialogue_contracts.py`, `test_dialogue_degradation.py`, and `test_sensitivity_redactor.py` pass.
