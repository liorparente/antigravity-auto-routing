# 01 — Hybrid Package Infrastructure & Root Config

**GitHub Issue:** [#10](https://github.com/liorparente/antigravity-auto-routing/issues/10)
**What to build:** Establish the package root by creating `skills/worker-routing/__init__.py` exporting core public symbols and defining `pyproject.toml` at the repository root.

**Blocked by:** None — can start immediately. (Frontier)

**Status:** completed

- [x] Create `skills/worker-routing/__init__.py` with canonical public exports (`run_critical_dialogue`, `ReviewCouncil`, `LearningJournal`, `LearnedState`, contracts).
- [x] Create `pyproject.toml` at repository root declaring package discovery and tool settings.
- [x] Verify unit test discovery runs cleanly with the package boundary present.
