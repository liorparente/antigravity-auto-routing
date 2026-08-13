# 24 — Dialogue-quality records

**What to build:** The fourth journal family's *writer*. Spec 0003's dialogue machinery emits one
dialogue-quality record per dialogue: occasion, topology, rounds, the per-round verdict sequence,
engagement counts, canary results, and the degradation and independence flags.

**Ownership returned to spec 0004 on 2026-08-13, and the original assignment was never valid.** This
ticket was filed as spec 0003's to implement, on the reasoning that only that code knows when a
dialogue ended and what happened inside it. But spec 0003's own Out of Scope list reads "The
LearningJournal, learner, scoreboard, and weekly report — spec 0004", and this writer is part of the
LearningJournal. The work was assigned to a spec that had already disclaimed it in writing, so no
session was ever going to pick it up — spec 0003 shipped its tickets 01–11 correctly and completely,
and `DialogueQualityRecord` is still constructed nowhere outside its own schema tests.

The lesson worth keeping: a cross-spec ticket must be checked against the *other* spec's Out of
Scope section, not only against what its author believes that team is best placed to do. A ticket
blocked on an owner who never agreed to own it is not blocked; it is unowned, and it stays unowned
silently, because both sides read the same status line as someone else's problem.

Both original blockers are satisfied: the schema landed with ticket 12 in `learning_journal.py`, and
spec 0003's dialogue machinery is on `main`.

Every input this writer needs already exists. Spec 0003 ticket 10 built the telemetry extensions
explicitly "for spec 0004's future LearningJournal to read", and it delivered:

- `AdvisoryTelemetryRecord` and `AdvisoryDebateResult` carry `occasion`, `topology`,
  `round_verdicts`, `canary_result`, `degradation_rung`, and `degraded_independence`. The
  vocabularies on both sides are pinned byte-identical by `test_cross_spec_vocabularies_agree`, so
  these map across without translation — and any drift reappears as a test failure rather than a
  runtime `ValueError` in the writer.
- **Engagement counts included.** `round_verdicts` is a tuple of `AdvisoryRoundVerdict`, and each
  entry holds a `VerdictContractResult` per Critic — `critic_a`, plus `critic_b` on panel rounds —
  each carrying `verdict`, `verified_quote_count`, and `objection_count`. Nothing needs threading
  out of the parser; ticket 10 already retained what the round loop used to discard.

So the work is not plumbing. It is one reduction decision, and it deserves to be made deliberately:
the journal's `DialogueRound` carries **one** `engagement_count` per round, while the consultation
supplies two integers per Critic and up to two Critics per round. Decide how verified quotes and
objections combine, and how a panel round's two Critics combine, and write the rule into the record's
docstring before writing the loop. A reduction chosen implicitly by whoever types first is a rule the
scoreboard then trends forever — and this is the metric that makes rubber-stamping visible, so a
reduction that hides one silent Critic behind one engaged one defeats the family's whole purpose.

Until this lands the scoreboard's critique-authenticity family reports "no data" (ticket 16), which
means every canary spec 0003 built produces a caught-or-missed result that nothing ever trends.

**Blocked by:** — (12 is done; spec 0003's dialogue machinery is on `main`)

**Status:** ready-for-agent — unowned since it was filed, against a spec that had excluded it

- [ ] Each completed dialogue emits one dialogue-quality record via the ticket 12 writer — one per
      dialogue, not one per round.
- [ ] The record carries occasion, topology, rounds, per-round verdicts, engagement counts, canary
      results, and the degradation and independence flags.
- [ ] Engagement counts reach the record per round, reduced from the `VerdictContractResult`s
      already carried on `round_verdicts` by a rule the ticket states out loud — never recomputed
      from text, and never a reduction that lets one engaged Critic mask a silent one.
- [ ] It correlates to its task by TaskIdentity — the same id the run's worker-execution records
      already journal under, so a dialogue and its invocations read together.
- [ ] A canary probe's record is distinguishable from a real dialogue's, so aggregation can count
      catches without counting the probe as ordinary dialogue activity.
- [ ] It stays content-free: no proposal text, no critique text.
- [ ] A sensitivity-halted consultation writes no dialogue-quality record, consistent with the
      halted-task rule ticket 12 enforces.
- [ ] The scoreboard's critique-authenticity family stops reporting "no data" once records exist.
