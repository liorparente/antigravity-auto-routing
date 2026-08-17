# Spec 0004 — The Learning Loop: journal, learner, scoreboard, and gates

* Status: ready-for-agent
* Date: 2026-08-11
* Related: Spec 0003 (CriticalDialogue), ADR 0005 Pillars 2 & 4, wayfinder map
  `self-improving-orchestrator` (tickets 01, 03, 06–09), research:
  `docs/research/learning-signal-inventory.md`, `docs/research/self-improvement-prior-art.md`
* Glossary: **LearningJournal**, **LearnerWorker** (`CONTEXT.md`); builds on
  **AdvisoryTelemetryRecord**, **TaskIdentity**

## Problem Statement

The system records *decisions* but not *results*. The signal inventory found the loop severed at
both ends:

1. **Execution is invisible.** No per-invocation latency, cost, success, or retry count is
   recorded anywhere. The system cannot answer "which model actually performs?"
2. **Ground truth is never joined.** Whether tests passed, whether a review approved, which
   stalemate option the human chose — none of it is recorded against the decision that produced
   it. "Did we choose well?" is unanswerable.
3. **Audit verdicts evaporate.** The post-session audit computes violations and codes, prints
   them, and loses them. Discipline has no trendline.
4. **The two existing learning mechanisms are both manual and both incomplete.** learn-session
   mines chat history — not telemetry — behind a manual trigger; the evaluator generates an
   optimized router config that nothing consumes.

The result: an orchestrator that cannot improve from experience, because experience is not
recorded, and a routing policy frozen at whatever was last hand-written.

## Solution

A closed loop in four parts:

- **LearningJournal** — every action appends content-free records to a dedicated stream: what ran,
  what it cost, what came of it, how the dialogue behaved, and whether the protocol was honored.
- **LearnerWorker** — a background worker (never the orchestrator) that distills the journal:
  a light pass at session end writing fresh lessons to institutional memory, and a deep weekly
  run proposing routing-table updates and brief improvements.
- **Scoreboard and weekly report** — four metric families computed from the journal, written as
  a short Markdown trend report every week: discipline, critique authenticity, efficiency, and a
  replay benchmark.
- **Gates** — a proposed change is accepted only after repeated benchmark trials meet threshold,
  persist completely, and concurrent non-benchmark scoreboard metrics do not regress; application is risk-tiered (memory auto, routing table auto
  after the gate, briefs human-approved, protocol untouchable); every adopted change is
  git-versioned; a post-adoption regression auto-reverts with a report.

## User Stories

1. As an orchestrator, I want every worker invocation recorded with latency, cost, success,
   retries, effort, and model family, so that routing can be judged against actual performance.
2. As an orchestrator, I want ground-truth outcomes — test results, review verdicts, plan
   acceptance — joined to earlier decisions by TaskIdentity, so that "what we decided" can be
   checked against "were we right".
3. As a developer, I want my stalemate resolutions recorded, so that the system learns from how I
   settle disagreements.
4. As a security auditor, I want post-session audit verdicts persisted rather than printed and
   lost, so that protocol discipline has a trendline.
5. As a security auditor, I want the journal content-free — numbers, categories, ids — so that a
   leaked journal reveals nothing about any task's content.
6. As a security auditor, I want sensitivity-halted tasks to carry no tags at all in the journal,
   so that the confirmation-oracle rule survives the learning loop.
7. As a maintainer, I want the journal separate from the audited telemetry stream, so that the
   audit record contract stays frozen while the learning schema is free to evolve.
8. As an orchestrator, I want a light session-end distillation that writes fresh lessons to
   institutional memory automatically, so that learning is continuous rather than on-demand.
9. As an orchestrator, I want a deep weekly run that proposes routing-table updates and brief
   improvements from journal evidence, so that the policy tracks reality.
10. As a developer, I want small tasks covered by a weekly batch retrospective synthesis, so that
    every action feeds learning without paying dialogue cost per action.
11. As a maintainer, I want the learner to be a background worker and never the orchestrator, so
    that the proposer and the approver are always separate parties.
12. As a developer, I want a proposed change accepted only after repeated benchmark trials meet
    threshold, persist completely, and concurrent non-benchmark metrics do not regress, so that
    single-run luck never reshapes my system.
13. As a developer, I want accepted memory changes to auto-apply and appear in the weekly report,
    so that low-risk learning flows without me as a bottleneck.
