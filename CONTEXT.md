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

### SilentEffortDowngrade
A CLI failure mode where an unsupported `--effort` value is not rejected but silently discarded: the invoking CLI (`claude`) writes a warning to stderr and dispatches the session at the model's `default_effort` instead, with no throw and no non-zero exit. Distinct from `agy`, which hard-errors on an invalid effort. Code that assumes a bad effort flag surfaces as a failed subprocess must special-case providers exhibiting this behavior rather than relying on exit-code detection.

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
positionally: group by `(task_id, ground_truth)`, and the last record in the append-only stream wins
— the same reduction [[ComplianceRecord]] already uses. Ticket 27 confirmed and settled this formal
reduction convention; no schema discriminator is needed. Spec 0004 tickets 25 and 27.

### LearnerWorker
The background worker that turns the [[LearningJournal]] into changed behavior: a light session-end distillation into institutional memory, and a deep weekly run proposing routing-table updates and brief diffs. It only proposes — an external [[AcceptanceGate]] (repeated benchmark trials, zero scoreboard regression) disposes, application is risk-tiered, adopted state is git-versioned, and a post-adoption regression auto-reverts. The protocol is unreachable by construction. Spec 0004. **Never instantiated in this repository as of 2026-08-27:** no `learned-state/` directory has ever existed here, so this worker has never produced an adopted `memory` document and the [[RiskTieredApplication]] tiers have never fired. Read every capability described above as designed-and-tested, not as running — spec 0014 deliberately scopes it out for that reason.

### ReplayBenchmarkRecord
One [[LearningJournal]] record per trial of the fixed replay benchmark: a `task_set` identifier, whether the trial succeeded, and — only when it did — the score the injected runner returned. A failed trial carries no `score` at all, never `0.0`: the two mean different things (the runner crashed vs. it ran and scored zero), and the [[AcceptanceGate]]'s fail-closed rule depends on telling them apart. Carries no [[TaskIdentity]]: the benchmark grades the evaluator's own fixed task set, never a development task, so there is nothing to correlate against and no sensitivity-halt boundary to enforce. It does carry an optional [[RunIdentity]] — a caller grouping one gate evaluation's trials under one `run_id`, the same optional-and-never-invented rule every other family follows. The fifth record family, added because the outcome family's closed verdict vocabulary (`OUTCOME_VERDICTS`) has nowhere for a number to sit. Spec 0004 ticket 26.

### AcceptanceGate
The proposal gate a [[LearnerWorker]] must clear before a routing-table update auto-applies: the fixed benchmark task set is run a configured number of times through an injected runner, each trial journaled as a [[ReplayBenchmarkRecord]], and the proposal is accepted only when *every* trial's score meets threshold and comparing the scoreboard before and after the trials shows zero regressed metrics — a single winning run among losing ones, or a regression in any one metric, both reject even when the mean score looks excellent. The learner never grades its own proposal: every score is the runner's, never a code path the gate or the learner computes itself. A runner failure mid-trial is journaled as a failed trial and fails the gate closed rather than being treated as a missing, ignorable data point. A journal write that fails rejects on its own account too — the trials then exist only in memory, the after-scoreboard read back from disk sees none of them, so nothing regresses and the gate would otherwise open on evidence that no longer exists. Three causes reject, and `journal_complete` distinguishes the one an operator can act on: fix the disk, re-run. Spec 0004 ticket 18.

### LearnedState
Versioned snapshots of the system's own learned documents — `memory`, `routing_table`, `briefs`, a closed vocabulary with no path-shaped member — with a one-step rollback. The opposite of [[LearningJournal]] on purpose: a learned lesson *is* text, so this store carries content where the journal is forbidden to. "Git-tracked" means the versions sit under a tracked directory (`learned-state/`, checked against the real `.gitignore`) and nothing more — this module never runs `git`; a worker sandbox that locks `.git/` would deadlock a `git commit` on the adoption path, and the human's ordinary commit is what actually captures history. A version, once written, is never rewritten: `adopt` allocates one past the highest version number ever used (never merely the current one) and writes it exactly once, so undoing an adoption only moves which version `current_version_dir` resolves to, never touches a byte on disk — "byte for byte" is true by construction, not by a copy that has to be verified. A version directory `history.jsonl` never names is an inert orphan left by a crash between writing the snapshot and recording it in history — left in place deliberately, for forensics, and safe to delete once you've confirmed it really is absent from `history.jsonl`. `roll_back` undoes the most recent adoption not already undone (a backward walk over `history.jsonl` skipping ones a prior rollback consumed), refusing to roll back the first adoption ever, since the state before it is the un-learned system this store does not model. A leaf module: no import of any sibling in this skill directory, no argument anywhere through which a live repository file (`routing-config.json`, a protocol file) could be named — ticket 20 owns applying a version to the live system, this ticket owns only versioning it. **Consequence of the store never having been created here (verified 2026-08-27):** `learned_state.get_scoped_memory` reads `read_current(root).get("memory")`, which is always `None` in this repository, so `extract_scoped_memory` always falls back to its built-in `GOLDEN_RULES` catalog. That catalog is therefore the *only* institutional memory that has ever reached a [[Worker]]'s mission brief. `knowledge/institutional-memory.md` is a third, separate artifact that no production code path opens at all — its sole reader is the sync test that guards it against the catalog. Spec 0014.

