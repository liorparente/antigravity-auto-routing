# 42 — Strongly-Typed Centralized Configuration Manager

* **Category:** Maintainability & Developer Experience
* **Priority:** Medium (Recommended Step 2)
* **Status:** open

## Problem Statement
Routing and council policies are parsed from loose JSON files (`routing-config.json`, `council-policy.json`) in multiple disparate locations. Missing fields or schema drifts can cause runtime crashes mid-task.

## Acceptance Criteria
- [ ] Create `skills/worker-routing/routing_config.py` with typed models (`dataclasses` / strict validation) for all config keys.
- [ ] Implement early schema validation at module load time with descriptive error messages in clear language.
- [ ] Provide immutable fallback defaults for all optional parameters (timeouts, degradation thresholds, quorum).
- [ ] Unify config consumers across `routing_check.py`, `debate_orchestrator.py`, and `council_review.py`.
- [ ] Add comprehensive unit tests in `test_routing_config.py`.
