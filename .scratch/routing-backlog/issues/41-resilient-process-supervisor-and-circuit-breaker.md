# 41 — Resilient Process Supervisor & Circuit Breaker for Worker CLIs

**GitHub Issue:** [#17](https://github.com/liorparente/antigravity-auto-routing/issues/17)

* **Category:** Reliability & Resilience
* **Priority:** High (Recommended Step 1)
* **Status:** done

## Problem Statement
When running external worker CLIs (`claude -p`, `codex exec`, `agy -p`), transient network latency, API rate limits, or hung processes can block execution or leave zombie processes in the process table.

## Acceptance Criteria
- [x] Add a `ProcessSupervisor` in `skills/worker-routing/debate_transport.py` that terminates entire process groups (`os.killpg`) on timeout.
- [x] Implement a **Circuit Breaker** state machine: if a specific provider fails 3 times consecutively, mark it degraded/unhealthy for a cooldown period (e.g., 60 seconds).
- [x] Fallback seamlessly to the next eligible worker in the fallback chain without stalling the orchestrator.
- [x] Log consecutive worker failures to `ERRORS.md` automatically.
- [x] Add unit tests in `skills/worker-routing/test_debate_transport.py` verifying timeout termination and circuit breaker transitions.

## Implementation Notes
- `ProcessSupervisor` (`skills/worker-routing/debate_transport.py`) is a `Runner`-compatible callable that spawns workers with `start_new_session=True` on POSIX and, on `TimeoutExpired`, escalates `os.killpg(pgid, SIGTERM)` then `SIGKILL` against the whole process group, with `ProcessLookupError` guarded at every step. It is injectable as `DebateTransport(runner=ProcessSupervisor())` or standalone.
- `CircuitBreaker` (`skills/worker-routing/debate_transport.py`) is a thread-safe, per-model `CLOSED -> OPEN -> HALF_OPEN` state machine (default: 3 consecutive failures, 60s cooldown), exposing `record_success`, `record_failure`, `is_available`, and `get_status`/`state`.
- `DebateTransport` now owns a `CircuitBreaker` by default; `invoke_worker` rejects a circuit-broken model before spawning a process (`CircuitBreakerOpenError`, a `RuntimeError` subclass), and `invoke_critic_safe` turns that into the existing safe-abstention path — a caller iterating a fallback chain sees a fast, explicit failure instead of stalling. `RecurringFailureNotifier`'s existing `ERRORS.md` logging is unchanged and still fires independently on repeated failures.
- New tests in `skills/worker-routing/test_debate_transport.py`: `ProcessSupervisorTests` (mocked SIGTERM/SIGKILL escalation, `ProcessLookupError` guards, and a real end-to-end subprocess test proving a grandchild process is also killed), `CircuitBreakerTests` (threshold, cooldown, half-open probe, thread-safety), and `DebateTransportCircuitBreakerTests` (integration + backwards-compatibility with the default breaker).
- Verified: `python3 skills/worker-routing/test_debate_transport.py` (23/23), the full CI `PYTHON_TESTS` list (zero regressions), `mypy --config-file pyproject.toml worker_routing/` (42 files, clean), and `ruff check skills/worker-routing/` (clean).