# Spec 0008 — Debate Engine Modular Decomposition: Pure State Machine, Process Transport, and Contract Extraction

* Status: ready-for-agent
* Date: 2026-08-18
* Related: Spec 0001 (Advisory Consultation), Spec 0003 (Critical Dialogue), Spec 0006 (Advisory Consultation Decomposition Phase 1), Spec 0007 (Critical Dialogue Engine Phase 2 Decomposition), ADR 0001, ADR 0004, ADR 0007, ADR 0010
* Issue: `.scratch/routing-backlog/issues/31-debate-orchestrator-modular-decomposition.md`
* Glossary: **CriticalDialogue**, **AdvisoryConsultation**, **VerdictContract**, **DegradationLadder**, **ConsultationTranscript**, **AdvisoryTelemetryRecord**, **TaskIdentity**, **AdvisoryStalemateReport** (`CONTEXT.md`)

---

## Problem Statement

While Spec 0007 successfully established `debate_orchestrator.py` as the core orchestration engine and decomposed `advisory_consultation.py` into a thin facade, `debate_orchestrator.py` itself has grown into a 2,900-line monolith. This produces several architectural and maintainability challenges:

1. **Mixed Concerns**: Subprocess CLI invocations (I/O, timeouts, PTY wrappers) are tightly tangled with pure mathematical quorum calculations, state transitions, and round logic.
2. **Heavy Test Footprint**: Verifying simple state transitions or consensus quorum currently requires initializing full mock subprocess fixtures rather than passing pure immutable data structures.
3. **Transport Fragility**: Model timeout failures or unresponsive CLI processes lack an isolated graceful fallback path with persistent user alerting on repeated failures.
4. **Scattered Contracts & Formats**: Residual contract verification and transcript generation logic remain in `debate_orchestrator.py` rather than concentrating in the designated leaf modules (`dialogue_contracts.py` and `dialogue_transcript.py`).

---

## Solution

Decompose `debate_orchestrator.py` into four cohesive, decoupled layers adhering to the Deep Module design principles (`/codebase-design`) while preserving 100% backward-compatible public exports through an ultra-thin `debate_orchestrator.py` facade:

1. **Pure Debate State Machine (`debate_state_machine.py`)**: A completely stateless, deterministic reducer (`advance_debate_state`) and immutable state models (`DebateState`, `RoundResult`) containing zero I/O, zero network, and zero subprocess dependencies.
2. **Isolated Worker Transport (`debate_transport.py`)**: Dedicated subprocess executor encapsulating PTY wrapping, non-interactive stdin enforcement (`< /dev/null`), timeouts, and graceful abstain error handling with recurring failure alerts.
3. **Consolidated Contracts & Transcripts (`dialogue_contracts.py` & `dialogue_transcript.py`)**: Deepen existing leaf modules by moving remaining contract parsing and transcript serialization out of the orchestrator.
4. **Backward-Compatible Facade (`debate_orchestrator.py`)**: Re-exports all public functions, classes, and types so all 986 existing unit and integration tests run without modification.
5. **Universal Multi-Harness Sync (`install.sh` / `uninstall.sh`)**: Update installer scripts to generically synchronize all Python modules (`*.py`) across `.agents/`, `.codex/`, and `~/.gemini/`.

---

## User Stories

1. As an autonomous agent running tests, I want `debate_state_machine.py` to be a pure function taking immutable state objects and returning new state objects, so that 100% of round transition edge cases can be tested in under 5ms without subprocess mocking.
2. As an orchestrator executing a Critical Dialogue, I want `debate_transport.py` to handle worker timeouts by returning a graceful `abstain` result, so that a transient network hiccup does not abort the entire multi-model deliberation.
3. As a developer/operator, I want an immediate alert if the same worker model fails twice consecutively, so that I can intervene before silent degradation spreads.
4. As a code reviewer, I want all contract validation logic located strictly in `dialogue_contracts.py`, so that quote verification and verdict rules have a single source of truth.
5. As an auditor, I want all transcript formatting and markdown rendering isolated in `dialogue_transcript.py`, so that transcript layout changes never risk breaking debate execution logic.
6. As a maintainer running existing test suites, I want `debate_orchestrator.py` to re-export all legacy symbols identically, so that none of the 986 tests break upon refactoring.
7. As an installer script, I want `install.sh` and `uninstall.sh` to copy all `*.py` files in `skills/worker-routing/` dynamically, so that newly extracted modules are automatically deployed across Antigravity, Claude Code, and Codex without manual Bash script edits.

---

## Implementation Decisions

1. **State Machine Pure Reducer Interface**:
   ```python
   @dataclass(frozen=True)
   class DebateState:
       occasion: Occasion
       task_description: str
       task_id: str
       round_number: int
       max_rounds: int
       planner_proposals: tuple[str, ...]
       critic_responses: tuple[tuple[CriticResponse, ...], ...]
       status: Literal["in_progress", "consensus", "stalemate", "security_halt", "budget_skipped"]
       stalemate_report: AdvisoryStalemateReport | None = None

   def advance_debate_state(
       current: DebateState,
       round_turn: RoundTurnResult,
       quorum_policy: QuorumPolicy,
   ) -> DebateState:
       """Pure reducer returning updated DebateState with zero side-effects."""
   ```

2. **Transport Layer & Recurring Failure Boundary**:
   - `debate_transport.py` encapsulates subprocess execution with:
     - Forced `< /dev/null` stdin redirection.
     - Timeout watchdog timer.
     - Safe conversion of unhandled CLI exceptions to `CriticVerdict(vote="abstain", confidence=0.0, error=...)`.
     - `RecurringFailureNotifier` logging repeated model failures to `ERRORS.md` and emitting user-facing alerts.

3. **Leaf Module Deepening**:
   - `dialogue_contracts.py` gains full responsibility for quote verification, atomic objection parsing, and engagement unit validation.
   - `dialogue_transcript.py` gains all markdown formatting, executive summary layout, and sensitivity redaction rendering.

4. **Dynamic Installer Globbing**:
   - In `install.sh` and `uninstall.sh`, replace hardcoded lists with dynamic file globbing (`cp "$SRC_DIR"/*.py "$TARGET_DIR/"`).

---

## Testing Decisions

1. **Pure Unit Tests (`test_debate_state_machine.py`)**:
   - Test round 1 -> round 2 -> round 3 transitions.
   - Test unanimous approval, single-critic rejection, 3-critic panel split quorum, and stalemate escalation.
   - 100% offline, zero subprocess mocking.

2. **Transport Tests (`test_debate_transport.py`)**:
   - Test clean exit, non-zero error exit, timeout abort, and recurring failure alerting.

3. **Full Regression Verification**:
   - Run all 986 unit tests across `skills/worker-routing/`.
   - Run `ruff check`, `mypy`, and `shellcheck`.
   - Verify multi-harness sync via `./install.sh .`.

---

## Out of Scope

- Changing the underlying CLI worker prompt texts (owned by `prompt_assembler.py`).
- Changing the HMAC manifest cryptographic signing algorithm (owned by `agent_council.py`).
- Altering the Learning Journal JSONL schema (owned by `learning_journal.py`).

---

## Further Notes

- Maintains 100% compliance with ADR 0001, ADR 0004, ADR 0007, and ADR 0010.
- Public signatures on `advisory_consultation.py` remain frozen.
