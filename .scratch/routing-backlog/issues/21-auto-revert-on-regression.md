# 21 — Mistakes self-correct and leave a trail

**What to build:** The weekly run compares the current scoreboard against the pre-adoption baseline.
When a metric regresses and the regression is attributable to an adopted change, that change is
automatically reverted and the revert is written into the report.

This is the other half of what makes self-modification safe: ticket 19 makes undo cheap, this ticket
makes undo automatic. Without it the loop can only ever accumulate.

**Blocked by:** 17, 19, 20

**Note:** `learned_state.roll_back` refuses (with a `ValueError`) to undo the *first* adoption ever
made — the state before it is the un-learned system, which that store does not model. This ticket's
revert runs unattended, so it must handle that refusal rather than propagate it: spec 0004's user
story 18 promises a revert for "an adopted change" with no carve-out, and the first-ever adoption is
reachable here, since a memory lesson auto-applies without a gate (ticket 20's tier 1). Decide
whether the revert reports the regression as unrevertable, or whether ticket 19's store grows a
representation of the pre-first-adoption state — but do not discover it as an uncaught exception in
a scheduled run.

**Status:** ready-for-agent

- [ ] A post-adoption regression triggers an automatic revert of the attributable change.
- [ ] The reverted state matches the pre-adoption version exactly.
- [ ] The revert appears as its own entry in the weekly report, naming the change and the metric that
      regressed.
- [ ] A regression not attributable to any adopted change reverts nothing and is reported as such.
- [ ] A reverted change is not re-adopted by the next weekly run on the same evidence.
- [ ] Tests cover an attributable regression, an unattributable one, and no regression at all.
