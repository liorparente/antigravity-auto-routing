# 06 — Learning signals: what every action must emit

**Type:** Grilling (HITL)
**Blocked by:** 01
**Status:** resolved — grilling session 2026-08-11

**Decision to make:** The signal set each action/request emits for the learning loop, where it is
stored, and the redaction boundary it must respect.

**Why it matters:** Learning wants rich data; the telemetry rules deliberately forbid task text —
and anything *derived* from it (the confirmation-oracle lesson). The tension must be resolved by
decision, not by accident.

**Research input (ticket 01):** the gap table closing `docs/research/learning-signal-inventory.md`
is this ticket's agenda. Decisive gaps: per-invocation execution signals (latency, tokens, cost,
success, retries — `production_invoker.py` records nothing), effort levels and per-round verdict
sequences, ground-truth outcomes (tests, review verdicts, plan acceptance), human stalemate
choices, and persistence of `routing_check.py` audit verdicts. Constraint discovered:
`.ralph/decisions/*.json` carries raw task text while telemetry is content-free — the learning
pipeline must keep content-bearing and redacted surfaces split.

**Options on the table (seeds):**

- Outcome signals: test results, review findings, user corrections, rework counts.
- Routing signals: chosen worker/effort vs escalations and failures (ADR 0005 escalation
  protocol already defines the 2-failure trigger).
- Dialogue-quality signals: rounds run, verdict distribution, disagreement rate, rubber-stamp
  flags.
- Cost and latency per action; per-model reliability over time.
- Storage: extend `.ralph/routing_telemetry.jsonl` (shared stream, `kind` discipline) vs a new
  dedicated stream.

**Resolution (2026-08-11, via grilling):**

1. **All four signal families are recorded per action:**
   - *Worker execution* — per-invocation latency, cost, success/failure, retry count, effort
     used (closes the `production_invoker.py` blind spot).
   - *Ground-truth outcomes* — test results, review verdicts, plan acceptance, and the human's
     stalemate choice (joins "what we decided" to "were we right").
   - *Dialogue quality* — rounds run, per-round verdict sequence, engagement-unit counts, canary
     results (the rubber-stamp trendline).
   - *Protocol compliance* — `routing_check.py` violations, DEC-*/LOG-* codes and metrics,
     persisted instead of evaporating to stdout.
2. **Storage: a new dedicated learning journal** (e.g. `.ralph/learning_signals.jsonl`), separate
   from the audited `routing_telemetry.jsonl` — the audit stream's record contract stays frozen;
   the learning journal is free to evolve.
3. **Redaction: numbers, categories, and ids only.** Coarse task-type tags (e.g. "bugfix",
   "refactor") are allowed on normal tasks; sensitivity-halted tasks get no tag at all; task text
   and anything derived from it never enter the journal. Full content stays where it already
   lives (transcripts, `.ralph/decisions/`), read locally by the learning pipeline under the
   existing content-bearing-surface rules.
