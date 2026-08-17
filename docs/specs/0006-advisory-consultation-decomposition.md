# Spec 0006 — Advisory Consultation Decomposition: Deep Modules for Dialogue Contracts, Degradation Policy, and Redacted Transcripts

* Status: ready-for-agent
* Date: 2026-08-17
* Related: Spec 0001 (Advisory Consultation), Spec 0003 (Critical Dialogue), Spec 0004 (Learning Loop), Spec 0005 (Unified Worker Invocation), ADR 0001, ADR 0004, ADR 0007, ADR 0010
* Glossary: **AdvisoryConsultation**, **CriticalDialogue**, **VerdictContract**, **DegradationLadder**, **ConsultationTranscript**, **AdvisoryTelemetryRecord**, **TaskIdentity** (`CONTEXT.md`)

## Problem Statement

The advisory consultation and critical dialogue subsystem has grown into a monolithic module exceeding 4,000 lines of code. This structure causes several severe architectural frictions:

1. **Entangled Responsibilities (Lack of Locality)**: The single module concurrently manages six disparate concerns: prompt rendering, contract and quote parsing, session budget degradation calculations, state machine coordination across debate rounds, telemetry emission with sensitivity redactions, and canary fixture generation. A modification to prompt parsing risks introducing subtle regressions in telemetry formatting or budget calculations.
2. **Impaired Testability & Slow Feedback**: Unit testing text parsing or budget math currently requires instantiating the entire consultation workflow with mock subprocess callables, generating hundreds of lines of boilerplate and coupling unit tests to debate state machines.
3. **High Cognitive Load**: For human developers and autonomous agents navigating the codebase, understanding or updating any dialogue capability requires scanning a 4,000-line file with hundreds of intertwined private helper functions.
4. **Shallow Facade Over Complex Mechanics**: The main consultation entry point exposes a wide and brittle surface instead of delegating to deep, cohesive modules with minimal public interfaces.

## Solution

Decompose the monolithic consultation engine into three cohesive, deep modules centered around distinct business boundaries, unified under a narrow facade:

- **Dialogue Contracts Module (`VerdictContractParser`)**: A deep, pure text-parsing and validation module that extracts critic verdicts, validates quoted excerpts against source artifacts, verifies atomic objections, and enforces anti-rubber-stamping invariants without any network, subprocess, or state dependencies.
- **Dialogue Degradation Module (`DegradationPolicy`)**: A deterministic, side-effect-free budget and ladder calculation module that maps session dialogue counts to degradation rungs (full rounds, reduced rounds, degraded independence, budget skip) according to configuration thresholds.
- **Dialogue Transcript & Telemetry Module (`DialogueReporter`)**: A secure reporting module that formats human-readable consultation transcripts and appends structured telemetry records, strictly enforcing fail-closed redaction of sensitive prompts upon sensitivity halts.
- **Narrow Orchestrator Facade**: Retains the top-level public interface (`run_advisory_consultation_debate`), coordinating the round loop while delegating contract validation, degradation calculations, and transcript serialization to the deep sub-modules.
- **Full Backwards Compatibility**: Re-exports all public types, constants, and helper functions so that existing test suites and external callers continue to function without disruption.

## User Stories

1. As an orchestrator conducting a critical dialogue, I want critic verdicts and quoted evidence to be parsed and verified by a dedicated contract parser, so that malformed or unverified responses fail closed reliably before reaching debate state machines.
2. As a test author, I want to test quote verification and objection parsing directly in isolated unit tests with plain strings, so that test suites run in milliseconds without mocking multi-round worker subprocesses.
3. As a developer modifying the degradation ladder policy, I want budget rung calculations to be isolated in a pure module, so that adjusting session caps cannot inadvertently affect transcript formatting or debate state transitions.
4. As a security auditor, I want transcript generation and sensitivity redactions to be encapsulated in a dedicated reporting module, so that task prompts are guaranteed to never leak into telemetry or transcripts during sensitivity halts.
5. As an AI coding agent exploring the repository, I want each module to have a single, obvious responsibility under 500 lines of code, so that understanding and updating the codebase is fast, reliable, and low-friction.
6. As a council review coordinator, I want multi-critic panel consensus and canary validation to rely on a clean debate coordinator, so that deadlock detection and stalemate reporting remain structurally verifiable.
7. As a quality engineer, I want the refactored architecture to maintain 100% backward compatibility across all public interfaces and constants, so that all 900+ existing regression tests pass continuously without modification.
8. As a learning loop consumer, I want consultation telemetry and degradation rungs to be emitted consistently on every outcome (including skips and halts), so that downstream scoreboards receive accurate ground truths.
9. As a system operator, I want budget degradation state to be inspectable through a simple, pure query interface, so that dialogue cost ceilings can be monitored in real time.
10. As an offline runner, I want all decomposed modules to accept dependency-injected parameters (such as clock, root directory, and worker callables), so that integration testing remains 100% deterministic and hermetic.

