# 01 — Learning-signal inventory

**Type:** Research (AFK)
**Blocked by:** none
**Status:** landed — verified at commit `7c5a9dd`
**Branch / findings:** `research/learning-signal-inventory` → `docs/research/learning-signal-inventory.md`

**Question:** What does the system already record per action — where, in what schema, under which
redaction rules — and which signals a continuous learning loop would still need?

**Why it matters:** Tickets 05–09 all assume we know what an "action" already emits. Guessing here
means designing a learning loop against telemetry that doesn't exist.

**Must cover:**

- `.ralph/routing_telemetry.jsonl` — both record families (the advisory `kind`-field asymmetry),
  full field lists, read from the code that writes them.
- ConsultationTranscript — contents per outcome, paths, the `sensitivity_halt` redaction rule and
  the confirmation-oracle lesson.
- `routing-audit.sh` — which violations it can detect post-session.
- `/learn-session` skill — what it extracts, where it writes (`knowledge/institutional-memory.md`,
  `CONTEXT.md`, `ERRORS.md`, `AGENTS.md`), how `install.sh` propagates.
- `/model-evaluator` skill — benchmark dimensions, and the schema of
  `skills/worker-routing/routing-config.json`.
- `.ralph/decisions/` council artifacts.
- Closing gap table: learning signal → exists today? → where → redaction constraint.

**Resolution:** Findings at `docs/research/learning-signal-inventory.md` (branch
`research/learning-signal-inventory`, commit `7c5a9dd`), closing with a 17-row gap table. Essence:
the system records *decisions* (route, outcome, rounds) under strict redaction, but almost nothing
about *execution* (no per-invocation latency/tokens/cost/success — `production_invoker.py` logs
nothing; no effort levels; no per-round verdict sequences) and nothing about *outcomes* (telemetry
never joined to tests/review/plan acceptance; human stalemate choices unrecorded). Audit verdicts
(`routing_check.py` violations, DEC-codes, metrics) go to stdout only — never persisted. The two
existing learning mechanisms don't close the loop: `/learn-session` mines chat history (not
telemetry) behind a manual approval gate, and model-evaluator's `active_router_config.json` is
consumed by nothing — the live `routing-config.json` is static audit-matching data, not a learned
policy. Redaction split to respect: telemetry/halt paths must stay content-free (digests are
confirmation oracles), while `.ralph/decisions/*.json` carries raw task text.
