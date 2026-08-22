# 06 — Perspective Reviewer Prompt Assembly & Contracts

**What to build:** Refactor prompt_assembler.py and dialogue_contracts.py to frame Council prompts around four analytical perspectives (Architecture, Risk, Maintainability, Security) rather than vendor brand names.

**Blocked by:** 04 — Generalize Worker Mode Token & Harness-Neutral Invocations

**Status:** ready-for-agent

- [ ] Define perspective role prompts in prompt_assembler.py for reviewer_architecture, reviewer_risk, reviewer_maintainability, and reviewer_security.
- [ ] Update MISSION_COPY to include perspective-specific review heuristics (interface depth, race conditions, anti-bloat, credential isolation).
- [ ] Update dialogue_contracts.py to parse structured findings and perspective tags.
- [ ] Verify unit tests pass in test_prompt_assembler.py and test_dialogue_contracts.py.
