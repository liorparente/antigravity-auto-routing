# 47 — Two-Tab Navigation & Bento Grid Layout

**GitHub Issue:** [#23](https://github.com/liorparente/antigravity-auto-routing/issues/23)

**What to build:** An updated HTML report layout in `learning_report_html.py` featuring Google Stitch's Ethos Analytics design system with a top tab bar (מדדי ביצוע vs הגדרת תפקידים) and a Bento Grid of role cards.

**Blocked by:** 46 — Model Capability Registry & Schema Contracts

**Recommended Worker:** Tier 0 (LM Studio Local $0) / Tier 1 (Fast Flash)

**Status:** ready-for-agent

- [ ] Render top navigation tab bar allowing switching between Metrics (Tab 1) and Role Matrix (Tab 2).
- [ ] Render the Bento Grid of role cards with colored accent sidebars and capability pill badges.
- [ ] Include segmented view toggle for primary roles ('תפקידי מפתח') vs granular sub-roles ('פירוט מלא').
- [ ] Ensure all dynamic strings are properly escaped via `_escape`.