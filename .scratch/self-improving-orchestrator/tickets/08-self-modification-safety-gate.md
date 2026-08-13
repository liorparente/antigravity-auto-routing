# 08 — Self-modification safety gate

**Type:** Grilling (HITL)
**Blocked by:** 07, 09
**Status:** resolved — grilling session 2026-08-11

**Decision to make:** What auto-applies vs what waits for human approval; how learned state is
versioned, rolled back, and synced across harnesses.

**Why it matters:** The system's own posture — fail-closed, signed manifests, honest reporting —
forbids an ungated self-editing orchestrator. `protocol.md` is executable authority: a bad learned
edit there corrupts every future session across all three harnesses via `install.sh` sync.

**Research input (ticket 03):** every *measured* self-modifying system (Darwin Gödel Machine,
SICA) uses the same guardrail set — sandboxed learner, benchmark-gated acceptance, version archive
with one-step rollback, human gate on promotion: "the learner proposes, an external gate
disposes." No self-grading — intrinsic self-assessment degrades performance; a proposed change is
scored only by the external benchmark. Learned changes land as *proposed diffs* to config, never
in-place mutations.

**Options on the table (seeds):**

- Risk tiers: memory auto-applies; `routing-config.json` auto-applies with a report; prompt
  templates gated; protocol changes PR-only.
- Canary: a learned change rides N sessions in shadow before adoption.
- Versioned learned state with git-native rollback; signature over adopted versions (the
  calibration-key mechanism already exists).
- Adoption thresholds keyed to ticket 09's metrics — a "learned" change must not regress them.

**Resolution (2026-08-11, via grilling):**

1. **Risk-tiered permissions** for changes that passed the ticket-09 acceptance gate (repeated
   trials + zero regression):
   - *Memory (lessons/insights)* — auto-applies; surfaced in the weekly report. Low risk:
     context-injection only, trivially reversible.
   - *Routing table* — auto-applies after the gate; each change gets a line in the weekly
     report. Medium risk, but benchmark-proven before adoption.
   - *Worker briefs (mission templates)* — human approval required. Highest leverage: shapes how
     every worker understands every task.
   - *Protocol* — not applicable: entirely outside the learning loop's reach (ticket 07).
2. **Versioned learned state, git-native.** Every adopted change is a tracked version; one-step
   manual rollback is always available.
3. **Auto-revert on regression.** The weekly run watches the scoreboard; if a metric regresses
   after an adopted change, the change is reverted automatically and reported. The system is free
   to learn because its mistakes self-correct and leave a reporting trail.
4. **Cross-harness propagation inherited:** adopted state syncs via the existing `install.sh`
   mechanism, same as all shared configuration today.
