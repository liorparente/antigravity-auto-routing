# 02 — Dynamic Model Registry, Effort Escalation & Fail-Safe Recovery

**What to build:** Centralized configuration engine (`routing_config.json`) supporting complete model and reasoning effort flexibility (`low`, `medium`, `high`, `ultra`), an autonomous escalation protocol that upgrades effort levels and model tiers after 2 consecutive task failures, and a fail-safe fallback mechanism for local LM Studio models that emits clear, interactive recovery prompts during network or model failures.

**Blocked by:** 01 — Cross-Harness Permission Alignment & Unified Setup (`install.sh`)

**Status:** ready-for-agent

- [ ] `routing_config.json` schema supports dynamic mapping of task complexity to model providers and calibrated reasoning effort tiers
- [ ] Task execution engine tracks attempt counters and automatically escalates effort (`medium` -> `high`/`ultra`) and model tier upon 2 consecutive failures
- [ ] Fail-safe controller attempts alternative local models (e.g. Gemma 4) when primary local LM Studio model fails
- [ ] Interactive error prompt schema is emitted when all local fallbacks are exhausted, offering actionable recovery choices
- [ ] Full unit test coverage in `test_routing.py` verifying model lookup, effort escalation state transitions, and fallback handling
