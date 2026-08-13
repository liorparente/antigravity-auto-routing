# 08 — Seeded-flaw canaries

**What to build:** On a schedule (about one in twenty dialogues or weekly, whichever comes first —
config, not a literal), a Critic invocation is given a plan from a fixture library of documented
seeded flaws instead of a real mission artifact. Approving a canary (per the ticket-02
VerdictContract) is recorded as a canary miss; objecting to the seeded flaw is a catch. A canary run
never produces a real plan/diff artifact and never feeds a real mission's outcome — it is pure
measurement.

**Blocked by:** 02.

**Status:** done

- [x] The canary schedule is driven by config (cadence and/or dialogue count), not a hardcoded
      literal, and is independently testable (e.g. inject a clock/counter fake).
- [x] A canary dialogue draws its artifact from the seeded-flaw fixture library, not from the real
      mission.
- [x] A Critic approving a canary is recorded as a miss; objecting is recorded as a catch — both are
      observable in telemetry.
- [x] A canary run does not write a mission plan/diff artifact to the injected root directory, and
      does not affect the real mission's consultation outcome.
- [x] The fixture library ships with at least one documented seeded flaw usable by the tests.

## Notes

Landed in commit `c3b5eae`. `is_canary=True` skips the Planner, shows the Critic a
`CANARY_FIXTURES` entry, records `canary_result: "miss" | "catch"` via a new `outcome="canary"`.
Cadence: `is_canary_dialogue`, config-driven, fires on whichever of dialogue-count/time threshold
comes first. `/code-review` caught three real issues: a missing named type alias
(`Literal["miss","catch"]` spelled out four times instead of once), two stale "five outcomes"
docstrings, and — the important one — a canary's `task_id` originally collided with the real
mission's (same digest-of-task-description), which would let a task_id-grouping consumer (like
spec 0004's future LearningJournal) silently fold canary noise into real metrics. Fixed by
extending the existing `sensitivity_halt` random-id special case to cover canaries too. 254 tests
pass. Noted, not fixed: canaries always probe a single Critic slot, never `critic_b` under panel
topology — a real coverage gap for the highest-stakes tier, flagged for whoever scopes future work
on the scoreboard/dashboard (ticket 10 / spec 0004), not a defect in this ticket's own scope.
