# Issue 31 — Further decompose `debate_orchestrator.py`

**Status:** backlog (deferred from Spec 0007 / Review Round 8)

## Problem Statement

`debate_orchestrator.py` was created during Spec 0007 as the primary orchestration engine for Planner/Critic CriticalDialogues, reducing `advisory_consultation.py` to a 94-line compatibility facade. However, `debate_orchestrator.py` itself contains ~2,900 lines spanning multiple responsibilities:
1. Low-level subprocess worker execution and JSON parsing.
2. In-memory debate state progression (`advance_debate_state`) and quorum math.
3. Roster resolution and fallback negotiation (`resolve_roster`).
4. Learning loop ground truth journaling and telemetry persistence.
5. Threaded dispatching for post-mortem dialogues.

## Competing Architectural Options & Trade-Offs

### Option A: Monolithic Orchestrator Engine (Status Quo)
- **Design:** Keep `debate_orchestrator.py` as the consolidated engine while leaf modules (`prompt_assembler.py`, `sensitivity_redactor.py`, `executive_dialogue_report.py`, `dialogue_contracts.py`) remain pure leaves.
- **Pros:** Zero intra-orchestrator import overhead, single call stack for debugging debate state loops, completely stable internal seams.
- **Cons:** High line count (~2,900 lines), mixing pure state transition functions with I/O and subprocess execution.
- **Migration Risk:** None (current state).

### Option B: Micro-Decomposition into Functional Sub-Modules
- **Design:** Split `debate_orchestrator.py` into:
  - `debate_state_machine.py` (pure state reducer, immutable `advance_debate_state`).
  - `roster_resolver.py` (model roster selection, fallback chains).
  - `debate_runner.py` (I/O, subprocess invocations, journal side-effects).
  - `post_mortem_dispatcher.py` (background thread spawning).
- **Pros:** Maximum modularity, pure unit testing of `debate_state_machine` without I/O fixtures, strict adherence to single-responsibility principle.
- **Cons:** Increased file count, circular dependency risks between state definitions and execution runners, complex star-import maintenance across multiple facade layers.
- **Migration Risk:** Medium-High (requires updating `install.sh`, `uninstall.sh`, CI workflows, and verifying ~1,000 unit tests for import regressions).

### Option C: Two-Tier State / Runner Partition (Recommended Next Step)
- **Design:** Split only the pure state reducer into `debate_state.py` while keeping execution in `debate_orchestrator.py`.
- **Pros:** Clean test seam for state progression with minimal file proliferation and zero breaking changes to public facade contracts.
- **Cons:** Does not eliminate all ~2,000 lines of execution logic in `debate_orchestrator.py`.
- **Migration Risk:** Low.

## Suggested Approach
Evaluate during the next architectural sprint when Spec 0008 is scheduled. Keep public signatures on `advisory_consultation.py` 100% frozen.
