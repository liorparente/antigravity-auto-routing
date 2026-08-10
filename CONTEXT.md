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

### AllowedDirectAction
An action the orchestrator performs itself rather than routing to a worker. The set is closed and enumerated: everything outside it is a routing violation. Membership is decided by whether a worker *can* do the work, not by whether the orchestrator finds it convenient — version control is a member because worker sandboxes cannot perform it at all.
