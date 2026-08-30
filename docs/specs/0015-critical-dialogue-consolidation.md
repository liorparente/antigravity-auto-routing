# Spec 0015: CriticalDialogue Consolidation and Shallow Facade Elimination

## Problem Statement

When an orchestrating AI agent or a developer seeks to deliberate ambiguous task complexity, review implementation plans, audit pull request diffs, or execute multi-agent council reviews, the codebase presents multiple competing, shallow entry points.

Historically, the system evolved from early binary advisory debates into a multi-perspective critical dialogue and council review architecture. However, during this evolution, legacy pass-through facades were preserved:
- A legacy consultation facade (`advisory_consultation.py`, 289 lines) that does not contain debate logic, but merely imports from eight sibling modules and re-exports over 60 aliased symbols.
- A legacy council review facade (`council_review.py`, 29 lines) that manipulates Python module lookup paths (`sys.path.insert`) at runtime to re-export council review interfaces.
- A legacy decision council module (`agent_council.py`) that duplicates security regex patterns and sensitivity checking logic rather than delegating to the dedicated sensitivity redactor leaf module.

These shallow wrappers create architectural friction, cause AI navigability confusion, leak internal module seams, and increase maintenance overhead without providing depth or leverage.

## Solution

Consolidate all advisory debate, critical dialogue, and multi-model council review capabilities into a single, cohesive, deep module (`CriticalDialogue`).

This deep module presents a compact, high-leverage interface for all dialogue occasions (ambiguity deliberation, plan review, code review, post-mortems, and council panel reviews), while hiding internal state machine transitions, verdict quote verifications, transcript rendering, and budget degradation rungs behind its seam.

All shallow pass-through facades are cleanly deleted following a test-driven migration of all internal callers and test suites, and legacy security pattern duplications are removed by delegating strictly to the dedicated sensitivity redactor module.

## User Stories

1. As an orchestrator agent, I want to invoke a single `run_critical_dialogue` interface for all deliberation occasions, so that I do not need to choose between multiple fragmented facades.
2. As an orchestrator agent, I want to request multi-model council reviews through a cohesive `request_council_review` interface, so that perspective evaluations and quorum reductions happen seamlessly behind one seam.
3. As a developer navigating the codebase, I want to find all debate orchestration logic in one deep module, so that I do not have to trace 60+ re-exported alias chains across multiple wrapper files.
4. As a test writer, I want to test dialogue workflows against a genuine execution seam, so that tests verify real system behavior without patching intermediate pass-through facades.
5. As a maintainer, I want all sensitivity detection and safe task identity derivation to live exclusively in the dedicated sensitivity redactor module, so that security patterns cannot drift between modules.
6. As an audit tool, I want consultation transcripts and telemetry records to be written deterministically across all dialogue occasions, so that the frozen audit and learning stream contracts remain 100% compliant.
7. As a council reviewer agent, I want structured perspective prompts and verdict parsing contracts to be validated uniformly, so that invalid votes or rubber-stamping approvals fail closed.
8. As an orchestrator executing in budget-constrained sessions, I want degradation ladder transitions (round reductions, cheaper rosters, or budget skips) to apply consistently across all dialogue types, so that token spend limits are strictly enforced.
9. As a developer running unit test suites, I want all tests to pass cleanly without runtime `sys.path` workarounds, so that the package conforms to standard Python packaging norms.
10. As a learner worker ingesting session outcomes, I want plan outcomes and stalemate resolutions to be journaled cleanly from the unified dialogue seam, so that institutional learning metrics stay accurate.

## Implementation Decisions

### Decision 1 — Unified Deep Module Architecture
The consultation engine, debate state machine, and council review capabilities will be unified into a single deep module:
- The module presents a small public surface: `run_critical_dialogue`, `request_council_review`, and `run_canary_dialogue`.
- The module internally encapsulates debate round execution, turn-taking between Planner and Critic models, perspective reviewer fan-out, quorum reduction, HMAC manifest signing, transcript generation, and telemetry logging.
- Internal dependencies (state machine, contracts, transcripts, degradation ladder) are treated as internal mechanics rather than exposed as public pass-through aliases.

### Decision 2 — Clean Deletion of Shallow Facades
In strict alignment with the Deletion Test:
- The legacy pass-through consultation facade (`advisory_consultation.py`) and the legacy council facade (`council_review.py`) will be completely deleted once all imports across the repository are updated.
- The root package exports (`worker-routing/__init__.py`) will bind directly to the unified deep module, preserving external library compatibility while eliminating internal shallow layers.

### Decision 3 — Single-Source Sensitivity Detection
- The legacy decision council module (`agent_council.py`) will remove its duplicated sensitive patterns and pattern-matching helper functions.
- All sensitivity scans and safe task identity derivations will be routed directly to the pure leaf module `sensitivity_redactor`.

### Decision 4 — Structural Invariants & Backward Compatibility
- Canonical task identity rules, random default generation on sensitivity halts, and non-prose learning journal constraints remain strictly invariant.
- All four dialogue occasions (`ambiguity`, `plan-review`, `code-review`, `post-mortem`) continue to support both binary Dyad debates and multi-agent Council panels.

## Testing Decisions

### Test Boundary and Seams
- Tests must verify external behavior through the public dialogue interface, rather than asserting on internal pass-through mappings.
- The primary test seam remains the injected worker runner callable (`(model, effort, prompt) -> str` / async equivalent), allowing 100% offline, deterministic testing without live subprocesses or network access.

### Modules Tested
- The unified critical dialogue module will be comprehensively tested for:
  - Binary planner-critic consensus and multi-round revision loops.
  - Multi-perspective council reviews with weighted quorum and unilateral security vetoes.
  - Sensitivity halt detection and canary verification.
  - Degradation ladder budget spend thresholds (rungs 0 through 3).
  - Transcript rendering and append-only telemetry emission.
- Package export tests will verify that all canonical symbols are exported cleanly from the root package `__init__.py`.

### Prior Art
- `test_debate_orchestrator.py` and `test_council_review.py` provide the authoritative baseline of over 1,700 passing assertions covering all round transitions, verdict parsers, and quorum calculations.

## Out of Scope

- Modifying the underlying foundation model CLI wrappers or adding new cloud provider SDKs.
- Altering the frozen schema of `routing_telemetry.jsonl` or `learning_journal.jsonl`.
- Redesigning the learning scoreboard arithmetic or weekly report dashboard.

## Further Notes

- The completion of this specification satisfies the recommendations of Architectural Review Candidate 1 and clears the council review conditions established in ADR 0015.
