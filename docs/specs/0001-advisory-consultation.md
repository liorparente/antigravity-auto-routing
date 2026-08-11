# Spec 0001 — AdvisoryConsultation: a real Planner–Critic deliberation loop

* Status: Implemented
* Date: 2026-08-10
* Related: ADR 0004 (Judicial Advisory Consultation), ADR 0005 Pillar 3, `protocol.md` Rule 6
* Glossary: **AdvisoryConsultation** (`CONTEXT.md`)

## Problem Statement

The orchestrator is required to resolve ambiguous or architecturally risky tasks by running a
deliberation between a Planner model and a Critic model before any code is written. Today that
deliberation does not happen. `run_advisory_consultation_debate` returns a hardcoded
`consensus_reached=True` and an f-string plan without contacting any model at all.

The failure mode is not "a missing feature" — it is a **false report**. A caller is told that a
Planner and a Critic examined the task and agreed, when neither was asked. Every downstream
decision that trusts that flag is built on a fabricated signal, and nothing in the system
distinguishes it from a genuine consensus. The stub has since been changed to raise, which stops the
lie but leaves the capability absent.

A second problem sits underneath: the word "debate" already means something else in this codebase.
`AgentCouncil` runs a *deterministic* round plan (safety → constraints → adjudication) with no model
and no network, and its output is signed and cached. Any real model-based loop that is dropped into
that module silently destroys the determinism its cache and HMAC signature depend on.

## Solution

A dedicated **AdvisoryConsultation** capability, living in its own module, that runs a genuine
multi-round exchange between a Planner model and a Critic model and returns a result that honestly
reports what happened — including "no consensus".

From the developer's perspective:

- When a task's complexity is ambiguous or confidence is low, the orchestrator runs an
  AdvisoryConsultation before planning further.
- Each round is visible: the Planner's proposal and the Critic's response are written to a transcript
  the developer can read.
- If the Critic approves, the agreed plan is written out for the developer's approval.
- If three rounds pass without approval, execution **halts** and the developer is shown a structured
  comparison of the two positions with explicit options — approve the Planner's architecture,
  approve the Critic's, or take it over themselves.
- If a worker cannot be reached, or a Critic response cannot be understood, the consultation fails
  closed. It never reports consensus it did not obtain.
- If the task is sensitive (credentials, PII, secrets), the consultation refuses to ship the task
  text to a cloud worker and escalates to the developer instead.

## User Stories

1. As an orchestrator, I want to run a real Planner–Critic exchange on an ambiguous task, so that the
   complexity classification is settled by argument instead of by guess.
2. As an orchestrator, I want the consultation to tell me honestly when consensus was **not** reached,
   so that I never proceed on a fabricated agreement.
3. As an orchestrator, I want a consultation that cannot be reached to fail closed, so that a network
   outage never degrades silently into a fake plan.
4. As a developer, I want to read the round-by-round transcript, so that I can judge whether the two
   models actually engaged with each other or just talked past one another.
5. As a developer, I want the final agreed plan written to a file for my approval, so that nothing is
   implemented before I have seen it.
6. As a developer, I want execution to stop after three unresolved rounds, so that the system does not
   loop indefinitely burning tokens on a disagreement it cannot settle.
7. As a developer, I want a stalemate to be presented as a structured comparison with explicit
   options, so that I can resolve the dispute in one decision instead of re-reading two long plans.
8. As a developer, I want to see which model played Planner and which played Critic in the output, so
   that I can tell whether the tiers were calibrated correctly for the task.
9. As a developer, I want to see how many rounds ran, so that a consultation that "agreed" in round 1
   is distinguishable from one that agreed only after two rejections.
10. As a developer, I want the consultation to refuse to send a sensitive task to a cloud worker, so
    that credentials and PII never leave the machine as a side effect of planning.
11. As a developer, I want a sensitive task to escalate to me for approval rather than silently
    skipping the consultation, so that I know planning was blocked and why.
12. As a security auditor, I want every consultation to be recorded in the routing telemetry log, so
    that I can reconstruct which decisions were model-deliberated and which were not.
