# 01 — Centralize Consultation Policy in `routing-config.json`

**What to build:** Centralize all council policy definitions (weighting, consensus rules, quorum thresholds, veto severities, round deadlines) from `council-policy.json` into `routing-config.json` under `"consultation_policy"`, with resilient fallback defaults and schema validation.

**Blocked by:** None — can start immediately.

**Status:** done

- [x] Add `"consultation_policy"` section to `skills/worker-routing/routing-config.json`.
- [x] Implement config helper to load consultation policy with safe defaults if keys are missing.
- [x] Verify `routing_check.py` and `test_routing.py` pass with zero regression.
