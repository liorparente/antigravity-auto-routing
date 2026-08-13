# Spec 0003 — CriticalDialogue: genuinely adversarial model dialogue on four occasions

* Status: Implemented
* Date: 2026-08-11
* Related: Spec 0001 (AdvisoryConsultation, implemented), ADR 0004, ADR 0005 Pillar 3,
  wayfinder map `self-improving-orchestrator` (tickets 02, 04, 05), research:
  `docs/research/critical-debate-prior-art.md`
* Glossary: **CriticalDialogue**, **VerdictContract** (`CONTEXT.md`); builds on
  **AdvisoryConsultation**, **AdvisoryStalemateReport**, **ConsultationTranscript**,
  **AdvisoryTelemetryRecord**, **TaskIdentity**, **WorkerModeToken**

## Problem Statement

The AdvisoryConsultation shipped by spec 0001 is honest — it never fabricates consensus — but it
is not yet *critical*, and it fires on only one occasion. Four gaps, each grounded in the prior-art
research:

1. **A bare approval passes as a review.** The verdict contract is a single line; a Critic that
   writes "approve" without reading the plan is indistinguishable from one that engaged. The
   literature shows this is the common failure mode, not the edge case: judges skew lenient, and
   converged consensus can be confidently wrong.
2. **Same-family pairs invite self-preference.** Self-preference bias is causally driven by
   self-recognition, and order effects alone can flip a majority of verdicts. Nothing today
   requires the Planner and Critic to come from different model families.
3. **Nothing watches the watcher.** A Critic that decays into rubber-stamping would go unnoticed
   indefinitely — there is no canary, no engagement measurement, no trend.
4. **One occasion, no budget.** Deliberation exists only for ambiguous complexity classification.
   Plans, diffs, and failures — the places where critique pays most — get none, and there is no
   cost control that would make wider use safe.

## Solution

**CriticalDialogue**: one dialogue infrastructure — the existing consultation loop, upgraded —
serving four occasions with a verdict contract that makes rubber-stamping structurally
impossible, cross-family rosters that suppress self-preference, probes that detect a decaying
Critic, and a budget that keeps all of it affordable.

From the developer's perspective:

- A Medium task's plan is critiqued by a cross-family Planner–Critic pair before implementation;
  a Complex task's plan faces a panel — one Planner, two Critics from two other families — and
  proceeds only when *both* Critics explicitly approve.
- A worker's diff on a Medium+ task (or any task showing risk signals) gets the same critical
  treatment before the change proceeds.
- Every failure, escalation, and stalemate triggers a background post-mortem dialogue that never
  blocks work.
- An approval is only an approval when it carries engagement: rationale before the verdict,
  quotes mechanically verified against the reviewed artifact, enumerable atomic objections.
- Seeded-flaw canaries periodically test whether the Critic still catches known-bad input.
- When cross-family independence cannot be met, the system substitutes or — as a last resort —
  runs same-family with an explicit degraded-independence flag. Never silently.
- Stalemates still halt to the human. No model adjudicator exists, not even an advisory one.

## User Stories

1. As an orchestrator, I want a plan-review dialogue for every task classified Medium or above,
   so that design flaws are caught by argument before any code exists.
2. As an orchestrator, I want Complex tasks reviewed by a panel of one Planner and two Critics
   from two other model families, so that no single family's blind spots decide an architecture.
3. As an orchestrator, I want panel consensus to require an explicit approval from both Critics,
   so that one enthusiastic reviewer cannot outvote an unresolved objection.
4. As an orchestrator, I want a post-execution dialogue over a worker's diff on Medium+ tasks,
   so that implementation errors are caught before the change proceeds.
5. As an orchestrator, I want risk signals — failing tests, an outsized diff, security-touching
   files — to trigger a code-review dialogue on any task regardless of size, so that risk rather
   than size decides scrutiny.
6. As an orchestrator, I want a post-mortem dialogue after every failure, escalation, and
   stalemate, so that the highest-learning-value events are never dropped.
7. As a developer, I want plan and code dialogues to block the mission until they finish, so that
   critique arrives before a mistake ships rather than after.
8. As a developer, I want post-mortem dialogues to run in the background, so that learning never
   delays my work.
9. As a developer, I want a Critic approval to require rationale, verified quotes, and
   enumerated objections, so that a bare "approve" can never pass as a review.
