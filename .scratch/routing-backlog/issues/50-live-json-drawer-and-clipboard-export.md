# 50 — Live JSON Drawer & Clipboard Export

**GitHub Issue:** [#26](https://github.com/liorparente/antigravity-auto-routing/issues/26)

**What to build:** A collapsible bottom code drawer displaying a real-time syntax-highlighted preview of `routing-config.json` reflecting user adjustments with a single-click clipboard copy button.

**Blocked by:** 49 — Client State Machine & Floating Action Pill

**Recommended Worker:** Tier 0 (LM Studio Local $0) / Tier 1 (Fast Flash)

**Status:** done

- [x] Implement collapsible bottom drawer with JetBrains Mono typography for `routing-config.json` preview.
- [x] Dynamically regenerate the JSON payload string on every role model/effort change.
- [x] Implement "📋 העתק קונפיגורציה" button with clipboard write and success toast.

## Delivered

The drawer previews `CLIENT_STATE.currentRoles` — every role's pending
`{model, effort}` — under a `roles` key, not a full reconstruction of
`routing-config.json`'s `RoleConfig`/`ProviderConfig` shapes. A role's
dropdown (ticket 48) offers any audited `{adapter}::{model_id}` pair with
no guaranteed id in the file's own `providers` section until a save
resolves one; synthesizing a full config shape client-side would mean
either fabricating a provider id or reading the role card's own binding
pill, which ticket 48 never repaints and so goes stale the instant a
role's model changes. Left the full serialization to ticket 51's save
path, which already owns validating a payload through
`routing_config.parse_routing_config`.

`updateConfigDrawer()` hooks into every mutation path ticket 49 already
funnels state changes through (`recordStateChange`, `undoChange`,
`resetDefaults`, `saveChanges`, plus the one-time init call) via a shared
`refreshStateViews()` seam that also calls `updateActionPill()` — a first
draft called both functions separately at each of the five sites, which
an iterative two-axis review caught as the same shape of duplication
`commitRoleEdit` (ticket 49) already exists to avoid, so it was extracted
before commit.

The preview is genuinely syntax-highlighted, not flat text: a hand-rolled
`syntaxHighlightJson`/`escapeHtmlText` pair (no runtime dependency, matching
this module's "zero build step" rule) tokenizes the JSON string and wraps
keys/strings/numbers/booleans/null in colored `<span>`s, escaping
`&`/`</`/`>` *before* wrapping so a role/model value can never inject live
markup into the drawer. A first draft rendered plain monospace text with
no token coloring at all — missing the ticket's own "syntax-highlighted"
line — caught by the same review pass and fixed before commit.

`copyConfigToClipboard()` feature-detects `navigator.clipboard` before
using it: that global is absent in most browsers over `file://`, the
zero-friction standalone mode Implementation Decisions §4 names as this
page's default, so a missing clipboard degrades to an error toast instead
of throwing out of a click handler. It always copies the plain JSON text
(`configPreviewJson()`), never the drawer's highlighted markup. The toggle
and copy buttons are markup siblings, not nested, so a real browser's
click on the copy button never bubbles into the toggle's own listener.