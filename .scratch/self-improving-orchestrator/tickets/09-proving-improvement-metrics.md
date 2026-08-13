# 09 — Proving improvement: the metric set

**Type:** Grilling (HITL)
**Blocked by:** 06
**Status:** resolved — grilling session 2026-08-11

**Decision to make:** The metric set and cadence that demonstrate the architect actually improved,
and where the trendline is surfaced.

**Why it matters:** "שיפור מתמיד" must be falsifiable. Without an agreed metric set the loop
optimizes anecdotes, and "the orchestrator got better" stays an assertion.

**Research input (ticket 03):** agent runs are high-variance — acceptance must aggregate repeated
trials (τ-bench's pass^k: even strong models drop below 25% at pass^8), with hard CI-style
fail-below-threshold gates and backtesting of candidate changes against historical consultations.

**Options on the table (seeds):**

- Routing-audit violation rate per session (should fall).
- Consultation stalemate rate and rubber-stamp rate (both falling *for the right reasons* —
  distinguishable via ticket 06's dialogue-quality signals).
- Escalation-loop frequency (2-failure retries falling).
- Rework: reopened tickets, post-review fix counts.
- Cost per completed ticket; latency per action tier.
- Periodic replay benchmark via `/model-evaluator` as the regression suite for learned changes.

**Resolution (2026-08-11, via grilling):**

1. **The scoreboard has four metric families**, all computed from the ticket-06 learning journal:
   - *Discipline* — protocol violation rate per session (persisted audit verdicts), trending down.
   - *Critique authenticity* — canary catch rate + engagement-unit trends: the critical dialogue
     is not decaying into rubber-stamping.
   - *Efficiency* — escalation rate (2-failure events), rework counts, cost per completed task.
   - *Replay benchmark* — a fixed benchmark task set run periodically through the existing
     evaluator: today's system vs last month's, on identical tasks.
2. **Acceptance gate for learned changes: repeated trials + zero regression.** A proposed change
   (routing-table update, brief version) runs multiple times on the benchmark set and is accepted
   only if it meets threshold and no scoreboard metric regresses. Single-run wins don't count.
3. **Trend surface: an auto-written weekly report** at the end of the weekly deep learning run —
   each metric, its direction, and what changed this week. Plain Markdown, one click to read.
4. **Follow-up queued at the user's request:** a live interactive dashboard becomes ticket 10 — a
   build task for after the loop exists, not a pre-spec decision. The weekly report remains the
   canonical record; the dashboard is a view over the same data.
