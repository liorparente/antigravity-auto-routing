# 02 — Prior art: what makes model-to-model debate genuinely critical

**Type:** Research (AFK)
**Blocked by:** none
**Status:** landed — verified at commit `ae9e264`
**Branch / findings:** `research/critical-debate-prior-art` → `docs/research/critical-debate-prior-art.md`

**Question:** What do primary sources establish about making inter-model critique genuinely
adversarial and quality-improving — and about detecting rubber-stamping?

**Why it matters:** Ticket 04 chooses a dialogue topology; without the evidence base the choice is
taste. The repo's core fear — false consensus — has an actual research literature.

**Must cover:**

- Multi-agent debate: measured gains and limits, including at least one critical evaluation of
  when debate does *not* help.
- Failure modes: sycophancy/conformity between models, degeneration-of-thought, echo chambers.
- Judge/critic biases: self-preference bias, position bias; heterogeneous-model rosters as
  mitigation.
- Topologies compared: pair, pair + adjudicator, panel; what round counts add marginal value.
- Verdict-contract designs that force engagement (structured objections, evidence requirements).
- Rubber-stamp detection: measurable signals that a critic approved without engaging.

**Resolution:** Findings at `docs/research/critical-debate-prior-art.md` (branch
`research/critical-debate-prior-art`, commit `ae9e264`; ~20 primary sources, all cited). Essence:
(1) debate's gains are real but *conditional* — multi-agent debate does not reliably beat
CoT/self-consistency; the value concentrates in forced disagreement and cross-family
heterogeneity, while intrinsic self-correction actively degrades. (2) Rounds are a liability past
~3: performance saturates by rounds 3–4 and conformity measurably grows per round (33.9%→44.4%
over rounds 1→5). (3) Critic biases are quantified and mechanistic: order-swap alone flipped 66/80
GPT-4 verdicts; self-preference is causally driven by self-recognition; judges skew lenient —
different providers for Planner vs Critic is the best-evidenced structural fix. (4) Engagement can
be forced by contract: rationale-before-verdict, mechanically verified quotes from the artifact,
atomic critique units scoreable for precision/recall — a bare "approve" becomes quantifiably
distinguishable from a real review. (5) Rubber-stamping is detectable with five telemetry probes:
capitulation under invalid challenge, position-swap inconsistency, seeded-flaw canaries, zero
engagement units, conformity-rate tracking — and converged consensus can still be confidently
wrong.
