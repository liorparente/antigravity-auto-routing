# Wayfinder Map — Critical Model Dialogue & the Self-Improving Orchestrator

**Charted:** 2026-08-11
**Status:** COMPLETE (2026-08-11) — specs published: `docs/specs/0003-critical-dialogue.md` +
`docs/specs/0004-learning-loop.md` (both ready-for-agent); research merged to `main`; ticket 10
(dashboard) queued in spec 0004's backlog. This map is now an archive of the decision history.
**Tracker:** decision tickets live in `tickets/` beside this map. Convention: `.scratch/<idea>/`,
local file tracker (per ADR 0005 Pillar 4; GitHub Issues sync stays optional and unused for now).
**Research findings:** committed by research agents to `docs/research/<name>.md` on
`research/<name>` branches — the map keeps a pointer per ticket.

## Destination

Two capabilities, one closed loop:

1. **Active, critical, quality-measured model dialogue.** Inter-model conversations — today only
   the Planner–Critic AdvisoryConsultation, fired on ambiguity — become a first-class dialogue
   system: genuinely adversarial, quality-scored, and available on more occasions than ambiguity
   alone. A conversation that rubber-stamps is detected, not celebrated.

2. **An orchestrator (the architect) that improves from every action/request.** Every action emits
   learning signals; signals distill into learned state (memory, routing config, prompt
   templates); learned state demonstrably changes future routing and dialogue decisions; and the
   improvement is *measured*, not asserted. Continuous and self-driven — replacing today's manual
   `/learn-session` and the merely-periodic benchmark job.

Non-negotiable invariants carried over from the existing system: no false consensus, fail-closed
on unreachable workers, telemetry redaction (no task text, nothing derived from it), and the
deterministic signed council stays model-free. Self-modification of the protocol gets a safety
gate, not a free hand.

## Notes

- Builds directly on ADR 0005's pillars: extends Pillar 3 (debate loop) and closes the loop
  between Pillar 2 (benchmarks → `routing-config.json`) and Pillar 4 (audit) into per-action
  learning.
- Existing assets to build on, not around: `advisory_consultation.py` (spec 0001, implemented,
  backlog tickets 01–07 done), `.ralph/routing_telemetry.jsonl`, consultation transcripts,
  `routing-audit.sh`, `/learn-session`, `/model-evaluator`, `routing-config.json`,
  `knowledge/institutional-memory.md`, `install.sh` cross-harness sync.
- "בכל פעולה" (on every action) is a cost question as much as an architecture question — trigger
  granularity and budget are their own decision (ticket 05), not an implementation detail.

## Decisions so far

**2026-08-11 — Ticket 04 (dialogue topology & roles), resolved by grilling:** tiered topology —
cross-family Planner–Critic pair by default, panel of Planner + two cross-family Critics for
Complex tasks (both must approve); stalemate remains human-only (no model adjudicator); four
occasion types (ambiguity, plan review, post-execution code review, failure post-mortem); full
verdict contract (rationale-first, mechanically verified quotes, atomic objections — approval
without engagement units = not approved); day-one probes: engagement-unit counts + seeded-flaw
canaries; one dialogue infrastructure for all occasions with mission-specific prompts;
degraded-independence flag instead of silent same-family fallback; round cap stays ≤3. Full
detail: `tickets/04-dialogue-topology-and-roles.md`.

**2026-08-11 — Ticket 05 (trigger granularity & budget), resolved by grilling:** plan review at
Medium+ (pair/panel per tier); code review at Medium+ plus risk signals (failing tests, outsized
diff, security files) at any tier; post-mortem on every failure/escalation/stalemate; plan and
code dialogues block, post-mortem runs in background; per-session budget cap with an ordered,
telemetry-flagged degradation ladder (rounds → roster → skip-with-report); small tasks feed
learning via passive telemetry + a weekly batch retrospective dialogue; sensitive tasks get a
local-only cross-family pair (fail closed if LM Studio is down); canary cadence ~1/20 dialogues or
weekly; round time limits and effort calibration inherited from spec 0001 and the protocol matrix.
Full detail: `tickets/05-trigger-granularity-and-budget.md`.