13. As a test author, I want to drive the entire loop without launching a model, so that the test
    suite stays deterministic, offline, and fast.
14. As a test author, I want to script the Critic's responses per round, so that I can exercise
    consensus-on-round-1, consensus-on-round-3, and stalemate as separate cases.
15. As a test author, I want to assert on the consultation's observable output rather than its
    internals, so that the tests survive a refactor of the loop.
16. As a maintainer, I want the deterministic `AgentCouncil` round plan to stay free of models and
    network calls, so that the planning cache and the HMAC-signed manifest remain valid.
17. As a maintainer, I want "AdvisoryConsultation" and "council debate rounds" to be two clearly
    separate names, so that no future reader confuses the signed deterministic passes with the
    model-based deliberation.
18. As a maintainer, I want a Critic reply I cannot parse to count as "not approved", so that a
    malformed response can never be mistaken for agreement.
19. As a maintainer, I want the worker prompts to carry the `[WORKER-MODE: AGY-NESTED-EXEC]` token, so
    that the Planner and Critic execute their mission instead of self-blocking on the routing gate.
20. As a maintainer, I want each worker invocation to be non-interactive, so that a consultation can
    never hang waiting on a terminal that isn't there.
21. As a maintainer, I want the consultation's result excluded from the 24-hour planning cache, so
    that a non-deterministic output is never replayed as though it were reproducible.
22. As an orchestrator, I want to configure which models play Planner and Critic, so that a Medium
    task can use a cheaper pair than a Complex one without changing code.
23. As an orchestrator, I want a per-round time limit, so that one stuck worker cannot stall the whole
    mission indefinitely.
24. As a developer, I want the transcript written even when the consultation ends in stalemate or
    error, so that I can debug what the models actually said.

## Implementation Decisions

**A new module owns this.** AdvisoryConsultation lives in its own module under
`skills/worker-routing/`, not in the agent council. The council's module docstring promises "no model
or network dependency", and its planning cache plus HMAC manifest are only sound while that promise
holds. The existing stub functions (`run_advisory_consultation_debate`,
`generate_debate_stalemate_report`, `AdvisoryDebateResult`, `needs_advisory_consultation`) move to the
new module; `MAX_DEBATE_ROUNDS` is shared or duplicated deliberately, not imported in a way that
couples the two.

**One seam: an injected worker-invocation callable.** The consultation entry point takes a callable
of the shape `(model, effort, prompt) -> str`. Production passes an implementation that shells out to
the CLI worker per the protocol's templates; tests pass a fake that returns scripted text. This is
the only seam the feature introduces. Explicitly rejected: patching `subprocess` in tests (couples
assertions to exact command strings — the brittleness logged on 2026-08-07), and a "fake mode"
environment toggle (test-only branches inside production code).

**Existing seams are reused, not duplicated.** Artifact destinations derive from an injected root
directory, exactly as `AgentCouncil(root_dir)` already does, so tests point it at a temporary
directory. Atomic file writing reuses the existing helper rather than reimplementing it.

**The Critic's verdict is a machine-readable contract.** The Critic prompt requires its response to
open with a verdict line — approve or revise — followed by prose. The loop parses only that line.
Anything unparseable is treated as "not approved" and recorded as such. Consensus is *only* an
explicit approval; absence of rejection is not agreement.

**Round protocol.** Round 1: Planner produces a plan from the task description. Critic responds with a
verdict plus critique. If approved, the loop ends at round 1. Otherwise the Planner receives the
critique and revises. Maximum three rounds, matching ADR 0005 Pillar 3 and `protocol.md` Rule 6.

**Stalemate is a halt, not a fallback.** After the final round without approval, the consultation
returns a stalemate result carrying both final positions and three options — approve Planner,
approve Critic, escalate to human. It does not pick a winner, and it does not return a plan. The
existing stalemate report structure is the starting shape.

