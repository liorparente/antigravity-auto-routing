# 05 — Trigger granularity & budget

**Type:** Grilling (HITL)
**Blocked by:** 01, 04
**Status:** resolved — grilling session 2026-08-11

**Decision to make:** When a dialogue fires (every request? complexity ≥ threshold? sampled?), at
what effort tiers, under what per-session budget and latency ceilings.

**Why it matters:** The destination says the architect improves "on every action/request" — but a
full consultation on every action multiplies cost and wall-clock. Today's trigger is the ambiguity
predicate only. This ticket decides how literal "every action" gets to be.

**Options on the table (seeds):**

- Tiered: full dialogue at complexity ≥ Medium; cheap local-model exchange below.
- Always-on shadow: local models converse on everything; cloud dialogue only on escalation.
- Sampling plus retrospective batch review of the sampled actions.
- Session budget cap with a degradation ladder (fewer rounds → cheaper roster → skip, reported).

**Resolution (2026-08-11, via grilling):**

1. **Plan-review dialogue: complexity ≥ Medium.** Pair at Medium, panel at Complex (per ticket
   04). Trivial/Simple route straight to a worker with no dialogue.
2. **Post-execution code-review dialogue: Medium+ always, plus risk signals at any tier** —
   failing tests, an unusually large diff, or security/auth-touching files trigger a dialogue
   even on small tasks.
3. **Post-mortem dialogue: every failure, escalation (the 2-failure rule), and stalemate.**
   Rare, highest-learning-value events — the direct feeder of the learning loop.
4. **Blocking stance:** plan review and code review block the mission (their point is to catch
   before proceeding); post-mortem always runs in the background.
5. **Budget: per-session cap with an ordered degradation ladder** — fewer rounds → cheaper
   roster → skip-with-report; every degradation is flagged in telemetry (never silent). The
   numeric cap is a config value, set in the spec.
6. **Small tasks still feed the loop:** every action emits passive telemetry (ticket 06), and a
   periodic batch retrospective runs one cheap dialogue over a bundle of past small actions —
   honoring "on every action" without paying per action. Default cadence: weekly (config).
7. **Sensitive tasks: local-only cross-family pair** (e.g. Gemma vs Qwen via LM Studio) — the
   sensitive content never leaves the machine and the critique survives; fail closed with human
   escalation if LM Studio is unavailable, as today.
8. **Canary cadence: ~1 per 20 dialogues or weekly, whichever comes first.**
9. **Inherited, not re-decided:** per-round time limits (spec 0001) and effort calibration (the
   protocol's existing complexity→effort matrix) apply to dialogue workers unchanged.
