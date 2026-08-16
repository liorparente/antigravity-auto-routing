# 30 — The acceptance gate's regression check compares the batch against itself

**What to build:** A decision about *what* `acceptance_gate.evaluate_proposal`'s "no scoreboard metric
regresses" rule is supposed to be measuring, and — if the answer is not what it measures today — the
change that makes it measure that.

**What it does now.** `baseline` is read before the trial loop and `current` after it, with the same
injected `now` and the same window. Between the two reads, the *only* thing that changed in the
journal is the batch's own `ReplayBenchmarkRecord`s. So:

1. Seven of the eight metrics are computed over byte-identical record sets and cannot move. In
   ordinary operation the "no metric regresses" rule can only ever fire on `mean_benchmark_score` —
   the one family the trials themselves feed. (`ScoreboardRegressionRejectionTests.test_a_regressed_
   metric_rejects_even_with_an_excellent_score` makes another metric move only by having the injected
   `runner` write a `ComplianceRecord` itself, standing in for a concurrent session.)
2. That check is therefore, in practice, "did this batch drag down its own trailing mean" — which
   `acceptance_gate.py`'s module docstring states outright and defends as deliberate, and which
   ticket 18's own `Status` records.

**The case that it is wrong.** A regression check exists to answer "does adopting this change make
something worse". At gate time nothing has been adopted: the trials are a *probe*, not the system's
post-adoption state. Measuring the probe against the trailing mean of earlier probes has two
consequences the ticket never asked for:

- **The incentive inverts.** The worse the previous batch scored, the lower the trailing mean, and
  the *more easily* the next proposal clears it. A degraded history makes the gate more permissive
  exactly when it should be less.
- **Run-to-run luck comes back.** Requiring every individual trial to clear `score_threshold` is the
  mechanism ticket 18 specifies for removing single-run luck ("the rule that keeps a lucky run from
  reshaping the system"). Adding a second, implicit bar — "beat the trailing mean" — is sensitive to
  the sampling noise of whatever happens to be in the window.

**The case that it is right** is the one the module already makes: without it, a proposal can ratchet
the benchmark down one just-adequate batch at a time, since every batch that merely clears the
threshold is accepted no matter how far below the established trend it sits.

Both are real. What is missing is a decision about where each belongs. A plausible resolution is that
the anti-ratchet rule is genuine but is **ticket 21's** (auto-revert on regression, which compares
post-adoption state against a pre-adoption baseline and is the only place a real before/after exists),
while the gate's own rule should compare states that differ by something other than its own probe.
That is a spec-level question about the boundary between tickets 18, 20 and 21, not a bug fix.

**Origin:** Spec-axis `/code-review` finding during the tickets 18/26 convergence loop, filed rather
than fixed: the behaviour is documented, deliberate, recorded in ticket 18's `Status`, and covered by
a named test (`test_a_batch_that_drags_down_its_own_benchmark_trend_regresses_too`). Reversing a
stated design decision inside a convergence pass is not a fix, and the consequences land in tickets 21
and 22, which are not written yet.

**Suggested handling:** this is an architecture decision, not an implementation task — run it through
`/council-review` or a Planner-Critic dialogue before writing code.

**Blocked by:** none to decide; any implementation should land with or before 21

**Status:** complete — ADR 0008 assigns candidate probe quality to the threshold check, concurrent
non-benchmark regression to the acceptance gate, and `mean_benchmark_score` anti-ratchet protection
to Ticket 21's post-adoption auto-revert.

- [x] A decision is recorded in ADR 0008 on what the gate's regression check compares, and
      on whether the anti-ratchet rule lives in the gate or in ticket 21's auto-revert.
- [x] If the gate's rule changes, `acceptance_gate.py`'s module docstring and ticket 18's `Status`
      both stop asserting the superseded rationale — they currently defend it explicitly.
- [x] `test_a_batch_that_drags_down_its_own_benchmark_trend_regresses_too` is updated to assert the
      decided behaviour, whichever way it goes, rather than deleted.
- [x] Whatever is decided, a proposal's own trials can still never *raise* the bar they are judged
      against — the "learner never grades its own proposal" invariant is untouched by this ticket.
