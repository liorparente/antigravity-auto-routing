# 21 — Mistakes self-correct and leave a trail

**What to build:** The weekly run compares the current scoreboard against the pre-adoption baseline.
When a metric regresses and the regression is attributable to an adopted change, that change is
automatically reverted and the revert is written into the report.

This is the other half of what makes self-modification safe: ticket 19 makes undo cheap, this ticket
makes undo automatic. Without it the loop can only ever accumulate.

**Blocked by:** 17, 19, 20

**Status:** ready-for-agent

- [ ] A post-adoption regression triggers an automatic revert of the attributable change.
- [ ] The reverted state matches the pre-adoption version exactly.
- [ ] The revert appears as its own entry in the weekly report, naming the change and the metric that
      regressed.
- [ ] A regression not attributable to any adopted change reverts nothing and is reported as such.
- [ ] A reverted change is not re-adopted by the next weekly run on the same evidence.
- [ ] Tests cover an attributable regression, an unattributable one, and no regression at all.