Two invariants govern how it fails, and both exist because the alternative was a rollback that silently *mostly* restored the previous state. **Every write is serialized across processes** by an exclusive lock spanning the whole read-decide-write section of `adopt` and `roll_back`, not merely the history append: two concurrent adopts each decide what to write from a read the other invalidates, and before the lock that lost a committed write in six trials of six. **Every question this store asks the filesystem is asked in a form that can fail** — `os.stat`/`os.listdir`, never `Path.is_dir()`/`.exists()`, which swallow `PermissionError` and answer `False`. A call that cannot fail is an assumption, not a question, and each time one was used the store reported damage as absence: an unlistable version directory read as an empty store, an intact one behind an unreadable parent reported as deleted. Every `OSError` from the store's own files therefore becomes a `ValueError` through a single converter, rather than a guard per path level — six review rounds each closed one level and found the next. Spec 0004 ticket 19.

### CouncilReview
A multi-agent peer review system orchestrating three independent foundation model families (Claude, Codex, Gemini) concurrently against strict heuristics (Deep Module Design, Security Bounds, Anti-Bloat) to validate implementation plans and code diffs before execution.

### SoftConfidenceScoring
A continuous evaluation metric where reviewers assign a score from -1.0 (strict rejection) to +1.0 (unreserved approval) instead of binary pass/fail votes. Includes an asymmetric loss multiplier (1.5x on negative scores) to heavily penalize faulty approvals of broken or vulnerable code.

### SecurityVeto
A unilateral, non-majority override mechanism where any single reviewer emitting a verified Critical or High severity security finding (e.g. CWE-89, CWE-78) immediately halts the review pipeline (`SECURITY_HALT`), preventing majority coalitions from overriding valid security vulnerabilities.

### ConsensusTable
The deterministic aggregator that evaluates reviewer vote records against weighted quorum thresholds and candidate hashes, resolving outcomes into UNANIMOUS, QUALIFIED, MATERIAL_DISAGREEMENT, INCOMPLETE, UNRESOLVED, or SECURITY_HALT.

### RiskTieredApplication
The four-tier safety mechanism that governs how proposed updates from the [[LearningLoop]] are applied to system documents based on their blast radius: Tier 1 memory lessons auto-apply to [[LearnedState]], Tier 2 routing-table updates auto-apply only after clearing the [[AcceptanceGate]], Tier 3 brief diffs are held as [[PendingProposal]]s awaiting explicit human approval, and Tier 4 protocol files are unreachable by construction via the closed `LearnedDocument` vocabulary (`{"memory", "routing_table", "briefs"}`). Spec 0004 ticket 20.

### PendingProposal
A staged change to worker briefs held in `.ralph/pending_proposals.jsonl` under an exclusive file lock, awaiting explicit human approval before being adopted into [[LearnedState]]. Spec 0004 ticket 20.

### ExpectedCurrent
A content-agnostic Compare-And-Swap (CAS) precondition mapping (`Mapping[LearnedDocument, str | None] | None`) passed to `learned_state.adopt`. Verified under the store's exclusive lock against the active document snapshots before writing a new version. A mismatch indicates concurrent state mutation and raises a distinct `ValueError`, allowing callers (such as `risk_tiered_application.apply_memory_lesson`) to retry transactionally without content leakage into `learned_state.py`. Spec 0004 ticket 33 / ADR 0010.

### MemoryLessonGrammar
The canonical round-trip text format for learned memory documents. Each entry begins with a `"- "` bullet prefix, with multiline continuations indented by two spaces (`"  "`). Stripping and serialization preserve internal indentation (such as embedded code snippets), normalize legacy newlines (`\r\n`, `\r` -> `\n`), and reject malformed unindented mixtures fail-closed. Spec 0004 ticket 33 / ADR 0010.

### WorkerInvocation
A unified runtime execution module encapsulating process spawning, non-interactive stdin enforcement, process group termination upon timeout, model alias resolution, transparent wall-clock duration measurement, cost estimation, and safe journal emission for both synchronous single-worker calls and asynchronous multi-model review panels.

