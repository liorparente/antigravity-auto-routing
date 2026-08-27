# 48 — Reactive Model-Effort Binding & Auto-Snap

**GitHub Issue:** [#24](https://github.com/liorparente/antigravity-auto-routing/issues/24)

**What to build:** Client-side dynamic binding where changing a role's model immediately filters the reasoning effort dropdown to only supported choices and automatically snaps to the model's default effort.

**Blocked by:** 47 — Two-Tab Navigation & Bento Grid Layout

**Recommended Worker:** Tier 0 (LM Studio Local $0) / Tier 1 (Fast Flash)

**Status:** done

- [x] Implement `onModelSelect(roleId, newModel)` in embedded JavaScript.
- [x] Dynamically rebuild effort `<select>` options using `MODEL_CAPABILITIES[newModel].supportedEfforts`.
- [x] Auto-snap role's active effort to `defaultEffort` if previous effort is unsupported.
- [x] Update effort color badge (Emerald for Low, Blue for Medium, Purple for High, Amber for Ultra).

## Delivered

`4566796` (implementation), `ecb70ee` and `209e2d6` (two fix-review rounds).

Two deviations from the text above, both deliberate:

* **Capabilities are keyed `provider::model`, not by bare model id.** The
  checkbox says `MODEL_CAPABILITIES[newModel]`, but bare-model-id keying is
  finding F7 that ticket 46's registry exists to fix, and the collision is
  live: `claude-sonnet-4-6` is in the registry twice with a different effort
  ladder per provider, so one entry would silently overwrite the other.
* **`low` is `#1a7a4c`, a green, not an emerald.** Spec user story 7 says
  "Green for Low"; the ticket's "Emerald" is not spec language. Three other
  rungs are darker than first drafted, and `xhigh` changed hue, because the
  badge paints text over its own color at 10% and the first palette left
  `ultra` at contrast 2.86 (WCAG AA wants 4.5) and `xhigh` only ΔE 6.2 from
  `high`. `medium` therefore moves off the spec-cited `#2563eb`, which
  measured 4.49; that hex is untouched as the theme blue and planner accent.

This is the first document in the family to ship an inline `<script>`, so
ticket 47's "no inline script" invariant is retired and replaced by a
narrower one: exactly two script tags, all dynamic values confined to a
JSON island, the executable block a static literal.