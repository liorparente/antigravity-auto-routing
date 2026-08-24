# 46 — Model Capability Registry & Schema Contracts

**What to build:** A strongly-typed model capability schema and registry in `routing_config.py` mapping every model to its supported reasoning efforts, default effort, reasoning tier, and context window.

**Blocked by:** 45 — Live Model Catalog & CLI Provider Capability Audit

**Recommended Worker:** Tier 0 (LM Studio Local $0) / Tier 1 (Fast Flash)

**Status:** ready-for-agent

- [ ] Define `ModelCapability` dataclass with `supported_efforts`, `default_effort`, `tier`, `context`, and `provider`.
- [ ] Implement `MODEL_CAPABILITIES_REGISTRY` containing audited model entries.
- [ ] Expose `get_role_matrix_view_data(config)` returning validated role records with model defaults.
- [ ] Add unit tests verifying schema validation and fail-closed contracts.