### DebateQuorumPolicy
A pure voting aggregation rule evaluated over arbitrary sequences of critic responses in [[AdvisoryConsultation]] debates: `unanimous` (requires all participating critics to approve), `majority` (`count // 2 + 1` approvals), and `qualified` (`(2 * count + 2) // 3` approvals, e.g. 2 of 2, 2 of 3, 3 of 4).

### AbstentionHandling
The deliberate normalization of `"abstain"` / timeout critic responses into valid non-approval votes without triggering unparseable verdict errors or terminating the debate. Abstentions increment total participant count without contributing to affirmative approvals, allowing quorum policies to evaluate gracefully under partial panel availability.

### Dyad
A binary turn-based deliberation exchange between a single Planner and a single Critic across up to three revision rounds, orchestrated within `debate_orchestrator.py` for Medium-complexity tasks. Spec 0009.

### CouncilPanel
A concurrent multi-model review ensemble evaluated under weighted scoring, soft confidence metrics, and selective HMAC-SHA256 manifest signing, orchestrated within `debate_orchestrator.py`. Spec 0009.

### ReviewCouncilFacade
The lightweight, backward-compatible 19-line delegating module in `skills/council-review/scripts/council_review.py` that routes external CLI and library callers directly to `debate_orchestrator.ReviewCouncil` without duplicating state machines or policy files. Spec 0009.

### SecurityVetoHandler
The universal fail-closed circuit breaker in `production_invoker.py` and `debate_state_machine.py` combining domain-agnostic finding severity/confidence evaluation (Trigger 1: any perspective with Critical/High finding at confidence $\ge 0.80$ halts debate) with perspective-exclusive unilateral block evaluation (Trigger 2: `reviewer_security` explicit `BLOCK` verdict halts debate unconditionally, while non-security `BLOCK` votes participate in weighted quorum reduction). Spec 0009, Spec 0012.

### DynamicCandidateHash
The SHA-256 digest calculated over the exact proposal content reviewed by a council panel, verified against critic candidate hashes during consensus reduction to detect and reject prompt tampering or stale candidate reviews. Spec 0009.

### Harness
The host runtime or CLI environment (such as Google Antigravity, Claude Code, or OpenAI Codex) executing the primary session and driving interaction with the user or automated workflows.

### Orchestrator
The root session agent operating within any [[Harness]], responsible for understanding task goals, planning, enforcing the routing gate, and coordinating execution across workers without performing unrouted state-modifying actions directly.

### Worker
A subordinate CLI process or subagent instance invoked by the [[Orchestrator]] with an explicit [[WorkerModeToken]], executing a scoped task within defined capability boundaries without being subject to top-level routing gates.

### Role
An abstract functional responsibility or job-to-be-done (such as `planner`, `builder_heavy`, `builder_light`, `reviewer_architecture`, `reviewer_risk`, `reviewer_maintainability`, `reviewer_security`, `adjudicator`, or `learner`) defined independently of any specific [[Provider]] or [[Model]].

### CapabilityRequirements
The declarative set of technical constraints and attributes (such as reasoning tier, tool access permissions, context window capacity, and local execution isolation) required to fulfill a specific [[Role]].

### Provider
A transport adapter or invocation interface (such as a vendor CLI wrapper, local model server, or HTTP API client) that connects an abstract [[Role]] and its [[CapabilityRequirements]] to an underlying [[Model]].

### Model
A concrete foundation model version and weights configuration (such as `claude-sonnet-5`, `gpt-5.6-sol`, `gemini-3.7-flash`, or `qwen3.8-27b`) invoked through a [[Provider]] to execute inference.

### ContextLayer
One of four strictly isolated tiers of prompt and memory state (Global memory, Project rules/glossary, Task specification, and Session history) assembled at invocation time to provide workers with relevant context without polluting the core context window.

### PerspectiveReviewer
A specialized reviewer within a [[CouncilPanel]] assigned to evaluate an architectural plan or code diff through one specific analytical domain lens (`reviewer_architecture`, `reviewer_risk`, `reviewer_maintainability`, or `reviewer_security`) rather than a model brand identity.

### LearningReportHtml
A pure, clock-free standalone HTML dashboard generator rendering empirical learning metrics, model family performance breakdowns, and compliance/degradation audit event streams directly from [[LearningJournal]] records beside the Markdown weekly report. Ticket 44.

