# 24 — Dialogue-quality records

**What to build:** The fourth journal family's *writer*. Spec 0003's dialogue machinery emits one
dialogue-quality record per dialogue: occasion, topology, rounds, the per-round verdict sequence,
engagement counts, canary results, and the degradation and independence flags.

**Ownership returned to spec 0004 on 2026-08-13.** This ticket was filed as spec 0003's to
implement, because only that code knows when a dialogue ended and what happened inside it. Spec 0003
has since closed — its tickets 01–11 all `done`, ticket 11 merged into `main` — without the writer
ever landing, and `DialogueQualityRecord` is constructed nowhere outside its own schema tests. A
ticket blocked on a session that no longer exists is not blocked; it is unowned, and it stays
unowned until someone writes their name on it.

Both original blockers are satisfied: the schema landed with ticket 12 in `learning_journal.py`, and
spec 0003's dialogue machinery is on `main`.

What the wiring can read straight off what already exists, and what it cannot:

- `AdvisoryTelemetryRecord` and `AdvisoryDebateResult` already carry `occasion`, `topology`,
  `round_verdicts`, `canary_result`, `degradation_rung`, and `degraded_independence`. The
  vocabularies on both sides are pinned byte-identical by `test_cross_spec_vocabularies_agree`, so
  these map across without translation — and any drift reappears as a test failure rather than a
  runtime `ValueError` in the writer.
- **Engagement counts are the exception, and they are the work.** They exist only inside the parse:
  `VerdictContractResult` carries `verified_quote_count` and `objection_count` per Critic per round,
  and what survives onto the result is the verdict alone. Threading those counts out to the record
  is what this ticket actually costs — and it is the whole point of the family, because an approval
  carrying zero engagement units is exactly the rubber-stamp this metric exists to make visible.

Until this lands the scoreboard's critique-authenticity family reports "no data" (ticket 16), which
means every canary spec 0003 built produces a caught-or-missed result that nothing ever trends.

**Blocked by:** — (12 is done; spec 0003's dialogue machinery is on `main`)

**Status:** ready-for-agent — unowned since spec 0003 closed without it

- [ ] Each completed dialogue emits one dialogue-quality record via the ticket 12 writer — one per
      dialogue, not one per round.
- [ ] The record carries occasion, topology, rounds, per-round verdicts, engagement counts, canary
      results, and the degradation and independence flags.
- [ ] Engagement counts reach the record per round, from the contract parse rather than recomputed
      from text after the fact.
- [ ] It correlates to its task by TaskIdentity — the same id the run's worker-execution records
      already journal under, so a dialogue and its invocations read together.
- [ ] A canary probe's record is distinguishable from a real dialogue's, so aggregation can count
      catches without counting the probe as ordinary dialogue activity.
- [ ] It stays content-free: no proposal text, no critique text.
- [ ] A sensitivity-halted consultation writes no dialogue-quality record, consistent with the
      halted-task rule ticket 12 enforces.
- [ ] The scoreboard's critique-authenticity family stops reporting "no data" once records exist.