## Implementation Decisions

1. **Decomposition Topology**:
   - Decompose into three deep peer modules residing in `skills/worker-routing/`:
     - `dialogue_contracts.py`: Implements `VerdictContractParser`, quote validation, atomic objection extraction, and contract result models.
     - `dialogue_degradation.py`: Implements `DegradationPolicy`, degradation rung resolution, and session budget state tracking.
     - `dialogue_transcript.py`: Implements transcript rendering, telemetry record building, and sensitivity halt redaction.
   - The primary file `advisory_consultation.py` remains the top-level facade and debate orchestrator, orchestrating the multi-round loop and re-exporting symbols for backward compatibility.

2. **Verdict Contract Model & Parser Interface**:
   - The contract parser takes the raw critic response, the source artifact string, and the expected format rules.
   - Returns a structured `VerdictContractResult` containing:
     - `verdict`: `"approved"`, `"revise"`, or `"unparseable"`.
     - `verified_quotes`: Sequence of extracted quotes that were confirmed to exist verbatim in the source artifact.
     - `objections`: Sequence of parsed atomic critique objections.
     - `is_valid_engagement`: Boolean confirming non-empty substantive critique prior to approval.

3. **Degradation Policy Interface**:
   - Exposes pure calculation functions mapping `(dialogue_count, session_cap)` to `(degradation_rung, max_rounds, is_degraded_independence, is_skipped)`.
   - Free of file I/O and mutable global state, ensuring deterministic evaluation.

4. **Transcript & Telemetry Interface**:
   - Exposes dedicated constructors for generating `ConsultationTranscript` and `AdvisoryTelemetryRecord`.
   - Centralizes the redaction rules: when an outcome is `sensitivity_halt`, all task text and prompt bodies are stripped, retaining only the matched sensitivity marker and random `TaskIdentity`.

5. **Migration & Slicing Strategy**:
   - Implement via **Vertical Slicing** with strict TDD:
     - **Slice 1**: Extract `dialogue_contracts.py` + verify full test suite green.
     - **Slice 2**: Extract `dialogue_degradation.py` + verify full test suite green.
     - **Slice 3**: Extract `dialogue_transcript.py` + verify full test suite green.
     - **Slice 4**: Clean up internal orchestration in `advisory_consultation.py` and run full lint, type-check, and multi-harness sync.

## Testing Decisions

1. **Behavior-Focused Tests**:
   - Tests must verify behavior through public module interfaces, avoiding assertions on private implementation details.
   - All text parsing tests must verify that invalid contracts, missing quotes, or truncated tokens fail closed with explicit diagnostic outcomes.
2. **Offline & Hermetic Execution**:
   - All tests run offline without network access or subprocess execution, relying on injected fake runners and temp directories.
3. **Zero-Regression Gate**:
   - All 911+ existing tests in `test_routing.py` and associated test suites must pass cleanly at every slice before proceeding to the next.

## Out of Scope

- Modifying the external API of `run_advisory_consultation_debate` or altering the shape of `AdvisoryDebateResult`.
- Changing the schema of `.ralph/routing_telemetry.jsonl` or altering the audited `AdvisoryTelemetryRecord` contract.
- Modifying prompt templates or LLM system instructions.
- Altering the 3-round debate protocol or stalemate resolution options.

## Further Notes

- This specification directly addresses the #1 architectural finding from the Council Review architecture evaluation.
- Following completion of this spec, subsequent work may unify worker execution adapters (Spec 0005) and consolidate learning loop facades.
