# 09 — Per-session dialogue budget and degradation ladder

**What to build:** A per-session dialogue budget (numeric cap from config). When the budget is
exhausted, an ordered degradation ladder applies: first reduce rounds, then cheapen the roster
(e.g. fall back toward lighter/local families), then — as the last rung — skip the dialogue
entirely and emit a report instead of running it. Every rung taken emits its own telemetry record;
degradation is never silent, including the skip rung.

**Blocked by:** 01 (and interacts with the roster substitution path from 05/07 for the "cheapen
roster" rung).

**Status:** done

- [x] A session tracks cumulative dialogue spend against the configured budget.
- [x] Crossing the budget triggers rung 1 (reduced rounds) on the next dialogue, observable as a
      lower effective round cap.
- [x] Continued spend under a still-exhausted budget triggers rung 2 (cheaper roster), observable as
      a different family/effort selection than the un-degraded path.
- [x] Full exhaustion triggers rung 3: the dialogue is skipped and a report is produced in its place
      — the caller never silently gets "no dialogue happened" with no trace.
- [x] Each rung transition emits its own telemetry record distinguishable from a normal run.

## Notes

Landed in commit `1932041`. Caller-tracked `session_spend_so_far` int; `resolve_degradation_rung`
maps spend-vs-cap into 4 bands (0/1/2/3), rungs compound. Rung 3 = new `"budget_skipped"` outcome,
zero worker calls, still writes transcript+telemetry. `/code-review` caught one real gap: rung 2
initially only lowered effort, not the roster/model itself, despite the ticket's own text
specifically describing "fall back toward lighter/local families" — fixed to also override the
model via `routing-config.json`'s `light_doer` entry. 279 tests pass.
