# ADR 0014: Hybrid Local-Cloud Routing Profiles & Rate-Limit Optimization

## Status
Approved

## Context
Antigravity operates as an orchestrator across multiple foundation model providers (Anthropic, OpenAI, Google) and local hardware (Apple Silicon via LM Studio / MLX).

Previously, all worker roles dispatched to cloud models indiscriminately, incurring monthly costs of ~$300 ($100 per provider) and risking token rate limit exhaustion during intensive engineering sessions. The local workstation possesses strong local hardware capable of serving 27B–32B open-weight models (such as `qwen3.8-27b-mlx`) at zero incremental token cost.

Furthermore, changing routing configurations in `routing-config.json` lacked institutional memory and instant reversibility; there was no structured way to preserve the baseline configuration or switch between optimized presets (e.g. flat $60/mo budget vs. cloud-heavy vs. air-gapped).

## Decision
1. **Three-Tier Fixed Subscription Alignment ($60/mo Target):**
   - Standardize on flat $20/month tier subscriptions across providers (Anthropic, OpenAI, Google) to eliminate variable billing anxiety.
   - Shift the routing objective from per-token micropayment optimization to **Rate-Limit Insulation**: offloading ~70% of routine token volume (boilerplate, single-file edits, unit tests, formatting, secrets handling) to local Qwen 3.8 27B, thereby preserving cloud subscription quotas for high-leverage architectural tasks.

2. **Role Allocations in Hybrid Profile (`hybrid_local_60usd`):**
   - **`planner`:** Claude Opus 5 (Effort: `high`) — interface-first architectural design.
   - **`heavy_doer`:** Claude Sonnet 5 (Effort: `high`) — complex multi-file logic implementation.
   - **`context_specialist`:** Gemini 3.7 Flash (Effort: `high`) — whole-repository 2M token context scanning.
   - **`light_doer` / `builder_light`:** Qwen 3.8 27B (Local) with Codex Luna (Max Effort) as fallback.
   - **`sensitive_executor`:** Qwen 3.8 27B (Local) — strict fail-closed data privacy for `.env` and secrets.
   - **`adjudicator`:** Qwen 3.8 27B (Local) — zero-cost neutral referee for Council Review stalemates.
   - **`qa_auditor`:** Codex 5.6 Sol (Effort: `ultra`/`high`) — zero-defect uncommitted diff verification.

3. **Persistent Profile Management Architecture (`skills/worker-routing/profiles/`):**
   - Maintain immutable versioned profile snapshots:
     - `01_baseline_cloud_default.json` (Original configuration snapshot)
     - `02_hybrid_local_60usd.json` (Optimized hybrid configuration)
     - `03_airgapped_local_only.json` (100% offline local profile)
   - Implement `switch_profile.py` with atomic file replacement (`os.replace`) and fail-closed validation via `routing_config.parse_routing_config`.

## Consequences
- **Cost Reduction:** Monthly spending drops from ~$300/mo to a fixed $60/mo (3 × $20/mo).
- **Zero Rate-Limit Choke:** Cloud quotas remain available for critical planning and QA throughout long sprints.
- **Reversibility:** Any prior configuration state can be restored instantly via `python3 skills/worker-routing/switch_profile.py <profile>`.
- **Privacy Assurance:** Sensitive files and secrets remain strictly air-gapped on local silicon.
