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

**Blocked by:** 17, 18, 20

**Status:** ready-for-agent

- [ ] The light session-end pass runs as a worker invocation and writes institutional-memory lessons.
- [ ] The deep weekly run produces routing-table proposals and brief diffs from journal evidence.
- [ ] The weekly run includes a batch retrospective over the week's small tasks.
- [ ] Every learner run is observable as a worker invocation through the injected callable; the
      orchestrator path itself writes no learned state.
- [ ] Both cadences take the current time as an input rather than reading the clock.
- [ ] Proposals reach the tiering from ticket 20 and are never applied by the learner directly.
- [ ] Tests drive both cadences offline through the injected worker callable.
