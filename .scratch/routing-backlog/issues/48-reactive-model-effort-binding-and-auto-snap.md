# 48 — Reactive Model-Effort Binding & Auto-Snap

**What to build:** Client-side dynamic binding where changing a role's model immediately filters the reasoning effort dropdown to only supported choices and automatically snaps to the model's default effort.

**Blocked by:** 47 — Two-Tab Navigation & Bento Grid Layout

**Recommended Worker:** Tier 0 (LM Studio Local $0) / Tier 1 (Fast Flash)

**Status:** ready-for-agent

- [ ] Implement `onModelSelect(roleId, newModel)` in embedded JavaScript.
- [ ] Dynamically rebuild effort `<select>` options using `MODEL_CAPABILITIES[newModel].supportedEfforts`.
- [ ] Auto-snap role's active effort to `defaultEffort` if previous effort is unsupported.
- [ ] Update effort color badge (Emerald for Low, Blue for Medium, Purple for High, Amber for Ultra).
