# 04 — Dialogue topology & roles

**Type:** Grilling (HITL)
**Blocked by:** 02
**Status:** resolved — grilling session 2026-08-11

**Decision to make:** The shape of the upgraded conversation — who participates, in what roles,
under what round structure and verdict contract — and which occasions get a conversation at all.

**Why it matters:** "פעילה, איכותית וביקורתית" is precisely a topology-and-contract question. The
baseline is the implemented AdvisoryConsultation: Planner ↔ Critic, ≤3 rounds, one verdict line,
fired only on ambiguous complexity classification.

**Research input (ticket 02):** the existing ≤3-round cap is *validated* — gains saturate by
rounds 3–4 and conformity grows per round, so "more critical" must not mean "more rounds". It
means: forced stance divergence, cross-provider Planner/Critic pairing (best-evidenced fix for
self-preference), an independence persona plus reflection step for the Critic, and a verdict
contract requiring rationale-before-verdict with mechanically verified plan quotes and enumerable
atomic objections — approval without engagement units should parse as "not approved". Rubber-stamp
probes (seeded-flaw canaries, position-swap consistency, capitulation probes, engagement-unit
counts) belong in this topology decision from day one, not only in ticket 09's metrics.

**Options on the table (seeds — sharpened by ticket 02's findings):**

- Strengthen the pair: mandatory structured objections, evidence citations, a required challenge
  round before any approval counts.
- Pair + Adjudicator: a third model rules on unresolved disagreement (vs today's human-only
  stalemate report).
- Heterogeneous panel: 3+ models across providers, role rotation; costlier, less self-preference.
- Occasion expansion: plan review, code review, routing decisions themselves, post-mortems.
- Roster policy: which models play which roles; whether Planner and Critic must be cross-provider.

**Resolution (2026-08-11, via grilling):**

1. **Tiered topology.** Default: a strengthened cross-family Planner–Critic pair.
   Complex/architectural tasks: a panel — one Planner plus two independent Critics from two
   *other* model families; panel consensus requires an explicit approval from both Critics.
2. **Stalemate stays human-only.** No model adjudicator, not even advisory. The existing
   stalemate report (two positions, three options) is unchanged.
3. **Occasion menu (types only; frequency/budget = ticket 05):** classification ambiguity
   (existing), plan review, post-execution code review (dialogue over the diff), and post-mortem
   on failure/regression — the direct feeder of the learning loop.
4. **Full verdict contract.** Rationale before verdict; quotes from the reviewed artifact,
   mechanically verified; enumerable atomic objections. An approval carrying zero engagement
   units parses as "not approved".
5. **Day-one rubber-stamp probes:** always-on engagement-unit counting + periodic seeded-flaw
   canaries. Order-swap and capitulation probes: deferred — revisit in ticket 09.
6. **One dialogue infrastructure for all four occasions** — the same
   rounds/contract/transcript/telemetry machinery, mission-specific prompts (plan / diff /
   lesson).
7. **Degraded independence, never silence.** If a cross-family partner is unreachable,
   substitute a family from the fallback chain (local LM Studio counts as a family); only if a
   single family remains, run same-family with an explicit `degraded-independence` flag in
   telemetry and transcript.
8. **Round cap stays ≤3** in all modes including panel — evidence: gains saturate by rounds 3–4
   and cross-model conformity grows per round.