**2026-08-11 — Ticket 06 (learning signals per action), resolved by grilling:** every action
records all four signal families — worker execution (latency/cost/success/retries/effort),
ground-truth outcomes (tests, review verdicts, stalemate choices), dialogue quality (rounds,
verdict sequences, engagement units, canary results), and protocol compliance (persisted audit
verdicts); stored in a new dedicated learning journal separate from the audited telemetry stream;
content-free by rule — numbers, categories, and ids only, coarse task-type tags on normal tasks,
no tags on sensitivity halts, full content stays on existing content-bearing surfaces read
locally. Full detail: `tickets/06-learning-signals-per-action.md`.

**2026-08-11 — Ticket 07 (learning mechanism), resolved by grilling:** three layers in scope —
auto learn-session mining the learning journal into institutional memory, a live routing table
actually consumed by the routing path, and versioned worker-brief templates improved via proposed
diffs; **protocol amendment proposals are explicitly out of scope** — `protocol.md` changes only
by human hands; cadence is light session-end distillation + a deep weekly run aligned with the
batch retrospective; the work is done by a dedicated background learner worker (the learner
proposes, the ticket-08 gate disposes). Full detail: `tickets/07-learning-mechanism.md`.

**2026-08-11 — Ticket 09 (proving improvement), resolved by grilling:** a four-family scoreboard
computed from the learning journal — discipline (violation rate), critique authenticity (canary
catch + engagement trends), efficiency (escalations, rework, cost per task), and a periodic
replay benchmark on a fixed task set; learned changes are accepted only via repeated benchmark
trials meeting threshold with zero regression on any scoreboard metric; trends surface as an
auto-written weekly Markdown report; a live dashboard was queued at the user's request as
follow-up build ticket 10. Full detail: `tickets/09-proving-improvement-metrics.md`.

**2026-08-11 — Ticket 08 (self-modification safety gate), resolved by grilling — MAP CLEARED:**
risk-tiered permissions for gate-passing changes — memory auto-applies (reported weekly), routing
table auto-applies after the benchmark gate (reported per change), worker briefs require human
approval, protocol untouchable (per 07); all learned state is git-versioned with one-step
rollback; auto-revert fires when the weekly run detects a scoreboard regression after adoption;
cross-harness propagation via the existing `install.sh`. Full detail:
`tickets/08-self-modification-safety-gate.md`.

## Tickets

| # | Title | Type | Blocked by | Status |
|---|-------|------|------------|--------|
| 01 | Learning-signal inventory | Research | — | landed (`7c5a9dd`) |
| 02 | Prior art: what makes model debate genuinely critical | Research | — | landed (`ae9e264`) |
| 03 | Prior art: self-improving orchestrator loops | Research | — | landed (`5207608`) |
| 04 | Dialogue topology & roles | Grilling | 02 | resolved |
| 05 | Trigger granularity & budget | Grilling | 01, 04 | resolved |
| 06 | Learning signals: what every action must emit | Grilling | 01 | resolved |
| 07 | Learning mechanism: how learned state changes behavior | Grilling | 03, 06 | resolved |
| 08 | Self-modification safety gate | Grilling | 07, 09 | resolved |
| 09 | Proving improvement: the metric set | Grilling | 06 | resolved |
| 10 | Live improvement dashboard (follow-up build) | Task | 06, 09 impl. | queued |

## Frontier

**THE MAP IS CLEAR.** All nine decision tickets are resolved (research 01–03 on `research/*`
branches; grilling decisions 04–09 recorded above). Remaining work: write the spec
(`docs/specs/`, next number in sequence) from **Decisions so far**, cutting it into
implementation tickets per the repo's convention — ticket 10 (live dashboard) rides into that
build backlog.

One decision ticket per session. Close it, record the decision under **Decisions so far**, update
ticket statuses, re-derive the frontier. When the map clears — merge the decisions into a spec
(`/to-spec`, joining `docs/specs/`).

## Not yet specified

- Whether learned state generalizes cross-project (the protocol installs into `~/.gemini/`
  globally) or stays per-repo.
- Local-model fine-tuning as a learning mechanism (weights-level learning).
- Online A/B experimentation on prompt-template variants inside live production sessions.
- GitHub Issues sync for this map.
