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

### TaskLabel
The pair a [[LearningJournal]] record hangs on: a [[TaskIdentity]] plus, optionally, a coarse
task-type tag such as "bugfix" or "refactor". A dedicated type rather than two loose fields on each
record, because the rule worth enforcing is a rule about the pair — a sensitivity-halted task carries
no tag of any kind, since a tag is derived from task text and a tag plus a timestamp is a
confirmation oracle over guessable work. That rule has two independent locks: the halted constructor
takes no tag argument, so there is no parameter through which one could be supplied, and construction
itself rejects a label that is both halted and tagged, so bypassing the constructors fails too. A
halted label's `task_id` must be the identity the halt already resolved — random, never a digest —
which the journal cannot verify and the caller therefore owns. Spec 0004.

### AllowedDirectAction
An action the orchestrator performs itself rather than routing to a worker. The set is closed and enumerated: everything outside it is a routing violation. Membership is decided by whether a worker *can* do the work, not by whether the orchestrator finds it convenient — version control is a member because worker sandboxes cannot perform it at all.

### CriticalDialogue
The upgraded [[AdvisoryConsultation]] machinery serving four occasions — ambiguity, plan review, post-execution code review, and post-mortem — under one round/contract/transcript/telemetry infrastructure. Tiered topology: a cross-family Planner–Critic pair by default; for Complex tasks, a panel of one Planner and two Critics from two other model families, where consensus requires an explicit approval from both. Stalemates halt to the human in every mode. Spec 0003.

### VerdictContract
The response contract a Critic must satisfy for its approval to count: rationale before the verdict line, quotes from the reviewed artifact that verify mechanically, and enumerable atomic objections. An approval carrying zero engagement units parses as "not approved" — the structural defense against rubber-stamping, extending spec 0001's rule that absence of rejection is not agreement.

### DegradationLadder
The per-session budget response of a [[CriticalDialogue]]: `dialogue_budget.session_dialogue_cap` in `routing-config.json` is a degradation trigger, not a hard ceiling. Spend below the cap runs undegraded (rung 0); each further cap's-width of spend takes one rung — reduced rounds (1), a single cheap model in every seat at low effort (2, recorded as degraded independence), and only at three times the cap the skip rung (3), so a cap of 10 admits up to 30 dialogues — the last 20 of them degraded — before every further one is skipped. A cap of zero (or negative) degenerates to always-skip. Every rung is visible: rung 2 flags `degraded_independence`, rung 3 is its own `budget_skipped` outcome, and every rung reaches the [[AdvisoryTelemetryRecord]] as `degradation_rung`. Spec 0003 ticket 09.

### LearningJournal
A dedicated, content-free JSONL stream recording five signal families per action — worker execution, ground-truth outcomes, dialogue quality, protocol compliance, and replay-benchmark trials. Kept separate from the audited [[AdvisoryTelemetryRecord]] stream so the audit contract stays frozen. Carries numbers, categories, and ids only; a coarse task-type tag on normal tasks; no tag of any kind on sensitivity halts. Records correlate via [[TaskIdentity]] and, within a task, via [[RunIdentity]] — except the replay-benchmark family, which grades the evaluator's own fixed task set rather than a development task and so carries no [[TaskIdentity]]. It still carries the optional [[RunIdentity]] every family may, grouping one gate evaluation's trials. "Signal family" names one of those five groupings and nothing finer — the four ground truths *inside* the outcome family are ground truths, never signals, so that one word keeps one granularity. Spec 0004.

### RunIdentity
The `run_id` a [[LearningJournal]] record carries to say *which attempt* it belongs to, as [[TaskIdentity]] says *what was worked on*. The two are needed together because `task_id` is deliberately stable across repeats — absent a caller-supplied id it is a digest of the task text — so two consultations of one task otherwise collapse into a single identity whose costs sum as though one run happened and whose second attempt's ground truth attaches to the first. Rework, one of the three efficiency measures spec 0004 asks for, is exactly what that collapse hides: it is counted as the distinct run identities carrying one [[TaskIdentity]], minus one. Distinct from a retry count, which stays honestly zero — no worker invocation is ever re-attempted today, and a second consultation of the same task is a second run rather than a retry of the first. Optional on every family, and never invented: a writer with no honest run identity omits it, and a consumer treats an omission as "names no run" rather than folding all such records into one. A [[ComplianceRecord]] uses it for the same job one level up — telling two audits of one session apart from one audit written twice. Spec 0004.

