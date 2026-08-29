# Domain Glossary (CONTEXT.md)

### SecurityContext
An immutable context constructed at system startup that holds resolved secrets and the workspace root directory for HMAC verification, isolating secret resolution from per-step metrics.

### ModelRoutingPolicy
A deterministic decision engine combining task complexity classification (T0–T3) and sensitivity detection to route tasks between local and cloud models.

### AdvisoryConsultation
A structured deliberation loop between Planner and Critic models triggered when task complexity classification is ambiguous, preventing false consensus.

### CouncilDebateRound
Deterministic multi-agent panel evaluation checking safety and constraints with an automated adjudicator resolving disagreements.

### WorkerModeToken
A marker (`[WORKER-MODE: NESTED-EXEC]`) carried inside a worker's prompt that identifies its holder as a nested worker and exempts it from the routing gate.

### AcceptanceGate
A deterministic gate validating proposed code against regression boundaries, test assertions, and institutional invariants before changes are merged.

### LearnedState
Atomic, versioned storage of accumulated agentic insights, scoreboard metrics, and Golden Rules maintained under `.ralph/` and synced across harnesses.

### VerdictContract
A strictly parsed critic response schema requiring verbatim quote verification against reviewed artifacts for approvals to guarantee absence of false agreement.

### TaskIdentity
A stable unique identifier resolved per run for telemetry and transcripts, defaulting to a non-reversible random identity on sensitivity halts.
