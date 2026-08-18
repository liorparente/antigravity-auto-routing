# Issue 31 — Further decompose `debate_orchestrator.py`

**Status:** backlog

## Follow-up design question

`debate_orchestrator.py` remains a broad compatibility facade after extracting
the prompt, sensitivity, contract, degradation, reporting, transcript, and
learning-journal modules. Decide whether its remaining orchestration,
state-machine, roster, and compatibility responsibilities should be separated
into smaller deep modules with narrow public interfaces.

## Why this is deferred

The current Spec 0007 change only requires annotation safety and does not
settle the target module boundaries, compatibility policy, or migration plan.
Those are architectural decisions and should be reviewed separately before
changing imports or public APIs.

## Suggested approach

Map the current responsibilities and call graph, propose module boundaries
with explicit ownership and test seams, then validate a staged extraction that
preserves the existing `debate_orchestrator` compatibility surface.
