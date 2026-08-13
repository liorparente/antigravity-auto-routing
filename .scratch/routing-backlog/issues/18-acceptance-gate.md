# 18 — The acceptance gate: repeated trials, zero regression

**What to build:** The rule that keeps a lucky run from reshaping the system. A proposal is evaluated
by running the fixed benchmark task set several times — the count is configuration — through an
injected benchmark-runner callable, and is accepted only if the score meets threshold **and** no
scoreboard metric regresses.

This ticket introduces the third and final test seam: the benchmark-runner callable, which returns
scripted scores in tests and drives the real evaluator in production. Do not add a fourth seam.

The learner never grades its own proposal. Scores come from the external runner and nowhere else —
this is the single most load-bearing decision in the spec, because the research it rests on found
that self-assessment measurably degrades performance.

The runner's scores are also the only source for the scoreboard's replay-benchmark family, and this
ticket does not persist them. Ticket 26 owns that record; land the two together, or the gate works
while the trend it is supposed to move stays permanently at "no data".

**Blocked by:** 16 (and pairs with 26, which journals what this runner returns)

**Status:** ready-for-agent

- [ ] The gate runs the benchmark set a configured number of times through an injected runner.
- [ ] A proposal is accepted only when the score meets threshold and no scoreboard metric regresses.
- [ ] A single winning run among losing runs is rejected.
- [ ] A regression in any one metric rejects, even when the benchmark score is excellent.
- [ ] No code path lets a proposal's own output influence its score.
- [ ] A runner failure mid-trial fails closed — the proposal is rejected, not assumed good.
- [ ] Tests cover acceptance, single-lucky-run rejection, single-metric-regression rejection, and
      runner failure.
