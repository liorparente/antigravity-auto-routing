# 03 — Revision rounds: the critique flows back to the Planner

**What to build:** A rejection is not the end of the consultation. When the Critic withholds approval,
the Planner is asked again — this time holding the Critic's objection — and the exchange continues up
to the round cap. This is what separates a deliberation from a formality: the test that matters is
that the Planner actually receives the critique, not merely that a counter increments.

**Blocked by:** 02

**Status:** done

- [x] A rejection causes a second Planner invocation, and the Critic's objection is present in what
      that invocation receives.
- [x] A consultation approved on the third exchange reports three rounds and writes the agreed plan.
- [x] Each round's Planner proposal and Critic response are retained in the result, in order.
- [x] A Critic that never approves produces exactly the configured number of exchanges and no more.
- [x] The round cap is a parameter with a default of three, matching the architectural decision, not a
      literal buried in the loop.

## Notes

**Status corrected 2026-08-11.** This file said `ready-for-agent` long after the work had landed.
The implementation is commit `532225f`, which sat stranded on branch
`worktree-ticket-03-revision-rounds` and reached `main` through merge `f874aac`. That stranding is
why the status never caught up — the ticket was closed against a branch nobody had merged yet.

Evidence for each criterion, all in `skills/worker-routing/`:

| Criterion | Where it is verified |
|---|---|
| Critique flows back | `test_rejection_sends_the_critics_objection_back_to_the_planner` |
| Approval on round 3 | `test_consensus_on_third_exchange_reports_three_rounds_and_writes_plan` |
| Rounds retained in order | `test_result_retains_each_rounds_exchange_in_order` |
| Exact exchange count | `test_critic_that_never_approves_produces_exactly_two_times_max_rounds_calls` |
| Round cap is a parameter | `run_advisory_consultation_debate(..., max_rounds: int = MAX_DEBATE_ROUNDS)` |
