# 01 — Worker Execution Result & Structured Output Extraction

**What to build:** Introduce the `WorkerExecutionResult` structured data type to `production_invoker.py` and implement deterministic extraction of raw output text, wall-clock execution duration in milliseconds, derived USD cost estimate, error diagnostics, and parsed structured review payloads (such as vote, confidence, and security findings) from worker stdout.

**Blocked by:** None — can start immediately

**Status:** ready-for-agent

- [ ] Define `WorkerExecutionResult` dataclass with fields for raw output, duration_ms, cost_estimate_usd, success, error, and parsed_payload.
- [ ] Implement text & JSON extraction helper to parse vote verdicts and structured security findings from stdout without duplicating regex logic across callers.
- [ ] Add unit tests verifying `WorkerExecutionResult` construction, extraction accuracy, and fail-closed error reporting.
