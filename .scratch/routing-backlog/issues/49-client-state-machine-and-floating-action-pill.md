# 49 — Client State Machine & Floating Action Pill

**GitHub Issue:** [#25](https://github.com/liorparente/antigravity-auto-routing/issues/25)

**What to build:** A client-side reactive state manager that tracks dirty edits across role cards and displays a glassmorphic floating action pill with Undo, Reset-to-Default, and Save actions.

**Blocked by:** 48 — Reactive Model-Effort Binding & Auto-Snap

**Recommended Worker:** Tier 0 (LM Studio Local $0) / Tier 1 (Fast Flash)

**Status:** ready-for-agent

- [ ] Maintain `currentRoles`, `savedSnapshot`, and an `undoHistory` stack.
- [ ] Render the sticky bottom floating action pill with pulsing dirty indicator when changes exist.
- [ ] Implement `undoChange()` to revert the last uncommitted edit.
- [ ] Implement `resetDefaults()` with a confirmation prompt to restore system presets.
- [ ] Add toast notification system for instant action feedback.