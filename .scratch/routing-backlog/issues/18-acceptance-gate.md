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
with ticket 26's schema and writer, per this ticket's own "land the two together" note). Acceptance
requires every trial to individually clear `score_threshold` — never a mean — which is what makes a
single winning run among losing ones reject on its own; `ScoreboardComparison.has_regression`
(`learning_scoreboard.py`, already computed for ticket 16) rejects independently of the score, even
an excellent one, including when the batch's own trials are what drags the replay-benchmark family's
own trend down (see `acceptance_gate.py`'s module docstring and
`ScoreboardRegressionRejectionTests.test_a_batch_that_drags_down_its_own_benchmark_trend_regresses_too`).
A runner failure — raising, or returning a value `ReplayBenchmarkRecord` itself refuses — is caught
per trial and journaled as `success=False`, never re-raised and never assumed good. A *journal write*
failure rejects too (`GateDecision.journal_complete`): the batch's evidence exists only in memory, the
`current` board read back from disk sees none of it, so nothing regresses and the gate would otherwise
open on evidence that no longer exists. Two follow-on tickets were filed rather than resolved here —
29 (`mean_benchmark_score` blends task sets) and 30 (the regression check compares the batch against
its own trials); 30 revisits the "deliberately" in the paragraph above and should be read alongside
it.

- [x] The gate runs the benchmark set a configured number of times through an injected runner.
- [x] A proposal is accepted only when the score meets threshold and no scoreboard metric regresses.
- [x] A single winning run among losing runs is rejected.
- [x] A regression in any one metric rejects, even when the benchmark score is excellent.
- [x] No code path lets a proposal's own output influence its score.
- [x] A runner failure mid-trial fails closed — the proposal is rejected, not assumed good.
- [x] Tests cover acceptance, single-lucky-run rejection, single-metric-regression rejection, and
      runner failure.
