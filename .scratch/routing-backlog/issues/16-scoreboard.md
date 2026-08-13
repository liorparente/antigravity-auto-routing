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

**Status:** ready-for-agent

- [ ] Each of the four metric families is computed from journal records alone.
- [ ] The same journal produces the same scoreboard every time; the current time is an injected
      input rather than read from the system clock.
- [ ] A metric family with no records reports "no data" and is distinguishable from a genuine zero.
- [ ] An empty journal produces a scoreboard rather than an error.
- [ ] Two scoreboards can be compared, and the comparison names which metrics improved, held, and
      regressed.
- [ ] Tests cover each family, the no-data case, the empty journal, and the comparison.
