# 27 — Two `plan` records, one `task_id`, and no field that tells them apart

**What to decide:** Ticket 25 gave the outcome family's `plan` ground truth two producers under one
`task_id` — `advisory_consultation.py`'s `_result` records `accepted` automatically when a
plan-producing dialogue (`ambiguity`, `plan-review`) reaches consensus; a developer who later rejects
that same plan records `rejected` by hand, per `protocol.md`'s "Learning-Journal Ground-Truth
Recording" section. Both land in the same append-only stream under the same `task_id` and
`ground_truth="plan"`.

`learning_journal.OutcomeRecord` carries exactly `task`, `ground_truth`, `verdict`, `run_id`,
`timestamp` — no field says who wrote a record or at what stage. So the only way to resolve two `plan`
records for one task is positional: group by `task_id`, and within a group the last record in the
stream wins — file order being write order in an append-only stream, the same reduction `CONTEXT.md`
already documents for `ComplianceRecord`.

A Codex Sol standards review raised this at P1 during ticket 25: downstream learning cannot
distinguish the Critic's approval from the developer's final verdict without that reduction, and
nothing enforces that a consumer actually performs it. Ticket 25 deferred the decision rather than
answering it inline, because `OutcomeRecord`'s schema belongs to ticket 12/14, not to ticket 25's
wiring.

**This ticket is the decision, not a foregone schema change.** Two real answers exist and the
ticket's job is to choose between them, openly:

- **The positional convention is enough.** Every consumer that reduces `OutcomeRecord`s already has
  to group by `task_id` for other reasons (a task can be re-run, ticket 25's own `run_id` narrowing);
  "last one wins" is one more line in that reduction, not a new capability. Document the rule
  explicitly wherever a consumer is written, the way `protocol.md` already states it for `plan`, and
  close this ticket with no schema change.
- **`OutcomeRecord` needs a discriminator.** A field naming who produced a record (the dialogue
  itself vs. a human) or what stage it was written at makes the two `plan` records self-describing
  instead of order-dependent, and protects every future consumer from getting the reduction wrong
  silently. This costs a schema change in a module whose entire design is that a malformed record is
  unconstructible rather than merely discouraged: the new field needs its own validated vocabulary
  (`_validate_choice`, matching the existing `ground_truth`/`verdict` pattern), a content-freedom
  argument as tight as the ones the module's other fields already carry, and a decision about
  records already on disk without it — an old record either defaults to an inferred value or the
  field stays optional and "absent" itself becomes a third case a consumer must handle.

Either way, the ticket that closes this must update `protocol.md`'s stated reduction rule to match
whatever is decided, so the documented rule and the actual mechanism never drift apart.

**Blocked by:** 25 (done, commit `533360f`)

**Status:** ready-for-agent

- [ ] The two-producers-one-`task_id`-one-`ground_truth` shape for `plan` is resolved one of two ways,
      chosen and justified rather than defaulted: the positional convention is declared sufficient, or
      `OutcomeRecord` gains a validated discriminator field.
- [ ] If a field is added: it is validated by construction like every other field on the record, it
      carries no content beyond a closed vocabulary, and records written before the field existed are
      handled explicitly (a stated default, or an "absent" case every consumer must read).
- [ ] `protocol.md`'s "Learning-Journal Ground-Truth Recording" section states whichever rule this
      ticket lands on, so the documented reduction and the real one cannot drift apart.
- [ ] Tests cover the chosen resolution: either that a consumer's positional reduction is correct
      under a stream ticket 25 already produces two-plan-records for, or that the new field
      round-trips through validation and rejects a value outside its vocabulary.
