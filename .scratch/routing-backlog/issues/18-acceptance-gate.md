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

**Status:** done — commit `e934fcb` (`acceptance_gate.py`, `test_acceptance_gate.py`; landed together
with ticket 26's schema and writer, per this ticket's own "land the two together" note). Ticket 30
and ADR 0008 settle the regression-check scope: acceptance requires every trial to individually clear
`score_threshold` — never a mean — durable journal evidence, and no concurrent non-benchmark
regression. `mean_benchmark_score` remains visible in `ScoreboardComparison` but its anti-ratchet
protection lives in Ticket 21's post-adoption auto-revert.
A runner failure — raising, or returning a value `ReplayBenchmarkRecord` itself refuses — is caught
per trial and journaled as `success=False`, never re-raised and never assumed good. A *journal write*
failure rejects too (`GateDecision.journal_complete`): the batch's evidence exists only in memory, the
`current` board read back from disk sees none of it, so nothing regresses and the gate would otherwise
open on evidence that no longer exists. Two follow-on tickets were filed rather than resolved here —
29 (`mean_benchmark_score` blends task sets) and 30 (the regression check compares the batch against
its own trials); 30 revisits the "deliberately" in the paragraph above and should be read alongside
it.

- [x] The gate runs the benchmark set a configured number of times through an injected runner.
- [x] A proposal is accepted only when every trial meets threshold, journal persistence is complete,
      and no concurrent non-benchmark scoreboard metric regresses; benchmark anti-ratchet is Ticket 21's
      post-adoption auto-revert (ADR 0008).
- [x] A single winning run among losing runs is rejected.
- [x] A regression in any one metric rejects, even when the benchmark score is excellent.
- [x] No code path lets a proposal's own output influence its score.
- [x] A runner failure mid-trial fails closed — the proposal is rejected, not assumed good.
- [x] Tests cover acceptance, single-lucky-run rejection, single-metric-regression rejection, and
      runner failure.