**Artifacts.** The round-by-round transcript is written to `.scratch/planning_debate.md`; an agreed
plan is written to `implementation_plan.md` for approval. Both paths come from `protocol.md` Rule 6.
The transcript is written on every outcome — consensus, stalemate, and error.

**Sensitivity gate precedes the loop.** Before any worker is invoked, the task text is evaluated for
sensitivity. A sensitive task does not proceed to a cloud Planner or Critic; the consultation halts
and reports that human approval is required (ADR 0004, Key Rule 2). This mirrors the fail-closed
behaviour required of local-model fallback.

**Worker command shape.** Prompts carry `[WORKER-MODE: AGY-NESTED-EXEC]`; invocations are
non-interactive; the Planner and Critic models and their reasoning efforts are parameters with
tier-appropriate defaults, not literals baked into the loop.

**Not cached, not signed.** The result is excluded from the planning cache and is not given a
calibration signature. Both mechanisms assert reproducibility this feature cannot offer.

**Telemetry.** Each consultation records a structured entry (task id, rounds run, outcome, models
used) through the existing routing telemetry helper.

## Testing Decisions

**What makes a good test here.** Assert only what a caller can observe: the returned result's
outcome, round count and plan; the content of the two artifact files; whether a worker was invoked at
all; and whether execution halted. Do not assert on prompt wording, internal helper names, or the
order of private calls — those are free to change.

**The single seam is the fake.** Every test injects a fake worker-invocation callable that returns
scripted responses keyed by round, and records the calls it received. No test touches the network, a
subprocess, or a real model.

**Cases to cover.**
- Consensus on round 1: one Planner call, one Critic call, plan written, `rounds_run == 1`.
- Consensus on round 3: prior rounds recorded in the transcript, plan written.
- Stalemate after the maximum rounds: no plan file, stalemate result carrying both positions and the
  three options, halt reported.
- Unparseable Critic verdict: treated as not approved; never reported as consensus.
- Worker invocation raises: fails closed; no plan file; transcript still written; the error surfaces.
- Sensitive task text: no worker is invoked at all (assert the fake recorded zero calls) and the
  result says human approval is required.
- Round cap: a consultation that never approves invokes the workers exactly the configured number of
  times, no more.
- Prompt carries the worker-mode token: assert on the recorded prompts, the one prompt property worth
  pinning, since its absence is a known live failure mode.
- Artifacts land under the injected root directory, not the real repo.

**Prior art.** `AgentCouncilDebateTests` in `skills/worker-routing/test_routing.py` is the closest
model: it drives a whole run through the public entry point inside a `TemporaryDirectory`, with the
environment cleared, and asserts on the resulting structure. `TransactionalWorkerCallTests` is the
reference for worker-call assertions. Tests are plain `unittest` and run via
`.venv/bin/python skills/worker-routing/test_routing.py` — there is no pytest in this project.

## Out of Scope

- The deterministic council round plan (`_debate_round_plan`, `_tier3_debate_manifest`) and everything
  that signs or caches it. Untouched.
- Judging the *quality* of what the models say. This spec covers the loop, its honesty, and its
  artifacts — not prompt engineering for better plans.
- Rendering the stalemate comparison as an interactive UI. The consultation returns the data; how the
  orchestrator displays it is separate.
- The ADR 0002 debt (`HMACValidator`, `root_dir` on `compute_metrics`) — see spec 0002.
- Dead-code triage of the other unreferenced helpers — see spec 0002.
- Any change to `protocol.md` or the rendered protocol copies.

## Further Notes

The stub this replaces was almost certainly not written in bad faith: the loop's hard questions —
what counts as consensus, what a stalemate produces, what happens when a worker is unreachable — have
no answer in ADR 0004 or ADR 0005, which describe the feature in a sentence each. Returning `True`
was the path of least resistance past an under-specified contract. This spec exists mainly to answer
those three questions before code is written, so the second attempt does not land in the same place.

The consultation's honesty properties are the acceptance criteria that matter most. If a reviewer has
time for only one thing, it should be confirming that no path through the loop can report consensus
without an explicit Critic approval.
