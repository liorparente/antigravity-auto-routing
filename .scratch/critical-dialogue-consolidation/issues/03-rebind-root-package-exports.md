# 03 — Migrate: Re-bind Root Package Exports and Internal Callers

**What to build:** Update `skills/worker-routing/__init__.py` and all internal package callers to import directly from `critical_dialogue.py` instead of the legacy `advisory_consultation.py` and `council_review.py` facades.

**Blocked by:** 02 — Expand: Implement the Deep CriticalDialogue Module

**Status:** ready-for-agent

- [ ] Update `skills/worker-routing/__init__.py` to import dialogue symbols from `critical_dialogue.py`
- [ ] Update all internal callers in `skills/worker-routing/` and `skills/council-review/` to target `critical_dialogue.py`
- [ ] Verify that package-level symbol resolution and `__all__` sorting tests pass cleanly