### ModelCapabilityRegistry
A centralized, strongly-typed registry mapping every audited pairing of a provider and a [[Model]] to that model's valid reasoning effort levels (`supported_efforts`), factory default effort (`default_effort`), and effort-ceiling capability tier (`tier` — the model's own highest supported reasoning effort rung; distinct from [[CapabilityRequirements]]'s `reasoning_tier`, a role's *required* floor rather than a model's *offered* ceiling — though both are drawn from the same rung vocabulary and so are directly rank-comparable — with one exception: a model with no effort ladder to rank at all carries `tier`'s non-rung sentinel `"none"` (three registry entries today, for two different reasons — `claude-3-7-sonnet` predates the reasoning-effort ladder, while both `lm_studio_local` models are served by a provider whose CLI contract exposes no reasoning-effort parameter at all, so no LM Studio model can carry a ladder regardless of vintage), which no role's floor can be satisfied by and which must be special-cased before any rank comparison), plus context capacity. It enables frontend interfaces to reject unsupported reasoning settings before runtime invocation. Built in `routing_config.py` from Ticket 45's audited CLI catalog, keyed by `(provider, model_id)` — a shape that lets a model explicitly named in `probe_models._CROSS_PROVIDER_EFFORT_LADDERS` (today, only `claude-sonnet-4-6`) carry a distinct second entry whose ladder is corrected down from that second provider's own full CLI enum (dropping `xhigh`) rather than copied from the natively audited provider, so it need not be narrower than the native entry in either field — today it is one rung wider, reaching `max`; the underlying catalog otherwise keys by bare model id and keeps only one provider's entry per id. Ticket 46 / Spec 0013.

### ServerMode
The condition, evaluated client-side by the dashboard's `isServerMode()` as `/^https?:$/.test(location.protocol)`, that the page was fetched from a `learning_report.py --serve` origin rather than opened straight from disk over `file://`. It is the single precondition gating every behavior that needs a same-origin backend: US14's `POST /api/config` save branch, Decision 1's automatic launch probe, and US8's on-demand live model refresh — outside it, the same "שמור שינויים" button downloads a `routing-config.json` instead (US13). Reachable only because the server serves the document itself at `GET /`; while it answered just its two `/api/*` routes, no operator could ever be in ServerMode and all three behaviors were unreachable despite passing their own unit tests (Ticket 53 / see `ERRORS.md`). Ticket 51/53 / Spec 0013 Decision 4.

### AutoSnap
The client-side state machine behavior that automatically resets a role's selected reasoning effort level whenever the user selects a new [[Model]] that does not support the currently active effort level, preventing invalid CLI worker invocations. It snaps to the target model's `default_effort`, falling back to the first rung of that model's `supported_efforts` in the one case that default is not itself a supported rung — so the snap target is always a rung the model actually offers, never merely the one it nominally defaults to. A model with no effort ladder at all (`supported_efforts` empty — see [[ModelCapabilityRegistry]]'s `"none"` tier) snaps to no effort rather than to a rung. Implemented twice against the same three-branch contract: `_resolve_effort_state` for server-rendered markup and `resolveEffortState` for live re-binding. Ticket 48 / Spec 0013.

### FloatingActionPill
A sticky glassmorphic bottom control bar in the Role Configuration Matrix dashboard that tracks uncommitted user edits (dirty state), provides a live change count with visual pulse, and exposes atomic Save, Undo, and Reset-to-Default actions. Ticket 49 / Spec 0013.

### RuleCatalog
The authoritative, strongly-typed in-code catalog of distilled engineering directives (`prompt_assembler.GOLDEN_RULES`) scored by `extract_scoped_memory` against task descriptions and target files to inject relevant institutional memory into worker prompts. Spec 0014.

### InstitutionalMemoryDocument
The human-readable Markdown build artifact (`knowledge/institutional-memory.md`) rendered deterministically from the [[RuleCatalog]] and its metadata by `prompt_assembler.render_institutional_memory()`. It is never edited directly. Spec 0014.

### CoTStreamingBlindspot
An execution failure mode where an SSE streaming client listens only to `delta.content` and discards `delta.reasoning_content` while calling a local reasoning model (e.g. Qwen 27B / DeepSeek R1). The client produces zero pipe bytes during the initial 60–150s Chain-of-Thought thinking phase, causing background task watchers to falsely flag the process as frozen (`Last progress: never`).

### UnbufferedInferenceTransport
A process execution standard requiring explicit `sys.stdout.flush()` on every streaming token and the use of unbuffered execution (`python3 -u` / `PYTHONUNBUFFERED=1`) when dispatching background Python scripts or CLI sub-processes over non-TTY pipes.

### StructuralConfigDrift
A runtime schema drift failure where a top-level configuration key (e.g. `_active_profile`) is registered only in `routing_check.NON_ROLE_CONFIG_KEYS` but omitted from `routing_config.STRUCTURAL_KEYS`, causing the dispatcher to parse top-level configuration metadata as invalid worker role definitions.
