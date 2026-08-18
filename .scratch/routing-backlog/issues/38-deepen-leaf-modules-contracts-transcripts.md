# 38 — Deepen Leaf Modules: Contract & Transcript Extraction (`dialogue_contracts.py` & `dialogue_transcript.py`)

* GitHub Issue: [#7](https://github.com/liorparente/antigravity-auto-routing/issues/7)
* Spec: [docs/specs/0008-debate-engine-modular-decomposition.md](file:///Users/liorparente/Projects/auto-routing/docs/specs/0008-debate-engine-modular-decomposition.md)

**Blocked by:** None — can start immediately.

**Status:** ready-for-agent

- [ ] Move quote verification and atomic objection parsing into `dialogue_contracts.py`.
- [ ] Move transcript markdown layout, executive summary rendering, and sensitivity redaction display into `dialogue_transcript.py`.
- [ ] Ensure leaf modules remain pure with zero upward dependencies on the orchestrator or state machine.
- [ ] Unit tests verifying that contract parsing and transcript generation function identically.
