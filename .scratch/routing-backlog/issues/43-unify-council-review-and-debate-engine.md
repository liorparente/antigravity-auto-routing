# 43 — Unify Council Review & Critical Dialogue Engine

* **Category:** Core Architecture & Deep Modules
* **Priority:** High (Recommended Step 3)
* **Status:** done
* **Spec:** [docs/specs/0009-unified-consultation-and-council-engine.md](file:///Users/liorparente/Projects/auto-routing/docs/specs/0009-unified-consultation-and-council-engine.md)
* **Implementation Plan:** [implementation_plan.md](file:///Users/liorparente/.gemini/antigravity/brain/8bc8d27e-5072-48cf-b2ff-5d7543c0e153/implementation_plan.md)

## Problem Statement
`skills/council-review/scripts/council_review.py` and `skills/worker-routing/debate_orchestrator.py` implement overlapping 3-round multi-agent consultation loops, duplicate adapters, and HMAC signature schemes.

## Agreed Decisions (Grilling Protocol Resolution)
1. **Thin Facade Backward Compatibility:** Maintain `skills/council-review/scripts/council_review.py` as a slim delegation wrapper (<25 lines) preserving all public classes/methods.
2. **Dynamic Topology by Complexity:**
   - `Dyad` for `Medium` complexity tasks (1 Planner vs 1 Critic).
   - `CouncilPanel` for `Complex` tasks (1 Planner + N Critics + Adjudicator with weighted quorum).
3. **Unified Configuration:** Merge council settings into `routing-config.json` under `"consultation_policy"`.
4. **Selective Manifest Signing:** Emit HMAC-signed manifests (`.ralph/council-manifest-*.json`) only for `CouncilPanel` runs.
5. **Universal Security Veto:** Trigger immediate `Security Halt` in both `Dyad` and `CouncilPanel` topologies when a critical vulnerability is detected.

## Acceptance Criteria
- [x] Refactor `debate_orchestrator.py` and `debate_state_machine.py` to support `Dyad` and `CouncilPanel` topologies.
- [x] Implement selective HMAC manifest signing and universal `SecurityVetoHandler`.
- [x] Migrate council configuration parameters to `routing-config.json`.
- [x] Reduce `skills/council-review/scripts/council_review.py` to a thin delegator.
- [x] Pass all 1,010 existing unit tests and council review tests with zero regression.