14. As a developer, I want routing-table changes to auto-apply only after the acceptance gate,
    each with its own report line, so that policy changes are frictionless but never invisible.
15. As a developer, I want brief changes to wait for my explicit approval, because they shape how
    every worker understands every task.
16. As a developer, I want the protocol permanently outside the loop's reach, so that the
    constitution changes only by human hands.
17. As a developer, I want every adopted change stored as a git-tracked version with one-step
    rollback, so that no learned mistake is ever expensive to undo.
18. As a developer, I want an automatic revert plus a report when a scoreboard metric regresses
    after adoption, so that the system's mistakes self-correct and leave a trail.
19. As a developer, I want a weekly Markdown report showing each metric, its direction, and what
    changed this week, so that improvement is a fact I read rather than a claim I hear.
20. As a project owner, I want a periodic replay benchmark running a fixed task set through the
    evaluator, so that today's system is comparable to last month's on identical work.
21. As a test author, I want the journal, report, and versioned state written under an injected
    root, and benchmark scoring behind an injected runner, so that the entire loop is testable
    offline and deterministically.
22. As a maintainer, I want adopted learned state propagated across harnesses by the existing
    install mechanism, so that all three environments learn as one.

## Implementation Decisions

**The LearningJournal is a new, separate stream.** One append-only JSONL journal beside the
routing telemetry, with a record kind per signal family: worker-execution, outcome,
dialogue-quality, compliance, and replay-benchmark. The audited telemetry stream is not extended —
its record contract (including the `kind`-asymmetry that distinguishes advisory from council
records) stays frozen. Records correlate across streams via TaskIdentity — except the
replay-benchmark family, which grades the evaluator's own fixed task set rather than a development
task and carries no TaskIdentity at all.

**Record contents by family.**
- *Worker-execution*: emitted on every worker invocation — duration, cost estimate, success or
  failure, retry count, effort level, model and family. This instruments the production invoker
  path, today's biggest blind spot.
- *Outcome*: emitted when each truth becomes known — test pass/fail, review verdict, plan
  accepted or rejected, and the human's stalemate choice — each carrying the TaskIdentity of the
  decision it grades. "Emitted" names an intent, not a mechanism: the entry points are backlog
  ticket 14 and the callers that invoke them are backlog ticket 25, filed after a 2026-08-13 review
  found this paragraph had assumed writers no ticket ever assigned.
- *Dialogue-quality*: written by spec 0003's machinery — occasion, topology, rounds, per-round
  verdict sequence, engagement counts, canary results, degradation and independence flags.
- *Compliance*: the post-session audit's violations, issue codes, and metrics, persisted per
  session instead of ending at stdout.
- *Replay-benchmark*: one record per acceptance-gate trial — the task-set identifier, whether the
  trial succeeded, and the score an injected benchmark runner returned. A failed trial is journaled
  as failed rather than omitted, so the trend it feeds has no silent gaps; a fifth family exists for
  it because the outcome family's closed verdict vocabulary has nowhere for a number to sit
  (backlog ticket 26).

**Redaction is structural.** The journal carries numbers, categories, and identifiers only. A
coarse task-type tag (for example "bugfix", "refactor") is permitted on normal tasks;
sensitivity-halted tasks carry no tag of any kind, consistent with the existing rule that nothing
derived from halted task text may surface. Where learning genuinely needs content, the learner
reads the existing content-bearing surfaces (transcripts, council decision records) locally,
under their existing rules — the journal itself never becomes one.

**The LearnerWorker.** A background worker invoked through the standard worker mechanism — never
the orchestrator itself. Two cadences: a light session-end pass that distills the session's
journal entries into institutional-memory lessons (extending the learn-session flow to mine the
journal, not just chat), and a deep weekly run that computes the scoreboard, runs the batch
retrospective synthesis over the week's small tasks (ADR 0009), and produces proposals:
routing-table updates and brief diffs. Cadence is driven by the existing scheduler; the modules
themselves take the current time as input and own no clock.

**The acceptance gate.** A proposal is evaluated by running the fixed benchmark task set multiple
times (count is config) through an injected benchmark-runner and comparing scoreboard metrics.
Accept only if every score meets threshold, every trial persists to the journal, and no concurrent
non-benchmark metric (discipline, critique authenticity, or efficiency) regresses. A probe-only
`mean_benchmark_score` regression remains visible but does not reject the candidate: probes are not
adopted system state. A single winning run is explicitly insufficient. The learner never grades its
own proposal by self-assessment — scores come only from the external runner.

