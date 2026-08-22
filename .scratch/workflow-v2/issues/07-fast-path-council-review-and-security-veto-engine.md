# 07 — Fast-Path 1-Shot Council Review & Security Veto Engine

**What to build:** Implement the 1-shot parallel synthesis fast path in debate_orchestrator.py and debate_state_machine.py, terminating in 1 round upon weighted quorum (>= 0.60), while preserving unilateral security vetoes and routing stalemates to escalation.

**Blocked by:** 03 — Dynamic Role Resolution & Preference Fallback in production_invoker.py, 06 — Perspective Reviewer Prompt Assembly & Contracts

**Status:** complete

- [x] Update debate_orchestrator.py to execute perspective reviewers in parallel concurrently.
- [x] Implement 1-shot fast path termination when weighted quorum (>= 0.60) is met without security veto.
- [x] Enforce unilateral security veto halting for critical severity findings regardless of quorum vote.
- [x] Route unresolved stalemates to Adjudicator local model or HITL with AdvisoryStalemateReport.
- [x] Verify unit tests pass in test_debate_orchestrator.py and test_debate_state_machine.py.
