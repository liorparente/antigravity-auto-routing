# 0015. Consolidate CriticalDialogue Engine and Eliminate Shallow Facades

Date: 2026-08-29
Status: Accepted

## Context
`advisory_consultation.py` (289 lines) and `council_review.py` (29 lines) existed as shallow pass-through facades over `debate_orchestrator.py`, maintaining brittle alias chains and `sys.path` workarounds across skills.

## Decision
1. Consolidate all consultation and council review orchestration into a deep `skills/worker-routing/critical_dialogue.py` module.
2. Delete `advisory_consultation.py` and `skills/council-review/scripts/council_review.py`.
3. Update `skills/worker-routing/__init__.py` and tests to bind directly to `critical_dialogue.py`.
4. Clean duplicate `SENSITIVE_PATTERNS` in `agent_council.py` by delegating directly to `sensitivity_redactor.py`.

## Consequences
- Single cohesive entry point for ambiguity, plan reviews, diff audits, and panel reviews.
- 350+ lines of shallow pass-through code and dynamic import hacks permanently removed.
- Full compatibility maintained through `worker-routing` root package exports.
