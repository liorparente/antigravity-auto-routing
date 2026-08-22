# 02 — Declarative Role & Provider Schema in routing-config.json

**What to build:** Restructure skills/worker-routing/routing-config.json to declare abstract roles with capability requirements (reasoning_tier, tool_access, min_context, local_only) and decoupled providers, while retaining backward compatibility for legacy callers.

**Blocked by:** 01 — Architecture Contracts ADR & Domain Glossary Alignment

**Status:** ready-for-agent

- [ ] Add roles dictionary declaring planner, builder_heavy, builder_light, reviewer_architecture, reviewer_risk, reviewer_maintainability, reviewer_security, and adjudicator.
- [ ] Add providers dictionary mapping logical provider IDs to adapters, model IDs, and default reasoning efforts.
- [ ] Add council_policy section with fast_path_enabled, quorum_threshold: 0.60, and perspective weights.
- [ ] Validate JSON syntax and retain existing legacy keys needed during transition.