10. As a maintainer, I want an approval carrying zero engagement units to parse as "not
    approved", so that rubber-stamping is structurally impossible rather than merely
    discouraged.
11. As a maintainer, I want quotes verified mechanically against the reviewed artifact, so that a
    Critic cannot fabricate engagement it did not perform.
12. As an orchestrator, I want the Planner and Critic drawn from different model families, so
    that self-preference bias cannot inflate approvals.
13. As an orchestrator, I want an unavailable family substituted from the fallback chain — local
    families included — so that one provider outage does not strip the dialogue's independence.
14. As a security auditor, I want a same-family fallback recorded as degraded independence in
    both telemetry and transcript, so that weakened critique is always visible and never silent.
15. As an orchestrator, I want seeded-flaw canaries injected on a schedule, so that a Critic that
    approves a known-bad plan is caught within days rather than months.
16. As a security auditor, I want per-round verdict sequences, engagement counts, and canary
    results recorded in telemetry, so that dialogue quality is measurable over time.
17. As a developer, I want a per-session dialogue budget with an ordered degradation ladder, so
    that quality control cannot silently consume the session.
18. As a security auditor, I want every budget degradation flagged in telemetry, so that skipped
    scrutiny is always accounted for.
19. As a developer, I want sensitive tasks debated only by local models from different families,
    so that critique survives without content ever leaving the machine.
20. As a developer, I want a stalemate to always halt to me with no model adjudicator, so that
    unresolved disagreement is never settled by a third opinion I did not ask for.
21. As a developer, I want the panel's stalemate report to carry the Planner's position and both
    Critics' final positions, so that I can resolve a three-voice dispute in one reading.
22. As a test author, I want the entire dialogue — pair and panel, all four occasions — drivable
    through the injected worker callable, so that every path is testable offline with scripted
    responses.
23. As a maintainer, I want the deterministic CouncilDebateRound untouched, so that its planning
    cache and HMAC-signed manifest remain valid.
24. As a maintainer, I want all dialogue worker prompts to carry the WorkerModeToken and all
    invocations to be non-interactive, per the protocol's existing rules.
25. As a maintainer, I want the round cap to stay at three in every mode including panel, because
    the evidence shows gains saturate by rounds 3–4 while conformity grows per round.

## Implementation Decisions

**One infrastructure, four occasions.** CriticalDialogue extends the AdvisoryConsultation module
family — the same round loop, transcript, telemetry, and fail-closed behavior — parameterized by
an occasion (ambiguity, plan review, code review, post-mortem) that selects the mission prompt
(plan / diff / lesson) and the blocking stance. No parallel second dialogue system is built.

**Tiered topology.** Occasion plus complexity select the topology: cross-family pair by default;
for Complex tasks, a panel of one Planner and two Critics from two families other than the
Planner's. Panel consensus is an explicit approval from *both* Critics in the same round; any
other combination at the round cap is a stalemate. The panel stalemate report extends the
AdvisoryStalemateReport shape to carry both Critics' final positions; its options remain approve
Planner / approve Critic(s) / escalate to human — it still never selects a winner.

**The VerdictContract.** A Critic response must contain, in order: rationale, then engagement
units, then the verdict line. Engagement units are (a) quotes from the reviewed artifact that the
loop verifies mechanically by matching against the artifact text, and (b) numbered atomic
objections (zero objections is valid only alongside verified quotes). An approval with zero
verified engagement units, a quote that fails verification, or an unparseable response all parse
as "not approved" — extending spec 0001's rule that absence of rejection is not agreement.

**Family, not model, is the independence unit.** A family is a provider lineage (the Claude
family, the Codex/GPT family, the Gemini family, and each local model lineage counts as its own
family). Pairs must span two families; panels three. Roster resolution follows the routing
config's role blocks and the protocol's fallback chains.

**Degraded independence, never silence.** If a required family is unreachable: substitute a
family from the fallback chain (local families qualify). Only when a single family remains does
the dialogue run same-family, and then it carries an explicit degraded-independence marker on its
AdvisoryTelemetryRecord and in its ConsultationTranscript.

