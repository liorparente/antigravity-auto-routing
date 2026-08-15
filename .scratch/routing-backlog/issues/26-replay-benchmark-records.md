# 26 — The replay benchmark leaves a record

**What to build:** A journal home for the replay benchmark's score, and the writer that fills it.

Ticket 16 requires all four metric families to be computed "from journal records alone", and its
fourth family is the replay benchmark's trend over time. No record family carries a score. The
outcome family cannot be made to hold one: `OUTCOME_VERDICTS` pairs every ground truth to a closed
vocabulary of categorical verdicts, deliberately, so that `("tests", "planner")` is unconstructible
— a number has nowhere to sit in that shape.

The runner itself arrives in ticket 18, which is blocked by 16. So the trend is missing a source at
both ends, and no ticket in the graph persists what the runner returns. Left alone, ticket 16 ships
a metric family that reports "no data" forever and ticket 18 produces scores that evaporate the way
audit verdicts did before ticket 15.

Decide where the score lives, and pay that decision's cost openly:

- **A fifth record family** makes the shape obvious and makes "four signal families" wrong in three
  places that currently agree — `CONTEXT.md`'s glossary, `learning_journal.py`'s module docstring,
  and `docs/specs/0004-learning-loop.md`.
- **Extending an existing family** keeps the count honest and needs a field the family's own
  validation can defend, in a module whose whole design is that a malformed record is
  unconstructible rather than merely discouraged.

Either way the schema lands in ticket 12's module — one module owns the record contract, its writers
live elsewhere — and content-freedom applies unchanged: a score is a number and a task set is an
identifier, so neither carries task text, and the existing `_validate_*` helpers already cover both
shapes.

**Blocked by:** 12 for the schema; 18 for the writer

**Status:** done — commit `e934fcb`. Chose "a fifth record family" (`ReplayBenchmarkRecord`,
`kind="replay_benchmark"`) over extending an existing one: the outcome family's `OUTCOME_VERDICTS`
deliberately makes `("tests", "planner")` unconstructible, and a score has nowhere to sit in that
shape without bending an unrelated family's schema around it. The writer lives in `acceptance_gate.py`
(ticket 18), its sole caller. `learning_scoreboard.compute_scoreboard` now computes a real windowed
mean over successful trials for `mean_benchmark_score`, `MetricNoData` only when the window holds
none. The four-family enumeration was corrected in the three places this ticket
named — `learning_journal.py`'s own module docstring, `CONTEXT.md`'s glossary, and
`docs/specs/0004-learning-loop.md` — and in roughly seven more that review kept turning up: inside
`learning_journal.py` alone the `GroundTruth` comment, `_validate_timestamp`'s and `TaskLabel`'s
docstrings, the `JournalRecord` comment and the reader's section header, plus
`learning_scoreboard._prefix_cut`, `learning_report._is_quiet_week`, and two sites in
`test_routing.py`. Treat the three-site list as the ticket's starting guess, not a checklist: a sixth
family should grep, not read this line. "No silent gaps" is enforced at
both ends: a failed trial is journaled as `success=False` rather than omitted, and a trial whose
*write* fails clears `GateDecision.journal_complete` and rejects the proposal, so the gate can never
open on a batch the trend never received. `task_set` is carried by the record as this ticket requires
but is still not read by any consumer — ticket 29 owns that.

- [x] A journal record can carry a replay-benchmark score and the identity of the task set scored.
- [x] The four-family enumeration is left correct everywhere it is written down — glossary, module
      docstring, spec — whether by keeping the count or by correcting every instance of it.
- [x] Each benchmark trial reaches the journal, and a trial that failed is recorded as failed rather
      than omitted, so the trend has no silent gaps.
- [x] Ticket 16's replay-benchmark family reports "no data" until such records exist, and a real
      trend once they do.
- [x] The record is content-free and written beneath the injected root, like every other family.
- [x] Tests cover the record's schema, the no-data case, a multi-trial trend, and a failed trial.
