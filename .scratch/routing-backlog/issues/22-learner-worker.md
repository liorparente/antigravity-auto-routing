# 22 — The LearnerWorker: a light pass and a deep run

**What to build:** The component that turns records into changed behavior — and it is a **worker**,
invoked through the standard mechanism, never the orchestrator. The proposer and the approver must
always be separate parties; an orchestrator that distills its own session is both.

Two cadences:

- **Session end, light.** Distills the session's journal entries into institutional-memory lessons,
  extending the learn-session flow to mine the journal rather than only chat history.
- **Weekly, deep.** Computes the scoreboard, runs a batch retrospective dialogue over the week's
  small tasks — so every action feeds learning without paying dialogue cost per action — and produces
  proposals: routing-table updates and brief diffs.

The modules own no clock: cadence comes from the existing scheduler, and the current time is an
input.

**This ticket owns the acceptance gate's configuration.** Ticket 18 asks for a benchmark run "several
times — the count is configuration", and `acceptance_gate.evaluate_proposal` provides that as
`trials` / `score_threshold` keyword arguments over `DEFAULT_TRIAL_COUNT` / `DEFAULT_SCORE_THRESHOLD`.
Nothing reads `routing-config.json` for them, deliberately: the gate already has a caller —
`risk_tiered_application.apply_routing_table_update` — but nothing supplied it config-sourced values
before this ticket, and a config read inside it would be a second source for a value its caller
already passes. `run_weekly_deep` is the first caller of `apply_routing_table_update` to supply them
from config, so reading the values here — the way
`advisory_consultation._load_dialogue_budget_config` reads `dialogue_budget.session_dialogue_cap` —
is what finishes ticket 18's "configuration" requirement rather than leaving it at a Python default.

**Blocked by:** 17, 18, 20

**Status:** done — commit 3cecc61

- [x] The light session-end pass runs as a worker invocation and writes institutional-memory lessons.
- [x] The deep weekly run produces routing-table proposals and brief diffs from journal evidence.
- [x] The weekly run includes a batch retrospective over the week's small tasks — implemented as a
      single one-shot worker prompt, not a full multi-round `post-mortem` dialogue occasion; see
      issue 31 for the open decision on whether it should become one.
- [x] Every learner run is observable as a worker invocation through the injected callable; the
      orchestrator path itself writes no learned state.
- [x] Both cadences take the current time as an input rather than reading the clock.
- [x] Proposals reach the tiering from ticket 20 and are never applied by the learner directly.
- [x] The acceptance gate's trial count and score threshold are read from `routing-config.json` and
      passed in, not left at `acceptance_gate.py`'s module defaults.
- [x] Tests drive both cadences offline through the injected worker callable.
