# 45 — Live Model Catalog & CLI Provider Capability Audit

**GitHub Issue:** [#21](https://github.com/liorparente/antigravity-auto-routing/issues/21)

**What to build:** An authoritative audit of real, callable models and reasoning effort parameters accepted by installed CLI tools (`claude`, `codex`, `agy`, and local LM Studio), establishing the genuine model catalog for the project.

**Blocked by:** None — can start immediately.

**Recommended Worker:** Tier 0 (LM Studio Local $0) / Tier 1 (Fast Flash)

**Status:** done

- [x] Audit actual supported model identifiers from installed CLI providers (`claude`, `codex`, `agy`, LM Studio).
- [x] Establish exact wire CLI model flags and reasoning effort parameters (`--effort`, `model_reasoning_effort`).
- [x] Produce `skills/worker-routing/probe_models.py` to probe live model availability dynamically.
- [x] Map human-readable display labels to exact CLI wire identifiers.

**Delivered:** [`skills/worker-routing/probe_models.py`](../../../skills/worker-routing/probe_models.py)
(`PROVIDER_CLI_CONTRACTS`, `AUDITED_MODEL_CATALOG`, `DISPLAY_LABEL_TO_MODEL_ID`,
`probe_lm_studio` / `probe_cli_provider` / `probe_all`, `audit_config_drift`),
tests in `test_probe_models.py`, and the written audit in
[`docs/research/live-model-catalog-audit.md`](../../../docs/research/live-model-catalog-audit.md).

Six drift findings (F1–F6) were recorded and deliberately left unfixed: correcting
them changes which model a role dispatches to, which belongs with ticket 46.
Three of them (F2, F4, F5) are machine-checked by `audit_config_drift()` and
pinned as five concrete findings in `test_probe_models.py`.

**Caller (Golden Rule 20):** the only shipped caller today is this module's own
`--audit` CLI entry point. The production consumer is ticket 46
(`get_role_matrix_view_data`, `MODEL_CAPABILITIES_REGISTRY`), which the backlog
already declares *blocked by 45*. Until 46 lands, `production_invoker`'s
`MODEL_ALIASES` / `CODEX_MODELS` / `AGY_MODELS` remain the live routing tables
and this module duplicates part of their knowledge.