**Risk-tiered application.** Memory lessons: auto-apply, listed in the weekly report. Unlike the
other two tiers, this one accumulates rather than replaces — each call reads the current memory
document, merges its new entries in (deduped, oldest-first-evicted past a fixed bound), and adopts
the merged result atomically against concurrent writers (ADR 0010). Routing-table updates:
auto-apply after passing the gate, one report line each. Brief diffs: held as pending proposals
until the human approves. The protocol: unreachable by construction — the learner has no code path
that writes it.

**Versioning and auto-revert.** Every adopted change is a git-tracked version of the learned
state; manual rollback is always one step. The weekly run compares the scoreboard against the
pre-adoption baseline; a regression attributable to an adopted change — including
`mean_benchmark_score` — triggers an automatic revert and a report entry. This post-adoption check
is the anti-ratchet boundary; it evaluates live system state rather than the gate's un-adopted
probe batch (ADR 0008). The system is free to learn because its mistakes self-correct and leave a
trail.

**The scoreboard.** Four metric families, all computed from the journal: discipline (protocol
violation rate per session), critique authenticity (canary catch rate and engagement-count
trends), efficiency (escalation rate, rework counts, cost per completed task), and the replay
benchmark trend. The weekly report renders each metric, its direction, adopted and reverted
changes, and any budget degradations — plain Markdown, one click to read.

**Propagation.** Adopted learned state syncs across harnesses through the existing install
mechanism, exactly like all shared configuration today.

## Testing Decisions

**What makes a good test here.** Assert on journal records, report contents, versioned-state
transitions, and which proposals were applied, held, or reverted — never on the learner's
internal reasoning or the wording of its prompts.

**The seams.** Three: the injected worker-invocation callable (drives the learner and the batch
retrospective offline), the injected root directory (journal, report, and versions never touch
the real repository in tests), and the one new seam — an injected benchmark-runner callable that
returns scripted scores in tests and drives the real evaluator in production.

**Cases to cover.**
- Each record family lands with its schema; correlation by TaskIdentity holds across streams.
- Content-freedom is enforced: no task text anywhere; a normal task carries at most a coarse tag;
  a sensitivity-halted task carries none.
- The gate accepts only threshold-plus-zero-regression across repeated scripted trials; a single
  good run is rejected; a concurrent non-benchmark metric regression rejects (ADR 0008).
- Tier routing: a memory lesson auto-applies; a routing update applies only post-gate; a brief
  diff is held pending and applies only on recorded human approval.
- Memory-lesson accumulation (ADR 0010): a second call's lessons merge with, rather than replace,
  a first call's; exact-duplicate lessons dedupe as a no-op; the document-wide bound evicts the
  oldest entries first once exceeded; two concurrent calls against the same root both survive via
  the compare-and-swap retry.
- Auto-revert: a scripted post-adoption regression reverts the change and writes the report
  entry; the reverted state matches the prior version exactly.
- The weekly report contains every metric family, every adopted and reverted change, and every
  degradation from the week's journal.
- The learner runs as a worker invocation (the fake records it); the orchestrator path itself
  writes no learned state.

**Prior art.** The consultation tests (whole-run through public entry points, temporary
directories, scripted fakes, plain `unittest`) and the transactional worker-call tests for
invocation assertions.

## Out of Scope

- Dialogue mechanics — spec 0003.
- **Queued build ticket (from map ticket 10): the live improvement dashboard** — an interactive
  view over the journal and scoreboard, built only after the journal and weekly report exist.
  The weekly report remains the canonical record; the dashboard is a view, never a second source
  of truth.
- Protocol amendment proposals of any kind (excluded by explicit decision — map ticket 07).
- Cross-project generalization of learned state, local-model fine-tuning, and online A/B testing
  in live sessions (all remain in the map's "Not yet specified").

## Further Notes

The evidence base is `docs/research/self-improvement-prior-art.md`: external signals beat
self-assessment (intrinsic self-correction measurably degrades performance), learned routing has
the strongest cost/quality evidence, durable gains come from diffable artifacts rather than
weights, and every measured self-modifying system uses the same guardrail set this spec adopts —
the learner proposes, an external gate disposes. The signal inventory that shaped the journal
schema is `docs/research/learning-signal-inventory.md`; the decision history is the wayfinder map
at `.scratch/self-improving-orchestrator/` (tickets 06–09).
