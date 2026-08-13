# 05 — Panel topology for Complex tasks

**What to build:** For Complex-tier occasions, the pair topology (one Planner, one Critic) becomes
a panel: one Planner and two Critics drawn from two families other than the Planner's. The injected
worker-invocation callable is keyed by role *and* round, so Planner / Critic A / Critic B each get
independently scripted responses in tests. Panel consensus is an explicit approval from *both*
Critics in the same round (each parsed through the ticket-02 VerdictContract); any other
combination at the round cap is a stalemate (handled fully in ticket 06). Round cap stays at three
in panel mode too.

**Blocked by:** 01, 02.

**Status:** done

- [x] Complex-tier plan-review and code-review occasions invoke three workers (Planner, Critic A,
      Critic B), each addressable independently by the test fake.
- [x] Consensus requires both Critics to approve (per the VerdictContract) in the same round.
- [x] One Critic approving and the other objecting is not consensus and triggers another round (or,
      at the cap, a stalemate — see ticket 06).
- [x] Non-Complex occasions still run the pair topology unchanged.
- [x] Round cap is three in panel mode, verified with a test that a panel with no consensus produces
      exactly three rounds and no more.

## Notes

Landed in commit `65fc189`. New keyword-only params `complexity`, `critic_a_model`/`effort`,
`critic_b_model`/`effort`; panel fires when `occasion in ("plan-review", "code-review") and
complexity == "complex"`. `AdvisoryDebateRound` gained an additive `critic_b_response` field.
Panel-mode stalemates still route through the existing two-voice `AdvisoryStalemateReport` with
both critics' feedback folded into one string — explicitly temporary, ticket 06's job to fix.
`/code-review` found no hard violations on either axis; first ticket to land clean, no fix round
needed. 199 tests pass. One open scope question noted, not a defect: whether Complex-tier
post-mortem should also get panel treatment — the spec's phrasing is generic ("for Complex tasks")
while this ticket scoped it to plan-review/code-review only, matching post-mortem's
lower-stakes/non-blocking/background treatment elsewhere in spec 0003.
