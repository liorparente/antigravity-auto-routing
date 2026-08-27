# 46 — Model Capability Registry & Schema Contracts

**GitHub Issue:** [#22](https://github.com/liorparente/antigravity-auto-routing/issues/22)

**What to build:** A strongly-typed model capability schema and registry in `routing_config.py` mapping every model to its supported reasoning efforts, default effort, reasoning tier, and context window.

**Blocked by:** 45 — Live Model Catalog & CLI Provider Capability Audit

**Recommended Worker:** Tier 0 (LM Studio Local $0) / Tier 1 (Fast Flash)

**Status:** done

- [x] Define `ModelCapability` dataclass with `supported_efforts`, `default_effort`, `tier`, `context`, and `provider`.
- [x] Implement `MODEL_CAPABILITIES_REGISTRY` containing audited model entries. *(as a builder function — see below)*
- [x] Expose `get_role_matrix_view_data(config)` returning validated role records with model defaults.
- [x] Add unit tests verifying schema validation and fail-closed contracts.

## Delivered

`636649b`, with 25 findings closed across eight fix-review rounds in `d19a77f`.

Verified against the code rather than the commit message: `ModelCapability`
carries all five named fields (plus `model_id` and `local_only`), the
registry holds 28 audited entries, `get_role_matrix_view_data` resolves all
9 roles, and `test_routing_config.py` runs 60 tests.

**One deviation.** There is no module-level `MODEL_CAPABILITIES_REGISTRY`
constant; the registry is built by `build_model_capabilities_registry()`.
That is deliberate and load-bearing, not an oversight: `probe_models`
imports `routing_config` eagerly at its own top level, so a bare
module-level read of `AUDITED_MODEL_CATALOG` breaks whenever `probe_models`
starts the import chain, and a bare top-level `import probe_models` breaks
the opposite order. Both directions are pinned by subprocess-isolated tests
(`test_importing_probe_models_before_routing_config_does_not_raise` and its
mirror). Deferring the read behind a call avoids both.