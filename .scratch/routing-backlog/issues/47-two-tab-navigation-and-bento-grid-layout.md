# 47 — Two-Tab Navigation & Bento Grid Layout

**GitHub Issue:** [#23](https://github.com/liorparente/antigravity-auto-routing/issues/23)

**What to build:** An updated HTML report layout in `learning_report_html.py` featuring Google Stitch's Ethos Analytics design system with a top tab bar (מדדי ביצוע vs הגדרת תפקידים) and a Bento Grid of role cards.

**Blocked by:** 46 — Model Capability Registry & Schema Contracts

**Recommended Worker:** Tier 0 (LM Studio Local $0) / Tier 1 (Fast Flash)

**Status:** done

- [x] Render top navigation tab bar allowing switching between Metrics (Tab 1) and Role Matrix (Tab 2).
- [x] Render the Bento Grid of role cards with colored accent sidebars and capability pill badges.
- [x] Include segmented view toggle for primary roles ('תפקידי מפתח') vs granular sub-roles ('פירוט מלא').
- [x] Ensure all dynamic strings are properly escaped via `_escape`.

## Delivered

`b9055ab`, with three false comment claims corrected in `a45d3e7`.

Verified by rendering the real report rather than reading the diff: the tab
bar carries both Hebrew labels, the grid renders 14 role cards (9 roles,
with the 5 primary ones appearing in both grids) each with an accent
sidebar, 199 capability pills, both segmented-toggle labels, and both
`#role-grid-simple` / `#role-grid-all` containers. Escaping was checked by
pushing `<img src=x onerror=...>&"` through a role value and confirming the
raw payload is absent from the document while `&lt;img` is present.

**Note for future readers.** This ticket's tab bar and toggle are pure CSS
(checked-radio sibling selectors), chosen to preserve a then-standing
invariant that the report shipped no inline `<script>` at all. Ticket 48
retired that invariant — the document now has two script tags — so the
CSS-only approach here is no longer a constraint the code is under, only
the way these two controls happen to work.