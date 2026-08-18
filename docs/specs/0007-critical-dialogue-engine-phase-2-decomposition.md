# Spec 0007 — Critical Dialogue Engine Phase 2 Decomposition: Pure Prompt Assembly, Debate State Machine, and Executive Budget Alerts

* Status: implemented (follow-up tracked in .scratch/routing-backlog/issues/31-debate-orchestrator-modular-decomposition.md)
* Date: 2026-08-18
* Related: Spec 0001 (Advisory Consultation), Spec 0003 (Critical Dialogue), Spec 0004 (Learning Loop), Spec 0006 (Advisory Consultation Decomposition Phase 1), ADR 0001, ADR 0004, ADR 0007, ADR 0010
* Glossary: **CriticalDialogue**, **AdvisoryConsultation**, **VerdictContract**, **DegradationLadder**, **ConsultationTranscript**, **AdvisoryTelemetryRecord**, **TaskIdentity**, **AdvisoryStalemateReport** (`CONTEXT.md`)

## Problem Statement

While Phase 1 (Spec 0006) successfully extracted `dialogue_contracts.py`, `dialogue_degradation.py`, and `dialogue_transcript.py`, `advisory_consultation.py` remains an oversized monolith exceeding 3,000 lines of code. This residual complexity creates critical operational and architectural friction:

1. **Entangled Prompt Assembly & Security Guards**: Prompt construction logic, role instructions (Planner vs Critic vs Adjudicator), canary injection markers, and anti-injection sanitization are tightly coupled to process execution and filesystem locks.
2. **Entangled Debate State Progression**: The multi-round debate state machine (single pair vs multi-critic panel, consensus calculation, stalemate resolution generation) is interspersed with outcome recording into `learning_outcomes.py` and low-level subprocess invocations.
3. **Lack of Executive Visibility & Budget Control**: When the system enters degraded budget rungs (rungs 1-3), the degradation happens silently in background telemetry without a prominent alert allowing human operators/CEOs to decide whether to continue or pause.
4. **Residual Legacy Code**: Unused experimental helpers and legacy log formatting branches from earlier protocol iterations clutter the codebase and increase cognitive load.

## Solution

Complete the decomposition of `advisory_consultation.py` into deep, cohesive modules with single responsibilities, reducing `advisory_consultation.py` to a clean <300 line facade:

1. **Prompt Assembler (`prompt_assembler.py`)**: A pure, stateless module responsible for constructing planner/critic prompts, canary markers, anti-injection framing, and role envelopes.
2. **Debate Orchestrator (`debate_orchestrator.py`)**: An isolated state machine governing round execution, multi-critic consensus quorum, engagement evaluation, and stalemate escalation.
3. **Sensitivity Redactor (`sensitivity_redactor.py`)**: Dedicated module enforcing privacy boundaries, scanning sensitivity markers, and deriving random `TaskIdentity` tokens for halted tasks.
4. **Executive Dialogue Reporter & Budget Guard (`executive_dialogue_report.py`)**:
   - Generates a concise 3-line executive summary (cost, consensus status, top model).
   - **Prominent Budget Alert**: Emits a prominent, actionable alert whenever a dialogue enters budget degradation rungs (1-3), giving the operator an explicit choice to proceed or pause.
5. **Ultra-Slim Facade (`advisory_consultation.py`)**: Retains 100% backward-compatible public exports (`run_advisory_consultation_debate`, types, constants) by delegating to the specialized deep modules.

## User Stories

1. As an operator/CEO, I want a concise 3-line summary after every critical dialogue (cost, consensus, recommended plan), so that I have instant visibility into model decisions.
2. As an operator/CEO, I want a prominent, unmistakable alert when dialogue budget thresholds are exceeded and the system degrades, so that I can explicitly decide whether to approve further model spend or halt.
3. As a developer writing unit tests, I want prompt assembly to be pure functions that take data and return strings, so that prompt formatting can be tested in milliseconds without mocking subprocesses.
4. As an architect, I want the debate state machine to be an isolated reducer/state machine, so that round transitions and consensus logic can be verified exhaustively against all edge cases.
5. As a security auditor, I want sensitivity detection and redaction to be isolated behind a strict boundary, guaranteeing zero leakage of sensitive prompts into telemetry or logs.
6. As an autonomous worker, I want `advisory_consultation.py` to be a slim facade under 300 lines with clear modular imports, so that code navigation and modifications have zero friction.
7. As a quality engineer, I want all 956 existing tests to pass without regression, ensuring seamless backward compatibility across the entire auto-routing ecosystem.

## Implementation Decisions

1. **Module Topography**:
   - `skills/worker-routing/prompt_assembler.py`: Pure functions (`build_planner_prompt`, `build_critic_prompt`, `build_stalemate_prompt`).
   - `skills/worker-routing/debate_orchestrator.py`: `DebateStateMachine`, `evaluate_round_consensus`, `build_stalemate_report`.
   - `skills/worker-routing/sensitivity_redactor.py`: `scan_sensitivity_markers`, `derive_safe_task_identity`.
   - `skills/worker-routing/executive_dialogue_report.py`: `render_executive_summary`, `format_budget_degradation_alert`.
   - `skills/worker-routing/advisory_consultation.py`: Slim facade coordinating the sub-modules and re-exporting legacy symbols.

2. **Executive Budget Alert Protocol**:
   - When `resolve_degradation_rung` returns rung >= 1, `executive_dialogue_report.py` formats a high-visibility warning block:
     ```
     ⚠️ [BUDGET DEGRADATION ALERT - Rung {rung}: {label}]
     Session dialogue spend has exceeded cap ({count}/{cap}).
     Reduced debate depth active. Operator action required: [CONTINUE | PAUSE].
     ```

3. **Pure Function Interfaces**:
   - `prompt_assembler.py` contains zero I/O, zero network, zero process execution.
   - All state transitions in `debate_orchestrator.py` take current state + round result and return new state.

4. **Zero-Regression Migration Slices**:
   - **Slice 1**: Create `prompt_assembler.py` and `sensitivity_redactor.py` with dedicated unit tests.
   - **Slice 2**: Create `debate_orchestrator.py` and `executive_dialogue_report.py` with dedicated unit tests.
   - **Slice 3**: Wire `advisory_consultation.py` to delegate to new modules while maintaining identical public signatures.
   - **Slice 4**: Run full test suite (all 956 tests) and verify zero regressions.

## Testing Decisions

1. **Unit Tests for Pure Modules**:
   - `test_prompt_assembler.py`: Test all prompt templates, canary injections, and escaping.
   - `test_debate_orchestrator.py`: Test 1-pair debate, 3-critic panel, consensus thresholds, and stalemate generation.
   - `test_executive_dialogue_report.py`: Test 3-line executive summaries and degradation alert generation.
2. **Full Regression Suite**:
   - Run `python3 -m unittest discover skills/worker-routing/` to ensure all existing tests pass cleanly.

## Out of Scope

- Modifying the public signatures of `run_advisory_consultation_debate`.
- Changing the HMAC signature scheme in `agent_council.py`.
- Altering the Learning Journal JSONL schema in `learning_journal.py`.

## Further Notes

- Approved by CEO during `/grill-me` session on 2026-08-18.
- Validated via Council Review heuristics for Deep Modules and Anti-Bloat standards.
