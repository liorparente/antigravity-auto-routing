# 42 — Strongly-Typed Centralized Configuration Manager

**GitHub Issue:** [#18](https://github.com/liorparente/antigravity-auto-routing/issues/18)

* **Category:** Maintainability & Developer Experience
* **Priority:** Medium (Recommended Step 2)
* **Status:** done

## Problem Statement
Routing and council policies are parsed from loose JSON files (`routing-config.json`, `council-policy.json`) in multiple disparate locations. Missing fields or schema drifts can cause runtime crashes mid-task.

## Acceptance Criteria
- [x] Create `skills/worker-routing/routing_config.py` with typed models (`dataclasses` / strict validation) for all config keys.
- [x] Implement early schema validation at module load time with descriptive error messages in clear language.
- [x] Provide immutable fallback defaults for all optional parameters (timeouts, degradation thresholds, quorum).
- [x] Unify config consumers across `routing_check.py`, `debate_orchestrator.py`, and `council_review.py`.
- [x] Add comprehensive unit tests in `test_routing_config.py`.

## Resolution
Implemented per the approved plan: `routing_config.py` now owns every `dataclass(frozen=True)` model
(`CapabilityRequirements`, `ProviderConfig`, `RoleConfig`, `LegacyRoleConfig`, `SecurityVetoConfig`,
`CouncilPolicyConfig`, `ConsultationProviderEntry`, `ConsultationWeightingConfig`,
`ConsultationPolicyConfig`, `CriticalDialogueConfig`, `RosterTopologyConfig`, `CanaryCadenceConfig`,
`DialogueBudgetConfig`, `AcceptanceGateConfig`, `RoutingConfig`), the `ConfigError` /
`ConfigFileNotFoundError` / `ConfigParseError` / `ConfigValidationError` hierarchy, an immutable
`DEFAULT_ROUTING_CONFIG`, and the `load_routing_config`/`parse_routing_config`/
`get_default_routing_config` public API. The module validates the checked-in `routing-config.json`
at import time (fail-closed, per dotted key path).

`production_invoker.py`, `consultation_policy.py`, `dialogue_degradation.py`,
`debate_orchestrator.py`, and `routing_check.py` all now delegate their file reads to
`routing_config`; `council_review.py` inherits the same typed loading transitively through
`consultation_policy.load_consultation_policy` (its only config-reading path), satisfying the
`council_review.py` unification criterion without a direct edit to that file. Each wiring preserved
its consumer's existing lenient/partial-field fallback semantics and exact return shapes — verified
against the full pre-existing test suite (`test_suite.py`, 1372 tests, all green) plus 36 new tests in
`test_routing_config.py`. `routing_config.py` was added to `install.sh`'s `MANAGED_FILES`,
`uninstall.sh`'s `INSTALLED_FILES`, and CI's `PYTHON_MODULES`/`PYTHON_TESTS` lists.