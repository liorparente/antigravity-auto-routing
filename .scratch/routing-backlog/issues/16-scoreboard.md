# 16 — The scoreboard: four metric families from the journal

**What to build:** A pure computation over journal records producing four metric families:

- **Discipline** — protocol violation rate per session.
- **Critique authenticity** — canary catch rate and engagement-count trends.
- **Efficiency** — escalation rate, rework counts, cost per completed task.
- **Replay benchmark** — the trend of the fixed task set's score over time.

No clock of its own: the current time is an input. No I/O beyond reading the journal. This is the
component the acceptance gate compares against, so it has to be deterministic given the same records.

Critique authenticity is fed by dialogue-quality records (ticket 24, unowned since spec 0003 closed
without writing them). Until those records exist the metric must report that it has no data — not
zero, and not a crash.

**Two other families are short of data for the same reason, and "computed from journal records
alone" is the criterion that exposes it.** The outcome family has entry points and no callers until
ticket 25, so escalation rate and cost per *completed* task have nothing to read; and no record
family carries a benchmark score at all until ticket 26, so the replay-benchmark trend has no
source. Only discipline is fully fed today (compliance records, written by every audit run), and
efficiency is fed in part — rework counts come from worker-execution records, which every
production consultation already writes. Build the no-data path first and treat it as the normal
case rather than the edge one: on the day this ships, most of the board takes it.

**Blocked by:** 13, 14, 15 (data sources: 24, 25, 26)

**Status:** done — commits `0c8ed7c`, `5a26606` (the journal reader), `5c0ab0a`, `179eb21`,
`0ddbcb9` (`learning_scoreboard.py`, all eight metrics, `compare_scoreboards`), `dabfc5f`, `8cab197`
(two rounds of a post-close `/code-review` closing findings on the no-clock self-test). Plan at
`.scratch/plans/16-scoreboard.md`.

- [x] Each of the four metric families is computed from journal records alone.
- [x] The same journal produces the same scoreboard every time; the current time is an injected
      input rather than read from the system clock.
- [x] A metric family with no records reports "no data" and is distinguishable from a genuine zero.
- [x] An empty journal produces a scoreboard rather than an error.
- [x] Two scoreboards can be compared, and the comparison names which metrics improved, held, and
      regressed.
- [x] Tests cover each family, the no-data case, the empty journal, and the comparison.

Two metrics ship as permanent `MetricNoData`, by design and not a gap in this ticket:
`escalation_rate` has no journal producer at all (`agent_council.py` writes no such event), and
`mean_benchmark_score` has no record family to read until ticket 26. Both are named explicitly in
`compute_scoreboard`'s own comments, and the empty-journal test asserts all eight metrics — including
these two — take the no-data path.
