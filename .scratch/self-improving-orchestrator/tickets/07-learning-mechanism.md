# 07 — Learning mechanism: how learned state changes behavior

**Type:** Grilling (HITL)
**Blocked by:** 03, 06
**Status:** resolved — grilling session 2026-08-11

**Decision to make:** Which learning layers are in scope, and how each converts captured signals
into changed orchestrator behavior.

**Why it matters:** "למידה עצמית" without a mechanism is a slogan. Each layer has different risk,
different latency-to-effect, and a different gate (ticket 08).

**Research input (ticket 03):** learned routing needs graded outcomes on every record —
"telemetry is the training signal"; durable orchestrator-level gains come from diffable text/JSON
artifacts (skill/workflow libraries, offline-compiled configs), not weights; layer (b) carries the
strongest external evidence (MixLLM / RouteLLM / FrugalGPT cost-quality results).

**Layers on the table:**

- **(a) Continuous learn-session:** auto-distillation of session signals into institutional
  memory / `CONTEXT.md` / `ERRORS.md` — learning by context injection. Today manual.
- **(b) Evaluator-driven config:** continuous or triggered regeneration of `routing-config.json`
  from benchmarks + live outcome signals. The `/model-evaluator` skill exists; today it is
  manual/periodic.
- **(c) Prompt-template evolution:** versioned worker-mission templates, improved against
  outcomes.
- **(d) Protocol amendment proposals:** the system drafts changes to `protocol.md` as PR-style
  proposals — never direct edits.

**Resolution (2026-08-11, via grilling):**

1. **Three learning layers are in scope:**
   - *Cumulative memory* — layer (a): learn-session runs automatically, extended to mine the new
     learning journal (not just chat history), writing lessons into institutional memory /
     `CONTEXT.md` / `ERRORS.md` for injection into every future session.
   - *Live routing table* — layer (b): the learned router config is actually consumed by the
     routing decision path, regenerated from real benchmark + journal outcomes. Closes the
     "generated but ingested by nothing" gap.
   - *Brief improvement* — layer (c): worker-mission prompt templates are versioned; improvements
     land only as proposed diffs against journal outcomes, never silent overwrites.
2. **Layer (d) — protocol amendment proposals — is explicitly OUT of scope.** The user chose to
   keep `protocol.md` entirely outside the learning loop's reach; the protocol changes only by
   human hands. This materially narrows ticket 08's gate surface.
3. **Cadence: two tiers** — a light distillation at the end of every session (fresh lessons,
   cheap), plus a deep weekly run (routing-table update + brief improvement), aligned with the
   weekly batch retrospective decided in ticket 05.
4. **Executor: a dedicated background learner worker**, not the orchestrator — implementing the
   research rule "the learner proposes, an external gate disposes" (ticket 08 defines the gate),
   and consistent with the protocol's own philosophy: the orchestrator routes, workers work.
