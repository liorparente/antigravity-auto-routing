# Domain Glossary

### SecurityContext
An immutable context constructed at system startup that holds the resolved calibration secret and root directory for HMAC verification. It isolates secret loading from metric calculation and per-step verification.

### StepAnalysis
A pure data record returned by `_analyze_step` containing the isolated policy evaluation metrics, structural binding issues, and code writes for a single step.

### ModelRoutingPolicy
A decision engine policy combining task complexity and sensitivity classification to route tasks between local and cloud models.

### AdvisoryConsultation
A structured deliberation loop between Planner and Critic models triggered when task complexity classification is ambiguous. Distinct from a [[CouncilDebateRound]]: an AdvisoryConsultation consults real models and is therefore neither reproducible nor signable.

### CouncilDebateRound
One pass of the council's deterministic decision plan — safety, then constraints, then adjudication when the first two disagree. No model and no network are involved, which is what allows a decision to be cached and signed. Not to be confused with an [[AdvisoryConsultation]].

### WorkerModeToken
The marker carried inside a worker's prompt that identifies its holder as a nested worker rather than the orchestrator. It exempts the holder from the routing gate. The exemption is deliberately observable to a model — it lives in the prompt, not in the environment — because an exemption a model cannot perceive always resolves to "not exempt".

### AdvisoryStalemateReport
The structured result of an [[AdvisoryConsultation]] that exhausted its round cap without an
explicit Critic approval: both final positions (the last Planner proposal and the last Critic
response) plus exactly three resolution options — approve the Planner's architecture, approve the
Critic's, or escalate to a human. It never selects a winner; picking one would be the false
consensus the consultation exists to prevent.

### ConsultationTranscript
The human-readable record an [[AdvisoryConsultation]] writes fresh (never appended) on every one of
its seven outcomes. For six of them it carries the full task text and every round's Planner/Critic
exchange that actually ran — that is the entire point of a transcript (a `budget_skipped` run has no
rounds to show; its transcript reports the skip itself). On a `sensitivity_halt` it instead carries
only the matched sensitivity marker and the halt's [[TaskIdentity]]: repeating the task text here
would defeat the halt the transcript exists to report.

### AdvisoryTelemetryRecord
One structured JSON record appended to the shared `.ralph/routing_telemetry.jsonl` stream — emitted
once per [[AdvisoryConsultation]] and once per [[CouncilDebateRound]] decision, both families sharing
the one file. The advisory record alone carries a `kind` field naming it an advisory consultation; a
council record carries no such field at all. That asymmetry, not a discriminator on both sides, is
what lets an auditor tell the two record families apart — council decisions are exactly the ones with
no `kind`. Never carries task text or the value that tripped a sensitivity halt, on any outcome.

### TaskIdentity
The `task_id` an [[AdvisoryConsultation]] resolves once per run and carries on its
[[AdvisoryTelemetryRecord]] — and, on a `sensitivity_halt`, on its [[ConsultationTranscript]] too, so
the two stay correlated for an auditor. A caller-supplied id always wins, on every outcome including
`sensitivity_halt` — this is the production path, since the routing protocol that drives this module
always supplies one. Absent one, every outcome but `sensitivity_halt` defaults to a stable digest of
the task text, safe to reuse across runs of the same task. `sensitivity_halt` never uses that digest:
it is derived from the task text, and a digest over guessable text is a confirmation oracle — exactly
what the redaction boundary around a halt (see [[AdvisoryConsultation]]) forbids. Its default is
instead a random identity, unrelated to the task text and generated fresh per halt, so an auditor can
still count and correlate distinct halts without recovering anything about what was halted. That
random default only ever applies when no caller id was given, which is what makes the trade-off
acceptable in practice.

### AllowedDirectAction
An action the orchestrator performs itself rather than routing to a worker. The set is closed and enumerated: everything outside it is a routing violation. Membership is decided by whether a worker *can* do the work, not by whether the orchestrator finds it convenient — version control is a member because worker sandboxes cannot perform it at all.

### CriticalDialogue
The upgraded [[AdvisoryConsultation]] machinery serving four occasions — ambiguity, plan review, post-execution code review, and post-mortem — under one round/contract/transcript/telemetry infrastructure. Tiered topology: a cross-family Planner–Critic pair by default; for Complex tasks, a panel of one Planner and two Critics from two other model families, where consensus requires an explicit approval from both. Stalemates halt to the human in every mode. Spec 0003.

### VerdictContract
The response contract a Critic must satisfy for its approval to count: rationale before the verdict line, quotes from the reviewed artifact that verify mechanically, and enumerable atomic objections. An approval carrying zero engagement units parses as "not approved" — the structural defense against rubber-stamping, extending spec 0001's rule that absence of rejection is not agreement.

### DegradationLadder
The per-session budget response of a [[CriticalDialogue]]: `dialogue_budget.session_dialogue_cap` in `routing-config.json` is a degradation trigger, not a hard ceiling. Spend below the cap runs undegraded (rung 0); each further cap's-width of spend takes one rung — reduced rounds (1), a single cheap model in every seat at low effort (2, recorded as degraded independence), and only at three times the cap the skip rung (3), so a cap of 10 admits up to 30 dialogues — the last 20 of them degraded — before every further one is skipped. A cap of zero (or negative) degenerates to always-skip. Every rung is visible: rung 2 flags `degraded_independence`, rung 3 is its own `budget_skipped` outcome, and every rung reaches the [[AdvisoryTelemetryRecord]] as `degradation_rung`. Spec 0003 ticket 09.

### LearningJournal
A dedicated, content-free JSONL stream recording four signal families per action — worker execution, ground-truth outcomes, dialogue quality, and protocol compliance. Kept separate from the audited [[AdvisoryTelemetryRecord]] stream so the audit contract stays frozen. Carries numbers, categories, and ids only; a coarse task-type tag on normal tasks; no tag of any kind on sensitivity halts. Records correlate via [[TaskIdentity]]. Spec 0004.

### LearnerWorker
The background worker that turns the [[LearningJournal]] into changed behavior: a light session-end distillation into institutional memory, and a deep weekly run proposing routing-table updates and brief diffs. It only proposes — an external acceptance gate (repeated benchmark trials, zero scoreboard regression) disposes, application is risk-tiered, adopted state is git-versioned, and a post-adoption regression auto-reverts. The protocol is unreachable by construction. Spec 0004.
