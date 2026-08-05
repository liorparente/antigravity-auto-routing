# Domain Decisions — Council Review

1. **Orchestration**: Claude, Codex, and `agy` are real, separate CLI processes, not a simulated conversation within a single model's prompt.
2. **Round Table Roles**: All three primary members receive the same evidence and perform the full planning, upgrading, and criticism task. They are not restricted to narrow sub-roles.
3. **Consensus & Adjudication**: Additional adjudication occurs only for a material disagreement. Unanimous or qualified agreement does not trigger it.
4. **Local Models (LM Studio)**: LM Studio provides an additional local opinion (adjudication tie-breaker) when allowed. Sensitive inputs are classified early and strictly routed to a local-only path with a local quorum.
5. **Exact Models**: Models may improve over time, but each run resolves an allowlisted model once, passes it explicitly, and records requested/actual model, effort, and CLI version.
6. **Output**: The final user artifact is `council_review_report.md` inside a unique run directory.
