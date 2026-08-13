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

So the work is not plumbing. It is one reduction decision: the journal's `DialogueRound` carries
**one** `engagement_count` per round, while the consultation supplies two integers per Critic and up
to two Critics per round. State the rule in the record's docstring before writing the loop — a
reduction chosen implicitly by whoever types first is a rule the scoreboard then trends forever.

Two things constrain that rule, and neither is a matter of taste:

**Quotes and objections are not interchangeable, and the parser says so at the gate.** An APPROVE
verdict is read as approved only when `verified_quote_count >= 1`; objections never substitute, in
any quantity. The comment at `advisory_consultation.py:133-141` names the rejected alternative
explicitly — "deliberately asymmetric, not `verified_quote_count + objection_count >= 1`" — because
a quote is byte-checked against the artifact while an objection is free text a Critic could fabricate
without reading anything, and `_parse_critic_verdict` (`:1758-1775`) implements exactly that,
returning `unparseable` for a bare approval regardless of `objection_count`. A summed
`engagement_count` would therefore encode, in the metric that exists to make rubber-stamping visible,
the precise equivalence the parser refuses three hundred lines earlier. That is a contradiction
inside one module, not a trade-off between two defensible options.

**Decided 2026-08-13 — quotes-alone.** `engagement_count` is the minimum, over the round's
*participating* Critics, of that Critic's `verified_quote_count`. Objections are not counted at all.
The alternative considered and rejected was quotes-gated (objections counted once at least one
verified quote exists). Two arguments carried it, and the muddled `DialogueRound` docstring wording
was explicitly *not* one of them: a response with zero verified quotes and three objections would
journal `engagement_count=3` for a response `_parse_critic_verdict` classifies as `unparseable` for
carrying no engagement at all; and `min` is already the rule for this record's sibling field, since a
panel round resolves to `approved` only when every Critic approved (`learning_journal.py:877-881`),
so the two fields cannot disagree about whether a round was engaged. The accepted cost, stated rather
than buried: a Critic writing five substantive objections and quoting nothing journals `0`. That is
the safe direction of error — a false low prompts a look at a working Critic, a false high certifies
a fabricating one — and nothing is lost system-wide, because `AdvisoryTelemetryRecord.round_verdicts`
keeps both integers.

**An absent Critic is not a silent one.** `critic_b` is `None` on every pair-mode round by
construction — including a canary's single-Critic probe — so a reduction across Critics must not read
its absence as a second Critic that said nothing. Guarding against one engaged Critic masking a
silent one (the reason a per-Critic minimum is tempting) and guarding against absence being scored as
silence pull in opposite directions; a rule that handles only the first is a quiet defect, and this
ticket's own earlier wording had exactly that lean.

Until this lands the scoreboard's critique-authenticity family reports "no data" (ticket 16), which
means every canary spec 0003 built produces a caught-or-missed result that nothing ever trends.

**Blocked by:** — (12 is done; spec 0003's dialogue machinery is on `main`)

**Status:** implemented — `95c215b` (2026-08-13). Was unowned from filing until 2026-08-13, having been assigned to a spec that had excluded it in writing.

- [x] Each completed dialogue emits one dialogue-quality record via the ticket 12 writer — one per
      dialogue, not one per round.
- [x] The record carries occasion, topology, rounds, per-round verdicts, engagement counts, canary
      results, and the degradation and independence flags.
- [x] Engagement counts reach the record per round, reduced from the `VerdictContractResult`s
      already carried on `round_verdicts` by a rule written into the record's docstring — never
      recomputed from text.
- [x] The reduction never lets objections raise `engagement_count` with no verified quote present,
      and never lets one engaged Critic mask a silent one.
- [x] A pair-mode round's `critic_b is None` is scored as an absent Critic, never as a silent one —
      the reduction runs over participating Critics, not over slots.
- [x] `DialogueRound`'s docstring is corrected in the same change: its redaction claim (a count,
      never the text) stays, and its definition clause stops implying objections are units while the
      writer excludes them. A schema and a writer that disagree is the Occasion/DialogueOccasion
      drift again.
- [x] It correlates to its task by TaskIdentity — the same id the run's worker-execution records
      already journal under, so a dialogue and its invocations read together.
- [x] A canary probe's record is distinguishable from a real dialogue's, so aggregation can count
      catches without counting the probe as ordinary dialogue activity.
- [x] It stays content-free: no proposal text, no critique text.
- [x] A sensitivity-halted consultation writes no dialogue-quality record, consistent with the
      halted-task rule ticket 12 enforces.
- [~] The scoreboard's critique-authenticity family stops reporting "no data" once records exist.
      **Half of this is done and half belongs to ticket 16.** The data half is complete: a real
      consultation now leaves `dialogue_quality` records in the journal, asserted by
      `test_a_real_consultation_leaves_dialogue_quality_data_in_the_journal`. The scoreboard that
      would read them does not exist yet, so nothing has actually stopped reporting "no data" —
      ticket 16 closes this line, not this ticket. Recorded as `[~]` rather than `[x]` because
      ticking it would claim an observable outcome no one can observe.
