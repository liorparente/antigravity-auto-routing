# 03 — Dynamic Role Resolution & Preference Fallback in production_invoker.py

**What to build:** Implement a RoleResolver class in skills/worker-routing/production_invoker.py that resolves abstract role requests to concrete CLI/HTTP commands using routing-config.json, with preference fallback and fail-closed local execution.

**Blocked by:** 02 — Declarative Role & Provider Schema in routing-config.json

**Status:** ready-for-agent

- [ ] Implement RoleResolver to parse roles and providers from routing-config.json.
- [ ] Refactor build_worker_command to accept either an explicit model string or an abstract role identifier.
- [ ] Implement preference fallback when a primary provider is unavailable or over quota.
- [ ] Enforce strict fail-closed behavior for sensitive_executor when local LM Studio endpoint is offline.
- [ ] Add comprehensive unit tests in skills/worker-routing/test_production_invoker.py and verify 100% pass.
