# 32 — `run_weekly_deep`'s `run_id` never reaches the acceptance gate's trial records

**What to build:** Forward `run_weekly_deep`'s `run_id` parameter through
`risk_tiered_application.apply_routing_table_update` into `acceptance_gate.evaluate_proposal`'s own
`run_id` keyword argument, so the `ReplayBenchmarkRecord`s a routing-table proposal's trials write
carry the learner run that produced them.

**What it does now.** `run_weekly_deep(..., run_id=...)` uses `run_id` for exactly one thing: seeding
`_change_id`/`proposal_id` derivation (see the function's own docstring — "used only to seed
`change_id`/`proposal_id` derivation"). It calls
`risk_tiered_application.apply_routing_table_update(routing_update, root_dir=root_dir, now=now,
runner=runner, change_id=..., trials=trials, score_threshold=score_threshold)` with no `run_id`
argument at all. `apply_routing_table_update` itself has no `run_id` parameter to receive one, and its
own call into `acceptance_gate.evaluate_proposal` builds `gate_kwargs` from `task_set`, `root_dir`,
`now`, `trials`, `score_threshold`, and optionally `report_journal_error` — never `run_id`. So every
`ReplayBenchmarkRecord` a weekly run's trials write lands with `run_id=None`, even when the caller
supplied one to `run_weekly_deep`.

**Why this matters.** `learning_journal.ReplayBenchmarkRecord.run_id` exists so a trial batch can be
traced back to the learner run that requested it — the same reason `WorkerExecutionRecord`,
`OutcomeRecord`, and `DialogueQualityRecord` all carry it. Ticket 26's replay-benchmark trend and any
future consumer asking "which weekly run's proposal produced this batch of trials" currently has no
way to answer that question from the journal alone; the only place the association exists is in the
caller's own memory at the moment of the call, and it evaporates once `run_weekly_deep` returns.

**Why this is a Category 2 item, not a quick fix.** Wiring it through touches three call sites'
signatures across two modules this ticket does not own: `apply_routing_table_update`'s own signature
in `risk_tiered_application.py` (ticket 20), and `evaluate_proposal`'s existing `run_id` parameter in
`acceptance_gate.py` (ticket 18) already validates it up front via a throwaway probe
`ReplayBenchmarkRecord` — so a `run_id` learner_worker starts forwarding must satisfy that same
validation, or a caller-supplied `run_id` that was fine for `learning_journal.append_journal_record`'s
own five *other* record types could still fail this one's construction. That is a real (if narrow)
compatibility question belonging to whoever owns tickets 18/20's contracts, not a one-line change
inside ticket 22's own file.

**Origin:** Ticket 22 convergence loop (`3cecc61`) review pass, Category 2 — a real gap, but the fix
crosses into tickets 18/20's owned signatures rather than staying inside `learner_worker.py`.

**Blocked by:** none technically, but should land with awareness of tickets 18 and 20's existing
contracts

**Status:** complete

- [x] `risk_tiered_application.apply_routing_table_update` accepts an optional `run_id: str | None`
      keyword argument and forwards it into `acceptance_gate.evaluate_proposal`'s own `run_id`.
- [x] `learner_worker.run_weekly_deep` passes its own `run_id` argument through to
      `apply_routing_table_update`.
- [x] A test asserts that `ReplayBenchmarkRecord`s written during a `run_weekly_deep` call carry the
      `run_id` the caller supplied, and that omitting `run_id` still works exactly as it does today
      (`run_id=None` throughout).
- [x] `acceptance_gate.evaluate_proposal`'s existing `run_id` validation (the throwaway probe record)
      is exercised by the new forwarding path, not bypassed by it.
