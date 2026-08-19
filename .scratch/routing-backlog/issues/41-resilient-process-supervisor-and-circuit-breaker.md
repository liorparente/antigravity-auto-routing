# 41 — Resilient Process Supervisor & Circuit Breaker for Worker CLIs

* **Category:** Reliability & Resilience
* **Priority:** High (Recommended Step 1)
* **Status:** open

## Problem Statement
When running external worker CLIs (`claude -p`, `codex exec`, `agy -p`), transient network latency, API rate limits, or hung processes can block execution or leave zombie processes in the process table.

## Acceptance Criteria
- [ ] Add a `ProcessSupervisor` in `skills/worker-routing/debate_transport.py` that terminates entire process groups (`os.killpg`) on timeout.
- [ ] Implement a **Circuit Breaker** state machine: if a specific provider fails 3 times consecutively, mark it degraded/unhealthy for a cooldown period (e.g., 60 seconds).
- [ ] Fallback seamlessly to the next eligible worker in the fallback chain without stalling the orchestrator.
- [ ] Log consecutive worker failures to `ERRORS.md` automatically.
- [ ] Add unit tests in `skills/worker-routing/test_debate_transport.py` verifying timeout termination and circuit breaker transitions.
