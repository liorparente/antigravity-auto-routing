# 03 — Universal Security Veto & Selective HMAC Manifest Signing in `debate_orchestrator.py`

**What to build:** Implement universal `SecurityVetoHandler` that halts debate immediately upon detecting critical/high severity findings in both `Dyad` and `CouncilPanel` modes, and selectively writes HMAC-signed manifests to `.ralph/` when in `CouncilPanel` mode.

**Blocked by:** 02 — Council Panel Reducer & Multi-Critic Weighted Quorum in `debate_state_machine.py`

**Status:** ready-for-agent

- [ ] Wire `SecurityVetoHandler` across both `Dyad` and `CouncilPanel` execution paths.
- [ ] Implement selective HMAC manifest signing in `.ralph/council-manifest-{run_id}.json` for `CouncilPanel` runs only.
- [ ] Add unit tests in `skills/worker-routing/test_debate_orchestrator.py` verifying security halt and manifest generation.
