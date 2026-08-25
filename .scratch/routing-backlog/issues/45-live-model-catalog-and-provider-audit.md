# 45 — Live Model Catalog & CLI Provider Capability Audit

**GitHub Issue:** [#21](https://github.com/liorparente/antigravity-auto-routing/issues/21)

**What to build:** An authoritative audit of real, callable models and reasoning effort parameters accepted by installed CLI tools (`claude`, `codex`, `agy`, and local LM Studio), establishing the genuine model catalog for the project.

**Blocked by:** None — can start immediately.

**Recommended Worker:** Tier 0 (LM Studio Local $0) / Tier 1 (Fast Flash)

**Status:** ready-for-agent

- [ ] Audit actual supported model identifiers from installed CLI providers (`claude`, `codex`, `agy`, LM Studio).
- [ ] Establish exact wire CLI model flags and reasoning effort parameters (`--effort`, `model_reasoning_effort`).
- [ ] Produce `skills/worker-routing/probe_models.py` to probe live model availability dynamically.
- [ ] Map human-readable display labels to exact CLI wire identifiers.