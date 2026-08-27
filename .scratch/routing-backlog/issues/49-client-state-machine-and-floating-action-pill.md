# 49 — Client State Machine & Floating Action Pill

**GitHub Issue:** [#25](https://github.com/liorparente/antigravity-auto-routing/issues/25)

**What to build:** A client-side reactive state manager that tracks dirty edits across role cards and displays a glassmorphic floating action pill with Undo, Reset-to-Default, and Save actions.

**Blocked by:** 48 — Reactive Model-Effort Binding & Auto-Snap

**Recommended Worker:** Tier 0 (LM Studio Local $0) / Tier 1 (Fast Flash)

**Status:** done

- [x] Maintain `currentRoles`, `savedSnapshot`, and an `undoHistory` stack.
- [x] Render the sticky bottom floating action pill with pulsing dirty indicator when changes exist.
- [x] Implement `undoChange()` to revert the last uncommitted edit.
- [x] Implement `resetDefaults()` with a confirmation prompt to restore system presets.
- [x] Add toast notification system for instant action feedback.

## Delivered

`8bbb71b`.

One addition beyond the four state/UI items above, not asked for by the
checklist but required to make the fourth one correct: an immutable
`SYSTEM_DEFAULTS` snapshot, captured once at script load, separate from the
mutable `savedSnapshot`. A first draft had `resetDefaults()` read
`savedSnapshot` — the same variable `saveChanges()` moves — so after any
save, "Reset to Default" only reached back as far as the last save, not
the page's actual rendered defaults, contradicting spec 0013 US12's
"restore factory routing presets ... **at any time**." Caught by an
independent two-axis review pass (both Standards and Spec sub-agents ran
twice, once before and once after this fix) and confirmed in a live
browser session before commit, not just under the node test harness.

`onModelSelect`/`onEffortSelect` share a `commitRoleEdit` seam (extracted
during the same review pass to remove a duplicated state-recording shape
between them); `paintRoleValue`/`cloneValue`'s `{model, effort}` parameter
is named `assignment` throughout for the same reason. Both are Standards
findings, not Spec ones.