### ComplianceRecord
One [[LearningJournal]] record per audit *run* — not per session. `routing-audit.sh` with no argument audits the most recent conversation, so a plain run followed by a `--strict` one appends two records under a single session id; that is kept rather than deduplicated, because a re-audit is a real event and a changed verdict is worth having. A consumer asking a per-session question reduces first: group by session id, and within a group the last record wins, file order being audit order in an append-only stream. Its `timestamp` is when the audit ran and never when the session happened — a backlog audited in one sitting stamps every record minutes apart — so a discipline trendline plots against `session_last_activity`, derived from the audited log's last modification, and skips a record that has none rather than substituting. A session id the journal's identifier pattern cannot hold is recorded under a digest of itself, never dropped: an audit is not re-run, so a refused record is a verdict lost permanently. Spec 0004.

### OutcomeRecord
One [[LearningJournal]] record per ground truth graded against an earlier decision: `tests`,
`review`, `plan`, or `stalemate_resolution`, each paired with a closed vocabulary of verdicts
(`OUTCOME_VERDICTS`) so a verdict belonging to another ground truth cannot be attached. Its `task_id`
is deliberately the *decision's* identity, not a fresh one for the grading event — that reuse is the
entire mechanism by which "what we decided" can be checked against "were we right." `run_id` narrows
what a record grades (that run, vs. the task as a whole) and is never invented, the same rule
[[RunIdentity]] states generally.

`plan` has two producers under one `task_id`, and that is by design, not an oversight.
[[AdvisoryConsultation]] records `accepted` itself, at its `_result` choke point — but only when
`outcome == "consensus"` and the occasion is plan-producing (`ambiguity`, `plan-review`); a
`code-review` or `post-mortem` dialogue debates a diff or a lesson, not a plan, so a plan verdict
about one would describe an artifact that does not exist. `rejected` has no in-process producer at
all: a stalemate is not a rejection — its first resolution option is "approve the Planner's
architecture," so the human who resolves it may accept the very plan a `rejected` record would have
condemned — so only a human who actually read and declined a plan may record one, by hand, once that
happens. Two `plan` records for one task are therefore expected, not a conflict.
`OutcomeRecord` carries no actor or stage field to distinguish them, so a consumer resolves them
positionally: group by `task_id`, and the last record in the append-only stream wins — the same
reduction [[ComplianceRecord]] already uses. Whether that positional convention is durable enough, or
the schema needs an explicit discriminator, is open; see ticket 27. Spec 0004 ticket 25.

### LearnerWorker
The background worker that turns the [[LearningJournal]] into changed behavior: a light session-end distillation into institutional memory, and a deep weekly run proposing routing-table updates and brief diffs. It only proposes — an external [[AcceptanceGate]] (repeated benchmark trials, zero scoreboard regression) disposes, application is risk-tiered, adopted state is git-versioned, and a post-adoption regression auto-reverts. The protocol is unreachable by construction. Spec 0004.

### ReplayBenchmarkRecord
One [[LearningJournal]] record per trial of the fixed replay benchmark: a `task_set` identifier, whether the trial succeeded, and — only when it did — the score the injected runner returned. A failed trial carries no `score` at all, never `0.0`: the two mean different things (the runner crashed vs. it ran and scored zero), and the [[AcceptanceGate]]'s fail-closed rule depends on telling them apart. Carries no [[TaskIdentity]]: the benchmark grades the evaluator's own fixed task set, never a development task, so there is nothing to correlate against and no sensitivity-halt boundary to enforce. It does carry an optional [[RunIdentity]] — a caller grouping one gate evaluation's trials under one `run_id`, the same optional-and-never-invented rule every other family follows. The fifth record family, added because the outcome family's closed verdict vocabulary (`OUTCOME_VERDICTS`) has nowhere for a number to sit. Spec 0004 ticket 26.

### AcceptanceGate
The proposal gate a [[LearnerWorker]] must clear before a routing-table update auto-applies: the fixed benchmark task set is run a configured number of times through an injected runner, each trial journaled as a [[ReplayBenchmarkRecord]], and the proposal is accepted only when *every* trial's score meets threshold and comparing the scoreboard before and after the trials shows zero regressed metrics — a single winning run among losing ones, or a regression in any one metric, both reject even when the mean score looks excellent. The learner never grades its own proposal: every score is the runner's, never a code path the gate or the learner computes itself. A runner failure mid-trial is journaled as a failed trial and fails the gate closed rather than being treated as a missing, ignorable data point. A journal write that fails rejects on its own account too — the trials then exist only in memory, the after-scoreboard read back from disk sees none of them, so nothing regresses and the gate would otherwise open on evidence that no longer exists. Three causes reject, and `journal_complete` distinguishes the one an operator can act on: fix the disk, re-run. Spec 0004 ticket 18.
