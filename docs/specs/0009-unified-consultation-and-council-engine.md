# Spec 0009 — Unified Consultation and Council Engine: Topology-Driven Multi-Agent Debate, Universal Security Veto, and Facade Consolidation

* **Status:** done
* **Date:** 2026-08-19
* **Related:** Spec 0001 (Advisory Consultation), Spec 0003 (Critical Dialogue), Spec 0007 (Critical Dialogue Engine Phase 2 Decomposition), Spec 0008 (Debate Engine Modular Decomposition), ADR 0001, ADR 0004, ADR 0007, ADR 0010
* **Issue:** [Issue 43: Unify Council Review & Critical Dialogue Engine](file:///Users/liorparente/Projects/auto-routing/.scratch/routing-backlog/issues/43-unify-council-review-and-debate-engine.md)
* **Glossary:** **CriticalDialogue**, **AdvisoryConsultation**, **VerdictContract**, **DegradationLadder**, **ConsultationTranscript**, **AdvisoryTelemetryRecord**, **TaskIdentity**, **AdvisoryStalemateReport**, **SecurityContext** (`CONTEXT.md`)

---

## Problem Statement

The repository currently maintains two separate, semi-redundant multi-model consultation implementations:
1. `skills/council-review/scripts/council_review.py`: A 365-line standalone 3-round council review system featuring weighted scoring, quorum evaluation, security veto short-circuiting, and HMAC manifest generation.
2. `skills/worker-routing/debate_orchestrator.py` & `debate_state_machine.py`: A pure reducer state machine and critical dialogue engine supporting planner-critic consensus, transcript logging, telemetry emission, and budget degradation.

This separation causes architectural drift, duplicated subprocess transport logic, conflicting configuration files (`routing-config.json` vs. `council-policy.json`), and increased maintenance overhead across the two subsystems.

---

## Solution

Unify both engines into a single, cohesive, topology-driven **Consultation & Council Engine** centered in `skills/worker-routing/`. The unified engine supports two distinct execution topologies (`Dyad` and `CouncilPanel`) governed by task complexity, shares a single pure state machine and process transport layer, centralizes configuration, enforces a universal Security Veto across all debate modes, and preserves 100% backward compatibility via a slim delegator facade.

---

## User Stories

1. As an orchestrator assessing a `Medium` complexity task, I want to initiate a lightweight `Dyad` consultation (1 Planner vs. 1 Critic), so that I can quickly sanity-check plans without burning tokens on a full panel.
2. As an orchestrator planning a `Complex` architectural change, I want to spin up a full `CouncilPanel` consultation (1 Planner + N Critics + Adjudicator), so that multi-model weighted quorum and security vetting are rigorously enforced.
3. As a security officer / developer, I want a critical security vulnerability found by any reviewer (in either `Dyad` or `CouncilPanel` mode) to trigger an immediate, unilateral `Security Halt`, so that no insecure design or code modification proceeds to execution.
4. As an auditor, I want `CouncilPanel` sessions to generate a cryptographically signed HMAC manifest in `.ralph/`, so that council verdicts are verifiable and tamper-evident.
5. As an auditor, I want lightweight `Dyad` sessions to avoid emitting unnecessary HMAC manifests, so that repository I/O and artifact noise remain minimal.
6. As a maintainer configuring the system, I want all model weights, quorum thresholds, and consultation timeouts defined in a single `routing-config.json` under `"consultation_policy"`, so that I have a single source of truth for all routing and review parameters.
7. As an external tool or legacy script calling `ReviewCouncil.review()`, I want the legacy module to remain functional as a thin facade (<25 lines), so that all existing integrations and tests continue to run without modification.
8. As a developer running the full test suite, I want all 1,010 existing unit and integration tests across `skills/worker-routing/` and `skills/council-review/` to pass cleanly with zero regression.

---

## Implementation Decisions

### 1. Unified Topology Model
The debate engine recognizes two primary consultation topologies:
- **`Dyad`**: Binary turn-based exchange between a single Planner and a single Critic across up to 3 revision rounds.
- **`CouncilPanel`**: Parallel multi-reviewer round execution with weighted vote scoring, quorum threshold evaluation (`score >= quorum_threshold`), and candidate hash ratification.

### 2. Universal Security Veto
The `SecurityVetoHandler` runs unconditionally after every reviewer round in both `Dyad` and `CouncilPanel` modes. Any finding with severity matching configured veto severities (e.g. `critical`, `high`) and confidence meeting the security threshold immediately halts the consultation and transitions the debate state to `status = "security_halt"`.

### 3. Selective HMAC Manifest Emission
- When running in `CouncilPanel` mode, the engine resolves the workspace HMAC secret (`AGY_CALIBRATION_SECRET` or `.ralph/cache/calibration.key`) and writes `.ralph/council-manifest-{run_id}.json`.
- When running in `Dyad` mode, manifest generation is bypassed, and standard `ConsultationTranscript` + `AdvisoryTelemetryRecord` logging is retained.

### 4. Consolidated Configuration Schema
The contents of `skills/council-review/references/council-policy.json` (weighting, consensus policy, quorum threshold, security veto configuration, and round deadlines) are merged into `skills/worker-routing/routing-config.json` under the top-level key `"consultation_policy"`.

### 5. Slim Facade Architecture
`skills/council-review/scripts/council_review.py` is refactored into an ultra-thin wrapper importing and delegating to `skills.worker-routing.debate_orchestrator`, preserving public classes (`ReviewCouncil`, `ReviewRequest`, `ReviewOutcome`, `PrivacyMode`, `SecurityVeto`).

---

## Testing Decisions

### 1. Test Seams & Behavior Verification
- **High-Level Seam 1 (Council Facade):** Verify `ReviewCouncil.review()` produces identical `ReviewOutcome` objects for unanimous approvals, material disagreements, security vetos, and local-only privacy modes.
- **High-Level Seam 2 (Debate Orchestrator):** Verify `run_advisory_consultation_debate()` properly branches between `Dyad` and `CouncilPanel` modes based on task parameters.
- **Pure State Machine Seam:** Verify `advance_debate_state()` correctly reduces multi-critic votes and calculates weighted quorum deterministically without subprocess mocking.

### 2. Prior Art in Codebase
- `skills/council-review/tests/test_council_review.py` (12 tests)
- `skills/worker-routing/test_debate_state_machine.py`
- `skills/worker-routing/test_debate_transport.py`
- `skills/worker-routing/test_acceptance_gate.py`

---

## Out of Scope

- Modifying the underlying CLI adapters or PTY execution logic in `debate_transport.py`.
- Altering the `LearningJournal` schema or outcome recording protocols defined in Spec 0004.
- Creating the visual HTML reporting dashboard (deferred to Issue 44).

---

## Further Notes

All changes must be synchronized across `.agents/`, `.codex/`, and `~/.gemini/` by invoking `install.sh .` upon completion of implementation.