**Triggers.** Plan review: complexity ≥ Medium. Code review: complexity ≥ Medium always, plus
risk signals at any tier — failing tests, a diff exceeding a configured size threshold, or
changes touching security-sensitive paths (threshold and path patterns are config, not literals).
Post-mortem: every failure, escalation (the protocol's 2-failure rule), and stalemate. The
ambiguity occasion keeps its existing predicate.

**Blocking stance.** Plan-review and code-review dialogues gate progress. Post-mortems run in the
background and never block; their occurrence and outcome are still recorded.

**Seeded-flaw canaries.** On a schedule (about one per twenty dialogues or weekly, whichever
comes first — config), the Critic receives a plan from a fixture library of documented seeded
flaws instead of a real mission artifact. Approving a canary is recorded as a canary miss;
objecting to the seeded flaw is a catch. Canary runs never feed a real mission's outcome.

**Budget.** A per-session dialogue budget (numeric cap is config). On exhaustion, an ordered
degradation ladder applies: reduce rounds, then cheapen the roster, then skip the dialogue
entirely with a report. Every rung taken emits a telemetry record — degradation is never silent.

**Sensitive tasks.** The sensitivity gate still precedes everything. A sensitive task may hold a
dialogue only between local models from two local families; if the local runtime is unavailable,
the consultation fails closed and escalates to the human, exactly as today.

**Telemetry.** The AdvisoryTelemetryRecord gains: occasion, topology, per-round verdict sequence,
engagement-unit counts per round, canary flag and result, degradation flags, and the
degraded-independence marker. All existing redaction invariants hold unchanged — no task text,
nothing derived from it, TaskIdentity rules as documented in the glossary. These fields exist to
be consumed by spec 0004's LearningJournal and scoreboard.

**Unchanged and inherited.** Round cap three in all modes; per-round time limits from spec 0001;
effort calibration from the protocol's complexity matrix; prompts carry the WorkerModeToken;
invocations are non-interactive; results are neither cached nor signed; the deterministic
CouncilDebateRound and everything that signs or caches it is untouched.

## Testing Decisions

**What makes a good test here.** Assert only what a caller can observe: the result's outcome,
topology, rounds, and engagement counts; the transcript and telemetry contents; whether execution
blocked or proceeded; which workers were invoked and how many times. Never assert on prompt
wording — with two pinned exceptions inherited from spec 0001: the WorkerModeToken's presence,
and the VerdictContract's parse behavior (the contract is an observable interface).

**The seams.** Two existing, zero new. Every model exchange goes through the injected
worker-invocation callable — the fake is keyed by role and round, which is what makes panel runs
scriptable (Planner, Critic A, Critic B each get their own scripted responses). Every artifact
lands under the injected root directory.

**Cases to cover.**
- Pair consensus with verified engagement: plan written, telemetry carries engagement counts.
- Approval with zero engagement units: parses as not-approved; a revision round follows.
- A quote that fails mechanical verification: the approval does not count.
- Panel: both approve → consensus; split verdict at the cap → stalemate report carrying three
  positions; both reject → stalemate.
- Canary: approval recorded as miss, objection as catch; a canary never produces a plan artifact.
- Degraded independence: family substitution attempted first; same-family run carries the marker
  in both telemetry and transcript.
- Budget ladder: rungs apply in order; each rung emits its telemetry record; the skip rung
  reports rather than silently omitting the dialogue.
- Sensitive task: zero cloud-family invocations recorded by the fake; local-unavailable fails
  closed.
- Post-mortem: runs without blocking the mission path; its record appears.
- Round cap: never more than three rounds in any mode.

**Prior art.** The AdvisoryConsultation tests in the worker-routing test suite: whole runs driven
through the public entry point inside a temporary directory, scripted fakes, assertions on
observable structure. Plain `unittest`, no pytest.

## Out of Scope

- The LearningJournal, learner, scoreboard, and weekly report — spec 0004.
- The live improvement dashboard — queued build ticket in spec 0004's backlog.
- Any change to `protocol.md` or the rendered protocol copies.
- Rendering the stalemate comparison as an interactive UI.
- Prompt-engineering the *content* of Planner missions beyond the VerdictContract's structure.

## Further Notes

Every quantitative claim behind these decisions is cited in
`docs/research/critical-debate-prior-art.md` (~20 primary sources). The two most load-bearing:
conformity between models grows with each round — which is why "more critical" is implemented as
a harder contract, not more rounds — and self-preference is causally tied to self-recognition,
which is why independence is defined at the family level. The decision history lives in the
wayfinder map at `.scratch/self-improving-orchestrator/` (tickets 04 and 05).
